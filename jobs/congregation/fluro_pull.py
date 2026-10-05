"""jobs/congregation/fluro_pull.py -- pulls People (contacts) and Serving
(service team assignments) live from Fluro via jobs/congregation/
fluro_client.py, and stages the result in fluro_staging.db (see
fluro_staging_schema.py) -- deliberately NOT congregation.db directly.

Scope, per Bill's explicit instruction (2026-09-26): Fluro is used ONLY to
enrich people ALREADY in congregation.db -- never to add new people to it.
A Fluro contact with no email/phone/fuzzy-name match to an existing member
is skipped entirely (counted in the run summary, never staged, never
touches congregation.db). Every contact that DOES match is classified:
  - 'possible_duplicate' -- no email/phone match, but a fuzzy name hit
                            (same FUZZY_THRESHOLD as member_match.py) --
                            needs a human "is this really them?" call
                            before any field gets filled in
  - 'clean_fill'         -- matched by email/phone; Fluro only fills fields
                            that are currently blank on the existing record
                            -- applied automatically, right here, since a
                            blank-only fill can never overwrite real data
  - 'conflict'           -- matched by email/phone; at least one field is
                            non-blank on BOTH sides and disagrees -- never
                            auto-applied, always needs a human pick
  - 'exact_no_change'    -- matched, identical, nothing to do

So this job DOES write to congregation.db for 'clean_fill' rows (via
fluro_apply.fill_blank_fields) -- that's the one case safe enough to not
need a human in the loop. 'conflict' and 'possible_duplicate' rows are
staged with review_status='pending' and, at the end of a successful run,
handed to notify_donna_fluro_review.py so Watson sends Donna the Telegram
review on its own -- no Claude Code / manual trigger needed for the
recurring pull-and-notify cycle. bot.py's handle_fluro_review_callback is
the only thing that can turn a 'conflict'/'possible_duplicate' row into an
actual congregation.db write.

Serving data (fluro_serving) is matched against the SAME staging pull's
contact rows (by fluro_id), so re-run fluro_pull.py before trusting
serving matches if contacts data is stale. Only matched contacts can have
a matched_member_id here -- a serving assignment for someone Fluro has but
congregation.db doesn't just stays unmatched (NULL), never creates a
member.

A re-run refreshes every staged row in place (upsert on fluro_id). A prior
'rejected' decision (Donna said "keep what's on file" / "not the same
person") sticks across re-pulls as long as the row is still the same
match_status -- it will NOT re-notify her every cycle for something she
already dismissed. An 'approved' conflict, by contrast, naturally
reclassifies as 'exact_no_change' on the next pull once the real
congregation.db value matches Fluro's (the write already happened), so
there's nothing left to re-flag there either.

Usage (also installed in crontab -- see memory/CRON.md):
  python3 -m jobs.congregation.fluro_pull
"""
import difflib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from jobs.congregation import fluro_apply, fluro_client
from jobs.congregation.fluro_common import is_blank
from jobs.congregation.fluro_staging_schema import create_tables, get_connection
from jobs.congregation.import_subsplash_contacts import _normalize_phone
from jobs.congregation.member_match import FUZZY_THRESHOLD

log = logging.getLogger(__name__)

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"

_COMPARE_FIELDS = ("email", "phone", "birthdate", "gender")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _extract_contact_fields(rec: dict) -> dict:
    first = (rec.get("firstName") or "").strip()
    last = (rec.get("lastName") or "").strip()
    email = next((e for e in (rec.get("emails") or []) if e), None)
    phone_raw = next(
        (p for p in (rec.get("phoneNumbers") or rec.get("local") or rec.get("international") or []) if p),
        None,
    )
    normalized_phone = _normalize_phone(phone_raw) if phone_raw else None

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
    """Returns (match_status, conflict_fields_dict) -- 'conflict' or
    'clean_fill' or 'exact_no_change' (never 'new'/'possible_duplicate',
    those are decided by the caller before a match exists)."""
    conflicts = {}
    any_fill = False
    field_map = {"email": fields["email"], "phone": fields["phone"], "birthdate": fields["dob"], "gender": fields["gender"]}
    for field in _COMPARE_FIELDS:
        fluro_val = (field_map[field] or "").strip()
        existing_val = (existing_row[field] or "").strip()
        if is_blank(fluro_val):
            continue
        if is_blank(existing_val):
            any_fill = True
            continue
        if fluro_val.lower() != existing_val.lower():
            conflicts[field] = {"fluro": fluro_val, "existing": existing_val}

    if conflicts:
        return "conflict", conflicts
    if any_fill:
        return "clean_fill", {}
    return "exact_no_change", {}


