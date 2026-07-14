"""Add meeting_reviews and meeting_review_action_items tables to watson.db.

Supports the Fireflies elder-meeting dashboard review flow (jobs/meet/
fireflies_review.py, jobs/dashboard/app.py's /meet/review/* routes), which
replaces the earlier Telegram go/cancel draft approval. That earlier flow's
now-dead fireflies_review_pending table is dropped here too — same pattern
as retiring thesis_token_health when it was superseded.

owner_member_id on meeting_review_action_items is a plain INTEGER, not a
real FK constraint: it points at congregation.db's members table, which is
a separate SQLite file. Cross-database FKs aren't possible in SQLite, so
this is an app-level lookup (jobs/meet/fireflies_review.get_member_name()).

Usage:
  python3 jobs/meet/migrate_meeting_reviews.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_reviews (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            fireflies_meeting_id TEXT NOT NULL,
            title                TEXT,
            meeting_date         TEXT,
            summary_text         TEXT,
            status               TEXT NOT NULL DEFAULT 'pending_review',
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            sent_at              TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_review_action_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id       INTEGER NOT NULL REFERENCES meeting_reviews(id),
            owner_text      TEXT,
            owner_member_id INTEGER,
            item_text       TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("DROP TABLE IF EXISTS fireflies_review_pending")
    conn.commit()
    print("Done: meeting_reviews + meeting_review_action_items ready; fireflies_review_pending dropped.")
except Exception as e:
    print(f"Migration error: {e}")
finally:
    conn.close()
