"""
Member matching and upsert for congregation.db.

find_or_create_member(db, name, email, phone, service_date) returns a member_id
using priority-ordered matching:
  1. Exact email match (case-insensitive)
  2. Exact phone match
  3. Fuzzy name match (difflib threshold 0.82) — records a duplicate_flag for review
  4. No match — inserts new member
"""

import difflib
import sqlite3
from datetime import datetime


FUZZY_THRESHOLD = 0.82


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _update_contact_info(db: sqlite3.Connection, member_id: int, email: str, phone: str) -> None:
    db.execute(
        """
        UPDATE members
        SET email      = CASE WHEN TRIM(COALESCE(email, '')) = '' AND ? != '' THEN ? ELSE email END,
            phone      = CASE WHEN TRIM(COALESCE(phone, '')) = '' AND ? != '' THEN ? ELSE phone END,
            updated_at = ?
        WHERE id = ?
        """,
        (email, email, phone, phone, _now(), member_id),
    )


def _set_first_visit_if_missing(db: sqlite3.Connection, member_id: int, service_date: str) -> None:
    db.execute(
        """
        UPDATE members SET first_visit_date = ?
        WHERE id = ? AND (first_visit_date IS NULL OR first_visit_date = '')
        """,
        (service_date, member_id),
    )


def find_or_create_member(
    db: sqlite3.Connection,
    name: str,
    email: str,
    phone: str,
    service_date: str,
) -> int:
    """Return member_id, creating a new record if no match is found."""
    email = (email or "").strip()
    phone = (phone or "").strip()
    name  = (name  or "").strip()

    # 1. Exact email match
    if email:
        row = db.execute(
            "SELECT id FROM members WHERE LOWER(email) = LOWER(?)", (email,)
        ).fetchone()
        if row:
            _update_contact_info(db, row[0], email, phone)
            _set_first_visit_if_missing(db, row[0], service_date)
            return row[0]

    # 2. Exact phone match
    if phone:
        row = db.execute(
            "SELECT id FROM members WHERE phone = ?", (phone,)
        ).fetchone()
        if row:
            _update_contact_info(db, row[0], email, phone)
            _set_first_visit_if_missing(db, row[0], service_date)
            return row[0]

    # 3. Fuzzy name match — match but flag for pastoral review
    if name:
        all_members = db.execute("SELECT id, name FROM members").fetchall()
        best_ratio, best_id = 0.0, None
        for mid, mname in all_members:
            ratio = difflib.SequenceMatcher(None, name.lower(), (mname or "").lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_id = ratio, mid
        if best_id is not None and best_ratio >= FUZZY_THRESHOLD:
            _update_contact_info(db, best_id, email, phone)
            _set_first_visit_if_missing(db, best_id, service_date)
            # Flag so Dr. Bill can confirm the auto-match was correct
            db.execute(
                """
                INSERT INTO duplicate_flags (member_id_a, member_id_b, reason, status)
                VALUES (?, ?, 'fuzzy_name', 'pending')
                """,
                (best_id, best_id),
            )
            return best_id

    # 4. No match — create new member
    db.execute(
        """
        INSERT INTO members (name, email, phone, first_visit_date, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, email or None, phone or None, service_date, _now()),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]
