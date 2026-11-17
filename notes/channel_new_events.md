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

## [tc] 2026-11-17 12:20 UTC — owner report: live site says "0 tropical cyclones in the last 90 days" — diagnosis + honest-zero fix (NOT promoted)

**Verdict: the 0 is not a true zero — it is a data-vs-deploy ordering artifact, and the
UI had no way to say so.** The served data genuinely contains no TC events, the
frontend counts correctly, and IBTrACS HAS storms (true count 43). Detail:

**(1) What is deployed on the box (`/opt/ewb-live`).** Read-only audit over ssh:
box `ewb_live/events/tc.py`, `ewb_live/sources/ibtracs.py`, `dashboard/app.js`,
`dashboard/trackviz.js` are **md5-identical to this branch** (synced 04:32Z 2026-11-17,
the weekly-idle sync from the DONE entry). `types.py` registers `tropical_cyclone`;
`pipeline.py` carries the unscored-track block. So the live TREE has the lane.
But the served **data** predates it:
- `data/results/events_selected.json` (Nov 17 03:02, 30 events) → 15 heat / 11 AR / 4 freeze, **0 TC** (also confirmed via https://ewb.espr.ai/data/results/events_selected.json).
- `data/results/events.json` (raw catalogue, Nov 17 03:01, 217 events) → **0 TC** too.
- `data/results/meta.json` `event_counts` has **no `tropical_cyclone` key at all** →
  written by pre-lane code (my build loops all four types; the key can never be absent
  when the lane exists). `logs/weekly-2026-11-16.log` line 311 confirms the 16th's
  07:00 scan: `raw {'heat_wave': 149, 'freeze': 22, 'atmospheric_river': 98}` —
  three types, no TC key → the weekly that built this window also ran pre-lane code.
- The last writer was `ewb-live-pipeline.service` (journal: started ~02:50Z, finished
  03:08:59Z, "blocker set unchanged … exiting 0"), run from the tree AS IT WAS AT
  03:00Z — my TC code only landed at 04:32Z. Frontend + data were out of order by
  92 minutes: the UI has the lane, the release does not, so the tab prints 0.

**(2) Frontend counting logic.** The number the owner saw is the per-type tab badge,
`renderControls()` in `app.js`: `state.events.filter(e => e.event_type === t).length`
where `state.events` = `events_selected.json` (`loadData`, with `events.json` only as
fallback). `#gm-count` (mergedmap.js `updateCount`) counts the merged timeline, not
per type. No date window is computed in the browser — the 90-day window is the
pipeline's published window (`meta.window` 2026-08-19..2026-11-17). The count is NOT
filtered by selection/publish gate or by pair_status: `pair_status.json` "unsupported"
reasons render as n/a rows in the scorecard only; nothing drops events from the list
or the badge. Nothing in the frontend dropped a TC event — there was nothing to drop.
Backend gates re-checked on real data (below): none of curation/floor/quota/
validate-publish-gate/qualification drops the lane.

**(3) IBTrACS freshness — the 0 is a real defect, not an honest zero.** Live cache
`data/cache/ibtracs/fixes_ibtracs.ALL.list.v04r01.parquet` (refreshed 04:12Z): latest
fix **2026-11-12 12:00Z ≈ 5 days behind real time** — normal IBTrACS publication lag,
not staleness (24 h refresh policy, mtime healthy). The trailing 90 days contain
47 storms / 43 qualifying events (≥34 kt ×3 fixes ×12 h rule) — matches the DONE-entry
sanity run; last named storms PENA (ended 11-12), CEMPAKA (11-11). **True count ≈ 43,
so 0 is wrong either way.** Re-ran the lane end-to-end ON THE BOX (scratch copy,
scratch caches; live files untouched): 43 events, publish gate rejects 0, quota 60
keeps all, `qualified_at` fallback set — no backend gate drops the lane.

**Bonus defect found while probing (fixed):** `detect_events` clamps every lane to
the ERA5 truth cutoff (ARCO lag ~6 d) — correct for the raster lanes, wrong for a
track lane that never reads ERA5: every week it silently erased the last ~6 days of
the "90-day" TC window, exactly where the newest cyclone lives. PENA's tail (11-11/12)
was outside the clamped scan (11-10 cutoff, probe run 1: window ..11-10).

**Fixes on this branch — commit `429c8ab`, live box untouched, nothing promoted:**
1. `ewb_live/sources/ibtracs.py::source_status()` — reads the cache parquet's time
   column ONLY (never downloads, fail-soft to None) → `{latest_fix, lag_days,
   cache_age_h, note}`.
2. `ewb_live/pipeline.py` publishes it as **`meta.ibtracs`** every run (`_ibtracs_meta`,
   fail-soft). Three honest states: dict = archive reach + lag; null = lane ran but the
   archive was unreachable; **key absent = release built by pre-lane code** (today's
   live case) — the UI can now tell "no cyclones" from "no data".
3. `ewb_live/events/__init__.py`: track lanes skip the truth-cutoff clamp (raster
   lanes unchanged; a fully-past-cutoff raster window still returns empty).
4. `dashboard/app.js`: when the TC lane shows zero, the empty-lane line and the tab
   badge tooltip carry the reason (3 states above; with `latest_fix`/`lag_days` quoted,
   e.g. "…archive through 2026-11-12 — ~5 days behind today"). `mergedmap.js`
   `layersForTab` no longer turns ALL raw layers on when a track tab is clicked
   (the lane has no raw layer — keep the user's choice).

**Tests**: `tests/test_ibtracs_source.py` +4 (archive reach, no cache, torn cache,
never-downloads); `tests/test_tc_events.py` +4 (clamp skip for track lanes, track lane
survives an all-past-cutoff window, rasters still clamped/empty, meta glue fail-soft);
`tests/e2e/test_tracklane_fe.py` +3 honest-zero FE states against meta variants.
Suites: `pytest tests -q --ignore=tests/e2e` → **346 passed, 2 failed** (the same two
upstream-ar-bounds environmental failures as `main`); `-m e2e` tracklane+trackviz → all
pass; `test_dashboard_fe.py` fails **identically on `main`** (verified by stashing my
diff AND by running the tests in the main worktree — pre-existing, other lanes' area).
`scripts/public_scrub_check.sh .` CLEAN.

**Box verification of the fix** (computation on the LXC from a /root scratch copy with
the live venv, scratch caches; deleted after): TC scan window now runs to the full
end date (2026-08-19..2026-11-17, no clamp), 43 events, PENA window complete
(11-06 15:00 → 11-12 06:00), `source_status` → latest_fix 2026-11-12T12:00Z, lag 5.0 d.

**Healing + one thing the orchestrator/owner must decide.** The site self-heals at the
next weekly (**Mon 2026-11-23 07:00Z**) IF the box tree still carries this branch at
that moment. It might not: `ops/remote.sh sync` is a whole-tree rsync, the other lanes'
worktrees still have pre-TC `types.py`/`select.py`/`pipeline.py`/`events/__init__.py`/
`app.js`, and whoever syncs last wins on Monday morning — a stale sync would silently
run a lane-less weekly and print… 0 (with the honest note, at least, once `app.js` is
also stale-but-paired — actually worse: a pre-lane app.js shows NO note at all).
**The durable fix is landing `aaTman/ne-tc` to `main` before the 23rd**; that promote is
the owner's/orchestrator's call, deliberately not taken here. If the owner wants the
lane live sooner: run one weekly-window pipeline from a tree that includes the lane
(`ops/events_rebuild.sh` shape) — also a promote-class action, awaiting instruction.
