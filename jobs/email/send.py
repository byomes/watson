"""jobs/email/send.py — Email skill: extract recipient/subject/body from natural language and send via SMTP."""
import json
import logging
import os
import smtplib
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


def _send_smtp(to_email: str, subject: str, body: str, to_name: str = "") -> None:
    smtp_host = os.getenv("WATSON_SMTP_HOST")
    smtp_user = os.getenv("WATSON_SMTP_USER")
    smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD")

    plain = f"{body}\n\n---\nWatson\nAI-powered digital assistant\nOffice of Dr. Bill Yomes\nwilliamckyomes.com/start"

    first_name = to_name.split()[0] if to_name else to_email
    html = (
        f"<p>{first_name},</p>"
        f"<p>Dr. Bill Yomes asked me to reach out to you.</p>"
        f"<p>{body}</p>"
        f"<hr>"
        f'<p style="color:#666;font-size:12px;">'
        f"Watson<br>"
        f"AI-powered digital assistant<br>"
        f"Office of Dr. Bill Yomes<br>"
        f'<a href="https://williamckyomes.com/start">williamckyomes.com/start</a>'
        f"</p>"
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = "Watson <watson.wcky@gmail.com>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(smtp_user, [to_email], msg.as_string())


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

    return {
        "confirm": True,
        "to_name": to_name,
        "to_email": to_email,
        "subject": subject,
        "body": body,
    }
