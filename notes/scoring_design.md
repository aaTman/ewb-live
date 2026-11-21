# Scoring design for the TC, MLC and flood lanes

Status: **design approved by the owner on 2026-11-20 (decisions D1-D10, section 7); implementation plan in
section 8.** This document contains no scoring code and nothing here is deployed. Everything
below is read from the repo at `origin/develop` (30faee0) and from read-only probes on the
EWB LXC; measured numbers say so, estimates say so.

Owner decision folded in (decided before this note; supersedes the "TC-8 metric options" part of the brief): the TC lane
is **not** track-presence-only. It gets ExtremeWeatherBench's own TC metrics **plus** two new
metrics, along-track error and cross-track error (Leonardo & Colle 2017 convention).

---------------------------------------------------------------------------------------------

## 0. Summary and the decisions I need

| Lane | Recommendation | Metrics (rows in `scores.parquet`) | Models scored | Cost (estimate) |
|---|---|---|---|---|
| **TC** (44 storms) | Hybrid: our own storm-following tracker on the store's MSLP gives forecast positions; EWB's landfall classes fed the same tracks | `TrackPositionError` km, `AlongTrackError` km (signed), `CrossTrackError` km (signed), `CentralPressureError` hPa (signed), `TrackMiss` 0/1, plus EWB `landfall_displacement`, `landfall_time_me`, `landfall_intensity_mae` (only where a landfall exists) | 8 virtual-store models (+ `ifs_hres` direct as baseline, see D4) | ~14-20 agent-h, ~2-4 h LXC wall for the first full backfill, ~20-40 min/week after |
| **MLC** (80 lows) | Same shared track core; verify against the ERA5 MSLP track the event came from | `TrackPositionError`, `AlongTrackError`, `CrossTrackError`, `CentralPressureError`, `TrackMiss` | same | ~8-10 agent-h, <1 h wall |
| **Flood** (40 events) | Daily exceedance contingency on the event's own threshold + areal accumulated-precip error. Only GraphCast carries precipitation in the store | `CriticalSuccessIndex`, `FalseAlarmRatio`/POD, `FractionsSkillScore`, `PrecipAreaBias` mm | `graphcast`, `graphcast_gfs` only (2 of 8) | ~8-10 agent-h, <1 h wall |

Order: pilot (WP0), shared track core + TC track metrics (WP1), MLC (WP2), EWB landfall classes (WP3), flood (WP4); section 8.

**Decisions** (owner-approved 2026-11-20, all accepted as recommended): see section 7 (D1-D10). **Plan**:
section 8 (work packages WP0-WP4, rule R1: no backfill overlaps the Monday 07:00 UTC weekly run).

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
  EWB densified arrays; my estimate is <3 GB per worker, but the pilot (WP0, section 8.2) must measure it
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
  documented `forecast_landfall` / `target_landfall` kwargs path. This is checked in the WP0 pilot and built in WP3 (section 8)
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
   exact sign statement is the owner-approved convention, D10).
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
  per lead), so a cell whose miss rate exceeds 50 % is greyed (D5).

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
  archive, ~3-4 days lag: `meta.ibtracs`); anything about ECMWF-direct models other than the `ifs_hres` baseline (D4).

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
table is a handful of 0/1 cells; not part of the plan; kept for reference only.

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

## 7. Decisions (owner-approved 2026-11-20)

The owner accepted D1-D10 exactly as recommended in the first revision of this note. These are now
the specification; the sections above are written to match them.

