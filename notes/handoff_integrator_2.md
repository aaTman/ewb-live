# Handoff — integrator pass 2 (started 2026-11-18 ~21:45 UTC)

Role brief: `briefs-new-events/integrate_dev.md` + its UPDATE 2026-11-18 22:00Z
section. Read `briefs-new-events/COMMON.md` (incl. the Time-box rules) first.
Worktree `/home/orca/orca/workspaces/ewb-live/ne-integration`, branch
`aaTman/ne-integration`. Never push, never touch `/opt/ewb-live` (live), never
promote. All compute on the ewb LXC (`ssh root@192.168.0.121`) via `ops/dev_site.sh`.

## Goal (unchanged)

Merge chips + mlc-fix + tc-map into the integration tree, rebuild the DEV data on
the box, deploy to the /dev/ site `http://192.168.0.121:8051/`, verify with
Playwright + e2e, and report: per-type counts, known issues, and what promoting to
live would involve (**ne-subsets/`e4fd539` stays DEV-ONLY**).

## DONE this pass (all committed on `aaTman/ne-integration`, nothing pushed)

1. **P0 `pipeline._sel_thresholds`** read `flood.p95` / `flood.FLOOR_MM` (don't
   exist) → every `meta.json` write died (detect AND `--publish-only`). Fixed to
   `flood.DEFAULT_QUANTILE` + string-keyed `floor_mm`; test added
   (`tests/test_pipeline_unit.py::test_sel_thresholds_is_serialisable_for_every_lane`).
2. **P0 ARCO probe**: `probe_arco_cutoff` asserted the single hour at `now−14 d`
   was valid; ARCO now ends **2026-11-12T23Z** with *interior* NaN holes
   (2026-11-04T05Z, 2026-11-09T23Z measured) → every lane build aborted. Now walks
   backwards from `now` (12 h stride, bounded); `truth_cutoff()` falls back to the
   cached cutoff with a warning. New `tests/test_truth_probe.py` (4 tests, fake
   archive, no network). Owner-visible: the dashboard window can only run to Nov 12.
