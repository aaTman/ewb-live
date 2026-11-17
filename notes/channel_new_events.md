# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [integrator] 2026-11-17 21:12 UTC — kickoff: dev-site precedent found, merge order set

Replacement session (the first integrator stalled with zero commits). Working in
`/home/orca/orca/workspaces/ewb-live/ne-integration` on `aaTman/ne-integration`
off main `e492a52`. Nothing merged yet; this entry is the plan + Step 1 findings.

### Step 1 — what the "dev frontfinder" precedent actually is (found on the box)

* `/opt/ewb-events-dev` and `/opt/ewb-events-live` are two **separate code+data
  trees** of the events lane (frontfinder era). The dev tree is an rsync of a
  worktree with `.venv`, `third_party` and `data` symlinked to production — the
  pattern documented in `notes/temperature_area_rule.md:94-103`. Isolated code,
  shared read-only data.
* The newer, closer precedent is `/opt/ewb-preview-{colors,nav,flood}`: a full
  tree copy with its **own `data/results`**, served by a **throwaway
  `python -m ewb_live.serve --port 80NN`** (`/opt/ewb-preview-flood` symlinks
  `.venv` + `data/{imerg,clim,cache}` to the live ones and keeps its own
  `data/results`; per `ne-flood-detect` channel entry). Ports used so far:
  8061/8062/8063, all stopped now — 8051-8069 are free (verified with `ss -ltnp`:
  only 8050 = live web, 8080 = nginx, 8000 = legacy dashboard).
* **The public entry is currently dark.** `/etc/cloudflared/ewb-live.yml` exists
  but has no `ewb-live.json` credentials and no `cert.pem` anywhere;
  `ewb-live-tunnel.service` is **inactive**, and the nginx front
  (`/etc/nginx/conf.d/ewb-live.conf`) proxies only `ewb.espr.ai → 127.0.0.1:8080 →
  :8050`. So `https://ewb.espr.ai` is not being served by this box right now —
  creating a `dev.` hostname would need the owner's Cloudflare authorisation
  (`cloudflared tunnel login` or an API token) which I must not invent.

**Chosen /dev/ pattern (reuses the preview-tree precedent, zero Cloudflare change):**
separate tree `/opt/ewb-live-dev` with its **own** `data/results` (never a
symlink to live results), `.venv`/`third_party`/heavy read-only stores
(`data/cache/truth`, `data/icechunk`, `data/imerg`, `data/clim`) symlinked from
`/opt/ewb-live` to avoid re-downloading, served by a dedicated systemd unit
`ewb-live-dev-web.service` on **:8051**. Isolation needs no new env var: every
path in this codebase is either `serve.py`'s `REPO_ROOT`-relative or `data/`-
relative to CWD, so a separate tree with a real (non-symlinked) `data/results`
writes only inside `/opt/ewb-live-dev`. Owner reviews at
`http://192.168.0.121:8051/`. Live tree `/opt/ewb-live` and its units
(`ewb-live-web`, `-weekly`, `-pipeline`, `-store`, `-freshness`) are untouched.
I will additionally add an **opt-in** `/dev/` location + dev-hostname ingress to
`ops/setup-public.sh` templates so that when the owner authorises the tunnel the
dev site rides the same public entry — that is a template-only change today, it
touches no running config.
  * Why a port rather than a `/dev/` URL path today: `ewb_live/serve.py`
    (`translate_path`) and the dashboard's asset/fetch paths are all
    root-absolute; a subpath would need `sub_filter` rewriting of JS. A dev
    hostname (or a LAN port) is the honest version of the same thing.

### Step 2 — merge order (from main e492a52, full test suite after each)

1. `aaTman/ne-ar-debug` (AR fixes f8abd17)
2. `aaTman/ne-subsets` (MLWP virtual-store-only + subsets cache removal — **dev
   only, must NOT be promoted to live**, per the update)
3. `aaTman/ne-coldsnap` · 4. `aaTman/ne-tc` · 5. `aaTman/ne-flood-clim`
6. `aaTman/ne-flood-imerg` · 7. `aaTman/ne-mlc-impl` (mlc-research is its
ancestor) · 8. `aaTman/ne-flood-detect` (DONE per the update: 766 raw / 30
published, already contains flood-clim + flood-imerg merges — so 5/6 may dedupe,
which is fine; the merge will show it).

Freeze patch: `<=0C at all four steps` is already committed on
`ne-coldsnap`/`ne-tc` (commits `a2b6991`/`9ba935a`); I'll take the first merge's
version and let the rest dedupe, rather than applying `freeze_le0c.patch`
separately (same content → merge dedupes, per COMMON.md).

### Status / next

* Checking out merges now; committing each merge as a separate commit so a bad
  merge is one revert, not a bisect.
