// ── Mobile-friendly reminder button overrides ────────────────────────────
(function() {
  var s = document.createElement('style');
  s.textContent = [
    '.r-drag{min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center;padding:0;font-size:18px;color:#ffffff;}',
    '.r-btns{gap:12px;}',
    '.r-btn{min-height:36px;min-width:44px;font-size:22px;padding:6px 8px;color:#ffffff;}'
  ].join('');
  document.head.appendChild(s);
})();

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
  const isOpen = document.getElementById('settings-panel').classList.toggle('open');
  document.getElementById('overlay-backdrop').style.display = isOpen ? 'block' : 'none';
}
function closeSettings() {
  document.getElementById('settings-panel').classList.remove('open');
  document.getElementById('overlay-backdrop').style.display = 'none';
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
function openRemindersPanel() {
  document.getElementById('settings-reminders').classList.add('open');
  loadRemindersPanel();
}
function closeRemindersPanel() {
  document.getElementById('settings-reminders').classList.remove('open');
}
async function loadRemindersPanel() {
  const el = document.getElementById('settings-reminders-list');
  el.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:6px 0">Loading...</div>';
  try {
    const data = await api('/api/reminders');
    if (!data.length) {
      el.innerHTML = '<div style="font-size:13px;color:var(--text-muted);padding:12px 0">No reminders set.</div>';
      return;
    }
    el.innerHTML = data.map(function(r) {
      const statusCls = r.status === 'done' ? 'status-archived' : 'status-active';
      const statusLbl = r.status === 'done' ? 'done' : 'active';
      const due = r.due_datetime ? r.due_datetime.replace('T',' ') : '';
      return '<div style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">' +
          '<span style="font-size:13px;font-weight:500;color:var(--text)">' + esc(r.title) + '</span>' +
          '<span class="' + statusCls + '">' + statusLbl + '</span>' +
        '</div>' +
        (due ? '<div style="font-size:11px;color:var(--text-muted);font-family:\'DM Mono\',monospace">' + esc(due) + '</div>' : '') +
      '</div>';
    }).join('');
  } catch(e) {
    el.innerHTML = '<div style="font-size:12px;color:var(--red);padding:6px 0">Failed to load reminders.</div>';
  }
}
var _allSkills = [];
var _skillTab = 'ready';
var _skillCategory = 'All';
var _skillCategories = ['All'];
var _skillSearch = '';

async function loadSkillsPanel() {
  const el = document.getElementById('settings-skills-list');
  el.innerHTML = '<div style="font-size:12px;color:var(--text3);padding:6px 0">Loading...</div>';
  _skillTab = 'ready';
  _skillCategory = 'All';
  _skillSearch = '';
  const _ss = document.getElementById('skill-search');
  if (_ss) _ss.value = '';
  _updateSkillTabUI();
  try {
    var results = await Promise.all([api('/api/skills'), api('/api/skills/categories')]);
    _allSkills = results[0];
    _skillCategories = ['All'].concat(results[1]);
    _renderCategoryPills();
    _renderSkillList();
  } catch(e) {
    el.innerHTML = '<div style="font-size:12px;color:var(--danger);padding:6px 0">Failed to load skills.</div>';
  }
}

function switchSkillTab(tab) {
  _skillTab = tab;
  _skillCategory = 'All';
  _skillSearch = '';
  const _ss = document.getElementById('skill-search');
  if (_ss) _ss.value = '';
  _updateSkillTabUI();
  _renderCategoryPills();
  _renderSkillList();
}

function switchSkillCategory(cat) {
  _skillCategory = cat;
  _renderCategoryPills();
  _renderSkillList();
}

function onSkillSearch(value) {
  _skillSearch = value;
  _renderCategoryPills();
  _renderSkillList();
}

function _updateSkillTabUI() {
  document.querySelectorAll('.skill-tab').forEach(function(btn) {
    const active = btn.dataset.tab === _skillTab;
    btn.style.color = active ? 'var(--accent)' : 'var(--text-muted)';
    btn.style.borderBottomColor = active ? 'var(--accent)' : 'transparent';
  });
}

function _renderCategoryPills() {
  const pillsEl = document.getElementById('skill-category-pills');
  if (!pillsEl) return;
  if (_skillTab !== 'ready' || _skillSearch) {
    pillsEl.innerHTML = '';
    return;
  }
  pillsEl.innerHTML = _skillCategories.map(function(cat) {
    const active = cat === _skillCategory ? ' active' : '';
    return '<button class="skill-cat-pill' + active + '" data-cat="' + esc(cat) + '" onclick="switchSkillCategory(this.dataset.cat)">' + esc(cat) + '</button>';
  }).join('');
}

function _renderSkillList() {
  const el = document.getElementById('settings-skills-list');
  var filtered = _allSkills.filter(function(s) {
    return (s.status || 'ready') === _skillTab;
  });
  const query = _skillSearch.trim().toLowerCase();
  if (query) {
    filtered = filtered.filter(function(s) {
      const name = s.slug.replace(/_/g, ' ');
      return name.includes(query) || (s.description || '').toLowerCase().includes(query);
    });
  } else if (_skillTab === 'ready' && _skillCategory !== 'All') {
    filtered = filtered.filter(function(s) {
      return (s.category || 'Utilities') === _skillCategory;
    });
  }
  if (!filtered.length) {
    const emptyMsg = query
      ? 'No skills matching “' + esc(query) + '”.'
      : 'No ' + _skillTab + ' skills' + (_skillCategory !== 'All' ? ' in ' + _skillCategory : '') + '.';
    el.innerHTML = '<div style="font-size:12px;color:var(--text3);padding:6px 0">' + emptyMsg + '</div>';
    return;
  }
  el.innerHTML = filtered.map(function(s) {
    const name = s.slug.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
    const approveBtn = _skillTab === 'dev'
      ? '<button class="skill-approve-btn" data-slug="' + esc(s.slug) + '" onclick="approveSkill(this)">Approve</button>'
      : '';
    return '<div class="skill-card" id="skill-card-' + esc(s.slug) + '">' +
      '<div class="skill-info">' +
        '<div class="skill-name">' + esc(name) + '</div>' +
        '<div class="skill-desc">' + esc(s.description) + '</div>' +
      '</div>' +
      '<div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0">' +
        approveBtn +
        '<button class="skill-use-btn" data-slug="' + esc(s.slug) + '" onclick="useSkill(this.dataset.slug)">Use</button>' +
      '</div>' +
    '</div>';
  }).join('');
}

async function approveSkill(btn) {
  const slug = btn.dataset.slug;
  console.log('approveSkill called:', slug);
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const result = await api('/api/skills/' + slug + '/approve', 'POST');
    if (result.success) {
      const card = document.getElementById('skill-card-' + slug);
      if (card) {
        card.style.transition = 'opacity .2s';
        card.style.opacity = '0';
        setTimeout(function() {
          card.outerHTML = '';
          _allSkills = _allSkills.map(function(s) {
            return s.slug === slug ? Object.assign({}, s, {status: 'ready'}) : s;
          });
        }, 200);
      }
    } else {
      btn.disabled = false;
      btn.textContent = 'Approve';
    }
  } catch(e) {
    btn.disabled = false;
    btn.textContent = 'Approve';
  }
}
function useSkill(slug) {
  const name = slug.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
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
const loaded = {chat:true, history:false, briefing:false, tasks:false, contacts:false, reading:false, projects:false};
const loaders = {};

const TAB_LABELS = {chat:'Chat',history:'History',briefing:'Briefing',tasks:'Tasks',reminders:'Reminders',contacts:'Contacts',reading:'Reading',projects:'Projects'};
let _activeTab = 'chat';
function switchTab(name) {
  _activeTab = name;
  if (name === 'chat') _clearChatBadge();
  TABS.forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
    const navEl = document.getElementById('nav-' + t);
    if (navEl) navEl.classList.toggle('active', t === name);
  });
  if (!loaded[name]) { loaded[name] = true; if (loaders[name]) loaders[name](); }
}
function _showChatBadge() {
  const b = document.getElementById('chat-nav-badge');
  if (b) b.style.display = 'block';
}
function _clearChatBadge() {
  const b = document.getElementById('chat-nav-badge');
  if (b) b.style.display = 'none';
}

