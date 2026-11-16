# 抽卡手记 Android 云端构建与 Release

对应工作流：`.github/workflows/gacha-release.yml`，Actions 名称 **Gacha Android Release**。复用 `xl_gacha_android/build.py`，构建正式包名 `cn.starfall.gacha.local`，沿用原正式签名和 B服配置。

## 环境

GitHub 托管的 Ubuntu runner 自动准备 Python 3.12、独立 venv、Pillow 12.2.0、Node 22、Temurin JDK 17、Android API 35 和 Build Tools 35.0.0。无需在个人电脑安装这些环境，也不依赖本地 E 盘 SDK。云端使用 runner 自身网络；本机通过 GitHub CLI 操作时按工作区约定使用 127.0.0.1:7897 系统代理。

## 一次性配置

仓库 Actions Secrets：

| Secret | 内容 |
|---|---|
| `GACHA_ANDROID_KEYSTORE_BASE64` | 原正式 PKCS12 签名文件的完整 Base64 |
| `GACHA_ANDROID_KEYSTORE_PASSWORD` | 原签名文件密码 |
| `GACHA_BILIBILI_CLIENT_JSON` | `merchant_id`、`app_id`、`server_id`、`client_key` 四项 B服客户端 SDK 接入配置 |

不配置个人访问令牌。发布步骤使用工作流自动提供的 `GITHUB_TOKEN`，仅发布 job 授予 `contents: write`；构建 job 只读仓库。流程不在 PR 或 fork 事件中加载签名材料。

公开证书 SHA256 与别名存放在 `xl_gacha_android/release-config.json`。更换为另一份证书会使构建失败，防止生成无法覆盖安装的包。证书指纹可公开，密钥和密码不得提交。

本机长期备份位于项目 Git 忽略目录 `local-research/private/gacha-release/`，目录限制为当前 Windows 用户与 SYSTEM 访问：

```text
release.p12                  原正式签名密钥
keystore-password.txt        密码，保留原值
bilibili-client.json         B服客户端配置
signing-metadata.json        公开证书指纹和别名
reference-v0.4.2.apk          用于签名对照的旧正式包
```

这里是本机私密备份，不随 Git 推送。GitHub Secrets 不能回读原值；需要另外保管此备份。不要将它移到会定期清理的 Codex 中间文件目录。

以后迁移仓库或重新配置 Secrets，先用已登录、具有仓库写权限的 GitHub CLI 执行校验，再加 `--apply` 上传；参数中不携带密码：

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'
$env:HTTP_PROXY=$env:HTTPS_PROXY
& .\.venv\Scripts\python.exe xl_gacha_android/scripts/configure_release.py `
  --private-dir E:/All-Projects/XL/local-research/private/gacha-release `
  --jdk 'C:/Program Files/Microsoft/jdk-25.0.3.9-hotspot'

# 校验通过后实际写入 Secrets
& .\.venv\Scripts\python.exe xl_gacha_android/scripts/configure_release.py `
  --private-dir E:/All-Projects/XL/local-research/private/gacha-release `
  --jdk 'C:/Program Files/Microsoft/jdk-25.0.3.9-hotspot' --apply
```

脚本先验证证书，再通过 stdin 调用 `gh secret set`。GitHub CLI 在本机加密后上传；日志只输出名称和结果。[GitHub CLI 说明](https://cli.github.com/manual/gh_secret_set)

## 只编译

合入 `debug` 的 Android、共享页面/配置或发布脚本变更，自动触发签名构建并上传 Artifact，不创建正式 Release。成功记录下可下载 `gacha-android-<版本号>`，包含 APK、`SHA256SUMS.txt`、`build-metadata.json` 和 `RELEASE_NOTES.md`；Artifact 保留 30 天。

流程晋级至 `main` 后，也可在 Actions → **Gacha Android Release** → **Run workflow** 手动执行：选择 `debug`、`test` 或 `main`，版本号留空，不勾选 `publish`。GitHub 的手动入口要求 workflow 文件已存在于默认分支，只有 debug 中的文件尚不满足此条件。[GitHub 手动触发说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

版本号、versionCode 从 `AndroidManifest.xml` 读取；`baseline.json` 的 integration_version 和 `docs/releases/gacha-v<版本>.md` 必须匹配。手填版本仅作一致性检查，不能覆盖源码版本。

## 正式发布

按 [贡献指南](../CONTRIBUTING.md) 先经任务 PR → debug → test → main。main 晋级需要用户明确授权，尤其是 debug/test 同时包含 Bot 或部署改动时，不以配置 APK 工作流为理由自动发布其他项目。

从已验收 main 发布有两种方式：

1. 在 Actions 手动运行，选择 **main** 并勾选 `publish`。流程从源码读取版本、编译签名和审计，成功后创建 `gacha-v<版本>` 标签与 GitHub Release。
2. 为 main 上的提交推送对应标签，例如 `gacha-v0.4.2`，自动编译并发布。标签版本与源码必须一致。

标签必须指向可从 `origin/main` 到达的提交；未晋级的 debug/test 发布请求会在读取 Secrets 前被拒绝。已存在的 Release 不覆盖，已有标签指向不同提交时失败，不移动标签或替换附件。APK 新版本需提高 versionName 和 versionCode，并加入对应公告。

Release 附件为 APK、`SHA256SUMS.txt`、`build-metadata.json`，正文来自已提交的更新公告。`gacha-v*` 与 Windows 更新工具的 `v*` 标签隔离；Android Release 不改动仓库全局 Latest 标记。

## 核验

构建会运行既有 Node 与 Wire 协议测试，随后验证正式包名、版本、非调试构建、原签名证书、B服启用、内置配置与源码指纹；测试类和签名文件不得进入 APK。签名材料仅暂存在 runner 私密目录，结束时清理；只上传审计后的 dist 目录。

下载后按 `SHA256SUMS.txt` 校验 APK。`build-metadata.json` 记录证书 SHA256、源提交与工作流运行编号。APK 可同签名覆盖安装；GitHub Actions 的构建成功不替代版本发布前的功能验收。
