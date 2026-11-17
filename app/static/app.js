'use strict';
/* MediRAG web UI — vanilla JS, no build step. All dynamic content is rendered
   through the DOM API (textContent), never innerHTML, so model output and
   report data cannot inject markup. */

const API = '/api/v1';
const $ = (sel, root = document) => root.querySelector(sel);
const state = {
  token: null, user: null,
  sessionId: null, sessions: [], sending: false,
  opts: null, job: null, pollTimer: null, reportId: null,
  usageTimer: null, usageUser: null, abort: null, docsTimer: null, docCaps: null,
};

/* ───────────── helpers ───────────── */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (['value', 'checked', 'disabled', 'selected', 'hidden'].includes(k)) el[k] = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); }));

let toastTimer;
function toast(msg, ms = 3500) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

class ApiError extends Error {
  constructor(status, message, data) { super(message); this.status = status; this.data = data; }
}
function errMsg(data, fallback) {
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  const d = data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).slice(-1)[0] ?? ''}: ${e.msg}`).join('; ');
  return fallback;
}

async function api(path, { method = 'GET', json, form, raw = false, noAuth = false, signal } = {}) {
  const headers = {};
  if (state.token && !noAuth) headers.Authorization = 'Bearer ' + state.token;
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;           // URLSearchParams / FormData set their own content type
  const res = await fetch(API + path, { method, headers, body, signal });
  if (res.status === 401 && !noAuth) {
    let msg = 'Your session expired. Please log in again.';
    try { const d = await res.json(); if (d && /disabled/i.test(d.detail || '')) msg = d.detail; } catch { /* keep default */ }
    logout(msg);
    throw new ApiError(401, msg);
  }
  if (raw && res.ok) return res;                       // caller reads the body (download / stream)
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, errMsg(data, `Request failed (${res.status})`), data);
  return data;
}

const pct = (v) => v == null ? '—' : Math.round(v * 100) + '%';
const num = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
const when = (s) => { const d = new Date(s); return isNaN(d) ? (s || '') : d.toLocaleString(); };
const rateClass = (v) => v == null ? 'na' : v >= 0.8 ? 'good' : v >= 0.5 ? 'ok' : 'bad';

/* Tiny, safe markdown subset: paragraphs, lists, **bold**, `code`, --- */
function inline(text) {
  const out = []; const re = /(\*\*[^*]+\*\*|`[^`]+`)/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    out.push(tok.startsWith('**') ? h('strong', { text: tok.slice(2, -2) }) : h('code', { text: tok.slice(1, -1) }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
function renderMd(text) {
  const frag = document.createDocumentFragment();
  const src = String(text || '').replace(/^\s*-{3,}\s*$/gm, '\n\n---\n\n');
  for (const block of src.split(/\n{2,}/)) {
    const t = block.trim(); if (!t) continue;
    if (/^-{3,}$/.test(t)) { frag.append(h('hr')); continue; }
    const lines = t.split('\n');
    if (lines.every(l => /^\s*[-*•]\s+/.test(l)))
      frag.append(h('ul', {}, lines.map(l => h('li', {}, inline(l.replace(/^\s*[-*•]\s+/, ''))))));
    else if (lines.every(l => /^\s*\d+[.)]\s+/.test(l)))
      frag.append(h('ol', {}, lines.map(l => h('li', {}, inline(l.replace(/^\s*\d+[.)]\s+/, ''))))));
    else
      frag.append(h('p', {}, lines.flatMap((l, i) => i ? [h('br'), ...inline(l)] : inline(l))));
  }
  return frag;
}

/* ───────────── auth ───────────── */
let authMode = 'login';
function setAuthMode(mode) {
  authMode = mode;
  $('#seg-login').classList.toggle('active', mode === 'login');
  $('#seg-signup').classList.toggle('active', mode === 'signup');
  $('#signup-extra').hidden = mode !== 'signup';
  $('#auth-submit').textContent = mode === 'login' ? 'Log in' : 'Create account';
  $('#auth-pass').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('#auth-error').hidden = true;
}
async function submitAuth() {
  const username = $('#auth-user').value.trim().toLowerCase();   // server stores usernames lower-case
  const password = $('#auth-pass').value;
  const err = $('#auth-error'); err.hidden = true;
  if (username.length < 3 || password.length < 8) {
    err.textContent = 'Username needs 3+ characters and password 8+ characters.'; err.hidden = false; return;
  }
  const btn = $('#auth-submit'); btn.disabled = true;
  try {
    if (authMode === 'signup') {
      const role = $('#auth-role').value;
      const body = { username, password, role };
      if (role === 'admin' && $('#auth-code').value) body.admin_code = $('#auth-code').value;
      await api('/auth/signup', { method: 'POST', json: body, noAuth: true });
    }
    const tok = await api('/auth/login', { method: 'POST', form: new URLSearchParams({ username, password }), noAuth: true });
    state.token = tok.access_token; state.user = { username: tok.username, role: tok.role };
    sessionStorage.setItem('medirag:auth', JSON.stringify({ token: state.token, user: state.user }));
    $('#auth-pass').value = '';
    showApp();
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
  } finally { btn.disabled = false; }
}
function logout(message) {
  stopPolling(); clearInterval(state.usageTimer); state.usageTimer = null; clearInterval(state.docsTimer); state.docsTimer = null;
  state.token = null; state.user = null;
  sessionStorage.removeItem('medirag:auth');
  $('#app-view').hidden = true; $('#auth-view').hidden = false;
  setAuthMode('login');
  if (message) { const e = $('#auth-error'); e.textContent = message; e.hidden = false; }
}

