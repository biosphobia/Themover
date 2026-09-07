# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds a single-file windowed executable "TheMover".
# Usage:  pyinstaller TheMover.spec
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (
    collect_submodules("themover")
    + ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "cv2", "numpy", "PIL", "mss", "pynput", "hid", "anthropic", "httpx2", "pydantic"]
)
try:
    import vgamepad  # noqa: F401
    hidden.append("vgamepad")
    datas = collect_data_files("vgamepad")  # bundles the ViGEmClient DLL
except Exception:
    datas = []
datas += collect_data_files("anthropic")
datas += [("assets/icon.png", "assets")]

a = Analysis(
    ["themover/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TheMover",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico" if os.path.exists("assets/icon.ico") else None,
)
