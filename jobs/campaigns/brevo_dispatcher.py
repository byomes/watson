"""jobs/campaigns/brevo_dispatcher.py — Periodic dispatcher for approved Brevo
sends across every book-launch campaign.

Cron: every 15 minutes (matches jobs/facebook/facebook_post.py's cadence —
see WATSON_ARCHITECTURE.md Active Scheduled Jobs).

Finds book_launch_sends rows with platform='brevo', status='approved',
send_date/send_time due, and dispatches each via jobs/campaigns/dispatch.py's
send_brevo_row() — the same function the dashboard/Telegram "Approve All"
path uses for already-due rows, so there is exactly one Brevo-sending code
path in this system, not two.

Comms Desk rows (source='comms_desk') also need admin_approved_at set —
excluded from this query's WHERE clause purely so this sweep doesn't keep
re-polling rows that are still pending that approval; send_brevo_row() itself
independently re-checks this regardless (see its docstring), so this filter
is an efficiency/log-noise optimization, not the actual safety gate.

send_time (added for Comms Desk's date+time picker) is nullable local
'HH:MM' — NULL preserves the original date-only behavior (due as soon as
send_date arrives) for every pre-existing/non-Comms-Desk row. Comparisons use
localtime, not SQLite's default UTC 'now', so a Comms Desk-picked time fires
against the same wall clock Kaci picked it on.
"""
from core.database import get_connection
from jobs.campaigns.dispatch import send_brevo_row


def run() -> int:
    conn = get_connection()
    try:
        due = conn.execute(
            "SELECT * FROM book_launch_sends "
            "WHERE platform='brevo' AND status='approved' "
            "AND (source != 'comms_desk' OR admin_approved_at IS NOT NULL) "
            "AND (send_date < date('now','localtime') "
            "     OR (send_date = date('now','localtime') "
            "         AND (send_time IS NULL OR send_time <= time('now','localtime'))))"
        ).fetchall()

        for row in due:
            # Rows only reach 'approved' via a real, intentional approval
            # (dashboard or Telegram, both pass dry_run=False explicitly) —
            # dry_run=False here too, for the same reason. send_brevo_row()
            # independently re-verifies the campaign is real+active regardless.
            send_brevo_row(conn, dict(row), dry_run=False)

        return len(due)
    finally:
        conn.close()


if __name__ == "__main__":
    count = run()
    print(f"Dispatched {count} due Brevo send(s).")
