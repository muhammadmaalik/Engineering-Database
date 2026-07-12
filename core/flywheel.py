"""Training-data flywheel: log turns, mark good, export with project_id."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from . import vault_index


def log_turn(
    query: str,
    response: str,
    project_id: str | None = None,
    *,
    timestamp: str | None = None,
    db_path: Path | None = None,
) -> tuple[int, int]:
    """Append QUERY + RESPONSE to message_log. Returns (query_id, response_id)."""
    vault_index.ensure_tables(db_path)
    ts = timestamp or datetime.now().strftime("%c")
    db = vault_index.get_db(db_path)
    try:
        cur = db.execute(
            """
            INSERT INTO message_log
                (timestamp, source_id, target_id, type, type_name, payload, payload_size, project_id)
            VALUES (?, 0, 0, 3, 'QUERY', ?, ?, ?)
            """,
            (ts, query, len(query), project_id),
        )
        query_id = int(cur.lastrowid)
        cur = db.execute(
            """
            INSERT INTO message_log
                (timestamp, source_id, target_id, type, type_name, payload, payload_size, project_id)
            VALUES (?, 0, 0, 3, 'RESPONSE', ?, ?, ?)
            """,
            (ts, response, len(response), project_id),
        )
        response_id = int(cur.lastrowid)
        db.commit()
        return query_id, response_id
    finally:
        db.close()


def mark_good(
    message_log_id: int | None = None,
    *,
    pair: bool = True,
    db_path: Path | None = None,
) -> list[int]:
    """Mark a message (or last QUERY+RESPONSE pair) as good for training.

    If message_log_id is None, uses the latest RESPONSE (and its QUERY when pair=True).
    Returns list of curation message_log_ids written.
    """
    vault_index.ensure_tables(db_path)
    db = vault_index.get_db(db_path)
    try:
        ids: list[int] = []
        if message_log_id is None:
            row = db.execute(
                "SELECT id, type_name FROM message_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return []
            last_id, type_name = int(row[0]), row[1]
            if type_name == "RESPONSE" and pair:
                prev = db.execute(
                    "SELECT id FROM message_log WHERE id = ? AND type_name = 'QUERY'",
                    (last_id - 1,),
                ).fetchone()
                if prev:
                    ids.append(int(prev[0]))
                ids.append(last_id)
            else:
                ids.append(last_id)
        else:
            ids.append(int(message_log_id))
            if pair:
                row = db.execute(
                    "SELECT type_name FROM message_log WHERE id = ?",
                    (message_log_id,),
                ).fetchone()
                if row and row[0] == "RESPONSE":
                    prev = db.execute(
                        "SELECT id FROM message_log WHERE id = ? AND type_name = 'QUERY'",
                        (message_log_id - 1,),
                    ).fetchone()
                    if prev:
                        ids.insert(0, int(prev[0]))

        now = datetime.now().isoformat()
        curated: list[int] = []
        for mid in ids:
            db.execute(
                """
                INSERT INTO curation (message_log_id, label, curated_at)
                VALUES (?, 'good', ?)
                ON CONFLICT(message_log_id) DO UPDATE SET
                    label = excluded.label,
                    curated_at = excluded.curated_at
                """,
                (mid, now),
            )
            curated.append(mid)
        db.commit()
        return curated
    finally:
        db.close()


def mark_last_pair_good(db_path: Path | None = None) -> list[int]:
    return mark_good(None, pair=True, db_path=db_path)


def export_jsonl(
    output_path: str | Path | None = None,
    *,
    label_filter: str | None = "good",
    project_id: str | None = None,
    db_path: Path | None = None,
) -> Path:
    """Export curated messages as JSONL with project_id in metadata.

    Each line:
      {"timestamp", "type", "payload", "label", "metadata": {"project_id": ...}, ...}
    """
    vault_index.ensure_tables(db_path)
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = paths.EXPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"dataset_{ts}.jsonl"
    output_path = Path(output_path)

    db = vault_index.get_db(db_path)
    try:
        query = """
            SELECT m.timestamp, m.type_name, m.payload, c.label, c.correction, m.project_id
            FROM message_log m
            JOIN curation c ON m.id = c.message_log_id
            WHERE 1=1
        """
        params: list[Any] = []
        if label_filter:
            query += " AND c.label = ?"
            params.append(label_filter)
        if project_id:
            query += " AND m.project_id = ?"
            params.append(project_id)
        query += " ORDER BY m.timestamp, m.id"

        try:
            rows = db.execute(query, params).fetchall()
        except sqlite3.OperationalError:
            rows = []

        with output_path.open("w", encoding="utf-8") as f:
            for ts, mtype, payload, label, correction, pid in rows:
                record: dict[str, Any] = {
                    "timestamp": ts,
                    "type": mtype,
                    "payload": payload,
                    "label": label,
                    "metadata": {"project_id": pid},
                }
                if correction:
                    record["correction"] = correction
                f.write(json.dumps(record) + "\n")
    finally:
        db.close()
    return output_path


def export_pairs_jsonl(
    output_path: str | Path | None = None,
    *,
    project_id: str | None = None,
    db_path: Path | None = None,
) -> Path:
    """Export QUERY/RESPONSE pairs with project_id metadata for LoRA."""
    vault_index.ensure_tables(db_path)
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = paths.EXPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"pairs_{ts}.jsonl"
    output_path = Path(output_path)

    db = vault_index.get_db(db_path)
    try:
        # Prefer adjacent QUERY/RESPONSE in message_log so project_id is available.
        sql = """
            SELECT q.payload, r.payload, q.project_id, q.timestamp
            FROM message_log q
            JOIN message_log r ON r.id = q.id + 1
            WHERE q.type_name = 'QUERY' AND r.type_name = 'RESPONSE'
        """
        params: list[Any] = []
        if project_id:
            sql += " AND q.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY q.id"
        rows = db.execute(sql, params).fetchall()

        with output_path.open("w", encoding="utf-8") as f:
            for query, response, pid, ts in rows:
                record = {
                    "instruction": query,
                    "output": response,
                    "metadata": {"project_id": pid, "timestamp": ts},
                }
                f.write(json.dumps(record) + "\n")
    finally:
        db.close()
    return output_path
