# sashing-ci

Sashing checks Linux kernel patch series from [lore](https://lore.kernel.org/).
Give it a Message-ID and a target branch: it gathers the series and any declared
prerequisites, checks each patch, then builds the whole series. Follow the runs
and logs in a read-only dashboard.

## Deploy with Podman

The [sample Quadlets](quadlet/) run one project with rootless Podman on Linux
amd64. Run these commands as the account that will own the services, from the
Sashing checkout you want to deploy.

1. Choose a full commit SHA from a successful [Publish images](https://github.com/jbrun3t/sashing-ci/actions/workflows/images.yml)
   run. All three images use that tag. Give them the local names used by the
   sample units:

   ```sh
   revision=YOUR_IMAGE_COMMIT_SHA
   for component in master worker web; do
       image="ghcr.io/jbrun3t/sashing-$component:$revision"
       podman pull "$image" && podman tag "$image" "localhost/sashing-$component:latest" || break
   done
   ```

2. Copy [the sample project](samples/linux-clk.ini) to the configuration
   directory. Set the repository and branch (`git_url`, `git_base`), the checks
   you want, and `url`, the address users will open. Remove the `[patchwork]`
   section if you don't use Patchwork reporting.

   ```sh
   mkdir -p ~/.config/sashing-ci
   cp samples/linux-clk.ini ~/.config/sashing-ci/project.ini
   ```

3. Install the units and start the services. Substitute your worker names if
   you changed `workers` in `project.ini`.

   ```sh
   mkdir -p ~/.config/containers/systemd
   ln -s "$PWD" ~/.config/containers/systemd/sashing
   head -c 24 /dev/urandom | base64 | podman secret create sashing-worker-pass -
   systemctl --user daemon-reload
   systemctl --user start sashing-master sashing-web sashing-worker@local1 sashing-worker@local2
   ```

The units mount `master/` and `tasks/` directly from the checkout. Keep
`~/.local/state/sashing/`: it holds the build history and logs. Worker caches
are in `~/.cache/sashing/`. For deployment with a different layout, see the
[mount and service reference](docs/reference.md#mounts-and-services).

## Submit a series and check the result

The dashboard is at <http://localhost:8011/> with the sample units. It shows
whether workers are online. To submit a run, use the `series` scheduler on
Buildbot's private interface. With the sample pod:

```sh
podman run --rm --pod sashing docker.io/curlimages/curl -fsS \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"force","params":{"msgid":"YOUR_MESSAGE_ID","username":"Local Tester <tester@example.com>"}}' \
    http://127.0.0.1:8010/api/v2/forceschedulers/series
```

Open the run in the dashboard to see patch checks, whole-series builds, and
step logs. A missing declared prerequisite stops the run. Checkpatch and
binding findings don't block the whole-series build.

## Optional: report to Patchwork

[pw-triage-bot](https://github.com/jbrun3t/pw-triage-bot) can submit new series
through [`trigger-ci.sh`](trigger-ci.sh). Set the hook's `SASHING_OWNER` to
`[patchwork] owner` in the project INI. The bot must be able to reach Buildbot
on port `8010`, and the master must be able to reach the `pw-check` socket at
`[patchwork] socket`. See the [integration reference](docs/reference.md#patchwork).

Keep Buildbot's writable interface private; expose only the read-only
dashboard. Keep its public URL and the master database to preserve existing
Patchwork log links.

## Updates

Update the mounted checkout between builds. Task changes need no restart or
image rebuild. Reload `sashing-master` for `master.cfg` or project settings;
restart it after builds finish if imported Python modules changed. See the
[reference](docs/reference.md) for image builds, backups, and the Buildbot API.
