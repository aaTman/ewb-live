# Handoff — frontend role → pi successor (2026-11-10 00:20 UTC)

## 1. Role & owned files
You are "frontend": implementer/owner of `dashboard/` in the EWB Live repo.
Owned: `dashboard/mergedmap.js` (global merged map), `dashboard/eventviz.js`
(Leaflet event-detail map), `dashboard/app.js` (routing/state glue),
`dashboard/styles.css`, `dashboard/index.html`, `tests/e2e/test_dashboard_fe.py`
(116 e2e), `tests/e2e/test_guardian_fe.py` (live-site guardians).
Design owns viz/assets design; ewb owns `ewb_live/` pipeline. Binding:
- **NO git commits** (orchestrator commits). Edit files only.
- **No EWB compute on this container** — all execution on the ewb LXC via
  `ops/remote.sh` (SPEC.md). Editing/reading repo here is fine.
- Worktree is SHARED with other agents editing the same files concurrently.

## 2. Where I stopped (exact)
**Done + LIVE (tree == deployed):**
- `mergedmap.js`: AR-outage fixes deployed to box by single-file scp —
  (a) AR branch paints IVT-class ramp ONLY where `.ivt` exists, else binary
  bit, and NEVER indexes `run[]` (`.run` has exactly 2 planes: heat,freeze;
  reading plane 2 = the AR-outage bug class, happened twice on 11-09);
  (b) rebound `const u8 = frame.bin` (refactor dropped it → whole-raster
  blank); (c) AR paints opaque alpha 255 (ramp+binary), heat/freeze keep
  RUN_ALPHA 215 — ar-ramp's pixel tests match CSS RGBs exactly on canvas.
- Box `data/results/raw_exceedance/index.json`: restored full v5 (336
  frames, dur v1, 3350 tracks) atomically after someone's v6 builder
  published a 4-frame stub at 21:44Z. The stub is kept at
  `index.json.v6-wip-2158` on the box. Frame `.bin/.trk` bytes were never
  overwritten (still the 09:08 v5 set), so the restore is consistent.
- `tests`: deleted `test_zz_debug_freeze.py` (committed scratch); added
  guardian `test_ar_layer_visible_on_live_frames` (AR px>500 + AR box on
  2026100312, runs against the box — THIS is the regression gate that catches
  AR going invisible; keep it green); updated `test_dur_chip_labels...`
  assertions for the new IVT-ramp label + swatch (unverified by a rerun).
- Last full e2e (after deploy): **110 passed, 3 failed** — all 3 were the
  ar-ramp agent's own tests (see half-done). My subsets
  (detail/layers/overlay/axe/merged: 25) all green.

**Half-done / edited-NOT-deployed:**
- `eventviz.js` LOCAL CHANGES NOT DEPLOYED (ponytail 23:59+00:10 fixes,
  syntax-checked only):
  1. play-interval leak: teardown calls `active.stopFn`; assignment
     `active.stopFn = stopPlay` added in the slider block (ponytail 00:15
     said assignment missing — it reviewed an older snapshot; grep confirms
     both halves present in the current tree).
  2. deleted dead `tierRGB`.
  3. AR obs default colour `'#ffffff'` → `css('--text','#111')` (was
     invisible on light basemap).
  4. `let customized = Object.keys(savedLayers).length > 0` (a `#lyr=`
     restored view must not be wiped on first lead click).
  Deploy = `scp -q dashboard/eventviz.js root@192.168.0.121:/opt/ewb-live/dashboard/`
  then run `-k "detail or axe"` (see 4-rem).
- **Unresolved**: `test_ar_ramp_paints_class_plane` and
  `test_ar_ramp_fallback_without_class_plane` still red: on the synthetic
  fixture (`_build_gd_fixture(..., raw_ivt=True)`, 5° grid, frames
  20251106xx, AR blob = 5 cells at row 28, cols 40-44) the canvas paints the
  basemap + readout fine but I found **zero AR/green pixels** (full-canvas
  scan). Live-site AR is green, so this is fixture-specific. I was mid-debug
  of `_build_raw_fixture`'s `.ivt`/AR-bit writes (they look correct: bits at
  blobs[2], ivt classes 1-5, `.bin` rewritten AFTER ivt block — check the
  rewrite actually includes the added AR bits and whether the test's
  wait/timing or `raw.ivt` flag handling in `getFrame` is the gap). Do not
  "fix" by loosening the live-site guardian gate.

**Open 00:15Z ponytail items, not started:**
- `mergedmap.js getFrame`: no length-check on `.run`/`.ivt` payloads —
  truncated sidecar → `undefined` → silently unpainted. Add in the `.then`:
  `if (run && run.byteLength !== 2*cells) run = null;` (and `ivt` vs `cells`).
  The per-frame miss already console.warns; a one-time summary counter was
  proposed but skipped as extra.
- `eventviz.js :991` comment claims detail "runs" = same rule as front page;
  FALSE: home map `.run` planes are seeded from pre-window history
  (`index.dur.seed`), detail `layerRuns` restarts at window start. Frontend
  fix: delete the false comments; real fix needs ewb to put a seeded run
  grid in viz.json (open question, see 5).

## 3. Running processes
- No cron jobs, no `ops/remote.sh bg` units started by me.
- All my background pytest runs exited (last: 110 passed / 3 failed).
- On the box: ewb's own weekly/web systemd cycle (do not touch). Other
  agents ran `remote.sh run ...` bursts at ~21:56Z (logs
  `/opt/ewb-live/logs/run-20261109T21*.log`) — finished, safe to ignore.
