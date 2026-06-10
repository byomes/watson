# */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/reminders/check_timed.py >> /home/billyomes/watson/logs/reminders.log 2>&1
import logging
from datetime import datetime

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.reminders import ensure_reminders_schema

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
    now = datetime.now()
    current_hhmm = now.strftime("%H:%M")

    with get_connection() as conn:
        ensure_reminders_schema(conn)
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'active' AND reminder_time IS NOT NULL"
        ).fetchall()

    for r in rows:
        try:
            rt = datetime.strptime(r["reminder_time"], "%H:%M")
            cur = datetime.strptime(current_hhmm, "%H:%M")
            diff = abs((rt - cur).total_seconds())
            if diff <= 300:  # within ±5 minutes
                send_telegram(r["title"])
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE reminders SET status = 'done', updated_at = datetime('now') WHERE id = ?",
                        (r["id"],),
                    )
                log.info("Fired timed reminder id=%s title=%r", r["id"], r["title"])
        except Exception as exc:
            log.error("Error checking reminder id=%s: %s", r["id"], exc)


if __name__ == "__main__":
    main()
