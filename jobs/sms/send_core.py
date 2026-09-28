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
      - 1 participant: unchanged 1:1 behavior -- send_mms if there's an
        attachment, else plain send_message.
      - >1 (a group thread): ONE gateway call to send_mms with every
        participant's number in the same `phoneNumbers` list (see
        gateway_client.send_mms's docstring) -- not a loop of separate 1:1
        sends. A plain SMS can't carry more than one recipient in its own
        PDU at all, so even a text-only group reply goes out as an
        (attachment-less) MMS; a loop of individual send_message calls
        delivers each participant their own private copy with no group
        envelope, which is exactly what broke here (confirmed 2026-09-28:
        Bill's reply in a group thread reached one participant as an
        ordinary individual text, not as part of the group).

    Returns (error_body, status_code, None) on failure, or (None, None,
    message_id) on success (message row already committed)."""
    participants = [r["phone"] for r in conn.execute(
        "SELECT phone FROM sms_thread_participants WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()]

    if not participants:
        return {"error": "thread has no participants"}, 502, None

    media_full_path = str(_MEDIA_DIR / media_url.rsplit("/", 1)[-1]) if media_url else None

    if len(participants) > 1:
        result = gateway_client.send_mms(participants, text, media_full_path, media_type)
    elif media_url:
        result = gateway_client.send_mms(participants[0], text, media_full_path, media_type)
    else:
        result = gateway_client.send_message(participants[0], text)

    if not result["success"]:
        return {"error": result.get("error") or "send failed"}, 502, None

    cur = conn.execute(
        """INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id, media_url, media_type, status)
           VALUES (?, 'out', ?, ?, ?, ?, 'sent')""",
        (thread_id, text, result.get("gateway_message_id"), media_url, media_type),
    )
    message_id = cur.lastrowid

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
