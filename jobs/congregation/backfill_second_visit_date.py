"""jobs/congregation/backfill_second_visit_date.py -- one-time compute of
members.second_visit_date from connect cards + attendance check-ins.

Built 2026-09-28 per Bill: tracking first_visit_date alone can't answer
"how long did it take this guest to come back," since that needs the
second visit's own date, not just a lifetime visit count. Same reasoning
as backfill_first_visit_date.py -- a purely computed value will always
have edge cases, so this backfills a real value onto each member's record
that Donna and Bill then own correcting by hand (editable as "Second
Visit" in the catalystdb admin board, wtsn.me/cat/catalystdb,
jobs/congregation/catalystdb_web.py's _EDITABLE_COLUMNS).

Anchored on members.first_visit_date, which must already be backfilled
and trustworthy (see backfill_first_visit_date.py and bug_tracker #204) --
a member with no first_visit_date on file is skipped here, since "second"
is meaningless without a known "first."

Per-member value, in priority order (same card-vs-attendance precedence
as backfill_first_visit_date.py, just scoped to strictly after
first_visit_date instead of from the beginning of history):
  1. Earliest connect card after first_visit_date with NO attendance row
     between first_visit_date and it -- a confirmed second-visit event.
  2. Otherwise, earliest attendance row after first_visit_date.
  3. Otherwise (a later card exists but no attendance row confirms it),
     that card itself.
  4. Otherwise leave second_visit_date untouched (no second visit yet).

Overwrites existing values unconditionally for every member where a value
is computable, same as backfill_first_visit_date.py -- this runs once as
an initial pass; after this, edits belong to Donna/Bill.

Usage:
  python3 -m jobs.congregation.backfill_second_visit_date
  python3 -m jobs.congregation.backfill_second_visit_date --dry-run
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"


def _compute_second_visit(conn: sqlite3.Connection, member_id: int, first_visit_date: str) -> str | None:
    second_card = conn.execute(
        "SELECT MIN(service_date) FROM deacon_visible_connect_cards "
        "WHERE member_id = ? AND service_date > ?",
        (member_id, first_visit_date),
    ).fetchone()[0]

    if second_card:
        intervening_attendance = conn.execute(
            "SELECT 1 FROM attendance WHERE member_id = ? AND service_date > ? AND service_date < ? LIMIT 1",
            (member_id, first_visit_date, second_card),
        ).fetchone()
        if not intervening_attendance:
            return second_card

    second_attendance = conn.execute(
        "SELECT MIN(service_date) FROM attendance WHERE member_id = ? AND service_date > ?",
        (member_id, first_visit_date),
    ).fetchone()[0]
    if second_attendance:
        return second_attendance

    return second_card  # last resort: an unconfirmed later card, no attendance to back it


def run(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    members = conn.execute(
        "SELECT id, name, first_visit_date, second_visit_date FROM members "
        "WHERE first_visit_date IS NOT NULL AND first_visit_date != ''"
    ).fetchall()

    changed = 0
    unchanged = 0
    uncomputable = 0
    no_first_visit = conn.execute(
        "SELECT COUNT(*) FROM members WHERE first_visit_date IS NULL OR first_visit_date = ''"
    ).fetchone()[0]

    for m in members:
        new_value = _compute_second_visit(conn, m["id"], m["first_visit_date"])
        if new_value is None:
            uncomputable += 1
            continue
        if new_value == m["second_visit_date"]:
            unchanged += 1
            continue
        changed += 1
        if dry_run:
            print(f"  [dry-run] id={m['id']} {m['name']!r}: {m['second_visit_date']!r} -> {new_value!r}")
        else:
            conn.execute(
                "UPDATE members SET second_visit_date = ?, updated_at = datetime('now') WHERE id = ?",
                (new_value, m["id"]),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    print()
    print("Backfill summary")
    print(f"  Changed:              {changed}")
    print(f"  Unchanged:            {unchanged}")
    print(f"  Uncomputable:         {uncomputable} (only ever attended/carded once)")
    print(f"  Skipped (no 1st visit): {no_first_visit}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute members.second_visit_date from cards + attendance")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes only; no writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
