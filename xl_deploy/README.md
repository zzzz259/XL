# XL branch deployment controller

The controller is a one-shot user service, invoked every 60 seconds by
`xl-deploy-poll.timer` using the venv at
`current/main/.venvs/xl-deploy-poll.service`. The poller is not in any deployment
stop list, so a pass already running can finish on its started release while a
main pointer changes. It checks `debug`, `test`, and `main`; a commit is
deployable only after GitHub Actions workflow **XL CI** succeeds for that exact
branch and 40-character SHA. Pending/failed CI does not advance the saved
cursor. Changes outside a branch's service mapping are recorded as no-ops.

## Branch snapshots and legacy service map

| Branch | Immutable pointer | Services affected |
|---|---|---|
| `debug` | `/home/admin/xl_deploy/current/debug` | CI/integration only; no runtime units |
| `test` | `/home/admin/xl_deploy/current/test` | Inventory mode: every enabled instance with `watch_ref = "test"` whose role is affected. Legacy mode: fixed test units below. |
| `main` | `/home/admin/xl_deploy/current/main` | Inventory mode: every enabled instance with `watch_ref = "main"` whose role is affected. Legacy mode: fixed main units below. |

The intended runtime architecture is one transport-only Gateway plus one or
more Bot instances. A Gateway routes raw events by explicit group-to-instance
ownership and relays authenticated outbound envelopes to the QQ API. Bot
instances own their feature configuration, recipient groups, queues, and data.
Branch labels represent code snapshots and do not grant or deny features.

The controller supports an optional, hand-maintained `[inventory.gateway]` and
`[[inventory.instances]]` configuration. It can deploy multiple instances that
watch the same branch snapshot; each instance retains its own config, data
roots, groups, listener ports, and systemd units. The fixed map below applies
only when `[inventory]` is absent, for migration compatibility. A Bot instance
may also declare `backend_config_path` when it owns an updater service; this
keeps the Bot and updater TOML files separate while assigning both services to
one logical instance.

Copy the inventory example from [config.toml.example](config.toml.example).
Give every instance a stable `instance_id`, select its code snapshot with
`watch_ref` (`test` or `main`), and assign unique units, ports, group IDs,
external config paths, and exclusively owned mutable data roots. Bot and
updater paths can be nested under the same instance root; another instance may
not claim that root. Each Bot's update sources must point only to outboxes in
that same instance's roots. Configure the
Gateway once with its own unit, code ref, config, data root, and port. Its
`[router].group_instances` and worker port map must match the inventory exactly.
Every referenced unit must already be installed as a systemd user unit and
must load the corresponding external config and `current/<watch_ref>` release.
Each routed Bot instance must have a distinct transport token in both the
Gateway config and its Bot config; the dry-run checks token IDs and matching
values without printing them.

Validate a prepared inventory without loading GitHub/router bearer tokens or
changing services, pointers, configs, or data. It reads the referenced runtime
TOML files to check identity, ports, routes, and owned paths, but prints no
credential values:

```bash
python -m xl_deploy --config /path/to/config.toml --dry-run-inventory
```

The read-only summary reports instance IDs, refs, units, ports, counts of owned
groups/data roots, and fails if the referenced Bot/Gateway configs disagree or
mutable runtime paths escape their declared roots. It intentionally omits group
IDs and credential values. If no inventory exists, it reports that legacy
test/main mapping is active.

Before enabling inventory mode, retain the old test/main configs and data in
place, translate each Bot and its updater into one explicit inventory entry
(using `backend_config_path` for the separate updater TOML), and review that
group ownership, service units, listener ports, mutable paths, and Gateway
routes match. The controller validates the inventory and
the referenced runtime configs before any maintenance, drain, or service stop;
it does not rewrite configs, move data, create units, or infer ownership. Add a
new instance by provisioning its unit and config, declaring its unique roots,
ports and groups, reviewing the dry-run, then allowing the next code update for
its `watch_ref` to preflight and start it. Inventory-only edits do not force a
code redeployment or bootstrap new units. An operator can run the validation
locally; there is no remote discovery step.

`debug` remains CI-only. The controller polls debug/test/main in the existing
fast-forward sequence; test/main are code snapshot refs, not product tiers.
Runtime instances must watch `test` or `main`; `debug` is rejected as a runtime
watch ref so it cannot silently remain undeployed.

| Legacy loopback port | Process | Purpose |
|---:|---|---|
| 8782 | `xl-qqbot-test.service` | legacy test worker |
| 8783 | `xl-qqbot-prod.service` | legacy main worker |
| 8784 | `xl-qqbot-control.service` | legacy deployment control API |
| 8785 | `xl-qqbot-router.service` | legacy single Gateway |
| 8786 | `xl-qqbot-test-notify.service` | legacy test notifier |
| 8787 | `xl-qqbot-main-notify.service` | legacy main notifier |
| 8790 | `xl-updata-server.service` | legacy main updater |
| 8791 | `xl-updata-server-test.service` | legacy test updater |

All Bot-internal endpoints bind only to `127.0.0.1`; keep them closed to public
ingress. `8781` is an old debug field in this topology. Existing test and main
configs and data roots remain separate. New outbound messages are authenticated
and tagged with their owning instance ID, and are relayed through the Gateway;
durable queues remain instance-owned.

Existing test and main `xl_updata_server` configurations and bearer secrets
remain separate. Each updater's `[polling].enabled` value directly controls its
scheduled polling; environment labels do not override the setting. GitHub
deployment itself never starts a game update. Both backend services are placed
in `xl-updata.slice`, capped at one CPU core in aggregate.

