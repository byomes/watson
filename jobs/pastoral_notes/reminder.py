"""
Sends follow-up reminders for notes_pending rows that have gone unanswered,
then expires rows that were reminded but still unanswered after another 2 hours.
Run on a cron every ~30 minutes.
"""

import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.pastoral_notes.db import get_db

log = logging.getLogger(__name__)


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def run() -> None:
    with get_db() as conn:
        # Rows pending > 2 hours with no reminder yet → send first reminder
        to_remind = conn.execute(
            """SELECT id, appointment_title, appointment_time
               FROM notes_pending
               WHERE status = 'pending'
                 AND reminded_at IS NULL
                 AND prompted_at <= datetime('now', '-2 hours')"""
        ).fetchall()

        for row in to_remind:
            msg = (
                f"Reminder: you haven't logged notes from your meeting — "
                f"{row['appointment_title']} at {row['appointment_time']}. "
                f"Reply with your notes or 'skip'."
            )
            _send_telegram(msg)
            conn.execute(
                "UPDATE notes_pending SET reminded_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            log.info("Sent reminder for: %s", row["appointment_title"])

        # Rows already reminded > 2 hours ago with no response → expire
        to_expire = conn.execute(
            """SELECT id, appointment_title
               FROM notes_pending
               WHERE status = 'pending'
                 AND reminded_at IS NOT NULL
                 AND reminded_at <= datetime('now', '-2 hours')"""
        ).fetchall()

        for row in to_expire:
            conn.execute(
                "UPDATE notes_pending SET status = 'expired' WHERE id = ?",
                (row["id"],),
            )
            log.info("Expired notes prompt for: %s", row["appointment_title"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
