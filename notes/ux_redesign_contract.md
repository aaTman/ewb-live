# EWB Live UX Redesign — Shared Contract & Acceptance Criteria

Branch: `feat/ux-redesign` (based on `origin/develop`)
Orchestrator: Lead agent (strict verification gate; no partial passes accepted)

## 1. Visual System & Global Constraints
- Preserve the Brightband OWB alabaster/dark token system (`--canvas`, `--paper`, `--alabaster`, `--alabaster-mid`, `--tint`, `--border`, `--border-strong`, `--matte-black`, `--ink-secondary`, `--ink-muted`, `--series-*`, `--better`, `--worse`).
- **No build step.** Plain HTML + CSS + vanilla JS.
- **No black-crescent pill bugs:** Never use `box-shadow: inset 3px 0 0 ...` on `border-radius: 999px` buttons. Active pills use solid inverted fill (`background: var(--matte-black); color: var(--canvas); border-color: var(--matte-black)`) or crisp border (`border-color: var(--matte-black); background: var(--alabaster)`).
- **Themed form controls everywhere:** Every `<input type="range">` and `<input type="checkbox">` in the dashboard (including inside `EventViz`) uses `accent-color: var(--matte-black)` and themed track/thumb styles — zero OS-default bright blue controls.
- **Accessibility:** Zero `axe-core` serious/critical violations in both light and dark themes; `scrollWidth <= innerWidth` at `390px`, `768px`, `1280px`, and `1440px`.
- **Public hygiene:** No internal IPs, hostnames, absolute paths, or usernames.

## 2. Shared JS Exports & Helpers (owned by WP1 in `dashboard/app.js`)
- `window.displayModelName(id)` -> clean human name string:
  - `ifs_hres` -> `IFS HRES`
  - `aifs_single` -> `AIFS Single`
  - `aifs` -> `AIFS`
  - `pangu_weather` -> `Pangu-Weather`
  - `pangu_weather_gfs` -> `Pangu-Weather (GFS)`
  - `fourcastnet_v2` -> `FourCastNet v2`
  - `fourcastnet_v2_gfs` -> `FourCastNet v2 (GFS)`
  - `fourcastnet` -> `FourCastNet`
  - `graphcast` -> `GraphCast`
  - `graphcast_gfs` -> `GraphCast (GFS)`
  - `aurora` -> `Aurora`
  - `aurora_gfs` -> `Aurora (GFS)`
  - `aigfs` -> `AIGFS`
  - Fallback: split `_gfs` suffix into ` (GFS)` and Title Case words.
- `window.displayModelHtml(id)` -> escaped HTML with `<span class="m-tag">GFS</span>` badge when `_gfs`-forced.
- `EventViz` and `TrackViz` call `(window.displayModelName ? window.displayModelName(m) : m)`.

## 3. Unified 5-Band Event Detail Contract (ALL 7 Event Types)
Applies identically to `heat_wave`, `cold_snap`, `freeze`, `atmospheric_river`, `flood`, `tropical_cyclone`, `midlat_cyclone`:

### Band 1 — Unified Header (`.det-header`, rendered by `app.js`)
```html
<div class="det-header">
  <div class="det-title-block">
    <button id="detail-close" class="backlink" type="button">← All events</button>
    <div class="det-title-row">
      <h2 id="detail-title">United States</h2>
      <span id="detail-type-slot"></span> <!-- holds typeChipHtml(ev.event_type) -->
    </div>
  </div>
  <div class="detail-chips" id="detail-chips">
    <!-- 3-4 compact .det-meta-chip pills: Dates+duration, Peak+unit, Region/Basin, Source -->
  </div>
</div>
<p class="event-story sr-only" id="detail-story"></p>
```
- `#detail-title` shows ONLY the location or storm name (e.g. `United States`, `Champi`, `Southern Ocean`), NEVER repeating the event type prefix that already sits beside it in `#detail-type-slot`.
- `#detail-title:focus { outline: none; }` and `#detail-title:focus-visible { outline: 2px solid var(--accent-focus); outline-offset: 2px; }` — no ugly focus rectangle when opened by click.

### Band 2 — Unified Control Bar (`.det-toolbar`, rendered inside `#detail-viz` by `EventViz` or `TrackViz`)
- Horizontal themed bar (`display: flex; align-items: center; justify-content: space-between; gap: 8px 14px; flex-wrap: wrap; padding: 8px 12px; background: var(--bg-inset); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 12px;`).
- Left `.det-tb-left`:
  - `EventViz`: `Lead` micro-label + `.ev-lead` pill buttons (`1d`, `3d`, `5d`, `10d` — keep `data-lead="<hours>"` and text e.g. `24h` or `1d` with clean active state).
  - `TrackViz`: Source/cadence summary (`IBTrACS · 48 fixes · 6-hourly` or `ERA5 MSLP · 18 fixes · 6-hourly`) + `Full track` / `Event window` segmented scope toggle (`.det-track-scope`).
