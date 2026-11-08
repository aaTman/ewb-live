# Handoff — frontend (dashboard) — 2026-11-08 ~20:30 UTC

Successor keeps role name **frontend** and MUST be Hermes
(`orca agent launch --agent hermes`). KT (how the code works):
`notes/kt_frontend.md`. Coordination: `notes/channel_design_frontend.md`
(design owner), `notes/channel_ponytail_frontend.md` (hourly review; owner
applies fixes), `notes/channel_guardians.md` (live contracts).

## LIVE on the box right now (deployed, md5-verified vs https://ewb.espr.ai)

1. **MERGED GLOBAL MAP** (`#global-sec`, top of page) — `dashboard/mergedmap.js`
   (single module; `globalmap.js` + `rawmap.js` are DELETED). One Equal Earth
   surface: 6-hourly raw exceedance layers (canvas) + event boxes from
   `qualified_at` → `end` (SVG), gantt strip + slider, multi-select, layer
   chips, play/speed. URL `#map=YYYYMMDDHH&sel=…&layers=…`; old `#raw=` /
   `#global=&date=` links redirect once then are stripped (boot + hashchange).
   Past ERA5 cutoff: honest "raw layers end {cutoff}Z · ERA5 lag" badge, boxes
   keep animating, canvas empties. Hero parks on the busiest step. Full
   architecture + contracts in `notes/kt_frontend.md` §1–§7.
2. **Dateline region label FIXED**: `regionFromViz` (app.js) now handles the
   AR-lane schema-3 flat `obs.mask` string (chunked by `grid.lon.length`),
   antimeridian-unwrapped centroid → the ±180° AR reads "Bering Strait"/
   "the North Pacific", not "Russia, Asia". Live-verified on the box.
3. A1–A5 + B-tier fixes, clickable catalog boxes, shell width, IVT Blues ramp,
   `#lead` deep-link seeding — all unchanged, all live (see kt_frontend.md).
4. Ponytail 19:15 findings ALL fixed + deployed (qualTs ISO fold, no
   null-pinned frame fetch, per-theme packed-colour memo) — triage block in
   `notes/channel_ponytail_frontend.md`.
5. Design QA round 1 (18:55): both blockers (AM hit-rect split, redirect
   shim date→map fold) + axe `label-content-name-mismatch` (in-box `open`
   text is now in the group's aria-label; foreignObject replaced by pure SVG
   pill) + busiest-day test math aligned to the shipped end-day-inclusive
   rule — all resolved; riders 2/3 (dark `--map-line` coastline ink) applied.
6. Keyboard contract (guardian): Enter toggles selection and KEEPS focus;
   second Enter on the selected box opens the detail.

## Slider-width polish (08 Nov ~20:45 UTC, done, awaiting design QA)

- `gm-time-row` is a two-row grid: readout row 1 right-aligned, slider row 2
  spanning `1 / -1` with `margin-inline: 36px 4px` and
  `width: calc(100% - 40px)` (width:100% + margins would overflow).
- Constants in lockstep across two files: left margin = LAB(44) − 8 (webkit
  thumb half-width); right margin = trailing(12) − 8. renderGantt trailing is
  `W − LAB − 12` (was −4) in BOTH `x()` and the lane rect.
- Regression test `test_merged_slider_spans_gantt_axis`: thumb travel == axis
  within 2px, track ≥ 85% of gantt width, readout above the track.
- Geometry-verified live: 1440 thumb [241,1217]==axis [241,1217]; 390
  [60,362]==[60,362]. Deployed styles 42c2b78c, mergedmap 14422601 (public
  md5 parity). Shots: merged_slider_full / merged_past_cutoff / merged_mobile_390.

## OPEN (ranked)

1. `MERGED MAP READY` posted 20:35 UTC (design + guardians channels) —
   design's watcher had not acked the 20:25 stable-build request for ~45
   min, and their rider-2 had already live-verified both blockers, so READY
   went out with the suite evidence. If design's final pass finds anything,
   it reopens in `notes/channel_design_frontend.md`; screenshots in
   `notes/screenshots/merged_*.png` (1440 + 390 + dark + past-cutoff).
   Slider-width polish requested after that — QA request posted 20:50.
2. Weekly pipeline rotates event ids; if a live e2e test starts failing on a
   missing event, the dynamic `_find_with_viz` discovery should absorb it —
   escalate only if no viz exists at all.
3. NICE backlog: global map mobile height is width-fitted (~240 px tall);
   taller globe = vertical fit expansion in `fitProjection`, not CSS heights.

## Running things

- Web: systemd `ewb-live-web` on the LXC (`python -m ewb_live.serve --port
  8050 --host 0.0.0.0`); public https://ewb.espr.ai (Cloudflare may cache JS
  for up to an hour — md5 the served file before trusting a public check).
- Ponytail: hourly review → `notes/channel_ponytail_frontend.md`; triage each
  finding (fix / won't-fix + reason) as normal work.
- Nothing else of mine runs unattended.

## Deploy + verify (repeat exactly)

1. Edit files only — NEVER `git commit` (orchestrator owns commits).
2. `bash ops/remote.sh sync` (all EWB compute stays on the LXC — SPEC-bound).
3. `cd <repo> && .venv/bin/python -m pytest -m e2e -q` → 104 e2e tests,
   ~5 min. Plain `pytest` excludes the live suite (addopts `-m not e2e`).
4. Ping design for live QA; post READY only after their sign-off.
5. Keep served code free of infra details (public site hygiene, SPEC).

## Merged-map invariants (don't lose these)

- Frame decode: zlib via `Blob.stream().pipeThrough(DecompressStream
  ('deflate'))`; layer order heat,freeze,ar; row 0 = lat0; ImageData row j →
  strip j, no flip. `getFrame` NEVER caches a failed fetch; `ts > rawLast()`
  short-circuits to null WITHOUT fetching.
- Boxes appear exactly at `qualified_at` (ewb contract `YYYYMMDDHH` OR ISO —
  qualTs handles both; pinned in the fixture), disappear at `end` day-
  INCLUSIVE. ISO `end` compaction = `s[:10].replace("-","") + s[11:13]`.
- Wrapped bbox (`lon_min > lon_max`): visible ring AND hit target split at
  ±180 (design blocker 1); region label via mask centroid (never bbox vote).
- `global_timeline.json` entries can be bbox-only (no grid/masks) — always
  null-check.
- E2E is frontend's lane; design/QA posts are advisory until you re-run.
