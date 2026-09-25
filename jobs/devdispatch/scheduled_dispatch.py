"""jobs/devdispatch/scheduled_dispatch.py -- cron entrypoint for held Claude
Code instructions (see jobs/devdispatch/scheduled.py for the mechanism).

Cron entry (runs every minute so a Bill-given time like 4:52pm fires within
a minute of it):
  * * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/devdispatch/scheduled_dispatch.py >> /home/billyomes/watson/logs/scheduled_dispatch.log 2>&1
"""
import logging

from jobs.devdispatch.scheduled import fire_due_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    results = fire_due_jobs()
    for r in results:
        log.info("fired scheduled_claude_jobs id=%s -> %s", r["id"], r["result"])


if __name__ == "__main__":
    main()
