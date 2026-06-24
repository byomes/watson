#!/usr/bin/env python3
"""
migrate_admin.py — One-time admin migration for watson.db.

Adds:
  - team_members: status, last_activity_date, last_comms_date
  - pastoral_notes: team_member_id, note_type, content, created_by
  - admin_users table
  - Donna's admin account (username: donna, password: catalyst302)

Run: python3 jobs/dashboard/migrate_admin.py
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "watson.db"


def run():
    from werkzeug.security import generate_password_hash

    conn = sqlite3.connect(DB_PATH)

    # ── team_members new columns ──────────────────────────────────────────────
    for col, defn in [
        ("status",             "TEXT DEFAULT 'active'"),
        ("last_activity_date", "TEXT"),
        ("last_comms_date",    "TEXT"),
        ("sort_order",         "INTEGER DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE team_members ADD COLUMN {col} {defn}")
            print(f"  Added team_members.{col}")
        except Exception:
            print(f"  team_members.{col} already exists — skipped")

    # ── pastoral_notes new columns ────────────────────────────────────────────
    for col, defn in [
        ("team_member_id", "INTEGER"),
        ("note_type",      "TEXT DEFAULT 'general'"),
        ("content",        "TEXT"),
        ("created_by",     "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pastoral_notes ADD COLUMN {col} {defn}")
            print(f"  Added pastoral_notes.{col}")
        except Exception:
            print(f"  pastoral_notes.{col} already exists — skipped")

    # ── admin_users table ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    print("  admin_users table created (or already existed)")

    # ── Insert Donna ──────────────────────────────────────────────────────────
    hashed = generate_password_hash("catalyst302")
    try:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("donna", hashed),
        )
        print("  Inserted admin user: donna")
    except Exception:
        print("  Admin user 'donna' already exists — skipped")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    run()
