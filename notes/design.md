# design — log, research, critique, proposal

Agent: design. Started pre-handoff; ops posted "HANDOFF: dashboard/" 2026-11-06 09:35 UTC
→ `dashboard/` is design-owned from then. Data contract per that message (per-file sample
fallback, 204-for-optional, LEAD_BINS everywhere, null-safe metrics).

## Reference research (what we borrow)

### Brightband OWB (owb.brightband.com) + WeatherBench 2 scorecards
- Scorecard = centrepiece: raw metric value in cells, background = % vs explicit baseline,
  baseline row pinned first, climatology pinned last; columns grouped per metric with
  6 lead sub-columns (1/3/5/7/10/15 d); diverging blue-better scale with printed ticks
  (`50 20 10 5 2 1 - 1 2 5 10 20 50`, Better ← → Worse). → Keep our value+rel-skill cell
  design; add tick labels under the legend; make the baseline row visibly the reference
  (we dim its text; also add "baseline" tag).
- Methodology page: every metric gets a one-paragraph definition WITH direction
  ("lower is better; perfect = 0"; sign conventions for bias), units, aggregation order,
  truth policy, model provenance, disclaimer. → We add compact per-metric explainer
  sentences (title attributes + a metric-info drawer) so a scientist trusts each column
  without leaving the page.
- OWB Home "answer first": ranked leaderboard, "won N of M cycles", win-streak, per-region
  champions, caveat copy in plain words. → Add a one-line "verdict" strip: best model per
  event-type by mean rel-skill over visible cells + #cells won.
- WB2 headline: skill-% cells vs a named baseline, operational vs ML model grouping,
  "not all aspects of a good forecast" honesty. → Keep; put models in scorecard order by
  skill but mark baseline; keep tooltips showing raw + rel + n_events (done).
- dynamical.org scorecard: metric-tab drilldown per variable, station-level pages,
  window chips (7/14/30/90 d). Good deep-linking idea; deferred (needs server data).

### EWB paper (arXiv 2605.01126) event figures — authoritative, per product owner
- Heat/freeze presentation figure (`plot_consecutive_map`, `figure2_*_consecutive.png`):
  **max consecutive event days per grid cell** over the case window; heat = Reds, freeze =
  Blues; cells with < MIN_CONSECUTIVE_DAYS (3) are blank; colorbar 3..max. The daily mask
  (upstream `temperature_events.py`): day counts only when **every 6-hourly step** passes
  the threshold (t2m > 85th pct / < 15th pct), land-only. Peak-day 2 m T map above/below
  climatology is the companion bottom row.
- Owner's direction (2026-11-06, supersedes my earlier guess): event visual = observed
  consecutive-day-count map over the event box + the same field for each model forecast
  at a chosen lead, so forecast vs observed duration and footprint compare directly.
  Lead-time selector + small per-model duration comparison. AR: IVT map + observed mask
  contour + forecast mask overlap (hits/misses/false alarms) with lead selector.
- AR figure (verified): rows = ERA5 + models × lead columns, IVT pcolormesh (binned
  0-1100 colormap) + thick black contour at the mask threshold; overlap of model vs ERA5
  mask is the CSI story; missing leads = "No forecast" placeholders.

