# 本机凭据与分阶段 SDK 续签

记录页「更新」优先读取当前账号的原生凭据，更新记录和玩家资料。缺少、过期或被游戏服务拒绝时进入查询页；网络失败保留记录页。旧版未保存凭据的账号需要先登录查询一次。

成功查询的凭据按 `SHA-256(provider:uid)` 的既有账号 ID 隔离，使用 Android Keystore AES-GCM 加密，存入应用 no-backup 目录并通过 AtomicFile 原子替换。JWT、B服 token 与 SDK 状态不进入 WebView 存储、抽卡报告、JSON/CSV 记录备份或诊断。删除账号同时清除其凭据；「我的 → 登录凭据」可以只清凭据并保留记录。B服令牌没有已确认的 JWT exp，因此不虚构七天期限，实际有效性由查询服务校验。

0.4.2 不提供自动续签开关或 SDK 状态导入入口。以下为保留的研发实现，尚未向普通用户开放：自动续签逐账号默认关闭，只对开发测试中导入完整、匹配且尚有效的 SDK 状态的账号开放；普通网页登录、TapTap 仅有 JWT、B服仅有另一套 token 时不能启用。导入文件兼容本地 `starfall-refresh-kit` 的 `version:1, profile:{appkey, signing_input, params}, credential:{open_id, channel, token}`，包含本人私密状态，不能附在工单或通用发布包。导入后开关仍关闭。

启用后 Android JobScheduler 每六小时在有网络时检查，重启后由系统恢复；前台刷新也先检查。只在 `vfp + 60 秒` 后、`exp` 前请求续签；失败尝试至少间隔十五分钟。候选需匹配 uid/appid/provider，iat 推进、vfp/exp 合法，并经游戏登录门验收，才替换旧凭据。网络、候选验收和写入失败均保留旧状态；SDK 要求人工授权时暂停重试，重新登录或导入有效状态后恢复。

Android 的省电、强制停止、设备离线、长期未运行和厂商策略都可能使任务错过有效期，过期必须人工登录。历史研究证明过一次真实独立续签，本次自动测试使用合成材料与受控网络；多周期真实续签仍需自然时间窗口验收，不能承诺永久登录。国内端点全部直连。

原生白名单桥：`credentialStatus` 返回非秘密状态；`credentialImport` 导入当前账号状态；`credentialConfigure` 显式开关；`credentialDelete` 清除；`querySaved` 在原生读取/续签/查询。页面 `/api/library/refresh` 在合并前再次校验返回账号 ID。服务端没有账号凭据存储或续签接口。

公共公告另从配置源同目录 `app-news.json` 下载纯文本，格式 `starfall-gacha-app-news` / schema 1、16KiB、最多10条，每条标题和正文总共600个Unicode码点；正常缓存六小时，失败保留已有内容。它独立于卡池版本，不下载或执行代码。服务端尚未部署时客户端显示暂未发布更新内容。
