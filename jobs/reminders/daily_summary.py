# 0 10 * * 1,2,3,4,6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/reminders/daily_summary.py >> /home/billyomes/watson/logs/reminders.log 2>&1
# 30 13 * * 1,2,3,4,6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/reminders/daily_summary.py >> /home/billyomes/watson/logs/reminders.log 2>&1
# 0 17 * * 1,2,3,4,6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/reminders/daily_summary.py >> /home/billyomes/watson/logs/reminders.log 2>&1
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.reminders import ensure_reminders_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def send_telegram(text):
    if vacation_gate("normal", "jobs.reminders.daily_summary", text):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def main():
    with get_connection() as conn:
        ensure_reminders_schema(conn)
        rows = conn.execute(
            "SELECT id, title FROM reminders "
            "WHERE status = 'active' AND reminder_time IS NULL "
            "ORDER BY created_at ASC"
        ).fetchall()

    n = len(rows)
    if n == 0:
        log.info("No active untimed reminders — skipping summary")
        return

    titles = "\n".join(f"• {r['title']}" for r in rows)
    text = (
        f"You have {n} active reminder{'s' if n != 1 else ''}:\n\n"
        f"{titles}\n\n"
        f"View or manage them in the dashboard Reminders tab"
    )
    send_telegram(text)
    log.info("Sent daily summary: %d reminder(s)", n)


if __name__ == "__main__":
    main()