### WindBorne Weather Events post (windbornesystems.com/blog/introducing-windborne-weather-events, 2026-10-29)
- Presentation: event = "a patch of unusual weather you can draw a boundary around and
  follow through time". One stable ID across runs/lead hours; per-day frame animation of
  the footprint; severity shown as percentile TIERS (P85/P98), not raw numbers; plain
  narrative copy ("spreads across the West by Oct 5, splits by Oct 10, only S CA remains
  by Oct 11"); events are climatology-relative, not absolute thresholds.
- Borrow: (1) stable event IDs with deep links (`?event=<id>` — already half-done);
  (2) a **day-scrubber evolution strip** for the consecutive-days map (their frame-per-day
  idea; our consecutive field + per-day masks support it); (3) tier language in the
  summary line; (4) an auto-generated one-sentence event story (dates, peak, footprint
  trend) from event fields; (5) summary chips row (type, dates, days, peak, bbox,
  truth) — keep ours, add "region" (centroid name from bbox → coarse region table).

## Critique of current dashboard (with live-data evidence, now.png + code read)
Priorities for implementation after the viz module. Scorecard base design is sound —
most items are small, high-trust fixes.

P0 (correctness/trust):
1. **Signed-error cells are coloured with the wrong direction**: pipeline
   `skill_vs_ifs_pct` for DurationMeanError is signed; −26.4 h vs baseline −32.0 h shows
   red as if worse, yet |−26.4| is closer to ideal 0. Fixed client-side: `dir:'zero'`
   metric class, skill = (|ref|−|model|)/|ref| (done in app.js this session).
2. Event detail charts drop event-level metrics silently (note exists) — the new per-event
   duration bar (below) makes DurationMeanError visible per model instead.
3. Truth pill: "era5 arco → fallback: ifs hres analysis" is cryptic; rephrase "ERA5 ·
   last 6 days: IFS analysis" + per-event "truth" chip (event end > cutoff → fallback).

P1 (hierarchy/readability):
4. No answer-first summary → add verdict strip (per visible type: leader by mean rel-skill
   over visible cells, "won k of n metric×lead cells"; recompute with filters).
5. Legend has no numeric scale → ticks "−50 … +50" under the strip, like OWB.
6. Model order differs per table (sorted per-type) → one global order by mean rel-skill
   over ALL visible cells so rows line up between heat/freeze tables.
7. Lead row is long (13 bins); default 1/3/5/7/10d already good; "all" chip exists. Keep.
8. Charts section: metric cards are absolute or rel — keep; add zeroline label "0 = IFS"
   in rel mode (done via y title), and hide cards with <2 models? no — keep.

P2 (mobile/a11y/perf):
9. Mobile 400px: header wraps to ~5 lines before data; controls wrap to 4 rows; scorecard
   tables scroll (OK); map+table stack (OK). Fix: compact meta strip on <620px (hide
   'Window' item, shrink chips), controls sticky-collapse under an "n filters" summary.
10. Contrast: cell text on strong red/blue fills is borderline; already switch fg on
    strong cells (done pre-handoff). Focus outlines exist for buttons; pills are spans —
    convert model pills to real <button>s.
11. Perf: map + 1500-rect init at once; cap at selected types (events.json fallback only).
    Current 45 selected events is fine; keep EVENT_TABLE_MAX_ROWS pattern for viz too:
    lazy-build viz assets (only open event → fetch its viz.json; already the plan).

## Event detail page design (top priority implementation)

Layout (replaces current 2-line detail-sec; keeps chips + per-metric charts below):

```
[← close]   Event title                                    #freeze_2026-09-29_52.5N-2.5E
[type chip][dates · 7 days][region: N Europe 52.5N,2.5W][peak −13.4°C @ core][truth: ERA5 mirror]
[story line: "Cold spell over the Netherlands; core below the 15th percentile for up to
 9 consecutive days; scored for 11 models against IFS HRES."]

Duration map block
  [lead selector: obs | 6h 12h … 240h]        observed max-consecutive-days  |  model @ lead
  [Plotly map bbox, 0.5° cells, Reds/Blues 3..max, blank<3] ×2 (obs | model), shared scale
  [model picker reuses global model set; "No forecast" placeholder]

Per-model duration comparison (bar): observed max consec (reference line) vs each model
  @ selected lead + DurationMeanError (signed, hours) chips; click lead chip to change.

AR variant: IVT magnitude map (binned 0→1100, 100/200 colormap) + observed mask contour;
  model panels at lead L: overlap class map hit/miss/false-alarm (green/red/orange) +
  observed contour; CSI% + area-match% readouts.

Per-metric mini scorecard (from scores.json rows for this event): compact table
  model × metric (mean over leads) with same cell colours + raw value, so the event's
  scores are legible without the main scorecard; per-metric ⓘ direction sentence.
```

Viz data: `data/results/events/<id>/viz.json` (+ `index.json`) from `ewb_live/viz/`
(design-owned). Dashboard fetches lazily on event open; 404/204/missing → graceful
"visuals pending" note; charts still work from scores.json. All JSON ≤ ~400 KB/event
(0.5° grid, ≤240-cell boxes, ≤4 model leads stored; quantized ints).

## ewb_live/viz module (owner-approved scope)

CLI `python -m ewb_live.viz --all [--limit N] [--leads 24,72,120,240] [--force]`.
- Truth: `data/cache/truth/era5.zarr` via `truth_mirror.open_mirror()`; post-ARCO-cutoff
  events use `hres_analysis_*surface_air_temperature.zarr` (surface → AR truth blank);
  6-hourly mask rule (every step passes) + land-only, exactly like upstream detection.
- Thresholds: `extremeweatherbench.defaults.get_climatology(0.85|0.15)` subset to bbox +
  event doy window (per-step doy×hour grid, climatology is hourly), cached as npz under
  `data/cache/viz/`.
- **Schema v1 (final, matches dashboard/eventviz.js):**
  - common: `{schema:1, kind, event_type, truth_source, grid:{lat[],lon[],cell_deg}, window:{start,end,days[]}}`
  - `kind:"consecutive_days"` (heat/freeze): `quantile`; `obs:{max_consec, anchor_day,
    footprint_cells, consec:[[int|null]] (obs max run-length grid), daily:["0/1 rows joined by /"]}`;
    `models:{model: {lead_h: {status:"ok", max_consec, footprint_cells (on obs anchor day),
    consec:[[...]]} | {status:"unavailable (...)"}}}`; skipped batch → `{status:"skipped (budget)"}`.
    Daily mask rule = all four 6-hourly steps pass vs per-(doy,hour) threshold, land-only
    (`regionmask_land_110`), exactly like upstream detection. Model day-k steps come from
    init `D00Z − lead` at leads `lead + 24k + {0,6,12,18}` via `sources.open_forecast`.
  - kind `ar_ivt`: `ivt_threshold_kg_m_s`; `obs:{anchor_time, area_cells, ivt:[[num|null]],
    mask:packed}` at the max-mask-area 6-h step in the window (null whole `obs` when the
    window is past the ARCO cutoff — surface-only analysis can't do IVT); `models:{model:
    {lead_h:{status, area_cells, ivt, mask}}}` — IVT+mask from the model's own q/u/v
    (`levels≥300 hPa`, EWB `build_atmospheric_river_mask_and_land_intersection`), valid at
    the obs anchor time. Overlap classes (hit/miss/false alarm) are computed **client-side**
    from the two mask grids.
  - grid: 0.5° (heat/freeze) / 1.0° (AR), nulls = ocean/no-data; sizes ~7–90 KB/event.
- Incremental: skips events whose viz.json exists (schema+generated version match);
  writes atomically; index.json = id → {kind, ok, size_kb, truth_source, generated}.
- Runs after scoring in weekly.py (request sent to ewb2 in notes/channel_design_ewb.md).

## Channel requests
- ops (channel_ops_design.md): serve nested `data/results/events/**` (same fallback rules);
  add `viz_index.json`-style optional-file 204s if needed. [sent 11:20]
- ewb2 (channel_design_ewb.md): call `python -m ewb_live.viz --all` from weekly.py after
  scoring step; keep events_selected.json stable for the week.

## Log
- 2026-11-06 ~11:0x: read channel; HANDOFF confirmed (09:35). Verified live app with
  Playwright (13 binned leads, 45 selected events, 0 console errors). Read current
  dashboard/ (rebuilt since session-start notes).
- Studied upstream EWB consecutive-day code + paper 6-panel figures; owner direction
  incorporated (supersedes masked-threshold-map idea).
- Windborne post fetched + distilled (above).
- 2026-11-06 11:20: posted serve.py request to ops.
- 2026-11-06 11:3x: built ewb_live/viz/ (see below), smoke-tested on 4 real events.

## DONE

- [ ] viz module: smoke + full-scale run — smoke DONE (heat/AR/freeze builders,
      10/45 events' viz.json on disk); full `--all` backfill must run on the EWB
      LXC via ops/remote.sh once notes/infra.md declares the wrapper ready
      (orchestrator rule 15:05 UTC: no EWB compute on orca). Horizon fix +
      regression tests tests/test_viz_unit.py (3 passed offline) in the tree.
- [x] dashboard: event page (consec maps, lead selector, duration bars, AR overlap,
      per-event mini scorecard, story line, deep links)
- [x] dashboard: verdict strip, legend ticks, global row order, pills→buttons, mobile pass
- [x] Playwright verify desktop 1500 + mobile 400, light/dark, zero console errors;
      screenshots notes/screenshots/design-*.png — verified against live partial
      scores + sample-data shadow server; re-run once backfill+scores complete.
- [x] channel messages posted (ops 11:20; ewb-3 relaunch ask + retraction under
      new compute rule, both nudged via term_0bafb4ff…)
- 12:1x: viz builder hardened (climatology npz cache, mirror-lon wrap, IVT mask). Built
  heat+AR viz.json (7–193 KB). `dashboard/eventviz.js` renders both types (lead/day
  selectors, duration bars, AR overlap); Playwright green desktop+mobile light+dark,
  0 console errors. Ops nested-path serving confirmed (HTTP 200). Freeze build in
  progress, then `--all` backfill; weekly.py hook with ewb2 pending.

## Session 2 (design successor 2, from ~12:45 UTC)

- Context: prior session ended ~12:35; the `--all` backfill survived as an orphan
  (PID 1521696, log /tmp/viz_all.log, one event at a time ~2 GB peak, matches the
  "moderate compute" mandate). ewb pipeline full scoring run also in flight
  (started 12:26, 45 jobs × 13 models — scores.json is PARTIAL during the run; the
  dashboard handles it). weekly.py viz hook confirmed in code (step 6, non-fatal);
  ewb agent handle is now 'ewb successor 3' term_0bafb4ff-2f94-4b13-b4e2-63cd5ef32a08.
- Freeze viz built (a5e694627968, 184 KB) — all three event types now have viz.
- Implemented the remaining P1/P2 checklist in dashboard/ (app.js/index.html/styles.css):
  * P0 truth pill reworded: "ERA5 · last ~6 days: IFS analysis" (+ per-event truth
    chip from meta.truth_source.arco_cutoff).
  * Verdict strip (answer-first): per visible event type, leader by mean rel-skill
    over visible cells + "won k/n cells"; recomputes with tab/model/lead filters.
  * Legend numeric ticks (|50 20 10 5 2 1| mirrored, OWB convention).
  * ONE global model row order for all scorecard tables (mean rel-skill over all
    visible cells; baseline pinned first) — rows line up between heat/freeze/AR.
  * Metric one-sentence blurbs (METRIC_BLURB + direction) as header/cell tooltips.
  * Event page: Windborne-style story line (region, dates, threshold+consecutive
    days/peak IVT/landfall, models scored, truth), region chip (coarse bbox-centroid
    gazetteer), peak chip units fixed (°C / kg m⁻¹ s⁻¹), per-event mini scorecard
    (model × metric, mean over leads, event-level metrics as single column, coloured
    vs baseline on this event), deep links `?event=<id>` (read at boot, kept in URL
    on open, cleared on close).
  * Mobile ≤620px: Window meta item hidden, sub-line hidden, controls non-sticky,
    search full-width, compact chips/verdict. (pills were already <button>s.)
- Verified via Playwright (/tmp/verify_design2.py): deep-link boot, verdict==blocks
  (2 of 3 types have scores mid-run), 12 ticks, story non-empty, region+truth chips,
  global order identical across tables, mini>0 for scored non-viz events, no-viz
  events degrade to mini+charts with no viz block, close clears ?event=, tab filter
  recomputes verdict; desktop 1500 + mobile 400, light+dark, zero console errors.
  Screenshots notes/screenshots/design-2-*.png.
- Pending: backfill to finish (~[3/45] at 13:0x; AR events ~10 min cold each), then
  re-verify with full scores (3 verdict chips, AR mini rows) + final screenshots;
  full-scale run IS the checklist "viz module: smoke + full-scale run" item.
- 13:2x: viz `--all` found a real builder bug: heat/freeze events whose event-window
  + lead exceeds a model's max lead (e.g. needs 246 h, model caps at 240 h) raised
  KeyError in `model_daily_pass` → event marked failed. Fixed in
  ewb_live/viz/build.py: missing requested leads now return
  `{status:"unavailable (beyond horizon (need 246 h, max 240 h))"}` per schema.
  Killed and relaunched the batch (new watcher proc_496f16ec2af6, appends to
  /tmp/viz_all.log); built events are skipped, the 2 failed ones now rebuild.
  Also sanity-checked sample-only fallback on a shadow server (:8053, shadow repo
  /tmp/ewb-shadow): verdict strip, story, mini scorecard, deep link, no-viz degrade
  all green with zero console errors (design-2-sample-*.png).
- ~15:05: the 15:03 host RAM resize (64→128 GB) also SIGTERM'd my relaunched
  backfill and the ewb pipeline run (PIPELINE_EXIT=143, same second). I restarted
  the backfill and asked ewb-3 to relaunch scoring — then the ORCHESTRATOR RULE
  landed: NO EWB compute on orca, everything moves to the EWB LXC
  (root@192.168.0.121, /opt/ewb-live, ops/remote.sh wrapper via infra agent).
  Killed my backfill (10/45 viz.json on disk, usable offline as fixtures),
  retracted the relaunch ask in channel_design_ewb.md, nudged ewb-3. Code/docs
  only on orca until notes/infra.md says the wrapper is ready.
- ~15:25: under the compute rule, added tests/test_viz_unit.py — 3 offline
  regression tests for the horizon fix (stubbed open_forecast: shortfall →
  "beyond horizon (need 258 h, max 240 h)", full coverage → ok shape, source
  error → degraded). 3 passed in 1.6 s; full offline suite 39 passed.
  DONE section rewritten to state exact remaining work: remote --all backfill
  + full-scale Playwright re-run once scores+assets land on the LXC.

## Reference review: Neil Gordon's Weather Explorer (2026-11-06, per orchestrator)

Live site + both repos studied (cloned to scratch: tehoro/weather-explorer-web,
weather-explorer-backend). **Both MIT © 2026 Neil Gordon** — code freely
borrowable with the notice kept; their ECMWF data is CC BY 4.0 (doesn't apply
to our data), Natural Earth basemap public domain. Architecture in one line:
backend precomputes per sweep, viewer is pure static files, every per-viewer
cost (decode, colouring, contours, particles) runs in the browser — their 0.5
vCPU per-request prototype froze, so this is now a hard rule (same lesson our
serve.py static-only design already reflects).

Mechanics studied:
- **Data format**: each gridded field frame = one lossless greyscale WebP,
  u8-quantised: value → 0..254 between `meta.lo/hi`, 255 = missing; wide-range
  fields sqrt-scaled; wind u/v packed in R/G channels; contours as 12-bit
  across R+G; categoricals as 1-based category indices. ~1/3 the size of the
  gzipped JSON uint16-base64 form. Frames are immutable files → cache forever.
- **Render**: WebGL2 shader samples two frames and blends (crossfade between
  valid times), colours via a 256-entry LUT texture; Lambert azimuthal
  equal-area projection centred on the view (polar-true areas), inverted
  per-pixel in the shader; Web Worker decodes WebP off-main-thread, marches
  squares for isobars; wind streaks advance by time not refresh rate.
- **UX**: event → ordered *scene tabs* (each = layer + box + window +
  captions); timeline slider with play/pause and ½×–4× speed remembered in
  localStorage; lead shown under the time as "T+123 h, 00 UTC run", analysis
  labelled "analysis"; captions point at features as the animation plays;
  click anywhere for the value at that point; Share encodes full view state in
  the URL (`#h=…&s=…&t=…&map=…&v=lon,lat,zoom`), links kept 7 days.
- **Backend pipeline**: objective field scan → AI writes scenes → a validator
  refuses to publish unless every caption matches the real frames (values at
  named places, extremes) → scenes.py cuts the field to the scene box/window
  (u16 quantise + gzip JSON) → the web repo's sync converts once to WebP.

What EWB Live adopts (all cheap on our coarse 28×39-class grids; canvas 2D +
Leaflet stays):
1. **u8 quantisation + missing-sentinel for viz.json payloads** (their
   backend-side idea: base64 u8 arrays + meta lo/hi, instead of JSON float
   arrays). Consecutive-day counts are integers 0..ndays and masks are
   boolean; IVT magnitudes quantise fine with a fixed scale. Expect our
   40–444 KB files ≈ 3–6× smaller. Concrete plan: bump viz schema to v2 —
   `obs.consec`/`models.*.daily` → base64 u8; `models.*.ivt` → base64 u8 with
   `{lo,hi}` meta and 255=missing. Loader decode is ~10 lines in eventviz.js.
   Do this in the viz module when the backfill next runs on the LXC (no
   rework of the 10 already built — v1 stays readable; ship both).
2. **Lead readout in the scrubber**: under the day/lead selector show
   "T+{lead} h · {init} run" and "observed (analysis)" for obs frames — makes
   the observed-vs-each-model-at-lead comparison unmistakable.
3. **Play + speed for the day scrub** (½×–4×, remembered): consecutive-day
   maps animate day by day; advance by time not rAF count (their lesson).
4. **Click-for-point-value on event maps**: tapping a cell shows observed
   threshold + consecutive count and each selected model's count at that
   cell. Highest-value borrow for our "observed vs model" pitch.
5. **Deep-link completeness**: extend `?event=` with `&lead=&day=&model=` so
   a link reproduces the exact frame; keep unknown params ignored (their
   Share pattern, minus the 7-day expiry).
6. **Immutable viz.json + `Cache-Control: immutable`** (their forever-cache
   rule; event ids already key content). Serve-side change → ask ops when
   serve.py next changes; viz rebuilds already bump `schema`.
7. **Caption line on the map** naming the feature for AR (mask contour +
   overlap), mirroring their "captions point out what to look for".

Skip, with reason: WebGL2 shader + Lambert azimuthal projection (overkill for
u8 grids of ~1k cells; Leaflet's plate carrée is consistent with our coarse
grids — revisit if grids drop below 0.25°); marching-squares isobars, wind
streaks, three.js cyclone view, in-browser MP4 export (no such product
surface); per-frame WebP images (only wins at finer grids; u8-in-JSON gets
90% of the bytes for far less machinery); their AI-highlight pipeline (ewb's
domain — but the **caption-vs-frames validator** idea is directly relevant to
ewb's curation/scoring QA; forwarded note in channel_design_ewb.md).

License bookkeeping if code is lifted: keep the MIT copyright notice in the
file header of anything ported (worker decode loop, speed-control block);
u8-quantisation and URL-state ideas are patterns, no code copied. Legacy
review (/opt/ewb-dashboard, notes/legacy_review.md) will be folded in when it
lands.

## Session 2 cont. (~15:30–16:00 UTC): schema v2, region labels, LXC cutover
- Weather Explorer reference review folded in (section above). Implemented its
  #1 adoptable: **viz schema v2** — pack_u8() u8 quantisation {b64,shape,lo,hi},
  255=missing, for obs/model consec + IVT arrays; eventviz.js unpacks v2 and
  still reads v1 (both paths Playwright-verified; 192→68 KB on d7f17e199dbf).
  Offline round-trip test in tests/test_viz_unit.py (first version failed —
  forgot to dequantize before comparing; fixed, 68 offline tests green).
- Legacy review folded in: **region labels ported** — dashboard/geo/countries.json
  (Natural Earth 110m, public domain, 155 KB, stripped + rounded) + ray-cast
  PIP in app.js (country+continent chip/story, band fallback for ocean, lazy
  load, /data/ route collision avoided via /geo/). Playwright on live server:
  Mali/Switzerland/USA chips exact vs independent python computation, 0 errors.
  verify-harness port + DESIGN-OWB CVD reading are follow-ups after this sprint.
- 15:35 infra posted LXC execution: ops/remote.sh (run/bg/logs/pull-results),
  guard on heavy entry points, network tests auto-skip off-box, dashboard now
  at http://192.168.0.121:8050/. Truth mirror rebuild on box: clean (1568/0 err).
- Launched `remote.sh bg viz python -m ewb_live.viz --all` on the box: died on
  missing system ecCodes → installed libeccodes0(+data) 2.34.1 on the box,
  flagged install.sh gap to infra channel, relaunched: building ~110 s/event,
  schema-aware skip rebuilds all 45 to v2. Watcher will wake me at completion
  for the full-scale Playwright pass against 192.168.0.121:8050.

## Simplification plan
Owner 2026-11-06: page is too verbose, too populated. OWB cues: one hero
panel, kv-stat row, sparse labels, whitespace. Hierarchy = scorecard hero →
event catalog+map → event detail (signature). Keep OWB tokens + Leaflet maps.

1. **Header** → one line: title · week select · updated · About · theme.
   Delete sample-banner prose; keep a short tooltip.
2. **Scorecard hero**: table only, default leads {24,72,120,240}; "more
   leads" control reveals the rest. Remove verdict strip, sc-legend wall,
   trend-chart section from home (chart moves inside a collapsed
   <details> under the table). Mini KPI row (events, models, window, truth)
   as one kv-line in OWB style.
3. **Catalog/map**: unchanged structure; row titles trimmed; filter bar
   stays compact.
4. **Event detail**: title + ONE story line + region/date chips. Observed
   map full width (~360px). Model comparison: chip picker (default top-3
   models for the event + IFS HRES always), 2-4 maps only — never a 13-grid.
   Mini scorecard + charts inside collapsed <details>. No bbox numbers,
   no detection strings, no truth-source chips (About popover instead).
5. **Controls**: leads = pills with sensible defaults; models = checkbox
   chips, only models that HAVE data; hide 6/12/18h noise (bin labels only).
6. **Copy**: labels not sentences; definitions via title tooltips; About
   dialog holds methodology, climatology (P95/P05, OWB ERA5 1990-2019),
   truth sources, metric meaning. Target: ~70% fewer words on home.
7. Verify: simplify-*.png before/after desktop+mobile; deploy box; update
   review_round1.md PREVIEW block.

## Session 3 (design successor 3, from ~18:58 UTC)
- ROLE MIXUP (orchestrator corrected at ~19:00): launched under the name 'ewb';
  before correction I edited `ewb_live/pipeline.py` (added meta.full_run_complete
  bookkeeping) and created `tests/test_pipeline_complete.py`, and synced code to
  the box once (18:58:21). ALL FULLY REVERTED: pipeline.py restored to the exact
  pre-session version (git checkout), test file deleted, box re-synced 18:59:42
  and verified (grep full_run_complete == 0). Children spawned 18:58:21-18:59:02
  may have run my `_job_main` variant — schema-compatible; no output damage
  (scores.parquet untouched by me). Facts I found, handed to ewb via channel
  (aigfs zero-rows, full_run_complete semantics, OOM #2, backfill contention).
- Own scope from here: dashboard/, ewb_live/viz/, notes/review_round1.md.

### Session 3 QA pass (deployed box, browser DOM assertions, ~19:10 UTC)
- Home: header FIRST body child, position:static (no overlap); one-line text
  "EWB Live · updated Nov 6, 2026 · About · theme". Scorecard hero compact:
  table only, lead pills = 1/3/5/7/10d behind 'more' + 'more metrics',
  model dropdown (details.dd) present. Catalog rows: zero 12-hex ids.
  Badge visible: "partial results — 10 of 11 models scored" (correct state).
- Detail (AR 29d2a97a, freeze 88bb73d7, AR 3d32db12): title once ("Atmospheric
  River 2026-08-06 to 2026-08-10"), no hex in title, exactly ONE story line,
  home hidden. Observed map framed at 1.33x RAW bbox (45px pad, maxZoom 5),
  model panels synced (1.22x/0.92x — width-limited by 522px panels, same
  zoom). 0 <circle> SVG elements (no dot pattern); smooth image overlays.
  Model picker cap 4 with ifs_hres baseline locked. No error banner.
- QA gotcha: window.__ev3 is set in a double-rAF -> missing until the tab is
  foregrounded (rAF throttle in background tabs). BringToFront before probing.
  Evidence: notes/screenshots/qa3-{home,freeze-detail,ar-europe}.png.
- REVIEW READY gate: all UI checks above PASS and viz for the 6 review events
  serves 200. Blocked ONLY on ewb's full scoring run + meta.full_run_complete
  (aigfs can never score -> flag is the only badge-clear path; see channel
  19:05). Review doc headline numbers must be re-checked after the badge
  clears, then marker flips.

## Session 3 (design, 22:20-23:35 UTC) — mini review dataset (owner decision)
- Owner lifted the full_run_complete gate: finalize views on a frozen interim
  snapshot with synthetic score cells, plus a sparse variant.
- NEW `scripts/build_mini_results.py`: copies live meta/events_selected/scores,
  fills every event x model x metric x lead gap with deterministic synthetic
  values (per-model skill factors, lead ramps, crc32-seeded noise; rows carry
  synthetic=true), rebuilds scorecard with the exact pipeline.build_scorecard
  reducer (logic replicated; tests/test_mini_snapshot.py pins the shape),
  writes data/results/mini/ (27 ev x 11 models, 31,554 rows / 25,117 synth)
  and mini-sparse/ (29d2a97a AR + 457f3f87 heat + 88bb73d7 freeze).
  meta.mini=true, full_run_complete stripped (mini is not the real complete).
- `dashboard/app.js`: `?data=mini|mini-sparse` -> DATA_DIR override in
  loadJSON (no sample fallback in mini mode); amber header chip (mini-badge)
  reads meta.mini_note. Default view behavior unchanged (verified: partial
  badge still 10/11 on live).
- `ewb_live/serve.py`: route data/results/{mini,mini-sparse}/<file>.json
  (.json only, no fallback, traversal-guarded). tests/test_mini_snapshot.py
  (5 tests) + offline suites green (71 passed).
- Viz rebuild of the 6 review events on the box, serial
  `systemd-run -p MemoryMax=12G` units (ewb's scoring keeps priority per
  orchestrator) — 23:21-23:3x, all OK under cap.
- Review-set change: owner asked 2 per type; freeze is n=1 in the FINAL set,
  so: 3 AR (29d2a97a, 43e1cd0e, 3d32db12) + 2 heat (457f3f87, 08cf5a90
  Amazon) + 1 freeze (88bb73d7); fbe99f30 (East Asia AR) dropped from the set.
  Discrepancy documented in review_round1.md.
- QA on mini: 27-row catalog, all 3 types + all 11 models in scorecard,
  detail Scores fold populated for AR metrics, sparse shows 3 rows, default
  URL untouched. Screenshot notes/screenshots/qa4-mini-home.png.
- Date-semantics fix (QA on rebuilt viz): ev.end is EXCLUSIVE (viz renders
  end-1min; detector titles already use end-1d) but storyLabel + catalog
  showed ev.end as an active day -> all events displayed one day long.
  Added eventLastDay() and used it in both; verified freeze: title, story,
  catalog and duration=6 now agree. Browser cache gotcha: app.js changes
  need a hard reload (CDP Page.reload ignoreCache) to show up.

## Session 3b — round-2 design review fixes (2026-11-06 ~23:50 UTC)
- B1: focus/touch tooltips + aria + roving tabindex; rows keyboard-operable; map rects keyboard:false.
- B2: builder reason vocabulary (+ REASONS map for old artifacts); ghost rows for unscored roster models (universe = declared ∪ scored — fixed first-cut bug using lead-filtered modelOrder); kv "N scored · M in roster" (roster = max(allModels, meta.models)).
- M1: gzip in serve.py (22× on scores.json), lazy Plotly on fold-open, our-assets no-cache (deploy-strand fix). Mini events.json.
- M2: occFit() occupancy-fit, minZoom 4. M3: Esri dark/light gray catalog, invert filter removed. M4: ink-muted AA. M5: units in story/Peak-col/headers. M6: fixed-scale duration bars + ticks + obs marker + model colors + Δ captions. M7: scroll fade + hint + tap-target sizes.
- Caught mid-QA: attachTip null-parent boot-killer (shipped ~10 min, fixed same session). Lesson: roving tabindex must be normalized AFTER rows are appended.
- Verified live via browser probes: dark Esri tiles, kv/ghost correctness, lazy plotly, gzip headers, kbd tooltip visible, no exception-name leakage. 91 offline tests green.

## Owner answers to review questions (2026-11-07 00:10 UTC, via orchestrator)
- Q1 freeze/p05 'empty': under science-reviewer investigation (possible bug). No dashboard change yet.
- Q2 Greenland freeze: KEEP — no population-bias adjustment.
- Q3 heat set: science reviewer cross-checking vs trends/external reports. Pending, no change.
- Q4 Esri basemap: approved, no offline maps needed. Compliance note:
  - We consume Esri-hosted World (Dark|Light) Gray base tile endpoints live (no tile caching/redistribution), which is the documented pattern for non-Esri libs (Esri's Leaflet/OpenLayers integration docs).
  - Terms (ArcGIS Online TOU summary, esri.com/content/dam/arcgisonline/docs/tou_summary.pdf): no systematic tile harvesting, no download/self-host/redistribute of Esri content, attribution to Esri + data providers required; Living Atlas commercial use needs a license — not our case (internal research product, non-commercial, LAN-deployed).
  - Attribution spec (developers.arcgis.com/documentation/esri-and-data-attribution/interactive-maps/): show "Powered by Esri" + Sources line, visible on/under the map. DONE: both catalog and detail maps' attribution controls now read "Powered by Esri | Sources: Esri, Garmin, GEBCO, NOAA, USGS, and the GIS User Community" (bottom-right, always visible). OSM fallback keeps OSM credit.
  - Residual risk note: TOU summary strictly phrases Esri-content use as "with Esri software or an AGO subscription"; anonymous keyless tile endpoints are widely used per Esri's own non-Esri-library docs, traffic here is negligible. If the product ever goes public/commercial, buy an ArcGIS Location Platform key.
- Q5 FourCastNet ARs: ewb to add RH->specific-humidity preprocessing. No client change needed: the builder's "unsupported (no specific humidity...)" status is data-driven from model_info.variables — it disappears automatically once ewb's spec advertises specific_humidity. Verify on first FCF AR viz + scores landing; then remove any stale wording expectations from review docs.

## Round-3 design-review fixes (2026-11-07 00:40-00:55 UTC)
- B1-touch: compat-mouseout double-toggle root-caused; touch show moved from
  pointerdown to click; verified via CDP touch emulation + dispatchTouchEvent.
- M6: fixed 168px value column (equal tracks), numeric day-ruler under bars.
- M4-ghost: dropped row opacity; real inks pass AA dark (aigfs now scored ->
  zero ghost rows live; rule remains).
- Peak column: kg/m/s abbreviation + title attr; fits 1440 without scroll.
- Yielded box memory to ewb (no viz jobs; zero active at request time).
- Debug hook window.__debug.state added to app.js (read-only state getter).
- B1-touch v2 (01:15 UTC): design-review's real-hardware log showed compat
  mouseout fires AFTER click (opposite of my assumption). Applied their
  800ms touch-window guard on hide paths + dropped pointerType click guard.
  Verified in has_touch + hover:none config with delayed check; desktop +
  keyboard paths re-checked clean.

## ROUND-2 CLEAN (2026-11-07 01:30 UTC)
design-review final verdict: 0 blockers, 0 majors live. Touch verified on true
phone emulation, axe 0 both themes. review_round1.md flipped to
"REVIEW READY — ROUND-2 CLEAN". Non-gating backlog + round-3 agenda in
design_review.md § DONE (dueling-map hero + verdict strip lead). Still open
on my side: post-run viz rebuild (FourCastNet AR panels + fresh scores) gated
by ewb memory pact; peak-column 1440 polish.
- ewb 01:10: a3bb50ccde3d excluded (all 11 models crash EWB spatial-
  displacement metric, EWB-side bug, ~119-128 GB event OOM risk;
  bench_failed). AR lane risk: whole-new-set test in flight; worst case =
  owner call. Verified live dashboard handles zero-score event cleanly.
  review_round1.md: ETA revised 03:30-04:30 + exclusion note.
- ewb 01:45 correction: pressure wave = science-review sr01_clim_validate.py
  scans, not design viz (12 GB cap held). Acked; no state change.
- ewb 01:45: freeze lane p05-empty LIVE (26 events); B2 mirror-NaN bug -> 17 events rescoring (review #5/#6 affected, same IDs); full_run_complete ETA 07:00-08:00; rebuild mini after next merge.
- Empty state verified LIVE (freeze lane, all three sites). Review set re-pinned: fbe99f/3d32/9700e4 + 457f/64a0/bace10.
- ewb TC-guard (B8): published set 13 AR + 11 heat, dropped fbe99f/6ad93b/9700e4; bind run_id 20261107T021544Z+. Review ARs #1/#3 re-pinned to 806867ad1ba1 + 7302847d9b16 (verified). Home 24 rows, TC cards gone, stale links degrade quietly.
- DR3 n/a rendering: app fetches pair_status.json; zero-row event fold + table-row tooltip show ewb reason (data-driven, no ids). a3bb verified live. Builder respects pair_status for synthetic fill + copies file into snapshots. Review #1 -> 85df401a3d8c (806867 is n/a). 395349/806867 need pair_status entries from ewb.
- ewb 03:00: ETA ~16:00-17:30 UTC (80 min/AR job, serial lanes); 4a9c67b01f25 = 4th n/a(memory) AR (4/13 n/a, 9 score fully); ids/run unchanged. Review set unaffected; docs updated; 4a9c needs pair_status entries from ewb.
- ewb 04:04 correction: ARCO cutoff re-scan, run 20261107T040432Z (14 AR+13 heat, antimeridian AR 255f68102c43, n/a=a3bb+4a9c only, ETA 19:00-23:00). B8 audit: dst_grid CRASH fixed (continuous lattice) + regrid frame fix + 3 unit tests; eventviz continuous across-180 fit/box; QA fixture dir mini-am (labeled synthetic); review #1 -> 255f68102c43.

## Session 4 (design successor 4, 2026-11-07 ~20:45→21:40 UTC) — split, viz v3, KT
- Post-restart successor. New split: **frontend** owns dashboard/*.js; I own
  visual direction (tokens/layout/copy/QA sign-off) + ewb_live/viz. Do not
  edit dashboard JS anymore — copy/behavior asks go through
  notes/channel_design_frontend.md.
- Owner refs studied (notes/owner_refs/): back-nav, more-metrics duplication
  (+ hourly multiplication bug — frontend fixing), opaque dark square
  (no-data background), over-zoomed maps, strikethrough chips; heat/freeze
  must match 2026-11-07_heatwave_reference.png. FINAL owner ruling: ONE
  exceedance class — heat above p85 in a single warm color, freeze below p15
  in one blue, rest transparent; NO extra quantiles ever. Reference chrome:
  light basemap, crisp cells, white "Heatwave #<id>" chip, bottom bar
  (Valid time left, single-swatch P85/P15 legend right), day stepping, same
  component obs vs model-at-lead.
- **viz schema 3** shipped (ewb_live/viz/build.py): obs.daily_bits +
  models[m][lead].daily_bits = bit-packed per-valid-day exceedance masks
  (day-major/row-major/MSB-first, day stride ceil(ny*nx/8)); land already
  applied; purely additive over v2. pack_bits round-trip test pins the JS
  decode contract (first test version wrong — assumed per-row padding;
  packbits pads once per day). Offline suite 120 passed.
- Fixture on the box: 457f3f87e24a viz.json schema 3 (unit ewb-viz-v3fix,
  MemoryMax=12G, 8 s, 170 KB; serving verified). Caveat: current published
  events carry details.quantile 0.95 until ewb's p85/p15 re-run.
- Spec + rulings posted: notes/viz_v3_spec.md + channel (overlay colors
  --tier heat #c6493c / freeze #1a4f96 @ .85, copy strings for p85/p15
  TYPE_CRITERIA + About, controls ruling: one disclosure per row — 'more
  leads' stays in LEADS row, metrics toggle moves into scorecard block
  headers). QA controls (tabs ×3, open/close ×3, both themes) after their
  deploy; will sign off in channel.
- Empty-lane state: verified generic (data-driven per type in tabs/table/map/
  scorecard + 'all' line — freeze lane live-proves it; heat/AR follow the same
  code path).
- Box facts: pipeline unit stopped 17:47 UTC pre-p85/p15; scores.json Nov 6
  18:52 merge; events = p95 run 20261107T040432Z; full_run_complete unset.
  Asked ewb (channel 21:25) to post the final list + flag completion; review
  set refresh (2/type intent), six-event viz rebuild, mini rebuild and
  headline-number verification all gated on that.
- Deliverables for public launch: notes/handoff_design_final.md +
  notes/kt_design.md (KT), per orchestrator; 'KT READY' posted to frontend
  channel. review_round1.md flipped to "REVIEW ON HOLD" (first line honest —
  READY returns when p85/p15 + final list + reference maps + real-score
  headline all land).

## Session 4 cont. (2026-11-08 ~02:00 UTC) — AR green, paper-matched IVT ramp, site public
- AR type color: owner wants green everywhere the type is color-coded. Picked
  **light `#2e7d32` / dark `#66bb6a`** (measured: 5.13:1 on paper, 6.40:1 on
  dark paper — both AA for text). Distinguishable from heat blue... verified
  hue-family separation vs heat/freeze and all MODEL_COLORS; closest neighbor
  is pangu teal #21b8a0 (much lighter + cyan-shifted; side-by-side ok, and
  charts label series). Mitigation noted: green vs heat have similar
  luminance under red-green CVD — type is never color-alone (labels on every
  tab/chip/legend), so acceptable. Replaces #6f251b in TYPE_META + any CSS
  token; frontend sweeps hardcoded hex.
- IVT colormap (owner req: match EWB paper arXiv 2605.01126): vision probes
  timed out; classified figure pixels programmatically (hue histogram,
  fig4.png + global_ar_era5_20211024_18z.png): paper AR fields are a
  **single-hue blue sequential ramp** (hue ~200°, dark blue = high IVT,
  near-white = low; NO magma/viridis). Replaces our magma bins. Ramp spec
  (stops are design's where paper colorbar not machine-readable): linear
  alpha/depth ramp on `#dbe9f6 -> #08306b`-style Blues, domain **250 → 1000
  kg m⁻¹ s⁻¹, capped**, **fully transparent below 250** (owner: no black
  low-end; basemap shows through — paper uses white, transparency is the
  map-appropriate equivalent), colorbar ticks 250/400/600/800/1000 with the
  **400 AR-threshold tick marked** (bold + 'AR mask ≥400'). Same ramp +
  floor on obs and every forecast panel. Green type color vs blue field:
  no clash (chrome vs field); flag stands — freeze type blue is closer to
  the ramp, accepted (lane headers disambiguate).
- Event-box click spec (owner req; frontend implements): box click = list-row
  click (same pushState #event=<id>&lead=, detail, back/Esc). Hover: stroke
  2→3px type color, fill alpha .12→.22, #tooltip with title + dates, cursor
  pointer. Selected on return: 3px stroke + 1.5px canvas-color halo. Keyboard:
  list rows remain the a11y path (boxes tabindex=-1, aria-hidden). Overlap:
  smallest-area box topmost and wins the click (z-order = area desc);
  chooser deferred (lazy: deterministic + matches "smallest wins" ask).
- SITE IS PUBLIC (https://ewb.espr.ai). LAN IP removed from About (infra-
  domain applied; verified absent in box tree + live HTML). No infra strings
  in served files from me; respond to infra requests in channel_infra.md.

## Session 5 (design) — 2026-11-08 ~03:15–04:05 UTC
- QA'd frontend 02:05 deploys on live ewb.espr.ai: green tokens PASS
  (light/dark computed exact, map stroke census 16/15/1), IVT Blues ramp PASS
  (colorbar pixel #dbe9f6 start, pure-blue, 250/400·mask/600/800/1000 ticks),
  event-box parity FAIL — app.js binds click/hover/tooltip on L.layerGroup,
  which does not propagate child events in Leaflet 1.9.4 (trusted click =
  inert, group receives 0). One-word fix (featureGroup @ app.js:1283) posted
  with re-sign condition (run the two named Playwright tests).
- review_round1.md rewritten + flipped REVIEW READY: 5 pins (255f68102c43,
  3d32db128915, 95aeafb7fd7f, 31a871378c3e, 88bb73d7ae25), headlines from
  real scorecard (AR CSI@24 aifs_single +13.8%; freeze n=1; heat RMSE
  2.00–2.27 °C, IFS heat rows in-flight), public URL only, caveats honest.
- Live data truth: curated events_selected.json = 31 (15 heat/15 AR/1
  freeze); meta 02:17 UTC, full_run_complete=false (ewb re-run ETA ~17:00).
- Owner directive (orchestrator, 2026-11-08): About page carries NO public
  URL or hosting mention — visitors are already on it; About = science only.
  Verified done: sibling removed index.html:116 "Public URL" line; worktree +
  live https://ewb.espr.ai/index.html both 0 hits. Standing rule for future
  About copy drops.

## Global event dashboard — design spec (2026-11-08, design; brief notes/briefs/global_dashboard.md)

DATA (design lane — DONE, shipped): `data/results/global_timeline.json`
schema 1, built by `ewb_live/viz/timeline.py` (auto-runs as the tail of
`python -m ewb_live.viz`, so it regenerates with every viz/weekly pass; ids
can never drift; live at 31 KB, 31/31 events masked). Contract:
`{schema:1, generated, window:{start,end}, mask_bits, events:[{id,type,title,
start,end,bbox,days:[ISO],grid:{lat0,lat1,lon0,lon1,step_deg,step_lon,ny,nx,
continuous_lon},masks:[b64 packbits per day]}]}`. Masks: day-major, row-major,
MSB-first, stride=ceil(ny*nx/8) — identical to viz schema-3 `daily_bits`;
Row order:
the viz grid `lat` array is ASCENDING and the mask rows follow it — how
eventviz.js maps rows to screen (row 0 = top?) pins the row-0 convention;
the timeline inherits that convention bit-for-bit, so render with the same
row→lat mapping as eventviz.js — check its `daily_bits` decoder and copy it. Cells ~1°. `days` are the active dates; missing `masks`
= bbox-only ghost (draw outline, never crash). `continuous_lon:true` →
lon1 > 180, draw on the continuous eastward lattice (no split). AR masks are
the event's anchor (peak) mask repeated daily (matches the AR detail panel).

SHELL (applies to the whole page, incl. this section): `--shell-w:
min(75vw, 1280px)` centered (`margin-inline:auto`) at viewport ≥ 1100px;
below that `width:100%; padding-inline:24px` (16px at ≤620px). Header,
controls, scorecard, map/catalog, event detail and this section all sit
inside the shell; maps and wide tables get `overflow-x:auto` INSIDE it.
Map/table never widen the shell.

MAP: d3-geo `geoEqualEarth` (vendor d3-geo; SVG; land from vendored Natural
Earth 110m land polygons `dashboard/geo/land-110m.geojson`). Projection fit
to the section width with 55.8°N flattening default; fitExtent padding 12px.
Colors: ocean = `--canvas`, land = `--paper`, coast/borders =
`--border-strong` 0.6px, graticule 10° solid at 0.18 alpha (same convention
as event maps). Esri tiles cannot be used here — that is accepted: the Equal
Earth basemap is vector, fully theme-matched, offline-safe.

FOOTPRINTS: per event+date, set of coarse cells → ONE `<path>` per event
(subpaths per cell run) filled `--series-<type>`: alpha 0.30 normal, 0.50
selected, 0.10 dimmed (while others are selected), 0.55 hover. No cell
strokes. Event bbox: dashed 1px `--series-<type>` alpha 0.5; selected: solid
1.5px + 1.5px `--canvas` halo under (catalog parity). Hit target = bbox
hull, min 44px on touch (invisible expanded rect). Cursor pointer.
Transitions: fill-opacity 120ms, none under prefers-reduced-motion.

TIME SLIDER: daily across window (90 steps). `<input type=range>` styled to
token ladder; thumb visual 20px with 44px touch pad; play/pause button
500ms/day; autoplay OFF under prefers-reduced-motion; arrows ±1 day,
Home/End → window edges; aria role=slider with
`aria-valuetext "<Mon D, YYYY> — N events active"`. Date readout mono
`--text-sm` at the right. Slider + play + readout in one row; on mobile the
slider takes the full row, readout above it.

LEGEND/LIST: three type chips (swatch `--series-*`, count of ACTIVE events,
live with the slider) shared with the page type-tab state; list of active
events (type dot + short title + date range, `--text-sm`), max-height
220px scroll; click toggles selection.

GANTT STRIP: under the slider, 3 swimlanes (heat/AR/freeze order
TYPE_ORDER), 12px row + 4px gap, x-axis = same date scale as the slider
(shared tick labels on demand: first-of-month, `--text-2xs` mono). Bar per
event in its lane, `--series-<type>` alpha 0.8 (selected 1.0 + 1px
`--canvas` outline). Click bar = select event + slider jumps to event start.

SELECTION & URL: multi-select; any subset. Selected = highlight, others
dim (no dim when selection is empty). URL `#global=id,id&date=YYYY-MM-DD`
via pushState on discrete actions, replaceState while dragging;
popstate/hashchange re-applies (reuse syncFromLocation plumbing). From a
selected event's list row: an "open" affordance goes to the existing
detail (`#event=…`); Back returns to this section with state restored
(URL is the source of truth).

LAYOUT: ≥980px: grid `1fr 280px` (map | legend+list), slider+Gantt span
both columns below. ≤979px: stacked — map (height 52vw, min 300 max 460px),
legend chips row, collapsible list, slider, Gantt. All inside the shell.

STATES: empty day → caption "No events on this date" (`--text-dim`,
`--text-sm`); asset 404/204 → honest "Global timeline is not available for
this run yet." (never a blank map); boot skeleton at final height (CLS 0).

A11Y: map `role=img` + aria-label summarizing date + counts; each event
`<g role=button tabindex=0 aria-pressed>` Enter/Space toggles; focus ring
`--accent-focus`. All interactions keyboard-reachable without a mouse.

TESTS (frontend, Playwright): slider drag changes the active-count readout;
map click and list toggle stay in sync (multi-select); URL round trip
(#global&date reload → same state); open event → Back restores global
state; aborted/404 asset request → honest message, no console errors.
READY protocol: after deploy, design re-QAs, then frontend posts
''GLOBAL DASHBOARD READY' — with
desktop 1440 + mobile 390 screenshots into notes/screenshots/.

## Raw exceedance map — design spec (2026-11-08, design; brief notes/briefs/raw_exceedance_map.md)

PLACEMENT: new `#raw-sec` DIRECTLY BELOW the existing `#global-sec` (no
toggle coupling — the event dashboard stays exactly as signed off). Header
line: "Raw detector output — every 6 hours" + muted subline "ERA5 threshold
exceedances before event filtering (heat p85, freeze p15, AR ≥400 mask).
Frames end <cutoff> (end of the ERA5 copy)."

DATA (design lane — DONE, backfilling on the box):
`data/results/raw_exceedance/index.json` + `<YYYYMMDDHH>.bin` frames.
- index (EXACTLY the frontend 13:40 contract, no extra keys):
  `{schema:1, generated:iso, grid:{res_deg:0.25, ny:721, nx:1440,
  lat0:-90, lon0:-180, step_lat:0.25, step_lon:0.25},
  frames:["YYYYMMDDHH",... asc], cutoff:"YYYYMMDDHH",
  window:{start:"YYYYMMDDHH", end:"YYYYMMDDHH"}}`.
- frame bin = **zlib( packbits(heat) ++ packbits(freeze) ++ packbits(ar) )**,
  each plane 129,780 B = row-major bits of 721×1440, MSB-first. Plane
  stride = ceil(721*1440/8). Decode in JS: `DecompressionStream('deflate')`
  (native zlib wrapper) then read bytes bitwise. **Row 0 = lat -90 (S→N),
  col 0 = lon -180** — the builder converts the ERA5-native (N→S, 0-360)
  mirror to this contract orientation (`_to_display` = flip rows, roll cols
  by half), so the frontend decoder needs no rotation.
- Frames are 6-hourly 00/06/12/18 UTC; ~22 KB each. Missing frame file →
  graceful "frame unavailable" caption, never a crash.

CANVAS RENDER (Equal Earth, same basemap style as the event map):
1. One offscreen equirect canvas (1440×721, ImageData): per frame set pixels
   by priority heat > freeze > ar (rare overlaps; temperature wins).
   Colors = series tokens read once via getComputedStyle
   (`--series-heat/-freeze/-ar`), alpha 0.85, everything else transparent.
2. Project once: for each SVG-pixel-grid position precompute the source
   (col,row) via `geoEqualEarth().invert()` (cache a Uint32Array lookup at
   load; recompute on resize only). Per frame: drawImage from the equirect
   canvas through the LUT with nearest sampling → crisp cell edges. Target
   ≥10 fps playback; if the LUT path drops frames, fall back to drawing
   visible runs per row directly (still no per-pixel JS projection).
3. Land never needs to be drawn (basemap shows through transparent cells).

CONTROLS: day-grouped 6-hour slider across `window` (360 steps; End is the
cutoff frame — days after get NO frames and the track end shows a gray tick
+ the subline explains). Play/pause + speed pill (250/500/1000 ms),
reduced-motion: no autoplay, transitions off. Readout mono
`Valid time: 01 Nov 2026, 12:00 UTC`. ←/→ ±6 h, Home/End edges.
Layer toggles: three chips (swatch + name + live pixel count of the current
frame, e.g. "heat 0.31% land"); all ON default; a11y aria-pressed.
Optional (only if trivial): none. No event boxes on this map — ever.

URL: `#raw=YYYYMMDDHH&layers=hf,a` (layers param omitted when all three).
Independent of `#date/#global` (event dashboard) state; a raw-only hash
must not disturb the event dashboard. Back/forward via existing plumbing.

PREFETCH: LRU cache of 12 frames; on step, fetch ±2 immediately, then fill
forward every 4th frame (playback is forward-biased). Abort in-flight loads
on rapid scrub. Frames served gzipped already by serve.py — no double pass.

STATES: boot skeleton at final height; frame fetch fail → honest caption;
empty frame (all zeros — possible mid-summer freeze=0 etc. is fine, the map
legitimately shows only what exists; if ALL visible layers are empty show
caption "No exceedances on this frame" — legitimate detector output).

A11Y: canvas `role=img` + aria-label summarising the frame ("Nov 1 12:00
UTC: heat 0.3% land, freeze 1.1%, AR 2.4% ocean"); slider aria-valuetext;
toggles keyboard-operable.

TESTS (frontend): step changes frame + readout; layer toggle changes
canvas + count; URL `#raw=…&layers=…` round-trip; missing frame file →
caption not crash; end-of-data marker present when cutoff < window end.

SIGN-OFF: after deploy I re-QA live (frame parity vs .bin decode, mobile
390, reduced-motion, URL) and frontend posts 'RAW MAP READY' with 1440 +
390 screenshots into notes/screenshots/.

## Session 5 (2026-11-08, cont.) — RAW MAP exceedance delivered + QA PASS
- Caught contract drift pre-first-frame: my builder had index/grid keys +
  row0=north + 0-360 lon vs frontend's deployed contract (row0=south,
  lon0=-180, frames strings). Killed run, purged 185 wrong-orientation
  frames, made builder emit frontend contract exactly (module renamed
  exceedance.py for ewb's weekly hook; _to_display flip/roll unit-tested).
- Crash found+fixed: fmt() choked on datetime64[ns] str() nanosecond tails.
- 336 frames backfilled; wire-verified via decode + event-bbox orientation
  cross-check (0.54 vs 0.05 in/mirror). Stale-server restart needed after
  serve route change (ewb-live-web) — noted for infra next time.
- Browser QA PASS (play, toggles, honest states); false-alarm "not
  available" was pre-restart browser cache. RAW MAP READY handed to
  frontend with screenshot go-ahead.

## Merged global map — design spec (2026-11-08, design; brief notes/briefs/merged_global_map.md)

ONE section `#global-sec` at top of page replaces BOTH current maps;
`#raw-sec`, globalmap.js, rawmap.js and their CSS retire into one module
(frontend picks the filename; suggest `map3d` free name e.g. onemap.js).
Everything below is behavior contract, not structure.

STACK (single shared Equal Earth fit, one projection instance):
1. CANVAS (bottom): raw 6-hourly planes exactly as shipped today (decode,
   LUT draw, priority heat>freeze>ar, tokens unchanged).
2. SVG (top): sphere outline, land-110m coastlines, graticule (existing
   styles), then EVENT BOXES.
BOXES: one per curated event; geometry = `bbox` emitted as a GeoJSON
Polygon ring projected through the shared projection (edges curve with
parallels; antimeridian handled by d3-geo clip the same way AR masks
already are — use the continuous-lon trick for lon1<lon0 events). Style:
fill = series token at 0.10 opacity, 1.5px stroke full token; selected
0.22 fill + 2px stroke; hover 0.18 + tooltip (title, "qualified {date} →
{end}"); focusable (role=button, aria-label with title+dates). NO
footprint masks on this map (boxes replace them — retire .gm-foot CSS and
the timeline asset's masks[] may stay for other consumers, unrendered).

TIME: 6-hourly steps from window.start to window.end (model window —
past the ERA5 cutoff too). Raw canvas draws nothing past `cutoff`: gray
end-tick on the slider + muted caption "raw frames end {cutoff} (ERA5
copy); event boxes continue". Box of event e visible at step t iff
qualified_at <= t < end. Slider, play/pause, ±6h, speed, "Valid time"
readout: unchanged from the raw map.
GANTT STRIP: bars per event on the same 6-hour track, from qualified_at to
end, type-colored, height 8px; click bar = select event + jump slider to
its qualification step. Event list: unchanged, rows clickable (select =
highlight box + detail panel via existing selectEvent; Back returns with
#map state intact — existing urlParams merge handles it).

DATA (design lane): global_timeline.json schema 2 — per entry add
`qualified_at:"YYYYMMDDHH"` (ewb computes from detection logic; see
orchestrator_watch note) + `q_source:"detection"|"start_fallback"`
(fallback = start truncated to hour — honest placeholder until ewb
lands). grid/frames/cutoff from raw_exceedance/index.json mirrored into
the timeline header so one fetch drives both layers. Entry bbox/start/end/
type/title unchanged; `masks` kept for other consumers, not rendered here.

URL: `#map=YYYYMMDDHH&layers=h,f,a&sel=id1,id2` (layers omitted when all
on; sel omitted when empty). Redirect shim on load + on hashchange:
`raw=` → `map=`; `global=a,b&date=D` → `map=D00&sel=a,b`. Old params never
survive a rewrite. Event detail keeps `event=`/`lead=` (orthogonal).

TOKENS / LIGHT THEME FIX (brief §6, mine to spec, applies with the swap):
`--map-ocean` light #eef4f8 / dark #0e1620 (currently transparent white on
white = the white-on-white complaint); sphere gets `fill: var(--map-ocean)`;
`.gm-land` fill stays `--paper` ONLY against the new ocean fill — verify
contrast pair in both themes; boxes over ocean must keep >=3:1 stroke
contrast (tokens already do on paper; re-check against --map-ocean dark).

A11Y: boxes focusable in tab order after the slider; canvas aria-label
extends with "n events visible (3 heat, 1 AR)" per frame; gantt bars are
buttons with aria-label.

TESTS (frontend, Playwright): box appears EXACTLY at qualified_at step and
gone at end step (fixture event straddling cutoff); layer toggles; select
+ Back restores #map+sel; #raw=/#global= redirect shims; antimeridian
fixture draws (no full-width band); light+dark contrast audit; mobile 390
no overflow. READY protocol unchanged: deploy -> design live QA -> frontend
posts '## [time] frontend: MERGED MAP READY' + 1440/390 shots.

## Session 6 (2026-11-08, eve.) — merged map: asset landed + QA round 1
- Rebuilt `global_timeline.json` on the box (18:40): q_source "detection" for
  all 31 (ewb's qualified_at, byte-identical vs events_selected.json);
  raw header mirrors index.json (cutoff = last frame = window end).
- Live QA round 1 (channel 18:55): visibility/URL/back/gantt/playback/
  mobile/light-theme PASS. Two blockers filed to frontend: AM-event .gm-hit
  spans the naive merged band (click outside the event); redirect shims drop
  `date`->`map` and leave stale raw=/global= keys after the boot rewrite.
- Ruling: dark --map-ocean stays #0e1620 (spec); box strokes >=3:1 verified
  both themes. Copy ask: past-cutoff badge -> "raw layers end {cutoff} ·
  ERA5 lag". Chips-with-count is a non-gating rider; speed select accepted.

### Rider — full-width time slider (orchestrator polish, 2026-11-08 eve.)
Track = full section (map) width, delete the leading empty span so the 1fr
column isn't squeezed; readout right-aligned same row (not relocated — e2e
reads it in place); mobile: readout above, track below; QA gate: track both
edges = section edges, step 0/max on the strip's first/last x within the
strip's own margins. Spec text in channel 19:45.
