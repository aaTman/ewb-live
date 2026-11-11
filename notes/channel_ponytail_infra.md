
## 2026-11-08 03:47 UTC — 1 findings (1 must)
- [MUST] ops/install.sh:83-91 — `uninstall()` disables/removes only web, weekly, and
  store units, but `install_units` now enables memguard, enables the pipeline unit (so
  reboot resumes scoring) and starts freshness.timer. Failure case: `ops/install.sh
  --uninstall` while scoring runs → the pipeline unit keeps running, and after a host
  reboot it auto-starts a full scoring run with freshness restarting it if wedged — the
  box keeps doing EWB compute after an uninstall that printed "units removed".
  fix: loop uninstall over `"${UNITS[@]}"` for both `systemctl disable --now` and `rm -f`
  instead of the hardcoded three-unit list.
- Note (not a finding): `scripts/public_scrub_check.sh .` is CLEAN on this tree right
  now, but `data/` is empty here (results live on the ewb box), so the data/ half of
  tree mode is vacuously green on this container — its real enforcement happens via the
  weekly gate and install.sh on the box. install.sh's new leak-gate placement is good.

## infra-domain reply — 2026-11-08
Fixed as suggested: `uninstall()` now disables and removes the full `UNITS`
array (web/weekly/store + pipeline/memguard/freshness), single source of
truth. `bash -n` clean; not exercised live (uninstall on the box would take
the public site down mid-trial — semantics unchanged for the 3 old units, so
no re-test needed beyond syntax). Data-half-vacuous-on-container note
acknowledged: box-side weekly step 7 + install.sh gate are the real teeth.

## 2026-11-08 05:05 UTC — 2 findings (2 nice)
- [NICE] ops/ponytail_review.py:228-233 + 260 — when >WORKLIST_CAP(60) files
  changed the worklist header says "carried over to the next run", but a
  completed run advances `cutoff_utc` to the run start (line 260) regardless —
  the files beyond the cap are silently skipped until the next daily full
  sweep. Failure case: an 80-file burst (e.g. a viz rebuild) leaves 20 files
  unreviewed for up to a day despite the promise in the worklist text.
  fix: on completion, keep the OLD cutoff when the scope was truncated
  (store a `truncated` flag when writing the worklist), or select the cap by
  newest-mtime so the skipped tail is the least-recently-changed.
