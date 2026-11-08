# Postmortem — scoring-run stall, 2026-11-06 18:52 → 2026-11-07 17:47 UTC

Owner: ewb (successor 4). Evidence: `ewb:/opt/ewb-live/logs/stall-20261107/`
(journal of both units, full pipeline.log, memguard.log, results mtimes,
pair_status snapshot, frozen meta.json, the runner script + unit files,
boot list). Nothing was deleted from the box; all timestamps below are UTC.

## TL;DR

The pipeline never hung. It scored **123 jobs / 20,121 rows** during the
~23 h stall window — but **zero** of it ever reached `data/results/`. The
interim-results writer was gated on `len(new_frames) % 10 == 0`
(pipeline.py:631); the counter grows by 9–11 model-frames per completed
job, so it almost never lands exactly on a multiple of 10. Results stayed
frozen at the last lucky hit (Nov 6 18:52:06, 6,437 rows) while `meta.json`
said `n_score_rows: 6437`. Meanwhile the run itself ran ~4× slower than the
ETA (serial AR lane), memguard pause/resume thrashing burned ~4 h and
killed in-flight jobs repeatedly, and the box rebooted at 17:47 with the
unit in a `static` (non-enabled) state, so scoring stayed down. The model
server was down for part of the window, so no agent was watching.

## Timeline

| When (UTC) | Event | Evidence |
|---|---|---|
| Nov 6 18:31:19 | unit **oom-killed** (jobs=3/AR1 collided with mlwp backfill) | journal `Failed with result 'oom-kill'`, kernel-oom.txt |
| Nov 6 18:32:12 | restart; interim write lands at **18:52:06** — the last published results (6,437 rows / 89 pairs) | scores.*/meta mtimes `2026-11-06 18:52:06` |
| Nov 6 18:59:02 | second **oom-kill** | journal |
| Nov 6 19:00–19:03 | relaunched with env, then agent-stopped to convert to a persistent unit (`ewb-live-pipeline.service`, Restart=on-failure) | journal, notes/ewb.md 19:01/19:14 |
| Nov 6 19:14 → 23:24 | **memguard pause/resume thrash**: ≥20 PAUSE→RESUME cycles, ~2–4 min apart. `mlwp_verify_store` (other agent) bursts 70–210 GB Committed_AS every ~3 min → avail oscillates 5 GB ↔ 127 GB; guard stops the unit under 8 GB, restarts 20 s later over 60 GB. Every stop kills in-flight workers (only per-job part files survive); the run never lives long enough to hit its interim write | memguard.log (PAUSED avail=5–7GB / RESUMED avail=127GB), journal Stopping/Started pairs every ~2–5 min |
| Nov 6 23:24 – Nov 7 03:46 | quiet-ish runs, repeatedly stopped by *deliberate agent deploys* (a3bb probe, B2 fix 01:37, shim/B8 deploy 02:08–02:23) | journal Consumed lines match notes/ewb.md session entries |
| Nov 7 00:27–00:28 | guard hits **external** pressure (~100 GB foreign python); logs PRESSURE-EXTERNAL + nudge, does *not* pause (scoring has priority) | memguard.log tail, pressure_alert.log |
| Nov 7 03:46:50 | **final run starts** (jobs=1 std + AR lane 1, EWB_LIVE_SKIP_EVENTS=4 ids) | runner-pipeline.sh, journal Started |
| Nov 7 04:04 | ARCO cutoff advanced → window shifted, re-detect republishes 27 events; resume point 5,626 rows / 79 pairs, 218 pairs to go | pipeline.log 04:04 |
| Nov 7 04:23 → 17:30 | 12 jobs complete over 13.4 h. AR jobs take 1.1–4.9 h each (82dc9c1b533c: 17,623 s, peak_rss 74.5 GB). Part files keep landing (128 during this segment), **outputs never written** — `% 10` gate never aligns | pipeline.log scored/event-job lines, score-parts-mtimes.txt |
| Nov 7 17:47:30 | **host reboot** (boot −1 ends) → clean stop of the unit mid-run (12 jobs done of 25; final merge at pool end never reached) | boot-list.txt, journal Stopped 17:47:30 |
| Nov 7 18:01:48 | boot; `ewb-live-pipeline.service` is **static** (no [Install] section — it predates `systemctl enable` practice) → does **not** come back. Scoring silently down | current-state.txt (`is-enabled: static`) |
| ~19:30–20:45 | Orca/model-server outage window: agents (including completion watchers) couldn't react; watcher design was completion-line-based anyway, so even online it would not have caught a *running-but-stale* run | orchestrator note; ops/ watcher procs in notes/ewb.md 03:55 |

Stall-window accounting: 123 `event job ... took` completions and 20,121
`scored (...) rows` logged between 18:52 and 17:45; published output
unchanged. No computation was *lost* (178 part files written after 18:52,
197 total) — it was invisible, which for the dashboard is the same thing.

## Root cause

