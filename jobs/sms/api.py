"""jobs/sms/api.py — Flask Blueprint for Watson SMS (project_backlog id=39).

Mount on the Watson dashboard app:
    from jobs.sms.api import sms_bp
    from jobs.sms.schema import create_tables as _sms_create_tables
    _sms_create_tables()
    app.register_blueprint(sms_bp)

Auth: every route requires header X-Watson-Key matching SMS_APP_API_KEY —
same shared-secret pattern as jobs/arc_interest/api.py, jobs/congregation/
servants_web.py, etc. This API is only ever called server-to-server from
the watson-tools Next.js app (wtsn.me/sms), never directly by a browser —
the human-facing PIN gate lives in watson-tools itself, not here.
"""
import base64
import json
import logging
import mimetypes
import os
import random
import sqlite3
import uuid

import requests
from datetime import date, datetime, timezone
from pathlib import Path

from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from core.database import get_connection
from jobs.analytics.attendance_reply import format_last_attended_reply
from jobs.congregation.elder_shepherding_report import build_deacon_group_names
from jobs.sms import broadcast_pacing, gateway_client, groups as sms_groups, push, send_core, settings as sms_settings
from jobs.sms.bridge import _get_or_create_thread, get_or_create_thread_multi, poll_inbound
from jobs.sms.carrier_lookup import normalize_phone

log = logging.getLogger(__name__)

sms_bp = Blueprint("sms", __name__, url_prefix="/api/sms")

_API_KEY = lambda: os.getenv("SMS_APP_API_KEY", "")
_GATEWAY_MODE = lambda: os.getenv("GATEWAY_MODE", "mock").strip().lower()
_VAPID_PUBLIC_KEY = lambda: os.getenv("VAPID_PUBLIC_KEY", "")

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")
_MEDIA_DIR = Path(__file__).resolve().parents[2] / "data" / "sms_media"
_MAX_MEDIA_BYTES = 5 * 1024 * 1024  # 5 MB -- generous for a phone photo, not a video


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _participants_for_thread(conn, thread_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT phone, contact_name FROM sms_thread_participants WHERE thread_id = ? ORDER BY id",
        (thread_id,),
    ).fetchall()
    return [{"phone": r["phone"], "contact_name": r["contact_name"]} for r in rows]


def _thread_dict(conn, row) -> dict:
    # highlight_note is only live for the day it was set (highlight_date) --
    # this keeps a stale birthday/etc. note from resurfacing a thread
    # forever without needing a separate cleanup job.
    is_highlighted = row["highlight_date"] == date.today().isoformat()
    return {
        "id": row["id"],
        "phone": row["phone"],
        "contact_name": row["contact_name"],
        "member_id": row["member_id"],
        "last_message_preview": row["last_message_preview"],
        "last_message_at": row["last_message_at"],
        "unread": bool(row["unread"]),
        "state": row["state"],
        "muted": bool(row["muted"]),
        "snoozed_until": row["snoozed_until"],
        "draft_text": row["draft_text"],
        "highlight_note": row["highlight_note"] if is_highlighted else None,
        "is_group": bool(row["is_group"]),
        "participants": _participants_for_thread(conn, row["id"]),
        "group_name": row["group_name"],
    }


def _message_dict(row, participants_by_phone: dict | None = None) -> dict:
    sender_phone = row["sender_phone"] if "sender_phone" in row.keys() else None
    return {
        "id": row["id"],
        "direction": row["direction"],
        "body": row["body"],
        "created_at": row["created_at"],
        "media_url": row["media_url"],
        "media_type": row["media_type"],
        "status": row["status"],
        "sender_phone": sender_phone,
        "sender_name": (participants_by_phone or {}).get(sender_phone) if sender_phone else None,
    }


def _scheduled_dict(row) -> dict:
    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "body": row["body"],
        "send_at": row["send_at"],
        "status": row["status"],
        "error": row["error"],
    }