3. **Merges**: ne-mlc-fix `1625905`, ne-tc-map `ff85aad`, ne-chips `3a52d4a`.
   Suite after each: **546 passed / 29 skipped** (`--ignore=tests/e2e`, needs
   `EWB_DATA_PREP_DIR=$PWD/third_party/ExtremeWeatherBench/data_prep`). Only
   conflicts: `notes/channel_new_events.md` (union-merged) + 2 PNGs (took chips').
4. **INCIDENT, mine**: `ops/dev_site.sh sync` used `rsync --delete --delete-excluded`
   → deleted `/opt/ewb-live-dev/{data,.venv,third_party,logs}` at 21:55 (the whole
   night's dev data). Live + the 4 preview trees verified untouched. Fixed
   (no `--delete-excluded`, post-sync guard), `init` re-seeded results from live,
   full rebuild re-run. Also fixed: my guard's *indented* heredoc terminator
   silently swallowed the `init)` case arm (heredoc terminators must be col 0).
5. `ops/dev_site.sh` **`assets`** subcommand: publish → timeline → detached
   `tc_wind` (60 min cap).
6. **serve**: `tc_wind.json` added to `OPTIONAL_FILES` → 204 instead of 404
   (verified `curl → 204`). It was the *only* console error on `/dev/`.
7. **e2e/harness integration fixes**: chips abbreviated the chip labels
   (`Trop. cyclone`, `Atm. river`, `Midlat. cyclone`) which silently broke `test_tracklane_fe.py`
   (3 honest-zero tests, tc's) and `test_dashboard_regions_fe.py::test_region_filters_lists_and_counts`;
   both now key on `[data-tab=…]`. `scripts/dev_review_shots.py` clicks by
   `data-tab` (was positional) and both harnesses use `open_page()` (wait for the
   tab control, not `networkidle` — external basemap requests never settle here,
   which killed every screenshot pass with a 30 s goto timeout).
8. Data rebuilt on the dev tree: detection complete (see the 22:55 channel entry
   for the table; MLC 143 raw / 80 published, 202 published total).
9. Channel entries at 21:56, 22:04, 22:15, 22:30, 22:55, 23:36 (all committed).

## In flight when this was written

* `ops/dev_site.sh lanes` on the box: viz finishing (38 events), then
  `pipeline --publish-only --days 90` (rewrites `meta.json` → 7-lane
  `event_counts`, `lane_status`, window end 2026-11-12, `ibtracs`).
  Poll: `bash ops/dev_site.sh status` / `ssh root@192.168.0.121 'tail -3 /opt/ewb-live-dev/logs/lanes.log'`.
* e2e against **live** with the merged tests: **48 failed / 113 passed / 4 skipped**
  (`/tmp/e2e_live_merged.log`, names in `/tmp/live_merged_names.txt`; 12 of the
  failures are `test_chips_fe.py` asserting the new chip UI that live lacks — expected).
  Yesterday's live baseline with yesterday's tests: 34/109 (`/tmp/e2e_live.log`).

## NEXT, in order

1. When lanes finish: `bash ops/dev_site.sh assets` (publish → timeline → wind
   field detached), poll `ssh … 'tail -3 /opt/ewb-live-dev/logs/tc_wind.log'`
   (~9 min, network-bound, 60 min cap).
2. `bash ops/dev_site.sh status` + `ssh … 'cd /opt/ewb-live-dev && .venv/bin/python ops/dev_site_report.py'`
   → record per-type counts + `meta.event_counts` + `viz docs`. Expect
   selected heat 15 / freeze 5 / cold_snap 7 / AR 11 (gate-rejected 4) / TC 44 /
   MLC 80 / flood 40; 202 published; truth cutoff 2026-11-12T23Z.
3. Playwright: `.venv/bin/python scripts/dev_review_shots.py --url
   http://192.168.0.121:8051 --out notes/screenshots` (desktop+phone × light/dark,
   console errors, event-detail probe) and `.venv/bin/python scripts/chips_shots.py
   --url http://192.168.0.121:8051 --prefix final` (1440/820/390, `pageOverflow`
   must be 0). Commit the shots + `report.json` with `git add -f` (notes/screenshots
   is gitignored but tracked-by-force).
4. e2e against **:8051** alone (not concurrently with anything else — the 45F
   dev run yesterday was partly Playwright-start failures from two concurrent
   runs): `EWB_LIVE_URL=http://192.168.0.121:8051 .venv/bin/python -m pytest
   tests/e2e -q -m e2e`. Compare **by name** with `/tmp/live_merged_names.txt`;
   any dev-only failure is mine to explain or fix. Expect `test_trackwind_fe.py`
   to need `tc_wind.json` (step 1) and the chips tests to pass here.
5. Final DONE entry in `notes/channel_new_events.md`: URL, per-type counts, known
   issues (see below), promotion plan (below), and what I did NOT do.

## Known issues to report

* ARCO truth ends 2026-11-12T23Z + interior holes; IMERG sampled 89 of 95 days.
  The UI does not *say* either of these; adding a line is a design call (owner).
* AR lane: publish gate rejects 4 of 15 selected AR events (`peak_land_pixels <
  500`) → tab count ≠ selection count. Gate is behaving as designed (ar-debug).
* MLC logs a ~4 KB per-reason screen histogram per build — noise; mlc's call.
* midlat_cyclone hue: I re-hued to amber earlier (branch's violet == TC violet);
  chips' palette work now gives MLC red `#a0312d` — check which the owner wants.
* Dev-only: `ne-subsets` (`e4fd539`, MLWP virtual-store-only scoring) must NOT go
  to live. Promotion = merge the lane branches (ar-debug, coldsnap, tc,
  flood-detect, mlc-impl, mlc-fix, chips, tc-map) and leave that one out.
* Live deploy would also need real scoring for the new lanes (dev reused live
  scores) — a full `python -m ewb_live.weekly --days 90` cycle on live.

## Gotchas

* Run unit tests as `EWB_DATA_PREP_DIR=$PWD/third_party/ExtremeWeatherBench/data_prep
  .venv/bin/python -m pytest tests -q --ignore=tests/e2e` (~2.5 min, 546/29).
* `channel_new_events.md` conflicts on every cross-branch merge: resolve with
  `git merge-file -p --union ours base theirs` (`:1/:2/:3` from the index).
* `notes/screenshots/` is in `.gitignore` yet files there are tracked → `git add -f`.
* `ops/dev_site.sh sync` is now safe, but **never** re-add `--delete-excluded`.
* Box: 256 G RAM, 234 G free disk, load ~3.5 during builds; viz is 49–134 s/event.
* The `lanes` script's viz step also rewrites `global_timeline.json` and mirrors
  `raw_exceedance` frames; `assets` re-publishes meta after it — idempotent.
