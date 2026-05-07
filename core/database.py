import sqlite3
from config.settings import DB_PATH


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name   TEXT NOT NULL,
                source_type   TEXT NOT NULL CHECK(source_type IN ('article', 'podcast', 'publication', 'journal')),
                title         TEXT NOT NULL,
                url           TEXT,
                summary       TEXT,
                published_date TEXT,
                fetched_date  TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'new'
                                  CHECK(status IN ('new', 'sent_to_broadcaster', 'archived', 'dismissed'))
            );

            CREATE TABLE IF NOT EXISTS library (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type  TEXT NOT NULL CHECK(content_type IN ('sermon', 'bible_study', 'voice_note', 'transcript', 'research')),
                title         TEXT NOT NULL,
                body          TEXT,
                tags          TEXT,
                bible_passage TEXT,
                date_created  TEXT,
                date_indexed  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS voice_notes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript    TEXT,
                tags          TEXT,
                theme         TEXT,
                date_captured TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'new'
                                  CHECK(status IN ('new', 'reviewed'))
            );
            CREATE TABLE IF NOT EXISTS reading_list (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                url           TEXT,
                source_name   TEXT,
                source_type   TEXT,
                summary       TEXT,
                date_added    TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'unread'
                                  CHECK(status IN ('unread', 'reading', 'finished'))
            );
        """)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
