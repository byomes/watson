"""
Weekly completed Catalyst tasks report.
Queries team_tasks for catalyst tasks completed in the last 7 days and emails
a summary to Bill and Donna.

Recipients: BILL_EMAIL, DONNA_EMAIL (from .env)
Schedule:   Thursday 1pm — 0 13 * * 4 (see crontab)

Usage:
  python3 jobs/team/weekly_completed_report.py
  python3 jobs/team/weekly_completed_report.py --dry-run
"""

import os
import smtplib
import sqlite3
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587
SMTP_USER  = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS  = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
FROM_ADDR  = os.getenv("WATSON_FROM_ADDRESS") or SMTP_USER

BILL_EMAIL  = os.getenv("BILL_EMAIL", "")
DONNA_EMAIL = os.getenv("DONNA_EMAIL", "")

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

_PRI_LABEL = {"1": "High", "2": "Medium", "3": "Low", "high": "High", "medium": "Medium", "low": "Low"}


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return date.fromisoformat(iso[:10]).strftime("%b %-d, %Y")
    except ValueError:
        return iso[:10]


def _build_html(tasks: list, date_from: str, date_to: str) -> str:
    from_fmt = date.fromisoformat(date_from).strftime("%b %-d")
    to_fmt   = date.fromisoformat(date_to).strftime("%b %-d, %Y")

    header = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#222;max-width:620px;margin:0 auto;padding:20px">
  <h2 style="margin:0 0 4px;font-size:18px;color:#111">Weekly Completed Tasks — Catalyst</h2>
  <p style="margin:0 0 20px;font-size:13px;color:#666">{from_fmt} – {to_fmt}</p>
"""

    if not tasks:
        body = '  <p style="font-size:14px;color:#555">No tasks completed this week.</p>\n'
    else:
        rows = ""
        for t in tasks:
            pri_raw   = (t["priority"] or "").strip()
            pri_label = _PRI_LABEL.get(pri_raw, pri_raw) if pri_raw else ""
            pri_cell  = (
                f'<span style="font-size:11px;background:#eee;border-radius:3px;'
                f'padding:1px 5px;margin-left:6px;color:#555">{pri_label}</span>'
                if pri_label else ""
            )
            done_date = _fmt_date(t["completed_at"])
            rows += (
                f'  <tr style="border-bottom:1px solid #eee">'
                f'<td style="padding:9px 8px;font-size:14px;color:#111">'
                f'{t["title"]}{pri_cell}</td>'
                f'<td style="padding:9px 8px;font-size:13px;color:#888;white-space:nowrap">'
                f'{done_date}</td>'
                f'</tr>\n'
            )
        count = len(tasks)
        body = (
            f'  <p style="font-size:13px;color:#555;margin-bottom:8px">'
            f'{count} task{"s" if count != 1 else ""} completed</p>\n'
            f'  <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif">\n'
            f'  <thead><tr style="background:#f5f5f5">'
            f'<th style="padding:8px;text-align:left;font-size:12px;color:#777;font-weight:600">TASK</th>'
            f'<th style="padding:8px;text-align:left;font-size:12px;color:#777;font-weight:600">COMPLETED</th>'
            f'</tr></thead>\n'
            f'  <tbody>\n{rows}  </tbody>\n  </table>\n'
        )

    footer = (
        '  <p style="margin-top:24px;font-size:11px;color:#bbb">'
        'Watson · Catalyst Community Church</p>\n'
        '</body>\n</html>'
    )
    return header + body + footer


def _send(recipients: list[str], subject: str, html: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{FROM_ADDR}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_ADDR, recipients, msg.as_string())

    print(f"Sent: {subject!r} → {', '.join(recipients)}")


def run(dry_run: bool = False) -> None:
    today     = date.today()
    date_to   = today.isoformat()
    date_from = (today - timedelta(days=7)).isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        tasks = conn.execute(
            """
            SELECT title, completed_at, priority
            FROM team_tasks
            WHERE category = 'catalyst'
              AND status = 'completed'
              AND completed_at >= datetime('now', '-7 days')
            ORDER BY completed_at DESC
            """,
        ).fetchall()
    finally:
        conn.close()

    from_fmt = date.fromisoformat(date_from).strftime("%b %-d")
    to_fmt   = date.fromisoformat(date_to).strftime("%b %-d, %Y")
    subject  = f"Weekly Completed Tasks — Catalyst | {from_fmt} – {to_fmt}"
    html     = _build_html(list(tasks), date_from, date_to)

    if dry_run:
        print(f"Subject : {subject}")
        print(f"To      : {BILL_EMAIL}, {DONNA_EMAIL}")
        print(f"Tasks   : {len(tasks)} found in last 7 days")
        print("-" * 60)
        for t in tasks:
            pri = t["priority"] or "—"
            print(f"  [{_fmt_date(t['completed_at'])}] {t['title']}  (priority: {pri})")
        if not tasks:
            print("  (none)")
        print("-" * 60)
        print("[dry-run] Email not sent.")
        return

    recipients = [a for a in [BILL_EMAIL, DONNA_EMAIL] if a]
    if not recipients:
        print("ERROR: No recipients configured — set BILL_EMAIL and DONNA_EMAIL in .env.", file=sys.stderr)
        sys.exit(1)

    _send(recipients, subject, html)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
