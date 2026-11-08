# CIRA MLWP Icechunk store — design notes and decisions (agent: mlwp-store)

Date: 2026-11-06. Store: `data/icechunk/cira_mlwp/` (Icechunk repo, local filesystem
storage). Source: `s3://noaa-oar-mlwp-data` (anonymous S3, NetCDF4/HDF5 objects).
No data is copied: every data variable is a Zarr v3 array whose chunks are Icechunk
virtual references (byte ranges) into the original S3 objects.

## 1. Library versions and features used

Installed with `uv add` into the shared project venv (Python 3.12):

| package | version |
|---|---|
| virtualizarr | 2.8.0 |
| icechunk | 2.3.1 |
| zarr (python) | 3.4.2 |
| xarray | 2025.11.x |
| obstore | (virtualizarr dep) |

Features relied on:

* **Virtual chunk containers with container-scoped credentials** (`RepositoryConfig
  .set_virtual_chunk_container` + `Repository.create(..., authorize_virtual_chunk_access
  = {...})`). The whole bucket is registered as one trusted container with an anonymous
  obstore S3 store; each session passes `s3_credentials(anonymous=True)` scoped to the
  container prefix. Virtual refs therefore store only `path + offset + length`, not
  URLs with credentials, and reads are authorised per container.
* **Manifest splitting along `init_time`** (`ManifestConfig(splitting=
  ManifestSplittingConfig.from_dict({...}))`). Without it, a model group with ~240 inits
  would live in one manifest holding ~1M refs, so every weekly append would rewrite that
  manifest. With splitting (3-D vars every 20 inits, 2-D vars every 96 inits) an append
  only rewrites the last, small manifests. Split sizes keep every manifest above
  ~1000 refs, the point at which Icechunk trains its URL compression dictionary.
* **Append semantics in `to_icechunk`** (`mode="a"` + `append_dim="init_time"`, and
  `mode="w"` for in-place group rewrite) so updates are incremental and cost-proportional
  to the new files.
* **Per-write commit** on a branch (`main`), giving point-in-time reads and cheap
  rollback; `Repository.list_manifest_files` / `session.status` used for inspection.

### Rejected: Zarr v3 rectilinear / variable chunk grids

The brief asked to prefer rectilinear (variable-size) chunk grids so one array could
cover layouts that change over time. Checked against the installed versions:
zarr-python 3.4.2 still has only `RegularChunkGrid` and
`ChunkKeyEncoding`/`RegularChunkKeyEncoding` in the v3 metadata model — variable-length
("rectilinear") chunking is a *proposed* Zarr v3.2 feature and is not implemented in
zarr-python, Icechunk or VirtualiZarr, and virtual references fundamentally require each
store chunk to map 1:1 onto one contiguous source byte range (one HDF5 chunk). Store
chunking therefore has to equal source chunking, which cannot vary inside one array.
Era groups (below) are the mechanism that works today.

## 2. Bucket survey

Method: `scripts/mlwp_survey.py` (VirtualiZarr `HDFParser` over obstore, 8 processes)
scanned every object of every model directory in the 120-day window, plus a sample of
19 files per model per year back to 2023. Raw records:
`data/cache/mlwp/survey_vz_120d.jsonl` (1611 records), `survey_vz_hist.jsonl` (412).
Report: `scripts/mlwp_survey_report.py`, output in `notes/mlwp_survey_120d.md`.

### Object layout (verified against the bucket)

```
s3://noaa-oar-mlwp-data/<CODE>_v<V>_<FORCING>/YYYY/mmdd/<CODE>_v<V>_<FORCING>_YYYYMMDDHH_f000_f240_06.nc
```
One file per model+init, leads 0..240 h step 6 (41 steps), NetCDF4/HDF5. Also present
but not model data: `Derived/`, `parquet/`, `colab_resources/`, `README.txt`,
`index.html`.

### Models and coverage (window 2026-07-09 .. 2026-11-05)

