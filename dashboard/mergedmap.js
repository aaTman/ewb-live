/* ==========================================================================
   mergedmap.js — THE ONE GLOBAL MAP (owner 2026-11-08, briefs/merged_global_map.md).
   Replaces GlobalDash (SVG) and RawDash (canvas) with a single view:
   the 6-hourly raw exceedance canvas layers + time slider, overlaid with
   EVENT BOXES (SVG, same projection) that appear at the frame where the
   event FIRST MEETS THE DETECTION CRITERIA (`qualified_at`, 6-hourly step)
   and stay until the event ends. Clickable (open detail; Back returns),
   multi-select highlight (#sel=), Gantt strip with qualified→end bars
   (click a bar = select + jump slider to the qualified step).
   URL state: #map=YYYYMMDDHH&layers=h,f,a&sel=id,id — the app's own keys
   ride along. Old links redirect on read: #raw=→#map=, #global=→#sel=.
   qualified_at is added by the ewb lane; until it lands, boxes fall back
   to the event `start` day at 00Z and one console.warn says so.
   Data: data/results/raw_exceedance/{index.json,<ts>.bin} (zlib, layer-major
   bit-packed rows heat/freeze/ar, row0=lat0, MSB-first) and
   data/results/global_timeline.json (events[]: id,type,title,start,end,bbox,
   grid,day,days,masks[,qualified_at]).
   Reduced motion: playback is frame-stepping only; nothing auto-plays.
   ========================================================================== */
