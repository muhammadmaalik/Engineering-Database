#!/usr/bin/env python3
"""
Motherbrain Shell - Terminal interface for the platform.
Query the vault, export training data, curate messages, manage models.
"""

import sqlite3
import os
import json
import sys
from pathlib import Path
from datetime import datetime
from huggingface_hub import hf_hub_download, list_repo_files

VAULT_DB = Path.home() / ".motherbrain" / "vault" / "vault_index.db"
VAULT_ROOT = Path.home() / ".motherbrain" / "vault"
MODELS_DIR = VAULT_ROOT / "shared" / "base_models"


def get_db():
    return sqlite3.connect(str(VAULT_DB))


def show_dashboard():
    print("=" * 50)
    print("        MOTHERBRAIN SHELL v0.3.0")
    print("=" * 50)

    db = get_db()

    projects = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    print(f"\n  Projects indexed: {projects}")

    messages = db.execute("SELECT COUNT(*) FROM message_log").fetchone()[0]
    print(f"  Messages logged:  {messages}")

    try:
        curated = db.execute("SELECT COUNT(*) FROM curation").fetchone()[0]
        print(f"  Messages curated: {curated}")
    except sqlite3.OperationalError:
        print(f"  Messages curated: 0 (run 'curate' first)")

    try:
        models = db.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        print(f"  Models registered: {models}")
    except sqlite3.OperationalError:
        print(f"  Models registered: 0")

    types = db.execute(
        "SELECT type_name, COUNT(*) as cnt FROM message_log GROUP BY type_name ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    if types:
        print("\n  Message types:")
        for t, c in types:
            print(f"    {t}: {c}")

    proj_list = db.execute("SELECT id, name, status FROM projects").fetchall()
    if proj_list:
        print("\n  Projects:")
        for pid, name, status in proj_list:
            print(f"    [{status}] {name} ({pid})")

    db.close()
    print("\n" + "-" * 50)


def export_training_data(output_path=None, label_filter=None):
    """Export curated messages as JSONL for fine-tuning."""
    db = get_db()

    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"dataset_{ts}.jsonl"

    try:
        query = """
            SELECT m.timestamp, m.type_name, m.payload, c.label, c.correction
            FROM message_log m
            JOIN curation c ON m.id = c.message_log_id
            WHERE 1=1
        """
        params = []

        if label_filter:
            query += " AND c.label = ?"
            params.append(label_filter)

        query += " ORDER BY m.timestamp"

        rows = db.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        print("[SHELL] No curated data. Run 'curate' first.")
        db.close()
        return None

    with open(output_path, 'w') as f:
        for ts, mtype, payload, label, correction in rows:
            record = {
                "timestamp": ts,
                "type": mtype,
                "payload": payload,
                "label": label
            }
            if correction:
                record["correction"] = correction
            f.write(json.dumps(record) + '\n')

    print(f"[SHELL] Exported {len(rows)} curated messages to {output_path}")
    db.close()
    return output_path


def curate_messages():
    """Step through uncurated messages and label them for training."""
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS curation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_log_id INTEGER UNIQUE,
            label TEXT,
            correction TEXT,
            curated_at TEXT,
            FOREIGN KEY (message_log_id) REFERENCES message_log(id)
        )
    """)

    rows = db.execute("""
        SELECT m.id, m.timestamp, m.type_name, m.payload
        FROM message_log m
        LEFT JOIN curation c ON m.id = c.message_log_id
        WHERE c.id IS NULL
        ORDER BY m.id
    """).fetchall()

    if not rows:
        print("[SHELL] No uncurated messages. Everything has been reviewed.")
        db.close()
        return

    total = len(rows)
    print(f"[SHELL] {total} uncurated messages to review.")
    print("Commands: (g)ood, (b)ad, (s)kip, c=<correction>, (q)uit\n")

    curated_count = 0

    for row in rows:
        msg_id, timestamp, mtype, payload = row

        print(f"[{msg_id}] {timestamp} | {mtype}")
        print(f"    {payload}")

        while True:
            choice = input("    > ").strip().lower()

            if choice == 'q':
                print(f"\n[SHELL] Curated {curated_count} messages. Progress saved.")
                db.commit()
                db.close()
                return
            elif choice == 'g':
                db.execute(
                    "INSERT INTO curation (message_log_id, label, curated_at) VALUES (?, 'good', ?)",
                    (msg_id, datetime.now().isoformat())
                )
                curated_count += 1
                break
            elif choice == 'b':
                db.execute(
                    "INSERT INTO curation (message_log_id, label, curated_at) VALUES (?, 'bad', ?)",
                    (msg_id, datetime.now().isoformat())
                )
                curated_count += 1
                break
            elif choice == 's':
                break
            elif choice.startswith('c='):
                correction = choice[2:]
                db.execute(
                    "INSERT INTO curation (message_log_id, label, correction, curated_at) VALUES (?, 'corrected', ?, ?)",
                    (msg_id, correction, datetime.now().isoformat())
                )
                curated_count += 1
                break
            else:
                print("    Invalid. (g)ood, (b)ad, (s)kip, c=<correction>, (q)uit")

    db.commit()
    print(f"\n[SHELL] All {curated_count} messages curated.")
    db.close()


# ─── MODEL REGISTRY ───────────────────────────────────────────

def init_model_registry():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT,
            file_path TEXT NOT NULL,
            quantization TEXT,
            size_bytes INTEGER,
            downloaded_at TEXT,
            base_model TEXT,
            role TEXT DEFAULT 'general'
        )
    """)
    db.commit()
    db.close()


