"""jobs/congregation/fluro_pull.py -- pulls People (contacts) and Serving
(service team assignments) live from Fluro via jobs/congregation/
fluro_client.py, and stages the result in fluro_staging.db (see
fluro_staging_schema.py) -- deliberately NOT congregation.db.

Per Bill's explicit instruction (2026-09-26): this never writes to
congregation.db on its own. Every pulled contact is classified against
the existing members table:
  - 'new'               -- no email/phone/fuzzy-name match at all
  - 'possible_duplicate' -- no email/phone match, but a fuzzy name hit
                            (same FUZZY_THRESHOLD as member_match.py) --
                            same judgment call the existing
                            subsplash_import_fuzzy flow makes, just against
                            live Fluro data instead of a CSV export
  - 'clean_fill'         -- matched by email/phone; Fluro only fills fields
                            that are currently blank on the existing record,
                            nothing would be overwritten
  - 'conflict'           -- matched by email/phone; at least one field is
                            non-blank on BOTH sides and disagrees
  - 'exact_no_change'    -- matched, identical, nothing to do

Only 'conflict' and 'possible_duplicate' rows need a human decision --
notify_donna_fluro_review.py sends those to Donna via Telegram
(bot.py's handle_fluro_review_callback actually applies an approved
change to congregation.db; this job never does). 'new' and 'clean_fill'
rows sit in staging for review at leisure, not gated on Donna.

Serving data (fluro_serving) is matched against the SAME staging pull's
contact rows (by fluro_id), so re-run fluro_pull.py before trusting
serving matches if contacts data is stale.

Known limitation: a re-run overwrites prior staging rows (INSERT OR
REPLACE on fluro_id), including resetting review_status back to 'pending'
for a conflict Donna already reviewed if the underlying disagreement is
still present in Fluro after her decision was applied. Fine for the
current usage pattern (run on demand, review promptly) -- if this becomes
a recurring scheduled pull, add real state-carryover before then.

Usage:
  python3 -m jobs.congregation.fluro_pull
"""
import difflib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from jobs.congregation import fluro_client
from jobs.congregation.fluro_staging_schema import create_tables, get_connection
from jobs.congregation.import_subsplash_contacts import _normalize_phone
from jobs.congregation.member_match import FUZZY_THRESHOLD

log = logging.getLogger(__name__)

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"

_COMPARE_FIELDS = ("email", "phone", "birthdate", "gender")

# Placeholder/sentinel values used on either side that mean "no real data",
# not an actual value to compare or overwrite with -- found live 2026-09-26:
# congregation.db uses '--' for an unset gender, Fluro uses 'unknown'. Both
# must be treated as blank on BOTH sides, or every never-filled-in field
# reads as a false-positive conflict.
_BLANK_SENTINELS = {"--", "-", "unknown", "n/a", "na", "none", ""}


def _is_blank(value) -> bool:
    return (value or "").strip().lower() in _BLANK_SENTINELS


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_contact_fields(rec: dict) -> dict:
    first = (rec.get("firstName") or "").strip()
    last = (rec.get("lastName") or "").strip()
    email = next((e for e in (rec.get("emails") or []) if e), None)
    phone_raw = next(
        (p for p in (rec.get("phoneNumbers") or rec.get("local") or rec.get("international") or []) if p),
        None,
    )
    normalized_phone = _normalize_phone(phone_raw) if phone_raw else None
    if normalized_phone == "(000) 000-0000":
        normalized_phone = None  # Fluro's own placeholder for "no phone on file", not a real number

    return {
        "fluro_id": rec["_id"],
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}".strip(),
        "email": (email or "").strip() or None,
        "phone": normalized_phone,
        "dob": (rec.get("dob") or rec.get("dateOfBirth") or "").split("T")[0] or None,
        "gender": (rec.get("gender") or "").strip() or None,
        "marital_status": (rec.get("maritalStatus") or "").strip() or None,
        "fluro_status": rec.get("status"),
        "household_id": rec.get("_ss_householdID"),
        "household_role": rec.get("householdRole"),
        "tags": [t.get("title") for t in (rec.get("tags") or []) if t.get("title")],
        "realms": [r.get("title") for r in (rec.get("realms") or []) if r.get("title")],
    }


