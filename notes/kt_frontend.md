# EWB Live dashboard — frontend KT (ewb/frontend owner)

Written 2026-11-08. Covers the dashboard served at the site root: everything
under `dashboard/`, plus how it consumes `data/results/` produced by
`ewb_live/`. Read together with `SPEC.md` and `notes/design.md` (design tokens
and layout specs). The site is public; never serve infra details (LAN IPs,
ports, paths, usernames, internal tool names) from anything under
`dashboard/` or generated JSON.

## 1. Architecture and file map

Static frontend, no build step, no framework. Plain ES2020 scripts loaded with
`defer`; the server is `ewb_live.serve` (stdlib http.server, translates
`/data/results/*` and dashboard paths).

| File | Role |
|---|---|
| `dashboard/index.html` | Page skeleton: header, controls (tabs/metrics/leads/week picker), merged global map section, scorecard, catalog map + events table, detail overlay, about. Script tags (defer order): vendor/leaflet → vendor/d3 → app.js → eventviz.js → mergedmap.js. |
| `dashboard/app.js` | Boot, data loading (`loadData`), state + URL routing, controls, scorecard, catalog Leaflet map + table, detail overlay orchestration, themes, week archive. Owns the global `state` const and `window.renderMap/renderControls/renderEventTable`. |
| `dashboard/mergedmap.js` | The merged global map (one Equal Earth surface, replaces `globalmap.js` + `rawmap.js`): 6-hourly raw exceedance layers on canvas + event boxes from `qualified_at` → `end` on SVG, timeline strip (gantt) + 6-hourly slider, selection (multi), play/speed, layer chips. IIFE exposing `window.MergedMap = {mount, urlParams, applyUrl, _dbg}`. URL `#map=YYYYMMDDHH&sel=<ids>&layers=h,f,a` (layers omitted when all-on; `sel=` empty when none). Old links (`#raw=`, `#global=&date=`) are redirected once on boot AND on same-document hashchange, then stripped. Past the ERA5 cutoff the canvas honestly empties, a badge shows "raw layers end {cutoff}Z · ERA5 lag", and boxes keep animating (the time axis extends past the last raw frame). Hero parks on the busiest step (most active boxes, ties → latest). Frames: zlib'd layer-major bitpacked `.bin` decoded via `Blob.stream().pipeThrough(DecompressStream('deflate'))`; each lat row drawn as a horizontal strip through the projection half-widths; per-layer ABGR packed colours memoized per theme. Boxes: hit-rect click toggles selection (stopPropagation — overlap-safe), in-box `open` pill (SVG, only when the box is wide enough); keyboard: Enter toggles selection and keeps focus (guardian contract), a second Enter on the selected box opens the detail; antimeridian-safe projection of wrapped bboxes: each box is the EASTWARD arc lon_min→lon_max normalized to [−180,180) — 350E→20E draws ONE minimal rect (−10→20), split at ±180 only where the arc crosses the map seam; the 0–360 and continuous (350,380) producer forms normalize identically. |
| `dashboard/eventviz.js` | Event detail renderer (`window.EventViz.render/teardown`): observed + per-model Leaflet panels, IVT ramp for ARs, daily-bits overlays, lead toolbar, colorbars. |
| `dashboard/styles.css` | Single stylesheet. Design tokens as CSS custom properties; `[data-theme=dark]` overrides. Page shell: `.shell { max-width: var(--shell-max); margin-inline: auto }` with `--shell-max: min(75vw, 1280px)` at ≥1100px, full-width + padding below. |
| `dashboard/vendor/` | leaflet.js (label 1.1.1, actually 1.2-era API), d3-array@3.2.4, d3-geo@3.1.1 (global dashboard only). |
| `dashboard/geo/` | `countries.json` (catalog + detail basemap), `land-110m.geojson` (global map basemap, Natural Earth 110m). |
| `tests/e2e/test_dashboard_fe.py` | Playwright suite (with `test_guardian_fe.py`: 104 e2e tests), `pytest -m e2e` (plain `pytest` excludes it via addopts). Live tests hit `EWB_LIVE_URL` (default box); fixture tests build a local webroot. |

## 2. Data contracts (served under `/data/results/`)

