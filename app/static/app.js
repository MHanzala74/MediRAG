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

async function api(path, { method = 'GET', json, form, raw = false, noAuth = false } = {}) {
  const headers = {};
  if (state.token && !noAuth) headers.Authorization = 'Bearer ' + state.token;
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;           // URLSearchParams / FormData set their own content type
  const res = await fetch(API + path, { method, headers, body });
  if (res.status === 401 && !noAuth) {
    logout('Your session expired. Please log in again.');
    throw new ApiError(401, 'Session expired');
  }
  if (raw) {
    if (!res.ok) throw new ApiError(res.status, `Request failed (${res.status})`);
    return res;
  }
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
  stopPolling();
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
  $('#tab-eval-btn').hidden = !isAdmin;
  $('#upload-card').hidden = !isAdmin; $('#upload-note').hidden = isAdmin;
  switchTab('chat'); loadHealth(); initChat();
}
function switchTab(name) {
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('active', b.dataset.tab === name);
  for (const t of ['chat', 'docs', 'eval']) $('#tab-' + t).hidden = t !== name;
  if (name === 'docs') loadDocs();
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
  if (m.kind === 'user') el.append(h('p', { text: m.text }));
  else el.append(renderMd(m.text));
  if (m.sources && m.sources.length) {
    const preview = h('div', { class: 'src-preview', hidden: true });
    el.append(h('div', { class: 'sources' }, m.sources.map(s => h('button', { class: 'src', type: 'button', text: '📄 ' + s.filename,
      onclick: () => { preview.hidden = false; preview.textContent = s.chunk_preview || '(no preview)'; } }))), preview);
  }
  if (m.meta) el.append(h('div', { class: 'meta', text: m.meta }));
  return el;
}
function appendBubble(m) {
  const box = $('#messages');
  if (box.querySelector('.empty')) clear(box);
  const el = bubble(m); box.append(el); box.scrollTop = box.scrollHeight; return el;
}
function updateCount() { $('#char-count').textContent = $('#chat-input').value.length; }
async function sendMessage() {
  const input = $('#chat-input'); const text = input.value.trim();
  if (!text || state.sending) return;
  state.sending = true; $('#chat-send').disabled = true;
  input.value = ''; updateCount();
  appendBubble({ kind: 'user', text });
  if (!state.sessions.find(s => s.id === state.sessionId)) {
    state.sessions.unshift({ id: state.sessionId, title: text.slice(0, 40) }); saveSessions(); renderSessions();
  }
  const typing = appendBubble({ kind: 'bot', text: '' });
  typing.append(h('span', { class: 'typing' }, h('i'), h('i'), h('i')));
  try {
    const r = await api('/chat/query', { method: 'POST', json: { message: text, session_id: state.sessionId } });
    typing.remove();
    appendBubble({ kind: 'bot', text: r.answer, sources: r.sources, meta: `${r.model_used} · ${r.retrieval_method} retrieval` });
  } catch (e) {
    typing.remove();
    if (e.status === 401) return;
    const refused = e.status === 422 && e.data && typeof e.data.detail === 'string';
    appendBubble({ kind: refused ? 'warn' : 'err', text: refused ? e.message : `Something went wrong: ${e.message}`,
      meta: refused ? 'Blocked by safety guardrails' : '' });
  } finally { state.sending = false; $('#chat-send').disabled = false; input.focus(); }
}

/* ───────────── documents ───────────── */
async function loadDocs() {
  const tb = clear($('#docs-table tbody'));
  try {
    const r = await api('/documents/list');
    $('#docs-empty').hidden = r.documents.length > 0;
    for (const d of r.documents)
      tb.append(h('tr', {}, h('td', { text: d.filename }),
        h('td', {}, h('span', { class: 'badge', text: d.role === 'admin' ? 'Admins only' : 'Everyone' })),
        h('td', { text: d.chunk_count }), h('td', { text: d.uploaded_by }), h('td', { text: when(d.created_at) })));
  } catch (e) { toast(e.message); }
}
async function uploadDoc() {
  const file = $('#doc-file').files[0]; const st = $('#upload-status');
  if (!file) { st.textContent = 'Choose a PDF first.'; return; }
  const fd = new FormData(); fd.append('file', file); fd.append('role', $('#doc-role').value);
  $('#doc-upload').disabled = true; st.textContent = 'Uploading and indexing… large PDFs can take a minute.';
  try {
    const r = await api('/documents/upload', { method: 'POST', form: fd });
    st.textContent = `Indexed “${r.filename}” into ${r.chunk_count} chunks.`;
    $('#doc-file').value = ''; loadDocs();
  } catch (e) { st.textContent = 'Upload failed: ' + e.message; }
  finally { $('#doc-upload').disabled = false; }
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
