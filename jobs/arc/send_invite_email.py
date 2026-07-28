"""jobs/arc/send_invite_email.py — Send the Writing Room invite email to an ARC reader."""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

log = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "arc_invite_email.html"
_LOGIN_URL = "https://williamckyomes.com/room"
# STOPGAP 2026-07-27: forced to williamckyomes.com sender until
# faithmakessense.com domain auth is fixed in Brevo — revert once confirmed
_FROM_EMAIL = "watson@williamckyomes.com"
_FROM_NAME  = "Watson"
_SUBJECT   = "You've Earned Access to the Writing Room."


def send_arc_invite_email(to_email: str, first_name: str) -> None:
    try:
        html = _TEMPLATE.read_text()
    except Exception as exc:
        log.error("Could not read arc_invite_email.html: %s", exc)
        return

    html = html.replace("{{ first_name }}", first_name).replace("{{ login_url }}", _LOGIN_URL)

    plain = (
        f"Hi {first_name},\n\n"
        "You did what you said you would do — and you've earned access to the Writing Room.\n\n"
        "The Writing Room is an inner circle of trusted readers who will watch What Child Is This "
        "develop from the ground up. Log in with your ARC email and password:\n\n"
        f"{_LOGIN_URL}\n\n"
        "Welcome to the inner circle.\n\n"
        "— Dr. Bill\n\n"
        "Watson · AI-powered digital assistant · Office of Dr. Bill Yomes"
    )

    try:
        result = send_email(
            to_email=to_email, to_name=first_name, subject=_SUBJECT,
            text_body=plain, html_body=html, include_signature=False,
            from_email=_FROM_EMAIL, from_name=_FROM_NAME,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])
        log.info("ARC invite email sent to %s.", to_email)
    except Exception as exc:
        log.error("Failed to send ARC invite email to %s: %s", to_email, exc)
        raise
