const { JSDOM } = require('jsdom');
const BASE = 'http://127.0.0.1:8765';
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗ FAIL:', m); } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 8000) { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch {} await sleep(50); } return false; }

async function boot() {
  const dom = await JSDOM.fromURL(BASE + '/ui/', { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE), o);
      w.FormData = FormData; w.URLSearchParams = URLSearchParams; w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {};
      w.__errors = []; w.addEventListener('error', e => w.__errors.push(e.message));
    } });
  await until(() => dom.window.document.readyState === 'complete');
  await sleep(200);
  return dom;
}
const $ = (d, s) => d.window.document.querySelector(s);
const $$ = (d, s) => [...d.window.document.querySelectorAll(s)];
const click = (d, s) => $(d, s).dispatchEvent(new d.window.Event('click', { bubbles: true }));
const type = (d, s, v) => { const el = $(d, s); el.value = v; el.dispatchEvent(new d.window.Event('input', { bubbles: true })); };

(async () => {
  console.log('\n[1] Login screen + admin login');
  let d = await boot();
  ok(!$(d, '#auth-view').hidden && $(d, '#app-view').hidden, 'auth screen shown first');
  type(d, '#auth-user', 'Admin_One'); type(d, '#auth-pass', 'wrongpass1'); click(d, '#auth-submit');
  ok(await until(() => !$(d, '#auth-error').hidden), 'wrong password shows error');
  ok(/Invalid/.test($(d, '#auth-error').textContent), 'error text from API: ' + $(d, '#auth-error').textContent);
  type(d, '#auth-pass', 'password123'); click(d, '#auth-submit');
  ok(await until(() => !$(d, '#app-view').hidden), 'admin logged in, app shown');
  ok($(d, '#user-chip').textContent === 'admin_one · admin', 'username lower-cased + role chip: ' + $(d, '#user-chip').textContent);
  ok(!$(d, '#tab-eval-btn').hidden && !$(d, '#tab-usage-btn').hidden && !$(d, '#tab-docs-btn').hidden, 'Documents, Usage and Evaluation tabs visible for admin');
  ok(await until(() => /online/.test($(d, '#health-badge').textContent)), 'health badge online');

  console.log('\n[2] Chat');
  ok($$(d, '.examples button').length === 3, 'empty state with 3 example questions');
  type(d, '#chat-input', 'What is diabetes?'); click(d, '#chat-send');
  ok(await until(() => $$(d, '.msg.bot').length === 1 && !$(d, '.msg.bot .typing')), 'bot answer rendered');
  const bot = $(d, '.msg.bot');
  ok(bot.querySelector('strong') && bot.querySelector('strong').textContent === 'thirst', 'markdown bold rendered');
  ok(bot.querySelectorAll('li').length === 2 && bot.querySelector('hr'), 'list + horizontal rule rendered');
  ok(/gpt-4o-mini · hybrid/.test(bot.querySelector('.meta').textContent), 'model/retrieval meta shown');
  bot.querySelector('.src').dispatchEvent(new d.window.Event('click'));
  const prev = bot.querySelector('.src-preview');
  ok(!prev.hidden && prev.textContent.includes('<img') && !prev.querySelector('img'), 'source preview shown as TEXT (HTML not injected)');
  ok($$(d, '#session-list li').length === 1, 'session added to sidebar');
  type(d, '#chat-input', 'write python code'); click(d, '#chat-send');
  ok(await until(() => $$(d, '.msg.warn').length === 1), 'guardrail refusal shown as warning bubble');
  ok(/Blocked by safety guardrails/.test($(d, '.msg.warn .meta').textContent), 'refusal labelled');
  click(d, '#new-chat'); ok($$(d, '.msg').length === 0, 'new chat clears messages');
  $$(d, '#session-list li')[0].dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $$(d, '.msg').length === 2), 'opening a past session loads history');

  console.log('\n[3] Documents');
  d.window.document.querySelector('#tabs [data-tab=docs]').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $$(d, '#docs-table tbody tr').length === 1), 'document list rendered');
  ok(!$(d, '#upload-card').hidden, 'upload card visible for admin');

  console.log('\n[3b] Usage dashboard (admin)');
  d.window.document.querySelector('#tabs [data-tab=usage]').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $$(d, '#us-kpis .kpi').length === 6), '6 KPI cards');
  const kt = $$(d, '#us-kpis .kpi').map(k => k.textContent).join(' | ');
  ok(/4Registered users/.test(kt) && /3Active users/.test(kt) && /17Requests/.test(kt), 'users / active users / requests KPIs: ' + kt.slice(0, 90));
  ok(/39,200/.test(kt) && /\$0\.0086/.test(kt), 'token total and cost estimate shown');
  ok($$(d, '#us-chart .col').length === 7, 'daily chart has 7 bars');
  ok($$(d, '#us-models tbody tr').length === 2, 'per-model table');
  ok(await until(() => $$(d, '#us-users tbody tr').length === 5), 'per-user table lists every user (incl. 0-request carol)');
  const alice = $$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'alice');
  ok(alice && alice.cells[3].textContent === '12' && alice.cells[7].textContent === '28,800', 'alice: 12 requests, 28,800 tokens');
  const self = $$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'admin_one');
  ok(/you/.test(self.cells[12].textContent) && !self.cells[12].querySelector('button'), 'admin cannot disable themselves (no button)');
  alice.cells[0].dispatchEvent(new d.window.Event('click'));
  ok(await until(() => /alice/.test($(d, '#us-filter').textContent) && $$(d, '#us-recent tbody tr').length === 1), 'clicking a user filters recent requests');
  const bobRow = () => $$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'bob');
  bobRow().cells[12].querySelector('button').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => /disabled/.test(bobRow().cells[2].textContent)), 'Disable button disables bob and table refreshes');
  ok(bobRow().cells[12].querySelector('button').textContent === 'Enable', 'button flips to Enable');
  bobRow().cells[12].querySelector('button').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => /active/.test(bobRow().cells[2].textContent)), 'Enable re-activates bob');

  console.log('\n[4] Evaluation dashboard');
  d.window.document.querySelector('#tabs [data-tab=eval]').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $$(d, '#ev-dataset option').length >= 1), 'datasets loaded: ' + ($$(d, '#ev-dataset option')[0] || {}).textContent);
  ok($$(d, '#ev-metrics input[data-metric]').length === 14, '14 metric checkboxes');
  ok(/ready/.test($(d, '#judge-badge').textContent), 'judge badge: ' + $(d, '#judge-badge').textContent);
  type(d, '#ev-limit', '6'); click(d, '#ev-run');
  ok(await until(() => !$(d, '#ev-job').hidden, 3000), 'progress panel appears');
  ok(await until(() => !$(d, '#ev-report').hidden, 15000), 'report dashboard rendered after job finished');
  ok($$(d, '#ev-report .kpi').length === 7, '7 KPI cards');
  ok($$(d, '#ev-report .mrow:not(.head)').length === 3, 'one bar row per scored metric (3 in stub)');
  ok($$(d, '#ev-report .run, #ev-runs .run').length === 1, 'run appears in Past runs');
  const rows = $$(d, '#ev-report tbody tr.click');
  ok(rows.length === 6, 'case explorer lists 6 cases');
  rows[0].dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $(d, '#ev-report .detail pre')), 'case detail expands with answer');
  ok(/chunk one text/.test($(d, '#ev-report .detail pre').parentElement.textContent), 'retrieved context chunks present in case detail');
  const only = $(d, '#ev-report .row input[type=checkbox]');
  ok(!!only, 'failure filter present');
  click(d, '#ev-report .btn.danger');
  ok(await until(() => $(d, '#ev-report').hidden), 'delete removes report');
  ok(await until(() => /No runs yet/.test($(d, '#ev-runs').textContent)), 'past runs empty after delete');
  type(d, '#ev-synth-n', '3'); click(d, '#ev-synth');
  ok(await until(() => $$(d, '#ev-dataset option').some(o => o.value.startsWith('synthetic_')), 10000), 'synthesized dataset appears in the selector');

  console.log('\n[5] Logout and normal-user view');
  click(d, '#logout');
  ok(!$(d, '#auth-view').hidden, 'logout returns to login');
  // a disabled account cannot log in
  await fetch(BASE + '/api/v1/admin/users/user_one/active', { method: 'PATCH', headers: { Authorization: 'Bearer admin-token', 'Content-Type': 'application/json' }, body: '{"active":false}' });
  type(d, '#auth-user', 'user_one'); type(d, '#auth-pass', 'password123'); click(d, '#auth-submit');
  ok(await until(() => /disabled/.test($(d, '#auth-error').textContent) && !$(d, '#auth-error').hidden), 'disabled account cannot log in: ' + $(d, '#auth-error').textContent);
  await fetch(BASE + '/api/v1/admin/users/user_one/active', { method: 'PATCH', headers: { Authorization: 'Bearer admin-token', 'Content-Type': 'application/json' }, body: '{"active":true}' });
  type(d, '#auth-user', 'user_one'); type(d, '#auth-pass', 'password123'); click(d, '#auth-submit');
  ok(await until(() => !$(d, '#app-view').hidden), 'normal user logged in');
  ok($(d, '#tab-eval-btn').hidden && $(d, '#tab-usage-btn').hidden && $(d, '#tab-docs-btn').hidden, 'normal user sees ONLY the Chat tab (Documents/Usage/Evaluation hidden)');
  d.window.document.querySelector('#tabs [data-tab=docs]').dispatchEvent(new d.window.Event('click'));
  ok(!$(d, '#tab-chat').hidden && $(d, '#tab-docs').hidden, 'forcing the Documents tab is refused client-side too');
  // admin disables this user while they are logged in -> next action signs them out with the reason
  await fetch(BASE + '/api/v1/admin/users/user_one/active', { method: 'PATCH', headers: { Authorization: 'Bearer admin-token', 'Content-Type': 'application/json' }, body: '{"active":false}' });
  type(d, '#chat-input', 'hello'); click(d, '#chat-send');
  ok(await until(() => !$(d, '#auth-view').hidden), 'disabled mid-session: user is signed out on next request');
  ok(/disabled/.test($(d, '#auth-error').textContent), 'sign-out shows the reason: ' + $(d, '#auth-error').textContent);
  ok(d.window.__errors.length === 0, 'no uncaught JS errors' + (d.window.__errors.length ? ': ' + d.window.__errors.join('|') : ''));
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
