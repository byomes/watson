"""jobs/congregation/serving_reminder.py -- weekly Sunday 1pm Telegram
nudge to Catalyst staff, pointing them at wtsn.me/cat/serving to check off
who served that day (see [[project_servant_banquet_tracking]] /
jobs/congregation/servants_web.py for the page itself).

Reuses jobs/congregation/pin_collection.TARGETS for the
congregation.db member_id -> watson.db people.id mapping -- same 6 staff,
already onboarded to Watson's Telegram bot (confirmed by that job's PIN
collection send). Per Bill 2026-09-22: staff should check the page
themselves each week, not reply with names over chat -- the page's
existing toggle UI is the "register who served" mechanism, this job is
just the reminder.

Cron:
  0 13 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.serving_reminder \
    >> /home/billyomes/watson/logs/serving_reminder.log 2>&1
"""
import logging

from jobs.congregation.pin_collection import TARGETS
from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [serving_reminder] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SERVING_URL = "https://wtsn.me/cat/serving"


def _build_message(first_name: str) -> str:
    return (
        f"Hi {first_name}, it's time to register who served today. "
        f"Check off your team's roster here: {SERVING_URL}"
    )


def main() -> None:
    for name, ids in TARGETS.items():
        first_name = name.split()[0]
        message = _build_message(first_name)
        sent = send_to_person(ids["person_id"], message)
        if sent:
            log.info("Sent serving reminder to %s", name)
        else:
            log.error("FAILED to send serving reminder to %s (person_id=%s)", name, ids["person_id"])


if __name__ == "__main__":
    main()
