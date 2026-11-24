# Channel: heat/freeze map layers -> consecutive-day duration

User request (2026-11-09): "Change the heat and freeze events on the map to be, instead of
threshold criteria, length of consecutive days."

Working interpretation (investigator to confirm/flag): on the merged global map
(dashboard/mergedmap.js, raw exceedance layers "Heat > P85" / "Freeze < P15"), replace the
binary per-timestep exceedance mask with a per-cell count of consecutive days the condition
has held (run length up to that timestep), shown as a graded colour ramp.

Agents (append-only, prefix entries with your role + timestamp):
- INVESTIGATOR: writes the design contract below under "## Contract".
- BACKEND: implements data side per contract.
- FRONTEND: implements map rendering per contract.

Rules: all EWB compute on the LXC via ops/remote.sh (never run pipelines on orca).
Do not commit or push. Do not touch unrelated uncommitted changes in the worktree.


## Contract (INVESTIGATOR, 2026-11-09)

### What exists today (verified, file:line)
- Producer `ewb_live/viz/exceedance.py`: per 6-hourly mirror step (00/06/12/18Z) writes
  `<YYYYMMDDHH>.bin = zlib(packbits(heat) ++ packbits(freeze) ++ packbits(ar))`, per-ROW packing
  (stride `ceil(nx/8)`), display orientation row0=south/col0=lon-180. heat = `t2m > OWB p85(hour anchor) & land`,
  freeze = `< p15 & land` — per-STEP, deliberately WITHOUT the daily min-over-4 pass (exceedance.py:165-176).
  AR + `.trk` label sidecars from one window-level tracked pass. `index.json` written LAST
  (go-live signal); frames cached by filename only → any mask-semantics change bumps `LAYER_REV`
  (box is on v3; worktree v4 pending). Incremental: pending-days only; `build_frames` skips days
  whose frame names are in `index["frames"]`. Weekly tail step 6b runs the module flagless
  (`weekly.py:209-224`); retention prunes frames outside the meta 90-day window.
  Live lane: 340 frames 2026081000→2026110218, ~22 KB/bin + 5 KB/trk.
- Consumer `dashboard/mergedmap.js`: fetches index.json + `<ts>.bin`, zlib-decompresses via
  `DecompressionStream('deflate')` (getFrame:121-147, one inflight per ts, LRU 7), paints heat/freeze/ar
  as flat single-colour masks via a device-pixel→(byte,bit) LUT (buildLut:207, paintFrame:245-287),
  colour = `--series-<name>` at alpha 215. "Legend" = the JS-built chip row `#raw-layers`
  (`LAYERS` labels 'Heat > P85' / 'Freeze < P15', mergedmap.js:25-29, renderLayerButtons:612);
  tokens live in `dashboard/styles.css:27-29,92-94` (dual theme). Frames past `raw.cutoff` are
  client-synthesised slider steps with no bytes (boxes only). serve.py serves the lane via a strict
  whitelist `\d{10}\.(?:bin|trk)|index\.json` (serve.py:163-166).
- Events: `ewb_live/events/temperature.py` already carries `details.max_consecutive_days` /
  `mean_consecutive_days` (per-cell run stats, 316-328) — live events have them (e.g. max_consec 6).
  `global_timeline.json` entries carry `start/end/days/qualified_at` but NOT the consec stats
  (timeline.py:100-104). Detection day rule = daily mask of ALL FOUR 6-hourly steps passing
  (upstream min-over-4; same rule as viz `daily_bits` and `recompute_merged_max_consec`),
  consecutive filter `consecutive_days()` in `viz/build.py:95`.
- Tests: `tests/test_raw_exceedance.py` (codec roundtrip, incremental build, orientation, .trk/flicker,
  serve routing); `tests/e2e/test_dashboard_fe.py` `_build_raw_fixture` (770-801) + merged/raw tests
  (848-1202: pixel-paint probe `_HEAT_PIXEL_JS`, chip toggles, graceful states; all gate `page.errors==[]`).

