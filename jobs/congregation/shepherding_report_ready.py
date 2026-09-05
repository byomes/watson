"""Shepherding Report Ready — weekly Telegram nudge pointing to the report
page, replacing the old shepherding_report.py (email) and
elder_shepherding_report.py (counts-in-message) jobs, both retired
2026-09-02 after Bill flagged getting two Telegram pings + an email every
Wednesday morning delivering report content directly. This job sends only
a short "it's ready" notice with a link -- the actual report content lives
at wtsn.me/cat/shepherdingreport (jobs/congregation/elder_shepherding_report_web.py,
unchanged and still backed by build_deacon_group_names() in
jobs/congregation/elder_shepherding_report.py, which is why that file was
NOT deleted even though its own send path is retired).

RECIPIENT_NAMES: Bill reviewed a test send to himself alone on 2026-09-02
and confirmed it, so Jim Bouchat and Bill Crook were added the same day
(same names used by the old elder_shepherding_report.py RECIPIENT_NAMES,
both already Telegram-onboarded).

Cron (Wednesday 8:30am, per Bill's explicit request when this job was
created -- later than the old jobs' 6:00am/6:15am slots):
  30 8 * * 3 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.shepherding_report_ready \
    >> /home/billyomes/watson/logs/shepherding_report_ready.log 2>&1

Usage:
  python3 -m jobs.congregation.shepherding_report_ready
"""

import logging
import os

from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.connect_cards.shepherding_report import _today
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [shepherding_report_ready] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RECIPIENT_NAMES = ("Bill Yomes", "Jim Bouchat", "Bill Crook")
REPORT_URL = "https://wtsn.me/cat/shepherdingreport"


def build_message() -> str:
    today = _today()
    return (
        f"\U0001f4ca This week's Catalyst Shepherding Report is ready — {today}\n"
        f"{REPORT_URL}"
    )


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def send_shepherding_report_ready() -> bool:
    """Notify everyone in RECIPIENT_NAMES that this week's report is ready.
    Returns True if at least one send succeeded."""
    text = build_message()

    if vacation_gate("normal", "jobs.congregation.shepherding_report_ready", text):
        log.info("Vacation mode is on — Shepherding Report Ready notice suppressed (logged).")
        return False

    with get_connection() as conn:
        ids = {name: _person_id(conn, name) for name in RECIPIENT_NAMES}

    sent_any = False
    for name, person_id in ids.items():
        if person_id is None:
            log.error("No people row found for %r — skipped", name)
            continue
        if send_to_person(person_id, text):
            log.info("Sent Shepherding Report Ready notice to %s", name)
            sent_any = True
        else:
            log.warning("Failed to send Shepherding Report Ready notice to %s (not onboarded?)", name)

    return sent_any


if __name__ == "__main__":
    print("Sending Shepherding Report Ready notice...")
    print(build_message())
    send_shepherding_report_ready()
