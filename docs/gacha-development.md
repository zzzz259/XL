# 抽卡手记构建、测试与升级

[用户指南](gacha-user-guide.md) · [架构与配置协议](gacha-architecture.md) · [HTTPS 运维](../xl_updata_server/docs/gacha-config-hosting.md)

Android 构建复用 `xl_gacha_web/static` 和 `data`，不能只取 Android 目录。电脑端在 0.3.4 后正式停止维护，保留的共享页面、配置和统计参考继续供 Android 使用。源码只含合成测试、虚构 demo 与非个人游戏配置。使用各项目/仓库已有 Python 环境，临时构建输出放 Git 外；不提交运行数据、浏览器 profile、签名密钥和产物。

## 电脑端停止维护与共享代码

Windows 抽卡手记最后版本为 0.3.4，不再提供构建、发布、登录适配或问题修复。Windows 专属打包 workflow 已移除，旧源码与打包脚本仅作历史资料。`xl_updata_tool` 是另一子项目，不受此决定影响。

```powershell
python -B -m unittest discover -s tests -p test_analysis.py -v
python -B -m unittest discover -s tests -p test_archive.py -v
python -B -m unittest discover -s tests -p test_catalog_contract.py -v
python -B -m unittest discover -s tests -p test_catalog_updates.py -v
python -B -m unittest discover -s tests -p test_core.py -v
node --test tests/image-export.test.cjs
```

在 `xl_gacha_web` 下运行上述共享参考检查。根 `XL CI` 的 `gacha-shared` 作业仅验证 Android 依赖的统计、存档、配置契约和页面脚本，不再构建 Windows artifact。

## Android

准备 Python 3.11+（项目环境安装 Pillow）、Node、JDK17+、SDK API35/build-tools35。支持 sdkmanager 标准 `platforms/android-35`、`build-tools/35.0.0`，也兼容旧离线 `platform/android-35`、`build-tools/android-15`。工具按 Windows/Unix 选择文件名；构建不会下载 SDK。

