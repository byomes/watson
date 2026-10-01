"""jobs/congregation/kids_checkin_csv_backfill.py -- one-shot Watson runner
(not meant to be driven step-by-step from a live Claude Code session): pulls
Bill's manually-downloaded Subsplash "check-ins" CSV exports off FMSPC
(D:\\OneDrive\\Desktop\\kids_att, one file per export batch, ~56 files
covering the full history across 2025-2026) via scp, parses every row, and
upserts into congregation.db's kids / kids_checkin tables alongside the
API-sourced pull in kids_checkin_client.py / kids_checkin_import.py.

Why a separate importer instead of reusing kids_checkin_import.run(): that
importer expects the API's nested JSON shape (profile-snapshot /
session-snapshot / event-snapshot with real Subsplash profile_id and
checkin_id). This CSV export is flat (name + event date/session + guardian
fields only) and carries no stable Subsplash ids at all, so kid identity
here is matched by (first_name, last_name) against the existing `kids`
table instead of subsplash_profile_id, and new synthetic ids are minted
for rows this script creates (prefixed "csv:") to satisfy the UNIQUE NOT
NULL columns. Household-candidate matching and member-row sync are reused
directly from kids_checkin_import.py so behavior stays identical.

Dedup rule: congregation.db's existing convention (see kids_attendance_web.py)
treats (kid_id, event_date) as one row per kid per Sunday -- a kid's current
class is just their most recent row, nothing to reconcile across sessions.
So for every CSV record, if a kids_checkin row already exists for that
(kid_id, event_date) -- whether from the API pull or an earlier run of this
script -- it is left untouched and the CSV record is skipped. This is what
makes re-running this script safe, and what prevents the CSV export (which
covers the same full history as the API pull) from doubling every row.

Usage: python3 -m jobs.congregation.kids_checkin_csv_backfill
"""
import csv
import hashlib
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from jobs.congregation.kids_checkin_import import _find_household_candidate, ensure_member_for_kid

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"
STAGING_DIR = Path.home() / "watson" / "data" / "imports" / "kids_att"
FMSPC_SOURCE = "fmspc:D:/OneDrive/Desktop/kids_att"
CHECKIN_SOURCE = "csv_backfill_2026_09_30"

_DT_RE = re.compile(r"^(.*?)\s+[A-Z]{2,5}$")


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def pull_from_fmspc() -> int:
    """scp's the whole kids_att folder from FMSPC into STAGING_DIR. The
    staging dir is wiped first: scp -r on a bare "kids_att" source copies
    the directory itself into the destination parent, so if STAGING_DIR
    already exists from a prior run, scp nests it one level deeper
    (kids_att/kids_att/*.csv) instead of refreshing it in place."""
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    STAGING_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["scp", "-r", FMSPC_SOURCE, str(STAGING_DIR.parent)],
        check=True,
    )
    return len(list(STAGING_DIR.glob("*.csv")))


def _parse_dt(raw: str):
    if not raw:
        return None
    raw = raw.strip().strip('"')
    m = _DT_RE.match(raw)
    cleaned = m.group(1) if m else raw
    return datetime.strptime(cleaned, "%B %d, %Y at %I:%M:%S %p")


def _find_kid_by_name(conn, first_name: str, last_name: str):
    rows = conn.execute(
        "SELECT id, household_id FROM kids WHERE LOWER(first_name) = LOWER(?) "
        "AND LOWER(IFNULL(last_name, '')) = LOWER(IFNULL(?, '')) ORDER BY id",
        (first_name, last_name or ""),
    ).fetchall()
    return rows


def _upsert_kid(conn, first_name: str, last_name: str, gender: str, stats: dict) -> int:
    matches = _find_kid_by_name(conn, first_name, last_name)
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        stats["ambiguous_name_matches"].append(f"{first_name} {last_name or ''}".strip())
        return matches[0]["id"]

    synthetic_id = "csv:" + hashlib.sha1(f"{first_name.lower()}:{(last_name or '').lower()}".encode()).hexdigest()[:16]
    cur = conn.execute(
        "INSERT INTO kids (subsplash_profile_id, first_name, last_name, gender, household_id, created_via) "
        "VALUES (?, ?, ?, ?, NULL, 'checkin_only')",
        (synthetic_id, first_name, last_name, gender),
    )
    stats["kids_created"] += 1
    return cur.lastrowid


