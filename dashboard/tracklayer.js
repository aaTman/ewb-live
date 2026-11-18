/* ==========================================================================
   tracklayer.js — the TRACK lanes on the one global map (tc-map, 2026-11-18).

   Why this exists: a cyclone is neither a raster lane nor a box. Its evidence
   is a best track, so with 44 published storms the merged map drew ZERO of
   them: global_timeline.json (the only asset the map reads) carried footprint
   lanes only, and dashboard/trackviz.js — the renderer that does draw tracks —
   is wired into the event page only. That is the owner's 2026-11-18 report, and
   it needed both halves fixed: the data asset (ewb_live/viz/timeline.py,
   TRACK_TYPES) and this layer.

   This module is a GENERIC track layer, not a TC widget: every lane whose
   timeline entry carries `track` (the shared 5-slot `details.track` row schema,
   ewb_live/events/tc.py + mlc.py) draws through it with its own colour, ramp,
   bin edges and wording. tropical_cyclone is live today; midlat_cyclone is
   wired here and draws nothing until its lane publishes events — an honest
   empty state, no invented data.

   Visual language = the event page's TrackViz, on the same map grounds:
     polyline   where the storm has been — solid inside the event window, thin
                dashed for the rest of the system's life
     dots       one per fix, filled by intensity bin (validated single-hue
                ramps, --tc-bin-1..5 — never a rainbow)
     ring       the storm's position at the selected step: hover/tap tooltip,
                click selects, Enter selects then a second Enter opens, and the
                selected storm's name label is the click-through to its page
   Time semantics: the frame slider REVEALS the track. A fix appears once the
   selected 6-hourly step reaches it, so playing back never shows the future;
   the ring sits on the last fix at or before the selected step. Before a
   storm's first fix it is simply not on the map.

   Layering: a transparent canvas over the raster canvas (lines + a few thousand
   fix dots — cheap to repaint every step) under a small SVG with ONE
   interactive marker per positioned storm (SVG is the a11y-correct place:
   focusable, labelled, and it stays a few dozen nodes). Both are
   pointer-events:none except the markers, so the event boxes beneath stay
   clickable, and both are sized from the merged map's own fit, so region views,
   resizes and theme flips are the map's business, not ours.

   Antimeridian: a track that crosses 180° is never stroked straight across the
   world. splitAtDateline() breaks the polyline at the seam and lands both ends
   exactly on it (interpolated crossing latitude), so the line leaves one edge
   and re-enters the other. Covered by its own test.

   Seams (both documented at their call sites in mergedmap.js):
     window.onMapTracks(payload)  on every map state change the map owns
     window.onTrackLegend(payload) for the colour-legend strip below the map
   Nothing else knows this module exists; it never touches another module's DOM.
   ========================================================================== */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  /* Marker touch target radius. The map's own HIT_MIN is 44 px (a box can
     spread that wide); 44 px discs in a tropical basin where five storms sit
     within a degree of each other would swallow each other and HIDE storms, so
     the marker is 30 px and keyboard access carries the rest. */
  const HIT_R = 15;

  /* ---------------- lanes ----------------
     A lane = colour + intensity ramp + wording. `strength` maps a fix's
     headline value onto a monotone "how strong" number so ONE ascending bin
     scan serves both directions (wind rises with intensity, pressure falls),
     while the bin labels stay in the lane's own unit — a point's colour and its
     label can then never disagree. */
  const LANES = {
    tropical_cyclone: {
      css: 'tc',
      name: 'Tropical cyclone',
      unit: 'kt',
      binTokens: ['--tc-bin-1', '--tc-bin-2', '--tc-bin-3', '--tc-bin-4', '--tc-bin-5'],
      // Saffir-Simpson bands, merged exactly as dashboard/trackviz.js (BINS)
      // and ewb_live/events/tc.py (CATEGORY_BANDS) merge them — all three agree.
      strength: (kt) => kt,
      bins: [
        { min: 0, lab: 'TD', cat: '<34 kt' },
        { min: 34, lab: 'TS', cat: '34–63 kt' },
        { min: 64, lab: 'C1', cat: '64–82 kt' },
        { min: 83, lab: 'C2–3', cat: '83–112 kt' },
        { min: 113, lab: 'C4–5', cat: '≥113 kt' },
      ],
      headline: 'peak wind',
      pressureLabel: 'min pressure',
      tip: 'Best track of every published storm (IBTrACS). The line is where the '
        + 'storm has been by the selected step, the dots are its 3–6-hourly fixes '
        + 'coloured by maximum sustained wind in the Saffir-Simpson bands, and the '
        + 'ring is its position at that step. Hover or focus a storm for its numbers.',
    },
    midlat_cyclone: {
      css: 'mlc',
      name: 'Midlatitude cyclone',
      unit: 'hPa',
      // The lane publishes no --mlc-bin-* tokens yet, so the ramp falls back to
      // the lane hue at stepped strength — no new hue is invented. Publish the
      // five tokens in styles.css and this picks them up with no code change.
      binTokens: ['--mlc-bin-1', '--mlc-bin-2', '--mlc-bin-3', '--mlc-bin-4', '--mlc-bin-5'],
      // Slot 3 of the mlc track rows IS central MSL pressure (mlc.py: lower is
      // stronger), so strength is the deficit against a benign 1013 hPa.
      strength: (hPa) => 1013 - hPa,
      // Edges chosen here so the lane has a defensible ramp until the mlc lane
      // names its own; its event floor is a centre ≤990 hPa.
      bins: [
        { min: 0, lab: '>985', cat: 'filling' },
        { min: 28, lab: '–985', cat: '985–975 hPa' },
        { min: 38, lab: '–975', cat: '975–965 hPa' },
        { min: 48, lab: '–965', cat: '965–955 hPa' },
        { min: 58, lab: '≤955', cat: 'deep' },
      ],
      headline: 'central pressure',
      pressureLabel: 'min pressure',
      tip: 'Track of every published midlatitude low (closed ERA5 MSLP centres). '
        + 'Dots are 6-hourly central pressures — a deeper low is a stronger low.',
    },
  };

  /* ---------------- small helpers ---------------- */
  const esc = (s) => String(s == null ? '' : s)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;' }[c]));
  const cssVar = (name, dflt) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || dflt;
  };
  const rgbOf = (v) => {
    const p = document.createElement('span');
    p.style.color = v;
    document.body.appendChild(p);
    const m = getComputedStyle(p).color.match(/\d+/g) || [128, 128, 128];
    p.remove();
    return [+m[0], +m[1], +m[2]];
  };
  const fmtDay = (ms) => new Date(ms).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const fmtHour = (ms) => String(new Date(ms).getUTCHours()).padStart(2, '0') + 'Z';
  const round1 = (v) => String(Math.round(v * 10) / 10);
  const UNKNOWN = '#848475';

  /* 'YYYYMMDDHH' — the map's frame key, which is what a timeline track fix
     carries in slot 0 (so the browser compares fixes with a string compare,
     same as qualified_at) — and anything Date understands both land on a ms. */
  function toMs(t) {
    const s = String(t);
    if (/^\d{10}$/.test(s)) {
      return Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10));
    }
    const d = new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
    return isNaN(d) ? null : d.getTime();
  }

  /* ---- intensity → bin → colour ---------------------------------------- */
  function binOf(lane, v) {
    if (!Number.isFinite(v)) return -1;
    const s = lane.strength(v);
    if (!Number.isFinite(s)) return -1;
    let b = 0;
    for (let i = 0; i < lane.bins.length; i++) if (s >= lane.bins[i].min) b = i;
    return b;
  }
  /* Canvas colours are resolved once per theme: reading a custom property is
     cheap, but rgbOf() forces a style flush per call and this runs in the paint
     path. The merged map's theme observer emits a 'theme' payload, which is what
     invalidates the cache. */
  let colCache = null;
  function packColors() {
    const cs = getComputedStyle(document.documentElement);
    const out = {};
    for (const key in LANES) {
      const lane = LANES[key];
      const series = cssVar(`--series-${lane.css}`, UNKNOWN);
      const rgb = rgbOf(series);
      const alpha = [0.42, 0.56, 0.7, 0.85, 1];      // unramped fallback (MLC)
      out[key] = {
        series,
        bins: lane.binTokens.map((tok, i) => {
          const v = cs.getPropertyValue(tok).trim();
          return v || `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha[i]})`;
        }),
      };
    }
    return out;
  }
  const colors = () => (colCache || (colCache = packColors()));
  const binColor = (type, b) => {
    const c = colors()[type];
    return !c || b < 0 ? cssVar('--series-unknown', UNKNOWN) : c.bins[b];
  };

  /* ---------------- geometry: antimeridian-safe polylines ----------------
     Equal Earth is drawn as ONE continuous [-180,180] sheet, so a track that
     crosses 180° must be BROKEN at the dateline, not stroked across the map
     (that draws a line through the whole world). Each part ends exactly on the
     seam at the interpolated crossing latitude and the next starts at the same
     point on the other side, so the line leaves one edge and re-enters the
     other — the same promise the event boxes make with bboxPaths().

     Input: points {lon, lat} with lon in [-180,180] (the repo wrap convention).
     Output: arrays of {lon, lat, src} — `src` is the input object the point
     came from (null for an interpolated seam point), so the caller can map a
     segment straight back to its screen points/fix data. A >180° step is by
     definition a crossing of the seam; a genuine discontinuity (a bad fix, two
     systems stitched together) cannot be told apart from a flat map, and this
     is the most honest thing it can show. */
  function splitAtDateline(pts) {
    const out = [];
    let cur = [];
    for (const q0 of pts) {
      const p = { lon: +q0.lon, lat: +q0.lat, src: q0 };
      if (cur.length) {
        const q = cur[cur.length - 1];
        const d = p.lon - q.lon;
        if (Math.abs(d) > 180) {
          const east = d < 0;                       // eastbound: leaves at +180
          const seam = east ? 180 : -180;
          const pW = p.lon + (east ? 360 : -360);    // p, in q's unwrapped frame
          const f = (seam - q.lon) / (pW - q.lon);
          const latC = q.lat + f * (p.lat - q.lat);
          cur.push({ lon: seam, lat: latC, src: null });
          out.push(cur);
          cur = [{ lon: -seam, lat: latC, src: null }, p];
          continue;
        }
      }
      cur.push(p);
    }
    if (cur.length) out.push(cur);
    return out;
  }

  /* ---------------- module state ---------------- */
  let host = null, cv = null, ctx = null, svg = null;
  let census = {};              // lane type -> Uint8Array of bins on screen now
  let marks = 0;                // markers drawn at the last payload

  function ensureDom() {
    if (svg && svg.isConnected) return true;
    host = document.querySelector('#raw-map-wrap');
    if (!host || typeof d3 === 'undefined' || !d3.geoEqualEarth) return false;
    cv = document.createElement('canvas');
    cv.id = 'gm-tracks';
    cv.setAttribute('aria-hidden', 'true');      // the markers carry the a11y
    svg = document.createElementNS(NS, 'svg');
    svg.id = 'gm-track-marks';
    svg.setAttribute('role', 'group');
    svg.setAttribute('aria-label', 'Cyclone tracks on the selected time step');
    host.appendChild(cv);
    host.appendChild(svg);
    ctx = cv.getContext('2d');
    return true;
  }

  /* ---------------- one storm ----------------
     Fixes are parsed once per timeline object (they never change): each fix
     gets its epoch ms, its live flag (inside the event window, the same rule
     TrackViz uses), its intensity bin and a path index. */
  const prepCache = new WeakMap();
  function prep(ev) {
    let p = prepCache.get(ev);
    if (p) return p;
    const lane = LANES[ev.type];
    const t0 = toMs(ev.start), t1 = toMs(ev.end);
    const rows = (ev.track && Array.isArray(ev.track.fixes)) ? ev.track.fixes : [];
    const fixes = [];
    for (const r of rows) {
      if (!Array.isArray(r) || r.length < 3) continue;
      const ms = toMs(r[0]);
      const lat = +r[1], lon = +r[2];
      if (ms == null || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const v = Number.isFinite(+r[3]) ? +r[3] : null;
      fixes.push({ ms, lat, lon, v, pres: Number.isFinite(+r[4]) ? +r[4] : null,
        bin: binOf(lane, v), live: !!(t0 != null && t1 != null && ms >= t0 && ms < t1),
        i: fixes.length });
    }
    p = { fixes, meta: ev.track || {} };
    prepCache.set(ev, p);
    return p;
  }
  /* The path order IS the chronology (a best track is a sequence), so the
     reveal is a prefix: everything up to the last fix at or before the step. */
  function revealIndex(fixes, frameMs) {
    let k = -1;
    for (let i = 0; i < fixes.length; i++) {
      if (fixes[i].ms <= frameMs) k = i;
      else break;
    }
    return k;
  }

  function layoutStorm(ev, proj, frameMs, ghost) {
    const { fixes } = prep(ev);
    if (!fixes.length) return null;
    const k = ghost ? fixes.length - 1 : revealIndex(fixes, frameMs);
    if (k < 0) return null;
    const shown = fixes.slice(0, k + 1);
    const spot = [];
    for (const f of shown) {
      const p = proj([f.lon, f.lat]);
      spot.push(p && Number.isFinite(p[0]) && Number.isFinite(p[1]) ? p : null);
    }
    // one projection of the seam points, cached by identity
    const parts = { pre: [], live: [] };
    const pass = (pool, key) => {
      const ok = pool.filter((f) => spot[f.i]);
      if (ok.length < 2) return;
      for (const seg of splitAtDateline(ok)) {
        const scr = [];
        for (const q of seg) {
          if (q.src) scr.push(spot[q.src.i]);
          else {
            const p = proj([q.lon, q.lat]);
            if (!p || !Number.isFinite(p[0])) return;   // cannot land on the seam
            scr.push(p);
          }
        }
        if (scr.length > 1) parts[key].push(scr);
      }
    };
    pass(shown.filter((f) => !f.live), 'pre');
    pass(shown.filter((f) => f.live), 'live');
    const last = shown[k];
    let peak = null;
    for (const f of shown) {
      if (!Number.isFinite(f.v)) continue;
      if (!peak || LANES[ev.type].strength(f.v) > LANES[ev.type].strength(peak.v)) peak = f;
    }
    return { ev, fixes, shown, spot, parts, last, peak, ghost: !!ghost };
  }

  /* ---------------- canvas: lines + fix dots ---------------- */
  function drawCanvas(payload, storms) {
    const dpr = window.devicePixelRatio || 1;
    const { W, H } = payload.fit;
    const cw = Math.round(W * dpr), ch = Math.round(H * dpr);
    if (cv.width !== cw || cv.height !== ch) { cv.width = cw; cv.height = ch; }
    cv.style.width = W + 'px';
    cv.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    const anySel = payload.sel.length > 0;
    for (const st of storms) {
      const col = colors()[st.ev.type];
      const base = (st.ghost ? 0.5 : 1)
        * (anySel && !payload.sel.includes(st.ev.id) ? 0.25 : 1);
      ctx.globalAlpha = base;
      // the system's life outside the event window: thin + dashed (TrackViz)
      strokePath(st.parts.pre, col.series, 1.1, [3, 3], 0.45);
      // the event window itself
      strokePath(st.parts.live, col.series, 1.6, null, 0.95);
      for (const f of st.shown) {
        const p = st.spot[f.i];
        if (!p) continue;
        ctx.globalAlpha = base * (f.live ? 1 : 0.5);
        ctx.fillStyle = binColor(st.ev.type, f.bin);
        ctx.beginPath();
        ctx.arc(p[0], p[1], f.live ? 1.9 : 1.2, 0, 6.2832);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }
  function strokePath(parts, color, width, dash, alpha) {
    if (!parts.length) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    if (dash) ctx.setLineDash(dash);
    ctx.globalAlpha *= alpha;
    for (const seg of parts) {
      ctx.beginPath();
      for (let i = 0; i < seg.length; i++) {
        if (i) ctx.lineTo(seg[i][0], seg[i][1]); else ctx.moveTo(seg[i][0], seg[i][1]);
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ---------------- markers (SVG, the interactive part) ---------------- */
  function drawMarks(payload, storms) {
    const { W, H } = payload.fit;
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const act = svg.contains(document.activeElement)
      ? document.activeElement.closest('#gm-track-marks [data-id]') : null;
    const keepId = act ? act.getAttribute('data-id') : null;
    svg.innerHTML = '';
    marks = 0;
    const anySel = payload.sel.length > 0;
    const placed = [];                       // label boxes already written
    // strongest first, so labels go to the storms that matter most
    const rank = (st) => (st.peak && Number.isFinite(st.peak.v)
      ? LANES[st.ev.type].strength(st.peak.v) : -1);
    const live = storms.filter((st) => st.last && !st.ghost)
      .sort((a, b) => rank(b) - rank(a));
    for (const st of live) {
      const p = st.spot[st.last.i];
      if (!p || p[0] < -4 || p[1] < -4 || p[0] > W + 4 || p[1] > H + 4) continue;
      markOne(payload, st, p, placed, anySel);
      marks++;
    }
    if (keepId) {
      const t = svg.querySelector(`[data-id="${CSS.escape(keepId)}"]`);
      if (t) t.focus({ preventScroll: true });
    }
    const names = [...svg.querySelectorAll('[data-name]')].map((n) => n.getAttribute('data-name'));
    svg.setAttribute('aria-label',
      `Cyclone tracks at ${payload.readout || 'the selected time'}: `
      + `${marks} storm${marks === 1 ? '' : 's'} positioned`
      + (names.length && names.length <= 8 ? ` (${names.join(', ')})` : ''));
  }

  const shortTitle = (ev) => {
    // an unnamed system still needs a map label: a short, distinct one
    const d = new Date(toMs(ev.start));
    return isNaN(d) ? String(ev.title || ev.id)
      : 'Storm ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  };
  const stormName = (ev) => (ev.track && ev.track.name) || shortTitle(ev);

  function markOne(payload, st, p, placed, anySel) {
    const ev = st.ev, lane = LANES[ev.type], m = st.last;
    const on = payload.sel.includes(ev.id);
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('class', 'gm-mark' + (on ? ' sel' : '')
      + (anySel && !on ? ' dim' : '') + (payload.previewId === ev.id ? ' hov' : ''));
    g.setAttribute('data-id', ev.id);
    g.setAttribute('data-type', ev.type);
    g.setAttribute('data-name', `${stormName(ev)} (${lane.name.toLowerCase()})`);
    g.setAttribute('role', 'button');
    g.setAttribute('tabindex', '0');
    g.setAttribute('aria-pressed', on ? 'true' : 'false');
    g.setAttribute('aria-label', ariaFor(st, m));
    const hit = document.createElementNS(NS, 'circle');
    hit.setAttribute('class', 'gm-mark-hit');
    hit.setAttribute('cx', p[0].toFixed(1)); hit.setAttribute('cy', p[1].toFixed(1));
    hit.setAttribute('r', HIT_R);
    g.appendChild(hit);
    const ring = document.createElementNS(NS, 'circle');
    ring.setAttribute('class', 'gm-mark-ring');
    ring.setAttribute('cx', p[0].toFixed(1)); ring.setAttribute('cy', p[1].toFixed(1));
    ring.setAttribute('r', on ? 7.5 : 6);
    ring.setAttribute('style', `stroke: var(--series-${lane.css})`);
    g.appendChild(ring);
    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('class', 'gm-mark-dot');
    dot.setAttribute('cx', p[0].toFixed(1)); dot.setAttribute('cy', p[1].toFixed(1));
    dot.setAttribute('r', 3);
    dot.setAttribute('fill', binColor(ev.type, m.bin));
    g.appendChild(dot);
    const lab = labelBox(st, p, payload.fit, placed, on || payload.previewId === ev.id);
    if (lab) {
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('class', 'gm-mark-lab' + (on ? ' is-open' : ''));
      t.setAttribute('x', lab.x.toFixed(1)); t.setAttribute('y', lab.y.toFixed(1));
      t.setAttribute('text-anchor', lab.right ? 'start' : 'end');
      t.setAttribute('style', `fill: var(--series-${lane.css})`);
      t.textContent = stormName(ev);
      g.appendChild(t);
      if (on) {
        // select ≠ open — the map's own contract (an event box has an in-box
        // pill for the same reason): the SELECTED storm's name is the click-through
        t.addEventListener('click', (e) => { e.stopPropagation(); openEvent(ev.id); });
      }
    }
    g.addEventListener('pointerenter', () => { showTip(st, m); hoverRow(ev.id, true); });
    g.addEventListener('pointermove', (e) => { if (window.moveTip) window.moveTip(e); });
    g.addEventListener('pointerleave', () => { hideTip(); hoverRow(ev.id, false); });
    g.addEventListener('click', () => toggleSel(ev.id));
    g.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      // guardian contract: the key acts and keeps focus (a re-render restores
      // it); a second Enter on a selected storm opens its event page
      if (payload.sel.includes(ev.id)) openEvent(ev.id); else toggleSel(ev.id);
    });
    g.addEventListener('focus', () => {
      showTip(st, m);
      const r = g.getBoundingClientRect();
      if (window.moveTip) window.moveTip({ clientX: r.left + r.width / 2, clientY: r.bottom });
    });
    g.addEventListener('blur', hideTip);
    svg.appendChild(g);
  }

  function ariaFor(st, m) {
    const ev = st.ev, lane = LANES[ev.type], t = ev.track;
    const bits = [stormName(ev), lane.name.toLowerCase()];
    if (t.basin) bits.push(t.basin.toLowerCase());
    const first = st.fixes[0], last = st.fixes[st.fixes.length - 1];
    bits.push(`${fmtDay(first.ms)} to ${fmtDay(last.ms)}`);
    if (Number.isFinite(t.peak)) {
      bits.push(`${lane.headline} ${round1(t.peak)} ${lane.unit}${t.peak_label ? ' ' + t.peak_label : ''}`);
    }
    if (Number.isFinite(t.pressure)) {
      bits.push(`${lane.pressureLabel.toLowerCase()} ${round1(t.pressure)} hPa`);
    }
    bits.push(`at the selected step ${Number.isFinite(m.v) ? round1(m.v) + ' ' + lane.unit : 'no value'}`);
    bits.push('Select this storm');
    return bits.join(', ');
  }

  /* One label per storm, to the right unless the marker sits near the right
     edge, and only where nothing is already written: a cheap greedy test
     against the boxes placed so far, with three rows to fall back on. A label
     that cannot fit INSIDE the map is not drawn at all — #gm-track-marks
     overflows visibly (like #gm-boxes), and a name written over the event list
     is worse than no name (the tooltip and the aria-label still carry it). */
  function labelBox(st, p, fit, placed, force) {
    const name = stormName(st.ev);
    if (!name) return null;
    const w = Math.min(110, name.length * 6.4 + 6), h = 11;
    const right = p[0] < fit.W - w - 18;
    const x = p[0] + (right ? 10 : -10);
    if (right ? x + w > fit.W - 4 : x - w < 4) return null;
    let y = p[1] - 7;
    for (let tries = 0; tries < 4; tries++) {
      const box = { x: right ? x : x - w, y: y - h, w, h };
      if (!placed.some((b) => !(box.x + box.w < b.x || b.x + b.w < box.x
        || box.y + box.h < b.y || b.y + b.h < box.y))) {
        placed.push(box);
        return { x, y, right };
      }
      y += 11;
    }
    if (!force) return null;
    placed.push({ x: right ? x : x - w, y: y - h, w, h });
    return { x, y, right };
  }

  /* ---------------- tooltip (the page's single #tooltip) ---------------- */
  const row = (k, v) => `<div class="t-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`;
  function showTip(st, m) {
    const tip = document.querySelector('#tooltip');
    if (!tip) return;
    const ev = st.ev, lane = LANES[ev.type], t = ev.track;
    const first = st.fixes[0], last = st.fixes[st.fixes.length - 1];
    const rows = [];
    if (t.basin) rows.push(row('basin', t.basin));
    rows.push(row('dates', `${fmtDay(first.ms)} → ${fmtDay(last.ms)}`));
    if (Number.isFinite(t.peak)) {
      rows.push(row(lane.headline,
        `${round1(t.peak)} ${lane.unit}${t.peak_label ? ' · ' + t.peak_label : ''}`));
    }
    if (Number.isFinite(t.pressure)) rows.push(row(lane.pressureLabel, `${round1(t.pressure)} hPa`));
    rows.push(row(`at ${fmtDay(m.ms)} ${fmtHour(m.ms)}`, Number.isFinite(m.v)
      ? `${round1(m.v)} ${lane.unit}${lane.bins[m.bin] ? ' · ' + lane.bins[m.bin].cat : ''}`
      : 'no value'));
    rows.push(row('fixes', `${st.fixes.length} · ${lane.unit}`));
    rows.push(row('click', 'select · then its name opens the event'));
    tip.innerHTML = `<div class="t-title">${esc(stormName(ev))} · ${esc(lane.name)}</div>`
      + rows.join('');
    tip.classList.remove('hidden');
  }
  const hideTip = () => {
    const tip = document.querySelector('#tooltip');
    if (tip) tip.classList.add('hidden');
  };

  /* map ↔ list coupling, exactly as the event boxes do it */
  function hoverRow(id, on) {
    const tr = document.querySelector(`#event-table tr[data-id="${CSS.escape(id)}"]`);
    if (tr) tr.classList.toggle('hov', on);
  }
  const toggleSel = (id) => { if (window.MergedMap && window.MergedMap.select) window.MergedMap.select(id); };
  const openEvent = (id) => { if (window.MergedMap && window.MergedMap.open) window.MergedMap.open(id); };

  /* ---------------- legend groups for the map's colour strip ----------------
     A lane's group appears ONLY when that lane has published events in the
     window: with nothing published there is no colour on the map to explain,
     and the honest zero belongs to the lane's own tab and event list (this is
     also what keeps the raster-only fixtures' legend contract intact). Stops
     the current step does not show are dimmed, same rule as the raster lanes. */
  function legendHtml(payload) {
    const tab = payload.tab || 'all';
    const groups = [];
    for (const type in LANES) {
      if (tab !== 'all' && tab !== type) continue;
      const lane = LANES[type];
      const n = (payload.events || []).filter((e) => e.type === type && e.track).length;
      if (!n) continue;
      const cen = census[type];
      const seen = cen ? cen.reduce((a, b) => a + b, 0) : -1;
      const stops = [];
      lane.bins.forEach((b, i) => {
        stops.push(`<span class="lg-bin${cen && !cen[i] ? ' out' : ''}">`
          + `<i class="swatch" style="background:${binColor(type, i)}" `
          + `title="${esc(`${b.lab} ${lane.unit} — ${b.cat}`)}"></i>`
          + `<span class="lg-t">${esc(b.lab)}</span></span>`);
      });
      stops.push(`<span class="lg-bin wide"><i class="swatch lg-line" `
        + `style="background:var(--series-${lane.css})" title="The line is the best `
        + `track; solid inside the event window, dashed for the rest of the system's `
        + `life. The ring is the storm's position at the selected step."></i>`
        + `<span class="lg-t">track</span></span>`);
      groups.push(`<span class="lg-group${seen === 0 ? ' none' : ''}" data-track="${type}" `
        + `title="${esc(lane.tip)}">`
        + `<span class="lg-head"><span class="lg-name">${esc(lane.name)}</span>`
        + `<span class="lg-unit">${esc(lane.unit)} · ${n} published</span>`
        + (seen === 0 ? '<span class="lg-none">none at this step</span>' : '') + '</span>'
        + `<span class="lg-stops">${stops.join('')}</span></span>`);
    }
    return groups.join('');
  }

  /* ---------------- the seam the merged map calls ---------------- */
  function onMapTracks(payload) {
    if (!payload || !payload.proj || !payload.fit || !ensureDom()) return;
    if (payload.reason === 'theme') colCache = null;
    census = {};
    if (!payload.mounted) {
      ctx.clearRect(0, 0, cv.width, cv.height);
      svg.innerHTML = '';
      marks = 0;
      return;
    }
    const frameMs = toMs(payload.ts);
    const tab = payload.tab || 'all';
    const evs = (payload.events || []).filter((e) => e.track && LANES[e.type]
      && (tab === 'all' || tab === e.type));
    const storms = [];
    for (const ev of evs) {
      const st = layoutStorm(ev, payload.proj, frameMs, false);
      if (st) storms.push(st);
    }
    // list-hover preview of a storm that is not on the map at this step: outline
    // its whole life, exactly the ghost an event box draws
    if (payload.previewId && !storms.some((s) => s.ev.id === payload.previewId)) {
      const ev = evs.find((e) => e.id === payload.previewId);
      if (ev) {
        const g = layoutStorm(ev, payload.proj, frameMs, true);
        if (g) storms.push(g);
      }
    }
    for (const st of storms) {
      if (st.ghost) continue;
      const c = census[st.ev.type] || (census[st.ev.type] = new Uint8Array(LANES[st.ev.type].bins.length));
      for (const f of st.shown) if (f.bin >= 0) c[f.bin] = 1;
    }
    drawCanvas(payload, storms);
    drawMarks(payload, storms);
  }

  window.EwbTracks = {
    onMapTracks, legendHtml, splitAtDateline, LANES,
    _dbg: () => ({ marks, lanes: Object.keys(LANES), census }),
  };
  window.onMapTracks = onMapTracks;
  window.onTrackLegend = legendHtml;
})();
