#!/usr/bin/env python3
"""
jobs/email_intake.py — Fetch unread Gmail, triage via Ollama, prompt Dr. Bill via Telegram.

Watson never marks email as read, archives, or acts on non-whitelist email without
an explicit Telegram response from Dr. Bill. If Dr. Bill does not respond, the email
stays unread in Gmail indefinitely. No timeout, no auto-archive, no fallback.

Crontab (run on watson server):
  */15 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/email_intake.py
"""

import email as email_lib
import email.header
import imaplib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.team.inbound import is_forwarded_email, process_inbound
import jobs.code_agent.agent as code_agent

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

WATSON_DIRECTIVE_LABEL = "Label_1238322494970583528"

WHITELIST = [
    "bill.yomes@gmail.com",
    "pastorbill@catalyst302.com",
    "me@williamckyomes.com",
    "bill@faithmakessense.com",
]

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"
TELEGRAM_CHAR_LIMIT = 4000

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")

_CATEGORY_ICONS = {
    "congregation": "👥",
    "known_contact": "👤",
    "ministry": "⛪",
    "newsletter": "📰",
    "notification": "🔔",
    "receipt": "🧾",
    "spam": "🗑️",
    "unknown": "❓",
}

_VALID_CATEGORIES = set(_CATEGORY_ICONS.keys())

_TRIAGE_PROMPT = (
    "You are Watson, an AI assistant for Dr. Bill Yomes, a church pastor. Triage this incoming email.\n\n"
    "Categories:\n"
    "- congregation: sent by a church member, parishioner, or family in the congregation\n"
    "- known_contact: sent by a known colleague, ministry partner, vendor, or professional contact\n"
    "- ministry: sent by a ministry organization, partner church, or church-related entity\n"
    "- newsletter: a newsletter, blog digest, or subscription update\n"
    "- notification: automated system notification, alert, or status update\n"
    "- receipt: purchase confirmation, receipt, or order update\n"
    "- spam: unsolicited bulk email, phishing, or promotional spam\n"
    "- unknown: cannot determine\n\n"
    "Reply ONLY with valid JSON (no markdown, no explanation):\n"
    '{{\n'
    '  "category": "<one of the above>",\n'
    '  "summary": "<2-3 sentence summary of what this email is about>",\n'
    '  "suggested_action": "<what Bill should do>",\n'
    '  "reply_warranted": <true or false>\n'
    '}}\n\n'
    "From: {sender_name} <{sender_email}>\n"
    "Subject: {subject}\n"
    "Body (first 600 chars):\n"
    "{body_snippet}"
)


# ── DB init ───────────────────────────────────────────────────────────────────

