"""jobs/sms/adb_inbound.py — inbound SMS/MMS ingestion via adb, reading
directly from the gateway phone's Telephony content provider instead of
capcom6's REST /inbox (jobs/sms/gateway_client.py). Built to fix group-text
splintering: capcom6's flat REST schema has no participant/thread concept at
all, so a group text's messages each land in their own 1:1 Watson thread.

Group-thread grouping is NOT based on Android's own content://mms-sms
thread_id/recipient_ids -- live-tested against two real inbound group-text
messages and confirmed Android's own thread merging is gated by a separate
"Group messaging" toggle, unrelated to which app holds the default-SMS role.
Instead, this reads each MMS's own address table (content://mms/<id>/addr)
directly: type=137 is the FROM address, type=130 is CC (the other group
participants), type=151 is TO (always just the gateway's own number, never a
participant) -- confirmed correct against real data even when Android's own
conversation view didn't merge the same messages. A thread's identity is the
canonical (sorted, normalized) participant set, not Android's thread_id --
android_thread_id is still recorded on the row as a courtesy/debugging aid,
not as the matching key.

No mock fixture exists for this path (there's no phone to fake) -- run with
dry_run=True to see what would be ingested without writing to watson.db.

Toggled on via SMS_INBOUND_MODE=adb (see jobs/sms/bridge.py's poll_inbound
dispatcher) -- GATEWAY_MODE=live keeps using gateway_client for OUTBOUND
sends either way, this module only replaces inbound.
"""
import json
import logging
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.database import get_connection
from jobs.sms import adb_client, autoresponder, push, settings as sms_settings
from jobs.sms.bridge import get_or_create_thread_multi
from jobs.sms.carrier_lookup import normalize_phone

log = logging.getLogger(__name__)

_CURSOR_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_adb_last_id.json"

# Same directory jobs/sms/api.py's _save_media()/get_media() use for
# outbound-attached images -- writing here directly (not through that
# function, which expects a base64 upload body) means inbound images are
# served by the exact same already-built /api/sms/media/<filename> route,
# no frontend or routing change needed.
_MEDIA_DIR = Path(__file__).resolve().parents[2] / "data" / "sms_media"

_ADDR_TYPE_FROM = "137"
_ADDR_TYPE_CC = "130"


