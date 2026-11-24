# ops — log

Agent: ops (senior frontend + DevOps). Date: 2026-11-06.

## Decisions & work

### Dashboard (task 1)
- **Weekly-cycle header (SPEC §4.1/§5).** Added `#cycle-line` pill in the header
  (`dashboard/index.html`, `styles.css`), rendered by `renderHeader()` in
  `dashboard/app.js` as `Week YYYY-WW · updated <Mon D, YYYY> · next update <Mon D, YYYY>`
  from `meta.json` (`week`, `last_updated`, `next_update`). Any missing key degrades to
  `—` in that segment only; a missing `cycle` just drops the tooltip hint. Hover tooltip
  carries the raw ISO timestamps and the archive note when viewing a past week.
- **Week picker.** `#week-picker` select in the meta strip, populated from
  `/data/results/weeks.json`; switching weeks reloads all four JSONs from
  `/data/results/history/<YYYY-WW>/` (no sample fallback for archives — a missing
  archive week is shown as an error, not silently substituted). `?week=YYYY-WW` URL
  param supported (deep links). The current week appears as "Current week" at the top.
- **serve.py.** Exposes `GET /data/results/weeks.json` (JSON listing of
  `data/results/history/<YYYY-WW>/` dirs matching `^\d{4}-W\d{2}$` that contain at
  least one `.json`, with their file lists) and serves
  `data/results/history/<YYYY-WW>/<file>.json`. Path handling keeps the traversal
  guarantees (normalized segments, week regex, filename restricted to `<name>.json`).
  Note: `ewb_live/serve.py` was rewritten mid-session by agent `ewb` (module-level
  `RESULTS_DIR`, CLI is now just `--host/--port`); my routes were preserved on top —
  verified with curl, including traversal probes (`..%2f` → 404).
- **Real-schema adaptation (no pipeline changes).** The dashboard reads both shapes:
  scorecard/scores as flat arrays (real pipeline) or `{rows: …}` dicts (sample);
  `lead_time_hours` may be `null` (event-level metrics like DurationMean/EarlySignal)
  → rendered as a separate *event* column in the scorecard, excluded from lead charts.
  `mean_value`/`value` NaN → neutral "—" cells, never coloured, skipped from means.
- **Baseline-relative skill.** `state.baseline` = `meta.baseline_model` (fallback
  `ifs_hres`, matching pipeline `BASELINE`). Relative skill is computed in the
  dashboard from `mean_value` vs the baseline's `mean_value` at the same
  event_type×metric×lead, sign-normalised with `higher_is_better` from
  `meta.metrics` (with a built-in `FALLBACK_METRICS` table for the known EWB metric
  keys incl. CSI/EarlySignal where higher is better). Models with no baseline row at
  a lead (or baseline NaN) get value cells with **no colour** and a "no skill ref"
  tooltip — the aigfs-style "AR-only model" case renders "—" everywhere else.
- **Scorecard tooltips show `n events`** (from `n_events`) on lead/event cells; the
  Mean-column tooltip reports the mean of visible leads and per-lead rel-skill list.
  Mean over leads respects the lead-time filter chips.
- **Real-data scale.** 1540-event real dataset loads in ~1.8 s headless. Event table
  renders at most 400 rows + "…N more — refine with the filter" (count stays exact).
  Map boxes crossing the antimeridian (19/22 real freezes have `lon_min > lon_max`)
  are split into two Leaflet rectangles; fit-bounds ignores the wrap artefact.
  Fixed a latent bug in the skill-charts renderer: the `.chart-card` (with the metric
  title) was never attached to the DOM.
- **sample/meta.json** now includes `cycle: "weekly"`, `week`, `next_update`
  (generator `dashboard/tools/generate_sample_data.py` updated + regenerated).

### Ops (task 2)
- `ops/ewb-live-web.service` (`Restart=always`, logs → `logs/web.log`),
  `ops/ewb-live-weekly.service` (oneshot) + `.timer` (`Mon *-*-* 06:00:00 UTC`,
  `Persistent=true`, logs → `logs/weekly.log`), templated on `__REPO__`.
- `ops/install.sh`: renders units into `~/.config/systemd/user`, enables linger,
  starts web + timer; cron/nohup fallback when `systemctl --user` is unavailable;
  `--status` / `--uninstall`. Verified timer normalises to Mon 2026-11-09 06:00 UTC.
- Installed & running: `ewb-live-web.service` active on 0.0.0.0:8050, curl 200 for
  `/`, `events.json`, sample-fallback `meta.json`, `weeks.json`. Linger is on
  (`loginctl show-user orca` → `Linger=yes`; the transient `enable-linger` non-zero
  exit is spurious). Old test server on 8051 (pid 904538) stopped.

### README (task 3)
- `README.md` written: overview, architecture diagram, data-source table, run
  commands per step, weekly cycle + ops commands.

