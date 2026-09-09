"""jobs/beachhouse/beachhouse_web.py — Flask Blueprint backing the
wtsn.me/p/beachhouse getaway search tool (Beach/Mountain/Romance tabs).

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
import re
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.beachhouse import AMENITY_FIELDS, CATEGORIES, FLASH_REGIONS
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


def _valid_category(category: str) -> bool:
    return category in CATEGORIES


def _row_to_summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "source": row["source"],
        "name": row["name"],
        "city": row["city"],
        "state": row["state"],
        "drive_hours": row["drive_hours"],
        "bedrooms": row["bedrooms"],
        "bathrooms": row["bathrooms"],
        "max_sleeps": row["max_sleeps"],
        **{a: bool(row[a]) for a in AMENITY_FIELDS},
        "source_url": row["source_url"],
        "primary_image_url": row["primary_image_url"],
        "price_low": row["price_low"],
        "price_high": row["price_high"],
        "price_note": row["price_note"],
        "review_status": row["review_status"],
    }


@beachhouse_web_bp.route("/api/p/beachhouse/categories", methods=["GET"])
@_require_key
def list_categories():
    return jsonify({
        slug: {
            "label": cfg["label"],
            "states": cfg["states"],
            "amenities": cfg["amenities"],
            "default_min_bedrooms": cfg.get("default_min_bedrooms"),
            "default_max_bedrooms": cfg.get("default_max_bedrooms"),
            "default_min_bathrooms": cfg.get("default_min_bathrooms"),
        }
        for slug, cfg in CATEGORIES.items()
    }), 200


@beachhouse_web_bp.route("/api/p/beachhouse/states", methods=["GET"])
@_require_key
def list_states():
    category = request.args.get("category", "").strip()
    if not _valid_category(category):
        return jsonify({"error": f"category must be one of {list(CATEGORIES)}"}), 400
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM bh_listings WHERE category = ? GROUP BY state ORDER BY state",
            (category,),
        ).fetchall()
    return jsonify([{"state": r["state"], "count": r["n"]} for r in rows]), 200


@beachhouse_web_bp.route("/api/p/beachhouse/search", methods=["GET"])
@_require_key
def search():
    category = request.args.get("category", "").strip()
    if not _valid_category(category):
        return jsonify({"error": f"category must be one of {list(CATEGORIES)}"}), 400

    # states: comma-separated, e.g. "Virginia,North Carolina" -- lets
    # Bill/Melanie pick any combination rather than one state at a time.
    states = [s.strip() for s in request.args.get("states", "").split(",") if s.strip()]
    source = request.args.get("source", "").strip()
    review_status = request.args.get("review_status", "").strip()
    min_bedrooms = request.args.get("min_bedrooms", "").strip()
    max_bedrooms = request.args.get("max_bedrooms", "").strip()
    min_bathrooms = request.args.get("min_bathrooms", "").strip()
    max_price = request.args.get("max_price", "").strip()
    include_unpriced = request.args.get("include_unpriced", "1").strip() != "0"
    q = request.args.get("q", "").strip()

    clauses = ["category = ?"]
    params: list = [category]
    if states:
        clauses.append(f"state IN ({', '.join(['?'] * len(states))})")
        params.extend(states)
    if source in ("vrbo", "airbnb"):
        clauses.append("source = ?")
        params.append(source)
    if review_status in _VALID_STATUSES:
        clauses.append("review_status = ?")
        params.append(review_status)
    else:
        # default view excludes dismissed candidates so the list doesn't
        # keep showing houses already ruled out
        clauses.append("review_status != 'dismissed'")
    if min_bedrooms.isdigit():
        clauses.append("bedrooms >= ?")
        params.append(int(min_bedrooms))
    if max_bedrooms.isdigit():
        clauses.append("bedrooms <= ?")
        params.append(int(max_bedrooms))
    if min_bathrooms:
        try:
            clauses.append("bathrooms >= ?")
            params.append(float(min_bathrooms))
        except ValueError:
            pass
    # amenity_<key>=1 for any amenity in this category's set -- e.g.
    # amenity_hot_tub=1&amenity_fireplace=1
    for amenity in AMENITY_FIELDS:
        if request.args.get(f"amenity_{amenity}", "").strip() == "1":
            clauses.append(f"{amenity} = 1")
    if max_price:
        try:
            max_price_val = float(max_price)
            # price_low is "the cheapest rate Bill/Melanie actually found" --
            # exclude a listing only once its own floor is over budget.
            # NULL means "not priced yet," which is unknown, not "$0" --
            # include_unpriced decides whether unknowns show up at all.
            if include_unpriced:
                clauses.append("(price_low IS NULL OR price_low <= ?)")
            else:
                clauses.append("(price_low IS NOT NULL AND price_low <= ?)")
            params.append(max_price_val)
        except ValueError:
            pass
    if q:
        clauses.append("(name LIKE ? OR city LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}"
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
    return jsonify(_row_to_summary(dict(row)) | {"description": row["description"]}), 200


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


@beachhouse_web_bp.route("/api/p/beachhouse/listing/<int:listing_id>/price", methods=["POST"])
@_require_key
def set_price_note(listing_id: int):
    """Manual price entry -- see jobs/beachhouse/schema.py's docstring for
    why this isn't automated (VRBO gates its pricing step behind a
    bot-detection challenge). price_low/price_high are $/week and drive the
    search UI's max-price filter; price_note is free text for extra color.
    Any field omitted from the body is left unchanged, not cleared -- send
    an explicit null to clear one on purpose."""
    body = request.get_json(silent=True) or {}
    updates: list[str] = []
    params: list = []

    if "price_note" in body:
        note = (body.get("price_note") or "").strip()[:500]
        updates.append("price_note = ?")
        params.append(note or None)
    for field in ("price_low", "price_high"):
        if field in body:
            raw = body.get(field)
            if raw in (None, ""):
                updates.append(f"{field} = ?")
                params.append(None)
            else:
                try:
                    updates.append(f"{field} = ?")
                    params.append(float(raw))
                except (TypeError, ValueError):
                    return jsonify({"error": f"{field} must be a number"}), 400

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    params.append(listing_id)
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE bh_listings SET {', '.join(updates)} WHERE id = ?", params)
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        row = conn.execute(
            "SELECT price_low, price_high, price_note FROM bh_listings WHERE id = ?", (listing_id,)
        ).fetchone()
    return jsonify({"id": listing_id, **dict(row)}), 200


# ── Flash deals (Travelzoo) -- separate, simpler shape, see schema.py ──────

def _deal_to_summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "source": row["source"],
        "name": row["name"],
        "city": row["city"],
        "state": row["state"],
        "town": row["town"],
        "drive_hours": row["drive_hours"],
        "price_per_night": row["price_per_night"],
        "discount_text": row["discount_text"],
        "source_url": row["source_url"],
        "primary_image_url": row["primary_image_url"],
        "review_status": row["review_status"],
    }


@beachhouse_web_bp.route("/api/p/beachhouse/deals/towns", methods=["GET"])
@_require_key
def list_deal_towns():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT town, COUNT(*) AS n FROM bh_deals GROUP BY town ORDER BY town"
        ).fetchall()
    counts = {r["town"]: r["n"] for r in rows}
    # always list every configured town, even ones with 0 results yet
    return jsonify([
        {"town": r["town"], "drive_hours": r["drive_hours"], "count": counts.get(r["town"], 0)}
        for r in FLASH_REGIONS
    ]), 200


@beachhouse_web_bp.route("/api/p/beachhouse/deals/search", methods=["GET"])
@_require_key
def search_deals():
    towns = [t.strip() for t in request.args.get("towns", "").split(",") if t.strip()]
    review_status = request.args.get("review_status", "").strip()
    max_price = request.args.get("max_price", "").strip()
    min_discount = request.args.get("min_discount", "").strip()
    q = request.args.get("q", "").strip()

    clauses = []
    params: list = []
    if towns:
        clauses.append(f"town IN ({', '.join(['?'] * len(towns))})")
        params.extend(towns)
    if review_status in _VALID_STATUSES:
        clauses.append("review_status = ?")
        params.append(review_status)
    else:
        clauses.append("review_status != 'dismissed'")
    if max_price:
        try:
            clauses.append("price_per_night <= ?")
            params.append(float(max_price))
        except ValueError:
            pass
    # min_discount is applied in Python below (discount_text is free text
    # like "38%-65% off" -- no clean SQL comparison), not here.
    if q:
        clauses.append("(name LIKE ? OR city LIKE ? OR town LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM bh_deals {where} ORDER BY review_status = 'saved' DESC, price_per_night ASC",
            params,
        ).fetchall()

    results = [_deal_to_summary(dict(r)) for r in rows]
    if min_discount:
        try:
            floor = float(min_discount)
            def _max_pct(text: str | None) -> float:
                if not text:
                    return -1
                nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
                return max(nums) if nums else -1
            results = [r for r in results if _max_pct(r["discount_text"]) >= floor]
        except ValueError:
            pass
    return jsonify(results), 200


@beachhouse_web_bp.route("/api/p/beachhouse/deals/<int:deal_id>", methods=["GET"])
@_require_key
def get_deal(deal_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bh_deals WHERE id = ?", (deal_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(_deal_to_summary(dict(row)) | {"description": row["description"]}), 200


@beachhouse_web_bp.route("/api/p/beachhouse/deals/<int:deal_id>/status", methods=["POST"])
@_require_key
def set_deal_status(deal_id: int):
    body = request.get_json(silent=True) or {}
    status = (body.get("review_status") or "").strip()
    if status not in _VALID_STATUSES:
        return jsonify({"error": f"review_status must be one of {_VALID_STATUSES}"}), 400
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE bh_deals SET review_status = ? WHERE id = ?", (status, deal_id)
        )
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    return jsonify({"id": deal_id, "review_status": status}), 200
