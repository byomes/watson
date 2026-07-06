"""
Attendance intake — poll Gmail for Donna's paper attendance lists.

Searches for UNREAD emails from DONNA_EMAIL with "Attendance" in the subject,
parses names from the body, looks up or creates member records, writes
attendance rows, marks the email read, and sends a Telegram notification.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/attendance_intake.py

Cron (every 30 minutes):
  */30 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/attendance_intake.py \
    >> /home/billyomes/watson/logs/attendance_intake.log 2>&1
"""

import email
import email.header
import email.utils
import imaplib
import logging
import os
import re
import sqlite3

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from jobs.connect_cards.utils import format_date_for_subject, most_recent_sunday, parse_date_from_subject
from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [attendance_intake] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

IMAP_HOST  = "imap.gmail.com"
IMAP_PORT  = 993
GMAIL_ADDR = os.getenv("WATSON_GMAIL_ADDRESS", "")
GMAIL_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
DONNA_EMAIL = os.getenv("DONNA_EMAIL", "")

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.connect_cards.attendance_intake", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


def _decode_subject(raw_subject: str) -> str:
    parts = email.header.decode_header(raw_subject)
    return "".join(
        p.decode(c or "utf-8") if isinstance(p, bytes) else p
        for p, c in parts
    )


def _get_plain_text(msg) -> str:
    """Return the best plain-text representation of the email body."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = part.get("Content-Disposition", "")
            if ct == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fall back to stripping HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html" and "attachment" not in part.get("Content-Disposition", ""):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    return BeautifulSoup(html, "html.parser").get_text(separator="\n")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return BeautifulSoup(text, "html.parser").get_text(separator="\n")
            return text
    return ""


# Lines that look like email boilerplate or footers — skip them
_SKIP_PATTERNS = re.compile(
    r"(^from:|^sent:|^to:|^subject:|^date:|^--$|@|http[s]?://|unsubscribe|"
    r"confidential|disclaimer|this email|this message)",
    re.IGNORECASE,
)


def _parse_names(body: str) -> list[str]:
    names = []
    for line in body.splitlines():
        s = line.strip()
        if not s or len(s) < 3:
            continue
        if _SKIP_PATTERNS.search(s):
            continue
        # Skip lines that are all caps and long — probably headers
        if s.isupper() and len(s) > 20:
            continue
        names.append(s)
    return names


def _find_or_create_member(conn: sqlite3.Connection, name: str, campus: str, service_date: str) -> int:
    row = conn.execute(
        "SELECT id, campus_preference FROM members WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()

    if row:
        member_id = row["id"]
        if not row["campus_preference"]:
            conn.execute(
                "UPDATE members SET campus_preference = ?, updated_at = datetime('now') WHERE id = ?",
                (campus, member_id),
            )
        return member_id

    conn.execute(
        """
        INSERT INTO members (name, status, campus_preference, first_visit_date)
        VALUES (?, 'member', ?, ?)
        """,
        (name, campus, service_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _attendance_exists(conn: sqlite3.Connection, member_id: int, service_date: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
        (member_id, service_date),
    ).fetchone() is not None


def _process_email(msg, conn: sqlite3.Connection) -> tuple[int, int, list[str]]:
    """Returns (inserted_count, skipped_count, skipped_names)."""
    subject = _decode_subject(msg.get("Subject", ""))
    body = _get_plain_text(msg)

    # Determine service date
    service_date_obj = parse_date_from_subject(subject)
    if service_date_obj is None:
        service_date_obj = most_recent_sunday()
    service_date = service_date_obj.isoformat()

    # Determine campus
    combined = subject + "\n" + (body.splitlines()[0] if body.splitlines() else "")
    if "online" in combined.lower():
        campus = "Online"
    else:
        campus = "Wilmington"

    names = _parse_names(body)
    log.info("Parsed %d names for %s (%s)", len(names), service_date, campus)

    inserted = 0
    skipped_names = []

    for name in names:
        try:
            member_id = _find_or_create_member(conn, name, campus, service_date)
            if not _attendance_exists(conn, member_id, service_date):
                conn.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, NULL)",
                    (member_id, service_date, campus),
                )
                # Auto-reinstate disconnected members
                try:
                    status_row = conn.execute(
                        "SELECT name, member_status FROM members WHERE id = ?", (member_id,)
                    ).fetchone()
                    if status_row and status_row["member_status"] == "disconnected":
                        conn.execute(
                            "UPDATE members SET member_status = 'active', status_reason = NULL, status_note = NULL WHERE id = ?",
                            (member_id,),
                        )
                        member_name = status_row["name"] or name
                        _send_telegram(f"⛪ {member_name} attended today and has been automatically reinstated as active.")
                except Exception as reinstate_exc:
                    log.warning("Auto-reinstatement check failed for %r: %s", name, reinstate_exc)
                inserted += 1
        except Exception as exc:
            log.error("Error processing name %r: %s", name, exc)
            skipped_names.append(name)

    conn.commit()
    return inserted, len(skipped_names), skipped_names, service_date, campus


def run() -> None:
    if not GMAIL_ADDR or not GMAIL_PASS:
        log.error("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
        return
    if not DONNA_EMAIL:
        log.error("DONNA_EMAIL must be set.")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_ADDR, GMAIL_PASS)
    except Exception as exc:
        log.error("IMAP login failed: %s", exc)
        return

    try:
        mail.select("INBOX")
        status, data = mail.search(
            None,
            f'(UNSEEN FROM "{DONNA_EMAIL}" SUBJECT "Attendance")',
        )
        if status != "OK":
            log.error("IMAP search failed: %s", status)
            return

        ids = data[0].split()
        log.info("Found %d matching email(s).", len(ids))
        if not ids:
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        try:
            for eid in ids:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    log.warning("Failed to fetch email id %s", eid)
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                try:
                    inserted, skipped_count, skipped_names, service_date, campus = _process_email(msg, conn)
                except Exception as exc:
                    log.exception("Error processing email id %s: %s", eid, exc)
                    continue

                mail.store(eid, "+FLAGS", "\\Seen")
                log.info("Marked email %s as read.", eid)

                date_label = format_date_for_subject(
                    __import__("datetime").date.fromisoformat(service_date)
                )
                note = f" (skipped: {', '.join(skipped_names)})" if skipped_names else ""
                _send_telegram(
                    f"📋 Attendance intake complete — {inserted} records added for {date_label} ({campus}){note}"
                )
        finally:
            conn.close()

    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    run()
