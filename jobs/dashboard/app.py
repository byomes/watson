"""Watson dashboard — port 5200, Tailscale-only."""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, g, jsonify, request
from jobs.people.api import people_create, people_delete, people_list, people_update

DB = os.path.expanduser("~/watson/data/watson.db")
SKILLS_FILE = Path(__file__).resolve().parents[2] / "memory" / "skills.json"
MEMORY = Path(__file__).resolve().parents[2] / "memory"
app = Flask(__name__)


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

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Watson</title>
<script>(function(){var t=localStorage.getItem('watson-theme')||'dark';document.documentElement.setAttribute('data-theme',t);})();</script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&family=DM+Mono:wght@400;500&display=swap');
:root[data-theme="dark"]{
  --bg:#0d0f12;--surface:#161a20;--surface-2:#1e2329;
  --border:#2a2f38;--text:#e8eaed;--text-muted:#6b7280;
  --accent:#c9a84c;--accent-soft:rgba(201,168,76,0.12);
  --green:#4ade80;--red:#f87171;--blue:#60a5fa;
  --bg2:#161a20;--bg3:#1e2329;--text2:#6b7280;--text3:#4b5563;
  --success:#4ade80;--danger:#f87171;--warn:#fb923c;
}
:root[data-theme="light"]{
  --bg:#f8f9fa;--surface:#ffffff;--surface-2:#f1f3f5;
  --border:#e2e5e9;--text:#1a1d21;--text-muted:#6b7280;
  --accent:#b8920d;--accent-soft:rgba(184,146,13,0.1);
  --green:#16a34a;--red:#dc2626;--blue:#2563eb;
  --bg2:#ffffff;--bg3:#f1f3f5;--text2:#6b7280;--text3:#9ca3af;
  --success:#16a34a;--danger:#dc2626;--warn:#d97706;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',system-ui,sans-serif;font-size:15px;min-height:100vh}
button{cursor:pointer;font-family:'DM Sans',system-ui,sans-serif}
input,select,textarea{font-family:'DM Sans',system-ui,sans-serif}
#hdr{position:fixed;top:0;left:0;right:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border);height:54px;display:flex;align-items:center;padding:0 14px;gap:10px}
#hdr-identity{display:flex;align-items:center;gap:10px;flex:1}
#hdr-mark{width:30px;height:30px;flex-shrink:0}
#hdr-name{font-size:15px;font-weight:700;color:var(--text);line-height:1.2}
#hdr-sub{font-size:10px;color:var(--text-muted);font-family:'DM Mono',monospace;line-height:1.2}
#gear-btn{background:none;border:none;color:var(--text-muted);padding:6px;border-radius:6px;display:flex;align-items:center;justify-content:center;flex-shrink:0;width:34px;height:34px}
#gear-btn:hover{color:var(--text)}
#main{padding:66px 0 84px;min-height:100vh}
.tab{display:none;padding:16px}
.tab.active{display:block}
#nav{position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);display:flex;padding-bottom:env(safe-area-inset-bottom);transition:transform .2s ease-out}
#nav.hidden{transform:translateY(100%)}
.nb{flex:1;background:none;border:none;color:var(--text-muted);padding:10px 0 8px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;font-family:'DM Mono',monospace;letter-spacing:.04em;text-transform:uppercase;border-top:2px solid transparent;transition:color .15s,border-color .15s}
.nb.active{color:var(--accent);border-top-color:var(--accent)}
.nb .ic{width:20px;height:20px;display:flex;align-items:center;justify-content:center}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:10px;transition:opacity .2s}
.card-title{font-size:13px;font-weight:500;line-height:1.4;margin-bottom:4px}
.meta{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.summary{font-size:12px;color:var(--text-muted);line-height:1.5;margin-bottom:8px}
.row{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.btn{flex:1;padding:7px 6px;font-size:11px;border-radius:6px;border:1px solid;font-family:'DM Mono',monospace;transition:opacity .15s}
.btn:hover{opacity:.75}
.btn-g{background:transparent;border-color:var(--green);color:var(--green)}
.btn-r{background:transparent;border-color:var(--red);color:var(--red)}
.btn-b{background:transparent;border-color:var(--blue);color:var(--blue)}
.btn-gh{background:transparent;border-color:var(--border);color:var(--text-muted)}
.btn-p{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-family:'DM Mono',monospace;letter-spacing:.04em;text-transform:uppercase}
.badge::before{content:'';width:6px;height:6px;border-radius:50%;flex-shrink:0}
.bh{color:var(--red)}.bh::before{background:var(--red)}
.bm{color:var(--warn)}.bm::before{background:var(--warn)}
.bl{color:var(--green)}.bl::before{background:var(--green)}
.fbox{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:12px}
.flabel{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
input[type=text],input[type=date],input[type=time],select,textarea{display:block;width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:11px 12px;color:var(--text);font-size:13px;outline:none;margin-bottom:7px}
input[type=text]::placeholder,textarea::placeholder{color:var(--text-muted)}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
select option{background:var(--surface)}
.tr{display:flex;align-items:flex-start;gap:9px;padding:10px 0;border-bottom:1px solid var(--border)}
.tc{width:20px;height:20px;border-radius:4px;border:1.5px solid var(--border);background:transparent;flex-shrink:0;display:flex;align-items:center;justify-content:center;margin-top:1px}
.tc.done{background:var(--green);border-color:var(--green)}
.tc.done::after{content:'\\2713';color:var(--bg);font-size:11px}
.tbody{flex:1;min-width:0}
.ttitle{font-size:13px;color:var(--text);line-height:1.4;margin-bottom:3px}
.ttitle.done{text-decoration:line-through;color:var(--text-muted)}
.tmeta{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.tdel{background:none;border:none;color:var(--text-muted);font-size:16px;padding:2px 4px;flex-shrink:0}
.tdel:hover{color:var(--red)}
.pills{display:flex;gap:6px;margin-bottom:12px}
.pill{flex:1;padding:6px 0;font-size:11px;text-transform:uppercase;letter-spacing:.04em;font-family:'DM Mono',monospace;border:1px solid var(--border);border-radius:6px;background:transparent;color:var(--text-muted)}
.pill.active{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.cr{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--border);cursor:pointer}
.av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px;font-weight:500;font-family:'DM Mono',monospace}
.cdet{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:11px;margin-bottom:4px}
.dl{font-size:12px;color:var(--text-muted);margin-bottom:4px}
.slabel{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px 2px}
.bk{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px}
.bk-title{font-size:13px;color:var(--text);margin-bottom:3px;line-height:1.4}
.bk-src{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.bk-sum{font-size:11px;color:var(--text-muted);line-height:1.5;margin-bottom:8px}
.sk{background:var(--surface-2);border-radius:4px;animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.35}50%{opacity:.7}}

.overlay-panel{position:fixed;top:0;left:0;right:0;bottom:0;z-index:200;background:var(--bg);display:flex;flex-direction:column;transform:translateY(100%);transition:transform .2s ease-out;visibility:hidden}
.overlay-panel.open{transform:translateY(0);visibility:visible}
.overlay-topbar{height:54px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 14px;flex-shrink:0}
.overlay-back{background:none;border:none;color:var(--accent);font-size:13px;font-weight:500;padding:6px 8px 6px 0;cursor:pointer;display:flex;align-items:center;gap:5px;flex-shrink:0;font-family:'DM Sans',system-ui,sans-serif;min-width:60px}
.overlay-title{flex:1;text-align:center;font-size:15px;font-weight:600;color:var(--text)}
.overlay-close{background:none;border:none;color:var(--text-muted);font-size:22px;padding:4px 0 4px 8px;cursor:pointer;flex-shrink:0;line-height:1;min-width:60px;text-align:right}
.overlay-close:hover{color:var(--text)}
.overlay-body{flex:1;overflow-y:auto}
.overlay-section{padding:20px 16px 0}
.overlay-section-hdr{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.1em;padding-bottom:8px;border-bottom:1px solid var(--border)}
.overlay-row{display:flex;align-items:center;height:48px;border-bottom:1px solid var(--border);cursor:pointer;padding:0 2px;gap:8px}
.overlay-row:hover .overlay-row-label{color:var(--accent)}
.overlay-row-label{flex:1;font-size:14px;color:var(--text)}
.overlay-row-value{font-size:13px;color:var(--text-muted);font-family:'DM Mono',monospace}
.overlay-row-chevron{color:var(--text-muted);font-size:18px;line-height:1;flex-shrink:0}
.overlay-sub{position:absolute;top:0;left:0;right:0;bottom:0;background:var(--bg);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .2s ease-out;visibility:hidden}
.overlay-sub.open{transform:translateX(0);visibility:visible}
#tab-chat{padding:0;display:none;flex-direction:column;position:fixed;top:54px;left:0;right:0;bottom:calc(60px + env(safe-area-inset-bottom,0px))}
#tab-chat.active{display:flex}
.new-chat-btn{display:flex;align-items:center;gap:12px;padding:13px 16px;background:var(--accent-soft);border:1px solid var(--accent);border-radius:8px;color:var(--accent);font-size:14px;font-weight:600;width:100%;text-align:left;margin-bottom:4px}
.new-chat-btn:hover{opacity:.8}
.history-label{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin:4px 0 6px 2px}
.session-row{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;cursor:pointer;transition:opacity .15s;margin-bottom:6px}
.session-row:hover{opacity:.8}
.session-info{flex:1;min-width:0}
.session-title{font-size:13px;color:var(--text);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:2px}
.session-date{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace}
.session-del{background:none;border:none;color:var(--text-muted);font-size:18px;padding:4px 6px;flex-shrink:0;border-radius:6px}
.session-del:hover{color:var(--red)}
#chat-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.msg-wrap{display:flex;flex-direction:column;animation:msgIn .15s ease-out}
@keyframes msgIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.msg-wrap.user{align-items:flex-end}
.msg-wrap.watson{align-items:flex-start}
.msg-bubble{max-width:80%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.55;word-break:break-word;white-space:pre-wrap}
.msg-wrap.user .msg-bubble{background:var(--accent-soft);color:var(--text);border:1px solid rgba(201,168,76,.2)}
.msg-wrap.watson .msg-bubble{background:var(--surface-2);color:var(--text)}
.msg-time{font-size:10px;color:var(--text-muted);font-family:'DM Mono',monospace;margin-top:3px;padding:0 2px}
.typing-indicator{display:flex;gap:4px;align-items:center;padding:10px 14px;background:var(--surface-2);border-radius:12px;width:fit-content}
.typing-indicator span{width:6px;height:6px;background:var(--text-muted);border-radius:50%;animation:tbounce 1.2s ease-in-out infinite}
.typing-indicator span:nth-child(2){animation-delay:.2s}
.typing-indicator span:nth-child(3){animation-delay:.4s}
@keyframes tbounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-5px)}}
#chat-input-area{padding:10px 14px;padding-bottom:max(10px,env(safe-area-inset-bottom,0px));border-top:1px solid var(--border);display:flex;gap:8px;background:var(--bg);flex-shrink:0}
#chat-input{flex:1;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:10px 14px;color:var(--text);font-size:13px;outline:none}
#chat-input::placeholder{color:var(--text-muted)}
#chat-input:focus{border-color:var(--accent)}
#chat-send-btn{background:var(--accent);border:none;border-radius:12px;padding:10px 18px;color:var(--bg);font-size:13px;font-weight:600;flex-shrink:0;cursor:pointer}
#chat-send-btn:hover{opacity:.8}
#chat-mic-btn{width:40px;height:40px;background:var(--surface-2);border:1px solid var(--border);border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;color:var(--text-muted);transition:color .15s,border-color .15s}
#chat-mic-btn.recording{border-color:var(--accent);color:var(--accent);animation:mic-pulse 1s ease-in-out infinite}
@keyframes mic-pulse{0%,100%{opacity:1}50%{opacity:.55}}
#chat-attach-btn{width:36px;height:36px;background:var(--surface-2);border:1px solid var(--border);border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;color:var(--text-muted);font-size:20px;line-height:1;padding:0}
#chat-attach-btn:hover{opacity:.75}
#chat-attach-indicator{display:none;padding:3px 14px 5px;background:var(--bg);font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;flex-shrink:0}
#chat-attach-x{background:none;border:none;color:var(--text-muted);font-size:14px;padding:0 3px;cursor:pointer;line-height:1}
#chat-attach-x:hover{color:var(--red)}
.ctr{text-align:center;padding:40px 0;color:var(--text-muted);font-size:13px}
.sbar{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:11px 12px;color:var(--text);font-size:13px;outline:none;margin-bottom:10px}
.sbar:focus{border-color:var(--accent)}
.sbar::placeholder{color:var(--text-muted)}
.ph-hdr{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px}
.theme-row{display:flex;justify-content:space-between;align-items:center;height:48px;border-bottom:1px solid var(--border)}
.theme-lbl{font-size:14px;color:var(--text)}
.theme-btn2{padding:6px 14px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);font-size:12px;font-family:'DM Mono',monospace}
.theme-btn2:hover{border-color:var(--accent);color:var(--accent)}
#settings-skills-list{overflow-y:auto;margin-top:4px}
.sp-back{display:flex;align-items:center;gap:5px;background:none;border:none;color:var(--accent);font-size:12px;padding:0 0 10px;cursor:pointer;font-family:'DM Mono',monospace}
.skill-card{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px;display:flex;align-items:center;gap:10px}
.skill-info{flex:1;min-width:0}
.skill-name{font-size:13px;font-weight:600;color:var(--text);margin-bottom:2px}
.skill-desc{font-size:11px;color:var(--text-muted);line-height:1.4}
.skill-use-btn{flex-shrink:0;padding:5px 12px;background:var(--accent-soft);border:1px solid var(--accent);border-radius:6px;color:var(--accent);font-size:11px;cursor:pointer;font-family:'DM Mono',monospace}

