# Handoff — guardian-design #1 (written 2026-11-08 ~14:35 UTC, context ~?%)
Role/brief: notes/briefs/guardian_design.md. Findings table: **notes/guardian_design.md** (source of truth, IDs G-01…G-37). Channel: notes/channel_guardians.md (append-only; my posts there carry wrong future timestamps up to 14:55 — wall clock was ~14:10–14:30).
## State
- Deployed (synced): CSS overrides block at the END of dashboard/styles.css ("guardian-design polish"), About rewrite + head meta/favicon + a11y fixes in index.html, eventviz.js patches (tierDataURL outline-only, bbox colour). e2e: 24 passed on the public URL after those syncs.
- RUNNING on the box: unit `ewb-viz-gd-all` (MemoryMax=12G) = serial rebuild of all 31 event viz (heat+freeze DONE, 15 ARs in progress ~10 min each). Check: `ssh root@192.168.0.121 "journalctl -u ewb-viz-gd-all --no-pager | grep -E 'INFO root: \[|failed' | tail"`; DONE line = `INFO root: DONE`. It also rewrites events/index.json + global_timeline.json at the end. Never run `python -m ewb_live.viz` CLI (always runs the raw_exceedance tail = other agent's lane).
- build.py `_reason()` change (archive-hole wording) is in the worktree and synced; takes effect on the next rebuild.
- Raw map is OFF-LIMITS until a `## … RAW MAP READY` header appears in notes/channel_design_frontend.md; then add it to the full audit (check frame decode, 390 layout, dark, reduced motion, a11y labels −6h/+6h).
## Still to do (in order)
1. When the unit finishes: contact-sheet ALL 31 events (harness m1.py, light; dark for ~6; 390 for ~4), verify AR pages (IVT ramp, mask contours, hit/miss text, IFS panel now present), fill the "visually verified" list in guardian_design.md (all 31 ids).
2. Re-QA the JS asks as frontend lands them (list = channel_guardians.md asks #2–#28): scorecard hero RMSE headline, EarlySignal units, tooltip CI, a11y (re-run a8.py: expect 0 violations), Plotly resize in detail Scores fold, landfall/max-run story numbers, chip placement/zoomSnap.
3. Data issues reported to ewb (channel_design_ewb.md): 3 AR events all-zero CSI (G-34), merged-event max_consecutive_days (G-37), landfall_days > duration, perf leak in public meta.json (G-31), raw exception text in pair_status (G-30). Verify fixes live; if CI/data not fixed by launch they must be listed in the DONE verdict.
4. Take "after" screenshots → notes/screenshots/gd-after-*.png (home light/dark 1440+390, About, heat/AR/freeze detail, scorecard, scores fold). Befores already saved as gd-before-*.
5. Run `bash scripts/public_scrub_check.sh` and `… https://ewb.espr.ai` (both CLEAN at 14:05) before DONE; also grep served JSON for non-pattern leaks (perf field was one).
6. Write `## DONE` in notes/guardian_design.md: launch verdict + post-launch polish (AR field clipped at bbox+2° (G-13), Gantt cursor, dueling-map extras, 0.25° polar smear for Greenland, Matter vs Inter fonts).
## Gotchas
- Orca has internet (Esri tiles load); use GD_LOCAL=1 to preview worktree CSS/HTML/JS against live data without deploying. `./ops/remote.sh sync` pushes the WHOLE worktree incl. frontend's WIP (they sync too).
- Frontend rule: re-read a file right before patching; surgical edits to app.js/index.html/styles.css (raw block is theirs). eventviz.js: I patched two spots, told them.
- Detail layout relies on DOM order in eventviz.js: root > toolbar, cbRow, picker, obsBlock, grid, nodata, dur; my CSS makes .ev3-grid display:contents and root a 2-col grid. If that DOM changes, revisit.
- Plotly detail charts mis-size on first open (G-29) — not mine; frontend.

## UPDATE ~15:20 UTC (supersedes "Still to do" above where different)
- Viz rebuild DONE (31/31, IFS present); timeline rebuilt with the ponytail date fix. All 31 event pages visually verified (list in guardian_design.md). After-screenshots saved: notes/screenshots/gd-after-*.png (12). `## DONE (interim)` is written in guardian_design.md.
- Antimeridian audit done and posted (channel_guardians 15:35): viz/catalog/global/detail/raw all pass with the one dateline event; synthetic freeze test across 180 passed.
- OPEN, verify when frontend lands them, then flip the verdict: ask #30 (binning), #3 hero headline, #4 default date, #5 H2, #31 raw ocean/pole rows. Re-run: harness v1/v2/v3-style probes (hero headers; ifs 1d heat Lowest-max MAE should read 0.157 not 0.94; global date default; story/title), axe (a8.py; raw-prev/next fixed in index.html), e2e (`EWB_LIVE_URL=https://ewb.espr.ai pytest -m e2e`), scrub (both modes).
- Data (ewb) to re-verify when they post: G-34 zero-CSI events, G-40 TC event, G-31 `perf` removed from public meta.json (`curl -s https://ewb.espr.ai/data/results/meta.json | grep -c peak_total_rss` must be 0), G-37/G-30. If still open at launch, keep them in the DONE verdict.
- If the weekly run (Mon 07:00 UTC) regenerates event ids, rerun /root/gd_rebuild.py on the box under systemd-run (MemoryMax=12G) — NOT the viz CLI.
