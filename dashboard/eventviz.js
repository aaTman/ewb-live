/* eventviz.js — per-event visual (consecutive-day maps, AR IVT + mask overlap).
 * Design-owned. Renders viz.json (schema 1-3, built by ewb_live/viz) into
 * a host element as ONE real-geography Leaflet map: the observed field is
 * the base overlay and every selected model stacks on it as its own layer
 * (briefs/event_view_overlay.md — one card per event with per-layer color /
 * opacity / visibility controls).
 *   EventViz.load(id) -> Promise<viz|null>   (silent 404 -> null)
 *   EventViz.render(host, viz, ev, opts)     (idempotent; re-call on theme flip)
 *     opts.models  optional array of model names (scorecard hint); overlay
 *                  mode ignores it for visibility — defaults are baseline
 *                  (opts.baseline, default 'ifs_hres') + the first 2 roster
 *                  models with data at the initial lead (contract §1).
 *     opts.layers  serialized layer state 'name|hex6|op%|01,...' (URL #lyr=).
 *     opts.day     valid-time index into window.days (URL #day=), clamped.
 *     opts.baseline optional baseline model name (default visible + color seed).
 *   EventViz.initialLead lead (hours) to start on; EventViz.onLead(lead),
 *   EventViz.onModels([names]), EventViz.onLayers(serialized) and
 *   EventViz.onDay(index|'') host hooks fire on user changes (and once at
 *   build time for onLead) so deep links stay in sync.
 *
 * Design language follows Neil Gordon's Weather Explorer (MIT licensed,
 * github.com/tehoro/weather-explorer-web): real geographic basemap, smooth
 * semi-transparent field overlay, basemap context that makes the continent
 * obvious. The mask-overlap contours use a marching-squares boundary trace
 * adapted from the same MIT codebase pattern.
 *
 * Each layer is an offscreen-canvas raster laid down as an L.imageOverlay
 * over the grid extent; its color, opacity and visibility come from the
 * per-layer state map. Dashed event box, 10° graticule, Esri gray tiles
 * with a one-shot OSM fallback. Models without data at the lead are listed
 * as one reason line. Without window.L (vendored Leaflet missing) the
 * module degrades to the old canvas-square renderers. */
