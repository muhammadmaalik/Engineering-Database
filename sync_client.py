#!/usr/bin/env python3
"""
Full Sync Client - Complete Motherbrain platform on laptop.
Syncs vault with home PC; AI runs local 32B or remote home GPU per config.
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, filedialog, simpledialog
import threading, json, sqlite3, time, os, sys
from pathlib import Path
from datetime import datetime
from queue import Queue, Empty
from urllib.parse import urlparse, urlunparse

# Ensure repo root is on path when launched as a script.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core import paths
from core import context as companion_context
from core import inference
from core import flywheel
from core import vault_index
from core.tools import run_with_tools, extract_final_text
from core.sync import SyncClient as VaultSyncClient

# Local vault paths (from shared core)
VAULT_ROOT = paths.VAULT_ROOT
VAULT_DB = paths.VAULT_DB
CHATS_DIR = paths.CHATS_DIR
PROJECTS_DIR = paths.PROJECTS_DIR
MODELS_DIR = paths.MODELS_DIR
DATASETS_DIR = paths.DATASETS_DIR

paths.ensure_dirs()
paths.ensure_config()
vault_index.ensure_tables()

# Fallback remote AI URL if config has never stored one
DEFAULT_REMOTE_AI = "http://24.44.30.91:8081"
DEFAULT_LOCAL_AI = "http://127.0.0.1:8081"

# Colors
BG, PANEL_BG, CHAT_BG, SIDEBAR_BG = "#1a1a1a", "#222222", "#282828", "#1e1e1e"
USER_COLOR, AI_COLOR, INPUT_BG, TEXT_COLOR = "#4a9eff", "#50fa7b", "#333333", "#e0e0e0"
ACCENT, DIM, BORDER, HEADER_BG, WARN = "#ff6b6b", "#777777", "#3a3a3a", "#111111", "#ffaa00"


def _port_swap(url: str, port: int) -> str:
    """Replace URL port, preserving scheme/host/path."""
    p = urlparse(url)
    host = p.hostname or "127.0.0.1"
    netloc = f"{host}:{port}"
    if p.username:
        auth = p.username
        if p.password:
            auth += f":{p.password}"
        netloc = f"{auth}@{netloc}"
    return urlunparse((p.scheme or "http", netloc, p.path or "", "", "", "")).rstrip("/")


def inference_mode(cfg=None) -> str:
    cfg = cfg or paths.load_config()
    return str((cfg.get("inference") or {}).get("mode", "local")).lower()


def resolve_remote_ai_url(cfg=None) -> str:
    """Prefer stored remote_url; else sync host on :8081; else default."""
    cfg = cfg or paths.load_config()
    inf = cfg.get("inference") or {}
    remote = (inf.get("remote_url") or "").strip()
    if remote:
        return remote.rstrip("/")
    sync_url = (cfg.get("sync") or {}).get("server_url") or ""
    if sync_url:
        return _port_swap(sync_url, 8081)
    return DEFAULT_REMOTE_AI


def set_inference_mode(mode: str) -> dict:
    """Update config inference.mode and active url (local vs remote)."""
    mode = "remote" if str(mode).lower() == "remote" else "local"
    cfg = paths.load_config()
    inf = dict(cfg.get("inference") or {})
    current_mode = str(inf.get("mode", "local")).lower()
    current_url = str(inf.get("url") or DEFAULT_LOCAL_AI).rstrip("/")

    if current_mode == "remote" and current_url:
        inf["remote_url"] = current_url
    elif not inf.get("remote_url"):
        inf["remote_url"] = resolve_remote_ai_url(cfg)

    inf["mode"] = mode
    if mode == "local":
        inf["url"] = DEFAULT_LOCAL_AI
    else:
        inf["url"] = (inf.get("remote_url") or resolve_remote_ai_url(cfg)).rstrip("/")

    cfg["inference"] = inf
    paths.save_config(cfg)
    return cfg


class SyncClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Hello Engineer - Remote")
        self.root.geometry("1100x750")
        self.root.configure(bg=BG)
        self.root.minsize(800, 500)

        self.server_connected = False
        self.last_ai_response = ""
        self.chat_context = []
        self.photo_path = None
        self.current_project = None
        self.current_chat_name = None
        self.current_chat_file = None
        self.ui_queue = Queue()
        self.mode_btn = None

        self._poll_ui_queue()

        # ─── TOP BAR ──────────────────────────────────────────
        topbar = tk.Frame(root, bg=HEADER_BG, height=44)
        topbar.pack(fill=tk.X, side=tk.TOP); topbar.pack_propagate(False)
        tk.Label(topbar, text="🧠", font=("Segoe UI", 16), bg=HEADER_BG).pack(side=tk.LEFT, padx=10)
        tk.Label(topbar, text="HELLO ENGINEER", fg=AI_COLOR, bg=HEADER_BG,
                font=("Consolas", 13, "bold")).pack(side=tk.LEFT, pady=8)

        self.mode_btn = tk.Button(
            topbar, text=self._mode_label(), command=self.toggle_inference_mode,
            bg="#333", fg=TEXT_COLOR, font=("Consolas", 8), relief=tk.FLAT,
            cursor="hand2", padx=8,
        )
        self.mode_btn.pack(side=tk.RIGHT, padx=4)

        self.conn_label = tk.Label(topbar, text="● Connecting...", fg=WARN, bg=HEADER_BG, font=("Consolas", 9))
        self.conn_label.pack(side=tk.RIGHT, padx=10)

        # ─── MAIN LAYOUT ──────────────────────────────────────
        self.main_paned = tk.PanedWindow(root, orient=tk.HORIZONTAL, bg=BORDER, sashwidth=3)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # ─── SIDEBAR ──────────────────────────────────────────
        sidebar = tk.Frame(self.main_paned, bg=SIDEBAR_BG, width=195)
        self.main_paned.add(sidebar)

        tk.Label(sidebar, text="◆ NAVIGATION", fg=DIM, bg=SIDEBAR_BG, font=("Consolas", 8, "bold")).pack(pady=(12,6), padx=8, anchor="w")
        for text, cmd in [
            ("💬  AI Chat", self.show_chat),
            ("📁  Projects", self.show_projects),
            ("📊  Dashboard", self.show_dashboard),
            ("🗄️  Vault", self.show_vault),
            ("📸  Photos", self.show_photos),
            ("🔄  Sync Now", self.sync_all),
        ]:
            btn = tk.Button(sidebar, text=text, command=cmd, bg=SIDEBAR_BG, fg="#bbbbbb",
                          font=("Consolas", 9), relief=tk.FLAT, anchor="w", padx=8, pady=5,
                          activebackground="#333", activeforeground=TEXT_COLOR, cursor="hand2")
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#2a2a2a"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=SIDEBAR_BG))

        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill=tk.X, padx=8, pady=8)
        tk.Label(sidebar, text="◆ CHAT HISTORY", fg=DIM, bg=SIDEBAR_BG, font=("Consolas", 8, "bold")).pack(pady=3, padx=8, anchor="w")
        self.chat_listbox = tk.Listbox(sidebar, bg=INPUT_BG, fg=TEXT_COLOR, font=("Consolas", 8),
                                       relief=tk.FLAT, selectbackground=USER_COLOR, height=6)
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
        tk.Button(sidebar, text="+ New Project", command=self.new_project_dialog, bg="#2a5a2a", fg=TEXT_COLOR,
                 font=("Consolas", 8), relief=tk.FLAT, cursor="hand2").pack(fill=tk.X, padx=8, pady=3)

        # ─── WORK AREA ────────────────────────────────────────
        self.work_frame = tk.Frame(self.main_paned, bg=BG)
        self.main_paned.add(self.work_frame)

        # ─── BOTTOM ───────────────────────────────────────────
        self.bottom_bar = tk.Label(root, text="Ready.", fg=DIM, bg=HEADER_BG, font=("Consolas", 8), anchor="w")
        self.bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Start
        threading.Thread(target=self.check_connection, daemon=True).start()
        self.show_chat()

    def _mode_label(self) -> str:
        mode = inference_mode()
        return f"AI: {'LOCAL' if mode == 'local' else 'REMOTE'}"

    def _poll_ui_queue(self):
        try:
            while True: func, args = self.ui_queue.get_nowait(); func(*args)
        except Empty: pass
        self.root.after(50, self._poll_ui_queue)

    def ui_call(self, func, *args): self.ui_queue.put((func, args))
    def get_db(self): return sqlite3.connect(str(VAULT_DB), timeout=5)
    def set_bottom(self, text): self.ui_call(self.bottom_bar.config, {"text": text})
    def clear_work(self):
        for w in self.work_frame.winfo_children(): w.destroy()

    def toggle_inference_mode(self):
        current = inference_mode()
        new_mode = "remote" if current == "local" else "local"
        cfg = set_inference_mode(new_mode)
        url = paths.inference_base_url(cfg)
        if self.mode_btn:
            self.mode_btn.config(text=self._mode_label())
        self.set_bottom(f"Inference → {new_mode} ({url})")
        self.server_connected = False
        self.conn_label.config(text="● Connecting...", fg=WARN)
        threading.Thread(target=self.check_connection, daemon=True).start()

    def check_connection(self):
        cfg = paths.load_config()
        url = paths.inference_base_url(cfg)
        mode = inference_mode(cfg)
        try:
            if inference.is_ready(cfg, timeout=5.0):
                self.server_connected = True
                label = f"● {mode.upper()}"
                self.ui_call(self.conn_label.config, {"text": label, "fg": AI_COLOR})
                self.ui_call(self.set_bottom, f"Connected ({mode}): {url}")
                return
        except Exception:
            pass
        self.server_connected = False
        self.ui_call(self.conn_label.config, {"text": "● Offline", "fg": "#ff4444"})
        self.ui_call(self.set_bottom, f"Cannot reach AI ({mode}): {url}")

    def sync_all(self):
        """Sync vault with home PC."""
        self.set_bottom("Syncing with home PC...")
        threading.Thread(target=self._run_sync, daemon=True).start()

    def _run_sync(self):
        try:
            result = VaultSyncClient().sync_all()
            pulled = result.get("pull", {}).get("count", 0)
            pushed = result.get("push", {}).get("count", 0)
            if isinstance(result.get("push"), dict) and "pushed" in result["push"]:
                pushed = len(result["push"].get("pushed") or []) or result["push"].get("count", pushed)
            conflicts = len(result.get("conflicts") or [])
            self.ui_call(
                self.set_bottom,
                f"Sync complete: pulled {pulled}, pushed {pushed}, conflicts {conflicts}",
            )
            self.ui_call(self.refresh_chat_list)
            self.ui_call(self.refresh_project_list)
        except Exception as e:
            self.ui_call(self.set_bottom, f"Sync error: {e}")

    def refresh_chat_list(self):
        self.chat_listbox.delete(0, tk.END)
        for f in sorted(CHATS_DIR.glob("*.json"), reverse=True):
            self.chat_listbox.insert(tk.END, f.stem)

    def new_chat(self):
        name = simpledialog.askstring("New Chat", "Chat name:") or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_chat_name = name
        self.current_chat_file = CHATS_DIR / f"{name}.json"
        self.chat_context = []
        self.photo_path = None
        if not self.current_chat_file.exists(): self.current_chat_file.write_text("[]")
        self.refresh_chat_list()
        self.show_chat()

    def load_chat(self, event):
        sel = self.chat_listbox.curselection()
        if sel:
            name = self.chat_listbox.get(sel[0])
            self.current_chat_name = name
            self.current_chat_file = CHATS_DIR / f"{name}.json"
            try: self.chat_context = json.loads(self.current_chat_file.read_text())
            except: self.chat_context = []
            self.show_chat()
            for turn in self.chat_context:
                if turn.get("user"): self.chat_add("you", turn["user"])
                if turn.get("ai"): self.chat_add("ai", turn["ai"])
            self.set_bottom(f"Loaded: {name}")

    def save_chat(self, user_text, ai_text, photo=None):
        if not self.current_chat_file: self.new_chat()
        try:
            history = json.loads(self.current_chat_file.read_text()) if self.current_chat_file.exists() else []
            history.append({"timestamp": datetime.now().isoformat(), "user": user_text, "ai": ai_text, "photo": photo})
            self.current_chat_file.write_text(json.dumps(history, indent=2))
        except: pass

    def refresh_project_list(self):
        try:
            vault_index.ensure_tables()
            db = self.get_db()
            projects = db.execute("SELECT id, name FROM projects").fetchall()
            names = ["None"] + [f"{name} ({pid})" for pid, name in projects]
            self.project_combo["values"] = names
            if not self.project_combo.get():
                self.project_combo.set("None")
            db.close()
        except Exception:
            pass

    def on_project_select(self, event):
        val = self.project_combo.get()
        self.current_project = None if val == "None" else val.split("(")[-1].rstrip(")")

    # ─── AI CHAT ──────────────────────────────────────────────
    def show_chat(self):
        self.clear_work()

        header = tk.Frame(self.work_frame, bg=PANEL_BG, height=35); header.pack(fill=tk.X, padx=5, pady=(5,0))
        tk.Label(header, text=f"💬 {self.current_chat_name or 'New Chat'}", fg=AI_COLOR, bg=PANEL_BG,
                font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=8)
        mode = inference_mode()
        tk.Label(header, text=f"[{mode}]", fg=DIM, bg=PANEL_BG, font=("Consolas", 8)).pack(side=tk.RIGHT, padx=8)

        toolbar = tk.Frame(self.work_frame, bg=PANEL_BG, height=30); toolbar.pack(fill=tk.X, padx=5)
        for t, c in [
            ("📸 Photo", self.attach_photo),
            ("🧹 Clear", self.clear_context),
            ("📋 Copy", self.copy_last),
            ("★ Mark good", self.mark_last_good),
        ]:
            tk.Button(toolbar, text=t, command=c, bg="#444", fg=TEXT_COLOR, font=("Segoe UI", 8),
                     relief=tk.FLAT, cursor="hand2", padx=10).pack(side=tk.LEFT, padx=2)

        self.chat_display = scrolledtext.ScrolledText(self.work_frame, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
                font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=10, state=tk.DISABLED)
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)
        for tag, c in [("user", USER_COLOR), ("ai", AI_COLOR), ("system", DIM)]:
            self.chat_display.tag_config(tag, foreground=c, font=("Segoe UI", 10, "bold" if tag != "system" else "italic"))

        input_frame = tk.Frame(self.work_frame, bg=BG); input_frame.pack(fill=tk.X, padx=5, pady=5)
        self.chat_input = tk.Text(input_frame, height=3, bg=INPUT_BG, fg=TEXT_COLOR, font=("Segoe UI", 10),
                relief=tk.FLAT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER,
                insertwidth=3, blockcursor=True)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_input.bind("<Return>", lambda e: self.send_msg() or "break" if not e.state & 0x1 else None)
        tk.Button(input_frame, text="Send", command=self.send_msg, bg=USER_COLOR, fg="white",
                 font=("Segoe UI", 10, "bold"), relief=tk.FLAT, padx=20, pady=5, cursor="hand2").pack(side=tk.RIGHT, padx=(5,0))

        status = f"AI ready ({mode})." if self.server_connected else f"AI offline ({mode})."
        self.chat_add("system", status)
        self.chat_input.focus_set()

    def attach_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Images & Videos", "*.png *.jpg *.jpeg *.gif *.bmp *.mp4 *.mov"), ("All", "*.*")])
        if path: self.photo_path = path; self.chat_add("system", f"📸 {Path(path).name}")

    def clear_context(self): self.chat_context = []; self.photo_path = None; self.chat_add("system", "Cleared.")
    def copy_last(self):
        if self.last_ai_response: self.root.clipboard_append(self.last_ai_response)

    def mark_last_good(self):
        try:
            ids = flywheel.mark_last_pair_good()
            if ids:
                self.chat_add("system", f"Marked good for training: {ids}")
                self.set_bottom(f"Curated message ids: {ids}")
            else:
                self.chat_add("system", "Nothing to mark — send a turn first.")
        except Exception as e:
            messagebox.showerror("Curation", str(e))

    def chat_add(self, sender, text):
        self.chat_display.configure(state=tk.NORMAL)
        if self.chat_display.get("1.0", tk.END).strip(): self.chat_display.insert(tk.END, "\n")
        self.chat_display.insert(tk.END, f"{'You' if sender=='you' else 'AI' if sender=='ai' else 'System'}\n", sender)
        self.chat_display.insert(tk.END, text + "\n")
        self.chat_display.see(tk.END)
        self.chat_display.configure(state=tk.DISABLED)

    def send_msg(self):
        if not self.server_connected:
            messagebox.showwarning("Offline", "Not connected to AI server."); return
        text = self.chat_input.get("1.0", tk.END).strip()
        if not text: return
        self.chat_input.delete("1.0", tk.END)
        self.chat_add("you", text)
        mode = inference_mode()
        self.set_bottom(f"Sending ({mode})...")
        threading.Thread(target=self.get_ai, args=(text,), daemon=True).start()

    def get_ai(self, text):
        try:
            media = ""
            if self.photo_path and Path(self.photo_path).exists():
                f = Path(self.photo_path)
                media = f"\n[Media: {f.name}, {f.stat().st_size/1024:.1f}KB]\n"

            prompt = companion_context.build_chat_prompt(
                text,
                project_id=self.current_project,
                history=self.chat_context,
                history_limit=6,
                media_note=media,
            )

            raw = run_with_tools(prompt, inference.complete)
            ai = extract_final_text(raw)

            self.last_ai_response = ai
            self.chat_context.append({"user": text, "ai": ai})
            self.ui_call(self.chat_add, "ai", ai or "(empty)")
            self.save_chat(text, ai, Path(self.photo_path).name if self.photo_path else None)
            try:
                flywheel.log_turn(text, ai, self.current_project)
            except Exception:
                pass
            self.ui_call(self.set_bottom, f"Response: {len(ai)} chars")
        except Exception as e:
            self.ui_call(self.chat_add, "system", f"Connection error: {e}")
            self.ui_call(self.conn_label.config, {"text": "● Offline", "fg": "#ff4444"})
            self.server_connected = False

    # ─── VIEWS ────────────────────────────────────────────────
    def show_dashboard(self):
        self.clear_work()
        tk.Label(self.work_frame, text="📊 Dashboard", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        db = self.get_db()
        try: projects = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        except: projects = 0
        chats = len(list(CHATS_DIR.glob("*.json")))
        mode = inference_mode()
        url = paths.inference_base_url()
        for t, v, c in [
            ("Projects", projects, AI_COLOR),
            ("Chats Saved", chats, WARN),
            ("Inference", f"{mode} · {'up' if self.server_connected else 'down'}", AI_COLOR if self.server_connected else ACCENT),
            ("AI URL", url, DIM),
        ]:
            f = tk.Frame(self.work_frame, bg=CHAT_BG, padx=20, pady=15, highlightthickness=1, highlightbackground=BORDER)
            f.pack(fill=tk.X, padx=20, pady=4)
            tk.Label(f, text=t, fg=DIM, bg=CHAT_BG).pack(anchor="w")
            tk.Label(f, text=str(v), fg=c, bg=CHAT_BG, font=("Segoe UI", 18 if t == "AI URL" else 24, "bold")).pack(anchor="w")
        db.close()

    def show_projects(self):
        self.clear_work()
        tk.Label(self.work_frame, text="📁 Projects", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        for proj in sorted(PROJECTS_DIR.iterdir()):
            if proj.is_dir():
                mf = proj / "manifest.json"
                if mf.exists():
                    m = json.load(open(mf))
                    text.insert(tk.END, f"▸ {m['project'].get('name', proj.name)} [{m['project'].get('status','?')}]\n")
                    text.insert(tk.END, f"  ID: {m['project']['id']}\n\n")
        text.configure(state=tk.DISABLED)

    def show_vault(self):
        self.clear_work()
        text = scrolledtext.ScrolledText(self.work_frame, bg=CHAT_BG, fg=TEXT_COLOR, font=("Consolas", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        text.insert(tk.END, f"Vault: {VAULT_ROOT}\n\n")
        for item in sorted(VAULT_ROOT.rglob("*"))[:100]:
            text.insert(tk.END, f"{'  '*(len(item.relative_to(VAULT_ROOT).parts)-1)}{'📁' if item.is_dir() else '📄'} {item.name}\n")
        text.configure(state=tk.DISABLED)

    def show_photos(self):
        self.clear_work()
        tk.Label(self.work_frame, text="📸 Photos", fg=AI_COLOR, bg=BG, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20,10))
        if self.photo_path and Path(self.photo_path).exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(self.photo_path); img.thumbnail((400, 300), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img); lbl = tk.Label(self.work_frame, image=photo, bg=BG); lbl.image = photo; lbl.pack()
            except: tk.Label(self.work_frame, text=f"File: {Path(self.photo_path).name}", fg=WARN, bg=BG).pack()
        else: tk.Label(self.work_frame, text="No media loaded.", fg=DIM, bg=BG).pack(pady=20)

    def new_project_dialog(self):
        name = simpledialog.askstring("New", "Project name:") or "Untitled"
        pid = simpledialog.askstring("ID", "Project ID:") or name.lower().replace(" ","_")
        proj_dir = PROJECTS_DIR / pid; proj_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["cad","datasets","models","firmware","sim","logs"]: (proj_dir/sub).mkdir(exist_ok=True)
        manifest = {
            "manifest_version": "1.0.0",
            "project": {
                "id": pid,
                "name": name,
                "description": "",
                "created": datetime.now().isoformat(),
                "updated": datetime.now().isoformat(),
                "status": "design",
                "tags": [],
            },
            "hardware": {"devices": []},
            "ai": {"models": [], "routing_rules": []},
            "simulation": {"environments": []},
            "datasets": {"collections": []},
            "logs": {"path": "logs/", "rotation": "daily", "retention_days": 30},
        }
        mf_path = proj_dir / "manifest.json"
        mf_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        try:
            vault_index.upsert_project_from_manifest(manifest, project_path=proj_dir)
        except Exception as e:
            messagebox.showwarning("Index", f"Project created but DB upsert failed: {e}")
        self.refresh_project_list(); self.show_projects()


if __name__ == "__main__":
    root = tk.Tk()
    app = SyncClient(root)
    root.mainloop()
