# Design review — EWB Live (round 2, design-review agent)

Reviewed live at http://192.168.0.121:8050 (live + `?data=mini` + `?data=mini-sparse`), Playwright Chromium at 1440/1280/820/390, light + dark. Screenshots: `notes/screenshots/dr-*.png`. Measurements: axe-core 4.10 injected, computed-contrast per scorecard cell, network traces, keyboard tab walk. Real scores land ~02:15 UTC; mini numbers are layout-only — this review is about the experience.

## Verdict

The bones are genuinely good: the OWB palette is cloned faithfully (all 14 tokens verified in OWB's production CSS), scorecard cells hit 12–15:1 contrast, the deep-link contract (`#event=<id>&lead=24h`) works, back returns to the list without reload, blue-is-better color semantics are consistent, and the ramps (YlOrBr / Blues / magma-IVT) are sequential and colorblind-safe. This is not a v1 anymore.

What holds it back from the Windborne/OWB bar is **trust and framing**. The product leaks its guts (`unavailable: ValueError`, `FileNotFoundError`, missing model rows with no explanation), its signature observed-vs-model figure drowns in a zoomed-out basemap that is 60% empty ocean, its duration comparison conveys literally nothing (all bars equal length, no axis, no reference line), and the two things every page tells you to do — "hover any cell", "click any row" — are impossible with a finger or a keyboard. Fix the two blockers and seven majors below and this becomes credible to a Brightband reviewer and useful to a model developer on their phone.

---

## Blockers

### B1. Touch and keyboard users are locked out of the core affordances
Evidence: `dr-probe-mobile-cell-tap.png` (tap on a scorecard cell at 390px does nothing); measured `mobile_cell_tap_tooltip_visible=false`; tab walk shows score cells have `role=null, tabindex=null`; event rows likewise. Code: `dashboard/app.js:716-746` binds tooltips to `mouseover/mousemove/mouseout` only. Meanwhile About instructs "Hover any cell… for raw value, baseline value, and n events", and the event table is the only way into an event page — its rows are `click`-only.
Why it matters: a third of reviewers will open this on a laptop trackpad-with-keyboard or a phone. On touch, the documented way to interpret a cell does not exist; with a keyboard, you cannot open an event at all. The tab order instead floods through 27 unlabeled `path.leaflet-interactive ""` stops on the catalog map before reaching anything useful.
Fix (spec):
- Tooltips: bind `focusin/focusout` alongside mouse; on touch (`pointerdown` with `pointerType==='touch'`), toggle the tooltip on tap, dismiss on next tap outside. Make each score cell `tabindex="0"` (roving tabindex per table row is fine) with `aria-label="aifs, duration error, −0.83 h, baseline −21.2 h, n 3"`.
- Event rows: `<tr tabindex="0" role="link">` + Enter/Space opens detail; add visible `:focus-visible` outline (reuse `--accent-focus`).
- Catalog map rectangles: set `interactive:false` or `keyboard:false` on the L.rectangles (the table is the accessible path), removing 27 unlabeled tab stops.

### B2. The product shows its error log and silently loses models
Evidence: `dr-detail-ar-mobile.png`, `dr-detail-heat-us-light.png` — reason lines read `no forecast at T+24 h: aifs (unavailable: ValueError)`, `pangu_weather_gfs (FileNotFoundError)`, and one variant renders `aifs(unavailable — not built)` (no space before paren). `dr-home-desktop-mini-sparse.png` — chip says `MODELS 7/11`, scorecard kv says `MODELS 11`, and the four data-less models simply vanish from every table with zero explanation.
Why it matters: this is the credibility surface for a Brightband reviewer and the *entry point* for a model developer ("where is my model?"). An exception class name is the universal signal that a tool is unfinished. A silently missing row reads as a bug, not as "no data".
Fix (spec): map failures to human reasons at the builder (`ewb_live/viz/build.py`) — e.g. `no 24 h forecast yet — model runs daily`, `archive lacks specific humidity (not supported for AR maps)`, `build failed for the Nov 3 run — pipeline log` — and have `eventviz.js:97,123` render the reason string verbatim without parenthesizing raw values; always emit a space before any parenthetical. In the scorecard, render data-less models as greyed rows with the metric cells as an em-dash and the reason in the tooltip, so visible rows always equal the chip count. Fix the `MODELS 11` vs `7/11` contradiction: the kv line should read `MODELS 7 scored · 11 in roster`.

---

## Majors

### M1. First load is ~6.3 MB, and opening an event pulls another ~7 MB — uncompressed
Measured: `plotly.min.js` 4.56 MB (eager `<script>` before first paint), `scores.json` 1.22 MB on every home load; in mini mode, opening **one event** fetches `mini/scores.json` 6.31 MB + `scorecard.json` 627 KB + `countries.json` 159 KB + per-event `viz.json` 90–270 KB. The server sends no gzip (`Accept-Encoding: gzip` returns identical sizes). Locally ready_s ≈ 0.8 s; over any real internet connection this is 10–30 s.
Fix: enable gzip/zstd in `ewb_live/serve.py` (JSON compresses ~8–10×); lazy-load Plotly only when the Scores fold is first opened (`import()` on toggle); fetch per-event slices instead of the full `scores.json` (the detail page needs one event, not 249). Target: < 500 KB to first paint.

### M2. The signature map frames empty ocean, not the event
Measured: AR-europe obs panel at 1440 renders a 133.9° × 62.2° viewport for an 82° × 35° event at zoom 3; on mobile (`dr-detail-ar-mobile.png`) the AR fills ~40% of one edge of a 370×430 panel — vision pass: "the data lives in a small dashed-orange inset inside a large gray map". The EWB paper figures and Windborne both frame the event; we frame the hemisphere.
Fix: `fitBounds` to the **field's occupied extent** (cells with nonzero value / mask), not the event bbox, padded 15%. Clamp min zoom so Mercator never drops below the point where continents stop being silhouettes (z≈4) — if the fit would go below, letterbox the field on the gray basemap rather than zooming out. For polar events (Greenland freeze), a Mercator view makes the data a smear near the pole; cap the top latitude or note the projection in the corner.

### M3. Dark-theme home map is an inverted OSM blob; detail maps are Esri gray
`dr-home-desktop-dark.png`: the catalog map is colorful OSM run through a CSS `invert` filter — dark featureless masses, no labels, wrong-looking grays; the detail pages use Esri World Light Gray (with its own dark treatment). Two basemap dialects in one product.
Fix: use Esri World Dark Gray Base tiles for dark mode everywhere (same provider as light, `server.arcgisonline.com/.../World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`), drop the CSS filter. Labels return, consistency returns.

### M4. `--ink-muted` fails WCAG AA everywhere it's used
Measured with axe-core on every page type: 7–29 `color-contrast serious` nodes per page, all the same token — `#8b8b7b` on `#fdfdfc`/`#ffffff` = **3.39–3.45:1** (needs 4.5 for 10.5–11.5px text); dark theme `#8b8b7b` on `#262626` ≈ 4.2:1. It's the most-used text color on the site: "updated …" header, table subtitles, captions, footer, column eyebrows.
Fix: light `--ink-muted` → `#6e6e60` (the existing `--ink-secondary`, 5.4:1) or `#71716a`; dark → `#a3a394` on `#262626` (≈ 6.9:1). One token change clears every axe violation on the product. Verify with the same axe run (script: scratch `dr_measure.py`).

### M5. The headline numbers have no units, and headers mangle the ones they have
Story line: `peak 32.6` (°C? K?), `peak IVT 1066` (kg m⁻¹ s⁻¹?). Event table Peak column mixes unit systems without labels: `1866` (IVT) next to `32.6` (°C) next to `−43.7` (°C) — vision pass flagged it as "inconsistent formatting". Worse, scorecard headers apply CSS `text-transform: uppercase` to the whole `<th>`: duration columns read `DURATION ERROR [H]` — capital H is a different unit (weber) — and `KG M⁻¹ S⁻¹`.
Fix: story labels `peak 32.6 °C`, `peak IVT 1066 kg m⁻¹ s⁻¹`; Peak column renders with unit suffix (`1866`, `32.6 °C`, `−43.7 °C`); add `.units { text-transform: none; }` to the `th` rule in `styles.css`.

### M6. The duration comparison conveys nothing
`dr-detail-heat-us-scores.png`: all four bars end at the same pixel because the scale max is the data max (all 3d) — no axis, no day ticks, no units, observed is just another gray bar with no reference line, and all model bars are series-orange while the scores table dots are blue/orange/olive, so bars can't be tied to models by color. The adjacent caption `max 3d (0) · footprint 1174 (obs 1202)` never explains the `(0)` (it's the duration Δ vs observed, in days).
Fix: fixed common scale `0 – max(7d, observed+2d)` with tick gridlines per day and axis label `days`; observed drawn as a dashed vertical reference line across all rows (label `obs 3d`) plus its own row; bars colored with each model's dot color; caption becomes `max 3d (Δ +0 d vs obs) · footprint 1174 cells (obs 1202)`.

