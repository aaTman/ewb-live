# Event density layer: plan

Legend: `[verified] ✅` checked against code or a test. `[unverified] ⚠️` not checked yet.

## Summary

We add a density layer to the global map. It counts how many events cover each map cell over the shown window. It paints those counts as a colour ramp under the event boxes. The top of the ramp is the highest count on the map right now, so it rescales when the type tab, region or window changes. A minimum control hides cells with fewer events than a chosen count (default 1). Risk: counts use event bounding boxes, not exact footprints, so big boxes over-count. [unverified] ⚠️

## Decisions (owner request, 2026-11-24)

- "For each event" means one density view per event type tab. The All tab shows all types combined. [unverified] ⚠️ (read of the request, not confirmed)
- Max of the ramp = the highest per-cell count in the current view. [verified] ✅ (owner request)
- Minimum = user control, default 1. Cells below it are not painted. [verified] ✅ (owner request)
- Source of counts = `bbox` of each event in `tlEvents()` filtered by the page tab. No new data files, no backend change. [verified] ✅ (`mergedmap.js densEvents()`: `tlEvents()`, tab filter, full-band boxes skipped; the Region view already filters `tlEvents`, so the grid follows it)

## What shipped (2026-11-24, this branch)

- `dashboard/density.js`: `countGrid(events, cellDeg)` (half-open cells, row 0 at
  lat -90, dateline boxes count both halves) and `scaleOf(grid, minCount)`
  (`{min: 1, max: 0}` when empty). Pure, no DOM; `window.EwbDensity` +
  `module.exports`.
- `mergedmap.js`: `dens = {on, min}` state, `paintDensity(ctx)` right after
  `drawBase` (under the raw raster, clipped to the sphere), the hue from
  `TYPE_SERIES_VAR` / `--series-unknown` for the All tab, legend group with the
  toggle + min input + ramp bar, URL keys `dens`/`densmin`.
- Cell edge 2.5°, ramp alpha 0.12 → 0.55. The density group does NOT count
  towards the 3-group popover threshold (it is a control, not a lane).

## Where the code goes

- New file `dashboard/density.js`: pure functions, no DOM. Exports on `window.EwbDensity` and `module.exports` (so node can test it).
  - `countGrid(events, cellDeg)` returns `{nx, ny, cellDeg, counts}`. `counts` is a `Uint16Array`, row 0 at lat -90, col 0 at lon -180. A bbox is `[lat_min, lat_max, lon_min, lon_max]`. If `lon_max < lon_min` the box wraps the dateline: count both halves, never the middle.
  - `scaleOf(grid, minCount)` returns `{min, max}`. `max` is the highest count. `min` is `minCount` clamped to `[1, max]`. An empty grid gives `{min: 1, max: 0}`.
- `dashboard/mergedmap.js`:
  - Add state `dens = {on: false, min: 1}`.
  - Add `paintDensity(ctx)`, called in `paintFrame` after `drawBase(ctx)` and before the raw raster. Use `proj([lon, lat])` (line 291 sets `proj`). Fill each counted cell with `rgba` from a ramp with alpha 0.55. Skip cells with count < `dens.min`.
  - Ramp colour comes from the CSS token `--series-<type>` for a type tab (see `layersForTab`, line 780). The All tab uses a neutral token. Do not hard-code hex colours (a test checks this style).
  - Add the toggle and the minimum input to the legend (`renderLegend`, line 1424). Add a ramp bar with the `min` and `max` numbers.
  - Persist in the URL: `urlParams` (1215) and `applyUrl` (1224). Keys `dens=1`, `densmin=N`.
- `dashboard/index.html`: load `density.js` before `mergedmap.js` (line 39). Add only markup that `renderLegend` cannot make.
- `dashboard/styles.css`: only the minimum styles for the new control.
- `SPEC.md`, dashboard section (line 221): one bullet that describes the layer.

## Steps (commit after each step)

1. [x] Red: `tests/test_density_js.py` runs `node` on `dashboard/density.js` (skip if no node, like `tests/test_mlc_hue.py`). Cases:
   - one box counts 1 in its cells and 0 elsewhere;
   - two overlapping boxes give 2 in the overlap;
   - a dateline box `[10, 20, 170, -170]` counts both halves and not lon 0;
   - `scaleOf` max equals the highest count;
   - `scaleOf` clamps `minCount` (0 becomes 1, 99 becomes max);
   - an empty list gives max 0.
   Run it. Confirm it fails because the file is missing.
2. [x] Green: write `dashboard/density.js`. Run `uv run pytest tests/test_density_js.py -q`.
3. [x] Wire the layer into `mergedmap.js`, `index.html`, `styles.css`. Add a test that `index.html` loads `density.js` before `mergedmap.js`.
4. [x] Update `SPEC.md`. Run `uv run pytest -q` and `uvx ruff check .`.
5. [x] Commit, push, open the PR with base `develop`.

## Rules for the agent

- Python tests: tabs, type hints on every function, Google docstrings, ruff clean.
- Branch is `feat/event-density-layer` in this worktree. Do not rename it. One agent in this worktree.
- PR base is `develop`: `gh pr create --base develop`. Check with `gh pr view --json baseRefName`.
- Never run EWB compute here. Never commit data, screenshots or PNGs.
- Wrap every foreground command in `timeout`. Ship a first commit within 45 minutes.
