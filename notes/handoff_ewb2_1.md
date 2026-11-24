# Handoff ewb2 → successor 3 ("ewb")
SUCCESSOR HANDLE: term_0bafb4ff-2f94-4b13-b4e2-63cd5ef32a08
(launched 2026-11-06 ~11:57Z via `orca agent launch`; group
headless-terminals:2f4b5ca9-ffba-4e51-8922-99ccfebbbb6d)

## Goal
Same as before: finish the ewb2 task list (see latest user task text in
notes/ewb.md '## ewb2 log' preamble + SPEC.md §5 binding). Remaining from
the original 5 tasks: (3) full 90-day scoring run with ALL models +
sanity-check RMSE, (4) weekly.py end-to-end + incremental re-run proof,
(5) pytest green + real '## DONE' section in notes/ewb.md. Do NOT git
commit. Append to '## ewb2 log' in notes/ewb.md.

## State: DONE (this session, all tested)
1. Truth mirror WORKS, built & verified:
   - ewb_live/scoring/truth_mirror.py; store data/cache/truth/era5.zarr
     (41 GB, zarr v2, time span 2026-07-25T00 .. 2026-10-30T18, 392
     6-hourly points = full window back from ARCO cutoff), manifest
     era5_mirror_manifest.json. Bit-exact spot check vs ARCO
     (2mT/u500/q700 diff 0.0). Incremental append confirmed.
   - truth.py make_targets serves mirror as ERA5 target when window
     covered; IFS-analysis fallback for post-cutoff days unchanged.
   - ARCO cutoff 2026-10-30T23:00 at build time; extend with
     `python -m ewb_live.scoring.truth_mirror --end <date> --days 90 --lookback 14 -t 24`
     (resumable; ARCO cutoff moves ~daily).
2. EWB mid-cache zarr-v2 crash FIXED: evaluate.py monkey-patch shim
   _cache_and_compute_zarr3 (writes zarr v3, strips encodings +
   _FillValue). Without it ALL derived-target scoring = 0 rows.
3. Performance layers:
   - scoring/inits_cache.py: persistent day-coverage memo for
     sources.available_inits (data/cache/aiwp_inits.json). GFS model
     build_forecast 33.5s -> 0.8s warm.
   - sources/ecmwf.py: lead loop parallelized via ThreadPoolExecutor
     (env EWB_LIVE_LEAD_PARALLEL, default 8): 8-lead read 163s -> 22s.
   - scoring/fc_cache.py (NEW, wired into build_forecast for
     ifs_hres/aifs_single only): disk cache of decoded ecmwf forecasts at
     data/cache/fc_parts keyed (model, init, vars) with FULL 0-240 leads
     fetched once and 10-degree-padded region box, re-cut to exact bbox +
     requested leads on read. NOT YET VALIDATED (cold test still running,
     see Running).
   - truth.py _cropped_target: mirror-backed targets hard-cropped to case
     bbox (+2° margin) inside subset_data_to_case BEFORE EWB derive. This
     is the fix for the 12-20 GB/worker memory hog (EWB derived fields
     computed on global grid because mask keeps global grid, drop=False).
     Values inside region identical; NOT YET MEASURED on real AR job.
4. Job isolation REWRITTEN in pipeline.py scoring loop: each event job
   now runs as its OWN SUBPROCESS (`python -m ewb_live.pipeline
   --_run-job <jobfile>`), ThreadPoolExecutor(jobs) just supervises.
   Failures recorded per job (meta.perf.failed_jobs), ONE serial retry
   after pool ends. OOM can no longer poison other jobs. _job_main added
   in pipeline.py. 39/39 unit tests pass after all edits.

