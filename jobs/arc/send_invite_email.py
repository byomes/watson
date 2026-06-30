"""jobs/arc/send_invite_email.py — Send the Writing Room invite email to an ARC reader."""
import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

log = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "arc_invite_email.html"
_LOGIN_URL = "https://williamckyomes.com/room"
_FROM      = "FMS Team <watson@faithmakessense.com>"
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

    host     = os.getenv("WATSON_SMTP_HOST", "smtp.gmail.com")
    port     = int(os.getenv("WATSON_SMTP_PORT", "587"))
    user     = os.getenv("WATSON_SMTP_USER", "")
    password = os.getenv("WATSON_SMTP_PASS", "")

    msg = MIMEMultipart("alternative")
    msg["To"]      = to_email
    msg["From"]    = _FROM
    msg["Subject"] = _SUBJECT
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, [to_email], msg.as_string())
        log.info("ARC invite email sent to %s.", to_email)
    except Exception as exc:
        log.error("Failed to send ARC invite email to %s: %s", to_email, exc)
        raise
