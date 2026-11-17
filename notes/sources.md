# Agent `sources` — decisions, findings, log

## Research: store discovery (2026-11-06)

### "Sierra MLWP store" identification
The task said to find the "Sierra MLWP" store on registry.opendata.aws. There is **no
registry entry named Sierra** (cloned awslabs/open-data-registry @ main and grepped all
~2700 dataset yamls: only NASA datasets mention "Sierra" = the mountain range). The only
MLWP entry is:

- **"AI Weather Prediction (AIWP) Model Rforecasts"**, dataset file `datasets/aiwp.yaml`,
  bucket **`s3://noaa-oar-mlwp-data`** (us-east-1, anonymous), managed by CIRA + NOAA-GSL
  (Radford et al. 2025 BAMS). MetPy calls the same archive "NOAA/CIRA MLWP Archive".
  Decision: this is "the MLWP store" the SPEC means; exposed as the AIWP backend.

Layout (verified live against the bucket on 2026-11-06):

```
s3://noaa-oar-mlwp-data/<CODE>_v<V>_<GFS|IFS>/<YYYY>/<mmdd>/<CODE>_v<V>_<INIT>_<YYYYMMDDHH>_f000_f240_06.nc
```

Top-level model dirs seen live: `AURO_v100_{GFS,IFS}`, `FOUR_v100_GFS` (legacy, unused),
`FOUR_v200_{GFS,IFS}`, `GRAP_v100_{GFS,IFS}`, `PANG_v100_{GFS,IFS}`, plus `Derived/`,
`parquet/`, `colab_resources/`, `README.txt`. We expose the four current codes in both
init systems (model ids `graphcast[_gfs]`, `pangu_weather[_gfs]`, `fourcastnet_v2[_gfs]`,
`aurora[_gfs]`).

One NetCDF4/HDF5 file per init: dims `time` (41 valid times, 0–240 h step 6), `level`
(13 hPa levels 1000..50), `latitude` 721 (90..-90 step 0.25), `longitude` 1440
(0..359.75). `time` holds valid times (init is in the filename). Variables verified from a
2026-11-05 file (GRAP IFS): `apcp msl q t t2 u u10 v v10 w z` (FOUR adds `r u100 v100 sp
tcwv`; PANG/AURO lack `apcp w`).

**Latency: current.** As of 2026-11-06 ~04:20Z the newest files were init **2026-11-05 12Z**
(00Z and 12Z inits appear with ~0.5–1 day lag, per registry "updated twice a day"). For the
90-day dashboard this is fine; it is not a real-time feed.

### ECMWF open data `s3://ecmwf-forecasts` (region eu-central-1)
Layout:
```
<YYYYMMDD>/<HH>z/<system>/0p25/oper/<YYYYMMDD><HH>0000-<lead>h-oper-fc.grib2   (GRIB2, 0.25°)
<...>-oper-fc.index   # JSON-lines: {levtype, param, levelist, step, _offset, _length, ...}
```
systems used: `ifs/0p25/oper` (IFS HRES deterministic) and `aifs-single/0p25/oper`.
Grid 721×1440, lon 0..359.75. Leads verified live on 2026-11-05 00Z: **IFS 0–144 h step 3 h,
144–360 h step 6 h (85 files); AIFS 0–360 h step 6 h (61 files)**. (Older docs say hourly
0–144 — no longer true.) `available_inits`/`open_forecast` list the directory once and
intersect with the requested leads.
Latency: 2026-11-05 00/06/12/18Z all present at check time; 2026-11-06 00Z not yet published
at 04:20Z (~07–08Z publish typical) → current to D-0/D-1.
S3 anonymous GetObject on this bucket throttles hard (SlowDown); ranged reads go through the
registry-listed CloudFront distribution `d2zvc0wgha4k2l.cloudfront.net`, LIST (unsupported by
CloudFront) stays on S3 with retries. Byte-range field selection via `.index` offsets, per
SPEC ("use GRIB .index files").

### Bonus: `s3://noaa-nws-graphcastgfs-pds` (AIGFS, operational GraphCast at NCEP)
`aigfs.<YYYYMMDD>/<HH>/model/atmos/grib2/aigfs.t<HH>z.pres.f<LLL>.grib2` + `.idx`.
Cycles 00/06/12/18Z, leads 0–384 step 6 (130 files/cycle), 0.25°, **pressure levels only**
(no 2 m/MSLP — usable for AR-type variables via p-level u/v/q only). Publication gaps
observed (whole day 2026-11-04 absent). Exposed as `aigfs`. Not required by SPEC; included
since it's live operational MLWP. (Its experimental predecessor `noaa-nws-fourcastnetgfs-pds`
also exists but is being replaced.)

