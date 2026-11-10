# Regional breakdowns — review (regions-review / Hermes, 2026-11-10)

Scope: commits d43b084..9c2619d on `aaTman/regional-breakdowns`, then the review's own
fixes (e8cb40d, 01136eb, 2aab7ad).
Real data: pulled with `EWB_LIVE_REMOTE_DIR=/opt/ewb-live/_branches/regional-breakdowns
ops/remote.sh pull-results` (live dirs untouched; nothing heavy on orca). Dashboard
served locally via `python -m ewb_live.serve`. Screenshots:
`notes/screenshots/review-regions-<region>-<desktop1440|mobile390>-<light|dark>.png`
(24) + `review-regions-owb-scorecard.png` (OWB reference).

## Findings, ranked

1. **MEDIUM — merged map looked unchanged vs Global for full-longitude regions
   (Tropics, NH, SH) — FIXED** (e8cb40d). The corner-bbox fit is width-bound for
   lat-bands, so the region occupied only ~30 % of the canvas height and out-of-region
   exceedance painted at full strength (owner screenshot review item b). Fix: a dim
   veil outside the region rect (`dimOutside` in mergedmap.js, painted above the
   raster; Global = no veil; new dual-theme token `--map-dim`). Verified by canvas
   pixel sampling (outside-band pixels shift toward the paper ground, in-band
   unchanged) and in the new screenshots.
2. **MEDIUM — feature broke 7 pre-existing e2e tests — FIXED** (2aab7ad). Boot's
   background fetch of `scorecard_by_region.json` 404s in the test_dashboard_fe
   fixtures → their `errs == []` assertions fail (1d4214f). Hidden from the default
   `uv run pytest -q` because `addopts = -m "not e2e"` — the lane only ever ran its
   own file. Fix: fixtures write a `{}` stub (= no region data, dropdown hidden,
   pre-region behaviour) and `serve.py` answers 204 for the missing file like the
   other OPTIONAL_FILES (also keeps a real first-deploy week console-clean).
3. **LOW — "1 events" in the merged-map section count (real North America, n = 1)
   — FIXED** (e8cb40d) → "1 event · layers every 6 h"; verified in the live DOM.
   (Owner review item c.)
4. **LOW — OPEN: 2 live-URL e2e failures** (`test_heat_detail_reference_component`,
   `test_heat_model_panels_forecast_at_lead`). They run against the *deployed*
   dashboard at 192.168.0.121:8050, which serves older eventviz code ("Heat — days >
   P85" vs expected "Valid time:"). They fail identically with this review's changes
   stashed — a stale-deployment mismatch, not a regional-lane bug. Recommendation:
   re-run `pytest -m e2e` after whatever lane deploys next; don't block on it.
5. **LOW — week switch left a stale `#region=europe` in the URL after resetting the
   region to Global — FIXED** (01136eb). Copying the URL at that moment restored a
   different view than the one displayed. Verified fixed (hash drops `region=`,
   `?week=` preserved).

## Counts consistency (real data, 28 scored events)

Checked in every one of the 6 regions × 2 widths × 2 themes: header EVENTS stat,
type-tab counts, Detected-events count + table rows, merged-map count, and
`regions[].n_events`/`by_type` agree everywhere. Global 28 (hw 15 / freeze 1 /
ar 12); Tropics 9 (hw 9), NH 14 (hw 6 / fz 1 / ar 7), SH 5 (ar 5), NA 1 (hw 1),
Europe 6 (ar 6); tropics+NH+SH = 28 = Global (partition holds).

## Map framing

- **Merged Equal-Earth map, North America (owner review item a): no bug.**
  Projected region bbox = x 136..774, y 12..443 on the 910×455 canvas; reference
  points all land inside: SF x=188, LA 206, Seattle 265, Maine 730, Miami 558;
  region centre (lon −97.5) → x=461 ≈ canvas centre. The fit does cover lat 25..60,
  lon −120..−75 completely; because the canvas also shows the sphere slice beyond the
  box (≈ lon −146..−51), the whole continent is visible at a strong zoom — that is
  the "looks like eastern Canada" impression, not a cut-off. If the owner wants less
  zoom for small boxes, cap the fit scale (one line in `fitProjection`) — not done.
- **Leaflet catalog map**: fits the region box for NA/Europe; full-longitude lat
  bands cannot be cropped vertically in Leaflet without losing horizontal context,
  so tropics/NH/SH stay near the world view (zoom is aspect-bound). Honest; the new
  merged-map veil carries the region signal there.
- **Global restores the default view** (no veil, events fit) ✓; deep link +
  Back/Forward restore verified.

## Science

1. **OPEN (recommendation) — centroid rule is sensible for heat waves/freezes, weak
   for ARs.** Heat waves/freezes are blob-shaped so the bbox centroid is near the
   event; ARs are long and thin and the centroid can sit far from the impact area.
   Real-data evidence: 4/28 scored events mismatch a >10 % footprint overlap (all
   "footprint touches region, centroid outside": one subtropical AR is 22 % in the
   Tropics but centroid at −20.1° → excluded; an unscored US-East-Coast AR has 49 %
   NA overlap yet misses the lon box by **0.1°**). Multi-membership would also need
   de-duplication awareness in aggregates if overlap assignment is adopted.
   Recommendation: when the scored universe grows, move to "region if >25 % of the
   event footprint overlaps the region box" (schema already supports
   multi-membership) — it changes published `scorecard_by_region` numbers, so it
   needs owner sign-off; left OPEN.
