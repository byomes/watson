"""jobs/email_job/brevo_send.py — Shared Brevo transactional email sender.

Groundwork only: no existing Gmail SMTP job calls this yet. Add callers in a
later pass once williamckyomes.com DNS/SPF/DKIM is confirmed in Brevo.
"""
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_FROM_EMAIL = "watson@send.williamckyomes.com"
DEFAULT_FROM_NAME = "Watson"
_TIMEOUT = 15
_MAX_RETRIES = 1

SIGNATURE_TEXT = "\n\n--\nWatson\nDigital Assistant to Dr. Bill Yomes\nwilliamckyomes.com/start"
SIGNATURE_HTML = '<p style="margin-top:24px;">--<br>Watson<br>Digital Assistant to Dr. Bill Yomes<br><a href="https://williamckyomes.com/start">williamckyomes.com/start</a></p>'


def send_email(to_email, to_name, subject, text_body, html_body=None, tags=None,
                from_email=None, from_name=None, include_signature=True):
    """Send one transactional email via Brevo's API.

    Returns {"success": bool, "message_id": str|None, "error": str|None}.
    Never raises on Brevo API errors or network failures — callers decide how
    to handle a failed send.
    """
    api_key = os.getenv("BREVO_API_KEY", "")
    if not api_key:
        return {"success": False, "message_id": None, "error": "BREVO_API_KEY not set in .env"}

    if include_signature:
        text_body = text_body + SIGNATURE_TEXT
        if html_body is not None:
            html_body = html_body + SIGNATURE_HTML

    payload = {
        "sender": {
            "email": from_email or DEFAULT_FROM_EMAIL,
            "name": from_name or DEFAULT_FROM_NAME,
        },
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "textContent": text_body,
    }
    if html_body:
        payload["htmlContent"] = html_body
    if tags:
        payload["tags"] = tags

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }

    attempts = _MAX_RETRIES + 1
    last_error = None
    for attempt in range(attempts):
        try:
            resp = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        except requests.exceptions.Timeout:
            last_error = "Brevo API request timed out"
            if attempt < attempts - 1:
                continue
            return {"success": False, "message_id": None, "error": last_error}
        except requests.exceptions.RequestException as exc:
            return {"success": False, "message_id": None, "error": f"Brevo API request failed: {exc}"}

        if resp.status_code in (200, 201):
            data = resp.json()
            return {"success": True, "message_id": data.get("messageId"), "error": None}

        if resp.status_code >= 500 and attempt < attempts - 1:
            last_error = f"Brevo API {resp.status_code}: {resp.text}"
            time.sleep(1)
            continue

        return {"success": False, "message_id": None, "error": f"Brevo API {resp.status_code}: {resp.text}"}

    return {"success": False, "message_id": None, "error": last_error}