### M7. Mobile event table silently clips its right-hand columns; chips are small
Measured at 390px: `#event-table` wrap has `scrollWidth 655 > clientWidth 356`, `overflow-x:auto` (it *is* scrollable) but lacks the `scrollable` fade class the scorecard wraps get — `Days`, `Peak`, region columns just vanish off the right edge with no affordance (`crop-mobile-table.png`). Chip/lead tap targets measure 28–32px tall vs the 44px guideline.
Fix: apply the same mask/fade `updScroll` logic to `.table-wrap.tall`; add a `drag horizontally for more →` micro-hint below the wrap at <480px; raise chip min-height to 36px with 44px hit area (padding), lead pills to 40px.

---

## Minors

1. Two max-duration definitions on one page: merged freeze story says `3d max run` while its own obs map badge says `max 4d` (`dr-detail-freeze-greenland-light.png`). Unify or rename the map badge to `grid max streak` and explain in the caption.
2. `more` and `more metrics` sit side by side doing different things (lead range vs metric set); rename `more` → `leads` disclosure or merge into one popover.
3. Baseline row's `outline: 2px solid var(--accent-focus)` (`styles.css`, `.baseline-row td.model-col`) reads as a focus/selected ring, not "reference". Replace with a subtle top-rule + the word "baseline" or a left key-line, reserve orange outline for actual focus.
4. Selected event rectangle turns solid blue with no explanation (`dr-home-desktop-dark.png`); add `selected` state to the existing better/worse key row or a 1px white inner stroke + tooltip "selected event".
5. About is well-typeset but written for the author: CSI is never defined ("cell text is the raw mean value" — of what?), "sample mode may show synthetic data" is a buried footnote, no update cadence in plain words. Add one line per gap under WHAT THIS IS; define CSI with its range ("0–1, higher is better; perfect = 1").
6. Mini/sparse modes emit console 404s probing `events.json` — invisible to users but makes any "open the console" trust check look broken; probe with a HEAD request or add the file.
7. No skeleton/spinner between HTML ready and multi-MB JSON resolving — on slower links the scorecard area is blank, which reads as broken, not loading. Add a 2-line skeleton for the scorecard and event table.
8. Tab order visits `About`/theme after the type chips but the map rectangles (pre-B1 fix) before the events table — re-verify order after B1: header → controls → scorecards → events table → map controls.

