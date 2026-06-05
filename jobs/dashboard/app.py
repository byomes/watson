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
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;font-size:14px;min-height:100vh}
button{cursor:pointer;font-family:inherit}
input,select,textarea{font-family:inherit}
#hdr{position:fixed;top:0;left:0;right:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:0 16px;height:48px;display:flex;align-items:center;justify-content:space-between}
#hdr h1{font-size:15px;font-weight:600;letter-spacing:.02em}
#theme-btn{background:none;border:none;color:var(--text2);font-size:17px;padding:4px 8px;border-radius:6px}
#theme-btn:hover{background:var(--bg3)}
#main{padding:60px 0 72px;min-height:100vh}
.tab{display:none;padding:12px 14px}
.tab.active{display:block}
#nav{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;padding-bottom:env(safe-area-inset-bottom)}
.nb{flex:1;background:none;border:none;color:var(--text3);padding:10px 0 8px;display:flex;flex-direction:column;align-items:center;gap:2px;font-size:10px;letter-spacing:.04em;text-transform:uppercase}
.nb .ic{font-size:18px;line-height:1}
.nb.active{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:13px;margin-bottom:10px;transition:opacity .2s}
.card-title{font-size:13px;font-weight:500;line-height:1.4;margin-bottom:4px}
.meta{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.summary{font-size:12px;color:var(--text2);line-height:1.5;margin-bottom:8px}
.row{display:flex;gap:6px;margin-top:8px}
.btn{flex:1;padding:7px 6px;font-size:11px;border-radius:7px;border:1px solid;font-family:'DM Mono',monospace}
.btn-g{background:rgba(63,185,80,.1);border-color:rgba(63,185,80,.3);color:var(--success)}
.btn-r{background:rgba(218,54,51,.1);border-color:rgba(218,54,51,.25);color:var(--danger)}
.btn-b{background:rgba(56,139,253,.1);border-color:rgba(56,139,253,.25);color:var(--accent)}
.btn-gh{background:var(--bg3);border-color:var(--border);color:var(--text2)}
.btn-p{background:var(--accent);border-color:var(--accent);color:#fff}
.badge{display:inline-block;font-size:10px;padding:2px 7px;border-radius:4px;letter-spacing:.04em;text-transform:uppercase;font-family:'DM Mono',monospace}
.bh{background:rgba(218,54,51,.12);color:var(--danger)}
.bm{background:rgba(210,153,34,.12);color:var(--warn)}
.bl{background:rgba(63,185,80,.12);color:var(--success)}
.fbox{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:12px}
.flabel{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
input[type=text],input[type=date],select,textarea{display:block;width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:9px 10px;color:var(--text);font-size:12px;outline:none;margin-bottom:7px}
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
.pill{flex:1;padding:6px 0;font-size:11px;text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--border);border-radius:7px;background:var(--bg2);color:var(--text3)}
.pill.active{background:var(--accent);border-color:var(--accent);color:#fff}
.cr{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);cursor:pointer}
.av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px;font-weight:500}
.cdet{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:11px;margin-bottom:4px}
.dl{font-size:12px;color:var(--text2);margin-bottom:4px}
.slabel{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px 2px}
.bk{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:11px 12px;margin-bottom:8px}
.bk-title{font-size:13px;color:var(--text);margin-bottom:3px;line-height:1.4}
.bk-src{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.bk-sum{font-size:11px;color:var(--text2);line-height:1.5;margin-bottom:8px}
.sk{background:var(--bg3);border-radius:4px;animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.8}}
.ctr{text-align:center;padding:40px 0;color:var(--text2);font-size:13px}
.sbar{width:100%;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:9px 11px;color:var(--text);font-size:13px;outline:none;margin-bottom:10px}
.sbar:focus{border-color:var(--accent)}
</style>
</head>
<body>

<div id="hdr">
  <h1>Watson</h1>
  <button id="theme-btn" onclick="toggleTheme()"></button>
</div>

<div id="main">

  <div id="tab-briefing" class="tab active">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em">Today's Briefing</div>
      <span id="b-offline" style="font-size:10px;color:var(--warn);display:none">· offline</span>
    </div>
    <div id="b-list"></div>
  </div>

  <div id="tab-tasks" class="tab">
    <div class="fbox">
      <div class="flabel">Add Task</div>
      <input type="text" id="t-title" placeholder="Task title…" onkeydown="if(event.key==='Enter')addTask()">
      <div style="display:flex;gap:6px">
        <input type="date" id="t-due" style="flex:1">
        <select id="t-pri" style="flex:1">
          <option value="high">High</option>
          <option value="medium" selected>Medium</option>
          <option value="low">Low</option>
        </select>
      </div>
      <button class="btn btn-p" style="width:100%;padding:9px;border-radius:7px;border:none;font-size:12px" onclick="addTask()">+ Add Task</button>
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

  <div id="tab-contacts" class="tab">
    <div style="display:flex;gap:8px;margin-bottom:4px">
      <input class="sbar" id="c-search" placeholder="Search contacts…" oninput="renderContacts()" style="flex:1;margin-bottom:0">
      <button id="c-add-btn" onclick="toggleAddContact()" style="padding:9px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:16px;flex-shrink:0">+</button>
    </div>
    <div id="c-add-form" class="fbox" style="display:none;margin-top:10px">
      <div class="flabel">New Contact</div>
      <input type="text" id="nc-name" placeholder="Name *">
      <input type="text" id="nc-email" placeholder="Email">
      <input type="text" id="nc-phone" placeholder="Phone">
      <input type="text" id="nc-rel" placeholder="Relationship">
      <input type="text" id="nc-notes" placeholder="Notes">
      <button class="btn btn-p" style="width:100%;padding:9px;border:none;border-radius:7px;font-size:12px" onclick="saveNewContact()">Add Contact</button>
    </div>
    <div id="c-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadContacts()">Retry</button>
    </div>
    <div id="c-list"></div>
  </div>

  <div id="tab-reading" class="tab">
    <div id="r-offline" class="ctr" style="display:none">Watson offline<br><br>
      <button class="btn btn-gh" style="flex:none;padding:7px 14px;width:auto" onclick="loadReading()">Retry</button>
    </div>
    <div id="r-list"></div>
  </div>

</div>

<nav id="nav">
  <button class="nb active" id="nav-briefing" onclick="switchTab('briefing')"><span class="ic">📰</span><span>Briefing</span></button>
  <button class="nb" id="nav-tasks" onclick="switchTab('tasks')"><span class="ic">✓</span><span>Tasks</span></button>
  <button class="nb" id="nav-contacts" onclick="switchTab('contacts')"><span class="ic">👤</span><span>Contacts</span></button>
  <button class="nb" id="nav-reading" onclick="switchTab('reading')"><span class="ic">📖</span><span>Reading</span></button>
</nav>

<script>
// ── Theme ─────────────────────────────────────────────────────────────────
const root = document.documentElement;
function _syncThemeBtn() {
  document.getElementById('theme-btn').textContent = root.getAttribute('data-theme') === 'dark' ? '☀' : '☾';
}
_syncThemeBtn();
function toggleTheme() {
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('watson-theme', next);
  _syncThemeBtn();
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
const TABS = ['briefing','tasks','contacts','reading'];
const loaded = {briefing:false, tasks:false, contacts:false, reading:false};
const loaders = {};

function switchTab(name) {
  TABS.forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
    document.getElementById('nav-' + t).classList.toggle('active', t === name);
  });
  if (!loaded[name]) { loaded[name] = true; loaders[name](); }
}

