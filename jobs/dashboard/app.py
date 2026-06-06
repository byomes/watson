"""Watson dashboard — port 5200, Tailscale-only."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, g, jsonify, request
from jobs.people.api import people_create, people_delete, people_list, people_update

DB = os.path.expanduser("~/watson/data/watson.db")
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
    c.commit()
    c.close()


_bootstrap()


# ── Shell ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Watson</title>
<script>(function(){var t=localStorage.getItem('watson-theme')||'dark';document.documentElement.setAttribute('data-theme',t);})();</script>
<style>
:root[data-theme="dark"]{
  --bg:#0d1117;--bg2:#161b22;--bg3:#21262d;
  --border:#30363d;--text:#e6edf3;--text2:#8b949e;--text3:#6e7681;
  --accent:#388bfd;--success:#3fb950;--danger:#da3633;--warn:#d29922;
}
:root[data-theme="light"]{
  --bg:#ffffff;--bg2:#f6f8fa;--bg3:#eaeef2;
  --border:#d0d7de;--text:#1f2328;--text2:#636c76;--text3:#818b98;
  --accent:#0969da;--success:#1a7f37;--danger:#cf222e;--warn:#9a6700;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;font-size:15px;min-height:100vh}
button{cursor:pointer;font-family:inherit}
input,select,textarea{font-family:inherit}
#hdr{position:fixed;top:0;left:0;right:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:0 16px;height:54px;display:flex;align-items:center;justify-content:space-between}
#hdr-identity{display:flex;align-items:center;gap:10px}
#hdr-title{font-size:15px;font-weight:700;letter-spacing:.02em;line-height:1.2}
#hdr-sub{font-size:10px;color:var(--text3);font-weight:400;line-height:1.2}
#gear-btn{background:none;border:none;color:var(--text2);font-size:24px;padding:4px 10px;border-radius:8px;line-height:1}
#gear-btn:hover{background:var(--bg3)}
#main{padding:66px 0 84px;min-height:100vh}
.tab{display:none;padding:14px}
.tab.active{display:block}
#nav{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;padding-bottom:env(safe-area-inset-bottom)}
.nb{flex:1;background:none;border:none;color:var(--text3);padding:12px 0 10px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:12px;letter-spacing:.04em;text-transform:uppercase}
.nb .ic{width:22px;height:22px;display:flex;align-items:center;justify-content:center}
.nb.active{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:13px;margin-bottom:10px;transition:opacity .2s;box-shadow:0 1px 3px rgba(0,0,0,0.12)}
.card-title{font-size:13px;font-weight:500;line-height:1.4;margin-bottom:4px}
.meta{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.summary{font-size:12px;color:var(--text2);line-height:1.5;margin-bottom:8px}
.row{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.btn{flex:1;padding:7px 6px;font-size:11px;border-radius:10px;border:1px solid;font-family:'DM Mono',monospace}
.btn-g{background:rgba(63,185,80,.1);border-color:rgba(63,185,80,.3);color:var(--success)}
.btn-r{background:rgba(218,54,51,.1);border-color:rgba(218,54,51,.25);color:var(--danger)}
.btn-b{background:rgba(56,139,253,.1);border-color:rgba(56,139,253,.25);color:var(--accent)}
.btn-gh{background:var(--bg3);border-color:var(--border);color:var(--text2)}
.btn-p{background:var(--accent);border-color:var(--accent);color:#fff}
.badge{display:inline-block;font-size:10px;padding:2px 7px;border-radius:4px;letter-spacing:.04em;text-transform:uppercase;font-family:'DM Mono',monospace}
.bh{background:rgba(218,54,51,.12);color:var(--danger)}
.bm{background:rgba(210,153,34,.12);color:var(--warn)}
.bl{background:rgba(63,185,80,.12);color:var(--success)}
.fbox{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:12px;margin-bottom:12px}
.flabel{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
input[type=text],input[type=date],input[type=time],select,textarea{display:block;width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:9px;padding:11px 12px;color:var(--text);font-size:12px;outline:none;margin-bottom:7px}
input:focus,select:focus{border-color:var(--accent)}
select option{background:var(--bg2)}
.tr{display:flex;align-items:flex-start;gap:9px;padding:10px 0;border-bottom:1px solid var(--border)}
.tc{width:20px;height:20px;border-radius:5px;border:2px solid var(--border);background:transparent;flex-shrink:0;display:flex;align-items:center;justify-content:center;margin-top:1px}
.tc.done{background:var(--success);border-color:var(--success)}
.tc.done::after{content:'✓';color:#fff;font-size:11px}
.tbody{flex:1;min-width:0}
.ttitle{font-size:13px;color:var(--text);line-height:1.4;margin-bottom:3px}
.ttitle.done{text-decoration:line-through;color:var(--text3)}
.tmeta{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.tdel{background:none;border:none;color:var(--text3);font-size:16px;padding:2px 4px;flex-shrink:0}
.tdel:hover{color:var(--danger)}
.pills{display:flex;gap:6px;margin-bottom:12px}
.pill{flex:1;padding:6px 0;font-size:11px;text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--border);border-radius:10px;background:var(--bg2);color:var(--text3)}
.pill.active{background:var(--accent);border-color:var(--accent);color:#fff}
.cr{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);cursor:pointer}
.av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px;font-weight:500}
.cdet{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:11px;margin-bottom:4px}
.dl{font-size:12px;color:var(--text2);margin-bottom:4px}
.slabel{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px 2px}
.bk{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:11px 12px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,0.12)}
.bk-title{font-size:13px;color:var(--text);margin-bottom:3px;line-height:1.4}
.bk-src{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.bk-sum{font-size:11px;color:var(--text2);line-height:1.5;margin-bottom:8px}
.sk{background:var(--bg3);border-radius:4px;animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.8}}
#tab-chat{padding:0;display:none;flex-direction:column;height:calc(100vh - 54px - 84px)}
#tab-chat.active{display:flex}
#chat-messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.msg-wrap{display:flex;flex-direction:column}
.msg-wrap.user{align-items:flex-end}
.msg-wrap.watson{align-items:flex-start}
.msg-bubble{max-width:78%;padding:10px 13px;border-radius:16px;font-size:13px;line-height:1.5;word-break:break-word;white-space:pre-wrap}
.msg-wrap.user .msg-bubble{background:#b07d10;color:#fff}
.msg-wrap.watson .msg-bubble{background:var(--bg2);color:var(--text);border:1px solid var(--border)}
.typing-indicator{display:flex;gap:4px;align-items:center;padding:10px 13px;background:var(--bg2);border:1px solid var(--border);border-radius:16px;width:fit-content}
.typing-indicator span{width:7px;height:7px;background:var(--text3);border-radius:50%;animation:tbounce 1.2s ease-in-out infinite}
.typing-indicator span:nth-child(2){animation-delay:.2s}
.typing-indicator span:nth-child(3){animation-delay:.4s}
@keyframes tbounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
#chat-input-area{padding:10px 14px;border-top:1px solid var(--border);display:flex;gap:8px;background:var(--bg);flex-shrink:0}
#chat-input{flex:1;background:var(--bg2);border:1px solid var(--border);border-radius:22px;padding:10px 14px;color:var(--text);font-size:13px;outline:none}
#chat-input:focus{border-color:var(--accent)}
#chat-send-btn{background:var(--accent);border:none;border-radius:22px;padding:10px 16px;color:#fff;font-size:13px;flex-shrink:0;cursor:pointer}
.ctr{text-align:center;padding:40px 0;color:var(--text2);font-size:13px}
.sbar{width:100%;background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:11px 12px;color:var(--text);font-size:13px;outline:none;margin-bottom:10px}
.sbar:focus{border-color:var(--accent)}
.ph-hdr{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px}
.theme-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0}
.theme-lbl{font-size:14px;color:var(--text)}
.theme-btn2{padding:7px 16px;background:var(--bg3);border:1px solid var(--border);border-radius:10px;color:var(--text2);font-size:12px}
#settings-panel{position:fixed;top:54px;right:0;left:0;z-index:9;background:var(--bg2);border-bottom:1px solid var(--border);padding:12px 16px;display:none}
</style>
</head>
<body>

<div id="hdr">
  <div id="hdr-identity">
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg"><rect width="32" height="32" rx="4" fill="#111827"/><text x="16" y="24" text-anchor="middle" font-family="Georgia,serif" font-size="21" font-weight="bold" fill="white">W</text></svg>
    <div>
      <div id="hdr-title">Watson</div>
      <div id="hdr-sub">Digital Assistant to Dr. Bill Yomes</div>
    </div>
  </div>
  <button id="gear-btn" onclick="toggleSettings(event)"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>
</div>

<div id="settings-panel">
  <div class="flabel">Appearance</div>
  <div class="theme-row">
    <span class="theme-lbl" id="theme-lbl-text">Dark mode</span>
    <button class="theme-btn2" id="theme-toggle-btn" onclick="toggleTheme()">Switch to Light</button>
  </div>
  <div class="theme-row" style="margin-top:6px">
    <span class="theme-lbl">Contacts</span>
    <button class="theme-btn2" onclick="document.getElementById('settings-panel').style.display='none';switchTab('contacts')">Open Contacts</button>
  </div>
</div>

<div id="main">

  <div id="tab-chat" class="tab active">
    <div id="chat-messages"></div>
    <div id="chat-input-area">
      <input id="chat-input" type="text" placeholder="Message Watson…">
      <button id="chat-send-btn" onclick="sendChat()">Send</button>
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


</div>

<nav id="nav">
  <button class="nb active" id="nav-chat" onclick="switchTab('chat')">
    <span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></span>
    <span>Chat</span>
  </button>
  <button class="nb" id="nav-briefing" onclick="switchTab('briefing')">
    <span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="9" x2="9" y2="21"/></svg></span>
    <span>Briefing</span>
  </button>
  <button class="nb" id="nav-tasks" onclick="switchTab('tasks')">
    <span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg></span>
    <span>Tasks</span>
  </button>
  <button class="nb" id="nav-reminders" onclick="switchTab('reminders')">
    <span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg></span>
    <span>Reminders</span>
  </button>
  <button class="nb" id="nav-reading" onclick="switchTab('reading')">
    <span class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></span>
    <span>Reading</span>
  </button>
</nav>

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
function toggleSettings(e) {
  e.stopPropagation();
  const p = document.getElementById('settings-panel');
  p.style.display = getComputedStyle(p).display === 'none' ? 'block' : 'none';
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('#settings-panel') && e.target.id !== 'gear-btn') {
    document.getElementById('settings-panel').style.display = 'none';
  }
});

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
const TABS = ['chat','briefing','tasks','reminders','contacts','reading'];
const loaded = {chat:true, briefing:false, tasks:false, reminders:false, contacts:false, reading:false};
const loaders = {};

function switchTab(name) {
  TABS.forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
    const navEl = document.getElementById('nav-' + t);
    if (navEl) navEl.classList.toggle('active', t === name);
  });
  if (!loaded[name]) { loaded[name] = true; if (loaders[name]) loaders[name](); }
}

// ── Chat ──────────────────────────────────────────────────────────────────
let chatHistory = [];

loaders.chat = function() {};

function _appendMsg(role, text) {
  const msgs = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap ' + role;
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  wrap.appendChild(bubble);
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

async function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  _appendMsg('user', text);
  chatHistory.push({role: 'user', content: text});
  if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
  _showTyping();
  try {
    const data = await api('/api/chat', 'POST', {message: text, history: chatHistory.slice(0, -1)});
    _hideTyping();
    const reply = data.response || '(no response)';
    _appendMsg('watson', reply);
    chatHistory.push({role: 'assistant', content: reply});
    if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
  } catch(_) {
    _hideTyping();
    _appendMsg('watson', 'Watson is offline.');
  }
}

document.getElementById('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

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

loaders.briefing = loadBriefing;

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
  document.getElementById('c-add-btn').textContent = show ? '×' : '+';
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


# ── Chat API ─────────────────────────────────────────────────────────────────

WATSON_SYSTEM = (
    "You are Watson, Dr. Bill Yomes' AI-powered digital assistant. "
    "You are terse, direct, and efficient. You never pastor or speak theologically "
    "without permission. You never guess — if uncertain, you say so. "
    "You act on Dr. Bill's behalf under his supervision."
)


@app.route("/api/chat", methods=["POST"])
def chat():
    import requests as _req
    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"error": "message required"}), 400
    messages = [{"role": "system", "content": WATSON_SYSTEM}]
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
    from jobs.calendar.calendar import mark_day_busy_from_now
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
    from jobs.calendar.calendar import get_todays_events
    try:
        events = get_todays_events()
        return jsonify(events)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="0.0.0.0", port=5200, debug=False)
