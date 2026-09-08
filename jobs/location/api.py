"""jobs/location/api.py — Flask Blueprint: phone (OwnTracks) -> Watson DB.

Mount on the Watson dashboard app:
    from jobs.location.api import location_bp
    app.register_blueprint(location_bp)

Ingestion auth: HTTP Basic Auth (OwnTracks' native "Auth" option), checked
against LOCATION_PASSWORD. Deliberately not a query-param key — a token in
the URL ends up in Tailscale Funnel / proxy access logs on every request.

Query auth (dashboard/other Watson code reading history back): X-Watson-Key
header, same pattern as jobs/bodyrec/api.py.
"""
import json
import logging
import math
import os
import secrets as secrets_mod
import sys
from functools import wraps
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.location import get_db

log = logging.getLogger(__name__)

location_bp = Blueprint("location", __name__)

_LOCATION_PASSWORD = lambda: os.getenv("LOCATION_PASSWORD", "")
_API_KEY = lambda: os.getenv("LOCATION_API_KEY", "")


def _require_basic_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        expected = _LOCATION_PASSWORD()
        if not expected or not auth or not secrets_mod.compare_digest(auth.password or "", expected):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        expected = _API_KEY()
        if not expected or not secrets_mod.compare_digest(request.headers.get("X-Watson-Key", ""), expected):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _zone_for(conn, lat, lon):
    """Name of the smallest-radius defined zone containing (lat, lon), or None."""
    best = None
    for z in conn.execute("SELECT name, center_lat, center_lon, radius_m FROM location_zones"):
        if _haversine_m(lat, lon, z["center_lat"], z["center_lon"]) <= z["radius_m"]:
            if best is None or z["radius_m"] < best[1]:
                best = (z["name"], z["radius_m"])
    return best[0] if best else None


def _fire_location_reminders(conn, zone_to):
    """Fire (Telegram + mark 'fired') any active dashboard reminder whose
    location_zone matches the zone just entered. No-op on leaving a zone or
    when nothing has a location trigger set for it -- Bill asked (2026-09-08)
    to drop the old blanket "Arrived at X / Left X" notice on every crossing
    in favor of only the reminders he explicitly asks for."""
    if not zone_to:
        return
    rows = conn.execute(
        "SELECT id, title FROM reminders WHERE status = 'active' AND location_zone = ?",
        (zone_to,),
    ).fetchall()
    if not rows or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    for r in rows:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"\U0001F4CD Reminder ({zone_to}): {r['title']}\n\n - Watson"},
                timeout=10,
            )
            conn.execute(
                "UPDATE reminders SET status = 'fired', updated_at = datetime('now') WHERE id = ?",
                (r["id"],),
            )
        except Exception as exc:
            log.error("location reminder fire failed id=%s: %s", r["id"], exc)
    conn.commit()


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "tid": row["tid"],
        "tst": row["tst"],
        "received_at": row["received_at"],
        "lat": row["lat"],
        "lon": row["lon"],
        "acc": row["acc"],
        "alt": row["alt"],
        "vel": row["vel"],
        "batt": row["batt"],
        "conn": row["conn_type"],
    }


@location_bp.route("/api/location", methods=["POST"])
@_require_basic_auth
def ingest():
    """OwnTracks HTTP endpoint. Must always return 200 + JSON array."""
    data = request.get_json(silent=True, force=True) or {}

    if data.get("_type") != "location":
        # waypoint / transition / lwt / etc. — nothing to store yet.
        return jsonify([]), 200

    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify([]), 200

    conn = get_db()
    try:
        prev = conn.execute(
            "SELECT lat, lon FROM location_pings ORDER BY id DESC LIMIT 1"
        ).fetchone()

        conn.execute(
            "INSERT INTO location_pings (tid, tst, lat, lon, acc, alt, vel, batt, conn_type, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data.get("tid"),
                data.get("tst"),
                lat,
                lon,
                data.get("acc"),
                data.get("alt"),
                data.get("vel"),
                data.get("batt"),
                data.get("conn"),
                json.dumps(data),
            ),
        )
        conn.commit()

        zone_from = _zone_for(conn, prev["lat"], prev["lon"]) if prev else None
        zone_to = _zone_for(conn, lat, lon)
        if zone_to != zone_from:
            conn.execute(
                "INSERT INTO location_events (tst, zone_from, zone_to, lat, lon) VALUES (?, ?, ?, ?, ?)",
                (data.get("tst"), zone_from, zone_to, lat, lon),
            )
            conn.commit()
            _fire_location_reminders(conn, zone_to)
    except Exception as exc:
        log.error("location ingest failed: %s", exc)
    finally:
        conn.close()

    return jsonify([]), 200


@location_bp.route("/api/location/latest", methods=["GET"])
@_require_key
def latest():
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM location_pings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify(None), 200
        return jsonify(_row_to_dict(row)), 200
    finally:
        conn.close()


@location_bp.route("/api/location/history", methods=["GET"])
@_require_key
def history():
    try:
        limit = min(int(request.args.get("limit", 500)), 5000)
    except ValueError:
        limit = 500

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM location_pings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows]), 200
    finally:
        conn.close()
