"""
deliver.py — Flask Blueprint for Dev Loop callbacks and project management.

Registered on Watson dashboard app:
    from jobs.dev_loop.deliver import dev_loop_bp
    app.register_blueprint(dev_loop_bp)

Routes:
    POST /api/dev-loop/callback          — FMSPC posts build status here
    GET  /api/dev-loop/projects          — List all dev projects
    GET  /api/dev-loop/projects/<slug>   — Get project + generated code
    POST /api/dev-loop/projects          — Create new project (triggers loop)
    POST /api/dev-loop/projects/<slug>/keep-going — Resume paused project
    POST /api/dev-loop/projects/<slug>/stop       — Stop paused project
    POST /api/dev-loop/projects/<slug>/retrigger  — Re-trigger with feedback
"""
import logging
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Blueprint, g, jsonify, request

log = logging.getLogger(__name__)

dev_loop_bp = Blueprint("dev_loop", __name__)

STAGING_BASE = Path(os.path.expanduser("~/watson/dev"))
WATSON_API_URL = os.getenv("WATSON_API_URL", "https://watson.tail0243ff.ts.net")

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = _API_KEY()
        if not key or request.headers.get("X-Watson-Key") != key:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _db():
    from flask import g as _g
    import sqlite3
    if "db" not in _g:
        db_path = os.path.expanduser("~/watson/data/watson.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        _g.db = c
    return _g.db


# ── Callback from FMSPC ───────────────────────────────────────────────────────

@dev_loop_bp.route("/api/dev-loop/callback", methods=["POST"])
@_require_key
def dev_loop_callback():
    from jobs.dev_loop import _send_telegram, _send_telegram_buttons

    data = request.get_json(force=True) or {}
    slug     = (data.get("slug") or "").strip()
    status   = (data.get("status") or "").strip()
    code     = data.get("code") or ""
    spec     = data.get("spec") or ""
    iteration    = data.get("iteration", 0)
    test_results = data.get("test_results") or {}
    iteration_history = data.get("iteration_history") or []

    if not slug or status not in ("delivered", "paused", "stopped", "failed"):
        return jsonify({"error": "slug and valid status required"}), 400

    db = _db()

    if status == "delivered":
        staging_dir = STAGING_BASE / slug
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "main.py").write_text(code, encoding="utf-8")
        if spec:
            (staging_dir / "spec.md").write_text(spec, encoding="utf-8")

        db.execute(
            "UPDATE dev_projects SET status='delivered', current_iteration=?, "
            "staging_path=?, delivered_at=datetime('now'), updated_at=datetime('now') "
            "WHERE slug=?",
            (iteration, str(staging_dir), slug),
        )
        db.commit()

        row = db.execute("SELECT title FROM dev_projects WHERE slug=?", (slug,)).fetchone()
        title = row["title"] if row else slug
        dash_url = f"{WATSON_API_URL}/#devloop"
        _send_telegram(
            f"Dev Loop — <b>DELIVERED</b>\n"
            f"<b>{title}</b> ({slug})\n\n"
            f"Completed in {iteration} iteration(s). Code ready for review.\n"
            f'<a href="{dash_url}">View on Dashboard</a>'
        )
        db.execute("UPDATE dev_projects SET telegram_notified=1 WHERE slug=?", (slug,))
        db.commit()
        log.info("DevLoop delivered: slug=%s iter=%d", slug, iteration)

    elif status == "paused":
        import json
        db.execute(
            "UPDATE dev_projects SET status='paused', current_iteration=?, "
            "updated_at=datetime('now') WHERE slug=?",
            (iteration, slug),
        )
        db.commit()

        row = db.execute("SELECT title FROM dev_projects WHERE slug=?", (slug,)).fetchone()
        title = row["title"] if row else slug
        errors = test_results.get("errors", [])
        err_summary = "\n".join(f"• {e}" for e in errors[:3]) or "No specific errors captured."

        _send_telegram_buttons(
            f"Dev Loop — <b>PAUSED</b>\n"
            f"<b>{title}</b> ({slug})\n\n"
            f"After {iteration} iteration(s), tests still failing.\n\n"
            f"<b>Errors:</b>\n{err_summary}\n\n"
            f"What would you like to do?",
            buttons=[
                {"label": "Keep Going", "data": f"devloop_keep:{slug}"},
                {"label": "Stop + Review", "data": f"devloop_stop:{slug}"},
            ],
        )
        log.info("DevLoop paused: slug=%s iter=%d", slug, iteration)

    elif status == "stopped":
        db.execute(
            "UPDATE dev_projects SET status='stopped', updated_at=datetime('now') WHERE slug=?",
            (slug,),
        )
        db.commit()
        log.info("DevLoop stopped: slug=%s", slug)

    elif status == "failed":
        db.execute(
            "UPDATE dev_projects SET status='failed', updated_at=datetime('now') WHERE slug=?",
            (slug,),
        )
        db.commit()
        log.info("DevLoop failed: slug=%s", slug)

    return jsonify({"ok": True})


