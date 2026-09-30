"""Adds kids.member_id, linking each Kids Checkin child to a real row in
`members` -- per Bill's 2026-09-29 correction: a kid living only in the
separate `kids` table is invisible to CatalystDB (jobs/congregation/
catalystdb_web.py queries `members` only), so household composition looks
wrong there even after a kid is correctly linked in `kids.household_id`.
`kids` stays the canonical Subsplash-profile-keyed tracking table (needed
for the checkin-history/review-queue machinery); `members` is now kept in
sync alongside it, household_role='child', so every kid actually shows up
in CatalystDB under their real household.

Usage:
  python3 jobs/congregation/migrate_kids_member_id.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(kids)").fetchall()}
    if "member_id" not in existing:
        conn.execute("ALTER TABLE kids ADD COLUMN member_id INTEGER REFERENCES members(id)")
        print("  [migrated] kids.member_id")
    else:
        print("  [exists]   kids.member_id")
    conn.commit()
    print("Done: kids.member_id ready.")
finally:
    conn.close()
