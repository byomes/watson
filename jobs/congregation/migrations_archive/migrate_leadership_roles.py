"""Add leadership_roles table to congregation.db.

Tags members with leadership roles (elder, staff, leader, ...) independent of
member_status. Used by the Member Management "Roles" control and the
Fireflies elder-meeting review pipeline (jobs/meet/fireflies_review.py).

Usage:
  python3 jobs/congregation/migrate_leadership_roles.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_roles (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id  INTEGER NOT NULL REFERENCES members(id),
            role       TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(member_id, role)
        )
        """
    )
    conn.commit()
    print("Done: leadership_roles table ready.")
except Exception as e:
    print(f"Skipped (table may already exist): {e}")
finally:
    conn.close()
