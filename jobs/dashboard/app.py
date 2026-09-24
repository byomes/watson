"""Watson dashboard — port 5200, Tailscale-only."""
import concurrent.futures
import csv
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_file, session, stream_with_context, url_for
from flask_cors import CORS
from jobs.people.api import congregation_list, people_create, people_delete, people_get, people_list, people_update
from jobs.routing.directive_prefixes import DIRECTIVE_PREFIXES
from config.settings import WATSON_SYSTEM
from core.vacation import is_vacation_mode, set_vacation_mode, vacation_gate
from core.claude_tier import is_api_spending_enabled, set_api_spending_enabled, get_spend_log, get_month_summary


DB = os.path.expanduser("~/watson/data/watson.db")
CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
EVENT_FILES_DIR = Path(os.path.expanduser("~/watson/data/event_files"))
SKILLS_FILE = Path(__file__).resolve().parents[2] / "memory" / "skills.json"
COMMANDS_FILE = Path(__file__).resolve().parents[2] / "memory" / "commands.json"
MEMORY = Path(__file__).resolve().parents[2] / "memory"
app = Flask(__name__, static_folder='static', template_folder='templates')
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    log.warning("FLASK_SECRET_KEY is not set — using insecure default. Set it in .env.")
    _secret_key = "watson-dashboard-secret"
app.secret_key = _secret_key
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
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
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN location_zone TEXT")
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
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created "
        "ON chat_messages(session_id, created_at)"
    )
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
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        direction  TEXT    NOT NULL,
        message    TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS location_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        lat        REAL    NOT NULL,
        lon        REAL    NOT NULL,
        timestamp  TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS logins (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        label      TEXT    NOT NULL,
        username   TEXT,
        password   TEXT,
        url        TEXT,
        notes      TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS login_challenges (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        challenge  TEXT    NOT NULL,
        response   TEXT    NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS vault_status (
        id         INTEGER PRIMARY KEY,
        locked     INTEGER DEFAULT 0,
        locked_at  DATETIME
    )""")
    c.execute("INSERT OR IGNORE INTO vault_status (id, locked) VALUES (1, 0)")
    c.execute("""CREATE TABLE IF NOT EXISTS shared_notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id  INTEGER NOT NULL,
        content    TEXT    NOT NULL,
        author     TEXT    NOT NULL,
        created_at TEXT    DEFAULT (datetime('now'))
    )""")
    try:
        c.execute("ALTER TABLE team_tasks ADD COLUMN priority TEXT DEFAULT 'medium'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE team_tasks ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE team_tasks ADD COLUMN category TEXT DEFAULT 'catalyst'")
    except Exception:
        pass
    try:
        c.execute("UPDATE team_tasks SET priority = '3' WHERE priority = 'medium'")
        c.execute("UPDATE team_tasks SET priority = '1' WHERE priority = 'high'")
        c.execute("UPDATE team_tasks SET priority = '5' WHERE priority = 'low'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE team_tasks ADD COLUMN completed_at TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS church_events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name       TEXT    NOT NULL,
        start_date       TEXT    NOT NULL,
        end_date         TEXT,
        description      TEXT,
        attendance_notes TEXT,
        created_at       TEXT    DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS church_event_files (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id      INTEGER NOT NULL REFERENCES church_events(id),
        filename      TEXT    NOT NULL,
        original_name TEXT    NOT NULL,
        file_type     TEXT,
        uploaded_at   TEXT    DEFAULT (datetime('now'))
    )""")
    c.commit()
    c.close()


_bootstrap()

from jobs.events.schema import create_tables as _events_create_tables
_events_create_tables()


def _bootstrap_congregation():
    """Add member_status columns to congregation.db members table."""
    try:
        c = sqlite3.connect(CONG_DB)
        for col_sql in [
            "ALTER TABLE members ADD COLUMN member_status TEXT DEFAULT 'active'",
            "ALTER TABLE members ADD COLUMN status_reason TEXT",
            "ALTER TABLE members ADD COLUMN status_since TEXT",
            "ALTER TABLE members ADD COLUMN status_note TEXT",
            "ALTER TABLE members ADD COLUMN snowbird_return TEXT",
        ]:
            try:
                c.execute(col_sql)
            except Exception:
                pass
        c.execute("UPDATE members SET member_status = 'active' WHERE member_status IS NULL")
        c.commit()
        c.close()
    except Exception as exc:
        log.warning("congregation.db migration: %s", exc)


_bootstrap_congregation()

from jobs.writing_room.api import writing_room_bp
from jobs.writing_room import bootstrap_db as _wr_bootstrap
_wr_bootstrap()
app.register_blueprint(writing_room_bp)

from jobs.bodyrec.api import bodyrec_bp
from jobs.bodyrec import bootstrap_db as _bodyrec_bootstrap
_bodyrec_bootstrap()
app.register_blueprint(bodyrec_bp)

from jobs.location.api import location_bp
from jobs.location import bootstrap_db as _location_bootstrap
_location_bootstrap()
app.register_blueprint(location_bp)

from jobs.arc.api import arc_bp
app.register_blueprint(arc_bp)

from jobs.arc.auth import arc_auth_bp
app.register_blueprint(arc_auth_bp)

from jobs.lead_magnet.api import lead_magnet_bp
app.register_blueprint(lead_magnet_bp)

from jobs.book_launch.api import book_launch_bp
app.register_blueprint(book_launch_bp)

from jobs.newsletter.api import newsletter_bp
app.register_blueprint(newsletter_bp)

from jobs.arc_interest.api import arc_interest_bp
app.register_blueprint(arc_interest_bp)

from jobs.publishing.api import publishing_bp
from jobs.publishing import bootstrap_db as _publishing_bootstrap
_publishing_bootstrap()
app.register_blueprint(publishing_bp)

from jobs.dashboard.publishing_routes import publishing_dashboard_bp
app.register_blueprint(publishing_dashboard_bp)

from jobs.congregation.attendance_web import attendance_web_bp
app.register_blueprint(attendance_web_bp)

from jobs.congregation.servants_web import servants_web_bp
app.register_blueprint(servants_web_bp)

from jobs.congregation.catalystdb_web import catalystdb_web_bp
app.register_blueprint(catalystdb_web_bp)

from jobs.congregation.elder_shepherding_report_web import elder_shepherding_report_web_bp
app.register_blueprint(elder_shepherding_report_web_bp)

from jobs.congregation.duplicate_review import duplicate_review_bp
app.register_blueprint(duplicate_review_bp)

from jobs.congregation.deacons_web import deacons_web_bp
app.register_blueprint(deacons_web_bp)

from jobs.congregation.papercards_web import papercards_web_bp
app.register_blueprint(papercards_web_bp)

from jobs.team.api import team_bp
app.register_blueprint(team_bp)

from jobs.curator.api import curator_bp
from jobs.curator import bootstrap_db as _curator_bootstrap
_curator_bootstrap()
app.register_blueprint(curator_bp)

from jobs.curator.worker import start_worker as _curator_start_worker
_curator_start_worker()

from jobs.links.api import links_bp
app.register_blueprint(links_bp)

from jobs.tools.api import tools_bp
from jobs.tools.schema import create_tables as _tools_create_tables
_tools_create_tables()
app.register_blueprint(tools_bp)

from jobs.campaigns.campaign_routes import campaigns_bp
from jobs.campaigns.schema import create_tables as _campaigns_create_tables
_campaigns_create_tables()
app.register_blueprint(campaigns_bp)

from jobs.comms.api import comms_bp
from jobs.comms import bootstrap_db as _comms_bootstrap_db
_comms_bootstrap_db()
app.register_blueprint(comms_bp)

from jobs.email_activity.api import email_activity_bp
app.register_blueprint(email_activity_bp)

from jobs.telegram.dashboard_api import telegram_log_bp
app.register_blueprint(telegram_log_bp)

from jobs.telegram.leader_tool_usage_api import leader_tool_usage_bp
app.register_blueprint(leader_tool_usage_bp)

from jobs.privacy.dashboard_api import privacy_guard_bp
app.register_blueprint(privacy_guard_bp)

from jobs.kb.api import kb_bp
from jobs.kb.schema import create_tables as _kb_create_tables
_kb_create_tables()
app.register_blueprint(kb_bp)

from jobs.exports.api import exports_bp
from jobs.exports.schema import create_tables as _exports_create_tables
_exports_create_tables()
app.register_blueprint(exports_bp)

from jobs.church_social.api import church_social_bp
from jobs.church_social.social import init_db as _church_social_init_db
_church_social_init_db()
app.register_blueprint(church_social_bp)

from jobs.church_social.social_web import church_social_web_bp
app.register_blueprint(church_social_web_bp)

from jobs.book.routes import book_bp
from jobs.book.schema import create_tables as _book_create_tables
_book_create_tables()
app.register_blueprint(book_bp)

from jobs.devdispatch.api import devdispatch_bp
app.register_blueprint(devdispatch_bp)

from jobs.dev.sandbox_session import dev_sandbox_bp, ensure_table as _dev_sandbox_ensure_table
_dev_sandbox_ensure_table()
app.register_blueprint(dev_sandbox_bp)

from jobs.trading.routes import trading_bp
from jobs.trading.schema import create_tables as _trading_create_tables
_trading_create_tables()
app.register_blueprint(trading_bp)

from jobs.location.routes import location_web_bp
app.register_blueprint(location_web_bp)

from jobs.servantcare.servantcare_web import servantcare_web_bp
from jobs.servantcare.schema import create_tables as _servantcare_create_tables
_servantcare_create_tables()
app.register_blueprint(servantcare_web_bp)

from jobs.beachhouse.beachhouse_web import beachhouse_web_bp
from jobs.beachhouse.schema import create_tables as _beachhouse_create_tables
_beachhouse_create_tables()
app.register_blueprint(beachhouse_web_bp)

# ── Admin template filters ────────────────────────────────────────────────────

_AV_COLORS = ['#4c7ec9','#4caf7d','#c9a84c','#c9504c','#9b59b6','#1abc9c','#e67e22','#2980b9']


@app.template_filter('member_color')
def _member_color(idx):
    return _AV_COLORS[(idx - 1) % len(_AV_COLORS)]


@app.template_filter('initials')
def _initials(name):
    if not name:
        return '?'
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


@app.template_filter('date_color')
def _date_color(d):
    if not d:
        return 'date-none'
    try:
        from datetime import date as _dt
        days = (_dt.today() - _dt.fromisoformat(str(d)[:10])).days
    except Exception:
        return 'date-none'
    if days <= 7:
        return 'date-gray'
    if days <= 14:
        return 'date-amber'
    return 'date-red'


def _build_email_body(content: str) -> str:
    return f"Dr. Bill asked me to send this to you:\n\n{content}"


def _send_telegram(text: str) -> None:
    """Send a plain text message via Telegram."""
    if vacation_gate("normal", "jobs.dashboard.app._send_telegram", text):
        return
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
    if vacation_gate("normal", "jobs.dashboard.app._send_qr_telegram", content):
        return
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


@app.route("/team")
def team():
    return render_template('team.html')


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({"current_time": datetime.now().isoformat()})


@app.route("/api/settings/vacation-mode", methods=["GET", "PATCH"])
def vacation_mode_api():
    if request.method == "PATCH":
        data = request.get_json(force=True) or {}
        if "vacation_mode" not in data:
            return jsonify({"error": "vacation_mode is required"}), 400
        set_vacation_mode(bool(data["vacation_mode"]))

    conn = _db()
    count_row = conn.execute("SELECT COUNT(*) AS n FROM vacation_suppressed_log").fetchone()
    recent = conn.execute(
        "SELECT source, message, created_at FROM vacation_suppressed_log ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return jsonify({
        "vacation_mode": is_vacation_mode(),
        "suppressed_count": count_row["n"] if count_row else 0,
        "recent": [dict(r) for r in recent],
    })


@app.route("/api/settings/api-spending", methods=["GET", "PATCH"])
def api_spending_settings():
    if request.method == "PATCH":
        data = request.get_json(force=True) or {}
        if "enabled" not in data:
            return jsonify({"error": "enabled is required"}), 400
        set_api_spending_enabled(bool(data["enabled"]))
    return jsonify(get_month_summary())


@app.route("/api/claude-tier/log", methods=["GET"])
def claude_tier_log():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    limit = request.args.get("limit", 50, type=int)
    return jsonify({
        "summary": get_month_summary(),
        "log": get_spend_log(limit),
    })


_TERM_BLOCKLIST = ("rm ", "sudo rm", "drop ", "drop;", "format ", "shutdown", "reboot", ":(){", ">(")

