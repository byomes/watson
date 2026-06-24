#!/usr/bin/env python3
"""One-time migration: copy tasks table → team_tasks for Bill (member_id=12).

Does not drop the tasks table. Safe to run multiple times (deduplicates by title).
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "watson.db"
BILL_MEMBER_ID = 12


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    tasks = conn.execute(
        "SELECT id, title, due_date, status, created_at FROM tasks"
    ).fetchall()

    if not tasks:
        print("No tasks found in tasks table — nothing to migrate.")
        conn.close()
        return

    migrated = 0
    skipped  = 0
    for t in tasks:
        existing = conn.execute(
            "SELECT id FROM team_tasks WHERE member_id=? AND title=? AND source='personal' LIMIT 1",
            (BILL_MEMBER_ID, t["title"]),
        ).fetchone()
        if existing:
            print(f"  SKIP (already exists): {t['title']}")
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO team_tasks (member_id, title, due_date, status, source, created_at) "
            "VALUES (?, ?, ?, ?, 'personal', ?)",
            (BILL_MEMBER_ID, t["title"], t["due_date"],
             t["status"] if t["status"] != "active" else "open",
             t["created_at"]),
        )
        migrated += 1
        print(f"  MIGRATED: {t['title']}")

    conn.commit()
    conn.close()
    print(f"\nDone — migrated {migrated}, skipped {skipped} (already present).")


if __name__ == "__main__":
    run()
