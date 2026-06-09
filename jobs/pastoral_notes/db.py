import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path.home() / "watson" / "data" / "watson.db"
DB_PATH = Path(os.getenv("WATSON_DB", str(_DEFAULT_DB)))


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes_pending (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id          TEXT UNIQUE,
                appointment_title TEXT,
                appointment_time  TEXT,
                prompted_at       TEXT,
                reminded_at       TEXT,
                status            TEXT DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pastoral_notes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id         INTEGER,
                event_id          TEXT,
                appointment_title TEXT,
                appointment_time  TEXT,
                note_text         TEXT,
                created_at        TEXT,
                FOREIGN KEY (person_id) REFERENCES people(id)
            )
        """)


create_tables()
