/* ==========================================================================
   rawmap.js — RAW EXCEEDANCE MAP (owner 2026-11-08, briefs/raw_exceedance_map.md).
   Second global view: RAW detector threshold exceedances (heat > p85,
   freeze < p15, AR mask) at every 6-hourly ERA5 timestep, NO event boxes.
   Equal Earth (d3-geo), canvas rendering with per-latitude-row strip
   drawImage (crisp cells, >= 10 fps), 3 toggleable layers, slider + play
   with speed control, URL state #raw=YYYYMMDDHH&layers=h,f,a riding the
   app's history plumbing (same contract style as GlobalDash).
   Data contract: data/results/raw_exceedance/index.json + <YYYYMMDDHH>.bin
   = zlib(layer-major bit-packed rows: heat,freeze,ar; each ny × ceil(nx/8),
   row 0 = lat0 (lowest), MSB-first — parity with eventviz daily_bits).
   Frames after index.cutoff are IFS-analysis and labelled.
   Reduced motion: playback is frame-stepping only (no tween); nothing
   auto-plays without a click.
   ========================================================================== */
(function () {
  'use strict';

  const PAD = 12;
  const LAYERS = [                       // draw order bottom→top
    { key: 'h', name: 'heat',   label: 'Heat > P85' },
    { key: 'f', name: 'freeze', label: 'Freeze < P15' },
    { key: 'a', name: 'ar',     label: 'AR mask', tip: 'AR mask (EWB, before cyclone screen): the raw upstream detector output. The event list additionally screens out tropical cyclones (IBTrACS).' },
  ];
  const SPEEDS = [2, 5, 10];             // fps choices

  let data = null;                       // index.json
  let idx = 0;                           // current frame index
  let on = { h: true, f: true, a: true }; // layer toggles
  let mounted = false;
  let playing = null, speedI = 1;
  let land = null, graticule = null, proj = null, path = null;
  let fit = null;                        // {W,H,rowTops:[...], rowBots, halfW:[..]}
  const frameCache = new Map();          // ts -> Uint8Array | null
  const CACHE_MAX = 7;

  const $ = (s) => document.querySelector(s);

  /* ---------------- frame time helpers ---------------- */
  const tsToDate = (ts) => new Date(Date.UTC(+ts.slice(0, 4), +ts.slice(4, 6) - 1,
    +ts.slice(6, 8), +ts.slice(8, 10)));
  const urlLayers = () => {              // positive list, only when not all-on
    const k = LAYERS.filter((L) => on[L.key]).map((L) => L.key);
    return k.length === 3 ? null : k.join(',');
  };
  function fmtReadout(ts) {
    const d = tsToDate(ts);
    const o = (opt) => d.toLocaleDateString('en-US', { ...opt, timeZone: 'UTC' });
    // brief format: DD Mon YYYY, HH:MM UTC (day-first)
    return `Valid time: ${o({ day: '2-digit' })} ${o({ month: 'short' })} `
      + `${o({ year: 'numeric' })}, ${String(d.getUTCHours()).padStart(2, '0')}:00 UTC`;
  }

  /* ---------------- data load ---------------- */
  function getFrame(i) {                 // -> Promise<Uint8Array|null>
    const ts = data.frames[i];
    if (!ts) return Promise.resolve(null);
    if (frameCache.has(ts)) return Promise.resolve(frameCache.get(ts));
    return fetch(`data/results/raw_exceedance/${ts}.bin`, { cache: 'no-cache' })
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
        console.warn('raw frame unavailable:', ts, err.message);
        frameCache.set(ts, null); return null;
      });
  }
  const prefetch = (i) => { [i - 1, i + 1].forEach((j) => {
    if (data && j >= 0 && j < data.frames.length) getFrame(j).catch(() => {}); }); };

  /* ---------------- projection / fit ---------------- */
  function fitProjection() {
    const wrap = $('#raw-map-wrap');
    const W = Math.max(240, wrap.clientWidth);
    proj = d3.geoEqualEarth();
    path = d3.geoPath(proj.scale(1).translate([0, 0]));
    const [[x0, y0], [x1, y1]] = path.bounds({ type: 'Sphere' });
    const dw = x1 - x0, dh = y1 - y0;
    const H = Math.round(Math.min((dh / dw) * (W - 2 * PAD) + 2 * PAD, W * 0.75));   // hug the map: no dead band on phones
    proj.fitExtent([[PAD, PAD], [W - PAD, H - PAD]], { type: 'Sphere' });
    path = d3.geoPath(proj);
    fit = { W, H };
    lut = null;                                    // rebuilt lazily for the new size (buildLut)
    const cv = $('#raw-canvas');
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cv.style.width = W + 'px'; cv.style.height = H + 'px';
    wrap.style.minHeight = H + 'px';
  }

  function drawBase(ctx) {
    const dpr = window.devicePixelRatio || 1;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, fit.W, fit.H);
    const cs = getComputedStyle(document.documentElement);
    const sphere = new Path2D(path({ type: 'Sphere' }));
    ctx.fillStyle = cs.getPropertyValue('--map-ocean').trim() || '#f2f1e6';   // ocean tint, as the global map (#31)
    ctx.fill(sphere);
    ctx.strokeStyle = cs.getPropertyValue('--border-strong').trim() || '#999';
    ctx.lineWidth = 0.8;
    ctx.stroke(sphere);
    ctx.strokeStyle = cs.getPropertyValue('--border-strong').trim() || '#999';
    ctx.lineWidth = 0.5;
    ctx.save();
    ctx.globalAlpha = 0.18;                       // graticule opacity, gm parity
    ctx.stroke(new Path2D(path(graticule)));
    ctx.restore();
    if (land) {
      ctx.fillStyle = cs.getPropertyValue('--paper').trim() || '#e8e8e8';
      ctx.strokeStyle = cs.getPropertyValue('--border-strong').trim() || '#bbb';
      ctx.lineWidth = 0.6;
      ctx.fill(new Path2D(path(land)));
      ctx.stroke(new Path2D(path(land)));
    }
    ctx.restore();
  }

  /* ---------------- raster cells ----------------
     Inverse-mapped: for every DEVICE pixel the source grid cell is looked up from
     a LUT built once per size (Equal Earth is pseudocylindrical, so each screen
     row is ONE latitude and longitude is linear in x — only two projection calls
     per screen row, no per-pixel invert). The previous per-latitude-row
     drawImage strips were sub-pixel tall (0.25° ≈ 0.5 px), so Math.ceil()
     made neighbours overlap: alpha doubled on every overlap (the scanline
     stripes) and the single pole row was stretched to a full-width band along
     the bottom edge. One pixel → one cell has no overlaps, no gaps, wraps
     cleanly at the date line and covers a pole row with ~0.25 px, not a band. */
  let lut = null, rasterCv = null;
  function buildLut() {
    const g = data.grid, nx = g.nx, ny = g.ny, stride = Math.ceil(nx / 8);
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(fit.W * dpr), ch = Math.round(fit.H * dpr);
    const byteIdx = new Int32Array(cw * ch).fill(-1);
    const bitMask = new Uint8Array(cw * ch);
    const cx = proj.translate()[0];
    // d3's invert CLAMPS outside the sphere (rows below the south pole came back as
    // lat −90 and painted the pole row as a slab under Antarctica) — bound the rows
    const yTop = proj([0, 90])[1], yBot = proj([0, -90])[1];
    for (let y = 0; y < ch; y++) {
      const sy = (y + 0.5) / dpr;
      if (sy < yTop || sy > yBot) continue;
      const ll = proj.invert([cx, sy]);                       // latitude of this screen row
      if (!ll || !Number.isFinite(ll[1]) || Math.abs(ll[1]) > 90) continue;
      const lat = ll[1];
      const j = Math.round((lat - g.lat0) / g.step_lat);       // cell CENTRES (ERA5-style grid)
      // rows 0 and ny-1 ARE the poles: one point replicated along the whole pole line, so a
      // flagged pole shows as a hairline across the sphere's edge — leave the pole rows blank
      if (j <= 0 || j >= ny - 1) continue;
      const kx = proj([1, lat])[0] - proj([0, lat])[0];        // screen px per degree of lon here
      if (!(kx > 0)) continue;
      const rowOff = j * stride, o = y * cw;
      for (let x = 0; x < cw; x++) {
        const lon = ((x + 0.5) / dpr - cx) / kx;
        if (lon < -180 || lon > 180) continue;                 // outside the sphere outline
        let i = Math.round((lon - g.lon0) / g.step_lon);
        i = ((i % nx) + nx) % nx;                              // +180 wraps onto the -180 column
        byteIdx[o + x] = rowOff + (i >> 3);
        bitMask[o + x] = 1 << (7 - (i & 7));
      }
    }
    lut = { cw, ch, byteIdx, bitMask };
  }
  function rgbOf(v) {
    // CSS token -> [r,g,b] via a probe element (handles hex/var names alike)
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
    if (!u8 || !data || !fit) return;
    if (!lut || lut.cw !== cv.width || lut.ch !== cv.height) buildLut();
    const g = data.grid, plane = g.ny * Math.ceil(g.nx / 8);
    const { cw, ch, byteIdx, bitMask } = lut;
    if (!rasterCv || rasterCv.width !== cw || rasterCv.height !== ch) {
      rasterCv = document.createElement('canvas');
      rasterCv.width = cw; rasterCv.height = ch;
    }
    const rctx = rasterCv.getContext('2d');
    const img = rctx.createImageData(cw, ch);
    const px = new Uint32Array(img.data.buffer);
    const cs = getComputedStyle(document.documentElement);
    // ABGR little-endian packed colour per layer (alpha 215/255, as before)
    const packed = LAYERS.map((L) => {
      const [r, gc, b] = rgbOf(cs.getPropertyValue(`--series-${L.name}`));
      return ((215 << 24) | (b << 16) | (gc << 8) | r) >>> 0;
    });
    const act = LAYERS.map((L, li) => (on[L.key] ? li : -1)).filter((li) => li >= 0);
    const n = cw * ch;
    for (let p = 0; p < n; p++) {
      const bi = byteIdx[p];
      if (bi < 0) continue;
      const m = bitMask[p];
      for (let a = 0; a < act.length; a++) {                  // later layers paint over earlier ones
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

  /* ---------------- render + controls ---------------- */
  let renderSeq = 0;
  function render() {
    if (!mounted || !data) return;
    const seq = ++renderSeq;
    const ts = data.frames[idx];
    $('#raw-slider').value = String(idx);
    $('#raw-slider').setAttribute('aria-valuetext', fmtReadout(ts));
    $('#raw-readout').textContent = fmtReadout(ts);
    const past = data.cutoff && ts > data.cutoff;
    const badge = $('#raw-badge');
    badge.classList.toggle('hidden', !past);   // .sec-badge sets display; class hides it
    badge.textContent = 'IFS analysis (ERA5 lag)';
    getFrame(idx).then((u8) => {
      if (seq !== renderSeq) return;
      const note = $('#raw-note');
      if (u8 === null && ts) {
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
    if (idx >= data.frames.length - 1) { stopPlay(); return; }
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
    if (idx >= data.frames.length - 1) { idx = 0; render(); }   // show frame 0 now, not one tick later
    playing = setInterval(stepPlay, Math.round(1000 / SPEEDS[speedI]));
    const b = $('#raw-play');
    b.textContent = '⏸ pause'; b.setAttribute('aria-label', 'Pause raw frames');
  }

  function writeUrl(push) {
    if (!mounted || !data) return;
    try {
      const p = hashParams();
      p.raw = data.frames[idx];
      const lay = urlLayers();
      if (lay) p.layers = lay; else delete p.layers;
      const u = locationFor(p);
      if (push) history.pushState({ event: state.selectedEvent || null }, '', u);
      else history.replaceState({ event: state.selectedEvent || null }, '', u);
    } catch (e) { /* file:// */ }
  }

  function urlParams() {
    if (!mounted || !data) return {};
    const p = { raw: data.frames[idx] };
    const lay = urlLayers();
    if (lay) p.layers = lay;
    return p;
  }

  function applyUrl(params) {
    if (!mounted || !data) return false;
    let changed = false;
    if (params.raw) {
      const want = data.frames.indexOf(params.raw);
      if (want >= 0 && want !== idx) { idx = want; changed = true; }
    }
    if ('layers' in params) {            // positive list of ON layers
      const keep = new Set((params.layers || '').split(',').filter(Boolean));
      for (const L of LAYERS) { const v = keep.has(L.key); if (on[L.key] !== v) { on[L.key] = v; changed = true; } }
      if (!on.h && !on.f && !on.a) on.h = on.f = on.a = true;
      renderLayerButtons();
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
        paintFrame(frameCache.get(data.frames[idx]) || null);
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
    $('#raw-next').onclick = () => { stopPlay(); if (idx < data.frames.length - 1) { idx++; writeUrl(true); render(); } };
    $('#raw-speed').onchange = (e) => {
      speedI = SPEEDS.indexOf(+e.target.value);
      if (playing) { clearInterval(playing); playing = setInterval(stepPlay, Math.round(1000 / SPEEDS[speedI])); }
    };
    slider.addEventListener('input', () => { stopPlay(); idx = +slider.value; writeUrl(false); render(); });
    // theme flip: the canvas bakes ocean/land/series colours at paint time, so repaint
    new MutationObserver(() => { if (data && fit) paintFrame(frameCache.get(data.frames[idx]) || null); })
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
    try { await _build(json); } catch (err) {
      console.warn('raw exceedance map failed:', err.message);
      fail('Raw exceedance data could not be rendered for this run.');
    }
  }

  async function _build(json) {
    data = json;
    idx = Math.max(0, data.frames.length - 1);
    const slider = $('#raw-slider');
    slider.max = String(data.frames.length - 1);
    slider.value = String(idx);
    land = null;
    try {
      const lr = await fetch('geo/land-110m.geojson');
      if (lr.ok) land = await lr.json();
    } catch (e) { /* ocean-only still honest */ }
    graticule = d3.geoGraticule().step([10, 10])();
    if (!land) land = { type: 'FeatureCollection', features: [] };
    mounted = true;
    // URL restore (deep link) before first paint
    const boot = hashParams();
    if (boot.raw) { const w = data.frames.indexOf(boot.raw); if (w >= 0) idx = w; }
    if (boot.layers) applyUrl({ layers: boot.layers }); else renderLayerButtons();
    fitProjection();
    $('#raw-skel').hidden = true;
    $('#raw-canvas').hidden = false;
    render();
    if ('ResizeObserver' in window) {
      let raf = 0;
      new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { if (mounted) { fitProjection(); render(); } });
      }).observe($('#raw-map-wrap'));
    }
  }

  window.RawDash = { mount, urlParams, applyUrl,
    _dbg: () => ({ idx, frames: data && data.frames, on, mounted }) };
})();
