"""jobs/book/routes.py — Flask Blueprint for the Cover Comp Idea Generator.

Mount on the Watson dashboard app:
    from jobs.book.routes import book_bp
    from jobs.book.schema import create_tables as _book_create_tables
    _book_create_tables()
    app.register_blueprint(book_bp)

Dashboard-only (no X-Watson-Key), same trust model as Dev Loop's
/api/dev-loop/projects* routes — this runs on the local/Tailscale-only
dashboard, not a public Vercel frontend.
"""
import json
import logging
import os
import threading

from flask import Blueprint, jsonify, request, send_file

from config.settings import GOOGLE_FONTS_API_KEY
from core.database import get_connection
from jobs.book import cover_comps, font_finder

log = logging.getLogger(__name__)

book_bp = Blueprint("book", __name__)


@book_bp.route("/api/cover-series", methods=["GET"])
def cover_series_list():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM cover_series ORDER BY name").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@book_bp.route("/api/cover-series", methods=["POST"])
def cover_series_create():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    palette = data.get("house_palette") or []
    font_ids = data.get("font_library_ids") or []

    if not name:
        return jsonify({"error": "name is required"}), 400
    if not isinstance(palette, list) or not (4 <= len(palette) <= 6):
        return jsonify({"error": "house_palette must be a list of 4-6 hex values"}), 400
    if not isinstance(font_ids, list) or not font_ids:
        return jsonify({"error": "font_library_ids must be a non-empty list"}), 400

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO cover_series (name, house_palette, font_library_ids) VALUES (?, ?, ?)",
            (name, json.dumps(palette), json.dumps(font_ids)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM cover_series WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("cover_series_create failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@book_bp.route("/api/cover-font-library", methods=["GET"])
def cover_font_library_list():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cover_font_library WHERE active=1 ORDER BY display_face"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@book_bp.route("/api/cover-concepts", methods=["GET"])
def cover_concepts_list():
    series_id = request.args.get("series_id")
    status = request.args.get("status")
    conn = get_connection()
    try:
        query = "SELECT * FROM cover_concepts WHERE 1=1"
        params = []
        if series_id:
            query += " AND series_id=?"
            params.append(series_id)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY id DESC"
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@book_bp.route("/api/cover-comps", methods=["POST"])
def cover_comps_create():
    data = request.get_json(force=True) or {}
    series_id = data.get("series_id")
    title = (data.get("title") or "").strip()
    subtitle = (data.get("subtitle") or "").strip()
    theme = (data.get("theme") or "").strip()
    key_concepts = (data.get("key_concepts") or "").strip()

    if not series_id:
        return jsonify({"error": "series_id is required"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not theme:
        return jsonify({"error": "theme is required"}), 400

    # Ollama serializes generate requests and this run can take several
    # minutes (3-5 packets, some with a retry) — run in a background thread
    # so the Flask worker isn't held open; concepts arrive on Telegram as
    # each one clears validation, same "fire and forget, results arrive
    # async" idiom as Dev Loop's subprocess.Popen dispatch.
    def _run():
        try:
            cover_comps.generate(int(series_id), title, subtitle, theme, key_concepts)
        except Exception as exc:
            log.error("cover_comps.generate failed for series_id=%s title=%r: %s", series_id, title, exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "generating"}), 202


@book_bp.route("/api/cover-concepts/<int:concept_id>/preview", methods=["POST"])
def cover_concept_preview(concept_id):
    try:
        path = cover_comps.generate_preview(concept_id)
    except Exception as exc:
        log.error("generate_preview failed for concept_id=%d: %s", concept_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    if path is None:
        return jsonify({"success": False, "error": "concept not found"}), 404
    return jsonify({"success": True, "preview_image_path": path})


@book_bp.route("/api/cover-comps/font-suggestions", methods=["POST"])
def cover_font_suggestions_create():
    """Stage 1 (narrow, ~47s+, Fonts API + Ollama) + stage 2 (render,
    fast) chained together — the first full run for a bucket. Later
    title/subtitle edits should call /rerender instead, which reuses
    this run's cached candidate list and skips stage 1 entirely."""
    data = request.get_json(force=True) or {}
    series_id = data.get("series_id")
    title = (data.get("title") or "").strip()
    subtitle = (data.get("subtitle") or "").strip()

    if not series_id:
        return jsonify({"error": "series_id is required"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not GOOGLE_FONTS_API_KEY:
        return jsonify({"error": "GOOGLE_FONTS_API_KEY is not set in ~/watson/.env"}), 400

    def _run():
        try:
            batch_id = font_finder.narrow_fonts(int(series_id))
            font_finder.render_batch(batch_id, title, subtitle)
        except Exception as exc:
            log.error("font_finder narrow+render failed for series_id=%s: %s", series_id, exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "generating"}), 202


@book_bp.route("/api/cover-comps/font-suggestions/rerender", methods=["POST"])
def cover_font_suggestions_rerender():
    """Stage 2 only — no Fonts API call, no Ollama call. Re-renders the
    most recent batch for this bucket against a new title/subtitle."""
    data = request.get_json(force=True) or {}
    series_id = data.get("series_id")
    title = (data.get("title") or "").strip()
    subtitle = (data.get("subtitle") or "").strip()

    if not series_id:
        return jsonify({"error": "series_id is required"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM cover_font_suggestion_batches WHERE series_id=? ORDER BY id DESC LIMIT 1",
            (series_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "no font-suggestion batch found for this bucket yet — run Suggest Fonts first"}), 404
    batch_id = row["id"]

    def _run():
        try:
            font_finder.render_batch(batch_id, title, subtitle)
        except Exception as exc:
            log.error("font_finder.render_batch failed for batch_id=%s: %s", batch_id, exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "rendering"}), 202


@book_bp.route("/api/cover-concepts/<int:concept_id>/preview-image")
def cover_concept_preview_image(concept_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT preview_image_path FROM cover_concepts WHERE id=?", (concept_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["preview_image_path"]:
        return jsonify({"error": "no preview generated"}), 404
    filepath = os.path.abspath(row["preview_image_path"])
    if not filepath.startswith(os.path.abspath(cover_comps.PREVIEW_DIR)) or not os.path.exists(filepath):
        return jsonify({"error": "not found"}), 404
    return send_file(filepath)
