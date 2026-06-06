"""Watson dashboard — port 5200, Tailscale-only."""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, g, jsonify, render_template, request
from jobs.people.api import people_create, people_delete, people_list, people_update

DB = os.path.expanduser("~/watson/data/watson.db")
SKILLS_FILE = Path(__file__).resolve().parents[2] / "memory" / "skills.json"
MEMORY = Path(__file__).resolve().parents[2] / "memory"
app = Flask(__name__, static_folder='static', template_folder='templates')


def _db():
    if "db" not in g:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        g.db = c
    return g.db


@app.teardown_appcontext
def _close(e=None):
    c = g.pop("db", None)
    if c:
        c.close()


def _bootstrap():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL,
        due_date   TEXT,
        priority   TEXT    NOT NULL DEFAULT 'medium',
        status     TEXT    NOT NULL DEFAULT 'active',
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    try:
        c.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        title        TEXT    NOT NULL,
        due_datetime TEXT    NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'active',
        sort_order   INTEGER DEFAULT 0,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reading_list (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        url         TEXT,
        source_name TEXT,
        summary     TEXT,
        status      TEXT    NOT NULL DEFAULT 'unread',
        date_added  TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL DEFAULT 'New Chat',
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        role       TEXT    NOT NULL,
        content    TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
    )""")
    try:
        c.execute("ALTER TABLE chat_sessions ADD COLUMN project_slug TEXT DEFAULT NULL")
    except Exception:
        pass
    c.commit()
    c.close()


_bootstrap()

# Pending skill proposal keyed by a single user (single-user system)
_pending_skill_request: str | None = None

# ── Shell ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template('index.html')


# ── Briefing API ──────────────────────────────────────────────────────────────

@app.route("/api/briefing")
def briefing_list():
    rows = _db().execute(
        "SELECT id, title, url, summary, source_name FROM briefing_items "
        "WHERE dismissed = 0 ORDER BY score DESC, fetched_at DESC LIMIT 30"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/briefing/meta")
def briefing_meta():
    row = _db().execute(
        "SELECT fetched_at FROM briefing_items ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    return jsonify({"generated_at": row["fetched_at"] if row else None})


@app.route("/api/briefing/<int:item_id>/approve", methods=["POST"])
def briefing_approve(item_id):
    _db().execute("UPDATE briefing_items SET dismissed = 1 WHERE id = ?", (item_id,))
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/briefing/<int:item_id>/reject", methods=["POST"])
def briefing_reject(item_id):
    _db().execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'manual' WHERE id = ?",
        (item_id,),
    )
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/briefing/<int:item_id>/facebook", methods=["POST"])
def briefing_facebook(item_id):
    _db().execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'facebook' WHERE id = ?",
        (item_id,),
    )
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/briefing/<int:item_id>/email", methods=["POST"])
def briefing_email(item_id):
    _db().execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'email' WHERE id = ?",
        (item_id,),
    )
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/briefing/<int:item_id>/tolist", methods=["POST"])
def briefing_tolist(item_id):
    db = _db()
    db.execute(
        "INSERT INTO reading_list (title, url, source_name, summary, status, date_added) "
        "SELECT title, url, source_name, summary, 'unread', datetime('now') "
        "FROM briefing_items WHERE id = ?",
        (item_id,),
    )
    db.execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'tolist' WHERE id = ?",
        (item_id,),
    )
    db.commit()
    return jsonify({"ok": True})


# ── Tasks API ─────────────────────────────────────────────────────────────────

@app.route("/api/tasks")
def tasks_list():
    rows = _db().execute(
        "SELECT * FROM tasks ORDER BY sort_order ASC, created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks", methods=["POST"])
def tasks_create():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    cur = _db().execute(
        "INSERT INTO tasks (title, due_date, priority, status) VALUES (?, ?, ?, ?)",
        (title, data.get("due_date"), data.get("priority", "medium"), "active"),
    )
    _db().commit()
    row = _db().execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def tasks_update(task_id):
    data = request.get_json(force=True)
    allowed = {"title", "due_date", "priority", "status"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    _db().execute(
        f"UPDATE tasks SET {set_clause} WHERE id = ?", (*fields.values(), task_id)
    )
    _db().commit()
    row = _db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(row) if row else {"error": "not found"})


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def tasks_delete(task_id):
    _db().execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>/reorder", methods=["PATCH"])
def tasks_reorder(task_id):
    data = request.get_json(force=True)
    sort_order = data.get("sort_order")
    if sort_order is None:
        return jsonify({"error": "sort_order required"}), 400
    _db().execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (sort_order, task_id))
    _db().commit()
    row = _db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(row) if row else {"error": "not found"})


# ── Contacts API ──────────────────────────────────────────────────────────────

@app.route("/api/contacts")
def contacts_list():
    return jsonify(people_list())


@app.route("/api/contacts", methods=["POST"])
def contacts_create():
    result = people_create(request.get_json(force=True))
    return jsonify(result), (400 if "error" in result else 201)


@app.route("/api/contacts/<int:contact_id>", methods=["PATCH"])
def contacts_update(contact_id):
    return jsonify(people_update(contact_id, request.get_json(force=True)))


@app.route("/api/contacts/<int:contact_id>", methods=["DELETE"])
def contacts_delete(contact_id):
    return jsonify(people_delete(contact_id))


# ── Reading API ───────────────────────────────────────────────────────────────

@app.route("/api/reading")
def reading_list():
    rows = _db().execute(
        "SELECT id, title, url, source_name, summary, date_added, status "
        "FROM reading_list ORDER BY date_added DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reading/<int:entry_id>", methods=["PATCH"])
def reading_update(entry_id):
    status = (request.get_json(force=True) or {}).get("status")
    if status not in ("unread", "reading", "finished"):
        return jsonify({"error": "invalid status"}), 400
    _db().execute(
        "UPDATE reading_list SET status = ? WHERE id = ?", (status, entry_id)
    )
    _db().commit()
    row = _db().execute(
        "SELECT * FROM reading_list WHERE id = ?", (entry_id,)
    ).fetchone()
    return jsonify(dict(row) if row else {"error": "not found"})


# ── Reminders API ────────────────────────────────────────────────────────────

@app.route("/api/reminders")
def reminders_list():
    rows = _db().execute(
        "SELECT * FROM reminders ORDER BY sort_order ASC, due_datetime ASC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reminders", methods=["POST"])
def reminders_create():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    due_datetime = (data.get("due_datetime") or "").strip()
    if not title or not due_datetime:
        return jsonify({"error": "title and due_datetime required"}), 400
    cur = _db().execute(
        "INSERT INTO reminders (title, due_datetime, status, sort_order) VALUES (?, ?, ?, ?)",
        (title, due_datetime, "active", data.get("sort_order", 0)),
    )
    _db().commit()
    row = _db().execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["PATCH"])
def reminders_update(reminder_id):
    data = request.get_json(force=True)
    allowed = {"title", "due_datetime", "status", "sort_order"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    _db().execute(
        f"UPDATE reminders SET {set_clause} WHERE id = ?", (*fields.values(), reminder_id)
    )
    _db().commit()
    row = _db().execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    return jsonify(dict(row) if row else {"error": "not found"})


@app.route("/api/reminders/<int:reminder_id>", methods=["DELETE"])
def reminders_delete(reminder_id):
    _db().execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    _db().commit()
    return jsonify({"ok": True})


# ── Chat Sessions API ─────────────────────────────────────────────────────────

@app.route("/api/chat/sessions")
def chat_sessions_list():
    project_slug = request.args.get("project_slug")
    if project_slug:
        rows = _db().execute(
            "SELECT id, title, created_at, updated_at, project_slug FROM chat_sessions "
            "WHERE project_slug = ? ORDER BY updated_at DESC",
            (project_slug,),
        ).fetchall()
    else:
        rows = _db().execute(
            "SELECT id, title, created_at, updated_at, project_slug FROM chat_sessions "
            "ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/chat/sessions", methods=["POST"])
def chat_sessions_create():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "New Chat").strip()
    cur = _db().execute(
        "INSERT INTO chat_sessions (title) VALUES (?)", (title,)
    )
    _db().commit()
    row = _db().execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/chat/sessions/<int:session_id>", methods=["PATCH"])
def chat_sessions_update(session_id):
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    _db().execute(
        "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
        (title, session_id)
    )
    _db().commit()
    row = _db().execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return jsonify(dict(row) if row else {"error": "not found"})


@app.route("/api/chat/sessions/<int:session_id>", methods=["DELETE"])
def chat_sessions_delete(session_id):
    _db().execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    _db().execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/chat/sessions/<int:session_id>/messages")
def chat_messages_list(session_id):
    rows = _db().execute(
        "SELECT id, session_id, role, content, created_at FROM chat_messages "
        "WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/chat/sessions/<int:session_id>/messages", methods=["POST"])
def chat_messages_create(session_id):
    data = request.get_json(force=True) or {}
    role = data.get("role", "").strip()
    content = data.get("content", "").strip()
    if role not in ("user", "assistant") or not content:
        return jsonify({"error": "role and content required"}), 400
    cur = _db().execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content)
    )
    _db().execute(
        "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
        (session_id,)
    )
    _db().commit()
    row = _db().execute(
        "SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return jsonify(dict(row)), 201


# ── Upload API ────────────────────────────────────────────────────────────────

_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".py", ".html", ".xml"}
_TRUNCATE_AT = 8000


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    f = request.files["file"]
    filename = f.filename or "unknown"
    ext = Path(filename).suffix.lower()
    try:
        if ext in _TEXT_EXTS:
            content = f.read().decode("utf-8")
        elif ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                return jsonify({"success": False, "error": "pypdf not installed. Run: pip install pypdf"})
            import io
            reader = pypdf.PdfReader(io.BytesIO(f.read()))
            content = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            try:
                content = f.read().decode("utf-8")
            except UnicodeDecodeError:
                return jsonify({"success": False, "error": "File type not supported for text extraction. Try a text-based file."})
        if len(content) > _TRUNCATE_AT:
            content = content[:_TRUNCATE_AT] + "\n[File truncated at 8000 characters]"
        return jsonify({"success": True, "content": content, "filename": filename})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)})


# ── Skills API ────────────────────────────────────────────────────────────────

@app.route("/api/skills")
def skills_list_api():
    if not SKILLS_FILE.exists():
        return jsonify([])
    try:
        skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        return jsonify(skills if isinstance(skills, list) else [])
    except Exception:
        return jsonify([])


# ── Chat API ─────────────────────────────────────────────────────────────────

WATSON_SYSTEM = """You are Watson, Dr. Bill Yomes's personal AI-powered digital assistant. You operate under his supervision and act on his behalf.