// ── Chat — session state ──────────────────────────────────────────────────
let currentSessionId = null;
let chatHistory = [];
let attachedFileContent = null;
let attachedFileName = null;
let activeProjectSlug = null;

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
      const dataAttrs = s.project_slug
        ? 'data-project-slug="' + esc(s.project_slug) + '" data-project-name="' + esc(projLabel || s.project_slug) + '"'
        : 'data-session-id="' + s.id + '" data-session-title="' + esc(s.title) + '"';
      return '<div class="session-row" ' + dataAttrs + '>' +
        '<div class="session-info">' +
          '<div class="session-title">' + esc(s.title) + badge + '</div>' +
          '<div class="session-date">' + dateStr + '</div>' +
        '</div>' +
        '<button class="session-del" data-del-id="' + s.id + '" title="Delete">&#215;</button>' +
      '</div>';
    }).join('');
    el.querySelectorAll('.session-row').forEach(row => {
      row.addEventListener('click', () => {
        if (row.dataset.projectSlug) {
          openProjectWorkspace(row.dataset.projectSlug, row.dataset.projectName);
        } else {
          openSession(Number(row.dataset.sessionId), row.dataset.sessionTitle);
        }
      });
    });
    el.querySelectorAll('.session-del').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        deleteSession(Number(btn.dataset.delId));
      });
    });
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
function _renderContent(text) {
  const e = text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  return e
    .replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>')
    .replace(/\n/g,'<br>');
}
function _createAvatarEl() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 512 512');
  svg.setAttribute('width','28');
  svg.setAttribute('height','28');
  svg.setAttribute('class','watson-avatar');
  svg.innerHTML = '<rect width="512" height="512" rx="51" fill="#111827"/><text x="256" y="400" font-family="Georgia,serif" font-size="360" font-weight="700" fill="white" text-anchor="middle">W</text>';
  return svg;
}
function _fmtTime(d) {
  const h = d.getHours(), m = d.getMinutes();
  return (h % 12 || 12) + ':' + String(m).padStart(2,'0') + (h < 12 ? ' am' : ' pm');
}
function _appendMsg(role, text) {
  const msgs = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap ' + role;
  if (role === 'watson') wrap.appendChild(_createAvatarEl());
  const col = document.createElement('div');
  col.className = 'msg-col';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  if (text.trim().startsWith('data:image/')) {
    const lines = text.trim().split('\n');
    const img = document.createElement('img');
    img.src = lines[0];
    img.style.maxWidth = '100%';
    img.style.borderRadius = '8px';
    bubble.appendChild(img);
    if (lines.length > 1) {
      const caption = document.createElement('div');
      caption.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:6px';
      caption.textContent = lines.slice(1).join('\n');
      bubble.appendChild(caption);
    }
  } else {
    bubble.innerHTML = _renderContent(text);
  }
  const time = document.createElement('div');
  time.className = 'msg-time';
  time.textContent = _fmtTime(new Date());
  col.appendChild(bubble);
  col.appendChild(time);
  wrap.appendChild(col);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
  if (role === 'watson' && _activeTab !== 'chat') _showChatBadge();
}

function _appendEmailConfirmCard(container, em) {
  const card = document.createElement('div');
  card.className = 'email-confirm-card';
  card.id = 'email-confirm-card';
  const bodyPreview = em.body && em.body.length > 200 ? esc(em.body.slice(0, 200)) + '…' : esc(em.body || '');
  card.innerHTML =
    '<div class="email-confirm-row"><span class="email-confirm-lbl">To: </span>' + esc(em.to_name) + ' (' + esc(em.to_email) + ')</div>' +
    '<div class="email-confirm-row"><span class="email-confirm-lbl">Subject: </span>' + esc(em.subject) + '</div>' +
    '<div class="email-confirm-row"><span class="email-confirm-lbl">Body: </span>' + bodyPreview + '</div>' +
    '<div class="email-confirm-btns">' +
      '<button class="email-confirm-send" onclick="confirmEmail(true)">Send ✓</button>' +
      '<button class="email-confirm-cancel" onclick="confirmEmail(false)">Cancel ✗</button>' +
    '</div>';
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;
}

function _activeMsgsEl() {
  const pw = document.getElementById('proj-workspace');
  return (pw && pw.classList.contains('open'))
    ? document.getElementById('pw-messages')
    : document.getElementById('chat-messages');
}

async function confirmEmail(send) {
  const card = document.getElementById('email-confirm-card');
  try {
    const data = await api('/api/email/confirm', 'POST', {confirm: send});
    if (card) card.remove();
    const reply = data.response || (send ? 'Email sent ✓' : 'Email cancelled.');
    const pw = document.getElementById('proj-workspace');
    if (pw && pw.classList.contains('open')) _appendPWMsg('watson', reply);
    else _appendMsg('watson', reply);
  } catch(e) {
    if (card) card.remove();
    _appendMsg('watson', 'Error: ' + e);
  }
}

