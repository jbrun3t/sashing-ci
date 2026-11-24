# Sashing CI reference

Start with the [deployment guide](../README.md).

## Mounts and services

Paths below are for the [sample Quadlets](../quadlet/) with default XDG
directories.

| Host | Container | Purpose |
| --- | --- | --- |
| `~/.local/state/sashing/` | master: `/buildbot` | Buildbot database, including logs |
| Checkout `master/` | master: `/opt/sashing` | Pipeline |
| `~/.config/sashing-ci/` | master and web: `/etc/sashing-ci` | Project settings |
| Checkout `tasks/` | worker: `/opt/sashing/tasks` | Checks and tuxmake presets |
| `pw-check` volume | master: `/run/pw-check` | Optional reporting socket |
| `~/.cache/sashing/<worker>/` | worker: `/home/sashing` | Git trees and b4 cache |
| `~/.cache/sashing/ccache/` | worker: `/home/sashing/.ccache` | Shared compiler cache |

To add capacity, add a name to `workers`, reload the master, and start
`sashing-worker@NAME`. Each worker can run one build per builder at a time.
Use `podman unshare` to access a worker cache owned by a subordinate UID.
A remote worker needs the same task mount and `BUILDMASTER` pointing to a
host that accepts its connections on port `9989`.

## Images and updates

[Publish images](https://github.com/jbrun3t/sashing-ci/actions/workflows/images.yml)
publishes when image inputs change on `main` and on manual dispatch. The
sample Quadlets use local tags; deployments can use the GHCR references directly.
For anonymous pulls, the packages must be public.

To build locally from the repository root:

```sh
podman build -f master/Containerfile -t localhost/sashing-master .
podman build -f worker/Containerfile -t localhost/sashing-worker .
podman build -f web/Containerfile -t localhost/sashing-web .
```

For the web image, `--build-arg REVISION="$(git rev-parse HEAD)"` adds a source
link to the footer. Replace the image and restart `sashing-web` to update it.
If the kernel requires a newer dtschema, rebuild the worker image with
`--no-cache` and restart the workers after their builds finish.

Use `systemctl --user reload sashing-master` for `master.cfg` and
`project.ini`. A change to an imported module, `buildbot.tac`, or the master
image needs a restart. A master restart ends running builds.

## Pipeline and task interface

The series build fetches `git_base` when it starts. Each patch build starts
from the same commit and applies the series through its assigned patch. An
apply failure doesn't prevent earlier patches from being checked. Later
patches report the one that blocked them. The whole-series builds run only
if no patch fails application or compilation.

| Task | Purpose |
| --- | --- |
| `gather-series DIRECTORY MSGID` | Write `series.mbx` and `series-prerequisites.mbx` and print the series' patch Message-IDs in order. Fail on unresolved prerequisites. |
| `apply-patch DIRECTORY [NUMBER]` | Apply the gathered mailboxes offline through the numbered patch or through the end. |
| `checkpatch` | Check HEAD. `FILE_PATH_CHANGES` and `CONFIG_DESCRIPTION` are notices. |
| `dt-binding-check FILE` | Report findings the commit adds to a binding schema. |
| `kbuild PRESET CONFIG [TARGET...]` | Build with tuxmake. Presets are in [`tasks/tuxmake/`](../tasks/tuxmake/). |

The task exit convention is `0` for pass, `1` for fail, and `3` for warning.
An unavailable binding check exits `2` and is reported as a warning.
An executable `master/summaries/TASK` can supply a finished step summary
for gather, patch application, checkpatch, and compile steps. It receives the
task exit code as its argument and the task's stdout and stderr on stdin.
Exit zero and print one nonempty line to set the summary without changing the
result.

## Patchwork

Make [`trigger-ci.sh`](../trigger-ci.sh) executable and available on the
triage bot's `PATH`. Set it as the relevant section's hook and set
`SASHING_OWNER` to `[patchwork] owner`. The hook submits to
`http://localhost:8010`, so it must run in the same pod with the sample
network setup. Serve `pw-check` on a Unix socket shared with the master and
set `[patchwork] socket` to the path as seen by the master. See the
[pw-triage-bot documentation](https://github.com/jbrun3t/pw-triage-bot)
for the command.

Patch checks are posted on their patch. Series checks are posted on each
patch with a `series-` prefix. The step's name is the check context, its
summary is the description, and the URL links to its log. Cancelled steps or
Buildbot exceptions are reported as warnings; steps that never ran are not
reported. A rerun replaces the check for the same context. If reporting
fails, see the master logs; reports aren't retried.

## Buildbot API and logs

Port `8010` is private to the sample pod. The [submission example](../README.md#submit-a-series-and-check-the-result)
returns the buildset ID and its build request IDs. For other queries, run
`curl` inside the pod, for example:

```sh
podman run --rm --pod sashing docker.io/curlimages/curl -fsS \
    'http://127.0.0.1:8010/api/v2/builders/series/builds?property=msgid'
```

- `/api/v2/builders/patch/builds?property=msgid&property=series_msgid` lists
  patch builds and the series to which they belong.
- `/api/v2/builds/BUILD_ID/steps` lists step results and summaries.
- Result codes: `0` success, `1` warnings, `2` failure, `3` skipped,
  `4` exception, `5` retry, `6` cancelled.

On the dashboard, `/series/MESSAGE_ID` opens a series and
`/builds/BUILD_ID/steps/STEP_NUMBER` opens a step's log. Add `/raw` for plain
text. On the dashboard index, `q` matches Message-IDs and repeated `status`
parameters select build states; `sort` and `order` control ordering.

Back up the master database with SQLite's online backup command. Stop the
master before restoring a backup.

```sh
sqlite3 ~/.local/state/sashing/state.sqlite ".backup state.sqlite.backup"
```
