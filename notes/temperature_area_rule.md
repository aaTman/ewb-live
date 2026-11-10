# Heat/freeze detection — the 2026-11-10 area rule

Owner instruction (2026-11-10): *"trigger a heatwave or freeze when at least
200k km2 of 3+ consecutive days are reached, add 250 km to each edge, and end
the event once it goes below 100k km2 in that box. same applies to freezes."*

Implemented in **`ewb_live/events/temperature_detect.py`** (new, pure
numpy/scipy, unit-tested in `tests/test_temperature_detect.py`);
`ewb_live/events/temperature.py` keeps its role as the ERA5/climatology front
end and now calls the new tracker instead of the upstream one. AR detection is
untouched (next review).

## Review: why the events looked wrong

Detection ran the upstream EWB blob tracker
(`third_party/ExtremeWeatherBench/data_prep/temperature_events.py`) end to
end. Measured on the live window (2026-08-11 .. 2026-11-03, the box's
`data/results/events.json`, which is the uncapped catalogue):

| symptom | cause |
|---|---|
| Boxes 20-52x the 200k km2 seed (median 2.06M km2, max 10.38M km2 = 2519 x 4119 km) | `expand_event_bounds` (:491) grows every edge `EXPANSION_DEGREES=1` deg per iteration while >=50% of the land in the edge band is active, `MAX_SPATIAL_ITERATIONS=20` (:71-73) — up to 20 deg per edge, unrelated to the seed size |
| Events end after a blob wobble while a big area is still exceedant; median duration 3 d, max 7 d | `_terminate_declined_events` (:1044) ends an event when its blob is missing on a day **or** falls below `AREA_DECLINE_FRACTION=0.5` of **its own peak gridpoint count** — relative and peak-scaled, no absolute floor |
| One object tracked as several overlapping events (8 overlapping same-type heat pairs in the raw catalogue) | `detect_events` (:1125) never suppresses a new seed inside a live event's box |
| 2 freezes over 90 days (1 after curation) | the seed test uses the mask *after* `_consecutive_filter_np(..., max_grace_days=1)` (:221), which both bridges 1-day gaps and trims run edges, then requires the 3-day intersection of that filtered mask to be one component >=200k km2; `events_to_dataframe` (:1339) drops further on `MIN_GRIDPOINTS=500` |

## The rule as implemented

1. **Daily mask** — unchanged upstream: cell-day is active when all four
   00/06/12/18Z steps pass the bound (heat `> p85`, freeze `< p15`, OWB pooled
   climatology) over land.
2. **Trigger** — cells that exceed on day *d* and on each of the previous two
   days (`joint = M[d] & M[d-1] & M[d-2]`), labelled seam-aware
   (`seam.label_periodic`); a component with area >= **200,000 km2** opens an
   event whose first day is d-2. Seeds are tried largest-first.
3. **Box** — that component's bbox padded by **250 km on each edge**: latitude
   pad = 250 km in degrees (3 rows at 0.25 deg), longitude pad = 250 km
   converted at the seed's mean |latitude| (clamped at 80 deg), rounded up to
   whole columns; latitudes clamp at the grid/pole, longitudes wrap (arc
   convention `lon_min > lon_max` for arcs crossing +/-180). The box is fixed
   for the life of the event.
4. **End** — the exceedance area inside that fixed box is recomputed daily; the
   first day below **100,000 km2** closes the event and is *not* part of it.
   Still above the floor when the scan window ends -> `details.ongoing = true`
   and the start date is stable across weekly runs (14-day load buffer, as
   before).
5. **No twins** — a seed whose cells are >=50% inside a live event's box is
   absorbed instead of opening a second event.
6. **Duration cap** — over the Amazon, the Sahara/Sahel, the Gulf and the
   Sahelian belt the daily rule is exceeded all season, so a fixed box's in-box
   area never falls under 100k km2: with the literal rule, 8 heat waves ran
   36-84 days (max in-box area 5.07M km2, min 109k km2), and all 8 were refused
   by the publish gate (`validate.MAX_DURATION_DAYS = 31`). Such an event now
   closes at the cap with `details.duration_capped = true` and a WARNING
   naming it. `EWB_LIVE_TEMP_MAX_DAYS` (default 31, `0` = literal rule, no cap).

