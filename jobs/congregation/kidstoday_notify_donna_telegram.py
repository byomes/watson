"""jobs/congregation/kidstoday_notify_donna_telegram.py -- standing weekly
Sunday noon Telegram to Donna with the /cat/kidstoday override link, so she
has time to set a servant override before kidsatt_weekly's 2:57pm
"who's serving" send. Per Bill's direct instruction 2026-10-03.

Cron:
  0 12 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.kidstoday_notify_donna_telegram \
    >> /home/billyomes/watson/logs/kidstoday_notify_donna_telegram.log 2>&1
"""
import logging

from jobs.telegram.donna_notify import send_to_donna

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidstoday_notify_donna_telegram] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MESSAGE = (
    "Today's kids servants are the defaults (Tara for Nursery/Pre-K, Lucie for Elementary) "
    "unless you set an override before 2:57pm:\nhttps://wtsn.me/cat/kidstoday"
)


def main() -> None:
    ok = send_to_donna(MESSAGE)
    log.info("Sent kidstoday reminder to Donna" if ok else "Failed to send kidstoday reminder to Donna")


if __name__ == "__main__":
    main()
