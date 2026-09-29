# XL branch deployment controller

The controller is a one-shot user service, invoked every 60 seconds by
`xl-deploy-poll.timer` using the venv at
`current/main/.venvs/xl-deploy-poll.service`. The poller is not in any deployment
stop list, so a pass already running can finish on its started release while a
main pointer changes. It checks `debug`, `test`, and `main`; a commit is
deployable only after GitHub Actions workflow **XL CI** succeeds for that exact
branch and 40-character SHA. Pending/failed CI does not advance the saved
cursor. Changes outside a branch's service mapping are recorded as no-ops.

## Branch and service map

| Branch | Immutable pointer | Services affected |
|---|---|---|
| `debug` | `/home/admin/xl_deploy/current/debug` | `xl-qqbot-debug.service` |
| `test` | `/home/admin/xl_deploy/current/test` | `xl-qqbot-test.service` |
| `main` | `/home/admin/xl_deploy/current/main` | `xl-qqbot-prod.service`, `xl-qqbot-router.service`, `xl-updata-server.service` as selected by changed paths |

Code is stored in `/home/admin/xl_deploy/releases/<sha>-<transaction-id>`;
each branch has an independent atomic `current` symlink. Journal/cursors,
config, QQ bot persistent state, backend data, databases, downloads and outbox
remain outside immutable releases. Missing current pointers fail closed; old
checkouts are never silently adopted as releases.

The router process itself owns the deployment-control API on
`127.0.0.1:8784`. There is no separate externally exposed control service.

## Manual setup and cutover

Run as the existing `admin` user. Do not use `sudo`. Begin with read-only
inventory; it makes no changes:

```bash
bash xl_deploy/scripts/bootstrap.sh --dry-run
bash xl_deploy/scripts/doctor.sh
```

Explicitly inventory and map existing bot/backend config and data paths before
activation. Old unit history mentions both `/home/admin/xl_qqbot-test` and
`/home/admin/xl-qqbot-test`; do not guess which checkout is live. Keep current
QQ Gateway ownership, credentials, bot persistent state and server `data/` in
place. Back them up with a consistent snapshot and never copy them into a
release directory.

Prepare the external secrets file without putting values in shell history or
terminal logs:

```bash
install -d -m 700 ~/.config/xl_deploy
umask 077
touch ~/.config/xl_deploy/secrets.env
touch ~/.config/xl_deploy/router.token
chmod 600 ~/.config/xl_deploy/secrets.env
chmod 600 ~/.config/xl_deploy/router.token
```

It must contain `GITHUB_TOKEN=...` for a token limited to read-only repository
and Actions metadata. Separately create
`~/.config/xl_deploy/router.token` containing the router bearer token; it must
match the deployment token in the existing bot config and be at least 32 random
characters. Both files must be mode `600`; config directories should be `700`.
Never place credentials or the production assetbundle key in Git.

The sample uses the CLI's `[github]`, `[deployment]`, and `[router]` schema.
`deployment.repository` is the local clone/worktree root used to fetch and diff
commits; set it to an operator-verified checkout and do not assume an old
bot/backend checkout is the deployment source. `releases_root` is immutable
candidate storage. Relative config paths resolve against the config file.
Review each path against the server inventory.

Copy/edit the sample only when no deployment config exists; never overwrite an
existing operator config. The CLI reads `/home/admin/xl_deploy/config.toml`:

```bash
install -d -m 700 ~/xl_deploy
if [[ ! -e ~/xl_deploy/config.toml ]]; then
  install -m 600 xl_deploy/config.toml.example ~/xl_deploy/config.toml
else
  printf '%s\n' 'Keeping existing ~/xl_deploy/config.toml unchanged'
fi
```

After release/current pointers and old-path mappings have been reviewed,
activation is explicit. Because this host already has user units, first stop
the exact affected services and verify each is inactive. Then activate with
the explicit reversible migration flag:

