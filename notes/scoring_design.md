# Scoring design for the TC, MLC and flood lanes

Status: **proposal only** (2026-11-20). No scoring code, no deploy, no merge. Everything
below is read from the repo at `origin/develop` (30faee0) and from read-only probes on the
EWB LXC; measured numbers say so, estimates say so.

Owner decision folded in (supersedes the "TC-8 metric options" part of the brief): the TC lane
is **not** track-presence-only. It gets ExtremeWeatherBench's own TC metrics **plus** two new
metrics, along-track error and cross-track error (Leonardo & Colle 2017 convention).

---------------------------------------------------------------------------------------------

## 0. Summary and the decisions I need

| Lane | Recommendation | Metrics (rows in `scores.parquet`) | Models scored | Cost (estimate) |
|---|---|---|---|---|
| **TC** (44 storms) | Hybrid: our own storm-following tracker on the store's MSLP gives forecast positions; EWB's landfall classes fed the same tracks | `TrackPositionError` km, `AlongTrackError` km (signed), `CrossTrackError` km (signed), `CentralPressureError` hPa (signed), `TrackMiss` 0/1, plus EWB `landfall_displacement`, `landfall_time_me`, `landfall_intensity_mae` (only where a landfall exists) | 8 virtual-store models (+ `ifs_hres` direct as baseline, see D4) | ~14-20 agent-h, ~2-4 h LXC wall for the first full backfill, ~20-40 min/week after |
| **MLC** (80 lows) | Same shared track core; verify against the ERA5 MSLP track the event came from | `TrackPositionError`, `AlongTrackError`, `CrossTrackError`, `CentralPressureError`, `TrackMiss` | same | ~8-10 agent-h, <1 h wall |
| **Flood** (40 events) | Daily exceedance contingency on the event's own threshold + areal accumulated-precip error. Only GraphCast carries precipitation in the store | `CriticalSuccessIndex`, `FalseAlarmRatio`/POD, `FractionsSkillScore`, `PrecipAreaBias` mm | `graphcast`, `graphcast_gfs` only (2 of 8) | ~8-10 agent-h, <1 h wall |

Order: shared track core + TC track metrics first, MLC second, EWB landfall classes third, flood last (section 7).

**Owner decisions needed** (details in section 8): D1 TC tracker (hybrid vs pure EWB), D2 which inits
count, D3 intensity (MSLP only, no wind), D4 baseline/model set (does ECMWF-direct `ifs_hres` count
for TC/MLC), D5 row granularity, D6 flood scope (GraphCast-only + AI-estimated truth), D7 MLC truth and
metric set, D8 leads shown, D9 compute window, D10 confirm the along/cross sign convention.

---------------------------------------------------------------------------------------------

## 1. How the existing lanes are scored, end to end

### 1.1 The chain (heat, freeze, cold snap, AR)

```
events.json (selected)                        ewb_live/events/*, select.py
  -> pipeline.run() builds `todo` per (event, pending models)      pipeline.py:650-700
       UNSCORED_TYPES (TRACK_TYPES | {"flood"}) are diverted: a documented
       pair_status "unsupported" is written per (event, model)     events/select.py:109, pipeline.py:~760
  -> _score_job() (process pool, one event, all pending models)    pipeline.py:238
       -> scoring/evaluate.score_event()
            truth_source_for_event(): ERA5 ARCO, else IFS HRES 0-h analysis   scoring/truth.py
            build_forecast(): inits in [start-240h, end), daily 00Z, leads 0..240 step 6 whose
                              valid time overlaps the event; sources.open_forecast via fc_cache
            EWB ExtremeWeatherBench(case, EvaluationObject(metric_list=default_metrics(type))).run_evaluation
            _reshape(): -> SCORE_COLUMNS
                event_id, event_type, model, metric, lead_time_hours, value, init_time
  -> part files + pair_sidecar/*.status.json  -> merged, fingerprint = start|end|bbox
  -> scores.parquet (+ fingerprint, truth_source) and scores.json
  -> build_scorecard()  -> scorecard.json         mean, n, n_events, bootstrap CI (>=3 events),
                                                  skill vs ifs_hres on the common event set
  -> build_region_scorecards() -> scorecard_by_region.json
  -> meta.json: pair_coverage, full_run_complete (scored OR documented zero-row per pair)
  -> dashboard/app.js: columns come from the scorecard rows per (event_type, metric, lead);
       TYPE_META[etype].noScores (app.js:18-26) short-circuits to the "Not scored" note
```

`default_metrics()` knows exactly heat_wave / freeze / cold_snap / atmospheric_river and raises
`ValueError` for anything else (`scoring/evaluate.py:90-120`). The metric lists are EWB's own
(`MaximumMeanAbsoluteError`, `RootMeanSquaredError`, `DurationMeanError`, `CriticalSuccessIndex`,
`SpatialDisplacement`, `EarlySignal`, ...). The score path is **EWB-evaluate-centric**: a case dict,
an `XarrayForecast`, an EWB target, EWB's `run_evaluation`.

### 1.2 What is lane-agnostic and what must be added

Lane-agnostic (reuse unchanged):

* The row contract `SCORE_COLUMNS` and `scores.parquet`/`scores.json` writers; `drop_duplicates` on
  columns+fingerprint.
* Incremental bookkeeping: `fingerprint(event)`, part files, `pair_status.json`, sidecars, the
  documented-zero-row vocabulary (`unsupported`, `no_data`, `bench_failed`, `empty`), `full_run_complete`.
* `build_scorecard()`: groups any `(event_type, metric, lead_time_hours, model)`; mean, n, n_events,
  event-bootstrap CI (needs >=3 events, weights events by row count), skill vs `ifs_hres` on the common
  event set. It does not care what the metric string is. `HIGHER_IS_BETTER` and `SIGNED_ERROR_METRICS`
  (pipeline.py:99-103) are the only per-metric switches.
