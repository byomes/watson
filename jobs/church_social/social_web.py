"""jobs/church_social/social_web.py — Flask Blueprint backing the
wtsn.me/cat/social command dashboard for the church's own Facebook/
Instagram queue.

Auth: X-Watson-Key matching CHURCH_SOCIAL_API_KEY (a DEDICATED key, not
DEACONS_API_KEY or any other consumer's, per this codebase's
one-key-per-external-consumer convention — see jobs/congregation/deacons_web.py's
docstring). Human login on top of that reuses the EXISTING deacon PIN
(1303) via /api/cat/deacons/verify_pin — deliberately not a new PIN table,
per Bill's choice during scoping (2026-09-14): this tool has a higher blast
radius than deacon roster access (it can trigger real public posts), but
the PIN itself stays the one every deacon already has, not a separate
secret to distribute.

Mount on the Watson dashboard app:
    from jobs.church_social.social_web import church_social_web_bp
    app.register_blueprint(church_social_web_bp)
"""
import base64
import binascii
import os
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request

from core.database import get_connection
from jobs.church_social.social import (
    add_to_queue,
    get_token_status,
    save_queued_image,
)

church_social_web_bp = Blueprint("church_social_web", __name__)

_API_KEY = lambda: os.getenv("CHURCH_SOCIAL_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@church_social_web_bp.route("/api/cat/social/status", methods=["GET"])
@_require_key
def status():
    return jsonify(get_token_status())


@church_social_web_bp.route("/api/cat/social/queue", methods=["GET"])
@_require_key
def queue():
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, platform, text, image_path, video_path, status, scheduled_time,
                      posted_time, fb_error, ig_error, created_at
               FROM church_social_queue
               ORDER BY scheduled_time DESC
               LIMIT 50"""
        ).fetchall()
    return jsonify({"posts": [dict(r) for r in rows]})


@church_social_web_bp.route("/api/cat/social/clips", methods=["GET"])
@_require_key
def clips():
    """Pulled Sermon Shots clips still awaiting review/scheduling —
    jobs/church_social/sermonshots_pull.py fills this table every 30 min.
    serve_token backs the dashboard's <video> preview and (once scheduled)
    Meta's own fetch of the file, both via GET /church_social/clip/<token>
    in jobs/church_social/api.py."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, video_name, serve_token, pulled_at
               FROM sermonshots_clips
               WHERE status != 'dismissed' AND queued_post_id IS NULL
               ORDER BY pulled_at DESC
               LIMIT 50"""
        ).fetchall()
    return jsonify({"clips": [dict(r) for r in rows]})


@church_social_web_bp.route("/api/cat/social/create", methods=["POST"])
@_require_key
def create():
    """JSON body (not multipart) — image_base64 may be a bare base64 string or a
    data: URL, since the dashboard's watsonFetch proxy only forwards string
    bodies, not multipart file streams (see jobs/church_social/social_web.py
    callers in watson-tools' src/app/api/cat/social/create/route.ts).

    clip_id schedules a pulled Sermon Shots clip instead of a compose-form
    image — mutually exclusive with image_base64; resolves to that clip's
    local video file and marks the clip 'scheduled' via queued_post_id so
    it drops out of GET /api/cat/social/clips."""
    data = request.get_json(force=True) or {}
    platform = (data.get("platform") or "").strip().lower()
    text = (data.get("text") or "").strip()
    scheduled_time_raw = (data.get("scheduled_time") or "").strip()
    image_b64 = data.get("image_base64")
    clip_id = data.get("clip_id")

    if platform not in ("facebook", "instagram", "both"):
        return jsonify({"error": "platform must be facebook, instagram, or both"}), 400
    if not text:
        return jsonify({"error": "text is required"}), 400
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time_raw)
    except ValueError:
        return jsonify({"error": "scheduled_time must be an ISO datetime"}), 400
    if platform in ("instagram", "both") and not image_b64 and not clip_id:
        return jsonify({"error": "Instagram requires an image or a clip"}), 400

    video_path = None
    clip_row = None
    if clip_id:
        with get_connection() as conn:
            clip_row = conn.execute(
                "SELECT id, local_path FROM sermonshots_clips WHERE id=? AND queued_post_id IS NULL",
                (clip_id,),
            ).fetchone()
        if not clip_row:
            return jsonify({"error": "clip not found or already scheduled"}), 404
        video_path = clip_row["local_path"]

    image_path = None
    if image_b64:
        if "," in image_b64[:60]:  # strip a data:image/...;base64, prefix if present
            image_b64 = image_b64.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError):
            return jsonify({"error": "image_base64 is not valid base64"}), 400
        image_path = save_queued_image(image_bytes)

    post_id = add_to_queue(
        text=text, scheduled_time=scheduled_dt, platform=platform, image_path=image_path, video_path=video_path
    )

    if clip_row:
        with get_connection() as conn:
            conn.execute(
                "UPDATE sermonshots_clips SET queued_post_id=? WHERE id=?", (post_id, clip_row["id"])
            )
            conn.commit()

    return jsonify({"id": post_id})


@church_social_web_bp.route("/api/cat/social/cancel", methods=["POST"])
@_require_key
def cancel():
    data = request.get_json(force=True) or {}
    post_id = data.get("id")
    if not post_id:
        return jsonify({"error": "id is required"}), 400

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, status FROM church_social_queue WHERE id=?", (post_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        if row["status"] == "posted":
            return jsonify({"error": "already posted"}), 409
        conn.execute("UPDATE church_social_queue SET status='cancelled' WHERE id=?", (post_id,))
        # Free up the clip (if this was a scheduled Sermon Shots clip) so it
        # goes back into GET /api/cat/social/clips for re-scheduling.
        conn.execute("UPDATE sermonshots_clips SET queued_post_id=NULL WHERE queued_post_id=?", (post_id,))
        conn.commit()
    return jsonify({"ok": True})
