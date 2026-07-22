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

# Curator users.name -> watson.db people.name, for SMS/email notifications. Exact
# mapping only, deliberately not fuzzy — a wrong guess here means texting/emailing the
# wrong person. Add an entry per daughter as they're onboarded.
_CONTACT_NAME_MAP = {"mel": "Melanie Yomes"}


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
                cover_image_url         TEXT,
                series_total            INTEGER,
                description             TEXT,
                kindle_unlimited        INTEGER,
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

            CREATE TABLE IF NOT EXISTS ingest_batches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                submitted_by    INTEGER REFERENCES users(id),
                total_jobs      INTEGER NOT NULL DEFAULT 0,
                completed_jobs  INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'running',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS ingest_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id         INTEGER REFERENCES books(id) ON DELETE SET NULL,
                batch_id        INTEGER REFERENCES ingest_batches(id) ON DELETE SET NULL,
                status          TEXT NOT NULL DEFAULT 'queued',
                input_type      TEXT NOT NULL,
                input_raw       TEXT,
                image_blob      BLOB,
                image_mimetype  TEXT,
                submitted_by    INTEGER REFERENCES users(id),
                error_message   TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            -- Attributed spice-content findings, one row per trusted source (see
            -- research.py's ranked source list). Excerpts are verbatim windows pulled
            -- from the source's own page text, not an Watson-authored summary — the
            -- detail page quotes these directly, attributed by source_name.
            CREATE TABLE IF NOT EXISTS spice_findings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                source_name   TEXT NOT NULL,
                source_type   TEXT NOT NULL,
                rank          INTEGER NOT NULL,
                excerpt       TEXT NOT NULL,
                url           TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
            CREATE INDEX IF NOT EXISTS idx_books_spice ON books(spice_rating);
            CREATE INDEX IF NOT EXISTS idx_book_sources_book ON book_sources(book_id);
            CREATE INDEX IF NOT EXISTS idx_reading_status_user ON reading_status(user_id);
            CREATE INDEX IF NOT EXISTS idx_reading_status_book ON reading_status(book_id);
            CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status ON ingest_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_ingest_jobs_batch ON ingest_jobs(batch_id);
            CREATE INDEX IF NOT EXISTS idx_spice_findings_book ON spice_findings(book_id);
        """)
        for alter_sql in [
            "ALTER TABLE books ADD COLUMN cover_image_url TEXT",
            "ALTER TABLE books ADD COLUMN series_total INTEGER",
            "ALTER TABLE books ADD COLUMN description TEXT",
        ]:
            try:
                conn.execute(alter_sql)
                conn.commit()
            except Exception:
                pass  # column already exists
        try:
            conn.execute("ALTER TABLE books DROP COLUMN spice_summary")
            conn.commit()
        except Exception:
            pass  # already dropped, or never existed on a fresh DB
        _migrate_kindle_unlimited_nullable(conn)


def _migrate_kindle_unlimited_nullable(conn: sqlite3.Connection) -> None:
    """kindle_unlimited was originally INTEGER NOT NULL DEFAULT 0 — a strict
    boolean that collapsed "confirmed not on KU" and "never managed to check"
    (e.g. Amazon's bot-block page, confirmed 2026-07-22 to return HTTP 200
    with block-page content indistinguishable from a real "no KU badge"
    listing under the old boolean-only check) into the same False value.
    Rebuilds the table with kindle_unlimited nullable (NULL = unknown) if it's
    still the old NOT NULL shape — SQLite has no ALTER COLUMN, so this is the
    standard create-copy-drop-rename migration. Idempotent: checked via
    PRAGMA table_info, no-ops once already migrated."""
    cols = conn.execute("PRAGMA table_info(books)").fetchall()
    ku_col = next((c for c in cols if c[1] == "kindle_unlimited"), None)
    if ku_col is None or ku_col[3] == 0:
        return  # column missing (shouldn't happen), or already nullable
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript("""
        ALTER TABLE books RENAME TO books_old_ku_migration;

        CREATE TABLE books (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            title                   TEXT NOT NULL,
            author                  TEXT NOT NULL,
            series                  TEXT,
            series_number           INTEGER,
            page_count              INTEGER,
            spice_rating            INTEGER,
            spice_notes             TEXT,
            kindle_unlimited        INTEGER,
            kindle_unlimited_checked_at TEXT,
            status                  TEXT NOT NULL DEFAULT 'pending',
            added_by                INTEGER REFERENCES users(id),
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            cover_image_url         TEXT,
            series_total            INTEGER,
            description             TEXT
        );

        INSERT INTO books (
            id, title, author, series, series_number, page_count, spice_rating,
            spice_notes, kindle_unlimited, kindle_unlimited_checked_at, status,
            added_by, created_at, cover_image_url, series_total, description
        )
        SELECT
            id, title, author, series, series_number, page_count, spice_rating,
            spice_notes, kindle_unlimited, kindle_unlimited_checked_at, status,
            added_by, created_at, cover_image_url, series_total, description
        FROM books_old_ku_migration;

        DROP TABLE books_old_ku_migration;
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


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


def resolve_user_contact(curator_user_name: str) -> dict | None:
    """Map a curator users.name to their watson.db people contact (phone/email).
    Exact mapping via _CONTACT_NAME_MAP only — never fuzzy-matches which real person a
    short name refers to."""
    full_name = _CONTACT_NAME_MAP.get((curator_user_name or "").strip().lower())
    if not full_name:
        return None
    from core.database import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name, phone, email FROM people WHERE name = ?", (full_name,)
        ).fetchone()
    return dict(row) if row else None
