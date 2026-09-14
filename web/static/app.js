/* ===========================================================================
   Dashboard + live A/B demo.

   No build step and no framework on purpose: this has to open from the file
   system on a machine with nothing installed, five minutes before a demo.

   Data comes from /api/results when a server is there, and falls back to the
   baked window.__HARNESS_DATA__ in data.js when it is not. Rendering is
   identical either way -- the fallback is not a degraded mode, it is the same
   dashboard reading the same committed JSON.
   =========================================================================== */
'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const STATE = {
  data: null,
  provider: null,       // which provider's arm pair is on screen
  defectFilter: null,   // a defect id, or null for "all"
  search: '',
  live: null,           // /api/health payload, or null when there is no server
  openQuestions: new Set(),
};

const DEFECT_ORDER = [...Array(10)].map((_, i) => `D${i + 1}`).concat('control');

/* ---------------------------------------------------------------------------
   Boot
   --------------------------------------------------------------------------- */
async function boot() {
  initTheme();

  let data = null;
  try {
    const r = await fetch('/api/results', { cache: 'no-store' });
    if (r.ok) data = await r.json();
  } catch { /* no server: fall through to the baked copy */ }

  if (!data) data = window.__HARNESS_DATA__ || null;
  if (!data) {
    $('#headline').innerHTML =
      '<p class="err">No results found. Run <code>python -m web.build_data</code> ' +
      '(or start <code>python -m web.server</code>) and reload.</p>';
    return;
  }

  STATE.data = data;
  buildRunPicker();
  renderAll();
  wireUi();
  await checkLive();
  applyDeepLink();
}

/* Deep link:  #ask=Q11  or  #ask=any%20free%20text
   Opens straight into the live panel and runs it. Worth having on stage --
   you land on the exact question you meant to show instead of scrolling a
   31-row list and picking the wrong one in front of an audience. */
