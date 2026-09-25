"""prayer_notify.py -- deacon prayer-request notifications with accountability buttons.

Sends a formatted prayer-request alert to a deacon's Telegram chat with two
inline buttons: "Logged / Done" (marks contact made) and "Remind me later"
(offers a 12/18/24/36-hour snooze). Every send is tracked in
prayer_contact_log so bot.py's callback handlers (pr_done/pr_later/pr_back/
pr_remind) can verify the button-presser is the same chat the notification
went to, and so prayer_reminder_fire.py's cron can re-fire snoozed ones.
Not gated behind bot.py's _is_authorized (Bill-only) -- deacons other than
Bill are the intended audience once onboarded.
"""
import os
import sqlite3

import requests

from config.settings import TELEGRAM_BOT_TOKEN

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

REMIND_HOURS = (12, 18, 24, 36)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prayer_contact_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            prayer_request_id   INTEGER NOT NULL REFERENCES prayer_requests(id),
            deacon_name         TEXT NOT NULL,
            deacon_person_id    INTEGER NOT NULL,
            telegram_chat_id    TEXT NOT NULL,
            telegram_message_id INTEGER,
            status              TEXT NOT NULL DEFAULT 'pending',
            sent_at             TEXT NOT NULL DEFAULT (datetime('now')),
            contacted_at        TEXT,
            remind_at           TEXT,
            snooze_hours        INTEGER,
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


def _pronoun(gender: str | None) -> str:
    g = (gender or "").strip().lower()
    if g == "male":
        return "his"
    if g == "female":
        return "her"
    return "their"


def format_message(conn, prayer_request_id: int, deacon_name: str) -> str | None:
    """Returns None if the request doesn't exist, has no linked member, or
    is leadership_only (those never go to deacons -- see
    deacon_visible_prayer_requests, which applies the same filter)."""
    row = conn.execute("""
        SELECT pr.request_text, pr.leadership_only, m.name, m.email, m.phone, m.gender
        FROM prayer_requests pr
        JOIN members m ON m.id = pr.member_id
        WHERE pr.id = ?
    """, (prayer_request_id,)).fetchone()
    if not row or row["leadership_only"]:
        return None

    possessive = _pronoun(row["gender"])
    contact_bits = [b for b in (row["email"], row["phone"]) if b]
    contact = ", ".join(contact_bits) if contact_bits else "no contact info on file"

    return (
        f'{deacon_name}, {row["name"]} asked for prayer this week: '
        f'"{row["request_text"]}"\n\n'
        f"Here is {possessive} info: {contact}."
    )


def _done_later_keyboard(log_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Logged / Done", "callback_data": f"pr_done:{log_id}"},
        {"text": "⏰ Remind me later", "callback_data": f"pr_later:{log_id}"},
    ]]}


def _send_telegram(chat_id, text: str, keyboard: dict) -> int | None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["result"]["message_id"]


def send_notification(prayer_request_id: int, deacon_person_id: int, deacon_name: str, deacon_chat_id: str) -> int | None:
    """Send a fresh prayer-contact notification. Returns the new
    prayer_contact_log id, or None if the request isn't eligible to send."""
    with _conn() as conn:
        ensure_schema(conn)
        text = format_message(conn, prayer_request_id, deacon_name)
        if text is None:
            return None

        cur = conn.execute(
            "INSERT INTO prayer_contact_log "
            "(prayer_request_id, deacon_name, deacon_person_id, telegram_chat_id, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (prayer_request_id, deacon_name, deacon_person_id, str(deacon_chat_id)),
        )
        log_id = cur.lastrowid

        message_id = _send_telegram(deacon_chat_id, text, _done_later_keyboard(log_id))
        conn.execute(
            "UPDATE prayer_contact_log SET telegram_message_id = ? WHERE id = ?",
            (message_id, log_id),
        )
    return log_id


def resend_notification(log_id: int) -> bool:
    """Re-send a snoozed notification's message with fresh buttons (used by
    prayer_reminder_fire.py once remind_at is due)."""
    with _conn() as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM prayer_contact_log WHERE id = ?", (log_id,)).fetchone()
        if not row:
            return False
        text = format_message(conn, row["prayer_request_id"], row["deacon_name"])
        if text is None:
            return False
        message_id = _send_telegram(row["telegram_chat_id"], text, _done_later_keyboard(log_id))
        conn.execute(
            "UPDATE prayer_contact_log SET telegram_message_id = ?, status = 'pending', "
            "sent_at = datetime('now'), remind_at = NULL, snooze_hours = NULL WHERE id = ?",
            (message_id, log_id),
        )
    return True