2. **OK — small-n honesty.** `build_scorecard` nulls the bootstrap CI below
   n_events ≥ 3 (real NA block: 603/603 rows CI-null); the UI reads "1 event ·
   indicative", empty lanes say "No freezes in Tropics this window" with the
   centroid rule in the tooltip, and type tabs show explicit 0s. No misleading
   table of dashes.
3. **OPEN (minor) — bootstrap meaningfulness at n = 3–5.** Europe (6) and SH (5)
   pass the gate but ~20-25 % of their cells have zero-width CIs (all events in the
   cell give the same value — legitimate, yet it reads as suspiciously precise).
   Optional: grey/mute cells with n < 5 in a future UI pass.
4. **OK — history weeks without the file.** W45/W46 (no `scorecard_by_region.json`)
   boot clean: dropdown and its label group hide, region resets to Global, tables
   render, zero console errors; switching back to the current week re-reveals the
   control. Region deep links are validated against the contract at boot (unknown
   key dropped from the URL).

## Design vs OWB (owb.brightband.com/scorecard)

| | OWB | EWB (this branch) |
|---|---|---|
| Label | "Region" above the control, 13px/500, muted warm-grey | "Region" above the control, 10.5px/500, muted (matches our ctl-label scale) |
| Placement | leftmost, before Baseline / Date range | leftmost group in the filter bar, before tabs/Models/Leads |
| Options | Global, Tropics, Northern Hemisphere, Southern Hemisphere, North America, Europe | identical, same order |
| Control | custom div-combobox, 13px text / 33px tall / 6px radius | native `<select>`, 12.5px / 27px / 3px — deliberately the house control scale (brief: match existing controls) |
| A11y | custom roles | native select: keyboard + SR behaviour for free ✓ |

Match is good; size differences follow our own control family, which the brief asked
for. Mobile 390px: control visible, in-viewport, focusable, both themes ✓.

## Tests run

- `uv run pytest -q` → **222 passed, 28 skipped, 111 deselected** (unit; includes
  the 9 regions unit tests).
- `uv run pytest -q -m e2e tests/e2e/` → **106 passed, 3 skipped, 2 failed** — the
  two are open item 4 (stale deployment); before this review's fix 2aab7ad they were
  7+2.
- `tests/e2e/test_dashboard_regions_fe.py` alone: 8 passed.
- Review fixes are UI/one-liners verified live via Playwright DOM + canvas pixels;
  no new unit tests added.
