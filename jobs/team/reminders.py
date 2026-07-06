#!/usr/bin/env python3
"""
reminders.py — Team task overdue and unanswered-email alerts via Telegram.

Usage:
  python jobs/team/reminders.py --overdue
  python jobs/team/reminders.py --unanswered
"""
import argparse
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"
LOG_PATH  = BASE_DIR / "logs" / "team_reminders.log"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("WATSON_CHAT_ID", "")

log = logging.getLogger(__name__)


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.team.reminders", text):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    ).raise_for_status()


def run_overdue() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT t.id, t.title, t.due_date, t.member_id,
               m.name AS member_name
        FROM team_tasks t
        JOIN team_members m ON m.id = t.member_id
        WHERE t.status = 'open'
          AND t.due_date < ?
          AND m.active = 1
        ORDER BY t.member_id, t.due_date
    """, (today,)).fetchall()
    conn.close()

    if not rows:
        log.info("No overdue tasks found.")
        return

    # Group by member
    by_member: dict = {}
    for row in rows:
        mid = row["member_id"]
        if mid not in by_member:
            by_member[mid] = {"name": row["member_name"], "tasks": []}
        by_member[mid]["tasks"].append(row)

    for mid, data in by_member.items():
        name = data["name"]
        tasks = data["tasks"]
        task_lines = "\n".join(f"- {t['title']} (due {t['due_date']})" for t in tasks)
        text = (
            f"⚠️ <b>{name}</b> has {len(tasks)} overdue task(s):\n"
            f"{task_lines}\n\n"
            f"Reply 'remind {name.split()[0].lower()}' to have Watson send a reminder email."
        )
        try:
            _send_telegram(text)
            log.info("Overdue alert sent for %s (%d tasks)", name, len(tasks))
        except Exception as exc:
            log.error("Failed to send overdue alert for %s: %s", name, exc)


def run_unanswered() -> None:
    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT msg.id, msg.subject, msg.sent_at, msg.member_id,
               m.name AS member_name
        FROM team_messages msg
        JOIN team_members m ON m.id = msg.member_id
        WHERE msg.direction = 'out'
          AND msg.replied_at IS NULL
          AND msg.sent_at < ?
          AND m.active = 1
        ORDER BY msg.sent_at DESC
    """, (cutoff,)).fetchall()
    conn.close()

    if not rows:
        log.info("No unanswered emails found.")
        return

    for row in rows:
        sent_date = (row["sent_at"] or "")[:10]
        text = (
            f"📬 No reply from <b>{row['member_name']}</b> to Watson's email sent {sent_date}.\n"
            f"Subject: {row['subject']}\n"
            f"No action taken — flagging for your awareness."
        )
        try:
            _send_telegram(text)
            log.info("Unanswered alert sent for %s (msg %d)", row["member_name"], row["id"])
        except Exception as exc:
            log.error("Failed to send unanswered alert for %s: %s", row["member_name"], exc)


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--overdue", action="store_true")
    parser.add_argument("--unanswered", action="store_true")
    args = parser.parse_args()

    if args.overdue:
        run_overdue()
    elif args.unanswered:
        run_unanswered()
    else:
        parser.print_help()
