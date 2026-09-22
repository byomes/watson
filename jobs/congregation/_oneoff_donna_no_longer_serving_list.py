"""One-off: email Donna the list of people who appear in the 2022/2023
Servant Leader Start Dates snapshots (an xlsx she/Bill provided 2026-09-22)
but have no member record in congregation.db and don't appear on the
current 2025 sheet either -- they read as former servants/volunteers no
longer active, but that's a judgment call for Donna, not Watson, to confirm.

Per Bill's 2026-09-16 rule (any email to Donna Redman goes out Tue/Wed/Thu
9am, never on request), queued for the next such slot rather than sent
immediately. Self-deletes (this file + its own crontab line) after sending,
same pattern as jobs/trading/_oneoff_live_loop_week1_summary.py.

Cron (one-off, fires once):
  0 9 23 9 *  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation._oneoff_donna_no_longer_serving_list \
    >> /home/billyomes/watson/logs/oneoff_donna_no_longer_serving.log 2>&1
"""
import logging
import os
import subprocess

from jobs.email_job.brevo_send import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [oneoff_donna_no_longer_serving] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"

NAMES = [
    ("Bob Pharis", "2018-01-01"),
    ("Buck Buckalew", "2018-09-01"),
    ("Cheryl Buckalew", "2018-09-01"),
    ("Cheryl Mcdonough", "2016-01-01"),
    ("David McDonough", "1986-10-01"),
    ("Dotty Pharis", "2018-01-01"),
    ("George Taylor", "2022-07-01"),
    ("Heather Plocharz", "2018-01-01"),
    ("Keith Redman", "2013-10-01"),
    ("Kim Barnes", "2016-06-01"),
    ("Mary Ruch", "2022-08-01"),
    ("Matthew Ruch", "2021-01-01"),
    ("Melanie Williams", "2018-01-01"),
    ("Natalie Mathena", "2020-09-01"),
    ("Nathan Gore", "2018-03-01"),
    ("Richard Barnes", "2022-06-01"),
    ("Ryan Plocharz", "2018-01-01"),
    ("Tasha Moore", "2014-09-14"),
]


def build_body() -> tuple[str, str]:
    lines = [
        "Hi Donna,",
        "",
        "While importing the Servant Leader Start Dates spreadsheet, I found these "
        "names on the older 2022/2023 tabs with a serving start date on file, but no "
        "member record at all in the current database, and they don't appear on the "
        "2025 tab either. My guess is they've stopped serving or left the church, but "
        "I didn't want to assume -- could you confirm whether any of these should "
        "still be tracked as active servants?",
        "",
    ]
    for name, start in NAMES:
        lines.append(f"- {name} (last known serving start: {start})")
    lines += ["", "Thanks,"]
    text_body = "\n".join(lines)
    html_body = "<br>".join(l if l else "<br>" for l in lines)
    return text_body, html_body


def _self_delete() -> None:
    this_file = os.path.abspath(__file__)
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
        remaining = [
            line for line in result.stdout.splitlines()
            if "_oneoff_donna_no_longer_serving_list" not in line
        ]
        subprocess.run(["crontab", "-"], input="\n".join(remaining) + "\n", text=True, check=True)
        log.info("Removed crontab entry.")
    except Exception as exc:
        log.warning("Could not remove crontab entry: %s", exc)
    try:
        os.remove(this_file)
        log.info("Deleted %s", this_file)
    except Exception as exc:
        log.warning("Could not delete %s: %s", this_file, exc)


def main() -> None:
    text_body, html_body = build_body()
    send_email(
        to_email=DONNA_EMAIL,
        to_name=DONNA_NAME,
        subject="Servant Leader names to double-check",
        text_body=text_body,
        html_body=html_body,
        tags=["congregation_data_correction"],
    )
    log.info(f"Emailed {len(NAMES)} name(s) to Donna.")
    _self_delete()


if __name__ == "__main__":
    main()
