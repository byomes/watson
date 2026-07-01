"""jobs/dashboard/publishing_routes.py — dashboard-only backend for the Publishing
section (Writing Room / ARC / TWJ Readers tabs).

These routes are deliberately NOT behind X-Watson-Key. That header is a shared
secret for external server-to-server callers (wcky, FMSPC) — see jobs/dev_loop/deliver.py
for the precedent: its FMSPC-facing callback route is key-protected, but every
route meant to be called by the dashboard's own browser JS has no auth decorator
at all, relying on the dashboard's network boundary (Tailscale-only) instead.
The existing /api/writing-room/*, /api/arc/*, /api/publishing/twj/* routes stay
key-protected as-is for wcky; this module is a separate, additive layer.

Mount on the Watson dashboard app:
    from jobs.dashboard.publishing_routes import publishing_dashboard_bp
    app.register_blueprint(publishing_dashboard_bp)
"""
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.arc.api import _ensure_table as _ensure_arc_tables
from jobs.arc.api import invite_reader_to_writing_room
from jobs.publishing import (
    generate_reader_password, generate_reader_username, get_db as get_pub_db,
    set_reader_password,
)
from jobs.writing_room import get_db as get_wr_db
from jobs.writing_room.api import set_partner_password
from jobs.writing_room.onboard import process_approval, process_denial, resend_welcome

publishing_dashboard_bp = Blueprint("publishing_dashboard", __name__)


# ── Writing Room ─────────────────────────────────────────────────────────────

