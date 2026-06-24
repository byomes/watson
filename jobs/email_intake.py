#!/usr/bin/env python3
"""
jobs/email_intake.py — Fetch unread Gmail, classify with Ollama, alert urgent via Telegram.

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

_CLASSIFY_PROMPT = (
    "Classify this email as urgent, queue, or discard. "
    "Urgent = pastoral, personal, time-sensitive, or from a known person. "
    "Queue = newsletters, ministry info, non-urgent requests. "
    "Discard = spam, promotions, automated notifications. "
    "Reply with one word only: urgent, queue, or discard.\n\n"
    "From: {sender}\nSubject: {subject}\nSnippet: {snippet}"
)


def init_gmail_inbox():
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
    conn.commit()
    conn.close()


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


def mark_as_read(uid):
    mail = _imap_connect()
    mail.store(uid.encode() if isinstance(uid, str) else uid, "+FLAGS", "\\Seen")
    mail.logout()


def _tg(text: str) -> None:
    """Send a pre-formatted message directly to Telegram."""
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


def _classify(sender, subject, snippet):
    prompt = _CLASSIFY_PROMPT.format(sender=sender, subject=subject, snippet=snippet)
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        word = resp.json().get("response", "").strip().lower().split()[0]
        if word in ("urgent", "queue", "discard"):
            return word
        log.warning("Unexpected classification response: %r — defaulting to queue", word)
        return "queue"
    except Exception as exc:
        log.error("Ollama classification failed: %s", exc)
        return "queue"


def _store(from_address, subject, snippet, full_body, received_at, classification):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO gmail_inbox
               (from_address, subject, snippet, full_body, received_at, status, classification)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (from_address, subject, snippet, full_body, received_at, classification, classification),
    )
    conn.commit()
    conn.close()


def _send_telegram(sender, subject, snippet):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set — cannot send urgent alert")
        return
    text = f"📬 Urgent email\n\nFrom: {sender}\nSubject: {subject}\n\n{snippet[:200]}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("Telegram alert failed: %s", exc)


def _extract_address(sender_field):
    match = re.search(r"<(.+?)>", sender_field)
    if match:
        return match.group(1).strip().lower()
    return sender_field.strip().lower()


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


def _handle_bill_email(sender, subject, body, received_at, msg_id):
    log.info("Bill directive received: %s", subject)

    # Step 1 — check if forwarded team email
    if is_forwarded_email(subject, body):
        result = process_inbound(subject, body, received_at)
        if result.get("matched"):
            log.info("Team inbound matched: %s", result.get("member_name"))
            return

    # Step 2 — Ollama digest
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


def run():
    init_gmail_inbox()
    emails = get_unread()
    log.info("Found %d unread email(s)", len(emails))

    for email in emails:
        msg_id    = email["id"]
        sender    = email["sender"]
        subject   = email["subject"]
        body      = email["body"]
        received_at = email.get("date") or datetime.utcnow().isoformat()
        snippet   = body[:200]

        addr = _extract_address(sender)

        # Bill's emails — handled as directives (includes forwarded team emails)
        if addr in WHITELIST:
            _handle_bill_email(sender, subject, body, received_at, msg_id)
            mark_as_read(msg_id)
            continue

        # Non-Bill forwarded emails — check for team inbound match
        if is_forwarded_email(subject, body):
            result = process_inbound(subject, body, received_at)
            if result.get("matched"):
                mark_as_read(msg_id)
                log.info("Team inbound matched: %s (tasks_created=%d)", result.get("member_name"), result.get("tasks_created", 0))
                continue

        classification = _classify(sender, subject, snippet)
        log.info("%-10s | %s | %s", classification.upper(), sender[:40], subject[:60])

        mark_as_read(msg_id)

        if classification == "discard":
            continue

        _store(sender, subject, snippet, body, received_at, classification)

        if classification == "urgent":
            _send_telegram(sender, subject, snippet)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
