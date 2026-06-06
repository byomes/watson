"""jobs/email/send.py — Email skill: extract recipient/subject/body from natural language and save a Gmail draft."""
import base64
import json
import logging
import os
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)


def _extract_fields(message: str) -> dict:
    prompt = (
        'Extract the email recipient name, subject line, and body from this message. '
        'Return JSON only: {"to": "", "subject": "", "body": ""}\n\n'
        f'Message: {message}'
    )
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end])
    raise ValueError(f"Could not parse LLM response: {raw[:200]}")


def _lookup_email(name: str) -> str | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT email FROM people WHERE name LIKE ? AND email IS NOT NULL AND email != '' LIMIT 1",
        (f"%{name}%",),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _save_draft(to_email: str, subject: str, body: str) -> None:
    from jobs.email_job.gmail import get_service
    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_service()
    service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()


def run(message: str = None) -> str:
    if message is None:
        return "Email skill ready. Tell me: who to send to, the subject, and the message."

    try:
        fields = _extract_fields(message)
    except Exception as exc:
        log.error("Email field extraction failed: %s", exc)
        return f"Couldn't extract email details: {exc}"

    to_name = fields.get("to", "").strip()
    subject = fields.get("subject", "").strip()
    body = fields.get("body", "").strip()

    if not to_name:
        return "I couldn't determine who to send the email to."

    to_email = _lookup_email(to_name)
    if not to_email:
        return (
            f"I couldn't find {to_name} in the People Registry. "
            "Add them first or provide a full email address."
        )

    try:
        _save_draft(to_email, subject, body)
    except Exception as exc:
        log.error("Gmail draft creation failed: %s", exc)
        return f"Draft failed: {exc}"

    return f"Draft saved to Gmail: To: {to_email}, Subject: {subject}"
