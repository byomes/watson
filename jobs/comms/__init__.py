"""jobs/comms — Comms Desk: Kaci's login-gated Facebook/email composer.

Reuses the book-launch campaign send infrastructure (jobs/campaigns/dispatch.py,
jobs/campaigns/brevo_dispatcher.py, jobs/facebook/facebook_post.py) under one
evergreen campaign_id='general-comms' row in book_launch_campaigns. This module
only owns what's genuinely new: comms_users auth, comms_holds (the "send now"
undo window), and comms_reset_tokens.
"""
import os
import secrets
import sqlite3
from pathlib import Path

import requests

from core.vacation import vacation_gate

DB = Path.home() / "watson" / "data" / "watson.db"

GENERAL_COMMS_CAMPAIGN_ID = "general-comms"

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS comms_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                role          TEXT NOT NULL CHECK(role IN ('volunteer','admin')),
                created_at    TEXT DEFAULT (datetime('now')),
                last_login    TEXT
            );

            CREATE TABLE IF NOT EXISTS comms_holds (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                send_id      INTEGER NOT NULL REFERENCES book_launch_sends(id),
                held_until   TEXT NOT NULL,
                canceled_at  TEXT,
                released_at  TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS comms_reset_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES comms_users(id),
                token      TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used_at    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # book_launch_sends: two nullable additions so Comms Desk content is
        # distinguishable from real launch-campaign content in reporting.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(book_launch_sends)").fetchall()}
        if "source" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN source TEXT DEFAULT 'campaign'")
        if "author_user_id" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN author_user_id INTEGER")
        if "send_time" not in cols:
            # Nullable 'HH:MM' (24h, local wall-clock time) — NULL preserves the
            # original date-only behavior (fires as soon as send_date is due) for
            # every pre-existing row and for any campaign that never sets it.
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN send_time TEXT")

        # Evergreen campaign row Comms Desk writes under. book_launch_campaigns.
        # launch_date/framework_weeks are NOT NULL in jobs/campaigns/schema.py —
        # the original spec's NULL/NULL proposal would violate that, so this uses
        # a placeholder creation date and framework_weeks=0 instead. Neither value
        # is read for a non-launch campaign (dispatch.py never touches them).
        conn.execute(
            """INSERT OR IGNORE INTO book_launch_campaigns
               (campaign_id, book_title, launch_date, start_date, framework_weeks, status)
               VALUES (?, 'General Comms', date('now'), date('now'), 0, 'active')""",
            (GENERAL_COMMS_CAMPAIGN_ID,),
        )
        conn.commit()


def send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.comms", text):
        return
    token, chat_id = _BOT_TOKEN(), _CHAT_ID()
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def generate_password(word_count: int = 3) -> str:
    """E.g. 'TigerMapleRiver' — capitalized dictionary words, no separator."""
    wordlist_path = Path(__file__).parent / "wordlist.txt"
    with open(wordlist_path) as f:
        words = [w.strip() for w in f if w.strip()]
    chosen = [secrets.choice(words) for _ in range(word_count)]
    return "".join(w.capitalize() for w in chosen)
