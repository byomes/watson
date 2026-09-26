"""jobs/events/signup_detect.py — detect event-signup notification emails in
Watson's inbox (jobs/email_intake.py polls this every minute) and either
silently attach the registration to an actively-tracked event, or ask Bill
via Telegram whether to start tracking a new one.

Called from jobs/email_intake.py's run() loop, in the same "special-case
before generic triage" slot as jobs/privacy/confirm.py — same contract:
returns "read" (caller marks the email read and moves on), "pending"
(handled — a pending action was stored and a Telegram prompt sent — but
leave the email unread until Bill responds, matching this file's top-level
"never act on non-whitelist email without an explicit Telegram response"
rule), or None (not a signup email at all; caller's generic triage runs
unchanged).
"""
import logging
import os
import re
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.ollama_json import generate_json
from core.vacation import vacation_gate
from jobs.events.matching import find_active_event, find_member_id, find_member_name
from jobs.events.schema import create_tables
from jobs.telegram.pending import store_pending_action

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"  # same fast/cheap model as email_intake.py's own triage classifier

_DETECT_PROMPT = (
    "You are Watson, an AI assistant for a church. Determine whether this email is an "
    "automated notification that ONE PERSON registered, signed up, bought a ticket, or RSVP'd "
    "for a church event (e.g. a picnic, retreat, class, or service project) through a signup "
    "or ticketing platform. This is NOT a newsletter, a general announcement, a receipt for a "
    "donation, or a personal message.\n\n"
    "Reply ONLY with valid JSON (no markdown, no explanation):\n"
    '{{\n'
    '  "is_event_signup": true or false,\n'
    '  "event_name_guess": "<best guess at the event name, or empty string>",\n'
    '  "first_name": "<registrant first name, or empty string>",\n'
    '  "last_name": "<registrant last name, or empty string>",\n'
    '  "email": "<registrant email, or empty string>",\n'
    '  "phone": "<registrant phone, or empty string>",\n'
    '  "ticket_type": "<ticket/registration type, or empty string>",\n'
    '  "num_tickets": <integer, default 1>\n'
    '}}\n\n'
    "Subject: {subject}\n"
    "Body (first 1200 chars):\n"
    "{body_snippet}"
)


# Cheap keyword prefilter, checked before spending an Ollama call — the
# Beelink serializes all Ollama generate requests (OLLAMA_NUM_PARALLEL=1, see
# WATSON_ARCHITECTURE.md), and email_intake.py's own generic non-whitelist
# triage already spends one LLM call per email that reaches this point, so
# this file should not double that cost on every ordinary email that's
# obviously not a signup notification.
_SIGNUP_KEYWORDS_RE = re.compile(
    r"\b(regist(er|ration)|sign[\s-]?up|rsvp|ticket|reservation|you'?re (going|attending|signed up)|"
    r"confirm(ed|ation)? (your|for))\b",
    re.IGNORECASE,
)


def _looks_like_signup(subject: str, body: str) -> bool:
    return bool(_SIGNUP_KEYWORDS_RE.search(f"{subject}\n{body[:2000]}"))


def _classify(subject: str, body: str) -> dict | None:
    prompt = _DETECT_PROMPT.format(subject=subject, body_snippet=body[:1200])
    try:
        return generate_json(OLLAMA_URL, model=OLLAMA_MODEL, prompt=prompt, timeout=60, retries=1)
    except Exception as exc:
        log.error("Event signup classification failed: %s", exc)
        return None


