"""jobs/comms/facebook_dispatch.py — Periodic dispatcher for approved Comms
Desk Facebook posts.

Cron: every 15 minutes (matches jobs/facebook/facebook_post.py and
jobs/campaigns/brevo_dispatcher.py's cadence).

Mirrors brevo_dispatcher.py's role, but for Facebook: facebook_post.py's own
cron only ever reads the facebook_queue table, and nothing previously moved
an approved Comms Desk Facebook row into that queue — book_launch_sends rows
from jobs/comms/api.py's mark_ready() just sat at status='approved' forever
with no dispatcher watching them (an existing gap that predates send_time;
comms_desk had never actually posted a Facebook post before this was added).

Finds due, approved, comms_desk Facebook rows and hands each to
jobs/campaigns/dispatch.py's dispatch_facebook_row() — the same function the
book-launch "Approve All" path uses — then marks the row 'sent' so it isn't
picked up again (dispatch_facebook_row() itself never touches
book_launch_sends.status; see its "known gap" note in dispatch.py).

Gating (send_date/send_time due-ness) happens here, before queuing, rather
than relying on facebook_queue.scheduled_time alone: jobs/comms/api.py's
cancel_send() can only stop a still-'approved' row, so a row must not be
handed to facebook_queue before it's actually due.
"""
from core.database import get_connection
from jobs.campaigns.dispatch import dispatch_facebook_row


def run() -> int:
    conn = get_connection()
    try:
        due = conn.execute(
            "SELECT * FROM book_launch_sends "
            "WHERE source='comms_desk' AND platform='facebook' AND status='approved' "
            "AND (send_date < date('now','localtime') "
            "     OR (send_date = date('now','localtime') "
            "         AND (send_time IS NULL OR send_time <= time('now','localtime'))))"
        ).fetchall()

        for row in due:
            row = dict(row)
            dispatch_facebook_row(conn, row, dry_run=False)
            conn.execute(
                "UPDATE book_launch_sends SET status='sent', sent_at=datetime('now') WHERE id=?",
                (row["id"],),
            )
            conn.commit()

        return len(due)
    finally:
        conn.close()


if __name__ == "__main__":
    count = run()
    print(f"Queued {count} due Comms Desk Facebook post(s).")
