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

## [regions-integration] 2026-11-10 10:55 UTC — fable-review SIGN-OFF on the live integration

fable-review resumed (spend limit reset 08:20Z), reviewed the live site against
the deployed bytes (app.js `2e6634e8` / mergedmap `e7ccc814` / styles `ef36aff2`)
at 1440 dark + 390 light, Global and `#region=europe`: **ack on everything
deployed** (dashboard half and the `ewb_live` + `build_region_scores` half), **no
changes requested**, and its layout call is to **keep the Region control beside
the type tabs** — page-wide filter like the tabs, `#sc-scope` echo is the right
pattern. No console errors on its checks. It holds no `dashboard/*` lock; the
lane is open to continue. Recorded from orchestration `msg_796968f97829`
(reply `msg_97aa2a2fcc5e`).

Lane state: live + reviewed + tested (unit 223/28 skipped; live e2e 120/3 skipped/0
failed). Branch `aaTman/regions-integration` (off `aaTman/fable-review` @ `b901e2e`).
Remaining owner calls only: centroid vs >25 % footprint assignment (changes
published numbers), muting scorecard cells with n < 5.

## 2026-11-10 (day 2) — owner's two follow-ups: footprint rule + small-sample cue (deployed)

**1 · Region membership is now footprint overlap, >25 %.** `ewb_live/regions.py`
unpacks each event's detection mask from its viz asset (`data/results/events/
<id>/viz.json`, both `ar_ivt` peak-day masks and `consecutive_days` unions, packed
or text) and assigns an event to a region when **more than 25 % of its cells**
fall inside the region box; events can belong to several regions at once. An event
with no asset falls back to the largest-overlap bbox centroid, and the output says
so: `assignment: {rule, threshold, fallback, events_with_footprint, events_total,
min_sample}`. The build also publishes `events: {id: [region keys]}` — the
authoritative map — and the JS views filter with that map (`EwbRegions.forEvent`)
instead of re-deriving geometry, so the tables, the counts and the list/map filter
can never disagree. `weekly.py` gained step 6c (rebuild regions *after* viz
assets, so a new event's first assignment is already footprint-based).
Live: footprints cover 29/132 events (all 28 scored ones); counts unchanged today
(28 global = 9 tropics + 14 NH + 5 SH; NA 1, EU 6) — this universe's masks sit well
inside their boxes, and a sweep from 5 % to 50 % only ever moved 3-4 memberships,
all at the hemispheres' ±20° seam, never dropping an event. The rule earns its keep
on the thin atmospheric rivers this was raised for. Sample mode has no viz assets,
so it runs on the centroid fallback and the JSON says so.

**2 · Small-sample cue.** A cell whose score rests on fewer than 5 events (read
from the server: `assignment.min_sample`) gets a soft warning-red halo and a corner
pip, and the page says it in words three ways: the lane header ("1 event ·
indicative" / "3 events · small sample"), a note under the verdict strip ("Glowing
cells rest on fewer than 5 events in North America … one event can move a cell by
tens of percent"), and the tooltip ("n events 1", "sample 1 event · below 5").
Never colour-only: shape + words, and it disappears with the region. First pass
used a hard inset ring in `--worse` red; the owner's screenshot showed why that is
wrong — in a 1-event lane every cell qualifies and the grid read as a wall of
worse-than-baseline. It is now a desaturated `--small-n` halo, low alpha, outer
glow only. Live today: 60 of 188 Global cells flagged, all in the freeze lane (1
event); tropics and Europe are clean; North America flags 60/61, which is the truth
about a one-event region.

**Clobber incident — read this before your next `ops/remote.sh run|bg|sync`.**
At 11:14:50Z a `remote.sh run` from a lane whose tree predates the regions work
rsynced that tree over `/opt/ewb-live` and silently reverted my 11:05 deploy
(`remote.sh` syncs first; mtimes came from the source tree, which is how I traced
it). Both lane trees now **carry** the regions code —
`dashboard/{regions.json,app.js,mergedmap.js,index.html,styles.css}`,
`ewb_live/{regions,pipeline,weekly,serve}.py`, `scripts/{build_region_scores,dev_preview}.py`,
`tests/{test_regions.py,e2e/test_dashboard_regions_fe.py}`, `tests/e2e/test_dashboard_fe.py`
(+2 lazy-fetch stubs) — byte-identical to this worktree, left uncommitted for the
orchestrator to commit. So a sync from `dogfish` or `fable-review` now reproduces
the full live state instead of undoing it. Until those files are committed in those
trees, single-file scp only.
Live is re-deployed and md5-verified against this worktree (10 files), service
restarted, region file regenerated (scorecard.json/scores.json md5s unchanged).
Rollback of this round: `/opt/ewb-live/rollback_pre_regions_v2_20261110/` +
`notes/rollback_pre_regions_v2/` (8 files, pre-footprint-rule bytes).

## 2026-11-10 13:10Z — another lane deployed over us (cleanly this time), and fable-review was a reverter

term_720dbed3 shipped the heat-3/freeze-1 paint gate at 13:01:28Z by scp'ing
`dashboard/{app,mergedmap}.js`, `dashboard/index.html` and
`ewb_live/viz/exceedance.py` from the **dogfish** working copy — which has carried
the regions code since 11:40Z, so their edit landed *on top of* mine and both
features are live together (verified: `forEvent` ×3 and `.small-n` ×14 on the
public bytes; live-mode e2e 122/3/0 incl. the 9 region tests). I adopted their
four files byte-for-byte from the box (commit `65dbb8c`) so this branch stays in
parity with live — unit 230/28, fixture e2e 51 passed.

The hazard runs both ways: dogfish matches live, but `fable-review/` still held
pre-gate bytes for those three files plus `viz/exceedance.py`, so one
`remote.sh run|bg|sync` from that lane would have reverted *their* fix exactly as
one from a pre-regions tree reverted mine. `fable-review/` is now synced to the
live bytes; the three trees agree on all 11 shared files. Still open, and stated
to that lane: their `dur.min` frontend contract is running on its `LAYERS[].min`
fallback in production (live `raw_exceedance/index.json` has `dur:{rev:'v2',
seed:'2026072500'}`, no `min`), and `test_raw_exceedance.py` exists only in
`diurnal-cycle-check/tests/`, so neither dogfish nor this branch covers the gate.

My v2 rollback dir now carries a warning README (box + tree, `927dc0e`): restoring
those 11:05Z dashboard files would revert their gate.
