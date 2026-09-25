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
import logging
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from core.database import get_connection
from jobs.sms import gateway_client
from jobs.sms.bridge import poll_inbound

log = logging.getLogger(__name__)

sms_bp = Blueprint("sms", __name__, url_prefix="/api/sms")

_API_KEY = lambda: os.getenv("SMS_APP_API_KEY", "")
_GATEWAY_MODE = lambda: os.getenv("GATEWAY_MODE", "mock").strip().lower()


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _thread_dict(row) -> dict:
    return {
        "id": row["id"],
        "phone": row["phone"],
        "contact_name": row["contact_name"],
        "last_message_preview": row["last_message_preview"],
        "last_message_at": row["last_message_at"],
        "unread": bool(row["unread"]),
    }


def _message_dict(row) -> dict:
    return {
        "id": row["id"],
        "direction": row["direction"],
        "body": row["body"],
        "created_at": row["created_at"],
    }


@sms_bp.route("/threads", methods=["GET"])
@_require_key
def list_threads():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sms_threads ORDER BY (last_message_at IS NULL), last_message_at DESC"
        ).fetchall()
        return jsonify({"threads": [_thread_dict(r) for r in rows]})
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

        return jsonify({
            "thread": _thread_dict(thread),
            "messages": [_message_dict(m) for m in messages],
        })
    finally:
        conn.close()


@sms_bp.route("/threads/<int:thread_id>/send", methods=["POST"])
@_require_key
def send_to_thread(thread_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = get_connection()
    try:
        thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (thread_id,)).fetchone()
        if not thread:
            return jsonify({"error": "not found"}), 404

        result = gateway_client.send_message(thread["phone"], text)
        if not result["success"]:
            return jsonify({"error": result.get("error") or "send failed"}), 502

        cur = conn.execute(
            "INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id) VALUES (?, 'out', ?, ?)",
            (thread_id, text, result.get("gateway_message_id")),
        )
        message_id = cur.lastrowid

        conn.execute(
            """UPDATE sms_threads
               SET last_message_at = datetime('now'),
                   last_message_preview = ?,
                   unread = 0
               WHERE id = ?""",
            (text, thread_id),
        )
        conn.commit()

        message = conn.execute("SELECT * FROM sms_messages WHERE id = ?", (message_id,)).fetchone()
        return jsonify({"message": _message_dict(message)})
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
