"""Catalyst DB grid cleanup (Bill's 2026-09-24 batch of requests, see
~/.claude/plans/zesty-cuddling-robin.md for the earlier Partner/Connected/
Active/Deacon/Residency work this continues):

  1. carrier -- dropped entirely (column + data). Confirmed empty (0 rows
     with a value) before dropping; unrelated to watson.db's phone_carriers
     table, which is a separate mechanism this doesn't touch.
  2. shepherding_exempt -- retired in favor of active_v2='disconnected'.
     The 3 currently-exempt members who aren't already disconnected/deceased
     get migrated to 'disconnected' first (preserving their real-world
     exclusion from shepherding reports), then the column is dropped.
  3. anniversary TEXT -- new column, marriage-anniversary data starting to
     come in.
  4. unsubscribed INTEGER NOT NULL DEFAULT 0 -- new toggle for comms
     unsubscribe status.
  5. notes -- cleared for the 30 members whose entire notes field was just
     "Subsplash tag: X" import clutter (see import_subsplash_contacts.py's
     _pick_partnership(), which as of this same commit no longer writes
     these going forward).

Usage:
  python3 jobs/congregation/migrate_catalystdb_grid_cleanup.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def _column_exists(conn, col: str) -> bool:
    return col in {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}


def _migrate_shepherding_exempt(conn):
    rows = conn.execute(
        "SELECT id, name FROM members WHERE shepherding_exempt = 1 "
        "AND active_v2 NOT IN ('disconnected', 'deceased')"
    ).fetchall()
    for member_id, name in rows:
        conn.execute(
            "UPDATE members SET active_v2 = 'disconnected', active = 0, "
            "member_status = 'disconnected' WHERE id = ?",
            (member_id,),
        )
        print(f"  [migrated] shepherding_exempt -> disconnected: {name!r} (id={member_id})")
    conn.commit()
    if not rows:
        print("  [migrated] shepherding_exempt -> disconnected: none needed")


def _clear_subsplash_notes(conn):
    before = conn.execute(
        "SELECT COUNT(*) FROM members WHERE notes LIKE 'Subsplash tag:%'"
    ).fetchone()[0]
    conn.execute("UPDATE members SET notes = NULL WHERE notes LIKE 'Subsplash tag:%'")
    conn.commit()
    print(f"  [cleared] notes: {before} rows had 'Subsplash tag:' clutter, now cleared")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        # Order matters: migrate shepherding_exempt data before dropping it.
        if _column_exists(conn, "shepherding_exempt"):
            _migrate_shepherding_exempt(conn)
            conn.execute("ALTER TABLE members DROP COLUMN shepherding_exempt")
            print("  [dropped] members.shepherding_exempt")
        else:
            print("  [exists]  members.shepherding_exempt already dropped")

        if _column_exists(conn, "carrier"):
            conn.execute("ALTER TABLE members DROP COLUMN carrier")
            print("  [dropped] members.carrier")
        else:
            print("  [exists]  members.carrier already dropped")

        if not _column_exists(conn, "anniversary"):
            conn.execute("ALTER TABLE members ADD COLUMN anniversary TEXT")
            print("  [migrated] members.anniversary")
        else:
            print("  [exists]   members.anniversary")

        if not _column_exists(conn, "unsubscribed"):
            conn.execute("ALTER TABLE members ADD COLUMN unsubscribed INTEGER NOT NULL DEFAULT 0")
            print("  [migrated] members.unsubscribed")
        else:
            print("  [exists]   members.unsubscribed")

        conn.commit()
        _clear_subsplash_notes(conn)
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
