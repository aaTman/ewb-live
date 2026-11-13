# EWB Live — Product Spec

Goal: an operational, live-updating dashboard that (1) finds the last 90 days of extreme
weather events (heat waves, freezes, atmospheric rivers) using ExtremeWeatherBench (EWB)
defaults, (2) scores AI weather models + IFS HRES on those events with EWB default metrics,
(3) shows a scorecard comparing models. Reference UX: Brightband's operational WeatherBench
dashboard (research it on the web: brightband.com, github.com/brightbandtech).

## Ground rules for all agents
- Python project managed by `uv` at repo root (`pyproject.toml`, Python 3.11+). Package: `ewb_live/`.
- Install EWB from source: `github.com/brightbandtech/ExtremeWeatherBench` (read its code;
  use its defaults: case/event types, targets, derived variables, metrics, thresholds).
- **Reuse EWB before writing anything.** The full EWB repo is at `third_party/ExtremeWeatherBench/`. Its
  `data_prep/` scripts are the reference implementations: `temperature_events.py` (heat waves/freezes),
  `ar_bounds.py` (ARs), `cira_icechunk_generation.py` (reference only: we build a NEW CIRA store on the latest Icechunk; do not use the hosted EWB store), `era5_climatology.py`,
  `generate_ghcnh.py`. Wrap or refine them; document any deviation.
- **Context handoff (mandatory).** When your context window reaches 90% (see the `used/total` counter in
  your status bar), stop and: (1) write `notes/handoff_<agent>_<n>.md` with goal, current state, what's done,
  what's in progress (running processes or PIDs, log files), next steps in order, gotchas, and file ownership;
  (2) launch your successor in this worktree:
  `orca agent launch --agent pi --surface terminal --prompt "You are agent '<agent>' (successor <n+1>). Read SPEC.md, then notes/handoff_<agent>_<n>.md, and continue the work from there." --json`;
  (3) post the new terminal handle at the top of your handoff file and in any channel files you use; (4) stop working.
- **Portability (product goal).** EWB Live will be handed to Brightband to run on a cloud container or GCP
  instance of their choosing. Don't hardcode hosts, IPs or absolute paths (/opt/ewb-live, 192.168.0.121,
  /opt/ewb-owb, /home/orca). All locations come from one config (env vars + `ewb_live/config.py`) with
  sensible defaults. Storage paths must accept local paths or gs://, s3:// URLs (fsspec/obstore; Icechunk
  local or GCS/S3 storage). Everything runs headless from a container. See notes/portability_plan.md.
- **Public site hygiene.** https://ewb.espr.ai is public. Nothing it serves may reveal the owner's infrastructure
  (LAN IPs, hostnames, ports, absolute paths, usernames, box specs, tunnel/tool names). Enforced by
  scripts/public_scrub_check.sh in deploy and weekly runs.
- Do NOT git commit. Do not edit files owned by another agent; if you need a change, write it in
  `notes/<your-agent>.md` under "Requests".
- Keep a log of decisions/blockers in `notes/<your-agent>.md`.
## Where code runs (hard rule, product owner 2026-11-06)
- **Edit here, run on `ewb`.** No EWB compute may run on this container
  (hostname `orca`, where Orca and the agents live). All execution happens on the
  dedicated EWB LXC: `ssh root@192.168.0.121` (hostname `ewb`, 16 cores, 128 GB RAM),
  deployment dir `/opt/ewb-live`.
- Use the wrapper: `ops/remote.sh run <cmd...>` (sync + foreground run with `.venv`),
  `ops/remote.sh bg <name> <cmd...>` (detached, log `logs/<name>.log`),
  `ops/remote.sh logs <name>`, `ops/remote.sh status`, `ops/remote.sh pull-results`.
- Heavy CLI entry points call `ewb_live._guard.require_ewb_host()` and exit unless on
  `ewb` (override: `EWB_LIVE_ALLOW_LOCAL=1`). Offline unit tests are unaffected.
- Dashboard: served by `ewb-live-web.service` on the box — **http://192.168.0.121:8050/**.
- Everything runs headless; cache under `data/cache/` (on `ewb`).
- Anonymous S3 access (`s3fs`/`fsspec` with `anon=True`). No credentials.
- Write tests in `tests/` (pytest), keep them fast (mark network tests `@pytest.mark.network`).

## Module contracts (stick to these signatures)

