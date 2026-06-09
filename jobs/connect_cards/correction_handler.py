"""
Correction handler — process reply emails that correct the missed attendance report.

Polls Gmail for UNREAD replies to "Missed" emails from authorized senders
(DONNA_EMAIL or BILL_CORRECTION_EMAIL), parses names, and inserts attendance
records to fix the data.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/correction_handler.py

Cron (every 30 minutes):
  */30 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/correction_handler.py \
    >> /home/billyomes/watson/logs/correction_handler.log 2>&1
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

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [correction_handler] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

IMAP_HOST  = "imap.gmail.com"
IMAP_PORT  = 993
GMAIL_ADDR = os.getenv("WATSON_GMAIL_ADDRESS", "")
GMAIL_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

DONNA_EMAIL          = os.getenv("DONNA_EMAIL", "").lower()
BILL_CORRECTION_EMAIL = os.getenv("BILL_CORRECTION_EMAIL", "").lower()

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
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
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = part.get("Content-Disposition", "")
            if ct == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html" and "attachment" not in part.get("Content-Disposition", ""):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return BeautifulSoup(
                        payload.decode(charset, errors="replace"), "html.parser"
                    ).get_text(separator="\n")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return BeautifulSoup(text, "html.parser").get_text(separator="\n")
            return text
    return ""


def _strip_quoted_text(body: str) -> str:
    lines = body.splitlines()
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped.lower().startswith("on ") and "wrote:" in stripped.lower():
            break
        clean.append(stripped)
    return "\n".join(clean)


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
        if s.isupper() and len(s) > 20:
            continue
        names.append(s)
    return names


def _find_or_create_member(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM members WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row:
        return row["id"]
    conn.execute(
        "INSERT INTO members (name, status, campus_preference) VALUES (?, 'member', 'Wilmington')",
        (name,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _attendance_exists(conn: sqlite3.Connection, member_id: int, service_date: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
        (member_id, service_date),
    ).fetchone() is not None


def _sender_name(addr: str) -> str:
    if addr == DONNA_EMAIL:
        return "Donna"
    if addr == BILL_CORRECTION_EMAIL:
        return "Dr. Bill"
    return addr


def _process_email(msg, conn: sqlite3.Connection) -> tuple[int, str, str]:
    """Returns (inserted_count, service_date, sender_name)."""
    from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    subject   = _decode_subject(msg.get("Subject", ""))
    body      = _get_plain_text(msg)
    body      = _strip_quoted_text(body)

    service_date_obj = parse_date_from_subject(subject)
    if service_date_obj is None:
        service_date_obj = most_recent_sunday()
    service_date = service_date_obj.isoformat()

    names = _parse_names(body)
    log.info("Parsed %d correction name(s) for %s from %s", len(names), service_date, from_addr)

    inserted = 0
    for name in names:
        try:
            member_id = _find_or_create_member(conn, name)
            if not _attendance_exists(conn, member_id, service_date):
                conn.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, 'Wilmington', NULL)",
                    (member_id, service_date),
                )
                inserted += 1
        except Exception as exc:
            log.error("Error processing correction name %r: %s", name, exc)

    conn.commit()
    return inserted, service_date, _sender_name(from_addr)


def run() -> None:
    if not GMAIL_ADDR or not GMAIL_PASS:
        log.error("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
        return

    authorized = {a for a in [DONNA_EMAIL, BILL_CORRECTION_EMAIL] if a}
    if not authorized:
        log.error("DONNA_EMAIL and/or BILL_CORRECTION_EMAIL must be set.")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_ADDR, GMAIL_PASS)
    except Exception as exc:
        log.error("IMAP login failed: %s", exc)
        return

    try:
        mail.select("INBOX")

        # Search for replies from either authorized sender
        matched_ids = set()
        for sender in authorized:
            status, data = mail.search(
                None,
                f'(UNSEEN FROM "{sender}" SUBJECT "Re: Missed")',
            )
            if status == "OK" and data[0]:
                matched_ids.update(data[0].split())

        if not matched_ids:
            log.info("No correction emails found.")
            return

        log.info("Found %d correction email(s).", len(matched_ids))
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        try:
            for eid in matched_ids:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    log.warning("Failed to fetch email id %s", eid)
                    continue

                msg       = email.message_from_bytes(msg_data[0][1])
                from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()

                if from_addr not in authorized:
                    log.info("Ignored unauthorized sender: %s", from_addr)
                    continue

                try:
                    inserted, service_date, sender_name = _process_email(msg, conn)
                except Exception as exc:
                    log.exception("Error processing email id %s: %s", eid, exc)
                    continue

                mail.store(eid, "+FLAGS", "\\Seen")

                date_label = format_date_for_subject(
                    __import__("datetime").date.fromisoformat(service_date)
                )
                _send_telegram(
                    f"✏️ Corrections applied — {inserted} attendance records updated "
                    f"for {date_label} (from {sender_name})"
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