/* Projects tab */
.proj-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:8px;cursor:pointer;transition:opacity .15s}
.proj-card:hover{opacity:.8}
.proj-name{font-size:14px;font-weight:600;color:var(--text);margin-bottom:4px}
.proj-meta{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace}
.status-active{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--green);font-family:'DM Mono',monospace}
.status-active::before{content:'';width:6px;height:6px;border-radius:50%;background:var(--green);flex-shrink:0}
.status-planned{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--warn);font-family:'DM Mono',monospace}
.status-planned::before{content:'';width:6px;height:6px;border-radius:50%;background:var(--warn);flex-shrink:0}
.status-archived{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace}
.status-archived::before{content:'';width:6px;height:6px;border-radius:50%;background:var(--text-muted);flex-shrink:0}
.arch-toggle{padding:5px 12px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);font-size:11px;cursor:pointer;font-family:'DM Mono',monospace}
.arch-toggle.on{border-color:var(--accent);color:var(--accent)}

/* Project workspace overlay */
#proj-workspace{position:fixed;top:0;left:0;right:0;bottom:0;z-index:100;background:var(--bg);display:none;flex-direction:column}
#proj-workspace.open{display:flex}
.pw-topbar{height:54px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 10px;gap:8px;flex-shrink:0;position:relative}
.pw-back{background:none;border:none;color:var(--accent);font-size:13px;font-weight:500;padding:6px 8px 6px 0;cursor:pointer;flex-shrink:0;font-family:'DM Sans',system-ui,sans-serif}
.pw-title{flex:1;font-size:15px;font-weight:600;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 4px}
.pw-icon-btn{width:36px;height:36px;background:none;border:none;color:var(--text-muted);border-radius:6px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:18px;flex-shrink:0;line-height:1;transition:color .15s}
.pw-icon-btn:hover{color:var(--text)}
.pw-kebab-menu{position:absolute;right:12px;top:54px;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;min-width:170px;z-index:101;display:none}
.pw-kebab-menu.open{display:block}
.pw-kebab-item{display:block;width:100%;padding:11px 14px;background:none;border:none;color:var(--text);text-align:left;font-size:13px;cursor:pointer;font-family:inherit;border-bottom:1px solid var(--border)}
.pw-kebab-item:last-child{border-bottom:none}
.pw-kebab-item:hover{opacity:.75}
.pw-kebab-item.danger{color:var(--red)}

/* Project summary card */
#pw-summary-card{margin:12px 14px 0;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;flex-shrink:0}
.pw-summary-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.pw-summary-lbl{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em}
.pw-summary-dismiss{background:none;border:none;color:var(--text-muted);font-size:18px;cursor:pointer;padding:0 4px;line-height:1}
.pw-summary-dismiss:hover{color:var(--text)}
.pw-summary-text{font-size:12px;color:var(--text-muted);line-height:1.6}

/* PW messages + input */
#pw-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
#pw-attach-indicator{padding:3px 14px 5px;background:var(--bg);font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;flex-shrink:0;display:none}
#pw-attach-x{background:none;border:none;color:var(--text-muted);font-size:14px;padding:0 3px;cursor:pointer;line-height:1}
#pw-attach-x:hover{color:var(--red)}
#pw-input-area{padding:10px 14px;padding-bottom:max(10px,env(safe-area-inset-bottom,0px));border-top:1px solid var(--border);display:flex;gap:8px;background:var(--bg);flex-shrink:0}
#pw-input{flex:1;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:10px 14px;color:var(--text);font-size:13px;outline:none}
#pw-input::placeholder{color:var(--text-muted)}
#pw-input:focus{border-color:var(--accent)}
#pw-send-btn{background:var(--accent);border:none;border-radius:12px;padding:10px 18px;color:var(--bg);font-size:13px;font-weight:600;cursor:pointer;flex-shrink:0}
#pw-send-btn:hover{opacity:.8}
#pw-attach-btn{width:36px;height:36px;background:var(--surface-2);border:1px solid var(--border);border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text-muted);font-size:20px;line-height:1;padding:0;flex-shrink:0}
#pw-attach-btn:hover{opacity:.75}
#pw-mic-btn{width:40px;height:40px;background:var(--surface-2);border:1px solid var(--border);border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text-muted);flex-shrink:0;transition:color .15s,border-color .15s}
#pw-mic-btn.recording{border-color:var(--accent);color:var(--accent);animation:mic-pulse 1s ease-in-out infinite}

