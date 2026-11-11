# Handoff — design lane → pi successor (2026-11-10 00:25 UTC)

## 1. Role & owned files
Design lane: visual direction, CSS tokens, copy, and the viz asset builders.
- `ewb_live/viz/exceedance.py` — raw_exceedance frame builder (shared with ar-ramp; both lanes edit it)
- `ewb_live/viz/timeline.py`, `ewb_live/viz/exceedance.py` publish paths — design-owned builds
- `dashboard/styles.css` — design tokens (frontend also edits; re-read before every patch)
- `dashboard/eventviz.js` — shared with frontend-ev; design edits event-detail visuals
- `dashboard/mergedmap.js` — frontend-owned; design reads for QA only
- `tests/test_raw_exceedance.py` — design-owned tests; `tests/e2e/test_guardian_fe.py` has the AR-presence regression net

## 2. Where I stopped (most important first)
**HALF-DONE — the IVT orientation fix (ponytail 00:04Z MUST).** Applied in the worktree, NOT synced, NOT rebuilt:
- `exceedance.py`: `_make_layer_fn` now returns ivt display-oriented (matches its docstring contract); builder writes `ivt_class_bytes(class_plane)` without the extra `_to_display` double-flip; `LAYER_REV` v6→**v7**.
- New test `test_ivt_sidecar_orientation` in `tests/test_raw_exceedance.py` drives the real `_make_layer_fn` seam. `.venv/bin/python -m pytest tests/test_raw_exceedance.py -q` → 9 passed.
- **Consequence:** the live site still serves v6 `.ivt` sidecars whose classes are antipodal garbage (IVT ramp paints wrong/phantom classes; the binary AR layer itself is fine). Do NOT let this sit.
- Caution: ar-ramp successor 2 was also editing `exceedance.py` (patch-tool sibling warning). Re-read the file; the fix may have landed twice — check `_make_layer_fn` return + the `ivt_class_bytes(` write + `LAYER_REV` before re-applying.
- **NOT DONE** (read, triaged, not implemented): ponytail design 00:15Z SHOULD — gate `index["dur"]`/`index["ivt"]` on newest frame's sidecar existing on disk (one `.exists()` check each, ~:709).
- Done earlier: dead `:has()` rule deleted (styles.css); eventviz `durAlpha` unified with front-page heat bins (3/4/5-6/7-13/14+, <3 unpainted; freeze 1/2/3/4-6/7+) + bar-tip text + comment — synced + smoke-verified live; AR colorbar SHOULD confirmed already fixed; 20:40Z + 00:15Z triage posted to `notes/channel_ponytail_design.md`.

## 3. Running jobs
- **v7 rebuild RUNNING on the box** (launched 01:38Z by design): systemd unit `ewb-ivt-v7`, MemoryMax=12G, cwd /opt/ewb-live, log `/opt/ewb-live/logs/ivt-v7.log` (started with "layer_rev v5 -> v7, full rebuild", ~1-2 h). Do NOT start a second builder. Watch: `./ops/remote.sh run systemctl is-active ewb-ivt-v7`. When it exits, verify index `layer_rev:"v7"` + spot-check one frame's `.ivt` non-zero count vs `.bin` AR count, then run the guardian AR test.
- Nothing else of mine is running. The 22:40Z e2e finished: 110 passed / 3 failed, all 3 ramp-lane WIP (failure signatures posted in `notes/channel_design_frontend.md` 22:52Z). Log: `/home/orca/.hermes/cache/scratch/e2e_ramp.log`.
- Sibling agents alive: ewb succ-5, frontend succ-2, ar-ramp succ-2 (Hermes — may be mid **ivt build job** on the box; check `ops/remote.sh` logs before launching any build of your own), science-review succ-3, regions-review (other worktree). Let them run.

