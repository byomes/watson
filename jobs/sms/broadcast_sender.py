"""jobs/sms/broadcast_sender.py -- fires due SMS broadcasts created via
POST /api/sms/broadcasts (Watson SMS's group-broadcast feature).

# * * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.sms.broadcast_sender

Delivery is always individual 1:1 texts, one per sms_broadcast_recipients
row -- never a single group-MMS thread (see jobs/sms/schema.py's broadcast
tables docstring). Each recipient gets or creates their own normal 1:1
thread (jobs.sms.bridge.get_or_create_thread_multi with a single-phone
list) and the send goes through send_core.send_and_record exactly like an
interactive 1:1 send, so it shows up in that person's thread like any other
text from Bill.

Processes at most _BATCH_SIZE pending recipients per run (not the whole
broadcast at once) with a short delay between sends, so a large broadcast
(e.g. "Everyone") spreads across several cron ticks instead of one run
blocking the minutely schedule -- resumable by construction: a broadcast
left with 'pending' rows just picks up again next tick, whether this is
its first run or recovering from a mid-send crash.
"""
import logging
import time

from core.database import get_connection
from jobs.sms import send_core
from jobs.sms.bridge import get_or_create_thread_multi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_BATCH_SIZE = 25
_SEND_DELAY_SECONDS = 0.5


def _claim_due_broadcasts(conn) -> None:
    """Flips any 'scheduled' broadcast whose send_at has arrived to
    'sending' -- the claim that keeps a later cron tick from treating it as
    not-yet-started. Idempotent: already-'sending' broadcasts (this run or a
    prior one that didn't finish) are untouched."""
    due = conn.execute(
        "SELECT id FROM sms_broadcasts WHERE status = 'scheduled' AND send_at <= datetime('now')"
    ).fetchall()
    for row in due:
        conn.execute(
            "UPDATE sms_broadcasts SET status = 'sending', started_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
    if due:
        conn.commit()


def _process_broadcast(conn, broadcast_id: int) -> int:
    """Sends up to _BATCH_SIZE pending recipients for one broadcast. Returns
    how many it processed (0 means this broadcast had nothing left to do,
    so the caller can move on and/or mark it complete)."""
    pending = conn.execute(
        "SELECT * FROM sms_broadcast_recipients WHERE broadcast_id = ? AND status = 'pending' LIMIT ?",
        (broadcast_id, _BATCH_SIZE),
    ).fetchall()

    for r in pending:
        thread_id = get_or_create_thread_multi(conn, [r["phone"]], {r["phone"]: r["contact_name"]} if r["contact_name"] else None)
        conn.commit()

        broadcast = conn.execute("SELECT body FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        error_body, _status, message_id = send_core.send_and_record(conn, thread_id, broadcast["body"], None, None)

        if error_body:
            conn.execute(
                "UPDATE sms_broadcast_recipients SET status = 'failed', thread_id = ?, error = ? WHERE id = ?",
                (thread_id, error_body.get("error") or "send failed", r["id"]),
            )
            conn.execute(
                "UPDATE sms_broadcasts SET failed_count = failed_count + 1 WHERE id = ?", (broadcast_id,)
            )
            log.error("Broadcast id=%s recipient id=%s (%s) failed: %s", broadcast_id, r["id"], r["phone"], error_body.get("error"))
        else:
            conn.execute(
                "UPDATE sms_broadcast_recipients SET status = 'sent', thread_id = ?, gateway_message_id = "
                "(SELECT gateway_message_id FROM sms_messages WHERE id = ?), sent_at = datetime('now') WHERE id = ?",
                (thread_id, message_id, r["id"]),
            )
            conn.execute(
                "UPDATE sms_broadcasts SET sent_count = sent_count + 1 WHERE id = ?", (broadcast_id,)
            )
            log.info("Broadcast id=%s recipient id=%s (%s) sent", broadcast_id, r["id"], r["phone"])

        conn.commit()
        time.sleep(_SEND_DELAY_SECONDS)

    return len(pending)


def _finalize_if_complete(conn, broadcast_id: int) -> None:
    remaining = conn.execute(
        "SELECT COUNT(*) FROM sms_broadcast_recipients WHERE broadcast_id = ? AND status = 'pending'",
        (broadcast_id,),
    ).fetchone()[0]
    if remaining:
        return

    failed = conn.execute("SELECT failed_count, recipient_count FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
    status = "failed" if failed["failed_count"] and failed["failed_count"] == failed["recipient_count"] else "sent"
    conn.execute(
        "UPDATE sms_broadcasts SET status = ?, completed_at = datetime('now') WHERE id = ?",
        (status, broadcast_id),
    )
    conn.commit()
    log.info("Broadcast id=%s complete: %s", broadcast_id, status)


def main():
    conn = get_connection()
    try:
        _claim_due_broadcasts(conn)

        in_progress = conn.execute("SELECT id FROM sms_broadcasts WHERE status = 'sending'").fetchall()
        for row in in_progress:
            _process_broadcast(conn, row["id"])
            _finalize_if_complete(conn, row["id"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
