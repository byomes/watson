/* watson.js — Dashboard UI  */

// ─── State ────────────────────────────────────────────────────────────────────
let activePage = 'home';
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
    case 'more':      renderMore();      break;
    case 'chat':      renderChat();      break;
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
      <div class="stat-card" style="cursor:pointer" onclick="switchTab('tasks')">
        <div class="stat-num">${activeTasks.length}</div>
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
    await renderTasks();
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

const _MORE_REPORT_CONFIGS = [
  { key: 'weekly_summary',    label: 'Weekly Summary',     desc: 'Attendance & activity for the week' },
  { key: 'shepherding',       label: 'Shepherding',        desc: 'Flock contact status' },
  { key: 'new_members',       label: 'New Members',        desc: 'Recent additions to congregation' },
  { key: 'birthday_upcoming', label: 'Upcoming Birthdays', desc: 'Next 30-day birthday list' },
];

let _moreSecLoaded    = {};
let _moreShepData     = null;
let _moreAuditData    = null;
let _moreReportResult = null;
let _moreActiveReport = null;
let _moreReportWeeks  = 4;
let _morePNTab        = 'active';
let _moreAllSkills    = [];
let _moreSkillCat     = 'All';
let _moreSkillQuery   = '';

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
    <div class="msec" id="msec-reports">
      <div class="msec-hdr" onclick="moreToggle('reports')">
        <span class="msec-title">Reports</span>
        <span class="msec-chev" id="msec-chev-reports">›</span>
      </div>
      <div class="msec-body" id="msec-body-reports">
        <div class="msec-inner" id="msec-inner-reports"></div>
      </div>
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
    <div class="mrrow" onclick="openLogins()" style="cursor:pointer">
      <span style="font-size:13px;font-weight:500">Logins</span>
      <span style="color:var(--gold);font-size:15px">›</span>
    </div>`);
}

function moreToggle(sec) {
  const body = document.getElementById(`msec-body-${sec}`);
  const chev = document.getElementById(`msec-chev-${sec}`);
  if (!body) return;
  const isOpen = body.classList.toggle('open');
  if (chev) chev.textContent = isOpen ? '⌄' : '›';
  if (isOpen && !_moreSecLoaded[sec]) {
    _moreSecLoaded[sec] = true;
    if (sec === 'reports')  moreLoadReports();
    if (sec === 'skills')   moreLoadSkills();
    if (sec === 'ministry') moreLoadMinistry();
    if (sec === 'reading')  moreLoadReading();
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

// ── Reports (bottom sheet) ───────────────────────────────────────────────────

function moreShowReportSheet() {
  const existing = document.getElementById('bsoverlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.className = 'bsoverlay';
  overlay.id = 'bsoverlay';
  overlay.innerHTML = `
    <div class="bsheet">
      <div class="bsheet-title">Run Report</div>
      <div class="mlabel">Report type</div>
      <div class="bsheet-presets">
        ${_MORE_REPORT_CONFIGS.map(r =>
          `<button class="bpreset${_moreActiveReport === r.key ? ' sel' : ''}"
            onclick="morePickReport('${r.key}',this)">${esc(r.label)}</button>`).join('')}
      </div>
      <div class="mlabel">Weeks to include</div>
      <div class="bsheet-presets">
        ${[2, 4, 8, 12].map(w =>
          `<button class="bpreset${_moreReportWeeks === w ? ' sel' : ''}"
            onclick="morePickReportWeek(${w},this)">${w}w</button>`).join('')}
      </div>
      <div class="mfrow" style="margin-top:14px">
        <button class="mbtn mbtn-p" onclick="moreRunReport()">Run</button>
        <button class="mbtn" onclick="document.getElementById('bsoverlay').remove()">Cancel</button>
      </div>
    </div>`;
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

function morePickReport(key, btn) {
  _moreActiveReport = key;
  btn.closest('.bsheet-presets').querySelectorAll('.bpreset').forEach(b => b.classList.remove('sel'));
  btn.classList.add('sel');
}

function morePickReportWeek(w, btn) {
  _moreReportWeeks = w;
  btn.closest('.bsheet-presets').querySelectorAll('.bpreset').forEach(b => b.classList.remove('sel'));
  btn.classList.add('sel');
}

async function moreRunReport() {
  if (!_moreActiveReport) { alert('Select a report type.'); return; }
  const overlay = document.getElementById('bsoverlay');
  if (overlay) overlay.remove();
  const resultEl = document.getElementById('more-report-result');
  if (resultEl) resultEl.innerHTML = '<div class="loading">Running&hellip;</div>';
  try {
    const data = await api(`/api/reports/run?type=${encodeURIComponent(_moreActiveReport)}&weeks=${_moreReportWeeks}`);
    _moreReportResult = data.content || data.result || JSON.stringify(data, null, 2);
    if (resultEl) resultEl.innerHTML = `
      <div class="mreport-result">${esc(_moreReportResult)}</div>
      <div class="mfrow" style="margin-top:8px">
        <button class="mbtn mbtn-sm" onclick="moreReportTelegram()">Telegram</button>
        <button class="mbtn mbtn-sm" onclick="moreReportEmail()">Email</button>
      </div>`;
  } catch {
    if (resultEl) resultEl.innerHTML = '<div class="empty">Report failed.</div>';
  }
}

async function moreReportTelegram() {
  if (!_moreReportResult) return;
  try {
    await api('/api/reports/telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: _moreActiveReport, weeks: _moreReportWeeks, content: _moreReportResult }),
    });
    alert('Sent to Telegram!');
  } catch { alert('Failed to send.'); }
}

async function moreReportEmail() {
  if (!_moreReportResult) return;
  try {
    await api('/api/reports/email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: _moreActiveReport, weeks: _moreReportWeeks, content: _moreReportResult }),
    });
    alert('Sent via email!');
  } catch { alert('Failed to send.'); }
}

// ── Reports ──────────────────────────────────────────────────────────────────

function moreLoadReports() {
  const el = document.getElementById('msec-inner-reports');
  if (!el) return;
  el.innerHTML = `
    <button class="mbtn" onclick="moreShowReportSheet()">Run a Report&hellip;</button>
    <div id="more-report-result"></div>`;
}

// ── Skills ───────────────────────────────────────────────────────────────────

async function moreLoadSkills() {
  const el = document.getElementById('msec-inner-skills');
  if (!el) return;
  _moreSkillCat   = 'All';
  _moreSkillQuery = '';
  el.innerHTML = `
    <input class="msrch" type="search" placeholder="Search skills&hellip;" oninput="moreSkillSearch(this.value)">
    <div style="font-size:11px;color:var(--muted);text-align:center;margin-bottom:8px">Trigger any skill by messaging Watson on Telegram.</div>
    <div id="more-skill-pills" class="mpills"></div>
    <div id="more-skill-list"><div class="loading">Loading&hellip;</div></div>`;
  try {
    const skills = await api('/api/skills');
    _moreAllSkills = Array.isArray(skills) ? skills : [];
    const cats = [...new Set(_moreAllSkills.map(s => s.category || 'General'))];
    moreRenderSkillPills(['All', ...cats]);
    moreRenderSkills(_moreAllSkills);
  } catch {
    const listEl = document.getElementById('more-skill-list');
    if (listEl) listEl.innerHTML = '<div class="empty">Could not load skills.</div>';
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
  let skills = _moreAllSkills;
  if (_moreSkillCat && _moreSkillCat !== 'All') {
    skills = skills.filter(s => (s.category || 'General') === _moreSkillCat);
  }
  if (_moreSkillQuery) {
    skills = skills.filter(s =>
      (s.name || '').toLowerCase().includes(_moreSkillQuery) ||
      (s.description || '').toLowerCase().includes(_moreSkillQuery)
    );
  }
  moreRenderSkills(skills);
}

function moreRenderSkills(skills) {
  const el = document.getElementById('more-skill-list');
  if (!el) return;
  if (!skills.length) {
    el.innerHTML = '<div class="empty">No skills found.</div>';
    return;
  }
  el.innerHTML = skills.map(s => {
    const triggers = Array.isArray(s.triggers) ? s.triggers : [];
    const triggerHtml = triggers.length ? `
      <div style="margin-top:6px">
        <div style="font-size:10px;color:var(--muted);letter-spacing:.04em;margin-bottom:4px">TRIGGER WITH:</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          ${triggers.map(t => {
            const url = 'https://t.me/wckyWatsonbot?text=Watson+' + encodeURIComponent(t);
            return `<button class="msk-trigger" onclick="window.open('${url}','_blank')">${esc(t)}</button>`;
          }).join('')}
        </div>
      </div>` : '';
    return `
    <div class="msk-card">
      <div class="msk-info">
        <div class="msk-name">${esc(s.name || s.slug || '')}</div>
        <div class="msk-desc">${esc(s.description || '')}</div>
        ${triggerHtml}
        <span class="msk-badge">${esc(s.status || 'ready')}</span>
      </div>
      ${s.status === 'pending' ? `<button class="mbtn mbtn-sm" onclick="moreApproveSkill('${esc(s.slug)}')">Approve</button>` : ''}
    </div>`;
  }).join('');
}

async function moreApproveSkill(slug) {
  try {
    await api(`/api/skills/${encodeURIComponent(slug)}/approve`, { method: 'POST' });
    moreLoadSkills();
  } catch { alert('Failed to approve skill.'); }
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
