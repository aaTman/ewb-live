# Knowledge transfer — EWB Live dashboard & viz (design role)

> 2026-11-08 evening: the two home-page maps are now ONE merged map
> (`#global-sec`, `dashboard/mergedmap.js` — globalmap.js/rawmap.js retire).
> Spec: `notes/design.md` "Merged global map"; session log:
> `notes/handoff_design_final.md` (end of file); QA: channel 18:55.

For a fresh expert hardening this site for public launch. Written by design
(successor 4), 2026-11-07 ~21:30 UTC. Ownership split (2026-11-07): **frontend**
owns `dashboard/*.js` implementation; **design** owns visual direction (tokens,
layout specs, copy, QA sign-off) and `ewb_live/viz/` data assets. Pipeline/
scoring is `ewb`'s lane. Never `git commit` here — the orchestrator commits
(SPEC.md).

## 1. Architecture & file map

Static-site architecture (Neil Gordon / Weather Explorer lesson: all per-viewer
work in the browser, server is dumb + immutable-ish JSON):

- `dashboard/` — no build step, vendored deps:
  - `index.html` (140 lines) — shell: header + kv-line, controls row, scorecard
    section, catalog (map + table), event-detail view, About `<dialog>`, footer.
  - `app.js` (1712 lines) — state, data loading, scorecard, catalog, routing,
    charts, region gazetteer (ray-cast PIP over `geo/countries.json`).
  - `mergedmap.js` — THE merged global map (briefs/merged_global_map.md):
    raw 6-hourly canvas + SVG event boxes/Gantt on one Equal Earth fit;
    consumes `global_timeline.json` (§2). `globalmap.js`/`rawmap.js` are the
    retired predecessors (removed from index.html; delete with frontend's OK).
    design spec `notes/design.md` "Merged global map".
  - `eventviz.js` (1162 lines) — event-detail map renderer (Leaflet overlays,
    canvas field drawing, u8/bit unpack, lead/day selectors, overlap classes).
  - `styles.css` (554 lines) — design system, tokens §5.
  - `geo/countries.json` (~159 KB) — Natural Earth 110m polygons (public
    domain), lazy-loaded, country/continent chips from bbox centroid.
  - `vendor/plotly.min.js` (4.56 MB, lazy), `vendor/leaflet/` (CSS `invert`
    filter removed in DR2; Esri basemaps now).
- `ewb_live/serve.py` (239 lines) — stdlib static server. Routes `/` →
  `dashboard/`, `/data/results/...` → results tree with sample fallback,
  `/data/results/history/<week>/...`, `/data/results/<mini-dir>/...`,
  nested `events/**/viz.json`; gzip on JSON/JS/CSS (mtime-keyed cache);
  `no-cache` on results + own assets; port 8050.
- `ewb_live/viz/` — asset builder (design-owned): `build.py` (579 lines,
  schema **3**), `timeline.py` (global dashboard asset, see §2),
  `exceedance.py` (raw 6-hourly map frames, see §2; module renamed from
  `raw_exceedance.py` 2026-11-08 — output dir unchanged),
  `__main__.py` (CLI `python -m ewb_live.viz --all
  [--events ids] [--leads 24,72,120,240] [--ar-leads 24,120] [--force]`;
  **always rebuilds `global_timeline.json` + `raw_exceedance/` as non-fatal
  tail steps**), `__init__.py`.
- `scripts/build_mini_results.py` (253 lines) — frozen review snapshots
  (`data/results/mini/`, `mini-sparse/`, `mini-nofreeze/`, `mini-am/`):
  copies live meta/events_selected/scores, fills every gap with deterministic
  synthetic cells (`synthetic=true` rows, `meta.mini=true`,
  full_run_complete stripped), rebuilds scorecard with the same reducer as
  `pipeline.build_scorecard`. Pair statuses from `pair_status.json` respected
  (no synthetic fill for non-scored pairs).
- `tests/` — pytest; offline suite 144 passed (2026-11-08), network marked
  tests auto-skip off the `ewb` box (`-m "not network"`).
- Ops wrapper: `ops/remote.sh` (see §10). All execution happens on the EWB LXC
  `ewb` (192.168.0.121), never on the orca dev container (hard rule).
- Notes: `notes/design.md` (decision log), `notes/review_round1.md` (review
  doc), `notes/design_review.md` (round-2 review verdicts), `notes/viz_v3_spec.md`
  (current overlay redesign spec), `notes/channel_*.md` (team, append-only),
  `notes/orchestrator_watch.md` (agent roster).

## 2. Data contracts the UI consumes

Loader: `app.js loadJSON()` fetches `data/results/<name>` and falls back to
`data/results/sample/<name>` per file (404→sample); `?data=<dir>` switches to
a frozen dir (no sample fallback, amber header chip). `?week=YYYY-WW` loads
`data/results/history/<week>/<name>` (no fallback). Six files load at boot:

| file | fields the UI reads |
|---|---|
| `meta.json` | `last_updated`, `models[]` (roster), `window{start,end,days}`, `truth_source{primary,fallback,note}` (+`arco_cutoff` for the per-event truth chip), `metrics{id:{event_types[]}}`, `baseline`, `full_run_complete` (clears partial badge; only path since aigfs can never score), `cycle/week/next_update`, `selection_thresholds`, `event_counts`, `mini`, `mini_note` |
| `events_selected.json` (fallback `events.json`) | Event: `id` (12-hex), `event_type` ∈ heat_wave/freeze/atmospheric_river, `title`, `start`, `end` (**EXCLUSIVE** — `eventLastDay()` renders end−1d), `bbox[lat_min,lat_max,lon_min,lon_max]` (lon span may wrap ±180 — split rects + continuous fit exist), `peak_value`, `details{quantile, clim_source, merged_from, merged_count, ocean_frac_start, landfall_time, …}` |
| `scores.json` | rows `{event_id,event_type,model,metric,lead_time_hours,value,init_time,synthetic?,truth_source}` — the per-event charts + detail mini table |
| `scorecard.json` | rows `{event_type,metric,lead_time_hours(model),mean_value,skill_vs_ifs_pct\|relative_skill_vs_ifs_pct,n_events}` — pre-aggregated by pipeline (`pipeline.build_scorecard`); `lead_time_hours:null` = event-level metric (renders single "event" column) |
| `pair_status.json` | keys `"<event_id>\|<model>"` → `{status, reason}` — drives n/a rows ("Not scored — N models: <reason>"), synthetic-fill skip. Reason vocabulary is data-driven from ewb; no event ids hardcoded in JS |
| `events/index.json` + `events/<id>/viz.json` | per-event visual assets, fetched lazily on event open (404/missing → "visuals pending", §3) |

`weeks.json` + `history/<YYYY-WW>/` (weekly snapshots, week picker);
`mini-nofreeze`/`mini-am` variant snapshots (frozen, review-doc links).

**`global_timeline.json`** (schema 2, built by `ewb_live/viz/timeline.py`
from events_selected.json + the per-event viz.json; ~31 KB gzipped-friendly;
`/data/results/` flat route serves it). Contract (**schema 2**, merged-map
era): `{schema:2, generated, window{start,end,days}, mask_bits, raw:{grid,
cutoff,first,last} (mirrored raw_exceedance/index.json header; step t has a
raw frame iff first<=t<=cutoff), events:[{id, type,title,start,end,bbox,
qualified_at:"YYYYMMDDHH" (ewb's detection step, q_source:"detection" —
live since 2026-11-08 18:40 for all events; "start_fallback" = start
truncated, only if the field is absent again), days:[ISO dates — active days,
end-1d], grid{lat0,lat1,lon0,lon1,step_deg,step_lon,ny,nx,continuous_lon},
masks:[b64 × len(days)]}]}`. Masks are ~1° OR-cells of the event's viz mask,
packed EXACTLY like viz-3 `daily_bits` (day-major, row-major, MSB-first,
stride=ceil(ny*nx/8)) — reuse the eventviz.js decoder; row order follows the
viz grid's ASCENDING `lat` array. `continuous_lon:true` → lon1>180, draw on
the continuous eastward lattice. Missing `masks` = bbox-only ghost entry.
Heat/freeze masks are TRUE per-day exceedance; AR masks are the anchor
(peak) mask repeated each active day (matches the AR detail panel).
`python -m ewb_live.viz.timeline` regenerates standalone; the `--all` pass
and weekly step 6 do it automatically (ids can never drift). Unit test:
`tests/test_global_timeline.py`. Design spec for the consumer section:
`notes/design.md` "Global event dashboard".

**`raw_exceedance/`** (raw detector output map, brief
`notes/briefs/raw_exceedance_map.md`; built by `ewb_live/viz/exceedance.py`
from the ERA5 mirror — heat `2t > p85` / freeze `2t < p15` OWB clim at the
6-hourly anchor (upstream frame-level operator, land-only, minus only the
daily min-over-4-steps pass) + EWB's own AR mask builder on mirror u/v/q
(levels match the AR set exactly). Frames `data/results/raw_exceedance/
<YYYYMMDDHH>.bin` = zlib(packbits heat ++ freeze ++ ar), planes
129,780 B each over 721×1440 in DISPLAY orientation: row 0 = lat −90
(N↑), col 0 = lon −180 (builder flipud+roll from mirror-native inside the
builder — JS reads it straight); `index.json` = {schema, generated,
grid{res_deg,ny,nx,lat0,lon0,step_lat,step_lon}, frames:["YYYYMMDDHH"…]
asc, cutoff:"YYYYMMDDHH" (mirror end — frames after it do not exist;
pipeline-consistent, label it in the UI), window{start,end}}. ~22 KB/frame;
full 90-day set ≈ 7–8 MB. Incremental (existing frames skip); CLI
`python -m ewb_live.viz.exceedance [--days N] [--force]`; weekly tail
keeps it current. First full build = systemd-run unit `--property=
MemoryMax=12G` (~1 h). Reuses ewb's functions by import — if the mirror
layout or the AR builder convention changes, ewb pings design (note in
orchestrator_watch.md). Frontend spec: `notes/design.md` "Raw exceedance
map" (updated to display orientation 2026-11-08). Tests:
`tests/test_raw_exceedance.py` (codec + incremental logic + serve routes).

**viz.json schema 3** (full contract + JS unpack snippets in
`notes/viz_v3_spec.md`): common `{schema:3, kind, event_type, truth_source,
grid{lat[],lon[],cell_deg}, window{start,end,days[]}, quantile,
threshold_note}`; `kind:"consecutive_days"` adds
`obs{max_consec, anchor_day, footprint_cells, consec:u8{b64,shape,lo,hi},
daily_bits:{b64,ndays,ny,nx}, daily:"0/1"-strings}` and
`models{model:{lead_h: {status, max_consec, footprint_cells, consec,
daily_bits}}}`; `kind:"ar_ivt"` carries IVT u8 fields + packed masks per
obs/model anchor. Encodings: u8 quantise (0..254→lo..hi, 255=missing) and
bit-pack (day-major, row-major, MSB-first, day stride `ceil(ny*nx/8)`),
unit-tested in `tests/test_viz_unit.py`. `daily_bits[day][cell]=1` ⇔ the cell
cleared the threshold at **all four 6-hourly steps** of that valid day (the
detection rule), land-only. AR mask overlap (hit/miss/false-alarm) is computed
client-side from obs+model masks.

## 3. State & routing model (app.js)

Single `state` object (~line 62): `meta, events, scores, scorecard,
pairStatus, tab, models:Set, leads:Set, allModels/allLeads,
baseline:'ifs_hres', week, weeks, chartMode, themePref, selectedEvent,
modelOrder, search, leadsMore, scMore, detailLead, detailModels, …`.

- URL: `?data=mini|mini-sparse|…` (data source override, `DATA_DIR`, evaluated
  once at boot), `?week=` (history snapshot; `replaceState` on change),
  `#event=<id>&lead=<h>` (deep link), `#date=YYYY-MM-DD&global=id,id`
  (global dashboard day + multi-select; verified open→Back restores).
- Opening an event **pushes** a history entry (`pushState`); `popstate` +
  `hashchange` are the single sync path (`push:false` on sync passes) — Back
  returns to the list without reload (the owner's "can't back out" complaint
  came from the pre-pushState replace-only model; frontend re-landed it —
  verify in QA).
- Controls state (tab/models/leads/theme) persists in
  `localStorage['ewb-live-state']`.
- Boot: parallel `loadJSON` of the six files → `renderAll()`; `window.__debug.state`
  read-only getter for QA probes.

## 4. Rendering pipeline

- **Scorecard** (`renderScorecard`): `scIndex()` builds
  `(etype|metric|lead) → {model:{mean,rel,n}}`; `relSkill()` re-derives
  relative skill client-side when absent, with `dir:'zero'` metrics scored by
  distance to zero. ONE global model order across tables (mean rel over visible
  cells; baseline row pinned first). Compact hero = headline metric per type at
  `DEFAULT_LEAD_BINS=[24,72,120,168,240]` behind `more leads`/`more metrics`
  disclosures (redesign pending — spec §5 of viz_v3_spec.md). Partial-results
  badge logic §2 (`full_run_complete`). Cells: raw value text + background by
  % vs baseline (`--better/--worse` rgb, diverging, printed ticks under legend).
- **Charts**: Plotly, **lazy on first fold-open** (`ensurePlotly()`;
  `skillDirty` defers while collapsed). Per-metric skill-vs-lead lines.
- **Maps** (catalog + eventviz): Leaflet + Esri World (Dark|Light) Gray tiles
  (theme-matched; attribution "Powered by Esri | Sources: …" enforced by TOU),
  OSM fallback. Event bboxes as rects (click opens detail;
  `keyboard:false`), antimeridian bboxes split. Fields drawn as canvas overlays
  (u8 grids; `drawField` ramp painting; heat/freeze Reds/Blues, IVT = paper-matched sequential Blues ramp,
  domain 250–1000 kg m⁻¹ s⁻¹, transparent below 250, 400 AR tick marked
  (arXiv 2605.01126 figures are single-hue blue, NOT magma — pixel-classified). **Currently smooth-interpolated overlays — the p85/p15 redesign
  switches heat/freeze to crisp 0.25° cell rendering** (§5 of the spec).
  fitBounds to the **occupied extent** (not raw bbox), 15% pad, minZoom 4;
  synced pan/zoom across obs+model panels; fullscreen per panel; QA handle
  `window.__ev3` (set after double rAF — foreground the tab before probing).
- **Viz assets** (`ewb_live/viz/build.py`): truth from the ERA5 mirror
  (`data/cache/truth/era5.zarr`) until the ARCO cutoff, then IFS-HRES 0-h
  analysis (surface-only ⇒ AR truth blank after cutoff); climatology = OWB
  pooled ERA5 1990-2019 hourly via `extremeweatherbench.defaults.get_climatology`
  fallback, cached as npz per (quantile, bbox, doy-window) under
  `data/cache/viz/`; grids 0.25° (heat/freeze) / 0.5° (AR); land-only
  (`regionmask_land_110`); model fields through `sources.open_forecast_nearest_init`
  (GFS models publish 12Z → init snap with lead offset); horizon shortfall →
  `{status:"beyond horizon (need 246 h, max 240 h)"}`; per-event failures are
  caught, recorded in `index.json`, batch continues; `FETCH_BUDGET_S=600` caps
  models per event ("skipped (budget)"). Writes atomic; skip-if-schema-match
  incremental; `--force` rebuilds. Weekly cycle calls it as step 6 (non-fatal).

## 5. Design system (styles.css tokens)

OWB (owb.brightband.com) clone — 14 production tokens verified. Light:
`--canvas #fdfdfc`, `--paper #fff`, `--alabaster #dfdecb`, `--tint #f2f1e6`,
`--border #d9d9cd`, `--matte-black #262620`, `--ink-secondary/--ink-muted
#6e6e60` (AA-fixed, DR2 M4). Brand `--accent #ff6000` is **graphic-only**
(never text, never a focus ring); text uses `--accent-link #b34500`, focus
`--accent-focus #cc4e00`. Dark theme block overrides (canvas #1e1e1a, paper
#262626, muted #a3a394). Type: Inter + IBM Plex Mono self-hosted (OFL, 104 KB
subset). Ladder: 10.5/11.5/12.5/14/20/26px (uppercase eyebrows +0.08em).
Color semantics: blue `rgb(26,79,150)` = **better than baseline**, red
`rgb(198,73,60)` = worse (sign-normalized per metric direction); type series
heat `#c6493c`, freeze `#1a4f96`, AR green light `#2e7d32`/dark `#66bb6a` (2026-11-08 owner: AR is green everywhere; AA in both themes); overlap hit/miss/FA
`--ev-hit/--ev-miss/--ev-fa`. The p85/p15 overlay reuses the type inks
(one color above threshold, alpha .85, everything else transparent).
Components: quiet pills/chips (44px touch), lane-empty state (italic
one-liner + ⓘ criteria tooltip, legitimate outcome), reason vocabulary
(honest absence strings, never exception class names), ghost rows for
data-less roster models (`MODELS n scored · m in roster`).

## 6. Owner decisions (chronological, with sources)

1. Brightband OWB scorecard conventions + EWB-paper per-event figures as the
   presentation bar (SPEC.md); Windborne "patch of weather" framing.
2. Reuse EWB defaults; thresholds now **heat P85 / freeze P15** of the OWB ERA5
   1990-2019 climatology (2026-11-07, SPEC.md "Temperature thresholds";
   supersedes the p95/p05 interim). AR must start over ocean, end on land.
   Zero events in a lane is legitimate → calm empty state. No two events
   overlap in time+space; same-event-only merge + duration cap; ≤15/type;
   unbiased selection (Greenland freeze kept, no population weighting).
3. Simplification plan (notes/design.md): sparse OWB home, scorecard hero,
   ~70% fewer words, About drawer holds methodology — executed.
4. Esri basemaps approved (internal non-commercial; TOU notes in
   notes/design.md; buy ArcGIS key before public launch).
5. Mini snapshots (`?data=mini`) with synthetic score cells for layout review
   (amber chip; viz/events/story real).
6. **Event maps must look like `notes/owner_refs/2026-11-07_heatwave_reference.png`**
   — final ruling: ONE exceedance class (P85 / P15), single color, crisp cells,
   white `Heatwave #<id>` chip, bottom bar valid-time + single-swatch legend,
   day stepping, same component obs vs model-at-lead (spec:
   notes/viz_v3_spec.md). No extra quantiles will ever be computed.
7. Round-1 review doc flow: `notes/review_round1.md` first line `REVIEW READY`
   only while current; owner previews at :8050 live + `?data=mini`.

## 7. Known bugs, tech debt, fragile areas

- **Live frontend bugs (owner refs, frontend is fixing)**: duplicate
  "more metrics" per tab click (multiplying buttons); opaque dark square
  behind AR IVT panels (no-data background painted); detail map zoomed to
  world view on some paths; strikethrough model chips are noise (use ghost
  rows + reason instead). `notes/owner_refs/2026-11-07_ar_detail_bugs.png`.
- **Data freshness while you read this**: pipeline unit stopped 17:47 UTC
  2026-11-07 awaiting the p85/p15 re-selection; `scores.json` is the Nov 6
  18:52 merge (heat-heavy partial), `events_selected.json` is the p95/p05
  27-event run `20261107T040432Z`; `full_run_complete` unset. Review set +
  headline numbers + mini rebuild are gated on ewb's next posts (§ design
  channel 21:25).
- `pair_status.json` entries for `4a9c67b01f25` (and any new exclusions) must
  come from ewb or n/a rows show the generic "no rows yet" line.
- viz fixtures built pre-p85 (quantile 0.95 in `details`) — shapes identical,
  thresholds change on rebuild. AR assets unchanged by schema 3 but the
  schema bump makes the next `--all` rewrite everything (~2–4 h cold).
- AR truth absent after the ARCO cutoff (surface-only analysis) — `obs:null`
  handling is load-bearing in eventviz.js.
- Excluded-but-listed events (`a3bb50ccde3d` etc.) render with zero rows —
  never let blank-rendering regress (fold `role=status`, row tooltips).
- `serve.py` gzip cache is in-process, unbounded-by-count (48→clear); fine for
  one box, not for a CDN-less public launch — put a real cache/CDN in front.
- Round-2 non-gating backlog + "make it incredible" agenda (dueling-map hero,
  5-second verdict strip — specified in notes/design_review.md, NOT yet built;
  verdict strip must read real scores post full_run_complete) in
  notes/design_review.md § DONE.
- Fragile: roving tabindex must normalize after row append; touch tooltip needs
  the 800 ms compat-mouseout guard; `__ev3` requires foreground tab;
  eventviz across-180 fit uses a continuous eastward lattice (156..196).

## 8. Test suite

- `cd <repo> && .venv/bin/python -m pytest -m "not network" -q` → **120
  passed** (2026-11-07). Viz contract tests: `tests/test_viz_unit.py`
  (pack_u8/pack_bits round-trips incl. the JS decode form, horizon degrade,
  antimeridian lattice/regrid). `tests/test_mini_snapshot.py` pins mini shape.
  serve/gzip tests in `tests/`. Network tests (28) run only on the box
  (`@pytest.mark.network`, auto-skip off-host via `ewb_live._guard`).
- E2E is frontend-owned (Playwright); design QA = browser probes against the
  live box (see §10 verify recipes).

## 9. Performance budget & measurements (DR2-verified)

- <500 KB to first paint target: live-mode boot ≈ 72.6 KB gzipped
  `scores.json` (1.22 MB raw, 22×) + scorecard + events + meta + viz deps;
  Plotly (4.56 MB) only after first Scores fold-open; countries.json lazy.
- viz.json 7–170 KB/event gzipped on the wire; mini `scores.json` is 6.3 MB
  raw — mini mode is a LAN review tool, don't public-launch with it.
- viz build cost: heat ~10 s–2 min warm-cache, AR ~10 min cold (box),
  `--all` cold ~2–4 h; climatology npz cache makes weekly runs minutes.
- Watch for: eager-Plotly regressions, boot-time fetches of events/**,
  un-gzipped routes.

## 10. Accessibility, deploy, verify

- a11y status: axe-core color-contrast **0 violations** both themes (DR2);
  roving tabindex + `aria-label` on score cells; event rows `tabindex=0
  role=link`; map rects out of tab order; focus-visible outlines use
  `--accent-focus`; 44px chip/pill hit areas; touch tooltips verified on true
  phone emulation. Re-run: `dr_measure.py`/`dr_walk.py` (scratch, re-creatable
  from design_review.md notes).
- **Deploy** (from the orca worktree; NEVER `git commit`):
  `./ops/remote.sh sync` (code-only; box owns data/ & logs/). Heavy entry
  points guard on hostname (`EWB_LIVE_ALLOW_LOCAL=1` override).
  Web: `ssh root@192.168.0.121 systemctl restart ewb-live-web` only when
  serve.py/paths changed (static edits serve immediately; own assets are
  no-cache — hard-reload QA browsers).
  Viz rebuild (design-owned, serial, memory-pact: yield to scoring):
  `ssh root@192.168.0.121 "systemd-run --unit=ewb-viz-<tag>
  --property=MemoryMax=12G --property=WorkingDirectory=/opt/ewb-live --
  /opt/ewb-live/.venv/bin/python -m ewb_live.viz --events <id> --force"`.
  Scoring/viz/weekly only ever on the box (`ops/remote.sh run|bg|logs|status|
  pull-results`).
- **Verify**: box app http://192.168.0.121:8050/ (+ `?data=mini`);
  `curl -s http://192.168.0.121:8050/data/results/meta.json | jq .full_run_complete`.
  The orca container has **no internet** (Esri tiles fail there — probe with
  `browser_exec`, which has a proxy, or expect tile 404s in local Playwright).
  Screenshots via capture_screenshot copy immediately (shared tmp path).
  `window.__debug.state` + `window.__ev3` are the QA hooks.