/* ───────────── shell ───────────── */
function showApp() {
  $('#auth-view').hidden = true; $('#app-view').hidden = false;
  const isAdmin = state.user.role === 'admin';
  $('#user-chip').textContent = `${state.user.username} · ${state.user.role}`;
  // Users only chat. Documents, usage analytics and evaluation are admin-only.
  for (const id of ['#tab-docs-btn', '#tab-usage-btn', '#tab-eval-btn']) $(id).hidden = !isAdmin;
  switchTab('chat'); loadHealth(); initChat();
}
function switchTab(name) {
  if (name !== 'chat' && state.user.role !== 'admin') name = 'chat';       // defence in depth; the API enforces it too
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('active', b.dataset.tab === name);
  for (const t of ['chat', 'docs', 'usage', 'eval']) $('#tab-' + t).hidden = t !== name;
  clearInterval(state.usageTimer); state.usageTimer = null;
  clearInterval(state.docsTimer); state.docsTimer = null;
  if (name === 'docs') { loadDocCaps(); loadDocs(); }
  if (name === 'usage') { loadUsage(); state.usageTimer = setInterval(loadUsage, 30000); }
  if (name === 'eval') loadEval();
}
async function loadHealth() {
  const b = $('#health-badge');
  try {
    const r = await api('/health', { noAuth: true });
    const f = r.features || {};
    b.textContent = `● online · ${f.llm_provider || ''}`;
    b.className = 'badge good';
    b.title = Object.entries(f).map(([k, v]) => `${k}: ${v}`).join('\n');
  } catch { b.textContent = '● offline'; b.className = 'badge bad'; }
}

/* ───────────── chat ───────────── */
const sessKey = () => `medirag:sessions:${state.user.username}`;
function initChat() {
  try { state.sessions = JSON.parse(localStorage.getItem(sessKey()) || '[]'); } catch { state.sessions = []; }
  newChat();
}
function saveSessions() { try { localStorage.setItem(sessKey(), JSON.stringify(state.sessions.slice(0, 40))); } catch { /* storage may be unavailable */ } }
function renderSessions() {
  const ul = clear($('#session-list'));
  for (const s of state.sessions)
    ul.append(h('li', { class: s.id === state.sessionId ? 'active' : '', title: s.title, text: s.title, onclick: () => openSession(s.id) }));
}
function newChat() {
  state.sessionId = uuid(); renderSessions(); renderMessages([]); $('#chat-input').focus();
}
async function openSession(id) {
  state.sessionId = id; renderSessions();
  try {
    const r = await api('/chat/history/' + encodeURIComponent(id));
    renderMessages(r.history.map(m => ({ kind: m.role === 'user' ? 'user' : 'bot', text: m.content })));
  } catch (e) { toast(e.message); }
}
const EXAMPLES = ['What are the common symptoms of type 2 diabetes?', 'What lifestyle changes help lower blood pressure?', 'What are the warning signs of a stroke?'];
function renderMessages(list) {
  const box = clear($('#messages'));
  if (!list.length) {
    box.append(h('div', { class: 'empty' },
      h('h2', { text: 'Ask about your documents' }),
      h('p', { text: 'Answers are grounded in the documents uploaded to this system and checked by safety guardrails.' }),
      h('div', { class: 'examples' }, EXAMPLES.map(q => h('button', { class: 'btn', type: 'button', text: q,
        onclick: () => { $('#chat-input').value = q; updateCount(); sendMessage(); } })))));
    return;
  }
  list.forEach(m => box.append(bubble(m)));
  box.scrollTop = box.scrollHeight;
}
function bubble(m) {
  const el = h('div', { class: `msg ${m.kind}` });
  const body = h('div', { class: 'content' });
  if (m.kind === 'user') body.append(h('p', { text: m.text }));
  else body.append(renderMd(m.text));
  el.append(body);
  decorate(el, m);
  return el;
}
/* sources chips + meta line (also used when a streamed answer completes) */
function decorate(el, m) {
  if (m.sources && m.sources.length) {
    const preview = h('div', { class: 'src-preview', hidden: true });
    el.append(h('div', { class: 'sources' }, m.sources.map(s => h('button', { class: 'src', type: 'button', text: '📄 ' + s.filename + (s.page ? ' · p.' + s.page : ''),
      title: s.section || '', onclick: () => { preview.hidden = false; preview.textContent = (s.section ? s.section + ' — ' : '') + (s.chunk_preview || '(no preview)'); } }))), preview);
  }
  if (m.meta) el.append(h('div', { class: 'meta', text: m.meta }));
}
function appendBubble(m) {
  const box = $('#messages');
  if (box.querySelector('.empty')) clear(box);
  const el = bubble(m); box.append(el); box.scrollTop = box.scrollHeight; return el;
}
function updateCount() { $('#char-count').textContent = $('#chat-input').value.length; }
const raf = (fn) => (window.requestAnimationFrame || ((f) => setTimeout(f, 16)))(fn);

