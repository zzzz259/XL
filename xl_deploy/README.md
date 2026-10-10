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
| `debug` | `/home/admin/xl_deploy/current/debug` | CI/integration only; no runtime units |
| `test` | `/home/admin/xl_deploy/current/test` | `xl-qqbot-test.service` and/or `xl-updata-server-test.service` |
| `main` | `/home/admin/xl_deploy/current/main` | `xl-qqbot-prod.service`, shared `xl-qqbot-router.service`, and/or `xl-updata-server.service` as selected by changed paths |

The one shared router retains each group's original `debug`/`test`/`production`
tier classification; both debug and test traffic are forwarded to the test Bot
worker at `127.0.0.1:8782`, while production uses `8783`. Test Bot changes
pause/drain both lower tiers but do not restart the router. The debug-to-test
forwarding rule is shipped by `main`, because the router owns the shared QQ
gateway. Debug runtime changes are therefore CI-verified no-ops. Test and main
backend configurations, bearer secrets, state databases, downloads, versions,
character output and Bot outboxes must remain separate. The test server is
always available for local control but its `environment = "test"` policy
forcibly disables scheduled CDN polling; GitHub deployment itself never starts
a game update. Both backend services are placed in `xl-updata.slice`, capped at
one CPU core in aggregate.

The test Bot's external `config.toml` must retain the same `[groups].debug` and
`[groups].test` IDs and feature-level settings as the router's classification.
The worker recomputes `GroupTier` from that configuration for every request;
the router preserves the group ID and routes by the original tier. Copying
only the test group list would accidentally evaluate debug groups as the
default tier.

Point the test Bot worker's `[watch]` data root, `character_data`,
`versions_dir`, and `outbox_dir` at the test backend's data tree. The shared
router keeps its main paths and can additionally watch both update sources via
`[[watch.update_sources]]`: `main` must match `[watch].outbox_dir`; `test` points
to the test backend outbox and uses `minimum_tier = "test"`. The main source
keeps its existing delivery reach, while test lifecycle notices and character
cards are enqueued only for test/debug groups. Both sources feed the same
persistent FIFO, but use separate event cursors, sent records, and idempotency
keys so equal game versions do not collide. A manual test-server run uses the
ordinary update pipeline and writes events and artifacts only to test data;
the router then queues its notices/cards without direct sending.

Code is stored in `/home/admin/xl_deploy/releases/<sha>-<transaction-id>`;
each runtime branch (`test` and `main`) has an independent atomic `current`
symlink. Debug requires only a persisted CI cursor. Journal/cursors,
config, QQ bot persistent state, backend data, databases, downloads and outbox
remain outside immutable releases. Missing current pointers fail closed; old
checkouts are never silently adopted as releases.

Backend release preflight installs `xl_updata_server/renderer` runtime packages
from its `package.json` and verifies `node -e "require('canvas')"` before any
service cutover. It also runs that environment's read-only `--healthcheck`
against its external config and checks the corresponding loopback `/healthz`
after startup. A missing or unloadable native renderer dependency therefore
fails staging while the active release remains untouched.

The router process itself owns the deployment-control API on
`127.0.0.1:8784`. There is no separate externally exposed control service.

## Update server control API

Each backend exposes its own loopback-only control API: production on
`127.0.0.1:8790`, test on `127.0.0.1:8791`. `/healthz` is read-only and
unauthenticated; the other routes require `Authorization: Bearer ...`. API
tokens come from different mode-600 systemd EnvironmentFiles:
`/home/admin/.config/xl_updata_server/api.env` and
`/home/admin/.config/xl_updata_server-test/api.env`, each defining
`XL_UPDATE_API_TOKEN` with a distinct random value of at least 32 characters.
Do not expose these ports through a firewall or reverse proxy.

| Method and path | Purpose |
|---|---|
| `GET /healthz` | Read-only environment and polling health; no CDN call |
| `GET /api/v1/status` | Authenticated environment, scheduled-poll policy and active run |
| `POST /api/v1/updates/run-once` | Authenticated empty-body one-shot check+process; returns `202` and a job ID |
| `GET /api/v1/jobs/{job_id}` | Authenticated persisted job status/result |

Only one scheduled/manual game-update run can execute at a time. A competing
manual request returns `409`; shutdown rejects new triggers with `503` and
waits for an active pipeline. Test mode never polls the CDN on a schedule, even
if `[polling].enabled = true`; the manual request uses the same ordinary update
pipeline and writes only to test data.