WHO YOU ARE:
- Terse, efficient, and direct. No filler. No unnecessary preamble.
- You never guess or fabricate. If you don't know, you say so and stop.
- You are not a pastor, counselor, or spiritual authority. You do not speak theologically or pastorally without explicit permission from Dr. Bill.
- You are not an image bearer. You have no soul, no Holy Spirit access, no spiritual discernment.

WHO DR. BILL IS:
- Senior Pastor of Catalyst Community Church in Wilmington, DE
- Founding Apologist of Faith Makes Sense
- Author of The Wrong Jesus (in progress)
- Doctor of Ministry in Theology and Apologetics, Liberty University
- His home and office are in the Newark/Wilmington, DE area (zip: 19702)

WHAT YOU HELP WITH:
- Research, content creation, scheduling, publishing workflows
- Sermon pipeline, blog drafts, social media, weekly email
- Reading list, connect cards, people registry
- Dev specs for new Watson jobs
- General questions, lookups, and task management

RULES:
- Never extrapolate Dr. Bill's theological or ministry positions
- Never pastor, counsel, or pray on his behalf
- Always identify yourself as Watson when asked
- Keep responses concise unless depth is explicitly requested
- When asked to send an email, always create a draft for Dr. Bill to review and send. Never send emails autonomously.
- CRITICAL: Never fabricate, invent, or hallucinate information. You have no access to emails, messages, files, calendars, or external data unless explicitly provided in this conversation. Never invent tasks, messages, cases, meetings, or any context not given to you.