/* Server-Sent Events over fetch (EventSource cannot send POST bodies or Authorization headers). */
function parseFrames(buf) {
  const events = []; let i;
  buf = buf.replace(/\r\n/g, '\n');
  while ((i = buf.indexOf('\n\n')) >= 0) {
    const frame = buf.slice(0, i); buf = buf.slice(i + 2);
    let name = 'message'; const data = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    }
    if (data.length) events.push({ event: name, data: JSON.parse(data.join('\n')) });
  }
  return { events, rest: buf };
}
async function* sseEvents(res) {
  if (!res.body || !res.body.getReader) {                // very old browser: no streaming, parse it all at the end
    yield* parseFrames((await res.text()) + '\n\n').events;
    return;
  }
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const out = parseFrames(buf); buf = out.rest;
      yield* out.events;
    }
  } finally { try { reader.releaseLock(); } catch { /* already released */ } }
}

function setSending(on) {
  state.sending = on;
  const b = $('#chat-send');
  b.textContent = on ? '■ Stop' : 'Send';
  b.classList.toggle('danger', on);
  b.title = on ? 'Stop generating' : '';
  $('#messages').setAttribute('aria-busy', on ? 'true' : 'false');   // screen readers wait for the finished answer
}
function stopGenerating() { if (state.abort) state.abort.abort(); }

async function sendMessage() {
  if (state.sending) { stopGenerating(); return; }               // the button doubles as "Stop"
  const input = $('#chat-input'); const text = input.value.trim();
  if (!text) return;
  setSending(true);
  input.value = ''; updateCount();
  appendBubble({ kind: 'user', text });
  if (!state.sessions.find(s => s.id === state.sessionId)) {
    state.sessions.unshift({ id: state.sessionId, title: text.slice(0, 40) }); saveSessions(); renderSessions();
  }
  const msgEl = appendBubble({ kind: 'bot', text: '' });
  const dots = h('span', { class: 'typing' }, h('i'), h('i'), h('i'));
  msgEl.querySelector('.content').append(dots);
  const box = $('#messages');
  state.abort = new AbortController();

  let answer = '', meta = null, final = null, painted = false;
  const paint = () => {
    painted = false;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 90;     // don't yank the view if the user scrolled up
    const c = clear(msgEl.querySelector('.content')); c.append(renderMd(answer));
    if (near) box.scrollTop = box.scrollHeight;
  };
  const schedule = () => { if (!painted) { painted = true; raf(paint); } };

  try {
    const res = await api('/chat/stream', { method: 'POST', json: { message: text, session_id: state.sessionId }, raw: true, signal: state.abort.signal });
    for await (const { event, data } of sseEvents(res)) {
      if (event === 'meta') meta = data;
      else if (event === 'token') { if (!answer) msgEl.classList.add('streaming'); answer += data.text; schedule(); }
      else if (event === 'replace') { answer = data.text; schedule(); }
      else if (event === 'done') final = data;
      else if (event === 'error') throw new ApiError(500, data.detail || 'Generation failed');
    }
    if (final) {                                              // authoritative text = exactly what the server saved
      answer = final.answer; paint(); msgEl.classList.remove('streaming');
      const ft = final.first_token_ms != null ? ` · first word in ${(final.first_token_ms / 1000).toFixed(1)} s` : '';
      decorate(msgEl, { kind: 'bot', sources: final.sources, meta: final.guardrail_passed
        ? `${final.model_used} · ${final.retrieval_method} retrieval${ft}` : 'Blocked by safety guardrails' });
      if (!final.guardrail_passed) msgEl.classList.add('warn');
    } else if (!answer) { msgEl.remove(); }
  } catch (e) {
    dots.remove(); msgEl.classList.remove('streaming');
    if (e.name === 'AbortError') {                            // user pressed Stop
      paint();
      if (!answer) msgEl.remove();
      else decorate(msgEl, { kind: 'bot', meta: 'Stopped — this partial answer was not saved to your chat history.' });
    } else if (e.status === 401) { /* api() already signed the user out */ }
    else {
      const refused = e.status === 422 && e.data && typeof e.data.detail === 'string';
      if (answer) { paint(); decorate(msgEl, { kind: 'bot', meta: 'The answer was cut short.' }); }
      else msgEl.remove();
      appendBubble({ kind: refused ? 'warn' : 'err', text: refused ? e.message : `Something went wrong: ${e.message}`,
        meta: refused ? 'Blocked by safety guardrails' : '' });
    }
  } finally { state.abort = null; setSending(false); input.focus(); }
}

