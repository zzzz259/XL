# -*- mode: python ; coding: utf-8 -*-
"""XL Update Tool PyInstaller spec（Release 构建，--onedir 模式）

前置条件（缺失会给出清晰报错）：
  1. scripts/release/stage-tools.ps1     -> <repo>/build/stage/tools
  2. scripts/release/prepare-java.ps1    -> xl_updata_tool/runtimes/java
  3. scripts/release/prepare-dotnet.ps1  -> xl_updata_tool/runtimes/dotnet
建议直接使用 scripts/release/build-release.ps1 一键编排。
"""

import os

block_cipher = None

# SPECPATH 由 PyInstaller 注入，为本 spec 文件所在目录（xl_updata_tool/）
APP_DIR = os.path.abspath(SPECPATH)  # noqa: F821
REPO_ROOT = os.path.dirname(APP_DIR)

STAGE_TOOLS = os.path.join(REPO_ROOT, 'build', 'stage', 'tools')
RUNTIMES = os.path.join(APP_DIR, 'runtimes')

_missing = []
if not os.path.isdir(STAGE_TOOLS):
    _missing.append(
        '  - %s\n    请先运行: powershell scripts/release/stage-tools.ps1' % STAGE_TOOLS)
if not os.path.isdir(RUNTIMES):
    _missing.append(
        '  - %s\n    请先运行: powershell scripts/release/prepare-java.ps1 与 '
        'scripts/release/prepare-dotnet.ps1' % RUNTIMES)
if _missing:
    raise SystemExit(
        '[build.spec] 缺少打包输入目录：\n%s\n'
        '或直接运行 scripts/release/build-release.ps1 一键构建。' % '\n'.join(_missing))

hiddenimports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PIL',
    'psutil',
]

# 注意：QtMultimedia / QtMultimediaWidgets 必须保留，音频播放依赖它们。
excludes = [
    'tkinter',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.Qt3DCore',
    'PySide6.QtCharts',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # (源路径, 目标路径) — 打包后相对于 exe 所在目录
        (STAGE_TOOLS, 'tools'),
        (RUNTIMES, 'runtimes'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir 模式：EXE 不得内嵌 binaries/datas（否则退化成每次启动解压到临时目录的
# onefile 行为，体积翻倍且启动缓慢）；二进制与数据只交给 COLLECT 落到 _internal/。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='XL',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    cofile=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='XL',
)
