# Bilibili 渠道：包体及调用链差异

> 历史研究归档（2026-10-23～24）。文内“尚未接入”“待验收”和测试版本号描述的是当时阶段，不代表当前发布状态。B 服与 TapTap App 入口已纳入 Android 0.3.6，真机 TapTap 授权修复已合入 `debug`；授权后仍需手动切回，通知返回方案未保留。当前流程以[渠道登录说明](../gacha-channel-login.md)、[用户指南](../gacha-user-guide.md)和源码为准。

核对日期：2026-10-23（北京时间）。静态样本根目录：官服 `E:/All-Projects/XL/base`；B 服 `E:/All-Projects/XL/base-bili/xl_2.0.4_20250411_020613_42f92`。后者外层另有 2,646,094,845 字节、后缀为 `.rar` 的文件。后续按用户说明检查文件头及 aapt，确认它实际是原始 APK（ZIP），已复制到专用工作区并成功安装，没有重新封装。哈希索引只统计解包后的样本根，不重复计算 APK。没有全量反编译。

## 样本及版本边界

| 对比项 | 官服 | B 服 | 证据 | 对适配的影响 |
|---|---|---|---|---|
| 格式 | APK 解包目录 | APK 解包目录，外层 `.rar` 实为可安装的原始 APK | 根文件、DEX、二进制 AXML；APK 文件头 PK、aapt、ADB 安装成功 | 已能直接在 MuMu 安装原版 B 服 |
| 文件数量/字节 | 1,265 / 407,769,867 | 11,559 / 2,802,380,809 | `official-hashes.json`、`bilibili-hashes.json` | B 服资源更完整，数量不可直接解释为渠道代码差异 |
| Manifest 包名 | `cn.haoplay.game.and.elpis` | `com.haoplay.elpis.bilibili` | 两份 `AndroidManifest.xml` 的 AXML manifest 节点 | 两个原版应用身份不同 |
| Manifest 版本 | 2.0.0 / code 23 | 2.0.4 / code 104 | 同上 | 不同版本对比，不能把所有差异归因于渠道 |
| SDK 平台 | min 22 / target 31 | min 24 / target 31 | uses-sdk | 独立宿主能力需分别判断 |
| DEX | 6 个 | 4 个 | 根目录 classes*.dex | 数量不代表业务协议差异 |
| 引擎 | IL2CPP + XLua；arm64/armeabi-v7a | IL2CPP + XLua；arm64/armeabi-v7a，UnityFS 标识 2021.3.32f1c1 | `lib/`、`assets/bin/Data/Managed/Metadata`、bundle 头 | 主要游戏业务不在 Java；无需全量反编译 SDK |
| 登录 SDK | 已有 PnSDK/TapTap 路线 | 新增 BSGameSdk/GSCPubCommon 桥；仍包含 PnSDK | B 服 `classes3.dex`，下文调用偏移 | SDK 共存不能说明主登录仍用 PnSDK |
| 游戏认证入口 | `https://elpis-accverify.haoplay.com.cn/v1/account` + `/login` | 相同 | 两份 `assets/bin/Data/96975748ef58940f3986e68419e8f420` 中 TextAsset `android_cfg`；B 服 LoginMgr | 不支持“必须另找完全不同认证服务器”的初始假定 |
| 客户服务入口 | `https://elpis-cli.haoplay.com.cn/v1/cli` | 相同 | 同一 android_cfg | 共享地址不等于共享账号数据 |
| 游戏渠道 | 现有工具固定 `haoplay_cn` 进入网关 | `LuaUtil.GetChannel` 委托 `SDKManager.GetSDKChannelForServer`，返回字面量 `bilibili`；独立认证以此值成功 | ARM64 分支 `0x8d66c8 → 0xcca2ac`、literal index 7120；auth-once-result.json | 已确认认证渠道是 bilibili；不能与 SDK 缓存 bilibili_zh 混用 |
| SDK 渠道枚举 | 官服分支 `PnSDK_CN` | Lua 明确 `Bilibili` 分支 | LoginWindow、LoginService | SDK 枚举不等于 HTTP 或网关 channel 字符串 |
| 游戏网关 | 历史实测动态 `:8801` | SDK 会话及独立 App 授权均返回 `elpis-gw.haoplay.com.cn:8801`；后者角色态 0、actorExist=true | 15:47 初次认证及 18:43/18:45 独立包实测 | 与现有白名单兼容，协议可复用；不据此合并两渠道角色命名空间 |
| 登录协议 | 1002 / 101002 | 相同 ID 和字段定义 | Proto、LoginService | 序列化层具备复用条件 |
| 历史查询 | 10504 / 110504，poolId → records | 相同；18:45 selector=1 返回码 0、空记录 | Proto、LotteryService、独立包实测 | 业务请求已成功，非空实际记录及所有选择器仍待验收 |

