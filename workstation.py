#!/usr/bin/env python3
"""
Motherbrain Workstation v2.1 - Performance Optimized
Fixed: threading, UI lag, CAD viewer, tree lazy-loading
Wired to companion core (context, tools, inference, models, sync, flywheel).
"""

import sys

# Windows high-DPI: must run before Tk is created (avoids blurry scaled UI).
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, filedialog, simpledialog
import threading, json, subprocess, sqlite3, time, webbrowser, os, shutil
from pathlib import Path
from datetime import datetime
from queue import Queue, Empty

from core import paths as mb_paths
from core import context as mb_context
from core import tools as mb_tools
from core import inference as mb_inference
from core import models as mb_models
from core import model_catalog as mb_model_catalog
from core import model_download as mb_model_download
from core import sync as mb_sync
from core import sync_service as mb_sync_service
from core import vault_index as mb_vault
from core import flywheel as mb_flywheel
from core import isaac_sim as mb_isaac
from core import auth as mb_auth


# ─── Paths (from companion core) ─────────────────────────────
mb_paths.ensure_dirs()
mb_vault.ensure_tables()

VAULT_DB = mb_paths.VAULT_DB
VAULT_ROOT = mb_paths.VAULT_ROOT
PROJECTS_DIR = mb_paths.PROJECTS_DIR
MODELS_DIR = mb_paths.MODELS_DIR
ADAPTERS_DIR = mb_paths.ADAPTERS_DIR
DATASETS_DIR = mb_paths.DATASETS_DIR
EXPORTS_DIR = mb_paths.EXPORTS_DIR
SCREENSHOTS_DIR = mb_paths.SCREENSHOTS_DIR
CHATS_DIR = mb_paths.CHATS_DIR
CHATS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Colors ──────────────────────────────────────────────────
BG, PANEL_BG, CHAT_BG, SIDEBAR_BG = "#1a1a1a", "#222222", "#282828", "#1e1e1e"
USER_COLOR, AI_COLOR, INPUT_BG, TEXT_COLOR = "#4a9eff", "#50fa7b", "#333333", "#e0e0e0"
ACCENT, DIM, BORDER, HEADER_BG, WARN = "#ff6b6b", "#777777", "#3a3a3a", "#111111", "#ffaa00"


