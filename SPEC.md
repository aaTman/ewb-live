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
  Thresholds: p85 for heat waves (owner, 2026-11-07, after the science review of missed heat waves; EWB-native), p15 for freezes (owner, 2026-11-07; EWB-native; zero freezes acceptable if none qualify). **Freeze also requires t2m <= 0 degC at all four 6-hourly steps (owner, 2026-11-16)**, applied to the daily mask before tracking (`FREEZE_MAX_C` in `ewb_live/events/temperature.py`).
- **Cold snap (product decision, owner 2026-11-16):** `cold_snap` is the freeze rule with the `<= 0 degC` requirement dropped — the same p15 exceedance mask (all four 6-hourly steps, land only), the same footprint/core tracker and the same area/duration floors; only the per-type temperature ceiling differs (`MAX_C_BY_TYPE`, so the scanner is parameterised rather than copied). It is a third `EVENT_TYPES` lane: same climatology, `qualified_at` reads the published **p15 raw-exceedance layer** with the 200k km² floor (that bit *is* the cold-snap mask, so the frames lane needs no new layer), and it is scored with freeze's EWB defaults (ERA5 2 m t2m target, MinMAE/RMSE/DurationMeanError). Because a freeze region is a subset of the cold-snap mask, the same spell would otherwise be published and scored twice: the raw catalogue `events.json` keeps both lanes (each cold snap carries `details.subzero_cell_fraction` / `details.contains_subzero`), and the curated `events_selected.json` drops a cold snap that overlaps a freeze in time+space in favour of the freeze — the more specific claim — under the owner's no-overlap rule (2026-11-06). Implemented in `ewb_live/events/cold_snap.py` (`subsume_freeze_overlaps`, called from `detect_events` before curation); every drop is logged to `curation.json` as `reason: "freeze-subsumed"`.
- **Heat/freeze tracking rule (owner, 2026-11-10; the footprint/core methodology, ported from `aaTman/ewb-events` into `ewb_live/events/event_tracker.py`; documented in `notes/footprint_tracker.md`, which supersedes `notes/temperature_area_rule.md`):** the thresholds and the daily exceedance mask (all four 6-hourly steps pass, land only) stay EWB-style; tracking is neither EWB's blob tracker nor the same morning's 200 k-seed/+250 km box rule. Identical for heat waves and freezes, on the daily mask `M[d]`:
  1. **Region:** each day the valid cells are grouped by a morphological closing — one dilation and
     erosion with a `(2r+1)²` square element (`region_radius`=6 cells, so holes up to 12 cells wide are
     filled; longitude wraps, latitude does not) — plus a local-density test: at least 30&nbsp;% of that
     13×13 window must be valid, which keeps a thin scatter from chaining across a continent and means
     bridging only happens inside an otherwise dense area. Valid cells always join; components are
     labelled with 8-connectivity.
  2. **Candidate:** a region whose valid area reaches **50 000 km²** (`seed_area_km2`) starts a candidate on its first qualifying day; a candidate must reach the qualify floor within `max_ramp_days`=5 days or it is dropped.
  3. **Qualify:** the candidate becomes an **event** on the first day its *footprint* covers **200 000 km²** (`qualify_area_km2`) — `details.qualified_date`. The event's `start` stays the candidate's first day, so ramp-ups keep early start dates. Qualification also **starts the core clock**: from this day (not from the candidate's start) the core and the anchor are evaluated (owner decision 2026-11-21, `Params.core_clock="qualified"`). Under the shipped rule (`core_clock="start"`, still pinned by the flood lane) a growing spell was closed by its own qualifying day — the core still held only the small seed area — so its qualifying day fell outside its dates and the region was free to seed a successor the same day; 34 events in the live catalogue (29 heat + 2 freeze + 3 cold snaps) have `qualified_date == end` that way.
  4. **Footprint (claim rule; revised by owner 2026-11-20, `notes/heatwave_box_diagnosis.md`).**
     The footprint is a set of *valid* cells — cells that pass the daily rule that day —
     never closing-filled cells. A labelled region continues an event only for the part
     of it that is valid **and** within `move_cells`=8 cells of the cells the event
     already holds; the rest of the region stays free and may seed its own candidate.
     A new candidate's first box is the bbox of the region's **valid** cells. Follow and
     order are otherwise unchanged: a region continues an event only where it overlaps
     the drifted footprint by >= `min_overlap`=0.5 of the smaller side, older events
     claim first. **Merge rule (`Params.merge_survivor`=`"largest_close"`, owner decision
     2026-11-21):** when objects reach the same cells, the qualified member holding the
     largest claim that day carries the group on (ties: older). Any other qualified
     member already `min_days` old is closed and published with `end` = the merge day;
     younger members and unqualified candidates are absorbed. Their claims are clipped
     into the survivor's box and two boxes are never unioned (`max_box_area_km2`, default
     `None`, remains an optional cap on box growth). `Params.merge_survivor` keeps the
     other rules as `"oldest_qualified"`/`"largest"`/`"separate"`; the flood/legacy lanes
     pin `"oldest_qualified"` and are byte-unchanged (`notes/footprint_tracker.md`).
     **Missing truth:** a day whose ERA5 field is entirely NaN keeps only the cells valid
     on both neighbouring days, so one missing interior day cannot end an event by itself.
     A missing first or last scan day is not bridged, and two or more consecutive missing
     days still split a spell. A bridged day never counts toward the `min_days` detection
     floor, but published durations and run lengths include it.
     **Cross-type conflict floor (curation, owner 2026-11-23):** the no-overlap
     rule of 2026-11-06 keeps its full force BETWEEN SAME-TYPE events; a
     CROSS-type pair now conflicts (and evicts the lower within-type percentile)
     only when the overlap is material — bbox `containment` = intersection area
     / smaller box area (deg^2, arc-aware) >= `CROSS_TYPE_MIN_CONTAINMENT`
     (default 0.5, env `EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT`). A graze below the
     floor publishes both events side by side (logged as `curation.json`
     `cross-type-graze` entries); real cross-type eviction still
     happens in production (measured: 10 drops on the 2026-11-22 window). The
     failure this fixes: a 4-day IMERG flood clipping the corner of a 17-day US
     heat-wave chain (containment 0.268, IoU 0.059) evicted the chain, and with
     it the continuation fragment carrying the Sep 16–22 heat wave into the
     showcase. TRACK lanes stay exempt (unchanged). Carve-out: the
     freeze⊃cold-snap subsumption of 2026-11-16 runs BEFORE curation on
     specificity grounds and ignores this floor — a cold snap grazing a freeze
     is still subsumed there even though curation would publish the pair.
     The knob is validated at import: outside [0, 1] (or non-numeric) the
     module refuses to load rather than silently disabling eviction.
     **Continuations (curation, owner 2026-11-21):** a heat, freeze or cold-snap fragment
     passed over by selection is still published, as its own event outside the per-type
     quota, when it starts within `MERGE_GAP_DAYS` of a selected same-type event's end,
     has bbox IoU >= `MERGE_MIN_IOU` with it, and conflicts with nothing kept
     (2026-11-23: the same predicate as conflict resolution — a same-type
     overlap or a material cross-type overlap blocks the hop, a sub-floor
     graze does not; blocked hops are logged as
     `curation.json` `continuation-skipped`). One hop only;
     `details.continues` names the parent.
     **Empty truth points:** the ERA5 mirror records a point ARCO serves as entirely NaN
     as `unavailable`, never complete, and retries it every run. The map lane lists such
     frames in `index.json` `no_truth`, the map labels them, and when one recovers that
     frame and every later frame are rebuilt.
     Qualification is never inherited: an event's `qualified_date` is a
     day *its own* footprint reached the floor, so a candidate that never reached it
     is absorbed like any younger member and leaves no event (round-3 review fix,
     2026-11-21 — the old oldest-survivor-with-inheritance rule published valid
     events whose peak footprint was below the qualify floor). The **tracker box** is still the bbox of everything its footprint has
     covered, growing and never shrinking: it is the follow/claim/seeding geometry and
     the anchor's search area, not the published rectangle.
  5. **Published box (what the dashboard draws and the scorer scores).** The Event
     `bbox` is the **persistence box**: the bbox of the cells valid on at least
     `publish_min_days` = max(2, ⌈0.5 × duration⌉) of the event's days
     (`publish_min_fraction`=0.5), trimmed to the area-weighted 2–98 % latitude/longitude
     quantiles of that mask (`publish_trim`=0.02; fallback: the bbox of every valid cell
     the event ever held). The tracker box is reported in `details.tracker_box_area_km2`
     next to `published_box_area_km2`, `fill` (the share of the published box's land that
     was a qualifying cell on some event day) and `daily_fill` (that share on a typical
     single day). The persistence box may hold **less** area than the footprint that
     qualified the event — it keeps only the cells valid on at least `publish_min_days`
     days, so a spell that qualified in one big push and then collapsed publishes a
     rectangle smaller than its 200 000 km² qualify floor (measured on the C5 replay of
     the live window: 15 of 215 published events hold less area in their persistence box
     than their largest qualifying-day footprint, and 1 of 215 publishes a box under the
     200 k km² floor); this is a publish-box property, not a tracking change, and is
     listed as an open owner question (`notes/footprint_tracker.md`).
  6. **End:** the **core** — the cells valid on *every* day since the event **qualified**, counted inside the **anchor box** — must hold >= **100 000 km²** (`end_area_km2`). Below that the event closes and that day is not part of it. The **anchor box** is the box as it stood on the later of day 3 (`anchor_day`) and the qualification day: it never freezes before qualification, so a spell that spends days growing into qualification anchors on the day it became an event. Because the core counts from the qualifying day, the qualifying day itself can never be the ending day: every valid event satisfies `start <= qualified < end` and its qualifying day is one of its recorded days. This is what ends permanent-belt spells physically (a belt that keeps re-claiming fresh ground still loses its core inside the frozen anchor box; longest in the qualification-clock replay of the live window: 28 days, 32 as shipped) without a duration cap; `end` is exclusive. A merge never hands qualification to a survivor that never reached the floor on its own footprint (see step 4), so a survivor's core clock and anchor always start on its own qualifying day — there is no merge-day clock.
  An event still open at the end of the scan window is `ongoing` and keeps its start date across runs. The published id hashes `(type, start, anchor bbox)` — the tracker box as it stood on the anchor day (day 3, or the qualification day when that is later), frozen for the event's life — so an ongoing event keeps its id across weekly runs while its start does, even as its persistence box is redrawn (2026-11-21, measured in `scripts/review/hb04_ongoing_ids.py` under the earlier `separate` rule and not re-measured for `largest_close`: the +7d weekly extend kept the id of **all 10** ongoing heat spells on the anchor basis — the published-box basis would have re-keyed 6 of them; under slid window starts (+7/+14 d) 8 of the 10 ongoing spells pair into the slid catalogue and **all 8 keep their anchor id** (published basis 3/8), the two non-pairing spells re-seed under new starts at the slid window head rather than re-keying; under `largest_close` an ongoing spell absorbed by a larger one is closed at the merge day; lanes with `anchor_day=None` hash the published bbox, and curation-merged ids in `select.py` keep hashing the merged bbox). `min_days`=3 is a detection floor (a shorter spell is not an event). Because 31-day caps are gone, the publish-gate ceiling is `validate.MAX_DURATION_DAYS = 45` (and `select.MAX_MERGED_DAYS`), above the longest real spell and below nonsense.
  **Mask source:** the daily masks are read from the published `raw_exceedance` frames (four steps ANDed; `ewb_live/events/frame_masks.py`) whenever the lane spans the window and the active clip is the lane's owb p85/p15, so detection cannot drift from what the map paints or from `qualified_at`; otherwise ERA5 ARCO is streamed through the upstream helpers. Daily-mean temperatures for `peak_value` come from the local ERA5 mirror on the same grid (`_mirror_t2m_daily`), per event when the mirror cannot serve the window.
  `qualified_at` scans frames from `details.qualified_date` onwards, so a grown box cannot qualify an event before its own footprint reached the floor.
  Per-event provenance is published in `details`: `qualified_date`, `duration_days`, `ongoing`, `footprint_area_km2`, `box_area_km2`, `core_area_km2` (`null` for days before `qualified_date` — the core counts from the qualifying day, so the ramp days are not a core in that sense; the series stays day-aligned with the footprint series), `anchor_box`, `mask_source`, `peak_area_km2`, `tracker_box_area_km2`, `published_box_area_km2`, `fill`, `daily_fill`, `tracker_params`, `detection`.

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
- **Event-density map layer (owner 2026-11-24):** an opt-in layer under the raw shading that paints,
  per 2.5° cell, how many events of the current type tab (All = every type) cover that cell over the
  whole shown window, counting event `bbox` footprints from `global_timeline.json` (region-filtered,
  dateline-aware, full-band boxes skipped). The top of the ramp is the busiest cell in the current
  view, so the scale rescales with the tab, the Region view and the window; a legend control turns
  the layer on and sets the minimum visible count (default 1, cells below it stay unpainted). It is a
  "where is the action" guide, not a measurement: a bounding box over-counts around its real
  footprint. State rides the URL (`#dens=1`, `#densmin=N`); the grid maths lives in
  `dashboard/density.js` (pure, tested in node by `tests/test_density_js.py`).
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