逐文件 SHA256 对比：同路径同哈希 **859**；同路径内容不同 **272**；仅官服 **134**；仅 B 服 **10,428**。完整分类在研究目录 `diff.json`。例如两 ABI 的 `libxlua.so/libmain.so` 相同，`libil2cpp.so/libunity.so` 不同；版本差异是后两者的重要混杂因素。

## B 服登录的实际调用链

DEX 偏移为文件内 `code_item` 偏移，来自修正指令宽度后的定向扫描，非源码行号：

1. Manifest 指定 `com.unity3d.player.App`；其 `onCreate`（classes3.dex `0x5f6db0`）调用 `GSCPubCommon.applicationAttach`。
2. `UnityPlayerActivity.onCreate`（`0x5fe9b8`）初始化 PnSDK，并调用 `BSGameSdkCenter.init`（`0x5f83ac`）→ `GSCPubCommon.init`。
3. `BSGameSdkCenter.login`（`0x5f8454`）→ `GSCPubCommon.login`，回调对象为 `BSGameSdkCenter$6`。
4. `$6.onSuccess`（`0x5f7ba4`）从 Bundle 读取 `uid / username / access_token` 等字段，封装 JSON，以 action `Login` 回调；随后存在 PnSDK `trackAccount` 调用。后者是统计调用，不能误读为把 B 服令牌交换成官服 JWT。
5. `BSGameSdkCallBack.unity3dSendMessage`（`0x5f6ec0`）经 `UnityPlayer.UnitySendMessage` 调用 `OnBSGameSdkCallback`。
6. `LoginWindow.lua:35–44` 的 Bilibili 分支注册 `LoginMgr.LoginCallback`。该回调（反编译文件约 429 行起）要求 `code == 1`，解码 detail，把 `detail.uid → LoginData.openId`、`detail.access_token → LoginData.token`。
7. 玩家继续登录时，`LoginWindow.lua:108` 调用 `LoginMgr.ConnectLoginServer`。`LoginMgr.lua:129–158` 向配置中的 `/login` 提交六字段，成功后使用 `data.addresses` 建连接。
8. `LoginService.lua:9–20` 发送 EnterGameReq，仍使用 LoginData 中原始令牌和外部账号 ID；channel/deviceId 来自 LuaUtil，authRemote 来自 SDKManager。

这里没有发现“必须由查询工具独立调用 B 站厂商服务端验签”的客户端步骤。服务端内部如何验证渠道令牌不能从本客户端证明，也不能由此断言厂商权限限制已不存在。

B 服还有 SDK `StartHeart/StopHeart`、区服/角色通知等行为（LoginMgr ConnectSocket、LoginService DealEnterGameRsp）。18:43/18:45 的短会话未发送这些通知，仍成功完成角色态及一次空历史查询；不推断长连接和全部业务也不需要。本研究不发送创建角色、充值或其他写入业务。

