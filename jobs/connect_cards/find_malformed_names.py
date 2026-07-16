"""
find_malformed_names.py — one-off manual utility, NOT a scheduled job, NOT wired into cron.

Lists every row in congregation.db `members` whose name is stored fully lowercase
or fully uppercase — same detection logic as jobs.connect_cards.utils._display_name
(str.islower() / str.isupper()). Intended as a data-quality punch list for manual
review (fix source data or leave to the display-layer formatter); this script does
not modify congregation.db.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/connect_cards/find_malformed_names.py
"""

import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def find_malformed_names() -> list[dict]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, campus_preference, email, status, member_status FROM members"
        ).fetchall()
    finally:
        conn.close()

    return [
        dict(r) for r in rows
        if r["name"] and (r["name"].islower() or r["name"].isupper())
    ]


if __name__ == "__main__":
    results = find_malformed_names()
    if not results:
        print("No all-lowercase or all-uppercase member names found.")
    else:
        print(f"{len(results)} member(s) with all-lowercase or all-uppercase names:\n")
        for r in results:
            print(
                f"  id={r['id']:<5} name={r['name']!r:<30} "
                f"campus={(r['campus_preference'] or '—'):<12} "
                f"email={(r['email'] or '—'):<35} "
                f"status={(r['status'] or '—'):<8} member_status={r['member_status'] or '—'}"
            )
