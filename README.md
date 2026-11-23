# sashing-ci

Kernel patch CI on [Buildbot](https://buildbot.net): given a message-id, apply the series
from lore, check each of its patches, then build the series.

- `master/`: the pipeline, mounted into the Buildbot master:
  - `master.cfg`: the flow, and what patchwork gets
  - `reporterjson.py`: a reporter writing JSON to a Unix socket
- `tasks/`: checks and build presets, mounted into each worker
- `worker/`: Buildbot worker, kernel toolchains and b4
- `web/`: the read-only Flask UI, shipped in the web image
- `quadlet/`: the rootless Quadlet units
- `samples/`: projects, such as `linux-clk.ini`: the master reads its own from
  `~/.config/sashing-ci/project.ini`

## Flow

```
series MSGID                 gather the patches at MSGID's revision
├─ patch MSGID               apply through this patch, then checkpatch, dt-binding-check for each
│                            binding schema it adds or changes, then each preset,
│                            patch_config on patch_target
└─ build                     the whole series, if every patch built: each config of
                             [series], with its presets
```

- The series applies on `git_base` as fetched when its build starts; its patch builds
  check out that same commit.
- If a patch fails to apply, earlier patches still run their checks. Later patches
  report which patch blocked them. A patch that fails to apply or build stops the
  series builds. checkpatch's and the binding checks' findings only warn.
- A patch build has its message-id as property `msgid`, its series' as `series_msgid`.
- A worker runs one build of each builder at a time. More builds at once: add a worker to
  `workers` and start its unit.

## Patchwork

[pw-triage-bot](https://github.com/jbrun3t/pw-triage-bot) starts the runs of
the series it triages through its hook, `trigger-ci.sh`. Mount the hook on the
bot's `PATH` and set its `SASHING_OWNER` environment variable to the project's
`[patchwork] owner`. Each test of those runs is reported as it ends, through pw-check's
socket, `[patchwork] socket`, in the `pw-check` volume the master mounts:

- a patch build's tests on its patch; a series build's on every patch of the series,
  context prefixed `series-`
- context: the step's name; description: its summary; link: its log
- cancelled, or broken by the CI: `warning`
- never ran: nothing; run again: replaces the check, patchwork showing each context's latest
- no socket: the master runs, and logs the failed reports

## Tasks

A task runs in the kernel tree, logs to its output, and exits 0 pass, 1 fail, 3 warning,
anything else when it could not check.

Compile steps look for an executable `master/summaries/TASK`. It receives the task's
exit code as its only argument and the finished stdout and stderr on stdin. Exit zero
and print one nonempty line to use it as the step description in the UI and Patchwork.
Otherwise the step keeps its normal summary. The companion does not set the test result.

| Task | Does |
|---|---|
| `gather-series MBOX MSGID` | saves the revision's prerequisites and patches from lore in one mailbox; prints the patches' message-ids in order |
| `apply-patch MBOX [NUMBER]` | applies the prerequisites and patches through NUMBER (default: the whole series); warns if prerequisites were needed |
| `checkpatch` | checks HEAD; `FILE_PATH_CHANGES` and `CONFIG_DESCRIPTION` are notices and do not by themselves warn or fail |
| `dt-binding-check FILE` | checks a binding schema for the findings HEAD adds |
| `kbuild PRESET CONFIG [TARGET...]` | builds with tuxmake |

Presets are tuxmake configuration files in `tasks/tuxmake/`; `default` applies to all.

## Images

The **Publish images** GitHub Actions workflow builds three Linux/amd64 images:
`ghcr.io/jbrun3t/sashing-master`, `ghcr.io/jbrun3t/sashing-worker` and
`ghcr.io/jbrun3t/sashing-web`. All three are tagged with the full commit SHA
of the workflow run. Pushes to `main` that change image inputs or the workflow
publish a set; use **Run workflow** to build on demand. Edits to the mounted
pipeline, tasks and project INI need no image build.

For anonymous pulls, set each GHCR package's visibility to public after its
first publication. In a deployment, use the tagged image reference directly.
For the sample Quadlets, give the images their local names:

```sh
revision=...  # full commit SHA from the image build
for component in master worker web; do
    image="ghcr.io/jbrun3t/sashing-$component:$revision"
    podman pull "$image" && podman tag "$image" "localhost/sashing-$component"
done
```

Alternatively, build locally from the repository root:

```sh
podman build -f master/Containerfile -t localhost/sashing-master .
podman build -f worker/Containerfile -t localhost/sashing-worker .
podman build -f web/Containerfile -t localhost/sashing-web .
```

## Setup

From the checkout containing the pipeline you want to run:

```sh
mkdir -p ~/.config/containers/systemd
ln -s "$PWD" ~/.config/containers/systemd/sashing
mkdir -p ~/.config/sashing-ci && cp samples/linux-clk.ini ~/.config/sashing-ci/project.ini
head -c 24 /dev/urandom | base64 | podman secret create sashing-worker-pass -
systemctl --user daemon-reload
systemctl --user start sashing-pod sashing-master sashing-worker@local1 sashing-worker@local2
```

- Quadlet reads the units from the checkout.
- The workers are instances of one template, which the pod does not start: start each by
  name.

To take a change:

- a task or tuxmake preset: workers read `tasks/` from the mounted directory.
  Edit between builds if every step of a run must use the same revision.
- `master.cfg` or the project: reload the master, which the running builds survive:
  `systemctl --user reload sashing-master`
- `reporterjson.py` or `buildbot.tac`: restart the master
- `master/Containerfile`, Buildbot: rebuild the master image, restart the master
- `web/`: rebuild the web image and restart sashing-web. Build with
  `--build-arg REVISION="$(git rev-parse HEAD)"` to link the UI footer to its source.
- A master restart ends the running builds.

The image keeps the dtschema it was built with. Once the tree requires a newer one, the
binding checks fail to check: rebuild without the cache, and restart the workers, which
stops their builds:

```sh
podman build --no-cache -f worker/Containerfile -t localhost/sashing-worker .
systemctl --user restart sashing-worker@local1 sashing-worker@local2
```

| Host | Container | |
|---|---|---|
| `~/.local/state/sashing/` | master: `/buildbot` | database |
| the checkout's `master/` | master: `/opt/sashing` | the flow |
| `~/.config/sashing-ci/` | master and web: `/etc/sashing-ci` | the project, `project.ini` |
| the checkout's `tasks/` | worker: `/opt/sashing/tasks` | executable checks and tuxmake presets |
| volume `pw-check` | master: `/run/pw-check` | pw-check's socket |
| `~/.cache/sashing/<worker>/` | worker: `/home/sashing` | builders' trees, b4's cache |
| `~/.cache/sashing/ccache/` | workers: `/home/sashing/.ccache` | ccache, shared |

- A worker's directory belongs to a subuid of the host user: reach it with
  `podman unshare`.
- A worker on another machine: the same image and tasks mount, `BUILDMASTER` set to the master's host,
  which then publishes port 9989.

## Use

The web UI, read-only, is at <http://localhost:8011>: `sashing-web`, which only reads the
master's REST API. The project's `url` is where patchwork's checks link to its log pages.

- `/`: the dashboard: the series checked, the runs running and queued, runs per day, who
  is online; then the series runs, each a folded tree opening on its builds, their steps,
  and the patch builds under `patches`. The query selects them: `q`, part of the
  message-id; `status`, once per status shown: `running`, `success`, `warnings`,
  `failure`, `other`; `sort`: `status`, `series`, `run`, `started` (the default) or
  `duration`; `order`: `asc` or `desc`
- `/series/<message-id>`: the runs of a series, as trees
- `/builds/<buildid>/steps/<number>`: a step's log; `.../raw`, as text. A step's other
  logs: `.../logs/<slug>`

Running runs and logs update in place: the pages poll fragments of the app with htmx.
The pages are Jinja templates, `web/templates/_*.html` the fragments. The image fetches
htmx and its idiomorph extension (0BSD), at the versions `web/Containerfile` names.
Vendored in `web/static/`: the fonts Inter and JetBrains Mono (OFL, licences in
`fonts/`).

Buildbot's own UI and REST API, port 8010, start and stop runs: only the pod's containers
reach them, such as the one the hook runs in. The examples run curl in the pod:

```sh
alias curl='podman run --rm -i --pod sashing docker.io/curlimages/curl'
```

Start a run; the result holds the buildset id:

```sh
curl -X POST localhost:8010/api/v2/forceschedulers/series -H 'Content-Type: application/json' \
	-d '{"jsonrpc": "2.0", "method": "force", "id": 1,
	     "params": {"builderNames": ["series"], "msgid": "<message-id>"}}'
```

Find a series' runs, its patch builds, and a build's steps:

```sh
curl -s 'localhost:8010/api/v2/builders/series/builds?property=msgid' |
	jq '.builds[] | select(.properties.msgid[0] == "<message-id>") | {buildid, complete, results, state_string}'
curl -s 'localhost:8010/api/v2/builders/patch/builds?property=msgid&property=series_msgid' |
	jq '.builds[] | select(.properties.series_msgid[0] == "<message-id>") |
	    {buildid, msgid: .properties.msgid[0], results, state_string}'
curl -s localhost:8010/api/v2/builds/<buildid>/steps | jq '.steps[] | {name, results, state_string}'
```

Results: 0 success, 1 warnings, 2 failure, 3 skipped, 4 exception, 5 retry, 6 cancelled.

## Logs

- In the master's database, `~/.local/state/sashing/state.sqlite`, compressed, kept until
  deleted: about 200 KB per build.
- Patchwork's checks link to them by build id and step number: keep the database.
- Back it up while the master runs; restore it with the master stopped:

```sh
sqlite3 ~/.local/state/sashing/state.sqlite ".backup state.sqlite.backup"
```
