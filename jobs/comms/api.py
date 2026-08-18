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

from jobs.campaigns.brevo_contacts import list_contacts as brevo_list_contacts
from jobs.campaigns.brevo_contacts import list_lists as brevo_list_lists
from jobs.comms import GENERAL_COMMS_CAMPAIGN_ID, generate_password, get_db, send_telegram
from jobs.comms.reset import confirm_reset, request_reset
from jobs.design import svg_generator

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
    conn = get_db()
    try:
        if not _user(conn, request.args.get("as_user_id", type=int)):
            return jsonify({"error": "forbidden"}), 403
    finally:
        conn.close()
    try:
        return jsonify(brevo_list_lists())
    except Exception as exc:
        log.warning("Brevo lists fetch failed: %s", exc)
        return jsonify({"error": "brevo unavailable"}), 502


@comms_bp.route("/api/comms/brevo/contacts", methods=["GET"])
@_require_key
def get_brevo_contacts():
    """Full contact roster for the "choose specific people" picker — not
    list-scoped, since Kaci may want someone outside any imported list."""
    conn = get_db()
    try:
        if not _user(conn, request.args.get("as_user_id", type=int)):
            return jsonify({"error": "forbidden"}), 403
    finally:
        conn.close()
    try:
        return jsonify(brevo_list_contacts())
    except Exception as exc:
        log.warning("Brevo contacts fetch failed: %s", exc)
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


