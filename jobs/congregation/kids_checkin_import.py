"""jobs/congregation/kids_checkin_import.py -- takes the raw pull from
jobs/congregation/kids_checkin_client.py (event instances + rosters) and
writes it into congregation.db's kids / kids_checkin tables.

Unlike fluro_pull.py's rule ("Fluro only enriches people ALREADY in
congregation.db, never adds new ones"), kids checked into classes never
fill out a connect card, so there is no existing record to match against
most of the time. Per Bill's explicit 2026-09-29 directive: an unmatched
kid gets a bare `kids` row created immediately -- no review gate, no wait
on anyone -- with household_id left NULL. Household linking is handled
separately: this script tries to match the checking-in GUARDIAN's phone or
email (both come through on every checkin record) against `members`, same
matcher fluro_pull.py uses. A guardian match becomes a *candidate*, staged
in kids_household_review_queue -- it is never applied automatically. Only
Donna approves a household link, via a Telegram message
notify_donna_kids_checkin_review.py sends her (gated to the calendar day
AFTER this import ran, and only 9am-8pm -- see that script's docstring).
A kid already queued (pending or resolved) is never re-queued on a re-run.

kids_checkin rows are keyed by subsplash_checkin_id (INSERT OR IGNORE), so
re-running this against the full-history pull every week is safe and never
duplicates an already-imported Sunday.

Usage:
  python3 -m jobs.congregation.kids_checkin_import                 # live pull from the phone
  python3 -m jobs.congregation.kids_checkin_import --from-file X.json  # reuse a saved pull (testing)
"""
import argparse
import json
import sqlite3
from pathlib import Path

from jobs.congregation.import_subsplash_contacts import _normalize_phone

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _find_household_candidate(conn, guardian_phone, guardian_email):
    phone = _normalize_phone(guardian_phone) if guardian_phone else None
    if phone:
        row = conn.execute(
            "SELECT id, household_id FROM members WHERE phone = ? AND household_id IS NOT NULL", (phone,)
        ).fetchone()
        if row:
            return row["id"], row["household_id"], "guardian_phone"
    if guardian_email:
        row = conn.execute(
            "SELECT id, household_id FROM members WHERE LOWER(email) = LOWER(?) AND household_id IS NOT NULL",
            (guardian_email.strip(),),
        ).fetchone()
        if row:
            return row["id"], row["household_id"], "guardian_email"
    return None, None, "no_match"


def _extract_checkin_records(pull_data: dict):
    """Flattens the raw pull into one dict per actual check-in record."""
    for instance in pull_data.get("results", []):
        for eu in instance.get("checkins", []):
            for rec in (eu.get("_embedded", {}).get("check-ins") or []):
                yield rec


def _upsert_kid(conn, profile: dict) -> int:
    row = conn.execute(
        "SELECT id, household_id FROM kids WHERE subsplash_profile_id = ?", (profile["id"],)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO kids (subsplash_profile_id, first_name, last_name, gender, household_id, created_via) "
        "VALUES (?, ?, ?, ?, NULL, 'checkin_only')",
        (profile["id"], profile.get("first_name", ""), profile.get("last_name"), profile.get("gender")),
    )
    return cur.lastrowid


def run(pull_data: dict) -> dict:
    conn = _connect()
    stats = {"checkin_rows_seen": 0, "checkin_rows_inserted": 0, "kids_created": 0, "queued_for_donna": 0}

    try:
        for rec in _extract_checkin_records(pull_data):
            stats["checkin_rows_seen"] += 1
            embedded = rec.get("_embedded") or {}
            profile = embedded.get("profile-snapshot") or {}
            guardian = embedded.get("by-profile-snapshot") or {}
            session_snap = embedded.get("session-snapshot") or {}
            event_snap = embedded.get("event-snapshot") or {}
            if not profile.get("id"):
                continue  # malformed record, skip

            before = conn.execute(
                "SELECT id FROM kids WHERE subsplash_profile_id = ?", (profile["id"],)
            ).fetchone()
            kid_id = _upsert_kid(conn, profile)
            if not before:
                stats["kids_created"] += 1

            event_date = (event_snap.get("start_at") or "")[:10]
            cur = conn.execute(
                "INSERT OR IGNORE INTO kids_checkin "
                "(kid_id, subsplash_checkin_id, event_id, class_name, event_date, checked_in_at, "
                " guardian_name, guardian_phone, guardian_email, checkin_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'subsplash_backlog')",
                (
                    kid_id, rec["id"], event_snap.get("id"), session_snap.get("title"), event_date,
                    rec.get("created_at"),
                    f"{guardian.get('first_name', '')} {guardian.get('last_name', '')}".strip() or None,
                    guardian.get("phone"), guardian.get("email"),
                ),
            )
            if cur.rowcount:
                stats["checkin_rows_inserted"] += 1

            kid_row = conn.execute("SELECT household_id FROM kids WHERE id = ?", (kid_id,)).fetchone()
            if kid_row["household_id"] is not None:
                continue  # already linked, nothing to queue
            already_queued = conn.execute(
                "SELECT 1 FROM kids_household_review_queue WHERE kid_id = ? AND status != 'resolved'", (kid_id,)
            ).fetchone()
            if already_queued:
                continue

            member_id, household_id, reason = _find_household_candidate(
                conn, guardian.get("phone"), guardian.get("email")
            )
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
    parser.add_argument("--from-file", help="reuse a saved kids_checkin_client.pull_full_history() JSON dump")
    args = parser.parse_args()

    if args.from_file:
        with open(args.from_file) as f:
            data = json.load(f)
    else:
        from jobs.congregation.kids_checkin_client import pull_full_history
        data = pull_full_history()

    if "error" in data:
        raise SystemExit(f"pull failed: {data}")

    result = run(data)
    print(
        f"checkin records seen: {result['checkin_rows_seen']}, "
        f"new checkin rows: {result['checkin_rows_inserted']}, "
        f"new kids: {result['kids_created']}, "
        f"queued for Donna: {result['queued_for_donna']}"
    )
