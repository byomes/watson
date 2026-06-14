/* watson.js — Dashboard UI  */

// ─── State ────────────────────────────────────────────────────────────────────
let activePage = 'home';

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

function fmtGenerated(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return iso; }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function priClass(p) {
  if (!p) return 'pri-normal';
  return `pri-${p}`;
}

function priLabel(p) {
  return p ? p.charAt(0).toUpperCase() + p.slice(1) : 'Normal';
}

// ─── Navigation ───────────────────────────────────────────────────────────────

function switchTab(page) {
  activePage = page;

  document.querySelectorAll('.nb').forEach(b => b.classList.remove('active'));
  const navBtn = document.getElementById('nav-' + page);
  if (navBtn) navBtn.classList.add('active');

  const pc     = document.getElementById('page-content');
  const addBar = document.getElementById('add-task-bar');

  if (page === 'tasks') {
    pc.classList.add('has-add-bar');
    addBar.classList.add('visible');
  } else {
    pc.classList.remove('has-add-bar');
    addBar.classList.remove('visible');
  }

  switch (page) {
    case 'home':      renderHome();      break;
    case 'briefing':  renderBriefing();  break;
    case 'tasks':     renderTasks();     break;
    case 'reminders': renderReminders(); break;
    case 'reading':   renderReading();   break;
  }
}

// ─── Home page ────────────────────────────────────────────────────────────────

