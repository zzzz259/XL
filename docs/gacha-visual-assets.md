# Android 视觉素材增量更新接口（v1）

Android 客户端已支持在不重新安装 APK 的情况下，更新角色立绘头像、时装对应的玩家头像、账号头像和头像框映射。客户端继续使用现有目录更新源：`https://<host>/<directory>/current.json`。服务端发布工作另行实施；当前没有新增默认更新地址，也不会请求推测出来的生产接口。

APK 内置的 `assets/manifest.json` 和图片始终作为离线底稿。现有目录没有 `visual_assets` 字段时，不请求视觉素材。目录和图片分别提交：目录更新成功、图片下载失败时，抽卡数据仍可使用，视觉素材继续保留上一次完整版本。

## 发布契约

在已被目录清单校验过 SHA-256 的 `catalog-<version>.json` **内容**中增加可选描述符。不要将其仅放在 `current.json`：现有目录下载器只保留该清单的既有字段。

```json
{
  "visual_assets": {
    "schema_version": 1,
    "version": "2026110201",
    "file": "visual-assets-2026110201.json",
    "sha256": "<映射文件原始 UTF-8 字节的 64 位小写 SHA-256>",
    "size": 1234
  }
}
```

`version` 必须为 1–20 位、无前导零的正十进制**字符串**。客户端按十进制精确比较，不转换成 JavaScript 数字。同一版本只能对应同一个映射 SHA-256；需要修改内容时必须增加版本。目录内容发生变化也需要按既有目录契约增加目录版本。

映射文件与 `current.json` 同目录。下面是结构示例，校验和及大小需要以实际文件字节计算：

```json
{
  "format": "starfall-gacha-visual-assets",
  "schema_version": 1,
  "version": "2026110201",
  "characters": {
    "10000201": {
      "path": "assets/portraits/10000201.webp",
      "name": "新角色",
      "rarity": 5
    }
  },
  "fashions": {
    "10201": {
      "path": "assets/portraits/10000201.webp",
      "rect_path": "assets/account-rect/HeadRect_10201_1.png"
    }
  },
  "account_items": {
    "5001": {
      "path": "assets/account/Frame_5001.png",
      "type": 109,
      "effect_value": null
    }
  },
  "files": {
    "assets/portraits/10000201.webp": {"sha256": "<64 位小写 SHA-256>", "size": 10000},
    "assets/account-rect/HeadRect_10201_1.png": {"sha256": "<64 位小写 SHA-256>", "size": 10000},
    "assets/account/Frame_5001.png": {"sha256": "<64 位小写 SHA-256>", "size": 10000}
  }
}
```

三个映射分组均可省略，按 ID 覆盖内置映射；缺失的内置 ID 继续保留。需要撤回远端覆盖时，发布更高版本并省略该条映射，即恢复内置条目。远端新增但内置不存在的 ID 在省略后不再显示图片。`characters` 必须有 `path`，`fashions` 必须有 `rect_path`，`account_items` 必须有 `path` 和 `type`。账号头像类型为 `108`，头像框为 `109`；限时框的 `effect_value` 为字符串 `"1"`，永久框可以省略或为 `null`。到期时间仍取玩家自己的账号数据。

角色、时装和类型 `108` 的账号头像可附带 `source_size: [width, height]` 和 `crop_rect: [x, y, width, height]`。坐标均为整数，裁切区域必须完整位于源图画布内，裁切必须同时声明源图尺寸；宽高为正数且各不超过 4096。例如 320×140 时装头像的游戏方形裁切为 `source_size: [320, 140]`、`crop_rect: [91, 23, 88, 88]`。客户端保留并使用这组元数据，无需服务端重新切出单独图片。

图片请求地址以更新源所在目录为基准，例如 `https://<host>/<directory>/assets/portraits/10000201.webp`。每个新路径必须出现在 `files` 中；也可引用 APK 内置文件表已有的路径而不重复下载。`files` 中每个文件必须被映射引用。每个映射发布应包含需要保留的全部远端覆盖，不会将多个远端版本逐层累积。

只接受以下扁平路径，文件名只能包含英文字母、数字、下划线及连字符：

- `assets/portraits/<name>.webp|png|jpg|jpeg`
- `assets/account/<name>.webp|png|jpg|jpeg`
- `assets/account-rect/<name>.webp|png|jpg|jpeg`

