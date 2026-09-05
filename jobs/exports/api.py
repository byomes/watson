"""jobs/exports/api.py — Flask Blueprint: GET /export/download/<token>,
the download route for jobs.exports.export_link.create_export_link().

Mount on the Watson dashboard app:
    from jobs.exports.api import exports_bp
    app.register_blueprint(exports_bp)

Same shape as jobs/kb/api.py's GET /kb/download/<token>: unauthenticated by
design, since the token itself (secrets.token_urlsafe(24)) is the
credential -- same trust model as a signed download link. Registered on
the same app/port as the MCP devdispatch connector and kb_bp, which sit
behind Tailscale Funnel proxying ALL paths on port 5200 to the public
internet (Funnel has no path-level filtering), so this route is reachable
from the open internet, not just the tailnet. Deliberate (Option A,
decided 2026-09-05, see jobs/exports/export_link.py's docstring) so
Claude.ai itself can fetch a link directly. Every linked file was
mandatorily scanned/redacted before this route ever sees it (enforced
inside create_export_link(), not here) -- the token + 15-minute expiry +
single-use is the only access control this route itself needs to enforce.
"""
import logging
from pathlib import Path

from flask import Blueprint, jsonify, send_file, after_this_request

from core.database import get_connection

log = logging.getLogger(__name__)

exports_bp = Blueprint("exports", __name__)


@exports_bp.route("/export/download/<token>", methods=["GET"])
def download_export(token):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT file_path, filename, used, (expires_at <= datetime('now')) AS expired "
            "FROM file_export_links WHERE token = ?",
            (token,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "unknown download link"}), 404
    if row["used"]:
        return jsonify({"error": "download link already used"}), 410
    if row["expired"]:
        return jsonify({"error": "download link expired"}), 410

    file_path = Path(row["file_path"])
    if not file_path.exists():
        return jsonify({"error": "export file no longer available"}), 410

    @after_this_request
    def _cleanup(response):
        # Runs after the response has fully streamed -- marking the token
        # used and deleting the file any earlier would break a legitimate
        # download still in flight.
        cleanup_conn = get_connection()
        try:
            cleanup_conn.execute(
                "UPDATE file_export_links SET used = 1, used_at = datetime('now') WHERE token = ?",
                (token,),
            )
            cleanup_conn.commit()
        finally:
            cleanup_conn.close()
        # file_path is always a staged copy (jobs/exports/export_link.py),
        # never the caller's original -- always safe to delete here.
        file_path.unlink(missing_ok=True)
        return response

    log.info("Export download served: token=%s... file=%s", token[:8], row["filename"])
    return send_file(file_path, as_attachment=True, download_name=row["filename"])