## EWB naming conventions matched (extremeweatherbench 1.1.1 from source)
- `inputs` forecast classes: `ForecastBase`, `ZarrForecast`, `KerchunkForecast`,
  `XarrayForecast(ds=...)`. `make_ewb_forecast` returns an `XarrayForecast` (evaluated via
  `subset_data_to_case` on `init_time`/`lead_time` coords).
- `defaults.DEFAULT_VARIABLE_NAMES` is a **list**: surface_air_temperature,
  air_pressure_at_mean_sea_level, eastward_wind, northward_wind, specific_humidity,
  geopotential, relative_humidity, 100m_eastward_wind/northward_wind,
  surface_dewpoint_temperature, surface_eastward/northward_wind, etc.
  **No precipitation name exists in EWB 1.1.1** → `apcp`/`tp` are NOT exposed (SPEC event
  types need none). `geopotential_height`/`z` map to `geopotential` (EWB CIRA mapping maps
  `z`; the redundant GRIB `gh` is dropped).

## Decisions
- Output convention: dims `(init_time, lead_time[timedelta64], level?[hPa int desc],
  latitude[90..-90 desc], longitude)`, **longitudes normalised to [-180,180) ascending**
  (`_transform.normalize_longitude`; EWB regrids/aligns either way — ewb agent said 0–360
  preferred but EWB aligns anyway; we keep one consistent convention documented in api.py).
  -> See Requests below (flagged to ewb).
- All three backends materialise to zarr under `data/cache/subsets/<model>/<YYYYmmddHH>Z/<hash>/`
  keyed by (model, init, vars, leads, bbox) — repeat calls are offline.
- **Cache eviction (orchestrator 2026-11-06)**: `ewb_live/sources/cache.py` prunes subset
  entries with init older than 120 days (90-day window + margin); triggered once/day from
  `open_forecast` and available as `python -m ewb_live.sources.cache`. Weekly refresh batches
  just add new init dirs; old ones age out.
- AIWP remote open = fsspec HTTPS range reads on HDF5 (`h5netcdf`), bbox+lead+level cut
  applied lazily *before* `.compute()` so only touched chunks are fetched.
- ECMWF: per-lead `.index` (CloudFront) → fetch only (levtype, param[, level]) byte ranges →
  cfgrib-decode a small temp GRIB blob.
- Removed from first draft: `accumulated_precipitation`, `geopotential_height` exposure
  (not in EWB 1.1.1 name list); `level_variables` now computed per model as
  exposed ∩ LEVEL_VARS_EWB.

## Requests (to other agents)
- **ewb**: registry drops `accumulated_precipitation`/`geopotential_height` (not EWB 1.1.1
  default names); longitudes are returned in [-180,180) ascending, latitudes descending.
  AR-scoring vars confirmed available: `eastward_wind`, `northward_wind`,
  `specific_humidity` on `level` 1000..50 hPa (all AIWP models, ifs_hres, aifs_single;
  aigfs has no surface fields). `available_inits` accepts/returns naive UTC datetimes.
- **mlwp-store**: see notes/channel_sources_mlwp.md.

## Status log
- 04:0x previous run interrupted mid-verification; re-verified everything live this session.
- Fixes this session: exposed variable set trimmed to EWB 1.1.1 `DEFAULT_VARIABLE_NAMES`
  (dropped `accumulated_precipitation`, `geopotential_height`); per-model
  `level_variables = exposed ∩ LEVEL_VARS_EWB`; ECMWF lead schedule corrected to the live
  3h/6h schedule (was documented 1h/3h) + single dir-listing per init instead of per-lead
  probing; AIWP bbox/lead/level cut applied before compute; zarr cache only written when
  `use_cache=True`; cache eviction module `ewb_live/sources/cache.py` (120-day init-age,
  daily auto-prune + `python -m ewb_live.sources.cache`); aigfs docstring: 4 cycles/day.
- unit tests `tests/test_sources_unit.py`: **12 passed** (no network).
- network suite `tests/test_sources_network.py` (one real recent init per model +
  make_ewb_forecast) running in background; results will be appended here.
- Earlier all-model smoke: opens succeeded for all 10 AIWP/ECMWF ids; aigfs 2026-11-05 00Z
  cycle confirmed absent (publication gap; `available_inits` correctly skips it).
- Coordination with mlwp-store agent: posted handoff contract in
  notes/channel_sources_mlwp.md and nudged. They replied: store API
  `ewb_live.stores.mlwp_icechunk` (list_models/list_inits/open_model lazy, native var names,
  init_time+lead_time coords), first usable build ~06:00-06:45 UTC with a ready nudge.
  **aiwp.py still reads NetCDF directly** (not the icechunk store) pending their ready signal.
