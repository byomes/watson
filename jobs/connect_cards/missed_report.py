"""
Missed attendance report — sends a Monday morning email of who missed that Sunday.

Queries congregation.db for members with no attendance record for the most
recent Sunday, then emails Donna and Dr. Bill with the list grouped by campus.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/missed_report.py

Cron (Monday 6:00am):
  0 6 * * 1 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/missed_report.py \
    >> /home/billyomes/watson/logs/missed_report.log 2>&1
"""

import logging
import os
import smtplib
import sqlite3
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

from jobs.connect_cards.utils import format_date_for_subject, most_recent_sunday

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [missed_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587
SMTP_USER  = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS  = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

REPORT_EMAIL = os.getenv("REPORT_EMAIL", "bill.yomes@gmail.com")
DONNA_EMAIL  = os.getenv("DONNA_EMAIL", "")

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


def _send_email(subject: str, body: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")

    recipients = [r for r in [REPORT_EMAIL, DONNA_EMAIL] if r]
    if not recipients:
        raise RuntimeError("No recipients configured — set REPORT_EMAIL and DONNA_EMAIL.")

    msg = MIMEText(body, "plain")
    msg["Subject"]  = subject
    msg["From"]     = f"Watson <{SMTP_USER}>"
    msg["To"]       = ", ".join(recipients)
    msg["Reply-To"] = SMTP_USER

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, recipients, msg.as_string())

    log.info("Sent %r to %s", subject, recipients)


def run() -> None:
    sunday = most_recent_sunday()
    service_date = sunday.isoformat()
    date_label = format_date_for_subject(sunday)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        missed = conn.execute(
            """
            SELECT m.id, m.name, m.campus_preference
            FROM members m
            WHERE m.active = 1
              AND (m.member_status IS NULL OR m.member_status = 'active')
              AND NOT EXISTS (
                SELECT 1 FROM attendance a
                WHERE a.member_id = m.id
                  AND a.service_date = ?
            )
            ORDER BY m.campus_preference, m.name
            """,
            (service_date,),
        ).fetchall()
    finally:
        conn.close()

    wilmington = [
        r["name"]
        for r in missed
        if (r["campus_preference"] or "").strip().lower() in ("wilmington", "")
    ]
    online = [
        r["name"]
        for r in missed
        if (r["campus_preference"] or "").strip().lower() == "online"
    ]
    hybrid = [
        r["name"]
        for r in missed
        if (r["campus_preference"] or "").strip().lower() == "hybrid"
    ]

    subject = f"Missed — {date_label}"

    if not wilmington and not online and not hybrid:
        body = f"All members accounted for on {date_label}. No missed report."
        _send_email(subject, body)
        _send_telegram(f"✅ Full attendance on {date_label} — no missed report needed.")
        log.info("Full attendance on %s.", date_label)
        return

    wilmington_section = "\n".join(wilmington) if wilmington else "(none)"

    body_parts = [
        "Watson — Missed Attendance Report",
        f"Sunday, {date_label}",
        "",
        "WILMINGTON CAMPUS",
        wilmington_section,
    ]

    if online:
        body_parts += [
            "",
            "ONLINE CAMPUS",
            "\n".join(online),
        ]

    if hybrid:
        body_parts += [
            "",
            "HYBRID CAMPUS",
            "\n".join(hybrid),
        ]

    body_parts += [
        "",
        "---",
        "Reply to this email with the names of anyone who was actually present "
        "and Watson will update the records.",
        "Watson / AI-powered digital assistant / Office of Dr. Bill Yomes",
    ]

    body = "\n".join(body_parts)

    _send_email(subject, body)
    _send_telegram(
        f"📊 Missed report sent for {date_label} — "
        f"{len(wilmington)} Wilmington, {len(online)} Online, {len(hybrid)} Hybrid"
    )


if __name__ == "__main__":
    run()
