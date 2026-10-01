"""jobs/uploads/api.py -- Flask Blueprint backing the wtsn.me/upload personal
dropbox tool: a blank page gated behind Bill's own PIN, single file +
optional project note, dropped into one watched folder on the Beelink.

Auth: header X-Watson-Key matching UPLOAD_API_KEY, a dedicated key for this
consumer (this codebase's one-key-per-external-consumer convention) --
separate from the PIN, which gates the human at the browser; this key gates
the watson-tools server-to-server call.

Mount on the Watson dashboard app:
    from jobs.uploads.api import uploads_bp
    app.register_blueprint(uploads_bp)

The frontend can't send this box a real multipart upload, so the file
arrives base64-encoded inside a JSON body instead (watsonFetch's shared
transport is JSON-only, see watson-tools' src/lib/watson.ts) -- same shape
as jobs/congregation/kids_attendance_web.py's import_csv().

jobs/uploads/watcher.py (cron, */5 * * * *) is what actually notices a new
file and tells Bill about it -- this route's only job is getting the file
safely onto disk (and, for a web upload specifically, recording the note
against its stamped filename so the watcher can surface it even though the
watcher itself scans the directory rather than this table).
"""
import base64
import logging
import os
import re
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
INBOX_DIR = os.path.expanduser("~/watson/data/uploads/inbox")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # general-purpose dropbox, bigger than kidsatt's 5MB CSV cap

uploads_bp = Blueprint("uploads", __name__)

_API_KEY = lambda: os.getenv("UPLOAD_API_KEY", "")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uploads (
            filename    TEXT PRIMARY KEY,
            note        TEXT,
            received_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _require_key(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


@uploads_bp.route("/api/uploads/create", methods=["POST"])
@_require_key
def create():
    data = request.get_json(force=True) or {}
    filename = (data.get("filename") or "").strip()
    content_b64 = data.get("content_base64") or ""
    note = (data.get("note") or "").strip() or None

    if not filename:
        return jsonify({"error": "filename is required"}), 400

    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception:
        return jsonify({"error": "invalid file content"}), 400

    if not content:
        return jsonify({"error": "file is empty"}), 400
    if len(content) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file is too large (25MB max)"}), 400

    # Strip to a bare basename (no path components from the client) and drop
    # anything but a conservative safe-filename charset, same as kidsatt's
    # import_csv(), so a crafted filename can't escape INBOX_DIR.
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename)) or "upload.bin"
    stamped_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_name}"

    os.makedirs(INBOX_DIR, exist_ok=True)
    dest_path = os.path.join(INBOX_DIR, stamped_name)
    with open(dest_path, "wb") as f:
        f.write(content)

    with _conn() as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO uploads (filename, note, received_at) VALUES (?, ?, datetime('now'))",
            (stamped_name, note),
        )
        conn.commit()

    return jsonify({"filename": stamped_name, "size": len(content), "saved": True}), 200
