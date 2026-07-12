# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Motherbrain Workstation (Windows .exe).

Vault/config always use ~/.motherbrain via core.paths (not _MEIPASS).
Build:  powershell -File scripts/build_exe.ps1
Output: dist/Motherbrain.exe
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = (
    collect_submodules("core")
    + collect_submodules("tools")
    + ["requests", "urllib3", "certifi", "charset_normalizer", "idna"]
)

a = Analysis(
    ["workstation.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("templates/web_companion.html", "templates"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Motherbrain",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
