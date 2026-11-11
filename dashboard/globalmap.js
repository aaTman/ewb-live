/* ==========================================================================
   globalmap.js — GLOBAL EVENT DASHBOARD (owner 2026-11-08).
   All curated events of the last 90 days on an Equal Earth map
   (d3-geo geoEqualEarth, vendored), a daily time slider with play/pause,
   multi-select across map + list + Gantt strip, and URL state
   (#global=id,id&date=YYYY-MM-DD) riding the app's existing history
   plumbing. Visual spec: notes/design.md "Global event dashboard";
   data contract: notes/kt_design.md §2 (data/results/global_timeline.json,
   schema 1). Masks decode EXACTLY like eventviz.js daily_bits: day-major,
   row-major, MSB-first, stride = ceil(ny*nx/8); row order follows the
   grid's ASCENDING lat array (row 0 = lat0 = lowest latitude).
   ========================================================================== */
(function () {
  'use strict';

  const TYPES = {
    heat_wave: { css: 'heat', label: 'Heat wave', short: 'heat' },
    freeze: { css: 'freeze', label: 'Freeze', short: 'freeze' },
    atmospheric_river: { css: 'ar', label: 'Atmospheric river', short: 'AR' },
  };
  const LANE_ORDER = ['heat_wave', 'atmospheric_river', 'freeze']; // design spec order
  const PAD = 12;          // fitExtent padding (design spec)
  const HIT_MIN = 44;      // min touch target (px)

  let data = null;         // global_timeline.json
  let dayList = [];        // ISO dates across the window
  let dateIdx = 0;
  const sel = new Set();
  let land = null, graticule = null, proj = null, path = null;
  let playing = null;      // interval id while playing
  let mounted = false;
  let bootParams = {};     // hash params captured synchronously at mount
  let fit = null;          // current projection fit {W,H}

  const $ = (s) => document.querySelector(s);
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (t, attrs, parent) => {
    const n = document.createElementNS(NS, t);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };

  /* ---------------- dates ---------------- */
  function dayWindow(w) {
    const out = [];
    const d = new Date(w.start + 'T00:00:00Z');
    // contract: window.days; fall back to the end-start span if a producer omits it
    let n = w.days;
    if (!n) n = Math.round((new Date(w.end + 'T00:00:00Z') - d) / 86400000) + 1;
    for (let i = 0; i < n; i++) {
      out.push(d.toISOString().slice(0, 10));
      d.setUTCDate(d.getUTCDate() + 1);
    }
    return out;
  }
  const fmtShort = (iso) => {
    const d = new Date(iso + 'T00:00:00Z');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  };

  /* ---------------- mask decode (parity with eventviz daily_bits) ------- */
  const maskCache = new Map();   // evId -> array of Uint8Array | null (per day)
  function dayMask(ev, dayIdx) {
    if (!ev.masks || !ev.masks.length) return null;
    let arr = maskCache.get(ev.id);
    if (!arr) {
      const { ny, nx } = ev.grid;
      const stride = Math.ceil(ny * nx / 8);
      arr = ev.masks.map((b64) => {
        const bin = atob(b64);
        const u = new Uint8Array(ny * nx);
        for (let k = 0; k < ny * nx; k++) {
          const byte = bin.charCodeAt((k >> 3));
          u[k] = (byte >> (7 - (k & 7))) & 1;
        }
        return u;
      });
      maskCache.set(ev.id, arr);
    }
    return arr[dayIdx] || null;
  }
  /* Antimeridian safety net. An event that crosses ±180 must carry lon_min > lon_max; when a
     producer instead records min/max of the raw longitudes the box becomes the whole latitude
     band (span ≈ 360°) and every renderer draws a world-wide rectangle. Recover the true
     wrapped box from the footprint masks: the widest empty longitude gap (circular) bounds it.
     Returns [S,N,W,E] or null (no footprint → caller must not draw the band). */
  const FULL_BAND_SPAN = 300;
  function bboxSpan(b) { return b[3] >= b[2] ? b[3] - b[2] : b[3] + 360 - b[2]; }
  function isFullBand(b) { return Array.isArray(b) && b[3] >= b[2] && bboxSpan(b) >= FULL_BAND_SPAN; }
  function recoverBbox(ev) {
    if (!ev.grid || !ev.masks || !ev.masks.length) return null;
    const { nx, ny, lon0, step_lon } = ev.grid;
    const cols = new Uint8Array(nx);
    for (let d = 0; d < ev.masks.length; d++) {
      const m = dayMask(ev, d);
      if (!m) continue;
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) if (m[j * nx + i]) cols[i] = 1;
    }
    if (!cols.some((c) => c)) return null;
    const norm = (l) => ((l + 180) % 360 + 360) % 360 - 180;
    if (nx * step_lon < FULL_BAND_SPAN) {
      // regional grid (it covers only the event's window, longitudes continuous across ±180):
      // the occupied column extent IS the box
      const f = cols.indexOf(1), l = cols.lastIndexOf(1);
      return [ev.bbox[0], ev.bbox[1], norm(lon0 + f * step_lon), norm(lon0 + (l + 1) * step_lon)];
    }
    // global grid: longest circular run of empty columns
    let best = 0, bestEnd = -1, run = 0;
    for (let k = 0; k < 2 * nx; k++) {
      if (!cols[k % nx]) { run++; if (run > best && run < nx) { best = run; bestEnd = k % nx; } } else run = 0;
    }
    if (best * step_lon < 20) return null;                       // genuinely wide event: keep the bbox
    const first = (bestEnd + 1) % nx;                            // first occupied column after the gap
    const last = (bestEnd - best + nx) % nx;                     // last occupied column before it
    const W = norm(lon0 + first * step_lon), E = norm(lon0 + (last + 1) * step_lon);
    return [ev.bbox[0], ev.bbox[1], W, E];
  }
  function repairBboxes() {
    for (const ev of data.events) {
      if (!isFullBand(ev.bbox)) continue;
      const fix = recoverBbox(ev);
      if (fix && typeof window.applyBboxFix === 'function') {
        ev.bbox = fix;
        window.applyBboxFix(ev.id, fix);                         // catalog + detail use state.events
      }
    }
  }
  function evDayIndex(ev, iso) { return ev.days.indexOf(iso); }
  const isActive = (ev, iso) => evDayIndex(ev, iso) >= 0;

  /* active set under the page type-tab filter (chips ⇄ type tabs, design).
     NOTE: `state` is a top-level const in app.js — a global lexical binding,
     NOT a window property; must be referenced by bare identifier with typeof. */
  const pageTab = () => (typeof state !== 'undefined' && state.tab) || 'all';
  function visibleEvents() {
    const tab = pageTab();
    return data.events.filter((e) => (tab === 'all' || e.type === tab));
  }

  /* ---------------- projection & base layers ---------------- */
  function contentRect() {
    // Union of event bboxes; wrap/continuous-lon events widen to the full
    // lon range (the Equal Earth map is whole-world; dateline content
    // renders on both edges, which is geographically correct here).
    let s = 85, n = -85, w = 180, e = -180, wrapped = false;
    for (const ev of data.events) {
      const [bS, bN, bW, bE] = ev.bbox;
      if (bS < s) s = bS; if (bN > n) n = bN;
      if (bW > bE) { wrapped = true; continue; }
      // ponytail 06:24 MUST: bbox-only entries (no viz.json yet) have no grid
      if (ev.grid && ev.grid.continuous_lon) { wrapped = true; }
      if (bW < w) w = bW; if (bE > e) e = bE;
    }
    if (wrapped || w > e) { w = -180; e = 180; }
    const m = 4;
    return { s: Math.max(-89.5, s - m), n: Math.min(85, n + m), w, e };
  }

  function fitProjection() {
    const wrapEl = $('#gm-map-wrap');
    const W = Math.max(240, wrapEl.clientWidth);
    const rect = contentRect();
    proj = d3.geoEqualEarth();
    proj.scale(1).translate([0, 0]);
    // Equal Earth is pseudocylindrical: x = f(lat)·lon, so the projected
    // width of a lat/lon rect varies with latitude — sample the parallels
    // for the true bounds (corner-only fit overflows at the equator).
    let xmin = Infinity, xmax = -Infinity;
    for (let la = rect.s; la <= rect.n + 1e-9; la += 2) {
      const a = proj([rect.w, la]), c = proj([rect.e, la]);
      if (a[0] < xmin) xmin = a[0];
      if (c[0] > xmax) xmax = c[0];
    }
    const yN = proj([0, rect.n])[1], yS = proj([0, rect.s])[1];
    const dw = Math.max(1e-6, xmax - xmin), dh = Math.max(1e-6, yS - yN);
    // Height follows the fitted aspect (design: fit to section width).
    const H = Math.round(Math.min(Math.max((dh / dw) * (W - 2 * PAD) + 2 * PAD, 240), W * 0.75));
    const k = Math.min((W - 2 * PAD) / dw, (H - 2 * PAD) / dh);
    proj.scale(k)
       .translate([W / 2 - ((xmin + xmax) / 2) * k, H / 2 - ((yN + yS) / 2) * k]);
    path = d3.geoPath(proj);
    const svg = $('#gm-map');
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    wrapEl.style.minHeight = H + 'px';   // wrap hugs the fitted map (no dead strip)
    fit = { W, H };
  }

  function renderBase() {
    const svg = $('#gm-map');
    // wipe dynamic + base groups, rebuild base
    svg.innerHTML = '';
    /* G-25: AR footprints are hatched so heat-vs-AR never relies on red/green
       alone (deutan/protan ΔE≈11–17); heat/freeze stay solid */
    const defs = mk('defs', {}, svg);
    const pat = mk('pattern', { id: 'gm-hatch-ar', width: 5, height: 5,
      patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
    mk('rect', { width: 5, height: 5, style: 'fill: var(--series-ar); opacity: .35' }, pat);
    mk('rect', { width: 2, height: 5, style: 'fill: var(--series-ar)' }, pat);
    const gBase = mk('g', { class: 'gm-base' }, svg);
    mk('path', { class: 'gm-sphere', d: path({ type: 'Sphere' }),
                 fill: 'var(--canvas)', stroke: 'var(--border-strong)', 'stroke-width': 0.8 }, gBase);
    mk('path', { class: 'gm-grat', d: path(graticule) }, gBase);
    mk('path', { class: 'gm-land', d: path(land) }, gBase);
    mk('g', { id: 'gm-evs' }, svg);
  }

  /* ---------------- footprint geometry ---------------- */
  function footPathD(ev, mask) {
    const { lat0, lon0, step_deg, step_lon, ny, nx } = ev.grid;
    const parts = [];
    for (let j = 0; j < ny; j++) {
      let i = 0;
      while (i < nx) {
        if (!mask[j * nx + i]) { i++; continue; }
        let i2 = i;
        while (i2 + 1 < nx && mask[j * nx + i2 + 1]) i2++;
        const la = lat0 + j * step_deg, lb = lat0 + (j + 1) * step_deg;
        const wa = lon0 + i * step_lon, eb = lon0 + (i2 + 1) * step_lon;
        // a run can straddle ±180 (continuous-longitude grids, e.g. 156.75…195°): proj() wraps each
        // corner separately, so an unsplit run drew a trapezoid from the right edge of the map to
        // the left one — the full-width band over the whole latitude row. Cut it at the seam.
        const w0 = ((wa + 180) % 360 + 360) % 360 - 180;
        const e0 = w0 + (eb - wa);
        const segs = e0 > 180 ? [[w0, 180], [-180, e0 - 360]] : [[w0, e0]];
        for (const [sa, sb] of segs) {
          // pseudocylindrical: x compresses with |lat| → the cell is a trapezoid
          const tl = proj([sa, lb]), tr = proj([sb, lb]);
          const br = proj([sb, la]), bl = proj([sa, la]);
          parts.push(`M${tl[0].toFixed(1)} ${tl[1].toFixed(1)}L${tr[0].toFixed(1)} ${tr[1].toFixed(1)}`
            + `L${br[0].toFixed(1)} ${br[1].toFixed(1)}L${bl[0].toFixed(1)} ${bl[1].toFixed(1)}Z`);
        }
        i = i2 + 1;
      }
    }
    return parts.join('');
  }

  function bboxPaths(ev) {
    // bbox [lat_min, lat_max, lon_min, lon_max]; wrap → two rects (catalog parity)
    const [s, n, w, e] = ev.bbox;
    if (isFullBand(ev.bbox)) return '';       // unrecoverable degenerate box: never paint a world-wide band
    const rect = (wa, ea) => {
      const tl = proj([wa, n]), tr = proj([ea, n]), br = proj([ea, s]), bl = proj([wa, s]);
      return `M${tl[0].toFixed(1)} ${tl[1].toFixed(1)}L${tr[0].toFixed(1)} ${tr[1].toFixed(1)}`
        + `L${br[0].toFixed(1)} ${br[1].toFixed(1)}L${bl[0].toFixed(1)} ${bl[1].toFixed(1)}Z`;
    };
    return w <= e ? rect(w, e) : rect(w, 180) + rect(-180, e);
  }

  /* ---------------- map render ---------------- */
  function renderMap() {
    const g = $('#gm-evs');
    g.innerHTML = '';
    if (!fit) return;
    const iso = dayList[dateIdx];
    const anySel = sel.size > 0;
    const active = visibleEvents().filter((ev) => isActive(ev, iso));
    // draw large footprints first; small ones on top (catalog overlap parity)
    active.sort((a, b) => (a.bbox[1] - a.bbox[0]) * (spanLon(a.bbox)) - (b.bbox[1] - b.bbox[0]) * spanLon(b.bbox));
    for (const ev of active) {
      const t = TYPES[ev.type] || { css: 'heat' };
      const cls = 'gm-ev' + (sel.has(ev.id) ? ' sel' : '') + (anySel && !sel.has(ev.id) ? ' dim' : '');
      const node = mk('g', {
        class: cls, 'data-id': ev.id, role: 'button', tabindex: 0,
        'aria-pressed': sel.has(ev.id) ? 'true' : 'false',
        'aria-label': `${ev.title}`,
      }, g);
      const mask = dayMask(ev, evDayIndex(ev, iso));
      const d = mask ? footPathD(ev, mask) : '';
      if (d) mk('path', { class: `gm-foot t-${t.css}`, d,
        style: `fill: ${t.css === 'ar' ? 'url(#gm-hatch-ar)' : `var(--series-${t.css})`}` }, node);
      const bd = bboxPaths(ev);
      if (sel.has(ev.id)) mk('path', { class: 'gm-halo', d: bd }, node);
      mk('path', { class: `gm-bbox t-${t.css}`, d: bd, style: `stroke: var(--series-${t.css})` }, node);
      // invisible expanded hit rect (min touch target), union of bbox pixels
      let hb = null;
      try { hb = node.getBBox(); } catch (e) { /* detached in tests */ }
      if (hb && hb.width >= 0) {
        const hw = Math.max(hb.width, HIT_MIN), hh = Math.max(hb.height, HIT_MIN);
        mk('rect', { class: 'gm-hit', x: hb.x + hb.width / 2 - hw / 2, y: hb.y + hb.height / 2 - hh / 2,
                     width: hw, height: hh, fill: 'transparent', 'pointer-events': 'all' }, node);
      }
      node.addEventListener('click', () => toggle(ev.id));
      node.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(ev.id); }
      });
      node.addEventListener('pointerenter', () => node.classList.add('hov'));
      node.addEventListener('pointerleave', () => node.classList.remove('hov'));
    }
    const svg = $('#gm-map');
    svg.setAttribute('aria-label',
      `Equal Earth world map — ${fmtLong(iso)}: ${active.length} event${active.length === 1 ? '' : 's'} active`);
  }
  const spanLon = (b) => (b[2] <= b[3] ? b[3] - b[2] : 360);
  const fmtLong = (iso) => new Date(iso + 'T00:00:00Z')
    .toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });

  // hatched swatch for AR (matches the map footprint; see renderBase)
  const swatch = (css) => css === 'ar'
    ? 'repeating-linear-gradient(45deg, var(--series-ar) 0 2px, transparent 2px 4px)'
    : `var(--series-${css})`;
  /* ---------------- chips / list / readout ---------------- */
  function renderSide() {
    const iso = dayList[dateIdx];
    const chips = $('#gm-chips');
    chips.innerHTML = '';
    const tab = pageTab();
    for (const k of LANE_ORDER) {
      const t = TYPES[k];
      const n = data.events.filter((e) => e.type === k && isActive(e, iso)).length;
      const b = document.createElement('button');
      b.className = 'gm-chip'; b.type = 'button';
      b.setAttribute('aria-pressed', tab === k ? 'true' : 'false');
      b.innerHTML = `<i style="background: ${swatch(t.css)}"></i>${t.label}`
        + ` <span class="gm-chip-n">${n}</span>`;
      b.onclick = () => {
        const next = (tab === k) ? 'all' : k;
        if (typeof state !== 'undefined') {
          state.tab = next;
          // ponytail 07:16 NICE: persist like the tab buttons do, or the
          // filter silently reverts on reload
          if (typeof saveState === 'function') saveState();
          // ponytail 08:1x: mirror the tab handler — renderAll covers map,
          // table, scorecard AND skill charts (the old trio skipped the latter)
          if (typeof renderControls === 'function') renderControls();
          if (typeof renderAll === 'function') renderAll();
        }
        render();
      };
      chips.appendChild(b);
    }
    const list = $('#gm-list');
    list.innerHTML = '';
    const act = visibleEvents().filter((ev) => isActive(ev, iso));
    for (const ev of act) {
      const t = TYPES[ev.type] || { css: 'heat' };
      const row = document.createElement('div');
      row.className = 'gm-lrow'; row.setAttribute('role', 'listitem');
      if (sel.has(ev.id)) row.setAttribute('aria-current', 'true');   // aria-selected is not valid on listitem
      row.tabIndex = 0;
      row.classList.toggle('sel', sel.has(ev.id));
      row.innerHTML = `<i style="background: ${swatch(t.css)}"></i>`
        + `<span class="gm-ltitle" title="${escA(ev.title)}">${escA(ev.title)}</span>`
        + `<span class="gm-ldates">${fmtShort(ev.days[0])}–${fmtShort(ev.days[ev.days.length - 1])}</span>`;
      row.addEventListener('click', () => toggle(ev.id));
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(ev.id); }
      });
      const open = document.createElement('button');
      open.textContent = 'open'; open.type = 'button';
      open.addEventListener('click', (e) => { e.stopPropagation(); openEvent(ev.id); });
      row.appendChild(open);
      list.appendChild(row);
    }
    $('#gm-count').textContent = `${data.events.length} events · ${fmtShort(dayList[0])} – ${fmtShort(dayList[dayList.length - 1])}`;
    $('#gm-date').textContent = fmtLong(iso);
    const note = $('#gm-note');
    // the element hides via class="hidden" (app convention), not the .hidden prop
    if (!act.length) { note.classList.remove('hidden'); note.textContent = 'No events on this date.'; }
    else { note.classList.add('hidden'); note.textContent = ''; }
  }
  const escA = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ---------------- Gantt strip ---------------- */
  function renderGantt() {
    const svg = $('#gm-gantt');
    svg.innerHTML = '';
    const W = Math.max(320, $('#gm-map-wrap').clientWidth);
    const LANE_H = 12, GAP = 4, LAB = 44, TOP = 2, AX = 16;
    const H = TOP + LANE_H * 3 + GAP * 2 + AX;
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const N = dayList.length;
    const x = (i) => LAB + (i / N) * (W - LAB - 4);
    const anySel = sel.size > 0;
    const tab = pageTab();
    LANE_ORDER.forEach((type, li) => {
      const y = TOP + li * (LANE_H + GAP);
      const lab = document.createElementNS(NS, 'text');
      lab.setAttribute('x', 0); lab.setAttribute('y', y + LANE_H - 3);
      lab.setAttribute('class', 'gm-lane-lab');
      lab.textContent = TYPES[type].short;
      svg.appendChild(lab);
      mk('rect', { class: 'gm-lane', x: LAB, y, width: W - LAB - 4, height: LANE_H, rx: 2, opacity: .35 }, svg);
      for (const ev of data.events) {
        if (ev.type !== type) continue;
        // clamp bars to the window (events may start/end outside the 90d span)
        const first = ev.days[0], last = ev.days[ev.days.length - 1];
        let i0 = dayList.indexOf(first);
        if (i0 < 0) i0 = dayList.findIndex((d) => d >= first && d <= last);
        let i1 = dayList.indexOf(last);
        if (i1 < 0) for (let q = dayList.length - 1; q >= 0; q--)
          if (dayList[q] >= first && dayList[q] <= last) { i1 = q; break; }
        if (i0 < 0 || i1 < 0 || i1 < i0) continue;
        const bx = x(i0), bw = Math.max(2, x(i1) - x(i0) + (W - LAB - 4) / N);
        const isSel = sel.has(ev.id);
        const bar = mk('rect', {
          class: 'gm-bar', x: bx, y, width: bw, height: LANE_H, rx: 2,
          style: `fill: var(--series-${TYPES[type].css})`,
          opacity: isSel ? 1 : (anySel ? .18 : (tab !== 'all' && tab !== type ? .18 : .8)),
          stroke: isSel ? 'var(--canvas)' : 'none', 'stroke-width': isSel ? 1 : 0,
          cursor: 'pointer',
        }, svg);
        bar.addEventListener('click', () => {
          if (!sel.has(ev.id)) sel.add(ev.id); else sel.delete(ev.id);
          const start = dayList.indexOf(ev.days[0]);
          if (start >= 0) dateIdx = start;
          writeUrl(true);
          render();
        });
      }
    });
    // cursor: the selected day (G-23), 1px ink line spanning the lanes
    const cx = x(dateIdx) + (W - LAB - 4) / N / 2;
    mk('line', { class: 'gm-cursor', x1: cx, x2: cx, y1: 0, y2: H - AX + 3,
                 style: 'stroke: var(--matte-black); stroke-width: 1; pointer-events: none' }, svg);
    // first-of-month ticks, shared scale
    let lastLab = -1e9;
    for (let i = 0; i < N; i++) {
      const d = new Date(dayList[i] + 'T00:00:00Z');
      if (d.getUTCDate() !== 1) continue;
      mk('line', { class: 'gm-tick', x1: x(i), x2: x(i), y1: TOP, y2: H - AX + 3 }, svg);
      if (x(i) - lastLab < 34) continue;
      lastLab = x(i);
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('x', x(i) + 2); t.setAttribute('y', H - 3);
      t.textContent = d.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
      svg.appendChild(t);
    }
  }

  /* ---------------- selection + URL ---------------- */
  function toggle(id) {
    if (sel.has(id)) sel.delete(id); else sel.add(id);
    writeUrl(true);
    render();
  }

  function urlParams() {
    if (!mounted || !data) {
      // pre-mount (boot race with deep-link open): keep whatever the URL had
      const p = hashParams();
      const o = {};
      if (p.global) o.global = p.global;
      if (p.date) o.date = p.date;
      return o;
    }
    const out = {};
    if (sel.size) out.global = [...sel].join(',');
    out.date = dayList[dateIdx];
    return out;
  }

  function writeUrl(push) {
    if (!mounted || !data) return;
    try {
      const p = hashParams();
      if (sel.size) p.global = [...sel].join(','); else delete p.global;
      p.date = dayList[dateIdx];
      const u = locationFor(p);
      if (push) history.pushState({ event: state.selectedEvent || null }, '', u);
      else history.replaceState({ event: state.selectedEvent || null }, '', u);
    } catch (e) { /* file:// */ }
  }

  function applyUrl(params) {
    if (!mounted || !data) return false;
    let changed = false;
    const wantSel = (params.global || '').split(',').filter(Boolean);
    const cur = [...sel];
    if (wantSel.length !== cur.length || wantSel.some((v, i) => v !== cur[i])) {
      sel.clear();
      for (const id of wantSel) if (data.events.some((e) => e.id === id)) sel.add(id);
      changed = true;
    }
    if (params.date && dayList.includes(params.date) && dayList[dateIdx] !== params.date) {
      dateIdx = dayList.indexOf(params.date);
      $('#gm-slider').value = dateIdx;
      changed = true;
    }
    if (changed) render();
    return changed;
  }

  function openEvent(id) {
    try {
      const p = Object.assign(hashParams(), urlParams(), { event: id });
      delete p.lead;
      location.hash = Object.entries(p).map(([a, b]) => `${a}=${encodeURIComponent(b)}`).join('&');
    } catch (e) {
      if (typeof selectEvent === 'function') selectEvent(id, true);
    }
    // hashchange fires the app's sync path (selectEvent with push:false)
  }

  /* ---------------- play / slider ---------------- */
  function stepPlay() {
    if (dateIdx >= dayList.length - 1) { stopPlay(); return; }
    dateIdx++;
    $('#gm-slider').value = dateIdx;
    writeUrl(false);
    render();
  }
  function stopPlay() {
    if (playing) { clearInterval(playing); playing = null; }
    const b = $('#gm-play');
    b.textContent = '▶ play'; b.setAttribute('aria-label', 'Play timeline');
  }
  function togglePlay() {
    if (playing) { stopPlay(); return; }
    if (dateIdx >= dayList.length - 1) { dateIdx = 0; $('#gm-slider').value = 0; render(); }
    playing = setInterval(stepPlay, 500);
    const b = $('#gm-play');
    b.textContent = '⏸ pause'; b.setAttribute('aria-label', 'Pause timeline');
  }

  /* ---------------- render root ---------------- */
  /* every toggle re-renders by rebuilding nodes, which dropped keyboard focus to
     <body> (Enter on a map event / list row): remember what had focus and
     re-focus its twin afterwards */
  function focusKey() {
    const a = document.activeElement;
    if (!a || !a.closest) return null;
    const host = a.closest('#gm-map, #gm-list, #gm-chips');
    const n = a.closest('[data-id], .gm-lrow, .gm-chip');
    if (!host || !n) return null;
    const idx = host.id === 'gm-list' ? [...host.children].indexOf(a.closest('.gm-lrow')) : -1;
    return { host: host.id, id: n.dataset && n.dataset.id, idx, chip: host.id === 'gm-chips' ? [...host.children].indexOf(n) : -1,
      inOpen: a.tagName === 'BUTTON' && host.id === 'gm-list' };
  }
  function restoreFocus(k) {
    if (!k) return;
    let t = null;
    if (k.host === 'gm-map' && k.id) t = document.querySelector(`#gm-map [data-id="${k.id}"]`);
    else if (k.host === 'gm-list' && k.idx >= 0) {
      const row = $('#gm-list').children[k.idx];
      t = row && (k.inOpen ? row.querySelector('button') : row);
    } else if (k.host === 'gm-chips' && k.chip >= 0) t = $('#gm-chips').children[k.chip];
    if (t && typeof t.focus === 'function') t.focus({ preventScroll: true });
  }
  function render() {
    if (!mounted || !data) return;
    const fk = focusKey();
    renderMap();
    renderSide();
    renderGantt();
    restoreFocus(fk);
    const s = $('#gm-slider');
    const act = visibleEvents().filter((ev) => isActive(ev, dayList[dateIdx]));
    s.setAttribute('aria-valuetext', `${fmtLong(dayList[dateIdx])} — ${act.length} event${act.length === 1 ? '' : 's'} active`);
  }
  // app.js calls this when the catalog type tabs change (chip ⇄ tab parity)
  function onTabChange() { render(); }

  function fail(msg) {
    $('#gm-skel').hidden = true;
    $('#gm-map').setAttribute('hidden', '');   // `hidden` IDL prop doesn't exist on SVGElement
    $('#gm-gantt').classList.add('hidden');    // same reason (styles.css .hidden works on SVG)
    const note = $('#gm-note');
    note.hidden = false;
    note.classList.remove('hidden');   // the app hides it via class, not attribute
    note.textContent = msg;
  }

  /* ---------------- boot ---------------- */
  /* LCP: the timeline + land fetches used to start only after the score JSON had
     finished (weeks → data → timeline → land: a 4-deep chain). app.js calls
     prefetch() first thing in boot so they ride in parallel with it. */
  let pre = null;
  function prefetch() {
    if (pre) return;
    pre = {
      timeline: fetch('data/results/global_timeline.json', { cache: 'no-cache' }),
      land: fetch('geo/land-110m.geojson'),
    };
    pre.timeline.catch(() => {}); pre.land.catch(() => {});   // consumers below handle failures
  }
  let started = false;
  async function mount() {
    if (started) return;     // app.js also calls mount(); the first caller wins
    started = true;
    if (mounted) return;
    // capture URL state SYNCHRONOUSLY — a deep-linked #event open in boot
    // may replaceState the hash before our fetch resolves
    bootParams = hashParams();
    const slider = $('#gm-slider');
    $('#gm-play').onclick = togglePlay;
    slider.addEventListener('input', () => {
      stopPlay();
      dateIdx = +slider.value;
      writeUrl(false);
      render();
    });
    slider.addEventListener('keydown', (e) => {
      if (e.key === 'Home') { dateIdx = 0; slider.value = 0; writeUrl(true); render(); }
      if (e.key === 'End') { dateIdx = dayList.length - 1; slider.value = dateIdx; writeUrl(true); render(); }
    });
    let ro = null;
    if ('ResizeObserver' in window) {
      let raf = 0;
      ro = new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          if (mounted) { fitProjection(); renderBase(); render(); }   // NOT `data` — land/graticule arrive after data
        });
      });
      ro.observe($('#gm-map-wrap'));
    }
    let json = null;
    try {
      const res = await (pre ? pre.timeline : fetch('data/results/global_timeline.json', { cache: 'no-cache' }));
      if (!res.ok) throw new Error('HTTP ' + res.status);
      json = await res.json();
      if (!json || !Array.isArray(json.events) || !json.window) throw new Error('bad payload');
    } catch (err) {
      // 404/204/corrupt → honest message; one concise warning, never a blank map
      console.warn('global timeline unavailable:', err.message);
      fail('Global timeline is not available for this run yet.');
      return;
    }
    try {
      await _build(json);
    } catch (err) {
      // ponytail 06:24: any render-path throw must leave an honest state,
      // not a permanent shimmer (mount is fire-and-forget by design)
      console.warn('global dashboard failed:', err.message);
      fail('Global timeline could not be rendered for this run.');
    }
  }

  async function _build(json) {
    const slider = $('#gm-slider');
    data = json;
    repairBboxes();
    dayList = dayWindow(json.window);
    slider.max = String(dayList.length - 1);
    // Default to the LAST day that has an active event (not a guaranteed-empty end day)
    // open on the BUSIEST day (most active events; ties → latest) so the hero is never an empty map (G-11)
    let dflt = dayList.length - 1, bestN = -1;
    for (let d = 0; d < dayList.length; d++) {
      const n = data.events.reduce((k, e) => k + (isActive(e, dayList[d]) ? 1 : 0), 0);
      if (n >= bestN) { bestN = n; dflt = d; }
    }
    dateIdx = dflt;
    slider.value = String(dateIdx);
    // restore from URL if present
    for (const id of (bootParams.global || '').split(',').filter(Boolean))
      if (data.events.some((e) => e.id === id)) sel.add(id);
    if (bootParams.date && dayList.includes(bootParams.date)) { dateIdx = dayList.indexOf(bootParams.date); slider.value = dateIdx; }
    land = null;
    try {
      const lr = await (pre ? pre.land : fetch('geo/land-110m.geojson'));
      if (lr.ok) land = await lr.json();
    } catch (e) { /* vector map still renders ocean-only + footprints */ }
    graticule = d3.geoGraticule().step([10, 10])();
    if (!land) land = { type: 'FeatureCollection', features: [] };
    mounted = true;
    fitProjection();
    renderBase();
    $('#gm-skel').hidden = true;
    $('#gm-map').removeAttribute('hidden');   // NOT .hidden — SVGElement has no IDL hidden
    render();
    writeUrl(false);
  }

  window.GlobalDash = { mount, prefetch, render, onTabChange, urlParams, applyUrl };
  /* Self-start: the timeline is an 8 KB file and does not depend on the score JSON, so
     don't queue it behind it — this is what the LCP element (first list row) waits on. */
  prefetch();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
