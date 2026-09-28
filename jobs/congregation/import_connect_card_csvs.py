"""
Batch backfill of historical Subsplash connect-card CSV exports into
congregation.db (members, connect_cards, attendance, next_steps,
prayer_requests). Built 2026-09-27 for Bill's three old exports (Wilmington
Campus, Online Campus, and a combined "Catalyst" form) dropped into
~/watson/incoming/connect_cards/.

Pure deterministic CSV parsing -- no LLM call of any kind, so there's no
Claude usage and nothing to route to Ollama either; it just wasn't needed
here (every field this script reads is a fixed form question, not free text
needing classification).

Column-layout notes discovered inspecting the three actual files:
  - The three exports don't share one header layout. The combined
    "Catalyst Connect Card.csv" has an explicit "Where did you attend with
    us? " column; the two campus-specific exports don't ask that (campus is
    inferred from the filename instead).
  - Subsplash's form changed over time, so "First Name"/"Last Name" (and,
    in the Online export, the prayer-request question) appear TWICE in the
    header with the same name -- for any given row only one of the two
    duplicate columns is actually populated (verified: 0 rows in either
    campus file have both name columns filled), so this script coalesces
    across every column matching a canonical field rather than trusting
    csv.DictReader's silent last-key-wins behavior, which would have
    dropped whichever half of the export used the OTHER duplicate slot.
  - Many data rows are shorter than the header (trailing empty fields
    dropped by the export) -- every column read here is bounds-checked.
  - The three files overlap in date range with each other, and the 2026
    portion likely already exists in congregation.db from live intake
    (jobs/connect_cards/intake.py). Safe/idempotent to (re)run: a row is
    skipped whenever a connect_cards row already exists for that member +
    service_date.

Deliberately does NOT insert into follow_ups for first-time visitors --
that auto-inserted "first-time visitor, no workflow" pattern was
Bill's own stopgap, cleared out 2026-09-24 in favor of the still-unbuilt
real follow-up system (see memory/project_followup_system_planned.md).
is_first_visit is still recorded on the connect_cards row itself (real form
answer, so no reason to discard it), just not fanned out into follow_ups.

Usage:
  python3 -m jobs.congregation.import_connect_card_csvs
  python3 -m jobs.congregation.import_connect_card_csvs --dir /path/to/csvs --dry-run
"""

import argparse
import csv
import glob
import os
import sqlite3
from datetime import datetime, timedelta

from jobs.congregation.member_match import find_or_create_member

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
DEFAULT_DIR = os.path.expanduser("~/watson/incoming/connect_cards")

NEXT_STEP_MAP = {
    "start following jesus":     "follow_jesus",
    "get baptized":               "baptism",
    "help growing in my faith":   "grow_faith",
    "become a catalyst partner":  "catalyst_partner",
    "small group":                "small_group",
    "ministry team":              "ministry_team",
}

CAMPUS_MAP = {
    "wilmington campus": "Wilmington",
    "online campus":     "Online",
}


def _get(row: list[str], i: int) -> str:
    return row[i].strip() if i < len(row) and row[i] else ""


