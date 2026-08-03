"""jobs/kb/api.py — Flask Blueprint: immediate KB sync trigger.

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
"""
import logging
import os

from flask import Blueprint, jsonify, request

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