/* ───────────── documents (admin) ───────────── */
async function loadDocCaps() {
  try { state.docCaps = await api('/documents/capabilities'); } catch { return; }
  const c = state.docCaps;
  $('#doc-file').setAttribute('accept', c.extensions.join(','));
  $('#doc-types').textContent = `Allowed: ${c.extensions.join(' ')} · max ${c.max_upload_mb} MB`;
  $('#doc-parser').value = c.default_mode;
  const deep = $('#doc-parser').querySelector('option[value=deep]');
  deep.disabled = !c.docling_installed;
  const note = $('#doc-docling-note');
  note.hidden = c.docling_installed;
  note.textContent = 'Docling is not installed on the server: scanned PDFs, tables, DOCX / PPTX / HTML / images are unavailable. See the README ("Document parsing").';
}
const STATUS_CLASS = { ready: 'good', processing: 'na', failed: 'bad' };
async function loadDocs() {
  const tb = clear($('#docs-table tbody'));
  let docs;
  try { docs = (await api('/documents/list')).documents; } catch (e) { if (e.status !== 401) toast(e.message); return; }
  $('#docs-empty').hidden = docs.length > 0;
  for (const d of docs) {
    const fileCell = h('td', {}, d.filename,
      d.note ? h('div', { class: 'small muted', text: d.note }) : null,
      d.status === 'failed' && d.error ? h('div', { class: 'small', style: 'color:var(--bad)', text: d.error }) : null);
    tb.append(h('tr', {}, fileCell,
      h('td', {}, h('span', { class: 'badge', text: d.role === 'admin' ? 'Admins only' : 'Everyone' })),
      h('td', {}, h('span', { class: 'pill ' + (STATUS_CLASS[d.status] || 'na'), text: d.status === 'processing' ? 'processing…' : d.status })),
      h('td', { text: d.pages ?? '—' }), h('td', { text: d.parser || '—' }), h('td', { text: d.status === 'ready' ? d.chunk_count : '—' }),
      h('td', { text: d.uploaded_by }), h('td', { text: when(d.created_at) }),
      h('td', {}, d.status === 'processing' ? null : h('button', { class: 'btn danger', type: 'button', text: 'Delete', onclick: () => deleteDoc(d) }))));
  }
  // keep refreshing while anything is still being parsed
  const busy = docs.some(d => d.status === 'processing');
  if (busy && !state.docsTimer) state.docsTimer = setInterval(loadDocs, 3000);
  if (!busy && state.docsTimer) { clearInterval(state.docsTimer); state.docsTimer = null; }
}
async function uploadDoc() {
  const file = $('#doc-file').files[0]; const st = $('#upload-status');
  if (!file) { st.textContent = 'Choose a file first.'; return; }
  const fd = new FormData(); fd.append('file', file); fd.append('role', $('#doc-role').value); fd.append('parser', $('#doc-parser').value);
  $('#doc-upload').disabled = true; st.textContent = 'Uploading…';
  try {
    const r = await api('/documents/upload', { method: 'POST', form: fd });
    st.textContent = `“${r.filename}” received — it is being parsed in the background; this page updates by itself.`;
    $('#doc-file').value = ''; await loadDocs();
  } catch (e) { st.textContent = 'Upload failed: ' + e.message; }
  finally { $('#doc-upload').disabled = false; }
}
async function deleteDoc(d) {
  if (!confirm(`Delete “${d.filename}”? It will be removed from search and the answers can no longer cite it.`)) return;
  try { await api('/documents/' + encodeURIComponent(d.doc_id), { method: 'DELETE' }); toast('Deleted'); loadDocs(); }
  catch (e) { toast(e.message); }
}