| # | Decision | Accepted answer |
|---|---|---|
| D1 | TC forecast-track method | **Hybrid (TC-A).** Our own storm-following tracker on the store's MSLP gives forecast positions, along/cross-track, central pressure and misses; EWB's landfall classes are fed the same tracks. The pure EWB pipeline (TC-B) is kept only as a validation reference for the landfall numbers. |
| D2 | Which inits are verified | Only inits at or after the storm's first ERA5-tracker (MLC) fix, i.e. the storm exists at init. **TC: amended 2026-11-21 (below): verification starts at the first observed fix with wind >= 34 kt, not the first IBTrACS fix.** Pre-genesis inits are excluded (genesis forecasting is a separate future lane). |
| D3 | TC intensity | **Central-pressure error (MSLP) only**, published as a signed bias with the 0.25 degree resolution warning. **No 10 m wind error is published.** |
| D4 | Model set and baseline for TC/MLC | The 8 virtual-store models **plus `ifs_hres` read through the existing ECMWF-direct path as the baseline**, so skill colouring works. This is the one non-virtual read and is approved for TC and MLC. |
| D5 | Row granularity and misses | **One row per `(event, model, init, lead)`** (same granularity as AR/heat); the miss rate is shown beside every error metric; a cell whose miss rate exceeds **50 %** is greyed. Misses are never imputed and never dropped silently. |
| D6 | Flood scope | **GraphCast only** (`graphcast`, `graphcast_gfs`) from the store, plus **ECMWF-direct `tp` for `ifs_hres` as the baseline**. The six models without precipitation are documented `unsupported`. Copy says "vs AI-estimated precipitation (PARDIG)", never "observed rain". |
| D7 | MLC metrics | **MLC-A** (track position, along/cross-track, central pressure, miss) with the published ERA5 (ARCO) track as truth, **plus MLC-B** (peak-depth error, deepening/bomb hit-miss) as a cheap add-on. |
| D8 | Leads shown in tables | All 6-hourly leads are stored; the dashboard columns are 24/48/72/96/120 h for TC, 24/48/72/96 h for MLC, 24-120 h (daily) for flood. |
| D9 | Compute window | A TC backfill on the LXC is approved, 12 workers, own pool, **<= 40 GB total RSS**, **never overlapping the weekly run** (section 8, rule R1), after the pilot's measured memory number. **Pool design amended 2026-11-21 (below): one pool with worker recycling.** (The 2.5-3 h estimate was replaced by the WP0 pilot's ~25 min, `notes/scoring_pilot.md`.) |
| D10 | Sign convention and motion | **Along-track > 0 = forecast ahead of the observed storm; cross-track > 0 = right of the observed track** (Leonardo & Colle 2017), observed motion from the centered 6 h + 6 h difference of observed positions, one-sided at the first/last fix, 2 km/h stationary cutoff, exactly as section 3.4. |

**Amendments (owner, 2026-11-21, after the WP0 pilot, `notes/scoring_pilot.md`).** Both are in force for WP1
and are implemented in this PR.

* **D9 (pool): ONE worker pool with worker recycling every 30 jobs, no split pool.** The pilot's 4-worker run of the
  full 9-model job mix projected 40.66 GB at 12 workers (3.347 GB per worker), marginally over the 40 GB cap; the cause
  is slow per-worker growth with the number of jobs a worker has run, and `max_tasks_per_child=30` measured 2.433 GB per
  worker, **29.7 GB projected at 12 workers**, at no wall-clock cost (338 s vs 405 s). The alternative (a 12-worker store
  pool plus a separate 2-worker `ifs_hres` pool) is not built: `ifs_hres` stays in the same pool as the eight store models.
  `scoring/lane_pool.py` asserts the cap (stops and drains above `EWB_LIVE_SCORING_RSS_CAP_GB`, default 40; exit 6).
* **D2 (TC only): verification starts at the FIRST observed fix with wind >= 34 kt, not at the first IBTrACS fix.** 00/12Z
  inits before that fix are excluded from scoring and reported as *not verified* (`meta.track_verification`, and the
  note under the TC table); pre-genesis misses are never counted. Reason (pilot 3.1): for inits issued while the storm is
  still a < 34 kt disturbance the miss rate is 80 % (7 % from hurricane-stage inits), because no 0.25 degree model
  carries a closed low yet, so those inits measured genesis, not track skill. The MLC lane keeps D2 as written (first
  ERA5-tracker fix). Inits with a fix at the init time and one 6 h later remain the other conditions.

Implementation notes that follow from the pilot (WP1): store gaps (4 `_gfs` models at 5 inits) are `no_data` inits, never
misses; S3 reads are retried (4 attempts, linear backoff; ~4 % of reads fail once); the sample records the TRUE init and
TRUE lead when the nearest-earlier-init rule supplied the forecast.

---------------------------------------------------------------------------------------------

## 8. Implementation plan

### 8.0 Rules for every work package

> Amended 2026-11-20: R1 is state-based now. The weekly unit's `TimeoutStartSec` is **24 h**
> (`ops/ewb-live-weekly.service`, merged) and the last weekly run took **~9 h**, so the fixed clock window of
> Monday 06:00-13:00 UTC it was originally sized from (old 6 h timeout) is retired.