原版 SDK 内部存在 App access_key → `/api/external/token.exchange/v3` → SDK 用户模型的步骤，但工具不必当然复刻该链路。后续已用当前账号公开 UID 与新的 App 显式授权结果直接通过游戏认证、角色态和空历史查询，详见 [授权入口研究](bilibili-channel-authorization.md)。

20:15–20:21 又独立复现 SDK 签名交换，自动取得 UID 与 SDK access_key；两次直接使用交换后的 SDK 令牌成功完成认证、已有角色态和 selector=1 空历史，最后一次原版游戏进程关闭。由此 Android 正常流程可以不填写 UID，手填作为备用；独立实现使用《星落》客户端接入配置，不需要完整原版 SDK 运行时或原版设备 ID。客户端配置值只在专用工作区，未写入仓库。

## 协议核对

B 服内置 Data manifest `data_9a5f7138a2c41f2632337c5667d50603.json` 指向 `assets_lualogic.bundle` 的哈希 `f1fc6adc3d62034c19299b7bca006200`。实际 bundle 前 32 字节为封装头，UnityFS 从 offset 32 开始。复用已有资源解析和 Lua 工具，仅定向提取登录、网络、Proto、LotteryService 等文件，未处理无关贴图/音频。

官服原始包声明的 Lua bundle `199fc6c9a623e0035001d5e2f4131102.bundle` 不在官服样本中。因此字段比较明确使用现存官服更新资源 `xl_updata_tool/output/lua/134359843258232660/Proto.lua`，不声称已对缺失的 2.0.0 内置 Lua 做比较。

八种消息的字段名、类型、编号完全相同：`Head / Msg / EnterGameReq / EnterGameRsp / GetGachaRecordsReq / GetGachaRecordsRsp / GachaRecord / ItemTuple`。本轮程序断言已通过。

| 消息 | 关键字段 |
|---|---|
| EnterGameReq | token=1、secret=2、deviceId=3、channel=4、platform=5、authRemote=6、openId=7、graphicsDeviceID=8、graphicsDeviceVendorID=9、loginIP=10 |
| EnterGameRsp | actorExist=1、actor=2、sysStamp=3、svrVersion=4、settings=5、openSvrTime=6 |
| GetGachaRecordsReq/Rsp | 请求 int32 poolId=1；响应 repeated GachaRecord records=1 |
| GachaRecord | poolId=1、gachaDrop=2、timestamp=3、uin=4、index=5、recordId=6 |

现有 Python 解析器读取除 uin 外的记录字段；合成符合 B 服 schema 的数据被现有解析器正确读取并去重。该检查不证明真实账号、全部卡池配置及所有服务器业务行为相同。

## 可复核产物

研究目录 `E:/AI-Agent/OpenAI-Codex/bilibili-channel-research/`：

- `inspect_packages.py`、两份 hashes/manifest/symbols JSON、`summary.json`、`diff.json`。
- `trace_glue.py`、`bilibili-glue-trace.json`：脱敏后的类、函数及调用偏移。
- `extract_login_lua.py`、`bilibili/`：本地定向研究资料，不进入 Git。
- `verify_channel_delegate.py`、`verified-channel-delegate.json`：离线核对 ARM64 `LuaUtil.GetChannel` 到 SDKManager.GetSDKChannelForServer 的分支和字符串引用，确认 `bilibili`，无需运行时附加。
- `sdk-callback-shape.json`：用户正常登录回调的 UID/access_token 存在，令牌格式不透明，与同包 PnSDK JWT 不同；只记录布尔状态和类型。
- `auth-once-result.json`：经用户明确授权后，独立认证 HTTP 200、retCode=0、动态网关地址。凭据只在进程内使用，无令牌值或账号值落盘。
- `verify_static_contract.py`、`offline-contract-results.json`：字段对比及合成验证。

所有结论的运行边界和后续验证见 [可行性报告](bilibili-channel-feasibility.md)。
