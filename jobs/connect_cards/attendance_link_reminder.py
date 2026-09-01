"""
Attendance link reminder — Sunday 3pm nudge to add human corrections
before missed_report.py runs Tuesday 7am.

Sends a Telegram message with the wtsn.me/cat/attendance link to Jim
Bouchat, Donna Redman, Bill Crook, Bill Yomes, Lucie Hale, Tara Mathena,
and Tyler McCauley via jobs/telegram/send_to_person.py.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/attendance_link_reminder.py

Cron (Sunday 3:00pm):
  0 15 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/attendance_link_reminder.py \
    >> /home/billyomes/watson/logs/attendance_link_reminder.log 2>&1
"""

import logging
import os

from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [attendance_link_reminder] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RECIPIENT_NAMES = (
    "Jim Bouchat",
    "Donna Redman",
    "Bill Crook",
    "Bill Yomes",
    "Lucie Hale",
    "Tara Mathena",
    "Tyler McCauley",
)

MESSAGE = (
    "\U0001F4CB Sunday attendance check-in\n\n"
    "Add today's corrections at https://wtsn.me/cat/attendance before "
    "Tuesday 7am -- that's when the Missed report goes out."
)


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def run() -> None:
    if vacation_gate("normal", "jobs.connect_cards.attendance_link_reminder", MESSAGE):
        return

    with get_connection() as conn:
        ids = {name: _person_id(conn, name) for name in RECIPIENT_NAMES}

    for name, person_id in ids.items():
        if person_id is None:
            log.error("No people row found for %r -- skipped", name)
            continue
        if send_to_person(person_id, MESSAGE):
            log.info("Sent attendance link reminder to %s", name)
        else:
            log.warning("Failed to send attendance link reminder to %s (not onboarded?)", name)


if __name__ == "__main__":
    run()
