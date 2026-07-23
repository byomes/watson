"""jobs/links/api.py — Flask Blueprint for the branded link redirector.

Mount on the Watson dashboard app:
    from jobs.links.api import links_bp
    app.register_blueprint(links_bp)
"""
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, session

DB = os.path.expanduser("~/watson/data/watson.db")

links_bp = Blueprint("links", __name__)

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _require_admin_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _row_to_dict(row) -> dict:
    return {
        "slug": row["slug"],
        "destination": row["destination"],
        "clicks": row["clicks"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "notes": row["notes"],
    }


# ── Public, unauthenticated ─────────────────────────────────────────────────

@links_bp.route("/api/links/resolve/<slug>", methods=["GET"])
def resolve_link(slug):
    conn = _db()
    try:
        row = conn.execute(
            "SELECT destination FROM branded_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE branded_links SET clicks = clicks + 1 WHERE slug = ?", (slug,)
        )
        conn.commit()
        return jsonify({"destination": row["destination"]}), 200
    finally:
        conn.close()


# ── Authenticated (dashboard admin session) ─────────────────────────────────

@links_bp.route("/api/links", methods=["GET"])
@_require_admin_session
def list_links():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM branded_links ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows]), 200
    finally:
        conn.close()


@links_bp.route("/api/links", methods=["POST"])
@_require_admin_session
def create_link():
    data = request.get_json(force=True) or {}
    slug = (data.get("slug") or "").strip().lower()
    destination = (data.get("destination") or "").strip()
    notes = (data.get("notes") or "").strip() or None

    if not slug or not _SLUG_RE.match(slug):
        return jsonify({"error": "slug must be URL-safe (lowercase letters, numbers, hyphens)"}), 400
    if not destination:
        return jsonify({"error": "destination is required"}), 400

    conn = _db()
    try:
        existing = conn.execute(
            "SELECT slug FROM branded_links WHERE slug = ?", (slug,)
        ).fetchone()
        if existing:
            return jsonify({"error": "slug already exists"}), 409

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO branded_links (slug, destination, clicks, created_at, updated_at, notes) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (slug, destination, now, now, notes),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM branded_links WHERE slug = ?", (slug,)).fetchone()
        return jsonify(_row_to_dict(row)), 200
    finally:
        conn.close()


@links_bp.route("/api/links/<slug>", methods=["PUT"])
@_require_admin_session
def update_link(slug):
    data = request.get_json(force=True) or {}

    conn = _db()
    try:
        existing = conn.execute(
            "SELECT slug FROM branded_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        set_clauses = []
        values = []
        if "destination" in data:
            destination = (data["destination"] or "").strip()
            if not destination:
                return jsonify({"error": "destination cannot be empty"}), 400
            set_clauses.append("destination = ?")
            values.append(destination)
        if "notes" in data:
            set_clauses.append("notes = ?")
            values.append((data["notes"] or "").strip() or None)

        set_clauses.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(slug)

        conn.execute(
            f"UPDATE branded_links SET {', '.join(set_clauses)} WHERE slug = ?",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM branded_links WHERE slug = ?", (slug,)).fetchone()
        return jsonify(_row_to_dict(row)), 200
    finally:
        conn.close()


@links_bp.route("/api/links/<slug>", methods=["DELETE"])
@_require_admin_session
def delete_link(slug):
    conn = _db()
    try:
        conn.execute("DELETE FROM branded_links WHERE slug = ?", (slug,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()
