SUCCESSOR-HANDLE: term_4227aacb-5179-4f34-b195-e06f70d30fb3 (design successor 3, launched 2026-11-06 via orca agent launch; runtime 228143db-3850-4dc9-9e76-77547fe0d393)

# Handoff — design successor 2 → 3 (ewb2 line), 2026-11-06 ~18:55 UTC

## Read first
SPEC.md (binding: NO git commits — orchestrator commits; NO EWB compute on
orca container — everything on LXC via ops/remote.sh; context-handoff rule).
notes/spec_simplify.md + notes/design.md '## Simplification plan' (owner's
sparse-UI direction, executed). notes/review_round1.md (final review doc,
deployed). notes/channel_design_ewb.md (team messages; last one is yours).

## Done (all deployed to box http://192.168.0.121:8050, code synced, NOTHING committed)
- eventviz v3 (Leaflet, Esri gray tiles, u8 schema v2 loader, contours,
  synced views, model picker cap 4 + baseline lock, fitBounds on RAW event
  bbox with 45px padding maxZoom 5 — view 1.3× bbox, asserted; post-layout
  refit in renderMaps fixes zero-size-container world view; faint 10°
  graticule; colorbar ticks de-collided; window.__ev3 QA handle).
- OWB restyle + simplification: one-line header (STATIC, sticky removed —
  QA overlap fix), kv-line, scorecard hero = headline metric/type at
  1/3/5/7/10 d (DEFAULT_LEAD_BINS=[24,72,120,168,240]) + 'more leads'/
  'more metrics' toggles (state.scMore), model dropdown (details.dd),
  home hides when detail opens, single story line (chips removed), no ids
  in table rows, About drawer. 'partial results' badge in scorecard header
  clears when meta.models all scored OR meta.full_run_complete=true.
- Scorecard now shows all registered models (was old 3-model) automatically
  from scores.json — the full run is what made it show more.
- 6 review events picked + viz built on box (units vizr6 5/5 ok, vizr6f ok):
  AR 29d2a97a7c09 (Australia), 43e1cd0e5eb4 (USWC), 3d32db128915 (Europe),
  fbe99f30e625 (EAsia); heat 457f3f87e24a (US Central); freeze 88bb73d7ae25
  (Greenland p15). All deep-link verified, freeze detail verified (4 maps,
  0 errors). Screenshots notes/screenshots/simplify-{before,after}-*.png +
  preview-box-*.png.

## Remaining (the only open task)
ewb-3's scoring on the corrected 27-event list is at 10/11 models; ONLY
'aigfs' missing; unit ewb-live-pipeline active. When it completes:
1. ssh check (single command, no loops):
   `./ops/remote.sh run python - <<PY`-style one-shot comparing set(models
   in data/results/scores.json rows) vs meta.json models — pattern used in
   this session (see channel/handoff). Or just fetch
   http://192.168.0.121:8050/results/scores.json + meta.json.
2. When complete: make EXACTLY 'REVIEW READY' the FIRST LINE of
   notes/review_round1.md (currently absent from the file — it must appear
   only there), and delete the 'Status / caveats' scoring-pending bullet
   wording (replace: full 11-model run complete; badge cleared). Verify on
   box that the badge is gone (Playwright: #scorecard-status hidden).
3. Done → report to orchestrator. STOP after; owner feedback next.

## Gotchas
- NEVER git commit (orchestrator commits at end).
- Serve/box data: box owns data/; ops/remote.sh sync is code-only.
- Browser screenshots: browser_exec capture_screenshot reuses one tmp
  path — copy immediately after each shot.
- Playwright: .venv/bin/python, args ['--no-sandbox']; this container has
  NO internet → tile failures expected, ignore; box URL has tiles via
  browser_exec (has proxy) instead.
- Background wait pattern: ssh one-shot or terminal(background,notify=
  ['PATTERN']) — never sleep-loop watchers (tool policy kills them).
- Model picker chip ids: window.__ev3 = {maps, boxRect} on detail maps.

SUCCESSOR-HANDLE-FILLED: (fill via orca agent launch output, post to top)
