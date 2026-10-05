"""Add alt_phone to congregation.db's members table.

Bill's 2026-09-26 request, hit while linking an SMS thread to a member
whose members.phone already had a different (wrong, in that case, but not
always) number on file: give him a real choice between overwriting phone
and keeping both -- the existing number stays in phone, the new one goes
in alt_phone. Nothing else reads or displays alt_phone yet; this is just
the column plus the SMS link-member "keep both" write path.

Usage:
  python3 jobs/congregation/migrate_alt_phone.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
        if "alt_phone" not in existing:
            conn.execute("ALTER TABLE members ADD COLUMN alt_phone TEXT")
            print("  [migrated] members.alt_phone")
        else:
            print("  [exists]   members.alt_phone")
        conn.commit()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
