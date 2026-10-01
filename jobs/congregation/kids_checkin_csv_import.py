"""jobs/congregation/kids_checkin_csv_import.py -- ingests a Subsplash
"catalyst-kids-check-ins" CSV export (the manual-download format from the
admin console, distinct from kids_checkin_client.py's API pull) into
kids / kids_checkin, the same tables kids_checkin_import.py and the
/cat/kidsatt tool (kids_attendance_web.py) both read and write.

Source: a CSV uploaded through the /cat/kidsatt "Batch import" dialog,
landing in data/imports/kids_att_csv/ (see kids_attendance_web.py's
import_csv()). Header row (exact Subsplash export column names):
  Event Date, Check-in time, Session, [Checked in] First Name,
  [Checked in] Last Name, [Checked in] Gender, [Checked in] Age,
  [Checked in] Grade, [Checked in] Email, [Checked in] Phone,
  Security Code, [Checked in by] First Name, [Checked in by] Last Name,
  [Checked in by] Email, [Checked in by] Phone, Check-out Time
Age/Grade/Security Code/Check-out Time have no home in the current schema
and are dropped, same as every other column kids_checkin_import.py drops
from its own source.

No profile id or checkin id comes through this export (unlike the API
pull), so:
  - Kid matching is by (first_name, last_name) case-insensitive against
    existing `kids` rows -- a real Subsplash profile id isn't available
    here, unlike kids_checkin_import.py's upsert. 2+ matches is counted
    as ambiguous and the lowest id is used (same "a leader can fix it by
    hand" posture kids_attendance_web.py already takes for manual moves);
    zero matches creates a new kid (created_via='csv_import', synthetic
    subsplash_profile_id namespaced "csv_import:").
  - subsplash_checkin_id is synthesized deterministically from
    (name, session, event_date, checked_in_at), so re-running the same
    CSV (or an overlapping export) is a safe no-op via INSERT OR IGNORE,
    the same guarantee kids_checkin_import.py gives for its own rows.
checkin_source='csv_import' keeps these rows distinguishable from both
the API backlog importer ('subsplash_backlog') and leader-typed rows
('leader_manual').

Household linking reuses kids_checkin_import.py's exact logic
(_find_household_candidate, ensure_member_for_kid): a guardian
phone/email match becomes a *candidate* queued in
kids_household_review_queue, never auto-applied -- only Donna approves
(see that module's docstring for the full policy).

Usage:
  python3 -m jobs.congregation.kids_checkin_csv_import <path.csv> [more.csv ...]
  python3 -m jobs.congregation.kids_checkin_csv_import --all   # every file currently in data/imports/kids_att_csv/
"""
import argparse
import csv
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from jobs.congregation.kids_checkin_import import _find_household_candidate, ensure_member_for_kid

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"
IMPORT_DIR = Path.home() / "watson" / "data" / "imports" / "kids_att_csv"

CLASS_NAMES = ["Nursery", "Pre-K", "Elementary Kids Church"]


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_subsplash_datetime(raw: str) -> tuple[str, str]:
    """"September 27, 2026 at 10:00:00 am EDT" -> ("2026-09-27", "2026-09-27 10:00:00").
    The timezone abbreviation is dropped -- stored as the wall-clock time
    Subsplash already localized, same as every other timestamp this app
    keeps as plain TEXT with no tz attached."""
    raw = raw.strip()
    date_part, _, time_part = raw.partition(" at ")
    time_part = time_part.rsplit(" ", 1)[0]  # drop trailing "EDT"/"EST"/etc.
    dt = datetime.strptime(f"{date_part} {time_part.upper()}", "%B %d, %Y %I:%M:%S %p")
    return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M:%S")


def _synthetic_checkin_id(first: str, last: str, session: str, event_date: str, checked_in_at: str) -> str:
    key = f"{first.lower()}|{last.lower()}|{session}|{event_date}|{checked_in_at}"
    return "csv_import:" + hashlib.sha1(key.encode()).hexdigest()[:16]


def _find_kid_matches(conn, first_name: str, last_name: str):
    return conn.execute(
        "SELECT id FROM kids WHERE LOWER(first_name) = LOWER(?) AND LOWER(COALESCE(last_name, '')) = LOWER(?) "
        "ORDER BY id",
        (first_name, last_name or ""),
    ).fetchall()


