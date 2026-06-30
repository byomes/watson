"""jobs/arc/send_signup_confirmation.py — Email ARC reader their login credentials."""
import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

_FROM      = "FMS Team <watson@faithmakessense.com>"
_SUBJECT   = "Your ARC Team Login — Track Your Commitments"
_LOGIN_URL = "https://williamckyomes.com/arc/login"


def send_signup_confirmation(to_email: str, first_name: str, temp_password: str) -> None:
    plain = (
        f"Hi {first_name},\n\n"
        "You're officially on the ARC team for The Wrong Jesus. Thank you for committing.\n\n"
        "We've set up a commitment tracker so you can check off each step as you go. "
        "Log in with the credentials below:\n\n"
        f"  Email: {to_email}\n"
        f"  Password: {temp_password}\n\n"
        f"Log in here: {_LOGIN_URL}\n\n"
        "This password was generated just for you — you can't reset it right now, so keep it safe. "
        "You'll use this same email and password to enter the Writing Room later if you complete all six commitments.\n\n"
        "Here's what you committed to:\n"
        "  1. Pray for the book's impact\n"
        "  2. Receive an advance copy of The Wrong Jesus before it's published\n"
        "  3. Read the book before the launch date\n"
        "  4. Post an honest review on Amazon on launch day\n"
        "  5. Share about the book on at least one social media platform\n"
        "  6. Spread the word to anyone who might benefit from reading it\n\n"
        "— Dr. Bill\n\n"
        "Watson · AI-powered digital assistant · Office of Dr. Bill Yomes"
    )

    html = plain.replace("\n", "<br>")
    html = f"<html><body style='font-family:Georgia,serif;font-size:16px;line-height:1.7;color:#1a1a1a;max-width:600px;margin:0 auto;padding:40px;'>{html}</body></html>"

    host     = SMTP_HOST
    port     = SMTP_PORT
    user     = SMTP_USER
    password = SMTP_PASS

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
        log.info("ARC signup confirmation sent to %s.", to_email)
    except Exception as exc:
        log.error("Failed to send ARC signup confirmation to %s: %s", to_email, exc)
        raise