(function () {
  'use strict';

  /* ---------------- data ---------------- */
  function unpackU8(p) {
    if (!p || typeof p.b64 !== 'string') return p;   // already a v1 list
    const bin = atob(p.b64), scale = (p.hi - p.lo) / 254, out = [];
    let k = 0;
    for (let j = 0; j < p.shape[0]; j++) {
      const row = new Array(p.shape[1]);
      for (let i = 0; i < p.shape[1]; i++, k++) {
        const q = bin.charCodeAt(k);
        row[i] = q === 255 ? null : p.lo + q * scale;
      }
      out.push(row);
    }
    return out;
  }
  function unpackViz(j) {
    if (j.schema !== 2 && j.schema !== 3) return j;
    const u = function (o, key) { if (o && o[key]) o[key] = unpackU8(o[key]); };
    /* schema 3 (ewb_live/viz, 2026-11): per-day tier masks arrive bit-packed —
       day-major stream, row-major within a day, MSB-first, each day padded to
       its own byte boundary. Decode to the schema-2 'daily' string-list form
       ('0'/'1' rows joined by '/') so every renderer path stays shared. */
    const ub = function (o) {
      if (o && o.daily_bits && typeof o.daily_bits.b64 === 'string') {
        o.daily = unpackDailyBits(o.daily_bits);
      }
    };
    u(j.obs, 'consec'); u(j.obs, 'ivt'); ub(j.obs);
    for (const m in j.models) for (const l in j.models[m]) {
      const e = j.models[m][l];
      if (e && typeof e === 'object') { u(e, 'consec'); u(e, 'ivt'); ub(e); }
    }
    return j;
  }
  function unpackDailyBits(db) {
    const bin = atob(db.b64), stride = Math.ceil(db.ny * db.nx / 8), out = [];
    for (let d = 0; d < db.ndays; d++) {
      const rows = [];
      for (let j = 0; j < db.ny; j++) {
        let r = '';
        for (let i = 0; i < db.nx; i++) {
          const k = j * db.nx + i;
          const byte = bin.charCodeAt(d * stride + (k >> 3));
          r += ((byte >> (7 - (k & 7))) & 1) ? '1' : '0';
        }
        rows.push(r);
      }
      out.push(rows.join('/'));
    }
    return out;
  }

  async function load(id) {
    // QA snapshots (?data=dir, whitelisted app-side) carry their own viz
    // tree under data/results/<dir>/events/ — never read live files there.
    let dir = 'data/results/';
    try {
      const p = new URLSearchParams(location.search).get('data');
      if (p && /^[a-z][a-z0-9-]{0,23}$/.test(p)) dir = 'data/results/' + p + '/';
    } catch (e) { /* no URLSearchParams: live path */ }
    const r = await fetch(dir + 'events/' + encodeURIComponent(id) + '/viz.json');
    // Ponytail 2026-11-08: null is reserved for "asset not built" (404);
    // malformed/parse/unpack failures surface distinctly so the UI can tell
    // the two apart, and thrown errors reach the caller's .catch.
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || (j.schema !== 1 && j.schema !== 2 && j.schema !== 3)) {
      return { __vizError: 'unrecognized viz format' };
    }
    return unpackViz(j);
  }

  /* ---------------- color ---------------- */
  function stops(list) { return list.map(function (h) {
    const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }); }
  const RAMPS = {
    heat: stops(['#ffffe5', '#fff7bc', '#fec44f', '#ec7014', '#993404']),
    freeze: stops(['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b']),
    // Owner 2026-11-08: IVT must match the EWB paper's AR figures — single-hue
    // blue sequential, dark = high IVT, near-white = low (NOT the old magma
    // ramp whose black low end read as an opaque slab). Design token: Blues
    // #dbe9f6 → #08306b, domain 250–1000 kg m⁻¹ s⁻¹, alpha 0 below 250.
    ivt: stops(['#dbe9f6', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b']),
  };
  function rampRGB(ramp, t) {
    t = Math.max(0, Math.min(1, t));
    const x = t * (ramp.length - 1), i = Math.min(ramp.length - 2, Math.floor(x)), f = x - i;
    const a = ramp[i], b = ramp[i + 1];
    return [Math.round(a[0] + (b[0] - a[0]) * f),
            Math.round(a[1] + (b[1] - a[1]) * f),
            Math.round(a[2] + (b[2] - a[2]) * f)];
  }
  function rampColor(ramp, t) {
    const c = rampRGB(ramp, t); return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
  }

  function css(name, dflt) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v ? v.trim() : dflt;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  // DR2 B2: the builder emits human reason strings; older viz.json artifacts
  // carry exception class names — map them to the same vocabulary here.
  const REASONS = { ValueError: 'field not derivable for this archive',
    KeyError: 'field not derivable for this archive',
    FileNotFoundError: 'archive missing for this run',
    IndexError: 'lead outside archive window',
    OSError: 'archive missing for this run' };
  function friendly(st) {
    st = String(st || '');
    if (/skipped/.test(st)) return 'skipped this week';
    const m = st.match(/^unavailable\s*\((.*)\)$/);
    if (m) return REASONS[m[1]] || m[1];
    if (/unavailable/i.test(st)) return 'no data at this lead';
    return st;
  }
  const MON3 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDay(d) { // 'YYYY-MM-DD' → 'DD Mon YYYY' (owner bottom-bar format)
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d || ''));
    return m ? (+m[3]) + ' ' + MON3[+m[2] - 1] + ' ' + m[1] : String(d || '');
  }
  function bar(label, n, maxN, color, extra, ticks, obsN) {
    const w = maxN > 0 ? Math.min(100, n > 0 ? Math.max(3, 100 * n / maxN) : 0) : 0;
    let grid = '';
    if (ticks) {
      for (let t = 1; t < maxN; t++) grid += '<u class="ev-bar-tick" style="left:' + (100 * t / maxN) + '%"></u>';
      if (Number.isFinite(obsN) && obsN > 0 && obsN < maxN)
        grid += '<u class="ev-bar-obs" style="left:' + (100 * obsN / maxN) + '%" title="observed ' + obsN + 'd"></u>';
    }
    return '<div class="ev-bar"><span class="ev-bar-label" title="' + esc(label) + '">' + esc(label) + '</span>' +
      '<span class="ev-bar-track"><i style="width:' + w + '%;background:' + color + '"></i>' + grid + '</span>' +
      '<span class="ev-bar-num">' + n + 'd' + (extra || '') + '</span></div>';
  }
  /* M6 (DR2): per-model bar colors — same palette as the scorecard dots */
  const BAR_FALLBACK = ['#4f7cff', '#e5533d', '#2f9e6e', '#b08200', '#8a56c9', '#c94f8a', '#3f9bc4'];
  function modelBarColor(m) {
    if (typeof MODEL_COLORS !== 'undefined' && MODEL_COLORS[m]) return MODEL_COLORS[m];
    let h = 0;
    for (let i = 0; i < m.length; i++) h = (h * 31 + m.charCodeAt(i)) >>> 0;
    return BAR_FALLBACK[h % BAR_FALLBACK.length];
  }
  /* #lead= deep link: start on window.EventViz.initialLead when it matches */
  function pickLead(leads) {
    const want = window.EventViz && +window.EventViz.initialLead;
    if (Number.isFinite(want) && want > 0) {
      for (let i = 0; i < leads.length; i++) if (+leads[i] === want) return leads[i];
    }
    return leads[0] || '24';
  }
  function notifyLeadHook(leadHours) {
    try { if (window.EventViz && window.EventViz.onLead) window.EventViz.onLead(+leadHours); }
    catch (e) { /* host hook must never break the viz */ }
  }
  function notifyModelsHook(list) {
    try { if (window.EventViz && window.EventViz.onModels) window.EventViz.onModels(list.slice()); }
    catch (e) { /* host hook must never break the viz */ }
  }
  /* status → short reason (DR2 B2): 'unavailable (build failed)' rendered as a
     labeled reason with proper spacing, not m(concat) */
  function reasonOf(st) {
    st = String(st || 'no data');
    const m = st.match(/^unavailable\s*\((.*)\)$/);
    return m ? 'unavailable: ' + friendly(st) : st;
  }
  /* M2 (DR2): fit the OCCUPIED extent, not the padded detector box. Bounds
     of the mask cells (AR) or consec>0 cells (heat/freeze), padded 15% of
     span; z4 min clamp letterboxes small events instead of showing poles.
     Falls back to the raw box when occupancy is degenerate or wraps the
     antimeridian. */
  function occFit(viz, rawBox) {
    try {
      if (!viz || !viz.obs || !viz.grid) return rawBox;
      const isAR = viz.kind === 'ar_ivt';
      const lat = viz.grid.lat, lon = viz.grid.lon;
      const ny = lat.length, nx = lon.length;
      const rows = isAR
        ? (viz.obs.mask ? unpackMask(viz.obs.mask, ny) : null)
        : (viz.obs.consec ? unpackU8(viz.obs.consec) : null);
      if (!rows) return rawBox;
      // init ±Infinity (not ±180): a grid entirely >180 (continuous 350→380
      // prime-meridian window) could never lower a 180-seeded w → 211° fit
      let s = 90, n = -90, w = Infinity, e = -Infinity, k = 0;
      // antimeridian-safe: track longitude in continuous coords relative to
      // the first occupied cell (AR events cross 180°; the old w>e bail-out
      // fell back to the padded rawBox → hemisphere view, owner bug A4).
      let anchor = null;
      const cont = function (lo) {
        if (anchor === null) { anchor = lo; return lo; }
        let x = lo;
        while (x - anchor > 180) x -= 360;
        while (anchor - x > 180) x += 360;
        return x;
      };
      for (let y = 0; y < ny; y++) {
        const row = rows[y]; if (!row) continue;
        for (let x = 0; x < nx; x++) {
          if (!(row[x] > 0)) continue;
          if (lat[y] < s) s = lat[y]; if (lat[y] > n) n = lat[y];
          const lx = cont(lon[x]);
          if (lx < w) w = lx; if (lx > e) e = lx;
          k += 1;
        }
      }
      if (k < 2) return rawBox;
      // A4 (owner 2026-11-07): tight occupancy fit + modest padding so the
      // event fills most of the panel (was 15% + z4 floor → hemisphere).
      const padLat = Math.max((n - s) * 0.08, 0.5), padLon = Math.max((e - w) * 0.08, 0.5);
      return [[Math.max(-85, s - padLat), w - padLon],
              [Math.min(85, n + padLat), e + padLon]];
    } catch (err) { return rawBox; }
  }

  function isDarkTheme() {
    const t = document.documentElement.dataset.theme;
    if (t === 'dark' || t === 'light') return t === 'dark';
    return !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function unpackMask(s, ny) {
    const rows = s.split('/');
    const out = [];
    for (let j = 0; j < ny; j++) {
      const r = rows[j] || '';
      const row = new Array(rows[0].length);
      for (let i = 0; i < row.length; i++) row[i] = r[i] === '1';
      out.push(row);
    }
    return out;
  }

  /* ================================================================
   * Leaflet v3 renderer (primary path — notes/spec_eventviz_maps.md)
   * ================================================================ */
  const STYLE_ID = 'ev3-styles';
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '.ev3-root{margin:0}',
      '.ev3-panel{background:var(--bg-panel);border:1px solid var(--border);border-radius:10px;' +
        'padding:8px;min-width:0;box-shadow:var(--shadow)}',
      '.ev3-head{display:flex;align-items:baseline;gap:6px;margin:0 2px 6px}',
      '.ev3-head b{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:auto}',
      '.ev3-fsbtn{flex:none;width:22px;height:22px;line-height:1;padding:0;border-radius:6px;cursor:pointer;' +
        'background:var(--bg-inset);border:1px solid var(--border);color:var(--text-dim);font-size:12px}',
      '.ev3-fsbtn:hover{color:var(--text);border-color:var(--accent)}',
      '.ev3-map{height:460px;border-radius:7px;overflow:hidden;background:var(--bg-inset)}',
      '.ev3-obsblock .ev3-panel{width:100%}',
      '.ev3-obsblock{margin-bottom:12px}',
      '.ev3-panel.ev3-obs .ev3-map{height:500px}',
      '.ev3-panel.ev3-obs{max-width:780px;margin-inline:auto}',
      '.ev3-layers{display:flex;flex-direction:column;gap:4px;margin:8px 2px 0;font-size:11.5px}',
      '.ev3-layer{display:flex;align-items:center;gap:8px;min-width:0}',
      '.ev3-layer.off .ev3-lop{opacity:.45},.ev3-layer.off .ev3-lsw{filter:grayscale(.8)}',  // axe: never dim text (contrast)
      '.ev3-lsw{display:flex;align-items:center;gap:6px;flex:none}',
      '.ev3-lcol{width:22px;height:18px;padding:0;border:1px solid var(--border);border-radius:4px;' +
        'background:none;cursor:pointer}',
      '.ev3-lname{font-size:11.5px;max-width:16ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.ev3-lop{width:90px;flex:none}',
      '.ev3-lopv{font-size:10.5px;color:var(--text-dim);width:3.5ch;flex:none;font-variant-numeric:tabular-nums}',
      '.ev3-lsum{font-size:10.5px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.ev3-layer.dis .ev3-lsw,.ev3-layer.dis .ev3-lop{opacity:.55}',
      '.ev3-slider{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0}',
      '.ev3-srange{width:min(220px,36vw)}',
      '.ev3-readout{font-size:11.5px;color:var(--text-dim);font-variant-numeric:tabular-nums;white-space:nowrap}',
      '.ev3-cbrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:2px 2px 4px;padding-bottom:18px}',
      '.ev3-cb{position:relative;width:min(300px,60vw)}',
      '.ev3-cb canvas{display:block;width:100%;height:12px;border:1px solid var(--border);border-radius:6px}',
      '.ev3-cb .tick{position:absolute;top:15px;font-size:10px;color:var(--text-dim);' +
        'transform:translateX(-50%);font-variant-numeric:tabular-nums}',
      '.ev3-cb .tick.key{color:var(--text);font-weight:700;white-space:nowrap}',
      '.ev3-cb-units{font-size:11px;color:var(--text-dim)}',
      '.ev3-cb-label{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em}',
      '.ev3-panel .leaflet-control-attribution{font-size:9px}',
      '.ev3-cb-note{font-size:11px;font-style:italic;color:var(--text-dim);max-width:60ch}',
      '.ev3-panel.ev3-fs{position:fixed;inset:16px;z-index:2600;display:flex;flex-direction:column;margin:0}',
      '.ev3-panel.ev3-fs .ev3-map{flex:1;height:auto;min-height:0}',
      '.ev3-panel.ev3-fs .leaflet-container{font:inherit}',
      /* B (owner 2026-11-07, heatwave_reference.png): single-tier categorical
         heat/freeze overlay — crisp cell edges (no bilinear), white label
         chip near the event, bottom bar (valid time left, one-swatch legend
         right). Tier colors are theme tokens; canvas fills need literal
         colors, so keep these in sync via css(). */
      'html{--tier-heat:#d9663c;--tier-freeze:#2171b5}',
      'html[data-theme="dark"]{--tier-heat:#f0895c;--tier-freeze:#6ea3dd}',
      '.ev3-crisp{image-rendering:pixelated}',
      '.ev3-namechip{background:var(--bg-panel);color:var(--text);border:1px solid var(--border);' +
        'border-radius:8px;box-shadow:var(--shadow);font:inherit;font-size:12.5px;font-weight:600;' +
        'padding:4px 10px;white-space:nowrap}',
      '.ev3-namechip.leaflet-tooltip-top:before{border-top-color:var(--border)}',
      '.ev3-namechip.leaflet-tooltip-bottom:before{border-bottom-color:var(--border)}',
      '.ev3-bbar{display:flex;justify-content:space-between;align-items:center;gap:10px;' +
        'margin:6px 2px 0;font-size:11px;color:var(--text-dim);background:var(--bg-inset);' +
        'border:1px solid var(--border);border-radius:8px;padding:5px 10px}',
      '.ev3-bbar .bbar-r{display:flex;align-items:center;gap:6px;white-space:nowrap}',
      '.ev3-swatch{width:12px;height:12px;border-radius:3px;border:1px solid rgba(127,127,127,.45);' +
        'display:inline-block;flex:none}',
    ].join('\n');
    document.head.appendChild(s);
  }

  /* ---- field canvas: bilinear upsample at 2× the grid resolution ---- */
  function fieldDataURL(grid, ny, nx, ramp, vmin, vmax, mode, fillRGB) {
    // mode: 'ramp' — below vmin transparent (consec); 'rampfull' — clamp (IVT);
    //       'mask' — solid ramp colour, alpha = value (daily mask);
    //       'fill' — solid fillRGB at alpha 0.18×value (overlap-fill toggle)
    const W = nx * 2, H = ny * 2;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d');
    const img = g.createImageData(W, H), d = img.data;
    const sample = function (gx, gy) {
      // bilinear over cell centres with null- neighbour weight redistribution
      const i0 = Math.floor(gx), j0 = Math.floor(gy);
      const fx = gx - i0, fy = gy - j0;
      const pts = [[i0, j0, (1 - fx) * (1 - fy)], [i0 + 1, j0, fx * (1 - fy)],
                   [i0, j0 + 1, (1 - fx) * fy], [i0 + 1, j0 + 1, fx * fy]];
      let sw = 0, sv = 0;
      for (let k = 0; k < 4; k++) {
        const i = pts[k][0], j = pts[k][1];
        if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
        const v = grid[j][i];
        if (v === null || v === undefined || !isFinite(v)) continue;
        sw += pts[k][2]; sv += pts[k][2] * v;
      }
      return sw > 0 ? sv / sw : null;
    };
    for (let r = 0; r < H; r++) {
      // canvas row 0 = north edge; grid rows ascend with latitude
      const gy = (ny - 0.5) - (r + 0.5) / H * ny;
      let col = r * W * 4;
      for (let cc = 0; cc < W; cc++) {
        const gx = (cc + 0.5) / W * nx - 0.5;
        const v = sample(gx, gy);
        let t = 0, a = 0, rgb = null;
        if (v !== null) {
          if (mode === 'mask') { t = 0.82; a = Math.max(0, Math.min(1, v)) * 255; }
          else if (mode === 'fill') { a = Math.max(0, Math.min(1, v)) * 0.18 * 255; rgb = fillRGB; }
          else if (mode === 'rampfull') { t = vmax > vmin ? (v - vmin) / (vmax - vmin) : 0; a = 255; }
          else if (v >= vmin) { t = vmax > vmin ? (v - vmin) / (vmax - vmin) : 1; a = 255; }
        }
        if (a <= 0) { d[col] = d[col + 1] = d[col + 2] = d[col + 3] = 0; col += 4; continue; }
        if (!rgb) rgb = rampRGB(ramp, t);
        d[col++] = rgb[0]; d[col++] = rgb[1]; d[col++] = rgb[2]; d[col++] = a;
      }
    }
    g.putImageData(img, 0, 0);
    return c.toDataURL('image/png');
  }

  /* ---- single-tier categorical raster (owner 2026-11-07 heat/freeze look):
     cells at/beyond the percentile in ONE color, everything else fully
     transparent; drawn at 8 px/cell with a hairline cell edge and displayed
     pixelated, so cell edges stay crisp at every zoom. ---- */
  function tierDataURL(mask, ny, nx, rgb) {
    const S = 8;
    const c = document.createElement('canvas'); c.width = nx * S; c.height = ny * S;
    const g = c.getContext('2d');
    g.fillStyle = 'rgb(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ')';
    // canvas row r = grid row ny-1-r (row 0 = north edge; grid rows ascend with latitude)
    const at = function (r, i) {
      if (r < 0 || r >= ny || i < 0 || i >= nx) return false;
      const row = mask[ny - 1 - r]; return !!(row && row[i]);
    };
    for (let r = 0; r < ny; r++) {
      for (let i = 0; i < nx; i++) if (at(r, i)) g.fillRect(i * S, r * S, S, S);
    }
    // design guardian 2026-11-08 (heatwave_reference.png): flat fill, ONE thin
    // outline around the region — no per-cell edges (they read as a hatch).
    g.strokeStyle = 'rgba(70,60,55,.55)'; g.lineWidth = 1.5; g.lineCap = 'square';
    g.beginPath();
    for (let r = 0; r < ny; r++) {
      for (let i = 0; i < nx; i++) {
        if (!at(r, i)) continue;
        const x = i * S, y = r * S;
        if (!at(r - 1, i)) { g.moveTo(x, y); g.lineTo(x + S, y); }
        if (!at(r + 1, i)) { g.moveTo(x, y + S); g.lineTo(x + S, y + S); }
        if (!at(r, i - 1)) { g.moveTo(x, y); g.lineTo(x, y + S); }
        if (!at(r, i + 1)) { g.moveTo(x + S, y); g.lineTo(x + S, y + S); }
      }
    }
    g.stroke();
    return c.toDataURL('image/png');
  }

  /* duration raster (overlay contract §2): solid layer colour per cell, the
     consecutive-DAY run rides the ALPHA — bins mirror the front page
     (mergedmap heatRunBin/freezeRunBin): heat 3 / 4 / 5-6 / 7-13 / 14+ with
     nothing under 3 painted (the site-wide minimum-3-day heat rule), freeze
     1 / 2 / 3 / 4-6 / 7+. Alpha steps 115..215 carry the classes here since
     the detail map has a single tier colour per layer. */
  function durAlpha(r, heat) {
    if (heat) return r >= 14 ? 215 : r >= 7 ? 190 : r >= 5 ? 165 : r >= 4 ? 140 : r >= 3 ? 115 : 0;
    return r >= 7 ? 215 : r >= 4 ? 190 : r === 3 ? 165 : r === 2 ? 140 : r === 1 ? 115 : 0;
  }
  function durRasterURL(run, ny, nx, rgb, heat) {
    const S = 8;
    const c = document.createElement('canvas'); c.width = nx * S; c.height = ny * S;
    const g = c.getContext('2d');
    for (let r = 0; r < ny; r++) {
      const row = run[ny - 1 - r];   // canvas row 0 = north edge
      if (!row) continue;
      for (let i = 0; i < nx; i++) {
        const a = durAlpha(row[i] || 0, heat);
        if (!a) continue;
        g.fillStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (a / 255).toFixed(3) + ')';
        g.fillRect(i * S, r * S, S, S);
      }
    }
    return c.toDataURL('image/png');
  }

  /* ---- marching-squares 0/1 mask boundary segments (lat/lon pairs) ---- */
  function maskContour(mask, lat, lon) {
    const ny = mask.length, nx = mask[0].length, segs = [];
    for (let j = 0; j < ny - 1; j++) {
      for (let i = 0; i < nx - 1; i++) {
        const tl = mask[j][i] ? 1 : 0, tr = mask[j][i + 1] ? 1 : 0;
        const bl = mask[j + 1][i] ? 1 : 0, br = mask[j + 1][i + 1] ? 1 : 0;
        const k = (tl << 3) | (tr << 2) | (br << 1) | bl;
        if (k === 0 || k === 15) continue;
        const midLon = (lon[i] + lon[i + 1]) / 2, midLat = (lat[j] + lat[j + 1]) / 2;
        const top = [lat[j], midLon], right = [midLat, lon[i + 1]];
        const bottom = [lat[j + 1], midLon], left = [midLat, lon[i]];
        switch (k) {
          case 1: case 14: segs.push([left, bottom]); break;
          case 2: case 13: segs.push([bottom, right]); break;
          case 3: case 12: segs.push([left, right]); break;
          case 4: case 11: segs.push([top, right]); break;
          case 6: case 9:  segs.push([top, bottom]); break;
          case 7: case 8:  segs.push([top, left]); break;
          case 5:  segs.push([left, bottom], [top, right]); break;
          case 10: segs.push([top, left], [bottom, right]); break;
        }
      }
    }
    return segs;
  }

  /* ---- module-level "currently rendered instance" for teardown ---- */
  let active = null;

  /* ---- nice ticks for the colorbar ---- */
  function niceTicks(vmin, vmax) {
    const raw = (vmax - vmin) / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = 10 * p;
    for (const m of [1, 2, 2.5, 5]) if (m * p >= raw) { step = m * p; break; }
    const t = [];
    for (let v = Math.ceil(vmin / step) * step; v <= vmax + 1e-9; v += step) t.push(+v.toFixed(6));
    if (!t.length || t[0] > vmin + 1e-9) t.unshift(vmin);
    if (t[t.length - 1] < vmax - 1e-9) t.push(vmax);
    return t;
  }
  function tickLabel(v) { return Math.abs(v) >= 100 ? String(Math.round(v)) : String(+v.toFixed(1)); }

  function drawColorbar(canvas, ramp) {
    const dpr = window.devicePixelRatio || 1, W = 280, H = 12;
    canvas.width = W * dpr; canvas.height = H * dpr;
    const g = canvas.getContext('2d'); g.scale(dpr, dpr);
    for (let x = 0; x < W; x++) {
      g.fillStyle = rampColor(ramp, x / (W - 1));
      g.fillRect(x, 0, 1, H);
    }
  }
  /* '#rrggbb' | 'rgb(r, g, b)' → [r,g,b] (fill colours for the overlap toggle) */
  function parseColor(s, dflt) {
    s = String(s || '').trim();
    let m = /^#([0-9a-f]{6})$/i.exec(s);
    if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
    m = /^rgba?\(\s*(\d+)[^0-9]+(\d+)[^0-9]+(\d+)/.exec(s);
    if (m) return [+m[1], +m[2], +m[3]];
    return dflt;
  }

  /* ---- main Leaflet renderer (both event kinds) ---- */
  function renderMaps(host, viz, ev, opts) {
    injectStyles();
    const isAR = viz.kind === 'ar_ivt';
    const ramp = isAR ? RAMPS.ivt : (viz.event_type === 'heat_wave' ? RAMPS.heat : RAMPS.freeze);
    /* A3 + owner 2026-11-08: IVT display floor — cells below 250 kg m⁻¹ s⁻¹
       are fully transparent (basemap shows through); scale domain 250–1000
       capped, matching the paper's blue ramp. The detector threshold
       (viz.ivt_threshold_kg_m_s, 400 by default) is marked on the colorbar. */
    const AR_FLOOR = 250, AR_CAP = 1000;
    const arThr = isAR ? (Number(viz.ivt_threshold_kg_m_s) || 400) : 0;
    /* B (owner 2026-11-07 correction): heat/freeze render a SINGLE tier —
       heat: cells above P85 in one warm color; freeze: cells below P15 in one
       blue. The asset drives it: viz.quantile picks the tier and the ramp. */
    const tierQ = !isAR && Number.isFinite(viz.quantile)
      ? Math.round(viz.quantile * 100) : (viz.event_type === 'freeze' ? 15 : 85);
    const tierCSS = isAR ? '' : css(viz.event_type === 'freeze' ? '--tier-freeze' : '--tier-heat',
      viz.event_type === 'freeze' ? '#2171b5' : '#d9663c');
    const tierLabel = 'P' + tierQ;
    const tierLegend = isAR ? null : {
      color: tierCSS, label: tierLabel,
      tip: (viz.event_type === 'freeze' ? 'daily value below the ' : 'daily value at or above the ')
        + tierQ + 'th-percentile local climatology',
    };
    const dark = isDarkTheme();
    const grid = viz.grid, ny = grid.lat.length, nx = grid.lon.length;
    const cell = grid.cell_deg || Math.abs(grid.lat[1] - grid.lat[0]) || 1;
    const fieldBounds = (function () {
      const s = Math.min(grid.lat[0], grid.lat[ny - 1]), n = Math.max(grid.lat[0], grid.lat[ny - 1]);
      // lon is EAST-ORDERED: a wrapped array (last < first, e.g. 350E→20E) is
      // one continuous eastward window — never min/max (that spans nearly the
      // whole globe and stitches a seam at the wrap meridian, the Oct-3 bug)
      const w = grid.lon[0], e = grid.lon[nx - 1] >= w ? grid.lon[nx - 1] : grid.lon[nx - 1] + 360;
      return [[s - cell / 2, w - cell / 2], [n + cell / 2, e + cell / 2]];
    })();

    const leads = (function () {
      const out = [];
      for (const m in viz.models) { const e = viz.models[m];
        if (e && typeof e === 'object') for (const l in e) if (/^\d+$/.test(l) && out.indexOf(l) < 0) out.push(l); }
      return out.sort(function (a, b) { return +a - +b; });
    })();
    const allModels = Object.keys(viz.models);
    const baselineName = (opts && opts.baseline) || 'ifs_hres';
    function hasDataAt(m, lead) {
      const e = viz.models[m];
      const entry = (e && typeof e === 'object' && !e.status) ? e[lead] : null;
      return !!(entry && entry.status === 'ok');
    }
    /* ---- initial view state (overlay contract §1 + §4, event-view channel)
       visible by default: the baseline plus the next two roster models that
       have data at the initial lead; observed is always a layer. opts.models
       is a scorecard hint and does NOT change visibility here. The anchor day
       (event peak) seeds the time slider; opts.day (host #day=) overrides. ---- */
    const initialLead = pickLead(leads);
    const initialSel = (function () {
      const out = [];
      if (hasDataAt(baselineName, initialLead)) out.push(baselineName);
      allModels.forEach(function (m) {
        if (out.length < 3 && m !== baselineName && hasDataAt(m, initialLead)) out.push(m);
      });
      return out;
    })();
    const anchorDay = (function () {
      const days = (viz.window && viz.window.days) || [];
      let d = viz.obs ? Math.max(0, days.indexOf(viz.obs.anchor_day)) : 0;
      const o = opts && opts.day != null ? Math.round(+opts.day) : NaN;
      if (isFinite(o) && days.length) d = Math.max(0, Math.min(days.length - 1, o));
      return d;
    })();
    /* ---- scaffolding: root, toolbar (lead + time slider), AR colorbar row,
       the single map card block ---- */
    const root = document.createElement('div'); root.className = 'ev3-root';
    host.appendChild(root);
    const toolbar = document.createElement('div'); toolbar.className = 'ev-toolbar';
    root.appendChild(toolbar);
    /* colorbar row (AR only — heat/freeze use the single-swatch tier legend +
       ramp note in the map's bottom bar; owner 2026-11-07 reference) */
    const cbRow = document.createElement('div'); cbRow.className = 'ev3-cbrow';
    if (isAR) {
      const cb = { vmin: AR_FLOOR, vmax: AR_CAP, units: 'kg m\u207b\u00b9 s\u207b\u00b9' };
      const lb = document.createElement('span'); lb.className = 'ev3-cb-label';
      lb.textContent = 'scale';
      const bar = document.createElement('div'); bar.className = 'ev3-cb';
      const cv = document.createElement('canvas'); bar.appendChild(cv);
      drawColorbar(cv, ramp);
      // Owner 2026-11-08: AR ticks fixed at 250/400/600/800/1000 with the
      // 400 detector threshold marked 'AR mask ≥400'.
      [250, 400, 600, 800, 1000].forEach(function (v) {
        const pct = 100 * (v - cb.vmin) / (cb.vmax - cb.vmin);
        const s = document.createElement('span'); s.className = 'tick';
        s.style.left = pct + '%';
        if (Math.abs(v - arThr) < 1) {
          s.classList.add('key');
          s.textContent = v + '·mask';
          s.title = 'AR mask threshold: IVT \u2265 ' + v + ' kg m\u207b\u00b9 s\u207b\u00b9';
        } else s.textContent = tickLabel(v);
        bar.appendChild(s);
      });
      const u = document.createElement('span'); u.className = 'ev3-cb-units';
      u.textContent = cb.units;
      cbRow.appendChild(lb); cbRow.appendChild(bar); cbRow.appendChild(u);
    }
    const obsBlock = document.createElement('div'); obsBlock.className = 'ev3-obsblock';
    const ui = { lead: initialLead, day: isAR ? null : anchorDay, sel: initialSel.slice() };
    const dur = document.createElement('div'); dur.className = 'ev-dur';
    root.appendChild(cbRow);
    if (!isAR) cbRow.style.display = 'none';   // B: single-swatch tier legend replaces the ramp scale
    root.appendChild(obsBlock);
    if (!isAR) root.appendChild(dur);   // §6 KEEP: cross-model duration bars

    /* view box: event box (bbox if available) padded ×1.9 */
    const box = (function () {
      if (ev && Array.isArray(ev.bbox) && ev.bbox.length === 4) {
        const [a, b, c, d] = ev.bbox.map(Number);
        // a world-wide band (lon span ≥ 300° with lon_min ≤ lon_max) is a degenerate antimeridian
        // box, not an event extent: fall through to the field bounds instead of drawing/fitting it
        if ([a, b, c, d].every(isFinite) && !(c <= d && d - c >= 300)) {
          // antimeridian-crossing box (c > d): pad in CONTINUOUS eastward
          // coords so fitBounds shows the whole crossing window in one view
          // (Leaflet renders lng > 180 natively); the dashed event box itself
          // still draws as two rectangles split at ±180.
          const span = (d >= c ? d - c : d + 360 - c);
          const padLat = (b - a) * 0.45, padLon = span * 0.45;
          if (c <= d) return { rect: [[a - padLat, c - padLon], [b + padLat, d + padLon]],
                               rects: [[[a, c], [b, d]]] };
          // detail view is continuous across 180, so the dashed box is ONE
          // continuous rect — Leaflet vector layers do not repeat across
          // world copies, and the old two-box split drew the east half off-
          // view. (The home map keeps the [-180,180] split; that view is
          // whole-world.)
          return { rect: [[a - padLat, c - padLon], [b + padLat, d + 360 + padLon]],
                   rects: [[[a, c], [b, d + 360]]] };
        }
      }
      return { rect: fieldBounds, rects: null };
    })();

    /* B: white label chip text + anchor — centroid of the observed occupied
       cells (stable while scrubbing valid time), matching the reference's
       'Heatwave #<id>' chip. */
    const chip = (function () {
      if (!ev || !ev.id || !viz.grid) return null;
      const name = viz.event_type === 'heat_wave' ? 'Heatwave'
        : viz.event_type === 'freeze' ? 'Freeze' : 'Atmospheric river';
      let lat = 0, lon = 0, k = 0, anchor = null;
      try {
        let rows = isAR
          ? (viz.obs && viz.obs.mask ? unpackMask(viz.obs.mask, ny) : null)
          : (viz.obs && viz.obs.consec ? unpackU8(viz.obs.consec) : null);
        if (!rows && viz.obs && viz.obs.daily) {
          // schema 3: occupancy = union of the per-day tier masks
          const d = viz.obs.daily;
          rows = [];
          for (let j = 0; j < ny; j++) {
            let any = 0;
            for (let r = 0; r < d.length; r++) {
              const row = d[r] && d[r][j];
              if (row && row.indexOf('1') >= 0) { any = 1; break; }
            }
            rows.push(any);
          }
        }
        if (!rows) return null;
        for (let j = 0; j < ny; j++) {
          const row = rows[j]; if (!row) continue;
          for (let i = 0; i < nx; i++) {
            if (!(isAR || viz.obs.consec ? row[i] > 0 : row[i])) continue;
            let lo = grid.lon[i];
            if (anchor === null) anchor = lo;
            while (lo - anchor > 180) lo -= 360;
            while (anchor - lo > 180) lo += 360;
            lat += grid.lat[j]; lon += lo; k += 1;
          }
        }
      } catch (e) { return null; }
      if (!k) return null;
      // G-35: anchor on the TOP EDGE (mid-longitude) of the event box so the label sits
      // outside the exceedance (it used to cover the field centre) and clear of the zoom
      // control; centroid when there is no box
      const nw = box.rects ? [box.rects[0][1][0], (box.rects[0][0][1] + box.rects[0][1][1]) / 2] : null;
      return { label: name + ' #' + String(ev.id).slice(0, 6), at: nw || [lat / k, lon / k] };
    })();


    /* ---- instance bookkeeping ---- */
    const maps = [];                 // live Leaflet maps (teardown + sync)
    let syncBusy = false, fsPanel = null, attrUsed = false;
    const gratColor = css('--text-dim', dark ? '#8a93a6' : '#5b6478');

    function onEsc(e) { if (e.key === 'Escape' && fsPanel) setFs(null); }
    document.addEventListener('keydown', onEsc);

    active = {
      maps: maps,
      teardown: function () {
        const stop = active && active.stopFn;   // play interval must die with the detail
        if (stop) stop();
        document.removeEventListener('keydown', onEsc);
        while (maps.length) { const m = maps.pop(); try { m.remove(); } catch (err) { /* detached */ } }
      },
    };

    /* pad an event rect by f of its span each side (array [[s,w],[n,e]]
       or LatLngBounds); clamped, minimum 4° so point boxes still zoom in */
    function padRect(r, f) {
      let sw, ne, we, ea;
      if (typeof r.getSouth === 'function') { sw = r.getSouth(); ne = r.getNorth(); we = r.getWest(); ea = r.getEast(); }
      else { sw = r[0][0]; we = r[0][1]; ne = r[1][0]; ea = r[1][1]; }
      const dLat = Math.max((ne - sw) * f, 4), dLon = Math.max((ea - we) * f, 4);
      return L.latLngBounds([Math.max(sw - dLat, -84), we - dLon],
                            [Math.min(ne + dLat, 84), ea + dLon]);
    }

    /* ---- tiles: theme-matched key-free basemap, one-shot OSM fallback.
       (CARTO basemaps now serve 'API KEY REQUIRED' watermarked tiles to
       keyless clients — verified 2026-11; Esri gray base renders clean.) ---- */
    function addTiles(map) {
      const esri = L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_' +
        (dark ? 'Dark' : 'Light') + '_Gray_Base/MapServer/tile/{z}/{y}/{x}',
        { maxZoom: 20,
          attribution: 'Powered by Esri | Sources: Esri, Garmin, GEBCO, NOAA, USGS, and the GIS User Community' });
      let swapped = false;
      esri.on('tileerror', function () {
        if (swapped || !map.hasLayer(esri)) return;
        swapped = true;
        map.removeLayer(esri);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
      });
      esri.addTo(map);
    }

    /* ---- graticule: faint 10° lines over the current view ---- */
    function drawGraticule(map, group) {
      group.clearLayers();
      const b = map.getBounds();
      const sty = { color: gratColor, weight: 0.6, opacity: 0.18, interactive: false };
      for (let la = Math.ceil(b.getSouth() / 10) * 10; la <= b.getNorth(); la += 10)
        L.polyline([[la, b.getWest()], [la, b.getEast()]], sty).addTo(group);
      for (let lo = Math.ceil(b.getWest() / 10) * 10; lo <= b.getEast(); lo += 10)
        L.polyline([[b.getSouth(), lo], [b.getNorth(), lo]], sty).addTo(group);
    }

    /* ---- fullscreen overlay ---- */
    function setFs(panel) {
      if (fsPanel && fsPanel === panel) {          // toggle the open one closed
        const p = fsPanel; fsPanel = null;
        p.el.classList.remove('ev3-fs');
        requestAnimationFrame(function () { p.map.invalidateSize(); });
        return;
      }
      if (fsPanel) {                               // switch: close current first
        const p = fsPanel; fsPanel = null;
        p.el.classList.remove('ev3-fs');
        requestAnimationFrame(function () { p.map.invalidateSize(); });
      }
      if (!panel) return;
      panel.el.classList.add('ev3-fs');
      fsPanel = panel;
      requestAnimationFrame(function () { requestAnimationFrame(function () { panel.map.invalidateSize(); }); });
    }

    /* ---- panel factory + shared-view wiring ---- */
    function makePanel(opts) {
      const el = document.createElement('div');
      el.className = 'ev3-panel' + (opts.obs ? ' ev3-obs' : '');
      el.innerHTML = '<div class="ev3-head"><b>' + esc(opts.title) + '</b>' +
        '<button class="ev3-fsbtn" type="button" title="zoom to fullscreen">⤢</button></div>';
      const mapEl = document.createElement('div'); mapEl.className = 'ev3-map';
      const layersEl = document.createElement('div'); layersEl.className = 'ev3-layers';
      /* B: bottom bar — 'Valid time: …' left, single-swatch tier legend right
         (hidden until setBar; AR panels keep the continuous colorbar instead). */
      const bar = document.createElement('div'); bar.className = 'ev3-bbar'; bar.style.display = 'none';
      const barL = document.createElement('span'); barL.className = 'bbar-l';
      const barR = document.createElement('span'); barR.className = 'bbar-r';
      bar.appendChild(barL); bar.appendChild(barR);
      el.appendChild(layersEl); el.appendChild(mapEl); el.appendChild(bar);
      obsBlock.appendChild(el);

      const map = L.map(mapEl, {
        worldCopyJump: true, zoomControl: true, scrollWheelZoom: true,
        attributionControl: !attrUsed, minZoom: 2, maxZoom: 10,
        zoomSnap: 0.25, zoomDelta: 0.5,   // G-34: integer zoom left events filling 25–40% of the panel
      });
      attrUsed = true;
      addTiles(map);
      // A4 (QA 2026-11-06 → owner 2026-11-07): fit the OCCUPIED footprint with
      // modest pixel padding so the event fills most of the panel; the z4/×1.9
      // combos read as 'hemisphere' at panel scale.
      const rawBox = box.rects ? box.rects[0] : box.rect;
      if (opts.obs) map.fitBounds(occFit(viz, rawBox), { animate: false, maxZoom: 7, padding: [16, 16] });
      else map.setView(maps[0] ? maps[0].getCenter() : [0, 0], maps[0] ? maps[0].getZoom() : 3);

      const grat = L.layerGroup().addTo(map);
      drawGraticule(map, grat);
      if (box.rects) box.rects.forEach(function (r) {
        L.rectangle(r, { color: css('--ink-secondary', '#6e6e60'), weight: 1.2, dashArray: '4 3', fill: false, interactive: false }).addTo(map);  // furniture, not data (design guardian G-26)
      });
      // B: white rounded label chip near the event ('Heatwave #119e56').
      // VERIFIED against this vendor build (1.1.1 label, 1.2-era code):
      // Map.openTooltip(content, latlng, options) — overlay =
      // new Tooltip(opts3).setContent(arg1).setLatLng(arg2). Passing the
      // LatLng first made setContent(LatLng) → appendChild(LatLng) throw,
      // which is the real cause of the "AR page shows no data" report.
      if (opts.chip && opts.chipAt) {
        map.openTooltip(String(opts.chip), opts.chipAt, { permanent: true, direction: 'top',
          offset: [0, -4], className: 'ev3-namechip' });
      }

      map.on('moveend', function () {
        drawGraticule(map, grat);
        if (syncBusy) return;
        syncBusy = true;
        const c = map.getCenter(), z = map.getZoom();
        for (let k = 0; k < maps.length; k++) if (maps[k] !== map) {
          maps[k].setView(c, z, { animate: false });
        }
        syncBusy = false;
      });
      el.querySelector('.ev3-fsbtn').onclick = function () { setFs(fsPanel === panel ? null : panel); };

      const panel = { el: el, map: map, layersEl: layersEl, overlays: [] };
      maps.push(map);
      panel.clearOverlays = function () {
        panel.overlays.forEach(function (o) { map.removeLayer(o); });
        panel.overlays = [];
      };
      panel.setBar = function (left, legend, tip) {
        if (!left && !legend) { bar.style.display = 'none'; return; }
        bar.style.display = '';
        barL.textContent = left || '';
        barL.title = tip || '';
        barR.innerHTML = '';
        if (legend) {
          const sw = document.createElement('i'); sw.className = 'ev3-swatch';
          sw.style.background = legend.color;
          const t = document.createElement('span'); t.textContent = legend.label;
          barR.appendChild(sw); barR.appendChild(t);
        }
        barR.title = (legend && legend.tip) || '';
      };
      return panel;
    }

    function contourStyle(color, dash) {
      return { color: color, weight: 1.5, opacity: 0.95, fill: false, interactive: false,
               dashArray: dash || null, lineJoin: 'round' };
    }

    /* ---- overlay layers (briefs/event_view_overlay.md): the one map. The
       observed field is the base; every model with data at the selected lead
       stacks as its own overlay pane with a visibility toggle, colour and
       opacity. Layer state serializes through EventViz.onLayers (host → URL
       #lyr=) so a pasted link reproduces the same view. ---- */
    let arObsMask = isAR && viz.obs ? unpackMask(viz.obs.mask, ny) : null;
    let obsPanel = null;
    const savedLayers = parseLayerState((opts && opts.layers) || '');
    const layers = {};                   // 'obs' | model -> layer; insertion order = stack order
    const tuned = {};   // user layer edits survive lead changes (ponytail 17:45)

    function parseLayerState(s) {
      const out = {};
      String(s || '').split(',').forEach(function (row) {
        const p = String(row).split('|');
        if (p.length !== 4) return;
        const st = { on: p[3] === '1' };
        if (/^[0-9a-f]{6}$/i.test(p[1])) st.color = '#' + p[1].toLowerCase();
        const op = +p[2];
        if (isFinite(op)) st.op = Math.max(0, Math.min(100, Math.round(op)));
        if (st.color || st.op !== undefined) out[decodeURIComponent(p[0])] = st;
      });
      return out;
    }
    // AR obs contour follows theme text: white-on-white was invisible on the light basemap
    function defColor(k) { return k === 'obs' ? (isAR ? css('--text', '#111') : tierCSS) : modelBarColor(k); }
    function mkLayer(k) {
      const s = Object.assign({}, savedLayers[k], tuned[k]);   // live edits beat URL/defaults
      const pane = 'ev3l-' + k.replace(/[^a-z0-9_-]/gi, '-');
      obsPanel.map.createPane(pane);
      obsPanel.map.getPane(pane).style.zIndex = k === 'obs' ? '410' : '420';
      return { key: k, pane: pane,
        label: k === 'obs' ? (isAR ? 'Observed IVT' : 'Observed') : k,
        color: s.color || defColor(k),
        on: s.on !== undefined ? s.on : (k === 'obs' ? true : initialSel.indexOf(k) >= 0),
        op: s.op !== undefined ? s.op : (k === 'obs' ? 85 : 50),
        ovs: [] };
    }
    function serializeLayers() {
      return Object.keys(layers).map(function (k) {
        const L = layers[k];
        return k + '|' + String(L.color).replace('#', '') + '|' + L.op + '|' + (L.on ? 1 : 0);
      }).join(',');
    }
    let customized = Object.keys(savedLayers).length > 0;   // a restored #lyr= view is already custom (ponytail 00:10)
    function saveState() {
      Object.keys(layers).forEach(function (k) {
        const L = layers[k]; tuned[k] = { on: L.on, color: L.color, op: L.op };
      });
      notifyModelsHook(ui.sel);
      try {
        if (window.EventViz && window.EventViz.onLayers) window.EventViz.onLayers(customized ? serializeLayers() : '');
        if (window.EventViz && window.EventViz.onDay) window.EventViz.onDay(
          (!isAR && ui.day !== anchorDay) ? ui.day : '');
      } catch (err) { /* host hooks must never break the viz */ }
    }
    function modelEntry(k) {
      const e = viz.models[k];
      return (e && typeof e === 'object' && !e.status) ? e[ui.lead] : null;
    }
    function grp(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
    function noDataReason(k) {
      const e = viz.models[k];
      return reasonOf((typeof e === 'string' ? e : (e && e.status)) || 'no data at this lead');
    }
    function layerSummary(L) {
      if (isAR) {
        if (L.key === 'obs') return viz.obs ? 'covers ' + grp(viz.obs.area_cells) + ' cells' : 'no observations (post-cutoff)';
        const e = modelEntry(L.key); if (!e) return noDataReason(L.key);
        return 'covers ' + grp(e.area_cells || 0) + ' cells' +
          (viz.obs ? ' (obs ' + grp(viz.obs.area_cells) + ')' : '');
      }
      if (L.key === 'obs') return viz.obs ? 'covers ' + grp(viz.obs.footprint_cells) +
        ' cells \u00b7 runs ' + viz.obs.max_consec + ' d' : '';
      const e = modelEntry(L.key); if (!e) return noDataReason(L.key);
      return 'covers ' + grp(e.footprint_cells) + ' cells (obs ' + grp(viz.obs.footprint_cells) +
        ') \u00b7 runs ' + e.max_consec + ' d (obs ' + viz.obs.max_consec + ' d)';
    }
    function layerRows() {
      const hostEl = obsPanel && obsPanel.layersEl;
      if (!hostEl) return;
      hostEl.innerHTML = '';
      ['obs'].concat(allModels).forEach(function (k) {
        const L = layers[k];   // absent when the model has no data at this lead
        const name = L ? L.label : (k === 'obs' ? (isAR ? 'Observed IVT' : 'Observed') : k);
        const row = document.createElement('div');
        row.className = 'ev3-layer' + (L && L.on ? '' : ' off') + (L ? '' : ' dis');
        const lbl = document.createElement('label');
        lbl.className = 'ev3-lsw';
        lbl.innerHTML = '<input type="checkbox" class="ev3-lvis"' + (L && L.on ? ' checked' : '') +
          ' aria-label="show layer ' + esc(name) + '"' + (L ? '' : ' disabled') + '>' +
          '<input type="color" class="ev3-lcol" value="' + (L ? L.color : '#808080') +
          '" aria-label="colour of layer ' + esc(name) + '"' + (L ? '' : ' disabled') + '>' +
          '<b class="ev3-lname">' + esc(name) + '</b>';
        row.appendChild(lbl);
        const rng = document.createElement('input');
        rng.type = 'range'; rng.className = 'ev3-lop'; rng.min = '0'; rng.max = '100'; rng.step = '5';
        rng.value = String(L ? L.op : 50);
        rng.setAttribute('aria-label', 'opacity of layer ' + name + ', percent');
        if (!L) rng.disabled = true;
        const pct = document.createElement('output'); pct.className = 'ev3-lopv';
        pct.textContent = (L ? L.op : 0) + '%';
        const sum = document.createElement('span'); sum.className = 'ev3-lsum';
        sum.textContent = L ? layerSummary(L)
          : (k === 'obs' ? 'no observations (post-cutoff)' : noDataReason(k));
        row.appendChild(rng); row.appendChild(pct); row.appendChild(sum);
        if (L) {
          lbl.querySelector('.ev3-lvis').addEventListener('change', function (ev2) {
            L.on = ev2.target.checked; customized = true;
            row.classList.toggle('off', !L.on);
            ui.sel = Object.keys(layers).filter(function (x) { return x !== 'obs' && layers[x].on; });
            paintAll(); saveState();
          });
          lbl.querySelector('.ev3-lcol').addEventListener('input', function (ev2) {
            L.color = ev2.target.value; customized = true;
            paintLayer(L); paintBar(); saveState();
          });
          rng.addEventListener('input', function () {
            L.op = +rng.value; pct.textContent = L.op + '%'; customized = true;
            // raster PNG is opacity-independent: nudge overlays, don't re-encode (ponytail 17:45)
            L.ovs.forEach(function (o) { if (o.setOpacity) o.setOpacity(L.op / 100); });
            paintBar(); saveState();
          });
        }
        hostEl.appendChild(row);
      });
    }
    /* per-cell trailing run R(k) = R(k-1)+1 while the day-k bit is on, else 0
       (contract §2: the same rule as the front page; runs truncate at
       window.days[0] — accepted cut, same as mergedmap). */
    function layerRuns(lay) {
      if (lay._runsLead === ui.lead && 'cache' in lay) return lay.cache;
      const days = lay.key === 'obs' ? (viz.obs && viz.obs.daily) : (modelEntry(lay.key) || {}).daily;
      lay._runsLead = ui.lead;
      let out = null;
      if (days && days.length) {
        out = [];
        let prev = null;
        for (let d = 0; d < days.length; d++) {
          const bit = unpackMask(days[d], ny);
          const run = bit.map(function (row, j) {
            const pr = prev ? prev[j] : null;
            return row.map(function (b, i) { return b ? 1 + (pr ? pr[i] : 0) : 0; });
          });
          out.push(run); prev = run;
        }
      }
      lay.cache = out;
      return out;
    }
    function addImg(lay, url, alpha, cls) {
      return L.imageOverlay(url, fieldBounds,
        { opacity: alpha, interactive: false, className: cls, pane: lay.pane }).addTo(obsPanel.map);
    }
    function paintLayer(lay) {
      if (!obsPanel) return;
      lay.ovs.forEach(function (o) { obsPanel.map.removeLayer(o); });
      lay.ovs = [];
      if (!lay.on || lay.op <= 0) return;
      const alpha = lay.op / 100;
      const rgb = parseColor(lay.color, [127, 127, 127]);
      if (isAR) {
        if (lay.key === 'obs') {
          if (!viz.obs || !viz.obs.ivt) return;
          lay.ovs.push(addImg(lay, fieldDataURL(viz.obs.ivt, ny, nx, RAMPS.ivt, AR_FLOOR, AR_CAP, 'ramp'), alpha, 'ev3-field'));
          if (arObsMask) maskContour(arObsMask, grid.lat, grid.lon).forEach(function (s) {
            const st = contourStyle(lay.color); st.pane = lay.pane;
            lay.ovs.push(L.polyline(s, st).addTo(obsPanel.map));
          });
        } else {
          const e = modelEntry(lay.key); if (!e || !e.mask) return;
          const st = contourStyle(lay.color); st.weight = 2; st.opacity = alpha; st.pane = lay.pane;
          maskContour(unpackMask(e.mask, ny), grid.lat, grid.lon).forEach(function (s) {
            lay.ovs.push(L.polyline(s, st).addTo(obsPanel.map));
          });
        }
      } else {
        const runs = layerRuns(lay); if (!runs) return;
        const run = runs[Math.max(0, Math.min(ui.day == null ? runs.length - 1 : ui.day, runs.length - 1))];
        lay.ovs.push(addImg(lay, durRasterURL(run, ny, nx, rgb, viz.event_type === 'heat_wave'), alpha, 'ev3-crisp'));
      }
    }
    function paintBar() {
      if (!obsPanel) return;
      if (isAR) { obsPanel.setBar(null, null); return; }
      const legend = { color: layers.obs ? layers.obs.color : tierLegend.color,
                       label: tierLegend.label, tip: tierLegend.tip };
      const heat = viz.event_type === 'heat_wave';
      obsPanel.setBar((heat ? 'Heat \u2014 days > ' : 'Freeze \u2014 days < ') + 'P' + tierQ, legend,
        'Consecutive UTC days the cell has exceeded the P' + tierQ + ' climatology at all four ' +
        '6-hourly steps; darkest = ' + (heat ? '14+ days (runs under 3 days are not painted)' : '7+ days') + '.');
    }
    function paintAll() {
      Object.keys(layers).forEach(function (k) { paintLayer(layers[k]); });
      paintBar();
      if (!isAR && viz.obs) drawDur();
    }
    /* duration-vs-observed bars (kept per contract §6): the currently VISIBLE
       model set — same roster the layer list shows as checked */
    function drawDur() {
      let html = '<div class="ev-dur-title">duration vs observed \u00b7 lead ' + ui.lead + 'h</div>';
      const obsN = viz.obs.max_consec;
      const vals = [{ label: 'observed', n: obsN, c: css('--text-dim', '#5b6478') }];
      Object.keys(layers).forEach(function (k) {
        if (k === 'obs' || !layers[k].on) return;
        const entry = modelEntry(k);
        if (entry && entry.status === 'ok') vals.push({ label: k, n: entry.max_consec, c: layers[k].color });
      });
      const maxN = Math.max(7, obsN + 2);
      vals.forEach(function (v, i) {
        const d = i === 0 ? '' : ' <b class="ev-bar-d">\u0394 ' + (v.n - obsN >= 0 ? '+' : '') + (v.n - obsN) + ' d vs obs</b>';
        html += bar(v.label, v.n, maxN, v.c, d, true, obsN);
      });
      dur.innerHTML = html + (() => {
        let r = '<div class="ev-bar-ruler"><span></span><span class="rtrack">';
        for (let t = 0; t <= maxN; t++) r += '<b class="rtick" style="left:' + (100 * t / maxN) + '%">' + t + '</b>';
        return r + '</span><span></span></div>';
      })();
      dur.style.display = '';
    }
    function rebuildLayers() {
      Object.keys(layers).forEach(function (k) {
        if (k !== 'obs' && !hasDataAt(k, ui.lead)) {
          layers[k].ovs.forEach(function (o) { obsPanel.map.removeLayer(o); });
          obsPanel.map.getPane(layers[k].pane).remove();
          delete layers[k];
        }
      });
      if (viz.obs && !layers.obs) layers.obs = mkLayer('obs');
      allModels.forEach(function (m) { if (hasDataAt(m, ui.lead) && !layers[m]) layers[m] = mkLayer(m); });
      ui.sel = Object.keys(layers).filter(function (k) { return k !== 'obs' && layers[k].on; });
      layerRows();
    }

    /* ---- toolbar ---- */
    if (leads.length) {
      const lab = document.createElement('span'); lab.className = 'ev-tool-label';
      lab.textContent = 'forecast lead'; toolbar.appendChild(lab);
      leads.forEach(function (l) {
        const b = document.createElement('button');
        b.className = 'mini ev-lead' + (l === ui.lead ? ' on' : '');
        b.type = 'button';
        b.textContent = (+l) + 'h';
        b.onclick = function () { ui.lead = l; notifyLeadHook(l); refreshLead(); };
        toolbar.appendChild(b);
      });
    }
    /* time slider (contract §4, mimics the front-page raw slider): play/pause,
       step, range over window.days, readout — ONE day index drives every layer
       (obs and model streams are valid-time indexed at the same day k). */
    let sliderSync = function () {};
    if (!isAR && viz.obs && viz.window && viz.window.days.length) {
      const sl = document.createElement('div'); sl.className = 'ev3-slider'; toolbar.appendChild(sl);
      const play = document.createElement('button');
      play.type = 'button'; play.className = 'mini ev3-play'; play.textContent = '\u25b6';
      play.setAttribute('aria-label', 'play valid-time animation');
      const prevB = document.createElement('button');
      prevB.type = 'button'; prevB.className = 'mini ev3-prev'; prevB.textContent = '\u2039';
      prevB.setAttribute('aria-label', 'step to previous day');
      const nextB = document.createElement('button');
      nextB.type = 'button'; nextB.className = 'mini ev3-next'; nextB.textContent = '\u203a';
      nextB.setAttribute('aria-label', 'step to next day');
      const rng = document.createElement('input');
      rng.type = 'range'; rng.className = 'ev3-srange';
      rng.min = '0'; rng.max = String(viz.window.days.length - 1); rng.step = '1';
      rng.value = String(ui.day); rng.setAttribute('aria-label', 'valid time');
      const ro = document.createElement('span'); ro.className = 'ev3-readout';
      let timer = null;
      function readout() {
        ro.textContent = 'Valid time ' + fmtDay(viz.window.days[ui.day]) +
          ' \u00b7 models T+' + (+ui.lead) + ' h';
      }
      function stopPlay() {
        if (!timer) return;
        clearInterval(timer); timer = null;
        play.textContent = '\u25b6'; play.setAttribute('aria-label', 'play valid-time animation');
      }
      function stepDay(d2) {
        const n = viz.window.days.length;
        ui.day = (ui.day + d2 + n) % n;   // wraps at the end (rawmap lesson: repaint synchronously)
        readout(); paintAll(); saveState();
      }
      play.onclick = function () {
        if (timer) { stopPlay(); return; }
        play.textContent = '\u23f8'; play.setAttribute('aria-label', 'pause valid-time animation');
        timer = setInterval(function () { stepDay(1); }, 500);   // ~2 days/s
      };
      prevB.onclick = function () { stopPlay(); stepDay(-1); };
      nextB.onclick = function () { stopPlay(); stepDay(1); };
      rng.addEventListener('input', function () {
        stopPlay(); ui.day = +rng.value; readout(); paintAll(); saveState();
      });
      [play, prevB, nextB, rng, ro].forEach(function (x) { sl.appendChild(x); });
      readout();
      sliderSync = readout;   // lead text changes with the lead
      active.stopFn = stopPlay;   // teardown must kill the play interval (ponytail 00:10)
    }

    function refreshLead() {
      toolbar.querySelectorAll('.ev-lead').forEach(function (b) {
        b.classList.toggle('on', b.textContent === ui.lead + 'h'); });
      rebuildLayers();
      paintAll();
      sliderSync();
      saveState();   // layer set can change with lead -> URL follows
    }

    /* ---- build ---- */
    obsPanel = makePanel({ obs: true, title: 'Observed + model overlays',
      chip: chip ? chip.label : null, chipAt: chip ? chip.at : null });
    rebuildLayers();
    paintAll();
    notifyLeadHook(ui.lead);   // sync host (deep link + re-render state) with the lead we picked

    /* post-layout refit (QA 2026-11-06): maps created while the freshly
       mounted container had zero layout size fit to a degenerate view —
       refit the map once real sizes exist */
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      maps.forEach(function (m) { m.invalidateSize({ animate: false }); });
      if (maps[0]) {
        const raw = box.rects ? box.rects[0] : box.rect;
        maps[0].fitBounds(occFit(viz, raw), { animate: false, maxZoom: 7, padding: [16, 16] });
      }
      window.__ev3 = { maps: maps, boxRect: box.rect, layers: serializeLayers };   // QA/test handle
    }); });
  }

  /* ================================================================
   * Legacy canvas renderers — fallback when window.L is unavailable
   * ================================================================ */
  function drawField(canvas, grid, lat, lon, opt) {
    // grid: 2D array [lat][lon] (numbers|null); opt: {ramp, vmax, vmin, mask, overlay, contour, title}
    const ny = lat.length, nx = lon.length;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth || 300, H = Math.max(120, Math.round(W * ny / nx));
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.height = H + 'px';
    const g = canvas.getContext('2d');
    g.scale(dpr, dpr);
    g.clearRect(0, 0, W, H);
    const pad = 4, cw = (W - 2 * pad) / nx, ch = (H - 2 * pad) / ny;
    const vmin = opt.vmin || 0, vmax = opt.vmax;
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const v = grid[j][i];
        if (v === null || v === undefined || !isFinite(v)) continue;
        const t = vmax > vmin ? (v - vmin) / (vmax - vmin) : 0;
        g.fillStyle = rampColor(opt.ramp, t);
        g.fillRect(pad + i * cw, pad + j * ch, Math.ceil(cw), Math.ceil(ch));
      }
    }
    if (opt.overlay) { // class grid: 'H' hit, 'M' miss, 'F' false alarm
      g.globalAlpha = 0.55;
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
        const c = opt.overlay[j][i];
        if (c === 'H') { g.fillStyle = '#2e7d32'; g.fillRect(pad + i * cw, pad + j * ch, Math.ceil(cw), Math.ceil(ch)); }
        else if (c === 'M') { g.fillStyle = '#c2185b'; g.fillRect(pad + i * cw, pad + j * ch, Math.ceil(cw), Math.ceil(ch)); }
        else if (c === 'F') { g.fillStyle = '#ef6c00'; g.fillRect(pad + i * cw, pad + j * ch, Math.ceil(cw), Math.ceil(ch)); }
      }
      g.globalAlpha = 1;
    }
    if (opt.contour) { // boolean grid -> stroke boundary cells
      g.strokeStyle = opt.contourColor || '#fff'; g.lineWidth = 1.4;
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
        if (!opt.contour[j][i]) continue;
        const up = j > 0 && opt.contour[j - 1][i], dn = j < ny - 1 && opt.contour[j + 1][i];
        const lf = i > 0 && opt.contour[j][i - 1], rt = i < nx - 1 && opt.contour[j][i + 1];
        const x = pad + i * cw, y = pad + j * ch;
        g.beginPath();
        if (!up) { g.moveTo(x, y); g.lineTo(x + cw, y); }
        if (!dn) { g.moveTo(x, y + ch); g.lineTo(x + cw, y + ch); }
        if (!lf) { g.moveTo(x, y); g.lineTo(x, y + ch); }
        if (!rt) { g.moveTo(x + cw, y); g.lineTo(x + cw, y + ch); }
        g.stroke();
      }
    }
    g.strokeStyle = css('--border', '#444'); g.globalAlpha = 0.5; g.lineWidth = 0.6;
    g.strokeRect(pad, pad, nx * cw, ny * ch);
    g.globalAlpha = 1;
    if (opt.fallback) { g.fillStyle = css('--text-dim', '#888'); g.font = '12px system-ui';
      g.fillText(opt.fallback, pad + 6, H / 2); }
    return { W: W, H: H };
  }

  function renderConsec(host, viz, ev, opts) {
    const ramp = viz.event_type === 'heat_wave' ? RAMPS.heat : RAMPS.freeze;
    const leads = Object.keys(viz.models).reduce(function (a, m) {
      const e = viz.models[m]; if (e && typeof e === 'object') Object.keys(e).forEach(function (l) { if (/^\d+$/.test(l) && a.indexOf(l) < 0) a.push(l); });
      return a;
    }, []).sort(function (a, b) { return +a - +b; });
    const ui = { lead: pickLead(leads), day: null };
    const models = visibleModels(viz, opts);
    const cell = viz.grid.lat.length;

    const wrap = document.createElement('div'); wrap.className = 'ev-wrap';
    host.appendChild(wrap);

    function panel(title, subtitle, cls) {
      const d = document.createElement('div'); d.className = 'ev-panel' + (cls ? ' ' + cls : '');
      d.innerHTML = '<div class="ev-panel-head"><b>' + esc(title) + '</b>' +
        (subtitle ? '<span class="ev-panel-sub">' + esc(subtitle) + '</span>' : '') + '</div>';
      const c = document.createElement('canvas'); d.appendChild(c);
      const f = document.createElement('div'); f.className = 'ev-foot'; d.appendChild(f);
      panel.into.appendChild(d);
      return { el: d, canvas: c, foot: f };
    }
    panel.into = wrap;

    const obs = panel('Observed · ' + (viz.truth_source === 'era5_mirror' ? 'ERA5' : 'IFS analysis'),
      viz.quantile ? (viz.quantile * 100 | 0) + 'th-pct climatology' : '');
    const right = document.createElement('div'); right.className = 'ev-models'; wrap.appendChild(right);

    const toolbar = document.createElement('div'); toolbar.className = 'ev-toolbar';
    host.insertBefore(toolbar, wrap);
    if (leads.length) {
      toolbar.innerHTML = '<span class="ev-tool-label">forecast lead</span>';
      leads.forEach(function (l) {
        const b = document.createElement('button');
        b.className = 'mini ev-lead' + (l === ui.lead ? ' on' : '');
        b.textContent = (+l) + 'h';
        b.onclick = function () { ui.lead = l; notifyLeadHook(l); renderAll(); };
        toolbar.appendChild(b);
      });
    }
    const days = document.createElement('div'); days.className = 'ev-days'; toolbar.appendChild(days);
    viz.window.days.forEach(function (d, i) {
      const n = viz.obs.daily[i].split('/').join('').split('').filter(function (c) { return c === '1'; }).length;
      const b = document.createElement('button');
      b.className = 'mini ev-day' + (ui.day === i ? ' on' : '');
      b.textContent = d.slice(5); b.title = n + ' cells above threshold';
      b.onclick = function () { ui.day = ui.day === i ? null : i; notifyLeadHook(ui.lead); renderAll(); };
      days.appendChild(b);
    });

    const dur = document.createElement('div'); dur.className = 'ev-dur';
    wrap.appendChild(dur);

    function drawObs() {
      let grid, vmin, vmax;
      if (ui.day === null) { grid = viz.obs.consec; vmin = 1; vmax = Math.max(3, viz.obs.max_consec); }
      else { grid = unpackMask(viz.obs.daily[ui.day], cell).map(function (r) { return r.map(function (b) { return b ? 1 : null; }); }); vmin = 0; vmax = 1; }
      drawField(obs.canvas, grid, viz.grid.lat, viz.grid.lon, { ramp: ramp, vmin: vmin, vmax: vmax });
      obs.foot.textContent = ui.day === null
        ? 'max ' + viz.obs.max_consec + ' consecutive days · anchor ' + viz.obs.anchor_day +
          ' (' + viz.obs.footprint_cells + ' cells)'
        : viz.window.days[ui.day] + ' — daily mask (click day again for count map)';
    }

    function drawModels() {
      right.innerHTML = '';
      panel.into = right;
      const emptyGrid = viz.obs.consec.map(function (r) { return r.map(function () { return null; }); });
      models.forEach(function (m) {
        const p = panel(m, '', 'ev-model-panel');
        const e = viz.models[m];
        const entry = (e && typeof e === 'object' && !e.status) ? e[ui.lead] : null;
        if (!entry || entry.status !== 'ok') {
          const st = (typeof e === 'string' ? e : (e && e.status)) || (entry && entry.status) || 'no data';
          drawField(p.canvas, emptyGrid, viz.grid.lat, viz.grid.lon, { ramp: ramp, vmax: 1, fallback: friendly(st) });
          return;
        }
        drawField(p.canvas, entry.consec, viz.grid.lat, viz.grid.lon,
          { ramp: ramp, vmin: 1, vmax: Math.max(viz.obs.max_consec, entry.max_consec) });
        const dErr = entry.max_consec - viz.obs.max_consec;
        p.foot.textContent = 'max ' + entry.max_consec + 'd (' + (dErr > 0 ? '+' : '') + dErr +
          ') · footprint ' + entry.footprint_cells + ' (obs ' + viz.obs.footprint_cells + ')';
      });
    }

    function drawDur() {
      let html = '<div class="ev-dur-title">duration vs observed · lead ' + ui.lead + 'h</div>';
      const obsN = viz.obs.max_consec;
      const vals = [{ label: 'observed', n: obsN, c: css('--text-dim', '#5b6478') }];
      models.forEach(function (m) {
        const e = viz.models[m]; const entry = e && typeof e === 'object' && e[ui.lead];
        if (entry && entry.status === 'ok') vals.push({ label: m, n: entry.max_consec, c: modelBarColor(m) });
      });
      // M6 (DR2): fixed scale — bars comparable across models and leads
      const maxN = Math.max(7, obsN + 2);
      vals.forEach(function (v, i) {
        const d = i === 0 ? '' : ' <b class="ev-bar-d">Δ ' + (v.n - obsN >= 0 ? '+' : '') + (v.n - obsN) + ' d vs obs</b>';
        html += bar(v.label, v.n, maxN, v.c, d, true, obsN);
      });
      dur.innerHTML = html + (() => {
        // DR2-M6: numeric day ruler under the tracks (same grid columns)
        let r = '<div class="ev-bar-ruler"><span></span><span class="rtrack">';
        for (let t = 0; t <= maxN; t++) r += '<b class="rtick" style="left:' + (100 * t / maxN) + '%">' + t + '</b>';
        return r + '</span><span></span></div>';
      })();
    }

    function renderAll() {
      toolbar.querySelectorAll('.ev-lead').forEach(function (b) {
        b.classList.toggle('on', b.textContent === ui.lead + 'h'); });
      days.querySelectorAll('.ev-day').forEach(function (b, i) {
        b.classList.toggle('on', ui.day === i); });
      drawObs(); drawModels(); drawDur();
    }
    renderAll();
  }

  function renderAR(host, viz, ev, opts) {
    const leads = [];
    Object.keys(viz.models).forEach(function (m) {
      const e = viz.models[m]; if (e && typeof e === 'object') Object.keys(e).forEach(function (l) { if (/^\d+$/.test(l) && leads.indexOf(l) < 0) leads.push(l); });
    });
    leads.sort(function (a, b) { return +a - +b; });
    const ui = { lead: pickLead(leads), overlap: true };
    const modelNames = visibleModels(viz, opts);
    const ny = viz.grid.lat.length;
    const obsMask = viz.obs ? unpackMask(viz.obs.mask, ny) : null;

    const wrap = document.createElement('div'); wrap.className = 'ev-wrap';
    const toolbar = document.createElement('div'); toolbar.className = 'ev-toolbar';
    host.appendChild(toolbar); host.appendChild(wrap);
    leads.forEach(function (l) {
      const b = document.createElement('button');
      b.className = 'mini ev-lead' + (l === ui.lead ? ' on' : '');
      b.textContent = (+l) + 'h';
      b.onclick = function () { ui.lead = l; notifyLeadHook(l); renderAll(); };
      toolbar.appendChild(b);
    });
    const legend = document.createElement('span'); legend.className = 'ev-legend';
    legend.innerHTML = '<i class="hit"></i>hit&nbsp;<i class="miss"></i>miss&nbsp;<i class="fa"></i>false alarm';
    toolbar.appendChild(legend);

    function panel(title, sub, cls) {
      const d = document.createElement('div'); d.className = 'ev-panel' + (cls ? ' ' + cls : '');
      d.innerHTML = '<div class="ev-panel-head"><b>' + esc(title) + '</b>' +
        (sub ? '<span class="ev-panel-sub">' + esc(sub) + '</span>' : '') + '</div>';
      const c = document.createElement('canvas'); d.appendChild(c);
      const f = document.createElement('div'); f.className = 'ev-foot'; d.appendChild(f);
      panel.into.appendChild(d);
      return { el: d, canvas: c, foot: f };
    }
    panel.into = wrap;

    const right = document.createElement('div'); right.className = 'ev-models';

    function renderAll() {
      wrap.innerHTML = '';
      if (viz.obs) {
        const p = panel('Observed IVT', viz.obs.anchor_time || '');
        drawField(p.canvas, viz.obs.ivt, viz.grid.lat, viz.grid.lon,
          { ramp: RAMPS.ivt, vmin: 200, vmax: 1100, contour: obsMask, contourColor: css('--text', '#111') });
        p.foot.textContent = 'mask ' + viz.obs.area_cells + ' cells ≥ ' + viz.ivt_threshold_kg_m_s + ' kg m⁻¹ s⁻¹';
      } else {
        const p = panel('Observed IVT', 'after truth cutoff — no AR truth');
        drawField(p.canvas, viz.grid.lat.map(function () { return viz.grid.lon.map(function () { return null; }); }),
          viz.grid.lat, viz.grid.lon, { ramp: RAMPS.ivt, vmax: 1, fallback: 'no observations (post-cutoff)' });
      }
      wrap.appendChild(right); right.innerHTML = '';
      panel.into = right;
      modelNames.forEach(function (m) {
        const e = viz.models[m]; const entry = e && typeof e === 'object' && e[ui.lead];
        const p = panel(m, '', 'ev-model-panel');
        const empty = viz.obs ? viz.obs.ivt.map(function (r) { return r.map(function () { return null; }); })
          : viz.grid.lat.map(function () { return viz.grid.lon.map(function () { return null; }); });
        if (!entry || entry.status !== 'ok') {
          drawField(p.canvas, empty, viz.grid.lat, viz.grid.lon,
            { ramp: RAMPS.ivt, vmax: 1, fallback: friendly((entry && entry.status) || 'unavailable') });
          return;
        }
        let overlay = null;
        let hit = 0, miss = 0, fa = 0;
        if (obsMask) {
          const mm = unpackMask(entry.mask, ny);
          overlay = mm.map(function (row, j) {
            return row.map(function (v, i) {
              if (v && obsMask[j][i]) { hit++; return 'H'; }
              if (v) { fa++; return 'F'; }
              if (obsMask[j][i]) { miss++; return 'M'; }
              return null;
            });
          });
        }
        drawField(p.canvas, entry.ivt, viz.grid.lat, viz.grid.lon, {
          ramp: RAMPS.ivt, vmin: 200, vmax: 1100,
          overlay: ui.overlap ? overlay : null,
          contour: ui.overlap ? null : obsMask, contourColor: '#fff',
        });
        p.foot.textContent = obsMask ? 'hit ' + hit + ' · miss ' + miss + ' · false alarm ' + fa
          : 'mask ' + entry.area_cells + ' cells';
      });
      toolbar.querySelectorAll('.ev-lead').forEach(function (b) {
        b.classList.toggle('on', b.textContent === ui.lead + 'h'); });
    }
    const ob = document.createElement('label'); ob.className = 'ev-toggle';
    ob.innerHTML = '<input type="checkbox" checked> overlap classes';
    ob.querySelector('input').onchange = function (e) { ui.overlap = e.target.checked; renderAll(); };
    toolbar.appendChild(ob);
    renderAll();
  }

  /* ---------------- entry ---------------- */
  function visibleModels(viz, opts) {
    const all = Object.keys((viz && viz.models) || {});
    if (!opts || !Array.isArray(opts.models)) return all;
    const base = opts.baseline || 'ifs_hres';
    const sel = opts.models.filter(function (m) { return all.indexOf(m) >= 0; });
    if (all.indexOf(base) >= 0 && sel.indexOf(base) < 0) sel.unshift(base);
    return sel;
  }
  function render(host, viz, ev, opts) {
    if (active) { try { active.teardown(); } catch (e) { /* already gone */ } active = null; }
    host.innerHTML = '';
    if (!viz) return;
    if (window.L) { renderMaps(host, viz, ev, opts); return; }
    if (viz.kind === 'consecutive_days') renderConsec(host, viz, ev, opts);
    else if (viz.kind === 'ar_ivt') renderAR(host, viz, ev, opts);
  }

  window.EventViz = { load: load, render: render, onModels: null,
    /* Ponytail 2026-11-08: closeDetail must release the live maps (tile +
       sync listeners) instead of leaving up to 5 detached Leaflet instances
       alive until the NEXT event renders. */
    teardown: function () {
      if (active) { try { active.teardown(); } catch (e) { /* already gone */ } active = null; }
    },
    /* QA-only debug hook (remove with app.js __debug): per-map view + overlay */
    __maps: function () { try {
      const ms = (active && active.maps) || [];
      return ms.map(function (m) {
        const b = m.getBounds(); let ov = null;
        m.eachLayer(function (l) { if (l && l.getBounds && l._url) {
          const e = l.getBounds();
          ov = [[+e.getSouth().toFixed(1), +e.getWest().toFixed(1)],
                [+e.getNorth().toFixed(1), +e.getEast().toFixed(1)]]; } });
        return { c: [+m.getCenter().lat.toFixed(1), +m.getCenter().lng.toFixed(1)], z: m.getZoom(),
                 view: [[+b.getSouth().toFixed(1), +b.getWest().toFixed(1)],
                        [+b.getNorth().toFixed(1), +b.getEast().toFixed(1)]],
                 overlay: ov }; }); } catch (e) { return []; } } };
})();