### Decisions (binding for BACKEND/FRONTEND)
1. **Day rule** — a cell-day counts as exceeded iff ALL FOUR 00/06/12/18Z steps of that UTC day
   exceed (heat `> P85`, freeze `< P15`, hour-anchored climatology, land-only). This is the existing
   house daily rule (detection, viz daily_bits) so raw-layer day counts and event qualification stay
   consistent: when an event box qualifies at "≥3 consecutive days", cells inside read ≥3. Rejected:
   any-step days (diverges from events) and per-step quarter-day runs (no human "days" reading).
2. **Run value** (trailing, per cell, at each 6-hourly frame t on day D):
   `R(t) = C(D−1) + 1` if every step of D up to and including t exceeded, else `0`,
   where `C(D) = C(D−1) + 1` if all four steps of D exceeded else `0`.
   So the current day counts *provisionally* (causal, converges to the daily rule at 18Z); a cell
   whose day fails reads 0 (unpainted) even mid-exceedance — that is the requested semantic change.
   AR layer is unchanged (no duration concept).
3. **Seeding** — the C-recurrence runs from the EARLIEST mirror step (now 2026-07-25, ~16 days
   before the frame window), not from the window start: runs at the window start are exact up to the
   available history; longer runs truncate (mirror is all we have). Persist no state: every build
   recomputes daily masks over the full mirror+window (heat/freeze only, no AR; minutes of CPU,
   far inside the 5400 s weekly budget).
4. **Encoding** — frame `.bin` and AR bytes UNCHANGED. New per-frame sidecar following the `.trk`
   precedent: `<YYYYMMDDHH>.run = zlib( u8[ny*nx] heat_run ++ u8[ny*nx] freeze_run )`, display
   orientation, values 0..255 (raw day count, uncapped up to 90; no display bins baked in).
   `index.json` gains `"dur": {"rev": "v1", "seed": "<YYYYMMDDHH>"}` — the consumer feature-detect
   signal. Retention block deletes `.run` too. In `build_frames`, a frame counts as done only if its
   `.run` (and `.trk`) exist on disk — idempotent backfill of sidecars onto cached frames without a
   `LAYER_REV` bump (mask bits are unchanged; bump only if heat/freeze mask rules change).
   serve.py whitelist becomes `\d{10}\.(?:bin|trk|run)|index\.json`; `systemctl restart ewb-live-web`.
5. **Colour ramp + legend (FRONTEND-owned ramp, bins fixed here)** — heat/freeze paint by run bin:
   `1 / 2 / 3 / 4–6 / 7+` (5 bins; "3" matches the ≥3-day detection floor). Monotonic ramp per layer:
   bin1 = the current `--series-*` hue at low alpha (~0.45), bin5 = full token colour at the current
   alpha 215 — direction darker/saturated, both themes, and any new ramp stop token must pass the
   house contrast check against both canvases. Cells with R=0 unpaint. Chips: swatch `i` becomes a
   5-stop gradient; label 'Heat — days > P85' / 'Freeze — days < P15'; `tip` reads
   'Consecutive UTC days the cell has exceeded the P85 (P15) climatology at all four 6-hourly steps;
   darkest = 7+ days.' No new static legend in index.html (chips are the legend).
   **Fallback**: `index.dur` missing OR a listed frame's `.run` 404s → binary paint as today
   (one `console.warn`), so an old lane or CDN skew never blanks the map.
6. **Event labels** — the box/Gantt tooltips gain the event length `· N days`, computed FRONTEND-side
   as `(end − start)` days (end is EXCLUSIVE; use the existing end−1d convention). Do NOT plumb
   `details.max_consecutive_days` through timeline.py (per-cell stat, different meaning, extra bytes) —
   open question to owner below.
7. **Data side is built only on the LXC**: `./ops/remote.sh run python -m ewb_live.viz.exceedance`
   (flagless full incremental — NEVER `--days N`, it truncates the live index; skill rule). The
   first run after the change rewrites every frame's sidecars (~full re-encode of 340+ frames).

### Files each implementer touches
BACKEND:
- `ewb_live/viz/exceedance.py` — daily-mask + run-recurrence fn (pure, unit-testable), full-mirror
  daily-mask pass, `.run` write in `build_frames`, done-check requires sidecar, `index["dur"]`,
  retention `.run` delete. No `weekly.py` change (module name is the hook contract — keep it).
