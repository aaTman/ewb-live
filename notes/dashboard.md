# Dashboard agent log

## Reference research — Brightband Operational WeatherBench (owb.brightband.com)

Researched 2026-11-06 via web (owb.brightband.com home / scorecard / skill-vs-lead /
methodology / changelog pages; github.com/brightbandtech/ExtremeWeatherBench;
brightband.com news post on EWB metrics).

Layout & navigation
- Single-page app with a top nav: Home | Skill vs Lead | Scorecard | Time Series |
  Methodology | Changelog. Deep-linkable via query params (?start=...&end=...&region=...).
- Global controls sit at the top of every view: Region selector, **Baseline selector**
  (any model can be the baseline; default HRES), Date range. The whole page re-renders
  against the chosen baseline.

Home view
- Leaderboard ranked on one chosen variable+metric ("Ranked by Temperature 850 hPa RMSE"):
  rank, model code chip, full name + org, the score, and "won N of M cycles".
- A "position per forecast cycle" bump chart: one point per daily cycle, rank on y-axis,
  so you see streaks and lead changes; headline stat "Active win streak: N".
- Per-region champion cards. Copy is plain and confident, explains its own caveats
  (e.g. "the latest ranked cycle trails real time by ~7 days").

Scorecard view (the centrepiece — our §4.2 analogue)
- Rows = models (baseline row first, then others, climatology last). Columns are grouped:
  one group per variable+metric ("2m Temperature RMSE [K]"), each split into 6 lead-time
  sub-columns (day 1, 3, 5, 7, 10, 15). A trailing "Mean" column per group is implied.
- Sections split by domain ("Surface", "Upper air") with their own table.
- Cells show the RAW value; the cell BACKGROUND encodes % difference vs baseline on a
  diverging scale, with an explicit legend strip:
  `-50 -20 -10 -5 -2 -1  1  2  5  10  20  50` , "Better ← → Worse" (blue better / red worse).
  The scale is perceptually log-ish (equal width for 1↔2 and 1/2↔1/3).
- Sign convention handled per metric so "better" is always blue.

Skill vs Lead view (our §4.3 analogue)
- Line chart, x = lead time (days 0–15), y = metric, one line per model; interactive
  legend toggles models. Controls: Variable, Metric, Region, Baseline.
- Baseline selector "None (absolute)": choosing a model re-expresses every curve as
  percent improvement over it. Climatology is plotted as a reference but starts hidden
  in the legend because its error squashes all other curves.

Changelog conventions worth copying
- Every scoring-convention change is announced with a link to the methodology section it
  revises; model pauses/removals are stated plainly with the reason.

Interactions summary to adopt: baseline-relative everything; raw numbers with color =
skill; legend click to toggle series; shared date-range/window; small type + generous
whitespace; methodology and "what am I looking at" copy adjacent to the views.

## EWB metric conventions (from github.com/brightbandtech/ExtremeWeatherBench + Brightband post)

- Heat wave / freeze default metrics: `RootMeanSquaredError`, `MeanAbsoluteError`,
  `MaximumMeanAbsoluteError` / `MaximumLowestMeanAbsoluteError` on `surface_air_temperature`
  (derived TMAX/TMIN), plus EWB's event-level questions: onset lead time and duration error.
- Atmospheric river metrics: CSI (detection on landfall), spatial displacement of the IVT
  centre of mass, IVT RMSE, onset lead time ("how far in advance can a major AR be predicted").
- Brightband's own comparison post: AI models generally beat IFS HRES on these, and do
  relatively better on detection (CSI) than on location (displacement). I used that shape
  in the sample data.

## Metric ids chosen for this project (schema contract for agent `ewb`)

Defined in `data/results/sample/meta.json` under `metrics` so the pipeline can adopt the
same ids; the dashboard renders from that dict (unknown ids pass through with fallback labels).

