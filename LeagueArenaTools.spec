# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["LeagueArenaTools.py"],
    pathex=[],
    binaries=[],
    datas=[("league_arena_tools.ico", ".")],
    hiddenimports=["websocket", "pystray", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # A one-file EXE unpacks every bundled binary on each launch, and some
    # packages add runtime hooks that run before the app starts. None of these
    # are used: requests falls back to charset_normalizer, Pillow only needs
    # PNG/ICO decoding (no fonts, AVIF or colour management), and TLS uses
    # the standard ssl module.
    excludes=[
        "cryptography", "OpenSSL", "chardet", "brotli", "brotlicffi",
        "h2", "hpack", "hyperframe", "socks", "zstandard",
        "setuptools", "pkg_resources", "_distutils_hack", "pydoc_data",
        "PIL._avif", "PIL.AvifImagePlugin", "PIL._imagingft", "PIL._imagingcms",
        "PIL.ImageCms", "PIL.ImageQt", "numpy",
    ],
    noarchive=False,
    optimize=1,
)
# Tcl's timezone database and clock translations are ~740 tiny files that
# would be unpacked on every launch, but only Tcl's `clock` command uses
# them and neither tkinter nor this app calls it.
a.datas = [
    entry for entry in a.datas
    if not entry[0].replace("\\", "/").startswith(
        ("_tcl_data/tzdata/", "_tcl_data/msgs/", "h2-")
    )
]
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