PowerShell example (put the secret in the process environment without printing
it; do not paste it into command history):

```powershell
$headers = @{ Authorization = "Bearer $env:XL_UPDATE_API_TOKEN" }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8791/api/v1/updates/run-once -Headers $headers
Invoke-RestMethod -Uri http://127.0.0.1:8791/api/v1/status -Headers $headers
```

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

This layout change requires an operator-controlled one-time cutover before the
new branch mapping can manage both backends: install/activate the reviewed
controller and unit templates, provision the distinct test config/data/API
token, and make verified `current/test` and `current/main` release pointers.
When upgrading an existing poller whose code is loaded from `current/main`,
the shared control-plane package may instead be installed in the stable
`/home/admin/xl_deploy/control-plane` directory and the poller unit pointed at
that path. This one-time controller upgrade is separate from both runtime
release pointers; it must not move `current/main` or alter main application
data.
The deployment poller never creates units, runtime configs, secrets, data
directories or initial release pointers. Test's initial `--healthcheck` may
accept an absent data directory/state database; the service creates these
under the configured test data root on startup, before `/healthz` is checked.
Existing legacy
`xl-qqbot-debug.service` definitions are preserved and are not installed by a
new bootstrap; this change does not stop or disable a pre-existing live debug
unit. Do not expect test-backend branch automation until the new poller mapping
is active.

Prepare the external secrets files without putting values in shell history or
terminal logs:

```bash
install -d -m 700 ~/.config/xl_deploy
umask 077
touch ~/.config/xl_deploy/secrets.env
touch ~/.config/xl_deploy/router.token
chmod 600 ~/.config/xl_deploy/secrets.env
chmod 600 ~/.config/xl_deploy/router.token
install -d -m 700 ~/.config/xl_updata_server ~/.config/xl_updata_server-test
touch ~/.config/xl_updata_server/api.env ~/.config/xl_updata_server-test/api.env
chmod 600 ~/.config/xl_updata_server/api.env ~/.config/xl_updata_server-test/api.env
```

It must contain `GITHUB_TOKEN=...` for a token limited to read-only repository
and Actions metadata. Separately create
`~/.config/xl_deploy/router.token` containing the router bearer token; it must
match the deployment token in the existing bot config and be at least 32 random
characters. Both files must be mode `600`; config directories should be `700`.
Never place credentials or the production assetbundle key in Git.

Provision distinct backend tokens in the two `api.env` files above. Keep
`/home/admin/xl_updata_server/config.toml` and `/home/admin/xl_updata_server/data`
for production. Provision separate `/home/admin/xl_updata_server-test/config.toml`
and `/home/admin/xl_updata_server-test/data`; set `[server].environment = "test"`,
`[api].enabled = true`, port `8791`, and a distinct `[paths].data_dir` there.
Production must declare `environment = "main"`, API port `8790`, and its own
data path. The deploy config also requires distinct `test_backend_config` and
`test_backend_data` values; never alias either to production or to
`releases_root`.

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
Before enabling, explicitly seed the `test` and `main` pointers from their
verified branch/SHA into new immutable releases and preflight their per-unit
virtualenvs; never point `current/` at an old mutable service checkout. The
debug branch is CI-only and does not require a current release pointer; its
first exact-SHA CI success is recorded as a no-op cursor. Then ensure each
runtime pointer targets a verified/preflighted release and
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
systemctl --user enable --now xl-qqbot-test.service
systemctl --user enable --now xl-updata-server.service xl-updata-server-test.service
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
starts services and checks readiness, then resumes routing. Backend-only
updates also notify and pause/drain their corresponding service tiers:
test-backend updates affect `debug` and `test`; main-backend updates affect
`production`. Only after health checks pass does it durably enqueue
`更新完毕`; any new non-empty project announcement follows in FIFO order.

Optional version announcements are maintained in
`xl_deploy/announcements/<change-id>.md` in the same candidate commit as the
runtime change. The poller reads only changed announcement files from that
exact candidate SHA, sorts multiple files by path and combines them within the
4000-character message limit. Test and main promotions each announce their own
changed note. Missing, empty, unreadable, or oversized notes never block an
otherwise healthy deployment; only the lifecycle messages are sent. GitHub
Release notes are not used as deployment announcements.

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
