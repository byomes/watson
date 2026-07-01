"""jobs/publishing — consolidated Writing Room / ARC / TWJ Reader admin (replaces watson-admin)."""
import secrets
import sqlite3
from pathlib import Path

import bcrypt

DB = Path.home() / "watson" / "data" / "watson.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS twj_readers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                password_hash TEXT NOT NULL,
                status        TEXT DEFAULT 'active',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login    TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS twj_feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                reader_id  INTEGER REFERENCES twj_readers(id),
                chapter    TEXT,
                feedback   TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # `name` wasn't in the original spec'd schema but the KV records had one
        # (used for the reader-facing greeting) — add it idempotently.
        try:
            conn.execute("ALTER TABLE twj_readers ADD COLUMN name TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists


def generate_reader_username(first_name: str, last_name: str, conn: sqlite3.Connection) -> str:
    """firstname.lastname, deduped with a numeric suffix — matches the pre-existing KV scheme."""
    base = f"{first_name.lower().strip()}.{last_name.lower().strip()}"
    username = base
    suffix = 1
    while conn.execute("SELECT 1 FROM twj_readers WHERE username = ?", (username,)).fetchone():
        username = f"{base}{suffix}"
        suffix += 1
    return username


def generate_reader_password(length: int = 16) -> str:
    return secrets.token_urlsafe(length)[:length]


def set_reader_password(reader_id: int, new_password: str) -> None:
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with get_db() as conn:
        conn.execute(
            "UPDATE twj_readers SET password_hash = ? WHERE id = ?", (pw_hash, reader_id)
        )
