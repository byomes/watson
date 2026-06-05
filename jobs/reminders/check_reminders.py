# * * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/reminders/check_reminders.py
import logging
import os
import sqlite3
from datetime import datetime

import requests

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    due = conn.execute(
        "SELECT * FROM reminders WHERE due_datetime <= datetime('now') AND status = 'active'"
    ).fetchall()
    for r in due:
        try:
            send_telegram(f"⏰ Reminder: {r['title']}")
            conn.execute("UPDATE reminders SET status = 'fired' WHERE id = ?", (r["id"],))
            conn.commit()
            log.info("Fired reminder id=%s title=%r", r["id"], r["title"])
        except Exception as e:
            log.error("Failed to fire reminder id=%s: %s", r["id"], e)
    conn.close()


if __name__ == "__main__":
    main()
