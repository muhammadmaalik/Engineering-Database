"""Main window and views for the standalone Occhialini desktop app."""

from __future__ import annotations

import html
import importlib
import json
import platform
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, QThreadPool
from PySide6.QtGui import QKeySequence, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS
from .workers import JobRunner


def _optional_core(name: str) -> Any | None:
    try:
        return importlib.import_module(f"core.{name}")
    except Exception:
        return None


CORE = {
    name: _optional_core(name)
    for name in (
        "auth", "context", "discovery", "inference", "isaac_sim",
        "model_catalog", "model_download", "models", "paths",
        "peer_auth", "sync", "sync_service", "vault_index",
    )
}


def _call(module: str, function: str, *args: Any, **kwargs: Any) -> Any:
    target = CORE.get(module)
    callback = getattr(target, function, None) if target else None
    if not callable(callback):
        raise RuntimeError(f"core.{module}.{function} is not available")
    return callback(*args, **kwargs)


def _card(parent: QWidget | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName("glassCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


def _heading(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 8)
    title_label = QLabel(title)
    title_label.setObjectName("pageTitle")
    layout.addWidget(title_label)
    if subtitle:
        caption = QLabel(subtitle)
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        layout.addWidget(caption)
    return box, layout


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class SecurePinEdit(QLineEdit):
    """PIN field that accepts digits but rejects clipboard shortcuts."""

    def keyPressEvent(self, event: Any) -> None:
        if any(
            event.matches(sequence)
            for sequence in (QKeySequence.Paste, QKeySequence.Copy, QKeySequence.Cut)
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: Any) -> None:
        event.accept()


class PinDialog(QDialog):
    """Modal PIN gate backed exclusively by ``core.auth.verify_pin``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unlock Occhialini")
        self.setModal(True)
        self.setFixedSize(390, 245)
        self._tries = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        title = QLabel("Occhialini")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        message = _muted("Enter your Motherbrain PIN to unlock this workstation.")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message)

        self.pin = SecurePinEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setMaxLength(12)
        self.pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pin.setPlaceholderText("PIN")
        self.pin.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,12}"), self.pin))
        self.pin.returnPressed.connect(self._submit)
        layout.addWidget(self.pin)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet(f"color: {COLORS['warning']};")
        layout.addWidget(self.status)
        unlock = QPushButton("Unlock")
        unlock.setObjectName("primary")
        unlock.clicked.connect(self._submit)
        layout.addWidget(unlock)
        self.pin.setFocus()

    def _submit(self) -> None:
        pin = self.pin.text()
        self.pin.clear()
        verifier = getattr(CORE.get("auth"), "verify_pin", None)
        if callable(verifier) and verifier(pin):
            self.accept()
            return
        self._tries += 1
        remaining = 5 - self._tries
        if remaining <= 0:
            self.reject()
            return
        if not callable(verifier):
            self.status.setText("PIN service unavailable")
        else:
            self.status.setText(f"PIN denied · {remaining} attempts remaining")


class TitleBar(QFrame):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__()
        self.window = window
        self.setObjectName("titleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 4, 5, 4)
        label = QLabel("OCCHIALINI")
        label.setStyleSheet("font-weight: 700; letter-spacing: 1px;")
        layout.addWidget(label)
        layout.addStretch()
        for text, callback, object_name in (
            ("—", window.showMinimized, "windowControl"),
            ("□", self._toggle_maximize, "windowControl"),
            ("×", window.close, "closeControl"),
        ):
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.setToolTip({"—": "Minimize", "□": "Maximize or restore", "×": "Close"}[text])
            button.clicked.connect(callback)
            layout.addWidget(button)

    def _toggle_maximize(self) -> None:
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle and hasattr(handle, "startSystemMove"):
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    NAVIGATION = (
        ("Home", "Home"),
        ("Chat", "Chat"),
        ("Projects", "Projects"),
        ("Models", "Models"),
        ("Peers / Sync", "Peers/Sync"),
        ("Isaac Sim", "Isaac Sim"),
        ("Settings", "Settings"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Occhialini")
        self.resize(1280, 800)
        self.setMinimumSize(980, 650)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.pool = QThreadPool.globalInstance()
        self.jobs = JobRunner(self.pool, self)
        self.current_project_id: str | None = None
        self.chat_history: list[dict[str, str]] = []
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.model_entries: list[dict[str, Any]] = []

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        shell = QWidget()
        shell.setObjectName("shell")
        outer.addWidget(shell)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(8, 8, 8, 8)
        shell_layout.setSpacing(8)
        shell_layout.addWidget(TitleBar(self))

        body = QHBoxLayout()
        body.setSpacing(9)
        shell_layout.addLayout(body, 1)
        body.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.status_bar = QLabel("Ready")
        self.status_bar.setObjectName("muted")
        shell_layout.addWidget(self.status_bar)

        self._add_page("Setup", self._build_setup_page())
        self._add_page("Home", self._build_home_page())
        self._add_page("Chat", self._build_chat_page())
        self._add_page("Projects", self._build_projects_page())
        self._add_page("Models", self._build_models_page())
        self._add_page("Peers/Sync", self._build_peers_page())
        self._add_page("Isaac Sim", self._build_isaac_page())
        self._add_page("Settings", self._build_settings_page())

        self.refresh_models()
        self.refresh_projects()
        self.refresh_peers()
        self.load_settings()
        self._select_initial_page()
        self._probe_services()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 14)
        brand = QLabel("◉  Occhialini")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addWidget(_muted("Private intelligence, close at hand."))
        layout.addSpacing(18)
        for label, key in self.NAVIGATION:
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=key: self.show_page(page))
            layout.addWidget(button)
            self.nav_buttons[key] = button
        layout.addStretch()
        self.sidebar_ai = QLabel("● AI checking")
        self.sidebar_ai.setObjectName("muted")
        layout.addWidget(self.sidebar_ai)
        self.sidebar_sync = QLabel("● Sync checking")
        self.sidebar_sync.setObjectName("muted")
        layout.addWidget(self.sidebar_sync)
        return sidebar

    def _add_page(self, key: str, page: QWidget) -> None:
        self.pages[key] = page
        self.stack.addWidget(page)

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        for nav_key, button in self.nav_buttons.items():
            button.setChecked(nav_key == key)
        if key == "Home":
            self.refresh_home()
        elif key == "Projects":
            self.refresh_projects()
        elif key == "Models":
            self.refresh_models()
        elif key == "Peers/Sync":
            self.refresh_peers()
        elif key == "Settings":
            self.load_settings()

    def _select_initial_page(self) -> None:
        has_model = any(bool(item.get("exists", True)) for item in self.model_entries)
        active = self._active_model()
        if not has_model and not active.get("exists"):
            self.stack.setCurrentWidget(self.pages["Setup"])
            for button in self.nav_buttons.values():
                button.setChecked(False)
        else:
            self.show_page("Home")

    def _set_status(self, text: str) -> None:
        self.status_bar.setText(text.splitlines()[0])

    def _show_error(self, title: str, detail: str) -> None:
        self._set_status(detail)
        QMessageBox.warning(self, title, detail.splitlines()[0])

    # Setup and Home -------------------------------------------------
    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.addStretch()
        card, content = _card()
        card.setMaximumWidth(760)
        content.addWidget(_section("Welcome to Occhialini"))
        content.addWidget(
            _muted(
                "Choose a local model to finish setup. Downloads run in the background; "
                "you can continue exploring the app while a job is active."
            )
        )
        self.setup_model = QComboBox()
        content.addWidget(self.setup_model)
        self.setup_progress = QProgressBar()
        self.setup_progress.setRange(0, 100)
        self.setup_progress.setValue(0)
        content.addWidget(self.setup_progress)
        self.setup_status = _muted("No model selected")
        content.addWidget(self.setup_status)
        buttons = QHBoxLayout()
        download = QPushButton("Download & activate")
        download.setObjectName("primary")
        download.clicked.connect(self.download_setup_model)
        buttons.addWidget(download)
        existing = QPushButton("Use an existing model")
        existing.clicked.connect(lambda: self.show_page("Models"))
        buttons.addWidget(existing)
        content.addLayout(buttons)
        centered = QHBoxLayout()
        centered.addStretch()
        centered.addWidget(card)
        centered.addStretch()
        layout.addLayout(centered)
        layout.addStretch()
        return page

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading("Home", "Your private workspace and connected systems at a glance.")
        layout.addWidget(heading)
        metrics = QGridLayout()
        self.home_metrics: dict[str, QLabel] = {}
        for column, (key, label) in enumerate(
            (("projects", "Projects"), ("models", "Local models"), ("peers", "Trusted peers"))
        ):
            card, box = _card()
            box.addWidget(_muted(label))
            value = QLabel("—")
            value.setObjectName("metric")
            box.addWidget(value)
            self.home_metrics[key] = value
            metrics.addWidget(card, 0, column)
        layout.addLayout(metrics)
        card, box = _card()
        box.addWidget(_section("Active intelligence"))
        self.home_model = QLabel("Checking model…")
        self.home_model.setWordWrap(True)
        box.addWidget(self.home_model)
        row = QHBoxLayout()
        self.start_ai_button = QPushButton("Start AI")
        self.start_ai_button.setObjectName("primary")
        self.start_ai_button.clicked.connect(self.start_ai)
        row.addWidget(self.start_ai_button)
        models = QPushButton("Manage models")
        models.clicked.connect(lambda: self.show_page("Models"))
        row.addWidget(models)
        row.addStretch()
        box.addLayout(row)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def refresh_home(self) -> None:
        active = self._active_model()
        self.home_model.setText(
            f"{active.get('filename') or 'No model configured'} · "
            f"{'available' if active.get('exists') else 'file missing'} · "
            f"{active.get('mode', 'local')}"
        )
        self.home_metrics["models"].setText(str(len(self.model_entries)))
        self.home_metrics["projects"].setText(str(self.project_list.count()))
        self.home_metrics["peers"].setText(str(self.peer_list.count()))

    # Chat -----------------------------------------------------------
    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading("Chat", "Talk to the active local or remote Motherbrain model.")
        layout.addWidget(heading)
        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setPlaceholderText("Your conversation will appear here.")
        layout.addWidget(self.chat_view, 1)
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText("Ask Occhialini…")
        self.chat_input.setMaximumHeight(100)
        layout.addWidget(self.chat_input)
        row = QHBoxLayout()
        self.chat_project = QComboBox()
        self.chat_project.addItem("No project", None)
        row.addWidget(self.chat_project)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_chat)
        row.addWidget(clear)
        row.addStretch()
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send_chat)
        row.addWidget(self.send_button)
        layout.addLayout(row)
        return page

    def _append_chat(self, who: str, text: str) -> None:
        color = COLORS["accent"] if who == "You" else COLORS["success"]
        self.chat_view.append(
            f'<p><b style="color:{color}">{html.escape(who)}</b><br>'
            f'{html.escape(text).replace(chr(10), "<br>")}</p>'
        )

    def _clear_chat(self) -> None:
        self.chat_history.clear()
        self.chat_view.clear()

    def send_chat(self) -> None:
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        self.chat_input.clear()
        self._append_chat("You", text)
        self.send_button.setEnabled(False)
        project_id = self.chat_project.currentData()
        history = list(self.chat_history)

        def request() -> str:
            prompt = text
            context_module = CORE.get("context")
            builder = getattr(context_module, "build_chat_prompt", None) if context_module else None
            if callable(builder):
                prompt = builder(text, project_id=project_id, history=history, history_limit=6)
            return str(_call("inference", "complete", prompt, n_predict=768) or "").strip()

        def done(answer: Any) -> None:
            response = str(answer or "(empty response)")
            self.chat_history.append({"user": text, "ai": response})
            self._append_chat("Occhialini", response)
            self.send_button.setEnabled(True)
            self._set_status("Response complete")

        self.jobs.submit(
            request,
            on_result=done,
            on_error=lambda error: (self.send_button.setEnabled(True), self._show_error("Chat", error)),
        )

    # Projects -------------------------------------------------------
    def _build_projects_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading("Projects", "Browse or remove indexed Motherbrain projects.")
        layout.addWidget(heading)
        split = QHBoxLayout()
        self.project_list = QListWidget()
        self.project_list.setMinimumWidth(300)
        self.project_list.currentItemChanged.connect(self._project_selected)
        split.addWidget(self.project_list, 2)
        card, details = _card()
        details.addWidget(_section("Project details"))
        self.project_detail = _muted("Select a project.")
        details.addWidget(self.project_detail)
        details.addStretch()
        self.delete_project_button = QPushButton("Delete project")
        self.delete_project_button.setObjectName("danger")
        self.delete_project_button.setEnabled(False)
        self.delete_project_button.clicked.connect(self.delete_project)
        details.addWidget(self.delete_project_button)
        split.addWidget(card, 3)
        layout.addLayout(split, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_projects)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _load_projects(self) -> list[dict[str, Any]]:
        vault = CORE.get("vault_index")
        if not vault:
            return []
        ensure = getattr(vault, "ensure_tables", None)
        if callable(ensure):
            ensure()
        db = vault.get_db()
        try:
            rows = db.execute(
                "SELECT id, name, description, status, path, tags FROM projects ORDER BY name"
            ).fetchall()
            return [
                {"id": r[0], "name": r[1], "description": r[2], "status": r[3], "path": r[4], "tags": r[5]}
                for r in rows
            ]
        finally:
            db.close()

    def refresh_projects(self) -> None:
        try:
            projects = self._load_projects()
        except Exception as exc:
            self._set_status(f"Projects unavailable: {exc}")
            projects = []
        self.project_list.clear()
        self.chat_project.clear()
        self.chat_project.addItem("No project", None)
        for project in projects:
            item = QListWidgetItem(project.get("name") or project["id"])
            item.setData(Qt.ItemDataRole.UserRole, project)
            self.project_list.addItem(item)
            self.chat_project.addItem(project.get("name") or project["id"], project["id"])
        self.refresh_home()

    def _project_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        project = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.current_project_id = project.get("id") if project else None
        self.delete_project_button.setEnabled(bool(project))
        if not project:
            self.project_detail.setText("Select a project.")
            return
        self.project_detail.setText(
            f"{project.get('description') or 'No description'}\n\n"
            f"ID: {project['id']}\nStatus: {project.get('status') or 'unknown'}\n"
            f"Tags: {project.get('tags') or 'none'}\nPath: {project.get('path') or 'unknown'}"
        )

    def delete_project(self) -> None:
        project_id = self.current_project_id
        if not project_id:
            return
        answer = QMessageBox.question(
            self,
            "Delete project",
            f"Delete “{project_id}” and its files? This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_project_button.setEnabled(False)
        self.jobs.submit(
            _call,
            "vault_index",
            "delete_project",
            project_id,
            remove_files=True,
            on_result=lambda _: (self._set_status("Project deleted"), self.refresh_projects()),
            on_error=lambda error: self._show_error("Delete project", error),
        )

    # Models ---------------------------------------------------------
    def _build_models_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading(
            "Models",
            "Activate local GGUF files or launch a curated/community download job.",
        )
        layout.addWidget(heading)
        self.active_model_label = _muted("Active model: checking…")
        layout.addWidget(self.active_model_label)
        self.model_table = QTableWidget(0, 4)
        self.model_table.setHorizontalHeaderLabels(("Model", "Source", "Size", "State"))
        self.model_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.model_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.model_table, 1)
        row = QHBoxLayout()
        activate = QPushButton("Activate + restart")
        activate.setObjectName("primary")
        activate.clicked.connect(self.activate_selected_model)
        row.addWidget(activate)
        self.model_download_choice = QComboBox()
        row.addWidget(self.model_download_choice, 1)
        download = QPushButton("Download")
        download.clicked.connect(self.download_selected_preset)
        row.addWidget(download)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_model_download)
        row.addWidget(cancel)
        local_import = QPushButton("Import GGUF")
        local_import.clicked.connect(self.import_local_model)
        row.addWidget(local_import)
        repair = QPushButton("Repair")
        repair.clicked.connect(self.repair_selected_model)
        row.addWidget(repair)
        remove = QPushButton("Remove")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove_selected_model)
        row.addWidget(remove)
        layout.addLayout(row)
        community = QHBoxLayout()
        self.community_query = QLineEdit()
        self.community_query.setPlaceholderText("Opt-in Hugging Face GGUF search")
        community.addWidget(self.community_query, 1)
        search = QPushButton("Search")
        search.clicked.connect(self.search_community_models)
        community.addWidget(search)
        self.community_repos = QComboBox()
        community.addWidget(self.community_repos, 1)
        files = QPushButton("List exact files")
        files.clicked.connect(self.load_community_files)
        community.addWidget(files)
        self.community_files = QComboBox()
        community.addWidget(self.community_files, 1)
        selected_download = QPushButton("Download selected")
        selected_download.clicked.connect(self.download_community_file)
        community.addWidget(selected_download)
        layout.addLayout(community)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 100)
        self.model_progress.setValue(0)
        layout.addWidget(self.model_progress)
        self.model_job_status = _muted("No active model job")
        layout.addWidget(self.model_job_status)
        return page

    def _presets(self) -> dict[str, dict[str, Any]]:
        catalog = CORE.get("model_catalog")
        entries = getattr(catalog, "list_curated", lambda: [])() if catalog else []
        return {str(entry["id"]): dict(entry) for entry in entries}

    def _active_model(self) -> dict[str, Any]:
        try:
            return dict(_call("models", "get_active_model") or {})
        except Exception:
            return {}

    def refresh_models(self) -> None:
        try:
            self.model_entries = list(_call("models", "list_all_models") or [])
        except Exception:
            self.model_entries = []
        active = self._active_model()
        self.active_model_label.setText(
            f"Active: {active.get('filename') or 'none'} · "
            f"{'ready on disk' if active.get('exists') else 'file not found'} · "
            f"{active.get('url') or 'no endpoint'}"
        )
        self.model_table.setRowCount(len(self.model_entries))
        for row, model in enumerate(self.model_entries):
            filename = model.get("filename") or model.get("name") or model.get("id") or "Unknown"
            size = int(model.get("size_bytes") or 0)
            values = (
                filename,
                model.get("source") or "disk",
                f"{size / (1024 ** 3):.2f} GB" if size else "—",
                "Active" if filename == active.get("filename") else "Available",
            )
            for column, value in enumerate(values):
                self.model_table.setItem(row, column, QTableWidgetItem(str(value)))
        presets = self._presets()
        selected = self.model_download_choice.currentData() if self.model_download_choice.count() else None
        self.model_download_choice.clear()
        self.setup_model.clear()
        for key, preset in presets.items():
            available = bool(preset.get("available"))
            label = str(preset.get("label") or key)
            if not available:
                self.model_download_choice.addItem(f"{label} — Coming Soon", None)
                item = self.model_download_choice.model().item(self.model_download_choice.count() - 1)
                if item:
                    item.setEnabled(False)
                continue
            details = (
                f"{label} · {preset.get('quantization')} · "
                f"{preset.get('estimated_size_gb') or '?'} GB · {preset.get('license')}"
            )
            self.model_download_choice.addItem(details, key)
            self.setup_model.addItem(details, key)
        if selected:
            index = self.model_download_choice.findData(selected)
            if index >= 0:
                self.model_download_choice.setCurrentIndex(index)
        self.refresh_home()

    def activate_selected_model(self) -> None:
        row = self.model_table.currentRow()
        if row < 0 or row >= len(self.model_entries):
            QMessageBox.information(self, "Models", "Select a local model first.")
            return
        model = self.model_entries[row]
        model_path = model.get("file_path") or model.get("path") or model.get("filename")
        if not model_path:
            return
        self.jobs.submit(
            _call,
            "inference",
            "activate_model",
            model_path,
            start=True,
            on_result=lambda _: (self._set_status(f"Activated {Path(model_path).name}"), self.refresh_models()),
            on_error=lambda error: self._show_error("Activate model", error),
        )

    def download_setup_model(self) -> None:
        self._start_download(self.setup_model.currentData(), self.setup_progress, self.setup_status, setup=True)

    def download_selected_preset(self) -> None:
        self._start_download(
            self.model_download_choice.currentData(),
            self.model_progress,
            self.model_job_status,
            setup=False,
        )

    def _start_download(
        self,
        preset_key: str | None,
        progress_bar: QProgressBar,
        status_label: QLabel,
        *,
        setup: bool,
    ) -> None:
        if not preset_key:
            return
        progress_bar.setRange(0, 0)
        status_label.setText("Preparing download…")
        self.model_cancel_event = threading.Event()

        def progress(percent: int, text: str) -> None:
            if percent >= 0:
                progress_bar.setRange(0, 100)
                progress_bar.setValue(percent)
            status_label.setText(text)

        def done(result: Any) -> None:
            progress_bar.setRange(0, 100)
            progress_bar.setValue(100)
            status_label.setText(f"Ready: {result}")
            self.refresh_models()
            if setup:
                self.show_page("Home")

        self.jobs.submit(
            self._download_model_job,
            preset_key,
            setup,
            with_progress=True,
            on_progress=progress,
            on_result=done,
            on_error=lambda error: (
                progress_bar.setRange(0, 100),
                progress_bar.setValue(0),
                status_label.setText(error.splitlines()[0]),
                self._show_error("Model download", error),
            ),
        )

    def _download_model_job(self, preset_key: str, activate: bool, progress: Any) -> str:
        preset = self._presets()[preset_key]
        progress(3, f"Resolving {preset.get('label', preset_key)}…")
        repository = _call(
            "model_catalog",
            "list_gguf_files",
            preset["repo_id"],
        )
        selected = next(
            item for item in repository["files"]
            if Path(item["filename"]).name == preset["filename"]
        )

        def report(done: int, total: int | None) -> None:
            percent = int(done * 100 / total) if total else -1
            text = (
                f"Downloading {percent}% · {done / (1024**3):.2f} GB"
                if total else f"Downloading {done / (1024**2):.1f} MB"
            )
            progress(percent, text)

        result = _call(
            "model_download",
            "download_gguf",
            repo_id=preset["repo_id"],
            filename=selected["filename"],
            revision=repository["revision"],
            expected_size=selected.get("size_bytes"),
            expected_sha256=selected.get("sha256"),
            metadata={
                "name": preset["label"],
                "quantization": preset.get("quantization"),
                "license": repository.get("license") or preset.get("license"),
                "publisher": preset.get("publisher"),
                "provenance": "curated",
            },
            cancel_event=self.model_cancel_event,
            progress=report,
        )
        if activate:
            progress(96, "Activating and restarting local AI…")
            _call("inference", "activate_model", result, start=True)
            cfg = _call("paths", "load_config")
            cfg.setdefault("models", {})["onboarding_completed"] = True
            _call("paths", "save_config", cfg)
        progress(100, "Download complete")
        return str(result)

    def cancel_model_download(self) -> None:
        event = getattr(self, "model_cancel_event", None)
        if event:
            event.set()
            self.model_job_status.setText("Cancelling; retry will resume the partial download…")

    def _selected_model(self) -> dict[str, Any] | None:
        row = self.model_table.currentRow()
        if 0 <= row < len(self.model_entries):
            return self.model_entries[row]
        return None

    def import_local_model(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import local GGUF", "", "GGUF models (*.gguf)")
        if not filename:
            return
        self.jobs.submit(
            _call,
            "model_download",
            "import_local_gguf",
            filename,
            on_result=lambda result: (
                self._set_status(f"Imported {Path(result).name}"),
                self.refresh_models(),
            ),
            on_error=lambda error: self._show_error("Import model", error),
        )

    def repair_selected_model(self) -> None:
        model = self._selected_model()
        if not model or not model.get("id") or model.get("source") == "disk":
            QMessageBox.information(self, "Repair model", "Select a registered model.")
            return
        self.jobs.submit(
            _call,
            "model_download",
            "repair_model",
            model["id"],
            on_result=lambda result: (
                self._set_status(f"Model status: {result['status']}"),
                self.refresh_models(),
            ),
            on_error=lambda error: self._show_error("Repair model", error),
        )

    def remove_selected_model(self) -> None:
        model = self._selected_model()
        if not model or not model.get("id") or model.get("source") == "disk":
            QMessageBox.information(self, "Remove model", "Select a registered model.")
            return
        if QMessageBox.question(
            self,
            "Remove model",
            "Remove the registry entry and local GGUF file?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.jobs.submit(
            _call,
            "model_download",
            "remove_model",
            model["id"],
            delete_file=True,
            on_result=lambda _: (self._set_status("Model removed"), self.refresh_models()),
            on_error=lambda error: self._show_error("Remove model", error),
        )

    def search_community_models(self) -> None:
        query = self.community_query.text().strip()
        if len(query) < 2:
            return
        cfg = _call("paths", "load_config")
        if not cfg.get("models", {}).get("community_search_enabled"):
            answer = QMessageBox.question(
                self,
                "Enable community search?",
                "Community repositories are not reviewed by Occhialini. "
                "You must inspect the publisher, license, revision, exact file, and size.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            cfg.setdefault("models", {})["community_search_enabled"] = True
            _call("paths", "save_config", cfg)
        self._set_status("Searching Hugging Face GGUF repositories…")

        def loaded(results: Any) -> None:
            self.community_repos.clear()
            for item in results:
                self.community_repos.addItem(
                    f"{item['repo_id']} · {item['license']} · {item['downloads']:,} downloads",
                    item,
                )
            self._set_status(f"Found {len(results)} community repositories")

        self.jobs.submit(
            _call,
            "model_catalog",
            "search_community",
            query,
            on_result=loaded,
            on_error=lambda error: self._show_error("Community search", error),
        )

    def load_community_files(self) -> None:
        repository = self.community_repos.currentData()
        if not repository:
            return

        def loaded(result: Any) -> None:
            self.community_files.clear()
            for item in result["files"]:
                details = {**item, **{k: result.get(k) for k in ("repo_id", "revision", "license", "publisher")}}
                self.community_files.addItem(
                    f"{item['filename']} · {item['quantization']} · "
                    f"{(item.get('size_bytes') or 0) / (1024**3):.2f} GB",
                    details,
                )
            self._set_status(f"Choose one exact file from revision {result['revision'][:12]}")

        self.jobs.submit(
            _call,
            "model_catalog",
            "list_gguf_files",
            repository["repo_id"],
            revision=repository.get("revision"),
            on_result=loaded,
            on_error=lambda error: self._show_error("List GGUF files", error),
        )

    def download_community_file(self) -> None:
        selected = self.community_files.currentData()
        if not selected:
            return
        self.model_cancel_event = threading.Event()
        self.model_progress.setRange(0, 0)
        self.model_job_status.setText(
            f"Downloading {selected['filename']} from {selected['repo_id']}…"
        )

        def job(progress: Any) -> str:
            def report(done: int, total: int | None) -> None:
                percent = int(done * 100 / total) if total else -1
                progress(percent, f"Downloading {done / (1024**3):.2f} GB")

            return str(
                _call(
                    "model_download",
                    "download_gguf",
                    repo_id=selected["repo_id"],
                    filename=selected["filename"],
                    revision=selected["revision"],
                    expected_size=selected.get("size_bytes"),
                    expected_sha256=selected.get("sha256"),
                    metadata={
                        "license": selected.get("license"),
                        "publisher": selected.get("publisher"),
                        "quantization": selected.get("quantization"),
                        "provenance": "community",
                    },
                    cancel_event=self.model_cancel_event,
                    progress=report,
                )
            )

        self.jobs.submit(
            job,
            with_progress=True,
            on_progress=lambda percent, text: (
                self.model_progress.setRange(0, 100) if percent >= 0 else None,
                self.model_progress.setValue(max(0, percent)),
                self.model_job_status.setText(text),
            ),
            on_result=lambda result: (
                self.model_progress.setRange(0, 100),
                self.model_progress.setValue(100),
                self.model_job_status.setText(f"Ready: {result}"),
                self.refresh_models(),
            ),
            on_error=lambda error: self._show_error("Community download", error),
        )

    def start_ai(self) -> None:
        self.start_ai_button.setEnabled(False)
        self._set_status("Starting AI server…")
        self.jobs.submit(
            _call,
            "inference",
            "start_server",
            on_result=lambda result: self._ai_started(bool(result)),
            on_error=lambda error: (self.start_ai_button.setEnabled(True), self._show_error("Start AI", error)),
        )

    def _ai_started(self, ready: bool) -> None:
        self.start_ai_button.setEnabled(True)
        self.sidebar_ai.setText("● AI online" if ready else "● AI did not start")
        self.sidebar_ai.setStyleSheet(f"color: {COLORS['success' if ready else 'danger']};")
        self._set_status("AI is online" if ready else "AI server did not report ready")

    # Peers and sync -------------------------------------------------
    def _build_peers_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading(
            "Peers / Sync",
            "Manage this device identity, pairing, trusted peers, and vault synchronization.",
        )
        layout.addWidget(heading)
        grid = QGridLayout()
        identity_card, identity = _card()
        identity.addWidget(_section("This device"))
        self.device_name = QLineEdit()
        self.device_name.setPlaceholderText("Device name")
        identity.addWidget(self.device_name)
        self.device_id_label = _muted("Identity: loading…")
        identity.addWidget(self.device_id_label)
        save_identity = QPushButton("Save identity")
        save_identity.clicked.connect(self.save_identity)
        identity.addWidget(save_identity)
        grid.addWidget(identity_card, 0, 0)
        pair_card, pair = _card()
        pair.addWidget(_section("Two-device confirmation"))
        self.pair_address = QLineEdit()
        self.pair_address.setPlaceholderText("Host LAN/WireGuard URL, e.g. http://10.0.0.1:8090")
        pair.addWidget(self.pair_address)
        open_pairing = QPushButton("Open 2-minute pairing window")
        open_pairing.clicked.connect(self.open_pairing)
        pair.addWidget(open_pairing)
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("Paste/scan OC1 connection key")
        pair.addWidget(self.pair_code)
        pair_button = QPushButton("Join connection key")
        pair_button.setObjectName("primary")
        pair_button.clicked.connect(self.pair_peer)
        pair.addWidget(pair_button)
        pair_actions = QHBoxLayout()
        check = QPushButton("Check")
        check.clicked.connect(self.check_pairing)
        pair_actions.addWidget(check)
        confirm = QPushButton("Confirm 8-digit code")
        confirm.clicked.connect(self.confirm_pairing)
        pair_actions.addWidget(confirm)
        pair.addLayout(pair_actions)
        self.pair_status = _muted("No pairing window active")
        pair.addWidget(self.pair_status)
        pair.addWidget(
            _muted(
                "Both screens must show the same code and both users must confirm. "
                "Signatures authenticate; use WireGuard to encrypt files in transit."
            )
        )
        grid.addWidget(pair_card, 0, 1)
        layout.addLayout(grid)
        layout.addWidget(_section("Trusted peers"))
        self.peer_list = QListWidget()
        layout.addWidget(self.peer_list, 1)
        row = QHBoxLayout()
        sync_now = QPushButton("Sync now")
        sync_now.setObjectName("primary")
        sync_now.clicked.connect(self.sync_now)
        row.addWidget(sync_now)
        health = QPushButton("Test connection")
        health.clicked.connect(self.sync_health)
        row.addWidget(health)
        start_server = QPushButton("Start sync server")
        start_server.clicked.connect(self.start_sync_server)
        row.addWidget(start_server)
        revoke = QPushButton("Revoke selected")
        revoke.setObjectName("danger")
        revoke.clicked.connect(self.revoke_selected_peer)
        row.addWidget(revoke)
        row.addStretch()
        layout.addLayout(row)
        self.sync_log = QPlainTextEdit()
        self.sync_log.setReadOnly(True)
        self.sync_log.setMaximumHeight(145)
        layout.addWidget(self.sync_log)
        return page

    def refresh_peers(self) -> None:
        try:
            store = CORE["peer_auth"].IdentityStore()
            identity = store.load_or_create_identity()
            peers = store.list_trusted_peers()
        except Exception as exc:
            self._show_error("Peer identity", str(exc))
            return
        self.device_name.setText(identity.name)
        self.device_id_label.setText(
            f"Ed25519 identity: {identity.device_id} · {platform.platform()}"
        )
        cfg = _call("paths", "load_config")
        self.pair_address.setText(
            str((cfg.get("sync") or {}).get("server_url") or "http://10.0.0.1:8090")
        )
        self.peer_list.clear()
        for peer in peers.values():
            item = QListWidgetItem(
                f"{peer.name} · {peer.device_id[:16]} · {', '.join(peer.scopes)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, peer.device_id)
            self.peer_list.addItem(item)
        self.refresh_home()

    def save_identity(self) -> None:
        try:
            CORE["peer_auth"].IdentityStore().rename_identity(self.device_name.text())
            self._set_status("Device identity saved")
            self.refresh_peers()
        except Exception as exc:
            self._show_error("Device identity", str(exc))

    def open_pairing(self) -> None:
        advertised = self.pair_address.text().strip()
        if not advertised:
            return
        self.jobs.submit(
            _call,
            "sync",
            "open_pairing_window",
            advertised,
            on_result=self._pairing_opened,
            on_error=lambda error: self._show_error("Open pairing", error),
        )

    def _pairing_opened(self, context: Any) -> None:
        self.pair_context = dict(context)
        self.pair_code.setText(self.pair_context["connection_key"])
        self.pair_status.setText("Connection key ready · expires in two minutes")
        self._set_status("Share or scan the connection key on the other device")

    def pair_peer(self) -> None:
        code = self.pair_code.text().strip()
        if not code:
            return
        self.jobs.submit(
            _call,
            "sync",
            "join_pairing_window",
            code,
            on_result=self._pairing_joined,
            on_error=lambda error: self._show_error("Pair peer", error),
        )

    def _pairing_joined(self, context: Any) -> None:
        self.pair_context = dict(context)
        self.pair_status.setText(
            f"Verification code: {self.pair_context['sas']} · confirm on both devices"
        )
        self._set_status("Compare the 8-digit code on both devices")

    def check_pairing(self) -> None:
        context = getattr(self, "pair_context", None)
        if not context:
            return
        self.jobs.submit(
            _call,
            "sync",
            "pairing_status",
            context,
            on_result=self._pairing_checked,
            on_error=lambda error: self._show_error("Pairing status", error),
        )

    def _pairing_checked(self, context: Any) -> None:
        self.pair_context = dict(context)
        self.pair_status.setText(
            f"Code: {context.get('sas') or 'waiting'} · "
            f"host {'✓' if context.get('host_confirmed') else '…'} · "
            f"guest {'✓' if context.get('guest_confirmed') else '…'}"
        )
        if context.get("complete"):
            self._set_status("Pairing complete")
            self.refresh_peers()

    def confirm_pairing(self) -> None:
        context = getattr(self, "pair_context", None)
        if not context or not context.get("sas"):
            QMessageBox.information(self, "Pairing", "Wait for both devices to show a code.")
            return
        code, ok = QInputDialog.getText(self, "Confirm pairing", "Matching 8-digit code:")
        if not ok or not code:
            return
        self.jobs.submit(
            _call,
            "sync",
            "confirm_pairing_window",
            context,
            code,
            on_result=self._pairing_checked,
            on_error=lambda error: self._show_error("Confirm pairing", error),
        )

    def revoke_selected_peer(self) -> None:
        item = self.peer_list.currentItem()
        if not item:
            return
        peer_id = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(
            self,
            "Revoke peer",
            f"Revoke trust for {item.text()}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            CORE["peer_auth"].IdentityStore().revoke_peer(str(peer_id))
            self.refresh_peers()
            self._set_status("Peer revoked")
        except Exception as exc:
            self._show_error("Revoke peer", str(exc))

    def sync_health(self) -> None:
        self.sync_log.appendPlainText("Checking sync endpoint…")
        self.jobs.submit(
            lambda: _call("sync", "SyncClient", timeout=5).health(),
            on_result=lambda result: self._sync_result("Health", result),
            on_error=lambda error: self._sync_failed(error),
        )

    def start_sync_server(self) -> None:
        cfg = _call("paths", "load_config")
        sync_cfg = cfg.get("sync") or {}
        self.jobs.submit(
            _call,
            "sync_service",
            "start",
            str(sync_cfg.get("listen_host") or "0.0.0.0"),
            int(sync_cfg.get("port") or 8090),
            on_result=lambda result: self._sync_result("Sync server started", result),
            on_error=lambda error: self._sync_failed(error),
        )

    def sync_now(self) -> None:
        self.sync_log.appendPlainText("Synchronizing vault…")
        self.jobs.submit(
            lambda: _call("sync", "SyncClient").sync_all(),
            on_result=lambda result: self._sync_result("Sync complete", result),
            on_error=lambda error: self._sync_failed(error),
        )

    def _sync_result(self, label: str, result: Any) -> None:
        self.sidebar_sync.setText("● Sync online")
        self.sidebar_sync.setStyleSheet(f"color: {COLORS['success']};")
        self.sync_log.appendPlainText(f"{label}\n{json.dumps(result, indent=2, default=str)}")
        self._set_status(label)

    def _sync_failed(self, error: str) -> None:
        self.sidebar_sync.setText("● Sync offline")
        self.sidebar_sync.setStyleSheet(f"color: {COLORS['danger']};")
        self.sync_log.appendPlainText(error.splitlines()[0])
        self._set_status(error)

    # Isaac Sim ------------------------------------------------------
    def _build_isaac_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading(
            "Isaac Sim",
            "Inspect the optional bridge and control simulation playback without blocking the UI.",
        )
        layout.addWidget(heading)
        card, box = _card()
        box.addWidget(_section("Bridge status"))
        self.isaac_status = _muted("Not checked")
        box.addWidget(self.isaac_status)
        row = QHBoxLayout()
        for label, action in (
            ("Refresh", "status"),
            ("Play", "play"),
            ("Pause", "pause"),
            ("Reset", "reset"),
        ):
            button = QPushButton(label)
            button.setObjectName("primary" if action == "status" else "")
            button.clicked.connect(lambda checked=False, command=action: self.isaac_action(command))
            row.addWidget(button)
        row.addStretch()
        box.addLayout(row)
        layout.addWidget(card)
        self.isaac_details = QPlainTextEdit()
        self.isaac_details.setReadOnly(True)
        layout.addWidget(self.isaac_details, 1)
        return page

    def isaac_action(self, action: str) -> None:
        self.isaac_status.setText("Contacting Isaac bridge…")

        def run() -> Any:
            if action == "status":
                status = _call("isaac_sim", "ping")
                scene = _call("isaac_sim", "get_scene_summary") if getattr(status, "connected", False) else {}
                return {"status": status.as_dict(), "scene": scene}
            return _call("isaac_sim", action)

        def done(result: Any) -> None:
            connected = bool((result.get("status") or {}).get("connected")) if isinstance(result, dict) else False
            self.isaac_status.setText("Bridge online" if connected else f"{action.title()} request complete")
            self.isaac_details.setPlainText(json.dumps(result, indent=2, default=str))

        self.jobs.submit(
            run,
            on_result=done,
            on_error=lambda error: (
                self.isaac_status.setText("Bridge unavailable"),
                self.isaac_details.setPlainText(error),
            ),
        )

    # Settings -------------------------------------------------------
    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        heading, _ = _heading("Settings", "Configure inference, synchronization, and Isaac Sim.")
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        form = QFormLayout(container)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.settings_fields: dict[str, QLineEdit] = {}
        for key, label, secret in (
            ("inference_url", "Inference URL", False),
            ("inference_model", "Model filename", False),
            ("inference_ngl", "GPU layers", False),
            ("inference_ctx", "Context size", False),
            ("sync_url", "Sync server URL", False),
            ("sync_token", "Sync token", True),
            ("isaac_host", "Isaac host", False),
            ("isaac_port", "Isaac port", False),
        ):
            field = QLineEdit()
            if secret:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.settings_fields[key] = field
            form.addRow(label, field)
        self.isaac_enabled = QCheckBox("Enable Isaac Sim bridge")
        form.addRow("", self.isaac_enabled)
        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self.save_settings)
        form.addRow("", save)
        form.addRow("", _muted("Settings are stored by core.paths in the existing Motherbrain config."))
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return page

    def load_settings(self) -> None:
        paths = CORE.get("paths")
        try:
            cfg = paths.load_config() if paths else {}
        except Exception:
            cfg = {}
        inference = cfg.get("inference") or {}
        sync = cfg.get("sync") or {}
        isaac = cfg.get("isaac_sim") or {}
        values = {
            "inference_url": inference.get("url", "http://127.0.0.1:8081"),
            "inference_model": inference.get("model", ""),
            "inference_ngl": inference.get("ngl", 28),
            "inference_ctx": inference.get("ctx", 2048),
            "sync_url": sync.get("server_url", ""),
            "sync_token": sync.get("token", ""),
            "isaac_host": isaac.get("host", "127.0.0.1"),
            "isaac_port": isaac.get("port", 8765),
        }
        for key, value in values.items():
            self.settings_fields[key].setText(str(value))
        self.isaac_enabled.setChecked(bool(isaac.get("enabled", False)))

    def save_settings(self) -> None:
        try:
            ngl = int(self.settings_fields["inference_ngl"].text())
            ctx = int(self.settings_fields["inference_ctx"].text())
            isaac_port = int(self.settings_fields["isaac_port"].text())
        except ValueError:
            QMessageBox.warning(self, "Settings", "GPU layers, context size, and Isaac port must be integers.")
            return
        try:
            cfg = _call("paths", "load_config")
            cfg["inference"] = {
                **(cfg.get("inference") or {}),
                "url": self.settings_fields["inference_url"].text().strip().rstrip("/"),
                "model": self.settings_fields["inference_model"].text().strip(),
                "ngl": ngl,
                "ctx": ctx,
            }
            cfg["sync"] = {
                **(cfg.get("sync") or {}),
                "server_url": self.settings_fields["sync_url"].text().strip().rstrip("/"),
                "token": self.settings_fields["sync_token"].text(),
            }
            cfg["isaac_sim"] = {
                **(cfg.get("isaac_sim") or {}),
                "enabled": self.isaac_enabled.isChecked(),
                "host": self.settings_fields["isaac_host"].text().strip() or "127.0.0.1",
                "port": isaac_port,
            }
            _call("paths", "save_config", cfg)
            self._set_status("Settings saved")
            self.refresh_models()
        except Exception as exc:
            self._show_error("Settings", str(exc))

    # Startup probes -------------------------------------------------
    def _probe_services(self) -> None:
        def probe_ai() -> bool:
            return bool(_call("inference", "is_ready", timeout=2.0))

        self.jobs.submit(
            probe_ai,
            on_result=lambda ready: self._set_ai_probe(bool(ready)),
            on_error=lambda _: self._set_ai_probe(False),
        )
        self.jobs.submit(
            lambda: _call("sync", "SyncClient", timeout=3).health(),
            on_result=lambda _: self._set_sync_probe(True),
            on_error=lambda _: self._set_sync_probe(False),
        )

    def _set_ai_probe(self, ready: bool) -> None:
        self.sidebar_ai.setText("● AI online" if ready else "● AI offline")
        self.sidebar_ai.setStyleSheet(f"color: {COLORS['success' if ready else 'danger']};")

    def _set_sync_probe(self, ready: bool) -> None:
        self.sidebar_sync.setText("● Sync online" if ready else "● Sync offline")
        self.sidebar_sync.setStyleSheet(f"color: {COLORS['success' if ready else 'danger']};")

    def closeEvent(self, event: Any) -> None:
        try:
            _call("sync_service", "stop")
        except Exception:
            pass
        event.accept()