@publishing_dashboard_bp.route("/api/dashboard/writing-room/partners", methods=["GET"])
def dash_wr_partners():
    status_filter = request.args.get("status")
    conn = get_wr_db()
    _cols = ("id, name, email, username, status, joined_at, last_active, "
             "why_join, faith_description, agreed_to_participate, created_at")
    try:
        if status_filter:
            rows = conn.execute(
                f"SELECT {_cols} FROM writing_room_partners WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_cols} FROM writing_room_partners ORDER BY created_at DESC"
            ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/writing-room/approve", methods=["POST"])
def dash_wr_approve():
    partner_id = (request.get_json(force=True) or {}).get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    process_approval(int(partner_id))
    return jsonify({"ok": True}), 200


@publishing_dashboard_bp.route("/api/dashboard/writing-room/deny", methods=["POST"])
def dash_wr_deny():
    partner_id = (request.get_json(force=True) or {}).get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    process_denial(int(partner_id))
    return jsonify({"ok": True}), 200


@publishing_dashboard_bp.route("/api/dashboard/writing-room/revoke", methods=["POST"])
def dash_wr_revoke():
    partner_id = (request.get_json(force=True) or {}).get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    conn = get_wr_db()
    try:
        conn.execute(
            "UPDATE writing_room_partners SET status = 'revoked' WHERE id = ?", (partner_id,)
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/writing-room/resend-welcome", methods=["POST"])
def dash_wr_resend_welcome():
    partner_id = (request.get_json(force=True) or {}).get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    ok = resend_welcome(int(partner_id))
    if not ok:
        return jsonify({"error": "partner not found"}), 404
    return jsonify({"ok": True}), 200


@publishing_dashboard_bp.route("/api/dashboard/writing-room/reset-password", methods=["POST"])
def dash_wr_reset_password():
    partner_id = (request.get_json(force=True) or {}).get("partner_id")
    if not partner_id:
        return jsonify({"error": "partner_id required"}), 400
    conn = get_wr_db()
    try:
        row = conn.execute(
            "SELECT id FROM writing_room_partners WHERE id = ?", (partner_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "partner not found"}), 404

    new_password = generate_reader_password()
    set_partner_password(partner_id, new_password)
    return jsonify({"ok": True, "password": new_password}), 200


@publishing_dashboard_bp.route("/api/dashboard/writing-room/messages", methods=["GET"])
def dash_wr_messages():
    limit = int(request.args.get("limit", 20))
    conn = get_wr_db()
    try:
        rows = conn.execute(
            "SELECT * FROM writing_room_messages ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/writing-room/calls", methods=["GET"])
def dash_wr_calls():
    conn = get_wr_db()
    try:
        rows = conn.execute(
            "SELECT * FROM writing_room_calls ORDER BY scheduled_at ASC"
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


# ── ARC ────────────────────────────────────────────────────────────────────────

@publishing_dashboard_bp.route("/api/dashboard/arc/readers", methods=["GET"])
def dash_arc_readers():
    _ensure_arc_tables()
    conn = get_wr_db()
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
                "FROM arc_reader_commitments WHERE arc_reader_id = ? ORDER BY commitment_number",
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


@publishing_dashboard_bp.route("/api/dashboard/arc/commitments/<int:commitment_id>/approve", methods=["POST"])
def dash_arc_approve_commitment(commitment_id: int):
    _ensure_arc_tables()
    conn = get_wr_db()
    try:
        row = conn.execute(
            "SELECT id FROM arc_reader_commitments WHERE id = ?", (commitment_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE arc_reader_commitments SET approved_by_admin = 1, flagged_as_suspicious = 0 WHERE id = ?",
            (commitment_id,),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/arc/commitments/<int:commitment_id>/reject", methods=["POST"])
def dash_arc_reject_commitment(commitment_id: int):
    _ensure_arc_tables()
    conn = get_wr_db()
    try:
        row = conn.execute(
            "SELECT id FROM arc_reader_commitments WHERE id = ?", (commitment_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE arc_reader_commitments SET approved_by_admin = 0, flagged_as_suspicious = 0, "
            "is_checked = 0, submitted_at = NULL WHERE id = ?",
            (commitment_id,),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/arc/invite-to-writing-room", methods=["POST"])
def dash_arc_invite():
    reader_id = (request.get_json(force=True) or {}).get("arc_reader_id")
    if not reader_id:
        return jsonify({"error": "arc_reader_id required"}), 400
    ok, error, status = invite_reader_to_writing_room(int(reader_id))
    if not ok:
        return jsonify({"error": error}), status
    return jsonify({"ok": True}), 200


# ── TWJ Readers ────────────────────────────────────────────────────────────────

@publishing_dashboard_bp.route("/api/dashboard/twj/readers", methods=["GET"])
def dash_twj_readers():
    conn = get_pub_db()
    try:
        rows = conn.execute(
            "SELECT id, username, name, email, status, created_at, last_login "
            "FROM twj_readers ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/twj/readers", methods=["POST"])
def dash_twj_add_reader():
    import bcrypt
    data       = request.get_json(force=True)
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name") or "").strip()
    email      = (data.get("email") or "").strip()
    if not (first_name and last_name and email):
        return jsonify({"error": "first_name, last_name, and email are required"}), 400

    conn = get_pub_db()
    try:
        username = generate_reader_username(first_name, last_name, conn)
        password = generate_reader_password()
        pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO twj_readers (username, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (username, f"{first_name} {last_name}", email, pw_hash),
        )
        conn.commit()
        return jsonify({"ok": True, "username": username, "password": password}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/twj/readers/bulk", methods=["POST"])
def dash_twj_bulk_add():
    import bcrypt
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "no file"}), 400

    text = file.read().decode("utf-8", errors="replace")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0] and "@" in parts[1]:
            rows.append((parts[0], parts[1]))

    if not rows:
        return jsonify({"error": "no valid rows parsed"}), 400

    csv_lines = ["username,password,name,email"]
    count = 0
    conn = get_pub_db()
    try:
        for name, email in rows:
            name_parts = name.split()
            first_name = name_parts[0] if name_parts else "reader"
            last_name  = "".join(name_parts[1:]) or str(count + 1)

            username = generate_reader_username(first_name, last_name, conn)
            password = generate_reader_password()
            pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

            conn.execute(
                "INSERT INTO twj_readers (username, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (username, name, email, pw_hash),
            )
            csv_lines.append(f"{username},{password},{name},{email}")
            count += 1
        conn.commit()
        return jsonify({"ok": True, "count": count, "csv": "\n".join(csv_lines)}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/twj/readers/<int:reader_id>/reset-password", methods=["POST"])
def dash_twj_reset_password(reader_id: int):
    conn = get_pub_db()
    try:
        row = conn.execute("SELECT id FROM twj_readers WHERE id = ?", (reader_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "reader not found"}), 404
    new_password = generate_reader_password()
    set_reader_password(reader_id, new_password)
    return jsonify({"ok": True, "password": new_password}), 200


@publishing_dashboard_bp.route("/api/dashboard/twj/readers/<int:reader_id>/revoke", methods=["POST"])
def dash_twj_revoke(reader_id: int):
    conn = get_pub_db()
    try:
        row = conn.execute("SELECT id FROM twj_readers WHERE id = ?", (reader_id,)).fetchone()
        if not row:
            return jsonify({"error": "reader not found"}), 404
        conn.execute("UPDATE twj_readers SET status = 'revoked' WHERE id = ?", (reader_id,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/twj/feedback", methods=["GET"])
def dash_twj_feedback():
    chapter = request.args.get("chapter")
    conn = get_pub_db()
    try:
        query = (
            "SELECT f.id, f.chapter, f.feedback, f.created_at, r.username, r.name, r.email "
            "FROM twj_feedback f JOIN twj_readers r ON f.reader_id = r.id "
        )
        if chapter:
            rows = conn.execute(
                query + "WHERE f.chapter = ? ORDER BY f.created_at DESC", (chapter,)
            ).fetchall()
        else:
            rows = conn.execute(query + "ORDER BY f.created_at DESC").fetchall()
        return jsonify([dict(r) for r in rows]), 200
    finally:
        conn.close()


@publishing_dashboard_bp.route("/api/dashboard/twj/feedback/<int:feedback_id>", methods=["DELETE"])
def dash_twj_delete_feedback(feedback_id: int):
    conn = get_pub_db()
    try:
        row = conn.execute("SELECT id FROM twj_feedback WHERE id = ?", (feedback_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute("DELETE FROM twj_feedback WHERE id = ?", (feedback_id,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()
