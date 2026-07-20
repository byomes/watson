"""jobs/curator/api.py — Flask Blueprint for Curator (Next.js/Vercel) -> Watson DB.

Mount on the Watson dashboard app:
    from jobs.curator.api import curator_bp
    app.register_blueprint(curator_bp)
"""
import json
import logging
import os
import sys
from functools import wraps
from pathlib import Path

import bcrypt
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.curator import DEFAULT_SPICE_MAX, get_db

log = logging.getLogger(__name__)

curator_bp = Blueprint("curator", __name__)

# Shared secret — same convention as Writing Room / bodyrec (WRITING_ROOM_API_KEY,
# despite the name, is the generic Watson<->Vercel shared key used by every
# server-side proxy on this pattern).
_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")

_EDIT_FIELDS = (
    "title", "author", "series", "series_number", "series_total", "page_count",
    "spice_rating", "spice_notes", "cover_image_url", "description",
    "kindle_unlimited",
)
_VALID_STATUSES = ("pending", "confirmed", "needs_review", "rejected")
_VALID_SHELVES = ("want_to_read", "reading", "read")
_VALID_SOURCE_TYPES = ("screenshot", "tiktok", "instagram", "youtube", "goodreads", "amazon", "other")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _book_row_to_dict(row, batch_info: dict | None = None) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "series": row["series"],
        "series_number": row["series_number"],
        "series_total": row["series_total"],
        "page_count": row["page_count"],
        "spice_rating": row["spice_rating"],
        "spice_notes": row["spice_notes"],
        "cover_image_url": row["cover_image_url"],
        "description": row["description"],
        "kindle_unlimited": bool(row["kindle_unlimited"]),
        "kindle_unlimited_checked_at": row["kindle_unlimited_checked_at"],
        "status": row["status"],
        "added_by": row["added_by"],
        "created_at": row["created_at"],
        "batch_id": batch_info.get("batch_id") if batch_info else None,
        "batch_total": batch_info.get("batch_total") if batch_info else None,
    }


def _batch_info_for_books(conn, book_ids: list[int]) -> dict[int, dict]:
    """book_id -> {"batch_id", "batch_total"} for every book that came from a batch
    submission — used to show the "part of a batch of N" indicator in Pending."""
    if not book_ids:
        return {}
    placeholders = ",".join("?" * len(book_ids))
    rows = conn.execute(
        f"SELECT ij.book_id, ij.batch_id, ib.total_jobs FROM ingest_jobs ij "
        f"JOIN ingest_batches ib ON ib.id = ij.batch_id "
        f"WHERE ij.book_id IN ({placeholders}) AND ij.batch_id IS NOT NULL",
        book_ids,
    ).fetchall()
    return {r["book_id"]: {"batch_id": r["batch_id"], "batch_total": r["total_jobs"]} for r in rows}


def _source_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "type": row["type"],
        "url": row["url"],
        "raw_extracted_text": row["raw_extracted_text"],
        "created_at": row["created_at"],
    }


def _finding_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "source_name": row["source_name"],
        "source_type": row["source_type"],
        "rank": row["rank"],
        "excerpt": row["excerpt"],
        "url": row["url"],
    }


def _reading_status_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "user_id": row["user_id"],
        "shelf": row["shelf"],
        "rating": row["rating"],
        "date_started": row["date_started"],
        "date_finished": row["date_finished"],
        "notes": row["notes"],
    }


# ── Auth ─────────────────────────────────────────────────────────────────────