- Boot files: `meta.json`, `events_selected.json` (curated set — shown by
  default; `events.json` is the raw fallback), `scores.json`,
  `scorecard.json`, `pair_status.json`, `weeks.json` (archive list; each week
  mirrored under `history/<week>/`). `loadData` fetches all in parallel and
  falls back to `sample/` dirs; a week whose files are missing must restore
  `state.week` + URL (see ponytail 2026-11-08).
- Event viz: `events/<id>/viz.json`, schema 1 (plain arrays), 2 (b64-packed
  consec/ivt), 3 (adds `obs.daily_bits` = `{b64, ndays, ny, nx}` — day-major,
  row-major, MSB-first bitmaps, stride `ceil(ny*nx/8)`). AR-lane schema-3
  viz.json uses a FLAT `obs.mask` 0/1 string (no `obs.daily`, no window).
  `regionFromViz` (app.js) handles both: `obs.daily` rows, or a flat string
  chunked by `viz.grid.lon.length`; centroid via `maskCentroid` with
  ref-relative lon unwrapping (antimeridian-safe — dateline ARs get
  "Bering Strait", not "Russia"; bbox vote only when no mask exists).
- Global timeline: `global_timeline.json`, schema 1:
  `{schema:1, generated, window:{start,end,days}, events:[{id,type,title,
  start,end,bbox:[S,N,W,E], grid:{ny,nx,lat0,lat1,lon0,lon1,step_deg,
  step_lon,continuous_lon?}, days:[iso...], masks:[b64 per day]}]}`.
  **Entries can be bbox-only** (no `grid`, no `masks`) when an event has no
  viz asset yet — timeline.py emits them by design. Every consumer must
  null-check (`ev.grid &&`, `dayMask` returns null).
- Mask semantics identical to eventviz `daily_bits` decoding.
- Raw exceedance frames: `raw_exceedance/index.json` = `{schema:1, generated,
  grid:{res_deg,ny,nx,lat0,lon0,step_lat,step_lon}, frames:[YYYYMMDDHH asc],
  cutoff, window:{start,end}}`; per frame `raw_exceedance/<YYYYMMDDHH>.bin` =
  zlib of layer-major concatenated bitplanes (heat, freeze, ar), each
  `ny × ceil(nx/8)` bytes, row 0 = lat0, MSB-first. `cutoff` = last ERA5
  frame; ewb lane confirmed (17:55) NO frames are emitted after it — the map
  shows "raw frames end here (ERA5 lag)" past cutoff and the time axis keeps
  extending for boxes only. A missing index or frame is an expected state
  (view shows "not available yet", one console warning; getFrame never caches
  a failed fetch — transient blips must not gray a step out for the session).

## 3. State + routing model

One `const state` in app.js (NOT a window property — separate scripts access
it as a bare lexical global with `typeof state !== 'undefined'` guards).
Hash contract (merged-map revision, Nov 8):
`#event=<id>`, `&lead=<Nh>`, `&tab=`, `&models=`, `#map=YYYYMMDDHH`,
`&sel=<ids>`, `&layers=h,f,a` (positive list, omitted when all-on).
Old keys `#raw=YYYYMMDDHH` and `#global=<id>&date=<iso>` are REDIRECTED once
(boot and same-doc hashchange both strip them, mapping `raw`→`map`,
`global`→`sel`) then never written again.
- `selectEvent`: records `state.listReturn = {y, x, ty}` BEFORE opening,
  seeds `state.detailLead` from the hash BEFORE `syncUrl()` erases it
  (ponytail MUST 2026-11-08 — otherwise a non-first `#lead` deep link silently
  opens leads[0]), pushState.
- Tweaks (lead, models, map frame, selection): `history.replaceState` via
  `syncUrl(false)`; `MergedMap.urlParams()` merges `map`/`sel`/`layers` into
  the hash, `MergedMap.applyUrl()` restores from hashchange/popstate.
- Back/forward: `popstate`/`hashchange` → `syncFromLocation`; `closeDetail`
  restores list scroll + wrap scroll; `renderEventTable`'s mount tail
  re-applies a pending `listReturn` after every async re-render and clears it
  on first user input. The restore target is the CLICK-TIME position.
