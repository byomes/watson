"""jobs/newsletter/api.py — Flask Blueprint for the general homepage
"stay updated" email capture (wcky home hero → Watson → watson.db → Brevo).

Distinct from jobs/lead_magnet/api.py (delivers a specific book's PDF) and
jobs/book_launch/api.py (notify-me for one specific forthcoming book):
this is a single, general-purpose mailing list with no book/slug tied to
it — just "keep me posted."

Mount on the Watson dashboard app:
    from jobs.newsletter.api import newsletter_bp
    app.register_blueprint(newsletter_bp)
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

newsletter_bp = Blueprint("newsletter", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")

_BREVO_BASE = "https://api.brevo.com/v3"
_BREVO_LEAD_MAGNET_FOLDER_ID = lambda: int(os.getenv("BREVO_LEAD_MAGNET_FOLDER_ID", "1"))
_NEWSLETTER_LIST_NAME = "Newsletter: Homepage"


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
            CREATE TABLE IF NOT EXISTS newsletter_signups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT,
                email        TEXT NOT NULL UNIQUE,
                brevo_tagged INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _brevo_headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": os.getenv("BREVO_API_KEY", ""),
    }


def _get_or_create_brevo_list() -> int | None:
    if not os.getenv("BREVO_API_KEY"):
        log.warning("BREVO_API_KEY not set — skipping Brevo list for newsletter")
        return None
    try:
        offset = 0
        while True:
            resp = requests.get(
                f"{_BREVO_BASE}/contacts/lists", params={"limit": 50, "offset": offset},
                headers=_brevo_headers(), timeout=10,
            )
            resp.raise_for_status()
            lists = resp.json().get("lists", [])
            for l in lists:
                if l["name"] == _NEWSLETTER_LIST_NAME:
                    return l["id"]
            if len(lists) < 50:
                break
            offset += 50

        resp = requests.post(
            f"{_BREVO_BASE}/contacts/lists",
            json={"name": _NEWSLETTER_LIST_NAME, "folderId": _BREVO_LEAD_MAGNET_FOLDER_ID()},
            headers=_brevo_headers(), timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as exc:
        log.error("Brevo list resolve/create failed for newsletter: %s", exc)
        return None


def _brevo_tag_subscriber(email: str, name: str, list_id: int | None) -> bool:
    if not os.getenv("BREVO_API_KEY"):
        log.warning("BREVO_API_KEY not set — skipping Brevo tag for %s", email)
        return False
    first_name = name.split()[0] if name else ""
    payload: dict = {"email": email, "updateEnabled": True}
    if first_name:
        payload["attributes"] = {"FIRSTNAME": first_name}
    if list_id is not None:
        payload["listIds"] = [list_id]
    try:
        resp = requests.post(
            f"{_BREVO_BASE}/contacts", json=payload, headers=_brevo_headers(), timeout=10,
        )
        if resp.status_code in (200, 201, 204):
            return True
        log.warning("Brevo contact upsert failed (%s): %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("Brevo contact upsert error for %s: %s", email, exc)
        return False


# ── Public: subscribe ──────────────────────────────────────────────────────────

@newsletter_bp.route("/api/newsletter/subscribe", methods=["POST"])
@_require_key
def newsletter_subscribe():
    _ensure_table()
    data  = request.get_json(force=True) or {}
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "email is required"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM newsletter_signups WHERE email = ?", (email,),
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already registered"}), 200

        cursor = conn.execute(
            "INSERT INTO newsletter_signups (name, email) VALUES (?, ?)",
            (name or None, email),
        )
        signup_id = cursor.lastrowid
        conn.commit()
    except Exception as exc:
        log.error("Newsletter signup insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    list_id = _get_or_create_brevo_list()
    tagged = _brevo_tag_subscriber(email, name, list_id)
    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE newsletter_signups SET brevo_tagged = 1 WHERE id = ?", (signup_id,),
            )
            conn2.commit()
        finally:
            conn2.close()

    try:
        send_telegram(
            f"\U0001F4EC New Newsletter Signup\n\n"
            f"Name: {name or '(none given)'}\n"
            f"Email: {email}\n"
            f"Brevo tag: {'✅ applied' if tagged else '⚠️ not applied — check BREVO_API_KEY'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for newsletter signup %s: %s", email, exc)

    return jsonify({"ok": True}), 200