@curator_bp.route("/api/curator/auth/login", methods=["POST"])
@_require_key
def login():
    data = request.get_json(force=True)
    name     = (data.get("name") or "").strip()
    password = data.get("password") or ""

    if not (name and password):
        return jsonify({"error": "name and password required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, password_hash FROM users WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if not row or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return jsonify({"error": "invalid credentials"}), 401
        return jsonify({"userId": row["id"], "name": row["name"]}), 200
    finally:
        conn.close()


# ── Books ────────────────────────────────────────────────────────────────────

@curator_bp.route("/api/curator/books", methods=["GET"])
@_require_key
def list_books():
    spice_max        = request.args.get("spice_max", type=int)
    kindle_unlimited  = request.args.get("kindle_unlimited")
    status            = request.args.get("status")
    search            = request.args.get("search", "").strip()
    show_all          = request.args.get("show_all") in ("1", "true", "True")

    if spice_max is None and not show_all:
        spice_max = DEFAULT_SPICE_MAX

    clauses, params = [], []

    if spice_max is not None:
        clauses.append("(spice_rating IS NULL OR spice_rating <= ?)")
        params.append(spice_max)
    if kindle_unlimited in ("1", "true", "True"):
        clauses.append("kindle_unlimited = 1")
    if status:
        if status not in _VALID_STATUSES:
            return jsonify({"error": f"status must be one of {_VALID_STATUSES}"}), 400
        clauses.append("status = ?")
        params.append(status)
    else:
        clauses.append("status != 'rejected'")
    if search:
        clauses.append("(title LIKE ? OR author LIKE ? OR series LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT * FROM books {where} ORDER BY created_at DESC", params
        ).fetchall()
        batch_info = _batch_info_for_books(conn, [r["id"] for r in rows])
        return jsonify([_book_row_to_dict(r, batch_info.get(r["id"])) for r in rows]), 200
    finally:
        conn.close()


@curator_bp.route("/api/curator/books/<int:book_id>", methods=["GET"])
@_require_key
def get_book(book_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        sources = conn.execute(
            "SELECT * FROM book_sources WHERE book_id = ? ORDER BY created_at ASC", (book_id,)
        ).fetchall()
        findings = conn.execute(
            "SELECT * FROM spice_findings WHERE book_id = ? ORDER BY rank ASC", (book_id,)
        ).fetchall()
        result = _book_row_to_dict(row)
        result["sources"] = [_source_row_to_dict(s) for s in sources]
        result["findings"] = [_finding_row_to_dict(f) for f in findings]
        return jsonify(result), 200
    finally:
        conn.close()


@curator_bp.route("/api/curator/books", methods=["POST"])
@_require_key
def create_book():
    """Manual add: text-field submission (title required, author/series optional)."""
    data   = request.get_json(force=True)
    title  = (data.get("title") or "").strip()
    author = (data.get("author") or "").strip()

    if not title:
        return jsonify({"error": "title is required"}), 400

    added_by = data.get("added_by")
    status   = data.get("status", "needs_review")
    if status not in _VALID_STATUSES:
        return jsonify({"error": f"status must be one of {_VALID_STATUSES}"}), 400

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO books (title, author, series, series_number, page_count, "
            "spice_rating, spice_notes, kindle_unlimited, status, added_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title, author or "Unknown", data.get("series"), data.get("series_number"),
                data.get("page_count"), data.get("spice_rating"), data.get("spice_notes"),
                int(bool(data.get("kindle_unlimited"))), status, added_by,
            ),
        )
        book_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return jsonify(_book_row_to_dict(row)), 201
    except Exception as exc:
        log.error("create_book failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()


@curator_bp.route("/api/curator/books/<int:book_id>", methods=["PATCH"])
@_require_key
def update_book(book_id):
    """Edit fields and/or change status (approve -> confirmed, reject -> rejected [soft-delete])."""
    data = request.get_json(force=True)

    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        set_clauses, values = [], []
        for f in _EDIT_FIELDS:
            if f in data:
                val = data[f]
                if f == "kindle_unlimited":
                    val = int(bool(val))
                set_clauses.append(f"{f} = ?")
                values.append(val)
        if "status" in data:
            if data["status"] not in _VALID_STATUSES:
                return jsonify({"error": f"status must be one of {_VALID_STATUSES}"}), 400
            set_clauses.append("status = ?")
            values.append(data["status"])
        if "kindle_unlimited" in data:
            set_clauses.append("kindle_unlimited_checked_at = datetime('now')")

        if set_clauses:
            values.append(book_id)
            conn.execute(f"UPDATE books SET {', '.join(set_clauses)} WHERE id = ?", values)
            conn.commit()

        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return jsonify(_book_row_to_dict(row)), 200
    except Exception as exc:
        log.error("update_book failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()


@curator_bp.route("/api/curator/books/<int:book_id>/sources", methods=["POST"])
@_require_key
def add_source(book_id):
    data = request.get_json(force=True)
    source_type = data.get("type")
    if source_type not in _VALID_SOURCE_TYPES:
        return jsonify({"error": f"type must be one of {_VALID_SOURCE_TYPES}"}), 400

    conn = get_db()
    try:
        book = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return jsonify({"error": "not found"}), 404
        cur = conn.execute(
            "INSERT INTO book_sources (book_id, type, url, raw_extracted_text) VALUES (?, ?, ?, ?)",
            (book_id, source_type, data.get("url"), data.get("raw_extracted_text")),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM book_sources WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(_source_row_to_dict(row)), 201
    finally:
        conn.close()


# ── Reading status ───────────────────────────────────────────────────────────

@curator_bp.route("/api/curator/reading-status", methods=["GET"])
@_require_key
def get_reading_status():
    user_id = request.args.get("user", type=int)
    if not user_id:
        return jsonify({"error": "user is required"}), 400

    shelf = request.args.get("shelf")
    if shelf and shelf not in _VALID_SHELVES:
        return jsonify({"error": f"shelf must be one of {_VALID_SHELVES}"}), 400

    conn = get_db()
    try:
        query = (
            "SELECT rs.*, b.title, b.author, b.series, b.spice_rating, b.page_count "
            "FROM reading_status rs JOIN books b ON b.id = rs.book_id "
            "WHERE rs.user_id = ?"
        )
        params = [user_id]
        if shelf:
            query += " AND rs.shelf = ?"
            params.append(shelf)
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = _reading_status_row_to_dict(r)
            d.update({
                "title": r["title"], "author": r["author"], "series": r["series"],
                "spice_rating": r["spice_rating"], "page_count": r["page_count"],
            })
            result.append(d)
        return jsonify(result), 200
    finally:
        conn.close()


@curator_bp.route("/api/curator/reading-status", methods=["POST"])
@_require_key
def upsert_reading_status():
    data    = request.get_json(force=True)
    book_id = data.get("book_id")
    user_id = data.get("user_id")
    shelf   = data.get("shelf", "want_to_read")

    if not (book_id and user_id):
        return jsonify({"error": "book_id and user_id are required"}), 400
    if shelf not in _VALID_SHELVES:
        return jsonify({"error": f"shelf must be one of {_VALID_SHELVES}"}), 400

    conn = get_db()
    try:
        book = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return jsonify({"error": "book not found"}), 404

        conn.execute(
            "INSERT INTO reading_status (book_id, user_id, shelf, rating, date_started, "
            "date_finished, notes) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(book_id, user_id) DO UPDATE SET "
            "shelf = excluded.shelf, "
            "rating = COALESCE(excluded.rating, reading_status.rating), "
            "date_started = COALESCE(excluded.date_started, reading_status.date_started), "
            "date_finished = COALESCE(excluded.date_finished, reading_status.date_finished), "
            "notes = COALESCE(excluded.notes, reading_status.notes)",
            (
                book_id, user_id, shelf, data.get("rating"),
                data.get("date_started"), data.get("date_finished"), data.get("notes"),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM reading_status WHERE book_id = ? AND user_id = ?", (book_id, user_id)
        ).fetchone()
        return jsonify(_reading_status_row_to_dict(row)), 200
    except Exception as exc:
        log.error("upsert_reading_status failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()


# ── Stats ────────────────────────────────────────────────────────────────────

@curator_bp.route("/api/curator/stats/<int:user_id>/<int:year>", methods=["GET"])
@_require_key
def get_stats(user_id, year):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT b.page_count, b.title, b.author, rs.rating, rs.date_finished "
            "FROM reading_status rs JOIN books b ON b.id = rs.book_id "
            "WHERE rs.user_id = ? AND rs.shelf = 'read' "
            "AND strftime('%Y', rs.date_finished) = ?",
            (user_id, str(year)),
        ).fetchall()
        total_pages = sum(r["page_count"] or 0 for r in rows)
        return jsonify({
            "year": year,
            "count": len(rows),
            "total_pages": total_pages,
            "books": [
                {"title": r["title"], "author": r["author"], "rating": r["rating"],
                 "date_finished": r["date_finished"]}
                for r in rows
            ],
        }), 200
    finally:
        conn.close()


# ── Ingest ───────────────────────────────────────────────────────────────────
#
# Single-item and batch submissions both land in ingest_jobs and are picked up by the
# sequential background worker (jobs/curator/worker.py, started once at app boot below).
# This route never blocks on research — it only ever writes a queue row and returns.

@curator_bp.route("/api/curator/ingest", methods=["POST"])
@_require_key
def ingest():
    """Enqueue a single-book submission: text title/author, cover image, or social link.
    Returns immediately with a job_id — poll GET /api/curator/ingest/status/<job_id>."""
    from jobs.curator.worker import enqueue_job

    submitted_by = request.form.get("submitted_by", type=int) or (
        request.get_json(silent=True) or {}
    ).get("submitted_by")

    image_bytes = None
    image_type = None
    if "image" in request.files:
        f = request.files["image"]
        image_bytes = f.read()
        image_type = f.mimetype

    if image_bytes is not None:
        payload = {
            "title": request.form.get("title"),
            "author": request.form.get("author"),
            "series": request.form.get("series"),
        }
    else:
        payload = request.get_json(force=True)

    title = (payload or {}).get("title")
    author = (payload or {}).get("author")
    series = (payload or {}).get("series")
    link = (payload or {}).get("link")

    if not (title or link or image_bytes):
        return jsonify({"error": "must provide title, link, or image"}), 400

    input_type = "image" if image_bytes else ("link" if link else "text")
    job_id = enqueue_job(
        input_type=input_type,
        input_raw=json.dumps({"title": title, "author": author, "series": series, "link": link}),
        image_bytes=image_bytes,
        image_mimetype=image_type,
        submitted_by=submitted_by,
    )

    return jsonify({
        "job_id": job_id,
        "batch_id": None,
        "status": "researching",
        "message": "Got it — researching now, check Pending in a bit.",
    }), 202


@curator_bp.route("/api/curator/ingest/batch", methods=["POST"])
@_require_key
def ingest_batch():
    """Enqueue a batch: a list of {title,author} items, or a single {link} item for
    reel/multi-book extraction. Returns immediately."""
    from jobs.curator.worker import enqueue_batch

    data = request.get_json(force=True)
    items = data.get("items")
    submitted_by = data.get("submitted_by")

    if not isinstance(items, list) or not items:
        return jsonify({"error": "items must be a non-empty list"}), 400
    for item in items:
        if not isinstance(item, dict) or not (item.get("title") or item.get("link")):
            return jsonify({"error": "each item needs a title or a link"}), 400

    result = enqueue_batch(items, submitted_by=submitted_by)

    return jsonify({
        "batch_id": result["batch_id"],
        "job_ids": result["job_ids"],
        "count": len(result["job_ids"]),
        "message": f"Got it — {len(items)} book{'s' if len(items) != 1 else ''} queued. "
                   "I'll text you when they're ready.",
    }), 202


@curator_bp.route("/api/curator/ingest/status/<int:job_id>", methods=["GET"])
@_require_key
def ingest_status(job_id):
    from jobs.curator.worker import get_job_status

    status = get_job_status(job_id)
    if not status:
        return jsonify({"error": "not found"}), 404
    return jsonify(status), 200