## Polish

- Colorbar tick labels crowd at 390px (`SCALE 200…1100 kg m⁻¹ s⁻¹` near the right edge) — drop to 3 ticks below 480px.
- Odd model counts leave a 2+1 asymmetric map grid with a full-width third panel (`dr-detail-heat-us-light.png`) — at n=3 use a 3-across row at ≥1280, or a 2-col grid with the wide panel as a full-width "hero".
- The dashed orange event-bbox rectangle appears on every panel with no legend anywhere — one caption under the colorbar: `dashed = event bounding box`.
- Story-line date range `Oct 16 – Oct 21` vs event end date `2026-10-22T00:00` — off-by-one presentation (end is exclusive); render end−1 day or add `→` semantics.
- Footer copy ("Metrics: ExtremeWeatherBench defaults · scorecard design: Brightband…") is the quietest it can be while being load-bearing for credibility — keep, but make the About link visually a link.
- Hover tooltip position flips inconsistently near the right viewport edge (observed twice at 1280) — clamp to viewport with 8px margin.

---

## Make it incredible (beyond defect-fixing)

1. **The dueling-map hero.** The event page should open as one composed figure, not a stack of panels: Observed pinned left, the selected model on the right, identical extent, one shared colorbar between them (the EWB paper's figure *is* this layout). A model selector under the pair swaps the right panel in place; hit/miss/false-alarm shading rides on the right panel at the chosen lead with the counts as its caption. One figure, one comparison, zero hunting.
2. **Basemap as furniture, event as subject.** Esri gray + graticule + event outline only (drop country fills on the detail maps — they're noise at z≥4); fit to occupied extent; when the field is narrow (Amazon heat, 15°×20°), rotate the panel aspect to hug the data instead of forcing a 1440-wide letterbox. Neil Gordon's explorer wins because the data always touches the frame.
3. **The 5-second verdict strip.** One tracked-label line under the header: `Nov week · 27 events · aifs_single best overall vs ifs_hres (+0.04 mean CSI) · best day-5 graphcast_gfs`. This is the answer to "who's best?" before anyone reads a table — and it's the sentence a Brightband reviewer will quote.
4. **Kill Plotly.** Two small line charts cost 4.56 MB. Replace with ~6 KB of inline-SVG small-multiples (skill-vs-lead, one per metric, shared axis, model colors from the dots). Instantly the fastest-loading tool of its class, and the small-multiple grid reads *better* than one overlaid multi-line chart.
5. **Reasons as a design system.** One component — a grey 11px line with a fixed vocabulary (`not yet run · not supported · build failed · out of window`) — used on chips (strikethrough models), map tiles, duration bars, and scorecard em-dashes, each linking to the pipeline log line. Never an exception class. A tool that explains its absences is the difference between "toy" and "instrument".

---

## Handoff

Blockers + majors appended to `notes/channel_design_review.md` (append-only) and design agent nudged via `orca terminal send` (`term_4227aacb-5179-4f34-b195-e06f70d30fb3`). Re-review loop: re-run `dr_walk.py` + `dr_measure.py` (scratch) against the live site after each fix lands; this file's severity lists are the gate — loop until zero blockers, zero majors.

## Re-review log (round-2 iteration, 2026-11-06 23:50 → 2026-11-07 00:55 UTC)

The design agent landed all 2 blockers + 7 majors live during this review and each was re-verified against the running site (`dr2-*.png`, axe-core, keyboard walk, touch emulation, network traces):

- **B1 closed** — score cells roving-tabindex + focus tooltip + `aria-label`; rows `tabindex=0 role=link`, Enter/Space opens; map rectangles out of tab order. Touch initially still failed: instrumented the live compat-event log and found Chrome fires the virtual `mouseout` *after* `click` at tap-end, re-hiding the tooltip (the fix at the time showed on click, which does not land last). Design applied the 800 ms touch-window guard on the mouse/blur hide paths; final probe on true-phone emulation (`has_touch` + CDP `hover:none`): tap shows, tap-away dismisses, mouse hover unaffected.
- **B2 closed** — reason vocabulary live (`unavailable: field not derivable for this archive` etc.), zero exception class names in page text, ghost em-dash rows reconcile with `MODELS 10 scored · 11 in roster`.
- **M1 closed** — gzip live (scores.json 1.22 MB → 72.6 KB on the wire, 22×), Plotly lazy on first fold-open (`undefined` at boot → charts render after), own assets `no-cache`, boot console clean.
- **M2 closed as major** — z4 min-zoom clamp + fit-to-occupied verified; plume now fills the panels' short axis. Residual wide-panel letterboxing (~45% width) accepted as polish (panel-aspect adaptation ≈ proposal #2's full form).
- **M3 closed** — Esri World Dark Gray in dark, invert filter deleted.
- **M4 closed** — `--ink-muted` fixed both themes; the one regression the fix introduced (`.unscored-row` ghost text failing in dark) was caught by axe and fixed; final `axe.run` color-contrast violations: **0** light and dark.
- **M5 closed** — `peak 32.6 °C`, `peak IVT 872 kg m⁻¹ s⁻¹`, headers render `[h]`.
- **M6 closed** — fixed day-scale, uniform tracks (1054 px ×4 — the first attempt's unequal tracks, caused by the delta text widening model rows' value cells, was measured and reported), per-model bar colors, `Δ +0 d vs obs` captions.
- **M7 closed** — fade + swipe hint live, chips/pills 44 px at 390.

## DONE

**Final verdict: round 2 clean — zero blockers, zero majors.** The dashboard is now credible (no error-log leakage, honest model accounting), fast (<100 KB gzipped JSON on the wire, lazy Plotly), accessible (axe color-contrast 0 violations, full keyboard path to every affordance, touch tooltips that actually work), and its signature maps frame the weather instead of the hemisphere. The OWB restraint finally matches the craft of its palette.

**What changed this round** (all by the design agent, driven through `notes/channel_design_review.md`): touch/keyboard access, reason-string vocabulary + ghost rows, gzip + lazy Plotly + cache policy, fit-to-occupied map framing with z4 clamp, theme-matched Esri basemaps, muted-ink token fix, units everywhere, duration-bar redesign (fixed scale, model colors, Δ semantics), mobile scroll affordances. Root-cause handbacks that mattered: the compat-`mouseout`-after-click ordering (touch tooltip), flex-track inequality from delta-text width (bar calibration), Playwright `has_touch` vs `hover:hover` (test methodology).

**Remaining (non-gating, in rank order):** Peak column still off-screen at 1440 (needs start/end date compaction or type-narrowed column set); wide-panel letterboxing → panel-aspect fit (incredible-proposal #2); numeric day ticks + observed reference line on duration bars; `more` vs `more metrics` merge; two "max duration" definitions (event `3d max run` vs map `max 4d` on merged events); map basemap labels faint at z4; odd-model-count 2+1 grid asymmetry; gzip not applied to HEAD requests (cosmetic). The five "make it incredible" proposals stand as the round-3 agenda — the dueling-map hero and the 5-second verdict strip first.

