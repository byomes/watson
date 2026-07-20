"""jobs/arc/send_signup_confirmation.py — Email ARC reader their login credentials.

Two templates, two contexts:
  - send_signup_confirmation() — first-time signup only (called from arc_apply()).
  - send_password_reset_email() — any password regeneration that isn't a first
    signup (called from resend_welcome(), which covers both the admin
    "Resend Welcome" button and the self-service forgot-password flow).
"""
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

_FROM          = "FMS Team <watson@faithmakessense.com>"
_SUBJECT       = "Your ARC Team Login — Track Your Commitments"
_RESET_SUBJECT = "Your ARC Login — New Password"
_LOGIN_URL     = "https://williamckyomes.com/arc/login"


def _send_email(to_email: str, subject: str, plain: str) -> None:
    """Shared SMTP-sending boilerplate for both ARC email templates."""
    html = plain.replace("\n", "<br>")
    html = f"<html><body style='font-family:Georgia,serif;font-size:16px;line-height:1.7;color:#1a1a1a;max-width:600px;margin:0 auto;padding:40px;'>{html}</body></html>"

    msg = MIMEMultipart("alternative")
    msg["To"]      = to_email
    msg["From"]    = _FROM
    msg["Subject"] = subject
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_USER, [to_email], msg.as_string())
        log.info("ARC email (%r) sent to %s.", subject, to_email)
    except Exception as exc:
        log.error("Failed to send ARC email (%r) to %s: %s", subject, to_email, exc)
        raise


def send_signup_confirmation(to_email: str, first_name: str, temp_password: str) -> None:
    """First-time ARC signup only — called from arc_apply()."""
    plain = (
        f"Hi {first_name},\n\n"
        "You're officially on the ARC team for The Wrong Jesus. Thank you for committing.\n\n"
        "We've set up a commitment tracker so you can check off each step as you go. "
        "Log in with the credentials below:\n\n"
        f"  Email: {to_email}\n"
        f"  Password: {temp_password}\n\n"
        f"Log in here: {_LOGIN_URL}\n\n"
        "This password was generated just for you — keep it safe. Forgot it later? Click "
        "\"Forgot password?\" on the login page and a new one will be sent to this email. "
        "You'll use this same email and password to enter the Writing Room later if you complete all five commitments.\n\n"
        "Here's what you committed to:\n"
        "  1. Pray for the book's impact\n"
        "  2. Read the book before the launch date\n"
        "  3. Post an honest review on Amazon on launch day\n"
        "  4. Share about the book on at least one social media platform\n"
        "  5. Tell people in your life who you think would connect with this book\n\n"
        "— Watson\n\n"
        "AI-powered digital assistant · Office of Dr. Bill Yomes"
    )
    _send_email(to_email, _SUBJECT, plain)


def send_password_reset_email(to_email: str, first_name: str, new_password: str) -> None:
    """Any password regeneration that isn't a first signup — called from resend_welcome()."""
    plain = (
        f"Hi {first_name},\n\n"
        "A new password has been generated for your ARC team login.\n\n"
        f"  Email: {to_email}\n"
        f"  Password: {new_password}\n\n"
        f"Log in here: {_LOGIN_URL}\n\n"
        "This password was generated just for you — keep it safe. Forgot it later? Click "
        "\"Forgot password?\" on the login page and a new one will be sent to this email.\n\n"
        "— Watson\n\n"
        "AI-powered digital assistant · Office of Dr. Bill Yomes"
    )
    _send_email(to_email, _RESET_SUBJECT, plain)