/* ───────────── usage dashboard (admin) ───────────── */
const nf = (v) => Number(v || 0).toLocaleString();
const usd = (v) => v == null ? '—' : '$' + Number(v).toFixed(Number(v) !== 0 && Math.abs(v) < 0.01 ? 5 : 4);
const ms = (v) => v ? (v >= 1000 ? (v / 1000).toFixed(1) + ' s' : Math.round(v) + ' ms') : '—';
async function loadUsage() {
  const days = $('#us-range').value;
  try {
    const [sum, users, recent] = await Promise.all([
      api('/admin/usage/summary?days=' + days),
      api('/admin/usage/users?days=' + days),
      api('/admin/usage/recent?limit=50' + (state.usageUser ? '&username=' + encodeURIComponent(state.usageUser) : '')),
    ]);
    renderUsageKpis(sum); renderUsageChart(sum.daily); renderModels(sum.by_model);
    renderUsers(users.users); renderRecent(recent.requests);
  } catch (e) { if (e.status !== 401) toast('Could not load usage: ' + e.message); }
}
function renderUsageKpis(s) {
  const t = s.totals, u = s.users;
  const k = (label, value, sub, cls = '') => h('div', { class: 'kpi ' + cls }, h('div', { class: 'v', text: value }),
    h('div', { class: 'l', text: label }), sub ? h('div', { class: 's', text: sub }) : null);
  const box = clear($('#us-kpis'));
  box.append(
    k('Registered users', nf(u.regular), `+ ${u.admins} admin${u.admins === 1 ? '' : 's'}${u.disabled ? ` · ${u.disabled} disabled` : ''}`),
    k('Active users', nf(t.active_users), 'sent ≥ 1 request in range'),
    k('Requests', nf(t.requests), `${nf(t.blocked)} blocked · ${nf(t.errors)} errors` + (t.cancelled ? ` · ${nf(t.cancelled)} stopped` : '')),
    k('LLM tokens', nf(t.total_tokens), `${nf(t.prompt_tokens)} in · ${nf(t.completion_tokens)} out`),
    k('Est. LLM cost', usd(t.cost_usd), t.unpriced_requests ? `${t.unpriced_requests} request(s) on an unpriced model` : 'estimate'),
    k('Avg response time', ms(t.avg_latency_ms), 'successful answers'));
}
function renderUsageChart(daily) {
  const box = clear($('#us-chart'));
  const max = Math.max(1, ...daily.map(d => d.requests));
  box.append(h('div', { class: 'chart', role: 'img', 'aria-label': 'Requests per day' },
    daily.map(d => h('div', { class: 'col', title: `${d.date}: ${d.requests} request(s), ${nf(d.tokens)} tokens, ${d.users} user(s)` },
      h('div', { class: 'b', style: `height:${d.requests ? Math.max(3, d.requests / max * 100) : 0}%` })))));
  if (daily.length) box.append(h('div', { class: 'chart-x' }, h('span', { text: daily[0].date }),
    h('span', { text: `peak ${nf(max)} / day` }), h('span', { text: daily[daily.length - 1].date })));
}
function renderModels(rows) {
  const tb = clear($('#us-models tbody'));
  if (!rows.length) { tb.append(h('tr', {}, h('td', { colspan: 4, class: 'muted', text: 'No requests in this range.' }))); return; }
  for (const m of rows)
    tb.append(h('tr', {}, h('td', { text: m.model === '-' ? '(blocked / failed)' : m.model, title: m.provider }),
      h('td', { class: 'num', text: nf(m.requests) }), h('td', { class: 'num', text: nf(m.total_tokens) }), h('td', { class: 'num', text: usd(m.cost_usd) })));
}
function renderUsers(rows) {
  const tb = clear($('#us-users tbody'));
  for (const u of rows) {
    const self = u.username === state.user.username;
    const btn = self ? h('span', { class: 'muted small', text: 'you' }) : h('button', {
      class: 'btn ' + (u.is_active ? 'danger' : ''), type: 'button', text: u.is_active ? 'Disable' : 'Enable',
      onclick: () => toggleUser(u) });
    tb.append(h('tr', {},
      h('td', { class: 'u', title: 'Show this user\'s recent requests', text: u.username, onclick: () => { state.usageUser = u.username; loadUsage(); } }),
      h('td', {}, h('span', { class: 'badge', text: u.role })),
      h('td', {}, h('span', { class: 'pill ' + (u.is_active ? 'good' : 'bad'), text: u.is_active ? 'active' : 'disabled' })),
      h('td', { class: 'num', text: nf(u.requests) }), h('td', { class: 'num', text: `${nf(u.blocked)} / ${nf(u.errors)}` }),
      h('td', { class: 'num', text: nf(u.prompt_tokens) }), h('td', { class: 'num', text: nf(u.completion_tokens) }),
      h('td', { class: 'num', text: nf(u.total_tokens) }), h('td', { class: 'num', text: usd(u.cost_usd) }),
      h('td', { class: 'num', text: ms(u.avg_latency_ms) }),
      h('td', { text: u.last_request ? when(u.last_request) : '—' }), h('td', { text: u.last_login ? when(u.last_login) : 'never' }),
      h('td', {}, btn)));
  }
}
function renderRecent(rows) {
  const f = clear($('#us-filter'));
  if (state.usageUser) f.append('· ', h('strong', { text: state.usageUser }), ' ',
    h('button', { class: 'btn ghost', type: 'button', text: '✕ show all', onclick: () => { state.usageUser = null; loadUsage(); } }));
  const tb = clear($('#us-recent tbody'));
  if (!rows.length) { tb.append(h('tr', {}, h('td', { colspan: 7, class: 'muted', text: 'No requests yet.' }))); return; }
  for (const r of rows)
    tb.append(h('tr', {}, h('td', { text: when(r.created_at) }), h('td', { text: r.username }),
      h('td', {}, h('span', { class: 'pill ' + (r.status === 'ok' ? 'good' : (r.status === 'blocked' || r.status === 'cancelled') ? 'na' : 'bad'), text: r.status === 'cancelled' ? 'stopped' : r.status })),
      h('td', { text: r.model === '-' ? '—' : r.model }), h('td', { class: 'num', text: nf(r.total_tokens) }),
      h('td', { class: 'num', text: ms(r.latency_ms) }), h('td', { class: 'num', text: usd(r.cost_usd) })));
}
async function toggleUser(u) {
  const enable = !u.is_active;
  if (!enable && !confirm(`Disable "${u.username}"? They will be signed out immediately and cannot log in until re-enabled.`)) return;
  try {
    await api(`/admin/users/${encodeURIComponent(u.username)}/active`, { method: 'PATCH', json: { active: enable } });
    toast(`${u.username} ${enable ? 'enabled' : 'disabled'}`); loadUsage();
  } catch (e) { toast(e.message); }
}

