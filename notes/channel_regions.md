# Channel: regional breakdowns

Append-only. Entries: `## [role] YYYY-MM-DD HH:MM UTC — title`. Brief: notes/briefs/regional_breakdowns.md

## [regions-data] 2026-11-09 15:30 UTC — DATA CONTRACT (build against this)

**Region definitions:** `dashboard/regions.json` — single source, JS fetches it, Python reads it. Shape `{"regions":[{"key","label","lat":[min,max],"lon":[min,max]}]}`, array order = dropdown order: global, tropics, northern_hemisphere, southern_hemisphere, north_america, europe. Bounds per brief (NH/SH start at ±20°). Edges inclusive. lat/lon are [min,max]; use them directly for map extents (frontend). Don't edit without posting here.

**New served file:** `data/results/scorecard_by_region.json` (served like scorecard.json, sample fallback included; weekly history snapshots will include it automatically, so older weeks 404 → `loadJSON` returns null → show a graceful state, e.g. fall back to Global rows or "no region data for this week"):

```json
{
  "baseline": "ifs_hres",
  "regions": [
    {"key": "global", "label": "Global", "n_events": 20,
     "by_type": {"heat_wave": 8, "freeze": 6, "atmospheric_river": 6}}
  ],
  "by_region": {
    "tropics": {"rows": [ ...scorecard.json row shape... ]},
    "northern_hemisphere": {"rows": [...]},
    "southern_hemisphere": {"rows": [...]},
    "north_america": {"rows": [...]},
    "europe": {"rows": [...]}
  }
}
```

- `regions` is ordered (dropdown list + honest counts). `n_events` = scored events in that region; `by_type` splits it. Empty region → `n_events: 0` and `{"rows": []}` — render "No events in this region", not an empty table.
- `by_region[key].rows` is EXACTLY the `scorecard.json` rows shape — produced by the same `pipeline.build_scorecard` reducer on the region-filtered scores (same bootstrap seeds per cell), so `normScRow` works on it unchanged.
- **`global` is deliberately absent from `by_region`**: use the already-loaded `scorecard.json` for Global (avoids duplicating the biggest blob; the two are identical by construction). One-line special case: `region === "global" ? state.scorecard : byRegion[region].rows`.
- `scorecard.json` itself is unchanged/byte-compatible.

**Assignment rule** (regions-data owns it; documented in `ewb_live/regions.py`): event → every region whose inclusive lat/lon box contains the **centroid of the event bbox**. Centroid lon = midpoint of the eastward arc, so antimeridian-crossing events (stored lon_min > lon_max) land correctly. Centroids with |lat| < 20 are Tropics-only (NH/SH start at ±20°). Multi-membership expected: a centroid in Europe is also Northern Hemisphere; Global holds all events. n_events counts distinct scored event_ids.

**Frontend needs no assignment logic** — the server slices the rows; you only need regions.json for labels/extents.

Python API if you need it: `ewb_live/regions.py` — `load_regions()`, `assign_regions(bbox)`, `build_region_scorecards(events, scores_df)`.


## [regions-data] 2026-11-09 16:12 UTC — DONE

**What changed** (commit `d43b084` on `aaTman/regional-breakdowns`, on top of baseline `93c69c3`):
- `dashboard/regions.json` — region definitions (single source of truth, see DATA CONTRACT entry).
- `ewb_live/regions.py` — `load_regions()`, `assign_regions(bbox)`, `bbox_centroid()`, `build_region_scorecards(events, scores_df)`. Assignment: bbox-centroid into inclusive boxes, dateline-aware eastward-arc centroid, NH/SH from ±20°, multi-membership, Global = all.
- `ewb_live/pipeline.py` — writes `data/results/scorecard_by_region.json` next to `scorecard.json` in `_write_outputs` (both live and publish-only paths). `scorecard.json` unchanged. Weekly `snapshot()` copies all results files, so history weeks pick the new file up automatically.
- `scripts/build_region_scores.py` — dev regen of the file for a results dir from its published `events.json`/`scores.json` (no scoring run needed).
- `data/results/sample/scorecard_by_region.json` — committed so the dropdown has data in sample mode right now (20 events: tropics 1, NH 17, SH 2, NA 5, Europe 5).
- `tests/test_regions.py` — 9 tests: definitions/order/labels pinned, centroid + dateline + ±20° boundary assignment, region rows == `build_scorecard` on the filtered scores (parity with the house reducer), empty regions -> `{"rows": []}` + `n_events: 0`, hemisphere partition of Global.

**Verified**: `uv run pytest -q` = 213 passed / 29 skipped (no failures). End-to-end on the ewb LXC at `EWB_LIVE_REMOTE_DIR=/opt/ewb-live/_branches/regional-breakdowns` (seeded with `cp -a` from live data, live untouched): `python -m ewb_live.pipeline --publish-only` exit 0, real 28-event universe -> tropics 9 + NH 14 + SH 5 = 28, all 5 by_region blocks populated.

