"""Creates the kids-attendance tables in congregation.db: kids, kids_checkin,
and kids_household_review_queue. Backs the Kids Checkin backlog/weekly
import (jobs/congregation/kids_checkin_client.py + kids_checkin_import.py),
started 2026-09-29 at Bill's request to track kids-class attendance
alongside adult attendance, sourced from Subsplash's checkin system since
kids never fill out a connect card.

kids: one row per child ever checked in, keyed by Subsplash's own stable
per-profile id (subsplash_profile_id) so the same kid across many Sundays
never gets re-created. household_id is left NULL at creation time -- per
Bill's 2026-09-29 directive, an unmatched kid's record is created
immediately with no review gate, and household linking happens later via
Donna (see kids_household_review_queue + notify_donna_kids_checkin_review.py).

kids_checkin: one row per child per event instance (mirrors how the adult
`attendance` table handles a person attending different campuses on
different dates -- a kid's current classroom is just their most recent row,
nothing to migrate as they graduate rooms).

kids_household_review_queue: staging area for household-link candidates
Watson finds by matching the checking-in guardian's phone/email against
`members`. Never auto-applied -- Donna approves each one via Telegram
(gated to the day AFTER the row was created, and only 9am-8pm, per
[[feedback_no_messages_after_8pm_standing]] and donna_notify.py's window).

Usage:
  python3 jobs/congregation/migrate_kids_checkin_tables.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subsplash_profile_id TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT,
            gender TEXT,
            household_id TEXT,
            created_via TEXT NOT NULL DEFAULT 'checkin_only',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kids_checkin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kid_id INTEGER NOT NULL REFERENCES kids(id),
            subsplash_checkin_id TEXT UNIQUE NOT NULL,
            event_id TEXT NOT NULL,
            class_name TEXT,
            event_date TEXT NOT NULL,
            checked_in_at TEXT,
            campus TEXT,
            guardian_name TEXT,
            guardian_phone TEXT,
            guardian_email TEXT,
            checkin_source TEXT NOT NULL DEFAULT 'subsplash_backlog',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kids_checkin_kid_id ON kids_checkin(kid_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kids_checkin_event_date ON kids_checkin(event_date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kids_household_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kid_id INTEGER NOT NULL REFERENCES kids(id),
            candidate_household_id TEXT,
            candidate_member_id INTEGER,
            match_reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            sent_at TEXT
        )
    """)
    conn.commit()
    print("kids / kids_checkin / kids_household_review_queue ready.")
finally:
    conn.close()
