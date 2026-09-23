"""One-off: ask Donna Redman to email Watson a full list of Catalyst's
leaders and their titles, so leadership_roles can be filled out beyond the
elders/deacons/staff already tagged (per Bill 2026-09-22 — Catalyst has
other leaders not yet in Watson's brain).

Scheduled 2026-09-23 9:00am per Bill's standing rule that any email to
Donna goes out Tue/Wed/Thu at 9am. Self-deletes its crontab line and this
file after a successful send; on failure it leaves both in place so the
next 9am tick (there isn't one, since this fires once) doesn't apply, so a
failed send just needs a manual re-run.

Cron:
  0 9 23 9 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation._oneoff_donna_catalyst_leaders_request \
    >> /home/billyomes/watson/logs/oneoff_donna_catalyst_leaders_request.log 2>&1
"""
import logging
import os
import subprocess

from jobs.email_job.brevo_send import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [oneoff_donna_catalyst_leaders_request] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"
WATSON_INBOX = "watson.wcky@gmail.com"


def build_body() -> tuple[str, str]:
    lines = [
        "Hi Donna,",
        "",
        "Dr. Bill asked me to reach out. Watson is building out a full picture of "
        "Catalyst's leadership team, and we want to make sure nobody's missing.",
        "",
        f"Could you email a full list of Catalyst's leaders and their titles to "
        f"{WATSON_INBOX}? Whatever format is easiest, a plain list is fine.",
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
    text_body, html_body = build_body()
    result = send_email(
        to_email=DONNA_EMAIL,
        to_name=DONNA_NAME,
        subject="Quick ask: full Catalyst leaders list",
        text_body=text_body,
        html_body=html_body,
        tags=["congregation_leadership_request"],
    )
    if not result.get("success"):
        log.error("Send failed, leaving cron/file in place for manual re-run: %s", result.get("error"))
        return

    log.info("Sent Catalyst leaders-list request to Donna.")

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
