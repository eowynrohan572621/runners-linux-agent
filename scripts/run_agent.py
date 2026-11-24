#!/usr/bin/env python3
"""Run one verified Linux fleet agent inside this macOS job's Docker VM."""

import hashlib
import io
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

PROFILE = "runners-mac-mini"
GUEST_DIR = "/opt/runners-mac-mini"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
STOPPING = False


def configuration(env):
    origin = env.get("CONTROL_URL", "").rstrip("/")
    url = urllib.parse.urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        raise ValueError("CONTROL_URL must be an HTTPS origin")
    if not env.get("JOIN_TOKEN"):
        raise ValueError("Set the JOIN_TOKEN repository secret")
    minutes = int(env.get("MAX_RUNTIME_MINUTES", "345"))
    if not 30 <= minutes <= 345:
        raise ValueError("MAX_RUNTIME_MINUTES must be between 30 and 345")
    tag = env.get("SERVER_TAG", "github-mac-mini")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", tag):
        raise ValueError("Invalid SERVER_TAG")
    label = "GitHub Mac mini " + env.get("GITHUB_REPOSITORY", "local")
    label += " #" + env.get("GITHUB_RUN_ID", "manual")
    return origin, env["JOIN_TOKEN"], minutes, tag, label


def download(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "runner-agent-bootstrap"})
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Agent download exceeded its size limit")
    return body


def extract_binary(archive, digest):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid agent bundle digest")
    if hashlib.sha256(archive).hexdigest() != digest:
        raise ValueError("Agent bundle checksum did not match")
    allowed = {"Dockerfile", "bin/runner-agent-linux-amd64", "bin/runner-agent-linux-arm64"}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
        members = bundle.getmembers()
        names = [item.name for item in members]
        if (set(names) != allowed or len(names) != len(allowed)
                or any(not item.isfile() for item in members)):
            raise ValueError("Unexpected files in agent bundle")
        return bundle.extractfile("bin/runner-agent-linux-amd64").read()


def guest(*command):
    return ["colima", "--profile", PROFILE, "ssh", "--", "sudo", "-n", *command]


def bootstrap_script(origin, token, tag, label, lifetime, binary):
    values = {
        "CONTROL_URL": origin,
        "AGENT_URL": origin + "/agent",
        "JOIN_TOKEN": token,
        "SERVER_TAG": tag,
        "LABEL": label,
        "RUNNER_DATA": "/var/lib/runners-mac-mini",
        "RUNNER_CODE_MANAGED": "1",
        "RUNNER_INSTALL_REVISION": hashlib.sha256(binary).hexdigest(),
        "LIFETIME_MIN": str(lifetime),
    }
    lines = ["set -eu", "umask 077", "mkdir -p /var/lib/runners-mac-mini"]
    lines.extend("export " + key + "=" + shlex.quote(value) for key, value in values.items())
    lines.extend(["echo $$ > /run/runners-mac-mini.pid", "exec " + GUEST_DIR + "/runner-agent"])
    return ("\n".join(lines) + "\n").encode()


def control_request(origin, path, token=None, payload=None):
    headers = {"User-Agent": "runner-agent-bootstrap"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(origin + path, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def find_run(status, label):
    return next((run for run in status.get("runs", [])
                 if run.get("label") == label and run.get("live")), None)


def log(message):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def stop_requested(signum, frame):
    global STOPPING
    STOPPING = True


def main():
    origin, token, minutes, tag, label = configuration(os.environ)
    if "--check" in sys.argv:
        log("Configuration is valid; one Mac runner is permitted by the workflow lock")
        return 0
    if sys.platform != "darwin":
        raise ValueError("This supervisor must run on the macOS workflow runner")
    started = float(os.environ.get("MAC_JOB_STARTED_AT", time.time()))
    deadline = started + minutes * 60
    process = None
    joined = None
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_requested)
    try:
        log("Downloading and verifying the control plane's current native agent")
        digest = download(origin + "/agent/rust/bundle.sha256", 256).decode().strip()
        binary = extract_binary(download(origin + "/agent/rust/bundle.tar", MAX_BUNDLE_BYTES), digest)
        log("Starting one Linux Docker guest using QEMU software emulation")
        subprocess.run([
            "colima", "--profile", PROFILE, "start", "--vm-type", "qemu",
            "--arch", "x86_64", "--cpus", "2", "--memory", "4", "--disk", "12",
            "--mount-type", "sshfs",
        ], check=True, timeout=1200)
        if STOPPING:
            return 0
        subprocess.run(guest("docker", "info", "--format", "{{.ServerVersion}}"), check=True, timeout=120)
        subprocess.run(guest("docker", "compose", "version"), check=True, timeout=120)
        install = "umask 077; mkdir -p " + GUEST_DIR
        install += "; cat > " + GUEST_DIR + "/runner-agent; chmod 700 " + GUEST_DIR + "/runner-agent"
        subprocess.run(guest("sh", "-ec", install), input=binary, check=True, timeout=180)
        lifetime = math.floor((deadline - time.time()) / 60)
        if lifetime < 15:
            raise RuntimeError("Insufficient job time left after VM startup")
        log("Starting the fleet agent with a " + str(lifetime) + " minute lifetime")
        process = subprocess.Popen(guest("sh", "-s"), stdin=subprocess.PIPE)
        # The credential travels on SSH stdin, never in command arguments or files.
        process.stdin.write(bootstrap_script(origin, token, tag, label, lifetime, binary))
        process.stdin.close()
        join_deadline = min(time.time() + 600, deadline)
        last_check = 0
        while not STOPPING and time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Agent SSH session exited with code " + str(process.returncode))
            if not joined and time.time() - last_check >= 10:
                last_check = time.time()
                try:
                    joined = find_run(control_request(origin, "/api/status"), label)
                except Exception:
                    log("Waiting for the control plane to confirm this agent")
                if joined:
                    log("Agent connected on fleet machine " + str(joined["machine"]))
            if not joined and time.time() >= join_deadline:
                raise RuntimeError("Agent did not join the fleet within ten minutes")
            time.sleep(2)
        log("Ending this Mac's lease; the workflow's waiting replacement can start next")
        return 0
    finally:
        if joined:
            try:
                control_request(origin, "/api/drain", token, {"run": joined["id"]})
                log("Requested a drain for this agent")
            except Exception:
                log("Drain request failed; the controller will detect the agent's departure")
        if process is not None:
            try:
                subprocess.run(guest("sh", "-c", 'test ! -f /run/runners-mac-mini.pid || kill -TERM "$(cat /run/runners-mac-mini.pid)"'), timeout=30, check=False)
                process.wait(timeout=60)
            except (subprocess.TimeoutExpired, OSError):
                process.kill()
                process.wait(timeout=10)
        try:
            subprocess.run(["colima", "--profile", PROFILE, "stop", "--force"], timeout=90, check=False)
        except (subprocess.TimeoutExpired, OSError):
            log("VM stop did not finish; the workflow cleanup step will retry")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # HTTP and subprocess exception strings can include unexpected data.
        log("Agent supervisor failed (" + type(error).__name__ + ")")
        sys.exit(1)
