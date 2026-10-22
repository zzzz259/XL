# 抽卡数据、统计与配置契约

[用户指南](gacha-user-guide.md) · [构建与测试](gacha-development.md) · [服务端 HTTPS](../xl_updata_server/docs/gacha-config-hosting.md)

## 数据边界

Windows：浏览器 UI → loopback `app.py` → 官方认证/网关；`archive.py` 将记录保存 SQLite。启动窗口管理本机服务，CDP 只管理专用官网登录浏览器。Host/Origin 白名单、请求大小和并发限制约束本机 API。

Android：内置本地域名 UI → 受约束的原生桥 → GachaClient/Wire；IndexedDB 保存各账号原始记录。官网使用独立 WebView，主页面桥不注入官网。`allowBackup=false`，备份由玩家显式导出。两端的游戏认证和网关直连，配置请求也不携带账号信息。

记录身份由官方验证的 UID/渠道组成，去重根据原始记录 ID/卡池/序号与内容。JSON 保留原始 item_id、pool_id、数量、时间及账号身份；CSV 旧格式无唯一身份时保守处理。配置更新按一份快照重映射原始 ID，保持记录数量/时间；保存的 catalog 指纹变化才触发重算。

## 统计

`analysis.py` 与 `mobile-engine.js` 保持普通、限定、典藏、启程和未知分类一致。保底组来自 catalog 的 `pity_group`，跨池继承只在同组连续历史成立；未知不伪造 UP。平均抽数=可见分类总抽数/五星数；首个五星与断档为下界；序号 80→1 连续。具体用户边界见使用指南，不将可见历史推断当成官方实时状态。

## 公共配置生产与分发

现有更新器定向下载包含五表的 Lua bundle，既有静态 parser 只读取字面量及 T(id) 标记，从不执行 Lua。`gacha_catalog.py` 只输出非个人配置；`BaseGachaBottomPool` 已加入 selector，extractor 自动沿用该白名单。解析后先在 staging 写 `gacha_config`，游戏版本成功发布后验证并镜像到 `data/gacha_config`。失败保留上一次公开版本且不阻断角色更新；同一游戏版本不会自动重跑，可用 CLI 补发。

`current.json` 字段：format=`starfall-gacha-catalog-manifest`、schema_version=1、version、file=`catalog-<version>.json`、sha256 小写64位、size 精确字节数、published_at UTC。版本是规范正整数十进制字符串，最多20位，避免 JavaScript 大整数损失。版本文件最大4MiB，manifest最大16KiB。版本 JSON 先落盘，再原子切换 current；同版本不同字节和降级拒绝。公开 JSON0644、公开目录0755；私有锁/账号/密钥不放宽。

catalog schema_version=1，cards 为正 ID → `[名称,星级]`，pools 为正 ID → 名称；两表均1..20000项，ID≤2^53−1，名称1..120字符且不含 ISO 控制字符，星级1..5或null。selectors 为1..8个唯一正 ID。pool_meta 精确覆盖 pools，category 为 normal/limited/collection/beginner/unknown，pity_group 为1..80位 `[A-Za-z0-9:_-]`；featured_ids/names等长、≤128、ID唯一且必须引用对应同名五星；hard_pity 必须存在，为null或1..1000。

类型0常驻、类型1启程；group1+bottom_type201限定，group2+bottom_type301典藏，其余未知。UP只保留 main_card_ids 中五星；硬保底取 bottom_pool 中type1的bottom_num。内置基线108卡片/89卡池来自版本134359843258232660，非个人数据。

客户端只接受完整 HTTPS manifest URL，禁止认证信息、query/fragment、编码/重复斜线/dot段路径及跳转，使用系统证书验证。先限长读取manifest，再限长下载同目录版本文件，验证hash/size/schema/版本与引用。正常成功最多6小时检查一次，失败退避。校验成功才原子提交本地指针，损坏回退内置或上次已验证缓存；从未预设 test 源。缓存尚无长期 blob 容量回收策略，部署 release 保留策略不负责客户端缓存。

`xl_gacha_web/scripts/update_catalog.py` 仅用于开发者重建内置基线，不是运行时远程发布器；公共发布用 server 的模块 CLI。静态公网只开放两个严格 JSON 路径规则，不开放控制 API/整个 data。Windows 与 Android 二进制更新分别依赖完整ZIP迁移和同签名覆盖安装，公共配置不下载代码。
