"""
reader.py — Email reply job entry point (cron target).

Polls watson.wcky@gmail.com for unread emails not yet labeled
"watson-processed", drafts a reply via qwen2.5:7b, sends the draft to
Bill via Telegram for approval, then labels the email processed.

Cron (every 15 min):
    */15 * * * * set -a && . /home/billyomes/watson/.env && set +a && \
      PYTHONPATH=/home/billyomes/watson \
      /home/billyomes/watson/venv/bin/python \
      /home/billyomes/watson/jobs/email_reply/reader.py \
      >> /home/billyomes/watson/logs/email_reply.log 2>&1
"""

import base64
import logging
import re
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.email_job.gmail import get_service
from jobs.email_reply.drafter import draft_reply
from jobs.email_reply.handler import init_table, save_pending, send_telegram_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PROCESSED_LABEL = "watson-processed"


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def _get_or_create_label(service) -> str:
    """Return the label ID for PROCESSED_LABEL, creating it if needed."""
    result = service.users().labels().list(userId="me").execute()
    for label in result.get("labels", []):
        if label["name"].lower() == PROCESSED_LABEL.lower():
            return label["id"]
    # Create it
    created = service.users().labels().create(
        userId="me",
        body={
            "name": PROCESSED_LABEL,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    log.info("Created Gmail label: %s (%s)", PROCESSED_LABEL, created["id"])
    return created["id"]


def _apply_label(service, gmail_id: str, label_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=gmail_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body, falling back to HTML → stripped."""
    if "parts" in payload:
        # Prefer plain text
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part["body"].get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        # Fall back: recurse into nested multipart
        for part in payload["parts"]:
            result = _extract_body(part)
            if result:
                return result
        return ""

    data = payload.get("body", {}).get("data", "")
    if not data:
        return ""
    text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    if payload.get("mimeType") == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _parse_sender(from_header: str) -> tuple[str, str]:
    """Return (display_name, email_address) from a From: header value."""
    m = re.match(r'^(.+?)\s*<([^>]+)>$', from_header.strip())
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    # bare address
    addr = from_header.strip()
    return addr, addr


def _fetch_unprocessed(service) -> list[dict]:
    """Return list of email dicts for unread messages not labeled watson-processed."""
    resp = service.users().messages().list(
        userId="me",
        q=f"is:unread -label:{PROCESSED_LABEL}",
        maxResults=10,
    ).execute()

    messages = resp.get("messages", [])
    results = []

    for stub in messages:
        gmail_id = stub["id"]
        msg = service.users().messages().get(
            userId="me", id=gmail_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        from_header = headers.get("From", "")
        sender_name, sender_email = _parse_sender(from_header)
        subject = headers.get("Subject", "(no subject)")
        body = _extract_body(msg["payload"])

        results.append({
            "gmail_id":    gmail_id,           # Gmail API ID — used for labeling only
            "message_id":  gmail_id,           # stored in DB per schema
            "thread_id":   msg.get("threadId"),
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject":     subject,
            "body":        body,
        })

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    init_table()

    try:
        service = get_service()
    except Exception as exc:
        log.error("Gmail auth failed: %s", exc)
        return

    label_id = _get_or_create_label(service)
    emails = _fetch_unprocessed(service)

    if not emails:
        log.info("No unprocessed emails found.")
        return

    log.info("Found %d unprocessed email(s).", len(emails))

    for email in emails:
        log.info("Processing: %s from %s", email["subject"], email["sender_email"])

        draft = draft_reply(email)
        if not draft:
            log.warning("Empty draft for message %s; skipping.", email["gmail_id"])
            _apply_label(service, email["gmail_id"], label_id)
            continue

        save_pending(email, draft)
        send_telegram_notification(email, draft)
        _apply_label(service, email["gmail_id"], label_id)

        log.info("Processed and labeled: %s", email["gmail_id"])


if __name__ == "__main__":
    run()
