/* watson.js — Dashboard UI  */

// ─── State ────────────────────────────────────────────────────────────────────
let activePage = 'home';
let _pendingOpenIdx = null;
let _pendingNoteTypes = {};
let _homeTaskTab = 'catalyst';
let _notesType = 'pastoral';
let chatHistory = [];
let chatMemoryContext = '';
let chatIdleTimer = null;
let lastKbResult = null;

// ─── Utilities ────────────────────────────────────────────────────────────────

async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

function setContent(html) {
  document.getElementById('page-content').innerHTML = html;
}

function todayLabel() {
  return new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
}

function fmtTime(iso) {
  if (!iso || !iso.includes('T')) return 'All day';
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  } catch { return iso; }
}

function fmtCalTime(iso) {
  if (!iso || !iso.includes('T')) return 'All day';
  try {
    const dt  = new Date(iso);
    const now = new Date();
    const tod = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tom = new Date(tod); tom.setDate(tod.getDate() + 1);
    const evDay = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
    const time  = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
    if (evDay.getTime() === tod.getTime()) return `Today ${time}`;
    if (evDay.getTime() === tom.getTime()) return `Tomorrow ${time}`;
    return `${dt.toLocaleDateString('en-US', { weekday: 'short' })} ${time}`;
  } catch { return iso; }
}

function fmtGenerated(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return iso; }
}

function fmtTaskDue(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr + 'T00:00:00');
    const opts = d.getFullYear() !== new Date().getFullYear()
      ? { month: 'short', day: 'numeric', year: 'numeric' }
      : { month: 'short', day: 'numeric' };
    return d.toLocaleDateString('en-US', opts);
  } catch { return dateStr; }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function priClass(p) {
  if (!p) return 'pri-3';
  return `pri-${p}`;
}

function priLabel(p) {
  return p || '3';
}

function catLabel(c) {
  if (c === 'fms') return 'FMS';
  if (c === 'personal') return 'Personal';
  return 'Catalyst';
}

let _openCatDrop = null;

// ─── Navigation ───────────────────────────────────────────────────────────────

function switchTab(page) {
  activePage = page;

  document.querySelectorAll('.nb').forEach(b => b.classList.remove('active'));
  const navBtn = document.getElementById('nav-' + page);
  if (navBtn) navBtn.classList.add('active');

  switch (page) {
    case 'home':      renderHome();      break;
    case 'notes':     renderNotes();     break;
    case 'briefing':  renderBriefing();  break;
    case 'reminders': renderReminders(); break;
    case 'more':      renderMore();      break;
    case 'chat':      renderChat();      break;
  }
}

// ─── Home page ────────────────────────────────────────────────────────────────

async function renderHome() {
  setContent('<div class="loading">Loading&hellip;</div>');
  _homeTaskTab = 'catalyst';

  const [pendingRes, calRes, openTasksRes, remindersRes, briefingRes, compTasksRes] = await Promise.allSettled([
    api('/api/pending'),
    api('/api/calendar/today'),
    api('/api/team/members/12/tasks?status=open&category=catalyst'),
    api('/api/reminders'),
    api('/api/briefing'),
    api('/api/team/members/12/tasks?status=completed&category=catalyst'),
  ]);

  const pending   = pendingRes.status   === 'fulfilled' ? pendingRes.value   : [];
  const calEvents = calRes.status       === 'fulfilled' ? calRes.value       : [];
  const reminders = remindersRes.status === 'fulfilled' ? remindersRes.value : [];
  const briefing  = briefingRes.status  === 'fulfilled' ? briefingRes.value  : [];

  const openTasks = openTasksRes.status === 'fulfilled' && Array.isArray(openTasksRes.value) ? openTasksRes.value : [];
  const compTasks = compTasksRes.status === 'fulfilled' && Array.isArray(compTasksRes.value) ? compTasksRes.value : [];
  const displayTasks = [...openTasks, ...compTasks];

  let html = '';

  // 1. Awaiting You (hidden if empty)
  if (Array.isArray(pending) && pending.length) {
    html += `<div class="sec-label">Awaiting You</div>`;
    _pendingOpenIdx = null;
    pending.forEach((item, idx) => {
      const type    = (item.type || 'NOTE').toUpperCase();
      const bc      = `badge-${type === 'EMAIL' ? 'EMAIL' : type === 'CALENDAR' ? 'CALENDAR' : 'NOTE'}`;
      const isNote  = type === 'NOTE' && item.id;
      html += `<div class="card" id="pending-card-${idx}" style="position:relative">
        ${isNote ? `<div style="position:absolute;top:0;right:0;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--red);font-size:18px;-webkit-tap-highlight-color:transparent" onclick="deleteInlineNote(${item.id},${idx})" title="Delete">✕</div>` : ''}
        <div class="card-title"${isNote ? ' style="padding-right:44px"' : ''}>${esc(item.title || item.subject || 'Pending item')}</div>
        ${item.subtitle ? `<div class="card-sub">${esc(item.subtitle)}</div>` : ''}
        <span class="badge ${bc}">${esc(type)}</span>
        ${isNote ? `
          <div id="pending-exp-${idx}" style="display:none;margin-top:10px">
            <div style="display:flex;border:1px solid var(--border);border-radius:var(--r-btn);overflow:hidden;margin-bottom:8px">
              <button id="pending-type-pastoral-${idx}" onclick="setPendingNoteType(${idx},'pastoral')"
                style="flex:1;padding:5px 0;border:none;cursor:pointer;background:var(--gold);color:#0f0f0f;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Pastoral</button>
              <button id="pending-type-leadership-${idx}" onclick="setPendingNoteType(${idx},'leadership')"
                style="flex:1;padding:5px 0;border:none;cursor:pointer;background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Leadership</button>
            </div>
            <textarea id="pending-ta-${idx}" rows="3"
              style="display:block;width:100%;margin-bottom:8px;padding:8px 10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:13px;outline:none;resize:vertical;box-sizing:border-box"
              placeholder="Pastoral note…"></textarea>
            <div style="display:flex;gap:8px">
              <button onclick="saveInlineNote(${item.id},${idx})" style="flex:1;padding:7px;background:var(--gold);color:#0f0f0f;border:none;border-radius:var(--r-btn);font-weight:600;font-family:inherit;font-size:13px;cursor:pointer">Save</button>
              <button onclick="skipInlineNote(${item.id},${idx})" style="padding:7px 14px;background:none;border:1px solid var(--border);border-radius:var(--r-btn);color:var(--muted);font-family:inherit;font-size:13px;cursor:pointer">Skip</button>
            </div>
          </div>
          <div style="margin-top:8px;cursor:pointer;font-size:11px;font-family:'DM Mono',monospace;color:var(--gold);letter-spacing:.04em" onclick="togglePendingExp(${idx})" id="pending-tog-${idx}">+ ADD NOTE</div>
        ` : ''}
      </div>`;
    });
  }

  // 2. Today's Agenda
  html += `<div class="sec-label">Next 36 Hours</div>`;
  const evArr = Array.isArray(calEvents) ? calEvents : (calEvents && !calEvents.error ? [calEvents] : []);
  if (!evArr.length) {
    html += `<div class="empty">No appointments today.</div>`;
  } else {
    html += `<div class="cal-card">`;
    evArr.forEach(ev => {
      html += `
        <div class="cal-row">
          <span class="cal-time">${esc(fmtCalTime(ev.start))}</span>
          <span class="cal-event">${esc(ev.summary || '(No title)')}</span>
        </div>`;
    });
    html += `</div>`;
  }

  // 3. At a Glance
  html += `
    <div class="sec-label">At a Glance</div>
    <div class="stats-row">
      <div class="stat-card" style="cursor:pointer" onclick="window.location='/team'">
        <div class="stat-num" id="home-stat-tasks-num">${openTasks.length}</div>
        <div class="stat-lbl">Tasks</div>
      </div>
      <div class="stat-card" style="cursor:pointer" onclick="switchTab('reminders')">
        <div class="stat-num">${Array.isArray(reminders) ? reminders.length : 0}</div>
        <div class="stat-lbl">Reminders</div>
      </div>
      <div class="stat-card" style="cursor:pointer" onclick="switchTab('briefing')">
        <div class="stat-num">${Array.isArray(briefing) ? briefing.length : 0}</div>
        <div class="stat-lbl">Briefing</div>
      </div>
    </div>`;

  // 4. Tasks (tabbed: Catalyst / FMS / Personal)
  html += `<div class="sec-label">Tasks</div>
<div style="display:flex;border:1px solid var(--border);border-radius:var(--r-btn);overflow:hidden;margin-bottom:12px">
  <button id="htab-catalyst" onclick="switchHomeTaskTab('catalyst')"
    style="flex:1;padding:7px 0;border:none;cursor:pointer;background:var(--gold);color:#0f0f0f;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Catalyst</button>
  <button id="htab-fms" onclick="switchHomeTaskTab('fms')"
    style="flex:1;padding:7px 0;border:none;cursor:pointer;background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">FMS</button>
  <button id="htab-personal" onclick="switchHomeTaskTab('personal')"
    style="flex:1;padding:7px 0;border:none;cursor:pointer;background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Personal</button>
</div>
<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:8px">
  <input id="home-task-inp" type="text" placeholder="Add a Catalyst task…"
    style="width:100%;padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:14px;outline:none;box-sizing:border-box"
    onfocus="this.style.borderColor='var(--gold)'"
    onblur="this.style.borderColor='var(--border)'"
    onkeydown="if(event.key==='Enter')addHomeTask()">
  <div style="display:flex;gap:8px">
    <input id="home-task-date" type="date"
      style="flex:1;padding:9px 8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:14px;outline:none;box-sizing:border-box;color-scheme:dark"
      onfocus="this.style.borderColor='var(--gold)'"
      onblur="this.style.borderColor='var(--border)'">
    <button onclick="addHomeTask()" style="flex:1;padding:9px 16px;background:var(--gold);color:#0f0f0f;border:none;border-radius:var(--r-btn);font-weight:600;font-family:inherit;font-size:14px;cursor:pointer">Add</button>
  </div>
</div>
<div id="home-tasks-list">${_homeTasksHtml(displayTasks)}</div>`;

  setContent(html);
}

async function switchHomeTaskTab(tab) {
  _homeTaskTab = tab;
  const labels = { catalyst: 'Catalyst', fms: 'FMS', personal: 'Personal' };
  ['catalyst', 'fms', 'personal'].forEach(t => {
    const btn = document.getElementById(`htab-${t}`);
    if (!btn) return;
    btn.style.background = t === tab ? 'var(--gold)' : 'transparent';
    btn.style.color      = t === tab ? '#0f0f0f'    : 'var(--muted)';
  });
  const inp = document.getElementById('home-task-inp');
  if (inp) {
    inp.placeholder = `Add a ${labels[tab] || tab} task…`;
    inp.value = '';
  }
  try {
    await _fetchAndRenderTasks(tab);
  } catch { /* silent */ }
}

async function _fetchAndRenderTasks(tab) {
  const [openR, compR] = await Promise.allSettled([
    api(`/api/team/members/12/tasks?status=open&category=${tab}`),
    api(`/api/team/members/12/tasks?status=completed&category=${tab}`),
  ]);
  const open = openR.status === 'fulfilled' && Array.isArray(openR.value) ? openR.value : [];
  const comp = compR.status === 'fulfilled' && Array.isArray(compR.value) ? compR.value : [];
  const listEl = document.getElementById('home-tasks-list');
  if (listEl) listEl.innerHTML = _homeTasksHtml([...open, ...comp]);
  if (tab === 'catalyst') {
    const numEl = document.getElementById('home-stat-tasks-num');
    if (numEl) numEl.textContent = open.length;
  }
  return open;
}

