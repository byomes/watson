"""Add connected_override to congregation.db's members table.

Bill's 2026-09-24 request: Connected needs to be editable/batch-editable
(some old one-off attendees -- e.g. anniversary-service guests we know
aren't coming back -- sit permanently as "1st time"/"guest" even though
that's not a useful read on them), but "this does not override Watson
logic" -- the live attendance-based computation in
jobs.congregation.catalystdb_web._connected() must keep running normally
for everyone else, and for anyone once their override is cleared.

connected_override TEXT, nullable: when set, it IS the member's Connected
value, full stop -- _connected() is never called for them. When NULL
(the default for everyone), Connected is computed exactly as before. The
catalystdb grid's Connected column writes here (see catalystdb_web.py's
update()/create() field-routing) and clears it back to NULL via the '--'
option, meaning "go back to auto".

Usage:
  python3 jobs/congregation/migrate_connected_override.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
        if "connected_override" not in existing:
            conn.execute("ALTER TABLE members ADD COLUMN connected_override TEXT")
            print("  [migrated] members.connected_override")
        else:
            print("  [exists]   members.connected_override")
        conn.commit()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
