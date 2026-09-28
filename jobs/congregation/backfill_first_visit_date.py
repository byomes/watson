"""jobs/congregation/backfill_first_visit_date.py -- one-time recompute of
members.first_visit_date from connect cards + attendance check-ins.

Built 2026-09-28 per Bill: a purely computed "first-time guest" derivation
will always have edge cases it can't resolve on its own ("just because
it's the first time someone fills out a card doesn't necessarily guarantee
that's their first visit"). Rather than keep refining a live heuristic in
jobs/analytics/conversion_report.py, this backfills a real value onto each
member's own record -- Donna and Bill then own correcting it by hand
(already editable as "First Visit" in the catalystdb admin board,
wtsn.me/cat/catalystdb, jobs/congregation/catalystdb_web.py's
_EDITABLE_COLUMNS) to reflect what actually happened, not what the data
can prove. jobs/analytics/conversion_report.py switches to trusting this
column directly once it's backfilled -- see that module's docstring.

Per-member value, in priority order:
  1. Earliest connect card (deacon_visible_connect_cards.service_date)
     with NO attendance row (card-driven or a leader's manual check-in)
     dated before it -- a confirmed first-time-guest event.
  2. Otherwise, earliest attendance row of any kind -- the best available
     evidence of when they were first known to be present, even without a
     confirming card (covers both "attended before ever submitting a
     card" and "submitted a card, but had already been checked in
     earlier").
  3. Otherwise (a card exists but attendance is somehow empty -- shouldn't
     happen since a card normally creates its own attendance row, but
     cheap to cover), the earliest card itself.
  4. Otherwise leave first_visit_date untouched (nothing to compute from).

Overwrites existing values unconditionally for every member where a value
is computable -- the current column is already known broadly unreliable
(bug_tracker #204: bulk-backfill artifacts, and even the fixed dynamic
derivation still had gaps like Bill Crook), so a fresh, better-computed
value is worth more here than preserving whatever was there before. This
runs once as an initial pass; after this, edits belong to Donna/Bill, not
to being silently overwritten again by a future re-run of this script.

Usage:
  python3 -m jobs.congregation.backfill_first_visit_date
  python3 -m jobs.congregation.backfill_first_visit_date --dry-run
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"


def _compute_first_visit(conn: sqlite3.Connection, member_id: int) -> str | None:
    first_card = conn.execute(
        "SELECT MIN(service_date) FROM deacon_visible_connect_cards WHERE member_id = ?",
        (member_id,),
    ).fetchone()[0]

    if first_card:
        prior_attendance = conn.execute(
            "SELECT 1 FROM attendance WHERE member_id = ? AND service_date < ? LIMIT 1",
            (member_id, first_card),
        ).fetchone()
        if not prior_attendance:
            return first_card

    first_attendance = conn.execute(
        "SELECT MIN(service_date) FROM attendance WHERE member_id = ?", (member_id,)
    ).fetchone()[0]
    if first_attendance:
        return first_attendance

    return first_card  # last resort: an unconfirmed card with no attendance at all


def run(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    members = conn.execute("SELECT id, name, first_visit_date FROM members").fetchall()

    changed = 0
    unchanged = 0
    uncomputable = 0

    for m in members:
        new_value = _compute_first_visit(conn, m["id"])
        if new_value is None:
            uncomputable += 1
            continue
        if new_value == m["first_visit_date"]:
            unchanged += 1
            continue
        changed += 1
        if dry_run:
            print(f"  [dry-run] id={m['id']} {m['name']!r}: {m['first_visit_date']!r} -> {new_value!r}")
        else:
            conn.execute(
                "UPDATE members SET first_visit_date = ?, updated_at = datetime('now') WHERE id = ?",
                (new_value, m["id"]),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    print()
    print("Backfill summary")
    print(f"  Changed:      {changed}")
    print(f"  Unchanged:    {unchanged}")
    print(f"  Uncomputable: {uncomputable} (no card or attendance on record)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recompute members.first_visit_date from cards + attendance")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes only; no writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
