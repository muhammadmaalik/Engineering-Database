#!/usr/bin/env python3
"""
Motherbrain - Complete Platform GUI
Chat AI + Vault + Training + Projects in one unified application.
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, filedialog
import threading
import requests
import json
import subprocess
import sqlite3
import time
import webbrowser
import os
import sys
from pathlib import Path
from datetime import datetime

# ─── Paths ───────────────────────────────────────────────────
VAULT_DB = Path.home() / ".motherbrain" / "vault" / "vault_index.db"
VAULT_ROOT = Path.home() / ".motherbrain" / "vault"
MODELS_DIR = VAULT_ROOT / "shared" / "base_models"
ADAPTERS_DIR = VAULT_ROOT / "shared" / "adapters"
DEFAULT_MODEL = MODELS_DIR / "gemma-2-9b-it-Q5_K_M.gguf"
LLAMA_SERVER = Path.home() / "llama.cpp" / "build" / "bin" / "llama-server"
SERVER_URL = "http://127.0.0.1:8081/completion"

# ─── Colors (same as AI GUI) ─────────────────────────────────
BG          = "#1e1e1e"
CHAT_BG     = "#2d2d2d"
SIDEBAR_BG  = "#252525"
USER_COLOR  = "#4a9eff"
AI_COLOR    = "#50fa7b"
INPUT_BG    = "#3c3c3c"
TEXT_COLOR  = "#ffffff"
ACCENT      = "#ff6b6b"
DIM         = "#888888"
BORDER      = "#444444"


class MotherbrainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Motherbrain")
        self.root.geometry("1100x750")
        self.root.configure(bg=BG)
        self.root.minsize(900, 550)
        
        # ─── State ────────────────────────────────────────────
        self.server_process = None
        self.server_ready = False
        self.math_mode = False
        self.last_ai_response = ""
        self.current_project = None
        self.current_project_name = "None"
        
        # ─── Header ───────────────────────────────────────────
        header = tk.Frame(root, bg=SIDEBAR_BG, height=48)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        
        tk.Label(header, text="🧠", font=("Segoe UI", 18), bg=SIDEBAR_BG).pack(side=tk.LEFT, padx=12)
        tk.Label(header, text="Motherbrain", fg=TEXT_COLOR, bg=SIDEBAR_BG,
                font=("Segoe UI", 15, "bold")).pack(side=tk.LEFT, padx=4)
        
        # Project indicator
        self.project_label = tk.Label(header, text="Project: None", fg=DIM, bg=SIDEBAR_BG,
                                      font=("Segoe UI", 9))
        self.project_label.pack(side=tk.RIGHT, padx=15)
        
        self.status_dot = tk.Label(header, text="●", fg="#ffaa00", bg=SIDEBAR_BG, font=("Segoe UI", 10))
        self.status_dot.pack(side=tk.RIGHT, padx=2)
        self.status_text = tk.Label(header, text="Loading...", fg=DIM, bg=SIDEBAR_BG,
                                    font=("Segoe UI", 9))
        self.status_text.pack(side=tk.RIGHT)
        
        # ─── Main Container ───────────────────────────────────
        main = tk.Frame(root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)
        
        # ─── Sidebar ──────────────────────────────────────────
        sidebar = tk.Frame(main, bg=SIDEBAR_BG, width=200, highlightthickness=1, highlightbackground=BORDER)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        
        tk.Label(sidebar, text="NAVIGATION", fg=DIM, bg=SIDEBAR_BG,
                font=("Segoe UI", 8, "bold")).pack(pady=(20,5), padx=15, anchor="w")
        
        self.nav_buttons = {}
        nav_items = [
            ("💬  Chat", self.show_chat),
            ("📊  Dashboard", self.show_dashboard),
            ("📁  Vault", self.show_vault),
            ("🏷️  Training Data", self.show_training),
            ("🤖  Models", self.show_models),
            ("📦  Projects", self.show_projects),
        ]
        
        for text, cmd in nav_items:
            btn = tk.Button(sidebar, text=text, command=cmd,
                          bg=SIDEBAR_BG, fg="#cccccc", font=("Segoe UI", 10),
                          relief=tk.FLAT, anchor="w", padx=15, pady=8,
                          activebackground="#333333", activeforeground=TEXT_COLOR,
                          cursor="hand2")
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#303030"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=SIDEBAR_BG))
            self.nav_buttons[text] = btn
        
        # Separator
        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill=tk.X, padx=10, pady=15)
        
        tk.Label(sidebar, text="QUICK ACTIONS", fg=DIM, bg=SIDEBAR_BG,
                font=("Segoe UI", 8, "bold")).pack(pady=5, padx=15, anchor="w")
        
        quick_actions = [
            ("⚡  Start AI Server", self.start_ai_server),
            ("📤  Export Dataset", self.export_dataset),
            ("🔄  Refresh", self.refresh_all),
        ]
        for text, cmd in quick_actions:
            btn = tk.Button(sidebar, text=text, command=cmd,
                          bg=SIDEBAR_BG, fg="#aaaaaa", font=("Segoe UI", 9),
                          relief=tk.FLAT, anchor="w", padx=15, pady=5,
                          activebackground="#333333", activeforeground=TEXT_COLOR,
                          cursor="hand2")
            btn.pack(fill=tk.X)
        
        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill=tk.X, padx=10, pady=15)
        tk.Label(sidebar, text="ACTIVE PROJECT", fg=DIM, bg=SIDEBAR_BG,
                font=("Segoe UI", 8, "bold")).pack(pady=5, padx=15, anchor="w")
        
        self.project_selector = ttk.Combobox(sidebar, state="readonly", 
                                             font=("Segoe UI", 9))
        self.project_selector.pack(fill=tk.X, padx=12, pady=3)
        self.project_selector.bind("<<ComboboxSelected>>", self.on_project_change)
        self.load_project_list()
        
        # ─── Content Area ─────────────────────────────────────
        self.content_frame = tk.Frame(main, bg=BG)
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Bottom bar
        self.bottom_bar = tk.Label(root, text="Ready.", fg=DIM, bg=SIDEBAR_BG,
                                   font=("Segoe UI", 8), anchor="w", height=1)
        self.bottom_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=0)
        
        # Start AI server thread
        threading.Thread(target=self.init_ai_server, daemon=True).start()
        
        # Show chat by default
        self.show_chat()
        
        # Poll for server ready
        self.root.after(2000, self.check_server)
    
    # ─── UTILITY ──────────────────────────────────────────────
    
    def get_db(self):
        return sqlite3.connect(str(VAULT_DB))
    
    def set_status(self, text, color=DIM):
        self.status_text.config(text=text, fg=color)
    
    def set_bottom(self, text):
        self.bottom_bar.config(text=text)
    
    def load_project_list(self):
        try:
            db = self.get_db()
            projects = db.execute("SELECT id, name FROM projects").fetchall()
            names = ["None"] + [f"{name} ({pid})" for pid, name in projects]
            self.project_selector["values"] = names
            self.project_selector.set("None")
            db.close()
        except:
            self.project_selector["values"] = ["None"]
            self.project_selector.set("None")
    
    def on_project_change(self, event):
        val = self.project_selector.get()
        if val == "None":
            self.current_project = None
            self.current_project_name = "None"
        else:
            self.current_project_name = val
        self.project_label.config(text=f"Project: {self.current_project_name}")
        self.set_bottom(f"Switched to project: {self.current_project_name}")
    
    def clear_content(self):
        for w in self.content_frame.winfo_children():
            w.destroy()
    
    # ─── AI SERVER ────────────────────────────────────────────
    
    def init_ai_server(self):
        self.start_ai_server(silent=True)
    
    def start_ai_server(self, silent=False):
        if not silent:
            self.set_status("Starting server...", "#ffaa00")
            self.set_bottom("Launching AI model...")
        
        model = DEFAULT_MODEL
        if not model.exists():
            if not silent:
                messagebox.showerror("Error", f"Model not found: {model}")
            return
        
        try:
            if self.server_process:
                self.server_process.terminate()
                self.server_process.wait()
        except:
            pass
        
        try:
            cmd = [str(LLAMA_SERVER), "-m", str(model), "--host", "127.0.0.1",
                   "--port", "8081", "-ngl", "99", "-c", "4096"]
            self.server_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            for _ in range(90):
                try:
                    resp = requests.post(SERVER_URL, json={"prompt":"test","n_predict":1,"temperature":0.7}, timeout=3)
                    if resp.status_code == 200:
                        self.server_ready = True
                        self.status_dot.config(fg="#50fa7b")
                        self.set_status("AI Ready", "#50fa7b")
                        self.set_bottom("AI server running on port 8081")
                        return
                except:
                    pass
                time.sleep(1)
        except Exception as e:
            if not silent:
                messagebox.showerror("Server Error", str(e))
    
    def check_server(self):
        if not self.server_ready:
            self.root.after(2000, self.check_server)
    
    # ─── CHAT VIEW ────────────────────────────────────────────
    
    def show_chat(self):
        self.clear_content()
        self.set_bottom("Chat - Type your message below")
        
        # Chat display
        self.chat_display = scrolledtext.ScrolledText(
            self.content_frame, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
            font=("Segoe UI", 11), insertbackground=TEXT_COLOR,
            relief=tk.FLAT, borderwidth=0, padx=20, pady=15, state=tk.DISABLED
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5,0))
        
        self.chat_display.tag_config("user", foreground=USER_COLOR, font=("Segoe UI", 11, "bold"))
        self.chat_display.tag_config("ai", foreground=AI_COLOR, font=("Segoe UI", 11))
        self.chat_display.tag_config("system", foreground=DIM, font=("Segoe UI", 9, "italic"))
        
        # Input area
        input_frame = tk.Frame(self.content_frame, bg=BG)
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.chat_input = tk.Text(input_frame, height=3, bg=INPUT_BG, fg=TEXT_COLOR,
                                  font=("Segoe UI", 11), insertbackground=TEXT_COLOR,
                                  relief=tk.FLAT, padx=12, pady=10,
                                  highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=USER_COLOR)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_input.bind("<Return>", self.chat_on_enter)
        
        btn_frame = tk.Frame(input_frame, bg=BG)
        btn_frame.pack(side=tk.RIGHT, padx=(8,0))
        
        self.math_btn = tk.Button(btn_frame, text="∑ Math", command=self.chat_toggle_math,
                            bg="#444444", fg="#cccccc", font=("Segoe UI", 9, "bold"),
                            relief=tk.FLAT, padx=12, pady=5, cursor="hand2")
        self.math_btn.pack(pady=(0,4))
        
        tk.Button(btn_frame, text="Send", command=self.chat_send,
                 bg=USER_COLOR, fg="white", font=("Segoe UI", 11, "bold"),
                 relief=tk.FLAT, padx=24, pady=5, cursor="hand2").pack()
        
        self.chat_input.focus_set()
        
        if self.server_ready:
            self.chat_add("system", "AI is ready. You can start chatting.")
        else:
            self.chat_add("system", "AI server is loading... please wait.")
    
    def chat_add(self, sender, text):
        self.chat_display.configure(state=tk.NORMAL)
        if self.chat_display.get("1.0", tk.END).strip():
            self.chat_display.insert(tk.END, "\n")
        label = {"you": "You", "ai": "AI", "system": "System"}.get(sender, sender)
        self.chat_display.insert(tk.END, f"{label}\n", sender)
        self.chat_display.insert(tk.END, text + "\n")
        self.chat_display.see(tk.END)
        self.chat_display.configure(state=tk.DISABLED)
    
    def chat_send(self):
        if not self.server_ready:
            messagebox.showwarning("Not Ready", "AI server is still loading.")
            return
        text = self.chat_input.get("1.0", tk.END).strip()
        if not text:
            return
        self.chat_input.delete("1.0", tk.END)
        self.chat_add("you", text)
        self.set_status("Thinking...", "#ffaa00")
        threading.Thread(target=self.chat_get_response, args=(text,), daemon=True).start()
    
    def chat_get_response(self, text):
        try:
            # Include project context if selected
            context = ""
            if self.current_project:
                context = f"[Current project: {self.current_project_name}]\n"
            
            prompt = f"{context}User: {text}\nAssistant:"
            resp = requests.post(SERVER_URL, json={
                "prompt": prompt, "n_predict": 1024, "temperature": 0.7
            }, timeout=120)
            
            if resp.status_code == 200:
                data = resp.json()
                ai_text = data.get("content", "").strip()
                if ai_text.startswith("User:") or ai_text.startswith("Assistant:"):
                    lines = ai_text.split('\n')
                    ai_text = '\n'.join(lines[1:]) if len(lines) > 1 else ai_text
                self.last_ai_response = ai_text
                self.root.after(0, self.chat_add, "ai", ai_text or "(no response)")
                self.root.after(0, self.set_status, "AI Ready", "#50fa7b")
                
                # Log to database
                self.log_conversation(text, ai_text)
                
                if self.math_mode and ai_text:
                    self.root.after(200, self.render_math, ai_text)
            else:
                self.root.after(0, self.chat_add, "system", f"Error: {resp.status_code}")
        except Exception as e:
            self.root.after(0, self.chat_add, "system", f"Error: {e}")
    
    def log_conversation(self, query, response):
        try:
            db = self.get_db()
            ts = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
            db.execute("INSERT INTO message_log (timestamp, source_id, target_id, type, type_name, payload, payload_size, project_id) VALUES (?,?,?,?,?,?,?,?)",
                      (ts, 0, 0, 3, "QUERY", query, len(query), self.current_project))
            db.execute("INSERT INTO message_log (timestamp, source_id, target_id, type, type_name, payload, payload_size, project_id) VALUES (?,?,?,?,?,?,?,?)",
                      (ts, 0, 0, 3, "RESPONSE", response, len(response), self.current_project))
            db.commit()
            db.close()
        except:
            pass
    
    def chat_toggle_math(self):
        self.math_mode = not self.math_mode
        if self.math_mode:
            self.math_btn.config(bg="#ffaa00", fg="#1e1e1e", text="∑ Math ON")
            if self.last_ai_response:
                self.render_math(self.last_ai_response)
        else:
            self.math_btn.config(bg="#444444", fg="#cccccc", text="∑ Math")
    
    def render_math(self, text):
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<script>MathJax={{tex:{{inlineMath:[['$','$']]}}}};</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>body{{background:#1e1e1e;color:#e0e0e0;font-family:'Segoe UI',sans-serif;
padding:40px;max-width:850px;margin:auto;line-height:1.9;font-size:16px;}}
h1{{color:#50fa7b;}}</style></head><body><h1>🧠 Math Render</h1>{text.replace(chr(10),'<br>')}</body></html>"""
        path = "/tmp/motherbrain_math.html"
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        webbrowser.open(f"file://{path}")
    
    def chat_on_enter(self, event):
        if not event.state & 0x1:
            self.chat_send()
            return "break"
    
    # ─── DASHBOARD VIEW ───────────────────────────────────────
    
    def show_dashboard(self):
        self.clear_content()
        self.set_bottom("Dashboard")
        
        canvas = tk.Canvas(self.content_frame, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.content_frame, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=BG)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        def card(parent, title, value, color=AI_COLOR):
            f = tk.Frame(parent, bg=CHAT_BG, padx=20, pady=15, highlightthickness=1, highlightbackground=BORDER)
            f.pack(fill=tk.X, padx=10, pady=5)
            tk.Label(f, text=title, fg=DIM, bg=CHAT_BG, font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(f, text=str(value), fg=color, bg=CHAT_BG, font=("Segoe UI", 24, "bold")).pack(anchor="w")
            return f
        
        tk.Label(scroll_frame, text="Platform Dashboard", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=10, pady=(15,5))
        
        db = self.get_db()
        
        projects = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        messages = db.execute("SELECT COUNT(*) FROM message_log").fetchone()[0]
        try:
            curated = db.execute("SELECT COUNT(*) FROM curation").fetchone()[0]
        except:
            curated = 0
        try:
            models = db.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        except:
            models = 0
        
        stats = tk.Frame(scroll_frame, bg=BG)
        stats.pack(fill=tk.X, padx=5, pady=5)
        
        card(stats, "Projects", projects, AI_COLOR)
        card(stats, "Messages Logged", messages, USER_COLOR)
        card(stats, "Curated", curated, "#ffaa00")
        card(stats, "Models", models, ACCENT)
        
        tk.Label(scroll_frame, text="Recent Messages", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=10, pady=(20,5))
        
        recent = db.execute("SELECT timestamp, type_name, payload FROM message_log ORDER BY id DESC LIMIT 10").fetchall()
        for ts, mtype, payload in recent:
            preview = payload[:100] + "..." if len(payload) > 100 else payload
            row = tk.Frame(scroll_frame, bg=BG)
            row.pack(fill=tk.X, padx=10, pady=2)
            tk.Label(row, text=f"[{ts}]", fg=DIM, bg=BG, font=("Segoe UI", 8), width=28, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=f"{mtype}:", fg=AI_COLOR, bg=BG, font=("Segoe UI", 8, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=preview, fg=TEXT_COLOR, bg=BG, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT)
        
        db.close()
    
    # ─── VAULT VIEW ───────────────────────────────────────────
    
    def show_vault(self):
        self.clear_content()
        self.set_bottom("Vault Browser")
        
        text = scrolledtext.ScrolledText(self.content_frame, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
                                         font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True)
        
        db = self.get_db()
        projects = db.execute("SELECT id, name, status, path FROM projects").fetchall()
        
        text.insert(tk.END, "═══ VAULT PROJECTS ═══\n\n")
        
        for pid, name, status, path in projects:
            text.insert(tk.END, f"▸ {name} [{status}]\n")
            text.insert(tk.END, f"  ID: {pid}\n")
            text.insert(tk.END, f"  Path: {path}\n")
            
            devices = db.execute("SELECT device_id, type, chip FROM devices WHERE project_id=?", (pid,)).fetchall()
            if devices:
                text.insert(tk.END, f"  Devices:\n")
                for did, dtype, chip in devices:
                    text.insert(tk.END, f"    - {did} ({dtype}, {chip})\n")
            
            models = db.execute("SELECT model_id, base_model, role FROM models WHERE project_id=?", (pid,)).fetchall()
            if models:
                text.insert(tk.END, f"  AI Models:\n")
                for mid, base, role in models:
                    text.insert(tk.END, f"    - {mid} ({base}) [{role}]\n")
            text.insert(tk.END, "\n")
        
        text.insert(tk.END, "═══ MODEL FILES ═══\n\n")
        if MODELS_DIR.exists():
            for f in sorted(MODELS_DIR.iterdir()):
                if f.is_file():
                    size = f.stat().st_size / (1024*1024)
                    text.insert(tk.END, f"  {f.name} ({size:.1f} MB)\n")
        
        if ADAPTERS_DIR.exists():
            text.insert(tk.END, "\n═══ LORA ADAPTERS ═══\n\n")
            for d in sorted(ADAPTERS_DIR.iterdir()):
                if d.is_dir():
                    text.insert(tk.END, f"  {d.name}\n")
        
        text.configure(state=tk.DISABLED)
        db.close()
    
    # ─── TRAINING DATA VIEW ───────────────────────────────────
    
    def show_training(self):
        self.clear_content()
        self.set_bottom("Training Data Manager")
        
        left = tk.Frame(self.content_frame, bg=BG, width=400)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left.pack_propagate(False)
        
        right = tk.Frame(self.content_frame, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left: stats + buttons
        tk.Label(left, text="Training Data", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(10,5))
        
        db = self.get_db()
        try:
            total = db.execute("SELECT COUNT(*) FROM message_log").fetchone()[0]
            curated = db.execute("SELECT COUNT(*) FROM curation").fetchone()[0]
            pairs = db.execute("SELECT COUNT(*) FROM conversation_pairs").fetchone()[0]
        except:
            total = curated = pairs = 0
        
        info_frame = tk.Frame(left, bg=CHAT_BG, padx=15, pady=15, highlightthickness=1, highlightbackground=BORDER)
        info_frame.pack(fill=tk.X, pady=5)
        tk.Label(info_frame, text=f"Total Messages: {total}", fg=TEXT_COLOR, bg=CHAT_BG, font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(info_frame, text=f"Curated: {curated}", fg=AI_COLOR, bg=CHAT_BG, font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(info_frame, text=f"Pairs: {pairs}", fg=USER_COLOR, bg=CHAT_BG, font=("Segoe UI", 10)).pack(anchor="w")
        
        tk.Button(left, text="Build Conversation Pairs", command=self.build_pairs,
                 bg=USER_COLOR, fg="white", font=("Segoe UI", 10), relief=tk.FLAT,
                 padx=15, pady=8, cursor="hand2").pack(fill=tk.X, pady=5)
        
        tk.Button(left, text="Export Training JSONL", command=self.export_dataset,
                 bg=AI_COLOR, fg="#1e1e1e", font=("Segoe UI", 10, "bold"), relief=tk.FLAT,
                 padx=15, pady=8, cursor="hand2").pack(fill=tk.X, pady=5)
        
        # Right: curation data
        tk.Label(right, text="Recent Curation", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10,5))
        
        self.curation_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
                                                       font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=15)
        self.curation_text.pack(fill=tk.BOTH, expand=True)
        
        try:
            recent = db.execute("""
                SELECT m.payload, c.label, c.correction, c.curated_at
                FROM curation c JOIN message_log m ON c.message_log_id = m.id
                ORDER BY c.id DESC LIMIT 20
            """).fetchall()
            for payload, label, correction, date in recent:
                self.curation_text.insert(tk.END, f"[{label.upper()}] {payload[:80]}\n")
                if correction:
                    self.curation_text.insert(tk.END, f"  → {correction[:80]}\n")
                self.curation_text.insert(tk.END, f"  {date}\n\n")
        except:
            self.curation_text.insert(tk.END, "No curation data yet. Chat with the AI to generate data.\n")
        
        self.curation_text.configure(state=tk.DISABLED)
        db.close()
    
    def build_pairs(self):
        db = self.get_db()
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS conversation_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_id INTEGER, response_id INTEGER,
                    query_text TEXT, response_text TEXT,
                    paired_at TEXT, curated_label TEXT, curated_correction TEXT
                )
            """)
            rows = db.execute("""
                SELECT q.id, q.payload, r.id, r.payload
                FROM message_log q JOIN message_log r ON r.id = q.id + 1
                WHERE q.type_name = 'QUERY' AND r.type_name = 'RESPONSE'
                AND q.id NOT IN (SELECT query_id FROM conversation_pairs WHERE query_id IS NOT NULL)
                ORDER BY q.id
            """).fetchall()
            count = 0
            for q_id, q_payload, r_id, r_payload in rows:
                db.execute("INSERT INTO conversation_pairs (query_id, response_id, query_text, response_text, paired_at) VALUES (?,?,?,?,?)",
                          (q_id, r_id, q_payload, r_payload, datetime.now().isoformat()))
                count += 1
            db.commit()
            messagebox.showinfo("Done", f"Built {count} new conversation pairs.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
        db.close()
        self.show_training()
    
    def export_dataset(self):
        db = self.get_db()
        try:
            pairs = db.execute("SELECT query_text, response_text, curated_label, curated_correction FROM conversation_pairs").fetchall()
        except:
            messagebox.showwarning("No Data", "No conversation pairs. Build them first.")
            db.close()
            return
        
        if not pairs:
            messagebox.showwarning("No Data", "No conversation pairs to export.")
            db.close()
            return
        
        path = filedialog.asksaveasfilename(defaultextension=".jsonl", filetypes=[("JSONL", "*.jsonl")])
        if not path:
            db.close()
            return
        
        with open(path, 'w') as f:
            for query, response, label, correction in pairs:
                record = {"instruction": query, "output": correction or response, "label": label or "none"}
                f.write(json.dumps(record) + '\n')
        
        messagebox.showinfo("Exported", f"Exported {len(pairs)} pairs to {path}")
        db.close()
        self.set_bottom(f"Exported to {path}")
    
    # ─── MODELS VIEW ──────────────────────────────────────────
    
    def show_models(self):
        self.clear_content()
        self.set_bottom("Model Registry")
        
        text = scrolledtext.ScrolledText(self.content_frame, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
                                         font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=15)
        text.pack(fill=tk.BOTH, expand=True)
        
        db = self.get_db()
        try:
            models = db.execute("SELECT id, name, quantization, size_bytes, role, source FROM model_registry").fetchall()
        except:
            models = []
        
        text.insert(tk.END, "═══ REGISTERED MODELS ═══\n\n")
        for mid, name, quant, size, role, source in models:
            size_mb = size / (1024*1024) if size else 0
            text.insert(tk.END, f"▸ {name}\n")
            text.insert(tk.END, f"  ID: {mid}\n")
            text.insert(tk.END, f"  Quantization: {quant or 'unknown'}\n")
            text.insert(tk.END, f"  Size: {size_mb:.1f} MB\n")
            text.insert(tk.END, f"  Role: {role}\n")
            text.insert(tk.END, f"  Source: {source}\n\n")
        
        text.insert(tk.END, "═══ ON DISK ═══\n\n")
        if MODELS_DIR.exists():
            for f in sorted(MODELS_DIR.iterdir()):
                if f.is_file():
                    size = f.stat().st_size / (1024*1024)
                    text.insert(tk.END, f"  {f.name} ({size:.1f} MB)\n")
        
        text.configure(state=tk.DISABLED)
        db.close()
    
    # ─── PROJECTS VIEW ────────────────────────────────────────
    
    def show_projects(self):
        self.clear_content()
        self.set_bottom("Project Manager")
        
        left = tk.Frame(self.content_frame, bg=BG, width=350)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left.pack_propagate(False)
        
        right = tk.Frame(self.content_frame, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        tk.Label(left, text="Projects", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(10,5))
        
        db = self.get_db()
        projects = db.execute("SELECT id, name, status, description FROM projects").fetchall()
        
        for pid, name, status, desc in projects:
            f = tk.Frame(left, bg=CHAT_BG, padx=15, pady=10, highlightthickness=1, highlightbackground=BORDER)
            f.pack(fill=tk.X, pady=3)
            tk.Label(f, text=name, fg=AI_COLOR, bg=CHAT_BG, font=("Segoe UI", 11, "bold")).pack(anchor="w")
            tk.Label(f, text=f"[{status}]", fg=DIM, bg=CHAT_BG, font=("Segoe UI", 9)).pack(anchor="w")
            if desc:
                tk.Label(f, text=desc[:80], fg=TEXT_COLOR, bg=CHAT_BG, font=("Segoe UI", 9), wraplength=300).pack(anchor="w")
        
        tk.Label(right, text="Project Details", fg=TEXT_COLOR, bg=BG,
                font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10,5))
        
        detail_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, bg=CHAT_BG, fg=TEXT_COLOR,
                                                font=("Segoe UI", 10), relief=tk.FLAT, padx=15, pady=15)
        detail_text.pack(fill=tk.BOTH, expand=True)
        detail_text.insert(tk.END, "Select a project from the left panel.\n\n")
        detail_text.insert(tk.END, "Projects connect your hardware devices, AI models,\n")
        detail_text.insert(tk.END, "simulation environments, and training data.\n\n")
        detail_text.insert(tk.END, "Each project has a manifest.json in the vault.\n")
        detail_text.insert(tk.END, "When you switch projects, the AI becomes aware\n")
        detail_text.insert(tk.END, "of that project's devices, models, and context.")
        detail_text.configure(state=tk.DISABLED)
        
        db.close()
    
    # ─── REFRESH ──────────────────────────────────────────────
    
    def refresh_all(self):
        self.load_project_list()
        self.set_bottom("Refreshed.")
    
    def on_close(self):
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MotherbrainApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
