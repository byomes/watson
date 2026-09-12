"""Add household_role to congregation.db's members table.

Backs family-relationship tracking (jobs/congregation/family_edit.py's
mark_spouse/mark_child, jobs/analytics/data_chat.py's spouse/child/parent
self-join queries). household_id (added by migrate_deacon_directory.py)
already groups a family together, but nothing distinguished WHO within that
group is the spouse vs. a child vs. the head -- Pastor Tyler asking Watson
"who is so-and-so's wife" surfaced the gap 2026-09-12, since a shared
household_id or shared last name can't disambiguate spouse from sibling
from parent/child.

Values: 'head', 'spouse', 'child', 'other' (an adult relative/roommate
sharing a household without being a spouse or child), or NULL if never
recorded. Deliberately a single free-text-ish column on members rather than
a separate relationship-pairs table -- household_id already IS the family
grouping; this just tags each member's role within it, so a plain self-join
on household_id answers "who is X's spouse/child/parent" without a second
table to keep in sync.

Usage:
  python3 jobs/congregation/migrate_household_role.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
    if "household_role" not in existing:
        conn.execute("ALTER TABLE members ADD COLUMN household_role TEXT")
        print("  [migrated] members.household_role")
    else:
        print("  [exists]   members.household_role")
    conn.commit()
    print("Done: household_role ready.")
finally:
    conn.close()