* Dashboard cell rendering: columns are generated from whatever rows the scorecard carries; small-n
  flags, `dir: 'zero'` styling for signed metrics (`DurationMeanError`, `MeanError`), tooltips.

Must be added per lane (nothing here exists for TC/MLC/flood):

1. A scoring module producing SCORE_COLUMNS rows: event -> forecast extraction -> metric. It cannot
   go through `score_event()` as written (EWB `IndividualCase`+target classes, `default_metrics`
   raises for these types).
2. Pipeline wiring: take the type out of `UNSCORED_TYPES`, dispatch to the lane scorer in
   `_score_job`, and replace the blanket `_UNSCORED_REASON` with real per-pair statuses (for example
   `unsupported: no precipitation variable` for the six flood models without one).
3. Truth policy. `truth_source_for_event()` is ARCO-vs-HRES; the lanes have different truths: the TC
   track from IBTrACS (provisional until the archive catches up), the MLC track from ERA5 ARCO, flood
   rain from PARDIG (AI-estimated, provisional). `truth_source` is part of the cache key, so each lane
   needs its own tag.
4. Metric registry entries: `SIGNED_ERROR_METRICS`, `HIGHER_IS_BETTER` (pipeline.py), `FALLBACK_METRICS`
   and `BINARY_METRICS` (app.js:186-211) for labels/units; new copy in `dashboard/detection.py` About text.
5. Dashboard: drop `noScores` / `noScoresNote` for the lane in `TYPE_META` (app.js:15-26), replace
   `tcNotScoredNote`. `scorecard_by_region` needs a check that unknown types with centroid-by-bbox work
   (`regions.build_region_scorecards` is keyed on event_type; I read it but did not run it).
6. Tests (pure numpy fixtures for the geometry; a recorded-field fixture per lane).

### 1.3 Environment facts that change the picture (measured 2026-11-20 on the LXC)

* The virtual store is **not** ~40 days deep any more. `list_inits` returns (inits, first..last):
  `GRAP_v100_IFS` 3886 (2022-01-01..2026-11-19 12Z), `GRAP_v100_GFS` 4824 (2021-12-31..),
  `PANG_v100_IFS/GFS` 3800/5036 (2022-01-01 / 2020-09-30..), `FOUR_v200_IFS/GFS` 3798/5034,
  `AURO_v100_IFS/GFS` 1200/1198 (2025-01..). A retired `FOUR_v100_GFS` group (2889 inits to 2023-10) is also
  there. Cadence 00Z/12Z, leads 0..240 h step 6 (41), 0.25 degree, 13 pressure levels. The 90-day event
  windows are well inside the archive, and a hindcast/climatology of track errors is possible if wanted.
* Variables per family (open_init on a recent init): GRAP `apcp msl q t t2 u u10 v v10 w z`;
  PANG and AURO `msl q t t2 u u10 v v10 z`; FOUR_v200 `msl r sp t t2 tcwv u u10 u100 v v10 v100 z`.
  So **MSLP, 10 m wind and geopotential on 300/500 hPa exist for all four families**; **precipitation
  exists only in GraphCast (`apcp`)**.
* `apcp` is "6-hr accumulated precipitation", units **m**, and has fill values: for the IFS-initialised
  GraphCast the leads 0 h and 6 h are fill (NaN), the GFS-initialised member is valid from 6 h.
  About 0.3 % of values are small negatives (clip at 0). `sources.registry` does **not** expose
  `apcp` today (no EWB default name), so flood needs a one-line registry/mapping addition.
* Read cost: a regional 2-level `z` read took 0.64 s, a regional `apcp` read 0.65 s (virtual refs into
  the AWS bucket; one HDF5 chunk = one lead x one level, so a regional read still moves a whole global
  2-D chunk). This agrees with the TC-8 probe (median 0.81 s per 3-variable read).
* Memory: `data/perf.json` of the last weekly run records `peak_total_rss_gb = 183.8` for 4 concurrent
  EWB jobs (the brief says 127 GB; the box has 256 GB). The new lanes are bbox-sized numpy reads, not
  EWB densified arrays; my estimate is <3 GB per worker, but the pilot in section 7 must measure it
  before the full run.
* `events_selected.json` on the LXC: 80 MLC, 44 TC, 40 flood (matches the brief); `events.json` carries
  the unselected totals (738 flood, 145 MLC) and must not be used for sizing.

---------------------------------------------------------------------------------------------

## 2. What the EWB package gives us for tropical cyclones (read from source)

Source: `third_party/ExtremeWeatherBench` v1.1.1 @ c6467f4 (`metrics.py`, `defaults.py`, `derived.py`,
`calc.py`, `events/tropical_cyclone.py`, `inputs.py`). The develop tree here has no `third_party/` (a
known fresh-worktree gap); the LXC dev tree and `ne-integration` carry it.

### 2.1 The TC metric classes that exist

| Class | What it computes | Needs |
|---|---|---|
| `LandfallMetric` (composite base) | Finds forecast and observed landfalls with `calc.find_landfalls` (NaturalEarth 10 m land + ocean geometry, crossings from open ocean only) and pairs them. Knobs: `approach` `"first"`/`"next"`, `exclude_post_landfall`, `min_target_separation_hours`, `max_time_mismatch_hours`, `landfall_time_filter` default `("window", 24 h)` (or `("proportional", f)`). Used as a composite: `LandfallMetric(metrics=[...], approach="next", forecast_variable=..., target_variable=...)` | forecast **track** DataArray with `latitude`, `longitude`, `valid_time` over `(lead_time, valid_time)`; target track with the same coords |
| `LandfallDisplacement` | great-circle distance (km) between forecast and observed landfall point, per init | paired landfalls |
| `LandfallTimeMeanError` | forecast landfall valid time minus observed, hours; **positive = forecast later** | paired landfalls |
| `LandfallIntensityMeanAbsoluteError` | `abs(forecast - target)` of the chosen intensity variable at landfall. EWB's default sets it to `air_pressure_at_mean_sea_level` (so: MSLP at landfall) | the intensity variable interpolated onto the landfall point |