def _has_pending_event_new(msg_id: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT 1 FROM tg_pending_actions "
            "WHERE type='event_new' AND status='pending' "
            "AND json_extract(payload, '$.uid') = ? LIMIT 1",
            (msg_id,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception as exc:
        log.warning("event_new pending dedup check failed: %s", exc)
        return False


def _resolve_stale_email_triage(msg_id: str, who: str, event_name: str) -> None:
    """A signup email that failed this file's own _classify() on an earlier
    poll cycle falls through to email_intake.py's generic non-whitelist
    triage, which stores an 'email_triage' pending action and pages Bill on
    Telegram to ask what to do with it. Since the email stays unread until
    something marks it read, this file re-tries the same email every poll
    (~1/min) and usually succeeds a cycle or two later -- but nothing was
    cancelling that earlier Telegram prompt, leaving Bill an open "please
    review" ask for a registration Watson had already silently finished
    intaking. Confirmed live 2026-09-26 (bug_tracker) on two Church Picnic
    registrations. Called only from the already-matched, already-inserted
    path below; a genuinely new/unmatched signup still gets its own
    intentional event_new prompt further down."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, telegram_message_id FROM tg_pending_actions "
            "WHERE type='email_triage' AND status='pending' "
            "AND json_extract(payload, '$.uid') = ? LIMIT 1",
            (msg_id,),
        ).fetchone()
        conn.close()
    except Exception as exc:
        log.warning("Stale email_triage lookup failed for uid=%s: %s", msg_id, exc)
        return
    if not row:
        return

    from jobs.telegram.pending import mark_done
    mark_done(row["id"])
    log.info(
        "Auto-resolved stale email_triage pending_id=%d for uid=%s (already logged as %s registration for %s)",
        row["id"], msg_id, event_name, who,
    )

    tg_msg_id = row["telegram_message_id"]
    if not tg_msg_id or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": tg_msg_id,
                "text": f"✅ Auto-resolved — this was a \"{event_name}\" registration for "
                        f"{who}, already logged. (Watson's first pass on this email "
                        f"couldn't parse it; a later retry caught it.)",
            },
            timeout=15,
        )
    except Exception as exc:
        log.warning("Failed to edit stale triage message for uid=%s: %s", msg_id, exc)


def _tg_send(text: str, keyboard: dict | None = None) -> int | None:
    if vacation_gate("normal", "jobs.events.signup_detect", text):
        return None
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set — cannot send event signup prompt")
        return None
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return None


def _insert_registration(
    conn: sqlite3.Connection, event_id: int, detection: dict, received_at: str
) -> tuple[int, str, str, int | None]:
    """Returns (row_id, first_name, last_name, member_id) -- the final,
    post-fallback values -- so callers can tell whether the registrant ended
    up genuinely nameless (see _unmatched_alert below)."""
    email = (detection.get("email") or "").strip()
    phone = (detection.get("phone") or "").strip()
    member_id = find_member_id(email, phone)
    first_name = (detection.get("first_name") or "").strip()
    last_name = (detection.get("last_name") or "").strip()
    if not first_name and not last_name and member_id:
        # The classifier sometimes has nothing to go on (e.g. a Subsplash
        # notification whose body never spells out the registrant's name),
        # but find_member_id still matched them by email/phone -- use the
        # congregation.db name rather than leaving the registrant blank.
        # Confirmed live 2026-09-18/20: Hayride and Bonfire registrations
        # both landed with empty first/last name despite a real member_id.
        member_name = find_member_name(member_id)
        if member_name:
            parts = member_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
    try:
        num_tickets = int(detection.get("num_tickets") or 1)
    except (TypeError, ValueError):
        num_tickets = 1
    cur = conn.execute(
        """INSERT INTO event_registrations
           (event_id, first_name, last_name, email, phone, ticket_type,
            num_tickets, member_id, source, submitted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'email', ?)""",
        (
            event_id,
            first_name,
            last_name,
            email or None,
            phone,
            (detection.get("ticket_type") or "").strip(),
            num_tickets,
            member_id,
            received_at,
        ),
    )
    return cur.lastrowid, first_name, last_name, member_id


def _alert_unmatched_signup(
    event_name: str, sender_email: str, subject: str, row_id: int
) -> None:
    """Bill asked (2026-09-20) to hear about this directly: the classifier
    found no name AND find_member_id found no congregation.db match either,
    so the registration landed with no name at all and nothing to fall back
    to -- most likely a first-time visitor whose very first interaction with
    the church was signing up for this event, before any connect card or
    other record of them exists. Rare (the classifier or the member match
    alone usually gives us something), but silent otherwise -- Bill would
    only ever find it by noticing a nameless row on the dashboard."""
    _tg_send(
        f"⚠️ Unmatched signup for \"{event_name}\": no name could be extracted "
        f"and no existing member matched their email/phone.\n\n"
        f"From: {sender_email}\n"
        f"Subject: {subject}\n"
        f"event_registrations id: {row_id}\n\n"
        f"Likely a first-time visitor — check the raw email and add their "
        f"name on the Events tab. - Watson"
    )


def _notify_creator_on_first_match(conn: sqlite3.Connection, event_id: int, event_name: str, registrant: str) -> None:
    """Tells the leader who set up this event (church_events.created_by --
    set by bot.py's _handle_new_event_notice), and Dr. Bill, once their
    event's first registration actually matches, since that leader almost
    always sends a test registration right after creating the event and
    otherwise never hears back whether it worked -- and Bill asked to be
    kept in the loop on those tests too (2026-09-18). Fires only once per
    event (creator_notified guards it) so real congregant signups after
    the first don't keep pinging anyone. Bill is skipped as a second
    recipient when he's the creator himself (he already gets the creator
    message above). No-ops per recipient if they're not an onboarded
    Telegram person, or (Bill's message) during vacation_gate -- this is a
    nice-to-have confirmation, never something a registration should be
    blocked or delayed on."""
    row = conn.execute(
        "SELECT created_by, creator_notified FROM church_events WHERE id = ?", (event_id,)
    ).fetchone()
    if not row or not row["created_by"] or row["creator_notified"]:
        return
    creator = row["created_by"]
    notified = False

    person = conn.execute("SELECT id FROM people WHERE name = ?", (creator,)).fetchone()
    if person:
        from jobs.telegram.send_to_person import send_to_person
        if send_to_person(
            person["id"],
            f"Good news, \"{event_name}\" just picked up a registration ({registrant}). "
            f"Tracking is working. - Watson",
        ):
            notified = True

    if creator != "Bill Yomes":
        if _tg_send(
            f"{creator}'s \"{event_name}\" just picked up its first registration ({registrant}). "
            f"Tracking is working. - Watson"
        ):
            notified = True

    if notified:
        conn.execute("UPDATE church_events SET creator_notified = 1 WHERE id = ?", (event_id,))


def handle_event_signup_email(
    msg_id: str, sender_email: str, subject: str, body: str, received_at: str
) -> str | None:
    if not _looks_like_signup(subject, body):
        return None

    create_tables()

    detection = _classify(subject, body)
    if not detection or not detection.get("is_event_signup"):
        return None

    name_guess = (detection.get("event_name_guess") or "").strip()
    who = f"{(detection.get('first_name') or '').strip()} {(detection.get('last_name') or '').strip()}".strip() or sender_email

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    matched = find_active_event(conn, name_guess, f"{subject}\n{body}")

    if matched:
        row_id, first_name, last_name, member_id = _insert_registration(
            conn, matched["id"], detection, received_at
        )
        _notify_creator_on_first_match(conn, matched["id"], matched["event_name"], who)
        conn.commit()
        conn.close()
        log.info(
            "Event signup matched — event=%r registrant=%s tickets=%s",
            matched["event_name"], who, detection.get("num_tickets"),
        )
        if not first_name and not last_name and not member_id:
            _alert_unmatched_signup(matched["event_name"], sender_email, subject, row_id)
        _resolve_stale_email_triage(msg_id, who, matched["event_name"])
        return "read"

    conn.close()

    if _has_pending_event_new(msg_id):
        log.info("Skipping re-prompt — pending event_new action already open for uid=%s", msg_id)
        return "pending"

    # body was previously dropped entirely after classification — Bill
    # couldn't see the actual signup email, same complaint as email_intake.py's
    # generic triage. Stored now (2026-09-11), same 2000-char convention as
    # email_intake.py's email_triage payload.
    payload = {
        "uid": msg_id,
        "sender_email": sender_email,
        "subject": subject,
        "body": body[:2000],
        "received_at": received_at,
        "event_name_guess": name_guess or subject,
        "detection": detection,
    }
    pending_id = store_pending_action("event_new", 0, payload)

    display_name = name_guess or "(unnamed event)"
    body_snippet = body.strip()[:400]
    ellipsis = "…" if len(body.strip()) > 400 else ""
    text = (
        f"🎪 New signup notification: no matching tracked event\n\n"
        f"From: {sender_email}\n"
        f"Subject: {subject}\n"
        f"Registrant: {who}\n\n"
        f"---\n{body_snippet}{ellipsis}\n---\n\n"
        f"Start tracking \"{display_name}\" as a new event?\n"
        f"Reply with the correct event name to track it under that instead, "
        f"or \"ignore\" to skip."
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Yes, track it", "callback_data": f"evnew_yes:{pending_id}"},
            {"text": "❌ No, ignore",   "callback_data": f"evnew_no:{pending_id}"},
        ]]
    }
    tg_msg_id = _tg_send(text, keyboard)
    if tg_msg_id:
        try:
            db = sqlite3.connect(DB_PATH)
            db.execute(
                "UPDATE tg_pending_actions SET telegram_message_id=? WHERE id=?",
                (tg_msg_id, pending_id),
            )
            db.commit()
            db.close()
        except Exception as exc:
            log.error("Failed to update tg_pending_actions message_id: %s", exc)

    log.info("Event-new prompt sent — from=%s pending_id=%d", sender_email, pending_id)
    return "pending"


# ── Action handlers (called from bot.py callbacks) ────────────────────────────

def handle_event_new_yes(payload: dict) -> str:
    """Bill tapped 'Yes, track it' — create the event and the first registration."""
    detection = payload.get("detection") or {}
    event_name = (payload.get("event_name_guess") or "New Event").strip()
    received_at = payload.get("received_at", "")
    uid = payload.get("uid", "")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO church_events (event_name, start_date, tracking_active) "
        "VALUES (?, date('now'), 1)",
        (event_name,),
    )
    event_id = cur.lastrowid
    _insert_registration(conn, event_id, detection, received_at)
    conn.commit()
    conn.close()

    if uid:
        try:
            from jobs.email_intake import mark_as_read
            mark_as_read(uid)
        except Exception as exc:
            log.error("mark_as_read failed for event_new: %s", exc)

    who = f"{detection.get('first_name', '')} {detection.get('last_name', '')}".strip() or "1 registrant"
    return f"✅ Now tracking \"{event_name}\" — {who} logged. Edit start date/details from the Events tab."


def handle_event_new_no(payload: dict) -> str:
    """Bill tapped 'No, ignore' — mark the email read, no event created."""
    uid = payload.get("uid", "")
    if uid:
        try:
            from jobs.email_intake import mark_as_read
            mark_as_read(uid)
        except Exception as exc:
            log.error("mark_as_read failed for event_new dismissal: %s", exc)
    return "Ignored — no event created."


_IGNORE_KEYWORDS = ("ignore", "skip", "not an event", "junk", "spam", "delete")
# "no" is checked separately as a whole word only (regex \b) — a plain
# substring check matched it inside "know", "announcement", "november",
# incorrectly declining a real event named e.g. "November Fellowship Night".
# Confirmed live 2026-09-11 before this shipped.
_IGNORE_WORD_RE = re.compile(r"\bno\b", re.IGNORECASE)


def handle_event_new_reply(payload: dict, instruction: str) -> str:
    """Bill replied with free text instead of tapping a button — added
    2026-09-11 per Bill, same complaint as email_intake.py's generic triage:
    no way to tell Watson what to do with a signup email it couldn't match
    to a tracked event.

    A clear decline routes to the exact same handler the 'No' button calls.
    Anything else is treated as the event name Bill wants it tracked under
    (the auto-detected guess is often wrong — e.g. "New Event" — and this
    is the direct fix: reply with the real name instead of accepting a bad
    guess or having to fix it later)."""
    lower = instruction.strip().lower()
    if any(kw in lower for kw in _IGNORE_KEYWORDS) or _IGNORE_WORD_RE.search(lower):
        return handle_event_new_no(payload)
    corrected = {**payload, "event_name_guess": instruction.strip()}
    return handle_event_new_yes(corrected)