**Notes for regions-frontend**:
- Real `scorecard_by_region.json` is ~2.1 MB (~2x scorecard.json). Lazy-load it on first non-Global selection instead of adding to the initial `Promise.all`.
- History weeks pre-dating `d43b084` have no snapshot of the file -> `loadJSON` null; render the graceful state (contract).
- The LXC branch dir keeps the real generated file at `/opt/ewb-live/_branches/regional-breakdowns/data/results/scorecard_by_region.json` if you want real-data numbers; pull with `EWB_LIVE_REMOTE_DIR=/opt/ewb-live/_branches/regional-breakdowns ops/remote.sh pull-results`.

**Open issues**: none blocking. If the owner later wants per-region *event lists* (not just aggregates) the `regions` counts already carry `by_type`; row-level event filtering stays client-side from `events.json` + the same centroid rule (or I can add ids to the contract on request).

## [regions-frontend] 2026-11-09 — WIP progress (commit 3bff4b4)

**Works** (code landed, not yet e2e-green): Region dropdown (`#region-select`, header, hidden until data), scorecard re-render per region via `scRows()` (tables, metric selection, skill charts, honest per-region `n_events`/`by_type` counts, region-aware empty-lane line "No X in <Region> this window"); catalog Leaflet map fits the region extent (maxBounds-clamped lat, refits Global on switch-back); merged Equal-Earth map `MergedMap.setRegion()` refits projection to the region's corner bbox (MultiPoint fit; LUT lon now centred on `ll[0]`); URL persistence `#region=` (push on select, Back/Forward + deep link restore, unknown key dropped); lazy load of `scorecard_by_region.json` per the perf note (background promise, never blocks first paint; deep link waits for it).
**Left**: finish e2e file `tests/e2e/test_dashboard_regions_fe.py` (synthetic fixture must also write `global_timeline.json` + reuse `_build_raw_fixture` from test_dashboard_fe, else merged-map boot logs console errors and breaks the errs==[] assertions), run it green, screenshots per region.
**Blocker**: none. Note for reuse: week format must be `YYYY-Www` (boot regex), catalog map container is `#map` (`window.__catalogMap` hook), type tabs are role=tab buttons in `#type-tabs` (no data-type attrs).

## regions-frontend — DONE (Hermes, 2026-11-09)

Shipped (commits 3bff4b4, 1d4214f):
- **Region dropdown** in the filter bar, OWB-style: tracked `Region` label above,
  native `<select>` below (keyboard-accessible), options from
  scorecard_by_region.json's ordered `regions`; hides itself on weeks predating
  the lane. `scorecard_by_region.json` (~2.1 MB) loads in the background — first
  paint never waits for it; region deep links await it once, then apply.
- **Per-region scorecard** via scRows() (contract: Global == scorecard.json);
  EVENTS kv, type-tab counts, Detected-events list, catalog-map boxes and the
  merged-map boxes/gantt/count all follow the region's **centroid rule — JS port
  of ewb_live/regions.py** (`eventRegions` in app.js, exposed as
  window.EwbRegions; e2e asserts dateline-wrap + subtropical parity).
  Merged-map title reads "<Region> events — last 90 days" (orchestrator review).
- **Maps move to the region extent** (Leaflet fitBounds clamp + Equal-Earth
  corner-fit with LUT lon centering); Back/Forward and #region= URL persistence,
  unknown keys dropped. Empty lanes read "No X in <Region> this window".
- **Tests**: tests/e2e/test_dashboard_regions_fe.py — 8 passed; full
  `pytest tests/` — 222 passed, 28 skipped, 111 deselected (network-marked);
  public-surface scrub gate green. No known failures.
- Screenshots: notes/screenshots/reg-1..7 (fixture data).

Known gaps / follow-ups: live-data verification against the box
(192.168.0.121:8050) pending the regions-data lane publishing
scorecard_by_region.json into the served week dir; local data/results has no
region file (feature stays honestly hidden until the file lands).

## [regions-review] 2026-11-10 02:30 UTC — DONE

Reviewed d43b084..9c2619d against **real** pulled data (28 scored events;
`pull-results` on the branch dir — live untouched, no heavy compute on orca), the
locally served dashboard in 6 regions × desktop/mobile × light/dark (24 screenshots
`notes/screenshots/review-regions-*`, plus an OWB reference shot), and
owb.brightband.com/scorecard. Full write-up: **notes/review_regions.md**.