There is **no** EWB metric for track position error, along-track, cross-track, track presence,
lifetime, or intensity away from landfall. `SpatialDisplacement` is the AR mask centre-of-mass metric, not a TC
metric. The generic `MeanError`/`MeanAbsoluteError`/`RootMeanSquaredError` exist and could be applied
to a track's MSLP/wind series.

EWB's default TC `EvaluationObject` (`defaults.py:531-536`) is exactly the composite
`[LandfallIntensityMeanAbsoluteError, LandfallTimeMeanError, LandfallDisplacement]`, `approach="next"`,
forecast and target variable `air_pressure_at_mean_sea_level`, target `inputs.IBTrACS()`, forecast
`TropicalCycloneTrackVariables()` on FourCastNetv2.

### 2.2 What the forecast side must supply

EWB does **not** score model fields; it scores tracks it derives itself:
`derived.TropicalCycloneTrackVariables` runs `events/tropical_cyclone.generate_tc_tracks_by_init_time`
(TempestExtremes-style: MSLP peaks, closed 200 Pa SLP contour within 5.5 degrees, closed -6 m
300-500 hPa thickness contour within 6.5 degrees, minimum-distance and wind-threshold screens).

* Inputs: `air_pressure_at_mean_sea_level`, `surface_eastward_wind`, `surface_northward_wind` (-> speed),
  and `geopotential_thickness` (300-500 hPa). The thickness comes from the `z` levels via the preprocess
  `defaults.preprocess_cira_icechunk_tc_forecast_dataset`. All four store families have `msl`, `u10`, `v10`
  and `z` on 300 and 500 hPa, so the input side is satisfiable.
* **The tracker is constrained by the observed track** (`requires_target_dataset=True`): candidate
  peaks are kept only within `max_spatial_distance_degrees=5` great-circle degrees (~555 km) and
  `max_temporal_hours=48` of observed best-track fixes (`_create_spatial_mask`). Consequences:
  (a) a forecast track cannot be more than ~5 degrees wrong by construction, so errors are truncated
  and optimistic; (b) a "miss" means "no valid peak within the mask", not "wrong place".
* A track needs `timestep_count_wind_minimum=10` lead steps with neighbourhood-peak wind >= 10 m/s
  (>= 60 h of valid steps). Inits late in a storm's life, with fewer than ten remaining steps, can never
  produce a track. That is a structural miss, not model skill.
* Target side: `inputs.IBTrACS` reads the full IBTrACS CSV from NOAA NCEI over the network and filters
  rows by `season` and by storm **name extracted from the case title** (`utils.extract_tc_names`). Our TC
  events are titled "Tropical Cyclone" (name only in `details.name`; two storms are unnamed), so EWB's
  IBTrACS filter would return nothing for them as published. Units are handled (knots->m/s, hPa->Pa;
  USA wind preferred over WMO, matching our `wind_basis` 1-min choice).
* Landfall metrics only exist where a storm crosses from ocean to land. With the coarse 110 m land mask
  I get 24 of 44 published tracks with any ocean->land transition (an upper-bound proxy, island hits
  included); open-ocean storms produce NaN under every Landfall* metric.

### 2.3 Assessment

EWB's own TC metrics are reusable but narrow: they score **landfall events**, not storms. They are
valid, comparable with the upstream benchmark, and cheap once tracks exist, but on their own they would
leave about half the lane (and every storm's open-ocean life) unscored, which is why the owner
asked for along/cross-track on top.

---------------------------------------------------------------------------------------------

## 3. TC scoring proposal

### 3.1 Design in one paragraph

Verify **storm-following forecasts**: for every 00Z/12Z init at which the storm already exists in
IBTrACS, extract the model's MSLP field from the virtual store, track the forecast cyclone forward
from the observed position with a vortex-following tracker, and compare forecast to best track at each
matched valid time. From the same tracks we get position error, along-track and cross-track error,
central-pressure error, a miss indicator, and (optionally) feed EWB's landfall classes.

### 3.2 Option TC-A (recommended): hybrid

* **Forecast track** from our own storm-following tracker in `ewb_live/scoring/track_verif.py`
  (new, pure numpy, unit-testable). It reuses the primitives already in `events/mlc_track.py`
  (`find_peaks_for_time_slice`, closed-contour check with a great-circle cap, haversine in km/deg, the
  antimeridian-safe longitude handling), which are themselves a port of EWB's TC tracker. Algorithm:
  1. Start at the **observed** position at init (`p_o(t_init)`), the standard practice for verifying
     forecast tracks from advisory time.
  2. At each 6-h step L, search for an MSLP local minimum (on the 0.25 degree field, light smoothing) within
     `r_step` degrees of the previous forecast position advanced by the last displacement (persistence
     prediction); `r_step` = 3 degrees (~330 km) per 6-h step. It must pass the closed-contour check
     (2 hPa, 5.5 degrees, same numbers as EWB).
  3. If no valid candidate for two consecutive steps the track is **lost** (lysis, merger, or the model
     never closed a low). All later leads of that init are misses.
  This is deliberately **not** constrained to a fixed 5 degree mask around the observed fix, so errors can
  grow with lead and are not truncated the way EWB's are.
* **Intensity and landfall.** Central pressure is the MSLP at the tracked minimum. Landfall metrics:
  take our forecast track, shape it into EWB's track DataArray (`latitude`, `longitude`, `valid_time` over
  `(lead_time, valid_time)`), build the observed landfall target from our own best-track series, and call
  `LandfallMetric(metrics=[IntensityMAE, TimeME, Displacement], approach="next", ...)` through its
  documented `forecast_landfall` / `target_landfall` kwargs path. This needs a one-day spike (section 7)
  because the classes expect EWB's `init_time`-indexed forecast layout; if the spike fails we fall
  back to our own 30-line landfall detector on `calc.find_landfalls`.

