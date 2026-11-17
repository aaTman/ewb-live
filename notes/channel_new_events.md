# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [tc] 2026-11-16 17:40 UTC — TRACK SCHEMA for `details["track"]` (mirror this, mlc)

Owner asked for "similar tracking view as ibtracs" for mid-latitude cyclones, so the
track representation is shared. `Event.details["track"]` is a **list of fixed-shape
rows**, oldest first, one row per track fix:

```json
"track": [
  ["2026-10-09T15:00:00Z", 23.6, -61.4, 45.0, 998.0],
  ["2026-10-09T18:00:00Z", 24.1, -62.2, 55.0, 991.0]
]
```

| slot | type | meaning |
| --- | --- | --- |
| 0 | str  | fix time, ISO-8601 **UTC with a trailing `Z`** (`%Y-%m-%dT%H:%M:%SZ`). The repo's `Event.start/end` are naive ISO; dashboard `parseISO()` appends `Z` to naive strings, so the `Z` is harmless but explicit — always emit it. |
| 1 | float | latitude, deg N |
| 2 | float | longitude, deg E, **normalized to [-180, 180]** (repo wrap convention, SPEC "Antimeridian bbox convention") |
| 3 | float\|null | **intensity in the event type's headline unit** — for `tropical_cyclone` the chosen-agency max sustained wind in **kt**; for `midlat_cyclone` use whatever your intensity metric is (e.g. 10 m wind kt or |MSLP| hPa) and say so in `details["intensity_units"]` |
| 4 | float\|null | pressure, **hPa** (MSLP for TCs; your centre pressure for MLCs). `null` when missing (IBTrACS `" "`/`T` sentinels). |

Rules both lanes follow:

1. **Rows are strictly ascending in time.** No dedup, no gaps filled — emit the raw
   fix cadence (IBTrACS is 3-hourly for most agencies, 6-hourly for some; do not resample).
2. `details["intensity_units"]`: short unit string for the axis/tooltip (`"kt"`, `"hPa"`, …).
3. `details["track_crosses_antimeridian"]`: `true` when any consecutive pair has
   `|Δlon| > 180`. A crossing track is **not** split into segments in the data; consumers
   split the polyline where `|Δlon| > 180` (the dashboard renderer does this).
4. The track is the **full life of the system**, which may be wider than the event window
   (`Event.start`/`end` mark the *qualifying* window). Consumers draw pre/post-qualifying
   fixes faded; `start`/`end` bound the "live" part.
5. Keep the row shape exactly 5 slots and JSON-native (no NaN — use `null`). It goes into
   `events.json`, which is public and parsed by the browser.
6. bbox stays the padded track extent with the `lon_min > lon_max` wrap convention.

tc's event-level extras live in `ewb_live/events/tc.py` (`scan_tc_events`) and the source
module is `ewb_live/sources/ibtracs.py` (`load_tracks(start, end)` returns a fix-level
DataFrame with columns
`sid, season, basin, name, time, lat, lon, wind_kt, pressure_hpa, nature, wind_source`
+ `full_track`/`status` helpers) — reuse it instead of re-downloading IBTrACS.
mlc: if you need 6-hourly ERA5/analysis cyclogenesis instead of IBTrACS, still publish the
same `details["track"]` shape so the one dashboard renderer serves both.

## [tc] 2026-11-16 17:40 UTC — shared-file edits I own (keep merges trivial)

Additive only, one block each: `ewb_live/events/types.py` (EVENT_TYPES),
`ewb_live/events/__init__.py` (scan dispatch + cache tag), `ewb_live/events/validate.py`
(title label + peak range + a TC cross-check block), `ewb_live/events/select.py`
(severity/floor/quota + "tracks do not conflict/merge" guard), `ewb_live/pipeline.py`
(no-metrics-for-TC documented pair_status), `dashboard/{app.js,styles.css,index.html}`
(label/colour/criteria + a track panel), `dashboard/detection.py` (About text stays
temperature-only; no change needed). New modules are mine: `ewb_live/sources/ibtracs.py`,
`ewb_live/events/tc.py`, `tests/test_tc_events.py`, `tests/test_ibtracs_source.py`.

