"""jobs/servantcare/schema.py — sc_listings / sc_listing_images tables in the
dedicated ~/watson/data/servantcare.db file."""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/servantcare.db")

CREATE_SC_LISTINGS = """
CREATE TABLE IF NOT EXISTS sc_listings (
    pid                 INTEGER PRIMARY KEY,
    chandle             TEXT NOT NULL,
    name                TEXT NOT NULL,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    bedrooms            INTEGER,
    beds                INTEGER,
    bathrooms           INTEGER,
    max_sleeps          INTEGER,
    max_stay_nights     INTEGER,
    description         TEXT,
    allergy_alert       TEXT,
    amenities_json       TEXT,
    pricing_json        TEXT,
    price_summary       TEXT,
    latitude            REAL,
    longitude           REAL,
    source_url          TEXT NOT NULL,
    primary_image_path  TEXT,
    scraped_at          TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_SC_LISTING_IMAGES = """
CREATE TABLE IF NOT EXISTS sc_listing_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pid         INTEGER NOT NULL REFERENCES sc_listings(pid) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    local_path  TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    UNIQUE (pid, seq)
)
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.execute(CREATE_SC_LISTINGS)
        conn.execute(CREATE_SC_LISTING_IMAGES)


if __name__ == "__main__":
    create_tables()
    print(f"servantcare.db ready at {DB_PATH}")