### 3.3 Option TC-B: pure EWB pipeline

Feed the store through `inputs.XarrayForecast(variables=[TropicalCycloneTrackVariables()])`, target
`inputs.IBTrACS()`, `composite_landfall_metrics`, run `run_evaluation`. Pros: exactly upstream numbers,
least new code for the landfall metrics. Cons: only landfall metrics (no along/cross-track unless we also
extract track positions from its output dataset, which is possible but entangled with its spill/cache
machinery); needs titles renamed to storm names and a network IBTrACS read per run; errors truncated at 5
degrees; structural misses for late inits; slow and memory-hungry because it reads global 3-D
thickness fields and runs through the same densifying path that peaked at 184 GB for ARs (the
densify behaviour, not the tracker, is the evidence; no TC run has been measured). Not recommended as the
only path; it remains the reference to validate TC-A's landfall numbers against.

### 3.4 The new metrics, exactly

Notation. Valid time `t = t_init + L`, `L` in {6, 12, ..., 240} h. Observed position at `t`:
`p_o = (phi_o, lam_o)` from the event's `details.track` (IBTrACS, 3-hourly fixes, so every 6-hourly
valid time has a fix when the storm exists). Forecast position `p_f = (phi_f, lam_f)` from the tracker.
`R = 6371.0 km` (same constant as `events/mlc_track.py`). All angles radians; `wrap(x)` maps to
[-180, 180) degrees or [-pi, pi).

1. Distance and bearing from observed to forecast (great circle):
   ```
   dlam   = wrap(lam_f - lam_o)
   a      = sin^2((phi_f-phi_o)/2) + cos(phi_o) cos(phi_f) sin^2(dlam/2)
   d      = 2 R asin(sqrt(a))                                   # total position error, km
   beta_f = atan2( sin(dlam) cos(phi_f),
                   cos(phi_o) sin(phi_f) - sin(phi_o) cos(phi_f) cos(dlam) )   # bearing o -> f, clockwise from north
   ```
2. Observed motion direction `theta_m(t)` (centered difference of **observed** positions):
   * Use observed fixes at `t - 6 h` and `t + 6 h`. Project each into the local tangent plane at `p_o(t)`:
     for a point `q`, `(d_q, beta_q)` = great-circle distance and bearing from `p_o(t)` to `q`,
     `(x_q, y_q) = (d_q sin beta_q, d_q cos beta_q)` (east, north, km). Motion vector
     `v = ((x_+ - x_-), (y_+ - y_-)) / (t_+ - t_-)`, `theta_m = atan2(v_x, v_y)`, speed `|v|`.
     Projecting at `p_o(t)` handles the antimeridian and high latitudes without special cases.
   * **First fix** (no `t-6 h`): forward difference with `t+6 h`. **Last fix**: backward difference with
     `t-6 h`. If the 6-h neighbour is missing but the 3-h neighbour exists, use the 3-h one-sided
     difference. If fewer than two usable fixes exist within +/-6 h, `theta_m` is undefined and
     along/cross for that sample are not emitted (position error still is).
   * **Stationary storms**: if `|v| < 2 km/h` (about 1 kt, less than 24 km in 12 h) the direction is not
     meaningful; along/cross are not emitted for that sample (position error still is).
3. Components (angles measured clockwise from north, so a positive angle from the motion direction
   to the error is to the **right**):
   ```
   delta        = wrap(beta_f - theta_m)
   along_track  = d cos(delta)     # > 0: forecast ahead of the observed storm (too fast)
   cross_track  = d sin(delta)     # > 0: forecast right of the observed track
   ```
   Checks: forecast displaced straight ahead gives `(+d, 0)`; displaced 90 degrees clockwise from the
   motion gives `(0, +d)`; `along^2 + cross^2 = d^2`. This matches the convention in the request (the
   cited Leonardo & Colle (2017, *Weather and Forecasting*) paper; I have not re-read it here and the
   exact sign statement should be confirmed, D10).
4. Central-pressure error: `CentralPressureError = mslp_fc_min - p_obs` in hPa, `p_obs` =
   IBTrACS USA/WMO pressure in the track row (slot 5), forecast value = MSLP at the tracked minimum.
   Positive = forecast too weak. Not emitted when the observed pressure is missing.

Matching by lead. A sample is `(event, model, init, L)` where the observed storm exists at `t_init` (D2)
and a fix exists at `t_init + L`. Leads that overshoot the storm's end have no sample. Models with
different init cadences use the pipeline's existing "nearest earlier init" rule
(`sources.api.open_forecast_nearest_init`); the lead then shifts by the offset and the row records the
true lead.

Aggregation.
* Rows: one per `(event, model, init, L)` with `lead_time_hours = L`, `init_time = t_init`; the
  existing `build_scorecard` then gives a per-lead mean over all events and inits, a bootstrap CI over
  storms, and the "Mean" column over leads. This is the same granularity as the AR/heat rows (D5).