NOTE for the orchestrator: `/opt/ewb-live` on the ewb LXC is the LIVE deploy and all four
of us sync there through `ops/remote.sh`. I check for a running `ewb-live-pipeline`
before every sync (never sync during a live weekly run).

## [tc] 2026-11-17 04:32 UTC — rebased onto local main (e492a52); what moved

Orca restarted, session ended; the branch was still on the old base `8ba00be`. Uncommitted
dashboard WIP was committed first (`b6d0684`), then `git rebase main` — **no conflicts**: the
main-side frontend work (`aaTman/make-nicer`, AR seam/IVT fixes, `ops/events_rebuild.sh`) and my
blocks touch different regions of `dashboard/app.js`/`styles.css`. `main..HEAD` is now 7 commits
(the owner freeze patch `9ba935a` replays as the first one, exactly as COMMON.md asked).

State after the rebase, all re-run:
- `pytest tests -q --ignore=tests/e2e` → **338 passed, 2 failed**: `test_ar_floors_match_upstream_ar_bounds`
  and `test_upstream_consecutive_filter_and_blobs` need `third_party/ExtremeWeatherBench/data_prep`,
  which no worktree here has — **they fail identically on `main`**, environmental, not mine.
- `scripts/public_scrub_check.sh .` → CLEAN (my first pass leaked `notes/channel…` into
  `dashboard/app.js`; the public gate caught it, the comment is reworded. Do not put internal
  note paths in `dashboard/`).
- `-m e2e` on the two tc files → 14 passed (chromium, local fixture, no live box).
- Box: `/opt/ewb-live` = main + this branch, synced 04:32Z with weekly idle; cache at
  `data/cache/ibtracs/fixes_ibtracs.ALL.list.v04r01.parquet` (6.4 MB, 730k fixes / 13.6k storms).
  Disk on the box is at 99 % (6 GB free) — the lane's whole cache is that one small parquet.

Adapted to main's frontend (the pieces that had to change because main moved):
- `--series-tc` violet lives in the existing lightness gap (freeze 34.0 < **TC 39.8** < AR 46.3 <
  heat 48.4, 6.51:1 light / 6.31 dark) so the four lane hues differ by hue *and* L*; the map-fill
  ladder is untouched because the lane has no fill.
- the lane has no score rows, so `TYPE_META[tropical_cyclone].noScores` prints
  "Not scored — N storms … no EWB benchmark for this lane yet" in the scorecard instead of the
  lane vanishing, and in **region** view the count comes from the published storms through
  `regionsOf()` — the data contract's `by_type` is a *scored* split and can only answer 0 here.

## [tc] 2026-11-17 04:32 UTC — DONE: tropical_cyclone from IBTrACS

**Rule** (documented in `ewb_live/events/tc.py`, mirrored in the UI + About page): one IBTrACS
storm (SID) = one event, qualifying at ≥3 fixes of ≥34 kt sustained spanning ≥12 h with
ET/MX fixes excluded; window = first→last storm-force fix (end exclusive, +1 fix step); bbox =
padded qualifying-fix extent (5°) with the repo's `lon_min > lon_max` wrap convention;
`peak_value` = max wind in the window (kt), min pressure + full track in `details`;
details.track follows the shared 5-slot schema at the top of this file. **Not scored** —
the pipeline records a documented `unsupported` pair_status per pair (so pair_coverage and
`full_run_complete` stay decidable), tracks never merge, and they sit outside the
"no two events overlap" rule.

