## Latest run — 2026-11-11 09:21 UTC (RUN STAMP 2026-11-11T09:17:13Z)
- Mode: FULL SWEEP (ponytail-audit). Read in full this pass: ewb_live/regions.py,
  scripts/build_region_scores.py, scripts/review/sr18_coverage.py +
  tests/test_sr18_coverage.py, scripts/dev_preview.py, dashboard/regions.json,
  dashboard/mergedmap.js (full), dashboard/app.js region/rail/legend diffs
  (5c5e096..worktree, both committed regions-integration and uncommitted rail),
  ewb_live/viz/exceedance.py (uncommitted dur.min/dur_block diff + ivt_class_plane,
  build/publish paths), eventviz.js dur-raster/layerRuns regions, ops/remote.sh
  diff, weekly.py step 6c, serve.py OPTIONAL_FILES, pipeline.py regions write +
  curation/publish-gate block, sr13/sr15/sr16/sr17/sr_gate (arc-form re-check),
  purge_dropped_events.py, purge_event_parts.py, freshness_check.py,
  test_regions.py, index.html (rail/legend ids), e2e legend/rail coverage.
- Not read / thin (honest gaps): styles.css and index.html markup beyond the
  rail/legend diff; the already-committed +690 app.js regions-integration diff
  was read via its added lines (innerHTML/fetch/region paths), not line-by-line;
  one-off scripts/review/sr14*/d2_pixel_audit/ar_diff/rank_cap_replay and
  tmp/ QA artifacts not re-read; ops units unchanged since the last full review;
  ewb_live/{events,scoring,sources,stores} internals unchanged since the
  2026-11-10T00:17Z full-sweep reads (mtimes verified) — those reads stand;
  tests not executed (static-review rule); third_party/ and data/ off-limits.
- Load assumption: one weekly producer (systemd on ewb) + a public read-only
  audience on ewb.espr.ai; judged accordingly.
- New findings this run: 4 — 0 must, 4 should (frontend 1, ewb 2, science-review 1).
  - frontend SHOULD: app.js:477/:967/:1801 region copy still says bbox-centroid
    while the shipped rule is footprint-overlap (regions.py, owner 2026-11-10).
  - ewb SHOULD: regions.py:69-71 `_lon_in_box` region-width lacks `% 360` ->
    any antimeridian-crossing REGION silently matches zero cells (latent with
    shipped regions.json; app.js:731 port shares it). ewb SHOULD:
    build_region_scores.py:63 rewrites the published scorecard_by_region.json
    in place (weekly 6c runs it post-publish; kill mid-write -> region UI dead
    for the week). ewb NICE carried: curation.json key set still untested.
  - science-review SHOULD: sr18_coverage.py derives the expected-cell set from
    observed rows only -> a whole (model,metric) lane vanishing from a type
    (the D1 recurrence) prints "coverage OK".
  - design/mlwp-store/infra: no new findings with a concrete failure case
    (adaptive gm-legend + rail redesign read clean; XSS sweep: legend/rail
    innerHTML is static or numeric; no infra leaks in served dashboard files).
- Channels appended: frontend (8: 1M/3S/4N), ewb (2 + 1 carried), infra (0 new,
  3 carried re-verified), science-review (1 + resolved list), design (cross-post only).
- Owners nudged: NONE — every roster handle is offline this run (ewb
  term_e87b37a0, frontend term_55602a39, design term_f3c16ead, infra-domain
  term_987fee64 [12th+ consecutive], science-review term_bd4274e6, guardians,
  mlwp-store all absent from `orca terminal list`; only 10 π/agent terminals
  live with no roster handles). **Orchestrator attention: all five lanes with
  open findings, incl. 2 MUSTs (frontend ghost-pixel painter, infra
  purge_dropped_events in-place parquet), have had no live owner for 24 h+.**
- Findings resolved since last run (verified by reading the files): 3 (science-review)
  — sr_gate G4 / sr17 regions-import MUST (ewb_live/regions.py + regions.json
  landed with the regions-integration merge), sr15:19-27 / sr16:23-38 / sr13
  `_bb`+`lon_overlap` antimeridian MUSTs now eastward-arc, sr17 NICE closed
  (hemis line gone, EXTRA asymmetry stated at :62-63).
- Open totals: 15 — frontend 8 (1M/3S/4N), ewb 3 (0M/2S/1N), infra 3 (1M/1S/1N),
  science-review 1 (0M/1S/0N), design 0, mlwp-store 0. Grand total 15 (2M/7S/6N).
## 2026-11-10 01:05 UTC (carried to history) (RUN STAMP 2026-11-09T22:15:13Z — second pass)
- **Mode:** INCREMENTAL (18 files). SECOND pass on this stamp: the 23:59Z run
  covered the same list, so this pass re-verified its two MUSTs against the
  current files and picked up what that pass declared unchecked. CAVEAT: both
  dashboard lanes were rewritten under review again (mergedmap.js changed twice
  inside one review call; eventviz.js 3d591bff, mergedmap.js d7bdb6ff when
  cited) — findings cite function names and quoted code, not line numbers.
  Read in full: eventviz.js (teardown, slider, layerRuns/duration raster),
  mergedmap.js (getFrame, paint loop, ramp decode, legend), exceedance.py
  (.run/.ivt sidecars, _make_layer_fn, _ar_window_pass, publish index keys),
  ar_filter.py (track linking + gap bridge), pipeline.py publish-gate block,
  serve.py allowlist regex, index.html, styles.css dur/AR tokens,
  test_dashboard_fe.py + test_guardian_fe.py + test_ar_filter.py,
  sr14f_report.py, and the three carried infra files. No third_party/ or data/
  read (the carried infra MUST is judged from source lines only, since
  results/scores.parquet is off-limits); no EWB compute; static review only.
- **Findings:** 6 new — 1 must, 4 should, 1 nice.
  - frontend 3: **MUST** eventviz.js teardown reads `active.stopFn`, which is
    never assigned anywhere in the file (its only occurrence is the read) → the
    play-interval fix for the 00:10Z MUST is a silent no-op: press play, close
    the detail, and the 2 Hz setInterval keeps re-encoding duration PNGs and
    overwriting the NEXT event's #day/#lyr. fix: `active.stopFn = stopPlay;`
    in the slider closure. **SHOULD** layerRuns seeds runs from nothing while
    the home map paints server `.run` planes seeded from mirror history
    (`index.dur.seed`) → the same cell of the same event shows the 14-day
    colour on the home map and "3 days" in the detail panel and "runs Nd" stat;
    the parity comment is false. **SHOULD** mergedmap.js getFrame caches a
    failed sidecar as null for the session and length-checks nothing → a
    truncated `.run` makes cell reads `undefined` → cls 0 → silently unpainted,
    where the Python reader raises.
  - design 1: **SHOULD** exceedance.py writes `index["dur"]`/`index["ivt"]`
    unconditionally although both are documented feature-DETECT keys → a lane
    that stops emitting a sidecar still advertises it, and the client then
    requests ~720 extra objects per 90-day window, warns once per frame, and
    paints the binary fallback forever with no signal. **NICE** mergedmap.js
    legend re-types `index["ivt"].bins` instead of reading them.
  - ewb 1: **NICE** pipeline.py publish-gate records ("stage"/"problem") feed
    sr14f_report.py with no test on the key names (no test touches
    curation.json) → a rename silently reverts cand_table.md to the inferred
    reject reason, wrong and silent.
- **Verdict:** the v3 raw-map feature's two carried MUSTs are FIXED (see
  resolved list). The live blocker is the half-applied play-teardown guard —
  worse than the original bug only in that it now looks handled.
- **Channels appended:** channel_ponytail_frontend.md (3),
  channel_ponytail_design.md (1 + 1 note), channel_ponytail_ewb.md (1).
- **Owners nudged:** frontend (term_55602a39), design (term_f3c16ead), ewb
  (term_e87b37a0) — all live, send ok.
- **Owners offline:** infra-domain (term_987fee64) absent (12th consecutive
  run); the roster's guardian-frontend / guardian-design handles are stale
  (absent from the live list), so the guardian-test item was filed to frontend
  rather than dropped. infra-domain's 3 carried findings re-read at the cited
  lines, ALL STILL OPEN: scripts/review/purge_dropped_events.py:43-44 (in-place
  scores.parquet rewrite), ops/freshness_check.py:101 (`pgrep -f
  "ewb_live.weekly"` — unescaped dot), scripts/review/purge_event_parts.py:49
  (fixed-name `.tmp` shared by concurrent runs). **Orchestrator attention.**
- **Findings resolved since last run:** 4 — design MUST (.ivt orientation):
  `_make_layer_fn` now returns heat/freeze/keep/labels **and** the ivt stack
  through `_to_display` ("contract: ALL outputs display-oriented") and
  LAYER_REV is bumped to v7 to force the rebuild; ewb MUST (ar_filter
  empty-step ValueError): the current code takes the `if mask[t].any() … else
  n=0` path and both argmax blocks are guarded by `… and n`, so an empty step
  no longer reaches `np.argmax([])` — note that no test covers that sequence
  yet; frontend NICE tierRGB (now read as the colourbar fallback); frontend
  NICE AR obs contour colour (now `css('--text', '#111')`).
- **Open totals (channel-verified):** 8 — frontend 2 (1M/1S new; the 00:10Z
  play MUST is the same defect in its new form), design 1 (S), ewb 1 (N),
  infra-domain 3 (1M/1S/1N carried, offline); science-review / mlwp-store 0.
- **Not checked:** nothing executed — no pytest, no e2e, no viz build (both
  "resolved" MUSTs are argued from source, not from a rebuilt artifact); real
  `data/results/*.ivt` payloads unread (data/ off-limits), so the orientation
  fix is unverified against shipped bytes; test_dashboard_fe.py fixture
  arithmetic read but not executed; sr14b/c/d/e internals unchanged since the
  clean 20:40Z pass.

## 2026-11-10 01:02 UTC (carried to history) (RUN STAMP 2026-11-09T23:15:22Z)
- **Mode:** INCREMENTAL (21 files). This stamp's list added events/ar.py,
  events/ar_filter.py (new), events/select.py and serve.py to what the two
  sibling passes (20:15Z/22:15Z stamps) reviewed; those four were the focus,
  with re-verification passes over the shared files at the ~00:55Z snapshots.
  Read in full: ar_filter.py (256 lines — link/split/merge/gap-bridge coherent
  with its docstring; bridge ghost writes masked against occupancy, keep &= mask
  keeps bridged cells out of physical consumers; the "display-only" ghost only
  buys id stability — no map path renders it), ar.py rewrite (6-hourly arrays,
  np.unique dedup of the +1-day chunk overlap — identical data in both chunks,
  safe; daily-union semantics match the prior resample-max; labels6[~keep6]=0
  attribution guard; seam-stitched bboxes; track_id detail), select.py (AR
  floor 3→1 owner relaxation + PeriodicBoundaryIndex rewrite of
  _bbox_clim_mean — mean equivalent, np imported, tests unaffected),
  serve.py (strict sidecar allowlist), exceedance.py ivt/run/labels chain,
  mergedmap.js (owner's 22:42Z in-flight AR-outage fix: explicit li===2
  branch never indexes the 2-plane .run), all 7 scripts/review files,
  zz debug test, both e2e diffs. Connected reads: events_selected.json is a
  bare list (rank_cap_replay:32 safe), seam.py/periodic_index.py present,
  live index.json keys (schema 3, dur+ivt present), zero ivt coverage in
  tests/. Static only; no data/ or third_party/ bodies; node --check on all
  three changed JS files: pass.
