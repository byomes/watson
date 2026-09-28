"""One-off: email Bill the corrected list of 2026 first-time guests.

Built 2026-09-27 -- Bill asked Watson for this list earlier and got it
wrong (whatever answered that used members.first_visit_date, the column
found corrupted the same day: it disagrees with a member's real earliest
attendance row for 85/208 members who have it set -- see bug_tracker #204
and jobs/analytics/conversion_report.py's docstring). This script derives
the list the same corrected way that fix uses: MIN(attendance.service_date)
per member, filtered to calendar year 2026.

Queued for 9am the next morning rather than sent immediately per Bill's
standing after-8pm rule (see memory/feedback_email_send_timing.md) --
self-deletes its own crontab line and this file after a successful send,
using the shared jobs.utilities.oneoff_cron helper (the old inline
self-delete pattern had a bug where it never actually matched/removed the
crontab line -- see memory/feedback_oneoff_cron_selfdelete_bug.md).

Scheduled (added 2026-09-27, fires once 2026-09-28 09:00 then self-deletes):
  0 9 28 9 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation._oneoff_2026_first_time_guests_email \
    >> /home/billyomes/watson/logs/oneoff_2026_first_time_guests_email.log 2>&1
"""

import logging
import os
import sqlite3

from jobs.email_job.brevo_send import send_email
from jobs.utilities.oneoff_cron import self_delete

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
BILL_EMAIL = "pastorbill@catalyst302.com"


def _first_time_guests_2026(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT m.name AS name,
               MIN(a.service_date) AS first_visit,
               (SELECT campus FROM attendance a2
                WHERE a2.member_id = m.id ORDER BY service_date LIMIT 1) AS campus
        FROM members m
        JOIN attendance a ON a.member_id = m.id
        GROUP BY m.id
        HAVING first_visit >= '2026-01-01' AND first_visit <= '2026-12-31'
        ORDER BY first_visit
        """
    ).fetchall()


def _build_body(rows: list[sqlite3.Row]) -> tuple[str, str]:
    lines = [f"{r['name']} - first visit {r['first_visit']} ({r['campus']})" for r in rows]
    text = (
        f"2026 first-time guests: {len(rows)}\n\n" + "\n".join(lines) + "\n\n"
        "This corrects an earlier answer that used a member data column "
        "(first_visit_date) found to be unreliable -- this list is derived "
        "directly from each person's earliest attendance record instead."
    )
    html_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['first_visit']}</td><td>{r['campus']}</td></tr>"
        for r in rows
    )
    html = (
        f"<p>2026 first-time guests: {len(rows)}</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<tr><th>Name</th><th>First visit</th><th>Campus</th></tr>"
        f"{html_rows}"
        "</table>"
        "<p>This corrects an earlier answer that used a member data column "
        "(first_visit_date) found to be unreliable. This list is derived "
        "directly from each person's earliest attendance record instead.</p>"
    )
    return text, html


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = _first_time_guests_2026(conn)
    finally:
        conn.close()

    text_body, html_body = _build_body(rows)
    result = send_email(
        to_email=BILL_EMAIL,
        to_name="Dr. Bill Yomes",
        subject="2026 First-Time Guests List (corrected)",
        text_body=text_body,
        html_body=html_body,
        tags=["congregation", "oneoff"],
    )

    if result["success"]:
        log.info("Sent 2026 first-time guests list (%d people) to %s", len(rows), BILL_EMAIL)
        self_delete(__file__)
    else:
        log.error("Send failed, leaving crontab entry + file in place for retry: %s", result["error"])


if __name__ == "__main__":
    main()
