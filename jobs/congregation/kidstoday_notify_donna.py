"""jobs/congregation/kidstoday_notify_donna.py -- weekly Sunday 11:45pm send
to Donna (via email, per her schedule) of the /cat/kidstoday form so she can
set servant overrides before the 2:55pm kidsatt_weekly send.

Cron:
  45 23 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.kidstoday_notify_donna \
    >> /home/billyomes/watson/logs/kidstoday_notify_donna.log 2>&1
"""
import logging

from jobs.email_job.donna_notify import send_to_donna

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidstoday_notify_donna] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KIDSTODAY_URL = "https://wtsn.me/cat/kidstoday"

BODY = f"""<p>Hi Donna,</p>
<p>It's time to review and update tomorrow's kids servant assignments if needed.</p>
<p><a href="{KIDSTODAY_URL}">Open the Kids Servants form</a></p>
<p>You can set overrides for any class if someone other than the default leader is serving tomorrow. Watson will send the updated list to the kids team at 2:55pm.</p>"""


def main() -> None:
    # jobs.email_job.donna_notify.send_to_donna sends now if it's already a
    # Tue/Wed/Thu 9am window, otherwise queues for the next one -- this
    # Sunday 11:45pm cron always queues.
    ok = send_to_donna("Kids Servants Assignment for Tomorrow", BODY)
    log.info("Queued/sent kidstoday form to Donna" if ok else "Failed to queue/send kidstoday form to Donna")


if __name__ == "__main__":
    main()