8 active model directories: `AURO_v100_GFS`, `AURO_v100_IFS`, `FOUR_v200_GFS`,
`FOUR_v200_IFS`, `GRAP_v100_GFS`, `GRAP_v100_IFS`, `PANG_v100_GFS`, `PANG_v100_IFS`.
Current cadence is **2 inits/day (00Z, 12Z)**; the historical archive used 4/day
(00/06/12/18) up to 2025. GFS-forced models have upstream gaps (no data on 07-16,
07-20, 07-25, 08-01, 08-07, 09-20, 09-26, 10-08, 10-16 in this window). Newest file
observed is 2026-11-05 12Z at survey time (00Z of the current day is typically not yet
published).

### Variables, dims, dtypes (stable across the window)

Grid 0.25 deg: latitude 721 (90 .. -90, descending), longitude 1440 (0 .. 359.75),
`level` = 13 int32 hPa values [1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100,
50] (+ 70 hPa in some). All variables float32, `_FillValue` 9.96921e36, CF-1.8 attrs.

| family | 3-D (time, level, lat, lon) | 2-D (time, lat, lon) |
|---|---|---|
| AURO | z q t u v | t2, u10, v10, msl |
| FOUR_v200 | u v z t r | u10 v10 u100 v100 t2 sp msl tcwv |
| GRAP | q t z v w u | t2 u10 v10 msl apcp |
| PANG | u v z t q | t2 u10 v10 sp msl tcwv |

(Exact per-model lists with codecs and ref counts: `notes/mlwp_survey_120d.md`.)

### How the metadata changes over time

Sampled 2023-01-15 through 2026-11-05 (412 historical records, 0 scan errors, plus the
1598 good records in the live window):

* **One single chunking era change in 3.5 years, and it is shared by every live model.**
  From `version` `2_2024-12-16` to `3_2025-02-20` (files dated 2025-05-26 -> 2025-05-28),
  the 3-D variables' HDF5 chunk shape changed from `(1, 13, 721, 1440)` (one chunk = one
  lead, all 13 levels) to `(1, 1, 721, 1440)` (one chunk = one lead, one level). 2-D
  variables stay `(1, 721, 1440)` on both sides. Consequence: virtual references go from
  41 per 3-D variable per file to 533, so one Zarr array cannot span the boundary and the
  store's era groups exist for exactly this. Upstream EWB dodges it by stopping its store
  at 2025-05-27.
* **Variable sets never changed.** For all eight live models the set of data variables is
  identical at 2023-01-15 and today: version bumps (`1_2023-10-14` -> `2_2024-12-16` ->
  `3_2025-02-20`) changed chunking and metadata, not the variable list. So a long
  `--days` build needs era groups for chunking only, never for schema.
* **Model archive start dates differ.** `AURO_v100_*` only exist from 2025-04-01; the
  other six live models run from 2023-01-15. A long window build therefore yields fewer
  inits for AURO, not missing data.
* **Retired `FOUR_v100_GFS`** (2023-01-15 .. 2023-10-01, `version` `1_2023-10-14`) is
  structurally different on every axis: 720 lat rows (no pole), variables on
  `zlevel`/`windlevel`/`rhlevel`/`tlevel` dims instead of `level`, and coarse chunks
  `(14, 240, 480)` / `(14, 1, 240, 480)`. It is representable here (the store keeps the
  source chunk grid) but sits outside the live window, so it is not built.
* **Cadence** dropped from 4 inits/day (00/06/12/18) historically to 2/day (00Z, 12Z) for
  the current archive.
* **Ref counts per file** changed with the era boundary: a 3-D variable has 41 refs before
  it, 533 after.
* **13 corrupt objects inside the window** (all IFS-forced 00Z inits):
  AURO_v100_IFS 0919/0921/0926/0929/1011/1016/1017, FOUR_v200_IFS 0919/0921,
  GRAP_v100_IFS 0919/1018, PANG_v100_IFS 0919/0926. They are truncated upstream
  (72 MB vs 2.7 GB for a healthy sibling) and fail with "incorrect metadata checksum"
  or "wrong B-tree signature" **including through a direct `xr.open_dataset` read**, so
  the existing `sources/aiwp.py` path breaks on them too. The builder skips them and
  reports them per run; the store simply lacks those `init_time` values.

