"""jobs/beachhouse/beachhouse_web.py — Flask Blueprint backing the
wtsn.me/p/beachhouse family beach-house search tool.

Mount on the Watson dashboard app:
    from jobs.beachhouse.beachhouse_web import beachhouse_web_bp
    app.register_blueprint(beachhouse_web_bp)

Auth: all /api/p/beachhouse/... routes require header X-Watson-Key matching
BEACHHOUSE_API_KEY -- same one-key-per-external-consumer convention as
jobs/servantcare/servantcare_web.py. Unlike ServantCare, there is no public
photo route here -- listing photos are hotlinked straight from VRBO's/
Airbnb's own CDN via primary_image_url, never downloaded/re-hosted (see
jobs/beachhouse/schema.py's module docstring for why).
"""
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.beachhouse.schema import get_connection

beachhouse_web_bp = Blueprint("beachhouse_web", __name__)

_API_KEY = lambda: os.getenv("BEACHHOUSE_API_KEY", "")
_VALID_STATUSES = ("new", "saved", "dismissed")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _row_to_summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "source": row["source"],
        "name": row["name"],
        "city": row["city"],
        "state": row["state"],
        "bedrooms": row["bedrooms"],
        "bathrooms": row["bathrooms"],
        "max_sleeps": row["max_sleeps"],
        "source_url": row["source_url"],
        "primary_image_url": row["primary_image_url"],
        "review_status": row["review_status"],
    }


@beachhouse_web_bp.route("/api/p/beachhouse/states", methods=["GET"])
@_require_key
def list_states():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM bh_listings GROUP BY state ORDER BY state"
        ).fetchall()
    return jsonify([{"state": r["state"], "count": r["n"]} for r in rows]), 200


@beachhouse_web_bp.route("/api/p/beachhouse/search", methods=["GET"])
@_require_key
def search():
    state = request.args.get("state", "").strip()
    source = request.args.get("source", "").strip()
    review_status = request.args.get("review_status", "").strip()
    min_bedrooms = request.args.get("min_bedrooms", "").strip()
    q = request.args.get("q", "").strip()

    clauses = []
    params: list = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if source in ("vrbo", "airbnb"):
        clauses.append("source = ?")
        params.append(source)
    if review_status in _VALID_STATUSES:
        clauses.append("review_status = ?")
        params.append(review_status)
    else:
        # default view excludes dismissed candidates so the list doesn't
        # keep showing houses Bill/Donna already ruled out
        clauses.append("review_status != 'dismissed'")
    if min_bedrooms.isdigit():
        clauses.append("bedrooms >= ?")
        params.append(int(min_bedrooms))
    if q:
        clauses.append("(name LIKE ? OR city LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM bh_listings {where} ORDER BY review_status = 'saved' DESC, state, city, bedrooms DESC",
            params,
        ).fetchall()
    return jsonify([_row_to_summary(dict(r)) for r in rows]), 200


@beachhouse_web_bp.route("/api/p/beachhouse/listing/<int:listing_id>", methods=["GET"])
@_require_key
def get_listing(listing_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bh_listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row)), 200


@beachhouse_web_bp.route("/api/p/beachhouse/listing/<int:listing_id>/status", methods=["POST"])
@_require_key
def set_status(listing_id: int):
    body = request.get_json(silent=True) or {}
    status = (body.get("review_status") or "").strip()
    if status not in _VALID_STATUSES:
        return jsonify({"error": f"review_status must be one of {_VALID_STATUSES}"}), 400
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE bh_listings SET review_status = ? WHERE id = ?", (status, listing_id)
        )
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    return jsonify({"id": listing_id, "review_status": status}), 200