function _homeTasksHtml(tasks) {
  if (!tasks.length) return '<div class="empty">No open tasks.</div>';
  const sorted = [...tasks].sort((a, b) => {
    // completed tasks sort after open
    const ac = a.status === 'completed' ? 1 : 0;
    const bc = b.status === 'completed' ? 1 : 0;
    if (ac !== bc) return ac - bc;
    if (ac) return (b.completed_at || '').localeCompare(a.completed_at || '');
    // open: priority asc (null last), then due_date asc (null last)
    const pa = a.priority ? parseInt(a.priority, 10) : 99;
    const pb = b.priority ? parseInt(b.priority, 10) : 99;
    if (pa !== pb) return pa - pb;
    const da = a.due_date || null;
    const db = b.due_date || null;
    if (da && db) return da.localeCompare(db);
    if (da) return -1;
    if (db) return 1;
    return 0;
  });
  return sorted.map(t => {
    const done = t.status === 'completed';
    const p = t.priority || '3';
    const dueStr = fmtTaskDue(t.due_date);
    return `
    <div class="task-card" id="home-task-${t.id}" style="align-items:center${done ? ';opacity:0.5' : ''}">
      <div class="home-chk-wrap" onclick="${done ? '' : `checkOffTask(${t.id}, this)`}">
        <div class="home-chk${done ? ' is-done' : ''}" id="home-chk-${t.id}"></div>
      </div>
      <div style="flex:1;min-width:0">
        <div class="home-task-title${done ? ' struck' : ''}" id="home-task-title-${t.id}">${esc(t.title)}</div>
        <div class="home-task-meta">
          <span class="pri ${priClass(p)}" style="margin-top:0">${esc(p)}</span>
          ${dueStr ? `<span style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted)">${esc(dueStr)}</span>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
}

async function checkOffTask(taskId, wrapEl) {
  const chkEl  = document.getElementById(`home-chk-${taskId}`);
  const titleEl = document.getElementById(`home-task-title-${taskId}`);
  const card   = document.getElementById(`home-task-${taskId}`);
  if (chkEl) chkEl.classList.add('is-done');
  if (titleEl) titleEl.classList.add('struck');
  if (card) card.style.opacity = '0.5';
  if (wrapEl) wrapEl.onclick = null;
  try {
    await api(`/api/team/tasks/${taskId}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ completed_at: new Date().toISOString() }),
    });
    if (_homeTaskTab === 'catalyst') {
      const numEl = document.getElementById('home-stat-tasks-num');
      if (numEl) numEl.textContent = Math.max(0, (parseInt(numEl.textContent, 10) || 0) - 1);
    }
  } catch {
    if (chkEl) chkEl.classList.remove('is-done');
    if (titleEl) titleEl.classList.remove('struck');
    if (card) card.style.opacity = '';
    if (wrapEl) wrapEl.onclick = () => checkOffTask(taskId, wrapEl);
    alert('Failed to complete task.');
  }
}

function toggleCatDrop(taskId, event) {
  event.stopPropagation();
  const drop = document.getElementById(`cat-drop-${taskId}`);
  if (!drop) return;
  if (_openCatDrop !== null && _openCatDrop !== taskId) {
    const prev = document.getElementById(`cat-drop-${_openCatDrop}`);
    if (prev) prev.style.display = 'none';
  }
  const isOpen = drop.style.display === 'block';
  drop.style.display = isOpen ? 'none' : 'block';
  _openCatDrop = isOpen ? null : taskId;
}

function _closeCatDrop() {
  if (_openCatDrop !== null) {
    const drop = document.getElementById(`cat-drop-${_openCatDrop}`);
    if (drop) drop.style.display = 'none';
    _openCatDrop = null;
  }
}

async function reassignCat(taskId, newCat, event) {
  event.stopPropagation();
  _closeCatDrop();
  if (newCat === _homeTaskTab) return;
  try {
    await api(`/api/team/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: newCat }),
    });
    const card = document.getElementById(`home-task-${taskId}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
    const numEl = document.getElementById('home-stat-tasks-num');
    if (numEl) {
      const cur = parseInt(numEl.textContent, 10) || 0;
      if (_homeTaskTab === 'catalyst') numEl.textContent = Math.max(0, cur - 1);
      else if (newCat === 'catalyst') numEl.textContent = cur + 1;
    }
  } catch { alert('Failed to reassign task.'); }
}

document.addEventListener('click', _closeCatDrop);

function openTaskDatePicker(taskId) {
  const el = document.getElementById(`task-due-${taskId}`);
  if (!el) return;
  const inp = document.createElement('input');
  inp.type = 'date';
  inp.style.cssText = 'font-size:11px;font-family:\'DM Mono\',monospace;color:var(--gold-dim);background:transparent;border:none;border-bottom:1px solid var(--border);outline:none;padding:0;width:120px;color-scheme:dark';
  el.replaceWith(inp);
  inp.focus();
  inp.addEventListener('change', async () => {
    const val = inp.value;
    if (!val) return;
    try {
      await api(`/api/team/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ due_date: val }),
      });
      const div = document.createElement('div');
      div.className = 'task-due';
      div.id = `task-due-${taskId}`;
      div.textContent = new Date(val).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      inp.replaceWith(div);
    } catch { alert('Failed to set due date.'); }
  });
  inp.addEventListener('blur', () => {
    if (!inp.value) {
      const div = document.createElement('div');
      div.className = 'task-due';
      div.id = `task-due-${taskId}`;
      div.style.color = 'var(--muted)';
      div.style.cursor = 'pointer';
      div.textContent = 'n/a';
      div.onclick = () => openTaskDatePicker(taskId);
      inp.replaceWith(div);
    }
  });
}

function togglePendingExp(idx) {
  const exp = document.getElementById(`pending-exp-${idx}`);
  const tog = document.getElementById(`pending-tog-${idx}`);
  if (!exp) return;
  const isOpen = exp.style.display !== 'none';
  if (_pendingOpenIdx !== null && _pendingOpenIdx !== idx) {
    const prevExp = document.getElementById(`pending-exp-${_pendingOpenIdx}`);
    const prevTog = document.getElementById(`pending-tog-${_pendingOpenIdx}`);
    if (prevExp) prevExp.style.display = 'none';
    if (prevTog) prevTog.textContent = '+ ADD NOTE';
  }
  if (isOpen) {
    exp.style.display = 'none';
    if (tog) tog.textContent = '+ ADD NOTE';
    _pendingOpenIdx = null;
  } else {
    exp.style.display = 'block';
    if (tog) tog.textContent = '− CANCEL';
    _pendingOpenIdx = idx;
    _pendingNoteTypes[idx] = 'pastoral';
    setPendingNoteType(idx, 'pastoral');
    const ta = document.getElementById(`pending-ta-${idx}`);
    if (ta) { ta.focus(); ta.value = ''; }
  }
}

function setPendingNoteType(idx, type) {
  _pendingNoteTypes[idx] = type;
  const pastoralBtn    = document.getElementById(`pending-type-pastoral-${idx}`);
  const leadershipBtn  = document.getElementById(`pending-type-leadership-${idx}`);
  if (pastoralBtn) {
    pastoralBtn.style.background = type === 'pastoral' ? 'var(--gold)' : 'transparent';
    pastoralBtn.style.color      = type === 'pastoral' ? '#0f0f0f' : 'var(--muted)';
  }
  if (leadershipBtn) {
    leadershipBtn.style.background = type === 'leadership' ? 'var(--gold)' : 'transparent';
    leadershipBtn.style.color      = type === 'leadership' ? '#0f0f0f' : 'var(--muted)';
  }
}

async function saveInlineNote(pendingId, idx) {
  const ta = document.getElementById(`pending-ta-${idx}`);
  const content = (ta?.value || '').trim();
  if (!content) { if (ta) ta.focus(); return; }
  const note_type = _pendingNoteTypes[idx] || 'pastoral';
  try {
    await api('/api/pastoral_notes/inline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pending_id: pendingId, content, note_type }),
    });
    const card = document.getElementById(`pending-card-${idx}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
    _pendingOpenIdx = null;
  } catch { alert('Failed to save note.'); }
}

async function skipInlineNote(pendingId, idx) {
  try {
    await api('/api/pastoral_notes/skip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pending_id: pendingId }),
    });
    const card = document.getElementById(`pending-card-${idx}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
    _pendingOpenIdx = null;
  } catch { alert('Failed to skip.'); }
}

