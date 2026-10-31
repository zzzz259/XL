# 抽卡数据、统计与配置契约

[用户指南](gacha-user-guide.md) · [构建与测试](gacha-development.md) · [服务端 HTTPS](../xl_updata_server/docs/gacha-config-hosting.md)

## 数据边界

Windows：浏览器 UI → loopback `app.py` → 官方认证/网关；`archive.py` 将记录保存 SQLite。启动窗口管理本机服务，CDP 只管理专用官网登录浏览器。Host/Origin 白名单、请求大小和并发限制约束本机 API。

Android：内置本地域名 UI（0.4 起为独立的移动端页面 `web/index.html` + `mobile-ui.css` + `mobile-app` 系列脚本与 `mobile-navigation.js`，不再打包电脑端旧版桌面 `index/app/style/theme/local/login/catalog-ui/environment`，仍复用共享 `demo.json` 与 `image-export.js`）→ 受约束的原生桥 → GachaClient/Wire；IndexedDB 保存各账号原始记录。官网使用独立 WebView，主页面桥不注入官网。栏间导航记录访问顺序与各栏滚动位置；0.4 起记录页统一为简洁布局：body 固定 `compact-ui`，不再有「简洁/详细」切换，也不读写 WebView localStorage 键 `starfall-ui-compact`，隐藏仅限次要信息展示（每条五星的时间与本池第几抽、账号更新时间、卡池尾部次要说明、覆盖说明及标题旁 eyebrow/次要文字），统计与原始记录不变；独立的当前垫抽区块已移除，五星间隔条高约 24px；查询页 `startLogin` 固定 `remember=false`、`profile=null`，每次登录都是新的官网会话（原生桥仍接受 remember/profile 参数，仅界面不提供会话保留），手动凭据位于 `query-flip` 登录卡背面，下方 `manual-open` 同一按钮在「手动凭据 / 返回登录方式」间翻转；非活动面禁用交互并隐藏于辅助技术，`manual-help-dialog` 只承载获取教程。返回键先关闭教程再翻回正面，查询失败保留输入和本人确认并停留背面，成功后才清空；B 服授权入口始终显示，`build-info.json` 未启用 `bilibili_enabled` 时按钮 `disabled`、`aria-disabled` 并标注「待接入」，启用后才可点击；页面无查看演示按钮与 TapTap/B 服诊断复制入口，`demo.json` 仅作为合成测试数据，由 MobileUiIntegration 经 `/api/library/import` 注入；记录页账号区优先显示真实昵称（缺资料时为「招募档案」），其下显示游戏 UID 与总抽数/五星/卡池摘要，顶部保留自定义存档别名，不再有 `account-quick-stats`，三类垫抽以 `career-pity` 进度条叠字并入生涯卡（限定/典藏显示 UP/歪 计数与「平均 UP」——该均值仍含歪出的抽数，普通保留「五星平均」），`career-extra` 只另计未分类记录、启程不显示，全部卡池的角色列表由统一的 `characters-toggle` 开关整体显隐；配置 revision 变化触发的后台重算保留当前筛选与浏览位置，不强制跳回顶部。`allowBackup=false`，备份由玩家显式导出。两端的游戏认证和网关直连，配置请求也不携带账号信息。

记录身份由官方验证的 UID/渠道组成，去重根据原始记录 ID/卡池/序号与内容。JSON 保留原始 item_id、pool_id、数量、时间及账号身份；CSV 旧格式无唯一身份时保守处理。配置更新按一份快照重映射原始 ID，保持记录数量/时间；保存的 catalog 指纹变化才触发重算。

## Android 长图保存与分享

共享 `image-export.js` 在内存中生成 PNG，安卓桥提供 `imageAlbum`、`imageStage`、`imageShare` 三个操作。保存到相册与分享独立：前者 Android 10+ 写入本应用贡献的 MediaStore.Images，目录 Pictures/星落抽卡手记，IS_PENDING 完成后发布，失败撤销本次条目；Android 9 按需申请 WRITE_EXTERNAL_STORAGE，仅该系统版本声明权限，写入 Pictures 后触发媒体扫描。JSON/CSV/ZIP 沿用 Downloads/文件选择器。

分享每张 PNG 分别写入内部 cache/shared-images，返回不含账号信息的 UUID 文件 ID。ImageShareProvider 不导出，只允许临时 URI 授权，只读打开精确 UUID PNG，禁止枚举、目录穿越、query/fragment、写入或任意文件路径；存档、配置、登录目录不在提供范围。单图 ACTION_SEND，多图 ACTION_SEND_MULTIPLE，EXTRA_STREAM 和 ClipData 按所选页顺序携带 content URI，仅授予读取权限。

PNG 每张最多 8 MiB，并校验格式、尺寸和像素上限；一次最多 100 张，临时缓存总量最多 128 MiB。启动/准备分享时清理超过 24 小时的文件，不会在分享面板打开后立即删除图片。取消分享不创建相册副本；打开选择器只回报 opened，不声称发送成功。批量相册保存逐张完成，失败停止并报告已保存数量，不回滚此前成功保存的图片。

## 统计