### 1. `ewb_live/sources/` — forecast data (owner: agent `sources`)
```python
def list_models() -> list[str]          # e.g. ["ifs_hres", "graphcast", "aifs", "fourcastnet", ...]
def available_inits(model: str, start: datetime, end: datetime) -> list[datetime]
def open_forecast(model: str, init: datetime, variables: list[str],
                  lead_hours: list[int] | None = None,
                  bbox: tuple[float,float,float,float] | None = None) -> xr.Dataset
```
- Returned Dataset uses EWB's forecast conventions (dims `init_time`, `lead_time` (timedelta),
  `latitude`, `longitude`, optional `level`; EWB variable names e.g. `surface_air_temperature`,
  `specific_humidity`, `eastward_wind`, `northward_wind`, `air_pressure_at_mean_sea_level` —
  confirm exact names in EWB source). Longitudes -180..180 or 0..360 consistent with EWB.
- Must also expose an EWB-compatible forecast object if EWB's evaluation API needs one
  (e.g. `make_ewb_forecast(model) -> extremeweatherbench.inputs.<Forecast class>`).
- Sources: AI models from the **Sierra MLWP store on AWS Open Data** (find it on
  registry.opendata.aws — search "MLWP", "Sierra"); IFS HRES from **AWS Open Data
  `s3://ecmwf-forecasts`** (ECMWF open data, GRIB2, 0.25°).
- Daily 00Z inits are enough; lead times 0–240 h at 6/12/24 h steps (whatever is available).

### 2. `ewb_live/events/` + `ewb_live/scoring/` (owner: agent `ewb`)
```python
def detect_events(start: date, end: date) -> list[Event]   # last 90 days
# Event: id, event_type in {"heat_wave","freeze","atmospheric_river"}, title, start, end,
#        bbox (lat_min, lat_max, lon_min, lon_max), peak_value, details(dict)
def score_event(event: Event, models: list[str]) -> pd.DataFrame
# columns: event_id, event_type, model, metric, lead_time_hours, value, init_time
```
- Detection must mirror EWB's case definitions/thresholds (heat wave & freeze: EWB's
  climatology percentile approach; AR: EWB's IVT/AR-mask approach). Target/truth: EWB default
  targets where they are available with low latency (ERA5 via ARCO, GHCN); if ERA5 latency
  is too high for recent days, fall back to IFS HRES 0-h analysis as the truth and record it.
- Scoring must call EWB's own evaluation machinery (`extremeweatherbench` evaluate/metrics)
  with each event type's default metrics.