/* ───────────── evaluation ───────────── */
async function loadEval() {
  try {
    state.opts = await api('/evaluation/options');
  } catch (e) { toast(e.message); return; }
  const o = state.opts;
  const sel = $('#ev-dataset'); const prev = sel.value; clear(sel);
  for (const d of o.datasets)
    sel.append(h('option', { value: d.name, text: `${d.name} — ${d.n_cases} cases (${d.with_reference} with reference)` }));
  if (prev && o.datasets.find(d => d.name === prev)) sel.value = prev;
  const jb = $('#judge-badge');
  jb.textContent = o.judge_configured ? 'Judge LLM ready' : 'No judge LLM key';
  jb.className = 'badge ' + (o.judge_configured ? 'good' : 'bad');
  $('#ev-guard').checked = o.guardrails_enabled;
  renderMetricPicker(o);
  if (o.active_job && !state.job) trackJob(o.active_job);
  loadRuns();
}
function renderMetricPicker(o) {
  const root = clear($('#ev-metrics'));
  for (const g of o.groups) {
    const items = o.metrics.filter(m => m.group === g);
    const head = h('input', { type: 'checkbox', checked: true, 'aria-label': g });
    const boxes = items.map(m => h('input', { type: 'checkbox', checked: true, value: m.name, 'data-metric': m.name }));
    const sync = () => { const n = boxes.filter(b => b.checked).length; head.checked = n === boxes.length; head.indeterminate = n > 0 && n < boxes.length; };
    head.addEventListener('change', () => { boxes.forEach(b => { b.checked = head.checked; }); });
    boxes.forEach(b => b.addEventListener('change', sync));
    root.append(h('div', { class: 'mgroup' },
      h('label', { class: 'check mhead' }, head, g),
      h('div', { class: 'mlist' }, items.map((m, i) =>
        h('label', { class: 'check', title: m.description }, boxes[i], m.name + (m.lower_is_better ? ' ↓' : ''))))));
  }
}
function selectedMetrics() {
  const all = [...document.querySelectorAll('#ev-metrics input[data-metric]')];
  const on = all.filter(b => b.checked).map(b => b.value);
  return on.length === all.length ? ['all'] : on;
}
function setBusy(busy) {
  for (const id of ['#ev-run', '#ev-synth']) $(id).disabled = busy;
}
async function startRun() {
  const err = clear($('#ev-error')); err.hidden = true;
  const metrics = selectedMetrics();
  if (!metrics.length) { err.textContent = 'Select at least one metric.'; err.hidden = false; return; }
  const body = { dataset: $('#ev-dataset').value, metrics, threshold: parseFloat($('#ev-threshold').value || '0.5'),
    use_guardrails: $('#ev-guard').checked, eval_raw: $('#ev-raw').checked };
  const lim = parseInt($('#ev-limit').value, 10); if (lim > 0) body.limit = lim;
  setBusy(true);
  try { trackJob((await api('/evaluation/run', { method: 'POST', json: body })).id); }
  catch (e) { err.textContent = e.message; err.hidden = false; setBusy(false); }
}
async function startSynth() {
  const err = clear($('#ev-error')); err.hidden = true;
  setBusy(true);
  try { trackJob((await api('/evaluation/datasets/synthesize', { method: 'POST', json: { n: parseInt($('#ev-synth-n').value, 10) || 15 } })).id); }
  catch (e) { err.textContent = e.message; err.hidden = false; setBusy(false); }
}
function stopPolling() { clearInterval(state.pollTimer); state.pollTimer = null; state.job = null; }
function trackJob(id) {
  stopPolling(); state.job = id; setBusy(true);
  $('#ev-job').hidden = false;
  const tick = async () => {
    let j;
    try { j = await api('/evaluation/jobs/' + id); }
    catch (e) { stopPolling(); setBusy(false); $('#ev-job').hidden = true; if (e.status !== 401) { const er = $('#ev-error'); er.textContent = 'Lost track of the job: ' + e.message; er.hidden = false; } return; }
    const label = j.kind === 'evaluate' ? 'Evaluating' : 'Generating test set';
    $('#ev-job-text').textContent = `${label}: ${j.done}/${j.total || '?'} — ${j.message || j.status}`;
    $('#ev-job-bar').style.width = (j.total ? Math.max(4, j.done / j.total * 100) : 8) + '%';
    if (j.status === 'done' || j.status === 'error') {
      stopPolling(); setBusy(false); $('#ev-job').hidden = true;
      if (j.status === 'error') { const er = $('#ev-error'); er.textContent = j.error || 'Job failed'; er.hidden = false; return; }
      if (j.kind === 'evaluate') { toast('Evaluation finished'); await loadRuns(); showReport(j.report_id); }
      else { toast(`Test set created: ${j.dataset}`); await loadEval(); $('#ev-dataset').value = j.dataset; }
    }
  };
  state.pollTimer = setInterval(tick, 1500); tick();
}
async function loadRuns() {
  const box = clear($('#ev-runs'));
  try {
    const { reports } = await api('/evaluation/reports');
    if (!reports.length) { box.append(h('p', { class: 'muted', text: 'No runs yet. Start one on the left.' })); return; }
    for (const r of reports) {
      const gm = r.group_means || {};
      box.append(h('div', { class: 'run' + (r.id === state.reportId ? ' active' : ''), onclick: () => showReport(r.id) },
        h('div', {}, h('strong', { text: when(r.generated_at) }),
          h('div', { class: 'small muted', text: `${r.dataset || ''} · ${r.n_cases} cases · ${r.judge || ''}` }),
          h('div', { class: 'small muted', text: ['retrieval', 'generation', 'safety'].map(g => `${g[0].toUpperCase()}${g.slice(1, 3)} ${num(gm[g])}`).join(' · ') })),
        h('span', { class: 'badge ' + rateClass(r.overall_pass_rate), text: pct(r.overall_pass_rate) + ' pass' })));
    }
  } catch (e) { box.append(h('p', { class: 'alert bad', text: e.message })); }
}
async function download(id, fmt) {
  try {
    const res = await api(`/evaluation/reports/${id}/download?format=${fmt}`, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = h('a', { href: url, download: `${id}.${fmt}` }); document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) { toast(e.message); }
}
async function removeReport(id) {
  if (!confirm('Delete this evaluation run?')) return;
  try { await api('/evaluation/reports/' + id, { method: 'DELETE' }); state.reportId = null; $('#ev-report').hidden = true; loadRuns(); toast('Deleted'); }
  catch (e) { toast(e.message); }
}
async function showReport(id) {
  let data;
  try { data = await api('/evaluation/reports/' + id); } catch (e) { toast(e.message); return; }
  state.reportId = id;
  document.querySelectorAll('.run').forEach(r => r.classList.remove('active'));
  const root = clear($('#ev-report')); root.hidden = false;
  const s = data.summary, c = data.config || {};
  const kpi = (l, v, cls = '') => h('div', { class: 'kpi ' + cls }, h('div', { class: 'v', text: v }), h('div', { class: 'l', text: l }));
  const gm = s.group_means || {};

  const card = h('div', { class: 'card' },
    h('div', { class: 'row between wrap gap' },
      h('div', {}, h('h2', { text: 'Evaluation results' }),
        h('div', { class: 'small muted', text: `${when(s.generated_at)} · judge: ${c.judge || 'n/a'} · dataset: ${c.dataset || 'n/a'}` })),
      h('div', { class: 'row gap' },
        ['md', 'csv', 'json'].map(f => h('button', { class: 'btn', type: 'button', text: '⬇ ' + f.toUpperCase(), onclick: () => download(id, f) })),
        h('button', { class: 'btn danger', type: 'button', text: 'Delete', onclick: () => removeReport(id) }))),
    h('div', { class: 'kpis', style: 'margin-top:14px' },
      kpi('Overall pass rate', pct(s.overall_pass_rate), rateClass(s.overall_pass_rate)),
      kpi('Retrieval (higher=better)', num(gm.retrieval), rateClass(gm.retrieval)),
      kpi('Generation', num(gm.generation), rateClass(gm.generation)),
      kpi('Safety', num(gm.safety), rateClass(gm.safety)),
      kpi('Guardrail accuracy', pct(s.guardrail_accuracy), rateClass(s.guardrail_accuracy)),
      kpi('Cases', String(s.n_cases) + (s.n_errors ? ` (${s.n_errors} errors)` : '')),
      kpi('Latency mean / p95', `${num(s.latency_mean_s, 1)}s / ${num(s.latency_p95_s, 1)}s`)));

  // metric bars
  const mt = h('div', { style: 'margin-top:18px' }, h('h3', { text: 'Metrics' }),
    h('div', { class: 'mrow head' }, h('span', { text: 'Metric' }), h('span', { text: 'Mean score (│ = threshold)' }), h('span', { text: 'Mean' }), h('span', { text: 'Pass' }), h('span', { text: 'n' })));
  const entries = Object.entries(s.metrics || {});
  if (!entries.length) mt.append(h('p', { class: 'muted', text: 'No metric produced a score — see errors below.' }));
  for (const [name, m] of entries) {
    const bar = h('div', { class: 'bar ' + rateClass(m.pass_rate) }, h('i', { style: `width:${Math.max(0, Math.min(1, m.mean ?? 0)) * 100}%` }),
      m.threshold != null ? h('b', { style: `left:${m.threshold * 100}%` }) : null);
    mt.append(h('div', { class: 'mrow', title: `${m.n_skipped} skipped, ${m.n_errors} errors` },
      h('span', {}, name + (m.lower_is_better ? ' ↓' : ''), h('span', { class: 'gtag', text: m.group })),
      bar, h('span', { text: num(m.mean) }), h('span', { class: 'pill ' + rateClass(m.pass_rate), text: pct(m.pass_rate) }), h('span', { text: m.n_scored })));
  }
  card.append(mt);

  // categories
  const cats = Object.entries(s.categories || {});
  if (cats.length) card.append(h('h3', { text: 'By question category' }),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Category', 'Cases', 'Fully passed'].map(t => h('th', { text: t })))),
      h('tbody', {}, cats.map(([k, v]) => h('tr', {}, h('td', { text: k }), h('td', { text: v.cases }), h('td', { text: `${v.passed} / ${v.cases}` })))))));

  // failures
  const fails = [];
  for (const cs of data.cases) for (const [mn, m] of Object.entries(cs.metrics || {})) if (m.success === false) fails.push([cs, mn, m]);
  card.append(h('h3', { text: `Failed metrics (${fails.length})` }));
  if (!fails.length) card.append(h('p', { class: 'muted', text: 'None 🎉' }));
  fails.slice(0, 40).forEach(([cs, mn, m]) => card.append(h('div', { class: 'detail' },
    h('strong', { text: `${cs.id} · ${mn} = ${num(m.score)} (threshold ${num(m.threshold)})` }),
    h('div', { class: 'small', text: cs.input }), h('div', { class: 'small muted', text: m.reason || 'No reason provided' }))));
  if (fails.length > 40) card.append(h('p', { class: 'muted small', text: `…and ${fails.length - 40} more — download the report for the full list.` }));

  // pipeline errors
  const errs = data.cases.filter(cs => cs.error);
  if (errs.length) card.append(h('h3', { text: 'Pipeline errors' }), ...errs.map(cs => h('div', { class: 'alert bad', text: `${cs.id}: ${cs.error}` })));

  // case explorer
  const only = h('input', { type: 'checkbox' });
  const tbody = h('tbody');
  const draw = () => {
    clear(tbody);
    for (const cs of data.cases) {
      const ms = Object.entries(cs.metrics || {});
      const nfail = ms.filter(([, m]) => m.success === false).length;
      if (only.checked && !nfail && !cs.error) continue;
      const tr = h('tr', { class: 'click' }, h('td', { text: cs.id }), h('td', { text: cs.category }),
        h('td', { text: cs.input }),
        h('td', {}, h('span', { class: 'pill ' + (cs.error ? 'bad' : cs.blocked ? 'na' : 'good'), text: cs.error ? 'error' : cs.blocked ? 'blocked' : 'answered' })),
        h('td', {}, h('span', { class: 'pill ' + (nfail ? 'bad' : 'good'), text: nfail ? `${nfail} failed` : 'ok' })));
      let open = null;
      tr.addEventListener('click', () => {
        if (open) { open.remove(); open = null; return; }
        open = h('tr', {}, h('td', { colspan: 5 }, caseDetail(cs, ms)));
        tr.after(open);
      });
      tbody.append(tr);
    }
  };
  only.addEventListener('change', draw);
  card.append(h('div', { class: 'row between' }, h('h3', { text: 'Case explorer' }),
    h('label', { class: 'check' }, only, 'Only cases with failures')),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['ID', 'Category', 'Question', 'Result', 'Metrics'].map(t => h('th', { text: t })))), tbody)));
  draw();
  root.append(card);
  loadRuns();
  root.scrollIntoView && root.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function caseDetail(cs, ms) {
  const d = h('div', { class: 'detail' },
    h('strong', { text: 'Question' }), h('pre', { text: cs.input }),
    cs.expected_output ? [h('strong', { text: 'Reference answer' }), h('pre', { text: cs.expected_output })] : null,
    h('strong', { text: cs.blocked ? `Answer (blocked at ${cs.block_stage} guardrail)` : 'Answer given' }), h('pre', { text: cs.actual_output || '(empty)' }),
    cs.sources && cs.sources.length ? h('div', { class: 'small muted', text: 'Sources: ' + cs.sources.join(', ') }) : null);
  if (cs.retrieval_context && cs.retrieval_context.length)
    d.append(h('details', {}, h('summary', { text: `Retrieved context (${cs.retrieval_context.length} chunks)` }),
      cs.retrieval_context.map(t => h('div', { class: 'chunk', text: t }))));
  d.append(h('table', { style: 'margin-top:10px' }, h('thead', {}, h('tr', {}, ['Metric', 'Score', 'Result', 'Reason'].map(t => h('th', { text: t })))),
    h('tbody', {}, ms.map(([n, m]) => h('tr', {}, h('td', { text: n }), h('td', { text: m.skipped ? '—' : num(m.score) }),
      h('td', {}, h('span', { class: 'pill ' + (m.skipped ? 'na' : m.error ? 'bad' : m.success ? 'good' : 'bad'),
        text: m.skipped ? 'skipped' : m.error ? 'error' : m.success ? 'pass' : 'fail' })),
      h('td', { class: 'small', text: m.skipped || m.error || m.reason || '' }))))));
  return d;
}