def _init_tables():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gmail_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_address TEXT,
            subject TEXT,
            snippet TEXT,
            full_body TEXT,
            received_at TEXT,
            status TEXT DEFAULT 'queue',
            classification TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ministry_emails (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_email  TEXT NOT NULL,
            sender_name   TEXT,
            subject       TEXT,
            body          TEXT,
            received_at   TEXT,
            category      TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE team_tasks ADD COLUMN source TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.close()


# ── IMAP ──────────────────────────────────────────────────────────────────────

def _imap_connect():
    gmail_addr = os.getenv("WATSON_GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(gmail_addr, gmail_pass)
    mail.select("inbox")
    return mail


def get_unread():
    mail = _imap_connect()
    _, data = mail.search(None, "UNSEEN")
    uids = data[0].split()
    results = []
    for uid in uids:
        _, msg_data = mail.fetch(uid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw)
        subject_parts = email.header.decode_header(msg.get("Subject", ""))
        subject = ""
        for part, enc in subject_parts:
            if isinstance(part, bytes):
                subject += part.decode(enc or "utf-8", errors="replace")
            else:
                subject += part
        sender = msg.get("From", "")
        date   = msg.get("Date", "")
        body   = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        results.append({
            "id":      uid.decode(),
            "subject": subject,
            "sender":  sender,
            "date":    date,
            "body":    body,
        })
    mail.logout()
    return results


def mark_as_read(uid: str) -> None:
    mail = _imap_connect()
    mail.store(uid.encode() if isinstance(uid, str) else uid, "+FLAGS", "\\Seen")
    mail.logout()


def delete_email(uid: str) -> None:
    """Move email to Gmail Trash."""
    mail = _imap_connect()
    mail.store(uid.encode() if isinstance(uid, str) else uid, "+FLAGS", "\\Deleted")
    mail.expunge()
    mail.logout()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_address(sender_field: str) -> str:
    match = re.search(r"<(.+?)>", sender_field)
    if match:
        return match.group(1).strip().lower()
    return sender_field.strip().lower()


def _extract_name(sender_field: str) -> str:
    match = re.match(r'^"?([^"<]+?)"?\s*<', sender_field)
    if match:
        return match.group(1).strip()
    addr = _extract_address(sender_field)
    return addr.split("@")[0]


def _tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


def _send_directive_telegram(sender, subject):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set — cannot send directive alert")
        return
    text = f"📬 New directive\n\nFrom: {sender}\nSubject: {subject}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("Telegram directive alert failed: %s", exc)


# ── Meeting summary detection & handling ──────────────────────────────────────

_MEETING_SUMMARY_PROMPT = """\
You are Watson, Dr. Bill Yomes's assistant. Analyze this email and return JSON only, no other text.

This email was written BY Dr. Bill Yomes. Determine if it is a meeting summary or leader update — meaning it describes a meeting or interaction Bill had with one of his church leaders, and includes action items or tasks.

Rules:
- leader_name: the name of the OTHER person Bill met with (NOT Bill himself). This is a church leader Bill manages.
- tasks_for_leader: tasks or action items the LEADER needs to do.
- tasks_for_bill: tasks or action items DR. BILL himself needs to do.
- Never put Bill in leader_name. Bill is always the author, never the leader being discussed.

Return:
{{
  "is_meeting_summary": true or false,
  "leader_name": "full name of the leader Bill met with, or null",
  "tasks_for_leader": ["task 1", "task 2"],
  "tasks_for_bill": ["task 1", "task 2"],
  "summary": "2-3 sentence summary of the meeting for the notes log"
}}

Return empty arrays [] if no tasks found. Never return placeholder strings.

Email:
Subject: {subject}
Body: {body}"""


def _detect_meeting_summary(subject: str, body: str) -> dict | None:
    prompt = _MEETING_SUMMARY_PROMPT.format(subject=subject, body=body)
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=90,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as exc:
        log.error("Meeting summary detection failed: %s", exc)
        return None


def _handle_meeting_summary(detection: dict) -> bool:
    """Process a detected meeting summary. Returns True if fully handled."""
    leader_name     = (detection.get("leader_name") or "").strip()
    tasks_for_leader = [t.strip() for t in (detection.get("tasks_for_leader") or []) if isinstance(t, str) and t.strip()]
    tasks_for_bill   = [t.strip() for t in (detection.get("tasks_for_bill")   or []) if isinstance(t, str) and t.strip()]
    summary          = (detection.get("summary") or "").strip()

    # Leader match
    member_id   = None
    member_name = None
    if leader_name:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, name FROM team_members WHERE name LIKE ? AND active=1 LIMIT 1",
                (f"%{leader_name}%",),
            ).fetchone()
            if not row:
                first = leader_name.split()[0]
                rows = conn.execute(
                    "SELECT id, name FROM team_members WHERE name LIKE ? AND active=1",
                    (f"{first}%",),
                ).fetchall()
                if len(rows) == 1:
                    row = rows[0]
            conn.close()
            if row:
                member_id   = row["id"]
                member_name = row["name"]
        except Exception as exc:
            log.error("Leader DB lookup failed: %s", exc)

    if not member_id:
        _tg(
            f"⚠️ Meeting summary received but couldn't match leader: "
            f"{leader_name or '(unknown)'}. No tasks logged."
        )
        return False

    try:
        conn = sqlite3.connect(DB_PATH)

        for task in tasks_for_leader:
            conn.execute(
                "INSERT INTO team_tasks (member_id, title, assigned_by, status, source, category, created_at) "
                "VALUES (?, ?, 'bill', 'active', 'email_intake', 'catalyst', datetime('now','localtime'))",
                (member_id, task),
            )

        for task in tasks_for_bill:
            conn.execute(
                "INSERT INTO team_tasks (member_id, title, assigned_by, status, source, category, created_at) "
                "VALUES (12, ?, 'bill', 'active', 'email_intake', 'catalyst', datetime('now','localtime'))",
                (task,),
            )

        if summary:
            conn.execute(
                "INSERT INTO shared_notes (member_id, content, author, created_at) "
                "VALUES (?, ?, 'bill', datetime('now','localtime'))",
                (member_id, summary),
            )

        try:
            conn.execute(
                "UPDATE team_members SET last_activity_date=date('now','localtime'), last_comms_date=date('now','localtime') WHERE id=?",
                (member_id,),
            )
        except sqlite3.OperationalError:
            conn.execute(
                "UPDATE team_members SET last_activity_date=date('now','localtime') WHERE id=?",
                (member_id,),
            )

        conn.commit()
        conn.close()
    except Exception as exc:
        log.error("Meeting summary DB write failed: %s", exc)
        _tg(f"⚠️ Meeting summary matched {member_name} but failed to log tasks: {exc}")
        return False

    _HONORIFICS = {"Dr.", "Dr", "Pastor", "Rev.", "Rev"}
    parts = member_name.split()
    first = parts[1] if len(parts) > 1 and parts[0] in _HONORIFICS else parts[0]
    _tg(
        f"📋 Meeting summary logged — {member_name}\n\n"
        f"Tasks for {first}: {len(tasks_for_leader)}\n"
        f"Tasks for you: {len(tasks_for_bill)}\n"
        f"Note logged: ✓"
    )
    return True


# ── Bill's own email (whitelist path) ─────────────────────────────────────────

def _handle_bill_email(sender, subject, body, received_at, msg_id):
    log.info("Bill directive received: %s", subject)

    if is_forwarded_email(subject, body):
        result = process_inbound(subject, body, received_at)
        if result.get("matched"):
            log.info("Team inbound matched: %s", result.get("member_name"))
            return

    detection = _detect_meeting_summary(subject, body)
    if detection and detection.get("is_meeting_summary"):
        if _handle_meeting_summary(detection):
            return

    prompt = (
        "You are Watson, Dr. Bill Yomes's administrative assistant. "
        "Bill sent you this email. Determine:\n"
        "1. What is Bill asking or sharing?\n"
        "2. Is this actionable (yes/no)?\n"
        "3. Do you need clarification to act (yes/no)?\n"
        "4. If clarification needed, what is your question? Keep it concise.\n"
        "5. A short summary (2-3 sentences max).\n\n"
        "Return only valid JSON:\n"
        '{\n'
        '  "summary": "string",\n'
        '  "actionable": true,\n'
        '  "needs_clarification": false,\n'
        '  "clarification_question": "string or null",\n'
        '  "action_taken": "string or null"\n'
        '}\n\n'
        f"Subject: {subject}\n\nBody:\n{body}"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "")
        result = json.loads(raw)
    except Exception as exc:
        log.error("Ollama digest failed: %s", exc)
        _tg(f"⚠️ Watson could not digest your email: {subject}\n\nOllama failed to process it.")
        return

    summary                = result.get("summary", "")
    needs_clarification    = result.get("needs_clarification", False)
    clarification_question = result.get("clarification_question") or ""

    if needs_clarification and clarification_question:
        tg_msg = (
            f"📧 Re: {subject}\n\n"
            f"Watson digest: {summary}\n\n"
            f"❓ {clarification_question}"
        )
        if len(tg_msg) <= TELEGRAM_CHAR_LIMIT:
            _tg(tg_msg)
        else:
            from jobs.email_job.gmail import send_as_watson
            email_body = (
                f"Dr. Bill,\n\n"
                f"Watson received your email: \"{subject}\"\n\n"
                f"Summary: {summary}\n\n"
                f"Before I proceed, I need clarification:\n\n"
                f"{clarification_question}\n\n"
                f"Watson | Administrative Assistant to Dr. Bill Yomes"
            )
            send_as_watson(to=sender, subject=f"Re: {subject}", body=email_body)
            _tg(f"📧 Watson emailed you a clarification question about: {subject}")
    else:
        action = result.get("action_taken") or "Logged and noted."
        tg_msg = (
            f"📧 Email digest: {subject}\n\n"
            f"{summary}\n\n"
            f"✅ {action}"
        )
        if len(tg_msg) <= TELEGRAM_CHAR_LIMIT:
            _tg(tg_msg)
        else:
            from jobs.email_job.gmail import send_as_watson
            send_as_watson(
                to=sender,
                subject=f"Watson digest: {subject}",
                body=f"Dr. Bill,\n\n{summary}\n\n✅ {action}\n\nWatson",
            )
            _tg(f"📧 Watson emailed you a digest of: {subject}")


# ── Non-whitelist triage ───────────────────────────────────────────────────────

def _cross_reference_sender(email_addr: str) -> tuple[str | None, int | None, str | None]:
    """Look up an email address in congregation.db and watson.db.

    Returns (name, member_id, source) or (None, None, None) if not found.
    member_id is from congregation.db only (None for watson.db hits).
    """
    try:
        cong = sqlite3.connect(CONG_DB)
        cong.row_factory = sqlite3.Row
        row = cong.execute(
            "SELECT name FROM members WHERE email = ? COLLATE NOCASE LIMIT 1",
            (email_addr,),
        ).fetchone()
        cong.close()
        if row:
            return row["name"], None, "congregation"
    except Exception as exc:
        log.warning("Congregation DB lookup failed: %s", exc)

    try:
        watson = sqlite3.connect(DB_PATH)
        watson.row_factory = sqlite3.Row
        row = watson.execute(
            "SELECT name FROM people WHERE email = ? COLLATE NOCASE LIMIT 1",
            (email_addr,),
        ).fetchone()
        watson.close()
        if row:
            return row["name"], None, "watson"
    except Exception as exc:
        log.warning("Watson DB people lookup failed: %s", exc)

    return None, None, None


def _triage_with_ollama(sender_name: str, sender_email: str, subject: str, body: str) -> dict:
    prompt = _TRIAGE_PROMPT.format(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        body_snippet=body[:600],
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if result.get("category") not in _VALID_CATEGORIES:
            result["category"] = "unknown"
        return result
    except Exception as exc:
        log.error("Ollama triage failed: %s", exc)
        return {
            "category": "unknown",
            "summary": f"Watson could not triage this email (Ollama error: {exc})",
            "suggested_action": "Review manually",
            "reply_warranted": False,
        }


def _send_triage_prompt(
    pending_id: int,
    category: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    summary: str,
    suggested_action: str,
    reply_warranted: bool,
) -> int | None:
    """Send the triage Telegram message with inline buttons. Returns Telegram message_id."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set — cannot send triage prompt")
        return None

    icon = _CATEGORY_ICONS.get(category, "❓")
    is_spam = category == "spam"

    reply_line = "\n💬 Reply may be warranted." if reply_warranted else ""
    text = (
        f"{icon} Email from {sender_name} <{sender_email}>\n"
        f"Subject: {subject}\n"
        f"Category: {category}\n\n"
        f"{summary}\n\n"
        f"Suggested: {suggested_action}"
        f"{reply_line}"
    )[:TELEGRAM_CHAR_LIMIT]

    second_btn_text = "🗑️ Delete" if is_spam else "🗑️ Mark as read"
    second_btn_data = f"et_delete:{pending_id}" if is_spam else f"et_markread:{pending_id}"

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Ingest and work", "callback_data": f"et_ingest:{pending_id}"},
            {"text": second_btn_text, "callback_data": second_btn_data},
        ]]
    }

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "reply_markup": keyboard,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")
    except Exception as exc:
        log.error("Telegram triage prompt failed: %s", exc)
        return None


def _handle_non_whitelist(
    msg_id: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    body: str,
    received_at: str,
) -> None:
    """Triage a non-whitelist email. Never marks as read. Stores pending action and prompts Bill."""
    # Cross-reference sender against congregation + people tables
    matched_name, matched_member_id, match_source = _cross_reference_sender(sender_email)
    if matched_name and not sender_name:
        sender_name = matched_name

    # Triage with Ollama
    triage = _triage_with_ollama(sender_name, sender_email, subject, body)
    category        = triage.get("category", "unknown")
    summary         = triage.get("summary", "")
    suggested_action = triage.get("suggested_action", "")
    reply_warranted = bool(triage.get("reply_warranted", False))

    # Store pending action (telegram_message_id = 0 placeholder, updated below)
    from jobs.telegram.pending import store_pending_action
    payload = {
        "uid":              msg_id,
        "sender_email":     sender_email,
        "sender_name":      sender_name,
        "subject":          subject,
        "body":             body[:2000],
        "received_at":      received_at,
        "category":         category,
        "summary":          summary,
        "suggested_action": suggested_action,
        "reply_warranted":  reply_warranted,
        "matched_member_id": matched_member_id,
        "match_source":     match_source,
    }
    pending_id = store_pending_action("email_triage", 0, payload)

    # Send triage prompt with inline buttons (pending_id embedded in callback_data)
    tg_msg_id = _send_triage_prompt(
        pending_id=pending_id,
        category=category,
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        summary=summary,
        suggested_action=suggested_action,
        reply_warranted=reply_warranted,
    )

    # Update tg_pending_actions with actual Telegram message_id
    if tg_msg_id:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE tg_pending_actions SET telegram_message_id=? WHERE id=?",
                (tg_msg_id, pending_id),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            log.error("Failed to update tg_pending_actions message_id: %s", exc)

    log.info(
        "Triage prompt sent — from=%s category=%s pending_id=%d",
        sender_email, category, pending_id,
    )


# ── Action handlers (called from bot.py callbacks) ────────────────────────────

def handle_ingest_action(payload: dict) -> str:
    """Route email after Bill taps 'Ingest and work'. Returns a status string for Telegram."""
    category        = payload.get("category", "unknown")
    sender_email    = payload.get("sender_email", "")
    sender_name     = payload.get("sender_name", "") or sender_email
    subject         = payload.get("subject", "")
    body            = payload.get("body", "")
    received_at     = payload.get("received_at", "")
    reply_warranted = payload.get("reply_warranted", False)
    uid             = payload.get("uid", "")

    if category in ("congregation", "known_contact"):
        if reply_warranted:
            email_dict = {
                "message_id":   uid,
                "sender_name":  sender_name,
                "sender_email": sender_email,
                "subject":      subject,
                "body":         body,
            }
            try:
                from jobs.email_reply.drafter import draft_reply
                from jobs.email_reply.handler import save_pending, send_telegram_notification
                draft = draft_reply(email_dict)
                save_pending(email_dict, draft)
                send_telegram_notification(email_dict, draft)
                return f"✅ Draft reply queued for approval — {sender_name}"
            except Exception as exc:
                log.error("Reply draft pipeline failed: %s", exc)
                return f"⚠️ Draft failed: {exc}"
        else:
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """INSERT INTO gmail_inbox
                       (from_address, subject, snippet, full_body, received_at, status, classification)
                       VALUES (?, ?, ?, ?, ?, 'pastoral', ?)""",
                    (sender_email, subject, body[:200], body, received_at, category),
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                log.error("gmail_inbox insert failed: %s", exc)
            return f"✅ Logged for follow-up — {sender_name}"

    if category in ("ministry", "unknown"):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """INSERT INTO ministry_emails
                   (sender_email, sender_name, subject, body, received_at, category)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sender_email, sender_name, subject, body, received_at, category),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            log.error("ministry_emails insert failed: %s", exc)
        return f"✅ Logged in ministry inbox — {sender_name}"

    if category in ("newsletter", "notification", "receipt"):
        if uid:
            try:
                mark_as_read(uid)
            except Exception as exc:
                log.error("mark_as_read failed: %s", exc)
        return f"✅ Marked as read — {subject[:60]}"

    if category == "spam":
        if uid:
            try:
                delete_email(uid)
            except Exception as exc:
                log.error("delete_email failed: %s", exc)
        return "🗑️ Deleted (spam)"

    return f"✅ Logged — {sender_name}"


def handle_markread_action(payload: dict) -> str:
    """Mark the email as read after Bill taps 'Mark as read'."""
    uid = payload.get("uid", "")
    subject = payload.get("subject", "")
    if uid:
        try:
            mark_as_read(uid)
        except Exception as exc:
            log.error("mark_as_read failed: %s", exc)
            return f"⚠️ Could not mark as read: {exc}"
    return f"✅ Marked as read — {subject[:60]}"


def handle_delete_action(payload: dict) -> str:
    """Delete (trash) the email after Bill taps 'Delete'."""
    uid = payload.get("uid", "")
    if uid:
        try:
            delete_email(uid)
        except Exception as exc:
            log.error("delete_email failed: %s", exc)
            return f"⚠️ Could not delete: {exc}"
    return "🗑️ Deleted"


# ── Main run loop ──────────────────────────────────────────────────────────────

def _get_donna_email_from_db() -> str | None:
    """Look up Donna's email from team_members at runtime."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email FROM team_members WHERE name LIKE '%Donna%' AND active=1 LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row["email"]:
            return row["email"].strip().lower()
    except Exception as exc:
        log.warning("Could not look up Donna's email: %s", exc)
    return None


def run():
    _init_tables()
    emails = get_unread()
    log.info("Found %d unread email(s)", len(emails))

    # Resolve Donna's email once per run for task-proposal reply detection
    donna_email = _get_donna_email_from_db()

    for msg in emails:
        msg_id      = msg["id"]
        sender_raw  = msg["sender"]
        subject     = msg["subject"]
        body        = msg["body"]
        received_at = msg.get("date") or datetime.utcnow().isoformat()

        addr = _extract_address(sender_raw)
        name = _extract_name(sender_raw)

        # Bill's own email — directive path (unchanged behavior)
        if addr in WHITELIST:
            _handle_bill_email(sender_raw, subject, body, received_at, msg_id)
            mark_as_read(msg_id)
            continue

        # Donna task-proposal reply — whitelist check
        if donna_email and addr == donna_email and "Watson — Proposed tasks" in subject:
            log.info("Donna task proposal reply received: %s", subject)
            try:
                from jobs.team.note_task_scan import handle_donna_proposal_reply
                handle_donna_proposal_reply(body)
            except Exception as exc:
                log.error("handle_donna_proposal_reply failed: %s", exc)
            mark_as_read(msg_id)
            continue

        # Non-Bill forwarded team emails (unchanged behavior)
        if is_forwarded_email(subject, body):
            result = process_inbound(subject, body, received_at)
            if result.get("matched"):
                mark_as_read(msg_id)
                log.info(
                    "Team inbound matched: %s (tasks_created=%d)",
                    result.get("member_name"),
                    result.get("tasks_created", 0),
                )
                continue

        # All other email — triage and prompt Bill; never mark read here
        _handle_non_whitelist(
            msg_id=msg_id,
            sender_name=name,
            sender_email=addr,
            subject=subject,
            body=body,
            received_at=received_at,
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
