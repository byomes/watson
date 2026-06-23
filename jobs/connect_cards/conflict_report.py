"""
Member conflict report — sends pending member_conflicts to Telegram for review.

Each conflict is sent as a separate Telegram message with inline buttons.
Watson never modifies any data until a button is tapped in bot.py.

Cron (Sunday 5pm):
  0 17 * * 0  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/conflict_report.py \
    >> /home/billyomes/watson/logs/conflict_report.log 2>&1

Usage:
  python3 /home/billyomes/watson/jobs/connect_cards/conflict_report.py
"""

import logging
import os
import sqlite3
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [conflict_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

# Import via config.settings so WATSON_BOT_TOKEN / TELEGRAM_BOT_TOKEN both work
from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID


def _send(text: str, keyboard: list | None = None) -> None:
    payload: dict = {"chat_id": WATSON_CHAT_ID, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()


def run() -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return

    conn = sqlite3.connect(CONG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM member_conflicts WHERE status = 'pending' ORDER BY detected_at ASC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        _send("✅ No member conflicts to review this week.")
        log.info("No pending conflicts.")
        return

    log.info("Sending %d pending conflict(s) to Telegram.", len(rows))

    for row in rows:
        cid = row["id"]

        if row["conflict_type"] == "shared_email":
            text = (
                f"⚠️ Shared Email Conflict\n"
                f"Email: {row['new_email']}\n\n"
                f"Existing member: {row['existing_name']}\n"
                f"New card name: {row['new_name']}\n\n"
                f"Are these the same person or different people?"
            )
            keyboard = [[
                {"text": "Same Person ✓",      "callback_data": f"mc_same:{cid}"},
                {"text": "Different People ✓", "callback_data": f"mc_diff:{cid}"},
                {"text": "Skip",               "callback_data": f"mc_skip:{cid}"},
            ]]
        else:  # same_name_diff_email
            text = (
                f"⚠️ Name Match, Different Email\n"
                f"Name: {row['existing_name']}\n\n"
                f"Existing email: {row['existing_email']}\n"
                f"New card email: {row['new_email']}\n\n"
                f"Should the email be updated?"
            )
            keyboard = [[
                {"text": "Update Email ✓",          "callback_data": f"mc_update_email:{cid}"},
                {"text": "Keep Both as Separate ✓", "callback_data": f"mc_keep_sep:{cid}"},
                {"text": "Skip",                    "callback_data": f"mc_skip:{cid}"},
            ]]

        try:
            _send(text, keyboard)
            log.info("Sent conflict id=%d type=%s", cid, row["conflict_type"])
        except Exception as exc:
            log.error("Failed to send conflict id=%d: %s", cid, exc)

        time.sleep(2)


if __name__ == "__main__":
    run()