/* File sidebar */
#pw-sidebar{position:fixed;top:0;right:0;bottom:0;width:280px;max-width:90vw;background:var(--surface);border-left:1px solid var(--border);z-index:102;transform:translateX(100%);transition:transform .2s ease-out;display:flex;flex-direction:column}
#pw-sidebar.open{transform:translateX(0)}
.pw-sidebar-hdr{height:54px;border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 14px;flex-shrink:0;gap:8px}
.pw-sidebar-title{flex:1;font-size:13px;font-weight:600}
.pw-sidebar-close{background:none;border:none;color:var(--text-muted);font-size:22px;padding:4px 6px;cursor:pointer;line-height:1}
.pw-sidebar-close:hover{color:var(--text)}
.pw-file-section{margin-bottom:16px}
.pw-file-section-lbl{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.pw-file-row{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;cursor:pointer;transition:opacity .15s}
.pw-file-row:hover{opacity:.7}
.pw-file-name{flex:1;font-size:12px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pw-file-date{font-size:10px;color:var(--text-muted);font-family:'DM Mono',monospace;flex-shrink:0}
.pw-new-note-btn{width:100%;padding:9px;background:var(--accent-soft);border:1px solid var(--accent);border-radius:6px;color:var(--accent);font-size:12px;font-weight:500;cursor:pointer;font-family:'DM Sans',system-ui,sans-serif}

/* Note modal */
#pw-note-modal{position:fixed;inset:0;z-index:103;background:rgba(0,0,0,.6);display:none;align-items:flex-end;justify-content:center}
#pw-note-modal.open{display:flex}
.pw-modal-inner{background:var(--surface);border-radius:12px 12px 0 0;padding:16px;width:100%;max-height:70vh;display:flex;flex-direction:column}
.pw-modal-title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:10px}
#pw-note-content{flex:1;min-height:120px;max-height:220px;resize:none;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;color:var(--text);font-size:13px;outline:none;margin-bottom:10px}
#pw-note-content::placeholder{color:var(--text-muted)}
#pw-note-content:focus{border-color:var(--accent)}

/* Project badge in history */
.proj-badge{font-size:9px;padding:2px 6px;border-radius:3px;background:var(--accent-soft);color:var(--accent);margin-left:5px;vertical-align:middle;letter-spacing:.02em;font-weight:500;font-family:'DM Mono',monospace}
</style>
</head>
<body>

<div id="hdr">
  <div id="hdr-identity">
    <svg id="hdr-mark" viewBox="0 0 30 30" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M4 6l4 14 4-9 4 9 4-14" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <div>
      <div id="hdr-name">Watson</div>
      <div id="hdr-sub">Digital Assistant to Dr. Bill Yomes</div>
    </div>
  </div>
  <button id="gear-btn" onclick="toggleSettings()"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>
</div>

<div id="settings-panel" class="overlay-panel">
  <div class="overlay-topbar">
    <div style="min-width:60px"></div>
    <span class="overlay-title">Settings</span>
    <button class="overlay-close" onclick="closeSettings()">&#215;</button>
  </div>
  <div id="settings-main" class="overlay-body">
    <div class="overlay-section">
      <div class="overlay-section-hdr">Appearance</div>
      <div class="overlay-row" onclick="toggleTheme()">
        <span class="overlay-row-label" id="theme-lbl-text">Dark mode</span>
        <span class="overlay-row-value" id="theme-toggle-btn">Switch to Light</span>
        <span class="overlay-row-chevron">&#8250;</span>
      </div>
    </div>
    <div class="overlay-section" style="margin-top:16px">
      <div class="overlay-section-hdr">Navigate</div>
      <div class="overlay-row" onclick="closeSettings();switchTab('contacts')">
        <span class="overlay-row-label">Contacts</span>
        <span class="overlay-row-chevron">&#8250;</span>
      </div>
      <div class="overlay-row" onclick="closeSettings();switchTab('reading')">
        <span class="overlay-row-label">Reading List</span>
        <span class="overlay-row-chevron">&#8250;</span>
      </div>
      <div class="overlay-row" onclick="openSkillsPanel()">
        <span class="overlay-row-label">Skills</span>
        <span class="overlay-row-chevron">&#8250;</span>
      </div>
    </div>
  </div>
  <div id="settings-skills" class="overlay-sub">
    <div class="overlay-topbar">
      <button class="overlay-back" onclick="closeSkillsPanel()">&#8592; Back</button>
      <span class="overlay-title">Skills</span>
      <div style="min-width:60px"></div>
    </div>
    <div class="overlay-body" style="padding:16px">
      <div id="settings-skills-list"></div>
    </div>
  </div>
</div>

<div id="main">

  <!-- ── Chat Tab ─────────────────────────────────────────────────── -->
  <div id="tab-chat" class="tab active">
    <div id="chat-messages"></div>
    <div id="chat-attach-indicator">&#128206; <span id="ai-filename"></span><button id="chat-attach-x" onclick="clearAttachment()" title="Remove">&#215;</button></div>
    <div id="chat-input-area">
      <input type="file" id="chat-file-input" style="display:none" onchange="handleFileSelect(this)">
      <button id="chat-attach-btn" onclick="document.getElementById('chat-file-input').click()" title="Attach file">+</button>
      <button id="chat-mic-btn" onclick="toggleVoice()"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg></button>
      <input id="chat-input" type="text" placeholder="Message Watson…">
      <button id="chat-send-btn" onclick="sendChat()">Send</button>
    </div>
  </div>

  <!-- ── History Tab ────────────────────────────────────────────────── -->
  <div id="tab-history" class="tab">
    <div style="display:flex;flex-direction:column;gap:8px;padding:14px">
      <button class="new-chat-btn" onclick="startNewChat()">+ Start a New Chat</button>
      <div class="history-label">Previous Chats</div>
      <div id="session-list"></div>
    </div>
  </div>

  <div id="tab-briefing" class="tab">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em">Today's Briefing</div>
      <span id="b-offline" style="font-size:10px;color:var(--warn);display:none">· offline</span>
    </div>
    <div id="b-list"></div>
  </div>

  <div id="tab-tasks" class="tab">
    <div class="fbox">
      <div class="flabel">Add Task</div>
      <input type="text" id="t-title" placeholder="Task title..." onkeydown="if(event.key==='Enter')addTask()">
      <div style="display:flex;gap:6px">
        <input type="date" id="t-due" style="flex:1">
        <select id="t-pri" style="flex:1">
          <option value="high">High</option>
          <option value="medium" selected>Medium</option>
          <option value="low">Low</option>
        </select>
      </div>
      <button class="btn btn-p" style="width:100%;padding:9px;border-radius:10px;border:none;font-size:12px" onclick="addTask()">+ Add Task</button>
    </div>
    <div class="pills">
      <button class="pill active" id="pill-all" onclick="setFilter('all')">All</button>
      <button class="pill" id="pill-active" onclick="setFilter('active')">Active</button>
      <button class="pill" id="pill-done" onclick="setFilter('done')">Done</button>
    </div>
    <div id="t-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadTasks()">Retry</button>
    </div>
    <div id="t-list"></div>
  </div>

  <div id="tab-reminders" class="tab">
    <div class="ph-hdr">Reminders</div>
    <div class="fbox">
      <div class="flabel">Add Reminder</div>
      <input type="text" id="r-title" placeholder="Reminder title..." onkeydown="if(event.key==='Enter')addReminder()">
      <div style="display:flex;gap:6px">
        <input type="date" id="r-date" style="flex:1">
        <input type="time" id="r-time" style="flex:1">
      </div>
      <button class="btn btn-p" style="width:100%;padding:9px;border-radius:10px;border:none;font-size:12px" onclick="addReminder()">+ Add Reminder</button>
    </div>
    <div class="pills">
      <button class="pill active" id="rpill-all" onclick="setRFilter('all')">All</button>
      <button class="pill" id="rpill-active" onclick="setRFilter('active')">Active</button>
      <button class="pill" id="rpill-done" onclick="setRFilter('done')">Done</button>
    </div>
    <div id="r-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadReminders()">Retry</button>
    </div>
    <div id="r-list"></div>
  </div>

  <div id="tab-contacts" class="tab">
    <div style="display:flex;gap:8px;margin-bottom:4px">
      <input class="sbar" id="c-search" placeholder="Search contacts..." oninput="renderContacts()" style="flex:1;margin-bottom:0">
      <button id="c-add-btn" onclick="toggleAddContact()" style="padding:9px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:9px;color:var(--text);font-size:16px;flex-shrink:0">+</button>
    </div>
    <div id="c-add-form" class="fbox" style="display:none;margin-top:10px">
      <div class="flabel">New Contact</div>
      <input type="text" id="nc-name" placeholder="Name *">
      <input type="text" id="nc-email" placeholder="Email">
      <input type="text" id="nc-phone" placeholder="Phone">
      <input type="text" id="nc-rel" placeholder="Relationship">
      <input type="text" id="nc-notes" placeholder="Notes">
      <button class="btn btn-p" style="width:100%;padding:9px;border:none;border-radius:10px;font-size:12px" onclick="saveNewContact()">Add Contact</button>
    </div>
    <div id="c-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadContacts()">Retry</button>
    </div>
    <div id="c-list"></div>
  </div>

  <div id="tab-reading" class="tab">
    <div id="rl-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadReading()">Retry</button>
    </div>
    <div id="rl-list"></div>
  </div>

  <div id="tab-projects" class="tab">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <button class="arch-toggle" id="arch-toggle" onclick="toggleArchivedProjects()">Show Archived</button>
      <button onclick="showNewProjectForm()" style="padding:7px 14px;background:var(--accent);border:none;border-radius:9px;color:#fff;font-size:12px;cursor:pointer;font-family:inherit">+ New Project</button>
    </div>
    <div id="proj-new-form" class="fbox" style="display:none;margin-bottom:10px">
      <div class="flabel">New Project</div>
      <input type="text" id="proj-new-name" placeholder="Project name..." onkeydown="if(event.key==='Enter')createProject()">
      <button class="btn btn-p" style="width:100%;padding:9px;border:none;border-radius:10px;font-size:12px" onclick="createProject()">Create</button>
    </div>
    <div id="proj-list"></div>
  </div>

</div>

<nav id="nav">
  <button class="nb active" id="nav-chat" onclick="switchTab('chat')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M2 12C2 6.48 6.48 2 12 2s10 4.48 10 10-4.48 10-10 10H2l2.29-2.29C3.13 18.07 2 15.17 2 12z"/></svg></span>
    <span>Chat</span>
  </button>
  <button class="nb" id="nav-history" onclick="switchTab('history')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M13 3a9 9 0 00-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 117 7c-1.93 0-3.68-.79-4.95-2.05l-1.42 1.42A8.954 8.954 0 0013 21a9 9 0 000-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg></span>
    <span>History</span>
  </button>
  <button class="nb" id="nav-briefing" onclick="switchTab('briefing')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zm-1 1.5L18.5 9H13V3.5zM6 20V4h5v7h7v9H6z"/></svg></span>
    <span>Briefing</span>
  </button>
  <button class="nb" id="nav-tasks" onclick="switchTab('tasks')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/></svg></span>
    <span>Tasks</span>
  </button>
  <button class="nb" id="nav-reminders" onclick="switchTab('reminders')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg></span>
    <span>Reminders</span>
  </button>
  <button class="nb" id="nav-projects" onclick="switchTab('projects')">
    <span class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg></span>
    <span>Projects</span>
  </button>
</nav>

<!-- Project workspace overlay -->
<div id="proj-workspace">
  <div class="pw-topbar">
    <button class="pw-back" onclick="closeProjectWorkspace()">&#8592;</button>
    <span class="pw-title" id="pw-title"></span>
    <div style="display:flex;gap:2px;flex-shrink:0">
      <button class="pw-icon-btn" title="Files" onclick="openFileSidebar()">&#128193;</button>
      <button class="pw-icon-btn" id="pw-kebab-btn" title="More" onclick="toggleKebabMenu(event)">&#8942;</button>
    </div>
    <div class="pw-kebab-menu" id="pw-kebab-menu">
      <button class="pw-kebab-item" onclick="archiveProject()">Archive Project</button>
      <button class="pw-kebab-item danger" onclick="confirmDeleteProject()">Delete Project</button>
    </div>
  </div>
  <div id="pw-summary-card" style="display:none">
    <div class="pw-summary-hdr">
      <span class="pw-summary-lbl">Watson&#39;s summary</span>
      <button class="pw-summary-dismiss" onclick="dismissSummary()">&#215;</button>
    </div>
    <div class="pw-summary-text" id="pw-summary-text"></div>
  </div>
  <div id="pw-messages"></div>
  <div id="pw-attach-indicator">&#128206; <span id="pw-attach-name"></span><button id="pw-attach-x" onclick="clearPWAttachment()">&#215;</button></div>
  <div id="pw-input-area">
    <input type="file" id="pw-file-input" style="display:none" onchange="handlePWFileSelect(this)">
    <button id="pw-attach-btn" onclick="document.getElementById('pw-file-input').click()">+</button>
    <button id="pw-mic-btn" onclick="togglePWVoice()"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg></button>
    <input id="pw-input" type="text" placeholder="Message Watson&#8230;">
    <button id="pw-send-btn" onclick="sendPWChat()">Send</button>
  </div>
</div>

<!-- File sidebar -->
<div id="pw-sidebar">
  <div class="pw-sidebar-hdr">
    <span class="pw-sidebar-title">Project Files</span>
    <button class="pw-sidebar-close" onclick="closeFileSidebar()">&#215;</button>
  </div>
  <div id="pw-sidebar-content" style="flex:1;overflow-y:auto;padding:12px"></div>
  <div style="padding:12px;border-top:1px solid var(--border);flex-shrink:0">
    <button class="pw-new-note-btn" onclick="openNoteModal()">+ New Note</button>
  </div>
</div>

<!-- Note modal -->
<div id="pw-note-modal">
  <div class="pw-modal-inner">
    <div class="pw-modal-title">New Note</div>
    <textarea id="pw-note-content" placeholder="Note content..."></textarea>
    <div class="row">
      <button class="btn btn-p" onclick="saveNote()">Save</button>
      <button class="btn btn-gh" onclick="closeNoteModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
// ── Theme ─────────────────────────────────────────────────────────────────
const root = document.documentElement;
function _syncThemeUI() {
  const isDark = root.getAttribute('data-theme') === 'dark';
  const btn = document.getElementById('theme-toggle-btn');
  const lbl = document.getElementById('theme-lbl-text');
  if (btn) btn.textContent = isDark ? 'Switch to Light' : 'Switch to Dark';
  if (lbl) lbl.textContent = isDark ? 'Dark mode' : 'Light mode';
}
_syncThemeUI();
function toggleTheme() {
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('watson-theme', next);
  _syncThemeUI();
}
function toggleSettings() {
  document.getElementById('settings-panel').classList.toggle('open');
}
function closeSettings() {
  document.getElementById('settings-panel').classList.remove('open');
  closeSkillsPanel();
}

// ── Skills panel ─────────────────────────────────────────────────────────────
function openSkillsPanel() {
  document.getElementById('settings-skills').classList.add('open');
  loadSkillsPanel();
}
function closeSkillsPanel() {
  document.getElementById('settings-skills').classList.remove('open');
}
async function loadSkillsPanel() {
  const el = document.getElementById('settings-skills-list');
  el.innerHTML = '<div style="font-size:12px;color:var(--text3);padding:6px 0">Loading...</div>';
  try {
    const skills = await api('/api/skills');
    if (!skills.length) {
      el.innerHTML = '<div style="font-size:12px;color:var(--text3);padding:6px 0">No skills installed yet.</div>';
      return;
    }
    el.innerHTML = skills.map(function(s) {
      const name = s.slug.replace(/_/g, ' ').replace(/\\b\\w/g, function(c){return c.toUpperCase();});
      return '<div class="skill-card">' +
        '<div class="skill-info">' +
          '<div class="skill-name">' + esc(name) + '</div>' +
          '<div class="skill-desc">' + esc(s.description) + '</div>' +
        '</div>' +
        '<button class="skill-use-btn" data-slug="' + esc(s.slug) + '" onclick="useSkill(this.dataset.slug)">Use Skill</button>' +
      '</div>';
    }).join('');
  } catch(e) {
    el.innerHTML = '<div style="font-size:12px;color:var(--danger);padding:6px 0">Failed to load skills.</div>';
  }
}
function useSkill(slug) {
  const name = slug.replace(/_/g, ' ').replace(/\\b\\w/g, function(c){return c.toUpperCase();});
  closeSettings();
  switchTab('chat');
  document.getElementById('chat-input').value = 'Watson, run ' + name;
  document.getElementById('chat-input').focus();
}

// ── Helpers ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function sk(n) {
  return Array.from({length:n}, () =>
    '<div class="card"><div class="sk" style="height:13px;width:60%;margin-bottom:8px"></div>' +
    '<div class="sk" style="height:10px;width:40%;margin-bottom:10px"></div>' +
    '<div class="sk" style="height:10px;width:88%;margin-bottom:5px"></div>' +
    '<div class="sk" style="height:10px;width:70%"></div></div>'
  ).join('');
}
async function api(url, method, body) {
  const opts = {method: method || 'GET', headers: {}};
  if (body) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(url, opts);
  return r.json();
}