* Event mean: the "event" column is the mean of all samples of a storm; since the bootstrap weights
  a storm by its row count, long-lived storms weigh more. If equal weight per storm is preferred, emit one
  row per `(event, model, L)` (mean over inits, `init_time` NaT); one switch, decided in D5.
* Signed metrics (`AlongTrackError`, `CrossTrackError`, `CentralPressureError`, `landfall_time_me`)
  go into `SIGNED_ERROR_METRICS` so the scorecard suppresses "skill vs baseline" for them (as for
  `DurationMeanError`), and the table styles them with `dir: 'zero'` (bias to zero). The skill-ranked
  metric is `TrackPositionError` (>= 0) and the miss rate.

### 3.5 Missed or lost forecast storms

* A sample where the observed storm exists at `t_init + L` but our tracker has lost or never closed the low
  at that step: **no value** for the error metrics, and one `TrackMiss = 1` row. Present samples emit
  `TrackMiss = 0`. The mean of `TrackMiss` in a cell is the miss rate; `n` of the error metrics vs
  `n` of `TrackMiss` gives the count of misses, shown in the cell tooltip ("23 of 31 samples tracked").
* Never impute: a miss is not given a large error (that would hide the failure inside a mean) and is
  not dropped silently either.
* Whole-pair statuses reuse the vocabulary: `no_data` (no init in store for the storm window), `empty`
  (no verifiable init, e.g. the storm only lasted shorter than one init interval), `scored`.
* Skill vs baseline: `build_scorecard` computes skill on the intersection of events both models
  scored, but it does **not** account for misses. A model that tracks only the easy storms would look
  good on position error; the cell must show `TrackMiss` beside it (the dashboard shows both columns
  per lead), or the dashboard must grey a cell whose miss rate exceeds a threshold (D5 sub-point).

### 3.6 Which forecast positions and intensities the store can actually supply (the TC-8 follow-up)

Measured by TC-8 (`notes/tc_score_probe.py`, 36 reads, graphcast/aurora/pangu_weather/fourcastnet_v2,
24/48/72 h, three strong storms) plus my recompute against the event tracks:

* **Position: yes.** The MSLP minimum is within 300 km of the best-track fix in 32/36 probes (median
  156 km). Position is the honest statistic; the 10 m wind maximum is **not** a reliable centre at
  0.25 degrees (median 314-686 km, it chases outer bands), so the tracker must be MSLP-based and the
  wind field must not be used for position.
* **Caveat on that evidence.** The probe checked "is there an MSLP minimum near the fix" from a single
  genesis-time init per storm. It is a presence test, not a tracker run; misses of a following tracker
  across 40 leads x many inits are not measured yet. That is what the pilot must produce.
* **Intensity by 10 m wind: not verifiable.** Forecast peak wind minus best-track wind is about -4 kt at
  24 h and -14 to -24 kt at 48-72 h; this is the 0.25 degree floor, not forecast error. A wind error
  number would be a resolution number. Do not publish it.
* **Intensity by central pressure: partly feasible, as a bias, not as skill.** Re-matching the probe rows
  to the event track pressures (36 rows; best-track pressure present for 32): median `fc - obs` MSLP
  is +0.7 hPa at 24 h, +0.9 hPa at 48 h, **+4.6 hPa at 72 h**, with a maximum of **+52 hPa** (a storm
  that deepened rapidly, Champi-like, while the model barely did). Early stages are close; rapid
  intensification is invisible to 0.25 degree AI models. So `CentralPressureError` is useful and
  honest as a **signed bias with a resolution warning**, and EWB's `LandfallIntensityMeanAbsoluteError`
  (MSLP at landfall) inherits the same limitation.
* Small sample (3 storms, one init each, matched to the nearest 3-hourly fix): indicative only.

### 3.7 What TC scoring can and cannot tell us

* Tells: where each model puts a storm as lead grows, whether it runs fast/slow and left/right of the
  real track, whether it carries the storm at all, how weak it is in pressure terms, and (landfall
  subset) landfall place/time/pressure errors.
* Does not tell: wind intensity or rapid intensification skill; genesis (pre-genesis inits excluded,
  D2); storm structure/size; skill for storms that are not in IBTrACS yet (the lane waits for the
  archive, ~3-4 days lag: `meta.ibtracs`); anything about ECMWF-direct models unless D4 includes them.

### 3.8 Cost, failure modes

* **Work size** (computed from `events_selected.json`, 12-hourly inits from first fix to last fix, leads
  where a fix exists): 674 `(storm, init)` combinations, **13 345** `(init, lead)` samples per model.
  8 virtual-store models -> 5 392 `(model, init)` track runs, ~107 k samples.
* **Reads** per `(model, init)`: leads where the storm exists, avg ~20, `msl` plus (for the wind
  diagnostic only) `u10`/`v10`: ~1 s per lead (TC-8 measured 0.81 s median). **~20 s per `(model, init)`
  -> ~30 h serial, ~2.5-3 h with 12 workers** for the full backfill of 8 models (estimate, to be
  measured by the pilot). Four IFS-initialised models only: ~1.3 h. Weekly incremental: new inits only,
  ~20-40 min.
* **Memory**: bbox windows of ~40x40 degrees x 40 leads x float32 is tens of MB; estimate <3 GB/worker.
  The risk is the store's open path: `open_model` costs ~8 GB RSS per model; the scorer must use
  `open_init` (0.8 s, one era group), which the existing `sources/aiwp.py` already does.
  Run it in its own pool, not inside the AR job pool (that pool peaked at 183.8 GB).
