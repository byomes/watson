"""jobs/lead_magnet/api.py — Flask Blueprint for the book companion-guide
lead-magnet funnel (wcky /guide/[slug] → Watson → watson.db).

Mount on the Watson dashboard app:
    from jobs.lead_magnet.api import lead_magnet_bp
    app.register_blueprint(lead_magnet_bp)

Reusable-template design: adding a future book means an INSERT into the
lead_magnets table (slug, title, pdf_filename, kit_tag_id, active) — no
new routes, no new code.
"""
import logging
import os
import sys
import threading
from functools import wraps
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import get_db, send_telegram

log = logging.getLogger(__name__)

lead_magnet_bp = Blueprint("lead_magnet", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")

_BREVO_BASE = "https://api.brevo.com/v3"
_BREVO_LEAD_MAGNET_FOLDER_ID = lambda: int(os.getenv("BREVO_LEAD_MAGNET_FOLDER_ID", "1"))
_LEAD_MAGNET_LIST_PREFIX = "Lead Magnet: "

_SEED_MAGNETS = [
    # (slug, title, pdf_filename, kit_tag_id, active)
    ("wrong-jesus", "The Wrong Jesus", "wrong-jesus-companion-guide.pdf", None, 1),
    ("he-is-risen", "He Is Risen", "he-is-risen-study-guide.pdf", None, 1),
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
            CREATE TABLE IF NOT EXISTS lead_magnets (
                slug         TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                kit_tag_id   INTEGER,
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_magnet_signups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                slug            TEXT NOT NULL REFERENCES lead_magnets(slug),
                name            TEXT NOT NULL,
                email           TEXT NOT NULL,
                kit_tag_applied INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (slug, email)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_magnet_views (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                slug       TEXT NOT NULL REFERENCES lead_magnets(slug),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        for slug, title, pdf_filename, kit_tag_id, active in _SEED_MAGNETS:
            conn.execute(
                "INSERT OR IGNORE INTO lead_magnets "
                "(slug, title, pdf_filename, kit_tag_id, active) VALUES (?, ?, ?, ?, ?)",
                (slug, title, pdf_filename, kit_tag_id, active),
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
    """Resolve the "Lead Magnet: {slug}" Brevo list, creating it under
    _BREVO_LEAD_MAGNET_FOLDER_ID if it doesn't exist yet — keeps the
    reusable-template design (new book = new lead_magnets row, no new
    code) intact on the Brevo side too."""
    if not os.getenv("BREVO_API_KEY"):
        log.warning("BREVO_API_KEY not set — skipping Brevo list for %s", slug)
        return None
    name = f"{_LEAD_MAGNET_LIST_PREFIX}{slug}"
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
    """Upsert the subscriber in Brevo: COMPANION_GUIDE_READER attribute
    (cross-book segment) plus the book's lead-magnet list, if resolved.
    Returns True on success."""
    if not os.getenv("BREVO_API_KEY"):
        log.warning("BREVO_API_KEY not set — skipping Brevo tag for %s", email)
        return False
    first_name = name.split()[0] if name else ""
    attributes = {"COMPANION_GUIDE_READER": True}
    if first_name:
        attributes["FIRSTNAME"] = first_name
    payload = {"email": email, "updateEnabled": True, "attributes": attributes}
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


# ── Public: lead magnet lookup (SSR landing page) ─────────────────────────────

@lead_magnet_bp.route("/api/lead-magnet/<slug>", methods=["GET"])
@_require_key
def lead_magnet_lookup(slug):
    _ensure_table()
    conn = get_db()
    try:
        magnet = conn.execute(
            "SELECT slug, title, pdf_filename, active FROM lead_magnets WHERE slug = ?",
            (slug,),
        ).fetchone()
    finally:
        conn.close()

    if not magnet or not magnet["active"]:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "slug": magnet["slug"],
        "title": magnet["title"],
        "pdf_filename": magnet["pdf_filename"],
        "active": bool(magnet["active"]),
    }), 200


# ── Public: subscribe ──────────────────────────────────────────────────────────

@lead_magnet_bp.route("/api/lead-magnet/subscribe", methods=["POST"])
@_require_key
def lead_magnet_subscribe():
    _ensure_table()
    data  = request.get_json(force=True) or {}
    slug  = (data.get("slug") or "").strip()
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not (slug and name and email):
        return jsonify({"error": "slug, name, and email are required"}), 400

    conn = get_db()
    try:
        magnet = conn.execute(
            "SELECT slug, title, pdf_filename, active FROM lead_magnets WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not magnet or not magnet["active"]:
            return jsonify({"error": "not found"}), 404

        existing = conn.execute(
            "SELECT id FROM lead_magnet_signups WHERE slug = ? AND email = ?",
            (slug, email),
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already registered"}), 200

        cursor = conn.execute(
            "INSERT INTO lead_magnet_signups (slug, name, email) VALUES (?, ?, ?)",
            (slug, name, email),
        )
        signup_id = cursor.lastrowid
        conn.commit()
    except Exception as exc:
        log.error("Lead magnet signup insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    # Brevo tagging is a "nice to have" — never allowed to block the
    # confirmation email below, which is the reliable fallback path.
    list_id = _get_or_create_brevo_list(slug)
    tagged = _brevo_tag_subscriber(email, name, list_id)
    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE lead_magnet_signups SET kit_tag_applied = 1 WHERE id = ?",
                (signup_id,),
            )
            conn2.commit()
        finally:
            conn2.close()

    def _send_confirmation():
        try:
            from jobs.lead_magnet.send_confirmation import send_guide_confirmation
            send_guide_confirmation(email, name, magnet["title"], magnet["pdf_filename"])
        except Exception as exc:
            log.error("Guide confirmation email failed for %s: %s", email, exc)

    threading.Thread(target=_send_confirmation, daemon=True).start()

    try:
        send_telegram(
            f"\U0001F4D8 New Lead Magnet Signup\n\n"
            f"Book: {magnet['title']}\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Brevo tag: {'✅ applied' if tagged else '⚠️ not applied — check BREVO_API_KEY'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for lead magnet signup %s: %s", email, exc)

    return jsonify({"ok": True}), 200


# ── Public: view tracking ──────────────────────────────────────────────────────

@lead_magnet_bp.route("/api/lead-magnet/view", methods=["POST"])
@_require_key
def lead_magnet_view():
    _ensure_table()
    data = request.get_json(force=True) or {}
    slug = (data.get("slug") or "").strip()
    if not slug:
        return jsonify({"error": "slug required"}), 400

    conn = get_db()
    try:
        conn.execute("INSERT INTO lead_magnet_views (slug) VALUES (?)", (slug,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True}), 200


# ── Dashboard-internal: list + stats (no X-Watson-Key — same-origin only, ─────
# ── matching /api/members, /api/events, /api/reports/run) ────────────────────

@lead_magnet_bp.route("/api/lead-magnet/list", methods=["GET"])
def lead_magnet_list():
    _ensure_table()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT slug, title FROM lead_magnets WHERE active = 1 ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    return jsonify([dict(r) for r in rows]), 200


@lead_magnet_bp.route("/api/lead-magnet/stats/<slug>", methods=["GET"])
def lead_magnet_stats(slug):
    _ensure_table()
    conn = get_db()
    try:
        views = conn.execute(
            "SELECT COUNT(*) AS n FROM lead_magnet_views WHERE slug = ?", (slug,)
        ).fetchone()["n"]
        signups = conn.execute(
            "SELECT COUNT(*) AS n FROM lead_magnet_signups WHERE slug = ?", (slug,)
        ).fetchone()["n"]
    finally:
        conn.close()

    conversion_rate = round((signups / views * 100), 1) if views else 0.0

    return jsonify({
        "slug": slug,
        "views": views,
        "signups": signups,
        "conversion_rate": conversion_rate,
    }), 200
