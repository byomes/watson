"""jobs/events/banquet_rsvp.py — detect and parse RSVP-form notification
emails for a `rsvp_tracking=1` church_events row (see jobs/events/schema.py),
starting with the annual Servant Leaders Banquet (Bill's 2026-09-24 request).

Why this is a SEPARATE handler from jobs/events/signup_detect.py rather than
an extension of it: signup_detect.py's whole model is "one notification
email = one attendee" -- every match becomes a registration, there is no
concept of someone declining. An RSVP form can reply either way, and Bill
explicitly wants both yes AND no tracked (so he can see who hasn't answered
yet), plus a food headcount that must NOT include "no" responses. Bolting a
third state onto signup_detect.py's binary "matched or not" flow risked
quietly changing behavior for the events (picnic, hayride, bonfire, ...)
that already work correctly in production. A church_events row only reaches
this file's classifier at all if Bill/Kaci explicitly set rsvp_tracking=1 on
it -- every other event's signup emails still go through signup_detect.py
untouched.

Called from jobs/email_intake.py's run() loop, BEFORE the existing
handle_event_signup_email() call -- an RSVP notification would otherwise
also satisfy signup_detect.py's own signup-keyword prefilter and get
recorded as a plain "attending" registration, losing the yes/no distinction
entirely. Same return contract as signup_detect.py: "read" (handled, mark
the email read), or None (not an RSVP email for any actively-tracked
rsvp_tracking event -- caller falls through to the normal pipeline).

Built BLIND -- no real Fluro RSVP-form notification email had been sent as
of 2026-09-24, since the form itself doesn't exist yet (Bill/Kaci need to
build it in Subsplash Fluro; Watson can't reach Fluro's admin UI itself --
see [[project_subsplash_fluro_admin_pull]], paused on a CAPTCHA). Rather
than hardcode a guess at Fluro's exact notification email layout, this uses
the same free-text Ollama classification approach as signup_detect.py's own
_classify(), which tolerates format drift. The form should be built with a
sender name/email/phone, a yes/no attending question, a "number of adults
in your party" question, and a "number of children needing paid childcare"
question -- see the classifier prompt below for the exact fields extracted.
Once real submission emails start arriving, re-check a handful against the
DB to confirm the classifier is reading them correctly; nothing here has
been validated against a live sample yet.
"""
import json
import logging
import os
import re
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH
from jobs.events.matching import find_member_id, find_member_id_by_name, find_member_name

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"  # same model signup_detect.py uses for this kind of one-shot email classification

# Cheap keyword prefilter, same reasoning as signup_detect.py's own
# _SIGNUP_KEYWORDS_RE -- avoid spending an Ollama call on ordinary email
# that obviously isn't a form submission notification.
_RSVP_KEYWORDS_RE = re.compile(
    r"\b(rsvp|fluro|form submission|has submitted|attending|banquet)\b",
    re.IGNORECASE,
)

_CLASSIFY_PROMPT = (
    "You are Watson, an AI assistant for a church. This email is a notification that someone "
    "submitted an RSVP form for an event called \"{event_name}\". Extract the submission details.\n\n"
    "Reply ONLY with valid JSON (no markdown, no explanation):\n"
    '{{\n'
    '  "is_rsvp_submission": true or false,\n'
    '  "first_name": "<respondent first name, or empty string>",\n'
    '  "last_name": "<respondent last name, or empty string>",\n'
    '  "email": "<respondent email, or empty string>",\n'
    '  "phone": "<respondent phone, or empty string>",\n'
    '  "attending": true or false,\n'
    '  "adult_count": <integer, how many adults total including the respondent, default 1>,\n'
    '  "child_count": <integer, how many children need paid childcare, default 0>,\n'
    '  "notes": "<any other relevant free text from the form, or empty string>"\n'
    '}}\n\n'
    "Subject: {subject}\n"
    "Body (first 1500 chars):\n"
    "{body_snippet}"
)


def _active_rsvp_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, event_name FROM church_events WHERE tracking_active = 1 AND rsvp_tracking = 1"
    ).fetchall()


def _match_event(rows: list[sqlite3.Row], subject: str, body: str) -> sqlite3.Row | None:
    """Same verbatim-name-in-text matching as signup_detect.py's
    find_active_event -- no fuzzy fallback needed here since in practice
    there is only ever one rsvp_tracking event open at a time."""
    text_l = f"{subject}\n{body}".lower()
    matches = [r for r in rows if (r["event_name"] or "").lower() in text_l]
    if len(matches) == 1:
        return matches[0]
    return None


