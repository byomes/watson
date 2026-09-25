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

Updated 2026-09-25 against a real sample submission Bill forwarded/received
2026-09-24 (event_registrations id=27, a Donna Redman test run using
placeholder names). Two things assumed at build time turned out wrong:

1. The notification sender is Subsplash **SnapPages** (no-reply@snappages.com),
   NOT Fluro -- see [[project_subsplash_fluro_admin_pull]], which was about a
   different, still-unbuilt admin-pull integration and can stay paused; it was
   never this form.
2. The form does not collect a single "number of adults" integer. It collects
   up to SIX individually-named attendees -- "First Attendee Name"/"Last Name"/
   "Email" for the respondent, then (if "Would you like to RSVP for additional
   servant leaders in your family?" is Yes) "Second Attendee Name" through
   "Sixth Attendee Name", each with its own Last Name field, no email/phone.
   These are each other actual servant leaders being RSVP'd for in one
   submission, not just a headcount -- so they need their OWN
   event_registrations row (matched to their own member_id by name) or
   banquet_report.py's non-responder list would wrongly show them as never
   having answered. Similarly, "number of children needing paid childcare"
   isn't a form field either -- the real question is "Will any children or
   non-serving teens be coming with you?" followed by a free-text list of
   "Name - age" lines. child_count is derived by counting that list; Bill
   should confirm whether "child_count" still means paid-childcare headcount
   given the form never actually asks about childcare, or whether it's just
   general kids/teens attending (flagged, not resolved here).

Also note: SnapPages' plain-text body strips all HTML structure, so label
and value run together with no separator or newline (e.g. "First Attendee
NameTestLast NameTestEmailkdredman@comcast.net"). Tried asking llama3.2:3b
to split label from value in that run-on text (the classify prompt below
spells out the known field order) -- it got the "attending" yes/no wrong
and mis-split "Second Attendee Name"/"Last Name" pairs on the real sample.
Since these field labels are a fixed, known set for this specific form,
_parse_snappages_fields() below splits on the literal label text
deterministically instead and is tried FIRST; the Ollama _classify() path
is now only a fallback for if the form's field labels ever change.
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
# that obviously isn't a form submission notification. "snappages" added
# 2026-09-25 once a real sample showed the actual sender.
_RSVP_KEYWORDS_RE = re.compile(
    r"\b(rsvp|fluro|snappages|form submission|has submitted|attending|banquet)\b",
    re.IGNORECASE,
)

_CLASSIFY_PROMPT = (
    "You are Watson, an AI assistant for a church. This email is a Subsplash SnapPages "
    "notification that someone submitted an RSVP form for an event called \"{event_name}\". "
    "The plain-text body has NO separators between field labels and answers because the "
    "original HTML formatting was stripped -- e.g. the raw text "
    "\"First Attendee NameTestLast NameTestEmailkdredman@comcast.net\" means "
    "First Attendee Name=Test, Last Name=Test, Email=kdredman@comcast.net. "
    "The form always has these fields, in this order (some only present if the prior "
    "yes/no answer was Yes):\n"
    "- First Attendee Name, Last Name, Email (the respondent)\n"
    "- Will you be attending? (Yes/No)\n"
    "- Would you like to RSVP for additional servant leaders in your family? (Yes/No)\n"
    "- If Yes: Second Attendee Name + Last Name, Third Attendee Name + Last Name, Fourth, "
    "Fifth, Sixth -- up to 6 total attendees. Only include ones that actually have a name filled in.\n"
    "- Will any children or non-serving teens be coming with you? (Yes/No)\n"
    "- If Yes: a block of free text lines, each roughly \"Name - age\" or \"Name, age\", one "
    "child/teen per line\n\n"
    "Reply ONLY with valid JSON (no markdown, no explanation):\n"
    '{{\n'
    '  "is_rsvp_submission": true or false,\n'
    '  "first_name": "<respondent first name (the First Attendee), or empty string>",\n'
    '  "last_name": "<respondent last name, or empty string>",\n'
    '  "email": "<respondent email, or empty string>",\n'
    '  "phone": "<respondent phone, or empty string>",\n'
    '  "attending": true or false,\n'
    '  "additional_attendees": [{{"first_name": "...", "last_name": "..."}}, ...],\n'
    '  "children": ["<name - age>", ...],\n'
    '  "notes": "<any other relevant free text from the form, or empty string>"\n'
    '}}\n\n'
    "Subject: {subject}\n"
    "Body (first 3000 chars):\n"
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


_SNAPPAGES_ORDINAL_LABELS = (
    "Second Attendee Name", "Third Attendee Name", "Fourth Attendee Name",
    "Fifth Attendee Name", "Sixth Attendee Name",
)
_SNAPPAGES_LABELS = (
    "First Attendee Name", "Last Name", "Email", "Will you be attending?",
    "Would you like to RSVP for additional servant leaders in your family?",
) + _SNAPPAGES_ORDINAL_LABELS + (
    "Will any children or non-serving teens be coming with you?",
    "Please provide names AND ages of all children/teens who will be attending.",
)
_SNAPPAGES_SPLIT_RE = re.compile(
    "(" + "|".join(re.escape(l) for l in sorted(_SNAPPAGES_LABELS, key=len, reverse=True)) + ")"
)


def _parse_snappages_fields(body: str) -> dict | None:
    """Deterministic parser for the real Servant Leaders Banquet SnapPages
    form (field labels confirmed against the 2026-09-24 sample -- see the
    module docstring). Splits the run-on plain-text body on the form's own
    literal, fixed label strings -- reliable in a way a small LLM guessing
    label/value word boundaries in concatenated text isn't. Returns None
    (caller falls back to _classify) if the two load-bearing labels aren't
    both found, e.g. the form gets rebuilt with different wording."""
    parts = _SNAPPAGES_SPLIT_RE.split(body)
    if len(parts) < 3:
        return None

    first_name = last_name = email = None
    attending = None
    additional_attendees: list[dict] = []
    has_children = False
    children_raw = None
    prev_label = None

    for i in range(1, len(parts), 2):
        label = parts[i]
        value = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if label == "First Attendee Name":
            first_name = value
        elif label == "Last Name":
            if prev_label == "First Attendee Name":
                last_name = value
            elif prev_label in _SNAPPAGES_ORDINAL_LABELS and additional_attendees:
                additional_attendees[-1]["last_name"] = value
        elif label == "Email":
            email = value
        elif label == "Will you be attending?":
            attending = value.lower().startswith("y")
        elif label in _SNAPPAGES_ORDINAL_LABELS:
            additional_attendees.append({"first_name": value, "last_name": ""})
        elif label == "Will any children or non-serving teens be coming with you?":
            has_children = value.lower().startswith("y")
        elif label == "Please provide names AND ages of all children/teens who will be attending.":
            children_raw = value
        prev_label = label

    if first_name is None or attending is None:
        return None

    children: list[str] = []
    if has_children and children_raw:
        # This is the last known label, so its "value" runs to the end of
        # the body, including the SnapPages footer/copyright line -- cut
        # that off before splitting into name/age lines.
        children_raw = re.split(r"Subsplash,?\s*LLC", children_raw, flags=re.IGNORECASE)[0]
        for line in children_raw.splitlines():
            line = line.strip()
            if sum(c.isalpha() for c in line) >= 2:
                children.append(line)

    return {
        "is_rsvp_submission": True,
        "first_name": first_name or "",
        "last_name": last_name or "",
        "email": email or "",
        "phone": "",
        "attending": attending,
        "additional_attendees": [a for a in additional_attendees if a["first_name"] or a["last_name"]],
        "children": children,
        "notes": "",
    }


def _classify(event_name: str, subject: str, body: str) -> dict | None:
    prompt = _CLASSIFY_PROMPT.format(event_name=event_name, subject=subject, body_snippet=body[:3000])
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


def _upsert_person_row(
    conn: sqlite3.Connection, event_id: int, first_name: str, last_name: str,
    email: str | None, phone: str | None, rsvp_status: str, child_count: int,
    notes: str | None, received_at: str,
) -> None:
    """One row per named attendee per event (num_tickets is always 1 here --
    the caller inserts one row per person rather than one row per submission,
    see the module docstring's 2026-09-25 update for why). A resubmission
    UPDATEs the existing row rather than piling up duplicates that would
    double-count the food/childcare totals. Matched first by member_id
    (email/phone against congregation.db), falling back to an exact
    first+last name match -- a first-time visitor RSVPing may have no
    congregation.db record at all."""
    member_id = find_member_id(email or "", phone or "")
    if member_id is None:
        member_id = find_member_id_by_name(first_name, last_name)
    if not first_name and not last_name and member_id:
        member_name = find_member_name(member_id)
        if member_name:
            parts = member_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

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
               SET rsvp_status = ?, num_tickets = 1, child_count = ?, email = ?, phone = ?,
                   extra_fields = ?, submitted_at = ?, source = 'email'
               WHERE id = ?""",
            (rsvp_status, child_count, email or None, phone or None,
             json.dumps({"notes": notes}) if notes else None, received_at, existing["id"]),
        )
        log.info("Banquet RSVP updated (resubmission) — event_id=%s member_id=%s status=%s",
                  event_id, member_id, rsvp_status)
        return

    conn.execute(
        """INSERT INTO event_registrations
           (event_id, first_name, last_name, email, phone, num_tickets, child_count,
            rsvp_status, extra_fields, member_id, source, submitted_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'email', ?)""",
        (event_id, first_name, last_name, email or None, phone or None, child_count,
         rsvp_status, json.dumps({"notes": notes}) if notes else None, member_id, received_at),
    )
    log.info("Banquet RSVP recorded — event_id=%s member_id=%s status=%s children=%s",
              event_id, member_id, rsvp_status, child_count)


def _upsert_rsvp(conn: sqlite3.Connection, event_id: int, detection: dict, received_at: str) -> None:
    """Writes one event_registrations row per named attendee in the
    submission -- the primary respondent plus any "additional servant
    leaders in your family" the form collected. Each additional attendee is
    itself an actual servant on the invite roster, not just a headcount
    bump, so each needs its own row matched to its own member_id or
    banquet_report.py's non-responder list would wrongly show them as never
    having answered even though this submission covered them. child_count
    (the children/teens name+age list) is recorded only on the primary
    row -- it's a family-level number, not per-adult -- so summing
    child_count across an event's rows still gives the right total."""
    email = (detection.get("email") or "").strip()
    phone = (detection.get("phone") or "").strip()
    first_name = (detection.get("first_name") or "").strip()
    last_name = (detection.get("last_name") or "").strip()
    rsvp_status = "yes" if detection.get("attending") else "no"

    children = detection.get("children") or []
    children = [str(c).strip() for c in children if str(c).strip()]
    child_count = len(children)
    notes_parts = []
    base_notes = (detection.get("notes") or "").strip()
    if base_notes:
        notes_parts.append(base_notes)
    if children:
        notes_parts.append("Children/teens attending: " + "; ".join(children))
    notes = " | ".join(notes_parts) or None

    _upsert_person_row(conn, event_id, first_name, last_name, email or None, phone or None,
                        rsvp_status, child_count, notes, received_at)

    additional = detection.get("additional_attendees") or []
    for person in additional:
        if isinstance(person, dict):
            a_first = str(person.get("first_name") or "").strip()
            a_last = str(person.get("last_name") or "").strip()
        else:
            # Tolerate the model returning plain "First Last" strings
            # instead of the requested {first_name,last_name} objects.
            parts = str(person).strip().split(" ", 1)
            a_first = parts[0] if parts else ""
            a_last = parts[1] if len(parts) > 1 else ""
        if not a_first and not a_last:
            continue
        _upsert_person_row(conn, event_id, a_first, a_last, None, None,
                            rsvp_status, 0, None, received_at)


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

    detection = _parse_snappages_fields(body)
    if detection is None:
        detection = _classify(matched["event_name"], subject, body)
    if not detection or not detection.get("is_rsvp_submission"):
        conn.close()
        return None

    _upsert_rsvp(conn, matched["id"], detection, received_at)
    conn.commit()
    conn.close()
    return "read"
