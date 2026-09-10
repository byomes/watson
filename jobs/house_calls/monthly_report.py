"""
Monthly house-call report — emails Jim (Bill's funeral home boss) about
every not-yet-reported house call, then marks those rows reported.

Reports by "unreported", not by calendar month — a call logged late still
gets included next run instead of silently missed, and marking reported_at
only on a confirmed send means nothing can be double-reported either.

Calls Jim already paid Bill for directly (toggled "paid" on the dashboard)
are never billed again — they're left out of the amount owed. If every
unreported call for the period has already been paid, the bill flips to a
confirmation email instead, telling Jim nothing is owed rather than staying
silent (silence would look like the report just didn't run).

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


def _rows_lines(rows) -> str:
    return "\n".join(
        f"{_when_label(r['called_at'])} — {r['family_last_name']} — ${r['amount']:.2f}"
        for r in rows
    )


def _rows_table_html(rows) -> str:
    rows_html = "".join(
        f"<tr><td>{_when_label(r['called_at'])}</td><td>{r['family_last_name']}</td>"
        f"<td>${r['amount']:.2f}</td></tr>"
        for r in rows
    )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:.95em'>"
        "<thead><tr>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Date/Time</th>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Family</th>"
        "<th style='text-align:left;border-bottom:2px solid #ddd;padding:6px 8px'>Amount</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )


def build_bill_report(unpaid_rows, paid_rows) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) billing Jim for unpaid_rows.

    paid_rows (already paid directly, out of band) are noted for his
    records but left out of the amount owed.
    """
    today = datetime.now(NY).strftime("%B %Y")
    subject = f"House Calls — {today}"
    total = sum(r["amount"] for r in unpaid_rows)

    paid_note_text = ""
    paid_note_html = ""
    if paid_rows:
        paid_names = ", ".join(r["family_last_name"] for r in paid_rows)
        n = len(paid_rows)
        paid_note_text = (
            f"\n\n(Already paid directly, no action needed: {n} call{'s' if n != 1 else ''} — {paid_names})"
        )
        paid_note_html = (
            f"<p style='margin-top:10px;color:#666;font-size:.9em'>Already paid directly, no action "
            f"needed: {n} call{'s' if n != 1 else ''} — {paid_names}</p>"
        )

    text_body = (
        f"Hi {BOSS_NAME},\n\n"
        f"House calls to bill for, as of today ({datetime.now(NY).strftime('%B %-d, %Y')}):\n\n"
        + _rows_lines(unpaid_rows)
        + f"\n\nTotal: {len(unpaid_rows)} calls, ${total:.2f}"
        + paid_note_text
        + "\n\nThanks,\nBill"
    )
    html_body = (
        "<html><body style='font-family:Georgia,serif;color:#222'>"
        f"<p>Hi {BOSS_NAME},</p>"
        f"<h2 style='border-bottom:2px solid #333;padding-bottom:8px'>House Calls — {today}</h2>"
        + _rows_table_html(unpaid_rows)
        + f"<p style='margin-top:16px'><strong>Total: {len(unpaid_rows)} calls, ${total:.2f}</strong></p>"
        + paid_note_html
        + "<p>Thanks,<br>Bill</p>"
        "</body></html>"
    )
    return subject, text_body, html_body


def build_confirmation_report(rows) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) confirming everything for the
    period was already paid directly — sent instead of the bill so Jim gets
    a positive confirmation rather than silence."""
    today = datetime.now(NY).strftime("%B %Y")
    subject = f"House Calls — {today} (Paid in Full)"
    total = sum(r["amount"] for r in rows)

    text_body = (
        f"Hi {BOSS_NAME},\n\n"
        f"Just confirming — all house calls for this period have already been paid "
        f"directly, so nothing is owed. For your records:\n\n"
        + _rows_lines(rows)
        + f"\n\nTotal: {len(rows)} calls, ${total:.2f} (already paid)\n\nThanks,\nBill"
    )
    html_body = (
        "<html><body style='font-family:Georgia,serif;color:#222'>"
        f"<p>Hi {BOSS_NAME},</p>"
        f"<h2 style='border-bottom:2px solid #333;padding-bottom:8px'>House Calls — {today} (Paid in Full)</h2>"
        f"<p>Just confirming — all house calls for this period have already been paid directly, "
        f"so nothing is owed. For your records:</p>"
        + _rows_table_html(rows)
        + f"<p style='margin-top:16px'><strong>Total: {len(rows)} calls, ${total:.2f} (already paid)</strong></p>"
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

    unpaid = [r for r in rows if not r["paid_at"]]
    paid = [r for r in rows if r["paid_at"]]

    if unpaid:
        subject, text_body, html_body = build_bill_report(unpaid, paid)
    else:
        subject, text_body, html_body = build_confirmation_report(paid)

    if dry_run:
        print(f"Subject: {subject}\n\n{text_body}")
        return

    to = to_override or BOSS_EMAIL
    if not to:
        total = sum(r["amount"] for r in unpaid)
        names = ", ".join(r["family_last_name"] for r in unpaid) or "none owed — all already paid"
        _telegram(
            f"House call report is ready ({len(unpaid)} calls owed, ${total:.2f}: {names}) but "
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
    log.info(
        "Sent %r to %s (%d billed, $%.2f owed; %d already paid)",
        subject, to, len(unpaid), sum(r["amount"] for r in unpaid), len(paid),
    )
    if unpaid:
        names = ", ".join(r["family_last_name"] for r in unpaid)
        total = sum(r["amount"] for r in unpaid)
        extra = f", {len(paid)} already paid" if paid else ""
        _telegram(f"Sent house call bill to {to}: {len(unpaid)} calls, ${total:.2f} owed — {names}{extra}. - Watson")
    else:
        _telegram(f"Sent house call report to {to}: all {len(paid)} calls already paid — confirmed nothing owed. - Watson")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the monthly house call report.")
    parser.add_argument("--dry-run", action="store_true", help="Print the report without sending or marking rows reported")
    parser.add_argument("--to", default=None, help="Override recipient email (takes precedence over FUNERAL_HOME_BOSS_EMAIL)")
    args = parser.parse_args()
    send_report(dry_run=args.dry_run, to_override=args.to)
