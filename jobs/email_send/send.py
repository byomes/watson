"""jobs/email/send.py — Email skill: extract recipient/subject/body from natural language and send via Brevo."""
import json
import logging
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.claude_tier import call_claude
from jobs.email_job.brevo_send import send_email
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

log = logging.getLogger(__name__)


def _extract_fields(message: str) -> dict:
    prompt = (
        'Extract the email recipient name, subject line, and body from this message. '
        'Return JSON only: {"to": "", "subject": "", "body": ""}\n\n'
        f'Message: {message}'
    )
    raw = call_claude(system="", user=prompt, job_name="email_send.send")
    if not raw:
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
    plain = body

    first_name = to_name.split()[0] if to_name else to_email
    html = (
        f"<p>{first_name},</p>"
        f"<p>Dr. Bill Yomes asked me to reach out to you.</p>"
        f"<p>{body}</p>"
    )

    result = send_email(
        to_email=to_email, to_name=to_name, subject=subject,
        text_body=plain, html_body=html,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send failed: {result['error']}")


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
