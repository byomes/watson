"""jobs/comms/api.py — Flask Blueprint for the comms-desk Next.js app → Watson DB.

Mount on the Watson dashboard app:
    from jobs.comms.api import comms_bp
    app.register_blueprint(comms_bp)

Auth: shared-secret X-Watson-Key header (COMMS_API_KEY), same pattern as
Writing Room / bodyrec. Role-based permission checks (who can edit/cancel
whose content) are enforced here, server-side, by looking the caller's role
up from comms_users via user_id — never trusted from client-supplied fields —
since this endpoint is reachable from the public internet via the Tailscale
Funnel the same as every other Watson API blueprint.
"""
import base64
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.campaigns.brevo_sync import sync_once as brevo_sync_once
from jobs.comms import GENERAL_COMMS_CAMPAIGN_ID, generate_password, get_db, send_telegram
from jobs.comms.reset import confirm_reset, request_reset

log = logging.getLogger(__name__)

comms_bp = Blueprint("comms", __name__)

_API_KEY = lambda: os.getenv("COMMS_API_KEY", "")
_HOLD_MINUTES = 12

_ASSETS_DIR = Path.home() / "comms-assets"
_ASSETS_CACHE = Path.home() / "watson" / "data" / "comms_assets_cache"
_ASSETS_RAW_BASE = "https://raw.githubusercontent.com/byomes/comms-assets/main"


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _user(conn, user_id):
    return conn.execute("SELECT * FROM comms_users WHERE id=?", (user_id,)).fetchone()


# ── Auth ─────────────────────────────────────────────────────────────────────

@comms_bp.route("/api/comms/login", methods=["POST"])
@_require_key
def login():
    data = request.get_json(force=True) or {}
    username, password = data.get("username", ""), data.get("password", "")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM comms_users WHERE username=?", (username,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return jsonify({"error": "invalid credentials"}), 401
        conn.execute("UPDATE comms_users SET last_login=datetime('now') WHERE id=?", (row["id"],))
        conn.commit()
        return jsonify({
            "userId": row["id"], "username": row["username"],
            "displayName": row["display_name"], "role": row["role"],
        })
    finally:
        conn.close()


@comms_bp.route("/api/comms/reset-request", methods=["POST"])
@_require_key
def reset_request():
    data = request.get_json(force=True) or {}
    found = request_reset(data.get("username", ""))
    return jsonify({"sent": found})  # always 200 — no enumeration


@comms_bp.route("/api/comms/reset-confirm", methods=["POST"])
@_require_key
def reset_confirm():
    data = request.get_json(force=True) or {}
    new_password = confirm_reset(data.get("token", ""))
    if not new_password:
        return jsonify({"error": "invalid or expired token"}), 400
    return jsonify({"newPassword": new_password})


# ── User management (admin only) ────────────────────────────────────────────