* **R1: backfills never overlap the weekly run.** `ewb-live-weekly.timer` fires **Monday 07:00 UTC**
  (`Persistent=true`, so a catch-up can fire at any time), `ewb-live-weekly.service` is `Type=oneshot` with
  **`TimeoutStartSec=24h`**, and the last weekly run took **~9 h** wall clock and peaked at **183.8 GB**
  total RSS on a 256 GB box (`data/perf.json`); a 40 GB backfill pool on top leaves no headroom. A weekly run
  of unknown length can be live at any hour, so the guard reads **state, not the clock**:
  * **Never start** a backfill/pilot job while the weekly run is live or pending: refuse if
    `systemctl is-active ewb-live-weekly.service` is `active` or `activating`, or the weekly/deploy flock is
    held (`/opt/ewb-live/.deploy.lock`, `flock -n`).
  * **Refuse from Monday 06:30 UTC until Monday 14:00 UTC** while the weekly service is not yet active - it
    may be about to start - unless it has already run that week, i.e. `systemctl show
    ewb-live-weekly.service -p ActiveExitTimestamp` is a time at or after this Monday 07:00 UTC. Outside
    those 7 h there is no clock restriction at all: the state checks alone decide.
  * Every backfill is launched through one guard (WP1 adds `ops/lane_backfill.sh`): it runs the two checks
    above, sets `timeout` to the hard cap, and **polls the same state every 30 s** while the job runs. If the
    weekly service becomes active or the flock is taken, it sends **SIGTERM** to the pool, waits for it to
    exit and leaves a resumable partial result (part files are keyed by `(event, model, fingerprint,
    truth)`, so a rerun resumes).
  * Pilot runs (< 15 min, < 10 GB) are exempt from the 40 GB cap but not from the guard.
* **R2: LXC only for compute**, via `ops/remote.sh`; every foreground command under `timeout` (<= 10 min),
  anything longer in the background with a log polled by short commands; unit tests with tiny synthetic
  data may run locally (`.venv/bin/python -m pytest tests -q`).
* **R3: one PR per work package, base `develop`** (verify with `gh pr view --json baseRefName`), no PNGs
  committed, no deploy and no merge by the agent; shared files (`pipeline.py`, `events/select.py`,
  `dashboard/app.js`, `sources/registry.py`) get minimal additive edits; new logic goes in new modules.
* **R4: scoring runs in its own worker pool** (`EWB_LIVE_SCORING_WORKERS`, default 12), reading with
  `stores.mlwp_icechunk.open_init` only (never `open_model`: ~8 GB RSS each), per-read timeouts, and a
  `ulimit -n` high enough for icechunk (the daily store update already hit "Too many open files").
* **R5: honest results.** A work package ends with a channel entry in `notes/channel_scoring.md`
  (append-only, same header format as the other channels) stating measured numbers, tests run and
  anything left open.

### 8.1 Order and budget

| WP | What | Depends on | Agent-h | LXC wall (estimate) |
|---|---|---|---|---|
| WP0 | Pilot: one TC + one MLC event, measured memory/time | none | 4-5 | ~10-15 min |
| WP1 | Shared track core + TC track metrics + TC lane wiring | WP0 | 12-15 | pilot repeat 5 min; backfill 2.5-3 h (8 models + baseline) |
| WP2 | MLC lane (MLC-A + MLC-B) | WP1 core | 5-6 | 20-40 min backfill |
| WP3 | EWB landfall classes for TC | WP1 | 4-6 | ~30 min |
| WP4 | Flood lane | WP1 wiring only | 8-10 | 20-30 min |
| | **Total** | | ~33-42 | ~4-5 h one-off, ~1 h/week after |

Dashboard and About copy ride in each WP's PR (the lane's `noScores` is only removed in the PR whose
rows exist on the box).

### 8.2 WP0: pilot (measure first)

* **Goal:** replace every estimate in this note that gates a later package with a measurement on one TC
  and one MLC event.
* **Events:** TC = the published storm `Polo` (EP, peak 155 kt, has an ocean->land transition; if it is not in
  `events_selected.json` take the strongest published storm that is flagged as crossing land by the
  110 m mask); MLC = the longest-lived published non-wrapped low (`details.lifetime_h` max, preferably
  `details.bomb` true).