async function deleteInlineNote(pendingId, idx) {
  try {
    await api('/api/pastoral_notes/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pending_id: pendingId }),
    });
    const card = document.getElementById(`pending-card-${idx}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
    _pendingOpenIdx = null;
  } catch { alert('Failed to delete.'); }
}

async function addHomeTask() {
  const inp = document.getElementById('home-task-inp');
  const title = (inp?.value || '').trim();
  if (!title) { if (inp) inp.focus(); return; }
  if (inp) inp.value = '';
  const dateInp = document.getElementById('home-task-date');
  const due_date = dateInp?.value || null;
  try {
    const body = { member_id: 12, title, priority: '3', category: _homeTaskTab, assigned_by: 'bill' };
    if (due_date) body.due_date = due_date;
    await api('/api/team/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (dateInp) dateInp.value = '';
    await _fetchAndRenderTasks(_homeTaskTab);
  } catch { alert('Failed to add task.'); }
}

async function _pollHomeData() {
  if (activePage !== 'home') return;
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.tagName === 'SELECT')) return;
  try {
    await fetch('/api/team/tasks/archive-completed');
    await _fetchAndRenderTasks(_homeTaskTab);
  } catch(e) { /* silent — stale data is acceptable */ }
}

setInterval(_pollHomeData, 15000);

// ─── Notes page ───────────────────────────────────────────────────────────────

function _noteCardHtml(n) {
  const isLeadership = !!n.is_leadership;
  const noteKey  = isLeadership ? `l${n.id}` : `p${n.id}`;
  const noteText = n.note || n.content || '';
  const actionBtn = isLeadership
    ? `<button onclick="deleteLeaderNote(${n.id})"
         style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid rgba(201,80,76,.4);background:none;color:var(--red);font-family:inherit;cursor:pointer;-webkit-tap-highlight-color:transparent">Delete</button>`
    : `<button onclick="archiveNote(${n.id})"
         style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--border);background:none;color:var(--muted);font-family:inherit;cursor:pointer;-webkit-tap-highlight-color:transparent">Archive</button>`;
  return `
    <div class="card" id="note-card-${noteKey}">
      ${!isLeadership && (n.person_name || n.name) ? `<div style="font-size:14px;font-weight:600;margin-bottom:6px">${esc(n.person_name || n.name)}</div>` : ''}
      <div class="note-text note-text-trunc" id="note-text-${noteKey}">${esc(noteText)}</div>
      <a id="note-toggle-${noteKey}" onclick="toggleNoteExpand('${noteKey}')"
         style="display:inline-block;margin-top:4px;font-size:11px;font-family:'DM Mono',monospace;color:var(--gold);cursor:pointer;-webkit-tap-highlight-color:transparent">Read more</a>
      ${isLeadership && n.member_name ? `<div style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);margin-top:4px">re: ${esc(n.member_name)}</div>` : ''}
      <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px">
        <div style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted)">${esc(n.created_at || '')}</div>
        ${actionBtn}
      </div>
    </div>`;
}

async function renderNotes() {
  _notesType = 'pastoral';
  setContent('<div class="loading">Loading&hellip;</div>');
  try {
    const [pastoralRes, sharedRes, membersRes] = await Promise.allSettled([
      api('/api/pastoral-notes?status=active'),
      api('/api/team/shared_notes?author=bill'),
      api('/api/team/members'),
    ]);

    const pastoral = pastoralRes.status === 'fulfilled' && Array.isArray(pastoralRes.value) ? pastoralRes.value : [];
    const shared   = sharedRes.status   === 'fulfilled' && Array.isArray(sharedRes.value)   ? sharedRes.value   : [];
    const members  = membersRes.status  === 'fulfilled' && Array.isArray(membersRes.value)  ? membersRes.value  : [];

    const leaderNotes = shared.map(n => ({ ...n, note: n.content, is_leadership: true }));
    const allNotes    = [...pastoral, ...leaderNotes].sort(
      (a, b) => (b.created_at || '').localeCompare(a.created_at || '')
    );

    const leaderOpts = members
      .filter(m => m.id !== 12)
      .map(m => `<option value="${m.id}">${esc(m.name)}</option>`)
      .join('');

    const listHtml = allNotes.length
      ? allNotes.map(_noteCardHtml).join('')
      : '<div class="empty">No notes yet.</div>';

    setContent(`
      <input id="notes-inp-name" type="text" placeholder="Person's name…"
        style="display:block;width:100%;margin-bottom:8px;padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:14px;outline:none;box-sizing:border-box"
        onfocus="this.style.borderColor='var(--gold)'" onblur="this.style.borderColor='var(--border)'">
      <select id="notes-leader-sel"
        style="display:none;width:100%;margin-bottom:8px;padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:14px;outline:none;box-sizing:border-box;cursor:pointer"
        onfocus="this.style.borderColor='var(--gold)'" onblur="this.style.borderColor='var(--border)'">
        <option value="">Select a leader…</option>
        ${leaderOpts}
      </select>
      <textarea id="notes-inp-text" rows="3" placeholder="Add a note…"
        style="display:block;width:100%;margin-bottom:8px;padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:14px;outline:none;resize:none;box-sizing:border-box"
        onfocus="this.style.borderColor='var(--gold)'" onblur="this.style.borderColor='var(--border)'"></textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div style="display:flex;border:1px solid var(--border);border-radius:var(--r-btn);overflow:hidden;flex:1">
          <button id="notes-type-pastoral" onclick="setNotesType('pastoral')"
            style="flex:1;padding:6px 0;border:none;cursor:pointer;background:var(--gold);color:#0f0f0f;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em;-webkit-tap-highlight-color:transparent">Pastoral</button>
          <button id="notes-type-leadership" onclick="setNotesType('leadership')"
            style="flex:1;padding:6px 0;border:none;cursor:pointer;background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em;-webkit-tap-highlight-color:transparent">Leadership</button>
        </div>
        <button id="notes-add-btn" onclick="addNote()"
          style="padding:7px 16px;background:var(--gold);color:#0f0f0f;border:none;border-radius:var(--r-btn);font-weight:600;font-family:inherit;font-size:13px;cursor:pointer;flex-shrink:0;-webkit-tap-highlight-color:transparent">Add note</button>
      </div>
      <div id="notes-warning" style="display:none;font-size:11px;font-family:'DM Mono',monospace;color:var(--red);margin-bottom:8px"></div>
      <div id="notes-list" style="margin-top:16px">${listHtml}</div>`);
  } catch {
    setContent('<div class="empty">Could not load notes.</div>');
  }
}

function setNotesType(type) {
  _notesType = type;
  const pastoralBtn   = document.getElementById('notes-type-pastoral');
  const leadershipBtn = document.getElementById('notes-type-leadership');
  const nameInp       = document.getElementById('notes-inp-name');
  const leaderSel     = document.getElementById('notes-leader-sel');
  if (pastoralBtn) {
    pastoralBtn.style.background = type === 'pastoral' ? 'var(--gold)' : 'transparent';
    pastoralBtn.style.color      = type === 'pastoral' ? '#0f0f0f'    : 'var(--muted)';
  }
  if (leadershipBtn) {
    leadershipBtn.style.background = type === 'leadership' ? 'var(--gold)' : 'transparent';
    leadershipBtn.style.color      = type === 'leadership' ? '#0f0f0f'    : 'var(--muted)';
  }
  if (nameInp)   nameInp.style.display   = type === 'pastoral'   ? 'block' : 'none';
  if (leaderSel) leaderSel.style.display = type === 'leadership' ? 'block' : 'none';
}

async function addNote() {
  const nameInp   = document.getElementById('notes-inp-name');
  const leaderSel = document.getElementById('notes-leader-sel');
  const textInp   = document.getElementById('notes-inp-text');
  const addBtn    = document.getElementById('notes-add-btn');
  const note      = (textInp?.value || '').trim();
  if (!note) { textInp?.focus(); return; }

  if (_notesType === 'leadership') {
    const member_id = parseInt(leaderSel?.value || '0', 10);
    if (!member_id) {
      const warnEl = document.getElementById('notes-warning');
      if (warnEl) {
        warnEl.textContent = 'Select a leader.';
        warnEl.style.display = 'block';
        setTimeout(() => { warnEl.style.display = 'none'; warnEl.textContent = ''; }, 3000);
      }
      return;
    }
    if (addBtn) { addBtn.disabled = true; addBtn.textContent = '…'; }
    try {
      const member_name = leaderSel.options[leaderSel.selectedIndex]?.text || '';
      const res = await api('/api/team/shared_notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ member_id, content: note, author: 'bill' }),
      });
      if (textInp) textInp.value = '';
      if (leaderSel) leaderSel.value = '';
      setNotesType('pastoral');
      if (res.note?.id) {
        const savedNote = { id: res.note.id, content: note, note, member_name, is_leadership: true, created_at: res.note.created_at || new Date().toLocaleString() };
        const list = document.getElementById('notes-list');
        if (list) {
          const emptyEl = list.querySelector('.empty');
          if (emptyEl) emptyEl.remove();
          const card = document.createElement('div');
          card.innerHTML = _noteCardHtml(savedNote);
          const el = card.firstElementChild;
          if (el) {
            el.style.opacity = '0';
            el.style.transition = 'opacity .3s';
            list.prepend(el);
            requestAnimationFrame(() => { el.style.opacity = '1'; });
          }
        }
      }
    } catch {
      alert('Failed to save note.');
    } finally {
      if (addBtn) { addBtn.disabled = false; addBtn.textContent = 'Add note'; }
    }
    return;
  }

  // Pastoral note
  const person_name = (nameInp?.value || '').trim();
  if (addBtn) { addBtn.disabled = true; addBtn.textContent = '…'; }
  try {
    const res = await api('/api/pastoral-notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ person_name, note }),
    });
    const savedNote = { id: res.id, person_name, note, created_at: new Date().toLocaleString() };
    if (nameInp) nameInp.value = '';
    if (textInp) textInp.value = '';
    if (savedNote.id) {
      const list = document.getElementById('notes-list');
      if (list) {
        const emptyEl = list.querySelector('.empty');
        if (emptyEl) emptyEl.remove();
        const card = document.createElement('div');
        card.innerHTML = _noteCardHtml(savedNote);
        const el = card.firstElementChild;
        if (el) {
          el.style.opacity = '0';
          el.style.transition = 'opacity .3s';
          list.prepend(el);
          requestAnimationFrame(() => { el.style.opacity = '1'; });
        }
      }
    }
  } catch {
    alert('Failed to save note.');
  } finally {
    if (addBtn) { addBtn.disabled = false; addBtn.textContent = 'Add note'; }
  }
}

function toggleNoteExpand(key) {
  const textEl   = document.getElementById(`note-text-${key}`);
  const toggleEl = document.getElementById(`note-toggle-${key}`);
  if (!textEl || !toggleEl) return;
  const isExpanded = !textEl.classList.contains('note-text-trunc');
  if (isExpanded) {
    textEl.classList.add('note-text-trunc');
    toggleEl.textContent = 'Read more';
  } else {
    textEl.classList.remove('note-text-trunc');
    toggleEl.textContent = 'Show less';
  }
}

async function archiveNote(id) {
  try {
    await api(`/api/pastoral-notes/${id}/archive`, { method: 'POST' });
    const card = document.getElementById(`note-card-p${id}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
  } catch { alert('Failed to archive note.'); }
}

async function deleteLeaderNote(id) {
  try {
    await api(`/api/team/shared_notes/${id}`, { method: 'DELETE' });
    const card = document.getElementById(`note-card-l${id}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
  } catch { alert('Failed to delete note.'); }
}

// ─── Briefing page ────────────────────────────────────────────────────────────

async function renderBriefing() {
  setContent('<div class="loading">Loading&hellip;</div>');

  const [itemsRes, metaRes] = await Promise.allSettled([
    api('/api/briefing'),
    api('/api/briefing/meta'),
  ]);

  const items = itemsRes.status === 'fulfilled' ? itemsRes.value : [];
  const meta  = metaRes.status  === 'fulfilled' ? metaRes.value : {};
  const genAt = meta.generated_at ? `Generated ${fmtGenerated(meta.generated_at)}` : '';

  let html = `
    <div class="b-hdr">
      <div class="b-hdr-title">WATSON</div>
      <div class="b-hdr-date">${esc(todayLabel())}</div>
      ${genAt ? `<div class="b-hdr-gen">${esc(genAt)}</div>` : ''}
    </div>`;

  if (!Array.isArray(items) || !items.length) {
    html += `<div class="empty">No briefing items today.</div>`;
  } else {
    items.forEach(item => {
      html += `
        <div class="b-card" id="b-card-${item.id}">
          <div class="b-source">${esc(item.source_name || '')}</div>
          <div class="b-title">${esc(item.title)}</div>
          <div class="b-actions">
            <button class="b-btn b-btn-read" onclick="window.open('${(item.url || '#').replace(/'/g, '%27')}','_blank')">Read</button>
            <button class="b-btn" id="b-approve-${item.id}" onclick="briefingAction(${item.id},'approve',this)">Email</button>
            <button class="b-btn" id="b-fb-${item.id}"     onclick="briefingAction(${item.id},'facebook',this)">Facebook</button>
            <button class="b-btn" id="b-list-${item.id}"   onclick="briefingAction(${item.id},'tolist',this)">To List</button>
            <button class="b-btn b-btn-reject" id="b-rej-${item.id}" onclick="briefingAction(${item.id},'reject',this)">Reject</button>
          </div>
        </div>`;
    });
  }

  setContent(html);
}

async function briefingAction(id, action, btnEl) {
  if (!btnEl || btnEl.classList.contains('sent')) return;
  const orig = btnEl.textContent;
  btnEl.classList.add('sent');
  btnEl.textContent = '…';
  try {
    await api(`/api/briefing/${id}/${action}`, { method: 'POST' });
    btnEl.textContent = '✓';
    if (action === 'reject') {
      const card = document.getElementById(`b-card-${id}`);
      if (card) { card.style.opacity = '.35'; card.style.pointerEvents = 'none'; }
    }
  } catch {
    btnEl.classList.remove('sent');
    btnEl.textContent = orig;
  }
}

// ─── Reminders page ───────────────────────────────────────────────────────────

async function renderReminders() {
  setContent('<div class="loading">Loading&hellip;</div>');
  try {
    const reminders = await api('/api/reminders');
    if (!Array.isArray(reminders) || !reminders.length) {
      setContent('<div class="empty">No active reminders.</div>');
      return;
    }
    let html = '';
    reminders.forEach(r => {
      const sub = r.reminder_time || r.due_datetime || '';
      html += `
        <div class="task-card" id="reminder-row-${r.id}">
          <div class="task-check" onclick="completeReminder(${r.id},this)"></div>
          <div class="task-body">
            <div class="task-title">${esc(r.title)}</div>
            ${sub ? `<div class="card-sub" style="margin-top:4px;font-size:12px;color:var(--muted)">${esc(sub)}</div>` : ''}
          </div>
        </div>`;
    });
    setContent(html);
  } catch {
    setContent('<div class="empty">Could not load reminders.</div>');
  }
}

async function completeReminder(id, checkEl) {
  if (!checkEl || checkEl.classList.contains('is-done')) return;
  checkEl.classList.add('is-done');
  const titleEl = checkEl.closest('.task-card')?.querySelector('.task-title');
  if (titleEl) titleEl.classList.add('struck');
  try {
    await api(`/api/reminders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'done' }),
    });
    await renderReminders();
  } catch {
    checkEl.classList.remove('is-done');
    if (titleEl) titleEl.classList.remove('struck');
  }
}

// ─── Reading page ─────────────────────────────────────────────────────────────

async function renderReading() {
  setContent('<div class="loading">Loading&hellip;</div>');
  try {
    const items = await api('/api/reading');
    if (!Array.isArray(items) || !items.length) {
      setContent('<div class="empty">Reading list is empty.</div>');
      return;
    }
    const statusLabel = { unread: 'Unread', reading: 'Reading', finished: 'Finished' };
    let html = '';
    items.forEach(item => {
      const sl  = statusLabel[item.status] || 'Unread';
      const sc  = `rl-${item.status || 'unread'}`;
      const ttl = item.url
        ? `<a href="${esc(item.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(item.title)}</a>`
        : esc(item.title);
      html += `
        <div class="rl-card">
          ${item.source_name ? `<div class="rl-source">${esc(item.source_name)}</div>` : ''}
          <div class="rl-title">${ttl}</div>
          <div class="rl-status ${sc}">${sl}</div>
        </div>`;
    });
    setContent(html);
  } catch {
    setContent('<div class="empty">Could not load reading list.</div>');
  }
}

// ── Reading List (More section) ──────────────────────────────────────────────

async function moreLoadReading() {
  const el = document.getElementById('msec-inner-reading');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading&hellip;</div>';
  try {
    const items = await api('/api/reading');
    if (!Array.isArray(items) || !items.length) {
      el.innerHTML = '<div class="empty">Reading list is empty.</div>';
      return;
    }
    const statusLabel = { unread: 'Unread', reading: 'Reading', finished: 'Finished' };
    el.innerHTML = items.map(item => {
      const sl  = statusLabel[item.status] || 'Unread';
      const sc  = `rl-${item.status || 'unread'}`;
      const ttl = item.url
        ? `<a href="${esc(item.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(item.title)}</a>`
        : esc(item.title);
      return `
        <div class="rl-card">
          ${item.source_name ? `<div class="rl-source">${esc(item.source_name)}</div>` : ''}
          <div class="rl-title">${ttl}</div>
          <div class="rl-status ${sc}">${sl}</div>
        </div>`;
    }).join('');
  } catch {
    el.innerHTML = '<div class="empty">Could not load reading list.</div>';
  }
}