**`pipeline.py:631` — `if len(new_frames) % 10 == 0: _write_outputs(...)`.**
The interim-writer intent was "every ~10 jobs". But `new_frames` is a list
of *per-model frames*: it starts at `1 + (cached part files for todo
events)` — an arbitrary offset (80 on the final run) — and `_collect`
appends all 9–11 model frames of a completed job at once. A counter that
increments by ~11 from an arbitrary offset lands on a multiple of 10 only
by coincidence, and each restart recomputes the offset. Final-run check:
12 job completions, counter walked 90→195, never exactly ≡0 mod 10
(simulation over the exact per-job frame counts,
stall-20261107/pipeline.log). Ruled-out alternatives: a failing write would
log `interim output write failed` (zero occurrences); writes to another
dir don't exist (`find / -name scores.parquet -newermt 2026-11-07` empty);
the parent was demonstrably alive (pair_status.json mtime 17:30:32, log
lines to 17:30:32).

This was a **silent-failure design**: every health signal (unit active,
parent alive, log growing, pair_status fresh, part files growing) said
healthy, while the artifact contract (meta.json.last_updated advancing)
was broken by construction.

## Contributing factors

1. **No per-job timeout.** The 4.9 h AR job (74.5 GB peak) had no ceiling;
   at jobs=1 one such job monopolized the lane and pushed the ETA from
   ~07:00 to ~16:30. Only the manual skip list capped memory bombs.
2. **Watchers that fire only on exit.** Both the systemd
   `Restart=on-failure` logic and the agent-side watchers keyed on process
   death / the `pipeline complete` line. A run that is alive but not
   publishing is, to all of them, healthy.
3. **memguard pause/resume thrash (19:14–23:24).** Stop/kill-based
   pausing with 20 s polling against a neighbor whose memory comes in
   3-min bursts converts memory pressure into total scoring stall — and
   each pause destroys in-flight AR jobs (hours of work). Also: the guard
   could *not* guarantee resume — a transient-unit stop earlier that
   evening (00:50–01:05) left the box guardless, and `Restart=on-failure`
   on the pipeline means guard-stop vs crash are indistinguishable.
4. **No network read timeouts** (observed risk this window: S3/ARCO reads
   inside scoring and truth-mirror had no timeout knob; a wedged socket
   shows up as a "slow job" — indistinguishable from slow compute).
5. **Model-server outage** — humans/agents couldn't inspect the box for
   hours; the completion watcher died with its session.
6. **Orchestrator watch lapse** — acceptance was "full run complete";
   nobody polled results freshness (a 5-minute `stat meta.json` check twice
   a day would have caught this at 18:52+2h).
7. **Unit `static`** → survived nothing: reboot at 17:47 = scoring down
   with no timer, no alarm, nobody's phone buzzing.

## Ranked fixes (implemented — see ewb.md session 2026-11-07)

1. **Interim results after every completed job** — replace the `% 10` gate
   with an unconditional `_write_outputs` on each collect. Removes the root
   cause outright; the merge is <5 s against a ≤30k-row frame. (Highest
   impact: even with every other factor present, results can never go
   stale while the parent is alive.)
2. **Progress heartbeat + staleness alarm** — pipeline touches
   `data/results/heartbeat.json` (job id, ts, counters) after every job;
   a systemd `WatchdogSec` keepalive thread in the parent plus
   `ewb-live-freshness.timer` (checks `meta.json.last_updated` +
   heartbeat; `systemctl restart ewb-live-pipeline` when stale >90 min
   while the unit is active). Catches *any* future silent-failure mode,
   including ones not anticipated here.
3. **Per-job timeout (env `EWB_LIVE_JOB_TIMEOUT_S`, default 10800) with one
   retry** — `subprocess.run(timeout=...)`; on timeout the job is killed
   (process group), documented as `bench_failed(timeout=...)` in
   pair_status, retried once serially, then recorded. No more 4.9 h
   lane-holders.
4. **memguard hardening** — never stop on external pressure (already the
   behavior after 22:05 owner rule), pause/resume replaced by
   SIGSTOP/SIGCONT of the *worker* processes (keeps the parent and its
   merged state alive, in-flight work survives a pause), guaranteed resume
   via monotonic watchdog: if paused > 15 min while avail > 40 GB, force
   resume + log ALERT. Resume no longer depends on the >60 GB threshold
   ever being reached again during a quiet window.
5. **Network read timeouts** — `EWB_LIVE_S3_READ_TIMEOUT_S` applied to the
   fsspec/obstore session defaults (read 120 s, connect 15 s) so a wedged
   socket fails the job fast into the timeout/retry path instead of
   pretending to compute.
6. **Enable the unit** (`systemctl enable`) so a host reboot resumes
   scoring automatically; freshness timer covers the stale case.
7. **Ops hygiene** (watch-side): orchestrator watch line gains
   "results age < 24 h" as an explicit acceptance check; weekly.py already
   re-runs pipeline, so the weekly timer is the ultimate backstop.

## What we lost / what we kept

- Lost: ~23 h of *publishing* (dashboard stale), ~4 h to guard thrash,
  repeated AR-job recomputation after each kill.
- Kept: all completed pair scores (part files), pair_status bookkeeping,
  and the frozen-but-valid 6,437-row results set. The incremental design
  (per-part files + pair_status) is why this was an availability incident
  and not a data-loss one.
