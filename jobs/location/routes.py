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
import csv
import io
import sys
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template_string, request

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
    h2 { font-size: 15px; margin: 26px 0 10px; color: #444; }
    .export-row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
    .export-row label { display: flex; flex-direction: column; font-size: 11px; color: #666; gap: 4px; }
    .export-row input { padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; font-family: inherit; }
    .export-row button { padding: 7px 16px; border-radius: 6px; border: 1px solid #c9a84c; background: #c9a84c; color: #000; font-weight: 600; cursor: pointer; font-size: 13px; }
    #log-wrap { max-height: 420px; overflow-y: auto; border: 1px solid #eee; border-radius: 8px; }
    #log-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    #log-table th { position: sticky; top: 0; background: #f7f7f7; text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; color: #666; font-size: 11px; text-transform: uppercase; }
    #log-table td { padding: 5px 10px; border-bottom: 1px solid #f2f2f2; }
  </style>
</head>
<body>
  <a href="/" style="display:inline-block;margin-bottom:14px;font-size:12px;color:#888;text-decoration:none">&larr; Dashboard</a>
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

  <h2>Export</h2>
  <div class="export-row">
    <label>From <input type="datetime-local" id="export-start"></label>
    <label>To <input type="datetime-local" id="export-end"></label>
    <button onclick="doExport()">Export CSV</button>
  </div>

  <h2>Past week log</h2>
  <div id="log-wrap">
    <table id="log-table">
      <thead><tr><th>Time</th><th>Lat</th><th>Lon</th><th>Acc (m)</th><th>Batt</th></tr></thead>
      <tbody id="log-body"><tr><td colspan="5" class="empty">Loading&hellip;</td></tr></tbody>
    </table>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map').setView([39.68, -75.75], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map);

    let line = null;
    let marker = null;

    async function loadZones() {
      const res = await fetch('/location/api/zones');
      const zones = await res.json();
      zones.forEach(z => {
        L.circle([z.center_lat, z.center_lon], {
          radius: z.radius_m,
          color: '#5b8def',
          weight: 1.5,
          fillColor: '#5b8def',
          fillOpacity: 0.08,
        }).addTo(map).bindPopup(z.name);
      });
    }

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

    function fmtLocal(iso) {
      const d = new Date(iso.replace(' ', 'T') + 'Z');
      return d.toLocaleString();
    }

    function pad(n) { return String(n).padStart(2, '0'); }

    function toLocalInputValue(d) {
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
        'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function toSqlUtc(dtLocalValue) {
      const d = new Date(dtLocalValue);
      return d.toISOString().slice(0, 19).replace('T', ' ');
    }

    function doExport() {
      const startVal = document.getElementById('export-start').value;
      const endVal = document.getElementById('export-end').value;
      const params = new URLSearchParams();
      if (startVal) params.set('start', toSqlUtc(startVal));
      if (endVal) params.set('end', toSqlUtc(endVal));
      window.location.href = '/location/api/export?' + params.toString();
    }

    async function loadLog() {
      const res = await fetch('/location/api/history?hours=168&limit=5000');
      const points = await res.json();
      const body = document.getElementById('log-body');
      if (!points.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty">No pings in the past week.</td></tr>';
        return;
      }
      body.innerHTML = points.slice().reverse().map(p => `
        <tr>
          <td>${fmtLocal(p.received_at)}</td>
          <td>${p.lat.toFixed(5)}</td>
          <td>${p.lon.toFixed(5)}</td>
          <td>${p.acc ?? '?'}</td>
          <td>${p.batt ?? '?'}</td>
        </tr>
      `).join('');
    }

    (function initExportDefaults() {
      const now = new Date();
      const weekAgo = new Date(now.getTime() - 7 * 24 * 3600 * 1000);
      document.getElementById('export-start').value = toLocalInputValue(weekAgo);
      document.getElementById('export-end').value = toLocalInputValue(now);
    })();

    loadZones();
    loadRange(24, document.querySelector('.ranges button.active'));
    loadLog();
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


@location_web_bp.route("/location/api/zones")
def location_api_zones_web():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_db()
    try:
        rows = conn.execute("SELECT name, center_lat, center_lon, radius_m FROM location_zones").fetchall()
        return jsonify([
            {"name": r["name"], "center_lat": r["center_lat"], "center_lon": r["center_lon"], "radius_m": r["radius_m"]}
            for r in rows
        ]), 200
    finally:
        conn.close()


@location_web_bp.route("/location/api/export")
def location_api_export():
    from jobs.dashboard.app import _admin_required
    redir = _admin_required()
    if redir:
        return jsonify({"error": "unauthorized"}), 401

    # Both are UTC "YYYY-MM-DD HH:MM:SS" strings — the page's JS converts the
    # browser's local datetime-local inputs to this format before sending,
    # matching how received_at is stored (SQLite datetime('now') is UTC).
    start = request.args.get("start", "")
    end = request.args.get("end", "")

    query = "SELECT received_at, lat, lon, acc, alt, vel, batt, tid FROM location_pings WHERE 1=1"
    params = []
    if start:
        query += " AND received_at >= ?"
        params.append(start)
    if end:
        query += " AND received_at <= ?"
        params.append(end)
    query += " ORDER BY id ASC LIMIT 50000"

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["received_at_utc", "lat", "lon", "accuracy_m", "altitude_m", "speed", "battery_pct", "tid"])
    for r in rows:
        writer.writerow([r["received_at"], r["lat"], r["lon"], r["acc"], r["alt"], r["vel"], r["batt"], r["tid"]])

    tag = lambda v, fallback: v.replace(" ", "_").replace(":", "") if v else fallback
    filename = f"watson_location_{tag(start, 'start')}_{tag(end, 'end')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
