"""HTML report generation for PaperTrail.

Produces a single self-contained page: the ranked papers are embedded as JSON
and rendered client-side, so the report can be searched, filtered and sorted
in the browser, and papers can be starred / marked as read (kept in the
browser's localStorage). Works from GitHub Pages or opened straight from disk.

Every run is archived as reports/YYYY-MM-DD.html (its data embedded, so the
archive is its own source of truth). After each run all pages are re-rendered
from that embedded data, so every week links to every other week and older
weeks pick up template improvements. papers.html is always the latest week.
"""

import json
import re
from datetime import date
from pathlib import Path

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PaperTrail — __DATE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #F7F7F5;
    --surface:   #FFFFFF;
    --text:      #1A1A2E;
    --body:      #374151;
    --muted:     #6B7280;
    --accent:    #2D6A4F;
    --accent-bg: #E3F0E9;
    --border:    #E5E7EB;
    --high:      #2D6A4F;
    --mid:       #D97706;
    --low:       #DC2626;
    --badge-bg:  #EEF2FF;
    --badge-fg:  #4338CA;
    --pre-bg:    #FEF3C7;
    --pre-fg:    #92400E;
    --mark:      #FDE68A;
    --star:      #D97706;
    --shadow:    rgba(0,0,0,0.07);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:        #14161B;
      --surface:   #1C1F26;
      --text:      #ECEDEF;
      --body:      #C9CCD3;
      --muted:     #9097A3;
      --accent:    #6FC69A;
      --accent-bg: #1F3A2D;
      --border:    #2D313A;
      --high:      #6FC69A;
      --mid:       #F0A93B;
      --low:       #F07171;
      --badge-bg:  #262B45;
      --badge-fg:  #A5B4FC;
      --pre-bg:    #3A2F14;
      --pre-fg:    #F5C969;
      --mark:      #5C4A12;
      --star:      #F0A93B;
      --shadow:    rgba(0,0,0,0.35);
    }
  }
  :root[data-theme="dark"] {
    --bg:        #14161B;
    --surface:   #1C1F26;
    --text:      #ECEDEF;
    --body:      #C9CCD3;
    --muted:     #9097A3;
    --accent:    #6FC69A;
    --accent-bg: #1F3A2D;
    --border:    #2D313A;
    --high:      #6FC69A;
    --mid:       #F0A93B;
    --low:       #F07171;
    --badge-bg:  #262B45;
    --badge-fg:  #A5B4FC;
    --pre-bg:    #3A2F14;
    --pre-fg:    #F5C969;
    --mark:      #5C4A12;
    --star:      #F0A93B;
    --shadow:    rgba(0,0,0,0.35);
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    font-size: 15px;
    line-height: 1.6;
    padding: 2rem 1rem 4rem;
  }

  .page { max-width: 780px; margin: 0 auto; }
  button, input, select { font: inherit; color: inherit; }

  /* ── Header ── */
  header { margin-bottom: 1.25rem; }
  .logo {
    font-family: 'DM Serif Display', serif;
    font-size: 2rem;
    color: var(--accent);
    letter-spacing: -0.02em;
  }
  .logo span { color: var(--text); }
  .head-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.6rem 1rem; }
  .weeks { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; }
  .weeks select {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.3rem 0.5rem; font-size: 0.82rem; max-width: 62vw;
  }
  .wk-btn {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--surface);
    color: var(--text); text-decoration: none; font-size: 1.1rem; line-height: 1;
  }
  .wk-btn:hover { border-color: var(--accent); color: var(--accent); }
  .wk-btn[aria-disabled="true"] { opacity: 0.35; pointer-events: none; }
  .weeks .linkish { text-decoration: none; white-space: nowrap; }
  .imported-note { color: var(--muted); font-size: 0.78rem; margin-top: 0.25rem; }
  .meta { margin-top: 0.4rem; color: var(--muted); font-size: 0.85rem; }

  /* ── Controls ── */
  .controls {
    position: sticky; top: 0; z-index: 5;
    background: var(--bg);
    padding: 0.75rem 0 0.9rem;
    margin-bottom: 1rem;
    border-bottom: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 0.6rem;
  }
  .search {
    width: 100%;
    padding: 0.55rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    font-size: 0.9rem;
  }
  .search-row { display: flex; gap: 0.5rem; align-items: stretch; }
  .search-row .search { flex: 1; min-width: 0; }
  .search-row .seg button { padding: 0 0.75rem; white-space: nowrap; }
  .week-tag {
    font-size: 0.72rem; font-weight: 500; color: var(--accent);
    text-decoration: none; border-bottom: 1px dotted currentColor;
  }
  .week-tag:hover { border-bottom-style: solid; }
  .more-btn {
    align-self: center; margin-top: 0.25rem;
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 0.45rem 1.1rem; font-size: 0.85rem; cursor: pointer; color: var(--accent);
  }
  .more-btn:hover { border-color: var(--accent); }
  .search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .row { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; font-size: 0.82rem; color: var(--muted); }
  .row label { display: inline-flex; align-items: center; gap: 0.4rem; }
  .row select {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.2rem 0.4rem; font-size: 0.82rem;
  }
  .row input[type=range] { accent-color: var(--accent); width: 110px; }
  .row input[type=checkbox] { accent-color: var(--accent); }
  .min-val { font-variant-numeric: tabular-nums; color: var(--text); font-weight: 500; min-width: 2.2em; }

  .chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
  .chip {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    font-size: 0.74rem; font-weight: 500;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
    cursor: pointer;
  }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  .chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg); }
  .chip .n { opacity: 0.7; margin-left: 0.25rem; font-variant-numeric: tabular-nums; }

  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .seg button {
    background: var(--surface); border: none; padding: 0.2rem 0.65rem;
    font-size: 0.8rem; cursor: pointer; color: var(--muted);
  }
  .seg button + button { border-left: 1px solid var(--border); }
  .seg button[aria-pressed="true"] { background: var(--accent-bg); color: var(--accent); font-weight: 600; }

  .status { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; font-size: 0.8rem; color: var(--muted); }
  .linkish { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 0.8rem; font-weight: 500; }
  .linkish:hover { text-decoration: underline; }

  /* ── Cards ── */
  .papers { display: flex; flex-direction: column; gap: 1rem; }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 4px solid var(--score-color, var(--border));
    border-radius: 0 8px 8px 0;
    padding: 1.25rem 1.5rem 1rem;
    transition: box-shadow 0.15s ease, opacity 0.15s ease;
  }
  .card:hover { box-shadow: 0 4px 16px var(--shadow); }
  .card.is-read { opacity: 0.55; }
  .card.is-read:hover { opacity: 1; }

  .card-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 0.5rem;
  }

  .title {
    font-size: 1rem;
    font-weight: 600;
    line-height: 1.4;
    color: var(--text);
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  .title:hover { color: var(--accent); }

  /* Score pill + bar */
  .score-block { flex-shrink: 0; text-align: right; min-width: 48px; }
  .score-pill {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--score-color);
    font-variant-numeric: tabular-nums;
  }
  .score-bar-track {
    width: 48px; height: 3px;
    background: var(--border);
    border-radius: 2px;
    margin-top: 4px;
  }
  .score-bar-fill {
    height: 100%;
    border-radius: 2px;
    background: var(--score-color);
  }

  .card-meta {
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 0.6rem;
    display: flex; flex-wrap: wrap; gap: 0.3rem 0.75rem;
    align-items: center;
  }
  .badge {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.1rem 0.45rem;
    border-radius: 4px;
    background: var(--badge-bg);
    color: var(--badge-fg);
  }
  .badge.preprint { background: var(--pre-bg); color: var(--pre-fg); }

  .abstract {
    font-size: 0.875rem;
    color: var(--body);
    line-height: 1.65;
    overflow-wrap: anywhere;
  }
  mark { background: var(--mark); color: inherit; border-radius: 2px; padding: 0 1px; }
  .toggle-btn {
    background: none; border: none; cursor: pointer;
    color: var(--accent); font-size: 0.8rem; font-weight: 500;
    padding: 0; margin-top: 0.3rem; display: block;
  }
  .toggle-btn:hover { text-decoration: underline; }

  .card-foot {
    margin-top: 0.7rem;
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 0.9rem;
    font-size: 0.78rem;
  }
  .matched { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-right: auto; }
  .matched .kw {
    background: var(--accent-bg); color: var(--accent);
    font-size: 0.7rem; font-weight: 500;
    padding: 0.05rem 0.5rem; border-radius: 999px;
    border: none; cursor: pointer;
  }
  .act {
    background: none; border: none; cursor: pointer;
    color: var(--muted); font-size: 0.78rem; padding: 0.1rem 0;
    display: inline-flex; align-items: center; gap: 0.25rem;
  }
  .act:hover { color: var(--accent); }
  .act.on { color: var(--star); }
  .act.read-on { color: var(--accent); }
  .act svg { width: 14px; height: 14px; }
  .doi-link { color: var(--muted); text-decoration: none; }
  .doi-link:hover { color: var(--accent); }

  .empty {
    text-align: center; color: var(--muted); font-size: 0.9rem;
    padding: 3rem 1rem; border: 1px dashed var(--border); border-radius: 8px;
  }

  /* ── Footer ── */
  footer {
    margin-top: 3rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
    text-align: center;
    font-size: 0.8rem;
    color: var(--muted);
  }

  @media (max-width: 560px) {
    .search-row { flex-direction: column; }
    .search-row .seg button { flex: 1; padding: 0.3rem 0.5rem; }
    body { padding: 1.25rem 1rem 3rem; }
    .card { padding: 1rem 1rem 0.85rem; }
    .controls { position: static; }
  }
