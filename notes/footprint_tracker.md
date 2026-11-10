# Heat/freeze tracking: the footprint/core methodology (port of `aaTman/ewb-events`)

Owner rule, 2026-11-10. Supersedes, in order:

1. EWB's upstream blob tracker (`expand_event_bounds` + `_terminate_declined_events`) —
   see `notes/ewb.md`; it grew boxes 1°/iteration while an edge stayed half-active
   (7–10 M km² boxes off a 200 k km² seed), ended events relative to their own peak,
   and tracked one object as 2–3 overlapping events (live 2026-09-12 had three heat
   waves on one start date).
2. The same morning's interim area rule (200 k seed → seed bbox + 250 km pad → end
   below 100 k km², 31-day cap) — `notes/temperature_area_rule.md`. Its code
   (`ewb_live/events/temperature_detect.py`) is deleted by this change; the note stays
   as the record of why the pad rule was not enough: a fixed 250 km pad cannot follow
   an event that moves, and "in-box area below 100 k km²" never fires where the
   climatology is exceeded all season (Amazon, Sahara/Sahel, Gulf), so those spells
   needed an arbitrary duration cap.

## What the rule is

The short version is in SPEC §2. Mechanics, in the order the tracker applies them
(`ewb_live/events/event_tracker.py`, all thresholds in its `Params` dataclass):
region grouping (closing + local density) → candidate at 50 k km² → event at 200 k
km² of footprint → box that grows with the footprint (drift ≤ 8 cells/day, ≥ 50 %
overlap of the smaller side, older events claim first) → end when the **core** (valid
every day since the start, measured inside the day-3 **anchor box**) holds < 100 k km².

The two ideas that actually buy something over the pad rule:

* **Qualify later than you seed.** A spell seeds at 50 k km² and has 5 days to reach
  200 k, so a heat wave that builds over three days keeps its early start date instead
  of starting on the day it crossed 200 k.
* **End on the core, not on the box.** A permanent p85 belt keeps 200 k km² *somewhere*
  in its box forever, but the cells that were valid on day 1 do not stay in place: the
  core drains and the event closes on its own (33 days is the live maximum, and it is a
  real central-African spell). No duration cap is needed, so `validate.MAX_DURATION_DAYS`
  moved 31 → 45 purely as a nonsense tripwire.

`Params` defaults are the reference CLI's defaults (checked against the reference run's
own `params` block): seed 50 000 km², qualify 200 000 km², end 100 000 km², min_days 3,
max_ramp_days 5, region_radius 6, region_min_fraction 0.3, move_cells 8, min_overlap
0.5, join_area 10 000 km², anchor_day 3, max_box_area None.

## Port layout

| reference (`aaTman/ewb-events`, branch `dev`, HEAD `e525504`) | this repo |
| --- | --- |
| `src/ewb_events/geometry.py` (Box, circular_span, dilate, label_regions) | `ewb_live/events/event_tracker.py` (same functions, same semantics) |
| `src/ewb_events/tracker.py` (Params, Tracker.step, detect_events) | `ewb_live/events/event_tracker.py` (`Params`, `Tracker.step(day, mask)`, `track_events(mask, dates, cell_area, params, hazard)`) |
| `src/ewb_events/masks.py` (frame decode → daily masks) | `ewb_live/events/frame_masks.py` (reads the lane's `index.json` + the same zlib/packbits frames through `ewb_live.viz.exceedance.frame_layers`) |
| `src/ewb_events/cli.py` (`event_record` JSON) | `ewb_live/events/temperature.py` (`Event` + `details`) |

Deliberate differences from the reference:

* `dt.date` days instead of `pandas.Timestamp`; `track_events` takes numpy arrays
  instead of an `xarray.Dataset`, so it is unit-testable without xarray.
* Boxes are reported on **cell centres** (`lats[i0]..lats[i1]`), the `ewb_live`
  convention every other producer uses; the reference reports cell **edges** (±0.125°).
  Every published bbox in this repo keeps the centre convention, and the antimeridian
  wrap convention of SPEC §2 is applied on top (`lon_min > lon_max`).
* The reference's `--cell-streak` experiment (a cell must pass N consecutive days before
  it counts as valid) is not ported: the lane already ANDs the four 6-hourly steps, and
  the production choice is the reference default `cell_streak = 1`.