```bash
bash xl_deploy/scripts/bootstrap.sh --activate --migrate-existing
```

Activation installs user unit files and a protected sample config only if
absent. Existing inactive unit files and matching drop-in directories from
`~/.config/systemd/user` are moved into a new timestamped
`~/xl_deploy/unit-backups/` directory, with a mode-600 migration manifest.
Active or symlinked units are refused. Never automatically remove unit backups;
they are the recovery source for restoring the former unit definitions. To
manually restore after stopping any newly installed affected unit, move the
replacement files aside, move the recorded `unit` files and `drop-in`
directories back to `~/.config/systemd/user/`, then run
`systemctl --user daemon-reload`; inspect the manifest and paths first. If
activation is interrupted part-way through, use that same manifest to restore
only entries that were actually moved.
The command does not stop/start bots, enable the timer, move old service/data
directories, or delete anything. It refuses invalid release pointers, unmapped
legacy configs, or secrets not at mode 600. Missing baseline pointers remain
missing; the installer never invents/adopts one.
Before enabling, explicitly seed each missing branch pointer from its verified
branch/SHA into a new immutable release and preflight its per-unit virtualenvs;
never point `current/` at an old mutable service checkout. Then ensure each
pointer targets a verified/preflighted release and
the old service/config/data mapping is confirmed. For the initial bot cutover,
send `检测到更新，正在更新bot，期间将暂停服务` before placing the old router/tiers
into maintenance and stopping them; keep all existing state/data directories
untouched. The account's user systemd
manager must persist after logout; have the server operator arrange linger
through the approved host process. Port 8784 remains loopback-only; do not
open it in a firewall. Only after those checks should you run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now xl-qqbot-router.service xl-qqbot-prod.service
systemctl --user enable --now xl-qqbot-debug.service xl-qqbot-test.service
systemctl --user enable --now xl-updata-server.service
systemctl --user enable --now xl-deploy-poll.timer
```

## Update order, recovery, and notices

For QQ bot tiers, the controller first asks the router to durably enqueue
`检测到更新，正在更新bot，期间将暂停服务` before pausing/draining and stopping
affected services. A successful response means the persistent router FIFO
accepted the message; it does not mean QQ has already delivered it. Deployment
continues during the 02:00–08:00 Asia/Shanghai send curfew, while the queued
start/completion/release/rollback messages wait for 08:00. It stages and
preflights the immutable release first, switches only that branch pointer,
starts services and checks readiness, then resumes routing. Only after health
checks pass does it durably enqueue `更新完毕`; any new non-empty release
announcement follows in FIFO order. Backend-only updates do not announce to QQ
unless the plan includes bot tiers.

Each notification request carries an idempotency key derived from the durable
deployment transaction ID and phase (plus tier). Replaying a transaction journal
therefore does not add duplicate queue rows. If the router cannot persist a
notice, it rejects the request and the deploy transaction must not proceed past
the start-notice gate. Router queue delivery remains at-least-once: a crash
after QQ accepts a message but before the local acknowledgement may cause a
duplicate.

The durable journal records each phase. After interruption the next poll
attempts recovery before another deployment: it retries a pending completion
notice for a healthy release or restores the previous release and checks it
before reporting rollback. If rollback/readiness cannot be verified,
maintenance stays enabled and the journal remains for operator action. Do not
delete a release or journal to clear an error. Inspect with:

```bash
bash xl_deploy/scripts/doctor.sh
journalctl --user -u xl-deploy-poll.service -n 100 --no-pager
journalctl --user -u xl-qqbot-router.service -n 100 --no-pager
```

QQ delivery is **at-least-once**: if QQ accepts a message but the process exits
before persisting delivery, recovery may resend it. The QQ sender has no
idempotency key, so exactly-once delivery cannot be promised. A pending notice
blocks another transaction until recovery resolves it.

```bash
systemctl --user status xl-deploy-poll.timer xl-deploy-poll.service
journalctl --user -u xl-deploy-poll.service --since today
```