// ── Tabs ──────────────────────────────────────────────────────────────────
const TABS = ['chat','history','briefing','tasks','reminders','contacts','reading','projects'];
const loaded = {chat:true, history:false, briefing:false, tasks:false, reminders:false, contacts:false, reading:false, projects:false};
const loaders = {};

const TAB_LABELS = {chat:'Chat',history:'History',briefing:'Briefing',tasks:'Tasks',reminders:'Reminders',contacts:'Contacts',reading:'Reading',projects:'Projects'};
function switchTab(name) {
  TABS.forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
    const navEl = document.getElementById('nav-' + t);
    if (navEl) navEl.classList.toggle('active', t === name);
  });
  if (!loaded[name]) { loaded[name] = true; if (loaders[name]) loaders[name](); }
}

// ── Chat — session state ──────────────────────────────────────────────────
let currentSessionId = null;
let chatHistory = [];
let attachedFileContent = null;
let attachedFileName = null;

// Load session list
async function loadSessions() {
  const el = document.getElementById('session-list');
  try {
    const sessions = await api('/api/chat/sessions');
    if (!sessions.length) {
      el.innerHTML = '<div class="ctr" style="padding:20px 0;font-size:12px">No previous chats</div>';
      return;
    }
    el.innerHTML = sessions.map(s => {
      const d = new Date(s.updated_at.replace(' ','T') + 'Z');
      const dateStr = d.toLocaleDateString('en-US', {month:'short', day:'numeric'}) + ' ' +
                      d.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
      const proj = s.project_slug ? projects.find(p => p.slug === s.project_slug) : null;
      const projLabel = proj ? proj.name : s.project_slug;
      const badge = s.project_slug ? '<span class="proj-badge">' + esc(projLabel || s.project_slug) + '</span>' : '';
      const onclick = s.project_slug
        ? 'openProjectWorkspace(\\'' + esc(s.project_slug) + '\\',\\'' + esc(projLabel || s.project_slug).replace(/'/g,"\\'") + '\\')'
        : 'openSession(' + s.id + ',\\'' + esc(s.title).replace(/'/g,"\\'") + '\\')';
      return '<div class="session-row" onclick="' + onclick + '">' +
        '<div class="session-info">' +
          '<div class="session-title">' + esc(s.title) + badge + '</div>' +
          '<div class="session-date">' + dateStr + '</div>' +
        '</div>' +
        '<button class="session-del" onclick="event.stopPropagation();deleteSession(' + s.id + ')" title="Delete">&#215;</button>' +
      '</div>';
    }).join('');
  } catch(_) {
    el.innerHTML = '<div class="ctr" style="font-size:12px">Offline</div>';
  }
}
loaders.history = loadSessions;

// Start a brand new session
async function startNewChat() {
  try {
    const s = await api('/api/chat/sessions', 'POST', {title: 'New Chat'});
    currentSessionId = s.id;
    chatHistory = [];
    document.getElementById('chat-messages').innerHTML = '';
    switchTab('chat');
    document.getElementById('chat-input').focus();
  } catch(_) { alert('Watson offline'); }
}

