# 抽卡手记构建、测试与升级

[用户指南](gacha-user-guide.md) · [架构与配置协议](gacha-architecture.md) · [HTTPS 运维](../xl_updata_server/docs/gacha-config-hosting.md)

两个客户端相邻存放：Android 构建复用 `xl_gacha_web/static` 和 `data`，不能只取 Android 目录。源码只含合成测试、虚构 demo 与非个人游戏配置。使用各项目/仓库已有 Python 环境，临时构建输出放 Git 外；不提交运行数据、浏览器 profile、签名密钥和产物。

## Windows

运行需要 Python 3.11+ 标准库与 Tk；发行 ZIP 自带运行时。自动官网登录需要本机 Chromium 系浏览器。打包另需 Windows x64、Node.js 与 PyInstaller 6.x。进入 `xl_gacha_web`，激活项目环境：

```powershell
python -B -m unittest discover -s tests -v
node --test tests/image-export.test.cjs
python -B scripts/build_portable.py --output E:/builds/starfall-windows
```

`scripts/build_windows.ps1` 可创建项目 `.venv` 并安装构建依赖；包管理器网络遵守本机代理策略。脚本要求 Node 验证通过才打包。资源使用明确清单，`data` 只包括图标、catalog、内置正式更新源；不会递归打包个人 data 文件。

根目录 Actions → **Build Windows portable app** → Run workflow，选择已审查分支，下载 `StarfallGacha-Windows-x64`。根 `.github/workflows/gacha-windows-portable.yml` 是实际入口；子目录 workflow 不会生效。手动流程仅产生构建 artifact，不自动发布 Release。主 `XL CI` 覆盖 Python/JS 测试与共享契约。

## Android

准备 Python 3.11+（项目环境安装 Pillow）、Node、JDK17+、SDK API35/build-tools35。支持 sdkmanager 标准 `platforms/android-35`、`build-tools/35.0.0`，也兼容旧离线 `platform/android-35`、`build-tools/android-15`。工具按 Windows/Unix 选择文件名；构建不会下载 SDK。

```sh
cd xl_gacha_android
node --test tests/engine.test.cjs
python -B -m unittest discover -s tests -p 'test_*.py' -v
python -B scripts/test_native.py --sdk "$ANDROID_HOME" --jdk "$JAVA_HOME" --output /outside/repo/native-tests
```

原生脚本编译所有实际 Java 源码与 CatalogUpdatesTest，执行纯 JVM WireTest，并输出 DEX 测试 JAR。`android.jar` 中 `org.json` 是编译 stub，不能据此声称运行验证；使用已启动的 API35 模拟器测试真实实现：

原生编译保留 Java8 兼容目标，仅用 `-Xlint:-options` 过滤新版 JDK 的过时目标选项警告；Android API 弃用说明和其他编译诊断仍可见。客户端/配置生产器与部署控制器的 CI lint 固定 Ruff0.17.1、显式 `--target-version py311`，本地按相同版本/参数复现。

```sh
adb shell mkdir -p /data/local/tmp/xl-catalog-test
adb push /outside/repo/native-tests/catalog-tests.jar /data/local/tmp/xl-catalog-test/catalog-tests.jar
adb push ../xl_gacha_web/tests/fixtures/catalog-contract.json /data/local/tmp/xl-catalog-test/contract.json
adb shell 'CLASSPATH=/data/local/tmp/xl-catalog-test/catalog-tests.jar app_process /system/bin cn.starfall.gacha.local.CatalogUpdatesTest /data/local/tmp/xl-catalog-test/state /data/local/tmp/xl-catalog-test/contract.json'
adb shell 'CLASSPATH=/data/local/tmp/xl-catalog-test/catalog-tests.jar app_process /system/bin cn.starfall.gacha.local.LoginLinksTest'
adb shell 'CLASSPATH=/data/local/tmp/xl-catalog-test/catalog-tests.jar app_process /system/bin cn.starfall.gacha.local.LoginReturnTest'
```

该测试不安装 APK、不读取已安装应用账号数据，不做真实登录。命令退出码必须为零。`XL CI` 使用同样原生编译、WireTest、JS 与 Android emulator 检查；首次本机开发可只编译，但须明确尚未运行 Android JSON。

