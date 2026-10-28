# 每期排行榜历史档案（第一期）

本功能是人工授权的命令行只读采集器。不会默认启动后台任务，不借用网页或安卓抽卡工具其他用户的凭据，不向QQ群发送消息，也不自动部署生产。

## 支持范围

| mode | 玩法 | 来源与完整性 |
|---|---|---|
| guild_war | 公会战 / 强敌来袭 | 2301元信息，2307/10033排名；正式请求没有分页字段，只能保存服务端可见片段 |
| illusion | 幻象禁地 | 2001元信息，2002排名，索引从1起；rankCount语义须以真实响应为准 |
| nightmare | 噩梦投影 | 3001元信息，3004排名，startIndex从0起；totalRank是服务端报告显示范围，不保证所有参与者 |

静态字段依据用户提供的游戏版本134359843258232660中Proto.lua与各玩法客户端代码。它证明协议布局，不证明当前服务器活动开放或允许完整遍历。噩梦排行不提供guildUid，不能按同名公会强行关联。公会memberList单独归档，范围未知时不称全服个人榜。

## 离线检查与操作

在xl_gacha_web目录使用Python 3.11+，仅需标准库：

```text
python -B leaderboard_cli.py dry-run
python -B leaderboard_cli.py list-seasons --database <本机私有目录>/leaderboards.sqlite3
```

联网命令必须显式指定数据库、scope和模式。scope是运维确认的区服/认证上下文标签；不同服务器或账号授权上下文不能复用同一个含糊的cn标签。首次研究使用独立测试上下文标签，尚未验证区服语义时应在交接记录中注明。

```text
python -B leaderboard_cli.py metadata --database <本机私有目录>/leaderboards.sqlite3 --scope <明确的上下文标签>
python -B leaderboard_cli.py smoke --database <本机私有目录>/leaderboards.sqlite3 --scope <明确的上下文标签>
python -B leaderboard_cli.py collect --database <本机私有目录>/leaderboards.sqlite3 --scope <明确的上下文标签> --modes illusion nightmare
python -B leaderboard_cli.py export --database <本机私有目录>/leaderboards.sqlite3 --snapshot-id <ID> --output <新文件>.json
```

终端交互隐藏输入JWT/现有支持的登录JSON；管道时只读取stdin一行。排行榜 CLI 额外接受已授权 test 续签材料使用的星落 SDK 应用2070035；原网页抽卡仍只接受2070021/2070023，不接受任意应用。不要把凭据放命令行、Git、日志或导出文件。收集器不会替你做短信验证或凭据续签；使用明确授权且有效的账号。进入网关不同于只做登录预检，可能与同账号其他游戏会话冲突，不应重复并发登录。

metadata仅查三类元信息，活动开放时也不读取排行，只记录观察。smoke查三类元信息和第一页，公会两个接口各一次对照；单连接，业务RPC最多7次、间隔至少2秒、整轮预算60秒。未开放时优先保留元信息，不反复请求排行榜；认证拒绝、限流、协议异常即停止，不自动重试。

collect默认三类合计最多20个榜单页、200条记录、64次业务RPC和180秒，复查也计预算。CLI不能将上限放宽；先根据实测再修改设计。时间预算贯穿连接与RPC；底层阻塞网络仍受单次socket/HTTP超时，不能把它当作操作系统级硬中断。

## 状态与历史口径

- 活动未开放、明确休赛、有效空榜、网络/认证失败独立记录，不用空榜覆盖旧赛季。
- 幻象isStart是本人是否正在挑战，不是活动开放开关；phase数字含义不能套用其他玩法。
- season_id来自服务端，不由本地日期生成；没有有效赛季身份只保留观察/错误，不写假赛季0。
- COMPLETE_REPORTED_RANGE仅表示覆盖了服务端报告范围；SERVER_VISIBLE_SLICE表示可见片段；PARTIAL/INCONSISTENT明确标记中断与不一致，不补造名次。
- 结算证据与覆盖度分离。初期没有自动SETTLED_VERIFIED；结算前最近一次观测最多称LAST_OBSERVED，不能充当官方最终榜。
- 每次采集独立快照，按scope、玩法、赛季、实体种类保存。昵称变化不改变UID/UIN身份，导出ID为字符串以保留64位精度。
- 数据库独立于抽卡history.sqlite3；重复run ID只能幂等重试相同内容，不能覆盖历史。

## 验收边界

离线模拟通过与实际服务器响应分别记录。真实烟测结果会单独保存摘要，未开放玩法不得标为已验证分页/全量/结算。Web/QQ查询不属于当前交付范围；长期定时、赛季封包与真实线上验收分开记录，不能用本地模拟结果代替。

不在生产小主机上跑无限爬取；本机数据库和导出含玩家名、UID、公会名等游戏公开榜单信息，分享前按实际使用范围审阅。只有经过用户确认的赛季封包流程才可清理对应运行期数据，其他数据库和旧快照不自动删除。

## 服务器长期赛季归档（第二期）

`leaderboard_seasons.py` 提供独立于第一期 schema1 的 `seasonal_leaderboards.sqlite3`。每次运行保留完整可见成员清单、全部排行与阵容字段、分页证据和去重原始业务PB；PARTIAL不会用于判定离开。玩家、公会和公会作战记录均有首次/最后可见观测区间，掉出或被顶出只结束当前出现区间，不删除历史。`battleUid`按赛季累积，不受单次约99条限制。

`leaderboard_season_package.py` 按模式、赛季生成tar.gz包。包内含自包含SQLite、manifest、SHA-256和去重原始PB；封包期间锁定该赛季写入，重开执行实际查询验证后才允许清理对应运行期数据。封包或验证失败不得清理。全局索引仅指向验证通过的赛季包。

服务器jobs通过`gacha_core.leaderboard_records`补充幻象2003阵容和公会2306索引；原始响应通过会话观察器采集，不包含认证帧。幻象按50人一批，公会按5个配置关卡一批，原始块逐条流式封包。phase→stage映射来自随仓库的已核验资源，未知phase跳过索引而不猜ID。

触发标签为`ROUTINE`、`PRE_END_300S/120S/60S/30S/15S`、`POST_CLOSE_1H`和`POST_CLOSE_UNAVAILABLE`；标签不替代coverage或settle_evidence，结束后单次补采不自动等于官方冻结榜。
