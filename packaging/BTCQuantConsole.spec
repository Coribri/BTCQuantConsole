# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("fastapi")
    + collect_submodules("starlette") + collect_submodules("pydantic")
    + collect_submodules("anyio") + ["eth_hash.backends.pycryptodome"])
a = Analysis([str(ROOT / "run_console.py")], pathex=[str(ROOT)], binaries=[],
    datas=[(str(ROOT / "btcquant/static"), "btcquant/static")]
        + copy_metadata("hyperliquid-python-sdk", recursive=True),
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["matplotlib", "pytest", "tkinter"], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BTCQuantConsole",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=[str(ROOT / "assets/icon.ico")],
    version=str(ROOT / "packaging/version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="BTCQuantConsole")