function applyDeepLink() {
  const m = /(?:^|[#&])ask=([^&]*)/.exec(location.hash || '');
  if (!m || !m[1]) return;

  const raw = decodeURIComponent(m[1].replace(/\+/g, ' ')).trim();
  const q = STATE.data.questions.find((x) => x.id.toLowerCase() === raw.toLowerCase());
  if (q) $('#ask-preset').value = q.id;
  else $('#ask-input').value = raw;

  $('#live').scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!$('#ask-run').disabled) runLive();
}

/* ---------------------------------------------------------------------------
   Run selection. eval/out holds one file per (arm, provider); the dashboard
   compares one PAIR at a time, because comparing a local baseline against a
   gemini harness would be measuring the model, not the harness.
   --------------------------------------------------------------------------- */
function providers() {
  const seen = new Map();
  for (const r of STATE.data.runs) {
    if (r.arm === 'dry') continue;
    const p = seen.get(r.provider) || { provider: r.provider, arms: {} };
    p.arms[r.arm] = r;
    seen.set(r.provider, p);
  }
  return [...seen.values()];
}

/* How many questions both arms of a provider actually cover. A --only slice
   leaves a real-looking file behind with 3 records in it, so "has both arms"
   alone is not enough to pick a default. */
function pairCoverage(p) {
  const arms = Object.values(p.arms);
  if (arms.length < 2) return 0;
  return Math.min(...arms.map((a) => a.summary?.total || 0));
}

function buildRunPicker() {
  const picker = $('#run-picker');
  const provs = providers();
  picker.innerHTML = '';
  for (const p of provs) {
    const n = pairCoverage(p);
    const label = n
      ? `${p.provider} — ${n} questions, both arms`
      : `${p.provider} — ${Object.keys(p.arms)[0]} only`;
    const o = el('option', null, label);
    o.value = p.provider;
    picker.appendChild(o);
  }

  // Default to the MOST COMPLETE pairing, not merely the first complete one.
  // An aborted 3-question API slice would otherwise outrank the full 31-question
  // run and the dashboard would open showing "n/a" against most questions --
  // which reads as a broken page rather than a partial run.
  const best = provs.slice().sort((a, b) => pairCoverage(b) - pairCoverage(a))[0];
  STATE.provider = (best || {}).provider || null;
  picker.value = STATE.provider || '';
  picker.disabled = provs.length < 2;
}

function currentPair() {
  const p = providers().find((x) => x.provider === STATE.provider);
  return { baseline: p?.arms.baseline || null, harness: p?.arms.harness || null };
}

/* ---------------------------------------------------------------------------
   Render
   --------------------------------------------------------------------------- */
function renderAll() {
  renderHeadline();
  renderDefects();
  renderQuestions();
  renderPresets();
  const runs = STATE.data.runs.length;
  $('#foot-meta').textContent =
    `${runs} result file${runs === 1 ? '' : 's'} · ${STATE.data.questions.length} gold questions · ` +
    `snapshot ${STATE.data.generated_at}`;
}

const pct = (c, n) => (n ? (c / n) * 100 : 0);
const fmtPct = (c, n) => (n ? `${pct(c, n).toFixed(1)}%` : '—');

function renderHeadline() {
  const { baseline, harness } = currentPair();
  const bs = baseline?.summary, hs = harness?.summary;

  const tile = (s, valueEl, metaEl, label) => {
    $(valueEl).textContent = s ? `${(s.accuracy * 100).toFixed(1)}%` : '—';
    $(metaEl).textContent = s ? `${s.correct} of ${s.total} correct · ${s.elapsed_s}s total` : `no ${label} run`;
  };
  tile(bs, '#tile-baseline', '#tile-baseline-meta', 'baseline');
  tile(hs, '#tile-harness', '#tile-harness-meta', 'harness');

  const hero = $('#hero-lift');
  if (bs && hs) {
    const d = (hs.accuracy - bs.accuracy) * 100;
    hero.textContent = `${d >= 0 ? '+' : ''}${d.toFixed(1)} pts`;
    hero.classList.toggle('is-negative', d < 0);
    $('#hero-note').textContent =
      `${(bs.accuracy * 100).toFixed(1)}% → ${(hs.accuracy * 100).toFixed(1)}% · ` +
      `${STATE.provider} · ${hs.total} questions`;
  } else {
    hero.textContent = '—';
    $('#hero-note').textContent = 'Needs both arms for the same provider.';
  }

  // The control row is the honesty check: if the harness scores worse on
  // questions with no defect, its tools are costing more context than they
  // earn, and that belongs on screen rather than in a footnote nobody reads.
  const cb = bs?.by_defect?.control, ch = hs?.by_defect?.control;
  const foot = $('#headline-footnote');
  if (cb && ch) {
    const worse = ch.correct < cb.correct;
    foot.textContent = worse
      ? `⚠ Control questions (no defect involved): baseline ${cb.correct}/${cb.n} → harness ` +
        `${ch.correct}/${ch.n}. The harness scored WORSE where the data was clean — the tool ` +
        `output is likely crowding the model's context on questions that never needed it.`
      : `Control questions (no defect involved): ${cb.correct}/${cb.n} → ${ch.correct}/${ch.n}. ` +
        `The harness holds on clean data, so the lift is not bought by hurting the easy cases.`;
  } else {
    foot.textContent = '';
  }
}

/* --- Per-defect grouped bars ------------------------------------------- */
function defectRows() {
  const { baseline, harness } = currentPair();
  const bd = baseline?.summary?.by_defect || {};
  const hd = harness?.summary?.by_defect || {};
  const titles = Object.fromEntries(STATE.data.defects.map((d) => [d.id, d.title]));
  titles.control = 'No defect involved';

  return DEFECT_ORDER
    .filter((id) => bd[id] || hd[id])
    .map((id) => {
      const b = bd[id] || { n: 0, correct: 0 };
      const h = hd[id] || { n: 0, correct: 0 };
      const lift = (b.n && h.n) ? pct(h.correct, h.n) - pct(b.correct, b.n) : null;
      return { id, title: titles[id] || id, b, h, lift };
    })
    // Biggest win first: the story is where the harness earns its keep.
    .sort((x, y) => (y.lift ?? -Infinity) - (x.lift ?? -Infinity));
}

function renderDefects() {
  const host = $('#defect-chart');
  host.innerHTML = '';
  const rows = defectRows();
  if (!rows.length) { host.appendChild(el('p', 'muted', 'No per-defect data in this run.')); return; }

  for (const r of rows) {
    const btn = el('button', 'drow' + (r.id === 'control' ? ' is-control' : ''));
    btn.type = 'button';
    btn.setAttribute('aria-pressed', String(STATE.defectFilter === r.id));
    if (STATE.defectFilter === r.id) btn.classList.add('is-selected');

    const name = el('div', 'dname');
    name.appendChild(document.createTextNode(r.id === 'control' ? 'control' : r.id));
    name.appendChild(el('small', null, r.title));
    btn.appendChild(name);

    const bars = el('div', 'dbars');
    const grid = el('div', 'grid');
    for (const t of [0, 25, 50, 75, 100]) {
      const g = el('div', 'grid-line' + (t === 0 ? ' zero' : ''));
      g.style.left = `${t}%`;
      grid.appendChild(g);
    }
    bars.appendChild(grid);
    bars.appendChild(track('baseline', r.b, r));
    bars.appendChild(track('harness', r.h, r));
    btn.appendChild(bars);

    const liftCls = r.lift == null ? '' : (r.lift > 0.5 ? ' up' : (r.lift < -0.5 ? ' down' : ''));
    btn.appendChild(el('div', 'dlift' + liftCls,
      r.lift == null ? '—' : (Math.abs(r.lift) < 0.5 ? 'no change' : `${r.lift > 0 ? '+' : ''}${r.lift.toFixed(0)} pts`)));

    btn.addEventListener('click', () => {
      STATE.defectFilter = STATE.defectFilter === r.id ? null : r.id;
      renderDefects();
      renderQuestions();
    });
    host.appendChild(btn);
  }

  // Shared axis, once at the bottom. One scale for both series -- never two.
  const axis = el('div', 'axis');
  axis.appendChild(el('div'));
  const ticks = el('div', 'axis-ticks');
  for (const t of [0, 25, 50, 75, 100]) {
    const l = el('div', 'axis-tick', `${t}%`);
    l.style.left = `${t}%`;
    ticks.appendChild(l);
  }
  axis.appendChild(ticks);
  axis.appendChild(el('div'));
  host.appendChild(axis);

  $('#defect-filter-note').textContent = STATE.defectFilter
    ? `Filtered to ${STATE.defectFilter} — select again to clear`
    : '';
  renderDefectTable(rows);
}

function track(arm, s, row) {
  const t = el('div', 'dtrack');
  const fill = el('div', `dfill dfill-${arm}`);
  const value = pct(s.correct, s.n);

  if (!s.n) {
    fill.className = 'dfill zero';
  } else {
    fill.style.width = `${value}%`;
    // A true zero would render as nothing at all, which reads as "no data"
    // rather than "got none right" -- exactly the confusion this project is
    // about. Show a hairline stub instead.
    if (value === 0) fill.style.width = '2px';
  }
  t.appendChild(fill);

  // Value at the tip: the fraction, which is what a reader actually wants
  // from a 3-question bucket ("2/3", not "66.7%").
  if (s.n) {
    const tip = el('div', 'dtip', `${s.correct}/${s.n}`);
    tip.style.left = `${value}%`;
    t.appendChild(tip);
  }

  const label = arm === 'baseline' ? 'Baseline' : 'Harness';
  t.addEventListener('mouseenter', (e) => showTip(e,
    `<b>${row.id} · ${label}</b><br>${s.correct}/${s.n} correct (${fmtPct(s.correct, s.n)})<br>` +
    `<span class="muted">${row.title}</span>`));
  t.addEventListener('mousemove', moveTip);
  t.addEventListener('mouseleave', hideTip);
  return t;
}

/* The table twin: every value in the chart, reachable without colour. */
function renderDefectTable(rows) {
  const host = $('#defect-table');
  host.innerHTML = '';
  const table = el('table', 'data');
  table.innerHTML =
    '<thead><tr><th>Defect</th><th>Baseline</th><th>Harness</th><th>Lift</th>' +
    '<th>What the defect is</th></tr></thead>';
  const tb = el('tbody');
  for (const r of rows) {
    const tr = el('tr');
    tr.appendChild(el('td', null, r.id));
    tr.appendChild(el('td', null, r.b.n ? `${r.b.correct}/${r.b.n} (${fmtPct(r.b.correct, r.b.n)})` : '—'));
    tr.appendChild(el('td', null, r.h.n ? `${r.h.correct}/${r.h.n} (${fmtPct(r.h.correct, r.h.n)})` : '—'));
    tr.appendChild(el('td', null, r.lift == null ? '—' : `${r.lift > 0 ? '+' : ''}${r.lift.toFixed(0)} pts`));
    tr.appendChild(el('td', 'text', r.title));
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  host.appendChild(table);
}

/* --- Questions ---------------------------------------------------------- */
function recordFor(run, qid) {
  return run?.records?.find((r) => r.id === qid) || null;
}

function renderQuestions() {
  const host = $('#question-list');
  host.innerHTML = '';
  const { baseline, harness } = currentPair();
  const needle = STATE.search.trim().toLowerCase();

  const qs = STATE.data.questions.filter((q) => {
    const tags = q.defect_ids.length ? q.defect_ids : ['control'];
    if (STATE.defectFilter && !tags.includes(STATE.defectFilter)) return false;
    if (needle && !(`${q.id} ${q.question}`.toLowerCase().includes(needle))) return false;
    return true;
  });

  if (!qs.length) { host.appendChild(el('p', 'muted', 'No questions match.')); return; }

  for (const q of qs) {
    const b = recordFor(baseline, q.id);
    const h = recordFor(harness, q.id);
    const item = el('div', 'qitem');

    const head = el('button', 'qhead');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(STATE.openQuestions.has(q.id)));
    head.appendChild(el('div', 'qid', q.id));

    const tags = el('div', 'qtags');
    for (const t of (q.defect_ids.length ? q.defect_ids : ['control'])) tags.appendChild(el('span', 'tag', t));
    head.appendChild(tags);

    head.appendChild(el('div', 'qtext', q.question));

    const marks = el('div', 'qmarks');
    marks.appendChild(badge(b?.correct, 'B'));
    marks.appendChild(badge(h?.correct, 'H'));
    head.appendChild(marks);

    const body = el('div', 'qbody');
    if (!STATE.openQuestions.has(q.id)) body.classList.add('hidden');
    buildQuestionBody(body, q, b, h);

    head.addEventListener('click', () => {
      const open = STATE.openQuestions.has(q.id);
      open ? STATE.openQuestions.delete(q.id) : STATE.openQuestions.add(q.id);
      body.classList.toggle('hidden', open);
      head.setAttribute('aria-expanded', String(!open));
    });

    item.appendChild(head);
    item.appendChild(body);
    host.appendChild(item);
  }
}

function badge(correct, who) {
  const label = who === 'B' ? 'Baseline' : 'Harness';
  if (correct == null) {
    const n = el('span', 'badge unknown');
    n.append(el('span', 'glyph', '–'), document.createTextNode(`${label} n/a`));
    n.title = `${label}: this question was not in the loaded run`;
    return n;
  }
  const n = el('span', `badge ${correct ? 'pass' : 'fail'}`);
  n.append(el('span', 'glyph', correct ? '✓' : '✗'),
           document.createTextNode(`${label} ${correct ? 'pass' : 'fail'}`));
  return n;
}

function buildQuestionBody(body, q, b, h) {
  const block = (title, code, note) => {
    const d = el('div');
    const hd = el('h4');
    hd.appendChild(document.createTextNode(title));
    if (note) hd.appendChild(el('span', 'muted', note));
    d.appendChild(hd);
    d.appendChild(el('pre', 'sql mono', code || '(no SQL produced)'));
    return d;
  };

  if (h?.tool_calls?.length) {
    const d = el('div');
    d.appendChild(el('h4', null, 'Harness tool trace'));
    const tr = el('div', 'trace');
    h.tool_calls.forEach((t, i) => tr.appendChild(el('span', 'trace-step', `${i + 1}. ${t}`)));
    d.appendChild(tr);
    body.appendChild(d);
  }

  body.appendChild(block('Gold SQL', q.gold_sql));
  body.appendChild(block('Baseline wrote', b?.final_sql,
    b ? ` · ${b.elapsed_s}s` : ''));
  body.appendChild(block('Harness wrote', h?.final_sql,
    h ? ` · ${h.elapsed_s}s · ${h.steps} step${h.steps === 1 ? '' : 's'}` : ''));

  for (const [who, rec] of [['Baseline', b], ['Harness', h]]) {
    const msg = rec?.error || rec?.mismatch;
    if (msg && !rec.correct) body.appendChild(el('p', 'why', `${who} — ${msg}`));
  }
  if (q.notes) body.appendChild(el('p', 'why', `Why this question is a trap: ${q.notes}`));
}

/* ---------------------------------------------------------------------------
   Live A/B panel
   --------------------------------------------------------------------------- */
function renderPresets() {
  const sel = $('#ask-preset');
  while (sel.options.length > 1) sel.remove(1);
  for (const q of STATE.data.questions) {
    const tags = q.defect_ids.length ? q.defect_ids.join(',') : 'control';
    const o = el('option', null, `${q.id} [${tags}] — ${q.question}`);
    o.value = q.id;
    sel.appendChild(o);
  }
}

async function checkLive() {
  const pill = $('#live-status');
  let health = null;
  try {
    const r = await fetch('/api/health', { cache: 'no-store' });
    if (r.ok) health = await r.json();
  } catch { /* no server */ }

  STATE.live = health;
  const blocked = $('#live-blocked');

  if (!health) {
    pill.className = 'status-pill status-unknown';
    pill.textContent = 'no server';
    blocked.classList.remove('hidden');
    blocked.innerHTML =
      'This page is open from the file system, so the live panel is off. ' +
      'The dashboard above is complete — it reads the committed results. ' +
      'To enable live questions:<ol>' +
      '<li><code>docker compose up -d</code></li>' +
      '<li><code>.venv\\Scripts\\python -m web.server</code></li>' +
      '<li>open <code>http://127.0.0.1:8000</code></li></ol>';
    $('#ask-run').disabled = true;
    return;
  }

  if (health.live_ready) {
    pill.className = 'status-pill status-ok';
    pill.textContent = `${health.llm.model || health.llm.provider} · db ok`;
    blocked.classList.add('hidden');
    $('#ask-run').disabled = false;
  } else {
    pill.className = 'status-pill status-down';
    pill.textContent = health.db.ok ? 'model unreachable' : 'database unreachable';
    blocked.classList.remove('hidden');
    blocked.innerHTML =
      `Live questions need both the database and a model.<br>` +
      `<b>Database:</b> ${health.db.ok ? 'ok' : 'down'} — ${escapeHtml(health.db.detail)}<br>` +
      `<b>Model (${escapeHtml(health.llm.provider)}):</b> ${health.llm.ok ? 'ok' : 'down'} — ` +
      `${escapeHtml(health.llm.detail)}`;
    $('#ask-run').disabled = true;
  }
}

async function runLive() {
  const qid = $('#ask-preset').value;
  const typed = $('#ask-input').value.trim();
  const preset = STATE.data.questions.find((q) => q.id === qid);
  const question = typed || preset?.question;
  if (!question) { $('#ask-input').focus(); return; }

  // A typed question overrides the preset, so it must not be graded against
  // the preset's gold answer -- that would report a confident wrong verdict.
  const gradeAgainst = (typed && preset && typed !== preset.question) ? null : (preset ? qid : null);

  const btn = $('#ask-run');
  btn.disabled = true;
  btn.textContent = 'Running…';

  for (const arm of ['baseline', 'harness']) {
    $(`#body-${arm}`).innerHTML = '';
    $(`#meta-${arm}`).textContent = 'queued';
    $(`#pane-${arm}`).classList.remove('is-running');
  }

  try {
    const resp = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, question_id: gradeAgainst }),
    });
    if (!resp.ok) throw new Error(`server returned ${resp.status}`);
    await consumeSse(resp, handleEvent);
  } catch (e) {
    $('#body-harness').appendChild(el('p', 'err', `Request failed: ${e.message}`));
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run both arms';
    for (const arm of ['baseline', 'harness']) $(`#pane-${arm}`).classList.remove('is-running');
  }
}

