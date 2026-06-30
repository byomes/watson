"""jobs/arc/api.py — Flask Blueprint for ARC reader signups → watson.db.

Mount on the Watson dashboard app:
    from jobs.arc.api import arc_bp
    app.register_blueprint(arc_bp)

Future: a completion-check job will query arc_readers WHERE status = 'active'
and verify each reader's commitments; readers who finish all six move to
status = 'complete', then get a Writing Room invitation (status = 'invited').
The status field is intentionally designed to support this pipeline.
"""
import logging
import os
import sys
from functools import wraps
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import get_db, send_telegram

log = logging.getLogger(__name__)

arc_bp = Blueprint("arc", __name__)

_API_KEY    = lambda: os.getenv("WRITING_ROOM_API_KEY", "")
_KIT_SECRET = lambda: os.getenv("KIT_API_SECRET", "")
_ARC_TAG_ID = 19285341  # Kit tag applied to every ARC signup


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _ensure_table() -> None:
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_readers (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name            TEXT NOT NULL,
                last_name             TEXT NOT NULL,
                email                 TEXT NOT NULL UNIQUE,
                book_slug             TEXT NOT NULL DEFAULT 'the-wrong-jesus',
                agreed_to_commitments INTEGER NOT NULL DEFAULT 0,
                status                TEXT NOT NULL DEFAULT 'active',
                kit_tag_applied       INTEGER NOT NULL DEFAULT 0,
                created_at            TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _kit_tag_subscriber(email: str, first_name: str, last_name: str) -> bool:
    """Apply Kit tag _ARC_TAG_ID to the subscriber via Kit v3. Returns True on success."""
    secret = _KIT_SECRET()
    if not secret:
        log.warning("KIT_API_SECRET not set — skipping Kit tag for %s", email)
        return False
    try:
        resp = requests.post(
            f"https://api.convertkit.com/v3/tags/{_ARC_TAG_ID}/subscribe",
            json={
                "api_secret": secret,
                "first_name": first_name,
                "email": email,
                "fields": {"last_name": last_name},
            },
            timeout=10,
        )
        if resp.ok:
            return True
        log.warning("Kit tag apply failed (%s): %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("Kit tag request error for %s: %s", email, exc)
        return False


@arc_bp.route("/api/arc/apply", methods=["POST"])
@_require_key
def arc_apply():
    _ensure_table()
    data       = request.get_json(force=True)
    first_name = (data.get("firstName") or "").strip()
    last_name  = (data.get("lastName") or "").strip()
    email      = (data.get("email") or "").strip().lower()

    if not (first_name and last_name and email):
        return jsonify({"error": "firstName, lastName, and email are required"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM arc_readers WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already registered"}), 200

        cursor = conn.execute(
            "INSERT INTO arc_readers (first_name, last_name, email, agreed_to_commitments) "
            "VALUES (?, ?, ?, 1)",
            (first_name, last_name, email),
        )
        reader_id = cursor.lastrowid
        conn.commit()
    except Exception as exc:
        log.error("ARC reader insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    tagged = _kit_tag_subscriber(email, first_name, last_name)

    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE arc_readers SET kit_tag_applied = 1 WHERE id = ?", (reader_id,)
            )
            conn2.commit()
        finally:
            conn2.close()

    try:
        send_telegram(
            f"📖 New ARC Reader\n\n"
            f"Name: {first_name} {last_name}\n"
            f"Email: {email}\n"
            f"Book: The Wrong Jesus\n"
            f"Kit tag: {'✅ applied' if tagged else '⚠️ failed — check KIT_API_SECRET'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for ARC reader %s: %s", email, exc)

    return jsonify({"ok": True}), 200
