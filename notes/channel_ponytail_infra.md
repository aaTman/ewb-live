
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
