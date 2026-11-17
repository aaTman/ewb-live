# Upstream PR draft 1 — `brightbandtech/ExtremeWeatherBench`

**Branch:** `perf/tc-locality-search` (local clone at `~/scratch/ExtremeWeatherBench`,
branched from `main` @ `c6467f4` = v1.1.1, **not pushed**)
**Diff:** 1 commit — `src/extremeweatherbench/events/tropical_cyclone.py`,
`tests/test_tropical_cyclone_locality.py` (new), `benchmarks/tc_detection_profile.py` (new)
**Status:** found while porting the TC detector to pure Python for ewb-live
(`ewb_live/events/mlc_track.py`), then profiled against upstream HEAD.

---

## PR title

`perf(events): search the neighbourhood of a peak, not the whole globe`

## PR body

### What

Two array-side searches in the tropical-cyclone detector scan the **whole
(global) field** to produce results that are **local by construction**:

1. `_create_spatial_mask` builds a `(n_track_points, nlat, nlon)` great-circle
   distance field — chunked 32 points at a time — to mark the union of discs of
   radius `max_distance_degrees` (5° at 0.25° resolution is **0.2 % of the
   globe**, so ~99 % of that temporary always compares False).
2. `find_contours_from_point_specified_field` subtracts the point value from the
   entire field and contours the entire field, **once per peak per field**
   (SLP and DZ). `find_valid_candidates` then rejects any contour whose
   *furthest* point is more than `max_gc_distance_slp_contour_degrees`
   / `max_gc_distance_dz_contour_degrees` from the peak — so every contour that
   can be accepted lies inside a ≤ 6.5° box around the peak.

Both are now restricted to a conservative window around the point. Conservatism
comes from Clairaut's relation: a geodesic of length ≤ r between two points no
farther from the equator than φ_max has `|Δlon| ≤ r / cos(φ_max)`; `cos` is
smallest at the highest |latitude| in the latitude window, so that window width
cannot clip a point that is in range. Latitude uses the (exact) bound
`|Δlat| ≤ r`. A contour that touches the window edge extends ≥ r from the peak
and therefore fails the tracker's distance test in *both* implementations, which
is why the accept/reject decision cannot change.

Fallbacks keep the change unconditionally safe: a window that would cross the
antimeridian, a grid with fewer than two coordinates per axis, or a degenerate
coordinate spacing falls back to the previous whole-field path (so masked-grid
and pole cases are byte-identical), and `find_contours_from_point_specified_field`
keeps its old 3-argument behaviour when `search_radius_degrees` is omitted.

### Measurements

`benchmarks/tc_detection_profile.py` — synthetic-but-realistic global 0.25°
field (721 × 1440, 40 localised closed lows, 12 IBTrACS rows per valid time,
`surface_pressure_threshold = 101 000 Pa`), no network:

| | before | after |
|---|---|---|
| `_create_spatial_mask` (12 rows) | 504 ms | **3 ms** |
| `_find_peaks_for_time_slice` | 760 ms/step | **24 ms/step** |
| 90-day 6-hourly analysis (360 steps), peak search | 274 s | **9 s** |
| 20-init × 120-lead forecast eval (2400 steps), peak search | 30.4 min | **1.0 min** |

Peak RSS also drops: the 32 × 721 × 1440 float64 distance temporary
(~268 MB per chunk per call) is gone; the largest remaining temporary is one
window.

After the change the per-step profile is flat (windowed `find_contours`,
`scipy.ndimage` max-filter for `peak_local_max`, xarray `DataArray`
construction per peak) — there is no second hot spot worth a PR here.

### Tests

`tests/test_tropical_cyclone_locality.py` (69 cases) pins the behaviour against
reference implementations of the replaced code:

* `_create_spatial_mask` **exactly equals** the whole-field mask over random
  point sets × {0.25°, 1°} grids × {1, 5, 12}° radii × {1, 7, 40} points;
  explicitly for discs straddling the dateline and sitting on the poles; and on
  0–360/descending, −180–180/ascending and non-uniform grids.
* contour equivalence, stated precisely: the contours that lie *well inside* the
  window are the same objects either way (asserted pointwise, `atol=1e-9`).
* `find_valid_candidates` accept/reject decisions are identical global vs
  windowed for every minimum of the fixture.
* end-to-end: `_find_peaks_for_time_slice` returns **the same peak set** with
  the window as when the window is patched out (`unittest.mock`), and the
  fixture is asserted non-vacuous so the comparison cannot pass trivially.

Existing suite: `tests/test_tropical_cyclone.py` 43 passed; the wider
`tests/` suite passes unchanged (`--ignore=tests/test_integration.py
--ignore=tests/test_golden.py`, which need network/golden data).

### Notes for the reviewer

* The window is derived from parameters the tracker already has
  (`max_gc_distance_*_degrees` + a 1° marching-squares margin), so nothing new
  is tunable.
* `find_contours_from_point_specified_field` returns contours in **full-field
  index space**, so no caller changes are needed.
* Not touched, but visible once the mask is cheap: `_find_peaks_for_time_slice`
  wraps each slice in a fresh `xr.DataArray` per peak (now the next-largest
  line in the profile), and `calc.haversine_distance` re-evaluates
  `np.radians`/`np.cos` on both operands, which cannot be hoisted because the
  coordinates are not hashable.

---

## Push commands (NOT run)

```bash
cd ~/scratch/ExtremeWeatherBench                      # clone of brightbandtech/ExtremeWeatherBench
git remote -v                                          # origin = https://github.com/brightbandtech/ExtremeWeatherBench.git
git remote add fork https://github.com/RLK5900/ExtremeWeatherBench.git   # owner's fork — create it first
git push -u fork perf/tc-locality-search
# then open the PR against main:
gh pr create --repo brightbandtech/ExtremeWeatherBench --base main \
  --head RLK5900:perf/tc-locality-search \
  --title "perf(events): search the neighbourhood of a peak, not the whole globe" \
  --body-file <the PR body above>
```

Sign-off (upstream has no DCO check; the repo's Justfile runs
`uv run ruff check` + `uv run pytest`):

```bash
cd ~/scratch/ExtremeWeatherBench && uv run ruff check src tests benchmarks && uv run pytest tests/test_tropical_cyclone.py tests/test_tropical_cyclone_locality.py -q
```