## Running NOW (do not kill blindly)
- FULL PIPELINE RUN IN FLIGHT #2: the first attempt (11:50Z) was killed
  when the launching agent session's process group was torn down at
  handoff; workers died before any job completed (log stops at 'to
  score', no rows lost, run is incremental). RESTARTED 2026-11-06
  12:26 UTC detached via setsid+nohup (own process group, survives
  session teardown). From repo root:
  `EWB_LIVE_INIT_PARALLEL=2 setsid nohup uv run python -m
  ewb_live.pipeline --days 90 --jobs 3 >> data/results/pipeline_full_run.log 2>&1`
  Verified 3 isolated `--_run-job` subprocesses alive (pids
  1510450/1510451/1510452 at 12:27Z; re-derive with `pgrep -af
  _run-job`). Start state: 843 cached rows / 12 done pairs, 45 jobs /
  483 pairs todo. First-pass AR jobs are slowest (fc_cache cold fetches
  0-240 leads per (model,init)). MONITOR: grep -E 'event job|scored
  \(|failed|perf' data/results/pipeline_full_run.log. Rerun the same
  command after it ends to collect failures/retries (incremental).
- fc_cache standalone test was KILLED mid-cold-fetch (it validated only
  that padded fetch is launched; cold fetch of 41 leads over a 30x30deg
  region is the single-job cost to watch in the real run, ~5-15 min
  expected). data/cache/fc_parts was wiped after the kill; the run
  rebuilds it.
- No other processes of mine running.

## Next steps (order)
1. Monitor the in-flight run; when it ends, rerun the same command once
   to sweep retry-failures, then read meta.json perf (peak_total_rss_gb
   must be <48; event_job_seconds median target <~60s warm — cold AR
   fetches will show as outliers, that's expected on first pass).
   If fc_cache cold fetches prove pathologically slow (>20 min/job),
   shrink fc_cache scope: fetch only requested leads, key includes a
   leads-set hash (fallback recipe in git-blame of fc_cache.py _FULL_LEADS).
2. Heavy-AR-job memory probe (orchestrator request #2): run ONE biggest
   AR event alone serially WITH sampling, e.g.
   `EWB_LIVE_INIT_PARALLEL=2 timeout 1800 .venv/bin/python -m ewb_live.pipeline --days 90 --jobs 1 --models ifs_hres --max-events 2`
   while sampling: `python -c "import psutil,time; [print(sum(p.memory_info().rss for p in psutil.process_iter(['pid']) if p.pid>0 and p.name()=='python')/2**30) for _ in range(180)]"` in background or use pipeline's own _PeakRss meta.perf output.
   Expect peak well below 10 GB/job now with crop; if not, next hog
   suspects: EWB align/reindex on 60-day target (chunk valid_time),
   score_event holding all models' datasets (build one model at a time).
3. Full run: `cd repo && uv run python -m ewb_live.pipeline --days 90
   --jobs 3 2>&1 | tee data/results/pipeline_full_run.log` (background
   terminal, notify). 45 events x up to 11 models, AR events first
   (submission order). Incremental: rerun same command continues from
   part files (data/results/events/<id>/...). Keep peak <48 GB (8 cores
   only; jobs=3 is the sweet spot; dask num_workers=2 per job already set
   in _score_job).
4. Sanity check scores: IFS HRES + AI models 2mT RMSE ~1-3 K at lead 24h
   rising with lead; investigate outliers (read scores.parquet, group by
   model/metric/lead).
5. weekly end-to-end: `uv run python -m ewb_live.weekly` — must run
   truth mirror update, mlwp store update, detection, scoring, history
   snapshot into data/results/history/<YYYY-WW>/, and _augment_meta adds
   cycle='weekly', week, next_update to meta.json. Re-run must be fast +
   incremental. NOTE weekly.py update_truth_mirror() step wired but
   never exercised end-to-end. NEW: weekly.py step 6 now calls
   `python -m ewb_live.viz --all` (design team's builder, timeout 5400,
   non-fatal try/except) after the snapshot — keep it in any weekly.py
   edits; see notes/channel_design_ewb.md.
6. pytest -m "not network" (must stay 39+ pass).
7. Update notes/ewb.md '## ewb2 log' with timings/perf + write real
   '## DONE' section (what works, timings, known gaps) when all above
   verified.

## Gotchas (learned the hard way)
- Machine: 8 cores (!), 64 GB RAM. `--jobs` > 3 thrashes CPU; workers
  peak RSS was 12-20 GB each BEFORE target crop fix.
- Terminal tool kills foreground calls at ~420s: run anything longer via
  background terminal (notify=true) and poll.
- pkill/pgrep -f patterns match YOUR OWN shell command line (self-kill).
  Kill by explicit PID lists; after killing a pipeline parent, ALSO kill
  orphaned spawn_main children (ps --ppid 1 -C python).
- ERA5 ARCO/mirror longitude is 0..360; event bboxes are -180..180 —
  use sources._transform.lon_indices (handles wrap), never raw .sel(lon).
- Mirror var is 'specific_humidity' (not 'q'); pressure coord 'level';
  keep coord names identical to ARCO when extending the mirror.
- zarr v2 append recipe: single combined attrs.put with
  _ARRAY_DIMENSIONS; v3 via xarray to_zarr(zarr_format=3) needs
  encoding={} and no _FillValue attrs on datetime vars.
- _score_job catches score_event exceptions -> returns None rows (models
  lacking vars are skipped BY DESIGN, recorded in log).
- scores.parquet currently contains OLD rows from the previous agent's
  3-model test; run() merges + dedups on fingerprint+truth_source, stale
  rows for changed truth_source mapping get filtered by keep== logic.
- 45 events (15 AR/heat/freeze), 13 models in registry, 11 scoreable.
- orchestrator rules: handoff at 90% context; jobs must be isolated
  (done); partial success fine; write scores for every job that succeeds.
- fc_cache._lead_sel handles timedelta/datetime/float lead_time dtypes;
  region key skips caching across antimeridian (rebuilds instead).
- inits_cache treats days ended >5d ago as final (aiwp_inits.json);
  recent 5 days always re-listed; weekly fresh weeks still correct.