class Workstation:
    def __init__(self, root):
        self.root = root
        self.root.title("Motherbrain Workstation v2.1")
        try:
            self.root.iconbitmap(str(mb_paths.bundle_dir() / "assets" / "occhialini.ico"))
        except Exception:
            pass
        self.root.geometry("1500x900")
        self.root.configure(bg=BG)
        self.root.minsize(1100, 600)
        
        self.server_ready = False
        self.math_mode = False
        self.last_ai_response = ""
        self.current_project = None
        self.current_project_name = "None"
        self.cmd_history, self.cmd_index = [], -1
        self.current_cmd_start = "1.0"
        self.photo_path = None
        self.chat_context = []
        self.current_chat_name = None
        self.current_chat_file = None
        self.ui_queue = Queue()
        self._after_id = None
        self.sync_status_text = "● Sync —"
        self.wsl_proc = None
        self._wsl_available = None
        self._term_line_buf = ""

        
        # Start UI poller
        self._poll_ui_queue()
        
        # ─── TOP BAR ──────────────────────────────────────────
        topbar = tk.Frame(root, bg=HEADER_BG, height=44)
        topbar.pack(fill=tk.X, side=tk.TOP); topbar.pack_propagate(False)
        
        tk.Label(topbar, text="🧠", font=("Segoe UI", 16), bg=HEADER_BG).pack(side=tk.LEFT, padx=10)
        tk.Label(topbar, text="MOTHERBRAIN WORKSTATION", fg=AI_COLOR, bg=HEADER_BG,
                font=("Consolas", 13, "bold")).pack(side=tk.LEFT, pady=8)
        
        self.top_status = tk.Label(topbar, text="● AI Offline", fg="#ff4444", bg=HEADER_BG, font=("Consolas", 9))
        self.top_status.pack(side=tk.RIGHT, padx=10)
        tk.Button(topbar, text="⚡ Start AI", command=lambda: threading.Thread(target=self.start_ai_server, daemon=True).start(),
                 bg="#2a5a2a", fg=TEXT_COLOR, font=("Consolas", 8), relief=tk.FLAT, cursor="hand2", padx=8).pack(side=tk.RIGHT, padx=5)
        self.sync_status = tk.Label(topbar, text=self.sync_status_text, fg=DIM, bg=HEADER_BG, font=("Consolas", 9))
        self.sync_status.pack(side=tk.RIGHT, padx=8)
        tk.Button(topbar, text="🔄 Sync Now", command=lambda: threading.Thread(target=self.run_sync_now, daemon=True).start(),
                 bg="#2a3a5a", fg=TEXT_COLOR, font=("Consolas", 8), relief=tk.FLAT, cursor="hand2", padx=8).pack(side=tk.RIGHT, padx=5)
        
        # ─── MAIN LAYOUT ──────────────────────────────────────
        self.main_paned = tk.PanedWindow(root, orient=tk.HORIZONTAL, bg=BORDER, sashwidth=3)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        
        # ─── SIDEBAR ──────────────────────────────────────────
        sidebar = tk.Frame(self.main_paned, bg=SIDEBAR_BG, width=195)
        self.main_paned.add(sidebar)
        
        tk.Label(sidebar, text="◆ NAVIGATION", fg=DIM, bg=SIDEBAR_BG, font=("Consolas", 8, "bold")).pack(pady=(12,6), padx=8, anchor="w")
        
        nav = [
            ("💬  AI Chat", self.show_chat),
            ("📁  Project Editor", self.show_project_editor),
            ("🧠  Training Console", self.show_training_console),
            ("📊  Dashboard", self.show_dashboard),
            ("📦  Model Manager", self.show_model_manager),
            ("🗄️  Vault Explorer", self.show_vault_explorer),
            ("📸  Photo Analyzer", self.show_photo_analyzer),
            ("🔧  Hardware Config", self.show_hardware_config),
            ("🤖  Isaac Sim", self.show_isaac_sim),
            ("📚  Dataset Manager", self.show_dataset_manager),
            ("🔄  Vault Sync", self.show_vault_sync),
            ("⚙️  Settings", self.show_settings),
        ]
        
        for text, cmd in nav:
            btn = tk.Button(sidebar, text=text, command=cmd, bg=SIDEBAR_BG, fg="#bbbbbb",
                          font=("Consolas", 9), relief=tk.FLAT, anchor="w", padx=8, pady=5,
                          activebackground="#333", activeforeground=TEXT_COLOR, cursor="hand2")
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#2a2a2a"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=SIDEBAR_BG))
        
        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill=tk.X, padx=8, pady=8)
        tk.Label(sidebar, text="◆ CHAT HISTORY", fg=DIM, bg=SIDEBAR_BG, font=("Consolas", 8, "bold")).pack(pady=3, padx=8, anchor="w")
        self.chat_listbox = tk.Listbox(
            sidebar, bg=INPUT_BG, fg=TEXT_COLOR, font=("Consolas", 8),
            relief=tk.FLAT, selectbackground=USER_COLOR, height=6,
        )
        self.chat_listbox.pack(fill=tk.X, padx=8, pady=3)
        self.chat_listbox.bind("<<ListboxSelect>>", self.load_chat)
        self.refresh_chat_list()
        tk.Button(sidebar, text="+ New Chat", command=self.new_chat, bg="#2a5a2a", fg=TEXT_COLOR,
                 font=("Consolas", 8), relief=tk.FLAT, cursor="hand2").pack(fill=tk.X, padx=8, pady=2)

        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill=tk.X, padx=8, pady=5)
        tk.Label(sidebar, text="◆ PROJECT", fg=DIM, bg=SIDEBAR_BG, font=("Consolas", 8, "bold")).pack(pady=3, padx=8, anchor="w")
        
        self.project_combo = ttk.Combobox(sidebar, state="readonly", font=("Consolas", 9))
        self.project_combo.pack(fill=tk.X, padx=8, pady=3)
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_select)
        self.refresh_project_list()
        
        btn_frame = tk.Frame(sidebar, bg=SIDEBAR_BG); btn_frame.pack(fill=tk.X, padx=8, pady=3)
        tk.Button(btn_frame, text="+ New", command=self.new_project_dialog, bg="#2a5a2a", fg=TEXT_COLOR,
                 font=("Consolas", 8), relief=tk.FLAT, cursor="hand2").pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # ─── WORK AREA ────────────────────────────────────────
        self.work_frame = tk.Frame(self.main_paned, bg=BG)
        self.main_paned.add(self.work_frame)
        
        # ─── RIGHT TERMINAL (WSL) ──────────────────────────────
        right_frame = tk.Frame(self.main_paned, bg=PANEL_BG, width=420)
        self.main_paned.add(right_frame)
        
        tk.Label(right_frame, text="◆ WSL TERMINAL", fg=AI_COLOR, bg=PANEL_BG, font=("Consolas", 9, "bold")).pack(pady=5)
        
        term_frame = tk.Frame(right_frame, bg="#000000", highlightthickness=1, highlightbackground=BORDER)
        term_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.terminal_text = tk.Text(term_frame, bg="#000000", fg="#00ff41", font=("Consolas", 9),
                                     insertbackground="#00ff41", relief=tk.FLAT, padx=8, pady=8)
        self.terminal_text.pack(fill=tk.BOTH, expand=True)
        self.terminal_text.bind("<Return>", self.terminal_execute)
        self.terminal_text.bind("<Key>", self.terminal_key)
        self.terminal_text.bind("<BackSpace>", self.terminal_backspace)
        self.terminal_text.bind("<Control-c>", self.terminal_interrupt)
        self.terminal_text.bind("<Control-l>", lambda e: self.terminal_clear() or "break")
        
        self.terminal_text.tag_config("prompt", foreground="#00ff41")
        self.terminal_text.tag_config("output", foreground="#00cc33")
        self.terminal_text.tag_config("error", foreground="#ff4444")
        self.terminal_text.tag_config("info", foreground="#ffaa00")
        
        self._start_wsl_terminal()
        
        # ─── BOTTOM BAR ───────────────────────────────────────
        self.bottom_bar = tk.Label(root, text="Ready.", fg=DIM, bg=HEADER_BG, font=("Consolas", 8), anchor="w")
        self.bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)
        
        threading.Thread(target=self.start_ai_server, daemon=True).start()
        threading.Thread(target=self.refresh_sync_status, daemon=True).start()
        # Re-detect an externally started llama-server (e.g. already on :8081).
        self.root.after(2000, self._poll_server_ready)
        self.show_chat()
    
    # ═══════════════════════════════════════════════════════════
    # UI QUEUE (prevents lag)
    # ═══════════════════════════════════════════════════════════
    
    def _poll_ui_queue(self):
        """Process UI updates from background threads without blocking."""
        try:
            while True:
                func, args = self.ui_queue.get_nowait()
                func(*args)
        except Empty:
            pass
        self._after_id = self.root.after(50, self._poll_ui_queue)
    
    def ui_call(self, func, *args):
        """Thread-safe way to update UI from background threads."""
        self.ui_queue.put((func, args))
    
    # ═══════════════════════════════════════════════════════════
    # UTILITY
    # ═══════════════════════════════════════════════════════════
    
    def get_db(self): return sqlite3.connect(str(VAULT_DB), timeout=5)
    def set_bottom(self, text): self.ui_call(self.bottom_bar.config, {"text": text})
    def clear_work(self):
        for w in self.work_frame.winfo_children(): w.destroy()
    
    def refresh_project_list(self):
        try:
            db = self.get_db()
            projects = db.execute("SELECT id, name FROM projects").fetchall()
            names = ["None"] + [f"{name} ({pid})" for pid, name in projects]
            self.project_combo["values"] = names
            self.project_combo.set(self.current_project_name if self.current_project else "None")
            db.close()
        except Exception as e:
            pass
    
    def on_project_select(self, event):
        val = self.project_combo.get()
        self.current_project = None if val == "None" else val.split("(")[-1].rstrip(")")
        self.current_project_name = val
        self.set_bottom(f"Project: {val}")
    
    # ═══════════════════════════════════════════════════════════
    # WSL TERMINAL (interactive bash via wsl.exe)
    # ═══════════════════════════════════════════════════════════

    def _wsl_installed(self) -> bool:
        if self._wsl_available is not None:
            return self._wsl_available
        try:
            r = subprocess.run(
                ["wsl.exe", "-l", "-v"],
                capture_output=True, timeout=10,
            )
            # wsl -l output is UTF-16LE on Windows; treat any success as installed.
            out = (r.stdout or b"") + (r.stderr or b"")
            text = out.decode("utf-16-le", errors="ignore") or out.decode("utf-8", errors="ignore")
            self._wsl_available = r.returncode == 0 and ("Ubuntu" in text or "VERSION" in text.upper() or "NAME" in text.upper())
        except Exception as e:
            self._wsl_available = False
        return self._wsl_available

    def _start_wsl_terminal(self):
        self.terminal_text.delete("1.0", tk.END)
        if not self._wsl_installed():
            self.terminal_text.insert(
                tk.END,
                "WSL is not installed or no distro is registered.\n"
                "Install with: wsl --install\n"
                "Then restart Workstation for a real Linux shell.\n"
                "Fallback: local PowerShell one-shots still work via 'win:' prefix.\n\n",
                "error",
            )
            self.terminal_prompt()
            return
        try:
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW
            self.wsl_proc = subprocess.Popen(
                ["wsl.exe", "-e", "bash", "-l"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=creationflags,
            )
            self.terminal_text.insert(tk.END, "WSL bash started (Ubuntu).\n", "info")
            self.terminal_text.insert(tk.END, "Interactive Linux shell — apt, pip, etc. work here.\n\n", "output")
            self.current_cmd_start = self.terminal_text.index("end-1c")
            threading.Thread(target=self._wsl_reader, daemon=True).start()
        except Exception as e:
            self.wsl_proc = None
            self.terminal_text.insert(tk.END, f"Failed to start WSL: {e}\n", "error")
            self.terminal_prompt()

    def _wsl_reader(self):
        proc = self.wsl_proc
        if not proc or not proc.stdout:
            return
        try:
            while proc.poll() is None:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                try:
                    text = chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = str(chunk)
                self.ui_call(self._term_append_output, text)
            # Drain remaining
            rest = proc.stdout.read() if proc.stdout else b""
            if rest:
                self.ui_call(self._term_append_output, rest.decode("utf-8", errors="replace"))
            self.ui_call(self._term_append_output, "\n[WSL shell exited]\n")
        except Exception as e:
            self.ui_call(self._term_append_output, f"\n[WSL read error: {e}]\n")

    def _term_append_output(self, text: str):
        # Preserve unfinished user input — WSL output used to move current_cmd_start
        # past typed text, so Enter sent empty lines (terminal looked "broken").
        pending = ""
        try:
            if self.terminal_text.compare(self.current_cmd_start, "<", "end-1c"):
                pending = self.terminal_text.get(self.current_cmd_start, "end-1c")
                self.terminal_text.delete(self.current_cmd_start, "end")
        except Exception:
            pending = ""
        self.terminal_text.insert(tk.END, text, "output")
        self.current_cmd_start = self.terminal_text.index("end-1c")
        if pending:
            self.terminal_text.insert(tk.END, pending)
        self.terminal_text.see(tk.END)

    def terminal_prompt(self):
        """Fallback prompt when WSL is unavailable."""
        cwd = os.getcwd().replace(str(Path.home()), "~")
        self.terminal_text.insert(tk.END, f"\n{cwd}$ ", "prompt")
        self.terminal_text.see(tk.END)
        self.current_cmd_start = self.terminal_text.index("end-1c")

    def terminal_clear(self):
        self.terminal_text.delete("1.0", tk.END)
        self.current_cmd_start = "1.0"
        return "break"

    def terminal_backspace(self, event):
        if self.terminal_text.compare("insert", "<=", self.current_cmd_start):
            return "break"
        return None

    def terminal_interrupt(self, event):
        if self.wsl_proc and self.wsl_proc.stdin and self.wsl_proc.poll() is None:
            try:
                self.wsl_proc.stdin.write(b"\x03")
                self.wsl_proc.stdin.flush()
            except Exception:
                pass
        return "break"

    def terminal_key(self, event):
        if self.terminal_text.compare("insert", "<", self.current_cmd_start):
            self.terminal_text.mark_set("insert", "end")
        if event.keysym == "Up" and self.cmd_history:
            self.cmd_index = self.cmd_index - 1 if self.cmd_index > 0 else len(self.cmd_history) - 1
            self.terminal_text.delete(self.current_cmd_start, "end")
            self.terminal_text.insert("end", self.cmd_history[self.cmd_index])
            return "break"
        if event.keysym == "Down" and self.cmd_history:
            self.cmd_index = (self.cmd_index + 1) % len(self.cmd_history)
            self.terminal_text.delete(self.current_cmd_start, "end")
            self.terminal_text.insert("end", self.cmd_history[self.cmd_index])
            return "break"

    def terminal_execute(self, event):
        line = self.terminal_text.get(self.current_cmd_start, "end-1c").rstrip("\n")
        # Advance insert past typed line
        self.terminal_text.insert(tk.END, "\n")
        self.current_cmd_start = self.terminal_text.index("end-1c")

        stripped = line.strip()
        if stripped:
            self.cmd_history.append(stripped)
            self.cmd_index = len(self.cmd_history)

        if stripped in ("clear", "cls"):
            self.terminal_clear()
            return "break"

        # Live WSL session: pipe the line to bash stdin
        if self.wsl_proc and self.wsl_proc.poll() is None and self.wsl_proc.stdin:
            try:
                self.wsl_proc.stdin.write((line + "\n").encode("utf-8"))
                self.wsl_proc.stdin.flush()
            except Exception as e:
                self.terminal_text.insert(tk.END, f"[WSL write error: {e}]\n", "error")
            return "break"

        # Fallback when WSL missing: one-shot PowerShell / cmd
        if stripped.startswith("win:"):
            stripped = stripped[4:].strip()
        if stripped:
            threading.Thread(target=self._terminal_run_fallback, args=(stripped,), daemon=True).start()
        else:
            self.terminal_prompt()
        return "break"

    def _terminal_run_fallback(self, line):
        """One-shot local shell when WSL is unavailable."""
        try:
            proc = subprocess.run(
                line, shell=True, capture_output=True, text=True, timeout=60, cwd=str(Path.home()),
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if out:
                self.ui_call(self.terminal_text.insert, tk.END, out, "output" if proc.returncode == 0 else "error")
            self.ui_call(self.terminal_prompt)
        except Exception as e:
            self.ui_call(self.terminal_text.insert, tk.END, f"[Error: {e}]\n", "error")
            self.ui_call(self.terminal_prompt)
    
    # ═══════════════════════════════════════════════════════════
    # AI SERVER + SYNC
    # ═══════════════════════════════════════════════════════════
    
    def _mark_ai_ready(self):
        self.server_ready = True
        active = mb_models.get_active_model()
        label = active.get("filename") or "AI"
        self.ui_call(self.top_status.config, {"text": f"● AI Ready ({label})", "fg": AI_COLOR})

    def _poll_server_ready(self):
        """Periodic health check so an already-running llama-server flips the UI online."""
        if self.server_ready:
            self.root.after(10000, self._poll_server_ready)
            return

        def _check():
            if mb_inference.is_ready(timeout=2.0):
                self._mark_ai_ready()
            self.ui_call(lambda: self.root.after(3000, self._poll_server_ready))

        threading.Thread(target=_check, daemon=True).start()

    def start_ai_server(self):
        self.ui_call(self.top_status.config, {"text": "● Starting...", "fg": WARN})
        try:
            cfg = mb_paths.load_config()
            inf = cfg.get("inference") or {}
            model_path = mb_paths.active_model_path(cfg)
            llama_bin = mb_paths.resolve_llama_server()
            # External server already up (common when llama-server was started outside the app).
            if mb_inference.is_ready(timeout=2.0):
                self._mark_ai_ready()
                return
            ok = mb_inference.start_server()
            if ok or mb_inference.is_ready(timeout=2.0):
                self._mark_ai_ready()
            else:
                self.server_ready = False
                self.ui_call(self.top_status.config, {"text": "● AI Offline", "fg": "#ff4444"})
                self.set_bottom("llama-server not reachable — check Start AI or run it on the config URL.")
        except FileNotFoundError as e:
            # If the binary/model path is wrong but something is already serving, still go online.
            if mb_inference.is_ready(timeout=2.0):
                self._mark_ai_ready()
                return
            self.server_ready = False
            msg = str(e)
            label = "● No Model" if "Model not found" in msg else "● AI Offline"
            self.ui_call(self.top_status.config, {"text": label, "fg": "#ff4444"})
            self.set_bottom(msg)
        except Exception as e:
            if mb_inference.is_ready(timeout=2.0):
                self._mark_ai_ready()
                return
            self.server_ready = False
            self.ui_call(self.top_status.config, {"text": "● Error", "fg": "#ff4444"})
            self.set_bottom(f"AI start error: {e}")

    def refresh_sync_status(self):
        cfg = mb_paths.load_config()
        url = mb_paths.sync_server_url(cfg)
        sync_cfg = cfg.get("sync") or {}
        try:
            client = mb_sync.SyncClient(timeout=5.0)
            health = client.health()
            msg = f"● Sync OK ({url})"
            color = AI_COLOR
            if isinstance(health, dict) and health.get("status"):
                msg = f"● Sync {health.get('status')} ({url})"
            detail = json.dumps(health, indent=2) if isinstance(health, dict) else str(health)
        except Exception as e:
            msg = f"● Sync offline ({url})"
            color = "#ff4444"
            detail = str(e)
        self.sync_status_text = msg
        self.ui_call(self.sync_status.config, {"text": msg, "fg": color})

        def _log():
            if hasattr(self, "vault_sync_log"):
                self.vault_sync_log.insert(tk.END, f"\n{msg}\n{detail}\n")
                self.vault_sync_log.see(tk.END)

        self.ui_queue.put(_log)

    def run_sync_now(self):
        self.ui_call(self.sync_status.config, {"text": "● Syncing...", "fg": WARN})
        self.set_bottom("Sync in progress...")
        try:
            result = mb_sync.SyncClient().sync_all()
            pull = result.get("pull") or {}
            push = result.get("push") or {}
            pulled = pull.get("count", len(pull.get("pulled") or []))
            pushed = push.get("count", len(push.get("pushed") or push.get("written") or []))
            if not isinstance(pushed, int):
                pushed = 0
            conflicts = len(result.get("conflicts") or [])
            msg = f"● Sync done (↓{pulled} ↑{pushed}"
            if conflicts:
                msg += f" !{conflicts}"
            msg += ")"
            self.ui_call(self.sync_status.config, {"text": msg, "fg": AI_COLOR})
            self.set_bottom(f"Sync complete: pulled={pulled} pushed={pushed} conflicts={conflicts}")

            def _log():
                if hasattr(self, "vault_sync_log"):
                    self.vault_sync_log.insert(
                        tk.END, f"\n{msg}\n{json.dumps(result, indent=2, default=str)}\n"
                    )
                    self.vault_sync_log.see(tk.END)

            self.ui_queue.put(_log)
        except Exception as e:
            self.ui_call(self.sync_status.config, {"text": "● Sync failed", "fg": "#ff4444"})
            self.set_bottom(f"Sync error: {e}")

            def _err():
                if hasattr(self, "vault_sync_log"):
                    self.vault_sync_log.insert(tk.END, f"\nSync error: {e}\n")
                    self.vault_sync_log.see(tk.END)

            self.ui_queue.put(_err)    
    # ═══════════════════════════════════════════════════════════
    # AI CHAT (optimized)
    # ═══════════════════════════════════════════════════════════
    
    def show_chat(self):
        self.clear_work(); self.set_bottom("AI Chat")
        
        toolbar = tk.Frame(self.work_frame, bg=PANEL_BG, height=35); toolbar.pack(fill=tk.X, padx=5, pady=(5,0))
        title = self.current_chat_name or "New Chat"
        tk.Label(toolbar, text=f"💬 {title}", fg=AI_COLOR, bg=PANEL_BG, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=6)
        tk.Button(toolbar, text="📸 Photo", command=self.attach_photo, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="🧹 Clear", command=self.clear_context, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="∑ Math", command=self.chat_toggle_math, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="📋 Copy", command=self.copy_last_response, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="⭐ Mark good for training", command=self.mark_good_for_training, bg="#5a4a2a", fg=TEXT_COLOR,
                 font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=3)
        
        self.chat_display = scrolledtext.ScrolledText(
            self.work_frame, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
            font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=10, state=tk.DISABLED)
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5,0))
        for tag, color in [("user", USER_COLOR), ("ai", AI_COLOR), ("system", DIM)]:
            self.chat_display.tag_config(tag, foreground=color, font=("Segoe UI", 10, "bold" if tag != "system" else "italic"))
        
        input_frame = tk.Frame(self.work_frame, bg=BG); input_frame.pack(fill=tk.X, padx=5, pady=5)
        self.chat_input = tk.Text(input_frame, height=3, bg=INPUT_BG, fg=TEXT_COLOR, font=("Segoe UI", 10),
                                  relief=tk.FLAT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_input.bind("<Return>", lambda e: self.chat_send() or "break" if not e.state & 0x1 else None)
        
        tk.Button(input_frame, text="Send", command=self.chat_send, bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 10, "bold"), relief=tk.FLAT, padx=20, pady=5, cursor="hand2").pack(side=tk.RIGHT, padx=(5,0))
        
        if self.chat_context:
            for turn in self.chat_context:
                if turn.get("user"):
                    self.chat_add("you", turn["user"])
                if turn.get("ai"):
                    self.chat_add("ai", turn["ai"])
        else:
            self.chat_add("system", "AI ready." if self.server_ready else "AI loading...")
        self.chat_input.focus_set()

    def refresh_chat_list(self):
        if not hasattr(self, "chat_listbox"):
            return
        self.chat_listbox.delete(0, tk.END)
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(CHATS_DIR.glob("*.json"), reverse=True):
            self.chat_listbox.insert(tk.END, f.stem)

    def new_chat(self):
        name = simpledialog.askstring("New Chat", "Chat name:") or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_chat_name = name
        self.current_chat_file = CHATS_DIR / f"{name}.json"
        self.chat_context = []
        self.photo_path = None
        if not self.current_chat_file.exists():
            self.current_chat_file.write_text("[]", encoding="utf-8")
        self.refresh_chat_list()
        self.show_chat()

    def load_chat(self, event=None):
        sel = self.chat_listbox.curselection()
        if not sel:
            return
        name = self.chat_listbox.get(sel[0])
        self.current_chat_name = name
        self.current_chat_file = CHATS_DIR / f"{name}.json"
        try:
            self.chat_context = json.loads(self.current_chat_file.read_text(encoding="utf-8"))
            if not isinstance(self.chat_context, list):
                self.chat_context = []
        except Exception:
            self.chat_context = []
        self.show_chat()
        self.set_bottom(f"Loaded chat: {name}")

    def save_chat(self, user_text, ai_text, photo=None):
        if not self.current_chat_file:
            name = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_chat_name = name
            self.current_chat_file = CHATS_DIR / f"{name}.json"
            self.chat_context = []
        try:
            history = []
            if self.current_chat_file.exists():
                try:
                    history = json.loads(self.current_chat_file.read_text(encoding="utf-8"))
                except Exception:
                    history = []
            if not isinstance(history, list):
                history = []
            history.append({
                "timestamp": datetime.now().isoformat(),
                "user": user_text,
                "ai": ai_text,
                "photo": photo,
            })
            CHATS_DIR.mkdir(parents=True, exist_ok=True)
            self.current_chat_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
            self.ui_call(self.refresh_chat_list)
        except Exception:
            pass
    
    def attach_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")])
        if path: self.photo_path = path; self.chat_add("system", f"📸 Attached: {Path(path).name}")
    
    def clear_context(self):
        self.chat_context = []; self.photo_path = None; self.chat_add("system", "Context cleared.")
    
    def copy_last_response(self):
        if self.last_ai_response: self.root.clipboard_append(self.last_ai_response)
    
    def chat_add(self, sender, text):
        self.chat_display.configure(state=tk.NORMAL)
        if self.chat_display.get("1.0", tk.END).strip(): self.chat_display.insert(tk.END, "\n")
        label = {"you":"You","ai":"AI","system":"System"}.get(sender, sender)
        self.chat_display.insert(tk.END, f"{label}\n", sender)
        self.chat_display.insert(tk.END, text + "\n")
        self.chat_display.see(tk.END)
        self.chat_display.configure(state=tk.DISABLED)
    
    def chat_send(self):
        if not self.server_ready: return
        text = self.chat_input.get("1.0", tk.END).strip()
        if not text: return
        self.chat_input.delete("1.0", tk.END)
        self.chat_add("you", text)
        threading.Thread(target=self.chat_get_response, args=(text,), daemon=True).start()
    
    def chat_get_response(self, text):
        try:
            media_note = ""
            if self.photo_path:
                media_note = f"[Attached image: {Path(self.photo_path).name}]"
            cfg = mb_paths.load_config()
            # Match web companion: tools off unless explicitly enabled.
            allow_tools = bool((cfg.get("web") or {}).get("allow_tools", False))
            prompt = mb_context.build_chat_prompt(
                text,
                project_id=self.current_project,
                history=self.chat_context,
                history_limit=4,
                media_note=media_note,
                include_tools=allow_tools,
            )

            def _complete(p: str) -> str:
                return mb_inference.complete(p, n_predict=512, cfg=cfg)

            if allow_tools:
                ai = mb_tools.run_with_tools(prompt, _complete, max_rounds=1)
                ai = mb_tools.extract_final_text(ai) or (ai or "").strip()
            else:
                ai = (_complete(prompt) or "").strip()
            self.last_ai_response = ai
            self.chat_context.append({"user": text, "ai": ai})
            self.ui_call(self.chat_add, "ai", ai or "(empty)")
            self.save_chat(text, ai, Path(self.photo_path).name if self.photo_path else None)
            mb_flywheel.log_turn(text, ai, self.current_project)
            if self.math_mode:
                self.ui_call(self.render_math, ai)
        except Exception as e:
            self.ui_call(self.chat_add, "system", f"Error: {e}")

    def mark_good_for_training(self):
        try:
            ids = mb_flywheel.mark_good()
            if ids:
                self.chat_add("system", f"Marked good for training (ids: {', '.join(map(str, ids))}).")
            else:
                self.chat_add("system", "Nothing to mark — send a chat turn first.")
        except Exception as e:
            self.chat_add("system", f"Mark failed: {e}")
    
    def chat_toggle_math(self):
        self.math_mode = not self.math_mode
        if self.math_mode and self.last_ai_response: self.render_math(self.last_ai_response)
    
    def render_math(self, text):
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<script>MathJax={{tex:{{inlineMath:[['$','$']]}}}};</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>body{{background:#1e1e1e;color:#e0e0e0;font-family:Segoe UI;padding:40px;max-width:850px;margin:auto;line-height:1.9;font-size:16px;}}</style>
</head><body><h1 style="color:#50fa7b">🧠 Math Render</h1>{text.replace(chr(10),'<br>')}</body></html>"""
        p = "/tmp/motherbrain_math.html"
        with open(p,'w') as f: f.write(html)
        webbrowser.open(f"file://{p}")
    
    # ═══════════════════════════════════════════════════════════
    # PHOTO ANALYZER
    # ═══════════════════════════════════════════════════════════
    
    def show_photo_analyzer(self):
        self.clear_work(); self.set_bottom("Photo Analyzer")
        tk.Label(self.work_frame, text="📸 Photo Analyzer", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        
        img_frame = tk.Frame(self.work_frame, bg=CHAT_BG, padx=10, pady=10, highlightthickness=1, highlightbackground=BORDER)
        img_frame.pack(fill=tk.X, padx=20, pady=10)
        
        if self.photo_path and Path(self.photo_path).exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(self.photo_path); img.thumbnail((400, 300), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                lbl = tk.Label(img_frame, image=photo, bg=CHAT_BG); lbl.image = photo; lbl.pack()
                tk.Label(img_frame, text=f"{Path(self.photo_path).name} ({Path(self.photo_path).stat().st_size/1024:.1f} KB)",
                        fg=DIM, bg=CHAT_BG, font=("Segoe UI", 8)).pack()
            except: tk.Label(img_frame, text="Install Pillow: pip install Pillow", fg=WARN, bg=CHAT_BG).pack()
        else:
            tk.Label(img_frame, text="No image. Use 'Attach Photo' in Chat or Load below.", fg=DIM, bg=CHAT_BG).pack(pady=20)
        
        btn_f = tk.Frame(self.work_frame, bg=BG); btn_f.pack(fill=tk.X, padx=20, pady=5)
        tk.Button(btn_f, text="📁 Load Image", command=self.attach_photo, bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 10), relief=tk.FLAT, cursor="hand2", padx=15, pady=8).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_f, text="🔍 Analyze", command=self.analyze_photo, bg=AI_COLOR, fg=BG,
                 font=("Segoe UI", 10, "bold"), relief=tk.FLAT, cursor="hand2", padx=15, pady=8).pack(side=tk.LEFT, padx=5)
        
        self.photo_result = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Segoe UI", 10),
                                                       relief=tk.FLAT, padx=15, pady=15, height=15)
        self.photo_result.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
    
    def analyze_photo(self):
        if not self.photo_path or not self.server_ready:
            messagebox.showwarning("Error", "Load image and start AI server."); return
        self.photo_result.delete("1.0", tk.END); self.photo_result.insert(tk.END, "Analyzing...\n")
        threading.Thread(target=self._run_photo_analysis, daemon=True).start()
    
    def _run_photo_analysis(self):
        try:
            prompt = (
                f"User: [Image: {Path(self.photo_path).name}, "
                f"{Path(self.photo_path).stat().st_size/1024:.1f}KB. "
                f"Describe what this image likely contains based on filename and context.]\nAssistant:"
            )
            content = mb_inference.complete(prompt, n_predict=1024)
            self.ui_call(self.photo_result.delete, "1.0", tk.END)
            self.ui_call(self.photo_result.insert, tk.END, f"📸 Analysis:\n\n{content}")
        except Exception as e:
            self.ui_call(self.photo_result.insert, tk.END, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════
    # PROJECT EDITOR (simplified, fast)
    # ═══════════════════════════════════════════════════════════
    
    def show_project_editor(self):
        self.clear_work(); self.set_bottom("Project Editor")
        if not self.current_project:
            tk.Label(self.work_frame, text="Select or create a project.", fg=DIM, bg=BG, font=("Segoe UI", 14)).pack(expand=True); return
        
        proj_dir = PROJECTS_DIR / self.current_project; mpath = proj_dir / "manifest.json"
        manifest = json.load(open(mpath)) if mpath.exists() else {"project":{},"hardware":{"devices":[]},"ai":{"models":[]},"datasets":{"collections":[]}}
        proj = manifest.get("project", {})
        
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        text.insert(tk.END, f"═══ {proj.get('name', self.current_project)} ═══\n\n")
        text.insert(tk.END, f"ID: {self.current_project}\nStatus: {proj.get('status','design')}\n\n")
        text.insert(tk.END, f"Description: {proj.get('description','')}\n\n")
        text.insert(tk.END, f"Tags: {', '.join(proj.get('tags',[]))}\n\n")
        text.insert(tk.END, "─── AI Models ───\n")
        for m in manifest.get("ai",{}).get("models",[]):
            text.insert(tk.END, f"  {m.get('model_id','?')} [{m.get('role','?')}]\n")
        text.insert(tk.END, "\n─── Devices ───\n")
        for d in manifest.get("hardware",{}).get("devices",[]):
            text.insert(tk.END, f"  {d.get('device_id','?')} ({d.get('type','?')}, {d.get('chip','?')})\n")
        text.insert(tk.END, "\n─── Datasets ───\n")
        for d in manifest.get("datasets",{}).get("collections",[]):
            text.insert(tk.END, f"  {d.get('name','?')} [{d.get('source','?')}] {d.get('size',0)} samples\n")
        text.insert(tk.END, "\n─── CAD Files ───\n")
        cad_dir = proj_dir / "cad"; cad_dir.mkdir(exist_ok=True)
        for cf in sorted(cad_dir.glob("*")):
            text.insert(tk.END, f"  📐 {cf.name} ({cf.stat().st_size/1024:.1f} KB)\n")
        
        text.configure(state=tk.DISABLED)
        
        btn_f = tk.Frame(self.work_frame, bg=BG); btn_f.pack(fill=tk.X, padx=10, pady=5)
        tk.Button(btn_f, text="+ CAD", command=lambda: self._add_cad_file(cad_dir), bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=15, pady=5).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="+ Device", command=lambda: self._add_device_dialog(manifest, mpath), bg=ACCENT, fg="white",
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=15, pady=5).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="+ Dataset", command=lambda: self._add_dataset_dialog(manifest, mpath), bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=15, pady=5).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="✏️ Edit Manifest", command=lambda: self._edit_manifest(mpath), bg=AI_COLOR, fg=BG,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=15, pady=5).pack(side=tk.RIGHT, padx=3)
        tk.Button(btn_f, text="🗑️ Delete Project", command=self.delete_project, bg="#5a2a2a", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=15, pady=5).pack(side=tk.RIGHT, padx=3)
    
    def _add_cad_file(self, cad_dir):
        path = filedialog.askopenfilename(filetypes=[("CAD","*.stl *.step *.obj *.f3d *.iges *.dxf"),("All","*.*")])
        if path: shutil.copy(path, cad_dir / Path(path).name); self.show_project_editor()
    
    def _add_device_dialog(self, manifest, mpath):
        did = simpledialog.askstring("Device", "Device ID:")
        if not did: return
        manifest.setdefault("hardware",{}).setdefault("devices",[]).append({
            "device_id": did, "type": simpledialog.askstring("Type","Type:") or "unknown",
            "chip": simpledialog.askstring("Chip","Chip:") or "unknown",
            "communication":{"protocol": simpledialog.askstring("Protocol","Protocol:") or "mqtt"}
        })
        json.dump(manifest, open(mpath,'w'), indent=2); self.show_project_editor()
    
    def _add_dataset_dialog(self, manifest, mpath):
        path = filedialog.askopenfilename(filetypes=[("Data","*.jsonl *.csv *.parquet")])
        if not path: return
        name = Path(path).stem; dest = PROJECTS_DIR / self.current_project / "datasets" / Path(path).name
        dest.parent.mkdir(exist_ok=True); shutil.copy(path, dest)
        samples = sum(1 for _ in open(path)) if path.endswith('.jsonl') else 0
        manifest.setdefault("datasets",{}).setdefault("collections",[]).append({
            "name":name,"source":"imported","format":Path(path).suffix[1:],
            "path":str(dest.relative_to(PROJECTS_DIR/self.current_project)),"size":samples,"tags":[]
        })
        json.dump(manifest, open(mpath,'w'), indent=2)
        messagebox.showinfo("Imported", f"Dataset '{name}' with ~{samples} samples."); self.show_project_editor()
    
    def _edit_manifest(self, mpath):
        subprocess.Popen(["xdg-open", mpath] if sys.platform != "win32" else ["notepad", mpath])
    
    def new_project_dialog(self):
        name = simpledialog.askstring("New", "Project name:") or "Untitled"
        pid = simpledialog.askstring("ID", "Project ID:") or name.lower().replace(" ","_")
        desc = simpledialog.askstring("Desc", "Description:") or ""
        proj_dir = PROJECTS_DIR / pid; proj_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["cad","datasets","models","firmware","sim","logs"]: (proj_dir/sub).mkdir(exist_ok=True)
        manifest = {"manifest_version":"1.0.0","project":{"id":pid,"name":name,"description":desc,
            "created":datetime.now().isoformat(),"updated":datetime.now().isoformat(),"status":"design","tags":[]},
            "hardware":{"devices":[]},"ai":{"models":[],"routing_rules":[]},
            "simulation":{"environments":[]},"datasets":{"collections":[]},
            "logs":{"path":"logs/","rotation":"daily","retention_days":30}}
        json.dump(manifest, open(proj_dir/"manifest.json",'w'), indent=2)
        try:
            mb_vault.upsert_project_from_manifest(manifest, project_path=proj_dir)
            mb_vault.index_project(pid)
        except Exception as e:
            self.set_bottom(f"Project created on disk; SQLite upsert warning: {e}")
        self.refresh_project_list(); self.project_combo.set(f"{name} ({pid})")
        self.current_project = pid; self.current_project_name = f"{name} ({pid})"
        self.show_project_editor()
    
    def delete_project(self):
        if not self.current_project: return
        if messagebox.askyesno("Delete", f"Delete {self.current_project}?"):
            pid = self.current_project
            try:
                mb_vault.delete_project(pid, remove_files=True)
            except Exception as e:
                messagebox.showerror("Delete", f"Failed to delete project: {e}")
                return
            self.current_project = None
            self.current_project_name = "None"
            self.refresh_project_list()
            self.show_dashboard()
    
    # ═══════════════════════════════════════════════════════════
    # TRAINING CONSOLE
    # ═══════════════════════════════════════════════════════════
    
    def show_training_console(self):
        self.clear_work(); self.set_bottom("Training Console")
        tk.Label(self.work_frame, text="🧠 Unsloth Training", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        
        for label, var, default in [
            ("Base Model:", "tm", "gemma-2-9b-it-Q5_K_M"),
            ("Dataset (.jsonl):", "td", ""),
            ("Epochs:", "te", "3"),
            ("Output Name:", "to", "my_adapter"),
        ]:
            f = tk.Frame(self.work_frame, bg=BG); f.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(f, text=label, fg=TEXT_COLOR, bg=BG, font=("Segoe UI", 10), width=18, anchor="w").pack(side=tk.LEFT)
            v = tk.StringVar(value=default); setattr(self, var, v)
            tk.Entry(f, textvariable=v, bg=INPUT_BG, fg=TEXT_COLOR, font=("Segoe UI", 10), relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)
            if var == "td":
                tk.Button(f, text="Browse", command=lambda: self.td.set(filedialog.askopenfilename(filetypes=[("JSONL","*.jsonl")]) or ""),
                         bg=USER_COLOR, fg="white", font=("Segoe UI", 8), relief=tk.FLAT, cursor="hand2").pack(side=tk.LEFT, padx=5)
        
        tk.Button(self.work_frame, text="🚀 START TRAINING", command=self.start_training, bg=AI_COLOR, fg=BG,
                 font=("Segoe UI", 14, "bold"), relief=tk.FLAT, cursor="hand2", padx=35, pady=12).pack(pady=15)
        
        self.train_out = scrolledtext.ScrolledText(self.work_frame, bg="#000", fg="#00ff41", font=("Consolas", 9),
                                                    relief=tk.FLAT, padx=10, pady=10, height=15)
        self.train_out.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0,10))
    
    def start_training(self):
        ds = self.td.get()
        if not ds: messagebox.showwarning("Missing", "Select dataset."); return
        self.train_out.delete("1.0", tk.END); self.train_out.insert(tk.END, f"Training...\n")
        cmd = f"cd ~/motherbrain/shell && source venv/bin/activate && python train.py {self.tm.get()} {ds} {self.to.get()}"
        threading.Thread(target=lambda: self._run_train(cmd), daemon=True).start()
    
    def _run_train(self, cmd):
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Batch UI updates for performance
        buffer = []
        for line in proc.stdout:
            buffer.append(line)
            if len(buffer) >= 5:
                self.ui_call(self.train_out.insert, tk.END, "".join(buffer)); buffer.clear()
                self.ui_call(self.train_out.see, tk.END)
        if buffer: self.ui_call(self.train_out.insert, tk.END, "".join(buffer))
        proc.wait()
    
    # ═══════════════════════════════════════════════════════════
    # DASHBOARD, MODELS, VAULT, DATASETS, HARDWARE, GIT, SETTINGS
    # ═══════════════════════════════════════════════════════════
    
    def show_dashboard(self):
        self.clear_work(); self.set_bottom("Dashboard")
        tk.Label(self.work_frame, text="📊 Dashboard", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        db = self.get_db()
        for title, val, color in [
            ("Projects", db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], AI_COLOR),
            ("Messages", db.execute("SELECT COUNT(*) FROM message_log").fetchone()[0], USER_COLOR),
        ]:
            f = tk.Frame(self.work_frame, bg=CHAT_BG, padx=20, pady=15, highlightthickness=1, highlightbackground=BORDER)
            f.pack(fill=tk.X, padx=20, pady=4)
            tk.Label(f, text=title, fg=DIM, bg=CHAT_BG, font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(f, text=str(val), fg=color, bg=CHAT_BG, font=("Segoe UI", 24, "bold")).pack(anchor="w")
        db.close()
    
    def show_model_manager(self):
        self.clear_work(); self.set_bottom("Models")
        tk.Label(self.work_frame, text="📦 Model Manager", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        if not mb_paths.load_config().get("models", {}).get("onboarding_completed"):
            tk.Label(
                self.work_frame,
                text=(
                    "First run: import a local GGUF or choose a reviewed download. "
                    "Occhialini Engineer and Occhialini Robotics are coming soon; no model is downloaded automatically."
                ),
                fg=WARN, bg=CHAT_BG, font=("Segoe UI", 9), justify=tk.LEFT,
                anchor="w", wraplength=1000, padx=12, pady=9,
            ).pack(fill=tk.X, padx=20, pady=(0, 8))

        active = mb_models.get_active_model()
        active_lbl = tk.Label(
            self.work_frame,
            text=f"Active: {active.get('filename')}  |  mode={active.get('mode')}  |  {active.get('url')}  |  exists={active.get('exists')}",
            fg=TEXT_COLOR, bg=BG, font=("Consolas", 9), anchor="w",
        )
        active_lbl.pack(fill=tk.X, padx=20, pady=(0, 8))

        list_frame = tk.Frame(self.work_frame, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        self.model_listbox = tk.Listbox(
            list_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10),
            selectbackground=USER_COLOR, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER,
        )
        self.model_listbox.pack(fill=tk.BOTH, expand=True)
        self._model_entries = mb_models.list_all_models()
        for m in self._model_entries:
            name = m.get("filename") or m.get("name") or m.get("id") or "?"
            size = m.get("size_bytes") or 0
            size_s = f"{size/(1024*1024):.1f} MB" if size else "?"
            src = m.get("source") or ""
            marker = " ★" if name == active.get("filename") else ""
            self.model_listbox.insert(tk.END, f"{name}  ({size_s})  [{src}]{marker}")

        btn_f = tk.Frame(self.work_frame, bg=BG); btn_f.pack(fill=tk.X, padx=20, pady=10)
        tk.Button(btn_f, text="Activate + Restart", command=self._models_set_active, bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Download Qwen 32B", command=lambda: threading.Thread(target=self._models_download_qwen32b, daemon=True).start(),
                 bg=AI_COLOR, fg=BG, font=("Segoe UI", 9, "bold"), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Cancel Download", command=self._models_cancel_download, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Import GGUF", command=self._models_import_local, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Repair", command=self._models_repair, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Remove", command=self._models_remove, bg="#6a3030", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Refresh", command=self.show_model_manager, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.RIGHT, padx=3)

        self.model_status = tk.Label(self.work_frame, text="", fg=DIM, bg=BG, font=("Consolas", 9), anchor="w")
        self.model_status.pack(fill=tk.X, padx=20, pady=(0, 10))

    def _models_selected_entry(self):
        sel = self.model_listbox.curselection()
        if not sel:
            return None
        return self._model_entries[sel[0]]

    def _models_set_active(self):
        entry = self._models_selected_entry()
        if not entry:
            messagebox.showwarning("Models", "Select a model first."); return
        model_path = entry.get("file_path") or entry.get("path") or entry.get("filename")

        def activate():
            try:
                self.set_bottom("Stopping old server and activating selected model...")
                result = mb_inference.activate_model(model_path, start=True)
                self.set_bottom(f"Active model: {result.get('filename')}")
                self.ui_call(self.show_model_manager)
            except Exception as exc:
                self.ui_call(messagebox.showerror, "Activate model", str(exc))

        threading.Thread(target=activate, daemon=True).start()

    def _models_import_local(self):
        selected = filedialog.askopenfilename(
            title="Import a local GGUF",
            filetypes=[("GGUF models", "*.gguf")],
        )
        if not selected:
            return
        try:
            target = mb_model_download.import_local_gguf(selected)
            self._models_finish_onboarding()
            self.set_bottom(f"Imported {target.name}; activate it when ready.")
            self.show_model_manager()
        except Exception as exc:
            messagebox.showerror("Import model", str(exc))

    def _models_repair(self):
        entry = self._models_selected_entry()
        if not entry or not entry.get("id") or entry.get("source") == "disk":
            messagebox.showinfo("Repair", "This disk-only model has no registry record.")
            return
        try:
            result = mb_model_download.repair_model(entry["id"])
            self.set_bottom(f"Model check: {result['status']}")
            self.show_model_manager()
        except Exception as exc:
            messagebox.showerror("Repair model", str(exc))

    def _models_remove(self):
        entry = self._models_selected_entry()
        if not entry:
            return
        if not messagebox.askyesno("Remove model", "Remove this model and its local GGUF file?"):
            return
        try:
            if entry.get("id") and entry.get("source") != "disk":
                mb_model_download.remove_model(entry["id"], delete_file=True)
            else:
                Path(entry.get("path") or "").unlink()
            self.show_model_manager()
        except Exception as exc:
            messagebox.showerror("Remove model", str(exc))

    def _models_download_qwen32b(self):
        preset = mb_model_catalog.get_curated("qwen-32b")
        self.ui_call(self.model_status.config, {"text": f"Resolving {preset.label} metadata..."})
        self.set_bottom(f"Preparing {preset.label}...")
        try:
            repository = mb_model_catalog.list_gguf_files(preset.repo_id or "")
            selected = next(
                item for item in repository["files"]
                if Path(item["filename"]).name == preset.filename
            )
            self._model_download_job = mb_model_download.DOWNLOADS.start(
                repo_id=preset.repo_id or "",
                filename=selected["filename"],
                revision=repository["revision"],
                expected_size=selected.get("size_bytes"),
                expected_sha256=selected.get("sha256"),
                metadata={
                    "name": preset.label,
                    "quantization": preset.quantization,
                    "license": repository.get("license") or preset.license,
                    "publisher": preset.publisher,
                    "provenance": "curated",
                },
                progress=lambda done, total: self.ui_call(
                    self.model_status.config,
                    {"text": (
                        f"Downloading {done * 100 / total:.1f}%"
                        if total else f"Downloading {done / (1024**2):.1f} MB"
                    )},
                ),
            )
            self._model_download_job.thread.join()
            if self._model_download_job.status != "completed":
                raise RuntimeError(self._model_download_job.error or "Download did not complete")
            self._models_finish_onboarding()
            self.ui_call(self.model_status.config, {"text": f"Ready: {preset.filename}. Select it to activate."})
            self.set_bottom(f"Downloaded {preset.label}")
            self.ui_call(self.show_model_manager)
        except Exception as exc:
            self.ui_call(self.model_status.config, {"text": f"Download error: {exc}"})

    def _models_cancel_download(self):
        job = getattr(self, "_model_download_job", None)
        if job and job.status in {"queued", "downloading"}:
            job.cancel()
            self.set_bottom("Cancelling model download; retry will resume.")

    def _models_finish_onboarding(self):
        cfg = mb_paths.load_config()
        cfg.setdefault("models", {})["onboarding_completed"] = True
        mb_paths.save_config(cfg)
    
    def show_vault_explorer(self):
        self.clear_work(); self.set_bottom("Vault")
        tk.Label(self.work_frame, text="🗄️ Vault", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        text.insert(tk.END, str(VAULT_ROOT) + "\n\n")
        for item in sorted(VAULT_ROOT.rglob("*"))[:200]:
            prefix = "  " * (len(item.relative_to(VAULT_ROOT).parts) - 1)
            text.insert(tk.END, f"{prefix}{'📁' if item.is_dir() else '📄'} {item.name}\n")
        text.configure(state=tk.DISABLED)
    
    def show_dataset_manager(self):
        self.clear_work(); self.set_bottom("Datasets")
        tk.Label(self.work_frame, text="📚 Datasets", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        for f in sorted(DATASETS_DIR.glob("*")):
            if f.is_file():
                samples = sum(1 for _ in open(f)) if f.suffix=='.jsonl' else '?'
                text.insert(tk.END, f"  {f.name} ({f.stat().st_size/1024:.1f} KB, ~{samples} samples)\n")
        text.configure(state=tk.DISABLED)
        tk.Button(self.work_frame, text="Export Training Data", command=self.export_dataset, bg=AI_COLOR, fg=BG,
                 font=("Segoe UI", 10), relief=tk.FLAT, cursor="hand2", padx=20, pady=8).pack(pady=10)
    
    def export_dataset(self):
        db = self.get_db()
        try: pairs = db.execute("SELECT query_text, response_text FROM conversation_pairs").fetchall()
        except: pairs = []
        if not pairs: messagebox.showwarning("None","No data."); db.close(); return
        path = filedialog.asksaveasfilename(defaultextension=".jsonl")
        if path:
            with open(path,'w') as f:
                for q,r in pairs: f.write(json.dumps({"instruction":q,"output":r})+'\n')
            messagebox.showinfo("Done",f"{len(pairs)} pairs exported.")
        db.close()
    
    def show_hardware_config(self):
        self.clear_work(); self.set_bottom("Hardware Config")
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text.insert(tk.END, """HARDWARE CONFIGURATION GUIDE

Microcontrollers: ESP32/ESP32-S3 (WiFi+BLE), RP2040, STM32H7, Arduino Nano
Protocols: MQTT (mosquitto), Serial/UART, BLE, WiFi Socket

MQTT Setup:
  sudo apt install mosquitto
  mosquitto -p 1883

WireGuard for remote access:
  sudo wg show
  sudo cat /etc/wireguard/client.conf

ESP32 Firmware Template: motherbrain/firmware/esp32_template/
""")
        text.configure(state=tk.DISABLED)
    
    def show_vault_sync(self):
        self.clear_work()
        self.set_bottom("Vault Sync")
        tk.Label(
            self.work_frame,
            text="Vault Sync",
            fg=AI_COLOR,
            bg=BG,
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))
        tk.Label(
            self.work_frame,
            text="Pair devices with a two-minute connection key, then sync over LAN or WireGuard.\n"
                 "Ed25519 signatures authenticate peers; WireGuard is still required for file confidentiality.",
            fg=DIM,
            bg=BG,
            font=("Consolas", 9),
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 10))

        cfg = mb_paths.load_config()
        sync_cfg = cfg.get("sync") or {}
        self.vault_sync_vars = {
            "sync_url": tk.StringVar(value=str(sync_cfg.get("server_url", "http://10.0.0.1:8090"))),
            "sync_token": tk.StringVar(value=str(sync_cfg.get("token", ""))),
            "role": tk.StringVar(value=str(cfg.get("role", "laptop"))),
        }
        for label, key, show in [
            ("Server URL", "sync_url", None),
            ("Sync token", "sync_token", "*"),
            ("Role (home/laptop)", "role", None),
        ]:
            f = tk.Frame(self.work_frame, bg=BG)
            f.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(f, text=label + ":", fg=TEXT_COLOR, bg=BG, width=22, anchor="w").pack(side=tk.LEFT)
            e = tk.Entry(
                f,
                textvariable=self.vault_sync_vars[key],
                bg=INPUT_BG,
                fg=TEXT_COLOR,
                relief=tk.FLAT,
                show=show or "",
            )
            e.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if key == "sync_token":
                self._harden_secret_entry(e)

        btn = tk.Frame(self.work_frame, bg=BG)
        btn.pack(anchor="w", padx=20, pady=12)
        tk.Button(
            btn, text="Save", command=self.save_vault_sync,
            bg=AI_COLOR, fg=BG, font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            btn, text="Test",
            command=lambda: threading.Thread(target=self.refresh_sync_status, daemon=True).start(),
            bg="#2a3a5a", fg=TEXT_COLOR, font=("Segoe UI", 10),
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            btn, text="Sync Now",
            command=lambda: threading.Thread(target=self.run_sync_now, daemon=True).start(),
            bg="#2a5a2a", fg=TEXT_COLOR, font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            btn, text="Start Sync Server",
            command=lambda: threading.Thread(target=self.start_sync_server, daemon=True).start(),
            bg="#5a3a2a", fg=TEXT_COLOR, font=("Segoe UI", 10),
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
        ).pack(side=tk.LEFT, padx=4)
        peer_btn = tk.Frame(self.work_frame, bg=BG)
        peer_btn.pack(anchor="w", padx=20, pady=(0, 8))
        for label, command in [
            ("Open Pairing Window", self._peer_open_pairing),
            ("Join Connection Key", self._peer_join_pairing),
            ("Check Pairing", self._peer_check_pairing),
            ("Confirm 8-Digit Code", self._peer_confirm_pairing),
            ("Revoke Peer", self._peer_revoke),
        ]:
            tk.Button(
                peer_btn, text=label, command=command, bg="#35364a", fg=TEXT_COLOR,
                font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=10, pady=6,
            ).pack(side=tk.LEFT, padx=3)

        self.vault_sync_log = scrolledtext.ScrolledText(
            self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 9),
            relief=tk.FLAT, height=14,
        )
        self.vault_sync_log.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 16))
        self.vault_sync_log.insert(
            tk.END,
            "Home PC: Start Sync Server → Open Pairing Window using its LAN/WireGuard URL.\n"
            "Laptop: Join Connection Key. Compare the same 8-digit code on both screens.\n"
            "Both: Confirm the code. Then the laptop can Sync Now.\n"
            "Trusted peer keys stay outside the synchronized vault.\n\n",
        )
        self._peer_log_status()
        threading.Thread(target=self.refresh_sync_status, daemon=True).start()

    def _peer_log(self, message):
        if hasattr(self, "vault_sync_log"):
            self.vault_sync_log.insert(tk.END, f"{message}\n")
            self.vault_sync_log.see(tk.END)

    def _peer_log_status(self):
        try:
            from core.peer_auth import IdentityStore

            store = IdentityStore()
            identity = store.load_or_create_identity()
            peers = store.list_trusted_peers()
            self._peer_log(f"Device: {identity.name} ({identity.device_id[:12]})")
            self._peer_log(
                "Trusted peers: " + (
                    ", ".join(f"{peer.name} ({peer.device_id[:12]})" for peer in peers.values())
                    if peers else "none"
                )
            )
        except Exception as exc:
            self._peer_log(f"Peer identity error: {exc}")

    def _peer_open_pairing(self):
        advertised = self.vault_sync_vars["sync_url"].get().strip()

        def run():
            try:
                context = mb_sync.open_pairing_window(advertised)
                self._pair_context = context
                self.ui_call(
                    self._peer_log,
                    f"\nConnection key (expires in 2 minutes):\n{context['connection_key']}\n",
                )
            except Exception as exc:
                self.ui_call(messagebox.showerror, "Open pairing", str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _peer_join_pairing(self):
        key = simpledialog.askstring("Join device", "Paste the connection key:")
        if not key:
            return

        def run():
            try:
                context = mb_sync.join_pairing_window(key)
                self._pair_context = context
                self.ui_call(self._peer_log, f"\nVerification code: {context['sas']}\nConfirm it on BOTH devices.")
            except Exception as exc:
                self.ui_call(messagebox.showerror, "Join pairing", str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _peer_check_pairing(self):
        context = getattr(self, "_pair_context", None)
        if not context:
            messagebox.showinfo("Pairing", "Open or join a pairing window first.")
            return

        def run():
            try:
                status = mb_sync.pairing_status(context)
                self._pair_context = status
                text = (
                    f"Verification code: {status.get('sas') or 'waiting for other device'} | "
                    f"host={status.get('host_confirmed')} guest={status.get('guest_confirmed')}"
                )
                self.ui_call(self._peer_log, text)
            except Exception as exc:
                self.ui_call(messagebox.showerror, "Pairing status", str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _peer_confirm_pairing(self):
        context = getattr(self, "_pair_context", None)
        if not context or not context.get("sas"):
            messagebox.showinfo("Pairing", "Join/check the pairing window until a code appears.")
            return
        code = simpledialog.askstring("Confirm pairing", "Enter the matching 8-digit code:")
        if not code:
            return

        def run():
            try:
                status = mb_sync.confirm_pairing_window(context, code)
                self._pair_context = status
                message = "Pairing complete and trusted." if status.get("complete") else "Confirmed here; waiting for the other device."
                self.ui_call(self._peer_log, message)
                self.ui_call(self._peer_log_status)
            except Exception as exc:
                self.ui_call(messagebox.showerror, "Confirm pairing", str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _peer_revoke(self):
        try:
            from core.peer_auth import IdentityStore

            store = IdentityStore()
            peers = store.list_trusted_peers()
            if not peers:
                messagebox.showinfo("Revoke peer", "No trusted peers.")
                return
            answer = simpledialog.askstring(
                "Revoke peer",
                "Enter peer ID:\n" + "\n".join(f"{peer.device_id} — {peer.name}" for peer in peers.values()),
            )
            if answer and store.revoke_peer(answer.strip()):
                self._peer_log(f"Revoked peer {answer.strip()}.")
                self._peer_log_status()
        except Exception as exc:
            messagebox.showerror("Revoke peer", str(exc))

    def start_sync_server(self):
        """Launch sync_server.py locally on :8090 (home PC)."""
        try:
            # If already healthy on localhost, done.
            try:
                h = mb_sync.SyncClient(server_url="http://127.0.0.1:8090", timeout=2.0).health()
                msg = f"Sync server already running: {h}"
                self.ui_queue.put(lambda: (
                    self.vault_sync_log.insert(tk.END, f"\n{msg}\n") if hasattr(self, "vault_sync_log") else None
                ))
                self.set_bottom("Sync server already up on :8090")
                return
            except Exception:
                pass

            cfg = mb_paths.load_config()
            sync_cfg = cfg.get("sync") or {}
            result = mb_sync_service.start(
                str(sync_cfg.get("listen_host") or "0.0.0.0"),
                int(sync_cfg.get("port") or 8090),
            )
            # Point local config at this machine when role=home
            if str(cfg.get("role", "")).lower() == "home":
                cfg.setdefault("sync", {})["server_url"] = "http://127.0.0.1:8090"
                mb_paths.save_config(cfg)
                if hasattr(self, "vault_sync_vars"):
                    self.ui_call(self.vault_sync_vars["sync_url"].set, "http://127.0.0.1:8090")

            time.sleep(1.2)
            health = mb_sync.SyncClient(server_url="http://127.0.0.1:8090", timeout=3.0).health()
            self.set_bottom("Sync server started on :8090")
            self.ui_queue.put(lambda: (
                self.vault_sync_log.insert(tk.END, f"\nStarted embedded sync server {result} → {health}\n")
                if hasattr(self, "vault_sync_log") else None
            ))
            self.refresh_sync_status()
        except Exception as e:
            self.set_bottom(f"Sync server start failed: {e}")
            self.ui_queue.put(lambda: (
                self.vault_sync_log.insert(tk.END, f"\nStart Sync Server failed: {e}\n")
                if hasattr(self, "vault_sync_log") else None
            ))

    def save_vault_sync(self):
        cfg = mb_paths.load_config()
        cfg["sync"] = {
            **(cfg.get("sync") or {}),
            "server_url": self.vault_sync_vars["sync_url"].get().strip().rstrip("/"),
            "token": self.vault_sync_vars["sync_token"].get().strip(),
        }
        cfg["role"] = self.vault_sync_vars["role"].get().strip() or "laptop"
        mb_paths.save_config(cfg)
        messagebox.showinfo("Saved", "Sync settings saved.")
        self.set_bottom("Vault sync settings saved.")
        threading.Thread(target=self.refresh_sync_status, daemon=True).start()

    @staticmethod
    def _harden_secret_entry(entry: tk.Entry) -> None:
        """Block paste/copy/cut and context menu on secret fields."""
        def _block(_event=None):
            return "break"

        for seq in (
            "<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>",
            "<Control-c>", "<Control-C>", "<Control-x>", "<Control-X>",
            "<Button-2>", "<Button-3>", "<Control-Insert>", "<Shift-Delete>",
        ):
            entry.bind(seq, _block)

    def show_git_sync(self):
        self.clear_work(); self.set_bottom("Git Sync")
        self.git_out = scrolledtext.ScrolledText(self.work_frame, bg="#000", fg="#00ff41", font=("Consolas", 9), relief=tk.FLAT, padx=10, pady=10)
        self.git_out.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        btn_f = tk.Frame(self.work_frame, bg=BG); btn_f.pack(fill=tk.X, padx=20, pady=5)
        for l, c in [("Status","git status"),("Add All","git add ."),("Commit",None),("Push","git push"),("Log","git log --oneline -10")]:
            tk.Button(btn_f, text=l, command=lambda c=c: self._git(c) if c else self._git_commit(),
                     bg="#444", fg=TEXT_COLOR, font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=2)
        self._git("git status")
    
    def _git(self, cmd):
        self.git_out.delete("1.0", tk.END)
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=str(Path.home()/"motherbrain"))
        self.git_out.insert(tk.END, f"$ {cmd}\n{r.stdout}{r.stderr}")
    
    def _git_commit(self):
        msg = simpledialog.askstring("Commit", "Message:")
        if msg: self._git(f'git add . && git commit -m "{msg}"')
    
    def show_isaac_sim(self):
        self.clear_work()
        self.set_bottom("Isaac Sim")
        tk.Label(
            self.work_frame,
            text="🤖 Isaac Sim Bridge",
            fg=AI_COLOR,
            bg=BG,
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 6))
        tk.Label(
            self.work_frame,
            text="Motherbrain talks to Isaac over TCP JSON (default 127.0.0.1:8765).\n"
                 "Start isaac_sim/bridge_server.py inside Isaac, then enable below.",
            fg=DIM,
            bg=BG,
            font=("Consolas", 9),
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        cfg = mb_paths.load_config()
        isaac = cfg.get("isaac_sim") or {}
        self.isaac_vars = {
            "enabled": tk.StringVar(value="true" if isaac.get("enabled") else "false"),
            "host": tk.StringVar(value=str(isaac.get("host", "127.0.0.1"))),
            "port": tk.StringVar(value=str(isaac.get("port", 8765))),
            "transport": tk.StringVar(value=str(isaac.get("transport", "tcp"))),
            "ros_domain_id": tk.StringVar(value=str(isaac.get("ros_domain_id", 0))),
            "default_robot_prim": tk.StringVar(
                value=str(isaac.get("default_robot_prim", "/World/Robot"))
            ),
        }
        for label, key in [
            ("Enabled (true/false)", "enabled"),
            ("Bridge host", "host"),
            ("Bridge port", "port"),
            ("Transport (tcp/ros2)", "transport"),
            ("ROS_DOMAIN_ID", "ros_domain_id"),
            ("Default robot prim", "default_robot_prim"),
        ]:
            f = tk.Frame(self.work_frame, bg=BG)
            f.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(f, text=label + ":", fg=TEXT_COLOR, bg=BG, width=28, anchor="w").pack(
                side=tk.LEFT
            )
            tk.Entry(
                f,
                textvariable=self.isaac_vars[key],
                bg=INPUT_BG,
                fg=TEXT_COLOR,
                relief=tk.FLAT,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        btn_row = tk.Frame(self.work_frame, bg=BG)
        btn_row.pack(anchor="w", padx=20, pady=12)
        tk.Button(
            btn_row,
            text="Save",
            command=self.save_isaac_settings,
            bg=AI_COLOR,
            fg=BG,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
            padx=18,
            pady=8,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Test Connection",
            command=lambda: threading.Thread(target=self.test_isaac_connection, daemon=True).start(),
            bg="#2a3a5a",
            fg=TEXT_COLOR,
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
            padx=18,
            pady=8,
        ).pack(side=tk.LEFT, padx=(0, 8))
        for label, cmd in [
            ("Play", lambda: self._isaac_action("play")),
            ("Pause", lambda: self._isaac_action("pause")),
            ("Reset", lambda: self._isaac_action("reset")),
        ]:
            tk.Button(
                btn_row,
                text=label,
                command=cmd,
                bg="#333",
                fg=TEXT_COLOR,
                font=("Consolas", 9),
                relief=tk.FLAT,
                cursor="hand2",
                padx=12,
                pady=8,
            ).pack(side=tk.LEFT, padx=4)

        self.isaac_status_box = scrolledtext.ScrolledText(
            self.work_frame,
            bg=CHAT_BG,
            fg=TEXT_COLOR,
            font=("Consolas", 9),
            relief=tk.FLAT,
            height=14,
        )
        self.isaac_status_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 16))
        threading.Thread(target=self.test_isaac_connection, daemon=True).start()

    def save_isaac_settings(self):
        cfg = mb_paths.load_config()
        try:
            port = int(self.isaac_vars["port"].get().strip() or "8765")
            domain = int(self.isaac_vars["ros_domain_id"].get().strip() or "0")
        except ValueError:
            messagebox.showerror("Isaac Sim", "port and ros_domain_id must be integers.")
            return
        enabled = self.isaac_vars["enabled"].get().strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        cfg["isaac_sim"] = {
            **(cfg.get("isaac_sim") or {}),
            "enabled": enabled,
            "host": self.isaac_vars["host"].get().strip() or "127.0.0.1",
            "port": port,
            "timeout": float((cfg.get("isaac_sim") or {}).get("timeout", 3.0) or 3.0),
            "transport": self.isaac_vars["transport"].get().strip() or "tcp",
            "ros_domain_id": domain,
            "default_robot_prim": self.isaac_vars["default_robot_prim"].get().strip()
            or "/World/Robot",
        }
        mb_paths.save_config(cfg)
        messagebox.showinfo("Saved", "Isaac Sim settings written to ~/.motherbrain/config.json")
        self.set_bottom("Isaac Sim settings saved.")
        threading.Thread(target=self.test_isaac_connection, daemon=True).start()

    def test_isaac_connection(self):
        status = mb_isaac.ping()
        scene = mb_isaac.get_scene_summary() if status.connected else {"ok": False}
        text = (
            f"{mb_isaac.describe_for_prompt()}\n\n"
            f"status = {json.dumps(status.as_dict(), indent=2)}\n\n"
            f"scene  = {json.dumps(scene, indent=2)}\n"
        )

        def _ui():
            if hasattr(self, "isaac_status_box"):
                self.isaac_status_box.delete("1.0", tk.END)
                self.isaac_status_box.insert(tk.END, text)
            self.set_bottom(
                "Isaac Sim online" if status.connected else f"Isaac Sim offline — {status.detail}"
            )

        self.ui_queue.put(_ui)

    def _isaac_action(self, action: str):
        def _run():
            fn = {"play": mb_isaac.play, "pause": mb_isaac.pause, "reset": mb_isaac.reset}.get(
                action
            )
            result = fn() if fn else {"ok": False, "error": "unknown action"}
            self.ui_queue.put(
                lambda: (
                    self.isaac_status_box.insert(tk.END, f"\n{action} → {json.dumps(result)}\n")
                    if hasattr(self, "isaac_status_box")
                    else None
                )
            )
            self.test_isaac_connection()

        threading.Thread(target=_run, daemon=True).start()

    def show_settings(self):
        self.clear_work(); self.set_bottom("Settings")
        tk.Label(self.work_frame, text="⚙️ Settings", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        cfg = mb_paths.load_config()
        inf = cfg.get("inference") or {}
        sync_cfg = cfg.get("sync") or {}
        isaac = cfg.get("isaac_sim") or {}

        self.settings_vars = {
            "mode": tk.StringVar(value=str(inf.get("mode", "local"))),
            "url": tk.StringVar(value=str(inf.get("url", "http://127.0.0.1:8081"))),
            "model": tk.StringVar(value=str(inf.get("model", ""))),
            "ngl": tk.StringVar(value=str(inf.get("ngl", 28))),
            "ctx": tk.StringVar(value=str(inf.get("ctx", 2048))),
            "sync_url": tk.StringVar(value=str(sync_cfg.get("server_url", ""))),
            "sync_token": tk.StringVar(value=str(sync_cfg.get("token", ""))),
            "role": tk.StringVar(value=str(cfg.get("role", "laptop"))),
            "isaac_enabled": tk.StringVar(value="true" if isaac.get("enabled") else "false"),
            "isaac_host": tk.StringVar(value=str(isaac.get("host", "127.0.0.1"))),
            "isaac_port": tk.StringVar(value=str(isaac.get("port", 8765))),
        }
        fields = [
            ("Inference mode (local/remote)", "mode"),
            ("Inference URL", "url"),
            ("Active model (filename)", "model"),
            ("GPU layers (ngl)", "ngl"),
            ("Context size", "ctx"),
            ("Sync server URL", "sync_url"),
            ("Sync token", "sync_token"),
            ("Role (home/laptop)", "role"),
            ("Isaac Sim enabled (true/false)", "isaac_enabled"),
            ("Isaac bridge host", "isaac_host"),
            ("Isaac bridge port", "isaac_port"),
        ]
        for label, key in fields:
            f = tk.Frame(self.work_frame, bg=BG); f.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(f, text=label + ":", fg=TEXT_COLOR, bg=BG, width=28, anchor="w").pack(side=tk.LEFT)
            show = "*" if key in ("sync_token",) else ""
            e = tk.Entry(
                f, textvariable=self.settings_vars[key], bg=INPUT_BG, fg=TEXT_COLOR,
                relief=tk.FLAT, show=show,
            )
            e.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if key == "sync_token":
                self._harden_secret_entry(e)
        tk.Button(self.work_frame, text="Save", command=self.save_settings,
                 bg=AI_COLOR, fg=BG, font=("Segoe UI", 11, "bold"), relief=tk.FLAT, cursor="hand2", padx=25, pady=10).pack(pady=15)

    def save_settings(self):
        cfg = mb_paths.load_config()
        try:
            ngl = int(self.settings_vars["ngl"].get().strip() or "28")
            ctx = int(self.settings_vars["ctx"].get().strip() or "2048")
            isaac_port = int(self.settings_vars["isaac_port"].get().strip() or "8765")
        except ValueError:
            messagebox.showerror("Settings", "ngl, ctx, and isaac port must be integers."); return
        cfg["inference"] = {
            **(cfg.get("inference") or {}),
            "mode": self.settings_vars["mode"].get().strip() or "local",
            "url": self.settings_vars["url"].get().strip().rstrip("/"),
            "model": self.settings_vars["model"].get().strip(),
            "ngl": ngl,
            "ctx": ctx,
            "parallel": int((cfg.get("inference") or {}).get("parallel", 1) or 1),
            "timeout": int((cfg.get("inference") or {}).get("timeout", 300) or 300),
        }
        cfg["sync"] = {
            **(cfg.get("sync") or {}),
            "server_url": self.settings_vars["sync_url"].get().strip().rstrip("/"),
            "token": self.settings_vars["sync_token"].get(),
        }
        enabled = self.settings_vars["isaac_enabled"].get().strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        cfg["isaac_sim"] = {
            **(cfg.get("isaac_sim") or {}),
            "enabled": enabled,
            "host": self.settings_vars["isaac_host"].get().strip() or "127.0.0.1",
            "port": isaac_port,
        }
        cfg["role"] = self.settings_vars["role"].get().strip() or "laptop"
        mb_paths.save_config(cfg)
        messagebox.showinfo("Saved", "Config written to ~/.motherbrain/config.json")
        self.set_bottom("Settings saved.")
        threading.Thread(target=self.refresh_sync_status, daemon=True).start()
    
    def on_close(self):
        if self._after_id: self.root.after_cancel(self._after_id)
        try:
            if self.wsl_proc and self.wsl_proc.poll() is None:
                self.wsl_proc.terminate()
        except Exception:
            pass
        try:
            mb_inference.stop_server()
        except Exception:
            pass
        try:
            mb_sync_service.stop()
        except Exception:
            pass
        self.root.destroy()


def _pin_gate(parent: tk.Tk) -> bool:
    """Modal unlock screen. Masked digits, no paste/copy, no hints. Max 5 tries."""
    ok = {"value": False}
    tries = {"n": 0}
    gate = tk.Toplevel(parent)
    gate.title("Motherbrain")
    gate.configure(bg=HEADER_BG)
    gate.resizable(False, False)
    gate.attributes("-topmost", True)
    gate.grab_set()
    gate.focus_force()
    w, h = 360, 200
    gate.update_idletasks()
    x = (gate.winfo_screenwidth() - w) // 2
    y = (gate.winfo_screenheight() - h) // 2
    gate.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(
        gate, text="MOTHERBRAIN", fg=AI_COLOR, bg=HEADER_BG,
        font=("Consolas", 14, "bold"),
    ).pack(pady=(28, 8))
    tk.Label(gate, text="Enter PIN", fg=DIM, bg=HEADER_BG, font=("Consolas", 9)).pack()

    pin_var = tk.StringVar()
    entry = tk.Entry(
        gate, textvariable=pin_var, show="*", bg=INPUT_BG, fg=TEXT_COLOR,
        insertbackground=TEXT_COLOR, relief=tk.FLAT, font=("Consolas", 16),
        justify="center", width=12,
    )
    entry.pack(pady=12, ipady=6)

    status = tk.Label(gate, text="", fg=WARN, bg=HEADER_BG, font=("Consolas", 8))
    status.pack()

    def _block(_e=None):
        return "break"

    for seq in (
        "<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>",
        "<Control-c>", "<Control-C>", "<Control-x>", "<Control-X>",
        "<Button-2>", "<Button-3>", "<Control-Insert>", "<Shift-Delete>",
        "<Control-a>", "<Control-A>",
    ):
        entry.bind(seq, _block)

    def _filter_keys(event):
        if event.keysym in ("BackSpace", "Delete", "Return", "Tab", "Escape"):
            return None
        if event.char and event.char.isdigit():
            return None
        if event.char:
            return "break"
        return None

    entry.bind("<Key>", _filter_keys)

    def _submit(_e=None):
        pin = pin_var.get()
        pin_var.set("")
        if mb_auth.verify_pin(pin):
            ok["value"] = True
            gate.destroy()
            return
        tries["n"] += 1
        left = 5 - tries["n"]
        if left <= 0:
            gate.destroy()
            return
        status.config(text=f"Denied ({left} left)")

    entry.bind("<Return>", _submit)
    tk.Button(
        gate, text="Unlock", command=_submit, bg="#2a5a2a", fg=TEXT_COLOR,
        font=("Consolas", 10), relief=tk.FLAT, cursor="hand2", padx=20, pady=6,
    ).pack(pady=8)

    def _on_close():
        gate.destroy()

    gate.protocol("WM_DELETE_WINDOW", _on_close)
    entry.focus_set()
    parent.wait_window(gate)
    return bool(ok["value"])


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    skip_pin = os.environ.get("MOTHERBRAIN_SKIP_PIN", "").strip() in ("1", "true", "yes")
    if not skip_pin and not _pin_gate(root):
        root.destroy()
        raise SystemExit(1)
    root.deiconify()
    app = Workstation(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    if os.environ.get("MOTHERBRAIN_SMOKE", "").strip() in ("1", "true", "yes"):
        root.after(1500, app.on_close)
    root.mainloop()