## 4. Remaining tasks (ordered, with acceptance)
1. **v7 rebuild (ivt orientation).** Sync repo to box (`./ops/remote.sh sync`), then ONE memory-capped job: `./ops/remote.sh run python -m ewb_live.viz.exceedance` (LAYER_REV v7 forces full rewrite of raw frames; ~hours; no heavy compute on orca). Accept: index.json `layer_rev:"v7"`; per-frame `.ivt` non-zero cell count ≈ `.bin` AR-cell count (was ~1k vs ~15k); UK Oct-3 AR cells classed, no antipodal phantoms; `test_ar_layer_visible_on_live_frames` green. Coordinate with ar-ramp succ-2 so only ONE builder runs.
2. **Gate dur/ivt index keys on disk** (exceedance.py publish, one check on newest frame; tests unchanged → the incremental tests still pass).
3. **AR IVT ramp QA** when ar-ramp posts `AR RAMP READY` in `channel_design_frontend.md`: live check https://ewb.espr.ai/#map=2026100312&layers=a — ≥2 ramp classes, 5-step legend 250/500/750/1000/1250 kg m⁻¹ s⁻¹, binary fallback when `.ivt` absent; screenshots 1440/390 light+dark to `notes/screenshots/`; colorblind check: adjacent lightest bins dE≥12 on light ground. If the guardian test fails, fix the TOKEN, not the test threshold.
4. **Gap-bridging (owner Option B)** — builder side is already in v6/v7 (`tracks[sid].bridged`, gap frame shows last footprint). Remaining is frontend: lighter fill for bridged frames + Playwright "Japan AR no longer blinks". Design reviews the visual honesty. Japan case anchors: sid 2746→2661 handoff, Oct 17-18.
5. **Ponytail frontend channel 00:15Z** (frontend implements, design verifies): MUST play-leak one-liner `active.stopFn = stopPlay;` in eventviz.js; SHOULD `.run`/`.ivt` byteLength check + no-negative-caching summary warn in mergedmap.js getFrame; SHOULD layerRuns vs server `.run` seeding disagreement (seed viz.json from shared run_recurrence or drop the "runs" claim).
6. **Ewb-lane items design tracks only:** heat p85/freeze p15, AR tropical exclusion + IBTrACS TC screen at track level, landfall_days ≥ 1, publish-gate drop logging (ponytail ewb 00:15Z NICE: test asserting gate_rejects keys {stage,id,problem}). About-copy contract (verbatim, owner-approved): "AR mask: EWB detection minus tropical objects (any cell within 15 deg of the equator) and tropical cyclones". Builders publish via temp-name + atomic swap (rule already stated to ar-ramp 22:42Z).

## 5. Gotchas & open questions
- NEVER `git commit` — the orchestrator commits. No pushes.
- All EWB compute on the LXC via `ops/remote.sh` (sync/run/pull-results). Bare `python` is not on PATH in orca — use `.venv/bin/python`.
- Siblings edit `eventviz.js`/`mergedmap.js`/`styles.css`/`exceedance.py` live: re-read immediately before every patch.
- e2e: `.venv/bin/python -m pytest tests/e2e -q -m e2e` (~4.5 min). Known-red (frontend's): axe violations chromium-detail-light/dark, and 2 ramp WIP tests until ar-ramp fixes fixtures/labels ("AR — IVT magnitude" chip).
- Serve hygiene: Cloudflare DYNAMIC + no-store — no CDN purge ever needed; verify with md5 of served vs worktree file.
- Cross-seam xarray: `slice(350, 380)` continuous labels; `isel` has no periodic wrap (xarray#7031).
- Live index.json right now: `d44179a6c28ecca5df191d71df19ef67`, layer_rev v6, window ends 2026110900, `ivt.bins=[250,500,750,1000,1250]`.
- Open: did ar-ramp succ-2 already apply the orientation fix? Check before double-patching. Open: whether frontend applied the 00:15Z play-leak MUST yet.

## 6. Channels & teammates
- `notes/channel_design_frontend.md` — main coordination (frontend, ar-ramp). Latest: my 22:42Z schema-agreed/review note + 22:52Z ramp-failure handoff.
- `notes/channel_ponytail_design.md` — ponytail findings + design triage (append replies under each batch).
- `notes/channel_ponytail_{frontend,ewb}.md` — other lanes' findings (00:15Z blocks untriaged by their owners).
- `notes/ar_ramp.md` (ar-ramp log), `notes/briefs/ar_colour_ramp.md` (binding brief), `notes/kt_design.md` + `notes/handoff_design_final.md` (predecessor docs), `notes/briefs/merged_global_map.md`.
- Teammates: frontend succ-2 (term_55602a39-edbc-42a8-8310-703b3f5e6d2a), ar-ramp succ-2 (Hermes), ewb succ-5, science-review succ-3, ponytail reviewer (hourly), orchestrator. Successor rule: keep the role name.

HANDOFF WRITTEN
