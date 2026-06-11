"""
handler.py — DB persistence, Telegram notification, and SMTP reply sending
for the email_reply job.

Called by reader.py (cron) to store pending records and notify Bill via Telegram.
Called by bot.py handlers to resolve "send", "change:", and "cancel" replies.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

load_dotenv(Path.home() / "watson" / ".env")

log = logging.getLogger(__name__)


# ── DB ────────────────────────────────────────────────────────────────────────

def _get_conn():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_table() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_reply_pending (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id    TEXT NOT NULL,
                thread_id     TEXT,
                sender_name   TEXT,
                sender_email  TEXT NOT NULL,
                subject       TEXT,
                original_body TEXT,
                draft_reply   TEXT,
                status        TEXT NOT NULL DEFAULT 'pending',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at   TEXT
            )
        """)


def save_pending(email: dict, draft: str) -> int:
    init_table()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO email_reply_pending
               (message_id, thread_id, sender_name, sender_email, subject, original_body, draft_reply)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                email["message_id"],
                email.get("thread_id"),
                email.get("sender_name", ""),
                email["sender_email"],
                email.get("subject", ""),
                email.get("body", ""),
                draft,
            ),
        )
        return cur.lastrowid


def get_latest_pending() -> dict | None:
    init_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM email_reply_pending WHERE status='pending' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _mark_resolved(record_id: int, status: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE email_reply_pending SET status=?, resolved_at=datetime('now') WHERE id=?",
            (status, record_id),
        )


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram_notification(email: dict, draft: str) -> None:
    """Send the draft approval message to Bill via the Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set; skipping notification")
        return

    text = (
        f"📧 New email from {email.get('sender_name', '')} <{email['sender_email']}>\n"
        f"Subject: {email.get('subject', '(no subject)')}\n\n"
        f"---DRAFT REPLY---\n"
        f"{draft}\n"
        f"---\n\n"
        f"Reply with:\n"
        f"• go — send this reply\n"
        f"• change: [your text] — send your version instead\n"
        f"• cancel — discard, do nothing"
    )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)


def _send_telegram_text(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


# ── SMTP ──────────────────────────────────────────────────────────────────────

def _get_in_reply_to(gmail_message_id: str) -> str | None:
    """Fetch the original email's Message-ID header for threading."""
    try:
        from jobs.email_job.gmail import get_service
        service = get_service()
        msg = service.users().messages().get(
            userId="me", id=gmail_message_id, format="metadata",
            metadataHeaders=["Message-ID"],
        ).execute()
        for h in msg.get("payload", {}).get("headers", []):
            if h["name"].lower() == "message-id":
                return h["value"]
    except Exception as exc:
        log.warning("Could not fetch Message-ID header for threading: %s", exc)
    return None


def _send_smtp_reply(to: str, subject: str, body: str, in_reply_to: str | None = None) -> None:
    smtp_user = os.getenv("WATSON_GMAIL_ADDRESS", "")
    smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
    from_addr = os.getenv("WATSON_FROM_ADDRESS") or smtp_user

    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    msg = MIMEMultipart("alternative")
    msg["To"]      = to
    msg["From"]    = f"Watson <{from_addr}>"
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = in_reply_to

    html_body = body.replace("\n", "<br>")
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body>{html_body}</body></html>", "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(from_addr, [to], msg.as_string())


# ── Resolution functions (called from bot.py) ─────────────────────────────────

def resolve_send() -> dict:
    """Send the drafted reply. Returns {"ok": bool, "msg": str, "sender": str}."""
    record = get_latest_pending()
    if not record:
        return {"ok": False, "msg": "No pending email reply found."}

    in_reply_to = _get_in_reply_to(record["message_id"])
    try:
        _send_smtp_reply(
            to=record["sender_email"],
            subject=record["subject"] or "",
            body=record["draft_reply"] or "",
            in_reply_to=in_reply_to,
        )
    except Exception as exc:
        log.error("SMTP send failed: %s", exc)
        return {"ok": False, "msg": f"Failed to send: {exc}"}

    _mark_resolved(record["id"], "sent")
    sender = record["sender_name"] or record["sender_email"]
    return {"ok": True, "msg": f"✅ Reply sent to {sender}", "sender": sender}


def resolve_change(new_text: str) -> dict:
    """Send Bill's custom reply. Returns {"ok": bool, "msg": str, "sender": str}."""
    record = get_latest_pending()
    if not record:
        return {"ok": False, "msg": "No pending email reply found."}

    in_reply_to = _get_in_reply_to(record["message_id"])
    try:
        _send_smtp_reply(
            to=record["sender_email"],
            subject=record["subject"] or "",
            body=new_text,
            in_reply_to=in_reply_to,
        )
    except Exception as exc:
        log.error("SMTP send failed: %s", exc)
        return {"ok": False, "msg": f"Failed to send: {exc}"}

    _mark_resolved(record["id"], "changed")
    sender = record["sender_name"] or record["sender_email"]
    return {"ok": True, "msg": f"✅ Your reply sent to {sender}", "sender": sender}


def resolve_cancel() -> dict:
    """Discard the pending reply. Returns {"ok": bool, "msg": str}."""
    record = get_latest_pending()
    if not record:
        return {"ok": False, "msg": None}  # No pending email; let caller fall through

    _mark_resolved(record["id"], "cancelled")
    sender = record["sender_name"] or record["sender_email"]
    return {"ok": True, "msg": f"❌ Reply cancelled"}
