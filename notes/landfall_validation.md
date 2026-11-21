# WP3: EWB landfall classes on our TC tracks - validation and findings

Run 2026-11-21 on the ewb LXC, private tree `/opt/ewb-preview-lf` (not `/opt/ewb-live`, not `-dev`), through
`ops/lane_backfill.sh tc` (R1 guard clean). Tool: `ewb_live/tools/landfall_validate.py`; code: `ewb_live/scoring/landfall.py`.

## 1. How our tracks reach EWB's classes

* Forecast: the followed track of every verified `(model, init)` (`tc.score_init` -> `rec["fc_track"]`, true init / true leads)
  becomes EWB's `(lead_time, valid_time)` layout (2-D `latitude` / `longitude` coords, `valid_time = init + lead`, MSLP in Pa).
* Target: the storm's own IBTrACS series from `event.details["track"]` (`valid_time` dim, pressure in Pa), keyed by `details.sid`.
  **No name filter is used.** EWB's `inputs.IBTrACS` filters by `season` + `tc_name in extract_tc_names(case.title)`, our TC titles
  are "Tropical Cyclone 2026-08-21 to 2026-08-28" (no name) and two pairs of published storms share a name in one season
  (`SAUDEL` 9227f3e04f69 / 2d3a927184d6, `UNNAMED` 786a036ca8d0 / d209cb72530b), so the name path would return the wrong or both storms
  (checked: EWB's name path for Saudel returns the landfalls of BOTH Saudel storms, 08-27 and 09-02). The sid-keyed target cannot.
* Call: `LandfallMetric([LandfallDisplacement, LandfallTimeMeanError, LandfallIntensityMeanAbsoluteError], approach="next",
  forecast_variable = target_variable = air_pressure_at_mean_sea_level)`: `maybe_compute_landfalls` (EWB's detection, next-landfall
  matching, default +/-24 h time window) once, then every class's `compute_metric(..., forecast_landfall=, target_landfall=)`.

## 2. Findings that shaped the design

1. **EWB de-duplicates forecast landfalls across inits.** `calc.find_landfalls` ends in `_deduplicate_landfalls(min_distance_km=50)`
   over the flattened `(init_time, lead_time)` landfalls: when successive inits forecast landfall at the same coast, only the first
   init's landfall survives and the others pair with nothing. Feeding all inits at once (what EWB's pipeline does) therefore turns most
   inits into misses. We call the classes once per init (`landfall_rows(..., pooled=False)`); `pooled=True` reproduces EWB's behaviour.
   Measured with real graphcast tracks (paired inits / inits with an observed landfall ahead):

   | storm | per init (ours) | pooled (EWB pipeline style) |
   |---|---|---|
   | Polo dd9825d6b56e | 10 / 18 | 6 / 18 |
   | Isaias 27222b8db889 | 5 / 6 | 2 / 6 |
   | Nolo 1a992ebc5446 | 14 / 20 | 10 / 20 |
   | Saudel 9227f3e04f69 | 3 / 14 | 2 / 14 |
   | Edouard 41ca1a5cf33c | 0 / 2 | 0 / 2 |

2. **Target equivalence.** Observed landfalls from our `details.track` target and from EWB's own `inputs.IBTrACS` target (its name + season
   filter on the NCEI `last3years` csv, then `find_landfalls`) are identical in time and position for Polo (2 landfalls), Edouard, Isaias,
   Nolo and Olana (all `dt = 0.0 h`). This includes the two storms that cross the antimeridian (Nolo, Olana): EWB's detector is not
   fooled by the 179 -> -179 wrap, so no special handling is needed. (Not run: the full TC-B pipeline, EWB's thickness-based tracker
   on global 3-D fields; our forecast tracks are our own tracker's, so only the target and the matching/metric classes are common.)
3. `details.track` is the published window (`window_truncated`), a landfall outside it is not seen; EWB's csv target drops fixes without
   wind AND pressure, ours keeps them (pressure NaN -> no intensity row for that init).

## 3. Rows (preview backfill, 44 storms x 9 models)

Storms with an observed landfall (EWB's detector on the best track): **14 of 44** (20 landfalls; the "about 24 of 44" coarse proxy over-counted).
13 storms have landfall rows (Olana's landfall precedes every verifiable >= 34 kt init, so it has none). Rows: `landfall_displacement` 381,
`landfall_time_me` 381, `landfall_intensity_mae` 381, `landfall_miss` 996 (2139 rows). Miss rate over 9 models: aurora 0.66, aurora_gfs 0.76,
fourcastnet_v2 0.85, fourcastnet_v2_gfs 0.85, graphcast 0.60, graphcast_gfs 0.56, ifs_hres 0.48, pangu_weather 0.66, pangu_weather_gfs 0.67
(the pairing needs a forecast landfall within +/-24 h of the observed one, so far leads are mostly misses: ~0.4-0.7 below 72 h, 0.8-1.0 beyond 96 h).

## 4. Storm classes EWB cannot score (no rows, never NaN rows)

* **No observed landfall** (30 of 44: open-ocean / recurving / never reaching land): nothing to match.
* **Landfall before the first verifiable init** (Olana 5e5ce42a239e): the storm only reaches >= 34 kt after / at its landfall, so no
  verified init has a landfall ahead; no rows (the amended D2 rule, scoring starts at the first >= 34 kt fix).
* **Landfall beyond 240 h** from every init: not eligible.
* **Observed landfall outside the published window** (`window_truncated`) or **no sid** in the details: cannot be keyed / seen.
* Storms with a landfall but no paired forecast landfall at all (Edouard, Dolly, Saudel 2d3a927184d6: 0 pairs) carry `landfall_miss = 1` rows only.
* Pressure at landfall missing in the best track: that init gets displacement and timing rows but no intensity row.
