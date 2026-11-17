# AR debug — "atmospheric rivers are bugged again" (ar-debug, 2026-11-17)

Read-only investigation of the live site (https://ewb.espr.ai) and the ewb LXC
(`/opt/ewb-live`), plus two small code fixes in this worktree. Nothing was
promoted, no live data touched, no code synced to the box.

## TL;DR — three separate live defects, only two are code bugs

| # | Symptom on the live site | Cause | Status |
|---|---|---|---|
| 1 | Home merged map: the AR layer is a global IVT halo painted in AR colours, and real AR cores have no magnitude shading | **Deployment skew**: the box serves a pre-v9 `dashboard/mergedmap.js` (3-plane `.bin`, `.ivt` schema 1) against the v9 lane it published itself (4 planes, `.ivt` schema 2 = the IVT *field*) | not a code bug — needs the main tree deployed + lane left alone |
| 2 | AR event pages: nearly every model tile reads *"unavailable (archive missing for this run)"* (49 of 121 tiles ok) | **Code bug** in `ewb_live/sources/api.py::open_forecast_nearest_init` — the "nearest earlier init" fallback could never run (guard inverted). Fixed + tested | fixed, commit `f8abd17` |
| 3 | The AR list is short and stale (11 ARs, latest ends 2026-10-31) | **Code bug** in `ewb_live/events/seam.py::mask_lat_span` — returned array order, not (south, north); ERA5 latitudes are descending, so every AR-object bbox came back inverted and the publish gate dropped 9 of the 15 curated ARs on the 2026-11-16 weekly. Fixed + tested. (Staleness itself is a fourth, data-side issue — see §4) | fixed, commit `f8abd17` |

Plus a bookkeeping gap: **`4a9c67b01f25` has zero score rows for all 11 models**
(its event page has an empty scorecard), and AR pairs are absent from the pair
ledger entirely (§5).

---

## 1. The home map: AR paint = the whole IVT field (defect 1)

**Evidence (live, 2026-11-17 ~03:50Z).** Frame `2026102000`,
`data/results/raw_exceedance/index.json`:

```
schema 3, layer_rev "v9", cutoff 2026110818, frames 332 (2026081800..2026110818)
layers: h, f, a, b("AR gap-bridge footprint (display only)")
ivt: {"schema": 2, "bins":[250,500,750,1000,1250], "ar_min":400.0, "above_bit":128}
grid: 0.25 deg, ny 721, nx 1440  → plane = 721*180 = 129,780 B
```

* `.bin` payload (inflated) = **519,120 B = 4.0 planes** → the published lane is
  main's v9 layout (heat, freeze, ar, bridged).
* `.ivt` payload = 1,038,240 B = one u8 per cell, and the class byte carries
  **bit 7** on 61,666 cells (IVT ≥ 400) → schema 2: the class plane is the IVT
  **field**, deliberately not masked by the AR paint
  (`ewb_live/viz/exceedance.py::ivt_class_plane`, written by the
  2026-11-10 `arfix_rebuild` deploy).
* The **served** `dashboard/mergedmap.js` is the older reader: 0 hits for
  `ghostPlane` / `above_bit` / `ivtS2` / `schema >= 2`, and its own comment says
  *"binary exceedance bits (3 planes)"*. Its AR branch reads
  `cls = ivt[cell]` and paints the ramp whenever `0 < cls <= 5`, with no AR-bit
  gate.

Computed on that frame with the served reader's own logic:

| quantity | cells |
|---|---|
| in the AR mask (`.bin` plane 2) | 37,064 |
| **painted with the AR ramp although not an AR** (IVT 250–400 → `cls == 1`) | **110,181** |
| AR cells that get a ramp colour (`cls` ≥ 129 because bit 7 is set → `cls > 5`) | **0 of 37,064** |

So every genuine AR cell (all of them ≥ 400 by definition of the detector) falls
through to the flat fallback colour, and ~11 % of the globe is coloured in AR
ramp bin 1. That is exactly "ARs are bugged again": a pale IVT halo everywhere,
flat-green cores, and no magnitude ramp. main's `mergedmap.js` gates the class
on the AR bit (`const cls = (!gh && ivt && ramp[li]) ? (ivtS2 ? (v & 7) : v) : 0`)
and handles the ghost plane — it is correct against the data already on disk.

**Why the skew exists (timeline, box file dates):**

* 2026-11-10 21:52Z — `ops/arfix_rebuild.sh` (bug-catcher lane) deploys LAYER_REV
  v9 / `.ivt` schema 2 / seam-periodic mask and rebuilds the lane
  (`backups/pre-arfix-20261110T215123Z`).
* 2026-11-16 11:14Z — lane index regenerated (v9).
* **2026-11-16 17:26Z — someone rsync'd an older tree onto the box.**
  `dashboard/mergedmap.js`, `ewb_live/viz/exceedance.py`, `ewb_live/viz/build.py`,
  `ewb_live/events/ar.py`, `ewb_live/events/seam.py` all carry that timestamp and
  are **older than main**, i.e. not the reverted main state either:

```
                          box (17:26Z sync)   this worktree (= main e492a52)
ewb_live/viz/exceedance.py  78dc166b93f8  v7    2db5edd25764  v9
ewb_live/viz/build.py       f6c4d5bedf17        48c8337be582
dashboard/mergedmap.js      06c3363cd881        6ce986139cd7
ewb_live/events/ar.py       4c49e7e4ff0b        fd59a9112212
ewb_live/pipeline.py / sources/api.py / dashboard/eventviz.js: identical
```

`/opt/ewb-live` is not a git checkout, so this is invisible from the box; the
only trace is the mtime wall and `backups/pre-restore-fri-20261116T215954Z`.

**Time-critical:** the box code is `LAYER_REV = "v7"`, and the builder wipes the
done-set on a rev mismatch (`if index.get("layer_rev") != LAYER_REV:
index["frames"] = []`). `ewb-live-weekly.timer` next fires **Mon 2026-11-23
07:00Z**. If the tree is not corrected first, that weekly rewrites the whole
lane with the old, non-periodic AR mask and the schema-1 `.ivt` → the seam-cut
ARs and the "IVT values just disappear" outage both come back, and the current
v9 assets are gone.

**Fix (owner/orchestrator action, not mine to take):** deploy this (main-based)
tree to the box, then leave the lane as it is — the map is fixed by the frontend
alone, no rebuild needed. `cd <worktree on main> && ops/remote.sh sync` (it
already wraps rsync in `flock -w 900 /opt/ewb-live/.deploy.lock`). Warning: the
box tree is shared and other lanes rsync it — `precipclim` builds were running
21:36Z→02:51Z on 2026-11-16/17 — so sequence the deploy, and do **not** run an
`exceedance` rebuild until the tree is main's.

## 2. AR event pages: "unavailable (archive missing for this run)" (defect 2, FIXED)

Per-model status of `data/results/events/<id>/viz.json` at the 24 h lead, the 11
served ARs × 11 models:

| model | ok/11 | | model | ok/11 |
|---|---|---|---|---|
| aigfs | 10 | | graphcast | 5 |
| aifs_single | 9 | | pangu_weather | 5 |
| ifs_hres | 9 | | aurora | 5 |
| aurora_gfs | 3 | | graphcast_gfs | 3 |
| pangu_weather_gfs | 3 | | fourcastnet_v2 / _gfs | 0 |

**49 / 121 ok.** Statuses: `unsupported (no specific humidity in this archive)`
×22 (the FourCastNet pair — legitimate, its archive has no `q`, so IVT is not
derivable), `unavailable (archive missing for this run)` ×28,
`unavailable (build failed)` ×7, `unavailable (field not derivable …)` ×3.

Root cause: `viz/build.py::_ar_model_panel` asks for the AR anchor's own 6-hourly
init, which the AIWP archives do not publish, and relies on
`api.open_forecast_nearest_init` to fall back to the nearest **earlier** run.
That fallback could never fire:

```python
for cand in reversed(available_inits(model, init - dt.timedelta(hours=window_h), init)):
    if cand >= init:            # available_inits is INCLUSIVE of init —
        try:                    # the only candidate that can satisfy this is
            return open_forecast(...)   # the init that just 404'd, so every
        except FileNotFoundError:       # earlier one was skipped and the loop
            continue                    # fell through to `raise exc0`
```

Verified on the box (read-only, tiny bbox subset):

```
available_inits('aurora', 2026-08-27T06 .. 2026-08-28T12) = [08-27 12Z, 08-28 00Z, 08-28 12Z]
open_forecast('aurora', 2026-08-28T06, [q,u,v], leads=[24]) → FileNotFoundError
    noaa-oar-mlwp-data/AURO_v100_IFS/2026/0828/AURO_v100_IFS_2026082806_f000_f240_06.nc
open_forecast('aurora', 2026-08-28T00, [q,u,v], leads=[30]) → OK (13 levels)
```

Same for `graphcast`, `graphcast_gfs`, `pangu_weather` at 06Z. AR anchors in the
served set are 00/06/12/18Z — 6 of 11 are 06Z/18Z — which is why **only the AR
view** is crippled: heat/freeze panels open `surface_air_temperature` at 00Z
inits that exist for every model.

Fixed (`ewb_live/sources/api.py`): skip candidates at/after the requested init,
try the earlier ones. Both callers already add the returned `off` to their leads
(`model_daily_pass` → `want = [l + off …]`, `_ar_model_panel` →
`lead + off`), so the valid times are unchanged. Tests:
`tests/test_sources_unit.py::test_nearest_init_*` (3, no network; all fail
before the fix).

**Residual, not fixed (needs an owner call).** The GFS-forced trio publishes
12Z-only on many days (`aurora_gfs` in the probe window: 08-27 12Z, 08-28 12Z).
For a 06Z AR anchor with `window_h=6` the window is [00Z, 06Z] and contains no
init at all, so those three models will still be red at ~half the anchors.
Widening `_ar_model_panel`'s `window_h` to 12–18 h would recover them, but that
serves a forecast up to 18 h older while the panel entry does not record the
offset — I did not want to make that semantic change silently. Proposal: widen
and publish `lead_used`/`offset_h` in the panel entry, then label the tile.

## 3. ARs dropped at the publish gate (defect 3, FIXED)

`logs/weekly-2026-11-16.log` lines 1921–1937 (run 2026-11-16 07:27Z), after
`curation final: {'atmospheric_river': 15, 'heat_wave': 15, 'freeze': 10}`:

```
publish gate REJECTS event c87f22586d94 (atmospheric_river): bbox latitudes invalid: 56.25..41.0
publish gate REJECTS event c87f22586d94 (atmospheric_river): centroid_lat 44.35 outside bbox lat 56.25..41.0 (+2.5 deg tol)
… 2f6ca6ae37dc: 61.0..57.0 … 44575bb316c0: 56.25..42.25 … 7619e04d404f: 60.5..60.25
… 4eef83538cf9 / ddf2b57d98be: centroid_lat outside bbox lat …
publish gate: 9/40 selected events rejected      # all 9 are atmospheric_river
```

9 of 15 curated ARs never published, including the two top-severity events of the
run (11,318,558 and 6,400,136 — the survivor at 4,992,566 is `bbbd78137ddd`).
The AR funnel itself is healthy: `segments 636 … kept 98`.

Cause: `ar.py` publishes the **AR-object extent** as the event bbox (that was the
fix for land-blob boxes cropping the oceanic half of the plume), taking its
latitudes from `seam.mask_lat_span`, which returned `(lats[rows[0]],
lats[rows[-1]])`. Both the ARCO archive and the local mirror run latitude
**descending** (checked on the box, `latitude.values[:3] = [90, 89.75, 89.5]`,
`[-3:] = [-89.5, -89.75, -90]`), so that pair is (north, south). `ar.py` then
builds `(lat_lo - 2.5, lat_hi + 2.5)` → `lat_min > lat_max`, or, for objects
narrower than 2 × the pad, an inverted-through narrow band that the
`centroid_lat`-inside-bbox check catches. One root cause explains every line
above.

Fixed (`ewb_live/events/seam.py`): min/max over the rows that hold cells — order
agnostic and span-true for gapped masks. Test:
`tests/test_seam.py::test_mask_lat_span_is_latitude_axis_order_agnostic`
(fails before the fix; also pins the padded-bbox inequality that the gate checks).

## 4. Why the AR list stops at 2026-10-31 (data-side, not a code bug)

* `meta.json` window is `2026-08-19 … 2026-11-17`, but the raw lane `cutoff` is
  `2026110818` and the local ERA5 mirror ends `2026-11-10T18:00`.
* The latest published AR ends **2026-10-31**; the 2026-11-16 scan did find
  November ARs (`curation MERGE … e217020b504d window 2026-11-02..2026-11-08`),
  so the recent end of the list is partly defect 3 deleting them and partly the
  store lagging the advertised window.
* The live AR set is also **provenance-mismatched**: none of the 11 served ARs
  carries `details.bbox_source`, which `ar.py` on main always writes — the
  published set (`events_selected.json`, 2026-11-17 03:02Z, snapshot
  `events_runs/events_selected_20261117T030203Z.json`) was produced by the older
  `ar.py` that the 17:26Z sync put on the box. The 2026-11-16 07:27Z snapshot
  (`…20261116T072737Z.json`, 6 ARs, `bbox_source` present) shares **zero** ids
  with it. So the AR numbers on the site come from a mixed code state; after the
  deploy they will all change.
* `weekly-2026-11-16.log:558`: `ewb_live.viz step failed (non-fatal): … timed
  out after 5400 seconds` — the AR/asset tail did not finish.
* **Box filesystem: `/` is 492 G, used 467 G, 4.5 G available (100 %)**, with
  `data/cache` at 316 G. Every "build failed" panel and the viz timeout should be
  read against that. Freeing cache before the Nov 23 weekly is an owner call.

## 5. Per-event hole: 4a9c67b01f25

`Atmospheric River 2026-10-07 to 2026-10-11` (SE Australia / NZ, peak IVT 865.3,
the highest-severity survivor): **0 score rows in `scores.json` for all 11
models** (the other 10 ARs have 11/11), listed 11× in
`data/results/blocker.sig` and in `meta.pair_coverage.unattempted_or_failed`.
Its `viz.json` is also one of the four AR docs without `obs.daily_bits` /
`obs.day_times` (the others: `56b282629cf6`, `e86c13f47bc0`, `029719d1e741`) —
those four therefore keep the anchor footprint on every active day of the home
timeline, which is the artefact the per-day masks were added to remove.
Separately, `pair_status.json` covers only 19 event ids (the served heat/freeze
set): **the 121 AR pairs (11 events × 11 models) are absent from the ledger
altogether**, so the AR coverage numbers in `meta.json`
(`expected 330 / scored 300 / documented_zero_rows 19`) cannot be reconciled
with the ledger.

## 6. Verified vs inferred

Verified by direct measurement: the 4-plane/`.ivt`-schema-2 vs 3-plane/schema-1
mismatch and its per-frame cell counts; the box-vs-main md5 skew and the
17:26Z sync time; both code bugs (each reproduced by a failing unit test); the
06Z archive 404 and the working 00Z+6 h lead on the box; the 9 AR publish-gate
rejections and the funnel counts; the AR score-row hole and the pair-ledger
gap; the mirror/ARCO latitude order; the lane cutoff, disk usage and the
Nov 23 timer.

Inferred (strong, not proven): that ENOSPC/`data/cache` pressure contributed to
the `build failed` panels and the 5 400 s viz timeout; that the 17:26Z rsync
came from a lane branched off an older commit (no git metadata on the box);
that the November ARs lost to defect 3 rather than to the screens (the funnel
kept 98 segments, and the rejected ids are exactly the ones with bad boxes).

## 7. Recommended order of operations (owner / orchestrator)

1. Deploy a main-based tree (with `f8abd17`) to `ewb:/opt/ewb-live` under the
   deploy flock, **before** any `exceedance`/weekly build runs on that box — the
   Nov 23 07:00Z weekly would otherwise rewrite the lane at v7.
2. Free disk (`data/cache` 316 G, 4.5 G free) before any rebuild.
3. Refresh the ERA5 mirror/ARCO copy so the lane cutoff can reach the window end
   the site advertises (2026-11-17).
4. Then rebuild in the `ops/arfix_rebuild.sh` order (map → weekly → AR viz →
   region scores), i.e. `ops/events_rebuild.sh` on main: the AR set will be
   re-published with object bboxes that pass the gate, and the AR viz rebuilt so
   the recovered model tiles land (`viz --all --force --types
   atmospheric_river`).
5. Re-run the pair/scoring builder for the AR events, including
   `4a9c67b01f25`, so the ledger covers the AR pairs (currently 121 absent).
6. Decide on the two open judgement calls: `_ar_model_panel` `window_h` 6 →
   12–18 h with `offset_h` published and shown, and how to present
   FourCastNet's legitimately-unsupported IVT (no `q` in its archive).

## 8. This worktree

Rebased onto `main` (`e492a52`) by fast-forward (the branch was 0 ahead / 48
behind; no conflicts, nothing of mine to keep outside main). Commits:

* `f8abd17` AR: fix the two live AR defects that were code bugs (init fallback,
  lat span) — `ewb_live/sources/api.py`, `ewb_live/events/seam.py`,
  `tests/test_sources_unit.py`, `tests/test_seam.py`.

Tests (`.venv` from a sibling worktree; this worktree has none):
`python -m pytest tests -q` → **302 passed, 29 skipped, 2 failed**; both
failures are pre-existing on `main` and environmental
(`test_events_unit.py::test_ar_floors_match_upstream_ar_bounds`,
`::test_upstream_consecutive_filter_and_blobs` —
`FileNotFoundError: EWB data_prep dir not found:
third_party/ExtremeWeatherBench/data_prep`). New tests fail individually
without the two fixes (3 failed / 19 passed on `tests/test_seam.py
tests/test_sources_unit.py` with the fixes stashed).

Evidence copies kept untracked under `tmp/ar-debug/` (box copies of
`mergedmap.js`, `exceedance.py`, `build.py`, `ar.py`, `seam.py`; served
`events_selected.json`, `meta.json`).
