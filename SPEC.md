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
  `orca agent launch --agent hermes --surface terminal --prompt "You are agent '<agent>' (successor <n+1>). Read SPEC.md, then notes/handoff_<agent>_<n>.md, and continue the work from there." --json`;
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
- **AR tropical-cyclone screen (owner, 2026-11-08):** keep EWB's upstream AR mask filter (component |mean lat| > 15 deg, size >= 500 px, IVT >= 400, Laplacian), and additionally drop AR candidates co-located with IBTrACS tropical-cyclone tracks (using EWB's data_prep/ibtracs_bounds.py). Documented deviation.
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
