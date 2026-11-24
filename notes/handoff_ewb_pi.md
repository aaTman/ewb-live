# Handoff — ewb lane (pipeline / scoring / raw layer) → pi successor
Written 2026-11-10 ~01:3xZ by hermes successor-5, on owner's replacement order. Supersedes nothing; the older notes/handoff_ewb2_6.md is background.

## 1. Role and owned files
You are "ewb" in the EWB Live team, working in the SHARED worktree `/home/orca/orca/workspaces/ewb-live/dogfish` (branch aaTman/live-extreme-weather-dashboard). Hard rules:
- ALL EWB compute runs on the EWB LXC (`root@192.168.0.121`, `/opt/ewb-live/`) via `./ops/remote.sh run <cmd>` / `status` / `bg`. NEVER run EWB compute in this container (orca). Portability rule: code must be box-portable.
- NEVER `git commit` — the orchestrator commits. Edit files only.
- Offline suite before deploying: `uv run pytest tests/ -q --ignore=tests/e2e -m "not network"` (was 209 green at last full run).
- You own: `ewb_live/events/*` (detection, ar_filter, select, qualification), `ewb_live/scoring/*`, `ewb_live/pipeline.py`, `ewb_live/weekly.py`, `ewb_live/viz/exceedance.py` (raw frame builder — design co-edits it now), `scripts/review/*` audit scripts, `SPEC.md` events/scoring sections.
- DASHBOARD FREEZE (owner, fable-review 01:24Z in notes/channel_design_frontend.md): do NOT edit `dashboard/*` or scp dashboard files to the box until fable-review posts 'REDESIGN LANDED'. Dashboard changes go as a message to fable-review in notes/channel_design_frontend.md.
- Hermes successor rule (if you ever hand off to hermes again): successors keep the role name and launch via `orca agent launch --agent hermes`.

## 2. Where I stopped (exact state)
The combined owner change "GAP-BRIDGING + landfall floor 3→1" is CODE-COMPLETE in the local tree, NOT deployed, tests written and passing; SPEC + About copy updated. Everything below is UNCOMMITTED in the worktree (orchestrator commits):
- `ewb_live/events/ar_filter.py` — GAP BRIDGE done: tracking persists through 1-step (6 h) mask gaps; `tracks[k]["bridged"] = [step idx]`; ghost grid writes the last footprint into `labels[t-1]` (labels/display only); physical `keep` masked `& mask` so bridged cells NEVER enter the physical mask; guards: `len(prev_track) > 1` (empty-prev-step) and `.ravel()` on the joint index. A 2-step gap ends the track.
- `tests/test_ar_filter.py` — 4 new tests (1-step dropout keeps one id + exact bridged footprint; 2-step gap ends track; bridged footprint absent from keep; far reappearance = new track). File: 11 passed.
- `ewb_live/events/select.py` — `AR_MIN_LANDFALL_DAYS = 1` (was 3), docstring cites owner 2026-11-09. ALL other curation rules untouched (60° span cap, top-15 quota, 500-px publish gate, tropical exclusion, TC screen @ 500 km).
- `ewb_live/events/ar.py` — `AR_RULE_REV = "v5"`.
- `ewb_live/viz/exceedance.py` — `LAYER_REV = "v7"` (design landed the v7 .ivt orientation fix on top of my v6 bridged note — both ride the same undeployed bump). My wiring: `_ar_window_pass` returns a 6th value `bridged` {sid: [gap tags]}, folds ghost pixels into the DISPLAY keep, writes `bridged` into `ar_tracks.json`; `build()` unpacks the 6-tuple and adds `index["tracks"][sid]["bridged"]` for the frontend's lighter fill. `_empty_pass` returns 6-tuple.
- `ewb_live/pipeline.py` — publish-gate rejections now APPENDED TO curation.json (`stage: "publish_gate"`, `problem` keys) — the "log every drop" owner requirement. Verified on box via journalctl replay (rank_and_cap 31 → gate rejects 3 → kept 28 == shipped 28).
- `dashboard/index.html` — About copy updated (landfall ≥ 1 day wording + bridged-frames honesty note). This edit predates the freeze; it is in the tree — fable-review will inherit it, mention it to them in channel_design_frontend before REDESIGN LANDED.
- `tests/test_raw_exceedance.py` — I fixed 2 stub `layer_fn`s (added 5th ivt plane; done-rule requires .ivt); design added `test_ivt_sidecar_orientation` on top. Targeted files: 37 passed.
- New audit scripts (uncommitted, mine): `scripts/review/d2_pixel_audit.py` (time-gated D2 gate), `scripts/review/rank_cap_replay.py`, `scripts/review/purge_event_parts.py` (updated: also drops scores.parquet rows).
- `notes/handoff_ewb2_6.md` — my previous handoff (now stale on this change).

