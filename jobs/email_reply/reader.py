"""
reader.py — Email reply job entry point (cron target).

Polls INBOX via IMAP for UNSEEN emails, drafts a reply via qwen2.5:7b,
sends the draft to Bill via Telegram for approval, then marks the email SEEN.

Cron (every 15 min):
    */15 * * * * set -a && . /home/billyomes/watson/.env && set +a && \
      PYTHONPATH=/home/billyomes/watson \
      /home/billyomes/watson/venv/bin/python \
      /home/billyomes/watson/jobs/email_reply/reader.py \
      >> /home/billyomes/watson/logs/email_reply.log 2>&1
"""

import email
import imaplib
import logging
import os
import re
import sys
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.email_reply.drafter import draft_reply
from jobs.email_reply.handler import init_table, save_pending, send_telegram_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _connect() -> imaplib.IMAP4_SSL:
    address = os.environ["WATSON_GMAIL_ADDRESS"]
    password = os.environ["WATSON_GMAIL_APP_PASSWORD"]
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(address, password)
    mail.select("INBOX")
    return mail


def _decode_header_value(value: str) -> str:
    return str(make_header(decode_header(value)))


def _parse_sender(from_header: str) -> tuple[str, str]:
    m = re.match(r'^(.+?)\s*<([^>]+)>$', from_header.strip())
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    addr = from_header.strip()
    return addr, addr


def _extract_body(msg: email.message.Message) -> str:
    plain = None
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
            elif ct == "text/html" and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
    else:
        payload = msg.get_payload(decode=True)
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text

    if plain:
        return plain
    if html:
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s{2,}", " ", text).strip()
    return ""


def _fetch_unseen(mail: imaplib.IMAP4_SSL) -> list[dict]:
    status, data = mail.search(None, "UNSEEN")
    if status != "OK":
        return []

    uids = data[0].split()
    results = []

    for uid in uids:
        status, msg_data = mail.fetch(uid, "(RFC822)")
        if status != "OK" or not msg_data or msg_data[0] is None:
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        message_id = msg.get("Message-ID", "").strip()
        from_header = _decode_header_value(msg.get("From", ""))
        subject = _decode_header_value(msg.get("Subject", "(no subject)"))
        sender_name, sender_email = _parse_sender(from_header)
        body = _extract_body(msg)

        results.append({
            "uid":          uid,
            "message_id":   message_id,
            "thread_id":    None,
            "sender_name":  sender_name,
            "sender_email": sender_email,
            "subject":      subject,
            "body":         body,
        })

    return results


def _mark_seen(mail: imaplib.IMAP4_SSL, uid: bytes) -> None:
    mail.store(uid, "+FLAGS", "\\Seen")


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    init_table()

    try:
        mail = _connect()
    except Exception as exc:
        log.error("IMAP connection failed: %s", exc)
        return

    try:
        emails = _fetch_unseen(mail)
    except Exception as exc:
        log.error("Failed to fetch unseen emails: %s", exc)
        mail.logout()
        return

    if not emails:
        log.info("No unseen emails found.")
        mail.logout()
        return

    log.info("Found %d unseen email(s).", len(emails))

    for em in emails:
        log.info("Processing: %s from %s", em["subject"], em["sender_email"])

        if "snappages.com" in em["sender_email"].lower():
            log.info("Skipping connect card from %s (snappages.com)", em["sender_email"])
            _mark_seen(mail, em["uid"])
            continue

        draft = draft_reply(em)
        if not draft:
            log.warning("Empty draft for %s; skipping.", em["message_id"])
            _mark_seen(mail, em["uid"])
            continue

        save_pending(em, draft)
        send_telegram_notification(em, draft)
        _mark_seen(mail, em["uid"])

        log.info("Processed and marked SEEN: %s", em["message_id"])

    mail.logout()


if __name__ == "__main__":
    run()
