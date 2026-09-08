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

            CREATE TABLE IF NOT EXISTS location_zones (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE,
                center_lat   REAL NOT NULL,
                center_lon   REAL NOT NULL,
                radius_m     REAL NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS location_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tst          INTEGER,
                received_at  TEXT NOT NULL DEFAULT (datetime('now')),
                zone_from    TEXT,
                zone_to      TEXT,
                lat          REAL,
                lon          REAL
            );
        """)
        # Seeded from the first day of real ping data: a tight overnight
        # cluster (97/102 pings, spread ~15-20m) at this centroid — Bill's
        # home. Radius gives room for normal GPS drift (observed acc 2-6m)
        # without reaching the street.
        conn.execute(
            "INSERT OR IGNORE INTO location_zones (name, center_lat, center_lon, radius_m) "
            "VALUES ('Home', 39.6277664948454, -75.7562382989691, 100)"
        )
