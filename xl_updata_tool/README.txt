====================================
  XL Update Tool - 游戏更新管理工具
  源码版使用说明
====================================

【项目简介】
  本工具用于监控游戏更新、下载资源包、追踪版本变化、
  以及将资源导入 AssetStudio 浏览解析。

【环境要求】
  - Windows 10/11 64位
  - Python 3.10 或更高版本（推荐 3.12）
  - 已安装 pip

【安装步骤】

  1. 解压本压缩包到任意目录

  2. 打开命令行（cmd 或 PowerShell），进入解压后的目录：
     cd xl_updata_tool

  3. 安装依赖（只需执行一次）：
     pip install PySide6

     如果下载慢，可用国内镜像：
     pip install PySide6 -i https://pypi.tuna.tsinghua.edu.cn/simple

  4. 运行：
     双击 run.bat
     或在命令行执行：python main.py

【首次使用】

  首次启动会自动检查更新。
  之后在顶部工具栏点击「检查更新」即可。

  主界面是一个版本列表，每行显示：
    版本日期  |  下载状态  |  Bundle数量  |  备注  |  [操作按钮]

  每行有三个操作按钮：
    [增量下载]  - 只下载相比上一版本有变化的文件（推荐）
    [全量下载]  - 下载此版本的全部文件（会弹窗确认）
    [删除已下载] - 删除此版本已下载的本地文件

  选中版本后，点击顶部「导入AS」可浏览已下载的资源。

【目录结构】

  xl_updata_tool/
    main.py              程序入口
    run.bat              一键启动脚本
    requirements.txt     Python依赖
    README.txt           本说明文件
    app/                 源代码
      core/              核心逻辑（下载、解析、数据库）
      ui/                UI界面
    tools/
      AssetStudio/       内置的AssetStudio CLI解析工具
    data/
      xl_updata.db       版本数据库（已预置历史版本信息）
      bundles/           下载的资源文件存放处（初始为空）

【常见问题】

  Q: 启动报错 "No module named PySide6"
  A: 未安装依赖，执行 pip install PySide6

  Q: 提示找不到 AssetStudio.CLI.exe
  A: 确保 tools/AssetStudio/ 目录完整存在

  Q: 下载速度慢
  A: bundle文件托管在CDN上，速度取决于网络环境

  Q: 能离线使用吗
  A: 浏览已下载的资源和查看版本历史可以离线，
     但检查更新和下载bundle需要联网
