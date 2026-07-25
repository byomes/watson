"""jobs/writing_room/send_arc_welcome_email.py — Fire the Writing Room welcome email for ARC readers."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.email_job.brevo_send import send_email

log = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "arc_welcome_email.html"
_FROM_EMAIL = "watson@faithmakessense.com"
_FROM_NAME  = "FMS Team"
_SUBJECT  = "Welcome to the Writing Room — Here's How to Participate."


def send_arc_welcome_email(to_email: str, first_name: str) -> None:
    try:
        html = _TEMPLATE.read_text()
    except Exception as exc:
        log.error("Could not read arc_welcome_email.html: %s", exc)
        return

    html = html.replace("{{ first_name }}", first_name)

    plain = (
        f"Hi {first_name},\n\n"
        "Welcome to the Writing Room. You earned this.\n\n"
        "Here's what you'll find inside:\n\n"
        "Board — community discussion and theology around What Child Is This.\n\n"
        "Beta — draft chapters as they're written. Your feedback shapes the manuscript.\n\n"
        "Prayer — collective prayer for the work.\n\n"
        "Write — direct messages to Dr. Bill.\n\n"
        "Calls — occasional live video calls with the Writing Room community.\n\n"
        "You didn't just read a book. You committed to the work behind it. That's why you're here.\n\n"
        "— Watson\n\n"
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
        log.info("ARC welcome email sent to %s.", to_email)
    except Exception as exc:
        log.error("Failed to send ARC welcome email to %s: %s", to_email, exc)
        raise
