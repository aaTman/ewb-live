# Scoring pilot (WP0): measured numbers

Status: WP0 of `notes/scoring_design.md` 8.2, run 2026-11-20 20:53-21:18 UTC on the ewb LXC. Throwaway
tracker `ewb_live/tools/track_pilot.py`, smoke test `tests/test_track_pilot_smoke.py` (11 tests, offline).
Machine-readable numbers: `notes/screenshots/scoring_pilot.json` (JSON only, no PNGs; force-added because
`notes/screenshots/` is excluded in this checkout). Nothing here is deployed; nothing under `/opt/ewb-live`
was written (read-only on `data/icechunk` and `data/results/events_selected.json`; code, cache and output
live in `/opt/ewb-pilot/`). Check: `find /opt/ewb-live -newer <start>` lists nothing under `data/`; `dashboard/` and `DEPLOYED_FROM` did change during the window (another agent's deploy, not this pilot, which never used `ops/remote.sh`).

## 0. Verdict (what WP1 needs from this)

| Gate in 8.2 | Measured | Result |
|---|---|---|
| TC tracker miss rate at 24-72 h > 50 % | **28 %** pooled over 9 models (n = 1647); per model 22 %-34 % | **not tripped**. But see 3.1: it is 80 % for inits issued while the storm is still a < 34 kt disturbance, 7 % from hurricane-stage inits. |
| Projected pool RSS at 12 workers > 40 GB | **40.66 GB** from the 4-worker run of the full 9-model job mix (3.347 GB peak per worker x 12 + 0.5 GB parent), measured pool peak at 4 workers 12.76 GB | **tripped, marginally**. The rule says stop: I did not start WP1. (Runs B, C, D below are further WP0 measurements taken to explain the number, not WP1 work.) |

What the same pilot says about the cause and the cure (so the orchestrator can decide in one step, not a rerun):

* Worker RSS is a slow growth with the number of jobs a worker has run (0.3 GB at start, 3.347 GB after ~110 jobs), not a
  per-job spike. Store-only jobs (8 models, run B) peak at **1.896 GB/worker -> 23.25 GB at 12 workers**. `ifs_hres`-only (run C) peaks at 0.852 GB/worker.
  The mix in one process is what reaches 3.347 GB (eccodes/cfgrib + icechunk + dask threads in one heap).
* Recycling workers every 30 jobs (`--recycle 30`, run D, same 441 jobs): **2.433 GB/worker -> 29.7 GB at 12 workers**, wall 338 s vs 405 s (no cost).
* Also true: at 12 workers each worker runs ~3x fewer jobs than at 4, so the real 12-worker peak is lower than any of these projections; I did **not** run 12 workers (the brief says 4), so that is untested.

**Recommendation: WP1 may proceed only with the pool built as one of (a) a store pool at 12 workers (23.25 GB projected) plus a separate 2-worker
`ifs_hres` pool (~2 GB), or (b) one pool with `max_tasks_per_child` <= 30 (29.7 GB projected). Either clears the D9 40 GB cap; neither is the pool as originally sketched,
so this needs an orchestrator/owner yes before WP1 starts.** WP1 should keep the measured RSS sampler and assert the cap in `lane_pool.py`.

## 1. What was run

* **TC:** `Polo` (`dd9825d6b56e`, EP, 155 kt peak, 892 hPa; the only published storm of that name).
  Track 2026-09-17 12Z .. 2026-09-30 00Z, 101 three-hourly fixes, 25 00/12Z inits with a fix at init and a verifying 6-h fix after it. The track goes ocean -> Baja California land (EWB found 2 observed landfalls, 25.9N 112.2W and 27.9N 110.7W).
* **MLC:** `eb5968513e1c` (lifetime 288 h, bomb = true, not wrapped), 6-hourly 2026-09-28 12Z .. 2026-10-10 12Z, 24 inits.
* **Models:** `graphcast`, `aurora`, `pangu_weather`, `fourcastnet_v2` and their `_gfs` twins from the icechunk store (`open_init` only, never `open_model`), plus `ifs_hres` through the existing ECMWF-direct backend (`sources.ecmwf.open_forecast`, `msl` only).
  441 `(event, model, init)` jobs per full run. Leads: every 6-h lead whose valid time has an observed fix (0 seeds the tracker); mean 25.6 leads read per job.
* **Tracker (prototype of the WP1 core, section 3.2):** start at the observed fix at init; per 6-h step search local MSLP minima (Gaussian sigma 1 cell) within `r_step` of the persistence-advanced previous position (TC 3 deg, MLC 5 deg), take the nearest one whose `centre + delta` isobar closes inside `max_gc` (TC 2 hPa / 5.5 deg, MLC 3 hPa / 6 deg); two consecutive steps without one lose the track (all later leads are misses). Steps whose whole field is fill (GraphCast lead 0 for IFS-initialised runs) are "no data", not misses.
  The window read is the observed track +/- 15 deg (longitude widened by 1/cos lat), one `compute()` per `(model, init)`.
* **Geometry:** exactly section 3.4 / D10 (centred 6 h + 6 h observed motion, one-sided at the ends, 2 km/h stationary cutoff, along > 0 ahead, cross > 0 right).
* **Pool:** 4 spawn workers, 4 dask threads each, `ProcessPoolExecutor`, RSS sampled every 2 s over the process tree (per pid and total, like `pipeline._PeakRss`) under a transient systemd unit with `MemoryMax=30G`, `RuntimeMaxSec=2700`, `Nice=10`.
* **R1 / pilot guard:** before every launch (`/opt/ewb-pilot/launch.sh`) and in every polling cycle (my own and the job's, every 5 s): `systemctl is-active ewb-live-weekly.service` = `inactive` and not Monday 06:00-14:00 UTC. All launches and polls passed (the weekly timer fires Mon 2026-11-23 07:00; the last run ended Fri 21:18), and `free -g` showed >= 247 GB available throughout. Nothing was aborted. Smoke runs, runs A, A-retry, B, C, D, the landfall spike all ran ~1-7 min each, well under `timeout`.

Runs: **A** all 9 models, 4 workers (the headline run; 18 transient S3 `StorageError` jobs were re-run by `--resume`, which retries errors); **B** the 8 store models only; **C** `ifs_hres` only (every second init); **D** = A with `--recycle 30`.

## 2. Time per `(model, init)` and the backfill estimate

| | mean s per job | open_init | read | tracker | leads read | peak RSS / worker | pool peak (4 workers) |
|---|---|---|---|---|---|---|---|
| store models (run B, 392 jobs) | **0.976** (p90 1.2) | 0.242 s | 0.624 s (0.024 s/lead) | 0.04 s | 25.6 | 1.896 GB | 7.05 GB |
| `ifs_hres` alone (run C, 25 jobs) | **20.909** (p90 38.9) | – | 20.75 s (0.804 s/lead) | 0.04 s | 25.8 | 0.852 GB | 3.19 GB |
| all 9 (run A, 441 jobs) | 3.82 (store 0.795, ifs 26.757) | 0.16 s | 3.59 s | 0.03 s | 25.6 | **3.347 GB** | **12.76 GB** |
| all 9 + recycle 30 (run D) | 3.1 | 0.17 s | 2.84 s | 0.03 s | 25.6 | 2.433 GB | 8.7 GB |

The store is far cheaper than the design assumed (design: ~20 s per `(model, init)`, ~1 s per lead; measured **~1 s per job, 0.024 s per lead**: a regional read of 25 leads is one dask `compute()` over 4 threads, the virtual-ref
fetches overlap). The tracker is negligible (0.04 s). `ifs_hres` is the whole cost: ~0.804 s per lead (GRIB range reads + decode), 20-27 s per init, 25x a store job.

**Recomputed WP1 backfill estimate** (replaces the 2.5-3 h of section 3.8 / 8.1; per-job costs above x the design's job counts, assumes 12 workers scale linearly from the 4-worker run, which was not measured):

| lane | jobs | leads/job | store worker-s | `ifs_hres` worker-s | wall at 12 workers (store only / + ifs) |
|---|---|---|---|---|---|
| TC (44 storms) | 5392 store + 674 ifs | 20.8 | 4158 | 11271 | 5.8 min / **21.4 min** |
| MLC (80 lows) | 4800 store + 600 ifs | 10.2 | 2478 | 4908 | 3.4 min / **10.3 min** |

So the "2.5-3 h" D9 window shrinks to roughly **25 min for the TC backfill, ~10 min for MLC, plus the weekly increment**; the binding limits become the 40 GB RSS cap and S3 politeness (see 5), not the clock. R1 still applies.

## 3. Tracker results, TC (Polo)

All numbers pool the 9 models and every init (n = samples with an observed fix; 33 no-data samples excluded: GraphCast IFS lead 0/6 fill, and leads beyond 240 h for the 12-h-older fallback init).
Position error is great-circle km to the best-track fix; CP error is forecast MSLP at the tracked low minus best-track pressure (hPa, positive = forecast too weak).

| lead (h) | n | miss rate | median pos. error (km) | p90 (km) | median along (km) | median cross (km) | median CP error (hPa) |
|---|---|---|---|---|---|---|---|
| 6 | 213 | 21 % | 33.5 | 87.4 | -18.4 | -8.6 | 33.4 |
| 12 | 213 | 25 % | 43.3 | 103.2 | -12.2 | -7.9 | 38.5 |
| 24 | 203 | 24 % | 57.5 | 167.2 | -12.2 | -15.4 | 42.8 |
| 36 | 194 | 25 % | 71.5 | 207.2 | -0.7 | -12.9 | 46.1 |
| 48 | 185 | 30 % | 91.8 | 239.0 | -5.2 | -20.0 | 50.2 |
| 72 | 167 | 35 % | 130.2 | 321.1 | 0.6 | -41.5 | 52.6 |
| 96 | 153 | 40 % | 187.0 | 489.8 | -49.2 | -32.5 | 40.6 |
| 120 | 135 | 47 % | 260.1 | 550.5 | -166.4 | -14.6 | 39.0 |
| 168 | 99 | 58 % | 637.2 | 1428.4 | -600.8 | 65.0 | 19.1 |

Per model (the cells D5 would grey above 50 %):

| model | miss 24 h | miss 48 h | miss 72 h | miss 24-72 h | median err 24 h (km) | 48 h | 72 h |
|---|---|---|---|---|---|---|---|
| aurora | 21 % | 27 % | 25 % | 24 % | 48 | 51 | 93 |
| aurora_gfs | 29 % | 32 % | 35 % | 30 % | 54 | 124 | 220 |
| fourcastnet_v2 | 21 % | 36 % | 40 % | 31 % | 93 | 100 | 128 |
| fourcastnet_v2_gfs | 24 % | 32 % | 65 % | 34 % | 229 | 544 | 126 |
| graphcast | 25 % | 27 % | 30 % | 27 % | 41 | 76 | 155 |
| graphcast_gfs | 29 % | 32 % | 35 % | 31 % | 54 | 58 | 98 |
| ifs_hres | 21 % | 27 % | 25 % | 24 % | 43 | 66 | 105 |
| pangu_weather | 22 % | 24 % | 26 % | 22 % | 68 | 126 | 163 |
| pangu_weather_gfs | 29 % | 32 % | 35 % | 31 % | 57 | 101 | 148 |

The headline numbers by lead (TC, pooled): miss rate 24 % / 30 % / 35 % at 24/48/72 h; median position error 57.5 / 91.8 / 130.2 km.

### 3.1 Where the TC misses are

Misses are concentrated in inits issued while Polo was still a weak disturbance (the best track starts 2026-09-17 12Z at 20 kt / 1010 hPa; the published event itself starts 09-21): the models carry no closed 2 hPa low there, so a storm-following tracker has nothing to follow.

| observed wind at init | inits | samples (24-72 h) | miss rate 24-72 h | median position error 24-72 h (km) |
|---|---|---|---|---|
| 34-63 kt (TS/Cat 1) | 2 | 153 | 0 % | 109 |
| <34 kt (pre-TS) | 7 | 495 | 80 % | 176 |
| >=64 kt (hurricane) | 16 | 999 | 7 % | 76 |

Reading: for the lane this is a **D2 question** ("storm exists at init" = first IBTrACS fix) rather than a tracker bug; a storm that is a 1010 hPa wave in the best track is not yet a closed low in any 0.25 degree model.
Options for WP1 (owner/orchestrator): keep D2 and let the miss rate show it (the 50 % grey rule will grey early-stage cells), or start verification at the first fix with wind >= 34 kt (the event's own `ts_fixes` rule). I would take the second, but that is a changed decision, so it is left open.
Beyond ~96 h the pooled miss rate climbs (40 % at 96 h, 47 % at 120 h) as storms decay and the observed track ends (the 240 h cell has n = 50 samples from the first inits only; ignore it).

## 4. Tracker results, MLC (longest-lived non-wrapped low)

| lead (h) | n | miss rate | median pos. error (km) | p90 (km) | median along (km) | median cross (km) | median CP error (hPa) |
|---|---|---|---|---|---|---|---|
| 6 | 208 | 0 % | 32.0 | 93.2 | 0.0 | -5.4 | 0.1 |
| 12 | 208 | 6 % | 33.1 | 161.1 | -7.3 | -2.2 | 0.2 |
| 24 | 199 | 18 % | 43.0 | 149.1 | -5.0 | -8.4 | 0.7 |
| 36 | 190 | 18 % | 56.6 | 181.1 | -10.0 | -16.2 | 1.1 |
| 48 | 181 | 19 % | 67.6 | 217.0 | -10.0 | -9.4 | 1.6 |
| 72 | 171 | 32 % | 169.1 | 317.7 | -0.7 | -4.8 | 3.2 |
| 96 | 153 | 45 % | 274.2 | 494.2 | 28.3 | -5.5 | 5.4 |
| 120 | 135 | 70 % | 308.9 | 539.8 | 62.1 | 17.6 | 6.7 |
| 168 | 99 | 88 % | 243.7 | 654.8 | 48.4 | -10.1 | 8.7 |

Per model:

| model | miss 24 h | miss 48 h | miss 72 h | miss 24-72 h | median err 24 h (km) | 48 h | 72 h |
|---|---|---|---|---|---|---|---|
| aurora | 13 % | 10 % | 16 % | 13 % | 38 | 42 | 94 |
| aurora_gfs | 19 % | 26 % | 47 % | 30 % | 73 | 57 | 152 |
| fourcastnet_v2 | 17 % | 29 % | 47 % | 27 % | 46 | 115 | 229 |
| fourcastnet_v2_gfs | 24 % | 21 % | 32 % | 26 % | 46 | 114 | 251 |
| graphcast | 26 % | 24 % | 37 % | 28 % | 33 | 62 | 66 |
| graphcast_gfs | 19 % | 21 % | 42 % | 26 % | 46 | 57 | 172 |
| ifs_hres | 4 % | 10 % | 16 % | 12 % | 30 | 56 | 169 |
| pangu_weather | 17 % | 14 % | 21 % | 18 % | 39 | 97 | 142 |
| pangu_weather_gfs | 24 % | 16 % | 32 % | 21 % | 62 | 108 | 145 |

MLC position errors are small to 48 h (43.0 / 67.6 km) and the central-pressure error is a real number (ERA5 and the model share the 0.25 degree grid: 1.6 hPa at 48 h), unlike TC.
The miss rate passes 50 % beyond ~100 h; the MLC lane's own gate (D5, grey above 50 %) would grey 120 h+. Median along/cross are within about 10 km to 72 h; nothing to read into the signs of such small medians.

## 5. Sign convention check (along/cross)

* Hand cases through the pilot's `along_cross` (observed at 0N 0E, heading 0, 30 km/h): ahead (+111.2, 0.0), behind (-111.2, 0.0), right (0.0, +111.2), left (0.0, -111.2) km; `along^2 + cross^2 = d^2` and the stationary / dateline cases are unit-tested (`tests/test_track_pilot_smoke.py`).
* Synthetic recurver (clockwise turning track and its mirror image) through `persistence_sanity`: a straight-line forecast lands on the outside of the turn in >= 90 % of 48 h and 96 h samples for both senses (tested).
* **Polo does not recurve** (observed heading change first vs last third of the track: 6.0 degrees clockwise), so the brief's "storm known to recurve" check was done on the **15 published TCs whose observed track turns by >= 60 degrees** instead: a clockwise turn must put straight-line persistence left of the observed track (cross < 0), an anticlockwise one right.
  Pooled agreement **0.62** (per storm 0.33-1.0, `scoring_pilot.json: sanity.published_tc_recurvers`). That is only mildly better than chance, because "turn" is measured crudely (mean heading of the first vs last third of the track) and persistence from an early init mostly differs by speed, not by turn; I do not count it as evidence either way.
  The sign is settled by the hand/synthetic cases, not by this.
* Models' real along/cross are small in the median at 24-72 h (TC along -12.2 / -5.2 / 0.6 km; cross -15.4 / -20.0 / -41.5 km), with a leftward cross-track drift beyond 48 h and a slow (along < 0) bias beyond 96 h; one storm, so no more than a description.

## 6. EWB landfall classes fed the pilot tracks (TC) — works

Spike: `track_pilot landfall --model graphcast` (18 GraphCast inits with >= 4 tracked leads, forecast MSLP at the tracked low = observed best-track pressure + the pilot's `fc - obs` error, Pa):

* Layout that EWB accepts: a `(lead_time, valid_time)` DataArray with `latitude`/`longitude` as `(lead_time, valid_time)` coordinates; `valid_time = init + lead` makes the mapping from `init_time` lossless (an `(init_time, lead_time)` layout is not what `find_landfalls` wants).
* `calc.find_landfalls` on the observed track: **2 landfalls** (25.87N 112.2W 2026-09-29 06:25Z, 27.90N 110.7W). On the forecast tracks: 12 landfall rows over 14 inits.
* `LandfallMetric(metrics=[Displacement, TimeMeanError, IntensityMAE], approach="next", ...).maybe_compute_landfalls(...)` then each metric's `compute_metric(..., forecast_landfall=..., target_landfall=...)` all returned values for **7 paired inits**:
  displacement 74-310 km, time error +1.3 h to -18.4 h (later inits earlier), intensity MAE 8-35 hPa (EWB reports Pa: 802-3456). So the documented `forecast_landfall` / `target_landfall` kwargs path works; WP3 does not need the 30-line fallback detector.
* Costs/gotchas: first call downloads Natural Earth land/ocean/lakes (network; point `CARTOPY_DATA_DIR` at a scratch dir), default 50 m geometry (not 10 m as section 2.1 says); `landfall_time_filter` default drops pairs whose timing error exceeds +/-24 h, so far-lead inits silently produce no pair (consistent with only 7 of 18 inits pairing); the pilot's intensity is a placeholder-quality number (fc MSLP = obs + error).

## 7. Findings WP1 must absorb

1. **Store gaps are real**: 20 jobs (4 `_gfs` models x inits 2026-09-20 00Z/12Z, 09-26 12Z, 10-08 00Z/12Z) have no init within 12 h. A further 58 of 441 jobs only worked through the nearest-earlier-init rule (lead shift 12 h): 12 per `_gfs` model (their 00Z cadence is sparse) and 1-5 for the IFS-initialised models. Report the first kind as `no_data` pair statuses, never as misses.
2. **12-h-older inits lose their 240 h lead**: the rescue init supplies valid times only to 228 h; those leads are `no data`, not misses. The pilot keeps `lead` = nominal lead (valid time - nominal init) and records `offset_h`; WP1's `match_samples` must store the true lead.
3. **Transient S3 errors**: the first pass of run A had 18 `StorageError: error fetching virtual reference ... InternalError` jobs (4 %, 4 workers; they were re-run by `--resume` and all succeeded). After a 3-try/2 s-backoff retry was added, run B needed 4 retries and run D 2, with 0 failed jobs. WP1 needs the read retry and should expect more of them at 12 workers.
4. **GraphCast (IFS-initialised) lead 0 is fill** for `msl` too, not only `apcp`; lead 6 is fine. The tracker seeds from the observed fix when lead 0 is empty.
5. **Central-pressure error is a resolution number for strong TCs**: median +42.8 hPa at 24 h (all inits and models pooled; Polo is an 892 hPa storm and 16 of 25 inits are hurricane-stage), much larger than the +0.7 hPa of the three-storm probe in 3.6 (milder storms). Keep the signed-bias-with-warning framing (D3); do not rank models on it.
6. **Use `open_init`**: confirmed cheap (0.18 s), no 8 GB `open_model` cost. `ulimit -n` was 1024 on the box and never hit here at 4 workers; set it higher for 12.
7. **dask threads per worker matter**: all numbers are 4 threads/worker (16 cores = 4 x 4). At 12 workers, use 1-2 threads/worker or the store read concurrency triples.
8. **Tracker design held**: persistence-advanced nearest closed low did not jump onto neighbouring lows in the synthetic two-low test; real-storm jumps were not audited by eye beyond the position-error distribution (p90 239.0 km at 48 h). A WP1 audit of the worst 20 samples is still advisable.
9. The pilot only covers Polo and one MLC. Everything above is one storm each; per-lead numbers have n ~ 150-210 per cell but only 25 / 24 distinct inits, so cell uncertainty is much larger than n suggests.

## 8. Reproduce

```
rsync -az ewb_live/ root@192.168.0.121:/opt/ewb-pilot/code/ewb_live/        # scratch copy; ops/remote.sh sync would write /opt/ewb-live, so it was not used
/opt/ewb-pilot/launch.sh A --out /opt/ewb-pilot/out --workers 4 --dask-threads 4 --wall-cap-s 2400   # pilot guard + systemd-run (MemoryMax 30G, RuntimeMaxSec 2700)
# env: PYTHONPATH=/opt/ewb-pilot/code EWB_MLWP_REPO=/opt/ewb-live/data/icechunk/cira_mlwp EWB_LIVE_CACHE=/opt/ewb-pilot/cache; python = /opt/ewb-live/.venv (read-only use)
python -m ewb_live.tools.track_pilot {run,report,sanity,landfall,assemble,guard} ...
```
Raw per-sample records (1 MB) stay on the box: `/opt/ewb-pilot/out/jobs.jsonl` (+ `out_B/C/D`). The committed JSON carries the aggregates, the per-job timing table and the run configuration.