/* ───────────── wiring ───────────── */
function init() {
  $('#seg-login').onclick = () => setAuthMode('login');
  $('#seg-signup').onclick = () => setAuthMode('signup');
  $('#auth-role').onchange = (e) => { $('#admin-code-wrap').hidden = e.target.value !== 'admin'; };
  $('#auth-submit').onclick = submitAuth;
  for (const id of ['#auth-user', '#auth-pass', '#auth-code']) $(id).addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });
  $('#logout').onclick = () => logout();
  for (const b of document.querySelectorAll('#tabs button')) b.onclick = () => switchTab(b.dataset.tab);
  $('#new-chat').onclick = newChat;
  $('#chat-send').onclick = sendMessage;
  $('#chat-input').addEventListener('input', updateCount);
  $('#chat-input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  $('#docs-refresh').onclick = loadDocs;
  $('#us-refresh').onclick = loadUsage;
  $('#us-range').onchange = loadUsage;
  $('#doc-upload').onclick = uploadDoc;
  $('#ev-run').onclick = startRun;
  $('#ev-synth').onclick = startSynth;
  $('#ev-reload').onclick = loadRuns;

  try {
    const saved = JSON.parse(sessionStorage.getItem('medirag:auth') || 'null');
    if (saved && saved.token) { state.token = saved.token; state.user = saved.user; showApp(); return; }
  } catch { /* ignore corrupt storage */ }
  $('#auth-view').hidden = false; setAuthMode('login');
}
document.addEventListener('DOMContentLoaded', init);
