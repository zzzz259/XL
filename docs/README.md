# XL 文档地图

本页标明正式文档的唯一职责和维护入口。项目事实以当前代码、受版本控制的配置示例及自动化脚本为准；历史计划不代表当前实现。

## 仓库级

| 文档 | 内容 |
|---|---|
| [README](../README.md) | 仓库总览、子项目导航、长期分支入口 |
| [贡献指南](../CONTRIBUTING.md) | 分支晋级、PR、提交、验证和发布流程 |
| [项目规范](../项目规范.md) | monorepo 布局、环境隔离、文档与产物卫生 |
| [安全与凭据](../SECURITY.md) | 凭据处理、外部资源边界、安全问题报告 |

## 子项目

### 更新工具

- [README](../xl_updata_tool/README.md)：用户快速开始、使用流程、源码环境和构建入口。
- [架构与协作基线](../xl_updata_tool/docs/架构与协作基线.md)：功能域、运行目录/产物契约及跨域变更边界。
- [开发文档指南](../xl_updata_tool/docs/开发文档指南.md)：开发、测试、调试和故障定位。
- [代码所有权与边界](../xl_updata_tool/docs/代码所有权与边界.md)：模块责任和公共边界。
- [版本历史](../xl_updata_tool/docs/版本历史.md)：已记录版本的功能、修复与架构变化。

### 更新服务端

- [README](../xl_updata_server/README.md)：服务职责、开发入口和生产运行概览。
- [配置与运维](../xl_updata_server/docs/配置与运维.md)：配置、依赖安装、部署、调度、产物和故障排查。

### QQ bot

- [README](../xl_qqbot/README.md)：功能、拓扑、快速启动与项目测试入口。
- [配置与运维](../xl_qqbot/docs/配置与运维.md)：TOML 配置、权限分级、systemd 部署、状态/日志及故障排查。

## 随工具分发的第三方材料与日志

以下文件是随外部工具带入的上游材料或历史运行日志，不是 XL 的政策、教程或当前运行日志来源。除非明确进行供应商升级或日志取证，不要把它们当作项目文档改写：

- [`vgmstream` 上游 README](../xl_updata_tool/tools/vgmstream/README.md)
- [QuickBMS 随附说明](../xl_updata_tool/tools/epic7_debank_v1_0/_subcontractors/quickbms.txt)
- [AssetStudio `log.txt`](../xl_updata_tool/tools/AssetStudio/log.txt) 与 [`log_prev.txt`](../xl_updata_tool/tools/AssetStudio/log_prev.txt)：随工具目录存在的历史日志，可能包含原机器路径；诊断时先审查敏感信息再分享。
- [`opmap.txt`](../xl_updata_tool/tools/lua/opmap.txt) 是 Lua opcode 映射数据，不是文档。

临时计划、审计笔记和测试草稿保存在 Codex 工作区，不放入本仓库。只把长期有效、可由实现验证的信息写进本地图示的正式文档。
