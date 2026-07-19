#!/usr/bin/env python3
"""Launch the standalone modern Occhialini PySide6 desktop app."""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    except ImportError:
        print(
            "PySide6 is required to run the modern Occhialini app. "
            "Install it in the environment you use to launch this file.",
            file=sys.stderr,
        )
        return 2

    from modern_desktop.main_window import MainWindow, PinDialog
    from modern_desktop.theme import apply_windows_backdrop, stylesheet

    high_dpi_pixmaps = getattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None)
    if high_dpi_pixmaps is not None:
        QApplication.setAttribute(high_dpi_pixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Occhialini")
    app.setOrganizationName("Occhialini")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    try:
        from core import paths

        app.setWindowIcon(QIcon(str(paths.bundle_dir() / "assets" / "occhialini.png")))
    except Exception:
        pass

    skip_pin = os.environ.get("MOTHERBRAIN_SKIP_PIN", "").strip().lower() in {"1", "true", "yes"}
    if not skip_pin:
        gate = PinDialog()
        if gate.exec() != QDialog.DialogCode.Accepted:
            return 1

    try:
        window = MainWindow()
        window.show()
        apply_windows_backdrop(window)
        if os.environ.get("OCCHIALINI_SMOKE", "").strip().lower() in {"1", "true", "yes"}:
            QTimer.singleShot(1500, app.quit)
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "Occhialini", f"The app could not start:\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