@comms_bp.route("/api/comms/users", methods=["GET"])
@_require_key
def list_users():
    caller = request.args.get("as_user_id", type=int)
    conn = get_db()
    try:
        caller_row = _user(conn, caller)
        if not caller_row or caller_row["role"] != "admin":
            return jsonify({"error": "forbidden"}), 403
        rows = conn.execute(
            "SELECT id, username, display_name, role, last_login FROM comms_users"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@comms_bp.route("/api/comms/users", methods=["POST"])
@_require_key
def create_user():
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        if not caller_row or caller_row["role"] != "admin":
            return jsonify({"error": "forbidden"}), 403

        password = generate_password()
        conn.execute(
            """INSERT INTO comms_users (username, email, password_hash, display_name, role)
               VALUES (?, ?, ?, ?, ?)""",
            (data["username"], data["email"], generate_password_hash(password),
             data["display_name"], data.get("role", "volunteer")),
        )
        conn.commit()
        return jsonify({"tempPassword": password})
    finally:
        conn.close()


# ── Brevo lists / contacts (email composer audience picker) ────────────────

@comms_bp.route("/api/comms/brevo/lists", methods=["GET"])
@_require_key
def get_brevo_lists():
    """Reads jobs/campaigns/brevo_sync.py's local mirror (brevo_lists +
    brevo_list_membership), not Brevo live — see that module's docstring.
    Same response shape as the old live jobs.campaigns.brevo_contacts.list_lists()
    call this replaced: [{id, name, count}, ...]."""
    conn = get_db()
    try:
        if not _user(conn, request.args.get("as_user_id", type=int)):
            return jsonify({"error": "forbidden"}), 403
        rows = conn.execute("""
            SELECT l.id AS id, l.name AS name, COUNT(m.contact_id) AS count
            FROM brevo_lists l
            LEFT JOIN brevo_list_membership m ON m.list_id = l.id
            GROUP BY l.id, l.name
            ORDER BY l.name COLLATE NOCASE
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.warning("Brevo lists mirror read failed: %s", exc)
        return jsonify({"error": "brevo mirror unavailable"}), 502
    finally:
        conn.close()


@comms_bp.route("/api/comms/brevo/contacts", methods=["GET"])
@_require_key
def get_brevo_contacts():
    """Full contact roster for the "choose specific people" picker — not
    list-scoped, since Kaci may want someone outside any imported list.
    Reads the local mirror, not Brevo live — see
    jobs/campaigns/brevo_sync.py. Same response shape as the old live
    jobs.campaigns.brevo_contacts.list_contacts() call this replaced:
    [{email, name}, ...]."""
    conn = get_db()
    try:
        if not _user(conn, request.args.get("as_user_id", type=int)):
            return jsonify({"error": "forbidden"}), 403
        rows = conn.execute("""
            SELECT email, TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS name
            FROM brevo_contacts
            ORDER BY email
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.warning("Brevo contacts mirror read failed: %s", exc)
        return jsonify({"error": "brevo mirror unavailable"}), 502
    finally:
        conn.close()


@comms_bp.route("/api/comms/brevo/refresh", methods=["POST"])
@_require_key
def refresh_brevo():
    """On-demand trigger for jobs/campaigns/brevo_sync.py's full pull —
    same sync_once() the hourly cron calls, just invoked immediately so
    Kaci can refresh the mirror right before finalizing a send instead of
    waiting for the next hourly run."""
    conn = get_db()
    try:
        if not _user(conn, request.args.get("as_user_id", type=int)):
            return jsonify({"error": "forbidden"}), 403
    finally:
        conn.close()
    try:
        return jsonify(brevo_sync_once())
    except Exception as exc:
        log.warning("Brevo on-demand refresh failed: %s", exc)
        return jsonify({"error": "brevo unavailable"}), 502


# ── Sends (calendar / composer) ─────────────────────────────────────────────

_RECIPIENT_MODES = ("segment", "brevo_list", "custom_emails")

def _row_to_dict(row, hold=None):
    d = dict(row)
    if hold:
        d["holdReleasesAt"] = hold["held_until"]
        d["holdId"] = hold["id"]
    return d


@comms_bp.route("/api/comms/sends", methods=["GET"])
@_require_key
def list_sends():
    caller = request.args.get("as_user_id", type=int)
    conn = get_db()
    try:
        caller_row = _user(conn, caller)
        if not caller_row:
            return jsonify({"error": "forbidden"}), 403

        if caller_row["role"] == "admin":
            rows = conn.execute(
                "SELECT * FROM book_launch_sends WHERE campaign_id=? AND source='comms_desk' "
                "ORDER BY send_date",
                (GENERAL_COMMS_CAMPAIGN_ID,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM book_launch_sends WHERE campaign_id=? AND source='comms_desk' "
                "AND author_user_id=? ORDER BY send_date",
                (GENERAL_COMMS_CAMPAIGN_ID, caller),
            ).fetchall()

        holds = {
            h["send_id"]: h for h in conn.execute(
                "SELECT * FROM comms_holds WHERE released_at IS NULL AND canceled_at IS NULL"
            ).fetchall()
        }
        return jsonify([_row_to_dict(r, holds.get(r["id"])) for r in rows])
    finally:
        conn.close()


@comms_bp.route("/api/comms/sends", methods=["POST"])
@_require_key
def create_send():
    """Create a draft (status='scheduled', no hold — Drafted state)."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        if not caller_row:
            return jsonify({"error": "forbidden"}), 403

        recipient_mode = data.get("recipient_mode", "segment")
        if recipient_mode not in _RECIPIENT_MODES:
            return jsonify({"error": "invalid recipient_mode"}), 400
        if recipient_mode == "segment":
            segment, recipient_detail = data["segment"], None
        else:
            # 'general' is a placeholder here, not a real target — the CHECK
            # constraint on `segment` predates recipient_mode and only allows
            # public/general/donor/arc; dispatch.py ignores `segment` entirely
            # once recipient_mode is 'brevo_list' or 'custom_emails'.
            segment = "general"
            recipient_detail = json.dumps(data.get("recipient_detail") or {})

        cur = conn.execute(
            """INSERT INTO book_launch_sends
               (campaign_id, week_number, send_date, platform, segment, subject, body_text,
                image_template_type, image_path, status, source, author_user_id,
                recipient_mode, recipient_detail)
               VALUES (?, 0, ?, ?, ?, ?, ?, NULL, ?, 'scheduled', 'comms_desk', ?, ?, ?)""",
            (GENERAL_COMMS_CAMPAIGN_ID, data["send_date"], data["platform"], segment,
             data.get("subject"), data["body_text"], data.get("image_path"), caller_row["id"],
             recipient_mode, recipient_detail),
        )
        conn.commit()
        return jsonify({"id": cur.lastrowid})
    finally:
        conn.close()


def _owns_or_admin(conn, caller_row, send_row):
    if caller_row["role"] == "admin":
        return True
    return send_row and send_row["author_user_id"] == caller_row["id"]


@comms_bp.route("/api/comms/sends/<int:send_id>", methods=["PUT"])
@_require_key
def edit_send(send_id):
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        send_row = conn.execute("SELECT * FROM book_launch_sends WHERE id=?", (send_id,)).fetchone()
        if not caller_row or not send_row or not _owns_or_admin(conn, caller_row, send_row):
            return jsonify({"error": "forbidden"}), 403
        if send_row["status"] not in ("scheduled",):
            return jsonify({"error": "cannot edit a send that is ready or sent"}), 400

        fields = {k: data[k] for k in
                  ("send_date", "subject", "body_text", "segment", "image_path", "recipient_mode")
                  if k in data}
        if "recipient_detail" in data:
            detail = data["recipient_detail"]
            fields["recipient_detail"] = json.dumps(detail) if detail is not None else None
        if not fields:
            return jsonify({"ok": True})
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE book_launch_sends SET {set_clause}, status='edited' WHERE id=?",
            (*fields.values(), send_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@comms_bp.route("/api/comms/sends/<int:send_id>/ready", methods=["POST"])
@_require_key
def mark_ready(send_id):
    """Path A (future send_date): flips straight to status='approved' — the
    existing 15-min sweeps pick it up whenever it's due. Path B (send now):
    stays 'scheduled', creates a comms_holds row with a 12-min undo window;
    jobs/comms/release_holds.py flips it to 'approved' once the hold expires."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        send_row = conn.execute("SELECT * FROM book_launch_sends WHERE id=?", (send_id,)).fetchone()
        if not caller_row or not send_row or not _owns_or_admin(conn, caller_row, send_row):
            return jsonify({"error": "forbidden"}), 403

        send_now = bool(data.get("send_now"))
        if send_now:
            conn.execute(
                "UPDATE book_launch_sends SET send_date=date('now') WHERE id=?", (send_id,)
            )
            held_until = (datetime.utcnow() + timedelta(minutes=_HOLD_MINUTES)).isoformat()
            cur = conn.execute(
                "INSERT INTO comms_holds (send_id, held_until) VALUES (?, ?)",
                (send_id, held_until),
            )
            conn.commit()
            return jsonify({"status": "held", "holdId": cur.lastrowid, "holdReleasesAt": held_until})

        conn.execute("UPDATE book_launch_sends SET status='approved' WHERE id=?", (send_id,))
        conn.commit()
        return jsonify({"status": "approved"})
    finally:
        conn.close()


@comms_bp.route("/api/comms/sends/<int:send_id>/cancel", methods=["POST"])
@_require_key
def cancel_send(send_id):
    """Cancel a not-yet-released hold, or (admin only) pull an approved-but-
    not-yet-sent row entirely."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        send_row = conn.execute("SELECT * FROM book_launch_sends WHERE id=?", (send_id,)).fetchone()
        if not caller_row or not send_row:
            return jsonify({"error": "forbidden"}), 403

        hold = conn.execute(
            "SELECT * FROM comms_holds WHERE send_id=? AND released_at IS NULL AND canceled_at IS NULL",
            (send_id,),
        ).fetchone()
        if hold:
            if not _owns_or_admin(conn, caller_row, send_row):
                return jsonify({"error": "forbidden"}), 403
            conn.execute("UPDATE comms_holds SET canceled_at=datetime('now') WHERE id=?", (hold["id"],))
            conn.execute("UPDATE book_launch_sends SET status='skipped' WHERE id=?", (send_id,))
            conn.commit()
            return jsonify({"status": "skipped"})

        # No active hold: pulling an approved-but-unsent row is admin-only per spec.
        if send_row["status"] == "approved":
            if caller_row["role"] != "admin":
                return jsonify({"error": "forbidden"}), 403
            conn.execute("UPDATE book_launch_sends SET status='skipped' WHERE id=?", (send_id,))
            conn.commit()
            return jsonify({"status": "skipped"})

        return jsonify({"error": "nothing to cancel"}), 400
    finally:
        conn.close()


# ── Sent log (Bill's read-only view) ────────────────────────────────────────

@comms_bp.route("/api/comms/sent-log", methods=["GET"])
@_require_key
def sent_log():
    caller = request.args.get("as_user_id", type=int)
    conn = get_db()
    try:
        caller_row = _user(conn, caller)
        if not caller_row or caller_row["role"] != "admin":
            return jsonify({"error": "forbidden"}), 403
        rows = conn.execute(
            "SELECT * FROM book_launch_sends WHERE campaign_id=? AND source='comms_desk' "
            "AND status='sent' ORDER BY sent_at DESC LIMIT 200",
            (GENERAL_COMMS_CAMPAIGN_ID,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ── Image upload ─────────────────────────────────────────────────────────────

@comms_bp.route("/api/comms/upload-image", methods=["POST"])
@_require_key
def upload_image():
    """Accepts {filename, content_base64, kind: 'facebook'|'email'}. Writes to
    a local cache path (what dispatch_facebook_row()/facebook_post.py actually
    read — they treat image_path as a local filesystem path, not a URL) and
    commits the same bytes to comms-assets for durable storage / raw-URL
    display in the composer preview. Returns both; callers store `imagePath`
    (local) on the send row, not `rawUrl`."""
    data = request.get_json(force=True) or {}
    kind = data.get("kind", "email")
    if kind not in ("facebook", "email"):
        return jsonify({"error": "invalid kind"}), 400

    ext = Path(data.get("filename", "")).suffix or ".jpg"
    fname = f"{uuid.uuid4().hex}{ext}"
    rel_path = f"{kind}/{fname}"

    try:
        content = base64.b64decode(data["content_base64"])
    except Exception:
        return jsonify({"error": "invalid content_base64"}), 400

    _ASSETS_CACHE.mkdir(parents=True, exist_ok=True)
    local_path = _ASSETS_CACHE / fname
    local_path.write_bytes(content)

    try:
        repo_path = _ASSETS_DIR / rel_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo_path.write_bytes(content)
        subprocess.run(["git", "-C", str(_ASSETS_DIR), "add", rel_path], check=True)
        subprocess.run(
            ["git", "-C", str(_ASSETS_DIR), "-c", "user.email=watson@williamckyomes.com",
             "-c", "user.name=Watson", "commit", "-m", f"Comms Desk upload: {rel_path}"],
            check=True,
        )
        subprocess.run(["git", "-C", str(_ASSETS_DIR), "push", "origin", "main"], check=True)
    except Exception as exc:
        log.warning("comms-assets commit failed (image still usable locally): %s", exc)

    return jsonify({
        "imagePath": str(local_path),
        "rawUrl": f"{_ASSETS_RAW_BASE}/{rel_path}",
    })
