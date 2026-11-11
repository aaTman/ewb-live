# Handoff: ar-ramp → pi successor (written 2026-11-10 ~00:50 UTC)

You are 'ar-ramp', successor 3 (pi). Predecessors: Mistral Vibe (built the
pipeline+frontend ramp, died mid-verification), Hermes successor 2 (this
writer, died to the replacement). You are Hermes-replaced; keep this file
updated if you hand off again.

## 1. Role and owned files

Task (binding brief: **notes/briefs/ar_colour_ramp.md** — read it first):
change the AR representation on the merged global map from a single green
mask to a STEPPED COLOUR RAMP by IVT magnitude, bins 250/500/750/1000/1250
kg m⁻¹ s⁻¹ (5 classes), green identity, 5-swatch legend, hover shows IVT
class, binary fallback must NEVER break, deploy to ewb LXC via
`ops/remote.sh` (NO EWB compute on this container), verify live on
https://ewb.espr.ai, screenshots to notes/screenshots/ar-ramp-*.png, post
'## [time] ar-ramp: AR RAMP READY' in notes/channel_design_frontend.md and
nudge design (term_f3c16ead-1e8d-4d15-bc73-1d93f45ea9e9) + frontend
(term_55602a39-edbc-42a8-8310-703b3f5e6d2a).

Files in play (lane ownership per channel header: frontend owns
dashboard/*.js, design owns ewb_live/viz assets — ar-ramp was authorized by
the brief to touch both for THIS feature):
- ewb_live/viz/exceedance.py — IVT class plane builder (schema 3, .ivt sidecars)
- dashboard/mergedmap.js — AR ramp paint + legend + hover (shared-hot: a
  sibling ponytail agent edits it concurrently; RE-READ before every patch)
- dashboard/styles.css — --dur-ar-1..5 tokens light/dark, .gm-legend styles
- dashboard/index.html — #gm-legend element
- tests/e2e/test_dashboard_fe.py — the three test_ar_ramp_* tests + fixtures
- notes/ar_ramp.md + notes/ar_ramp_completion.md — predecessor logs (the
  "verification checklist" in completion.md was written BEFORE any test ran;
  the predecessor's tests were broken — successor 2 fixed them, see below)

## 2. Exactly where this stopped (successor 2, Hermes)

DONE and verified:
- Data build COMPLETE on the box: 336 `<YYYYMMDDHH>.ivt` sidecars
  (zlib u8 class planes 0–5, same geometry as the .bin AR plane),
  index.json schema 3 with `ivt:{schema:1,bins:[250,500,750,1000,1250]}`,
  serving HTTP 200 on the LAN origin AND public. Build job (unit name was
  ewb-ar-ramp) finished: log tail "raw_exceedance: 336 frames indexed".
- Serve allowlist already extended to .ivt (ewb_live/serve.py, deployed;
  web serves it — no restart needed).
- Fixed the predecessor's BROKEN e2e tests (they navigated past the fixture
  cutoff and compared raw hex vs canvas; 2 of 3 had never passed): rewrote
  all three test_ar_ramp_* tests — now **3/3 pass locally** (fixture server,
  ~5 s). New tests use `MergedMap._dbg().ivtAt(j,i)`, an opaque-ramp pixel
  census, hover via mouse.move over the class-3 cell, and a REAL 404 stub
  (`page.route("**/*.ivt")` → fulfill 404) for the fallback test.
- Added mergedmap.js pieces successor 2 wrote (the brief required a hover
  tooltip; predecessor never implemented one): rAF-throttled pointermove on
  #raw-canvas reading `lut.cellIdx` + `curIvt` → shared #tooltip element
  ("AR IVT 750–1000 kg m⁻¹ s⁻¹"); `_dbg` now exposes `packed(li)` and
  `ivtAt(j,i)` for tests; renderLegend shares IVT_BINS.
- DEPLOYED: `ops/remote.sh run …` synced everything; mergedmap.js md5
  **3fee88a4683fd4454bdef164b43b635d identical in worktree, on the box
  (/opt/ewb-live/dashboard/), and at the public edge**. Box tests:
  tests/test_raw_exceedance.py 8 passed.
- Live public census REACHED and PASSED the hard parts: census (AR px > 0,
  classes present) and 5-swatch legend on
  https://ewb.espr.ai/#map=2026100312&layers=a (script ran through census +
  legend before failing).

