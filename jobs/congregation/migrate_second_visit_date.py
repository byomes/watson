"""Add second_visit_date to congregation.db's members table.

Bill's 2026-09-28 request: first_visit_date alone can't show how long it
took a guest to come back a second time -- that requires tracking the
second visit as its own dated field, not just a "2nd time" status label
(catalystdb_web.py's _connected() already derives that label live from a
visit count, but never stored the date it happened). Mirrors
migrate_connected_override.py's idempotent ALTER TABLE pattern.

second_visit_date TEXT, nullable: same shape and same editable-in-
catalystdb-board treatment as first_visit_date (see
backfill_second_visit_date.py and catalystdb_web.py's _EDITABLE_COLUMNS).
The two columns together let a report compute "days between first and
second visit" directly instead of re-deriving it from attendance history
each time.

Usage:
  python3 jobs/congregation/migrate_second_visit_date.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
        if "second_visit_date" not in existing:
            conn.execute("ALTER TABLE members ADD COLUMN second_visit_date TEXT")
            print("  [migrated] members.second_visit_date")
        else:
            print("  [exists]   members.second_visit_date")
        conn.commit()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