- `state.lastViewedEvent` keeps the last-viewed catalog box highlighted
  (3px stroke) on return.
- Week archive: `?week=<label>` query (not hash); failed week loads roll back
  state + URL (never show current-week numbers labeled as a failed archived
  week).

## 4. Rendering pipeline

- Boot: `boot()` → `loadData` → renderAll (controls, scorecard, catalog map,
  table) → deep-link open from hash → `MergedMap.mount()` (fire-and-forget,
  every failure path lands in `fail()` with an honest message + one console
  warning — never a stuck skeleton; fetch throw and render throw are both
  caught).
- Global dashboard: d3 `geoEqualEarth` fit with parallel-sampled bounds
  (pseudocylindrical: x compresses with |lat|, so cells/bboxes are drawn as
  4-corner trapezoids, never rectangles). Fit height drives
  `#gm-map-wrap` inline `minHeight` (wrap hugs the map; no fixed CSS height —
  a `clamp()` height left a dead band on phones). ResizeObserver re-fits once
  `mounted` (land/graticule arrive after data). Type chips mutate `state.tab`
  and MUST call `window.renderMap()` explicitly — a module-local
  `renderMap` would shadow app.js's inside the IIFE (ponytail 06:24).
- Merged map painting: canvas basemap+raster (`drawBase` inks sphere/land
  with `--map-line`, graticule `--border-strong` @ .18 — seam threshold in
  the guardian test is 12 on the green channel; the doubled-alpha stripe bug
  signature is ~24); boxes/gantt are SVG. Frame fetches are deduped per ts
  via an in-flight map.
- Detail: `EventViz.render(host, viz, ev, {models, baseline})`; lead buttons
  re-render panels; `EventViz.teardown()` is called in `closeDetail` AND on
  re-render (ponytail: closing the last event must not leave detached Leaflet
  instances with live tile listeners).
- Viz error taxonomy: fetch 404/null → "not built yet"; malformed/`__vizError`
  → "could not be built (failed/incomplete)" — distinct calm messages, one
  console warning, rest of the detail page still renders.
- AR colormap: paper-aligned Blues ramp (8 stops `#dbe9f6→#08306b`), domain
  250–1000 kg m⁻¹ s⁻¹, transparent below 250 (never black), 400·mask threshold
  tick on the colorbar.

## 5. Design system

Tokens in `:root`, dark theme via `[data-theme=dark]`; theme cycles
auto→light→dark (`cycleTheme` repaints catalog boxes + table). No hardcoded
hex in JS — colors come from CSS vars (`--series-heat`, `--series-freeze`,
`--series-ar` `#2e7d32` light / `#66bb6a` dark [contrast 5.13 / 6.40],
`--tier-heat`, `--tier-freeze`). Page shell `--shell-max: min(75vw, 1280px)`
centered at ≥1100px, gutters 24px/16px below (measured: main+header left
edges equal at 1920/1440/1280/1024/820/390, no horizontal overflow).
Owner feedback docs: `notes/review_round1.md`, `notes/design_review.md`,
screenshots under `notes/owner_refs/` (2026-11-07 heatwave reference,
AR detail bugs, more-metrics multiplying — all resolved by A1–A5 + B fixes).

## 6. Tests

`cd <repo> && .venv/bin/python -m pytest -m e2e -q` → 104 tests,
~5 min. Live tests skip cleanly when the box is unreachable. Fixtures build a
local webroot (dashboard/ + snapshot data). Gotchas (all encoded in the
suite):
- `state` is reachable in `page.evaluate` (same main world).
- Playwright clicks re-center the viewport — click rows already at viewport
  center and assert against `state.listReturn.y/ty`, not pre-click numbers.
- `class="hidden"` (display:none) hides detail sections: assert with
  `state="attached"`, never visibility.
- Fixtures must serve `weeks.json` + `global_timeline.json` (boot fetches;
  404s trip `page.errors == []`).
- Transparency probe uses the most-common painted color (tier fill), not the
  first painted pixel (edge hairline).