* **Models:** all 8 virtual-store models + `ifs_hres` direct, every 00/12Z init with the storm present.
* **Files to add:** `ewb_live/tools/track_pilot.py` (throwaway CLI, imports a minimal prototype of the
  storm-following tracker from a module the WP1 core will absorb), `tests/test_track_pilot_smoke.py`
  (synthetic closed-low field: tracker follows it; fails when the low vanishes).
* **Files to touch:** none outside `ewb_live/tools/` and `notes/`.
* **Output:** `notes/scoring_pilot.md` + `notes/screenshots/scoring_pilot.json` (JSON only, no PNGs):
  per `(model, init)` read seconds, tracker seconds, peak RSS per worker and for the pool (sampled like
  `pipeline._PeakRss`), miss rate by lead, median/p90 position error by lead, along/cross sign sanity
  (a storm known to recurve), and the EWB landfall classes fed the pilot tracks for the TC event (works /
  fails and why).
* **Tests:** the smoke test above under `.venv/bin/python -m pytest tests -q` (offline).
* **Acceptance:** (1) pilot ran inside R1 and under `timeout`; (2) measured numbers for s/(model,init),
  peak RSS/worker, pool RSS at 12 workers (extrapolated by a 4-worker run, stated as such), (3) the
  backfill wall-clock estimate and RSS cap for WP1 recomputed from them and written at the top of
  `notes/scoring_pilot.md`; (4) if pool RSS projects above 40 GB or the tracker miss rate on the TC event
  is above 50 % at 24-72 h, the agent **stops and reports to the orchestrator** instead of proceeding.
* **Agent brief:** "Build a throwaway storm-following MSLP tracker pilot (`ewb_live/tools/track_pilot.py`), run it on
  the LXC on Polo (TC) and the longest-lived MLC over all 8 store models + ifs_hres, write measured
  RSS/time/miss/position numbers to `notes/scoring_pilot.md`, respect R1-R5, open a docs PR to develop."

### 8.3 WP1: shared track core + TC track metrics + TC lane

* **Goal:** TC rows `TrackPositionError`, `AlongTrackError`, `CrossTrackError`, `CentralPressureError`,
  `TrackMiss` in `scores.parquet` for all 44 storms x 9 models, visible in the TC tab.
* **Files to add:**
  * `ewb_live/scoring/track_verif.py` (pure numpy, no I/O): `great_circle`, `bearing`,
    `observed_motion` (section 3.4 step 2, first/last fix, stationary cutoff), `along_cross`,
    `follow_track` (storm-following tracker, built on `events/mlc_track.py` primitives, param sets for TC
    and MLC), `match_samples` (init/lead/valid-time matching incl. nearest-earlier-init shift),
    `rows_for_pair` (emits the SCORE_COLUMNS rows and `TrackMiss`).
  * `ewb_live/scoring/tc.py`: IBTrACS adapter from `details.track`, store reader (`open_init`, regional
    window around the track, `msl` only for tracking), `score_tc_event(event, models) -> DataFrame, status`.
  * `ewb_live/scoring/lane_pool.py`: the dedicated worker pool and RSS sampler (R4), reused by WP2/WP4.
  * `ops/lane_backfill.sh`: the R1 guard and launcher (`ops/lane_backfill.sh <lane> [--models ...]`).
    Guard behaviour is state-based (R1, amended): refuse to start when `systemctl is-active
    ewb-live-weekly.service` is `active`/`activating` or the weekly/deploy flock `/opt/ewb-live/.deploy.lock`
    is held (`flock -n`); refuse between Monday 06:30 UTC and Monday 14:00 UTC while the weekly service is
    not yet active, unless `systemctl show ewb-live-weekly.service -p ActiveExitTimestamp` shows a run that
    finished at or after this Monday 07:00 UTC; no other clock window. Then run the lane under `timeout`
    with the hard cap, **poll those same checks every 30 s**, and on a positive poll send **SIGTERM** to the
    worker pool's process group, wait for it to drain, and exit 0: completed part files
    (`event, model, fingerprint, truth`) let the next run resume.
  * `tests/test_track_verif.py`, `tests/test_score_tc.py`, `tests/test_lane_guard.py` (R1 guard logic with a
    fake clock, fake `systemctl` output and a fake lock file).
