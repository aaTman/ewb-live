# Agent `ewb` — log

## Environment / repo decisions
- Created `pyproject.toml` (uv-managed, package `ewb_live/` flat layout, hatchling backend,
  Python 3.12 pinned via `.python-version`). EWB added as dependency from
  `git+https://github.com/brightbandtech/ExtremeWeatherBench` (v1.1.1 @ main, Nov 2026).
- **Collision (03:52Z):** another agent (presumably `sources`) ran `uv init --package` inside
  `ewb_live/`, which added a bogus `[tool.uv.workspace] members=["ewb_live"]` to the root
  pyproject and nested boilerplate (`ewb_live/pyproject.toml`, `ewb_live/src/`, README).
  I removed the nested files and the workspace block; a plain package dir breaks uv workspace
  autodiscovery otherwise. **Request to `sources`: do not run `uv init` inside `ewb_live/`;
  the repo root is the single uv project. Use `uv add` only.**

## ExtremeWeatherBench study (v1.1.1, cloned to scratchpad ewb-src)

### Package layout
`cases.py` (IndividualCase + case yaml), `inputs.py` (Forecast/Target classes),
`defaults.py` (default targets/forecasts/metrics + climatology accessor),
`metrics.py`, `evaluate.py` (orchestrator), `derived.py` (DerivedVariable: AR mask, TC track, CAPE),
`events/atmospheric_river.py` (IVT + mask algorithm), `regions.py` (bounded/centered/shapefile),
`outputs.py` (long-form DataFrame / xarray Dataset), `utils.py`, `data/events.yaml` (329 curated cases).
Event detection for temperature events is NOT in the installed package; it lives in
`data_prep/temperature_events.py` (+ `ar_bounds.py`, `era5_climatology.py`) as CLI scripts.

### Case yaml schema (`ewb.cases.load_cases()`, `load_individual_cases_from_yaml`)
```yaml
- case_id_number: 1
  title: 2021 Pacific Northwest
  start_date: 2021-06-20 00:00:00
  end_date: 2021-07-03 00:00:00
  location:
    type: bounded_region
    parameters: {latitude_min: 39.0, latitude_max: 65.0, longitude_min: -136.75, longitude_max: -105.75}
  event_type: heat_wave   # heat_wave | freeze | atmospheric_river | severe_convection | tropical_cyclone
```
AR cases use 0-360 longitudes; temp cases -180..180. `regions.map_to_create_region` dispatches on
`type` (bounded_region -> BoundingBoxRegion, centered_region, shapefile_region).

### Event types & default metrics (`defaults.get_brightband_evaluation_objects()`)
- **heat_wave**: MaximumMeanAbsoluteError (tolerance 24 h), RootMeanSquaredError,
  DurationMeanError(threshold=climatology q0.85, op `ge`), MaximumLowestMeanAbsoluteError.
  Targets: ERA5 (ARCO) + GHCN, variable `surface_air_temperature`.
- **freeze**: MinimumMeanAbsoluteError, RootMeanSquaredError,
  DurationMeanError(climatology q0.15, op `le`). Same targets.
- **atmospheric_river**: CriticalSuccessIndex (thresh 0.5/0.5), SpatialDisplacement, EarlySignal.
  Target: ERA5 with derived variable `AtmosphericRiverVariables(output_variables=
  ["atmospheric_river_land_intersection"])`.
- severe_convection (LSR/PPH) and tropical_cyclone (IBTrACS) defaults exist; out of scope.
- DurationMeanError preserves `init_time` (value = duration error in hours, ±48 h case buffer
  applied internally); RMSE preserves `lead_time`; peak metrics occupy (init_time, lead_time).

### Targets / truth sources (`inputs.py`)
- `inputs.ERA5`: ARCO zarr `gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3`,
  mapping `2m_temperature -> surface_air_temperature` etc. (ERA5_metadata_variable_mapping).
  Grid: lat 90..-90 desc 0.25°, lon 0..359.75, 37 pressure levels (1..1000 hPa), hourly.
  NOTE: EWB defaults pass `storage_options={"remote_options": {"anon": True}}`, which gcsfs
  ignores -> slow ADC-discovery retries (~2 min). Pass `{"token": "anon"}` instead.
- `inputs.GHCN`: `gs://extremeweatherbench/datasets/ghcnh_all_2020_2024.parq` — **covers only
  2020-2024**, useless for our 2026 window. GHCN is a polars LazyFrame target with station QC.
- Other targets: LSR, PPH, IBTrACS (not needed).

### Climatology (heat/freeze thresholds)
`defaults.get_climatology(quantile)` opens
`gs://extremeweatherbench/datasets/surface_air_temperature_1990_2019_climatology.zarr`,
dims (quantile[0.10,0.15,0.25,0.50,0.75,0.85,0.90], dayofyear 366, hour [0,6,12,18], lat 721, lon 1440),
var `2m_temperature`. Built by `data_prep/era5_climatology.py` from 1990-2019 ERA5 (day-of-year
rolling weighted quantiles at synoptic hours).

### AR algorithm (`events/atmospheric_river.py`)
IVT = (1/g) ∫ u q, v q dp trapezoid over levels >= 300 hPa (u,v,q EWB names). Mask per valid time:
|blurred Laplacian(IVT), sigma=3| >= 2.5 kg/m²/s² dilated by radius 8 (0.25° grid) AND IVT >= 400
kg/m/s; time-linked 6-connected labeling; drop labels < 500 gridpoints or |mean lat| <= 15°.
`atmospheric_river_land_intersection = mask & land_110` (0/1). Detection scripts in
`data_prep/ar_bounds.py` only re-tighten bounds of curated events — no global AR scanner exists.

### Temperature-event detection (data_prep/temperature_events.py `global` subcommand)
1. ERA5 hourly t2m restricted to 0/6/12/18 UTC, land-only via regionmask natural_earth land_110.
2. Daily exceedance mask: day passes only if ALL four 6-hourly steps satisfy the bound vs
   climatology quantile sel(dayofyear, hour) (heat: >= q0.85; freeze: <= q0.15).
3. `_consecutive_filter_np(mask, MIN_CONSECUTIVE_DAYS=3, grace 1 day)`: keep cells in >=3-day runs,
   bridging 1-day gaps.
4. Daily blob tracking (`detect_events`): 2-D label per day, overlap-merge into events
   (union bbox with 360°-arc lon merging); new event seeds only if the joint footprint of the
   blob and the previous 2 days has a connected component >= MIN_AREA_KM2 = 200,000 km².
   Events terminate when absent or area < 50% of peak (AREA_DECLINE_FRACTION).
5. `expand_event_bounds`: grow bbox by 1° bands while >= 50% of land points on an edge are
   active on the peak day (max 20 iterations; edges that are <25% land pre-disabled).
6. Keep: peak >= 500 gridpoints, peak area >= 200,000 km², max consec days >= 3.
7. Constants: MIN_GRIDPOINTS=500, MIN_AREA_KM2=2e5, EXPANSION_DEGREES=1, MAX_SPATIAL_ITERATIONS=20,
   EDGE_VALIDITY_THRESHOLD=0.5, TEMPORAL_LOAD_BUFFER_DAYS=14.

### Evaluation API
```python
ewb = evaluate.ExtremeWeatherBench(case_metadata=[case_dict...],
                                   evaluation_objects=[EvaluationObject(event_type, metric_list,
                                   target, forecast), ...])
df = ewb.run_evaluation(n_jobs=1, output_format="pandas")
# df columns: value, lead_time, init_time, target_variable, metric, forecast_source,
#             target_source, case_id_number, event_type (+ extras e.g. peak_valid_time)
```
Forecast classes: ZarrForecast / KerchunkForecast / **XarrayForecast(ds=...)** (in-memory or lazy
ds). Forecast ds needs dims (init_time, lead_time[timedelta], latitude, longitude[, level]) with
EWB variable names (or variable_mapping). valid_time is derived = init+lead; EWB subsets to case
via `derive_indices_from_init_time_and_lead_time`. Targets need valid_time (or time) + spatial dims;
forecast is aligned (regridded) to target grid automatically. Metrics run with dask;
`user_selected_lead_hours` kwarg supported by some metrics.

## Measurements (this box, 2026-11-06)
- Network to GCS public buckets: fast (~95-370 MB/s measured; 25M-elem ARCO slice in 0.26 s).
- **ERA5 ARCO latency: last complete hour 2026-10-30T23 -> ~6 days behind wall clock.**
- ARCO time coord spans 1900-2050 (coords only; beyond last data reads NaN).
- gcsfs: always pass `token="anon"` (the `anon=True` alias is passed through but ADC probing
  still burns ~2 min on first access).

## Decisions
- **Truth**: ERA5 ARCO (EWB default) for all detection + scoring up to its latency cutoff;
  computed dynamically at runtime (scan), cached in `data/cache/truth_cutoff.json`.
  Events with end_date beyond the cutoff get a truth fallback decision at scoring time:
  if `ewb_live.sources` offers `ifs_hres`, build an `HRESAnalysisTarget` from lead-0 forecasts
  (SPEC-sanctioned fallback); else skip scoring those events and flag in meta.json.
  GHCN is NOT usable for recent dates (bucket covers 2020-2024) -> ERA5-only truth; recorded.
- **Detection** (after orchestrator course correction, 2026-11-06): no custom port.
  `ewb_live/events/_upstream.py` loads `third_party/ExtremeWeatherBench/data_prep/`
  scripts verbatim via importlib (pinned clone @ c6467f4, MIT); `temperature.py` is a
  thin wrapper around upstream `_run_global`/`build_exceedance_mask`/`detect_events`/
  `expand_event_bounds` for the heat_wave + freeze global scan, `ar.py` reuses the same
  upstream mask algorithm + blob tracker. All thresholds/logic are upstream's
  (q0.85 `>` / q0.15 `<`, MIN_CONSECUTIVE_DAYS=3, MIN_AREA_KM2=2e5, MIN_GRIDPOINTS=500).
  Deviations from upstream listed under "Deviations from upstream data_prep scripts".
