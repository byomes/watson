"""jobs/campaigns/weekly_digest.py — Weekly (or on-demand) Telegram digest of
what's queued for every active book-launch campaign.

Surfaces book_launch_sends rows that are status='scheduled' and either:
  - send_date falls within the next 7 days (normal weekly cadence), or
  - send_date is today-or-earlier and previewed_at IS NULL (an already-due
    item the digest hasn't shown yet — covers on-demand runs mid-week)

Cron: Sunday 6pm (see WATSON_ARCHITECTURE.md Active Scheduled Jobs).
On-demand: `python3 jobs/campaigns/weekly_digest.py` — same logic, runnable
any time; already-previewed rows won't be re-surfaced.
"""
import argparse
import os
from collections import defaultdict

import requests
from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.campaigns.schema import create_tables

load_dotenv(os.path.expanduser("~/watson/.env"))

TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
WATSON_DASHBOARD_URL = os.getenv("WATSON_API_URL", "https://watson.tail0243ff.ts.net")


def _send_telegram_with_buttons(text: str, keyboard: list) -> None:
    if vacation_gate("normal", "jobs.campaigns.weekly_digest", text):
        print("Vacation mode on — digest suppressed (logged).")
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — skipping send.")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "reply_markup": {"inline_keyboard": keyboard},
        },
        timeout=10,
    )
    if not resp.ok:
        print(f"Telegram send failed: {resp.text}")


def _label_for(row) -> str:
    if row["subject"]:
        return row["subject"]
    first_line = (row["body_text"] or "").splitlines()[0] if row["body_text"] else ""
    return (first_line[:70] + "...") if len(first_line) > 70 else first_line


def build_digest(campaign_id: str | None = None) -> list[dict]:
    """Return a list of {campaign, rows_by_week} for every active campaign
    that has anything to surface. Does NOT send Telegram or mark previewed —
    call run() for the full side-effecting flow."""
    conn = get_connection()
    try:
        campaigns = conn.execute(
            "SELECT * FROM book_launch_campaigns WHERE status='active'"
            + (" AND campaign_id=?" if campaign_id else ""),
            (campaign_id,) if campaign_id else (),
        ).fetchall()

        digests = []
        for campaign in campaigns:
            rows = conn.execute(
                """SELECT * FROM book_launch_sends
                   WHERE campaign_id=? AND status='scheduled'
                     AND (
                       send_date BETWEEN date('now') AND date('now', '+7 days')
                       OR (send_date <= date('now') AND previewed_at IS NULL)
                     )
                   ORDER BY week_number, send_date""",
                (campaign["campaign_id"],),
            ).fetchall()

            if not rows:
                continue

            by_week = defaultdict(list)
            for r in rows:
                by_week[r["week_number"]].append(dict(r))

            digests.append({"campaign": dict(campaign), "rows_by_week": dict(by_week)})

        return digests
    finally:
        conn.close()


def _mark_previewed(row_ids: list[int]) -> None:
    if not row_ids:
        return
    conn = get_connection()
    try:
        conn.executemany(
            "UPDATE book_launch_sends SET previewed_at=datetime('now') WHERE id=?",
            [(rid,) for rid in row_ids],
        )
        conn.commit()
    finally:
        conn.close()


def run(campaign_id: str | None = None) -> int:
    """Build and send one Telegram message per active campaign with anything
    queued. Returns the number of campaigns messaged."""
    create_tables()
    digests = build_digest(campaign_id)
    messaged = 0

    for entry in digests:
        campaign = entry["campaign"]
        rows_by_week = entry["rows_by_week"]

        lines = [f"📋 {campaign['book_title']} — queued sends"]
        keyboard = []
        all_ids = []

        for week_number in sorted(rows_by_week):
            week_rows = rows_by_week[week_number]
            lines.append(f"\nWeek {week_number} ({len(week_rows)} item{'s' if len(week_rows) != 1 else ''}):")
            for r in week_rows:
                lines.append(f"  • {r['platform']}/{r['segment']} — {_label_for(r)}")
                all_ids.append(r["id"])

            keyboard.append([
                {"text": f"Open Editor — Wk{week_number}",
                 "url": f"{WATSON_DASHBOARD_URL}/campaigns/{campaign['campaign_id']}/week/{week_number}"},
                {"text": f"Approve All — Wk{week_number}",
                 "callback_data": f"camp_approve:{campaign['campaign_id']}:{week_number}"},
            ])

        _send_telegram_with_buttons("\n".join(lines), keyboard)
        _mark_previewed(all_ids)
        messaged += 1
        print(f"Digest sent for {campaign['campaign_id']}: {len(all_ids)} item(s) across {len(rows_by_week)} week(s).")

    if not digests:
        print("Nothing to surface for any active campaign.")

    return messaged


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly (or on-demand) book-launch campaign digest.")
    parser.add_argument("--campaign-id", default=None, help="Limit to one campaign (default: all active).")
    args = parser.parse_args()
    run(campaign_id=args.campaign_id)