* **Failure modes**: tracker jumping to a neighbouring low (binary-storm, Fujiwhara), losing a weak or
  decaying storm early (misses), storms near the dateline (existing `unwrap_lons`/wrap handling must be
  reused, 4 published TCs have wrapped bboxes), `window_truncated` and `ongoing` storms (fingerprint changes
  as the storm extends so they re-score; do not publish a track error for a still-ongoing storm's
  verifying tail beyond the archive edge), stationary storms (cross/along undefined), fill values in
  the first leads (GraphCast), IFS-HRES-direct baseline missing a variable for some inits, GFS-initialised
  members with upstream gaps (nearest-earlier-init rule).

---------------------------------------------------------------------------------------------

## 4. MLC scoring proposal

### 4.1 Truth

The event's own track (`details.track`, rows `[iso, lat, lon, central MSLP, pressure]`, 6-hourly)
from the ERA5 MSLP tracker (mlc-v3). Unlike TC, truth and forecast are **both 0.25 degree MSLP**, so
there is no resolution floor: central pressure of a mid-latitude low is resolved by AI models and is a
fair target. Earlier (audit_mlc F5) the lane was left unscored because "the event *is* the analysis
minimum" adds no information; that is true only of comparing ERA5 against ERA5. Comparing the
**forecasts** against it is the information.

### 4.2 Option MLC-A (recommended): track + central-pressure verification, same core as TC

Same machinery as section 3.4/3.5: storm-following tracker from the observed position at each init at
which the low already exists, MSLP only (no wind, no thickness), then
`TrackPositionError`, `AlongTrackError`, `CrossTrackError`, `CentralPressureError` (forecast min MSLP minus
ERA5 min MSLP at the matched time, hPa), `TrackMiss`. Differences from TC: step search radius 4-5 degrees
per 6-h step (MLCs move 30-60 km/h and deepen explosively), the 3 hPa closed-contour recipe from
`mlc_track.Params` rather than the TC 2 hPa, no landfall metrics, motion vector from the 6-hourly track. At 6-hourly
fixes the 12-h centered difference is exactly the 6 h + 6 h neighbours.

Along-track is the interesting component for MLCs (fast movers: timing/phase errors dominate); the
existing lane has no other forecast-vs-observed signal at all.

### 4.3 Option MLC-B: event-peak verification without tracking

For each `(event, model, init)`: forecast minimum MSLP over a +/-8 degree window around the observed track
at each valid time vs the ERA5 central pressure (`PeakDepthError`, hPa) plus a deepening verification:
24-h fall in forecast vs observed and a bomb hit/miss (contingency on the event's `details.bomb`
flag). Pros: no tracker, no loss-of-track failure mode, trivial cost. Cons: window-minimum can pick
a different low (ambiguity), no position information, bomb counts may be tiny (check
`details.bomb` frequency before promising a table). A reasonable cheap first deliverable; the
cost of adding it to MLC-A is ~1 agent-h because the field reads are shared.

### 4.4 Cost and failure modes

* 80 events; 600 `(event, init)` combinations (12-hourly, the low exists at init), **5 504** `(init, lead)`
  samples per model; `msl` only. ~9 leads/init -> ~43 k reads for 8 models, ~0.3-0.8 s each: ~4-6 h
  serial, **~20-40 min with 12 workers** (estimate). Memory small. Weekly: minutes.
* Truth horizon: ARCO MSLP ends ~11-12T18Z (latency ~6 days). Events/leads whose valid time is past the
  ARCO end have no truth; they are not scored (no IFS-analysis splice in v1; the lane's event tracks
  already stop at the truth edge). Ongoing events re-score on fingerprint change.
* Failure modes: low merges/splits, secondary cyclogenesis, orographic MSLP reduction artefacts (the
  same in ERA5 and the model, so mostly cancel; still gate on `details.genesis_elev_m`), dateline/antimeridian
  wrap (14 published MLC tracks have wrapped bboxes), models diverging from IFS-initialised
  conditions at long leads (more misses; report the miss rate with every error).
* What it cannot tell us: precipitation/wind impact of the low, frontal structure, genesis forecast
  (inits where the low does not yet exist are excluded, D2), skill for lows ERA5 did not track.

---------------------------------------------------------------------------------------------

## 5. Flood scoring proposal

### 5.1 Truth and forecast variables

* Truth: PARDIG daily totals on the ERA5 0.25 degree lattice (`data/pardig/daily`, mm/day; units confirmed in
  `details.data_source`). It is an **AI-estimated, provisional** product, and the events are *defined*
  by it. That makes any score conditional on events chosen where PARDIG exceeded the threshold; the
  dashboard copy must say "vs AI-estimated precipitation (PARDIG)" and not "vs observed rain".
