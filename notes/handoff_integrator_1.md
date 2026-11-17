# Handoff — integrator (session 2, started 2026-11-17 ~21:05 UTC)

Role brief: `briefs-new-events/integrate_dev.md` (+ its UPDATE section). Read
`COMMON.md` and that brief first. Worktree: `/home/orca/orca/workspaces/ewb-live/ne-integration`,
branch `aaTman/ne-integration` (off main `e492a52`). Never push, never touch
`/opt/ewb-live` data or units.

## Goal

Merge all new-event-type lanes into one coherent tree and stand up a **/dev/**
review site on the ewb LXC (`ssh root@192.168.0.121`) so the owner can visually
review the seven-lane dashboard before anything is promoted. Do NOT promote.

## State — DONE

1. **All 8 branch merges are in**, one merge commit each, full unit suite green
   after every merge. Final: **474 passed, 29 skipped** (`1b8cec0` + later
   commits; see `notes/channel_new_events.md` [integrator] 21:35 entry for the
   table, the registration-unification decisions and the 3 integration bugs).
   Order used: ne-ar-debug → ne-subsets → ne-coldsnap → ne-tc → ne-flood-detect
   (⊃ flood-clim, flood-imerg, verified ancestors) → ne-mlc-impl (⊃ mlc-research).
2. **/dev/ site is UP**: `http://192.168.0.121:8051/`, systemd unit
   `ewb-live-dev-web.service`, tree `/opt/ewb-live-dev` (own `data/results`;
   heavy read-only inputs symlinked from `/opt/ewb-live`; live never written).
   Tooling committed: `ops/dev_site.sh` (sync|init|lanes|web|status|watch|stop|url),
   `ops/dev_site_report.py`, `scripts/dev_review_shots.py` (Playwright: per-tab
   counts, event-detail probe, console errors, desktop/phone × light/dark shots
   into `notes/screenshots/`).
3. Test venv in the worktree: `uv sync --python 3.12` (done). Unit tests need
   `third_party/ExtremeWeatherBench` (gitignored) — copied from
   `../events/third_party`; run as
   `EWB_DATA_PREP_DIR=$PWD/third_party/ExtremeWeatherBench/data_prep .venv/bin/python -m pytest tests -q`.
4. Post-merge extras committed: `viz/timeline.py` TYPES += flood (flood Gantt
   lane gets bars via bbox-only entries); `app.js` honest counts for *any* lane
   with no score rows in the build (was: only `noScores` lanes) + a note whenever
   a lane has no table.

## In progress

* **Lane data build on the dev tree** — `ops/dev_site.sh lanes`, log
  `/opt/ewb-live-dev/logs/lanes.log`. Sequence: `pipeline --events-only --days 90
  --top-n 15` → `viz --all --force --types heat_wave,freeze,cold_snap,atmospheric_river`
  → `pipeline --publish-only --days 90`. Last seen: heat 142 / freeze 11 /
  cold_snap 22 raw, AR scan done, TC **44** events (49 storms, 5 below floor).
  Remaining in the log: midlat_cyclone (streams MSLP from ARCO — the slow one),
  flood (IMERG, ~11 min at 90 days), then viz, then publish.
  Check with: `bash ops/dev_site.sh status` (prints per-type selected/raw counts,
  meta counts, viz count, disk) and `bash ops/dev_site.sh watch`.
* **e2e baseline on LIVE** running locally: `/tmp/e2e_live.log`
  (`EWB_LIVE_URL=http://192.168.0.121:8050 .venv/bin/python -m pytest tests/e2e -q -m e2e`).
  Against DEV it was **37 failed / 106 passed / 4 skipped**; the live baseline
  tells us how many are pre-existing (pi-colors' channel entry says several
  legend/`#raw-layers` tests assert the OLD contract "and now fail by design").
  Diff the two failure lists before blaming the merge.

## Next (in order)

1. When lanes finish: re-sync code (`bash ops/dev_site.sh sync`) — the running
   job predated the `timeline.py` flood edit — then on the box re-run ONLY
   `.venv/bin/python -m ewb_live.viz.timeline` and
   `.venv/bin/python -m ewb_live.pipeline --publish-only --days 90`
   (from `/opt/ewb-live-dev`, with `EWB_DATA_PREP_DIR` exported) so
   `global_timeline.json` + `meta.json` (7-type `event_counts`, `ibtracs`) match.
2. Record per-type counts: `bash ops/dev_site.sh status` and
   `ssh root@192.168.0.121 'cd /opt/ewb-live-dev && .venv/bin/python ops/dev_site_report.py'`.
3. Screenshots + console check: `.venv/bin/python scripts/dev_review_shots.py
   --url http://192.168.0.121:8051 --out notes/screenshots` → commit the shots
   (or at least `report.json`) and confirm 0 console errors, that each tab shows
   non-zero real events where expected, and that TC/MLC event pages render a
   `svg.tv-svg` track (TrackViz) while heat/freeze/AR render the EventViz panel.
4. Step 5 report into `notes/channel_new_events.md`: the URL, per-type counts,
   known issues, and exactly what promotion would involve. **Do not promote.**
5. Compare `/tmp/e2e_live.log` vs the DEV e2e run; list any merge-caused e2e
   regressions in the DONE entry (and fix if they are mine).

## Gotchas

* **`aaTman/ne-subsets` is DEV-ONLY** (owner update): MLWP virtual-store-only
  scoring + subsets cache removal. Promotion = merge the lane branches but NOT
  that merge commit (`e4fd539`); say so loudly in the report.
* Detection lanes are re-derived from ARCO because the dev tree's raw frames do
  not span the window (~40 s per 4-day AR chunk; heat/freeze/cold_snap ~30 s each).
* Only **16 of 30** released events have `data/results/events/<id>/viz.json` in
  the live results copy — a pre-existing live gap (same numbers on live, verified).
  The dev `viz --all --force` step should fill them: if the dev site shows more
  viz than live, that is expected, not a regression.
* `meta.json` honest-zero currently exists **only for the TC lane**
  (`meta.ibtracs`). `events/mlc.py` fails soft to `[]` on an ARCO/MSLP read
  failure, so an MLC lane that failed to run would read as "no midlatitude
  cyclones met the criteria" — flag this in the report as a follow-up
  (generalise to `meta.lane_status`) rather than inventing it at the 11th hour.
* Midlat_cyclone colour: the mlc branch picked violet `#5f3d99`, essentially the
  same hue as the TC lane's `#6f42c1`; I re-hued it to amber (`styles.css`, two
  lines, commented). Owner/design may want it back — mention it.
* Cloudflare tunnel on the box has **no credentials** (`ewb-live-tunnel.service`
  inactive, no `ewb-live.json`/`cert.pem`), so `https://ewb.espr.ai` is not being
  served by this box and there is no dev *hostname*; the LAN port is the honest
  equivalent (same pattern as `/opt/ewb-preview-*` on :8061-8063). Adding a real
  `dev.` hostname needs the owner's Cloudflare authorisation.
* Disk on the box: 51% used, ~235 G free. Dev tree ≈ 110 M + caches.
* `notes/channel_new_events.md` conflicts on every cross-branch merge: it is
  append-only per agent, so resolve with
  `git merge-file -p --union ours /dev/null theirs` (see `/tmp/ne_merge.sh`
  pattern; `/tmp/hunk.py` has the hunk-join helpers used for the shared files).