function _showTyping() {
  const msgs = document.getElementById('chat-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap watson';
  wrap.id = 'typing-wrap';
  wrap.appendChild(_createAvatarEl());
  const ind = document.createElement('div');
  ind.className = 'typing-indicator';
  ind.innerHTML = '<span></span><span></span><span></span>';
  wrap.appendChild(ind);
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
  const sendBtn = document.getElementById('chat-send-btn');
  const micBtn = document.getElementById('chat-mic-btn');
  const text = input.value.trim();
  if (!text) return;
  if (!currentSessionId) {
    const title = text.length > 50 ? text.slice(0, 50) : text;
    const sess = await api('/api/chat/sessions', 'POST', {title});
    currentSessionId = sess.id;
  }
  input.value = '';
  input.style.height = '';

  const displayMsg = attachedFileName ? text + '\n📎 ' + attachedFileName : text;
  const ollamaMsg = attachedFileContent
    ? '[Attached file: ' + attachedFileName + ']\n' + attachedFileContent + '\n\n---\n\nUser message: ' + text
    : text;
  if (attachedFileContent) clearAttachment();

  _appendMsg('user', displayMsg);
  api('/api/chat/sessions/' + currentSessionId + '/messages', 'POST', {role:'user', content: displayMsg});

  if (chatHistory.length === 0) {
    const title = text.length > 50 ? text.slice(0,50) + '…' : text;
    api('/api/chat/sessions/' + currentSessionId, 'PATCH', {title});
  }

  chatHistory.push({role: 'user', content: ollamaMsg});
  if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);

  input.disabled = true;
  sendBtn.disabled = true;
  if (micBtn) micBtn.disabled = true;

  // Show typing indicator; watson bubble created lazily on first token
  _showTyping();
  const msgs = document.getElementById('chat-messages');
  let watsonBubble = null, watsonTextNode = null, watsonCursor = null;

  function _createWatsonBubble() {
    if (watsonBubble) return;
    _hideTyping();
    const wrap = document.createElement('div');
    wrap.className = 'msg-wrap watson';
    wrap.appendChild(_createAvatarEl());
    const col = document.createElement('div');
    col.className = 'msg-col';
    watsonBubble = document.createElement('div');
    watsonBubble.className = 'msg-bubble';
    watsonTextNode = document.createTextNode('');
    watsonCursor = document.createElement('span');
    watsonCursor.className = 'stream-cursor';
    watsonCursor.textContent = '|';
    watsonBubble.appendChild(watsonTextNode);
    watsonBubble.appendChild(watsonCursor);
    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = _fmtTime(new Date());
    col.appendChild(watsonBubble);
    col.appendChild(time);
    wrap.appendChild(col);
    msgs.appendChild(wrap);
    msgs.scrollTop = msgs.scrollHeight;
  }

  let fullReply = '';
  let confirmEmailData = null;
  let _imgCount = 0;
  let _lastImgContainer = null;

  function _reEnable() {
    input.disabled = false;
    sendBtn.disabled = false;
    if (micBtn) micBtn.disabled = false;
  }

  function _finish() {
    _hideTyping();
    if (watsonCursor) watsonCursor.remove();
    if (watsonBubble && watsonTextNode && watsonTextNode.textContent && watsonBubble.children.length === 0) {
      const content = watsonTextNode.textContent;
      if (content.trim().startsWith('data:image/')) {
        watsonTextNode.textContent = '';
        const lines = content.trim().split('\n');
        const img = document.createElement('img');
        img.src = lines[0];
        img.style.maxWidth = '100%';
        img.style.borderRadius = '8px';
        watsonBubble.appendChild(img);
        if (lines.length > 1) {
          const caption = document.createElement('div');
          caption.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:6px';
          caption.textContent = lines.slice(1).join('\n');
          watsonBubble.appendChild(caption);
        }
      } else {
        watsonBubble.innerHTML = _renderContent(content);
      }
    }
    if (fullReply) {
      chatHistory.push({role: 'assistant', content: fullReply});
      if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
      api('/api/chat/sessions/' + currentSessionId + '/messages', 'POST', {role:'assistant', content: fullReply});
      if (_activeTab !== 'chat') _showChatBadge();
    }
    if (confirmEmailData) {
      _appendEmailConfirmCard(msgs, confirmEmailData);
    }
    _reEnable();
  }

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: ollamaMsg, history: chatHistory.slice(0, -1), session_id: currentSessionId, project_slug: activeProjectSlug})
    });
    if (!response.ok) {
      _hideTyping();
      _createWatsonBubble();
      if (watsonTextNode) watsonTextNode.textContent = 'Watson is offline.';
      if (watsonCursor) watsonCursor.remove();
      _reEnable();
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const event of events) {
        if (!event.trim()) continue;
        const dataLines = event.split('\n').filter(function(l) { return l.startsWith('data: '); });
        if (!dataLines.length) continue;
        for (const dataLine of dataLines) {
          const data = dataLine.slice(6);
          if (data === '[DONE]') {
            _finish();
            return;
          } else if (data.startsWith('[ERROR]')) {
            _hideTyping();
            _createWatsonBubble();
            if (watsonCursor) watsonCursor.remove();
            if (watsonTextNode) watsonTextNode.textContent = data.slice(7).trim();
            _reEnable();
            return;
          } else if (data.startsWith('[CONFIRM_EMAIL]')) {
            try { confirmEmailData = JSON.parse(data.slice(15)); } catch(_) {}
          } else if (data.startsWith('[QR_IMAGE]')) {
            _createWatsonBubble();
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + data.slice(10);
            img.alt = 'QR Code';
            img.style.cssText = 'max-width:280px;border-radius:8px;margin-top:12px;display:block;';
            if (watsonBubble) watsonBubble.appendChild(img);
          } else if (data.startsWith('[IMAGE_URL]')) {
            if (!watsonBubble) _createWatsonBubble();
            const img = document.createElement('img');
            img.src = data.slice(11).trim();
            img.style.cssText = 'max-width:100%;border-radius:8px;display:block;margin:10px 0 4px;';
            if (watsonBubble) watsonBubble.appendChild(img);
          } else if (data.startsWith('[IMAGE_LINK]')) {
            const url = data.slice(12).trim();
            if (watsonBubble) {
              const link = document.createElement('a');
              link.href = url;
              link.target = '_blank';
              link.rel = 'noopener noreferrer';
              link.textContent = 'Open ↗';
              link.style.cssText = 'display:inline-block;margin-bottom:10px;font-size:12px;color:var(--accent);text-decoration:none;padding:4px 12px;border:1px solid var(--accent);border-radius:6px;';
              watsonBubble.appendChild(link);
            }
          } else {
            _createWatsonBubble();
            try {
              const parsed = JSON.parse(data);
              fullReply += (parsed.token !== undefined) ? parsed.token : data;
            } catch(_) {
              fullReply += data;
            }
            if (watsonTextNode) watsonTextNode.textContent = fullReply;
            msgs.scrollTop = msgs.scrollHeight;
          }
        }
      }
    }
    _finish();
  } catch(e) {
    _hideTyping();
    _createWatsonBubble();
    if (watsonTextNode) watsonTextNode.textContent = 'Watson is offline.';
    if (watsonCursor) watsonCursor.remove();
    _reEnable();
  }
}