// ── Briefing ──────────────────────────────────────────────────────────────
let bItems = [];

async function loadBriefing() {
  document.getElementById('b-list').innerHTML = sk(3);
  try {
    bItems = await api('/api/briefing');
    renderBriefing();
  } catch(_) {
    document.getElementById('b-offline').style.display = 'block';
    document.getElementById('b-list').innerHTML = '';
  }
}
loaders.briefing = loadBriefing;

function renderBriefing() {
  const el = document.getElementById('b-list');
  if (!bItems.length) { el.innerHTML = '<div class="ctr">No items today</div>'; return; }
  el.innerHTML = bItems.map(i => `
    <div class="card" id="bi-${i.id}">
      <div class="card-title">${esc(i.title)}</div>
      <div class="meta">${esc(i.source_name)}</div>
      ${i.summary ? '<div class="summary">' + esc(i.summary) + '</div>' : ''}
      <div class="row">
        <button class="btn btn-g" onclick="bAction(${i.id},'approve')">👍 Approve</button>
        <button class="btn btn-r" onclick="bAction(${i.id},'reject')">👎 Reject</button>
        <button class="btn btn-b" onclick="bAction(${i.id},'facebook')">📘 FB</button>
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

loaded.briefing = true;
loadBriefing();

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
    <div class="tr" id="task-${t.id}">
      <button class="tc ${t.status === 'done' ? 'done' : ''}" onclick="toggleTask(${t.id})"></button>
      <div class="tbody">
        <div class="ttitle ${t.status === 'done' ? 'done' : ''}">${esc(t.title)}</div>
        <div class="tmeta">
          <span class="badge b${t.priority[0]}">${t.priority}</span>
          ${t.due_date ? '<span style="font-size:10px;color:var(--text3)">' + esc(t.due_date) + '</span>' : ''}
        </div>
      </div>
      <button class="tdel" onclick="deleteTask(${t.id})">×</button>
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
        (c.email ? '<div class="dl">✉ ' + esc(c.email) + '</div>' : '') +
        (c.phone ? '<div class="dl">📞 ' + esc(c.phone) + '</div>' : '') +
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
const S_LABEL = {reading:'📖 Reading', unread:'📋 Unread', finished:'✅ Finished'};

async function loadReading() {
  document.getElementById('r-list').innerHTML = sk(4);
  document.getElementById('r-offline').style.display = 'none';
  try {
    books = await api('/api/reading');
    renderReading();
  } catch(_) {
    document.getElementById('r-list').innerHTML = '';
    document.getElementById('r-offline').style.display = 'block';
  }
}
loaders.reading = loadReading;

function renderReading() {
  const el = document.getElementById('r-list');
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
            '<button class="btn btn-gh" onclick="setBookStatus(' + b.id + ',\'' + s + '\')">→ ' + s + '</button>'
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


# ── Tasks API ─────────────────────────────────────────────────────────────────

@app.route("/api/tasks")
def tasks_list():
    rows = _db().execute(
        "SELECT * FROM tasks ORDER BY created_at DESC"
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app.run(host="0.0.0.0", port=5200, debug=False)
