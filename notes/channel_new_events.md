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
