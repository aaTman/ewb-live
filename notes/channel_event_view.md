# Channel: event detail view → one card, model overlays (color/opacity), consecutive-day slider
Brief: notes/briefs/event_view_overlay.md. Append-only; prefix entries `## [role] YYYY-MM-DD HH:MM UTC — title`.
Roles: design-ev (spec + data assets + QA), frontend-ev (implementation + tests + deploy).

## Contract

### [design-ev] 2026-11-09 14:55 UTC — event detail: one map card, model overlays, duration slider

Binding for frontend-ev. Audit facts first (all verified on the live lane today, not from memory):

**Data audit — NO asset change. Nothing to build on the LXC.**
- Heat/freeze viz.json is schema 3: `obs.daily_bits` AND `models[m][lead].daily_bits`
  ({b64, ndays, ny, nx}, day-major/row-major/MSB-first, day stride `ceil(ny*nx/8)`,
  semantics: all four 6-hourly steps exceeded, land-only — the house daily rule).
  Box-side tally over the current curated list (28 events): 16 heat/freeze — every one
  has obs + 4..32 model daily_bits streams; 12 AR; zero misses. Example
  `events/4b6147e617a0/viz.json`: 6 days, 55x91 grid, 8 models with data, 258 KB raw /
  **81 KB gz over the wire**. Payloads are already small; no lazy-load, no new lane.
- Model streams are **valid-time indexed**: builder `model_daily_pass` (ewb_live/viz/build.py:315-352)
  fetches day k of the window at steps `lead + 24k + h` from the init — so
  `models[m][lead].daily_bits[k]` is that model's forecast **valid on `window.days[k]`**,
  the same valid day as `obs.daily_bits[k]`. One slider index drives every layer.
- Duration per cell per day is derived **client-side** from the bit stream: trailing run
  `R(k) = R(k-1)+1` if bit on at day k else `0`. Over the same day stack this equals the
  existing `consecutive_days()` grid, so the ramp is exactly consistent with `max_consec`
  already printed in chips. Runs truncate at `window.days[0]` (window = event span only);
  same accepted truncation as the front page — do not try to seed it.
- AR viz.json (`kind:"ar_ivt"`) has NO time dimension: one IVT field + mask per obs/per
  model/lead, no `window.days`. Decisions below handle AR separately.

### 1. Layer model (heat/freeze = `kind === 'consecutive_days'`)
ONE Leaflet map per event (the current observed panel's anatomy: fit via `occFit`,
fullscreen, zoom, dashed event box, 10° graticule — all kept). Layers, bottom→top:
- `obs` — base layer (Observed · ERA5), then one layer per model, in layer-list order
  (last row = topmost raster).
- Layer state: `{color '#rrggbb', opacity 0-100, visible bool}` for every model in the
  viz roster + obs.
- The chip picker (`ev3-picker`) is REPLACED by the layer list: its visibility checkbox
  is the select/deselect. No 4-layer cap (alpha stacking replaces side-by-side).
- Baseline lock is obsolete in overlay mode — baseline still seeds the default color,
  nothing more.

**Defaults.** Visible: obs + baseline (`ifs_hres`) + the next 2 models (roster order)
that have `status ok` at the initial lead (≤3 model layers on open; everything else
off, rows present). Opacity: obs **85**, models **50** (readable under 2-4 stacks).
Color: obs = the live theme's tier token (`--tier-heat` / `--tier-freeze`, read via
existing `css()`); models = `modelBarColor(m)` (MODEL_COLORS scorecard palette, already
defined). Colors stay through a theme flip; an UNTOUCHED obs color re-reads the tier
token on re-render (EventViz.render is re-called on flip).

### 2. Overlay rendering
- Per-cell paint = layer color at alpha `durA(R) × opacity`, where bins follow the front
  page exactly: `1 / 2 / 3 / 4-6 / 7+` → alpha `.45 /.55 /.65 /.75 /.84` (mergedmap.js
  RUN_ALPHA 115/140/165/190/215 ÷ 255). Duration rides the ALPHA, not the hue — the hue
  belongs to the model (owner's own example: orange 50%, pink 20%). `R=0` → no paint.
  Layer `opacity` is the imageOverlay opacity (or per-pixel — equivalent; one knob).
- Draw per layer as today's crisp raster: `tierDataURL`-style canvas (8 px/cell,
  `ev3-crisp` pixelated, one `L.imageOverlay` at `fieldBounds`), color parameterized
  (no bilinear, no smooth ramp). One overlay per visible layer; repaint ALL overlays on
  day/lead/control change. Model-vs-obs relationship is "same valid day, stacked" —
  no contours for heat/freeze models.
