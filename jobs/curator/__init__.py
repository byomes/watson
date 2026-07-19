"""jobs/curator — book-tracking/discovery app (Mel + daughters). Own DB, separate from watson.db/congregation.db."""
import os
import sqlite3
from pathlib import Path

import requests

from core.vacation import vacation_gate

DB = Path.home() / "watson" / "data" / "curator.db"

SPICE_SCALE = {
    0: "Clean",
    1: "Kissing Only",
    2: "Closed Door",
    3: "Fade to Black",
    4: "Open Door",
    5: "Explicit",
}
DEFAULT_SPICE_MAX = 3

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bootstrap_db() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS books (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                title                   TEXT NOT NULL,
                author                  TEXT NOT NULL,
                series                  TEXT,
                series_number           INTEGER,
                page_count              INTEGER,
                spice_rating            INTEGER,
                spice_notes             TEXT,
                kindle_unlimited        INTEGER NOT NULL DEFAULT 0,
                kindle_unlimited_checked_at TEXT,
                status                  TEXT NOT NULL DEFAULT 'pending',
                added_by                INTEGER REFERENCES users(id),
                created_at              TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS book_sources (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id           INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                type              TEXT NOT NULL,
                url               TEXT,
                raw_extracted_text TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS reading_status (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                user_id       INTEGER NOT NULL REFERENCES users(id),
                shelf         TEXT NOT NULL DEFAULT 'want_to_read',
                rating        INTEGER,
                date_started  TEXT,
                date_finished TEXT,
                notes         TEXT,
                UNIQUE(book_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
            CREATE INDEX IF NOT EXISTS idx_books_spice ON books(spice_rating);
            CREATE INDEX IF NOT EXISTS idx_book_sources_book ON book_sources(book_id);
            CREATE INDEX IF NOT EXISTS idx_reading_status_user ON reading_status(user_id);
            CREATE INDEX IF NOT EXISTS idx_reading_status_book ON reading_status(book_id);
        """)


def send_telegram(text: str, reply_markup: dict | None = None) -> int | None:
    """Returns the sent message's Telegram message_id, or None if not sent."""
    if vacation_gate("normal", "jobs.curator", text):
        return None
    token = _BOT_TOKEN()
    chat_id = _CHAT_ID()
    if not (token and chat_id):
        return None
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")
    except Exception:
        return None
