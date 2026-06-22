"""Watson dashboard — port 5200, Tailscale-only."""
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, Response, g, jsonify, render_template, request, session, stream_with_context
from flask_cors import CORS
from jobs.people.api import congregation_list, people_create, people_delete, people_list, people_update
from config.settings import WATSON_SYSTEM


DB = os.path.expanduser("~/watson/data/watson.db")
SKILLS_FILE = Path(__file__).resolve().parents[2] / "memory" / "skills.json"
MEMORY = Path(__file__).resolve().parents[2] / "memory"
app = Flask(__name__, static_folder='static', template_folder='templates')
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    log.warning("FLASK_SECRET_KEY is not set — using insecure default. Set it in .env.")
    _secret_key = "watson-dashboard-secret"
app.secret_key = _secret_key
CORS(app)


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
    try:
        c.execute("ALTER TABLE reading_list ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
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
    c.execute("""CREATE TABLE IF NOT EXISTS memory_sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        summary    TEXT    NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS routing_corrections (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        original_message TEXT    NOT NULL,
        detected_intent  TEXT,
        correct_intent   TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.commit()
    c.close()


_bootstrap()

from jobs.writing_room.api import writing_room_bp
from jobs.writing_room import bootstrap_db as _wr_bootstrap
_wr_bootstrap()
app.register_blueprint(writing_room_bp)

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

_DASH_CONF_AFFIRM = frozenset({"yes", "yep", "yeah", "confirm", "go"})
_DASH_CONF_DENY   = frozenset({"no", "nope", "cancel", "stop"})


def _log_routing_correction_db(original_message: str, detected_intent: str) -> None:
    try:
        _db().execute(
            "INSERT INTO routing_corrections (original_message, detected_intent, correct_intent) VALUES (?, ?, 'cancelled_by_user')",
            (original_message, detected_intent),
        )
        _db().commit()
    except Exception as exc:
        log.error("Correction log failed: %s", exc)


def _dash_skill_description(slug: str, message: str = "") -> str:
    _map = {
        "add_task":         f"add a task: '{message}'" if message else "add a task",
        "bible_lookup":     "look up a Bible verse",
        "command_executor": "run a shell command",
        "contacts_lookup":  "search contacts",
        "pastoral_search":  "search pastoral notes",
        "book_appointment": "book an appointment",
        "kb":               "search the knowledge base",
        "kb_export":        "export knowledge base files",
        "web_search":       f"search the web for '{message}'" if message else "search the web",
        "image_search":     "search for an image",
        "email_send":       "draft and send an email",
        "summarizer":       "summarize this text",
        "dad_joke":         "tell a dad joke",
        "riddle":           "give a riddle",
    }
    return _map.get(slug, f"run the {slug.replace('_', ' ')} skill")

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


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({"current_time": datetime.now().isoformat()})


_TERM_BLOCKLIST = ("rm ", "sudo rm", "drop ", "drop;", "format ", "shutdown", "reboot", ":(){", ">(")

_TERM_COMMANDS = {
    "system status": ("skill", "jobs.dev.system_monitor"),
    "check logs": ("shell", "tail -50 " + os.path.expanduser("~/watson/logs/watson.log")),
    "disk usage": ("shell", "df -h"),
    "memory usage": ("shell", "free -h"),
    "git pull": ("shell", "git -C " + os.path.expanduser("~/watson") + " pull"),
    "restart watson bot": ("shell", "sudo systemctl restart watson-bot.service"),
    "restart dashboard": ("shell", "sudo systemctl restart watson-dashboard.service"),
    "count congregation members": ("sqlite", "congregation"),
    "count tasks": ("sqlite", "tasks"),
    "count connect cards": ("sqlite", "connect_cards"),
    "watson audit skills": ("skill", "jobs.dev.skill_tester"),
    "watson fix all failing skills": ("fix_skills", None),
}


@app.route("/api/terminal", methods=["POST"])
def terminal():
    import subprocess as _sp
    import sqlite3 as _sq

    data = request.get_json(force=True) or {}
    cmd = (data.get("command") or "").strip()
    if not cmd:
        return jsonify({"output": "No command provided.", "success": False})

    cmd_lower = cmd.lower()
    for blocked in _TERM_BLOCKLIST:
        if blocked in cmd_lower:
            return jsonify({"output": f"Blocked: '{blocked}' is not allowed.", "success": False})

    output = ""
    success = True
    entry = _TERM_COMMANDS.get(cmd_lower)

    if entry:
        kind, target = entry

        if kind == "shell":
            try:
                result = _sp.run(
                    target, shell=True, capture_output=True, text=True, timeout=30
                )
                output = (result.stdout or "") + (result.stderr or "")
                success = result.returncode == 0
            except _sp.TimeoutExpired:
                output = "Command timed out after 30 seconds."
                success = False
            except Exception as exc:
                output = str(exc)
                success = False

        elif kind == "skill":
            try:
                import importlib
                mod = importlib.import_module(target)
                output = str(mod.run())
            except Exception as exc:
                output = f"Skill error: {exc}"
                success = False

        elif kind == "sqlite":
            try:
                db_path = os.path.expanduser("~/watson/data/watson.db")
                with _sq.connect(db_path) as _c:
                    if target == "congregation":
                        try:
                            row = _c.execute("SELECT COUNT(*) FROM congregation").fetchone()
                            output = f"Congregation members: {row[0]}"
                        except Exception:
                            db2 = os.path.expanduser("~/watson/data/congregation.db")
                            with _sq.connect(db2) as _c2:
                                row = _c2.execute("SELECT COUNT(*) FROM congregation").fetchone()
                                output = f"Congregation members: {row[0]}"
                    elif target == "tasks":
                        row = _c.execute("SELECT COUNT(*) FROM tasks WHERE status='active'").fetchone()
                        output = f"Active tasks: {row[0]}"
                    elif target == "connect_cards":
                        row = _c.execute("SELECT COUNT(*) FROM connect_cards").fetchone()
                        output = f"Connect cards: {row[0]}"
            except Exception as exc:
                output = f"DB error: {exc}"
                success = False

        elif kind == "fix_skills":
            try:
                from jobs.dev.skill_tester import run_all_skill_tests
                results = run_all_skill_tests()
                failed = results["failed"] + results["errors"]
                if not failed:
                    output = "No failing skills found."
                else:
                    slugs = [r["slug"] for r in failed]
                    output = f"Queued {len(slugs)} failing skill(s) for fix: " + ", ".join(slugs)
            except Exception as exc:
                output = f"Error: {exc}"
                success = False

    elif True:  # route all commands through skill system
        try:
            from jobs.skillbuilder import router as _router
            route_result = _router.route(cmd, "dashboard")
            if route_result.get("action") == "skill":
                slug = route_result.get("slug", "")
                skills = _router._load_skills("dashboard")
                skill = next((s for s in skills if s["slug"] == slug), None)
                if skill:
                    output = str(_router._run_skill(skill, message=route_result.get("message")))
                else:
                    output = f"Skill '{slug}' not found."
            else:
                output = route_result.get("message") or "No result."
        except Exception as exc:
            output = f"Error: {exc}"
            success = False
    else:
        output = "Unknown command. Use the buttons above or prefix with 'Watson '."
        success = False

    output = output.strip() or "(no output)"
    if len(output) > 500:
        _send_telegram(f"Watson Terminal output:\n\n{output[:3000]}")
        output += "\n\n[Full output sent to Telegram]"

    return jsonify({"output": output, "success": success})


@app.route("/api/pending")
def pending_items():
    items = []
    db = _db()

    try:
        rows = db.execute(
            "SELECT id, subject as title, sender as subtitle, 'EMAIL' as type "
            "FROM email_reply WHERE status='awaiting_approval'"
        ).fetchall()
        for r in rows:
            items.append({"type": r["type"], "title": r["title"], "subtitle": r["subtitle"]})
    except Exception:
        pass

    try:
        rows = db.execute(
            "SELECT id, appointment_title as title, appointment_time as subtitle, 'NOTE' as type "
            "FROM notes_pending WHERE status='pending'"
        ).fetchall()
        for r in rows:
            items.append({"type": r["type"], "title": r["title"], "subtitle": r["subtitle"]})
    except Exception:
        pass

    try:
        rows = db.execute(
            "SELECT id, title, 'Awaiting confirm' as subtitle, 'BUILD' as type "
            "FROM tasks WHERE status='awaiting_confirm'"
        ).fetchall()
        for r in rows:
            items.append({"type": r["type"], "title": r["title"], "subtitle": r["subtitle"]})
    except Exception:
        pass

    return jsonify(items)


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
        sentences = re.split(r'(?<=[.!?])\s+', (row['summary'] or '').strip())
        excerpt = ' '.join(sentences[:2])
        draft = f"{row['title']}\n\n{excerpt}\n\n{row['url']}\n\n#Apologetics #Theology #Faith"
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
        "SELECT * FROM tasks WHERE status = 'active' ORDER BY sort_order ASC, created_at DESC"
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


# ── People API (Watson contacts) ──────────────────────────────────────────────

@app.route("/api/people")
def people_list_api():
    rows = _db().execute(
        "SELECT id, name, email, phone, info, relationship, notes, carrier "
        "FROM people ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/people", methods=["POST"])
def people_create_api():
    result = people_create(request.get_json(force=True))
    return jsonify(result), (400 if "error" in result else 201)


@app.route("/api/people/<int:person_id>", methods=["PATCH"])
def people_update_api(person_id):
    return jsonify(people_update(person_id, request.get_json(force=True)))


@app.route("/api/people/<int:person_id>", methods=["DELETE"])
def people_delete_api(person_id):
    return jsonify(people_delete(person_id))


# ── Congregation API (read-only) ───────────────────────────────────────────────

@app.route("/api/congregation")
def congregation_list_api():
    q = (request.args.get("q") or "").strip()
    if q:
        rows = _db().execute(
            "SELECT id, name, email, campus FROM congregation "
            "WHERE name LIKE ? COLLATE NOCASE ORDER BY name COLLATE NOCASE LIMIT 30",
            (f"%{q}%",),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    return jsonify(congregation_list())


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


@app.route("/api/reading/<int:entry_id>/reorder", methods=["PATCH"])
def reading_reorder(entry_id):
    data = request.get_json(force=True) or {}
    sort_order = data.get("sort_order")
    if sort_order is None:
        return jsonify({"error": "sort_order required"}), 400
    _db().execute("UPDATE reading_list SET sort_order = ? WHERE id = ?", (sort_order, entry_id))
    _db().commit()
    row = _db().execute("SELECT * FROM reading_list WHERE id = ?", (entry_id,)).fetchone()
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
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        skills = data.get("skills", data) if isinstance(data, dict) else data
        if not isinstance(skills, list):
            return jsonify([])
        for s in skills:
            if "status" not in s:
                s["status"] = "ready"
            if "category" not in s:
                s["category"] = "Utilities"
        skills.sort(key=lambda s: (s.get("name") or s.get("slug") or "").lower())
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
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        skills = data.get("skills", data) if isinstance(data, dict) else data
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


# ── Memory API ───────────────────────────────────────────────────────────────

@app.route("/api/memory/recent")
def memory_recent():
    rows = _db().execute(
        "SELECT summary FROM memory_sessions ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return jsonify([r["summary"] for r in rows])


@app.route("/api/chat/summarize", methods=["POST"])
def chat_summarize():
    import requests as _sreq
    data = request.get_json(force=True) or {}
    history = data.get("history") or []
    msgs = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
    if len(msgs) < 2:
        return jsonify({"ok": True, "skipped": True})
    convo = "\n".join(f"{m['role'].title()}: {m['content']}" for m in msgs)
    prompt = (
        "Summarize this conversation in 3-5 sentences. Focus on topics discussed, decisions made, "
        "tasks mentioned, and anything Dr. Bill said about himself, his ministry, or his plans. "
        "Be specific and factual. No preamble. "
        "Important: The person in this conversation is Dr. William C.K. Yomes. Use his full name accurately. Do not substitute or confuse him with any other person.\n\n" + convo
    )
    try:
        resp = _sreq.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        summary = (resp.json().get("response") or "").strip()
    except Exception as exc:
        log.error("chat summarize failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
    if summary:
        _db().execute("INSERT INTO memory_sessions (summary) VALUES (?)", (summary,))
        _db().commit()
    return jsonify({"ok": True})


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
    memory_context = (data.get("memory_context") or "").strip()

    def _sse(text):
        lines = str(text).split('\n')
        return '\n'.join(f'data: {line}' for line in lines) + '\n\n'

    def _stream_simple(text):
        yield _sse(text)
        yield "data: [DONE]\n\n"

    def _stream_error(msg):
        yield f"data: [ERROR] {msg}\n\n"

    def _emit_status(text):
        return f'data: {json.dumps({"type": "status", "text": text})}\n\n'

    def _sse_response(gen):
        r = Response(stream_with_context(gen), mimetype="text/event-stream")
        r.headers['Cache-Control'] = 'no-cache'
        r.headers['X-Accel-Buffering'] = 'no'
        return r

    if not message:
        return _sse_response(_stream_error("message required"))

    msg_lower = message.lower().strip()
    import re as _re

    # Dashboard confirmation gate — check for a pending skill confirmation before routing
    from jobs.telegram.pending import get_pending_confirmation, mark_pending_status
    _dash_conf = get_pending_confirmation()
    if _dash_conf and _dash_conf["payload"].get("source") == "dashboard":
        _stored = _dash_conf["payload"]
        if msg_lower in _DASH_CONF_AFFIRM:
            mark_pending_status(_dash_conf["id"], "confirmed")
            _action = _stored.get("action_type")
            if _action == "skill":
                _conf_slug  = _stored.get("slug", "")
                _prefetched = _stored.get("prefetched_result")
                if _prefetched:
                    return _sse_response(_stream_simple("✓ " + _prefetched))
                _conf_msg = _stored.get("original_message", message)
                _skills = _router._load_skills("dashboard")
                _skill  = next((s for s in _skills if s["slug"] == _conf_slug), None)
                if _skill:
                    try:
                        _res = _router._run_skill(_skill, message=_conf_msg)
                        return _sse_response(_stream_simple("✓ " + str(_res)))
                    except Exception as _exc:
                        return _sse_response(_stream_simple(f"Skill error: {_exc}"))
                else:
                    return _sse_response(_stream_simple(f"Skill '{_conf_slug}' not available."))
            elif _action == "sms_me":
                from jobs.sms.sms_send import send_sms as _send_sms_direct_conf
                _op = os.getenv("WATSON_OWNER_PHONE")
                _oc = os.getenv("WATSON_OWNER_CARRIER", "verizon")
                if _op:
                    _r = _send_sms_direct_conf("Dr. Bill", _op, _oc, _stored["sms_message"])
                    _reply = "Text sent to you." if _r["success"] else f"Failed: {_r['error']}"
                else:
                    _reply = "WATSON_OWNER_PHONE not set in .env."
                return _sse_response(_stream_simple(_reply))
            elif _action == "sms_contact":
                from jobs.sms.sms_send import send_sms_to_contact as _sms_conf_send
                from jobs.people.lookup import lookup_member as _lm_conf
                _chits = _lm_conf(_stored.get("sms_name", ""))
                _cc = next((c for c in _chits if c.get("phone")), None)
                if _cc:
                    _r = _sms_conf_send(_cc, _stored["sms_message"])
                    _reply = f"Text sent to {_cc['name']}." if _r["success"] else f"Failed: {_r['error']}"
                else:
                    _reply = f"No contact found for '{_stored.get('sms_name', '')}'."
                return _sse_response(_stream_simple(_reply))
            else:
                return _sse_response(_stream_simple("Couldn't execute — action not recognized."))
        elif msg_lower in _DASH_CONF_DENY:
            mark_pending_status(_dash_conf["id"], "cancelled")
            _log_routing_correction_db(_stored.get("original_message", ""), _dash_conf["type"])
            return _sse_response(_stream_simple("Got it, cancelled."))
        else:
            # Not yes/no — discard pending, fall through and reprocess
            mark_pending_status(_dash_conf["id"], "cancelled")

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

    if msg_lower.startswith('debug:'):
        from jobs.dev.claude_debug import run
        result = run(message)
        return _sse_response(_stream_simple(str(result)))

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
                    yield _emit_status("→ Generating QR code...")
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
            from jobs.telegram.pending import store_skill_confirmation as _store_dash_sms
            _store_dash_sms("sms_me", {
                "source": "dashboard",
                "action_type": "sms_me",
                "original_message": message,
                "sms_message": _sms_msg_raw,
            })
            return _sse_response(_stream_simple(
                f"Just to confirm — you want me to text you: '{_sms_msg_raw}'. Reply yes to proceed or no to cancel."
            ))

        elif _sms_pattern:
            _contact_raw = _sms_pattern.group(1).strip()
            _sms_msg_start = _sms_pattern.start(2)
            _sms_message = message[_sms_msg_start:].strip()
            _recent_qr = _db().execute(
                "SELECT content FROM qr_cache ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if 'qr' in msg_lower and _recent_qr:
                _sms_message = f"QR code: {_recent_qr['content']}"
            from jobs.telegram.pending import store_skill_confirmation as _store_dash_sms
            _store_dash_sms("sms_contact", {
                "source": "dashboard",
                "action_type": "sms_contact",
                "original_message": message,
                "sms_name": _contact_raw,
                "sms_message": _sms_message,
            })
            return _sse_response(_stream_simple(
                f"Just to confirm — you want me to text {_contact_raw}: '{_sms_message}'. Reply yes to proceed or no to cancel."
            ))

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
    # KB search pre-check — must fire before conversational/factual intercepts
    _kb_triggers = ("search kb", "search my notes", "search my sermons", "what have i said about", "what did i preach on", "find in my notes", "look in my sermons", "kb search", "search my kb", "summarize my")
    if any(t in message.lower() for t in _kb_triggers):
        from jobs.skills.kb_search import run as _kb_run
        def _kb_stream():
            yield _emit_status("→ Searching your notes...")
            try:
                result = _kb_run(message)
                yield _sse(result)
            except Exception as exc:
                yield _sse(f"KB search failed: {exc}")
            yield "data: [DONE]\n\n"
        return _sse_response(_kb_stream())
    _identity = _router._is_identity_query(message)
    _factual = _router._is_factual_query(message)
    _conv = _router._is_conversational(message)
    log.info("ROUTE msg=%r identity=%s factual=%s conversational=%s", message[:120], _identity, _factual, _conv)

    # 0. Identity questions go straight to Ollama
    if _identity:
        route_result = {"action": "chat"}
    # 1. Factual queries go directly to web search, bypassing Ollama
    elif _factual:
        def _web_search_stream():
            yield _emit_status("→ Searching the web...")
            from jobs.research.web_search import run as web_search_run
            ws_result = web_search_run(message)
            yield _sse("✓ " + ws_result)
            yield "data: [DONE]\n\n"
        return _sse_response(_web_search_stream())
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
        _slug = route_result.get("slug", "unknown")
        # Execute the skill now to check for special flows (e.g. email draft confirmation UI)
        if "result" not in route_result:
            _skills = _router._load_skills("dashboard")
            _skill  = next((s for s in _skills if s["slug"] == _slug), None)
            if _skill:
                try:
                    route_result["result"] = _router._run_skill(
                        _skill, message=route_result.get("message")
                    )
                except Exception as exc:
                    log.error("Skill execution failed for %s: %s", _slug, exc)
                    route_result["result"] = f"Skill failed: {exc}"
            else:
                log.warning("Skill '%s' not found in registry — falling through to chat", _slug)
        if "result" in route_result:
            _result = route_result["result"]
            # Email draft has its own rich confirmation UI — pass through unchanged
            if isinstance(_result, dict) and _result.get("confirm"):
                _sk_status = f"→ Running {_slug.replace('_', ' ')}..."
                session["pending_email"] = _result
                _ctext = f"I found {_result['to_name']} at {_result['to_email']}. Confirm below to send."
                _cjson = json.dumps({
                    "to_name": _result["to_name"],
                    "to_email": _result["to_email"],
                    "subject":  _result["subject"],
                    "body":     _result["body"],
                })
                def _email_gen(t=_ctext, cj=_cjson, st=_sk_status):
                    yield _emit_status(st)
                    yield _sse(t)
                    yield f"data: [CONFIRM_EMAIL]{cj}\n\n"
                    yield "data: [DONE]\n\n"
                return _sse_response(_email_gen())
            # All other skills — confirmation gate before returning result
            _desc = _dash_skill_description(_slug, message)
            from jobs.telegram.pending import store_skill_confirmation as _store_skill_dash
            _store_skill_dash(_slug, {
                "source": "dashboard",
                "action_type": "skill",
                "slug": _slug,
                "original_message": message,
                "prefetched_result": str(_result),
            })
            return _sse_response(_stream_simple(
                f"Just to confirm — you want me to {_desc}. Reply yes to proceed or no to cancel."
            ))

    if route_result["action"] == "build":
        import threading
        threading.Thread(
            target=_router._build_in_background,
            args=(route_result["description"], route_result["job_path"], "dashboard"),
            daemon=True,
        ).start()
        return _sse_response(_stream_simple("Building that skill now. I'll notify you via Telegram when it's ready."))

    if route_result["action"] == "propose":
        route_result = {"action": "chat"}

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

    # ── LLM inference — Claude (primary) with Ollama fallback ────────────────
    # Build message history — last 20 from DB session if available
    if session_id:
        _db_rows = _db().execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        messages = [
            {"role": r["role"], "content": r["content"]}
            for r in _db_rows[-20:]
            if r["role"] in ("user", "assistant") and r["content"]
        ]
        if not messages or messages[-1].get("content") != message:
            messages.append({"role": "user", "content": message})
    else:
        messages = []
        for h in history[-20:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

    # Inject project memory into system prompt if a project is active
    _proj_mem_path = None
    _proj_mem_contents = ""
    if project_slug:
        _proj_mem_path = Path(os.path.expanduser(
            f"~/watson/memory/projects/{project_slug}/{project_slug}.md"
        ))
        if _proj_mem_path.exists():
            try:
                _proj_mem_contents = _proj_mem_path.read_text(encoding="utf-8")
            except Exception:
                _proj_mem_contents = ""
    _base = WATSON_SYSTEM
    if _proj_mem_contents:
        _base = f"PROJECT CONTEXT:\n{_proj_mem_contents}\n\n---\n\n{_base}"
    _system = f"{memory_context}\n\n---\n\n{_base}" if memory_context else _base

    def _update_project_memory(slug, mem_path, current_mem, user_msg, reply):
        try:
            import requests as _mreq
            prompt = (
                "You are Watson, Dr. Bill's AI assistant. Based on this exchange, update the project memory file. "
                "Return ONLY the updated markdown file contents, nothing else.\n\n"
                f"Current memory:\n{current_mem}\n\n"
                f"New exchange:\nDr. Bill: {user_msg}\nWatson: {reply}\n\n"
                "Return the complete updated memory file, preserving all existing sections and updating "
                "Current State and Next Steps as appropriate."
            )
            _r = _mreq.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
                timeout=60,
            )
            _r.raise_for_status()
            updated = (_r.json().get("response") or "").strip()
            if updated:
                mem_path.parent.mkdir(parents=True, exist_ok=True)
                mem_path.write_text(updated, encoding="utf-8")
        except Exception as _exc:
            log.error("project memory update failed for %s: %s", slug, _exc)

    def _save_reply(reply_text):
        if not session_id or not reply_text:
            return
        try:
            with sqlite3.connect(DB) as _c:
                _c.execute(
                    "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'assistant', ?)",
                    (session_id, reply_text),
                )
                _c.execute(
                    "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                    (session_id,),
                )
        except Exception as _exc:
            log.error("Failed to save assistant reply to DB: %s", _exc)

    def _stream_ollama_fallback(msgs=messages, sys=_system):
        import threading
        full_reply = []
        first_token = True
        try:
            ollama_msgs = [{"role": "system", "content": sys}] + list(msgs)
            resp = _req.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "llama3.2:3b",
                    "messages": ollama_msgs,
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
                    if first_token:
                        yield _emit_status("→ Response streaming...")
                        first_token = False
                    full_reply.append(token)
                    yield _sse(token)
                if chunk.get("done"):
                    break
            yield "data: [DONE]\n\n"
        except Exception:
            yield "data: [ERROR] Watson timed out. Try again.\n\n"
            return
        reply_text = "".join(full_reply)
        _save_reply(reply_text)
        if project_slug and _proj_mem_path:
            threading.Thread(
                target=_update_project_memory,
                args=(project_slug, _proj_mem_path, _proj_mem_contents, message, reply_text),
                daemon=True,
            ).start()

    def _stream_claude(msgs=messages, sys=_system):
        import anthropic
        import threading
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set — falling back to Ollama")
            yield from _stream_ollama_fallback(msgs, sys)
            return
        full_reply = []
        yield _emit_status("→ Thinking...")
        first_token = True
        try:
            client = anthropic.Anthropic(api_key=api_key)
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=sys,
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        if first_token:
                            yield _emit_status("→ Response streaming...")
                            first_token = False
                        full_reply.append(text)
                        yield _sse(text)
            yield "data: [DONE]\n\n"
        except Exception as exc:
            log.warning("Claude API failed (%s) — falling back to Ollama", exc)
            yield from _stream_ollama_fallback(msgs, sys)
            return
        reply_text = "".join(full_reply)
        _save_reply(reply_text)
        if project_slug and _proj_mem_path:
            threading.Thread(
                target=_update_project_memory,
                args=(project_slug, _proj_mem_path, _proj_mem_contents, message, reply_text),
                daemon=True,
            ).start()

    return _sse_response(_stream_claude())


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

        if msg_lower.startswith('debug:'):
            from jobs.dev.claude_debug import run
            result = run(message)
            return _reply(str(result))

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
                    "messages": [{"role": "system", "content": WATSON_SYSTEM}, {"role": "user", "content": msg}],
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
        _body = {"model": "llama3.2:3b", "messages": [{"role": "system", "content": WATSON_SYSTEM}] + messages, "stream": True, "num_predict": 300}
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

@app.route("/api/book-appointment", methods=["POST"])
def book_appointment():
    data = request.get_json(silent=True) or {}
    confirmation_id = data.get("confirmation_id", "").strip()
    event_id = data.get("event_id", "").strip()
    guest_name = data.get("guest_name", "").strip()
    guest_email = data.get("guest_email", "").strip()
    appointment_type = data.get("appointment_type", "").strip()
    scheduled_at = data.get("scheduled_at", "").strip()

    if not confirmation_id or not event_id or not guest_name or not guest_email:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO appointment_bookings
               (confirmation_id, event_id, guest_name, guest_email, appointment_type, scheduled_at, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'confirmed', datetime('now'))""",
            (confirmation_id, event_id, guest_name, guest_email, appointment_type, scheduled_at)
        )
    return jsonify({"ok": True})


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


# ── Dashboard prefs ───────────────────────────────────────────────────────────

_PREFS_PATH = os.path.expanduser("~/watson/data/dashboard_prefs.json")


@app.route("/api/prefs", methods=["GET"])
def prefs_get():
    try:
        if not os.path.exists(_PREFS_PATH):
            return jsonify({"menu_order": []})
        with open(_PREFS_PATH) as f:
            return jsonify(json.load(f))
    except Exception as exc:
        log.error("prefs GET failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/prefs", methods=["POST"])
def prefs_post():
    try:
        data = request.get_json(force=True) or {}
        os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
        with open(_PREFS_PATH, "w") as f:
            json.dump(data, f)
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("prefs POST failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Shepherding report ────────────────────────────────────────────────────────

@app.route("/api/shepherding/run")
def shepherding_run():
    from jobs.connect_cards.shepherding_report import telegram_shepherding_summary
    try:
        summary = telegram_shepherding_summary()
        return jsonify({"summary": summary})
    except Exception as exc:
        log.error("shepherding/run failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/shepherding/email", methods=["POST"])
def shepherding_email():
    from jobs.connect_cards.shepherding_report import send_shepherding_report
    try:
        send_shepherding_report()
        return jsonify({"message": "Shepherding report sent to your email ✓"})
    except Exception as exc:
        log.error("shepherding/email failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/shepherding/report")
def shepherding_report():
    import re as _re2
    from jobs.connect_cards.shepherding_report import generate_shepherding_report
    try:
        _, html = generate_shepherding_report()
        body_match = _re2.search(r'<body[^>]*>(.*?)</body>', html, _re2.DOTALL)
        body = body_match.group(1) if body_match else html
        return jsonify({"html": body})
    except Exception as exc:
        log.error("shepherding/report failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/shepherding/exempt", methods=["POST"])
def shepherding_exempt():
    CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    if not member_id:
        return jsonify({"error": "member_id required"}), 400
    try:
        conn = sqlite3.connect(CONG_DB)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE members SET shepherding_exempt = 1 WHERE id = ?", (member_id,)
        )
        conn.commit()
        row = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
        conn.close()
        name = row["name"] if row else str(member_id)
        return jsonify({"ok": True, "name": name})
    except Exception as exc:
        log.error("shepherding/exempt failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/shepherding/checkin", methods=["POST"])
def shepherding_checkin():
    from datetime import timedelta
    CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    if not member_id:
        return jsonify({"error": "member_id required"}), 400
    try:
        today = _date.today()
        # Sunday = weekday 6; roll back to most recent Sunday
        days_back = (today.weekday() + 1) % 7
        prev_sunday = (today - timedelta(days=days_back)).isoformat()

        conn = sqlite3.connect(CONG_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, campus_preference FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "member not found"}), 404
        name   = row["name"]
        campus = row["campus_preference"] or "Wilmington"
        conn.execute(
            "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, NULL)",
            (member_id, prev_sunday, campus),
        )
        conn.commit()
        conn.close()

        from jobs.connect_cards.shepherding_report import _fmt_date
        return jsonify({"ok": True, "name": name, "date": _fmt_date(prev_sunday)})
    except Exception as exc:
        log.error("shepherding/checkin failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Data audit ────────────────────────────────────────────────────────────────

@app.route("/api/audit/run", methods=["POST"])
def audit_run():
    try:
        from jobs.connect_cards.data_audit import find_likely_duplicates, find_data_inconsistencies
        return jsonify({
            "duplicates":      find_likely_duplicates(),
            "inconsistencies": find_data_inconsistencies(),
        })
    except Exception as exc:
        log.error("audit/run failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/audit/merge", methods=["POST"])
def audit_merge():
    try:
        from jobs.connect_cards.data_audit import merge_members, update_member_field
        data      = request.get_json(force=True) or {}
        winner_id = data.get("winner_id")
        loser_id  = data.get("loser_id")
        if not winner_id or not loser_id:
            return jsonify({"error": "winner_id and loser_id required"}), 400
        field_choices = data.get("field_choices", {})
        a_id = data.get("a_id", winner_id)
        b_id = data.get("b_id", loser_id)

        CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
        conn = sqlite3.connect(CONG_DB)
        conn.row_factory = sqlite3.Row
        loser_row = conn.execute("SELECT * FROM members WHERE id = ?", (loser_id,)).fetchone()
        conn.close()

        for field, choice in field_choices.items():
            chosen_id = a_id if choice == "a" else b_id
            if chosen_id == loser_id and loser_row:
                update_member_field(int(winner_id), field, loser_row[field] or "")

        return jsonify(merge_members(int(winner_id), int(loser_id)))
    except Exception as exc:
        log.error("audit/merge failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/audit/keep-separate", methods=["POST"])
def audit_keep_separate():
    try:
        data = request.get_json(force=True) or {}
        a_id = data.get("member_a_id")
        b_id = data.get("member_b_id")
        if not a_id or not b_id:
            return jsonify({"error": "member_a_id and member_b_id required"}), 400
        lo, hi = sorted([int(a_id), int(b_id)])
        CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
        conn = sqlite3.connect(CONG_DB)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO audit_exemptions (member_a_id, member_b_id) VALUES (?, ?)",
                (lo, hi),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("audit/keep-separate failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/audit/correct-field", methods=["POST"])
def audit_correct_field():
    try:
        from jobs.connect_cards.data_audit import update_member_field
        data      = request.get_json(force=True) or {}
        member_id = data.get("member_id")
        field     = data.get("field")
        value     = data.get("value", "")
        if not member_id or not field:
            return jsonify({"error": "member_id and field required"}), 400
        return jsonify(update_member_field(int(member_id), field, value))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.error("audit/correct-field failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Reports ───────────────────────────────────────────────────────────────────

@app.route("/api/reports/run")
def reports_run():
    report_type = request.args.get("type", "").strip()
    weeks       = request.args.get("weeks", 4, type=int)
    if not report_type:
        return jsonify({"error": "type required"}), 400
    try:
        if report_type == "shepherding":
            from jobs.connect_cards.shepherding_report import telegram_shepherding_summary
            content = telegram_shepherding_summary()
        else:
            content = f"[{report_type.replace('_', ' ').title()} — last {weeks} weeks]\n\nReport generation for this type is not yet implemented."
        return jsonify({"type": report_type, "weeks": weeks, "content": content})
    except Exception as exc:
        log.error("reports/run failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reports/telegram", methods=["POST"])
def reports_telegram():
    data    = request.get_json(force=True) or {}
    rtype   = data.get("type", "report")
    weeks   = data.get("weeks", "")
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "content required"}), 400
    try:
        label = rtype.replace("_", " ").title()
        header = f"*{label}*" + (f" — {weeks}w" if weeks else "")
        _send_telegram(f"{header}\n\n{content}")
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("reports/telegram failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reports/email", methods=["POST"])
def reports_email():
    import smtplib
    from email.mime.text import MIMEText
    data    = request.get_json(force=True) or {}
    rtype   = data.get("type", "report")
    weeks   = data.get("weeks", "")
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "content required"}), 400
    try:
        smtp_host = os.getenv("WATSON_SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("WATSON_SMTP_PORT", 587))
        smtp_user = os.getenv("WATSON_GMAIL_ADDRESS")
        smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD")
        from_addr = os.getenv("WATSON_FROM_ADDRESS", smtp_user)
        to_addr   = "bill.yomes@gmail.com"
        label     = rtype.replace("_", " ").title()
        subject   = f"Watson Report: {label}" + (f" ({weeks}w)" if weeks else "")
        msg = MIMEText(content)
        msg["Subject"] = subject
        msg["From"]    = f"Watson <{from_addr}>"
        msg["To"]      = to_addr
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("reports/email failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/shepherding/telegram", methods=["POST"])
def shepherding_telegram():
    from jobs.connect_cards.shepherding_report import telegram_shepherding_summary
    try:
        summary = telegram_shepherding_summary()
        _send_telegram(summary)
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("shepherding/telegram failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Sessions ─────────────────────────────────────────────────────────────────

@app.route("/api/sessions", methods=["POST"])
def sessions_create():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "New Conversation").strip()
    project_slug = (data.get("project_slug") or "").strip() or None
    source = (data.get("source") or "voice").strip()
    db = _db()
    cur = db.execute(
        "INSERT INTO chat_sessions (title, project_slug) VALUES (?, ?)",
        (title, project_slug),
    )
    db.commit()
    row = dict(db.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (cur.lastrowid,)
    ).fetchone())
    return jsonify(row), 201


