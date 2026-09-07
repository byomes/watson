"""jobs/location — phone GPS ping ingestion (OwnTracks HTTP mode) and history."""
import sqlite3
from pathlib import Path

DB = Path.home() / "watson" / "data" / "watson.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS location_pings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tid          TEXT,
                tst          INTEGER,
                received_at  TEXT NOT NULL DEFAULT (datetime('now')),
                lat          REAL NOT NULL,
                lon          REAL NOT NULL,
                acc          REAL,
                alt          REAL,
                vel          REAL,
                batt         REAL,
                conn_type    TEXT,
                raw_json     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_location_pings_tst ON location_pings(tst);
        """)
