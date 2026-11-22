/* TrackViz — the Unified Event Detail for TRACK lanes (tropical_cyclone,
   midlat_cyclone), WP3 of the UX redesign (notes/ux_redesign_contract.md §3).

   The track lanes have no viz.json asset: the event carries its own geometry,
   so the detail view draws it here instead of loading EventViz. One renderer
   serves every lane that publishes a track, on the shared row schema —
   details.track = [[iso_time, lat, lon_degE, intensity, pressure_hpa], …]
   oldest first, intensity in details.intensity_units, nulls JSON-native:

     slot 0  ISO-8601 UTC (a naive string is read as UTC)
     slot 1  latitude, deg N
     slot 2  longitude, deg E in [-180, 180] (the repo wrap convention)
     slot 3  intensity in the lane's headline unit, or null
     slot 4  pressure hPa, or null

   A track that crosses the antimeridian is NOT split in the data
   (details.track_crosses_antimeridian says whether it does): longitudes are
   unwrapped along the track here and the drawn geometry is clipped to the
   globe, so the line leaves one edge and re-enters the other.

   STRUCTURE (identical anatomy for both track lanes, matching EventViz):
     Band 2  .det-toolbar   source · cadence + Full lifecycle / Event window
                           scope toggle, event-window date span
     Band 3  .det-stage     .det-map-col  (Equal Earth SVG track + ⤢ + legend bar)
                           .det-side-col   (Storm summary kv rows + Track notes fold)
     Band 4  .det-summary-strip  intensity timeline (wind kt for TC, central
                           pressure hPa for MLC) with the event window shaded
     Band 1 and Band 5 belong to app.js (header, scores fold).

   LANE SCALES. TC keeps the five Saffir-Simpson wind bins of
   ewb_live/events/tc.py (--tc-bin-1..5) on a violet stroke (--series-tc).
   MLC is a PRESSURE lane: five hPa bins where LOWER = deeper = stronger, on a
   red ramp (--tv-mlc-bin-1..5, tuned for both themes) on --series-mlc, and its
   peak fix is the MINIMUM central pressure. An MLC never prints TD/TS/CAT.

   Visuals: an Equal Earth globe on the merged-map grounds (--map-ocean /
   --paper / --map-line) so this panel reads as the same product. The event
   window (event.start → end) is solid with full-size points; the rest of the
   system's life is dashed and faded and can be hidden with the scope toggle.
   Hover or tap a fix for time, position and the lane's own intensity.
*/
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const PAD = 10;
  const STYLE_ID = 'tv-detail-styles';

  /* ---------------- lane scales ----------------
     wind bins in the lane's intensity unit — the Saffir-Simpson bands of
     ewb_live/events/tc.py merged into five swatches (CAT2+3, CAT4+5), keeping
     the real band edges so a point's colour and its label never disagree */
  const TC_BINS = [
    { min: 0,   tok: '--tc-bin-1', lab: '<34 TD' },
    { min: 34,  tok: '--tc-bin-2', lab: '34–63 TS' },
    { min: 64,  tok: '--tc-bin-3', lab: '64–82 CAT1' },
    { min: 83,  tok: '--tc-bin-4', lab: '83–112 CAT2–3' },
    { min: 113, tok: '--tc-bin-5', lab: '≥113 CAT4–5' },
  ];
  /* mirrors CATEGORY_BANDS in ewb_live/events/tc.py (kt) */
  const BANDS = [[137, 'CAT 5'], [113, 'CAT 4'], [96, 'CAT 3'], [83, 'CAT 2'],
                 [64, 'CAT 1'], [34, 'TS'], [0, 'TD']];
  const category = (v) => (!Number.isFinite(v) ? 'TD'
    : (BANDS.find(([lo]) => v >= lo) || [0, 'TD'])[1]);

  /* Midlatitude lane: central pressure bins, LOWER = deeper = stronger, so
     index 4 is the strongest storm (the ramp runs shallow → deep). */
  const MLC_BINS = [
    { tok: '--tv-mlc-bin-1', lab: '>990' },
    { tok: '--tv-mlc-bin-2', lab: '980–990' },
    { tok: '--tv-mlc-bin-3', lab: '970–980' },
    { tok: '--tv-mlc-bin-4', lab: '955–970' },
    { tok: '--tv-mlc-bin-5', lab: '≤955 hPa' },
  ];
  const binOf = (v, isMLC) => {
    if (!Number.isFinite(v)) return -1;
    if (!isMLC) {
      let b = 0;
      for (let i = 0; i < TC_BINS.length; i++) if (v >= TC_BINS[i].min) b = i;
      return b;
    }
    if (v > 990) return 0;
    if (v > 980) return 1;
    if (v > 970) return 2;
    if (v > 955) return 3;
    return 4;
  };

  /* ---------------- component CSS ----------------
     The band chrome (.det-toolbar / .det-stage / .det-map-col / .det-side-* /
     .det-summary-strip / .det-kv-row / details.det-unavail) is styled once in
     styles.css for ALL seven lanes — this module does not re-declare it. What
     lives here is only what the track lane owns: the scope toggle's pressed
     state, the borderless map fill, the fullscreen stage, the lane-hued track,
     the pressure ramp, and the timeline's own marks. Every selector is scoped
     under .tv-root so nothing can reach the other lanes. */
  const CSS = [
    '.tv-root{margin:0;min-width:0}',
    /* ---- Band 2 readouts ---- */
    '.tv-root .det-tb-meta{font-size:var(--text-xs);color:var(--ink-secondary);' +
      'font-variant-numeric:tabular-nums}',
    '.tv-root .det-tb-range{font-size:var(--text-xs);color:var(--ink-secondary);' +
      'font-variant-numeric:tabular-nums;white-space:nowrap}',
    /* segmented scope toggle — the pressed half is a solid inverted fill, never
       an inset box-shadow on a 999px pill (contract §1) */
    '.tv-root .det-track-scope button:hover{background:var(--tint)}',
    '.tv-root .det-track-scope button[aria-pressed="true"]{background:var(--matte-black);' +
      'color:var(--canvas);border-color:var(--matte-black);font-weight:500}',
    '.tv-root .det-track-scope button:focus-visible{outline:2px solid var(--accent-focus);outline-offset:2px}',
    /* ---- Band 3: the SVG fills the map card (the card owns the frame) ---- */
    '.tv-root .tv-map-wrap{margin:0;border:0;border-radius:0;background:var(--map-ocean);min-width:0}',
    /* fullscreen: the map card covers the screen, Esc exits (keydown hook) */
    '.tv-root .det-map-col.ev3-fs{position:fixed;inset:16px;z-index:2600;margin:0;' +
      'border-radius:8px;box-shadow:var(--shadow)}',
    '.tv-root .det-map-col.ev3-fs .tv-map-wrap{flex:1 1 auto;min-height:0;display:flex;' +
      'align-items:center;justify-content:center}',
    '.tv-root .det-map-col.ev3-fs .tv-map-wrap svg{max-height:100%}',
    /* the lane's identity hue strokes the track: styles.css carries the TC
       default, this scoped rule re-hues the midlatitude lane */
    '.tv-root.is-mlc .tv-line-qual{stroke:var(--series-mlc)}',
    '.tv-root.is-mlc .tv-line-pre{stroke:var(--series-mlc)}',
    /* line-style key in the integrated bar (the swatch chips are shared) */
    '.tv-root .tv-keys{margin-inline-start:auto;display:inline-flex;flex-wrap:wrap;gap:4px 12px}',
    '.tv-root .tv-keyline{display:inline-block;width:18px;border-top:2px solid var(--ink-secondary)}',
    '.tv-root .tv-keyline.dash{border-top-style:dashed;opacity:.65}',
    /* the summary rail reads label-left / value-right in the lane's mono */
    '.tv-root .det-kv-row{justify-content:space-between}',
    '.tv-root .det-kv-row > .v{text-align:right;overflow-wrap:anywhere}',
    /* the notes fold keeps its paragraphs tight */
    '.tv-root .det-unavail-body{display:flex;flex-direction:column;gap:5px}',
    '.tv-root .det-unavail-body p{margin:0}',
    '.tv-root .tv-tip{white-space:pre-line}',
    /* ---- Band 4: intensity timeline ---- */
    '.tv-root .det-spark{display:block;width:100%;height:110px}',
    '.tv-root .det-sp-empty{font-size:var(--text-xs);color:var(--ink-muted)}',
    '.tv-root .det-sp-window{fill:var(--alabaster-mid);opacity:.5}',
    '.tv-root .det-sp-axis{stroke:var(--border-strong);stroke-width:1;fill:none}',
    '.tv-root .det-sp-grid{stroke:var(--alabaster-mid);stroke-width:1;stroke-dasharray:2 3;fill:none}',
    '.tv-root .det-sp-lab{font-family:var(--font-mono);font-size:10px;fill:var(--ink-muted)}',
    '.tv-root .det-sp-peak{font-family:var(--font-mono);font-size:10.5px;fill:var(--text);' +
      'paint-order:stroke;stroke:var(--paper);stroke-width:2.5px;stroke-linejoin:round}',
    /* ---- midlatitude pressure ramp: deeper = stronger. Light: deeper runs
       DARKER crimson; dark: deeper runs BRIGHTER crimson (both read as "more").
       Measured on the map grounds exactly as --tc-bin-* was, so the two storm
       lanes carry the same weight there: ocean 3.03 4.35 5.98 7.93 9.61 (light)
       / 3.23 4.39 6.09 8.52 9.35 (dark) — the >=3:1 floor of --map-line */
    ':root{--tv-mlc-bin-1:#bd7b74;--tv-mlc-bin-2:#a95c55;--tv-mlc-bin-3:#96433d;'
      + '--tv-mlc-bin-4:#852c27;--tv-mlc-bin-5:#7a1816}',
    'html[data-theme="dark"]{--tv-mlc-bin-1:#ac4740;--tv-mlc-bin-2:#c25f55;'
      + '--tv-mlc-bin-3:#d67d70;--tv-mlc-bin-4:#e6a096;--tv-mlc-bin-5:#f4a69e}',
  ].join('\n');

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  /* ---------------- helpers ---------------- */
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  };
  const mk = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const css = (name, dflt) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || dflt;
  };
  const parseISO = (s) => {
    if (!s) return null;
    const d = new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
    return isNaN(d) ? null : d;
  };
  const loadLand = () => (landP = landP || fetch('geo/land-110m.geojson')
    .then((r) => (r.ok ? r.json() : null)).catch(() => null));

  const DAY = (d, yr) => {
    const o = { month: 'short', day: 'numeric', timeZone: 'UTC' };
    if (yr) o.year = 'numeric';
    return d.toLocaleDateString('en-US', o);
  };
  /* `Oct 14 – Oct 26` (short) / `Oct 14 – Oct 26, 2026` (same year) /
     `Dec 30, 2025 – Jan 4, 2026` (crossing one) */
  function spanText(a, b, withYear) {
    if (!a && !b) return '';
    if (!a) return DAY(b, !!withYear);
    if (!b) return DAY(a, !!withYear);
    if (!withYear) return `${DAY(a, false)} – ${DAY(b, false)}`;
    return a.getUTCFullYear() === b.getUTCFullYear()
      ? `${DAY(a, false)} – ${DAY(b, true)}`
      : `${DAY(a, true)} – ${DAY(b, true)}`;
  }
  /* "IBTrACS v04r01 (NOAA NCEI, best track)" → "IBTrACS";
     "ERA5 MSLP (ARCO), closed-low tracker" → "ERA5 MSLP" */
  const shortSource = (s, dflt) => {
    const t = String(s || '').split('(')[0].split(',')[0].trim().replace(/\s+v\d\S*$/, '');
    return t || dflt;
  };

  /* ---------------- state ---------------- */
  let root = null, panelHost = null, mapHost = null, sparkHost = null, legendBar = null;
  let fsCol = null, fsBtn = null;
  let tip = null, data = null, land = null, landP = null;
  let ro = null, themeObs = null, onKey = null, onResize = null, fsOn = false;
  let prj = null, path = null, fit = { W: 0, H: 0 };
  let mapKey = '', sparkKey = '';
  const width360 = new Map();

  /* the lane's identity hue, read fresh from the CSS custom property so a theme
     flip never keeps the old resolved colour and MLC never hard-codes a hex */
  const laneHue = () => css(data && data.isMLC ? '--series-mlc' : '--series-tc',
    (data && data.hueFallback) || 'currentColor');

  /* ---------------- geometry ----------------
     Unwrap longitudes along the track: every fix takes the representative of
     {lon + 360k} nearest its predecessor, so a track that crosses the dateline
     is one continuous line in lon-extended space; the map's own globe clip
     pushes it out at one edge and back in at the other. A jump larger than
     180° AFTER unwrapping is a genuine discontinuity (bad fix, or a track
     stitched from two systems) and starts a new line segment. */
  function layout(fixes) {
    let prev = null;
    for (const f of fixes) {
      let lon = f.lon;
      if (prev != null) {
        while (lon - prev > 180) lon -= 360;
        while (prev - lon > 180) lon += 360;
      }
      f.lonU = lon;
      f.wrap = Math.round((lon - f.lon) / 360);   // 360-degree copies, see projectFix
      prev = lon;
    }
    const segs = [];
    let cur = null;
    fixes.forEach((f, i) => {
      if (i && Math.abs(f.lonU - fixes[i - 1].lonU) > 180) cur = null;
      if (!cur) { cur = []; segs.push(cur); }
      cur.push(f);
    });
    return segs;
  }

  /* the scope toggle: `Event window` keeps only the live fixes, and a segment
     breaks wherever a non-live fix sat between two live ones */
  function visibleSegs() {
    if (!data.windowOnly) return data.segs;
    const out = [];
    for (const seg of data.segs) {
      let cur = null;
      for (const f of seg) {
        if (!f.live) { cur = null; continue; }
        if (!cur) { cur = []; out.push(cur); }
        cur.push(f);
      }
    }
    return out;
  }

  const binTok = (b) => ((data && data.isMLC ? MLC_BINS : TC_BINS)[b] || TC_BINS[b]).tok;
  const binColor = (b) => (b < 0 ? css('--series-unknown', '#848475')
                                 : css(binTok(b), '#848475'));

  /* ---------------- projection / fit ---------------- */
  function fitProjection() {
    const W = Math.max(260, mapHost.clientWidth || 640);
    let p = d3.geoEqualEarth();
    const b = d3.geoPath(p.scale(1).translate([0, 0])).bounds({ type: 'Sphere' });
    const dw = b[1][0] - b[0][0], dh = b[1][1] - b[0][1];
    // hug the globe: Equal Earth is ~1.95:1, a fixed height would leave a dead band on phones
    let H = Math.max(200, Math.round(Math.min((dh / dw) * (W - 2 * PAD) + 2 * PAD, W * 0.75)));
    // fullscreen owns its height: fill the modal instead of hugging the width
    const avail = mapHost.clientHeight || 0;
    if (fsOn && avail > 200) H = avail;
    prj = d3.geoEqualEarth().fitExtent([[PAD, PAD], [W - PAD, H - PAD]], { type: 'Sphere' });
    path = d3.geoPath(prj);
    fit = { W, H };
  }
  const project = (lon, lat) => {
    const p = prj([lon, lat]);
    return p && Number.isFinite(p[0]) && Number.isFinite(p[1]) ? p : null;
  };
  /* A d3 projection folds longitude back into [-180, 180], so an unwrapped
     longitude cannot be projected directly. Equal Earth is pseudocylindrical —
     x is LINEAR in longitude at a fixed latitude — so a 360-degree shift is
     exactly one full map width at that latitude, and the wrapped copy of a fix
     sits at its own x plus that width. */
  function widthAt(lat) {
    const key = Math.round(lat * 2) / 2;
    let w = width360.get(key);
    if (w === undefined) {
      const a = prj([-180, key]), b = prj([180, key]);
      w = a && b ? b[0] - a[0] : 0;
      width360.set(key, w);
    }
    return w;
  }
  function projectFix(f, off) {
    const p = project(f.lon, f.lat);
    if (!p) return null;
    const k = (f.wrap || 0) + (off || 0);
    return k ? [p[0] + k * widthAt(f.lat), p[1]] : p;
  }
  const line = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');

  /* ---------------- tooltip ----------------
     Lane-clean: a midlatitude low reads in hPa and NEVER prints a TD/TS/CAT
     category, which is a wind-scale statement about a tropical cyclone. */
  function fixLine(f) {
    if (!Number.isFinite(f.v)) return `no ${data.unit} value`;
    return data.isMLC ? `${Math.round(f.v)} hPa`
                      : `${Math.round(f.v)} ${data.unit} · ${category(f.v)}`;
  }
  function showTip(f, x, y) {
    if (!tip) return;
    const rows = [(f.iso ? String(f.iso).replace('T', ' ').replace(/Z$/, '') + ' UTC' : 'time unknown'),
      `${f.lat.toFixed(1)}°, ${f.lon.toFixed(1)}°`,
      fixLine(f)];
    if (!data.isMLC) rows.push(Number.isFinite(f.pres) ? `${Math.round(f.pres)} hPa` : 'no pressure');
    else if (f.peak) rows.push('deepest fix');
    if (!f.live && !data.windowOnly) rows.push('outside the event window');
    tip.textContent = rows.join('\n');
    tip.hidden = false;
    tip.style.left = Math.max(78, Math.min(fit.W - 78, x)) + 'px';
    tip.style.top = Math.max(64, y - 12) + 'px';
  }
  const hideTip = () => { if (tip) tip.hidden = true; };

  /* ---------------- draw: track map ---------------- */
  function draw() {
    if (!root || !data || !mapHost) return;
    fitProjection();
    width360.clear();
    mapKey = fit.W + 'x' + fit.H;
    const { W, H } = fit;
    mapHost.innerHTML = '';

    const svg = mk('svg', { class: 'tv-svg', width: W, height: H,
      viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': data.aria });
    const clip = mk('clipPath', { id: 'tv-globe-clip' }, mk('defs', {}, svg));
    mk('path', { d: path({ type: 'Sphere' }) }, clip);

    const base = mk('g', {}, svg);
    mk('path', { class: 'tv-sphere', d: path({ type: 'Sphere' }) }, base);
    mk('path', { class: 'tv-grat', d: path(d3.geoGraticule().step([20, 20])()) }, base);
    if (land) mk('path', { class: 'tv-land', d: path(land) }, base);

    const g = mk('g', { 'clip-path': 'url(#tv-globe-clip)' }, svg);

    /* every fix is projected once per draw; a crossing track then gets the
       world copies (off = -1, +1) so it re-enters at the other edge */
    for (const f of data.fixes) f._p = projectFix(f);

    /* track: the full life dashed underneath, the event window solid on top.
       A track that leaves the antimeridian is drawn again in the next world
       copy (off = -1, +1) so it re-enters at the other edge, exactly like a
       wrapped world map; the globe clip cuts both copies at the edge.
       In `Event window` scope the dashed life is not drawn at all. */
    const segs = visibleSegs();
    for (const off of [-1, 0, 1]) {
      for (const seg of segs) {
        if (off) for (const f of seg) f._g = projectFix(f, off);
        const pts = seg.map((f) => (off ? f._g : f._p)).filter(Boolean);
        if (!data.windowOnly) {
          const d = line(pts);
          if (d) mk('path', { class: 'tv-line-pre', d }, g);
        }
        const live = line(seg.filter((f) => f.live).map((f) => (off ? f._g : f._p)).filter(Boolean));
        if (live) mk('path', { class: 'tv-line-qual', d: live }, g);
      }
    }

    /* fixes: pre/post-qualifying first, then the window, the peak last (on top) */
    const pool = data.windowOnly ? data.fixes.filter((f) => f.live) : data.fixes;
    const peakShown = data.peak && (!data.windowOnly || data.peak.live) ? data.peak : null;
    const ordered = pool.filter((f) => !f.live)
      .concat(pool.filter((f) => f.live && !f.peak), peakShown ? [peakShown] : []);
    for (const f of ordered) {
      const spots = [f._p];
      for (const off of [-1, 1]) {                 // world copies of the same fix
        const q = projectFix(f, off);
        if (q && q[0] > -8 && q[0] < W + 8 && q[1] > -8 && q[1] < H + 8) spots.push(q);
      }
      const cls = f.peak ? 'tv-fix tv-fix-peak' : (f.live ? 'tv-fix' : 'tv-fix tv-fix-out');
      spots.forEach((p, ci) => {
        if (f.peak && !f._spot && p[0] > PAD && p[0] < W - PAD) f._spot = p;
        const c = mk('circle', { cx: p[0].toFixed(1), cy: p[1].toFixed(1),
          r: f.peak ? 6 : f.live ? 3.6 : 2.4, fill: binColor(f.bin), class: cls,
          'data-i': f.i, ...(ci ? { 'data-copy': String(ci) } : {}) }, g);
        c.addEventListener('pointerenter', () => showTip(f, p[0], p[1]));
        c.addEventListener('pointerleave', hideTip);
        c.addEventListener('pointerdown', () => showTip(f, p[0], p[1]));
      });
    }

    if (peakShown && Number.isFinite(peakShown.v)) {
      /* the label is drawn OUTSIDE the globe clip (a clipped "11… kt" reads as a
         bug) and clamped into the canvas; a peak that sits in a world copy is
         labelled at the copy that is actually on screen */
      const cand = [peakShown._spot, peakShown._p].find(
        (p) => p && p[0] > 40 && p[0] < W - 40) || peakShown._p;
      if (cand) {
        const x = Math.max(46, Math.min(W - 46, cand[0]));
        const y = Math.max(18, cand[1] - 8);
        const right = x < W * 0.62;
        const t = mk('text', { class: 'tv-lab', x: (x + (right ? 9 : -9)).toFixed(1), y,
          'text-anchor': right ? 'start' : 'end' }, mk('g', {}, svg));
        t.textContent = data.isMLC ? `${Math.round(peakShown.v)} hPa`
                                   : `${Math.round(peakShown.v)} ${data.unit}`;
      }
    }

    mapHost.appendChild(svg);
    tip = el('div', 'tv-tip');
    tip.hidden = true;
    mapHost.appendChild(tip);
  }

  /* ---------------- Band 2: toolbar ---------------- */
  function buildToolbar() {
    const bar = el('div', 'det-toolbar');
    const left = el('div', 'det-tb-left');
    left.appendChild(el('span', 'det-tb-meta', [
      data.sourceShort, `${data.nFixes} fixes`, data.cadence,
    ].filter(Boolean).join(' · ')));

    const seg = el('div', 'det-track-scope');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Track scope');
    const mkBtn = (label, win) => {
      const b = el('button', '', label);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(win === data.windowOnly));
      b.addEventListener('click', () => {
        if (data.windowOnly === win) return;
        data.windowOnly = win;
        seg.querySelectorAll('button').forEach((x) => {
          x.setAttribute('aria-pressed', String(x === b));
        });
        buildLegendBar();
        draw();
      });
      seg.appendChild(b);
    };
    mkBtn('Full lifecycle', false);
    mkBtn('Event window', true);
    left.appendChild(seg);

    bar.appendChild(left);
    const right = el('div', 'det-tb-right');
    right.appendChild(el('span', 'det-tb-range', data.spanLong || 'no event window'));
    bar.appendChild(right);
    return bar;
  }

  /* ---------------- Band 3: legend bar under the map ----------------
     The lane's own scale, in the shared bar vocabulary (.lg-name title,
     .swatch chips, .det-map-bar-i items) so the storm bars read like the
     temperature lanes' bars next door. */
  function buildLegendBar() {
    if (!legendBar || !fsCol) return;
    legendBar.innerHTML = '';
    legendBar.appendChild(el('span', 'lg-name', data.isMLC
      ? 'Central pressure (hPa)' : 'Sustained wind (kt)'));
    for (const b of (data.isMLC ? MLC_BINS : TC_BINS)) {
      const s = el('span', 'det-map-bar-i');
      const sw = el('i', 'swatch');
      sw.style.background = `var(${b.tok})`;
      s.appendChild(sw);
      s.appendChild(document.createTextNode(b.lab));
      legendBar.appendChild(s);
    }
    const keys = el('span', 'tv-keys');
    const key = (cls, txt) => {
      const k = el('span', 'det-map-bar-i');
      k.appendChild(el('i', 'tv-keyline ' + cls));
      k.appendChild(document.createTextNode(txt));
      return k;
    };
    keys.appendChild(key('', 'event window'));
    keys.appendChild(key('dash', data.windowOnly ? 'lifecycle hidden' : 'rest of life'));
    legendBar.appendChild(keys);
  }

  /* ---------------- Band 3: summary rows + notes ---------------- */
  function buildSide() {
    const col = el('div', 'det-side-col');
    const head = el('div', 'det-side-head');
    head.appendChild(el('b', '', 'Storm summary'));
    head.appendChild(el('span', '', data.sourceShort));
    col.appendChild(head);

    const body = el('div', 'det-side-body');
    const list = el('div', 'det-kv-list');
    const peak = data.peak;
    const fin = (v) => Number.isFinite(v);
    const rows = [];
    /* the four rows a track rail always answers (intensity, window, cadence,
     source) carry an em dash rather than vanishing, so the summary never
     collapses; the optional extras only appear when the lane publishes them */
    if (data.isMLC) {
      /* the deepest reading the lane publishes: the marked fix first, then the
         published minimum under either key (mlc.py: min_mslp_hPa; the shared
         slot-4 name: min_pressure_hpa) */
      const pub = [data.d.min_mslp_hPa, data.d.min_pressure_hpa].filter((x) => Number.isFinite(x));
      const minP = peak && Number.isFinite(peak.v) ? peak.v : (pub.length ? Math.min(...pub) : null);
      rows.push(['Min pressure', minP != null ? `${Math.round(minP)} hPa` : '—']);
      rows.push(['Peak depth / Bomb', data.d.bomb
        ? `Bomb ×${fin(data.d.bomb_ratio) ? data.d.bomb_ratio.toFixed(1) : '1'}`
        : 'Non-bomb']);
      if (fin(data.d.displacement_km)) {
        rows.push(['Distance', `${Math.round(data.d.displacement_km).toLocaleString('en-US')} km`]);
      }
      rows.push(['Event window', data.spanShort || '—']);
      rows.push(['Fixes', `${data.nFixes}${data.cadence ? ` (${data.cadence})` : ''}`]);
      rows.push(['Source', data.sourceShort]);
    } else {
      rows.push(['Peak wind', peak && fin(peak.v)
        ? `${Math.round(peak.v)} ${data.unit} · ${category(peak.v)}`
        : (data.cat || '—')]);
      rows.push(['Min pressure', fin(data.d.min_pressure_hpa)
        ? `${Math.round(data.d.min_pressure_hpa)} hPa` : '—']);
      if (data.d.basin_name) rows.push(['Basin', String(data.d.basin_name)]);
      rows.push(['Event window', data.spanShort || '—']);
      rows.push(['Fixes', `${data.nFixes}${data.cadence ? ` (${data.cadence})` : ''}`]);
      if (data.d.wind_basis) {
        rows.push(['Wind basis', `${data.d.wind_basis}${data.d.wind_source ? ` (${data.d.wind_source})` : ''}`]);
      }
      rows.push(['Source', data.sourceShort]);
    }
    for (const [k, v] of rows) {
      const r = el('div', 'det-kv-row');
      r.appendChild(el('span', 'k', k));      /* the shared .det-kv-row > .k/.v */
      r.appendChild(el('span', 'v', v));
      list.appendChild(r);
    }
    body.appendChild(list);
    col.appendChild(body);

    /* the explanatory copy lives in a fold — never a prose wall under the map */
    const notes = [];
    notes.push('Solid line and full points: the event window. Dashed and faded: the rest of '
      + 'the system’s life — before it reached storm force and after it left. '
      + (data.isMLC ? 'Point colour is central pressure; the label marks the deepest fix.'
        : `Point colour is ${data.unit}; the label marks the peak.`));
    if (data.d.track_crosses_antimeridian) notes.push('This track crosses the 180° meridian.');
    if (data.d.window_truncated) {
      const tw = data.d.window_truncated_fixes || {};
      let t = 'This storm’s life extends past the displayed data window';
      if (tw.before_window != null || tw.after_window != null) {
        const before = Number(tw.before_window) || 0;
        t += ` (${before} fix${before === 1 ? '' : 'es'} before it`;
        if (Number(tw.after_window)) t += `, ${Number(tw.after_window)} after`;
        t += ')';
      }
      notes.push(t + '; only the part inside the window is shown.');
    }
    if (!data.isMLC && data.d.wind_basis && data.d.wind_basis !== '1-min') {
      notes.push('Winds here are ' + data.d.wind_basis + ' sustained ('
        + (data.d.wind_source || 'RSMC') + ' agency); the Saffir-Simpson '
        + 'bands are a 1-minute scale, so the category shown is a lower bound.');
    }
    const fold = document.createElement('details');
    fold.className = 'det-unavail';
    const sum = document.createElement('summary');
    sum.textContent = 'Track notes';
    fold.appendChild(sum);
    const fbody = el('div', 'det-unavail-body');
    for (const t of notes) fbody.appendChild(el('p', '', t));
    fold.appendChild(fbody);
    col.appendChild(fold);
    return col;
  }

  /* a 1/2/2.5/5/10 tick ladder so the printed stops are round */
  function niceTicks(a, b, n) {
    let raw = (b - a) / Math.max(1, n);
    if (!Number.isFinite(raw) || raw <= 0) raw = Math.max(Math.abs(a) * 0.1, 1);
    const e = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / e;
    const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
    let lo = Math.floor(a / step) * step;
    let hi = Math.ceil(b / step) * step;
    if (!(hi > lo)) hi = lo + step;
    const vals = [];
    for (let v = lo, k = 0; v <= hi + step * 0.5 && k < 12; k++, v = lo + k * step) {
      vals.push(Math.round(v / step) * step);
    }
    return { lo, hi, vals, dp: step < 1 ? (step < 0.1 ? 2 : 1) : 0 };
  }
  const fmtTick = (v, dp) => (dp > 0 ? v.toFixed(dp).replace(/\.?0+$/, '') : String(Math.round(v)));

  /* ---------------- Band 4: intensity timeline ----------------
     TC reads wind, MLC reads central pressure UPSIDE DOWN (a deeper low reads
     higher), the qualifying window is a shaded band, the peak fix is dotted
     and labelled, and the whole chart speaks through an aria-label. */
  function drawSpark() {
    if (!root || !data || !sparkHost) return;
    const W = Math.max(240, Math.round(sparkHost.clientWidth || 360));
    const H = 110;
    sparkKey = W + 'x' + H;
    sparkHost.innerHTML = '';

    const pts = [];
    for (const f of data.fixes) {
      if (Number.isFinite(f.v) && f.t) {
        pts.push({ x: f.t.getTime(), v: f.v, peak: !!f.peak, iso: f.iso });
      }
    }
    if (pts.length < 2) {
      sparkHost.appendChild(el('div', 'det-sp-empty', 'No intensity series available'));
      return;
    }

    const mL = 42, mR = 14, mT = 22, mB = 20;
    const iw = Math.max(40, W - mL - mR), ih = Math.max(30, H - mT - mB);
    const t0 = pts[0].x, t1 = pts[pts.length - 1].x, span = Math.max(1, t1 - t0);
    let lo = Infinity, hi = -Infinity;
    for (const p of pts) { if (p.v < lo) lo = p.v; if (p.v > hi) hi = p.v; }
    const flip = data.isMLC;                     // lower hPa → higher on the chart
    /* the axis is built on NICE ticks in "reads-higher-is-stronger" space, so
       the two lanes share one code path (wind up = stronger, pressure down =
       stronger) and the printed stops are round numbers, not raw extremes */
    const aLo = flip ? -hi : lo, aHi = flip ? -lo : hi;
    const tk = niceTicks(aLo, aHi, 4);
    const yLo = tk.lo, yHi = tk.hi;
    const X = (t) => mL + ((t - t0) / span) * iw;
    const Y = (v) => {
      const s = flip ? -v : v;
      return mT + (1 - (s - yLo) / ((yHi - yLo) || 1)) * ih;
    };

    const hue = laneHue();
    const aria = (data.isMLC ? 'Central pressure profile (hPa)' : `Sustained wind profile (${data.unit})`)
      + `: ${pts.length} fixes from ${DAY(new Date(t0))} to ${DAY(new Date(t1))}`
      + (flip ? `, deepest ${Math.round(lo)} hPa` : `, peak ${Math.round(hi)} ${data.unit}`)
      + (data.spanShort ? `; shaded band marks the event window, ${data.spanShort}` : '');
    const svg = mk('svg', { class: 'det-spark', viewBox: `0 0 ${W} ${H}`,
      role: 'img', 'aria-label': aria });

    /* qualifying window band */
    const w0 = data.w0 ? Math.max(mL, X(Math.max(data.w0.getTime(), t0))) : null;
    const w1 = data.w1 ? Math.min(mL + iw, X(Math.min(data.w1.getTime(), t1))) : null;
    if (w0 != null && w1 != null && w1 > w0) {
      mk('rect', { class: 'det-sp-window', x: w0.toFixed(1), y: mT,
        width: (w1 - w0).toFixed(1), height: ih }, svg);
    }

    /* axes: nice ticks with gridlines, the baseline, and the dates below */
    const lab = (x, y, txt, anchor) => {
      const t = mk('text', { class: 'det-sp-lab', x: x.toFixed(1), y: y.toFixed(1),
        'text-anchor': anchor || 'start' }, svg);
      t.textContent = txt;
      return t;
    };
    for (const tv of tk.vals) {
      const val = flip ? -tv : tv;          /* ticks live in flipped axis space */
      const y = Y(val);
      if (y > mT + 1 && y < mT + ih - 1) {
        mk('path', { class: 'det-sp-grid', d: `M${mL} ${y.toFixed(1)}H${(mL + iw).toFixed(1)}` }, svg);
      }
      lab(4, Math.max(mT + 7, Math.min(mT + ih, y + 3.5)), fmtTick(val, tk.dp));
    }
    mk('path', { class: 'det-sp-axis', d: `M${mL} ${mT + ih}H${(mL + iw).toFixed(1)}` }, svg);
    lab(mL, H - 6, DAY(new Date(t0)));
    lab(mL + iw, H - 6, DAY(new Date(t1)), 'end');

    /* the profile: a Catmull-Rom spline through the fixes, in the lane hue */
    const P = pts.map((p) => [X(p.x), Y(p.v)]);
    let d = 'M' + P[0][0].toFixed(1) + ' ' + P[0][1].toFixed(1);
    for (let i = 0; i < P.length - 1; i++) {
      const p0 = P[i - 1] || P[i], p1 = P[i], p2 = P[i + 1], p3 = P[i + 2] || p2;
      d += 'C' + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + ' ' + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)
        + ' ' + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + ' ' + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)
        + ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1);
    }
    mk('path', { d, fill: 'none', stroke: hue, 'stroke-width': 1.8,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);

    /* peak fix: dot + value label, clamped inside the plot */
    const pi = pts.findIndex((p) => p.peak);
    const ki = pi >= 0 ? pi : (flip
      ? pts.reduce((a, p, i) => (p.v < pts[a].v ? i : a), 0)
      : pts.reduce((a, p, i) => (p.v > pts[a].v ? i : a), 0));
    const pk = pts[ki];
    /* data-i=-1: this dot is NOT a track fix. Without it a bare `circle` inside
       the detail panel reads back as fix 0 to anything that keys off data-i. */
    mk('circle', { cx: X(pk.x).toFixed(1), cy: Y(pk.v).toFixed(1), r: 3.6,
      fill: hue, stroke: css('--paper', '#fff'), 'stroke-width': 1.2,
      class: 'det-sp-peak-dot', 'data-i': -1, 'data-spark': 'peak' }, svg);
    const lx = Math.max(mL + 28, Math.min(mL + iw - 28, X(pk.x)));
    const ly = Math.max(12, Y(pk.v) - 8);
    const t = mk('text', { class: 'det-sp-peak', x: lx.toFixed(1),
      y: ly.toFixed(1), 'text-anchor': 'middle' }, svg);
    t.textContent = data.isMLC ? `${Math.round(pk.v)} hPa` : `${Math.round(pk.v)} ${data.unit}`;

    sparkHost.appendChild(svg);
  }

  /* ---------------- fullscreen ---------------- */
  function setFs(on) {
    if (!fsCol) return;
    fsOn = !!on;
    fsCol.classList.toggle('ev3-fs', fsOn);
    if (fsBtn) {
      fsBtn.setAttribute('aria-pressed', String(fsOn));
      fsBtn.title = fsOn ? 'Exit fullscreen (Esc)' : 'Expand to fullscreen (Esc)';
    }
    /* the SVG is sized in pixels: redraw it at the new container size */
    requestAnimationFrame(() => { if (root) { mapKey = ''; draw(); drawSpark(); } });
  }

  /* ---------------- entry ---------------- */
  function render(hostEl, ev, opts) {
    const details = (ev && ev.details) || {};
    const rows = Array.isArray(details.track) ? details.track : null;
    if (!hostEl || !rows || !rows.length) return;
    if (typeof d3 === 'undefined' || !d3.geoEqualEarth) return;
    injectStyles();
    cleanup();

    const isMLC = ev.event_type === 'midlat_cyclone'
      || String(details.intensity_units || '').trim().toLowerCase() === 'hpa'
      || /^hpa\b/i.test(String(details.intensity_units || '').trim());
    const unit = isMLC ? 'hPa' : (String(details.intensity_units || '').trim() || 'kt');
    const t0 = parseISO(ev.start), t1 = parseISO(ev.end);

    const fixes = [];
    for (const r of rows) {
      if (!Array.isArray(r) || r.length < 3) continue;
      const [iso, lat, lon, val, pres] = r;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const t = parseISO(iso);
      const v = Number.isFinite(val) ? val : null;
      const p = Number.isFinite(pres) ? pres : (isMLC ? v : null);
      fixes.push({ iso, lat, lon, v, pres: p, t, bin: binOf(v, isMLC), i: fixes.length,
        live: !!(t0 && t1 && t && t >= t0 && t < t1) });
    }
    if (!fixes.length) return;

    /* peak = the lane's "strongest": max wind for a TC, MINIMUM pressure for a
       midlatitude low. Preferred inside the event window, lifetime otherwise. */
    let peak = null;
    const better = isMLC ? (a, b) => a < b : (a, b) => a > b;
    const pick = (pool) => pool.forEach((f) => {
      if (Number.isFinite(f.v) && (!peak || better(f.v, peak.v))) peak = f;
    });
    pick(fixes.filter((f) => f.live));
    if (!peak) pick(fixes);
    if (peak) peak.peak = true;

    /* cadence: the published step, else the median fix spacing on the track */
    let step = Number.isFinite(details.fix_step_h) && details.fix_step_h > 0
      ? details.fix_step_h : null;
    if (!step) {
      const ts = fixes.map((f) => (f.t ? f.t.getTime() : null)).filter((x) => x);
      const dh = [];
      for (let i = 1; i < ts.length; i++) {
        const h = (ts[i] - ts[i - 1]) / 3600000;
        if (h > 0 && h < 72) dh.push(h);
      }
      if (dh.length) {
        dh.sort((a, b) => a - b);
        const m = dh[Math.floor(dh.length / 2)];
        step = Math.round(m * 10) / 10;
      }
    }
    const cadence = step ? `${step}-hourly` : 'irregular cadence';

    const srcShort = shortSource(details.source, isMLC ? 'ERA5 MSLP' : 'IBTrACS');
    const name = details.name && details.name !== 'Unnamed' ? details.name : 'this storm';
    /* the window readout: `end` is exclusive, so the last active instant is
       one second before it */
    const w0 = t0 || (fixes[0] && fixes[0].t) || null;
    const w1 = t1 ? new Date(t1.getTime() - 1000) : null;
    const spanShort = spanText(w0, w1, false);
    const spanLong = spanText(w0, w1, true);

    const first = fixes[0], last = fixes[fixes.length - 1];
    const aria = `Best track of ${name}: ${fixes.length} fixes, `
      + `${String(first.iso).slice(0, 10)} to ${String(last.iso).slice(0, 10)}, `
      + (isMLC ? 'lowest central pressure ' : 'peak ')
      + (peak && Number.isFinite(peak.v)
        ? `${Math.round(peak.v)} ${unit}${(!isMLC && details.peak_category) ? ' (' + details.peak_category + ')' : ''}`
        : 'n/a');

    data = {
      fixes, segs: layout(fixes), peak, unit, isMLC, d: details, windowOnly: false,
      nFixes: fixes.length, cadence, sourceShort: srcShort, spanShort, spanLong,
      w0, w1, aria, cat: String(details.peak_category || '').trim(),
      hueFallback: (opts && opts.color) || null,
    };

    root = el('div', 'tv-root' + (isMLC ? ' is-mlc' : ''));
    root.appendChild(buildToolbar());

    const stage = el('div', 'det-stage');
    const mapCol = el('div', 'det-map-col');
    fsCol = mapCol;
    fsBtn = el('button', 'det-fsbtn', '⤢');
    fsBtn.type = 'button';
    fsBtn.setAttribute('aria-pressed', 'false');
    fsBtn.title = 'Expand to fullscreen (Esc)';
    fsBtn.setAttribute('aria-label', 'Expand the track map to fullscreen');
    fsBtn.addEventListener('click', () => setFs(!fsOn));
    mapCol.appendChild(fsBtn);
    mapHost = el('div', 'tv-map-wrap');
    mapCol.appendChild(mapHost);
    legendBar = el('div', 'det-map-bar');
    legendBar.setAttribute('aria-hidden', 'true');
    mapCol.appendChild(legendBar);
    buildLegendBar();
    stage.appendChild(mapCol);
    stage.appendChild(buildSide());
    root.appendChild(stage);

    const strip = el('div', 'det-summary-strip');
    strip.appendChild(el('div', 'ev-dur-title',
      isMLC ? 'Central pressure profile (hPa)' : 'Sustained wind profile (kt)'));
    sparkHost = el('div', 'det-spark-wrap');
    strip.appendChild(sparkHost);
    root.appendChild(strip);

    hostEl.innerHTML = '';
    hostEl.appendChild(root);
    panelHost = hostEl;

    draw();
    drawSpark();
    if (!land) loadLand().then((l) => { land = l; if (root) draw(); });

    /* redraw only when the box actually changed size (the initial observe()
     "resize" matches the draw above, so it costs nothing) */
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => {
        if (!root) return;
        if (mapHost.clientWidth > 0
            && mapHost.clientWidth + 'x' + mapHost.clientHeight !== mapKey) draw();
        if (sparkHost.clientWidth > 0
            && Math.round(sparkHost.clientWidth) + 'x110' !== sparkKey) drawSpark();
      });
      ro.observe(mapHost);
      ro.observe(sparkHost);
    } else {
      onResize = () => { if (root) { draw(); drawSpark(); } };
      window.addEventListener('resize', onResize);
    }
    /* the SVG is built from resolved token colours: a theme flip must redraw */
    if (typeof MutationObserver !== 'undefined') {
      themeObs = new MutationObserver(() => { if (root) { draw(); drawSpark(); } });
      themeObs.observe(document.documentElement, { attributes: true,
        attributeFilter: ['data-theme', 'class'] });
    }
    onKey = (e) => { if (e.key === 'Escape' && fsOn) setFs(false); };
    document.addEventListener('keydown', onKey);
  }

  function cleanup() {
    if (ro) { ro.disconnect(); ro = null; }
    if (themeObs) { themeObs.disconnect(); themeObs = null; }
    if (onKey) { document.removeEventListener('keydown', onKey); onKey = null; }
    if (onResize) { window.removeEventListener('resize', onResize); onResize = null; }
    if (fsCol) fsCol.classList.remove('ev3-fs');
    fsCol = null; fsBtn = null; fsOn = false;
    if (panelHost) panelHost.innerHTML = '';
    panelHost = null;
    root = null; mapHost = null; sparkHost = null; legendBar = null;
    tip = null; data = null; prj = null; path = null;
    mapKey = ''; sparkKey = '';
    width360.clear();
  }

  function teardown() { cleanup(); }

  window.TrackViz = { render, teardown };
})();