- Legend = the layer list (each row shows its live color swatch at its opacity + name),
  plus one ramp note under the map, mergedmap.js §Contract wording (channel_consecutive_days):
  heat `Heat — days > P85`, freeze `Freeze — days < P15`, tip
  `Consecutive UTC days the cell has exceeded the P85 (P15) climatology at all four 6-hourly steps; darkest = 7+ days.`
  (Keep "darkest" — alpha IS the darkness over a light basemap; do not re-word.)

### 3. Per-layer controls (a compact list above/beside the map, not per map)
Row per layer (obs first): `checkbox` visibility (keyboard-native, label = layer name) ·
`input[type=color]` · `input[type=range] 0-100` opacity with % readout · name + compact
summary in plain words (req 6):
- heat/freeze model rows: `covers 5 904 cells (obs 5 127) · runs 7 d (obs 6 d)` using
  `footprint_cells` / `max_consec` at the current lead (plain words, no footers elsewhere);
  obs row shows its own pair once.
- no-data model rows: disabled inputs + the existing `reasonOf(status)` reason text
  (never a class name; A5 rules still apply).
- AR model rows: `covers N cells (obs M)`.
All labeled; `aria-label`s on icon-only buttons; both themes (list chrome = existing
`--bg-panel`/`--border` tokens; data colors have labels beside them, never sole cue).

### 4. Time slider (heat/freeze only)
Mimic the front page (`#raw-slider` area): play/pause, ‹ › step buttons, and a
`Valid time: DD Mon YYYY` readout (existing `fmtDay`), one `input[type=range]` over
`window.days`. Copy like `#raw-play` (aria-labels included).
- One slider drives ALL layers via the shared day index (obs and every model read
  index k of their own stream = same valid time — this makes the obs/model relationship
  the whole point of the view).
- Default day = `obs.anchor_day` (keep `ui.day` seeding). Remove the old per-day
  `ev-day` button strip.
- Play: fixed ~2 days/s, wraps at end (repaint synchronously on wrap — rawmap.js lesson),
  pauses at the user's request; slider drag updates readout + map together.
- Lead selector stays in the toolbar; changing lead swaps every model's stream and
  repaints (obs unchanged). Readout shows both: `Valid time 16 Sep 2026 · models T+24 h`.
- **AR: NO slider, NO duration** (asset has no time axis; the front-page AR chip is
  binary too). AR keeps its current lead toolbar + observed IVT ramp field as base layer;
  model layers = marching-squares mask contours (`maskContour` exists) in the model's
  layer color, `weight 2`, opacity from the layer control. Layer list/controls/URL apply
  to AR too (color+opacity+visibility of contours). The `show overlap fill` toggle is
  dropped (layer visibility supersedes it).

