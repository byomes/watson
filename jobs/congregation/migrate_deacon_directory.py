"""Add address/household/deacon columns to congregation.db's members table.

Backs the deacon directory import (jobs/congregation/import_deacon_directory.py).
Deliberately does NOT reuse partnership_status (Guest/Regular Attender/Partner,
added in commit 7985876) — deacon_status is a different, more granular concept
(active/inactive/remote/unassigned within the deacon-care system) and
overloading partnership_status risks breaking whatever already reads it.

Usage:
  python3 jobs/congregation/migrate_deacon_directory.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

NEW_COLUMNS = [
    ("address", "TEXT"),
    ("household_id", "TEXT"),
    ("deacon", "TEXT"),
    ("deacon_status", "TEXT"),
]

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
    for col, defn in NEW_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE members ADD COLUMN {col} {defn}")
            print(f"  [migrated] members.{col}")
        else:
            print(f"  [exists]   members.{col}")
    conn.commit()
    print("Done: deacon directory columns ready.")
finally:
    conn.close()