// ─── More page ────────────────────────────────────────────────────────────────

let _moreSecLoaded    = {};
let _moreShepData     = null;
let _moreAuditData    = null;
let _morePNTab        = 'active';
let _moreAllSkills    = [];
let _moreSkillCat     = 'All';
let _moreSkillQuery   = '';
let _moreAllMembers    = [];
let _memberPage        = 0;
let _memberCurrentList = null;
let _memberSearchTimer = null;
let _expandedMemberId  = null;

function renderMore() {
  _moreSecLoaded = {};
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  setContent(`
    <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-card);margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px">
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--gold)"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>
        <span style="font-size:13px;font-weight:500">Appearance</span>
      </div>
      <label class="mswitch">
        <input type="checkbox" id="more-theme-chk" onchange="moreToggleTheme(this.checked)" ${isLight ? 'checked' : ''}>
        <span class="mswitch-track"></span>
        <span class="mswitch-thumb"></span>
      </label>
    </div>
    <div class="mrrow" onclick="switchTab('briefing')" style="cursor:pointer">
      <span style="font-size:13px;font-weight:500">Briefing</span>
      <span style="color:var(--gold);font-size:15px">›</span>
    </div>
    <div class="msec" id="msec-skills">
      <div class="msec-hdr" onclick="moreToggle('skills')">
        <span class="msec-title">Skills</span>
        <span class="msec-chev" id="msec-chev-skills">›</span>
      </div>
      <div class="msec-body" id="msec-body-skills">
        <div class="msec-inner" id="msec-inner-skills"></div>
      </div>
    </div>
    <div class="msec" id="msec-reading">
      <div class="msec-hdr" onclick="moreToggle('reading')">
        <span class="msec-title">Reading List</span>
        <span class="msec-chev" id="msec-chev-reading">›</span>
      </div>
      <div class="msec-body" id="msec-body-reading">
        <div class="msec-inner" id="msec-inner-reading"><div class="loading">Loading&hellip;</div></div>
      </div>
    </div>
    <div class="msec" id="msec-ministry">
      <div class="msec-hdr" onclick="moreToggle('ministry')">
        <span class="msec-title">Ministry</span>
        <span class="msec-chev" id="msec-chev-ministry">›</span>
      </div>
      <div class="msec-body" id="msec-body-ministry">
        <div class="msec-inner" id="msec-inner-ministry"><div class="loading">Loading&hellip;</div></div>
      </div>
    </div>
    <div class="msec" id="msec-events">
      <div class="msec-hdr" onclick="moreToggle('events')">
        <span class="msec-title">Events</span>
        <span class="msec-chev" id="msec-chev-events">›</span>
      </div>
      <div class="msec-body" id="msec-body-events">
        <div class="msec-inner" id="msec-inner-events"><div class="loading">Loading&hellip;</div></div>
      </div>
    </div>
    <div class="msec" id="msec-members">
      <div class="msec-hdr" onclick="moreToggle('members')">
        <span class="msec-title">Members</span>
        <span class="msec-chev" id="msec-chev-members">›</span>
      </div>
      <div class="msec-body" id="msec-body-members">
        <div class="msec-inner" id="msec-inner-members"></div>
      </div>
    </div>
    <div class="msec" id="msec-dev">
      <div class="msec-hdr" onclick="moreToggle('dev')">
        <span class="msec-title">Dev Loop</span>
        <span class="msec-chev" id="msec-chev-dev">›</span>
      </div>
      <div class="msec-body" id="msec-body-dev">
        <div class="msec-inner" id="msec-inner-dev"><div class="loading">Loading&hellip;</div></div>
      </div>
    </div>
    <div class="mrrow" onclick="openLogins()" style="cursor:pointer">
      <span style="font-size:13px;font-weight:500">Logins</span>
      <span style="color:var(--gold);font-size:15px">›</span>
    </div>
    <a href="/admin" target="_blank" rel="noopener" class="mrrow" style="text-decoration:none;cursor:pointer">
      <span style="font-size:13px;font-weight:500;color:var(--text)">Team Admin</span>
      <span style="color:var(--gold);font-size:15px">›</span>
    </a>`);
}

function moreToggle(sec) {
  const body = document.getElementById(`msec-body-${sec}`);
  const chev = document.getElementById(`msec-chev-${sec}`);
  if (!body) return;
  const isOpen = body.classList.toggle('open');
  if (chev) chev.textContent = isOpen ? '⌄' : '›';
  if (isOpen && !_moreSecLoaded[sec]) {
    _moreSecLoaded[sec] = true;
    if (sec === 'skills')   moreLoadSkills();
    if (sec === 'ministry') moreLoadMinistry();
    if (sec === 'reading')  moreLoadReading();
    if (sec === 'events')   moreLoadEvents();
    if (sec === 'members')  moreLoadMembers();
    if (sec === 'dev')      devLoopLoad();
  }
}

// ── Ministry ─────────────────────────────────────────────────────────────────

function moreLoadMinistry() {
  const el = document.getElementById('msec-inner-ministry');
  if (!el) return;
  el.innerHTML = `
    <div class="mtabs">
      <button class="mtab active" id="mmin-tab-pn"    onclick="moreMinTab('pn')">Pastoral Notes</button>
      <button class="mtab"        id="mmin-tab-shep"  onclick="moreMinTab('shep')">Shepherding</button>
      <button class="mtab"        id="mmin-tab-audit" onclick="moreMinTab('audit')">Audit</button>
    </div>
    <div id="mmin-body-pn">
      <button class="mbtn mbtn-sm" onclick="moreTogglePNForm()" style="margin-bottom:8px">+ New Note</button>
      <div id="more-pn-form" style="display:none">
        <div class="mform">
          <input id="mpn-name" placeholder="Person's name" type="text">
          <textarea id="mpn-note" rows="3" placeholder="Note&hellip;"></textarea>
          <div class="mfrow">
            <button class="mbtn mbtn-p" onclick="moreSavePN()">Save</button>
            <button class="mbtn"        onclick="moreTogglePNForm()">Cancel</button>
          </div>
        </div>
      </div>
      <div class="mtabs" style="margin-top:4px">
        <button class="mtab active" id="mpn-tab-active"   onclick="moreSetPNTab('active')">Active</button>
        <button class="mtab"        id="mpn-tab-archived" onclick="moreSetPNTab('archived')">Archived</button>
      </div>
      <div id="mpn-list"><div class="loading">Loading&hellip;</div></div>
    </div>
    <div id="mmin-body-shep" style="display:none">
      <button class="mbtn" onclick="moreRunShepReport()">Run Shepherding Report</button>
      <div id="mshep-result"></div>
    </div>
    <div id="mmin-body-audit" style="display:none">
      <button class="mbtn" onclick="moreRunAudit()">Run Congregation Audit</button>
      <div id="maudit-result"></div>
    </div>`;
  moreLoadPN('active');
}

function moreMinTab(tab) {
  ['pn', 'shep', 'audit'].forEach(t => {
    const body = document.getElementById(`mmin-body-${t}`);
    const btn  = document.getElementById(`mmin-tab-${t}`);
    if (body) body.style.display = t === tab ? '' : 'none';
    if (btn)  btn.classList.toggle('active', t === tab);
  });
}

function moreTogglePNForm() {
  const f = document.getElementById('more-pn-form');
  if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
}

function moreSetPNTab(tab) {
  _morePNTab = tab;
  ['active', 'archived'].forEach(t => {
    const btn = document.getElementById(`mpn-tab-${t}`);
    if (btn) btn.classList.toggle('active', t === tab);
  });
  moreLoadPN(tab);
}

async function moreLoadPN(tab) {
  const el = document.getElementById('mpn-list');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading&hellip;</div>';
  try {
    const notes = await api(`/api/pastoral-notes?status=${tab}`);
    if (!Array.isArray(notes) || !notes.length) {
      el.innerHTML = `<div class="empty">No ${tab} notes.</div>`;
      return;
    }
    el.innerHTML = notes.map(n => `
      <div class="mpn-card">
        <div style="font-size:13px;font-weight:500">${esc(n.person_name || n.name || '')}</div>
        <div style="font-size:12px;color:var(--muted);margin:4px 0">${esc(n.note || '')}</div>
        <div style="font-size:11px;color:var(--muted)">${esc(n.created_at || '')}</div>
        ${tab === 'active' ? `<button class="mbtn mbtn-sm" onclick="moreArchivePN(${n.id})" style="margin-top:6px">Archive</button>` : ''}
        ${tab === 'archived' ? `<button class="mbtn mbtn-sm mbtn-d" onclick="moreDeletePN(${n.id})" style="margin-top:6px">Delete</button>` : ''}
      </div>`).join('');
  } catch {
    el.innerHTML = '<div class="empty">Could not load notes.</div>';
  }
}

async function moreSavePN() {
  const name = (document.getElementById('mpn-name')?.value || '').trim();
  const note = (document.getElementById('mpn-note')?.value || '').trim();
  if (!name || !note) { alert('Name and note are required.'); return; }
  try {
    await api('/api/pastoral-notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ person_name: name, note }),
    });
    moreTogglePNForm();
    document.getElementById('mpn-name').value = '';
    document.getElementById('mpn-note').value = '';
    moreLoadPN(_morePNTab);
  } catch { alert('Failed to save note.'); }
}

async function moreArchivePN(id) {
  try {
    await api(`/api/pastoral-notes/${id}/archive`, { method: 'POST' });
    moreLoadPN(_morePNTab);
  } catch { alert('Failed to archive note.'); }
}

async function moreDeletePN(id) {
  if (!confirm('Permanently delete this note?')) return;
  try {
    await api(`/api/pastoral-notes/${id}`, { method: 'DELETE' });
    moreLoadPN(_morePNTab);
  } catch { alert('Failed to delete note.'); }
}

async function moreRunShepReport() {
  const el = document.getElementById('mshep-result');
  if (!el) return;
  el.innerHTML = '<div class="loading">Running&hellip;</div>';
  try {
    const data = await api('/api/shepherding/report');
    _moreShepData = data;
    let html = '';
    if (data.overdue?.length) {
      html += `<div class="mlabel">Overdue Contact (${data.overdue.length})</div>
        <div class="mshep-wrap"><table class="mshep-table">
          <tr><th>Name</th><th>Last Contact</th><th></th></tr>
          ${data.overdue.map(p => `
            <tr>
              <td>${esc(p.name)}</td>
              <td>${esc(p.last_contact || 'Never')}</td>
              <td style="white-space:nowrap">
                <button class="mbtn mbtn-sm" onclick="moreShepCheckin(${p.id})">Check in</button>
                <button class="mbtn mbtn-sm" onclick="moreShepExempt(${p.id})">Exempt</button>
              </td>
            </tr>`).join('')}
        </table></div>`;
    }
    if (!html) html = '<div class="empty">No overdue contacts.</div>';
    html += `
      <div class="mfrow" style="margin-top:12px">
        <button class="mbtn mbtn-sm" onclick="moreShepTelegram()">Send Telegram</button>
        <button class="mbtn mbtn-sm" onclick="moreShepEmail()">Send Email</button>
      </div>`;
    el.innerHTML = html;
  } catch {
    el.innerHTML = '<div class="empty">Could not run report.</div>';
  }
}

async function moreShepCheckin(personId) {
  try {
    await api(`/api/shepherding/checkin/${personId}`, { method: 'POST' });
    moreRunShepReport();
  } catch { alert('Failed to record check-in.'); }
}

async function moreShepExempt(personId) {
  try {
    await api(`/api/shepherding/exempt/${personId}`, { method: 'POST' });
    moreRunShepReport();
  } catch { alert('Failed to set exempt.'); }
}

async function moreShepTelegram() {
  try {
    await api('/api/shepherding/telegram', { method: 'POST' });
    alert('Sent to Telegram!');
  } catch { alert('Failed to send.'); }
}

async function moreShepEmail() {
  try {
    await api('/api/shepherding/email', { method: 'POST' });
    alert('Sent via email!');
  } catch { alert('Failed to send.'); }
}

async function moreRunAudit() {
  const el = document.getElementById('maudit-result');
  if (!el) return;
  el.innerHTML = '<div class="loading">Running audit&hellip;</div>';
  try {
    const data = await api('/api/congregation/audit');
    _moreAuditData = data;
    moreRenderAudit(data);
  } catch {
    el.innerHTML = '<div class="empty">Audit failed.</div>';
  }
}

