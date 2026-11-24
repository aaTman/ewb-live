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

## PRIME-MERIDIAN SEAM FIX (Nov 9 03:10 UTC, frontend lane of owner bug)
- mergedmap.js `bboxPaths`: eastward arc normalized to [−180,180), split
  only at the ±180 map seam. Works on wrapped [350,20] AND continuous
  [350,380] bbox forms (design's post-fix timeline convention).
- eventviz.js `box()`: canonicalizes 350E→20E to one continuous rect and
  snaps onto the grid's world copy (Leaflet vectors don't repeat across
  copies). `fieldBounds` east-ordered (the old min/max was the stitched
  330° overlay — the field seam the owner saw).
- Fixture gm5 in `_gd_timeline` (5 events now — merged_renders asserts
  "5 events"/5 bars/3 busy boxes) + schema-3 ar_ivt viz.json (continuous
  lon 350..380). Tests: `test_merged_prime_meridian_box_contiguous`,
  `test_detail_prime_meridian_field_and_box` (read fit after 700 ms).
- AWAITING ewb rebuild: when assets land on the box (LAYER_REV bump),
  run both gates on the 10 live crossing events (design's list in
  channel_design_ewb 02:40Z: ARs 82dc9c1b533c 029719d1e741 4a9c67b01f25
  3935b8c53d9d + 6 more) and report in channel_design_frontend.

STATUS 2026-11-10: sr13 v3 done (label-veto matcher, in repo scripts/review/sr13_flicker.py). Verdict: 4 true curated flickers confirmed; box logs/sr13_v3_stdout.json. Open: owner decision on gap-bridge extension (recommend yes, multi-frame). Event->label id joins invalid across eras — use bbox geometry. Next if owner silent: audit remaining 43 big blinks for curated-window proximity in dashboard event lists.

STATUS2 2026-11-10: §L done — 9/12 curated AR events have interior AR-dark frames (max run 7). Channel posted. Blocked on owner: gap-bridge extension + curation gate.

STATUS3 2026-11-10: §L.1 amendment posted — day-clip retraction (max run 3/18h, not 7/42h); majority class = window-outlives-object (6/9 events, DEAD frames), gap-bridge alone insufficient; curation gate needed too. Id joins confirmed invalid: idx.tracks ledger HIT 2/12.

STATUS4 2026-11-10: §M posted — heat_wave/freeze events clean on own planes; dead-tail defect is AR-only. Lane pin valid. Referee scope COMPLETE; blocked on owner §L.1/§L.2 decision.

STATUS5 2026-11-10: §M.1 — plane order source-confirmed; raw lane public via mergedmap.js => 43 big non-curated blinks user-visible; gap-bridge decision must cover raw-layer tracks too. Still blocked on owner decision.

STATUS6 2026-11-10: §N posted — 43 big blinks diffuse mid-lat, zero tropical, one gap-bridge fix covers all. Handoff TODO closed. Referee queue EMPTY; all sections §K-§N closed; blocked on owner decision only.

STATUS7 2026-11-10: §O gate validated on box (FAIL 4/19 = exact baseline). Referee work fully closed; repo holds all artifacts.
STATUS8 2026-11-10: §P posted — design-lane 25/75 blink split validated (24.9/75.1, full lane); their tmp/blink_class.py proven dtype-broken (one-frame proof, 2026081106). Blink box killed, no stray procs on box. Referee queue EMPTY; sole open item = owner two-mechanism decision (§L.1+§L.2+§M.1+§N packet, gate = sr_gate.py exit 0).
STATUS9 2026-11-10: §P.1 posted — design-lane root-cause report fully verified on pinned lane (269,593 blink px exact; single-step 86.7% vs 77% v5; worst-frame shifts across builds, tag slip noted). Referee work incl. provenance CLOSED. Still blocked solely on owner A/B/C mechanism pick.
STATUS10 2026-11-10: §Q posted — clamp cost simulated (2.2% @ bridge=3, 1 clamp/0 reject); 8002 starts-dead finding. sr16_clamp_sim.py in scripts/review/ + box. Queue empty again; owner A/B/C still the gate on everything.
STATUS11 2026-11-10: §R posted — gate amended (--bridge N, run-based G2, new G3 start-dead; baseline FAIL preserved, verified on box). §L.1/§Q RECOMMENDATION CORRECTED: bridge=3 alone kills interior flicker; clamp needed only for 8002 dead-at-t0 + tail hygiene. Brief updated. Still owner-blocked on A/B/C.
STATUS12 2026-11-10: full amended-gate baseline posted (4/12/1, all FAIL pre-fix, counts match §K/§L.1/§Q exactly). Referee lane fully closed; owner A/B/C decision is the only remaining gate on the fix lane.
STATUS13 2026-11-10: §S posted — centroid-vs->25%-overlap region assignment measured on pinned lane: 0/12 disagreements, centroid scheme costs zero now; sr17 kept as regression tripwire. Referee lane still owner-blocked on A/B/C.
STATUS14 2026-11-10: §S.1 posted — per-frame overlap rule still 0/12 disagree; n<5 muting census: silences all freeze cells + only NA-heat cell (3/10 cells). Referee lane owner-blocked on A/B/C + these two design calls.
STATUS15 2026-11-10: §T posted — landfall-floor era-shift invalidates §K-§S baselines on regeneration; 8002 (sole G3/§Q case) exits via merge, so post-run G3 PASS is not fix-evidence. Acceptance protocol = before/after gate runs with era-diff attribution. Referee lane owner-blocked; sr16/sr17 re-runnable one-liners for the new set.
STATUS16 2026-11-10: sr_gate self-pins events_sha (verified 51f96b150336625a on box, compiles clean). Referee lane owner-blocked on A/B/C; regeneration pins unchanged.
STATUS17 appended 2026-11-10T08:50:06Z: §S.2 — sr_gate gained G4 (sr17 region tripwire, 3-line add), verified on box --fast --bridge 3: G2 PASS/G3 FAIL(8002)/G4 PASS 0, rc=1. Channel + science_review posted. Regen watcher proc_a9b04febc6d6 armed; still blocked on owner A/B/C.
STATUS18 appended 2026-11-10T09:00:55Z: brief acceptance section corrected to amended 4-leg gate (§R/§S.2/§T semantics); no new evidence. Still blocked on owner A/B/C; regen watcher armed.
