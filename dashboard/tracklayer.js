/* ==========================================================================
   tracklayer.js — the cyclone lanes on the one global map (tc-map, 2026-11-18).

   Two things live here, in this order on the canvas:

   1. the WIND FIELD (the primary encoding, owner direction 2026-11-18): every
      cell where the storm's wind reached tropical-storm force (≥ 34 kt / 39 mph),
      shaded by Saffir-Simpson class — TS, CAT 1…CAT 5. The numbers come from
      ewb_live/viz/tc_wind.py (ERA5 10 m wind, max per cell over the UTC day) in
      data/results/tc_wind.json. The field is REANALYSIS: ERA5 at 0.25° reads
      systematically below the best-track peak (measured 0.69× median on the
      published storms), so the layer, its legend and every tooltip name the
      field as "ERA5 10 m wind" and show the best-track peak separately. The
      shading never gets to be "the category" on its own.
   2. the TRACK, for orientation: a thin polyline (solid inside the event window,
      dashed for the rest of the system's life), one dot per best-track fix in
      the lane's own intensity ramp, and a ring at the storm's position at the
      selected step — which is also the interactive object (hover/tap tooltip,
      click selects, Enter then opens the event page).

   Why this module exists at all: a cyclone is neither a raster lane nor a box.
   With 44 published storms the merged map drew ZERO of them — global_timeline
   .json (the only asset the map read) listed footprint lanes only, and the one
   renderer that draws tracks (dashboard/trackviz.js) is wired into the event
   page only. That was the owner's 2026-11-18 report; the data half is fixed in
   ewb_live/viz/timeline.py (TRACK_TYPES), the drawing half is this file.

   Lanes are data-driven: a lane is a colour, an intensity ramp, bin edges and
   wording (LANES below). tropical_cyclone has a wind field; midlat_cyclone is
   wired for the same treatment and shows nothing until its lane publishes
   events — an honest empty state, no invented data.

   Time semantics: the frame slider REVEALS the storm. The field shows the day
   under the slider (that is what the asset holds: the day's maximum), the track
   shows only fixes at or before the selected step — so playing back never shows
   the future — and the ring sits on the last fix at or before it.

   Layering: one transparent canvas over the raster canvas (field cells, lines,
   fix dots) under a small SVG with one interactive marker per positioned storm
   (SVG is the a11y-correct place: focusable, labelled, a few dozen nodes). Both
   are pointer-events:none except the markers, so the event boxes below stay
   clickable, and both are sized from the merged map's own fit: region views,
   resizes and theme flips stay the map's business.

   Antimeridian: the field is stored as per-cell centres in the repo's
   [-180,180) frame, so a cell is placed by projecting its own centre and a cell
   that straddles ±180 is also painted at the opposite edge; the track polyline
   is split by splitAtDateline() (tested on its own).

   Seams (both documented at their call sites in mergedmap.js):
     window.onMapTracks(payload)   on every map state change the map owns
     window.onTrackLegend(payload) for the colour-legend strip below the map
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

  /* ---------------- the wind field (this lane's primary encoding) ----------
     data/results/tc_wind.json, written by ewb_live/viz/tc_wind.py: per storm,
     per UTC day, a ±5° window of u8 knots (0 = below the 34 kt floor, never
     drawn) plus the class scale, the source string and the measured bias. Fetched
     lazily, once, and only because a track lane is on the map; a missing asset
     (a build that has not run yet) is not an error — the tracks still draw and
     the legend says the field is unavailable. */
  const WIND = { asked: false, data: null, byId: null, classOf: null, failed: false };
  const WIND_URL = 'data/results/tc_wind.json';
  /* Ramp fallbacks (before the asset arrives, or if it never does): the same six
     steps, so the legend never contradicts the producer's class list. Light-mode
     values; the dark theme overrides them with --tc-wind-*. */
  const WIND_FB = [
    { id: 1, tok: '--tc-wind-1', fb: '#ab99d3' }, { id: 2, tok: '--tc-wind-2', fb: '#9782c7' },
    { id: 3, tok: '--tc-wind-3', fb: '#856ab9' }, { id: 4, tok: '--tc-wind-4', fb: '#7352ac' },
    { id: 5, tok: '--tc-wind-5', fb: '#61399e' }, { id: 6, tok: '--tc-wind-6', fb: '#511c90' }];

  function windClassLut(classes) {
    const lut = new Uint8Array(256);
    classes.forEach((c) => {
      const lo = Math.max(0, Math.round(c.kt[0]));
      const hi = c.kt[1] == null ? 255 : Math.min(255, Math.round(c.kt[1]));
      for (let k = lo; k <= hi; k++) lut[k] = c.id;
    });
    return lut;
  }
  function loadWind(again) {
    if (WIND.asked && !again) return;
    WIND.asked = true;
    fetch(WIND_URL, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((d) => {
        if (!d || !Array.isArray(d.storms) || !Array.isArray(d.classes)) throw new Error('bad payload');
        WIND.data = d;
        WIND.classOf = windClassLut(d.classes);
        WIND.byId = new Map(d.storms.map((s) => [s.id, Object.assign({}, s,
          { dayByDate: new Map(s.days.map((e) => [e.d, e])) })]));
        S && draw(S);                       // late arrival: repaint now
      })
      .catch((err) => {
        WIND.failed = true;
        console.warn('cyclone wind field unavailable (tracks still draw):', err.message);
        S && draw(S);
      });
  }
  /* the day's field under the slider: the asset holds one grid per UTC day */
  function fieldEntry(ev, frameDate) {
    const s = WIND.byId && WIND.byId.get(ev.id);
    return s && frameDate ? (s.dayByDate.get(frameDate) || null) : null;
  }
  const frameDateOf = (ts) => { const s = String(ts || ''); return s.length >= 8
    ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : null; };

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
    /* The wind ramp is read from the ASSET's own tokens, so the classes, the
       legend swatches and the canvas cannot drift apart: the producer owns the
       scale, the stylesheet only owns the colour values. */
    out.wind = (WIND.data ? WIND.data.classes : WIND_FB)
      .map((c) => cs.getPropertyValue(c.tok).trim() || c.fb);
    out.halo = cssVar('--map-ocean', '#eef4f8');   // the ground the map itself paints
    return out;
  }
  const colors = () => (colCache || (colCache = packColors()));
  const binColor = (type, b) => {
    const c = colors()[type];
    return !c || b < 0 ? cssVar('--series-unknown', UNKNOWN) : c.bins[b];
  };
  const windColor = (id) => (colors().wind || [])[id - 1] || '';

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
  let S = null;                 // the payload we last drew (repainted on load)
  let last = null;              // {storms, date} of that paint, for field hover
  let tipEv = null, tipRaf = 0, windTip = false;
  let windSeen = null;        // classes painted at the last step, for the legend

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
    if (!host.dataset.tip) {                  // wind-field hover, once per mount
      host.dataset.tip = '1';
      // #raw-canvas is where the map's own AR hover lives (and it is the only
      // layer under the cursor that takes events); the wrap is the fallback for
      // a map with no raster frame. Our canvas is pointer-events:none on purpose.
      const tipHost = document.querySelector('#raw-canvas') || host;
      tipHost.addEventListener('pointermove', (e) => { tipEv = e; if (!tipRaf) tipRaf = requestAnimationFrame(fieldTip); });
      tipHost.addEventListener('pointerleave', () => { if (windTip) { windTip = false; hideTip(); } });
    }
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

  /* ---------------- canvas: the wind field (this lane's primary encoding) ---
     PLACEMENT CONTRACT, pinned by tests/test_tc_wind.py: cell (i, j) of a day's
     window is centred at latitude lat0 + i*step — row 0 is the SOUTHERN centre —
     and longitude wrap(lon0 + j*step), column 0 the western one.

     Equal Earth is linear in x at a fixed latitude, so a row costs four
     projections (its two cell edges for the y band, two points a degree apart
     for the pixel scale) and every cell after that is arithmetic. Cells of one
     class share one Path2D, so a day is at most six fills. A column that runs
     past ±180 is painted again at the opposite edge — split at the seam — so
     the field wraps the dateline exactly like the tracks and the boxes do.

     Below-floor cells are 0 in the asset and are not drawn at all: the layer
     shows where the wind reached tropical-storm force, nowhere else. */
  const WIND_ALPHA = 0.88;
  /* A 0.25° cell is about HALF a CSS pixel on a 700 px-wide world sheet: painted
     at its true size it disappears into anti-aliasing and the layer reads as
     "storm force nowhere". So a cell never paints smaller than this — the same
     legibility floor the map's own raster assets serve by storing ~1° cells.
     Where the geometry is bigger than the floor (region views, zoomed maps)
     nothing is dilated, and the tooltip always quotes the cell's own number. */
  const WIND_MIN_PX = 1.15;
  function gridOf(e) {                           // [v, n, v, n, …] -> Uint8Array, once
    if (!e._g) {
      const g = new Uint8Array(e.ny * e.nx);
      let k = 0;
      for (let i = 0; i < e.rle.length; i += 2) {
        e.rle[i + 1] && g.fill(e.rle[i], k, k + e.rle[i + 1]);
        k += e.rle[i + 1];
      }
      e._g = g;
    }
    return e._g;
  }
  const wrapLon = (x) => ((x + 180) % 360 + 360) % 360 - 180;

  function drawField(payload, storms) {
    if (!WIND.data) return;
    const step = WIND.data.step_deg, proj = payload.proj, H = payload.fit.H;
    const date = frameDateOf(payload.ts);
    const anySel = payload.sel.length > 0;
    const paths = new Map();                     // "class|alpha" -> Path2D
    for (const st of storms) {
      if (st.ghost) continue;
      const e = fieldEntry(st.ev, date);
      if (!e) continue;
      const a = WIND_ALPHA * (anySel && !payload.sel.includes(st.ev.id) ? 0.3 : 1);
      const g = gridOf(e);
      const seen = windSeen;
      for (let i = 0; i < e.ny; i++) {
        const lat = e.lat0 + i * step;
        const yn = proj([0, lat + step / 2]), ys = proj([0, lat - step / 2]);
        if (!yn || !ys) continue;
        const y = yn[1], hGeo = ys[1] - yn[1];
        if (y > H || y + hGeo < 0) continue;                   // row off the sheet
        const h = Math.max(hGeo, WIND_MIN_PX) + 0.4;
        // px per degree of longitude on this row, measured inside the sheet
        const ref = e.lon0 > 178 ? e.lon0 - 1 : e.lon0;
        const a0 = proj([ref, lat]), a1 = proj([ref + 1, lat]);
        if (!a0 || !a1) continue;
        const kx = a1[0] - a0[0], w360 = Math.abs(kx) * 360;
        const x0 = a0[0] + (e.lon0 - ref) * kx, wGeo = Math.abs(kx) * step;
        const w = Math.max(wGeo, WIND_MIN_PX) + 0.4;
        for (let j = 0; j < e.nx; j++) {
          const cid = WIND.classOf[g[i * e.nx + j]];
          if (!cid) continue;
          if (seen) seen[cid] = 1;              // the legend dims what is absent
          const key = cid + '|' + a;
          const p = paths.get(key) || (paths.set(key, new Path2D()), paths.get(key));
          const lonW = e.lon0 + j * step, x = x0 + j * step * kx;
          if (lonW + step <= 180) p.rect(x, y, w, h);
          else {
            const wr = Math.max(0, (180 - lonW) * Math.abs(kx));   // right-edge sliver
            if (wr > 0.4) p.rect(x, y, wr, h);
            if (w - wr > 0.4) p.rect(x - w360, y, w - wr, h);      // the rest, left edge
          }
        }
      }
    }
    for (const [key, p] of paths) {
      ctx.globalAlpha = +key.split('|')[1];
      ctx.fillStyle = windColor(+key.split('|')[0]);
      ctx.fill(p);
    }
    ctx.globalAlpha = 1;
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
    drawField(payload, storms);                  // field first: tracks sit on top
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
        if (WIND.data && f.live) {
          // a best-track dot can now sit ON a wind cell of its own colour, so the
          // dots that matter carry a thin ring of the map's own ground. Live fixes
          // only: the pre-window tail is already quiet, and a ring on every fix is
          // a white ribbon laid along the whole track.
          ctx.strokeStyle = colors().halo;
          ctx.lineWidth = 1.9;
          ctx.beginPath();
          ctx.arc(p[0], p[1], 2.8, 0, 6.2832);
          ctx.stroke();
        }
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

  /* ---------------- field hover: the wind under the cursor -----------------
     The merged map's own AR hover answers for the raster canvas; this one adds
     the cyclone field on the same element and only claims the tooltip when a
     cell is actually under the cursor (and gives it back when it leaves). The
     tooltip names the field and the day, and keeps the best-track peak in its
     own row — the two numbers are never allowed to look like the same thing. */
  function windAt(evt) {
    if (!WIND.data || !last || !S || !S.proj.invert) return null;
    const r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const ll = S.proj.invert([(evt.clientX - r.left) * (S.fit.W / r.width),
      (evt.clientY - r.top) * (S.fit.H / r.height)]);
    if (!ll || !Number.isFinite(ll[0]) || !Number.isFinite(ll[1])) return null;
    const step = WIND.data.step_deg;
    let best = null;
    for (const st of last.storms) {
      const e = fieldEntry(st.ev, last.date);
      if (!e) continue;
      const i = Math.round((ll[1] - e.lat0) / step);
      const j = Math.round((((ll[0] - e.lon0 + 540) % 360) - 180) / step);
      if (i < 0 || j < 0 || i >= e.ny || j >= e.nx) continue;
      const kt = gridOf(e)[i * e.nx + j];
      const cid = WIND.classOf[kt];
      if (cid && (!best || cid > best.cid)) best = { kt, cid, st, d: e.d };
    }
    return best;
  }
  function fieldTip() {
    tipRaf = 0;
    const tip = document.querySelector('#tooltip');
    if (!tip || !S) return;
    const hit = windAt(tipEv);
    if (!hit) {
      if (windTip) { windTip = false; hideTip(); }     // the AR hover owns it again
      return;
    }
    const c = WIND.data.classes[hit.cid - 1], t = hit.st.ev.track || {};
    const peak = Number.isFinite(t.peak)
      ? `${round1(t.peak)} ${t.units || 'kt'}${t.peak_label ? ' · CAT ' + t.peak_label : ''}`
      : 'not published';
    tip.innerHTML = `<div class="t-title">${esc(c.label)} · ERA5 10 m wind</div>`
      + row('wind, daily max', `${hit.kt} kt · ${Math.round(hit.kt * 1.15078)} mph`)
      + row('storm', stormName(hit.st.ev))
      + row('day', hit.d)
      + row('best-track peak', peak)
      + `<div class="t-note">Reanalysis at 0.25° under-reads cyclone peaks: the `
      + `shading is the wind field, never the official category.</div>`;
    tip.classList.remove('hidden');
    if (window.moveTip) window.moveTip(tipEv);
    windTip = true;
  }

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
     the current step does not show are dimmed, same rule as the raster lanes.

     For a lane that has a wind field, the FIELD's classes are the scale (read
     from the asset, so swatches cannot disagree with the map), and the legend
     says what the numbers are: sustained 10 m wind, daily maximum, reanalysis —
     with the measured under-read in the group's own title. The best-track
     colours stay where they belong: the dots on the line and the tooltip. */
  function legendHtml(payload) {
    const tab = payload.tab || 'all';
    const groups = [];
    const windType = (WIND.data && WIND.data.lane) || null;
    for (const type in LANES) {
      if (tab !== 'all' && tab !== type) continue;
      const lane = LANES[type];
      const n = (payload.events || []).filter((e) => e.type === type && e.track).length;
      if (!n) continue;
      const cen = census[type];
      const seen = cen ? cen.reduce((a, b) => a + b, 0) : -1;
      const isWind = windType === type;
      const stops = [];
      if (isWind) {
        for (const c of WIND.data.classes) {
          const kt = c.kt[1] == null ? `≥${c.kt[0]} kt` : `${c.kt[0]}–${c.kt[1]} kt`;
          const mph = c.kt[1] == null ? `${c.mph[0]}+ mph` : `${c.mph[0]}–${c.mph[1]} mph`;
          stops.push(`<span class="lg-bin${windSeen && !windSeen[c.id] ? ' out' : ''}">`
            + `<i class="swatch" style="background:${windColor(c.id)}" `
            + `title="${esc(`${c.label} · ${kt} · ${mph} sustained 10 m wind`)}"></i>`
            + `<span class="lg-t">${esc(c.label.replace('CAT ', 'C'))}</span></span>`);
        }
      } else {
        lane.bins.forEach((b, i) => {
          stops.push(`<span class="lg-bin${cen && !cen[i] ? ' out' : ''}">`
            + `<i class="swatch" style="background:${binColor(type, i)}" `
            + `title="${esc(`${b.lab} ${lane.unit} — ${b.cat}`)}"></i>`
            + `<span class="lg-t">${esc(b.lab)}</span></span>`);
        });
      }
      stops.push(`<span class="lg-bin wide"><i class="swatch lg-line" `
        + `style="background:var(--series-${lane.css})" title="The line is the best `
        + `track; solid inside the event window, dashed for the rest of the system's `
        + `life. The ring is the storm's position at the selected step."></i>`
        + `<span class="lg-t">track</span></span>`);
      const bias = isWind && WIND.data.bias && WIND.data.bias.ratio_median != null
        ? ` It under-reads the peaks — the field's daily maximum is a median `
          + `${WIND.data.bias.ratio_median}× the best-track peak across the `
          + `${WIND.data.bias.n} published storms — so the shading is always at `
          + `or below the official category, which is the dot colour.` : '';
      const tip = isWind
        ? `Shaded cells are where ${WIND.data.source} reached that Saffir–Simpson `
          + `class, as the daily maximum over the UTC day, drawn on a `
          + `${WIND.data.step_deg}° grid (a cell is the maximum of the `
          + `${WIND.data.read_deg}° readings inside it).${bias} Dots are best-track `
          + `fixes (${n} published) and the ring is the storm's position at the `
          + `selected step.`
        : lane.tip;
      const unit = isWind
        ? 'ERA5 10 m wind · daily max · under-reads peaks'
        : `${lane.unit} · ${n} published`;
      groups.push(`<span class="lg-group${seen === 0 ? ' none' : ''}" data-track="${type}" `
        + `title="${esc(tip)}">`
        + `<span class="lg-head"><span class="lg-name">${esc(lane.name)}</span>`
        + `<span class="lg-unit">${esc(unit)}</span>`
        + (seen === 0 ? '<span class="lg-none">none at this step</span>' : '') + '</span>'
        + `<span class="lg-stops">${stops.join('')}</span></span>`);
    }
    return groups.join('');
  }

  /* ---------------- the seam the merged map calls ---------------- */
  function draw(payload) {
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
    windSeen = WIND.data ? new Uint8Array(WIND.data.classes.length + 1) : null;
    last = { storms, date: frameDateOf(payload.ts) };
    drawCanvas(payload, storms);
    drawMarks(payload, storms);
  }

  function onMapTracks(payload) {
    if (!payload || !payload.proj || !payload.fit || !ensureDom()) return;
    if (payload.reason === 'theme') colCache = null;
    census = {};
    S = payload;
    if (!payload.mounted) {
      ctx.clearRect(0, 0, cv.width, cv.height);
      svg.innerHTML = '';
      marks = 0;
      last = null;
      return;
    }
    loadWind();                                  // once; repaints when it lands
    draw(payload);
  }

  window.EwbTracks = {
    onMapTracks, legendHtml, splitAtDateline, LANES,
    _dbg: () => ({ marks, lanes: Object.keys(LANES), census, wind: !!WIND.data,
      windFailed: WIND.failed, windSeen }),
    // the field hit test on page coordinates (the e2e test's hook: the hover is
    // rAF-throttled and pointer-event driven, the lookup is not). The debug form
    // reports what the lookup saw, so a miss is diagnosable instead of mysterious.
    _at: (x, y) => windAt({ clientX: x, clientY: y }),
    _why: (x, y) => ({
      have: !!WIND.data, drawn: !!last, date: last && last.date,
      storms: last ? last.storms.map((s) => s.ev.id) : [],
      fields: last ? last.storms.filter((s) => fieldEntry(s.ev, last.date)).length : 0,
      ll: S && S.proj.invert
        ? S.proj.invert([(x - cv.getBoundingClientRect().left) * (S.fit.W / cv.getBoundingClientRect().width),
          (y - cv.getBoundingClientRect().top) * (S.fit.H / cv.getBoundingClientRect().height)])
        : 'no invert',
      hit: windAt({ clientX: x, clientY: y }) ? true : false,
      cells: (last && S && S.proj.invert) ? last.storms.map((st) => {
        const e = fieldEntry(st.ev, last.date); if (!e) return null;
        const r = cv.getBoundingClientRect();
        const ll = S.proj.invert([(x - r.left) * (S.fit.W / r.width), (y - r.top) * (S.fit.H / r.height)]);
        const step = WIND.data.step_deg;
        const i = Math.round((ll[1] - e.lat0) / step);
        const j = Math.round((((ll[0] - e.lon0 + 540) % 360) - 180) / step);
        return { id: st.ev.id, lat0: e.lat0, lon0: e.lon0, ny: e.ny, nx: e.nx,
                 max: Math.max.apply(null, Array.from(gridOf(e))), i, j,
                 inb: i >= 0 && j >= 0 && i < e.ny && j < e.nx,
                 kt: (i >= 0 && j >= 0 && i < e.ny && j < e.nx) ? gridOf(e)[i * e.nx + j] : null };
      }).filter(Boolean) : [],
    }),
  };
  window.onMapTracks = onMapTracks;
  window.onTrackLegend = legendHtml;
})();
