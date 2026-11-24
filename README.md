# Runners Linux agent

One standard GitHub-hosted `ubuntu-24.04` runner joins an existing Runners
control plane as a native Docker workload agent. Docker and Compose are checked
on startup. The controller's native agent bundle is downloaded and verified
before execution.

## Scheduling

- An hourly schedule at minute 23 UTC requests a replacement.
- A concurrency group permits one running host and one waiting replacement.
  New requests replace an older waiting request without cancelling the active host.
- Each job has a 345-minute budget and reports its remaining lifetime to the
  controller. It requests a drain and stops before GitHub's six-hour limit.
- The next waiting run then starts on a fresh Linux host. Volumes are ephemeral;
  persistent application data needs external storage.

GitHub may delay or drop scheduled requests, and disables public-repository
schedules after 60 days without repository activity. The schedule has no end
date, but uninterrupted or perpetual hosting cannot be guaranteed.

## Configuration

Repository variable `CONTROL_URL` contains the HTTPS controller origin.
Repository secret `JOIN_TOKEN` contains the fleet join token. The runtime agent
receives no GitHub API token. Checkout credentials are not persisted.

Use **Actions → Linux fleet agent → Run workflow** to start immediately.
Disable the workflow to stop requesting replacements and cancel the active run
to stop the current host.

The fleet machine appears with tag `github-linux` and a label containing the
repository and workflow run ID. The existing controller chooses its workloads.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

GitHub references: [runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[six-hour job limit](https://docs.github.com/en/actions/reference/limits),
[schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
