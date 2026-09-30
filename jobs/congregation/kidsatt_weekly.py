"""jobs/congregation/kidsatt_weekly.py -- weekly Sunday 2:55pm Telegram
send of /cat/kidsatt (kids attendance tracker) to Lucie and Tara.

Cron:
  55 14 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.kidsatt_weekly \
    >> /home/billyomes/watson/logs/kidsatt_weekly.log 2>&1
"""
import logging

from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidsatt_weekly] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KIDSATT_URL = "https://wtsn.me/cat/kidsatt"
TARGETS = {
    "Lucie Hale": 332,
    "Tara Mathena": 450,
}


def main() -> None:
    message = f"Kids attendance tracker:\n{KIDSATT_URL}"

    for name, person_id in TARGETS.items():
        sent = send_to_person(person_id, message)
        if sent:
            log.info("Sent kidsatt to %s", name)
        else:
            log.error("FAILED to send kidsatt to %s (person_id=%s)", name, person_id)


if __name__ == "__main__":
    main()