- Nothing of mine needs to finish. Safe to leave everything.

## 4. Remaining tasks (ordered, with acceptance)
1. **Verify undeployed eventviz.js fixes** — deploy + `pytest -m e2e -q -k
   "detail or axe or layers"` green; add the ponytail-requested e2e: play ▶,
   close detail, open a DIFFERENT event, wait 1.5 s → no pageerrors and the
   new event's URL `#day` absent/unchanged (play leak must not fire hooks).
   Fixtures: `live_url`, `heat_event`/`ar_event`, `_open_event()`,
   `#detail-close`, play button `.ev3-play`.
2. **Fix the two ar-ramp fixture tests** (root cause in the fixture or
   mergedmap's 5°-grid path, not the tests' intent). Acceptance: full
   `-m e2e` suite ≥ 113 passed / 0 failed.
3. **getFrame sidecar length checks** (above). Acceptance: a hand-made
   truncated `.run` fixture paints binary, doesn't blank cells.
4. **GAP-BRIDGING (owner option B)** — frontend half: render bridged AR
   frames with a lighter fill + `bridged` attribute/title. BLOCKED on ewb
   (temporal-continuity tracking rule in `ewb_live/`, max gap 1×6 h step;
   2-step gap ends track; tests on their side; frames rebuilt as v6).
   Acceptance: Playwright check that the Japan AR no longer blinks between
   consecutive frames (owner's case), and bridged steps are visually lighter.
5. **AR IVT ramp data path** (with ar-ramp/design): bins every 250
   kg m⁻¹ s⁻¹ (250/500/750/1000/1250+); ramp engages when the index
   advertises `ivt` and `.ivt` sidecars exist for all frames — currently
   only ~20 partial `.ivt` files on the box and the live index (v5) has no
   `ivt` key, so live AR is solid-colour by design. Acceptance: live site
   shows stepped greens per brief `notes/briefs/ar_colour_ramp.md`, guardian
   AR test still green, binary fallback stays.
6. **Landing + publish-hygiene rules to enforce in review** (owner
   decisions): AR tropical exclusion (equator ±15°) + TC screen at TRACK
   level; `landfall_days >= 1` for AR events; heat P85 / freeze P15
   thresholds; publish-gate drops must be logged; **builders write to temp
   names then swap** (rename over `index.json`/`events_selected.json` only
   when complete — the 21:44Z 4-frame stub nearly killed the live map).

## 5. Gotchas & open questions
- **Deploy**: box serves `/opt/ewb-live/dashboard/` statically; no service
  restart needed for JS. Use single-file `scp` while other agents have WIP
  in the tree — `ops/remote.sh sync/run` rsyncs the WHOLE tree (races +
  pushes their half-saved edits). Never `run` without knowing tree state.
- **Cache**: `https://ewb.espr.ai` (Cloudflare): JS/assets `no-cache` (no
  purge needed after deploy), `index.json` `no-store`, frames `.bin/.trk`
  `public max-age=3600` (a same-name frame rebuild can serve stale ≤1 h).
- **Data shapes**: live raw grid is 0.5° (721×1440, row=(lat+90)*2);
  fixture grids differ (5°). `.bin` = zlib, layer-major h,f,a bitpacked;
  `.run` = zlib u8 2 planes (heat,freeze) — AR must never index it;
  `.ivt` = zlib u8 per-cell class 0/1-5. `index.json` v5: schema 2,
  `dur:{rev,seed}`, tracks 3350; mergedmap accepts string-or-{ts} frames.
- **Paint contract**: AR opaque (255) so canvas RGB == CSS
  `--dur-ar-1..5` / `--series-ar` exactly — pixel tests rely on this.
  Heat/freeze blend at 215 over the basemap (calibrated dE); a pixel test on
  those must not expect raw CSS RGBs.
- **Prime-meridian**: boxes are minimal EASTWARD arcs normalized to
  [-180,180); `occFit` seeds ±Infinity (NEVER ±180); detail fits the
  occupied blob, not the field grid (test contract A4).
- **Concurrent agents**: check `git diff`/file mtimes before editing shared
  files; re-read the region right before patching. A sibling's mid-write
  snapshot caused phantom SyntaxError test failures earlier.
- **Open questions**: (a) ar-ramp fixture zero-AR cause (only unresolved
  defect); (b) layerRuns seeded-grid — needs viz.json schema change by ewb
  or drop the "runs Nd" claim from detail; (c) whether/when design ships
  the v6 frames (bridged footprints + full `.ivt`) and the LAYER_REV bump.

## 6. Channels & teammates
- `notes/channel_design_frontend.md` — orchestrator + design + frontend
  (post root-cause/STATUS notes here; my last posts: AR-OUTAGE ROOT CAUSE
  19:05Z, ARS-BACK-ON ~22:10Z).
- `notes/channel_design_ewb.md` — ewb coordination (periodic-longitude fix,
  gap-bridging rule, viz.json schema).
- `notes/channel_ponytail_frontend.md` — the ponytail reviewer's findings
  + my triage replies (MUST/SHOULD/NICE; re-verify anchors, it reviews
  snapshots and can lag the tree).
- `notes/kt_frontend.md` — frontend knowledge-transfer notes (keep current).
- Teammates: **design** (frames/viz assets), **ewb** (pipeline/tracking),
  **ar-ramp** (Mistral vibe, `term_6daf7b0a...` — owns the AR-ramp feature,
  coordinate before mergedmap paint-path edits), **hotfix** agent (done,
  ARS RESTORED posted ~20:25Z).

HANDOFF WRITTEN