### 6. Results store — versioned Icechunk repo (`ewb_live/stores/results_icechunk.py`)
Replaces "dump `data/results/*.json` at the end of the run". Rollout is phased; this section is the
contract for all phases. **Phase 1 (this change) ships the store module + tests only: nothing in
the pipeline, server or dashboard reads or writes it yet.**

**Repo.** Its own repo (`EWB_RESULTS_REPO`, default `data/icechunk-results`, local filesystem), separate
from the MLWP *reference* store of §5 (that one is refs only; this one holds real data). Single writer:
the pipeline collector process commits; scoring workers never touch the repo; a flock refuses a second
writer.

**Layout.** `/events/<id>/` holds the event record + per-model status (attrs), `scores/<col>` (columns of
`SCORE_COLUMNS` minus the per-event constants), `blobs/<name>` (uint8 bytes, e.g. `viz.json`) and
`grids/<name>` (n-D arrays: masks, gridded data). Root attrs carry `schema`, `window` and `run`.

**One commit per event.** The collector commits each finished event in ONE transaction on branch
`run/<ISO-week>` (event record, score rows, blobs, grids, including documented-zero events with no
rows). Commit metadata references the weekly run: `week`, `run_id`, `rule_rev` (`TRACK_RULE_REV`),
`code_sha`, `event_id`. Rescoring overwrites the event's subtree atomically; readers never see a
half-written event. `begin_run` is idempotent, so a resumed run keeps what it already committed.