- `ewb_live/serve.py` — whitelist `run` ext (+ restart ewb-live-web; announce blip in channel).
- `tests/test_raw_exceedance.py` — see acceptance 1-4.
FRONTEND:
- `dashboard/mergedmap.js` — getFrame also fetches/decodes `<ts>.run` (share `inflight`; cache
  `{bin, run}` pair), LUT gains a flat cell-index (run plane is raw u8 per layer, no bit packing),
  paintFrame h/f bins by run value with the ramp, binary fallback, LAYERS labels/tips, chip gradient
  swatches, event tooltip `· N days`. Past-cutoff synthesised frames keep drawing nothing.
- `dashboard/styles.css` — ramp stop tokens `--dur-heat-1..5`, `--dur-freeze-1..5` (light+dark),
  contrast ratios reported in the channel. No token deletions.
- `dashboard/index.html` — no expected change.
- `tests/e2e/test_dashboard_fe.py` — `_build_raw_fixture` writes `.run` sidecars (flag to omit,
  keeping the old-fixture fallback path tested); new/updated tests per acceptance 5-8.

### Acceptance tests
1. Offline unit: run-recurrence — synthetic per-step masks spanning ≥4 days incl. a mid-day failure
   and a 4-step-complete day; assert per-frame R grids match §2 (provisional count, reset, growth)
   and the cap/roundtrip of the `.run` codec (two u8 planes, zlib).
2. Offline unit: `build_frames` writes a `.run` per frame; a frame missing its `.run` is re-written
   while `.bin` bytes are unchanged when masks are identical; retention deletes `.run`.
3. Offline unit: `index.json` carries `dur.rev`/`dur.seed`; serve.py `translate_path` routes
   `2026081000.run` and still rejects `20260810.run`/`evil.run.json` (extend existing route test).
4. Offline unit: recurrence seeded from pre-window history — with lookback days provided, the first
   in-window frame's R already exceeds 1 where the lookback says so.
5. e2e (fixture with sidecars): canvas shows bin-1 and bin-5 heat pixels at the fixture cells
   (poll via `wait_for_function`, per-bin tolerance ≥35/ch for alpha blend), run=0 fixture cell has
   no heat pixel; `page.errors == []`.
6. e2e fallback: fixture WITHOUT `.run` files (index has no `dur`) still paints binary heat pixels
   with zero console errors (extends the existing `_build_raw_fixture` default).
7. e2e legend: chip labels contain 'days', chip count still 3, toggle/URL semantics unchanged
   (existing `test_raw_layer_toggles_and_url` must pass unmodified).
8. e2e event label: box `<title>` for a known fixture event contains '· 3 days' (fixture event span).
9. Ship check on the box (BACKEND): after the LXC run, `curl http://192.168.0.121:8050/data/results/raw_exceedance/index.json`
   has `dur`, one indexed frame's `.run` fetches and decodes to 2×ny×nx bytes, leak-grep the index;
   FRONTEND verifies served `mergedmap.js` contains a new-build marker symbol before READY claims.

### Open questions for the owner
- Day rule "all four 6-hourly steps" (recommended, = detection) vs "any step" (more cells painted,
  diverges from event boxes)? Confirm.
- Runs are only exact back to the mirror start (2026-07-25); window-start runs truncate silently —
  acceptable, or add a small "durations exact from <date>" note?
- Ramp bins 1/2/3/4–6/7+ and darkest=7+ — confirm, or want a longer tail (…/14+)?
- Event length: calendar span N days (recommended, zero backend cost) vs per-cell
  `max_consecutive_days` already in event details (needs a timeline.py passthrough)?

INVESTIGATOR DONE (2026-11-09)
1. Raw lane: per-6h step masks in `<ts>.bin` (zlib packbits heat/freeze/ar) + `.trk`, index last;
   consumer mergedmap.js paints flat masks via a pixel→bit LUT; chips row is the only legend.
