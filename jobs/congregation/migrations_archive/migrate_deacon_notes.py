"""Add deacon_notes table to congregation.db and split it off from
follow_ups.

follow_ups is read by pastoral-only tooling (pastoral_reports.py,
state_of_church.py, gcal/pre_meeting_brief.py, monthly_state_report.py)
and written to by connect-card intake -- none of that is meant for deacon
eyes. deacon_notes is the new deacon-facing log (see deacons_web.py):
same shape minus card_id, since a deacon-logged note never originates
from a connect card. This migration copies over any follow_ups rows that
were actually deacon-authored (card_id IS NULL -- the connect-card-intake
rows all have card_id set) rather than moving them, so pastoral-report
historical counts against follow_ups don't change.

Usage:
  python3 jobs/congregation/migrate_deacon_notes.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deacon_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id  INTEGER NOT NULL REFERENCES members(id),
            note       TEXT NOT NULL,
            status     TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # Idempotent: only copies a follow_ups row once (guards on a row with
    # the same member_id/note/created_at not already existing).
    rows = conn.execute(
        "SELECT member_id, note, status, created_at FROM follow_ups WHERE card_id IS NULL"
    ).fetchall()
    copied = 0
    for member_id, note, status, created_at in rows:
        existing = conn.execute(
            "SELECT 1 FROM deacon_notes WHERE member_id = ? AND note = ? AND created_at = ?",
            (member_id, note, created_at),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO deacon_notes (member_id, note, status, created_at) VALUES (?, ?, ?, ?)",
            (member_id, note, status, created_at),
        )
        copied += 1

    conn.commit()
    print(f"Done: deacon_notes table ready, {copied} row(s) copied from follow_ups.")
except Exception as e:
    print(f"Failed: {e}")
    raise
finally:
    conn.close()