Closed before this change (do not redo): scoring lane FINAL (26,565 rows, AR@48 n=12, science-review §I FINAL PASS); raw v5 rebuild on box CLEAN (336 frames, D2 min 503.4 km); "missing AR layer" was a FALSE ALARM (v5 md5s public==box, viz assets 200); publish-gate + rank-cap hygiene fixes verified; SPEC §2 bullets (lines ~86–88) already document BOTH decisions (gap-bridge + landfall ≥1).

## 3. Running jobs / processes
- Box: NOTHING active (`./ops/remote.sh status` last checked 01:3xZ — no ewb-live-* units active; last weekly completed 07:23:31Z Nov 9, scrub CLEAN).
- Always-on timers are safe and must stay: `ewb-live-pipeline.service`, `ewb-live-weekly` (static, timer-driven), `ewb-live-freshness.timer`, `ewb-live-web.service`, `ewb-live-memguard.service`. The ponytail hourly review loop (frontend-owned) keeps writing notes/channel_ponytail_*.md — read-only reviewer, leave it.
- Locally: no background processes from me.

## 4. Ordered remaining tasks (acceptance criteria)
1. Deploy the undeployed change: rsync/sync tree to box per ops/remote.sh workflow, run pipeline: re-run selection with floor 1 (incremental scoring picks up newly-qualified events; scores.parquet grows past 26,565 rows), then raw frames v7 force-rebuild + global_timeline regeneration (builder writes temp names then swaps — already the case), republish, `public_scrub_check` CLEAN. Acceptance: weekly exits 0, index rev=v7, curation.json shows publish_gate rejections logged.
2. D2 re-gate the v7 frames: `python scripts/review/d2_pixel_audit.py` — acceptance: min distance ≥ 500 km (was 503.4 on v5).
3. Post the new AR list (which ARs entered vs the shipped 12 by the floor change) to notes/channel_design_ewb.md AND notes/channel_science_review.md for verification — owner's explicit request. Include old-vs-new funnel numbers (was 65→39→2→9→3→12 shipped).
4. science-review/ponytail open items still unfixed in MY lane: ponytail NICE 00:15Z (channel_ponytail_ewb.md) — add a unit test pinning the publish-gate curation keys (`stage`/`id`/`problem`) so sr14f_report.py can't silently drift. MUST 23:59Z (ar_filter empty-step argmax) is already fixed (my `len(prev_track) > 1` guard + tests) — reply in that channel to close it.
5. Frontend contract to relay (frozen lane, via channel_design_frontend to fable-review): v7 index publishes `tracks[sid].bridged = [tags]`; AR fill for those cells must be lighter. The Japan AR blink check should then pass.
6. Still PENDING owner decisions — do NOT change: eb42387c81a3 span-cap exemption, severity-vs-IVT ranking, landfall floor 3-vs-2 (moot: owner chose 1).
7. Housekeeping if time: `tests/test_tc_screen.py` still asserts `<= 250.0` buffer while TC_SCREEN_KM is 500 (suite passes, misleading).

## 5. Gotchas
- Score parts cache keys on event FINGERPRINT, not code version — rule changes that alter event content need `scripts/review/purge_event_parts.py` (drops score_parts + spill + scores.parquet rows) or re-score is a NO-OP (pipeline `done` set derives from scores.parquet rows).
- `_frame_complete` requires .bin+.run+.trk+.ivt — an incremental run will NOT rewrite stale sidecars; a rules change MUST bump LAYER_REV (v7 already bumped, undeployed — don't bump again without cause).
- xarray rule for this repo: if chunks=None, subset 1.variables 2.time 3.space (sel/isel only), THEN chunk, THEN math.
- Tropical exclusion is TRACK-level and strict `< 15.0` (|lat| exactly 15.00 is RETAINED); applied in detection, scoring, AND raw AR layer through the ONE shared `ar_filter_tracks`.
- Bridged ghost pixels live ONLY in labels/display keep; events, scores, physical masks never see them. Keep that invariant when touching ar_filter.
- Seam: periodic longitude via label-then-union in `events/seam.py`; `PeriodicBoundaryIndex` in `ewb_live/periodic_index.py` is the shared crop primitive (design owns it — flag changes).
- Never write `data/` by hand on the box while a unit is running (the v4 D2 contamination came from a concurrent-writer collision).
- IBTrACS years gate = {y-1, y, y+1}.
- ops/remote.sh `bg` args must be passed separately (quoting quirk); watchers must fire on unit INACTIVE/FAILED, not only success.

## 6. Channels and teammates
- notes/channel_science_review.md — science-review (verifications, §I final PASS history).
- notes/channel_design_ewb.md — design (raw layer consumer, v7 bridged contract).
- notes/channel_design_frontend.md — design+frontend joint; fable-review (Claude) owns dashboard/* NOW under freeze until 'REDESIGN LANDED'.
- notes/channel_frontend.md — frontend blink/AR facts.
- notes/channel_ponytail_{ewb,design,frontend,science_review}.md — hourly read-only review loop findings; ewb-channel items are yours.
- The orchestrator relays owner decisions; owner decisions in these briefs are final (gap-bridge option B, landfall ≥1, TC buffer 500 km).

HANDOFF WRITTEN