- First network run caught two real bugs, both fixed + live-verified: (a)
  `aiwp.available_inits` double-prefixed the bucket in `list_prefix` (returned [] → tests
  skipped), (b) `aigfs.open_forecast` used `cat_file(stop=)` → TypeError (fsspec wants `end=`).
- Second network run: 11/12 passed (all 8 AIWP ids, ifs_hres, aifs_single,
  make_ewb_forecast), aigfs failed on "no lead files". Root cause: AIGFS Herbie-style `.idx`
  stores for record i the *cumulative offset BEFORE* the message — record range is
  [END_i, END_{i+1}) with the last end = object size (verified by scanning GRIB magic/7777
  markers). Also cfgrib names decoded fields by GRIB2 shortName (`t`,`q`,`u`,`v`,`z`), not the
  idx `TMP`/`SPFH` names — lookup translated via `_IDX_TO_CFGrib`. aigfs test passes;
  T@500 hPa sanity check 255-269 K over CONUS bbox.
- Value sanity checks (graphcast init 2026-11-04 00Z, bbox 30-40N/110-100W): t2 mean 285.8 K,
  geopotential 13 levels 1000..50 hPa monotone 4e2..2e5 m2/s2. All plausible.

## Status log (concurrent sources instances, 2026-11-06 04:00-05:20Z)
- Two concurrent instances of the `sources` role ran in this tree (harness collision);
  edits interleaved on aiwp.py/aigfs.py. Final on-disk state is coherent; both fixes are
  compatible: AIWP fill-masking (GraphCast lead-0 NaN slots) + lazy bbox pre-cut before
  compute; aigfs Herbie-.idx byte-range semantics fixed (field i spans [END_i, END_{i+1}),
  last ends at object size — the first column value 0 is the first message's START, not an
  end offset).
- `ecmwf._decode_grib` keys decoded vars by both cfgrib name and `GRIB_shortName`.
- **aigfs `geopotential`**: NDFD "HGT" on pressure levels decodes as eccodes `gh` =
  geopotential *height* in gpm, not z. Converted x9.80665 to m2/s2 on read (units attr set).
  Verified 500 mb over CONUS = 5719 gpm (physically sane, Nov 3 00Z).
- Network suite (one real recent init per model) + unit suite re-run after fixes: see DONE.

---

## DONE (2026-11-06 05:20 UTC)

### What works (all verified against live data today)
`ewb_live/sources/` implements SPEC section 1 in full:
- `list_models()` → `aifs_single, aigfs, aurora, aurora_gfs, fourcastnet_v2,
  fourcastnet_v2_gfs, graphcast, graphcast_gfs, ifs_hres, pangu_weather,
  pangu_weather_gfs` (11 ids; AIWP/CIRA MLWP store + ECMWF open data + NOAA AIGFS).
- `available_inits(model, start, end) -> list[datetime]` (naive UTC; handles the
  publication gaps observed live, e.g. AIGFS 2026-11-04 entirely absent).
- `open_forecast(model, init, variables, lead_hours=None, bbox=None)` → EWB-convention
  Dataset: dims `(init_time, lead_time, level?, latitude, longitude)`, EWB variable names
  from `defaults.DEFAULT_VARIABLE_NAMES`, lead_time timedelta64, level hPa (desc),
  longitudes [-180,180) asc, latitudes 90..-90 desc. bbox wraps the antimeridian.
  Byte-range subsetting: GRIB `.index`/`.idx` offsets for ECMWF & AIGFS; lazy HDF5 range
  reads for AIWP. Materialised subsets cached as zarr under `data/cache/subsets/`.
- `make_ewb_forecast(model, variables, inits, ...)` → `extremeweatherbench.inputs.XarrayForecast`
  consumable by EWB's evaluation machinery.
- `python -m ewb_live.sources.cache` (+ once-daily auto-prune inside `open_forecast`) evicts
  cache entries older than 120 days for the weekly refresh cycle.

### Tests
- `uv run pytest tests/test_sources_unit.py -q` → **12 passed**, no network.
- `uv run pytest tests/test_sources_network.py -m network` → **12 passed** (~6 min): opens a
  real recent init for all 11 models + EWB forecast wrapper; physical-value sanity confirmed
  (t2, z, T@500 hPa plausible).