不允许 URL、查询参数、百分号编码、目录穿越、SVG、脚本、字体、HTML、应用 UI、品牌或吉祥物覆盖。映射上层未知字段直接拒绝。图片验证扩展名对应的 PNG/WebP/JPEG 文件头和画布边界，不运行下载内容。

约束：映射最多 1 MiB、2048 个下载文件；单图最多 4 MiB；下载文件声明总量最多 32 MiB；画布宽高各最多 4096。角色、时装、账号物品每组最多 20000 条；ID 为不超过 JavaScript 安全整数上限的正整数字符串。下载只接受同源 HTTPS 的固定相对路径、不跟随重定向，使用连接/读取超时和有界读取，不附带账号凭据或复用游戏查询请求。HTTP 的压缩传输也拒绝，要求原始字节便于按大小和哈希验证。

发布顺序：先上传图片，再上传映射文件，再生成引用该映射的新版目录，最后切换现有 `current.json`。保留上一版本图片有利于离线客户端重试。服务端应提供 `Content-Length`、原始字节、不重定向的 HTTPS 200 响应。客户端仅依赖上述通用文件接口，未修改游戏协议。

## 本地路由及桥接

原来的本地 `GET /assets/manifest.json` 返回内置底稿与当前已验证远端映射的合并结果，并增加 `_revision`；未激活远端版本时为 `"bundled"`，激活后为映射 SHA-256。下载图片路径改写为：

```text
assets/remote/<mapping-sha256>/assets/portraits/<name>.webp
```

本地图片服务只按当前或上一版本文件索引读取哈希命名的缓存文件，不使用请求路径直接打开私有文件。合并后的 `files` 表也包含这些改写后的 URL 与图片哈希。缓存不会提供任意 JSON 或可执行资源。对当前缓存图片的读取会再次校验大小、哈希和图片头；缺失或损坏时返回 404。

原生桥接动作：

- `visualStatus`：获取视觉更新状态。
- `visualCheck`：从当前目录描述符重新检查；参数 `{ "force": true }` 可跳过失败退避并修复同版本损坏缓存。
- `catalogStatus`、`catalogCheck`、`catalogSource`：保留原返回字段，并增加 `visual_assets` 状态。

视觉状态字段为 `phase`（`unconfigured` / `checking` / `current` / `error`）、`revision`、`version`、`configured`、`checking`、`error`、`checked_at`、`retry_at`、`origin`（`bundled` / `cache`）。失败仅暴露固定的 `VISUAL_*` 错误码。失败后自动检查退避 60 秒；同一已激活描述符无需再次下载。目录完成检查后自动触发视觉检查；切换或清空更新源会先使旧的在途下载失效。清空更新源保留已验证缓存用于离线回退。

前端应持续读取目录状态，直到目录与视觉两者的 `checking` 都为 `false`。视觉 `revision` 改变后重新加载 `/assets/manifest.json`，再重绘角色列表与账号头像；版本路径避免旧图片缓存命中新的映射。

## 缓存与验证

图片和映射先写入版本目录，所有哈希、引用及格式校验成功后，才通过原子移动更新活动指针。保留当前和上一完整版本，启动时验证映射及所有图片；当前损坏时依次回退上一版本、内置版本。中断下载不能激活半套映射。清理范围限定为应用私有视觉缓存中的哈希目录；旧版本被淘汰后已有页面需重新载入最新映射。

`xl_gacha_android/tests/VisualAssetsTest.java` 使用真实 JSON、真实文件系统和可控下载器，不访问生产接口。`scripts/test_native.py` 会编译该测试并打包到 `catalog-tests.jar`。Android 测试入口为 `cn.starfall.gacha.local.VisualAssetsTest <全新可写缓存目录>`。测试涵盖安装、不可变 URL、离线、哈希及路径拒绝、原子回退、损坏修复、版本冲突、源切换并发和内置兼容。

`scripts/build_login_return_test.py` 也会将这组离线测试编入隔离测试 APK。建议通过 `am instrument -w cn.starfall.gacha.returnqa/cn.starfall.gacha.local.VisualAssetsIntegration` 在正常 Android 应用进程运行，检查 instrumentation 返回码为 `-1` 且结果包含 `PASS`。部分模拟器的 `app_process` 在测试打印通过后可能以原生崩溃退出；单凭通过日志不能将这种运行判为成功。
