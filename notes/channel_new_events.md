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