function moreRenderAudit(data) {
  const el = document.getElementById('maudit-result');
  if (!el) return;
  let html = '';
  if (data.duplicates?.length) {
    html += `<div class="mlabel">Possible Duplicates (${data.duplicates.length})</div>`;
    html += moreRenderDupe(data.duplicates);
  }
  if (data.inconsistencies?.length) {
    html += `<div class="mlabel">Inconsistencies (${data.inconsistencies.length})</div>`;
    html += moreRenderIncon(data.inconsistencies);
    html += `<button class="mbtn mbtn-p" onclick="moreApplyCorrections()" style="margin-top:8px">Apply All Corrections</button>`;
  }
  if (!html) html = '<div class="empty">Audit complete — no issues found.</div>';
  el.innerHTML = html;
}

function moreRenderDupe(dupes) {
  return dupes.map(pair => `
    <div class="maudit-card">
      <div style="font-size:12px;margin-bottom:6px">
        <strong>${esc(pair[0].name)}</strong> vs <strong>${esc(pair[1].name)}</strong>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px">
        ${esc(pair[0].email || '')} / ${esc(pair[1].email || '')}
      </div>
      <div class="mfrow">
        <button class="mbtn mbtn-p mbtn-sm" onclick="moreMerge(${pair[0].id},${pair[1].id})">Merge</button>
        <button class="mbtn mbtn-sm"        onclick="moreKeepSep(${pair[0].id},${pair[1].id})">Keep Separate</button>
      </div>
    </div>`).join('');
}

function moreUpdateMergeBtn() {}

async function moreMerge(id1, id2) {
  try {
    await api('/api/congregation/merge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id1, id2 }),
    });
    moreRunAudit();
  } catch { alert('Merge failed.'); }
}

async function moreKeepSep(id1, id2) {
  try {
    await api('/api/congregation/keep-separate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id1, id2 }),
    });
    moreRunAudit();
  } catch { alert('Failed.'); }
}

function moreRenderIncon(items) {
  return items.map(item => `
    <div class="maudit-card">
      <div style="font-size:12px;font-weight:500">${esc(item.name || '')}</div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px">${esc(item.issue || '')}</div>
      ${item.fix ? `<div style="font-size:11px;margin-top:6px">Fix: ${esc(item.fix)}</div>` : ''}
    </div>`).join('');
}

async function moreApplyCorrections() {
  try {
    await api('/api/congregation/audit/apply', { method: 'POST' });
    moreRunAudit();
  } catch { alert('Failed to apply corrections.'); }
}

// ── Commands ──────────────────────────────────────────────────────────────────

async function moreLoadSkills() {
  const el = document.getElementById('msec-inner-skills');
  if (!el) return;
  _moreSkillCat   = 'All';
  _moreSkillQuery = '';
  el.innerHTML = `
    <input class="msrch" type="search" placeholder="Search commands&hellip;" oninput="moreSkillSearch(this.value)">
    <div style="font-size:11px;color:var(--muted);text-align:center;margin-bottom:8px">Tap a command to load it into the chat tab.</div>
    <div id="more-skill-pills" class="mpills"></div>
    <div id="more-skill-list"><div class="loading">Loading&hellip;</div></div>`;
  try {
    const commands = await api('/api/commands');
    _moreAllSkills = Array.isArray(commands) ? commands : [];
    const cats = [...new Set(_moreAllSkills.map(s => s.category || 'General'))].sort();
    moreRenderSkillPills(['All', ...cats]);
    moreRenderSkills(_moreAllSkills);
  } catch {
    const listEl = document.getElementById('more-skill-list');
    if (listEl) listEl.innerHTML = '<div class="empty">Could not load commands.</div>';
  }
}

function moreSetSkillTab(tab) { moreSetSkillCat(tab); }

function moreRenderSkillPills(cats) {
  const el = document.getElementById('more-skill-pills');
  if (!el) return;
  el.innerHTML = cats.map(c =>
    `<button class="mpill${c === _moreSkillCat ? ' active' : ''}" onclick="moreSetSkillCat('${esc(c)}')">${esc(c)}</button>`
  ).join('');
}

function moreSetSkillCat(cat) {
  _moreSkillCat = cat;
  document.querySelectorAll('.mpill').forEach(p => {
    p.classList.toggle('active', p.textContent === cat);
  });
  moreApplySkillFilter();
}

function moreSkillSearch(q) {
  _moreSkillQuery = q.toLowerCase();
  moreApplySkillFilter();
}

function moreApplySkillFilter() {
  let cmds = _moreAllSkills;
  if (_moreSkillCat && _moreSkillCat !== 'All') {
    cmds = cmds.filter(s => (s.category || 'General') === _moreSkillCat);
  }
  if (_moreSkillQuery) {
    cmds = cmds.filter(s =>
      (s.name || '').toLowerCase().includes(_moreSkillQuery) ||
      (s.description || '').toLowerCase().includes(_moreSkillQuery) ||
      (s.command || '').toLowerCase().includes(_moreSkillQuery)
    );
  }
  moreRenderSkills(cmds);
}

function moreRenderSkills(cmds) {
  const el = document.getElementById('more-skill-list');
  if (!el) return;
  if (!cmds.length) {
    el.innerHTML = '<div class="empty">No commands found.</div>';
    return;
  }
  el.innerHTML = cmds.map(s => `
    <div class="msk-card" onclick="launchCommand(${JSON.stringify(s.command || '')})" style="cursor:pointer">
      <div class="msk-info">
        <div class="msk-name">${esc(s.name || '')}</div>
        <div class="msk-desc">${esc(s.description || '')}</div>
      </div>
    </div>`).join('');
}

function launchCommand(command) {
  const ta = document.getElementById('chat-textarea');
  if (ta) {
    ta.value = command;
    ta.dispatchEvent(new Event('input'));
  }
  switchTab('chat');
  setTimeout(() => {
    const ta2 = document.getElementById('chat-textarea');
    if (ta2) ta2.focus();
  }, 50);
}

// ── Events ───────────────────────────────────────────────────────────────────

