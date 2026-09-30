"""jobs/congregation/kidstoday_notify_donna.py -- weekly Sunday 11:45pm send
to Donna (via email, per her schedule) of the /cat/kidstoday form so she can
set servant overrides before the 2:55pm kidsatt_weekly send.

Cron:
  45 23 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.kidstoday_notify_donna \
    >> /home/billyomes/watson/logs/kidstoday_notify_donna.log 2>&1
"""
import logging
from datetime import datetime, time
import os

from jobs.email_job.brevo_send import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidstoday_notify_donna] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KIDSTODAY_URL = "https://wtsn.me/cat/kidstoday"


def should_send_to_donna() -> bool:
    """Donna gets emails on Tue/Wed/Thu 9am per feedback_donna_email_schedule."""
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour
    return weekday in (1, 2, 3) and 8 <= hour <= 10


def main() -> None:
    # Emails to Donna queue for the next Tue/Wed/Thu 9am slot per her schedule.
    # This Sunday 11:45pm send queues for the next-day-or-later slot.

    send_email(
        to="donna.redman@gmail.com",
        subject="Kids Servants Assignment for Tomorrow",
        html=f"""
        <p>Hi Donna,</p>
        <p>It's time to review and update tomorrow's kids servant assignments if needed.</p>
        <p><a href="{KIDSTODAY_URL}">Open the Kids Servants form</a></p>
        <p>You can set overrides for any class if someone other than the default leader is serving tomorrow. Watson will send the updated list to the kids team at 2:55pm.</p>
        <p>- Watson</p>
        """,
    )
    log.info("Sent kidstoday form to Donna")


if __name__ == "__main__":
    main()
