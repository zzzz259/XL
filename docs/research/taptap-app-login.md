# TapTap App 授权与充值门户登录

> 历史研究归档（2026-10-23～24）。文内“尚未接入”“待验收”和测试版本号描述的是当时阶段，不代表当前发布状态。B 服与 TapTap App 入口已纳入 Android 0.3.6，真机 TapTap 授权修复已合入 `debug`；授权后仍需手动切回，通知返回方案未保留。当前流程以[渠道登录说明](../gacha-channel-login.md)、[用户指南](../gacha-user-guide.md)和源码为准。

2026-10-24，针对手机已登录 TapTap、手记仍要求网页重新登录的反馈，已完成公共脚本分析、MuMu 复现，以及清空网页登录状态后的独立全流程验证。随后已接入 Android `0.3.5-tap-test` / 403 的测试入口，并从包内完成查询。该实现通过官方扫码授权复用本机 TapTap 已登录状态，再沿原充值门户授权、认证和查询；尚未正式发布，实体手机兼容性待用户验收。

## 已确认

- 当前 Android `LoginActivity` 将 HTTPS 保留在 WebView；仅当 `hasGesture()` 为真且协议为 `tap` 或 `taptap` 时尝试外部 App，其余非 HTTPS 跳转被拦截，启动异常被忽略。由脚本延迟发起的跳转、其他官方协议及 Intent URL 是否影响本次反馈，需要观察实际跳转后确定。
- 官方充值门户动态加载 `https://p4cn.17995api.net/static/jsapi/PnSDK-h5.js?runtime=v2`，进入 `https://platform.17995cdn.com/assets/html/pages/main.html`。
- 其 `js/main.js?v=876` 的 TapTap 分支调用 `window.sdk.launchTapAuth()`。
- `https://platform.17995cdn.com/assets/js/public/sdk.js?v=876` 中 `launchTapAuth()` 生成 PKCE/state，将官方回调设为 `p4cn.haoplay.com.cn/rest/endpoints/tap`，随后 `window.open('https://accounts.taptap.cn/authorize?...', '_blank')`。它同时轮询官方结果端点，收到结果后继续 PnSDK 登录。公共脚本保存于 E 盘专用研究目录。
- 门户当前入口明确采用网页登录；国内 `/login` 页面默认手机号登录，扫码模块 `qrcode-login-bd33d847.js` 提示通过 App 扫描，页面中的 App 链接是下载链接，并未自动唤起本机已登录账号。此前推测的 scheme/Intent 拦截不是已确认的首要原因，单纯放开更多协议不能产生缺失的 App 登录入口。

## 已走通的路径

1. 保持原门户发起的 `accounts.taptap.cn/authorize` 请求及其 PKCE/state。未登录时进入官方 `/login` 并选择扫码。
2. 官方 `/api/device-code/get` 返回本次挑战；其 `qrcode_url` 为 `https://accounts.taptap.cn/device`，查询参数名为 `qrcode、user_code`。原网页继续通过 `/api/device-code/login` 等待结果。挑战值仅在研究进程和 WebView 内存使用，不写入报告或源码。
3. 从已安装 TapTap 公共 APK 的路由表确认 `/to` 对应 `/base/common/web/page`，通过 `taptap://taptap.com/to?url=<编码后的官方 device URL>`、指定包 `com.taptap` 打开官方扫码确认页。App 直接使用已登录账号，无需手机号、密码或验证码。
4. 用户在官方 App 勾选协议并确认本次网页登录，返回手记后，原网页进入 `/authorize` 的第三方授权确认页。保留此确认；没有自动跳过授权。
5. 完成官方回调后，原门户轮询 `rest/v1/tapauth.php` 接收结果，继续自身身份交换。手记只在原白名单门户来源接收 JWT，沿已有游戏查询链路完成查询。

不需要另接原生 TapSDK、提取 TapTap 私有会话、使用服务端私钥或改变充值门户的 OAuth 回调。不要把其他应用的 TapTap openid 当作星落身份。

## 实机证据

- MuMu Android 15 / API 35；TapTap `com.taptap`，`2.96.10-mkt#100000`；系统 WebView `110.0.5481.154.1`。
- 2026-10-24 01:12 首次完整查询成功。随后重复查询仍为同一份 243 条记录、1746 抽，没有重复累计。
- 11:47–11:50 清除独立测试版官方站点的 Cookie、localStorage 等网站数据，保留手记 IndexedDB 存档及 TapTap App 会话。确认网页回到 `/login`，重新扫码交给 App，在同一个门户请求内完成两处官方确认，11:50:24 查询成功。
- 选择器 `1、2、24000001、24000002` 的返回码均为 0，分别返回 `80、80、3、80` 条记录，共 243 条，统计 1746 抽。
- 未输入手机号、密码或验证码；未读取 App 私有目录；未运行游戏、抽卡或创建角色。账号 UID、令牌与完整记录未进入本研究文档或脱敏报告。
- 中间脚本、公共 APK、公共前端文件及脱敏证据在 `E:\AI-Agent\OpenAI-Codex\taptap-login-research`。`query-evidence-clean-retest.json` 为独立清会话复测回执。

## 接入方案与限制

