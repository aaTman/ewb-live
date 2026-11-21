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