async function moreLoadEvents() {
  const el = document.getElementById('msec-inner-events');
  if (!el) return;
  el.innerHTML = '<div class="loading">Loading&hellip;</div>';
  try {
    const events = await api('/api/events');
    let html = `<button class="mbtn mbtn-sm" onclick="moreShowEventForm()" style="margin-bottom:12px">+ Log Event</button>`;
    html += '<div id="mevt-form" style="display:none"></div>';
    if (!Array.isArray(events) || !events.length) {
      html += '<div class="empty">No events logged yet.</div>';
    } else {
      html += events.map(ev => {
        let dateRange = ev.start_date;
        if (ev.end_date && ev.end_date !== ev.start_date) dateRange += ' – ' + ev.end_date;
        return `
          <div class="mpn-card" id="mevt-card-${ev.id}">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
              <div style="flex:1;min-width:0">
                <div style="font-size:13px;font-weight:600">${esc(ev.event_name)}</div>
                <div style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);margin-top:2px">${esc(dateRange)}</div>
                ${ev.file_count ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">${ev.file_count} file${ev.file_count !== 1 ? 's' : ''}</div>` : ''}
              </div>
              <button class="mbtn mbtn-sm mbtn-d" onclick="moreDeleteEvent(${ev.id})" style="flex-shrink:0">Delete</button>
            </div>
            ${ev.description || ev.attendance_notes ? `
            <div id="mevt-detail-${ev.id}" style="display:none;margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
              ${ev.description      ? `<div style="font-size:12px;color:var(--text);margin-bottom:4px">${esc(ev.description)}</div>` : ''}
              ${ev.attendance_notes ? `<div style="font-size:12px;color:var(--muted)">${esc(ev.attendance_notes)}</div>`             : ''}
            </div>
            <div onclick="moreToggleEventDetail(${ev.id})" style="font-size:11px;font-family:'DM Mono',monospace;color:var(--gold);cursor:pointer;margin-top:6px;-webkit-tap-highlight-color:transparent" id="mevt-tog-${ev.id}">+ Details</div>` : ''}
          </div>`;
      }).join('');
    }
    el.innerHTML = html;
  } catch {
    const inner = document.getElementById('msec-inner-events');
    if (inner) inner.innerHTML = '<div class="empty">Could not load events.</div>';
  }
}

function moreToggleEventDetail(id) {
  const detail = document.getElementById(`mevt-detail-${id}`);
  const tog    = document.getElementById(`mevt-tog-${id}`);
  if (!detail) return;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'block';
  if (tog) tog.textContent = isOpen ? '+ Details' : '– Details';
}

function moreShowEventForm() {
  const formEl = document.getElementById('mevt-form');
  if (!formEl) return;
  if (formEl.style.display !== 'none') {
    formEl.style.display = 'none';
    return;
  }
  formEl.style.display = 'block';
  formEl.innerHTML = `
    <div class="mform" style="margin-bottom:12px">
      <input id="mevt-name"  type="text" placeholder="Event name *">
      <div style="display:flex;gap:8px">
        <input id="mevt-start" type="date" placeholder="Start date *" style="flex:1;color-scheme:dark">
        <input id="mevt-end"   type="date" placeholder="End date"     style="flex:1;color-scheme:dark">
      </div>
      <textarea id="mevt-desc" rows="4" placeholder="Description"></textarea>
      <textarea id="mevt-att"  rows="4" placeholder="Attendance &amp; Session Notes"></textarea>
      <div style="margin-bottom:8px">
        <label style="display:inline-block;cursor:pointer;padding:5px 10px;border:1px solid var(--border);border-radius:var(--r-btn);font-size:11px;color:var(--text);background:var(--surface)">
          Attach Files
          <input id="mevt-files" type="file" multiple style="display:none" onchange="moreShowFileNames(this)">
        </label>
        <div id="mevt-filenames" style="font-size:11px;color:var(--muted);margin-top:4px"></div>
      </div>
      <div id="mevt-err" style="display:none;font-size:12px;color:var(--red);margin-bottom:8px"></div>
      <div class="mfrow">
        <button class="mbtn mbtn-p" onclick="moreSaveEvent()">Save</button>
        <button class="mbtn"        onclick="moreShowEventForm()">Cancel</button>
      </div>
    </div>`;
  document.getElementById('mevt-name')?.focus();
}

function moreShowFileNames(input) {
  const el = document.getElementById('mevt-filenames');
  if (!el) return;
  el.textContent = Array.from(input.files).map(f => f.name).join(', ');
}

async function moreSaveEvent() {
  const name  = (document.getElementById('mevt-name')?.value  || '').trim();
  const start = (document.getElementById('mevt-start')?.value || '').trim();
  const errEl = document.getElementById('mevt-err');
  if (!name || !start) {
    if (errEl) { errEl.textContent = 'Event name and start date are required.'; errEl.style.display = 'block'; }
    return;
  }
  const fd = new FormData();
  fd.append('event_name', name);
  fd.append('start_date', start);
  const end  = (document.getElementById('mevt-end')?.value  || '').trim();
  const desc = (document.getElementById('mevt-desc')?.value || '').trim();
  const att  = (document.getElementById('mevt-att')?.value  || '').trim();
  if (end)  fd.append('end_date',         end);
  if (desc) fd.append('description',      desc);
  if (att)  fd.append('attendance_notes', att);
  const filesInput = document.getElementById('mevt-files');
  if (filesInput?.files) {
    for (const f of filesInput.files) fd.append('files[]', f);
  }
  try {
    const res = await fetch('/api/events', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    _moreSecLoaded['events'] = false;
    moreLoadEvents();
  } catch {
    if (errEl) { errEl.textContent = 'Failed to save event. Try again.'; errEl.style.display = 'block'; }
  }
}

async function moreDeleteEvent(id) {
  if (!confirm('Delete this event and all its files?')) return;
  try {
    await api(`/api/events/${id}`, { method: 'DELETE' });
    const card = document.getElementById(`mevt-card-${id}`);
    if (card) {
      card.style.transition = 'opacity .3s';
      card.style.opacity = '0';
      setTimeout(() => { if (card.parentNode) card.remove(); }, 300);
    }
  } catch { alert('Failed to delete event.'); }
}

// ── Members ───────────────────────────────────────────────────────────────────

async function moreLoadMembers() {
  const el = document.getElementById('msec-inner-members');
  if (!el) return;
  _moreAllMembers   = [];
  _memberPage       = 0;
  _memberCurrentList = null;
  _expandedMemberId = null;
  el.innerHTML = `
    <input id="mmem-search" type="search" class="msrch" placeholder="Search members&hellip;"
      oninput="memberSearchDebounced(this.value)" style="margin-bottom:8px">
    <div id="mmem-stats" style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:8px">Loading&hellip;</div>
    <div id="mmem-list"><div class="loading">Loading&hellip;</div></div>`;
  try {
    const members = await api('/api/members');
    _moreAllMembers = Array.isArray(members) ? members : [];
    _memberRenderStats();
    _memberRenderList();
  } catch {
    const listEl = document.getElementById('mmem-list');
    if (listEl) listEl.innerHTML = '<div class="empty">Could not load members.</div>';
  }
}

function _memberRenderStats() {
  const el = document.getElementById('mmem-stats');
  if (!el) return;
  const total        = _moreAllMembers.length;
  const active       = _moreAllMembers.filter(m => !m.member_status || m.member_status === 'active').length;
  const deceased     = _moreAllMembers.filter(m => m.member_status === 'deceased').length;
  const disconnected = _moreAllMembers.filter(m => m.member_status === 'disconnected').length;
  const non_local    = _moreAllMembers.filter(m => m.member_status === 'non_local').length;
  const snowbird     = _moreAllMembers.filter(m => m.member_status === 'snowbird').length;
  const parts = [`Total: ${total}`, `Active: ${active}`];
  if (deceased)     parts.push(`\u{1F7E4} ${deceased}`);
  if (disconnected) parts.push(`\u{1F534} ${disconnected}`);
  if (non_local)    parts.push(`\u{1F535} ${non_local}`);
  if (snowbird)     parts.push(`\u{1F7E1} ${snowbird}`);
  el.textContent = parts.join(' · ');
}

function _memberStatusBadge(status) {
  if (!status || status === 'active') return '';
  const map = { deceased: '\u{1F7E4} Deceased', disconnected: '\u{1F534} Disconnected', non_local: '\u{1F535} Non-local', snowbird: '\u{1F7E1} Snowbird' };
  return `<span style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted)">${esc(map[status] || status)}</span>`;
}

function _memberRenderList() {
  const el = document.getElementById('mmem-list');
  if (!el) return;
  const source = _memberCurrentList !== null ? _memberCurrentList : _moreAllMembers;
  if (!source.length) {
    el.innerHTML = '<div class="empty">No members found.</div>';
    return;
  }
  const page = source.slice(0, (_memberPage + 1) * 20);
  let html = page.map(m => `
    <div class="mpn-card" id="mmem-row-${m.id}">
      <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer;-webkit-tap-highlight-color:transparent"
           onclick="moreExpandMember(${m.id})">
        <span style="font-size:13px;font-weight:500">${esc(m.name || '')}</span>
        <span id="mmem-badge-${m.id}">${_memberStatusBadge(m.member_status)}</span>
      </div>
      <div id="mmem-exp-${m.id}" style="display:none"></div>
    </div>`).join('');
  if (source.length > page.length) {
    html += `<button class="mbtn mbtn-sm" onclick="memberLoadMore()" style="margin-top:8px">Load more (${source.length - page.length} remaining)</button>`;
  }
  el.innerHTML = html;
}

function memberLoadMore() {
  _memberPage++;
  _memberRenderList();
}

function memberSearchDebounced(q) {
  if (_memberSearchTimer) clearTimeout(_memberSearchTimer);
  _memberSearchTimer = setTimeout(() => {
    _memberPage = 0;
    if (q.length >= 2) {
      _memberCurrentList = _moreAllMembers.filter(m =>
        (m.name || '').toLowerCase().includes(q.toLowerCase())
      );
    } else {
      _memberCurrentList = null;
    }
    _memberRenderList();
  }, 300);
}

function moreExpandMember(id) {
  const expEl = document.getElementById(`mmem-exp-${id}`);
  if (!expEl) return;
  if (_expandedMemberId === id) {
    expEl.style.display = 'none';
    _expandedMemberId = null;
    return;
  }
  if (_expandedMemberId !== null) {
    const prev = document.getElementById(`mmem-exp-${_expandedMemberId}`);
    if (prev) prev.style.display = 'none';
  }
  _expandedMemberId = id;
  const m = _moreAllMembers.find(x => x.id === id);
  if (!m) return;
  const status = m.member_status || 'active';
  const opts = [
    ['active', 'Active'], ['deceased', 'Deceased'], ['disconnected', 'Disconnected'],
    ['non_local', 'Non-local'], ['snowbird', 'Snowbird'],
  ].map(([v, l]) => `<option value="${v}"${status === v ? ' selected' : ''}>${l}</option>`).join('');
  expEl.innerHTML = `
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
      ${m.email ? `<div style="margin-bottom:6px"><a href="mailto:${esc(m.email)}" style="color:var(--gold);font-size:13px;text-decoration:none">${esc(m.email)}</a></div>` : ''}
      ${m.phone ? `<div style="margin-bottom:10px"><a href="tel:${esc(m.phone)}" style="color:var(--gold);font-size:13px;text-decoration:none">${esc(m.phone)}</a></div>` : ''}
      <div style="margin-bottom:8px">
        <label style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);display:block;margin-bottom:4px">STATUS</label>
        <select id="mmem-status-${id}" onchange="memberStatusChange(${id})"
          style="width:100%;padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:13px;outline:none">${opts}</select>
      </div>
      <div id="mmem-snowbird-wrap-${id}" style="margin-bottom:8px${status === 'snowbird' ? '' : ';display:none'}">
        <label style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);display:block;margin-bottom:4px">EXPECTED RETURN</label>
        <input type="date" id="mmem-return-${id}" value="${esc(m.snowbird_return || '')}"
          style="width:100%;padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:13px;outline:none;box-sizing:border-box;color-scheme:dark">
      </div>
      <div style="margin-bottom:10px">
        <label style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);display:block;margin-bottom:4px">NOTES</label>
        <textarea id="mmem-note-${id}" rows="2"
          style="display:block;width:100%;padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-btn);color:var(--text);font-family:inherit;font-size:13px;outline:none;resize:vertical;box-sizing:border-box"
          placeholder="Optional note&hellip;">${esc(m.status_note || '')}</textarea>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <button class="mbtn mbtn-p mbtn-sm" onclick="memberSave(${id})">Save</button>
        <span id="mmem-saved-${id}" style="display:none;font-size:12px;color:#2e7d32">✓ Saved</span>
      </div>
    </div>`;
  expEl.style.display = 'block';
}

function memberStatusChange(id) {
  const sel = document.getElementById(`mmem-status-${id}`);
  const wrap = document.getElementById(`mmem-snowbird-wrap-${id}`);
  if (sel && wrap) wrap.style.display = sel.value === 'snowbird' ? 'block' : 'none';
}

async function memberSave(id) {
  const sel     = document.getElementById(`mmem-status-${id}`);
  const noteEl  = document.getElementById(`mmem-note-${id}`);
  const retEl   = document.getElementById(`mmem-return-${id}`);
  const savedEl = document.getElementById(`mmem-saved-${id}`);
  const status  = sel?.value || 'active';
  const body = {
    member_status:   status,
    status_note:     (noteEl?.value || '').trim() || null,
    snowbird_return: (status === 'snowbird' && retEl?.value) ? retEl.value : null,
  };
  try {
    const updated = await api(`/api/members/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const idx = _moreAllMembers.findIndex(m => m.id === id);
    if (idx !== -1) Object.assign(_moreAllMembers[idx], updated);
    _memberRenderStats();
    const badgeEl = document.getElementById(`mmem-badge-${id}`);
    if (badgeEl) badgeEl.innerHTML = _memberStatusBadge(status);
    if (savedEl) {
      savedEl.style.display = 'inline';
      setTimeout(() => { savedEl.style.display = 'none'; }, 2500);
    }
  } catch { alert('Failed to save.'); }
}

function moreToggleTheme(isLight) {
  const theme = isLight ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('watson-theme', theme);
}

// ─── Chat tab ─────────────────────────────────────────────────────────────────

async function renderChat() {
  document.getElementById('chat-overlay').classList.add('active');
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
  const ta = document.getElementById('chat-textarea');
  if (ta) ta.focus();
  try {
    const res = await fetch('/api/memory/recent');
    const summaries = await res.json();
    if (Array.isArray(summaries) && summaries.length) {
      chatMemoryContext = 'WATSON MEMORY — RECENT SESSIONS:\n' +
        summaries.map((s, i) => `[${i + 1}] ${s}`).join('\n') +
        '\n\nUse this context to maintain continuity with Dr. Bill across conversations.';
    } else {
      chatMemoryContext = '';
    }
  } catch { chatMemoryContext = ''; }
}

function closeChat() {
  if (chatIdleTimer) { clearTimeout(chatIdleTimer); chatIdleTimer = null; }
  if (chatHistory.length >= 2) _summarizeChat(); // fire-and-forget
  chatHistory = [];
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.innerHTML = '';
  chatMemoryContext = '';
  document.getElementById('chat-overlay').classList.remove('active');
  switchTab('home');
}

async function _summarizeChat() {
  try {
    await fetch('/api/chat/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ history: chatHistory }),
    });
  } catch {}
}

function renderWithImages(text) {
  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  const imgPat = /https?:\/\/\S+\.(?:jpg|jpeg|png|webp|gif)(?:\?\S*)?|https?:\/\/(?:images\.unsplash\.com|unsplash\.com)\/\S+/gi;
  let out = '', last = 0, m;
  while ((m = imgPat.exec(text)) !== null) {
    out += esc(text.slice(last, m.index));
    out += `<img src="${esc(m[0])}" alt="" loading="lazy" style="max-width:100%;border-radius:4px;display:block;margin-top:6px">`;
    last = m.index + m[0].length;
  }
  out += esc(text.slice(last));
  return out;
}

const _DIRECTIVE_PREFIXES = ['kb:', 'cdb:', 'wdb:', 'web:', 'task:', 'note:', 'remind:', 'sms:', 'polish:', 'bible:'];

function applyDirective(prefix) {
  const ta  = document.getElementById('chat-textarea');
  const sel = document.getElementById('chat-directive-sel');
  if (!ta) return;
  let val = ta.value;
  // Strip any existing directive prefix
  for (const d of _DIRECTIVE_PREFIXES) {
    if (val.toLowerCase().startsWith(d)) {
      val = val.slice(d.length).trimStart();
      break;
    }
  }
  if (prefix) {
    ta.value = prefix + ' ' + val;
  } else {
    ta.value = val;
  }
  // Reset dropdown to default so same directive can be reselected
  if (sel) sel.value = '';
  ta.focus();
  ta.selectionStart = ta.selectionEnd = ta.value.length;
  // Trigger auto-resize
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
}

function appendChatMsg(role, content) {
  const msgs = document.getElementById('chat-messages');
  if (!msgs) return;
  const div = document.createElement('div');
  div.className = `cmsg cmsg-${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'cmsg-bubble';
  bubble.textContent = content;
  div.appendChild(bubble);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return bubble;
}

async function sendChatStream() {
  const ta = document.getElementById('chat-textarea');
  if (!ta) return;
  const message = ta.value.trim();
  if (!message) return;

  if (message.toLowerCase() === 'email that to me' && lastKbResult) {
    ta.value = '';
    ta.style.height = 'auto';
    ta.focus();
    appendChatMsg('user', message);
    const msgs = document.getElementById('chat-messages');
    const statusEl = document.createElement('div');
    statusEl.className = 'cstatus';
    statusEl.textContent = 'Sending to your inbox…';
    if (msgs) { msgs.appendChild(statusEl); msgs.scrollTop = msgs.scrollHeight; }
    try {
      await api('/api/skills/kb/email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lastKbResult),
      });
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', 'Sent to your inbox.');
      lastKbResult = null;
    } catch (err) {
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', `Error: ${err.message}`);
    }
    return;
  }

  const _msgLower = message.toLowerCase();
  if (_msgLower.startsWith('kb:') || _msgLower.startsWith('search the kb:')) {
    ta.value = '';
    ta.style.height = 'auto';
    ta.focus();
    appendChatMsg('user', message);
    const msgs = document.getElementById('chat-messages');
    const statusEl = document.createElement('div');
    statusEl.className = 'cstatus';
    statusEl.textContent = 'Searching knowledge base…';
    if (msgs) { msgs.appendChild(statusEl); msgs.scrollTop = msgs.scrollHeight; }
    try {
      const res = await api('/api/skills/kb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: message }),
      });
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', res.result || '(no result)');
      const resultText = res.result || '';
      const parts = resultText.split('\n\nSources:');
      const synopsis = parts[0] || '';
      const sources = (parts[1] || '').split('\n')
        .map(l => l.replace(/^•\s*/, '').trim())
        .filter(l => l && !l.startsWith('Reply'));
      lastKbResult = { query: res.query || '', synopsis, sources };
    } catch (err) {
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', `Error: ${err.message}`);
    }
    return;
  }

  if (message.toLowerCase().startsWith('polish this:')) {
    ta.value = '';
    ta.style.height = 'auto';
    ta.focus();
    appendChatMsg('user', message);
    const msgs = document.getElementById('chat-messages');
    const statusEl = document.createElement('div');
    statusEl.className = 'cstatus';
    statusEl.textContent = 'Polishing…';
    if (msgs) { msgs.appendChild(statusEl); msgs.scrollTop = msgs.scrollHeight; }
    try {
      const res = await api('/api/skills/polish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: message }),
      });
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', res.result || '(no result)');
    } catch (err) {
      if (statusEl.parentNode) statusEl.remove();
      appendChatMsg('watson', `Error: ${err.message}`);
    }
    return;
  }

  ta.value = '';
  ta.style.height = 'auto';
  ta.focus();

  appendChatMsg('user', message);
  chatHistory.push({ role: 'user', content: message });

  if (chatIdleTimer) clearTimeout(chatIdleTimer);
  chatIdleTimer = setTimeout(async () => {
    if (chatHistory.length >= 2) await _summarizeChat();
    chatIdleTimer = null;
  }, 30 * 60 * 1000);

  const msgs = document.getElementById('chat-messages');
  const statusEl = document.createElement('div');
  statusEl.className = 'cstatus';
  statusEl.textContent = 'Watson is thinking…';
  if (msgs) { msgs.appendChild(statusEl); msgs.scrollTop = msgs.scrollHeight; }

  const watsonDiv = document.createElement('div');
  watsonDiv.className = 'cmsg cmsg-watson';
  const bubble = document.createElement('div');
  bubble.className = 'cmsg-bubble';
  watsonDiv.appendChild(bubble);

  let fullReply = '';
  let bubbleAdded = false;

  try {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history: chatHistory.slice(0, -1), memory_context: chatMemoryContext }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let done = false;

    while (!done) {
      const { done: streamDone, value } = await reader.read();
      if (streamDone) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split('\n\n');
      buf = events.pop();

      for (const event of events) {
        const dataLines = event.split('\n')
          .filter(l => l.startsWith('data: '))
          .map(l => l.slice(6));
        if (!dataLines.length) continue;

        const first = dataLines[0];
        if (first === '[DONE]') { done = true; break; }
        if (first.startsWith('[ERROR]')) {
          if (statusEl.parentNode) statusEl.remove();
          statusEl.textContent = first.slice(7).trim() || 'Error from Watson';
          if (msgs) { msgs.appendChild(statusEl); msgs.scrollTop = msgs.scrollHeight; }
          done = true; break;
        }
        if (first.startsWith('[CONFIRM_EMAIL]') || first.startsWith('[QR_IMAGE]')) continue;

        try {
          const json = JSON.parse(first);
          if (json.type === 'status') {
            statusEl.textContent = json.text;
            continue;
          }
        } catch {}

        const token = dataLines.join('\n');
        if (!bubbleAdded) {
          bubbleAdded = true;
          if (statusEl.parentNode) statusEl.remove();
          if (msgs) msgs.appendChild(watsonDiv);
        }
        fullReply += token;
        bubble.innerHTML = renderWithImages(fullReply);
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
      }
    }
  } catch (err) {
    if (statusEl.parentNode) statusEl.remove();
    appendChatMsg('watson', `Error: ${err.message}`);
  }

  if (fullReply) chatHistory.push({ role: 'assistant', content: fullReply });
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const savedTheme = localStorage.getItem('watson-theme');
  if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);

  const dateEl = document.getElementById('hdr-date');
  if (dateEl) {
    dateEl.textContent = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  }
  const wMark = document.getElementById('hdr-mark');
  if (wMark) wMark.addEventListener('click', () => location.reload(true));

  const chatTa = document.getElementById('chat-textarea');
  if (chatTa) {
    chatTa.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatStream();
      }
    });
    chatTa.addEventListener('input', () => {
      chatTa.style.height = 'auto';
      chatTa.style.height = Math.min(chatTa.scrollHeight, 120) + 'px';
    });
  }
  // iOS keyboard: keep chat input visible when keyboard opens
  if (window.visualViewport) {
    const chatTab = document.getElementById('tab-chat');
    const NAV_HEIGHT = 60;
    function onViewportResize() {
      if (!chatTab) return;
      const vv = window.visualViewport;
      const bottomOffset = window.innerHeight - vv.height - vv.offsetTop;
      const keyboardHeight = Math.max(0, bottomOffset);
      chatTab.style.bottom = (keyboardHeight + NAV_HEIGHT) + 'px';
    }
    window.visualViewport.addEventListener('resize', onViewportResize);
    window.visualViewport.addEventListener('scroll', onViewportResize);
  }

  switchTab('home');
});