**Live vs published.** `main` = published. The run branch is the live view while the week is in
progress (the site may show "W48 in progress, n/N events"; rankings over a partial set are labelled
provisional). `publish` tags the run tip `<ISO-week>`, fast-forwards `main` to it and retires the run
branch (the tag keeps the snapshot). History snapshots (`data/results/history/<YYYY-WW>/`) become
those tags. One failed event is a status row, not a reason to withhold the other events.

**Retention — the store holds the rolling window, not history.**
1. *Logical:* every run `prune_window` drops events whose `end` is before the window start (a normal
   commit; events that cannot be dated are kept).
2. *Physical:* `maintenance` (dry run unless `confirm=True`) drops weekly tags beyond the newest
   `EWB_RESULTS_KEEP_TAG_WEEKS` (default 12), retires stale unpublished run branches older than the
   oldest kept week, expires snapshots older than `EWB_RESULTS_SNAPSHOT_KEEP_DAYS` (default 14) and
   garbage-collects what nothing references. Branch tips and kept tags are never expired. Size is
   therefore ~ the 90-day window plus retained tags' deltas; `tests/test_results_icechunk.py` rolls
   30 weeks and asserts the repo stays flat once the window is full.
3. *Owner rule:* physical deletion stays opt-in. The weekly may only call `maintenance(confirm=True)`
   once the owner has approved it; until then it runs the dry run and logs the report.

