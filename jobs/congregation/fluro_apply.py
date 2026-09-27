"""jobs/congregation/fluro_apply.py -- the ONLY code path that ever moves a
staged fluro_contacts row from fluro_staging.db into the real
congregation.db members table. Called exclusively from bot.py's
handle_fluro_review_callback, i.e. only after Donna (or Bill) taps an
explicit Telegram button -- see notify_donna_fluro_review.py for the
messages/buttons and jobs/congregation/fluro_pull.py's docstring for why
nothing here runs unattended.

Three actions, matching the three button rows a review message can show:
  - apply_conflict_values(fluro_id)  -- a 'conflict' row: overwrite the
    specific fields that disagreed with Fluro's value (only those fields --
    never a blanket overwrite of the whole member row)
  - keep_existing(fluro_id)          -- a 'conflict' row: dismiss, no write
  - merge_same_person(fluro_id)      -- a 'possible_duplicate' row: treat
    the fuzzy match as correct, fill any currently-blank fields on the
    existing member (same blank-only rule as import_subsplash_contacts.py)
  - create_new_member(fluro_id)      -- a 'possible_duplicate' row: treat
    as a genuinely different person, insert a new congregation.db member
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from jobs.congregation.fluro_staging_schema import get_connection as staging_conn

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"

_CONFLICT_FIELD_TO_MEMBER_COLUMN = {"email": "email", "phone": "phone", "birthdate": "birthdate", "gender": "gender"}


class FluroApplyError(RuntimeError):
    pass


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


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

        cconn = _cong_conn()
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


def merge_same_person(fluro_id: str) -> str:
    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        if staged["match_status"] != "possible_duplicate":
            raise FluroApplyError(f"row {fluro_id} is not a pending possible_duplicate (status={staged['match_status']})")
        member_id = staged["matched_member_id"]

        cconn = _cong_conn()
        try:
            row = cconn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if not row:
                raise FluroApplyError(f"matched member id {member_id} no longer exists in congregation.db")

            updates = {}
            if not (row["email"] or "").strip() and staged["email"]:
                updates["email"] = staged["email"]
            if not (row["phone"] or "").strip() and staged["phone"]:
                updates["phone"] = staged["phone"]
            if not (row["birthdate"] or "").strip() and staged["dob"]:
                updates["birthdate"] = staged["dob"]
            if not (row["gender"] or "").strip() and staged["gender"]:
                updates["gender"] = staged["gender"]

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                cconn.execute(
                    f"UPDATE members SET {set_clause}, updated_at = ? WHERE id = ?",
                    (*updates.values(), _now(), member_id),
                )
                cconn.commit()
            name = row["name"]
        finally:
            cconn.close()

        sconn.execute("UPDATE fluro_contacts SET review_status = 'approved' WHERE fluro_id = ?", (fluro_id,))
        sconn.commit()
        filled = f" ({', '.join(updates)} filled in)" if updates else " (no blank fields to fill)"
        return f"Confirmed as the same person -- linked to {name}{filled}."
    finally:
        sconn.close()


def create_new_member(fluro_id: str) -> str:
    sconn = staging_conn()
    try:
        staged = _get_staged(sconn, fluro_id)
        if staged["match_status"] != "possible_duplicate":
            raise FluroApplyError(f"row {fluro_id} is not a pending possible_duplicate (status={staged['match_status']})")

        name = f"{staged['first_name']} {staged['last_name']}".strip()
        cconn = _cong_conn()
        try:
            cconn.execute(
                """
                INSERT INTO members (name, email, phone, birthdate, gender, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, staged["email"], staged["phone"], staged["dob"], staged["gender"],
                 "Imported from Fluro (confirmed as a different person from the fuzzy match)", _now()),
            )
            new_id = cconn.execute("SELECT last_insert_rowid()").fetchone()[0]
            cconn.commit()
        finally:
            cconn.close()

        sconn.execute(
            "UPDATE fluro_contacts SET matched_member_id = ?, review_status = 'approved' WHERE fluro_id = ?",
            (new_id, fluro_id),
        )
        sconn.commit()
        return f"Created a new member record for {name} (id {new_id})."
    finally:
        sconn.close()