// Open an existing session
async function openSession(id, title) {
  currentSessionId = id;
  chatHistory = [];
  document.getElementById('chat-messages').innerHTML = '';
  switchTab('chat');
  try {
    const msgs = await api('/api/chat/sessions/' + id + '/messages');
    msgs.forEach(m => {
      _appendMsg(m.role === 'assistant' ? 'watson' : 'user', m.content);
      chatHistory.push({role: m.role, content: m.content});
    });
    document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
  } catch(_) { _appendMsg('watson', 'Could not load messages.'); }
}

// Delete a session
async function deleteSession(id) {
  if (!confirm('Delete this chat?')) return;
  await api('/api/chat/sessions/' + id, 'DELETE');
  if (currentSessionId === id) {
    currentSessionId = null;
    chatHistory = [];
    document.getElementById('chat-messages').innerHTML = '';
  }
  loadSessions();
}

// ── Chat — messaging ──────────────────────────────────────────────────────
function _fmtTime(d) {
  const h = d.getHours(), m = d.getMinutes();
  return (h % 12 || 12) + ':' + String(m).padStart(2,'0') + (h < 12 ? ' am' : ' pm');
}
function _appendMsg(role, text) {
  const msgs = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap ' + role;
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  const time = document.createElement('div');
  time.className = 'msg-time';
  time.textContent = _fmtTime(new Date());
  wrap.appendChild(bubble);
  wrap.appendChild(time);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
}

function _showTyping() {
  const msgs = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap watson';
  wrap.id = 'typing-wrap';
  wrap.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
}

function _hideTyping() {
  const el = document.getElementById('typing-wrap');
  if (el) el.remove();
}

async function handleFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/upload', {method: 'POST', body: fd});
    const data = await r.json();
    if (data.success) {
      attachedFileContent = data.content;
      attachedFileName = data.filename;
      document.getElementById('ai-filename').textContent = data.filename;
      document.getElementById('chat-attach-indicator').style.display = 'block';
    } else {
      alert('Upload failed: ' + (data.error || 'Unknown error'));
    }
  } catch(e) {
    alert('Upload error: ' + e.message);
  }
  input.value = '';
}

function clearAttachment() {
  attachedFileContent = null;
  attachedFileName = null;
  document.getElementById('chat-attach-indicator').style.display = 'none';
  document.getElementById('ai-filename').textContent = '';
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (!currentSessionId) {
    const title = text.length > 50 ? text.slice(0, 50) : text;
    const sess = await api('/api/chat/sessions', 'POST', {title});
    currentSessionId = sess.id;
  }
  input.value = '';

  const displayMsg = attachedFileName ? text + '\\n\U0001F4CE ' + attachedFileName : text;
  const ollamaMsg = attachedFileContent
    ? '[Attached file: ' + attachedFileName + ']\\n' + attachedFileContent + '\\n\\n---\\n\\nUser message: ' + text
    : text;
  if (attachedFileContent) clearAttachment();

  _appendMsg('user', displayMsg);

  // Save user message
  api('/api/chat/sessions/' + currentSessionId + '/messages', 'POST', {role:'user', content: displayMsg});

  // Auto-title from first message
  if (chatHistory.length === 0) {
    const title = text.length > 50 ? text.slice(0,50) + '…' : text;
    api('/api/chat/sessions/' + currentSessionId, 'PATCH', {title});
  }

  chatHistory.push({role: 'user', content: ollamaMsg});
  if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
  _showTyping();
  try {
    const data = await api('/api/chat', 'POST', {message: ollamaMsg, history: chatHistory.slice(0, -1)});
    _hideTyping();
    const reply = data.response || '(no response)';
    _appendMsg('watson', reply);
    chatHistory.push({role: 'assistant', content: reply});
    if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
    // Save Watson reply
    api('/api/chat/sessions/' + currentSessionId + '/messages', 'POST', {role:'assistant', content:reply});
  } catch(_) {
    _hideTyping();
    _appendMsg('watson', 'Watson is offline.');
  }
}

document.getElementById('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

// Load sessions on first visit
loadSessions();

// ── Voice input ───────────────────────────────────────────────────────────
(function() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = document.getElementById('chat-mic-btn');
  if (!SR) { btn.style.display = 'none'; return; }
  const rec = new SR();
  rec.continuous = false;
  rec.interimResults = false;
  rec.lang = 'en-US';
  let active = false;
  function stopRec() {
    active = false;
    btn.classList.remove('recording');
    try { rec.stop(); } catch(_) {}
  }
  rec.onresult = function(e) {
    const t = e.results[0][0].transcript.trim();
    stopRec();
    if (t) {
      document.getElementById('chat-input').value = t;
      setTimeout(sendChat, 100);
    }
  };
  rec.onerror = function(e) { console.log('Speech error:', e.error); stopRec(); };
  rec.onend = function() { if (active) stopRec(); };
  window.toggleVoice = function() {
    if (!currentSessionId) { startNewChat(); return; }
    if (active) { stopRec(); return; }
    active = true;
    btn.classList.add('recording');
    try { rec.start(); } catch(_) { stopRec(); }
  };
})();

// ── Briefing ──────────────────────────────────────────────────────────────
let bItems = [];
let bGeneratedAt = null;

function fmtGeneratedAt(s) {
  if (!s) return '';
  return 'Generated ' + new Date(s.replace(' ', 'T') + 'Z').toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
}

async function loadBriefing() {
  document.getElementById('b-list').innerHTML = sk(3);
  try {
    const [items, meta] = await Promise.all([api('/api/briefing'), api('/api/briefing/meta')]);
    bItems = items;
    bGeneratedAt = meta && meta.generated_at ? meta.generated_at : null;
    renderBriefing();
  } catch(_) {
    document.getElementById('b-offline').style.display = 'block';
    document.getElementById('b-list').innerHTML = '';
  }
}
loaders.briefing = loadBriefing;

function renderBriefing() {
  const el = document.getElementById('b-list');
  const dateStr = new Date().toLocaleDateString('en-US', {weekday:'long', month:'long', day:'numeric', year:'numeric'});
  const genStr = fmtGeneratedAt(bGeneratedAt);
  const header = '<div style="text-align:center;padding:18px 0 4px">' +
    '<div style="font-size:14px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;font-variant:small-caps;color:#d29922">Watson</div>' +
    '<div style="font-size:12px;font-style:italic;color:var(--text2);font-family:Georgia,serif;margin-top:5px">' + dateStr + '</div>' +
    (genStr ? '<div style="font-size:11px;font-style:italic;color:var(--text3);font-family:Georgia,serif;margin-top:3px">' + genStr + '</div>' : '') +
    '<hr style="border:none;border-top:1px solid rgba(210,153,34,0.45);margin:12px 0 16px">' +
    '</div>';
  if (!bItems.length) { el.innerHTML = header + '<div class="ctr">No items today</div>'; return; }
  el.innerHTML = header + bItems.map(i => `
    <div class="card" id="bi-${i.id}">
      <div class="card-title">${esc(i.title)}</div>
      <div class="meta">${esc(i.source_name)}</div>
      ${i.summary ? '<div class="summary">' + esc(i.summary) + '</div>' : ''}
      <div class="row">
        <button class="btn btn-g" onclick="bAction(${i.id},'approve')">Read</button>
        <button class="btn btn-b" onclick="bAction(${i.id},'email')">Email</button>
        <button class="btn btn-b" onclick="bAction(${i.id},'facebook')">Facebook</button>
        <button class="btn btn-gh" onclick="bAction(${i.id},'tolist')">To List</button>
        <button class="btn btn-r" onclick="bAction(${i.id},'reject')">Reject</button>
      </div>
    </div>`).join('');
}

async function bAction(id, action) {
  const el = document.getElementById('bi-' + id);
  if (el) el.style.opacity = '0.3';
  await api('/api/briefing/' + id + '/' + action, 'POST');
  if (el) el.remove();
  bItems = bItems.filter(i => i.id !== id);
}

// ── Tasks ─────────────────────────────────────────────────────────────────
let tasks = [];
let tFilter = 'all';

async function loadTasks() {
  document.getElementById('t-list').innerHTML = sk(4);
  document.getElementById('t-offline').style.display = 'none';
  try {
    tasks = await api('/api/tasks');
    renderTasks();
  } catch(_) {
    document.getElementById('t-list').innerHTML = '';
    document.getElementById('t-offline').style.display = 'block';
  }
}
loaders.tasks = loadTasks;

function setFilter(f) {
  tFilter = f;
  ['all','active','done'].forEach(x =>
    document.getElementById('pill-' + x).classList.toggle('active', x === f));
  renderTasks();
}

function renderTasks() {
  const el = document.getElementById('t-list');
  const vis = tFilter === 'all' ? tasks : tasks.filter(t => t.status === tFilter);
  if (!vis.length) { el.innerHTML = '<div class="ctr">No tasks</div>'; return; }
  el.innerHTML = vis.map(t => `
    <div class="tr" data-drag-id="${t.id}" id="task-${t.id}">
      <button class="tc ${t.status === 'done' ? 'done' : ''}" onclick="toggleTask(${t.id})"></button>
      <div class="tbody">
        <div class="ttitle ${t.status === 'done' ? 'done' : ''}">${esc(t.title)}</div>
        <div class="tmeta">
          <span class="badge b${t.priority[0]}">${t.priority}</span>
          ${t.due_date ? '<span style="font-size:10px;color:var(--text3)">' + esc(t.due_date) + '</span>' : ''}
        </div>
      </div>
      <button class="tdel" onclick="deleteTask(${t.id})">&#215;</button>
    </div>`).join('');
}

