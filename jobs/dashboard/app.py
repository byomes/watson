"""Watson dashboard — port 5200, Tailscale-only."""
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, Response, g, jsonify, render_template, request, session, stream_with_context
from jobs.people.api import people_create, people_delete, people_list, people_update
from config.settings import WATSON_SYSTEM

def call_gemini(messages, system_prompt):
    import requests, os
    api_key = os.getenv("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


DB = os.path.expanduser("~/watson/data/watson.db")
SKILLS_FILE = Path(__file__).resolve().parents[2] / "memory" / "skills.json"
MEMORY = Path(__file__).resolve().parents[2] / "memory"
app = Flask(__name__, static_folder='static', template_folder='templates')
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    log.warning("FLASK_SECRET_KEY is not set — using insecure default. Set it in .env.")
    _secret_key = "watson-dashboard-secret"
app.secret_key = _secret_key


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
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT    NOT NULL,
        due_datetime  TEXT    NOT NULL DEFAULT '',
        reminder_time TEXT,
        status        TEXT    NOT NULL DEFAULT 'active',
        created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT
    )""")
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN reminder_time TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN updated_at TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
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
    try:
        c.execute("ALTER TABLE chat_messages ADD COLUMN source TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS pastoral_notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        person_name TEXT    NOT NULL,
        note        TEXT    NOT NULL,
        status      TEXT    NOT NULL DEFAULT 'active',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    )""")
    try:
        c.execute("ALTER TABLE people ADD COLUMN carrier TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS qr_cache (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        content    TEXT    NOT NULL,
        filepath   TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS appointment_bookings (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        confirmation_id  TEXT    NOT NULL UNIQUE,
        event_id         TEXT    NOT NULL,
        guest_name       TEXT    NOT NULL,
        guest_email      TEXT    NOT NULL,
        appointment_type TEXT    NOT NULL DEFAULT '',
        scheduled_at     TEXT    NOT NULL DEFAULT '',
        status           TEXT    NOT NULL DEFAULT 'confirmed',
        cancelled_at     TEXT,
        created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    try:
        c.execute("ALTER TABLE appointment_bookings ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE appointment_bookings ADD COLUMN cancelled_at TEXT")
    except Exception:
        pass
    c.commit()
    c.close()


_bootstrap()

_EMAIL_SIGNATURE = "---\nWatson\nAI-powered digital assistant\nOffice of Dr. Bill Yomes\nwilliamckyomes.com/start"


def _build_email_body(content: str) -> str:
    return f"Dr. Bill asked me to send this to you:\n\n{content}\n\n{_EMAIL_SIGNATURE}"


def _send_telegram(text: str) -> None:
    """Send a plain text message via Telegram."""
    import requests as _rq
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _send_qr_telegram(png_bytes: bytes, content: str) -> None:
    """Send QR code photo via Telegram."""
    import io as _io
    import requests as _rq
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            files={'photo': ('qr_code.png', _io.BytesIO(png_bytes), 'image/png')},
            data={'chat_id': chat_id, 'caption': f'QR code for: {content}'},
            timeout=10,
        )
    except Exception:
        pass


# Pending skill proposal keyed by a single user (single-user system)
_pending_skill_request: str | None = None

# ── Shell ─────────────────────────────────────────────────────────────────────



@app.route("/static/watson.js")
def serve_appjs():
    path = Path(__file__).parent / "static" / "watson.js"
    content = path.read_bytes()
    response = Response(content, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/")
def index():
    import time
    return render_template('index.html', app_js_ts=int(time.time()))


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
    db = _db()
    db.execute("UPDATE briefing_items SET dismissed = 1 WHERE id = ?", (item_id,))
    db.commit()
    row = db.execute("SELECT url FROM briefing_items WHERE id = ?", (item_id,)).fetchone()
    url = row["url"] if row else None
    return jsonify({"ok": True, "url": url})


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
    db = _db()
    db.execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'facebook' WHERE id = ?",
        (item_id,),
    )
    db.commit()
    row = db.execute(
        "SELECT title, summary, url FROM briefing_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row:
        draft = f"{row['title']}\n\n{row['summary']}\n\n{row['url']}\n\n#Apologetics #Theology #Faith"
        db.execute(
            "INSERT INTO facebook_queue (title, summary, url, draft_text, status) VALUES (?, ?, ?, ?, 'pending')",
            (row["title"], row["summary"], row["url"], draft),
        )
        db.commit()
    return jsonify({"ok": True, "queued": True})


@app.route("/api/briefing/<int:item_id>/email", methods=["POST"])
def briefing_email(item_id):
    from datetime import datetime as _dt
    db = _db()
    db.execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'email' WHERE id = ?",
        (item_id,),
    )
    db.commit()
    db.execute("""CREATE TABLE IF NOT EXISTS email_queue (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        subject    TEXT    NOT NULL,
        body       TEXT,
        url        TEXT,
        status     TEXT    NOT NULL DEFAULT 'pending',
        created_at TEXT    NOT NULL
    )""")
    row = db.execute(
        "SELECT title, url, summary FROM briefing_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row:
        db.execute(
            "INSERT INTO email_queue (title, summary, url, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (row["title"], row["summary"], row["url"], _dt.now().isoformat()),
        )
        db.commit()
    return jsonify({"ok": True, "queued": True})


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


@app.route("/api/briefing/<int:item_id>/research", methods=["POST"])
def briefing_research(item_id):
    db = _db()
    item = db.execute(
        "SELECT title, url, source_name, summary FROM briefing_items WHERE id = ?",
        (item_id,)
    ).fetchone()
    if not item:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.execute(
        "INSERT INTO research_sources (title, source_name, url, summary, content_type) "
        "VALUES (?, ?, ?, ?, 'article')",
        (item["title"], item["source_name"], item["url"], item["summary"])
    )
    db.execute(
        "UPDATE briefing_items SET dismissed = 1, reject_reason = 'research' WHERE id = ?",
        (item_id,)
    )
    db.commit()
    return jsonify({"ok": True, "saved": True})


@app.route("/api/research")
def research_list():
    rows = _db().execute(
        "SELECT * FROM research_sources ORDER BY added_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


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


@app.route("/api/contacts/import", methods=["POST"])
def contacts_import():
    import threading

    def _run():
        try:
            from jobs.people.google_contacts import import_contacts
            counts = import_contacts(sync_only=False)
            summary = (
                f"Google Contacts import complete: {counts['inserted']} new, "
                f"{counts['updated']} updated, {counts['skipped']} skipped. "
                f"Total: {counts['total']}"
            )
        except Exception as exc:
            log.error("Google Contacts import failed: %s", exc)
            summary = f"Google Contacts import failed: {exc}"
        bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if bot_token and chat_id:
            try:
                import requests as _req
                _req.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"📇 {summary}"},
                    timeout=15,
                )
            except Exception as exc:
                log.error("Telegram notify failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"response": "Importing contacts from Google. This may take a moment…"})


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
        "SELECT * FROM reminders WHERE status = 'active' ORDER BY sort_order ASC, created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reminders", methods=["POST"])
def reminders_create():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    reminder_time = (data.get("reminder_time") or "").strip() or None
    cur = _db().execute(
        "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
        "VALUES (?, datetime('now'), ?, 'active', datetime('now'), datetime('now'))",
        (title, reminder_time),
    )
    _db().commit()
    row = _db().execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["PATCH"])