// ─── Logins overlay ───────────────────────────────────────────────────────────

let _loginsData        = [];
let _challengeAttempts = 0;
let _currentChallengeId = null;
let _loginsUnlocked    = false;
let _editingLoginId    = null;

function openLogins() {
  _challengeAttempts  = 0;
  _currentChallengeId = null;
  _loginsUnlocked     = false;
  _editingLoginId     = null;
  document.getElementById('logins-overlay').classList.add('active');
  _loginsCheckStatus();
}

function closeLogins() {
  document.getElementById('logins-overlay').classList.remove('active');
  _loginsUnlocked = false;
  _editingLoginId = null;
  _loginsHideForm();
}

async function _loginsCheckStatus() {
  try {
    const data = await api('/api/logins/status');
    if (data.locked) { _loginsShowState('locked'); }
    else             { await _loginsBeginChallenge(); }
  } catch(e) {
    _loginsShowState('locked');
  }
}

function _loginsShowState(state) {
  ['locked','challenge','list'].forEach(s => {
    const el = document.getElementById('logins-state-' + s);
    if (el) el.style.display = 'none';
  });
  const target = document.getElementById('logins-state-' + state);
  if (target) target.style.display = '';
  const addBtn = document.getElementById('logins-add-btn');
  if (addBtn) addBtn.style.display = (state === 'list') ? '' : 'none';
}

async function _loginsBeginChallenge() {
  _loginsShowState('challenge');
  const errEl = document.getElementById('logins-challenge-err');
  if (errEl) errEl.textContent = '';
  try {
    const url = '/api/logins/challenge' + (_currentChallengeId ? '?exclude=' + _currentChallengeId : '');
    const data = await api(url);
    _currentChallengeId = data.id;
    document.getElementById('logins-challenge-word').textContent = data.challenge;
    const inp = document.getElementById('logins-challenge-input');
    inp.value = '';
    inp.focus();
  } catch(e) {
    document.getElementById('logins-challenge-word').textContent = '(error)';
  }
}

async function loginsSubmitChallenge() {
  const response = (document.getElementById('logins-challenge-input').value || '').trim();
  if (!response) return;
  const errEl = document.getElementById('logins-challenge-err');
  try {
    const data = await api('/api/logins/challenge/verify', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({response}),
    });
    if (data.success) {
      _loginsUnlocked = true;
      await _loginsLoadList();
    } else {
      _challengeAttempts++;
      if (_challengeAttempts >= 3) {
        try { await api('/api/logins/lock', {method: 'POST'}); } catch(e) {}
        _loginsShowState('locked');
      } else {
        const rem = 3 - _challengeAttempts;
        errEl.textContent = `Incorrect — ${rem} attempt${rem === 1 ? '' : 's'} remaining.`;
        const inp = document.getElementById('logins-challenge-input');
        inp.value = '';
        inp.focus();
      }
    }
  } catch(e) {
    errEl.textContent = 'Error verifying. Try again.';
  }
}

async function loginsSkipChallenge() {
  _challengeAttempts++;
  if (_challengeAttempts >= 3) {
    try { await api('/api/logins/lock', {method: 'POST'}); } catch(e) {}
    _loginsShowState('locked');
    return;
  }
  const errEl = document.getElementById('logins-challenge-err');
  if (errEl) errEl.textContent = '';
  const excludeId = _currentChallengeId;
  _currentChallengeId = null;
  try {
    const data = await api(`/api/logins/challenge?exclude=${excludeId}`);
    _currentChallengeId = data.id;
    document.getElementById('logins-challenge-word').textContent = data.challenge;
    const inp = document.getElementById('logins-challenge-input');
    inp.value = '';
    inp.focus();
  } catch(e) {}
}

async function _loginsLoadList() {
  _loginsShowState('list');
  const list     = document.getElementById('logins-list');
  const searchEl = document.getElementById('logins-search');
  list.innerHTML = '<div class="loading">Loading&hellip;</div>';
  if (searchEl) searchEl.value = '';
  try {
    const data = await api('/api/logins');
    if (data && data.locked) { _loginsShowState('locked'); return; }
    _loginsData = Array.isArray(data) ? data : [];
    _loginsRenderList();
    if (searchEl) searchEl.focus();
  } catch(e) {
    list.innerHTML = '<div class="empty">Failed to load logins.</div>';
  }
}

function _loginsFilterAndRender() {
  const q = (document.getElementById('logins-search')?.value || '').trim().toLowerCase();
  if (!q) { _loginsRenderList(); return; }
  _loginsRenderList(_loginsData.filter(l =>
    (l.label    || '').toLowerCase().includes(q) ||
    (l.username || '').toLowerCase().includes(q) ||
    (l.url      || '').toLowerCase().includes(q)
  ));
}