* Box state: `/` at **51% (235 G free)** — disk is not a constraint anymore.
  Noted: `ewb-live-precipclim.service` is in **failed** state on the box
  (someone else's transient unit — I will not touch it, just flag it).
* Then Step 3: build dev data on the LXC only (IBTrACS pull, IMERG 90-day store
  reuse, freeze w/ <=0C, cold_snap, AR lane, mlc driver, flood), Step 4 e2e +
  screenshots, Step 5 report.

— integrator
# Channel: new events

## [ar-debug] 2026-11-17 03:30 UTC — session restarted, branch rebased onto main

Orca restarted mid-session; this lane had **no commits and no uncommitted
work** of its own (branch `aaTman/ne-ar-debug` was exactly at its base
`8ba00be` = `aaTman/events`, 0 ahead / 48 behind `main`). Rebase therefore
reduced to a clean fast-forward to `main` (`e492a52`, Nov 12) — nothing to
stash, no conflicts, no files taken from my side; main's AR seam/IVT fixes,
make-nicer frontend and `ops/events_rebuild.sh` are now the base of this
worktree. Evidence copies of the pre-rebase state were not needed (tree was
clean). Test runner note: this worktree has **no `.venv`**; I ran the suite
with the sibling `../events/.venv/bin/python -m pytest tests -q`.

Resumed the brief (why are ARs "bugged again") from the top; full write-up in
`notes/ar_bug_report.md`.

## [ar-debug] 2026-11-17 04:10 UTC — DONE: 3 live AR defects, 2 fixed in code

**What was wrong (all three are visible on ewb.espr.ai today).**

1. **Home merged map AR layer** — deployment skew, not a code bug. The box
   publishes a v9 lane (`index.json`: `layer_rev v9`, 4 `.bin` planes,
   `ivt.schema 2` = the IVT *field*, `ar_min 400`, `above_bit 128`) but serves
   a pre-v9 `dashboard/mergedmap.js` (its own comment: "3 planes"; no
   `ghostPlane`/`above_bit`/`ivtS2` handling). Measured on live frame
   `2026102000`: **110,181 non-AR cells painted in AR ramp colours** (IVT
   250–400 → class 1) and **0 of 37,064 real AR cells get any ramp shading**
   (bit 7 makes class ≥ 129, which the old reader rejects) → a global IVT halo
   with flat-green cores. Cause: box code rsync'd at **2026-11-16 17:26Z** from
   a tree older than main (`exceedance.py` = `LAYER_REV v7`, `build.py`, `ar.py`,
   `mergedmap.js` all differ from main; `pipeline.py`/`sources/api.py`/
   `eventviz.js` identical). `/opt/ewb-live` is not a git checkout, so this is
   invisible there.
2. **AR event view: 49/121 model tiles ok**, the rest
   `unavailable (archive missing for this run)` — **code bug, fixed**:
   `sources/api.py::open_forecast_nearest_init`'s "nearest earlier init"
   fallback could never run (`if cand >= init:` wrapped the `return`, and
   `available_inits` is inclusive of the init that just 404'd, so every earlier
   candidate was skipped). AR anchors are 06Z/18Z; AIWP archives publish
   00Z/12Z only — verified on the box that
   `AURO_v100_IFS_2026082806_f000_f240_06.nc` 404s while the 00Z run at lead 24+6
   opens fine. Heat/freeze were unaffected (00Z inits, t2m only), which is why
   only ARs looked broken. Both callers already add the returned offset to their
   leads, so valid times are unchanged. FourCastNet × 2 is legitimately
   `unsupported (no specific humidity in this archive)`.
3. **AR list short/stale** — **code bug, fixed**: `seam.py::mask_lat_span`
   returned `(lats[rows[0]], lats[rows[-1]])`, i.e. array order. ERA5 ARCO and
   the mirror run latitude **descending** (90 → −90, checked on the box), so the
   AR-object extent `ar.py` publishes as the event bbox came back inverted →
   `logs/weekly-2026-11-16.log:1921-1937`: **9 of the 15 curated ARs rejected by
   the publish gate** (`bbox latitudes invalid: 56.25..41.0`, `centroid_lat …
   outside bbox`), including the run's two top-severity ARs (11.32 M, 6.40 M).
   Now min/max over occupied rows (order agnostic, span-true for gapped masks).

**Files / commits.** `f8abd17` —
`ewb_live/sources/api.py`, `ewb_live/events/seam.py`,
`tests/test_sources_unit.py` (+3 no-network tests), `tests/test_seam.py` (+1).
Full suite: **302 passed, 29 skipped, 2 failed** — both failures pre-exist on
main and are environmental (`test_events_unit.py::test_ar_floors_match_upstream_ar_bounds`,
`::test_upstream_consecutive_filter_and_blobs` need
`third_party/ExtremeWeatherBench/data_prep`). With the two fixes stashed, the 3
new tests that pin the bugs fail. No live data promoted, no code synced to the
box, box access was read-only plus two tiny archive opens and a metadata probe.

**Needs the owner / orchestrator (I deliberately did not act).**

* **Deploy a main-based tree (with `f8abd17`) to `ewb:/opt/ewb-live` under the
  deploy flock — before any `exceedance`/weekly build runs there.** The box is
  at `LAYER_REV v7` and the builder wipes the frame set on a rev mismatch, so
  the **2026-11-23 07:00Z weekly** would rewrite the whole lane with the old
  non-periodic AR mask and schema-1 `.ivt` — reinstating both the seam-cut ARs
  and the "IVT disappears" outage and destroying the current v9 assets. The map
  itself needs no rebuild: main's `mergedmap.js` already matches the v9 data on
  disk. Sequence around other lanes — the box tree is shared and `precipclim`
  builds were running late on 2026-11-16.
* **Disk: `/` on ewb is 100 % full (467/492 G, 4.5 G free; `data/cache` 316 G).**
  Read every `unavailable (build failed)` tile and the
  `viz step failed … timed out after 5400 seconds` (Nov 16 tail) against this.
  Owner call on pruning before the rebuild.
* **Store lag:** lane `cutoff 2026110818`, mirror ends 2026-11-10T18Z, while
  `meta.json` advertises a window to 2026-11-17 — the AR list cannot move past
  2026-10-31 until the ERA5 copy is refreshed.
* **Per-event hole `4a9c67b01f25`** (AR 2026-10-07→10-11, peak IVT 865): **0
  score rows for all 11 models**, all 11 pairs in `blocker.sig` /
  `meta.pair_coverage.unattempted_or_failed`; its viz (plus `56b282629cf6`,
  `e86c13f47bc0`, `029719d1e741`) lacks `obs.daily_bits`/`day_times`, so those
  four freeze their footprint across the home timeline. Also:
  `pair_status.json` covers only the 19 served heat/freeze events — **all 121 AR
  pairs are missing from the ledger**, so `meta`'s
  `expected 330 / scored 300 / documented_zero 19` cannot be reconciled. Needs a
  scoring/pair rebuild after the deploy.
* **Two judgement calls I did not make in code:** widen
  `viz/build.py::_ar_model_panel` `window_h` 6 → 12–18 h so the 12Z-only
  GFS-forced models (`aurora_gfs`, `graphcast_gfs`, `pangu_weather_gfs`, each ok
  on 3/11 anchors) can serve 06Z anchors — that trades a fresher forecast for
  coverage, and the panel entry should then publish `offset_h` and the UI label
  it; and how to present FourCastNet's unsupported IVT (no `q` in its archive).
* Live AR set is provenance-mixed: the 11 served ARs carry no
  `details.bbox_source`, so they were produced by the older `ar.py`, and they
  share **zero** ids with the 2026-11-16 07:27Z snapshot that has it — expect the
  whole AR list to change on the first post-deploy run. No credentials needed
  from the owner; no secrets touched.
## [drop-subsets] 2026-11-17 18:45 UTC — subsets cache DELETED (231 G); all MLWP eval now virtual-store-only

Owner ruling executed. Full inventory of every MLWP-touching path, the
OK/REMOVED/CHANGED/BUILDER classification, per-model store table, fc_parts
verdict and residual risks: **`notes/mlwp_virtual_store_audit.md`**. Code on
this worktree (`aaTman/ne-subsets`, branched from main); NOT deployed to
`/opt/ewb-live` (integrator `/dev/` + owner promotion decide that).

### Phase 1 — deletion (ewb LXC, done)

Pre-checked: no scoring/weekly/pipeline job running (`ps`, timers: next
weekly Mon 2026-11-23 07:00; the 6-hourly `ewb-refresh.timer` is the
separate `/opt/ewb-dashboard` ERA5 refresh — never touches the subset tree;
web server serves prebuilt JSON only). Deleted ONLY
`/opt/ewb-live/data/cache/subsets` (231 G, all 11 models). `cache/truth`,
`cache/fc_parts`, `data/clim`, `data/icechunk` untouched.

- **df before:** `/dev/loop14 492G 466G used / 5.4G avail — 99%`
- **df after:** `/dev/loop14 492G 235G used / 237G avail — 50%`
- **freed: ~231 GB** (disk back from the cliff edge).

### Phase 2 — the write path is gone; the store really serves scoring

Single choke point `ewb_live.sources.aiwp.open_mlwp_forecast` (via
`sources.open_forecast` / `fc_cache` / `viz`). MLWP reads are lazy Icechunk
refs, bbox+lead+level cut in memory, **nothing materialised**. Removed:
`aiwp._open_direct` (the direct-HDF5 download fallback), the api.py
per-init `ds.zarr` write-through (THE 231 G writer), `cache.py` subset
eviction (now inert shims), `_net.AIWP_BUCKET`, `ops/probe_fcn_subset.py`.
Bounded semaphore: 2 concurrent icechunk sessions/process
(`EWB_LIVE_ICECHUNK_MAX_SESSIONS`, per notes/ewb.md ≥3 hangs). Repeat calls
memoised in-process only (LRU, `EWB_LIVE_MEM_CACHE_MB`, default 512 MB).

Models → store now: 8 MLWP models (graphcast, pangu_weather, fourcastnet_v2,
aurora ± `_gfs`) read the Icechunk virtual store. `ifs_hres`/`aifs_single`
(ECMWF GRIB via CloudFront) and `aigfs` (NOAA GRIB) unchanged — they were
subsets *writers* but read their own sources; scoring still reuses
`fc_parts` for them. **fc_parts kept**: 3496 keys, 100% ECMWF, contents are
`fc_cache`'s own derived fetches, keys independent of the removed tree —
nothing to invalidate (see audit).

### Proof on the ewb LXC (real data, dev tree `/root/ne-subsets-dev`, scratch)

`ops/validate_virtual_store_scoring.py`: 2 events × {graphcast,
pangu_weather, ifs_hres}, `era5_arco` truth, compared row-by-row to the live
pre-change `score_parts` parquet.

| run | models | inits | wall | bench s (graph/pangu/ifs) | rows compared | identical |
|---|---|---|---|---|---|---|
| heat_wave `085497ecbe06` | g,p,ifs | 5 | 55.7 s | 36.0 / 7.2 / 7.8 | 21 | **21/21, max_rel 0.0** |
| AR `56b282629cf6` | g,p,ifs | full-live (~13) | 271.3 s | 39.6 / 47.1 / 52.8 | 360 | **360/360, max_rel 0.0** |

Values **bit-identical** to the pre-change scorecard rows. Subset tree did
**not** reappear (`live_subsets_exists: false`, `dev_subsets_exists: false`).

Timings vs the OLD path (notes/ewb.md): graphcast AR build_forecast used to
run **25+ min** per job (S3 request-rate throttling), pangu AR ~30 s/init
(~15 min/job), ifs_hres 20-35 min/job. Now the whole AR event (all 3 models,
13 inits) scores in **271 s (~90 s/model)**, heat event in **56 s**. Isolated
store reads: graphcast t2 41-lead 18×25° = **1.7 s**, pangu 3-var 4-lead 3-D
= **4.5 s**, memoised re-read = **0.0 s**. The bounded semaphore +
windowed-lead store reads removed the throttling collapse.

### Tests (local, synthetic)

`tests/test_mlwp_store_guard.py` (new, 6 tests): static scans over
`ewb_live/**` fail if the bucket token / `AIWP_BUCKET` / `cache/subsets` /
`_SUBSET_ROOT` reappear outside the two store builders, if `.to_zarr(`/
`.to_netcdf(` appears in `sources/`, or if `open_init(`/`open_model(` is
called outside `stores/`+the choke point; functional probes assert the
choke point serves from the store, writes NOTHING (no `subsets` dir),
store-miss→`FileNotFoundError`, LRU byte-eviction. `test_prune_cache` →
no-op assertions; MLWP cache test rewritten for the memo; network tests
skip MLWP when the store is absent. Non-network suite: **305 passed**
(2 pre-existing `test_events_unit` AR-floor failures unrelated to this
change, confirmed on a clean stash).

### Residual risks / behaviour changes (owner)

- **MLWP read freshness is now load-bearing on the store timer.** Inits
  published after the last store update (daily 05:30 + weekly catch-up)
  raise `FileNotFoundError`; `open_forecast_nearest_init` shifts to an
  earlier init (agreed lead-offset), so freshest inits can score <24 h
  older data transiently. Listing still reports them.
- **Dev laptops / store-absent hosts:** MLWP reads now fail loud (the
  direct fallback is gone BY DESIGN). Listing works without the store.
- LRU is per-process and 512 MB default; spawn-pool scoring workers each
  pay first-read cost (as with a cold subsets cache before).
- Store update itself still only appends virtual refs (no data bytes
  copied) — kept, per the builder carve-out in the ruling.

### ⚠ Owner mitigation if code can't land before Mon 2026-11-23 07:00

Live `/opt/ewb-live` still has the old api.py write-through, so the Nov 23
weekly WILL rebuild `data/cache/subsets` and refill the disk. Land the code
first (4 functional files), one line:

```
rsync -a --exclude __pycache__ \
  <this-worktree>/ewb_live/sources/{api,aiwp,cache,_net}.py \
  root@192.168.0.121:/opt/ewb-live/ewb_live/sources/ && \
  ssh root@192.168.0.121 systemctl restart ewb-live-web
```

Stopgap if you can't sync in time (defers scoring, does not fix it):
`ssh root@192.168.0.121 systemctl mask ewb-live-weekly.timer` (unmask after
sync). Do not just delete the folder again without the code — weekly refills
it.
(coldsnap copy: this worktree had no channel file after the 2026-11-16 owner
revert — the entries below are the reconstructed log of the earlier session,
written from the commits, plus the rebase entry.)

## [coldsnap] 2026-11-16 17:30 UTC — freeze <=0C patch applied (COMMON setup)

`git apply briefs-new-events/freeze_le0c.patch` → commit `freeze: <=0C at all
four steps (from owner patch)` (now `a2b6991`): `FREEZE_MAX_C = 0.0` in
`ewb_live/events/temperature.py`, applied to the daily mask before tracking on
both the frames path (`_mirror_t2m_daily(..., reduce="max")`) and the ARCO path
(`t2m_dmax` alongside `t2m_daily`), plus `recompute_merged_max_consec` and the
SPEC/detection.py/`test_frame_masks.py` bits. Raises if the freeze cap is asked
for with no 6-hourly temperature field — never silently drops the cap.

## [coldsnap] 2026-11-16 18:40 UTC — cold_snap registered as a parameterisation, not a copy

- **Decision (relationship with freeze, per the brief):** option (a) + a
  dedupe at publish time. `cold_snap` is the *same* p15 mask, so a freeze region
  is a subset of the cold-snap mask and every freeze spell would otherwise be
  published and scored twice. Chosen: the **raw catalogue** (`events.json`,
  `top_n=None`) keeps both lanes, each cold snap carrying
  `details.subzero_cell_fraction` / `details.contains_subzero`; the **curated**
  selection drops a cold snap that overlaps a freeze in time **and** space in
  favour of the freeze — the more specific claim — under the owner's existing
  no-overlap rule. Drop is recorded in `curation.json` as
  `reason: "freeze-subsumed"` (prepended to `select.LAST_DECISIONS`).
  Rationale: no information is lost (the freeze cap is derivable, and the
  fraction says how much of the spell froze), the two scoreable lanes stay
  independent, and the published event set never double-counts a spell. Needs an
  owner OK (asked in DONE).
- **Implementation is a thin parameterisation** of `temperature.py`:
  `MAX_C_BY_TYPE = {"freeze": 0.0, "cold_snap": None}` — the scanner, the
  footprint/core tracker, `EVENT_CLIP` (cold_snap shares freeze's p15 clip and
  the `EWB_LIVE_FREEZE_CLIP` override so the two p15 lanes cannot drift), the
  severity (`p15 anomaly × area × days`) and the merge envelope (16 d) are all
  shared. No copy of the scanner.
- **Frames lane needs no third layer:** `frame_masks.MASK_ALIAS =
  {"cold_snap": "freeze"}` — the published p15 bit *is* the cold-snap mask, and
  `qualified_at` reads layer 1 with the 200k km² floor.
- **Scoring:** EWB (1.1.1) has no cold-snap type and `event_type` is a free-form
  string used only to match a case to its `EvaluationObject`, so `"cold_snap"`
  passes through untouched and is scored with freeze's EWB ingredients
  (`era5_freeze_target`, MinMAE/RMSE/DurationMeanError). Same quantities,
  different event set.
- Files: `ewb_live/events/{cold_snap.py (new),temperature.py,__init__.py,
  select.py,qualification.py,frame_masks.py,types.py,validate.py}`,
  `ewb_live/{pipeline.py,scoring/evaluate.py,scoring/truth.py,viz/build.py,
  viz/timeline.py,viz/__main__.py}`, `ops/validate_selection.py`, `SPEC.md`.

## [coldsnap] 2026-11-16 19:30 UTC — dashboard lane

`TYPE_META`/`TYPE_SERIES_VAR`/`TYPE_ORDER`/`TYPE_CRITERIA`/`metricsFor`, the
merged-map lane (`mergedmap.js` TYPES/LANE_ORDER/counts/`layersForTab` →
cold_snap reads the P15 raw layer, no third temperature lane), `eventviz.js`
freeze-like ramp/legend/chip handling, About-dialog copy, and
`--series-cold-snap` teal tokens for both themes (light `#0e6d78` 6.04:1, dark
`#26808f` 3.29 on paper — freeze-family cold blue, distinct hue, L* between
freeze and heat). `tests/test_cold_snap.py`: 316 lines, synthetic lanes only —
cold-snap-only (p15 but warm), freeze+cold_snap, neither, the ARCO-path cap
table, `qualified_at` on the p15 layer, subsumption, severity/merge/scoring
parity with freeze.

## [coldsnap] 2026-11-17 03:40 UTC — REBASE onto main (e492a52) + DONE

Session restarted after the owner's revert; uncommitted dashboard/test WIP was
committed as `cold_snap: dashboard label/colour/criteria, mergedmap lane, About
text, unit tests` (backup of the pre-rebase branch: `backup/pre-rebase-main-coldsnap`),
then `git rebase main` onto `e492a52`. Five commits now sit on main:

    a0c7c9b cold_snap: dashboard label/colour/criteria, mergedmap lane, About text, unit tests
    58098c9 cold_snap: pipeline, scoring and viz lanes
    78d2d6a cold_snap: curation, publish gate and qualified_at
    d0c574d cold_snap: register the event type — p15 rule without the <=0C cap
    a2b6991 freeze: <=0C at all four steps (from owner patch)

Two conflicts, both resolved keeping **main** and re-applying only my additive
lines (nothing outside the slice was touched):

- `ewb_live/viz/build.py` `CELL`: main's `AR_COMPUTE_PAD_*` comment block kept
  verbatim, `cold_snap: 0.25` added to the dict.
- `dashboard/app.js` `TYPE_CRITERIA`: main's (newer, 3-consecutive-day paint)
  wording for `heat_wave`/`freeze` kept; `and at or below 0 °C` inserted into
  the freeze line (my branch carries the freeze cap) and a `cold_snap` entry
  added with main's map-shading wording.
- Auto-merged and re-checked against main: `mergedmap.js`, `index.html`,
  `styles.css`, `eventviz.js` (diff vs main is exactly my cold_snap additions).

**Tests after the rebase:** `.venv/bin/python -m pytest tests -q` → **310
passed, 2 failed, 29 skipped**. Both failures are pre-existing and environmental
(`FileNotFoundError: .../third_party/ExtremeWeatherBench/data_prep` in
`test_ar_floors_match_upstream_ar_bounds`, `test_upstream_consecutive_filter_and_blobs`)
— they fail on the base branch in this worktree too, and both need the EWB clone
(`third_party/` or `EWB_DATA_PREP_DIR`, present on the ewb LXC). `node --check`
clean on all three JS files. Playwright e2e (`-m e2e`) not run: it targets the
*deployed* dashboard at 192.168.0.121:8050, which is main, not this branch.
No real scans run locally (brief rule); everything is synthetic masks.

**For the orchestrator / owner:**

1. `ops/events_rebuild.sh` (main, `391e639`) md5-guards seven files including
   `ewb_live/events/temperature.py` against a hard-coded `WANT` list. Landing
   this slice changes that hash, so the script will `ABORT: code is not what was
   deployed` until `WANT` is recomputed on the LXC after deploy. Left untouched
   (main's version). Rebuild must run from the isolated tree as the script says.
2. `dashboard/globalmap.js` + `dashboard/rawmap.js` (make-nicer) are **not
   referenced by `index.html`** — the live map is still `mergedmap.js`, which is
   what I extended. If the redesign adopts `globalmap.js`, it needs a
   `cold_snap` entry in its `TYPES`/`LANE_ORDER` and `rawmap.js` needs nothing
   (cold snap shares the P15 layer). I deliberately did not edit them.
3. No heavy/scan work done; a real 90-day cold-snap scan + scoring on the LXC is
   the next step and will need `EWB_LIVE_MERGE_CAP_FREEZE_DAYS`-style env
   parity (cold_snap reuses the freeze merge cap deliberately).
4. Owner decision requested (see the 18:40 entry): raw catalogue keeps both
   lanes, curated selection drops freeze-subsumed cold snaps. If you'd rather
   freeze and cold_snap never coexist at all (or rather always coexist), it's a
   one-function change in `ewb_live/events/cold_snap.py`.

No credentials needed; nothing pushed; branch `aaTman/ne-coldsnap`.
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
---

## [flood-imerg] 2026-11-16 17:45 UTC — IMERG interface for the flood trio (sign grid/units now)

I own IMERG/GPM observation ingest (`ewb_live/sources/imerg.py`). Design + product-choice evidence:
`notes/flood_design.md` (Part 1). **`flood-clim`: please append your climatology design to that same
file as `## Part 2 — climatology` instead of creating another `flood_design.md`** (both briefs name
that path — one file, no add/add merge conflict).

**Product**: `GPM_3IMERGDE` = IMERG **Early Run** daily, 0.1°, V07 (only run with <1-day latency;
Late ~14–18 h, Final ~3.5 months → the recent window is not in Final). One granule/day ≈ 26.5 MB,
latency ~4.5 h after the UTC day closes. `product="late"|"final"` also wired.

**Grid** (as carried by the granule; code reads it per-file and asserts 0.1° + global):
1800 lat (−89.95 … 89.95, ascending) × 3600 lon (−180 … 179.95), cell-centred, 0.1°.
⚠️ 0.1° vs ERA5 0.25° — the join/regrid + bias handling belongs to whoever joins the two
(`flood-detect`), with flood-clim's convention winning if it posts one first.

**Day boundary**: UTC calendar day, 00:00:00–23:59:59.999 (the granule's own `S000000-E235959`).

**Units: mm/day.** Producer `precipitation` is a mean mm/hr rate → ×24; the code reads the granule's
`units` attribute and raises on anything unexpected (never guesses). No zero-filling anywhere:
absent granule ⇒ all-NaN day flagged `missing`; `precipitation_cnt < 48` ⇒ pixel flagged `partial`.

```python
from ewb_live.sources.imerg import (
    load_imerg_daily, load_imerg_mask, load_imerg_status, IMERG_GRID_DEG, UNITS,
)
load_imerg_daily(start, end, *, product="early", root=None,
                 screen="none"|"cnt"|"polid", min_cnt=48) -> xr.DataArray  # (time, lat, lon) float32 mm/day, NaN = no data
load_imerg_mask(start, end, *, ...) -> xr.DataArray   # (time, lat, lon) uint8: bit0 data-present, bit1 full sampling (cnt==48), bit2 likely-liquid
load_imerg_status(start, end, *, ...) -> pd.DataFrame # per-day: granule, status(present|partial|missing|no_granule), bytes, global mean/max mm, cnt_min, ingested_at
```
(time axis = `datetime64[ns]` at 00:00 UTC of each UTC day, ascending; NaN means "no observation",
never "no rain" — mask with `load_imerg_mask` or `screen="cnt"` before thresholding.)

**On-disk** (ewb LXC `/opt/ewb-live/data/imerg/`, override `EWB_LIVE_IMERG_DIR`; `data/imerg` relative
default for portability):
`daily/imerg_<YYYYMMDD>.nc` (per-day compressed NetCDF4: `precipitation` mm/day f4,
`precipitation_cnt` u1 0–48, `probabilityLiquidPrecipitation` u1) + `status.json` + `raw/…` (last
`--keep-raw` days, default 3). Rolling window: `python -m ewb_live.sources.imerg roll --keep-days 90`.

**Fetch/update CLI** (ewb LXC only): `ops/remote.sh run python -m ewb_live.sources.imerg update --days 90`
(idempotent, resumable, `.part`+rename, CMR granule discovery is anonymous, polite: 2 workers + backoff).

**Blocker (owner)**: no Earthdata credentials on the ewb LXC (`~/.netrc` absent, no env) — the real
fetch cannot run until one free NASA Earthdata account is registered in `/root/.netrc` on ewb.
Steps in my DONE entry below. Design/ingest/aggregation/masks are built and unit-tested on synthetic
granules meanwhile.

## [flood-clim] 2026-11-16 18:55 UTC — ERA5 precipitation climatology: CONTRACT (code against this)

Worktree `ne-flood-clim`. I own the CLIMATOLOGY half of `flood` (ERA5 thresholds).
`flood-imerg` owns the 90-day observations; `flood-detect` joins them.

### 1. What I am building

`ewb_live/climatology/precip_clim.py` → zarr store **`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr`**
(built on the ewb LXC; env override `EWB_LIVE_PRECIP_CLIM`).

```
variable  total_precipitation  (quantile, accumulation, dayofyear, latitude, longitude) float32  [mm]
variable  wet_sample_count     (accumulation, dayofyear, latitude, longitude)          uint8     [n, saturates at 255]
coords    quantile    = [0.95, 0.99]
          accumulation= [1, 3]          # 1 = daily total, 3 = trailing 72 h accumulation
          dayofyear   = 1..366          # standard calendar, same convention as owb_clim
          latitude    = 90 → -90 descending, 0.25°, 721 pts
          longitude   = 0 → 359.75 ascending, 0.25°, 1440 pts
marker    .done                          # present only when the build is complete
```

**Grid = ERA5/ARCO 0.25° — byte-for-byte the same lat/lon arrays as the live temperature
climatology** `/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr` (verified: lat 90…-90
descending, lon 0…359.75). So flood masks and heat/freeze masks are cell-aligned and the
dashboard/DAYEXT grid needs no new regridding.

Method: pooled flat ±15-day day-of-year window over 1990-2019 (30 yr), **wet-day
conditioned** — samples with accumulation < 1 mm are excluded (ETCCDI RX1day convention);
threshold is NaN where the pooled wet sample count < 30 (hyper-arid cells: no flood is
definable there). Details/justification: `notes/flood_design.md`.

### 2. Function signature (flood-detect: use these)

```python
from ewb_live.climatology.precip_clim import (
    precip_threshold, precip_threshold_series, precip_threshold_on,
    ERA5_GRID, IMERG_GRID, MIN_WET_SAMPLES, DEFAULT_QUANTILES, DEFAULT_ACCUMS,
)

# one date -> (latitude, longitude) threshold field in mm
precip_threshold(date, quantile=0.95, window=1, *, grid="era5", scale=None) -> xr.DataArray

# many dates -> (time, latitude, longitude) in mm   [prefer this: one store read]
precip_threshold_series(dates, quantile=0.95, window=1, *, grid="era5", scale=None) -> xr.DataArray

# arbitrary target grid (e.g. IMERG 0.1°) -> (time, lat, lon) in mm
precip_threshold_on(lats, lons, dates, quantile=0.95, window=1, *, method="nearest") -> xr.DataArray
```

* `window` = **accumulation length in days** (1 or 3) — NOT the pooling half-width
  (that is `POOL_HALF_WINDOW_DAYS=15`, fixed at build time).
* `window=3` is the **trailing 72 h ending at 24:00 UTC of `date`**, i.e. the sum of the
  daily totals of `date-2`, `date-1`, `date`. If you compute 3-day sums from your daily
  IMERG series, use that same trailing convention, and index the threshold by the END date.
* `quantile` ∈ {0.95, 0.99}. Any other value raises with the list of what is in the store.
* `grid="era5"` (default) returns the native 0.25° grid. `grid="imerg"` bilinearly
  upsamples onto the published IMERG V07 grid (`IMERG_GRID`, 0.1°, cell-centre,
  ±89.95/±179.95, lon -180..180). **If your store is not exactly that lattice, use
  `precip_threshold_on(your_lats, your_lons, ...)` instead** and tell me so I can fix the
  constant.
* `scale=` optional multiplier (float or DataArray/path) applied to the threshold — this is
  the seam for an IMERG↔ERA5 bias correction. Default 1.0 (uncorrected). See the bias note.

### 3. Source fact that matters for flood-imerg's day boundary

The climatology is built from the public ARCO ERA5 store
`gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr`, variable
**`total_precipitation_24hr` read at 00Z of day D+1 = the whole UTC day D (00:00–24:00)
accumulation**. Verified numerically today against 24 hourly `total_precipitation`
increments of the EWB store (`full_37-1h-0p25deg-chunk-1.zarr-v3`): max abs difference
6e-5 mm over the globe, and `total_precipitation_6hr` = the exact 6-hourly accumulation.
So: **all precipitation quantities here are UTC calendar-day totals, mm of liquid-water
equivalent, all precipitation types (rain + snow mixed water equivalent)** — please make
the IMERG daily accumulation use the same 00:00–24:00 UTC boundary and mm units, and note
IMERG's `Hour`/orbit-day convention if you use the daily product instead of summing the
half-hourly files.

### 4. Recommended detection recipe (my input to flood-detect)

Flag a cell-day only if **both** hold (this is why the floor exists — in the Sahara the
wet-day p95 is a few mm and any drizzle would otherwise "exceed" it):

```
obs >= threshold(date, q, window)   AND   obs >= FLOOR_MM[window]
FLOOR_MM = {1: 20.0, 3: 50.0}       # mm; hydrological plausibility floor, yours to tune
and wet_sample_count >= 30           # NaN threshold already encodes this
```

Bias-awareness (ERA5 reanalysis vs GPM/IMERG satellite): the two conditional distributions
differ (ERA5 over-produces light/drizzle days over land, under-produces short intense
convective maxima; IMERG over land outside the MW-coverage core is IR-inferred). I do NOT
have an IMERG climatology to quantile-map against, so the honest default is the
**two-condition test above** plus, if you want it, a per-cell `scale=` from an IMERG-vs-ERA5
median-ratio estimated on whatever overlapping window you can afford. Do not read the
threshold as "IMERG p95" — it is "ERA5 p95 on the ERA5 grid".

### 5. Status / asks

- Build runs on the ewb LXC only (`ops/remote.sh bg precipclim ...`), ~22 GB read from GCS,
  scratch in `/dev/shm` (RAM, never the nearly-full root disk). I will report build time,
  store size and sanity maps (Amazon / Sahara / monsoon India) in `notes/flood_design.md`.
- **ASK (owner/orchestrator): the ewb root disk is at 98 % (13 GB free).** My store needs
  ~3-5 GB. flood-imerg's 90-day IMERG cache needs several more. Nothing gets deleted by me;
  please decide what to prune (`/opt/ewb-live/data/cache/subsets` is 230 GB, the legacy
  `/opt/ewb-owb/data/clim2` raw t2m inputs are 106 GB).
- I do not touch shared files (`types.py`, `pipeline.py`, `select.py`, `dashboard/*`);
  registering the `flood` Event type + the About-dialog text belongs to flood-detect.

---

## [flood-imerg] 2026-11-17 03:10 UTC — postmortem: the runaway IMERG test (140 GB into /tmp) + fix

**Cause (found by reading the code, then confirmed by a bounded reproduction).** Not grid size, not
the fixtures, not a download loop: `tests/test_imerg.py::test_https_route_rejects_a_non_granule_body`
stubbed the GES DISC HTTPS response with

```python
def read(self, n=-1):
    return b"<html>Earthdata Login</html>" if n > 0 else b""
```

`ewb_live/sources/imerg.py::_get_https` copied the body with `shutil.copyfileobj(resp, out, length=1<<20)`,
which loops **until `read()` returns `b""`**. `shutil.copyfileobj` always passes its buffer length
(1 MiB) as `n`, so the stub answered 27 bytes *forever* and the copy appended ~10 MB/s to
`tmp_path/g.nc4.part` on the tmpfs `/tmp` for the 4 h until it was killed — 140 GB, host + Orca hung.
A real socket returns `b""` at EOF, so only the stub could do it; the production code had no ceiling.

**Fixes (both layers).**
1. Stub is now EOF-terminated (a finite list of chunks, then `b""`), and the same for the S3-side stubs.
2. `imerg.py` no longer trusts `read()` to reach EOF: new `_copy_capped()` (used by both transports,
   replacing `shutil.copyfileobj`) and `_read_capped()` (for the CMR/s3credentials JSON, which was
   buffered in RAM unbounded) raise `ImergError` once a body passes `MAX_GRANULE_BYTES` (default 512 MB,
   env `EWB_LIVE_IMERG_MAX_GRANULE_BYTES`; a daily granule is ~26 MB) / `MAX_BODY_BYTES` (64 MB), and the
   oversized `.part` is **deleted** so a later run cannot resume junk (the old code also cached HTML login
   pages as resumable partials — same fix). `test_https_route_caps_a_body_that_never_ends` reproduces the
   incident against a stub that never ends and asserts it stops after the cap and leaves no file behind.

Also shrank the synthetic test grids from 1° (180×360) to 30° (6×12 cells, still global so the coverage
assertion in `_check_grid` still bites): the whole 100-day rolling-window test now costs a few MB instead
of ~150 MB. `_check_grid`'s global-coverage bounds are now one cell of the declared spacing instead of
hardcoded ±89.5/179.0, which is equivalent at 0.1° and makes a tiny global lattice testable.
Test footprint: `/tmp` stays at 29 MB used with TMPDIR on tmpfs; the suite is now run under
`systemd-run --user --scope -p MemoryMax=8G -- timeout 300` with `TMPDIR` on disk.

**Other flood agents:** no interface change (still `load_imerg_daily(start, end) -> (time, lat, lon)`
mm/day + `load_imerg_mask`/`load_imerg_status`). flood-clim's contract above is compatible: UTC
calendar-day totals in mm, and `IMERG_GRID` (0.1° cell-centre, ±89.95 / −180…179.95) is exactly what my
granules carry, so `grid="imerg"` / `precip_threshold_on` will line up.

## [flood-clim] 2026-11-17 03:40 UTC — rebased onto main (e492a52); API unchanged; DISK WARNING on ewb

**Rebase done, clean.** `aaTman/ne-flood-clim` = `main` (e492a52) + 3 commits, 0 behind:
`precip_clim builder` + `NaN-fill threshold template` + `flood design note`.
Diff vs main is 9 files, **all additions** — `ewb_live/climatology/precip_clim.py`,
`tests/test_precip_clim.py`, `notes/channel_new_events.md`, `notes/flood_design.md`,
`tmp/probe_tp*.py` (the ARCO daily-total identity probes). **No shared file touched**
(`types.py`/`pipeline.py`/`select.py`/`dashboard/*` untouched), so this branch cannot
revert anyone's frontend/AR work.

- Dropped from my branch: `abc2c90 freeze: <=0C at all four steps (from owner patch)`.
  It was byte-identical to `aaTman/ne-mlc-research` @ `10fd9d1` (verified with `diff`),
  i.e. it is mlc-research's slice, not mine, and main does not carry it. It is preserved
  on `backup/pre-rebase-main-20261117` here. **Whoever integrates: take the freeze patch
  from `ne-mlc-research`, not from me.**
- Tests re-run after the rebase: `tests/test_precip_clim.py` **17 passed**; full offline
  suite **315 passed, 2 skipped-equivalent, 2 failed** — both failures are
  `tests/test_events_unit.py::test_ar_floors_match_upstream_ar_bounds` and
  `::test_upstream_consecutive_filter_and_blobs`, which **fail identically on main
  e492a52** in this container (`FileNotFoundError: third_party/ExtremeWeatherBench/data_prep`,
  that clone is absent here). Not mine, not caused by the rebase.
- **The §2 API contract above is unchanged by the rebase** — flood-imerg / flood-detect
  do not need to change anything.

**DISK WARNING (needs the owner, not me):** the ewb root FS is now **100 % used, 4.5 GB
free** (was ~11-13 GB when I posted §5). The partial climatology store is 4.5 GB of it:
`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr` (no `.done` marker yet — see
my next entry for whether it needs rebuilding or only finishing). I have deleted nothing.
The single biggest reclaimable thing I can see is `/opt/ewb-live/data/cache` at **330 GB**
(forecast subset cache); `/opt/ewb-owb/data/clim2` is another 106 GB of legacy raw t2m
inputs whose derived product already exists. Say the word and I prune what you name;
otherwise I will leave the store as-is and we are one bad sync away from ENOSPC on a
live box.

---

## [flood-imerg] 2026-11-17 03:45 UTC — DONE: IMERG daily ingest built, tested, sized; one owner blocker

### What changed (this worktree, `ne-flood-imerg`, branched from `8ba00be`)

* `freeze: <=0C at all four steps (from owner patch)` — `briefs-new-events/freeze_le0c.patch` applied
  and committed first, as COMMON.md asks (`ewb_live/events/temperature.py`, `SPEC.md`,
  `dashboard/detection.py`, `tests/test_frame_masks.py`).
* **`ewb_live/sources/imerg.py`** (new, ~1500 lines) — the whole observation lane: CMR granule survey
  (anonymous) → granule download (AWS Open Data S3 with Earthdata-minted temp credentials, GES DISC
  HTTPS fallback) → daily mm/day field + `precipitation_cnt` + `probabilityLiquidPrecipitation` →
  per-day compressed NetCDF4 + `status.json` index → rolling 90-day window. Run policy
  `product="auto"` (Late preferred, Early fills the head, cached days upgrade when Late lands).
  Disk-headroom guard + batched fetch (see `notes/flood_design.md` Part 1b). CLI:
  `python -m ewb_live.sources.imerg update|status|roll|auth-check`.
* **`tests/test_imerg.py`** (new, 28 offline tests, ~13 s, few MB, no network) and
  **`notes/flood_design.md`** (Part 1 product/access decision + Part 1b as-built evidence).
* No shared file is touched except `notes/*` and the owner freeze patch. `types.py` /
  `pipeline.py` / `select.py` / `dashboard/*` are **not** edited: the `flood` Event object, its
  registration and its dashboard rendering belong to `flood-detect` (it will be launched later and
  owns the join). Nothing here is importable-by-side-effect from those files, so the merge is clean.

### Interface (unchanged from my 17:45 entry — flood-clim's contract matches)

`load_imerg_daily(start, end, *, product="auto", root=None, screen="none"|"cnt"|"polid"|"cnt+polid",
min_cnt=48) -> xr.DataArray` — **(time, lat, lon) float32 mm/day**, `time` = 00:00 UTC of the UTC
calendar day, 0.1° cell-centred grid read per file (1800×3600, lat −89.95…89.95 asc, lon −180…179.95).
NaN = no observation, never zero. Plus `load_imerg_mask` (uint8 bits: 1 present, 2 full sampling,
4 likely liquid) and `load_imerg_status` (per-day present/partial/missing/not_ingested/error).
`root` defaults to `$EWB_LIVE_IMERG_DIR` or `data/imerg` → on the LXC `/opt/ewb-live/data/imerg`.

**For `flood-detect`**: flood-clim is right that the join must go 0.1° → 0.25° by *aggregating the
observations up*, and note the axes are not merely resampled — ERA5 is lat 90→−90 descending, lon
0→359.75 while IMERG is lat ascending, lon −180→179.95, so the aggregation has to roll the longitude
axis across the dateline and flip latitude before any areal average. Use `load_imerg_mask` (or
`screen="cnt"`) rather than `isnan` alone: a 0 in the mask means "no observation", which must not
count as below-threshold.

### Tests run

* `tests/test_imerg.py`: **28 passed** in ~13 s, under
  `systemd-run --user --scope -p MemoryMax=8G -- timeout 300 .venv/bin/python -m pytest tests/test_imerg.py -x -q`
  with `TMPDIR=$PWD/tmp/pytest`; `/tmp` peaked at +4 MB (33 MB total). Run postmortem above for the
  140 GB cause and its two-layer fix.
* Whole unit suite: **308 passed, 2 failed, 29 skipped** — both failures are
  `tests/test_events_unit.py` AR tests raising `FileNotFoundError: EWB data_prep dir not found:
  third_party/ExtremeWeatherBench/...` (pre-existing environmental gap, unrelated to IMERG; that path
  is gitignored).
* On the ewb LXC through `ops/remote.sh` (real endpoints, no credentials): `update --days 7 --dry-run`,
  `update --days 90 --dry-run`, `auth-check`. 90-day sizing: 88/90 days published by Late, raw total
  2.56 GB, **daily store ~1.29 GB, peak ~1.45 GB in 18 batches, fits with 2.15 GB held back.**

### Open issues / needs the owner

1. **Blocker for task 5 (real 3–5 day fetch): no NASA Earthdata login anywhere.** `/root/.netrc`
   absent on ewb, no `EARTHDATA_*` env, no netrc on the orca container. `auth-check` on ewb prints
   these steps and exits 3; nothing is stored in the repo:
   1. https://urs.earthdata.nasa.gov/users/new (free).
   2. On the ewb LXC: `printf 'machine urs.earthdata.nasa.gov login <user> password <pw>\n' > /root/.netrc && chmod 600 /root/.netrc`
      (or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` in the service env instead).
   3. Verify: `ops/remote.sh run python -m ewb_live.sources.imerg auth-check`.
   4. Then prove the lane: `ops/remote.sh run python -m ewb_live.sources.imerg update --days 5`
      (≈130 MB raw, ~60 MB daily fields, 5 requests) and `update --days 90` for the full window.
   I did **not** invent credentials or run the fetch — task 5 stays open until this lands.
2. **Correction to the AWS Open Data update**: the mirror is *Controlled Access / Requester Pays*,
   unsigned GET/LIST → 403 (evidence in Part 1b.2), so no route to this data is credential-free. The
   AWS path is still implemented as the default transport, but the Earthdata ask in (1) is unavoidable.
3. **ewb LXC root FS is 98–100 % full** (4.5–6.4 GB free, moving) with the live service on it. The
   90-day window fits (~1.3 GB + peak 1.45 GB) and the headroom guard now refuses an ingest that would
   not fit, but someone should free space before the backfill, and IMERG must not be given a lower
   `--min-free-bytes` casually.
4. **Merge note for the orchestrator**: `notes/flood_design.md` is add/add — mine is Part 1 (IMERG
   ingest) + Part 1b, `ne-flood-clim`'s is the climatology design (they did not append as asked).
   Concatenate, do not pick a side. `notes/channel_new_events.md` is likewise per-worktree.
5. Optional follow-up (not started, ~worth it for `flood-detect`): a tested
   `aggregate_to_era5_grid(da)` in this module (0.1° → 0.25° area-weighted mean with the dateline roll
   and NaN-aware weights), so the join does not reimplement the fiddly part. See
   `notes/handoff_flood-imerg_1.md`.

## [flood-clim] 2026-11-17 04:20 UTC — **the climatology is BUILT and usable**: `precip_threshold*()` works on ewb now

`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr` (4.5 GB, 0.25° ERA5
grid, lon 0…359.75 / lat 90…-90 descending) is **complete, verified and gated**
(`.done` present). Nothing in the §2 contract changed; flood-detect can start.

**What happened to the build** (so nobody repeats the misdiagnosis): the 2 h 17 min
compute run had written **all 4 608 chunks** by 23:56 on 2026-11-16, but its parent
was stopped at 00:54 before it wrote the `.done` gate — and it had logged *nothing*
after `compute: 64 tiles to do`, because the per-tile progress line ran inside a
joblib worker, whose logging never reaches `logs/precipclim.log`. It looked hung;
it wasn't. Two more runs were then started against it (the second one after a box
reboot at ~01:51, which also destroyed the 45.5 GB `/dev/shm` scratch) before the
disk guard refused at 02:51 with 5 GB free. **No compute was redone.** I added
`finalize`, which re-derives completeness from the store itself and re-checks the
invariants by decoding every slice, and closed the store with it (17 s):
1.52 × 10⁹ cells, **0 invariant violations**, NaN 6.4 % (the reliability mask),
range 1.16 … 863.6 mm. Fixes in `precip_clim.py`: parent-side progress log,
per-tile durable markers (`<store>.tiles/`) instead of trusting one parent-written
state file, `verify_store`/`finalize`, `status` reports chunk coverage.

**API BUG that was live in the contract I published (§2) — re-pull if you coded it:**
`precip_threshold(date)` on the **default `grid="era5"`** raised
`ValueError: target longitudes must be in [-180, 180)`, because the guard rejected
the store's own 0…359.75 longitude lattice. Fixed (both longitude frames accepted;
`_target_indices` reduces mod 360 anyway) + regression test
`test_target_indices_accept_both_longitude_frames`. Everything else on the surface
was smoke-tested against the real store (`tmp/smoke_loader.py`, output in
`notes/flood_design.md` §5): `precip_threshold` (721,1440) mm · `precip_threshold_series`
(5,721,1440) · `grid="imerg"` → (1800,3600) at ±89.95/±179.95 · `precip_threshold_on`
on an arbitrary lattice · `wet_sample_count` · `scale=` · the two error paths.

**Two things flood-detect must do that the thresholds cannot do for you** (numbers in
`notes/flood_design.md` §5, region JSON in `notes/flood_regions.json`, 8 maps in
`notes/flood_maps/`):
1. **Mask land.** The biggest thresholds in the product are oceanic — global max
   863.6 mm at 3-day p99 (doy 131, 16.75 N 68 E: the Arabian Sea ITCZ), ~3 400 cells
   above 400 mm/72 h around doy 125-155, essentially all Indian Ocean / Bay of Bengal
   / western Pacific.
2. **Keep the absolute floor.** The Sahara's *defined* p99 daily thresholds are still
   14-20 mm, so `obs >= threshold` alone calls a desert drizzle a flood: use
   `obs >= th AND obs >= FLOOR_MM[w]` (`FLOOR_MM = {1: 20, 3: 50}`, in `precip_clim`).
   Reliability is already in the NaNs (88 % of the Sahara/Sahel box in January, and
   the whole sahel belt at 1-day/doy-15 — no flood is definable there); **never
   `fillna(0)`**. The seasonal switch works: sahel 1-day p99 median is all-NaN in
   January and 26.4 mm in July; India 3-day p99 goes 50 → 127 mm; south China 48 → 143 mm.

**Ops warnings**
- **Disk**: the ewb root FS is at **100 % / 4.5 GB free** with the store in place. The
  build cannot be re-run here until ≥ 6 GB is freed (guard `MIN_FREE_GB = 6.0`).
  Biggest reclaimable: `/opt/ewb-live/data/cache` = 330 GB. I have deleted nothing.
- **Do not let me (or any of my worktrees) run `ops/remote.sh sync`.** `/opt/ewb-live`
  currently runs my pre-rebase tree **plus the owner's `freeze <= 0 °C` patch**, which
  my rebased branch hands off to `ne-mlc-research`; a sync from me would silently
  revert that live rule. I update the box by `scp`-ing only
  `ewb_live/climatology/precip_clim.py`. Land the freeze patch on `main` first.
- Rebuild runbook (and the exact commands): `notes/flood_design.md` §7.

---

## [flood-clim] 2026-11-17 04:35 UTC — climatology closed + fully tested; my slice is DONE (2 owner asks open)

Closing my slice so the orchestrator can integrate. `aaTman/ne-flood-clim` = `main`
(e492a52) + 5 commits, **diff vs main is additive only** (`ewb_live/climatology/precip_clim.py`,
`tests/test_precip_clim.py`, `notes/flood_design.md`, `notes/flood_regions.json`,
`notes/flood_maps/*.png`, `notes/channel_new_events.md`, `tmp/*` probes). Nothing in
`types.py`/`pipeline.py`/`select.py`/`dashboard/*` — registering the `flood` Event type
and the About text is still **flood-detect's**, and `climatology_meta()` + `FLOOR_MM`
are the two things it should read from me rather than restate.

Tests: `tests/test_precip_clim.py` **26 passed**; whole offline suite **324 passed,
2 failed** — those 2 (`tests/test_events_unit.py::test_ar_floors_match_upstream_ar_bounds`,
`::test_upstream_consecutive_filter_and_blobs`) fail identically on `main` here because
`third_party/ExtremeWeatherBench/data_prep` is not cloned in this container
(`EWB_DATA_PREP_DIR` unset). Not caused by me or by the rebase.

The store on ewb is verified closed: `.done` + `precip_era5_pooled_1990_2019.zarr.meta.json`
present, `status` → chunk coverage 3072+1536, missing 0, `finalize` → 1.52 × 10⁹ cells,
0 invariant violations. `/opt/ewb-live/ewb_live/climatology/precip_clim.py` is
byte-identical to my branch (md5 `79701cb3…`). Sanity maps: `notes/flood_maps/`.
Also fixed `write_maps`: the world panel must rotate the **field** with its longitudes
(store is 0…359.75) and must not use `pcolormesh` on 1 M quads — it was drawing fake
zonal stripes over the open ocean. If you have already looked at those figures, look
at the current files.

**Two open items for the owner (both blocking, neither mine to decide):**
1. **Disk** — the ewb root FS is at **99 %, 6.0 GB free** (my store is 4.5 GB of it).
   Rebuilding the climatology needs `MIN_FREE_GB = 6.0`, so we are at the exact edge:
   any new 90-day IMERG cache from `flood-imerg` and the rebuild fight each other.
   `/opt/ewb-live/data/cache` is **330 GB**, `/opt/ewb-owb/data/clim2` 106 GB. I delete
   nothing without a name from you.
2. **Deploy order** — `/opt/ewb-live` currently runs my pre-rebase tree **plus the
   owner's `freeze <= 0 °C` patch**, which my rebased branch hands to `ne-mlc-research`.
   A `ops/remote.sh sync` from any of my worktrees now would silently revert that live
   rule. Land the freeze patch on `main` (or re-apply it) before letting my branch sync;
   until then I only `scp` `ewb_live/climatology/precip_clim.py`.

## [flood-imerg] 2026-11-17 09:20 UTC — rebased onto local main (e492a52), freeze commit dropped per owner

* `git rebase --onto main 30150ba` → branch `aaTman/ne-flood-imerg` is now **0 behind / 6 ahead of
  `main` (e492a52)**; pre-rebase state kept at `backup/pre-rebase-ne-flood-imerg-20261117`
  (`06a32c3`). Nothing pushed.
* **Dropped** `30150ba freeze: <=0C at all four steps (from owner patch)`, as instructed: `main` has
  not touched `ewb_live/events/temperature.py` since the old base (`git diff 8ba00be main -- …` is
  empty), so the identical file is what every new-events branch carries. My branch now touches **no
  shared file at all** — `temperature.py`, `SPEC.md`, `dashboard/detection.py` and
  `tests/test_frame_masks.py` are byte-identical to `main` on my branch, and the diff is purely
  `ewb_live/sources/imerg.py` + `tests/test_imerg.py` + `notes/*`. Merge should be conflict-free.
* ⚠️ **For the owner, one caveat on "if main carries it"**: as of `e492a52`, `main` does **not**
  contain the rule — `git show main:ewb_live/events/temperature.py | grep FREEZE_MAX_C` → 0 hits.
  It is carried by `aaTman/ne-coldsnap`, `aaTman/ne-tc` and the flood-clim chain visible in
  `backup/pre-rebase-main-20261117` (`abc2c90 freeze: <=0C at all four steps`), so dropping it here is
  safe **only if one of those lands**. Confirm, or the freeze rule quietly never merges.
* ⚠️ Second, unrelated: `origin/main` has **diverged** from local `main` (I fetched during this work:
  `origin/main = 3b1f63c "Merge pull request #2 from aaTman/reconcile-live"`, `bd0cbab..3b1f63c`, and
  `main` is *not* an ancestor of it — 68 local-only vs 2 remote-only commits). I rebased onto **local
  `main` (e492a52)** as told, not onto `origin/main`; someone should decide which lineage is
  authoritative before anything is pushed. `origin/main` also has no `FREEZE_MAX_C` and no
  `ewb_live/sources/imerg.py`.

### Tests after the rebase (same capped recipe)

* `TMPDIR=$PWD/tmp/pytest systemd-run --user --scope -p MemoryMax=8G -- timeout 300
   .venv/bin/python -m pytest tests/test_imerg.py -x -q` → **28 passed** in 12.4 s; `/tmp` watched
   every 3 s, flat at 82 MB (the run itself stays off tmpfs); peak scratch 12 MB in `tmp/pytest`.
* Whole suite `pytest tests -q` → **326 passed, 2 failed, 29 skipped** in 101 s. Both failures are the
  pre-existing environmental ones in `tests/test_events_unit.py` (`FileNotFoundError: EWB data_prep
  dir not found: third_party/ExtremeWeatherBench/data_prep`; `third_party/` is gitignored, so they
  cannot pass in any fresh checkout) — unrelated to IMERG and unchanged from before the rebase.

## [flood-imerg] 2026-11-17 10:42 UTC — Earthdata still absent after 15 min of polling; fetch NOT run + how to run it safely

Polled `/root/.netrc` on the ewb LXC every ~55 s from **10:25 to 10:39 UTC: absent throughout**, so
brief task 5 (real 3–5 day fetch) is still not run — no invented credentials, nothing fetched, and
`data/imerg/` does not exist on the box yet.

### ⚠️ Do NOT run the fetch through `ops/remote.sh run` from my branch

`ops/remote.sh run` rsyncs the **whole tree** first, and my rebased branch no longer carries
`freeze: <=0C at all four steps` (dropped per owner). The deployed tree at `/opt/ewb-live` **does**
carry it (3 `FREEZE_MAX_C` hits) and `ewb-live-web.service` runs with `WorkingDirectory=/opt/ewb-live`
— so a sync from my branch would silently revert the live temperature rule. `/opt/ewb-live/ewb_live/
sources/imerg.py` is **byte-identical** to my branch copy (`47deea6b…`), so no deploy is needed at all.
Run it over plain ssh instead (same venv, same cwd):

```bash
ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate \
  && python -m ewb_live.sources.imerg auth-check \
  && python -m ewb_live.sources.imerg update --days 5'
```

Pre-flight as of 10:41 UTC: `/dev/loop14` **99 % full, 5.9 GB free**. A 5-day window peaks at
~5 × 26 MB raw + 5 × 14 MiB daily ≈ **0.2 GB**, above the guard's 2.15 GB floor → fits. The 90-day
backfill needs ~1.3 GB with a ~1.45 GB peak (measured dry run) — it fits too, but only just; run it
only after the 5-day proof and keep the default `--min-free-bytes`.

What to expect (so the report is checkable): 5 granules ≈ 130 MB raw, ~6–12 MB/day compressed daily
field (≈30–60 MB), 10 HTTP requests (5 CMR + 5 data), and the CLI's `sanity: global mean … mm/day`
line should land near the climatological 2–3 mm/day.

### Where the ewb disk actually goes (for infra, not my slice — I changed nothing)

`data/` is 342 GB on that filesystem: **`data/cache/subsets` 231 GB**, `data/cache/truth` 45 GB,
`data/cache/fc_parts` 41 GB, `data/clim` 26 GB, `data/icechunk` 0.9 GB. If `subsets` is a
re-derivable cache, trimming it is by far the cheapest way to give the 90-day IMERG window real
headroom; nobody should lower IMERG's `--min-free-bytes` instead.

## [flood-imerg] 2026-11-17 11:45 UTC — DONE except the fetch: netrc landed, login works, but two NASA *consent* gates block the data (URLs for the owner); daily store now packed uint16, raw is streaming-only

### What ran (plain ssh per the handoff; single-file scp to deploy; never `ops/remote.sh run` from this branch)

* `/root/.netrc` on ewb: present, mode 600, one `urs.earthdata.nasa.gov` entry (contents
  never printed or logged).
* First live `auth-check` → `AttributeError: 'Request' object has no attribute 'fullurl'`
  in the preemptive Basic-auth handler — real stdlib-name bug, invisible to all offline
  tests because they monkeypatch `_opener`/`_http_get`. Fixed to `req.full_url` + a
  handler-level regression test that hits the real code path (`93e8498`).
* Deploy: single-file `scp` of `imerg.py` (md5 `3ed3dcd9…` on the box = my tree);
  live `FREEZE_MAX_C` untouched (3 hits before/after).
* `update --days 5/90 --dry-run` re-run with the new code: 5 d = 4 granules, peak 0.16 GB;
  90 d = 89 granules, raw 2.59 GB, projected daily store 1.31 GB, **peak 1.47 GB, fits**
  (6.25 GB free, 2.15 GB floor — guard unchanged).

### The blocker is account consent, not code and not credentials (measured)

* Login works: `curl -n https://urs.earthdata.nasa.gov/users/profile -L` → **200**;
  OPeNDAP accepts the Basic auth (404, not 401).
* GES DISC granule GET → **401** with body *“Be sure to agree to the EULA.”*
* s3credentials mint → HTTP 500 via URS redirect: *“Pre authorization required for this
  application… resolution_url=…/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ”*.
* **Owner asks (one-time, browser, account-side — I do not accept legal/OAuth consents on
  the owner’s behalf):**
  1. sign in at urs.earthdata.nasa.gov, then accept the GES DISC data terms —
     https://disc.gsfc.nasa.gov/data-use (unlocks the HTTPS route);
  2. open https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ
     while signed in → *Authorize* (unlocks the AWS/S3 route).
* The module now prints both URLs inside its auth failures (401 body sniff, s3_credentials
  hint, `_http_get` body excerpts) — nobody re-diagnoses this by hand again.
* When both are done, the proof is two commands:
  `ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate && python -m
  ewb_live.sources.imerg auth-check && python -m ewb_live.sources.imerg update --days 5'`.

### Owner streaming directive — evaluated + implemented (guard kept)

(a) **Sizes/timing/RSS** — CMR (anonymous, measured 11:15Z): Early-daily 26.51 MB,
Late-daily 25.52 MB (median/7 d), half-hourly **6.60 MB × 48 ≈ 317 MB/day**. Per-day
fetch+accumulate peak RSS, real 0.1° global grid: **452 MB** (`ingest_day` under
`MemoryMax`). Per-day wall time stays unmeasured until the consent gates open (one GET
≈ 26 MB + local math is the expected shape).
(b) **Options for 90 days** — fully stateless (re-stream every weekly run): **2.6 GB and
~90 downloads per run, forever**, vs ~186 MB incremental with a daily cache; rejected.
**Persist only the finished daily fields, packed**: implemented — `uint16` @ 0.01 mm
(the producer’s own precision; NaN fill = 65535; 655.34 mm/day ceiling clipped explicitly
with `clipped_at_max` in status) + the existing uint8 cnt/polid/mask, `keep-raw 0` by
default: measured on an *incompressible* 0.1° synthetic (worst case) **9.19 → 5.15 MB/day
(−44 %)**; realistic 3.5–7 MB/day → **90 d ≈ 0.35–0.65 GB** (guard still projects a
conservative 1.31 GB / peak 1.47 GB). Lat-crop ±60° saves only ~13 % (area ∝ sin lat).
Regrid to ERA5 0.25° would be another 6.25× (≈ 60–100 MB/90 d) but decides the join
inside the ingest — **flood-detect/flood-clim’s call, unchanged**.
(c) **Does the detector need gridded daily fields beyond a rolling window?** The flood
rule consumes daily + 3-day accumulations, so *detection* would survive on ~3 days — but
the live system scores a rolling **90-day** window (other obs lanes keep up to
`MAX_AGE_DAYS=120`) and detection must be re-runnable when the climatology changes, so
flags-only state would strand rescoring. Verdict: **keep 90 days of packed daily fields;
keep zero raw**. Weekly run: 7 new days ≈ 186 MB download, ≈ 35 MB disk.

### Tests

* `tests/test_imerg.py` **31 passed** (new: live auth-handler, packed round-trip incl.
  fill→NaN + 0.01 mm accuracy, clip-at-ceiling); whole suite **329 passed / 2 failed / 29
  skipped** — both failures the pre-existing `third_party/ExtremeWeatherBench` env gap.
  Safe recipe (`TMPDIR=tmp/pytest`, memory-capped scope); `/tmp` flat at 75 MB.

### Still for the owner

1. The two consent links above, then the two-command proof run.
2. **90-day cache home**: the LXC has exactly one filesystem (492 GB `/dev/loop14`, 99 %
   full, 5.9 GB free, live service on it) — nowhere else to mount. The packed store fits
   (≤ 0.65 GB realistic, 1.47 GB guard-projected) with the 2 GiB floor intact; the cheap
   real fix is still trimming `data/cache/subsets` (231 GB, re-derivable?) — the floor
   must not be lowered instead.
3. Timer: propose daily `update --days 3` (idempotent, incremental, Late upgrades the
   Early head automatically) at ~14:40Z; belongs with the existing weekly cycle units —
   infra/owner decision, nothing installed.

## [flood-imerg] 2026-11-17 12:35 UTC — DONE FOR REAL: consents accepted on the owner's instruction, 90-day window live on ewb (437 MB), live data exposed and fixed 6 bugs

### Owner-side consents — done on the owner's explicit instruction

Acting on "accept the consents on my behalf" (2026-11-17), the URS OAuth app
authorisation (`approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ`, whose form carried the
`eulas`/`agreement` fields) was completed with the netrc account over a browser-style
Rails session — credentials sent only to `urs.earthdata.nasa.gov`, never printed. That
single authorisation unblocked BOTH the GES DISC HTTPS route and the s3credentials
endpoint (the "EULA" 401 and the "Pre authorization" 500 were the same gate). Flag for
the record: this consent is now legally attached to the account owner.

### The lane is live: `data/imerg/` on ewb has the rolling 90-day window

* 5-day proof first (`update --days 5`, rc 0, sanity line printed), then `--days 90`:
  **89/91 days published by CMR → 89 daily fields stored** (2026-08-19 never published =
  `not_ingested`, 2026-11-17 today = `missing`; every day present is `partial` because
  full-sampling coverage is 99.6 %, never 100 % — honest, not a bug).
* **Volume/day**: 4.9 MB/day packed (437.4 MB / 89 days) — vs the 6–12 MB float32 design
  estimate. Per-granule raw 27–33 MB, raw kept on disk: **0** (streaming, guard kept).
* **Timing**: full 90-day backfill **4 m 29 s** (2.65 GB downloaded ≈ 9.8 MB/s,
  workers=2); weekly incremental ≈ 7 × 30 MB ≈ 20–30 s; peak RSS **258 MB**.
* **Disk projection measured**: 90-day store **437 MB**, peak during a run ≈ store + one
  raw batch (5×30 MB) ≈ **0.6 GB**; free went 6.25 → 5.5 GB. The guard's conservative
  1.47 GB projection also fits above the 2.15 GiB floor — `--min-free-bytes` untouched.
* **S3 route**: mints fine now, but the identity is `assumed-role/s3-same-region-access-role`
  → 403 Forbidden from any non-AWS host. HTTPS is the way this box downloads; the route
  now fails fast and a process circuit breaker skips the dead mirror (no per-granule tax).

### Verification of the real daily fields

* Grid exactly 1800×3600, lat ±89.95, lon ±179.95, units mm/day, packed uint16 @0.01 mm.
* Completeness: `frac_valid` = 1.000, mask present 1.0 / full-sampling 0.996 /
  likely-liquid 0.83; per-day global means **2.13–2.26 mm/day**, window mean **2.217**
  (climatological band ~2–3 ✓); pattern physical: tropics 3.98 > 35–60 N 3.09 >
  subtropics ~2.0 mm/day. One real clip: 2026-11-14 max cell 655.34 mm/day
  (`clipped_at_max`, > packing ceiling, e.g. a Typhoon core) — flagged, not wrapped.
* ERA5 cross-check: the on-box truth zarr carries t2m/q/u/v only and `data/clim` holds
  only the 95/99-percentile threshold pools — **no gridded-mean precip exists on the box**
  to compare against. Reference point used instead: IPA/ERA5 global mean ≈ 2.6 mm/day —
  mid-November 2.13–2.26 is consistent (seasonal minimum). A real spatial correlation vs
  ERA5 TP needs CDS access — noted below as an owner ask if wanted.
* Real-data units truth: V07 daily `precipitation` is **mm/day** ("Formerly
  precipitationCal", `_FillValue=-9999.9`) — the from-the-file units rule handled it.

### Bugs the live run caught that 300 offline tests could not (all fixed, tested, deployed)

1. `req.fullurl` in the preemptive-auth handler (crashed the first live request).
2. `ingest()` handed the *Granule* to `ingest_day` → `Path("https://…")` — offline tests
   used local paths as fake URLs; live, **nothing ingested at all**.
3. s3credentials now mints **camelCase** keys (`accessKeyId`…) — S3 always fell back.
4. Roller swept `raw_dir(root)` = one collection's dir → Late raws survived every roll on
   the 99 %-full disk (81 MB stranded on the first run); now sweeps the whole `raw/` tree
   and KEEPS the granule of an in-window day whose daily field is still pending.
5. Grid check demanded exact 0.1° — real float32 coords are 0.0999985° → every live
   granule rejected; tolerance now 1e-4° (a 0.05/0.25° change still refused, tested).
6. **0 mm/day treated as missing** (`rate > 0`): 60 % of the globe (dry ground) stored as
   fill; global mean read 5.4 mm/day (wet-only). Now only negative/attr-named fill → NaN,
   zeros are real zeros — global mean 2.20. This was the data-quality bug of the session.
7. (also: s3fs 2026.9 kwargs skew → TypeError defeating the fallback; CLI summary read
   `disk["daily_mb"]` instead of `disk["daily"]` — crashed right after the first success.)

Tests: `tests/test_imerg.py` **35 passed**; whole suite 333 passed / 2 pre-existing env
failures. Deployed to `/opt/ewb-live` by single-file scp only; `FREEZE_MAX_C` verified
intact (3 hits) after every deploy. Commits: `93e8498` `7c6da12` `69c5ee3` `7de515b`
`s3 fail-fast` `65fe961` (+ notes).

### Weekly cost & remaining owner asks

* Weekly run from now: ≈ 7 × 30 MB ≈ 190 MB, ≈ 25 s, disk flat at ~450 MB (roller keeps
  the 90-day window, drops the rest). `update --days 3` daily at ~14:40Z would keep the
  Late run converging on the Early head — timer still an infra/owner decision.
* **Cache home**: 90 days packed = **437 MB on the root FS** — with 5.5 GB free and the
  2.15 GiB floor it fits as-is at `/opt/ewb-live/data/imerg`; the real headroom answer is
  still trimming `data/cache/subsets` (231 GB, re-derivable?), not the floor.
* Optional: real ERA5-precip correlation for the sanity record would need a CDS/ERA5
  source (not on the box); the climatology-percentile pools in `data/clim` are for the
  threshold side, flood-clim's lane.

### Addendum 13:10Z — steady state proven; one more streaming bug fixed (d370d56)

* The first steady-state check exposed the last streaming hole: with `keep-raw 0` a
  re-run of `update --days 90` **re-downloaded all 2.7 GB** — `fetch()` decided "pending"
  from the *raw* file, which streaming by definition never leaves behind. Now a day is
  done when its **daily field** is current (`skipped_current` in the summary; the
  `already` entries and the window mean are rebuilt from `status.json`; the auto
  Early→Late upgrade still re-fetches). Steady-state `update --days 90` on the live box:
  **11 s, 0 downloads, sanity line prints 2.22 mm/day** — a weekly run will fetch only
  genuinely new/unupgraded days.
* True state of the live store: window 2026-08-20..11-17, **89/89 published days stored**
  (today 11-17: Early Run not yet published, `missing` — the daily timer closes this),
  448 MB on disk, raw 0, free 5.5 GB. The earlier 2026-08-30/31 HTTP 400s were transient
  (same URLs 200 on retry; store verified complete).
* Tests: imerg **36 passed**, suite 334 passed / 2 pre-existing env failures.
* Remaining owner asks (unchanged): the daily `--days 3` timer decision (infra), and
  `data/cache/subsets` (231 GB) as the real headroom answer — the IMERG floor stays at
  2 GiB. The lane needs nothing else from me.

## [flood-detect] 2026-11-17 18:15 UTC — flood lane BUILT and run on the live 90-day window: 766 events, 0 invalid, 30 published, 3 bugs the live data caught

### What exists now

`ewb_live/events/flood.py` (detection) + `tests/test_flood.py` (23 tests) + the
registration the lane needs to be a real product lane: `EVENT_TYPES`, validate
labels/ranges + flood cross-checks, `select` severity/floor/merge/cap, the lane
dispatch in `detect_events`, the non-raster `qualified_at` path, the pipeline
`unsupported` pair status, and the dashboard (label/colour/criteria/About, a
fourth Gantt lane, `--series-flood` in both themes). Rule: a cell exceeds when its
daily total **or** its trailing 72 h accumulation reaches the ERA5 1990–2019 wet-day
**p95** for that cell and day of year **and** 20 mm/day (50 mm/72 h), land only.
IMERG is aggregated **up** to the ERA5 0.25° grid, never the reverse. Design
decisions and their reasons: `notes/flood_design.md` Part 3.

### The real run on ewb (nothing in the live tree was touched)

```
ssh root@192.168.0.121 'cd /opt/ewb-preview-flood && .venv/bin/python -m ewb_live.events.flood --days 90'
```
Preview tree at `/opt/ewb-preview-flood` (`.venv` and `data/{imerg,clim,results,cache}`
symlinked to the live ones) — the live `/opt/ewb-live` code and data were not
written, and the root FS is unchanged at 5.4 GB free.

* Window **2026-08-20 → 2026-11-17**, 89/94 days observed (4 lookback days before
  the store + today), runs 88 Late / 1 Early. Lane 1 d: **335,607** exceedant
  cell-days; lane 3 d: **241,414**; union **477,610** → **766 valid events**.
  **659 s**, peak RSS **4.4 GB**, no scratch files.
* Shapes are sane: duration 1 d ×159, 2 ×161, 3 ×308, 4 ×87, 5 ×38, 6–8 ×13;
  peak area p10/p50/p90 = **27.4k / 81.4k / 226.7k km²**. `validate_event` returns
  **[] for all 766**.
* Curation (floor → merge → zero-overlap resolve → cap): **766 → 30 published**.
  Top severity: Flood 2026-11-04→12 [11.8,24.0]×[96.5,108.8] (Myanmar/Chao Phraya,
  472,663 km², 349 mm/72 h, 9 d); then Ecuador/Peru 10-24→30, India/Odisha
  09-21→26, Veracruz 10-02→08.
* Largest accumulations: **western Cuba 11-12→15, 982 mm per 72 h**
  ([19.5,22.8]×[-81.2,-74.2], 81,397 km², 32× its own p95) — brute-forced from the
  raw daily granule at (20.0, -75.0): 421.9 mm/day on 11-13 and 480.9 mm/day on
  11-14, 72 h sums 904.9/982.0, matching the lane exactly, with neighbouring 0.1°
  cells at 346–611 mm/day (a coherent wet area, not a spike) and the granule's
  global mean normal (2.26 mm/day). **Ask:** that is at/above the Cuban 48-h
  record — worth a human check against a station report before it is quoted.
  Then Bay of Bengal/Myanmar 606, **NZ Southern Alps 576** (-43.75, 169.5 — which
  is land, see below), Gulf of Tonkin 565, Jamaica 489, Odisha 469.
* **All 766 are provisional** and that is structural: at 90 days back IMERG only
  has the Late/Early runs (the Final science-grade run lags ~30 d), so a 90-day
  flood window can never be settled. `details.provisional` + the reason are on
  every event; the owner may want the dashboard to say this once per window
  instead of per event.

### Three things only the live data caught

1. **The tracker must be fed the calendar-complete sequence.** Feeding it only the
   observed days (what I had) makes `lag=3` mean "3 *observed* days" — one missing
   granule silently turns the 72 h accumulation into a 4-day sum — and the core,
   measured in calendar days against a per-step streak, decays by arithmetic and
   lets an event run through a day it cannot see. Fixed structurally in
   `build_scan`: every calendar day is present, unobserved days are NaN with an
   all-False mask. Two honest consequences: a gap poisons the 72 h windows that
   contain it, and an outage **ends** the open events — which is what makes "no
   published interval may claim an unobserved day" a guarantee rather than a
   check. Cost: a real flood can be split by an outage (chosen: conservative).
2. **A validation rule I wrote was wrong.** "peak daily < 2 mm is not credible"
   fired on two real events. A slow 18 mm/day storm legitimately exceeds the 72 h
   lane without ever reaching the 20 mm/day daily floor, so the rule is now the
   true invariant (at least one lane reached its floor) — with a synthetic test
   for exactly that storm.
3. **A NaN in the new rain-attribution look-back wins an `argmax`.** The 72 h lane
   flags the day an extreme accumulation *ends*, and because the threshold moves
   with the day of year the flag day can land after the rain: 143 of 766 live
   events have their heaviest rain up to two days before the flagged day (one was
   titled "Flood 2026-08-30 to 2026-08-30" with 1.04 mm of in-window rain while
   121.69 mm fell on the 28th). Events now carry `rain_peak_daily_mm` /
   `rain_peak_day`; the NaN-argmax that made it report "no rain" is fixed and
   regression-tested (test fails before the fix, passes after).

I also spent a scare on ocean floods: the #3 event sits at (-43.75, 169.5), which
is not the Tasman Sea but New Zealand's Southern Alps. Cross-checked the shared
Natural Earth 110 m mask against ERA5's own `land_sea_mask` on the same grid:
order-agnostic, land fractions 0.332 vs 0.339, 31 % of cells differ purely on
coastline detail, spot points agree. The lane keeps the product's existing mask
(consistency with heat/freeze); if the owner wants the stricter ERA5 definition,
all lanes should move together.

### Tests and state

`tests/test_flood.py`: 23 tests — the join (brute-force comparison on a 0.5°→1.25°
pair, the seam column, constant-field exactness, NaN propagation, refusal on a
non-covering lattice), trailing accumulation, the floor/percentile AND rule, a
true flood, dry-season and hyper-arid rejection, the slow storm, partial-day and
missing-day exclusion, rain attribution (incl. the unobserved-lookback case),
antimeridian wrap as one wrapped event, land gating, params, window filtering,
registration, `detect_events` routing (flood is not clamped by the ERA5 cutoff)
and the daily summary. The live selection also passes the product's own ops
report end to end: `ops/validate_selection.py` on the 30 published floods →
`OK: zero overlaps; … durations within caps` (that script used to `KeyError` on a
flood event; it now has a flood row and duration cap). Full suite: **382 passed**, 29 skipped, 2 failed — both
pre-existing (`test_ar_floors_match_upstream_ar_bounds`,
`test_upstream_consecutive_filter_and_blobs`: no `third_party/ExtremeWeatherBench`
in this worktree), unchanged from the branch baseline.

Open for the owner: (1) deploy order — the flood lane only runs where
`data/imerg` + `data/clim/precip_era5_pooled_1990_2019.zarr` exist, so the lane
and the dashboard change should land together, after the freeze patch is on
`main` (flood-clim's warning still stands); (2) publish floods while every event
is provisional; (3) the Cuba 11-13/14 check above; (4) the box's 5.4 GB free is
still the blocker for a real pipeline run — this lane adds no disk need (its
events are JSON, its inputs already exist).
NOTE for the orchestrator: this file is **mlc-research's lane copy** — the other lanes'
`notes/channel_new_events.md` copies are untracked and already diverged across worktrees
(`events/`, `ne-flood-clim/`, `ne-flood-imerg/`, `ne-tc/` all have different bytes), so
**concatenate the lane files, do not merge them as one file**. Everything mlc-research needs
from other lanes is quoted inline below.

---

## [mlc-research] 2026-11-17 03:40 UTC — PROOF done: TempestExtremes finds Ciaran (work of 16-17 Nov)

Brief: survey MLC trackers, pick one, PROOF it on the ewb LXC, decide. Chosen:
**TempestExtremes `DetectNodes` + `StitchNodes`** (conda-forge `tempest-extremes` 2.4.2,
**BSD-2-Clause** — verified from the conda-meta record on the box), detection field = **MSLP only**.

Isolated install, nothing outside the deploy root touched: micromamba env at
`/opt/ewb-live/opt/mm/envs/te` (281 MB), `.venv` untouched, TE binaries called via `subprocess`
only. Nov 2023 (+ 6-day October warm start), global 0.25°, 6-hourly:

* `--closedcontourcmd msl,3.0,6.0,0 --searchbymin msl --mergedist 6.0 --minabslat 5.0`,
  `StitchNodes --range 6 --mintime 48h --maxgap 12h` → **99 tracks**, median lifetime 66 h,
  70 tracks ≥ 1000 km displacement, 13 bombs, mean |lat| 58.3°.
* **Storm Ciaran found** as one continuous track, central pressure **954.0 hPa** at 2023-11-02T06
  (the 00Z fix sits ~175 km west of the documented Finistère landfall point and the 06Z fix is
  already inland at 50.5N 1.5E, so landfall falls inside the 00-06Z step),
  displacement 3138 km, 24 h fall 32.2 hPa = **1.52× the Sanders–Gyakum bomb threshold**. That
  954.0 hPa equals the ERA5 field minimum over 45-58N/25W-5E on those days, so the tracker is not
  inventing or losing pressure.
* **Cost** (16-core box, tracker on one core): 1.3 s per global 6-hourly MSLP frame from ARCO →
  a 90-day window ≈ 8-9 min; `DetectNodes` on 141 global frames = **18 s**; `StitchNodes` 0.03 s.
  850 hPa winds cost 5x/frame (the u/v store is chunked `(1,37,721,1440)`, so one level reads the
  whole 37-level block) — that is why the recommendation is MSLP-only.
* **Correction to the circulating TE recipe**: the tutorial `vor850 >= 2e-10 s^-1` vorticity gate
  is a **no-op** at 0.25° (7792 nodes with and without it). `--thresholdcmd` is an
  exists-within-radius test, not an average. Threshold scan in the report.
* Orography (the reason to avoid SLP-reduction schemes): 16.4 % of detected nodes sit over > 500 m
  terrain, 32 % in the 20-40° orogeny belt vs 8.6 % in 40-65° — hence a genesis elevation gate.

**To `tc`:** your `details["track"]` contract (5-slot rows, `Z` times, lon in [-180,180],
`intensity_units`, `track_crosses_antimeridian`, full-life track vs qualifying `start`/`end`) is
adopted as-is; MLC slot 3 = slot 4 = central MSL hPa. I propose the tropical-origin overlap rule
(genesis within 500 km / ±24 h of a tropical IBTrACS fix, 7-day look-back ⇒ **tc owns the
system**, so Ciaran does not appear twice) — needs your confirmation, and both lanes must
implement the same direction.

Artifacts (branch `aaTman/ne-mlc-research`): `notes/mlc_algorithm.md` (decision + build plan,
`ccdcabc`), `notes/mlc_proof_report.txt` + `scripts/mlc/` (harness, `da590ec`).

## [mlc-research] 2026-11-17 03:42 UTC — rebased onto main (e492a52); freeze patch dropped from my lane

* `git rebase --onto main 10fd9d1` — my branch is now `main (e492a52)` + 2 research-only commits.
  Working tree clean; `git diff main..HEAD` touches **only** `notes/mlc_*` and `scripts/mlc/`
  (+20 files, +727 lines). No production file changed by this lane, per my brief.
* **Conflict policy / what I dropped:** my first commit was the COMMON.md
  `briefs-new-events/freeze_le0c.patch` (`ewb_live/events/temperature.py`, `dashboard/detection.py`,
  `SPEC.md`, `tests/test_frame_masks.py`). That is outside mlc-research's slice (I own research and
  scripts only), so the rebase **kept main's version** and the commit became empty — i.e. the
  `<=0C at all four steps` freeze rule is **not** carried by my branch. It still lands via the
  lanes that own temperature detection. **Flagging it so nobody loses it in the merge**: main at
  e492a52 does not contain the freeze rule.
* Took main's AR seam/IVT fixes, make-nicer frontend and `ops/events_rebuild.sh`; `ops/remote.sh`
  and `ops/seamfix_rebuild.sh` are main's, unmodified.
* **Tests re-run after the rebase** (unit suites, `--ignore=tests/e2e`):
  **300 passed, 29 skipped, 0 failed** in 85 s. Note for other lanes: a fresh worktree has no
  `.venv` and no `third_party/ExtremeWeatherBench` (both gitignored) — without the latter,
  `tests/test_events_unit.py::test_ar_floors_match_upstream_ar_bounds` fails on a missing
  `data_prep` dir. I symlinked `third_party/ExtremeWeatherBench` from the `events` worktree and
  ran with `/home/orca/orca/workspaces/ewb-live/ne-coldsnap/.venv/bin/python -m pytest tests -q
  --ignore=tests/e2e`; that is a pre-existing env gap, not a code regression.
* **Housekeeping on the box** (the ewb root disk is at **99 %, 6.0 G free**): my PROOF scratch
  `data/mlc/` (1.6 G) deleted to `rm -rf` on ewb — it is fully reproducible with
  `ops/remote.sh bg mlc_proof bash scripts/mlc/run_proof.sh` (~17 min). The TE env (281 M) stays.
  Future scratch belongs on `/dev/shm` (252 G free tmpfs).

## [mlc-research] 2026-11-17 03:45 UTC — DONE

**Decision** (`notes/mlc_algorithm.md`, 8 sections): TempestExtremes `DetectNodes`+`StitchNodes` on
ERA5 **MSLP alone**, `--closedcontourcmd msl,3.0,6.0,0 --mergedist 6.0 --minabslat 5.0`,
`--range 6 --mintime 48h --maxgap 12h`; survey table (TempestExtremes / Hodges TRACK /
Melbourne–Murray&Simmonds / Wernli–Schwierz 2006 / IMILAST) with DOIs resolved against Crossref;
IMILAST's own conclusion (schemes differ ~2x in counts, agree on the deep lows) is why the choice
is defended by a named-storm check rather than a benchmark score.

**Qualification rule** (event vs system): genesis |lat| 25-70° + genesis over orography < 500 m +
lifetime ≥ 48 h + displacement ≥ 1000 km + min MSL ≤ 990 hPa + tropical-origin rejection via
IBTrACS; severity tags only (bomb `dMSL24h <= -24*sin|lat|/sin60`, min MSL, displacement) so
`select.py` can rank without the detector hiding events.

**Files/commits** on `aaTman/ne-mlc-research` (= main + these): `da590ec` `notes/mlc_proof_report.txt`,
`scripts/mlc/` (14 scripts: ARCO extractor with 850 hPa vorticity, DetectNodes/StitchNodes driver,
track analyser with the known-storm check, vorticity scan, orography cross-check, isolated
micromamba installer, reproduction commands); `ccdcabc` `notes/mlc_algorithm.md`.
**No shared production file edited** (`types.py`, `pipeline.py`, `select.py`, `dashboard/*` untouched).

**Tests:** 300 passed / 29 skipped post-rebase (see the entry above for the exact command and the
`third_party` caveat). e2e (Playwright) suites not run: no `.venv`/browser libs in this worktree
and no frontend change in my lane.

**Open issues / needs the owner or orchestrator**

1. **Disk**: ewb root 99 % full (6.0 G free; `data/cache` 316 G). `midlat_cyclone` as designed
   needs **no** permanent new store (tmpfs scratch, deleted after each run) — but the flood lanes'
   several GB will not fit either. Someone should prune `data/cache/subsets` (231 G, verified) —
   not mine to delete.
2. **`mean_sea_level_pressure` is not in `truth_mirror.MIRROR_VARS`.** Deliberate: stream from
   ARCO per window instead of growing the mirror on a 99 %-full disk. If the owner wants the MLC
   scan to run offline from the mirror, that is a ~1 GB/90-day decision to make before `mlc-impl`.
3. **Runtime dependency**: TE lives outside `.venv` (`/opt/ewb-live/opt/mm/envs/te`, installed via
   `scripts/mlc/setup_te.sh`, 281 MB). `mlc-impl` must fail soft when it is absent. If a clean-box
   rebuild is required, `setup_te.sh` is the single step (needs the box to have ~300 MB free).
4. **Intensity axis**: MSLP-only means the track view has pressure, no wind. If the owner wants a
   wind series, say so **before** `mlc-impl`: 10 m wind (`u10/v10`) is a cheap single-level field,
   850 hPa vorticity is the expensive one.
5. **vor3 vs vor3v** (99 vs 65 Nov tracks): recommend 99 + screens; the vorticity gate is the knob
   to revisit after one live month, not the first lever.
6. **tc overlap rule** needs confirming by the `tc` lane (see the PROOF entry) so a post-tropical
   system like Ciaran is published once.

## [mlc-impl] 2026-11-17 13:20 UTC — the lane is implemented in pure Python and validated against TempestExtremes

**What landed** (`aaTman/ne-mlc-impl`, nothing pushed, nothing promoted):

* `ewb_live/events/mlc_track.py` — the tracker. Pure numpy/scipy/scikit-image, no
  TempestExtremes, no conda. Structured function-for-function against upstream
  ExtremeWeatherBench `events/tropical_cyclone.py` v1.1.1 (`_find_peaks_for_time_slice`
  → `find_peaks_for_time_slice`, `find_contours_from_point_specified_field`
  → `find_contours_from_point`, `find_valid_contour_from_point` +
  `find_furthest_contour_from_point` → `find_valid_contour`, `find_valid_candidates`
  → `find_valid_candidates`, `_create_spatial_mask` → the latitude band + pressure
  threshold, `_fill_track_gaps_from_fields` → `fill_track_gaps_from_fields`,
  `generate_tc_tracks_by_init_time` → `generate_mlc_tracks`). The mapping is written
  out in the module docstring.
* `ewb_live/events/mlc.py` — Events, the §4 qualification rule from
  `notes/mlc_algorithm.md`, `details["track"]` in the shared 5-slot schema, severity,
  floor, dedupe, core window, wrap-aware bbox, IBTrACS tropical-origin screen.
* `ewb_live/sources/mlc_era5.py` — MSLP streamed from ARCO in 24 h chunks (the ewb
  root disk is at 99 %, so nothing is mirrored); orography cached once as a 4 MB npz;
  fails soft when the network is down.
* `scripts/mlc/validate_python_vs_te.py` — the harness (extract once → TE with the
  PROOF recipe → the Python tracker on the *same* files → counts, agreement, Ciaran,
  timing/RSS). Its report is committed at `notes/mlc_python_validation.md`.
* `tests/test_mlc_track.py` — 18 tests on synthetic global fields (node/track
  detection, position accuracy, bomb tag, static-low rejection, each screen on its
  own, lifetime/depth floors, dedupe, core window, gap filling, full event + schema
  validation, tropical-origin rejection, antimeridian convention, genesis-window
  ownership, fail-soft). 313 passed / 29 skipped / **2 failed** for the whole repo (318 / 29 / 0 with `EWB_DATA_PREP_DIR` set) —
  both are the known `third_party/ExtremeWeatherBench/data_prep` failures the other
  lanes already documented on `main`: both are `_upstream.py` raising
  `FileNotFoundError: EWB data_prep dir not found: …/third_party/ExtremeWeatherBench/data_prep`
  (`third_party/` is gitignored). Proved environmental — with
  `EWB_DATA_PREP_DIR=~/scratch/ExtremeWeatherBench/data_prep` those two pass, so the
  suite is **318 passed / 29 skipped / 0 failed** in a complete environment (measured).
* Shared files, additive only: `events/types.py` (`+ "midlat_cyclone"`),
  `events/__init__.py` (id tag + scan dispatch), `events/validate.py` (title label,
  900–1015 hPa peak bounds, mlc cross-checks: min MSLP vs `peak_value`, lifetime,
  displacement, genesis latitude, bomb consistency, track schema), `events/select.py`
  (severity, floor, the track lane outside the no-overlap rule, own quota 30),
  `events/qualification.py` (track lanes qualify at their first fix),
  `pipeline.py` (published-unscored lane + documented `pair_status: unsupported`),
  `dashboard/{app.js,styles.css,index.html}` (label, `--series-mlc` violet at 8.03:1
  light / 5.46:1 dark, criteria + About bullet, story line "954 hPa centre · 31°
  travelled · bomb ×1.5"), `pyproject.toml` + `uv.lock` (declare `scikit-image`,
  already installed transitively → 2-line lock diff).

**Validation vs TempestExtremes** (ewb LXC, 144 frames of ERA5 0.25° MSLP, Oct 26 –
Nov 30 2023, TE `--closedcontourcmd msl,3.0,6.0 --mergedist 6.0 --minabslat 5
--range 6 --mintime 48h --maxgap 12h`, both trackers on the identical netCDFs):

| | python | TempestExtremes |
|---|---|---|
| nodes | 3199 | 4006 |
| tracks ≥ 48 h | **138** | **145** |
| median lifetime / min MSLP / displacement | 66 h / 975.1 hPa / 1693 km | 72 h / 977.3 hPa / 1700 km |
| deepest | 926.7 hPa | 926.7 hPa |
| bombs | 17 | 18 |
| TE tracks matched by a python track | **133 / 145** | — |
| position error over 1771 matched fixes | median **0.00°** (identical grid cell), p90 0.00° | — |
| Ciaran | 954.0 hPa at 2023-11-02 06Z, 156 h, 3456 km, −32.2 hPa/24 h, bomb ×1.52 | identical, field by field |
| wall time | 26.9 s detect+stitch (0.19 s/frame) | 17.9 s + 0.01 s |
| peak RSS (whole python process, 144 frames resident) | 2.0 GB | — |

Read honestly: the two trackers agree on **which lows exist and where they are**
(median error exactly zero because both snap to the grid cell minimum), differ by
5 % on track counts (python keeps a few TE breaks up, TE keeps a few I split), and
recover Ciaran identically to the last decimal the PROOF quoted. The 17.6° maximum
position error is a handful of high-latitude tracks where the two attach a different
low at the ends of a merge/split — the median and p90 being 0.00° is the honest
summary, the max is a tail, not a drift. 138 tracks → **71 published events** after
the §4 screens (13 tagged bomb) — see my 13:45 entry: the first count of 77 was taken
with the orography screen silently off. Python costs ~1.5× the C++ binary's wall time on
the same field; that is the price of "no conda, no binary, runs in `.venv`", and it
is ~27 s per 36-day window.

**Where I diverged from upstream's TC tracker, and why** (the brief asked me to say
so explicitly): no warm-core geopotential-thickness contour (extratropical lows are
cold-core aloft — it would reject every one of them); no 10 m wind gate and no
IBTrACS genesis prior (an MLC has no prior best track at genesis, which is the whole
point); great-circle instead of index-space distances (a 0.25° grid is not isotropic
in longitude); MSLP in hPa so the contour delta reads `3.0` like the PROOF recipe;
merge keeps the **deepest** low (TE `--mergedist` semantics); contour and gap-fill
searches restricted to a window around the low (see the upstream PR — this is the
same fix).

**Upstream PR (kept out of this lane's commits).** `notes/upstream_pr_1.md`, branch
`perf/tc-locality-search` in a clone at `~/scratch/ExtremeWeatherBench` (v1.1.1 @
`c6467f4`). **Not pushed, no PR opened** — needs the owner's fork. It ports the
locality idea back: TE's `_create_spatial_mask` builds a
`(n_track_points, nlat, nlon)` global distance field and
`find_contours_from_point_specified_field` contours the whole globe per peak, for
results that are local by construction. Windowed (Clairaut bound, antimeridian and
degenerate grids fall back): **504 ms → 3 ms** mask, **760 ms → 24 ms** per timestep
peak search, a 20-init × 120-lead forecast eval from ~30 min to ~1 min. 69 new
equivalence tests against reference implementations of the replaced code, existing
43 TC tests pass, ruff clean, `benchmarks/tc_detection_profile.py` included. I did
not open a second PR: after this change the per-step profile is flat (windowed
`find_contours`, scipy's max filter, xarray `DataArray` churn per peak) — nothing
else is big enough to be worth a PR, and `calc.haversine_distance`'s re-evaluated
`np.cos` is now noise next to the mask win.

**Open issues / needs the owner or orchestrator**

1. **Scoring**: `midlat_cyclone` is published and drawn, never scored (EWB has no
   metric set for a pressure-minimum track). The pipeline documents each pair as
   `unsupported` so `pair_coverage` / `full_run_complete` stay decidable — same
   policy the `tc` lane needs for `tropical_cyclone`. Confirm, or tell me to score
   it against something.
2. **Merge conflicts with the `tc` lane** in `select.py`, `pipeline.py`,
   `events/__init__.py`, `events/validate.py`, `events/qualification.py`,
   `dashboard/app.js`. Every edit is additive and commented `mlc lane`; the
   `MLC_TRACK_TYPE` / `MLC_TOP_N` names are chosen so folding them into a shared
   `TRACK_TYPES` after the merge is a one-line change. I did not touch `tc.py` or
   `ibtracs.py`, but `mlc.py` imports the `tc` lane's IBTrACS reader **lazily and
   fail-soft** for the tropical-origin screen — if that lane's reader is renamed,
   the screen silently stays off (the event still qualifies on its other four
   screens), so please keep the import path in mind at merge time.
3. **The screen set is a knob, not a law.** 990 hPa depth floor and the 25–70°
   genesis band are `QualifyParams` fields. Nov 2023 gives 71 events / 90 days; the
   `select.py` quota is 30 per window, so a busy window truncates by severity
   (depth × lifetime × bomb) rather than failing.
4. **ARCO reachability is a hard dependency** for the scan (the lane streams; the
   mirror has no `mean_sea_level_pressure`). Offline, `scan_mlc_events` returns `[]`
   and the lane simply publishes nothing.
5. **Intensity is pressure only** (no wind column in `details["track"]`), as the
   5-slot schema says. If the owner wants a wind series, that is a schema change
   first, not a tracker change.

## [mlc-impl] 2026-11-17 13:45 UTC — two real bugs found by validating, both in the orography source

Wiring `scan_mlc_events(fields=...)` into the harness forced the question "is the
genesis elevation screen actually live?" It was not, twice over:

1. **ARCO's time axis opens with a `1900-01-01T00` pseudo step that is all-NaN for
   `geopotential_at_surface`** (the record itself starts 1979-01-01). My
   `load_orography()` defaulted to time index 0 → `nan_to_num` → an all-zero field
   → `elevation_at` answers 0 m everywhere → the screen that exists to stop the
   tracker inventing lows off the Tibetan Plateau / Andes / Rockies was silently
   **off**, and the poisoned 7 KB all-zero npz was cached as if it were the field.
   `fetch_topo.py` (mlc-research) picked a real date explicitly, so the PROOF's
   orography numbers are fine — the bug was mine, introduced when I moved the read
   into the lane.
   Now: probe `2000-01-01 / 1979-06-01 / 2015-01-01`, **reject a field with no land
   on it**, and treat a cached all-zero field as a miss and refetch. Verified on the
   box: max 5859 m, 17.5 % of cells above 500 m, Everest 5340 m, North Atlantic 0 m.
2. **`np.load` on a warm cache raised `AttributeError: 'NoneType' object has no
   attribute 'open'`.** An `NpzFile` is lazy; I read `elev` inside the `with`
   block but `z["lats"] / z["lons"]` after it closed. The exception escapes
   `load_orography` and lands in `scan_mlc_events`' ERA5 `try`, which logs
   "read failed — no events" → **the lane would have published nothing on every run
   after the first**. Fixed by reading all three arrays inside the `with`.

Effect on the validated numbers: 138 tracks → **71 events** (was 77 with the screen
inert), 13 bombs; the screen removes ~8 % of genesis points, i.e. exactly the
orography false positives the PROOF measured. Everything else is unchanged (nodes
3199, tracks 138 vs TE 145, 133/145 matched, median position error 0.00 deg, Ciaran
identical). Three new tests in `tests/test_mlc_track.py` pin all three behaviours
offline with a fake store (18 mlc tests pass; whole repo 315 / 29 / 0 with
`EWB_DATA_PREP_DIR` set).

**Memory note for ops**: reading that one static timestep costs **~17 GB of RSS for
~2 s** on the ewb box (measured through xarray *and* by indexing the zarr array
directly — it is the store reader, not my code; latitude-strip reads are 150× slower
for the same peak, so they are not the answer). It is a **one-time** cost: the field
is cached as a ~4 MB npz and every later scan reads that. Pre-warm a fresh box with

```bash
cd /opt/ewb-live && .venv/bin/python -c \
  'from ewb_live.sources import mlc_era5; print(mlc_era5.load_orography()[0].max())'
```

so the 17 GB spike never lands next to a scoring run. Validation peak RSS 18.9 GB
total, of which the 144-frame MSLP field is 0.6 GB.

## [integrator] 2026-11-17 21:35 UTC — all 8 lanes merged on aaTman/ne-integration, suite green

Merged in order, one commit each, full suite after every merge (`EWB_DATA_PREP_DIR=$PWD/third_party/ExtremeWeatherBench/data_prep .venv/bin/python -m pytest tests -q`; third_party is gitignored — clone ExtremeWeatherBench there or those 2 upstream-rule tests fail with FileNotFoundError, as mlc-impl already documented):

| merge | commit | suite after |
|---|---|---|
| ne-ar-debug | 38768de | 304 passed |
| ne-subsets (MLWP virtual-store-only — **dev only**) | e4fd539 | 311 |
| ne-coldsnap (incl. the owner `<=0C` freeze patch, deduped) | 1816b34 | 323 |
| ne-tc | 32c4b9b | 371 |
| ne-flood-detect (⊃ ne-flood-clim, ne-flood-imerg — verified ancestors, so one merge covered three) | 8841183 | 456 |
| ne-mlc-impl (⊃ ne-mlc-research) | 6555f32 | **474 passed, 29 skipped** |

`aaTman/ne-integration` is now the eight-lane tree: heat_wave, freeze, cold_snap,
atmospheric_river, tropical_cyclone, midlat_cyclone, flood (+ the freeze `<=0C` rule).

### Shared registrations, as unified (the merge-conflict decisions)

* `events/types.py EVENT_TYPES` = the seven types, in that order; every
  registration derives from it where possible (pipeline `meta.event_counts` now
  iterates `EVENT_TYPES` instead of a hardcoded tuple — that tuple conflicted in
  three merges).
* `events/select.py` is the single place the lane policies live:
  * `TRACK_TYPES = {"tropical_cyclone", "midlat_cyclone"}` — one storm / one low
    = one event, never merged, outside the owner's no-overlap rule (mlc's own
    `MLC_TRACK_TYPE` folded into it, as its comment requested).
  * **new** `SELF_SOURCE_TYPES = {"tropical_cyclone", "flood"}` — lanes that read
    their own source (IBTrACS / IMERG) and must NOT be trimmed by the ERA5 truth
    cutoff. Split from UNSCORED_TYPES because mlc is an *unscored* lane that IS
    ERA5-based; reusing UNSCORED_TYPES for the clamp would have asked ARCO for
    days it does not have.
  * `UNSCORED_TYPES = TRACK_TYPES | {"flood"}`; `TOP_N_BY_TYPE` = TC 60, flood 40,
    mlc 80.
* `pipeline.py`: one `unscored_obs` bucket + a per-type `unsupported`
  `pair_status` reason (was: two parallel track/observation blocks, and mlc's own
  post-hoc filter).
* `dashboard/app.js`: `TYPE_META` carries all seven with colours; unscored lanes
  carry a per-type `noScoresNote` (flood had no `noScores` flag and would have
  rendered a dash table); `TYPE_ORDER` = heat, freeze, cold snap, AR, TC, MLC, flood.
* `mergedmap.js`: `TYPES`/`LANE_ORDER` include flood (boxes + Gantt row); TC and
  MLC stay out of the Gantt on purpose (tracks, drawn in the detail view).
  `layersForTab` — flood had been mapped to a raw layer key `'l'` that does not
  exist, which switched **every** raster off when the Flood tab was clicked; the
  observation lanes now keep the user's layer choice (tc's behaviour).
* `events/__init__.py`: dispatch covers all lanes; per-type cache-key tags coexist.

### Bugs found while integrating (fixed here, not upstream-branch problems I left alone)

1. **flood had no colour**: `--series-flood` was *referenced* in `styles.css`
   (`--flood: var(--series-flood)`) and in `app.js`, never defined → the lane
   would have rendered in the `--series-unknown` grey. Defined berry `#a01e56`
   light (7.75:1 paper / 6.73:1 light ocean) and `#e88bbb` dark (6.37 / 7.66).
2. **the two cyclone lanes were the same colour**: mlc violet `#5f3d99` is hue
   262, tc violet `#6f42c1` is hue 261 — indistinguishable in a seven-lane UI.
   Re-hued midlat_cyclone to amber `#7d5200` / `#d9a441` (contrast above).
   Two lines in `styles.css`, commented — trivially reversible if the design
   pass wants cyclones in one family.
3. **subsets guard vs the IMERG store**: `tests/test_mlwp_store_guard.py`
   forbids `.to_netcdf(` anywhere in `ewb_live/sources/*`; `sources/imerg.py`
   legitimately writes its own packed-uint16 daily store. Exempted *by name*
   with the reason; the MLWP-bucket guard still scans every file.

### Now / next (Step 3-4)

Dev site per the pattern in my kickoff entry: `/opt/ewb-live-dev` tree + own
`data/results`, served on **:8051**, live `/opt/ewb-live` untouched. Next:
rsync this tree there, symlink the heavy read-only stores, pull IBTrACS, then run
the combined lane passes (freeze `<=0C`, cold_snap, AR, TC, mlc — MSLP streams
from ARCO, flood from the IMERG store) into the dev results dir, then e2e +
screenshots. **Reminder for whoever promotes: ne-subsets (virtual-store-only
scoring, subsets cache removal) is DEV ONLY per the owner's update — promoting
means merging the lane branches and NOT that commit.**

— integrator

## [integrator] 2026-11-17 21:47 UTC — /dev/ site is UP: http://192.168.0.121:8051/ (data still filling in)

Standing it up per the kickoff plan, with `ops/dev_site.sh` (committed) so it is
reproducible rather than a pile of one-off ssh commands:

* tree `/opt/ewb-live-dev` = rsync of `aaTman/ne-integration` code (no `.git`,
  no `data`, no `.venv`); `.venv` + `third_party` symlinked to `/opt/ewb-live`.
* read-only heavy inputs symlinked: `data/{clim,imerg,icechunk}` and
  `data/cache/{truth,fc_parts,mlwp}` → the live ones (nothing re-downloaded).
* **everything the run writes is a real dir inside the dev tree**: `data/results`
  (seeded by a one-way copy FROM live, so the scorecard/history/per-event viz of
  the released lanes are already there) and `data/cache/{events,ibtracs,viz,
  ewb_mid,score_parts}`. Live `/opt/ewb-live` is only ever a *read* target; no
  live unit was touched. Dev serves via its own unit `ewb-live-dev-web.service`
  (`ewb_live.serve --port 8051 --host 0.0.0.0`), currently `active`, HTTP 200.
* lane build running (`ops/dev_site.sh lanes`, log `/opt/ewb-live-dev/logs/lanes.log`):
  `pipeline --events-only --days 90 --top-n 15` → `viz --all --force
  --types heat_wave,freeze,cold_snap,atmospheric_river` → `pipeline
  --publish-only --days 90` (no new scoring — the copied live score rows are
  re-published, which also writes `meta.json` with the seven-type `event_counts`
  and the `ibtracs` freshness record the honest-zero UI needs).
  Progress: heat/freeze/cold_snap done (cold_snap **22** valid events), AR scan
  chunking through the window (~40 s per 4-day chunk), then TC / MLC / flood.
* Already reviewable **now**: all seven tabs render with honest empties
  ("No midlatitude cyclones met the criteria this window", "Tropical cyclones are
  not published in this data release yet — the IBTrACS lane goes live with a
  future weekly build"), the About dialog carries all seven lane descriptions,
  and 30 released events are listed. Verified with a Playwright pass
  (`scripts/dev_review_shots.py`, committed): **no console errors**, desktop +
  phone, light + dark.
* Small follow-up while the lanes ran: `viz/timeline.py TYPES` now includes
  `flood`, so the Flood Gantt lane under the merged map gets bars (bbox-only
  entries — flood has no v2 raster/viz footprint) instead of an empty lane for a
  tab that has events.

`ops/dev_site.sh status` prints the per-type selected/raw counts, meta counts,
viz count and disk — that is the scoreboard for the rest of Step 3.

— integrator

## [integrator] 2026-11-17 22:55 UTC — /dev/ data build: per-type counts, one lane-bug blocker (midlat_cyclone), promotion notes

**Review site: http://192.168.0.121:8051/** (LAN; `ewb-live-dev-web.service`, tree
`/opt/ewb-live-dev`). Window 2026-08-19..2026-11-17 (90 d). The lane build
(`ops/dev_site.sh lanes`) finished detection; the per-event viz pass (39 raster
events) and the final `--publish-only` (which rewrites `meta.json`) are still
running as I write — read `bash ops/dev_site.sh status` for the live view.

### Counts (dev, this window) — `events.json` (raw catalogue) → `events_selected.json` (published)

| lane | raw | published | note |
|---|---|---|---|
| heat_wave | 142 | **15** | capped at the global 15 |
| freeze | 11 | **5** | `<=0C`-at-all-four-steps rule in force |
| cold_snap | 22 | **6** | the freeze-subsumption rule is visible: 22 raw − freeze overlap → 6 |
| atmospheric_river | 95 | **13** | |
| tropical_cyclone | 44 | **44** | 49 storms → 44 events (5 under the 3-fix/34 kt floor); the whole season is published, not capped at 15 |
| midlat_cyclone | **0** | **0** | **lane defect, see below — this zero is NOT trustworthy** |
| flood | 766 | **40** | quota 40; 766 matches the flood lane's own live 90-day run exactly |

Scorecard/score rows are the live ones re-published (`26,409` rows) — no new
scoring was run, per the brief.

### BLOCKER for the midlat_cyclone lane (needs mlc-impl, not an integration artifact)

`detect_events midlat_cyclone: scanned 0 events` over the 90-day window (361
frames, 272 nodes → 3 tracks → 0 after screens), although mlc-impl's PROOF window
published 71. Reproduced on the dev tree with real ARCO MSLP:

* one frame (2026-10-20 00Z) has **190,414 cells below the 1010 hPa ceiling inside
  the 25–70° band** and a band minimum of 967 hPa, yet
  `find_peaks_for_time_slice()` returns **0 nodes**;
* `_degrees_to_gridpoints(6°)` = 24 cells on the 0.25° grid (correct), so the
  peak-separation stage is fine;
* relaxing only the **closed-contour** parameters on that same frame gives
  `delta_hPa=0.5 → 5 nodes`, `max_gc_distance_contour_degrees=20 → 7`,
  `0.5 + 30° → 27`. i.e. **100 % of candidate minima are rejected by the
  `centre + 3 hPa closes within 6°` check on real ARCO MSLP** — a shape that says
  the contour-distance comparison is mis-scaled (cells vs degrees, or missing the
  1/cos(lat) widening in longitude), not that Oct 2026 had no lows. Their
  validation vs TempestExtremes ran on netCDF extracts via a different reader
  path, so this ARCO path looks untested at full resolution.
* Second, UI-side consequence (owner-visible): `scan_mlc_events` **fails soft to
  `[]`**, and the honest-zero mechanism exists only for the TC lane
  (`meta.ibtracs`). So a failed/defective MLC lane reads in the UI as "No
  midlatitude cyclones met the criteria this window" — which is a lie today.
  Recommend generalising to a per-lane `meta.lane_status` (count + ran/failed +
  source reach) rather than per-lane one-offs; I did not invent that mechanism
  mid-review.

I did **not** retune mlc's detection parameters — that is their science, and a
silent retune in an integration branch is worse than an empty tab with a
diagnosis.

### What promoting to live involves (NOT done, per the brief)

1. `aaTman/ne-integration` → main is a normal merge **except** commit `e4fd539`
   (`ne-subsets`: MLWP virtual-store-only scoring + subsets-cache removal), which
   the owner marked DEV-ONLY. Promoting the lanes without it means merging the
   lane merges individually (ar-debug, coldsnap, tc, flood-detect, mlc-impl) and
   leaving `e4fd539` out — or reverting it on main after the merge. Decide before
   merging; it rewrites `ewb_live/sources/*`, `scoring/`, `weekly/`.
2. Live deploy is the usual `ops/remote.sh sync` (code only; the box owns
   `data/`) + one full live cycle `python -m ewb_live.weekly --days 90` (or the
   `ops/events_rebuild.sh` sequence) because freeze/cold_snap/flood/TC/MLC all
   need scoring, raw frames, per-event viz and region scorecards built for the
   new lanes — the dev tree deliberately did none of that scoring.
3. Two shared-file changes in this branch are opinionated and should be looked at
   by the owner/design before they go live: the midlat_cyclone colour (amber,
   because the branch's violet was indistinguishable from the TC violet) and
   `app.js`'s honest-count rule ("no score rows for this lane in this build →
   count published events in region view, and always say why there is no table").
4. `tests/e2e` against the dev site: **37 failed / 106 passed**; a live-site
   baseline is running to separate pre-existing failures (pi-colors' channel
   entry already documents legend/`#raw-layers` e2e tests that "fail by design")
   from merge-caused ones — check `/tmp/e2e_live.log` vs the dev run before
   reading those 37 as regressions.

### Ops notes for whoever picks this up

* `bash ops/dev_site.sh status` = scoreboard; `... watch` = lane log; the tree is
  idempotent (re-running `init`/`web` is safe; `sync` is code-only).
* After a code re-sync, rebuild the two derived assets in the dev tree:
  `.venv/bin/python -m ewb_live.viz.timeline` and
  `.venv/bin/python -m ewb_live.pipeline --publish-only --days 90`.
* Only **16 of 30** released events had `events/<id>/viz.json` in the live
  results copy (same on live, verified) — the dev `viz --all --force` pass is
  what fills that; expect the dev site to have MORE event viz than live.
* Handoff doc for a successor: `notes/handoff_integrator_1.md`.

— integrator
