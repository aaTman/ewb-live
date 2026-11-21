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
   grid,day,days,masks[,qualified_at]). The flood lane rides its OWN daily
   class frames, flood_<YYYYMMDD>.cls (zlib u8 on the same grid: 0 = no
   evidence, 1..5 = accumulation classes over the detector's union rule —
   ewb_live/viz/flood_frames.py), advertised additively as index.flood and
   fetched only for days it lists (defect F0, owner 2026-11-19: floods were
   invisible on the map while other lanes' shading leaked under the chip).
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
    // flood (defect F0, owner 2026-11-19): the lane's own daily raster —
    // union-rule cells (>= 20 mm/day OR >= 50 mm/72 h over the ERA5 P95
    // wet-day climatology, land only) from flood_<YYYYMMDD>.cls, shaded by
    // accumulation. Not bit-packed .bin planes: IMERG is a DAILY product,
    // so the grid is constant across the four frames of its UTC day.
    { key: 'l', name: 'flood',  label: 'Flood — mm/day-equiv', ramp: true, bins: 5, min: 1,
      tip: 'Precipitation over the detector rule: daily >= the ERA5 P95 wet-day threshold AND >= 20 mm, OR trailing 72 h >= P95 AND >= 50 mm (land only, fully observed cells only). Shaded by the mm/day-equivalent accumulation max(daily, 72 h total / 3): 20-50, 50-100, 100-200, 200-350, >=350. Cells that qualify only through the 72 h rule ride the lightest stop. One frame per UTC day (the rain product is daily); blank where the source has no day. The index (raw.flood.tip) restates the actual build source — Google PARDIG AI estimate (CC BY 4.0) or GPM IMERG.' },
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
                      ar: [200, 200, 200, 200, 200], flood: [200, 200, 200, 200, 200] };
  // qualification gate per lane: heat = the detector's MIN_CONSECUTIVE_DAYS,
  // freeze = 1 completed day (context; events still need 3). index.dur.min
  // (published with the v2 sidecars) wins; otherwise the built-in LAYERS[].min.
  // raw is still null on the early paint/legend paths, so every branch must
  // tolerate that and fall back to L.min.
  const minDays = (L) => (raw && raw.dur && raw.dur.min && raw.dur.min[L.name] != null)
    ? raw.dur.min[L.name] : (L.min || 0);
  const runBin = (count, bins) => Math.min(count - 1, bins - 1);   // bins: 1..bins
  const isTrack = (e) => !!(e && e.track);   // TRACK lane → tracklayer.js draws it
  const TYPES = {
    heat_wave:          { css: 'heat',      short: 'Heat',            narrow: 'Heat' },
    freeze:             { css: 'freeze',    short: 'Freeze',          narrow: 'Freeze' },
    // cold snap = the freeze rule without the 0 °C cap: its own colour, but it
    // shares the P15 raw layer, which IS its mask (no third temperature lane)
    cold_snap:          { css: 'cold-snap', short: 'Cold snap',       narrow: 'Cold' },
    atmospheric_river:  { css: 'ar',        short: 'AR',              narrow: 'AR' },
    // flood rides BOTH: the daily class raster below (LAYERS key l) and the
    // event boxes — the raster is the evidence field, the boxes the events
    flood:              { css: 'flood',     short: 'Flood',           narrow: 'Flood' },
    /* TRACK lanes (tc-map 2026-11-18): a cyclone entry carries its own `track`
       geometry, so it is drawn by the additive tracklayer.js layer (polyline,
       intensity fixes, position at the selected step) and NEVER as a bbox box —
       a rectangle around a 3,000 km track is not information. These entries give
       that layer the lane colour and the Gantt short label. */
    tropical_cyclone:   { css: 'tc',        short: 'Tropical cyclone', narrow: 'TC', track: true },
    midlat_cyclone:     { css: 'mlc',       short: 'Midlat. cyclone',  narrow: 'MLC', track: true },
  };
  // Gantt lanes: EVERY lane with events in the window shows, midlat_cyclone
  // included (its lane now publishes events — round 2 was filed because it was
  // silently missing while holding 80 storms). A track lane gets bars/ribbon like
  // every other lane, from qualified_at → end, which is how a storm becomes
  // findable on the time strip at all.
  // Grouped in the chips' order (Temperature | Water | Storms): the lane adjacency is
  // part of what the identity palette was validated against — if this order
  // changes, re-run the palette check (notes/chips_design.md).
  const LANE_ORDER = ['heat_wave', 'cold_snap', 'freeze', 'atmospheric_river',
    'flood', 'tropical_cyclone', 'midlat_cyclone'];
  const SPEEDS = [2, 5, 10];               // fps choices

  let raw = null;                          // raw_exceedance index.json
  let tl = null;                           // global_timeline.json (null = unavailable)
  let idx = 0;                             // current frame index
  let on = { h: true, f: true, a: true, l: true };  // layer toggles
  const sel = new Set();                   // selected event ids (multi-highlight)
  let previewId = null;                    // event pointed at in the page's event list (outline only)
  // Dense lanes a reader expanded to packed rows (label click). IN MEMORY ONLY:
  // the strip's height rides on it, so it never persists to the URL and never
  // changes on hover or a frame step (only on the toggle itself).
  const gExpand = new Set();
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
    return k.length === LAYERS.length ? null : k.join(',');
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

  /* ---------------- flood day frames (defect F0) ----------------
     flood_<YYYYMMDD>.cls: one u8 class grid per UTC day on the raw grid
     (ewb_live/viz/flood_frames.py). Cached per DAY (the four frames of a day
     share it); a day absent from index.flood.days never fetches, and a
     failed fetch caches null — the lane draws nothing, honestly. */
  const flCache = new Map();               // day -> Uint8Array | null
  function getFl(i) {
    if (!raw || !raw.flood || !Array.isArray(raw.flood.days)) return Promise.resolve(null);
    const ts = raw.frames[i];
    if (!ts) return Promise.resolve(null);
    const day = ts.slice(0, 8);
    const days = raw.flood._set || (raw.flood._set = new Set(raw.flood.days));
    if (!days.has(day)) return Promise.resolve(null);
    if (flCache.has(day)) return Promise.resolve(flCache.get(day));
    return inflateUrl(`data/results/raw_exceedance/flood_${day}.cls`)
      .then((u) => {
        const g = raw.grid;
        if (u.length !== g.ny * g.nx) {            // grid skew: never paint garbage
          console.warn('flood class frame size mismatch:', day, u.length);
          u = null;
        }
        if (flCache.size >= 12) flCache.delete(flCache.keys().next().value);
        flCache.set(day, u);
        return u;
      })
      .catch((err) => {
        console.warn('flood class frame unavailable:', day, err.message);
        flCache.set(day, null);
        return null;
      });
  }

  /* ---------------- projection / fit ---------------- */
  function fitProjection() {
    const wrap = $('#raw-map-wrap');
    const W = Math.max(240, wrap.clientWidth);
    proj = d3.geoEqualEarth();
    path = d3.geoPath(proj.scale(1).translate([0, 0]));
    const [[x0, y0], [x1, y1]] = path.bounds({ type: 'Sphere' });
    const dw = x1 - x0, dh = y1 - y0;
    let H = Math.round(Math.min((dh / dw) * (W - 2 * PAD) + 2 * PAD, W * 0.75));
    /* Theater mode caps the map's height in CSS (styles.css: html.theater
       #raw-map-wrap) so the map and the timeline stay on one screen. The canvas
       is sized from here, not from CSS, so read the cap back and fit inside it:
       fitExtent keeps the sphere's aspect and letterboxes it in ocean instead
       of stretching it, and the wrap's content height lands on H exactly (the
       #gm-boxes overlay matches the canvas pixel for pixel either way). A
       border-box max-height is the outer box, so the frame/padding comes off. */
    const wrapCs = getComputedStyle(wrap);
    const cap = parseFloat(wrapCs.maxHeight);
    if (Number.isFinite(cap)) {
      const frame = parseFloat(wrapCs.borderTopWidth) + parseFloat(wrapCs.borderBottomWidth)
        + parseFloat(wrapCs.paddingTop) + parseFloat(wrapCs.paddingBottom);
      H = Math.max(160, Math.min(H, Math.floor(cap - (Number.isFinite(frame) ? frame : 0))));
    }
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
    emitTracks('resize');      // the track layer is sized/projection-matched here
  }

  /* ---------------- TRACK lanes: the seam to tracklayer.js ----------------
     A cyclone entry carries its own geometry, and drawing it (canvas lines +
     fix dots, plus one interactive SVG marker per positioned storm) is that
     module's business, not the merged map's. This hands it what only the map
     knows — the live projection, the fit, the selected step, the region/tab
     filter, the selection — at every state change the map owns. Absent module
     (or a timeline with no track entries) is a no-op: nothing here assumes it.
     See notes/tc_map.md for the layer's own contract. */
  function emitTracks(reason) {
    if (typeof window.onMapTracks !== 'function') return;
    try {
      window.onMapTracks({
        reason, proj, fit, mounted,
        ts: raw ? raw.frames[idx] : null,
        readout: raw ? fmtReadout(raw.frames[idx]).replace(/^Valid time: /, '') : '',
        region, tab: pageTab(), sel: Array.from(sel), previewId,
        events: tl ? tlEvents() : [],
      });
    } catch (err) { console.warn('track layer:', err.message); }
  }

  /* The wind asset can arrive AFTER the layer has drawn and the legend has been
     built (it is a separate fetch). Repaint and rebuild the legend, so the
     swatches and the canvas can never disagree about what is on the map. */
  window.onTracksAssetReady = () => {
    if (!mounted) return;
    emitTracks('asset');
    try { renderLegend(); } catch (err) { console.warn('track legend:', err.message); }
  };

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
  let curFl = null;      // flood class grid (u8) of the painted day, null = no flood evidence layer
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
    const fl = curFl;      // flood day frame paints even past the ERA5 cutoff (the rain source
                                       // — PARDIG by default — reaches later than ERA5 truth)
    if (!raw || (!frame && !fl)) { dimOutside(ctx); return; }
    const u8 = frame ? frame.bin : null;              // binary exceedance bits (4 planes)
    const run = (frame && raw.dur && frame.run) ? frame.run : null;   // null → binary paint (fallback)
    if (!fit) { dimOutside(ctx); return; }
    if (!lut || lut.cw !== cv.width || lut.ch !== cv.height) buildLut();
    const g = raw.grid, plane = g.ny * Math.ceil(g.nx / 8), cells = g.ny * g.nx;
    // plane 3 'bridged' (v8 assets): AR cells that are only a carried-over
    // footprint across a 1-step mask dropout. Feature-detected on the payload
    // length, so pre-v8 3-plane assets still paint exactly as before.
    const ghostPlane = u8 && u8.length >= 4 * plane ? 3 * plane : -1;
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
    const ivt = curIvt = (frame && raw.ivt && frame.ivt) ? frame.ivt : null;   // IVT class plane (u8, per-cell; hover reads curIvt)
    curPaint = { u8, plane, ghostPlane, ivtS2 };
    const n = cw * ch;
    for (let p = 0; p < n; p++) {
      const bi = byteIdx[p];
      if (bi < 0) continue;
      const m = bitMask[p];
      for (let a = 0; a < act.length; a++) {                  // later layers paint over earlier
        const li = act[a];
        if (li === 3) {
          // flood: a daily CLASS grid, not bit planes — class > 0 IS the rule
          // (union exceedance), the class is the accumulation bin
          const v = fl ? fl[cellIdx[p]] : 0;
          if (v > 0 && ramp[3]) px[p] = ramp[3][Math.min(v, LAYERS[3].bins) - 1];
        } else if (li === 2) {
          if (!u8) continue;                       // past-cutoff frame: no AR bits
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
        } else if (u8 && (u8[li * plane + bi] & m)) px[p] = packed[li];
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
    // (a TRACK event's preview outline is drawn by tracklayer.js instead)
    const ghost = previewId && !active.some((e) => e.id === previewId)
      ? tl.events.find((e) => e.id === previewId) : null;
    if (ghost && !isTrack(ghost)) {
      const gd = bboxPaths(ghost), gt = TYPES[ghost.type] || { css: 'heat' };
      if (gd) mk('path', { class: 'gm-ghost', d: gd, 'aria-hidden': 'true',
                           style: `stroke: var(--series-${gt.css}); fill: var(--series-${gt.css})` }, g);
    }
    for (const ev of active) {
      if (isTrack(ev)) continue;             // TRACK lane → tracklayer.js
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
    // the canvas describes what the CANVAS paints (layers + boxes); a track
    // lane is a separate layer with its own aria-label on #gm-track-marks
    const boxed = active.filter((e) => !isTrack(e));
    const nHeat = boxed.filter((e) => e.type === 'heat_wave').length;
    const nFrz = boxed.filter((e) => e.type === 'freeze').length;
    const nCold = boxed.filter((e) => e.type === 'cold_snap').length;
    const nAr = boxed.filter((e) => e.type === 'atmospheric_river').length;
    const nFl = boxed.filter((e) => e.type === 'flood').length;
    const nTk = active.length - boxed.length;
    const parts = [];
    if (nHeat) parts.push(`${nHeat} heat`);
    if (nFrz) parts.push(`${nFrz} freeze`);
    if (nCold) parts.push(`${nCold} cold snap`);
    if (nAr) parts.push(`${nAr} AR`);
    if (nFl) parts.push(`${nFl} flood`);
    $('#raw-canvas').setAttribute('aria-label',
      `Equal Earth world map — raw detector layers and event boxes: `
      + `${boxed.length} event${boxed.length === 1 ? '' : 's'} visible`
      + (parts.length ? ` (${parts.join(', ')})` : '')
      + (nTk ? ` — plus ${nTk} cyclone track${nTk === 1 ? '' : 's'} in the track layer` : ''));
  }
  const spanLon = (b) => (b[2] <= b[3] ? b[3] - b[2] : 360);
  function hoverRow(id, on) {                  // map box → its row in the event list
    const tr = document.querySelector(`#event-table tr[data-id="${CSS.escape(id)}"]`);
    if (tr) tr.classList.toggle('hov', on);
  }
  function preview(id) {                       // event list → outline on map + timeline (no canvas repaint)
    if (!mounted || !raw || previewId === id) return;
    previewId = id;
    renderBoxes(); renderGantt(); emitTracks('preview');
  }
  function layersForTab(tab) {                 // the page's type filter narrows the raw layers too
    // Owner F0 (2026-11-19): a single chip shows ONLY its own lane's raw
    // shading — heat/AR sheets under a flood chip read as flood evidence.
    // cold_snap has no raw layer of its own: the P15 (freeze) lane IS its
    // mask; flood has its own daily class frames (key 'l'). Lanes with no
    // raster at all (the cyclone TRACK lanes) turn every raster OFF: their
    // evidence is tracks and boxes, never another lane's shading.
    const key = { heat_wave: 'h', freeze: 'f', cold_snap: 'f',
                  atmospheric_river: 'a', flood: 'l' }[tab];
    for (const L of LAYERS) on[L.key] = tab === 'all' || (!!key && L.key === key);
  }

  /* ---------------- Gantt strip (qualified → end, shared time scale) ----------------
     Round 1 (2026-11-19) made the strip FIT its box (1 user unit = 1 CSS px, height
     a function of the LANE COUNT only) and stop bars HIDING each other (greedy
     sub-row packing). Round 2 (2026-11-20) makes it survive DENSE lanes: the live
     window has heat/AR/freeze, but a new-event run adds midlat cyclone (80),
     tropical cyclone (44) and flood (40), and a packed 7-deep lane in a fixed slot
     thinned its bars to 2.2 px hairlines. The strip now:
       · shows EVERY lane with events in the window, midlat_cyclone included;
       · keeps ONE bar height (G_BAR) everywhere — it never thins a bar below G_BAR;
       · lets a SPARSE lane (max concurrency <= G_DENSE) grow its slot to fit its
         sub-rows (bar + gap each), and
       · draws a DENSITY RIBBON for a DENSE lane instead of hairlines: a step area
         whose height is how many events are active at that moment, in the lane
         colour, clickable to jump the slider and labelled with a hover readout.
         Selected / list-previewed events of a dense lane are ALWAYS drawn as a
         full bar over the ribbon, so selection never disappears; the lane label
         toggles a dense lane between ribbon and fully-expanded packed rows.
     The height is still content-driven and depends ONLY on the data window and the
     expand toggles — never on hover or the frame — so the CLS reservation in
     styles.css (min-height) stays exact for the collapsed default view. */
  const G_BAR = 6;        // ONE uniform bar height (>= 5 px), every lane, never thinned
  const G_ROWGAP = 2;     // gap between sub-rows inside a lane
  const G_DENSE = 3;      // max concurrency <= this packs as sub-rows; more is a ribbon
  const G_RIB = 14;       // density-ribbon lane height (~2.3 bar heights)
  const G_GAP = 2;        // vertical gap between lane bands
  const G_AX = 13;        // month-label band under the lanes (a 10 px label, baseline 3 px up)
  const G_RIGHT = 12;     // trailing inset = slider thumb half-width (see styles.css)
  const G_MIN_W = 240;    // never shrink the axis below this
  const GUT = 2;          // x gutter between bars in one sub-row
  const NARROW_W = 600;   // below this container width, labels move ABOVE their lane
  const G_CAP = 11;       // the caption row a narrow strip puts each lane name on
  const G_LAB_MIN = 40, G_LAB_MAX = 150, G_LAB_FRAC = 0.34;   // label column limits
  // a lane's band height from its packed row count (>= 1) in bars-mode
  const G_BARS_H = (rows) => Math.max(G_BAR, rows * G_BAR + (rows - 1) * G_ROWGAP);
  // a bar's top within its lane (row 0 at the band top)
  const G_BAR_Y = (row) => row * (G_BAR + G_ROWGAP);
  const G_LANE_MIN = 12;  // wide: a band is never shorter than its centred label

  /* Lane labels are the strip's only text that must never be clipped: the old 44 px
     column clipped nothing only because the whole strip was scaled to 56%; at true
     size `Tropical cyclone` needs ~101 px of --text-2xs mono. Widths are measured
     once per font and cached — required for correctness, not just speed, because
     getComputedTextLength() returns 0 while the section is hidden (detail view
     open), and caching a 0 would collapse the column under the slider's margin and
     misalign the axis. Below G_LAB_FRAC of the width the names are ellipsised by
     CHARACTER (the label font is mono, so one advance is exact) and the full name
     moves to the label's tooltip. */
  const gLab = { font: '', ch: 0, w: new Map(), pad: G_LAB_MIN };
  function labelMetrics(svg, W, names) {
    const root = getComputedStyle(document.documentElement);
    const font = root.getPropertyValue('--text-2xs').trim() + '|'
      + root.getPropertyValue('--font-mono').trim();
    if (gLab.font !== font) { gLab.font = font; gLab.ch = 0; gLab.w.clear(); }
    let max = 0, ch = gLab.ch, ok = gLab.ch > 0;
    const probe = mk('text', { class: 'gm-lane-lab', x: 0, y: -9999 }, svg);
    for (const n of names) {
      let w = gLab.w.get(n);
      if (w === undefined) {
        probe.textContent = n;
        w = probe.getComputedTextLength();
        if (w > 0) gLab.w.set(n, w); else ok = false;   // hidden: measure nothing
      }
      if (w > 0) { max = Math.max(max, w); ch = Math.max(ch, w / n.length); }
    }
    probe.textContent = 'M';
    const em = probe.getComputedTextLength();
    probe.remove();
    if (em > 0) { ch = Math.max(ch, em); ok = true; }
    if (!ok) return { pad: gLab.w.size ? gLab.pad : G_LAB_MIN, name: (n) => n };
    // The label column is never allowed to eat more than 20 % of the strip, so the
    // time axis keeps >= 80 % of the width even on a phone (round 2: a clipped or
    // 30 %-wide label column made the axis unreadable at 390 px). If the widest
    // name still will not fit the column it is ellipsised (below), never clipped.
    const axisCap = Math.max(30, Math.floor(0.2 * W - G_RIGHT));
    const pad = Math.min(axisCap, Math.min(Math.max(G_LAB_MIN, Math.round(max) + 9), G_LAB_MAX,
      Math.max(G_LAB_MIN + 12, Math.round(W * G_LAB_FRAC))));
    gLab.pad = pad;
    const name = (short) => {
      if ((gLab.w.get(short) || 0) <= pad - 8) return short;
      const fits = Math.floor((pad - 8) / ch);
      return fits < 3 ? short.slice(0, 1) + '…'
        : short.slice(0, fits - 1).replace(/[\s.]+$/, '') + '…';
    };
    return { pad, name };
  }

  /* Two pieces of the strip are pure geometry, so they live apart from the SVG:
   they take numbers and return numbers, which means the rules that decide what
   the reader can and cannot see can be read — and argued with — without a
   browser. */

  /* Greedy interval packing on x, UNCAPPLIED: a bar takes the first row whose last
   bar has already ended, so a lane gets EXACTLY as many rows as its own overlap
   depth — no cap, no thinning, one G_BAR height on every row. The caller decides
   whether those rows are drawn (a sparse lane, or a dense lane the reader expanded)
   or replaced by a density ribbon. Bars in one row never overlap in x and rows never
   overlap in y, so a drawn bar is always visible and clickable. Returns the row
   count (the lane's max concurrency), which also picks sparse vs dense. */
  function packLane(items) {
    items.sort((a, b) => a.bx - b.bx || a.x1 - b.x1);   // left to right, ties: early end first
    const ends = [];
    for (const it of items) {
      const free = ends.findIndex((e) => it.bx >= e);
      it.row = free >= 0 ? free : ends.length;
      if (it.row === ends.length) ends.push(-Infinity);
      ends[it.row] = it.x1 + GUT;
    }
    return { rows: ends.length };
  }

  /* A dense lane's DENSITY RIBBON: one rectangle per constant-activity span, whose
   height is how many of the lane's events are active in that span, scaled to the
   lane's peak. It is a step area, not bars: the reader sees WHERE the lane crowds
   without a smear of hairlines, each span answers hover with "N active, <date>", and
   clicking a span jumps the slider to it. Returns spans [{xa, xb, n, h}] sized inside
   a lane band of `h` pixels (a single active event clamps to >= 2 px, so it stays visible). */
  function ribbonSpans(items, h) {
    if (!items.length) return { spans: [], peak: 0 };
    const lo = items[0].bx, hi = items[items.length - 1].x1;
    const cl = (v) => Math.max(lo, Math.min(hi, v));
    const bounds = [...new Set([lo, hi, ...items.map((it) => cl(it.bx)), ...items.map((it) => cl(it.x1))])]   // sorted after clamping
      .sort((a, b) => a - b).filter((v, i, a) => !i || v > a[i - 1] + 0.01);
    const active = (mid) => items.reduce((n, it) => n + (it.bx <= mid && it.x1 > mid ? 1 : 0), 0);
    let peak = 1;
    const spans = [];
    for (let i = 0; i < bounds.length - 1; i++) {
      const xa = bounds[i], xb = bounds[i + 1];
      const n = active((xa + xb) / 2);
      if (n <= 0) continue;                                  // gap: leave the lane ground bare
      peak = Math.max(peak, n);
      spans.push({ xa, xb, n, h: n });                        // height filled after peak is known
    }
    for (const s of spans) s.h = Math.max(2, Math.round((s.n / peak) * h));
    return { spans, peak };
  }

  /* The axis's own calendar: Mondays once a week is wide enough to tell them
   apart (16 px), month starts when it is not. `named` marks the tick that carries
   a month label — the first Monday of a month in week mode, every month start in
   month mode — and the caller drops labels closer together than the text is wide. */
  function timeTicks(t0, t1, pxDay) {
    const weekly = pxDay * 7 >= 16;
    const d = new Date(t0);
    d.setUTCDate(d.getUTCDate() + 1); d.setUTCHours(0, 0, 0, 0);
    if (weekly) while (d.getUTCDay() !== 1) d.setUTCDate(d.getUTCDate() + 1);
    else while (d.getUTCDate() !== 1) d.setUTCDate(d.getUTCDate() + 1);
    const out = [];
    for (; d.getTime() <= t1; weekly ? d.setUTCDate(d.getUTCDate() + 7) : d.setUTCMonth(d.getUTCMonth() + 1))
      out.push({ ms: d.getTime(), named: weekly ? d.getUTCDate() <= 7 : true,
                 label: d.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' }) });
    return out;
  }

  function renderGantt() {
    const svg = $('#gm-gantt');
    svg.innerHTML = '';
    if (!tl) { svg.removeAttribute('viewBox'); return; }
    // The strip's OWN content box is the geometry: `width: 100%` makes it the
    // card's content width, and the slider sits in that same box (same origin x,
    // same width), so [LAB, W - G_RIGHT] is exactly the thumb's travel and 1 user
    // unit is 1 CSS px. Hidden (detail view open) it measures 0 — fall back to the
    // map column's width minus the card's gutters rather than paint a 0-wide axis.
    const own = svg.clientWidth || $('#raw-map-wrap').clientWidth - 26;
    const W = Math.max(G_MIN_W, own);
    // below NARROW_W there is no room for a label column that is ALSO >= 80 % axis,
    // so the names move to a caption row above each lane and the axis gets the full
    // width (round 2: a 30 %-wide column clipped "Tropical cyclone" at 390 px)
    const narrow = W < NARROW_W;
    let shortNames = narrow;
    const labOf = (type) => (shortNames ? (TYPES[type].narrow || TYPES[type].short) : TYPES[type].short);
    const t0 = tsToDate(raw.frames[0]).getTime();
    const t1 = tsToDate(raw.frames[raw.frames.length - 1]).getTime();
    const span = Math.max(1, t1 - t0);
    const anySel = sel.size > 0;
    const tab = pageTab();

    /* 1 · filter first — the region view and the frame-window clamp behave exactly
     as before, because the label column is sized to the names this strip will
     actually DRAW. A LANE belongs to the strip when its type has any event in the
     window (Region then hides BARS, never lanes), so the strip's height and its
     label column — hence the axis origin — stay constant across the frame step,
     the type tab and the Region dropdown: that is what the CLS reservation in
     styles.css and the "nothing below the legend moved" contract need. */
    const inWindow = (ev) => qualTs(ev) <= raw.frames[raw.frames.length - 1] && endTs(ev) >= raw.frames[0];
    const lanes = LANE_ORDER.filter((type) => tl.events.some((ev) => ev.type === type && inWindow(ev)));

    /* Which lanes are DENSE is decided from the overlap DEPTH, which a greedy
     packing count gives the same way at any width — so it can be known before the
     label column (which needs it to reserve ▸/▾ room for the expand toggle). */
    const depthOf = (items) => {
      const iv = items.map((ev) => [tsToDate(qualTs(ev)).getTime(), tsToDate(endTs(ev)).getTime()])
        .sort((a, b) => a[0] - b[0]);
      const ends = []; let rows = 0;
      for (const [a, b] of iv) {
        const f = ends.findIndex((e) => a >= e); const r = f >= 0 ? f : ends.length;
        if (r === ends.length) ends.push(0); ends[r] = b; rows = Math.max(rows, ends.length);
      }
      return rows;
    };
    const laneItems = new Map(lanes.map((type) =>
      [type, tlEvents().filter((ev) => ev.type === type && inWindow(ev))]));
    const dense = new Set(lanes.filter((type) => depthOf(laneItems.get(type)) > G_DENSE));
    // WIDE: names sit in a left column sized to them (caret reserved for dense lanes).
    // NARROW: the column would eat the axis, so LAB collapses to 0, the axis runs the
    // full width, and each lane is named on a caption row above its band instead.
    let LAB, laneName;
    if (narrow) { LAB = 0; laneName = (s) => s; }
    else {
      const names = () => lanes.map((type) => (dense.has(type) ? '▸ ' : '') + labOf(type));
      ({ pad: LAB, name: laneName } = labelMetrics(svg, W, names()));
      // a column too narrow for a full name takes the short forms (TC, MLC), not an ellipsis
      if (names().some((n) => laneName(n) !== n)) {
        shortNames = true;
        ({ pad: LAB, name: laneName } = labelMetrics(svg, W, names()));
      }
    }
    $('#global-sec').style.setProperty('--gm-lab', LAB + 'px');

    const x = (ms) => LAB + Math.min(1, Math.max(0, (ms - t0) / span)) * (W - LAB - G_RIGHT);
    const xInv = (px) => t0 + ((px - LAB) / (W - LAB - G_RIGHT)) * span;   // px → ms (ribbon click)
    const pxDay = (W - LAB - G_RIGHT) / (span / 86400e3);
    const BW_MIN = Math.max(2, Math.min(4, pxDay * 0.8));   // a day stays findable, capped at 4 px

    /* 2 · pack each lane (rows = its own concurrency), then choose a mode: a lane
     with nothing to draw is an empty rule; a DENSE lane collapsed is a ribbon; a
     dense lane the reader expanded, or any SPARSE lane, draws packed sub-rows at
     ONE uniform bar height. Band height follows from the mode, and bands are then
     stacked — so the strip grows for content, never for crowding-in-a-slot. */
    const groups = lanes.map((type) => {
      const items = laneItems.get(type).map((ev) => {
        const q = qualTs(ev), e = endTs(ev);
        const bx = x(tsToDate(q).getTime());
        const bw = Math.max(BW_MIN, x(tsToDate(e).getTime()) - bx);
        return { ev, q, bx, bw, x1: bx + bw, row: 0 };
      });
      const { rows } = packLane(items);
      const isDense = dense.has(type);
      const expanded = gExpand.has(type);
      const mode = rows === 0 ? 'empty' : (isDense && !expanded ? 'ribbon' : 'bars');
      const bh = mode === 'empty' ? 2 : (mode === 'ribbon' ? G_RIB
        : (narrow ? G_BARS_H(rows) : Math.max(G_LANE_MIN, G_BARS_H(rows))));
      return { type, items, rows, isDense, expanded, mode, bh, lab: labOf(type) };
    });
    let top = 0;
    for (const g of groups) {
      // narrow: a caption row (G_CAP) sits ABOVE the band for a named lane, so the
      // axis can run the full width with no left column; g.y stays the BAND top.
      g.capH = (narrow && g.rows) ? G_CAP : 0;
      g.capY = top;
      g.y = top + g.capH;
      top += g.capH + g.bh + G_GAP;
    }
    const gridBot = groups.length ? groups[groups.length - 1].y + groups[groups.length - 1].bh : 0;
    const H = gridBot + G_AX;
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    // the box IS the viewBox: nothing is ever scaled, whatever the lane count
    svg.style.height = H + 'px'; svg.style.minHeight = H + 'px';

    const msLabel = (ms) => new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

    /* 3 · lane chrome + labels. Every non-empty lane is named (as on the chips); a
     DENSE lane's label is a button that toggles it between ribbon and expanded rows
     (aria-expanded, ▸/▾ affordance, keyboard). An empty lane collapses to a 2 px
     rule with NO label, handing its column back to the axis. */
    groups.forEach((g, i) => {
      mk('rect', { class: 'gm-lane' + (i % 2 ? ' gm-lane-alt' : '') + (g.rows ? '' : ' gm-lane-none'),
                   x: LAB, y: g.y, width: W - LAB - G_RIGHT, height: g.bh, rx: Math.min(2.5, g.bh / 2) }, svg);
      if (i) mk('line', { class: 'gm-lane-sep', x1: 0, x2: W - G_RIGHT,
                          y1: g.capY - G_GAP / 2, y2: g.capY - G_GAP / 2 }, svg);
      if (!g.rows) return;
      const caret = g.isDense ? (g.expanded ? '▾ ' : '▸ ') : '';
      const full = caret + g.lab;
      const labY = narrow ? g.capY + G_CAP - 2 : g.y + g.bh / 2;   // above the band, or centred in it
      const t = mk('text', { class: 'gm-lane-lab' + (g.isDense ? ' toggle' : ''),
                             x: 0, y: labY, dy: narrow ? '0' : '.36em' }, svg);
      t.textContent = laneName(full);
      if (g.isDense) {
        t.setAttribute('role', 'button'); t.setAttribute('tabindex', '0');
        t.setAttribute('aria-expanded', String(g.expanded));
        mk('title', {}, t).textContent = g.lab + ' — ' + g.rows + ' at once: click to '
          + (g.expanded ? 'collapse to a density ribbon' : 'expand every bar');
        const toggle = (e) => { e.stopPropagation();
          if (gExpand.has(g.type)) gExpand.delete(g.type); else gExpand.add(g.type); render(); };
        t.addEventListener('click', toggle);
        t.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e); } });
      } else if (t.textContent !== full) mk('title', {}, t).textContent = g.lab;   // ellipsis: full name on hover
    });

    /* 4 · event marks. A bars lane paints every item at G_BAR height, one row per
     sub-row. A ribbon lane paints its density spans (click jumps the slider) and
     then ALWAYS re-paints a selected / list-previewed event as a full bar on top,
     so a selection is never lost inside the ribbon. */
    const drawBar = (g, it, over) => {
      const ev = it.ev;
      const isSel = sel.has(ev.id) || previewId === ev.id;
      const by = g.mode === 'ribbon' ? g.y + (g.bh - G_BAR) / 2
        : g.y + (g.bh - G_BARS_H(g.rows)) / 2 + G_BAR_Y(it.row);   // packed rows centred in the band
      const bar = mk('rect', {
        class: 'gm-bar', 'data-id': ev.id, x: it.bx, y: by,
        width: it.bw, height: G_BAR, rx: Math.min(2.5, G_BAR / 2),
        style: `fill: var(--series-${TYPES[g.type].css})`,
        opacity: isSel ? 1 : (anySel ? .18 : (tab !== 'all' && tab !== g.type ? .18 : .85)),
        stroke: isSel ? 'var(--canvas)' : 'none', 'stroke-width': isSel ? 1 : 0,
        cursor: 'pointer',
      }, svg);
      mk('title', {}, bar).textContent = `${ev.title} — qualified ${fmtShort(it.q)}${evSpan(ev)}`;
      bar.addEventListener('click', () => {
        if (!sel.has(ev.id)) sel.add(ev.id); else sel.delete(ev.id);
        jumpTo(it.q); writeUrl(true); render();
      });
    };
    for (const g of groups) {
      if (g.mode === 'bars') {
        for (const it of g.items) drawBar(g, it, false);
      } else if (g.mode === 'ribbon') {
        const { spans } = ribbonSpans(g.items, g.bh);
        const dim = anySel ? .3 : (tab !== 'all' && tab !== g.type ? .28 : .8);
        const grp = mk('g', { class: 'gm-ribbon' }, svg);
        for (const s of spans) {
          const r = mk('rect', { class: 'gm-rib-seg', x: s.xa, y: g.y + g.bh - s.h,
                                 width: Math.max(0.6, s.xb - s.xa), height: s.h,
                                 style: `fill: var(--series-${TYPES[g.type].css})`,
                                 opacity: dim, cursor: 'pointer' }, grp);
          mk('title', {}, r).textContent = `${g.lab} — ${s.n} active, ${msLabel(xInv(s.xa))}`;
          r.addEventListener('click', () => {
            const ts = tsToStr(new Date(xInv(s.xa)));
            jumpTo(ts); writeUrl(true); render();
          });
        }
        for (const it of g.items) {                 // selection always on top of the ribbon
          if (sel.has(it.ev.id) || previewId === it.ev.id) drawBar(g, it, true);
        }
      }
    }

    /* 5 · cursor + time axis: the current frame is a 1 px ink line over the lanes
     (G-23 lineage, still the topmost thing painted); ticks are Mondays when a
     week is wide enough to tell them apart and month starts when it is not; month
     names sit under the axis and never closer than 34 px. */
    const cx = x(tsToDate(raw.frames[idx]).getTime());
    mk('line', { class: 'gm-cursor', x1: cx, x2: cx, y1: 0, y2: gridBot }, svg);
    let lastLab = -1e9;
    for (const tk of timeTicks(t0, t1, pxDay)) {
      const tx = x(tk.ms);
      mk('line', { class: 'gm-tick' + (tk.named ? '' : ' minor'),
                   x1: tx, x2: tx, y1: 0, y2: gridBot }, svg);
      if (!tk.named || tx - lastLab < 34) continue;   // 34 px: a month name plus a gap
      lastLab = tx;
      mk('text', { class: 'gm-month', x: tx + 2, y: H - 3 }, svg).textContent = tk.label;
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
    const noTruth = (raw.no_truth || []).includes(ts);   // blank because ERA5 has no data
    badge.classList.toggle('hidden', !past && !noTruth);   // .sec-badge sets display; class hides it
    // design-recommended copy (19:05): honest end-of-frames marker, not an IFS claim
    badge.textContent = past ? `raw layers end ${fmtShort(raw.cutoff)} ${raw.cutoff.slice(8, 10)}Z · ERA5 lag`
      : noTruth ? 'No ERA5 data for this step' : '';
    renderBoxes();
    renderGantt();
    emitTracks('frame');       // track lanes follow the step, the tab and the region
    Promise.all([getFrame(idx), getFl(idx)]).then(([frame, fl]) => {
      if (seq !== renderSeq) return;
      curFl = fl;                          // flood day frame of THIS step (flood_frames contract)
      const note = $('#raw-note');
      if (frame === null && ts && !past) {
        note.classList.remove('hidden');
        note.textContent = `Frame ${fmtReadout(ts)} is not available yet — `
          + 'the raw exceedance build has not produced it.';
        legStats = censusFrame(frame);     // a flood day frame still answers
        paintFrame(frame);
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
      if (LAYERS.every((L) => !on[L.key])) LAYERS.forEach((L) => { on[L.key] = true; });
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
    flood:  { name: 'Flood', unit: 'mm/day-equiv',
      flat: 'over threshold', flatTip: 'Over the flood rule — the accumulation class frame is unavailable, so one flat colour paints every exceeded cell' },
  };
  // fallback legend edges when index.flood.bins_mm is absent (kept in sync
  // with ewb_live/viz/flood_frames.BINS_MM)
  const FLOOD_BINS_FALLBACK = [20, 50, 100, 200, 350];
  let legStats = null;                         // lane census of the painted frame
  let legHtml = '';                            // last rendered markup (playback: no reflow churn)
  let legOpen = false;                         // "Scales" expanded (labelled ramps) vs compact swatches

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
    if (!raw) return null;
    const key = region ? region.key : 'global';
    const fl = curFl;
    if (!frame || !frame.bin) {
      if (!fl) return null;
      // past the ERA5 cutoff (or a missing .bin): only the flood day frame
      // can still describe what paints — every other lane draws nothing
      const s = { key, durV2: false,
                  ramp: { heat: false, freeze: false, ar: false, flood: true },
                  n: LAYERS.map((L) => new Uint8Array(L.bins)), prov: LAYERS.map(() => 0) };
      for (let c = 0; c < fl.length; c++) {
        const v = fl[c];
        if (v > 0) s.n[3][Math.min(v, LAYERS[3].bins) - 1] = 1;
      }
      if (frame) { frame.leg = s; frame.legKey = key; }
      return s;
    }
    if (frame.legKey === key) return frame.leg;
    const g = raw.grid, nx = g.nx, ny = g.ny, cells = nx * ny;
    const durV2 = !!(raw.dur && String(raw.dur.rev || '').indexOf('v2') === 0);
    const run = (raw.dur && frame.run) ? frame.run : null;
    const ivt = (raw.ivt && frame.ivt) ? frame.ivt : null;
    const s = { key, durV2, ramp: { heat: !!run, freeze: !!run, ar: !!ivt, flood: !!fl },
                n: LAYERS.map((L) => new Uint8Array(L.bins)), prov: LAYERS.map(() => 0) };
    const clampGate = (li) => (li >= 2 ? 1
      : Math.min(Math.max(minDays(LAYERS[li]), 1), LAYERS[li].bins));
    const reach = (li) => (li === 2 ? (ivt ? LAYERS[2].bins : 1)
      : li === 3 ? (fl ? LAYERS[3].bins : 0)
      : !run ? 1 : LAYERS[li].bins - clampGate(li) + 1 + (durV2 ? 1 : 0));
    const need = LAYERS.map((L, li) => reach(li));    // stops still missing per lane
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
        if (need[3] && fl) {                          // flood: daily class grid
          const v = fl[c];
          if (v > 0) hit(3, Math.min(v, LAYERS[3].bins) - 1);
        }
      }
    };
    for (let j = j0; j <= j1 && need.reduce((a, b) => a + b, 0) > 0; j++) {
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
        if (L.name === 'flood' && !raw.flood) return;      // no asset on this site: no phantom group
        const M = LEGEND[L.name];
        const n = cen ? cen.n[li] : null;                  // null = nothing painted yet
        const ramp = !cen || cen.ramp[L.name];
        const live = n ? n.reduce((a, b) => a + b, 0) : -1;  // stops present in this frame
        const from = L.name === 'ar' ? 1 : Math.min(Math.max(minDays(L), 1), L.bins);
        // flood legend stops are mm/day-equivalent ranges, not day counts
        const fe = L.name === 'flood'
          ? ((raw.flood && Array.isArray(raw.flood.bins_mm) && raw.flood.bins_mm.length === L.bins
              && raw.flood.bins_mm) || FLOOD_BINS_FALLBACK) : null;
        const stops = [];
        if (ramp) {
          // only stops the lane can paint: days below the gate never reach the canvas
          for (let b = from; b <= L.bins; b++) {
            const lo = IVT_BINS[b - 1];
            stops.push(legStop(
              `var(--dur-${L.name}-${b})`,
              // top class reads '1250+' — same '+ suffix as the day ramps; the
              // caption column stays narrow enough that the digits never touch
              L.name === 'ar' ? (b < L.bins ? String(lo) : `${lo}+`)
                : fe ? (b < L.bins ? `${fe[b - 1]}\u2013${fe[b]}` : `${fe[L.bins - 1]}+`)
                : dayLbl(b, L.bins),
              L.name === 'ar'
                ? (b < L.bins ? `${lo}\u2013${IVT_BINS[b]} kg m\u207b\u00b9 s\u207b\u00b9` : `\u2265${lo} kg m\u207b\u00b9 s\u207b\u00b9`)
                : fe
                  ? (b < L.bins
                     ? `${fe[b - 1]}\u2013${fe[b]} mm/day-equivalent \u2014 max(daily rain, 72 h total / 3)`
                     : `\u2265${fe[L.bins - 1]} mm/day-equivalent \u2014 max(daily rain, 72 h total / 3)`)
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
        const tip = (L.key === 'l' && raw && raw.flood && raw.flood.tip) || L.tip || M.name;
        groups.push(`<span class="lg-group${none ? ' none' : ''}" data-layer="${L.key}"`
          + ` title="${tip}">`
          + `<span class="lg-head"><span class="lg-name">${M.name}</span>`
          + `<span class="lg-unit">${M.unit}</span>`
          + (none ? '<span class="lg-none">none in view</span>' : '') + '</span>'
          + `<span class="lg-stops">${stops.join('')}</span></span>`);
      });
    }
    /* TRACK lanes contribute their own groups (tracklayer.js): a lane only
       appears when it has published events, so the raster-only pages keep
       their exact legend contract (counts, widths, fixed height). */
    if (typeof window.onTrackLegend === 'function') {
      try {
        const extra = window.onTrackLegend({ tab: pageTab(), ts: raw && raw.frames[idx],
          region, events: tl ? tlEvents() : [], sel: Array.from(sel) });
        if (extra) groups.push(extra);
      } catch (err) { console.warn('track legend:', err.message); }
    }
    /* COMPACT by default once three or more lanes share the strip (owner
       2026-11-19: the 7-lane legend was 154 px of the first screen): only each
       lane's name and its colour swatches — every swatch keeps its tooltip, the
       lane's group keeps its own — and a "Scales" control opens the labelled ramps
       (the old layout). One or two lanes (a type tab) are already one short row
       and always show their labels. */
    const compact = groups.length >= 3 && !legOpen;
    const toggle = groups.length >= 3
      ? `<button type="button" class="lg-toggle" aria-expanded="${legOpen}" `
        + `title="${legOpen ? 'Hide' : 'Show'} the numbers under each colour scale">`
        + `Scales <span aria-hidden="true">${legOpen ? '▴' : '▾'}</span></button>` : '';
    const html = toggle + groups.join('');
    leg.classList.toggle('compact', compact);
    if (html === legHtml) return;              // same legend: no reflow per frame
    legHtml = html;
    leg.innerHTML = html;
    // never display:none: an EMPTY strip keeps its CLS reservation (styles.css
    // #gm-legend:empty) — a filled one is exactly as large as its rows — and
    // "all layers off" cannot happen anyway
    leg.classList.remove('hidden');
    legendHug();
  }

  /* A compact strip whose WHOLE natural row fits the card hugs its scales in
     one row (round 1's fit-content promise: a strip that fits must not pad
     out to the map column); one that does not fits the map column in equal
     aligned grid columns — its edges are the map's. "wide" is the CSS's call
     (the --lg-wide custom property, set by the same container query that
     switches the layout); "fits one row" is arithmetic over the children's
     NATURAL widths: head and stops are content-sized flex items, so they
     keep their natural width even while the grid stretches their group box.
     The inline track list (repeat(<items>, max-content)) only ever lands on
     the compact wide grid — every other state clears it. Runs on legend
     rebuilds and on container resizes, never per animation frame. */
  function legendHug() {
    const leg = $('#gm-legend');
    if (!leg) return;
    const groups = leg.querySelectorAll('.lg-group');
    let single = false;
    if (groups.length && leg.classList.contains('compact')
        && getComputedStyle(leg).getPropertyValue('--lg-wide').trim() === '1') {
      const cs = getComputedStyle(leg);
      const px = (v) => parseFloat(v) || 0;
      const colGap = px(cs.columnGap);           // between the strip's items
      const headGap = px(getComputedStyle(groups[0]).columnGap);   // head|ramp
      let need = px(cs.paddingLeft) + px(cs.paddingRight);
      leg.querySelectorAll(':scope > *').forEach((el) => {
        const head = el.querySelector('.lg-head');
        const stops = el.querySelector('.lg-stops');
        need += head && stops ? head.getBoundingClientRect().width + headGap
                              + stops.getBoundingClientRect().width
          : el.getBoundingClientRect().width;
      });
      need += colGap * (leg.children.length - 1);
      single = need <= leg.clientWidth + 1;
    }
    leg.classList.toggle('hug', single);
    // a wrapped compact strip lays its lanes in k PAIRS of `label | swatches`
    // tracks (each group a subgrid over its pair), so every row's swatches start
    // at the same x within a column; the toggle rides in the reserved top-right
    // corner instead of opening a row of its own
    let cols = 0;
    const toggle = leg.querySelector(':scope > .lg-toggle');
    if (!single && groups.length > 1 && leg.classList.contains('compact')
        && getComputedStyle(leg).getPropertyValue('--lg-wide').trim() === '1') {
      const px = (v) => parseFloat(v) || 0;
      const cs = getComputedStyle(leg);
      const gap = px(cs.columnGap);
      let head = 0, stops = 0;
      groups.forEach((g) => {
        head = Math.max(head, g.querySelector('.lg-head').getBoundingClientRect().width);
        stops = Math.max(stops, g.querySelector('.lg-stops').getBoundingClientRect().width);
      });
      const reserve = toggle ? toggle.getBoundingClientRect().width + 16 : px(cs.paddingRight);
      const avail = leg.clientWidth - px(cs.paddingLeft) - reserve;
      cols = Math.max(1, Math.min(groups.length,
        Math.floor((avail + gap) / (head + stops + 2 * gap))));
      leg.style.paddingRight = reserve + 'px';
    } else leg.style.paddingRight = '';
    leg.classList.toggle('cols', cols > 0);
    leg.style.gridTemplateColumns = single
      ? `repeat(${leg.children.length}, max-content)`
      : (cols ? `repeat(${cols}, max-content minmax(max-content, 1fr))` : '');
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
    $('#gm-legend').addEventListener('click', (ev) => {     // the "Scales" expander
      if (!ev.target.closest('.lg-toggle')) return;
      legOpen = !legOpen;
      legHtml = '';                                          // force the repaint
      renderLegend();
    });
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
      emitTracks('theme');     // the track layer bakes lane/ramp colours too
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
        raf = requestAnimationFrame(() => {
          if (mounted) { fitProjection(); render(); legendHug(); }
        });
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
    /* the two ways another layer may act on the map (tracklayer.js): select an
       event (the same multi-highlight + #sel= a box click does) and open its
       detail page; hover mirrors the box ↔ event-list row highlight. */
    select: toggleSel, open: openEvent, hover: hoverRow,
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
                >> (7 - (i & 7))) & 1; },
      // the flood CLASS of a cell in the painted day frame (0 = no evidence):
      // the flood layer answers from the daily grid, not the 6-hourly bits
      flAt: (j, i) => (curFl && raw ? (curFl[j * raw.grid.nx + i] || 0) : 0) }) };
})();