def model_download(repo_id, quantization=None):
    """Download a GGUF model from Hugging Face."""
    init_model_registry()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[SHELL] Finding GGUF files in {repo_id}...")

    try:
        files = list_repo_files(repo_id)
        gguf_files = [f for f in files if f.endswith('.gguf')]

        if not gguf_files:
            print(f"[SHELL] No GGUF files found in {repo_id}")
            return

        # If quantization specified, filter
        if quantization:
            gguf_files = [f for f in gguf_files if quantization.upper() in f.upper()]

        if not gguf_files:
            print(f"[SHELL] No GGUF files matching quantization '{quantization}'")
            return

        # Show options
        print(f"[SHELL] Found {len(gguf_files)} matching files:")
        for i, f in enumerate(gguf_files):
            size_mb = "unknown"
            print(f"  [{i}] {f}")

        if len(gguf_files) == 1:
            choice = 0
        else:
            choice = int(input(f"  Choose file [0-{len(gguf_files)-1}]: "))

        selected = gguf_files[choice]
        filename = Path(selected).name
        dest = MODELS_DIR / filename

        if dest.exists():
            print(f"[SHELL] Model already exists: {dest}")
        else:
            print(f"[SHELL] Downloading {selected}...")
            hf_hub_download(
                repo_id=repo_id,
                filename=selected,
                local_dir=str(MODELS_DIR),
                local_dir_use_symlinks=False
            )
            print(f"[SHELL] Downloaded to {dest}")

        # Register in database
        model_id = filename.replace('.gguf', '')
        name_parts = repo_id.split('/')
        model_name = name_parts[-1] if len(name_parts) > 1 else repo_id
        size_bytes = dest.stat().st_size if dest.exists() else 0
        size_mb = size_bytes / (1024 * 1024)

        db = get_db()
        db.execute("""
            INSERT OR REPLACE INTO model_registry (id, name, source, file_path, quantization, size_bytes, downloaded_at, base_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            model_id,
            model_name,
            repo_id,
            str(dest),
            quantization or "unknown",
            size_bytes,
            datetime.now().isoformat(),
            repo_id
        ))
        db.commit()
        db.close()

        print(f"[SHELL] Model registered: {model_id} ({size_mb:.1f} MB)")

    except Exception as e:
        print(f"[SHELL] Error: {e}")


def model_list():
    """List all registered models."""
    init_model_registry()
    db = get_db()

    models = db.execute("""
        SELECT id, name, quantization, size_bytes, role, downloaded_at
        FROM model_registry ORDER BY downloaded_at DESC
    """).fetchall()

    if not models:
        print("[SHELL] No models registered. Use 'model download <repo>' to get one.")
        db.close()
        return

    print("\n  Registered Models:")
    print(f"  {'ID':<40} {'Quant':<10} {'Size':<10} {'Role'}")
    print(f"  {'-'*40} {'-'*10} {'-'*10} {'-'*15}")

    for mid, name, quant, size, role, date in models:
        size_mb = size / (1024 * 1024) if size else 0
        print(f"  {mid:<40} {quant or '?':<10} {size_mb:>6.1f} MB  {role}")

    db.close()


def model_info(model_id):
    """Show details for a specific model."""
    init_model_registry()
    db = get_db()

    model = db.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()

    if not model:
        print(f"[SHELL] Model not found: {model_id}")
        db.close()
        return

    print(f"\n  Model: {model[0]}")
    print(f"  Name: {model[1]}")
    print(f"  Source: {model[2]}")
    print(f"  Path: {model[3]}")
    print(f"  Quantization: {model[4]}")
    print(f"  Size: {model[5] / (1024*1024):.1f} MB")
    print(f"  Role: {model[7]}")
    print(f"  Downloaded: {model[6]}")

    db.close()


# ─── SEARCH ───────────────────────────────────────────────────

def search_projects(query):
    """Full-text search across projects."""
    db = get_db()
    results = db.execute(
        "SELECT p.id, p.name, p.description, p.status FROM projects p "
        "JOIN projects_fts fts ON p.rowid = fts.rowid "
        "WHERE projects_fts MATCH ?", (query,)
    ).fetchall()

    if results:
        print(f"\n  Results for '{query}':")
        for pid, name, desc, status in results:
            print(f"    [{status}] {name} ({pid})")
            if desc:
                print(f"      {desc[:100]}")
    else:
        print(f"  No results for '{query}'")

    db.close()


# ─── INTERACTIVE MODE ─────────────────────────────────────────

def interactive_mode():
    """Command loop for the shell."""
    print("Type 'help' for commands, 'quit' to exit.")

    while True:
        try:
            cmd = input("\nmotherbrain> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()

        if action == "quit" or action == "exit":
            break
        elif action == "help":
            print("Commands:")
            print("  dashboard   - Show system overview")
            print("  curate      - Review and label messages for training")
            print("  export [label] [path] - Export curated data as JSONL")
            print("  model download <huggingface/repo> [quant] - Download a GGUF model")
            print("  model list  - List registered models")
            print("  model info <id> - Show model details")
            print("  search <query> - Full-text search projects")
            print("  projects    - List all projects")
            print("  messages    - Show message counts")
            print("  quit        - Exit shell")
        elif action == "dashboard":
            show_dashboard()
        elif action == "curate":
            curate_messages()
        elif action == "export":
            label = parts[1] if len(parts) > 1 else None
            path = parts[2] if len(parts) > 2 else None
            export_training_data(path, label)
        elif action == "model":
            if len(parts) > 1:
                sub = parts[1].lower()
                if sub == "download":
                    repo = parts[2] if len(parts) > 2 else None
                    quant = parts[3] if len(parts) > 3 else None
                    if repo:
                        model_download(repo, quant)
                    else:
                        print("Usage: model download <huggingface/repo> [quantization]")
                elif sub == "list":
                    model_list()
                elif sub == "info":
                    if len(parts) > 2:
                        model_info(parts[2])
                    else:
                        print("Usage: model info <id>")
                else:
                    print(f"Unknown model command: {sub}")
            else:
                print("Usage: model <download|list|info>")
        elif action == "search":
            if len(parts) > 1:
                search_projects(" ".join(parts[1:]))
            else:
                print("Usage: search <query>")
        elif action == "projects":
            db = get_db()
            for pid, name, status in db.execute("SELECT id, name, status FROM projects").fetchall():
                print(f"  [{status}] {name} ({pid})")
            db.close()
        elif action == "messages":
            db = get_db()
            total = db.execute("SELECT COUNT(*) FROM message_log").fetchone()[0]
            try:
                curated = db.execute("SELECT COUNT(*) FROM curation").fetchone()[0]
            except:
                curated = 0
            print(f"  Total messages: {total}")
            print(f"  Curated: {curated}")
            print(f"  Remaining: {total - curated}")
            db.close()
        else:
            print(f"Unknown command: {action}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "dashboard":
            show_dashboard()
        elif cmd == "curate":
            curate_messages()
        elif cmd == "export":
            label = sys.argv[2] if len(sys.argv) > 2 else None
            path = sys.argv[3] if len(sys.argv) > 3 else None
            export_training_data(path, label)
        elif cmd == "model":
            sub = sys.argv[2] if len(sys.argv) > 2 else None
            if sub == "list":
                model_list()
            elif sub == "download":
                repo = sys.argv[3] if len(sys.argv) > 3 else None
                quant = sys.argv[4] if len(sys.argv) > 4 else None
                if repo:
                    model_download(repo, quant)
        elif cmd == "search":
            if len(sys.argv) > 2:
                search_projects(" ".join(sys.argv[2:]))
        else:
            print(f"Unknown command: {cmd}")
    else:
        interactive_mode()
