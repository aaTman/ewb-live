# Blink/flicker fix — owner decision brief (referee, 2026-11-10)

One page. All evidence verified on lane build 2026-11-10T02:48:26Z,
events sha256 51f96b150336625a (full lane rebuilds invalidate per-frame
numbers; re-run scripts/review/sr_gate.py after any rebuild).

## Problem (verified)
1. Pixel blink on EVERY interior frame: 269,593 blink px over 335 windows.
   75.1% = border jitter of surviving components; 24.9% = whole-component
   1-step dropouts (§P, §P.1).
2. User-visible: mergedmap.js renders the raw lane for every frame globally,
   so 43 big (≥500 px) non-curated blinks flicker on the live map (§M.1, §N).
3. Curated layer is NOT clean: 4 curated AR events blink inside their own
   windows (§K, sr13 v3), and 6 curated events have interior DEAD frames
   (object truly gone mid-window; max run 3 frames/18h) — a DISJOINT failure
   mode from blinks (§L.1). The builder's flickering_tracks gate is blind to
   both (§L.2, source-verified). AR-class only; heat/freeze clean (§M).

## Options
A. Temporal hysteresis (relaxed threshold for established tracks).
   Attacks the 75.1% border-jitter majority AT SOURCE. Cost: changes the
   SHARED detection fn — events lane outputs move; needs curation re-run.
B. Track-level 1-step gap persistence in ar_filter_tracks + gap-bridged
   footprint. Removes all 43 big raw-layer blinks at once (§N) and the 4
   curated-window blinks. Masks (does not remove) border jitter; does NOT
   fix interior DEAD spans (object is gone, nothing to bridge).
C. Frontend temporal blend (~35% alpha of t-1 plane). Cosmetic, zero
   rebuild, lane-isolated; risk = visible 6h ghost fringe. Touches nothing
   in data; curated DEAD frames stay dark (arguably correct).
Plus (independent of A/B/C): curation-lifetime gate — clamp curated window
to object life and reject windows with unbridgable interior DEAD spans.
REQUIRED for the dead-at-t0 class only (1 event, 8002) + tail hygiene
(§R correction): a shipped 3-frame gap-bridge alone kills the interior-
flicker class — longest interior DEAD run is 3.
Simulated cost (§Q/§R): clamp+bridge=3 trims ONE window (8002, 4 frames),
rejects none under interior-run semantics; the G3 reject path exists for
8002's dead-at-t0 start. Acceptance: scripts/review/sr_gate.py --bridge <shipped N>.

## Referee recommendation (evidence-weighted)
A + the curation-lifetime gate is the only combination that reduces flicker
at source AND cleans the curated layer; B is the cheaper data-side
alternative if the owner will not let the shared detection fn move; C is a
band-aid that can ship today while A/B is rebuilt. The two mechanisms
previously asked for (raw gap-bridge + curation clamp) map to B + clamp.

## Acceptance (pre-registered §O, amended §R/§S.2 — gate source is truth)
scripts/review/sr_gate.py --bridge <shipped N> on the box (NO --fast); exit 0 only when
G1: sr13 reports 0 curated-window blinks with gap > bridge (1-step gaps within
    the shipped bridge are legitimately absorbed);
G2: sr15 reports 0 interior DEAD *runs* longer than the shipped bridge
    (run-based per §R: a fix shipping a K-frame bridge leaves runs ≤K);
G3: no curated window starts DEAD (the 8002 dead-at-t0 class; bridge can't fix);
G4: sr17 reports 0 centroid-vs-overlap region disagreements (§S tripwire).
NEARBY frames (object alive outside a static bbox) are report-only.
Baseline today (bridge=0): G1 FAIL(4), G2 FAIL(12 runs/19 frames), G3 FAIL(8002)
— validated frame-for-frame against the §K/§L.1 findings; G4 PASS. At bridge=3,
G2 already PASSes; nothing short of (bridge + lifetime-gate) gets exit 0.
The gate self-pins lane generation + events_sha — before/after-regeneration runs
are attributable (§T). `--fast` skips G1; a --fast PASS is NOT full acceptance.

## Artifact safety
Do NOT reuse tmp/blink_* scripts (uint8 fancy-index bug, §P). Corrected
classifier: scripts/review/blink_class_full3.py.
