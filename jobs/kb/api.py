"""jobs/kb/api.py — Flask Blueprint: immediate KB sync trigger + export
download links.

Mount on the Watson dashboard app:
    from jobs.kb.api import kb_bp
    app.register_blueprint(kb_bp)

Lets generate.py (FMSPC) trigger jobs.kb.sync_and_index.run_sync() right
after a successful scp transfer, instead of only on the nightly 2am cron —
the raw claude.ai transcript URL is fed into weekly blog drafting and needs
to be live same-day, not just eventually true (bug #51 / backlog #24, #29).

Same X-Watson-Key shared-secret pattern as jobs/bodyrec/api.py and
jobs/writing_room/api.py. This route calls run_sync() directly — same code
path as the cron, serialized against it via the same lock — so it is a
trigger, not a second implementation of the sync logic.

GET /api/kb/export-link (added 2026-08-24) is a thin, X-Watson-Key-gated
manual-trigger wrapper around jobs.kb.export_link.run() — mainly for
curl/browser testing without going through the dashboard chat or MCP
connector. The real intended callers are the kb_export_link skill (dashboard
chat, jobs/skillbuilder/router.py) and Claude.ai's run_watson_skill MCP tool
(jobs/devdispatch/api.py) — both route to the same jobs.kb.export_link.run().

GET /kb/download/<token> streams the resulting zip — unauthenticated by
design, since the token itself (a secrets.token_urlsafe(24) URL path
segment) is the credential, same shape as a signed download link. Safe
because this route is reachable only via the Tailscale-only dashboard
origin (watson.tail0243ff.ts.net), not the public Funnel path, and every
token is single-use and expires in 15 minutes (jobs/kb/schema.py).
"""
import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, after_this_request

from core.database import get_connection
from jobs.kb.sync_and_index import run_sync

log = logging.getLogger(__name__)

kb_bp = Blueprint("kb", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")


@kb_bp.route("/api/kb/sync-now", methods=["POST"])
def sync_now():
    expected = _API_KEY()
    received = request.headers.get("X-Watson-Key")

    if not expected:
        log.warning("KB sync-now rejected: WRITING_ROOM_API_KEY not configured on this server")
        return jsonify({"error": "unauthorized"}), 401
    if not received:
        log.warning("KB sync-now rejected: no X-Watson-Key header sent")
        return jsonify({"error": "unauthorized"}), 401
    if received != expected:
        log.warning(
            "KB sync-now rejected: X-Watson-Key did not match (received %d chars, expected %d chars)",
            len(received), len(expected),
        )
        return jsonify({"error": "unauthorized"}), 401

    try:
        result = run_sync(source="immediate")
    except Exception as exc:
        log.exception("Immediate KB sync trigger failed")
        return jsonify({"ok": False, "moved": 0, "indexed": 0, "error": str(exc)}), 500

    return jsonify(result), (200 if result["ok"] else 500)


@kb_bp.route("/api/kb/export-link", methods=["GET"])
def export_link_route():
    expected = _API_KEY()
    received = request.headers.get("X-Watson-Key")
    if not expected or not received or received != expected:
        return jsonify({"error": "unauthorized"}), 401

    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "missing query parameter 'q'"}), 400

    from jobs.kb.export_link import run as export_link_run
    result = export_link_run(query)
    return jsonify(result), (200 if result.get("ok") else 404)


@kb_bp.route("/kb/download/<token>", methods=["GET"])
def download_export(token):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT zip_path, caption, used, (expires_at <= datetime('now')) AS expired "
            "FROM kb_export_links WHERE token = ?",
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

    zip_path = Path(row["zip_path"])
    if not zip_path.exists():
        return jsonify({"error": "export file no longer available"}), 410

    @after_this_request
    def _cleanup(response):
        # Runs after the response has fully streamed — marking the token
        # used and deleting the zip any earlier would break a legitimate
        # download that's still in flight.
        cleanup_conn = get_connection()
        try:
            cleanup_conn.execute("UPDATE kb_export_links SET used = 1 WHERE token = ?", (token,))
            cleanup_conn.commit()
        finally:
            cleanup_conn.close()
        zip_path.unlink(missing_ok=True)
        return response

    log.info("KB export download served: token=%s... zip=%s", token[:8], zip_path.name)
    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)
