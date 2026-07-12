# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Motherbrain Workstation (Windows .exe).

Vault/config always use ~/.motherbrain via core.paths (not _MEIPASS).
Build:  powershell -File scripts/build_exe.ps1
Output: dist/Motherbrain.exe
"""

block_cipher = None

# Only ship modules the workstation GUI needs. collect_submodules("core") would
# also pull web_companion (and optional ML stacks via huggingface hooks).
hidden = [
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
    "huggingface_hub",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "core",
    "core.paths",
    "core.context",
    "core.tools",
    "core.inference",
    "core.models",
    "core.sync",
    "core.vault_index",
    "core.flywheel",
    "core.devices",
    "tools",
    "tools.system_agent",
]

# Keep the onefile under ~50MB — training/torch lives in shell/, not the GUI.
excludes = [
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "tensorboard",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "matplotlib",
    "cv2",
    "unsloth",
    "transformers",
    "accelerate",
    "triton",
    "sympy",
    "IPython",
    "notebook",
    "core.web_companion",
]

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
    excludes=excludes,
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
    manifest="motherbrain.manifest",  # Per-monitor DPI aware (sharp Tk on Windows)
)
