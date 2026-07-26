"""jobs/campaigns/brevo_dispatcher.py — Periodic dispatcher for approved Brevo
sends across every book-launch campaign.

Cron: every 15 minutes (matches jobs/facebook/facebook_post.py's cadence —
see WATSON_ARCHITECTURE.md Active Scheduled Jobs).

Finds book_launch_sends rows with platform='brevo', status='approved',
send_date <= today, and dispatches each via jobs/campaigns/dispatch.py's
send_brevo_row() — the same function the dashboard/Telegram "Approve All"
path uses for already-due rows, so there is exactly one Brevo-sending code
path in this system, not two.
"""
from core.database import get_connection
from jobs.campaigns.dispatch import send_brevo_row


def run() -> int:
    conn = get_connection()
    try:
        due = conn.execute(
            "SELECT * FROM book_launch_sends "
            "WHERE platform='brevo' AND status='approved' AND send_date <= date('now')"
        ).fetchall()

        for row in due:
            send_brevo_row(conn, dict(row))

        return len(due)
    finally:
        conn.close()


if __name__ == "__main__":
    count = run()
    print(f"Dispatched {count} due Brevo send(s).")
