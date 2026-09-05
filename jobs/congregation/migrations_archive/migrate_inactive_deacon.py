"""One-time migration (2026-08-31): seed the new "Inactive" deacon bucket
(see jobs/congregation/deacons_web.py and deacon_reports.EXCLUDED_DEACON_VALUES)
from the existing attendance-tool campus classification.

Every member whose campus_preference = 'Inactive' gets deacon = 'Inactive',
unconditionally overwriting whatever deacon they currently have. This is a
one-time bulk seed, not an ongoing sync -- from here on, deacon is an
independently editable field (via wtsn.me/cat/deacons), so a deacon can pull
someone back onto their own roster later even if that person's campus stays
Inactive.

Usage:
  python3 jobs/congregation/migrate_inactive_deacon.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, deacon FROM members WHERE campus_preference = 'Inactive' ORDER BY name"
        ).fetchall()

        if not rows:
            print("No members with campus_preference = 'Inactive'. Nothing to do.")
            return

        for r in rows:
            prev = r["deacon"] or "(none)"
            print(f"  {r['name']} (id={r['id']}): {prev!r} -> 'Inactive'")

        conn.execute(
            "UPDATE members SET deacon = 'Inactive' WHERE campus_preference = 'Inactive'"
        )
        conn.commit()
        print(f"\nDone: {len(rows)} member(s) set to deacon = 'Inactive'.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
