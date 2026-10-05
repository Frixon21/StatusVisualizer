# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


hidden_imports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("paho.mqtt")
)


def static_datas():
    """Bundle app/static but omit the dev-only heatmap tuner page."""
    entries = []
    static_root = Path("app/static")
    for path in sorted(static_root.rglob("*")):
        if not path.is_file() or path.name == "heatmap-dev.html":
            continue
        entries.append((str(path), path.parent.as_posix()))
    return entries


a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=static_datas(),
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StatusVisualizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