CODE AGENT:
When Dr. Bill asks you to build something, draft a spec in this format:
SPEC: [one sentence summary]
FILES TO CREATE OR MODIFY:
- [filepath]: [what changes]
DB CHANGES: [table: columns] or NONE
CRON: [schedule: command] or NONE
STEPS: numbered list
RISKS: [anything that could break existing functionality]
ESTIMATED LINES: [number]
Then ask: Reply CONFIRM to build this.
When Dr. Bill says CONFIRM, tell him to run:
cd ~/watson && PYTHONPATH=/home/billyomes/watson venv/bin/python jobs/code_agent/confirm.py --manual "[spec text]"

SYSTEM CONTEXT:
- Watson repo: ~/watson (Beelink, user: billyomes)
- Dashboard: ~/watson/jobs/dashboard/app.py (Flask, port 5200)
- DB: ~/watson/data/watson.db (SQLite)
- Jobs: ~/watson/jobs/<jobname>/
- All cron jobs need PYTHONPATH=/home/billyomes/watson
- Never touch .env, credentials, or auth files
- Never auto-push — Bill pulls manually"""


_AFFIRM = {"yes", "yes please", "go ahead", "build it", "sure", "do it", "yep", "yeah"}
_DENY = {"no", "never mind", "nope", "cancel", "don't", "no thanks"}


@app.route("/api/chat", methods=["POST"])
def chat():
    import requests as _req
    from jobs.skillbuilder import router as _router
    global _pending_skill_request

    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"error": "message required"}), 400

    msg_lower = message.lower().strip()

    # Handle yes/no follow-up on a pending skill proposal
    if _pending_skill_request is not None:
        if msg_lower in _AFFIRM:
            pending = _pending_skill_request
            _pending_skill_request = None
            slug = pending.lower()[:40]
            for ch in " !?:,;'\"/\\.":
                slug = slug.replace(ch, "_")
            slug = "_".join(p for p in slug.split("_") if p)[:30]
            job_path = f"jobs/custom/{slug}.py"
            from jobs.skillbuilder.build import build_skill as _build_skill
            import threading
            threading.Thread(target=_build_skill, args=(pending, job_path), daemon=True).start()
            return jsonify({"response": "Building that skill now. I’ll notify you via Telegram when it’s ready."})
        if msg_lower in _DENY or msg_lower.startswith("no "):
            _pending_skill_request = None
            return jsonify({"response": "Got it. Let me know if you need anything else."})

    # Skill routing
    try:
        route_result = _router.route(message, "dashboard")
    except Exception as exc:
        route_result = {"action": "chat"}

    if route_result["action"] == "skill":
        return jsonify({"response": "✓ " + route_result["result"]})

    if route_result["action"] == "propose":
        _pending_skill_request = message
        return jsonify({"response": route_result["message"]})

    # Fall through to Ollama
    core_md_path = Path(os.path.expanduser("~/watson/memory/core.md"))
    try:
        core_md = core_md_path.read_text(encoding="utf-8")
        system_prompt = f"{core_md}\n\n{WATSON_SYSTEM}"
    except FileNotFoundError:
        system_prompt = WATSON_SYSTEM
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-20:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})
    try:
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json={"model": "llama3.2:3b", "messages": messages, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json().get("message", {}).get("content", "")
        return jsonify({"response": reply})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Calendar API ──────────────────────────────────────────────────────────────


@app.route("/api/calendar/busy-rest-of-day", methods=["POST"])
def calendar_busy_rest_of_day():
    import requests as _req
    from jobs.gcal.calendar import mark_day_busy_from_now
    from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
    try:
        count = mark_day_busy_from_now()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    try:
        _req.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": WATSON_CHAT_ID,
                "text": f"\U0001f6ab Marked rest of day as busy. {count} appointment(s) affected.",
            },
            timeout=10,
        )
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/calendar/today")
def calendar_today():
    from jobs.gcal.calendar import get_todays_events
    try:
        events = get_todays_events()
        return jsonify(events)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Projects ──────────────────────────────────────────────────────────────────

import re as _re
from datetime import date as _date
from werkzeug.utils import secure_filename as _secure


def _parse_projects_index():
    index_path = MEMORY / "projects" / "_index.md"
    if not index_path.exists():
        return []
    rows = []
    lines = index_path.read_text(encoding="utf-8").splitlines()
    header = None
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower().replace(" ", "_") for c in cells]
            continue
        if all(_re.fullmatch(r"[-:]+", c) for c in cells):
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


@app.route("/api/projects")
def projects_list():
    return jsonify(_parse_projects_index())


@app.route("/api/projects/<slug>")
def projects_get(slug):
    md_path = MEMORY / "projects" / slug / f"{slug}.md"
    if not md_path.exists():
        return jsonify({"error": "not found"}), 404
    rows = _parse_projects_index()
    meta = next((r for r in rows if r.get("slug") == slug), {})
    return jsonify({"slug": slug, "meta": meta, "content": md_path.read_text(encoding="utf-8")})


@app.route("/api/projects/<slug>/files")
def projects_files_list(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"notes": [], "files": []})
    notes, files = [], []
    notes_dir = project_dir / "notes"
    if notes_dir.exists():
        for f in sorted(notes_dir.iterdir()):
            if f.is_file():
                st = f.stat()
                notes.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
    files_dir = project_dir / "files"
    if files_dir.exists():
        for f in sorted(files_dir.iterdir()):
            if f.is_file():
                st = f.stat()
                files.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
    return jsonify({"notes": notes, "files": files})


@app.route("/api/projects/<slug>/files/<filename>")
def projects_files_get(slug, filename):
    from flask import send_from_directory
    section = request.args.get("section", "files")
    subdir = "notes" if section == "notes" else "files"
    file_dir = MEMORY / "projects" / slug / subdir
    if not (file_dir / filename).exists():
        return jsonify({"error": "not found"}), 404
    return send_from_directory(str(file_dir), filename)


@app.route("/api/projects/<slug>/notes", methods=["POST"])
def projects_notes_add(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    note_text = (data.get("note") or "").strip()
    if not note_text:
        return jsonify({"error": "note required"}), 400
    notes_dir = project_dir / "notes"
    notes_dir.mkdir(exist_ok=True)
    today = _date.today().isoformat()
    note_file = notes_dir / f"{today}.md"
    sep = "\n\n---\n\n" if note_file.exists() else ""
    with note_file.open("a", encoding="utf-8") as f:
        f.write(f"{sep}{note_text}\n")
    return jsonify({"ok": True, "file": note_file.name})


@app.route("/api/projects/<slug>/files", methods=["POST"])
def projects_files_upload(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400
    files_dir = project_dir / "files"
    files_dir.mkdir(exist_ok=True)
    filename = _secure(f.filename or "upload")
    dest = files_dir / filename
    f.save(str(dest))
    return jsonify({"ok": True, "name": filename, "size": dest.stat().st_size})


@app.route("/api/projects/<slug>/chat", methods=["POST"])
def projects_chat_session(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    rows = _parse_projects_index()
    meta = next((r for r in rows if r.get("slug") == slug), {})
    title = f"{meta.get('name', slug)} — Chat"
    db = _db()
    cur = db.execute(
        "INSERT INTO chat_sessions (title, project_slug) VALUES (?, ?)",
        (title, slug),
    )
    db.commit()
    session = dict(db.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (cur.lastrowid,)
    ).fetchone())
    return jsonify(session), 201


@app.route("/api/projects", methods=["POST"])
def projects_create():
    data = request.get_json(force=True, silent=True) or {}
    slug = (data.get("slug") or "").strip().lower().replace(" ", "_")
    name = (data.get("name") or "").strip()
    if not slug or not name:
        return jsonify({"error": "slug and name required"}), 400
    project_dir = MEMORY / "projects" / slug
    if project_dir.exists():
        return jsonify({"error": "project already exists"}), 409
    try:
        from jobs.memory.new_project import create_project
        create_project(slug, name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "slug": slug, "name": name}), 201


@app.route("/api/projects/<slug>", methods=["DELETE"])
def projects_delete(slug):
    import shutil
    import subprocess as _sp
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "not found"}), 404
    try:
        index_path = MEMORY / "projects" / "_index.md"
        if index_path.exists():
            lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = [
                l for l in lines
                if not _re.match(r"\|\s*" + _re.escape(slug) + r"\s*\|", l.strip())
            ]
            index_path.write_text("".join(new_lines), encoding="utf-8")
        shutil.rmtree(str(project_dir))
        _sp.run(["git", "add", str(MEMORY / "projects")], cwd=str(MEMORY.parent), check=True)
        _sp.run(["git", "commit", "-m", f"project: deleted {slug}"], cwd=str(MEMORY.parent), check=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"success": True})


@app.route("/api/projects/<slug>/status", methods=["PATCH"])
def projects_status_update(slug):
    import subprocess as _sp
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in ("Active", "Planned", "Archived"):
        return jsonify({"error": "invalid status"}), 400
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "not found"}), 404
    try:
        md_path = project_dir / f"{slug}.md"
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")
            content = _re.sub(r"\*\*Status:\*\*\s*.+", f"**Status:** {status}", content)
            md_path.write_text(content, encoding="utf-8")
        index_path = MEMORY / "projects" / "_index.md"
        if index_path.exists():
            lines = index_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("|") and _re.match(r"\|\s*" + _re.escape(slug) + r"\s*\|", stripped):
                    parts = [p.strip() for p in stripped.strip("|").split("|")]
                    if len(parts) >= 3:
                        parts[2] = status
                        line = "| " + " | ".join(parts) + " |"
                new_lines.append(line)
            index_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        _sp.run(["git", "add", str(project_dir), str(MEMORY / "projects" / "_index.md")],
                cwd=str(MEMORY.parent), check=True)
        _sp.run(["git", "commit", "-m", f"project({slug}): status → {status}"],
                cwd=str(MEMORY.parent), check=True)
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"success": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="0.0.0.0", port=5200, debug=False)
