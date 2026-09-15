"""jobs/church_social/api.py — Flask Blueprint: GET /church_social/img/<filename>,
the public image-hosting route jobs.church_social.social.post_to_instagram()
points Instagram's Graph API image_url at. Also GET /church_social/clip/<token>,
the equivalent route for pulled Sermon Shots clips — same public-fetch need
(Reels posting) plus the /cat/social dashboard's own <video> preview, which
hits this route directly rather than through the Vercel proxy (clip files
run into the hundreds of MB, too large to round-trip through a serverless
function).

Mount on the Watson dashboard app:
    from jobs.church_social.api import church_social_bp
    app.register_blueprint(church_social_bp)

Unlike jobs/exports/api.py's download route, neither of these is single-use
or expiring: Meta's servers must be able to fetch (and retry fetching) the
file up until the post actually publishes, and the content itself is bound
for a public Facebook/Instagram post anyway, so there's no sensitivity to
guard beyond not letting the route be used to read arbitrary files off
disk. The random token (secrets.token_urlsafe(16) — image filename via
social.save_queued_image(), clip serve_token via sermonshots_pull.py /
migrate_clip_scheduling.py) is the only access control either route needs;
the lookups below only rule out path traversal / directory listing /
enumeration, not guessability. Registered on the same app/port as
exports_bp and kb_bp, which Tailscale Funnel proxies in full to the public
internet — required here, since Meta's servers have no path onto the
tailnet.
"""
import re

from flask import Blueprint, abort, send_file

from core.database import get_connection
from jobs.church_social.social import IMAGE_DIR

church_social_bp = Blueprint("church_social", __name__)

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_-]+\.(jpg|jpeg|png)$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")


@church_social_bp.route("/church_social/img/<filename>", methods=["GET"])
def serve_queued_image(filename):
    if not _SAFE_FILENAME.match(filename):
        abort(404)

    path = IMAGE_DIR / filename
    if not path.exists():
        abort(404)

    return send_file(path)


@church_social_bp.route("/church_social/clip/<token>", methods=["GET"])
def serve_clip(token):
    if not _SAFE_TOKEN.match(token):
        abort(404)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT local_path FROM sermonshots_clips WHERE serve_token=?", (token,)
        ).fetchone()
    if not row:
        abort(404)

    return send_file(row["local_path"])
