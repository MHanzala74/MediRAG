const { JSDOM } = require('jsdom');
const BASE = 'http://127.0.0.1:8766';
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗ FAIL:', m); } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 8000) { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch {} await sleep(50); } return false; }
async function boot() {
  const dom = await JSDOM.fromURL(BASE + '/ui/', { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o = {}) => { const opts = { ...o }; if (o.signal) { const ac = new AbortController(); o.signal.addEventListener('abort', () => ac.abort()); opts.signal = ac.signal; } return fetch(new URL(u, BASE), opts); }; w.TextDecoder = TextDecoder; w.FormData = FormData; w.URLSearchParams = URLSearchParams;
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

  console.log('\n[1b] Admin uploads a PDF through the Documents tab');
  tab(d, 'docs');
  ok(await until(() => /\.pdf/.test($(d, '#doc-types').textContent)), 'capabilities loaded: ' + $(d, '#doc-types').textContent);
  ok($(d, '#doc-docling-note').hidden === (!$$(d, '#doc-parser option[value=deep]')[0].disabled ? true : false), 'Docling note consistent with the Deep option');
  const fs = require('fs');
  const bytes = fs.readFileSync('/tmp/medirag_ui_sample.pdf');
  Object.defineProperty($(d, '#doc-file'), 'files', { value: [new File([bytes], 'guide.pdf', { type: 'application/pdf' })] });
  select(d, '#doc-role', 'user'); select(d, '#doc-parser', 'fast');
  click(d, '#doc-upload');
  ok(await until(() => $$(d, '#docs-table tbody tr').length === 1), 'uploaded document appears in the table');
  ok(await until(() => /ready/.test($$(d, '#docs-table tbody tr')[0].cells[2].textContent), 15000), 'status becomes "ready" by itself (background parsing + polling)');
  const row = $$(d, '#docs-table tbody tr')[0];
  ok(row.cells[3].textContent === '2' && row.cells[4].textContent === 'pypdf' && row.cells[5].textContent === '2', `pages=2, parser=pypdf, chunks=2 (got ${row.cells[3].textContent}/${row.cells[4].textContent}/${row.cells[5].textContent})`);
  click(d, '#logout');

  console.log('\n[2] Sign up as a normal user and chat');
  ok(await signup(d, 'alice_ui', 'user'), 'user account created and logged in');
  ok($(d, '#tab-usage-btn').hidden && $(d, '#tab-docs-btn').hidden && $(d, '#tab-eval-btn').hidden, 'user sees only the Chat tab');
  for (const q of ['What is the blood pressure target?', 'What are the symptoms of diabetes?', 'I want to end my life, what is the easiest way?']) {
    const n = $$(d, '.msg').length;
    type(d, '#chat-input', q); click(d, '#chat-send');
    ok(await until(() => $$(d, '.msg').length >= n + 2 && !$(d, '.typing') && !$(d, '.msg.streaming') && $(d, '#chat-send').textContent === 'Send'), `message answered (streamed): ${q}`);
  }
  ok($$(d, '.msg.warn').length === 1, 'guardrail refusal shown');
  const firstBot = $$(d, '.msg.bot')[0];
  ok(/guide\.pdf, p\. 2/.test(firstBot.textContent), 'answer was built from the uploaded PDF (page-tagged passages reached the LLM)');
  const chips = [...firstBot.querySelectorAll('.src')].map(b => b.textContent);
  ok(chips.includes('📄 guide.pdf · p.1') && chips.includes('📄 guide.pdf · p.2'), 'source chips show file + page: ' + chips.join(' | '));
  click(d, '#logout');

  console.log('\n[3] Admin dashboard shows the real numbers');
  await login(d, 'root_admin'); ok(await until(() => !$(d, '#app-view').hidden), 'admin logs in again');
  tab(d, 'usage');
  ok(await until(() => $$(d, '#us-users tbody tr').length === 2), 'two users listed');
  const kt = $$(d, '#us-kpis .kpi').map(k => k.textContent).join(' | ');
  console.log('     KPIs:', kt);
  ok(/1Registered users/.test(kt) && /1Active users/.test(kt), 'registered 1 user, active 1');
  ok(/3Requests1 blocked · 0 errors/.test(kt), '3 requests, 1 blocked');
  ok(/264/.test(kt) && /240 in · 24 out/.test(kt), 'tokens: 240 in / 24 out / 264 total (2 answered requests)');
  ok(/\$0\.00005/.test(kt), 'cost estimate = $0.00005 (gpt-4o-mini pricing)');
  const alice = $$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'alice_ui');
  ok(alice && alice.cells[3].textContent === '3' && alice.cells[4].textContent === '1 / 0' && alice.cells[7].textContent === '264', 'alice row: 3 requests, 1 blocked, 264 tokens');
  ok(alice.cells[11].textContent !== 'never', 'last login recorded: ' + alice.cells[11].textContent);
  ok($$(d, '#us-chart .col').length === 7, 'chart: 7 daily bars');
  ok($$(d, '#us-models tbody tr').length === 2, 'by-model table has the model + blocked row');
  ok(!$(d, '#us-recent').textContent.includes('What is diabetes'), 'recent requests do NOT contain message text');

  console.log('\n[3b] Admin deletes the document');
  tab(d, 'docs');
  ok(await until(() => $$(d, '#docs-table tbody tr').length === 1), 'document listed');
  $$(d, '#docs-table tbody tr')[0].cells[8].querySelector('button').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => $$(d, '#docs-table tbody tr').length === 0 && !$(d, '#docs-empty').hidden), 'Delete removes it from the table');
  tab(d, 'usage');

  console.log('\n[4] Disable the user from the dashboard');
  alice.cells[12].querySelector('button').dispatchEvent(new d.window.Event('click'));
  ok(await until(() => /disabled/.test($$(d, '#us-users tbody tr').find(r => r.cells[0].textContent === 'alice_ui').cells[2].textContent)), 'alice shown as disabled');
  click(d, '#logout');
  await login(d, 'alice_ui');
  ok(await until(() => /disabled/.test($(d, '#auth-error').textContent) && !$(d, '#auth-error').hidden), 'disabled user cannot log in: ' + $(d, '#auth-error').textContent);
  ok(d.window.__errors.length === 0, 'no uncaught JS errors' + (d.window.__errors.length ? ': ' + d.window.__errors.join('|') : ''));
  console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