`details` keeps every key the dashboard/curation read before
(`peak_area_km2`, `max_consecutive_days`, `mean_consecutive_days`,
`peak_gridpoints`, `mean_temp_c`, `min_temp_c`, `quantile`, `clim_source`,
`operator`, `antimeridian_wrap`) and adds the tracking provenance:
`seed_area_km2`, `seed_gridpoints`, `trigger_date`, `box_pad_km`,
`end_area_km2`, `area_in_box_km2` (per event day), `ongoing`,
`duration_capped`.

## Before / after (same window, same ERA5 mirror, box LXC)

| | before (upstream tracker) | after (area rule) |
|---|---|---|
| raw heat_wave / freeze | 61 / 2 | 77 / **4** |
| curated (top_n=15) heat / freeze | 15 / 1 | 15 / **3** |
| box area, curated (median / max) | 4.58 / 10.38 M km2 | 1.91 / 7.47 M km2 (max = a 1.65M km2 seed + 250 km) |
| duration, raw (median / max) | 3 d / 7 d | 7 d / 31 d (10 capped, 10 ongoing) |
| publish-gate rejections | — | **0** |
| raw same-type time+space overlaps | 8 | 55 (see below) |

Freezes found: NW India (09-29..10-06, -18.7 C), S Africa (09-30..10-05),
Libya/Egypt (10-04..10-10), Canadian Arctic (10-11..10-18, -44.5 C). All pass
the gate and rank above the freeze floor in curation.

**Raw overlaps went 8 -> 55.** They are the persistent belts, where several
genuine 1.3-2.9 Mkm2 boxes coexist for weeks; curation's no-overlap rule keeps
the set clean (0 overlapping pairs in the curated output, verified), but the
raw catalogue (`events.json`, the full catalogue the "all events" view reads)
is now busier. If that matters, the knobs are a stricter seed floor
(`min_area_km2`, currently 200k) and `SEED_ABSORB_FRACTION` (currently 0.5).

## Reproduce

```bash
# on the LXC, scratch tree, reads production data read-only
rsync -az --exclude .venv --exclude .git --exclude data --exclude logs \
      --exclude third_party ./ root@192.168.0.121:/opt/ewb-events-dev/
ssh root@192.168.0.121 "cd /opt/ewb-events-dev && ln -sfn /opt/ewb-live/data data && \
  ln -sfn /opt/ewb-live/.venv .venv && ln -sfn /opt/ewb-live/third_party third_party && \
  EWB_LIVE_TRUTH_CACHE=/tmp/tc.json PYTHONPATH=. .venv/bin/python -u \
  scripts/review/evt01_area_rule_compare.py --start 2026-08-11 --end 2026-11-09 \
  --out /tmp/evt02.json"
```
`scripts/review/evt01_area_rule_compare.py` reads the local ERA5 mirror
(`data/cache/truth/era5.zarr`) instead of streaming ARCO: ~1 min per event
type. Unit tests: `pytest tests/test_temperature_detect.py` (16 cases, no
network).

## Not changed here (flagged for the owner)

- `validate.MAX_DURATION_DAYS` (31) and `select.MERGE_CAP_DAYS` (heat/freeze
  16 d) still cap long spells; the detector now closes at 31 instead of
  producing events the gate refuses. A 40-day heat wave should probably be
  publishable, which means raising both together.
- Curation severity uses `anomaly_K x peak_area_km2 x max_consecutive_days`;
  the capped tropical monsters score *low* on it (small anomaly), so they do
  not crowd the showcase — worth a look if the seed floor moves.
- `qualification.SEED_AREA_KM2` (200k, in-box) is consistent with the new
  trigger: on the seed days the padded box always holds >= the seed area, so
  `qualified_at` lands on/after the first trigger day.
- AR detection untouched, next review.