@sms_bp.route("/threads", methods=["GET"])
@_require_key
def list_threads():
    archived = request.args.get("archived") == "1"
    conn = get_connection()
    try:
        if archived:
            rows = conn.execute(
                "SELECT * FROM sms_threads WHERE state = 'archived' "
                "ORDER BY (last_message_at IS NULL), last_message_at DESC"
            ).fetchall()
        else:
            # Snoozed-but-due threads reappear on their own here (no cron
            # needed) -- the snooze is just a filter, not a separate queue.
            # A thread with a still-live highlight (highlight_date = today,
            # e.g. a birthday note) is pinned above everything else.
            rows = conn.execute(
                "SELECT * FROM sms_threads WHERE state != 'archived' "
                "AND (snoozed_until IS NULL OR snoozed_until <= datetime('now')) "
                "ORDER BY (highlight_date = date('now') AND highlight_note IS NOT NULL) DESC, "
                "(last_message_at IS NULL), last_message_at DESC"
            ).fetchall()
        return jsonify({"threads": [_thread_dict(conn, r) for r in rows]})
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>", methods=["PATCH"])
@_require_key
def update_thread(thread_id):
    data = request.get_json(force=True) or {}
    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404

        if "state" in data:
            state = data["state"]
            if state not in ("open", "archived"):
                return jsonify({"error": "state must be 'open' or 'archived'"}), 400
            conn.execute("UPDATE sms_threads SET state = ? WHERE id = ?", (state, thread_id))

        if "muted" in data:
            conn.execute("UPDATE sms_threads SET muted = ? WHERE id = ?", (1 if data["muted"] else 0, thread_id))

        if "unread" in data:
            conn.execute("UPDATE sms_threads SET unread = ? WHERE id = ?", (1 if data["unread"] else 0, thread_id))

        if "draft_text" in data:
            conn.execute("UPDATE sms_threads SET draft_text = ? WHERE id = ?", (data["draft_text"] or None, thread_id))

        if "group_name" in data:
            # Meaningless for a 1:1 thread -- only a group thread's display
            # name is ever overridden this way (see displayName() in
            # SmsApp.tsx and _group_title() in adb_inbound.py, both of
            # which prefer this over the auto participant-name label).
            if not thread["is_group"]:
                return jsonify({"error": "group_name only applies to a group thread"}), 400
            group_name = (data["group_name"] or "").strip() or None
            conn.execute("UPDATE sms_threads SET group_name = ? WHERE id = ?", (group_name, thread_id))

        if "highlight_note" in data and not data["highlight_note"]:
            conn.execute(
                "UPDATE sms_threads SET highlight_note = NULL, highlight_date = NULL WHERE id = ?",
                (thread_id,),
            )

        if "snoozed_until" in data:
            snoozed_until = data["snoozed_until"]
            if snoozed_until:
                try:
                    datetime.strptime(snoozed_until, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return jsonify({"error": "snoozed_until must be 'YYYY-MM-DD HH:MM:SS' UTC"}), 400
            conn.execute("UPDATE sms_threads SET snoozed_until = ? WHERE id = ?", (snoozed_until or None, thread_id))

        conn.commit()
        row = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        return jsonify({"thread": _thread_dict(conn, row)})
    finally:
        conn.close()


_SPELLCHECK_PROMPT = """Fix ONLY spelling mistakes and obvious keyboard/autocorrect typos in the text below. Do not reword, rephrase, add, remove, or reinterpret anything -- preserve the exact wording, tone, punctuation style, and line breaks otherwise. Output ONLY the corrected text, nothing else -- no quotes, no preamble, no explanation.

TEXT:
{text}"""


@sms_bp.route("/spellcheck", methods=["POST"])
@_require_key
def spellcheck():
    """Local-model typo fixer for the compose box's one-tap fix button.
    Deliberately scoped to spelling/typos only (not a rewrite/rephrase) --
    see feedback_ai_never_originates_relational_language.md: Watson must
    never author or alter the substance of Bill's own relational wording,
    only mechanically correct it."""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "gemma3:4b",
                "prompt": _SPELLCHECK_PROMPT.format(text=text),
                "stream": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        fixed = resp.json().get("response", "").strip()
        # Defensive strip in case the model wraps its answer in quotes
        # despite being told not to.
        if len(fixed) >= 2 and fixed[0] == fixed[-1] and fixed[0] in ('"', "'"):
            fixed = fixed[1:-1].strip()
        if not fixed:
            return jsonify({"error": "spellcheck returned nothing"}), 502
        return jsonify({"text": fixed})
    except Exception as exc:
        log.error("spellcheck: ollama call failed: %s", exc)
        return jsonify({"error": "spellcheck unavailable"}), 502


@sms_bp.route("/search", methods=["GET"])
@_require_key
def search_messages():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})

    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT m.id AS message_id, m.thread_id, m.body, m.created_at,
                      t.contact_name, t.phone, t.is_group
               FROM sms_messages m
               JOIN sms_threads t ON t.id = m.thread_id
               WHERE m.body LIKE ? ESCAPE '\\'
               ORDER BY m.created_at DESC
               LIMIT 50""",
            ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        ).fetchall()
        results = []
        for r in rows:
            # A group thread's phone is the synthetic "group:<id>" key
            # (jobs/sms/schema.py) -- never show it raw, same guard as
            # sabbath_digest.py's group-label fix.
            if r["is_group"]:
                names = [p["contact_name"] or p["phone"] for p in _participants_for_thread(conn, r["thread_id"])]
                display = " & ".join(names[:2]) + (f" & {len(names) - 2} others" if len(names) > 2 else "")
            else:
                display = r["contact_name"] or r["phone"]
            results.append({
                "message_id": r["message_id"],
                "thread_id": r["thread_id"],
                "body": r["body"],
                "created_at": r["created_at"],
                "contact_name": display,
                "phone": None if r["is_group"] else r["phone"],
            })
        return jsonify({"results": results})
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>/messages", methods=["GET"])
@_require_key
def thread_messages(thread_id):
    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404

        messages = conn.execute(
            "SELECT * FROM sms_messages WHERE thread_id = ? ORDER BY created_at ASC, id ASC",
            (thread_id,),
        ).fetchall()
        # Built once per request, not per message, to avoid N+1 lookups.
        participants_by_phone = {p["phone"]: p["contact_name"] for p in _participants_for_thread(conn, thread_id)}

        return jsonify({
            "thread": _thread_dict(conn, thread),
            "messages": [_message_dict(m, participants_by_phone) for m in messages],
        })
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>/context", methods=["GET"])
@_require_key
def thread_context(thread_id):
    """Pastoral context pulled from congregation.db by the thread's soft
    member_id cross-reference (see jobs/sms/schema.py's module docstring --
    it's a phone match, never a foreign key, since congregation.db is a
    separate file). Reuses jobs/analytics/attendance_reply.py's shared
    last-attended sentence so this reads identically to Team Chat/bot.py."""
    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404
        member_id = thread["member_id"]
    finally:
        conn.close()

    if not member_id:
        return jsonify({"matched": False})

    try:
        cong = sqlite3.connect(CONGREGATION_DB)
        cong.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        log.error("thread_context: could not open congregation.db: %s", exc)
        return jsonify({"matched": False, "error": "congregation lookup unavailable"}), 502

    try:
        member = cong.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if not member:
            return jsonify({"matched": False})

        last = cong.execute(
            "SELECT service_date, campus FROM attendance WHERE member_id = ? ORDER BY service_date DESC LIMIT 1",
            (member_id,),
        ).fetchone()
        last_attended = last["service_date"] if last else None
        campus = last["campus"] if last else None

        household = []
        if member["household_id"]:
            household = [
                {"name": r["name"], "household_role": r["household_role"]}
                for r in cong.execute(
                    "SELECT name, household_role FROM members WHERE household_id = ? AND id != ?",
                    (member["household_id"], member["id"]),
                ).fetchall()
            ]

        serving_teams = [
            {"team_name": r["team_name"], "position": r["position"]}
            for r in cong.execute(
                "SELECT team_name, position FROM team_memberships WHERE member_id = ? AND active = 1",
                (member["id"],),
            ).fetchall()
        ]

        return jsonify({
            "matched": True,
            "name": member["name"],
            "campus_preference": member["campus_preference"],
            "first_visit_date": member["first_visit_date"],
            "deacon": member["deacon"],
            "last_attended_summary": format_last_attended_reply(member["name"], last_attended, campus),
            "household": household,
            "birthdate": member["birthdate"],
            "anniversary": member["anniversary"],
            "active_status": member["active"],
            "serving_teams": serving_teams,
            "started_serving_date": member["started_serving_date"],
        })
    finally:
        cong.close()


@sms_bp.route("/attention", methods=["GET"])
@_require_key
def attention_list():
    """Flattened at-risk/critical/disconnected members for the gear-menu
    "At Risk & Critical" panel -- reuses elder_shepherding_report.py's
    build_deacon_group_names() so the bucket definition (14-27 days = at
    risk, 28-62 = critical, 63+ = disconnected) stays a single source of
    truth rather than a second attendance query drifting out of sync with
    the deacon report. Disconnected is included here (2026-09-28, split
    off critical's old open-ended 28+ range) so the most-absent people
    don't silently drop off this outreach list. thread_id is looked up by
    the same member_id soft cross-reference sms_threads already carries
    (see thread_context() above), so "Text" can jump straight into an
    existing thread when one exists instead of always opening a blank
    compose."""
    conn = get_connection()
    try:
        thread_by_member = {
            r["member_id"]: r["id"]
            for r in conn.execute(
                "SELECT member_id, id FROM sms_threads WHERE member_id IS NOT NULL AND is_group = 0"
            ).fetchall()
        }
    finally:
        conn.close()

    members = []
    for group in build_deacon_group_names():
        for m in group["members"]:
            if m["bucket"] not in ("at_risk", "critical", "disconnected"):
                continue
            members.append({
                "id": m["id"],
                "name": m["name"],
                "phone": m["phone"],
                "bucket": m["bucket"],
                "weeks_absent": m["days_since"] // 7,
                "thread_id": thread_by_member.get(m["id"]),
            })

    _BUCKET_SORT = {"disconnected": 0, "critical": 1, "at_risk": 2}
    members.sort(key=lambda x: (_BUCKET_SORT[x["bucket"]], -x["weeks_absent"]))
    return jsonify({"members": members})


def _save_media(media_base64: str, media_type: str) -> str:
    """Decodes and writes an uploaded image to disk, returns its /api/sms/media
    URL path. Raises ValueError on anything malformed or oversized."""
    if not media_type.startswith("image/"):
        raise ValueError("only image attachments are supported")
    try:
        raw = base64.b64decode(media_base64, validate=True)
    except Exception as exc:
        raise ValueError("media_base64 is not valid base64") from exc
    if len(raw) > _MAX_MEDIA_BYTES:
        raise ValueError("image is too large (5 MB max)")

    ext = mimetypes.guess_extension(media_type) or ""
    filename = f"{uuid.uuid4().hex}{ext}"
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (_MEDIA_DIR / filename).write_bytes(raw)
    return f"/api/sms/media/{filename}"


@sms_bp.route("/media/<filename>", methods=["GET"])
@_require_key
def get_media(filename):
    path = (_MEDIA_DIR / filename).resolve()
    if _MEDIA_DIR.resolve() not in path.parents or not path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(path)


# Send-and-record logic lives in jobs/sms/send_core.py, shared with
# jobs/sms/scheduled_sender.py -- see that module's docstring for why (a
# scheduled send into a group thread needs the same participant fan-out).
_send_and_record = send_core.send_and_record


@sms_bp.route("/send-to-self", methods=["POST"])
@_require_key
def send_to_self():
    """Narrowly-scoped exception to the tap-to-send rule -- Bill decided
    2026-09-26 that Watson may send directly on his instruction when, and
    only when, the recipient is Bill's own personal number, since that
    doesn't touch the human-relationship concern the tap-to-send gate
    exists for (see feedback_ai_never_originates_relational_language.md).
    Hard-coded to WATSON_OWNER_PHONE so this can never widen to any other
    recipient regardless of what's passed in -- there is deliberately no
    `phone` field accepted here."""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    phone_digits = normalize_phone(os.getenv("WATSON_OWNER_PHONE", ""))
    if not phone_digits:
        return jsonify({"error": "WATSON_OWNER_PHONE is not configured"}), 500

    conn = get_connection()
    try:
        thread_id = _get_or_create_thread(conn, phone_digits, "Dr. Bill Yomes")
        error_body, error_status, message_id = _send_and_record(conn, thread_id, text, None, None)
        if error_body:
            return jsonify(error_body), error_status
        return jsonify({"ok": True, "thread_id": thread_id, "message_id": message_id})
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>/send", methods=["POST"])
@_require_key
def send_to_thread(thread_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    media_base64 = data.get("media_base64")
    media_type = data.get("media_type")
    if not text and not media_base64:
        return jsonify({"error": "text or media_base64 is required"}), 400

    media_url = None
    if media_base64:
        try:
            media_url = _save_media(media_base64, media_type or "")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404

        error_body, error_status, message_id = _send_and_record(conn, thread_id, text, media_url, media_type)
        if error_body:
            return jsonify(error_body), error_status

        message = conn.execute("SELECT * FROM sms_messages WHERE id = ?", (message_id,)).fetchone()
        return jsonify({"message": _message_dict(message)})
    finally:
        conn.close()


def _resolve_recipients(data: dict) -> tuple[list[str], dict[str, str], tuple[dict, int] | None]:
    """Accepts either the new `recipients: [{phone, name?}]` shape (a
    compose-window "group text" with more than one person) or the original
    single `phone`/`name` fields, so existing callers/tests keep working
    unchanged. Returns (phones, hint_names, error_response_or_None)."""
    recipients_raw = data.get("recipients")
    if recipients_raw:
        if not isinstance(recipients_raw, list) or not recipients_raw:
            return [], {}, ({"error": "recipients must be a non-empty list"}, 400)
        phones: list[str] = []
        hint_names: dict[str, str] = {}
        for r in recipients_raw:
            phone_digits = normalize_phone((r.get("phone") or "").strip())
            if not phone_digits:
                return [], {}, ({"error": f"could not parse phone number: {r.get('phone')!r}"}, 400)
            if phone_digits not in phones:
                phones.append(phone_digits)
            name = (r.get("name") or "").strip()
            if name:
                hint_names[phone_digits] = name
        return phones, hint_names, None

    phone_raw = (data.get("phone") or "").strip()
    if not phone_raw:
        return [], {}, ({"error": "phone or recipients is required"}, 400)
    phone_digits = normalize_phone(phone_raw)
    if not phone_digits:
        return [], {}, ({"error": "could not parse phone number"}, 400)
    name = (data.get("name") or "").strip()
    return [phone_digits], ({phone_digits: name} if name else {}), None


@sms_bp.route("/send", methods=["POST"])
@_require_key
def send_new():
    """Starts (or continues, if this exact set of people already has a
    thread) a conversation -- the "new message" compose flow. A single
    recipient behaves exactly as before; multiple recipients (`recipients`
    in the body) start/continue a group thread, matched by participant set
    the same way an inbound group text is (jobs/sms/bridge.py's
    get_or_create_thread_multi) so it merges correctly either way."""
    data = request.get_json(force=True) or {}
    phones, hint_names, error = _resolve_recipients(data)
    if error:
        return jsonify(error[0]), error[1]
    text = (data.get("text") or "").strip()
    media_base64 = data.get("media_base64")
    media_type = data.get("media_type")
    if not text and not media_base64:
        return jsonify({"error": "text or media_base64 is required"}), 400

    media_url = None
    if media_base64:
        try:
            media_url = _save_media(media_base64, media_type or "")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    conn = get_connection()
    try:
        thread_id = get_or_create_thread_multi(conn, phones, hint_names)

        error_body, error_status, message_id = _send_and_record(conn, thread_id, text, media_url, media_type)
        if error_body:
            return jsonify(error_body), error_status

        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        message = conn.execute("SELECT * FROM sms_messages WHERE id = ?", (message_id,)).fetchone()
        return jsonify({"thread": _thread_dict(conn, thread), "message": _message_dict(message)}), 201
    finally:
        conn.close()


@sms_bp.route("/schedule", methods=["POST"])
@_require_key
def schedule_new():
    """Same recipient resolution as send_new above, but for the compose
    window's date/time picker -- schedules the first message to a
    (possibly brand-new) contact or group instead of sending it
    immediately."""
    data = request.get_json(force=True) or {}
    phones, hint_names, error = _resolve_recipients(data)
    if error:
        return jsonify(error[0]), error[1]
    text = (data.get("text") or "").strip()
    send_at = (data.get("send_at") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    error = _validate_send_at(send_at)
    if error:
        return jsonify({"error": error}), 400

    conn = get_connection()
    try:
        thread_id = get_or_create_thread_multi(conn, phones, hint_names)
        conn.commit()

        cur = conn.execute(
            "INSERT INTO sms_scheduled_messages (thread_id, body, send_at) VALUES (?, ?, ?)",
            (thread_id, text, send_at),
        )
        conn.commit()

        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        scheduled = conn.execute("SELECT * FROM sms_scheduled_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"thread": _thread_dict(conn, thread), "scheduled": _scheduled_dict(scheduled)}), 201
    finally:
        conn.close()


@sms_bp.route("/contacts", methods=["GET"])
@_require_key
def search_contacts():
    """Name-search over congregation.db members with a phone on file, for
    the compose window's contact-picker. Returns at most 15 matches."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"contacts": []})

    try:
        cong = sqlite3.connect(CONGREGATION_DB)
        cong.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        log.error("search_contacts: could not open congregation.db: %s", exc)
        return jsonify({"contacts": []})

    try:
        rows = cong.execute(
            """SELECT id, name, phone FROM members
               WHERE phone IS NOT NULL AND phone != '' AND name LIKE ? ESCAPE '\\'
               ORDER BY name
               LIMIT 15""",
            ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        ).fetchall()
        return jsonify({
            "contacts": [{"id": r["id"], "name": r["name"], "phone": r["phone"]} for r in rows]
        })
    finally:
        cong.close()


@sms_bp.route("/settings", methods=["GET"])
@_require_key
def get_settings():
    row = sms_settings._get_row()
    return jsonify(row)


@sms_bp.route("/settings", methods=["PATCH"])
@_require_key
def update_settings():
    data = request.get_json(force=True) or {}
    vacation_mode = data.get("vacation_mode")
    sabbath_silence = data.get("sabbath_silence")
    sabbath_autoresponder_on = data.get("sabbath_autoresponder_on")
    sabbath_autoresponder_body = data.get("sabbath_autoresponder_body")
    vacation_autoresponder_on = data.get("vacation_autoresponder_on")
    vacation_autoresponder_body = data.get("vacation_autoresponder_body")
    row = sms_settings.set_setting(
        vacation_mode=bool(vacation_mode) if vacation_mode is not None else None,
        sabbath_silence=bool(sabbath_silence) if sabbath_silence is not None else None,
        sabbath_autoresponder_on=bool(sabbath_autoresponder_on) if sabbath_autoresponder_on is not None else None,
        sabbath_autoresponder_body=sabbath_autoresponder_body if sabbath_autoresponder_body is not None else None,
        vacation_autoresponder_on=bool(vacation_autoresponder_on) if vacation_autoresponder_on is not None else None,
        vacation_autoresponder_body=vacation_autoresponder_body if vacation_autoresponder_body is not None else None,
    )
    return jsonify(row)


@sms_bp.route("/members/search", methods=["GET"])
@_require_key
def search_members():
    """Name-search over ALL congregation.db members, phone on file or not --
    for linking an unmatched thread's number to the right person (the
    /contacts search above deliberately excludes phone-less members since
    that one is for picking who to text, a different job)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"members": []})

    try:
        cong = sqlite3.connect(CONGREGATION_DB)
        cong.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        log.error("search_members: could not open congregation.db: %s", exc)
        return jsonify({"members": []})

    try:
        rows = cong.execute(
            """SELECT id, name, phone FROM members
               WHERE name LIKE ? ESCAPE '\\'
               ORDER BY name
               LIMIT 15""",
            ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        ).fetchall()
        return jsonify({
            "members": [{"id": r["id"], "name": r["name"], "phone": r["phone"]} for r in rows]
        })
    finally:
        cong.close()


@sms_bp.route("/threads/<int:thread_id>/link-member", methods=["POST"])
@_require_key
def link_member(thread_id):
    """Attaches an unmatched thread to an existing congregation.db member --
    e.g. someone texts in on a number that isn't on file yet, but Bill
    recognizes who it is. Writes the number into members.phone (only when
    that field is currently empty, unless overwrite=true is explicitly
    passed -- or into members.alt_phone instead, alongside the existing
    phone, when keep_both=true) and sets sms_threads.member_id/contact_name
    so the pastoral context panel resolves for this thread going forward."""
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    overwrite = bool(data.get("overwrite"))
    keep_both = bool(data.get("keep_both"))
    if not member_id:
        return jsonify({"error": "member_id is required"}), 400

    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404
        phone_digits = thread["phone"]
    finally:
        conn.close()

    try:
        cong = sqlite3.connect(CONGREGATION_DB)
        cong.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        log.error("link_member: could not open congregation.db: %s", exc)
        return jsonify({"error": "congregation lookup unavailable"}), 502

    try:
        member = cong.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if not member:
            return jsonify({"error": "member not found"}), 404

        formatted_phone = f"({phone_digits[:3]}) {phone_digits[3:6]}-{phone_digits[6:]}"
        existing_phone = (member["phone"] or "").strip()
        if existing_phone and existing_phone != formatted_phone and not overwrite and not keep_both:
            return jsonify({
                "error": "phone_conflict",
                "existing_phone": existing_phone,
                "new_phone": formatted_phone,
            }), 409

        if keep_both and existing_phone and existing_phone != formatted_phone:
            cong.execute("UPDATE members SET alt_phone = ? WHERE id = ?", (formatted_phone, member_id))
        else:
            cong.execute("UPDATE members SET phone = ? WHERE id = ?", (formatted_phone, member_id))
        cong.commit()
    finally:
        cong.close()

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sms_threads SET member_id = ?, contact_name = ? WHERE id = ?",
            (member_id, member["name"], thread_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        return jsonify({"ok": True, "thread": _thread_dict(conn, row)})
    finally:
        conn.close()


@sms_bp.route("/gateway/delivery", methods=["POST"])
def gateway_delivery_webhook():
    """Receives sms:delivered/sms:sent/sms:failed webhook events from the
    gateway app -- confirmed 2026-09-26 against the app's real WebHookEvent
    shape ({id, webhookId, deviceId, event, payload: {messageId, ...}}), not
    the flat {gateway_message_id, status} guess this originally shipped
    with. No X-Watson-Key here (the gateway app's webhook POSTs can't carry
    custom headers) -- instead this checks the payload's deviceId against
    SMS_GATEWAY_DEVICE_ID as a lightweight authenticity check, acceptable
    since this only runs over the home LAN, not the public internet."""
    data = request.get_json(force=True) or {}
    expected_device_id = os.getenv("SMS_GATEWAY_DEVICE_ID", "")
    if not expected_device_id or data.get("deviceId") != expected_device_id:
        return jsonify({"error": "unrecognized device"}), 403

    event = data.get("event")
    status_by_event = {"sms:delivered": "delivered", "sms:sent": "sent", "sms:failed": "failed"}
    status = status_by_event.get(event)
    message_id = (data.get("payload") or {}).get("messageId")
    if not status or not message_id:
        return jsonify({"ok": True, "ignored": True})

    conn = get_connection()
    try:
        # Group sends have one sms_messages row per bubble but one
        # sms_message_recipients row per participant (see _send_and_record)
        # -- check that first since gateway_message_id is ambiguous/NULL on
        # the parent row for a group send. Falls back to the plain 1:1 path
        # unchanged otherwise.
        recipient = conn.execute(
            "SELECT message_id FROM sms_message_recipients WHERE gateway_message_id = ?", (message_id,)
        ).fetchone()
        if recipient:
            conn.execute(
                "UPDATE sms_message_recipients SET status = ? WHERE gateway_message_id = ?",
                (status, message_id),
            )
        else:
            conn.execute(
                "UPDATE sms_messages SET status = ? WHERE gateway_message_id = ?",
                (status, message_id),
            )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>/scheduled", methods=["GET"])
@_require_key
def list_scheduled(thread_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sms_scheduled_messages WHERE thread_id = ? ORDER BY send_at ASC",
            (thread_id,),
        ).fetchall()
        return jsonify({"scheduled": [_scheduled_dict(r) for r in rows]})
    finally:
        conn.close()


def _validate_send_at(send_at: str) -> str | None:
    """Returns an error message, or None if send_at is a valid future UTC
    'YYYY-MM-DD HH:MM:SS' timestamp."""
    try:
        parsed = datetime.strptime(send_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return "send_at must be 'YYYY-MM-DD HH:MM:SS' UTC"
    if parsed <= datetime.now(timezone.utc):
        return "send_at must be in the future"
    return None


@sms_bp.route("/threads/<int:thread_id>/scheduled", methods=["POST"])
@_require_key
def create_scheduled(thread_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    send_at = (data.get("send_at") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    error = _validate_send_at(send_at)
    if error:
        return jsonify({"error": error}), 400

    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404

        cur = conn.execute(
            "INSERT INTO sms_scheduled_messages (thread_id, body, send_at) VALUES (?, ?, ?)",
            (thread_id, text, send_at),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM sms_scheduled_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"scheduled": _scheduled_dict(row)}), 201
    finally:
        conn.close()


@sms_bp.route("/scheduled/<int:scheduled_id>", methods=["PUT"])
@_require_key
def update_scheduled(scheduled_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    send_at = (data.get("send_at") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    error = _validate_send_at(send_at)
    if error:
        return jsonify({"error": error}), 400

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM sms_scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        # Editing a failed send retries it -- back to pending, error cleared.
        conn.execute(
            "UPDATE sms_scheduled_messages SET body = ?, send_at = ?, status = 'pending', error = NULL WHERE id = ?",
            (text, send_at, scheduled_id),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM sms_scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
        return jsonify({"scheduled": _scheduled_dict(row)})
    finally:
        conn.close()


@sms_bp.route("/scheduled/<int:scheduled_id>", methods=["DELETE"])
@_require_key
def delete_scheduled(scheduled_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sms_scheduled_messages WHERE id = ?", (scheduled_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@sms_bp.route("/templates", methods=["GET"])
@_require_key
def list_templates():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sms_templates ORDER BY id").fetchall()
        return jsonify({
            "templates": [
                {"id": r["id"], "label": r["label"], "body": r["body"], "updated_at": r["updated_at"]}
                for r in rows
            ]
        })
    finally:
        conn.close()


@sms_bp.route("/templates", methods=["POST"])
@_require_key
def create_template():
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip()
    body = (data.get("body") or "").strip()
    if not label or not body:
        return jsonify({"error": "label and body are required"}), 400

    import re
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "template"

    conn = get_connection()
    try:
        template_id = slug
        suffix = 2
        while conn.execute("SELECT 1 FROM sms_templates WHERE id = ?", (template_id,)).fetchone():
            template_id = f"{slug}_{suffix}"
            suffix += 1

        conn.execute(
            "INSERT INTO sms_templates (id, label, body, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (template_id, label, body),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM sms_templates WHERE id = ?", (template_id,)).fetchone()
        return jsonify({"id": row["id"], "label": row["label"], "body": row["body"], "updated_at": row["updated_at"]}), 201
    finally:
        conn.close()


@sms_bp.route("/templates/<template_id>", methods=["PUT"])
@_require_key
def update_template(template_id):
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body is required"}), 400

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM sms_templates WHERE id = ?", (template_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        conn.execute(
            "UPDATE sms_templates SET body = ?, updated_at = datetime('now') WHERE id = ?",
            (body, template_id),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM sms_templates WHERE id = ?", (template_id,)).fetchone()
        return jsonify({"id": row["id"], "label": row["label"], "body": row["body"], "updated_at": row["updated_at"]})
    finally:
        conn.close()


@sms_bp.route("/heartbeat", methods=["GET"])
@_require_key
def latest_heartbeat():
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sms_gateway_heartbeat ORDER BY checked_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify({"heartbeat": None})
        return jsonify({
            "heartbeat": {
                "checked_at": row["checked_at"],
                "ok": bool(row["ok"]),
                "battery_pct": row["battery_pct"],
                "detail": row["detail"],
            }
        })
    finally:
        conn.close()


@sms_bp.route("/poll-now", methods=["POST"])
@_require_key
def poll_now():
    """Manual refresh button's live poll -- same drain the bridge.py cron
    runs every minute, but on demand so a reply doesn't sit for up to 60s
    before showing up just because the user tapped refresh right after it
    arrived."""
    ingested = poll_inbound()
    return jsonify({"ok": True, "ingested": ingested})


@sms_bp.route("/mock/inject", methods=["POST"])
@_require_key
def mock_inject():
    if _GATEWAY_MODE() != "mock":
        return jsonify({"error": "GATEWAY_MODE is not 'mock'"}), 403

    data = request.get_json(force=True) or {}
    phone = data.get("phone")
    text = data.get("text")
    if not phone or not text:
        return jsonify({"error": "phone and text are required"}), 400

    gateway_client.mock_queue_push(phone, text, name=data.get("name"))
    ingested = poll_inbound()
    return jsonify({"ok": True, "ingested": ingested})


@sms_bp.route("/push/vapid-public-key", methods=["GET"])
@_require_key
def push_vapid_public_key():
    return jsonify({"publicKey": _VAPID_PUBLIC_KEY()})


@sms_bp.route("/push/subscribe", methods=["POST"])
@_require_key
def push_subscribe():
    data = request.get_json(force=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "endpoint and keys.p256dh/keys.auth are required"}), 400

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sms_push_subscriptions (endpoint, p256dh, auth)
               VALUES (?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth""",
            (endpoint, p256dh, auth),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@sms_bp.route("/push/unsubscribe", methods=["POST"])
@_require_key
def push_unsubscribe():
    data = request.get_json(force=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "endpoint is required"}), 400

    conn = get_connection()
    try:
        conn.execute("DELETE FROM sms_push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# --- Groups + Broadcasts ----------------------------------------------
#
# A broadcast is Bill's own exact wording (see feedback_ai_never_originates
# _relational_language) sent as individual 1:1 texts (jobs/sms/
# broadcast_sender.py) to a Bill-defined group ("anything Bill defines" --
# a filter over deacon/team/role/campus, a hand-picked list of people, or
# both). Watson resolves membership and fans the send out on schedule;
# Bill authors the message and defines who's in the group. See
# jobs/sms/groups.py and jobs/sms/schema.py's broadcast tables docstring.

def _resolve_adhoc(filter_json: dict, active_only: bool, manual: list, manual_only: bool = False) -> list[dict]:
    """Shared by /groups/preview and POST /broadcasts (when the caller
    passes a filter/manual list directly instead of a saved group_id).

    manual_only=True skips the filter dimensions entirely and starts from
    an empty base -- without this, "Just these people" has no way to mean
    just those people: an empty filter_json isn't "match nobody", it's
    "no restriction on this dimension" (i.e. resolves to everyone), so
    manual entries would land on top of the full congregation instead of
    replacing it. Confirmed live 2026-09-29 before this flag existed: an
    ad-hoc broadcast meant for one recipient resolved 156 -- caught and
    canceled before send_at, never delivered."""
    recipients = [] if manual_only else sms_groups.resolve_filter(filter_json, active_only)
    by_phone = {r["phone"]: r for r in recipients}
    for m in manual:
        phone = normalize_phone((m.get("phone") or "").strip())
        if not phone:
            continue
        if m.get("mode") == "exclude":
            by_phone.pop(phone, None)
        else:
            by_phone.setdefault(
                phone, {"member_id": m.get("member_id"), "phone": phone, "contact_name": m.get("name")}
            )
    return list(by_phone.values())


def _group_dict(conn, row) -> dict:
    _, recipients = sms_groups.resolve_group(conn, row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "filter": json.loads(row["filter_json"] or "{}"),
        "active_only": bool(row["active_only"]),
        "manual_only": bool(row["manual_only"]),
        "recipient_count": len(recipients),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@sms_bp.route("/groups/options", methods=["GET"])
@_require_key
def group_options():
    """Distinct deacon/team/role/campus values plus the event picker's
    options (events with at least one phone-having registrant), for the
    group-builder UI's pickers."""
    return jsonify(sms_groups.group_options())


@sms_bp.route("/groups/preview", methods=["POST"])
@_require_key
def group_preview():
    """Resolves an ad-hoc (not-yet-saved) filter + manual overrides, for
    the compose flow's live recipient count/list before Bill saves a group
    or schedules a broadcast."""
    data = request.get_json(force=True) or {}
    resolved = _resolve_adhoc(
        data.get("filter") or {}, bool(data.get("active_only", True)), data.get("manual") or [],
        manual_only=bool(data.get("manual_only")),
    )
    resolved.sort(key=lambda r: (r["contact_name"] or "").lower())
    return jsonify({"recipient_count": len(resolved), "recipients": resolved[:200]})


@sms_bp.route("/groups", methods=["GET"])
@_require_key
def list_groups():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sms_groups ORDER BY name").fetchall()
        return jsonify({"groups": [_group_dict(conn, r) for r in rows]})
    finally:
        conn.close()


@sms_bp.route("/groups", methods=["POST"])
@_require_key
def create_group():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO sms_groups (name, filter_json, active_only, manual_only) VALUES (?, ?, ?, ?)",
            (name, json.dumps(data.get("filter") or {}), 1 if data.get("active_only", True) else 0,
             1 if data.get("manual_only") else 0),
        )
        group_id = cur.lastrowid
        for m in (data.get("manual") or []):
            phone = normalize_phone((m.get("phone") or "").strip())
            if not phone:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO sms_group_members (group_id, member_id, phone, contact_name, mode) "
                "VALUES (?, ?, ?, ?, ?)",
                (group_id, m.get("member_id"), phone, m.get("name"), "exclude" if m.get("mode") == "exclude" else "include"),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM sms_groups WHERE id = ?", (group_id,)).fetchone()
        return jsonify({"group": _group_dict(conn, row)}), 201
    finally:
        conn.close()


@sms_bp.route("/groups/<int:group_id>", methods=["PUT"])
@_require_key
def update_group(group_id):
    data = request.get_json(force=True) or {}
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sms_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        name = (data.get("name") or row["name"]).strip()
        filter_json = data.get("filter") if "filter" in data else json.loads(row["filter_json"])
        active_only = 1 if data.get("active_only", bool(row["active_only"])) else 0
        manual_only = 1 if data.get("manual_only", bool(row["manual_only"])) else 0
        conn.execute(
            "UPDATE sms_groups SET name = ?, filter_json = ?, active_only = ?, manual_only = ?, updated_at = datetime('now') WHERE id = ?",
            (name, json.dumps(filter_json), active_only, manual_only, group_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sms_groups WHERE id = ?", (group_id,)).fetchone()
        return jsonify({"group": _group_dict(conn, row)})
    finally:
        conn.close()


@sms_bp.route("/groups/<int:group_id>", methods=["DELETE"])
@_require_key
def delete_group(group_id):
    conn = get_connection()
    try:
        in_use = conn.execute(
            "SELECT COUNT(*) FROM sms_broadcasts WHERE group_id = ? AND status IN ('scheduled', 'sending')",
            (group_id,),
        ).fetchone()[0]
        if in_use:
            return jsonify({"error": "group has a pending broadcast -- cancel it first"}), 409
        conn.execute("DELETE FROM sms_group_members WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM sms_groups WHERE id = ?", (group_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@sms_bp.route("/groups/<int:group_id>/members", methods=["GET"])
@_require_key
def get_group_members(group_id):
    conn = get_connection()
    try:
        group, recipients = sms_groups.resolve_group(conn, group_id)
        if not group:
            return jsonify({"error": "not found"}), 404
        overrides = conn.execute(
            "SELECT id, member_id, phone, contact_name, mode FROM sms_group_members WHERE group_id = ? ORDER BY contact_name",
            (group_id,),
        ).fetchall()
        recipients.sort(key=lambda r: (r["contact_name"] or "").lower())
        return jsonify({
            "recipient_count": len(recipients),
            "recipients": recipients,
            "overrides": [dict(o) for o in overrides],
        })
    finally:
        conn.close()


@sms_bp.route("/groups/<int:group_id>/members", methods=["POST"])
@_require_key
def add_group_member(group_id):
    data = request.get_json(force=True) or {}
    phone = normalize_phone((data.get("phone") or "").strip())
    if not phone:
        return jsonify({"error": "a valid phone is required"}), 400

    conn = get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sms_groups WHERE id = ?", (group_id,)).fetchone():
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "INSERT OR REPLACE INTO sms_group_members (group_id, member_id, phone, contact_name, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (group_id, data.get("member_id"), phone, (data.get("name") or "").strip() or None,
             "exclude" if data.get("mode") == "exclude" else "include"),
        )
        conn.commit()
        _, recipients = sms_groups.resolve_group(conn, group_id)
        return jsonify({"ok": True, "recipient_count": len(recipients)}), 201
    finally:
        conn.close()


@sms_bp.route("/groups/<int:group_id>/members/<int:override_id>", methods=["DELETE"])
@_require_key
def delete_group_member(group_id, override_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sms_group_members WHERE id = ? AND group_id = ?", (override_id, group_id))
        conn.commit()
        _, recipients = sms_groups.resolve_group(conn, group_id)
        return jsonify({"ok": True, "recipient_count": len(recipients)})
    finally:
        conn.close()


def _broadcast_dict(row) -> dict:
    return {
        "id": row["id"],
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "body": row["body"],
        "send_at": row["send_at"],
        "status": row["status"],
        "recipient_count": row["recipient_count"],
        "sent_count": row["sent_count"],
        "failed_count": row["failed_count"],
        "spread_hours": row["spread_hours"],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


@sms_bp.route("/broadcasts", methods=["GET"])
@_require_key
def list_broadcasts():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sms_broadcasts ORDER BY created_at DESC LIMIT 50").fetchall()
        return jsonify({"broadcasts": [_broadcast_dict(r) for r in rows]})
    finally:
        conn.close()


@sms_bp.route("/broadcasts/<int:broadcast_id>", methods=["GET"])
@_require_key
def get_broadcast(broadcast_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        recipients = conn.execute(
            "SELECT * FROM sms_broadcast_recipients WHERE broadcast_id = ? ORDER BY contact_name",
            (broadcast_id,),
        ).fetchall()
        return jsonify({"broadcast": _broadcast_dict(row), "recipients": [dict(r) for r in recipients]})
    finally:
        conn.close()


@sms_bp.route("/broadcasts", methods=["POST"])
@_require_key
def create_broadcast():
    """The confirm step: resolves the group (a saved group_id, or an ad-hoc
    filter/manual list) RIGHT NOW and freezes that exact recipient list
    into sms_broadcast_recipients -- a later membership change never
    silently changes who an already-scheduled broadcast reaches. The
    frontend is expected to have shown this same resolution via
    /groups/preview or /groups/<id>/members before calling this."""
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    send_at = (data.get("send_at") or "").strip()
    if not body:
        return jsonify({"error": "body is required"}), 400
    error = _validate_send_at(send_at)
    if error:
        return jsonify({"error": error}), 400

    group_id = data.get("group_id")
    conn = get_connection()
    try:
        if group_id:
            group, recipients = sms_groups.resolve_group(conn, group_id)
            if not group:
                return jsonify({"error": "group not found"}), 404
            group_name = group["name"]
        else:
            recipients = _resolve_adhoc(
                data.get("filter") or {}, bool(data.get("active_only", True)), data.get("manual") or [],
                manual_only=bool(data.get("manual_only")),
            )
            group_name = (data.get("group_name") or "").strip() or None

        # One-off removals from the reviewed list (e.g. Bill tapped "remove"
        # on someone in the confirm screen) -- applies on top of either
        # branch above without touching a saved group's own membership.
        exclude_phones = {normalize_phone(p) for p in (data.get("exclude_phones") or []) if normalize_phone(p)}
        if exclude_phones:
            recipients = [r for r in recipients if r["phone"] not in exclude_phones]

        if not recipients:
            return jsonify({"error": "resolved group has no recipients (no phone on file, or all filtered out)"}), 400

        spread_hours = data.get("spread_hours")
        try:
            spread_hours = float(spread_hours) if spread_hours else None
        except (TypeError, ValueError):
            spread_hours = None

        cur = conn.execute(
            "INSERT INTO sms_broadcasts (group_id, group_name, body, send_at, recipient_count, spread_hours) VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, group_name, body, send_at, len(recipients), spread_hours),
        )
        broadcast_id = cur.lastrowid

        # Shuffle send order and give each recipient their own randomized
        # send time (jobs/sms/broadcast_pacing.py) -- a single device
        # texting many numbers the instant a broadcast is due, in list
        # order, is exactly the burst pattern carrier anti-spam filters
        # flag; this makes it look like Bill individually texting people
        # one at a time instead.
        shuffled = list(recipients)
        random.shuffle(shuffled)
        staggered_times = broadcast_pacing.stagger_send_times(send_at, len(shuffled), spread_hours=spread_hours)

        conn.executemany(
            "INSERT INTO sms_broadcast_recipients (broadcast_id, member_id, phone, contact_name, send_at) VALUES (?, ?, ?, ?, ?)",
            [(broadcast_id, r["member_id"], r["phone"], r["contact_name"], t) for r, t in zip(shuffled, staggered_times)],
        )
        conn.commit()

        row = conn.execute("SELECT * FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        return jsonify({"broadcast": _broadcast_dict(row)}), 201
    finally:
        conn.close()


@sms_bp.route("/broadcasts/<int:broadcast_id>", methods=["DELETE"])
@_require_key
def cancel_broadcast(broadcast_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM sms_broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        if row["status"] != "scheduled":
            return jsonify({"error": f"cannot cancel a broadcast that is already {row['status']}"}), 409
        conn.execute(
            "UPDATE sms_broadcasts SET status = 'canceled', completed_at = datetime('now') WHERE id = ?",
            (broadcast_id,),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()