def _service_date(submission_str: str) -> str | None:
    try:
        dt = datetime.strptime(submission_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    days_back = (dt.weekday() + 1) % 7
    return (dt - timedelta(days=days_back)).date().isoformat()


def _column_indices(header: list[str], *needles: str) -> list[int]:
    """All column indices whose header contains every needle (case-insensitive)."""
    return [
        i for i, h in enumerate(header)
        if all(n in h.lower() for n in needles)
    ]


def _coalesce_first(row: list[str], indices: list[int]) -> str:
    for i in indices:
        v = _get(row, i)
        if v:
            return v
    return ""


def _coalesce_join(row: list[str], indices: list[int]) -> str | None:
    seen = []
    for i in indices:
        v = _get(row, i)
        if v and v not in seen:
            seen.append(v)
    return "; ".join(seen) or None


def _parse_next_steps(text: str | None) -> list[str]:
    if not text:
        return []
    keys = []
    for item in text.split(","):
        item_lower = item.strip().lower()
        for substr, key in NEXT_STEP_MAP.items():
            if substr in item_lower and key not in keys:
                keys.append(key)
    return keys


def _filename_campus(path: str) -> str | None:
    base = os.path.basename(path).lower()
    if "wilmington" in base:
        return "Wilmington"
    if "online" in base:
        return "Online"
    return None


def process_file(conn: sqlite3.Connection, path: str, dry_run: bool) -> dict:
    stats = {"rows": 0, "inserted": 0, "skipped_existing": 0, "skipped_no_name": 0,
              "skipped_no_date": 0, "errors": 0}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)

    date_idx     = _column_indices(header, "submission date")
    first_idx    = _column_indices(header, "first name")
    last_idx     = _column_indices(header, "last name")
    combined_name_idx = _column_indices(header, "name (first")
    email_idx    = _column_indices(header, "email")
    phone_idx    = _column_indices(header, "phone")
    comment_idx  = _column_indices(header, "question/comment")
    prayer_idx   = _column_indices(header, "how can we pray")
    leader_idx   = _column_indices(header, "leadership only")
    firstvis_idx = _column_indices(header, "first sunday")
    campus_idx   = _column_indices(header, "where did you attend")
    nextstep_idx = _column_indices(header, "next step")

    fallback_campus = _filename_campus(path)

    for raw in rows:
        stats["rows"] += 1
        try:
            svc_date = _service_date(_coalesce_first(raw, date_idx)) if date_idx else None
            if not svc_date:
                stats["skipped_no_date"] += 1
                continue

            first = _coalesce_first(raw, first_idx)
            last = _coalesce_first(raw, last_idx)
            name = f"{first} {last}".strip()
            if not name:
                # Some rows only carry a single combined "Name (First & Last)"
                # column instead of split first/last -- found 2026-09-27
                # (1902 Wilmington rows, 700 Online rows had this and nothing
                # else, all with a real name in that combined column).
                name = _coalesce_first(raw, combined_name_idx)
            if not name:
                stats["skipped_no_name"] += 1
                continue

            email = _coalesce_first(raw, email_idx).lower()
            phone = _coalesce_first(raw, phone_idx)

            if campus_idx:
                raw_campus = _coalesce_first(raw, campus_idx).lower()
                campus = CAMPUS_MAP.get(raw_campus, fallback_campus or "Wilmington")
            else:
                campus = fallback_campus or "Wilmington"

            comment = _coalesce_first(raw, comment_idx) or None
            prayer = _coalesce_join(raw, prayer_idx)
            leadership_text = _coalesce_join(raw, leader_idx) or ""
            leadership_only = "leadership only" in leadership_text.lower()
            next_step_text = _coalesce_join(raw, nextstep_idx)
            next_step_keys = _parse_next_steps(next_step_text)
            is_first_visit = _coalesce_first(raw, firstvis_idx).lower().startswith("yes")

            if dry_run:
                stats["inserted"] += 1
                continue

            member_id = find_or_create_member(conn, name, email, phone, svc_date)

            existing_card = conn.execute(
                "SELECT id FROM connect_cards WHERE member_id = ? AND service_date = ?",
                (member_id, svc_date),
            ).fetchone()
            if existing_card:
                stats["skipped_existing"] += 1
                continue

            conn.execute(
                """
                INSERT INTO connect_cards
                  (member_id, service_date, campus, questions_comments,
                   prayer_request, next_steps, is_first_visit, prayer_request_public)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (member_id, svc_date, campus, comment, prayer, next_step_text,
                 1 if is_first_visit else 0, 0 if leadership_only else 1),
            )
            card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            existing_attendance = conn.execute(
                "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
                (member_id, svc_date),
            ).fetchone()
            if not existing_attendance:
                conn.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, ?)",
                    (member_id, svc_date, campus, card_id),
                )

            for step_key in next_step_keys:
                conn.execute(
                    "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                    (member_id, card_id, step_key, svc_date),
                )

            if prayer:
                conn.execute(
                    "INSERT INTO prayer_requests (member_id, card_id, request_text, date, leadership_only) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (member_id, card_id, prayer, svc_date, 1 if leadership_only else 0),
                )

            stats["inserted"] += 1
            if stats["inserted"] % 200 == 0:
                conn.commit()

        except Exception as exc:
            stats["errors"] += 1
            print(f"  ERROR ({os.path.basename(path)} row {stats['rows']}): {exc}")

    if not dry_run:
        conn.commit()

    return stats


def run(directory: str, dry_run: bool = False) -> None:
    paths = sorted(glob.glob(os.path.join(directory, "*.csv")))
    if not paths:
        print(f"No CSV files found in {directory}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        for path in paths:
            print(f"\n── {os.path.basename(path)} ──")
            stats = process_file(conn, path, dry_run)
            for k, v in stats.items():
                print(f"  {k}: {v}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Subsplash connect-card CSV exports into congregation.db")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="Directory of CSV files (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and count only; no DB writes")
    args = parser.parse_args()
    run(args.dir, dry_run=args.dry_run)
