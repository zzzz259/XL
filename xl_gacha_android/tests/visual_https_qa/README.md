# Android HTTPS 素材更新回归

准备阶段使用 Python 3.12、Node 22、Pillow、OpenSSL、JDK 17、Android API 35 和 build-tools 35.0.0。以下命令从 `xl_gacha_android` 执行。

```bash
# 在 emulator-runner 步骤之前执行。每轮使用新的工作目录。
python -B tests/visual_https_qa/run.py prepare --work "$RUNNER_TEMP/visual-https-qa"

# 在现有 API 35 emulator-runner 的 script 中执行。只有一台在线 AVD 时可省略 --serial。
python -B tests/visual_https_qa/run.py exercise --work "$RUNNER_TEMP/visual-https-qa"
```

`prepare` 履历固定为 0.4.1 的 `78e384745f6204925f8978957091c229739a2a8b`、0.4.2 的 `4a61982c3ac428ffe6eda399deb8ddb84a76f510` 与当前 0.4.3 checkout。用浅 fetch 补齐缺失提交，再按构建所需路径提取源码。三版 QA APK 使用同一开发签名和隔离包名 `cn.starfall.gacha.visualhttpsqa`，包内默认源为空。

准备阶段创建两天有效的 QA 私有 CA 和 `localhost` TLS 证书。修改仅位于临时构建副本，在 QA APK 的 manifest 添加按域名的信任配置，仅允许 `localhost` 使用该 CA，并保持 cleartext 关闭。测试通过 ADB reverse 将设备回环 HTTPS 连接到 runner 的回环服务。断线阶段移除这条转发，再重启应用查看缓存。所有下载由真实 APK 的 Android HTTPS 下载器执行。

| 验收链 | 预期图片 GET |
| --- | --- |
| 0.4.1、0.4.2 分别从 V1 到 V2 | A、B、C |
| 0.4.1、0.4.2 各覆盖到 0.4.3，再从 V1 到 V2 | 覆盖后从原缓存读出 A、B，更新 V2 时仅 GET C，并各自保留 49 条合成抽卡记录、464 抽 |
| 0.4.3 同版 C 损坏修复 | C |
| 0.4.3 断线重启 | 0，V2 PNG 仍可解码 |
| 0.4.3 直接从 V1 更新到 V3 | C、D |
| 0.4.3 冷装首次检查 V3 | A、B、C、D |

A、B、C、D 分别是角色头像、时装横图、类型 108 的道具头像和类型 109 的头像框。每阶段验证原生缓存的视觉映射、WebView 实际读取的图片 SHA-256、尺寸和裁切字段，以及 HTTPS GET 次数、状态码和响应字节数。覆盖验收从应用界面导入同一合成报告，并截取实际生成的 JSON 导出。长图证据为应用生成的 1080px PNG。

机读证据位于 `$RUNNER_TEMP/visual-https-qa/evidence/`，包含 `results.json`、`https-requests.jsonl`、`fixture-index.json` 及覆盖验收的前后 JSON 和长图。只上传该目录即可。使用已准备的 APK 重跑时，可传 `--evidence` 指定一个新目录。独立场景会重置隔离 QA 包，适用于一次性 AVD。

预计准备 2–5 分钟，AVD 已启动后的验收 3–7 分钟。耗时需以 GitHub hosted runner 的实际记录为准。这组回归使用合成 HTTPS 测试服务。线上 test 证书、部署配置和真实 AB 提取由对应的线上联调验证。
