"""jobs/sms/bridge.py — pulls inbound messages from the gateway (real or
mock) into sms_threads/sms_messages, resolving a contact name against
congregation.db by phone match.

Not yet installed in crontab (no phone hardware as of 2026-09-25) — run
directly (`python -m jobs.sms.bridge`) or call poll_inbound() from
POST /api/sms/mock/inject for testing. Once the phone is live, add a cron
line the same way every other Watson job is scheduled
(PYTHONPATH=/home/billyomes/watson inlined).
"""
import logging
import os
import sqlite3

from core.database import get_connection
from jobs.sms import gateway_client, push
from jobs.sms.carrier_lookup import normalize_phone

log = logging.getLogger(__name__)

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")


def _lookup_member(phone_digits: str) -> tuple[str | None, int | None]:
    """Best-effort match against congregation.db members.phone, normalizing
    both sides the same way. Returns (name, member_id) or (None, None)."""
    try:
        conn = sqlite3.connect(CONGREGATION_DB)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        log.error("_lookup_member: could not open congregation.db: %s", exc)
        return None, None

    try:
        rows = conn.execute("SELECT id, name, phone FROM members WHERE phone IS NOT NULL AND phone != ''").fetchall()
        for row in rows:
            if normalize_phone(row["phone"]) == phone_digits:
                return row["name"], row["id"]
        return None, None
    finally:
        conn.close()


def _get_or_create_thread(conn, phone_digits: str, hint_name: str | None):
    row = conn.execute("SELECT * FROM sms_threads WHERE phone = ?", (phone_digits,)).fetchone()
    if row:
        return row["id"]

    contact_name, member_id = _lookup_member(phone_digits)
    if not contact_name:
        contact_name = hint_name

    cur = conn.execute(
        "INSERT INTO sms_threads (phone, contact_name, member_id) VALUES (?, ?, ?)",
        (phone_digits, contact_name, member_id),
    )
    return cur.lastrowid


def poll_inbound() -> int:
    """Drains the gateway's inbound queue into sms_threads/sms_messages.
    Returns the number of messages ingested."""
    inbound = gateway_client.fetch_inbound()
    if not inbound:
        return 0

    ingested = 0
    conn = get_connection()
    try:
        for msg in inbound:
            phone_digits = normalize_phone(msg.get("phone", ""))
            if not phone_digits:
                log.warning("poll_inbound: skipping message with unparseable phone %r", msg.get("phone"))
                continue

            thread_id = _get_or_create_thread(conn, phone_digits, msg.get("name"))
            text = msg.get("text", "")

            conn.execute(
                "INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id) VALUES (?, 'in', ?, ?)",
                (thread_id, text, msg.get("gateway_message_id")),
            )
            conn.execute(
                """UPDATE sms_threads
                   SET last_message_at = datetime('now'),
                       last_message_preview = ?,
                       unread = 1
                   WHERE id = ?""",
                (text, thread_id),
            )
            ingested += 1

            thread_row = conn.execute(
                "SELECT contact_name, phone FROM sms_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            title = thread_row["contact_name"] or thread_row["phone"]
            body = text if len(text) <= 120 else text[:117] + "..."
            try:
                push.send_push_to_all({
                    "title": title,
                    "body": body,
                    "thread_id": thread_id,
                    "url": f"/sms?thread={thread_id}",
                })
            except Exception as exc:  # noqa: BLE001 — a push failure must never break ingestion
                log.warning("poll_inbound: push notify failed for thread_id=%s: %s", thread_id, exc)

        conn.commit()
    finally:
        conn.close()

    return ingested


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = poll_inbound()
    print(f"poll_inbound: ingested {n} message(s)")
