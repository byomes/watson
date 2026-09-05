"""jobs/servantcare/servantcare_web.py — Flask Blueprint backing the
wtsn.me/p/servantcare family vacation-rental search tool.

Mount on the Watson dashboard app:
    from jobs.servantcare.servantcare_web import servantcare_web_bp
    app.register_blueprint(servantcare_web_bp)

Auth: data routes (/api/p/servantcare/...) require header X-Watson-Key
matching SERVANTCARE_API_KEY -- a dedicated key for this consumer, same
one-key-per-external-consumer convention as attendance_web.py. The photo
route (/p/servantcare/photos/<pid>/<filename>) is deliberately public and
unauthenticated -- a browser <img src> tag can't send a custom header --
gated instead by a strict filename regex, same pattern as
jobs/comms/api.py's serve_asset().
"""
import json
import os
import re
from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory

from jobs.servantcare.schema import get_connection
from jobs.servantcare.scraper import IMAGES_DIR

servantcare_web_bp = Blueprint("servantcare_web", __name__)

_API_KEY = lambda: os.getenv("SERVANTCARE_API_KEY", "")
_PID_RE = re.compile(r"^\d+$")
_FILENAME_RE = re.compile(r"^\d+\.(jpg|jpeg|png|webp|gif)$")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _row_to_summary(row: dict) -> dict:
    return {
        "pid": row["pid"],
        "name": row["name"],
        "city": row["city"],
        "state": row["state"],
        "bedrooms": row["bedrooms"],
        "bathrooms": row["bathrooms"],
        "max_sleeps": row["max_sleeps"],
        "price_summary": row["price_summary"],
        "source_url": row["source_url"],
        "primary_image_url": (
            f"/p/servantcare/photos/{row['pid']}/{os.path.basename(row['primary_image_path'])}"
            if row["primary_image_path"] else None
        ),
    }


@servantcare_web_bp.route("/api/p/servantcare/states", methods=["GET"])
@_require_key
def list_states():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM sc_listings GROUP BY state ORDER BY state"
        ).fetchall()
    return jsonify([{"state": r["state"], "count": r["n"]} for r in rows]), 200


@servantcare_web_bp.route("/api/p/servantcare/search", methods=["GET"])
@_require_key
def search():
    state = request.args.get("state", "").strip()
    min_bedrooms = request.args.get("min_bedrooms", "").strip()
    min_sleeps = request.args.get("min_sleeps", "").strip()
    q = request.args.get("q", "").strip()

    clauses = []
    params: list = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if min_bedrooms.isdigit():
        clauses.append("bedrooms >= ?")
        params.append(int(min_bedrooms))
    if min_sleeps.isdigit():
        clauses.append("max_sleeps >= ?")
        params.append(int(min_sleeps))
    if q:
        clauses.append("(name LIKE ? OR city LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM sc_listings {where} ORDER BY state, city, name",
            params,
        ).fetchall()
    return jsonify([_row_to_summary(dict(r)) for r in rows]), 200


@servantcare_web_bp.route("/api/p/servantcare/listing/<int:pid>", methods=["GET"])
@_require_key
def get_listing(pid: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sc_listings WHERE pid = ?", (pid,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        images = conn.execute(
            "SELECT seq, local_path FROM sc_listing_images WHERE pid = ? ORDER BY seq", (pid,)
        ).fetchall()

    listing = dict(row)
    listing["amenities"] = json.loads(listing.pop("amenities_json") or "[]")
    listing["pricing"] = json.loads(listing.pop("pricing_json") or "[]")
    listing["image_urls"] = [
        f"/p/servantcare/photos/{pid}/{os.path.basename(img['local_path'])}" for img in images
    ]
    listing.pop("primary_image_path", None)
    return jsonify(listing), 200


@servantcare_web_bp.route("/p/servantcare/photos/<pid>/<filename>", methods=["GET"])
def serve_photo(pid: str, filename: str):
    if not _PID_RE.match(pid) or not _FILENAME_RE.match(filename):
        return jsonify({"error": "not found"}), 404
    directory = IMAGES_DIR / pid
    return send_from_directory(directory, filename)