/* Minimal SSE reader. EventSource cannot POST, and the question has to go in
   a body, so we parse the stream ourselves. */
async function consumeSse(resp, onEvent) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const chunks = buf.split('\n\n');
    buf = chunks.pop();
    for (const chunk of chunks) {
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data:')) continue;   // ": keep-alive" lands here
        try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* ignore */ }
      }
    }
  }
}

function handleEvent(ev) {
  const arm = ev.arm;
  const body = arm ? $(`#body-${arm}`) : null;

  switch (ev.type) {
    case 'arm_start':
      $(`#pane-${arm}`).classList.add('is-running');
      $(`#meta-${arm}`).innerHTML = '<span class="spinner"></span>';
      break;

    case 'prompting':
      body.appendChild(el('p', 'muted', ev.detail));
      break;

    case 'tool_call': {
      const n = el('div', 'ev ev-tool');
      const head = el('div', 'ev-name');
      head.appendChild(el('span', 'mono', ev.name));
      head.appendChild(el('span', 'muted', `step ${ev.step}`));
      n.appendChild(head);
      const args = Object.entries(ev.args || {})
        .map(([k, v]) => `${k}: ${String(v).slice(0, 160)}`).join(' · ');
      if (args) n.appendChild(el('div', 'ev-args mono', args));
      n.dataset.tool = ev.name;
      n.dataset.step = ev.step;
      body.appendChild(n);
      body.scrollTop = body.scrollHeight;
      break;
    }

    case 'tool_result': {
      // Attach the output to the call that produced it, collapsed. The schema
      // card alone is 8 KB -- expanded by default it would bury everything.
      const owner = [...body.querySelectorAll('.ev-tool')].reverse()
        .find((n) => n.dataset.tool === ev.name && n.dataset.step === String(ev.step));
      const target = owner || body;
      const d = el('details');
      const lines = (ev.content || '').split('\n').length;
      d.appendChild(el('summary', null, `${lines} line${lines === 1 ? '' : 's'} returned`));
      d.appendChild(el('pre', 'mono', ev.content || ''));
      target.appendChild(d);
      body.scrollTop = body.scrollHeight;
      break;
    }

    case 'sql': {
      const d = el('div', 'ev');
      d.appendChild(el('h4', null, 'Final SQL'));
      d.appendChild(el('pre', 'sql mono', ev.sql));
      body.appendChild(d);
      break;
    }

    case 'arm_done': {
      $(`#pane-${arm}`).classList.remove('is-running');
      const bits = [`${ev.elapsed_s}s`];
      if (ev.steps) bits.push(`${ev.steps} step${ev.steps === 1 ? '' : 's'}`);
      if (ev.usage?.total_tokens) bits.push(`${ev.usage.total_tokens.toLocaleString()} tok`);
      $(`#meta-${arm}`).textContent = bits.join(' · ');

      if (ev.error) body.appendChild(el('p', 'err', ev.error));
      if (ev.rows?.length) body.appendChild(rowsTable(ev.rows, ev.row_count));
      else if (!ev.error) body.appendChild(el('p', 'muted', 'Query returned no rows.'));

      if (ev.correct != null) {
        const v = el('div');
        v.appendChild(badge(ev.correct, arm === 'baseline' ? 'B' : 'H'));
        body.appendChild(v);
      }
      body.scrollTop = body.scrollHeight;
      break;
    }

    case 'arm_error':
      $(`#pane-${arm}`).classList.remove('is-running');
      $(`#meta-${arm}`).textContent = 'failed';
      body.appendChild(el('p', 'err', ev.error));
      break;

    case 'done':
      if (!ev.graded) {
        for (const a of ['baseline', 'harness']) {
          $(`#body-${a}`).appendChild(el('p', 'muted',
            'Ungraded — a free-text question has no gold answer to compare against.'));
        }
      }
      break;

    case 'fatal':
      $('#body-harness').appendChild(el('p', 'err', ev.error));
      break;
  }
}

