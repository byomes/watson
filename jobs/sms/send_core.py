"""jobs/sms/send_core.py — shared outbound send-and-record logic, used by
both jobs/sms/api.py (interactive sends) and jobs/sms/scheduled_sender.py
(cron-fired scheduled sends). Extracted out of api.py during the group-text
fix so a scheduled send into a group thread also fans out to every
participant, instead of scheduled_sender.py duplicating (and diverging
from) the fan-out logic a second time.
"""
from pathlib import Path

from jobs.sms import gateway_client

_MEDIA_DIR = Path(__file__).resolve().parents[2] / "data" / "sms_media"


def send_and_record(conn, thread_id: int, text: str, media_url: str | None, media_type: str | None):
    """Looks up sms_thread_participants itself rather than taking a phone
    param -- every thread has at least one participant row (see
    jobs/sms/schema.py's one-time backfill and bridge.py's
    _get_or_create_thread), so this is never an "empty participants"
    fallback, just one real code path for both cases:
      - 1 participant: one gateway call, unchanged behavior for 1:1 threads.
      - >1 (a group thread): one gateway call per participant, one
        sms_messages row for the single bubble Bill sees
        (gateway_message_id left NULL -- ambiguous across recipients), one
        sms_message_recipients row per participant with their own id/status.
        Thread-level status is a conservative rollup: 'failed' if any
        recipient failed, else 'sent'. Per-recipient failure detail is
        captured in sms_message_recipients but not surfaced in the UI yet
        (deliberately deferred, see plan).

    Returns (error_body, status_code, None) on failure, or (None, None,
    message_id) on success (message row already committed)."""
    participants = [r["phone"] for r in conn.execute(
        "SELECT phone FROM sms_thread_participants WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()]

    def _send_one(phone: str) -> dict:
        if media_url:
            return gateway_client.send_mms(phone, text, str(_MEDIA_DIR / media_url.rsplit("/", 1)[-1]), media_type)
        return gateway_client.send_message(phone, text)

    if len(participants) <= 1:
        phone = participants[0] if participants else None
        result = _send_one(phone) if phone else {"success": False, "gateway_message_id": None, "error": "thread has no participants"}
        if not result["success"]:
            return {"error": result.get("error") or "send failed"}, 502, None
        status = "sent"
        gateway_message_id = result.get("gateway_message_id")
    else:
        results = {phone: _send_one(phone) for phone in participants}
        if not any(r["success"] for r in results.values()):
            return {"error": "send failed for every participant"}, 502, None
        status = "failed" if any(not r["success"] for r in results.values()) else "sent"
        gateway_message_id = None  # ambiguous across recipients

    cur = conn.execute(
        """INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id, media_url, media_type, status)
           VALUES (?, 'out', ?, ?, ?, ?, ?)""",
        (thread_id, text, gateway_message_id, media_url, media_type, status),
    )
    message_id = cur.lastrowid

    if len(participants) > 1:
        conn.executemany(
            "INSERT INTO sms_message_recipients (message_id, phone, gateway_message_id, status) VALUES (?, ?, ?, ?)",
            [
                (message_id, phone, r.get("gateway_message_id"), "sent" if r["success"] else "failed")
                for phone, r in results.items()
            ],
        )

    preview = text or "📷 Photo"
    conn.execute(
        """UPDATE sms_threads
           SET last_message_at = datetime('now'),
               last_message_preview = ?,
               unread = 0
           WHERE id = ?""",
        (preview, thread_id),
    )
    conn.commit()
    return None, None, message_id