def _read_cursor() -> dict:
    if not _CURSOR_PATH.exists():
        return {"last_sms_id": 0, "last_mms_id": 0}
    try:
        data = json.loads(_CURSOR_PATH.read_text())
        return {"last_sms_id": int(data.get("last_sms_id", 0)), "last_mms_id": int(data.get("last_mms_id", 0))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {"last_sms_id": 0, "last_mms_id": 0}


def _write_cursor(cursor: dict) -> None:
    _CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CURSOR_PATH.write_text(json.dumps(cursor))


def _save_mms_image(device: str, part_id: str, content_type: str) -> str | None:
    """Pulls one MMS part's raw image bytes via `adb shell content read`
    (the part row has no text column for binary data) and writes it into
    the same data/sms_media/ directory jobs/sms/api.py serves outbound
    attachments from. Returns the /api/sms/media/<filename> URL, or None if
    the read failed or came back empty -- a failed image pull degrades to
    no-image, never breaks ingestion of the rest of the message."""
    raw = adb_client.read_binary(device, f"content://mms/part/{part_id}")
    if not raw:
        return None
    ext = mimetypes.guess_extension(content_type) or ""
    filename = f"{uuid.uuid4().hex}{ext}"
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (_MEDIA_DIR / filename).write_bytes(raw)
    return f"/api/sms/media/{filename}"


def _mms_body(device: str, mms_id: str) -> tuple[str, str | None]:
    """Returns (body, media_url). A text/plain part's `text` column holds
    the literal body -- confirmed against real data, no binary stream read
    needed. An image part (ct starting "image/") gets its actual bytes
    pulled and saved via _save_mms_image() so it can render inline, same as
    an outbound-attached image already does; a caption text/plain part
    alongside it is still used as the body. No text and no image part
    (e.g. vCard/audio) -> a stub body, no attachment preview."""
    parts = adb_client.query(device, f"content://mms/{mms_id}/part")
    body = ""
    media_url = None
    for p in parts:
        ct = p.get("ct") or ""
        if ct == "text/plain" and p.get("text"):
            body = p["text"]
        elif ct.startswith("image/") and not media_url:
            media_url = _save_mms_image(device, p.get("_id"), ct)
    if not body and not media_url:
        body = "[Attachment]"
    return body, media_url


def _mms_participants(device: str, mms_id: str) -> tuple[str | None, list[str]]:
    """Returns (sender_phone, participants) for one MMS -- sender is the
    FROM address (type=137), participants is FROM + all CC (type=130),
    normalized, TO (type=151, always the gateway's own number) excluded."""
    addrs = adb_client.query(device, f"content://mms/{mms_id}/addr")
    sender = None
    participants: list[str] = []
    for a in addrs:
        atype = a.get("type")
        phone = normalize_phone(a.get("address") or "")
        if not phone:
            continue
        if atype == _ADDR_TYPE_FROM:
            sender = phone
            if phone not in participants:
                participants.append(phone)
        elif atype == _ADDR_TYPE_CC and phone not in participants:
            participants.append(phone)
    return sender, participants


def _group_title(conn, thread_id: int) -> str:
    # A Bill-given group_name (jobs/sms/schema.py) always wins once set --
    # see api.py's PATCH /threads/<id> -- otherwise fall back to a
    # participant-name list, same as the frontend's displayName().
    row = conn.execute("SELECT group_name FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
    if row and row["group_name"]:
        return row["group_name"]
    rows = conn.execute(
        "SELECT phone, contact_name FROM sms_thread_participants WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()
    names = [r["contact_name"] or r["phone"] for r in rows]
    if len(names) <= 2:
        return " & ".join(names)
    return f"{names[0]} & {len(names) - 1} others"


def poll_inbound_adb(dry_run: bool = False) -> int:
    device = adb_client.connect_device()
    if not device:
        log.warning("poll_inbound_adb: gateway phone unreachable via adb, skipping tick")
        return 0

    cursor = _read_cursor()
    sms_rows = adb_client.query(
        device, "content://sms", where=f"type=1 AND _id > {cursor['last_sms_id']}", sort="_id ASC"
    )
    mms_rows = adb_client.query(
        device, "content://mms", where=f"msg_box=1 AND _id > {cursor['last_mms_id']}", sort="_id ASC"
    )

    if not sms_rows and not mms_rows:
        return 0

    planned = []  # list of dicts describing what would be ingested, for dry_run
    ingested = 0

    conn = get_connection()
    try:
        max_sms_id = cursor["last_sms_id"]
        max_mms_id = cursor["last_mms_id"]

        for row in sms_rows:
            row_id = int(row["_id"])
            max_sms_id = max(max_sms_id, row_id)
            phone = normalize_phone(row.get("address") or "")
            if not phone:
                log.warning("poll_inbound_adb: skipping sms _id=%s with unparseable address %r", row_id, row.get("address"))
                continue

            gateway_message_id = f"adb-sms-{row_id}"
            if not dry_run and conn.execute(
                "SELECT 1 FROM sms_messages WHERE gateway_message_id = ?", (gateway_message_id,)
            ).fetchone():
                continue

            body = row.get("body") or ""
            created_at = datetime.fromtimestamp(int(row["date"]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            if dry_run:
                planned.append({"kind": "sms", "id": row_id, "participants": [phone], "sender": phone, "body": body})
                continue

            hint_names = {phone: row["person"]} if row.get("person") else None
            thread_id = get_or_create_thread_multi(conn, [phone], hint_names)
            _insert_message(conn, thread_id, body, gateway_message_id, phone, created_at)
            _notify(conn, thread_id, phone, body)
            ingested += 1

        for row in mms_rows:
            row_id = int(row["_id"])
            max_mms_id = max(max_mms_id, row_id)

            sender, participants = _mms_participants(device, row_id)
            if not participants:
                log.warning("poll_inbound_adb: skipping mms _id=%s with no resolvable participants", row_id)
                continue

            gateway_message_id = f"adb-mms-{row_id}"
            if not dry_run and conn.execute(
                "SELECT 1 FROM sms_messages WHERE gateway_message_id = ?", (gateway_message_id,)
            ).fetchone():
                continue

            body, media_url = _mms_body(device, row_id)
            # MMS `date` is epoch SECONDS on this device (confirmed against
            # real data) -- SMS `date` above is epoch milliseconds. Different
            # units for the two tables, not a typo.
            created_at = datetime.fromtimestamp(int(row["date"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            android_thread_id = row.get("thread_id")

            if dry_run:
                planned.append({
                    "kind": "mms", "id": row_id, "participants": participants,
                    "sender": sender, "body": body, "media_url": media_url, "android_thread_id": android_thread_id,
                })
                continue

            thread_id = get_or_create_thread_multi(conn, participants, None, android_thread_id)
            _insert_message(conn, thread_id, body, gateway_message_id, sender, created_at, media_url)
            _notify(conn, thread_id, sender, body)
            ingested += 1

        if dry_run:
            print(json.dumps(planned, indent=2))
            return len(planned)

        conn.commit()
    finally:
        conn.close()

    _write_cursor({"last_sms_id": max_sms_id, "last_mms_id": max_mms_id})
    return ingested


def _insert_message(
    conn, thread_id: int, body: str, gateway_message_id: str, sender_phone: str | None,
    created_at: str, media_url: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id, sender_phone, created_at, media_url) "
        "VALUES (?, 'in', ?, ?, ?, ?, ?)",
        (thread_id, body, gateway_message_id, sender_phone, created_at, media_url),
    )
    conn.execute(
        """UPDATE sms_threads
           SET last_message_at = ?, last_message_preview = ?, unread = 1, snoozed_until = NULL, state = 'open'
           WHERE id = ?""",
        (created_at, body, thread_id),
    )


def _notify(conn, thread_id: int, sender_phone: str | None, body: str) -> None:
    thread_row = conn.execute(
        "SELECT contact_name, phone, is_group, muted FROM sms_threads WHERE id = ?", (thread_id,)
    ).fetchone()
    if thread_row["muted"]:
        return
    if sms_settings.should_silence_notifications():
        # Same condition an autoresponder (if Bill has one enabled for the
        # active mode) should fire under -- see jobs/sms/autoresponder.py.
        autoresponder.maybe_autorespond(conn, thread_id, sender_phone, is_group=bool(thread_row["is_group"]))
        return

    if thread_row["is_group"]:
        title = _group_title(conn, thread_id)
        sender_row = conn.execute(
            "SELECT contact_name FROM sms_thread_participants WHERE thread_id = ? AND phone = ?",
            (thread_id, sender_phone),
        ).fetchone()
        sender_label = (sender_row["contact_name"] if sender_row else None) or sender_phone or "Someone"
        push_body = f"{sender_label}: {body}"
    else:
        title = thread_row["contact_name"] or thread_row["phone"]
        push_body = body
    push_body = push_body if len(push_body) <= 120 else push_body[:117] + "..."

    unread_count = conn.execute("SELECT COUNT(*) FROM sms_threads WHERE unread = 1 AND state = 'open'").fetchone()[0]
    try:
        push.send_push_to_all({
            "title": title, "body": push_body, "thread_id": thread_id,
            "url": f"/sms?thread={thread_id}", "unread_count": unread_count,
        })
    except Exception as exc:  # noqa: BLE001 — a push failure must never break ingestion
        log.warning("poll_inbound_adb: push notify failed for thread_id=%s: %s", thread_id, exc)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    n = poll_inbound_adb(dry_run=args.dry_run)
    print(f"poll_inbound_adb: {'would ingest' if args.dry_run else 'ingested'} {n} message(s)")
