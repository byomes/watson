"""jobs/location/routes.py — Flask Blueprint: the Location dashboard page.

Mount on the Watson dashboard app:
    from jobs.location.routes import location_web_bp
    app.register_blueprint(location_web_bp)

Auth: gated by jobs.dashboard.app._admin_required() — same session check as
/admin, /trading, and the meet-review pages. This is deliberately separate
from jobs/location/api.py's X-Watson-Key-gated routes (those are for the
phone's OwnTracks client, not the browser) — the page's own fetch calls hit
these session-gated endpoints instead, so the API key never ends up in
page source.
"""
import sys
from pathlib import Path

from flask import Blueprint, jsonify, render_template_string, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.location import get_db

location_web_bp = Blueprint("location_web", __name__)

_PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Location — Watson</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    body { font-family: -apple-system, Arial, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 20px; }
    .note { color: #777; font-size: 13px; margin-bottom: 16px; }
    .status { padding: 10px 14px; background: #f7f7f7; border: 1px solid #eee; border-radius: 8px; margin-bottom: 14px; font-size: 13px; }
    .status .stale { color: #b8860b; font-weight: 600; }
    .ranges { display: flex; gap: 6px; margin-bottom: 12px; }
    .ranges button { font-size: 12px; padding: 5px 12px; border-radius: 16px; border: 1px solid #ccc; background: #fff; cursor: pointer; }
    .ranges button.active { background: #c9a84c; border-color: #c9a84c; color: #000; font-weight: 600; }
    #map { height: 480px; border-radius: 8px; border: 1px solid #eee; }
    .empty { color: #999; font-style: italic; padding: 12px 0; }
  </style>
</head>
<body>
  <h1>Location</h1>
  <p class="note">Phone location history, logged via OwnTracks.</p>

  <div class="status" id="status">Loading&hellip;</div>

  <div class="ranges">
    <button data-hours="1" onclick="loadRange(1, this)">1h</button>
    <button data-hours="24" onclick="loadRange(24, this)" class="active">24h</button>
    <button data-hours="168" onclick="loadRange(168, this)">7d</button>
    <button data-hours="720" onclick="loadRange(720, this)">30d</button>
    <button data-hours="0" onclick="loadRange(0, this)">All</button>
  </div>

  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map').setView([39.68, -75.75], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map);

    let line = null;
    let marker = null;

    function fmtAgo(iso) {
      const then = new Date(iso.replace(' ', 'T') + 'Z');
      const mins = Math.round((Date.now() - then.getTime()) / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return mins + ' min ago';
      const hrs = Math.round(mins / 60);
      if (hrs < 24) return hrs + ' hr ago';
      return Math.round(hrs / 24) + ' d ago';
    }

    async function loadRange(hours, btn) {
      document.querySelectorAll('.ranges button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const res = await fetch('/location/api/history?hours=' + hours);
      const points = await res.json();

      if (line) { map.removeLayer(line); line = null; }
      if (marker) { map.removeLayer(marker); marker = null; }

      if (!points.length) {
        document.getElementById('status').innerHTML = '<span class="empty">No pings in this range yet.</span>';
        return;
      }

      const latlngs = points.map(p => [p.lat, p.lon]);
      line = L.polyline(latlngs, { color: '#c9a84c', weight: 3 }).addTo(map);

      const last = points[points.length - 1];
      marker = L.marker([last.lat, last.lon]).addTo(map)
        .bindPopup('Last seen ' + fmtAgo(last.received_at) + '<br>Accuracy: ' + (last.acc ?? '?') + 'm');

      map.fitBounds(line.getBounds().pad(0.15));

      const stale = (Date.now() - new Date(last.received_at.replace(' ', 'T') + 'Z').getTime()) > 30 * 60000;
      document.getElementById('status').innerHTML =
        `Last seen <strong>${fmtAgo(last.received_at)}</strong>` +
        (stale ? ' <span class="stale">(stale)</span>' : '') +
        ` &middot; ${points.length} points in range` +
        ` &middot; battery ${last.batt ?? '?'}%`;
    }

    loadRange(24, document.querySelector('.ranges button.active'));
  </script>
</body>
</html>
"""


@location_web_bp.route("/location")
def location_page():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return redir

    return render_template_string(_PAGE_TEMPLATE)


@location_web_bp.route("/location/api/history")
def location_api_history_web():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "unauthorized"}), 401

    try:
        hours = int(request.args.get("hours", 24))
    except ValueError:
        hours = 24
    try:
        limit = min(int(request.args.get("limit", 5000)), 5000)
    except ValueError:
        limit = 5000

    conn = get_db()
    try:
        if hours > 0:
            rows = conn.execute(
                "SELECT * FROM location_pings WHERE received_at >= datetime('now', ?) "
                "ORDER BY id ASC LIMIT ?",
                (f"-{hours} hours", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM location_pings ORDER BY id ASC LIMIT ?", (limit,)
            ).fetchall()

        return jsonify([
            {
                "lat": r["lat"],
                "lon": r["lon"],
                "acc": r["acc"],
                "batt": r["batt"],
                "received_at": r["received_at"],
            }
            for r in rows
        ]), 200
    finally:
        conn.close()
