"""One-off: announce serve tracking in Watson to Catalyst staff, with a
link to wtsn.me/cat/serving and a heads-up that Watson will start sending
a Sunday 1pm Telegram reminder (jobs/congregation/serving_reminder.py) to
check off who served.

Scheduled 2026-09-23 9:00am per Bill. Self-deletes its crontab line and
this file after a successful send; on failure it leaves both in place for
a manual re-run.

Cron:
  0 9 23 9 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation._oneoff_staff_serving_announcement \
    >> /home/billyomes/watson/logs/oneoff_staff_serving_announcement.log 2>&1
"""
import logging
import os
import subprocess

from jobs.email_job.brevo_send import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [oneoff_staff_serving_announcement] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SERVING_URL = "https://wtsn.me/cat/serving"

RECIPIENTS = [
    ("Donna Redman", "donna@catalyst302.com"),
    ("Kaci Gravatt", "kaci.gravatt@yahoo.com"),
    ("Lucie Hale", "lucie.hale@gmail.com"),
    ("Melanie Yomes", "melanie.yomes@gmail.com"),
    ("Tara Mathena", "taratem@verizon.net"),
    ("Tyler McCauley", "mcca7812@gmail.com"),
]


def build_body(first_name: str) -> tuple[str, str]:
    lines = [
        f"Hi {first_name},",
        "",
        "Dr. Bill asked me to reach out. We're onboarding serve tracking into the "
        "Watson system, so there's now one place to see who actually served on a "
        "given Sunday for each team.",
        "",
        "You can check it here:",
        SERVING_URL,
        "",
        "Could you take a look and note who served this past Sunday for your team?",
        "",
        "Starting this week, Watson will message you on Telegram every Sunday at "
        "1pm with a link to check off who served that day.",
        "",
        "Thanks,",
    ]
    text_body = "\n".join(lines)
    html_body = "<br>".join(l if l else "<br>" for l in lines)
    return text_body, html_body


def _self_delete_cron(script_name: str) -> None:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
    lines = result.stdout.split("\n")
    out = []
    i = 0
    while i < len(lines):
        if script_name in lines[i]:
            if out and out[-1].strip().startswith("#"):
                out.pop()
            i += 1
            continue
        out.append(lines[i])
        i += 1
    subprocess.run(["crontab", "-"], input="\n".join(out), text=True, check=True)


def main() -> None:
    all_ok = True
    for name, email in RECIPIENTS:
        first_name = name.split()[0]
        text_body, html_body = build_body(first_name)
        result = send_email(
            to_email=email,
            to_name=name,
            subject="New: Serve Tracking in Watson",
            text_body=text_body,
            html_body=html_body,
            tags=["congregation_serving_announcement"],
        )
        if result.get("success"):
            log.info("Sent serving-tracking announcement to %s", name)
        else:
            all_ok = False
            log.error("FAILED to send to %s: %s", name, result.get("error"))

    if not all_ok:
        log.error("At least one send failed, leaving cron/file in place for manual re-run.")
        return

    script_path = os.path.abspath(__file__)
    script_name = os.path.basename(script_path)
    try:
        _self_delete_cron(script_name)
        log.info("Removed crontab entry for %s", script_name)
    except Exception as exc:
        log.warning("Could not remove crontab entry: %s", exc)
    try:
        os.remove(script_path)
        log.info("Removed one-off script file: %s", script_path)
    except Exception as exc:
        log.warning("Could not remove one-off script file: %s", exc)


if __name__ == "__main__":
    main()