- Global tests use a synthetic deterministic timeline (`_gd_timeline()`),
  including a bbox-only ghost event (the 06:24 MUST regression), an
  ISO-`qualified_at` event (pins the qualTs ISO branch), and a dateline AR.
- Prime-meridian rendering contracts (Nov 9 seam fix): merged `bboxPaths`
  draws the minimal EASTWARD arc (norm lon_min → +span) — never min/max,
  never the raw 0–360 form (a +360 box wraps the globe). Leaflet detail
  additionally SNAPS the box onto the field grid's world copy (grid
  `lon[0]` anchor ±360 loop): vector layers don't repeat across copies,
  so box/overlay/fitBounds must share ONE copy. `fieldBounds` is
  EAST-ORDERED (`lon[0]`..`lon[last]`+360 if wrapped) — min/max of a
  wrapped lon array is the "stitched AR field" bug itself (spans 330°).
  Fixture gm5 (`test_detail_prime_meridian_field_and_box`) pins it: AR
  viz needs `kind:'ar_ivt'` + a non-empty `models` (loader routes on
  kind; `Object.keys(viz.models)` crashes otherwise). `occFit` seeds its
  lon window at ±Infinity, NEVER ±180 — a grid entirely >180 (continuous
  PM window) can never lower a 180-seeded `w` (shrink-only min) → 211°
  hemisphere fit (the Nov-9 flake). occFit fits the OCCUPIED blob (A4
  owner contract), not the field grid: a small blob in a big grid means
  the view ⊉ overlay BY DESIGN — test blob-in-view + overlay-continuous-
  same-copy, not overlay ⊆ view.
- Merged-map gotchas: slider↔gantt alignment is a two-file contract — CSS
  `margin-inline: 36px 4px` on `#raw-slider` (thumb-centre maths: LAB(44)−8
  left, trailing(12)−8 right) must move in lockstep with renderGantt's
  `W - LAB - 12` trailing in BOTH `x()` and the lane rect; guarded by
  `test_merged_slider_spans_gantt_axis` (2px tolerance). Track width is
  `calc(100% - 40px)` because width:100% + margins overflows the gantt span.
  Readout is row 1 (right-aligned), slider row 2 (`grid-column: 1 / -1`).
  Busy-frame assertions must match the module's own
  compaction of ISO `end` (`"T" in s → s[:10].replace("-","") + s[11:13]` —
  the test `_c()` helper mirrors it exactly); `#raw-badge` copy assert via
  `.lower()`; old-link tests need a real sync signal
  (`#raw-readout:not(:has-text("—"))`) before reading `location.hash` —
  the boot redirect is async; small boxes render no in-box `open` pill
  (width < 96px) — open via `press("Enter")`; "Failed to load resource"
  console noise from deliberately-dropped fixture frames is filtered in
  `page.errors`.
- axe: an SVG in-box affordance must NOT be a `<foreignObject>` `<button>`
  (nested-interactive + aria-hidden-focus inside the `role=button` group) —
  use pure SVG `<g><rect/><text/></g>`; visible text (`open`) must appear in
  the group's accessible name (label-content-name-mismatch).

## 7. Performance / a11y / deployment

- Payloads: timeline ~32 KB gzip, land-110m 138 KB, viz.json per event fetched
  lazily on open (never in the catalog). Global map renders ≤ ~2k cell paths;
  redraw on slider drag is rAF-cheap (SVG attribute writes).
- A11y: slider `role=slider` + `aria-valuetext` ("Oct 29 — 2 events active");
  map events `<g role=button tabindex=0 aria-pressed>`; list rows
  keyboard-operable with `aria-selected`; skeletons are `aria-hidden`; notes
  are `role=status`; contrast checked for both themes.
- Deploy: `bash ops/remote.sh sync` (rsync repo → box, restarts
  `ewb-live-web` systemd unit). NEVER git commit here (orchestrator commits);
  NO EWB compute on this container — everything runs on the LXC via
  `ops/remote.sh`. Verify after deploy: `tests/e2e` suite plus
  `/tmp/gm_verify.py`-style live probes for new sections.
- Ready protocol: deploy → design re-QAs live → then post
  '## [time] frontend: MERGED MAP READY' with desktop-1440 +
mobile-390 screenshots.
  Don't self-sign.