* Threshold (the event's own definition): `max(ERA5 wet-day p95 for that day-of-year, 20 mm)` for the daily
  lane, `max(p95 of the 72 h accumulation, 50 mm)` for the 3-day lane (`details.lanes`, `precip_clim`
  `precip_threshold_on`). The same threshold applies to forecast and truth.
* Forecast: store `apcp` (m, 6-h accumulated). Daily total = sum of the four 6-h bins of the UTC day;
  3-day = sum of 12 bins. **Only GraphCast has it** (`graphcast`, `graphcast_gfs`). Aurora, Pangu and
  FourCastNet v2 carry no precipitation in the store (FourCastNet has `tcwv`, which is not rain), so
  six of eight virtual-store models are `unsupported: no precipitation variable` for flood
  (documented zero-row pairs, as aigfs already is for temperature). ECMWF-direct models (`ifs_hres`,
  `aifs_single`) publish `tp` in ECMWF open data, but it is not in `sources.registry._ECMWF_VARS` and
  not in the virtual store (D6).
* Data caveats measured above: IFS-initialised GraphCast has no valid `apcp` at 0 h and 6 h, so its first
  complete daily total is day 2 (lead 48 h as the end of the bin); the GFS-initialised member has
  valid bins from 6 h. Clip small negatives to 0. The ERA5 low bias on precipitation is accepted; note
  GraphCast was trained on ERA5, so its rain distribution is ERA5-like, which is a consistent comparison
  against ERA5-based thresholds even if PARDIG itself is higher in places.

### 5.2 Option F-A (recommended): daily exceedance contingency + areal accumulation error

Verification domain: land cells of the event bbox padded by 2 degrees, **days** from `event.start - 2` to `event.end + 2` so false alarms outside the footprint and
outside the event days are counted. For each `(event, model, init-day-lead)`:

* Hit/miss/false alarm per cell-day on the threshold -> `CriticalSuccessIndex`, `FalseAlarmRatio`,
  probability of detection (EWB's own `ThresholdMetric` classes accept separate forecast/target
  thresholds, so they can be called directly on our DataArrays).
* Because a 0.25 degree cell-wise match is harsh for 6-h AI QPF, also a **Fractions Skill Score** at
  neighbourhoods of 1, 3 and 5 cells (new, ~20 lines, standard definition).
* `PrecipAreaBias`: forecast minus truth of the footprint-accumulated rain over the event window (mm,
  signed, area mean over cells that exceeded in truth) and the peak-cell daily error (signed mm).
* `lead_time_hours` = end of the verified day's accumulation measured from the init (24, 48, ..., 240
  for 00Z inits). 00Z inits only (as the existing pipeline does), nearest-earlier-init rule for gaps.
* Signed metrics (`PrecipAreaBias`) go in `SIGNED_ERROR_METRICS`; CSI/FSS in `HIGHER_IS_BETTER`.

### 5.3 Option F-B: event-level hit/miss only

One yes/no per `(event, model, lead)`: "did the forecast produce at least one cell-day over the
threshold in the footprint during the event window". Cheapest, but with 40 events and 2 models the
table is a handful of 0/1 cells; use it only as a fallback if the owner defers precipitation depth
verification.

### 5.4 Cost and failure modes

* Reads: per event ~12 daily 00Z inits x ~(event days x 4 bins) `apcp` bins x 2 models ~ 19 k reads
  total, ~0.65 s each (measured): ~3.5 h serial, **~20-30 min with 12 workers** (estimate). Memory
  trivial (a 0.25 degree window of ~1800 cells).
* Failure modes: PARDIG missing days (`missing_days` in `details.data_source`; 94/95 days present, the
  last day absent) -> that day is not scored; fill/negative values; only two models so skill vs a
  baseline needs `ifs_hres` direct `tp` (D6) or the table has no colour reference; the verification domain
  choice moves CSI a lot (conventions must be pinned in the About text); selection bias of events
  defined from the same truth (CSI is conditional on a truth exceedance existing, so POD is inflated
  relative to an unconditional verification).
* What it cannot tell us: flood (runoff/river) impact, sub-daily intensity, skill of the 6 models with no
  precipitation in the store, absolute accuracy of the truth (AI product, provisional).

---------------------------------------------------------------------------------------------

## 6. How the new rows fit the existing schema and dashboard

No schema change. `scores.parquet` keeps `event_id, event_type, model, metric, lead_time_hours, value,
init_time, fingerprint, truth_source`.

| Lane | `metric` strings | unit | signed (skill suppressed) | higher is better |
|---|---|---|---|---|
| TC, MLC | `TrackPositionError` | km | no | no |
| | `AlongTrackError`, `CrossTrackError` | km | **yes** | n/a |
| | `CentralPressureError` | hPa | **yes** | n/a |
| | `TrackMiss` | 0/1 (rate) | no | no |
| TC only | `landfall_displacement` km, `landfall_time_me` h (**signed**), `landfall_intensity_mae` hPa | | | |
| Flood | `CriticalSuccessIndex`, `FractionsSkillScore` | | no | **yes** |
| | `FalseAlarmRatio` | | no | no |
| | `PrecipAreaBias` | mm | **yes** | n/a |

* `lead_time_hours` is the true lead; landfall rows use observed-landfall time minus init.
* Truth tags in `truth_source`: `ibtracs`, `era5_arco` (MLC; reuses the existing tag so ARCO-availability
  invalidation still works), `pardig` (flood). `fingerprint` already changes with start/end/bbox, which is
  what an ongoing storm needs.
* Pair statuses: TC/MLC: `scored`, `no_data`, `empty`. Flood: six models `unsupported: no precipitation
  variable in the MLWP store`.
* Dashboard: remove `noScores` for the lane in `TYPE_META`, register the metrics in
  `FALLBACK_METRICS` (label, units, `dir: 'zero'` for signed ones), treat `TrackMiss` and the
  miss rate as a binary-formatted column, add About text for each metric (definitions and the sign
  conventions above), and replace `tcNotScoredNote` with a scored-lane note that still states
  the IBTrACS lag.
* Per-event story/detail panel: out of scope for scoring itself; a follow-up can plot forecast vs
  observed tracks from a small per-event JSON the scorer also writes (not in the parquet).

---------------------------------------------------------------------------------------------

## 7. Effort, order, and the pilot

Estimates (focused agent hours = coding + tests + one review cycle; compute = LXC wall clock). These
are my estimates, not measurements, except where marked.

