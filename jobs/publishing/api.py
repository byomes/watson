"""jobs/publishing/api.py — Flask Blueprint for TWJ Reader admin (replaces watson-admin's
Next.js API routes). Writing Room and ARC admin routes already live in
jobs/writing_room/api.py and jobs/arc/api.py respectively — this module only adds
what watson-admin covered and nothing else did: TWJ reader management.

Mount on the Watson dashboard app:
    from jobs.publishing.api import publishing_bp
    app.register_blueprint(publishing_bp)
"""
import logging
import os
import sys
from functools import wraps
from pathlib import Path

import bcrypt
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.publishing import (
    generate_reader_password, generate_reader_username, get_db, set_reader_password,
)

log = logging.getLogger(__name__)

publishing_bp = Blueprint("publishing", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── TWJ Readers ────────────────────────────────────────────────────────────────

@publishing_bp.route("/api/publishing/twj/readers", methods=["GET"])
@_require_key
def list_readers():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, username, name, email, status, created_at, last_login "
            "FROM twj_readers ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/readers", methods=["POST"])
@_require_key
def add_reader():
    data       = request.get_json(force=True)
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name") or "").strip()
    email      = (data.get("email") or "").strip()

    if not (first_name and last_name and email):
        return jsonify({"error": "first_name, last_name, and email are required"}), 400

    conn = get_db()
    try:
        username = generate_reader_username(first_name, last_name, conn)
        password = generate_reader_password()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO twj_readers (username, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (username, f"{first_name} {last_name}", email, pw_hash),
        )
        conn.commit()
        return jsonify({"ok": True, "username": username, "password": password}), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/readers/bulk", methods=["POST"])
@_require_key
def bulk_add_readers():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "no file"}), 400

    text = file.read().decode("utf-8", errors="replace")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0] and "@" in parts[1]:
            rows.append((parts[0], parts[1]))

    if not rows:
        return jsonify({"error": "no valid rows parsed"}), 400

    csv_lines = ["username,password,name,email"]
    count = 0

    conn = get_db()
    try:
        for name, email in rows:
            name_parts = name.split()
            first_name = name_parts[0] if name_parts else "reader"
            last_name  = "".join(name_parts[1:]) or str(count + 1)

            username = generate_reader_username(first_name, last_name, conn)
            password = generate_reader_password()
            pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

            conn.execute(
                "INSERT INTO twj_readers (username, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (username, name, email, pw_hash),
            )
            csv_lines.append(f"{username},{password},{name},{email}")
            count += 1
        conn.commit()
        return jsonify({"ok": True, "count": count, "csv": "\n".join(csv_lines)}), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/readers/<int:reader_id>/reset-password", methods=["POST"])
@_require_key
def reset_reader_password(reader_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM twj_readers WHERE id = ?", (reader_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "reader not found"}), 404

    new_password = generate_reader_password()
    set_reader_password(reader_id, new_password)
    return jsonify({"ok": True, "password": new_password}), 200


@publishing_bp.route("/api/publishing/twj/readers/<int:reader_id>/revoke", methods=["POST"])
@_require_key
def revoke_reader(reader_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM twj_readers WHERE id = ?", (reader_id,)).fetchone()
        if not row:
            return jsonify({"error": "reader not found"}), 404
        conn.execute("UPDATE twj_readers SET status = 'revoked' WHERE id = ?", (reader_id,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── TWJ Reader-facing (login, session check, feedback submit) ──────────────────
# Called server-side by wcky's Next.js API routes, same X-Watson-Key pattern as
# every other route here — end readers never talk to Watson directly.

@publishing_bp.route("/api/publishing/twj/login", methods=["POST"])
@_require_key
def reader_login():
    data     = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not (username and password):
        return jsonify({"error": "username and password required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, email, password_hash, status FROM twj_readers WHERE username = ?",
            (username,),
        ).fetchone()
        if not row or row["status"] != "active":
            return jsonify({"error": "invalid credentials"}), 401
        if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return jsonify({"error": "invalid credentials"}), 401

        conn.execute(
            "UPDATE twj_readers SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],)
        )
        conn.commit()
        return jsonify({"ok": True, "username": username, "name": row["name"], "email": row["email"]}), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/reader/<username>", methods=["GET"])
@_require_key
def reader_session(username: str):
    """Session-cookie validation for /twj/read — the cookie just stores the username."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, name, email, status FROM twj_readers WHERE username = ?",
            (username,),
        ).fetchone()
        if not row or row["status"] != "active":
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row)), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/feedback/submit", methods=["POST"])
@_require_key
def submit_feedback():
    data     = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    chapter  = (data.get("chapter") or "").strip()
    text     = (data.get("text") or "").strip()
    if not (username and chapter and text):
        return jsonify({"error": "username, chapter, and text are required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM twj_readers WHERE username = ? AND status = 'active'", (username,)
        ).fetchone()
        if not row:
            return jsonify({"error": "unauthorized"}), 401
        conn.execute(
            "INSERT INTO twj_feedback (reader_id, chapter, feedback) VALUES (?, ?, ?)",
            (row["id"], chapter, text),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── TWJ Feedback (admin) ─────────────────────────────────────────────────────────

@publishing_bp.route("/api/publishing/twj/feedback", methods=["GET"])
@_require_key
def list_feedback():
    chapter = request.args.get("chapter")
    conn = get_db()
    try:
        query = (
            "SELECT f.id, f.chapter, f.feedback, f.created_at, "
            "r.username, r.name, r.email "
            "FROM twj_feedback f JOIN twj_readers r ON f.reader_id = r.id "
        )
        if chapter:
            rows = conn.execute(
                query + "WHERE f.chapter = ? ORDER BY f.created_at DESC", (chapter,)
            ).fetchall()
        else:
            rows = conn.execute(query + "ORDER BY f.created_at DESC").fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_bp.route("/api/publishing/twj/feedback/<int:feedback_id>", methods=["DELETE"])
@_require_key
def delete_feedback(feedback_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM twj_feedback WHERE id = ?", (feedback_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute("DELETE FROM twj_feedback WHERE id = ?", (feedback_id,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()
