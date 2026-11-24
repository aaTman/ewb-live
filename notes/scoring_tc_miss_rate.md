# TC miss rate: is the `ifs_hres` advantage real? (PR #16 follow-up, 2026-11-21)

Owner review of the preview: every model has a higher `TrackMiss` rate than the `ifs_hres` baseline. Checked on the
existing backfill part files (read-only, 44 storms, 9 models) plus one tracker-sensitivity run (`ewb_live/tools/track_sensitivity.py`,
15 storms, 1827 `(model, init)` reads, 417 s, run through `ops/lane_backfill.sh`). Verdict: **real, not an artefact of matching or
tracking**: the AI models draw shallower lows, which fail the closed-contour test more often.

## 1. Same set of samples (matching asymmetry?)

Common set = `(storm, valid time, true lead)` where every model has a sample (5775 samples, leads 6-240 h pooled; per lead below).
The init set is the same by construction (00/12Z inits with a fix at init, from the first >= 34 kt fix); the only per-model
differences are store gaps (36 inits per `_gfs` model) and the 12 h-earlier rescue init, both removed by the common set.

| miss rate, common set (n) | 24 h (310) | 48 h (253) | 72 h (199) | 96 h (165) | 120 h (125) | all leads (5775) |
|---|---|---|---|---|---|---|
| ifs_hres | 5.5 % | 5.5 % | 5.5 % | 10.9 % | 10.4 % | 6.8 % |
| pangu_weather | 10.3 % | 11.9 % | 12.6 % | 14.5 % | 14.4 % | 11.3 % |
| graphcast | 10.3 % | 14.2 % | 15.1 % | 17.6 % | 21.6 % | 13.6 % |
| fourcastnet_v2 | 12.3 % | 19.4 % | 20.1 % | 21.2 % | 24.0 % | 21.7 % |
| aurora | 9.0 % | 12.6 % | 15.6 % | 21.2 % | 29.6 % | 26.8 % |
| `_gfs` twins (graphcast / pangu / aurora / fcn) | 6.1-8.4 % | 10.7-19.0 % | 12.1-29.6 % | 12.1-32.1 % | 14.4-32.8 % | 11.2-26.5 % |

The gap does not come from different samples: on the 5 IFS-initialised models alone (8023 samples) it is ifs 7.2 % vs
graphcast 13.0 %, pangu 10.9 %, fcn 21.5 %, aurora 25.7 %. Where `ifs_hres` tracked a sample, the store model missed it in 4.9 %
(pangu) to 19 % (aurora) of cases; where `ifs_hres` missed and the store model tracked: 0.6-1.2 %. All models miss the same
~6 % (both-missed 6.4 %): storms at weak or decaying stages nobody resolves.

## 2. Survivorship in the track error?

Track error on the common set where ALL models are non-missed (3521 samples, mean km): graphcast 78.4, pangu 94.9, aurora 88.5,
ifs_hres 103.3, fcn 111.5 (`_gfs` twins 127-165). The ordering of the headline (own non-missed samples: ifs 52 km at 24 h,
graphcast 43 km) is the same on the common set (24 h: graphcast 40.0, ifs 49.2): dropping each model's misses does not
flatter the MLWP models. The banner still only ranks track error, so it now says so and quotes the miss rates.

## 3. Why: the lows are shallower, and misses sit on weak storms

* Median forecast-minus-observed central pressure on the common tracked set: ifs_hres **+1.8 hPa**; graphcast +9.8; aurora +13.8;
  pangu +14.1; fcn +17.1 (`_gfs` twins +9.9 to +16.0). The AI models carry weaker, smoother cores. A 2 hPa closed contour (EWB's own
  number, same for every model, same 0.25 degree grid, same smoothing) fails on them first.
* Miss rate (24-72 h) by observed wind at the valid time: < 34 kt: ifs 28 % vs MLWP 40-56 %; 34-49 kt: ifs 7 % vs 13-24 %;
  50-63 kt: 3 % vs 4-11 %; 64-95 kt: 0.3 % vs 3-8 %; >= 96 kt: 0 % vs 0.5-9 %. The gap is concentrated in weak storms.

## 4. Tracker sensitivity (same fields, only the tracker changes)

15 storms, leads 24-120 h, all 9 models. Miss rate, `ifs_hres` vs the mean of the 8 MLWP models:

| tracker | ifs_hres | mean MLWP | gap | ratio |
|---|---|---|---|---|
| 2 hPa closed contour, lost after 2 (shipped) | 8.2 % | 11.6 % | +3.4 pt | 1.41 |
| 2 hPa, lost after 3 | 8.2 % | 11.5 % | +3.3 pt | 1.41 |
| 1 hPa closed contour | 3.6 % | 5.5 % | +1.9 pt | 1.53 |
| 0.5 hPa closed contour | 1.4 % | 2.9 % | +1.5 pt | 2.04 |

A looser contour closes most of the absolute gap (the misses are shallow-low failures) but `ifs_hres` stays ahead at every setting,
so the ranking is not a tuning artefact. Patience (lost after 3) changes nothing: the misses are steps with no closed low at all, not
flicker. For storms >= 50 kt the misses are 1-3 % for every model.

## 5. Decision

No tracker or matching change: nothing asymmetric was found, and the parameters are EWB's. The difference is a finding about the
models (shallower lows at 0.25 degrees), reported as such. The dashboard banner now reads "ranks track error only" and shows the
leader's and the baseline's mean miss rate beside the track-error skill.