_TERM_COMMANDS = {
    "system status": ("skill", "jobs.dev.system_monitor"),  # doc: CPU, memory, disk, and service health.
    # watson-bot.service / watson-dashboard.service log via StandardOutput=journal
    # (see /etc/systemd/system/*.service) — there is no single ~/watson/logs/watson.log
    # file to tail anymore; per-job scripts each write their own log under logs/ instead.
    # The api.telegram.org/bot lines are filtered out: they're just getUpdates polling
    # noise, and the raw URL embeds the bot token in plaintext (httpx request logging).
    "check logs": ("shell", "journalctl -u watson-bot.service -u watson-dashboard.service -n 100 --no-pager | grep -v 'api.telegram.org/bot' | tail -50"),  # doc: Tail the watson-bot / watson-dashboard systemd journal.
    "disk usage": ("shell", "df -h"),  # doc: Disk usage.
    "memory usage": ("shell", "free -h"),  # doc: Memory usage.
    "git pull": ("shell", "git -C " + os.path.expanduser("~/watson") + " pull"),  # doc: Pull latest changes into ~/watson.
    "restart watson bot": ("shell", "sudo systemctl restart watson-bot.service"),  # doc: Restart watson-bot.service (passwordless sudo scoped to exactly this command).
    "restart dashboard": ("shell", "sudo systemctl restart watson-dashboard.service"),  # doc: Restart watson-dashboard.service (passwordless sudo scoped to exactly this command).
    "count congregation members": ("sqlite", "congregation"),  # doc: Row count from the congregation table.
    "count tasks": ("sqlite", "tasks"),  # doc: Row count of active tasks.
    "count connect cards": ("sqlite", "connect_cards"),  # doc: Row count from the connect_cards table.
    "watson audit skills": ("skill", "jobs.dev.skill_tester"),  # doc: Run the full skill_audit self-test and report pass/fail.
    "watson fix all failing skills": ("fix_skills", None),  # doc: Queue every skill_audit-failing skill for auto-fix.
    "conflict_check": ("async_job", "jobs.connect_cards.conflict_report"),  # doc: Run the member-conflict report in the background (results arrive via Telegram).
}


@app.route("/api/terminal", methods=["POST"])
def terminal():
    import subprocess as _sp
    import sqlite3 as _sq

    if not session.get("admin_logged_in"):
        return jsonify({
            "output": "Not authenticated — log in at /admin/login to use the terminal.",
            "success": False,
        }), 401

    data = request.get_json(force=True) or {}
    cmd = (data.get("command") or "").strip()
    if not cmd:
        return jsonify({"output": "No command provided.", "success": False})

    cmd_lower = cmd.lower()
    for blocked in _TERM_BLOCKLIST:
        if blocked in cmd_lower:
            return jsonify({"output": f"Blocked: '{blocked}' is not allowed.", "success": False})

    # Directive prefix routing — early-return before _TERM_COMMANDS lookup
    def _pfx_out(text):
        return jsonify({"output": (text or "(no output)").strip(), "success": True})

    if cmd_lower.startswith("cdb:"):  # doc: Query the congregation database in plain English (attendance, membership, campus, engagement trends).
        try:
            from jobs.skills.cdb_query import run as _cdb_run
            return _pfx_out(_cdb_run(cmd[4:].strip()) or "No results.")
        except Exception as _exc:
            return jsonify({"output": f"cdb error: {_exc}", "success": False})

    if cmd_lower.startswith("wdb:"):  # doc: Query the leadership/team database (task status, stalled work, follow-ups, meeting notes).
        try:
            from jobs.skills.wdb_query import run as _wdb_run
            return _pfx_out(_wdb_run(cmd[4:].strip()) or "No results.")
        except Exception as _exc:
            return jsonify({"output": f"wdb error: {_exc}", "success": False})

    if cmd_lower.startswith("web:"):  # doc: Web search (prefix form of the web_search skill).
        try:
            from jobs.research.web_search import run as _web_run
            return _pfx_out(_web_run(cmd[4:].strip()) or "No results.")
        except Exception as _exc:
            return jsonify({"output": f"web error: {_exc}", "success": False})

    if cmd_lower.startswith("imagegen:") or cmd_lower.startswith("imgen:"):  # doc: Generate an AI image from a text prompt.
        try:
            from jobs.skills.image_gen_skill import run as _image_gen_run
            _prefix_len = len("imagegen:") if cmd_lower.startswith("imagegen:") else len("imgen:")
            return _pfx_out(_image_gen_run(cmd[_prefix_len:].strip()) or "No result.")
        except Exception as _exc:
            return jsonify({"output": f"image error: {_exc}", "success": False})

    if cmd_lower.startswith("backlog:"):  # doc: Log an item to the project backlog.
        _bl_arg = cmd[8:].strip()
        if not _bl_arg:
            return _pfx_out("Format: backlog: <title> | <summary>")
        from jobs.dev.backlog import create_backlog_item as _create_backlog_item, parse_directive_text as _parse_backlog
        _bl_title, _bl_summary = _parse_backlog(_bl_arg)
        _create_backlog_item(_bl_title, _bl_summary)
        return _pfx_out(f"Logged to backlog: {_bl_title}")

    if cmd_lower.startswith("xkb:"):  # doc: Search sermon transcripts only (narrower than kb:, which searches all KB content).
        try:
            from jobs.skills.kb_search import search_kb as _search_kb, format_result as _fmt_kb
            _kq = cmd[4:].strip()
            return _pfx_out(_fmt_kb(_search_kb(_kq, "sermons", True)))
        except Exception as _exc:
            return jsonify({"output": f"KB error: {_exc}", "success": False})

    if cmd_lower.startswith("search the kb:") or cmd_lower.startswith("kb:"):  # doc: Search the full ChromaDB knowledge base (sermons, devotionals, bible study notes, etc).
        try:
            from jobs.skills.kb_search import search_kb as _search_kb, format_result as _fmt_kb
            _kq = cmd[14:].strip() if cmd_lower.startswith("search the kb:") else cmd[3:].strip()
            return _pfx_out(_fmt_kb(_search_kb(_kq)))
        except Exception as _exc:
            return jsonify({"output": f"KB error: {_exc}", "success": False})

    if cmd_lower.startswith("shepherding:"):  # doc: Pastoral shepherding report — critical care, at-risk, first-time visitors, no-next-step members.
        try:
            from jobs.connect_cards.shepherding_report import telegram_shepherding_summary
            return _pfx_out(telegram_shepherding_summary() or "No results.")
        except Exception as _exc:
            return jsonify({"output": f"shepherding error: {_exc}", "success": False})

    if cmd_lower == "state of church report":  # doc: Generate and email the full State of the Church HTML report (async — delivered by email).
        # jobs.connect_cards.state_of_church has no importable run() (it's a
        # python -m script that emails the HTML report) and connect_cards/ is
        # off-limits to modify -- so this fires it the same way
        # /api/reports/state-of-church already does: background subprocess.
        import subprocess as _sp_soc
        import threading as _th_soc

        def _run_state_of_church():
            _sp_soc.run(
                ["venv/bin/python", "-m", "jobs.connect_cards.state_of_church"],
                cwd="/home/billyomes/watson",
                env={**os.environ, "PYTHONPATH": "/home/billyomes/watson"},
            )

        _th_soc.Thread(target=_run_state_of_church, daemon=True).start()
        return _pfx_out("Generating the State of the Church report — it'll be emailed to you shortly.")

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

        elif kind == "async_job":
            import threading
            import importlib
            mod = importlib.import_module(target)
            threading.Thread(target=mod.run, daemon=True).start()
            output = "Running conflict check… results will arrive via Telegram."

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
            "SELECT tpa.id, np.appointment_title as title, np.appointment_time as subtitle, 'NOTE' as type "
            "FROM tg_pending_actions tpa "
            "JOIN notes_pending np ON np.id = json_extract(tpa.payload, '$.notes_pending_id') "
            "WHERE tpa.type = 'pastoral_note' AND tpa.status = 'pending'"
        ).fetchall()
        for r in rows:
            items.append({"id": r["id"], "type": r["type"], "title": r["title"], "subtitle": r["subtitle"]})
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
    data = request.get_json(force=True) or {}
    # Carrier is routed through phone_carriers (watson.db), not the legacy
    # people.carrier column — pop it before it ever reaches people_update().
    carrier_value = data.pop("carrier", None)

    result = people_update(person_id, data) if data else people_get(person_id)

    if carrier_value is not None and isinstance(result, dict) and "error" not in result:
        if carrier_value.strip():
            phone_for_carrier = data.get("phone") or result.get("phone")
            result["carrier_result"] = _apply_carrier_update(phone_for_carrier, carrier_value)
        else:
            result["carrier_result"] = {"status": "skipped"}

    return jsonify(result)


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


_MEMBER_FIELDS = (
    "m.id, m.name, m.email, m.phone, m.campus_preference, m.partnership_status, m.active, "
    "m.member_status, m.status_reason, m.status_since, m.status_note, m.snowbird_return, "
    "m.partner, m.active_v2, m.residency, "
    "(SELECT MAX(service_date) FROM ("
    "  SELECT service_date FROM connect_cards WHERE member_id = m.id "
    "  UNION "
    "  SELECT service_date FROM attendance WHERE member_id = m.id"
    ")) AS last_seen"
)


def _cong_conn():
    c = sqlite3.connect(CONG_DB)
    c.row_factory = sqlite3.Row
    return c


def _apply_carrier_update(phone_raw, carrier_value):
    """Save a confirmed carrier via jobs.sms.carrier_lookup.save_carrier — the
    single source of truth for carrier data (phone_carriers, watson.db), keyed
    by normalized phone number. Never writes to members or the legacy
    people.carrier column.

    Returns {"status": "saved"} / {"status": "skipped_no_phone"} /
    {"status": "error", "error": str}.
    """
    from jobs.sms.carrier_lookup import normalize_phone, save_carrier
    digits = normalize_phone(phone_raw or "")
    if not digits:
        return {"status": "skipped_no_phone"}
    try:
        save_carrier(digits, carrier_value, source="manual", confirmed=True)
        return {"status": "saved"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@app.route("/api/phone-carrier")
def phone_carrier_lookup_api():
    """Look up the confirmed carrier for a phone number from phone_carriers
    (watson.db) — the shared cache used by SMS sending. Only ever returns a
    carrier when confirmed=1; an unconfirmed NumVerify guess is never
    surfaced as if it were a known fact."""
    from jobs.sms.carrier_lookup import normalize_phone
    digits = normalize_phone(request.args.get("phone", ""))
    if not digits:
        return _no_cache(jsonify({"carrier": None}))
    row = _db().execute(
        "SELECT carrier FROM phone_carriers WHERE phone_number = ? AND confirmed = 1",
        (digits,),
    ).fetchone()
    return _no_cache(jsonify({"carrier": row["carrier"] if row else None}))


def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/api/members")
def members_list_api():
    try:
        c = _cong_conn()
        rows = c.execute(
            f"SELECT {_MEMBER_FIELDS} FROM members m ORDER BY m.name COLLATE NOCASE"
        ).fetchall()
        c.close()
        return _no_cache(jsonify([dict(r) for r in rows]))
    except Exception as exc:
        return _no_cache(jsonify({"error": str(exc)})), 500


@app.route("/api/members/search")
def members_search_api():
    q = (request.args.get("q") or "").strip()
    if not q:
        return _no_cache(jsonify([]))
    try:
        c = _cong_conn()
        rows = c.execute(
            f"SELECT {_MEMBER_FIELDS} FROM members m "
            "WHERE m.name LIKE ? COLLATE NOCASE ORDER BY m.name COLLATE NOCASE LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
        c.close()
        return _no_cache(jsonify([dict(r) for r in rows]))
    except Exception as exc:
        return _no_cache(jsonify({"error": str(exc)})), 500


@app.route("/api/members/<int:member_id>", methods=["PATCH"])
def members_update_api(member_id):
    data = request.get_json(force=True) or {}
    # partner/active_v2/residency (2026-09-24) are the new columns replacing
    # member_status/partnership_status; both old and new stay writable here
    # during the transition since this dashboard's own frontend still edits
    # the old ones -- see ~/.claude/plans/zesty-cuddling-robin.md.
    allowed = {
        "member_status", "status_reason", "status_since", "status_note", "snowbird_return",
        "campus_preference", "partnership_status", "name", "partner", "active_v2", "residency",
    }
    fields = {k: v for k, v in data.items() if k in allowed}

    if "name" in fields:
        name_val = (fields["name"] or "").strip()
        if not name_val:
            return jsonify({"error": "name cannot be empty"}), 400
        fields["name"] = name_val

    # Carrier is not a members column — it's saved separately to phone_carriers
    # (watson.db), keyed by phone number, not member id. Presence of the key
    # (even "") signals intent; absence means "don't touch."
    carrier_value = data.get("carrier")

    last_seen_input = data.get("last_seen")
    if isinstance(last_seen_input, str):
        last_seen_input = last_seen_input.strip() or None

    if not fields and last_seen_input is None and carrier_value is None:
        return jsonify({"error": "nothing to update"}), 400
    try:
        c = _cong_conn()

        existing = c.execute(
            "SELECT campus_preference, phone, "
            "(SELECT MAX(service_date) FROM ("
            "  SELECT service_date FROM connect_cards WHERE member_id = members.id "
            "  UNION "
            "  SELECT service_date FROM attendance WHERE member_id = members.id"
            ")) AS last_seen "
            "FROM members WHERE id = ?",
            (member_id,),
        ).fetchone()
        if not existing:
            c.close()
            return jsonify({"error": "not found"}), 404

        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            c.execute(
                f"UPDATE members SET {set_clause} WHERE id = ?",
                (*fields.values(), member_id),
            )

        if last_seen_input and last_seen_input != existing["last_seen"]:
            dup = c.execute(
                "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
                (member_id, last_seen_input),
            ).fetchone()
            if not dup:
                # '--' (the blank-value convention) must fall back to
                # Wilmington too, same as None/blank/empty.
                _blank_campus = (None, "", "--")
                campus = (
                    fields.get("campus_preference")
                    if fields.get("campus_preference") not in _blank_campus
                    else existing["campus_preference"]
                    if existing["campus_preference"] not in _blank_campus
                    else "Wilmington"
                )
                c.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, NULL)",
                    (member_id, last_seen_input, campus),
                )

        c.commit()
        row = c.execute(
            f"SELECT {_MEMBER_FIELDS} FROM members m WHERE m.id = ?", (member_id,)
        ).fetchone()
        c.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        result = dict(row)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if carrier_value is not None:
        if carrier_value.strip():
            result["carrier_result"] = _apply_carrier_update(existing["phone"], carrier_value)
        else:
            result["carrier_result"] = {"status": "skipped"}

    return jsonify(result)