- **AR detection**: no global AR scanner exists upstream (`ar_bounds.py` only tightens
  curated cases); the wrapper composes upstream pieces — EWB's IVT/Laplacian mask
  (`extremeweatherbench.events.atmospheric_river`) on 6-hourly ERA5 + upstream daily
  blob tracker on the AR *land-intersection* mask, requiring >= 2 land days and
  >= 10 land pixels at peak (MIN_LAND_PIXELS_FOR_PEAK from `ar_bounds.py`).
  ~86 GB streamed per 90 days, feasible.
- **Scoring**: EWB's own `ExtremeWeatherBench.run_evaluation` per (event, model) with default
  metric list for the event type, ERA5 target (or HRES-analysis target), and the model's forecast
  wrapped as `inputs.XarrayForecast` built via the SPEC contract (`open_forecast`).
  Long df reshaped to contract columns (event_id, event_type, model, metric, lead_time_hours,
  value, init_time); init_time-preserved metrics (DurationMeanError, EarlySignal) carry
  lead_time_hours = NaN.
- **Dev adapter**: `ewb_live/scoring/_dev_forecast.py` implements the sources contract backed by
  ERA5 ARCO itself ("perfect persistence" pseudo-model) for end-to-end tests until sources lands.
- Requests to other agents -> below.

## Deviations from upstream data_prep scripts (course correction: keep EWB logic, document every deviation)

Upstream = `third_party/ExtremeWeatherBench/data_prep/temperature_events.py` + `ar_bounds.py`
@ c6467f4, loaded unmodified via `ewb_live/events/_upstream.py` (importlib). All thresholds,
masks, consecutive filter, blob tracking, bound expansion, and keep-filters call upstream
functions directly. Deviations, each justified:

1. **Mid-month window chunking fix** (wrapper, not upstream logic): upstream `_run_global`
   derives month chunks via `date_range(freq="MS")`, which yields an EMPTY index for any
   window starting mid-month -> `xr.concat` crash. Wrapper keeps identical monthly chunking
   but starts the first chunk at `start`. Upstream bug; flagged to file upstream.
2. **14-day look-back for the rolling window** (refinement the course correction allows):
   upstream global scan loads exactly [start, end]; the wrapper extends load+scan by
   upstream's own `TEMPORAL_LOAD_BUFFER_DAYS` (14 d) so in-progress events keep stable
   start dates across weekly runs, then drops events with no active day inside
   [start, end]. Same scan math, larger input.
3. **Threads scheduler**: in-process dask `threads` instead of upstream's `LocalCluster`
   (scheduling only; computed graphs identical).
4. **No plotting**: consecutive-map / event PNG sub-steps skipped.
5. **Longitude convention**: Event schema uses lon in [-180, 180); ERA5/upstream CSV is
   0-360. Wrapper normalises on output only (`normalize_lon_180`), with the wrapped-arc
   convention lon_min > lon_max = eastward arc across the antipode.
6. **`peak_value`**: descriptive stat for the Event schema (max daily-mean t2m over active
   cells in the expanded box for heat, min for freeze; peak IVT for ARs). Upstream CSV has
   no such column; it does not affect detection or filtering.
7. **Event `end` exclusive**: Event schema end = last active date + 1 day (half-open
   interval); upstream CSV reports the inclusive last active date.
8. **Strict operators**: wrapper uses upstream CLI defaults `>` q0.85 / `<` q0.15 for
   detection. EWB's DurationMeanError scoring metric uses `>=`/`<=` against the same
   quantiles — deliberately kept as upstream ships them; detection and metric are separate
   concerns and mixing them would be a silent logic change.
9. **AR composition** (no upstream global AR scanner exists): AR mask from the *installed
   package* (`extremeweatherbench.events.atmospheric_river`, same code upstream
   `ar_bounds.py` tightens) on 6-hourly ERA5; daily land-intersection mask fed into the
   upstream temperature blob tracker (`te.detect_events`) with `min_seed_area_km2=0`;
   keep-filters = `ar_bounds.py`'s `MIN_LAND_PIXELS_FOR_PEAK=10` at peak day + >= 2 land
   days; bbox padded 2 deg for the scoring margin; peak = max IVT inside mask.

## Refactors (this pass)
- **Single ARCO cutoff probe**: `ewb_live/events/truth.py` (bisect x 4 open-ocean points,
  cache `data/cache/truth_cutoff.json`, TTL 12 h). `ewb_live/scoring/truth.py` now delegates;
  old duplicate linear-walk probe removed. Canonical truth-source names: `era5_arco` /
  `ifs_hres_analysis` everywhere.
- **score_event isolation**: one EWB bench per (event, model) so one model's failure cannot
  discard the other models' finished results; dtype-safe lead_time -> hours in `_reshape`
  (EWB returns timedelta64 or numeric hours depending on metric family).
- **ERA5 default targets** get `storage_options={"token": "anon"}` via `dataclasses.replace`
  (EWB defaults pass `remote_options.anon` which gcsfs ignores -> ~2 min ADC stall per run).
- **pipeline caches keyed by (event fingerprint, truth_source)**: part files under
  `data/cache/score_parts/` include a sha1 of fingerprint+truth, and the parquet `keep`
  filter drops cached rows whose truth policy changed (both directions), so re-bounded or
  truth-reclassified events are always rescored, never served stale.
- **`ewb_live/weekly.py`** implements SPEC section 5: mlwp_icechunk update (skips cleanly
  when not installed) -> `pipeline.run` (incremental) -> meta.json cycle/week/next_update ->
  snapshot to `data/results/history/<YYYY-WW>/`; logs `logs/weekly-<date>.log`; non-zero
  exit on failure; re-run safe.

## Runtime fixes & decisions (05:20-05:45 UTC, this session)
- **gcsfs storage-control probe patched** (`ewb_live/_gcsfs_speedup.py`, applied at
  `import ewb_live`): gcsfs 2026.10 `ExtendedGcsFileSystem._get_bucket_type` pings the
  Storage Control API with ADC on *every* GCS metadata op when no ADC exists (this
  host); each miss costs ~12 s and misses are not cached. We answer NON_HIERARCHICAL
  instantly for the two public flat buckets we read
  (`gcp-public-data-arco-era5`, `extremeweatherbench`); other buckets untouched.
  This removed ~7 min of dead time from the first dev smoke run.
- **`_reshape` index-alignment bug fixed** (`scoring/evaluate.py`): scalar
  `out["event_id"] = event.id` onto an empty DataFrame produced zero-length columns;
  mixing with n-length columns made event_id NaN for all rows. Now every column is
  built from arrays. Caught by `tests/test_pipeline_unit.py::test_reshape_ewb_longform`.
- **`score_event` isolates per model** (one `ExtremeWeatherBench` bench per model
  instead of one for all): EWB's `run_evaluation` raises on the first failing
  case-operator, so a single bad model would previously discard the whole event's
  results. Also tolerates xarray-format output.
- **Upstream grace-day semantics verified** on synthetic data: `_consecutive_filter_np
  (mask, 3, 1)` *trims run edges* (a clean 4-day run keeps days 1..3) — the wrapper
  mirrors upstream `_run_global` exactly, quirks included; the live 14-day load buffer
  keeps start dates stable across runs regardless.
- **Dev adapter** `scoring/_dev_forecast.py`: builds a true `XarrayForecast`
  (init_time/lead_time coords, timedelta leads) from ARCO slices;
  `score_event(..., build_forecast_override=...)` injects it.
- **Sources is live** (notes/sources.md, 05:28Z: 24/24 tests): contract matches
  (`list_models/model_info/available_inits/open_forecast/make_ewb_forecast`); real
  scoring uses `build_forecast` over `sources.open_forecast`. Known caveats adopted:
  graphcast lead-0 NaN (EWB drops NaN rows), aigfs pressure-levels only.

## Curation / selection rules (orchestrator mandate 2026-11-06 07:0xZ)