document.getElementById('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
(function() {
  const ta = document.getElementById('chat-input');
  ta.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });
})();

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
  const result = await api('/api/briefing/' + id + '/' + action, 'POST');
  if (action === 'approve' && result && result.url) window.open(result.url, '_blank');
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

async function importGoogleContacts() {
  const msg = document.getElementById('c-import-msg');
  msg.textContent = 'Importing from Google Contacts…';
  msg.style.display = 'block';
  try {
    const data = await api('/api/contacts/import', 'POST');
    msg.textContent = data.response || 'Import started.';
    setTimeout(() => loadContacts(), 3000);
  } catch(e) {
    msg.textContent = 'Import failed: ' + e;
  }
}

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
    carrier: document.getElementById('nc-carrier').value || null,
    notes: document.getElementById('nc-notes').value.trim() || null,
  });
  if (!c.error) {
    contacts = [...contacts, c].sort((a,b) => a.name.localeCompare(b.name));
    ['nc-name','nc-email','nc-phone','nc-rel','nc-notes'].forEach(id =>
      document.getElementById(id).value = '');
    document.getElementById('nc-carrier').value = '';
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
        (function(){
          var opts = ['','AT&T','Verizon','T-Mobile','Sprint','US Cellular','Cricket','Boost','Metro PCS','Other'];
          var sel = '<select id="ec-ca-' + c.id + '" style="width:100%;padding:9px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:13px;font-family:inherit">';
          opts.forEach(function(o){ sel += '<option value="' + o + '"' + (c.carrier===o?' selected':'') + '>' + (o||'Carrier (Unknown)') + '</option>'; });
          return sel + '</select>';
        })() +
        '<input type="text" id="ec-no-' + c.id + '" value="' + esc(c.notes||'') + '" placeholder="Notes">' +
        '<div class="row"><button class="btn btn-p" onclick="saveEdit(' + c.id + ')">Save</button>' +
        '<button class="btn btn-gh" onclick="cancelEdit()">Cancel</button></div>'
      :
        (c.email ? '<div class="dl">&#9993; ' + esc(c.email) + '</div>' : '') +
        (c.phone ? '<div class="dl">&#128222; ' + esc(c.phone) + '</div>' : '') +
        (c.relationship ? '<div class="dl">' + esc(c.relationship) + '</div>' : '') +
        (c.carrier ? '<div class="dl" style="color:var(--text3);font-size:11px">&#128246; ' + esc(c.carrier) + '</div>' : '') +
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
    carrier: document.getElementById('ec-ca-' + id).value || null,
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
  var el = document.getElementById('r-list');
  var vis = rFilter === 'all' ? reminders : reminders.filter(function(r) { return r.status === rFilter; });
  if (!vis.length) { el.innerHTML = '<div class="ctr">No reminders</div>'; return; }
  el.innerHTML = vis.map(function(r) {
    return '<div class="tr" data-drag-id="' + r.id + '" id="rem-' + r.id + '">' +
      '<span class="r-drag" draggable="true" title="Drag to reorder">&#10495;</span>' +
      '<div class="tbody">' +
        '<div class="ttitle' + (r.status === 'done' ? ' done' : '') + '" id="rtitle-' + r.id + '">' + esc(r.title) + '</div>' +
        (r.due_datetime ? '<div class="tmeta"><span style="font-size:10px;color:var(--text3)">&#9200; ' + esc(fmtDt(r.due_datetime)) + '</span></div>' : '') +
      '</div>' +
      '<div class="r-btns">' +
        '<button class="r-btn r-edit" onclick="editReminder(' + r.id + ')" title="Edit">&#9998;</button>' +
        '<button class="r-btn r-done" onclick="doneReminder(' + r.id + ')" title="Done">&#10003;</button>' +
        '<button class="r-btn r-del" onclick="deleteReminder(' + r.id + ')" title="Delete">&#215;</button>' +
      '</div>' +
    '</div>';
  }).join('');
}

function editReminder(id) {
  var titleEl = document.getElementById('rtitle-' + id);
  var r = reminders.find(function(x) { return x.id === id; });
  if (!r || !titleEl) return;
  var inp = document.createElement('input');
  inp.type = 'text';
  inp.value = r.title;
  inp.className = 'r-inline-edit';
  inp.onblur = function() { _saveReminderEdit(id, inp.value); };
  inp.onkeydown = function(e) {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    else if (e.key === 'Escape') { inp.onblur = null; renderReminders(); }
  };
  titleEl.replaceWith(inp);
  inp.focus(); inp.select();
}

function _saveReminderEdit(id, val) {
  val = (val || '').trim();
  var r = reminders.find(function(x) { return x.id === id; });
  if (!r) return;
  if (!val || val === r.title) { renderReminders(); return; }
  reminders = reminders.map(function(x) { return x.id === id ? Object.assign({}, x, {title: val}) : x; });
  renderReminders();
  api('/api/reminders/' + id, 'PATCH', {title: val});
}