# ── Members CSV export / import (update-only, id is the match key) ────────────
#
# 2026-09-24: switched from member_status/partnership_status to the new
# active_v2/partner/residency columns (see
# ~/.claude/plans/zesty-cuddling-robin.md). connected is exported for
# reference but is NOT a _CSV_DIFF_FIELDS entry -- it's computed live from
# attendance, not a stored column, so it's never diffed or imported even if
# someone edits that cell in the spreadsheet.

_CSV_ACTIVE_V2_VALUES = {"active", "non-active", "disconnected", "deceased"}
_CSV_RESIDENCY_VALUES = {"local", "non-local", "snowbird"}
_CSV_PARTNER_VALUES = {"partner", "np"}
_CSV_CAMPUS_VALUES = {"Wilmington", "Online", "Hybrid", "--"}
_CSV_EXPORT_COLUMNS = ["id", "name", "email", "phone", "partner", "connected", "active_v2", "residency", "campus_preference"]
_CSV_DIFF_FIELDS = ["name", "email", "phone", "partner", "active_v2", "residency", "campus_preference"]


@app.route("/api/members/export")
def members_export_api():
    from datetime import date as _date

    from jobs.congregation.catalystdb_web import _connected

    try:
        c = _cong_conn()
        rows = c.execute(
            "SELECT id, name, email, phone, partner, active_v2, residency, campus_preference "
            "FROM members ORDER BY name COLLATE NOCASE"
        ).fetchall()
        today = _date.today()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_EXPORT_COLUMNS)
        for r in rows:
            row_dict = dict(r)
            row_dict["connected"] = _connected(c, r["id"], today)
            writer.writerow([row_dict[col] if row_dict[col] is not None else "" for col in _CSV_EXPORT_COLUMNS])
        c.close()
        filename = f"members-export-{datetime.now().strftime('%Y-%m-%d')}.csv"
        resp = Response(buf.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _parse_members_import_csv(file_storage):
    """Parse an uploaded members CSV, match rows to existing members by id, validate
    enums, and diff changed fields against current DB values. Read-only — never writes.

    Blank cells in name/email/phone/partner/active_v2/residency/campus_preference
    are treated as "leave unchanged" rather than a value to apply, so a stray blank
    cell in a bulk edit can't silently wipe a field (name is NOT NULL and the other
    fields are true enums with no blank member -- '--' is campus_preference's own
    blank marker, a real selectable value there, not an empty cell).

    Returns {"rows": [...], "counts": {...}}. Each row dict has: id, name, status
    (one of "to_update" / "unchanged" / "skipped_unknown_id" / "error"), reason,
    changes (list of {field, old_value, new_value}), and — only for "to_update" rows —
    new_values (dict of field -> validated new value, ready to write).
    """
    raw = file_storage.read()
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    reader = csv.DictReader(io.StringIO(text))

    c = _cong_conn()
    result_rows = []
    counts = {"unchanged": 0, "to_update": 0, "skipped_unknown_id": 0, "errors": 0}

    for row in reader:
        raw_id = (row.get("id") or "").strip()
        try:
            member_id = int(raw_id)
        except (TypeError, ValueError):
            counts["skipped_unknown_id"] += 1
            result_rows.append({
                "id": raw_id or None, "name": (row.get("name") or "").strip(),
                "status": "skipped_unknown_id", "reason": "missing or non-numeric id", "changes": [],
            })
            continue

        existing = c.execute(
            "SELECT id, name, email, phone, partner, active_v2, residency, campus_preference "
            "FROM members WHERE id = ?", (member_id,),
        ).fetchone()
        if not existing:
            counts["skipped_unknown_id"] += 1
            result_rows.append({
                "id": member_id, "name": (row.get("name") or "").strip(),
                "status": "skipped_unknown_id", "reason": "unknown id", "changes": [],
            })
            continue

        errors = []
        incoming_active_v2 = (row.get("active_v2") or "").strip()
        if incoming_active_v2 and incoming_active_v2 not in _CSV_ACTIVE_V2_VALUES:
            errors.append(f"invalid active_v2: {incoming_active_v2!r}")
        incoming_residency = (row.get("residency") or "").strip()
        if incoming_residency and incoming_residency not in _CSV_RESIDENCY_VALUES:
            errors.append(f"invalid residency: {incoming_residency!r}")
        incoming_campus = (row.get("campus_preference") or "").strip()
        if incoming_campus and incoming_campus not in _CSV_CAMPUS_VALUES:
            errors.append(f"invalid campus_preference: {incoming_campus!r}")
        incoming_partner = (row.get("partner") or "").strip()
        if incoming_partner and incoming_partner not in _CSV_PARTNER_VALUES:
            errors.append(f"invalid partner: {incoming_partner!r}")

        if errors:
            counts["errors"] += 1
            result_rows.append({
                "id": member_id, "name": existing["name"], "status": "error",
                "reason": "; ".join(errors), "changes": [],
            })
            continue

        changes = []
        new_values = {}
        for field in _CSV_DIFF_FIELDS:
            new_val = (row.get(field) or "").strip()
            if not new_val:
                continue  # blank cell: leave this field unchanged
            old_val = existing[field]
            old_norm = (old_val or "").strip() if old_val is not None else ""
            if new_val != old_norm:
                changes.append({"field": field, "old_value": old_val, "new_value": new_val})
                new_values[field] = new_val

        if changes:
            counts["to_update"] += 1
            result_rows.append({
                "id": member_id, "name": existing["name"], "status": "to_update",
                "reason": None, "changes": changes, "new_values": new_values,
            })
        else:
            counts["unchanged"] += 1
            result_rows.append({
                "id": member_id, "name": existing["name"], "status": "unchanged",
                "reason": None, "changes": [],
            })

    c.close()
    return {"rows": result_rows, "counts": counts}


@app.route("/api/members/import/preview", methods=["POST"])
def members_import_preview_api():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400
    try:
        parsed = _parse_members_import_csv(f)
    except Exception as exc:
        return jsonify({"error": f"failed to parse CSV: {exc}"}), 400

    changes = [
        {"id": r["id"], "name": r["name"], "field": ch["field"], "old_value": ch["old_value"], "new_value": ch["new_value"]}
        for r in parsed["rows"] if r["status"] == "to_update" for ch in r["changes"]
    ]
    errors = [{"id": r["id"], "name": r["name"], "reason": r["reason"]} for r in parsed["rows"] if r["status"] == "error"]
    skipped = [{"id": r["id"], "name": r["name"], "reason": r["reason"]} for r in parsed["rows"] if r["status"] == "skipped_unknown_id"]

    return jsonify({"counts": parsed["counts"], "changes": changes, "errors": errors, "skipped": skipped})


@app.route("/api/members/import/confirm", methods=["POST"])
def members_import_confirm_api():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400
    try:
        parsed = _parse_members_import_csv(f)
    except Exception as exc:
        return jsonify({"error": f"failed to parse CSV: {exc}"}), 400

    to_update = [r for r in parsed["rows"] if r["status"] == "to_update"]
    backup_created = False
    if to_update:
        from core.db_backup import prune_old_backups

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{CONG_DB}.bak-{ts}"
        shutil.copy2(CONG_DB, backup_path)
        prune_old_backups(CONG_DB)
        backup_created = True

        from jobs.congregation.catalystdb_web import _ACTIVE_V2_TO_LEGACY_ACTIVE

        c = sqlite3.connect(CONG_DB)
        try:
            for r in to_update:
                new_values = dict(r["new_values"])
                # Keep the legacy active boolean in sync, same as
                # catalystdb_web.py's /update endpoint, so an active_v2
                # change via CSV import doesn't go stale for reports not
                # yet migrated off it.
                if "active_v2" in new_values and new_values["active_v2"] in _ACTIVE_V2_TO_LEGACY_ACTIVE:
                    new_values["active"] = _ACTIVE_V2_TO_LEGACY_ACTIVE[new_values["active_v2"]]
                set_clause = ", ".join(f"{field} = ?" for field in new_values)
                values = list(new_values.values()) + [r["id"]]
                c.execute(f"UPDATE members SET {set_clause} WHERE id = ?", values)
            c.commit()
        except Exception:
            c.rollback()
            c.close()
            return jsonify({"error": "import failed, no changes applied, backup preserved"}), 500
        c.close()

    counts = parsed["counts"]
    return jsonify({
        "updated": counts["to_update"],
        "unchanged": counts["unchanged"],
        "skipped_unknown_id": counts["skipped_unknown_id"],
        "errors": counts["errors"],
        "backup_created": backup_created,
    })


@app.route("/api/members/<int:member_id>/roles", methods=["GET"])
def member_roles_list_api(member_id):
    try:
        c = _cong_conn()
        rows = c.execute(
            "SELECT role FROM leadership_roles WHERE member_id = ? AND is_active = 1 ORDER BY role",
            (member_id,),
        ).fetchall()
        c.close()
        return _no_cache(jsonify([r["role"] for r in rows]))
    except Exception as exc:
        return _no_cache(jsonify({"error": str(exc)})), 500


@app.route("/api/members/<int:member_id>/roles", methods=["POST"])
def member_roles_add_api(member_id):
    data = request.get_json(force=True) or {}
    role = (data.get("role") or "").strip().lower()
    if not role:
        return jsonify({"error": "role is required"}), 400
    try:
        c = _cong_conn()
        member = c.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
        if not member:
            c.close()
            return jsonify({"error": "not found"}), 404
        c.execute(
            """
            INSERT INTO leadership_roles (member_id, role, is_active) VALUES (?, ?, 1)
            ON CONFLICT(member_id, role) DO UPDATE SET is_active = 1
            """,
            (member_id, role),
        )
        c.commit()
        rows = c.execute(
            "SELECT role FROM leadership_roles WHERE member_id = ? AND is_active = 1 ORDER BY role",
            (member_id,),
        ).fetchall()
        c.close()
        return jsonify([r["role"] for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/members/<int:member_id>/roles/<role>", methods=["DELETE"])
def member_roles_delete_api(member_id, role):
    role = (role or "").strip().lower()
    try:
        c = _cong_conn()
        c.execute(
            "UPDATE leadership_roles SET is_active = 0 WHERE member_id = ? AND role = ?",
            (member_id, role),
        )
        c.commit()
        rows = c.execute(
            "SELECT role FROM leadership_roles WHERE member_id = ? AND is_active = 1 ORDER BY role",
            (member_id,),
        ).fetchall()
        c.close()
        return jsonify([r["role"] for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/members/<int:member_id>/aliases", methods=["GET"])
def member_aliases_list_api(member_id):
    """Read-only — aliases are managed via 'cdb: alias <name> = <alias>' only."""
    try:
        c = _cong_conn()
        rows = c.execute(
            "SELECT alias FROM member_aliases WHERE member_id = ? ORDER BY alias COLLATE NOCASE",
            (member_id,),
        ).fetchall()
        c.close()
        return _no_cache(jsonify([r["alias"] for r in rows]))
    except Exception as exc:
        return _no_cache(jsonify({"error": str(exc)})), 500


@app.route("/api/members/batch-update", methods=["POST"])
def members_batch_update_api():
    from jobs.connect_cards.batch_update import FIELDS as _BU_FIELDS, validate_value, batch_update_members

    data = request.get_json(force=True) or {}
    field = (data.get("field") or "").strip()
    value = data.get("value")
    names_raw = data.get("names")
    if isinstance(names_raw, list):
        names = [str(n).strip() for n in names_raw if str(n).strip()]
    else:
        names = [n.strip() for n in re.split(r"[,\n]", names_raw or "") if n.strip()]

    if field not in _BU_FIELDS:
        return jsonify({"error": f"invalid field: {field!r}"}), 400
    if not names:
        return jsonify({"error": "no names provided"}), 400

    err = validate_value(field, value)
    if err:
        return jsonify({"error": err}), 400

    try:
        resolution = batch_update_members(field, value, names)
        return jsonify(resolution)
    except Exception as exc:
        log.error("batch-update preview error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/members/batch-update/confirm", methods=["POST"])
def members_batch_update_confirm_api():
    from jobs.connect_cards.batch_update import validate_value, commit_batch_update

    data = request.get_json(force=True) or {}
    field = (data.get("field") or "").strip()
    value = data.get("value")

    try:
        member_ids = [int(x) for x in (data.get("member_ids") or [])]
    except (TypeError, ValueError):
        return jsonify({"error": "invalid member_ids"}), 400
    if not member_ids:
        return jsonify({"error": "no member_ids provided"}), 400

    err = validate_value(field, value)
    if err:
        return jsonify({"error": err}), 400

    try:
        result = commit_batch_update(field, value, member_ids, actor="Bill (Dashboard)")
        return jsonify(result), (200 if not result["errors"] else 400)
    except Exception as exc:
        log.error("batch-update confirm error: %s", exc)
        return jsonify({"error": str(exc)}), 500


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
        if vacation_gate("normal", "jobs.dashboard.app.contacts_import", summary):
            return
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


@app.route("/api/fireflies/webhook", methods=["POST"])
def fireflies_webhook():
    import hashlib
    import hmac
    import threading

    raw_body = request.get_data()
    received_raw = request.headers.get("x-hub-signature", "")
    secret = os.getenv("FIREFLIES_WEBHOOK_SECRET", "")

    if not secret:
        log.error("FIREFLIES_WEBHOOK_SECRET not set; rejecting Fireflies webhook.")
        return jsonify({"error": "not configured"}), 401

    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Fireflies (like GitHub) prefixes the digest, e.g. "sha256=<hex>" — strip it before comparing.
    received = received_raw[7:] if received_raw.lower().startswith("sha256=") else received_raw

    if not received or not hmac.compare_digest(computed, received):
        sig_headers = {k: v for k, v in request.headers.items() if "signature" in k.lower()}
        log.warning(
            "Fireflies webhook signature mismatch. computed=%s received_header(x-hub-signature)=%r "
            "signature_headers=%s all_header_names=%s",
            computed, received_raw, sig_headers, list(request.headers.keys()),
        )
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}
    log.info("Fireflies webhook payload: %s", payload)

    # Confirmed from a real test payload: {"event": "test", "timestamp": ...,
    # "meeting_id": "test_00000000"} — the key is "event", not "eventType" as
    # general docs describe, and "meeting_id" (snake_case), not "meetingId".
    # Check both key names defensively in case real events differ in shape.
    event_type = payload.get("event") or payload.get("eventType")
    meeting_id = payload.get("meetingId") or payload.get("meeting_id")

    # event == "test" is Fireflies' webhook test-button payload — intentionally
    # a no-op (falls through to the else branch below like any other
    # unrecognized event), not a bug.
    #
    # Confirmed via journalctl 2026-07-21: Fireflies actually sends
    # event="meeting.transcribed" (raw transcript only, no summary/action
    # items yet) and event="meeting.summarized" (full summary ready). We act
    # on "meeting.summarized" only — draft_review_email() needs the summary
    # data that "meeting.transcribed" doesn't have yet.
    if event_type == "meeting.summarized" and meeting_id:
        def _run():
            from jobs.meet.fireflies_review import process_meeting
            try:
                process_meeting(meeting_id)
            except Exception as exc:
                log.error("Fireflies review processing failed for %s: %s", meeting_id, exc)

        threading.Thread(target=_run, daemon=True).start()
    elif event_type == "meeting.transcribed":
        log.info(
            "Fireflies webhook ignored (transcript only, awaiting summary): meeting_id=%r",
            meeting_id,
        )
    else:
        log.info("Fireflies webhook ignored: event=%r meeting_id=%r", event_type, meeting_id)

    return jsonify({"ok": True}), 200


# ── Fireflies meeting review (dashboard) ─────────────────────────────────────
# Gated by _admin_required() — same session check as /admin/*. Not explicitly
# specified, but this handles pastoral/leadership meeting content, and the
# admin session is the closest existing precedent for "protected, sensitive
# dashboard content" in this app.

@app.route("/meet/reviews")
def meet_reviews_list():
    redir = _admin_required()
    if redir:
        return redir
    db = _db()
    reviews = db.execute(
        "SELECT id, title, meeting_date, status, created_at FROM meeting_reviews ORDER BY created_at DESC"
    ).fetchall()
    return render_template("meet_reviews_list.html", reviews=[dict(r) for r in reviews])


@app.route("/meet/review/<int:review_id>")
def meet_review_page(review_id):
    redir = _admin_required()
    if redir:
        return redir
    db = _db()
    review = db.execute("SELECT * FROM meeting_reviews WHERE id=?", (review_id,)).fetchone()
    if not review:
        return "Review not found", 404
    items = db.execute(
        "SELECT * FROM meeting_review_action_items WHERE review_id=? ORDER BY sort_order",
        (review_id,),
    ).fetchall()

    from jobs.meet.fireflies_review import get_review_owners
    members = get_review_owners()

    return render_template(
        "meet_review.html",
        review=dict(review),
        items=[dict(i) for i in items],
        members=members,
    )


@app.route("/api/meet/review/<int:review_id>", methods=["POST"])
def meet_review_save(review_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401

    data = request.get_json(force=True) or {}
    summary_text = data.get("summary_text", "")
    items = data.get("items") or []

    db = _db()
    review = db.execute("SELECT id FROM meeting_reviews WHERE id=?", (review_id,)).fetchone()
    if not review:
        return jsonify({"error": "not found"}), 404

    db.execute("UPDATE meeting_reviews SET summary_text=? WHERE id=?", (summary_text, review_id))

    # Simplest correct way to handle add/delete/reorder from the UI in one
    # save: replace the full item set. Low-volume, single-editor table, no
    # other table references these rows.
    db.execute("DELETE FROM meeting_review_action_items WHERE review_id=?", (review_id,))
    for i, item in enumerate(items):
        item_text = (item.get("item_text") or "").strip()
        if not item_text:
            continue
        owner_member_id = item.get("owner_member_id") or None
        db.execute(
            """INSERT INTO meeting_review_action_items
               (review_id, owner_text, owner_member_id, item_text, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (review_id, item.get("owner_text") or "", owner_member_id, item_text, i),
        )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/meet/review/<int:review_id>/preview", methods=["POST"])
def meet_review_preview(review_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401

    db = _db()
    review = db.execute("SELECT * FROM meeting_reviews WHERE id=?", (review_id,)).fetchone()
    if not review:
        return jsonify({"error": "not found"}), 404
    items = db.execute(
        "SELECT * FROM meeting_review_action_items WHERE review_id=? ORDER BY sort_order",
        (review_id,),
    ).fetchall()

    from jobs.meet.fireflies_review import BILL_PREVIEW_EMAIL, send_html_email
    from jobs.meet.templates.elder_review import (
        build_structured_content_from_review, render_elder_review_email, render_elder_review_plain,
    )

    structured = build_structured_content_from_review(dict(review), [dict(i) for i in items])
    subject, html = render_elder_review_email(structured, preview=True)
    plain = render_elder_review_plain(structured)
    try:
        send_html_email(BILL_PREVIEW_EMAIL, subject, html, plain)
    except Exception as exc:
        log.error("Preview email failed for review %s: %s", review_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"success": True, "sent_to": BILL_PREVIEW_EMAIL})


@app.route("/api/meet/review/<int:review_id>/send", methods=["POST"])
def meet_review_send(review_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401

    db = _db()
    review = db.execute("SELECT * FROM meeting_reviews WHERE id=?", (review_id,)).fetchone()
    if not review:
        return jsonify({"error": "not found"}), 404
    if review["status"] == "sent":
        return jsonify({"error": "This review has already been sent."}), 409

    items = db.execute(
        "SELECT * FROM meeting_review_action_items WHERE review_id=? ORDER BY sort_order",
        (review_id,),
    ).fetchall()

    from jobs.meet.fireflies_review import get_elder_emails, send_html_email
    from jobs.meet.templates.elder_review import (
        build_structured_content_from_review, render_elder_review_email, render_elder_review_plain,
    )

    elders = get_elder_emails()
    if not elders:
        return jsonify({"error": "No members tagged 'elder' found — tag elders in Member Management first."}), 400

    structured = build_structured_content_from_review(dict(review), [dict(i) for i in items])
    subject, html = render_elder_review_email(structured, preview=False)
    plain = render_elder_review_plain(structured)

    sent, failed = 0, []
    for name, email in elders:
        try:
            send_html_email(email, subject, html, plain)
            sent += 1
        except Exception as exc:
            log.error("Failed to send elders review to %s <%s>: %s", name, email, exc)
            failed.append(name)

    db.execute(
        "UPDATE meeting_reviews SET status='sent', sent_at=datetime('now') WHERE id=?",
        (review_id,),
    )
    db.commit()

    # Auto-create team_tasks for action items whose FINAL resolved owner
    # (after any edits Bill made) is one of the 8 fixed elder-review people
    # AND has a team_members record. People on the list without one (Bill
    # Crook, Jim Bouchat, as of this writing) stay email-only — expected,
    # not an error. Runs after the email send + status update above are
    # already committed: a failed task-creation call must never block the
    # send, and one failed item must never block the rest.
    from jobs.meet.fireflies_review import ELDER_REVIEW_OWNERS
    owners_by_id = {o["id"]: o for o in ELDER_REVIEW_OWNERS}

    created_tasks: list[tuple[str, int]] = []
    failed_tasks: list[str] = []
    for item in items:
        owner_member_id = item["owner_member_id"]
        if not owner_member_id:
            continue
        owner = owners_by_id.get(owner_member_id)
        if not owner or owner["table"] != "team_members":
            continue
        title = (item["item_text"] or "").strip()[:500]
        if not title:
            continue
        try:
            task_id = _create_team_task(owner["id"], title, source="fireflies_review", category="catalyst")
            created_tasks.append((owner["display_name"], task_id))
        except Exception as exc:
            log.error("Failed to auto-create task for %s (review %s): %s", owner["display_name"], review_id, exc)
            failed_tasks.append(owner["display_name"])
    if created_tasks:
        db.commit()

    task_counts: dict[str, int] = {}
    for name, _task_id in created_tasks:
        task_counts[name] = task_counts.get(name, 0) + 1
    task_breakdown = ", ".join(f"{count} for {name}" for name, count in task_counts.items())

    msg = f"Sent to {sent} elder(s)."
    if failed:
        msg += f" Failed: {', '.join(failed)}."
    if created_tasks:
        msg += f" Created {len(created_tasks)} task(s)"
        if task_breakdown:
            msg += f" ({task_breakdown})"
        msg += "."
    if failed_tasks:
        msg += f" Task creation failed for: {', '.join(failed_tasks)}."

    return jsonify({
        "success": True,
        "sent": sent,
        "failed": failed,
        "tasks_created": len(created_tasks),
        "tasks_failed": failed_tasks,
        "msg": msg,
    })


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


# ── Thesis Tracker API ───────────────────────────────────────────────────────

@app.route("/api/thesis-tracker/latest")
def thesis_tracker_latest():
    try:
        db = _db()
        snapshot = db.execute(
            "SELECT * FROM thesis_snapshots ORDER BY pulled_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not snapshot:
            return jsonify(None)
        snapshot_id = snapshot["id"]
        titles = db.execute(
            "SELECT title, downloads FROM thesis_titles WHERE snapshot_id = ? ORDER BY downloads DESC",
            (snapshot_id,),
        ).fetchall()
        countries = db.execute(
            "SELECT country, downloads FROM thesis_countries WHERE snapshot_id = ? ORDER BY downloads DESC",
            (snapshot_id,),
        ).fetchall()
        institutions = db.execute(
            "SELECT institution, downloads FROM thesis_institutions WHERE snapshot_id = ? ORDER BY downloads DESC",
            (snapshot_id,),
        ).fetchall()
        referrers = db.execute(
            "SELECT referrer, downloads FROM thesis_referrers WHERE snapshot_id = ? ORDER BY downloads DESC",
            (snapshot_id,),
        ).fetchall()
        result = dict(snapshot)
        result["titles"] = [dict(r) for r in titles]
        result["countries"] = [dict(r) for r in countries]
        result["institutions"] = [dict(r) for r in institutions]
        result["referrers"] = [dict(r) for r in referrers]
        return jsonify(result)
    except sqlite3.OperationalError:
        return jsonify(None)


@app.route("/api/thesis-tracker/countries")
def thesis_tracker_countries():
    """All countries ever recorded across every snapshot, not just the latest one.

    Rolling 30-day snapshots can drop a country that genuinely was downloaded
    there once, just outside the current window. We want the full historical
    picture, so for each distinct country we take the downloads value from
    whichever snapshot most recently recorded it (not a sum across snapshots,
    since rolling windows would double-count).
    """
    try:
        db = _db()
        rows = db.execute(
            """
            SELECT tc.country, tc.downloads, tc.snapshot_id,
                   ts.pulled_at,
                   MIN(ts.pulled_at) OVER (PARTITION BY tc.country) AS first_seen,
                   MAX(ts.pulled_at) OVER (PARTITION BY tc.country) AS last_seen
            FROM thesis_countries tc
            JOIN thesis_snapshots ts ON ts.id = tc.snapshot_id
            WHERE tc.country IS NOT NULL
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return jsonify([])

    if not rows:
        return jsonify([])

    latest_by_country = {}
    for row in rows:
        country = row["country"]
        key = (row["pulled_at"], row["snapshot_id"])
        existing = latest_by_country.get(country)
        if existing is None or key > existing[0]:
            latest_by_country[country] = (key, row)

    result = [
        {
            "country": country,
            "downloads": row["downloads"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }
        for country, (_key, row) in latest_by_country.items()
    ]
    result.sort(key=lambda r: r["downloads"] or 0, reverse=True)
    return jsonify(result)


@app.route("/api/thesis-tracker/citations")
def thesis_tracker_citations():
    try:
        db = _db()
        rows = db.execute(
            "SELECT title, authors, venue, year, doi, url, sources, confidence "
            "FROM thesis_citations ORDER BY first_seen_at DESC"
        ).fetchall()
        doi_row = db.execute(
            "SELECT doi FROM thesis_doi_watch ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return jsonify({"citations": [], "doi": None})
    return jsonify({
        "citations": [dict(r) for r in rows],
        "doi": doi_row["doi"] if doi_row else None,
    })


@app.route("/api/thesis-tracker/pull", methods=["POST"])
def thesis_tracker_pull():
    dashboard_link = os.getenv("DC_DASHBOARD_LINK")
    if not dashboard_link:
        return jsonify({"success": False, "error": "DC_DASHBOARD_LINK missing from .env"}), 400
    from jobs.thesis_tracker.scrape import scrape
    result = scrape(dashboard_link)
    return jsonify(result)


# ── Reminders API ────────────────────────────────────────────────────────────

@app.route("/api/reminders")
def reminders_list():
    rows = _db().execute(
        "SELECT * FROM reminders WHERE status = 'active' ORDER BY sort_order ASC, created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reminders", methods=["POST"])
def reminders_create():
    """due_datetime, when given, must be a real UTC timestamp
    ("YYYY-MM-DD HH:MM:SS") comparable to SQLite's own datetime('now') --
    jobs/reminders/check_reminders.py's per-minute cron fires (via Telegram)
    on `due_datetime != '' AND due_datetime <= datetime('now')`.

    location_zone, when given, must match a jobs.location `location_zones.name`
    -- jobs/location/api.py fires (via Telegram) any active reminder whose
    location_zone matches the zone just entered. A reminder may set either,
    or both (whichever trigger happens first fires it and marks it 'fired').
    """
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    due_datetime = (data.get("due_datetime") or "").strip()
    location_zone = (data.get("location_zone") or "").strip() or None
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not due_datetime and not location_zone:
        return jsonify({"error": "due_datetime or location_zone is required"}), 400
    reminder_time = (data.get("reminder_time") or "").strip() or None
    cur = _db().execute(
        "INSERT INTO reminders (title, due_datetime, reminder_time, location_zone, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', datetime('now'), datetime('now'))",
        (title, due_datetime, reminder_time, location_zone),
    )
    _db().commit()
    row = _db().execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["PATCH"])
def reminders_update(reminder_id):
    data = request.get_json(force=True)
    allowed = {"title", "status", "reminder_time", "sort_order", "due_datetime", "location_zone"}
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
    """Per Bill's 2026-09-08 request, mirrors the Telegram post-appointment
    "share:" prefix (jobs/pastoral_notes/handler.py) as an explicit `share`
    flag here: the note always saves privately first, and `share: true`
    additionally copies it into deacon_notes once person_name resolves to
    exactly one active congregation member. `shared` in the response tells
    the frontend whether that resolution actually succeeded."""
    data = request.get_json()
    person_name = (data.get("person_name") or "").strip()
    note = (data.get("note") or "").strip()
    share = bool(data.get("share"))
    if not person_name or not note:
        return jsonify({"error": "person_name and note are required"}), 400
    cur = _db().execute(
        "INSERT INTO pastoral_notes (person_name, note) VALUES (?, ?)",
        (person_name, note)
    )
    _db().commit()
    shared = False
    if share:
        from jobs.pastoral_notes.handler import _share_as_deacon_note
        shared = _share_as_deacon_note(person_name, note)
    return jsonify({"id": cur.lastrowid, "ok": True, "shared": shared})


@app.route("/api/pastoral-notes/<int:note_id>/archive", methods=["POST"])
def pastoral_notes_archive(note_id):
    _db().execute(
        "UPDATE pastoral_notes SET status = 'archived' WHERE id = ?",
        (note_id,)
    )
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/pastoral-notes/<int:note_id>", methods=["DELETE"])
def pastoral_notes_delete(note_id):
    _db().execute("DELETE FROM pastoral_notes WHERE id = ?", (note_id,))
    _db().commit()
    return jsonify({"ok": True})


# ── Church Events API ─────────────────────────────────────────────────────────

@app.route("/api/events")
def events_list():
    rows = _db().execute("""
        SELECT e.id, e.event_name, e.start_date, e.end_date, e.event_time,
               e.description, e.attendance_notes, e.created_at, e.tracking_active,
               COUNT(DISTINCT f.id) as file_count,
               COUNT(DISTINCT r.id) as registration_count,
               COALESCE(SUM(r.num_tickets), 0) as total_tickets
        FROM church_events e
        LEFT JOIN church_event_files f ON f.event_id = e.id
        LEFT JOIN event_registrations r ON r.event_id = e.id
        GROUP BY e.id
        ORDER BY e.start_date DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events/<int:event_id>/registrations")
def events_registrations_list(event_id):
    rows = _db().execute("""
        SELECT id, first_name, last_name, email, phone, ticket_type,
               ticket_price, num_tickets, extra_fields, member_id, source, submitted_at
        FROM event_registrations
        WHERE event_id = ?
        ORDER BY submitted_at DESC, id DESC
    """, (event_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events", methods=["POST"])
def events_create():
    event_name = (request.form.get("event_name") or "").strip()
    start_date = (request.form.get("start_date") or "").strip()
    if not event_name or not start_date:
        return jsonify({"error": "event_name and start_date are required"}), 400
    end_date         = request.form.get("end_date") or None
    description      = request.form.get("description") or None
    attendance_notes = request.form.get("attendance_notes") or None

    db = _db()
    cur = db.execute(
        "INSERT INTO church_events (event_name, start_date, end_date, description, attendance_notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_name, start_date, end_date, description, attendance_notes),
    )
    db.commit()
    event_id = cur.lastrowid

    files = request.files.getlist("files[]")
    if files:
        event_dir = EVENT_FILES_DIR / str(event_id)
        event_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not f.filename:
                continue
            original_name = Path(f.filename).name
            safe_name = re.sub(r"[^\w.\-]", "_", original_name)
            dest = event_dir / safe_name
            f.save(str(dest))
            db.execute(
                "INSERT INTO church_event_files (event_id, filename, original_name, file_type) "
                "VALUES (?, ?, ?, ?)",
                (event_id, safe_name, original_name, f.content_type),
            )
        db.commit()

    row = db.execute("""
        SELECT e.id, e.event_name, e.start_date, e.end_date,
               e.description, e.attendance_notes, e.created_at,
               COUNT(f.id) as file_count
        FROM church_events e
        LEFT JOIN church_event_files f ON f.event_id = e.id
        WHERE e.id = ?
        GROUP BY e.id
    """, (event_id,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/events/<int:event_id>")
def events_get(event_id):
    db = _db()
    row = db.execute("SELECT * FROM church_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    files = db.execute(
        "SELECT id, filename, original_name, file_type, uploaded_at "
        "FROM church_event_files WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    result = dict(row)
    result["files"] = [dict(f) for f in files]
    return jsonify(result)


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def events_delete(event_id):
    db = _db()
    event_dir = EVENT_FILES_DIR / str(event_id)
    if event_dir.exists():
        shutil.rmtree(event_dir)
    db.execute("DELETE FROM church_event_files WHERE event_id = ?", (event_id,))
    db.execute("DELETE FROM church_events WHERE id = ?", (event_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/events/<int:event_id>/files/<path:filename>")
def events_serve_file(event_id, filename):
    event_dir = EVENT_FILES_DIR / str(event_id)
    filepath = (event_dir / filename).resolve()
    if not str(filepath).startswith(str(event_dir.resolve())):
        return jsonify({"error": "not found"}), 404
    if not filepath.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(str(filepath))


@app.route("/api/bugs")
def bugs_list():
    rows = _db().execute("""
        SELECT id, title, description, repo, status, commit_hash, discovered_at, resolved_at
        FROM bug_tracker
        ORDER BY (status = 'open') DESC, discovered_at DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/bugs", methods=["POST"])
def bugs_create():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    description = (data.get("description") or "").strip() or None
    repo = (data.get("repo") or "watson").strip()

    db = _db()
    cur = db.execute(
        "INSERT INTO bug_tracker (title, description, repo) VALUES (?, ?, ?)",
        (title, description, repo),
    )
    db.commit()
    row = db.execute("SELECT * FROM bug_tracker WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/bugs/<int:bug_id>", methods=["PATCH"])
def bugs_update(bug_id):
    data = request.get_json(force=True) or {}
    db = _db()
    row = db.execute("SELECT * FROM bug_tracker WHERE id = ?", (bug_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    if data.get("status") == "resolved":
        commit_hash = (data.get("commit_hash") or "").strip()
        if not commit_hash:
            return jsonify({"error": "commit_hash is required to mark a bug resolved"}), 400
        db.execute(
            "UPDATE bug_tracker SET status = 'resolved', commit_hash = ?, resolved_at = datetime('now') WHERE id = ?",
            (commit_hash, bug_id),
        )
    elif data.get("status") == "open":
        db.execute(
            "UPDATE bug_tracker SET status = 'open', commit_hash = NULL, resolved_at = NULL WHERE id = ?",
            (bug_id,),
        )
    db.commit()
    row = db.execute("SELECT * FROM bug_tracker WHERE id = ?", (bug_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/bugs/<int:bug_id>", methods=["DELETE"])
def bugs_delete(bug_id):
    db = _db()
    db.execute("DELETE FROM bug_tracker WHERE id = ?", (bug_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/dev/vps-cost-estimate")
def dev_vps_cost_estimate():
    from jobs.dev.vps_cost_estimate import build_estimate
    return jsonify(build_estimate())


@app.route("/api/fixes")
def fixes_list():
    from jobs.dev.fix_log import recent_fixes
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(recent_fixes(limit))


@app.route("/api/network-devices")
def network_devices_list():
    from jobs.network_monitor.db import all_devices, init_db, online_cutoff
    init_db()
    cutoff = online_cutoff()
    devices = []
    for r in all_devices():
        d = dict(r)
        d["online"] = bool(d["last_seen"] and d["last_seen"] >= cutoff)
        devices.append(d)
    return jsonify(devices)


@app.route("/api/network-devices/<mac>", methods=["PATCH"])
def network_devices_update(mac):
    from jobs.network_monitor.db import update_device
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip() or None
    assigned_to = (data.get("assigned_to") or "").strip() or None
    update_device(mac, label, assigned_to)
    return jsonify({"ok": True})


@app.route("/api/network-devices/<mac>", methods=["DELETE"])
def network_devices_delete(mac):
    from jobs.network_monitor.db import delete_device
    delete_device(mac)
    return jsonify({"ok": True})


@app.route("/api/network-devices/<mac>/sessions")
def network_devices_sessions(mac):
    from jobs.network_monitor.db import device_sessions
    return jsonify(device_sessions(mac))


@app.route("/api/house-calls")
def house_calls_list():
    from jobs.house_calls.db import init_db, all_calls
    init_db()
    return jsonify([dict(r) for r in all_calls()])


@app.route("/api/house-calls/<int:call_id>/paid", methods=["PATCH"])
def house_calls_set_paid(call_id):
    from jobs.house_calls.db import mark_paid, mark_unpaid
    data = request.get_json(force=True) or {}
    if data.get("paid"):
        mark_paid([call_id])
    else:
        mark_unpaid([call_id])
    return jsonify({"ok": True})


@app.route("/api/house-calls/<int:call_id>", methods=["PATCH"])
def house_calls_update(call_id):
    from jobs.house_calls.db import update_call
    data = request.get_json(force=True) or {}
    family_last_name = data.get("family_last_name")
    if family_last_name is not None:
        family_last_name = family_last_name.strip()
        if not family_last_name:
            return jsonify({"error": "family_last_name cannot be blank"}), 400
    update_call(
        call_id,
        family_last_name=family_last_name,
        called_at=data.get("called_at"),
        amount=data.get("amount"),
        notes=data.get("notes"),
    )
    return jsonify({"ok": True})


@app.route("/api/house-calls/<int:call_id>", methods=["DELETE"])
def house_calls_delete(call_id):
    from jobs.house_calls.db import delete_call
    delete_call(call_id)
    return jsonify({"ok": True})


@app.route("/api/project-backlog")
def project_backlog_list():
    rows = _db().execute("""
        SELECT id, title, summary, detail, status, added_date
        FROM project_backlog
        ORDER BY (status = 'planned') DESC, added_date DESC, id DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/project-backlog", methods=["POST"])
def project_backlog_create():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    summary = (data.get("summary") or "").strip()
    detail = (data.get("detail") or "").strip() or None

    from jobs.dev.backlog import create_backlog_item
    new_id = create_backlog_item(title, summary, detail)
    row = _db().execute("SELECT * FROM project_backlog WHERE id = ?", (new_id,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/project-backlog/<int:item_id>", methods=["PATCH"])
def project_backlog_update(item_id):
    data = request.get_json(force=True) or {}
    db = _db()
    row = db.execute("SELECT * FROM project_backlog WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    fields, params = [], []
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        fields.append("title = ?")
        params.append(title)
    if "summary" in data:
        fields.append("summary = ?")
        params.append((data.get("summary") or "").strip())
    if "detail" in data:
        fields.append("detail = ?")
        params.append((data.get("detail") or "").strip() or None)
    if "status" in data:
        status = (data.get("status") or "").strip()
        if status not in ("planned", "done"):
            return jsonify({"error": "status must be 'planned' or 'done'"}), 400
        fields.append("status = ?")
        params.append(status)

    if fields:
        params.append(item_id)
        db.execute(f"UPDATE project_backlog SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM project_backlog WHERE id = ?", (item_id,)).fetchone()
    return jsonify(dict(row))


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


# ── Commands API ──────────────────────────────────────────────────────────────

@app.route("/api/commands")
def commands_list_api():
    if not COMMANDS_FILE.exists():
        return jsonify([])
    try:
        commands = json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))
        if not isinstance(commands, list):
            return jsonify([])
        # TEMPORARY diagnostic logging for the missing-commands investigation
        # (2026-07-29) — remove once root cause is confirmed from live traffic.
        try:
            _log_commands_debug(commands)
        except Exception:
            pass
        return jsonify(commands)
    except Exception:
        return jsonify([])


@app.route("/api/directives")
def directives_list_api():
    """Canonical colon-prefix directives available on the dashboard, straight
    from jobs/routing/directive_prefixes.py — backs the chat directive
    dropdown so it can't drift from the registry (2026-07-29)."""
    prefixes = [p for p, cfg in DIRECTIVE_PREFIXES.items() if cfg["dashboard"]]
    return jsonify(prefixes)


def _log_commands_debug(commands):
    names = {c.get("name") for c in commands if isinstance(c, dict)}
    watch = ["Debug loop", "Log a bug", "Run a skill by slug", "Expanded KB search"]
    log_path = Path(__file__).parents[2] / "logs" / "commands_debug.log"
    entry = {
        "ts": datetime.now().isoformat(),
        "remote_addr": request.remote_addr,
        "x_forwarded_for": request.headers.get("X-Forwarded-For"),
        "user_agent": request.headers.get("User-Agent"),
        "all_headers": dict(request.headers),
        "count": len(commands),
        "present": {w: (w in names) for w in watch},
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


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


@app.route("/api/skills/kb", methods=["POST"])
def skill_kb():
    from jobs.skills.kb_search import search_kb, format_result
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    query = text
    sermons_only = query.lower().startswith("xkb:")
    for prefix in ("search the kb:", "xkb:", "kb:"):
        if query.lower().startswith(prefix):
            query = query[len(prefix):].strip()
            break
    if not query:
        return jsonify({"error": "No query provided"}), 400
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(search_kb, query, "sermons", sermons_only).result()
        return jsonify({"result": format_result(result), "query": result["query"]})
    except Exception as exc:
        log.error("KB search error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/skills/kb/email", methods=["POST"])
def skill_kb_email():
    data = request.get_json(force=True) or {}
    query = (data.get("query") or "").strip()
    synopsis = (data.get("synopsis") or "").strip()
    sources = data.get("sources") or []
    if not synopsis:
        return jsonify({"error": "No synopsis provided"}), 400
    sources_str = "\n".join(f"• {s}" for s in sources)
    body = f"{synopsis}\n\nSources:\n{sources_str}"
    try:
        from jobs.email_job.brevo_send import send_email
        result = send_email(
            to_email="pastorbill@catalyst302.com",
            to_name="Bill Yomes",
            subject=f"KB Search: {query}",
            text_body=body,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("KB email error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Report API ────────────────────────────────────────────────────────────────

@app.route("/api/report", methods=["POST"])
def report_run():
    data = request.get_json(force=True) or {}
    rtype = (data.get("type") or "").strip().lower()
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    try:
        if rtype == "cdb":
            from jobs.skills.cdb_query import run as _cdb_run
            return jsonify({"result": _cdb_run(query) or "No results."})
        elif rtype == "wdb":
            from jobs.skills.wdb_query import run as _wdb_run
            return jsonify({"result": _wdb_run(query) or "No results."})
        else:
            return jsonify({"error": f"Unknown report type: {rtype}"}), 400
    except Exception as exc:
        log.error("Report API error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reports/state-of-church", methods=["POST"])
def reports_state_of_church():
    import subprocess, threading
    def _run():
        subprocess.run(
            ["venv/bin/python", "-m", "jobs.connect_cards.state_of_church"],
            cwd="/home/billyomes/watson",
            env={**os.environ, "PYTHONPATH": "/home/billyomes/watson"}
        )
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


# ── Memory API ───────────────────────────────────────────────────────────────

@app.route("/api/memory/recent")
def memory_recent():
    rows = _db().execute(
        "SELECT summary FROM memory_sessions ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return jsonify([r["summary"] for r in rows])


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

        # Time query
        if _siri_re.search(r"what.*(time|hour).*is it|what time|current time", msg_lower):
            from jobs.time_check import run as _time_run
            return _reply(_time_run())

        # Identity / factual routing; everything else (including
        # conversational messages) goes through the skill router --
        # _is_conversational is not special-cased into a direct bypass here
        # (it silently skips short skill-trigger phrases like "polish this:
        # hello").
        _identity = _siri_router._is_identity_query(msg)
        _factual = _siri_router._is_factual_query(msg)

        if _identity:
            route_result = {"action": "chat"}
        elif _factual:
            from jobs.research.web_search import run as web_search_run
            return _reply("✓ " + web_search_run(msg))
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
            return _reply("Building that skill now — this'll take a few minutes. I'll notify you via Telegram when it's ready; other requests may be delayed until it's done.")

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
            return _reply("Building that skill now — this'll take a few minutes. I'll notify you via Telegram when it's ready; other requests may be delayed until it's done.")

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
    text = f"\U0001f6ab Marked rest of day as busy. {count} appointment(s) affected."
    if vacation_gate("normal", "jobs.dashboard.app.calendar_busy_rest_of_day", text):
        return jsonify({"ok": True})
    try:
        _req.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": WATSON_CHAT_ID,
                "text": text,
            },
            timeout=10,
        )
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/calendar/today")
def calendar_today():
    from jobs.gcal.gcal_service import get_next_36h_events
    try:
        events = get_next_36h_events()
        return jsonify(events)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Appointments API ──────────────────────────────────────────────────────────

@app.route("/api/book-appointment", methods=["POST"])
def book_appointment():
    key = request.headers.get("X-Watson-Key", "")
    if not key or key != os.getenv("WRITING_ROOM_API_KEY"):
        return jsonify({"error": "unauthorized"}), 401
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
    from jobs.email_job.brevo_send import send_email

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
    email_body = (
        f"Hi {first_name},\n\n"
        "Your appointment with Dr. Bill Yomes has been cancelled.\n\n"
        "To book a new appointment, visit:\n"
        "williamckyomes.com/meet"
    )
    result = send_email(
        to_email=row["guest_email"], to_name=guest_name,
        subject="Your Appointment Has Been Cancelled",
        text_body=email_body,
    )
    if not result["success"]:
        log.error("cancel_appointment: failed to send email to %s: %s", row["guest_email"], result["error"])

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
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py


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
        # '--' (the blank-value convention) must fall back to Wilmington
        # too, same as None/blank/empty.
        campus = row["campus_preference"] if row["campus_preference"] not in (None, "", "--") else "Wilmington"
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


# ── Deacon Reports (manual send only — never on a cron; a report is only
#    ever sent after the dashboard shows a preview and the button is clicked) ──

def _strip_body(html: str) -> str:
    import re as _re_body
    m = _re_body.search(r'<body[^>]*>(.*?)</body>', html, _re_body.DOTALL)
    return m.group(1) if m else html


@app.route("/api/deacon-reports/list")
def deacon_reports_list():
    from jobs.congregation.deacon_reports import deacon_counts
    try:
        return jsonify({"deacons": deacon_counts()})
    except Exception as exc:
        log.error("deacon-reports/list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/preview-master")
def deacon_reports_preview_master():
    from jobs.congregation.deacon_reports import generate_master_shepherding_report
    try:
        _, html = generate_master_shepherding_report()
        return jsonify({"html": _strip_body(html)})
    except Exception as exc:
        log.error("deacon-reports/preview-master failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/preview-unassigned")
def deacon_reports_preview_unassigned():
    from jobs.congregation.deacon_reports import generate_unassigned_report
    try:
        _, html = generate_unassigned_report()
        return jsonify({"html": _strip_body(html)})
    except Exception as exc:
        log.error("deacon-reports/preview-unassigned failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/preview-pastor-list")
def deacon_reports_preview_pastor_list():
    from jobs.congregation.deacon_reports import generate_pastor_list_report
    try:
        _, html = generate_pastor_list_report()
        return jsonify({"html": _strip_body(html)})
    except Exception as exc:
        log.error("deacon-reports/preview-pastor-list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/preview/<path:deacon_name>")
def deacon_reports_preview_one(deacon_name):
    from jobs.congregation.deacon_reports import generate_deacon_report
    try:
        _, html = generate_deacon_report(deacon_name)
        return jsonify({"html": _strip_body(html)})
    except Exception as exc:
        log.error("deacon-reports/preview failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/send-master", methods=["POST"])
def deacon_reports_send_master():
    from jobs.congregation.deacon_reports import send_master_shepherding_report
    data = request.get_json(force=True) or {}
    try:
        sent_to = send_master_shepherding_report(
            also_to_bill=bool(data.get("also_bill")),
            also_to_elders=bool(data.get("also_elders")),
        )
        return jsonify({"message": f"Master Shepherding Report sent to {', '.join(sent_to)} ✓"})
    except Exception as exc:
        log.error("deacon-reports/send-master failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/send-unassigned", methods=["POST"])
def deacon_reports_send_unassigned():
    from jobs.congregation.deacon_reports import send_unassigned_report
    try:
        sent_to = send_unassigned_report()
        return jsonify({"message": f"Unassigned report sent to {sent_to} ✓"})
    except Exception as exc:
        log.error("deacon-reports/send-unassigned failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/send-pastor-list", methods=["POST"])
def deacon_reports_send_pastor_list():
    from jobs.congregation.deacon_reports import send_pastor_list_report
    try:
        sent_to = send_pastor_list_report()
        return jsonify({"message": f"Pastor Bill's List sent to {sent_to} ✓"})
    except Exception as exc:
        log.error("deacon-reports/send-pastor-list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/deacon-reports/send/<path:deacon_name>", methods=["POST"])
def deacon_reports_send_one(deacon_name):
    from jobs.congregation.deacon_reports import send_deacon_report
    try:
        sent_to = send_deacon_report(deacon_name)
        return jsonify({"message": f"Deacon Report sent to {deacon_name} ({sent_to}) ✓"})
    except Exception as exc:
        log.error("deacon-reports/send failed: %s", exc)
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
            content = f"[{report_type.replace('_', ' ').title()}: last {weeks} weeks]\n\nReport generation for this type is not yet implemented."
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
        header = f"*{label}*" + (f": {weeks}w" if weeks else "")
        _send_telegram(f"{header}\n\n{content}")
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("reports/telegram failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reports/email", methods=["POST"])
def reports_email():
    from jobs.email_job.brevo_send import send_email
    data    = request.get_json(force=True) or {}
    rtype   = data.get("type", "report")
    weeks   = data.get("weeks", "")
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "content required"}), 400
    try:
        to_addr = "bill.yomes@gmail.com"
        label   = rtype.replace("_", " ").title()
        subject = f"Watson Report: {label}" + (f" ({weeks}w)" if weeks else "")
        result = send_email(
            to_email=to_addr,
            to_name="Bill Yomes",
            subject=subject,
            text_body=content,
        )
        if not result["success"]:
            raise RuntimeError(result["error"])
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
                json={"model": "qwen2.5:7b", "messages": [{"role": "system", "content": system}] + messages_payload, "stream": True, "num_predict": 400},
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


# ── Location API ─────────────────────────────────────────────────────────────

@app.route("/api/location", methods=["POST"])
def location_intake():
    key = request.headers.get("X-Watson-Key", "")
    if not key or key != os.getenv("WRITING_ROOM_API_KEY"):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    lat = data.get("lat")
    lon = data.get("lon")
    timestamp = data.get("timestamp")
    if lat is None or lon is None or not timestamp:
        return jsonify({"error": "lat, lon, and timestamp are required"}), 400
    _db().execute(
        "INSERT INTO location_log (lat, lon, timestamp) VALUES (?, ?, ?)",
        (float(lat), float(lon), timestamp),
    )
    _db().commit()
    return jsonify({"status": "ok"})


# ── Logins / Vault API ────────────────────────────────────────────────────────

# Module-level challenge store (single-user system)
_active_challenge: dict = {"id": None, "response": None}


def lock_vault() -> None:
    """Lock the vault, record timestamp, and send Telegram alert with Unlock button."""
    import sqlite3 as _sq
    with _sq.connect(DB) as _c:
        _c.execute(
            "UPDATE vault_status SET locked = 1, locked_at = datetime('now') WHERE id = 1"
        )
    try:
        if vacation_gate("system_failure", "jobs.dashboard.app.lock_vault", "vault locked — 3 failed attempts"):
            return
        import requests as _rq
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        token   = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Unlock Vault", callback_data="vault_unlock"),
        ]])
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "⚠️ Login vault locked: 3 failed attempts on dashboard.",
                "reply_markup": keyboard.to_dict(),
            },
            timeout=10,
        )
    except Exception as exc:
        log.error("lock_vault telegram notify failed: %s", exc)


# Vault password field is encrypted at rest with a Fernet key (WATSON_VAULT_KEY in
# .env, added 2026-09-16 after a security review found the `logins` table stored
# passwords in plaintext with no auth on the read/unlock routes). All /api/logins*
# routes require an admin session on top of that — the old "vault lock" boolean
# alone was bypassable via a direct POST to /api/logins/unlock.
_VAULT_KEY = os.getenv("WATSON_VAULT_KEY")
_vault_fernet = Fernet(_VAULT_KEY.encode()) if _VAULT_KEY else None


def _encrypt_vault_password(plain):
    if not plain or not _vault_fernet:
        return plain
    return _vault_fernet.encrypt(plain.encode()).decode()


def _decrypt_vault_password(value):
    if not value or not _vault_fernet:
        return value
    try:
        return _vault_fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        # Pre-migration plaintext row, or key mismatch — surface as-is rather than 500.
        return value


def _decrypt_login_row(row):
    d = dict(row)
    if "password" in d:
        d["password"] = _decrypt_vault_password(d["password"])
    return d


@app.route("/api/logins/status")
def logins_status():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    row = _db().execute("SELECT locked FROM vault_status WHERE id = 1").fetchone()
    locked = bool(row["locked"]) if row else False
    return jsonify({"locked": locked})


@app.route("/api/logins/challenge")
def logins_challenge():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    exclude = request.args.get("exclude", type=int)
    db = _db()
    if exclude is not None:
        rows = db.execute(
            "SELECT id, challenge, response FROM login_challenges WHERE id != ?", (exclude,)
        ).fetchall()
    else:
        rows = db.execute("SELECT id, challenge, response FROM login_challenges").fetchall()
    if not rows:
        # fallback: pick any
        rows = db.execute("SELECT id, challenge, response FROM login_challenges").fetchall()
    if not rows:
        return jsonify({"error": "no challenges configured"}), 500
    import random
    row = random.choice(rows)
    _active_challenge["id"] = row["id"]
    _active_challenge["response"] = row["response"]
    return jsonify({"id": row["id"], "challenge": row["challenge"]})


@app.route("/api/logins/challenge/verify", methods=["POST"])
def logins_challenge_verify():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    response = (data.get("response") or "").strip().lower()
    stored   = (_active_challenge.get("response") or "").strip().lower()
    if not stored:
        return jsonify({"success": False, "error": "no active challenge"})
    if response == stored:
        _active_challenge["id"] = None
        _active_challenge["response"] = None
        return jsonify({"success": True})
    return jsonify({"success": False})


@app.route("/api/logins/unlock", methods=["POST"])
def logins_unlock():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    _db().execute("UPDATE vault_status SET locked = 0, locked_at = NULL WHERE id = 1")
    _db().commit()
    return jsonify({"ok": True})


@app.route("/api/logins/lock", methods=["POST"])
def logins_lock():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    lock_vault()
    return jsonify({"ok": True})


@app.route("/api/logins")
def logins_list():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    row = _db().execute("SELECT locked FROM vault_status WHERE id = 1").fetchone()
    if row and row["locked"]:
        return jsonify({"locked": True})
    rows = _db().execute(
        "SELECT id, label, username, password, url, notes, created_at, updated_at "
        "FROM logins ORDER BY label COLLATE NOCASE"
    ).fetchall()
    return jsonify([_decrypt_login_row(r) for r in rows])


@app.route("/api/logins", methods=["POST"])
def logins_create():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label required"}), 400
    cur = _db().execute(
        "INSERT INTO logins (label, username, password, url, notes) VALUES (?, ?, ?, ?, ?)",
        (label, data.get("username") or None, _encrypt_vault_password(data.get("password") or None),
         data.get("url") or None, data.get("notes") or None),
    )
    _db().commit()
    row = _db().execute("SELECT * FROM logins WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(_decrypt_login_row(row)), 201


@app.route("/api/logins/<int:login_id>", methods=["PUT"])
def logins_update(login_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    allowed = {"label", "username", "password", "url", "notes"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    if "password" in fields:
        fields["password"] = _encrypt_vault_password(fields["password"])
    set_clause = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now')"
    _db().execute(
        f"UPDATE logins SET {set_clause} WHERE id = ?", (*fields.values(), login_id)
    )
    _db().commit()
    row = _db().execute("SELECT * FROM logins WHERE id = ?", (login_id,)).fetchone()
    return jsonify(_decrypt_login_row(row) if row else {"error": "not found"})


@app.route("/api/logins/<int:login_id>", methods=["DELETE"])
def logins_delete(login_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    _db().execute("DELETE FROM logins WHERE id = ?", (login_id,))
    _db().commit()
    return jsonify({"ok": True})


# ── Status API ────────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"current_time": datetime.now().isoformat()})


# ── Admin ─────────────────────────────────────────────────────────────────────


def recalculate_team_status():
    """Recalculate and write status for all active team members."""
    from datetime import date as _date2
    today = _date2.today()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        members = conn.execute(
            "SELECT id, last_activity_date, last_comms_date FROM team_members WHERE active=1"
        ).fetchall()
        for m in members:
            dates = [d for d in [m["last_activity_date"], m["last_comms_date"]] if d]
            if not dates:
                status = "stalled"
            else:
                last_str = max(dates)
                try:
                    last_dt = _date2.fromisoformat(last_str[:10])
                    days_ago = (today - last_dt).days
                except Exception:
                    days_ago = 999
                if days_ago <= 7:
                    status = "active"
                elif days_ago <= 14:
                    status = "needs_attention"
                else:
                    status = "stalled"
                if status == "active":
                    overdue = conn.execute(
                        "SELECT COUNT(*) FROM team_tasks WHERE member_id=? AND status='open' AND due_date < ?",
                        (m["id"], today.isoformat()),
                    ).fetchone()[0]
                    if overdue > 0:
                        status = "needs_attention"
            conn.execute("UPDATE team_members SET status=? WHERE id=?", (status, m["id"]))
        conn.commit()
    except Exception as exc:
        log.error("recalculate_team_status error: %s", exc)
    finally:
        conn.close()


def _admin_required():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    return None


@app.route("/admin/login", methods=["GET"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_index"))
    return render_template("admin_login.html", error=None)


@app.route("/admin/login", methods=["POST"])
def admin_login_post():
    from werkzeug.security import check_password_hash
    username = (request.form.get("username") or "").strip().lower()
    password = (request.form.get("password") or "").strip()
    db = _db()
    row = db.execute(
        "SELECT password_hash FROM admin_users WHERE username=?", (username,)
    ).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        session.permanent = True
        session["admin_logged_in"] = True
        session["admin_user"] = username
        return redirect(url_for("admin_index"))
    return render_template("admin_login.html", error="Invalid credentials.")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_user", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin_index():
    redir = _admin_required()
    if redir:
        return redir
    recalculate_team_status()
    db = _db()
    try:
        rows = db.execute("""
            SELECT m.id, m.name, m.role, m.ministry, m.email,
                   COALESCE(m.status, 'stalled') AS status,
                   m.last_activity_date, m.last_comms_date,
                   COUNT(CASE WHEN m.id != 12 OR t.category = 'catalyst' THEN t.id END) AS open_task_count
            FROM team_members m
            LEFT JOIN team_tasks t ON t.member_id = m.id AND t.status = 'open'
            WHERE m.active = 1
            GROUP BY m.id
            ORDER BY m.name COLLATE NOCASE
        """).fetchall()
        members = [dict(r) for r in rows]
    except Exception as exc:
        log.error("admin_index DB error: %s", exc)
        members = []
    total = len(members)
    active = sum(1 for m in members if m["status"] == "active")
    needs_attention = sum(1 for m in members if m["status"] == "needs_attention")
    open_tasks = sum(m["open_task_count"] for m in members)
    return render_template(
        "admin.html",
        members=members,
        stats={"total": total, "active": active, "needs_attention": needs_attention, "open_tasks": open_tasks},
        admin_user=session.get("admin_user", "donna"),
    )


@app.route("/admin/leader/<int:member_id>")
def admin_leader(member_id):
    redir = _admin_required()
    if redir:
        return redir
    db = _db()
    member = db.execute("SELECT * FROM team_members WHERE id=?", (member_id,)).fetchone()
    if not member:
        return jsonify({"error": "not found"}), 404
    # A task stays visible for 12h after being checked off (completed_at set),
    # then drops out of this list — row is kept, not deleted, for history/reporting.
    _active_or_recent = (
        "(status NOT IN ('done','completed') "
        "OR (completed_at IS NOT NULL AND completed_at > datetime('now', '-12 hours')))"
    )
    if member_id == 12:
        tasks = db.execute(
            f"SELECT * FROM team_tasks WHERE member_id=? AND category='catalyst' AND {_active_or_recent} "
            "ORDER BY CAST(priority AS INTEGER) ASC, due_date ASC",
            (member_id,),
        ).fetchall()
    else:
        tasks = db.execute(
            f"SELECT * FROM team_tasks WHERE member_id=? AND {_active_or_recent} "
            "ORDER BY CAST(priority AS INTEGER) ASC, due_date ASC",
            (member_id,),
        ).fetchall()
    try:
        notes = db.execute(
            "SELECT * FROM pastoral_notes WHERE team_member_id=? AND note_type != 'private' ORDER BY created_at DESC",
            (member_id,),
        ).fetchall()
    except Exception:
        notes = []
    messages = db.execute(
        "SELECT * FROM team_messages WHERE member_id=? ORDER BY COALESCE(sent_at, created_at) DESC LIMIT 20",
        (member_id,),
    ).fetchall()
    try:
        shared_notes = db.execute(
            "SELECT id, content, author, created_at FROM shared_notes "
            "WHERE member_id=? ORDER BY created_at DESC",
            (member_id,),
        ).fetchall()
    except Exception:
        shared_notes = []
    return jsonify({
        "member":       dict(member),
        "tasks":        [dict(r) for r in tasks],
        "notes":        [dict(r) for r in notes],
        "messages":     [dict(r) for r in messages],
        "shared_notes": [dict(r) for r in shared_notes],
    })


def _create_team_task(member_id: int, title: str, source: str, due_date: str | None = None,
                       category: str = "catalyst", priority: str = "3") -> int:
    """Shared team_tasks insert, extracted from admin_task() (the Home
    dashboard / Team tab "add task" route) so meet_review_send()'s
    auto-task-creation for elder-review action items (jobs/dashboard/app.py)
    reuses the exact same insert path rather than a second one. Does not
    commit — callers control the transaction boundary."""
    today = datetime.now().date().isoformat()
    db = _db()
    cur = db.execute(
        "INSERT INTO team_tasks (member_id, title, due_date, source, status, category, priority) VALUES (?,?,?,?,?,?,?)",
        (member_id, title, due_date, source, "open", category, priority),
    )
    db.execute(
        "UPDATE team_members SET last_activity_date=? WHERE id=?",
        (today, member_id),
    )
    return cur.lastrowid


@app.route("/admin/task", methods=["POST"])
def admin_task():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    member_id = data.get("team_member_id") or data.get("member_id")
    title = (data.get("title") or "").strip()
    if not member_id or not title:
        return jsonify({"error": "team_member_id and title required"}), 400
    task_id = _create_team_task(
        member_id, title,
        source=session.get("admin_user", "donna"),
        due_date=data.get("due_date") or None,
    )
    _db().commit()
    return jsonify({"success": True, "task_id": task_id})


@app.route("/admin/task/reassign", methods=["POST"])
def admin_task_reassign():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    task_id      = data.get("task_id")
    new_member_id = data.get("new_member_id")
    if not task_id or not new_member_id:
        return jsonify({"error": "task_id and new_member_id required"}), 400
    db = _db()
    task = db.execute("SELECT title, member_id, category FROM team_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "task not found"}), 404
    if (task["category"] or "catalyst") != "catalyst":
        return jsonify({"error": "reassignment only allowed on Catalyst tasks"}), 403
    new_member = db.execute("SELECT name FROM team_members WHERE id=?", (new_member_id,)).fetchone()
    if not new_member:
        return jsonify({"error": "member not found"}), 404
    old_member_id = task["member_id"]
    today = datetime.now().date().isoformat()
    db.execute("UPDATE team_tasks SET member_id=? WHERE id=?", (new_member_id, task_id))
    db.execute("UPDATE team_members SET last_activity_date=? WHERE id=?", (today, old_member_id))
    db.execute("UPDATE team_members SET last_activity_date=? WHERE id=?", (today, new_member_id))
    db.commit()
    try:
        _send_telegram(
            f"\U0001f504 Task reassigned by {'Dr. Bill' if session.get('admin_user') == 'drbill' else 'Donna'}: '{task['title']}' → {new_member['name']}"
        )
    except Exception as exc:
        log.warning("Telegram notify failed for task reassign: %s", exc)
    return jsonify({"success": True})


@app.route("/admin/note", methods=["POST"])
def admin_note():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    member_id = data.get("team_member_id") or data.get("member_id")
    content = (data.get("content") or "").strip()
    if not member_id or not content:
        return jsonify({"error": "team_member_id and content required"}), 400
    today = datetime.now().date().isoformat()
    db = _db()
    member = db.execute("SELECT name FROM team_members WHERE id=?", (member_id,)).fetchone()
    person_name = member["name"] if member else "Unknown"
    db.execute(
        "INSERT INTO pastoral_notes (person_name, note, team_member_id, note_type, content, created_by) "
        "VALUES (?, ?, ?, 'team', ?, ?)",
        (person_name, content, member_id, content, session.get("admin_user", "donna")),
    )
    db.execute(
        "UPDATE team_members SET last_activity_date=? WHERE id=?",
        (today, member_id),
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/admin/notes", methods=["POST"])
def admin_notes_create():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    content = (data.get("content") or "").strip()
    if not member_id or not content:
        return jsonify({"error": "member_id and content required"}), 400
    today = datetime.now().date().isoformat()
    db = _db()
    cur = db.execute(
        "INSERT INTO shared_notes (member_id, content, author) VALUES (?, ?, ?)",
        (member_id, content, session.get("admin_user", "donna")),
    )
    db.execute(
        "UPDATE team_members SET last_activity_date=? WHERE id=?",
        (today, member_id),
    )
    db.commit()
    row = db.execute(
        "SELECT id, content, author, created_at FROM shared_notes WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify({"success": True, "note": dict(row)})


@app.route("/admin/notes/<int:note_id>", methods=["DELETE"])
def admin_notes_delete(note_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    db = _db()
    note = db.execute("SELECT id FROM shared_notes WHERE id=?", (note_id,)).fetchone()
    if not note:
        return jsonify({"error": "not found"}), 404
    db.execute("DELETE FROM shared_notes WHERE id=?", (note_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/pastoral_notes/inline", methods=["POST"])
def pastoral_notes_inline():
    import json as _json
    data = request.get_json(force=True) or {}
    pending_id = data.get("pending_id")
    content = (data.get("content") or "").strip()
    if not pending_id or not content:
        return jsonify({"error": "pending_id and content required"}), 400
    db = _db()
    row = db.execute(
        "SELECT id, payload FROM tg_pending_actions WHERE id=? AND status='pending'",
        (pending_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    payload = _json.loads(row["payload"] or "{}")
    notes_pending_id = payload.get("notes_pending_id")
    member_id = payload.get("member_id")
    person_name = "Unknown"
    if notes_pending_id:
        np_row = db.execute(
            "SELECT appointment_title FROM notes_pending WHERE id=?", (notes_pending_id,)
        ).fetchone()
        if np_row:
            person_name = np_row["appointment_title"]
    if member_id:
        m_row = db.execute("SELECT name FROM team_members WHERE id=?", (member_id,)).fetchone()
        if m_row:
            person_name = m_row["name"]
    note_type = (data.get("note_type") or "pastoral").strip()
    if note_type == "leadership":
        db.execute(
            "INSERT INTO shared_notes (member_id, content, author) VALUES (?, ?, 'bill')",
            (member_id or 0, content),
        )
        if member_id:
            today = datetime.now().strftime("%Y-%m-%d")
            db.execute(
                "UPDATE team_members SET last_activity_date=? WHERE id=?",
                (today, member_id),
            )
    else:
        db.execute(
            "INSERT INTO pastoral_notes (person_name, note, team_member_id, note_type, content, created_by) "
            "VALUES (?, ?, ?, 'private', ?, 'bill')",
            (person_name, content, member_id, content),
        )
    db.execute("UPDATE tg_pending_actions SET status='done' WHERE id=?", (pending_id,))
    if notes_pending_id:
        db.execute("UPDATE notes_pending SET status='resolved' WHERE id=?", (notes_pending_id,))
    db.commit()
    try:
        if note_type == "leadership":
            _send_telegram(f"📋 Leadership note saved for {person_name}.")
        else:
            _send_telegram(f"✓ Pastoral note saved for {person_name}.")
    except Exception:
        pass
    return jsonify({"success": True})


@app.route("/api/pastoral_notes/skip", methods=["POST"])
def pastoral_notes_skip():
    import json as _json
    data = request.get_json(force=True) or {}
    pending_id = data.get("pending_id")
    if not pending_id:
        return jsonify({"error": "pending_id required"}), 400
    db = _db()
    row = db.execute("SELECT payload FROM tg_pending_actions WHERE id=?", (pending_id,)).fetchone()
    if row:
        try:
            payload = _json.loads(row["payload"] or "{}")
            notes_pending_id = payload.get("notes_pending_id")
            if notes_pending_id:
                db.execute("UPDATE notes_pending SET status='skipped' WHERE id=?", (notes_pending_id,))
        except Exception:
            pass
    db.execute("UPDATE tg_pending_actions SET status='skipped' WHERE id=?", (pending_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/pastoral_notes/delete", methods=["POST"])
def pastoral_notes_delete_pending():
    import json as _json
    data = request.get_json(force=True) or {}
    pending_id = data.get("pending_id")
    if not pending_id:
        return jsonify({"error": "pending_id required"}), 400
    db = _db()
    row = db.execute("SELECT payload FROM tg_pending_actions WHERE id=?", (pending_id,)).fetchone()
    if row:
        try:
            payload = _json.loads(row["payload"] or "{}")
            notes_pending_id = payload.get("notes_pending_id")
            if notes_pending_id:
                db.execute("DELETE FROM notes_pending WHERE id=?", (notes_pending_id,))
        except Exception:
            pass
    db.execute("DELETE FROM tg_pending_actions WHERE id=?", (pending_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/admin/task/priority", methods=["POST"])
def admin_task_priority():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    task_id  = data.get("task_id")
    priority = data.get("priority")
    if not task_id or priority not in ("1", "2", "3", "4", "5"):
        return jsonify({"error": "task_id and valid priority required"}), 400
    db = _db()
    task = db.execute("SELECT title, category FROM team_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "task not found"}), 404
    db.execute("UPDATE team_tasks SET priority=? WHERE id=?", (priority, task_id))
    db.commit()
    try:
        _send_telegram(
            f"\U0001f4cc Task priority updated by {'Dr. Bill' if session.get('admin_user') == 'drbill' else 'Donna'}: '{task['title']}' → {priority}"
        )
    except Exception:
        pass
    return jsonify({"success": True})


@app.route("/admin/task/due-date", methods=["POST"])
def admin_task_due_date():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    task_id  = data.get("task_id")
    due_date = data.get("due_date")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    if due_date is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(due_date)):
        return jsonify({"error": "invalid date format, expected YYYY-MM-DD"}), 400
    db = _db()
    task = db.execute("SELECT id FROM team_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "task not found"}), 404
    db.execute("UPDATE team_tasks SET due_date=? WHERE id=?", (due_date, task_id))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/team/tasks/<int:task_id>/title", methods=["PATCH"])
def api_task_title(task_id):
    data  = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    db   = _db()
    task = db.execute("SELECT member_id FROM team_tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["member_id"] != 12:
        return jsonify({"error": "not found"}), 404
    db.execute("UPDATE team_tasks SET title=? WHERE id=?", (title, task_id))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/team/tasks/<int:task_id>", methods=["DELETE"])
def admin_delete_task(task_id):
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    db = _db()
    task = db.execute("SELECT id FROM team_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "task not found"}), 404
    db.execute("DELETE FROM team_tasks WHERE id=?", (task_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/admin/member", methods=["POST"])
def admin_add_member():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    data = request.get_json(force=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name  = (data.get("last_name")  or "").strip()
    role       = (data.get("role")       or "").strip()
    ministry   = (data.get("ministry")   or "").strip()
    email      = (data.get("email")      or "").strip()
    phone      = (data.get("phone")      or "").strip() or None
    if not first_name or not last_name or not role or not ministry or not email:
        return jsonify({"error": "first_name, last_name, role, ministry, and email are required"}), 400
    name  = f"{first_name} {last_name}"
    today = datetime.now().date().isoformat()
    now   = datetime.now().isoformat(timespec="seconds")
    db = _db()
    cur = db.execute(
        """INSERT INTO team_members
               (name, email, phone, role, ministry, active, status, last_activity_date, created_at)
           VALUES (?, ?, ?, ?, ?, 1, 'active', ?, ?)""",
        (name, email, phone, role, ministry, today, now),
    )
    db.commit()
    member_id = cur.lastrowid
    try:
        _send_telegram(
            f"👤 New team member added by {'Dr. Bill' if session.get('admin_user') == 'drbill' else 'Donna'}: {name}, {role} ({ministry})"
        )
    except Exception as exc:
        log.warning("Telegram notify failed for new member: %s", exc)
    return jsonify({"success": True, "member_id": member_id})


@app.route("/admin/ministries")
def admin_ministries():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    db = _db()
    rows = db.execute(
        "SELECT DISTINCT ministry FROM team_members"
        " WHERE ministry IS NOT NULL AND ministry != ''"
        " ORDER BY ministry COLLATE NOCASE"
    ).fetchall()
    return jsonify({"ministries": [r["ministry"] for r in rows]})


@app.route("/admin/roster")
def admin_roster():
    redir = _admin_required()
    if redir:
        return jsonify({"error": "not authenticated"}), 401
    db = _db()
    rows = db.execute("""
        SELECT m.id, m.name, m.role, m.ministry, m.email,
               COALESCE(m.status, 'stalled') AS status,
               m.last_activity_date, m.last_comms_date,
               COUNT(CASE WHEN m.id != 12 OR t.category = 'catalyst' THEN t.id END) AS open_task_count
        FROM team_members m
        LEFT JOIN team_tasks t ON t.member_id = m.id AND t.status = 'open'
        WHERE m.active = 1
        GROUP BY m.id
        ORDER BY m.name COLLATE NOCASE
    """).fetchall()
    members = [dict(r) for r in rows]
    return jsonify({
        "members": members,
        "stats": {
            "total":            len(members),
            "active":           sum(1 for m in members if m["status"] == "active"),
            "needs_attention":  sum(1 for m in members if m["status"] == "needs_attention"),
            "open_tasks":       sum(m["open_task_count"] for m in members),
        },
    })


# ── Google Calendar OAuth ─────────────────────────────────────────────────────

_GCAL_CREDENTIALS = Path(__file__).resolve().parents[2] / "config" / "credentials.json"
_GCAL_TOKEN = Path(__file__).resolve().parents[2] / "config" / "token.json"
_GCAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
_GCAL_REDIRECT_URI = "https://watson.tail0243ff.ts.net/gcal-auth/callback"


@app.route("/gcal-auth")
def gcal_auth():
    import os, pathlib
    from requests_oauthlib import OAuth2Session
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    import json
    creds_data = json.loads(pathlib.Path(_GCAL_CREDENTIALS).read_text())["web"]
    oauth = OAuth2Session(
        client_id=creds_data["client_id"],
        redirect_uri=_GCAL_REDIRECT_URI,
        scope=["https://www.googleapis.com/auth/calendar"]
    )
    auth_url, state = oauth.authorization_url(
        creds_data["auth_uri"],
        access_type="offline",
        prompt="consent"
    )
    session["gcal_oauth_state"] = state
    session["gcal_client_id"] = creds_data["client_id"]
    session["gcal_client_secret"] = creds_data["client_secret"]
    return redirect(auth_url)

@app.route("/gcal-auth/callback")
def gcal_auth_callback():
    import os, json, pathlib
    from requests_oauthlib import OAuth2Session
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    creds_data = json.loads(pathlib.Path(_GCAL_CREDENTIALS).read_text())["web"]
    state = session.get("gcal_oauth_state")
    oauth = OAuth2Session(
        client_id=creds_data["client_id"],
        redirect_uri=_GCAL_REDIRECT_URI,
        state=state
    )
    token = oauth.fetch_token(
        creds_data["token_uri"],
        authorization_response=request.url.replace("http://", "https://"),
        client_secret=creds_data["client_secret"]
    )
    import json as _json
    _GCAL_TOKEN.write_text(_json.dumps({
        "token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "token_uri": creds_data["token_uri"],
        "client_id": creds_data["client_id"],
        "client_secret": creds_data["client_secret"],
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "expiry": None
    }))
    return "<html><body><p>Calendar authorized. Token saved.</p></body></html>"


@app.route("/robots.txt")
def robots_txt():
    # No robots.txt existed at all before this (confirmed live: plain 404 on
    # the Funnel domain) — added specifically to keep /docs/ out of crawl
    # attempts. The token itself is the actual security boundary; this is
    # just crawler etiquette on top of it.
    return Response("User-agent: *\nDisallow: /docs/\n", mimetype="text/plain")


# ── Docs (token-gated live current-state snapshot) ─────────────────────────

_DOCS_ARCH_FILE = Path.home() / "watson" / "memory" / "WATSON_ARCHITECTURE.md"
_DOCS_FILE_MAP_FILE = Path.home() / "watson" / "memory" / "FILE_MAP.md"


@app.route("/docs/<token>/current-state")
def docs_current_state(token):
    import hmac

    expected = os.getenv("WATSON_DOCS_TOKEN", "")
    # Constant-time compare, not == — and never compare against an empty
    # expected token (an unset WATSON_DOCS_TOKEN must never make an empty/
    # missing token "match"). abort(404) renders Flask's own generic 404 page
    # so this route is indistinguishable from one that doesn't exist at all.
    if not expected or not hmac.compare_digest(token, expected):
        abort(404)

    arch_text = _DOCS_ARCH_FILE.read_text()
    file_map_text = _DOCS_FILE_MAP_FILE.read_text()
    arch_mtime = datetime.fromtimestamp(_DOCS_ARCH_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    file_map_mtime = datetime.fromtimestamp(_DOCS_FILE_MAP_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    body = (
        "# WATSON CURRENT STATE — fetched live, do not treat as stale\n"
        f"Generated by Watson's nightly cron. Architecture last modified: {arch_mtime}. "
        f"File map last modified: {file_map_mtime}.\n\n"
        "---\n"
        f"{arch_text}\n"
        "---\n"
        f"{file_map_text}"
    )
    resp = Response(body, mimetype="text/plain")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # threaded=True: without it, Werkzeug's dev server handles one request
    # at a time -- any page that fires concurrent requests (e.g.
    # DeaconBoard.tsx's parallel roster+list fetch, each of which also
    # makes its own isToolLive() resolve call) can have one queue behind
    # another long enough that the caller gives up, which requireLiveTool.ts
    # fails closed on ("tool not live" 404) even though the tool is live.
    # Reproduced 2026-09-01 on /cat/deacons; safe to enable since
    # core/database.py's get_connection() already sets a busy_timeout for
    # concurrent SQLite access.
    # Bind to loopback only (security review 2026-09-16, revised 2026-09-16
    # after it broke every wtsn.me tool). The original fix bound this to the
    # Tailscale interface IP directly, which does close the LAN-exposure
    # hole (dashboard was on 0.0.0.0, reachable from the LAN with no OS
    # firewall enforcing the intended Tailscale-only boundary), but it also
    # broke `tailscale serve`/`funnel`, which proxies public and tailnet
    # traffic to http://127.0.0.1:5200 on this same host, not to the
    # Tailscale IP. Nothing listened on loopback anymore, so every request
    # through the Funnel URL (https://watson.tail0243ff.ts.net, including
    # wtsn.me's server-side isToolLive() checks) hit a connection refused
    # and came back as a 502 from serve/funnel's own reverse proxy.
    # Loopback-only still blocks direct LAN access (nothing on the LAN can
    # reach 127.0.0.1 on this box), and serve/funnel remains the only way in
    # from the tailnet or the public internet, same as before the 0.0.0.0
    # fix -- this is the config Tailscale's own docs assume.
    app.run(host="127.0.0.1", port=5200, debug=False, threaded=True)
