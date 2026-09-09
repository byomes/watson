"""
Monthly house-call report — emails Jim (Bill's funeral home boss) every
not-yet-reported house call, with the date/time and amount owed, then marks
those rows reported.

Reports by "unreported", not by calendar month — a call logged late still
gets included next run instead of silently missed, and marking reported_at
only on a confirmed send means nothing can be double-reported either.

Cron (1st of month, 8am):
  0 8 1 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/house_calls/monthly_report.py \
    >> /home/billyomes/watson/logs/house_calls_report.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.house_calls.monthly_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.house_calls.monthly_report --dry-run
  PYTHONPATH=/home/billyomes/watson python -m jobs.house_calls.monthly_report --to someone@example.com
"""
import argparse
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email
from jobs.house_calls.db import init_db, mark_reported, unreported_calls

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [house_calls.monthly_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

BOSS_NAME = "Jim"
BOSS_EMAIL = os.getenv("FUNERAL_HOME_BOSS_EMAIL", "")
PREVIEW_EMAIL = "pastorbill@catalyst302.com"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("WATSON_CHAT_ID", "")


def _telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured, skipping notify: %s", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        log.warning("Telegram notify failed: %s", exc)


def _when_label(called_at: str) -> str:
    return datetime.strptime(called_at, "%Y-%m-%d %H:%M").strftime("%b %-d, %Y %-I:%M %p")


def build_report(rows) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the pending house calls."""
    today = datetime.now(NY).strftime("%B %Y")
    subject = f"House Calls — {today}"
    total = sum(r["amount"] for r in rows)

    lines = [
        f"{_when_label(r['called_at'])} — {r['family_last_name']} — ${r['amount']:.2f}"
        for r in rows
    ]
    text_body = (
        f"Hi {BOSS_NAME},\n\n"
        f"House calls to bill for, as of today ({datetime.now(NY).strftime('%B %-d, %Y')}):\n\n"
        + "\n".join(lines)
        + f"\n\nTotal: {len(rows)} calls, ${total:.2f}\n\nThanks,\nBill"
    )

    rows_html = "".join(
        f"<tr><td>{_when_label(r['called_at'])}</td><td>{r['family_last_name']}</td>"
        f"<td>${r['amount']:.2f}</td></tr>"
        for r in rows
    )
    html_body = (
        "<html><body style='font-family:Georgia,serif;color:#222'>"
        f"<p>Hi {BOSS_NAME},</p>"
        f"<h2 style='border-bottom:2px solid #333;padding-bottom:8px'>House Calls — {today}</h2>"
        "<table style='width:100%;border-collapse:collapse;font-size:.95em'>"
        "<thead><tr>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Date/Time</th>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Family</th>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Amount</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
        f"<p style='margin-top:16px'><strong>Total: {len(rows)} calls, ${total:.2f}</strong></p>"
        "<p>Thanks,<br>Bill</p>"
        "</body></html>"
    )
    return subject, text_body, html_body


def send_report(dry_run: bool = False, to_override: str | None = None) -> None:
    init_db()
    rows = unreported_calls()

    if not rows:
        log.info("No unreported house calls — nothing to send.")
        if not dry_run:
            _telegram("No house calls to report this period. - Watson")
        return

    subject, text_body, html_body = build_report(rows)

    if dry_run:
        print(f"Subject: {subject}\n\n{text_body}")
        return

    to = to_override or BOSS_EMAIL
    if not to:
        total = sum(r["amount"] for r in rows)
        names = ", ".join(r["family_last_name"] for r in rows)
        _telegram(
            f"House call report is ready ({len(rows)} calls, ${total:.2f}: {names}) but "
            "FUNERAL_HOME_BOSS_EMAIL isn't set in .env yet — send me Jim's email "
            "and I'll send it next run. Nothing has been marked reported. - Watson"
        )
        log.warning("FUNERAL_HOME_BOSS_EMAIL not set — report not sent, rows left unreported.")
        return

    result = send_email(
        to_email=to, to_name=BOSS_NAME, subject=subject,
        text_body=text_body, html_body=html_body, include_signature=False,
    )
    if not result["success"]:
        _telegram(f"House call report FAILED to send to {to}: {result['error']}. - Watson")
        raise RuntimeError(f"Brevo send to {to} failed: {result['error']}")

    mark_reported([r["id"] for r in rows])
    total = sum(r["amount"] for r in rows)
    names = ", ".join(r["family_last_name"] for r in rows)
    log.info("Sent %r to %s (%d calls, $%.2f)", subject, to, len(rows), total)
    _telegram(f"Sent house call report to {to}: {len(rows)} calls, ${total:.2f} — {names}. - Watson")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the monthly house call report.")
    parser.add_argument("--dry-run", action="store_true", help="Print the report without sending or marking rows reported")
    parser.add_argument("--to", default=None, help="Override recipient email (takes precedence over FUNERAL_HOME_BOSS_EMAIL)")
    args = parser.parse_args()
    send_report(dry_run=args.dry_run, to_override=args.to)