async function addTask() {
  const title = document.getElementById('t-title').value.trim();
  if (!title) return;
  const t = await api('/api/tasks', 'POST', {
    title,
    due_date: document.getElementById('t-due').value || null,
    priority: document.getElementById('t-pri').value,
  });
  if (!t.error) {
    tasks.unshift(t);
    document.getElementById('t-title').value = '';
    document.getElementById('t-due').value = '';
    document.getElementById('t-pri').value = 'medium';
    renderTasks();
  }
}

async function toggleTask(id) {
  const t = tasks.find(x => x.id === id);
  if (!t) return;
  const status = t.status === 'done' ? 'active' : 'done';
  tasks = tasks.map(x => x.id === id ? Object.assign({}, x, {status}) : x);
  renderTasks();
  api('/api/tasks/' + id, 'PATCH', {status});
}

async function deleteTask(id) {
  tasks = tasks.filter(t => t.id !== id);
  renderTasks();
  api('/api/tasks/' + id, 'DELETE');
}

// ── Contacts ──────────────────────────────────────────────────────────────
let contacts = [];
let expandedC = null;
let editingC = null;

async function loadContacts() {
  document.getElementById('c-list').innerHTML = sk(4);
  document.getElementById('c-offline').style.display = 'none';
  try {
    contacts = await api('/api/contacts');
    renderContacts();
  } catch(_) {
    document.getElementById('c-list').innerHTML = '';
    document.getElementById('c-offline').style.display = 'block';
  }
}
loaders.contacts = loadContacts;

function toggleAddContact() {
  const f = document.getElementById('c-add-form');
  const show = f.style.display === 'none';
  f.style.display = show ? 'block' : 'none';
  document.getElementById('c-add-btn').textContent = show ? '\u00d7' : '+';
}

async function saveNewContact() {
  const name = document.getElementById('nc-name').value.trim();
  if (!name) return;
  const c = await api('/api/contacts', 'POST', {
    name,
    email: document.getElementById('nc-email').value.trim() || null,
    phone: document.getElementById('nc-phone').value.trim() || null,
    relationship: document.getElementById('nc-rel').value.trim() || null,
    notes: document.getElementById('nc-notes').value.trim() || null,
  });
  if (!c.error) {
    contacts = [...contacts, c].sort((a,b) => a.name.localeCompare(b.name));
    ['nc-name','nc-email','nc-phone','nc-rel','nc-notes'].forEach(id =>
      document.getElementById(id).value = '');
    toggleAddContact();
    renderContacts();
  }
}

function renderContacts() {
  const q = (document.getElementById('c-search').value || '').toLowerCase();
  const el = document.getElementById('c-list');
  const filt = contacts.filter(c =>
    !q || c.name.toLowerCase().includes(q) || (c.email||'').toLowerCase().includes(q));
  if (!filt.length) { el.innerHTML = '<div class="ctr">' + (q ? 'No matches' : 'No contacts') + '</div>'; return; }
  el.innerHTML = filt.map(c => {
    const hue = c.name.charCodeAt(0) * 13 % 360;
    const ini = c.name.split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase();
    const exp = expandedC === c.id;
    const ed  = editingC  === c.id;
    return '<div>' +
      '<div class="cr" onclick="toggleC(' + c.id + ')">' +
        '<div class="av" style="background:hsl(' + hue + ',40%,28%);color:hsl(' + hue + ',60%,75%)">' + ini + '</div>' +
        '<div style="flex:1;min-width:0">' +
          '<div style="font-size:13px;color:var(--text)">' + esc(c.name) + '</div>' +
          '<div style="font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(c.email||c.phone||c.relationship||'') + '</div>' +
        '</div>' +
        '<span style="color:var(--text3);font-size:14px;transform:rotate(' + (exp?90:0) + 'deg);display:inline-block;transition:transform .15s">&#x203A;</span>' +
      '</div>' +
      (exp ? '<div class="cdet">' + (ed ?
        '<input type="text" id="ec-n-' + c.id + '" value="' + esc(c.name) + '" placeholder="Name">' +
        '<input type="text" id="ec-e-' + c.id + '" value="' + esc(c.email||'') + '" placeholder="Email">' +
        '<input type="text" id="ec-p-' + c.id + '" value="' + esc(c.phone||'') + '" placeholder="Phone">' +
        '<input type="text" id="ec-r-' + c.id + '" value="' + esc(c.relationship||'') + '" placeholder="Relationship">' +
        '<input type="text" id="ec-no-' + c.id + '" value="' + esc(c.notes||'') + '" placeholder="Notes">' +
        '<div class="row"><button class="btn btn-p" onclick="saveEdit(' + c.id + ')">Save</button>' +
        '<button class="btn btn-gh" onclick="cancelEdit()">Cancel</button></div>'
      :
        (c.email ? '<div class="dl">&#9993; ' + esc(c.email) + '</div>' : '') +
        (c.phone ? '<div class="dl">&#128222; ' + esc(c.phone) + '</div>' : '') +
        (c.relationship ? '<div class="dl">' + esc(c.relationship) + '</div>' : '') +
        (c.notes ? '<div class="dl" style="color:var(--text3);font-size:11px">' + esc(c.notes) + '</div>' : '') +
        '<div class="row" style="margin-top:8px">' +
          '<button class="btn btn-gh" onclick="startEdit(' + c.id + ')">Edit</button>' +
          '<button class="btn btn-r" onclick="delContact(' + c.id + ')">Delete</button>' +
        '</div>'
      ) + '</div>' : '') +
    '</div>';
  }).join('');
}

function toggleC(id) {
  if (editingC === id) return;
  expandedC = expandedC === id ? null : id;
  renderContacts();
}
function startEdit(id) { editingC = id; renderContacts(); }
function cancelEdit() { editingC = null; renderContacts(); }

async function saveEdit(id) {
  const name = document.getElementById('ec-n-' + id).value.trim();
  if (!name) return;
  const c = await api('/api/contacts/' + id, 'PATCH', {
    name,
    email: document.getElementById('ec-e-' + id).value.trim() || null,
    phone: document.getElementById('ec-p-' + id).value.trim() || null,
    relationship: document.getElementById('ec-r-' + id).value.trim() || null,
    notes: document.getElementById('ec-no-' + id).value.trim() || null,
  });
  if (!c.error) {
    contacts = contacts.map(x => x.id === id ? c : x).sort((a,b) => a.name.localeCompare(b.name));
    editingC = null;
    renderContacts();
  }
}

async function delContact(id) {
  if (!confirm('Delete this contact?')) return;
  await api('/api/contacts/' + id, 'DELETE');
  contacts = contacts.filter(c => c.id !== id);
  if (expandedC === id) expandedC = null;
  renderContacts();
}

// ── Reading ───────────────────────────────────────────────────────────────
let books = [];
const S_ORDER = ['reading','unread','finished'];
const S_LABEL = {reading:'&#128214; Reading', unread:'&#128203; Unread', finished:'&#9989; Finished'};

async function loadReading() {
  document.getElementById('rl-list').innerHTML = sk(4);
  document.getElementById('rl-offline').style.display = 'none';
  try {
    books = await api('/api/reading');
    renderReading();
  } catch(_) {
    document.getElementById('rl-list').innerHTML = '';
    document.getElementById('rl-offline').style.display = 'block';
  }
}
loaders.reading = loadReading;

function renderReading() {
  const el = document.getElementById('rl-list');
  if (!books.length) { el.innerHTML = '<div class="ctr">Reading list is empty</div>'; return; }
  const groups = S_ORDER.map(s => ({s, items: books.filter(b => b.status === s)})).filter(g => g.items.length);
  el.innerHTML = groups.map(g =>
    '<div style="margin-bottom:20px">' +
    '<div class="slabel">' + S_LABEL[g.s] + '</div>' +
    g.items.map(b =>
      '<div class="bk">' +
        '<div class="bk-title">' + esc(b.title) + '</div>' +
        (b.source_name ? '<div class="bk-src">' + esc(b.source_name) + '</div>' : '') +
        (b.summary ? '<div class="bk-sum">' + esc(b.summary) + '</div>' : '') +
        '<div class="row">' +
          S_ORDER.filter(s => s !== b.status).map(s =>
            `<button class="btn btn-gh" onclick="setBookStatus(${b.id},'${s}')">&#8594; ${s}</button>`
          ).join('') +
        '</div>' +
      '</div>'
    ).join('') +
    '</div>'
  ).join('');
}

async function setBookStatus(id, status) {
  const b = await api('/api/reading/' + id, 'PATCH', {status});
  if (!b.error) {
    books = books.map(x => x.id === id ? b : x);
    renderReading();
  }
}

// ── Reminders ─────────────────────────────────────────────────────────────
let reminders = [];
let rFilter = 'all';

async function loadReminders() {
  document.getElementById('r-list').innerHTML = sk(4);
  document.getElementById('r-offline').style.display = 'none';
  try {
    reminders = await api('/api/reminders');
    renderReminders();
  } catch(_) {
    document.getElementById('r-list').innerHTML = '';
    document.getElementById('r-offline').style.display = 'block';
  }
}
loaders.reminders = loadReminders;

function setRFilter(f) {
  rFilter = f;
  ['all','active','done'].forEach(x =>
    document.getElementById('rpill-' + x).classList.toggle('active', x === f));
  renderReminders();
}

function fmtDt(s) {
  return s ? s.replace('T', ' ') : '';
}

function renderReminders() {
  const el = document.getElementById('r-list');
  const vis = rFilter === 'all' ? reminders : reminders.filter(r => r.status === rFilter);
  if (!vis.length) { el.innerHTML = '<div class="ctr">No reminders</div>'; return; }
  el.innerHTML = vis.map(r => `
    <div class="tr" data-drag-id="${r.id}" id="rem-${r.id}">
      <button class="tc ${r.status === 'done' ? 'done' : ''}" onclick="toggleReminder(${r.id})"></button>
      <div class="tbody">
        <div class="ttitle ${r.status === 'done' ? 'done' : ''}">${esc(r.title)}</div>
        <div class="tmeta">
          <span style="font-size:10px;color:var(--text3)">&#9200; ${esc(fmtDt(r.due_datetime))}</span>
        </div>
      </div>
      <button class="tdel" onclick="deleteReminder(${r.id})">&#215;</button>
    </div>`).join('');
}

