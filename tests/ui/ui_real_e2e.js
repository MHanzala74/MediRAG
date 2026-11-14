const { JSDOM } = require('jsdom');
const BASE = 'http://127.0.0.1:8766';
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗ FAIL:', m); } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 8000) { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch {} await sleep(50); } return false; }
async function boot() {
  const dom = await JSDOM.fromURL(BASE + '/ui/', { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.FormData = FormData; w.URLSearchParams = URLSearchParams;
      w.confirm = () => true; w.HTMLElement.prototype.scrollIntoView = () => {}; w.__errors = []; w.addEventListener('error', e => w.__errors.push(e.message)); } });
  await until(() => dom.window.document.readyState === 'complete'); await sleep(200); return dom;
}
const $ = (d, s) => d.window.document.querySelector(s);
const $$ = (d, s) => [...d.window.document.querySelectorAll(s)];
const click = (d, s) => $(d, s).dispatchEvent(new d.window.Event('click', { bubbles: true }));
const type = (d, s, v) => { const el = $(d, s); el.value = v; el.dispatchEvent(new d.window.Event('input', { bubbles: true })); };
const select = (d, s, v) => { const el = $(d, s); el.value = v; el.dispatchEvent(new d.window.Event('change', { bubbles: true })); };
async function signup(d, name, role) {
  click(d, '#seg-signup'); type(d, '#auth-user', name); type(d, '#auth-pass', 'password123'); select(d, '#auth-role', role); click(d, '#auth-submit');
  return until(() => !$(d, '#app-view').hidden);
}
async function login(d, name) { type(d, '#auth-user', name); type(d, '#auth-pass', 'password123'); click(d, '#auth-submit'); }
const tab = (d, n) => d.window.document.querySelector(`#tabs [data-tab=${n}]`).dispatchEvent(new d.window.Event('click'));

(async () => {
  const d = await boot();
  console.log('\n[1] Sign up as admin through the UI (real backend)');
  ok(await signup(d, 'root_admin', 'admin'), 'admin account created and logged in');
  ok(!$(d, '#tab-usage-btn').hidden && !$(d, '#tab-docs-btn').hidden && !$(d, '#tab-eval-btn').hidden, 'admin sees Documents / Usage / Evaluation');
  click(d, '#logout');

  console.log('\n[2] Sign up as a normal user and chat');
  ok(await signup(d, 'alice_ui', 'user'), 'user account created and logged in');
  ok($(d, '#tab-usage-btn').hidden && $(d, '#tab-docs-btn').hidden && $(d, '#tab-eval-btn').hidden, 'user sees only the Chat tab');
  for (const q of ['What is diabetes?', 'What is hypertension?', 'write python code']) {
    const n = $$(d, '.msg').length;
    type(d, '#chat-input', q); click(d, '#chat-send');
    ok(await until(() => $$(d, '.msg').length >= n + 2 && !$(d, '.typing')), `message answered: ${q}`);
  }
  ok($$(d, '.msg.warn').length === 1, 'guardrail refusal shown');
  click(d, '#logout');

  console.log('\n[3] Admin dashboard shows the real numbers');
  await login(d, 'root_admin'); ok(await until(() => !$(d, '#app-view').hidden), 'admin logs in again');
  tab(d, 'usage');
  ok(await until(() => $$(d, '#us-users tbody tr').length === 2), 'two users listed');
  const kt = $$(d, '#us-kpis .kpi').map(k => k.textContent).join(' | ');
  console.log('     KPIs:', kt);
  ok(/1Registered users/.test(kt) && /1Active users/.test(kt), 'registered 1 user, active 1');
  ok(/3Requests1 blocked · 0 errors/.test(kt), '3 requests, 1 blocked');
  ok(/2,400/.test(kt) && /2,000 in · 400 out/.test(kt), 'tokens: 2,000 in / 400 out / 2,400 total (2 answered requests)');
  ok(/\$0\.0005/.test(kt), 'cost estimate = $0.0005 (gpt-4o-mini pricing)');
  const alice = $$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'alice_ui');
  ok(alice && alice.cells[3].textContent === '3' && alice.cells[4].textContent === '1 / 0' && alice.cells[7].textContent === '2,400', 'alice row: 3 requests, 1 blocked, 2,400 tokens');
  ok(alice.cells[11].textContent !== 'never', 'last login recorded: ' + alice.cells[11].textContent);
  ok($$(d, '#us-chart .col').length === 7, 'chart: 7 daily bars');
  ok($$(d, '#us-models tbody tr').length === 2, 'by-model table has the model + blocked row');
  ok(!$(d, '#us-recent').textContent.includes('What is diabetes'), 'recent requests do NOT contain message text');

  console.log('\n[4] Disable the user from the dashboard');
  alice.cells[12].querySelector('button').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => /disabled/.test($$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'alice_ui').cells[2].textContent)), 'alice shown as disabled');
  click(d, '#logout');
  await login(d, 'alice_ui');
  ok(await until(() => /disabled/.test($(d, '#auth-error').textContent) && !$(d, '#auth-error').hidden), 'disabled user cannot log in: ' + $(d, '#auth-error').textContent);
  ok(d.window.__errors.length === 0, 'no uncaught JS errors' + (d.window.__errors.length ? ': ' + d.window.__errors.join('|') : ''));
  console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