def _create_one_send(conn, caller_row, item: dict) -> tuple[int, str | None]:
    """Validates + inserts one book_launch_sends row. Shared by the single-item
    create_send() route and the batch import route below, so a Claude.ai batch
    and Kaci's own composer go through identical image_intent handling.

    `item.image_intent` (default 'none'):
      - 'none' — manual image_path (or none) supplied by the caller, as before.
      - 'ai_quote' — Facebook only; renders a branded quote card via
        svg_generator.create_quote_card(quote_text, quote_attribution) and
        attaches it as image_path. needs_image stays 0.
      - 'needs_manual' — Facebook only; image_path stays NULL, needs_image=1
        so Comms Desk can badge it until someone attaches a real photo.

    Returns (row_id, rel_path) — rel_path is the comms-assets-relative path of
    a newly generated quote card (for the caller to batch into one commit), or
    None if no asset was generated. Raises ValueError on any bad input — the
    caller decides whether that's a 400 or a per-item batch failure.
    """
    recipient_mode = item.get("recipient_mode", "segment")
    if recipient_mode not in _RECIPIENT_MODES:
        raise ValueError("invalid recipient_mode")
    if recipient_mode == "segment":
        segment, recipient_detail = item["segment"], None
    else:
        # 'general' is a placeholder here, not a real target — the CHECK
        # constraint on `segment` predates recipient_mode and only allows
        # public/general/donor/arc; dispatch.py ignores `segment` entirely
        # once recipient_mode is 'brevo_list' or 'custom_emails'.
        segment = "general"
        recipient_detail = json.dumps(item.get("recipient_detail") or {})

    platform = item["platform"]
    image_intent = item.get("image_intent", "none")
    if image_intent not in ("none", "ai_quote", "needs_manual"):
        raise ValueError("invalid image_intent")
    if image_intent != "none" and platform != "facebook":
        raise ValueError("image_intent is Facebook-only")

    image_path = item.get("image_path")
    needs_image = 0
    rel_path = None
    if image_intent == "ai_quote":
        quote_text = item.get("quote_text")
        if not quote_text:
            raise ValueError("quote_text required for image_intent=ai_quote")
        attribution = item.get("quote_attribution") or "Dr. Bill Yomes"
        png_path = svg_generator.create_quote_card(quote_text, attribution)
        if str(png_path).startswith("Error:"):
            raise ValueError(f"quote card generation failed: {png_path}")
        content = Path(png_path).read_bytes()
        image_path, rel_path = _write_asset(content, "facebook", ".png")
    elif image_intent == "needs_manual":
        image_path = None
        needs_image = 1

    cur = conn.execute(
        """INSERT INTO book_launch_sends
           (campaign_id, week_number, send_date, send_time, platform, segment, subject, body_text,
            image_template_type, image_path, status, source, author_user_id,
            recipient_mode, recipient_detail, needs_image)
           VALUES (?, 0, ?, ?, ?, ?, ?, ?, NULL, ?, 'scheduled', 'comms_desk', ?, ?, ?, ?)""",
        (GENERAL_COMMS_CAMPAIGN_ID, item["send_date"], item.get("send_time"), platform, segment,
         item.get("subject"), item["body_text"], image_path, caller_row["id"],
         recipient_mode, recipient_detail, needs_image),
    )
    return cur.lastrowid, rel_path


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

        try:
            row_id, rel_path = _create_one_send(conn, caller_row, data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        conn.commit()
        if rel_path:
            _commit_assets([rel_path], f"Comms Desk quote card: {rel_path}")
        return jsonify({"id": row_id})
    finally:
        conn.close()


@comms_bp.route("/api/comms/sends/batch", methods=["POST"])
@_require_key
def create_sends_batch():
    """Claude.ai batch import — one request, many draft rows. Each item goes
    through the same _create_one_send() validation as the single-item route;
    a bad item fails only itself (ok:False in its result slot), never the
    rest of the batch. Generated quote-card assets are swept into a single
    comms-assets commit at the end instead of one push per item."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        if not caller_row:
            return jsonify({"error": "forbidden"}), 403

        results = []
        generated_rel_paths = []
        for item in data.get("items", []):
            try:
                row_id, rel_path = _create_one_send(conn, caller_row, item)
                results.append({"ok": True, "id": row_id})
                if rel_path:
                    generated_rel_paths.append(rel_path)
            except Exception as exc:
                results.append({"ok": False, "error": str(exc)})
        conn.commit()
        _commit_assets(
            generated_rel_paths,
            f"Comms Desk batch import: {len(generated_rel_paths)} quote card(s)",
        )
        return jsonify({"results": results})
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
        editable = send_row["status"] in ("scheduled", "edited") or (
            send_row["platform"] == "brevo"
            and send_row["status"] == "approved"
            and not send_row["admin_approved_at"]
        )
        if not editable:
            return jsonify({"error": "cannot edit a send that is ready or sent"}), 400

        fields = {k: data[k] for k in
                  ("send_date", "send_time", "subject", "body_text", "segment", "image_path",
                   "recipient_mode", "needs_image")
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
    jobs/comms/release_holds.py flips it to 'approved' once the hold expires.

    Brevo rows skip Path B entirely regardless of send_now: an email always
    still needs a separate admin approval (see /approve-send and
    send_brevo_row()'s admin_approved_at gate) before it can actually go out,
    so the 12-minute undo hold's urgency doesn't apply — there's no way for
    an email to fire without that extra click either way."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        send_row = conn.execute("SELECT * FROM book_launch_sends WHERE id=?", (send_id,)).fetchone()
        if not caller_row or not send_row or not _owns_or_admin(conn, caller_row, send_row):
            return jsonify({"error": "forbidden"}), 403

        send_now = bool(data.get("send_now")) and send_row["platform"] != "brevo"
        if send_now:
            # Clear any previously-chosen send_time too — "send now" means now,
            # not whatever time-of-day was picked for a since-abandoned schedule.
            conn.execute(
                "UPDATE book_launch_sends SET send_date=date('now'), send_time=NULL WHERE id=?", (send_id,)
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


@comms_bp.route("/api/comms/sends/<int:send_id>/approve-send", methods=["POST"])
@_require_key
def approve_send(send_id):
    """Admin-only extra gate for Comms Desk emails, separate from whoever
    marked the row 'ready'. Sets admin_approved_at, which
    jobs/campaigns/dispatch.py:send_brevo_row() requires (for
    source='comms_desk' rows) before it will actually call Brevo — see
    schema.py's admin_approved_at comment. Facebook rows don't use this at
    all; they're unaffected and still fire straight off 'approved'/holds."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        caller_row = _user(conn, data.get("as_user_id"))
        if not caller_row or caller_row["role"] != "admin":
            return jsonify({"error": "forbidden"}), 403

        send_row = conn.execute("SELECT * FROM book_launch_sends WHERE id=?", (send_id,)).fetchone()
        if not send_row or send_row["platform"] != "brevo":
            return jsonify({"error": "not an email send"}), 400
        if send_row["status"] != "approved" or send_row["admin_approved_at"]:
            return jsonify({"error": "not pending approval"}), 400

        conn.execute(
            "UPDATE book_launch_sends SET admin_approved_at=datetime('now') WHERE id=?", (send_id,)
        )
        conn.commit()
        return jsonify({"status": "approved_for_send"})
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

def _write_asset(content: bytes, kind: str, ext: str = ".jpg") -> tuple[str, str]:
    """Writes `content` to the local cache path (what dispatch_facebook_row()/
    facebook_post.py actually read) and the comms-assets repo working copy.
    Returns (local_path, rel_path); does NOT commit/push — call
    _commit_assets() once per request so N writes cost one push, not N."""
    fname = f"{uuid.uuid4().hex}{ext}"
    rel_path = f"{kind}/{fname}"
    _ASSETS_CACHE.mkdir(parents=True, exist_ok=True)
    local_path = _ASSETS_CACHE / fname
    local_path.write_bytes(content)
    repo_path = _ASSETS_DIR / rel_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    repo_path.write_bytes(content)
    return str(local_path), rel_path


def _commit_assets(rel_paths: list[str], message: str) -> None:
    if not rel_paths:
        return
    try:
        subprocess.run(["git", "-C", str(_ASSETS_DIR), "add", *rel_paths], check=True)
        subprocess.run(
            ["git", "-C", str(_ASSETS_DIR), "-c", "user.email=watson@williamckyomes.com",
             "-c", "user.name=Watson", "commit", "-m", message],
            check=True,
        )
        subprocess.run(["git", "-C", str(_ASSETS_DIR), "push", "origin", "main"], check=True)
    except Exception as exc:
        log.warning("comms-assets commit failed (images still usable locally): %s", exc)


@comms_bp.route("/api/comms/upload-image", methods=["POST"])
@_require_key
def upload_image():
    """Accepts {filename, content_base64, kind: 'facebook'|'email'}. Writes to
    a local cache path and commits the same bytes to comms-assets for durable
    storage / raw-URL display in the composer preview. Returns both; callers
    store `imagePath` (local) on the send row, not `rawUrl`."""
    data = request.get_json(force=True) or {}
    kind = data.get("kind", "email")
    if kind not in ("facebook", "email"):
        return jsonify({"error": "invalid kind"}), 400

    ext = Path(data.get("filename", "")).suffix or ".jpg"

    try:
        content = base64.b64decode(data["content_base64"])
    except Exception:
        return jsonify({"error": "invalid content_base64"}), 400

    local_path, rel_path = _write_asset(content, kind, ext)
    _commit_assets([rel_path], f"Comms Desk upload: {rel_path}")

    return jsonify({
        "imagePath": local_path,
        "rawUrl": f"{_ASSETS_RAW_BASE}/{rel_path}",
    })