def _stage_contacts(staging_conn, cconn, contacts: list[dict]) -> dict:
    """cconn is an open congregation.db connection -- clean_fill rows are
    written through it immediately (see module docstring)."""
    stats = {"skipped_no_match": 0, "possible_duplicate": 0, "clean_fill": 0, "conflict": 0, "exact_no_change": 0}
    now = _now()

    for rec in contacts:
        fields = _extract_contact_fields(rec)
        if not fields["name"]:
            continue

        existing_row, match_method = _find_existing_member(cconn, fields["email"], fields["phone"])

        if existing_row is None:
            fuzzy_row = _find_fuzzy_member(cconn, fields["name"])
            if fuzzy_row is None:
                # No match at all -- per scope, Fluro never adds new people. Not staged.
                stats["skipped_no_match"] += 1
                continue
            existing_row = None
            matched_member_id = fuzzy_row["id"]
            match_method = "fuzzy"
            match_status = "possible_duplicate"
            conflict_fields = {}
        else:
            matched_member_id = existing_row["id"]
            match_status, conflict_fields = _classify(existing_row, fields)

        stats[match_status] += 1

        if match_status == "clean_fill":
            fluro_apply.fill_blank_fields(cconn, matched_member_id, fields["email"], fields["phone"], fields["dob"], fields["gender"])
            review_status = "auto_applied"
        elif match_status in ("conflict", "possible_duplicate"):
            review_status = "pending"
        else:
            review_status = "not_needed"

        staging_conn.execute(
            """
            INSERT INTO fluro_contacts
                (fluro_id, first_name, last_name, email, phone, dob, gender, marital_status,
                 fluro_status, household_id, household_role, tags, realms, raw_json, pulled_at,
                 match_status, matched_member_id, match_method, conflict_fields, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fluro_id) DO UPDATE SET
                first_name=excluded.first_name, last_name=excluded.last_name, email=excluded.email,
                phone=excluded.phone, dob=excluded.dob, gender=excluded.gender,
                marital_status=excluded.marital_status, fluro_status=excluded.fluro_status,
                household_id=excluded.household_id, household_role=excluded.household_role,
                tags=excluded.tags, realms=excluded.realms, raw_json=excluded.raw_json,
                pulled_at=excluded.pulled_at, match_status=excluded.match_status,
                matched_member_id=excluded.matched_member_id, match_method=excluded.match_method,
                conflict_fields=excluded.conflict_fields,
                review_status = CASE
                    WHEN fluro_contacts.review_status = 'rejected'
                         AND fluro_contacts.match_status = excluded.match_status
                    THEN 'rejected'
                    ELSE excluded.review_status
                END
            """,
            (
                fields["fluro_id"], fields["first_name"], fields["last_name"], fields["email"],
                fields["phone"], fields["dob"], fields["gender"], fields["marital_status"],
                fields["fluro_status"], fields["household_id"], fields["household_role"],
                json.dumps(fields["tags"]), json.dumps(fields["realms"]), json.dumps(rec), now,
                match_status, matched_member_id, match_method, json.dumps(conflict_fields), review_status,
            ),
        )
    staging_conn.commit()
    # Drop any stale rows from a prior run that are no longer produced this run
    # (e.g. a contact that used to fuzzy-match and now genuinely has no match).
    staging_conn.execute(
        "DELETE FROM fluro_contacts WHERE pulled_at != ?", (now,)
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
        cconn = fluro_apply.cong_conn()
        try:
            contact_stats = _stage_contacts(staging_conn, cconn, contacts)
        finally:
            cconn.close()

        teams = fluro_client.fetch_all_service_teams(token)
        serving_count = _stage_serving(staging_conn, teams)

        staging_conn.execute(
            "UPDATE fluro_pull_runs SET finished_at = ?, contacts_pulled = ?, serving_pulled = ?, status = 'done' WHERE id = ?",
            (_now(), len(contacts), serving_count, run_id),
        )
        staging_conn.commit()

        summary = {"run_id": run_id, "contacts_pulled": len(contacts), "serving_pulled": serving_count, **contact_stats}
        log.info("fluro_pull complete: %s", summary)

        pending = contact_stats["conflict"] + contact_stats["possible_duplicate"]
        if pending:
            from jobs.congregation import notify_donna_fluro_review
            notify_donna_fluro_review.run()

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
