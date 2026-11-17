# MLWP virtual-store audit (role drop-subsets, 2026-11-17)

Owner ruling: **all CIRA MLWP forecast evaluation goes through the Icechunk
virtual store** (`data/icechunk/cira_mlwp`, refs-only virtualisation of the
NetCDF4 objects in the CIRA/NOAA MLWP archive; the store never copies data
bytes — updating it appends virtual references + small real coord chunks).
Any code that downloaded/materialised archive data for evaluation is
removed; every consumer now opens through the single choke point
`ewb_live.sources.aiwp.open_mlwp_forecast`. Enforcement:
`tests/test_mlwp_store_guard.py` (static scans + functional probes).

## Phase 1 — deletion (done, this role)

`/opt/ewb-live/data/cache/subsets` (231 G, all 11 models) deleted on the ewb
LXC with no scoring/weekly/pipeline job running (checked `ps` + timers; web
server and the separate `/opt/ewb-dashboard` refresh never touch it).
df before: 466 G used / 5.4 G avail (99%) → after: 235 G used / 237 G
avail (50%). `cache/truth`, `cache/fc_parts`, `data/clim`, `data/icechunk`
untouched. `fc_parts` (41 G) verified to hold ONLY `ifs_hres`/`aifs_single`
keys — an ECMWF-derived read cache, independent of the removed MLWP subsets
tree, so it is kept (see §fc_parts).

## Inventory — every path touching CIRA MLWP forecast data

Legend: **OK** reads via the virtual store · **REMOVED** downloaded /
materialised archive data · **CHANGED** consumed downloaded data ·
**BUILDER** store build/maintenance (sanctioned direct-bucket access).

| Path | Was | Now |
|---|---|---|
| `ewb_live/sources/api.py` `open_forecast*` | materialised every read to `data/cache/subsets/<model>/<init>/<hash>/ds.zarr` (THE 231 G writer, all models) | **CHANGED**: in-process bounded LRU (`EWB_LIVE_MEM_CACHE_MB`, default 512 MB); no disk materialisation anywhere |
| `ewb_live/sources/aiwp.py` `_open_via_store` | primary = store | **OK** — became the single choke point `open_mlwp_forecast()`; bounded semaphore (default 2 sessions, `EWB_LIVE_ICECHUNK_MAX_SESSIONS`; notes/ewb.md: ≥3 concurrent icechunk sessions hang) |
| `ewb_live/sources/aiwp.py` `_open_direct` | lazy HDF5 range reads of archive objects via fsspec (download path, also the fallback that could re-fill caches) | **REMOVED**. Store miss/absent now raises `FileNotFoundError` (keeps `open_forecast_nearest_init` earlier-init fallback + `fc_cache`/`evaluate` error handling intact); other store failures → `RuntimeError` |
| `aiwp.available_inits` | per-day bucket LIST via `_net.list_prefix(AIWP_BUCKET)` | **CHANGED**: store `list_inits` authoritative; tail-after-store listed through `stores.mlwp_common.day_files` (store-builder LIST layer, filenames only, never data) |
| `ewb_live/sources/cache.py` (`prune_cache`, `maybe_prune_daily`, `python -m ewb_live.sources.cache`) | evicted subset entries >120 d | **REMOVED** (inert shims with old signatures; CLI prints retirement notice) |
| `ewb_live/sources/_net.py` | `AIWP_BUCKET` constant; `open_remote_nc` used by the MLWP direct path | constant **REMOVED**; `open_remote_nc` kept as a generic lazy-range helper with no MLWP use (stall-fix regression test pins its config) |
| `ewb_live/scoring/fc_cache.py` | passthrough to `sources.open_forecast` for MLWP models; disk-caches ECMWF only (`CACHE_MODELS=("ifs_hres","aifs_single")`) | **OK** unchanged; MLWP calls now land on the store via api/aiwp. Its cache = fc_parts, ECMWF-only (see below) |
| `ewb_live/scoring/evaluate.py` `build_forecast` → `fc_cache.open_forecast_cached` | consumed whatever api delivered (subsets-backed) | **CHANGED** (transitively): MLWP models now read store→memory; comments about "AR subsets" are prose only |
| `ewb_live/scoring/inits_cache.py` | cached day coverage of the archive bucket in `data/cache/aiwp_inits.json` | **OK** (filename-level list cache, no data bytes); docstring updated; keys are store/tail listings |
| `ewb_live/weekly.py` `update_mlwp_icechunk` + step 1 | runs `python -m ewb_live.stores.mlwp_icechunk update` (flocked, appends refs only) | **BUILDER OK** — this is the store update, not an evaluation download; weekly no longer has anything that materialises forecasts |
| `ewb_live/pipeline.py` | scores via evaluate/fc_cache | **CHANGED** transitively (reads via store). No subset references |
| `ewb_live/viz/build.py` `model_daily_pass`, IVT tiles (`open_forecast_nearest_init`) | api-backed (subsets write-through) | **CHANGED** transitively; still api-only (viz/__init__ docstring promise "no direct GCS/icechunk here" holds) |
| `ewb_live/scoring/truth.py` | IFS-HRES 0-h analysis fallback + ERA5 mirror (ARCO/GCS) | **OUT OF SCOPE** — non-MLWP truth sources, unchanged |
| `ewb_live/sources/ecmwf.py`, `aigfs.py` | GRIB byte-range reads of `ecmwf-forecasts` (CloudFront) / `noaa-nws-graphcastgfs-pds` | **OUT OF SCOPE** — non-MLWP, unchanged (they no longer write subsets because the api write-through is gone) |
| `ewb_live/stores/mlwp_common.py`, `mlwp_icechunk.py` | builder: LIST + virtual-ref registration + maintenance | **BUILDER OK** — the only sanctioned direct-bucket code in the package; virtualises refs, does NOT copy forecast bytes; `build`/`update` keep only manifests/coords locally |
| `ops/probe_fcn_subset.py` | debug probe reading `data/cache/subsets/fourcastnet_v2_gfs/...` | **REMOVED** (deleted) |
| `ops/probe_fcn_levels.py`, `probe_fcn_gfs_levels.py`, `probe_fcn_dims.py`, `probe_fcn_trace.py` | model probes | OK: read via `sources.open_forecast` / `mlwp_icechunk.open_init` (store), no materialisation |
| `scripts/mlwp_survey.py`, `mlwp_archive_survey.py`, `mlwp_survey_report.py` | bucket inventory JSON (`data/cache/mlwp/inventory_*.json`, filenames only) | **BUILDER/QA OK** — listing inventory for the store builder, no data bytes |
| `scripts/mlwp_verify_store.py`, `mlwp_probe_file.py` | store-vs-direct QA and single-file probes (open raw HDF5 for reference) | **BUILDER/QA OK** — store build-time verification tools under `scripts/`, outside the guarded package; flagged, kept |
| `tests/*` | unit/network tests | updated (see Enforcement); `test_mlwp_icechunk.py` fixtures use sample URLs (tests are outside the guard scope) |
| `dashboard/`, `serve.py` | serve prebuilt JSON/viz assets | OK — no forecast reads |
| docs | `notes/sources.md` (materialise + prune statements), `notes/ewb.md` prose | updated (sources.md); `SPEC.md` never named the subset tree (only "cache under `data/cache/`", still true for truth/fc_parts/etc.) |

