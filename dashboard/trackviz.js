/* TrackViz — best-track map for TRACK lanes (tropical_cyclone today; anything
   else that publishes details.track). tc lane, 2026-11-16.

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

   Visuals: an Equal Earth globe on the merged-map grounds (--map-ocean /
   --paper / --map-line) so this panel reads as the same product. Fixes are
   coloured by the lane's single-hue violet ramp (--tc-bin-1..5) in wind bins
   on the Saffir-Simpson thresholds. The event window (event.start → end) is
   solid with full-size points; the rest of the system's life (its life before
   storm force, and its decay after) is dashed and faded. Hover or tap a fix
   for time, position, wind and pressure.
*/
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const PAD = 10;

  /* wind bins in the lane's intensity unit — the Saffir-Simpson bands of
     ewb_live/events/tc.py merged into five swatches (CAT2+3, CAT4+5), keeping
     the real band edges so a point's colour and its label never disagree */
  const BINS = [
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

  let panel = null, mapHost = null, obs = null, data = null, land = null, landP = null;

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

  function binOf(v) {
    if (!Number.isFinite(v)) return -1;
    let b = 0;
    for (let i = 0; i < BINS.length; i++) if (v >= BINS[i].min) b = i;
    return b;
  }
  const binColor = (b) => (b < 0 ? css('--series-unknown', '#848475')
                                 : css(BINS[b].tok, '#848475'));

  /* ---------------- projection / fit ---------------- */
  let prj = null, path = null, fit = { W: 0, H: 0 };
  function fitProjection() {
    const W = Math.max(260, mapHost.clientWidth || 640);
    let p = d3.geoEqualEarth();
    const b = d3.geoPath(p.scale(1).translate([0, 0])).bounds({ type: 'Sphere' });
    const dw = b[1][0] - b[0][0], dh = b[1][1] - b[0][1];
    // hug the globe: Equal Earth is ~1.95:1, a fixed height would leave a dead band on phones
    const H = Math.max(200, Math.round(Math.min((dh / dw) * (W - 2 * PAD) + 2 * PAD, W * 0.75)));
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
  const width360 = new Map();
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

  /* ---------------- tooltip ---------------- */
  let tip = null;
  function showTip(f, x, y) {
    if (!tip) return;
    const rows = [(f.iso ? String(f.iso).replace('T', ' ').replace(/Z$/, '') + ' UTC' : 'time unknown'),
      `${f.lat.toFixed(1)}°, ${f.lon.toFixed(1)}°`,
      Number.isFinite(f.kt) ? `${Math.round(f.kt)} ${data.units} · ${category(f.kt)}`
                            : `no ${data.units} value`,
      Number.isFinite(f.pres) ? `${Math.round(f.pres)} hPa` : 'no pressure'];
    if (!f.live) rows.push('outside the event window');
    tip.textContent = rows.join('\n');
    tip.hidden = false;
    tip.style.left = Math.max(78, Math.min(fit.W - 78, x)) + 'px';
    tip.style.top = Math.max(64, y - 12) + 'px';
  }
  const hideTip = () => { if (tip) tip.hidden = true; };

  /* ---------------- draw ---------------- */
  function draw() {
    if (!panel || !data || !mapHost) return;
    fitProjection();
    width360.clear();
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
       wrapped world map; the globe clip cuts both copies at the edge. */
    for (const off of [-1, 0, 1]) {
      for (const seg of data.segs) {
        if (off) for (const f of seg) f._g = projectFix(f, off);
        const pts = seg.map((f) => (off ? f._g : f._p)).filter(Boolean);
        const d = line(pts);
        if (!d) continue;
        mk('path', { class: 'tv-line-pre', d }, g);
        const live = line(seg.filter((f) => f.live).map((f) => (off ? f._g : f._p)).filter(Boolean));
        if (live) mk('path', { class: 'tv-line-qual', d: live }, g);
      }
    }

    /* fixes: pre/post-qualifying first, then the window, the peak last (on top) */
    const ordered = data.fixes.filter((f) => !f.live)
      .concat(data.fixes.filter((f) => f.live && !f.peak), data.peak ? [data.peak] : []);
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

    if (data.peak && Number.isFinite(data.peak.kt)) {
      /* the label is drawn OUTSIDE the globe clip (a clipped "11… kt" reads as a
         bug) and clamped into the canvas; a peak that sits in a world copy is
         labelled at the copy that is actually on screen */
      const cand = [data.peak._spot, data.peak._p].find(
        (p) => p && p[0] > 40 && p[0] < W - 40) || data.peak._p;
      if (cand) {
        const x = Math.max(46, Math.min(W - 46, cand[0]));
        const y = Math.max(18, cand[1] - 8);
        const right = x < W * 0.62;
        const t = mk('text', { class: 'tv-lab', x: (x + (right ? 9 : -9)).toFixed(1), y,
          'text-anchor': right ? 'start' : 'end' }, mk('g', {}, svg));
        t.textContent = `${Math.round(data.peak.kt)} ${data.units}`;
      }
    }

    mapHost.appendChild(svg);
    tip = el('div', 'tv-tip');
    tip.hidden = true;
    mapHost.appendChild(tip);
  }

  /* ---------------- legend / copy ---------------- */
  function legend(units) {
    const box = el('div', 'tv-legend');
    box.setAttribute('aria-hidden', 'true');
    for (const b of BINS) {
      const s = el('span');
      const sw = el('span', 'tv-sw');
      sw.style.background = `var(${b.tok})`;
      s.appendChild(sw);
      s.appendChild(document.createTextNode(`${b.lab} ${units}`));
      box.appendChild(s);
    }
    return box;
  }

  /* ---------------- entry ---------------- */
  function render(hostEl, ev) {
    const rows = ev && ev.details && Array.isArray(ev.details.track) ? ev.details.track : null;
    if (!hostEl || !rows || !rows.length) return;
    if (typeof d3 === 'undefined' || !d3.geoEqualEarth) return;

    const units = (ev.details.intensity_units || '').trim() || 'kt';
    const cat = String(ev.details.peak_category || '').trim();
    const t0 = parseISO(ev.start), t1 = parseISO(ev.end);

    const fixes = [];
    for (const r of rows) {
      if (!Array.isArray(r) || r.length < 3) continue;
      const [iso, lat, lon, kt, pres] = r;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const t = parseISO(iso);
      fixes.push({ iso, lat, lon, kt: Number.isFinite(kt) ? kt : null,
        pres: Number.isFinite(pres) ? pres : null, bin: binOf(kt), i: fixes.length,
        live: !!(t0 && t1 && t && t >= t0 && t < t1) });
    }
    if (!fixes.length) return;

    let peak = null;
    const pick = (pool) => pool.forEach((f) => {
      if (Number.isFinite(f.kt) && (!peak || f.kt > peak.kt)) peak = f;
    });
    pick(fixes.filter((f) => f.live));
    if (!peak) pick(fixes);
    if (peak) peak.peak = true;

    const name = ev.details.name && ev.details.name !== 'Unnamed' ? ev.details.name : 'this storm';
    const first = fixes[0], last = fixes[fixes.length - 1];
    const aria = `Best track of ${name}: ${fixes.length} fixes, `
      + `${String(first.iso).slice(0, 10)} to ${String(last.iso).slice(0, 10)}, peak `
      + (peak && Number.isFinite(peak.kt) ? `${Math.round(peak.kt)} ${units}${cat ? ' (' + cat + ')' : ''}` : 'n/a');

    data = { fixes, segs: layout(fixes), peak, units, aria };
    panel = hostEl;
    panel.innerHTML = '';

    const head = el('div', 'tv-head');
    head.appendChild(el('b', '', 'Best track'));
    head.appendChild(el('span', '', [
      ev.details.source || 'IBTrACS',
      `${fixes.length} fixes`,
      ev.details.fix_step_h ? `${ev.details.fix_step_h}-hourly` : 'irregular cadence',
      ev.details.wind_source ? `wind: ${ev.details.wind_source} agency` : '',
      ev.details.basin_name || '',
    ].filter(Boolean).join(' · ')));
    panel.appendChild(head);

    mapHost = el('div', 'tv-map-wrap');
    panel.appendChild(mapHost);
    panel.appendChild(legend(units));

    const note = el('p', 'tv-note');
    note.textContent = 'Solid line and full points: the event window. Dashed and faded: the rest of '
      + 'the system’s life — before it reached storm force and after it left. Point colour is '
      + `${units}; the label marks the peak.`
      + (ev.details.track_crosses_antimeridian ? ' This track crosses the 180° meridian.' : '');
    panel.appendChild(note);

    draw();
    land = null;
    loadLand().then((l) => { land = l; if (panel) draw(); });

    if (typeof ResizeObserver !== 'undefined') {
      obs = new ResizeObserver(() => { if (panel && mapHost.clientWidth > 0) draw(); });
      obs.observe(mapHost);
    } else {
      window.addEventListener('resize', draw);
    }
  }

  function teardown() {
    if (obs) { obs.disconnect(); obs = null; }
    window.removeEventListener('resize', draw);
    if (panel) panel.innerHTML = '';
    panel = null; mapHost = null; tip = null; data = null; land = null;
  }

  window.TrackViz = { render, teardown };
})();
