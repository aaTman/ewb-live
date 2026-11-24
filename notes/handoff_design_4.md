# Handoff design #4 → successor 'design' (handle: to be claimed in notes/orchestrator_watch.md) — 2026-11-08 03:10 UTC

I'm design successor 4 at ~89% context. Read SPEC.md first (run everything on
the ewb LXC via ops/remote.sh; NEVER git commit; handoff naming per SPEC).
Authoritative state docs: **notes/kt_design.md** (full KT) +
**notes/handoff_design_final.md** (my earlier final — mostly still true; this
file supersedes it where they differ, mainly: site is now PUBLIC at
https://ewb.espr.ai, event ids changed twice since).

## Done since handoff_design_final.md (2026-11-08 ~01:00–03:10 UTC)
1. viz schema 3 rebuilt for the CURRENT 30 events (12+2 AR / 15 heat / 1
   freeze, run 20261107T231622Z, full_run_complete TRUE), orphans purged,
   all ids verified serving 200 + matching type + schema 3.
2. build.py:381 arg-swap (ponytail MUST) + app.js/index.html p85/p15 copy +
   LAN-IP removal from About (public-site scrub verified 3 places).
3. AR green tokens measured + shipped: light #2e7d32 / dark #66bb6a (AA);
   event-box click parity spec, IVT paper-matched ramp spec (Blues
   #dbe9f6→#08306b, 250–1000 kg m⁻¹ s⁻¹, transparent <250, 400 tick marked —
   pixel-classified from arXiv 2605.01126 fig4 + global_ar pngs), all posted
   to notes/channel_design_frontend.md + notes/design.md + KT doc.
4. Ponytail 02:05 finding: sibling already fixed truth.py keying (+bbox);
   I deleted my dead _covers helper + orphan glob import; unit tests pass.

## THE ONE OPEN LOOP — VERIFIED CLOSED (03:12 UTC)
Unit ewb-viz-truthfix (`--all --force`, MemoryMax=16G) completed
"successfully", 10m45s CPU: **31 'ok' lines, 0 errors/tracebacks** — all 30
current events rebuilt post truth-key fix. Nothing pending in design's lane
except frontend-depends sign-offs below.

## Still open (not mine or gated)
- ewb: s3fs/aiobotocore bug ('multiple values for config') kills ifs_hres +
  aifs_single viz panels 90/90 — repro in channel_design_ewb.md 01:25.
  Until fixed, EVERY event page shows those two panels 'unavailable'.
- ewb: anti-drift pipeline hook (viz spawn after selection, pipeline.py
  ~:387) — proposal posted, not yet landed.
- frontend: apply green token, IVT Blues ramp + transparency floor, event-box
  parity + Playwright test; I sign off visually after deploy (QA probes:
  browser_exec against https://ewb.espr.ai; window.__debug.state / __ev3).
- Then: refresh review set (2/type intent — now 15 heat/14 AR/1 freeze),
  refresh notes/review_round1.md, flip REVIEW READY; headline numbers vs
  real scores (full_run_complete is TRUE now — this can be done).
- review_round1.md first line is currently 'REVIEW ON HOLD — ...' (honest).
- Channel for infra requests: notes/channel_infra.md (respond-only there).

Ponytail mode active (full): lazy fixes only, triage findings in
notes/channel_ponytail_design.md. No git commits. Scoring lanes outrank viz
jobs on the box.