* Inputs come from the published frames lane when it spans the window (the reference read
  its own downloaded copy of the same bytes); the ERA5 ARCO path through the upstream
  helpers stays as the fallback (different clip, different climatology mode, or a lane
  whose head/tail is behind the window — logged with the reason).

## Validation (LXC, 2026-11-10, `scripts/review/evt02_tracker_port_compare.py`)

Window 2026-08-11..2026-11-02, 336 frames, the reference's own downloaded lane, so both
sides read identical bytes.

* **Port vs reference:** 175 events each (153 heat_wave, 22 freeze). The sets of
  `(hazard, start, end)` triples are **identical** — 0 port-only, 0 reference-only — and
  peak-area medians/maxima agree (heat 676.6 M / 8 397.3 M km², freeze 614.4 M / 2 155.1
  km²). Duration medians 4 d (heat) / 3 d (freeze), p90 7 d, max 33 d.
* **Publish gate:** 0 rejects of 175 (the 33-day event passes under the 45-day ceiling).
* **vs the published catalogue** (deployed upstream-tracker output): heat 61 → 153 raw
  events, freeze 2 → 22. The published-only entries are mostly fragments of a longer new
  event (the matcher asks for ≥3 shared days and bbox IoU ≥0.15, and a new 20-day box is
  several times the area of the 3-day published fragment inside it), which is the point
  of the change: one spell, one event.
* **Funnel** (merge → rank → cap 15, with the still-live AR events unchanged):
  15 heat_wave / 12 freeze / 15 atmospheric_river curated. Curated heat durations are
  5–20 days (production shows 3–7), and freeze goes from 2 curated to 12.

## Open product questions (for the owner, not blockers)

* **Region chaining can make one giant object.** `region_radius` = 6 cells closes gaps of
  1.5°, which can chain the Sahara–Arabia–India p85 belt into one event: the window's
  largest footprint is 8.4 M km² and its box spans 30° of latitude. The knob is
  `Params.max_box_area_km2` (a cap stops growth and leaves the rest of the region to a
  neighbour event — covered by `tests/test_event_tracker.py`), or a smaller
  `region_radius`. Reference-faithful default is no cap.
* **12 of 15 freeze slots** are now filled by percentile-relative freezes (peak values
  3–19 °C in desert regions, which SPEC §2 already documents as legitimate). If the
  dashboard should not spend a fifth of its cards on those, that is a curation-floor
  decision (`select._passes_floor`), not a detection one.
* **Window-head truncation.** The lane is a rolling 84-day window; when it starts after
  the scan window, the scan falls back to ERA5 (and says so). Events cannot start before
  the lane's first day, hence `details.window_start_truncated`.

## Reproduce

```bash
# on the ewb LXC, scratch checkout (production /opt/ewb-live untouched)
cd /opt/ewb-events-live && EWB_LIVE_CACHE=/opt/ewb-events-live/data/cache \
  /opt/ewb-live/.venv/bin/python scripts/review/evt02_tracker_port_compare.py \
    --start 2026-08-11 --end 2026-11-02 --lane laneref \
    --reference /opt/ewb-events/output/events_streak1.json \
    --published /opt/ewb-live/data/results/events.json --out /tmp/evt02.json
# laneref/ = symlinks to /opt/ewb-events/data/raw/*.bin + an index.json describing the
# grid (ny 721, nx 1440, lat0 -90, lon0 -180, step 0.25) and cutoff = last frame.
```