**Fixed (3 small commits, not pushed, not deployed):**
- `e8cb40d` mergedmap — "1 events" → "1 event" (real North America n=1) and a
  dim-outside-region veil (`--map-dim` token) so full-longitude regions (Tropics/
  NH/SH) no longer render identical to Global (owner screenshot review b/c).
  Verified via canvas pixels + screenshots.
- `01136eb` app — week switch reset the region but left a stale `#region=` in the
  URL; hash now follows state.
- `2aab7ad` serve+e2e — the feature's background fetch of
  `scorecard_by_region.json` 404'd in the **test_dashboard_fe fixtures**, failing
  7 previously-green e2e tests (hidden by `-m "not e2e"` in addopts). Fixtures now
  stub the file; serve.py returns 204 for it like other optional files.

**Not bugs (measured, documented in the review):** the North America Equal-Earth fit
does cover lat 25..60, lon −120..−75 centred (SF x=188…Maine x=730 on the 910px
canvas) — the "west coast cut off" read is the strong zoom, not an off-centre fit.
Leaflet can't vertically crop full-longitude lat bands without losing horizontal
context, so tropics/NH/SH stay near world view there; the new veil carries the
signal on the merged map.

**Counts:** header EVENTS, type tabs, Detected-events list, merged-map count and
`regions[].n_events` agree in all 24 viewport/theme combos; tropics+NH+SH = Global = 28.

**Open (recommendations, owner calls):**
1. Centroid rule: fine for heat waves/freezes, weak for thin ARs — 4/28 scored
   events mismatch a >10 % footprint overlap (knife-edge example: a US-East-Coast AR
   misses the NA box by 0.1°). Suggest overlap-fraction (>25 %) assignment when the
   scored universe grows; changes contract numbers, so left to the owner.
2. Optional UI pass: mute scorecard cells with n < 5 (Europe/SH pass the n≥3 CI gate
   but ~20-25 % of cells have zero-width CIs).
3. Two live-URL e2e failures (heat detail, model panels) run against the stale
   deployment on 192.168.0.121:8050 and fail without this branch too — re-run
   `-m e2e` after the next deploy.

**Tests:** `uv run pytest -q` 222 passed / 28 skipped; `pytest -m e2e tests/e2e/`
106 passed / 3 skipped / 2 failed (item 3 above) — was 7+2 failing before this
review's fixture fix.

## [regions-integration] 2026-11-10 04:50 UTC — INTEGRATED onto the landed redesign + LIVE

Owner asked for regions on the main website. fable-review had just landed its
redesign (live app.js `77791cfb` etc.) and owned `dashboard/*`; its freeze was
lifted 04:25Z. I asked it who ports (orchestration 03:50Z); its session answered
that message with a **provider spend limit** ("resets 8:20am UTC"), so per the
owner's instruction I went solo and left a review package.

- **Base**: `aaTman/fable-review` @ `b901e2e` == the deployed bytes (md5-verified).
  **Branch**: `aaTman/regions-integration` (this worktree); the pre-redesign lane
  stays on `aaTman/regional-breakdowns`.
- **Port**: Region control moved into the redesign's explore filter row beside the
  type tabs (page-wide filter, same pattern as the type filter), scorecard echoes
  it in `#sc-scope` ("in Europe · all regions"). Everything region-scoped goes
  through `scRows()`; JS centroid rule stays a tested port of `regions.py`;
  merged map keeps the corner fit + `--map-dim` veil + region-named count.
  **Dropped**: the Leaflet catalog-map extent code (the redesign retired that map).
- **Deployed**: single-file scp of `dashboard/{app,mergedmap}.js`,
  `dashboard/{index.html,styles.css,regions.json}`, `ewb_live/{regions,pipeline,serve}.py`,
  `scripts/build_region_scores.py`; web service restarted; region JSON generated
  on the box (scorecard/events/scores md5s unchanged). Rollback:
  `notes/rollback_pre_regions/` + `/opt/ewb-live/rollback_pre_regions_20261110/`.
- **Live counts now agree in every surface** (header EVENTS, map count, list):
  Global 28 = tropics 9 + NH 14 + SH 5; NA 1, Europe 6. Archived weeks 204 → the
  control hides. Zero console errors.
- **Tests**: unit 223 passed / 28 skipped; e2e vs the deployed box 120 passed /
  3 skipped / 0 failed (the review's 2 stale-deployment reds are gone); new
  `tests/e2e/test_dashboard_regions_fe.py` (7 tests, e2e-marked).
- **Bug found on live**: `scripts/build_region_scores.py` read only `events.json`;
  4 of 28 scored events live only in `events_selected.json`, so the first file
  said 24 and disagreed with `scorecard.json`. Now unions both + warns;
  regression-tested. `pipeline.py` never had this (it passes the curated set).
- Handoff/review doc: `notes/regions_integration_fable.md`. Still open: centroid
  vs >25 % footprint assignment, muting n < 5 cells.
