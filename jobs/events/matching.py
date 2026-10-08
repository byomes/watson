"""jobs/events/matching.py — shared helpers for event_registrations: matching
a registrant to an existing congregation.db member, and matching an incoming
signup email to an actively-tracked church_events row.

find_member_id() itself stays read-only. find_or_create_member_id() (added
2026-10-03) is the one path here that writes to congregation.db — see its
docstring.
"""
import difflib
import os
import re
import sqlite3

from jobs.sms.carrier_lookup import normalize_phone

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")

# congregation.db stores members.phone formatted, e.g. "(302) 898-2979" --
# event signups/SMS always hand this module plain digits. Comparing against
# a digits-only projection of the column (strip the punctuation this data
# actually uses) instead of exact string equality, found live 2026-10-03
# while this match-before-create path was being added: an exact-string
# phone lookup was silently failing for any formatted member, which would
# have meant find_or_create_member_id creating duplicate members for
# people who already exist.
_PHONE_DIGITS_SQL = "REPLACE(REPLACE(REPLACE(REPLACE(phone,'(',''),')',''),'-',''),' ','')"

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
            phone_digits = normalize_phone(phone) or re.sub(r"\D", "", phone)
            row = conn.execute(
                f"SELECT id FROM members WHERE {_PHONE_DIGITS_SQL} = ? LIMIT 1", (phone_digits,)
            ).fetchone()
            conn.close()
            if row:
                return row["id"]
            return None
        conn.close()
    except Exception:
        return None
    return None


def find_or_create_member_id(email: str, phone: str, first_name: str = "", last_name: str = "") -> tuple[int | None, bool]:
    """Like find_member_id, but creates a new congregation.db member when no
    match exists and there's at least an email or phone to key the new
    record off of. Bill's call (2026-10-03, prompted by a picnic signup
    from someone he was texting with but who had no congregation.db
    record): a stranger to the church can sign up for an event before ever
    attending a service, and should still be tracked rather than sitting
    unlinked until Bill happens to notice and create a member by hand (as
    happened for that picnic signup). A member created this way has zero
    attendance rows, so catalystdb_web.py's _connected() already classifies
    them as 'neighbor' (pre-guest) with no extra status field needed.

    Returns (member_id, created) — created=True means this call just
    inserted a brand-new member row, so callers can fire a distinct
    "new neighbor" notification instead of (or alongside) their existing
    unmatched-signup alert.

    Still returns (None, False) with no email and no phone — nothing to
    key a new record off of, and no way to re-match it to this person
    later, so there's nothing useful to create."""
    member_id = find_member_id(email, phone)
    if member_id:
        return member_id, False

    email = (email or "").strip()
    phone = (phone or "").strip()
    if not email and not phone:
        return None, False

    name = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
    if not name:
        # No name at all available (e.g. the picnic signup that prompted
        # this — classifier found nothing, just an email/phone) — use the
        # email's local part as a provisional display name rather than
        # leaving it blank; Bill corrects it once he actually meets them.
        name = email.split("@")[0] if email else phone

    try:
        conn = sqlite3.connect(CONG_DB, timeout=5)
        cur = conn.execute(
            "INSERT INTO members (name, email, phone) VALUES (?, ?, ?)",
            (name, email or None, phone or None),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id, True
    except Exception:
        return None, False


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
        if not rows:
            # Members stored with the title (e.g. "Dr. Bill Yomes") never
            # match a form's plain "Bill Yomes" otherwise.
            rows = conn.execute(
                "SELECT id FROM members WHERE LOWER(name) = LOWER(?) LIMIT 2", (f"Dr. {full_name}",)
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