### How to run (no setup beyond the repo venv)
```python
from ewb_live import sources as s
inits = s.available_inits("ifs_hres", datetime(2026,11,1), datetime(2026,11,6))
ds = s.open_forecast("graphcast", inits[-1],
                     ["surface_air_temperature", "eastward_wind", "northward_wind"],
                     lead_hours=[0, 24, 48], bbox=(20, 50, -130, -60))
fc = s.make_ewb_forecast("graphcast", ["surface_air_temperature"], inits[-2:], [0, 12], bbox)
```

### Known gaps / caveats
- **"Sierra MLWP"**: no such registry entry exists; it is the AIWP entry
  (`s3://noaa-oar-mlwp-data`, CIRA/NOAA) — documented above.
- aigfs exposes pressure-level fields only (no 2 m/MSLP) — unusable for heat/freeze
  detection, fine for AR vars.
- AIWP latency ~0.5–1 day; ECMWF publishes D 00Z by ~07–08Z same day. Both "current to
  today" as of 2026-11-06.
- Lead grids differ by model (AIWP/AIGFS 6 h; IFS 3 h≤144 h then 6 h; AIFS 6 h); requesting
  an unpublished lead silently drops it (ECMWF) or raises (AIWP step-6 requirement in
  `aiwp.open_forecast` keeps only multiples of 6 in 0–240).
- **icechunk store**: `ewb_live/stores/mlwp_icechunk.py` appeared (mlwp-store agent), but no
  store built at `data/icechunk/` and no "usable" announcement on the channel yet, so
  **aiwp.py currently reads the NetCDF files DIRECTLY (not through the icechunk store)**.
  Handoff plan agreed on notes/channel_sources_mlwp.md; I switch + re-run both test files on
  their ready nudge; direct reader stays as fallback.
- `uv add --dev pytest` was needed (add-only, per ground rules).

---

## DONE (agent `sources`, 2026-11-06 05:30Z)

### What works (all verified against live buckets today)
`ewb_live/sources/` implements the SPEC section 1 contract:

- `list_models()` -> 11 ids: `graphcast`, `pangu_weather`, `fourcastnet_v2`, `aurora` and
  each `*_gfs` variant (AIWP store `s3://noaa-oar-mlwp-data`), `ifs_hres`, `aifs_single`
  (`s3://ecmwf-forecasts`, eu-central-1), `aigfs` (operational NCEP GraphCast,
  `s3://noaa-nws-graphcastgfs-pds`, bonus source).
- `available_inits(model, start, end)` -> naive-UTC datetimes, probes real object
  existence (handles publication gaps, e.g. aigfs 2026-11-04 absent).
- `open_forecast(model, init, variables, lead_hours=None, bbox=None, use_cache=True)` ->
  Dataset on EWB conventions: dims `(init_time, lead_time[timedelta64[ns]], level?[hPa desc],
  latitude[90..-90], longitude[-180,180))`, EWB 1.1.1 variable names. bbox
  `(lat_min, lat_max, lon_min, lon_max)` with lon in [-180,180], antimeridian wrap
  supported. Only requested variables/leads/grid are fetched (HDF5 range reads for NetCDF;
  `.index`/`.idx` byte-range GRIB reads otherwise). zarr-cached under
  `data/cache/subsets/<model>/<init>/<hash>/` (keyed on model+init+vars+leads+bbox);
  repeat calls are offline. Cache auto-prunes inits older than 120 days
  (`python -m ewb_live.sources.cache`).
- `make_ewb_forecast(model, variables, inits, lead_hours, bbox)` ->
  `extremeweatherbench.inputs.XarrayForecast` (multi-init concat), consumed by EWB
  evaluation (verified isinstance + vars via network test).
- Model metadata via `model_info(model)` -> ModelSpec (variables, level_variables, levels,
  backend, resolution).
- ECMWF reads use the CloudFront distribution (S3 GET on `ecmwf-forecasts` 503s
  anonymously); LIST and fallbacks use s3fs anon with retry/backoff.

### How to run
```
uv run pytest -m "not network"          # 12 unit tests, offline
uv run pytest -m network                # 12 network tests, ~7 min (real inits)
```
Python: `from ewb_live import sources; sources.open_forecast("ifs_hres",
datetime(2026,11,5), ["surface_air_temperature"], [0,24], (30,45,-125,-115))`.
Final suite state: **24 passed** (unit + network, 2026-11-06 05:28Z).

### Known gaps / caveats
- "Sierra MLWP" is not a registry name; the AIWP store (`aiwp.yaml`,
  `s3://noaa-oar-mlwp-data`) is the MLWP source (see research section above).
- `graphcast*` lead 0 is all-NaN (2-step model; first slot is fill by design). Other AIWP
  models have valid lead 0. Fill value (9.96921e36) masked to NaN for all AIWP vars.