def _find_existing_member(conn, email, phone):
    if email:
        row = conn.execute("SELECT * FROM members WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
        if row:
            return row, "email"
    if phone:
        row = conn.execute("SELECT * FROM members WHERE phone = ?", (phone,)).fetchone()
        if row:
            return row, "phone"
    return None, None


def _find_fuzzy_member(conn, name):
    best_ratio, best_row = 0.0, None
    for row in conn.execute("SELECT * FROM members").fetchall():
        ratio = difflib.SequenceMatcher(None, name.lower(), (row["name"] or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_row = ratio, row
    if best_row is not None and best_ratio >= FUZZY_THRESHOLD:
        return best_row
    return None


def _classify(existing_row, fields: dict) -> tuple[str, dict]:
    """Returns (match_status, conflict_fields_dict)."""
    conflicts = {}
    any_fill = False
    field_map = {"email": fields["email"], "phone": fields["phone"], "birthdate": fields["dob"], "gender": fields["gender"]}
    for field in _COMPARE_FIELDS:
        fluro_val = (field_map[field] or "").strip()
        existing_val = (existing_row[field] or "").strip()
        if _is_blank(fluro_val):
            continue
        if _is_blank(existing_val):
            any_fill = True
            continue
        if fluro_val.lower() != existing_val.lower():
            conflicts[field] = {"fluro": fluro_val, "existing": existing_val}

    if conflicts:
        return "conflict", conflicts
    if any_fill:
        return "clean_fill", {}
    return "exact_no_change", {}


def _stage_contacts(staging_conn, cong_conn, contacts: list[dict]) -> dict:
    stats = {"new": 0, "possible_duplicate": 0, "clean_fill": 0, "conflict": 0, "exact_no_change": 0}
    now = _now()

    for rec in contacts:
        fields = _extract_contact_fields(rec)
        if not fields["name"]:
            continue

        existing_row, match_method = _find_existing_member(cong_conn, fields["email"], fields["phone"])
        matched_member_id = None
        conflict_fields = {}

        if existing_row is not None:
            matched_member_id = existing_row["id"]
            match_status, conflict_fields = _classify(existing_row, fields)
        else:
            fuzzy_row = _find_fuzzy_member(cong_conn, fields["name"])
            if fuzzy_row is not None:
                matched_member_id = fuzzy_row["id"]
                match_method = "fuzzy"
                match_status = "possible_duplicate"
            else:
                match_status = "new"

        stats[match_status] += 1

        staging_conn.execute(
            """
            INSERT INTO fluro_contacts
                (fluro_id, first_name, last_name, email, phone, dob, gender, marital_status,
                 fluro_status, household_id, household_role, tags, realms, raw_json, pulled_at,
                 match_status, matched_member_id, match_method, conflict_fields, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(fluro_id) DO UPDATE SET
                first_name=excluded.first_name, last_name=excluded.last_name, email=excluded.email,
                phone=excluded.phone, dob=excluded.dob, gender=excluded.gender,
                marital_status=excluded.marital_status, fluro_status=excluded.fluro_status,
                household_id=excluded.household_id, household_role=excluded.household_role,
                tags=excluded.tags, realms=excluded.realms, raw_json=excluded.raw_json,
                pulled_at=excluded.pulled_at, match_status=excluded.match_status,
                matched_member_id=excluded.matched_member_id, match_method=excluded.match_method,
                conflict_fields=excluded.conflict_fields,
                review_status=CASE WHEN excluded.match_status IN ('conflict','possible_duplicate')
                                    THEN 'pending' ELSE 'not_needed' END
            """,
            (
                fields["fluro_id"], fields["first_name"], fields["last_name"], fields["email"],
                fields["phone"], fields["dob"], fields["gender"], fields["marital_status"],
                fields["fluro_status"], fields["household_id"], fields["household_role"],
                json.dumps(fields["tags"]), json.dumps(fields["realms"]), json.dumps(rec), now,
                match_status, matched_member_id, match_method, json.dumps(conflict_fields),
            ),
        )
    staging_conn.commit()
    return stats


def _stage_serving(staging_conn, teams: list[dict]) -> int:
    now = _now()
    staging_conn.execute("DELETE FROM fluro_serving")  # full refresh each pull, no partial-state concerns like contacts

    count = 0
    for team in teams:
        team_title = team.get("title")
        fluro_team_id = team["_id"]
        for assignment in team.get("assignments") or []:
            assignment_title = assignment.get("title")
            for contact in assignment.get("contacts") or []:
                fluro_contact_id = contact.get("_id")
                if not fluro_contact_id:
                    continue
                matched_row = staging_conn.execute(
                    "SELECT matched_member_id FROM fluro_contacts WHERE fluro_id = ?", (fluro_contact_id,)
                ).fetchone()
                matched_member_id = matched_row["matched_member_id"] if matched_row else None

                staging_conn.execute(
                    """
                    INSERT INTO fluro_serving
                        (fluro_team_id, team_title, assignment_title, fluro_contact_id,
                         contact_first_name, contact_last_name, matched_member_id, pulled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fluro_team_id, team_title, assignment_title, fluro_contact_id,
                        contact.get("firstName"), contact.get("lastName"), matched_member_id, now,
                    ),
                )
                count += 1
    staging_conn.commit()
    return count


def run() -> dict:
    create_tables()
    staging_conn = get_connection()
    run_id = staging_conn.execute(
        "INSERT INTO fluro_pull_runs (started_at, status) VALUES (?, 'running')", (_now(),)
    ).lastrowid
    staging_conn.commit()

    try:
        token_info = fluro_client.get_session_token()
        token = token_info["token"]

        contacts = fluro_client.fetch_all_contacts(token)
        cong_conn = _cong_conn()
        contact_stats = _stage_contacts(staging_conn, cong_conn, contacts)
        cong_conn.close()

        teams = fluro_client.fetch_all_service_teams(token)
        serving_count = _stage_serving(staging_conn, teams)

        staging_conn.execute(
            "UPDATE fluro_pull_runs SET finished_at = ?, contacts_pulled = ?, serving_pulled = ?, status = 'done' WHERE id = ?",
            (_now(), len(contacts), serving_count, run_id),
        )
        staging_conn.commit()

        summary = {"run_id": run_id, "contacts_pulled": len(contacts), "serving_pulled": serving_count, **contact_stats}
        log.info("fluro_pull complete: %s", summary)
        return summary
    except Exception as exc:
        staging_conn.execute(
            "UPDATE fluro_pull_runs SET finished_at = ?, status = 'failed' WHERE id = ?", (_now(), run_id)
        )
        staging_conn.commit()
        log.error("fluro_pull failed: %s", exc)
        raise
    finally:
        staging_conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(json.dumps(result, indent=2))
