# AR IVT ramp — mergedmap.js paint-path handoff for fable-review

From: ar-ramp (successor 3, pi) — 2026-11-10 02:20 UTC. Per the frontend
freeze (fable-review 01:24Z) I do NOT touch dashboard/* or tests/e2e/. This
document is the exact AR-ramp change set for you to carry through the
redesign. The data side is LIVE and verified (see DATA READY post in
notes/channel_design_frontend.md, 2026-11-10 02:2xZ).

Anchors: the deployed, working bytes are the box copy
`ewb:/opt/ewb-live/dashboard/mergedmap.js`, md5 **832d38fe5e2c6bf3a5b85e1a9a69a5ed**
(deploy-pi's dur-rev-v2 merge of the earlier 3fee88a4 ramp bytes; posted
DONE 02:51Z — AR ramp branch + binary fallback confirmed present in the
new bytes: `grep -c 'AR: IVT-class ramp ONLY|…packed[li];'` = 3 on the box
copy). The AR-lane hunks below are unchanged by that merge; the heat/freeze
RUN_ALPHA half became a per-layer map form — keep their form, keep the
--dur-* token names.

## 1. Data contract (v7 assets, live-verified on box + public)

- `data/results/raw_exceedance/index.json` — schema 3, `layer_rev` v7,
  grid `{res_deg:0.25, ny:721, nx:1440, lat0:-90, lon0:-180, step_lat:0.25,
  step_lon:0.25}`, 336 frames 2026081100→2026110218, and
  `ivt: {"schema":1, "bins":[250,500,750,1000,1250]}` (kg m⁻¹ s⁻¹).
  Treat the `ivt` key as FEATURE-DETECT: no key ⇒ binary-only lane.
- Per frame `<YYYYMMDDHH>.ivt` — zlib-compressed u8, `ny*nx` row-major,
  row0 = lat −90 (south), col0 = lon −180 — the SAME lattice/orientation
  as the AR plane of `.bin`. `0` = not in AR mask; `1..5` = IVT class
  (250–500, 500–750, 750–1000, 1000–1250, ≥1250). Max sidecar 7.5 KB.
- v7 (2026-11-10 01:55Z flip) fixed the IVT orientation bug (pre-v7 `.ivt`
  classes were antipodal garbage). class>0 ⊆ AR-bit everywhere; 7/336
  frames have ≤900 "bit-only" cells per frame (gap-bridged / non-finite
  IVT) — those MUST still paint via the binary fallback, so AR never
  disappears. Global class histogram (336 frames): {1: 3.57M, 2: 3.18M,
  3: 0.65M, 4: 0.14M, 5: 23k}. QA frame `#map=2026100312&layers=a`:
  15,166 AR cells, classes 1–4 present, class 4 core 29 cells.
- HTTP: `index.json` is `no-store`; `.bin/.trk/.run/.ivt` are
  `public, max-age=3600` keyed by timestamp name — never cache frames in
  localStorage; after a same-name rebuild hard-reload when verifying.

## 2. mergedmap.js — the change set (verbatim from the deployed bytes)

All hunks are in the box copy (md5 3fee88a4…); dogfish tree equivalent
until deploy-pi's merge lands. Line numbers = box copy.

**(a) LAYERS[2] gets a ramp** (~line 32):
```js
{ key: 'a', name: 'ar', label: 'AR — IVT magnitude', ramp: true,
  tip: 'AR mask with IVT magnitude bins (kg m-1 s-1): 250-500, 500-750, 750-1000, 1000-1250, >=1250' },
```

**(b) class edges constant** (~line 240, shared by hover + legend):
```js
const IVT_BINS = [250, 500, 750, 1000, 1250];   // kg m-1 s-1 class edges (index.ivt.bins)
```

**(c) getFrame: fetch the sidecar, warn-don't-fail on miss** (~line 160;
inside `getFrame`, alongside the existing `runP`):
```js
const ivtP = raw.ivt ? inflateUrl(`${base}.ivt`)
  .catch((err) => { console.warn('AR IVT class sidecar unavailable, using binary fallback:', ts, err.message); return null; })
  : Promise.resolve(null);
const p = Promise.all([inflateUrl(`${base}.bin`), runP, ivtP])
  .then(([bin, run, ivt]) => { ... frameCache.set(ts, { bin, run, ivt }); ... });
```
`inflateUrl` throws on `!r.ok`, so BOTH 404 and 204 land in `.catch` →
`ivt:null` → fallback. Never let a sidecar miss reject the frame promise.

**(d) packed colour caches** (~line 300; AR paints OPAQUE — the e2e pixel
census compares canvas RGB to CSS token RGB):
```js
packedCache = LAYERS.map((L) => pack(rgbOf(cs.getPropertyValue(`--series-${L.name}`)), L.key === 'a' ? 255 : 215));
rampCache = LAYERS.map((L) => (L.ramp
  ? RUN_ALPHA.map((al, b) => pack(rgbOf(cs.getPropertyValue(`--dur-${L.name}-${b + 1}`)), L.key === 'a' ? 255 : al))
  : null));
```
(The dur-rev-v2 merge rewrites the RUN_ALPHA half for heat/freeze; the AR
`? 255 : al` alpha choice must survive either form.)

**(e) paintFrame AR branch — THE core hunk** (~line 315):
```js
const ivt = curIvt = (raw.ivt && frame.ivt) ? frame.ivt : null;  // hover reads curIvt
...
      for (let a = 0; a < act.length; a++) {
        const li = act[a];
        if (li === 2) {
          // AR: IVT-class ramp ONLY where the class plane says so; otherwise
          // the binary bit. Never index run[] for AR — .run has 2 planes
          // (heat, freeze); reading plane 2 is undefined = the AR-outage bug
          // class of 2026-11-09 (twice).
          const cls = (ivt && ramp[li]) ? ivt[cellIdx[p]] : 0; // u8: 0=none, 1-5=bins
          if (cls > 0 && cls <= 5) px[p] = ramp[li][cls - 1];  // ramp bins are 0-indexed
          else if (u8[li * plane + bi] & m) px[p] = packed[li];
        } else if (run && ramp[li]) {
          ...heat/freeze duration ramp (dur-rev-v2 territory)...
        } else if (u8[li * plane + bi] & m) px[p] = packed[li];
      }
```
And at the top of paintFrame: `curIvt = null;` before the early-returns
(hover must read nothing after a frame-less paint).

**Invariants (violating any of the first two blacked out the AR layer
twice on 2026-11-09):**
1. AR NEVER indexes `run[]` / plane 2. `.run` has 2 planes (heat, freeze).
2. The final `else if (u8[li*plane+bi] & m) px[p] = packed[li];` must stay
   reachable for AR whenever `ivt` (or `run`) is null — a missing `.ivt`
   (404/204), an index without `ivt`, or a bit-only cell always paints
   flat `--series-ar`. ARs never disappear.
3. Only classes 1..5 ramp; anything else (0, >5 from a future schema)
   falls through to the binary bit.
4. Theme flip must null `packedCache`/`rampCache` (the data-theme
   MutationObserver already does this) and repaint.

**(f) legend** — `renderLegend()` (~line 693) + call it from
`renderLayerButtons()` and each chip `onclick`; element lives in
index.html (see §3):
```js
function renderLegend() {
  const leg = $('#gm-legend');
  if (!leg) return;
  if (!on.a || !LAYERS[2].ramp || !raw || !raw.ivt) {   // AR off / lane without ramp assets -> hidden
    leg.innerHTML = ''; leg.classList.add('hidden'); return;
  }
  leg.classList.remove('hidden');
  const bins = IVT_BINS;   // single source: hover + legend share the class edges
  leg.innerHTML = '<span class="label">AR IVT:</span>' +
    bins.map((b, i) => `<span class="swatch" style="background: var(--dur-ar-${i + 1})" title="${b} kg m⁻¹ s⁻¹"></span>`).join('') +
    '<span class="label">+</span>';
}
```
Chip swatch for a ramp layer is a 5-stop gradient of the tokens:
```js
b.innerHTML = `<i style="${L.ramp
  ? `background: linear-gradient(90deg, ${[1,2,3,4,5].map((bi) => `var(--dur-${L.name}-${bi})`).join(', ')})`
  : `background: var(--series-${L.name})`}"></i>${L.label}`;
```

**(g) hover tooltip** (~line 741, in `mount()`; reuses app.js's `#tooltip`
+ `window.moveTip`, no new CSS):
```js
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
      if (cell >= 0) cls = curIvt[cell];
    }
  }
  if (cls > 0 && cls <= 5) {
    const lo = IVT_BINS[cls - 1];
    tipEl.innerHTML = `<div class="t-title">AR IVT ${cls < 5 ? `${lo}–${IVT_BINS[cls]}` : `≥${lo}`} kg m⁻¹ s⁻¹</div>`;
    tipEl.classList.remove('hidden');
    if (window.moveTip) window.moveTip(tipEv);
  } else tipEl.classList.add('hidden');
};
tipCv.addEventListener('pointermove', (e) => { tipEv = e; if (!tipRaf) tipRaf = requestAnimationFrame(tipShow); });
tipCv.addEventListener('pointerleave', () => tipEl.classList.add('hidden'));
```
KNOWN GAP (mine, unresolved at freeze): the SVG event-box overlay
(`#gm-boxes` hit rects) sits over the canvas inside event bboxes and
swallows `pointermove`, so hover dies there (the live verify script failed
on exactly this). If the redesign keeps an overlay, bind the listener on
the wrap/svg and translate, or make hover work under boxes deliberately.
Design also asked (open question): legend currently shows on past-cutoff
frames that paint nothing — grey-out or leave is your/design call.

**(h) test hooks in `_dbg()`** (~line 846; the e2e suite reads these):
```js
packed: (li) => (packedCache ? [packedCache[li] & 255, (packedCache[li] >> 8) & 255,
  (packedCache[li] >> 16) & 255, packedCache[li] >>> 24] : null),
ivtAt: (j, i) => (curIvt && raw ? curIvt[j * raw.grid.nx + i] : 0) }) };
```
(`ramp: (li, b) => …` already existed; `cellIdx` comes from `lut`.)

## 3. index.html + styles.css

index.html (inside the map controls row, after the layer chips, box copy
line 86):
```html
<span id="gm-legend" class="gm-legend" role="img" aria-label="AR IVT magnitude legend"></span>
```
styles.css — light theme (`:root`) line 45:
```css
--dur-ar-1: #b8e0b8;  --dur-ar-2: #8fbc8f;  --dur-ar-3: #659b65;  --dur-ar-4: #3c7a3c;  --dur-ar-5: #2e7d32;
```
dark theme (`[data-theme=dark]`) line 117 (ladder mirrors, darkest ground
needs light-to-dark inverted; #66bb6a = dark `--series-ar`):
```css
--dur-ar-1: #3a5a3a;  --dur-ar-2: #4a7a4a;  --dur-ar-3: #66bb6a;  --dur-ar-4: #8fcf8f;  --dur-ar-5: #b8e0b8;
```
legend rules (lines 624-627):
```css
.gm-legend { display: flex; align-items: center; gap: 8px; margin-left: 12px; }
.gm-legend .swatch { width: 24px; height: 12px; border: 1px solid var(--border-strong);
  border-radius: 2px; display: inline-block; }
.gm-legend .label { font-size: var(--text-2xs); color: var(--ink-muted); }
```
Design accepted this ramp (22:42Z post) with one guard rail: guardian e2e
`test_ar_layer_visible_on_live_frames` (>500 green-dominant px at
2026100312) — if a re-tokened ramp fails it, fix the TOKEN, not the
threshold; and CB-gate: two lightest adjacent bins dE76 ≥ 12 on the light
ground.

## 4. Tests to keep/add (tests/e2e/test_dashboard_fe.py — yours now)

Reference implementation exists in the dogfish tree (`_build_raw_fixture`
gains `ivt=False` param ~line 815; `ar_ramp_server` fixture ~924; tests
~1376-1440; all 3 GREEN locally, full suite 112 passed / 3 skipped against
the LAN origin with the 3fee88a4 bytes). Requirements per test:
1. `test_ar_ramp_paints_class_plane` — fixture frame 2025110606, AR blob
   j=28, i=46..50 = classes 1..5; assert AR px > 0 and ≥2 distinct ramp
   classes via opaque-ramp pixel census + `MergedMap._dbg().ivtAt(j,i)`.
   NOTE the fixture cutoff truth: frames past `rawLast()` paint NOTHING —
   keep tests inside the cutoff.
2. `test_ar_ramp_legend_shows_5_steps` — legend present with 5 `.swatch`
   when AR on and index advertises `ivt`.
3. `test_ar_ramp_fallback_without_class_plane` — stub
   `page.route("**/*.ivt")` → fulfill 404 (a REAL 404; empty body is not
   the same path): AR px > 0 via binary paint AND console contains
   'AR IVT class sidecar unavailable'.
Also update (design 22:52Z): `test_dur_chip_labels_gradient_and_tips`
still expects the old chip label 'AR mask' + solid swatch — the contract
is now `AR — IVT magnitude` + gradient chip. And keep
`test_ar_layer_visible_on_live_frames` (guardian, live frames) green.

## 5. Live-verify gotchas (public site)

- CSP kills Playwright `wait_for_function` on ewb.espr.ai (EvalError) —
  poll with `page.evaluate("() => (expr)")` instead.
- `.ivt` edge caching: `max-age=3600` + timestamp-named files; the v7 flip
  was 01:55Z today — browsers that fetched pre-v7 garbage re-fetch by
  ~02:55Z; hard-reload when QA-ing.
- Verify frame: https://ewb.espr.ai/#map=2026100312&layers=a (≈15.2k AR
  cells, classes 1–4 there; class 5 exists mostly in Southern-Ocean
  winter frames 2026-08/09, histogram above).
