"""jobs/congregation/fluro_apply.py -- the ONLY code path that ever writes
a Fluro-sourced value into the real congregation.db members table.

Scope, per Bill's explicit instruction (2026-09-26): Fluro is used ONLY to
enrich people already in Watson's congregation.db -- never to add new
people. A Fluro contact with no email/phone/fuzzy-name match to an
existing member is not imported at all (see fluro_pull.py). There is
deliberately no "create a new member from Fluro" action anywhere in this
file.

Two write paths:
  - fill_blank_fields(cconn, member_id, email, phone, dob, gender) -- the
    shared blank-only fill used both by fluro_pull.py's automatic
    'clean_fill' pass (no real disagreement, nothing to review) and by
    confirm_possible_duplicate below (once a human confirms a fuzzy name
    match is really the same person).
  - apply_conflict_values(fluro_id) -- a 'conflict' row: overwrite the
    SPECIFIC fields that disagreed with Fluro's value, never a blanket
    overwrite. Requires an explicit Telegram tap (bot.py's
    handle_fluro_review_callback) -- see fluro_pull.py's docstring for why
    a real disagreement is never auto-applied.

Plus two review-dismissal actions with no DB write: keep_existing (reject
a conflict) and reject_possible_duplicate (confirm it's NOT the same
person -- also no write, since Fluro data for a non-match is simply
irrelevant here, not a new-member candidate).
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from jobs.congregation.fluro_common import is_blank
from jobs.congregation.fluro_staging_schema import get_connection as staging_conn

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"

_CONFLICT_FIELD_TO_MEMBER_COLUMN = {"email": "email", "phone": "phone", "birthdate": "birthdate", "gender": "gender"}


class FluroApplyError(RuntimeError):
    pass


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def fill_blank_fields(cconn: sqlite3.Connection, member_id: int, email, phone, dob, gender) -> dict:
    """Blank-only fill, same rule as import_subsplash_contacts.py: only
    ever fills a field that's currently blank (or a known placeholder --
    see fluro_common.is_blank) on the existing member. Never overwrites a
    real existing value -- that's apply_conflict_values' job, and that one
    requires an explicit human tap. Returns the fields actually changed
    (empty dict if nothing was blank)."""
    row = cconn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise FluroApplyError(f"member id {member_id} no longer exists in congregation.db")

    updates = {}
    if is_blank(row["email"]) and email and not is_blank(email):
        updates["email"] = email
    if is_blank(row["phone"]) and phone and not is_blank(phone):
        updates["phone"] = phone
    if is_blank(row["birthdate"]) and dob and not is_blank(dob):
        updates["birthdate"] = dob
    if is_blank(row["gender"]) and gender and not is_blank(gender):
        updates["gender"] = gender

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cconn.execute(
            f"UPDATE members SET {set_clause}, updated_at = ? WHERE id = ?",
            (*updates.values(), _now(), member_id),
        )
        cconn.commit()
    return updates


def _get_staged(sconn, fluro_id: str) -> sqlite3.Row:
    row = sconn.execute("SELECT * FROM fluro_contacts WHERE fluro_id = ?", (fluro_id,)).fetchone()
    if not row:
        raise FluroApplyError(f"no staged fluro_contacts row for fluro_id={fluro_id}")
    return row


def apply_conflict_values(fluro_id: str) -> str:
    import json

    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        if staged["match_status"] != "conflict":
            raise FluroApplyError(f"row {fluro_id} is not a pending conflict (status={staged['match_status']})")
        conflicts = json.loads(staged["conflict_fields"] or "{}")
        if not conflicts:
            raise FluroApplyError(f"row {fluro_id} has no conflict_fields recorded")

        cconn = cong_conn()
        try:
            set_clauses, values = [], []
            for field in conflicts:
                column = _CONFLICT_FIELD_TO_MEMBER_COLUMN[field]
                set_clauses.append(f"{column} = ?")
                values.append(conflicts[field]["fluro"])
            values.extend([_now(), staged["matched_member_id"]])
            cconn.execute(
                f"UPDATE members SET {', '.join(set_clauses)}, updated_at = ? WHERE id = ?", values
            )
            cconn.commit()
            name_row = cconn.execute("SELECT name FROM members WHERE id = ?", (staged["matched_member_id"],)).fetchone()
            name = name_row["name"] if name_row else f"member #{staged['matched_member_id']}"
        finally:
            cconn.close()

        sconn.execute("UPDATE fluro_contacts SET review_status = 'approved' WHERE fluro_id = ?", (fluro_id,))
        sconn.commit()
        return f"Applied Fluro's value{'s' if len(conflicts) > 1 else ''} for {', '.join(conflicts)} on {name}."
    finally:
        sconn.close()


def keep_existing(fluro_id: str) -> str:
    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        sconn.execute("UPDATE fluro_contacts SET review_status = 'rejected' WHERE fluro_id = ?", (fluro_id,))
        sconn.commit()
        return f"Kept the existing record for {staged['first_name']} {staged['last_name']} unchanged."
    finally:
        sconn.close()


def confirm_possible_duplicate(fluro_id: str) -> str:
    """Donna/Bill confirmed the fuzzy name match IS the same person --
    fills any blank fields on the existing member, same rule as the
    automatic clean_fill pass."""
    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        if staged["match_status"] != "possible_duplicate":
            raise FluroApplyError(f"row {fluro_id} is not a pending possible_duplicate (status={staged['match_status']})")
        member_id = staged["matched_member_id"]

        cconn = cong_conn()
        try:
            updates = fill_blank_fields(cconn, member_id, staged["email"], staged["phone"], staged["dob"], staged["gender"])
            name_row = cconn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
            name = name_row["name"] if name_row else f"member #{member_id}"
        finally:
            cconn.close()

        sconn.execute("UPDATE fluro_contacts SET review_status = 'approved' WHERE fluro_id = ?", (fluro_id,))
        sconn.commit()
        filled = f" ({', '.join(updates)} filled in)" if updates else " (no blank fields to fill)"
        return f"Confirmed as the same person -- linked to {name}{filled}."
    finally:
        sconn.close()


def reject_possible_duplicate(fluro_id: str) -> str:
    """Donna/Bill confirmed the fuzzy name match is NOT the same person.
    No congregation.db write -- per scope, Fluro never creates a new
    member here, so a non-match simply has nothing to do."""
    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        sconn.execute("UPDATE fluro_contacts SET review_status = 'rejected' WHERE fluro_id = ?", (fluro_id,))
        sconn.commit()
        return f"Got it -- {staged['first_name']} {staged['last_name']} from Fluro is a different person. No changes made."
    finally:
        sconn.close()