- Right `.det-tb-right`:
  - `EventViz` (time-indexed): `▶` `‹` `›` + custom-themed `.ev3-srange` slider + `.ev3-readout` (`Aug 23, 2026 · T+24h`). For AR: anchor timestamp + `T+<lead>h`.
  - `TrackViz`: Active window range readout + fullscreen `⤢` button (`.det-fsbtn`).

### Band 3 — Unified Split Stage (`.det-stage`, rendered inside `#detail-viz` by `EventViz` or `TrackViz`)
- CSS Grid: `grid-template-columns: minmax(0, 1.55fr) minmax(260px, 1fr); gap: 12px; align-items: stretch;` (collapses to `1fr` below `900px`).
- **Left Column (`.det-map-col`)**:
  - Card container (`background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; display: flex; flex-direction: column; position: relative;`).
  - Fullscreen button `.ev3-fsbtn` / `.det-fsbtn` anchored cleanly top-right of the map stage (Esc exits fullscreen).
  - Map viewport (`height: 400px` desktop, `320px` mobile; Leaflet for `EventViz`, Equal Earth SVG for `TrackViz`).
  - Integrated bottom legend bar (`.det-map-bar`): compact strip directly under the map inside `.det-map-col` showing the lane's scale (single-tier swatch + description for Heat/Cold/Freeze/Flood; continuous IVT colorbar for AR; wind kt bins for TC; pressure hPa bins for MLC).
- **Right Column (`.det-side-col`)**:
  - Card container (`background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; display: flex; flex-direction: column; gap: 8px;`).
  - Header `.det-side-head` (`display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 8px; border-bottom: 1px solid var(--border);`):
    - `EventViz`: `<b>Active overlays</b><span>N of M</span>`
    - `TrackViz`: `<b>Storm summary</b><span>IBTrACS / ERA5</span>`
  - Body `.det-side-body`:
    - `EventViz`: ONLY layers with data at the selected lead in grid-aligned `.ev3-layer` rows (`checkbox | color input | name+stats | opacity slider | %`).
    - `TrackViz`: Structured `.det-kv-list` rows (Peak intensity, Min pressure, Basin/Region, Distance & Bomb status for MLC, Fixes & cadence, Source).
  - Collapsible footer `<details class="det-unavail">`:
    - `EventViz`: `<summary>N models unavailable at <lead></summary>` expanding to list unavailable models & reasons (hidden if 0 unavailable).
    - `TrackViz`: `<summary>Track notes</summary>` holding the solid-vs-dashed lifecycle, truncation, and wind-basis notes.

### Band 4 — Unified Summary / Comparison Strip (`.det-summary-strip`, rendered inside `#detail-viz`)
- Card container (`margin-top: 12px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px;`).
- `EventViz` (Heat / Cold / Freeze / Flood): Duration vs Observed horizontal bar chart (`.ev-dur`, human-readable model names).
- `EventViz` (AR): Mask area / overlap comparison bars (Observed vs active models at selected lead, cells & % of observed).
- `TrackViz` (TC / MLC): Clean inline SVG intensity timeline sparkline (wind kt for TC; inverted central pressure hPa for MLC) shading the event window and marking peak fix.

### Band 5 — Unified Per-Event Scores Fold (`#detail-scores`, rendered by `app.js`)
- Identical `<details id="detail-scores" class="fold">` across all 7 event types, with human-readable model names in `#detail-mini` and lazy Plotly lead charts in `#detail-charts`.

## 4. Floating Map Legend Contract (WP1 + WP4)
- `#gm-legend` lives inside `#raw-map-wrap` (or anchored over top-right of `#raw-map-wrap`) with a floating `Legend ▾` button (`.map-leg-btn`) positioned beside `#theater-btn` at the top-right corner of `#raw-map-wrap`.
- When 3+ lanes are active (`All` tab), the legend popover starts CLOSED (`[hidden]` / collapsed), reclaiming the 160px vertical space between `#raw-map-wrap` and `.timeline`! Clicking `Legend ▾` opens the full labeled scales popover cleanly (Esc or click-outside closes it).
- When 1–2 lanes are active (single hazard tab), a single slim inline legend bar may sit cleanly below the map (<= 36px tall) OR inside the same compact bar with labeled stops.

## 5. File Ownership (Strictly Disjoint)
- **WP1 (`wp1-shell-scorecard`):** `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css`
- **WP2 (`wp2-eventviz-detail`):** `dashboard/eventviz.js`
- **WP3 (`wp3-trackviz-detail`):** `dashboard/trackviz.js`
- **WP4 (`wp4-map-legend-tracks`):** `dashboard/mergedmap.js`, `dashboard/tracklayer.js`