def reminders_update(reminder_id):
    data = request.get_json(force=True)
    allowed = {"title", "status", "reminder_time", "sort_order"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    set_parts = [f"{k} = ?" for k in fields] + ["updated_at = datetime('now')"]
    set_clause = ", ".join(set_parts)
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

    # Trigger reflect every 10 assistant messages (runs silently in background)
    if role == "assistant":
        count = _db().execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        if count > 0 and count % 10 == 0:
            session_row = _db().execute(
                "SELECT project_slug FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            project_slug = session_row["project_slug"] if session_row else None
            import threading
            from jobs.memory.reflect import reflect
            threading.Thread(
                target=reflect, args=(session_id, project_slug), daemon=True
            ).start()

    return jsonify(dict(row)), 201


# ── Pastoral Notes API ───────────────────────────────────────────────────────

@app.route("/api/pastoral-notes", methods=["GET"])
def pastoral_notes_list():
    status = request.args.get("status", "active")
    rows = _db().execute(
        "SELECT id, person_name, note, status, created_at FROM pastoral_notes WHERE status = ? ORDER BY created_at DESC",
        (status,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/pastoral-notes", methods=["POST"])
def pastoral_notes_create():
    data = request.get_json()
    person_name = (data.get("person_name") or "").strip()
    note = (data.get("note") or "").strip()
    if not person_name or not note:
        return jsonify({"error": "person_name and note are required"}), 400
    cur = _db().execute(
        "INSERT INTO pastoral_notes (person_name, note) VALUES (?, ?)",
        (person_name, note)
    )
    _db().commit()
    return jsonify({"id": cur.lastrowid, "ok": True})


@app.route("/api/pastoral-notes/<int:note_id>/archive", methods=["POST"])
def pastoral_notes_archive(note_id):
    _db().execute(
        "UPDATE pastoral_notes SET status = 'archived' WHERE id = ?",
        (note_id,)
    )
    _db().commit()
    return jsonify({"ok": True})


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

_CATEGORY_ORDER = ["Core", "Research", "Writing", "Documents", "Design", "Watson Dev", "Utilities"]


@app.route("/api/skills")
def skills_list_api():
    if not SKILLS_FILE.exists():
        return jsonify([])
    try:
        skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        if not isinstance(skills, list):
            return jsonify([])
        for s in skills:
            if "status" not in s:
                s["status"] = "ready"
            if "category" not in s:
                s["category"] = "Utilities"
        return jsonify(skills)
    except Exception:
        return jsonify([])


@app.route("/api/skills/categories")
def skills_categories_api():
    return jsonify(_CATEGORY_ORDER)


@app.route("/api/skills/<slug>/approve", methods=["POST"])
def approve_skill(slug):
    if not SKILLS_FILE.exists():
        return jsonify({"success": False, "error": "skills.json not found"}), 404
    try:
        skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        skill = next((s for s in skills if s.get("slug") == slug), None)
        if not skill:
            return jsonify({"success": False, "error": "Skill not found"}), 404
        skill["status"] = "ready"
        SKILLS_FILE.write_text(json.dumps(skills, indent=2), encoding="utf-8")
        import subprocess
        repo = SKILLS_FILE.parents[1]
        subprocess.run(["git", "add", str(SKILLS_FILE)], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"skill: approved {slug} → ready"],
            cwd=str(repo), capture_output=True,
        )
        try:
            from jobs.memory.sync import main as sync_main
            sync_main()
        except Exception:
            pass
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Chat Streaming API ────────────────────────────────────────────────────────

@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    import requests as _req
    from jobs.skillbuilder import router as _router
    global _pending_skill_request

    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    session_id = data.get("session_id")
    project_slug = data.get("project_slug")

    def _sse(text):
        lines = str(text).split('\n')
        return '\n'.join(f'data: {line}' for line in lines) + '\n\n'

    def _stream_simple(text):
        yield _sse(text)
        yield "data: [DONE]\n\n"

    def _stream_error(msg):
        yield f"data: [ERROR] {msg}\n\n"

    def _sse_response(gen):
        r = Response(stream_with_context(gen), mimetype="text/event-stream")
        r.headers['Cache-Control'] = 'no-cache'
        r.headers['X-Accel-Buffering'] = 'no'
        return r

    if not message:
        return _sse_response(_stream_error("message required"))

    msg_lower = message.lower().strip()
    import re as _re

    # Remind me intake
    _remind_timed_m = _re.match(r'^remind me at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(.+)', msg_lower)
    _remind_plain_m = None if _remind_timed_m else _re.match(r'^remind me\s+(.+)', msg_lower)
    if _remind_timed_m or _remind_plain_m:
        from jobs.reminders import parse_reminder_time
        if _remind_timed_m:
            _rt = parse_reminder_time(_remind_timed_m.group(1))
            _title = message[_remind_timed_m.start(2):].strip() if _rt else message[len("remind me at "):].strip()
        else:
            _rt = None
            _title = message[_remind_plain_m.start(1):].strip()
        if _title:
            _db().execute(
                "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
                "VALUES (?, datetime('now'), ?, 'active', datetime('now'), datetime('now'))",
                (_title, _rt),
            )
            _db().commit()
            _reply = f"Reminder set for {_rt}: {_title}" if _rt else f"Reminder saved: {_title}"
            return _sse_response(_stream_simple(_reply))

    # build: dispatch — route to Gemini coder
    if msg_lower.startswith('build:'):
        description = message[6:].strip()
        import threading
        from jobs.dev.gemini_coder import request_build

        def _run_build():
            request_build(description)

        threading.Thread(target=_run_build, daemon=True).start()
        return _sse_response(_stream_simple(
            "Sending to Gemini... I'll notify you via Telegram when the build is ready."
        ))

    # debug: dispatch — route to Gemini debugger
    if msg_lower.startswith('debug:'):
        description = message[6:].strip()
        import threading
        from jobs.dev.gemini_coder import request_debug

        def _run_debug():
            request_debug(description)

        threading.Thread(target=_run_debug, daemon=True).start()
        return _sse_response(_stream_simple(
            "Sending to Gemini debugger... I'll notify you via Telegram when the debug prompt is ready."
        ))

    # QR code generation
    _QR_TRIGGERS = ('qr code', 'qr-code', 'make a qr', 'give me a qr',
                    'generate a qr', 'create a qr', 'make qr', 'qr for')
    if any(t in msg_lower for t in _QR_TRIGGERS):
        import base64 as _b64
        import re as _re2
        from jobs.qr.qr_generate import generate_qr as _gen_qr
        _qr_patterns = [
            r'(?:make a|give me a|generate a|create a|make|give me)\s+qr\s+(?:code\s+)?(?:for[: ]+)?(.+)',
            r'qr\s+(?:code\s+)?(?:for\s+)?(.+)',
        ]
        _qr_content = None
        for _pat in _qr_patterns:
            _m = _re2.search(_pat, msg_lower)
            if _m:
                _qr_content = _m.group(1).strip()
                break
        if _qr_content:
            try:
                _filepath, _png = _gen_qr(_qr_content)
                _img_b64 = _b64.b64encode(_png).decode('utf-8')
                _db().execute(
                    "INSERT INTO qr_cache (content, filepath) VALUES (?, ?)",
                    (_qr_content, _filepath),
                )
                _db().commit()
                _send_qr_telegram(_png, _qr_content)

                def _qr_stream(content=_qr_content, b64=_img_b64):
                    yield _sse(f'QR code generated for: {content}')
                    yield f'data: [QR_IMAGE]{b64}\n\n'
                    yield 'data: [DONE]\n\n'

                return _sse_response(_qr_stream())
            except Exception as _exc:
                return _sse_response(_stream_simple(f'QR generation failed: {_exc}'))
        else:
            return _sse_response(_stream_simple('What should the QR code contain?'))

    # QR email follow-up: "email this to [name]"
    _email_qr_match = _re.search(r'(?:email|send)\s+this\s+(?:qr\s+)?to\s+(.+)', msg_lower)
    if _email_qr_match:
        _db().execute("DELETE FROM qr_cache WHERE created_at < datetime('now', '-24 hours')")
        _db().commit()
        _lq = _db().execute(
            "SELECT content, filepath FROM qr_cache ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if _lq:
            _contact_name = _email_qr_match.group(1).strip().rstrip('.')
            from jobs.people.lookup import lookup_member as _lm
            _hits = _lm(_contact_name)
            _contact = next((c for c in _hits if c.get('email')), None)
            if _contact:
                from jobs.qr.qr_generate import send_qr_email as _send_qr_email
                try:
                    _send_qr_email(_contact['email'], _contact['name'], _lq['content'], open(_lq['filepath'], 'rb').read())
                    return _sse_response(_stream_simple(
                        f"QR code sent to {_contact['name']} ({_contact['email']})."
                    ))
                except Exception as _exc:
                    return _sse_response(_stream_simple(f'Failed to send email: {_exc}'))
            else:
                return _sse_response(_stream_simple(
                    f"No contact found for '{_contact_name}'. Check the name and try again."
                ))

    # SMS pre-check
    _SMS_TRIGGERS = (
        'text ', 'send a text', 'send text', 'shoot a text',
        'shoot them a text', 'shoot her a text', 'shoot him a text',
    )
    if any(t in msg_lower for t in _SMS_TRIGGERS):
        import re as _sms_re
        from jobs.people.lookup import lookup_member as _lookup_sms
        from jobs.sms.sms_send import send_sms_to_contact as _send_sms

        _sms_me_pattern = _sms_re.search(
            r'(?:text|send a text to)\s+me\s+(?:that\s+|saying\s+)?(.+)',
            msg_lower,
        )
        _sms_pattern = _sms_re.search(
            r'(?:text|send a text to|send text to|shoot a text to)\s+(\w+(?:\s+\w+)?)\s*(?::|that\s+|saying\s+|to say\s+)?\s*(.+)',
            msg_lower,
        )

        if _sms_me_pattern:
            _sms_msg_raw = message[_sms_me_pattern.start(1):].strip()
            _recent_qr = _db().execute(
                "SELECT content FROM qr_cache ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if 'qr' in msg_lower and _recent_qr:
                _sms_msg_raw = f"QR code content: {_recent_qr['content']}"
            _owner_phone = os.getenv('WATSON_OWNER_PHONE')
            _owner_carrier = os.getenv('WATSON_OWNER_CARRIER', 'verizon')
            if not _owner_phone:
                return _sse_response(_stream_simple(
                    "WATSON_OWNER_PHONE is not set in .env. Add your phone number to send texts to yourself."
                ))
            from jobs.sms.sms_send import send_sms as _send_sms_direct
            _sms_result = _send_sms_direct('Dr. Bill', _owner_phone, _owner_carrier, _sms_msg_raw)
            if _sms_result['success']:
                return _sse_response(_stream_simple(f"Text sent to you: {_sms_msg_raw}"))
            else:
                return _sse_response(_stream_simple(f"Failed to send text: {_sms_result['error']}"))

        elif _sms_pattern:
            _contact_raw = _sms_pattern.group(1).strip()
            _sms_msg_start = _sms_pattern.start(2)
            _sms_message = message[_sms_msg_start:].strip()
            _recent_qr = _db().execute(
                "SELECT content FROM qr_cache ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if 'qr' in msg_lower and _recent_qr:
                _sms_message = f"QR code: {_recent_qr['content']}"
            _sms_hits = _lookup_sms(_contact_raw)
            _sms_contact = next((c for c in _sms_hits if c.get('phone')), None)
            if not _sms_contact:
                return _sse_response(_stream_simple(
                    f"No contact found for '{_contact_raw}'. Check the name and try again."
                ))
            _sms_result = _send_sms(_sms_contact, _sms_message)
            if _sms_result['success']:
                return _sse_response(_stream_simple(
                    f"Text sent to {_sms_contact['name']} ({_sms_contact.get('phone', '')})."
                ))
            else:
                return _sse_response(_stream_simple(f"Failed: {_sms_result['error']}"))

    # Handle yes/no follow-up on a pending skill proposal
    if _pending_skill_request is not None:
        if msg_lower in _AFFIRM:
            pending = _pending_skill_request
            _pending_skill_request = None
            job_path = _router._generate_job_path(pending)
            import threading
            threading.Thread(
                target=_router._build_in_background,
                args=(pending, job_path, "dashboard"),
                daemon=True,
            ).start()
            return _sse_response(_stream_simple("Building that skill now. I'll notify you via Telegram when it's ready."))
        if msg_lower in _DENY or msg_lower.startswith("no "):
            _pending_skill_request = None
            return _sse_response(_stream_simple("Got it. Let me know if you need anything else."))

    # Pastoral notes — create
    _pn_create = _re.search(
        r'pastoral note[s]?\s+(?:that|for|about)?\s*(.+)',
        message, _re.IGNORECASE
    )
    if _pn_create:
        raw = _pn_create.group(1).strip()
        name_match = _re.match(r'^([A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+(?:is|has|was|will|needs|received)', raw)
        if name_match:
            person_name = name_match.group(1)
            note_text = raw
        else:
            person_name = "Unknown"
            note_text = raw
        _db().execute(
            "INSERT INTO pastoral_notes (person_name, note) VALUES (?, ?)",
            (person_name, note_text)
        )
        _db().commit()
        return _sse_response(_stream_simple(f"Pastoral note saved for {person_name}."))

    # Pastoral notes — show
    if any(p in msg_lower for p in ("show pastoral notes", "pastoral notes", "show me the pastoral notes")):
        rows = _db().execute(
            "SELECT person_name, note, created_at FROM pastoral_notes WHERE status = 'active' ORDER BY created_at DESC"
        ).fetchall()
        if not rows:
            return _sse_response(_stream_simple("No active pastoral notes."))
        lines = ["**Pastoral Notes**\n"]
        for r in rows:
            lines.append(f"**{r['person_name']}** — {r['created_at'][:16]}\n{r['note']}\n")
        return _sse_response(_stream_simple("\n".join(lines)))

    # Send contact info
    _send_contact = _re.search(
        r"send (.+?)'s contact info to (.+)",
        message, _re.IGNORECASE
    )
    if _send_contact:
        contact_name = _send_contact.group(1).strip()
        recipient_name = _send_contact.group(2).strip().rstrip('.')

        from jobs.people.lookup import lookup_member as _lookup_member
        _contact_results = _lookup_member(contact_name)
        contact = _contact_results[0] if _contact_results else None

        _recip_results = _lookup_member(recipient_name)
        recipient = _recip_results[0] if _recip_results else None

        if not contact:
            return _sse_response(_stream_simple(f"I couldn't find contact info for {contact_name}."))
        if not recipient or not recipient["email"]:
            return _sse_response(_stream_simple(f"I couldn't find an email address for {recipient_name} in the people registry."))

        lines = [f"Contact info for {contact['name']}:"]
        if contact["phone"]:
            lines.append(f"Phone: {contact['phone']}")
        if contact["email"]:
            lines.append(f"Email: {contact['email']}")
        body = "\n".join(lines)

        import smtplib
        from email.mime.text import MIMEText
        smtp_host = os.getenv("WATSON_SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("WATSON_SMTP_PORT", 587))
        smtp_user = os.getenv("WATSON_GMAIL_ADDRESS")
        smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD")
        from_addr = os.getenv("WATSON_FROM_ADDRESS", smtp_user)

        msg = MIMEText(_build_email_body(body))
        msg["Subject"] = f"Contact info: {contact['name']}"
        msg["From"] = f"Watson <{from_addr}>"
        msg["To"] = recipient["email"]

        try:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, [recipient["email"]], msg.as_string())
            return _sse_response(_stream_simple(f"Sent {contact['name']}'s contact info to {recipient['name']} at {recipient['email']}."))
        except Exception as exc:
            return _sse_response(_stream_simple(f"Failed to send email: {exc}"))

    # Member lookup
    _lookup_triggers = ("contact info", "contact", "lookup", "find member", "who is")
    if any(t in msg_lower for t in _lookup_triggers):
        _SKIP_WORDS = {"what", "what's", "whats", "who", "is", "are", "was",
                       "please", "send", "get", "find", "lookup", "show",
                       "give", "tell", "me", "the", "a", "an", "his", "her",
                       "their", "your", "my", "do", "you", "have", "any",
                       "info", "contact", "number", "phone", "email", "for",
                       "on", "about", "and", "or", "of", "in", "to", "s"}
        _msg_clean = _re.sub(r'^watson[,\s]+', '', message, flags=_re.IGNORECASE).strip()
        _words = _re.findall(r"[a-zA-Z']+", _msg_clean)
        _name_words = [w for w in (_re.sub(r"'s\b", "", w) for w in _words)
                       if w and w.lower() not in _SKIP_WORDS and len(w) > 1]
        if _name_words:
            _lq = " ".join(_name_words[:2])
            from jobs.people.lookup import lookup_member
            members = lookup_member(_lq)
            if not members and len(_name_words) > 1:
                members = lookup_member(_name_words[-1])
            if not members:
                members = lookup_member(_name_words[0])
            if not members:
                return _sse_response(_stream_simple(f"No members found matching '{_lq}'."))
            session['last_contact_lookup'] = [
                {"name": m["name"], "phone": m.get("phone") or "", "email": m.get("email") or ""}
                for m in members
            ]
            blocks = []
            for m in members:
                contact_lines = [m['name']]
                if m.get("phone"):
                    contact_lines.append(m["phone"])
                if m.get("email"):
                    contact_lines.append(m["email"])
                blocks.append("\n".join(contact_lines))
            return _sse_response(_stream_simple("\n\n".join(blocks)))

    # Send last contact lookup result
    _send_that = _re.search(r"send that to[:\s]+(.+)", message, _re.IGNORECASE)
    if _send_that:
        _last = session.get('last_contact_lookup')
        if not _last:
            return _sse_response(_stream_simple(
                "I don't have a recent contact lookup to send. Ask me for someone's contact info first."
            ))
        _target = _send_that.group(1).strip().rstrip('.')
        _to_email = None
        _to_label = _target
        if '@' in _target:
            _to_email = _target
        elif _target.lower() in ("me", "myself"):
            _to_email = os.getenv("WATSON_OWNER_EMAIL")
            if not _to_email:
                return _sse_response(_stream_simple("WATSON_OWNER_EMAIL is not set."))
        else:
            from jobs.people.lookup import lookup_member as _lookup_member
            _recip_hits = _lookup_member(_target)
            _recip = next((r for r in _recip_hits if r.get("email")), None)
            if not _recip or not _recip["email"]:
                return _sse_response(_stream_simple(
                    f"I couldn't find an email address for {_target} in the people registry."
                ))
            _to_email = _recip["email"]
            _to_label = _recip["name"]

        _body_blocks = []
        for _c in _last:
            _clines = [_c["name"]]
            if _c.get("phone"):
                _clines.append(_c["phone"])
            if _c.get("email"):
                _clines.append(_c["email"])
            _body_blocks.append("\n".join(_clines))
        _body = "\n\n".join(_body_blocks)
        _names = ", ".join(_c["name"] for _c in _last)
        _subject = f"Contact info: {_names}"

        import smtplib
        from email.mime.text import MIMEText
        _smtp_host = os.getenv("WATSON_SMTP_HOST", "smtp.gmail.com")
        _smtp_port = int(os.getenv("WATSON_SMTP_PORT", 587))
        _smtp_user = os.getenv("WATSON_GMAIL_ADDRESS")
        _smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD")
        _from_addr = os.getenv("WATSON_FROM_ADDRESS", _smtp_user)
        _emsg = MIMEText(_build_email_body(_body))
        _emsg["Subject"] = _subject
        _emsg["From"] = f"Watson <{_from_addr}>"
        _emsg["To"] = _to_email
        try:
            with smtplib.SMTP(_smtp_host, _smtp_port) as _srv:
                _srv.starttls()
                _srv.login(_smtp_user, _smtp_pass)
                _srv.sendmail(_from_addr, [_to_email], _emsg.as_string())
            return _sse_response(_stream_simple(
                f"Sent {_names}'s contact info to {_to_label} at {_to_email}."
            ))
        except Exception as _exc:
            return _sse_response(_stream_simple(f"Failed to send email: {_exc}"))

    # Report menu
    from jobs.connect_cards.report_menu import get_menu_html as _get_menu_html
    if msg_lower in ("reports", "report menu", "pastoral reports"):
        return _sse_response(_stream_simple(_get_menu_html()))

    _report_match = _re.match(r'^run report:\s*(\S+)(?:\s+(\d+))?', msg_lower)
    if _report_match:
        from jobs.connect_cards.report_menu import run_report
        _report_key = _report_match.group(1).strip()
        _report_weeks = int(_report_match.group(2)) if _report_match.group(2) else None
        try:
            subject, html = run_report(_report_key, weeks=_report_weeks)
        except Exception as exc:
            return _sse_response(_stream_simple(f"Report error: {exc}"))
        import re as _re2
        body_match = _re2.search(r'<body[^>]*>(.*?)</body>', html, _re2.DOTALL)
        body = body_match.group(1) if body_match else html
        return _sse_response(_stream_simple(body))

    # Skill audit — show report
    if any(p in msg_lower for p in ("show skill audit", "skill audit report")):
        _audit_path = Path(os.path.expanduser("~/watson/data/skill_audit.json"))
        if not _audit_path.exists():
            return _sse_response(_stream_simple(
                "No skill audit on file. Say 'run skill audit' to generate one."
            ))
        try:
            _audit = json.loads(_audit_path.read_text(encoding="utf-8"))
            _s = _audit.get("summary", {})
            _lines = [
                f"Skill Audit — run {_audit.get('run_at', 'unknown')[:19]}\n",
                f"✅ Functional: {_s.get('functional', 0)}",
                f"📝 Prompt-only: {_s.get('prompt_only', 0)}",
                f"❌ Broken: {_s.get('broken', 0)}",
                f"📦 Missing deps: {_s.get('missing_deps', 0)}",
                f"🔑 Missing creds: {_s.get('missing_creds', 0)}",
            ]
            _issues = [
                sk for sk in _audit.get("skills", [])
                if sk.get("status") != "functional" and sk.get("status") != "prompt_only"
            ]
            if _issues:
                _lines.append("\nBroken/Issues:")
                for _sk in _issues:
                    _lines.append(f"{_sk['slug']} — {_sk['status']}: {_sk['detail']}")
            return _sse_response(_stream_simple("\n".join(_lines)))
        except Exception as _exc:
            return _sse_response(_stream_simple(f"Failed to load skill audit: {_exc}"))

    # Skill audit — run in background
    if any(p in msg_lower for p in ("run skill audit", "audit skills", "audit my skills")):
        import threading as _threading
        def _run_audit():
            try:
                from jobs.skillbuilder.audit import run_skill_audit
                run_skill_audit()
            except Exception as _exc:
                log.error("Skill audit background run failed: %s", _exc)
        _threading.Thread(target=_run_audit, daemon=True).start()
        return _sse_response(_stream_simple(
            "Running skill audit in the background. I'll send a Telegram when it's done."
        ))

    # Direct slug dispatch — from dashboard Use button (run:<slug>)
    if msg_lower.startswith("run:"):
        run_body = message[4:].strip()
        _run_parts = run_body.split(None, 1)
        slug = _run_parts[0] if _run_parts else ""
        skill_message = _run_parts[1] if len(_run_parts) > 1 else ""
        skills = _router._load_skills("dashboard")
        skill = next((s for s in skills if s["slug"] == slug), None)
        if skill:
            try:
                result = _router._run_skill(skill, message=skill_message)
            except Exception as exc:
                result = f"Skill error: {exc}"
            return _sse_response(_stream_simple(str(result)))
        else:
            return _sse_response(_stream_simple(f"Skill not found: {slug}"))

    # Time query pre-check
    if _re.search(r"what.*(time|hour).*is it|what time|current time", msg_lower):
        from jobs.time_check import run as _time_run
        return _sse_response(_stream_simple(_time_run()))
    _identity = _router._is_identity_query(message)
    _factual = _router._is_factual_query(message)
    _conv = _router._is_conversational(message)
    log.info("ROUTE msg=%r identity=%s factual=%s conversational=%s", message[:120], _identity, _factual, _conv)

    # 0. Identity questions go straight to Ollama
    if _identity:
        route_result = {"action": "chat"}
    # 1. Factual queries go directly to web search, bypassing Ollama
    elif _factual:
        from jobs.research.web_search import run as web_search_run
        ws_result = web_search_run(message)
        return _sse_response(_stream_simple("✓ " + ws_result))
    # 2. Conversational messages go straight to Ollama
    elif _conv:
        route_result = {"action": "chat"}
    # 3. Everything else goes through the skill router
    else:
        try:
            route_result = _router.route(message, "dashboard")
        except Exception:
            route_result = {"action": "chat"}

    if route_result["action"] == "skill":
        if "result" not in route_result:
            slug = route_result["slug"]
            skills = _router._load_skills("dashboard")
            skill = next((s for s in skills if s["slug"] == slug), None)
            if skill:
                try:
                    route_result["result"] = _router._run_skill(
                        skill, message=route_result.get("message")
                    )
                except Exception as exc:
                    log.error("Skill execution failed for %s: %s", slug, exc)
                    route_result["result"] = f"Skill failed: {exc}"
            else:
                log.warning("Skill '%s' not found in registry — falling through to Gemini", slug)
        if "result" in route_result:
            result = route_result["result"]
            if isinstance(result, dict) and result.get("confirm"):
                session["pending_email"] = result
                confirm_text = f"I found {result['to_name']} at {result['to_email']}. Confirm below to send."
                confirm_json = json.dumps({
                    "to_name": result["to_name"],
                    "to_email": result["to_email"],
                    "subject": result["subject"],
                    "body": result["body"],
                })
                def _email_gen(t=confirm_text, cj=confirm_json):
                    yield _sse(t)
                    yield f"data: [CONFIRM_EMAIL]{cj}\n\n"
                    yield "data: [DONE]\n\n"
                return _sse_response(_email_gen())
            return _sse_response(_stream_simple("✓ " + result))

    if route_result["action"] == "build":
        import threading
        threading.Thread(
            target=_router._build_in_background,
            args=(route_result["description"], route_result["job_path"], "dashboard"),
            daemon=True,
        ).start()
        return _sse_response(_stream_simple("Building that skill now. I'll notify you via Telegram when it's ready."))

    if route_result["action"] == "propose":
        _pending_skill_request = message
        return _sse_response(_stream_simple(route_result["message"]))

    if route_result["action"] == "wrap_up":
        log.info("WRAP_UP triggered — session_id=%s project_slug=%s", session_id, project_slug)
        try:
            import threading
            from jobs.memory.wrap_up import wrap_up as _wrap_up
            threading.Thread(
                target=_wrap_up,
                args=(session_id, project_slug),
                daemon=True,
            ).start()
        except Exception as exc:
            log.error("WRAP_UP: failed to start background thread: %s", exc)
        return _sse_response(_stream_simple("Wrapping up this session. I'll save it to memory and notify you via Telegram."))

    if any(t in msg_lower for t in _router._BUILD_TRIGGERS):
        import threading
        description = _router._extract_build_description(message)
        job_path = _router._generate_job_path(description)
        threading.Thread(
            target=_router._build_in_background,
            args=(description, job_path, "dashboard"),
            daemon=True,
        ).start()
        return _sse_response(_stream_simple("Building that skill now. I'll notify you via Telegram when it's ready."))

    # Fall through to Ollama
    messages = []
    for h in history[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    def _stream_ollama(msgs=messages):
        try:
            resp = _req.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "llama3.2:3b",
                    "system": WATSON_SYSTEM,
                    "messages": msgs,
                    "stream": True,
                    "num_predict": 300,
                },
                stream=True,
                timeout=30,
            )
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield _sse(token)
                if chunk.get("done"):
                    break
            yield "data: [DONE]\n\n"
        except Exception:
            yield "data: [ERROR] Watson timed out. Try again.\n\n"

    return _sse_response(_stream_ollama())


# ── Siri API ──────────────────────────────────────────────────────────────────

@app.route("/api/siri", methods=["POST"])
def siri():
    import re as _siri_re
    import requests as _siri_req
    import threading as _siri_threading
    from jobs.skillbuilder import router as _siri_router

    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"status": "error", "error": "message required"}), 400

    def _process(msg=message):
        msg_lower = msg.lower().strip()

        def _reply(text):
            _send_telegram(text or "No response from Watson.")

        # Remind me intake
        _remind_timed_m = _siri_re.match(r'^remind me at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(.+)', msg_lower)
        _remind_plain_m = None if _remind_timed_m else _siri_re.match(r'^remind me\s+(.+)', msg_lower)
        if _remind_timed_m or _remind_plain_m:
            from jobs.reminders import parse_reminder_time
            if _remind_timed_m:
                _rt = parse_reminder_time(_remind_timed_m.group(1))
                _title = msg[_remind_timed_m.start(2):].strip() if _rt else msg[len("remind me at "):].strip()
            else:
                _rt = None
                _title = msg[_remind_plain_m.start(1):].strip()
            if _title:
                with sqlite3.connect(DB) as _c:
                    _c.execute(
                        "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
                        "VALUES (?, datetime('now'), ?, 'active', datetime('now'), datetime('now'))",
                        (_title, _rt),
                    )
                return _reply(f"Reminder set for {_rt}: {_title}" if _rt else f"Reminder saved: {_title}")

        # build: dispatch
        if msg_lower.startswith('build:'):
            from jobs.dev.gemini_coder import request_build
            _siri_threading.Thread(target=request_build, args=(msg[6:].strip(),), daemon=True).start()
            return _reply("Sending to Gemini... I'll notify you via Telegram when the build is ready.")

        # debug: dispatch
        if msg_lower.startswith('debug:'):
            from jobs.dev.gemini_coder import request_debug
            _siri_threading.Thread(target=request_debug, args=(msg[6:].strip(),), daemon=True).start()
            return _reply("Sending to Gemini debugger... I'll notify you via Telegram when the debug prompt is ready.")

        # Time query
        if _siri_re.search(r"what.*(time|hour).*is it|what time|current time", msg_lower):
            from jobs.time_check import run as _time_run
            return _reply(_time_run())

        # Identity / factual / conversational routing
        _identity = _siri_router._is_identity_query(msg)
        _factual = _siri_router._is_factual_query(msg)
        _conv = _siri_router._is_conversational(msg)

        if _identity:
            route_result = {"action": "chat"}
        elif _factual:
            from jobs.research.web_search import run as web_search_run
            return _reply("✓ " + web_search_run(msg))
        elif _conv:
            route_result = {"action": "chat"}
        else:
            try:
                route_result = _siri_router.route(msg, "dashboard")
            except Exception:
                route_result = {"action": "chat"}

        if route_result["action"] == "skill":
            if "result" not in route_result:
                slug = route_result["slug"]
                skills = _siri_router._load_skills("dashboard")
                skill = next((s for s in skills if s["slug"] == slug), None)
                if skill:
                    try:
                        route_result["result"] = _siri_router._run_skill(skill, message=route_result.get("message"))
                    except Exception as exc:
                        route_result["result"] = f"Skill failed: {exc}"
                else:
                    route_result["result"] = f"Skill '{slug}' not found."
            return _reply("✓ " + str(route_result["result"]))

        if route_result["action"] == "build":
            _siri_threading.Thread(
                target=_siri_router._build_in_background,
                args=(route_result["description"], route_result["job_path"], "dashboard"),
                daemon=True,
            ).start()
            return _reply("Building that skill now. I'll notify you via Telegram when it's ready.")

        if route_result["action"] == "propose":
            return _reply(route_result["message"])

        if any(t in msg_lower for t in _siri_router._BUILD_TRIGGERS):
            description = _siri_router._extract_build_description(msg)
            job_path = _siri_router._generate_job_path(description)
            _siri_threading.Thread(
                target=_siri_router._build_in_background,
                args=(description, job_path, "dashboard"),
                daemon=True,
            ).start()
            return _reply("Building that skill now. I'll notify you via Telegram when it's ready.")

        # Ollama fallback
        try:
            resp = _siri_req.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "llama3.2:3b",
                    "system": WATSON_SYSTEM,
                    "messages": [{"role": "user", "content": msg}],
                    "stream": True,
                    "num_predict": 300,
                },
                stream=True,
                timeout=45,
            )
            resp.raise_for_status()
            parts = []
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if token:
                    parts.append(token)
                if chunk.get("done"):
                    break
            return _reply("".join(parts) or "No response from Watson.")
        except Exception as exc:
            return _reply(f"Watson error: {exc}")

    _siri_threading.Thread(target=_process, daemon=True).start()
    return jsonify({"status": "ok"})


# ── Chat API ─────────────────────────────────────────────────────────────────

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
            job_path = _router._generate_job_path(pending)
            import threading
            threading.Thread(
                target=_router._build_in_background,
                args=(pending, job_path, "dashboard"),
                daemon=True,
            ).start()
            return jsonify({"response": "Building that skill now. I’ll notify you via Telegram when it’s ready."})
        if msg_lower in _DENY or msg_lower.startswith("no "):
            _pending_skill_request = None
            return jsonify({"response": "Got it. Let me know if you need anything else."})

    # Skill audit — show report
    if any(p in msg_lower for p in ("show skill audit", "skill audit report")):
        _audit_path = Path(os.path.expanduser("~/watson/data/skill_audit.json"))
        if not _audit_path.exists():
            return jsonify({"response": "No skill audit on file. Say 'run skill audit' to generate one."})
        try:
            _audit = json.loads(_audit_path.read_text(encoding="utf-8"))
            _s = _audit.get("summary", {})
            _lines = [
                f"Skill Audit — run {_audit.get('run_at', 'unknown')[:19]}\n",
                f"✅ Functional: {_s.get('functional', 0)}",
                f"📝 Prompt-only: {_s.get('prompt_only', 0)}",
                f"❌ Broken: {_s.get('broken', 0)}",
                f"📦 Missing deps: {_s.get('missing_deps', 0)}",
                f"🔑 Missing creds: {_s.get('missing_creds', 0)}",
            ]
            _issues = [
                sk for sk in _audit.get("skills", [])
                if sk.get("status") != "functional" and sk.get("status") != "prompt_only"
            ]
            if _issues:
                _lines.append("\nBroken/Issues:")
                for _sk in _issues:
                    _lines.append(f"{_sk['slug']} — {_sk['status']}: {_sk['detail']}")
            return jsonify({"response": "\n".join(_lines)})
        except Exception as _exc:
            return jsonify({"response": f"Failed to load skill audit: {_exc}"})

    # Skill audit — run in background
    if any(p in msg_lower for p in ("run skill audit", "audit skills", "audit my skills")):
        import threading as _threading
        def _run_audit():
            try:
                from jobs.skillbuilder.audit import run_skill_audit
                run_skill_audit()
            except Exception as _exc:
                log.error("Skill audit background run failed: %s", _exc)
        _threading.Thread(target=_run_audit, daemon=True).start()
        return jsonify({"response": "Running skill audit in the background. I'll send a Telegram when it's done."})

    # 0. Identity questions go straight to Ollama
    # Skip routing for conversational messages — go straight to Ollama
    if _router._is_identity_query(message) or _router._is_conversational(message):
        route_result = {"action": "chat"}
    else:
        try:
            route_result = _router.route(message, "dashboard")
        except Exception:
            route_result = {"action": "chat"}

    if route_result["action"] == "skill":
        if "result" not in route_result:
            slug = route_result["slug"]
            skills = _router._load_skills("dashboard")
            skill = next((s for s in skills if s["slug"] == slug), None)
            if skill:
                try:
                    route_result["result"] = _router._run_skill(
                        skill, message=route_result.get("message")
                    )
                except Exception as exc:
                    log.error("Skill execution failed for %s: %s", slug, exc)
                    route_result["result"] = f"Skill failed: {exc}"
            else:
                log.error("Skill '%s' not found in registry", slug)
                route_result["result"] = f"Skill '{slug}' not found."
        result = route_result["result"]
        if isinstance(result, dict) and result.get("confirm"):
            session["pending_email"] = result
            return jsonify({
                "response": f"I found {result['to_name']} at {result['to_email']}. Confirm below to send.",
                "confirm_email": {
                    "to_name": result["to_name"],
                    "to_email": result["to_email"],
                    "subject": result["subject"],
                    "body": result["body"],
                },
            })
        return jsonify({"response": "✓ " + result})

    if route_result["action"] == "build":
        import threading
        threading.Thread(
            target=_router._build_in_background,
            args=(route_result["description"], route_result["job_path"], "dashboard"),
            daemon=True,
        ).start()
        return jsonify({"response": "Building that skill now. I'll notify you via Telegram when it's ready."})

    if route_result["action"] == "propose":
        _pending_skill_request = message
        return jsonify({"response": route_result["message"]})

    if route_result["action"] == "wrap_up":
        session_id = data.get("session_id")
        project_slug = data.get("project_slug")
        log.info("WRAP_UP triggered — session_id=%s project_slug=%s", session_id, project_slug)
        try:
            import threading
            from jobs.memory.wrap_up import wrap_up as _wrap_up
            threading.Thread(
                target=_wrap_up,
                args=(session_id, project_slug),
                daemon=True,
            ).start()
        except Exception as exc:
            log.error("WRAP_UP: failed to start background thread: %s", exc)
        return jsonify({"response": "Wrapping up this session. I'll save it to memory and notify you via Telegram."})

    # Safety net: if any build trigger leaked past the router, fire the build now.
    # This prevents the Ollama SPEC/CONFIRM path from ever activating on build requests.
    if any(t in msg_lower for t in _router._BUILD_TRIGGERS):
        import threading
        description = _router._extract_build_description(message)
        job_path = _router._generate_job_path(description)
        threading.Thread(
            target=_router._build_in_background,
            args=(description, job_path, "dashboard"),
            daemon=True,
        ).start()
        return jsonify({"response": "Building that skill now. I'll notify you via Telegram when it's ready."})

    # Fall through to Ollama
    messages = []
    for h in history[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})
    try:
        _body = {"model": "llama3.2:3b", "system": WATSON_SYSTEM, "messages": messages, "stream": True, "num_predict": 300}
        log.info("Ollama /api/chat request body: %s", json.dumps(_body))
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json=_body,
            stream=True,
            timeout=30,
        )
        resp.raise_for_status()
        reply_parts = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except Exception:
                continue
            token = chunk.get("message", {}).get("content", "")
            if token:
                reply_parts.append(token)
            if chunk.get("done"):
                break
        return jsonify({"response": "".join(reply_parts)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Calendar API ──────────────────────────────────────────────────────────────


@app.route("/api/calendar/busy-rest-of-day", methods=["POST"])
def calendar_busy_rest_of_day():
    import requests as _req
    from jobs.gcal.gcal_service import mark_day_busy_from_now
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
    from jobs.gcal.gcal_service import get_todays_events
    try:
        events = get_todays_events()
        return jsonify(events)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Appointments API ──────────────────────────────────────────────────────────

@app.route("/api/cancel-appointment")
def cancel_appointment():
    import smtplib
    from email.mime.text import MIMEText

    confirmation_id = request.args.get("id", "").strip()
    if not confirmation_id:
        return jsonify({"ok": False, "error": "id required"})

    db = _db()
    row = db.execute(
        "SELECT * FROM appointment_bookings WHERE confirmation_id = ?",
        (confirmation_id,),
    ).fetchone()

    if not row:
        return jsonify({"ok": False, "error": "not found"})

    if row["status"] == "cancelled":
        return jsonify({"ok": False, "error": "already_cancelled"})

    # Delete Google Calendar event
    try:
        from jobs.gcal.gcal_service import cancel_event
        cancel_event(row["event_id"])
    except Exception as exc:
        log.error("cancel_appointment: failed to delete calendar event %s: %s", row["event_id"], exc)

    # Mark as cancelled
    db.execute(
        "UPDATE appointment_bookings SET status = 'cancelled', cancelled_at = datetime('now') "
        "WHERE confirmation_id = ?",
        (confirmation_id,),
    )
    db.commit()

    # Send cancellation email to guest
    guest_name = row["guest_name"] or ""
    first_name = guest_name.split()[0] if guest_name else "there"
    smtp_user = os.getenv("WATSON_GMAIL_ADDRESS")
    smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD")
    from_addr = os.getenv("WATSON_FROM_ADDRESS", smtp_user)
    email_body = (
        f"Hi {first_name},\n\n"
        "Your appointment with Dr. Bill Yomes has been cancelled.\n\n"
        "To book a new appointment, visit:\n"
        "williamckyomes.com/meet\n\n"
        "Watson\n"
        "AI-powered digital assistant\n"
        "Office of Dr. Bill Yomes\n"
        "williamckyomes.com/start"
    )
    msg = MIMEText(email_body)
    msg["Subject"] = "Your Appointment Has Been Cancelled"
    msg["From"] = f"Watson <{from_addr}>"
    msg["To"] = row["guest_email"]
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [row["guest_email"]], msg.as_string())
    except Exception as exc:
        log.error("cancel_appointment: failed to send email to %s: %s", row["guest_email"], exc)

    # Send Telegram notification
    appt_type = row["appointment_type"] or "appointment"
    scheduled = row["scheduled_at"] or "unknown time"
    _send_telegram(
        f"\U0001f4c5 {guest_name} cancelled their {appt_type} appointment scheduled for {scheduled}"
    )

    return jsonify({"ok": True})


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


@app.route("/api/email/confirm", methods=["POST"])
def email_confirm():
    from jobs.email.send import _send_smtp
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("confirm"):
        session.pop("pending_email", None)
        return jsonify({"response": "Email cancelled."})
    pending = session.pop("pending_email", None)
    if not pending:
        return jsonify({"response": "No pending email found."}), 400
    try:
        _send_smtp(
            pending["to_email"], pending["subject"], pending["body"],
            to_name=pending["to_name"],
        )
    except Exception as exc:
        log.error("Email confirm send failed: %s", exc)
        return jsonify({"response": f"Failed to send email: {exc}"}), 500
    return jsonify({"response": f"Email sent to {pending['to_name']} ✓"})


@app.route("/api/projects/<slug>/memory", methods=["GET"])
def projects_memory_get(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "not found"}), 404
    mem_path = project_dir / "memory.md"
    content = mem_path.read_text(encoding="utf-8") if mem_path.exists() else ""
    return jsonify({"content": content})


@app.route("/api/projects/<slug>/memory", methods=["POST"])
def projects_memory_post(slug):
    project_dir = MEMORY / "projects" / slug
    if not project_dir.exists():
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    addition = (data.get("content") or "").strip()
    if not addition:
        return jsonify({"error": "content required"}), 400
    mem_path = project_dir / "memory.md"
    existing = mem_path.read_text(encoding="utf-8") if mem_path.exists() else ""
    sep = "\n\n" if existing.strip() else ""
    mem_path.write_text(existing + sep + addition, encoding="utf-8")
    return jsonify({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="0.0.0.0", port=5200, debug=False)
