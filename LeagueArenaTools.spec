# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["LeagueArenaTools.py"],
    pathex=[],
    binaries=[],
    datas=[("league_arena_tools.ico", ".")],
    hiddenimports=["websocket", "pystray", "PIL.Image", "PIL.ImageDraw", "psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LeagueArenaTools",
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
    codesign_identity=None,
    entitlements_file=None,
    icon=["league_arena_tools.ico"],
    version="version_info.txt",
)