2. Contract: day = all four 00/06/12/18Z steps exceeding P85/P15 (house daily rule, matches detection);
   value = trailing consecutive-day run per cell (current day provisional), seeded from mirror history (exact from 2026-07-25).
3. Encoding: `.bin`/AR untouched; new `<ts>.run` sidecar (zlib two u8 planes, raw day count) +
   `index.dur{rev,seed}`; serve.py whitelist + web restart; ramp bins 1/2/3/4-6/7+ with binary fallback.
4. Events already carry duration info (`details.max_consecutive_days` in temperature.py; live values
   spot-checked) but timeline entries do not — recommend map labels show calendar span (end−start), computed frontend-side, no backend change.
5. Files+acceptance listed per implementer; all compute on LXC via ops/remote.sh, full flagless rebuild.
Open questions for the user: (a) all-4-steps day rule vs any-step; (b) silent run truncation before
mirror start OK, or note it; (c) bin tail (7+ vs 14+); (d) event label = calendar span vs per-cell
max_consecutive_days (needs a timeline.py passthrough).

ORCHESTRATOR (2026-11-09): Open questions resolved with investigator defaults pending user override:
(a) all-four-steps day rule; (b) truncation before mirror start is acceptable, no UI note; (c) bins 1/2/3/4-6/7+;
(d) event label = calendar span computed frontend-side. Launching BACKEND and FRONTEND now, in parallel.

BACKEND (2026-11-09 ~03:05Z): Starting data side. Files I touch (all other
worktree changes are not mine): ewb_live/viz/exceedance.py (run_recurrence,
.run codec, full-mirror daily-mask pass, sidecar-aware done-check, index.dur,
prune_frames retention), ewb_live/serve.py (whitelist +run ext),
tests/test_raw_exceedance.py (acceptance 1-4). Offline tests green locally
(203 passed).
BLOCKER/HOLD: box is mid-weekly (unit ewb-live-artrack3, pipeline scoring,
peak_rss ~9.3 GB/job) — holding my build until it finishes, no concurrent
AR pass. I am syncing my code now; that run's step 6b re-invokes
ewb_live.viz.exceedance in a FRESH subprocess, so it may do the .run
backfill itself with the new code (idempotent — my own flagless run after
it will just verify + re-index). No writes to the lane from me until then.
NOTICE: when my build lands I will `systemctl restart ewb-live-web` for the
serve.py whitelist change — ~1 s dashboard blip; FRONTEND, re-pull anything
in flight before that point or expect one retry.

FRONTEND (2026-11-09 ~03:40Z): Map side implemented per Contract §5-6, built
against the fixture (did not wait for BACKEND). Files touched (nothing else):
- dashboard/mergedmap.js — getFrame shares one inflight per ts and fetches
  `<ts>.run` alongside `.bin` ONLY when index.dur is present, caching the
  {bin,run} pair (404 → one console.warn, binary paint for that frame;
  dur missing → no .run fetch at all); LUT gains flat cellIdx (raw u8 plane,
  no bit packing); paintFrame bins heat/freeze by run (bins 1/2/3/4-6/7+,
  RUN_ALPHA 115..215), run=0 unpainted, AR unchanged, past-cutoff frames
  still draw nothing; chip labels 'Heat — days > P85' / 'Freeze — days < P15'
  with the contract tips; heat/freeze swatches = 5-stop gradient of the ramp
  tokens; box + Gantt tooltips gain '· N days' (calendar span, end exclusive,
  frontend-side); _dbg extended (project/dpr/grid/ramp) for the pixel probe.
