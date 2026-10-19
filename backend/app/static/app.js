/* Tender Co-Pilot frontend — vanilla JS, no dependencies.
   Talks to the backend REST API mounted at /api (same origin). */

'use strict';

const API = '/api';

/* ---------------- helpers ---------------- */

function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function dash(v) {
  return (v === null || v === undefined || v === '') ? '—' : v;
}

/** INR with Indian digit grouping. Null -> "not published". */
function inr(v) {
  if (v === null || v === undefined || v === '') return 'not published';
  const n = Number(v);
  if (Number.isNaN(n)) return 'not published';
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) +
    ', ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

/** "in 3d 4h" / "OVERDUE by 2d 1h". */
function countdown(iso) {
  if (!iso) return { text: '—', overdue: false };
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return { text: '—', overdue: false };
  let diff = t - Date.now();
  const overdue = diff < 0;
  diff = Math.abs(diff);
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  const s = d > 0 ? d + 'd ' + h + 'h' : h > 0 ? h + 'h ' + m + 'm' : m + 'm';
  return { text: overdue ? 'OVERDUE by ' + s : 'in ' + s, overdue };
}

/** Normalise match score to 0–100 (API may return 0–1 or 0–100). */
function normScore(s) {
  if (s === null || s === undefined || s === '') return null;
  const n = Number(s);
  if (Number.isNaN(n)) return null;
  return n > 1 ? Math.min(n, 100) : Math.max(0, n * 100);
}

function verdictClass(v) {
  const s = String(v || '').toUpperCase().trim();
  if (/NO[\s-]?GO/.test(s)) return 'nogo';
  if (s.includes('CAUTION')) return 'caution';
  if (s.includes('GO')) return 'go';
  return 'unknown';
}

function verdictBadge(v) {
  const label = dash(v) === '—' ? 'UNKNOWN' : String(v).toUpperCase();
  return '<span class="badge ' + verdictClass(v) + '">' + esc(label) + '</span>';
}

function scoreBar(score) {
  const n = normScore(score);
  if (n === null) return '<span class="muted">—</span>';
  return '<span class="score"><span class="bar"><span class="fill" style="width:' +
    Math.round(n) + '%"></span></span><span class="val">' + Math.round(n) + '</span></span>';
}

function statusBadge(st) {
  const s = String(st || '').toUpperCase().trim();
  const cls = s === 'PASS' || s === 'DONE' || s === 'COMPLETE' ? 'go'
    : s === 'FAIL' || s === 'FAILED' || s === 'OVERDUE' ? 'nogo'
    : s === 'UNKNOWN' || s === 'PENDING' || s === 'TODO' ? 'unknown' : 'caution';
  return '<span class="badge ' + cls + '">' + esc(s || 'UNKNOWN') + '</span>';
}

function urgencyChip(dl) {
  const u = String(dl.urgency || deriveUrgency(dl.due_at) || '').toLowerCase();
  const label = u ? u.charAt(0).toUpperCase() + u.slice(1) : '—';
  return '<span class="chip ' + esc(u) + '">' + esc(label) + '</span>';
}

function deriveUrgency(dueAt) {
  if (!dueAt) return 'unknown';
  const t = new Date(dueAt).getTime() - Date.now();
  if (Number.isNaN(t)) return 'unknown';
  if (t < 0) return 'overdue';
  if (t < 86400000) return 'critical';
  if (t < 7 * 86400000) return 'soon';
  return 'upcoming';
}

function asArray(d) {
  if (Array.isArray(d)) return d;
  if (d && Array.isArray(d.items)) return d.items;
  if (d && Array.isArray(d.tenders)) return d.tenders;
  if (d && Array.isArray(d.profiles)) return d.profiles;
  if (d && Array.isArray(d.deadlines)) return d.deadlines;
  if (d && Array.isArray(d.runs)) return d.runs;
  return [];
}

function errorBox(msg) {
  return '<div class="error-box" role="alert">' + esc(msg) + '</div>';
}

/* ---------------- api ---------------- */

async function api(path, opts) {
  const res = await fetch(API + path, Object.assign(
    { headers: { 'Content-Type': 'application/json' } }, opts || {}));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; }
  catch (e) { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.message)) ||
      ('Request failed (HTTP ' + res.status + ')');
    const msgText = Array.isArray(msg)
      ? msg.map((m) => (m && typeof m === 'object' ? (m.msg || JSON.stringify(m)) : String(m))).join('; ')
      : msg;
    throw new Error(msgText);
  }
  return data;
}

const get = (p) => api(p);
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body || {}) });
const put = (p, body) => api(p, { method: 'PUT', body: JSON.stringify(body || {}) });
const del = (p) => api(p, { method: 'DELETE' });

/* ---------------- state & router ---------------- */

const state = {
  profiles: [],
  tenders: [],
  filters: { profile_id: '', verdict: '', category: '', q: '' },
  selectedTenderId: null,
  detailTab: 'brief',
};

const VIEWS = ['dashboard', 'tenders', 'profiles', 'run', 'deadlines', 'ingest'];

function navigate(hash) {
  if (location.hash === hash) { route(); }
  else { location.hash = hash; }
}

