"""jobs/team/api.py — Team Management API blueprint."""
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"
CONG_DB   = BASE_DIR / "data" / "congregation.db"

log = logging.getLogger(__name__)

team_bp = Blueprint("team", __name__, url_prefix="/api/team")


def _db():
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ── Members ───────────────────────────────────────────────────────────────────

@team_bp.route("/members", methods=["GET"])
def members_list():
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM team_members WHERE active=1 ORDER BY COALESCE(sort_order, id) ASC, id ASC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("members_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members", methods=["POST"])
def members_create():
    try:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_members (name, email, phone, role, ministry, notes, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, data.get("email"), data.get("phone"), data.get("role"),
             data.get("ministry"), data.get("notes"), data.get("source", "manual")),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_members WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("members_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>", methods=["PUT"])
def members_update(member_id):
    try:
        data = request.get_json(force=True) or {}
        allowed = {"name", "email", "phone", "role", "ministry", "notes", "source", "active"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "nothing to update"}), 400
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn = _db()
        conn.execute(
            f"UPDATE team_members SET {set_clause} WHERE id=?", (*fields.values(), member_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_members WHERE id=?", (member_id,)).fetchone()
        conn.close()
        return jsonify(dict(row) if row else {"error": "not found"})
    except Exception as exc:
        log.error("members_update error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/reorder", methods=["POST"])
def members_reorder():
    try:
        data = request.get_json(force=True) or {}
        order = data.get("order", [])
        if not isinstance(order, list) or not order:
            return jsonify({"error": "order must be a non-empty array"}), 400
        conn = _db()
        for i, member_id in enumerate(order):
            conn.execute("UPDATE team_members SET sort_order=? WHERE id=?", (i, member_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as exc:
        log.error("members_reorder error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/search", methods=["GET"])
def members_search():
    try:
        query = (request.args.get("name") or "").strip()
        if not query:
            return jsonify([])
        conn = _db()
        rows = conn.execute(
            "SELECT id, name FROM team_members WHERE active=1 AND name LIKE ? ORDER BY name ASC",
            (f"%{query}%",),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("members_search error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>", methods=["DELETE"])
def members_delete(member_id):
    try:
        conn = _db()
        conn.execute("UPDATE team_members SET active=0 WHERE id=?", (member_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("members_delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>/profile", methods=["GET"])
def member_profile(member_id):
    try:
        conn = _db()
        member = conn.execute("SELECT * FROM team_members WHERE id=?", (member_id,)).fetchone()
        if not member:
            conn.close()
            return jsonify({"error": "not found"}), 404
        objectives = conn.execute(
            "SELECT * FROM team_objectives WHERE member_id=? ORDER BY created_at DESC",
            (member_id,)
        ).fetchall()
        goals = conn.execute(
            "SELECT * FROM team_goals WHERE member_id=? ORDER BY created_at DESC",
            (member_id,)
        ).fetchall()
        tasks = conn.execute(
            "SELECT * FROM team_tasks WHERE member_id=? AND status='open' ORDER BY due_date ASC",
            (member_id,)
        ).fetchall()
        meetings = conn.execute(
            "SELECT id, date, SUBSTR(summary,1,200) AS summary_excerpt, email_sent "
            "FROM team_meetings WHERE member_id=? ORDER BY date DESC LIMIT 3",
            (member_id,)
        ).fetchall()
        conn.close()
        return jsonify({
            "member": dict(member),
            "objectives": [dict(r) for r in objectives],
            "goals": [dict(r) for r in goals],
            "tasks": [dict(r) for r in tasks],
            "recent_meetings": [dict(r) for r in meetings],
        })
    except Exception as exc:
        log.error("member_profile error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/search", methods=["GET"])
def team_search():
    try:
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify([])
        results = []
        seen_emails = set()

        # Search congregation.db
        if CONG_DB.exists():
            try:
                conn2 = sqlite3.connect(CONG_DB)
                conn2.row_factory = sqlite3.Row
                rows = conn2.execute(
                    "SELECT id, name, email, phone FROM members "
                    "WHERE name LIKE ? COLLATE NOCASE LIMIT 10",
                    (f"%{q}%",),
                ).fetchall()
                for r in rows:
                    results.append({
                        "source": "congregation",
                        "id": r["id"],
                        "name": r["name"],
                        "email": r["email"] or "",
                        "phone": r["phone"] or "",
                    })
                    if r["email"]:
                        seen_emails.add(r["email"])
                conn2.close()
            except Exception as exc:
                log.warning("congregation search failed: %s", exc)

        # Search watson.db people table
        try:
            conn = _db()
            rows = conn.execute(
                "SELECT id, name, email, phone FROM people "
                "WHERE name LIKE ? COLLATE NOCASE LIMIT 10",
                (f"%{q}%",),
            ).fetchall()
            for r in rows:
                if r["email"] not in seen_emails:
                    results.append({
                        "source": "people",
                        "id": r["id"],
                        "name": r["name"],
                        "email": r["email"] or "",
                        "phone": r["phone"] or "",
                    })
            conn.close()
        except Exception as exc:
            log.warning("people search failed: %s", exc)

        return jsonify(results[:15])
    except Exception as exc:
        log.error("team_search error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Objectives ────────────────────────────────────────────────────────────────

@team_bp.route("/members/<int:member_id>/objectives", methods=["GET"])
def objectives_list(member_id):
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM team_objectives WHERE member_id=? ORDER BY created_at DESC",
            (member_id,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("objectives_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>/objectives", methods=["POST"])
def objectives_create(member_id):
    try:
        data = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_objectives (member_id, title, description) VALUES (?,?,?)",
            (member_id, title, data.get("description")),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_objectives WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("objectives_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/objectives/<int:obj_id>", methods=["DELETE"])
def objectives_delete(obj_id):
    try:
        conn = _db()
        conn.execute("DELETE FROM team_objectives WHERE id=?", (obj_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("objectives_delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Goals ─────────────────────────────────────────────────────────────────────

@team_bp.route("/members/<int:member_id>/goals", methods=["GET"])
def goals_list(member_id):
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM team_goals WHERE member_id=? ORDER BY created_at DESC",
            (member_id,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("goals_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>/goals", methods=["POST"])
def goals_create(member_id):
    try:
        data = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_goals (member_id, title, target_date, objective_id) VALUES (?,?,?,?)",
            (member_id, title, data.get("target_date"), data.get("objective_id")),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_goals WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("goals_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/goals/<int:goal_id>", methods=["PUT"])
def goals_update(goal_id):
    try:
        data = request.get_json(force=True) or {}
        allowed = {"title", "target_date", "status"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "nothing to update"}), 400
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn = _db()
        conn.execute(f"UPDATE team_goals SET {set_clause} WHERE id=?", (*fields.values(), goal_id))
        conn.commit()
        row = conn.execute("SELECT * FROM team_goals WHERE id=?", (goal_id,)).fetchone()
        conn.close()
        return jsonify(dict(row) if row else {"error": "not found"})
    except Exception as exc:
        log.error("goals_update error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/goals/<int:goal_id>", methods=["DELETE"])
def goals_delete(goal_id):
    try:
        conn = _db()
        conn.execute("DELETE FROM team_goals WHERE id=?", (goal_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("goals_delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Tasks ─────────────────────────────────────────────────────────────────────

@team_bp.route("/members/<int:member_id>/tasks", methods=["GET"])
def member_tasks_list(member_id):
    try:
        status   = request.args.get("status", "open")
        category = request.args.get("category")
        conn = _db()
        if status == "all" and not category:
            rows = conn.execute(
                "SELECT * FROM team_tasks WHERE member_id=? ORDER BY due_date ASC",
                (member_id,),
            ).fetchall()
        elif status == "all":
            rows = conn.execute(
                "SELECT * FROM team_tasks WHERE member_id=? AND category=? ORDER BY due_date ASC",
                (member_id, category),
            ).fetchall()
        elif not category:
            rows = conn.execute(
                "SELECT * FROM team_tasks WHERE member_id=? AND status=? ORDER BY due_date ASC",
                (member_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM team_tasks WHERE member_id=? AND status=? AND category=? ORDER BY due_date ASC",
                (member_id, status, category),
            ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("member_tasks_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/tasks", methods=["GET"])
def tasks_all():
    try:
        status = request.args.get("status", "open")
        conn = _db()
        if status == "all":
            rows = conn.execute(
                "SELECT t.*, m.name AS member_name, m.ministry "
                "FROM team_tasks t JOIN team_members m ON m.id=t.member_id "
                "ORDER BY t.due_date ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT t.*, m.name AS member_name, m.ministry "
                "FROM team_tasks t JOIN team_members m ON m.id=t.member_id "
                "WHERE t.status=? ORDER BY t.due_date ASC",
                (status,)
            ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("tasks_all error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>/tasks", methods=["POST"])
def tasks_create(member_id):
    try:
        data = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        priority = data.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_tasks (member_id, title, due_date, goal_id, source, priority) VALUES (?,?,?,?,?,?)",
            (member_id, title, data.get("due_date"), data.get("goal_id"), data.get("source", "manual"), priority),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("tasks_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/tasks", methods=["POST"])
def tasks_create_flat():
    try:
        data = request.get_json(force=True) or {}
        member_id = data.get("member_id")
        title = (data.get("title") or "").strip()
        if not member_id or not title:
            return jsonify({"error": "member_id and title required"}), 400
        priority = data.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        category = data.get("category", "catalyst")
        if category not in ("catalyst", "fms", "personal"):
            category = "catalyst"
        source = data.get("assigned_by", "manual")
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_tasks (member_id, title, due_date, source, priority, category) VALUES (?,?,?,?,?,?)",
            (member_id, title, data.get("due_date"), source, priority, category),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify({"success": True, "task_id": cur.lastrowid, "task": dict(row)}), 201
    except Exception as exc:
        log.error("tasks_create_flat error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/tasks/<int:task_id>", methods=["PUT"])
def tasks_update(task_id):
    try:
        data = request.get_json(force=True) or {}
        allowed = {"title", "due_date", "status", "goal_id"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "nothing to update"}), 400
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn = _db()
        conn.execute(f"UPDATE team_tasks SET {set_clause} WHERE id=?", (*fields.values(), task_id))
        conn.commit()
        row = conn.execute("SELECT * FROM team_tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        return jsonify(dict(row) if row else {"error": "not found"})
    except Exception as exc:
        log.error("tasks_update error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/tasks/<int:task_id>", methods=["PATCH"])
def tasks_patch(task_id):
    try:
        data = request.get_json(force=True) or {}
        allowed = {"category", "priority", "status"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "nothing to update"}), 400
        conn = _db()
        row = conn.execute("SELECT member_id FROM team_tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row["member_id"] != 12:
            conn.close()
            return jsonify({"error": "not found"}), 404
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE team_tasks SET {set_clause} WHERE id=?", (*fields.values(), task_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as exc:
        log.error("tasks_patch error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
def tasks_delete(task_id):
    try:
        conn = _db()
        conn.execute("DELETE FROM team_tasks WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("tasks_delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Meetings ──────────────────────────────────────────────────────────────────

@team_bp.route("/members/<int:member_id>/meetings", methods=["POST"])
def meetings_create(member_id):
    try:
        data = request.get_json(force=True) or {}
        date = (data.get("date") or "").strip()
        if not date:
            return jsonify({"error": "date required"}), 400
        conn = _db()
        cur = conn.execute(
            "INSERT INTO team_meetings (member_id, date, transcript, summary, email_draft) "
            "VALUES (?,?,?,?,?)",
            (member_id, date, data.get("transcript"), data.get("summary"), data.get("email_draft")),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM team_meetings WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as exc:
        log.error("meetings_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/members/<int:member_id>/meetings", methods=["GET"])
def meetings_list(member_id):
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT id, date, SUBSTR(summary,1,200) AS summary_excerpt, email_sent "
            "FROM team_meetings WHERE member_id=? ORDER BY date DESC",
            (member_id,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("meetings_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/meetings/<int:meeting_id>", methods=["GET"])
def meeting_detail(meeting_id):
    try:
        conn = _db()
        row = conn.execute("SELECT * FROM team_meetings WHERE id=?", (meeting_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))
    except Exception as exc:
        log.error("meeting_detail error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/meetings/<int:meeting_id>/send-email", methods=["PUT"])
def meeting_send_email(meeting_id):
    try:
        conn = _db()
        meeting = conn.execute("SELECT * FROM team_meetings WHERE id=?", (meeting_id,)).fetchone()
        if not meeting:
            conn.close()
            return jsonify({"error": "not found"}), 404
        member = conn.execute(
            "SELECT * FROM team_members WHERE id=?", (meeting["member_id"],)
        ).fetchone()

        conn.execute("UPDATE team_meetings SET email_sent=1 WHERE id=?", (meeting_id,))
        sent_at = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO team_messages (member_id, direction, subject, body, sent_at) "
            "VALUES (?,?,?,?,?)",
            (meeting["member_id"], "out",
             f"Follow-up from our meeting on {meeting['date']}",
             meeting["email_draft"] or "", sent_at),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "member": dict(member) if member else None})
    except Exception as exc:
        log.error("meeting_send_email error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Transcript processing ─────────────────────────────────────────────────────

@team_bp.route("/process-transcript", methods=["POST"])
def process_transcript():
    try:
        from jobs.team.extractor import process_transcript as _extract
        data = request.get_json(force=True) or {}
        member_id = data.get("member_id")
        transcript = (data.get("transcript") or "").strip()
        date = (data.get("date") or datetime.now().strftime("%Y-%m-%d"))

        if not member_id or not transcript:
            return jsonify({"error": "member_id and transcript required"}), 400

        conn = _db()
        member = conn.execute("SELECT * FROM team_members WHERE id=?", (member_id,)).fetchone()
        conn.close()
        if not member:
            return jsonify({"error": "member not found"}), 404

        result = _extract(member["name"], transcript, date)
        return jsonify(result)
    except Exception as exc:
        log.error("process_transcript error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Messages ──────────────────────────────────────────────────────────────────

@team_bp.route("/messages", methods=["GET"])
def messages_list():
    try:
        direction = request.args.get("direction")  # optional filter: 'in' or 'out'
        conn = _db()
        if direction in ("in", "out"):
            rows = conn.execute(
                "SELECT msg.*, m.name AS member_name, m.ministry "
                "FROM team_messages msg JOIN team_members m ON m.id=msg.member_id "
                "WHERE msg.direction=? ORDER BY COALESCE(msg.sent_at, msg.created_at) DESC",
                (direction,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT msg.*, m.name AS member_name, m.ministry "
                "FROM team_messages msg JOIN team_members m ON m.id=msg.member_id "
                "ORDER BY COALESCE(msg.sent_at, msg.created_at) DESC"
            ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("messages_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/messages/<int:msg_id>", methods=["DELETE"])
def message_delete(msg_id):
    try:
        conn = _db()
        conn.execute("DELETE FROM team_messages WHERE id=?", (msg_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@team_bp.route("/messages/send", methods=["POST"])
def messages_send():
    try:
        from jobs.team.email_job import send_team_email
        data = request.get_json(force=True) or {}
        member_id = data.get("member_id")
        subject   = (data.get("subject") or "").strip()
        body      = (data.get("body") or "").strip()
        meeting_id = data.get("meeting_id")

        if not member_id or not subject or not body:
            return jsonify({"error": "member_id, subject, and body required"}), 400

        result = send_team_email(member_id, subject, body, meeting_id=meeting_id)
        return jsonify(result)
    except Exception as exc:
        log.error("messages_send error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Shared Notes ──────────────────────────────────────────────────────────────

@team_bp.route("/members/<int:member_id>/shared_notes", methods=["GET"])
def shared_notes_list(member_id):
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT id, content, author, created_at FROM shared_notes "
            "WHERE member_id=? ORDER BY created_at DESC",
            (member_id,),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        log.error("shared_notes_list error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/shared_notes", methods=["POST"])
def shared_notes_create():
    try:
        data = request.get_json(force=True) or {}
        member_id = data.get("member_id")
        content = (data.get("content") or "").strip()
        if not member_id or not content:
            return jsonify({"error": "member_id and content required"}), 400
        today = datetime.now().date().isoformat()
        conn = _db()
        cur = conn.execute(
            "INSERT INTO shared_notes (member_id, content, author) VALUES (?, ?, 'bill')",
            (member_id, content),
        )
        conn.execute(
            "UPDATE team_members SET last_activity_date=? WHERE id=?",
            (today, member_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, content, author, created_at FROM shared_notes WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        conn.close()
        return jsonify({"success": True, "note": dict(row)})
    except Exception as exc:
        log.error("shared_notes_create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@team_bp.route("/shared_notes/<int:note_id>", methods=["DELETE"])
def shared_notes_delete(note_id):
    try:
        conn = _db()
        note = conn.execute(
            "SELECT author FROM shared_notes WHERE id=?", (note_id,)
        ).fetchone()
        if not note:
            conn.close()
            return jsonify({"error": "not found"}), 404
        if note["author"] != "bill":
            conn.close()
            return jsonify({"error": "can only delete own notes"}), 403
        conn.execute("DELETE FROM shared_notes WHERE id=?", (note_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as exc:
        log.error("shared_notes_delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500
