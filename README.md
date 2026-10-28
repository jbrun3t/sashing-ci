# sashing-ci

Kernel patch CI on [Buildbot](https://buildbot.net): given a message-id, apply the series
from lore, check each of its patches, then build the series.

- `master/master.cfg`: the flow, and what patchwork gets
- `master/reporterjson.py`: a reporter writing JSON to a Unix socket
- `tasks/`: the work, scripts that know nothing of Buildbot
- `worker/`: the worker image, `localhost/sashing-worker`: toolchains, b4 and the tasks
- `quadlet/`: the rootless Quadlet units
- `samples/`: projects, such as `linux-clk.ini`, mounted as the master's `project.ini`

## Flow

```
series MSGID                 apply the series on git_base, at MSGID's revision
├─ patch COMMIT              one build per patch: checkpatch, dt-binding-check for each
│                            binding schema it adds or changes, then each preset,
│                            patch_config on patch_target
└─ build                     the whole series, if every patch built: each config of
                             [series], with its presets
```

- The series applies on `git_base` as fetched when its build starts; its patch builds
  check out that same commit.
- A patch that fails to build stops the series. checkpatch's and the binding checks'
  findings only warn.
- A patch build has its message-id as property `msgid`, its series' as `series_msgid`.
- A worker runs one build of each builder at a time. More builds at once: add a worker to
  `workers` and start its unit.

## Patchwork

[pw-triage-bot](https://github.com/jbrun3t/pw-triage-bot), on the host, starts the runs of
the series it triages through its hook, `trigger-ci.sh`, as the project's
`[patchwork] owner`. Each test of those runs is reported as it ends, through pw-check's
socket (pw-triage-bot's `samples/pw-check.socket`, installed as
`pw-check-linux-clk.socket`):

- a patch build's tests on its patch; a series build's on every patch of the series,
  context prefixed `series-`
- context: the step's name; description: its summary; link: its log
- cancelled, or broken by the CI: `warning`
- never ran: nothing; run again: replaces the check, patchwork showing each context's latest
- no socket: the master runs, and logs the failed reports

## Tasks

A task runs in the kernel tree, logs to its output, and exits 0 pass, 1 fail, 3 warning,
anything else when it could not check.

| Task | Does |
|---|---|
| `apply-series COMMITS MSGID` | applies a series from lore with b4, at MSGID's revision |
| `checkpatch` | runs checkpatch on HEAD |
| `dt-binding-check FILE` | checks a binding schema for the findings HEAD adds |
| `kbuild PRESET CONFIG [TARGET...]` | builds with tuxmake |

Presets are tuxmake configuration files in `tasks/tuxmake/`; `default` applies to all.

## Setup

```sh
podman build -f worker/Containerfile -t localhost/sashing-worker .
ln -s "$PWD" ~/.config/containers/systemd/sashing
head -c 24 /dev/urandom | base64 | podman secret create sashing-worker-pass -
systemctl --user daemon-reload
systemctl --user start sashing-pod sashing-master sashing-worker@local1 sashing-worker@local2
```

- Quadlet reads the units from the checkout; they find `master.cfg` and the project
  relative to themselves.
- The workers are instances of one template, which the pod does not start: start each by
  name.

To take a change:

- a task: rebuild the image, restart the workers
- `master.cfg`, `reporterjson.py` or the project: `systemctl --user restart sashing-master`,
  which ends the running builds

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
| `$XDG_RUNTIME_DIR/pw-check/` | master: `/run/pw-check` | pw-check's socket |
| `~/.cache/sashing/<worker>/` | worker: `/home/tuxmake` | builders' trees, b4's cache |
| `~/.cache/sashing/ccache/` | workers: `/home/tuxmake/.ccache` | ccache, shared |

- A worker's directory belongs to a subuid of the host user: reach it with
  `podman unshare`.
- A worker on another machine: the same image, `BUILDMASTER` set to the master's host,
  which then publishes port 9989.

## Use

The web UI is at <http://localhost:8010>: the `series` builder has the form starting a run.

Start a run from the REST API; the result holds the buildset id:

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
- Patchwork's checks link to them by builder and build number: keep the database.
- Back it up while the master runs; restore it with the master stopped:

```sh
sqlite3 ~/.local/state/sashing/state.sqlite ".backup state.sqlite.backup"
```