- heat_wave: `rmse_k`, `mae_k`, `tmax_mae_k`, `onset_error_days`, `duration_error_days`
- freeze:   `rmse_k`, `mae_k`, `tmin_mae_k`, `onset_error_days`, `duration_error_days`
- atmospheric_river: `ivt_rmse_kg_m_s`, `csi`, `spatial_displacement_km`, `onset_lead_hours`
  (the latter two/three "higher is better": `csi`, `onset_lead_hours` — flagged
  `higher_is_better: true` in meta so the heatmap colours them correctly)

## Decisions

- Vendor Plotly 2.35.2 and Leaflet 1.9.4 into `dashboard/vendor/` (no CDN dependency, no
  build step, offline-safe; map TILES still need internet, everything else does not).
- Map uses Leaflet rectangles + circleMarkers only — no default marker PNG assets needed.
- Scorecard keeps ALL lead columns like OWB (leads 6..240 h grouped 1/3/5/7/10/14 days)
  but a lead-time chip selector filters which lead groups are shown (spec requirement).
- Relative-skill colouring is computed client-side from raw means (model vs ifs_hres at
  the same event_type×metric×lead), so it works even if the pipeline omits the
  `relative_skill_vs_ifs_pct` field; when the field IS present the dashboard uses it
  (single source of truth if the pipeline normalises sign differently).
- Fallback strategy (spec: "load data/results/*.json and fall back to sample/"):
  implemented TWICE — client-side per-file (fetch real, 404 → sample) AND server-side in
  serve.py (missing data/results/X → serve data/results/sample/X). Per-file fallback so a
  partially-populated real data dir still works.
- serve.py is stdlib-only (http.server). Serves repo root: `/` → dashboard/index.html,
  dashboard assets under `/dashboard/...` (also reachable directly), JSON under
  `/data/results/...`. Adds no-cache headers on JSON so daily pipeline updates show up on
  refresh. Guards against path traversal. `--host/--port` CLI args.