```sh
cd xl_gacha_android
node --test tests/mobile-navigation.test.cjs tests/mobile-view-model.test.cjs tests/mobile-export.test.cjs tests/engine.test.cjs tests/tap-app-login.test.cjs
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

QQ 分享窗口回归另外使用隔离包 `cn.starfall.gacha.returnqa`。构建脚本默认打包 0.4 移动端页面资源，并将 `update-source.json` 置空、`build-info.json` 标记 `bilibili_enabled: false`，避免 QA 连接真实配置源或显示 B 服入口（B 服实现仍在，仅构建传入 Git 外 `--bilibili-config` 才写入并显示入口；普通 QA 未提供该私有配置故隐藏）；只有显式传 `--assets` 才改用给定资源。下面构建脚本仅将初始官网导航替换为 `about:blank`，不改变产品的回调和会话处理；随后用虚构 `qaCode`/`qaState` 检查弹窗绑定、超时/换会话/重放拒绝，以及本人确认后恢复原窗口。测试不连接真实授权端点，也不读正式应用数据。首次安装前确认设备没有同包名的其他 QA 存档，测试结束移除临时包；不要卸载正式版。

```sh
python -B scripts/build_login_return_test.py --sdk "$ANDROID_HOME" --jdk "$JAVA_HOME" --output /outside/repo/login-return
adb install /outside/repo/login-return/login-return-qa.apk
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.LoginReturnIntegration
adb shell am instrument -w -e separateTask false cn.starfall.gacha.returnqa/cn.starfall.gacha.local.LoginReturnIntegration
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.ImageExportIntegration
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.LoginFreshIntegration
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.TapAppIntegration
adb shell am instrument -w -e mobile true cn.starfall.gacha.returnqa/cn.starfall.gacha.local.TapAppIntegration
adb shell am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.MobileUiIntegration
adb uninstall cn.starfall.gacha.returnqa
```

两次输出都必须包含 `PASS 12 isolated Android` 和 `INSTRUMENTATION_CODE: -1`；仅 adb 退出码为零不代表断言通过。同任务、独立任务分别运行，CI 同样检查输出。该测试 APK 使用 Git 外临时开发签名，不能当成正式升级包。

相册/分享检查另需包含 `PASS 20 image album/share checks` 和 `INSTRUMENTATION_CODE: -1`，覆盖真实 MediaStore 落盘/字节、发布目录、原文件导出的 Downloads 路径和字节、临时 provider 读取、单图/多图意图、只读授权、非法 URI 拒绝与过期清理，测试完成前删除自己贡献的相册图片和测试文档。QA provider authority 使用独立包名，避免与正式版冲突。`--assets /outside/repo/release/assets` 可为隔离测试包加入刚构建的页面资源并启用 QA WebView 调试，供合成多页图片界面验证；正式包不启用调试。

新登录清理检查需包含 `PASS 8 fresh-login WebView checks` 和 `INSTRUMENTATION_CODE: -1`。测试向隔离 WebView 的官网来源写入合成旧 JWT/HP/provider/sessionStorage，调用原生产代码的页面完成回调，确认轮询在清理前关闭、实际存储被清空、重新加载后才开放轮询。另以真实官网新登录入口检查等待授权且无旧身份自动查询；不要读取或输出真实凭据。原生测试 JAR 会先进行四字节对齐，使 MuMu 可以直接映射 dex，不依赖 shell 创建提取命名空间。

TapTap App 衔接测试需包含 `PASS 16 TapTap App WebView/handoff checks`、`tap_policy_checks=21` 与 `INSTRUMENTATION_CODE: -1`。它拦截合成官网响应及 App 启动，测试真实 WebView 监听、包名、一次性交接、未安装 App 的反馈、会话切换、当前窗口恢复、超时与诊断不含挑战。21 项链接策略检查在同一个正常 Android 应用进程执行；MuMu 的 shell `app_process` 可能在执行完策略后以 139 退出，不能据打印通过就声称命令成功。JS 监听另用 `node --test tests/tap-app-login.test.cjs` 覆盖官方来源、fetch/XHR、重复注入及过期。

手机布局分支另外需包含 `layout=phone`、`PASS 20 TapTap App WebView/handoff checks` 和成功结束码。该夹具使用含 Mobile 标识的手机 UA 与官网相同的扫码切换可见性条件，并通过 history.replaceState 模拟 `/authorize` 到 `/login` 的 SPA 跳转；同时验证原 state/PKCE 保留、网页登录回退恢复原 UA、再次 App 授权可用。诊断只记录页面路径和布局准备状态，完整 URL 与挑战仅在原生内存中绑定本次窗口，不进入诊断。

移动端界面集成测试需包含 `PASS ` 与 `mobile UI checks` 和 `INSTRUMENTATION_CODE: -1`；仅 adb 退出码为零不代表断言通过。MobileUiIntegration 加载真实主页面：先清空隔离存档，检查空库打开查询页、登录为页内流程而非对话框、已移除控件（remember-login、demo-button、compact-toggle、tap-diagnostics 等）缺席且查询页无宣传文案、手动凭据入口存在；随后 fetch 内置 `demo.json` 并附上合成账号身份，经 `/api/library/import` 注入（不依赖任何界面演示入口），重载后检查合成数据汇总数值（总抽数 464、五星 9）、当前账号与抽卡生涯汇总、三类垫抽以 `career-pity` 简行并入生涯卡、统一简洁模式（body 固定 `compact-ui`、次要信息隐藏）、卡池默认展开五星及间隔条（高约 24px）、卡池分类筛选、原始招募明细折叠内搜索/卡池/星级/仅五星筛选与空结果、长图弹窗实际生成一张 1080px PNG、返回键先走页面 `window.mobileBack`（关闭弹层后回到上一栏并恢复其滚动位置）、账号切换与重载后恢复上次账号，以及 140% 大字体下页面与汇总指标均无横向溢出。QA 包配置源为空，测试不连真实配置网络、不做真实登录。

对应 Node 单测 `tests/mobile-navigation.test.cjs` 校验三栏初始目的地（有选中报告或本机存档进「记录」、否则进「查询」）、返回上一页并恢复其滚动位置、replace 去重与非法页面拒绝；`tests/mobile-view-model.test.cjs` 校验汇总视图模型（累计与分类抽数、可见垫抽、最近五星倒序与上下文、条数上限）、卡池分类视图与记录卡片（最新在前、筛选后分页、仅五星/星级/卡池/搜索与空态、UP ID 取自内置 catalog，且不改动原始分析数据）；`tests/mobile-export.test.cjs` 校验 JSON/CSV 导出保真与安全文件名、CSV 公式中立化、不支持的格式与空记录报错，并确认共享长图引擎 `image-export.js` 脱离旧版桌面弹窗仍可加载；构建脚本与 `XL CI` 在打包前运行这组测试。

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