QQ 分享窗口回归另外使用隔离包 `cn.starfall.gacha.returnqa`。下面构建脚本仅将初始官网导航替换为 `about:blank`，不改变产品的回调和会话处理；随后用虚构 `qaCode`/`qaState` 检查弹窗绑定、超时/换会话/重放拒绝，以及本人确认后恢复原窗口。测试不连接真实授权端点，也不读正式应用数据。首次安装前确认设备没有同包名的其他 QA 存档，测试结束移除临时包；不要卸载正式版。

```sh
python -B scripts/build_login_return_test.py --sdk "$ANDROID_HOME" --jdk "$JAVA_HOME" --output /outside/repo/login-return
adb install /outside/repo/login-return/login-return-qa.apk
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.LoginReturnIntegration
adb shell am instrument -w -e separateTask false cn.starfall.gacha.returnqa/cn.starfall.gacha.local.LoginReturnIntegration
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.ImageExportIntegration
adb uninstall cn.starfall.gacha.returnqa
```

两次输出都必须包含 `PASS 12 isolated Android` 和 `INSTRUMENTATION_CODE: -1`；仅 adb 退出码为零不代表断言通过。同任务、独立任务分别运行，CI 同样检查输出。该测试 APK 使用 Git 外临时开发签名，不能当成正式升级包。

相册/分享检查另需包含 `PASS 20 image album/share checks` 和 `INSTRUMENTATION_CODE: -1`，覆盖真实 MediaStore 落盘/字节、发布目录、原文件导出的 Downloads 路径和字节、临时 provider 读取、单图/多图意图、只读授权、非法 URI 拒绝与过期清理，测试完成前删除自己贡献的相册图片和测试文档。QA provider authority 使用独立包名，避免与正式版冲突。`--assets /outside/repo/release/assets` 可为隔离测试包加入刚构建的页面资源并启用 QA WebView 调试，供合成多页图片界面验证；正式包不启用调试。

Android 15 模拟器可验证系统分享预览，不替代接收端真机测试。2026-10-23 用户确认 0.3.4 相册保存和分享在真机均正常；功能提交 a66209d7 的 CI 检查通过。接收应用/机型没有逐项枚举，Android 9 保存相册的权限分支需要另行验证。

发布构建需要原签名密钥：

```sh
# 在受保护环境中设置 STARFALL_KEY_PASS，不能写进 Git 或命令历史。
python -B build.py --sdk "$ANDROID_HOME" --jdk "$JAVA_HOME" --output /outside/repo/release --keystore /private/starfall-original.p12 --key-alias starfall
```

仅本地开发可显式传 `--development-key`，生成 Git 外的开发密钥；`--debug` 只为 QA 开启 WebView 调试。开发密钥与原签名不同，其 APK 不可冒充正式升级包。现有 applicationId `cn.starfall.gacha.local` 保持不变；versionCode 必须随正式二进制发布单调增加。签名前用 `apksigner verify --print-certs` 核对旧/新证书 SHA256；原密钥/密码应有离线备份，丢失后不能覆盖升级旧应用，只能导出记录后迁移。密钥永不放在源码、APK 或 CI artifact。

## 服务端与发布门禁

在 `xl_updata_server` 项目环境运行 `python -m pytest -q`；Linux 必须包含 public JSON 的 0644/私有 umask 回归。配置生产器 CLI 和静态服务说明见 HTTPS 文档。客户端默认正式来源为 `https://8.134.186.234/gacha-config/current.json`；测试来源需明确设置 test 地址。所有共享负例在 `xl_gacha_web/tests/fixtures/catalog-contract.json`，由 Python 客户端、producer、Android 实际 JSON 共同执行。

仓库按 debug → test → main 晋级；CI 通过不等于已部署。服务端稀疏发布、存储保留、回滚与锁见 [xl_deploy 运维](../xl_deploy/README.md)。不要为了引导配置而运行完整游戏 CDN 抓取；先验证独立配置快照再由运营者明确种入 test。Android 第二个真实账号查询已由用户确认；2026-10-23 用户另行确认 QQ 授权后的浏览器分享回工具流程真机可用。模拟器合成测试与用户实际授权验收应分别记录，不泛化为所有机型均已兼容。