def _classify(event_name: str, subject: str, body: str) -> dict | None:
    prompt = _CLASSIFY_PROMPT.format(event_name=event_name, subject=subject, body_snippet=body[:1500])
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as exc:
        log.error("Banquet RSVP classification failed: %s", exc)
        return None


def _upsert_rsvp(conn: sqlite3.Connection, event_id: int, detection: dict, received_at: str) -> None:
    """One row per respondent per event -- a resubmission (someone
    corrects their headcount, or changes their mind) UPDATEs the existing
    row rather than piling up duplicates that would double-count the food
    and childcare totals. Matched first by member_id (email/phone against
    congregation.db), falling back to an exact first+last name match
    within this event if no member match was found -- a first-time
    visitor RSVPing may have no congregation.db record at all."""
    email = (detection.get("email") or "").strip()
    phone = (detection.get("phone") or "").strip()
    first_name = (detection.get("first_name") or "").strip()
    last_name = (detection.get("last_name") or "").strip()
    member_id = find_member_id(email, phone)
    if member_id is None:
        # No email/phone match -- fall back to an exact name match so the
        # invite-roster cross-reference in banquet_report.py still counts
        # this respondent as having answered, not as "no response yet".
        # See find_member_id_by_name's docstring for why this is exact-only.
        member_id = find_member_id_by_name(first_name, last_name)
    if not first_name and not last_name and member_id:
        member_name = find_member_name(member_id)
        if member_name:
            parts = member_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

    rsvp_status = "yes" if detection.get("attending") else "no"
    try:
        adult_count = int(detection.get("adult_count") or 1)
    except (TypeError, ValueError):
        adult_count = 1
    try:
        child_count = int(detection.get("child_count") or 0)
    except (TypeError, ValueError):
        child_count = 0
    notes = (detection.get("notes") or "").strip() or None

    existing = None
    if member_id:
        existing = conn.execute(
            "SELECT id FROM event_registrations WHERE event_id = ? AND member_id = ?",
            (event_id, member_id),
        ).fetchone()
    if existing is None and first_name and last_name:
        existing = conn.execute(
            "SELECT id FROM event_registrations WHERE event_id = ? AND member_id IS NULL "
            "AND LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?)",
            (event_id, first_name, last_name),
        ).fetchone()

    if existing:
        conn.execute(
            """UPDATE event_registrations
               SET rsvp_status = ?, num_tickets = ?, child_count = ?, email = ?, phone = ?,
                   extra_fields = ?, submitted_at = ?, source = 'email'
               WHERE id = ?""",
            (rsvp_status, adult_count, child_count, email or None, phone or None,
             json.dumps({"notes": notes}) if notes else None, received_at, existing["id"]),
        )
        log.info("Banquet RSVP updated (resubmission) — event_id=%s member_id=%s status=%s",
                  event_id, member_id, rsvp_status)
        return

    conn.execute(
        """INSERT INTO event_registrations
           (event_id, first_name, last_name, email, phone, num_tickets, child_count,
            rsvp_status, extra_fields, member_id, source, submitted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'email', ?)""",
        (event_id, first_name, last_name, email or None, phone or None, adult_count, child_count,
         rsvp_status, json.dumps({"notes": notes}) if notes else None, member_id, received_at),
    )
    log.info("Banquet RSVP recorded — event_id=%s member_id=%s status=%s adults=%s children=%s",
              event_id, member_id, rsvp_status, adult_count, child_count)


def handle_banquet_rsvp_email(
    msg_id: str, sender_email: str, subject: str, body: str, received_at: str
) -> str | None:
    if not _RSVP_KEYWORDS_RE.search(f"{subject}\n{body[:2000]}"):
        return None

    from jobs.events.schema import create_tables
    create_tables()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = _active_rsvp_events(conn)
    if not rows:
        conn.close()
        return None

    matched = _match_event(rows, subject, body)
    if not matched:
        conn.close()
        return None

    detection = _classify(matched["event_name"], subject, body)
    if not detection or not detection.get("is_rsvp_submission"):
        conn.close()
        return None

    _upsert_rsvp(conn, matched["id"], detection, received_at)
    conn.commit()
    conn.close()
    return "read"