async function renderHome() {
  setContent('<div class="loading">Loading&hellip;</div>');

  const [pendingRes, calRes, tasksRes, remindersRes, briefingRes] = await Promise.allSettled([
    api('/api/pending'),
    api('/api/calendar/today'),
    api('/api/tasks'),
    api('/api/reminders'),
    api('/api/briefing'),
  ]);

  const pending   = pendingRes.status   === 'fulfilled' ? pendingRes.value   : [];
  const calEvents = calRes.status       === 'fulfilled' ? calRes.value       : [];
  const tasks     = tasksRes.status     === 'fulfilled' ? tasksRes.value     : [];
  const reminders = remindersRes.status === 'fulfilled' ? remindersRes.value : [];
  const briefing  = briefingRes.status  === 'fulfilled' ? briefingRes.value  : [];

  const activeTasks = Array.isArray(tasks) ? tasks.filter(t => t.status !== 'done') : [];
  const topTasks    = activeTasks.slice(0, 3);

  let html = '';

  // 1. Awaiting You (hidden if empty)
  if (Array.isArray(pending) && pending.length) {
    html += `<div class="sec-label">Awaiting You</div>`;
    pending.forEach(item => {
      const type = (item.type || 'NOTE').toUpperCase();
      const bc   = `badge-${type === 'EMAIL' ? 'EMAIL' : type === 'CALENDAR' ? 'CALENDAR' : 'NOTE'}`;
      html += `
        <div class="card">
          <div class="card-title">${esc(item.title || item.subject || 'Pending item')}</div>
          ${item.subtitle ? `<div class="card-sub">${esc(item.subtitle)}</div>` : ''}
          <span class="badge ${bc}">${esc(type)}</span>
        </div>`;
    });
  }

  // 2. Today's Agenda
  html += `<div class="sec-label">Today's Agenda</div>`;
  const evArr = Array.isArray(calEvents) ? calEvents : (calEvents && !calEvents.error ? [calEvents] : []);
  if (!evArr.length) {
    html += `<div class="empty">No appointments today.</div>`;
  } else {
    html += `<div class="cal-card">`;
    evArr.forEach(ev => {
      html += `
        <div class="cal-row">
          <span class="cal-time">${esc(fmtTime(ev.start))}</span>
          <span class="cal-event">${esc(ev.summary || '(No title)')}</span>
        </div>`;
    });
    html += `</div>`;
  }

  // 3. At a Glance
  html += `
    <div class="sec-label">At a Glance</div>
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-num">${activeTasks.length}</div>
        <div class="stat-lbl">Tasks</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">${Array.isArray(reminders) ? reminders.length : 0}</div>
        <div class="stat-lbl">Reminders</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">${Array.isArray(briefing) ? briefing.length : 0}</div>
        <div class="stat-lbl">Briefing</div>
      </div>
    </div>`;

  // 4. Top Tasks
  html += `<div class="sec-label">Top Tasks</div>`;
  if (!topTasks.length) {
    html += `<div class="empty">No open tasks.</div>`;
  } else {
    topTasks.forEach(t => {
      html += `
        <div class="task-card">
          <div class="task-check display-only"></div>
          <div class="task-body">
            <div class="task-title">${esc(t.title)}</div>
            <span class="pri ${priClass(t.priority)}">${priLabel(t.priority)}</span>
          </div>
        </div>`;
    });
    html += `<a class="view-all" onclick="switchTab('tasks')">View all tasks &rsaquo;</a>`;
  }

  setContent(html);
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
      const url = JSON.stringify(item.url || '#');
      html += `
        <div class="b-card" id="b-card-${item.id}">
          <div class="b-source">${esc(item.source_name || '')}</div>
          <div class="b-title">${esc(item.title)}</div>
          <div class="b-actions">
            <button class="b-btn b-btn-read" onclick="window.open(${url},'_blank')">Read</button>
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

// ─── Tasks page ───────────────────────────────────────────────────────────────

async function renderTasks() {
  setContent('<div class="loading">Loading&hellip;</div>');
  try {
    const tasks = await api('/api/tasks');
    renderTaskList(tasks);
  } catch {
    setContent('<div class="empty">Could not load tasks.</div>');
  }
}

function renderTaskList(tasks) {
  if (!Array.isArray(tasks) || !tasks.length) {
    setContent('<div class="empty">No tasks yet.</div>');
    return;
  }
  let html = '';
  tasks.forEach(t => {
    const done = t.status === 'done';
    html += `
      <div class="task-card" id="task-row-${t.id}">
        <div class="task-check${done ? ' is-done' : ''}" onclick="completeTask(${t.id},this)"></div>
        <div class="task-body">
          <div class="task-title${done ? ' struck' : ''}">${esc(t.title)}</div>
          <span class="pri ${priClass(t.priority)}">${priLabel(t.priority)}</span>
        </div>
      </div>`;
  });
  setContent(html);
}

async function completeTask(id, checkEl) {
  if (!checkEl || checkEl.classList.contains('is-done')) return;
  checkEl.classList.add('is-done');
  const titleEl = checkEl.closest('.task-card')?.querySelector('.task-title');
  if (titleEl) titleEl.classList.add('struck');
  try {
    await api(`/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'done' }),
    });
  } catch {
    checkEl.classList.remove('is-done');
    if (titleEl) titleEl.classList.remove('struck');
  }
}

async function addTask() {
  const input = document.getElementById('add-task-input');
  const title = (input.value || '').trim();
  if (!title) { input.focus(); return; }

  const btn = document.getElementById('add-task-btn');
  btn.textContent = '…';
  btn.disabled = true;

  try {
    await api('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, priority: 'normal' }),
    });
    input.value = '';
    await renderTasks();
  } catch {
    alert('Failed to add task.');
  } finally {
    btn.textContent = 'Submit';
    btn.disabled = false;
  }
}

document.addEventListener('keydown', e => {
  if (activePage === 'tasks' && e.key === 'Enter' && document.activeElement.id === 'add-task-input') {
    addTask();
  }
});

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
        <div class="task-card">
          <div class="task-check display-only"></div>
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

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const dateEl = document.getElementById('hdr-date');
  if (dateEl) {
    dateEl.textContent = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  }
  const wMark = document.getElementById('hdr-mark');
  if (wMark) wMark.addEventListener('click', () => location.reload(true));
  switchTab('home');
});
