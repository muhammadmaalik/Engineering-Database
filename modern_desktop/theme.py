"""Shared colors and Qt stylesheet for the modern desktop client."""

from __future__ import annotations

import ctypes
import os
from typing import Any

COLORS = {
    "window": "#080B12",
    "surface": "#111722",
    "surface_raised": "#182131",
    "glass": "rgba(24, 33, 49, 220)",
    "border": "rgba(255, 255, 255, 34)",
    "text": "#F5F7FB",
    "muted": "#AAB4C4",
    "accent": "#79B8FF",
    "accent_strong": "#3D8BFF",
    "success": "#68D391",
    "warning": "#F6C760",
    "danger": "#FF7B86",
}


def stylesheet() -> str:
    c = COLORS
    return f"""
    * {{
        font-family: "Segoe UI Variable", "Segoe UI";
        font-size: 10pt;
        color: {c["text"]};
    }}
    QMainWindow, QDialog, QWidget#root {{
        background: transparent;
    }}
    QWidget#shell {{
        background: {c["window"]};
        border: 1px solid {c["border"]};
        border-radius: 18px;
    }}
    QWidget#sidebar, QFrame#glassCard, QFrame#titleBar {{
        background: {c["glass"]};
        border: 1px solid {c["border"]};
        border-radius: 14px;
    }}
    QFrame#titleBar {{
        border-radius: 13px;
    }}
    QLabel#brand {{
        font-size: 16pt;
        font-weight: 700;
        color: {c["text"]};
    }}
    QLabel#pageTitle {{
        font-size: 24pt;
        font-weight: 650;
    }}
    QLabel#sectionTitle {{
        font-size: 14pt;
        font-weight: 650;
    }}
    QLabel#muted, QLabel#caption {{
        color: {c["muted"]};
    }}
    QLabel#metric {{
        font-size: 26pt;
        font-weight: 700;
        color: {c["accent"]};
    }}
    QPushButton {{
        min-height: 34px;
        padding: 2px 14px;
        background: {c["surface_raised"]};
        border: 1px solid {c["border"]};
        border-radius: 9px;
    }}
    QPushButton:hover {{
        background: #223047;
        border-color: rgba(121, 184, 255, 110);
    }}
    QPushButton:pressed {{
        background: #172235;
    }}
    QPushButton:disabled {{
        color: #6F7887;
        background: #111722;
    }}
    QPushButton#primary {{
        color: #06111F;
        background: {c["accent"]};
        border-color: {c["accent"]};
        font-weight: 650;
    }}
    QPushButton#primary:hover {{
        background: #A5D0FF;
    }}
    QPushButton#danger {{
        color: {c["danger"]};
    }}
    QPushButton#nav {{
        min-height: 40px;
        text-align: left;
        padding-left: 14px;
        border-color: transparent;
        background: transparent;
        color: {c["muted"]};
    }}
    QPushButton#nav:hover {{
        background: rgba(255, 255, 255, 12);
        color: {c["text"]};
    }}
    QPushButton#nav:checked {{
        color: {c["text"]};
        background: rgba(121, 184, 255, 32);
        border-color: rgba(121, 184, 255, 70);
    }}
    QPushButton#windowControl, QPushButton#closeControl {{
        min-width: 34px;
        max-width: 34px;
        min-height: 28px;
        padding: 0;
        border: 0;
        background: transparent;
    }}
    QPushButton#windowControl:hover, QPushButton#closeControl:hover {{
        background: rgba(255, 255, 255, 22);
    }}
    QPushButton#closeControl:hover {{
        background: #C42B3A;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QListWidget, QTableWidget {{
        background: {c["surface"]};
        border: 1px solid {c["border"]};
        border-radius: 9px;
        padding: 8px;
        selection-background-color: {c["accent_strong"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border-color: {c["accent"]};
    }}
    QComboBox::drop-down {{
        border: 0;
        width: 28px;
    }}
    QListWidget::item {{
        min-height: 34px;
        padding: 5px;
        border-radius: 7px;
    }}
    QListWidget::item:selected {{
        background: rgba(121, 184, 255, 45);
    }}
    QHeaderView::section {{
        background: {c["surface_raised"]};
        border: 0;
        border-bottom: 1px solid {c["border"]};
        padding: 8px;
        color: {c["muted"]};
    }}
    QProgressBar {{
        min-height: 8px;
        max-height: 8px;
        border: 0;
        border-radius: 4px;
        background: #202A3B;
        text-align: center;
    }}
    QProgressBar::chunk {{
        border-radius: 4px;
        background: {c["accent"]};
    }}
    QScrollBar:vertical {{
        width: 10px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        min-height: 28px;
        border-radius: 5px;
        background: #38445A;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
    }}
    QToolTip {{
        color: {c["text"]};
        background: #202A3B;
        border: 1px solid {c["border"]};
        padding: 5px;
    }}
    """


def apply_windows_backdrop(widget: Any) -> None:
    """Enable Windows 11 Mica/rounded corners when DWM supports them."""
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        mica = ctypes.c_int(2)  # DWMSBT_MAINWINDOW
        corners = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(mica), ctypes.sizeof(mica))
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
    except (AttributeError, OSError, ValueError):
        pass
