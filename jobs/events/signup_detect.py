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
import json
import logging
import os
import re
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.events.matching import find_active_event, find_member_id
from jobs.events.schema import create_tables
from jobs.telegram.pending import store_pending_action

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:1b"  # same fast/cheap model as email_intake.py's own triage classifier

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


def _insert_registration(conn: sqlite3.Connection, event_id: int, detection: dict, received_at: str) -> int:
    email = (detection.get("email") or "").strip()
    phone = (detection.get("phone") or "").strip()
    member_id = find_member_id(email, phone)
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
            (detection.get("first_name") or "").strip(),
            (detection.get("last_name") or "").strip(),
            email or None,
            phone,
            (detection.get("ticket_type") or "").strip(),
            num_tickets,
            member_id,
            received_at,
        ),
    )
    return cur.lastrowid


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
    matched = find_active_event(conn, name_guess, f"{subject}\n{body}")

    if matched:
        _insert_registration(conn, matched["id"], detection, received_at)
        conn.commit()
        conn.close()
        log.info(
            "Event signup matched — event=%r registrant=%s tickets=%s",
            matched["event_name"], who, detection.get("num_tickets"),
        )
        return "read"

    conn.close()

    if _has_pending_event_new(msg_id):
        log.info("Skipping re-prompt — pending event_new action already open for uid=%s", msg_id)
        return "pending"

    payload = {
        "uid": msg_id,
        "sender_email": sender_email,
        "subject": subject,
        "received_at": received_at,
        "event_name_guess": name_guess or subject,
        "detection": detection,
    }
    pending_id = store_pending_action("event_new", 0, payload)

    display_name = name_guess or "(unnamed event)"
    text = (
        f"🎪 New signup notification — no matching tracked event\n\n"
        f"From: {sender_email}\n"
        f"Subject: {subject}\n"
        f"Registrant: {who}\n\n"
        f"Start tracking \"{display_name}\" as a new event?"
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