- **Findings:** 1 new — 1 must.
  - design 1: **MUST** exceedance.py:339-341+:275-277 (23:15Z snapshot) —
    layer_fn returned IVT mirror-native while the AR mask went
    display-oriented; build_frames ANDed them, so every .ivt sidecar binned
    location A's IVT with location B's mask: on the live 22:24Z asset real AR
    cells bin to class 0 (blank on the new merged-map IVT ramp) with phantom
    bins at antipodal cells; mergedmap.js:325 renders the plane faithfully.
    **FIXED UNDER REVIEW** by the owner's 00:56Z landing — verified: layer_fn
    now returns stack([_to_display(v) …]) and the caller's flip is removed.
    Residual sub-item kept open: ivt_class_plane/ivt_class_bytes still have
    no unit test.
- **Verdict:** the four backend files this stamp newly covered are clean
  (serve allowlist strict, select rewrite equivalent, ar_filter/ar.py
  semantics preserved with the shared-track-screen payoff); the only new
  finding was already fixed by the owner mid-run. Repo is moving fast
  (eventviz re-landed 00:49Z, exceedance 00:56Z) — re-verify before patching.
- **Channels appended:** channel_ponytail_design.md (1, MUST→resolved-in-run).
- **Owners nudged:** design (term_f3c16ead) — live but pty not writable
  (mid-run; two sends refused with terminal_not_writable, one after a 90 s
  wait-submit). Finding stands in the channel; nudge pending — the MUST it
  cites was already fixed by the owner at 00:56Z, so nothing is blocked.
- **Owners offline:** infra-domain (term_987fee64) absent again (10th
  consecutive) — carried purge_dropped_events.py:43-45 MUST (to_parquet still
  in-place, re-read this run), freshness_check.py:101 SHOULD,
  purge_event_parts.py NICE. **Orchestrator attention.**
- **Findings resolved since prior pass:** 7, re-verified by reading code:
  frontend MUST eventviz five-dead-vars (scaffolding re-created;
  node --check), SHOULD lead-chip rebuild (rebuildLayers keeps live layer
  objects; two new round-trip e2e tests), NICE opacity-drag (setOpacity-only
  path :981), NICE zz debug test (file deleted), NICE tierRGB (line deleted);
  design SHOULD AR colorbar legend (restored, 250/400·mask/600-1000 ticks),
  NICE durAlpha-vs-heatRunBin bins (now exact parity, comment + code);
  science NICE sr14f publish-gate inference (reads stage=publish_gate
  records; inference demoted to labeled fallback; owner-acked 21:3xZ).
- **Open totals (channel-verified ~01:00Z):** 13 — frontend 6 (sibling
  00:10Z+00:15Z entries: 2 must / 3 should / 1 nice, play-interval teardown
  leak + strict sidecar-size checks etc.), design 1 (sibling SHOULD:
  unconditional index dur/ivt keys), science-review 2 (sr14f/sr14c ladder
  duplication SHOULD — now materialized: select.py AR floor relaxed 3→1, so
  the next funnel re-run prints stale "landfall_days < 3" rejects for
  1-2-day landfalls production now ships; + sibling 00:10Z SHOULD), ewb 1
  (sibling NICE), infra-domain 3 (1M/1S/1N), mlwp-store 0. My new MUST is
  fixed, not counted.
- **Not checked:** box RAM headroom for scan_ar_events' new ~3 GB step-array
  residency (window-level ivt6/labels6 replace daily-resampled arrays); live
  funnel/curation artifacts; e2e suite not executed; sibling runs' open
  findings not re-reviewed (owner triage in their channels is the source).
## 2026-11-10 00:10 UTC (carried to history) (RUN STAMP 2026-11-09T20:15:31Z)
- **Mode:** INCREMENTAL (14 files). Late sibling: the 22:15Z-stamp run
  completed 23:59Z while this pass was mid-review; its channel entries
  pre-date this one and are not duplicated (the play-interval finding is
  filed here as a severity escalation of its SHOULD, with the cross-event
  URL-corruption mechanism added). Files read in full at pinned snapshots
  (sha256-12 in /home/orca/.hermes/cache/scratch/ponytail_snap_20261109T201531Z/):
  eventviz.js 76125 B (whole single-card overlay rewrite, renderMaps probe
  re-run: both paint paths execute to completion — the 20:40Z
  ReferenceError MUST is resolved), app.js (#lyr/#day hooks :1870-1871,
  syncUrl/locationFor encode round-trip), mergedmap.js diff (getFrame 3-asset
  fetch with one-warn 404 fallbacks, AR cls 1-5→ramp + binary fallback,
  heat plane 0/freeze plane 1, cellIdx LUT, PM-arc bboxPaths, flat RUN_ALPHA
  215 + heatRunBin 3/4/5-6/7-13/14+), styles.css (dur-heat/freeze/ar tokens
  both themes; retired classes unreferenced), exceedance.py sidecar codec
  vs JS decode (layout + bins match), pipeline.py (gate_rejects →
  curation.json — correct; LAST_DECISIONS ordering fine),
  rank_cap_replay.py, sr14_ar_funnel/b/c/d/e/f, test_dashboard_fe.py +
  test_guardian_fe.py diffs (new ramp/dur/overlay tests are real-behavior
  assertions; guardian AR-pixel probe verified theme-consistent with both
  --dur-ar token sets). No third_party/ or data/ read; no EWB compute;
  static review only. eventviz.js was live-edited during the pass (72438→
  76125 B); findings cite the 76125 B snapshot.
- **Findings:** 2 new — 1 must, 1 should.
  - frontend 2: **MUST** eventviz.js:708 vs :1146 play interval survives
    teardown → escalates the 23:59Z SHOULD: after ▶ + close, saveState keeps
    firing window.EventViz.onDay/onLayers, which by then belong to the NEXT
    event's render (app.js rebinds) → 2Hz corruption of that event's
    #day/#lyr URL + pageerrors on removed maps; one-line stopPlay() in
    teardown + play→close e2e. **SHOULD** eventviz.js:905/:912 customized
    starts false on a restored #lyr view → first lead-click clears #lyr from
    the URL while the custom view stays on screen; init from savedLayers.
  - science-review 1: **SHOULD** rank_cap_replay.py:53 validates without
    recompute_merged_max_consec (applied by detect_events between
    rank_and_cap and the gate) → merged events crossing the [1,31]
    plausibility bound flip replay vs production → false exit-1 alarm;
    mirror detect_events or skip merged ids with a note.
- **Verdict:** the overlay rewrite lands clean — the previous frontend
  MUST and 3 carried findings are fixed; the play timer is now the lane's
  only MUST.
- **Channels appended:** channel_ponytail_frontend.md (2, one an
  escalation), channel_ponytail_science_review.md (1).
- **Owners nudged:** frontend (term_55602a39), science-review
  (term_bd4274e6) — both live, send ok.
- **Owners offline:** infra-domain (term_987fee64) absent (11th
  consecutive run). Its 3 carried findings re-read at the cited lines,
  ALL STILL OPEN: purge_dropped_events.py:43-44 (in-place scores.parquet
  rewrite), freshness_check.py:101 (pgrep dot unescaped),
  purge_event_parts.py:49 (fixed-name .tmp). **Orchestrator attention.**
- **Findings resolved since last run:** 7 — the sibling run (23:59Z)
  verified the same set by reading code (frontend 4: renderMaps MUST,
  lead-change layer reset, opacity drag, zz debug test; design 2: AR
  colorbar rebuilt, dur bins mirrored; science-review 1: sr14f reads
  real publish_gate records); this run independently re-verified the
  frontend 4 + sr14f against the 76125 B snapshot before filing.
- **Open totals (channel-verified):** 10 — ewb 1 (1M, ar_filter empty-step
  ValueError), design 1 (1M, .ivt orientation), frontend 4 (1M play
  interval/escalated, 1S #lyr clear this run; 2N tierRGB + AR #fff contour
  carried), infra-domain 3 (carried, offline), science-review 1 (1S this
  run), mlwp-store 0.
- **Not checked:** e2e suites not executed (static review; MUST is a
  deterministic control-flow leak read directly from teardown:708-712 and
  stepDay→saveState); sr14 scripts not re-run on the box (read-only pass);
  test fixtures' data windows (Oct-3 frame retention) not audited; the
  detail-map alpha ramp (115-215) vs front-page flat-215 color-channel
  difference is design's documented choice, not re-filed.
 (carried to history)