**Files**: `ewb_live/sources/ibtracs.py` (download/parse/cache, USA_-first agency choice
documented, fail-soft to a stale cache), `ewb_live/events/tc.py`, registration in
`types.py`/`__init__.py`/`validate.py`/`select.py`/`qualification.py`/`pipeline.py` (all additive
blocks), `dashboard/trackviz.js` + `app.js`/`styles.css`/`index.html`, `scripts/tc_sanity.py`.
Commits: `9ba935a` (owner freeze patch), `016f234`, `b6d0684`, `37c8f96`, `dd9615d`, `48852b1`, `adfe226`.

**Tests**: `tests/test_ibtracs_source.py` (12) + `tests/test_tc_events.py` (20) locally on
synthetic fix tables; `tests/e2e/test_trackviz_fe.py` (8, renderer: fix-per-point, window/context
marks, binning, theme tokens, antimeridian world copies, nulls, teardown, resize-refit) and
`tests/e2e/test_tracklane_fe.py` (6, the real dashboard against the sample snapshot + one storm:
named/kt row, TrackViz instead of a viz.json fetch, teardown on close, "Not scored" lane,
screenshots `notes/screenshots/fe-tc-track*.png`, `fe-tc-detail.png`).

**Real data on the box** (`ops/remote.sh run python scripts/tc_sanity.py`, 90 days ending
2026-11-17) — **43 events**: Northeast Pacific 17 (median peak 105 kt, max 155), Northwest
Pacific 13 (93/159), North Atlantic 7 (50/105), North Indian 3 (84/119), Southwest Indian 2,
South Pacific 1; categories TS 19 / CAT1 3 / CAT2 3 / CAT3 4 / CAT4 11 / CAT5 3; median duration
4.6 d, max 16.8 d; 41/43 named; 2 cross the 180° meridian; longest track 249 fixes.
`validate_event` rejects 0, `merge_same_type` merges 0 (by design), the floor passes 43/43, the
quota (60) keeps all 43, and a storm inside an area event registers 0 conflicts.

Two real-data findings fixed on the way:
1. **v04r01 ships `BASIN` EMPTY for the whole North Atlantic** (2,306 storms; all northern
   hemisphere inside the Atlantic box). Seven of my 43 events printed "unassigned basin".
   `basin_for()` now labels a blank fix from its position (Atlantic→NA, off-Mexico Pacific→EP,
   anything else stays unknown) and the event takes the **modal** basin of its fixes, so a
   recurving hurricane's European remnant cannot rename the storm.
2. the trailing 90 days holds 43 qualifying storms, so the global 15-per-type cap would have
   dropped most of the season — the lane carries its own quota (60, `EWB_LIVE_TOP_N_TC`).

**For `mlc` / the orchestrator**: the renderer needs nothing shared — `app.js` routes any event
with `details.track` to `TrackViz` (not `EventViz`), so midlat_cyclone gets the map by publishing
the same rows + `details.intensity_units` (e.g. `"kt"` or `"hPa"`), zero further dashboard edits.
If your intensity is not wind, set the ramp's meaning by renaming nothing — the legend prints
`<label> <unit>`, the bin edges are the Saffir-Simpson kt bands, so for MSLP-based intensity tell
me in this file and I will add a second band table (or publish wind in kt as slot 3 and keep
pressure in slot 4, which needs no change at all).

**Open, needs the owner**:
1. **No scorecard for this lane.** EWB has no metric set for best-track events; the lane is
   published for context and rendered, never benchmarked. If the owner wants scores, the decision
   is what "hit" means for a storm track (e.g. distance-to-track vs cone overlap) — that is a new
   EWB case type, not a dashboard change.
2. **Track lanes are exempt from the no-overlap rule and from merging.** A hurricane routinely
   sits inside a heat wave and a tropical AR; applying the rule would delete the most interesting
   rows. Flagged in `select.py::TRACK_TYPES`. Say the word if a lane overlap badge is wanted
   instead (the data is already there).
3. Quota 60 (a busy 90-day window fits); the dashboard list shows them all.
4. The best-track map is the full Equal Earth globe, on purpose (parity with the site's other
   maps) — the design pass may want a zoom-to-track inset; the geometry is all in `TrackViz`.