## What each model reads now (2026-11-17)

* `graphcast`, `graphcast_gfs`, `pangu_weather`, `pangu_weather_gfs`,
  `fourcastnet_v2`, `fourcastnet_v2_gfs`, `aurora`, `aurora_gfs` (8 MLWP):
  Icechunk virtual store at `EWB_MLWP_REPO` (live:
  `/opt/ewb-live/data/icechunk/cira_mlwp`), lazy refs, bbox+lead+level cut
  in memory, bounded 2 concurrent sessions.
* `ifs_hres`, `aifs_single`: ECMWF open-data GRIB byte ranges (CloudFront)
  — unchanged; scoring keeps reusing `data/cache/fc_parts` (kept).
* `aigfs`: NOAA AIGFS GRIB bucket — unchanged.
* ERA5 truth (ARCO/GCS + local mirror), clim: unchanged (non-MLWP).

All 11 models previously wrote `data/cache/subsets` (per-model 16–29 G
each); MLWP models now read it via the store; ECMWF/AIGFS models simply
re-read their sources (api-level repeat calls served by the in-process LRU;
scoring-level ECMWF repeat reads by fc_parts).

## fc_parts verdict (kept)

Keys are `{ifs_hres|aifs_single}_<init>_<vars>_<region>_<leads>.zarr` —
1772 + 1724 entries, zero MLWP models, contents are derived ECMWF fetches
written by `fc_cache` from its own `sources.open_forecast` calls, not from
the subsets tree. Keys/validity do not depend on the removed layout:
nothing to invalidate. (41 G, owner may still prune it; deletion was not
required by this ruling and is not done.)

## Enforcement (tests/test_mlwp_store_guard.py)

Static scans over `ewb_live/**/*.py` fail if any of reappears:
1. the archive-bucket token, `AIWP_BUCKET`, `cache/subsets`,
   `cache_path("subsets"…)`, `_SUBSET_ROOT` outside the two store-builder
   modules;
2. `.to_zarr(`/`.to_netcdf(` anywhere in `ewb_live/sources/` (the sources
   layer may not materialise);
3. `open_init(`/`open_model(` calls outside `stores/` + `sources/aiwp.py`
   (the choke point).
Functional: `open_forecast` for an MLWP model is served by the (fake)
store, memoised in-process, and writes NOTHING under the cache root (no
`subsets` dir may exist); store miss → `FileNotFoundError`; store absent →
loud `FileNotFoundError`; LRU evicts by bytes; choke point exists and
caps concurrent sessions ≤ 2. Network tests skip MLWP when the store is
absent locally.

## Behaviour changes / residual risks

* **Store freshness is now load-bearing for MLWP reads**: inits published
  after the last store update (daily 05:30 UTC timer + weekly catch-up)
  fail `FileNotFoundError` until the next update; `open_forecast_nearest_
  init` silently shifts to an earlier published init (with the agreed
  lead-offset convention), so the freshest inits can score slightly older
  data for <24 h. Listing still reports them (tail LIST), matching is what
  shifts.
* Store absent (dev laptops): MLWP listing works, reads fail loud — the
  direct fallback is gone BY DESIGN.
* The in-process LRU is per-process; scoring pool spawn workers each pay
  first-read cost (as with the old cold subsets cache), and shares one
  512 MB budget by default.
* ≥3 concurrent icechunk sessions hang (notes/ewb.md): bounded to 2 per
  PROCESS; multi-process pools multiply (4 spawn workers × 2 = 8 host
  sessions) — same posture as the notes' recommendation; measured below.
