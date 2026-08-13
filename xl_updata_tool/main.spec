# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # --- 将 tools 目录完整打包到根目录 ---
        ('tools', 'tools'),
    ],
    hiddenimports=[
        # --- PySide6 动态加载的模块，防止打包后找不到 ---
        'PySide6.QtSvg',
        'PySide6.QtNetwork',
        'PySide6.QtMultimedia',
        'PySide6.QtWebEngineWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # --- 排除不需要的大模块以减小体积（可选）---
        # 'tkinter', 'matplotlib', 'numpy',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,          # 建议设为 True 以减小体积
    upx=True,             # 建议启用 UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 与 -w 参数对应
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)