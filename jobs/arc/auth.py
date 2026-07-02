"""jobs/arc/auth.py — ARC reader session auth + dashboard/commitment routes.

Mount on the Watson dashboard app:
    from jobs.arc.auth import arc_auth_bp
    app.register_blueprint(arc_auth_bp)
"""
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import bcrypt
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.arc.api import _COMMITMENT_TEXTS
from jobs.writing_room import get_db

log = logging.getLogger(__name__)

arc_auth_bp = Blueprint("arc_auth", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")
_SESSION_DAYS = 30


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _get_session_reader(session_token: str):
    """Return arc_readers row for a valid, non-expired session token, or None."""
    if not session_token:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT s.arc_reader_id, s.expires_at, "
            "r.id, r.first_name, r.last_name, r.email, r.first_login_at "
            "FROM arc_sessions s "
            "JOIN arc_readers r ON r.id = s.arc_reader_id "
            "WHERE s.session_token = ?",
            (session_token,),
        ).fetchone()
        if not row:
            return None
        if datetime.utcnow().isoformat() > row["expires_at"]:
            return None
        return row
    finally:
        conn.close()


def _require_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Arc-Session", "")
        reader = _get_session_reader(token)
        if not reader:
            return jsonify({"error": "session required"}), 401
        return f(*args, reader=reader, **kwargs)
    return wrapper


# ── POST /api/arc/login ───────────────────────────────────────────────────────

@arc_auth_bp.route("/api/arc/login", methods=["POST"])
@_require_key
def arc_login():
    data     = request.get_json(force=True)
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not (email and password):
        return jsonify({"error": "email and password required"}), 400

    conn = get_db()
    try:
        reader = conn.execute(
            "SELECT id, first_name, last_name, email, password_hash, first_login_at, status "
            "FROM arc_readers WHERE email = ?",
            (email,),
        ).fetchone()
        if not reader or not reader["password_hash"]:
            return jsonify({"error": "invalid credentials"}), 401
        if reader["status"] != "active":
            return jsonify({"error": "invalid credentials"}), 401
        if not bcrypt.checkpw(password.encode(), reader["password_hash"].encode()):
            return jsonify({"error": "invalid credentials"}), 401

        # Issue session token
        token      = secrets.token_urlsafe(48)
        expires_at = (datetime.utcnow() + timedelta(days=_SESSION_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO arc_sessions (arc_reader_id, session_token, expires_at) VALUES (?, ?, ?)",
            (reader["id"], token, expires_at),
        )

        # Record first login
        if not reader["first_login_at"]:
            conn.execute(
                "UPDATE arc_readers SET first_login_at = datetime('now') WHERE id = ?",
                (reader["id"],),
            )

        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "session_token": token,
        "reader_id": reader["id"],
        "first_name": reader["first_name"],
        "last_name": reader["last_name"],
        "email": reader["email"],
    }), 200


# ── GET /api/arc/dashboard ────────────────────────────────────────────────────

@arc_auth_bp.route("/api/arc/dashboard", methods=["GET"])
@_require_key
@_require_session
def arc_dashboard(reader):
    conn = get_db()
    try:
        commitments = conn.execute(
            "SELECT id, commitment_number, commitment_text, is_checked, evidence_text, "
            "submitted_at, flagged_as_suspicious, approved_by_admin "
            "FROM arc_reader_commitments WHERE arc_reader_id = ? ORDER BY commitment_number",
            (reader["id"],),
        ).fetchall()
        checked_count = sum(1 for c in commitments if c["is_checked"])
        return jsonify({
            "reader": {
                "id": reader["id"],
                "first_name": reader["first_name"],
                "last_name": reader["last_name"],
                "email": reader["email"],
            },
            "commitments": [dict(c) for c in commitments],
            "progress": {"checked": checked_count, "total": len(_COMMITMENT_TEXTS)},
        }), 200
    finally:
        conn.close()


# ── POST /api/arc/commitments ─────────────────────────────────────────────────

@arc_auth_bp.route("/api/arc/commitments", methods=["POST"])
@_require_key
@_require_session
def arc_update_commitments(reader):
    data    = request.get_json(force=True)
    updates = data.get("updates") or []

    if not isinstance(updates, list) or not updates:
        return jsonify({"error": "updates array required"}), 400

    conn = get_db()
    try:
        for upd in updates:
            commitment_number = upd.get("commitment_number")
            if commitment_number is None:
                continue

            row = conn.execute(
                "SELECT id, commitment_number FROM arc_reader_commitments "
                "WHERE arc_reader_id = ? AND commitment_number = ?",
                (reader["id"], commitment_number),
            ).fetchone()
            if not row:
                continue

            is_checked    = 1 if upd.get("is_checked") else 0
            evidence_text = (upd.get("evidence_text") or "").strip() or None

            # Commitments 4-6 require evidence_text to be checked
            if commitment_number in {3, 4, 5} and is_checked and not evidence_text:
                return jsonify({
                    "error": f"commitment {commitment_number} requires evidence_text"
                }), 400

            conn.execute(
                "UPDATE arc_reader_commitments "
                "SET is_checked = ?, evidence_text = ?, submitted_at = datetime('now') "
                "WHERE id = ?",
                (is_checked, evidence_text, row["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True}), 200


# ── POST /api/arc/feedback ────────────────────────────────────────────────────

@arc_auth_bp.route("/api/arc/feedback", methods=["POST"])
@_require_key
@_require_session
def arc_feedback(reader):
    data        = request.get_json(force=True)
    target_type = (data.get("target_type") or "").strip()
    target_slug = (data.get("target_slug") or "").strip()
    if not (target_type and target_slug):
        return jsonify({"error": "target_type and target_slug are required"}), 400
    reaction = (data.get("reaction") or "").strip() or None
    comment  = (data.get("comment") or "").strip() or None
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO arc_reader_feedback "
            "(arc_reader_id, target_type, target_slug, reaction, comment) "
            "VALUES (?, ?, ?, ?, ?)",
            (reader["id"], target_type, target_slug, reaction, comment),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True}), 200