## 2026-11-09 23:59 UTC (first pass this stamp) (RUN STAMP 2026-11-09T22:15:13Z)
- **Mode:** INCREMENTAL (19 files). Read in full: eventviz.js (complete
  single-card rewrite), ar_filter.py (new module, plus its consumers
  events/ar.py:244-255 and viz/exceedance.py wiring), exceedance.py diff
  end to end (.run/.ivt/.trk sidecars, window AR pass, flicker gate,
  label_sig, prune, atomic writes), mergedmap.js diff (ramp decode, cellIdx
  LUT, PM-arc bboxPaths, evSpan, legend), app.js (#lyr/#day wiring),
  index.html, styles.css (dur/AR ramp tokens, G-04 grid retirement),
  serve.py (sidecar allowlist), pipeline.py (gate_rejects into
  curation.json), sr14f/sr14-funnel gate-record change, e2e test diffs.
  The two MUSTs were confirmed by reviewer micro-repros against the repo
  code (no pipeline/viz compute, no code edits). CAVEAT: owners kept
  editing mid-run (eventviz 22:18Z, mergedmap 22:25Z, ar_filter 22:27Z,
  all after the stamp); findings cite the ~23:5xZ snapshots.
  test_zz_debug_freeze.py vanished mid-run (deleted by owner — counted
  resolved). sr14a-e/rank_cap_replay unchanged since the clean 20:40Z
  review, not re-read. No third_party/ or data/ read.
- **Findings:** 5 new — 2 must, 1 should, 2 nice.
  - ewb 1: **MUST** ar_filter.py:89-95 track_ar_components raises
    ValueError (empty-sequence argmax) when a fully-empty AR step precedes
    a non-empty step — crash reproduced live; kills the whole day's AR pass
    in detection AND in the raw lane; one-line prev-empty guard + test.
  - design 1: **MUST** exceedance.py:275-277 the .ivt class sidecar mixes
    mirror-native IVT with the display-oriented AR mask then flips again —
    every built .ivt is garbage (AR cells class 0 or neighbour's IVT);
    mergedmap's binary fallback hides it, so the shipped IVT ramp silently
    never works (reproduced: IVT-900 AR cell writes class 0). Fix must
    bump LAYER_REV or delete *.ivt, else bad sidecars never rebuild.
  - frontend 3: **SHOULD** eventviz.js:1146 play setInterval survives
    teardown — every play+close leaks a 500 ms PNG-reencode loop in the
    long-lived tab; **NICE** tierRGB :530 still dead (carried);
    **NICE** AR obs default contour #fff invisible on the light basemap
    (legacy path used theme ink).
- **Verdict:** two silent-data MUSTs on the v3 raw-map feature — fix the
  .ivt orientation before the ramp feature is called done; ar_filter's
  empty-frame crash is one line.
- **Channels appended:** channel_ponytail_ewb.md (1),
  channel_ponytail_design.md (1), channel_ponytail_frontend.md (3).
- **Owners nudged:** ewb (term_e87b37a0), frontend (term_55602a39),
  design (term_f3c16ead) — all live, send ok. science-review: no findings,
  not nudged.
- **Owners offline:** infra-domain (term_987fee64) absent (10th
  consecutive run). Its 3 carried findings re-read at the cited lines,
  ALL STILL OPEN: purge_dropped_events.py:43-44 (in-place scores.parquet
  rewrite), freshness_check.py:101 (pgrep dot unescaped),
  purge_event_parts.py:49 (fixed-name .tmp). **Orchestrator attention.**
- **Findings resolved since last run:** 7, re-verified by reading code:
  frontend 4 — the renderMaps ReferenceError MUST (scaffolding toolbar/
  cbRow/obsBlock/box/chip re-created at :583-695), the lead-change layer
  reset (tuned map :871/:888 keeps user edits across leads), opacity drag
  now nudges overlays instead of re-encoding PNGs (:978-983),
  test_zz_debug_freeze.py deleted; design 2 — AR detail colorbar with the
  400-threshold tick rebuilt (:591-615), detail durAlpha bins now mirror
  the front page per type (heat 3/4/5-6/7-13/14+, freeze 1/2/3/4-6/7+ —
  mergedmap.js heatRunBin/freezeRunBin read side by side);
  science-review 1 — sr14f now reads the real publish_gate records from
  curation (inference kept only as no-record fallback).
- **Open totals (channel-verified):** 8 — ewb 1 (1M this run), design 1
  (1M this run), frontend 3 (1S/2N this run, tierRGB carried),
  infra-domain 3 (1M/1S/1N carried, offline), science-review/mlwp-store 0.
- **Not checked:** e2e suite not executed (static review; the two MUSTs
  are deterministic regardless); ivt6's orientation is established from
  the shared EWB call the old code flipped + the micro-repro, not from a
  live build artifact; run_recurrence daily-rule wording vs
  channel_consecutive_days §2 not re-audited (codec test covers it);
  sr14b/d/e internals (unchanged files).


## 2026-11-09 20:45 UTC (carried to history) (RUN STAMP 2026-11-09T18:15:13Z)
- **Mode:** INCREMENTAL (14 files; the 17:17Z worklist's 12 plus rank_cap_replay.py
  and test_zz_debug_freeze.py, which that stamp's later pass had not listed).
  CAVEAT: dashboard/eventviz.js was rewritten live under review (worklist saw
  18:24Z; owner re-landed initialSel/anchorDay/ui at 20:08Z) — findings cite the
  20:08Z snapshot; a stamp-17:17 sibling run (report entry 19:14Z) completed
  concurrently; nothing here duplicates its channel entries. Read in full:
  eventviz.js (both live snapshots + the deleted block via `git show HEAD:` for
  the fix), app.js detail wiring (locationFor/hashParams verified: #lyr/#day
  encode/decode round-trip correct), mergedmap.js duration-ramp diff (index.dur
  gate, .run 404 one-warn fallback, heat plane=0/freeze=1, cellIdx LUT,
  eastward-arc bboxPaths, end-exclusive evSpan — coherent, no findings),
  styles.css dur tokens (light+dark, legacy .ev-* fallback styles still used),
  pipeline.py curation.json move + gate_rejects, rank_cap_replay.py (Event
  round-trip lossless, rank_and_cap deterministic, DEFAULT_TOP_N=15 matches the
  run — clean), sr14a-f/sr14e (floors mirror _passes_floor incl. .days
  truncation; read-only triage), test_dashboard_fe.py diff, zz debug test.
  No third_party/ or data/ read; no EWB compute; static review only.
- **Findings:** 5 new — 1 must, 2 should, 2 nice.
  - frontend 2: **MUST** eventviz.js renderMaps references five deleted-and-
    never-recreated variables (cbRow :581, obsBlock :678, box :690/:1067/:1070,
    chip :1056, toolbar :985-1046) → any event detail open throws ReferenceError
    at `host.appendChild(cbRow)`, app.js catch paints "viz asset failed", the
    map never renders (node --check passes; runtime crash proven by scope audit
    against both snapshots); fix: re-create the scaffolding, box/chip IIFEs
    recoverable from `git show HEAD:dashboard/eventviz.js:539-611`. **NICE**
    tierRGB computed never read (:526).
  - design 2: **SHOULD** AR detail lost its IVT legend: cbRow unconditionally
    display:none (:582) while the 250–1000 blue ramp is still painted (:884) →
    intensity uninterpretable, no ≥400 threshold marker; **NICE** detail heat
    bins (durAlpha 1/2/3/4-6/7+, comment claims front-page parity) vs front-page
    heat bins (3/4/5-6/7-13/14+) → a run=1–2 cell paints in detail, blank on
    home; contract §5 (1/2/3/…) and the mergedmap tip (min 3) also disagree —
    design to pick one binning.
  - science-review 1: **NICE** sr14f_report.py:81 infers "publish gate:
    peak_land_pixels < 500" for candidates with no decision record; the
    pipeline now records the real reject problem in curation.json
    (stage=publish_gate) — read it, keep inference as fallback.
- **Verdict:** frontend's MUST blocks every event detail page while it stands;
  mergedmap, pipeline curation.json change and rank_cap_replay verified clean.
- **Channels appended:** channel_ponytail_frontend.md (2),
  channel_ponytail_design.md (2), channel_ponytail_science_review.md (1).
- **Owners nudged:** frontend (term_55602a39), design (term_f3c16ead),
  science-review (term_bd4274e6) — all live, send ok.
- **Owners offline:** infra-domain (term_987fee64) absent (sibling run: 8th,
  this run: 9th consecutive). Its carried findings re-read at the cited lines:
  purge_dropped_events.py:43-44 (in-place scores.parquet rewrite),
  purge_event_parts.py:49 (fixed-name .tmp) and freshness_check.py:101
  (pgrep dot unescaped) all STILL OPEN; its earlier ponytail_review.py glob
  MUST is now fixed in code. **Orchestrator attention.**
- **Findings resolved since last run:** 4, re-verified by reading the code:
  hardcoded-0.72 opacity probe replaced by the |60| round-trip assertions
  (frontend); styles.css:712 dead :has() rule deleted (design, owner-acked);
  sr14c_cand.py curation.json guard + sr14f ladder-dedup fixed in-run
  (science-review, owner-acked 20:3xZ); #lyr= deep-link fully wired (sibling
  run credited the landing; re-verified here). test_zz_debug_freeze.py —
  flagged by the sibling run — is still on disk (counted open).
- **Open totals (channel-verified):** 13 — frontend 5 (1M/1S/3N: this run's
  MUST + NICE; carried lead-chip rebuild SHOULD, opacity-drag repaint NICE,
  zz debug test NICE), design 2 (1S/1N, this run), science-review 3 (2S
  carried funnel findings + 1N this run), infra-domain 3 (1M/1S/1N:
  purge_dropped_events :43-44 MUST, freshness_check :101 SHOULD,
  purge_event_parts NICE; the ops/ponytail_review.py:82 glob MUST is now
  RESOLVED — :82 uses parts[0]-membership with the explanatory comment).
  ewb, mlwp-store 0.
- **Not checked:** e2e suite not executed (static review only; the MUST is a
  deterministic ReferenceError regardless); eventviz.js state at patch time
  (owner still editing — re-verify before fixing); whether the heat-bin
  divergence is a signed design decision (no channel record either way); the
  box's live funnel output.


## 2026-11-09 18:05 UTC (carried to history) (RUN STAMP 2026-11-09T17:17:13Z)
- **Mode:** INCREMENTAL. Files: dashboard/app.js, dashboard/eventviz.js,
  dashboard/mergedmap.js, dashboard/styles.css, scripts/review/sr14_ar_funnel.py,
  sr14b_maps.py, sr14c_cand.py, sr14d_candmaps.py, sr14e_replay.py,
  sr14f_report.py, tests/e2e/test_dashboard_fe.py, test_zz_debug_freeze.py.
  Connected code read: app.js selectEvent/syncUrl/popstate (:1755-1795,
  :2021-2155), mergedmap bboxPaths/evSpan/duration planes, eventviz layer-state
  closure end to end, select.py rank_and_cap/merged_from signatures, ar.py
  details keys, sr14a funnel-JSON keys vs sr14f's reads, Leaflet vendor
  createPane/getPane. CAVEAT: owners edited during the run (addImg pane fix
  landed 17:15; sr14c guard fix 17:5x) — findings cite the ~18:00Z state.
  A stale-#lyr bleed across event switches was checked and DROPPED: every
  selectEvent-with-other-id path with a dirty URL is hidden behind the detail
  view, so it is unreachable in-app.
- **Findings:** 6 new — 0 must, 3 should, 3 nice.
  - frontend 3: **SHOULD** eventviz.js:735,861 lead-chip click rebuilds the
    Observed entry from defaults → user opacity/colour edits silently reset
    (same loss for models absent at the clicked lead); **NICE** eventviz.js:893
    opacity slider re-encodes the raster PNG per tick (setOpacity suffices);
    **NICE** test_zz_debug_freeze.py committed scratch debug, asserts nothing,
    boots Chromium every e2e pass.
  - science-review 2: **SHOULD** sr14c_cand.py:13,21 missing-curation.json
    guard defeated one line later → IndexError, whole artifact lost (owner
    APPLIED within the run; verified fixed at :21-22, not carried);
    **SHOULD** sr14f_report.py:42-81 reject-reason ladder hand-duplicates
    sr14c's thresholds → mislabelled funnel.md table on next threshold edit.
  - design 1: **NICE** styles.css:712-714 dead `:not(:has(.ev3-grid ...))`
    rule (ev3-grid no longer exists; max-width:none already applied at :669).
- **Verdict:** no blockers; the lead-change layer reset is the one user-visible
  bug in the shipped layer-state feature (untested interaction).
- **Channels appended:** channel_ponytail_frontend.md (3),
  channel_ponytail_science_review.md (2), channel_ponytail_design.md (1).
  Stray hyphen-name science-review channel file created by this run merged
  into the underscore file and removed.
- **Owners nudged:** frontend (term_55602a39), design (term_f3c16ead),
  science-review (term_bd4274e6) — all live, send ok.
- **Owners offline:** infra-domain (term_987fee64) absent from roster (7th
  consecutive run). Its 3 carried findings re-read at the cited lines, ALL
  STILL OPEN: purge_dropped_events.py:43-44 (scores.parquet rewritten in
  place), purge_event_parts.py fixed-name .tmp, freshness_check.py:101
  (pgrep dot unescaped). **Orchestrator attention.**
- **Findings resolved since last run:** 5 — frontend 2 (lyr deep-link now
  wired app.js:1866; stale opacity test probe replaced), design 2 (640px
  Observed cap removed; dead selectors removed — the one surviving :has rule
  re-filed as NICE), science-review 1 (sr14a dateline lon wrap fixed as
  specified and funnel re-run). Plus 1 filed-and-fixed within this run (sr14c).
- **Open totals:** 8 (infra-domain 1M/1S/1N; frontend 1S/2N; science-review
  1S; design 1N; ewb, mlwp-store 0).
- **Not checked:** e2e suite not executed here (static review only; frontend
  runs it on the box); mergedmap date-line top-5 map crops (sr14b) would
  render full-globe for a crossing track — diagnostic-only cosmetic, not
  filed; live runtime appearance; sr14f output on the box not inspected.


## 2026-11-09 12:16 UTC (carried to history) (RUN STAMP 2026-11-09T12:16:13Z)
- **Mode:** INCREMENTAL (same 2 files as 11:19Z — the run reviewed the
  owner fixes applied after 11:15Z). Read in full: viz/exceedance.py
  (653 lines: had_frames snapshot :482-489, label-selected day slices
  :273-274/:295-296, times_all seed guard :627, flicker gate :570-588,
  build/prune/index flow re-walked end to end) and
  scripts/review/d2_pixel_audit.py (99 lines: coverage guard :43-48,
  labels_from_bytes import :24/:84, crop + wrap-invariant haversine,
  offender reporting :79-94). Connected code re-read: tc_screen.py
  (KM/HOURS env constants :49-51, load_ibtracs_tracks None-paths),
  weekly.py:209-226 (exceedance = non-fatal subprocess, 5400 s timeout),
  ar_diff.py (ledger is passed explicitly by design, so a flicker-abort
  week cannot mis-attribute — ledger-before-gate NOT filed), owner
  channel acks (design 11:30Z, science-review 11:3xZ both applied per
  spec). Infra carry re-read at cited lines. No third_party/ or data/
  read; no EWB compute; static review only.
- **Findings:** 3 new — 0 must, 0 should, 3 nice.
  - design 1: **NICE** exceedance.py:570-588 — flicker gate runs after
    the ~256-day `_daily_runs` (:543): a flicker raise pays minutes of
    recurrence compute per weekly attempt for nothing. Fix: move the
    gate block to right after layer_fn (:534) — same fail-closed
    position, all inputs come from _ar_window_pass.
  - science-review 2: **NICE** d2:46 — `times.max()` on empty usable
    points (empty/header-only IBTrACS cache) → opaque zero-size-array
    traceback instead of the file's own refusal message (still exits
    non-zero, no false CLEAN). Fix: 2-line `not len(times)` SystemExit.
    **NICE** d2:86,94 — "total distinct within-buffer tracks" counts
    distinct (track, frame, storm-point) strings: one track in the
    buffer across several frames inflates the headline count (~6x for a
    week-long lingerer). Fix: count a set of track labels.
- **Verdict:** no musts/shoulds — both 11:19Z fixes verified correct and
  complete; what's left is message-quality and wasted-work residue.
- **Channels appended:** channel_ponytail_design.md,
  channel_ponytail_science_review.md.
- **Owners nudged:** design (term_f3c16ead), science-review
  (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — 3 carried items
  re-verified open by reading the cited lines (purge_dropped_events.py
  :43-44 scores.parquet still rewritten IN PLACE — sibling
  purge_event_parts.py now shows the intended backup+tmp+os.replace
  pattern; freshness_check.py:101 pgrep dot still unescaped;
  purge_event_parts.py:49 fixed-name .tmp), **orchestrator attention**.
- **Findings resolved since last run:** 3 of 3 reviewable — design's
  stale-mirror fresh-index NICE (exceedance.py:627 ternary, accepted
  first option) and science-review's stale-cache SHOULD (d2:45-48, the
  exact 3-line guard; science-review verified on box: CLEAN 503.4 km,
  336 frames, guard inert on fresh data) + decode_trk NICE (deleted,
  labels_from_bytes imported). All verified by reading the files.
  Infra: 0 of 3.
- **Open totals:** 6 (design 0M/0S/1N; science-review 0M/0S/2N; infra
  1M/1S/1N; ewb, frontend, mlwp-store 0).
- **Not checked:** whether the weekly full build still fits the 5400 s
  weekly.py:220 subprocess timeout now that _daily_runs recomputes the
  trimmed 256-day history EVERY build (static review can't measure; if
  it doesn't fit, weekly kills the lane and the raw map silently freezes
  at its last index — the one scale risk this round); whether the box's
  v5 weekly actually runs D2 in-lane (the 503.4 km CLEAN is
  science-review's self-report); _storm_pass cos-lat crop clamp under
  |lat|>~78 deg carried unfiled (d2's own crop full-width catches it
  loud).

## 2026-11-09 11:19 UTC (carried to history) (RUN STAMP 2026-11-09T11:15:13Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (652 lines —
  post-10:16Z state: had_frames bail, label-selected day slices, SCHEMA-2
  tracks/labels flow, flicker gate, run_recurrence, prune/index flow) and
  scripts/review/d2_pixel_audit.py (98 lines). Connected code read:
  events/ar_filter.py + events/tc_screen.py in full (screen gates vs d2
  gates: +6 h step gate and <=KM both supersets of d2's ±24 h — gate
  alignment re-verified, crop coverage OK incl. the 0-360 lon haversine
  wrap-invariance proof); weekly.py:209-224 (exceedance runs as a
  subprocess, non-fatal — flicker raise cannot kill the scorecard);
  dashboard/mergedmap.js frame loader (reads .bin/.run/dur only — tracks{}/
  .trk unconsumed so far, schema edge not live); tests/test_raw_exceedance.
  py (225 lines: codec, incremental, flicker, recurrence incl. u8 cap and
  seeding, backfill+prune all tested). Ownership guess noted: d2_pixel_
  audit.py = scripts/ default infra, kept with science-review by the
  10:16Z precedent (same audit lane). No third_party/ or data/ read; no
  EWB compute; static review only.
- **Findings:** 3 new — 0 must, 1 should, 2 nice.
  - science-review 2: **SHOULD** d2_pixel_audit.py:46-56,86-89 — stale
    IBTrACS cache (by-design fallback at tc_screen.py:110-115, or IBTrACS
    publication lag) leaves the newest frames with no near storm points;
    they are `continue`d and D2 prints CLEAN over an unaudited window tail
    — certifying the exact TC-buffer violation this gate exists to catch.
    Fix: 3-line coverage assertion (max storm time vs last frame - 3 d).
    **NICE** d2:29-30 — decode_trk duplicates exceedance.
    labels_from_bytes; the documented .trk widening path would fix one and
    silently garble the other. Import the sibling instead.
  - design 1: **NICE** exceedance.py:626 — stale mirror + fresh index dir:
    times_all empty -> fmt(times_all[0]) IndexError in the weekly log
    instead of a clean skip (the had_frames bail covers only
    published-frames; crash lands before any publish, no data risk).
    One-line `if times_all else None`.
- **Verdict:** no musts; one theme — gates that pass when their own input
  data is missing.
- **Channels appended:** channel_ponytail_design.md,
  channel_ponytail_science_review.md.
- **Owners nudged:** design (term_f3c16ead), science-review
  (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — 3 carried items
  (purge_dropped_events.py in-place scores.parquet rewrite MUST;
  ops/freshness_check.py:101 pgrep dot; purge_event_parts.py:49
  fixed-name pair_status tmp), **orchestrator attention**.
- **Findings resolved since last run:** 3 of 3 reviewable — design's two
  10:16Z SHOULDs (had_frames snapshot exceedance.py:479-482/:523;
  label-selected day slices :273-274 and :295-296, both callers) and
  science-review's year-staleness SHOULD (d2:42 computes IBTrACS years
  from frame tags +/-1). All verified by reading the current files.
  Infra: 0 of 3 (files not in this worklist, handles unchanged).
- **Open totals:** 5 (design 0M/0S/1N; science-review 0M/1S/1N; infra
  1M/1S/1N; ewb, frontend, mlwp-store 0).
- **Not checked:** whether the box's live IBTrACS cache is actually fresh
  (static review; the D2 finding stands regardless of today's cache
  state); flickering_tracks cannot fire under the current ar_filter
  linking rules (a gapped id can never reappear) — judged defensive
  belt, not dead-code filed; _storm_pass cos-lat 0.2 crop clamp under
  500 km at |lat|>~78 deg carried unfiled from 10:16Z (d2 catches it
  loud).

## 2026-11-09 10:16 UTC (carried to history) (RUN STAMP 2026-11-09T10:16:13Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (644 lines —
  build/gate/prune flow, new empty-AR bail, layer_fn/_daily_runs slicing,
  run_recurrence math + u8 saturation probe), scripts/review/
  d2_pixel_audit.py (98 lines: empty-window guard, crop math, ±24 h gate,
  0-360 lon handling), scripts/review/sr12_tropical_audit.py (183 lines:
  grid/band decode, per-year IBTrACS ±1, _ov replica, score reconcilers).
  Connected code read: events/ar_filter.py + events/tc_screen.py in full
  (load_ibtracs_tracks SEASON filter, _storm_pass +6 h gate). EXTERNAL
  INPUT VERIFIED live: EWB inputs.IBTRACS_URI = official NOAA
  ibtracs.ALL.list.v04r01.csv (333 MB; range-fetched header + tail) —
  SEASON values are plain integers, and southern-hemisphere storms carry
  the ENDING year (2026 ISO rows with SEASON 2027 confirmed in data).
  Carried infra files re-read at the cited lines. TESTS EXECUTED offline:
  test_raw_exceedance + test_ar_tropical_filter + test_viz_unit = 18
  passed (up from 15); 3 worklist files py_compile clean. No
  third_party/ or data/ read, no EWB compute, no box runs.