</style>
</head>
<body>
<div class="page">

<header>
  <div class="head-row">
    <div class="logo">Paper<span>Trail</span></div>
    <nav class="weeks" id="weeks" aria-label="Weekly reports" hidden>
      <a class="wk-btn" id="prev" title="Previous week  ( [ )" aria-label="Previous week">‹</a>
      <select id="week" aria-label="Choose week"></select>
      <a class="wk-btn" id="next" title="Next week  ( ] )" aria-label="Next week">›</a>
      <a class="linkish" id="latest">Latest →</a>
    </nav>
  </div>
  <div class="meta" id="meta"></div>
</header>

<div class="controls">
  <div class="search-row">
    <input class="search" id="q" type="search" placeholder="Search papers, authors, journals…" autocomplete="off">
    <div class="seg" id="scope" role="group" aria-label="Search scope">
      <button data-scope="week" aria-pressed="true">This week</button>
      <button data-scope="all">All weeks</button>
    </div>
  </div>
  <div class="row">
    <div class="seg" id="view" role="group" aria-label="View">
      <button data-view="all" aria-pressed="true">All</button>
      <button data-view="unread">Unread</button>
      <button data-view="starred">Starred <span id="star-n"></span></button>
    </div>
    <label>Sort
      <select id="sort">
        <option value="score">Relevance</option>
        <option value="date">Newest</option>
        <option value="if">Impact factor</option>
      </select>
    </label>
    <label>Min score
      <input id="min" type="range" min="0" max="1" step="0.01">
      <span class="min-val" id="min-val"></span>
    </label>
  </div>
  <div class="chips" id="sources" aria-label="Sources"></div>
  <div class="chips" id="keywords" aria-label="Keywords"></div>
  <div class="status">
    <span id="count" aria-live="polite"></span>
    <span>
      <button class="linkish" id="export" hidden>Export starred</button>
      <button class="linkish" id="reset" hidden>Clear filters</button>
    </span>
  </div>
