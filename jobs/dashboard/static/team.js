/* team.js — Watson Team Management */
'use strict';

const TeamApp = (() => {
  let _members = [];
  let _allTasks = [];
  let _meetings = [];
  let _messages = [];
  let _currentMember = null;
  let _currentProfile = null;
  let _editMode = false;
  let _taskFilter = 'all';
  let _ministryFilter = 'all';
  let _importExtracted = null;
  let _dragStartIdx = null;
  let _dragInitialized = false;

  // ── API helpers ─────────────────────────────────────────────

  async function _get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async function _post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async function _put(url, body) {
    const r = await fetch(url, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async function _del(url) {
    const r = await fetch(url, {method: 'DELETE'});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  // ── Utilities ───────────────────────────────────────────────

  function _initials(name) {
    return (name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  }

  function _isOverdue(due) {
    if (!due) return false;
    return due < new Date().toISOString().slice(0, 10);
  }

  function _today() { return new Date().toISOString().slice(0, 10); }

  function _weekEnd() {
    const d = new Date();
    d.setDate(d.getDate() + (7 - d.getDay()));
    return d.toISOString().slice(0, 10);
  }

  function _monthEnd() {
    const d = new Date();
    d.setMonth(d.getMonth() + 1, 0);
    return d.toISOString().slice(0, 10);
  }

  function _esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Tab switching ────────────────────────────────────────────

  function switchTab(name, btn) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.bnav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    if (btn) btn.classList.add('active');

    if (name === 'tasks') loadTasks();
    if (name === 'notes') loadMeetings();
    if (name === 'comms') loadMessages();
  }

  // ── TEAM TAB ─────────────────────────────────────────────────

  async function loadMembers() {
    try {
      _members = await _get('/api/team/members');
      _renderMinistryChips();
      _renderMembers();
      // _initDrag(); // drag-to-reorder disabled
    } catch(e) {
      document.getElementById('members-list').innerHTML =
        '<div class="empty-state">Failed to load members</div>';
    }
  }

  function _renderMinistryChips() {
    const ministries = [...new Set(_members.map(m => m.ministry).filter(Boolean))];
    const row = document.getElementById('ministry-chips');
    row.innerHTML = '<div class="chip active" data-ministry="all" onclick="TeamApp.filterMinistry(\'all\',this)">All</div>';
    ministries.forEach(min => {
      const chip = document.createElement('div');
      chip.className = 'chip';
      chip.dataset.ministry = min;
      chip.textContent = min;
      chip.onclick = () => filterMinistry(min, chip);
      row.appendChild(chip);
    });
  }

  function filterMinistry(min, el) {
    _ministryFilter = min;
    document.querySelectorAll('#ministry-chips .chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    _renderMembers();
  }

  function _renderMembers() {
    const list = document.getElementById('members-list');
    const filtered = _ministryFilter === 'all'
      ? _members
      : _members.filter(m => m.ministry === _ministryFilter);

    if (!filtered.length) {
      list.innerHTML = '<div class="empty-state"><i class="ti ti-users"></i>No team members yet</div>';
      return;
    }

    list.innerHTML = filtered.map(m => {
      const taskCount = m.open_task_count || 0;
      return `
        <div class="card member-card" draggable="false" data-member-id="${m.id}" onclick="TeamApp.openProfile(${m.id})">
          <div class="avatar">${_initials(m.name)}</div>
          <div class="member-info">
            <div class="member-name">${_esc(m.name)}</div>
            <div class="member-meta">${_esc(m.role || '')}${m.role && m.ministry ? ' · ' : ''}${_esc(m.ministry || '')}</div>
            <div class="member-stats">
              <span class="stat-badge ${taskCount > 0 ? 'warn' : ''}">${taskCount} task${taskCount !== 1 ? 's' : ''}</span>
              ${m.last_meeting_date ? `<span class="stat-badge">Last met ${m.last_meeting_date}</span>` : ''}
            </div>
          </div>
        </div>`;
    }).join('');
  }

  function _getListOrder(list) {
    return [...list.querySelectorAll('.member-card')].map(c => parseInt(c.dataset.memberId)).filter(Boolean);
  }

  // _initDrag disabled — drag-to-reorder UI removed
  function _initDrag() {}

  // ── Add leader ───────────────────────────────────────────────

  let _searchTimer = null;
  function startAddLeader() {
    document.getElementById('add-leader-form').style.display = 'block';
    document.getElementById('leader-search-input').addEventListener('input', function() {
      clearTimeout(_searchTimer);
      _searchTimer = setTimeout(() => _doSearch(this.value), 300);
    });
  }

  async function _doSearch(q) {
    if (q.length < 2) {
      document.getElementById('search-results').style.display = 'none';
      return;
    }
    try {
      const results = await _get(`/api/team/search?q=${encodeURIComponent(q)}`);
      const el = document.getElementById('search-results');
      if (!results.length) { el.style.display = 'none'; return; }
      el.style.display = 'block';
      el.innerHTML = results.map(r => `
        <div class="search-result-item" onclick="TeamApp.prefillFromSearch(${JSON.stringify(r).replace(/"/g,'&quot;')})">
          <div>${_esc(r.name)}</div>
          <div class="sub">${_esc(r.email || '')} ${r.phone ? '· ' + _esc(r.phone) : ''}</div>
        </div>`).join('');
    } catch(e) { /* ignore */ }
  }

  function prefillFromSearch(result) {
    document.getElementById('new-name').value    = result.name  || '';
    document.getElementById('new-email').value   = result.email || '';
    document.getElementById('new-phone').value   = result.phone || '';
    document.getElementById('search-results').style.display = 'none';
    document.getElementById('new-name').focus();
  }

  async function saveNewLeader() {
    const name = document.getElementById('new-name').value.trim();
    if (!name) { alert('Name is required'); return; }
    try {
      await _post('/api/team/members', {
        name,
        email:    document.getElementById('new-email').value.trim(),
        phone:    document.getElementById('new-phone').value.trim(),
        role:     document.getElementById('new-role').value.trim(),
        ministry: document.getElementById('new-ministry').value.trim(),
        notes:    document.getElementById('new-notes').value.trim(),
      });
      cancelAdd();
      loadMembers();
    } catch(e) { alert('Error: ' + e.message); }
  }

  function cancelAdd() {
    document.getElementById('add-leader-form').style.display = 'none';
    document.getElementById('search-results').style.display  = 'none';
    document.getElementById('leader-search-input').value = '';
    ['new-name','new-email','new-phone','new-role','new-ministry','new-notes']
      .forEach(id => document.getElementById(id).value = '');
  }

  // ── Profile panel ────────────────────────────────────────────

  async function openProfile(memberId) {
    try {
      const data = await _get(`/api/team/members/${memberId}/profile`);
      _currentProfile = data;
      _currentMember  = data.member;
      _editMode = false;
      _renderProfile(data);
      document.getElementById('profile-panel').classList.add('open');
    } catch(e) { alert('Failed to load profile: ' + e.message); }
  }

  function closeProfile() {
    document.getElementById('profile-panel').classList.remove('open');
  }

  function _renderProfile(data) {
    const m = data.member;
    document.getElementById('profile-name').textContent = m.name;
    document.getElementById('profile-meta').textContent = [m.role, m.ministry].filter(Boolean).join(' · ');

    const body = document.getElementById('profile-body');
    body.innerHTML = `
      <div id="profile-view-fields">
        <div class="field-row">
          <label>Email</label>
          <div style="font-size:14px;padding:4px 0">${_esc(m.email || '—')}</div>
        </div>
        <div class="field-row">
          <label>Phone</label>
          <div style="font-size:14px;padding:4px 0">${_esc(m.phone || '—')}</div>
        </div>
        <div class="field-row">
          <label>Notes</label>
          <div style="font-size:14px;padding:4px 0;color:var(--muted)">${_esc(m.notes || '—')}</div>
        </div>
      </div>
      <div id="profile-edit-fields" style="display:none">
        <div class="field-row"><label>Name</label><input id="edit-name" type="text" value="${_esc(m.name)}"></div>
        <div class="field-row"><label>Email</label><input id="edit-email" type="email" value="${_esc(m.email||'')}"></div>
        <div class="field-row"><label>Phone</label><input id="edit-phone" type="tel" value="${_esc(m.phone||'')}"></div>
        <div class="field-row"><label>Role</label><input id="edit-role" type="text" value="${_esc(m.role||'')}"></div>
        <div class="field-row"><label>Ministry</label><input id="edit-ministry" type="text" value="${_esc(m.ministry||'')}"></div>
        <div class="field-row"><label>Notes</label><textarea id="edit-notes">${_esc(m.notes||'')}</textarea></div>
        <div class="btn-row">
          <button class="btn btn-gold" onclick="TeamApp.saveProfileEdit()">Save</button>
          <button class="btn btn-red" onclick="TeamApp.deleteMember()">Remove</button>
        </div>
      </div>

      <div class="section-hdr">
        <h3>Objectives</h3>
        <button class="add-btn" onclick="TeamApp.addObjective()">+ Add</button>
      </div>
      <div id="objectives-list">
        ${data.objectives.length ? data.objectives.map(o => `
          <div class="list-item">
            <span>${_esc(o.title)}</span>
            <button class="del-btn" onclick="TeamApp.deleteObjective(${o.id})">×</button>
          </div>`).join('') : '<div class="text-muted" style="font-size:13px;padding:6px 0">None yet</div>'}
      </div>
      <div id="add-objective-form" style="display:none">
        <div class="field-row"><input id="new-objective" type="text" placeholder="Objective title"></div>
        <div class="btn-row">
          <button class="btn btn-gold" onclick="TeamApp.saveObjective()">Add</button>
          <button class="btn btn-ghost" onclick="document.getElementById('add-objective-form').style.display='none'">Cancel</button>
        </div>
      </div>

      <div class="section-hdr">
        <h3>Goals</h3>
        <button class="add-btn" onclick="TeamApp.addGoal()">+ Add</button>
      </div>
      <div id="goals-list">
        ${data.goals.length ? data.goals.map(g => `
          <div class="list-item">
            <div>
              <div>${_esc(g.title)}</div>
              ${g.target_date ? `<div style="font-size:11px;color:var(--muted)">by ${g.target_date}</div>` : ''}
            </div>
            <button class="del-btn" onclick="TeamApp.deleteGoal(${g.id})">×</button>
          </div>`).join('') : '<div class="text-muted" style="font-size:13px;padding:6px 0">None yet</div>'}
      </div>
      <div id="add-goal-form" style="display:none">
        <div class="field-row"><input id="new-goal-title" type="text" placeholder="Goal title"></div>
        <div class="field-row"><label>Target date</label><input id="new-goal-date" type="date"></div>
        <div class="btn-row">
          <button class="btn btn-gold" onclick="TeamApp.saveGoal()">Add</button>
          <button class="btn btn-ghost" onclick="document.getElementById('add-goal-form').style.display='none'">Cancel</button>
        </div>
      </div>

      <div class="section-hdr"><h3>Open Tasks</h3></div>
      <div id="profile-tasks-list">
        ${data.tasks.length ? data.tasks.map(t => `
          <div class="task-item">
            <input type="checkbox" class="task-check" onchange="TeamApp.checkTask(${t.id},this)">
            <div class="task-content">
              <div class="task-title">${_esc(t.title)}</div>
              ${t.due_date ? `<div class="task-meta ${_isOverdue(t.due_date) ? 'overdue' : ''}">Due ${t.due_date}</div>` : ''}
            </div>
          </div>`).join('') : '<div class="text-muted" style="font-size:13px;padding:6px 0">No open tasks</div>'}
      </div>

      <div class="section-hdr"><h3>Recent Meetings</h3></div>
      <div>
        ${data.recent_meetings.length ? data.recent_meetings.map(mt => `
          <div class="meeting-item" onclick="TeamApp.openMeeting(${mt.id})">
            <div class="meeting-info">
              <div class="meeting-date">${mt.date}</div>
              <div class="meeting-excerpt">${_esc(mt.summary_excerpt || '')}</div>
            </div>
            <span class="email-badge ${mt.email_sent ? 'sent' : 'pending'}">${mt.email_sent ? 'Sent' : 'Pending'}</span>
          </div>`).join('') : '<div class="text-muted" style="font-size:13px;padding:6px 0">No meetings on file</div>'}
      </div>
    `;
  }

  function toggleProfileEdit() {
    _editMode = !_editMode;
    document.getElementById('profile-view-fields').style.display = _editMode ? 'none' : 'block';
    document.getElementById('profile-edit-fields').style.display = _editMode ? 'block' : 'none';
    document.getElementById('profile-edit-btn').textContent = _editMode ? 'Cancel' : 'Edit';
  }

  async function saveProfileEdit() {
    try {
      await _put(`/api/team/members/${_currentMember.id}`, {
        name:     document.getElementById('edit-name').value.trim(),
        email:    document.getElementById('edit-email').value.trim(),
        phone:    document.getElementById('edit-phone').value.trim(),
        role:     document.getElementById('edit-role').value.trim(),
        ministry: document.getElementById('edit-ministry').value.trim(),
        notes:    document.getElementById('edit-notes').value.trim(),
      });
      await openProfile(_currentMember.id);
    } catch(e) { alert('Error: ' + e.message); }
  }

  async function deleteMember() {
    if (!confirm('Remove this team member?')) return;
    try {
      await _del(`/api/team/members/${_currentMember.id}`);
      closeProfile();
      loadMembers();
    } catch(e) { alert('Error: ' + e.message); }
  }

  function addObjective() { document.getElementById('add-objective-form').style.display = 'block'; }
  function addGoal()      { document.getElementById('add-goal-form').style.display = 'block'; }

  async function saveObjective() {
    const title = document.getElementById('new-objective').value.trim();
    if (!title) return;
    try {
      await _post(`/api/team/members/${_currentMember.id}/objectives`, {title});
      await openProfile(_currentMember.id);
    } catch(e) { alert('Error: ' + e.message); }
  }

  async function deleteObjective(id) {
    if (!confirm('Delete this objective?')) return;
    try { await _del(`/api/team/objectives/${id}`); await openProfile(_currentMember.id); }
    catch(e) { alert('Error: ' + e.message); }
  }

  async function saveGoal() {
    const title = document.getElementById('new-goal-title').value.trim();
    if (!title) return;
    try {
      await _post(`/api/team/members/${_currentMember.id}/goals`, {
        title,
        target_date: document.getElementById('new-goal-date').value || null,
      });
      await openProfile(_currentMember.id);
    } catch(e) { alert('Error: ' + e.message); }
  }

  async function deleteGoal(id) {
    if (!confirm('Delete this goal?')) return;
    try { await _del(`/api/team/goals/${id}`); await openProfile(_currentMember.id); }
    catch(e) { alert('Error: ' + e.message); }
  }

  async function checkTask(taskId, el) {
    try {
      await _put(`/api/team/tasks/${taskId}`, {status: el.checked ? 'done' : 'open'});
      if (el.checked) {
        const item = el.closest('.task-item');
        if (item) { item.style.opacity = '0.4'; }
      }
    } catch(e) { alert('Error: ' + e.message); el.checked = !el.checked; }
  }

  // ── TASKS TAB ─────────────────────────────────────────────────

  async function loadTasks() {
    const list = document.getElementById('tasks-list');
    list.innerHTML = '<div class="loading-row"><div class="spinner"></div></div>';
    try {
      _allTasks = await _get('/api/team/tasks?status=all');
      _renderTasks();
    } catch(e) {
      list.innerHTML = '<div class="empty-state">Failed to load tasks</div>';
    }
  }

  function filterTasks(filter, el) {
    _taskFilter = filter;
    document.querySelectorAll('[data-task-filter]').forEach(c => c.classList.remove('active'));
    if (el) el.classList.add('active');
    _renderTasks();
  }

  function _renderTasks() {
    const today   = _today();
    const weekEnd = _weekEnd();
    const banner  = document.getElementById('overdue-banner');
    const overdue = _allTasks.filter(t => t.status === 'open' && t.due_date && t.due_date < today);

    banner.classList.toggle('show', overdue.length > 0);

    let tasks;
    switch(_taskFilter) {
      case 'mine':    tasks = _allTasks.filter(t => t.status !== 'done' && t.member_id === 12); break;
      case 'team':    tasks = _allTasks.filter(t => t.status !== 'done' && t.member_id !== 12); break;
      case 'overdue': tasks = _allTasks.filter(t => t.status === 'open' && t.due_date && t.due_date < today); break;
      case 'week':    tasks = _allTasks.filter(t => t.status === 'open' && t.due_date && t.due_date >= today && t.due_date <= weekEnd); break;
      case 'done':    tasks = _allTasks.filter(t => t.status === 'done'); break;
      case 'all':     tasks = _allTasks; break;
      default:        tasks = _allTasks.filter(t => t.status === 'open'); break;
    }

    const list = document.getElementById('tasks-list');
    if (!tasks.length) {
      list.innerHTML = '<div class="empty-state"><i class="ti ti-check"></i>No tasks</div>';
      return;
    }

    list.innerHTML = tasks.map(t => {
      const od = t.due_date && t.due_date < today;
      const isPersonal = t.member_id === 12 || t.source === 'personal';
      const memberBadge = isPersonal
        ? `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(102,102,102,.12);color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.03em">Personal</span>`
        : `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(201,168,76,.1);color:var(--gold);border:1px solid rgba(201,168,76,.2);font-family:'DM Mono',monospace;letter-spacing:.03em">${_esc(t.member_name||'')}${t.ministry?' · '+_esc(t.ministry):''}</span>`;
      return `
        <div class="card" style="cursor:default">
          <div class="task-item" style="padding:0;border:none">
            <input type="checkbox" class="task-check" ${t.status==='done'?'checked':''} onchange="TeamApp.checkTaskGlobal(${t.id},this)">
            <div class="task-content">
              <div class="task-title">${_esc(t.title)}</div>
              <div class="task-meta" style="margin-top:4px;display:flex;flex-wrap:wrap;align-items:center;gap:6px">
                ${memberBadge}
                ${t.due_date ? `<span class="${od?'overdue':''}">Due ${t.due_date}</span>` : ''}
                ${t.status==='done' ? `<button onclick="TeamApp.deleteTask(${t.id})" style="margin-left:auto;background:none;border:none;color:var(--red);cursor:pointer;font-size:12px;padding:2px 6px;border-radius:4px;border:1px solid rgba(201,80,76,.3)">Delete</button>` : ''}
              </div>
            </div>
          </div>
        </div>`;
    }).join('');
  }

  async function deleteTask(taskId) {
    try {
      await _del('/api/team/tasks/' + taskId);
      _allTasks = _allTasks.filter(t => t.id !== taskId);
      _renderTasks();
    } catch(e) { alert('Error: ' + e.message); }
  }
  async function checkTaskGlobal(taskId, el) {
    try {
      await _put(`/api/team/tasks/${taskId}`, {status: el.checked ? 'done' : 'open'});
      const task = _allTasks.find(t => t.id === taskId);
      if (task) task.status = el.checked ? 'done' : 'open';
      if (el.checked) filterTasks('done', document.querySelector('[data-task-filter=done]'));
      else _renderTasks();
    } catch(e) { alert('Error: ' + e.message); el.checked = !el.checked; }
  }

  function showAddTaskModal() {
    const sel = document.getElementById('add-task-member');
    sel.innerHTML = _members.map(m => `<option value="${m.id}">${_esc(m.name)}</option>`).join('');
    document.getElementById('add-task-modal').style.display = 'block';
  }

  function closeAddTask() { document.getElementById('add-task-modal').style.display = 'none'; }

  async function saveNewTask() {
    const title = document.getElementById('add-task-title').value.trim();
    if (!title) { alert('Task title required'); return; }
    const memberId = document.getElementById('add-task-member').value;
    try {
      await _post(`/api/team/members/${memberId}/tasks`, {
        title,
        due_date: document.getElementById('add-task-due').value || null,
      });
      closeAddTask();
      document.getElementById('add-task-title').value = '';
      document.getElementById('add-task-due').value   = '';
      loadTasks();
    } catch(e) { alert('Error: ' + e.message); }
  }

  // ── NOTES TAB ─────────────────────────────────────────────────

  async function loadMeetings() {
    const list = document.getElementById('meetings-list');
    list.innerHTML = '<div class="loading-row"><div class="spinner"></div></div>';
    try {
      const meetings = [];
      for (const m of _members) {
        const ms = await _get(`/api/team/members/${m.id}/meetings`);
        ms.forEach(mt => { mt._member_name = m.name; meetings.push(mt); });
      }
      meetings.sort((a, b) => b.date.localeCompare(a.date));
      _meetings = meetings;
      _renderMeetings();
    } catch(e) {
      list.innerHTML = '<div class="empty-state">Failed to load meetings</div>';
    }
  }

  function _renderMeetings() {
    const list = document.getElementById('meetings-list');
    if (!_meetings.length) {
      list.innerHTML = '<div class="empty-state"><i class="ti ti-file-text"></i>No meetings yet</div>';
      return;
    }
    list.innerHTML = _meetings.map(mt => `
      <div class="meeting-item" onclick="TeamApp.openMeeting(${mt.id})">
        <div class="avatar">${_initials(mt._member_name)}</div>
        <div class="meeting-info">
          <div style="font-size:13px;font-weight:600">${_esc(mt._member_name)} · ${mt.date}</div>
          <div class="meeting-excerpt">${_esc(mt.summary_excerpt || '')}</div>
        </div>
        <span class="email-badge ${mt.email_sent ? 'sent' : 'pending'}">${mt.email_sent ? 'Email Sent' : 'Email Pending'}</span>
      </div>`).join('');
  }

  async function openMeeting(meetingId) {
    try {
      const mt = await _get(`/api/team/meetings/${meetingId}`);
      const panel = document.getElementById('meeting-panel');
      const title = document.getElementById('meeting-panel-title');
      const body  = document.getElementById('meeting-panel-body');
      title.textContent = `Meeting — ${mt.date}`;
      body.innerHTML = `
        <div class="field-row">
          <label>Summary</label>
          <div style="font-size:14px;line-height:1.6;padding:4px 0">${_esc(mt.summary || '(none)')}</div>
        </div>
        <div class="divider"></div>
        <div class="field-row">
          <label>Email Draft</label>
          ${mt.email_sent
            ? `<div style="font-size:13px;color:var(--muted);white-space:pre-wrap">${_esc(mt.email_draft || '(none)')}</div>`
            : `<textarea id="mt-email-draft" style="min-height:160px">${_esc(mt.email_draft || '')}</textarea>`
          }
        </div>
        ${!mt.email_sent ? `
          <div class="btn-row">
            <button class="btn btn-gold" onclick="TeamApp.sendMeetingEmail(${mt.id}, ${mt.member_id})">Send Email</button>
          </div>` : `
          <div style="font-size:12px;color:var(--green);margin-top:8px">✓ Email sent</div>`
        }
        ${mt.transcript ? `
          <div class="divider"></div>
          <div class="field-row">
            <label>Transcript</label>
            <div style="font-size:13px;color:var(--muted);max-height:200px;overflow:auto;white-space:pre-wrap">${_esc(mt.transcript.slice(0,2000))}${mt.transcript.length>2000?'…':''}</div>
          </div>` : ''}
      `;
      panel.classList.add('open');
    } catch(e) { alert('Failed to load meeting: ' + e.message); }
  }

  function closeMeeting() {
    document.getElementById('meeting-panel').classList.remove('open');
  }

  async function sendMeetingEmail(meetingId, memberId) {
    const draft = document.getElementById('mt-email-draft');
    const body  = draft ? draft.value.trim() : '';
    try {
      await _put(`/api/team/meetings/${meetingId}/send-email`, {});
      if (body) {
        await _post('/api/team/messages/send', {
          member_id: memberId,
          subject: 'Follow-up from Watson',
          body,
          meeting_id: meetingId,
        });
      }
      closeMeeting();
      loadMeetings();
    } catch(e) { alert('Error sending email: ' + e.message); }
  }

  // ── TRANSCRIPT IMPORT ─────────────────────────────────────────

  function openImport() {
    const sel = document.getElementById('import-member-select');
    sel.innerHTML = '<option value="">— choose —</option>' +
      _members.map(m => `<option value="${m.id}">${_esc(m.name)}</option>`).join('');
    document.getElementById('import-date').value = _today();
    document.getElementById('import-panel').classList.add('open');
    _showImportStep(1);
  }

  function closeImport() {
    document.getElementById('import-panel').classList.remove('open');
    _importExtracted = null;
  }

  function _showImportStep(n) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.getElementById('import-step-' + n).classList.add('active');
  }

  function importNext(n) { _showImportStep(n); }

  async function processTranscript() {
    const memberId   = document.getElementById('import-member-select').value;
    const transcript = document.getElementById('import-transcript').value.trim();
    const date       = document.getElementById('import-date').value;
    if (!memberId) { alert('Please select a leader'); return; }
    if (!transcript) { alert('Please paste a transcript'); return; }

    _showImportStep(3);
    try {
      const result = await _post('/api/team/process-transcript', {
        member_id: parseInt(memberId),
        transcript,
        date,
      });
      if (result.error) throw new Error(result.error);
      _importExtracted = result;
      _populateReview(result);
      _showImportStep(4);
    } catch(e) {
      alert('Extraction failed: ' + e.message);
      _showImportStep(2);
    }
  }

  function _populateReview(data) {
    document.getElementById('review-summary').value       = data.summary || '';
    document.getElementById('review-email-subject').value = data.email_subject || 'Follow-up from our meeting';
    document.getElementById('review-email-draft').value   = data.email_draft || '';

    const tasksEl = document.getElementById('review-tasks');
    tasksEl.innerHTML = (data.tasks || []).map((t, i) => `
      <div class="field-row" style="display:flex;gap:8px;align-items:center">
        <input type="text" value="${_esc(t.title)}" style="flex:1" id="rt-title-${i}">
        <input type="date" value="${t.due_date || ''}" style="width:130px" id="rt-date-${i}">
      </div>`).join('');

    const goalsEl = document.getElementById('review-goals');
    goalsEl.innerHTML = (data.goals || []).map((g, i) => `
      <div class="field-row" style="display:flex;gap:8px;align-items:center">
        <input type="text" value="${_esc(g.title)}" style="flex:1" id="rg-title-${i}">
        <input type="date" value="${g.target_date || ''}" style="width:130px" id="rg-date-${i}">
      </div>`).join('');
  }

  async function saveImport() {
    const memberId = parseInt(document.getElementById('import-member-select').value);
    const date     = document.getElementById('import-date').value;
    const summary  = document.getElementById('review-summary').value.trim();
    const emailDraft = document.getElementById('review-email-draft').value.trim();
    const subject  = document.getElementById('review-email-subject').value.trim();

    // Collect tasks
    const taskCount = (_importExtracted.tasks || []).length;
    const tasks = [];
    for (let i = 0; i < taskCount; i++) {
      const t = document.getElementById('rt-title-' + i);
      if (t && t.value.trim()) {
        tasks.push({ title: t.value.trim(), due_date: document.getElementById('rt-date-' + i).value || null });
      }
    }

    // Collect goals
    const goalCount = (_importExtracted.goals || []).length;
    const goals = [];
    for (let i = 0; i < goalCount; i++) {
      const g = document.getElementById('rg-title-' + i);
      if (g && g.value.trim()) {
        goals.push({ title: g.value.trim(), target_date: document.getElementById('rg-date-' + i).value || null });
      }
    }

    try {
      // Save meeting
      const mt = await _post(`/api/team/members/${memberId}/meetings`, {
        date, summary, email_draft: emailDraft, transcript: document.getElementById('import-transcript').value,
      });

      // Save tasks
      for (const t of tasks) {
        await _post(`/api/team/members/${memberId}/tasks`, {
          title: t.title, due_date: t.due_date, source: 'transcript', meeting_id: mt.id,
        });
      }

      // Save goals
      for (const g of goals) {
        await _post(`/api/team/members/${memberId}/goals`, {
          title: g.title, target_date: g.target_date,
        });
      }

      closeImport();
      switchTab('notes', document.querySelector('.bnav-btn:nth-child(3)'));
      await loadMeetings();
    } catch(e) { alert('Error saving: ' + e.message); }
  }

  // ── COMMS TAB ──────────────────────────────────────────────────

  async function loadMessages() {
    const list = document.getElementById('messages-list');
    list.innerHTML = '<div class="loading-row"><div class="spinner"></div></div>';
    try {
      _messages = await _get('/api/team/messages');
      _renderMessages();
      _updateCommStats();
    } catch(e) {
      list.innerHTML = '<div class="empty-state">Failed to load messages</div>';
    }
  }

  function _updateCommStats() {
    const weekAgo = new Date(); weekAgo.setDate(weekAgo.getDate() - 7);
    const waStr   = weekAgo.toISOString().slice(0, 10);
    const sent    = _messages.filter(m => m.sent_at && m.sent_at >= waStr).length;
    const waiting = _messages.filter(m => !m.replied_at).length;
    document.getElementById('stat-sent').textContent     = sent;
    document.getElementById('stat-awaiting').textContent = waiting;
  }

  const _TONE_COLORS = {
    urgent:        '#c9504c',
    concern:       '#c9a84c',
    request:       '#4c7ec9',
    update:        '#4caf7d',
    informational: '#666',
  };
  const _TONE_BG = {
    urgent:        'rgba(201,80,76,.15)',
    concern:       'rgba(201,168,76,.1)',
    request:       'rgba(76,126,201,.15)',
    update:        'rgba(76,175,125,.15)',
    informational: 'rgba(102,102,102,.15)',
  };

  function _renderMessages() {
    const list = document.getElementById('messages-list');
    if (!_messages.length) {
      list.innerHTML = '<div class="empty-state"><i class="ti ti-mail"></i>No messages yet</div>';
      return;
    }
    list.innerHTML = _messages.map(msg => {
      const isIn  = msg.direction === 'in';
      const date  = (msg.sent_at || msg.created_at || '').slice(0, 10);
      const label = isIn ? `From ${_esc(msg.member_name)}` : _esc(msg.member_name);
      const preview = _esc((msg.subject || '') + (msg.body ? ' — ' + msg.body.slice(0, 60) : ''));

      let toneBadge = '';
      if (isIn && msg.tone) {
        const tone = msg.tone.toLowerCase();
        const col  = _TONE_COLORS[tone] || '#666';
        const bg   = _TONE_BG[tone]     || 'rgba(102,102,102,.15)';
        toneBadge  = `<span style="font-size:10px;padding:2px 7px;border-radius:10px;background:${bg};color:${col};margin-left:4px">${_esc(msg.tone)}</span>`;
      }

      const unreadDot = (!msg.replied_at && isIn)
        ? '<div class="msg-unread" style="margin-top:4px;margin-left:auto"></div>' : '';

      return `
        <div class="msg-item" onclick="TeamApp.showMessage(${JSON.stringify(msg).replace(/"/g,'&quot;')})">
          <div class="avatar" style="width:38px;height:38px;font-size:14px;${isIn ? 'background:#1a3a1a;' : ''}">${_initials(msg.member_name)}</div>
          <div class="msg-info">
            <div class="msg-name">${label}${toneBadge}</div>
            <div class="msg-preview">${preview}</div>
          </div>
          <div>
            <div class="msg-date">${date}</div>
            ${unreadDot}
          </div>
        </div>`;
    }).join('');
  }

  function showMessage(msg) {
    const panel = document.getElementById('meeting-panel');
    const title = document.getElementById('meeting-panel-title');
    const body  = document.getElementById('meeting-panel-body');
    const isIn  = msg.direction === 'in';

    title.textContent = isIn ? `From ${msg.member_name}` : `To ${msg.member_name}`;

    let html = '';
    if (isIn) {
      const tone     = (msg.tone || 'informational').toLowerCase();
      const toneCol  = _TONE_COLORS[tone] || '#666';
      const toneBg   = _TONE_BG[tone]     || 'rgba(102,102,102,.15)';
      html = `
        <div class="field-row"><label>From</label><div style="font-size:14px">${_esc(msg.member_name)} · ${_esc(msg.ministry || '')}</div></div>
        <div class="field-row"><label>Subject</label><div style="font-size:14px">${_esc(msg.subject || '')}</div></div>
        <div class="field-row"><label>Received</label><div style="font-size:14px">${_esc(msg.sent_at || '')}</div></div>
        ${msg.tone ? `<div style="margin-bottom:10px"><span style="font-size:12px;padding:3px 10px;border-radius:10px;background:${toneBg};color:${toneCol}">${_esc(msg.tone)}</span></div>` : ''}
        <div class="divider"></div>
        <div style="font-size:14px;line-height:1.7;white-space:pre-wrap">${_esc(msg.body || '')}</div>
      `;
    } else {
      html = `
        <div class="field-row"><label>To</label><div style="font-size:14px">${_esc(msg.member_name)}</div></div>
        <div class="field-row"><label>Subject</label><div style="font-size:14px">${_esc(msg.subject || '')}</div></div>
        <div class="field-row"><label>Sent</label><div style="font-size:14px">${_esc(msg.sent_at || '')}</div></div>
        <div class="divider"></div>
        <div style="font-size:14px;line-height:1.7;white-space:pre-wrap">${_esc(msg.body || '')}</div>
        <div class="divider"></div>
        <div style="font-size:12px;color:var(--muted)">${msg.replied_at ? '✓ Replied ' + msg.replied_at.slice(0,10) : 'No reply yet'}</div>
      `;
    }

    html += '<div style="margin-top:20px;padding-top:12px;border-top:1px solid var(--border)"><button onclick="TeamApp.deleteMessage(' + msg.id + ')" style="background:none;border:1px solid rgba(201,80,76,.3);color:var(--red);padding:6px 16px;border-radius:6px;cursor:pointer;font-size:13px">Delete</button></div>';
    body.innerHTML = html;
    panel.classList.add('open');
  }

  async function deleteMessage(msgId) {
    try {
      await _del('/api/team/messages/' + msgId);
      document.getElementById('meeting-panel').classList.remove('open');
      await loadMessages();
    } catch(e) { alert('Error: ' + e.message); }
  }
  async function deleteMessage(msgId) {
    if (!confirm('Delete this message?')) return;
    try {
      await _del('/api/team/messages/' + msgId);
      document.getElementById('meeting-panel').classList.remove('open');
      await loadMessages();
    } catch(e) { alert('Error: ' + e.message); }
  }
  function showCompose() {
    const sel = document.getElementById('compose-member');
    sel.innerHTML = _members.map(m => `<option value="${m.id}">${_esc(m.name)}</option>`).join('');
    document.getElementById('compose-modal').style.display = 'block';
  }

  function closeCompose() {
    document.getElementById('compose-modal').style.display = 'none';
  }

  async function sendCompose() {
    const memberId = parseInt(document.getElementById('compose-member').value);
    const subject  = document.getElementById('compose-subject').value.trim();
    const body     = document.getElementById('compose-body').value.trim();
    if (!subject || !body) { alert('Subject and message required'); return; }
    try {
      await _post('/api/team/messages/send', {member_id: memberId, subject, body});
      closeCompose();
      document.getElementById('compose-subject').value = '';
      document.getElementById('compose-body').value    = '';
      loadMessages();
    } catch(e) { alert('Error: ' + e.message); }
  }

  // ── Init ──────────────────────────────────────────────────────

  async function init() {
    await loadMembers();
    // Populate member dropdowns for add-task modal and compose
    const sel1 = document.getElementById('add-task-member');
    const sel2 = document.getElementById('compose-member');
    if (sel1) sel1.innerHTML = _members.map(m => `<option value="${m.id}">${_esc(m.name)}</option>`).join('');
    if (sel2) sel2.innerHTML = _members.map(m => `<option value="${m.id}">${_esc(m.name)}</option>`).join('');
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    switchTab,
    loadMembers,
    filterMinistry,
    startAddLeader,
    prefillFromSearch,
    saveNewLeader,
    cancelAdd,
    openProfile,
    closeProfile,
    toggleProfileEdit,
    saveProfileEdit,
    deleteMember,
    addObjective,
    saveObjective,
    deleteObjective,
    addGoal,
    saveGoal,
    deleteGoal,
    checkTask,
    loadTasks,
    filterTasks,
    checkTaskGlobal,
    deleteTask,
    showAddTaskModal,
    closeAddTask,
    saveNewTask,
    loadMeetings,
    openMeeting,
    closeMeeting,
    sendMeetingEmail,
    openImport,
    closeImport,
    importNext,
    processTranscript,
    saveImport,
    loadMessages,
    showMessage,
    deleteMessage,
    deleteMessage,
    showCompose,
    closeCompose,
    sendCompose,
  };
})();

(function(){ 
  function updateDate(){ 
    var el=document.getElementById('hdr-date'); 
    var d=new Date(); 
    el.textContent=d.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'}); 
  } 
  updateDate(); 
  setInterval(updateDate,60000); 
})();

(function(){
  function updateDate(){
    var el=document.getElementById('hdr-date');
    if(!el) return;
    var d=new Date();
    el.textContent=d.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
  }
  updateDate();
  setInterval(updateDate,60000);
})();