- **Findings:** 3 new — 0 must, 3 should, 0 nice.
  - design 2: **SHOULD** exceedance.py:515 — the empty-AR bail reads
    index["frames"] AFTER the LAYER_REV/--force wipes (:478-484): a
    rev-bump or --force build that hits the all-AR-fail/stale-mirror path
    takes the fresh-index branch and publishes frames:[] + null window —
    the blank-published-index failure the :516-521 guard exists to
    prevent, narrowed to rebuild runs. Fix: snapshot had_frames before
    the wipes, gate on it.
    **SHOULD** exceedance.py:270-272+:292-293 — heat/freeze masks are
    taken POSITIONALLY from the day slice while the file's own hour
    filters (:493, :533) treat non-6h mirror steps as possible: one 03Z
    step in the mirror and steps 2..4 are computed from 03Z/06Z/09Z,
    labeled 06Z/12Z/18Z with climatology at the wrong hour — silently
    wrong pixels and .run durations, no crash (AR is label-safe). Fix:
    label-select the exact day_times list in both callers.
  - science-review 1: **SHOULD** d2_pixel_audit.py:42 — hardcoded
    `| {2025, 2026, 2027}` IBTrACS seasons go stale as the window rolls:
    a Nov-Dec-2027 southern-hemisphere storm carries SEASON 2028 (verified
    convention), falls out of the gate's input, and d2 certifies CLEAN
    with AR pixels inside its 500 km buffer. Fix: years = frame year ±1,
    as sr12:78 already does.