Legacy group/tier fields may still be read as migration inputs. Inventory
mode maps each group directly to exactly one instance ID and rejects duplicate
ownership. Compatibility fields do not authorize or disable features.

In legacy mode, point the test worker and test jobs' `[watch]` data root, `character_data`,
`versions_dir`, and `outbox_dir` at the test backend's data tree. Main worker and
main jobs use the main backend tree. Each jobs process sends proactive messages
to its matching notifier, whose separate FIFO and media spool live below that
release's configured `watch.data_dir/notifications/{test,production}`. Test
updates therefore queue only to the configured test groups; main updates and
Bilibili notices queue only to the configured main groups. A manual test-server
run uses the ordinary update pipeline and writes events and artifacts only to
test data.

In inventory mode, repeat these paths per instance and ensure each configured
Bot outbox and update-source outbox is within that instance's declared data
roots. The controller verifies those ownership boundaries before service
changes.

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

In the legacy fixed-unit topology, the deployment-control API is a separate
`xl-qqbot-control.service` process on
`127.0.0.1:8784`; it retains the controller's announce, maintenance, and drain
contracts. It calls the Gateway coordination API on `8785` and the test/main
notifier APIs on `8786`/`8787`. The Gateway API tracks readiness and in-flight
requests so deployments can pause and drain before stopping workers. None of
these services is exposed outside loopback.

These fixed ports and release ownership apply only when `[inventory]` is absent.
In inventory mode, deployment control, Gateway, notifier, jobs, worker, and
updater units are selected from the hand-maintained inventory and updated when
their configured code snapshot changes. A start notice is a deployment gate
only after every target group's delivery is acknowledged; durable queue
insertion alone does not prove that QQ received it.

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

## Manual setup and cutover for the legacy topology

The commands in this section install only the fixed legacy test/main units.
Inventory mode requires operators to install the unit names declared in
`[inventory.gateway]` and `[[inventory.instances]]` with each instance's
external config and `current/<watch_ref>` paths. Do not mix the legacy install
commands with inventory-defined units.

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
`~/.config/xl_deploy/router.token` containing the internal Bot API bearer token;
it must match `router.deployment_token`, `gateway.control_token`, and
`notification.api_token` in the relevant external Bot configs and be at least
32 random characters. Both files must be mode `600`; config directories should
be `700`.
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
send and confirm delivery of
`检测到更新，正在更新bot，期间将暂停服务` before placing the old router/tiers
into maintenance and stopping them; keep all existing state/data directories
untouched. The account's user systemd
manager must persist after logout; have the server operator arrange linger
through the approved host process. Ports 8782–8787 remain loopback-only; do not
open them in a firewall. Only after those checks should you run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now xl-qqbot-router.service xl-qqbot-prod.service
systemctl --user enable --now xl-qqbot-test.service xl-qqbot-test-notify.service
systemctl --user enable --now xl-qqbot-main-notify.service xl-qqbot-control.service
systemctl --user enable --now xl-updata-server.service xl-updata-server-test.service
systemctl --user enable --now xl-qqbot-test-jobs.service xl-qqbot-main-jobs.service
systemctl --user enable --now xl-deploy-poll.timer
```

## Update order, recovery, and notices

For inventory-managed Bot instances, the controller first asks deployment
control to durably enqueue for each selected `instance_id`
`检测到更新，正在更新bot，期间将暂停服务` before pausing/draining and stopping
affected services. A successful response means each selected instance's
Notification Service durably accepted the message; it does not mean QQ has
already delivered it. Deployment
continues during the 02:00–08:00 Asia/Shanghai send curfew, while the queued
start/completion/release/rollback messages wait for 08:00. It stages and
preflights the immutable release first, switches only that branch pointer,
starts services and checks readiness, then resumes routing. Backend-only
updates also pause/drain the configured instance when that backend service is
part of a Bot instance. A backend-only instance has no Bot announcement or
Gateway maintenance scope. Only after health checks pass does it durably enqueue
`更新完毕`; any new non-empty project announcement follows in the destination
instance's notifier FIFO.

Optional version announcements are maintained in
`xl_deploy/announcements/<change-id>.md` in the same candidate commit as the
runtime change. The poller reads only changed announcement files from that
exact candidate SHA, sorts multiple files by path and combines them within the
4000-character message limit. Test and main promotions each announce their own
changed note. Missing, empty, unreadable, or oversized notes never block an
otherwise healthy deployment; only the lifecycle messages are sent. GitHub
Release notes are not used as deployment announcements.

Each notification request carries an idempotency key derived from the durable
deployment transaction ID and phase (plus instance ID). Replaying a transaction journal
therefore does not add duplicate queue rows. If the selected notifier cannot
persist a notice, the control API rejects the request and the deploy transaction
must not proceed past the start-notice gate. Queue delivery remains at-least-once: a crash
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
journalctl --user -u xl-qqbot-control.service -n 100 --no-pager
journalctl --user -u xl-qqbot-test-notify.service -n 100 --no-pager
journalctl --user -u xl-qqbot-main-notify.service -n 100 --no-pager
```

QQ delivery is **at-least-once**: if QQ accepts a message but the process exits
before persisting delivery, recovery may resend it. The QQ sender has no
idempotency key, so exactly-once delivery cannot be promised. A pending notice
blocks another transaction until recovery resolves it.

```bash
systemctl --user status xl-deploy-poll.timer xl-deploy-poll.service
journalctl --user -u xl-deploy-poll.service --since today
```