- 增加“使用已登录 TapTap 授权”操作：在严格匹配的官方登录页捕获本次官方二维码挑战，用户点击后交给指定的官方 App；继续保留原网页，返回后完成官方第三方授权。普通玩家不需要看到或复制二维码值，也不需要研究工具或 root。
- 可在官方登录页注入只针对 `/api/device-code/get` 响应的短期监听，并用 `evaluateJavascript` 取回 `qrcode_url`；实际封装时必须限制主页面来源、HTTPS 主机与 `/device` 路径、大小及生命周期。网页 hook 和 App 路由均属于尚未稳定封装的接入点，需提供失败提示及原网页登录备用。
- 不自动勾选或确认玩家的官方授权，不把挑战当作长期凭据缓存；取消、超时、切号和 App 不存在应可恢复。官方 SDK 等待交换结果的时限为两分钟；本轮早期调试曾超过时限，不能继续复用过期请求，最终清会话复测已在同一次请求内完成。
- 当前 `LoginActivity` 将父页和子页同时按权重显示，授权页面可能只占半屏，属于需要修正的显示/交互问题。返回手记需继续轮询；App 确认后的自动回跳未验证，当前用户手动返回即可完成。
- **构建基线更正**：研究包从 `E:\All-Projects\XL` 的较旧实现构建，未继承已验收发布工作树 `E:\AI-Agent\OpenAI-Codex\worktrees\XL-gacha-config-storage` 中的完整 0.3.4。新登录清理实际 localStorage/sessionStorage、重新加载后才允许轮询的修复早在 0.3.1 已完成，并已包含于 0.3.4；它不是正式 0.3.4 的新回归。本研究包漏带该修复，才出现默认清理不完整、复用旧 JWT 的现象。此前将其表述为新发现的产品问题不准确。本轮 CDP 清站点后的 TapTap 可行性证据仍成立，但不能用此研究包评价 0.3.4 的回归情况。
- 只验证了上述 MuMu、TapTap 和 WebView 组合；更多国内 App 版本、实体手机、海外 TapTap、未登录 App、取消/过期及进程重建仍需兼容性验收。官方国内网页和 App 路由可能变化，不能据此承诺所有版本稳定。

版本口径：基础功能归入 0.3.5；0.4 预留 UI 和信息优化。之前发布的 0.4.0-bili-test 是测试标签，旧 APK 未因文档改名而改变。改名研究包使用 401；完成下述基线合并后的独立测试包使用 402，可覆盖升级旧测试包。

版本号修改不代表已继承相应发布基线。后续正式整合必须以完整 0.3.4 发布工作树为基础再移入 B 服与 TapTap 新实现，不能只补单个清理函数。对比正式 0.3.4 APK 与研究 APK，后者同样缺少 QQ 分享回流和图片分享 Provider。实际 APK 符号与源码对比记录在 E 盘研究目录 `baseline-audit.json`。MuMu 原包 `cn.starfall.gacha.local` 仍为正式 0.3.4 / versionCode 8；本轮研究包为 `cn.starfall.gacha.bilitest`，没有覆盖原正式应用。

## 基线合并后续（2026-10-24）

按用户“先合并、先解决再开发”的要求，现已以 `5402df97` 的完整 0.3.4 客户端为基线重新整合现有 B 服实现。新登录清理、QQ 回流、相册/分享和默认配置源均已恢复；旧研究包的缺失记录保留为历史问题，不再代表当前源码。新包为 `0.3.5-bili-test` / 402，已在 MuMu 覆盖升级并保留存档，新登录及 B 服完整查询复测通过。详情见[合并与验证记录](../releases/gacha-v0.3.5.md)。TapTap App 新入口仍未开发，后续从此合并基线继续。

## 产品接入后续（2026-10-24）

在合并基线上增加 `TapTap App 授权并查询`。保留原官网协议与 TapTap 选择；进入官方登录页后准备本次扫码请求，由原生按钮指定 `com.taptap` 打开。玩家在 App 确认后手动返回原任务，完成官网授权。当前活动授权子窗口占用完整网页区域，回退时恢复上一窗口。

实现拆分为 `TapAppLogin`（严格链接校验与一次性交接）、`TapLoginController`（原窗口生命周期）、`tap-app-login.js`（限官方 `/login` 页面、限设备码响应）、`TapAppDiagnostics`（固定阶段与环境信息）。没有向官网注入手记原生桥，也不读取 TapTap 私有目录。挑战仅在内存短期保留，绑定本次手记会话与窗口代次；导航、换会话、使用后、超时及取消时失效。原门户的 PKCE/state、认证与查询保持原链路。

13:06:06 的包内验收完成：官网全新会话 → TapTap App 确认 → 原网页确认 → 游戏认证及查询 → 两账号存档保持隔离。四个选择器均返回 0，分别为 80、80、3、80 条，共 243 条记录、1746 抽，与既有存档合并后没有重复累计。该次操作只点击产品入口和官方确认，没有通过 CDP 注入或搬运授权值；CDP 仅读取脱敏结果。此前逐屏调试曾超过两分钟，原请求被正确拒绝，未复用到本次成功请求。

测试包仍是 `cn.starfall.gacha.bilitest`，版本 `0.3.5-tap-test` / 403，可覆盖之前的 402 测试包。国内 TapTap 已登录、MuMu API35/WebView110 组合通过；真机、更多 App/WebView 版本、国际版及后台进程被系统回收后的体验仍需验收。脱敏诊断含版本、设备环境、固定步骤、结果和耗时，不含 UID、设备码、完整 URL、账号凭据或抽卡明细。