- **Verdict:** no musts; all three are one theme — gates and slices that
  were correct for THIS week's data, each one line from being
  date/shape-proof.
- **Channels appended:** channel_ponytail_design.md,
  channel_ponytail_science_review.md.
- **Owners nudged:** design (term_f3c16ead), science-review
  (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — 3 carried items
  re-verified open (purge_dropped_events.py:43-44 in-place
  scores.parquet rewrite MUST; ops/freshness_check.py:101 pgrep dot;
  purge_event_parts.py:49 fixed-name pair_status tmp), **orchestrator
  attention**.
- **Findings resolved since last run:** both 09:15Z SHOULDs — design's
  empty-pass blank-publish (exceedance.py:515-524 bail now returns
  instead of publishing; narrower residual filed above) and
  science-review's vacuous D2 CLEAN (d2:37-38 + sr12:41-42 empty-frames
  SystemExit), verified by reading the files. Infra: 0 of 3.
- **Open totals:** 6 (design 0M/2S/0N; science-review 0M/1S/0N; infra
  1M/1S/1N; ewb, frontend, mlwp-store 0).
- **Not checked:** whether the box's post-v5 weekly has actually run D2
  with a non-empty window (static review; local test run stands in);
  _storm_pass's cos-lat 0.2 crop clamp under-covers 500 km at |lat|>~78°
  — judged loud-fail (d2's own crop is full-width), not filed; sr12's
  events bbox layout trust carried from the 07:19Z read.

## 2026-11-09 09:15 UTC (carried to history) (RUN STAMP 2026-11-09T09:15:13Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (v5+ empty-pass /
  label_sig / gate flow), scripts/review/d2_pixel_audit.py (post-fix),
  sr12_tropical_audit.py, sr13_flicker.py. Connected code read: pipeline.
  _sweep_orphan_temps glob + pathlib dotfile probe (a dot-prefixed tmp
  candidate finding was DISPROVED and dropped — glob finds dot files,
  matching design's earlier ack), tests/test_raw_exceedance.py read_presence
  usage (kept-for-tests, not dead), purge_dropped_events.py:43-44,
  purge_event_parts.py:49, ops/freshness_check.py pgrep site (carried infra
  items re-verified). TESTS EXECUTED offline: test_raw_exceedance +
  test_ar_filter = 15 passed; 4 files py_compile clean. Empty-pass blanking
  mechanism probed: label_sig(np.zeros(1)) differs from any real remap sig;
  layer_fn over an empty AR pass KeyErrors the first day_times. No
  third_party/ or data/ read, no EWB compute, no box runs.
- **Findings:** 2 new — 0 must, 2 should, 0 nice.
  - design 1: **SHOULD** exceedance.py:399-400+538-542 — all-days AR fail
    (or stale mirror, times==[]) now returns the 08:16Z-introduced
    _empty_pass; its degenerate label_sig wipes index["frames"],
    build_frames then KeyErrors every day (empty pos_of) and writes zero
    frames, and build() publishes frames:[] with window null — a transient
    AR failure blanks the live raw-map window for up to a week despite all
    frame data being on disk. Fix: return early (keep last good index) when
    the AR pass is empty and the cached index has frames.
  - science-review 1: **SHOULD** d2_pixel_audit.py:36+84-87 — empty
    index["frames"] → worst_km stays inf → "D2 CLEAN (min = inf km,
    frames=0)" exit 0: the post-rebuild v5 certification gate passes
    vacuously on the blank index the finding above can publish. Fix:
    SystemExit when frames list is empty.
- **Verdict:** no musts. The two SHOULDs are one fault line: 08:16Z's
  empty-pass fix turned crash-and-keep-index into blank-and-publish, and
  D2 — the gate that certifies the fix train — cannot see an empty window.
- **Channels appended:** channel_ponytail_design.md,
  channel_ponytail_science_review.md.
- **Owners nudged:** design (term_f3c16ead), science-review
  (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — carried MUST
  (purge_dropped_events.py:43-44 in-place scores.parquet rewrite,
  re-verified open) + carried SHOULD (ops/freshness_check.py pgrep
  unescaped dot, re-verified open) + carried NICE (purge_event_parts.py:49
  fixed-name pair_status.tmp, re-verified open), **orchestrator attention**.
- **Findings resolved since last run:** all 9 from the 08:16Z run,
  verified by reading the files: design's planned-set fix (:553-557,
  ar_times-only, unseen-not-fatal), --days cap only build_times (:500-501,
  full-window AR pass at :513), _empty_pass on all-days-fail (:399-400);
  science-review's d2 frame_layers[2] AR-plane decode (:55), d2 lon grid
  −180..180 (:39, haversine verified convention-safe), sr12 IBTrACS raise
  (:78-80), sr13 in-loop decode (:22-24) + dead-chain nulling (:71-73),
  sr13 dead line removed. infra's NICE (purge_event_parts) NOT fixed.
- **Open totals:** 5 (design 0M/1S/0N; science-review 0M/1S/0N; infra
  1M/1S/1N; ewb, frontend, mlwp-store 0).
- **Not checked:** whether today's v5 rebuild on the box actually ran D2
  with a non-empty window (static review; the vacuous-PASS path is code-
  verified, not observed); sr13's chain-growth O(chains×frames) cost on
  the 360-frame window judged fine, not measured.

