"""jobs/comms/release_holds.py — releases "send now" holds once their
12-minute undo window has elapsed.

Cron: every 2-3 minutes.

Flips the linked book_launch_sends row from 'scheduled' to 'approved' once
held_until has passed; the next 15-min facebook_post.py / brevo_dispatcher.py
sweep sends it normally, using the same dispatch code as every other
book-launch send. Holds canceled during the window (canceled_at set by
jobs/comms/api.py) are skipped and left released_at=NULL.
"""
from jobs.comms import get_db


def run() -> int:
    conn = get_db()
    try:
        due = conn.execute(
            "SELECT * FROM comms_holds "
            "WHERE released_at IS NULL AND canceled_at IS NULL AND held_until <= datetime('now')"
        ).fetchall()

        for hold in due:
            conn.execute(
                "UPDATE book_launch_sends SET status='approved' WHERE id=?",
                (hold["send_id"],),
            )
            conn.execute(
                "UPDATE comms_holds SET released_at = datetime('now') WHERE id=?",
                (hold["id"],),
            )
        conn.commit()
        return len(due)
    finally:
        conn.close()


if __name__ == "__main__":
    count = run()
    print(f"Released {count} comms hold(s).")