def _upsert_kid(conn, first_name: str, last_name: str, gender: str | None) -> tuple[int, bool, bool]:
    """Returns (kid_id, created, ambiguous)."""
    matches = _find_kid_matches(conn, first_name, last_name)
    if len(matches) == 1:
        return matches[0]["id"], False, False
    if len(matches) > 1:
        return matches[0]["id"], False, True
    profile_id = "csv_import:" + hashlib.sha1(f"{first_name}|{last_name}|{datetime.now().timestamp()}".encode()).hexdigest()[:16]
    cur = conn.execute(
        "INSERT INTO kids (subsplash_profile_id, first_name, last_name, gender, created_via) "
        "VALUES (?, ?, ?, ?, 'csv_import')",
        (profile_id, first_name, last_name or None, gender),
    )
    return cur.lastrowid, True, False


def run(csv_path: Path) -> dict:
    stats = {
        "rows_seen": 0,
        "checkin_rows_inserted": 0,
        "kids_created": 0,
        "ambiguous_name_matches": 0,
        "queued_for_donna": 0,
    }
    conn = _connect()
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        # Ascending by check-in time so current_class ends up as each kid's
        # truly most recent class -- same ordering guarantee
        # kids_checkin_import.py relies on for the API pull.
        def _sort_key(row):
            try:
                return _parse_subsplash_datetime(row["Check-in time"])[1]
            except Exception:
                return ""
        rows.sort(key=_sort_key)

        for row in rows:
            stats["rows_seen"] += 1
            first_name = (row.get("[Checked in] First Name") or "").strip()
            last_name = (row.get("[Checked in] Last Name") or "").strip()
            if not first_name:
                continue  # malformed row, skip
            gender_raw = (row.get("[Checked in] Gender") or "").strip().lower()
            gender = {"male": "M", "female": "F"}.get(gender_raw, gender_raw or None)
            session = (row.get("Session") or "").strip()

            try:
                event_date, checked_in_at = _parse_subsplash_datetime(row["Check-in time"])
            except Exception:
                continue  # unparseable timestamp, skip rather than guess

            kid_id, created, ambiguous = _upsert_kid(conn, first_name, last_name, gender)
            if created:
                stats["kids_created"] += 1
            if ambiguous:
                stats["ambiguous_name_matches"] += 1
            ensure_member_for_kid(conn, kid_id)

            guardian_name = (
                f"{(row.get('[Checked in by] First Name') or '').strip()} "
                f"{(row.get('[Checked in by] Last Name') or '').strip()}"
            ).strip() or None
            guardian_phone = (row.get("[Checked in by] Phone") or "").strip() or None
            guardian_email = (row.get("[Checked in by] Email") or "").strip() or None

            checkin_id = _synthetic_checkin_id(first_name, last_name, session, event_date, checked_in_at)
            cur = conn.execute(
                "INSERT OR IGNORE INTO kids_checkin "
                "(kid_id, subsplash_checkin_id, event_id, class_name, event_date, checked_in_at, "
                " guardian_name, guardian_phone, guardian_email, checkin_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv_import')",
                (
                    kid_id, checkin_id, checkin_id, session, event_date, checked_in_at,
                    guardian_name, guardian_phone, guardian_email,
                ),
            )
            if cur.rowcount:
                stats["checkin_rows_inserted"] += 1

            if session in CLASS_NAMES:
                conn.execute(
                    "UPDATE kids SET current_class = ?, updated_at = datetime('now') WHERE id = ?",
                    (session, kid_id),
                )

            kid_row = conn.execute("SELECT household_id FROM kids WHERE id = ?", (kid_id,)).fetchone()
            if kid_row["household_id"] is not None:
                continue  # already linked, nothing to queue
            already_queued = conn.execute(
                "SELECT 1 FROM kids_household_review_queue WHERE kid_id = ? AND status != 'resolved'", (kid_id,)
            ).fetchone()
            if already_queued:
                continue
            member_id, household_id, reason = _find_household_candidate(conn, guardian_phone, guardian_email)
            conn.execute(
                "INSERT INTO kids_household_review_queue "
                "(kid_id, candidate_household_id, candidate_member_id, match_reason) VALUES (?, ?, ?, ?)",
                (kid_id, household_id, member_id, reason),
            )
            stats["queued_for_donna"] += 1

        conn.commit()
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="CSV file(s) to import")
    parser.add_argument("--all", action="store_true", help=f"import every .csv currently in {IMPORT_DIR}")
    args = parser.parse_args()

    paths = sorted(IMPORT_DIR.glob("*.csv")) if args.all else [Path(p) for p in args.files]
    if not paths:
        raise SystemExit("no CSV files given -- pass a path or --all")

    totals = {
        "rows_seen": 0, "checkin_rows_inserted": 0, "kids_created": 0,
        "ambiguous_name_matches": 0, "queued_for_donna": 0,
    }
    for path in paths:
        result = run(path)
        print(f"{path.name}: {result}")
        for k in totals:
            totals[k] += result[k]
    print(f"\nTOTAL: {totals}")