@app.route("/api/sessions", methods=["GET"])
def sessions_list():
    db = _db()
    rows = db.execute(
        "SELECT s.id, s.title, s.project_slug, s.created_at, s.ended_at, "
        "COUNT(m.id) as message_count "
        "FROM chat_sessions s "
        "LEFT JOIN chat_messages m ON m.session_id = s.id "
        "GROUP BY s.id ORDER BY s.created_at DESC LIMIT 100"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sessions/<int:session_id>", methods=["GET"])
def sessions_get(session_id):
    db = _db()
    row = db.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    messages = db.execute(
        "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return jsonify({"session": dict(row), "messages": [dict(m) for m in messages]})


@app.route("/api/sessions/<int:session_id>/messages", methods=["POST"])
def sessions_message_add(session_id):
    db = _db()
    row = db.execute(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(force=True) or {}
    role = (data.get("role") or "user").strip()
    content = (data.get("content") or "").strip()
    source = (data.get("source") or "voice").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    cur = db.execute(
        "INSERT INTO chat_messages (session_id, role, content, source) VALUES (?, ?, ?, ?)",
        (session_id, role, content, source),
    )
    db.execute(
        "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "ok": True})


@app.route("/api/sessions/<int:session_id>/title", methods=["PATCH"])
def sessions_title_update(session_id):
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    db = _db()
    db.execute(
        "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
        (title, session_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:session_id>/close", methods=["POST"])
def sessions_close(session_id):
    import requests as _req
    db = _db()
    row = db.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    db.execute(
        "UPDATE chat_sessions SET ended_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    db.commit()

    messages = db.execute(
        "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()

    history_dir = Path(os.path.expanduser("~/watson/data/history"))
    history_dir.mkdir(parents=True, exist_ok=True)
    session_data = dict(row)
    md_lines = [
        f"# {session_data['title']}",
        f"Session ID: {session_id}",
        f"Started: {session_data['created_at']}",
        f"Project: {session_data['project_slug'] or 'None'}",
        "",
    ]
    for m in messages:
        label = "Bill" if m["role"] == "user" else "Watson"
        md_lines.append(f"**{label}** ({m['created_at'][:16]})")
        md_lines.append(m["content"])
        md_lines.append("")

    md_path = history_dir / f"{session_id}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    suggested_slug = None
    if not session_data.get("project_slug") and messages:
        conversation_text = " ".join(m["content"] for m in messages[:10])
        projects = _parse_projects_index()
        project_names = [f"{p.get('slug','')}: {p.get('name','')}" for p in projects]
        if project_names:
            try:
                detect_resp = _req.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "qwen2.5:7b",
                        "prompt": (
                            f"Given this conversation excerpt, which project does it most likely belong to?\n\n"
                            f"Projects: {', '.join(project_names)}\n\n"
                            f"Conversation: {conversation_text[:500]}\n\n"
                            f"Reply with only the project slug, or 'none' if no clear match."
                        ),
                        "stream": False,
                    },
                    timeout=15,
                )
                detected = detect_resp.json().get("response", "none").strip().lower().split()[0]
                valid_slugs = [p.get("slug", "") for p in projects]
                if detected in valid_slugs:
                    suggested_slug = detected
            except Exception as exc:
                log.warning("Project auto-detect failed: %s", exc)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "markdown_path": str(md_path),
        "suggested_project_slug": suggested_slug,
    })


@app.route("/api/sessions/<int:session_id>/file", methods=["POST"])
def sessions_file(session_id):
    import subprocess as _sp
    data = request.get_json(force=True) or {}
    project_slug = (data.get("project_slug") or "").strip()
    project_name = (data.get("project_name") or "").strip()

    if not project_slug:
        return jsonify({"error": "project_slug required"}), 400

    db = _db()
    session_row = db.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session_row:
        return jsonify({"error": "session not found"}), 404

    project_dir = MEMORY / "projects" / project_slug
    if not project_dir.exists():
        if not project_name:
            return jsonify({"error": "project_name required for new project"}), 400
        try:
            from jobs.memory.new_project import create_project
            create_project(project_slug, project_name)
        except Exception as exc:
            return jsonify({"error": f"Failed to create project: {exc}"}), 500

    history_path = Path(os.path.expanduser(f"~/watson/data/history/{session_id}.md"))
    if not history_path.exists():
        messages = db.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        session_data = dict(session_row)
        md_lines = [
            f"# {session_data['title']}",
            f"Session ID: {session_id}",
            f"Started: {session_data['created_at']}",
            "",
        ]
        for m in messages:
            label = "Bill" if m["role"] == "user" else "Watson"
            md_lines.append(f"**{label}** ({m['created_at'][:16]})")
            md_lines.append(m["content"])
            md_lines.append("")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text("\n".join(md_lines), encoding="utf-8")

    notes_dir = project_dir / "notes"
    notes_dir.mkdir(exist_ok=True)
    date_str = session_row["created_at"][:10]
    dest = notes_dir / f"{date_str}-session-{session_id}.md"
    import shutil
    shutil.copy2(str(history_path), str(dest))

    db.execute(
        "UPDATE chat_sessions SET project_slug = ?, auto_filed = 1, updated_at = datetime('now') WHERE id = ?",
        (project_slug, session_id),
    )
    db.commit()

    try:
        _sp.run(["git", "add", str(dest)], cwd=str(MEMORY.parent), check=True)
        _sp.run(
            ["git", "commit", "-m", f"session({session_id}): filed under {project_slug}"],
            cwd=str(MEMORY.parent), check=True,
        )
    except Exception as exc:
        log.warning("Git commit for session file failed: %s", exc)

    _send_telegram(f"📁 Session '{session_row['title']}' filed under {project_slug}")

    return jsonify({"ok": True, "filed_to": project_slug, "note_file": dest.name})


@app.route("/api/voice", methods=["POST"])
def voice():
    import requests as _req
    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id")
    project_slug = (data.get("project_slug") or "").strip() or None
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "message required"}), 400

    project_context = ""
    if project_slug:
        mem_path = MEMORY / "projects" / project_slug / "memory.md"
        if mem_path.exists():
            project_context = mem_path.read_text(encoding="utf-8")[:2000]

    db = _db()
    if session_id:
        try:
            db.execute(
                "INSERT INTO chat_messages (session_id, role, content, source) VALUES (?, 'user', ?, 'voice')",
                (session_id, message),
            )
            db.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            db.commit()
        except Exception as exc:
            log.warning("Failed to persist user voice message: %s", exc)

    CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    if CLAUDE_API_KEY:
        try:
            import urllib.request, json as _json
            system = WATSON_SYSTEM
            if project_context:
                system += f"\n\nPROJECT CONTEXT:\n{project_context}"

            messages_payload = []
            for h in history[-6:]:
                if h.get("role") in ("user", "assistant") and h.get("content"):
                    messages_payload.append({"role": h["role"], "content": h["content"]})
            messages_payload.append({"role": "user", "content": message})

            payload = _json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "system": system,
                "messages": messages_payload,
            }).encode()

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
            reply = result["content"][0]["text"]
        except Exception as exc:
            log.error("Claude API voice call failed: %s", exc)
            reply = f"Claude API error: {exc}"
    else:
        messages_payload = []
        for h in history[-4:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages_payload.append({"role": h["role"], "content": h["content"]})
        messages_payload.append({"role": "user", "content": message})
        system = WATSON_SYSTEM
        if project_context:
            system += f"\n\nPROJECT CONTEXT:\n{project_context}"
        try:
            resp = _req.post(
                "http://localhost:11434/api/chat",
                json={"model": "llama3.2:3b", "messages": [{"role": "system", "content": system}] + messages_payload, "stream": True, "num_predict": 400},
                stream=True,
                timeout=30,
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
            reply = "".join(parts) or "No response."
        except Exception as exc:
            reply = f"Watson error: {exc}"

    if session_id:
        try:
            db.execute(
                "INSERT INTO chat_messages (session_id, role, content, source) VALUES (?, 'assistant', ?, 'voice')",
                (session_id, reply),
            )
            db.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            db.commit()
        except Exception as exc:
            log.warning("Failed to persist assistant voice message: %s", exc)

    return jsonify({"response": reply, "session_id": session_id})


# ── Blog Draft Submission ──────────────────────────────────────────────────────

@app.route('/api/submit-draft', methods=['POST'])
def submit_draft():
    data = request.get_json()
    slug = data.get('slug', '').strip()
    content = data.get('content', '').strip()

    if not slug or not content:
        return jsonify({'error': 'Missing slug or content'}), 400

    title = slug
    title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()

    body = re.sub(r'^---.*?---\s*', '', content, flags=re.DOTALL).strip()

    db = _db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO blog_drafts (slug, title, body, status) VALUES (?, ?, ?, 'pending')",
            (slug, title, body)
        )
        db.commit()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'ok': True, 'slug': slug}), 200


# ── Status API ────────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"current_time": datetime.now().isoformat()})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="0.0.0.0", port=5200, debug=False)
