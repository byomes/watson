"""jobs/writing_room/api.py — Flask Blueprint for wcky site → Watson DB.

Mount on the Watson dashboard app:
    from jobs.writing_room.api import writing_room_bp
    app.register_blueprint(writing_room_bp)
"""
import logging
import os
import sys
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import bcrypt
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import bootstrap_db, get_db, send_telegram
from jobs.writing_room.onboard import (
    alert_new_application, kit_tag_on_activation, process_approval, process_denial,
)
from jobs.writing_room.reset import confirm_reset, request_reset, validate_token

log = logging.getLogger(__name__)

writing_room_bp = Blueprint("writing_room", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Login ─────────────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/login", methods=["POST"])
@_require_key
def login():
    data     = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not (username and password):
        return jsonify({"error": "username and password required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, username, password_hash, status FROM writing_room_partners "
            "WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return jsonify({"error": "invalid credentials"}), 401
        if row["status"] == "approved":
            return jsonify({"error": "pending_verification"}), 403
        if row["status"] != "active":
            return jsonify({"error": "invalid credentials"}), 401
        if not row["password_hash"]:
            return jsonify({"error": "invalid credentials"}), 401
        if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return jsonify({"error": "invalid credentials"}), 401
        conn.execute(
            "UPDATE writing_room_partners SET last_active = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return jsonify({"partnerId": row["id"], "name": row["name"], "username": row["username"]}), 200
    finally:
        conn.close()


# ── Signup ────────────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/signup", methods=["POST"])
@_require_key
def signup():
    data = request.get_json(force=True)
    name    = (data.get("name") or "").strip()
    email   = (data.get("email") or "").strip().lower()
    why     = (data.get("why_join") or "").strip()
    faith   = (data.get("faith_description") or "").strip()
    agreed  = 1 if data.get("agreed_to_participate") else 0

    if not (name and email and why):
        return jsonify({"error": "name, email, and why_join are required"}), 400

    conn = get_db()
    try:
        # Block repeat applications from denied/revoked emails
        existing = conn.execute(
            "SELECT status FROM writing_room_partners WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            status = existing["status"]
            if status in ("denied", "revoked"):
                return jsonify({"ok": True, "message": "application received"}), 200
            return jsonify({"error": "email already registered"}), 409

        cursor = conn.execute(
            "INSERT INTO writing_room_partners (name, email, why_join, faith_description, agreed_to_participate, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (name, email, why, faith or None, agreed),
        )
        partner_id = cursor.lastrowid
        conn.commit()
    except Exception as exc:
        log.error("Signup insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    try:
        alert_new_application(partner_id)
    except Exception as exc:
        log.error("Alert failed for partner %d: %s", partner_id, exc)

    return jsonify({"ok": True, "message": "application received"}), 200


# ── Posts ─────────────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/post", methods=["POST"])
@_require_key
def new_post():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    section    = data.get("section", "board")
    content    = (data.get("content") or "").strip()

    if not (partner_id and content):
        return jsonify({"error": "partner_id and content required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO writing_room_posts (partner_id, section, content) VALUES (?, ?, ?)",
            (partner_id, section, content),
        )
        conn.commit()
        return jsonify({"ok": True, "post_id": cursor.lastrowid}), 200
    finally:
        conn.close()


@writing_room_bp.route("/api/writing-room/reply", methods=["POST"])
@_require_key
def new_reply():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    parent_id  = data.get("parent_id")
    section    = data.get("section", "board")
    content    = (data.get("content") or "").strip()

    if not (partner_id and parent_id and content):
        return jsonify({"error": "partner_id, parent_id, and content required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO writing_room_posts (partner_id, section, parent_id, content) VALUES (?, ?, ?, ?)",
            (partner_id, section, parent_id, content),
        )
        conn.commit()
        return jsonify({"ok": True, "post_id": cursor.lastrowid}), 200
    finally:
        conn.close()


# ── Beta Feedback ─────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/feedback", methods=["POST"])
@_require_key
def new_feedback():
    data        = request.get_json(force=True)
    partner_id  = data.get("partner_id")
    target_type = data.get("target_type")
    target_slug = data.get("target_slug")
    reaction    = data.get("reaction")
    comment     = (data.get("comment") or "").strip() or None

    if not (partner_id and target_type and target_slug):
        return jsonify({"error": "partner_id, target_type, target_slug required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO writing_room_beta_feedback "
            "(partner_id, target_type, target_slug, reaction, comment) VALUES (?, ?, ?, ?, ?)",
            (partner_id, target_type, target_slug, reaction, comment),
        )
        conn.commit()
        return jsonify({"ok": True, "feedback_id": cursor.lastrowid}), 200
    finally:
        conn.close()


# ── Messages ──────────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/message", methods=["POST"])
@_require_key
def new_message():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    name       = (data.get("name") or "").strip()
    email      = (data.get("email") or "").strip()
    message    = (data.get("message") or "").strip()

    if not (name and email and message):
        return jsonify({"error": "name, email, message required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO writing_room_messages (partner_id, name, email, message) VALUES (?, ?, ?, ?)",
            (partner_id, name, email, message),
        )
        conn.commit()
        return jsonify({"ok": True, "message_id": cursor.lastrowid}), 200
    finally:
        conn.close()


# ── Calls ─────────────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/call", methods=["POST"])
@_require_key
def new_call():
    data         = request.get_json(force=True)
    title        = (data.get("title") or "").strip()
    scheduled_at = data.get("scheduled_at")
    meeting_url  = data.get("meeting_url")

    if not (title and scheduled_at):
        return jsonify({"error": "title and scheduled_at required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO writing_room_calls (title, scheduled_at, meeting_url) VALUES (?, ?, ?)",
            (title, scheduled_at, meeting_url),
        )
        conn.commit()
        return jsonify({"ok": True, "call_id": cursor.lastrowid}), 200
    finally:
        conn.close()


# ── Read: posts, calls, messages ──────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/posts", methods=["GET"])
@_require_key
def get_posts():
    section = request.args.get("section", "board")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT p.id, p.partner_id, p.section, p.parent_id, p.content, p.flagged, p.created_at, "
            "       pr.name AS partner_name "
            "FROM writing_room_posts p "
            "LEFT JOIN writing_room_partners pr ON p.partner_id = pr.id "
            "WHERE p.section = ? "
            "ORDER BY p.created_at ASC",
            (section,),
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@writing_room_bp.route("/api/writing-room/calls", methods=["GET"])
@_require_key
def get_calls():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM writing_room_calls ORDER BY scheduled_at ASC"
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@writing_room_bp.route("/api/writing-room/messages", methods=["GET"])
@_require_key
def get_messages():
    limit = int(request.args.get("limit", 20))
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM writing_room_messages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


# ── Partners (admin) ──────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/partners", methods=["GET"])
@_require_key
def list_partners():
    status_filter = request.args.get("status")
    conn = get_db()
    _cols = "id, name, email, username, status, joined_at, last_active, why_join, faith_description, agreed_to_participate, created_at"
    try:
        if status_filter:
            rows = conn.execute(
                f"SELECT {_cols} FROM writing_room_partners WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_cols} FROM writing_room_partners ORDER BY created_at DESC"
            ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


# ── Password Reset ────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/reset-request", methods=["POST"])
@_require_key
def reset_request():
    data  = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email required"}), 400
    request_reset(email)  # silent on unknown email
    return jsonify({"ok": True}), 200


@writing_room_bp.route("/api/writing-room/reset-validate", methods=["GET"])
@_require_key
def reset_validate():
    token = request.args.get("token", "")
    pid   = validate_token(token)
    if pid is None:
        return jsonify({"valid": False}), 200
    return jsonify({"valid": True}), 200


@writing_room_bp.route("/api/writing-room/reset-confirm", methods=["POST"])
@_require_key
def reset_confirm():
    data     = request.get_json(force=True)
    token    = data.get("token", "")
    password = data.get("password", "")
    if not (token and password):
        return jsonify({"error": "token and password required"}), 400
    ok = confirm_reset(token, password)
    if not ok:
        return jsonify({"error": "invalid or expired token"}), 400
    return jsonify({"ok": True}), 200


# ── Email Verification ────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/verify-send", methods=["POST"])
@_require_key
def verify_send():
    import secrets as _secrets
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT name, email FROM writing_room_partners WHERE id = ? AND status = 'approved'",
            (partner_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "partner not found or not in approved status"}), 404

        token      = _secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=72)).isoformat()
        conn.execute(
            "INSERT INTO writing_room_verify_tokens (partner_id, token, expires_at) VALUES (?, ?, ?)",
            (partner_id, token, expires_at),
        )
        conn.commit()

        from jobs.writing_room.onboard import send_verification_email
        first_name = row["name"].split()[0]
        send_verification_email(row["email"], first_name, token)
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@writing_room_bp.route("/api/writing-room/verify-validate", methods=["GET"])
@_require_key
def verify_validate():
    token = request.args.get("token", "")
    if not token:
        return jsonify({"valid": False}), 200

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT vt.partner_id, vt.expires_at, p.name "
            "FROM writing_room_verify_tokens vt "
            "JOIN writing_room_partners p ON vt.partner_id = p.id "
            "WHERE vt.token = ? AND vt.used = 0",
            (token,),
        ).fetchone()
        if not row:
            return jsonify({"valid": False}), 200
        if datetime.utcnow().isoformat() > row["expires_at"]:
            return jsonify({"valid": False}), 200
        return jsonify({"valid": True, "partner_id": row["partner_id"], "name": row["name"]}), 200
    finally:
        conn.close()


@writing_room_bp.route("/api/writing-room/verify-confirm", methods=["POST"])
@_require_key
def verify_confirm():
    data     = request.get_json(force=True)
    token    = (data.get("token") or "").strip()
    password = data.get("password") or ""

    if not (token and password):
        return jsonify({"error": "token and password required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT vt.id, vt.partner_id, vt.expires_at, p.name, p.email "
            "FROM writing_room_verify_tokens vt "
            "JOIN writing_room_partners p ON vt.partner_id = p.id "
            "WHERE vt.token = ? AND vt.used = 0",
            (token,),
        ).fetchone()
        if not row:
            return jsonify({"error": "invalid or expired token"}), 400
        if datetime.utcnow().isoformat() > row["expires_at"]:
            return jsonify({"error": "invalid or expired token"}), 400

        pw_hash   = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        joined_at = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE writing_room_partners SET password_hash = ?, status = 'active', joined_at = ? "
            "WHERE id = ?",
            (pw_hash, joined_at, row["partner_id"]),
        )
        conn.execute(
            "UPDATE writing_room_verify_tokens SET used = 1 WHERE id = ?", (row["id"],)
        )
        conn.commit()

        try:
            first_name = row["name"].split()[0]
            kit_tag_on_activation(row["email"], first_name)
        except Exception as exc:
            log.warning("Kit tag failed for partner %d: %s", row["partner_id"], exc)

        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── Admin Actions ─────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/approve", methods=["POST"])
@_require_key
def approve():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    try:
        process_approval(int(partner_id))
    except Exception as exc:
        log.error("Approval failed for partner %s: %s", partner_id, exc)
        return jsonify({"error": "approval failed"}), 500
    return jsonify({"ok": True}), 200


@writing_room_bp.route("/api/writing-room/deny", methods=["POST"])
@_require_key
def deny():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    try:
        process_denial(int(partner_id))
    except Exception as exc:
        log.error("Denial failed for partner %s: %s", partner_id, exc)
        return jsonify({"error": "denial failed"}), 500
    return jsonify({"ok": True}), 200


@writing_room_bp.route("/api/writing-room/revoke", methods=["POST"])
@_require_key
def revoke():
    data       = request.get_json(force=True)
    partner_id = data.get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "UPDATE writing_room_partners SET status = 'revoked' WHERE id = ?", (partner_id,)
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── Call delete ───────────────────────────────────────────────────────────────

@writing_room_bp.route("/api/writing-room/call/<int:call_id>", methods=["DELETE"])
@_require_key
def delete_call(call_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM writing_room_calls WHERE id = ?", (call_id,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()
