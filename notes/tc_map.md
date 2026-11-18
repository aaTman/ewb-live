# TC lane on the merged map — rationale

Owner brief: `briefs-new-events/tc_map.md`, plus the 2026-11-18 addition
("the TC wind field must DISSIPATE when the storm ends"). Delivery entry:
`notes/channel_new_events.md`, thread `[tc-map]`. Preview:
http://192.168.0.121:8053/ (third tree, `/opt/ewb-live-tcmap`, unit
`ewb-live-tcmap-web`; `:8050` live and `:8051` dev untouched).

## What the layer is

Cyclones were published as events and never appeared on the merged world map.
Three separate causes, all in shared files: `mergedmap.js`'s `TYPES` tuple had
no cyclone lane (so cyclone timeline events were filtered out before drawing),
`global_timeline.json` carried no track geometry, and `TrackViz` was wired only
into the event page. The fix is additive: cyclone lanes in `TYPES`, a compact
`track` object per storm in the timeline asset (schema 3), and a new
`dashboard/tracklayer.js` the map calls through `window.onMapTracks(payload)` /
`window.onTrackLegend(payload)`.

Per the owner's direction, **the primary encoding is the wind field, not the
line**: every 1° cell where sustained 10 m wind reached tropical-storm force
(≥ 34 kt) is shaded, classed by Saffir-Simpson, as the daily maximum; the best
track sits on top as thin orientation. Data source: **ERA5 ARCO `10u`/`10v`**
(sustained 10-min mean — the quantity Saffir-Simpson is defined on; the gust
field would overstate every class). Chosen because the local truth mirror has no
10 m wind (pressure levels only) and IBTrACS wind radii (D34/D50/D64) are not in
this repo's cache, so radii are a later option, not today's data.

Rendering is one canvas under the SVG markers: one `Path2D` per class, rows
projected as four points (Equal Earth is linear in x at fixed latitude), columns
that straddle ±180° repainted at the other edge so the field wraps the dateline
the way the tracks and the boxes do. Cells are run-length packed because the
browser has no synchronous inflate: 70 KB for 44 storms and 279 day grids.

Two pointer facts worth keeping: the SVG overlay owns the pointer everywhere its
markers and **labels** cover, and at world scale a storm's own label lies across
most of its field — so the cell under the cursor answers unless the pointer is on
the dot (`DOT_CLAIM_PX = 9`). And with the field present, only the storm's
current position keeps the ground-coloured halo, so ~260 dots do not compete with
the primary encoding.

## Honesty about the resolution gap

ERA5 at 0.25° resolves no eyewall. Across the published storms the field's
maximum near the peak fix is a **median 0.56× the IBTrACS best-track peak**
(0.34× worst, 0.93× best, n=34). No silent correction: the ratio ships in the
asset, the legend unit reads `ERA5 10 m wind · daily max · under-reads peaks`,
the cell tooltip keeps the best-track peak in its own row under a note that the
shading is the wind field and never the official category. A day past the
archive's cutoff gets no entry and is listed in `days_no_data` — never drawn as a
zero field, which would read as "that day made no storm-force wind". 10 of the 44
storms have no storm-force cell anywhere; their `ratio` is `null`, not `0.00`,
because that is an absence and not a measurement.

## The dissipation rule (the owner's addition)

**Root cause of the lingering field, measured before touching anything:** the
producer had no concept of the storm ending. It took the storm's *whole best-track
envelope* — first fix to last fix, ignoring the event's own life window — built a
per-cell daily maximum over it, and the layer then painted that whole day's grid
for **any** frame whose date fell in the range. Three leaks followed mechanically:

| leak | measured on the shipped asset |
|---|---|
| pre-genesis shading (fixes before the event's first qualifying fix) | 35 of 44 storms had such fixes; 113 cells drawn on days before the field existed |
| ET-tail shading (post-tropical fixes, which the lane excludes from its events) | 23 storms had fixes after the event's end; 19 cells drawn after the last one |
| dead hours inside an end-day (the storm died at 09Z, the day was shaded all day) | 436 cells on end-days |
| cells with no live fix near them (stale interpolated position, borrowed ambient wind) | 1.0% of all cells, up to **8.3°** from any live fix |

The rule now, stated once in the producer and enforced twice:

1. **Alive means the lane's event window** — first to last qualifying fix
   (≥ 34 kt, `NATURE` not `ET`/`MX`), exactly what `ewb_live/events/tc.py`
   publishes as `start`/`end`. Nothing outside it is read.
2. **Only the 00/06/12/18Z steps inside that window contribute.** A step is
   alive iff `birth ≤ step < death`, so a storm that dies at 09Z contributes
   00Z and 06Z of that day and nothing after: the day's grid is the max of the
   hours that actually existed (`days[].steps` says which).
3. **The position is interpolated at the step, never carried.** A stale centre is
   how a dead storm's field kept extending.
4. **Attribution is strict**: a cell belongs to the nearest live centre *across all
   storms*, and only inside the ≥ 34 kt connected component containing that storm's
   core. Ambient storm-force wind 6° away is not this storm's field, and neither
   is a neighbour's.
5. **Ongoing means the data ran out, not the best track.** `details.ongoing` says
   the last qualifying fix is the last fix — true of a storm that dissolved six
   weeks before the cutoff too. `life.ongoing` additionally requires a death at or
   after the last 10 m wind step with real values (`dissipation.cutoff`; ARCO's
   time axis is *not* the cutoff — it lists NaN-filled steps ahead of the data), so
   the map draws "still open" for exactly one storm today: Xavier.
6. **The layer refuses to draw what the producer should not have shipped.**
   `aliveAt()` / `dissipatedAt()` gate the field, the cell hit test and the
   tooltips on the same life window, so a hand-edited or stale asset cannot put
   wind behind a dead storm. A dissipated storm keeps its track, loses its field,
   and its marker goes hollow with a tooltip that says `dissipated <date> · no
   wind field now`; the legend has a swatch for it.

Current build: **4031 drawn cells** (3996 TS + 35 CAT 1) over 279 day grids,
against 4353 before — 322 cells of wind that did not belong to a living storm,
and the farthest cell is now 4.98° from a live fix instead of 8.3°.
`python -m ewb_live.viz.tc_wind --audit` prints the storm-by-storm table (days,
cells, cells on the last day, cells after death, cells before birth, max distance)
and exits non-zero on any violation; on the current asset all 44 storms pass.
Wired into the weekly cycle as step 6d in `ewb_live/weekly.py`, guarded and
non-fatal exactly like the raw-exceedance step.

## Verification

* `tests/test_tc_wind.py` — 48 unit tests against a hermetic `FakeStore` shaped
  exactly like ARCO: class boundaries, day windows (dateline averaging, fast-mover
  growth, polar clamping that slides and never wraps), the RLE round-trip,
  missing-day → `days_no_data`, placement contract (a blob at a known lat/lon
  lands at the exact row/col the browser paints), and the dissipation rule:
  no cell on a day before birth or after death, end-days carry only their alive
  steps, a cell 8° from a live centre belongs to nobody, `ongoing` only when the
  data runs out, audit flags a doctored asset.
* `tests/e2e/test_trackwind_fe.py` — 6 Playwright tests on a fixture, with the
  field measured A/B against the same page with the asset aborted (the best track
  is violet too, so a colour count alone proves nothing).
* `scripts/tcmap_check.py` — reads the live :8053 asset, picks a storm that dies
  mid-day (Choi-Wan, death 2026-10-07T09Z, last live steps 00Z/06Z), and measures
  the rendered frame at 06Z and at 12Z: field drawn and hit-tested as Choi-Wan at
  06Z; at 12Z the layer reports `field: false`, the hit test returns nothing, the
  marker reads `data-state="dissipated"`, and 255 violet pixels are gone while
  still-living storms keep theirs. Screenshots:
  `notes/screenshots/tc_map_zoom_last_day_Choi-Wan.png` (field present) and
  `tc_map_zoom_after_Choi-Wan.png` (gone, neighbours untouched).

## Limits and open decisions for the owner

* **Sub-daily dead hours on a day the storm lives through.** If a storm is alive
  at 00Z and dies at 09Z *and* the day is still its last, the 12Z/18Z frames show
  the day's max-of-alive-steps rather than nothing — the asset is daily, so within
  a day the map cannot show an hour the storm did not exist. Fixing that means a
  6-hourly asset (4× the cells, 4× the fetch). Say the word.
* **Bias handling.** The field under-reads peaks by ~0.56× median. Today it is
  labelled, not corrected. Alternatives: a documented rescale for class *colour*
  only (the honest-sounding option that is actually the least honest — it invents
  wind), or IBTrACS radii (needs D34/D50/D64 in the cache), which would draw the
  real observed envelope.
* **Max-over-day vs instantaneous.** The lane shows the daily maximum, so a frame
  at 18Z shows wind that blew at 03Z. Instantaneous 6-hourly fields would be more
  literal and 4× the bytes; and at world scale the daily max is what makes the
  envelope visible at all.
* **Loudness at default zoom.** A 1° cell is ~2 px on the world sheet, so the
  field reads as a wash; the honest levers are IBTrACS radii or a region zoom, not
  a bigger cell.
* `days_no_data` on the current window: 2026-11-04, 11-09, 11-15, 11-16, 11-17 —
  those days are absent from the asset, not zero, and the data cutoff the
  `ongoing` test uses is 2026-11-12T18:00Z.