</div>

<div class="papers" id="papers"></div>

<footer id="footer"></footer>

</div>

<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const papers = DATA.papers;
  const SHORT = 280;
  // Slider floor: the pipeline threshold, or lower if older data scored below it
  const FLOOR = Math.floor(Math.min(DATA.threshold, ...papers.map(p => p.score)) * 100) / 100;

  // ── Persistent per-browser state (stars / read), keyed by paper URL ──
  const store = {
    get(k, fallback) {
      try { const v = localStorage.getItem('papertrail:' + k); return v ? JSON.parse(v) : fallback; }
      catch { return fallback; }
    },
    set(k, v) {
      try { localStorage.setItem('papertrail:' + k, JSON.stringify(v)); } catch {}
    },
  };
  const starred = new Map(Object.entries(store.get('starred', {})));  // id -> paper snapshot
  const read    = new Set(store.get('read', []));
  const saveStars = () => store.set('starred', Object.fromEntries(starred));
  const saveRead  = () => store.set('read', [...read]);

  const prefs = store.get('prefs', {});
  const state = {
    q: '',
    view: 'all',
    sort: prefs.sort || 'score',
    min: FLOOR,
    sources: new Set(),
    keyword: null,
    expanded: new Set(),
    scope: 'week',        // 'week' = this report, 'all' = every archived week
    limit: 100,           // cards rendered before "Show more"
  };
  const PAGE = 100;

  // ── All-weeks index: loaded on demand from reports/search-index.js ──
  let ALL = null, loadingAll = false;
  function loadAll() {
    if (ALL || loadingAll) return;
    loadingAll = true;
    const s = document.createElement('script');
    s.src = (DATA.prefix || '') + 'search-index.js';
    s.onload = () => { ALL = window.PAPERTRAIL_INDEX || []; loadingAll = false; render(); };
    s.onerror = () => {
      loadingAll = false; state.scope = 'week'; render();
      $('count').textContent = 'Could not load the all-weeks index (reports/search-index.js).';
    };
    document.head.appendChild(s);
  }
  const pool = () => state.scope === 'all' && ALL ? ALL : papers;

  // ── Helpers ──
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const scoreVar = s => s >= 0.5 ? 'var(--high)' : s >= 0.35 ? 'var(--mid)' : 'var(--low)';
  const isPreprint = p => !p.journal || p.journal === 'preprint';
  const url = p => (p.id && !p.id.startsWith('http')) ? 'https://doi.org/' + p.id : p.id;
  // Dates come as "2026-09-02", "2026-Sep-02" (PubMed) or just "2026-Sep"
  const MONTHS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
  const dateKey = d => {
    const m = /^(\d{4})(?:-(\w+))?(?:-(\d+))?/.exec(d || '');
    if (!m) return 0;
    const mon = m[2] ? (/^\d+$/.test(m[2]) ? +m[2] - 1 : MONTHS.indexOf(m[2].slice(0, 3).toLowerCase())) : 0;
    return Date.UTC(+m[1], Math.max(mon, 0), m[3] ? +m[3] : 1);
  };

  // Keyword highlighting: whole-token matches, longest first
  const kwRe = DATA.keywords.length
    ? new RegExp('\\b(' + [...DATA.keywords].sort((a, b) => b.length - a.length).map(reEsc).join('|') + ')\\b', 'gi')
    : null;
  function highlight(text) {
    const parts = [];
    const res = [kwRe];
    const q = state.q.trim();
    if (q.length >= 2) res.push(new RegExp('(' + q.split(/\s+/).map(reEsc).join('|') + ')', 'gi'));
    // Collect ranges from all regexes on raw text, then merge and escape
    const ranges = [];
    for (const re of res) {
      if (!re) continue;
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text))) { if (!m[0]) { re.lastIndex++; continue; } ranges.push([m.index, m.index + m[0].length]); }
    }
    if (!ranges.length) return esc(text);
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [ranges[0].slice()];
    for (const r of ranges.slice(1)) {
      const last = merged[merged.length - 1];
      if (r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push(r.slice());
    }
    let i = 0;
    for (const [a, b] of merged) { parts.push(esc(text.slice(i, a)), '<mark>', esc(text.slice(a, b)), '</mark>'); i = b; }
    parts.push(esc(text.slice(i)));
    return parts.join('');
  }

  const hayCache = new WeakMap();
  const hay = p => {
    let h = hayCache.get(p);
    if (h === undefined) {
      h = [p.title, p.abstract, p.authors, p.journal, p.source].join(' ').toLowerCase();
      hayCache.set(p, h);
    }
    return h;
  };

  // ── Filtering ──
  function filtered() {
    const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    let list = state.view === 'starred'
      ? [...starred.values()]
      : pool();
    list = list.filter(p => {
      if (state.view !== 'starred' && p.score < state.min) return false;
      if (state.view === 'unread' && read.has(p.id)) return false;
      if (state.sources.size && !state.sources.has(p.source)) return false;
      if (state.keyword && !(p.matched || []).includes(state.keyword)) return false;
      if (terms.length) {
        const h = hay(p);
        if (!terms.every(t => h.includes(t))) return false;
      }
      return true;
    });
    const by = {
      score: (a, b) => b.score - a.score,
      date:  (a, b) => dateKey(b.date) - dateKey(a.date) || b.score - a.score,
      if:    (a, b) => (b.if ?? -1) - (a.if ?? -1) || b.score - a.score,
    }[state.sort];
    return [...list].sort(by);
  }

  // ── Rendering ──
  const ICON_STAR = '<svg viewBox="0 0 24 24" fill="FILL" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

  // Link to the report(s) a paper appeared in, when not the one being viewed
  const LABELS = Object.fromEntries((DATA.reports || []).map(r => [r.date, r.label]));
  function weekTag(p) {
    const weeks = (p.weeks || []).filter(w => w !== DATA.date || state.scope === 'all');
    if (!weeks.length || (state.scope !== 'all' && state.view !== 'starred')) return '';
    const [w, ...rest] = weeks;
    const label = w === DATA.date ? 'This week' : 'Week of ' + (LABELS[w] || w);
    return `<a class="week-tag" href="${esc((DATA.prefix || '') + w + '.html')}" title="${esc(weeks.map(x => LABELS[x] || x).join(', '))}">${esc(label)}${rest.length ? ` +${rest.length}` : ''}</a>`;
  }

  function card(p) {
    const s = p.score ?? 0;
    const abs = p.abstract || '';
    const long = abs.length > SHORT;
    const open = state.expanded.has(p.id);
    const shown = long && !open ? abs.slice(0, SHORT) + '…' : abs;
    const ifStr = p.if != null ? ` · IF ${Number(p.if).toFixed(1)}` : '';
    const authors = p.authors || '';
    const isStar = starred.has(p.id), isRead = read.has(p.id);
    const u = url(p);
    return `
      <article class="card${isRead ? ' is-read' : ''}" style="--score-color:${scoreVar(s)}" data-id="${esc(p.id)}">
        <div class="card-top">
          <a class="title" href="${esc(u)}" target="_blank" rel="noopener">${highlight(p.title || '')}</a>
          <div class="score-block" title="semantic ${p.score_semantic ?? '–'} · lexical ${p.score_lexical ?? '–'}">
            <div class="score-pill">${s.toFixed(2)}</div>
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(s * 100, 100)}%"></div></div>
          </div>
        </div>
        <div class="card-meta">
          <span class="badge${isPreprint(p) ? ' preprint' : ''}">${esc(p.source)}</span>
          ${!isPreprint(p) ? `<span>${esc(p.journal)}${ifStr}</span>` : ''}
          <span>${esc(p.date)}</span>
          <span title="${esc(authors)}">${esc(authors.length > 60 ? authors.slice(0, 60) + '…' : authors)}</span>
          ${weekTag(p)}
        </div>
        <div class="abstract">${highlight(shown)}${long ? `<button class="toggle-btn" data-act="toggle">${open ? 'Show less' : 'Show more'}</button>` : ''}</div>
        <div class="card-foot">
          <div class="matched">${(p.matched || []).map(k => `<button class="kw" data-kw="${esc(k)}" title="Filter by this keyword">${esc(k)}</button>`).join('')}</div>
          <button class="act${isStar ? ' on' : ''}" data-act="star" aria-pressed="${isStar}">${ICON_STAR.replace('FILL', isStar ? 'currentColor' : 'none')}${isStar ? 'Starred' : 'Star'}</button>
          <button class="act${isRead ? ' read-on' : ''}" data-act="read" aria-pressed="${isRead}">${ICON_CHECK}${isRead ? 'Read' : 'Mark read'}</button>
          <a class="doi-link" href="${esc(u)}" target="_blank" rel="noopener">Open ↗</a>
        </div>
      </article>`;
  }

  const $ = id => document.getElementById(id);

  function renderChips() {
    const counts = {};
    const src = state.view === 'starred' ? [...starred.values()] : pool();
    src.forEach(p => { counts[p.source] = (counts[p.source] || 0) + 1; });
    $('sources').innerHTML = Object.keys(counts).sort().map(src =>
      `<button class="chip" data-src="${esc(src)}" aria-pressed="${state.sources.has(src)}">${esc(src)}<span class="n">${counts[src]}</span></button>`
    ).join('');
    const kwCounts = {};
    src.forEach(p => (p.matched || []).forEach(k => { kwCounts[k] = (kwCounts[k] || 0) + 1; }));
    $('keywords').innerHTML = DATA.keywords.map(k =>
      `<button class="chip" data-kw="${esc(k)}" aria-pressed="${state.keyword === k}">${esc(k)}<span class="n">${kwCounts[k] || 0}</span></button>`
    ).join('');
  }

  // Filter changes start from the first page again
  const update = () => { state.limit = PAGE; render(); };

  function render() {
    const list = filtered();
    const papersEl = $('papers');
    if (state.scope === 'all' && !ALL) {
      papersEl.innerHTML = '<div class="empty">Loading all weeks…</div>';
    } else if (list.length) {
      const rest = list.length - state.limit;
      papersEl.innerHTML = list.slice(0, state.limit).map(card).join('') +
        (rest > 0 ? `<button class="more-btn" data-act="more">Show ${Math.min(rest, PAGE)} more (${rest} left)</button>` : '');
    } else {
      papersEl.innerHTML = `<div class="empty">${state.view === 'starred' && !starred.size
        ? 'No starred papers yet — star papers to build a reading list. It is saved in this browser.'
        : 'No papers match these filters.'}</div>`;
    }
    const base = state.view === 'starred' ? starred.size : pool().length;
    const what = state.view === 'starred' ? 'starred papers'
      : state.scope === 'all' ? `papers across ${(DATA.reports || []).length} weeks` : 'papers';
    $('count').textContent = `Showing ${list.length} of ${base} ${what}`;
    $('star-n').textContent = starred.size ? `(${starred.size})` : '';
    $('export').hidden = state.view !== 'starred' || !starred.size;
    const dirty = state.q || state.sources.size || state.keyword || state.min !== FLOOR;
    $('reset').hidden = !dirty;
    document.querySelectorAll('#view button').forEach(b => b.setAttribute('aria-pressed', b.dataset.view === state.view));
    document.querySelectorAll('#scope button').forEach(b => b.setAttribute('aria-pressed', b.dataset.scope === state.scope));
    $('scope').hidden = (DATA.reports || []).length < 2;
    renderChips();
  }

  // ── Header / footer ──
  $('meta').textContent =
    `${papers.length} of ${DATA.total} relevant papers · Past ${DATA.days} days · Generated ${DATA.generated}`;
  $('footer').textContent = `Generated by PaperTrail · Sources: ${DATA.sources.join(', ')}`;
  if (DATA.imported) {
    $('meta').insertAdjacentHTML('afterend',
      '<div class="imported-note">Recovered from an older report: author lists are shortened.</div>');
  }

  // ── Week navigation (reports are listed newest first) ──
  const REPORTS = DATA.reports || [];
  const here = REPORTS.findIndex(r => r.date === DATA.date);
  const href = r => (DATA.prefix || '') + r.date + '.html';
  if (REPORTS.length > 1 && here !== -1) {
    $('weeks').hidden = false;
    $('week').innerHTML = REPORTS.map((r, i) =>
      `<option value="${i}"${i === here ? ' selected' : ''}>${esc(r.label)} · ${r.n} papers${i === 0 ? ' · latest' : ''}</option>`
    ).join('');
    $('week').addEventListener('change', e => { location.href = href(REPORTS[+e.target.value]); });
    const link = (el, r) => r ? (el.href = href(r)) : el.setAttribute('aria-disabled', 'true');
    link($('prev'), REPORTS[here + 1]);
    link($('next'), REPORTS[here - 1]);
    if (here === 0) $('latest').hidden = true; else $('latest').href = DATA.home;
  }

  // ── Controls ──
  const min = $('min');
  min.min = FLOOR;
  min.value = state.min;
  $('min-val').textContent = state.min.toFixed(2);
  $('sort').value = state.sort;

  let t;
  $('q').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value; update(); }, 120); });
  min.addEventListener('input', e => { state.min = +e.target.value; $('min-val').textContent = state.min.toFixed(2); update(); });
  $('sort').addEventListener('change', e => { state.sort = e.target.value; store.set('prefs', { ...prefs, sort: state.sort }); update(); });
  $('view').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    state.view = b.dataset.view; update();
  });
  $('scope').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    state.scope = b.dataset.scope;
    if (state.scope === 'all') loadAll();
    update();
  });
  $('sources').addEventListener('click', e => {
    const b = e.target.closest('[data-src]'); if (!b) return;
    const s = b.dataset.src;
    state.sources.has(s) ? state.sources.delete(s) : state.sources.add(s);
    update();
  });
  const toggleKw = k => { state.keyword = state.keyword === k ? null : k; update(); };
  $('keywords').addEventListener('click', e => { const b = e.target.closest('[data-kw]'); if (b) toggleKw(b.dataset.kw); });
  $('reset').addEventListener('click', () => {
    state.q = ''; $('q').value = '';
    state.sources.clear(); state.keyword = null;
    state.min = FLOOR; min.value = state.min; $('min-val').textContent = state.min.toFixed(2);
    update();
  });
  $('export').addEventListener('click', () => {
    const lines = [...starred.values()].sort((a, b) => b.score - a.score).map(p =>
      `- [${p.title}](${url(p)})  \n  ${p.authors ? p.authors.split(/[,;]/)[0].trim() + ' et al. · ' : ''}${isPreprint(p) ? p.source : p.journal} · ${p.date}`);
    const blob = new Blob([`# PaperTrail — starred papers\n\n${lines.join('\n')}\n`], { type: 'text/markdown' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'papertrail-starred.md' });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  $('papers').addEventListener('click', e => {
    const kw = e.target.closest('.matched [data-kw]');
    if (kw) { toggleKw(kw.dataset.kw); return; }
    const btn = e.target.closest('[data-act]'); if (!btn) return;
    if (btn.dataset.act === 'more') { state.limit += PAGE; render(); return; }
    const id = btn.closest('.card').dataset.id;
    const p = pool().find(x => x.id === id) || papers.find(x => x.id === id) || starred.get(id);
    const act = btn.dataset.act;
    if (act === 'toggle') { state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id); }
    if (act === 'star')   { starred.has(id) ? starred.delete(id) : starred.set(id, { ...p, weeks: p.weeks || [DATA.date] }); saveStars(); }
    if (act === 'read')   { read.has(id) ? read.delete(id) : read.add(id); saveRead(); }
    render();
  });

  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement !== $('q')) { e.preventDefault(); $('q').focus(); }
    if (e.key === 'Escape' && document.activeElement === $('q')) { $('q').blur(); }
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (!typing && e.key === '[' && $('prev').href) location.href = $('prev').href;
    if (!typing && e.key === ']' && $('next').href) location.href = $('next').href;
  });

  render();
})();
</script>
</body>
</html>
"""


_FIELDS = ("id", "title", "abstract", "authors", "date", "source", "journal",
           "if", "score", "score_semantic", "score_lexical", "matched")

# Keys added per page at render time; not part of a report's stored data
_PAGE_KEYS = ("reports", "prefix", "home")

_DATA_RE = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.S)
_REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")


def render_page(data: dict) -> str:
    # Escape "</" so abstracts can never close the <script> tag early
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__DATE__", data["generated"])
            .replace("__DATA__", payload))


def read_report(path) -> dict:
    """Recover the stored data of an archived report page."""
    m = _DATA_RE.search(Path(path).read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path}: no embedded report data")
    data = json.loads(m.group(1))
    for k in _PAGE_KEYS:
        data.pop(k, None)
    return data


def write_search_index(reports: list[dict], path: Path):
    """All weeks' papers in one file, newest week first, one entry per paper.

    Loaded by the page only when searching "All weeks". A plain script (not
    JSON + fetch) so it also loads when the pages are opened from disk.
    """
    index: dict[str, dict] = {}   # key -> entry; keyed by both URL and title
    entries: list[dict] = []
    for r in reports:
        for p in r["papers"]:
            # Same paper = same URL or same title up to case/punctuation
            keys = [p["id"], re.sub(r"[^a-z0-9]+", " ", p["title"].lower()).strip()]
            entry = next((index[k] for k in keys if k in index), None)
            if entry is None:
                entry = {**p, "weeks": [r["date"]]}
                entries.append(entry)
            elif r["date"] not in entry["weeks"]:
                entry["weeks"].append(r["date"])
            for k in keys:
                index.setdefault(k, entry)
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    path.write_text(f"window.PAPERTRAIL_INDEX = {payload};\n", encoding="utf-8")


def rebuild_site(reports_dir="reports", latest_path="papers.html"):
    """Re-render every archived week plus the latest page with a fresh week list."""
    reports_dir = Path(reports_dir)
    files = sorted((f for f in reports_dir.glob("*.html") if _REPORT_RE.match(f.name)),
                   reverse=True)
    if not files:
        return
    reports = [read_report(f) for f in files]
    manifest = [{"date": r["date"],
                 "label": date.fromisoformat(r["date"]).strftime("%b %d, %Y"),
                 "n": len(r["papers"])} for r in reports]

    write_search_index(reports, reports_dir / "search-index.js")

    latest = Path(latest_path)
    # Relative links from an archived week back to the latest page
    home = Path("..", latest.name) if latest.parent == reports_dir.parent else latest.resolve()
    for f, r in zip(files, reports):
        f.write_text(render_page({**r, "reports": manifest, "prefix": "",
                                  "home": home.as_posix()}), encoding="utf-8")
    latest.write_text(render_page({**reports[0], "reports": manifest,
                                   "prefix": f"{reports_dir.name}/",
                                   "home": latest.name}), encoding="utf-8")


def write_html(papers: list[dict], top_n: int, keywords: list[str],
               days: int, sources: list[str], threshold: float = 0.0,
               reports_dir="reports", latest_path="papers.html",
               report_date: date | None = None, imported: bool = False) -> Path:
    """Archive this run as reports/<date>.html and refresh all pages.

    A second run on the same day replaces that day's report.
    """
    report_date = report_date or date.today()
    subset = [{k: p.get(k) for k in _FIELDS} for p in papers[:top_n]]
    for p in subset:
        p["score"] = float(p["score"] or 0)
        p["matched"] = p["matched"] or []

    data = {
        "date":      report_date.isoformat(),
        "generated": report_date.strftime("%B %d, %Y"),
        "days":      days,
        "sources":   list(sources),
        "keywords":  list(keywords),
        "threshold": float(threshold),
        "total":     len(papers),
        "papers":    subset,
    }
    if imported:
        data["imported"] = True

    out = Path(reports_dir) / f"{data['date']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(data), encoding="utf-8")
    rebuild_site(reports_dir, latest_path)
    return out
