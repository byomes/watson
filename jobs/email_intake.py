#!/usr/bin/env python3
"""
jobs/email_intake.py — Fetch unread Gmail, classify with Ollama, alert urgent via Telegram.

Crontab (run on watson server):
  */15 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/email_intake.py
"""

import logging
import re
import sqlite3
from datetime import datetime

import requests

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.email_job.gmail import get_unread, mark_as_read
import jobs.code_agent.agent as code_agent

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


def poll_directives():
    emails = get_unread(label=WATSON_DIRECTIVE_LABEL)
    log.info("Found %d WATSON_DIRECTIVE email(s)", len(emails))

    for email in emails:
        msg_id  = email["id"]
        sender  = _extract_address(email["sender"])
        subject = email["subject"]
        body    = email["body"]
        received_at = email.get("date") or datetime.utcnow().isoformat()

        if sender not in WHITELIST:
            log.warning("Directive from non-whitelisted sender %s — skipping", sender)
            mark_as_read(msg_id)
            continue

        mark_as_read(msg_id)

        if "watson build:" in subject.lower():
            code_agent.handle(subject, body)
        else:
            _store(sender, subject, body[:200], body, received_at, "directive")
            _send_directive_telegram(sender, subject)


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

        classification = _classify(sender, subject, snippet)
        log.info("%-10s | %s | %s", classification.upper(), sender[:40], subject[:60])

        mark_as_read(msg_id)

        if classification == "discard":
            continue

        _store(sender, subject, snippet, body, received_at, classification)

        if classification == "urgent":
            _send_telegram(sender, subject, snippet)

    poll_directives()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
