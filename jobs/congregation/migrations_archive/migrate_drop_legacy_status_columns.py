"""Phase 6 (final step) of the Partner/Connected/Active/Deacon/Residency
redesign, see ~/.claude/plans/zesty-cuddling-robin.md.

Every read/write site was confirmed switched over in Phases 4-5 (and a
further sweep just before this script was written, which caught several
sites Phase 4/5 had missed -- see the 2026-09-24 commits in this repo's
history for the full list). This is the destructive step:

  1. DROP COLUMN status, member_status, partnership_status, deacon_status,
     status_reason, status_since, status_note, snowbird_return.
  2. RENAME COLUMN active_v2 TO active, after first dropping the OLD
     boolean `active` column -- there's a brief moment mid-script where
     neither exists, which is fine since this all happens inside one
     script run with no other process touching the table concurrently.

Run this only after confirming (a) a fresh backup exists and (b) nothing
in the live codebase still reads/writes any of the 8 dropped columns or
the old boolean `active` (grep the repo for each name first).

Usage:
  python3 jobs/congregation/migrate_drop_legacy_status_columns.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

_DROPPED_COLUMNS = (
    "status",
    "member_status",
    "partnership_status",
    "deacon_status",
    "status_reason",
    "status_since",
    "status_note",
    "snowbird_return",
)


def _column_exists(conn, col: str) -> bool:
    return col in {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        for col in _DROPPED_COLUMNS:
            if _column_exists(conn, col):
                conn.execute(f"ALTER TABLE members DROP COLUMN {col}")
                print(f"  [dropped] members.{col}")
            else:
                print(f"  [exists]  members.{col} already dropped")
        conn.commit()

        if _column_exists(conn, "active") and not _column_exists(conn, "active_v2"):
            print("  [exists]  members.active already renamed (active_v2 gone)")
        elif _column_exists(conn, "active") and _column_exists(conn, "active_v2"):
            conn.execute("ALTER TABLE members DROP COLUMN active")
            print("  [dropped] members.active (old boolean)")
            conn.execute("ALTER TABLE members RENAME COLUMN active_v2 TO active")
            print("  [renamed] members.active_v2 -> active")
            conn.commit()
        else:
            print("  [skip]    unexpected column state for active/active_v2 -- check manually")

        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
