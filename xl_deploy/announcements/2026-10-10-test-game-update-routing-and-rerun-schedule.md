本次 test 部署用于验证 GitHub 自动部署，并包含两项代码修复：

- 更新服务的复刻排期解析兼容 CDN 保留大小写的 Lua 文件名，避免 Linux 因大小写不匹配跳过排期生成。
- 共享 router 的代码已支持 test 更新来源进入持久发件箱，并限制在 test/debug 群；但 router 只随 main 发布，因此这次 test 部署不会启用该项，仍需后续单独发布 router 才能端到端验证 test 游戏更新播报。

本次只验证 test 环境，不代表 main 已部署或完成线上验证。