function rowsTable(rows, total) {
  const wrap = el('div', 'rows-table');
  const t = el('table');
  const cols = Object.keys(rows[0] || {});
  const thead = el('thead');
  const hr = el('tr');
  for (const c of cols) hr.appendChild(el('th', null, c));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tb = el('tbody');
  for (const r of rows.slice(0, 50)) {
    const tr = el('tr');
    for (const c of cols) tr.appendChild(el('td', null, r[c] == null ? 'NULL' : String(r[c])));
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  wrap.appendChild(t);
  if (total > rows.length) wrap.appendChild(el('p', 'muted', `… ${total} rows total`));
  return wrap;
}

/* ---------------------------------------------------------------------------
   Plumbing
   --------------------------------------------------------------------------- */
function wireUi() {
  $('#run-picker').addEventListener('change', (e) => {
    STATE.provider = e.target.value;
    renderAll();
  });

  document.querySelectorAll('.seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.seg-btn').forEach((o) => {
        const on = o === b;
        o.classList.toggle('is-on', on);
        o.setAttribute('aria-pressed', String(on));
      });
      const chart = b.dataset.view === 'chart';
      $('#defect-chart').classList.toggle('hidden', !chart);
      $('#defect-table').classList.toggle('hidden', chart);
    });
  });

  let t;
  $('#q-search').addEventListener('input', (e) => {
    clearTimeout(t);
    t = setTimeout(() => { STATE.search = e.target.value; renderQuestions(); }, 120);
  });
  $('#q-clear').addEventListener('click', () => {
    $('#q-search').value = '';
    STATE.search = '';
    STATE.defectFilter = null;
    renderDefects();
    renderQuestions();
  });

  $('#ask-run').addEventListener('click', runLive);
  $('#ask-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !$('#ask-run').disabled) runLive();
  });
  $('#ask-preset').addEventListener('change', () => { $('#ask-input').value = ''; });

  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('harness-theme', next); } catch { /* private mode */ }
  });
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('harness-theme'); } catch { /* private mode */ }
  document.documentElement.dataset.theme =
    saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

const TIP = () => $('#tooltip');
function showTip(e, html) { TIP().innerHTML = html; TIP().classList.add('on'); moveTip(e); }
function moveTip(e) {
  const t = TIP();
  const pad = 14;
  const x = Math.min(e.clientX + pad, innerWidth - t.offsetWidth - 8);
  const y = Math.min(e.clientY + pad, innerHeight - t.offsetHeight - 8);
  t.style.left = `${Math.max(8, x)}px`;
  t.style.top = `${Math.max(8, y)}px`;
}
function hideTip() { TIP().classList.remove('on'); }

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

boot();
