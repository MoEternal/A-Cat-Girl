from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).parents[1]
backend_src = project_root / "backend" / "src"
icon_path = project_root / "deploy" / "windows" / "catgirl.ico"
caption_icon_path = project_root / "deploy" / "windows" / "catgirl-window-black.ico"

datas = [
    (str(project_root / "frontend" / "dist"), "frontend/dist"),
    (str(project_root / "plugins"), "plugins"),
    (str(icon_path), "assets"),
    (str(caption_icon_path), "assets"),
]
datas += collect_data_files("tiktoken")
hiddenimports = [
    "catgirl.plugins.file_memory",
    "html.parser",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    *collect_submodules("tiktoken"),
    *collect_submodules("tiktoken_ext"),
]

a = Analysis(
    [str(project_root / "scripts" / "webview_entry.py")],
    pathex=[str(backend_src)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="A Cat Girl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(caption_icon_path),
    contents_directory="runtime",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="A Cat Girl",
)
