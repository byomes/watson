"""
Weekly duplicate-member digest — rescans for candidate duplicate pairs and
sends a Telegram nudge with the wtsn.me/cat/duplicates review link.

Supersedes jobs/connect_cards/conflict_report.py's Sunday 5pm run (retired
2026-08-30 — that job's own detection pass + per-conflict Telegram
buttons/member_conflicts table are replaced by jobs/congregation/
duplicate_review.py's scan + the wtsn.me/cat/duplicates web review UI).
This job just triggers the same scan and points Dr. Bill at the page,
rather than re-sending one Telegram message per candidate.

Also nudges Donna Redman and Jim Bouchat (added 2026-09-20, same
recipients as attendance_link_reminder.py's 3pm text) so they can check
wtsn.me/cat/duplicates before doing attendance corrections.

Cron (Sunday 2pm — moved from 4pm 2026-09-20 so the scan lands before
attendance_link_reminder.py's 3pm correction text, not after it):
  0 14 * * 0  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/congregation/duplicates_weekly_notify.py \
    >> /home/billyomes/watson/logs/duplicates_weekly_notify.log 2>&1
"""
import logging
import os
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.congregation.duplicate_review import DB_PATH, scan_for_duplicates
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [duplicates_weekly_notify] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REVIEW_URL = "https://wtsn.me/cat/duplicates"

EXTRA_RECIPIENT_NAMES = ("Donna Redman", "Jim Bouchat")


def _pending_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM duplicate_flags WHERE status = 'pending' AND member_id_a != member_id_b"
        ).fetchone()[0]
    finally:
        conn.close()


def _send(text: str) -> None:
    if vacation_gate("normal", "jobs.congregation.duplicates_weekly_notify", text):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json={"chat_id": WATSON_CHAT_ID, "text": text},
        timeout=10,
    )
    resp.raise_for_status()


def _send_to_extras(text: str) -> None:
    if vacation_gate("normal", "jobs.congregation.duplicates_weekly_notify", text):
        return
    with get_connection() as conn:
        ids = {
            name: conn.execute(
                "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
            for name in EXTRA_RECIPIENT_NAMES
        }
    for name, row in ids.items():
        if row is None:
            log.error("No people row found for %r -- skipped", name)
            continue
        if send_to_person(row["id"], text):
            log.info("Sent duplicate digest to %s", name)
        else:
            log.warning("Failed to send duplicate digest to %s (not onboarded?)", name)


def run() -> None:
    new_count = scan_for_duplicates()
    pending = _pending_count()
    log.info("Scan added %d new candidate(s); %d pending total.", new_count, pending)

    if pending == 0:
        text = "✅ No possible duplicate members to review this week."
    else:
        text = (
            f"👥 {pending} possible duplicate member{'s' if pending != 1 else ''} to review "
            f"({new_count} new this week)\n\n{REVIEW_URL}"
        )

    _send(text)
    _send_to_extras(text)


if __name__ == "__main__":
    run()
