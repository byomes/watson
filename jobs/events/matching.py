"""jobs/events/matching.py — shared helpers for event_registrations: matching
a registrant to an existing congregation.db member (read-only, informational
cross-reference only — never writes to congregation.db from this feature),
and matching an incoming signup email to an actively-tracked church_events row.
"""
import difflib
import os
import sqlite3

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")

FUZZY_EVENT_THRESHOLD = 0.55


def find_member_id(email: str, phone: str) -> int | None:
    """Read-only lookup against congregation.db members — email first, then
    phone. Returns None on no match or any DB error; never creates or
    modifies a member (congregation.db is live pastoral data, out of scope
    for this feature)."""
    email = (email or "").strip()
    phone = (phone or "").strip()
    if not email and not phone:
        return None
    try:
        conn = sqlite3.connect(f"file:{CONG_DB}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        if email:
            row = conn.execute(
                "SELECT id FROM members WHERE LOWER(email) = LOWER(?) LIMIT 1", (email,)
            ).fetchone()
            if row:
                conn.close()
                return row["id"]
        if phone:
            row = conn.execute(
                "SELECT id FROM members WHERE phone = ? LIMIT 1", (phone,)
            ).fetchone()
            conn.close()
            if row:
                return row["id"]
            return None
        conn.close()
    except Exception:
        return None
    return None


def find_member_id_by_name(first_name: str, last_name: str) -> int | None:
    """Fallback for when a signup/RSVP email carries no email/phone that
    matches congregation.db (or the classifier failed to extract one) --
    added 2026-09-24 for jobs/events/banquet_rsvp.py, whose invite-roster
    cross-reference (jobs/congregation/banquet_report.py) depends on every
    respondent who IS an active servant actually getting a member_id, not
    just the ones who happened to give a matchable email/phone. Exact
    "first last" match only (no fuzzy/partial matching, unlike
    serving_edit.py's cascade) -- this is a read-only informational
    cross-reference, not an edit, so a wrong match here would silently
    misattribute someone's RSVP rather than just fail loudly like a typo'd
    edit command would. Ambiguous (more than one member with the same
    full name) is treated as no match, same reasoning as
    find_active_event's ambiguous-match handling below."""
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if not first_name or not last_name:
        return None
    full_name = f"{first_name} {last_name}"
    try:
        conn = sqlite3.connect(f"file:{CONG_DB}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "SELECT id FROM members WHERE LOWER(name) = LOWER(?) LIMIT 2", (full_name,)
        ).fetchall()
        conn.close()
        if len(rows) == 1:
            return rows[0][0]
    except Exception:
        return None
    return None


def find_member_name(member_id: int) -> str | None:
    """Read-only lookup of a matched member's full name, for backfilling a
    registrant's first/last name when the signup email's own classification
    came back blank but find_member_id still matched them by email/phone —
    e.g. a Subsplash notification with no name in the body at all."""
    if not member_id:
        return None
    try:
        conn = sqlite3.connect(f"file:{CONG_DB}?mode=ro", uri=True, timeout=5)
        row = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def find_active_event(conn: sqlite3.Connection, name_guess: str, text: str) -> dict | None:
    """Match name_guess/text against tracking_active=1 church_events rows.

    Returns the matched row as a dict, or None if no active event is a
    confident match. Two ways to match: the event's own name appears
    verbatim in the email text (subject+body), or a fuzzy ratio against the
    extracted name guess clears FUZZY_EVENT_THRESHOLD. Ambiguous (more than
    one match) is treated the same as no match — better to ask Bill than to
    guess wrong between two open events.
    """
    rows = conn.execute(
        "SELECT id, event_name, start_date, end_date FROM church_events WHERE tracking_active = 1"
    ).fetchall()
    text_l = (text or "").lower()
    name_guess_l = (name_guess or "").lower().strip()

    matches = []
    for row in rows:
        event_name_l = (row["event_name"] or "").lower()
        if event_name_l and event_name_l in text_l:
            matches.append(row)
            continue
        if name_guess_l:
            ratio = difflib.SequenceMatcher(None, name_guess_l, event_name_l).ratio()
            if ratio >= FUZZY_EVENT_THRESHOLD:
                matches.append(row)

    if len(matches) == 1:
        return dict(matches[0])
    return None
