/* ctx — Explorer / Labs / Deployment
   One session at a time. Explorer reads it, Labs experiments beside it,
   Deployment stages changes and applies them under the session's own id. */

const $ = s => document.querySelector(s);
const NS = 'http://www.w3.org/2000/svg';
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const fmt = n => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n || 0);
const num = n => Number(n || 0).toLocaleString();
const el = (t, a) => { const e = document.createElementNS(NS, t);
  for (const k in a) e.setAttribute(k, a[k]); return e; };
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ago = ts => { const d = Date.now() / 1000 - ts;
  return d < 60 ? 'just now' : d < 3600 ? Math.floor(d / 60) + 'm ago'
    : d < 86400 ? Math.floor(d / 3600) + 'h ago' : Math.floor(d / 86400) + 'd ago'; };

let DATA = null, SESSIONS = [], PAGE = 'explorer';
let BOARD = { nodes: [], edges: [] }, DEP = { items: [] }, LOG = [], LOGSEEN = 0, POLL = 0;

/* ═══════════════ content rendering ═══════════════
   Only <, &, > are neutralised so quotes survive the tokenizer; everything is
   inserted as element content, never into an attribute. */
function hiJSON(t) {
  return esc(t).replace(
    /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g,
    m => {
      let c = 's3';
      if (/^"/.test(m)) c = /:$/.test(m.trim()) ? 's1' : 's2';
      else if (/true|false|null/.test(m)) c = 's4';
      return `<span class="${c}">${m}</span>`;
    }).replace(/([{}\[\],])/g, '<span class="s5">$1</span>');
}
function hiCode(code, lang) {
  const t = (lang || '').toLowerCase();
  if (t === 'json' || (!t && /^\s*[{\[]/.test(code))) {
    try { return hiJSON(JSON.stringify(JSON.parse(code), null, 2)); } catch (e) {}
  }
  return esc(code);
}
function inlineMd(x) {
  return x
    .replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/&lt;(\/?[a-zA-Z][\w:-]*)&gt;/g, '<span class="xtag">&lt;$1&gt;</span>');
}
function renderMd(src) {
  const blocks = [];
  let txt = src.replace(/```([\w+-]*)\n([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push(`<pre>${lang ? `<span class="lang">${esc(lang)}</span>` : ''}` +
      `<code>${hiCode(code.replace(/\n$/, ''), lang)}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  txt = esc(txt);
  const lines = txt.split('\n'), out = [];
  let list = null, para = [], tbl = null;
  const fp = () => { if (para.length) { out.push('<p>' + inlineMd(para.join(' ')) + '</p>'); para = []; } };
  const fl = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const ft = () => { if (tbl) { out.push('</tbody></table>'); tbl = null; } };
  const fa = () => { fp(); fl(); ft(); };
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (!t) { fa(); continue; }
    if (/^\u0000\d+\u0000$/.test(t)) { fa(); out.push(blocks[+t.replace(/\u0000/g, '')]); continue; }
    let m;
    if ((m = t.match(/^(#{1,6})\s+(.*)$/))) { fa();
      const lv = Math.min(4, m[1].length); out.push(`<h${lv}>${inlineMd(m[2])}</h${lv}>`); continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { fa(); out.push('<hr>'); continue; }
    if ((m = t.match(/^&gt;\s?(.*)$/))) { fa(); out.push(`<blockquote>${inlineMd(m[1])}</blockquote>`); continue; }
    if (t.startsWith('|') && !tbl && /^\|[\s:|-]+\|$/.test((lines[i + 1] || '').trim())) {
      fp(); fl();
      out.push(`<table><thead><tr>${t.split('|').slice(1, -1)
        .map(c => `<th>${inlineMd(c.trim())}</th>`).join('')}</tr></thead><tbody>`);
      tbl = 1; i++; continue;
    }
    if (tbl && t.startsWith('|')) {
      out.push(`<tr>${t.split('|').slice(1, -1)
        .map(c => `<td>${inlineMd(c.trim())}</td>`).join('')}</tr>`); continue;
    }
    if (tbl) ft();
    if ((m = t.match(/^([-*+])\s+(.*)$/))) { fp(); ft();
      if (list !== 'ul') { fl(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li>${inlineMd(m[2])}</li>`); continue; }
    if ((m = t.match(/^\d+[.)]\s+(.*)$/))) { fp(); ft();
      if (list !== 'ol') { fl(); out.push('<ol>'); list = 'ol'; }
      out.push(`<li>${inlineMd(m[1])}</li>`); continue; }
    fl(); ft(); para.push(t);
  }
  fa();
  return out.join('\n');
}
function renderBody(txt) {
  const t = (txt || '').trim();
  if (/^[{\[]/.test(t)) {
    try {
      return `<pre><span class="lang">json</span><code>` +
        hiJSON(JSON.stringify(JSON.parse(t), null, 2)) + `</code></pre>`;
    } catch (e) {
      /* looks like JSON but will not parse — almost always truncated. Running
         markdown over it would mangle it into paragraphs. */
      return `<pre><span class="lang">json · unterminated</span><code>${hiJSON(t)}</code></pre>`;
    }
  }
  return renderMd(txt || '');
}

/* ═══════════════ onboarding ═══════════════ */
let CTXS = [], PICK = null, WS = null;

async function boot() {
  const [a, b] = await Promise.all([
    (await fetch('/api/sessions')).json(),
    (await fetch('/api/ctxs')).json(),
  ]);
  SESSIONS = a.sessions || [];
  CTXS = b.sessions || [];
  paintCtxList(); paintSessions('');
  obMode(CTXS.length ? 'open' : 'new');

  /* A refresh should land you back where you were. The ctx session stays open
     until you close it from File, so reload resumes rather than restarting. */
  const openSid = (b.active || {}).ctx_session;
  if (openSid && CTXS.some(w => w.id === openSid)) await openCtx(openSid);
}
function obMode(m) {
  ['open', 'new'].forEach(k => {
    $('#ob-t-' + k).classList.toggle('on', k === m);
    $('#ob-' + k).classList.toggle('on', k === m);
  });
}
function paintCtxList() {
  $('#ctxlist').innerHTML = CTXS.length ? CTXS.map(w => `
    <div class="srow" data-id="${esc(w.id)}">
      <span class="pill">${w.nodes} nodes</span>
      <span class="nm">${esc(w.name)}</span>
      <span class="mt">${esc(w.harness_name || '—')} · ${esc(w.adapter)} · ${ago(w.updated)}</span>
      <span class="go">&rsaquo;</span>
    </div>`).join('')
    : '<div class="empty">No ctx sessions yet. Create one from a harness session.</div>';
  $('#ctxlist').querySelectorAll('.srow').forEach(e => e.onclick = () => openCtx(e.dataset.id));
}
function paintSessions(q) {
  q = (q || '').toLowerCase();
  const rows = SESSIONS.filter(s => !q ||
    (s.name + ' ' + s.cwd + ' ' + s.adapter).toLowerCase().includes(q));
  $('#slist').innerHTML = rows.map(s => `
    <div class="srow ${PICK === s.key ? 'sel' : ''}" data-key="${esc(s.key)}">
      <span class="pill ${s.status}">${esc(s.status)}</span>
      <span class="nm">${esc(s.name)}</span>
      <span class="mt">${esc(s.adapter)} · ${esc((s.cwd || '').split('/').slice(-2).join('/'))}</span>
      <span class="go">${PICK === s.key ? '&#10003;' : '&rsaquo;'}</span>
    </div>`).join('') || '<div class="empty">Nothing matches that filter.</div>';
  $('#slist').querySelectorAll('.srow').forEach(e => {
    e.onclick = () => pickHarness(e.dataset.key);
    e.ondblclick = () => { pickHarness(e.dataset.key); createCtx(); };
  });
}
function pickHarness(key) {
  PICK = key;
  const s = SESSIONS.find(x => x.key === key);
  const msg = $('#pickmsg');
  msg.textContent = s ? `Selected — ${s.name} · ${s.adapter}. Name it below, then Create.` : '';
  msg.classList.toggle('ok', !!s);
  $('#step2').classList.toggle('ready', !!s);
  if (!$('#ctxname').value.trim() && s) $('#ctxname').value = s.name.slice(0, 48);
  paintSessions($('#filter').value);
  const f = $('#ctxname'); if (f) { f.focus(); f.select(); }
}
async function createCtx() {
  if (!PICK) { say('No session selected',
    'Choose a harness session from the list above, then name it.', 'warn'); return; }
  const name = $('#ctxname').value.trim();
  if (!name) { await say('Name required', 'Give this ctx session a name so you can find it again.', 'warn');
    $('#ctxname').focus(); return; }
  const r = await (await fetch('/api/ctxs/create', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: PICK, name }) })).json();
  if (!r.ok) { say('Could not create session', esc(r.error || 'Unknown error.'), 'warn'); return; }
  WS = { id: r.id, name: r.name };
  await importSession(r.key);
}
async function openCtx(id) {
  const r = await (await fetch('/api/ctxs/open', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }) })).json();
  if (!r.ok) { say('Could not open session', esc(r.error || 'Unknown error.'), 'warn'); return; }
  WS = { id: r.id, name: r.name };
  await importSession(r.key);
}
$('#filter').oninput = e => paintSessions(e.target.value);
$('#ctxname').addEventListener('keydown', e => { if (e.key === 'Enter') createCtx(); });

async function importSession(key) {
  DATA = await (await fetch('/api/session?key=' + encodeURIComponent(key))).json();
  if (DATA.error) return say('Could not load session', esc(DATA.error), 'warn');
  $('#onboard').style.display = 'none';
  $('#menubar').style.display = 'flex';
  $('#strip').style.display = 'flex';
  SEL = null; DRAW = null; ITEM = null; t = 0; tT = 0; s1 = s1T = s2 = s2T = 0;
  closeSide(); strip(); setPage('explorer');
  BOARD = { nodes: [], edges: [] }; LNODE = null; lx = 40; ly = 40; lk = 1;
  await Promise.all([loadBoard(), loadDeploy(), loadRuns(), pollLog()]);
  $('#status').style.display = 'flex';
  $('#mws').textContent = WS ? WS.name : '—';
  $('#mharness').textContent = `${DATA.session.adapter} · ${DATA.session.name}`;
  paintKinds();
  PULSE = null; STALE = false; FRESH = Date.now() / 1000;
  paintRefresh();
  status();
  if (!POLL) POLL = setInterval(tickPoll, 1600);
}
function switchSession() {
  WS = null;
  $('#menubar').style.display = 'none';
  $('#strip').style.display = 'none';
  $('#status').style.display = 'none';
  document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
  $('#onboard').style.display = 'flex';
  boot();
}
/* ═══════════════ REFRESH ═══════════════
   The session you are looking at keeps running while you look at it. A refresh
   re-reads the transcript and repaints in place -- it deliberately does NOT
   re-import, because that would throw away the bucket you had open and drop you
   back on Explorer. What you were reading stays on screen; only the numbers,
   the buckets and the items underneath it move. */
let PULSE = null, PFLAGS = '', STALE = false, FRESH = 0;
let AUTO = localStorage.getItem('ctx.auto') === '1';

async function refreshData(quiet) {
  if (!DATA) return;
  const key = DATA.session.key;
  const btn = $('#refreshbtn'); if (btn) btn.classList.add('spin');
  let d;
  try {
    d = await (await fetch('/api/session?key=' + encodeURIComponent(key))).json();
  } catch (err) {
    if (btn) btn.classList.remove('spin');
    if (!quiet) say('Could not refresh', 'The server did not answer.', 'warn');
    return;
  }
  if (btn) btn.classList.remove('spin');
  if (d.error) { if (!quiet) say('Could not refresh', esc(d.error), 'warn'); return; }

  DATA = d;
  STALE = false; FRESH = Date.now() / 1000;
  /* a bucket that no longer exists cannot stay selected */
  if (SEL && !DATA.buckets.some(b => b.key === SEL)) {
    SEL = null; DRAW = null; ITEM = null; tT = 0; closeSide();
  }
  strip(); paintRefresh(); paintKinds(); status();
  if (PAGE === 'explorer') kick();
  /* an explicit refresh re-reads the open item too; auto-refresh leaves it
     alone, so the panel does not jump while you are reading it */
  if (!quiet && SEL && ITEM != null) await openItem(SEL, ITEM);
  if (!quiet) flash('Refreshed from the transcript.');
}
const reload = () => refreshData(false);

/* Cheap stat of the transcript: has it grown since we last read it? */
async function pulse() {
  if (!DATA) return;
  let p;
  try {
    p = await (await fetch('/api/pulse?key=' + encodeURIComponent(DATA.session.key))).json();
  } catch (err) { return; }
  if (!p || !p.ok) return;
  /* anything that changes what the session is sending, not just the transcript */
  const sig = [p.mtime, p.size, p.flags || '', p.deploy || 0].join(':');
  if (PULSE === null) { PULSE = sig; PFLAGS = p.flags || ''; return; }
  if (sig === PULSE) return;
  /* A relaunch changes which flags a session carries without writing anything
     to the transcript. That changes what every deployment's status MEANS, so
     the page has to be re-read, not just marked stale. */
  const flagsMoved = (p.flags || '') !== PFLAGS;
  PULSE = sig; PFLAGS = p.flags || '';
  if (flagsMoved) { await loadDeploy(); if (PAGE === 'deploy') paintDeploy(); }
  if (AUTO) await refreshData(true);
  else { STALE = true; paintRefresh(); }
}

function paintRefresh() {
  const b = $('#refreshbtn'); if (!b) return;
  b.classList.toggle('stale', STALE);
  b.title = STALE ? 'The session has moved on since you loaded it \u2014 click to re-read (\u2318R)'
                  : (FRESH ? 'Re-read the transcript (\u2318R) \u00b7 last read ' + ago(FRESH)
                           : 'Re-read the transcript (\u2318R)');
  const a = $('#autokbd'); if (a) a.textContent = AUTO ? 'on' : 'off';
}

/* ═══════════════ pages ═══════════════ */
function setPage(p) {
  PAGE = p;
  document.querySelectorAll('.page').forEach(x => x.classList.remove('on'));
  $('#pg-' + p).classList.add('on');
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.p === p));
  $('#strip').style.display = p === 'explorer' ? 'flex' : 'none';
  if (p === 'explorer') kick();
  if (p === 'labs') { layoutLab(); paintLab(); paintLabSide(); }
  if (p === 'deploy') { loadDeploy(); }
}

/* ═══════════════ EXPLORER ═══════════════ */
let SEL = null, DRAW = null, ITEM = null, ITEMHTML = '';
let t = 0, tT = 0, RAF = 0, lastW = 0, lastH = 0, calm = 0;
let s1 = 0, s1T = 0, max1 = 0, s2 = 0, s2T = 0, maxScroll = 0, zoneX = 0;
let railPx = +(localStorage.getItem('ctx.rail') || 0);
let sidePx = +(localStorage.getItem('ctx.side') || 0);
/* The five turn-by-turn kinds are bands of their own, but they are one stream.
   So drilling any of them fans out the WHOLE stream in the order it happened,
   with the kind you picked lit and the other four dimmed but still in place.
   One fan, five readings of it: you see your question with the reply, the call
   and the result still around it, instead of a list of questions on their own. */
const KINDC = { user_turns: 'var(--k-user)', assistant_turns: 'var(--k-asst)',
                thinking: 'var(--k-think)', tool_calls: 'var(--k-call)',
                tool_results: 'var(--k-result)', compact_summary: 'var(--k-compact)' };
const STEPN = { user_turns: 'you asked', assistant_turns: 'it answered',
                thinking: 'reasoning', tool_calls: 'tool', tool_results: 'result',
                compact_summary: 'compact summary' };

const isConv = key => (DATA.conversation_kinds || []).includes(key);

/* A turn-by-turn band wears its stream colour, so the band you drilled and the
   records it lights are the same colour. Everything else keeps the edit-class
   colour, which is what those buckets are actually about. */
const bandColor = b => KINDC[b.key] || b.color;

/* While a turn-by-turn band is open, the five kinds sit on screen as a legend
   that is also the switch: it says what each colour means and moves the light
   without having to find a dimmed ribbon to click. */
function paintKinds() {
  const host = $('#kinds'); if (!host || !DATA) return;
  if (!DRAW || !isConv(DRAW)) { host.style.display = 'none'; host.innerHTML = ''; return; }
  const seen = {};
  for (const r of (DATA.sequence || [])) seen[r.kind] = (seen[r.kind] || 0) + 1;
  host.style.display = 'flex';
  host.innerHTML = (DATA.conversation_kinds || []).filter(k => seen[k]).map(k => {
    const bk = DATA.buckets.find(x => x.key === k);
    return `<button class="${k === DRAW ? 'on' : ''}" onclick="switchKind('${k}')"
      style="--c:${KINDC[k] || 'var(--dim)'}"><i></i>${bk ? esc(bk.label) + ' ' : ''}${
      esc(STEPN[k] || k)}<b>${num(seen[k])}</b></button>`;
  }).join('');
}

/* Move the light to another kind, keeping the scroll: it is the same list. */
function switchKind(k) {
  if (k === DRAW) return;
  SEL = k; DRAW = k; ITEM = null; closeSide(); paintKinds(); kick();
}

function seqTitle(r) {
  if (r.kind === 'tool_calls') {
    return r.tool + (r.result_tokens != null
      ? ` \u2192 ${fmt(r.result_tokens)} back` : ' \u2192 no result');
  }
  return (STEPN[r.kind] || r.kind) + (r.title ? ' \u00b7 ' + r.title : '');
}

/* What level 2 fans out for a band: the stream for a conversation kind, the
   band's own items for anything else. */
function levelItems(key) {
  const b = DATA.buckets.find(x => x.key === key);
  if (!b) return [];
  return isConv(key) ? (DATA.sequence || []) : b.items;
}

const autoRail = W => clamp(W * 0.23, 196, 286);
const autoSide = W => Math.min(520, W * 0.42);

const GAP1 = 3, GAP2 = 4, MIN1 = 15, MIN2 = 18, NODE = 18, PAD = 26;
function allocate(vals, avail, minH, gap) {
  const n = vals.length, H = avail - gap * (n - 1);
  const locked = new Array(n).fill(false);
  for (;;) {
    const free = H - minH * locked.filter(Boolean).length;
    const sum = vals.reduce((a, v, i) => locked[i] ? a : a + v, 0) || 1;
    let ch = false;
    for (let i = 0; i < n; i++) if (!locked[i] && free * vals[i] / sum < minH) { locked[i] = true; ch = true; }
    if (!ch || locked.every(Boolean)) break;
  }
  const free = Math.max(0, H - minH * locked.filter(Boolean).length);
  const sum = vals.reduce((a, v, i) => locked[i] ? a : a + v, 0) || 1;
  return vals.map((v, i) => locked[i] ? minH : free * v / sum);
}

function kick() { cancelAnimationFrame(RAF); calm = 0; RAF = requestAnimationFrame(loop); }
function loop() {
  const wrap = $('#stagewrap'), W = wrap.clientWidth, H = wrap.clientHeight;
  if (Math.abs(tT - t) > 0.0008) t += (tT - t) * 0.17; else t = tT;
  if (Math.abs(s1T - s1) > 0.4) s1 += (s1T - s1) * 0.22; else s1 = s1T;
  if (Math.abs(s2T - s2) > 0.4) s2 += (s2T - s2) * 0.22; else s2 = s2T;
  if (t === 0 && !SEL) DRAW = null;
  const moving = t !== tT || s1 !== s1T || s2 !== s2T || W !== lastW || H !== lastH;
  lastW = W; lastH = H;
  render(W, H); layout();
  calm = moving ? 0 : calm + 1;
  if (calm < 4) RAF = requestAnimationFrame(loop);
}
function layout() {
  const pg = $('#pg-explorer'), MW = pg.clientWidth;
  const open = $('#ex-side').classList.contains('open');
  const sw = clamp(sidePx || autoSide(MW), 300, MW * 0.7);
  $('#ex-side').style.width = sw + 'px';
  $('#stagewrap').style.right = (open ? sw : 0) + 'px';
  const SW = MW - (open ? sw : 0);
  const rail = clamp(railPx || autoRail(SW), 150, SW * 0.62);
  $('#gripA').classList.toggle('on', !!DRAW && t > 0.55);
  $('#gripA').style.left = lerp(SW, rail, t) + 'px';
  $('#gripB').classList.toggle('on', open);
  $('#gripB').style.left = (MW - sw) + 'px';
}

function render(W, H) {
  const svg = $('#stage');
  if (!DATA || W < 60 || H < 60) return;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = '';
  const railW = clamp(railPx || autoRail(W), 150, W * 0.62);
  const leftW = lerp(W, railW, t);
  const pad = Math.max(12, PAD - 14 * t);
  const avail = H - pad * 2;
  const bs = DATA.buckets;
  zoneX = leftW;

  /* level 1 — a compact origin at centre-left fanning out to the buckets */
  const h1 = allocate(bs.map(b => b.tokens), avail, MIN1, GAP1);
  const lab1 = lerp(250, 0, Math.min(1, t * 1.6));
  const x0 = pad, x1 = Math.max(x0 + NODE + 10, leftW - NODE - lab1 - pad * t);
  const xm = (x0 + NODE + x1) / 2;
  const tot1 = h1.reduce((a, v) => a + v, 0) + GAP1 * (bs.length - 1);
  max1 = Math.max(0, tot1 - avail);
  if (s1T > max1) s1T = max1;
  if (s1 > max1) s1 = max1;
  const sumT = bs.reduce((a, b) => a + b.tokens, 0);
  const root1 = Math.max(44, Math.min(130, avail * 0.2));
  const rootY = pad + (avail - root1) / 2;
  let y = pad + (max1 > 0 ? -s1 : (avail - tot1) / 2), c1 = rootY;

  svg.appendChild(el('rect', { x: x0, y: rootY, width: NODE, height: root1, rx: 4,
    fill: 'var(--root)', 'fill-opacity': .55 }));
  if (H > 170) {
    const a = el('text', { x: x0, y: Math.max(14, rootY - 12), class: 'rootlabel' });
    a.textContent = leftW > 250 ? 'session context' : 'context'; svg.appendChild(a);
    const v = el('text', { x: x0, y: rootY + root1 + 20, class: 'rootval' });
    v.textContent = leftW > 250 ? num(DATA.est_total) + ' est. tokens' : fmt(DATA.est_total);
    svg.appendChild(v);
  }

  const geo = {};
  bs.forEach((b, i) => {
    const h = h1[i], yy = y; geo[b.key] = { y: yy, h };
    const sh = sumT > 0 ? root1 * (b.tokens / sumT) : root1 / bs.length;
    const isSel = DRAW === b.key;
    const g = el('g', { class: 'band' + (isSel ? ' sel' : '') });
    if (DRAW && !isSel) g.setAttribute('opacity', String(lerp(1, .26, t)));
    const bc = bandColor(b);
    g.appendChild(el('path', { class: 'ribbon', fill: bc, d:
      `M${x0 + NODE},${c1} C${xm},${c1} ${xm},${yy} ${x1},${yy}` +
      ` L${x1},${yy + h} C${xm},${yy + h} ${xm},${c1 + sh} ${x0 + NODE},${c1 + sh} Z` }));
    g.appendChild(el('rect', { class: 'nodebar', x: x1, y: yy, width: NODE, height: h, rx: 3, fill: bc }));
    const my = yy + h / 2;
    const tag = el('text', { y: my, class: 'btag', fill: bc,
      x: t < .5 ? x1 + NODE + 10 : x1 - 8, 'text-anchor': t < .5 ? 'start' : 'end' });
    tag.textContent = b.label; g.appendChild(tag);
    const fade = Math.max(0, 1 - t * 2.1);
    if (fade > 0.02 && lab1 > 60) {
      const n = el('text', { x: x1 + NODE + 34, y: my, class: 'blabel', opacity: fade });
      n.textContent = b.title; g.appendChild(n);
      const v = el('text', { x: leftW - pad, y: my, class: 'bval', 'text-anchor': 'end', opacity: fade });
      v.textContent = `${fmt(b.tokens)} · ${b.pct}%`; g.appendChild(v);
    }
    g.onclick = () => { (DRAW === b.key && t > 0.5) ? overview() : drill(b.key); };
    g.onmousemove = e => tip(e, b.label, b.title, b.tokens, b.pct, bc, b.badge,
      b.breakdown ? `${b.count} exchanges · `
          + b.breakdown.slice(0, 3).map(x => `${x.title} ${fmt(x.tokens)}`).join(' · ')
        : `${b.count} items`);
    g.onmouseleave = hideTip;
    svg.appendChild(g);
    y += h + GAP1; c1 += sh;
  });

  /* level 2 — the selected band fans out into its items */
  if (!DRAW || t < 0.03) { maxScroll = 0; return; }
  const b = bs.find(x => x.key === DRAW);
  const conv = isConv(DRAW);
  if (!b || !levelItems(DRAW).length) { maxScroll = 0; return; }
  const src = geo[DRAW], items = levelItems(DRAW), n = items.length;
  const rightX = W - pad, region = rightX - (x1 + NODE);
  if (region < 80) { maxScroll = 0; return; }

  const need2 = n * (MIN2 + GAP2) - GAP2;
  const canvas2 = Math.max(avail, need2);
  maxScroll = Math.max(0, canvas2 - avail);
  if (s2T > maxScroll) s2T = maxScroll;
  if (s2 > maxScroll) s2 = maxScroll;
  const h2 = allocate(items.map(i => i.tokens), canvas2, MIN2, GAP2);
  const sumV = items.reduce((a, i) => a + i.tokens, 0);
  const hasBar = maxScroll > 0;
  const lab2 = region > 320 ? 240 : (region > 200 ? 120 : 0);
  const x3 = rightX - lab2 - NODE - (hasBar ? 10 : 0);
  const xs = x1 + NODE, xm2 = (xs + x3) / 2;
  let yc = 0, cum = src.y;

  const clip = el('clipPath', { id: 'z2' });
  clip.appendChild(el('rect', { x: xs, y: pad - 2, width: Math.max(1, W - xs), height: avail + 4 }));
  svg.appendChild(clip);
  const grow = Math.min(1, Math.max(0, (t - 0.18) / 0.82));
  const layer = el('g', { opacity: String(grow), 'clip-path': 'url(#z2)' });

  items.forEach((it, i) => {
    /* slices must sum to EXACTLY the band height or the fan spills over its
       neighbours — so no floor here; sub-pixel slices are the point. */
    const sh = sumV > 0 ? src.h * (it.tokens / sumV) : src.h / n;
    const hb = h2[i], yb = pad + yc - s2, ya = cum;
    yc += hb + GAP2; cum += sh;
    if (yb + hb < pad - 60 || yb > pad + avail + 60) return;
    const ic = it.superseded ? 'var(--faint)'
      : it.pending ? (it.live ? 'var(--live)' : 'var(--k-pending)')
      : (conv ? (KINDC[it.kind] || b.color) : bandColor(b));
    const on = !conv || it.kind === DRAW;
    const g = el('g', { class: 'band' + (ITEM === it.i ? ' sel' : '') + (it.pending ? (it.live ? ' inject' : ' pend') : '') });
    /* In the stream it is the KIND that is lit, not one record: drill Tool
       Calls and every tool call stays bright while the other kinds sit back.
       Picking a single record must not dim the rest of its own kind -- that
       record is marked by .sel instead, which deepens it rather than fading
       its neighbours. Outside the stream, one item at a time is the point. */
    const op = conv ? (on ? 1 : 0.16)
                    : (ITEM != null && ITEM !== it.i ? 0.3 : 1);
    if (op < 1) g.setAttribute('opacity', String(op));
    g.appendChild(el('path', { class: 'ribbon', fill: ic, d:
      `M${xs},${ya} C${xm2},${ya} ${xm2},${yb} ${x3},${yb}` +
      ` L${x3},${yb + hb} C${xm2},${yb + hb} ${xm2},${ya + sh} ${xs},${ya + sh} Z` }));
    g.appendChild(el('rect', { class: 'nodebar', x: x3, y: yb, width: NODE, height: hb, rx: 2, fill: ic }));
    if (lab2 > 0) {
      const my = yb + hb / 2, tx = x3 + NODE + 9;
      const tg = el('text', { x: tx, y: my, class: 'btag', fill: ic });
      tg.textContent = it.label; g.appendChild(tg);
      if (lab2 > 200) {
        const nm = el('text', { x: tx + 36, y: my, class: 'blabel' });
        // ▣ marks an item that carries a rendered image
        nm.textContent = (it.superseded ? '\u2014 ' : '') + (it.imgs ? '\u25A3 ' : '')
          + (conv ? seqTitle(it) : (it.title || '—'));
        g.appendChild(nm);
        const vv = el('text', { x: rightX - (hasBar ? 10 : 0), y: my, class: 'bval', 'text-anchor': 'end' });
        vv.textContent = fmt(it.tokens); g.appendChild(vv);
      }
    }
    const pc = sumV > 0 ? Math.round(1000 * it.tokens / sumV) / 10 : 0;
    g.onclick = () => {
      /* The stream is one list, so a record of another kind is not a dead end:
         clicking it moves the category -- level 1 follows level 2, the light
         and the colour move to that kind, and the scroll stays where it is
         because the list underneath is the same list. */
      if (conv && it.kind !== DRAW) {
        SEL = it.kind; DRAW = it.kind; ITEM = null; paintKinds();
        return openItem(DRAW, it.i);
      }
      (ITEM === it.i && $('#ex-side').classList.contains('open'))
        ? closeSide() : openItem(b.key, it.i);
    };
    g.onmousemove = e => tip(e, it.label, conv ? seqTitle(it) : (it.title || '—'),
      it.tokens, pc, ic, conv ? (STEPN[it.kind] || it.kind) : b.badge,
      conv ? `line ${it.line}${it.model ? ' · ' + it.model : ''}${on ? '' : ' · other kind'}`
           : `turn ${it.turn} · line ${it.line}${it.imgs ? ' · ' + it.imgs + ' image' + (it.imgs > 1 ? 's' : '') : ''}`);
    g.onmouseleave = hideTip;
    layer.appendChild(g);
  });
  svg.appendChild(layer);
  if (hasBar) {
    const th = Math.max(30, avail * avail / canvas2);
    svg.appendChild(el('rect', { class: 'sbar', x: W - 7,
      y: pad + (avail - th) * (s2 / maxScroll), width: 4, height: th, rx: 2,
      opacity: String(0.3 * grow) }));
  }
}
function overview() { SEL = null; ITEM = null; tT = 0; closeSide(); paintKinds(); kick(); }
function drill(key) {
  const same = DRAW && isConv(DRAW) && isConv(key);   /* same list, keep the scroll */
  SEL = key; DRAW = key; ITEM = null; tT = 1;
  if (!same) { s2 = s2T = 0; }
  closeSide(); paintKinds(); kick();
}

function tip(e, label, title, val, pct, color, badge, sub) {
  const x = $('#tip');
  x.innerHTML = `<b>${esc(label)} — ${esc(title)}</b>
    <i>${num(val)} est. tokens · ${pct}%${sub ? ' · ' + esc(sub) : ''}</i><br>
    <i style="color:${color}">${esc(badge)}</i>`;
  x.style.opacity = 1;
  x.style.left = Math.min(e.clientX + 14, innerWidth - 314) + 'px';
  x.style.top = Math.min(e.clientY + 14, innerHeight - 92) + 'px';
}
function hideTip() { $('#tip').style.opacity = 0; }

/* scroll routing — whatever the cursor is over scrolls, nothing chains past it */
$('#stagewrap').addEventListener('wheel', e => {
  const r = $('#stagewrap').getBoundingClientRect(), x = e.clientX - r.left;
  e.preventDefault();
  if (!DRAW || t < 0.5 || x < zoneX) {
    if (max1 > 0) s1T = clamp(s1T + e.deltaY, 0, max1);
  } else if (maxScroll > 0) s2T = clamp(s2T + e.deltaY, 0, maxScroll);
  kick();
}, { passive: false });

function gripDrag(id, onMove, onReset) {
  const g = $(id);
  g.addEventListener('mousedown', e => {
    e.preventDefault();
    $('#pg-explorer').classList.add('dragging'); g.classList.add('drag');
    const mv = ev => { onMove(ev); kick(); };
    const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up);
      $('#pg-explorer').classList.remove('dragging'); g.classList.remove('drag');
      localStorage.setItem('ctx.rail', railPx); localStorage.setItem('ctx.side', sidePx); kick(); };
    addEventListener('mousemove', mv); addEventListener('mouseup', up);
  });
  g.addEventListener('dblclick', () => { onReset(); kick();
    localStorage.setItem('ctx.rail', railPx); localStorage.setItem('ctx.side', sidePx); });
}
gripDrag('#gripA', e => { const r = $('#pg-explorer').getBoundingClientRect();
  railPx = clamp(e.clientX - r.left, 150, $('#stagewrap').clientWidth * 0.62); }, () => railPx = 0);
gripDrag('#gripB', e => { const r = $('#pg-explorer').getBoundingClientRect();
  sidePx = clamp(r.right - e.clientX, 300, r.width * 0.7); }, () => sidePx = 0);
addEventListener('resize', () => { if (DATA && PAGE === 'explorer') kick(); if (PAGE === 'labs') drawEdges(); });

/* ---- explorer side panel ---- */
let RAW = false, CURTEXT = '', CURITEM = null;
/* A prepared change has no segment to fetch -- it is not in the transcript and
   not in the package. Show it against what the section holds today, so the two
   states sit next to each other. */
function openPending(b, it) {
  const now = (b.items || []).filter(x => !x.pending);
  const cur = now.reduce((a, x) => a + x.tokens, 0);
  const after = it.replaces ? it.tokens : cur + it.tokens;
  /* Three different truths, and the panel used to assert the first one always:
     armed = written, nothing changed; launched = started with the flag but
     unconfirmed; in_effect = the running session carries it, so it IS in the
     package -- it just came from a launch flag instead of the transcript, which
     is why reading the transcript cannot see it. */
  const live = !!it.live;
  const chip = live ? 'IN THE PACKAGE · VIA LAUNCH FLAG'
    : it.status === 'dropped' ? 'DROPPED BY A LATER LAUNCH'
    : it.status === 'launched' ? 'LAUNCHED · NOT CONFIRMED'
    : 'NOT IN THE PACKAGE YET';
  const col = live ? 'var(--live)' : 'var(--k-pending)';
  CURTEXT = it.text || ''; CURITEM = { bucket: b, item: it, data: { text: it.text } };
  $('#ex-k').textContent = `${b.label} · ${b.title}`;
  $('#tolabs').style.display = 'none';
  $('#ex-body').innerHTML = `
    <div class="chip" style="color:${col};border-color:${col}">${chip}</div>
    <h2>${it.label} — ${esc(it.title)}</h2>
    <div class="sub">from deployment ${esc(it.deploy)} · ${esc(it.status)} · ${num(it.chars)} chars</div>
    <div class="twostate">
      <div><span class="k">${live ? 'from the transcript' : 'now, in the package'}</span>
        <b>${num(cur)}</b><i>${now.length} section${now.length === 1 ? '' : 's'}</i></div>
      <div class="arrow">${live ? '+' : '\u2192'}</div>
      <div class="to" style="${live ? 'border-color:var(--live)' : ''}">
        <span class="k">${live ? 'this flag, live' : it.replaces ? 'replaced by this' : 'with this added'}</span>
        <b>${num(live ? it.tokens : after)}</b>
        <i>${live ? 'total ' + num(after) : (after >= cur ? '+' : '') + num(after - cur)}</i></div>
    </div>
    ${live ? `<div class="how" style="border-left-color:var(--live)">
      <b>This is in the package.</b> The session is running with deployment
      ${esc(it.deploy)}'s flag, so this text is sent on every request. It is shown
      separately because it is <i>not</i> in the transcript — a config change
      arrives through launch config, and <code>--system-prompt-snapshot off</code>
      deliberately records nothing.
      <div class="note">It stays in effect only while that process runs with that flag.</div></div>`
    : it.status === 'dropped' ? `<div class="how" style="border-left-color:var(--restart)">
      <b>No longer in the package.</b> Deployment ${esc(it.deploy)} was applied under an
      earlier launch, but the session is running now without its flag — a later config
      deployment relaunched it with a different one. Re-run the session carrying both
      if you want them together.</div>`
    : `<div class="how" style="border-left-color:var(--k-pending)">
      <b>Prepared, not applied.</b> ${it.replaces ? 'This would replace' : 'This would be appended to'}
      ${esc(b.label)} ${esc(b.title)} the next time the session is launched with
      deployment ${esc(it.deploy)}'s flags. Nothing above it has changed yet.
      <div class="note">Deployment \u25b8 ${esc(it.deploy)} \u25b8 Run session, then Verify.</div></div>`}
    <div class="row">
      <div class="seg"><button class="on" onclick="setRaw(false)">Rendered</button>
        <button onclick="setRaw(true)">Raw</button></div>
      <button onclick="copyText()">Copy</button>
    </div>
    <div class="k" style="margin-top:14px">${live ? 'what it is sending' : 'what would be added'}</div>
    <div class="body" id="cbody"></div>`;
  setRaw(RAW);
  $('#ex-side').classList.add('open'); kick();
}

const itemURL = (src) => `/api/item?key=${encodeURIComponent(DATA.session.key)}`
  + `&bucket=${encodeURIComponent(src.bucket)}&i=${src.i}`;

async function openItem(key, i) {
  const b = DATA.buckets.find(x => x.key === key);
  const it = levelItems(key).find(x => x.i === i);
  ITEM = i;
  if (it && it.pending) return openPending(b, it);
  /* always name the session explicitly — the server's fallback is the pinned
     ctx session, which is not necessarily what Explorer is showing */
  const src = it.src || { bucket: key, i };
  const d = await (await fetch(itemURL(src))).json();
  /* a tool call is only half the story — fetch what came back with it */
  let res = null;
  if (it.result_src) { try { res = await (await fetch(itemURL(it.result_src))).json(); } catch (e) { res = null; } }
  if (d.error) {
    /* never leave a filled-in header over an empty body -- say what happened */
    $('#ex-k').textContent = `${b.label} · ${b.title}`;
    $('#ex-body').innerHTML = `<div class="chip warn">unavailable</div>
      <h2>${esc(it ? it.label : '')}</h2>
      <div class="how" style="border-left-color:var(--restart)"><b>${esc(d.error)}</b>
        <div class="note">The fan and the store disagree about this item. Refresh (\u2318R).</div></div>`;
    $('#ex-side').classList.add('open'); kick(); return;
  }
  const sup = it && it.superseded;
  const LIMIT = 150000, cut = (d.text || '').length > LIMIT;
  CURTEXT = (d.text || '').slice(0, LIMIT);
  CURITEM = { bucket: b, item: it, data: d };
  $('#ex-k').textContent = `${b.label} · ${b.title}`;
  $('#tolabs').style.display = '';
  const pc2 = it.kind ? (KINDC[it.kind] || bandColor(b)) : bandColor(b);
  $('#ex-body').innerHTML = `
    <div class="chip" style="color:${pc2};border-color:${pc2}">${esc(b.badge)}</div>
    <h2>${it.label} — ${esc(d.title) || 'item'}</h2>
    <div class="sub">turn ${d.turn} · line ${d.line} · ${num(d.tokens)} est. tokens · ${num(d.chars)} chars</div>
    ${sup ? `<div class="how" style="border-left-color:var(--restart)">
      <b>Replaced — not being sent.</b> This is what the transcript records, but the
      session is running with a <code>--system-prompt-file</code> that replaces it.
      What is actually in the package is the injected section in this same fan.</div>` : ''}
    <div class="how" style="border-left-color:${pc2}"><b>${esc(b.class_title)}</b>${esc(b.how)}
      ${b.note ? `<div class="note">${esc(b.note)}</div>` : ''}</div>
    <div class="row">
      <div class="seg"><button class="on" onclick="setRaw(false)">Rendered</button>
        <button onclick="setRaw(true)">Raw</button></div>
      <button onclick="copyText()">Copy</button>
      <button onclick="openFile()">Open file</button>
    </div>
    ${imagesHtml(d)}
    ${d.no_text ? `<div class="how" style="border-left-color:var(--k-think)">
      <b>Nothing to show, and that is the finding.</b>
      <div class="note">${esc(d.why).replace(/\n\n/g, '<br><br>')}</div></div>` : ''}
    <div class="body" id="cbody"></div>
    ${res && !res.error ? `<div class="pairhd">\u2192 what came back<span>${num(res.tokens)} est. tokens \u00b7 line ${res.line}</span></div>
      ${imagesHtml(res)}
      <div class="body pairbody">${inlineMd(String(res.text || '').slice(0, 40000))}</div>
      ${(res.text || '').length > 40000 ? `<div class="trunc">Showing the first 40,000 of ${num((res.text || '').length)} characters.</div>` : ''}` : ''}
    ${cut ? `<div class="trunc">Showing the first ${num(LIMIT)} of ${num((d.text || '').length)} characters.</div>` : ''}`;
  setRaw(RAW);
  $('#ex-side').classList.add('open'); kick();
}
/* Images are lifted out of the text so they cannot distort the token estimate
   or flood the renderer — but the payload is still here, so show it. */
function imagesHtml(d) {
  const imgs = d.images || [];
  if (!imgs.length) return '';
  return `<div class="imgs">
    <span class="k">${imgs.length} image${imgs.length > 1 ? 's' : ''} · ${
      num(d.image_tokens || 0)} tokens of the ${num(d.tokens || 0)} above</span>
    ${imgs.map(im => `<figure class="imgfig">
      <img src="${im.uri}" alt="" loading="lazy" onclick="lightbox(this.src)">
      <figcaption><b>${im.w || '?'}×${im.h || '?'}</b><span>${im.kb} KB on disk</span>
        <span>${num(im.tokens)} tokens on the wire</span></figcaption>
    </figure>`).join('')}</div>`;
}
function lightbox(src) { $('#lbimg').src = src; $('#lightbox').classList.add('on'); }

function setRaw(r) {
  RAW = r;
  const segs = $('#ex-body') && $('#ex-body').querySelectorAll('.seg button');
  if (segs) segs.forEach((b, i) => b.classList.toggle('on', (i === 1) === r));
  const c = $('#cbody'); if (c) c.innerHTML = r ? `<pre>${esc(CURTEXT)}</pre>` : renderBody(CURTEXT);
}
function copyText() { navigator.clipboard && navigator.clipboard.writeText(CURTEXT); }
function closeSide() { ITEM = null; $('#ex-side').classList.remove('open');
  $('#tolabs').style.display = 'none'; kick(); }
async function openFile() {
  const r = await (await fetch('/api/open', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key }) })).json();
  if (r.error) say('Could not open the file', esc(r.error), 'warn');
}
/* Explorer -> Labs: pin the open item onto the board as a context node */
async function toLabs() {
  if (!CURITEM) return;
  const { bucket, item, data } = CURITEM;
  await fetch('/api/board/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      key: DATA.session.key,
      kind: 'context',
      title: `${item.label} · ${data.title || bucket.title}`,
      body: (data.text || '').slice(0, 20000),
      ref: { bucket: bucket.key, i: item.i, line: data.line, edit_class: bucket.edit_class }
    })
  });
  await loadBoard();
  setPage('labs');
}

/* ---- footer strip ---- */
function strip() {
  const d = DATA, s = d.session;
  const eph = (d.ephemeral || []).reduce((a, b) => a + b.tokens, 0);
  const fixed = d.fixed || [], cut = d.cut || {};
  const fixedNames = fixed.map(b => b.label + ' ' + b.title).join(' \u00b7 ');
  const share = d.est_total ? Math.round(100 * (d.editable_total || 0) / d.est_total) : 0;
  $('#strip').innerHTML = `
    <div class="fc" title="Everything in the package the CLI sends: the newest snapshot, the compact summary, and every turn after it."><div class="k">context</div><v>${num(d.est_total)}</v></div>
    <div class="fc" title="The provider's own input count for the last call. Exact."><div class="k">exact</div><v>${d.exact_total ? num(d.exact_total) : '\u2014'}</v></div>
    <div class="fc" title="${d.calibrated ? 'Bucket shares come from measured chars/token; the total comes from the provider. Character rules alone were ' + (d.drift_pct > 0 ? '+' : '') + d.drift_pct + '% out, so every bucket is scaled by this.' : 'No provider count in this transcript \u2014 raw estimates, uncalibrated.'}"><div class="k">calibration</div><v>${d.calibrated ? '\u00d7' + d.calibration : 'raw'}</v></div>
    <div class="fc" title="In the package and changeable. This is what Explorer draws."><div class="k">editable</div><v>${num(d.editable_total || 0)} <i style="font-style:normal;color:var(--faint)">${share}%</i></v></div>
    <div class="fc" title="${fixed.length ? 'In the package, no lever: ' + esc(fixedNames) : 'Nothing fixed'}"><div class="k">fixed</div><v>${d.fixed_total ? num(d.fixed_total) : '\u2014'}</v></div>
    <div class="fc" title="Reasoning the model produced over this session's life. Output, not context \u2014 the text was never stored and is never re-sent. Never added to the total."><div class="k">reasoning out</div><v>${d.reasoning_generated ? num(d.reasoning_generated) : (eph ? num(eph) : '\u2014')}</v></div>
    <div class="fc"${cut.compactions ? ' title="Records before the last compaction. The CLI discarded them; the transcript keeps them; they are not in the package."' : ''}><div class="k">cut as history</div><v>${
      cut.compactions ? num(cut.records_dropped) + ' / ' + num(cut.records_total) : '\u2014'}</v></div>
    <div class="fc path"><div class="k">store location</div>
      <v onclick="openFile()" title="${esc(s.transcript)}">${esc(s.transcript)}</v></div>`;
}

/* ═══════════════ LABS ═══════════════ */
let lx = 40, ly = 40, lk = 1, LNODE = null, WIRE = false, WIREFROM = null, LTAB = 'detail';
let RUNS = { runs: [] }, EDIT = null, PROJ = [], DRAGGING = false;
/* ═══════════════ LABS CANVAS ═══════════════
   One transformed layer holds both the edges and the nodes, so they cannot
   drift apart at any zoom. Nodes are reconciled in place rather than rebuilt,
   so selecting one does not flash the whole board. The view transform is
   applied once per animation frame, so a fast wheel or drag coalesces instead
   of forcing a layout per event. */
const SVGO = 20000;                       // svg origin offset, lets edges go negative
let vx = 40, vy = 40, vk = 1;             // view transform
let viewDirty = false, viewRAF = 0;
const NODEEL = new Map();                 // label -> element
const NSIZE = new Map();                  // label -> {w,h}

function applyView() {
  viewDirty = false;
  $('#lcanvas').style.transform = `translate(${vx}px,${vy}px) scale(${vk})`;
  const lab = $('#lab');
  lab.style.backgroundSize = `${24 * vk}px ${24 * vk}px`;
  lab.style.backgroundPosition = `${vx}px ${vy}px`;
  const pct = $('#zoompct'); if (pct) pct.textContent = Math.round(vk * 100) + '%';
}
function queueView() {
  if (viewDirty) return;
  viewDirty = true;
  viewRAF = requestAnimationFrame(applyView);
}
function lT() { queueView(); }

function zoomAt(cx, cy, k) {
  const nk = clamp(k, 0.15, 3);
  vx = cx - (cx - vx) * (nk / vk); vy = cy - (cy - vy) * (nk / vk); vk = nk;
  queueView();
}
function zoomBy(f) {
  const c = $('#lab').getBoundingClientRect();
  zoomAt(c.width / 2, c.height / 2, vk * f);
}
function zoomReset() {
  const c = $('#lab').getBoundingClientRect();
  zoomAt(c.width / 2, c.height / 2, 1);
}

/* ---- reconcile: create, update, remove — never wholesale rebuild ---- */
/* The canvas is a pipeline: gather -> work -> review. A card shows the one
   thing that matters at its stage, so you can read the board without opening
   anything: what an extraction found, whether a cell ran, what a review still
   has outstanding. */
/* A card shows prose, not its source. Markdown markers, rules and table pipes
   are noise at 10px in a 206px box -- what is left is the sentence. */
function plainPreview(t) {
  return String(t || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^\s*[-*_]{3,}\s*$/gm, ' ')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\|/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function nodeHTML(n) {
  const isCode = n.kind === 'code' || n.kind === 'analysis';
  const body = (n.body || '').trim();
  const img = (n.result && (n.result.images || [])[0]) || null;
  const meta = [];
  let preview, head = '';

  if (n.kind === 'extraction') {
    const ms = n.matches || [];
    const tok = ms.reduce((a, m) => a + (m.tokens || 0), 0);
    head = n.query
      ? `<div class="lq">${esc(n.query)}</div>`
      : '<div class="lq empty">no query yet</div>';
    preview = ms.length
      ? ms.slice(0, 8).map(m => m.label).join(' ') + (ms.length > 8 ? ` +${ms.length - 8}` : '')
      : 'nothing gathered yet';
    if (ms.length) meta.push(`${ms.length} record${ms.length === 1 ? '' : 's'}`, fmt(tok) + ' tok');
    if (n.summary) meta.push('summarised');
  } else if (n.kind === 'review') {
    const sent = n.sent || [], audit = n.audit || [];
    const concerns = audit.filter(a => a.verdict === 'concern').length;
    preview = sent.length
      ? sent.map(x => x.from).join(', ') + ' \u2192 ' + (sent[sent.length - 1].body || '').slice(0, 90)
      : 'nothing sent for review yet';
    if (sent.length) meta.push(`${sent.length} sent`);
    if (audit.length) meta.push(`${audit.length} audited`);
    if (concerns) meta.push(`${concerns} concern${concerns === 1 ? '' : 's'}`);
    if (n.state && n.state !== 'open') meta.push(n.state);
  } else {
    preview = isCode ? (n.body || '').split('\n').slice(0, 3).join('\n')
      : (plainPreview(body) || 'empty — ask the agent to fill it');
    if (isCode && n.result) meta.push(n.result.ok ? `${n.result.ms || 0} ms` : 'failed');
  }

  return `<div class="lhd"><span class="ll">${n.label}</span>
      <span class="lk">${esc(n.kind)}</span>
      <span class="lwhen">${n.updated ? ago(n.updated) : ''}</span></div>
    <div class="lt">${esc(n.title || 'Untitled')}</div>
    ${head}
    <div class="lb${isCode ? ' mono' : ''}">${esc(preview)}</div>
    ${img ? `<img class="thumb" src="${img}" alt="">` : ''}
    ${meta.length ? `<div class="lft">${meta.map(esc).join('\u2009\u00b7\u2009')}</div>` : ''}
    <span class="port l"></span><span class="port r"></span>`;
}

function syncNodes() {
  const host = $('#lnodes');
  const seen = new Set();
  for (const n of BOARD.nodes) {
    seen.add(n.label);
    let el2 = NODEEL.get(n.label);
    if (!el2) {
      el2 = document.createElement('div');
      el2.dataset.l = n.label;
      host.appendChild(el2);
      NODEEL.set(n.label, el2);
      nodeDrag(el2);
    }
    const sig = JSON.stringify([n.kind, n.title, n.body, n.updated,
      n.result && n.result.at, n.result && (n.result.images || []).length]);
    if (el2.dataset.sig !== sig) { el2.innerHTML = nodeHTML(n); el2.dataset.sig = sig; }
    el2.className = 'lnode ' + n.kind
      + (LNODE === n.label ? ' sel' : '') + (WIREFROM === n.label ? ' wire' : '');
    const x = n.x || 0, y = n.y || 0;
    if (el2._x !== x || el2._y !== y) {
      el2.style.left = x + 'px'; el2.style.top = y + 'px'; el2._x = x; el2._y = y;
    }
    NSIZE.set(n.label, { w: el2.offsetWidth || 224, h: el2.offsetHeight || 96 });
  }
  for (const [label, el2] of NODEEL) {
    if (!seen.has(label)) { el2.remove(); NODEEL.delete(label); NSIZE.delete(label); }
  }
}
function paintLab() { syncNodes(); drawEdges(); queueView(); }

/* selection only toggles classes — no rebuild, no flash */
function markSelection() {
  for (const [label, el2] of NODEEL) {
    el2.classList.toggle('sel', LNODE === label);
    el2.classList.toggle('wire', WIREFROM === label);
  }
}

function drawEdges() {
  const svg = $('#ledges');
  const byId = Object.fromEntries(BOARD.nodes.map(n => [n.id, n]));
  const parts = [`<defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4"
      markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L8,4 L0,8 z" fill="var(--line2)"/></marker></defs>`];
  for (const e of BOARD.edges || []) {
    const A = byId[e.from], B = byId[e.to];
    if (!A || !B) continue;
    const a = NSIZE.get(A.label) || { w: 224, h: 96 }, b = NSIZE.get(B.label) || { w: 224, h: 96 };
    const x1 = (A.x || 0) + a.w + SVGO, y1 = (A.y || 0) + a.h / 2 + SVGO;
    const x2 = (B.x || 0) + SVGO, y2 = (B.y || 0) + b.h / 2 + SVGO;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
    parts.push(`<path d="M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}"
      fill="none" stroke="var(--line2)" stroke-width="1.75" marker-end="url(#ar)"/>`);
  }
  svg.innerHTML = parts.join('');
}

function nodeDrag(el2) {
  let sx, sy, ox, oy, moved = false;
  el2.addEventListener('mousedown', ev => {
    if (ev.button !== 0) return;
    ev.stopPropagation();
    moved = false; DRAGGING = true;
    sx = ev.clientX; sy = ev.clientY; ox = el2._x || 0; oy = el2._y || 0;
    const mv = m => {
      const dx = (m.clientX - sx) / vk, dy = (m.clientY - sy) / vk;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      moved = true;
      el2._x = ox + dx; el2._y = oy + dy;
      el2.style.left = el2._x + 'px'; el2.style.top = el2._y + 'px';
      const n = BOARD.nodes.find(v => v.label === el2.dataset.l);
      if (n) { n.x = el2._x; n.y = el2._y; }
      drawEdges();
    };
    const up = () => {
      removeEventListener('mousemove', mv); removeEventListener('mouseup', up);
      DRAGGING = false;
      if (moved) {
        fetch('/api/board/move', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: DATA.session.key, label: el2.dataset.l,
            x: el2._x, y: el2._y }) });
      } else if (WIRE) { wireClick(el2.dataset.l); }
      else openNode(el2.dataset.l);
    };
    addEventListener('mousemove', mv); addEventListener('mouseup', up);
  });
}

function fitLab() {
  if (!BOARD.nodes.length) return;
  let mnx = 1e9, mny = 1e9, mxx = -1e9, mxy = -1e9;
  for (const n of BOARD.nodes) {
    const s2 = NSIZE.get(n.label) || { w: 224, h: 96 };
    mnx = Math.min(mnx, n.x || 0); mny = Math.min(mny, n.y || 0);
    mxx = Math.max(mxx, (n.x || 0) + s2.w); mxy = Math.max(mxy, (n.y || 0) + s2.h);
  }
  const c = $('#lab').getBoundingClientRect();
  const pad = 60;
  vk = clamp(Math.min((c.width - pad * 2) / (mxx - mnx || 1),
                      (c.height - pad * 2) / (mxy - mny || 1), 1), 0.15, 1);
  vx = (c.width - (mxx - mnx) * vk) / 2 - mnx * vk;
  vy = (c.height - (mxy - mny) * vk) / 2 - mny * vk;
  queueView();
}

/* ---- viewport: wheel zooms, wheel+shift or drag pans, all rAF-batched ---- */
(function () {
  const c = $('#lab');
  let panning = false, sx = 0, sy = 0, space = false;
  c.addEventListener('wheel', e => {
    e.preventDefault();
    const r = c.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (e.ctrlKey || e.metaKey || !e.shiftKey && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      // trackpad pinch arrives as ctrl+wheel; a plain wheel zooms like Figma
      zoomAt(mx, my, vk * Math.pow(0.999, e.deltaY * (e.ctrlKey ? 3 : 1.6)));
    } else {
      vx -= e.deltaX; vy -= e.deltaY; queueView();
    }
  }, { passive: false });
  c.addEventListener('mousedown', e => {
    if (e.target.closest('.lnode') && !space) return;
    panning = true; sx = e.clientX - vx; sy = e.clientY - vy; c.classList.add('grab');
  });
  addEventListener('mousemove', e => {
    if (!panning) return;
    vx = e.clientX - sx; vy = e.clientY - sy; queueView();
  });
  addEventListener('mouseup', () => { panning = false; c.classList.remove('grab'); });
  addEventListener('keydown', e => { if (e.code === 'Space') space = true; });
  addEventListener('keyup', e => { if (e.code === 'Space') space = false; });
})();

async function loadBoard() {
  if (!DATA) return;
  const d = await (await fetch('/api/board?key=' + encodeURIComponent(DATA.session.key))).json();
  const changed = d.version !== BOARD.version || d.nodes.length !== BOARD.nodes.length;
  BOARD = d;
  /* Reconciled in place, so a poll never disturbs a drag or an open editor. */
  if (PAGE === 'labs' && changed && !DRAGGING && !EDIT) paintLab();
}

/* ---- connect mode ---- */
function toggleWire() {
  WIRE = !WIRE; WIREFROM = null;
  $('#wirebtn').classList.toggle('on', WIRE);
  $('#lab').classList.toggle('wiring', WIRE);
  $('#labhint').textContent = WIRE
    ? 'connect mode — click a source node, then a target'
    : 'drag to pan · scroll to zoom · shift+scroll to pan sideways';
  markSelection();
}
async function wireClick(label) {
  if (!WIREFROM) { WIREFROM = label; markSelection(); return; }
  if (WIREFROM === label) { WIREFROM = null; markSelection(); return; }
  const r = await (await fetch('/api/board/connect', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, from: WIREFROM, to: label }) })).json();
  WIREFROM = null;
  /* the flow is one-way, and a refusal has to say which way */
  const why = (r.board || {}).error;
  if (why) flash(why);
  await loadBoard(); paintLab();
}

/* ---- node editor ---- */
const CELL_SEED = `import matplotlib.pyplot as plt

b = ctx.buckets[:8]
fig, ax = plt.subplots(figsize=(6, 3))
ax.barh([x['label'] for x in b], [x['tokens'] for x in b])
ax.invert_yaxis(); ax.set_title('tokens by bucket')

print(ctx.bucket_df().to_string(index=False))`;
function newNode(kind) {
  LNODE = null; LTAB = 'detail';
  EDIT = { mode: 'new', kind, title: '', body: kind === 'code' ? CELL_SEED : '' };
  labSide(true); paintLabSide();
}
function editNode() {
  const n = BOARD.nodes.find(x => x.label === LNODE); if (!n) return;
  EDIT = { mode: 'edit', label: n.label, kind: n.kind, title: n.title, body: n.body || '' };
  paintLabSide();
}
function cancelEdit() { EDIT = null; paintLabSide(); }
async function saveNode() {
  const title = $('#f-title').value.trim(), body = $('#f-body').value, kind = $('#f-kind').value;
  if (!title) { await say('Title required', 'Every node needs a title.', 'warn');
    const f = $('#f-title'); if (f) f.focus(); return; }
  const url = EDIT.mode === 'new' ? '/api/board/add' : '/api/board/update';
  const c = $('#lab').getBoundingClientRect();
  const payload = EDIT.mode === 'new'
    ? { key: DATA.session.key, kind, title, body,
        x: Math.round((c.width / 2 - vx) / vk - 112),
        y: Math.round((c.height / 2 - vy) / vk - 48) }
    : { key: DATA.session.key, label: EDIT.label, kind, title, body };
  const r = await (await fetch(url, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json();
  EDIT = null;
  if (r.node) LNODE = r.node.label;
  await loadBoard(); paintLab(); paintLabSide();
}

/* ---- pipelines ---- */
function pipeFrom(label) {
  const n = BOARD.nodes.find(x => x.label === label); if (!n) return [];
  const byId = Object.fromEntries(BOARD.nodes.map(x => [x.id, x]));
  const out = [], seen = new Set([n.id]); let q = [n.id];
  while (q.length) {
    const c = q.shift(); out.push(byId[c]);
    (BOARD.edges || []).forEach(e => {
      if (e.from === c && !seen.has(e.to)) { seen.add(e.to); q.push(e.to); }
    });
  }
  return out;
}
function syncRunBtn() {
  const b = $('#runbtn'); if (!b) return;
  const chain = LNODE ? pipeFrom(LNODE) : [];
  b.disabled = chain.length < 2;
  b.title = chain.length > 1 ? 'Run ' + chain.map(n => n.label).join(' → ')
                             : 'Select a node that has outgoing connections';
}
async function runPipeline() {
  if (!LNODE) return;
  const r = await (await fetch('/api/pipeline/run', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, start: LNODE }) })).json();
  if (!r.ok) { say('Pipeline did not start', esc(r.error || 'Unknown error.'), 'warn'); return; }
  await loadRuns(); await loadBoard(); paintLab();
  openNode(r.output_node);
}
async function loadSaved() {
  if (!WS) return;
  try {
    const d = await (await fetch('/api/ctxs')).json();
    if ((d.active || {}).ctx_session === WS.id) { WS.saved = d.active.saved || 0; status(); }
  } catch (e) {}
}
async function loadRuns() {
  if (!DATA) return;
  RUNS = await (await fetch('/api/runs?key=' + encodeURIComponent(DATA.session.key))).json();
  status();
}
const pendingRuns = () => (RUNS.runs || []).filter(r => r.status === 'queued').length;
const runFor = label => (RUNS.runs || []).find(r => r.output_node === label);
function runsHtml() {
  const rs = (RUNS.runs || []).slice().reverse();
  if (!rs.length) return '<div class="empty">No pipeline runs yet.<br><br>' +
    'Select a node that has outgoing connections, then press <b>Run pipeline</b>.</div>';
  return rs.map(r => `<div class="drow ${LNODE === r.output_node ? 'sel' : ''}"
      onclick="openNode('${r.output_node}')">
      <div class="t"><span class="l">${r.label}</span>
        <span class="n">${esc(r.chain.join(' → '))}</span>
        <span class="st ${r.status === 'done' ? 'deployed' : 'staged'}">${r.status}</span></div>
      <div class="m">→ ${r.output_node} · ${ago(r.created)}</div></div>`).join('');
}

function openNode(label) {
  LNODE = label; LTAB = 'detail'; EDIT = null;
  labSide(true);
  paintLabSide(); paintLab();
}
function setLabTab(t) { LTAB = t; paintLabSide(); }
function paintLabSide() {
  const fresh = LOG.length > LOGSEEN, q = pendingRuns();
  $('#lab-tabs').innerHTML = `
    <button class="${LTAB === 'detail' ? 'on' : ''}" onclick="setLabTab('detail')">Node</button>
    <button class="${LTAB === 'runs' ? 'on' : ''}" onclick="setLabTab('runs')">Runs${
      q ? `<span class="badgeq">${q}</span>` : ''}</button>
    <button class="${LTAB === 'log' ? 'on' : ''}" onclick="setLabTab('log')">Activity${
      fresh && LTAB !== 'log' ? '<i class="dot"></i>' : ''}</button>`;
  const b = $('#lab-body');
  syncRunBtn();
  if (LTAB === 'log') { LOGSEEN = LOG.length; b.innerHTML = logHtml(); return; }
  if (LTAB === 'runs') { b.innerHTML = runsHtml(); return; }

  if (EDIT) {
    b.innerHTML = `
      <span class="k">${EDIT.mode === 'new' ? 'New node' : 'Editing ' + EDIT.label}</span>
      <label class="f"><span class="k">Kind</span>
        <select id="f-kind">${['extraction', 'analysis', 'review', 'note', 'prompt']
          .map(k => `<option ${k === EDIT.kind ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
      <label class="f"><span class="k">Title</span>
        <input type="text" id="f-title" value="${esc(EDIT.title)}" placeholder="What is this node?"></label>
      <label class="f"><span class="k">Body</span>
        <textarea id="f-body" placeholder="${EDIT.kind === 'prompt'
          ? 'The instruction this step carries out…'
          : EDIT.kind === 'code' ? '# ctx.buckets, ctx.items(\'B\'), ctx.df(), ctx.nodes, plt…'
          : 'Markdown…'}">${esc(EDIT.body)}</textarea></label>
      <div class="row"><button class="pri" onclick="saveNode()">Save</button>
        <button onclick="cancelEdit()">Cancel</button></div>`;
    setTimeout(() => { const f = $('#f-title'); if (f) f.focus(); }, 0);
    return;
  }

  const n = BOARD.nodes.find(x => x.label === LNODE);
  if (!n) { b.innerHTML = '<div class="empty">Select a node, or create one from the toolbar.' +
    '<br><br>Your other session can also create and fill nodes over MCP.</div>'; return; }
  const body = (n.body || '').trim();
  const outs = (BOARD.edges || []).filter(e => e.from === n.id)
    .map(e => (BOARD.nodes.find(x => x.id === e.to) || {}).label).filter(Boolean);
  const ins = (BOARD.edges || []).filter(e => e.to === n.id)
    .map(e => (BOARD.nodes.find(x => x.id === e.from) || {}).label).filter(Boolean);
  const chain = pipeFrom(n.label), run = runFor(n.label);
  b.innerHTML = `
    <div class="nhead">
      <span class="nlabel">${esc(n.label)}</span>
      <span class="nkind ${esc(n.kind)}">${esc(n.kind)}</span>
      <span class="nwhen">${ago(n.updated)}</span>
    </div>
    <h2 class="ntitle">${esc(n.title || 'Untitled')}</h2>
    ${nodeStats(n)}
    ${(ins.length || outs.length) ? `<div class="wires">
      ${ins.length ? `<span><i>in</i>${ins.map(esc).join(' ')}</span>` : ''}
      ${outs.length ? `<span><i>out</i>${outs.map(esc).join(' ')}</span>` : ''}
      ${chain.length > 1 ? `<span><i>chain</i>${chain.map(x => esc(x.label)).join(' \u2192 ')}</span>` : ''}
      ${run ? `<span><i>run</i>${esc(run.label)} ${esc(run.status)}</span>` : ''}
    </div>` : ''}
    ${run && run.status === 'queued' ? `<div class="how" style="border-left-color:var(--restart)">
        <b>${run.label} is queued</b>ctx resolved the chain into one brief and is waiting for your agent.
        <div class="note">Tell your other session: &ldquo;run the pending ctx pipeline&rdquo;. It calls
        <code>pipeline_pending()</code> then <code>pipeline_complete()</code>.</div></div>` : ''}
    <div class="row">
      <button onclick="editNode()">Edit</button>
      ${n.kind !== 'session' ? `<button class="warn" onclick="delNode('${n.label}')">Delete</button>` : ''}
    </div>
    ${n.kind === 'extraction' ? extractPane(n) : ''}
    ${n.kind === 'review' ? reviewPane(n) : ''}
    ${n.kind === 'code' || n.kind === 'analysis' ? `<div class="cell">
        <textarea class="cellsrc" id="cellsrc" spellcheck="false">${esc(n.body || '')}</textarea>
        <div class="row"><button class="pri" id="runcell" onclick="runCell()">Run cell ⇧⏎</button>
          <button onclick="saveCell()">Save source</button></div>
        ${cellOutput(n.result)}</div>` : ''}
    ${n.kind === 'analysis' ? analysisSend(n) : ''}
    ${body && !['code', 'analysis', 'extraction', 'review'].includes(n.kind) ? `<div class="row">
        <select id="f-dkind">
          <option value="inject_system_prompt">inject into the recorded prompt</option>
          <option value="append_system_prompt">append via launch flag</option>
          <option value="system_prompt_file">replace via launch flag</option>
        </select>
        <button class="pri" onclick="stageNode($('#f-dkind').value)">Send to Deployment</button>
      </div><div class="body">${renderBody(body)}</div>`
      : ['code', 'analysis', 'extraction', 'review'].includes(n.kind) ? '' : `<div class="how"><b>Nothing written yet</b>Write it yourself with <b>Edit</b>, or ask your
         other session: &ldquo;analyse ${n.label} and write it to the board&rdquo;.
         <div class="note">It calls <code>board_write(node:"${n.label}", body:"…")</code>.</div></div>`}`;
}

async function delNode(label) {
  if (!await ask(`Delete ${label}?`,
    'The node and every connection to it are removed. Nothing else is affected.',
    { ok: 'Delete', danger: true })) return;
  await fetch('/api/board/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) });
  LNODE = null; labSide(false); await loadBoard();
}
async function stageNode(kind) {
  const n = BOARD.nodes.find(x => x.label === LNODE); if (!n) return;
  await fetch('/api/deploy/stage', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, kind,
      title: `${n.label} · ${n.title}`, payload: { text: n.body || '' } }) });
  await loadDeploy(); setPage('deploy');
}
/* EXTRACTION — one question, and the records that answer it. */
/* The numbers that matter at this stage, stated once and large. A panel that
   leads with data reads faster than one that leads with prose. */
function nodeStats(n) {
  let cells = [];
  if (n.kind === 'extraction') {
    const ms = n.matches || [];
    cells = [['records', num(ms.length)],
             ['tokens', fmt(ms.reduce((a, m) => a + (m.tokens || 0), 0))],
             ['buckets', String(new Set(ms.map(m => m.bucket)).size || 0)]];
  } else if (n.kind === 'analysis' || n.kind === 'code') {
    const r = n.result || {};
    const ins = (BOARD.edges || []).filter(e => e.to === n.id).length;
    cells = [['feeds in', String(ins)],
             ['last run', r.at ? (r.ms || 0) + ' ms' : '\u2014'],
             ['figures', String((r.images || []).length)]];
  } else if (n.kind === 'review') {
    const audit = n.audit || [];
    cells = [['sent', String((n.sent || []).length)],
             ['audited', String(audit.length)],
             ['concerns', String(audit.filter(a => a.verdict === 'concern').length)]];
  } else { return ''; }
  return `<div class="stats">${cells.map(([k, v]) =>
    `<div><i>${esc(k)}</i><b>${esc(v)}</b></div>`).join('')}</div>`;
}

function extractPane(n) {
  const ms = n.matches || [];
  const tok = ms.reduce((a, m) => a + (m.tokens || 0), 0);
  return `<div class="stage">
    <div class="k">what to look for</div>
    <div class="row">
      <input type="text" id="x-q" class="grow" placeholder="keyword or phrase\u2026"
             value="${esc(n.query || '')}" onkeydown="if(event.key==='Enter')runExtract('${n.label}')">
      <label class="chk" title="treat the query as a regular expression">
        <input type="checkbox" id="x-re"> regex</label>
      <button class="pri" onclick="runExtract('${n.label}')">Gather</button>
    </div>
    ${ms.length ? `<div class="xsum"><b>${num(ms.length)}</b> records ·
        <b>${num(tok)}</b> tokens · in the order they happened</div>
      <div class="xlist">${ms.map(m => `<div class="xrow" onclick="openFromLab('${m.bucket}',${m.i})">
        <span class="xl">${esc(m.label)}</span>
        <span class="xb">${esc(m.bucket_title)}</span>
        <span class="xt">${esc(m.excerpt || m.title || '')}</span>
        <span class="xn">${fmt(m.tokens)}</span></div>`).join('')}</div>`
      : `<div class="how"><b>Nothing gathered yet</b>Ask it a question above, or let your other
         session do it: <div class="note"><code>extract_run(node:"${n.label}", query:"\u2026")</code></div></div>`}
    ${n.summary ? `<div class="k" style="margin-top:14px">summary</div>
      <div class="body">${renderBody(n.summary)}</div>`
      : ms.length ? `<div class="note" style="margin-top:10px">No summary. Optional \u2014 the records
         stand on their own. Ask the agent for one with
         <code>extract_summary(node:"${n.label}", \u2026)</code>.</div>` : ''}
  </div>`;
}

async function runExtract(label) {
  const q = ($('#x-q') || {}).value || '';
  const re = !!(($('#x-re') || {}).checked);
  if (!q.trim()) return flash('Give it something to look for.');
  const r = await (await fetch('/api/extract/run', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label, query: q, regex: re }) })).json();
  if (!r.ok) return say('Extraction failed', esc(r.error || ''), 'warn');
  await loadBoard();
  flash(`${label}: ${r.count} record${r.count === 1 ? '' : 's'} · ${num(r.tokens)} tokens`
    + (r.truncated ? ' (capped)' : ''));
}

/* Jump from a gathered record straight to it in Explorer. */
function openFromLab(bucket, i) {
  setPage('explorer');
  drill(bucket);
  setTimeout(() => openItem(bucket, i), 60);
}

/* ANALYSIS — hand the conclusion to a review node. */
function analysisSend(n) {
  const reviews = BOARD.nodes.filter(x => x.kind === 'review');
  if (!reviews.length) return `<div class="note">Create a review node to send this to.</div>`;
  return `<div class="stage">
    <div class="k">send for review</div>
    <div class="row">
      <select id="a-to">${reviews.map(r =>
        `<option value="${esc(r.label)}">${esc(r.label)} — ${esc(r.title || 'review')}</option>`).join('')}</select>
      <button class="pri" onclick="sendToReview('${n.label}')">Send</button>
    </div>
    <textarea id="a-note" class="sendnote" placeholder="What does this analysis conclude? (optional \u2014 the cell body and its output go too)"></textarea>
  </div>`;
}

async function sendToReview(from) {
  const to = ($('#a-to') || {}).value;
  const note = ($('#a-note') || {}).value || '';
  const r = await (await fetch('/api/review/send', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, from, to, note }) })).json();
  if (!r.ok) return say('Could not send', esc(r.error || ''), 'warn');
  await loadBoard();
  flash(`${from} sent to ${to} for review.`);
}

/* REVIEW — what arrived, what an audit found, and the one way out. */
function reviewPane(n) {
  const sent = n.sent || [], audit = n.audit || [];
  const V = { pass: 'var(--live)', concern: 'var(--restart)', note: 'var(--faint)' };
  return `<div class="stage">
    <div class="k">sent for review</div>
    ${sent.length ? sent.map(x => `<div class="sentbox">
        <div class="sh"><b>${esc(x.from)}</b><span>${esc(x.title || '')}</span><i>${ago(x.at)}</i></div>
        ${x.body ? `<div class="body">${renderBody(x.body)}</div>` : ''}
        ${x.stdout ? `<pre class="oprec">${esc(x.stdout.slice(0, 4000))}</pre>` : ''}
        ${(x.images || []).map(src => `<img class="thumb wide" src="${src}" alt="figure">`).join('')}
      </div>`).join('')
      : `<div class="how"><b>Nothing sent yet</b>An analysis node sends its conclusion here with
         <b>Send</b>. Nothing reaches Deployment except through a review.</div>`}

    <div class="k" style="margin-top:14px">audit</div>
    ${audit.length ? `<div class="auditlist">${audit.map(a => `<div class="arow">
        <span class="av" style="color:${V[a.verdict] || 'var(--faint)'}">${esc(a.verdict)}</span>
        <span class="an">${esc(a.note)}</span>
        <span class="at">${esc(a.by || '')} · ${ago(a.at)}</span></div>`).join('')}</div>`
      : `<div class="note">No audit yet. Ask your other session:
         &ldquo;audit ${n.label} and say whether it is safe&rdquo; \u2014 it calls
         <code>review_read</code> then <code>review_audit</code>. An agent can never apply anything.</div>`}

    <div class="k" style="margin-top:14px">propose to Deployment</div>
    ${n.state === 'staged' && n.proposal ? `<div class="how" style="border-left-color:var(--live)">
        <b>Staged as ${esc(n.proposal.deploy)}.</b> It is on the Deployment page now, still unapplied \u2014
        applying is a separate, human click.</div>`
      : `<div class="row">
        <select id="r-kind">
          <option value="inject_system_prompt">inject into the recorded prompt</option>
          <option value="append_system_prompt">append via launch flag</option>
          <option value="system_prompt_file">replace via launch flag</option>
        </select>
        <button class="pri" onclick="stageReview('${n.label}')">Stage for deployment</button>
      </div>
      <textarea id="r-text" class="sendnote" placeholder="The exact text this deployment carries\u2026"></textarea>`}
  </div>`;
}

async function stageReview(label) {
  const kind = ($('#r-kind') || {}).value;
  const text = ($('#r-text') || {}).value || '';
  if (!text.trim()) return flash('A deployment needs the text it carries.');
  const r = await (await fetch('/api/review/stage', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label, kind, payload: { text } }) })).json();
  if (!r.ok) return say('Could not stage', esc(r.error || ''), 'warn');
  await loadBoard(); await loadDeploy();
  say('Staged for deployment', `${label} became <b>${esc(r.item.label)}</b> on the Deployment page. ` +
    'Nothing is applied until you deploy it there.');
}

function logHtml() {
  if (!LOG.length) return '<div class="empty">No MCP calls yet.<br><br>' +
    'Connect a second session with <code>claude mcp add ctx</code> and ask it about this one.</div>';
  return LOG.slice().reverse().map(r => {
    const a = Object.entries(r.args || {}).map(([k, v]) => k + '=' + String(v).slice(0, 40)).join(' ');
    return `<div class="logrow ${r.ok ? '' : 'err'}">
      <time>${new Date(r.ts * 1000).toLocaleTimeString()}</time>
      <b>${esc(r.tool)}</b><span>${esc(r.note || a || '')}</span></div>`;
  }).join('');
}

/* ═══════════════ DEPLOYMENT ═══════════════ */
let DSEL = null;
async function loadDeploy() {
  if (!DATA) return;
  DEP = await (await fetch('/api/deploy?key=' + encodeURIComponent(DATA.session.key))).json();
  const n = (DEP.items || []).filter(i => i.status === 'staged').length;
  $('#dcount').textContent = n ? ` ${n}` : '';
  if (PAGE === 'deploy') paintDeploy();
}
function paintDeploy() {
  const items = DEP.items || [];
  $('#dlist').innerHTML = items.length ? items.map(i => `
    <div class="drow ${DSEL === i.label ? 'sel' : ''}" onclick="selDeploy('${i.label}')">
      <div class="t"><span class="l">${i.label}</span><span class="n">${esc(i.title)}</span>
        <span class="st ${i.status}">${i.status}</span></div>
      <div class="m">${esc(i.kind)} · ${ago(i.created)}</div>
    </div>`).join('')
    : '<div class="empty">Nothing staged.<br><br>Send a node from Labs, or ask the agent to call <code>deploy_stage</code>.</div>';
  paintDPane();
}
function selDeploy(l) { DSEL = l; paintDeploy(); }
function paintDPane() {
  const p = $('#dpane');
  const i = (DEP.items || []).find(x => x.label === DSEL);
  const okBanner = DEP.transcript_ok
    ? `<div class="banner ok"><b>Transcript validates</b>${esc(DEP.transcript_note || '')}</div>`
    : `<div class="banner"><b>Transcript does not validate</b>${esc(DEP.transcript_note || '')}</div>`;
  const runBanner = DEP.running
    ? `<div class="banner"><b>Session is running</b>Transcript deployments are refused while the
        process is alive — it holds the conversation in RAM and would append over the edit.
        Stop it first. Config deployments are unaffected.</div>` : '';
  if (!i) { p.innerHTML = okBanner + runBanner +
    '<div class="empty">Select a deployment on the left.</div>'; return; }
  const cfg = i.kind === 'system_prompt_file' || i.kind === 'append_system_prompt';
  const pay = i.payload || {};
  p.innerHTML = okBanner + runBanner + `
    <h2>${i.label} — ${esc(i.title)}</h2>
    <div class="sub">${esc(i.kind)} · <b class="st ${i.status}">${esc(i.status)}</b> · staged ${ago(i.created)}
      ${i.deployed_at ? '· launched ' + ago(i.deployed_at) : ''}</div>
    ${i.kind === 'inject_system_prompt' ? `<div class="how" style="border-left-color:var(--live)">
      <b>Written into the recorded prompt.</b> The CLI records the system prompt on a
      conversation\u2019s first request and replays that record on every resume, so this
      needs no launch flag and cannot be dropped by the next launch.
      <div class="note">Transcript-class: stop the session, deploy, resume. A launch with
      <code>--system-prompt-snapshot off</code> would ignore the record.</div></div>` : ''}
    ${i.status === 'dropped' ? `<div class="how" style="border-left-color:var(--restart)">
      <b>Not in effect any more.</b> ${esc(i.note || '')}. The file is still written, so
      re-running the session with the flags below puts it back.
      <div class="note"><code>${esc(i.flags || '')}</code></div></div>` : ''}
    ${cfg && !['in_effect', 'dropped'].includes(i.status) ? `<div class="how" style="border-left-color:var(--restart)">
      <b>Written, not in effect.</b> A config change reaches a session only when the
      session is launched with its flag \u2014 and a plain resume ignores it, because
      the system prompt is recorded on a conversation's first request and replayed
      on every later one. These flags handle that:
      <div class="note"><code>${esc(i.flags || '')}</code></div>
      Use <b>Run session</b> below (it carries them), then <b>Verify</b>.</div>` : ''}
    ${i.status === 'in_effect' ? `<div class="how" style="border-left-color:var(--live)">
      <b>Confirmed in the session.</b> Found in its recorded system prompt.</div>` : ''}
    <dl class="kv">
      <dt>target</dt><dd>${cfg ? 'launch config (file + flag)' : 'transcript'}</dd>
      <dt>file</dt><dd>${esc(DATA.session.transcript)}</dd>
      ${pay.lines ? `<dt>lines</dt><dd>${pay.lines.join(', ')}</dd>` : ''}
      ${pay.line ? `<dt>line</dt><dd>${pay.line}</dd>` : ''}
      ${pay.ops ? `<dt>operations</dt><dd>${opsSummary(pay.ops)}</dd>` : ''}
      ${i.note ? `<dt>result</dt><dd>${esc(i.note)}</dd>` : ''}
    </dl>
    <div class="row">
      <button class="pri" onclick="applyDeploy('${i.label}')" ${
        ['deployed', 'in_effect'].includes(i.status) ? 'disabled' : ''}>${
        cfg ? 'Arm' : 'Deploy'}</button>
      ${cfg && i.status !== 'staged' ? `<button onclick="verifyDeploy('${i.label}')">Verify</button>` : ''}
      ${cfg ? `<button onclick="convertDeploy('${i.label}')"
        title="Write it into the recorded prompt instead, so it survives a relaunch">Convert to injection</button>` : ''}
      ${i.snapshot ? `<button class="warn" onclick="rollbackDeploy('${i.label}')">Roll back</button>` : ''}
      <button onclick="runSession()">Run session</button>
      <button onclick="dropDeploy('${i.label}')">Remove</button>
    </div>
    <div class="k" style="margin-top:16px">content</div>
    ${pay.ops ? opsTable(pay.ops)
      : pay.text != null ? bigBody(pay.text)
      : `<pre>${esc(JSON.stringify(pay, null, 2))}</pre>`}`;
}
/* The flag mechanism is the weaker one: it has to be re-passed every launch and
   the next launch drops it. Same text, better lever. */
async function convertDeploy(label) {
  const r = await (await fetch('/api/deploy/convert', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) })).json();
  if (!r.ok) return say('Could not convert', esc(r.error || ''), 'warn');
  await loadDeploy();
  say('Converted to an injection', `<b>${esc(label)}</b> now writes its text into the ` +
    'transcript\u2019s recorded system prompt. It is staged, not applied \u2014 deploy it ' +
    'with the session stopped, and it will survive relaunches with no flags.');
}

/* Did it actually land? The only honest answer comes from the target session's
   own transcript, not from the fact that we wrote a file. */
async function verifyDeploy(label) {
  const r = await (await fetch('/api/deploy/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) })).json();
  await loadDeploy();
  if (!r.ok) return say('Could not verify', esc(r.error || ''), 'warn');
  say(r.landed ? 'In effect' : 'Not in effect yet', esc(r.note || ''),
      r.landed ? '' : 'warn');
}

/* A deployment document is an ordered list of three operations. The file is
   rebuilt rather than patched: editing a JSONL record changes its length, so
   every later byte offset moves — there is no in-place edit to make. */
function opsSummary(ops) {
  const c = { replace: 0, delete: 0, insert: 0 };
  (ops || []).forEach(o => { const k = (o.op || '').toLowerCase();
    if (k === 'delete') c.delete += ((o.lines || [o.line]) || []).filter(x => x != null).length;
    else if (c[k] != null) c[k]++; });
  return `${c.replace} replace · ${c.delete} delete · ${c.insert} insert`;
}
const OPCAP = 200000;          // one record shown in full up to this much

/* A record is one line of JSON text. Older rows stored an object here, and
   .slice() on an object threw -- taking the whole detail panel down with it,
   which is why a big deployment looked like it "did not render". */
function opJSON(o) {
  const v = o && o.json;
  if (v == null) return '';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

function opsTable(ops) {
  const list = ops || [];
  const total = list.reduce((a, o) => a + opJSON(o).length, 0);
  return `<div class="ops">
    <div class="opsum">${list.length} operation${list.length === 1 ? '' : 's'} ·
      ${num(total)} chars · click one to read the record</div>
    ${list.map(o => {
      const k = (o.op || '').toLowerCase();
      const j = opJSON(o);
      const tgt = k === 'delete' ? 'lines ' + ((o.lines || [o.line]) || []).filter(x => x != null).join(', ')
        : k === 'insert' ? 'after line ' + o.after_line : 'line ' + o.line;
      const col = k === 'delete' ? 'var(--restart)' : k === 'insert' ? 'var(--live)' : 'var(--config)';
      const cut = j.length > OPCAP;
      return `<details class="op">
        <summary><b style="color:${col}">${esc(k)}</b>
          <span class="tgt">${esc(tgt)}</span>
          <span class="pk">${j ? num(j.length) + ' chars' : '—'}</span>
          <span class="pv">${esc(j.slice(0, 110))}</span></summary>
        ${j ? `<pre class="oprec">${esc(cut ? j.slice(0, OPCAP) : j)}</pre>${
          cut ? `<div class="trunc">Showing the first ${num(OPCAP)} of ${num(j.length)} characters.</div>` : ''}`
          : '<div class="trunc">This operation carries no record.</div>'}
      </details>`;
    }).join('')}</div>`;
}

/* Markdown over a megabyte is a lot of regex for no gain: past a point, show it
   as text and say so. */
const MDCAP = 120000;
function bigBody(t) {
  const v = String(t == null ? '' : t);
  if (v.length <= MDCAP) return `<div class="body">${renderBody(v)}</div>`;
  return `<div class="trunc">${num(v.length)} characters — shown as plain text.</div>
    <pre class="oprec">${esc(v.slice(0, OPCAP))}</pre>${
    v.length > OPCAP ? `<div class="trunc">Showing the first ${num(OPCAP)}.</div>` : ''}`;
}
async function applyDeploy(label) {
  if (!await ask(`Deploy ${label}?`,
    'A snapshot is taken first and the result is validated. If validation fails the ' +
    'transcript is rolled back automatically.', { ok: 'Deploy', danger: true })) return;
  const r = await (await fetch('/api/deploy/apply', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) })).json();
  if (r.ok) {
    say('Deployed', esc(r.validation || r.note || '') +
      (r.command ? code(r.command) : ''), 'ok');
  } else {
    say('Refused', esc(r.error || ''), 'warn');
  }
  await loadDeploy();
}
async function rollbackDeploy(label) {
  if (!await ask(`Roll back ${label}?`,
    'The transcript is restored from the snapshot taken before this deployment.',
    { ok: 'Roll back', danger: true })) return;
  const r = await (await fetch('/api/deploy/rollback', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) })).json();
  say(r.ok ? 'Rolled back' : 'Roll back failed',
    r.ok ? 'The transcript was restored from its snapshot.' : esc(r.error || ''),
    r.ok ? 'ok' : 'warn');
  await loadDeploy();
}
async function dropDeploy(label) {
  if (!await ask(`Remove ${label}?`,
    'This only clears it from the list. A deployment that already ran is not undone — ' +
    'use Roll back for that.', { ok: 'Remove', danger: true })) return;
  await fetch('/api/deploy/drop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label }) });
  DSEL = null; await loadDeploy();
}
async function validateTranscript() {
  const r = await (await fetch('/api/deploy/validate', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key }) })).json();
  flash((r.ok ? 'Transcript valid — ' : 'Transcript INVALID — ') + r.note);
}
async function runSession() {
  const r = await (await fetch('/api/deploy/run', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key }) })).json();
  say(r.launched ? `Launched in ${r.launched}` : 'Run this yourself',
    'Same session id, so it resumes rather than forks.' + code(r.command),
    r.launched ? 'ok' : 'info');
}

/* ═══════════════ polling ═══════════════ */
/* ═══════════════ WORKSPACE FILES ═══════════════
   A .ctxproj holds the board, staged deployments and run history — everything
   you built, nothing the session owns. Opening one never replays a transcript. */
/* ═══════════════ STATUS BAR ═══════════════ */
function status() {
  const el2 = $('#status'); if (!el2 || !DATA) return;
  const q = pendingRuns(), st = (DEP.items || []).filter(i => i.status === 'staged').length;
  el2.innerHTML = `
    <span><b>${esc(WS ? WS.name : '—')}</b>${
      WS && WS.saved ? ` <i style="font-style:normal;color:var(--live)">saved ${ago(WS.saved)}</i>` : ''}</span>
    <span>${esc(DATA.session.adapter)} · ${esc(DATA.session.status)} · ${esc(DATA.session.cwd || '')}</span>
    <span><b>${BOARD.nodes.length}</b> nodes · <b>${(BOARD.edges || []).length}</b> edges</span>
    <span><b>${q}</b> queued</span>
    <span><b>${st}</b> staged</span>
    <span><b>${LOG.length}</b> MCP</span>
    <span>${STALE ? '<b style="color:var(--restart)">stale</b>'
      : 'read ' + (FRESH ? esc(ago(FRESH)) : '\u2014')}${AUTO ? ' \u00b7 auto' : ''}</span>
    <span class="slegend">
      <span><i style="background:var(--live)"></i>no restart</span>
      <span><i style="background:var(--restart)"></i>restart</span>
      <span><i style="background:var(--config)"></i>restart + flags</span>
    </span>`;
}


async function pollLog() {
  try {
    const d = await (await fetch('/api/log?n=120')).json();
    const grew = d.calls.length !== LOG.length;
    LOG = d.calls;
    if (grew && !EDIT && !DRAGGING) paintLabSide();
    return grew;
  } catch (e) { return false; }
}
async function tickPoll() {
  if (!DATA) return;
  const grew = await pollLog();
  await loadBoard();                       /* version-guarded; repaints only on change */
  await loadSaved();                       /* autosave happens server-side; show when */
  if (grew) { await loadDeploy(); await loadRuns(); }
  await pulse();
  status();
}
addEventListener('keydown', e => {
  if (e.key !== 'Escape' || MODAL_CLOSE) return;
  if (PAGE === 'explorer') { $('#ex-side').classList.contains('open') ? closeSide() : (SEL && overview()); }
  if (PAGE === 'labs') { if (WIRE) toggleWire(); else labSide(false); }
});
boot();

/* ═══════════════ MENU BAR ═══════════════ */
function menu(name) {
  const m = document.querySelector(`.menu[data-m="${name}"]`);
  const wasOpen = m.classList.contains('open');
  document.querySelectorAll('.menu').forEach(x => x.classList.remove('open'));
  if (!wasOpen) m.classList.add('open');
}
addEventListener('click', e => {
  if (!e.target.closest('.menu')) document.querySelectorAll('.menu').forEach(x => x.classList.remove('open'));
});
const closeMenus = () => document.querySelectorAll('.menu').forEach(x => x.classList.remove('open'));

async function mFile(a) {
  closeMenus();
  if (a === 'new' || a === 'open') {
    if (WS) {
      await fetch('/api/ctxs/save', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: WS.id }) });
      await fetch('/api/ctxs/close', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: '{}' });
      WS = null;
    }
    switchSession(); obMode(a === 'new' ? 'new' : 'open'); return;
  }
  if (a === 'auto') {
    AUTO = !AUTO;
    localStorage.setItem('ctx.auto', AUTO ? '1' : '0');
    paintRefresh();
    flash(AUTO ? 'Auto-refresh on \u2014 the view follows the session.'
               : 'Auto-refresh off \u2014 refresh by hand (\u2318R).');
    if (AUTO) refreshData(true);
    return;
  }
  if (!WS) { say('No ctx session open', 'Open or create one from File first.', 'warn'); return; }
  if (a === 'save') {
    const r = await (await fetch('/api/ctxs/save', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: WS.id }) })).json();
    if (r.ok) WS.saved = r.saved || Date.now() / 1000;
    flash(r.ok ? `Saved “${WS.name}”` : (r.error || 'save failed'));
  }
  if (a === 'rename') {
    const n = await askText('Rename ctx session', WS.name,
      { ok: 'Rename', placeholder: 'Session name' });
    if (!n || !n.trim()) return;
    const r = await (await fetch('/api/ctxs/rename', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: WS.id, name: n }) })).json();
    if (r.ok) { WS.name = r.name; $('#mws').textContent = r.name; status(); }
  }
  if (a === 'close') {
    await fetch('/api/ctxs/save', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: WS.id }) });
    await fetch('/api/ctxs/close', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    WS = null; switchSession();
  }
}
function mEdit(a) {
  closeMenus();
  if (PAGE !== 'labs') setPage('labs');
  if (a === 'connect') return toggleWire();
  if (a === 'delete') return LNODE ? delNode(LNODE) : flash('No node selected.');
  newNode(a);
}
function mView(a) {
  closeMenus();
  if (a === 'fit') return fitLab();
  if (a === 'reset') { railPx = 0; sidePx = 0; labPx = 0;
    localStorage.removeItem('ctx.rail'); localStorage.removeItem('ctx.side');
    localStorage.removeItem('ctx.lab'); layout(); layoutLab(); kick(); return flash('Panel widths reset.'); }
  setPage(a);
}
function mRun(a) {
  closeMenus();
  if (a === 'cell') return runCell();
  if (a === 'pipeline') { setPage('labs'); return runPipeline(); }
  if (a === 'validate') { setPage('deploy'); return validateTranscript(); }
  if (a === 'session') { setPage('deploy'); return runSession(); }
}
function mHelp(a) {
  closeMenus();
  if (a === 'why') return say('What ctx is for', HELP_WHY());
  if (a === 'flow') return say('A working session, end to end', HELP_FLOW());
  if (a === 'voice') return say('Working by voice', HELP_VOICE());
  if (a === 'mcp') return say('Connect an agent (MCP)', HELP_MCP());
  return say('ctx', HELP_ABOUT());
}

/* The docs live here rather than in a README nobody opens mid-session. Short on
   purpose: what it is for, how to say things to it, and what to ask an agent. */
const HELP_WHY = () => `
  <b>The problem.</b> A long session drifts. The model starts answering from
  stale context — something you corrected an hour ago, a tool result that is no
  longer true, a system prompt written for a different task. You cannot see any
  of it, so you cannot fix it. You start a new session and lose everything.
  <br><br>
  <b>What ctx does.</b> It reads the session's own store and shows you the
  context as it actually is, then lets you change it and put the session back.
  Three pages, in the order you use them:
  <br><br>
  <b>1 Explorer</b> — what is in the window right now. Only what is really sent
  (history discarded by a compaction is cut) and only what you can actually
  change. Every bucket says what it costs and what the lever is.
  <br>
  <b>2 Labs</b> — a canvas to work it out. <i>Extraction</i> gathers the records
  that mention something. <i>Analysis</i> is a notebook cell over what they
  gathered — pandas, charts, no limits. <i>Review</i> holds the conclusion still
  while it is audited.
  <br>
  <b>3 Deployment</b> — the only door to the transcript. Stop the session,
  rewrite, validate, resume under the <b>same id</b>. Never a fork.
  <br><br>
  <b>The rule.</b> Nothing reaches a transcript except through Deployment, and
  applying is always a human click. Every apply takes a snapshot first and rolls
  back if the result does not validate.`;

const HELP_FLOW = () => `
  <b>Two sessions, not one.</b> The <i>worker</i> is where you do the actual
  research or build. The <i>meta-agent</i> is a second session with the ctx MCP
  server attached, pointed at the worker. Keep them separate — an agent that
  analyses the session it lives in adds its own tool schemas to the context it
  is measuring.
  <br><br>
  <b>1 Drift.</b> The worker runs long. It now holds corrections you have
  already made, tool output that is no longer true, examples you have outgrown.
  You know some of it is wrong; you cannot see which.
  <br><br>
  <b>2 Look.</b> Open ctx on that session. <b>Explorer</b> shows the window as
  it actually is — system instructions, your turns, its turns, tool calls and
  what came back — sized, labelled, and cut down to what is really being sent.
  <br><br>
  <b>3 Gather and judge.</b> In <b>Labs</b>, an <i>extraction</i> node pulls
  every record that mentions a topic, a keyword, a specific value. An
  <i>analysis</i> node weighs it: how much of the window is this costing, is the
  value right, is it a hallucination, is it still the direction you want.
  <br><br>
  <b>4 Propose.</b> Send the conclusion to a <i>review</i> node. Have the agent
  audit it. Then stage a deployment — records dropped, text corrected, examples
  replaced, an instruction injected.
  <br><br>
  <b>5 Inject and restart.</b> Stop the worker, deploy, start it again on the
  same id. The change is in the recorded context from its next request onward.
  <br><br>
  <b>What you are changing:</b> facts, examples, tone, which tools it believes
  it has, what concepts it is working from. The brain of the harness, on
  purpose, instead of starting over and losing the session.
  <br><br>
  <b>What to say to the meta-agent:</b>
  <br>&ldquo;What ctx session is active, and what is in its context?&rdquo;
  <br>&ldquo;Find everything about <i>&lt;topic&gt;</i> in the context memory
  and tell me how much it costs.&rdquo;
  <br>&ldquo;The values in <i>&lt;topic&gt;</i> are wrong — change X to Y and
  stage the injection.&rdquo;
  <br>&ldquo;Remove those examples, use these instead, and stage it.&rdquo;
  <br>&ldquo;Audit that deployment and tell me what it breaks.&rdquo;
  <br><br>
  It stages; you deploy. Every apply snapshots first, validates tool-call
  pairing, and rolls back if the result does not hold.`;

const HELP_VOICE = () => `
  ctx is built to be <b>spoken to</b>. If you dictate into a CLI or an agent,
  the labels are the interface:
  <br><br>
  <b>Say the label, not the thing.</b> Every item has a short address — <code
  class="inl">B</code> is a bucket, <code class="inl">B1</code> an item in it,
  <code class="inl">N4</code> a canvas node, <code class="inl">D13</code> a
  deployment. "Drop B1 and K85" is unambiguous dictated; "drop that tool result
  about the grep" is not.
  <br><br>
  <b>Why not words like delta or echo.</b> A dictated word blurs into the
  sentence around it and a transcript cannot tell the label from the content.
  <code class="inl">B1</code> always reads as an identifier, whatever sentence
  it lands in.
  <br><br>
  <b>Labels never move.</b> They are assigned on first sight and persisted, so
  <code class="inl">B1</code> means the same item tomorrow. Only the order is
  stored — the scheme can change without renumbering.
  <br><br>
  <b>Dictation drops words.</b> Anything destructive asks first, and an agent is
  told to act only on labels you named — no wildcards, no "clear the board". If
  a transcription garbles a label, it fails loudly rather than deleting
  something adjacent.`;

const HELP_MCP = () => `
  <b>Register it once</b>, then start a session:
  ${code('claude mcp add ctx -s user -- python3 /absolute/path/to/mcp_server.py')}
  <b>Point it at a different session</b> from the one you are analysing —
  running it inside the session it measures adds its own tool schemas to that
  context.
  <br><br>
  <b>Why bother.</b> Reading a 600k-token window is not something to do by eye.
  The agent searches it, explains what is heavy, and drafts the change — while
  you keep the judgement and the click.
  <br><br>
  <b>What to say out loud:</b>
  <br>&ldquo;What is in this session's context?&rdquo; → <code
  class="inl">current_session</code>
  <br>&ldquo;What is bucket B and how do I change it?&rdquo; → <code
  class="inl">get_bucket</code>
  <br>&ldquo;Read B1 to me&rdquo; → <code class="inl">get_item</code>
  <br>&ldquo;Put an extraction node on the board for everything mentioning
  compaction&rdquo; → <code class="inl">board_add</code> + <code
  class="inl">extract_run</code>
  <br>&ldquo;Summarise what N4 gathered&rdquo; → <code
  class="inl">extract_summary</code>
  <br>&ldquo;Audit N9 and tell me if it is safe&rdquo; → <code
  class="inl">review_read</code> + <code class="inl">review_audit</code>
  <br>&ldquo;Stage a change that drops those tool results&rdquo; → <code
  class="inl">deploy_stage_ops</code>
  <br>&ldquo;Delete N5 and N6&rdquo; → <code class="inl">board_remove</code>
  <br>&ldquo;Remove deployment D20&rdquo; → <code class="inl">deploy_remove</code>
  <br><br>
  <b>What it cannot do.</b> Stage, never apply. An agent can propose, gather,
  analyse and audit; deploying is yours. Every call is logged to the
  <b>Activity</b> tab in Labs.`;

const HELP_ABOUT = () => `
  Context engineering for AI coding sessions. Python 3 stdlib only — no install,
  no dependencies, macOS, Linux and Windows.
  <br><br>
  <b>Explorer</b> — see what is actually in the context<br>
  <b>Labs</b> — extraction, analysis, review<br>
  <b>Deployment</b> — the only door to the transcript
  <br><br>
  Adapters for Claude Code and Codex; both are a home-directory store plus an
  append-only JSONL transcript. Shares are measured, the total comes from the
  provider's own count, and the drift between them is shown rather than hidden.
  <br><br>
  <b>Help ▸ What ctx is for</b> is the one-page version.`;

function flash(msg) {
  const el2 = $('#status'); if (!el2) return;
  const old = el2.innerHTML;
  el2.innerHTML = `<span style="color:var(--live)"><b>${esc(msg)}</b></span>`;
  setTimeout(() => { status(); }, 2200);
}

/* ═══════════════ CODE CELLS ═══════════════ */
async function saveCell() {
  const n = BOARD.nodes.find(x => x.label === LNODE); if (!n) return;
  const src = $('#cellsrc') ? $('#cellsrc').value : n.body;
  await fetch('/api/board/update', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label: n.label, body: src }) });
  n.body = src; flash('Cell source saved.');
}
async function runCell() {
  const n = BOARD.nodes.find(x => x.label === LNODE);
  if (!n || n.kind !== 'code') { flash('Select a code cell first.'); return; }
  const src = $('#cellsrc') ? $('#cellsrc').value : (n.body || '');
  const btn = $('#runcell'); if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
  const r = await (await fetch('/api/cell/run', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: DATA.session.key, label: n.label, code: src }) })).json();
  n.body = src; n.result = r;
  await loadBoard(); paintLabSide(); paintLab();
}
function cellOutput(r) {
  if (!r) return '<div class="cellmeta">Not run yet.</div>';
  const bad = !r.ok;
  return `<div class="cellout ${bad ? 'err' : ''}">
    ${r.stdout ? `<pre>${esc(r.stdout)}</pre>` : ''}
    ${r.stderr ? `<pre style="color:#d8b980">${esc(r.stderr)}</pre>` : ''}
    ${r.error ? `<pre style="color:#e0913f">${esc(r.error)}</pre>` : ''}
    ${(r.images || []).map(src => `<img src="${src}" alt="figure">`).join('')}
    ${!r.stdout && !r.error && !(r.images || []).length ? '<div class="cellmeta">No output.</div>' : ''}
    <div class="cellmeta">${r.ok ? 'ok' : 'failed'} · ${r.ms || 0} ms${
      r.at ? ' · ' + ago(r.at) : ''}</div></div>`;
}

/* ═══════════════ LABS PANEL RESIZE ═══════════════ */
let labPx = +(localStorage.getItem('ctx.lab') || 0), LABW = 0;
const MINCANVAS = 360;              /* the panel may never eat the whole board */

/* The canvas column just changed width. Hold the viewport centre still, so
   widening the panel narrows the view from both sides instead of sweeping the
   nodes on the right out of sight. */
function keepCentre() {
  const w = $('#lab').clientWidth;
  if (LABW && w && w !== LABW) { vx += (w - LABW) / 2; queueView(); }
  LABW = w;
}

/* Open or close the detail panel. Flex siblings, so the canvas takes the space
   back on close -- there is no layer to slide out from under. */
function labSide(open) {
  $('#lab-side').classList.toggle('open', open);
  layoutLab();
}

function layoutLab() {
  const pg = $('#pg-labs'); if (!pg) return;
  const MW = pg.clientWidth;
  const w = clamp(labPx || Math.min(420, MW * 0.34), 260, Math.max(260, MW - MINCANVAS));
  $('#lab-side').style.width = w + 'px';
  $('#gripL').classList.toggle('on', $('#lab-side').classList.contains('open'));
  keepCentre();
}
(function () {
  const g = $('#gripL'); if (!g) return;
  g.addEventListener('mousedown', e => {
    e.preventDefault(); document.body.classList.add('dragging'); g.classList.add('drag');
    const mv = ev => { const r = $('#pg-labs').getBoundingClientRect();
      labPx = clamp(r.right - ev.clientX, 260, Math.max(260, r.width - MINCANVAS));
      layoutLab(); drawEdges(); };
    const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up);
      document.body.classList.remove('dragging'); g.classList.remove('drag');
      localStorage.setItem('ctx.lab', labPx); };
    addEventListener('mousemove', mv); addEventListener('mouseup', up);
  });
  g.addEventListener('dblclick', () => { labPx = 0; localStorage.removeItem('ctx.lab'); layoutLab(); });
})();
addEventListener('resize', () => { if (PAGE === 'labs') layoutLab(); });

/* ═══════════════ KEYBOARD ═══════════════ */
addEventListener('keydown', e => {
  if (MODAL_CLOSE) return;                 /* a dialog owns the keyboard */
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''));
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); return mFile('save'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); return mFile('new'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o') { e.preventDefault(); return mFile('open'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); return mEdit('code'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'r') { e.preventDefault(); return refreshData(false); }
  if (e.shiftKey && e.key === 'Enter' && PAGE === 'labs') { e.preventDefault(); return runCell(); }
  if (typing) return;
  if (e.key === '1') setPage('explorer');
  if (e.key === '2') setPage('labs');
  if (e.key === '3') setPage('deploy');
  if (e.key === 'f' && PAGE === 'labs') fitLab();
  if (e.key === 'c' && PAGE === 'labs') toggleWire();
});

/* ═══════════════ DIALOGS ═══════════════
   Replaces alert/confirm/prompt. Same three shapes, but in the app's own
   surface: Enter confirms, Escape cancels, focus lands where you'd expect, and
   a destructive action is coloured as one. Every call returns a promise. */
let MODAL_CLOSE = null;

function modal({ title, body = '', icon = 'info', input = null, actions, wide = false }) {
  if (MODAL_CLOSE) MODAL_CLOSE();          /* only one dialog at a time */
  return new Promise(resolve => {
    const box = $('#modal');
    $('#m-icon').className = 'micon ' + icon;
    $('#m-icon').textContent = icon === 'warn' ? '!' : icon === 'ok' ? '✓' : 'i';
    $('#m-title').textContent = title;
    $('#m-body').innerHTML = body;
    $('#m-body').style.display = body ? '' : 'none';
    const field = $('#m-field'), inp = $('#m-input');
    field.classList.toggle('on', input !== null);
    if (input !== null) { inp.value = input.value || ''; inp.placeholder = input.placeholder || ''; }
    $('#m-act').innerHTML = actions
      .map((a, i) => `<button data-i="${i}" class="${a.cls || ''}">${esc(a.label)}</button>`).join('');

    const done = value => {
      box.classList.remove('on');
      removeEventListener('keydown', onKey, true);
      MODAL_CLOSE = null;
      resolve(value);
    };
    MODAL_CLOSE = () => done(null);
    $('#m-act').querySelectorAll('button').forEach(b => {
      b.onclick = () => {
        const a = actions[+b.dataset.i];
        done(a.value === undefined ? (input !== null ? inp.value : true) : a.value);
      };
    });
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      if (e.key === 'Enter' && !e.shiftKey) {
        const primary = actions.findIndex(a => a.primary);
        if (primary >= 0) {
          e.preventDefault(); e.stopPropagation();
          const a = actions[primary];
          done(a.value === undefined ? (input !== null ? inp.value : true) : a.value);
        }
      }
    }
    addEventListener('keydown', onKey, true);
    box.classList.add('on');
    box.onmousedown = e => { if (e.target === box) done(null); };
    setTimeout(() => { (input !== null ? inp : $('#m-act button:last-child')).focus();
      if (input !== null) inp.select(); }, 0);
  });
}
const say = (title, body = '', icon = 'info') =>
  modal({ title, body, icon, actions: [{ label: 'OK', primary: true, value: true }] });
const ask = (title, body = '', { ok = 'Confirm', danger = false } = {}) =>
  modal({ title, body, icon: danger ? 'warn' : 'info', actions: [
    { label: 'Cancel', value: null },
    { label: ok, primary: true, value: true, cls: danger ? 'danger' : 'pri' }] });
const askText = (title, value = '', { body = '', ok = 'Save', placeholder = '' } = {}) =>
  modal({ title, body, input: { value, placeholder }, actions: [
    { label: 'Cancel', value: null },
    { label: ok, primary: true, cls: 'pri' }] });
const code = t => `<span class="cmd">${esc(t)}</span>`;