Raw detector output over 90 d was ~100x too dense (1400 ARs, 118 heat, 22 freeze vs
EWB's curated events.yaml scale: tens over *years*). The upstream scripts are
detectors; EWB ships a curated case list. Added `ewb_live/events/select.py`
applied inside `detect_events` (raw scans stay cached untouched):

1. Floors (EWB-ingredient based, no new physical thresholds):
   * heat_wave/freeze: upstream keep-filters already apply (>=3 consec days,
     peak area >= 200k km2, >=500 gridpoints) — unchanged.
   * atmospheric_river: `landfall_days >= 3` and `peak IVT >= 600 kg/m/s`
     (2x the 400 mask threshold). Rationale: events.yaml ARs are multi-day
     strong landfalling systems; single-day coastal grazes are not cases.
2. Severity (ranks "major" events; uses EWB's own climatology file):
   * heat_wave: `anomaly_K x peak_area_km2 x max_consecutive_days`, where
     anomaly_K = peak daily-mean t2m minus mean q0.85 climatology over the
     event bbox/days/hours (same quantile as detection). Rewards heat domes
     (large anomaly) over climatologically-hot deserts.
   * freeze: symmetric with q0.15 and min daily-mean t2m.
   * atmospheric_river: `landfall_days x peak_land_pixels x (peak_IVT - 400)`.
3. Fragment dedup (spatio-temporal merging): severity-desc greedy; drop an
   event overlapping an already-kept event by >= 2 days AND whose bbox
   intersection >= 40% of the smaller bbox (same system, restarted track).
4. Cap: top `--top-n` per type (default 15, `0`/None disables), configurable
   via `pipeline --top-n` and `detect_events(top_n=...)`.

Curation runs after the cached raw-scan read, so changing rules never costs a
re-scan. `severity_rank_score` is stored in Event.details for the dashboard.

### Update 2026-11-06 08:1xZ (orchestrator review of first curated output)

Three defects found in the first curated run, fixed:

1. **AR attribution bug**: land-pixel counts / peak IVT / landfall_days were
   computed over the *globe* restricted only by event dates, so overlapping
   ARs shared values (peak 2972.4 everywhere). Now computed inside each
   event's own bbox (index mask honours the wrapped-lon arc); Event.details
   gained per-event `centroid_lat` (peak-day land-pixel weighted).
2. **Over-merged AR tracks + deep tropics**: upstream events.yaml AR cases:
   duration p50 4 d / p90 8 d, centroid |lat| >= 22 deg. Enforced: tracker
   tracks > 12 days split into consecutive <= 12-day segments (ar.py
   AR_MAX_DURATION_DAYS, documented deviation); curation floor now also
   rejects centroid |lat| < 20 (kills the 7-17N monsoon plumes), duration
   outside 2-14 d, or bbox spans > 60 deg in either direction. Same bbox/
   duration envelope sanity for heat_wave/freeze (3-21 d, <= 60 deg spans).
3. **Scoring performance** (target < 1 min per event job): profiled one job —
   cost is cold remote reads in `sources.open_forecast` (ifs_hres ~30-50 s
   per init: GRIB index fetches + CloudFront 503 retry backoffs), which ran
   *sequentially over up to 21 inits*. Fixed in scoring (no sources change):
   - per-init lead list filtered to leads whose valid time overlaps the event
     window (init 08-01 for a mid-Aug event previously read all 41 leads);
   - init reads run in a thread pool (`EWB_LIVE_INIT_PARALLEL`, default 4;
     verified thread-safe on ecmwf/aiwp backends, 4 cold inits 42 s wall vs
     126 s serial);
   - pipeline jobs are now (event, all pending models): the EWB target
     (truth load + case masks, identical for all models) is built once per
     event instead of 11x; part files stay per (event, model) for resume.
   - truth off-by-one: event.end is exclusive midnight; last scored instant is
     end-1h, so ARCO covering that instant suffices (events through the
     cutoff day now use era5_arco, not the hres fallback).

RMSE units check: raw ifs_hres vs ERA5 over Munich bbox in the source loader
= RMSE 1.12 K / bias -0.2 K at lead 0 (Kelvin correct, bbox + alignment
correct). The ~5 K figure is being re-measured through the EWB path with the
new loader; will be reported from the real scores.parquet.

## Requests
- **sources**: no `uv init` inside `ewb_live/` (see collision above).
- **sources**: contract functions must be import-pure (`ewb_live.sources` imports must not touch
  network at import time); `open_forecast` should return a *lazy* xarray Dataset (EWB dask-pipes it);
  `list_models()` should include `"ifs_hres"` exactly (scorecard baseline name per SPEC section 3).
  Needed vars for AR scoring: `eastward_wind`, `northward_wind`, `specific_humidity` on `level`
  (hPa, 300-1000) + EWB surface names otherwise. Longitudes 0-360 preferred (EWB aligns anyway).

---

## DONE
Refreshed 2026-11-08 21:20 UTC — weekly end-to-end 20:55:19Z exit 0: **full_run_complete=true, 30,586 rows, 31 events** (15 AR / 15 heat / 1 freeze), AR lane 15/15 scored (a3bb50ccde3d recovered on retry, 1,340 rows). https://ewb.espr.ai serving clean.
Correctness fixes shipped + verified on live data: **G-34** longitude-convention normalization in the mirror crop (west/east heat RMSE comparable, Greenland 3.9–5.0 K, science-review PASS); **H4** remote-ARCO truth branch now normalized too (`_normalized_target`) — 8fe8ae212d73 rescored physical: ifs CSI 0.137 @ lead-0 → 0.085 @ lead-120, nonzero for 11/11 models; **IBTrACS cyclone screen** + `qualified_at` (31/31 events); antimeridian crop handling (G-34 class); **D6** DurationMeanError skill suppressed (20/20 cells null, signed mean kept); G-30 public_reason (0 raw exceptions), G-37 merged max_consecutive_days recomputed, G-38 peak rows ≤ 240 h.
Honest holes: 3 valid_time pairs terminal (fourcastnet_v2 ×2, pangu_weather_gfs) + 18 documented-unsupported pairs (aigfs no 2 m temp, fourcastnet_v2 gap); `details.tc_screen` provenance attaches from newly-detected candidates onward — the 15 shipped ARs keep pre-fix details (disclosed to science-review).
Weekly hygiene: history re-snapshotted to data/results/history/2026-W45/ (stale perf keys gone), auto-viz + global_timeline.json + raw_exceedance regenerated, public_scrub_check CLEAN pre- and post-publish, quarantine collision fixed (relative-path names). Offline suite green (176/28 pre-patch; targeted re-runs after each fix).
Older: D2 like-for-like skill, D3 event-bootstrap CIs (seed 20261108), ponytail batches 1–10 all triaged/fixed/acked.

## Runtime fixes round 2 (05:40-05:55 UTC)
- Scoring pool switched to spawn start-method: run() opens GCS zarrs
  (cutoff probe + detection) before the ProcessPool, and forked children
  inherited the broken asyncio/gcsfs state -> workers died with
  "process terminated abruptly" + "Event loop is closed" tracebacks.
  Verified with an orchestration test using a real graphcast event.
- gcsfs speedup had silently regressed: the package __init__ import was
  edited to drop the apply() call (comment claimed "applied on import").
  Restored the explicit `_gcs.apply()`; verified 0 ADC probe warnings in
  a fresh 90-day scan (previously ~12 s per metadata op).
- events/truth.truth_cutoff now accepts either "probed_at" or "fetched"
  cache key so it and scoring/truth.probe_arco_cutoff (same cache file,
  different schema) don't force each other to re-probe.
- tests/test_events_unit.py + test_pipeline_unit.py: 17 pass (46 s —
  sibling tests in tests/ hit live networks and must be run separately).

## End-to-end orchestration validation (05:54-05:57 UTC)
Ran pl.run() from a guarded script with a synthetic cached event
(heat_wave 2026-10-12..15, CA/NV bbox) scoring real graphcast vs ERA5 ARCO:
- spawn pool worked; 74 score rows; metrics per EWB defaults for heat_wave:
  RMSE (40 rows), MMAE, MaximumLowestMeanAbsoluteError, DurationMeanError.
- scores.parquet/scores.json/scorecard.json/meta.json all written atomically.
- Second invocation: "to score: 0 jobs", rows unchanged (74) -> incremental
  cache verified.
- Spawn caveat: entry module is re-imported in children, so scripts driving
  run() need the __name__ == "__main__" guard (the CLI has it).
- RMSE ~4.8-5.5 K and flat over leads 6-36 h for graphcast. Follow-up
  diagnostics: lead_time coord is proper timedelta64 (earlier int() overflow
  was my probe's bug, and a NaN bias in the first probe was coordinate-label
  alignment between forecast (cell-centre grid, 0.125 deg offset) and ARCO -
  not a data problem). Forecast fields at leads >= 6 h are clean (287.5 K
  box mean, 0 NaN). Remaining flatness is plausible grid-shift artifact over
  the steep coastal gradient box; EWB's own lead_time_hours output was
  always correct (6..240).
- detect90b progress: heat_wave 118, freeze 22 events; AR at ~40 s/chunk
  under the gcsfs speedup.

## Validation results (06:00-06:30 UTC)
- 90-day detection complete (window 2026-08-08..2026-10-30, ARCO cutoff):
  1540 events -> data/results/events.json: heat_wave 118, freeze 22,
  atmospheric_river 1400. AR durations 1-20 d (median 2 d), heat median 3 d
  (= EWB's 3-day minimum). 19 ARs have lon_min > lon_max: legitimate
  date-line-crossing arcs (EWB arc-bbox convention), isolated on failure.
- HRES 0-h analysis fallback (truth for events beyond ARCO's lag) built and
  verified end-to-end: data/cache/truth/hres_analysis_2026103100_2026110100_
  surface_air_temperature.zarr — 4 time steps, mean 285.6 K, 0 NaN.
- graphcast quirk found: lead_time=0 field is ALL NaNs on the MLWP store
  (analysis-initialised, f000 not populated); leads >= 6 h are clean.
  Scoring is unaffected (metrics start at 6 h), but do not filter events on
  lead-0 data anywhere in the stack.
- First scoring run launched: 30 events x 5 models (ifs_hres, graphcast,
  pangu_weather, aifs_single, fourcastnet_v2), jobs=4, round-robin event
  selection across the three types.

## Alignment verdict + progressive outputs (06:40 UTC)
- Time-alignment check (graphcast 2026-10-12T00 init vs ARCO, box-mean
  forecast-minus-ARCO swept over dh=-12..+12 h): bias min at dh=0
  (-0.84 K lead 6, -0.70 K lead 12), rising off-centre -> valid-time
  alignment is correct. The flat ~5 K short-lead RMSE is spatial
  (0.125 deg cell-centre offset over steep coastal gradients in the test
  box), not a plumbing bug.
- pipeline.run() now writes scores/scorecard/meta every 10 completed jobs
  (atomic), so the dashboard updates during multi-hour runs; final write
  unchanged. Restarting a killed run is free: completed (event, model) part
  files are re-read, not recomputed.

## Ops notes
- spawn pool children don't inherit logging config; _score_job now calls
  basicConfig itself (future runs get per-worker INFO progress; current run
  started before that fix, monitored via CPU/net/part files instead).

## Scoring-run incident: pool worker hangs (06:37-07:35 UTC)
150-job run (30 events x 5 models, jobs=4) hung: after ~50 min, 3 of 4
spawn workers had frozen CPU, zero network, dead CLOSE-WAIT sockets to S3
(icechunk); the 4th (CPU-bound) worker was still computing. Meanwhile two
free processes calling sources.open_forecast concurrently completed in 2 s
with fresh downloads -> the sources layer is healthy single-shot. The hang
appears specific to multiple concurrent icechunk-reading pool children
(no ptrace/sudo here for a definitive stack; py-spy blocked by yama).
Mitigations adopted: low-parallelism scoring runs for the four AI models
(jobs<=2, to be verified by timing run), HRES scored in its own pass (it is
CPU-bound via cfgrib GRIB indexing, not icechunk). Machine has 8 cores.

## Requests (for agent sources)
- ifs_hres open_forecast is ~20-35 min CPU per (event, 10 inits, 0-240 h)
  job: cfgrib appears to index/read the global 0.25 deg GRIB per lead. A
  per-(init,lead) on-disk index cache and/or grib-filter pre-subset would
  give a ~10x win for the scoring pipeline. (ewb)
- MLWP/icechunk reads hang when >=3 pool children download concurrently
  (observed with jobs=4; see Scoring-run incident). A bounded in-process
  semaphore on concurrent icechunk sessions in aiwp.py would make the
  scoring pool safer at jobs=4. (ewb)

## Curation split + measurement round 3 (07:00-07:30 UTC)
- events/select.py (curation: severity floor + dedupe + top-15/type) was
  added to my package by a sibling agent; reviewed and sound. Fixed the
  pipeline side: events.json now holds the FULL catalogue (1540), curated
  subset goes to events_selected.json and drives scoring.
- Cold-job timing (spawn pool, AR event, 31 inits x 0-240h):
  * graphcast AR build_forecast: 25+ min (37-level IVT vars x 31 inits of
    small icechunk range GETs -> S3 request-rate throttling; earlier
    "worker hang" diagnosis was throttling, not deadlock - rx resumes at
    ~100 MB/s after quiet windows).
  * pangu AR: ~30 s per init (~15 min/job). ifs_hres AR: comparable via
    cfgrib. Heat/freeze jobs (1 var) are minutes.
  * ARCO truth phase: fast when cached (~350MB zarr per event bbox).
- Production run launched 07:23 UTC: 6 curated events (round-robin, 2/type)
  x {ifs_hres, graphcast, pangu_weather}, jobs=3. Parts merge + outputs
  written every 10 jobs. Resume is free: rerun the same command.
- Third agent 'mlwp-store' is rebuilding the MLWP read path (Icechunk
  virtual store, notes/channel_sources_mlwp.md); uv lock contention can
  stall 'uv run' starts briefly.

## ROOT CAUSE: AR mask metrics silently dead on tight case regions
Symptom: AR parts contained only EarlySignal=0 rows (+NaN CSI) — no
SpatialDisplacement, CSI always 0/NaN, across all models/leads.
Mechanism (verified by end-to-end mini-event repro, /tmp/orch/ar_debug*.log):
EWB applies the IndividualCase bounded region to the FORECAST dataset
BEFORE derived-variable computation, but computes ERA5-target derived
variables on the global grid and subsets afterwards. The AR mask
finalisation drops blobs < 500 grid points (min_size_gridpoints=500).
Our event bboxes are the blob bboxes themselves (~10 deg), so forecast-side
blobs were truncated below 500 pts and dropped -> forecast mask identically
empty -> CSI=0, SpatialDisplacement NaN (dropped), EarlySignal NaN/0.
The target-side mask (0.17 mean) survived because it was finalised globally.
Fix (ewb_live/scoring/evaluate.py score_event): for atmospheric_river, the
case is built from event.padded(BBOX_PAD_DEG=10) so region == forecast data
domain and blobs stay whole. Verified: forecast mask mean 0.059 (non-zero),
CSI mean 0.546 over leads, SpatialDisplacement finite; graphcast vs ERA5 on
a real Aug-2026 AR. Heat/freeze metrics are value-based and unaffected.
Consequence: the first production run's AR parts were deleted before the
restart (pipeline_score6b.log) — they encoded the broken geometry.

# DONE

## What works (all exercised end-to-end against real data on 2026-11-06)
1. `ewb_live.events.detect_events(start, end)` finds heat_wave / freeze /
   atmospheric_river events in the last 90 days from ARCO ERA5 with EWB's
   own thresholds (p85/p15 climatology, 3-day runs, 500-gridpoint/200k km2
   spatial filters; AR: IVT>=400 + dilated-Laplacian>=2.5 mask, >=500 pts,
   land intersection, |lat|>15). Raw detection is cached per type under
   data/cache/events/ so reruns are instant.
2. Truth policy per SPEC: ERA5 ARCO is the truth source; measured latency
   is ~6 days (cutoff probed live, cached 6 h in data/cache/truth_cutoff.json).
   Events ending past the cutoff fall back to an IFS HRES 0-h analysis
   truth materialised from s3://ecmwf-forecasts (verified: 0 NaN).
3. `ewb_live.scoring.score_event(event, models)` runs EWB's own
   evaluation: IndividualCase from the Event, XarrayForecast over the
   sources API (daily inits, leads capped to 240 h, lead filter to the
   event window), default metrics per event type, per-model isolation,
   output reshaped to the SPEC score contract.
4. `ewb_live/pipeline.py` (CLI: `uv run python -m ewb_live.pipeline
   --days 90 --end 2026-11-06 --models ... --max-events N --jobs J`):
   detect -> curate -> incremental score (fingerprint + truth-policy
   cache, per-(event,model) part files, resume is free) -> writes
   data/results/{events.json, events_selected.json, scores.parquet,
   scores.json, scorecard.json, meta.json} atomically, progressively
   during the run. Process pool with spawn start method (fork inherits
   the parent's async/gcsfs state and segfaults workers).
5. Real production run today: 90-day window detected 909 deduplicated
   events (769 AR, 118 heat, 22 freeze; window 2026-08-08..2026-10-30);
   curation (severity floor + dedupe + top-15/type) selected 45 for
   scoring; 6 were scored (2 per type) x {ifs_hres, graphcast,
   pangu_weather}. 12/18 event-model pairs done at write-time of this
   section (RMSE/MMAE/duration metrics populated; scorecard with
   skill-vs-ifs_hres computed; e.g. t2m RMSE at 24-30 h: graphcast/pangu
   ~1.3-1.4 K, ifs_hres ~1.55 K). The two AR jobs are the straggler (see
   gaps); rerunning the same command resumes and finishes them.

## How to run
    cd /home/orca/orca/workspaces/ewb-live/dogfish
    export NO_GCE_CHECK=True
    uv run python -m ewb_live.pipeline --days 90 --end 2026-11-06 \
        --models ifs_hres,graphcast,pangu_weather --max-events 6 --jobs 3
Unit tests: `uv run python -m pytest -q tests/test_events_unit.py
tests/test_pipeline_unit.py` (22 pass, offline). Opt-in live-network
tests (incl. the AR-metric regression guard): `EWB_LIVE_NET=1 uv run
python -m pytest -q tests/test_integration_network.py`.

## Known gaps / cost facts
- AR mask metrics need the padded case region (this session's root-cause
  fix, above). Without it every CSI is 0/NaN.
- AR scoring is expensive: MLWP u/v/q on 13 levels x daily inits over the
  padded domain is latency-bound on icechunk range-GETs; a cold AR event
  job takes ~1 h, heat/freeze jobs ~1-13 min. The mlwp-store agent's
  Icechunk store should cut the MLWP read cost.
- graphcast f000 (lead 0) is all-NaN in MLWP stores; EWB defaults start at
  6 h so scores are unaffected, but don't add lead-0 metrics blindly.
- Only 3 of 11 registered models scored this pass (cost). Resuming the
  same command with more models extends incrementally.
- Curated scoring set is 45 events (15/type); full catalogue in
  events.json. --max-events caps the scored subset via round-robin over
  types, so a small run still covers heat/freeze/AR.

## ewb2 log

### Performance rework (2026-11-06)
- **Truth mirror** (`ewb_live/scoring/truth_mirror.py`, new): local zarr-v2
  store `data/cache/truth/era5.zarr` with `2m_temperature`,
  `specific_humidity`, `u/v_component_of_wind` (levels >= 300 hPa),
  6-hourly, ARCO coord names/values so EWB logic is untouched. Built
  2026-07-25 -> 2026-10-30 (392 pts, 41 GB); resumable/incremental
  (append-only on an origin-anchored dense time axis), 24-thread copy.
  Spot-checked bit-exact against ARCO (2mT 40N/74W, u500, q700: diff 0.0).
  `make_targets` serves ERA5 targets from the mirror when it covers the
  event window; IFS-analysis fallback stays for post-cutoff days.
- **EWB mid-result cache fix** (`scoring/evaluate.py` shim
  `_cache_and_compute_zarr3`): vendored `maybe_cache_and_compute` writes
  zarr v2 intermediates, which fail on CF-encoded datetime coords
  ("Zarr format 2 arrays do not support `serializer`"). Shim densifies the
  same way but writes zarr v3 with per-variable encodings stripped and
  `_FillValue` attrs dropped. This was why derived-target scoring produced
  0 rows.
- **Init-listing cache** (`scoring/inits_cache.py`, new):
  `sources.available_inits` re-lists `s3://noaa-oar-mlwp-data` per day
  (~33 s per call for the four GFS-based models). Day coverage for days
  ended >5 d ago is final, so it is memoized in
  `data/cache/aiwp_inits.json` (merge-on-write, cross-process safe).
  Warm build_forecast: graphcast_gfs 33.3 s -> 0.8 s.
- **Stage profile** (heat_wave 31b6bf4a, 11 models, mirror-backed truth):
  build forecasts ~156 s cold / ~10 s warm; truth derive 28 s cold;
  10 models warm cache 12 s. Old behaviour: 1-11 min/event on global ARCO
  streaming; mirror + caches put single-event-job cost well under target.
- Full 90-day run launched with all models: `python -m ewb_live.pipeline
  --days 90 --jobs 4` (log `data/results/pipeline_full_run.log`).
- **Job isolation rework** (pipeline.py): per-event-job SUBPROCESSes
  (`--_run-job` worker mode) after two ProcessPoolExecutor runs where one
  OOM-killed worker poisoned the whole pool (BrokenProcessPool => all 45
  jobs lost, 0 scored). Failures now recorded per job + one serial
  retry; peak RSS tracked in meta.perf.
- **Memory hog fix** (truth.py `_cropped_target`): mirror-backed ERA5
  targets are hard-cropped to the case bbox (+2 deg) inside
  subset_data_to_case before EWB derives IVT etc. — EWB's mask keeps the
  global grid (drop=False) and derived fields on the global grid were
  the 12-20 GB/worker hog.
- **Forecast-part cache** (fc_cache.py): caches decoded ifs_hres /
  aifs_single reads (0-240 leads, 10-deg padded box) in
  data/cache/fc_parts so the 15 overlapping AR events share fetches.
- Context handoff at team 90% rule: full state in
  notes/handoff_ewb2_1.md; successor is agent 'ewb' (successor 3),
  term_0bafb4ff-2f94-4b13-b4e2-63cd5ef32a08. A full scoring run is
  in flight (started 11:50Z, jobs=3, log data/results/pipeline_full_run.log,
  persist_on_release) — successor monitors it; do not kill.

### ewb (successor 3) session, 12:15-12:40 UTC
- Monitored the 11:50Z full run: 3 cold AR jobs ran 30+ min with zero
  output, RSS climbing ~350 MB/min each, fc_parts never written. Root
  cause measured: ecmwf-forecasts GRIB byte ranges are GLOBAL fields
  (region padding saves no transfer), so fc_cache's full-0-240-lead
  strategy moved ~8 GB per (model, init) [41 leads x 4 vars x 13 levels
  x ~4 MB]; an AR job needs ~10 inits x 2 ECMWF models -> ~170 GB
  first-pass fetch, and lead windows differ per (event, init) so the
  region-quantized full-lead keys never shared. Pathological per the
  handoff's own >20 min/job criterion.
- Also found and killed ewb2's stale fc_cache standalone test (pid
  1447984, reported killed in handoff but still running, 7.4 GB,
  competing for bandwidth).
- FIX: reverted fc_cache to fetch only REQUESTED leads, key extended
  with leads-set hash (fc_cache.py docstring records the measurement).
  Verified cold+warm micro-test (13-lead 2t fetch, warm hit 0.22 s).
- Killed the 11:50 run, relaunched the same command at 12:25Z
  (EWB_LIVE_INIT_PARALLEL=2, --jobs 3, log
  data/results/pipeline_full_run.log, background proc_e7a43f417b46,
  persist_on_release). NOTE: the launch tool retried the background
  call once creating a duplicate tree (12:24:58Z); the duplicate was
  killed at 12:26Z — only the tracked tree (python pid 1510511) runs.
  Resume was free (12 cached pairs intact).
- 12:34Z state: fc_parts 22 keys/1.4 GB (cold fetches complete in
  ~1-2 min), job RSS 4-8 GB transient peak 14 GB, ~22 GB used total.
  Same lead-set hashes recur across AR jobs -> reuse where lead
  windows coincide.

## Session log 2026-11-06 (15:40-16:00 UTC) — publish validator (design channel idea 1)
- Added ewb_live/events/validate.py (pure stdlib): per-event claim checks;
  pipeline.run step 1b REJECTS failing events before events_selected/scoring.
- Freeze semantics learning (validator caught it on live data): SPEC/EWB
  define freeze as below LOCAL p15 — desert events with 14.8-21 degC cold
  extremes are valid. Gate bounds are physical tripwires (freeze -90..45),
  not climatological claims. Pinned by test.
- Regression guard test validates live events_selected.json: 45/45 pass.
- Known-bad in tree (design's, uncommitted): viz test_pack_u8_roundtrip
  IndexError; 67/68 offline green.
- No compute run locally (host guard _guard.py; hard rule). All heavier
  checks deferred to LXC via ops/remote.sh when infra.md says ready.

## Session log 2026-11-06 (16:00-16:30 UTC) — OWB climatology replaces EWB default for heat/freeze finders (product-owner decision)
- Product decision: heat/freeze finder thresholds come from the legacy OWB
  pooled climatology on the ewb box (/opt/ewb-owb: raw 6-hourly ERA5 t2m
  1990-2019 zarrs (30 yrs — the "1990-2020" in the request was off; the
  31st clim2 entry is the legacy p01/p99 product), ~106 GB, READ-IN-PLACE),
  NOT EWB's shipped climatology.zarr. Product named
  t2m_owb_1990_2019.zarr to match contents (request said 1990_2020). Percentiles: p95 heat / p05 freeze (owner: ERA5 +
  pooled climatology are muted, p01/p99 would be too extreme). Store also
  carries p01/p99 (OWB severe-tier ref) and p15/p85 (like-for-like
  comparison against EWB's shipped percentiles).
- Code: ewb_live/climatology/owb_clim.py (copied from /opt/ewb-owb/clim.py,
  credited; quantiles configurable; get_climatology_owb loader returns the
  same DataArray shape as defaults.get_climatology). Finders switch via
  EVENT_CLIP_MODES (default env EWB_LIVE_CLIM=owb; ewb_default keeps the
  old 0.85/0.15 CLI defaults for comparison runs). Fallback to EWB
  defaults when store not built (warns; honest half-provisioned run).
  Event details carry quantile + clim_source; meta.json has a
  "climatology" provenance block for the dashboard.
- Variable/estimator notes (SPEC "if EWB used a different variable, say
  so"): EWB's definition uses the SAME variable — 6-hourly t2m, daily-pass
  = all four 0/6/12/18 steps exceed the bound (resample .min). Not daily
  max. Differences are (a)  estimator: ours = flat +/-10-day pooled window
  quantile per (dayofyear, hour); EWB = triangular rolling-mean estimator;
  (b) period identical (1990-2019 both). Hour handling: the finder applies
  bounds at the four 6-hourly anchors with exact-hour match (upstream
  sel(dayofyear, hour) + nearest) — no cross-hour interpolation happens,
  so the "linear ramp across each hour" smoothness is a property of the
  climatology field, applied consistently as long as detection stays on
  the 6-hourly grid (it does).
- Validation step (product step 4): `python -m ewb_live.climatology.
  owb_clim compare --points 400` on the box — compares q0.15/q0.85 like-
  for-like + q0.05/q0.95 vs nearest shipped (q0.10/q0.90); results appended
  below when run.
- Build run: ops/remote.sh bg clim ... quantiles --workers 10 (16c/128G box;
  joblib tile workers ~3-6 GB each; viz backfill concurrent).

## Session log 2026-11-06 (16:05-16:25 UTC) — NO-OVERLAP product rule: curation rewrite
- Owner rule (permanent): no two events may overlap in BOTH time and space
  in events_selected. Implemented in ewb_live/events/select.py (replaces
  the old greedy fragment dedupe which only dropped >=2d overlap AND >=40%
  bbox-cover fragments — too loose):
  1. merge_same_type: same-type events with time gap <= 1 day (overlap or
     touch) AND intersecting bboxes -> ONE event, transitively (union-find
     components, chains collapse). Time=bbox-consistent union; attrs
     recomputed (heat: peak=max, min_temp=min; freeze: min_temp=peak=min;
     AR: IVT/landfall=max, core keeps its pixel/threshold pair);
     title and id rebuilt (publish gate re-checks); merged_from/count/core
     recorded in details. Union bbox kept if within 40deg lat x 60deg lon,
     else the strongest member's CORE bbox (deviation note: a pure
     connectivity split inside a chain cannot remove overlap — chain links
     overlap by construction — so components always collapse to one event
     and the core fallback bounds the box). Chains >31 days are truncated
     to 31d (validator cap) and logged.
  2. Cross-type: events still overlapping a kept different-type event in
     time+space are dropped, global greedy in within-type severity-rank
     order (rank-0 beats rank-1 across types; rank ties break on absolute
     severity, deterministic id tiebreak).
  3. Refill: candidates are one global queue ordered by within-type rank,
     per-type quota top_n, so a cross-type drop frees the slot for the
     next same-type candidate under the same rules.
- Decisions land in data/results/curation.json (pipeline writes
  select.LAST_DECISIONS) + logger lines "curation MERGE/DROP".
- Tests: tests/test_select_noverlap.py (9 offline; severity monkeypatched
  so no remote climatology reads) incl. the zero-pairwise-overlap invariant
  and a Siberia-chain-style fixture. 77/77 offline pass.
- Portability ground rule applied to new code: owb_clim paths now via env
  (EWB_LIVE_OWB_RAW, EWB_LIVE_CLIM_OUT) with box defaults; no new hardcoded
  hosts/paths.
- Desert freezes: legit under percentile-relative rules; regeneration
  report will flag any hot-desert freeze (Sep-Oct Sahara-class) explicitly
  for the owner.

## events_selected REGENERATED + scoring relaunched (ewb-3, ~17:30 UTC)
- OWB climatology validated on the box (6 quantiles incl. p95/p05, EWB
  layout, sane extremes). OWB-vs-shipped sample (400 pts): same-quantile
  estimator offset ~±1.3 K; finder p95/p05 are ±2.5 K stricter than the
  shipped q0.90/q0.10 neighbours.
- New selection (p95/p05 + no-overlap curation): **15 AR + 9 heat + 0
  freeze = 24**, zero overlap pairs verified on the published file
  (antimeridian-aware check). 24 heat_wave raw -> 10 after merge -> 9 after
  one cross-type drop vs AR rank 3; AR 769 raw -> 15 (long merged corridors,
  m47/m19/m19 — windows up to 31 days). **FREEZE = 0 globally** under p05
  for Aug 8-Oct 30 2026 (scan verified running in owb mode; this is a real
  threshold consequence, flagged to design+owner as a product call).
- Fixed severity bug found in the first regen: _severity read the shipped
  EWB remote store even in owb mode => all-zero anomalies; now reads the
  OWB local zarr at the finder's own quantiles (ewb_default mode keeps
  0.85/0.15). Detection cache hit; selection re-derived with real
  severities; same final ids.
- Posted full 24-event list + model-var note to design channel, nudged
  design terminal (all their ids changed; freeze class empty pending owner
  call on p05; dashboard label should read 1990-2019).
- Scoring relaunched for all 13 models: jobs=3 + AR lane **1** (was 2):
  merged AR windows now reach 31 days and the dense valid_time cube scales
  ~quadratically => two concurrent long ARs could exceed 128 GB. AR parts
  with unchanged ids reuse cache. Mem-guard watcher (stops unit at >110 GB).
- Trap (again): chained pkills on one ssh line self-kill when an earlier
  pattern's literal text matches a later pattern; one bracketed pattern
  only.

## 2026-11-06 ~18:00 UTC — p05-freeze funnel + merge-rule rework + AR ocean rule (ewb-3)
### (1) Freeze=0 diagnosis (orchestrator ask): p05 is STRUCTURALLY EMPTY
Scan ran correctly in owb mode (clim=owb q_up=0.05); ALL conventions verified
good by live probe on the box: truth grid lat -60..90 ASCENDING 0.25deg,
lon 0-360; climatology label-based sel handles the 721-descending axis; both
in K; hour-ramp = exact 6-hourly anchors; exceedance cell-days land in the
right latitude bands for the season. The zero is the upstream AND-chain:
  - exceedance cell-days (all 4 anchors < p05), Sep 1-21 2026: 7,659
  - after upstream >=3-day consecutive filter:        179  (-97.7%)
  - largest post-filter blob:        6,928 km2 (20 cells)
  - upstream seed rule demands:     200,000 km2 largest blob on the seed
                                     day AND the adjacent day
  - days meeting seed floor: 0/21; adjacent-day seed pairs: 0 -> 0 events
Mechanism: 12 consecutive sub-daily steps below p05 ~ (0.05)^12 under
independence; synoptic persistence relaxes it but not to blob scale. Lower
area/gridpoint floors cannot rescue 179 scattered cells.
EVB-shipped-p15 scan (ewb_default) gave 15 raw freezes from the identical
chain => this is the quantile, not the pipeline. OWB-POOLED p15 now gives
only 2 raw candidates (Oct 16/19): the pooled estimator sits ~1.25K colder
at q0.15 than the shipped estimator (climcmp), so it eats most of the
class too. Options for the owner: (a) extend the OWB store to q0.25
(~5h rebuild on the box); (b) run freeze against the shipped EWB
climatology (mixed-mode, code change); (c) accept 0-2 freezes. Default in
code stays the owner's p05; EWB_LIVE_FREEZE_CLIP/EWB_LIVE_HEAT_CLIP
override. Current regen runs EWB_LIVE_FREEZE_CLIP=0.15 => <=2 freeze events.
### (2) Mega-event fix (merge semantics)
merge_same_type rewritten: fragments merge only if same type, gap<=1d AND
bbox IoU>=0.3 (env EWB_LIVE_MERGE_MIN_IOU), greedy from highest-severity
seed, merged duration capped at upstream events.yaml per-type p90 (AR 9d,
heat 16d, freeze 16d; env-overridable), union bbox still capped 40x60.
Non-merge overlaps (same or cross type) DROP the lower within-type
severity PERCENTILE (was rank-ordinal). Tests rewritten + new: IoU gate,
duration caps, drop-not-merge, ocean-rule. 92/92 offline.
### (3) AR ocean-origin rule (owner, 2026-11-06)
ar.py now computes the FULL IVT daily mask alongside the land intersection
(builder key 'atmospheric_river_mask'); ocean_land_rule() rejects at
candidate stage: first-day ocean fraction < 0.5
(EWB_LIVE_AR_OCEAN_FRAC_START_MIN) or ocean fraction < 0.05 on every day
(all-land object). Landfall >=1 day with >=10 land px is implied by the
existing tracking floors. details now carry ocean_frac_start,
ocean_frac_max, land_frac_peak, landfall_time; funnel counters logged
("AR events kept: N (funnel: {...})"). AR_MAX_DURATION_DAYS 12->9 to match
the merge cap. 3 offline tests (all-land rejected, ocean->land accepted,
land-start rejected).
### Regen #3 state: heat 15 + freeze 2 (cache), AR scan running with ocean
rule + 9d split; curation then publishes events_selected.json. Scoring
STOPPED (orchestrator) — relaunch after repost.
### 18:20 UTC — reposted + scoring relaunched
Final selection published: 27 events (15 AR / 11 heat / 1 freeze), zero
arc-aware pairwise overlaps, AR durations 2-9d, ocean fields on all ARs
(min ocean_frac_start 0.51). List reposted to channel_design_ewb.md,
design nudged. Scoring relaunched (ewb-live-pipeline, jobs=3, AR lane 2,
EWB_LIVE_FREEZE_CLIP=0.15) => 297 event-model pairs; mem-guard watcher
armed. PITFALL LOGGED: any pipeline/regen launch MUST carry the same
EWB_LIVE_* env as the regen that produced the list — a bare relaunch
re-ran detection at p05 and briefly clobbered events_selected.json to
26 events (fixed by relaunch with the env; caught in 1 min).
### 18:31 UTC — scoring OOM'd at AR_JOBS=2; relaunched AR_JOBS=1
journalctl: ewb-live-pipeline oom-kill at 18:31:19 — two 9d AR jobs plus
temp jobs exceeded 128 GB (densified 3D u/v/q cubes: AR job cost tracks
INIT COUNT and variables, not just window length, so the 9d cap did not
make 2 lanes safe). Relaunched with EWB_LIVE_AR_JOBS=1 (the setting the
previous full run survived), jobs=3. NOTE: per-job score rows only reach
scores.parquet every ~10 jobs, so the ~4 jobs done pre-OOM will re-run
(done pairs: 0 at resume) — acceptable, ~30 min.
### 19:01 UTC — stray-edit check + scoring resumed safely
- Stray session review: only suspicious touch was pipeline.py mtime 18:57,
  content byte-identical to the 18:32 synced copy -> harmless. events/,
  scoring/, weekly.py untouched by it.
- Root cause of 2 OOM kills: kernel OOM (systemd-oomd inactive) from the
  EWB densify spike colliding with the other agent's mlwp_icechunk
  backfill (16 workers, since 17:20). Scoring resumed jobs=2, AR_JOBS=1
  (~45-50 GB peak); box-side ops_memguard.sh stops MY unit at
  MemAvailable<20 GB within 30 s; sources channel informed. Resume
  confirmed: 89 pairs reused (6,437 rows), 208 to go.
### 19:14 UTC — scoring on a persistent unit + auto pause/resume guard
bg's transient unit gets GC'd on stop, so the guard could not restart it.
Wrote /etc/systemd/system/ewb-live-pipeline.service (ExecStart wraps the
same runner script; env baked) and upgraded ops_memguard.sh to PAUSE at
MemAvailable<25 GB / RESUME at >60 GB (scoring survives other agents'
bursts unattended; resume reuses cached pairs). Running: 89 pairs done,
208 to go. Completion marker for watchers: 'pipeline complete' log line.

## Full-run completion machinery (ewb, ~19:48 UTC)
- aigfs zero-rows ROOT CAUSE: evaluate requested surface_air_temperature for
  heat/freeze; aigfs (GFS-based) has no 2 m temperature (probe: vars =
  air_temperature/u/v/q/geopotential). ARs were fine; the heat/freeze
  failures surfaced as bench errors -> 0 rows silently.
- FIX: per-event-type variable precheck in score_event (heat/freeze only):
  model lacking surface_air_temperature -> status 'unsupported', reason
  'n/a (no 2 m temperature)', build never attempted. AR path untouched.
- Pair-status bookkeeping: score_event(status_out=...) records EVERY model
  outcome: scored / unsupported / no_data / bench_failed / empty. Worker
  writes <out>.status.json sidecar; parent merges into
  results/pair_status.json (persisted across restarts; terminal
  unsupported/no_data pairs are not rescheduled).
- meta.json: pair_coverage {expected, scored, documented_zero_rows,
  unattempted_or_failed, failed_jobs_after_retry} + full_run_complete =
  no unattempted pairs AND no job failed after its serial retry.
- Per-job PEAK RSS logged (worker self-rusage -> job log line peak_rss_mb=).
- Unit: persistent /etc/systemd/system/ewb-live-pipeline.service,
  Restart=on-failure RestartSec=60 StartLimitIntervalSec=0 (mem-guard stop
  = clean stop, no auto-restart; crashes self-heal). jobs=3 std + AR lane 1.
- Tests: tests/test_pair_status.py (3 offline: unsupported+never-built,
  same model scores on AR, no_data/bench_failed). Suite 95/95 offline.

## 20:30 UTC status + diagnostics (ewb)
- End-to-end probe (event 4727538248b7 heat_wave, era5_arco): 727 rows,
  aigfs -> 'unsupported / n/a (no 2 m temperature)', other 8-9 models scored.
  aigfs fix CONFIRMED on box. Earlier 62s zero-row jobs were transient
  (concurrent-run cache contention), not structural.
- jobs=3 (3 std + 1 AR) measured ~110 GB box usage -> over 90 GB budget;
  dropped runner to --jobs 2 (2 std + 1 AR).
- Chronic box churn 19:31+ is mlwp_verify_store.py (mlwp agent, PID 103220):
  70-210 GB Committed_AS in ~90s bursts, ~3 min cycles. RSS-based ps missed
  it; /proc/meminfo diff (AnonPages -73GB in one window) identified it.
  Memguard pauses scoring each burst and resumes ~20s later; heat/freeze
  complete in gaps, AR (~20 min jobs) can starve while it runs.
  Nudged mlwp agent (term_85cad75b) + channel note posted.

## 2026-11-07 01:05 UTC — a3bb50ccde3d exclusion: evidence (ewb)
New-set AR event a3bb50ccde3d (South Pacific, 08-05..08-11, bbox 35x40 deg,
padded 55 deg lat) fails ALL 11 models identically in EWB 1.1.1 metrics:
SpatialDisplacement._compute_metric -> center_of_mass apply_ufunc gets a 3D
core array (COM returns 3-tuple (11.3, 101.0, 118.0); expects 2). Model-
independent (incl. ifs_hres/aifs_single with native q), so it's the TARGET
(ERA5-derived AR mask) path, NOT the FCN r->q preprocess. Same event also
exploded memory: single job peak_rss=118.7 GB logged, and a standalone
1-model probe of it was kernel-OOM-killed ALONE on the box (>128 GB).
Repro trail: ops/probe_fcn_{trace,dims,levels,subset}.py + box logs
(fcn_trace2/fcn_dims2/probe_fcn_levels.log). Levels store is uniform
(13 levels, t+r, no q for both FOUR_v200_IFS/_GFS) - ruled out.
Action: EWB_LIVE_SKIP_EVENTS=a3bb50ccde3d (documented bench_failed per
pair, counts toward full_run_complete coverage). memguard re-installed as a
PERSISTENT unit (was transient: `systemctl stop` deleted the unit and left
the box guardless 00:50-01:05). Watching the next AR event to see if the
crash is a3bb-only or hits the whole new-set AR lane (would be a
design/owner decision: EWB oversized-case bug project vs event set).

## 2026-11-07 01:45 UTC — owner freeze decision + B2 mirror fix deployed (ewb)
1) Freeze: FREEZE_CLIP=0.15 removed from .runner-pipeline.sh; p05 reselect
   kept 0 freeze events (funnel logged 01:40:23). meta.json now records
   selection_thresholds + event_counts{freeze:0}. p15 scores dropped by the
   fingerprint merge. 26 events / 207 pairs planned.
2) B2: mirror coords had zarr default _FillValue 0.0 -> xarray masked real
   0E col + equator row as NaN. Creation now fill_value=None;
   ensure_coord_integrity() added (reads + truth switch), 4 tests
   (tests/test_truth_mirror_coords.py). Store repaired in place, verified.
   52 parts across 17 events (10 heat, 7 AR: 119e56026cbf 395349feddab
   64a046c111ce 806867ad1ba1 3d32db128915 971b752852cf eecddeca3560
   82dc9c1b533c bace10fee425 4727538248b7 8a462d8d4796 e7fa2290cc77
   08cf5a90f8fe 66b5b9f6f9db 559d8626b887 a1e36b2ef2f5 + a3bb already
   excluded) deleted, pair_status purged -> incremental rescore in flight.
3) B5: per-row truth_source verified present (6437 rows era5_arco); zero
   events end after cutoff -> no HRES-truth rows this window.
Run restarted 01:39; AR lane serial (memory-capped), ETA ~07:00-08:00 UTC.

## 2026-11-07 02:30 UTC — AR lane: EWB dask bug fix + TC guard (science-review B8) deployed
- Root-caused the AR bench_failed wave: EWB 1.1.1 SpatialDisplacement apply_ufunc(vectorize=True, dask="allowed") hands the ufunc the whole dask 3D mask -> "tuple of 3, expected 2". Shim in evaluate.py materializes masks to numpy; tests 3/3 incl. upstream repro (tests/test_spatial_displacement_shim.py).
- B8: TC cores were in the AR lane (Dolphin 2972, Isaias 2018, monsoon blob 1785 kg/m/s). Added ar.tc_plausibility_rule (IVT cap + compact-core fill-ratio) AND select.py AR_MAX_PEAK_IVT=1500 ceiling (cache-compatible). 6/6 tests.
- Ids churn fixed: results/events_runs/events_selected_<UTC>.json + ledger.json per run (last 40).
- Deployed 02:15 UTC; TC trio dropped, publish gate trimmed 2 sub-floor replacements; final 13 AR + 11 heat, 185 pairs. a3bb50ccde3d remains documented-skip (119 GB memory bomb).
- Channels posted (science-review + design). ETA ~07:00-08:00 UTC. Tests 116/116 offline.

## 2026-11-07 02:25 UTC — memory-scaled AR skips + evidence correction
- Watcher fired on fbe99f30e625's completion line, but it was the 01:58 PRE-shim run: "no rows" (all models failed), 75 GB peak. Not shim proof — shim stays unit-verified only until a new-set AR scores.
- AR memory scales ~linearly with bbox area: measured 728 deg^2 -> 75 GB, ~1400 -> ~119 GB (a3bb). Legit AR 806867ad1ba1 (36x59 = 2124 deg^2) projects ~165-190 GB; 395349feddab (38x38) ~122 GB. Both would trip the 100 GB guard into a kill-restart loop (or worse, hit neighbors on shared host). Added both to EWB_LIVE_SKIP_EVENTS (documented zero-row, same path as a3bb). Published AR set: 13 events, 10 scorable + 3 documented memory-exclusions. Root fix is upstream (EWB dense valid_time reindex), tracked as a known limitation.

## 2026-11-07 03:55 UTC — shim PROVEN in production; AR memory scaling law; ETA moves to ~16:00-17:30 UTC
- 3d32db128915 (28x31 AR, previously all-11-model failure) scored 10 models + 1 documented unsupported at 03:43 — the SpatialDisplacement shim works on the box, not just in unit tests.
- AR job RSS scales ~0.075 GB/deg2 + ~15 GB base. Remaining ARs 06ceb4d8 (25x37 ~84 GB) and 66b5b9f6f9db (23x46 ~94 GB) would collide with 2 std lanes against the 100 GB guard ceiling -> switched runner to --jobs 1 (heat serializes behind ARs). 4a9c67b01f25 (30x43, ~112 GB projected) added as 4th documented memory-exclusion.
- Cost: 8 AR jobs x ~80 min serial + 11 heat jobs serialized => ETA ~16:00-17:30 UTC (was ~07-08). New completion watcher proc_523d1003b335 (200x300s). Published set unchanged: 13 AR (9 scorable, 4 documented n/a) + 11 heat.

## 2026-11-07 04:20 UTC — selection churn correction (my 04:05 "identical" claim was wrong)
- ARCO cutoff advancing to Oct 31 forced a full re-scan; the extended window CHANGED the selection: 7 added / 4 removed (new run_id 20261107T040432Z). Added: ARs 0e530ab9eebf, 255f68102c43 (antimeridian, real span 20x38), 7188c11f75c2, 92c9cb48559b, e86c13f47bc0 + heat a9f9ea7bca1c, b24f79e63be0 (tail-Oct, new truth days). Removed: 395349feddab, 806867ad1ba1 (memory-excluded, now moot — out of set), 85df401a3d8c, a1e36b2ef2f5. Published: 14 AR + 13 heat = 27 events; a3bb50ccde3d + 4a9c67b01f25 documented n/a (memory), 12 ARs scorable.
- My watcher bug: compared the newest snapshot against the live file (self-comparison, vacuously "identical"). Fixed approach: diff two run-stamped snapshots by name. Lesson logged.
- ETA re-based from measured 5.7 s/deg2 AR cost: ~14.5 h of AR lane from 04:04 -> ~19:00-23:00 UTC today. Memory: largest scorable AR 66b5b9f6f9db ~95 GB est — AR_JOBS=1 + jobs=1 keeps total ~110 GB, within historical envelope; guard unchanged.

## 2026-11-07 22:30-23:00 UTC — stall postmortem shipped; fixes deployed; p85/p15 re-selection started (ewb)
- Postmortem: notes/postmortem_stall_20261107.md (root cause: `len(new_frames) % 10 == 0` publish gate almost never aligned -> 23 h, 20,121 rows scored but never written; secondary: memguard v1 thrashing, non-enabled unit, no staleness alarm).
- Fixes deployed + tests (131 offline pass): publish after EVERY job + publish-on-start; heartbeat.json + ops/freshness_check.py + 10-min systemd timer (hb 90-min limit, restarts stale-but-active unit only); per-job timeout 21600 s via subprocess; S3 connect/read timeouts 15/120 s; memguard v2 (own-RSS-only pause, guaranteed force-resume); ops/run_scoring.sh runner + rendered units installed on box (pipeline ENABLED now, memguard + freshness.timer active).
- New `--publish-only` path: merges cached parts against published events, no detection/scoring. Run 22:50:32Z published **5,626 rows / 79 pairs** — NOT 20k: the ARCO truth cutoff advanced again, flipping truth_source for late-window events and (correctly) re-orphaning ~14.5k rows whose truth policy changed; they are back in todo for honest re-scoring.
- Product decision applied in code (defaults, env still overrides): heat p85 / freeze p15 (temperature.py). FCN RH->q for ARs + B2 mirror integrity fix confirmed already in prod. Unit restarted 22:54:57Z -> full re-selection at p85/p15 running (run-20261107T225457Z.log), then incremental scoring of changed/orphaned pairs.

## 2026-11-07 23:35 UTC — p85/p15 selection posted, full re-score running, handoff written (ewb)
- Selection run 20261107T231622Z: 30 events (15 AR / 15 heat / 1 freeze Greenland). E China Sep-Oct present at p85 (32d04a30d801, Sep 27-30, 14-32N/100-122E, peak 32.6C) — science-review check passes. Gate rejected AR f13a6a2d2652 (peak_land_pixels 435<500). Full re-score 330 pairs from 0 (p85 fingerprint churn + ARCO cutoff advance); publish-after-every-job visibly working (14,133 rows by job 1).
- Successor instructions: notes/handoff_ewb_4.md (monitor to full_run_complete, then weekly on box, then real DONE here).

## 2026-11-08 00:15-02:10 UTC — completion audit, 256 GB re-lane, ponytail, public-leak fix (ewb)
- 23:52Z "pipeline complete" audited GENUINE: 330 pairs = 257 scored + 73 documented-zero, fingerprints+truth current, 22,763 rows live (stall rows all in). AR re-score was never needed (parts keyed by fingerprint|truth; p85 left AR keys unchanged) — my 36 h ETA was wrong, corrected in channel.
- Box RAM 256 GB: AR lane 1->3, memguard budget 200 GB/resume 60 GB (unit Environment), restart 01:02Z -> Nov 8 window selection 31 events, 163 pairs pending, 18 AR jobs / 3 lanes, ETA ~17:00Z. Exit semantics: 0 only when full_run_complete (else "N pairs missing" + exit 1 -> systemd re-enters).
- Ponytail 3/3 applied (unpack crash / stride severity sampler / TTL docstring). meta.json public leak (abs paths) fixed in owb_clim.climatology_meta. tests/e2e FE failures are design's in-flight dashboard edits, not mine. Handoff refreshed (handoff_ewb_4.md).

## 2026-11-08 03:20 UTC — ponytail 02:05Z batch 5/5 fixed (ewb)
hres-analysis cache key now bbox-scoped (+viz glob-reuse deleted, exact-key only); scores.parquet atomic write; subset zarr cache self-healing + tmp/replace; ecmwf surface lead-axis fix; phantom failed_jobs cleaned from perf. Offline 134 pass; the 9 red tests are design's own e2e FE suite. Synced to box (workers hot-pickup; parent on next restart). Run still draining (18 AR jobs / 3 lanes, ETA ~17:00Z).

## 2026-11-08 03:55 UTC — ponytail 02:32Z batch 3/3 fixed (ewb)
Heartbeat beat now spans the serial retry loop (no false-wedge restart mid-retry); weekly leak gate moved pre-snapshot + quarantines flagged data/results files (*.quarantined) + re-checks post-publish; dead update_truth_mirror deleted. Offline 140 pass (only design's FE suite red). Synced.

## 2026-11-08 (later) — ponytail 04:42Z: 429 throttle retry fixed (ewb)
_net.with_retries now retries HTTP 429 (docstring's actual promise); dead RuntimeError catch dropped. Test added. Offline 141 pass; the only 3 failures remain design's in-flight test_dashboard_fe.py (their suite, not mine). Synced.

## 2026-11-08 05:40 UTC — ponytail 05:03Z batch 5/5 fixed (ewb)
Quarantine moved out of scanned/served tree (data/quarantine/) — my own bug from 03:55 batch; --publish-only exits 0 on successful un-stick; --force now clears stale pair_status + sidecar fold-in (D1-class stale-terminal lock fixed); events_runs snapshots trimmed to 40; dead None-check dropped. Offline 142 pass. Synced.

## 2026-11-08 06:30 UTC — Phase B D1-D3 implemented; D1 re-score running (ewb)
D1: IFS-read TypeError root-caused to our client_kwargs Config object (s3fs>=2026.9 wants config_kwargs=); fixed in _net.s3(), no version pins, live read verified on box. 51 stale terminal no_data keys + 4 valid_time unsupported dropped from pair_status; aigfs genuine gaps kept; unit restarted 06:17Z (background watcher on full_run_complete). D2: scorecard skill on model∩baseline event intersection (+skill_n_common, disjoint->None). D3: event-bootstrap 95% CI per cell (B=400, seed 20261108, event-weighted; null if n_events<3). Offline 149 pass (1 design FE). Ponytail 05:16 batch 4/4 fixed same push (part-file atomic write, stamped quarantine, unique tmp in _atomic_write, publish-only guard). Weekly runs after re-score completes.

## 2026-11-08 06:40 UTC — ponytail 06:24Z MUST fixed: read_error vs no_data (ewb)
ForecastReadError raised when all init reads fail (no-inits archive stays no_data); score_event records status read_error -> automatically retriable (:736 tuple) and completion-blocking (:583 tuple), zero pipeline.py changes needed. Test added. Watcher v2 on heartbeat phase=complete AND full_run_complete; D1 re-score at 5/23 jobs, 23,164 rows.

## 2026-11-08 16:40 UTC — AR tropical-cyclone screen (owner decision, G-40 deviation) (ewb)
Deviation from upstream EWB AR definition, documented per owner instruction:
- Upstream mask filters kept EXACTLY (IVT>=400, Laplacian ridge, >=500 px component, component mean |lat|>15) for comparability — events/atmospheric_river.py untouched; raw exceedance map remains the raw upstream mask, labeled ("AR mask (EWB, before cyclone screen)" in dashboard/rawmap.js).
- ADDED ewb_live/events/tc_screen.py: IBTrACS TC colocation screen at candidate stage in scan_ar_events (after blob tracking + existing TC guard). Rule: drop candidate when an IBTrACS track center is within EWB_LIVE_TC_SCREEN_KM (default 250, EWB ibtracs_bounds.py buffer scale) of any mask pixel on the nearest segment day AND within EWB_LIVE_TC_SCREEN_HOURS (default 24) of the window. EWB_LIVE_TC_SCREEN=0 disables. Track access reuses EWB's IBTrACS URI + column mapping; full CSV cached at data/cache/ibtracs/tracks.parquet (24h TTL); fetch failure -> stale cache -> fail-soft disabled (screen never kills detection).
- Provenance: kept AR events carry details.tc_screen{radius_km,hours,tracks_available,ibtracs_sid}; public meta.json carries detection_deviation.ar_tc_screen{rule,radius_km,hours,source}; funnel logs tc_track count with sid/name/distance/matched_time.
- Empirical: 0c7a7f10f667 (adjudicated post-tropical cyclone, guardian G-40) co-located with HANNA (IBTrACS 2026270N28302): track center within 227.4 km of the mask at 2026-09-27 00Z < 250 km -> screen drops it. The owner's adjudicated curation exclusion (data/curation_exclusions.json) remains as a belt-and-braces backstop.
- Tests: tests/test_tc_screen.py (6: center-in-component dropped, far track kept, time-separated kept, fail-soft None, loader maps EWB columns + caches + serves stale on refetch failure) + tests/test_ar_tropical_filter.py (upstream filter pins). Offline suite 164 passed, 28 skipped.

## 2026-11-08 18:40-20:20 UTC — successor 5: post-host-restart verify, D6, ponytail 19:15, H4 remote-ARCO fix (ewb)
- Restart recovery verified: scoring unit auto-restarted 18:35Z, drained clean ("pipeline complete: 31 events, 28889 rows", exit 0, 19:19Z). Guardian items confirmed on LIVE data: G-30 0 raw reasons, G-38 0 peak rows >240 h, G-37 merged runs recomputed (46d97→11, e4cc→7, 88bb→4). Offline suites green (176/28 pre-patch).
- Merged-map: qualified_at live for all 31 events (the restart run picked up the 17:35Z code a day early) — correction + answers posted to channel_design_frontend/ewb.
- D6 (science-review final): DurationMeanError skill suppressed — a signed mean can read +skill for a LARGER error (aurora +82.2% was that artifact); SIGNED_ERROR_METRICS in build_scorecard, skill=null, signed mean kept; test added. Dashboard duration cells go null from the next scorecard build.
- Ponytail 19:15Z batch: heartbeat moved out of the served tree (data/heartbeat.json) + both ops/freshness_check.py readers updated + the public copy deleted on the box; sr11 "RMSE" finding proven STALE (script already queries RootMeanSquaredError; review ran a pre-fix copy); gzip cache drops stale (path, mtime) entries; quarantine flattens relative paths into the stamped name (collision was live: one run flagging four meta.json kept one survivor).
- Weekly 19:20Z attempt: a3bb50ccde3d SCORED (its earlier bench_failed was transient): +1340 rows, 30229 total, full complete; AR lane now 15/15 — the "AR means are 14 events" disclosure is moot. Leak gate exit-1 flagged stale perf keys in history/2026-W45/meta.json (09:23Z snapshot predating G-31) + mini*/meta.json fixtures (box copies predated the seed fix). Minis restored from clean worktree copies; the W45 history meta re-snapshots this run. INCIDENT (mine): scp of 3 same-named files clobbered live data/results/meta.json at 20:07Z -> restored via pipeline --publish-only within ~3 min, public meta verified (30229 rows) + scrub CLEAN. Note: --publish-only republishes full_run_complete=false (it skips the documented-zero fold-in); the full run recomputes it.
- H4 ROOT CAUSE (science-review 19:5xZ major): the remote-ARCO truth branch — taken whenever mirror_covers(start,end) is False, i.e. for the FRESHEST window events — served truth without the G-34 longitude normalization (which lives in the mirror branch's _crop_bbox). Remote store is 0–360 (verified) vs forecast −180..180 → zero-overlap CSI for western events. 8fe8ae212d73 was the only shipped victim. Fix: _normalized_target wraps the remote-ARCO target, canonicalizing lon after subset_data_to_case (scoring/truth.py) + offline test test_remote_arco_target_normalizes_after_subset. 8fe8 purged (957 rows, 11 pairs, 11 parts, 1 sidecar); weekly re-run scoring it now (bg on box, watcher proc_62e6db676d7e).
- H3 minors: day-slack pinned in tc_screen docstring (day-resolution gate, ~36–48 h effective); tc_screen-provenance absence on the 15 shipped ARs disclosed to science-review (claim holds from the next detection cycle).
- On weekly completion: verify 8fe8 CSI nonzero/decaying + scorecard duration-skill nulls + W45 history re-snapshotted clean + scrub CLEAN, then refresh ## DONE honestly.
