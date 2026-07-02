#!/usr/bin/env python3
"""
migrate_admin_preview.py — Add is_admin_preview flag to arc_readers.

Adds:
  - arc_readers.is_admin_preview (INTEGER DEFAULT 0) — when set to 1, bypasses
    the manuscript unlock/close date gate for that reader account only
    (admin QA use, e.g. previewing the manuscript reader outside 7/15-9/15).

Run: python3 jobs/arc/migrate_admin_preview.py
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "watson.db"


def run():
    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute(
            "ALTER TABLE arc_readers ADD COLUMN is_admin_preview INTEGER NOT NULL DEFAULT 0"
        )
        print("  Added arc_readers.is_admin_preview")
    except Exception:
        print("  arc_readers.is_admin_preview already exists — skipped")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    run()
