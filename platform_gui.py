#!/usr/bin/env python3
"""
Motherbrain Platform Manager - GUI for vault, database, curation, models.
Retro terminal aesthetic. Separate from the AI chat GUI.
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import sqlite3
import json
import os
import sys
from pathlib import Path
from datetime import datetime

VAULT_DB = Path.home() / ".motherbrain" / "vault" / "vault_index.db"
VAULT_ROOT = Path.home() / ".motherbrain" / "vault"
MODELS_DIR = VAULT_ROOT / "shared" / "base_models"


class RetroTerminal:
    """A terminal-style frame with green-on-black text."""
    
    def __init__(self, parent, height=20):
        self.frame = tk.Frame(parent, bg="#0a0a0a", highlightthickness=1, highlightbackground="#1a3a1a")
        self.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.output = scrolledtext.ScrolledText(
            self.frame, wrap=tk.WORD, bg="#0a0a0a", fg="#00ff41",
            font=("Courier New", 10), insertbackground="#00ff41",
            relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
            height=height
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.configure(state=tk.DISABLED)
        
        self.output.tag_config("header", foreground="#00ff41", font=("Courier New", 12, "bold"))
        self.output.tag_config("success", foreground="#00cc33")
        self.output.tag_config("warning", foreground="#ffaa00")
        self.output.tag_config("error", foreground="#ff4444")
        self.output.tag_config("dim", foreground="#006611")
        self.output.tag_config("highlight", foreground="#ffffff", background="#003300")
    
    def clear(self):
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.configure(state=tk.DISABLED)
    
    def write(self, text, tag=None):
        self.output.configure(state=tk.NORMAL)
        if self.output.get("1.0", tk.END).strip():
            self.output.insert(tk.END, "\n")
        self.output.insert(tk.END, text, tag or "success")
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)
    
    def writeln(self, text, tag=None):
        self.write(text + "\n", tag)


class PlatformGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Motherbrain Platform Manager")
        self.root.geometry("1000x700")
        self.root.configure(bg="#0d0d0d")
        self.root.minsize(800, 500)
        
        # Colors
        self.bg = "#0d0d0d"
        self.panel_bg = "#111111"
        self.green = "#00ff41"
        self.dim_green = "#006611"
        self.text_color = "#cccccc"
        
        # Header
        header = tk.Frame(root, bg="#0a0a0a", height=45, highlightthickness=1, highlightbackground="#1a3a1a")
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        
        tk.Label(header, text="> motherbrain_platform.exe", fg=self.green, bg="#0a0a0a",
                font=("Courier New", 14, "bold")).pack(side=tk.LEFT, padx=15, pady=10)
        
        tk.Label(header, text="v1.0 // local vault", fg=self.dim_green, bg="#0a0a0a",
                font=("Courier New", 9)).pack(side=tk.RIGHT, padx=15, pady=10)
        
        # Main content - two panels
        main_frame = tk.Frame(root, bg=self.bg)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        
        # Left panel - buttons
        left_panel = tk.Frame(main_frame, bg=self.panel_bg, width=220, 
                             highlightthickness=1, highlightbackground="#1a3a1a")
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0,5))
        left_panel.pack_propagate(False)
        
        tk.Label(left_panel, text="[ COMMANDS ]", fg=self.green, bg=self.panel_bg,
                font=("Courier New", 10, "bold")).pack(pady=(15,10))
        
        buttons = [
            ("📊  Dashboard", self.show_dashboard),
            ("📁  Vault Browser", self.show_vault),
            ("📝  Message Log", self.show_messages),
            ("🏷️  Curation", self.show_curation),
            ("🤖  Models", self.show_models),
            ("📦  Export Data", self.export_data),
            ("🔄  Refresh All", self.refresh),
            ("🧹  Clear Terminal", self.clear_terminal),
        ]
        
        for text, cmd in buttons:
            btn = tk.Button(left_panel, text=text, command=cmd,
                          bg=self.panel_bg, fg="#00cc44", font=("Courier New", 9),
                          relief=tk.FLAT, anchor="w", padx=15, pady=6,
                          activebackground="#1a3a1a", activeforeground=self.green,
                          cursor="hand2", width=24)
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#1a2a1a"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=self.panel_bg))
        
        # Status
        tk.Label(left_panel, text="", bg=self.panel_bg).pack(expand=True)
        self.left_status = tk.Label(left_panel, text="Ready.", fg=self.dim_green, bg=self.panel_bg,
                                    font=("Courier New", 8))
        self.left_status.pack(pady=10)
        
        # Right panel - terminal output
        right_panel = tk.Frame(main_frame, bg=self.bg)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.terminal = RetroTerminal(right_panel)
        
        # Bottom bar
        bottom = tk.Frame(root, bg="#0a0a0a", height=25, highlightthickness=1, highlightbackground="#1a3a1a")
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        bottom.pack_propagate(False)
        self.bottom_status = tk.Label(bottom, text="> _", fg=self.green, bg="#0a0a0a",
                                      font=("Courier New", 9), anchor="w")
        self.bottom_status.pack(fill=tk.X, padx=12, pady=3)
        
        # Load dashboard on start
        self.root.after(500, self.show_dashboard)
    
    def get_db(self):
        return sqlite3.connect(str(VAULT_DB))
    
    def status(self, text):
        self.bottom_status.config(text=f"> {text}")
        self.left_status.config(text=text[:30])
    
    def clear_terminal(self):
        self.terminal.clear()
        self.status("Terminal cleared.")
    
    def refresh(self):
        self.terminal.clear()
        self.show_dashboard()
        self.status("Refreshed.")
    
    def show_dashboard(self):
        self.terminal.clear()
        db = self.get_db()
        
        self.terminal.write("╔══════════════════════════════════════╗", "dim")
        self.terminal.write("║     MOTHERBRAIN PLATFORM STATUS      ║", "header")
        self.terminal.write("╚══════════════════════════════════════╝", "dim")
        
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
        
        try:
            pairs = db.execute("SELECT COUNT(*) FROM conversation_pairs").fetchone()[0]
        except:
            pairs = 0
        
        self.terminal.write(f"\n  Projects indexed.... {projects}", "success")
        self.terminal.write(f"  Messages logged..... {messages}", "success")
        self.terminal.write(f"  Messages curated.... {curated}", "warning" if curated == 0 else "success")
        self.terminal.write(f"  Conversation pairs.. {pairs}", "success")
        self.terminal.write(f"  Models registered... {models}", "success")
        
        # Recent message types
        types = db.execute("SELECT type_name, COUNT(*) FROM message_log GROUP BY type_name ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
        if types:
            self.terminal.write("\n  --- Message Types ---", "dim")
            for t, c in types:
                self.terminal.write(f"  {t:<15} : {c}", "success")
        
        # DB size
        db_size = VAULT_DB.stat().st_size / 1024 if VAULT_DB.exists() else 0
        self.terminal.write(f"\n  Vault DB size........ {db_size:.1f} KB", "dim")
        self.terminal.write(f"  Vault path........... {VAULT_ROOT}", "dim")
        
        db.close()
        self.status("Dashboard loaded.")
    
    def show_vault(self):
        self.terminal.clear()
        self.terminal.write("=== VAULT BROWSER ===", "header")
        
        db = self.get_db()
        projects = db.execute("SELECT id, name, status, path FROM projects").fetchall()
        
        if not projects:
            self.terminal.write("  No projects in vault.", "warning")
        else:
            for pid, name, status, path in projects:
                self.terminal.write(f"\n  [{status.upper()}] {name}", "highlight")
                self.terminal.write(f"    ID:   {pid}", "dim")
                self.terminal.write(f"    Path: {path}", "dim")
                
                devices = db.execute("SELECT device_id, type, chip FROM devices WHERE project_id=?", (pid,)).fetchall()
                if devices:
                    self.terminal.write(f"    Devices ({len(devices)}):", "success")
                    for did, dtype, chip in devices:
                        self.terminal.write(f"      - {did} [{dtype}] {chip}", "success")
        
        # Model files
        self.terminal.write("\n\n--- MODEL FILES ---", "dim")
        if MODELS_DIR.exists():
            for f in sorted(MODELS_DIR.iterdir()):
                if f.is_file():
                    size_mb = f.stat().st_size / (1024*1024)
                    self.terminal.write(f"  {f.name} ({size_mb:.1f} MB)", "success")
        else:
            self.terminal.write("  No models downloaded.", "warning")
        
        db.close()
        self.status("Vault browsed.")
    
    def show_messages(self):
        self.terminal.clear()
        self.terminal.write("=== RECENT MESSAGES ===", "header")
        
        db = self.get_db()
        messages = db.execute("SELECT timestamp, type_name, payload FROM message_log ORDER BY id DESC LIMIT 30").fetchall()
        
        for ts, mtype, payload in messages:
            tag = "warning" if mtype == "HEARTBEAT" else "success"
            preview = payload[:80] + "..." if len(payload) > 80 else payload
            self.terminal.write(f"  [{ts}] {mtype}", tag)
            self.terminal.write(f"    {preview}", "dim")
        
        db.close()
        self.status(f"Showing {len(messages)} recent messages.")
    
    def show_curation(self):
        self.terminal.clear()
        self.terminal.write("=== CURATION STATUS ===", "header")
        
        db = self.get_db()
        
        try:
            total_curated = db.execute("SELECT COUNT(*) FROM curation").fetchone()[0]
            labels = db.execute("SELECT label, COUNT(*) FROM curation GROUP BY label").fetchall()
        except:
            self.terminal.write("  No curation data yet. Run 'curate' in shell.", "warning")
            db.close()
            return
        
        self.terminal.write(f"  Total curated: {total_curated}", "success")
        for label, count in labels:
            color = "success" if label == "good" else "warning"
            self.terminal.write(f"    {label}: {count}", color)
        
        # Uncurated count
        uncurated = db.execute("""
            SELECT COUNT(*) FROM message_log m
            LEFT JOIN curation c ON m.id = c.message_log_id
            WHERE c.id IS NULL AND m.type_name != 'HEARTBEAT'
        """).fetchone()[0]
        self.terminal.write(f"\n  Remaining to curate: {uncurated}", "warning" if uncurated > 0 else "success")
        
        # Recent curation
        recent = db.execute("""
            SELECT m.payload, c.label, c.correction, c.curated_at
            FROM curation c JOIN message_log m ON c.message_log_id = m.id
            ORDER BY c.id DESC LIMIT 5
        """).fetchall()
        
        if recent:
            self.terminal.write("\n--- Recent Curation ---", "dim")
            for payload, label, correction, date in recent:
                self.terminal.write(f"  [{label}] {payload[:60]}", "success")
                if correction:
                    self.terminal.write(f"    -> {correction[:60]}", "dim")
        
        db.close()
        self.status("Curation viewed.")
    
    def show_models(self):
        self.terminal.clear()
        self.terminal.write("=== MODEL REGISTRY ===", "header")
        
        db = self.get_db()
        try:
            models = db.execute("SELECT id, name, quantization, size_bytes, role FROM model_registry").fetchall()
        except:
            self.terminal.write("  No models registered.", "warning")
            db.close()
            return
        
        for mid, name, quant, size, role in models:
            size_mb = size / (1024*1024) if size else 0
            self.terminal.write(f"\n  {name}", "highlight")
            self.terminal.write(f"    ID:     {mid}", "dim")
            self.terminal.write(f"    Quant:  {quant or 'unknown'}", "success")
            self.terminal.write(f"    Size:   {size_mb:.1f} MB", "success")
            self.terminal.write(f"    Role:   {role}", "success")
        
        # Adapters
        adapters_dir = VAULT_ROOT / "shared" / "adapters"
        if adapters_dir.exists():
            adapters = list(adapters_dir.iterdir())
            if adapters:
                self.terminal.write("\n--- LORA ADAPTERS ---", "dim")
                for a in adapters:
                    if a.is_dir():
                        self.terminal.write(f"  {a.name}", "success")
        
        db.close()
        self.status("Models listed.")
    
    def export_data(self):
        self.terminal.clear()
        self.terminal.write("=== EXPORT TRAINING DATA ===", "header")
        
        db = self.get_db()
        
        try:
            pairs = db.execute("""
                SELECT query_text, response_text, curated_label, curated_correction
                FROM conversation_pairs
            """).fetchall()
        except:
            self.terminal.write("  No conversation pairs. Run 'pairs' in shell first.", "warning")
            db.close()
            return
        
        if not pairs:
            self.terminal.write("  No data to export.", "warning")
            db.close()
            return
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"/tmp/motherbrain_export_{ts}.jsonl"
        
        with open(path, 'w') as f:
            for query, response, label, correction in pairs:
                record = {
                    "instruction": query,
                    "output": correction or response,
                    "label": label or "none"
                }
                f.write(json.dumps(record) + '\n')
        
        self.terminal.write(f"  Exported {len(pairs)} pairs", "success")
        self.terminal.write(f"  File: {path}", "dim")
        self.terminal.write(f"  Size: {Path(path).stat().st_size} bytes", "dim")
        
        db.close()
        self.status(f"Exported to {path}")


if __name__ == "__main__":
    root = tk.Tk()
    app = PlatformGUI(root)
    root.mainloop()