async function addReminder() {
  const title = document.getElementById('r-title').value.trim();
  const date = document.getElementById('r-date').value;
  const time = document.getElementById('r-time').value;
  if (!title || !date || !time) return;
  const r = await api('/api/reminders', 'POST', {title, due_datetime: date + ' ' + time, sort_order: 0});
  if (!r.error) {
    reminders.unshift(r);
    document.getElementById('r-title').value = '';
    document.getElementById('r-date').value = '';
    document.getElementById('r-time').value = '';
    renderReminders();
  }
}

async function toggleReminder(id) {
  const r = reminders.find(x => x.id === id);
  if (!r) return;
  const status = r.status === 'done' ? 'active' : 'done';
  reminders = reminders.map(x => x.id === id ? Object.assign({}, x, {status}) : x);
  renderReminders();
  api('/api/reminders/' + id, 'PATCH', {status});
}

async function deleteReminder(id) {
  reminders = reminders.filter(r => r.id !== id);
  renderReminders();
  api('/api/reminders/' + id, 'DELETE');
}

// ── Drag to reorder ───────────────────────────────────────────────────────
let _drag = null;

function attachDrag(listId, reorderCb) {
  const list = document.getElementById(listId);
  if (!list) return;

  list.addEventListener('touchstart', function(e) {
    const row = e.target.closest('[data-drag-id]');
    if (!row) return;
    _drag = {
      row, list, reorderCb,
      startY: e.touches[0].clientY,
      active: false,
      timer: setTimeout(function() { _startDrag(e.touches[0]); }, 500)
    };
  }, {passive: true});

  list.addEventListener('touchmove', function(e) {
    if (!_drag) return;
    const dy = Math.abs(e.touches[0].clientY - _drag.startY);
    if (!_drag.active && dy > 8) { clearTimeout(_drag.timer); _drag = null; return; }
    if (!_drag.active) return;
    e.preventDefault();
    _moveDrag(e.touches[0]);
  }, {passive: false});

  list.addEventListener('touchend', function() {
    if (!_drag) return;
    clearTimeout(_drag.timer);
    if (_drag.active) _endDrag();
    _drag = null;
  });
}

function _startDrag(touch) {
  if (!_drag) return;
  _drag.active = true;
  const row = _drag.row;
  const rect = row.getBoundingClientRect();
  _drag.offsetY = touch.clientY - rect.top;
  const ghost = row.cloneNode(true);
  ghost.style.cssText = 'position:fixed;left:' + rect.left + 'px;top:' + rect.top + 'px;width:' + rect.width + 'px;opacity:0.85;pointer-events:none;z-index:999;box-shadow:0 8px 24px rgba(0,0,0,.4);background:var(--bg3);border-radius:10px';
  document.body.appendChild(ghost);
  _drag.ghost = ghost;
  _drag.ghostTop = rect.top;
  const ph = document.createElement('div');
  ph.style.cssText = 'height:' + rect.height + 'px;background:var(--bg3);opacity:0.3;border-radius:10px';
  row.parentNode.insertBefore(ph, row);
  row.style.display = 'none';
  _drag.ph = ph;
  navigator.vibrate && navigator.vibrate(20);
}

function _moveDrag(touch) {
  if (!_drag || !_drag.active) return;
  const y = _drag.ghostTop + (touch.clientY - _drag.startY);
  _drag.ghost.style.top = y + 'px';
  const rows = Array.from(_drag.list.querySelectorAll('[data-drag-id]')).filter(r => r !== _drag.row);
  let target = null;
  for (const r of rows) {
    const rect = r.getBoundingClientRect();
    if (touch.clientY < rect.top + rect.height / 2) { target = r; break; }
  }
  if (target) _drag.list.insertBefore(_drag.ph, target);
  else _drag.list.appendChild(_drag.ph);
}

function _endDrag() {
  if (!_drag || !_drag.active) return;
  _drag.ghost.remove();
  _drag.row.style.display = '';
  _drag.list.insertBefore(_drag.row, _drag.ph);
  _drag.ph.remove();
  const orderedIds = Array.from(_drag.list.querySelectorAll('[data-drag-id]')).map(r => parseInt(r.dataset.dragId));
  orderedIds.forEach(function(id, idx) { _drag.reorderCb(id, idx); });
}

attachDrag('t-list', function(id, order) {
  tasks = tasks.map(t => t.id === id ? Object.assign({}, t, {sort_order: order}) : t);
  api('/api/tasks/' + id + '/reorder', 'PATCH', {sort_order: order});
});
attachDrag('r-list', function(id, order) {
  reminders = reminders.map(r => r.id === id ? Object.assign({}, r, {sort_order: order}) : r);
  api('/api/reminders/' + id, 'PATCH', {sort_order: order});
});

// ── Projects tab ──────────────────────────────────────────────────────────────
let projShowArchived = false;
let projects = [];

async function loadProjects() {
  const el = document.getElementById('proj-list');
  el.innerHTML = sk(3);
  try {
    projects = await api('/api/projects');
    renderProjects();
  } catch(_) {
    el.innerHTML = '<div class="ctr">Watson offline</div>';
  }
}
loaders.projects = loadProjects;

function renderProjects() {
  const el = document.getElementById('proj-list');
  const filtered = projects.filter(p =>
    projShowArchived ? (p.status||'').toLowerCase() === 'archived' : (p.status||'').toLowerCase() !== 'archived'
  );
  if (!filtered.length) {
    el.innerHTML = '<div class="ctr">' + (projShowArchived ? 'No archived projects' : 'No projects yet') + '</div>';
    return;
  }
  el.innerHTML = filtered.map(p => {
    const s = (p.status || '').toLowerCase();
    const sc = s === 'active' ? 'status-active' : s === 'planned' ? 'status-planned' : 'status-archived';
    const restoreBtn = projShowArchived
      ? '<button class="btn btn-gh" style="flex:none;padding:4px 10px;font-size:11px" onclick="event.stopPropagation();restoreProject(\\'' + esc(p.slug) + '\\')">Restore</button>'
      : '';
    return '<div class="proj-card" onclick="openProjectWorkspace(\\'' + esc(p.slug) + '\\',\\'' + esc(p.name||p.slug).replace(/'/g,"\\'") + '\\')">' +
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">' +
        '<div class="proj-name">' + esc(p.name||p.slug) + '</div>' + restoreBtn +
      '</div>' +
      '<div style="display:flex;justify-content:space-between;align-items:center">' +
        '<span class="' + sc + '">' + esc(p.status||'') + '</span>' +
        '<span class="proj-meta">' + esc(p.last_updated||'') + '</span>' +
      '</div>' +
    '</div>';
  }).join('');
}

function showNewProjectForm() {
  const f = document.getElementById('proj-new-form');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
  if (f.style.display === 'block') document.getElementById('proj-new-name').focus();
}

async function createProject() {
  const name = document.getElementById('proj-new-name').value.trim();
  if (!name) return;
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
  const r = await api('/api/projects', 'POST', {slug, name});
  if (r.error) { alert('Error: ' + r.error); return; }
  document.getElementById('proj-new-name').value = '';
  document.getElementById('proj-new-form').style.display = 'none';
  loadProjects();
}

function toggleArchivedProjects() {
  projShowArchived = !projShowArchived;
  const btn = document.getElementById('arch-toggle');
  btn.classList.toggle('on', projShowArchived);
  btn.textContent = projShowArchived ? 'Show Active' : 'Show Archived';
  renderProjects();
}

async function restoreProject(slug) {
  const r = await api('/api/projects/' + slug + '/status', 'PATCH', {status: 'Active'});
  if (r.success) {
    const p = projects.find(x => x.slug === slug);
    if (p) p.status = 'Active';
    renderProjects();
  }
}

// ── Project workspace ─────────────────────────────────────────────────────────
let pwSlug = null;
let pwSessionId = null;
let pwChatHistory = [];
let pwAttachedContent = null;
let pwAttachedName = null;

async function openProjectWorkspace(slug, name) {
  pwSlug = slug;
  pwSessionId = null;
  pwChatHistory = [];
  pwAttachedContent = null;
  pwAttachedName = null;
  document.getElementById('pw-title').textContent = name;
  document.getElementById('pw-messages').innerHTML = '';
  document.getElementById('pw-summary-card').style.display = 'none';
  document.getElementById('pw-attach-indicator').style.display = 'none';
  document.getElementById('pw-kebab-menu').classList.remove('open');
  closeFileSidebar();
  document.getElementById('proj-workspace').classList.add('open');
  document.getElementById('nav').classList.add('hidden');
  document.body.style.overflow = 'hidden';
  try {
    const [proj, sessions] = await Promise.all([
      api('/api/projects/' + slug),
      api('/api/chat/sessions?project_slug=' + encodeURIComponent(slug))
    ]);
    if (proj.content) {
      const lines = proj.content.split('\\n');
      const summary = lines.find(l => l.trim() && !l.startsWith('#') && !l.startsWith('**') && !l.startsWith('- ') && !l.startsWith('|'));
      if (summary) {
        document.getElementById('pw-summary-text').textContent = summary.trim();
        document.getElementById('pw-summary-card').style.display = 'block';
      }
    }
    if (sessions && sessions.length) {
      pwSessionId = sessions[0].id;
      const msgs = await api('/api/chat/sessions/' + pwSessionId + '/messages');
      msgs.forEach(m => {
        _appendPWMsg(m.role === 'assistant' ? 'watson' : 'user', m.content);
        pwChatHistory.push({role: m.role, content: m.content});
      });
      document.getElementById('pw-messages').scrollTop = document.getElementById('pw-messages').scrollHeight;
    }
  } catch(_) {}
  document.getElementById('pw-input').focus();
}