## 3. Store design

* **Repository**: local Icechunk store at `data/icechunk/cira_mlwp/`, branch `main`,
  one commit per build/update run. VCC + anonymous container credentials as above.
* **Groups**: one per model, named exactly like the bucket directory and EWB's
  `CIRA_MODEL_NAMES` values (`GRAP_v100_IFS`, ...). Era handling: the plain group holds
  the first layout era; if source chunking changes mid-window a new group
  `<model>__YYYYMMDD` is created for the newer era. `open_model()` returns a single
  logical view by lazily concatenating the era groups along `init_time`, so readers never
  see the split. Group attrs record `layout_signature`, `era_start`, `era_end`,
  `built_with`.
* **Dims/coords**: `(init_time, lead_time, level?, latitude, longitude)`. The file's
  `time` (valid times) is converted to `lead_time` `timedelta64[s]` (0..240 h step 6)
  which becomes the dimension; `init_time` is a real (non-virtual) `datetime64` coord,
  one value per source file; a correct 2-D `valid_time (init_time, lead_time)` coord is
  stored as small real chunks because a 1-D `valid_time` would be wrong after
  concatenation. `latitude`/`longitude`/`level` are real inline coords (native values,
  lat descending, lon 0..359.75).
* **Chunks**: store chunks equal the source HDF5 chunk shapes ((1,1,721,1440) /
  (1,721,1440) in the current era), which is what makes reads cheap: one requested grid
  cell range fetches exactly the needed S3 byte ranges.
* **Coords written as real data** cost nothing (a few KB per group) and make subset
  reads free of S3 round-trips.

## 4. Incremental update semantics (`ewb_live/stores/mlwp_icechunk.py`)

Per run, per model, the planner picks one of three kinds:

1. `build` — model absent from the store: write all window files (`mode="w-"`).
2. `append` — every missing init is newer than the stored maximum:
   `to_icechunk(mode="a", append_dim="init_time")`.
3. `rewrite` — missing inits fall inside the stored range (upstream backfill or a gap in
   our own run): rewrite the era group in place with the whole window (`mode="w"`),
   because appending would put inits out of order.

Idempotent: a run with nothing to add makes no commit (`summary["commit"] is None`,
changes discarded). All models in one run share one session, so a run produces exactly
one commit. Files that fail to parse are recorded in `summary["skipped"]` and never abort
the run. Reference generation is parallel across 8 processes
(`ProcessPoolExecutor`, per-model batches to bound memory).

Operational properties:
* `build` stages at `<repo>.tmp` and moves the finished repo into place with one
  `os.replace` after the commit, so consumers never observe a half-built root group
  (a full build takes ~30 min; sources' fallback absorbed a `GroupNotFoundError` during
  the first, unstaged build — that window is now closed).
* `update` needs no staging: Icechunk commits are snapshot-atomic, so readers keep
  seeing the previous snapshot until the new commit lands.
* `update` self-heals: if the repo path is absent (fresh checkout), it runs a full
  staged build and marks `summary["self_healed_build"] = True`, so the weekly cycle
  works on a machine that has never built the store.

### Known-bad ledger (why updates converge)

13 objects in the window are truncated upstream (section 2) and can never be
virtualised. Without special handling they look like *interior* gaps, so every weekly
`update` would choose `rewrite`, re-write ~950 files, fail on the same 13 again, and
never converge — a first `update --dry-run` after the build did exactly that (planned
951 files instead of ~4 appends). Fix: a **known-bad ledger** in the root-group attrs
(`known_bad: {url: {error, first_seen, last_checked}}`):

* planning excludes objects that failed within `RETRY_AFTER_DAYS` (14) and reports the
  count per model as `known_bad_skipped`;
* each run merges its failures in and drops URLs that now parse (upstream repair), so a
  repaired object re-enters the plan after at most 14 days;
* the ledger is written before the commit, and an all-failing run whose ledger is
  unchanged makes no commit (idempotency preserved);