### Verification (task 4)
- Playwright + headless chromium (`uv add --dev playwright`) against sample-mode
  and real-mode servers. Functional assertions: cycle line text, picker options and
  week switch, tabs/counts, 400-row cap, detail charts, mean-vs-lead-filter,
  tooltip contents (`vs ifs_hres`, `n events`) — all pass, **zero JS console/page
  errors** in every run.
- Screenshots in `notes/screenshots/`: `dashboard-sample.png`, `dashboard-real.png`,
  `dashboard-real-detail.png` (older `0x-*.png` are the dashboard agent's).
  Real-mode shot was taken while only `events.json` existed (scores still being
  scored by agent ewb); scores fall back to sample per the server's design — worth
  re-shooting after the first full weekly run.

## Requests
- (to ewb) None blocking. Observation: one-shot `pipeline.py` meta.json lacks
  `week`/`next_update`; the dashboard degrades to `Week —` for those. `weekly.py`
  already writes all three keys, so after Monday's first weekly run the header is
  complete. If you ever add `cycle`/`week` to the one-shot meta too, the dashboard
  picks them up with no changes.
- (to ewb2, withdrawn) Requested earlier whether the pipeline should write the
  selected set as `events.json` — no longer needed: the dashboard now prefers
  `events_selected.json` when present, so no pipeline change is required.

## Round 2 — orchestrator dashboard fixes (2026-11-06)

Four fixes from the orchestrator's review of `notes/screenshots/now.png`:

1. **Selected events by default.** `loadData()` now fetches
   `data/results/events_selected.json` first (scored set: 15/15/15) and falls
   back to `events.json` (raw candidates) when absent. serve.py answers **204
   No Content** for a genuinely-missing `events_selected.json` (see
   `OPTIONAL_FILES`) so the probe never logs a console 404; `fetchJSON()` maps
   204 → null. Tabs now read 15/15/15.
2. **Lead binning.** 37 raw lead offsets (6h steps up to 252h) are binned by
   nearest to `LEAD_BINS = [6,12,18, 1d..10d]` via `binLead()`/`mergeBinned()`
   (mean of rows sharing a bin; `idKey` distinguishes scorecard `event_type`
   vs scores `event_id`). Only bins with data appear; scorecard defaults to
   1d/3d/5d/7d/10d (`DEFAULT_LEAD_BINS`).
3. **Week header derivation.** When meta lacks `week`/`next_update`,
   `isoWeekStr()` derives the ISO week from `last_updated` and
   `nextWeeklyUpdate()` computes the next Monday 06:00 UTC. Header now shows
   `Week 2026-W45 · updated Nov 6, 2026 · next update Nov 9, 2026` from the
   one-shot meta.json.
4. **Numeric lead axes.** All lead charts plot `x = lead_hours/24` on a linear
   axis with `tickmode:'array'` per-bin ticks (`numericLeadAxis()`). Ticks are
   **density-thinned** to the card's pixel width so 6h/12h/18h never collide on
   narrow cards (hover still shows the exact bin). Two layout bugs fixed on the
   way: detail charts dropped their `.chart-card` title wrapper when appending
   (`host.appendChild(div)` → `card`), and skill charts were plotted mid-append
   so `getBoundingClientRect()` measured the auto-fit grid before its final
   track count — plotting now happens in a second pass after the grid settles.

Verification (playwright, `:8050` real data + sandbox servers for sample-only and
snapshot modes): tabs 15/15/15, cycle line correct, chips 6h..10d with
1/3/5/7/10d default-on, x-axis linear with 11 clean ticks, freeze + detail charts
linear, **zero console/page errors** in all three modes; `pytest -m "not network"`
→ 39 passed. Screenshots refreshed: `notes/screenshots/dashboard-real.png`
(+ `dashboard-real-detail.png`, `dashboard-sample.png`); vision-reviewed: no
overlapping/clipped tick labels remain. Known cosmetic: the skill-charts grid is
height-imbalanced (3 stacked charts in the heat-wave block vs 2 in freeze — data
shape, not a rendering bug).

## DONE

- Weekly-cycle header line + week picker (serve.py weeks.json + history routes):
  implemented, curl- and browser-verified.
- Sample meta.json updated with `cycle`/`week`/`next_update`.
- Dashboard handles real scale/missingness: verified against the live 1540-event
  dataset (1.8 s load, bounded table, antimeridian map fix, NaN/null-lead guards,
  no-skill-ref cells, `n events` tooltips). Real schema needs no pipeline changes.
- systemd --user units + `ops/install.sh` installed; web service live on :8050
  (curl 200), weekly timer armed for Mon 06:00 UTC (Persistent); 8051 test server
  stopped; linger enabled.
- README.md at repo root.
- Headless screenshots saved to `notes/screenshots/`; no JS console errors.
- Not done (by contract): git commit (SPEC forbids); `weekly.py` untouched.
