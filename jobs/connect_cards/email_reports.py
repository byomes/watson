"""
Connect card email reports — send weekly summaries to Bill, Donna, and Kaci.

Configuration (env vars):
  BILL_EMAIL                Recipient for next steps + comments report
  DONNA_EMAIL               Recipient for attendance report
  KACI_EMAIL                Recipient for prayer requests report
  WATSON_GMAIL_ADDRESS      SMTP login / from address (smtp.startlogic.com)
  WATSON_GMAIL_APP_PASSWORD SMTP password / app password

Cron (on watson):
  0 5 * * 1  python3 -m jobs.connect_cards.email_reports --bill  (Monday)
  0 5 * * 1  python3 -m jobs.connect_cards.email_reports --kaci  (Monday)
  0 5 * * 2  python3 -m jobs.connect_cards.email_reports --donna (Tuesday)

Usage:
  python3 -m jobs.connect_cards.email_reports --bill
  python3 -m jobs.connect_cards.email_reports --donna
  python3 -m jobs.connect_cards.email_reports --kaci
  python3 -m jobs.connect_cards.email_reports --all
  python3 -m jobs.connect_cards.email_reports --bill --date 2026-06-01
"""

import argparse
import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

from jobs.connect_cards.reports import bill_report, donna_report, kaci_report

load_dotenv(os.path.expanduser("~/watson/.env"))

SMTP_HOST = "smtp.startlogic.com"
SMTP_PORT = 587
FROM_ADDR = os.getenv("WATSON_GMAIL_ADDRESS", "")
FROM_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

BILL_EMAIL  = os.getenv("BILL_EMAIL", "")
DONNA_EMAIL = os.getenv("DONNA_EMAIL", "")
KACI_EMAIL  = os.getenv("KACI_EMAIL", "")


def most_recent_sunday() -> str:
    today = date.today()
    days_ago = (today.weekday() + 1) % 7
    return (today - timedelta(days=days_ago)).isoformat()


def _send(to: str, subject: str, html: str) -> None:
    if not FROM_ADDR or not FROM_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    if not to:
        raise RuntimeError(f"Recipient address is empty — check env vars.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_ADDR
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(FROM_ADDR, FROM_PASS)
        smtp.sendmail(FROM_ADDR, [to], msg.as_string())

    print(f"Sent: {subject!r} → {to}")


def send_bill_report(service_date: str | None = None) -> None:
    d = service_date or most_recent_sunday()
    subject, html = bill_report(d)
    _send(BILL_EMAIL, subject, html)


def send_donna_report(service_date: str | None = None) -> None:
    d = service_date or most_recent_sunday()
    subject, html = donna_report(d)
    _send(DONNA_EMAIL, subject, html)


def send_kaci_report(service_date: str | None = None) -> None:
    d = service_date or most_recent_sunday()
    subject, html = kaci_report(d)
    _send(KACI_EMAIL, subject, html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send connect card weekly reports.")
    parser.add_argument("--bill",  action="store_true", help="Send Bill's next steps + comments report")
    parser.add_argument("--donna", action="store_true", help="Send Donna's attendance report")
    parser.add_argument("--kaci",  action="store_true", help="Send Kaci's prayer requests report")
    parser.add_argument("--all",   action="store_true", help="Send all three reports")
    parser.add_argument("--date",  default=None, help="Service date (YYYY-MM-DD); defaults to most recent Sunday")
    args = parser.parse_args()

    if not any([args.bill, args.donna, args.kaci, args.all]):
        parser.error("Specify at least one of --bill, --donna, --kaci, or --all.")

    service_date = args.date

    if args.bill or args.all:
        send_bill_report(service_date)
    if args.donna or args.all:
        send_donna_report(service_date)
    if args.kaci or args.all:
        send_kaci_report(service_date)
