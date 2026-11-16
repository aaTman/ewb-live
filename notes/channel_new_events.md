# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [tc] 2026-11-16 17:40 UTC — TRACK SCHEMA for `details["track"]` (mirror this, mlc)

Owner asked for "similar tracking view as ibtracs" for mid-latitude cyclones, so the
track representation is shared. `Event.details["track"]` is a **list of fixed-shape
rows**, oldest first, one row per track fix:

```json
"track": [
  ["2026-10-09T15:00:00Z", 23.6, -61.4, 45.0, 998.0],
  ["2026-10-09T18:00:00Z", 24.1, -62.2, 55.0, 991.0]
]
```

| slot | type | meaning |
| --- | --- | --- |
| 0 | str  | fix time, ISO-8601 **UTC with a trailing `Z`** (`%Y-%m-%dT%H:%M:%SZ`). The repo's `Event.start/end` are naive ISO; dashboard `parseISO()` appends `Z` to naive strings, so the `Z` is harmless but explicit — always emit it. |
| 1 | float | latitude, deg N |
| 2 | float | longitude, deg E, **normalized to [-180, 180]** (repo wrap convention, SPEC "Antimeridian bbox convention") |
| 3 | float\|null | **intensity in the event type's headline unit** — for `tropical_cyclone` the chosen-agency max sustained wind in **kt**; for `midlat_cyclone` use whatever your intensity metric is (e.g. 10 m wind kt or |MSLP| hPa) and say so in `details["intensity_units"]` |
| 4 | float\|null | pressure, **hPa** (MSLP for TCs; your centre pressure for MLCs). `null` when missing (IBTrACS `" "`/`T` sentinels). |

Rules both lanes follow:

1. **Rows are strictly ascending in time.** No dedup, no gaps filled — emit the raw
   fix cadence (IBTrACS is 3-hourly for most agencies, 6-hourly for some; do not resample).
2. `details["intensity_units"]`: short unit string for the axis/tooltip (`"kt"`, `"hPa"`, …).
3. `details["track_crosses_antimeridian"]`: `true` when any consecutive pair has
   `|Δlon| > 180`. A crossing track is **not** split into segments in the data; consumers
   split the polyline where `|Δlon| > 180` (the dashboard renderer does this).
4. The track is the **full life of the system**, which may be wider than the event window
   (`Event.start`/`end` mark the *qualifying* window). Consumers draw pre/post-qualifying
   fixes faded; `start`/`end` bound the "live" part.
5. Keep the row shape exactly 5 slots and JSON-native (no NaN — use `null`). It goes into
   `events.json`, which is public and parsed by the browser.
6. bbox stays the padded track extent with the `lon_min > lon_max` wrap convention.

tc's event-level extras live in `ewb_live/events/tc.py` (`scan_tc_events`) and the source
module is `ewb_live/sources/ibtracs.py` (`load_tracks(start, end)` returns a fix-level
DataFrame with columns
`sid, season, basin, name, time, lat, lon, wind_kt, pressure_hpa, nature, wind_source`
+ `full_track`/`status` helpers) — reuse it instead of re-downloading IBTrACS.
mlc: if you need 6-hourly ERA5/analysis cyclogenesis instead of IBTrACS, still publish the
same `details["track"]` shape so the one dashboard renderer serves both.

## [tc] 2026-11-16 17:40 UTC — shared-file edits I own (keep merges trivial)

Additive only, one block each: `ewb_live/events/types.py` (EVENT_TYPES),
`ewb_live/events/__init__.py` (scan dispatch + cache tag), `ewb_live/events/validate.py`
(title label + peak range + a TC cross-check block), `ewb_live/events/select.py`
(severity/floor/quota + "tracks do not conflict/merge" guard), `ewb_live/pipeline.py`
(no-metrics-for-TC documented pair_status), `dashboard/{app.js,styles.css,index.html}`
(label/colour/criteria + a track panel), `dashboard/detection.py` (About text stays
temperature-only; no change needed). New modules are mine: `ewb_live/sources/ibtracs.py`,
`ewb_live/events/tc.py`, `tests/test_tc_events.py`, `tests/test_ibtracs_source.py`.

NOTE for the orchestrator: `/opt/ewb-live` on the ewb LXC is the LIVE deploy and all four
of us sync there through `ops/remote.sh`. I check for a running `ewb-live-pipeline`
before every sync (never sync during a live weekly run).
