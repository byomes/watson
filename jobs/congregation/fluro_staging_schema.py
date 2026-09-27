"""jobs/congregation/fluro_staging_schema.py -- schema for the Fluro (Subsplash
admin) staged-pull database.

Deliberately a SEPARATE database file from congregation.db (Bill's explicit
instruction, 2026-09-26): everything pulled from Fluro lands here first.
Nothing here is ever written into congregation.db automatically -- Donna
reviews anything that looks like a real conflict (jobs/congregation/
notify_donna_fluro_review.py) before fluro_pull.py's apply step touches the
real member records. See jobs/congregation/fluro_pull.py for the pull logic
and jobs/congregation/fluro_client.py for how the data is actually fetched
(live, authenticated, via the dedicated Android gateway phone's own Chrome
session over ADB/Tailscale -- see that module's docstring).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "watson" / "data" / "fluro_staging.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables() -> None:
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fluro_pull_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            contacts_pulled INTEGER,
            serving_pulled INTEGER,
            status TEXT NOT NULL DEFAULT 'running'  -- running | done | failed
        );

        CREATE TABLE IF NOT EXISTS fluro_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fluro_id TEXT NOT NULL UNIQUE,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            dob TEXT,
            gender TEXT,
            marital_status TEXT,
            fluro_status TEXT,
            household_id TEXT,
            household_role TEXT,
            tags TEXT,              -- JSON array of tag titles
            realms TEXT,            -- JSON array of realm/campus titles
            raw_json TEXT,          -- full Fluro record, for fields not modeled above
            pulled_at TEXT NOT NULL,
            match_status TEXT NOT NULL DEFAULT 'unmatched',
                -- new | clean_fill | conflict | exact_no_change | possible_duplicate
            matched_member_id INTEGER,
            match_method TEXT,      -- email | phone | fuzzy | NULL
            conflict_fields TEXT,   -- JSON: {field: {"fluro": x, "existing": y}}
            review_status TEXT NOT NULL DEFAULT 'pending'  -- pending | approved | rejected | not_needed
        );

        CREATE TABLE IF NOT EXISTS fluro_serving (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fluro_team_id TEXT NOT NULL,
            team_title TEXT,
            assignment_title TEXT,     -- role name within the team, e.g. "Vocalist"
            fluro_contact_id TEXT NOT NULL,
            contact_first_name TEXT,
            contact_last_name TEXT,
            matched_member_id INTEGER,
            pulled_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fluro_contacts_match_status ON fluro_contacts(match_status);
        CREATE INDEX IF NOT EXISTS idx_fluro_contacts_review_status ON fluro_contacts(review_status);
        CREATE INDEX IF NOT EXISTS idx_fluro_serving_contact ON fluro_serving(fluro_contact_id);
        """
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_tables()
    print(f"fluro_staging.db ready at {DB_PATH}")