function route() {
  const h = (location.hash || '#/dashboard').replace(/^#\/?/, '');
  const parts = h.split('/');
  const view = parts[0] || 'dashboard';
  document.querySelectorAll('#nav button').forEach((b) => {
    const v = b.getAttribute('data-view');
    b.classList.toggle('active', v === view || (view === 'tender' && v === 'tenders'));
  });
  const el = document.getElementById('view');
  window.scrollTo(0, 0);
  if (view === 'tender' && parts[1]) {
    renderTenderDetail(el, parts[1]);
  } else if (VIEWS.includes(view)) {
    ({ dashboard: renderDashboard, tenders: renderTenders, profiles: renderProfiles,
       run: renderRun, deadlines: renderDeadlines, ingest: renderIngest })[view](el);
  } else {
    renderDashboard(el);
  }
}

function setLoading(el, msg) {
  el.innerHTML = '<div class="loading"><span class="spinner"></span>' + esc(msg || 'Loading…') + '</div>';
}

/* ---------------- Dashboard ---------------- */

async function renderDashboard(el) {
  setLoading(el, 'Loading dashboard…');
  const [tendersR, deadlinesR, runsR] = await Promise.allSettled([
    get('/tenders'),
    get('/deadlines?days=7'),
    get('/runs'),
  ]);
  const tenders = tendersR.status === 'fulfilled' ? asArray(tendersR.value) : [];
  const deadlines = deadlinesR.status === 'fulfilled' ? asArray(deadlinesR.value) : [];
  const runs = runsR.status === 'fulfilled' ? asArray(runsR.value) : [];
  const errors = [tendersR, deadlinesR, runsR].filter((r) => r.status === 'rejected')
    .map((r) => r.reason.message);

  const goCount = tenders.filter((t) => verdictClass(t.verdict) === 'go').length;
  const urgent = deadlines.filter((d) => {
    if (!d.due_at) return false;
    const t = new Date(d.due_at).getTime();
    return !Number.isNaN(t) && t >= Date.now();
  });
  const lastRun = runs[0] || null;

  let html = '';
  if (errors.length) html += errorBox('Some dashboard data could not load: ' + errors.join('; '));

  html += '<div class="stats">' +
    statCard(tenders.length, 'Tenders tracked') +
    statCard(urgent.length, 'Open deadlines (≤ 7 days)') +
    statCard(goCount, 'GO verdicts') +
    statCard(lastRun ? (lastRun.verdict ? String(lastRun.verdict).toUpperCase() : 'Done') : '—',
      'Last pipeline run' + (lastRun && lastRun.created_at ? ' · ' + fmtDate(lastRun.created_at) : '')) +
    '</div>';

  html += '<div class="card"><h2>Urgent deadlines</h2>';
  if (!urgent.length) {
    html += '<div class="empty">No deadlines due in the next 7 days.<br><span class="small">Deadlines come from tracked tenders in the backend database.</span></div>';
  } else {
    const sorted = urgent.slice()
      .sort((a, b) => new Date(a.due_at) - new Date(b.due_at)).slice(0, 8);
    html += sorted.map((d) => {
      const cd = countdown(d.due_at);
      return '<div class="deadline-row">' + urgencyChip(d) +
        '<span><strong>' + esc(d.label || d.kind || 'Deadline') + '</strong>' +
        (d.tender_id ? ' <span class="muted small">· tender #' + esc(d.tender_id) + '</span>' : '') + '</span>' +
        '<span class="due"><span class="countdown' + (cd.overdue ? ' overdue' : '') + '">' +
        esc(cd.text) + '</span> · ' + esc(fmtDate(d.due_at)) + '</span></div>';
    }).join('');
  }
  html += '<div class="btn-row"><button type="button" class="btn secondary" data-nav="#/deadlines">All deadlines</button></div></div>';

  html += '<div class="card"><h2>What this shows</h2><p class="muted small" style="margin:0">' +
    'Counts above are computed live from the backend: tracked tenders, their deadlines, ' +
    'and the most recent pipeline run. Figures marked as coming from tender documents ' +
    'carry section/page citations on the tender detail page.</p></div>';

  el.innerHTML = html;
  bindNav(el);
}

function statCard(num, label) {
  return '<div class="stat"><div class="num">' + esc(num) + '</div><div class="label">' +
    esc(label) + '</div></div>';
}

function bindNav(root) {
  root.querySelectorAll('[data-nav]').forEach((b) => {
    b.addEventListener('click', () => navigate(b.getAttribute('data-nav')));
  });
}

/* ---------------- Tenders list ---------------- */

async function renderTenders(el) {
  setLoading(el, 'Loading tenders…');
  let profiles = [];
  try { profiles = asArray(await get('/profiles')); } catch (e) { /* profiles optional */ }
  state.profiles = profiles;
  if (!state.filters.profile_id && profiles.length === 1) state.filters.profile_id = String(profiles[0].id);

  const categories = [...new Set(state.tenders.map((t) => t.category).filter(Boolean))];

  let html = '<div class="card"><h2>Tenders</h2>' +
    '<p class="muted small" style="margin-top:-0.4rem">Tracked in the backend database (seed data or ingested public listings). ' +
    'Match score and verdict are computed for the selected company profile.</p>' +
    '<div class="filters">' +
    '<div class="field"><label for="f-profile">Company profile</label><select id="f-profile">' +
    '<option value="">— none —</option>' +
    profiles.map((p) => '<option value="' + esc(p.id) + '"' +
      (String(state.filters.profile_id) === String(p.id) ? ' selected' : '') + '>' + esc(p.name) + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label for="f-verdict">Verdict</label><select id="f-verdict">' +
    ['', 'GO', 'CAUTION', 'NO-GO', 'UNKNOWN'].map((v) =>
      '<option value="' + v + '"' + (state.filters.verdict === v ? ' selected' : '') + '>' +
      (v === '' ? 'All' : v) + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label for="f-category">Category</label><select id="f-category">' +
    '<option value="">All</option>' +
    categories.map((c) => '<option value="' + esc(c) + '"' +
      (state.filters.category === c ? ' selected' : '') + '>' + esc(c) + '</option>').join('') +
    '</select></div>' +
    '<div class="field search"><label for="f-q">Search</label>' +
    '<input type="text" id="f-q" placeholder="Title, organisation, reference no…" value="' + esc(state.filters.q) + '"></div>' +
    '</div></div>' +
    '<div id="tender-list"></div>';

  el.innerHTML = html;

  const reload = () => loadTenderList(el);
  ['f-profile', 'f-verdict', 'f-category'].forEach((id) => {
    el.querySelector('#' + id).addEventListener('change', (e) => {
      state.filters[id === 'f-profile' ? 'profile_id' : id === 'f-verdict' ? 'verdict' : 'category'] = e.target.value;
      reload();
    });
  });
  let qTimer = null;
  el.querySelector('#f-q').addEventListener('input', (e) => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { state.filters.q = e.target.value; reload(); }, 250);
  });

  await loadTenderList(el);
}

async function loadTenderList(el) {
  const box = el.querySelector('#tender-list');
  box.innerHTML = '<div class="loading"><span class="spinner"></span>Loading tenders…</div>';
  const f = state.filters;
  const qs = new URLSearchParams();
  if (f.profile_id) qs.set('profile_id', f.profile_id);
  if (f.verdict) qs.set('verdict', f.verdict);
  if (f.category) qs.set('category', f.category);
  let tenders;
  try {
    tenders = asArray(await get('/tenders' + (qs.toString() ? '?' + qs.toString() : '')));
  } catch (e) {
    box.innerHTML = errorBox('Could not load tenders: ' + e.message);
    return;
  }
  state.tenders = tenders;

  const q = f.q.trim().toLowerCase();
  const shown = q ? tenders.filter((t) =>
    [t.title, t.organisation, t.ref_no, t.tender_id_external].some((x) =>
      String(x || '').toLowerCase().includes(q))) : tenders;

  if (!shown.length) {
    box.innerHTML = '<div class="card"><div class="empty">No tenders match these filters.<br>' +
      '<span class="small">Add seed data or run ingestion from the Ingest tab.</span></div></div>';
    return;
  }

  box.innerHTML = shown.map((t) => {
    const cd = countdown(t.bid_end_at);
    return '<button type="button" class="tender-card" data-tender="' + esc(t.id) + '">' +
      '<div class="title">' + esc(dash(t.title)) + '</div>' +
      '<div class="meta">' + esc(dash(t.organisation)) +
      (t.ref_no ? ' · Ref: ' + esc(t.ref_no) : '') +
      (t.source ? ' · Source: ' + esc(t.source) : '') + '</div>' +
      '<div class="row">' + verdictBadge(t.verdict) + scoreBar(t.match_score) +
      '<span class="small muted">Bid due: <span class="countdown' + (cd.overdue ? ' overdue' : '') + '">' +
      esc(cd.text) + '</span></span>' +
      '<span class="small muted">' + esc(inr(t.est_value_known === false ? null : t.est_value)) + '</span>' +
      '</div></button>';
  }).join('');

  box.querySelectorAll('[data-tender]').forEach((b) => {
    b.addEventListener('click', () => navigate('#/tender/' + b.getAttribute('data-tender')));
  });
}

/* ---------------- Tender detail ---------------- */

async function renderTenderDetail(el, id) {
  setLoading(el, 'Loading tender…');
  let d;
  try {
    d = await get('/tenders/' + encodeURIComponent(id));
  } catch (e) {
    el.innerHTML = errorBox('Could not load tender: ' + e.message) +
      '<button type="button" class="btn secondary" data-nav="#/tenders">← Back to tenders</button>';
    bindNav(el);
    return;
  }

  const t = d.tender || d;
  const brief = d.brief || (d.raw_json && d.raw_json.brief) || (t.raw_json && t.raw_json.brief) || null;
  const verdicts = asArray(d.verdicts);
  const deadlines = asArray(d.deadlines);
  const bidpack = d.bidpack || null;
  state.selectedTenderId = t.id;

  const emd = t.emd_amount != null && t.emd_amount !== ''
    ? inr(t.emd_amount) + (t.emd_type ? ' (' + esc(t.emd_type) + ')' : '')
    : (t.emd_type ? esc(t.emd_type) + ' — amount not published' : '—');
  const cd = countdown(t.bid_end_at);

  let html = '<button type="button" class="btn secondary small" data-nav="#/tenders" style="margin-bottom:0.8rem">← All tenders</button>' +
    '<div class="card">' +
    '<h1 class="detail-title">' + esc(dash(t.title)) + '</h1>' +
    '<p class="muted small" style="margin-top:0">' + esc(dash(t.organisation)) +
    (t.ref_no ? ' · Ref: ' + esc(t.ref_no) : '') + '</p>' +
    '<div class="row" style="display:flex;gap:0.7rem;align-items:center;flex-wrap:wrap;margin-bottom:1rem">' +
    verdictBadge(t.verdict) + scoreBar(t.match_score) +
    (t.source ? '<span class="small muted">Source: ' + esc(t.source) + '</span>' : '') + '</div>' +
    '<div class="facts">' +
    fact('Category', dash(t.category)) +
    fact('Estimated value', inr(t.est_value_known === false ? null : t.est_value)) +
    fact('EMD', emd) +
    fact('Bid due', fmtDate(t.bid_end_at)) +
    fact('Time left', '<span class="countdown' + (cd.overdue ? ' overdue' : '') + '">' + esc(cd.text) + '</span>') +
    (t.published_at ? fact('Published', fmtDate(t.published_at)) : '') +
    (t.tender_id_external ? fact('Portal tender ID', esc(t.tender_id_external)) : '') +
    '</div>' +
    '<div class="btn-row"><button type="button" class="btn small" id="detail-run">Run pipeline for this tender</button></div>' +
    '<div id="detail-run-note" class="small muted" style="margin-top:0.4rem"></div>' +
    '</div>';

  const tabs = [['brief', 'Brief'], ['eligibility', 'Eligibility'], ['deadlines', 'Deadlines'], ['bidpack', 'Bid Pack']];
  html += '<div class="card"><div class="subtabs" role="tablist">' +
    tabs.map(([k, label]) => '<button type="button" role="tab" data-dtab="' + k + '"' +
      (state.detailTab === k ? ' class="active"' : '') + '>' + label + '</button>').join('') +
    '</div><div id="detail-tab-body"></div></div>';

  el.innerHTML = html;
  bindNav(el);

  const body = el.querySelector('#detail-tab-body');
  const renderTab = () => {
    el.querySelectorAll('[data-dtab]').forEach((b) =>
      b.classList.toggle('active', b.getAttribute('data-dtab') === state.detailTab));
    if (state.detailTab === 'brief') body.innerHTML = renderBrief(brief);
    else if (state.detailTab === 'eligibility') body.innerHTML = renderEligibility(t, verdicts);
    else if (state.detailTab === 'deadlines') body.innerHTML = renderDeadlineList(deadlines, t.id);
    else if (state.detailTab === 'bidpack') {
      body.innerHTML = renderBidPack(bidpack, t.id);
      bindBidPack(body, t.id, bidpack);
    }
  };
  el.querySelectorAll('[data-dtab]').forEach((b) => {
    b.addEventListener('click', () => { state.detailTab = b.getAttribute('data-dtab'); renderTab(); });
  });
  renderTab();

  const runBtn = el.querySelector('#detail-run');
  runBtn.addEventListener('click', async () => {
    const note = el.querySelector('#detail-run-note');
    runBtn.disabled = true;
    note.innerHTML = '<span class="spinner"></span>Running pipeline…';
    try {
      const r = await post('/runs', { profile_id: state.filters.profile_id || null, tender_id: t.id });
      note.innerHTML = 'Run finished: ' + verdictBadge(r.verdict) + ' score ' +
        esc(r.match_score != null ? Math.round(normScore(r.match_score)) : '—');
      setTimeout(() => navigate('#/tender/' + encodeURIComponent(t.id)), 1200);
    } catch (e) {
      note.innerHTML = '<span style="color:var(--nogo)">' + esc('Run failed: ' + e.message) + '</span>';
      runBtn.disabled = false;
    }
  });
}

function fact(k, v) {
  return '<div class="fact"><div class="k">' + esc(k) + '</div><div class="v">' + v + '</div></div>';
}

function prettifyKey(k) {
  return String(k).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ---- Brief tab ---- */

function renderBrief(brief) {
  if (!brief) return '<div class="empty">No brief available yet.<br><span class="small">Run the pipeline for this tender to generate one from the tender document.</span></div>';
  if (Array.isArray(brief)) return renderBriefFacts(brief.map((x, i) =>
    ({ label: x.label || x.key || ('Fact ' + (i + 1)), value: x.value, section: x.section, page: x.page })));
  if (Array.isArray(brief.facts)) return renderBriefFacts(brief.facts);
  if (brief && brief.facts && typeof brief.facts === 'object' && !Array.isArray(brief.facts)) {
    const facts = Object.entries(brief.facts).map(([k, v]) => ({
      label: prettifyKey(k),
      value: (v && typeof v === 'object') ? JSON.stringify(v) : v,
      section: null, page: null
    }));
    let html = renderBriefFacts(facts);
    if (Array.isArray(brief.citations) && brief.citations.length) {
      html += '<h4 style="margin-top:1rem">Sources</h4><ul class="cites">' + brief.citations.map((c) =>
        '<li><span class="muted small">Section ' + esc(dash(c.section)) +
        (c.page != null ? ' · p. ' + esc(c.page) : '') + '</span><br>' +
        esc(dash(c.quote)) + '</li>').join('') + '</ul>';
    }
    return html;
  }
  if (brief && typeof brief === 'object') {
    const rows = Object.entries(brief).filter(([k]) => k !== 'facts')
      .map(([k, v]) => ({ label: k, value: v, section: null, page: null }));
    return renderBriefFacts(rows);
  }
  return '<pre class="dump">' + esc(JSON.stringify(brief, null, 2)) + '</pre>';
}

function renderBriefFacts(facts) {
  if (!facts.length) return '<div class="empty">Brief is empty.</div>';
  return '<div class="table-wrap"><table><thead><tr><th>Fact</th><th>Value</th><th>Source</th></tr></thead><tbody>' +
    facts.map((f) => {
      const cite = [f.section ? 'Section ' + f.section : null,
        f.page != null ? 'p. ' + f.page : null].filter(Boolean).join(' · ') || '—';
      const val = (typeof f.value === 'object') ? JSON.stringify(f.value) : dash(f.value);
      return '<tr><td><strong>' + esc(dash(f.label)) + '</strong></td><td>' + esc(val) + '</td>' +
        '<td class="muted small">' + esc(cite) + '</td></tr>';
    }).join('') + '</tbody></table></div>' +
    '<p class="muted small">Citations refer to the tender document sections/pages the fact was extracted from.</p>';
}

/* ---- Eligibility tab ---- */

function renderEligibility(t, verdicts) {
  let html = '';
  if (t.verdict) {
    html += '<p style="margin-top:0"><strong>Overall:</strong> ' + verdictBadge(t.verdict) +
      (t.verdict_reason ? ' <span class="muted">' + esc(t.verdict_reason) + '</span>' : '') + '</p>';
  }
  if (!verdicts.length) {
    return html + '<div class="empty">No per-criterion eligibility verdicts yet.<br>' +
      '<span class="small">Run the pipeline for this tender to evaluate it against the company profile.</span></div>';
  }
  html += '<div class="table-wrap"><table><thead><tr><th>Criterion</th><th>Status</th><th>Required</th><th>Actual (profile)</th><th>Source</th></tr></thead><tbody>' +
    verdicts.map((v) => '<tr><td><strong>' + esc(dash(v.criterion || v.name)) + '</strong></td>' +
      '<td>' + statusBadge(v.status) + '</td>' +
      '<td>' + esc(dash(v.required)) + '</td>' +
      '<td>' + esc(dash(v.actual)) + '</td>' +
      '<td class="muted small">' + esc(dash(v.citation || v.source)) + '</td></tr>').join('') +
    '</tbody></table></div>';
  return html;
}

/* ---- Deadlines tab ---- */

function renderDeadlineList(deadlines, tenderId) {
  if (!deadlines.length) {
    return '<div class="empty">No deadlines recorded for this tender.</div>';
  }
  const sorted = deadlines.slice().sort((a, b) => new Date(a.due_at) - new Date(b.due_at));
  return sorted.map((d) => {
    const cd = countdown(d.due_at);
    return '<div class="deadline-row">' + urgencyChip(d) +
      '<span><strong>' + esc(d.label || d.kind || 'Deadline') + '</strong>' +
      (d.kind ? ' <span class="muted small">· ' + esc(d.kind) + '</span>' : '') + '</span>' +
      '<span class="due"><span class="countdown' + (cd.overdue ? ' overdue' : '') + '">' +
      esc(cd.text) + '</span> · ' + esc(fmtDate(d.due_at)) + '</span></div>';
  }).join('');
}

/* ---- Bid Pack tab ---- */

function renderBidPack(bidpack, tenderId) {
  const items = (bidpack && Array.isArray(bidpack.items)) ? bidpack.items : [];
  let html = '<p class="muted small" style="margin-top:0">Tick items off as you prepare them. Changes save automatically to the backend.</p>';
  if (!items.length) {
    html += '<div class="empty">No bid-pack checklist yet.<br><span class="small">Run the pipeline for this tender to generate one from the tender document.</span></div>';
    return html;
  }
  html += '<ul class="checklist" id="bidpack-list">' + items.map((it, i) => {
    const done = /^(done|true|complete|completed|yes)$/i.test(String(it.status));
    const label = it.item || it.label || it.name || ('Item ' + (i + 1));
    return '<li><input type="checkbox" id="bp-' + i + '" data-idx="' + i + '"' +
      (done ? ' checked' : '') + '>' +
      '<label for="bp-' + i + '" class="' + (done ? 'done' : '') + '">' + esc(label) + '</label></li>';
  }).join('') + '</ul><div class="save-note" id="bidpack-note"></div>';
  return html;
}

function bindBidPack(root, tenderId, bidpack) {
  const list = root.querySelector('#bidpack-list');
  if (!list) return;
  const note = root.querySelector('#bidpack-note');
  list.addEventListener('change', async () => {
    const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
    const items = boxes.map((b) => {
      const label = list.querySelector('label[for="' + b.id + '"]').textContent;
      b.nextElementSibling.classList.toggle('done', b.checked);
      return { item: label, status: b.checked ? 'done' : 'pending' };
    });
    note.textContent = 'Saving…';
    try {
      await put('/bidpack/' + encodeURIComponent(tenderId), { items });
      note.textContent = 'Saved ✓';
      setTimeout(() => { note.textContent = ''; }, 1500);
    } catch (e) {
      note.textContent = 'Save failed: ' + e.message;
      note.style.color = 'var(--nogo)';
    }
  });
}

/* ---------------- Profiles ---------------- */

const SECTORS = ['works', 'goods', 'services', 'consultancy'];

async function renderProfiles(el) {
  setLoading(el, 'Loading profiles…');
  let profiles;
  try {
    profiles = asArray(await get('/profiles'));
  } catch (e) {
    el.innerHTML = errorBox('Could not load profiles: ' + e.message);
    return;
  }
  state.profiles = profiles;

  let html = '<div class="card"><h2>Company profiles</h2>' +
    '<p class="muted small" style="margin-top:-0.4rem">Eligibility verdicts and match scores are computed against the selected profile. Keep turnover, certifications and experience up to date.</p>';
  if (!profiles.length) {
    html += '<div class="empty">No company profiles yet. Create one below to start matching tenders.</div>';
  } else {
    html += '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Sector</th><th>Turnover (3-yr avg)</th><th>Flags</th><th></th></tr></thead><tbody>' +
      profiles.map((p) => {
        const flags = [p.udyam_registered && 'Udyam', p.startup_recognised && 'Startup',
          p.blacklisted && 'BLACKLISTED'].filter(Boolean).join(', ');
        return '<tr><td><strong>' + esc(p.name) + '</strong></td>' +
          '<td>' + esc(dash(p.sector)) + '</td>' +
          '<td>' + esc(inr(p.avg_turnover_3yr)) + '</td>' +
          '<td class="small">' + esc(dash(flags)) + '</td>' +
          '<td style="white-space:nowrap"><button type="button" class="btn secondary small" data-edit="' + esc(p.id) + '">Edit</button> ' +
          '<button type="button" class="btn danger small" data-del="' + esc(p.id) + '">Delete</button></td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  html += '<div class="btn-row"><button type="button" class="btn" id="profile-new">+ New profile</button></div></div>';
  html += '<div id="profile-form-box"></div>';

  el.innerHTML = html;

  el.querySelector('#profile-new').addEventListener('click', () =>
    renderProfileForm(el.querySelector('#profile-form-box'), null));
  el.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => {
    const p = profiles.find((x) => String(x.id) === b.getAttribute('data-edit'));
    renderProfileForm(el.querySelector('#profile-form-box'), p);
    el.querySelector('#profile-form-box').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
  el.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    const p = profiles.find((x) => String(x.id) === b.getAttribute('data-del'));
    if (!window.confirm('Delete profile "' + (p ? p.name : '') + '"? This cannot be undone.')) return;
    try {
      await del('/profiles/' + encodeURIComponent(b.getAttribute('data-del')));
      renderProfiles(el);
    } catch (e) { el.insertAdjacentHTML('afterbegin', errorBox('Delete failed: ' + e.message)); }
  }));
}

function renderProfileForm(box, p) {
  const v = (k) => (p && p[k] != null ? p[k] : '');
  const certs = p && Array.isArray(p.certifications) ? p.certifications.join('\n') : (v('certifications') || '');
  let expLines = '';
  if (p && Array.isArray(p.experience)) {
    expLines = p.experience.map((e) =>
      typeof e === 'string' ? e : [e.work || e.description || '', e.value_rs != null ? e.value_rs : (e.value || ''),
        e.year || ''].filter((x) => x !== '').join(' | ')).join('\n');
  } else expLines = v('experience') || '';

  box.innerHTML = '<div class="card"><h3>' + (p ? 'Edit profile' : 'New profile') + '</h3>' +
    '<div id="pf-err"></div>' +
    '<div class="form-grid">' +
    fld('pf-name', 'Company name', '<input type="text" id="pf-name" required value="' + esc(v('name')) + '">') +
    fld('pf-sector', 'Sector', '<select id="pf-sector">' +
      SECTORS.map((s) => '<option value="' + s + '"' + (v('sector') === s ? ' selected' : '') + '>' + s + '</option>').join('') + '</select>') +
    fld('pf-regtype', 'Registration type', '<input type="text" id="pf-regtype" placeholder="e.g. Private Limited, Proprietorship" value="' + esc(v('registration_type')) + '">') +
    fld('pf-turnover', 'Avg 3-yr turnover (₹)',
      '<input type="number" id="pf-turnover" min="0" step="any" placeholder="e.g. 25000000" value="' + esc(v('avg_turnover_3yr')) + '">') +
    fld('pf-local', 'Local content %',
      '<input type="number" id="pf-local" min="0" max="100" step="any" placeholder="e.g. 60" value="' + esc(v('local_content_pct')) + '">') +
    '<div class="field"><label>&nbsp;</label><div class="check-row"><input type="checkbox" id="pf-udyam"' + (v('udyam_registered') ? ' checked' : '') + '><label for="pf-udyam">Udyam (MSME) registered</label></div>' +
    '<div class="check-row" style="margin-top:0.4rem"><input type="checkbox" id="pf-startup"' + (v('startup_recognised') ? ' checked' : '') + '><label for="pf-startup">DPIIT startup recognised</label></div>' +
    '<div class="check-row" style="margin-top:0.4rem"><input type="checkbox" id="pf-blacklisted"' + (v('blacklisted') ? ' checked' : '') + '><label for="pf-blacklisted">Blacklisted / debarred</label></div></div>' +
    fld('pf-certs', 'Certifications <span class="hint">one per line</span>',
      '<textarea id="pf-certs" placeholder="ISO 9001:2015&#10;Electrical Contractor Licence — Class A">' + esc(certs) + '</textarea>', true) +
    fld('pf-exp', 'Experience <span class="hint">one per line: work | value Rs | year</span>',
      '<textarea id="pf-exp" placeholder="Road resurfacing, NH-44 | 25000000 | 2024&#10;School building repair | 8000000 | 2023">' + esc(expLines) + '</textarea>', true) +
    fld('pf-notes', 'Notes', '<textarea id="pf-notes" placeholder="Anything else relevant">' + esc(v('notes')) + '</textarea>', true) +
    '</div>' +
    '<div class="btn-row"><button type="button" class="btn" id="pf-save">' + (p ? 'Save changes' : 'Create profile') + '</button>' +
    '<button type="button" class="btn secondary" id="pf-cancel">Cancel</button></div></div>';

  box.querySelector('#pf-cancel').addEventListener('click', () => { box.innerHTML = ''; });
  box.querySelector('#pf-save').addEventListener('click', async () => {
    const errBox = box.querySelector('#pf-err');
    const name = box.querySelector('#pf-name').value.trim();
    if (!name) { errBox.innerHTML = errorBox('Company name is required.'); return; }
    const num = (id) => {
      const raw = box.querySelector('#' + id).value.trim();
      return raw === '' ? null : Number(raw);
    };
    const body = {
      name,
      sector: box.querySelector('#pf-sector').value,
      registration_type: box.querySelector('#pf-regtype').value.trim() || null,
      udyam_registered: box.querySelector('#pf-udyam').checked,
      startup_recognised: box.querySelector('#pf-startup').checked,
      blacklisted: box.querySelector('#pf-blacklisted').checked,
      avg_turnover_3yr: num('pf-turnover'),
      local_content_pct: num('pf-local'),
      certifications: box.querySelector('#pf-certs').value.split('\n').map((s) => s.trim()).filter(Boolean),
      experience: parseExperience(box.querySelector('#pf-exp').value),
      notes: box.querySelector('#pf-notes').value.trim() || null,
    };
    try {
      if (p) await put('/profiles/' + encodeURIComponent(p.id), body);
      else await post('/profiles', body);
      renderProfiles(document.getElementById('view'));
    } catch (e) {
      errBox.innerHTML = errorBox('Save failed: ' + e.message);
    }
  });
}

function fld(id, label, control, full) {
  return '<div class="field' + (full ? ' full' : '') + '"><label for="' + id + '">' + label + '</label>' + control + '</div>';
}

function parseExperience(text) {
  return text.split('\n').map((s) => s.trim()).filter(Boolean).map((line) => {
    const parts = line.split('|').map((x) => x.trim());
    if (parts.length >= 3) {
      const valNum = Number(String(parts[1]).replace(/[^0-9.]/g, ''));
      return { work: parts[0], value_rs: Number.isNaN(valNum) ? null : valNum, year: parts[2] };
    }
    return { work: line, value_rs: null, year: null };
  });
}

/* ---------------- Run ---------------- */

async function renderRun(el) {
  setLoading(el, 'Loading…');
  let profiles = [], tenders = [];
  try { profiles = asArray(await get('/profiles')); } catch (e) { /* ignore */ }
  try { tenders = asArray(await get('/tenders')); } catch (e) { /* ignore */ }

  let html = '<div class="card"><h2>Run pipeline</h2>' +
    '<p class="muted small" style="margin-top:-0.4rem">Runs the agent pipeline — fetch/match the tender document, generate the RAG brief, check eligibility against the profile, and compute the verdict.</p>' +
    '<div class="form-grid">' +
    '<div class="field"><label for="run-profile">Company profile</label><select id="run-profile">' +
    profiles.map((p, i) => '<option value="' + esc(p.id) + '"' + (i === 0 ? ' selected' : '') + '>' + esc(p.name) + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label for="run-tender">Tender</label><select id="run-tender">' +
    '<option value="">All open tenders</option>' +
    tenders.filter((t) => !t.bid_end_at || new Date(t.bid_end_at).getTime() > Date.now()).map((t) =>
      '<option value="' + esc(t.id) + '">' + esc((t.ref_no ? t.ref_no + ' — ' : '') + (t.title || ('Tender #' + t.id))) + '</option>').join('') +
    '</select></div>' +
    '</div>' +
    '<div class="btn-row"><button type="button" class="btn" id="run-go"' +
    (profiles.length ? '' : ' disabled') + '>Run</button></div>' +
    (profiles.length ? '' : '<p class="muted small">Create a company profile first.</p>') +
    '<div id="run-progress" class="small muted" style="margin-top:0.6rem"></div>' +
    '</div>' +
    '<div id="run-result"></div>' +
    '<div class="card"><h2>Recent runs</h2><div id="run-history"></div></div>';

  el.innerHTML = html;

  const goBtn = el.querySelector('#run-go');
  goBtn.addEventListener('click', async () => {
    const profileId = el.querySelector('#run-profile').value;
    const tenderId = el.querySelector('#run-tender').value || null;
    const prog = el.querySelector('#run-progress');
    const resBox = el.querySelector('#run-result');
    goBtn.disabled = true;
    prog.innerHTML = '<span class="spinner"></span>Pipeline running — this can take a while for document-heavy tenders…';
    resBox.innerHTML = '';
    try {
      const r = await post('/runs', { profile_id: profileId, tender_id: tenderId });
      prog.innerHTML = 'Run finished.';
      resBox.innerHTML = renderRunResult(r);
      resBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
      loadRunHistory(el, profileId);
    } catch (e) {
      prog.innerHTML = '';
      resBox.innerHTML = errorBox('Run failed: ' + e.message);
    } finally {
      goBtn.disabled = false;
    }
  });

  await loadRunHistory(el, profiles.length ? profiles[0].id : null);
}

function renderRunResult(r) {
  if (!r) return '';
  const steps = Array.isArray(r.steps) ? r.steps : (Array.isArray(r.agent_steps) ? r.agent_steps : []);
  let html = '<div class="card"><h2>Run result</h2>' +
    '<div class="row" style="display:flex;gap:0.8rem;align-items:center;flex-wrap:wrap;margin-bottom:0.8rem">' +
    verdictBadge(r.verdict) + scoreBar(r.match_score) +
    (r.tender_id != null ? '<span class="small muted">Tender #' + esc(r.tender_id) + '</span>' : '') +
    (r.run_id || r.id ? '<span class="small muted">Run #' + esc(r.run_id || r.id) + '</span>' : '') +
    '</div>';

  if (steps.length) {
    html += '<h3>Agent steps</h3><ul class="steps">' + steps.map((s) => {
      if (typeof s === 'string') return '<li>' + esc(s) + '</li>';
      const nm = s.name || s.agent || s.step || 'Step';
      const st = s.status || '';
      const det = s.detail || s.message || s.output || '';
      return '<li><strong>' + esc(nm) + '</strong>' +
        (st ? '<span class="step-status ' + (/fail|error/i.test(st) ? 'fail' : 'ok') + '">' + esc(st) + '</span>' : '') +
        (det ? '<br><span class="muted small">' + esc(String(det)).slice(0, 500) + '</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }

  const summary = r.summary || r.result || null;
  if (summary && typeof summary === 'object') {
    html += '<h3>Summary</h3><pre class="dump">' + esc(JSON.stringify(summary, null, 2)) + '</pre>';
  } else if (r.verdict_reason) {
    html += '<p class="muted">' + esc(r.verdict_reason) + '</p>';
  }

  if (r.tender_id != null) {
    html += '<div class="btn-row"><button type="button" class="btn secondary small" data-nav="#/tender/' +
      esc(r.tender_id) + '">Open tender detail</button></div>';
  }
  html += '</div>';
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  bindNav(tmp); // bind while detached; listeners survive insertion into the DOM
  return tmp.innerHTML;
}

async function loadRunHistory(el, profileId) {
  const box = el.querySelector('#run-history');
  box.innerHTML = '<div class="loading"><span class="spinner"></span>Loading history…</div>';
  let runs;
  try {
    runs = asArray(await get('/runs' + (profileId ? '?profile_id=' + encodeURIComponent(profileId) : '')));
  } catch (e) {
    box.innerHTML = errorBox('Could not load run history: ' + e.message);
    return;
  }
  if (!runs.length) {
    box.innerHTML = '<div class="empty">No runs yet.</div>';
    return;
  }
  box.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Run</th><th>When</th><th>Tender</th><th>Score</th><th>Verdict</th></tr></thead><tbody>' +
    runs.slice(0, 20).map((r) =>
      '<tr><td>#' + esc(r.id != null ? r.id : '—') + '</td>' +
      '<td class="small">' + esc(fmtDate(r.created_at || r.started_at)) + '</td>' +
      '<td>' + (r.tender_id != null
        ? '<button type="button" class="btn secondary small" data-nav="#/tender/' + esc(r.tender_id) + '">#' + esc(r.tender_id) + '</button>'
        : '<span class="muted small">all</span>') + '</td>' +
      '<td>' + scoreBar(r.match_score) + '</td>' +
      '<td>' + verdictBadge(r.verdict) + '</td></tr>').join('') +
    '</tbody></table></div>';
  bindNav(box);
}

/* ---------------- Deadlines ---------------- */

async function renderDeadlines(el) {
  setLoading(el, 'Loading deadlines…');
  let deadlines;
  try {
    deadlines = asArray(await get('/deadlines?days=30'));
  } catch (e) {
    el.innerHTML = errorBox('Could not load deadlines: ' + e.message);
    return;
  }

  const groups = { overdue: [], critical: [], soon: [], upcoming: [], unknown: [] };
  deadlines.forEach((d) => {
    const u = String(d.urgency || deriveUrgency(d.due_at)).toLowerCase();
    (groups[u] || groups.unknown).push(d);
  });
  Object.values(groups).forEach((g) => g.sort((a, b) => new Date(a.due_at) - new Date(b.due_at)));

  const order = [['overdue', 'Overdue'], ['critical', 'Due within 24 hours'],
    ['soon', 'Due within 7 days'], ['upcoming', 'Later'], ['unknown', 'No date']];

  let html = '<div class="card"><h2>Upcoming deadlines</h2>' +
    '<p class="muted small" style="margin-top:-0.4rem">Next 30 days, from tracked tenders in the backend database.</p>';
  if (!deadlines.length) {
    html += '<div class="empty">No deadlines recorded.</div>';
  } else {
    order.forEach(([key, label]) => {
      const g = groups[key];
      if (!g.length) return;
      html += '<div class="group-title">' + urgencyChip({ urgency: key }) + '<span>' + esc(label) +
        ' (' + g.length + ')</span></div>';
      html += g.map((d) => {
        const cd = countdown(d.due_at);
        return '<div class="deadline-row">' +
          '<span><strong>' + esc(d.label || d.kind || 'Deadline') + '</strong>' +
          (d.kind ? ' <span class="muted small">· ' + esc(d.kind) + '</span>' : '') +
          (d.tender_id != null ? ' <span class="muted small">· tender #' + esc(d.tender_id) + '</span>' : '') + '</span>' +
          '<span class="due"><span class="countdown' + (cd.overdue ? ' overdue' : '') + '">' +
          esc(cd.text) + '</span> · ' + esc(fmtDate(d.due_at)) + '</span></div>';
      }).join('');
    });
  }
  html += '</div>';
  el.innerHTML = html;
}

/* ---------------- Ingest ---------------- */

function renderIngest(el) {
  el.innerHTML = '<div class="card"><h2>Ingest public listings</h2>' +
    '<p class="muted small">Fetch new tender listings from public portals into the database. ' +
    'Public listings only. Respect portal ToS; see README.</p>' +
    '<div class="form-grid">' +
    '<div class="field"><label for="ing-keyword">Keyword</label>' +
    '<input type="text" id="ing-keyword" placeholder="e.g. road construction"></div>' +
    '<div class="field"><label for="ing-max">Max results</label>' +
    '<input type="number" id="ing-max" min="1" max="200" value="20"></div>' +
    '</div>' +
    '<div class="btn-row"><button type="button" class="btn" id="ing-go">Run ingestion</button></div>' +
    '<div id="ing-result" class="mt"></div></div>';

  el.querySelector('#ing-go').addEventListener('click', async () => {
    const btn = el.querySelector('#ing-go');
    const box = el.querySelector('#ing-result');
    const keyword = el.querySelector('#ing-keyword').value.trim();
    const maxResults = Math.max(1, parseInt(el.querySelector('#ing-max').value, 10) || 20);
    if (!keyword) { box.innerHTML = errorBox('Enter a keyword first.'); return; }
    btn.disabled = true;
    box.innerHTML = '<div class="loading"><span class="spinner"></span>Ingesting public listings…</div>';
    try {
      const r = await post('/ingest/run', { keyword, max_results: maxResults });
      const errs = Array.isArray(r.errors) ? r.errors : [];
      box.innerHTML = '<div class="stats">' +
        statCard(r.found != null ? r.found : '—', 'Listings found') +
        statCard(r.added != null ? r.added : '—', 'Added to database') +
        statCard(errs.length, 'Errors') + '</div>' +
        (errs.length ? '<h3>Errors</h3><ul class="steps">' +
          errs.slice(0, 20).map((e) => '<li>' + esc(typeof e === 'string' ? e : JSON.stringify(e)) + '</li>').join('') +
          '</ul>' : '<p class="muted small">Ingestion complete. New tenders appear under the Tenders tab.</p>');
    } catch (e) {
      box.innerHTML = errorBox('Ingestion failed: ' + e.message);
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------------- init ---------------- */

async function checkHealth() {
  const h = document.getElementById('health');
  try {
    await get('/health');
    h.classList.add('ok');
    h.querySelector('.health-text').textContent = 'API online';
  } catch (e) {
    h.classList.add('down');
    h.querySelector('.health-text').textContent = 'API unreachable';
  }
}

document.querySelectorAll('#nav button').forEach((b) => {
  b.addEventListener('click', () => navigate('#/' + b.getAttribute('data-view')));
});
window.addEventListener('hashchange', route);
if (!location.hash) location.hash = '#/dashboard';

checkHealth();
route();
