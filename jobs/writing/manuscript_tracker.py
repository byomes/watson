"""jobs/writing/manuscript_tracker.py — track manuscript drafts, word counts, and diffs."""
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]

import os
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))


def _get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manuscripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_path TEXT,
            word_count INTEGER DEFAULT 0,
            snapshot TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _word_count(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text))


def _compute_diff(old_text: str, new_text: str) -> dict:
    try:
        import diff_match_patch as dmp_module
        dmp = dmp_module.diff_match_patch()
        diffs = dmp.diff_main(old_text, new_text)
        dmp.diff_cleanupSemantic(diffs)
        added = sum(len(t) for op, t in diffs if op == 1)
        removed = sum(len(t) for op, t in diffs if op == -1)
        return {"added_chars": added, "removed_chars": removed, "diff_count": len(diffs)}
    except ImportError:
        old_words = set(old_text.split())
        new_words = set(new_text.split())
        return {
            "added_chars": len(new_text) - len(old_text),
            "removed_chars": 0,
            "diff_count": len(new_words - old_words),
        }


def add_manuscript(title: str, file_path: str = None) -> str:
    content = ""
    if file_path:
        p = Path(file_path).expanduser()
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="ignore")

    wc = _word_count(content)
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO manuscripts (title, file_path, word_count, snapshot) VALUES (?, ?, ?, ?)",
            (title, file_path, wc, content[:10000]),
        )
        conn.commit()
        return f"Manuscript '{title}' added ({wc} words)."
    finally:
        conn.close()


def update_manuscript(title: str, file_path: str = None, new_text: str = None) -> str:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM manuscripts WHERE title=? ORDER BY id DESC LIMIT 1", (title,)
        ).fetchone()
        if not row:
            return f"No manuscript found: {title}"

        if file_path:
            p = Path(file_path).expanduser()
            new_text = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else new_text or ""
        elif not new_text:
            return "Provide file_path or new_text."

        old_snapshot = row["snapshot"] or ""
        diff = _compute_diff(old_snapshot, new_text)
        wc = _word_count(new_text)
        wc_delta = wc - row["word_count"]

        conn.execute(
            "UPDATE manuscripts SET word_count=?, snapshot=?, updated_at=? WHERE id=?",
            (wc, new_text[:10000], datetime.utcnow().isoformat(), row["id"]),
        )
        conn.commit()

        sign = "+" if wc_delta >= 0 else ""
        return (
            f"'{title}' updated: {wc} words ({sign}{wc_delta}), "
            f"+{diff['added_chars']} chars added, -{diff['removed_chars']} removed."
        )
    finally:
        conn.close()


def list_manuscripts() -> str:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT title, word_count, updated_at FROM manuscripts ORDER BY updated_at DESC").fetchall()
        if not rows:
            return "No manuscripts tracked."
        lines = [f"Manuscripts ({len(rows)}):"]
        for r in rows:
            lines.append(f"  {r['title']}: {r['word_count']} words — updated {r['updated_at'][:10]}")
        return "\n".join(lines)
    finally:
        conn.close()


def run(message: str = None) -> str:
    if not message:
        return list_manuscripts()

    msg = message.strip()
    if msg.lower() in ("list", "all", "show"):
        return list_manuscripts()

    # update <title> from <file>
    m = re.match(r'update\s+(.+?)\s+from\s+(.+)', msg, re.IGNORECASE)
    if m:
        return update_manuscript(m.group(1).strip(), file_path=m.group(2).strip())

    # add <title> from <file> / add <title>
    m = re.match(r'add\s+(.+?)\s+from\s+(.+)', msg, re.IGNORECASE)
    if m:
        return add_manuscript(m.group(1).strip(), file_path=m.group(2).strip())

    m = re.match(r'add\s+(.+)', msg, re.IGNORECASE)
    if m:
        return add_manuscript(m.group(1).strip())

    return list_manuscripts()
