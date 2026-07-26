"""jobs/campaigns/dispatch.py — Shared send-dispatch logic for the book-launch
campaign system. Used by:
  - the dashboard "Approve All" button (jobs/campaigns/campaign_routes.py)
  - the Telegram "Approve All" callback (bot.py's handle_campaign_callback)
  - jobs/campaigns/brevo_dispatcher.py's periodic cron sweep

Facebook rows are handed off to the EXISTING facebook_queue table/cron
(jobs/facebook/facebook_post.py) — this module never posts to Facebook
directly and never touches facebook_post.py.

Brevo rows are sent directly via jobs/email_job/brevo_send.py, one recipient
at a time, with a small delay between sends.
"""
import os
import re
import time

import requests
from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

_SEND_DELAY_SECONDS = 0.2
_LINK_RE = re.compile(r"(https?://\S+|(?<!\S)williamckyomes\.com\S*)")

TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.campaigns.dispatch", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _slugify(title: str) -> str:
    return title.strip().lower().replace(" ", "-")


# ── Recipient resolution ─────────────────────────────────────────────────────

def resolve_recipients(conn, campaign_id: str, segment: str) -> list[dict]:
    """Return [{"email": ..., "name": ...}, ...] for a book_launch_sends
    segment value. 'general' is the Kit snapshot taken at import time;
    'donor' and 'arc' are live queries against their source tables so the
    campaign always sends to whoever is currently active, not a stale copy."""
    if segment == "general":
        rows = conn.execute(
            "SELECT email, name FROM book_launch_contacts WHERE campaign_id=? AND segment='general'",
            (campaign_id,),
        ).fetchall()
        return [{"email": r["email"], "name": r["name"] or ""} for r in rows]

    if segment == "donor":
        import sqlite3
        donors_conn = sqlite3.connect(os.path.expanduser("~/watson/data/donors.db"))
        donors_conn.row_factory = sqlite3.Row
        try:
            rows = donors_conn.execute(
                "SELECT email, name FROM donors WHERE segment != 'lapsed-donor' "
                "AND email IS NOT NULL AND email != ''"
            ).fetchall()
        finally:
            donors_conn.close()
        return [{"email": r["email"], "name": r["name"] or ""} for r in rows]

    if segment == "arc":
        campaign = conn.execute(
            "SELECT book_title FROM book_launch_campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        book_slug = _slugify(campaign["book_title"]) if campaign else None

        import sqlite3
        arc_conn = sqlite3.connect(os.path.expanduser("~/watson/data/watson.db"))
        arc_conn.row_factory = sqlite3.Row
        try:
            if book_slug:
                rows = arc_conn.execute(
                    "SELECT email, first_name, last_name FROM arc_readers "
                    "WHERE status='active' AND book_slug=?",
                    (book_slug,),
                ).fetchall()
            else:
                rows = arc_conn.execute(
                    "SELECT email, first_name, last_name FROM arc_readers WHERE status='active'"
                ).fetchall()
        finally:
            arc_conn.close()
        return [
            {"email": r["email"], "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()}
            for r in rows
        ]

    raise ValueError(f"Unknown segment for recipient resolution: {segment!r}")


# ── Facebook dispatch (queue-only — never posts directly) ──────────────────

def _facebook_ordinal(conn, campaign_id: str, week_number: int, send_id: int) -> int:
    """Post 1 / Post 2 label, derived from insertion order within the week —
    book_launch_sends has no explicit ordinal column."""
    rows = conn.execute(
        "SELECT id FROM book_launch_sends "
        "WHERE campaign_id=? AND week_number=? AND platform='facebook' ORDER BY id",
        (campaign_id, week_number),
    ).fetchall()
    for i, r in enumerate(rows, start=1):
        if r["id"] == send_id:
            return i
    return 1


def dispatch_facebook_row(conn, row) -> None:
    """Insert a book_launch_sends Facebook row into the existing facebook_queue
    table. facebook_post.py's own cron (unchanged) picks it up and fires it —
    it already handles the no-image case (posts to /feed, not /photos)."""
    ordinal = _facebook_ordinal(conn, row["campaign_id"], row["week_number"], row["id"])
    title = f"{row['campaign_id']} Wk{row['week_number']} Post{ordinal}"
    link_m = _LINK_RE.search(row["body_text"] or "")
    url = link_m.group(0) if link_m else None

    conn.execute(
        """INSERT INTO facebook_queue
           (title, summary, url, draft_text, status, scheduled_time, image_path)
           VALUES (?, NULL, ?, ?, 'approved', ?, NULL)""",
        (title, url, row["body_text"], f"{row['send_date']} 00:00:00"),
    )
    conn.commit()


# ── Brevo dispatch (sends directly) ─────────────────────────────────────────

def send_brevo_row(conn, row) -> dict:
    """Send one book_launch_sends Brevo row to every recipient in its segment,
    then mark the row sent. Never raises — one recipient failure doesn't abort
    the batch, and the row itself is only marked 'sent' once the batch is done
    (success or partial failure alike; a total outage still marks it sent so a
    broken segment doesn't retry forever — see the Telegram summary for the
    real per-recipient outcome)."""
    recipients = resolve_recipients(conn, row["campaign_id"], row["segment"])

    succeeded, failed = 0, []
    for recipient in recipients:
        try:
            result = send_email(
                to_email=recipient["email"],
                to_name=recipient["name"],
                subject=row["subject"] or "",
                text_body=row["body_text"],
            )
            if result["success"]:
                succeeded += 1
            else:
                failed.append((recipient["email"], result["error"]))
        except Exception as exc:
            failed.append((recipient["email"], str(exc)))
        time.sleep(_SEND_DELAY_SECONDS)

    conn.execute(
        "UPDATE book_launch_sends SET status='sent', sent_at=datetime('now') WHERE id=?",
        (row["id"],),
    )
    conn.commit()

    label = row["subject"] or (row["body_text"] or "").splitlines()[0][:60]
    summary_lines = [
        f"Sent '{label}' to {len(recipients)} {row['segment']} contacts — "
        f"{succeeded} succeeded, {len(failed)} failed."
    ]
    for email, error in failed[:10]:
        summary_lines.append(f"  ✗ {email}: {error}")
    if len(failed) > 10:
        summary_lines.append(f"  ...and {len(failed) - 10} more failures.")
    _send_telegram("\n".join(summary_lines))

    return {"send_id": row["id"], "recipients": len(recipients), "succeeded": succeeded, "failed": failed}


# ── Approve-all entry point (shared by dashboard + Telegram) ───────────────

def approve_week(campaign_id: str, week_number: int) -> dict:
    """Set status='approved' on every not-yet-approved row for this
    campaign+week, dispatch Facebook rows to facebook_queue immediately, and
    for Brevo rows whose send_date has already arrived, send right now instead
    of waiting for brevo_dispatcher.py's next cron tick."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM book_launch_sends WHERE campaign_id=? AND week_number=? "
            "AND status NOT IN ('approved', 'sent')",
            (campaign_id, week_number),
        ).fetchall()

        approved_count = 0
        dispatched_facebook = 0
        dispatched_brevo = 0
        today = conn.execute("SELECT date('now')").fetchone()[0]

        for row in rows:
            conn.execute("UPDATE book_launch_sends SET status='approved' WHERE id=?", (row["id"],))
            conn.commit()
            row = dict(row)
            row["status"] = "approved"
            approved_count += 1

            if row["platform"] == "facebook":
                dispatch_facebook_row(conn, row)
                dispatched_facebook += 1
            elif row["platform"] == "brevo" and row["send_date"] <= today:
                send_brevo_row(conn, row)
                dispatched_brevo += 1

        return {
            "campaign_id": campaign_id, "week_number": week_number,
            "approved": approved_count, "facebook_queued": dispatched_facebook,
            "brevo_sent_now": dispatched_brevo,
        }
    finally:
        conn.close()
