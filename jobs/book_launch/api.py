"""jobs/book_launch/api.py — Flask Blueprint for pre-launch "notify me" book
signup forms (wcky /<book-slug> landing page → Watson → watson.db → Brevo).

Distinct from jobs/lead_magnet/api.py: lead magnets deliver a PDF to an
existing book's readers; this handles pre-launch interest signups for a
book that hasn't shipped yet (no PDF, no confirmation email — just a Brevo
list tag and a Telegram heads-up to Bill).

Mount on the Watson dashboard app:
    from jobs.book_launch.api import book_launch_bp
    app.register_blueprint(book_launch_bp)

Reusable-template design: adding a future book means an INSERT into the
book_launches table (slug, title, active) — no new routes, no new code.
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

book_launch_bp = Blueprint("book_launch", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")

_BREVO_BASE = "https://api.brevo.com/v3"
_BREVO_LEAD_MAGNET_FOLDER_ID = lambda: int(os.getenv("BREVO_LEAD_MAGNET_FOLDER_ID", "1"))
_BOOK_LAUNCH_LIST_PREFIX = "Book Launch: "

_SEED_BOOKS = [
    # (slug, title, active)
    ("guardrails", "GUARDRAILS", 1),
]


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
            CREATE TABLE IF NOT EXISTS book_launches (
                slug       TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS book_launch_signups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                slug         TEXT NOT NULL REFERENCES book_launches(slug),
                name         TEXT,
                email        TEXT NOT NULL,
                brevo_tagged INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (slug, email)
            )
        """)
        conn.commit()

        for slug, title, active in _SEED_BOOKS:
            conn.execute(
                "INSERT OR IGNORE INTO book_launches (slug, title, active) VALUES (?, ?, ?)",
                (slug, title, active),
            )
        conn.commit()
    finally:
        conn.close()


def _brevo_headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": os.getenv("BREVO_API_KEY", ""),
    }


def _get_or_create_brevo_list(slug: str) -> int | None:
    """Resolve the "Book Launch: {slug}" Brevo list, creating it under
    _BREVO_LEAD_MAGNET_FOLDER_ID if it doesn't exist yet."""
    if not os.getenv("BREVO_API_KEY"):
        log.warning("BREVO_API_KEY not set — skipping Brevo list for %s", slug)
        return None
    name = f"{_BOOK_LAUNCH_LIST_PREFIX}{slug}"
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
                if l["name"] == name:
                    return l["id"]
            if len(lists) < 50:
                break
            offset += 50

        resp = requests.post(
            f"{_BREVO_BASE}/contacts/lists",
            json={"name": name, "folderId": _BREVO_LEAD_MAGNET_FOLDER_ID()},
            headers=_brevo_headers(), timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as exc:
        log.error("Brevo list resolve/create failed for %s: %s", slug, exc)
        return None


def _brevo_tag_subscriber(email: str, name: str, list_id: int | None) -> bool:
    """Upsert the subscriber in Brevo with the book's launch list, if
    resolved. Returns True on success."""
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

@book_launch_bp.route("/api/book-launch/subscribe", methods=["POST"])
@_require_key
def book_launch_subscribe():
    _ensure_table()
    data  = request.get_json(force=True) or {}
    slug  = (data.get("slug") or "").strip()
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not (slug and email):
        return jsonify({"error": "slug and email are required"}), 400

    conn = get_db()
    try:
        book = conn.execute(
            "SELECT slug, title, active FROM book_launches WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not book or not book["active"]:
            return jsonify({"error": "not found"}), 404

        existing = conn.execute(
            "SELECT id FROM book_launch_signups WHERE slug = ? AND email = ?",
            (slug, email),
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already registered"}), 200

        cursor = conn.execute(
            "INSERT INTO book_launch_signups (slug, name, email) VALUES (?, ?, ?)",
            (slug, name or None, email),
        )
        signup_id = cursor.lastrowid
        conn.commit()
    except Exception as exc:
        log.error("Book launch signup insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    # Brevo tagging is a "nice to have" — never allowed to block the
    # response above from having already succeeded.
    list_id = _get_or_create_brevo_list(slug)
    tagged = _brevo_tag_subscriber(email, name, list_id)
    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE book_launch_signups SET brevo_tagged = 1 WHERE id = ?",
                (signup_id,),
            )
            conn2.commit()
        finally:
            conn2.close()

    try:
        send_telegram(
            f"\U0001F4D6 New Book Launch Signup\n\n"
            f"Book: {book['title']}\n"
            f"Name: {name or '(none given)'}\n"
            f"Email: {email}\n"
            f"Brevo tag: {'✅ applied' if tagged else '⚠️ not applied — check BREVO_API_KEY'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for book launch signup %s: %s", email, exc)

    return jsonify({"ok": True}), 200
