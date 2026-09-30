"""Adds kids.current_class -- a persistent, leader-editable classroom
assignment, replacing "most recent kids_checkin.class_name" as the source
of truth for which class a kid shows up under when they aren't present on
the selected date.

Why: the derived-from-history approach meant a kid who aged out or left
entirely had no way to ever stop showing up in the Kids Attendance tool --
their last real checkin just kept being treated as "current" forever.
current_class is NULL-able specifically so a leader can clear it (the
wtsn.me/cat/kidsatt "X" button / jobs/congregation/kids_attendance_web.py's
remove()) to take a kid out of the tool's view entirely, not just move
them elsewhere. kids_checkin_import.py keeps it in sync with real Subsplash
activity going forward (a genuine new checkin is real evidence of current
classroom and should un-clear a prior removal); kids_attendance_web.py's
move() updates it when a leader manually moves someone.

Backfill: seeds every existing kid's current_class from their most recent
kids_checkin.class_name, so nothing changes for anyone until a leader
actually edits or removes it.

Usage:
  python3 jobs/congregation/migrate_kids_current_class.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(kids)").fetchall()}
    if "current_class" not in existing:
        conn.execute("ALTER TABLE kids ADD COLUMN current_class TEXT")
        print("  [migrated] kids.current_class")
    else:
        print("  [exists]   kids.current_class")

    rows = conn.execute(
        """SELECT kid_id, class_name FROM kids_checkin kc
           WHERE event_date = (SELECT MAX(event_date) FROM kids_checkin WHERE kid_id = kc.kid_id)"""
    ).fetchall()
    for kid_id, class_name in rows:
        conn.execute(
            "UPDATE kids SET current_class = ? WHERE id = ? AND current_class IS NULL", (class_name, kid_id)
        )
    conn.commit()
    print(f"Backfilled current_class for up to {len(rows)} kids.")
finally:
    conn.close()