function _loginsRenderList(data) {
  if (data === undefined) data = _loginsData;
  const list = document.getElementById('logins-list');
  if (!data.length) {
    list.innerHTML = `<div class="empty">${_loginsData.length ? 'No logins match your search.' : 'No logins saved. Tap + to add one.'}</div>`;
    return;
  }
  list.innerHTML = data.map(l => `
    <div class="login-card" id="login-card-${l.id}">
      <div style="display:flex;align-items:flex-start;gap:8px">
        <div style="flex:1;min-width:0">
          <div class="login-label">${esc(l.label)}</div>
          ${l.username ? `<div class="login-meta">${esc(l.username)}</div>` : ''}
          ${l.url      ? `<div class="login-meta" style="color:var(--blue)">${esc(l.url)}</div>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;margin-top:2px">
          <button class="login-reveal-btn" onclick="loginsEdit(${l.id})">Edit</button>
          <button class="login-del-btn"    onclick="loginsDelete(${l.id})">Del</button>
        </div>
      </div>
      ${l.password ? `
      <div class="login-row" style="margin-top:8px">
        <span class="login-pwd" id="login-pwd-${l.id}"
              data-pwd="${esc(String(l.password)).replace(/"/g,'&quot;')}">••••••••</span>
        <button class="login-reveal-btn" onclick="loginsReveal(${l.id})" title="Show/hide password">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
               stroke-linejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
        </button>
      </div>` : ''}
      ${l.notes ? `<div class="login-meta" style="margin-top:6px">${esc(l.notes)}</div>` : ''}
    </div>`).join('');
}

function loginsReveal(id) {
  const el = document.getElementById('login-pwd-' + id);
  if (!el) return;
  if (el.getAttribute('data-showing')) {
    el.textContent = '••••••••';
    el.removeAttribute('data-showing');
  } else {
    el.textContent = el.getAttribute('data-pwd');
    el.setAttribute('data-showing', '1');
  }
}

function loginsShowForm(id) {
  _editingLoginId = id || null;
  const panel   = document.getElementById('logins-add-panel');
  const titleEl = document.getElementById('logins-form-title');
  panel.style.display = '';
  if (id) {
    const l = _loginsData.find(x => x.id === id);
    if (l) {
      titleEl.textContent = 'Edit Login';
      document.getElementById('login-inp-label').value = l.label    || '';
      document.getElementById('login-inp-user').value  = l.username || '';
      document.getElementById('login-inp-pass').value  = l.password || '';
      document.getElementById('login-inp-url').value   = l.url      || '';
      document.getElementById('login-inp-notes').value = l.notes    || '';
    }
  } else {
    titleEl.textContent = 'Add Login';
    ['label','user','pass','url','notes'].forEach(f => {
      const el = document.getElementById('login-inp-' + f);
      if (el) el.value = '';
    });
  }
  document.getElementById('login-inp-label').focus();
  panel.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function loginsEdit(id) { loginsShowForm(id); }

function _loginsHideForm() {
  const panel = document.getElementById('logins-add-panel');
  if (panel) panel.style.display = 'none';
  ['label','user','pass','url','notes'].forEach(f => {
    const el = document.getElementById('login-inp-' + f);
    if (el) el.value = '';
  });
  _editingLoginId = null;
}

async function loginsSave() {
  const label = (document.getElementById('login-inp-label').value || '').trim();
  if (!label) { document.getElementById('login-inp-label').focus(); return; }
  const body = {
    label,
    username: document.getElementById('login-inp-user').value.trim() || null,
    password: document.getElementById('login-inp-pass').value        || null,
    url:      document.getElementById('login-inp-url').value.trim()  || null,
    notes:    document.getElementById('login-inp-notes').value.trim()|| null,
  };
  try {
    if (_editingLoginId) {
      await api(`/api/logins/${_editingLoginId}`, {
        method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body),
      });
    } else {
      await api('/api/logins', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body),
      });
    }
    _loginsHideForm();
    await _loginsLoadList();
  } catch(e) {
    alert('Failed to save login.');
  }
}

async function loginsDelete(id) {
  if (!confirm('Delete this login?')) return;
  try {
    await api(`/api/logins/${id}`, {method: 'DELETE'});
    _loginsData = _loginsData.filter(l => l.id !== id);
    _loginsRenderList();
  } catch(e) {
    alert('Failed to delete login.');
  }
}

// ─── Dev Loop ─────────────────────────────────────────────────────────────────

let _devProjects = [];
let _devView = 'list';
let _devActiveSlug = null;

const _DEV_STATUS_BADGE = {
  pending:   { cls: 'badge-NOTE',     label: 'PENDING'   },
  running:   { cls: 'badge-CALENDAR', label: 'RUNNING'   },
  paused:    { cls: 'badge-EMAIL',    label: 'PAUSED'     },
  delivered: { cls: '',               label: 'DELIVERED',  style: 'background:rgba(76,175,125,.12);color:var(--green);border:1px solid rgba(76,175,125,.3)' },
  stopped:   { cls: '',               label: 'STOPPED',    style: 'background:rgba(102,102,102,.10);color:var(--muted);border:1px solid var(--border)' },
  failed:    { cls: '',               label: 'FAILED',     style: 'background:rgba(201,80,76,.12);color:var(--red);border:1px solid rgba(201,80,76,.3)' },
};

function _devBadge(status) {
  const s = _DEV_STATUS_BADGE[status] || { cls: '', label: status.toUpperCase(), style: '' };
  const style = s.style ? ` style="${s.style}"` : '';
  return `<span class="badge ${s.cls}"${style}>${s.label}</span>`;
}

function _devFmtDate(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }
  catch { return iso; }
}

async function devLoopLoad() {
  const el = document.getElementById('msec-inner-dev');
  if (!el) return;
  try {
    _devProjects = await api('/api/dev-loop/projects');
    _devView = 'list';
    _devActiveSlug = null;
    _devRenderList(el);
  } catch(e) {
    el.innerHTML = `<div class="empty">Failed to load projects.</div>`;
  }
}

function _devRenderList(el) {
  if (!el) el = document.getElementById('msec-inner-dev');
  if (!el) return;
  let html = `
    <div style="display:flex;justify-content:flex-end;margin-bottom:10px">
      <button class="mbtn mbtn-p mbtn-sm" onclick="devLoopShowNew()">+ New Project</button>
    </div>`;
  if (!_devProjects.length) {
    html += `<div class="empty">No projects yet.</div>`;
  } else {
    _devProjects.forEach(p => {
      const paused = p.status === 'paused';
      html += `
        <div class="card" style="cursor:pointer;margin-bottom:8px" onclick="devLoopOpenProject('${esc(p.slug)}')">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
            <div>
              <div class="card-title">${esc(p.title)}</div>
              <div class="card-sub" style="font-family:'DM Mono',monospace;font-size:11px">${esc(p.slug)}</div>
            </div>
            ${_devBadge(p.status)}
          </div>
          <div class="card-sub" style="margin-top:6px">${_devFmtDate(p.updated_at)}</div>
          ${paused ? `
            <div style="display:flex;gap:8px;margin-top:10px" onclick="event.stopPropagation()">
              <button class="mbtn mbtn-p mbtn-sm" onclick="devLoopKeepGoing('${esc(p.slug)}')">Keep Going</button>
              <button class="mbtn mbtn-sm mbtn-d" onclick="devLoopStop('${esc(p.slug)}')">Stop</button>
            </div>` : ''}
        </div>`;
    });
  }
  el.innerHTML = html;
}

async function devLoopOpenProject(slug) {
  const el = document.getElementById('msec-inner-dev');
  if (!el) return;
  el.innerHTML = `<div class="loading">Loading&hellip;</div>`;
  try {
    const p = await api(`/api/dev-loop/projects/${encodeURIComponent(slug)}`);
    _devActiveSlug = slug;
    _devView = 'detail';
    const code = p.code || '';
    const spec = p.spec || '';
    const history = p.iteration_history ? JSON.parse(p.iteration_history || '[]') : [];
    el.innerHTML = `
      <div style="margin-bottom:12px">
        <button class="mbtn mbtn-sm" onclick="devLoopBackToList()" style="margin-bottom:8px">← Back</button>
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
          <div>
            <div style="font-size:15px;font-weight:600">${esc(p.title)}</div>
            <div style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted)">${esc(p.slug)}</div>
          </div>
          ${_devBadge(p.status)}
        </div>
        ${p.status === 'paused' ? `
          <div style="display:flex;gap:8px;margin-top:10px">
            <button class="mbtn mbtn-p mbtn-sm" onclick="devLoopKeepGoing('${esc(slug)}')">Keep Going</button>
            <button class="mbtn mbtn-sm mbtn-d" onclick="devLoopStop('${esc(slug)}')">Stop + Review</button>
          </div>` : ''}
      </div>
      ${code ? `
        <div class="mlabel">Generated Code</div>
        <div style="position:relative;margin-bottom:10px">
          <button class="mbtn mbtn-sm" onclick="devLoopCopyCode()" style="position:absolute;top:8px;right:8px;z-index:2">Copy</button>
          <pre id="dev-code-block" style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r-card);padding:12px;font-size:12px;font-family:'DM Mono',monospace;overflow-x:auto;white-space:pre;max-height:400px;overflow-y:auto;margin:0">${esc(code)}</pre>
        </div>` : ''}
      <div class="mlabel">Feedback & Re-trigger</div>
      <div class="mform" style="margin-bottom:10px">
        <textarea id="dev-feedback-ta" rows="4" placeholder="Paste Claude's review or write your own feedback…" style="resize:vertical"></textarea>
        <div style="display:flex;gap:8px">
          <button class="mbtn mbtn-p" style="flex:1;margin-bottom:0" onclick="devLoopRetrigger('${esc(slug)}')">Re-trigger Loop</button>
          <button class="mbtn" style="margin-bottom:0" onclick="devLoopShowNew()">New Project</button>
        </div>
      </div>
      ${spec ? `
        <div class="mlabel">Spec</div>
        <pre style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r-card);padding:12px;font-size:12px;font-family:'DM Mono',monospace;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;margin-bottom:10px">${esc(spec)}</pre>` : ''}
      <div class="mlabel">Details</div>
      <div class="card" style="font-size:12px;font-family:'DM Mono',monospace;margin-bottom:6px">
        <div>Iterations: ${p.current_iteration || 0} / ${p.max_iterations || 3}</div>
        ${p.delivered_at ? `<div>Delivered: ${_devFmtDate(p.delivered_at)}</div>` : ''}
        ${p.staging_path ? `<div style="color:var(--muted);margin-top:4px;word-break:break-all">${esc(p.staging_path)}</div>` : ''}
      </div>`;
  } catch(e) {
    el.innerHTML = `<div class="empty">Failed to load project.</div>`;
  }
}

function devLoopBackToList() {
  _devView = 'list';
  _devActiveSlug = null;
  const el = document.getElementById('msec-inner-dev');
  if (!el) return;
  _devRenderList(el);
}

function devLoopCopyCode() {
  const el = document.getElementById('dev-code-block');
  if (!el) return;
  const text = el.textContent || '';
  const btn = el.parentElement && el.parentElement.querySelector('button');
  function onSuccess() {
    if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); }
  }
  function onFailure() {
    if (btn) { btn.textContent = 'Select & copy manually'; setTimeout(() => { btn.textContent = 'Copy'; }, 2500); }
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
      execCommandFallback(text) ? onSuccess() : onFailure();
    });
  } else {
    execCommandFallback(text) ? onSuccess() : onFailure();
  }
  function execCommandFallback(str) {
    try {
      const ta = document.createElement('textarea');
      ta.value = str;
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }
}

function devLoopShowNew() {
  _devView = 'new';
  const el = document.getElementById('msec-inner-dev');
  if (!el) return;
  el.innerHTML = `
    <div style="margin-bottom:12px">
      <button class="mbtn mbtn-sm" onclick="devLoopBackToList()" style="margin-bottom:8px">← Back</button>
      <div style="font-size:15px;font-weight:600">New Dev Project</div>
    </div>
    <div class="mform">
      <input id="dev-new-title" type="text" placeholder="Title *" autocomplete="off">
      <input id="dev-new-slug"  type="text" placeholder="Slug (auto-generated if blank)" autocomplete="off">
      <div style="display:flex;border:1px solid var(--border);border-radius:var(--r-btn);overflow:hidden;margin-bottom:8px">
        <button id="dev-type-desc" onclick="devLoopSetType('description')"
          style="flex:1;padding:7px 0;border:none;cursor:pointer;background:var(--gold);color:#0f0f0f;font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Description</button>
        <button id="dev-type-spec" onclick="devLoopSetType('spec')"
          style="flex:1;padding:7px 0;border:none;cursor:pointer;background:transparent;color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;letter-spacing:.04em">Spec</button>
      </div>
      <textarea id="dev-new-input" rows="6" placeholder="Describe what you want to build…" style="resize:vertical"></textarea>
      <button class="mbtn mbtn-p" onclick="devLoopSubmit()">Submit → FMSPC</button>
    </div>`;
}

let _devNewType = 'description';
function devLoopSetType(t) {
  _devNewType = t;
  const d = document.getElementById('dev-type-desc');
  const s = document.getElementById('dev-type-spec');
  if (d && s) {
    d.style.background = t === 'description' ? 'var(--gold)' : 'transparent';
    d.style.color       = t === 'description' ? '#0f0f0f' : 'var(--muted)';
    s.style.background  = t === 'spec' ? 'var(--gold)' : 'transparent';
    s.style.color       = t === 'spec' ? '#0f0f0f' : 'var(--muted)';
    const ta = document.getElementById('dev-new-input');
    if (ta) ta.placeholder = t === 'spec' ? 'Paste or write your technical spec…' : 'Describe what you want to build…';
  }
}

async function devLoopSubmit() {
  const title      = (document.getElementById('dev-new-title')?.value || '').trim();
  const slug       = (document.getElementById('dev-new-slug')?.value || '').trim().toLowerCase();
  const input_text = (document.getElementById('dev-new-input')?.value || '').trim();
  if (!title || !input_text) { alert('Title and input are required.'); return; }
  const body = { title, input_text, input_type: _devNewType };
  if (slug) body.slug = slug;
  try {
    const result = await api('/api/dev-loop/projects', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    _devProjects.unshift(result);
    _devView = 'list';
    const el = document.getElementById('msec-inner-dev');
    if (el) _devRenderList(el);
    alert(`Loop started: ${result.slug || title}`);
  } catch(e) {
    alert('Failed to start loop: ' + e.message);
  }
}

async function devLoopKeepGoing(slug) {
  try {
    await api(`/api/dev-loop/projects/${encodeURIComponent(slug)}/keep-going`, { method: 'POST' });
    await devLoopLoad();
    alert('Loop extended — Keep Going sent to FMSPC.');
  } catch(e) {
    alert('Failed: ' + e.message);
  }
}

async function devLoopStop(slug) {
  if (!confirm(`Stop the loop for "${slug}"?`)) return;
  try {
    await api(`/api/dev-loop/projects/${encodeURIComponent(slug)}/stop`, { method: 'POST' });
    await devLoopLoad();
  } catch(e) {
    alert('Failed: ' + e.message);
  }
}

async function devLoopRetrigger(slug) {
  const feedback = (document.getElementById('dev-feedback-ta')?.value || '').trim();
  if (!feedback && !confirm('Re-trigger with no feedback?')) return;
  try {
    await api(`/api/dev-loop/projects/${encodeURIComponent(slug)}/retrigger`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback }),
    });
    await devLoopLoad();
    alert('Re-trigger sent to FMSPC.');
  } catch(e) {
    alert('Failed: ' + e.message);
  }
}