* **Files to touch (minimal, additive):** `ewb_live/pipeline.py` (`_score_job` dispatch by `event_type`; import
  of `UNSCORED_TYPES`; `SIGNED_ERROR_METRICS`, `HIGHER_IS_BETTER`; the `_UNSCORED_REASON` entry for TC is
  dropped), `ewb_live/events/select.py` (`UNSCORED_TYPES` loses `tropical_cyclone`),
  `ewb_live/scoring/__init__.py` (exports), `dashboard/app.js` (`TYPE_META.tropical_cyclone`: drop `noScores`,
  `FALLBACK_METRICS`, miss-rate/grey rule, `tcNotScoredNote` replaced), `dashboard/detection.py` (About
  text: formulas, sign convention, resolution warning, miss handling).
* **Tests:**
  * Geometry, hand-computed cases: straight-ahead, 90 degrees right/left, backward, dateline crossing,
    high latitude, stationary storm (NaN along/cross, position kept), first/last fix one-sided motion,
    `along^2 + cross^2 = d^2` property test.
  * Tracker: synthetic moving low, low that vanishes (misses from that lead on), two lows (no jump).
  * Matching: 12 h cadence, nearest-earlier-init shift, pre-genesis init excluded (D2).
  * Row contract: columns/dtypes equal SCORE_COLUMNS; `build_scorecard` on synthetic TC rows gives
    skill only for `TrackPositionError`, none for the signed metrics; miss rows counted.
  * Pipeline: TC pairs are no longer "unsupported"; `pair_coverage` stays decidable
    (`tests/test_pair_status.py` and `tests/test_pipeline_unit.py` still pass).
* **Acceptance:** (1) all tests pass; (2) on the LXC, a dev-tree run of 3 storms x 9 models writes the
  rows and the dashboard dev site shows the TC tab with Mean/lead columns, signed-metric styling and
  the miss rate; (3) the measured seconds per (model, init) agree with the WP0 pilot within 10 %; (4) full backfill (44 storms)
  launched only through `ops/lane_backfill.sh` inside R1, <= 40 GB pool RSS, completes with
  `full_run_complete` true for the lane; (5) `scores.parquet` rows for existing lanes are byte-for-byte
  unaffected (row counts per type/metric unchanged); (6) PR base `develop`.
* **Agent brief:** "Implement `ewb_live/scoring/track_verif.py` and the TC lane scorer per
  `notes/scoring_design.md` sections 3 and 6 (formulas in 3.4, D1-D10 are decided), wire the pipeline and
  dashboard, add `ops/lane_backfill.sh` with the R1 weekly-overlap guard, test as listed, backfill via the
  guard, PR to develop."
