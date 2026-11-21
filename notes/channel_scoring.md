# Channel — scoring (TC / MLC / flood lanes)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`. R5 of `notes/scoring_design.md`.

## [wp1] 2026-11-21 01:25 UTC — WP1 progress: code + offline tests done, dev run next

Branch `feat/scoring-tc-track-core` off `origin/develop` (e09ebe3). Built: `ewb_live/scoring/track_verif.py` (geometry per
section 3.4 / D10, tracker, rows incl. `TrackMiss`), `scoring/tc.py` (IBTrACS adapter, readers with 4-try backoff retry, store
gaps = `no_data` not misses), `scoring/lane_pool.py` (ONE pool, `max_tasks_per_child=30`, RSS sampler + 40 GB cap),
`ops/lane_backfill.sh` (state-based R1 guard, 30 s poll, SIGTERM + resumable), pipeline wiring (TC out of
`UNSCORED_TYPES`, `truth_source = ibtracs`, `_score_job` dispatch, scores.json compaction), dashboard cell + About copy.
Owner decisions of 2026-11-21 (single recycled pool; TC verified from the first >= 34 kt fix) recorded in section 7 of the
note. Offline: `test_track_verif` 28, `test_score_tc` 21, `test_lane_guard` 26, pilot smoke 11 pass. Next: unit suite + 3-storm
dev run + full backfill in a private preview tree (`/opt/ewb-preview-tc`, port 8062; not /opt/ewb-live, not /opt/ewb-live-dev).

## [wp1] 2026-11-21 02:20 UTC — WP1 DONE: TC lane scored (44 storms x 9 models), PR to develop

**What changed.** TC left `UNSCORED_TYPES` (`truth_source = ibtracs`); per `(event, model, init, lead)` rows
`TrackPositionError`, `AlongTrackError` (>0 = ahead), `CrossTrackError` (>0 = right), `CentralPressureError`, `TrackMiss` in
`scores.parquet` (scores.json carries the per-init rows collapsed to means). New: `scoring/track_verif.py`, `scoring/tc.py`,
`scoring/lane_pool.py`, `ops/lane_backfill.sh`, `dashboard/track_scoring.py` (About listing, tested against the scorer),
`tests/test_track_verif.py`, `test_score_tc.py`, `test_lane_guard.py`. Touched: `pipeline.py` (lane block, `_score_job` dispatch,
`SIGNED_ERROR_METRICS`, scores.json compaction, `meta.track_verification`, sidecar fold-in keeps a proven `fp`),
`events/select.py`, `scoring/truth.py`, `scoring/__init__.py`, `dashboard/app.js|index.html|styles.css`, `tools/track_pilot.py`
(imports the absorbed geometry/tracker). Owner decisions of 2026-11-21 recorded in section 7 of the note: ONE pool recycled
every 30 jobs (`ifs_hres` in the same pool); TC verified from the first observed fix >= 34 kt (earlier inits "not verified").

**Measured (LXC, private preview tree `/opt/ewb-preview-tc`, port 8062; not /opt/ewb-live, not /opt/ewb-live-dev).**
Full TC backfill launched through `ops/lane_backfill.sh tc` inside R1 (weekly inactive, Saturday): 369 pairs, 4311 jobs,
**wall 1557 s (26 min)**, 12 workers, recycle 30, **peak pool RSS 18.1 GB** (peak worker 1.80 GB, 149 worker processes over the
run; the pilot projected 29.7 GB), 5 transient S3 retries, 0 failed jobs. Per `(model, init)` job: store 1.116 s (pilot 0.976 s,
+14 %), `ifs_hres` 22.5 s (pilot 20.9 s, +8 %); tracker 0.023 s. The 3-storm dev run (27 pairs, 351 jobs, 203 s) had store 0.917 s
and `ifs_hres` 39.7 s (all 12 workers on ECMWF at once). Store gaps: 36 of 518 verified inits per `_gfs` model have no init in
the store (even via the 12 h earlier one): reported `no_data` in the pair status, no rows, never misses. R1 exercised for real:
guard flipped to "activating" mid-run -> SIGTERM -> the pool drained (stats `stopped: true`, exit 0), rerun resumed from the
existing part files (15 of 16 pairs scored).
Rows: **TC scored rows 291,778** over 44 events x 9 models (396 pairs `scored`, 88 `unsupported`: `aifs_single`, `aigfs`) =
TrackPositionError 56,124; AlongTrackError 55,908; CrossTrackError 55,908; CentralPressureError 56,033; TrackMiss 67,805.
Existing lanes' rows per (type, metric) identical before/after. `meta.full_run_complete` true (2255 pairs: 812 scored + 1443
documented). 518 00/12Z inits verified, 156 earlier ones not verified. Headline (24 h / 120 h): `ifs_hres` 52 / 265 km,
`graphcast` 43 / 230 km; miss rate 5-31 % (no cell above the 50 % grey rule). scores.parquet 2.0 MB, scores.json 13 MB raw /
0.78 MB gzip.

**Tests.** Offline: `test_track_verif` 28, `test_score_tc` 23, `test_lane_guard` 26, pilot smoke 11. Box (preview tree, not the
live tree): unit suite 675 passed, 2 failed (`test_cold_snap::test_cold_snap_severity_and_merge_envelope_follow_freeze`,
`test_imerg::test_s3_credentials_parses_and_caches`, the two known box failures; 28 network-marked deselected;
`test_fcn_humidity.py` excluded: its import opens the CIRA icechunk from S3 at collection and hung in this run); e2e
(`tests/e2e -m e2e`, against the preview server) **199 passed, 1 skipped, 0 failed**, incl. new TC-lane e2e at 1440 and 390 px.
Browser check of the real data at 1440 and 390 px: no JS errors, no horizontal page overflow (screenshots kept outside the repo).

**Open.** (1) Not deployed, not merged. The weekly run will score the TC lane in its own pool after the EWB jobs (~26 min extra);
the first weekly after merge rescores nothing already in part files. (2) The pilot's "store +/-10 %" check: store job +14 % at 12
workers, `ifs_hres` +8 % (and 39.7 s with 12 concurrent ECMWF readers in the small run): the ECMWF backend is the bottleneck, consider
`EWB_LIVE_LEAD_PARALLEL` if S3 politeness matters. (3) WP2 (MLC) and WP3 (landfall classes) are untouched; `Lane` in `lane_pool.py`
is the registration point. (4) Pre-existing: sidecars of EWB jobs carry no `fp`, which used to turn every documented
zero-row pair into a blocker after a publish-only run; fixed here (same-status fp is carried), flagged for review.
