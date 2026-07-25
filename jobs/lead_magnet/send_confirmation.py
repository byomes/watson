"""jobs/lead_magnet/send_confirmation.py — Email the companion-guide download
link to a new lead-magnet signup.

Same SMTP boilerplate pattern as jobs/arc/send_signup_confirmation.py (Gmail
SMTP via WATSON_GMAIL_ADDRESS / WATSON_GMAIL_APP_PASSWORD) — every email
sender in this codebase is a small self-contained module rather than a
shared cross-module import, so this follows that same established shape.
"""
import logging
import os

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

_DOWNLOAD_BASE = "https://williamckyomes.com/guides"


def send_guide_confirmation(to_email: str, name: str, title: str, pdf_filename: str) -> None:
    first_name = name.split()[0] if name else "there"
    download_url = f"{_DOWNLOAD_BASE}/{pdf_filename}"

    plain = (
        f"Hi {first_name},\n\n"
        f"Thanks for requesting the free companion guide for {title}.\n\n"
        "You can download it right here:\n\n"
        f"  {download_url}\n\n"
        "We hope it's helpful.\n\n"
        "— Watson\n\n"
        "AI-powered digital assistant · Office of Dr. Bill Yomes"
    )
    html = plain.replace("\n", "<br>")
    html = (
        "<html><body style='font-family:Georgia,serif;font-size:16px;"
        "line-height:1.7;color:#1a1a1a;max-width:600px;margin:0 auto;"
        f"padding:40px;'>{html}</body></html>"
    )

    try:
        result = send_email(
            to_email=to_email, to_name=first_name, subject=f"Your Free Companion Guide — {title}",
            text_body=plain, html_body=html, include_signature=False,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])
        log.info("Lead magnet confirmation email sent to %s (%s).", to_email, title)
    except Exception as exc:
        log.error("Failed to send lead magnet confirmation email to %s: %s", to_email, exc)
        raise
