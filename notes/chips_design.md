# Event-type chips — streamline + UX pass (role chips-design, 2026-11-17)

Brief: `briefs-new-events/chips_design.md`. Branch `aaTman/ne-chips`;
the integrator merges. **Preview:** http://192.168.0.121:8052/ (tree
`/opt/ewb-preview-chips` on the ewb LXC: this worktree's `dashboard/` +
`ewb_live/`, `data` symlinked read-only to `/opt/ewb-live-dev/data`, served by
`python3 -m ewb_live.serve --port 8052`). The dev site on :8051 and live
`/opt/ewb-live` were never written to.

Design method: the bundled **dataviz** skill (`references/color-formula.md`,
`palette.md`, `anti-patterns.md`, `interaction.md`, `components.md`) applied to
the chips; its `scripts/validate_palette.js` ran for every colour decision
(output below). No `frontend-design` skill exists in this environment.

## 1. What was uneven (measured, not vibes)

`scripts/chips_shots.py` (committed) records geometry + screenshots before and
after at 1440 / 820 / 390, light + dark
(`notes/screenshots/chips_before-*.png`, `chips_after-*.png`,
`chips_before_metrics.json`, `chips_after_metrics.json`).

Before (dev site :8051, 2026-11-17):

* **Ragged wrap.** Desktop 1440: 7 chips on row 1, one orphan ("Floods 40") on
  row 2. Phone 390: rows of 2/2/1/1. A wrapped row whose last line holds one
  chip is the definition of unorganised.
* **Eight different widths, no rhythm** (68–176 px at desktop), driven by
  label length; the count badge floated directly behind the label, so its
  distance varied per chip and nothing right-aligned.
* **29 px pills** everywhere — under the 44 px touch target on phones.
* **Colour-only identity** on the tab row and event list (9 px dots); the TC↔
  MLc pair was two purple dots of different hue, the legend's job was done by
  colour alone.
* **Tooltips almost nowhere**: only the TC zero tab had a title. No detection
  rule anywhere near the filter.
* **Type tags** (verdict strip, scorecard lane headers, skill-chart captions)
  were a different component per location: solid series fill + white 10.5 px
  uppercase text, then in dark mode two CSS rules flipped ink to `#1a1a16`
  and one `[style*="series-freeze"]` special case flipped back to `#fff` —
  inline `style="background:var(--series-…)"` hexes everywhere.
* **Selection not in the URL**: Region and the frame were deep-linkable, the
  type filter lived only in localStorage.
* **Semantics**: `aria-pressed` buttons (an honest choice when the row was
  written, but the owner brief now asks for tablist semantics; see §4).

## 2. The palette (identity colours, validated)

Identity colour per type is **categorical**: fixed hue order = the chip order,
colour follows the entity (never rank or filter state), every type also has a
glyph + label (never colour alone), text wears text tokens.

Order is grouped — Temperature | Water | Storms:
`heat_wave, cold_snap, freeze · atmospheric_river, flood · tropical_cyclone, midlat_cyclone`.
Both display orders that put colours side by side — the chip row and the
merged-map Gantt lanes (`heat, cold_snap, freeze, ar, flood`, reordered to
match) — were validated.