| Work item | Agent-h | Compute (wall) | Notes |
|---|---|---|---|
| **P0 pilot/spike**: our tracker on 3 storms x 4 models x all inits; measure miss rate, RSS, s/init; EWB landfall classes fed our tracks | 4-5 | ~10-15 min | decides D1 and the cost numbers; **do this first** |
| Shared track core (`scoring/track_verif.py`: geometry, motion vector, along/cross, tracker, matching, miss accounting; pure numpy tests) | 4-5 | 0 | used by TC and MLC |
| TC lane (IBTrACS adapter from `details.track`, store reader, rows, pipeline dispatch, pair statuses, dashboard copy, tests) | 8-10 | full backfill 2.5-3 h (8 models), weekly 20-40 min | |
| TC landfall (EWB classes via kwargs, validate vs TC-B on 3 landfalling storms) | 4-6 | ~30 min | optional if the spike fails |
| MLC lane | 4-5 | 20-40 min | reuses core; includes MLC-B peak metric (+1) |
| Flood lane (registry `apcp` mapping, daily accumulation, thresholds, CSI/FSS/area bias, pair statuses, tests) | 8-10 | 20-30 min | most plumbing, least science risk |
| Dashboard + About copy for all three | 3-4 | 0 | can ride each lane's PR |
| **Total** | ~35-45 | ~5-6 h one-off, ~1 h/week after | |

Recommended order:

1. **P0 spike** (half a day). If the follow-tracker's miss rate on the three strong storms is acceptable
   and cost matches, go; if not, take TC-B (pure EWB) for landfall only and decide D1 differently.
2. **Shared core + TC track metrics** (owner's stated priority; the core is built under the lane that
   stresses it hardest).
3. **MLC** (second user of the core; resolution-matched truth, 80 events gives a real sample).
4. **EWB landfall metrics** for TC (opt-in once track metrics are live).
5. **Flood** (most plumbing, two models, provisional AI truth: lowest scientific return, so last).

One PR per lane against `develop`, no deploy until the owner has seen the pilot numbers.

---------------------------------------------------------------------------------------------

## 8. Owner decisions needed

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | TC forecast-track method | (A) own storm-following tracker + EWB landfall classes; (B) EWB pipeline only | A; B stays as validation reference |
| D2 | Which inits are verified | storm must already exist in IBTrACS at init (**exclude pre-genesis**) vs also count pre-genesis inits as misses | exclude; genesis forecasting is a separate lane later |
| D3 | TC intensity | MSLP bias only; or also 10 m wind | **MSLP only**, with the resolution warning; no wind number |
| D4 | Model set and baseline for TC/MLC | 8 virtual-store models only (no `ifs_hres` skill colouring) vs also read `ifs_hres` (and `aifs_single`) via the existing ECMWF-direct path for the baseline | include `ifs_hres` as the baseline reference so skill colouring works; it is the only non-virtual read and TC-8 scoped the store only, so explicit approval needed |
| D5 | Row granularity and miss handling | per-init rows (long-lived storms weigh more) vs per-(event, lead) means; grey cells above a miss-rate threshold | per-init rows (matches AR/heat), show miss rate beside every error; threshold 50 % to grey |
| D6 | Flood scope | GraphCast-only (2 of 8 models) vs also add ECMWF-direct `tp`; label "vs AI-estimated precip (PARDIG)"; or defer flood | GraphCast-only + ECMWF `tp` for ifs_hres as baseline, with the provisional-truth label; defer if the owner dislikes scoring vs an AI product |
| D7 | MLC metrics | MLC-A (track + pressure) now, MLC-B peak/bomb extra; truth = ERA5 ARCO track as published | MLC-A, MLC-B as a cheap add-on |
| D8 | Leads shown in the tables | store all 6-hourly leads; dashboard columns, e.g. 24/48/72/96/120 h for TC, 24/48/72/96 h for MLC, 24..120 h flood | as stated |
| D9 | Compute window | OK to run a 2.5-3 h backfill on the LXC with 12 workers in its own pool, capped at ~40 GB RSS, outside the weekly run | yes, after the pilot memory number |
| D10 | Sign convention | confirm along-track > 0 = ahead of observed storm, cross-track > 0 = right of observed track (Leonardo & Colle 2017) and the 2 km/h stationary cutoff; also the 6 h / 12 h centered-difference baseline | as specified in section 3.4 |

---------------------------------------------------------------------------------------------

## 9. Evidence and method

All on the LXC (`root@192.168.0.121`), read-only, each command under `timeout`, 2026-11-20:

* `events_selected.json` (`/opt/ewb-live/data/results`): TC 44, MLC 80, flood 40 (also 15 heat, 13 AR,
  8 cold snap, 5 freeze). Init/lead sizing computed from `details.track` (TC fixes 3-hourly, MLC 6-hourly):
  TC 674 inits / 13 345 samples, MLC 600 / 5 504 (12-hourly inits at/after first fix and before last
  fix, leads where a fix exists). Flood: median duration 5.3 d, median bbox 115 deg^2.
* Store (`ewb_live.stores.mlwp_icechunk`): `list_models`, `list_inits`, `open_init` per family;
  variable lists and `apcp` attrs/lead validity as in section 1.3; timing of regional `z` and `apcp` reads.
* `data/perf.json`: `peak_total_rss_gb` 183.84 (the last weekly run, 4 jobs, 35 events).
* TC land-crossing proxy: `viz.build.land_mask` (110 m) over the published tracks: 24/44 with an
  ocean->land transition.
* TC-8 re-match: `notes/screenshots/tc_score_probe.json` rows joined to `details.track` pressure; 36
  rows, 32 with pressure; medians in section 3.6.
* EWB source read, not run: `metrics.py` (Landfall* classes), `defaults.py:471-536`,
  `derived.py:88-300`, `events/tropical_cyclone.py`, `inputs.py` IBTrACS, `calc.find_landfalls`.

Not done (needs the pilot): any track-follower run, any EWB TC pipeline run on the store, a measured
RSS for the new scorers, `build_region_scorecards` behaviour on new types, the Leonardo & Colle text.