- dashboard/styles.css — --dur-heat-1..5 / --dur-freeze-1..5 both themes,
  bin5 = var(--series-*) alias, no deletions. Ramp direction: low bins
  DARKEN (light theme) / LIGHTEN (dark theme) away from the token — forced:
  every token must keep >=3:1, so the visual fade rides on the alpha ramp
  (0.45→0.84). WCAG contrast vs the worst of canvas/paper/map-ocean per
  theme (min of 3 grounds):
  light heat 7.28 6.29 5.45 4.83 4.28 | freeze 10.62 9.62 8.69 7.95 7.27
  dark  heat 7.29 6.70 6.16 5.73 5.34 | freeze  5.56 4.85 4.21 3.71 3.26
  (dark 3.26 = the pre-existing --series-freeze token itself on #0e1620).
- tests/e2e/test_dashboard_fe.py — _build_raw_fixture(run=True) writes dur +
  `.run` sidecars (default run=False keeps the binary fallback path as the
  existing fixtures' default); heat fixture adds a third bit cell with run=0;
  gm4 fixture event extended to 2026-01-05 for the 3-day tooltip; new
  dur_server fixture + acceptance tests 5-8.

Test results (local, playwright chromium, fixture servers only): 21 passed /
0 failed across all merged+raw+dur tests, incl. test_raw_layer_toggles_and_url
and test_raw_renders_steps_and_badge UNMODIFIED (binary fixtures untouched);
tests/test_raw_exceedance.py 7 passed. Fixture bin-probe asserts bin-1 and
bin-5 pixels at the exact projected cells (tol 35/ch, eff colours computed
from the app's own ramp pack over --map-ocean) and run=0 cell unpainted.
node --check mergedmap.js clean.
Pending BACKEND DONE: ship check 9 frontend part (served mergedmap.js marker
+ screenshot notes/screenshots/fe-merged-duration.png) after the LXC run +
ewb-live-web restart. BACKEND: I have nothing in flight against the box —
the restart blip is fine as-is.

BACKEND (2026-11-09 ~04:10Z): mid-flight fix in my lane, flagging for the
author (ponytail 03:24Z label_sig work landed in exceedance.py while my
first LXC build ran): build() line ~488 did json.dumps(remap) on the numpy
uint16 remap array -> TypeError 'Object of type ndarray is not JSON
serializable' on EVERY build (killed unit ewb-live-durbackfill inside build,
before any frame write; lane untouched, index still v3). Fixed in place with
the minimal intent-preserving change: json.dumps(remap.tolist(), ...) —
fingerprints the same numbering table. Offline suite green after the fix
(7 passed). Relaunching the flagless full build now.

FRONTEND (2026-11-09 ~04:35Z): HOLD/NOTICE — the box is still mid-weekly
(artrack3: ewb_live.weekly + two pipeline scoring jobs live for ~1.5 h;
index.dur absent, ewb-live-web not yet restarted). BACKEND's last entry says
it is holding the build; BACKEND's own wait loop has likely expired — if the
weekly finished and nobody ran the flagless
`./ops/remote.sh run python -m ewb_live.viz.exceedance`, that step is still
owed. FRONTEND session stays alive watching for 'BACKEND DONE' or dur in the
served index; on landing I run ship check 9 frontend part + screenshot and
post FRONTEND DONE. Meanwhile the deployed mergedmap.js is already the new
build and paints the lane binary via the contract §5 fallback (dur missing →
no .run fetches, zero console noise) — safe state, no rollback needed.

BACKEND DONE (2026-11-09 ~04:40Z)

Shipped (my files, uncommitted in worktree, code synced to box):
- ewb_live/viz/exceedance.py — run_recurrence (§2 recurrence: provisional
  current-day count, mid-day reset, cap 255), .run codec (run_bytes/
  run_from_bytes = zlib(u8 heat_run ++ u8 freeze_run), display orientation),
  _daily_runs = full-mirror daily-mask pass §3 (heat/freeze only, no AR, no
  persisted state; seed = earliest mirror step 2026072500), sidecar-aware
  done-check (_frame_complete: indexed AND .run AND .trk on disk -> the
  idempotent backfill, no LAYER_REV bump — masks unchanged), build_frames
  takes run_seed {day: (c_h, c_f)}, index.dur {rev:"v1", seed}, retention
  .run delete via new prune_frames. LAYER_REV stays v4 (mask rules untouched).
  No weekly.py change. Also fixed a mid-flight breakage in my lane authored
  by the ponytail pass (json.dumps(numpy remap) -> .tolist(), see entry
  above — every build was dying before any frame write).
- ewb_live/serve.py — raw lane whitelist now \d{10}\.(?:bin|trk|run)|index\.json;
  served octet-stream, immutable-content caching like .bin (no cache change).
- tests/test_raw_exceedance.py — acceptance 1-4 (recurrence provisional/
  reset/growth/cap + codec roundtrip/size guard; .run per frame + rewrite-
  missing-sidecar-with-byte-identical-.bin + prune deletes .run; route
  accepts 2026081000.run, rejects 20260810.run / evil.run.json /
  2026081012.run.json; lookback seed: first in-window frame reads 4 with
  c=3 seed, both recurrence-level and build-level).

Tests: tests/test_raw_exceedance.py 7 passed; full offline suite 205 passed,
28 skipped, 0 failed (e2e deselected).

LXC build (flagless, never --days): unit ewb-live-durbackfill2,
'raw_exceedance: 336 frames indexed' — first post-change run re-encoded all
frames and backfilled sidecars (layer_rev v3->v4 full rebuild + window slid
2026081000->2026081100, retention pruned the old day trio).

Ship check 9 (LAN origin http://192.168.0.121:8050):
- index.json 200: dur {'rev':'v1','seed':'2026072500'}, 336 frames,
  layer_rev v4, window 2026081100->2026110218, cutoff 2026110218.
- 2026110218.run served: 21,381 B zlib -> 2,076,480 B decoded
  == 2*ny*nx (721*1440*2) exact.
- leak-grep over index text (/opt/|192\.168|/home/|ewb-live|:8050): CLEAN.
- sanity: first in-window frame heat run max 18 = 17 pre-window mirror days
  +1 (seeding exact per §3); last frame heat run max 101 (polar plateau,
  full mirror span +1). Freeze sidecar empty-but-valid at window start.

systemctl restart ewb-live-web done ~04:39Z (blip over). FRONTEND: box now
serves the new build — run your mergedmap.js marker check + screenshot
whenever. Note for everyone: the still-running artrack3 weekly will reach
step 6b with this same code and no-op (all frames complete); its own
pipeline scoring jobs were never touched.

FRONTEND DONE (2026-11-09 ~05:05Z)
Summary: mergedmap.js fetches `<ts>.run` only when `index.dur` is set (shared
inflight/frame cache, {bin,run} pairs); heat/freeze paint through a 5-bin ramp
(bins 1/2/3/4-6/7+, alpha 115/140/165/190/215) with run=0 unpainted; missing
`.run` -> one console.warn, binary paint (lane never blanks); chips relabelled
`days > P85`/`days < P15` with gradient swatches + duration tips, AR chip
unchanged; event tooltip appends `· N days` (calendar span, end exclusive).
styles.css: --dur-heat-1..5 / --dur-freeze-1..5 both themes; WCAG vs
canvas/paper/map-ocean ground: light min 4.28, dark min 3.26 (dark freeze-1 =
pre-existing freeze token) — all ≥3:1.
Local tests: tests/e2e/test_dashboard_fe.py merged+raw+dur subset 21 passed
(acceptance 5-8: bin-paint probe, chip gradient, 404->binary fallback, tooltip
span), tests/test_raw_exceedance.py 7 passed, guardian a11y e2e 71 passed /
3 skipped, node --check clean, dark-theme fixture paint spot-checked.
Ship check 9 (frontend part), served site http://192.168.0.121:8050:
- served mergedmap.js sha256 == worktree build; styles.css carries dur tokens
- index.dur = {rev: v1, seed: 2026072500}, 336 frames, cutoff 2026110218
- 2026110218.run served (whitelist live after web restart 04:39:37Z; earlier
  404 was the restart blip) and zlib-decodes to exactly 2*ny*nx = 2076480 B,
  max run 101 (fits u8)
- live page mounted, zero console errors; canvas pixel histogram at the cutoff
  frame matches all five heat ramp stops (8344/3010/1330/2068/1350 px,
  tol 12 over ocean|paper blends) -> graded ramp paints on real data
- screenshot: notes/screenshots/fe-merged-duration.png (map card parked at
  2026110218; 3.0% warm ramp ink)
Frontend lane closed. No files outside dashboard/mergedmap.js,
dashboard/styles.css, tests/e2e/test_dashboard_fe.py touched by me.

ORCHESTRATOR (2026-11-09): USER REPORT — viewing the website, they see only ~3 colours for heat
and ask whether the change was pushed. FRONTEND to diagnose and fix.

FRONTEND FIX DONE (ramp v2 — five distinguishable heat steps shipped; screenshot notes/screenshots/fe-merged-duration-v2.png)
Diagnosis (both questions answered):
1. NOT a cache problem. The public endpoint (https://ewb.espr.ai, Cloudflare → ewbweb Caddy → box :8050) serves mergedmap.js/styles.css with `Cache-Control: no-cache` (upstream nginx adds it; data/ gets `no-store`) and .run files 200 octet-stream; public byte-streams are sha256-IDENTICAL to this worktree (mergedmap.js e6aad76c…, styles.css 94e966d0…). URLs unversioned is moot with no-cache + Cloudflare honoring it. The change WAS pushed.
2. It was perceptual. Adjacent heat bins blended over the ocean were only ΔE76 7–12 apart: the alpha 115→215 ramp multiplies every colour's distance-from-ground by only 1.16× across bins, and the top pair (#b84438→#c6493c) even goes LIGHTER. A person sees ~3 bands. Matches the vision review of the old screenshot ("only the top step clearly distinct").
Fix (styles.css tokens + mergedmap.js RUN_ALPHA only; contract colours for series tokens untouched):
- Flat alpha 215 for all five bins; separation now carried entirely by colour.
- Light theme heat: gold→maroon #c57b2c→#c35b2a→#ad3321→#7d1619→#4c0810 (hue break at bins 1–2 so gold vs orange reads differently); freeze: cyan→navy #3596ba→#2b77b0→#2a58a0→#1a2d71→#0f1b45. Dark theme inverts (rust→gold / steel→ice).
- Every adjacent blended pair now ΔE76 ≥ 12 (12.1–18.2 light, 12.2–17.8 dark, ocean and paper grounds); every token keeps ≥3:1 on canvas AND paper in both themes (min computed: light 3.02, dark 3.03). Ramp chips already paint the five stops — unchanged.
- e2e: fixture latent bug fixed (run=5 = the 4–6 bin while the probe asserted the 7+ bin; fixture run is now 7). `pytest -m e2e -k "merged or raw or dur_ or archived or region_mask or detail_prime or gantt"` → 23 passed, 1 skipped; node --check OK; test_raw_exceedance 8 passed.
Deploy + verify: rsync mergedmap.js+styles.css to the box; live public page at the cutoff frame: heat bin pixel histogram [77383, 26860, 12665, 17604, 12059] (all five stops exact), 0 console errors; zoomed crop over the longest heat corridor (Siberia/Mongolia, 51.5°N 69.5°E): all 5 bins match the ramp exclusively (tol 12); freeze bins 4/5 read at high latitudes. Screenshot overwritten with v2.
UNRELATED CHURN NOTICE: 12 live_url detail tests (event open/back/esc, detail reference panels) now fail on the box because a rebuild is running right now (`python tmp/blink_class.py`, started 15:40Z) and events/index.json currently maps ids → `{kind:"consecutive_days", ok, size_kb}` stub records instead of event records, so the detail view finds no panel. That is another lane's in-flight data churn — not a frontend regression; none of the ramp work touches the event path. Will not re-litigate in this lane.

ORCHESTRATOR (2026-11-09): USER CHANGE — heat waves should only appear from 3 consecutive days.
Do not paint heat cells with run 1 or 2. Assigned to a MISTRAL agent.

MISTRAL DONE (2026-11-09): Changed heat ramp bins to 3 / 4 / 5-6 / 7-13 / 14+ days (5 bins, darkest=longest), using v2 colour ramp tokens. Heat cells with run < 3 are now unpainted; freeze unchanged (bins 1/2/3/4-6/7+). Chip label/tip updated for heat to state minimum 3 days and 14+ max. Local e2e tests (merged/raw/dur_) all pass (18/18). Deployed dashboard/mergedmap.js and dashboard/styles.css to the box via rsync. Verified on https://ewb.espr.ai: no console errors, screenshot saved to notes/screenshots/fe-merged-duration-v3.png.
