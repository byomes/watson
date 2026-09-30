"""One-off: emails Donna a summary of the Kids Checkin attendance tracking
work done 2026-09-29 (build + full-history backlog + dedup + CatalystDB
sync). Per Bill's request the same night, scheduled for the next
Tue/Wed/Thu 9am slot (Wed 2026-09-30), matching the standing Donna-email
cadence (see feedback_donna_email_schedule memory) rather than sending
immediately.

Guarded by a marker file, not just the crontab self-delete below, since a
prior one-off's self-delete matched on the bare filename instead of the
actual `-m module.path` string in the crontab line and never actually
removed it (see feedback_oneoff_cron_selfdelete_bug memory) -- if that
happens again here, this at least stays a harmless no-op next time cron
fires it rather than sending the summary twice.

Cron (fires once, 2026-09-30 9:00am):
  0 9 30 9 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    -m jobs.congregation.oneoff_2026_09_30_donna_kids_checkin_summary_email \
    >> /home/billyomes/watson/logs/oneoff_donna_kids_checkin_summary.log 2>&1
"""
import subprocess
from pathlib import Path

from jobs.email_job.brevo_send import send_email

MARKER_PATH = Path.home() / "watson" / "data" / ".oneoff_donna_kids_checkin_summary_sent"
DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"

SUBJECT = "Kids Checkin attendance now tracked in the database"

BODY = """Hi Donna,

Quick rundown of something Watson finished building last night: kids attendance tracking, alongside adult attendance.

Kids checked into classes on Sundays through Subsplash never had a record in the database, since they don't fill out a connect card. Watson pulled the full checkin history, January 2025 through this past Sunday, and now tracks it.

What happened:

- 88 Sundays of history were pulled, covering 375 check-ins.
- Subsplash had created duplicate profiles for a handful of kids (one had 5 separate profiles for the same child). Those got merged into one record per kid, so the final count is 64 kids.
- For each kid, Watson tries to match the checking-in parent's phone or email to an existing family on file. It never links a kid to a household automatically, that's always your call, sent as a Telegram message with a button to approve or reject.
- The first big batch, 77 messages, went to Bill instead of you, just for this initial backlog so it wouldn't flood your Telegram all at once. About 10 of those were skipped and will land in your own Telegram this morning, along with anything new going forward.
- 19 kids had no phone or email match at all, so there was nothing for a button to offer. Those need a manual lookup when you have a chance.
- Once a kid is linked to a household, they now show up correctly under that family in CatalystDB.

Going forward this runs on its own: a new pull happens every Monday, and any new kids needing a household review land in your Telegram at 9am.

Let me know if anything looks off.
"""


def _self_delete_cron_line() -> None:
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    except Exception:
        return
    marker = "jobs.congregation.oneoff_2026_09_30_donna_kids_checkin_summary_email"
    remaining = "\n".join(line for line in current.splitlines() if marker not in line)
    subprocess.run(["crontab", "-"], input=remaining, text=True)


def run() -> bool:
    if MARKER_PATH.exists():
        print("Already sent, skipping (marker present).")
        return False

    result = send_email(DONNA_EMAIL, DONNA_NAME, SUBJECT, BODY, tags=["kids_checkin", "one_off"])
    if result["success"]:
        MARKER_PATH.touch()
        _self_delete_cron_line()
        print("Sent.")
    else:
        print(f"Send failed: {result['error']}")
    return result["success"]


if __name__ == "__main__":
    run()
