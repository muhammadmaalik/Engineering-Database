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
from core import sync as mb_sync
from core import vault_index as mb_vault
from core import flywheel as mb_flywheel

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
            ("📚  Dataset Manager", self.show_dataset_manager),
            ("🔄  Git Sync", self.show_git_sync),
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
        except: pass
    
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
        except Exception:
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
        self.terminal_text.insert(tk.END, text, "output")
        self.terminal_text.see(tk.END)
        self.current_cmd_start = self.terminal_text.index("end-1c")

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
        try:
            client = mb_sync.SyncClient()
            health = client.health()
            msg = f"● Sync OK ({url})"
            color = AI_COLOR
            if isinstance(health, dict) and health.get("status"):
                msg = f"● Sync {health.get('status')} ({url})"
        except Exception:
            msg = f"● Sync offline ({url})"
            color = "#ff4444"
        self.sync_status_text = msg
        self.ui_call(self.sync_status.config, {"text": msg, "fg": color})

    def run_sync_now(self):
        self.ui_call(self.sync_status.config, {"text": "● Syncing...", "fg": WARN})
        self.set_bottom("Sync in progress...")
        try:
            result = mb_sync.SyncClient().sync_all()
            pulled = result.get("pull", {}).get("count", 0)
            pushed = result.get("push", {}).get("count", len(result.get("push", {}).get("pushed", []) or []))
            conflicts = len(result.get("conflicts") or [])
            msg = f"● Sync done (↓{pulled} ↑{pushed}"
            if conflicts:
                msg += f" !{conflicts}"
            msg += ")"
            self.ui_call(self.sync_status.config, {"text": msg, "fg": AI_COLOR})
            self.set_bottom(f"Sync complete: pulled={pulled} pushed={pushed} conflicts={conflicts}")
        except Exception as e:
            self.ui_call(self.sync_status.config, {"text": "● Sync failed", "fg": "#ff4444"})
            self.set_bottom(f"Sync error: {e}")
    
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
            prompt = mb_context.build_chat_prompt(
                text,
                project_id=self.current_project,
                history=self.chat_context,
                media_note=media_note,
            )
            ai = mb_tools.run_with_tools(prompt, mb_inference.complete)
            ai = mb_tools.extract_final_text(ai) or (ai or "").strip()
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
            (PROJECTS_DIR/self.current_project/"manifest.json").unlink(missing_ok=True)
            self.current_project = None; self.refresh_project_list(); self.show_dashboard()
    
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
        tk.Button(btn_f, text="Set Active", command=self._models_set_active, bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Download / Set Qwen 32B", command=lambda: threading.Thread(target=self._models_download_qwen32b, daemon=True).start(),
                 bg=AI_COLOR, fg=BG, font=("Segoe UI", 9, "bold"), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Apply Gemma 9B preset", command=self._models_apply_gemma, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="⚡ Start AI", command=lambda: threading.Thread(target=self.start_ai_server, daemon=True).start(),
                 bg="#2a5a2a", fg=TEXT_COLOR, font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_f, text="Refresh", command=self.show_model_manager, bg="#444", fg=TEXT_COLOR,
                 font=("Segoe UI", 9), relief=tk.FLAT, cursor="hand2", padx=12, pady=6).pack(side=tk.RIGHT, padx=3)

        self.model_status = tk.Label(self.work_frame, text="", fg=DIM, bg=BG, font=("Consolas", 9), anchor="w")
        self.model_status.pack(fill=tk.X, padx=20, pady=(0, 10))

    def _models_selected_filename(self):
        sel = self.model_listbox.curselection()
        if not sel:
            return None
        entry = self._model_entries[sel[0]]
        return entry.get("filename") or Path(entry.get("file_path") or entry.get("path") or "").name

    def _models_set_active(self):
        name = self._models_selected_filename()
        if not name:
            messagebox.showwarning("Models", "Select a model first."); return
        mb_models.set_active_model(name)
        self.set_bottom(f"Active model: {name}")
        self.show_model_manager()

    def _models_apply_gemma(self):
        try:
            mb_models.apply_preset("gemma-9b")
            self.set_bottom("Applied Gemma 9B preset")
            self.show_model_manager()
        except Exception as e:
            messagebox.showerror("Preset", str(e))

    def _models_download_qwen32b(self):
        preset = mb_models.PRESETS["qwen-32b"]
        self.ui_call(self.model_status.config, {"text": f"Downloading {preset['filename']} from {preset['repo']}..."})
        self.set_bottom(f"Downloading {preset['filename']}...")
        try:
            from huggingface_hub import hf_hub_download, list_repo_files
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            files = list_repo_files(preset["repo"])
            ggufs = [f for f in files if f.endswith(".gguf") and preset["quant"] in f]
            selected = ggufs[0] if ggufs else preset["filename"]
            # Prefer exact filename match if present
            for f in files:
                if f.endswith(preset["filename"]) or Path(f).name == preset["filename"]:
                    selected = f
                    break
            hf_hub_download(
                repo_id=preset["repo"],
                filename=selected,
                local_dir=str(MODELS_DIR),
                local_dir_use_symlinks=False,
            )
            mb_models.apply_preset("qwen-32b")
            self.ui_call(self.model_status.config, {"text": f"Ready: {preset['filename']} set active."})
            self.set_bottom(f"Qwen 32B ready: {preset['filename']}")
            self.ui_call(self.show_model_manager)
        except Exception as e:
            # Still set preset so config points at the expected file
            try:
                mb_models.apply_preset("qwen-32b")
            except Exception:
                pass
            self.ui_call(self.model_status.config, {"text": f"Download/set error: {e}"})
            self.set_bottom(f"Qwen 32B: {e}")
            self.ui_call(self.show_model_manager)
    
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
    
    def show_settings(self):
        self.clear_work(); self.set_bottom("Settings")
        tk.Label(self.work_frame, text="⚙️ Settings", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        cfg = mb_paths.load_config()
        inf = cfg.get("inference") or {}
        sync_cfg = cfg.get("sync") or {}

        self.settings_vars = {
            "mode": tk.StringVar(value=str(inf.get("mode", "local"))),
            "url": tk.StringVar(value=str(inf.get("url", "http://127.0.0.1:8081"))),
            "model": tk.StringVar(value=str(inf.get("model", ""))),
            "ngl": tk.StringVar(value=str(inf.get("ngl", 99))),
            "ctx": tk.StringVar(value=str(inf.get("ctx", 8192))),
            "sync_url": tk.StringVar(value=str(sync_cfg.get("server_url", ""))),
            "sync_token": tk.StringVar(value=str(sync_cfg.get("token", ""))),
            "role": tk.StringVar(value=str(cfg.get("role", "laptop"))),
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
        ]
        for label, key in fields:
            f = tk.Frame(self.work_frame, bg=BG); f.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(f, text=label + ":", fg=TEXT_COLOR, bg=BG, width=28, anchor="w").pack(side=tk.LEFT)
            tk.Entry(f, textvariable=self.settings_vars[key], bg=INPUT_BG, fg=TEXT_COLOR, relief=tk.FLAT).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )
        tk.Button(self.work_frame, text="Save", command=self.save_settings,
                 bg=AI_COLOR, fg=BG, font=("Segoe UI", 11, "bold"), relief=tk.FLAT, cursor="hand2", padx=25, pady=10).pack(pady=15)

    def save_settings(self):
        cfg = mb_paths.load_config()
        try:
            ngl = int(self.settings_vars["ngl"].get().strip() or "99")
            ctx = int(self.settings_vars["ctx"].get().strip() or "8192")
        except ValueError:
            messagebox.showerror("Settings", "ngl and ctx must be integers."); return
        cfg["inference"] = {
            **(cfg.get("inference") or {}),
            "mode": self.settings_vars["mode"].get().strip() or "local",
            "url": self.settings_vars["url"].get().strip().rstrip("/"),
            "model": self.settings_vars["model"].get().strip(),
            "ngl": ngl,
            "ctx": ctx,
        }
        cfg["sync"] = {
            **(cfg.get("sync") or {}),
            "server_url": self.settings_vars["sync_url"].get().strip().rstrip("/"),
            "token": self.settings_vars["sync_token"].get(),
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
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = Workstation(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
