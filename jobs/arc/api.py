"""jobs/arc/api.py — Flask Blueprint for ARC reader signups + admin actions.

Mount on the Watson dashboard app:
    from jobs.arc.api import arc_bp
    app.register_blueprint(arc_bp)

Also register arc_auth_bp:
    from jobs.arc.auth import arc_auth_bp
    app.register_blueprint(arc_auth_bp)
"""
import logging
import os
import secrets
import sys
import threading
import uuid
from functools import wraps
from pathlib import Path

import bcrypt
import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import get_db, send_telegram

log = logging.getLogger(__name__)

arc_bp = Blueprint("arc", __name__)

_API_KEY    = lambda: os.getenv("WRITING_ROOM_API_KEY", "")
_KIT_SECRET = lambda: os.getenv("KIT_API_SECRET", "")
_ARC_TAG_ID = 19285341  # Kit tag applied to every ARC signup

_COMMITMENT_TEXTS = [
    "Pray for the book's impact",
    "Read the book before the launch date",
    "Post an honest review on Amazon on launch day",
    "Share about the book on at least one social media platform",
    "Tell people in your life who you think would connect with this book",
]
_EVIDENCE_REQUIRED = {3, 4, 5}  # commitment_number values


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _ensure_table() -> None:
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_readers (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name                TEXT NOT NULL,
                last_name                 TEXT NOT NULL,
                email                     TEXT NOT NULL UNIQUE,
                book_slug                 TEXT NOT NULL DEFAULT 'the-wrong-jesus',
                agreed_to_commitments     INTEGER NOT NULL DEFAULT 0,
                status                    TEXT NOT NULL DEFAULT 'active',
                kit_tag_applied           INTEGER NOT NULL DEFAULT 0,
                created_at                TEXT NOT NULL DEFAULT (datetime('now')),
                login_token               TEXT UNIQUE,
                password_hash             TEXT,
                first_login_at            TEXT,
                approved_for_writing_room INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_reader_commitments (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                arc_reader_id         INTEGER NOT NULL REFERENCES arc_readers(id),
                commitment_number     INTEGER NOT NULL,
                commitment_text       TEXT NOT NULL,
                is_checked            INTEGER NOT NULL DEFAULT 0,
                evidence_text         TEXT,
                submitted_at          TEXT,
                flagged_as_suspicious INTEGER NOT NULL DEFAULT 0,
                approved_by_admin     INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                arc_reader_id INTEGER NOT NULL REFERENCES arc_readers(id),
                session_token TEXT NOT NULL UNIQUE,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arc_reader_feedback (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                arc_reader_id  INTEGER NOT NULL REFERENCES arc_readers(id),
                target_type    TEXT NOT NULL,
                target_slug    TEXT NOT NULL,
                reaction       TEXT,
                comment        TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Idempotent column additions for existing rows
        # Note: SQLite ALTER TABLE ADD COLUMN cannot use UNIQUE — use a separate index instead
        for sql in [
            "ALTER TABLE arc_readers ADD COLUMN login_token TEXT",
            "ALTER TABLE arc_readers ADD COLUMN password_hash TEXT",
            "ALTER TABLE arc_readers ADD COLUMN first_login_at TEXT",
            "ALTER TABLE arc_readers ADD COLUMN approved_for_writing_room INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE writing_room_partners ADD COLUMN first_login_at TEXT",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_arc_readers_login_token ON arc_readers(login_token)",
        ]:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass
    finally:
        conn.close()


def _kit_tag_subscriber(email: str, first_name: str, last_name: str) -> bool:
    """Apply Kit tag _ARC_TAG_ID to the subscriber via Kit v3. Returns True on success."""
    secret = _KIT_SECRET()
    if not secret:
        log.warning("KIT_API_SECRET not set — skipping Kit tag for %s", email)
        return False
    try:
        resp = requests.post(
            f"https://api.convertkit.com/v3/tags/{_ARC_TAG_ID}/subscribe",
            json={
                "api_secret": secret,
                "first_name": first_name,
                "email": email,
                "fields": {"last_name": last_name},
            },
            timeout=10,
        )
        if resp.ok:
            return True
        log.warning("Kit tag apply failed (%s): %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("Kit tag request error for %s: %s", email, exc)
        return False


def _seed_commitments(conn, reader_id: int) -> None:
    for i, text in enumerate(_COMMITMENT_TEXTS, start=1):
        conn.execute(
            "INSERT OR IGNORE INTO arc_reader_commitments "
            "(arc_reader_id, commitment_number, commitment_text, is_checked, submitted_at) "
            "VALUES (?, ?, ?, 0, NULL)",
            (reader_id, i, text),
        )


@arc_bp.route("/api/arc/apply", methods=["POST"])
@_require_key
def arc_apply():
    _ensure_table()
    data       = request.get_json(force=True)
    first_name = (data.get("firstName") or "").strip()
    last_name  = (data.get("lastName") or "").strip()
    email      = (data.get("email") or "").strip().lower()

    if not (first_name and last_name and email):
        return jsonify({"error": "firstName, lastName, and email are required"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM arc_readers WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "message": "already registered"}), 200

        temp_password = secrets.token_urlsafe(12)
        pw_hash       = bcrypt.hashpw(temp_password.encode(), bcrypt.gensalt()).decode()
        login_token   = str(uuid.uuid4())

        cursor = conn.execute(
            "INSERT INTO arc_readers "
            "(first_name, last_name, email, agreed_to_commitments, login_token, password_hash) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (first_name, last_name, email, login_token, pw_hash),
        )
        reader_id = cursor.lastrowid
        _seed_commitments(conn, reader_id)
        conn.commit()
    except Exception as exc:
        log.error("ARC reader insert failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()

    tagged = _kit_tag_subscriber(email, first_name, last_name)

    if tagged:
        conn2 = get_db()
        try:
            conn2.execute(
                "UPDATE arc_readers SET kit_tag_applied = 1 WHERE id = ?", (reader_id,)
            )
            conn2.commit()
        finally:
            conn2.close()

    def _send_confirmation():
        try:
            from jobs.arc.send_signup_confirmation import send_signup_confirmation
            send_signup_confirmation(email, first_name, temp_password)
        except Exception as exc:
            log.error("Signup confirmation email failed for %s: %s", email, exc)

    threading.Thread(target=_send_confirmation, daemon=True).start()

    try:
        send_telegram(
            f"📖 New ARC Reader\n\n"
            f"Name: {first_name} {last_name}\n"
            f"Email: {email}\n"
            f"Book: The Wrong Jesus\n"
            f"Kit tag: {'✅ applied' if tagged else '⚠️ failed — check KIT_API_SECRET'}"
        )
    except Exception as exc:
        log.error("Telegram notify failed for ARC reader %s: %s", email, exc)

    return jsonify({"ok": True}), 200


# ── Admin: commitment approve / reject ────────────────────────────────────────

@arc_bp.route("/api/arc/commitments/<int:commitment_id>/approve", methods=["PATCH"])
@_require_key
def approve_commitment(commitment_id: int):
    _ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM arc_reader_commitments WHERE id = ?", (commitment_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE arc_reader_commitments "
            "SET approved_by_admin = 1, flagged_as_suspicious = 0 WHERE id = ?",
            (commitment_id,),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@arc_bp.route("/api/arc/commitments/<int:commitment_id>/reject", methods=["PATCH"])
@_require_key
def reject_commitment(commitment_id: int):
    _ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM arc_reader_commitments WHERE id = ?", (commitment_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE arc_reader_commitments "
            "SET approved_by_admin = 0, flagged_as_suspicious = 0, "
            "is_checked = 0, submitted_at = NULL WHERE id = ?",
            (commitment_id,),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── Admin: list readers with commitments ──────────────────────────────────────

@arc_bp.route("/api/arc/readers/commitments", methods=["GET"])
@_require_key
def list_readers_with_commitments():
    _ensure_table()
    conn = get_db()
    try:
        readers = conn.execute(
            "SELECT id, first_name, last_name, email, status, approved_for_writing_room, created_at "
            "FROM arc_readers ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in readers:
            commitments = conn.execute(
                "SELECT id, commitment_number, commitment_text, is_checked, evidence_text, "
                "submitted_at, flagged_as_suspicious, approved_by_admin "
                "FROM arc_reader_commitments WHERE arc_reader_id = ? "
                "ORDER BY commitment_number",
                (r["id"],),
            ).fetchall()
            result.append({
                "id": r["id"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "email": r["email"],
                "status": r["status"],
                "approved_for_writing_room": r["approved_for_writing_room"],
                "created_at": r["created_at"],
                "commitments": [dict(c) for c in commitments],
            })
        return jsonify(result), 200
    finally:
        conn.close()


# ── Admin: password reset / resend welcome ────────────────────────────────────

def set_reader_password(reader_id: int, new_password: str) -> None:
    """Bcrypt-hash new_password and store it on the reader row — same pattern as
    jobs.writing_room.api.set_partner_password() and jobs.publishing.set_reader_password()."""
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = get_db()
    try:
        conn.execute("UPDATE arc_readers SET password_hash = ? WHERE id = ?", (pw_hash, reader_id))
        conn.commit()
    finally:
        conn.close()


def resend_welcome(reader_id: int) -> bool:
    """Re-send the signup confirmation email with a freshly generated password.

    ARC has no token-based verify/reset flow like Writing Room — the original
    confirmation email carries the plaintext password directly, and only the
    bcrypt hash is ever stored — so "resend" necessarily means "issue a new one."
    """
    conn = get_db()
    try:
        reader = conn.execute(
            "SELECT id, first_name, email FROM arc_readers WHERE id = ?", (reader_id,)
        ).fetchone()
    finally:
        conn.close()
    if not reader:
        return False

    new_password = secrets.token_urlsafe(12)
    set_reader_password(reader_id, new_password)

    def _send():
        try:
            from jobs.arc.send_signup_confirmation import send_signup_confirmation
            send_signup_confirmation(reader["email"], reader["first_name"], new_password)
        except Exception as exc:
            log.error("Resend welcome email failed for ARC reader %s: %s", reader["email"], exc)

    threading.Thread(target=_send, daemon=True).start()
    return True


# ── Admin: invite to Writing Room ─────────────────────────────────────────────

def invite_reader_to_writing_room(reader_id: int) -> tuple[bool, str | None, int]:
    """Core logic behind /api/arc/invite-to-writing-room, factored out so both the
    external (X-Watson-Key) route and the dashboard-only route can call it without
    duplicating the commitment-check / insert / Kit-tag / email flow.

    Returns (ok, error_message, http_status).
    """
    _ensure_table()
    conn = get_db()
    try:
        reader = conn.execute(
            "SELECT id, first_name, last_name, email, password_hash, approved_for_writing_room "
            "FROM arc_readers WHERE id = ?",
            (reader_id,),
        ).fetchone()
        if not reader:
            return False, "reader not found", 404

        if reader["approved_for_writing_room"]:
            return False, "reader already invited to Writing Room", 409

        # Verify all 5 commitments are admin-approved
        commitments = conn.execute(
            "SELECT commitment_number, approved_by_admin FROM arc_reader_commitments "
            "WHERE arc_reader_id = ? ORDER BY commitment_number",
            (reader_id,),
        ).fetchall()
        if len(commitments) != 5:
            return False, "commitment records incomplete — cannot invite", 400
        unapproved = [c["commitment_number"] for c in commitments if not c["approved_by_admin"]]
        if unapproved:
            return False, f"commitments {unapproved} not yet approved", 400

        name    = f"{reader['first_name']} {reader['last_name']}"
        email   = reader["email"]
        pw_hash = reader["password_hash"]

        # Create writing_room_partners row (username = email, password = same as ARC)
        from datetime import datetime as _dt
        joined_at = _dt.utcnow().isoformat()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO writing_room_partners "
                "(name, email, username, password_hash, status, joined_at, agreed_to_participate) "
                "VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (name, email, email, pw_hash, joined_at),
            )
        except Exception as exc:
            log.warning("writing_room_partners insert skipped (may already exist): %s", exc)

        conn.execute(
            "UPDATE arc_readers SET approved_for_writing_room = 1 WHERE id = ?",
            (reader_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Apply writing-room-partner Kit tag
    def _tag_and_notify():
        try:
            from jobs.writing_room.onboard import kit_tag_on_activation
            kit_tag_on_activation(email, reader["first_name"])
        except Exception as exc:
            log.error("Kit tag failed for ARC-promoted partner %s: %s", email, exc)
        try:
            from jobs.arc.send_invite_email import send_arc_invite_email
            send_arc_invite_email(email, reader["first_name"])
        except Exception as exc:
            log.error("ARC invite email failed for %s: %s", email, exc)

    threading.Thread(target=_tag_and_notify, daemon=True).start()

    log.info("ARC reader %d (%s) invited to Writing Room.", reader_id, email)
    return True, None, 200


@arc_bp.route("/api/arc/invite-to-writing-room", methods=["POST"])
@_require_key
def invite_to_writing_room():
    data      = request.get_json(force=True)
    reader_id = data.get("arc_reader_id")
    if not reader_id:
        return jsonify({"error": "arc_reader_id required"}), 400

    ok, error, status = invite_reader_to_writing_room(int(reader_id))
    if not ok:
        return jsonify({"error": error}), status
    return jsonify({"ok": True}), 200
