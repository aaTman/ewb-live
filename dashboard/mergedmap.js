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
   bit-packed rows heat/freeze/ar/bridged — plane 3 flags AR cells that are
   only a gap-bridge footprint; row0=lat0, MSB-first), duration sidecars
   <ts>.run (zlib, two raw u8 planes heat/freeze; dur rev v2: low 7 bits =
   consecutive COMPLETED UTC days meeting the daily rule — constant across
   all four frames of a day — bit 7 = today so far, dithered on qualifying
   cells only. Qualification gate: a cell paints only when its completed
   streak reaches the lane minimum (heat = detector MIN_CONSECUTIVE_DAYS = 3,
   freeze = 1; index.dur.min overrides the built-in LAYERS[].min)) and
   data/results/global_timeline.json (events[]: id,type,title,start,end,bbox,
   grid,day,days,masks[,qualified_at]).
   Reduced motion: playback is frame-stepping only; nothing auto-plays.
   ========================================================================== */
(function () {
  'use strict';

  const PAD = 12;
  const HIT_MIN = 44;                      // min touch target (px)
  const LAYERS = [                         // draw order bottom→top
    // min = qualification gate: heat is the detector's MIN_CONSECUTIVE_DAYS
    // (ewb_live/events/temperature.py, 3) — index.dur.min (published by the
    // exceedance build) overrides these built-in defaults. Freeze paints from
    // its first completed day (owner 2026-11-10): freeze EVENTS still need the
    // detector minimum, but cold runs are shorter/rarer, so a 3-day paint floor
    // would leave the lane nearly blank and the 1-2 day ramp shades unusable.
    { key: 'h', name: 'heat',   label: 'Heat — days > P85', ramp: true, bins: 7, min: 3,
      tip: 'Blank below 3 consecutive completed days — the detector minimum, so such a cell can never become a heat wave. Solid = consecutive COMPLETED UTC days the cell exceeded the P85 climatology at all four 6-hourly steps (constant within a day; 3–7+); dithered = today so far (provisional), only on a cell that already qualifies. 7+ days is the darkest stop in light, the brightest in dark.' },
    { key: 'f', name: 'freeze', label: 'Freeze — days < P15', ramp: true, bins: 3, min: 1,
      tip: 'Shaded from its first consecutive completed day: cold runs are short, so sub-3-day cells stay visible for context (a freeze EVENT still needs 3+ days). Solid = consecutive COMPLETED UTC days the cell fell below the P15 climatology at all four 6-hourly steps (constant within a day; 1–3+); dithered = today so far (provisional), only on a cell that already shows at least one completed day. 3+ days is the darkest stop in light, the brightest in dark.' },
    { key: 'a', name: 'ar',     label: 'AR — IVT magnitude', ramp: true, bins: 5,
      tip: 'Detected atmospheric rivers, shaded by IVT magnitude (kg m-1 s-1): 250-500, 500-750, 750-1000, 1000-1250, >=1250.'
        + ' Only cells the EWB AR detector flags (and the tropical/cyclone screens keep) are drawn; IVT outside an AR is never shown.'
        + ' Pale green = a track\'s gap-bridge footprint (display only).' },
  ];
  // bin opacities: ONE constant alpha per lane (all lanes 200) — the ramps'
  // lightness carries the scale (light theme: even L* steps, darker = more
  // days / higher IVT; dark theme: the same steps REVERSED, brightest = most
  // extreme, so extremes never recede into the dark map), and one alpha per
  // lane keeps that ordering intact after blending. Constant < 255 also lets
  // the graticule and coasts read through every fill; coastlines are
  // additionally re-stroked above the raster in paintFrame (owner review
  // 2026-11-11, complaint 1: opaque AR sheets hid the oceans' grid).
  const RUN_ALPHA = { heat: [200, 200, 200, 200, 200, 200, 200], freeze: [200, 200, 200],
                      ar: [200, 200, 200, 200, 200] };
  // qualification gate per lane: heat = the detector's MIN_CONSECUTIVE_DAYS,
  // freeze = 1 completed day (context; events still need 3). index.dur.min
  // (published with the v2 sidecars) wins; otherwise the built-in LAYERS[].min.
  // raw is still null on the early paint/legend paths, so every branch must
  // tolerate that and fall back to L.min.
  const minDays = (L) => (raw && raw.dur && raw.dur.min && raw.dur.min[L.name] != null)
    ? raw.dur.min[L.name] : (L.min || 0);
  const runBin = (count, bins) => Math.min(count - 1, bins - 1);   // bins: 1..bins
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
  let previewId = null;                    // event pointed at in the page's event list (outline only)
  let mounted = false;
  let playing = null, speedI = 1;
  let land = null, graticule = null, proj = null, path = null;
  let fit = null;                          // {W,H}
  let region = null;                       // Region dropdown view {key,label,lat,lon}; null = world
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
  const dayMs = (d) => {                 // date part of compact 'YYYYMMDD[HH]' or ISO, at 00Z
    const s = String(d);
    const iso = /^\d{10}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : s.slice(0, 10);
    return Date.parse(iso + 'T00:00:00Z');
  };
  // tooltip event length: calendar span, end EXCLUSIVE (end-1d = last live day) — contract #6
  const evSpan = (ev) => ` · ${Math.round((dayMs(ev.end) - dayMs(ev.start)) / 86400e3)} days`;

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
    return tlEvents().filter((e) => qualTs(e) <= ts && ts < endTs(e)
      && (tab === 'all' || e.type === tab));
  }
  // type tabs of the page (state is a top-level const in app.js — bare identifier)
  const pageTab = () => (typeof state !== 'undefined' && state.tab) || 'all';
  /* Region view (app.js Region dropdown): boxes, gantt bars and the count all
     follow the server's per-event region map (window.EwbRegions.forEvent —
     footprint-overlap rule from ewb_live/regions.py, centroid port as the
     fallback). If the helper isn't loaded yet, show everything — honest
     fallback, never a wrong filter. */
  const inRegionView = (e) => !region || !(window.EwbRegions && window.EwbRegions.forEvent)
    || window.EwbRegions.forEvent(e.id, e.bbox).includes(region.key);
  const tlEvents = () => (tl ? tl.events.filter(inRegionView) : []);
  function jumpTo(tsStr) {                 // first frame >= ts (clamped)
    let i = raw.frames.findIndex((f) => f >= tsStr);
    if (i < 0) i = raw.frames.length - 1;
    if (i !== idx) { idx = i; return true; }
    return false;
  }

  /* ---------------- frame load ---------------- */
  const rawLast = () => (raw.cutoff && raw.cutoff >= raw.frames[0] && raw.cutoff <= raw.frames[raw.frames.length - 1]
    ? raw.cutoff : raw.frames[raw.frames.length - 1]);   // last ts with a .bin on disk
  function inflateUrl(url) {              // fetch + zlib-decompress one asset
    return fetch(url, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
      .then(async (buf) => {
        const ds = new DecompressionStream('deflate');
        const stream = new Blob([buf]).stream().pipeThrough(ds);  // ArrayBuffer has no pipeThrough
        return new Uint8Array(await new Response(stream).arrayBuffer());
      });
  }
  function getFrame(i) {                   // -> Promise<{bin,run,ivt}|null>; run=null without index.dur, ivt=null without index.ivt
    const ts = raw.frames[i];
    if (!ts) return Promise.resolve(null);
    if (ts > rawLast()) return Promise.resolve(null);    // past ERA5 cutoff: no frames, boxes only
    if (frameCache.has(ts)) return Promise.resolve(frameCache.get(ts));
    if (inflight.has(ts)) return inflight.get(ts);
    const base = `data/results/raw_exceedance/${ts}`;
    // duration sidecar only when the index advertises it; a 404 paints binary for
    // this frame with one warn, so an old lane or CDN skew never blanks the map
    const runP = raw.dur ? inflateUrl(`${base}.run`)
      .catch((err) => { console.warn('duration sidecar unavailable, painting binary:', ts, err.message); return null; })
      : Promise.resolve(null);
    // AR IVT class plane sidecar only when the index advertises it (v3 schema)
    const ivtP = raw.ivt ? inflateUrl(`${base}.ivt`)
      .catch((err) => { console.warn('AR IVT class sidecar unavailable, using binary fallback:', ts, err.message); return null; })
      : Promise.resolve(null);
    const p = Promise.all([inflateUrl(`${base}.bin`), runP, ivtP])
      .then(([bin, run, ivt]) => {
        if (frameCache.size >= CACHE_MAX) frameCache.delete(frameCache.keys().next().value);
        frameCache.set(ts, { bin, run, ivt });
        return frameCache.get(ts);
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
    /* Region view (Region dropdown, briefs/regional_breakdowns.md): fit to the
       region's corner bbox — MultiPoint, because Equal Earth is monotone in
       both axes so the corners' bounds ARE the region's bounds. Canvas size
       stays sphere-based: switching regions never reflows the page. */
    proj.fitExtent([[PAD, PAD], [W - PAD, H - PAD]], region ? {
      type: 'MultiPoint',
      coordinates: [[region.lon[0], region.lat[0]], [region.lon[1], region.lat[0]],
                    [region.lon[0], region.lat[1]], [region.lon[1], region.lat[1]]],
    } : { type: 'Sphere' });
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
  let lut = null, rasterCv = null, packedCache = null, rampCache = null,
      ghostCache = null;   // packed colours: one cache per theme
  let curIvt = null;                                   // AR IVT class plane of the painted frame (hover)
  let curPaint = null;   // {u8, plane, ghostPlane, ivtS2} of the painted frame (hover reads the AR bit)
  const IVT_BINS = [250, 500, 750, 1000, 1250];        // kg m-1 s-1 class edges (index.ivt.bins)
  function buildLut() {
    const g = raw.grid, nx = g.nx, ny = g.ny, stride = Math.ceil(nx / 8);
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(fit.W * dpr), ch = Math.round(fit.H * dpr);
    const byteIdx = new Int32Array(cw * ch).fill(-1);
    const bitMask = new Uint8Array(cw * ch);
    const cellIdx = new Int32Array(cw * ch).fill(-1);   // raw u8-plane index (run sidecar, no bit packing)
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
      const rowOff = j * stride, cellRow = j * nx, o = y * cw;
      for (let x = 0; x < cw; x++) {
        // ll[0] = longitude under the centre column (0 in a world fit, region centre otherwise)
        const lon = ll[0] + ((x + 0.5) / dpr - cx) / kx;
        if (lon < -180 || lon > 180) continue;
        let i = Math.round((lon - g.lon0) / g.step_lon);
        i = ((i % nx) + nx) % nx;                             // +180 wraps onto -180
        byteIdx[o + x] = rowOff + (i >> 3);
        bitMask[o + x] = 1 << (7 - (i & 7));
        cellIdx[o + x] = cellRow + i;
      }
    }
    lut = { cw, ch, byteIdx, bitMask, cellIdx };
  }
  function rgbOf(v) {
    const p = document.createElement('span');
    p.style.color = v; document.body.appendChild(p);
    const m = getComputedStyle(p).color.match(/\d+/g) || [128, 128, 128];
    p.remove();
    return [+m[0], +m[1], +m[2]];
  }
  function paintFrame(frame) {
    const cv = $('#raw-canvas');
    const ctx = cv.getContext('2d');
    drawBase(ctx);
    curIvt = null;                                    // no frame → nothing for hover to read
    curPaint = null;
    if (!frame || !raw || !fit) { dimOutside(ctx); return; }
    const u8 = frame.bin;                        // binary exceedance bits (4 planes)
    const run = (raw.dur && frame.run) ? frame.run : null;   // null → binary paint (fallback)
    if (!lut || lut.cw !== cv.width || lut.ch !== cv.height) buildLut();
    const g = raw.grid, plane = g.ny * Math.ceil(g.nx / 8), cells = g.ny * g.nx;
    // plane 3 'bridged' (v8 assets): AR cells that are only a carried-over
    // footprint across a 1-step mask dropout. Feature-detected on the payload
    // length, so pre-v8 3-plane assets still paint exactly as before.
    const ghostPlane = u8.length >= 4 * plane ? 3 * plane : -1;
    // .ivt schema 2: class in the low 3 bits, bit 7 = IVT >= index.ivt.ar_min
    // (the AR threshold). Schema 1 sidecars were class-only AND were zeroed
    // wherever the AR layer did not paint — which is how "IVT values just
    // disappear" happened, so the two schemas are read differently.
    const ivtS2 = !!(raw.ivt && raw.ivt.schema >= 2);
    const { cw, ch, byteIdx, bitMask, cellIdx } = lut;
    if (!rasterCv || rasterCv.width !== cw || rasterCv.height !== ch) {
      rasterCv = document.createElement('canvas');
      rasterCv.width = cw; rasterCv.height = ch;
    }
    const rctx = rasterCv.getContext('2d');
    const img = rctx.createImageData(cw, ch);
    const px = new Uint32Array(img.data.buffer);
    // ABGR little-endian packed colours: flat per layer (alpha 215, binary paint)
    // + the heat (7-bin) / freeze (3-bin) duration ramps (token alpha RUN_ALPHA).
    // rgbOf forces a
    // style flush per span — too heavy for the paint loop, so memoize per theme
    // (the data-theme observer invalidates)
    if (!packedCache) {
      const cs = getComputedStyle(document.documentElement);
      const pack = (c, al) => ((al << 24) | (c[2] << 16) | (c[1] << 8) | c[0]) >>> 0;
      // every lane's flat binary-fallback paint rides one alpha (215): the
      // colour is the lane token, the alpha keeps the basemap readable
      packedCache = LAYERS.map((L) => pack(rgbOf(cs.getPropertyValue(`--series-${L.name}`)), 215));
      // the gap-bridge footprint stays visibly weaker than a physical AR cell:
      // it is a tracking decision, not a cell that met the IVT rule
      ghostCache = pack(rgbOf(cs.getPropertyValue('--series-ar')), 90);
      rampCache = LAYERS.map((L) => (L.ramp
        ? RUN_ALPHA[L.name].map((al, b) => pack(rgbOf(cs.getPropertyValue(`--dur-${L.name}-${b + 1}`)), al))
        : null));
    }
    const packed = packedCache, ramp = rampCache, ghostCol = ghostCache;
    const durV2 = !!(raw.dur && String(raw.dur.rev || '').indexOf('v2') === 0);
    const act = LAYERS.map((L, li) => (on[L.key] ? li : -1)).filter((li) => li >= 0);
    const ivt = curIvt = (raw.ivt && frame.ivt) ? frame.ivt : null;   // IVT class plane (u8, per-cell; hover reads curIvt)
    curPaint = { u8, plane, ghostPlane, ivtS2 };
    const n = cw * ch;
    for (let p = 0; p < n; p++) {
      const bi = byteIdx[p];
      if (bi < 0) continue;
      const m = bitMask[p];
      for (let a = 0; a < act.length; a++) {                  // later layers paint over earlier
        const li = act[a];
        if (li === 2) {
          // AR: the binary bit says PAINT, the class plane says how strong.
          // Never index run[] for AR — .run has 2 planes (heat, freeze);
          // reading plane 2 is undefined = the AR-outage bug class of
          // 2026-11-09 (twice). The class plane never decides whether a cell
          // paints: it is the IVT field (schema 2), not the AR mask.
          const gh = ghostPlane >= 0 && (u8[ghostPlane + bi] & m);
          const v = ivt ? ivt[cellIdx[p]] : 0;
          const cls = (!gh && ivt && ramp[li]) ? (ivtS2 ? (v & 7) : v) : 0;
          if (gh) px[p] = ghostCol;                        // faint: display-only footprint
          else if (u8[li * plane + bi] & m) {
            if (cls > 0 && cls <= 5) px[p] = ramp[li][cls - 1];  // bins are 0-indexed
            else px[p] = packed[li];                       // mask w/o class: flat green
          }
        } else if (run && ramp[li]) {
          const L = LAYERS[li];
          const r = run[li * cells + cellIdx[p]];             // heat plane 0, freeze plane 1; 0 = unpainted
          const done = durV2 ? (r & 127) : r;        // v1 sidecars: whole byte is the run
          if (done < minDays(L)) continue;           // sub-threshold: blank, every hour
          // today so far (bit 7) = texture only, never a new point: 2px
          // checkerboard to the next ramp shade on an already-painted cell
          const prov = durV2 && (r & 128)
            && ((((p % cw) >> 1) + ((p / cw) | 0) >> 1) & 1);
          px[p] = ramp[li][runBin(done + (prov ? 1 : 0), L.bins)];
          continue;
        } else if (u8[li * plane + bi] & m) px[p] = packed[li];
      }
    }
    rctx.putImageData(img, 0, 0);
    ctx.save();
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    ctx.drawImage(rasterCv, 0, 0, fit.W, fit.H);
    ctx.restore();
    // coastlines ABOVE the fills (owner complaint 2026-11-11: fills hid the
    // basemap): a second thin stroke pass of the same Natural Earth land so
    // coasts and the sphere edge read through every layer
    if (land) {
      const cs2 = getComputedStyle(document.documentElement);
      ctx.save();
      ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
      ctx.strokeStyle = cs2.getPropertyValue('--map-line').trim() || '#999';
      ctx.lineWidth = 0.6;
      ctx.stroke(new Path2D(path(land)));
      ctx.restore();
    }
    dimOutside(ctx);
  }

  /* Region view: veil everything outside the Region rect (Equal Earth is
     monotone in both axes, so the region projects to an exact rectangle).
     Painted above the raster so out-of-region exceedance never reads at full
     strength (owner review 2026-11-09: Tropics/hemispheres looked unchanged
     vs Global). Global = no veil. */
  function dimOutside(ctx) {
    if (!region || !fit) return;
    const pts = [[region.lon[0], region.lat[0]], [region.lon[1], region.lat[0]],
                 [region.lon[0], region.lat[1]], [region.lon[1], region.lat[1]]].map(proj);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.max(0, Math.min(...xs)), x1 = Math.min(fit.W, Math.max(...xs));
    const y0 = Math.max(0, Math.min(...ys)), y1 = Math.max(0, Math.min(fit.H, Math.max(...ys)));
    ctx.save();
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    ctx.fillStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--map-dim').trim() || 'rgba(250,250,248,0.62)';
    ctx.fillRect(0, 0, fit.W, y0);
    ctx.fillRect(0, y1, fit.W, fit.H - y1);
    ctx.fillRect(0, y0, x0, y1 - y0);
    ctx.fillRect(x1, y0, fit.W - x1, y1 - y0);
    ctx.restore();
  }

  /* ---------------- event boxes (SVG overlay, same projection) ---------------- */
  const FULL_BAND_SPAN = 300;
  function bboxSpan(b) { return b[3] >= b[2] ? b[3] - b[2] : b[3] + 360 - b[2]; }
  function isFullBand(b) { return Array.isArray(b) && bboxSpan(b) >= FULL_BAND_SPAN; }
  function bboxPaths(ev) {
    // bbox [lat_min, lat_max, lon_min, lon_max]; the box is the EASTWARD arc
    // lon_min→lon_max normalized to [-180,180): a 0–360 producer form like
    // 350E→20E (the Oct-3 UK AR) draws as ONE rect at −10→20, not split at
    // ±180 and not a whole-globe band. Split only where the arc crosses ±180
    // (the map's own seam).
    const [s, n] = ev.bbox;
    if (isFullBand(ev.bbox)) return '';        // degenerate band: never paint world-wide
    const norm = (l) => ((l + 180) % 360 + 360) % 360 - 180;
    const w = norm(ev.bbox[2]), e = w + bboxSpan(ev.bbox);
    const rect = (wa, ea) => {
      const tl = proj([wa, n]), tr = proj([ea, n]), br = proj([ea, s]), bl = proj([wa, s]);
      return `M${tl[0].toFixed(1)} ${tl[1].toFixed(1)}L${tr[0].toFixed(1)} ${tr[1].toFixed(1)}`
        + `L${br[0].toFixed(1)} ${br[1].toFixed(1)}L${bl[0].toFixed(1)} ${bl[1].toFixed(1)}Z`;
    };
    return e <= 180 ? rect(w, e) : rect(w, 180) + rect(-180, e - 360);
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
    // list preview of an event that is not on screen at this time step: a
    // non-interactive outline, so pointing at a row always shows WHERE it is
    const ghost = previewId && !active.some((e) => e.id === previewId)
      ? tl.events.find((e) => e.id === previewId) : null;
    if (ghost) {
      const gd = bboxPaths(ghost), gt = TYPES[ghost.type] || { css: 'heat' };
      if (gd) mk('path', { class: 'gm-ghost', d: gd, 'aria-hidden': 'true',
                           style: `stroke: var(--series-${gt.css}); fill: var(--series-${gt.css})` }, g);
    }
    for (const ev of active) {
      const t = TYPES[ev.type] || { css: 'heat' };
      const cls = 'gm-ev' + (sel.has(ev.id) ? ' sel' : '') + (anySel && !sel.has(ev.id) ? ' dim' : '')
        + (previewId === ev.id ? ' hov' : '');
      const node = mk('g', {
        class: cls, 'data-id': ev.id, role: 'button', tabindex: 0,
        'aria-pressed': sel.has(ev.id) ? 'true' : 'false',
        'aria-label': `${ev.title}: ${fmtShort(ev.start)} – ${fmtShort(ev.end)}`,
      }, g);
      const bd = bboxPaths(ev);
      if (!bd) { node.remove(); continue; }
      mk('title', {}, node).textContent =
        `${ev.title} — qualified ${fmtShort(qualTs(ev))} → ${fmtShort(ev.end)}${evSpan(ev)}`;
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
      node.addEventListener('pointerenter', () => { node.classList.add('hov'); hoverRow(ev.id, true); });
      node.addEventListener('pointerleave', () => { node.classList.remove('hov'); hoverRow(ev.id, false); });
    }
    if (keepFocus) {
      const t = g.querySelector(`[data-id="${CSS.escape(keepFocus)}"]`);
      if (t) t.focus({ preventScroll: true });
    }
    if (typeof window.onMapEvents === 'function') window.onMapEvents(active.map((e) => e.id), Array.from(sel));
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
  function hoverRow(id, on) {                  // map box → its row in the event list
    const tr = document.querySelector(`#event-table tr[data-id="${CSS.escape(id)}"]`);
    if (tr) tr.classList.toggle('hov', on);
  }
  function preview(id) {                       // event list → outline on map + timeline (no canvas repaint)
    if (!mounted || !raw || previewId === id) return;
    previewId = id;
    renderBoxes(); renderGantt();
  }
  function layersForTab(tab) {                 // the page's type filter narrows the raw layers too
    const key = { heat_wave: 'h', freeze: 'f', atmospheric_river: 'a' }[tab];
    for (const L of LAYERS) on[L.key] = !key || L.key === key;
  }

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
      for (const ev of tlEvents()) {
        if (ev.type !== type) continue;
        const q = qualTs(ev), e = endTs(ev);
        if (q > raw.frames[raw.frames.length - 1] || e < raw.frames[0]) continue;   // clamped out
        const bx = x(tsToDate(q).getTime()), be = x(tsToDate(e).getTime());
        const bw = Math.max(2, be - bx);
        const isSel = sel.has(ev.id) || previewId === ev.id;
        const bar = mk('rect', {
          class: 'gm-bar', 'data-id': ev.id, x: bx, y, width: bw, height: LANE_H, rx: 2,
          style: `fill: var(--series-${TYPES[type].css})`,
          opacity: isSel ? 1 : (anySel ? .18 : (tab !== 'all' && tab !== type ? .18 : .8)),
          stroke: isSel ? 'var(--canvas)' : 'none', 'stroke-width': isSel ? 1 : 0,
          cursor: 'pointer',
        }, svg);
        mk('title', {}, bar).textContent = `${ev.title} — qualified ${fmtShort(q)}${evSpan(ev)}`;
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
    getFrame(idx).then((frame) => {
      if (seq !== renderSeq) return;
      const note = $('#raw-note');
      if (frame === null && ts && !past) {
        note.classList.remove('hidden');
        note.textContent = `Frame ${fmtReadout(ts)} is not available yet — `
          + 'the raw exceedance build has not produced it.';
        legStats = null; paintFrame(null);
      } else {
        note.classList.add('hidden');
        legStats = censusFrame(frame);        // legend describes THIS painted frame
        paintFrame(frame);
      }
      renderLegend();
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
      // no widget: the type tabs are the only layer toggles (complaint 3);
      // #layers= deep links ride the state alone. render() below repaints.
    }
    const s = params.sel || params.global;           // old #global= links redirect
    if (s !== undefined) {
      const ids = s.split(',').filter(Boolean);
      if (ids.join(',') !== Array.from(sel).join(',')) { sel.clear(); ids.forEach((i) => sel.add(i)); changed = true; }
    }
    if (changed) render();
    return changed;
  }

  /* ---------------- adaptive colour legend ----------------
     The lanes' colours mean different things (heat/freeze = consecutive
     COMPLETED days, AR = IVT magnitude in kg m⁻¹ s⁻¹), a lane's ramp can
     shrink at runtime (index.dur.min raises the paint floor; a missing .run /
     .ivt sidecar falls back to one flat colour), and the Region dropdown
     veils everything outside its rect. So the legend is generated from the
     state the map is in right now: one group per layer that is ON, listing
     only the stops that lane can paint, with the stops this frame does not
     paint dimmed and a lane with no cells at all marked. A colour that is not
     on screen never reads as live. */
  const LEGEND = {
    heat:   { name: 'Heat wave', unit: 'days > P85',
      flat: 'exceeds P85', flatTip: 'Exceeds P85 — the duration sidecar is unavailable, so one flat colour paints every exceeded cell' },
    freeze: { name: 'Freeze', unit: 'days < P15',
      flat: 'below P15', flatTip: 'Below P15 — the duration sidecar is unavailable, so one flat colour paints every exceeded cell' },
    ar:     { name: 'AR IVT', unit: 'kg m⁻¹ s⁻¹',
      flat: 'AR mask', flatTip: 'AR mask — the IVT class sidecar is unavailable, so one flat green paints every masked cell' },
  };
  let legStats = null;                         // lane census of the painted frame
  let legHtml = '';                            // last rendered markup (playback: no reflow churn)

  const legStop = (bg, label, title, out, extraCls) =>
    `<span class="lg-bin${out ? ' out' : ''}">`
    + `<i class="swatch${extraCls || ''}" style="background:${bg}" title="${title}"></i>`
    + `<span class="lg-t">${label}</span></span>`;
  const dayLbl = (b, bins) => (b >= bins ? b + '+' : String(b));
  const dayTip = (L, b) => `${dayLbl(b, L.bins)} consecutive completed day`
    + `${b > 1 ? 's' : ''} ${L.name === 'heat' ? 'above P85' : 'below P15'}`;

  /* Cell census of one frame: which legend stops the frame ACTUALLY paints,
     under the same rules paintFrame applies (dur gate, the v2 provisional bit,
     the IVT class plane, the binary fallbacks), scoped to the Region view —
     outside the veil nothing is legible. Presence, not counts: the legend only
     asks "is this colour on screen", so a lane leaves the scan as soon as every
     stop it can reach has been found once and the whole scan stops when all
     three are satisfied — at 0.25° that keeps it well under the repaint cost.
     Layer toggles never enter it (it describes the frame, the legend decides
     what to show), and it is cached on the frame object, recomputed when the
     Region changes. */
  function censusFrame(frame) {
    if (!raw || !frame || !frame.bin) return null;
    const key = region ? region.key : 'global';
    if (frame.legKey === key) return frame.leg;
    const g = raw.grid, nx = g.nx, ny = g.ny, cells = nx * ny;
    const durV2 = !!(raw.dur && String(raw.dur.rev || '').indexOf('v2') === 0);
    const run = (raw.dur && frame.run) ? frame.run : null;
    const ivt = (raw.ivt && frame.ivt) ? frame.ivt : null;
    const s = { key, durV2, ramp: { heat: !!run, freeze: !!run, ar: !!ivt },
                n: LAYERS.map((L) => new Uint8Array(L.bins)), prov: [0, 0, 0] };
    const clampGate = (li) => (li === 2 ? 1
      : Math.min(Math.max(minDays(LAYERS[li]), 1), LAYERS[li].bins));
    const reach = (li) => (li === 2 ? (ivt ? LAYERS[2].bins : 1)
      : !run ? 1 : LAYERS[li].bins - clampGate(li) + 1 + (durV2 ? 1 : 0));
    const need = [reach(0), reach(1), reach(2)];    // stops still missing per lane
    const hit = (li, b) => { if (!s.n[li][b]) { s.n[li][b] = 1; need[li]--; } };
    const bin = frame.bin, stride = Math.ceil(nx / 8), planeB = ny * stride;
    const stride4 = bin.length >= 4 * planeB;         // v8+ carries the bridged plane
    const ivtS2c = !!(raw.ivt && raw.ivt.schema >= 2);
    let j0 = 0, j1 = ny - 1, i0 = 0, i1 = nx - 1;
    if (region) {
      j0 = Math.max(0, Math.round((region.lat[0] - g.lat0) / g.step_lat));
      j1 = Math.max(j0, Math.min(ny - 1, Math.round((region.lat[1] - g.lat0) / g.step_lat)));
      if (region.lon[1] - region.lon[0] < 360) {   // whole-width regions stay global in lon
        const wi = (v) => ((Math.round((v - g.lon0) / g.step_lon) % nx) + nx) % nx;
        i0 = wi(region.lon[0]); i1 = wi(region.lon[1]);
      }
    }
    const span = (j, a, b) => {
      const crow = j * stride;
      for (let i = a; i <= b; i++) {
        const c = j * nx + i, byte = crow + (i >> 3), m = 1 << (7 - (i & 7));
        for (let li = 0; li < 2; li++) {                   // heat plane 0, freeze plane 1
          if (!need[li]) continue;
          const L = LAYERS[li];
          if (run) {
            const r = run[li * cells + c];
            const done = durV2 ? (r & 127) : r;
            if (done < minDays(L)) continue;               // sub-gate: never painted
            hit(li, Math.min(done - 1, L.bins - 1));
            // the provisional bit dithers toward the NEXT shade, so that shade is on
            // screen too — and the 'today' stop itself rides as a third outcome
            if (durV2 && (r & 128)) {
              hit(li, Math.min(done, L.bins - 1));
              if (!s.prov[li]) { s.prov[li] = 1; need[li]--; }
            }
          } else if (bin[li * planeB + byte] & m) hit(li, 0);
        }
        if (need[2]) {                                     // AR: class plane, never run[]
          if (ivt) {
            // the legend counts what is PAINTED: schema 2 classes cover the
            // whole IVT field, so a class without the AR bit is a tint, not a
            // stop on the AR ramp
            const painted = !!(bin[2 * planeB + byte] & m);
            const ghost = stride4 && (bin[3 * planeB + byte] & m);
            const v = ivt[c], cls = ivtS2c ? (v & 7) : v;
            if (painted && !ghost && cls > 0 && cls <= LAYERS[2].bins) hit(2, cls - 1);
            else if (painted && !ghost) hit(2, 0);      // mask without a class: flat green
          }
          // cls === 0 with the bit set paints the flat series green (a non-finite
          // IVT under the mask — the class edges start at 250, the mask at 400).
          // Not a legend stop: it is a data gap, and its colour is the top class
          // in the light ramp anyway.
          else if (bin[2 * planeB + byte] & m) hit(2, 0);
        }
      }
    };
    for (let j = j0; j <= j1 && need[0] + need[1] + need[2] > 0; j++) {
      if (i0 <= i1) span(j, i0, i1);
      else { span(j, i0, nx - 1); span(j, 0, i1); }         // dateline-crossing region
    }
    frame.leg = s; frame.legKey = key;
    return s;
  }

  function renderLegend() {
    const leg = $('#gm-legend');
    if (!leg) return;
    const groups = [];
    if (raw) {
      const cen = legStats;
      LAYERS.forEach((L, li) => {
        if (!on[L.key]) return;                            // off the map → off the legend
        const M = LEGEND[L.name];
        const n = cen ? cen.n[li] : null;                  // null = nothing painted yet
        const ramp = !cen || cen.ramp[L.name];
        const live = n ? n.reduce((a, b) => a + b, 0) : -1;  // stops present in this frame
        const from = L.name === 'ar' ? 1 : Math.min(Math.max(minDays(L), 1), L.bins);
        const stops = [];
        if (ramp) {
          // only stops the lane can paint: days below the gate never reach the canvas
          for (let b = from; b <= L.bins; b++) {
            const lo = IVT_BINS[b - 1];
            stops.push(legStop(
              `var(--dur-${L.name}-${b})`,
              // top class reads '1250+' — same '+ suffix as the day ramps; the
              // caption column stays narrow enough that the digits never touch
              L.name === 'ar' ? (b < L.bins ? String(lo) : `${lo}+`) : dayLbl(b, L.bins),
              L.name === 'ar'
                ? (b < L.bins ? `${lo}\u2013${IVT_BINS[b]} kg m\u207b\u00b9 s\u207b\u00b9` : `\u2265${lo} kg m\u207b\u00b9 s\u207b\u00b9`)
                : dayTip(L, b),
              !!n && n[b - 1] === 0));
          }
          // 'today so far' rides as a dithered shade, never a new colour (dur rev v2)
          if (L.name !== 'ar' && cen && cen.durV2 && cen.prov[li] > 0) {
            stops.push(legStop(
              `repeating-conic-gradient(var(--dur-${L.name}-${from}) 0% 25%, var(--series-${L.name}) 0% 50%) 0 0 / 4px 4px`,
              'today',
              'Today so far (provisional): dithered one shade up on a cell that already qualifies — it never adds a new point',
              false, ' prov'));
          }
        } else {
          stops.push(legStop(`var(--series-${L.name})`, M.flat, M.flatTip, !!n && live === 0));
        }
        const none = !!n && live === 0;
        groups.push(`<span class="lg-group${none ? ' none' : ''}" data-layer="${L.key}"`
          + ` title="${L.tip || M.name}">`
          + `<span class="lg-head"><span class="lg-name">${M.name}</span>`
          + `<span class="lg-unit">${M.unit}</span>`
          + (none ? '<span class="lg-none">none in view</span>' : '') + '</span>'
          + `<span class="lg-stops">${stops.join('')}</span></span>`);
      });
    }
    const html = groups.join('');
    if (html === legHtml) return;              // same legend: no reflow per frame
    legHtml = html;
    leg.innerHTML = html;
    // never display:none: the strip's height is a CLS reservation (styles.css
    // #gm-legend:empty), and "all layers off" cannot happen anyway
    leg.classList.remove('hidden');
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
      packedCache = null; rampCache = null; ghostCache = null;   // theme flip: repack colours
      if (raw && fit) paintFrame(frameCache.get(raw.frames[idx]) || null);
    })
      .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    // AR hover: IVT class under the cursor in the shared #tooltip (brief:
    // hover shows the IVT value or class). rAF-throttled pointermove reads
    // the painted frame's class plane through the pixel→cell LUT.
    const tipCv = $('#raw-canvas'), tipEl = $('#tooltip');
    let tipRaf = 0, tipEv = null;
    const tipShow = () => {
      tipRaf = 0;
      let cls = 0;
      if (on.a && curIvt && lut) {
        const r = tipCv.getBoundingClientRect();
        const x = Math.round((tipEv.clientX - r.left) * (tipCv.width / r.width));
        const y = Math.round((tipEv.clientY - r.top) * (tipCv.height / r.height));
        if (x >= 0 && y >= 0 && x < lut.cw && y < lut.ch) {
          const cell = lut.cellIdx[y * lut.cw + x];
          if (cell >= 0 && curPaint) {
            // the schema-2 class plane covers the whole IVT field; the map
            // only shows ARs, so only cells the AR layer paints answer a hover
            const nx = raw.grid.nx, stride = Math.ceil(nx / 8);
            const j = Math.floor(cell / nx), i = cell - j * nx;
            const bit = 1 << (7 - (i & 7)), o = j * stride + (i >> 3);
            const gh = curPaint.ghostPlane >= 0 && (curPaint.u8[curPaint.ghostPlane + o] & bit);
            if (!gh && (curPaint.u8[2 * curPaint.plane + o] & bit)) {
              const v = curIvt[cell];
              cls = curPaint.ivtS2 ? (v & 7) : v;
            }
          }
        }
      }
      if (cls > 0 && cls <= 5) {
        const lo = IVT_BINS[cls - 1];
        const rng = cls < 5 ? `${lo}–${IVT_BINS[cls]}` : `≥${lo}`;
        tipEl.innerHTML = `<div class="t-title">IVT ${rng} kg m⁻¹ s⁻¹</div>`;
        tipEl.classList.remove('hidden');
        if (window.moveTip) window.moveTip(tipEv);
      } else tipEl.classList.add('hidden');
    };
    tipCv.addEventListener('pointermove', (e) => { tipEv = e; if (!tipRaf) tipRaf = requestAnimationFrame(tipShow); });
    tipCv.addEventListener('pointerleave', () => tipEl.classList.add('hidden'));

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
        const n = tlEvents().filter((e) => qualTs(e) <= ts && ts < endTs(e)).length;
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
    // URL restore (deep link) before first paint; redirects fold in here
    const boot = hashParams();
    updateCount();   // region may have been parked by a deep link pre-mount
    applyRegionLabel();
    $('#raw-skel').hidden = true;
    $('#raw-canvas').hidden = false;
    $('#gm-boxes').removeAttribute('hidden');   // SVGElement has no .hidden IDL — attribute only
    if (!('layers' in boot) && pageTab() !== 'all') layersForTab(pageTab());
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

  function updateCount() {   // region-aware section count (setRegion + mount)
    const c = $('#gm-count');
    if (!c) return;
    if (!tl) { c.textContent = 'layers every 6 h'; return; }
    const n = tlEvents().length;
    c.textContent = `${region ? `${region.label} \u00b7 ` : ''}${n} event${n === 1 ? '' : 's'} \u00b7 layers every 6 h`;
  }
  // the section is labelled with what it now shows (review 2026-11-09)
  function applyRegionLabel() {
    const sec = $('#global-sec');
    if (sec) sec.setAttribute('aria-label',
      `${region ? region.label : 'Global'} events and raw detector output \u2014 last 90 days`);
  }

  window.MergedMap = { mount, urlParams, applyUrl,
    preview,
    /* Region view (app.js Region dropdown): null/`global` = whole world.
       Refits the projection + repaints; before mount it just parks the view
       (mount's fitProjection picks it up). */
    setRegion: (r) => {
      region = r && r.key && r.key !== 'global'
        ? { key: r.key, label: r.label || r.key, lat: r.lat, lon: r.lon } : null;
      if (mounted) {
        applyRegionLabel();
        updateCount();
        fitProjection(); render();
      }
    },
    onTabChange: (tab) => {
      if (!mounted) return;
      if (tab) { layersForTab(tab); writeUrl(false); }
      render();
    },
    // project/ramp/dpr/grid: the e2e bin-probe reads painted pixels at known cells
    _dbg: () => ({ idx, frames: raw && raw.frames, on, sel: Array.from(sel), mounted, tl: !!tl,
      region: region ? region.key : null,
      dpr: window.devicePixelRatio || 1, grid: raw && raw.grid,
      project: (ll) => (proj ? proj(ll) : null),
      ramp: (li, b) => (rampCache && rampCache[li] ? [rampCache[li][b] & 255,
        (rampCache[li][b] >> 8) & 255, (rampCache[li][b] >> 16) & 255, rampCache[li][b] >>> 24] : null),
      packed: (li) => (packedCache ? [packedCache[li] & 255, (packedCache[li] >> 8) & 255,
        (packedCache[li] >> 16) & 255, packedCache[li] >>> 24] : null),
      ivtAt: (j, i) => { const v = (curIvt && raw) ? curIvt[j * raw.grid.nx + i] : 0;
        return curPaint && curPaint.ivtS2 ? (v & 7) : v; },
      // the AR paint bit at a cell (1 = the AR layer paints it): schema 2
      // classes exist outside the mask by design, so tests must ask separately
      arAt: (j, i) => { if (!raw || !curPaint) return 0;
        const nx = raw.grid.nx, stride = Math.ceil(nx / 8);
        return (curPaint.u8[2 * curPaint.plane + j * stride + (i >> 3)]
                >> (7 - (i & 7))) & 1; } }) };
})();