function closeProjectWorkspace() {
  document.getElementById('proj-workspace').classList.remove('open');
  document.getElementById('nav').classList.remove('hidden');
  document.body.style.overflow = '';
  pwSlug = null;
}

function dismissSummary() {
  document.getElementById('pw-summary-card').style.display = 'none';
}

// ── PW messaging ──────────────────────────────────────────────────────────────
function _appendPWMsg(role, text) {
  const msgs = document.getElementById('pw-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap ' + role;
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  const time = document.createElement('div');
  time.className = 'msg-time';
  time.textContent = _fmtTime(new Date());
  wrap.appendChild(bubble);
  wrap.appendChild(time);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
}
function _showPWTyping() {
  const msgs = document.getElementById('pw-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap watson';
  wrap.id = 'pw-typing-wrap';
  wrap.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
}
function _hidePWTyping() { const el = document.getElementById('pw-typing-wrap'); if (el) el.remove(); }

async function handlePWFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/upload', {method:'POST', body:fd});
    const data = await r.json();
    if (data.success) {
      pwAttachedContent = data.content;
      pwAttachedName = data.filename;
      document.getElementById('pw-attach-name').textContent = data.filename;
      document.getElementById('pw-attach-indicator').style.display = 'block';
    }
  } catch(e) { alert('Upload error: ' + e.message); }
  input.value = '';
}
function clearPWAttachment() {
  pwAttachedContent = null; pwAttachedName = null;
  document.getElementById('pw-attach-indicator').style.display = 'none';
  document.getElementById('pw-attach-name').textContent = '';
}

async function sendPWChat() {
  const input = document.getElementById('pw-input');
  const text = input.value.trim();
  if (!text || !pwSlug) return;
  if (!pwSessionId) {
    try {
      const s = await api('/api/projects/' + pwSlug + '/chat', 'POST');
      pwSessionId = s.id;
    } catch(_) { return; }
  }
  input.value = '';
  const displayMsg = pwAttachedName ? text + '\\n\U0001F4CE ' + pwAttachedName : text;
  const ollamaMsg = pwAttachedContent
    ? '[Attached file: ' + pwAttachedName + ']\\n' + pwAttachedContent + '\\n\\n---\\n\\nUser message: ' + text
    : text;
  if (pwAttachedContent) clearPWAttachment();
  _appendPWMsg('user', displayMsg);
  api('/api/chat/sessions/' + pwSessionId + '/messages', 'POST', {role:'user', content:displayMsg});
  if (pwChatHistory.length === 0) {
    const title = text.length > 50 ? text.slice(0,50) + '...' : text;
    api('/api/chat/sessions/' + pwSessionId, 'PATCH', {title});
  }
  pwChatHistory.push({role:'user', content:ollamaMsg});
  if (pwChatHistory.length > 40) pwChatHistory = pwChatHistory.slice(-40);
  _showPWTyping();
  try {
    const data = await api('/api/chat', 'POST', {message:ollamaMsg, history:pwChatHistory.slice(0,-1)});
    _hidePWTyping();
    const reply = data.response || '(no response)';
    _appendPWMsg('watson', reply);
    pwChatHistory.push({role:'assistant', content:reply});
    if (pwChatHistory.length > 40) pwChatHistory = pwChatHistory.slice(-40);
    api('/api/chat/sessions/' + pwSessionId + '/messages', 'POST', {role:'assistant', content:reply});
  } catch(_) { _hidePWTyping(); _appendPWMsg('watson', 'Watson is offline.'); }
}
document.getElementById('pw-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendPWChat(); }
});

// PW voice
(function() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = document.getElementById('pw-mic-btn');
  if (!SR) { btn.style.display = 'none'; return; }
  const rec = new SR();
  rec.continuous = false; rec.interimResults = false; rec.lang = 'en-US';
  let active = false;
  function stopRec() { active = false; btn.classList.remove('recording'); try { rec.stop(); } catch(_) {} }
  rec.onresult = function(e) {
    const t = e.results[0][0].transcript.trim();
    stopRec();
    if (t) { document.getElementById('pw-input').value = t; setTimeout(sendPWChat, 100); }
  };
  rec.onerror = function() { stopRec(); };
  rec.onend = function() { if (active) stopRec(); };
  window.togglePWVoice = function() {
    if (active) { stopRec(); return; }
    active = true; btn.classList.add('recording');
    try { rec.start(); } catch(_) { stopRec(); }
  };
})();

// ── File sidebar ──────────────────────────────────────────────────────────────
async function openFileSidebar() {
  document.getElementById('pw-sidebar').classList.add('open');
  await loadFileSidebar();
}
function closeFileSidebar() { document.getElementById('pw-sidebar').classList.remove('open'); }

async function loadFileSidebar() {
  const el = document.getElementById('pw-sidebar-content');
  if (!pwSlug) return;
  el.innerHTML = '<div style="color:var(--text3);font-size:12px;padding:4px 0">Loading...</div>';
  try {
    const data = await api('/api/projects/' + pwSlug + '/files');
    let html = '<div class="pw-file-section"><div class="pw-file-section-lbl">Watson Notes</div>';
    if (data.notes && data.notes.length) {
      html += data.notes.map(f => {
        const d = new Date(f.mtime * 1000).toLocaleDateString('en-US', {month:'short', day:'numeric'});
        return '<div class="pw-file-row" onclick="injectFile(\\'' + esc(pwSlug) + '\\',\\'' + esc(f.name) + '\\',\\'notes\\')">' +
          '<div class="pw-file-name">' + esc(f.name) + '</div>' +
          '<div class="pw-file-date">' + d + '</div></div>';
      }).join('');
    } else {
      html += '<div style="font-size:11px;color:var(--text3);padding:4px 2px">No notes yet</div>';
    }
    html += '</div><div class="pw-file-section"><div class="pw-file-section-lbl">Uploaded Files</div>';
    if (data.files && data.files.length) {
      html += data.files.map(f => {
        const d = new Date(f.mtime * 1000).toLocaleDateString('en-US', {month:'short', day:'numeric'});
        return '<div class="pw-file-row" onclick="injectFile(\\'' + esc(pwSlug) + '\\',\\'' + esc(f.name) + '\\',\\'files\\')">' +
          '<div class="pw-file-name">' + esc(f.name) + '</div>' +
          '<div class="pw-file-date">' + d + '</div></div>';
      }).join('');
    } else {
      html += '<div style="font-size:11px;color:var(--text3);padding:4px 2px">No files uploaded</div>';
    }
    html += '</div>';
    el.innerHTML = html;
  } catch(_) {
    el.innerHTML = '<div style="color:var(--danger);font-size:12px">Failed to load files.</div>';
  }
}

async function injectFile(slug, filename, section) {
  try {
    const r = await fetch('/api/projects/' + slug + '/files/' + encodeURIComponent(filename) + '?section=' + section);
    const text = await r.text();
    pwAttachedContent = text.slice(0, 8000);
    pwAttachedName = filename;
    document.getElementById('pw-attach-name').textContent = filename;
    document.getElementById('pw-attach-indicator').style.display = 'block';
    closeFileSidebar();
    document.getElementById('pw-input').focus();
  } catch(e) { alert('Could not load file: ' + e.message); }
}

// ── Note modal ────────────────────────────────────────────────────────────────
function openNoteModal() {
  document.getElementById('pw-note-content').value = '';
  document.getElementById('pw-note-modal').classList.add('open');
  setTimeout(function() { document.getElementById('pw-note-content').focus(); }, 50);
}
function closeNoteModal() { document.getElementById('pw-note-modal').classList.remove('open'); }

async function saveNote() {
  const text = document.getElementById('pw-note-content').value.trim();
  if (!text || !pwSlug) return;
  const r = await api('/api/projects/' + pwSlug + '/notes', 'POST', {note: text});
  if (r.ok) { closeNoteModal(); loadFileSidebar(); }
  else { alert('Failed to save note: ' + (r.error || 'Unknown error')); }
}

// ── Kebab / archive / delete ──────────────────────────────────────────────────
function toggleKebabMenu(e) {
  e.stopPropagation();
  document.getElementById('pw-kebab-menu').classList.toggle('open');
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('#pw-kebab-menu') && e.target.id !== 'pw-kebab-btn') {
    document.getElementById('pw-kebab-menu').classList.remove('open');
  }
});

async function archiveProject() {
  document.getElementById('pw-kebab-menu').classList.remove('open');
  if (!pwSlug) return;
  const r = await api('/api/projects/' + pwSlug + '/status', 'PATCH', {status: 'Archived'});
  if (r.success) {
    const p = projects.find(x => x.slug === pwSlug);
    if (p) p.status = 'Archived';
    closeProjectWorkspace();
  } else { alert('Archive failed: ' + (r.error || 'Unknown error')); }
}

function confirmDeleteProject() {
  document.getElementById('pw-kebab-menu').classList.remove('open');
  if (!pwSlug) return;
  if (!confirm('This will permanently delete the project and all its files. Are you sure?')) return;
  deleteProject();
}

async function deleteProject() {
  const slug = pwSlug;
  const r = await api('/api/projects/' + slug, 'DELETE');
  if (r.success) {
    projects = projects.filter(p => p.slug !== slug);
    closeProjectWorkspace();
    renderProjects();
  } else { alert('Delete failed: ' + (r.error || 'Unknown error')); }
}
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


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
