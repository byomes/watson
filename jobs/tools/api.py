"""jobs/tools/api.py — Flask Blueprint serving the public wtsn.me tool
registry.

Mount on the Watson dashboard app:
    from jobs.tools.api import tools_bp
    app.register_blueprint(tools_bp)

/api/tools/resolve/<category>/<slug> is called server-side from the
watson-tools Vercel app's [category]/[slug] dynamic route — same shape as
jobs/links/api.py's /api/links/resolve/<slug> for wcky's /go/[slug]:
public, unauthenticated, GET-only, and only ever returns a tool whose
status is 'live' (a draft row is invisible here even if its category/slug
is already known — the first-deploy Telegram gate in
jobs/tools/registry.py is what flips a row to 'live').
"""
from flask import Blueprint, jsonify

from jobs.tools.registry import get_live_tool

tools_bp = Blueprint("tools", __name__)


@tools_bp.route("/api/tools/resolve/<category>/<slug>", methods=["GET"])
def resolve_tool(category, slug):
    tool = get_live_tool(category, slug)
    if not tool:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "category": tool["category"],
        "slug": tool["slug"],
        "title": tool["title"],
        "tool_type": tool["tool_type"],
        "target_url": tool["target_url"],
        "body_text": tool["body_text"],
    }), 200