# ── Project API ───────────────────────────────────────────────────────────────

@dev_loop_bp.route("/api/dev-loop/projects", methods=["GET"])
def dev_loop_list():
    rows = _db().execute(
        "SELECT id, slug, title, input_type, status, current_iteration, "
        "max_iterations, created_at, updated_at, delivered_at, staging_path "
        "FROM dev_projects ORDER BY updated_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@dev_loop_bp.route("/api/dev-loop/projects/<slug>", methods=["GET"])
def dev_loop_get(slug):
    row = _db().execute(
        "SELECT * FROM dev_projects WHERE slug=?", (slug,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    result = dict(row)
    staging = row["staging_path"]
    if staging:
        p = Path(staging) / "main.py"
        if p.exists():
            result["code"] = p.read_text(encoding="utf-8")
        spec_p = Path(staging) / "spec.md"
        if spec_p.exists():
            result["spec"] = spec_p.read_text(encoding="utf-8")
    return jsonify(result)


@dev_loop_bp.route("/api/dev-loop/projects", methods=["POST"])
def dev_loop_create():
    import re
    from jobs.dev_loop.trigger import trigger_dev_loop

    data = request.get_json(force=True) or {}
    slug       = (data.get("slug") or "").strip().lower()
    title      = (data.get("title") or "").strip()
    input_type = (data.get("input_type") or "description").strip()
    input_text = (data.get("input_text") or "").strip()

    if not title or not input_text:
        return jsonify({"error": "title and input_text required"}), 400

    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]

    if not re.match(r"^[a-z0-9][a-z0-9\-]*$", slug):
        return jsonify({"error": "slug must be lowercase alphanumeric with hyphens"}), 400

    if input_type not in ("description", "spec"):
        return jsonify({"error": "input_type must be 'description' or 'spec'"}), 400

    result = trigger_dev_loop(slug, title, input_type, input_text)
    if not result["ok"]:
        return jsonify({"error": result.get("error", "trigger failed")}), 500

    row = _db().execute("SELECT * FROM dev_projects WHERE slug=?", (slug,)).fetchone()
    return jsonify(dict(row) if row else {"slug": slug, "ok": True}), 201


@dev_loop_bp.route("/api/dev-loop/projects/<slug>/keep-going", methods=["POST"])
def dev_loop_keep_going(slug):
    from jobs.dev_loop.trigger import trigger_dev_loop

    row = _db().execute(
        "SELECT * FROM dev_projects WHERE slug=?", (slug,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    if row["status"] != "paused":
        return jsonify({"error": "project is not paused"}), 400

    feedback = ""
    existing_code_path = STAGING_BASE / slug / "main.py"
    if existing_code_path.exists():
        try:
            file_contents = existing_code_path.read_text(encoding="utf-8")
            feedback = (
                f"The current version of the file is:\n\n{file_contents}\n\n"
                "Improve it based on the original instructions. Fix any remaining issues."
            )
        except Exception:
            pass

    result = trigger_dev_loop(
        slug=slug,
        title=row["title"],
        input_type=row["input_type"],
        input_text=row["input_text"],
        start_iteration=row["current_iteration"] + 1,
        extend_by=3,
        feedback=feedback,
    )
    if not result["ok"]:
        return jsonify({"error": result.get("error")}), 500
    return jsonify({"ok": True, "slug": slug})


@dev_loop_bp.route("/api/dev-loop/projects/<slug>/stop", methods=["POST"])
def dev_loop_stop(slug):
    db = _db()
    row = db.execute("SELECT id FROM dev_projects WHERE slug=?", (slug,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    db.execute(
        "UPDATE dev_projects SET status='stopped', updated_at=datetime('now') WHERE slug=?",
        (slug,),
    )
    db.commit()
    return jsonify({"ok": True})


@dev_loop_bp.route("/api/dev-loop/projects/<slug>/retrigger", methods=["POST"])
def dev_loop_retrigger(slug):
    from jobs.dev_loop.trigger import trigger_dev_loop

    data = request.get_json(force=True) or {}
    feedback = (data.get("feedback") or "").strip()

    row = _db().execute("SELECT * FROM dev_projects WHERE slug=?", (slug,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    result = trigger_dev_loop(
        slug=slug,
        title=row["title"],
        input_type=row["input_type"],
        input_text=row["input_text"],
        start_iteration=1,
        feedback=feedback,
    )
    if not result["ok"]:
        return jsonify({"error": result.get("error")}), 500
    return jsonify({"ok": True, "slug": slug})
