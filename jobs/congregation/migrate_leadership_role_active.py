"""Add is_active to congregation.db's leadership_roles table.

leadership_roles previously had no way to track a role ending (e.g. a deacon
stepping down) without hard-deleting the row and losing the history. Adds
is_active (default 1) so app.py's role removal can deactivate instead of
delete, and re-adding the same role reactivates the existing row rather than
violating the UNIQUE(member_id, role) constraint.

Usage:
  python3 jobs/congregation/migrate_leadership_role_active.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(leadership_roles)").fetchall()}
    if "is_active" not in existing:
        conn.execute(
            "ALTER TABLE leadership_roles ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
        print("  [migrated] leadership_roles.is_active")
    else:
        print("  [exists]   leadership_roles.is_active")
    conn.commit()
    print("Done: leadership_roles.is_active ready.")
finally:
    conn.close()
