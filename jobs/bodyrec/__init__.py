"""jobs/bodyrec — body recomposition tracker (bill/mel profiles)."""
import sqlite3
from pathlib import Path

DB = Path.home() / "watson" / "data" / "watson.db"

PROFILES = ("bill", "mel")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS body_entries (
                id           TEXT PRIMARY KEY,
                profile      TEXT NOT NULL,
                date         TEXT NOT NULL,
                weight       REAL,
                neck         REAL,
                waist        REAL,
                hip          REAL,
                height       REAL,
                fat_percent  REAL,
                fat_lbs      REAL,
                lean_lbs     REAL,
                notes        TEXT
            );

            CREATE TABLE IF NOT EXISTS body_settings (
                profile           TEXT PRIMARY KEY,
                height            REAL,
                goal_fat_percent  REAL,
                goal_weight       REAL
            );
        """)
