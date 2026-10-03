"""One-off: Telegrams Donna the /cat/kidstoday override link at noon on
2026-10-04 (Sunday), per Bill's direct instruction, so she has time to set
a servant override before the 2:57pm kidsatt_weekly "who's serving" send.

Guarded by a marker file in addition to the crontab self-delete (see
feedback_oneoff_cron_selfdelete_bug memory -- a prior one-off's self-delete
matched on the bare filename instead of the actual `-m module.path` string
in the crontab line and never actually removed it).

Cron (fires once, 2026-10-04 noon):
  0 12 4 10 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.oneoff_2026_10_04_kidstoday_donna_telegram \
    >> /home/billyomes/watson/logs/oneoff_kidstoday_donna_telegram.log 2>&1
"""
from pathlib import Path

from jobs.telegram.donna_notify import send_to_donna
from jobs.utilities.oneoff_cron import self_delete

MARKER_PATH = Path.home() / "watson" / "data" / ".oneoff_2026_10_04_kidstoday_donna_telegram_sent"

MESSAGE = (
    "Today's kids servants are the defaults (Tara for Nursery/Pre-K, Lucie for Elementary) "
    "unless you set an override before 2:57pm:\nhttps://wtsn.me/cat/kidstoday"
)


def run() -> bool:
    if MARKER_PATH.exists():
        print("Already sent, skipping (marker present).")
        return False

    ok = send_to_donna(MESSAGE)
    if ok:
        MARKER_PATH.touch()
        self_delete(__file__)
        print("Sent.")
    else:
        print("Send failed.")
    return ok


if __name__ == "__main__":
    run()