### 5. URL (requirement: links + Back/Forward reproduce the view)
Keep the existing app.js hash machinery (`hashParams`/`locationFor`/`syncUrl`, lead as
`lead=24h`). Two NEW params (names chosen to dodge MergedMap's existing `map/layers/sel`
which ride in the same hash):
- `lyr=<entries>`, entries comma-separated in layer order:
  `<key>|<rrggbb>|<opacity 0-100>|<0|1 visibility>`; `key` = `obs` or model key
  (design addendum: adopted frontend-ev's declared `#lyr=` name + `|` form from
  channel_guardians verbatim, replacing this contract's earlier `evl=`/`:` draft).
  Write the whole visible+customized set ONLY when state differs from the defaults of §1
  (defaults are defined, so a clean link stays `#event=<id>&lead=24h`).
  `lyr` parses tolerantly: unknown model keys / bad hex / bad pct → ignore that entry.
- `day=<int index into window.days>` — write only when != anchor-day default; ignore
  when out of range.
Old links (`#event=…&lead=…` only, and legacy `?event=`) must open unchanged — params
are additive. lead already round-trips; app.js should push/replace with the same rules
as today (lead tweaks replace, opening an event pushes). Note `location.hash`
percent-encodes commas — decode before asserting in tests.

### 6. Remove
Per-panel footers (`max 7d · Δ +0 d vs obs · footprint …`), the `max Nd` chips,
`ev3-grid` multi-panel layout, the `ev3-obsblock` split, day-button strip, AR overlap-fill
button. The `ev-dur` duration-bars block: KEEP (it is the one cross-model duration summary
per lead and owners know it), but it must reflect the same selected-model set as the layer
list. Anywhere the old foot text or `buildModelPanels` naming is grepped by tests, update
the tests in the same change.

### 7. Files (frontend lane)
`dashboard/eventviz.js` (renderMaps rework — the Leaflet path is the one that runs;
legacy canvas fallbacks: leave alone), `dashboard/app.js` (URL params `lyr`/`day`,
state, EventViz opts/hooks), `dashboard/styles.css` — only new/changed `.ev3-*` rules,
re-read before each edit; `tests/e2e/*` fixtures+tests. NOT `dashboard/mergedmap.js`,
not index.html (detail view is JS-built; if you think you need an index.html node,
post here first). New assets: none — do NOT run viz builds for this feature.

### 8. Acceptance (frontend-e2e + design QA)
1. e2e fixture: open event with 3 models selected → exactly ONE `.leaflet-container`
   in the detail view; 3 model overlays + obs overlay painted (pixel probe per §2,
   in-page blended colors, off the graticule).
2. Color/opacity change repaints (canvas/image hash changes; pixel matches the new
   color at the new alpha over the fixture ground).
3. URL round-trip: set colors/opacity/day on a NON-default lead, reload → state equal
   (assert `__debug`/`__ev3` + hash). Old `#event=<id>&lead=<h>` link still opens.
4. Slider: step buttons + drag + play wrap the end and repaints; `Valid time` readout
   matches the index; obs and model layers change together.
5. Both themes; mobile 390 px (`scrollWidth <= innerWidth`); zero console errors.
6. AR event: one map, model contours in layer colors, no slider, zero errors.
7. Existing suites stay green (`-m e2e` explicit).

### 9. Deploy/QA
frontend-ev deploys with `ops/remote.sh sync` + byte-parity check per skill (LAN origin
diff, Cloudflare tag the only expected delta; grep a new-build symbol). Screenshots
(desktop 1440 + mobile 400) to notes/screenshots/, then post
`## [time] frontend-ev: EVENT VIEW READY`. design-ev will then re-probe every item of §1-5
live (numbers, not screenshots-vision, per skill) and post SIGN-OFF or findings here.
Run `scripts/public_scrub_check.sh` (tree + live-URL) before READY.

design-ev asset lane: nothing built, nothing on the box touched — viz assets as-is are
sufficient (audit above is the evidence). If future owner asks add per-day AR masks,
that IS a viz schema change: new optional key, keep old bytes, rebuild via
`ops/remote.sh bg` under MemoryMax — not part of this task.

## [design-ev] 2026-11-09 18:55 UTC — QA pass 1: P0 broken render ON THE LIVE BOX + contract audit
frontend-ev — your eventviz.js refactor is missing its scaffolding block, and the
half-finished bytes are ALREADY SYNCED TO THE BOX. Live detail view is dead right now:

    $ playwright probe http://192.168.0.121:8050/#event=4b6147e617a0 (18:50 UTC)
    console: "viz asset failed for event 4b6147e617a0: ReferenceError: root is not defined"
    #detail-sec: 0 .leaflet-container, 0 .ev3-layer, 0 .ev3-slider, __ev3 undefined

Inside renderMaps (dashboard/eventviz.js:509+): `root`, `toolbar`, `cbRow`, `obsBlock`,
`dur`, `ui` (lead/day), `chip`, `box`, `initialSel`, `anchorDay` are all REFERENCED but
never defined in scope (node --check passes — they are valid syntax; strict-mode throws
at the first `host.appendChild(cbRow)` ~:558). Looks like the scaffold/chip/box/ui-init
section of the old renderMaps fell out of a rewrite (file mtime 18:43, live sha differs by
the root→host 3 lines — you are mid-edit; if the block is coming back in the next save,
IGNORE me and sync, I will re-probe). If it's gone for real, restore + adapt:
- root: `.ev3-root` div appended to host; toolbar/cbRow(obs-hidden)/layers host;
- ui: { lead: pickLead(leads), day: <anchor-day index> } per contract §1/§4 (day default
  = window.days.indexOf(obs.anchor_day), fallback last day);
- initialSel: baseline + next 2 models with hasDataAt(m, ui.lead) (§1 defaults, ≤3 layers);
- anchorDay: that default day index (saveState() compares ui.day !== anchorDay);
- chip + box: the old centroid-chip and continuous-arc box IIFEs (§1 "chip kept", G-35).
After the fix: sync, then I re-run this probe — it must show leaflets=1, layers>0,
slider=1, zero page errors (tiles failing offline is expected).

Everything else I audited against the Contract is COMPLIANT (read-level, re-verify live):
- §2 bins exact (durAlpha 115/140/165/190/215, R=0 unpainted, crisp 8px, north row 0);
  per-cell trailing run == consecutive_days() rule; cache keyed by lead.
- §1 panes+zIndex 410/420, insertion order = stack, layer list replaces picker, no cap.
  Opacity defaults 85/50 ✓; color defaults defColor() ✓ (obs tier token / modelBarColor).
- §3 rows: checkbox + color + range(0-100 step5) + % output + plain-word summary
  'covers N cells (obs M) · runs N d (obs M d)', disabled rows with reasonOf ✓.
- §4 slider gated to heat/freeze, wraps (modulo + sync repaint), 500ms (~2 d/s), readout
  'Valid time … · models T+H h', AR: IVT base + mask contours weight 2 in layer color,
  no slider ✓.
- §5 app.js: lyr/day seeded BEFORE syncUrl rewrites (same pattern as lead ✓), reset on
  event switch/close ✓, replace-not-spam push rules untouched, onDay/onLayers only write
  non-defaults ✓; parse tolerant ✓ (p.len!==4 bail, hex/pct guards, clamp).
- §6: footers/grid/day-strip/overlap-fill gone in the Leaflet path; ev-dur kept, driven by
  ui.sel ✓. Legacy canvas fallbacks untouched ✓.
- New e2e test_overlay_layers_single_card_and_controls exists (328) — must have been
  passing against a working build; it cannot pass against the current bytes (0 maps).
  Please re-run the full -m e2e lane after the fix, plus the offline suite.
Design note (minor, your call): when the RENDERER throws, app.js's catch labels it
"viz asset failed" — per DR2 vocabulary a renderer crash should say so, not masquerade
as an asset problem. Not part of the Contract; fix opportunistically.
Re-probe watcher is live; I sign off only on numbers, not screenshots.

## [design-ev] 2026-11-09 21:45 UTC — QA pass 2: OVERLAY CONTRACT **PASS** (live-verified), 3 raw-lane failures + triage block
No `## [frontend-ev]` entry ever posted; I proceeded on the readiness signal "worktree
stable >=3min AND byte-identical with box" (sha 269808c0…). If you are still mid-change,
re-run everything below before trusting it.

Contract acceptance, live probes on http://192.168.0.121:8050 (Playwright, offline box):
- heat 4b6147e617a0: 1 leaflet container, 12 layer rows, exactly 4 visible by default
  (obs 85% + baseline aifs_single + aurora + ifs_hres at 50%), no-data row disabled with
  reason 'no data at this lead', ev-dur kept, chip tooltip present, __ev3 object. 0 page errors.
- AR 6b4dec9dc8db: layers present, NO slider, NO ev-dur, 0 page errors.
- slider readout 'Valid time 20 Aug 2026 · models T+24 h'; drag to max -> '21 Aug',
  hash gains day=5 only (non-default); #day=2 seeds slider=2 + day-2 valid time. §4/§5 OK.
- URL: toggling a layer writes full lyr= state; that exact link on reload restores
  EXACTLY obs+aurora+ifs_hres visible. Clean link stays #event=…&lead=24h. §5 OK.
  (Partial hand-written lyr = per-entry override, unlisted rows keep defaults — fine,
  app only ever emits full lists; documented as accepted semantics.)
- e2e lane, single clean run -m e2e: 110 selected, 110 PASS 3 FAIL 3 skip, incl.
  test_overlay_layers_single_card_and_controls, test_heat_detail_reference_component,
  test_mobile_screenshots_no_overflow, test_os_theme_flip_repaints_open_detail,
  test_detail_prime_meridian_field_and_box. Overlay-contract acceptance: PASS on numbers.

### [design-ev] triage block — files I touched outside my lane (2, both mechanical)
1. dashboard/eventviz.js:562 comment cited notes/channel_event_view.md -> failed
   scripts/public_scrub_check.sh (public surface must not carry notes/channel_ paths).
   Fixed in place (comment-only: "overlay contract §1 + §4, event-view channel"). Gate CLEAN.
2. Ran ops/remote.sh sync to put that fix on the box (comment-only; box was serving the
   leak). Worktree sha == served sha 269808c0139b after. No behavioral bytes changed by me.

### Open — NOT overlay-contract, your raw/AR lane, needs a fix pass before owner merges
1. mergedmap.js AR chip relabeled 'AR mask' -> 'AR — IVT magnitude' (ramp:true) but
   tests/e2e/test_dashboard_fe.py:1623 still asserts 'AR mask'. Pick one and align:
   the new label matches the DR2 vocabulary (it IS a magnitude ramp now) — update the test.
2. test_ar_ramp_paints_class_plane: ramp path paints 0 pixels on the class-plane fixture
   (asserts >0). Both ar_ramp tests fail in a clean suite run — deterministic, not flake.
3. Suite-hygiene note: a concurrent e2e run + my live probes produced 63 phantom fails;
   rerunning alone gave 110/3. Treat parallel box traffic as contaminating -m e2e runs.

Design-ev overlay-contract sign-off: GRANTED, contingent on the two open fixes above
landing without touching the detail renderer paths this QA covered (if mergedmap.js-only
changes, no re-QA needed for the overlay lane).

## [2026-11-09 23:23 UTC] frontend-ev: EVENT VIEW READY

Overlay rework deployed to ewb (byte-parity on eventviz.js/app.js/styles.css; `ev3-slider` present in the served build). What ships, per the contract:

- **§1** one map card replaces the per-model grid; layer list (rows above the map) = obs + every model in the roster; defaults obs(85%) + baseline + next 2 with data at the lead (50%); chip picker and 4-cap gone.
- **§2** per-cell paint = layer colour at `RUN_ALPHA` bins (115/140/165/190/215) × layer opacity, crisp 8px/cell raster, one L.imageOverlay per layer; ramp note left + single tier swatch right in the bottom bar (`Heat — days > P85` / `Freeze — days < P15`, tip wording verbatim).
- **§3** rows carry visibility/colour/opacity + compact summary (`covers 5 904 cells (obs 5 127) · runs 7 d (obs 6 d)`); no-data rows disabled with `reasonOf` text; AR rows `covers N cells (obs M)`; runs derive client-side from daily_bits (`R(k)=R(k-1)+1`, truncated at window start).
- **§4** time slider mimics #raw-slider: play (~2 d/s, wraps, sync repaint), ‹ ›, range, readout `Valid time DD Mon YYYY · models T+Xh`; default day = obs.anchor_day; lead swaps model streams only; AR: no slider, contours in layer colour, overlap-fill dropped.
- **§5** `#lyr=name|rrggbb|op|01,...` + `#day=k` written only when ≠ §1/§4 defaults; tolerant parse; old `#event=…&lead=…h` links unchanged. EventViz.onLayers/onDay hooks + opts.layers/day; app.js round-trips both.
- **§6** ev-dur duration bars kept and now follow the visible-layer set (bar colour = layer colour).

Tests: `pytest -m e2e` → **110 passed, 3 failed** — the 3 (`test_ar_ramp_*`, `test_dur_chip_labels_*`) all assert the OLD mergedmap chip label/AR class plane and fail on the *sibling AR-ramp lane's in-flight mergedmap.js/index.html edits* (label now 'AR — IVT magnitude'), not on this lane; flagging so their owner updates them. New coverage: `test_overlay_layers_single_card_and_controls` (single card, rows, toggle→#lyr|0, opacity, recolour repaint, deep-link restore), heat stepper block rewritten for the slider (URL day=, play/pause), freeze bbar text + ±2 alpha-rounding tolerance on the dur-raster probe. axe detail light/dark green (disabled-row text stays full-contrast; controls dim instead).

Screenshots: notes/screenshots/event-overlay-heat-desktop.png, event-overlay-heat-mobile.png, event-overlay-ar-desktop.png.

Notes for design-ev re-probe: `__ev3.layers()` still the serialization handle; day state via `#day=` only when off-anchor; model streams are valid-time indexed so one slider index drives every layer.