function doneReminder(id) {
  reminders = reminders.filter(function(r) { return r.id !== id; });
  renderReminders();
  api('/api/reminders/' + id, 'PATCH', {status: 'done'});
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

(function() {
  var list = document.getElementById('r-list');
  var _rdrag = null;

  list.addEventListener('dragstart', function(e) {
    if (!e.target.classList.contains('r-drag')) return;
    var row = e.target.closest('[data-drag-id]');
    if (!row) return;
    _rdrag = row;
    row.classList.add('r-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setDragImage(row, 20, 20);
  });

  list.addEventListener('dragover', function(e) {
    e.preventDefault();
    var row = e.target.closest('[data-drag-id]');
    if (!row || row === _rdrag) return;
    list.querySelectorAll('.r-over-top,.r-over-bot').forEach(function(x) { x.classList.remove('r-over-top','r-over-bot'); });
    var rect = row.getBoundingClientRect();
    row.classList.add(e.clientY < rect.top + rect.height / 2 ? 'r-over-top' : 'r-over-bot');
  });

  list.addEventListener('dragleave', function(e) {
    if (!list.contains(e.relatedTarget)) {
      list.querySelectorAll('.r-over-top,.r-over-bot').forEach(function(x) { x.classList.remove('r-over-top','r-over-bot'); });
    }
  });

  list.addEventListener('drop', function(e) {
    e.preventDefault();
    var target = e.target.closest('[data-drag-id]');
    list.querySelectorAll('.r-over-top,.r-over-bot').forEach(function(x) { x.classList.remove('r-over-top','r-over-bot'); });
    if (!target || !_rdrag || target === _rdrag) { _rdrag = null; return; }
    var rect = target.getBoundingClientRect();
    var before = e.clientY < rect.top + rect.height / 2;
    var dragId = parseInt(_rdrag.dataset.dragId);
    var targetId = parseInt(target.dataset.dragId);
    var dragIdx = reminders.findIndex(function(r) { return r.id === dragId; });
    if (dragIdx === -1) { _rdrag = null; return; }
    var item = reminders.splice(dragIdx, 1)[0];
    var newTargetIdx = reminders.findIndex(function(r) { return r.id === targetId; });
    if (newTargetIdx === -1) { reminders.splice(dragIdx, 0, item); _rdrag = null; return; }
    reminders.splice(before ? newTargetIdx : newTargetIdx + 1, 0, item);
    _rdrag = null;
    renderReminders();
    reminders.forEach(function(r, i) { api('/api/reminders/' + r.id, 'PATCH', {sort_order: i}); });
  });

  list.addEventListener('dragend', function() {
    if (_rdrag) _rdrag.classList.remove('r-dragging');
    list.querySelectorAll('.r-over-top,.r-over-bot').forEach(function(x) { x.classList.remove('r-over-top','r-over-bot'); });
    _rdrag = null;
  });
})();

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
      ? '<button class="btn btn-gh" style="flex:none;padding:4px 10px;font-size:11px" onclick="event.stopPropagation();restoreProject(\'' + esc(p.slug) + '\')">Restore</button>'
      : '';
    return '<div class="proj-card" onclick="openProjectWorkspace(\'' + esc(p.slug) + '\',\'' + esc(p.name||p.slug).replace(/'/g,"'") + '\')">' +
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
  activeProjectSlug = slug;
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
      const lines = proj.content.split('\n');
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
  activeProjectSlug = null;
}

function dismissSummary() {
  document.getElementById('pw-summary-card').style.display = 'none';
}

// ── PW messaging ──────────────────────────────────────────────────────────────
function _appendPWMsg(role, text) {
  const msgs = document.getElementById('pw-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap ' + role;
  if (role === 'watson') wrap.appendChild(_createAvatarEl());
  const col = document.createElement('div');
  col.className = 'msg-col';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = _renderContent(text);
  const time = document.createElement('div');
  time.className = 'msg-time';
  time.textContent = _fmtTime(new Date());
  col.appendChild(bubble);
  col.appendChild(time);
  wrap.appendChild(col);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
}
function _showPWTyping() {
  const msgs = document.getElementById('pw-messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg-wrap watson';
  wrap.id = 'pw-typing-wrap';
  wrap.appendChild(_createAvatarEl());
  const ind = document.createElement('div');
  ind.className = 'typing-indicator';
  ind.innerHTML = '<span></span><span></span><span></span>';
  wrap.appendChild(ind);
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
  const displayMsg = pwAttachedName ? text + '\n📎 ' + pwAttachedName : text;
  const ollamaMsg = pwAttachedContent
    ? '[Attached file: ' + pwAttachedName + ']\n' + pwAttachedContent + '\n\n---\n\nUser message: ' + text
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
    const data = await api('/api/chat', 'POST', {message:ollamaMsg, history:pwChatHistory.slice(0,-1), session_id: pwSessionId, project_slug: pwSlug});
    _hidePWTyping();
    const reply = data.response || '(no response)';
    _appendPWMsg('watson', reply);
    if (data.confirm_email) _appendEmailConfirmCard(document.getElementById('pw-messages'), data.confirm_email);
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
  if (!pwSlug) return;
  closeProjectMemory();
  document.getElementById('pw-sidebar').classList.add('open');
  await loadFileSidebar();
}
function closeFileSidebar() { document.getElementById('pw-sidebar').classList.remove('open'); }

async function handleSidebarFileUpload(input) {
  if (!input.files.length || !pwSlug) return;
  const file = input.files[0];
  input.value = '';
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch('/api/projects/' + pwSlug + '/files', { method: 'POST', body: form });
    if (!resp.ok) throw new Error(await resp.text());
    await loadFileSidebar();
    const msg = document.getElementById('pw-upload-msg');
    msg.style.display = 'block';
    setTimeout(() => { msg.style.display = 'none'; }, 2000);
  } catch (e) {
    alert('Upload failed: ' + e);
  }
}

async function openProjectMemory() {
  if (!pwSlug) return;
  closeFileSidebar();
  const panel = document.getElementById('project-memory-panel');
  const content = document.getElementById('pm-content');
  content.textContent = 'Loading…';
  panel.classList.add('open');
  try {
    const data = await api('/api/projects/' + pwSlug + '/memory');
    content.textContent = data.content || '(no memory yet)';
  } catch (e) {
    content.textContent = 'Failed to load memory.';
  }
}

function closeProjectMemory() {
  document.getElementById('project-memory-panel').classList.remove('open');
}

async function saveProjectMemory() {
  if (!pwSlug) return;
  const ta = document.getElementById('pm-textarea');
  const addition = ta.value.trim();
  if (!addition) return;
  try {
    await api('/api/projects/' + pwSlug + '/memory', 'POST', { content: addition });
    const content = document.getElementById('pm-content');
    const existing = content.textContent === '(no memory yet)' ? '' : content.textContent;
    content.textContent = existing + (existing ? '\n\n' : '') + addition;
    ta.value = '';
    const msg = document.getElementById('pm-saved-msg');
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, 2000);
  } catch (e) {
    alert('Failed to save: ' + e);
  }
}

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
        return '<div class="pw-file-row" onclick="injectFile(\'' + esc(pwSlug) + '\',\'' + esc(f.name) + '\',\'notes\')">' +
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
        return '<div class="pw-file-row" onclick="injectFile(\'' + esc(pwSlug) + '\',\'' + esc(f.name) + '\',\'files\')">' +
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

// ── Pull-to-refresh ────────────────────────────────────────────────────────
(function() {
  const THRESHOLD = 80;
  let startY = 0, pulling = false, triggered = false;

  const indicator = document.createElement('div');
  indicator.id = 'ptr-indicator';
  indicator.innerHTML = '<div id="ptr-spinner"></div><div id="ptr-label"></div>';
  document.body.appendChild(indicator);
  const spinner = document.getElementById('ptr-spinner');
  const label = document.getElementById('ptr-label');

  function _overlayOpen() {
    return !!(document.querySelector('.overlay-panel.open') ||
              document.querySelector('#proj-workspace.open'));
  }

  function _animate(dist) {
    const ratio = Math.min(dist / THRESHOLD, 1);
    indicator.style.opacity = ratio;
    indicator.style.transform = 'translateX(-50%) translateY(' + (ratio * 20 - 20) + 'px)';
  }

  function _reset() {
    pulling = false;
    triggered = false;
    indicator.style.transition = 'opacity .3s,transform .3s';
    indicator.style.opacity = '0';
    indicator.style.transform = 'translateX(-50%) translateY(-20px)';
    spinner.classList.remove('spinning');
    label.textContent = '';
  }

})();

// ── Pastoral Notes ────────────────────────────────────────────────────────────

let _pastoralStatus = 'active';

async function openPastoralNotes() {
  _pastoralStatus = 'active';
  const panel = document.getElementById('pastoral-panel');
  panel.style.display = 'flex';
  _syncPastoralTabs();
  await loadPastoralNotes();
}

function closePastoralNotes() {
  document.getElementById('pastoral-panel').style.display = 'none';
}

function _syncPastoralTabs() {
  const active = document.getElementById('pn-tab-active');
  const archived = document.getElementById('pn-tab-archived');
  if (!active || !archived) return;
  if (_pastoralStatus === 'active') {
    active.style.borderBottomColor = 'var(--accent)';
    active.style.color = 'var(--accent)';
    archived.style.borderBottomColor = 'transparent';
    archived.style.color = 'var(--text-muted)';
  } else {
    archived.style.borderBottomColor = 'var(--accent)';
    archived.style.color = 'var(--accent)';
    active.style.borderBottomColor = 'transparent';
    active.style.color = 'var(--text-muted)';
  }
}

async function loadPastoralNotes() {
  const res = await fetch(`/api/pastoral-notes?status=${_pastoralStatus}`);
  const notes = await res.json();
  const container = document.getElementById('pastoral-notes-list');
  if (!notes.length) {
    container.innerHTML = '<p style="color:var(--text-muted);padding:1rem;">No notes.</p>';
    return;
  }
  container.innerHTML = notes.map(n => `
    <div class="pastoral-card" id="pn-${n.id}">
      <div class="pn-header">
        <span class="pn-name">${n.person_name}</span>
        <span class="pn-date">${n.created_at.slice(0,16)}</span>
      </div>
      <div class="pn-note">${n.note}</div>
      ${_pastoralStatus === 'active' ? `<button class="pn-archive-btn" onclick="archivePastoralNote(${n.id})">Archive</button>` : ''}
    </div>
  `).join('');
}

async function archivePastoralNote(id) {
  await fetch(`/api/pastoral-notes/${id}/archive`, { method: 'POST' });
  const card = document.getElementById(`pn-${id}`);
  if (card) card.remove();
  const container = document.getElementById('pastoral-notes-list');
  if (container && !container.querySelector('.pastoral-card')) {
    container.innerHTML = '<p style="color:var(--text-muted);padding:1rem;">No notes.</p>';
  }
}

async function setPastoralView(status) {
  _pastoralStatus = status;
  _syncPastoralTabs();
  await loadPastoralNotes();
}

function togglePastoralForm() {
  const form = document.getElementById('pn-add-form');
  const visible = form.style.display !== 'none';
  form.style.display = visible ? 'none' : 'flex';
  if (!visible) document.getElementById('pn-name-input').focus();
}

async function savePastoralNote() {
  const name = document.getElementById('pn-name-input').value.trim();
  const note = document.getElementById('pn-note-input').value.trim();
  if (!name || !note) return;
  await fetch('/api/pastoral-notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ person_name: name, note })
  });
  document.getElementById('pn-name-input').value = '';
  document.getElementById('pn-note-input').value = '';
  document.getElementById('pn-add-form').style.display = 'none';
  await loadPastoralNotes();
}

function openReportsPanel() {
  document.getElementById('settings-main').style.display = 'none';
  document.getElementById('settings-reports').classList.add('open');
  loadReportsList();
}

function closeReportsPanel() {
  document.getElementById('settings-reports').classList.remove('open');
  document.getElementById('settings-main').style.display = '';
}

function openShepherdingPanel() {
  document.getElementById('settings-main').style.display = 'none';
  document.getElementById('settings-shepherding').classList.add('open');
}

function closeShepherdingPanel() {
  document.getElementById('settings-shepherding').classList.remove('open');
  document.getElementById('settings-main').style.display = '';
}

async function runShepherdingReport() {
  const out = document.getElementById('shepherding-output');
  const view = document.getElementById('shepherding-report-view');
  out.textContent = 'Generating report…';
  view.innerHTML = '';
  try {
    const [summaryData, reportData] = await Promise.all([
      api('/api/shepherding/run'),
      api('/api/shepherding/report'),
    ]);
    out.textContent = summaryData.summary || summaryData.error || 'Done.';
    if (reportData.html) {
      view.innerHTML = reportData.html;
      view.querySelectorAll('tr[data-member-id]').forEach(row => {
        const memberId = row.getAttribute('data-member-id');
        const nameCell = row.querySelector('td:first-child');
        if (!nameCell) return;
        const btn = document.createElement('button');
        btn.textContent = 'Exempt';
        btn.style.cssText = 'background:transparent;color:#888;border:1px solid #444;font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer;margin-left:6px;';
        btn.onmouseenter = () => { btn.style.color = '#fff'; btn.style.borderColor = '#888'; };
        btn.onmouseleave = () => { btn.style.color = '#888'; btn.style.borderColor = '#444'; };
        btn.onclick = () => exemptMember(parseInt(memberId), nameCell.textContent.trim(), btn, row);
        nameCell.appendChild(btn);

        const seenBtn = document.createElement('button');
        seenBtn.textContent = 'Seen Sunday';
        seenBtn.style.cssText = 'background:transparent;color:#4caf8a;border:1px solid #2d6b52;font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer;margin-left:6px;';
        seenBtn.onmouseenter = () => { seenBtn.style.color = '#6fcfaa'; seenBtn.style.borderColor = '#4caf8a'; };
        seenBtn.onmouseleave = () => { seenBtn.style.color = '#4caf8a'; seenBtn.style.borderColor = '#2d6b52'; };
        seenBtn.onclick = () => checkInMember(parseInt(memberId), nameCell.textContent.trim(), seenBtn, row);
        nameCell.appendChild(seenBtn);
      });
    }
  } catch (e) {
    out.textContent = 'Error: ' + e.message;
  }
}

async function checkInMember(memberId, name, btn, row) {
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const data = await api('/api/shepherding/checkin', 'POST', { member_id: memberId });
    if (data.ok) {
      btn.textContent = `✓ Checked In ${data.date}`;
      row.style.opacity = '0.5';
    } else {
      btn.textContent = 'Error';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Error';
    btn.disabled = false;
  }
}

async function exemptMember(memberId, name, btn, row) {
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const data = await api('/api/shepherding/exempt', 'POST', { member_id: memberId });
    if (data.ok) {
      btn.textContent = 'Exempted';
      row.style.opacity = '0.5';
    } else {
      btn.textContent = 'Error';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Error';
    btn.disabled = false;
  }
}

async function emailShepherdingReport() {
  const out = document.getElementById('shepherding-output');
  out.textContent = 'Sending…';
  try {
    const data = await api('/api/shepherding/email', 'POST');
    out.textContent = data.message || data.error || 'Report sent.';
  } catch (e) {
    out.textContent = 'Error: ' + e.message;
  }
}

// ── Data Audit ────────────────────────────────────────────────────────────────

let _auditData = null;

async function runDataAudit() {
  const container = document.getElementById('audit-results');
  container.innerHTML = '<p style="font-size:12px;color:#888;margin:8px 0">Running audit…</p>';
  try {
    _auditData = await api('/api/audit/run', 'POST');
    renderAuditResults(_auditData);
  } catch (e) {
    container.innerHTML = `<p style="font-size:12px;color:#e74c3c;margin:8px 0">Error: ${e.message}</p>`;
  }
}

function renderAuditResults(data) {
  const container = document.getElementById('audit-results');
  const dupes  = data.duplicates     || [];
  const incons = data.inconsistencies || [];
  let html = '';

  html += `<h3 style="font-size:11px;color:#f0c040;margin:10px 0 6px;text-transform:uppercase;letter-spacing:.06em">&#128269; Likely Duplicates (${dupes.length})</h3>`;
  if (!dupes.length) {
    html += `<p style="font-size:12px;color:#555;margin:0 0 4px">No likely duplicates found.</p>`;
  } else {
    dupes.forEach((pair, idx) => { html += _renderDupePair(pair, idx); });
  }

  html += `<h3 style="font-size:11px;color:#f0c040;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.06em">&#9888; Data Inconsistencies (${incons.length})</h3>`;
  if (!incons.length) {
    html += `<p style="font-size:12px;color:#555;margin:0">No data inconsistencies found.</p>`;
  } else {
    incons.forEach((member, idx) => { html += _renderInconsistencyCard(member, idx); });
  }

  container.innerHTML = html;
}

function _renderDupePair(pair, idx) {
  const a = pair.a, b = pair.b;
  const confColor = pair.confidence === 'high' ? '#c0392b' : '#b8860b';
  const FIELDS = [
    { key: 'name',              label: 'Name'      },
    { key: 'email',             label: 'Email'     },
    { key: 'phone',             label: 'Phone'     },
    { key: 'campus_preference', label: 'Campus'    },
    { key: 'card_count',        label: 'Cards'     },
    { key: 'last_seen',         label: 'Last Seen' },
  ];
  const CHOICE_FIELDS = new Set(['name', 'email', 'phone', 'campus_preference']);

  let rows = '';
  FIELDS.forEach(f => {
    const va = a[f.key] || '—', vb = b[f.key] || '—';
    const diff = String(va) !== String(vb);
    const bg = diff ? 'background:#1c1500;' : '';
    let radios = '';
    if (diff && CHOICE_FIELDS.has(f.key)) {
      radios =
        `<label style="font-size:10px;margin-left:3px"><input type="radio" name="pair-${idx}-${f.key}" value="a" checked> A</label>` +
        `<label style="font-size:10px;margin-left:3px"><input type="radio" name="pair-${idx}-${f.key}" value="b"> B</label>`;
    }
    rows +=
      `<tr style="${bg}">` +
        `<td style="font-size:10px;color:#555;padding:3px 4px;white-space:nowrap">${f.label}</td>` +
        `<td style="font-size:11px;padding:3px 4px;color:${diff?'#f0c040':'#888'};word-break:break-all">${va}</td>` +
        `<td style="font-size:11px;padding:3px 4px;color:${diff?'#f0c040':'#888'};word-break:break-all">${vb}</td>` +
        `<td style="padding:3px 4px;white-space:nowrap">${radios}</td>` +
      `</tr>`;
  });

  return (
    `<div id="dupe-pair-${idx}" style="background:#141414;border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin-bottom:10px">` +
      `<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">` +
        `<span style="background:${confColor};color:#fff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:bold;text-transform:uppercase">${pair.confidence}</span>` +
        `<span style="font-size:11px;color:#555">${pair.match_reason}</span>` +
      `</div>` +
      `<table style="width:100%;border-collapse:collapse">` +
        `<tr>` +
          `<th style="font-size:9px;color:#444;padding:2px 4px;text-align:left;font-weight:normal"></th>` +
          `<th style="font-size:9px;color:#777;padding:2px 4px;text-align:left;font-weight:normal">A — id:${a.id}</th>` +
          `<th style="font-size:9px;color:#777;padding:2px 4px;text-align:left;font-weight:normal">B — id:${b.id}</th>` +
          `<th></th>` +
        `</tr>` +
        `${rows}` +
      `</table>` +
      `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:8px">` +
        `<span style="font-size:11px;color:#555">Survivor:</span>` +
        `<label style="font-size:12px"><input type="radio" name="pair-${idx}-survivor" value="${a.id}" onchange="updateMergeBtn(${idx})"> Keep A</label>` +
        `<label style="font-size:12px"><input type="radio" name="pair-${idx}-survivor" value="${b.id}" onchange="updateMergeBtn(${idx})"> Keep B</label>` +
        `<button id="merge-btn-${idx}" onclick="executeMerge(${idx})" disabled ` +
          `style="margin-left:auto;background:transparent;color:#444;border:1px solid #333;font-size:11px;padding:3px 10px;border-radius:4px;cursor:default">` +
          `Merge` +
        `</button>` +
      `</div>` +
    `</div>`
  );
}

function updateMergeBtn(idx) {
  const sel = document.querySelector(`input[name="pair-${idx}-survivor"]:checked`);
  const btn = document.getElementById(`merge-btn-${idx}`);
  if (sel) {
    btn.disabled = false;
    btn.style.color = '#e74c3c';
    btn.style.borderColor = '#c0392b';
    btn.style.cursor = 'pointer';
  }
}

async function executeMerge(idx) {
  const pair = _auditData.duplicates[idx];
  const sel  = document.querySelector(`input[name="pair-${idx}-survivor"]:checked`);
  if (!sel) return;
  const winnerId = parseInt(sel.value);
  const loserId  = winnerId === pair.a.id ? pair.b.id : pair.a.id;
  const fieldChoices = {};
  ['name', 'email', 'phone', 'campus_preference'].forEach(f => {
    const inp = document.querySelector(`input[name="pair-${idx}-${f}"]:checked`);
    if (inp) fieldChoices[f] = inp.value;
  });
  const btn = document.getElementById(`merge-btn-${idx}`);
  btn.textContent = '…'; btn.disabled = true;
  try {
    const data = await api('/api/audit/merge', 'POST', {
      winner_id: winnerId, loser_id: loserId,
      field_choices: fieldChoices,
      a_id: pair.a.id, b_id: pair.b.id,
    });
    if (data.ok) {
      const card = document.getElementById(`dupe-pair-${idx}`);
      card.style.opacity = '0.35';
      card.style.pointerEvents = 'none';
      btn.textContent = '✓ Merged';
    } else {
      btn.textContent = data.error || 'Error';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Error';
    btn.disabled = false;
  }
}

function _renderInconsistencyCard(member, idx) {
  let fieldsHtml = '';
  member.inconsistencies.forEach((inc, fIdx) => {
    let varHtml = '';
    inc.variations.forEach((v, vIdx) => {
      const isSug = v.value === inc.suggested;
      const isCur = v.value === inc.current_value && !isSug;
      const badge = isSug
        ? `<span style="font-size:9px;color:#4caf8a;margin-left:3px">[suggested]</span>`
        : (isCur ? `<span style="font-size:9px;color:#666;margin-left:3px">[current]</span>` : '');
      varHtml +=
        `<label style="display:flex;align-items:center;gap:4px;font-size:12px;padding:2px 0;cursor:pointer">` +
          `<input type="radio" name="member-${idx}-field-${fIdx}" value="${vIdx}" ${isSug ? 'checked' : ''}>` +
          `<span style="word-break:break-all">${v.value || '(empty)'}</span>` +
          `<span style="color:#555;font-size:11px">(${v.count}×)</span>${badge}` +
        `</label>`;
    });
    fieldsHtml +=
      `<div style="margin-bottom:10px">` +
        `<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">${inc.field}</div>` +
        `${varHtml}` +
      `</div>`;
  });

  return (
    `<div id="inconsistency-card-${idx}" style="background:#141414;border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin-bottom:10px">` +
      `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">` +
        `<strong style="font-size:13px;color:#ccc">${member.member_name || '(no name)'}</strong>` +
        `<span style="font-size:11px;color:#444">${member.card_count} cards</span>` +
      `</div>` +
      `${fieldsHtml}` +
      `<button onclick="applyCorrections(${idx})" ` +
        `style="background:transparent;color:#4caf8a;border:1px solid #2d6b52;font-size:11px;padding:3px 10px;border-radius:4px;cursor:pointer">` +
        `Apply Corrections` +
      `</button>` +
    `</div>`
  );
}

async function applyCorrections(idx) {
  const member = _auditData.inconsistencies[idx];
  const btn = document.querySelector(`#inconsistency-card-${idx} button`);
  btn.textContent = '…'; btn.disabled = true;
  try {
    for (let fIdx = 0; fIdx < member.inconsistencies.length; fIdx++) {
      const inc = member.inconsistencies[fIdx];
      const inp = document.querySelector(`input[name="member-${idx}-field-${fIdx}"]:checked`);
      if (!inp) continue;
      const value = inc.variations[parseInt(inp.value)].value;
      await api('/api/audit/correct-field', 'POST', { member_id: member.member_id, field: inc.field, value });
    }
    const card = document.getElementById(`inconsistency-card-${idx}`);
    card.style.opacity = '0.35';
    card.style.pointerEvents = 'none';
    btn.textContent = '✓ Corrected';
  } catch (e) {
    btn.textContent = 'Error';
    btn.disabled = false;
  }
}

function loadReportsList() {
  const reports = [
    { key: 'next_steps', label: 'Next Steps' },
    { key: 'missed_weeks', label: 'Absent Members' },
    { key: 'first_time_visitors', label: 'First-Time Visitors' },
    { key: 'attendance_trends', label: 'Attendance Trends' },
    { key: 'overview', label: 'Congregation Overview' },
  ];
  const list = document.getElementById('settings-reports-list');
  list.innerHTML = reports.map(r => `
    <div class="overlay-row" onclick="runReportFromMenu('${r.key}')">
      <span class="overlay-row-label">${r.label}</span>
      <span class="overlay-row-chevron">&#8250;</span>
    </div>
  `).join('');
}

const REPORT_TIME_PARAMS = {
  'next_steps': { label: 'Next Steps', param: 'weeks', default: 12 },
  'missed_weeks': { label: 'Absent Members', param: 'weeks', default: 3 },
  'first_time_visitors': { label: 'First-Time Visitors', param: 'weeks', default: 4 },
  'attendance_trends': { label: 'Attendance Trends', param: 'weeks', default: 8 },
  'overview': { label: 'Congregation Overview', param: null, default: null },
};

function runReportFromMenu(key) {
  const config = REPORT_TIME_PARAMS[key] || { param: null };
  if (config.param === 'weeks') {
    showReportTimePicker(key, config);
  } else {
    executeReport(key, null);
  }
}

function showReportTimePicker(key, config) {
  const existing = document.getElementById('report-time-picker');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'report-time-picker';
  modal.style.cssText = 'position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg);border-radius:16px 16px 0 0;padding:24px;width:100%;max-width:480px;padding-bottom:max(24px,env(safe-area-inset-bottom))">
      <div style="font-size:1em;font-weight:600;margin-bottom:16px">${config.label}</div>
      <div style="font-size:.85em;color:var(--text-muted);margin-bottom:12px">How many weeks back?</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px">
        ${[2,4,6,8,12,16,24].map(w => `
          <button onclick="pickReportWeeks('${key}',${w})"
            style="padding:8px 16px;border-radius:8px;border:1px solid var(--border);background:${w===config.default?'var(--accent)':'var(--surface-2)'};color:${w===config.default?'#fff':'var(--text)'};font-size:.9em;cursor:pointer">
            ${w}w
          </button>`).join('')}
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px">
        <input id="report-weeks-custom" type="number" min="1" max="104" placeholder="Custom weeks"
          style="flex:1;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:.9em">
        <button onclick="pickReportWeeks('${key}', parseInt(document.getElementById('report-weeks-custom').value)||${config.default})"
          style="padding:10px 18px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-size:.9em;cursor:pointer">Go</button>
      </div>
      <button onclick="document.getElementById('report-time-picker').remove()"
        style="width:100%;padding:12px;border-radius:8px;border:1px solid var(--border);background:none;color:var(--text-muted);font-size:.9em;cursor:pointer">Cancel</button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

function pickReportWeeks(key, weeks) {
  const modal = document.getElementById('report-time-picker');
  if (modal) modal.remove();
  executeReport(key, weeks);
}

function executeReport(key, weeks) {
  closeReportsPanel();
  closeSettings();
  switchTab('chat');
  const message = weeks ? `run report: ${key} ${weeks}` : `run report: ${key}`;
  const container = document.getElementById('chat-messages');
  if (container) {
    const userBubble = document.createElement('div');
    userBubble.className = 'msg user';
    userBubble.textContent = message;
    container.appendChild(userBubble);
    container.scrollTop = container.scrollHeight;
  }
  fetch('/api/chat/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ message: message, history: [], session_id: currentSessionId || 0 })
  }).then(response => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let bubble = null;
    function read() {
      reader.read().then(({ done, value }) => {
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith('data: ') && line !== 'data: [DONE]' && !line.startsWith('data: [ERROR]')) {
            const text = line.slice(6);
            if (!bubble) {
              bubble = document.createElement('div');
              bubble.className = 'msg assistant';
              bubble.style.maxWidth = '100%';
              if (container) {
                container.appendChild(bubble);
                container.scrollTop = container.scrollHeight;
              }
            }
            bubble.innerHTML += text;
            if (container) container.scrollTop = container.scrollHeight;
          }
        }
        read();
      });
    }
    read();
  });
}
