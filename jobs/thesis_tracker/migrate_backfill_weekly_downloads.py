"""Backfill weekly_downloads on existing thesis_snapshots rows.

weekly_downloads = total_downloads - previous row's total_downloads (by id order),
NULL for the first row. Same delta logic scrape.py now applies going forward, so
historical rows and future rows are computed consistently.

Usage:
  python3 jobs/thesis_tracker/migrate_backfill_weekly_downloads.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
try:
    conn.execute("ALTER TABLE thesis_snapshots ADD COLUMN weekly_downloads INTEGER")
except sqlite3.OperationalError as e:
    if "duplicate column name" not in str(e):
        raise

rows = conn.execute(
    "SELECT id, total_downloads FROM thesis_snapshots ORDER BY id"
).fetchall()

prev_total = None
updated = 0
for row in rows:
    weekly = None if prev_total is None or row["total_downloads"] is None else row["total_downloads"] - prev_total
    conn.execute(
        "UPDATE thesis_snapshots SET weekly_downloads = ? WHERE id = ?",
        (weekly, row["id"]),
    )
    updated += 1
    prev_total = row["total_downloads"]

conn.commit()
conn.close()
print(f"Done: backfilled weekly_downloads on {updated} thesis_snapshots rows.")