- Not creating `ewb_live/__init__.py` (package owned by another agent);
  `python -m ewb_live.serve` works without it via PEP 420 namespace packages when run
  from the repo root. Noted in Requests for the sources/pipeline agents to keep whatever
  they create compatible (if they add __init__.py that's fine too).

## Requests (to other agents)

- `ewb`: adopt the metric ids and `higher_is_better` flags in meta.json above; if you
  choose different ids, update `meta.json`'s `metrics` block (dashboard renders from it).
  `scores.json` may be either a bare list of rows or `{"rows": [...]}` — both parse.
  A row may omit `init_time`; the dashboard then falls back to event `start`.
- `ewb`/pipeline: write `data/results/meta.json` on every run with
  `last_updated` (UTC ISO), `window {start,end}`, `models`, `truth_source` — the header
  banner reads exactly those keys (missing keys degrade to '—').

## Decisions (continued, after first build + verification round)

- serve.py root design changed after the first browser test: serving the repo root made
  `/` resolve assets to `/app.js` (wrong). Web root is now `dashboard/` itself
  (`/` → index.html, assets at `/styles.css` etc.); `/data/results/<file>.json` is mapped
  onto the results dir inside `translate_path`, so nothing else on disk is reachable
  (`/SPEC.md`, `/notes/…`, traversal attempts → 404, verified).
- Real-data precedence + honest sample labelling: when a file under `/data/results/` is
  served via the sample fallback, the response carries `X-EWB-Data: sample`; the client
  shows the SAMPLE DATA badge if ANY of the four files came from sample (or
  `meta.is_sample`). Tested: real meta+scores, fallback events+scorecard → correct mix.
- Lead axis on charts is CATEGORICAL (6h,12h,1d,2d,3d,5d,7d,10d evenly spaced). A linear
  hours axis crowded 6h/12h/1d into the left 10% and their labels overlapped (measured:
  6h/12h boxes overlapped by 3px). Post-fix: min gap between adjacent label centres = 50px
  on every chart (measured across all 14).
- Lead chips filter the SCORECARD columns only; line charts always show the full
  lead curve (dropping points off a "skill vs lead" curve makes it unreadable).
- Chart legend: horizontal, inside the first chart of each event-type section
  (verified present in 3/3 sections). Model pills double as a global colour key.
- Map fit-bounds on all event bboxes at first render (default centre clipped
  Japan/SE-Australia boxes). Dark mode inverts tiles via CSS filter.
- Scorecard tables wider than the viewport get a persistent thin scrollbar + a right-edge
  mask fade while not scrolled to the end (`.scrollable`/`.at-end` classes).
- `python -m ewb_live.serve` works with or without `ewb_live/__init__.py`
  (sources agent added one meanwhile; no interaction).

## Verification record (2026-11-06)

- `node --check` on app.js, `py_compile` on serve.py + generator: clean.
- curl matrix on `python -m ewb_live.serve`: index/assets/JSON all 200; missing file → 404;
  sample fallback 200 with `X-EWB-Data: sample`; traversal/encoding attempts and
  everything outside dashboard/ + data/results/ → 404; JSON sent no-cache.
- Headless Chromium (Playwright, screenshots in notes/screenshots/ 01–07):
  3 scorecard tables / 756 cells / 630 skill-coloured (baseline cells neutral) /
  6 model pills / 8 lead chips / 14 skill charts / 20 map rectangles / 20 table rows;
  lead-chip filter changes cell count (756→672); model toggle works; event click opens
  detail with 5 per-metric charts; theme cycles auto→light→dark; SAMPLE badge shown.
  **JS console errors: none.** Tick-order/overlap measured per chart via DOM
  (all correct, min gap 50px); map fit-bounds shows all 6 sampled regions.
- Data sanity: graphcast +17.1% vs IFS (heat rmse @10d), fourcastnet −6.9% (AR CSI),
  onset-lead sign-normalised positive = better.

## DONE

What works (all verified against real code paths, no stubs):
1. `data/results/sample/` — 20 events (7 heat / 6 freeze / 7 AR) over 2026-08-08…11-06,
   4464 score rows, 672 aggregated scorecard rows, meta with metric dictionary +
   higher_is_better flags. Regenerable: `python3 dashboard/tools/generate_sample_data.py`
   (seeded, stdlib only).
2. `dashboard/` — static, no build step; Plotly 2.35.2 + Leaflet 1.9.4 vendored in
   `dashboard/vendor/` (offline-safe except OSM tiles). Sections per SPEC §4: header
   (last updated / window / truth / sample badge), Brightband-style scorecard heatmap
   (raw values, blue/red skill-vs-IFS background, Mean columns, custom tooltips, legend
   strip), skill-vs-lead small-multiples with absolute / vs-IFS-% modes, event map +
   filterable table, per-event detail charts. Controls: event-type tabs, model toggles,
   lead chips, chart mode toggle; dark/light/auto theme; state persisted in
   localStorage. Loads `data/results/*.json`, falls back per-file to `sample/`.
3. `ewb_live/serve.py` — stdlib-only, `python -m ewb_live.serve --port 8050`. Serves
   dashboard at `/`, results at `/data/results/` with sample fallback +
   `X-EWB-Data: sample` header, no-cache on JSON, path lockdown.

How to run:
    cd <repo root> && python -m ewb_live.serve --port 8050
    open http://localhost:8050/

Known gaps / notes:
- Map tiles need internet (everything else works offline); offline you get rectangles on
  a blank basemap.
- Scorecard with all 8 leads selected overflows horizontally on screens < ~1400px — by
  design (per-table scroll with fade hint), same as OWB.
- If the pipeline later writes files with the same names in `data/results/`, the
  dashboard picks them up on refresh with zero changes, provided meta.json carries the
  keys above (see Requests).
- Sample event set has 6 freezes (SPEC says "~20 events"; total is exactly 20).
- A test instance of the server is left running on port 8051 (log: logs/serve-test.log).