* `known_bad(model=None, path=...)` exposes it to consumers.

Seeded on the operational store with commit `GKBH0DV571XRWRC8STG0` (13 entries) so the
first weekly run does not pay for a pointless full rewrite.

### Sentinel values (consumer note)

Source files pad cells (land points, missing levels) with the sentinel
`9.969209968386869e36`. VirtualiZarr reads it and stores it as the Zarr array's
`fill_value = 9.96921e36`, but Zarr applies `fill_value` only to *missing* chunks, so
padded cells keep their raw value; and xarray's direct NetCDF read of the same file
exposes no `_FillValue` in `attrs` or `encoding`, so `mask_and_scale` has nothing to act
on there either. Verified on both paths (values and NaN positions identical, both return
the raw number - and mean over a padded region overflows to `inf` in float32). Consumers
that need NaN masks must apply `da.where(da.abs() < 9e36)` themselves; behaviour is the
same as before the store existed, but is easy to trip over when comparing paths.

## 5. Measured behaviour

* **Full operational build** (`build --days 120 --workers 8`, 2026-11-06 05:28:xx ->
  05:55:10 UTC): **27 min** wall for 1611 files / 8 models, **1598 inits stored** (13
  corrupt files skipped), **~4.85M virtual refs** (2829-3403 per init per model; per-model
  counts cross-checked against the build log's `refs_added`), **594 manifest files**,
  repo on disk **101 MB**, `native_bytes` written **29 KB** (coords + `valid_time`) versus
  **5.84 TB** of source data referenced. Peak main-process RSS 1.0 GB. Log:
  `logs/mlwp_build_full.log`.
* Full-store verification (`scripts/mlwp_verify_store.py`): all 8 models match the live
  bucket counts minus exactly the 13 corrupt objects, zero extras, sorted, unique;
  `open_model()` 0.06-0.37 s; subset read (1 init x 3 leads x t2+msl x 15x41 bbox)
  **0.24-0.70 s**, max abs diff vs the direct NetCDF read **0.0** for every model.
* Subset read (1 init, 3 leads, 2 vars, 41x61 grid cell bbox): **0.46 s** through the
  store vs **13.6 s** for the direct `xr.open_dataset` + `.sel()` path on the same file
  (sources measured 1.9 s vs 51.8 s on their own bbox).
* Incremental update: append of 2 inits (1 model) = **13.3 s** vs 30.9 s for the 6-init
  build of the same model, i.e. cost tracks the number of new files, not the archive.
* Value equality vs direct NetCDF read: max abs difference **0.0** (bit-identical) for
  2-D (`t2`, `msl`) and 3-D (`z`) variables, before and after an append/rewrite cycle.
* Tests: 11/11 pass (`pytest tests/test_mlwp_icechunk.py -m ''`, 64 s), including the
  staged-build fixture, the self-heal path and the idempotency no-op.

## 6. Deviations from upstream `data_prep/cira_icechunk_generation.py`

* Upstream stops its store at 2025-05-27 to dodge the chunking change; this store covers
  any window and splits eras instead of dropping data.
* Upstream converts to kerchunk JSON then writes with `icechunk` manually via xarray
  kerchunk; this store uses VirtualiZarr 2.8's `HDFParser` and `Dataset.vz.to_icechunk`
  directly (no kerchunk intermediate, no `consolidated` metadata).
* Upstream keeps `valid_time` as a 1-D coord inherited from the first init after
  `swap_dims`; that is wrong for other inits. Here `valid_time` is a real 2-D
  `(init_time, lead_time)` coord.
* Upstream skips `FCNv1`. In this bucket there is no FCN directory at all (the retired
  `FOUR_v100_GFS` is the structurally odd one); it is representable here because the
  store keeps the source chunk grid, but it is outside the live 120-day window.
* Same variable names, same model keys, same `(init_time, lead_time)` convention, so
  EWB-compatible: `xarray.open_datatree` style access `store[model]` works per group.

## 7. How to run

```bash
# build from scratch (fails if the repo already exists)
uv run python -m ewb_live.stores.mlwp_icechunk build --days 120 --workers 8
# weekly incremental (append / backfill as needed, one commit)
uv run python -m ewb_live.stores.mlwp_icechunk update --days 120 --workers 8
# optional: --models GRAP_v100_IFS ..., --repo PATH, --end YYYY-MM-DD, --dry-run

uv run python -c "from ewb_live.stores.mlwp_icechunk import open_model, list_models, list_inits, known_bad; \
ds = open_model('GRAP_v100_IFS'); print(ds.sizes)"
```
`known_bad(model)` lists upstream-corrupt objects whose inits are absent by design.
`build`/`update` print progress on **stderr** and the summary JSON on **stdout**.
```bash

uv run pytest tests/test_mlwp_icechunk.py -m ''          # offline unit tests
uv run pytest tests/test_mlwp_icechunk.py -m network     # end-to-end (S3)

# post-build verification against the live bucket (counts, values, read speed)
uv run python scripts/mlwp_verify_store.py
```

Weekly wiring: `ewb_live/weekly.py::update_mlwp_icechunk` (step 1 of the weekly cycle)
now prefers `python -m ewb_live.stores.mlwp_icechunk update --days 120` and falls back to
the previous `mlwp-icechunk` executable / `mlwp_icechunk` module lookups if this module
is ever absent. No other file outside `ewb_live/stores/`, `scripts/`, `tests/`, and
`notes/` was touched; `ewb_live/sources/` is untouched as agreed.

## DONE

**What works (verified by real runs, logs in `logs/`)**
* Survey of 1611 objects (120-day window) + 412 historical samples: variables, dims,
  dtypes, HDF5 chunk shapes, codecs (`bytes|shuffle|zlib`), era boundaries, coverage
  gaps, 13 corrupt upstream objects. Findings in section 2, raw data in
  `data/cache/mlwp/*.jsonl`, report generator `scripts/mlwp_survey_report.py`.
* Store builder: virtual refs only, one group per model, EWB-compatible names/dims,
  manifest splitting, VCC + anonymous container credentials, 8-way parallel reference
  generation, one commit per run.
* Incremental `update`: append (newest-only), rewrite (interior backfill), idempotent
  no-op, staged atomic `build`, self-healing `update` on a missing repo, and a persisted
  known-bad ledger that makes the weekly cycle converge (measured on the operational
  store: `update --days 120 --dry-run` plans **0 files** / commits nothing, vs 951 files
  before the ledger existed; `logs/mlwp_dryrun_after_ledger.json`). Append measured at
  13.3 s for 2 new inits.
* Read path: `list_models()`, `list_inits(model, start, end)`, `open_model(model)`
  (lazy, dask-backed), `known_bad(model)`. Subset reads are ~30x faster than the direct
  NetCDF path and bit-identical to it (max abs diff 0.0).
* Tests: `tests/test_mlwp_icechunk.py` - **13/13 pass** (5 offline unit tests including
  the ledger merge semantics, 8 `@pytest.mark.network` end-to-end tests: build, listing,
  lazy structure, value equality vs NetCDF, read speed, self-heal, idempotency, ledger
  round-trip, operational-store read).
* Full operational build of the 120-day window: `data/icechunk/cira_mlwp/` built
  2026-11-06 (`logs/mlwp_build_full.log`) - 8 models, 1598 inits, ~4.85M virtual refs,
  594 manifests, 101 MB repo, 27 min wall, 29 KB of data actually copied vs 5.84 TB
  referenced. Verified end-to-end with `scripts/mlwp_verify_store.py` (PASS: counts match
  the bucket minus the 13 corrupt objects, all reads bit-identical, 0.24-0.70 s per
  subset).

**How to run**: section 7 (one-line CLI for build/update, four-function Python API
(`list_models`, `list_inits`, `open_model`, `known_bad`), pytest and verification
commands). `ewb_live.weekly` step 1 calls `update --days 120` automatically.

**Gaps / known limits**
* Reads require S3 reachability and low-latency access to `us-east-1` (no local data
  cache). For heavy repeated scans of the same region, cache locally or read a
  pre-computed Zarr copy.
* `latitude` is descending: `ds.sel(latitude=slice(30, 40))` returns nothing; use
  `slice(40, 30)` (same behaviour as the source files).
* `lead_time` is a timedelta coord: select with `pd.Timedelta(hours=6)`, not `6`.
* Layout change *backwards* inside one era (files reverting to an older chunk layout in
  the middle of the window) is refused and reported in `summary["skipped"]` rather than
  guessed; it needs a human decision.
* Corrupt upstream objects (section 2) are missing inits in the store, by design;
  `known_bad()` reports them. A repaired object is retried only on the first run after
  `RETRY_AFTER_DAYS` (14), so a NOAA fix lands up to two weeks late.
* No group deletion: if a rewrite would leave a stale era group (layout reverted and
  the old group's inits all fell out of the window), `update` leaves the stale group;
  `open_model` would then include stale inits until a full rebuild. Not reachable in
  the current window (single era per model).
* `FCNv1`-class models: no FCN directory exists in the bucket; `FOUR_v100_GFS` (retired,
  structurally different) is supported by design but not built in the live window. A
  `--days 1400` build would include it; that build has not been run.
* `build` requires the repo path not to exist. Rebuilding in place means deleting the
  directory (or using `--repo`), which is deliberately left to the operator.

## successor 2 (2026-11-06) — full-archive virtual store, daily update, edge-chunk gate

Product decisions implemented: (1) VIRTUAL-only full archive, no rolling window;
(2) daily update + systemd units; (3) virtualizarr partial-edge-chunk guard;
(4) sources aiwp.py works across full history (store-primary listing); (5) verified.

### Module rewrite (ewb_live/stores/mlwp_icechunk.py)
- Removed the 120-day window/eviction entirely. Planning defaults to the FULL
  archive via new `mlwp_common.inventory_all()` (year-wise LIST, cached, floor
  ARCHIVE_FIRST_YEAR=2019; survey: 31,463 files, 9 model dirs, earliest 2020-09-30).
- Era-groups: contiguous runs of equal `layout_signature` per model; groups named
  `<model>` (first era) / `<model>__YYYYMMDDHH` (era changes). open_model/list_models
  map them back to models and concat along init_time.
- `build` stages at `<repo>.tmp` and moves into place (readers never see a half
  root); `backfill [--batch 750] [--swap]` = full-archive build into
  `<repo>.backfill`, resumable (skips inits already ingested; batched commits per
  model), `--swap` promotes atomically, old repo kept at `<repo>.old-<ts>`.
- `update`: flock (flock LOCK_EX|NB on `<repo>.lock`; exits 0 "locked" if another
  writer runs), self-heals a missing repo via staged build, appends only tail inits
  (interior gaps trigger an in-place era rewrite), stall watchdog rebuilds the
  worker pool if a batch wedges, one commit per run, writes stats to
  data/store_metrics.json.
- Maintenance: `maintenance()` = expire_snapshots(older_than) + garbage_collect,
  runs from update() every MAINTENANCE_INTERVAL_DAYS=7 (root attr stamp), also CLI.
  TimeoutStartSec=3h in the unit is the hard backstop for the service.
- Known-bad ledger: skipped-wins-over-succeeded merge, so edge-gate REFUSALS also
  enter the ledger (converges; retried after RETRY_AFTER_DAYS=14).
- Portability (orchestrator rule): repo paths dispatch by scheme — local,
  s3://, gs:// (icechunk s3_storage/gcs_storage/local_filesystem_storage, all
  verified on box); `EWB_MLWP_REPO` env overrides DEFAULT_REPO; no new hardcoded
  hosts/IPs/abs-paths (repo-relative default data/icechunk/cira_mlwp).
- VirtualiZarr pin: LOADABLE force-materialises 1-D coords (fixes the
  FOUR_v100_GFS swap_dims failure: 41-step virtual `time` coord stays unloaded →
  raise early with a clear message if any 1-D coord is virtual after parse).

### Edge-chunk safety gate (decision 3)
- `partial_edge_chunks(vds)`: flags vars whose grid extent isn't a multiple of the
  chunk size (the virtualizarr ≤2.8 mis-offset hazard class).
- `probe_edge_chunk_equivalence(url, init)`: writes the risky virtual dataset to a
  temp icechunk repo, reads the LAST edge chunk back through the store, compares
  bit-exact (equal_nan) vs a direct NetCDF range read of the same region.
- `_edge_gate` runs per era on the era's gate file: no partial edge chunks → pass;
  partials → probe must pass or the whole era is REFUSED into the ledger.
  FOUR_v100_GFS (lead 41 in chunks of 14) is the live regression case — probe
  PASSES on the box's virtualizarr 2.8.5/xarray-pinned stack (test not skipped).
- Tests: offline detection test + network probe tests for a live model (trivial
  pass) and FOUR (real round-trip; skip-with-message if a future version breaks).

### Ops
- ops/ewb-live-store.service (oneshot, update, TimeoutStartSec=3h, logs/store.log)
  + ewb-live-store.timer (OnCalendar=*-*-* 05:30:00 UTC, Persistent=true);
  install.sh UNITS updated. Installed on ewb 2026-11-06 17:17 UTC; timer active,
  first trigger 2026-11-07 05:30 UTC. Weekly cycle's store step is now a no-op
  catch-up (flock + idempotence). Legacy /root/mlwp-icechunk untouched, cron off.

### Sources edits (logged in channel_sources_mlwp.md)
- aiwp.py: `_store()` → repo_exists (scheme-aware); available_inits → store listing
  primary + direct LIST only for the tail past the store's last init; cadence
  docstring fix. +26/−14, contract-preserving.

### Bugs found & fixed while verifying (each with a regression test)
1. layout_signature hashed init_time VALUES → every file its own era → two eras in
   one day collided on the dated group name (ContainsGroupError mid-build). Now
   ignores init_time; dated names hour-granular; test_layout_signature_ignores_init_time_values.
2. Freshly created icechunk repo has no committed root → zarr open_group(mode="r")
   GroupNotFoundError on resume checks. open_repo(create=True) now commits root
   attrs immediately; _root_group/_EmptyRoot tolerate legacy empty roots.
3. Edge-probe direct read stripped the bucket from the s3fs key (FOUR probe raised
   FileNotFoundError; looked like a failed probe, was a bad path). split("://")[1].
4. repo_is_local regex missed 's3://' (digit in scheme) → treated s3:// repos as
   local paths. Fixed to RFC-3986 scheme class.

### Test results (ewb box, 2026-11-06)
- offline: 13 passed. network: 12 passed (smoke build, list/open, virtual-vs-direct
  value identity on GRAP t2/z bit-exact, subset read <30s, self-heal, idempotent
  update, maintenance roundtrip, known-bad ledger, edge probes GRAP+FOUR, full
  archive inventory ≥30k files back to ≤2020-12-31, real-store read).

## DONE (successor 2) — 2026-11-06

Full-archive virtual store is LIVE on ewb: /opt/ewb-live/data/icechunk/cira_mlwp
(old 120-day store kept at cira_mlwp.old — delete when satisfied).

Verification (scripts/mlwp_verify_store.py, ewb box, 2026-11-06 19:31-19:40 UTC):
- Verdict PASS across all 9 model dirs. Per-model store-vs-bucket init counts:
  missing=0, extra=0 (known-bad ledger files excluded on both sides — e.g.
  GRAP_v100_IFS store=3859 vs bucket=3860 is exactly its 1 ledgered corrupt object).
- Value checks vs direct NetCDF range reads (bbox 40N..30N, 230..245E, t2+msl,
  lead 0/6/12h) at the NEWEST (2026-11-06 00Z) and OLDEST init of every model —
  maxdiff = 0.0 everywhere (bit-exact incl. NaN positions). Covers 2020-09-30
  (PANG_v100_GFS), 2021 (GRAP_v100_GFS 2021-12-31), 2022+ eras, and yesterday.
- Subset read time through the store: 0.35–2.11 s per init (open_model 60–117 s
  is the one-off full-archive group concat + manifest scan; per-process cost,
  sources caches the handle in-process).
- Coverage: 2020-09-30 .. 2026-11-06, 2–6 eras per model (era groups named
  <model>[__YYYYMMDDHH]), 31,454 files ingested of 31,463 (9 upstream-corrupt
  HDF5 objects ledgered: 7 AURO_v100_IFS, 2 FOUR_v200_IFS+GRAP checksum/B-tree —
  matching sources' known-bad findings; zero refused by the edge gate).
- Store size: 742 MB metadata, 11,371 objects, ~13.4M virtual chunk refs.
- Backfill wall time: 8,121 s (2h15m) at 16 workers, 0 parse errors beyond the
  9 upstream-corrupt files, resumable (it survived nothing — but the resume path
  was exercised by the relaunch after the empty-root bug fix).
- Daily update: no-op catch-up on a current store = 11.0 s, no commit, one JSONL
  row in data/icechunk/store_metrics.jsonl. Daily timer armed: first fire
  2026-11-07 05:30 UTC (Persistent=true). Expected steady-state update ≈ tens of
  seconds (4–8 new inits/day, batch parse ~2 files/s/worker).
- Edge-chunk guard: FOUR_v100_GFS (41 leads, chunks of 14 — the legacy mis-offset
  case) probes bit-exact on virtualizarr 2.8.5; nothing refused. Gate is per-era;
  failures land in the known-bad ledger and are retried after 14 days.
- Maintenance: expire+gc wired into update every 7 days; verified roundtrip in
  network tests (maintenance_due flips via root attrs).

Files (all uncommitted per the standing rule): ewb_live/stores/mlwp_icechunk.py
(rewrite), mlwp_common.py (inventory_all), sources/aiwp.py (store-primary
available_inits; logged in channel), ops/ewb-live-store.{service,timer} (new),
ops/install.sh, SPEC.md §5, README.md, tests/test_mlwp_icechunk.py (25 tests:
13 offline + 12 network, all green on box), scripts/mlwp_verify_store.py,
scripts/mlwp_archive_survey.py, scripts/mlwp_probe_file.py.

## Memory-burst incident + open_init fix (2026-11-06, post-DONE)
ewb flagged mlwp_verify_store.py (PID 103220) committing 70-210GB anon in ~90s
cycles, starving their scoring lane (MemAvailable -> 14GB). Killed verify +
network tests immediately; MemAvailable restored to ~123GB.
Root cause: open_model() decodes every era group's manifests -> measured
~8GB RSS / ~115s (GRAP_v100_GFS, systemd-capped probe); the old verify looped it
18x in ONE process and GC lag stacked the opens.
Fixes:
- `mlwp_icechunk.open_init(model, init)`: opens only the era group containing
  init via its init_time coord (~40MB read). Measured: open 0.83s (vs 115s),
  read 0.64s, maxdiff 0.0 (GRAP 2021-12-31T12Z, inside 8G systemd scope).
- aiwp.py `_open_via_store` switched open_model -> open_init (their lane; noted
  in channel file, revertible +4/-3).
- mlwp_verify_store.py rewritten: per-check subprocess under
  `systemd-run --scope -pMemoryMax=20G` (default --mem-gb 20, 0=off), child mode
  `--value-check MODEL INIT_ISO`; coverage/size stay manifest-light. Fixed
  systemd-run flag (--scope rejects --wait) and a latent bug where the old
  script opened a full model view just to list vars.
- Skill reference store-builder-pitfalls.md corrected (killed the
  "cache the dataset handle" advice; added open_init rule).
Re-verify (capped children): PASS — 9 models missing=0/extra=0, 18/18
bit-identical, opens 0.7-0.9s reads 0.5-0.8s, run ~9min, MemAvailable >=120GB
throughout. sources network 12/12 on open_init path. Historical check:
FOUR_v100_GFS covers 2020-09..2023-10 incl. 2021-06 (59 inits); registry GFS
variants start 2021-12-22+ (empty 2021-06 windows for them are correct).