def _process_file(conn, path: Path, stats: dict):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stats["rows_seen"] += 1
            first_name = (row.get("[Checked in] First Name") or "").strip()
            if not first_name:
                stats["rows_skipped_malformed"] += 1
                continue
            last_name = (row.get("[Checked in] Last Name") or "").strip() or None
            gender = (row.get("[Checked in] Gender") or "").strip() or None
            class_name = (row.get("Session") or "").strip() or None

            event_dt = _parse_dt(row.get("Event Date"))
            if event_dt is None:
                stats["rows_skipped_malformed"] += 1
                continue
            event_date = event_dt.strftime("%Y-%m-%d")

            checkin_dt = _parse_dt(row.get("Check-in time"))
            checked_in_at = checkin_dt.strftime("%Y-%m-%d %H:%M:%S") if checkin_dt else None

            guardian_first = (row.get("[Checked in by] First Name") or "").strip()
            guardian_last = (row.get("[Checked in by] Last Name") or "").strip()
            guardian_name = f"{guardian_first} {guardian_last}".strip() or None
            guardian_phone = (row.get("[Checked in by] Phone") or "").strip() or None
            guardian_email = (row.get("[Checked in by] Email") or "").strip() or None

            kid_id = _upsert_kid(conn, first_name, last_name, gender, stats)

            already = conn.execute(
                "SELECT id FROM kids_checkin WHERE kid_id = ? AND event_date = ?", (kid_id, event_date)
            ).fetchone()
            if already:
                stats["rows_already_covered"] += 1
                continue

            synthetic_checkin_id = "csv:" + hashlib.sha1(
                f"{kid_id}:{event_date}:{class_name}".encode()
            ).hexdigest()[:20]
            synthetic_event_id = f"csv:{event_date}"  # event_id is NOT NULL; CSV has no real one
            cur = conn.execute(
                "INSERT OR IGNORE INTO kids_checkin "
                "(kid_id, subsplash_checkin_id, event_id, class_name, event_date, checked_in_at, "
                " guardian_name, guardian_phone, guardian_email, checkin_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kid_id, synthetic_checkin_id, synthetic_event_id, class_name, event_date, checked_in_at,
                 guardian_name, guardian_phone, guardian_email, CHECKIN_SOURCE),
            )
            if not cur.rowcount:
                stats["rows_already_covered"] += 1
                continue
            stats["rows_inserted"] += 1

            if class_name:
                conn.execute(
                    "UPDATE kids SET current_class = ?, updated_at = datetime('now') WHERE id = ?",
                    (class_name, kid_id),
                )

            kid_row = conn.execute("SELECT household_id FROM kids WHERE id = ?", (kid_id,)).fetchone()
            if kid_row["household_id"] is not None:
                continue
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


def run() -> dict:
    stats = {
        "rows_seen": 0, "rows_inserted": 0, "rows_already_covered": 0,
        "rows_skipped_malformed": 0, "kids_created": 0, "queued_for_donna": 0,
        "ambiguous_name_matches": [],
    }
    conn = _connect()
    try:
        for path in sorted(STAGING_DIR.glob("*.csv")):
            _process_file(conn, path, stats)
        conn.commit()

        for kid_id in conn.execute(
            "SELECT id FROM kids WHERE subsplash_profile_id LIKE 'csv:%' AND member_id IS NULL"
        ).fetchall():
            ensure_member_for_kid(conn, kid_id["id"])
        conn.commit()
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    n_files = pull_from_fmspc()
    print(f"pulled {n_files} CSV files from FMSPC into {STAGING_DIR}")

    result = run()
    print(
        f"rows seen: {result['rows_seen']}, new rows inserted: {result['rows_inserted']}, "
        f"already covered (API pull or earlier run): {result['rows_already_covered']}, "
        f"skipped (malformed): {result['rows_skipped_malformed']}, "
        f"new kids created: {result['kids_created']}, "
        f"queued for Donna: {result['queued_for_donna']}"
    )
    if result["ambiguous_name_matches"]:
        print(f"ambiguous name matches (multiple existing kids, used first match -- review manually): "
              f"{sorted(set(result['ambiguous_name_matches']))}")