`analysis.py` 与 `mobile-engine.js` 保持普通、限定、典藏、启程和未知分类一致。保底组来自 catalog 的 `pity_group`，跨池继承只在同组连续历史成立；未知不伪造 UP。界面展示卡池 UP 时只引用已验证 catalog 的 `pool_meta.featured_ids`。角色头像由 `mobile-assets.js` 按 item_id 从本地 manifest 映射到 HeadRect，屏幕内方形图取左侧人物区，长图按 `(0,0,140,140)` 源区域裁切，未知 ID 保留首字 fallback，不联网取图。玩家资料由 `GachaClient` 从现有 EnterGameRsp.actor 解析昵称、游戏 UID、等级、faceId 与 actorHead，不新增 RPC；存档主键仍为 channel + openId，旧别名保留。玩家头像按 fashionId→headId→faceId 映射，头像框独立叠加，限时框按响应中的 sysStamp 判断，名次角标只按实际字段显示。查询页 gugu 使用本地 WebP，点击随机播放互动后回 idle，减少动态效果时显示静态图。不显示累计消耗金额。平均抽数=可见分类总抽数/五星数；首个五星与断档为下界；序号 80→1 连续。具体用户边界见使用指南，不将可见历史推断当成官方实时状态。

移动端评价与事件标签由 `mobile-insights.js` 派生：输入为 `mobile-engine.js` 的 report，只统计普通/限定/典藏且 `hard_pity>0` 的卡池，记录按 pool/timestamp/index/record_id 去重；生涯覆盖全部可见历史，近 45 天窗口含端点并剔除未来记录。状态机为 empty（暂无记录）、small（样本较少）、zero（暂未见金）、unknown（星级待确认）、rated（按「抽数÷硬保底」均值分档评级），不足 100 抽或五星少于 3 个时 rated 也标 `lowConfidence` 并在界面加「暂估」。标签只来自有规则的事件：十连 2 金及以上（含未知稀有度时按「十连至少双金」计）、`pulls_since_five` 达到 `hard_pity` 的抽满井、跨池连续 2 次及以上的 UP/歪 连击；不产出无规则依据的标签。`mobile-probability.js` 按本地 BaseWord_cn 静态规则建模：五星基础 2%、UP 占五星 50%、60 抽未出五星后 2%→7% 起每抽 +5%、70 抽硬保底、非 UP 后下一五星必 UP；以状态转移求首次 UP 的 1–140 抽累计分布，`thresholds` 固定为超欧 ≤6、超非 ≥122（约 5% 分位），`eventGrade` 还要求 `interval_exact`、`category` 为限定/典藏、`hard_pity===70` 且 `guaranteed===false` 才判超非。评价阈值与事件分位是两套口径，均只描述可见历史，不代表官方概率或未来预测。

## 公共配置生产与分发

现有更新器定向下载包含五表的 Lua bundle，既有静态 parser 只读取字面量及 T(id) 标记，从不执行 Lua。`gacha_catalog.py` 只输出非个人配置；`BaseGachaBottomPool` 已加入 selector，extractor 自动沿用该白名单。解析后先在 staging 写 `gacha_config`，游戏版本成功发布后验证并镜像到 `data/gacha_config`。失败保留上一次公开版本且不阻断角色更新；同一游戏版本不会自动重跑，可用 CLI 补发。

`current.json` 字段：format=`starfall-gacha-catalog-manifest`、schema_version=1、version、file=`catalog-<version>.json`、sha256 小写64位、size 精确字节数、published_at UTC。版本是规范正整数十进制字符串，最多20位，避免 JavaScript 大整数损失。版本文件最大4MiB，manifest最大16KiB。版本 JSON 先落盘，再原子切换 current；同版本不同字节和降级拒绝。公开 JSON0644、公开目录0755；私有锁/账号/密钥不放宽。

catalog schema_version=1，cards 为正 ID → `[名称,星级]`，pools 为正 ID → 名称；两表均1..20000项，ID≤2^53−1，名称1..120字符且不含 ISO 控制字符，星级1..5或null。selectors 为1..8个唯一正 ID。pool_meta 精确覆盖 pools，category 为 normal/limited/collection/beginner/unknown，pity_group 为1..80位 `[A-Za-z0-9:_-]`；featured_ids/names等长、≤128、ID唯一且必须引用对应同名五星；hard_pity 必须存在，为null或1..1000。

类型0常驻、类型1启程；group1+bottom_type201限定，group2+bottom_type301典藏，其余未知。UP只保留 main_card_ids 中五星；硬保底取 bottom_pool 中type1的bottom_num。内置基线108卡片/89卡池来自版本134359843258232660，非个人数据。

客户端只接受完整 HTTPS manifest URL，禁止认证信息、query/fragment、编码/重复斜线/dot段路径及跳转，使用系统证书验证。先限长读取manifest，再限长下载同目录版本文件，验证hash/size/schema/版本与引用。正常成功最多6小时检查一次，失败退避。校验成功才原子提交本地指针，损坏回退内置或上次已验证缓存；从未预设 test 源。缓存尚无长期 blob 容量回收策略，部署 release 保留策略不负责客户端缓存。

`xl_gacha_web/scripts/update_catalog.py` 仅用于开发者重建内置基线，不是运行时远程发布器；公共发布用 server 的模块 CLI。静态公网只开放两个严格 JSON 路径规则，不开放控制 API/整个 data。Windows 与 Android 二进制更新分别依赖完整ZIP迁移和同签名覆盖安装，公共配置不下载代码。