(function () {
  'use strict';

  const PAD = 12;
  const HIT_MIN = 44;                      // min touch target (px)
  const LAYERS = [                         // draw order bottom→top
    { key: 'h', name: 'heat',   label: 'Heat > P85' },
    { key: 'f', name: 'freeze', label: 'Freeze < P15' },
    { key: 'a', name: 'ar',     label: 'AR mask', tip: 'AR mask (EWB, before cyclone screen): the raw upstream detector output. The event list additionally screens out tropical cyclones (IBTrACS).' },
  ];
  const TYPES = {
    heat_wave:          { css: 'heat',   short: 'Heat' },
    freeze:             { css: 'freeze', short: 'Freeze' },
    atmospheric_river:  { css: 'ar',     short: 'AR' },
  };
  const LANE_ORDER = ['heat_wave', 'freeze', 'atmospheric_river'];
  const SPEEDS = [2, 5, 10];               // fps choices

  let raw = null;                          // raw_exceedance index.json
  let tl = null;                           // global_timeline.json (null = unavailable)
  let idx = 0;                             // current frame index
  let on = { h: true, f: true, a: true };  // layer toggles
  const sel = new Set();                   // selected event ids (multi-highlight)
  let mounted = false;
  let playing = null, speedI = 1;
  let land = null, graticule = null, proj = null, path = null;
  let fit = null;                          // {W,H}
  const frameCache = new Map();            // ts -> Uint8Array | null
  const inflight = new Map();              // ts -> Promise (one fetch per frame; prefetch+paint share it)
  const CACHE_MAX = 7;

  const $ = (s) => document.querySelector(s);
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (t, attrs, parent) => {
    const n = document.createElementNS(NS, t);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };

  /* ---------------- time helpers ---------------- */
  const tsToDate = (ts) => new Date(Date.UTC(+ts.slice(0, 4), +ts.slice(4, 6) - 1,
    +ts.slice(6, 8), +ts.slice(8, 10)));
  const tsToStr = (d) => '' + d.getUTCFullYear()
    + String(d.getUTCMonth() + 1).padStart(2, '0')
    + String(d.getUTCDate()).padStart(2, '0')
    + String(d.getUTCHours()).padStart(2, '0');
  const urlLayers = () => {                // positive list, only when not all-on
    const k = LAYERS.filter((L) => on[L.key]).map((L) => L.key);
    return k.length === 3 ? null : k.join(',');
  };
  function fmtReadout(ts) {
    const d = tsToDate(ts);
    const o = (opt) => d.toLocaleDateString('en-US', { ...opt, timeZone: 'UTC' });
    return `Valid time: ${o({ day: '2-digit' })} ${o({ month: 'short' })} `
      + `${o({ year: 'numeric' })}, ${String(d.getUTCHours()).padStart(2, '0')}:00 UTC`;
  }
  const fmtShort = (d) => {
    const iso = /^\d{10}$/.test(d) ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d.slice(0, 10);
    return new Date(iso + 'T00:00:00Z')
      .toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  };

  /* ---- event instants (contract: qualified_at — compact 'YYYYMMDDHH' as the
     ewb builder emits, ISO also accepted; fallback: event `start` day at 00Z) ---- */
  let qualWarned = false;
  function qualTs(ev) {                    // 'YYYYMMDDHH', comparable with frames
    const q = ev.qualified_at;
    if (q) {
      const s = String(q);
      if (/^\d{10}$/.test(s)) return s;                        // ewb contract form
      const z = s.replace(/[-:]/g, '').replace('Z', '');       // 2026-08-05T06:00:00Z → 20260805T060000
      if (z.length >= 13) return z.slice(0, 8) + z.slice(9, 11);  // date digits + hour after 'T'
    }
    if (!qualWarned) { qualWarned = true;
      console.warn('global_timeline events lack qualified_at — boxes fall back to the event start day'); }
    return ev.start.slice(0, 4) + ev.start.slice(5, 7) + ev.start.slice(8, 10) + '00';
  }
  const endTs = (ev) => {
    const s = String(ev.end);
    if (/^\d{10}$/.test(s)) return s;
    return s.slice(0, 4) + s.slice(5, 7) + s.slice(8, 10) + (s.slice(11, 13) || '00');
  };
  function activeEvents() {                // visible at the current frame
    if (!tl) return [];
    const ts = raw.frames[idx], tab = pageTab();
    return tl.events.filter((e) => qualTs(e) <= ts && ts < endTs(e)
      && (tab === 'all' || e.type === tab));
  }
  // type tabs of the page (state is a top-level const in app.js — bare identifier)
  const pageTab = () => (typeof state !== 'undefined' && state.tab) || 'all';
  function jumpTo(tsStr) {                 // first frame >= ts (clamped)
    let i = raw.frames.findIndex((f) => f >= tsStr);
    if (i < 0) i = raw.frames.length - 1;
    if (i !== idx) { idx = i; return true; }
    return false;
  }

  /* ---------------- frame load ---------------- */
  const rawLast = () => (raw.cutoff && raw.cutoff >= raw.frames[0] && raw.cutoff <= raw.frames[raw.frames.length - 1]
    ? raw.cutoff : raw.frames[raw.frames.length - 1]);   // last ts with a .bin on disk
  function getFrame(i) {                   // -> Promise<Uint8Array|null>
    const ts = raw.frames[i];
    if (!ts) return Promise.resolve(null);
    if (ts > rawLast()) return Promise.resolve(null);    // past ERA5 cutoff: no frames, boxes only
    if (frameCache.has(ts)) return Promise.resolve(frameCache.get(ts));
    if (inflight.has(ts)) return inflight.get(ts);   // ponytail 20:35: one fetch per frame, ever
    const p = fetch(`data/results/raw_exceedance/${ts}.bin`, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
      .then(async (buf) => {
        const ds = new DecompressionStream('deflate');
        const stream = new Blob([buf]).stream().pipeThrough(ds);  // ArrayBuffer has no pipeThrough
        const u8 = new Uint8Array(await new Response(stream).arrayBuffer());
        if (frameCache.size >= CACHE_MAX) frameCache.delete(frameCache.keys().next().value);
        frameCache.set(ts, u8);
        return u8;
      })
      .catch((err) => {
        // transient failure (mid-publish .bin, brief blip): warn and return
        // null WITHOUT caching — a miss-cache here would gray the step out
        // for the whole session even after the frame lands (ponytail 19:15)
        console.warn('raw frame unavailable:', ts, err.message);
        return null;
      })
      .finally(() => inflight.delete(ts));
    inflight.set(ts, p);
    return p;
  }
  const prefetch = (i) => { [i - 1, i + 1].forEach((j) => {
    if (raw && j >= 0 && j < raw.frames.length) getFrame(j).catch(() => {}); }); };

  /* ---------------- projection / fit ---------------- */
  function fitProjection() {
    const wrap = $('#raw-map-wrap');
    const W = Math.max(240, wrap.clientWidth);
    proj = d3.geoEqualEarth();
    path = d3.geoPath(proj.scale(1).translate([0, 0]));
    const [[x0, y0], [x1, y1]] = path.bounds({ type: 'Sphere' });
    const dw = x1 - x0, dh = y1 - y0;
    const H = Math.round(Math.min((dh / dw) * (W - 2 * PAD) + 2 * PAD, W * 0.75));
    proj.fitExtent([[PAD, PAD], [W - PAD, H - PAD]], { type: 'Sphere' });
    path = d3.geoPath(proj);
    fit = { W, H };
    lut = null;                                    // rebuilt lazily (buildLut)
    const cv = $('#raw-canvas');
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cv.style.width = W + 'px'; cv.style.height = H + 'px';
    wrap.style.minHeight = H + 'px';
    const bx = $('#gm-boxes');                     // overlay tracks the canvas exactly
    bx.setAttribute('width', W); bx.setAttribute('height', H);
    bx.setAttribute('viewBox', `0 0 ${W} ${H}`);
  }

  function drawBase(ctx) {
    const dpr = window.devicePixelRatio || 1;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, fit.W, fit.H);
    const cs = getComputedStyle(document.documentElement);
    const sphere = new Path2D(path({ type: 'Sphere' }));
    ctx.fillStyle = cs.getPropertyValue('--map-ocean').trim() || '#f2f1e6';
    ctx.fill(sphere);
    ctx.strokeStyle = cs.getPropertyValue('--map-line').trim() || '#999';
    ctx.lineWidth = 0.8;                            // design rider 3: map-line ink
    ctx.stroke(sphere);
    ctx.lineWidth = 0.5;
    ctx.save();
    ctx.globalAlpha = 0.18;                        // graticule opacity, gm parity
    ctx.stroke(new Path2D(path(graticule)));
    ctx.restore();
    if (land) {
      ctx.fillStyle = cs.getPropertyValue('--paper').trim() || '#e8e8e8';
      ctx.strokeStyle = cs.getPropertyValue('--map-line').trim() || '#bbb';
      ctx.lineWidth = 0.6;                          // coastlines readable on both oceans
      ctx.fill(new Path2D(path(land)));
      ctx.stroke(new Path2D(path(land)));
    }
    ctx.restore();
  }

  /* ---------------- raster cells ----------------
     Inverse-mapped per DEVICE pixel through a LUT built once per size (Equal
     Earth is pseudocylindrical: one screen row = one latitude, longitude
     linear in x). One pixel → one cell: no strip overlaps, no scanline
     stripes, clean date-line wrap, pole rows blank. */
  let lut = null, rasterCv = null, packedCache = null;   // packed colours: one cache per theme
  function buildLut() {
    const g = raw.grid, nx = g.nx, ny = g.ny, stride = Math.ceil(nx / 8);
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(fit.W * dpr), ch = Math.round(fit.H * dpr);
    const byteIdx = new Int32Array(cw * ch).fill(-1);
    const bitMask = new Uint8Array(cw * ch);
    const cx = proj.translate()[0];
    // d3's invert CLAMPS outside the sphere — bound the rows, leave pole rows blank
    const yTop = proj([0, 90])[1], yBot = proj([0, -90])[1];
    for (let y = 0; y < ch; y++) {
      const sy = (y + 0.5) / dpr;
      if (sy < yTop || sy > yBot) continue;
      const ll = proj.invert([cx, sy]);
      if (!ll || !Number.isFinite(ll[1]) || Math.abs(ll[1]) > 90) continue;
      const lat = ll[1];
      const j = Math.round((lat - g.lat0) / g.step_lat);     // cell CENTRES
      if (j <= 0 || j >= ny - 1) continue;                    // pole rows = replicated point
      const kx = proj([1, lat])[0] - proj([0, lat])[0];       // screen px per lon degree
      if (!(kx > 0)) continue;
      const rowOff = j * stride, o = y * cw;
      for (let x = 0; x < cw; x++) {
        const lon = ((x + 0.5) / dpr - cx) / kx;
        if (lon < -180 || lon > 180) continue;
        let i = Math.round((lon - g.lon0) / g.step_lon);
        i = ((i % nx) + nx) % nx;                             // +180 wraps onto -180
        byteIdx[o + x] = rowOff + (i >> 3);
        bitMask[o + x] = 1 << (7 - (i & 7));
      }
    }
    lut = { cw, ch, byteIdx, bitMask };
  }
  function rgbOf(v) {
    const p = document.createElement('span');
    p.style.color = v; document.body.appendChild(p);
    const m = getComputedStyle(p).color.match(/\d+/g) || [128, 128, 128];
    p.remove();
    return [+m[0], +m[1], +m[2]];
  }
  function paintFrame(u8) {
    const cv = $('#raw-canvas');
    const ctx = cv.getContext('2d');
    drawBase(ctx);
    if (!u8 || !raw || !fit) return;
    if (!lut || lut.cw !== cv.width || lut.ch !== cv.height) buildLut();
    const g = raw.grid, plane = g.ny * Math.ceil(g.nx / 8);
    const { cw, ch, byteIdx, bitMask } = lut;
    if (!rasterCv || rasterCv.width !== cw || rasterCv.height !== ch) {
      rasterCv = document.createElement('canvas');
      rasterCv.width = cw; rasterCv.height = ch;
    }
    const rctx = rasterCv.getContext('2d');
    const img = rctx.createImageData(cw, ch);
    const px = new Uint32Array(img.data.buffer);
    // ABGR little-endian packed colour per layer (alpha 215/255). rgbOf forces a
    // style flush per span — too heavy for the paint loop, so memoize per theme
    // (the data-theme observer invalidates)
    if (!packedCache) {
      const cs = getComputedStyle(document.documentElement);
      packedCache = LAYERS.map((L) => {
        const [r, gc, b] = rgbOf(cs.getPropertyValue(`--series-${L.name}`));
        return ((215 << 24) | (b << 16) | (gc << 8) | r) >>> 0;
      });
    }
    const packed = packedCache;
    const act = LAYERS.map((L, li) => (on[L.key] ? li : -1)).filter((li) => li >= 0);
    const n = cw * ch;
    for (let p = 0; p < n; p++) {
      const bi = byteIdx[p];
      if (bi < 0) continue;
      const m = bitMask[p];
      for (let a = 0; a < act.length; a++) {                  // later layers paint over earlier
        const li = act[a];
        if (u8[li * plane + bi] & m) px[p] = packed[li];
      }
    }
    rctx.putImageData(img, 0, 0);
    ctx.save();
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    ctx.drawImage(rasterCv, 0, 0, fit.W, fit.H);
    ctx.restore();
  }

  /* ---------------- event boxes (SVG overlay, same projection) ---------------- */
  const FULL_BAND_SPAN = 300;
  function bboxSpan(b) { return b[3] >= b[2] ? b[3] - b[2] : b[3] + 360 - b[2]; }
  function isFullBand(b) { return Array.isArray(b) && b[3] >= b[2] && bboxSpan(b) >= FULL_BAND_SPAN; }
  function bboxPaths(ev) {
    // bbox [lat_min, lat_max, lon_min, lon_max]; wrap → two rects (catalog parity)
    const [s, n, w, e] = ev.bbox;
    if (isFullBand(ev.bbox)) return '';        // degenerate band: never paint world-wide
    const rect = (wa, ea) => {
      const tl = proj([wa, n]), tr = proj([ea, n]), br = proj([ea, s]), bl = proj([wa, s]);
      return `M${tl[0].toFixed(1)} ${tl[1].toFixed(1)}L${tr[0].toFixed(1)} ${tr[1].toFixed(1)}`
        + `L${br[0].toFixed(1)} ${br[1].toFixed(1)}L${bl[0].toFixed(1)} ${bl[1].toFixed(1)}Z`;
    };
    return w <= e ? rect(w, e) : rect(w, 180) + rect(-180, e);
  }
  /* Antimeridian safety net (port of GlobalDash): a producer that recorded
     min/max of raw wrapped longitudes yields a ~whole-band box; recover the
     true wrapped box from the footprint masks (widest circular empty gap). */
  function recoverBbox(ev) {
    if (!ev.grid || !ev.masks || !ev.masks.length) return null;
    const { nx, ny, lon0, step_lon } = ev.grid;
    const cols = new Uint8Array(nx);
    for (let d = 0; d < ev.masks.length; d++) {
      const bin = atob(ev.masks[d]);
      for (let k = 0; k < ny * nx; k++) {
        if ((bin.charCodeAt(k >> 3) >> (7 - (k & 7))) & 1) cols[k % nx] = 1;
      }
    }
    if (!cols.some((c) => c)) return null;
    const norm = (l) => ((l + 180) % 360 + 360) % 360 - 180;
    if (nx * step_lon < FULL_BAND_SPAN) {      // regional grid: occupied columns ARE the box
      const f = cols.indexOf(1), l = cols.lastIndexOf(1);
      return [ev.bbox[0], ev.bbox[1], norm(lon0 + f * step_lon), norm(lon0 + (l + 1) * step_lon)];
    }
    let best = 0, bestEnd = -1, run = 0;
    for (let k = 0; k < 2 * nx; k++) {
      if (!cols[k % nx]) { run++; if (run > best && run < nx) { best = run; bestEnd = k % nx; } } else run = 0;
    }
    if (best * step_lon < 20) return null;     // genuinely wide event: keep the bbox
    const first = (bestEnd + 1) % nx, last = (bestEnd - best + nx) % nx;
    return [ev.bbox[0], ev.bbox[1], norm(lon0 + first * step_lon), norm(lon0 + (last + 1) * step_lon)];
  }
  function repairBboxes() {
    for (const ev of tl.events) {
      if (!isFullBand(ev.bbox)) continue;
      const fix = recoverBbox(ev);
      if (fix) { ev.bbox = fix;
        if (typeof window.applyBboxFix === 'function') window.applyBboxFix(ev.id, fix); }
    }
  }

  function openEvent(id) {                     // open detail through the app's hash sync
    try {
      const p = Object.assign(hashParams(), urlParams(), { event: id });
      delete p.lead;
      location.hash = Object.entries(p).map(([a, b]) => `${a}=${encodeURIComponent(b)}`).join('&');
    } catch (e) {
      if (typeof selectEvent === 'function') selectEvent(id, true);
    }
  }

  /* keyboard focus survives a rebuild: every toggle re-renders by rebuilding
     nodes, which would drop focus to <body> (guardian keyboard contract) */
  function focusedBoxId() {
    const a = document.activeElement;
    const n = a && a.closest && a.closest('#gm-boxes [data-id]');
    return n ? n.getAttribute('data-id') : null;
  }

  function toggleSel(id) {
    if (sel.has(id)) sel.delete(id); else sel.add(id);
    writeUrl(true); render();
  }

  function renderBoxes() {
    const g = $('#gm-boxes');
    const keepFocus = focusedBoxId();
    g.innerHTML = '';
    if (!fit || !tl) return;
    const ts = raw.frames[idx];
    const active = activeEvents().slice();
    active.sort((a, b) => (a.bbox[1] - a.bbox[0]) * spanLon(a.bbox)
      - (b.bbox[1] - b.bbox[0]) * spanLon(b.bbox));      // big footprints under small
    const anySel = sel.size > 0;
    for (const ev of active) {
      const t = TYPES[ev.type] || { css: 'heat' };
      const cls = 'gm-ev' + (sel.has(ev.id) ? ' sel' : '') + (anySel && !sel.has(ev.id) ? ' dim' : '');
      const node = mk('g', {
        class: cls, 'data-id': ev.id, role: 'button', tabindex: 0,
        'aria-pressed': sel.has(ev.id) ? 'true' : 'false',
        'aria-label': `${ev.title}: ${fmtShort(ev.start)} – ${fmtShort(ev.end)}`,
      }, g);
      const bd = bboxPaths(ev);
      if (!bd) { node.remove(); continue; }
      mk('title', {}, node).textContent =
        `${ev.title} — qualified ${fmtShort(qualTs(ev))} → ${fmtShort(ev.end)}`;
      if (sel.has(ev.id)) mk('path', { class: 'gm-halo', d: bd }, node);
      mk('path', { class: `gm-bbox t-${t.css}`, d: bd,
                   style: `stroke: var(--series-${t.css}); fill: var(--series-${t.css})` }, node);
      let hb = null;
      try { hb = node.getBBox(); } catch (e) { /* detached in tests */ }
      if (hb && hb.width >= 0) {
        const hw = Math.max(hb.width, HIT_MIN), hh = Math.max(hb.height, HIT_MIN);
        const hx = hb.x + hb.width / 2 - hw / 2, hy = hb.y + hb.height / 2 - hh / 2;
        const OPEN_W = 48;
        // open button only when the box can hold it without eating the hit area;
        // small boxes open via keyboard Enter-twice, the gantt bar, or the table row
        const showOpen = hw >= OPEN_W * 2;
        const hit = mk('rect', { class: 'gm-hit', x: hx, y: hy,
                   width: showOpen ? hw - OPEN_W : hw, height: hh,
                   fill: 'transparent', 'pointer-events': 'all' }, node);
        // hit rect stops the click: an overlapping larger box UNDER this one must
        // not catch it through DOM bubbling (only the rect/path of the top hit toggles)
        hit.addEventListener('click', (e) => { e.stopPropagation(); toggleSel(ev.id); });
        // explicit open affordance inside the box (select ≠ open; the table row opens).
        // plain SVG text, not an <button>: a focusable HTML button inside the
        // role=button group is axe nested-interactive / aria-hidden-focus
        if (showOpen) {
          const og = mk('g', { class: 'gm-open-wrap', 'aria-hidden': 'true' }, node);
          mk('rect', { class: 'gm-open-pill', x: hx + hw - OPEN_W + 2, y: hy + 1,
                       width: OPEN_W - 4, height: 16, rx: 3 }, og);
          const t = mk('text', { class: 'gm-open', x: hx + hw - OPEN_W / 2, y: hy + 12,
                                 'text-anchor': 'middle' }, og);
          t.textContent = 'open';
          og.addEventListener('click', (e) => { e.stopPropagation(); openEvent(ev.id); });
          // axe label-content-name-mismatch: visible text must live inside the
          // group's accessible name — the 'open' hint is visible, so name it
          node.setAttribute('aria-label', `${ev.title}: ${fmtShort(ev.start)} – ${fmtShort(ev.end)} open`);
        }
      }
      node.addEventListener('click', () => toggleSel(ev.id));
      node.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          // guardian contract: Enter toggles selection and KEEPS focus (render
          // re-asserts it); a second Enter on an already-selected box is the
          // keyboard equivalent of the in-box open pill
          if (sel.has(ev.id)) openEvent(ev.id); else toggleSel(ev.id);
        }
      });
      node.addEventListener('pointerenter', () => node.classList.add('hov'));
      node.addEventListener('pointerleave', () => node.classList.remove('hov'));
    }
    if (keepFocus) {
      const t = g.querySelector(`[data-id="${CSS.escape(keepFocus)}"]`);
      if (t) t.focus({ preventScroll: true });
    }
    const nHeat = active.filter((e) => e.type === 'heat_wave').length;
    const nFrz = active.filter((e) => e.type === 'freeze').length;
    const nAr = active.filter((e) => e.type === 'atmospheric_river').length;
    const parts = [];
    if (nHeat) parts.push(`${nHeat} heat`);
    if (nFrz) parts.push(`${nFrz} freeze`);
    if (nAr) parts.push(`${nAr} AR`);
    $('#raw-canvas').setAttribute('aria-label',
      `Equal Earth world map — raw detector layers and event boxes: `
      + `${active.length} event${active.length === 1 ? '' : 's'} visible`
      + (parts.length ? ` (${parts.join(', ')})` : ''));
  }
  const spanLon = (b) => (b[2] <= b[3] ? b[3] - b[2] : 360);

  /* ---------------- Gantt strip (qualified → end, shared time scale) ---------------- */
  function renderGantt() {
    const svg = $('#gm-gantt');
    svg.innerHTML = '';
    if (!tl) return;
    const W = Math.max(320, $('#raw-map-wrap').clientWidth);
    const LANE_H = 12, GAP = 4, LAB = 44, TOP = 2, AX = 16;
    const H = TOP + LANE_H * 3 + GAP * 2 + AX;
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const t0 = tsToDate(raw.frames[0]).getTime();
    const t1 = tsToDate(raw.frames[raw.frames.length - 1]).getTime();
    const x = (ms) => LAB + Math.min(1, Math.max(0, (ms - t0) / (t1 - t0))) * (W - LAB - 12);  // trailing 12 = slider thumb inset
    const anySel = sel.size > 0;
    const tab = pageTab();
    for (const type of LANE_ORDER) {
      const li = LANE_ORDER.indexOf(type);
      const y = TOP + li * (LANE_H + GAP);
      const lab = mk('text', { x: 0, y: y + LANE_H - 3, class: 'gm-lane-lab' }, svg);
      lab.textContent = TYPES[type].short;
      mk('rect', { class: 'gm-lane', x: LAB, y, width: W - LAB - 12, height: LANE_H, rx: 2, opacity: .35 }, svg);
      for (const ev of tl.events) {
        if (ev.type !== type) continue;
        const q = qualTs(ev), e = endTs(ev);
        if (q > raw.frames[raw.frames.length - 1] || e < raw.frames[0]) continue;   // clamped out
        const bx = x(tsToDate(q).getTime()), be = x(tsToDate(e).getTime());
        const bw = Math.max(2, be - bx);
        const isSel = sel.has(ev.id);
        const bar = mk('rect', {
          class: 'gm-bar', 'data-id': ev.id, x: bx, y, width: bw, height: LANE_H, rx: 2,
          style: `fill: var(--series-${TYPES[type].css})`,
          opacity: isSel ? 1 : (anySel ? .18 : (tab !== 'all' && tab !== type ? .18 : .8)),
          stroke: isSel ? 'var(--canvas)' : 'none', 'stroke-width': isSel ? 1 : 0,
          cursor: 'pointer',
        }, svg);
        mk('title', {}, bar).textContent = `${ev.title} — qualified ${fmtShort(q)}`;
        bar.addEventListener('click', () => {
          if (!sel.has(ev.id)) sel.add(ev.id); else sel.delete(ev.id);
          jumpTo(q);
          writeUrl(true); render();
        });
      }
    }
    // cursor: the current frame (G-23 lineage), 1px ink line spanning the lanes
    const cx = x(tsToDate(raw.frames[idx]).getTime());
    mk('line', { x1: cx, x2: cx, y1: 0, y2: H - AX + 3,
                 style: 'stroke: var(--matte-black); stroke-width: 1; pointer-events: none' }, svg);
    let lastLab = -1e9;
    const d = new Date(t0);
    d.setUTCDate(d.getUTCDate() + 1); d.setUTCHours(0, 0, 0, 0);
    for (; d.getTime() <= t1; d.setUTCDate(d.getUTCDate() + 1)) {
      if (d.getUTCDate() !== 1) continue;
      mk('line', { class: 'gm-tick', x1: x(d.getTime()), x2: x(d.getTime()), y1: TOP, y2: H - AX + 3 }, svg);
      if (x(d.getTime()) - lastLab < 34) continue;
      lastLab = x(d.getTime());
      const t = mk('text', { x: x(d.getTime()) + 2, y: H - 3 }, svg);
      t.textContent = d.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
    }
  }

  /* ---------------- render + controls ---------------- */
  let renderSeq = 0;
  function render() {
    if (!mounted || !raw) return;
    const seq = ++renderSeq;
    const ts = raw.frames[idx];
    $('#raw-slider').value = String(idx);
    $('#raw-slider').setAttribute('aria-valuetext', fmtReadout(ts));
    $('#raw-readout').textContent = fmtReadout(ts);
    const past = ts > rawLast();
    const badge = $('#raw-badge');
    badge.classList.toggle('hidden', !past);   // .sec-badge sets display; class hides it
    // design-recommended copy (19:05): honest end-of-frames marker, not an IFS claim
    badge.textContent = past ? `raw layers end ${fmtShort(raw.cutoff)} ${raw.cutoff.slice(8, 10)}Z · ERA5 lag` : '';
    renderBoxes();
    renderGantt();
    getFrame(idx).then((u8) => {
      if (seq !== renderSeq) return;
      const note = $('#raw-note');
      if (u8 === null && ts && !past) {
        note.classList.remove('hidden');
        note.textContent = `Frame ${fmtReadout(ts)} is not available yet — `
          + 'the raw exceedance build has not produced it.';
        paintFrame(null);
      } else {
        note.classList.add('hidden');
        paintFrame(u8);
      }
      prefetch(idx);
    });
  }

  function stepPlay() {
    if (idx >= raw.frames.length - 1) { stopPlay(); return; }
    idx++;
    writeUrl(false);
    render();
  }
  function stopPlay() {
    if (playing) { clearInterval(playing); playing = null; }
    const b = $('#raw-play');
    b.textContent = '▶ play'; b.setAttribute('aria-label', 'Play raw frames');
  }
  function togglePlay() {
    if (playing) { stopPlay(); return; }
    if (idx >= raw.frames.length - 1) { idx = 0; render(); }   // show frame 0 now, not one tick later
    playing = setInterval(stepPlay, Math.round(1000 / SPEEDS[speedI]));
    const b = $('#raw-play');
    b.textContent = '⏸ pause'; b.setAttribute('aria-label', 'Pause raw frames');
  }

  function writeUrl(push) {
    if (!mounted || !raw) return;
    try {
      const p = hashParams();
      delete p.raw; delete p.global; delete p.date;    // old keys never survive a rewrite
      delete p.layers; delete p.sel;                   // re-added below only when non-default
      const u = locationFor(Object.assign(p, urlParams()));
      if (push) history.pushState({ event: state.selectedEvent || null }, '', u);
      else history.replaceState({ event: state.selectedEvent || null }, '', u);
    } catch (e) { /* file:// */ }
  }

  function urlParams() {
    if (!mounted || !raw) return {};
    const p = { map: raw.frames[idx] };
    const lay = urlLayers();
    if (lay) p.layers = lay;
    if (sel.size) p.sel = Array.from(sel).join(',');
    return p;
  }

  function applyUrl(params) {
    if (!mounted || !raw) return false;
    let changed = false;
    let want = params.map || params.raw;          // old #raw= links redirect
    if (!want && params.global && params.date) want = params.date.slice(0, 10).replace(/-/g, '') + '00';  // old #global=&date= → map at that day
    if (want) {
      const w = raw.frames.indexOf(want);
      if (w >= 0 && w !== idx) { idx = w; changed = true; }
    }
    if ('layers' in params) {                        // positive list of ON layers
      const keep = new Set((params.layers || '').split(',').filter(Boolean));
      for (const L of LAYERS) { const v = keep.has(L.key); if (on[L.key] !== v) { on[L.key] = v; changed = true; } }
      if (!on.h && !on.f && !on.a) on.h = on.f = on.a = true;
      renderLayerButtons();
    }
    const s = params.sel || params.global;           // old #global= links redirect
    if (s !== undefined) {
      const ids = s.split(',').filter(Boolean);
      if (ids.join(',') !== Array.from(sel).join(',')) { sel.clear(); ids.forEach((i) => sel.add(i)); changed = true; }
    }
    if (changed) render();
    return changed;
  }

  function renderLayerButtons() {
    const box = $('#raw-layers');
    box.innerHTML = '';
    for (const L of LAYERS) {
      const b = document.createElement('button');
      b.className = 'gm-chip'; b.type = 'button';
      b.dataset.layer = L.key;
      if (L.tip) b.title = L.tip;
      b.setAttribute('aria-pressed', on[L.key] ? 'true' : 'false');
      b.innerHTML = `<i style="background: var(--series-${L.name})"></i>${L.label}`;
      b.onclick = () => {
        on[L.key] = !on[L.key];
        if (!on.h && !on.f && !on.a) on[L.key] = true;   // never all-off (no blank map)
        renderLayerButtons();
        writeUrl(false);
        paintFrame(frameCache.get(raw.frames[idx]) || null);
      };
      box.appendChild(b);
    }
  }

  /* ---------------- boot ---------------- */
  function fail(msg) {
    $('#raw-skel').hidden = true;
    const note = $('#raw-note');
    note.classList.remove('hidden');
    note.textContent = msg;
  }

  async function mount() {
    if (mounted) return;
    const slider = $('#raw-slider');
    $('#raw-play').onclick = togglePlay;
    $('#raw-prev').onclick = () => { stopPlay(); if (idx > 0) { idx--; writeUrl(false); render(); } };
    $('#raw-next').onclick = () => { stopPlay(); if (idx < (raw && raw.frames.length - 1)) { idx++; writeUrl(true); render(); } };
    $('#raw-speed').onchange = (e) => {
      speedI = SPEEDS.indexOf(+e.target.value);
      if (playing) { clearInterval(playing); playing = setInterval(stepPlay, Math.round(1000 / SPEEDS[speedI])); }
    };
    slider.addEventListener('input', () => { stopPlay(); idx = +slider.value; writeUrl(false); render(); });
    // theme flip: the canvas bakes ocean/land/series colours at paint time, so repaint
    new MutationObserver(() => {
      packedCache = null;                             // theme flip: repack colours
      if (raw && fit) paintFrame(frameCache.get(raw.frames[idx]) || null);
    })
      .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    let json = null;
    try {
      const res = await fetch('data/results/raw_exceedance/index.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      json = await res.json();
      if (!json || !Array.isArray(json.frames) || !json.grid) throw new Error('bad payload');
    } catch (err) {
      console.warn('raw exceedance index unavailable:', err.message);
      fail('Raw exceedance data is not built yet — it lands with the weekly run.');
      return;
    }
    // timeline is additive: boxes vanish without it, the layers still work
    try {
      const tres = await fetch('data/results/global_timeline.json', { cache: 'no-cache' });
      if (tres.ok) {
        const t = await tres.json();
        if (t && Array.isArray(t.events)) { tl = t; repairBboxes(); }
      } else console.warn('event timeline unavailable: HTTP ' + tres.status);
    } catch (err) { console.warn('event timeline unavailable:', err.message); }
    try { await _build(json); } catch (err) {
      console.warn('merged map failed:', err.message);
      fail('The global map data could not be rendered for this run.');
    }
  }

  async function _build(json) {
    raw = json;
    // time axis: raw frames, extended 6-hourly to the timeline window end —
    // past the ERA5 cutoff the canvas draws nothing but boxes keep moving
    if (tl && tl.window && tl.window.end) {
      const end = new Date(Date.UTC(+tl.window.end.slice(0, 4), +tl.window.end.slice(5, 7) - 1,
        +tl.window.end.slice(8, 10)));
      const first = new Date(tsToDate(raw.frames[raw.frames.length - 1]).getTime() + 6 * 3600e3);
      for (let t = first; t <= end; t.setUTCHours(t.getUTCHours() + 6)) raw.frames.push(tsToStr(t));
    }
    idx = raw.frames.length - 1;
    if (tl) {                                   // park the hero on the busiest step (ties → latest)
      let bestN = -1;
      for (let i = raw.frames.length - 1; i >= 0; i--) {
        const ts = raw.frames[i];
        const n = tl.events.filter((e) => qualTs(e) <= ts && ts < endTs(e)).length;
        if (n > bestN) { bestN = n; idx = i; }
      }
    }
    const slider = $('#raw-slider');
    slider.max = String(raw.frames.length - 1);
    slider.value = String(idx);
    land = null;
    try {
      const lr = await fetch('geo/land-110m.geojson');
      if (lr.ok) land = await lr.json();
    } catch (e) { /* ocean-only still honest */ }
    graticule = d3.geoGraticule().step([10, 10])();
    if (!land) land = { type: 'FeatureCollection', features: [] };
    mounted = true;
    fitProjection();                          // before any render (paint needs fit)
    renderLayerButtons();
    // URL restore (deep link) before first paint; redirects fold in here
    const boot = hashParams();
    $('#gm-count').textContent = tl ? `${tl.events.length} events · layers every 6 h`
                                    : 'layers every 6 h';
    $('#raw-skel').hidden = true;
    $('#raw-canvas').hidden = false;
    $('#gm-boxes').removeAttribute('hidden');   // SVGElement has no .hidden IDL — attribute only
    applyUrl(boot);                           // renders if anything changed
    if (boot.raw || boot.global || boot.date) writeUrl(false);   // old keys never survive (redirect shim)
    // same-document hash nav (no reload) must strip old keys too
    window.addEventListener('hashchange', () => {
      const p = hashParams();
      if (p.raw || p.global || p.date) writeUrl(false);
    });
    render();                                 // first paint (idempotent with the one above)
    if ('ResizeObserver' in window) {
      let raf = 0;
      new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { if (mounted) { fitProjection(); render(); } });
      }).observe($('#raw-map-wrap'));
    }
  }

  window.MergedMap = { mount, urlParams, applyUrl,
    onTabChange: () => { if (mounted) render(); },
    _dbg: () => ({ idx, frames: raw && raw.frames, on, sel: Array.from(sel), mounted, tl: !!tl }) };
})();
