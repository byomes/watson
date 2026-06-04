"""
Connect card email reports — send weekly summaries to Bill, Donna, and Kaci.

Configuration (env vars):
  BILL_EMAIL                Recipient for next steps + comments report
  DONNA_EMAIL               Recipient for attendance report
  KACI_EMAIL                Recipient for prayer requests report
  WATSON_GMAIL_ADDRESS      SMTP login / from address (smtp.startlogic.com)
  WATSON_GMAIL_APP_PASSWORD SMTP password / app password

Cron (on watson):
  0 5 * * 1  python3 -m jobs.connect_cards.email_reports --all           (Monday primary)
  0 5 * * 4  python3 -m jobs.connect_cards.email_reports --all --updated (Thursday updated)
  0 4 * * 0  python3 -m jobs.connect_cards.email_reports --sync          (Sunday congregation sync)

Usage:
  python3 -m jobs.connect_cards.email_reports --all
  python3 -m jobs.connect_cards.email_reports --all --updated
  python3 -m jobs.connect_cards.email_reports --bill --date 2026-06-01
  python3 -m jobs.connect_cards.email_reports --sync
  python3 -m jobs.connect_cards.email_reports --sync --date 2026-06-01
"""

import argparse
import os
import smtplib
import sqlite3
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

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def most_recent_sunday() -> str:
    today = date.today()
    days_ago = (today.weekday() + 1) % 7
    return (today - timedelta(days=days_ago)).isoformat()


def _previous_monday_5am(sunday: str) -> str:
    """Return 'YYYY-MM-DD 05:00:00' for the Monday before the given Sunday."""
    sun = date.fromisoformat(sunday)
    monday = sun - timedelta(days=6)
    return f"{monday.isoformat()} 05:00:00"


def _send(to: str, subject: str, html: str) -> None:
    if not FROM_ADDR or not FROM_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    if not to:
        raise RuntimeError("Recipient address is empty — check env vars.")

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


def send_bill_report(service_date: str | None = None, updated: bool = False) -> None:
    d = service_date or most_recent_sunday()
    subject, html = bill_report(d, updated=updated)
    _send(BILL_EMAIL, subject, html)


def send_donna_report(service_date: str | None = None, updated: bool = False) -> None:
    d = service_date or most_recent_sunday()
    subject, html = donna_report(d, updated=updated)
    _send(DONNA_EMAIL, subject, html)


def send_kaci_report(service_date: str | None = None, updated: bool = False) -> None:
    d = service_date or most_recent_sunday()
    subject, html = kaci_report(d, updated=updated)
    _send(KACI_EMAIL, subject, html)


# ── Sunday congregation sync ──────────────────────────────────────────────────

def sync_attendance(service_date: str | None = None) -> None:
    """
    Sync congregation records from new connect cards submitted since Monday 5am.
    Updates last_seen and fills in blank contact fields. No emails sent.
    """
    d = service_date or most_recent_sunday()
    cutoff = _previous_monday_5am(d)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        new_cards = conn.execute(
            """
            SELECT name, email, phone, campus, service_date
            FROM connect_cards
            WHERE service_date = ?
              AND created_at > ?
            """,
            (d, cutoff),
        ).fetchall()

        if not new_cards:
            print(f"[sync_attendance] No new cards for {d} since {cutoff}.")
            return

        updated = 0
        for card in new_cards:
            email = (card["email"] or "").strip()
            name  = (card["name"] or "").strip()

            # Match congregation record by email, then by name
            cong = None
            if email:
                cong = conn.execute(
                    "SELECT id, email, phone FROM congregation WHERE email = ?",
                    (email,),
                ).fetchone()
            if cong is None and name:
                cong = conn.execute(
                    "SELECT id, email, phone FROM congregation WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()

            if cong is None:
                continue

            conn.execute(
                """
                UPDATE congregation
                SET last_seen = ?,
                    email = CASE WHEN TRIM(COALESCE(email, '')) = '' THEN ? ELSE email END,
                    phone = CASE WHEN TRIM(COALESCE(phone, '')) = '' THEN ? ELSE phone END,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (d, email, card["phone"] or "", cong["id"]),
            )
            updated += 1

        conn.commit()
        print(
            f"[sync_attendance] {d}: {len(new_cards)} new card(s) since {cutoff}, "
            f"{updated} congregation record(s) updated."
        )
    finally:
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send connect card weekly reports.")
    parser.add_argument("--all",     action="store_true", help="Send all three reports")
    parser.add_argument("--bill",    action="store_true", help="Send Bill's next steps + comments report")
    parser.add_argument("--donna",   action="store_true", help="Send Donna's attendance report")
    parser.add_argument("--kaci",    action="store_true", help="Send Kaci's prayer requests report")
    parser.add_argument("--updated", action="store_true", help="Mark as Thursday updated run (adds flag to subject and header note)")
    parser.add_argument("--sync",    action="store_true", help="Run congregation sync only — no emails sent")
    parser.add_argument("--date",    default=None,        help="Service date (YYYY-MM-DD); defaults to most recent Sunday")
    args = parser.parse_args()

    if not any([args.all, args.bill, args.donna, args.kaci, args.sync]):
        parser.error("Specify at least one of --all, --bill, --donna, --kaci, or --sync.")

    service_date = args.date

    if args.sync:
        sync_attendance(service_date)
    else:
        updated = args.updated
        if args.bill or args.all:
            send_bill_report(service_date, updated=updated)
        if args.donna or args.all:
            send_donna_report(service_date, updated=updated)
        if args.kaci or args.all:
            send_kaci_report(service_date, updated=updated)
