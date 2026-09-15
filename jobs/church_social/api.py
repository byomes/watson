"""jobs/church_social/api.py — Flask Blueprint: GET /church_social/img/<filename>,
the public image-hosting route jobs.church_social.social.post_to_instagram()
points Instagram's Graph API image_url at.

Mount on the Watson dashboard app:
    from jobs.church_social.api import church_social_bp
    app.register_blueprint(church_social_bp)

Unlike jobs/exports/api.py's download route, this isn't single-use or
expiring: Meta's servers must be able to fetch (and retry fetching) the
image up until the post actually publishes, and the image itself is bound
for a public Facebook/Instagram post anyway, so there's no sensitivity to
guard beyond not letting the route be used to read arbitrary files off
disk. The random token filename (secrets.token_urlsafe(16), assigned in
social.save_queued_image()) is the only access control this route needs;
the regex below only rules out path traversal / directory listing, not
guessability. Registered on the same app/port as exports_bp and kb_bp,
which Tailscale Funnel proxies in full to the public internet — required
here, since Meta's servers have no path onto the tailnet.
"""
import re

from flask import Blueprint, abort, send_file

from jobs.church_social.social import IMAGE_DIR

church_social_bp = Blueprint("church_social", __name__)

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_-]+\.(jpg|jpeg|png)$")


@church_social_bp.route("/church_social/img/<filename>", methods=["GET"])
def serve_queued_image(filename):
    if not _SAFE_FILENAME.match(filename):
        abort(404)

    path = IMAGE_DIR / filename
    if not path.exists():
        abort(404)

    return send_file(path)