The previous set did **not** pass, measured with the site's own surfaces
(`--paper` #ffffff light / #262626 dark):

```
LIGHT  [FAIL] Chroma floor        #0e6d78 C 0.081 (cold snap read grey)
       [WARN] CVD                 #a01e56↔#2e7d32 ΔE 7.1 (flood↔AR, deutan)
       [FAIL] Normal-vision floor #1a4f96↔#0e6d78 ΔE 11.3 (freeze↔cold snap)
DARK   [FAIL] Lightness band      heat/ar/flood/tc/mlc all L≈0.70–0.75 (band 0.48–0.67)
       [FAIL] Chroma floor        #26808f 0.085, #b39ddb 0.091
       [FAIL] CVD                 #b39ddb↔#e88bbb ΔE 4.7 (tc↔flood, protan)
       [FAIL] Normal-vision floor #b39ddb↔#e88bbb ΔE 9.5
```

i.e. exactly the collisions the brief flagged: cold_snap↔freeze were the same
cold sludge, and in dark mode the flood berry sat right on top of the TC
violet.

Final set — changes only where the check demanded; identity hues preserved
(light: only cold snap and flood moved; dark: everything stepped back into
the band, freeze unchanged):

| type | light old → new | dark old → new | why |
|---|---|---|---|
| heat_wave | `#c6493c` | `#f57160 → #e76761` | dark L 0.70 out of band |
| cold_snap | `#0e6d78 → #0b859e` | `#26808f → #289f9d` | chroma floor; freeze sibling needs normal ΔE ≥15 |
| freeze | `#1a4f96` | `#4d73bf` | passes both |
| atmospheric_river | `#2e7d32` | `#66bb6a → #76a729` | dark L 0.72 out of band |
| flood | `#a01e56 → #9b255c` | `#e88bbb → #da6b94` | light CVD 7.1 vs AR green; dark out of band |
| tropical_cyclone | `#6f42c1` | `#b39ddb → #8c8dd1` | dark CVD 4.7 vs flood + chroma floor |
| midlat_cyclone | `#7d5200` | `#d9a441 → #c38823` | dark L 0.75 out of band |

Validator runs (skill `scripts/validate_palette.js`, surfaces = the site's own
chip ground):

```
LIGHT chips (surface #ffffff)    heat,cold,freeze,ar,flood,tc,mlc
  PASS Lightness band · PASS Chroma floor · PASS CVD (worst 8.1 flood↔ar deutan)
  PASS Normal-vision (worst 15.9 freeze↔cold) · PASS Contrast ≥3:1 → ALL PASS
DARK chips (surface #262626)
  PASS band · PASS chroma · PASS CVD (worst 9.5 tc↔flood protan)
  PASS Normal (worst 15.1 freeze↔cold) · PASS Contrast → ALL PASS
LIGHT GANTT order  heat,freeze,cold,ar,flood   → ALL PASS (CVD 8.1, normal 15.0)
DARK   GANTT order heat,freeze,cold,ar,flood   → ALL PASS (CVD 11.0, normal 15.0)
```

Mark-vs-surface checks beyond the chip ground (WCAG ≥3:1 everywhere): map
ocean light #eef4f8 → 4.24–7.93 per type; dark ocean #0e1620 → 3.92–6.35;
hover tint + canvas also clear. One documented **relief** case: the dark
freeze glyph on the *selected* alabaster wash (#3a3a30) measures 2.47 — the
chip's text label sits 7 px from the glyph and carries the identity (the
skill's relief rule: visible direct labels), and the fill ladder elsewhere
holds ≥3.6.

Status colours stayed status (`--worse`/`--better` remain the scorecard's
neutral skill pair; heat red keeps the documented series-vs-status collision
note in `styles.css` — identity chips and skill cells never share a surface
role, and both carry labels).

## 3. One chip component

`dashboard/styles.css` §"event-type chips": `.etype-tab` (interactive) and
`.type-chip` (static tag) share the metrics — 999 px radius, 7 px gap, ink
text, glyph tinted by `--etype` via a `data-etype` mapping (zero inline
hexes in JS: `inline: 0` is asserted by the tests).

* **Tab**: 32 px tall (44 px on touch/≤479 px), one line, counts in IBM Plex
  Mono `min-width: 3ch` right-aligned → digits tabular, width does not jiggle
  when counts change (region switch) and the row reserves its height
  (`.type-filter` reservation 44 px / 56 px touch — CLS contract kept).
* **States**: rest paper+border / hover tint / **selected = the page's one
  selected state** (alabaster wash + 3 px ink key-line, weight 500, count in
  ink — never an orange fill, never lane-coloured text) / focus-visible ring
  from the global token in both themes / zero = muted count + tooltip, still
  selectable ("No midlatitude cyclones met the criteria this window…" and the
  TC honest-zero archive note ride `title`). No disabled state exists on
  purpose.
* **Grouping**: chips are ordered Temperature | Water | Storms; the group
  heads carry a 1 px hairline (`::before` of the group-head tab, so the
  tablist still contains exactly one element per tab).
* **Static tags** (verdict strip, lane headers, skill-chart captions): soft
  lane ground (`--series-*-soft`) + ink text + glyph — the white-on-series
  text and the two dark ink-flip hacks (incl. the `[style*="series-freeze"]`
  special case) are deleted. Text contrast on every chip ground clears 4.5
  in both themes (asserted).
* **Event list**: the colour dot became the same lane glyph (identity not
  colour-alone in the list too).
* **Gantt lane order** matches the chips (`mergedmap.js LANE_ORDER`), with a
  comment: changing that order invalidates the adjacency validation above.

## 4. Interaction / a11y

* `#type-tabs` is a real `role=tablist` (static in `index.html`); each chip is
  `role=tab` with `aria-selected` + `aria-controls="explore scorecard-sec"` —
  it names both regions the filter changes, which is what the old
  "no single tabpanel" objection was really about.
* Roving tabindex; ArrowLeft/Right/Up/Down + Home/End move **focus** only
  (manual activation — arrows must not re-render the page); Enter/Space/click
  select. Focus survives the re-render and `scrollIntoView({inline:'nearest'})`
  keeps the focused chip visible in the scroller; smooth-scroll respects
  `prefers-reduced-motion`.
* Accessible name = visible text + count ("Heat wave 15") — the first draft's
  separate `aria-label` tripped axe's `label-content-name-mismatch`, so the
  long label + one-line detection rule (About copy) ride the tooltip instead.
* **URL**: `#tab=<lane>` — written by `syncUrl()` next to region/lead/frame,
  read at boot (URL wins over localStorage), followed on Back/Forward, unknown
  values fall back to All and self-clear from the address bar. Deep link +
  keyboard selection are covered by the new e2e.
* Multi-select: none — the current UI is single-select, so the redesign is too.

## 5. Responsive contract

One row at every width: `#type-tabs` never wraps; when the chips cannot fit it
scrolls (`scroll-snap-type: x proximity`, hidden thin scrollbar, wheel
translate, edge-fade masks toggled by JS exactly like the scorecard tables'
`scrollable/at-end` pattern). At 390 px: one 44 px row, page horizontal
overflow 0 px, keyboard End scrolls the last chip fully into view — all
asserted in `tests/e2e/test_chips_fe.py`.

## 6. Tests

* **New** `tests/e2e/test_chips_fe.py` (7 tests, green against the preview):
  tablist semantics + grouped order + dividers; keyboard nav + `#tab=` round
  trip incl. invalid value; honest zero selectable + explained + empty lane
  note; 390 px no-overflow / one-row / 44 px / focus scroll; AA text contrast
  of every chip label in both themes; "one component" metric (no inline
  hexes, same radius, one height).
* **Selector updates (needed — the labels are now short/singular and the row
  is grouped)**: `Atmospheric rivers→Atmospheric river`, `Heat waves→Heat
  wave`, `Freezes→Freeze`, `Tropical cyclones→Tropical cyclone` (substring
  matches keep working against older deployments), tab `nth-child(4)→(5)`
  (AR's new position), fixture `tabs.count()==4→8` (All + every lane renders
  even at zero — the honest-zero contract), regions
  `startswith("Heat waves"/"Atmospheric rivers")` → singular, and
  `test_ar_type_color_token_both_themes` now pins the **new** dark AR token
  with a pointer to this note. Files touched: `test_dashboard_fe.py`,
  `test_dashboard_regions_fe.py`, `test_tracklane_fe.py`.
* Suite state, honestly: this branch's e2e was **not green before this
  change**. Reference run against the dev site (:8051) with the pre-change
  deployed code and the old tests: **45 failed / 93 passed / 8 errors**. Per
  file after this change, against the preview: chips **7/7 pass** (new file),
  regions **9/9 pass**, guardian + tracklane **6 failed / 77 passed** (all 6
  in the pre-existing set), dashboard_fe **31 failed / 15 passed / 1
  skipped** vs **33** pre-existing failing in the base run — every remaining
  failure name is a subset of the base failure list (diffing the two logs),
  and a stash-and-rerun of one representative fixture failure against clean
  integration code fails identically: the synthetic `gd/dur` fixtures are
  stale relative to the 7-lane world. This branch **fixed** two of the base
  failures (`test_controls_live_in_the_section_they_change`,
  `test_type_filter_narrows_map_list_and_scorecard_together`) and added zero.
  Reviving the rest of the synthetic-fixture suite is a separate brief.

## 7. Touched code (small on purpose)

`dashboard/app.js` (TYPE_META one-liners, TYPE_ICONS + `typeChipHtml`,
grouped TYPE_ORDER, tablist `renderControls`, `tabsKeyNav`/`tabsToggleFade`,
`setTab`→URL, boot + popstate `#tab=`, event-row glyph, chips in verdict/
lane/caption), `dashboard/styles.css` (series tokens ×2 themes, one chip
component, reservations; deleted the flip hacks), `dashboard/index.html`
(role=tablist), `dashboard/mergedmap.js` (LANE_ORDER grouping), tests as
above, `scripts/chips_shots.py` (before/after harness).

## 8. Open questions for the owner

1. Short label **"Midlat. cyclone"** on the chip (long "Midlatitude cyclone"
   in tooltip/aria). Alternative: "Midlatitude low" — the lane's own term.
2. The phone row is **one scrollable line** (snap + fade). The alternative was
   a two-column tidy grid; the line keeps the Temperature|Water|Storms order
   readable as a sequence. Say the word if you want the grid.
3. Dark **freeze glyph on the selected wash** is the one mark at 2.47:1
   (label-relief documented). Fixable only by lightening freeze dark
   `#4d73bf→#5679c4`, which costs the sibling-pair normal ΔE (15.1→14.2). The
   gate wins; flagging the trade.
4. The dev build's `global_timeline.json` currently carries no cold_snap/flood
   bars, so those Gantt lanes are empty there — data-side (integrator's lane
   build), the lane order/colours are ready for it.
