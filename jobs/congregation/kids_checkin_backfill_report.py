"""jobs/congregation/kids_checkin_backfill_report.py -- one-shot runner for
Watson (not meant to be driven step-by-step from a live Claude Code
session): wakes the phone, pulls full kids_checkin history via
kids_checkin_client.pull_full_history(), imports it via
kids_checkin_import.run(), and reports before/after coverage specifically
for the Sundays Bill flagged as missing (May/June/July 2026) alongside the
whole pull's stats. Safe to re-run any time -- the importer dedupes on
subsplash_checkin_id.

Usage: python3 -m jobs.congregation.kids_checkin_backfill_report
"""
import sqlite3
from pathlib import Path

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"

_GAP_SUNDAYS = [
    "2026-05-03", "2026-05-10", "2026-05-17", "2026-05-24", "2026-05-31",
    "2026-06-07", "2026-06-14", "2026-06-21", "2026-06-28",
    "2026-07-05", "2026-07-12", "2026-07-19", "2026-07-26",
]


def _coverage() -> dict:
    conn = sqlite3.connect(CONGREGATION_DB)
    out = {}
    for d in _GAP_SUNDAYS:
        n = conn.execute("SELECT COUNT(*) FROM kids_checkin WHERE event_date = ?", (d,)).fetchone()[0]
        out[d] = n
    conn.close()
    return out


def main() -> None:
    before = _coverage()

    from jobs.congregation.kids_checkin_client import pull_full_history
    from jobs.congregation import kids_checkin_import

    data = pull_full_history()
    if "error" in data:
        print(f"PULL FAILED: {data}")
        return

    stats = kids_checkin_import.run(data)
    after = _coverage()

    print(f"pull: {data['total_instances']} instances, {sum(len(r['checkins']) for r in data['results'])} checkin records")
    if data.get("failedEvents"):
        print(f"failed events (transient fetch errors, safe to re-run): {data['failedEvents']}")
    print(f"import: {stats}")
    print()
    print("Gap-Sunday coverage (checkin rows found), before -> after:")
    still_missing = []
    newly_filled = []
    for d in _GAP_SUNDAYS:
        b, a = before[d], after[d]
        marker = ""
        if b == 0 and a > 0:
            marker = "  <-- NEWLY FILLED"
            newly_filled.append(d)
        elif a == 0:
            marker = "  (still no per-child data -- Subsplash checkin app likely wasn't used that week)"
            still_missing.append(d)
        print(f"  {d}: {b} -> {a}{marker}")
    print()
    print(f"Summary: {len(newly_filled)} Sundays newly backfilled, {len(still_missing)} still have no per-child data.")


if __name__ == "__main__":
    main()
