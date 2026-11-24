# Runners Mac mini agent

One GitHub-hosted Apple Silicon macOS runner joins an existing Runners control
plane as a Docker workload agent. This does not move or replace the controller.

The workflow requests `macos-15`, GitHub's standard M1 macOS VM. GitHub allocates
a new VM for each job; this is not a dedicated physical Mac mini. Since hosted
Apple Silicon runners do not expose nested hardware virtualization, Colima runs
one x86_64 Linux Docker guest with QEMU software emulation. This is suitable for
light workloads and is slower than native Linux. The guest has 2 CPUs, 4 GiB RAM
and a 12 GiB sparse disk; usable storage also depends on the hosted runner.

## Scheduling

- Every hour at minute 23 UTC, a replacement is requested.
- A repository-wide concurrency group permits one running Mac and one waiting
  replacement. Superseded waiting runs may show as cancelled; the current Mac
  continues running.
- Each job gives itself a 345-minute budget, including tool and VM startup, and
  reports the remaining lifetime to the controller.
- On shutdown it requests a drain, stops its agent and stops its own VM. The
  waiting job then receives a fresh Mac. No workflow dispatch token is given to
  the runtime agent.
- GitHub can delay or drop scheduled requests, and public-repository schedules
  are disabled after 60 days without repository activity. This provides recurring
  capacity, not guaranteed uninterrupted or perpetual hosting. Local volumes
  disappear with each job; use external storage for persistent workload data.

The agent is fetched from the configured controller's native bundle endpoint.
Its checksum and the archive's file names/types are verified before execution.
The fleet join token is passed over SSH stdin and kept out of the repository,
command arguments and generated files. The checkout does not retain Git credentials.

## Configuration

Set repository variable `CONTROL_URL` to the HTTPS controller origin and repository
secret `JOIN_TOKEN` to its fleet join token. Use **Actions → Mac mini agent → Run
workflow** to start immediately. The hourly schedule provides later requests.
Disable the workflow to stop requesting replacements; cancel the active run to
stop the current Mac as well.

The machine appears with tag `github-mac-mini` and a label identifying this
repository and workflow run. The controller decides which applications to place
on it. Existing fleet machines and application replica counts are unchanged.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

GitHub references: [runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[six-hour job limit](https://docs.github.com/en/actions/reference/limits),
[schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