* **Known limitations of the WP1 TC lane** (independent review of PR #16; documented, not changed):
  * *Fingerprint.* A part file is keyed by `(event, model, fingerprint, truth)` and the fingerprint is
    `start|end|bbox`. A best-track revision inside the same window (changed fixes, same start / end / bbox)
    reuses the old part files until the storm's window changes or `--force`. IBTrACS revisions after the
    first days are rare but not zero.
  * *Search window.* The MSLP read window is the observed track +/- 15 degrees from the init. A forecast low further
    away than that is clipped out of the field and shows up as a miss, not as a large position error, so the largest
    errors are truncated (as EWB's 5 degree mask truncates them, at a larger radius).
  * *ECMWF lead drop.* `ecmwf.open_forecast` drops a lead whose index or object cannot be fetched without an error
    (`_read_lead` returns None). Such leads become "no data" samples of `ifs_hres`, so the baseline's sample count can
    shrink silently (8563 `ifs_hres` samples vs 8504 graphcast in the first backfill); the common-set numbers in
    `notes/scoring_tc_miss_rate.md` are unaffected, the per-cell `n` is.
  * *10-day skill-vs-lead.* The "Skill vs lead" charts show every stored lead out to 240 h; beyond about 120 h few storms
    have a verifying fix, so the curves are sparse (miss rates reach 1.0 on a handful of samples). The table is limited to
    24-120 h (D8).

### 8.4 WP2: MLC lane

* **Goal:** MLC-A rows (same five metrics) for 80 lows x 9 models, plus MLC-B `PeakDepthError` (hPa) and
  `BombHitMiss` (contingency counts via `TruePositives/FalsePositives/FalseNegatives` style rows or a 0/1
  agreement row; choose the one `build_scorecard` and the table render without special cases).
* **Files to add:** `ewb_live/scoring/mlc.py` (ERA5-track adapter from `details.track`, window reader for
  `msl`, MLC parameter set for `follow_track`: 4-5 degrees per 6 h step, 3 hPa closed contour; MLC-B
  peak-depth and 24 h-fall verification), `tests/test_score_mlc.py`.
* **Files to touch:** `ewb_live/pipeline.py` (dispatch + metric sets), `ewb_live/events/select.py`
  (`UNSCORED_TYPES` loses `midlat_cyclone`), `dashboard/app.js` (`TYPE_META.midlat_cyclone`, metric labels),
  `dashboard/detection.py`.
* **Tests:** synthetic deepening low (pressure and bomb verification), truth-edge handling (leads whose
  valid time passes the ARCO end are not scored), wrapped-bbox (antimeridian) track, `truth_source`
  tag `era5_arco` invalidation, plus shared-core tests untouched.
* **Acceptance:** tests pass; backfill of 80 events via the R1 guard in <= 1 h; MLC tab shows the five
  metrics + MLC-B with miss rates; no regression in other lanes' row counts; PR base `develop`.
* **Agent brief:** "Add the MLC lane scorer on top of `track_verif.py` (MLC-A and MLC-B per sections 4 and 6),
  wire pipeline and dashboard, test as listed, run the 80-event backfill through `ops/lane_backfill.sh`, PR to
  develop."

### 8.5 WP3: EWB landfall classes for TC

* **Goal:** `landfall_displacement`, `landfall_time_me`, `landfall_intensity_mae` rows for the storms
  with an ocean->land crossing, computed by EWB's own classes from our forecast tracks.
* **Files to add:** `ewb_live/scoring/landfall.py` (track -> EWB track DataArray layout
  `(lead_time, valid_time)` with `latitude/longitude/valid_time`, observed landfall target from the
  IBTrACS series, call `LandfallMetric(metrics=[...], approach="next", forecast_variable=target_variable=
  "air_pressure_at_mean_sea_level")` through the `forecast_landfall`/`target_landfall` kwargs; fallback
  `calc.find_landfalls` direct path if the layout is incompatible), `tests/test_landfall.py` (synthetic track
  crossing a coastline; needs `third_party/ExtremeWeatherBench` on the LXC dev tree, the test skips
  without it), `notes/landfall_validation.md` (comparison with the TC-B EWB pipeline on 3 landfalling
  storms).
* **Files to touch:** `ewb_live/scoring/tc.py` (call it), `ewb_live/pipeline.py`
  (`SIGNED_ERROR_METRICS += landfall_time_me`), `dashboard/app.js` (labels, units hPa/km/h),
  `dashboard/detection.py`.
* **Tests:** synthetic landfall geometry and timing sign (forecast later = positive), open-ocean storm
  gives no rows (not NaN rows), duplicates (island crossings) handled as EWB does.
* **Acceptance:** on 3 landfalling storms the rows match a TC-B run to within the tolerance written in
  `notes/landfall_validation.md` (displacement within one grid cell, time within the filter window),
  rows appear only for storms with a landfall, existing rows unaffected, PR base `develop`.
* **Agent brief:** "Feed our forecast tracks to EWB's `LandfallMetric` classes per section 3.2, emit the three
  landfall metrics for landfalling storms only, validate against EWB's own TC pipeline on three storms
  and document the comparison, PR to develop."

### 8.6 WP4: flood lane

* **Goal:** `CriticalSuccessIndex`, `FalseAlarmRatio`, `FractionsSkillScore`, `PrecipAreaBias` for 40 events
  x {`graphcast`, `graphcast_gfs`, `ifs_hres`}; the other six models `unsupported: no precipitation variable
  in the MLWP store`.
* **Files to add:** `ewb_live/scoring/flood.py` (PARDIG daily truth reader from `data/pardig/daily`,
  ERA5 thresholds via `climatology.precip_clim.precip_threshold_on`, forecast daily/3-day totals from
  6-h `apcp` bins with fill/negative handling, contingency + FSS (1/3/5-cell neighbourhoods) + areal
  bias over the padded verification domain and `start-2 .. end+2` days), `tests/test_score_flood.py`.
* **Files to touch:** `ewb_live/sources/registry.py` and `ewb_live/sources/aiwp.py` (expose `apcp` as a
  precipitation variable, name chosen and documented in the registry; m -> mm), `ewb_live/sources/ecmwf.py`
  (`tp` mapping for `ifs_hres`: accumulated since init, de-accumulate to 6-h bins), `ewb_live/pipeline.py`
  (dispatch, per-model `unsupported` reason, `HIGHER_IS_BETTER`/`SIGNED_ERROR_METRICS`),
  `ewb_live/events/select.py` (`UNSCORED_TYPES` loses `flood`), `dashboard/app.js` (flood `TYPE_META`,
  labels, the "vs AI-estimated precipitation (PARDIG)" copy), `dashboard/detection.py`.
* **Tests:** exact contingency counts on a hand-built 3x3 truth/forecast pair, FSS limits (perfect = 1,
  disjoint = 0), accumulation across the day boundary, fill values/negatives, IFS-initialised GraphCast
  lead 0/6 h fill (day-1 total not emitted), PARDIG missing day not scored, de-accumulation of `tp`.
* **Acceptance:** tests pass; the flood tab shows three scored models and six documented `unsupported`
  ones with the provisional-truth note; backfill (40 events) via the R1 guard in <= 1 h; other lanes
  unaffected; PR base `develop`.
* **Agent brief:** "Add the flood lane scorer per sections 5 and 6: expose store `apcp` and ECMWF `tp`, verify
  daily exceedance contingency/FSS/areal accumulation against PARDIG on the event's own ERA5-p95 threshold
  for graphcast, graphcast_gfs and ifs_hres, document the six unsupported models, test as listed, backfill
  via the guard, PR to develop."

* **WP4 as built (flood lane; deviations from the plan above, owner brief 2026-11-21).**
  * *Scope:* GraphCast only (`graphcast`, `graphcast_gfs`); the `ifs_hres` ECMWF-`tp` baseline of D6 is **not built**, so flood has no skill
    colour and the dashboard says "GraphCast only - not ranked". The other nine registered models are documented `unsupported` pairs
    ("no precipitation in the MLWP store ... (GraphCast only)"). `sources/registry.py`, `aiwp.py` and `ecmwf.py` are untouched: the scorer reads
    `apcp` straight from `open_init`, like the TC lane reads `msl`.
  * *Metrics (row per event, model, 00Z init, verified day):* `CriticalSuccessIndex`, `ProbabilityOfDetection`, `FalseAlarmRatio`,
    `FractionsSkillScore` (3x3 cells), `FractionsSkillScore5` (5x5), `PrecipAreaBias` (mm, signed, footprint mean forecast - truth),
    `PeakPrecipError` (mm, signed). Contingency/FSS are on the **daily lane** (`max(ERA5 wet-day p95, 20 mm)` per cell and day of year) even
    though all 40 events have the 72 h lane as lane of record; the footprint is the event's own union (daily OR 72 h) exceedance mask.
    A day without a defined denominator emits no row for that metric (FAR needs a forecast exceedance: n is smaller than for CSI).
  * *Data facts the plan had wrong:* `apcp` fill is `9.97e36`, not NaN, and only some IFS-initialised inits have it in the first 6 h (so the
    IFS member has **no 24 h cell** in any event: its first complete day is lead 48 h); ~15-30 % of cells are small negatives (clipped at 0).
  * *No nearest-earlier-init rescue.* A rescued 12Z init emits leads 36, 60, ... which the dashboard bins into the 1 d / 3 d columns; a missing 00Z
    init is `no_data` instead. The upstream GFS-initialised store lacks the 00Z init on ~1/3 of days (21 % of the planned `graphcast_gfs` inits).
  * *Known limitations:* AI-estimated truth that also defines the events (POD/CSI conditional on a truth exceedance); GraphCast was trained on ERA5
    (low-biased rain); two members of one model family; the 24 h column is empty for `graphcast`; a forced rerun of a lane needs the part files
    AND the cached rows/statuses of its pairs dropped to be re-merged (`--force` rewrites parts, but publish-only keeps cached rows for pairs
    already marked done).
  * *Measured (LXC, private tree, 40 events x 2 models, 80 pairs, 1458 jobs, 12 workers recycle 30):* pool 340-355 s, total 10 min 18 s - 10 min
    59 s through `ops/lane_backfill.sh` (serial planning of the 40 events, ~5 s each, is the rest), peak pool RSS 16.1 GB, 1.39 GB per worker,
    1.8-1.9 s per store job; 36,388 flood rows.

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
