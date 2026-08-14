"""jobs/arc_interest/api.py — Flask Blueprint for the ARC waitlist signup
(wcky /arc/preview → Watson → watson.db).

Mount on the Watson dashboard app:
    from jobs.arc_interest.api import arc_interest_bp
    app.register_blueprint(arc_interest_bp)

Captures name + email only. Applies the same Kit tag as full ARC signups
(jobs.arc.api._ARC_TAG_ID) and stores a dedup row. Does NOT touch
arc_readers, issue login credentials, or send any credential email — see
jobs/arc/api.py for that (unrelated) flow.
"""
import logging
import os
import sys
from functools import wraps
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.arc.api import _ARC_TAG_ID
from jobs.writing_room import get_db, send_telegram

log = logging.getLogger(__name__)

arc_interest_bp = Blueprint("arc_interest", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")
_KIT_SECRET = lambda: os.getenv("KIT_API_SECRET", "")
# _ARC_TAG_ID (imported above) is intentionally the same Kit tag as full
# ARC signups — Bill's call: interest-only signups aren't meant to be
# distinguishable from full readers within Kit. arc_interest_signups vs
# arc_readers is where the real distinction lives.


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
            CREATE TABLE IF NOT EXISTS arc_interest_signups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                email           TEXT NOT NULL UNIQUE,
                kit_tag_applied INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _kit_tag_subscriber(tag_id: int, email: str, name: str) -> bool:
    """Apply the given Kit tag to the subscriber via Kit v3. Returns True on success."""
    secret = _KIT_SECRET()
    if not secret:
        log.warning("KIT_API_SECRET not set — skipping Kit tag for %s", email)
        return False
    try:
        resp = requests.post(
            f"https://api.convertkit.com/v3/tags/{tag_id}/subscribe",
            json={"api_secret": secret, "first_name": name, "email": email},
            timeout=10,
        )
        if resp.ok:
            return True
        log.warning("Kit tag apply failed (%s): %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("Kit tag request error for %s: %s", email, exc)
        return False


@arc_interest_bp.route("/api/arc-interest/signup", methods=["POST"])
@_require_key
def arc_interest_signup():
    _ensure_table()
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not (name and email):
        return jsonify({"error": "name and email are required"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM arc_interest_signups WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already on the list"}), 200

        conn.execute(
            "INSERT INTO arc_interest_signups (name, email) VALUES (?, ?)",
            (name, email),
        )
        conn.commit()
    except Exception as exc:
        log.error("ARC interest signup insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    # Kit tagging is a "nice to have" — guarded against a missing
    # KIT_API_SECRET (skips cleanly, logs, and never raises) and never
    # allowed to block the response above, which already recorded the
    # dedup row.
    tagged = _kit_tag_subscriber(_ARC_TAG_ID, email, name)
    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE arc_interest_signups SET kit_tag_applied = 1 WHERE email = ?",
                (email,),
            )
            conn2.commit()
        finally:
            conn2.close()

    try:
        send_telegram(
            f"\U0001F4DD New ARC Interest Signup\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Kit tag: {'✅ applied' if tagged else '⚠️ not applied — check KIT_API_SECRET'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for ARC interest signup %s: %s", email, exc)

    return jsonify({"ok": True}), 200
