"""Add leadership_only column to prayer_requests table."""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        "ALTER TABLE prayer_requests ADD COLUMN leadership_only INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()
    print("Done.")
except Exception as e:
    print(f"Skipped (column may already exist): {e}")
finally:
    conn.close()