## 2026-11-09 08:16 UTC (carried to history) (RUN STAMP 2026-11-09T08:16:13Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (626 lines, v5
  build: SCHEMA 2, .trk labels, remap/ledger, pre-build flicker gate,
  tracks{} spans), scripts/review/d2_pixel_audit.py, purge_event_parts.py,
  sr12_tropical_audit.py, sr13_flicker.py. Connected code read:
  events/ar_filter.py in full (track_ar_components link rules — children
  link only to directly-consecutive steps, so a one-step component gap
  re-links as a NEW sid → the build-time flicker gate structurally cannot
  see a physical blink; ar_filter_tracks labels are dense 1-based, matching
  exceedance's remap loop), pipeline._part_path/_sweep_orphan_temps glob +
  pair_status key + sidecar naming (purge_event_parts' globs and paths all
  match the pipeline's real names ✓), freshness_check pgrep site,
  dashboard JS (no .trk/tracks consumer yet → schema-vs-tracks concerns
  moot). TESTS EXECUTED offline: test_raw_exceedance + test_ar_filter =
  15 passed; 4 review scripts py_compile clean. d2 MUST REPRODUCED
  locally: d2's decode_bin on canonical frame_bytes output → ValueError
  crash (compressed frame < plane size); on a frame compressing larger than
  the plane → ~519k phantom pixels matching no layer. No third_party/ or
  data/ read, no EWB compute, no box runs.
- **Findings:** 9 new — 3 must, 3 should, 3 nice.
  - design 3: **MUST** exceedance.py:548-551 — flicker gate's `planned`
    includes stale frames on disk whose AR day failed THIS build (the
    `.bin exists` branch), presence (ar_times-only) can never fill them →
    every spanning track reads as dropout → RuntimeError EVERY build,
    lane freezes up to 90 days; fix = drop the branch (one line).
    **SHOULD** :486-488+500 — manual `--days N` runs the AR window pass on
    the capped window → label_sig wipe → published window shrinks to N
    days for a week; fix = cap only build_frames' time list.
    **NICE** :392 — all-days-fail → np.concatenate([]) ValueError, the
    exact failure the :361 guard's comment names; fix = empty-`ms` return.
  - science-review 5: **MUST** d2_pixel_audit.py:33-36 — decode_bin never
    decompresses and reads the HEAT plane, not AR: crashes on real frames
    (reproduced) or audits garbage → the v5 exit-0 gate cannot verify v5;
    fix = frame_layers(...)[2]. **MUST** d2:49 — lon grid 0..360 vs the
    frame's −180 col-0: all distances measured 180° off; fix = linspace
    (-180,180). **SHOULD** sr12:78-79 — IBTrACS unavailable → silent
    `continue` → false PASS (d2 itself raises); fix = raise.
    **SHOULD** sr13:23-26+50 — all frame masks + all chain masks retained →
    ~1 GB RSS on the 90-day window; fix = decode in-loop + free chains
    older than t−2. **NICE** sr13:18 dead `rs` line.
  - infra 1 (ownership guess noted — scripts/ default; sibling
    purge_dropped_events precedent): **NICE** purge_event_parts.py:49-50 —
    `pair_status.tmp` fixed name matches no sweep glob → orphan forever on
    kill; fix = `.tmp.<pid>` convention.
- **Verdict:** 3 musts. d2_pixel_audit is a dead gate in its current state
  (crash-or-garbage + 180° grid); design's MUST freezes the raw-map lane on
  a transient AR failure the code itself documents as routine.
- **Channels appended:** channel_ponytail_design.md,
  channel_ponytail_science_review.md, channel_ponytail_infra.md.
- **Owners nudged:** design (term_f3c16ead), science-review
  (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — carried MUST
  (purge_dropped_events.py:43-44 in-place scores.parquet rewrite,
  re-verified open) + carried SHOULD (ops/freshness_check.py:101 pgrep
  dot, re-verified open) + new NICE, **orchestrator attention**.
- **Findings resolved since last run:** all 8 from the 07:19Z run,
  verified by reading the files: design's --days prune guard (:570) +
  window-from-frames (:576) + frame_bytes level param dropped; sr12's
  timestamp overlap (:129-133), blocked haversine (:49-59), widened TC gate
  (:81); sr13's speck filter (:36-39), hoisted dilation (:56), dead line
  removed. (science-review acked 6/6 in its channel; design's fixes
  present in file.)
- **Open totals:** 11 (design 1M/1S/1N; science-review 2M/2S/1N; infra
  1M/1S/1N; ewb, frontend, mlwp-store 0).
- **Not checked:** whether today's on-box v5 rebuild has actually been
  d2-gated (local reproduction stands in for the box run — static-review
  rule); sr13 RSS figures are mask-count arithmetic, not measured; the
  sid-split blink claim is read from track_ar_components' link rules —
  sr13's overlap chains remain the ground-truth detector.

## 2026-11-09 07:19 UTC (carried to history) (RUN STAMP 2026-11-09T07:15:46Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (619 lines — all four
  06:16Z fixes, build/prune/window flow, --days path, sidecar writers),
  scripts/review/sr12_tropical_audit.py, scripts/review/sr13_flicker.py.
  Connected code read: events/ar_filter.py (track-level screen + _storm_pass
  +6 h gate, no size floor on tracks), events/select.py:242-257
  (_overlaps/_bbox_intersects authoritative timestamp rule), tc_screen.py
  constants/load fn, pipeline._sweep_orphan_temps glob, weekly.py:209-224 +
  viz/__main__.py:93-96 callers (no --days in any automated path), events JSON
  bbox layout. TESTS EXECUTED offline: test_raw_exceedance + test_ar_filter =
  15 passed; sr12/sr13 py_compile OK; pathlib-glob dotfile probe (design's ack
  verified true). No third_party/ or data/ read, no EWB compute, no box runs.
- **Findings:** 8 new — 0 must, 5 should, 3 nice.
  - design 2: **SHOULD** exceedance.py:566-570 — `--days N` (documented
    backfill mode) prunes PUBLISHED frames older than the cap and rewrites
    index window to the capped start: a manual `--days 7` deletes ~83 days of
    live map history until next weekly. Fix: prune only when `not days`, and
    derive window from index["frames"] (also fixes window.end claiming a frame
    whose AR day failed). **NICE** :73 — unused `level` param on frame_bytes.
  - science-review 6 (sr12/sr13 routed here, scripts/review precedent + these
    are the reviewer's own Section-I tools; guess noted): **SHOULD** sr12:127-131
    _ov compares calendar dates while select._overlaps compares timestamps —
    same-day partial overlap = forbidden pair reported clean (false PASS).
    **SHOULD** sr12:49-55 full T×P haversine matrix — peak-season frame
    (~500k px × ~400 TC pts) ≈ several GB of float64 intermediates → box OOM;
    chunk pixels. **SHOULD** sr13:31-35 objs() keeps sub-50px specks and every
    chain holds a 1 MB full-grid mask for the whole run → multi-GB RSS +
    quadratic dilations on a ~360-frame window; filter by bincount size.
    **NICE** sr13:55 loop-invariant dilation recomputed per candidate object.
    **NICE** sr13:70 dead `active` line. **NICE** sr12:77 audit TC gate 24 h
    vs detector 30 h — the (24,30] band can never fire.
- **Verdict:** no musts. design's fix is two lines in build(); the three
  science-review SHOULDs are what keep the Section-I audit from false-PASSing
  or OOMing on the box.
- **Channels appended:** channel_ponytail_design.md; channel_ponytail_science_review.md (created).
- **Owners nudged:** design (term_f3c16ead), science-review (term_bd4274e6) — both live, send accepted.
- **Owners offline:** infra-domain (term_987fee64) — carried MUST
  (purge_dropped_events.py:43-44 in-place non-atomic scores.parquet rewrite,
  re-verified open) + SHOULD (ops/freshness_check.py:101 unescaped pgrep dot,
  re-verified open), **orchestrator attention**.
- **Findings resolved since last run:** all four 06:16Z design findings —
  planned-set filter (:545-549), _ar_window_pass empty early-return
  (:359-366), _write_atomic at all three frame writers (:210/213/215),
  run_seed trim (:514-515) — verified in the file; owner's pathlib-glob ack
  independently confirmed.
- **Open totals:** 11 (design 0M/1S/1N; science-review 0M/3S/3N; infra 1M/1S;
  ewb, frontend, mlwp-store 0).
- **Not checked:** build()/sr scripts run on the box (static review only —
  AR-pixel and IBTrACS-point counts in the OOM findings are worst-case
  estimates, current-window runs did not crash); JS decoder behavior on a
  shrunk index window; whether any operator doc outside this repo recommends
  --days (the CLI help itself does).

## 2026-11-09 05:17 UTC (carried to history) (RUN STAMP 2026-11-09T05:17:13Z)
- **Mode:** INCREMENTAL. Read in full: viz/exceedance.py (label_sig fix,
  t_lo trim, _sweep_orphan_temps call, tracks{} span block, run/label
  sidecar flow), tests/test_raw_exceedance.py (8 tests, new
  label_sig test). Connected code read: pipeline._sweep_orphan_temps glob
  (*.tmp.* matches index.json.tmp.<pid>, age-guard keeps live tmp),
  scoring/truth_mirror.py (mirror is exactly 6-hourly 00/6/12/18Z),
  events/ar_filter.py, serve.py allowlist.
  TESTS EXECUTED offline: test_raw_exceedance = 8 passed.
- **Findings:** 2 new — 0 must, 1 should, 1 nice (design).
  - design 1: **SHOULD** exceedance.py:506-515 — flicker gate raises after
    build_frames wrote every pending frame but before index.json: a real
    blink freezes the lane AND every weekly run rewrites the whole window
    first. Fix: compute presence from in-memory keep6/labels6/ar_times and
    gate BEFORE build_frames.
    **NICE** exceedance.py:527-531 — index["tracks"] never cleared when a
    build's screen drops every track (stale tooltips); fix: else pop.
- **Channels appended:** channel_ponytail_design.md.
- **Owners nudged:** design (term_f3c16ead) — live.
- **Owners offline:** infra-domain (term_987fee64) — carried MUST
  (purge_dropped_events.py:43-44) + SHOULD (ops/freshness_check.py:101),
  **orchestrator attention**.
- **Findings resolved since last run:** design MUST (label_sig numpy
  json.dumps), design SHOULD (_daily_runs trim), design SHOULD (tmp
  sweep) — all fixed, owner acked.
- **Open totals:** 3 (design 0M/1S/1N; infra 1M/1S).

## 2026-11-09 04:15 UTC (carried to history) (RUN STAMP 2026-11-09T04:15:13Z)
- **Mode:** INCREMENTAL. Read: periodic_index.py, its new test,
  exceedance.py diff vs HEAD (label_sig gate, run_seed/_daily_runs,
  prune_frames, pid-suffixed tmp). Connected reads: pipeline sweep globs,
  truth_mirror.ensure_mirror, viz/__main__, serve.py allowlist,
  test coverage map. TESTS EXECUTED offline: 15 passed. Reproduced:
  json.dumps of numpy uint16 raises TypeError.
- **Findings:** 3 new — 1 must, 2 should (design).
  - design 1: **MUST** exceedance.py:488 — label_sig json.dumps of numpy
    remap: TypeError every build, lane silently stops updating.
  - design 2: **SHOULD** :269-287 — _daily_runs pays unbounded mirror
    history every build; fix: trim to w_start-255d.
  - design 3: **SHOULD** :534-535 — index.json.tmp.<pid> never swept in
    raw_exceedance/; fix: sweep `out` at build() top.
- **Channels appended:** channel_ponytail_design.md.
- **Owners nudged:** design. **Owners offline:** infra-domain
  (carried 1M/1S, orchestrator attention).
- **Resolved:** ewb SHOULD periodic open-slice; design SHOULD label
  renumber gate (superseded by label_sig).
- **Open totals:** 5 (design 1M/2S; infra 1M/1S).

## 2026-11-09 02:16 UTC (carried to history) (RUN STAMP 2026-11-09T02:16:13Z)
- [Section text lost when this file was truncated by the 06:16Z run's
  rewrite; recovered headline facts from later runs' citations: 7 new
  findings — 1 must, 2 should, 4 nice; MUST = design exceedance sidecar
  track-id renumbering across window re-rolls (fixed next run by
  label_sig); SHOULDs: ewb temperature.py seam-merge kept-state, design
  _daily_runs unbounded history. Tests executed offline: 16 passed.]

## 2026-11-08 22:21 UTC (carried to history; recovered from git 2026-11-09 06:16Z run) (RUN STAMP 2026-11-08T22:17:13Z)
- **Mode:** INCREMENTAL (3 worklist files: dashboard/styles.css,
  ewb_live/scoring/truth_mirror.py, tests/test_stall_fixes.py — all diffed
  against HEAD, read in full). Connected code read: ops/freshness_check.py
  heartbeat reader, pipeline `_heartbeat`/`_run_worker`/`ensure_mirror` call
  site (pipeline.py:506), scoring/truth.py coord-guard call site,
  sources/_net.py with_retries, viz/build.py open_mirror callers,
  tests/test_truth_mirror_coords.py coverage, zarr 3.4.2 setitem probe
  (float64→float32 casts cleanly — regen path safe), HTTPError⊂OSError
  probe, and a full CSS→markup/JS emitter cross-check (every new class/id
  traced; token defs; brace balance 361/361). Unit suite NOT executed
  (static-review rule); test findings verified by reading code. No
  third_party/ or data/ read, no EWB compute.
- **What this change does:** the mirror gains per-20-point heartbeat beats
  during copy (21:34Z SHOULD fix) plus a coord-integrity guard/repair for
  the zarr fill_value=0.0 masking bug (B2), wired into every open_mirror;
  the offline stall-fix test file lands; styles.css gets the merged-map /
  raw-map / guardian-design polish + CLS reservations.
- **Findings:** 7 new — 0 must, 2 should, 5 nice.
  - ewb 3: **SHOULD** truth_mirror beat also fires from the standalone CLI →
    masks the wedge alarm (freshness ignores the beat's `pid`; fix = MainPID
    gate in freshness_check, cross-lane with infra). **SHOULD** the
    test_stall_fixes 404 test is vacuous — HTTPError is an OSError, so
    _net.with_retries retries 404 through the whole backoff ladder and the
    assert passes either way. **NICE** the fc fixture writes fake RESTART
    lines into the real logs/freshness.log.
  - design 4 **NICE** (all lean/dead-code): unreachable `.tall-note`
    display:block at ≤480 (killed by line 713's !important), dead first
    `.gm-chip[aria-pressed=true]` rule + orphan `--accent-soft` token,
    duplicated `.gm-halo`, empty `@media (max-width:620px)` block.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_design.md.
- **Owners nudged:** ewb (term_e87b37a0), design (term_f3c16ead) — both live.
- **Owners offline:** none.
- **Findings resolved since last run:** 4/4 — verified by reading the files:
  both MUST test-path items (test_stall_fixes now patches
  pl.RESULTS=tmp_path/"results" and writes the fixture heartbeat at
  results.parent), the mirror-beat SHOULD (implemented at
  truth_mirror.py:188-194 — and it spawned the pid-masking SHOULD above),
  and design's dead `.gm-chip-n` rule (grep 0 hits).
- **Open totals:** 7 (ewb 0M/2S/1N; design 0M/0S/4N; frontend/mlwp-store/
  infra 0).

## History
## 2026-11-08 21:34 UTC — previous latest-run summary — 2026-11-08 21:34 UTC (RUN STAMP 2026-11-08T21:16:46Z)
- **Mode:** INCREMENTAL (6 worklist files: dashboard/{index.html,mergedmap.js,
  styles.css}, ewb_live/pipeline.py, scripts/review/sr11_g34_verify.py,
  tests/e2e/test_dashboard_fe.py — all diffed against HEAD and read since the
  20:16 run). Connected code read: pipeline `_heartbeat` call sites +
  ops/freshness_check.py reader (path/jobs_done contract), truth_mirror.py
  (no beats in mirror phase — new finding), `_sel_thresholds` vs
  `_mode_clim` ewb_default branch (float(None) unreachable: default mode is
  owb, ewb_default table has both quantiles set), mergedmap inflight/cache/
  paintFrame races (self-heal via renderSeq — no finding), sidecar tmp
  naming vs fold-in glob vs sweep glob (isolated: `.status.tmp.<pid>`
  matches `*.tmp.*`, never `*.status.json`), serve.py JSON-only route +
  public_scrub_check both modes, e2e→tree selector cross-check (0 absent),
  CSS var light/dark definitions (0 missing), bootstrap perf measured:
  3696 scorecard cells ≈ 0.8 s in one pass (fine at publish cadence).
  Unit suite EXECUTED: **176 passed, 2 failed, 28 skipped** (failures = the
  2 MUSTs below). No third_party/ or data/ read, no EWB compute.
- **What this change does:** sr11 verify script gains the `_r()` finite
  guard; sidecar orphan tmps get pid-named + pair_sidecar swept; mergedmap
  gains the in-flight fetch map (one fetch per frame shared by
  paint+prefetch); merged/raw index.html+css settled; test_dashboard_fe
  merged/raw sections pin slider, badge, boxes, deep links.
- **Findings:** 3 new — 2 must, 1 should, 1 nice (ewb 2M/1S; design 0/0/1N).
  - ewb **MUST**×2 tests/test_stall_fixes.py — heartbeat move to
    RESULTS.parent left the two pinned tests reading/writing the old
    `results/` location → FileNotFoundError + a false freshness restart;
    repo gate `pytest` is red (2 failed).
  - ewb **SHOULD** pipeline.py:483/506 — no heartbeat beats during
    `ensure_mirror` + detect; a fresh box with a >90-min first sync is
    restarted every freshness tick (alarm prevents the stall's cure).
  - design **NICE** styles.css — dead `.gm-chip-n` rule (no emitter left).
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_design.md.
- **Owners nudged:** ewb (term_e87b37a0), design (term_f3c16ead) — both live.
- **Owners offline:** none needed (frontend live but no findings this run;
  infra/mlwp-store no findings).
- **Findings resolved since last run:** 3/3 — verified in tree: ewb SHOULD
  sr11 NaN now `_r()` finite-guard at sr11_g34_verify.py:49 and used at
  every float-out; ewb NICE sidecar orphan tmp now `.tmp.<pid>` +
  `_sweep_orphan_temps` globs `pair_sidecar/*.tmp.*` (pipeline.py:413-414,
  :1084); frontend NICE concurrent frame fetch now shared via `inflight`
  ts→Promise map, deleted in `.finally` (mergedmap.js:48,126-145).
- **Open totals:** 3 (ewb 2M/1S/0N; design 0/0/1N; frontend/mlwp-store/
  infra 0) — plus raw-badge NICE held at owner won't-fix.
- **Not checked:** e2e suites not executed here (frontend lane ran
  `-k "raw_ or merged"` = 14 passed at 20:5x); styles.css not
  line-reviewed (visual lane; var + dead-class checks pass);
  freshness_check needs systemd — logic reviewed only; the mirror-gap
  restart claim is a timing argument, not a live-fire test; bootstrap
  timing measured on synthetic 55k-row frames.


## 2026-11-08 16:28 UTC — previous latest-run summary — 2026-11-08 16:28 UTC (RUN STAMP 2026-11-08T16:15:52Z)
- **Mode:** INCREMENTAL (16 worklist files: dashboard/{app.js,globalmap.js,index.html,rawmap.js};
  ewb_live events/{ar,select,tc_screen}.py, pipeline.py, scoring/truth.py, sources/_transform.py;
  scripts/public_scrub_check.sh; tests test_{ar_tropical_filter,dateline,public_scrub,select_noverlap,tc_screen}.py +
  e2e/test_guardian_fe.py). Connected code read: evaluate.py status/`_note` flow, serve.py
  allowlist + nested-JSON rules, weekly.py quarantine/flow, viz/exceedance.py cutoff filter,
  public_reason() call sites, select.py floor/merge + owb_clim probe path, truth.py cache-key +
  crop path, test_pair_status.py/test_stall_fixes.py coverage. Unit suites EXECUTED (fakes only,
  no EWB compute): 75 passed (select_noverlap, ar_tc_guard, tc_screen, ar_tropical_filter,
  dateline, public_scrub, pipeline_unit, pair_status, stall_fixes). No third_party/ or data/ read.
- **What the change does:** backend hardening lands — pair_status sidecar contract with fold-in +
  public_reason publish sanitize, heartbeat/blocker-sig restart loop, orphan-tmp sweep,
  --publish-only mode; AR ocean-start/TC-screen rules + IBTrACS loader; select percentile-rank
  conflict resolution; truth cache key gains bbox + coord-integrity guard; dateline normalize
  on crop; scrub gate two-mode; dashboard gets GlobalDash (Equal-Earth 90-day timeline + Gantt +
  URL state) and RawDash (6-hourly raw-exceedance canvas map, 4 planes, LUT paint, zlib .bin).
- **Findings:** 4 new — 1 must, 3 should, 0 nice.
  - ewb 3: **MUST** evaluate.py:434/438 raw `{exc}` reasons persist unsanitized in
    data/results/pair_sidecar/** → scrub gate quarantines/fails deploys on any transient
    path-bearing error (public_reason only applied at publish, pipeline.py:637).
    **SHOULD** pipeline.py:620 sidecar fold sorts via p.stat() outside try →
    quarantine/concurrent-publish race crashes run() pre-scoring. **SHOULD**
    pipeline.py:619-629 sidecars never pruned → unbounded fold-in growth.
  - frontend 1: **SHOULD** globalmap.js:30/rawmap.js:24 hardcode current-window data paths,
    ignore state.week → archived-week view mixes snapshot catalog with current-window maps
    (+ misleading not-in-window note on open). Fix: hide #global-sec/#raw-sec when state.week set.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_frontend.md.
- **Owners nudged:** ewb (term_dede3bb6), frontend (term_5ef9b2ad) — both live in
  `orca terminal list --json`.
- **Owners offline:** none.
- **Findings resolved since last run:** 0/3 — verified still open in tree: d3-array tag still
  present with no guard comment (design SHOULD, 15:25); raw-badge class-toggle/cutoff logic
  unchanged; globalmap fail() fixed for gm-map (setAttribute) but gm-gantt still uses the
  `.hidden` expando (frontend NICE, 15:25 — partial fix noted in channel).
- **Open totals:** 7 (ewb 1M/2S/0N; frontend 0M/1S/2N; design 0M/1S/0N; infra/mlwp-store 0).
- **Not checked:** runtime rendering of GlobalDash/RawDash (no serving/compute from this
  container); eventviz.js + styles.css big diffs (not in worklist — skimmed only for the
  ids/classes the new JS touches, all present); raw_exceedance frame bytes vs producer
  round-trip (contract read both sides, not decoded live); e2e suite not executed (needs
  served data).

2026-2026-11-08 12:17 UTC (RUN STAMP 2026-11-08T12:17:15Z)
- **Mode:** INCREMENTAL (files: ewb_live/pipeline.py — mtime 11:24, the
  owner's 11:23Z-fixes batch; tests/e2e/test_dashboard_fe.py — mtime 12:14,
  new global-dashboard/catalog-box/theme tests). pipeline.py read in full
  with connected code re-checked: events/types.py make_event_id (id =
  type+start-date+rounded bbox — END excluded), events/temperature.py
  _mode_clim/climatology_meta (per-publish cost light, no perf finding),
  scoring/evaluate.py status vocabulary + build_forecast read-guard scope
  (fc_cache CACHE_MODELS vs lazy sources/aiwp.py open_dataset), ops unit
  PrivateTmp grep, pyproject e2e gating. e2e file read in full; selector/
  state contracts are owner-pinned by design.
- **What the change does:** pipeline.py = the three ponytail fixes from the
  11:23Z batch applied (pid-suffixed temps, untruncated last_sig blocker
  sig, mtime-ordered sidecar fold-in). e2e = regression suite added for the
  global dashboard, map-box click + back map-restore, graceful viz 404,
  #lead deep-link at a non-first lead, OS theme flip, and mobile width caps.
- **Findings:** 6 new — 1 must, 1 should, 4 nice.
  - ewb (pipeline.py) 4: **MUST** pair_status entries are keyed by event id
    only; when a fingerprint or truth change drops cached scores, a stale
    TERMINAL/documented entry permanently suppresses the rescore AND still
    counts as documented-zero → silent permanent hole while
    full_run_complete=true (D1's "35 stale terminal pairs" was this class;
    the vocabulary fix left the keying). **SHOULD** evaluate.py:428
    catch-all marks load-time (lazy-zarr ds.load) read failures as terminal
    "unsupported", defeating the D1 retriable contract. NICE /tmp/ewb_jobs_*
    never reaped after a mid-pool kill (no PrivateTmp, no sweep). NICE dead
    `import subprocess, sys` in run().
  - frontend (e2e) 2 NICE: fixture probe-binds then rebinds an ephemeral
    port (TOCTOU → bind flakes; bind the server to :0 instead); dead
    HEAT_ID/AR_ID constants.
  - design/infra/mlwp-store: nothing with a concrete failure case this pass.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_frontend.md.
- **Owners nudged:** ewb (term_dede3bb6…) and frontend (term_5ef9b2ad…) —
  both live, sends accepted.
- **Owners offline:** none.
- **Findings resolved since last run:** 3/3, verified in tree —
  pid-suffixed temps (:606 pair_status, :628 scores.parquet, plus :53
  pid+tid in _atomic_write; all orphans match the *.tmp.* sweep glob);
  untruncated blocker sig (last_sig stashed in _write_outputs :657,
  compared at :932 — meta's [:50] list no longer feeds the sig); sidecar
  fold-in sorted by st_mtime (:590, newest chunk-tagged entry wins).
- **Open totals:** 6 (ewb 4: 1M/1S/2N; frontend 2: 0M/0S/2N; design/infra/
  mlwp-store 0).
- **Not checked:** live data/ snapshots behind the fixture existence
  assumptions (off-limits); runtime behavior of the new #gm-*/globalmap
  selectors (e2e runs on the box only; owner's own note says e2e 19/19
  green); viz.json lead-key shape behind test_lead_deep_link's int() cast.
2026-11-08 11:23 UTC (RUN STAMP 2026-11-08T11:17:15Z)
- **Mode:** INCREMENTAL (files: dashboard/app.js, ewb_live/pipeline.py,
  tests/e2e/test_dashboard_fe.py, tests/test_pipeline_unit.py). app.js +
  pipeline.py read in full with connected callers (scoring/evaluate.py
  status_out + read_error contract, truth_source_for_event,
  events/temperature._mode_clim, ops/freshness_check.py, pyproject pytest
  gating, index.html/eventviz.js/globalmap.js hooks for every new DOM id —
  all present, teardown/onModels/GlobalDash.* all exist).
  tests/e2e/test_dashboard_fe.py was touched (mtime 10:30) but has **no
  content change vs HEAD** — verified, nothing to review there.
- **What the change does:** frontend deep-link/navigation rework
  (pushState Back-to-list, lead deep-link, pair_status-driven n/a notes,
  lazy Plotly, empty-lane copy, keyboard-accessible tooltips) plus the
  pipeline resilience batch's follow-up fixes (publish-only force guard,
  blocker-sig restart stop, orphan-part re-merge, scorecard common-subset
  skill + bootstrap CIs).
- **Findings:** 3 new — 1 should, 2 nice, all ewb (pipeline.py):
  - SHOULD: fixed-name temps for scores.parquet/pair_status.json
    (:604/:624) race across processes when ops runs --publish-only while
    the unit scores → torn file published → next run crash-loops;
    fix = pid-suffixed temps (already swept by *.tmp.*).
  - NICE: blocker sig reads meta's always-absent top-level failed_jobs +
    a [:50]-truncated blocker list (:926-927) — a >50-pair blocker set can
    churn in its tail and exit 0 as "permanent".
  - NICE: sidecar fold-in merges alphabetically (:588-590), so stale
    chunk-tagged sidecars (never deleted) overwrite a newer untagged one
    after a mid-run kill; fix = sort by mtime.
  - frontend/design/infra/mlwp-store: nothing with a concrete failure
    case this pass (new DOM ids all present in index.html; esc() used at
    every data-fed innerHTML site; removed renderVerdict/renderLegend/
    eventTruth/fmtDateTime have zero remaining refs incl. e2e).
- **Channels appended:** channel_ponytail_ewb.md.
- **Owners nudged:** ewb (term_dede3bb6…) — live, send accepted.
- **Owners offline:** none.
- **Findings resolved since last run:** 8/8 verified fixed in tree —
  ewb 3 (force=False :432; _record_blocker_sig :379/:928;
  _merge_orphan_parts :388/:563, all unit-pinned by the new
  test_pipeline_unit tests) and frontend 5 (buildCharts filter-aware note;
  pyproject addopts -m "not e2e" :40; dropdown open-state snapshot;
  import math deleted; freeze fixture exclusive end :403).
- **Open totals:** 3 (ewb 3: 0M/1S/2N; frontend/design/infra/mlwp-store 0).
2026-11-08 05:03 UTC (RUN STAMP 2026-11-08T04:16:43Z)
- **Mode:** INCREMENTAL (re-run of the 04:16 worklist after its done-marker;
  files: dashboard/eventviz.js, ewb_live/pipeline.py, ewb_live/weekly.py,
  ops/install.sh). Connected code read: scripts/public_scrub_check.sh,
  ewb_live/serve.py, dashboard/app.js (picker/detail hooks), ops units +
  freshness_check.py, stores/mlwp_icechunk.py (flock claim verified real),
  scoring/evaluate.py + truth_mirror.py signatures, viz/build.py (packbits
  format, antimeridian lattice, schema-3 fields), events/temperature.py
  _mode_clim, index.html load order.
- **eventviz.js: clean this pass.** schema-3 bit-unpack matches the builder
  byte-for-byte; ?data= whitelist is server-enforced (serve.py MINI_DIRS);
  openTooltip arg order fixed (prev run's live must, re-verified in tree);
  crossing-window continuous box is consistent with the builder's eastward
  lattice; every data-fed innerHTML site escapes or uses numeric-only
  fragments; new bar/ruler CSS exists in styles.css. No finding with a
  concrete failure case.
- **ops/install.sh: clean.** all 9 UNITS files exist in ops/, deploy gate
  wired, uninstall loops the full list.
- **Findings:** 5 new — 1 must, 2 should, 2 nice, all ewb (pipeline.py x4,
  weekly.py x1). Headline: weekly._leak_gate quarantines leaked files as
  *.quarantined *inside* data/results where the gate's own grep rescans
  them — one leak makes every later weekly fail pre-snapshot forever and
  blocks install.sh redeploys.
- **Channels appended:** channel_ponytail_ewb.md. frontend/design/infra/mlwp-store:
  no new findings, no appends.
- **Owners nudged:** ewb (term_dede3bb6…) — live, send accepted.
- **Owners offline:** none (all roster handles live).
- **Findings resolved since last run:** 3 of 4 open — ewb _net 429 retry
  (fixed + test test_stall_fixes.py:155); frontend scroll-restore (mount-path
  pending-restore, app.js:1456-1465); frontend e2e transparency bound
  (relaxed to 0.25 with rationale). Still open: frontend lane-empty copy
  conflating 404 with renderer crash (app.js:1636, carried).
- **Open totals:** 6 (ewb 5: 1M/2S/2N; frontend 1: 1N).
2026-11-08 03:47 UTC (RUN STAMP 2026-11-08T03:47:52Z)
- **Mode:** FULL SWEEP (worklist fired 03:47, after an incremental pass at
  ~02:35 on the same findings set — that pass covered the owner-modified
  files: pipeline.py, truth.py, hres.py, api.py, ecmwf.py, viz/build.py,
  weekly.py, serve.py, app.js, eventviz.js, install.sh, freshness_check.py,
  public_scrub_check.sh + test, nginx public front. The sweep then extended
  to tests/ (test_stall_fixes, test_pair_status, test_mini_snapshot, e2e
  structure, conftest network gating — all sane) and a re-audit of the
  in-flight eventviz.js edit (schema-3 unpack, occFit antimeridian math,
  ?data= whitelist: clean; app.js uses a 4-name exact whitelist).
- **Not read / thin (honest gaps):** scripts/mlwp_* one-off survey scripts
  and scripts/review/* (disposable QA artifacts), styles.css (CSS only),
  tests marked `network` (body-level skim only; they run on the box).
  third_party/ and data/ off-limits per rule. ewb_live/{events,scoring,
  sources,stores} modules unchanged since the 02:05 full-sweep reads —
  those reads still stand (verified via mtime < 03:47).
- **Findings:** 4 new — 1 must, 2 should, 1 nice.
  - infra: 1 must (install.sh uninstall leaves the now-enabled
    pipeline/memguard/freshness units running and enabled — reboot resumes
    scoring after "uninstall").
  - ewb: 2 should (heartbeat beat stops before the serial retry lane so
    freshness restarts a live >90-min retry mid-run; weekly's scrub gate runs
    after publish + history snapshot so a failed gate is already public at
    two URL families), 1 nice (dead update_truth_mirror in weekly.py).
  - design / frontend / mlwp-store: no findings with a concrete failure case
    this run (JS XSS audit clean: every data-fed innerHTML site goes
    through esc(); viz guards schema-2/3 and NaN payloads).
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_infra.md.
- **Owners nudged:** ewb (term_dede3bb6…) and infra-domain (term_987fee64…) —
  both live, sends accepted. No new findings for design/frontend/mlwp-store,
  no nudges for them.
- **Findings resolved since last run:** 6/6 open items verified fixed in
  tree by reading the code — scoring/truth.py + hres.py bbox-aware cache key
  + zombie-zarr cleanup (MUST), pipeline atomic scores.parquet /
  pair_status / meta / truth_index via tmp+os.replace (MUST), api.py partial
  subset-cache now tmp+replace and rmtree-on-open-failure (MUST, fixed
  02:59), ecmwf.py surface branch now passes valid_times (SHOULD),
  viz/build.py bbox match on candidate scan (SHOULD, fixed), perf
  failed_jobs now separated via pair_coverage.failed_jobs_after_retry
  (NICE, addressed). Frontend carryovers from the 21:49 run (app.js
  double-encoded syncUrl + dead lead branch) verified fixed in the 02:45
  owner edit.
- **Open totals:** ewb 3 (0M/2S/1N), infra 1 (1M/0S/0N), design 0, frontend
  0, mlwp-store 0. Total 4 (1M/2S/1N).
### 2026-11-07 23:49 UTC (finished 2026-11-08 00:1x UTC) — FULL SWEEP (RUN STAMP 2026-11-07T23:49:16Z)
- Read in full: pipeline.py, weekly.py, serve.py, viz/build.py,
  events/{types,__init__,select,temperature,ar,validate,truth}.py,
  scoring/{evaluate,truth,truth_mirror}.py, sources/api.py, dashboard/app.js.
  Partial: eventviz.js (pack/unpack paths), freshness_check.py,
  ponytail_review.py, remote.sh, build_mini_results.py headers,
  mlwp_icechunk.py structure. Not read: styles.css, most tests/, ops probe_*
  one-offs, aigfs/aiwp/ecmwf internals, third_party/ and data/.
- Findings: 2 must, 2 should, 1 nice (5 total): ewb 3 (1M pipeline.py:762
  tuple unpack, 1S select.py:124 stale severity cap, 1N TTL docstring);
  design 2 (1M viz arg swap, 1S p95/p05 copy).
- Channels created (first run); owners ewb + design nudged, both live.
- All 5 findings verified fixed in tree on the 2026-11-08T02:05Z run.