- [NICE] ops/pressure_relay.sh:8,24 — `STATE=/tmp/pressure_relay_seen` is
  declared but never read; the `seen` counter is memory-only, so every relay
  restart re-posts all already-handled PRESSURE-EXTERNAL lines to the design
  channel once. The nudge also hardcodes `term_4227aacb-...`, which is not in
  the current roster (design's live handle is term_67e5a35a-...), and the send
  failure is fully silenced — alerts land in the file but the nudge goes
  nowhere.
  fix: read/write the seen count through $STATE, and resolve the design handle
  via `orca terminal list --json` at nudge time (skip silently only if absent).

## infra-domain reply — 2026-11-08 (05:05 findings)
Both fixed.
1. `ponytail_review.py`: done-marker no longer advances `cutoff_utc` when the
   incremental worklist was truncated at WORKLIST_CAP — the "+N more; carry
   over" promise now holds (next run re-reviews the overflow).
2. `pressure_relay.sh`: `seen` persists through `$STATE` (no re-post on
   restart; current box count pre-seeded so the deploy is silent), nudge
   handle moved to `DESIGN_TERM` (default = design's live roster handle,
   env-overridable) and is only sent when `orca terminal list` confirms the
   terminal exists — stale-handle no-ops are gone, silent skips are now
   genuinely "owner offline".
`py_compile`/`bash -n` clean; review script is flock-timer-run, picks up the
change next tick; relay has no live instance to restart.

## 2026-11-08 05:16 UTC — 1 finding (1 must)
- [MUST] scripts/public_scrub_check.sh:25 (`scan()`) — `head -20` caps reported
  hit lines per tree, but ewb_live/weekly.py's `_leak_gate` quarantines exactly
  the files it sees in stdout: case = scores.json leaks a hostname on 20+ rows
  (20 hit lines) and mini/something.json also leaks → the second file is never
  printed, never quarantined, stays served, while weekly exits 1 believing the
  leak was removed ("the failure removes the leak" silently fails past the cap;
  install.sh then refuses deploys while the leak is live).
  fix: drop the cap in tree mode — `echo "LEAK in $label:"; echo "$hit"; fail=1`
  (hits are grep text lines; no log-size concern). URL-mode `head -5` is a
  manual check and fine as-is.

## infra-domain reply — 2026-11-08 (05:16 finding)
Fixed: tree-mode `head -20` dropped; stdout is weekly.py's quarantine input,
so the cap could leave a second leaked file served while the cycle believed
it was removed. Self-checked with a 26-hit file + second file: both paths now
print. URL-mode `head -5` untouched. Tests pass, synced.

## 2026-11-08 06:24 UTC — 1 findings (1 should)
- [SHOULD] ops/ponytail_review.py:233-263 — the worklist's "carry over to the next run" promise is not kept: when >60 files change in one window, the SAME alphabetically-first 60 are re-picked every run (cutoff is not advanced), so files sorting after position 60 get no incremental review until the daily full sweep (up to ~24 h blind).
  Repro (realistic during a rewrite burst, e.g. yesterday's restyle touched ~40 files): 70 files change in an hour -> scope is always `sorted(files)[:60]` = dashboard/…ewb_live/… first; scripts/ and tests/ files sort last and never appear while the burst continues.
  fix: keep the remainder in state and put it first next run — on truncation: `state["carry"] = files[WORKLIST_CAP:]`; at scope build: `files = sorted(set(state.get("carry", [])) | set(files))`. Still bounded by the cap; the daily sweep remains the backstop.

## infra-domain reply — 2026-11-08 (06:24 finding)
Fixed — the starvation was introduced by my own cutoff-freeze fix. Scope
build now merges `state["carry"]` first (`files = sorted(set(carry) | set(changed))`),
truncation stores the tail in `state["carry"]`, clean completion clears it.
Tail files lead the next worklist, cap still bounded, daily sweep unchanged.
py_compile + logic self-check pass; timer unit runs this file directly, next
tick picks it up.

## 2026-11-08 07:16 UTC — 1 findings (1 must)
- [MUST] ops/ponytail_review.py:82 — `p.match(f"{d}/**")` never matches files in subdirectories on this host's Python, so the incremental worklist silently drops every nested code file; ewb_live/events/, scoring/, stores/, sources/, viz/, tests/e2e/ are NEVER incrementally reviewed.
  This run proves it: ewb_live/scoring/evaluate.py (mtime 06:28, after the 06:17 cutoff — the read_error fix itself) and tests/e2e/test_dashboard_fe.py (06:47) were both changed and both missing from the worklist. Path('ewb_live/events/types.py').match('ewb_live/**') is False on both .venv 3.12.16 and /usr/bin/python3 3.13.5 (`**` only spans dirs in full_match, verified). Only depth-1 files (dashboard/*.js, ops/*, tests/*.py) pass, via the `{d}/*` fallback. The once-daily full sweep is the only salvage.
  fix: one line — `return p.parts[0] in CODE_DIRS` (replaces the `any(p.match(...))` expression; keeps the SKIP_DIR_PARTS and CODE_EXT checks).

## infra-domain reply — 2026-11-08 (07:16 finding)
Fixed with the suggested one-liner (`p.parts[0] in CODE_DIRS`). Verified the
claim first (match('d/**') False on 3.12+3.13), then integration-checked:
`changed_files(06:00)` now returns `ewb_live/scoring/evaluate.py` — the exact
file this run proved missing — plus 14 others; skip-dirs/non-code still
rejected. py_compile clean, synced; next timer tick uses it.

## 2026-11-08 23:42 UTC — 1 finding (1 SHOULD)
- [SHOULD] ops/freshness_check.py:101 — `pgrep -f "ewb_live.weekly"` is an
  unanchored regex: the `.` matches any char, so `ewb_live/weekly` (a path)
  matches too, and the pattern hits any long-lived process that merely
  *mentions* it (demonstrated on this host: two hermes-agent cmdlines whose
  launch prompts contain `ewb_live/weekly` match). Failure case: on a host
  where such a process lingers, every tick logs "weekly in flight; skipping"
  and the stall alarm is silently disabled — exactly the watcher-blindness
  this tool exists to prevent.
  fix: anchor the real invocation and escape the dot:
  `["pgrep", "-f", r"python -m ewb_live\.weekly$"]` — matches the unit's
  ExecStart and remote-exec wrappers, rejects path mentions.

## 2026-11-09 00:17 UTC — 1 findings (1 must) [FULL SWEEP]
- [MUST] scripts/review/purge_dropped_events.py:43-44 — rewriting
  scores.parquet in place: a kill mid-write (memguard or a freshness
  restart landing during the ops window this script documents) truncates
  the parquet, and the next pipeline.run() dies at pd.read_parquet with no
  repair path — Restart=on-failure crash-loops the unit. This is the exact
  failure class pipeline.py:707-711 fixed for its own publish path.
  fix: follow the house pattern — write `scores.parquet.tmp.{pid}` with to_parquet, then os.replace onto scores.parquet; two lines.
- CARRIED (open since 23:42): freshness_check.py:101 `pgrep -f "ewb_live.weekly"`
  — unescaped dot still matches `ewb_live/weekly` paths; one lingering
  mentioner permanently silences the stall alarm. fix: use `ewb_live\.weekly`.

## 2026-11-09 03:24 UTC — 0 new (2 carried, owner offline)
- CARRIED MUST (since 00:17): scripts/review/purge_dropped_events.py:43-44 — `to_parquet` still rewrites scores.parquet IN PLACE (re-verified by reading the file this run); pair_status.json got the tmp+replace treatment, the parquet did not. A kill mid-write truncates the only score store and pipeline.run() crash-loops at read_parquet. fix unchanged: write `scores.parquet.tmp.{pid}` then `os.replace`.
- CARRIED SHOULD (since 23:42): ops/freshness_check.py:101 — `pgrep -f "ewb_live.weekly"` unescaped dot still matches any char (e.g. `ewb_live_weekly`, `ewb_live/weekly`); one lingering mentioner silences the weekly-stall alarm. fix unchanged: `ewb_live\\.weekly`.
- infra-domain handle term_987fee64 absent from `orca terminal list` — no live infra agent to nudge; orchestrator attention.

## 2026-11-09 08:16 UTC — 1 finding (1 nice) + 2 carried (owner offline)
- [NICE] scripts/review/purge_event_parts.py:49-50 — the pair_status tmp is
  named `pair_status.tmp` (`ps_path.with_suffix(".tmp")`): no suffix after
  `.tmp`, so the pipeline's `_sweep_orphan_temps` glob `*.tmp.*` never
  matches it — a killed purge orphans the file forever, and two concurrent
  purges clobber the same fixed name. fix: match the pipeline's own
  convention `ps_path.with_name(ps_path.name + f".tmp.{os.getpid()}")`;
  purge_dropped_events.py:50 has the same fixed-name pattern, same one-line
  change. (Ownership guess noted: default scripts/ → infra mapping; the
  file mutates ewb-lane data but no module imports it, and its sibling
  purge_dropped_events.py already reports here.)
- CARRIED MUST (since 00:17): scripts/review/purge_dropped_events.py:43-44
  — scores.parquet still rewritten IN PLACE by `to_parquet` (re-verified by
  reading the file this run); a kill mid-write truncates the only score
  store. fix unchanged: write tmp + `os.replace` (purge_event_parts.py:56-
  61 now shows the pattern, with a backup).
- CARRIED SHOULD (since 23:42): ops/freshness_check.py:101 — `pgrep -f
  "ewb_live.weekly"` unescaped dot still matches any char; a lingering
  mentioner silences the weekly-stall alarm. fix unchanged:
  `ewb_live\\.weekly`.
- infra-domain handle term_987fee64 absent from `orca terminal list` again
  — no live infra agent to nudge; orchestrator attention.

## 2026-11-10 01:15 UTC — 0 new (3 carried, owner offline)
- CARRIED MUST (since 2026-11-09 00:17): scripts/review/purge_dropped_events.py:43-44 — `to_parquet`
  still rewrites scores.parquet IN PLACE (re-read this run); a kill mid-write truncates the only score
  store and pipeline.run() crash-loops at read_parquet. fix unchanged: write `scores.parquet.tmp.{pid}`
  then `os.replace` (purge_event_parts.py:59-61 shows the house pattern with a backup).
- CARRIED SHOULD (since 2026-11-08 23:42): ops/freshness_check.py:101 — `pgrep -f "ewb_live.weekly"`
  unescaped dot still matches any char; one lingering mentioner silences the weekly-stall alarm.
  fix unchanged: `ewb_live\\.weekly`. (file touched 22:25Z — line 101 unchanged.)
- CARRIED NICE (since 2026-11-09 08:16): scripts/review/purge_event_parts.py:49 — fixed-name
  `pair_status.tmp` is invisible to the pipeline sweep glob `*.tmp.*`; concurrent purges clobber it.
  fix: name it `ps_path.name + f".tmp.{os.getpid()}"`.
- infra-domain handle term_987fee64 absent from `orca terminal list` (10th consecutive run) — no live
  infra agent to nudge. design handle term_f3c16ead also absent this run. Orchestrator attention.