HALF-DONE (state):
- Live verification script `/home/orca/.hermes/cache/scratch/ar_ramp_live.py`
  (run with repo .venv from repo root; polls via page.evaluate because the
  PUBLIC site CSP kills wait_for_function — keep that pattern). It FAILED at
  the LAST step: hover over a sampled ramp pixel on public → #tooltip never
  appeared (fixture hover passes; public hover does not). PRIME SUSPECT: the
  SVG event-box overlay (#gm-boxes hit rects, interactive by design) sits
  OVER the canvas inside event bboxes and swallows pointermove, so the
  canvas listener never fires there; the fixture's test cell happened to be
  outside any box. Diagnose first (e.g. `document.elementFromPoint(x,y)` at
  the sample). Cheap fix if confirmed: attach the pointermove listener to
  #gm-map-wrap (or the svg too) and translate coordinates — or accept
  "hover works outside event boxes" and note it for design QA. Then rerun
  the script: it writes the 4 screenshots notes/screenshots/ar-ramp-
  {desktop-1440-light,desktop-1440-dark,mobile-390-light,mobile-390-dark}.png
  (NONE taken yet — the run died before screenshot 1) and asserts fallback
  (stubbed-404 binary px > 0 + 'AR IVT class sidecar unavailable' console
  warning) + dark census + mobile AR px + zero console errors. Prints
  VERIFY PASS/FAIL.

NOT started: READY post, channel nudges, final notes/ar_ramp.md '## DONE'.

## 3. Running processes / jobs

- LOCAL: the full e2e background run (`pytest tests/e2e -m e2e -q`)
  COMPLETED GREEN at ~01:00Z against the LAN origin: **112 passed,
  3 skipped in 270 s** — the current deployed bytes (mergedmap md5
  3fee88a4…) pass the whole suite, incl. the 3 new ar_ramp tests. Re-run
  only if dashboard/* changes again (baseline before this feature: 104).
- BOX: nothing ar-ramp-related running (builder unit exited clean; only
  ewb-live-memguard + ewb-live-web active). No long box jobs to babysit.
  ewb's weekly pipeline lane is NOT yours — check before any new unit
  (design rule: never concurrent with scoring, MemoryMax=12G, one job).

## 4. Remaining tasks (ordered, with acceptance)

1. Fix/diagnose the public-site hover (see §2). Accept: live script prints
   VERIFY PASS (hover tooltip text contains "AR IVT" on a public class px).
2. ~~Run the FULL e2e suite~~ DONE as of the last deploy: 112 passed /
   3 skipped vs LAN, ar_ramp 3/3 (see §3). Re-run only after any further
   dashboard/*.js change.
3. Screenshots from the script: notes/screenshots/ar-ramp-desktop-1440-
   {light,dark}.png + ar-ramp-mobile-390-{light,dark}.png on the live site.
   Visually confirm ramp classes visible + legend in all four.
4. Post '## [<UTC time>] ar-ramp: AR RAMP READY' in
   notes/channel_design_frontend.md with md5s, test counts, screenshots
   paths; nudge design (term_f3c16ead-1e8d-4d15-bc73-1d93f45ea9e9) for the
   live QA pass (design does NOT accept self-sign-off — expect re-QA) and
   frontend (term_55602a39-edbc-42a8-8310-703b3f5e6d2a) since you edited
   their file. Nudge mechanism: `orca` CLI (~/.local/bin/orca) — run
   `orca agent-context --json` to find the send/nudge command for terminal
   ids (schema v1, 288 commands; the command name is under Agent Sessions).
5. Append the successor-2/3 log to notes/ar_ramp.md ending with '## DONE'.
6. No git commits — ever (orchestrator commits the worktree).

Standing owner decisions to honor in any data-touching work (from SPEC +
channels, relevant if design/ewb ask for rebuilds): heat p85 / freeze p15
(ERA5 1990-2019), AR tropical exclusion + TC screen at track level,
gap-bridging for 1-step AR blinks (design proposals A/B/C in
channel_design_frontend.md 18:05 post — owner pick pending), AR landfall_days
>= 1, publish-gate drops must be logged, AR IVT ramp bins
250/500/750/1000/1250, builders write to temp names then swap (atomic
publish — the .ivt build followed it; keep it).

## 4b. DASHBOARD FREEZE (fable-review, 2026-11-10 01:24Z — owner request)

Claude in worktree 'fable-review' owns the frontend lane for a UI redesign:
make NO edits to dashboard/* and do NOT scp/sync dashboard files to the box
until fable-review posts 'REDESIGN LANDED' in notes/channel_design_frontend.md.
Any dashboard change you need (incl. the hover fix of §2) must be POSTED in
that channel addressed to fable-review, not implemented. Non-dashboard work
(pipeline, assets, non-e2e tests, this note) is unaffected. Note this may
also block finishing the live-verify/screenshots sequence — hover is a
mergedmap.js change; coordinate in the channel first.

## 5. Gotchas / open questions

- PUBLIC SITE CSP: `wait_for_function` throws EvalError on ewb.espr.ai —
  poll with `page.evaluate("() => (expr)")` instead (the fixture tests on
  localhost CAN use wait_for_function).
- Merged-map paint invariants (do not regress): AR NEVER indexes the .run
  sidecar (.run has only 2 planes; plane-2 reads caused the two AR outages
  of 2026-11-09); binary fallback branch
  `else if (u8[li*plane+bi] & m) px[p] = packed[li]` must stay reachable
  when ivt/run are null; 204 and 404 both treated as missing.
- A sibling ponytail agent edits mergedmap.js + test_dashboard_fe.py
  concurrently and has ALREADY changed AR alpha to opaque (255) in
  packedCache/rampCache (canvas RGB == CSS token — the tests rely on this).
  Re-read files before patching; verify box/worktree/public md5 after any
  sync you trigger.
- Fixture truths (tests/e2e/test_dashboard_fe.py): raw fixture cutoff =
  2025110606 (frame …12 is past cutoff and paints NOTHING — predecessor's
  bug); AR blob j=28, i=46..50 = classes 1..5 at frame 2025110606; cell
  center = project([lon0+i*step, lat0+j*step]).
- Live data: 336 frames 2026081100→2026110218, cutoff 2026110218,
  schema 3 {dur, tracks, ivt} sidecars all present; verification frame
  #map=2026100312&layers=a has ~15k AR cells; .bin/.trk/.ivt are
  max-age=3600 while index.json is no-store — after any same-name rebuild
  the edge can mix versions for ≤1 h (hard-reload when verifying).
- Open question for design (raise at READY): legend shows whenever AR is on
  + index advertises ivt, even on past-cutoff frames that paint nothing —
  fine or should it grey out? And the hover-vs-event-box overlap finding
  (whether canvas hover must work UNDER boxes).
- Tooltip reuses app.js's #tooltip + global moveTip (app.js is top-level,
  no IIFE) — no new CSS needed; hide paths on pointerleave/layer-off.

## 6. Channels and teammates

- notes/channel_design_frontend.md — the working channel (append-only,
  format '## [YYYY-MM-DD HH:MM UTC] <from> -> <to>: <subject>'). Post READY
  HERE.
- notes/channel_design_ewb.md — data-lane coordination (ewb owns detection/
  tracking; any rebuild of shared AR masks is ewb+design, coordinate first).
- notes/orchestrator_watch.md — orchestrator directives; the owner decision
  replacing Hermes agents with pi agents is logged there.
- Team terminal ids: design term_f3c16ead-1e8d-4d15-bc73-1d93f45ea9e9
  (visual QA sign-off owner, owns styles/tokens/legend copy), frontend
  term_55602a39-edbc-42a8-8310-703b3f5e6d2a (owns dashboard/*.js — nudge
  about mergedmap.js edits), ewb (data lane).
- Docs to skim before working: SPEC.md, notes/kt_design.md, notes/kt_
  frontend.md, notes/ar_ramp.md, notes/ar_ramp_completion.md (predecessor,
  partly aspirational), notes/briefs/ar_colour_ramp.md (binding).

HANDOFF WRITTEN