- `aigfs`: pressure levels only (no 2 m/MSLP fields published); `geopotential` converted
  from NDFD gpm to m2/s2. Cycles 00/06/12/18Z but NCEP publishes with gaps; surface-based
  event scoring should not use it — AR-type u/v/q scoring works.
- AIWP store latency ~0.5-1 day (00Z/12Z inits); ECMWF ~7-8 h after run. Both fine for the
  weekly cycle, not a real-time feed.
- IFS 0.25deg GRIB leads are 3 h step to 144 h, 6 h beyond (not hourly as older docs say);
  requested leads missing from a run are skipped silently, `lead_time` reflects what
  existed.
- `fourcastnet_v2` GFS variant exposes `relative_humidity` + 100 m winds extra; others
  don't have those (per-model `spec.variables`).
- mlwp-store agent is building an icechunk store (`ewb_live.stores.mlwp_icechunk`) over
  the same AIWP data; `aiwp.py` still reads NetCDF directly and can be swapped behind the
  same API when they give the ready signal (notes/channel_sources_mlwp.md).

---

## UPDATE (2026-11-06 06:10 UTC): aiwp.py now reads THROUGH the icechunk store
Per orchestrator plan, `ewb_live/sources/aiwp.py` switched its primary read path to the
mlwp-store agent's VirtualiZarr/Icechunk store (`ewb_live.stores.mlwp_icechunk`, repo at
`data/icechunk/cira_mlwp/`), keeping the direct NetCDF/HDF5 reader as automatic fallback:
- `_open_via_store`: `open_model(code)` (lazy, virtual chunk refs) -> scalar-sel init_time,
  timedelta sel lead_time, level, lazy bbox index-cut, per-variable compute. Any missing
  model/init or ANY store exception -> `_StoreMiss` -> `_open_direct` (covers inits newer
  than the store's weekly `update --days 120` and mid-rebuild windows; mlwp-store did a full
  rebuild 05:28-05:53 during which the fallback served all reads silently).
- `available_inits` = union(direct bucket listing, store `list_inits`).
- Verified equivalence vs direct on live inits (GRAP 2026-11-04 00Z t2/q; FOUR_v200_IFS
  2026-11-04 12Z t/r/z, bbox+leads): values bit-identical incl. NaN placement, final dims/
  coords identical; store read 1.9 s vs direct 51.8 s for a bbox subset (27x).
- FOUR_v200_IFS lacks q/w/apcp in the RAW files (store faithful; registry already models this).
- Store build skips ~5 corrupt upstream objects (metadata-checksum errors, incl. FOUR/GRAP
  00Z inits); direct reader fails on those too — no coverage lost.
- `aiwp.py` DOES use the store (primary path). Answers the earlier DONE caveat.

Post-switch test results (2026-11-06 06:20 UTC):
- unit suite 12 passed; network suite (`-m network`) 12 passed in 292 s over the store path
  (was 421 s direct); end-to-end `open_forecast('pangu_weather', 2026-11-05 12Z, ...)` cold
  1.0 s via store (direct ≈50 s), zarr cache roundtrip exact, `make_ewb_forecast` OK.

## UPDATE (2026-11-06 07:05 UTC): available_inits honors the store's known-bad ledger
`aiwp.available_inits` filters direct-listing hits against `mlwp_icechunk.known_bad(model)`
(13 permanently-truncated IFS objects; store ledger retried every 14 days) — those inits
fail on BOTH read paths, so they must not be offered. Verified zero leakage over
2026-09-01..10-31 for all 4 IFS-forced models; unit 12 passed, AIWP network tests 9 passed.

---

## UPDATE 2026-11-17 (agent `drop-subsets`) — SUPERSEDES the subset-cache statements above

- The materialised subset cache under the retired subsets tree and its
  eviction (`ewb_live.sources.cache`) are REMOVED (owner ruling: all CIRA MLWP
  evaluation via the virtual store; tree deleted on ewb, 231 G freed).
- AIWP/MLWP reads are now virtual-store-ONLY through the single choke point
  `ewb_live.sources.aiwp.open_mlwp_forecast` (Icechunk refs, lazy bbox/lead
  cut in memory, bounded 2 concurrent sessions); the direct HDF5 fallback is
  gone. `open_forecast` repeat calls are memoised in-process (bounded LRU,
  `EWB_LIVE_MEM_CACHE_MB`), nothing is written to disk.
- ECMWF/AIGFS backends unchanged (byte-range GRIB). Full inventory +
  enforcement: `notes/mlwp_virtual_store_audit.md`,
  `tests/test_mlwp_store_guard.py`.