**Cache bounding (phase 2 deliverable).** The big caches must be bounded by the same window, never
significantly larger week over week: `data/cache/subsets` already evicts by init time
(`sources/cache.py`, 120 d); `fc_parts` and the `truth` ERA5 mirror have no eviction today and get a
window-keyed policy (window start minus a margin) with a dry-run report first. Deletion needs the
same owner approval as above.

**Phase 2 (dual-write) — behaviour.** `pipeline._write_outputs` (already called after every job) calls
`results_sink.ResultsSink.sync`, which commits every event whose content hash (event record + rows +
public pair status) changed, to branch `run/<week>`; selection churn is dropped with one `retain_only`
commit. After the viz/global steps `weekly` attaches `events/<id>/viz.json` and the run docs
(`events_selected`, `curation`, `events`, `global_timeline`, `meta`, `scorecard`, `scorecard_by_region`,
`pair_status`, `tc_wind`), prunes to the window, publishes (tag + `main`) and logs
`tools/results_parity` (store vs files: ids, score rows, docs, viz bytes). Everything is best-effort: a
store failure logs a warning (three in a row disable the sink for the run) and never fails the run or
changes a file. `EWB_LIVE_RESULTS_STORE=0` turns it off; run identity comes from
`EWB_LIVE_RUN_WEEK` / `EWB_LIVE_RUN_ID` / `EWB_LIVE_RUN_WINDOW_END` (weekly pins them so a run that
crosses midnight stays one run). `maintenance` runs as a DRY RUN every publish and is only applied
when the owner sets `EWB_RESULTS_MAINTENANCE_CONFIRM=1`.

**Cache bounding — measured 2026-11-24** (`python -m ewb_live.tools.cache_retention`, report only):
`subsets` 118 GB and `fc_parts` 59 GB are keyed by init time and the caches are only ~4 months old, so
nothing is evictable yet (window + 30 d margin = 120 d); `subsets` evicts itself, `fc_parts` does not and
needs the same init-time eviction before it passes 120 d (about 15 GB/month of growth otherwise). The ERA5
mirror (`truth/era5.zarr`, 51 GB for 116 days, ~3 GB/week) only grows forward and is the one unbounded
cache; trimming it means rewriting the time axis (rolling rebuild), designed in phase 3.

**Phases.** (1) store module + spec + tests [this change]; (2) dual-write: the collector commits each
event to the store while the existing files are still written, parity-checked; cache retention report;
(3) `serve.py` materialises the unchanged `/data/results/*` URL contract from the store, live banner,
backfill W45–W47 from `history/` as tags; (4) retire the end-of-run dump paths.
