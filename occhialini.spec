# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-file build for the modern PySide6 Occhialini app."""

block_cipher = None

hidden = [
    "requests",
    "huggingface_hub",
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "core",
    "core.auth",
    "core.context",
    "core.discovery",
    "core.flywheel",
    "core.inference",
    "core.isaac_sim",
    "core.model_catalog",
    "core.model_download",
    "core.models",
    "core.paths",
    "core.peer_auth",
    "core.sync",
    "core.sync_service",
    "core.tools",
    "core.vault_index",
    "modern_desktop",
    "modern_desktop.main_window",
    "modern_desktop.theme",
    "modern_desktop.workers",
    "sync_server",
]

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
]

a = Analysis(
    ["modern_app.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("assets/occhialini.png", "assets"),
        ("assets/occhialini.ico", "assets"),
        ("templates/web_companion.html", "templates"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name="Occhialini",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest="packaging/occhialini.manifest",
    icon="assets/occhialini.ico",
    version="packaging/occhialini_version_info.txt",
)
