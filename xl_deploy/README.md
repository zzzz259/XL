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
The shared deployment-control API uses the Gateway's external config directory;
it can address every configured instance without adding another Bot instance
token or copying one Bot's business configuration into another.
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
# 发布目录、保留与清理

新候选仍然是独立、detached 的 Git worktree，以完整 SHA 标识。创建时先使用
`--no-checkout`，再设置非 cone 稀疏规则，最后 checkout。发布目录包含仓库根文件、
`xl_deploy`、`xl_qqbot`、`xl_updata_server` 和 `xl_updata_tool/tools/lua`；不会先落地
Windows 工具或抽卡客户端再删除。Lua jar、服务器 renderer 和依赖清单保留。
服务器还复用 `xl_updata_tool/app/features/characters/parser`；其无 Qt 解析器、
platform 诊断基础设施、shared 的 Lua/契约模块及包入口也在白名单内。
预检仍准备该分支所有服务的环境，因为它们共用 `current/<branch>` 指针。

保留策略的权威文件为 `<state_dir>/release-storage.json`，模板见
`release-storage.json.example`。它与 journal、锁和 poll cursor 一样放在发布目录外。
字段 `pins` 和 `rollback` 是绝对发布目录路径数组，分别记录人工固定版本和人工恢复
流程、备份或旧控制平面仍可能引用的版本。不要仅凭目录时间猜测可删除的版本。
`releases` 记录新部署的目录名及 `{ "branch": "test", "sha": "完整40位SHA",
"created_at": 1792500000 }`；时间为 Unix 秒。文件不存在时部署可以继续，但清理
始终拒绝执行；文件损坏时新部署在停止服务之前失败，供管理员核查。

首次启用时先核对 Git worktree 注册、全部 current 指针、运行中的共享 Gateway/
control/poller、旧 unit 的代码路径，以及人工回滚脚本和备份；将这些人工引用放入
`pins`/`rollback`。已有 journal 必须有效。未记录分支和创建时间的旧版本被视为
legacy，普通自动清理始终保留。不要为旧版本编造分支归属；已确认的分支可补录
至少最近三个版本，未知版本需通过独立的 `--include-legacy` 选项纳入人工清理。
初始空结构可按下面方式创建（执行前填好需要的保护路径；只创建，不覆盖已有文件）：

```python
import json
import os
from pathlib import Path
from xl_deploy.state import DeploymentState

state = DeploymentState('/home/admin/xl_deploy/state',
                        releases_root='/home/admin/xl_deploy/releases')
authority = {'schema': 1, 'pins': [], 'rollback': [], 'releases': {}}
with state.lock():
    path = state.state_dir / 'release-storage.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(authority, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
```

维护命令无需 GitHub token 或 router token；先审核 JSON，再使用相同选项执行：

```sh
python -m xl_deploy --config /home/admin/xl_deploy/config.toml --cleanup-releases
python -m xl_deploy --config /home/admin/xl_deploy/config.toml --cleanup-releases --include-legacy
python -m xl_deploy --config /home/admin/xl_deploy/config.toml --cleanup-releases --include-legacy --apply
```

报告中的 `protected`、`retained`、`candidates` 都把绝对路径映射到原因，`blocked`
非空时退出码为 1，整个待清理集合不执行删除；执行中发现状态变化则立即停止，
`removed` 记录已经删除的目录。dry run 同样取得部署锁以获得一致视图，只读发布内容。
清理同时验证当前分支指针、journal 的 previous/candidate（包括已完成日志的最近
回滚句柄）、人工保护路径，以及 systemd 的 ExecStart/WorkingDirectory 和全部同用户
进程的 cwd、exe、argv、maps、PYTHONPATH/PYTHONHOME/VIRTUAL_ENV。只从选定环境键
提取发布引用，不输出环境或完整命令行。Gateway 的工作目录可能在外部数据目录，
不能只检查 cwd。`/proc` 不可读或 systemd 证据不完整时拒绝执行。

每个已记录分支至少保留最近三个发布，同时保护所有小于 24 小时的版本和所有引用。
legacy 显式纳入后仍有 24 小时下限（使用目录 mtime；不声称是准确部署时间）。
仅处理指定根的直接子目录；必须匹配 SHA/UUID 名称、真实目录、Git 注册、HEAD 和
Git common-dir。未知目录、目录符号链接、路径越界、缺失/损坏的权威文件均阻止清理。
有源文件改动或未知未跟踪内容的 worktree 也拒绝删除；允许生成的 `.venvs`，删除由
`git worktree remove` 执行，避免遗留注册。不会使用裸 `rm -rf` 或 worktree prune。

自动清理只在健康部署及通知全部完成、journal 已为 completed 后执行，沿用同一
deployment.lock，且每次删除前重新读取全部保护证据；错误记录进 journal evidence，
不会回滚健康版本。未完成/失败/待重试通知的部署不会触发自动清理。首次旧版本清理
仍需人工审核 dry run，并显式启用 legacy。

升级稳定控制平面时，先备份控制平面源码、服务定义与恢复入口，保存旧路径为 pin/
rollback，在部署锁保护下从审核 SHA 准备独立的稳定代码目录及环境，再只重载/重启
部署控制平面本身。先验证它能读取现有 inventory/journal/current、恢复与 dry run，
再让普通 test 部署使用新逻辑。这个维护操作不移动 `current/main`，也不重启共享
Gateway 或生产业务服务；不要把维护升级等同于一次 main 部署。
