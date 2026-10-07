"""jobs/uploads/watcher.py -- cron (*/5 * * * *): scans INBOX_DIR directly
(not just rows jobs/uploads/api.py wrote) so it also catches files dropped
in by other means (scp, OneDrive sync, manual copy), not just the
wtsn.me/upload web form. Tells Bill about anything new over Telegram, with
whatever project note was attached at upload time if there is one.

    PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.uploads.watcher
"""
import logging
import os
import sqlite3
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.uploads.api import DB_PATH, INBOX_DIR, _ensure_table

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _ensure_seen_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_watcher_seen (
            filename TEXT PRIMARY KEY,
            seen_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _note_for(conn, filename: str) -> str | None:
    row = conn.execute("SELECT note FROM uploads WHERE filename = ?", (filename,)).fetchone()
    return row["note"] if row else None


def _notify(filename: str, note: str | None) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Skipping Telegram notify for %s -- Telegram not configured", filename)
        return
    text = f"\U0001F4E5 New upload: {filename}"
    if note:
        text += f"\nNote: {note}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.error("Telegram notify failed for %s: %s", filename, exc)


def main() -> None:
    if not os.path.isdir(INBOX_DIR):
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        _ensure_seen_table(conn)
        conn.commit()

        seen = {row["filename"] for row in conn.execute("SELECT filename FROM upload_watcher_seen")}

        for name in sorted(os.listdir(INBOX_DIR)):
            path = os.path.join(INBOX_DIR, name)
            if name in seen or not os.path.isfile(path):
                continue

            note = _note_for(conn, name)
            _notify(name, note)

            conn.execute(
                "INSERT OR IGNORE INTO upload_watcher_seen (filename, seen_at) VALUES (?, datetime('now'))",
                (name,),
            )
            conn.commit()
            log.info("Notified Bill about new upload: %s", name)


if __name__ == "__main__":
    main()
