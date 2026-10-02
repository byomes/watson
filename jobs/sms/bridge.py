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
from jobs.sms import autoresponder, gateway_client, push, settings as sms_settings
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
    thread_id = cur.lastrowid
    # Every thread -- 1:1 or group -- gets a sms_thread_participants row, so
    # api.py's _send_and_record can be one code path instead of an is_group
    # fork. 1:1 threads created here (outbound /send, /schedule,
    # send-to-self) never go through adb_inbound.py, so this insert is the
    # only place they'd otherwise be missed.
    conn.execute(
        "INSERT OR IGNORE INTO sms_thread_participants (thread_id, phone, contact_name, member_id) VALUES (?, ?, ?, ?)",
        (thread_id, phone_digits, contact_name, member_id),
    )
    return thread_id


def existing_thread_participant_sets(conn) -> dict[int, frozenset]:
    """thread_id -> frozenset(phones) for every thread. Shared by
    get_or_create_thread_multi below and jobs/sms/adb_inbound.py, which
    matches inbound group messages against this same map -- so a group
    thread started from the compose window (outbound) correctly merges
    with that same group's future inbound replies, and vice versa."""
    rows = conn.execute("SELECT thread_id, phone FROM sms_thread_participants").fetchall()
    by_thread: dict[int, set] = {}
    for r in rows:
        by_thread.setdefault(r["thread_id"], set()).add(r["phone"])
    return {tid: frozenset(phones) for tid, phones in by_thread.items()}


def get_or_create_thread_multi(conn, phones: list[str], hint_names: dict[str, str] | None = None,
                                android_thread_id: str | None = None) -> int:
    """Like _get_or_create_thread, but for any number of participants --
    the single-phone case is just len(phones) == 1. A thread's identity is
    its participant SET (matched via existing_thread_participant_sets),
    not which one number happened to start it, so this is the one path
    both inbound group ingestion (adb_inbound.py) and the compose window's
    "start a group text" flow share -- a group created either way merges
    with the same group's messages arriving the other way."""
    hint_names = hint_names or {}
    key = frozenset(phones)

    participant_sets = existing_thread_participant_sets(conn)
    for thread_id, existing in participant_sets.items():
        if existing == key:
            if android_thread_id:
                conn.execute("UPDATE sms_threads SET android_thread_id = ? WHERE id = ?", (android_thread_id, thread_id))
            return thread_id

    is_group = len(phones) > 1
    if is_group:
        phone = f"group:{android_thread_id or '-'.join(sorted(phones))}"
        contact_name = None
        member_id = None
    else:
        phone = phones[0]
        contact_name, member_id = _lookup_member(phone)
        contact_name = contact_name or hint_names.get(phone)

    cur = conn.execute(
        "INSERT INTO sms_threads (phone, contact_name, member_id, is_group, android_thread_id) VALUES (?, ?, ?, ?, ?)",
        (phone, contact_name, member_id, 1 if is_group else 0, android_thread_id),
    )
    thread_id = cur.lastrowid

    for p in phones:
        name, member_id = _lookup_member(p)
        name = name or hint_names.get(p)
        conn.execute(
            "INSERT OR IGNORE INTO sms_thread_participants (thread_id, phone, contact_name, member_id) VALUES (?, ?, ?, ?)",
            (thread_id, p, name, member_id),
        )

    return thread_id


def poll_inbound() -> int:
    """Drains the gateway's inbound queue into sms_threads/sms_messages.
    Returns the number of messages ingested.

    SMS_INBOUND_MODE=adb dispatches to jobs/sms/adb_inbound.py instead,
    which reads the gateway phone's Telephony content provider directly via
    adb -- fixes group-text splintering, which capcom6's flat REST /inbox
    schema (the body below) has no way to represent. Default stays
    'gateway' (this function's own REST-based body, untouched) as a
    one-env-var rollback lever if the adb path ever misbehaves in
    production -- zero code deleted from the old path."""
    if os.getenv("SMS_INBOUND_MODE", "gateway").strip().lower() == "adb":
        from jobs.sms.adb_inbound import poll_inbound_adb
        return poll_inbound_adb()

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

            gateway_message_id = msg.get("gateway_message_id")
            if gateway_message_id:
                existing = conn.execute(
                    "SELECT 1 FROM sms_messages WHERE gateway_message_id = ?", (gateway_message_id,)
                ).fetchone()
                if existing:
                    # Already ingested -- can happen if the poll window is
                    # ever rewound (e.g. to backfill a message a prior bug
                    # dropped) and re-covers an already-handled message.
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
                       unread = 1,
                       snoozed_until = NULL,
                       state = 'open'
                   WHERE id = ?""",
                (text, thread_id),
            )
            ingested += 1

            thread_row = conn.execute(
                "SELECT contact_name, phone, muted FROM sms_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            # A reply is exactly the kind of thing a snooze shouldn't hide --
            # clearing it above brings the thread back to the top of the list.
            if thread_row["muted"]:
                continue
            # Vacation mode / Friday Sabbath: message still lands in the
            # thread normally (unread, in the list) -- only the push
            # notification and badge bump are suppressed. This is also
            # exactly the condition an autoresponder (if Bill has one
            # enabled for the active mode) should fire under.
            if sms_settings.should_silence_notifications():
                autoresponder.maybe_autorespond(conn, thread_id, phone_digits, is_group=False)
                continue
            title = thread_row["contact_name"] or thread_row["phone"]
            body = text if len(text) <= 120 else text[:117] + "..."
            unread_count = conn.execute(
                "SELECT COUNT(*) FROM sms_threads WHERE unread = 1 AND state = 'open'"
            ).fetchone()[0]
            try:
                push.send_push_to_all({
                    "title": title,
                    "body": body,
                    "thread_id": thread_id,
                    "url": f"/sms?thread={thread_id}",
                    "unread_count": unread_count,
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
