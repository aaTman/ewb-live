/* ==========================================================================
   dashboard/density.js — event-density grid maths for the merged map.

   PURE: no DOM, no d3, no fetch. It turns the page's event list into a
   per-cell count ("how many events of this tab cover this cell"), which
   mergedmap.js paints as a colour ramp under the event boxes and the legend
   scales. The map keeps its own view state; this file only answers two
   questions: how many events per cell, and what min/max the ramp needs.

   A count comes from an event's BOUNDING BOX, not its exact footprint, so a
   big box over-counts around its edges — the ramp is a "where is the action"
   guide, not a measurement. Bboxes that wrap the dateline count both halves
   and never the middle of the map (same rule as the boxes overlay).

   Exposed as window.EwbDensity for the page and module.exports for node, so
   tests/test_density_js.py can run this exact file.
   ========================================================================== */
(function () {
  'use strict';

  /**
   * Count how many event boxes cover each grid cell of the whole globe.
   *
   * @param {Array} events Event objects (uses `.bbox`) or bare bboxes. A bbox
   *     is `[latMin, latMax, lonMin, lonMax]` in degrees; when `lonMax <
   *     lonMin` the box is the eastward arc across ±180, so both halves count.
   * @param {number} cellDeg Cell edge in degrees (the grid always covers the
   *     whole globe: `nx = 360/cellDeg`, `ny = 180/cellDeg`).
   * @returns {{nx: number, ny: number, cellDeg: number, counts: Uint16Array}}
   *     `counts` is row-major, row 0 at lat -90 and col 0 at lon -180, so a
   *     cell is `counts[i * nx + j]`.
   */
  function countGrid(events, cellDeg) {
    const cell = Number.isFinite(cellDeg) && cellDeg > 0 ? cellDeg : 10;
    const nx = Math.max(1, Math.round(360 / cell));
    const ny = Math.max(1, Math.round(180 / cell));
    const counts = new Uint16Array(nx * ny);
    const list = Array.isArray(events) ? events : [];
    for (const ev of list) {
      const b = Array.isArray(ev) ? ev : (ev && ev.bbox);
      if (!Array.isArray(b) || b.length < 4) continue;
      const s = Number(b[0]), n = Number(b[1]), w = Number(b[2]), e = Number(b[3]);
      if (![s, n, w, e].every(Number.isFinite)) continue;
      const south = Math.min(s, n), north = Math.max(s, n);
      // Rows and columns the box touches. A cell covers [lo, lo+cell), so a
      // box edge that lands on a boundary does not light the cell below it.
      const i0 = clampI(Math.floor((south + 90) / cell), ny);
      const i1 = clampI(Math.ceil((north + 90) / cell) - 1, ny);
      if (i1 < i0) continue;
      const spans = e < w ? [[w, 180], [-180, e]] : [[w, e]];
      for (const [a, bb] of spans) {
        const j0 = clampI(Math.floor((a + 180) / cell), nx);
        const j1 = clampI(Math.ceil((bb + 180) / cell) - 1, nx);
        for (let i = i0; i <= i1; i++) {
          const row = i * nx;
          for (let j = j0; j <= j1; j++) counts[row + j] += 1;
        }
      }
    }
    return { nx, ny, cellDeg: cell, counts };
  }

  /**
   * Clamp a row/column index to the grid.
   *
   * @param {number} k Raw index.
   * @param {number} n Grid size on that axis.
   * @returns {number} Index inside `[0, n - 1]`.
   */
  function clampI(k, n) { return Math.min(n - 1, Math.max(0, k)); }

  /**
   * Pick the ramp's stops for the grid as it stands in the current view.
   *
   * The top of the ramp is always the busiest cell on the map right now, so
   * the ramp rescales when the tab, region or window changes. The bottom is
   * the reader's minimum, clamped into `[1, max]`: a minimum of 0 means "show
   * everything", one above the top collapses to the top.
   *
   * @param {{counts: Uint16Array}} grid Grid from `countGrid`.
   * @param {number} minCount Reader's minimum count (default 1).
   * @returns {{min: number, max: number}} `max` is the highest count (0 when
   *     nothing is counted), in which case `min` is 1 and the layer paints
   *     nothing.
   */
  function scaleOf(grid, minCount) {
    const counts = (grid && grid.counts) || [];
    let max = 0;
    for (let k = 0; k < counts.length; k++) if (counts[k] > max) max = counts[k];
    if (!max) return { min: 1, max: 0 };
    const want = Number.isFinite(minCount) ? Math.round(minCount) : 1;
    return { min: Math.min(Math.max(want, 1), max), max };
  }

  const api = { countGrid, scaleOf };
  if (typeof window !== 'undefined') window.EwbDensity = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
