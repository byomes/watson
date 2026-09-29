"""jobs/sms/broadcast_sender.py -- fires due SMS broadcast recipients
created via POST /api/sms/broadcasts (Watson SMS's group-broadcast
feature).

# * * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.sms.broadcast_sender

Delivery is always individual 1:1 texts, one per sms_broadcast_recipients
row -- never a single group-MMS thread (see jobs/sms/schema.py's broadcast
tables docstring). Each recipient gets or creates their own normal 1:1
thread (jobs.sms.bridge.get_or_create_thread_multi with a single-phone
list) and the send goes through send_core.send_and_record exactly like an
interactive 1:1 send, so it shows up in that person's thread like any
other text from Bill.

Pacing: each recipient row already carries its OWN randomized send_at,
computed once up front at confirm time (jobs/sms/broadcast_pacing.py) --
this job's only pacing job is to also add a small random real-time delay
between any sends that land in the *same* cron tick (SMS_BROADCAST_TICK_*
env vars), so even a same-minute cluster doesn't fire in one instant. A
row is claimed (claimed_at set) via a conditional UPDATE, checked for
rowcount, before it's sent -- guards against double-send if two ticks ever
overlap (plausible now that a tick can legitimately take tens of seconds
because of the pacing sleep, unlike the old fixed-batch design).
"""
import logging
import random
import time

from core.database import get_connection
from jobs.sms import broadcast_pacing, send_core
from jobs.sms.bridge import get_or_create_thread_multi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Safety cap on how many due recipients one cron tick will dispatch, purely
# to bound worst-case tick duration (with the dispatch-delay sleep between
# each) well under the 60s cron cadence -- not a pacing knob itself, the
# per-recipient send_at spacing already handles pacing. Only matters when
# catching up after downtime piles up due recipients in one tick.
_MAX_PER_TICK = 10


def _claim_due_broadcasts(conn) -> None:
    """Flips any 'scheduled' broadcast whose overall send_at (== its first
    recipient's send_at) has arrived to 'sending'. Idempotent: already-
    'sending' broadcasts (this run or a prior one) are untouched."""
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


def _claim_recipient(conn, recipient_id: int) -> bool:
    cur = conn.execute(
        "UPDATE sms_broadcast_recipients SET claimed_at = datetime('now') "
        "WHERE id = ? AND status = 'pending' AND claimed_at IS NULL",
        (recipient_id,),
    )
    conn.commit()
    return cur.rowcount == 1


def _send_one(conn, broadcast_id: int, body: str, r) -> None:
    thread_id = get_or_create_thread_multi(conn, [r["phone"]], {r["phone"]: r["contact_name"]} if r["contact_name"] else None)
    conn.commit()

    error_body, _status, message_id = send_core.send_and_record(conn, thread_id, body, None, None)

    if error_body:
        conn.execute(
            "UPDATE sms_broadcast_recipients SET status = 'failed', thread_id = ?, error = ? WHERE id = ?",
            (thread_id, error_body.get("error") or "send failed", r["id"]),
        )
        conn.execute("UPDATE sms_broadcasts SET failed_count = failed_count + 1 WHERE id = ?", (broadcast_id,))
        log.error("Broadcast id=%s recipient id=%s (%s) failed: %s", broadcast_id, r["id"], r["phone"], error_body.get("error"))
    else:
        conn.execute(
            "UPDATE sms_broadcast_recipients SET status = 'sent', thread_id = ?, gateway_message_id = "
            "(SELECT gateway_message_id FROM sms_messages WHERE id = ?), sent_at = datetime('now') WHERE id = ?",
            (thread_id, message_id, r["id"]),
        )
        conn.execute("UPDATE sms_broadcasts SET sent_count = sent_count + 1 WHERE id = ?", (broadcast_id,))
        log.info("Broadcast id=%s recipient id=%s (%s) sent", broadcast_id, r["id"], r["phone"])

    conn.commit()


def _finalize_if_complete(conn, broadcast_id: int) -> None:
    remaining = conn.execute(
        "SELECT COUNT(*) FROM sms_broadcast_recipients WHERE broadcast_id = ? AND status = 'pending'",
        (broadcast_id,),
    ).fetchone()[0]
    if remaining:
        return

    counts = conn.execute("SELECT failed_count, recipient_count FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
    status = "failed" if counts["failed_count"] and counts["failed_count"] == counts["recipient_count"] else "sent"
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

        in_progress = {row["id"] for row in conn.execute("SELECT id FROM sms_broadcasts WHERE status = 'sending'").fetchall()}
        if not in_progress:
            return

        due = conn.execute(
            "SELECT * FROM sms_broadcast_recipients "
            "WHERE status = 'pending' AND claimed_at IS NULL AND send_at IS NOT NULL AND send_at <= datetime('now') "
            "ORDER BY send_at LIMIT ?",
            (_MAX_PER_TICK,),
        ).fetchall()

        delay_lo, delay_hi = broadcast_pacing.tick_dispatch_delay_range()
        touched_broadcasts: set[int] = set()

        for r in due:
            if r["broadcast_id"] not in in_progress:
                continue
            if not _claim_recipient(conn, r["id"]):
                continue  # another tick already grabbed this row

            broadcast = conn.execute("SELECT body FROM sms_broadcasts WHERE id = ?", (r["broadcast_id"],)).fetchone()
            _send_one(conn, r["broadcast_id"], broadcast["body"], r)
            touched_broadcasts.add(r["broadcast_id"])
            time.sleep(random.uniform(delay_lo, delay_hi))

        for broadcast_id in touched_broadcasts:
            _finalize_if_complete(conn, broadcast_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
