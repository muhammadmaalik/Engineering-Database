#!/usr/bin/env python3
"""
Motherbrain GUI - Desktop chat interface for the local AI platform.
Connects to llama-server on port 8081.
Math mode renders LaTeX equations in browser.
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import requests
import json
import subprocess
import sys
import os
import signal
import time
import webbrowser
from pathlib import Path

MODEL_PATH = Path.home() / ".motherbrain" / "vault" / "shared" / "base_models" / "gemma-2-9b-it-Q5_K_M.gguf"
LLAMA_SERVER = Path.home() / "llama.cpp" / "build" / "bin" / "llama-server"
SERVER_URL = "http://127.0.0.1:8081/completion"

class MotherbrainGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Motherbrain AI")
        self.root.geometry("900x700")
        self.root.configure(bg="#1e1e1e")
        self.root.minsize(600, 400)
        
        # State
        self.math_mode = False
        self.last_ai_response = ""
        
        # Colors
        self.bg_color = "#1e1e1e"
        self.chat_bg = "#2d2d2d"
        self.user_color = "#4a9eff"
        self.ai_color = "#50fa7b"
        self.input_bg = "#3c3c3c"
        self.text_color = "#ffffff"
        self.accent = "#ff6b6b"
        
        # Header frame
        header = tk.Frame(root, bg="#252525", height=50)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        
        title_frame = tk.Frame(header, bg="#252525")
        title_frame.pack(side=tk.LEFT, padx=20, pady=10)
        
        tk.Label(title_frame, text="🧠", font=("Segoe UI", 18), bg="#252525").pack(side=tk.LEFT)
        tk.Label(title_frame, text="Motherbrain", fg=self.text_color, bg="#252525",
                font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT, padx=8)
        
        # Status indicator
        self.status_dot = tk.Label(header, text="●", fg="#ffaa00", bg="#252525", font=("Segoe UI", 12))
        self.status_dot.pack(side=tk.RIGHT, padx=10)
        self.status_label = tk.Label(header, text="Loading...", fg="#888888", bg="#252525",
                                     font=("Segoe UI", 10))
        self.status_label.pack(side=tk.RIGHT)
        
        # Chat display
        self.chat_display = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, bg=self.chat_bg, fg=self.text_color,
            font=("Segoe UI", 11), insertbackground=self.text_color,
            relief=tk.FLAT, borderwidth=0, padx=20, pady=15
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8,0))
        self.chat_display.configure(state=tk.DISABLED)
        
        # Tags for colored text
        self.chat_display.tag_config("user", foreground=self.user_color, 
                                     font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4)
        self.chat_display.tag_config("ai", foreground=self.ai_color, 
                                     font=("Segoe UI", 11), spacing1=8, spacing3=4)
        self.chat_display.tag_config("system", foreground="#888888", 
                                     font=("Segoe UI", 9, "italic"), spacing1=4, spacing3=2)
        
        # Separator line
        sep = tk.Frame(root, height=1, bg="#444444")
        sep.pack(fill=tk.X, padx=8)
        
        # Input frame
        input_frame = tk.Frame(root, bg=self.bg_color)
        input_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=8)
        
        self.input_field = tk.Text(input_frame, height=3, bg=self.input_bg, fg=self.text_color,
                                   font=("Segoe UI", 11), insertbackground=self.text_color,
                                   relief=tk.FLAT, borderwidth=0, padx=12, pady=10,
                                   highlightthickness=1, highlightbackground="#555555",
                                   highlightcolor=self.user_color)
        self.input_field.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_field.bind("<Return>", self.on_enter)
        self.input_field.insert("1.0", "")
        
        # Button frame
        btn_frame = tk.Frame(input_frame, bg=self.bg_color)
        btn_frame.pack(side=tk.RIGHT, padx=(8,0))
        
        self.math_btn = tk.Button(btn_frame, text="∑ Math", command=self.toggle_math,
                            bg="#444444", fg="#cccccc", font=("Segoe UI", 9, "bold"),
                            relief=tk.FLAT, padx=12, pady=5, cursor="hand2",
                            activebackground="#555555", activeforeground="white")
        self.math_btn.pack(pady=(0,4))
        
        send_btn = tk.Button(btn_frame, text="Send", command=self.send_message,
                            bg=self.user_color, fg="white", font=("Segoe UI", 11, "bold"),
                            relief=tk.FLAT, padx=24, pady=5, cursor="hand2",
                            activebackground="#6ab4ff", activeforeground="white")
        send_btn.pack()
        
        # Bottom status bar
        self.bottom_status = tk.Label(root, text="", fg="#555555", bg=self.bg_color,
                                      font=("Segoe UI", 8))
        self.bottom_status.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=3)
        
        # Server process
        self.server_process = None
        self.server_ready = False
        
        # Start server
        threading.Thread(target=self.start_server, daemon=True).start()
        
        # Check server after delay
        self.root.after(1500, self.check_server_ready)
        
        # Focus input
        self.input_field.focus_set()
    
    def start_server(self):
        """Launch llama-server as subprocess."""
        try:
            cmd = [
                str(LLAMA_SERVER),
                "-m", str(MODEL_PATH),
                "--host", "127.0.0.1",
                "--port", "8081",
                "-ngl", "99",
                "-c", "4096"
            ]
            self.server_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            for _ in range(90):
                try:
                    resp = requests.post(SERVER_URL, json={
                        "prompt": "test", "n_predict": 1, "temperature": 0.7
                    }, timeout=3)
                    if resp.status_code == 200:
                        self.server_ready = True
                        return
                except:
                    pass
                time.sleep(1)
        except Exception as e:
            print(f"Server error: {e}")
    
    def check_server_ready(self):
        if self.server_ready:
            self.status_dot.config(fg="#50fa7b")
            self.status_label.config(text="AI Ready", fg="#50fa7b")
            self.add_message("system", "Motherbrain is ready. Ask me anything.")
        else:
            self.root.after(2000, self.check_server_ready)
    
    def add_message(self, sender, text):
        """Add a message to the chat display."""
        self.chat_display.configure(state=tk.NORMAL)
        if self.chat_display.get("1.0", tk.END).strip():
            self.chat_display.insert(tk.END, "\n")
        
        tag = sender.lower()
        if tag == "system":
            self.chat_display.insert(tk.END, text + "\n", tag)
        else:
            label = "You" if tag == "you" else "AI"
            self.chat_display.insert(tk.END, f"{label}  \n", tag)
            self.chat_display.insert(tk.END, text + "\n")
        
        self.chat_display.see(tk.END)
        self.chat_display.configure(state=tk.DISABLED)
    
    def render_math(self, text):
        """Open AI response with LaTeX rendering in browser."""
        escaped = text.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>
MathJax = {{
  tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] }}
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>
body {{ background: #1e1e1e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; 
       padding: 40px; max-width: 850px; margin: auto; line-height: 1.9; font-size: 16px; }}
h1 {{ color: #50fa7b; font-size: 24px; margin-bottom: 30px; border-bottom: 1px solid #333; padding-bottom: 15px; }}
p {{ margin: 16px 0; }}
strong {{ color: #4a9eff; }}
code {{ background: #333; padding: 2px 6px; border-radius: 4px; font-size: 14px; }}
pre {{ background: #2d2d2d; padding: 20px; border-radius: 8px; overflow-x: auto; border: 1px solid #444; }}
blockquote {{ border-left: 3px solid #50fa7b; padding-left: 20px; color: #aaa; margin: 20px 0; }}
a {{ color: #4a9eff; }}
</style>
</head>
<body>
<h1>🧠 Motherbrain - Math Render</h1>
{text.replace(chr(10), '<br>')}
</body>
</html>"""
        path = "/tmp/motherbrain_math.html"
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        webbrowser.open(f"file://{path}")
    
    def toggle_math(self):
        self.math_mode = not self.math_mode
        if self.math_mode:
            self.math_btn.config(bg="#ffaa00", fg="#1e1e1e", text="∑ Math ON")
            if self.last_ai_response:
                self.render_math(self.last_ai_response)
        else:
            self.math_btn.config(bg="#444444", fg="#cccccc", text="∑ Math")
    
    def send_message(self):
        if not self.server_ready:
            messagebox.showwarning("Server Not Ready", "AI server is still loading. Please wait.")
            return
        
        user_text = self.input_field.get("1.0", tk.END).strip()
        if not user_text:
            return
        
        self.input_field.delete("1.0", tk.END)
        self.add_message("you", user_text)
        self.status_dot.config(fg="#ffaa00")
        self.status_label.config(text="Thinking...", fg="#ffaa00")
        self.bottom_status.config(text="Processing...")
        
        threading.Thread(target=self.get_ai_response, args=(user_text,), daemon=True).start()
    
    def get_ai_response(self, user_text):
        try:
            prompt = f"User: {user_text}\nAssistant:"
            resp = requests.post(SERVER_URL, json={
                "prompt": prompt,
                "n_predict": 1024,
                "temperature": 0.7
            }, timeout=120)
            
            if resp.status_code == 200:
                data = resp.json()
                ai_text = data.get("content", "").strip()
                
                # Clean artifacts
                if ai_text.startswith("User:") or ai_text.startswith("Assistant:"):
                    lines = ai_text.split('\n')
                    ai_text = '\n'.join(lines[1:]) if len(lines) > 1 else ai_text
                
                self.last_ai_response = ai_text
                
                self.root.after(0, self.add_message, "ai", ai_text if ai_text else "(no response)")
                self.root.after(0, self.status_dot.config, {"fg": "#50fa7b"})
                self.root.after(0, self.status_label.config, {"text": "AI Ready", "fg": "#50fa7b"})
                self.root.after(0, self.bottom_status.config, {"text": f"Response: {len(ai_text)} chars"})
                
                if self.math_mode and ai_text:
                    self.root.after(200, self.render_math, ai_text)
            else:
                self.root.after(0, self.add_message, "system", f"Error: Server returned {resp.status_code}")
                self.root.after(0, self.status_dot.config, {"fg": "#ff4444"})
                self.root.after(0, self.status_label.config, {"text": "Error", "fg": "#ff4444"})
                self.root.after(0, self.bottom_status.config, {"text": "Request failed"})
        except requests.exceptions.Timeout:
            self.root.after(0, self.add_message, "system", "Request timed out. Please try again.")
            self.root.after(0, self.status_dot.config, {"fg": "#ff4444"})
            self.root.after(0, self.status_label.config, {"text": "Timeout", "fg": "#ff4444"})
            self.root.after(0, self.bottom_status.config, {"text": "Timeout"})
        except Exception as e:
            self.root.after(0, self.add_message, "system", f"Error: {str(e)}")
            self.root.after(0, self.status_dot.config, {"fg": "#ff4444"})
            self.root.after(0, self.status_label.config, {"text": "Error", "fg": "#ff4444"})
    
    def on_enter(self, event):
        if not event.state & 0x1:
            self.send_message()
            return "break"
    
    def on_close(self):
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = MotherbrainGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