- **AR rule (product decision):** an atmospheric river must start over the ocean and end on land (landfall). All-land AR objects are not events.
- **`qualified_at` (merged-global-map contract, owner 2026-11-08):** every event in `events_selected.json` carries `qualified_at` — the earliest 6-hourly ERA5 frame in the event window whose detector frame-level mask (exactly the published `raw_exceedance` layers: heat > p85 / freeze < p15 / upstream AR mask) covers at least the detection area floor **inside the event bbox**: 200 000 km² (upstream seed-area floor; cell-area converted at the bbox's mean |lat|) for heat/freeze, 500 pixels (`min_mask_gridpoints`) for AR. Day-level criteria (3-day AND-chain persistence; AR ocean-start/landfall) are carried by the event itself, so `qualified_at` is the sub-daily qualification time and never precedes the event start. If no frame in the window qualifies (e.g. frames end at the ERA5 mirror cutoff first), `qualified_at` = 00:00 UTC of the event start date. Implementation: `ewb_live/events/qualification.py`; the field is publication-level only (outside `Event`, fingerprint, scoring caches).
- **AR tropical exclusion (owner, 2026-11-08; revised 2026-11-10):** in addition to EWB's upstream AR mask filter, (a) an AR track whose **area-weighted centroid stays between 15S and 15N for a full day (4 consecutive 6-hourly steps)** is DROPPED entirely — a tropical *cell* is not the test, the centroid is, and a brief plunge across 15 deg is weather — and (b) cells of an AR track that come within the IBTrACS TC screen radius are removed **at the steps they touch (2026-11-10: step level, was whole-track)**; a track inside a cyclone at *every* step is dropped entirely. Both apply EVERYWHERE: detection/event list, scoring, and the raw AR layer on the global map, at the level of TRACKED AR objects: ARs are tracked across 6-hourly steps with unique persistent track ids, and the tropical keep/drop decision applies to the whole track (no frame-to-frame flicker). Tracks persist through 1-step (6 h) gaps where the mask briefly dips below threshold; the gap frame shows the last footprint, flagged as bridged (display/tracking only; detection unchanged — the raw layer paints those cells faintly from `.bin` plane 3 so a ghost never looks like a physical cell). Documented deviation from upstream. AR curation floor: landfall on >= 1 day (owner, 2026-11-09; was 3). Publish-gate drops must always be logged, **and auditable by location**: every entry in `ar_tracks.json` `dropped[]` carries `bbox` + centroid, and every partially TC-screened track carries `tc_steps` (the owner reports of 2026-11-10 could not be attributed from the ledger otherwise — notes/ar_outage_20261110.md §2). Measured failure that moved the TC rule to step level: `ar_20260811T00_9` (170 623 cells / 56 steps) and `ar_20260815T12_254` (73 422 cells) each erased weeks of mid-latitude AR on one storm touch, which is the owner's "IVT values just disappear" (north Atlantic 08-12 18Z -> 08-13 00Z: 1 304 -> 85 painted cells against 5 238 real mask cells) and "disappears then pops back" (south Atlantic 08-14 12Z -> 08-15 18Z) reports.
- **AR IVT magnitude plane is the FIELD, not the paint (owner report, 2026-11-10):** the `raw_exceedance` `.ivt` sidecar publishes IVT bins over the whole IVT field — low 3 bits = class (0 = <250, then 250-500, 500-750, 750-1000, 1000-1250, >=1250 kg m-1 s-1), bit 7 = IVT >= 400 (the detector's own threshold); `index.ivt = {schema: 2, bins, ar_min, above_bit}`. It must never be masked by the AR mask: the dashboard colours AND hovers the AR layer from this plane, so a paint-masked plane made the published `.ivt` non-zero set cell-identical to the AR paint, and every screen or marginal-`|∇²IVT|` flicker deleted the IVT values along with it (5 515 cells at IVT >= 400 in the north Atlantic at 08-13 00Z vs 85 painted). What the screens decide is what counts as an AR (`.bin` plane 2 = the paint bit); the physical field is always published. The frontend paints AR colour only where the paint bit is set, a faint tint for IVT >= 400 cells that are not painted, and hover labels the difference. Implementation: `ewb_live/viz/exceedance.py::ivt_class_plane`, `dashboard/mergedmap.js`.
- **AR track identity (owner, 2026-11-10):** because exclusion is track-level, a track must be ONE physical object. A child links to a parent only when (i) overlap/child-size >= 0.25, (ii) the parent/child centroids are within 900 km per 6-hourly step (`LINK_MAX_KM`, ~41 m/s — generous versus real AR advection), and (iii) the parent track is younger than 40 steps / 10 days (`LINK_MAX_STEPS`). Measured failure this fixes: with overlap alone, one chain grew to 132 steps / 1.92M cells (`ar_20260811T00_4`), a single tropical cell then hid a month of mid-latitude ARs (6.74M cells hidden vs 7.66M painted, 54% of it in 10 tracks), and the same physical AR "appeared" fully formed 6 h later under a fresh id when the chain re-seeded (owner report: south Atlantic, 2026-09-07 06Z -> 12Z). Implementation: `ewb_live/events/ar_filter.py`.
- **Antimeridian bbox convention (canonical, owner 2026-11-08):** all bboxes use
  `lat_min, lat_max, lon_min, lon_max` with longitudes in `[-180, 180]`. A bbox whose
  longitude arc crosses the antimeridian is encoded with **`lon_min > lon_max`**, meaning
  "from `lon_min` eastward across ±180 to `lon_max`" (e.g. `lon_min=156.75, lon_max=-165`
  is a ~38° wide Pacific box, NOT 341° wide). Non-crossing bboxes always have
  `lon_min <= lon_max`. This is the single convention everywhere: in `events_selected.json`
  / `events_detected.json` (the events.json schema), in `Event.bbox`, in every
  `to_ewb_case()` region (EWB's `BoundedRegion` implements the same wrap test), and in
  the published `global_timeline.json` bbox fields. Consumers MUST test
  `lon_min > lon_max` before treating the interval as a plain `[min, max]` range —
  rendering or subsetting it naively yields a ~360° wide region. (`ewb_live.sources.
  _transform.lon_indices` / `subset_bbox` are the reference implementation; viz grids
  for crossing boxes use a continuous eastward lattice with longitudes > 180, which
  Leaflet renders natively.)
- **Temperature thresholds (product decision):** heat wave and freeze finders use the ERA5 2 m temperature
  climatology built from /opt/ewb-owb/data/clim2 (1990-2020, 6-hourly; method from /opt/ewb-owb/clim.py),
  written to /opt/ewb-live/data/clim/t2m_owb_1990_2020.zarr on the ewb box. It replaces EWB's default climatology.
  Thresholds: p85 for heat waves (owner, 2026-11-07, after the science review of missed heat waves; EWB-native), p15 for freezes (owner, 2026-11-07; EWB-native; zero freezes acceptable if none qualify).
- **Heat/freeze tracking rule (owner, 2026-11-10; the footprint/core methodology, ported from `aaTman/ewb-events` into `ewb_live/events/event_tracker.py`; documented in `notes/footprint_tracker.md`, which supersedes `notes/temperature_area_rule.md`):** the thresholds and the daily exceedance mask (all four 6-hourly steps pass, land only) stay EWB-style; tracking is neither EWB's blob tracker nor the same morning's 200 k-seed/+250 km box rule. Identical for heat waves and freezes, on the daily mask `M[d]`:
  1. **Region:** each day the valid cells are grouped by a morphological closing — one dilation and
     erosion with a `(2r+1)²` square element (`region_radius`=6 cells, so holes up to 12 cells wide are
     filled; longitude wraps, latitude does not) — plus a local-density test: at least 30&nbsp;% of that
     13×13 window must be valid, which keeps a thin scatter from chaining across a continent and means
     bridging only happens inside an otherwise dense area. Valid cells always join; components are
     labelled with 8-connectivity.
  2. **Candidate:** a region whose valid area reaches **50 000 km²** (`seed_area_km2`) starts a candidate on its first qualifying day; a candidate must reach the qualify floor within `max_ramp_days`=5 days or it is dropped.
  3. **Qualify:** the candidate becomes an **event** on the first day its *footprint* covers **200 000 km²** (`qualify_area_km2`) — `details.qualified_date`. The event's `start` stays the candidate's first day, so ramp-ups keep early start dates.
  4. **Box:** the event box is the bbox of everything its footprint has covered, so it grows with the event (no fixed pad, no per-iteration expansion). The footprint may claim new region cells within `move_cells`=8 cells/day of what it already holds, and only where the region is claimed by nobody or overlaps an existing event's box by >= `min_overlap`=0.5 of the smaller side; older events claim first. `max_box_area_km2` (default `None`) caps growth where an owner wants smaller objects.
  5. **End:** the **core** — the cells valid on *every* day since the event began, counted inside the **anchor box** (the box as it stood on day 3, `anchor_day`) — must hold >= **100 000 km²** (`end_area_km2`). Below that the event closes and that day is not part of it. This is what ends permanent-belt spells physically (longest in the live window: 33 days over central Africa) without a duration cap; `end` is exclusive.
  An event still open at the end of the scan window is `ongoing` and keeps its start date across runs. `min_days`=3 is a detection floor (a shorter spell is not an event). Because 31-day caps are gone, the publish-gate ceiling is `validate.MAX_DURATION_DAYS = 45` (and `select.MAX_MERGED_DAYS`), above the longest real spell and below nonsense.
  **Mask source:** the daily masks are read from the published `raw_exceedance` frames (four steps ANDed; `ewb_live/events/frame_masks.py`) whenever the lane spans the window and the active clip is the lane's owb p85/p15, so detection cannot drift from what the map paints or from `qualified_at`; otherwise ERA5 ARCO is streamed through the upstream helpers. Daily-mean temperatures for `peak_value` come from the local ERA5 mirror on the same grid (`_mirror_t2m_daily`), per event when the mirror cannot serve the window.
  `qualified_at` scans frames from `details.qualified_date` onwards, so a grown box cannot qualify an event before its own footprint reached the floor.
  Per-event provenance is published in `details`: `qualified_date`, `duration_days`, `ongoing`, `footprint_area_km2`, `box_area_km2`, `core_area_km2`, `anchor_box`, `mask_source`, `peak_area_km2`, `tracker_params`, `detection`.

### 3. `ewb_live/pipeline.py` (owner: agent `ewb`, integration later)
`python -m ewb_live.pipeline --days 90` → writes:
- `data/results/events.json` — list of Event dicts
- `data/results/scores.parquet` and `data/results/scores.json` — rows as above
- `data/results/scorecard.json` — aggregated: per event_type × metric × lead_time × model,
  mean value, n_events, and relative skill vs `ifs_hres` (% difference, sign normalised so
  positive = better than IFS).
- `data/results/meta.json` — last_updated (UTC ISO), window, models, truth source.
Incremental: re-runs reuse cached scores; only new events/inits are computed.

### 4. `dashboard/` (owner: agent `design`; earlier `dashboard`, then `ops`)
- Static web app (no build step required preferred: HTML + JS, e.g. Plotly/Observable Plot via
  CDN, or a small Vite app with built output committed) reading `data/results/*.json`.
- Served by `python -m ewb_live.serve --port 8050` (simple static server, serves `dashboard/`
  and `data/results/`).
- Pages/sections (modeled on Brightband's operational WeatherBench dashboard):
  1. Header: "last updated", window, truth source.
  2. **Scorecard**: model × (event type, metric, lead time) heat-map table, colored by
     relative skill vs IFS HRES (blue better / red worse), raw values in tooltips.
  3. Lead-time line charts per event type & metric, one line per model.
  4. Event catalog: map (Leaflet/Plotly) of detected event bboxes + filterable table.
  5. Event detail view (KEY ORIGINAL FEATURE): every event box on the map is clickable and opens a summary
     (type, dates, region, peak, truth source, per-model mini scorecard) with a visual modeled on the
     ExtremeWeatherBench paper's per-event-type figures. Assets come from `ewb_live/viz/` (owner: design),
     written to `data/results/events/<event_id>/`, generated weekly after scoring.
     Heat wave/freeze visual = consecutive days above/below the EWB climatology threshold (observed vs. each
     model at a selected lead). AR visual = IVT map + observed AR mask contour + forecast mask overlap.
     Presentation reference: https://windbornesystems.com/blog/introducing-windborne-weather-events
  Also reference dynamical.org scorecards and WeatherBench 2 scorecards for conventions.
- **Methodology explainer (About dialog, owner 2026-11-10):** the About dialog is the page that
  explains the detection criteria — the daily rule plus the five tracker steps (group / seed /
  qualify / follow / end) with the numbers the detector actually uses, then a collapsed
  "Reproduce the tracker in Python" fold that fetches `dashboard/detection.py` (authored, plain
  numpy + scipy, `python3 detection.py` prints a worked example). The listing is documentation that
  people run, so `tests/test_detection_page.py` runs the same masks through it and through
  `ewb_live/events/event_tracker.py` and fails if the two disagree, and checks the About wording
  against `Params` — a threshold change must not leave the page describing the old rule. Keep new
  methodology text here (not on the main view) and keep the numbers derived from `Params`.
- Must work with the sample data in `data/results/sample/` until real data exists
  (dashboard agent generates realistic sample files following the schemas above).
- Header shows the weekly cycle: "Week YYYY-WW · updated <date> · next update <date>"; optional week picker over data/results/history/.

### 5. Ops — WEEKLY refresh cycle
The dashboard updates **once a week** (Mondays 07:00 UTC, systemd system timer on the
`ewb` LXC — 07:00 avoids the `/root/mlwp-icechunk` 06:00 daily cron; server always on).
CIRA store: VIRTUAL (refs only, no downloads), covers the FULL CIRA MLWP archive (no rolling window), and updates DAILY at 05:30 UTC (ewb-live-store.timer).
Each weekly run, in order: (1) `mlwp_icechunk update` (catch-up no-op if the daily update ran) + refresh IFS/other sources with the new week's
inits, (2) **new search for extreme events** over the rolling last-90-day window (events can be added,
extended or re-bounded as truth data fills in), (3) **new benchmarking/eval** for all new or changed
events and any newly available forecasts, (4) rewrite data/results/*.json atomically, (5) archive a
snapshot to data/results/history/<YYYY-WW>/ so week-over-week changes are viewable.
meta.json gains: `cycle: "weekly"`, `week` (ISO year-week), `next_update` (UTC ISO).
Entry point: `python -m ewb_live.weekly` (orchestrates steps 1–5, logs to logs/weekly-<date>.log,
non-zero exit on failure, safe to re-run).
