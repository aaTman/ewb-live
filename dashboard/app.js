/* EWB Live dashboard — vanilla JS + vendored Plotly/Leaflet. No build step. */
'use strict';

/* ------------------------------------------------------------------ */
/* constants                                                           */
/* ------------------------------------------------------------------ */
const TYPE_META = {
  heat_wave:          { label: 'Heat waves',         short: 'Heat wave',
                        rule: 'ERA5 2 m temperature above the p85 climatology at every 6-hourly step of a UTC day, over land.' },
  freeze:             { label: 'Freezes',            short: 'Freeze',
                        rule: 'Below the p15 climatology and at or below 0 °C at every 6-hourly step of a UTC day, over land.' },
  cold_snap:          { label: 'Cold snaps',         short: 'Cold snap',
                        rule: 'Below the p15 climatology at every 6-hourly step of a UTC day, over land — the freeze rule without its 0 °C cap.' },
  atmospheric_river:  { label: 'Atmospheric rivers', short: 'Atmospheric river',
                        rule: 'IVT peaking at ≥400 kg m⁻¹ s⁻¹ with a ridge criterion; starts over ocean and makes landfall.' },
  tropical_cyclone:   { label: 'Tropical cyclones',  short: 'Tropical cyclone',
                        rule: 'One IBTrACS storm: ≥3 fixes at tropical-storm force (≥34 kt) spanning ≥12 h. Scored on track error against the best track, from the first ≥34 kt fix.' },
  flood:              { label: 'Floods',             short: 'Flood',
                        rule: 'AI-estimated daily or 72 h rain (Google PARDIG, CC BY 4.0) past the ERA5 p95 wet-day climatology AND at least 20 mm/day (50 mm/72 h). Scored vs AI-estimated precipitation, GraphCast only. (IMERG rain is the fallback source.)' },
  midlat_cyclone:     { label: 'Midlatitude cyclones', short: 'Midlatitude cyclone',
                        rule: 'A closed ERA5 MSLP low ≤990 hPa living ≥48 h and travelling ≥1000 km, born between 25° and 70°. Scored on track error, central pressure, peak depth and bomb detection against that ERA5 track.' },
};
/* AR green (design 2026-11-08, owner request): all type colors resolve
   through CSS tokens — dual-theme values, no hardcoded hex in JS. */
const TYPE_SERIES_VAR = { heat_wave: '--series-heat', freeze: '--series-freeze',
  cold_snap: '--series-cold-snap', atmospheric_river: '--series-ar',
  tropical_cyclone: '--series-tc', flood: '--series-flood',
  midlat_cyclone: '--series-mlc' };
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function typeColor(etype) {
  return cssVar(TYPE_SERIES_VAR[etype] || '--series-unknown', '#848475');
}
/* Chips order + Gantt lanes group related types (owner 2026-11-17):
   Temperature (heat, cold snap, freeze) | Water (AR, flood) | Storms
   (tropical, midlatitude). The same order is the palette's fixed hue order —
   the identity colours were validated against THIS adjacency (both themes,
   notes/chips_design.md), so re-ordering them is a palette change, not a
   cosmetic one. */
const TYPE_ORDER = ['heat_wave', 'cold_snap', 'freeze', 'atmospheric_river',
  'flood', 'tropical_cyclone', 'midlat_cyclone'];
/* Group heads carry a visible caption in the tab row so the row reads as three
   labelled groups — Temperature | Water | Storms — not one undifferentiated
   line of pills (owner feedback 2026-11-17: the grouping was too subtle). The
   caption is a presentational span (role=none + aria-hidden), so the tablist
   still owns exactly one tab per type and the a11y tree is unchanged. */
const TYPE_GROUP_LABEL = { heat_wave: 'Temperature', atmospheric_river: 'Water',
  tropical_cyclone: 'Storms' };
/* Which grid row a chip belongs to: All travels with the Temperature row, and
   Water + Storms share the second. Captions are keyed by group name. */
const TYPE_GROUP_KEY = { Temperature: 'temperature', Water: 'water', Storms: 'storms' };
/* chip labels are short (one line in a four-across grid, not truncated); the
   full label + the one-line detection rule ride the tooltip (About copy) */
const TYPE_TAB_LABEL = { heat_wave: 'Heat wave', cold_snap: 'Cold snap', freeze: 'Freeze',
  atmospheric_river: 'Atm. river', flood: 'Flood', tropical_cyclone: 'Trop. cyclone',
  midlat_cyclone: 'Midlat. cyclone' };
/* monoline glyphs — identity never colour alone (dataviz): every lane reads
   by shape at 15 px, the series hue is only the third channel */
const TYPE_ICONS = {
  all: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 9.5h17M3.5 14.5h17"/><path d="M12 3.5a13 13 0 0 1 0 17M12 3.5a13 13 0 0 0 0 17"/>',
  heat_wave: '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7"/>',
  cold_snap: '<path d="M12 3v9.5"/><path d="m8.4 8.6 3.6 3.9 3.6-3.9"/><path d="M4.5 18h15"/>',
  freeze: '<path d="M12 3v18"/><path d="m4.2 7.5 15.6 9"/><path d="m19.8 7.5-15.6 9"/><path d="m9.6 5.2 2.4 2 2.4-2"/><path d="m9.6 18.8 2.4-2 2.4 2"/>',
  atmospheric_river: '<path d="M2.5 7c2.8-2.8 5.6 2.4 8.4.2s5.6 2.4 8.4-.2"/><path d="M3.5 17.5h12"/><path d="m12.6 14.4 3.5 3.1-3.5 3.1"/>',
  flood: '<path d="M3 8c2.3-2.2 4.7 2 7 0s4.7 2.2 7 0"/><path d="M3 12.7c2.3-2.2 4.7 2 7 0s4.7 2.2 7 0"/><path d="M3 17.4c2.3-2.2 4.7 2 7 0s4.7 2.2 7 0"/>',
  /* Hurricane symbol (owner 2026-11-18): the two-arm NHC swirl with a filled
     eye — not wind lines, which read as the AR river glyph one chip away.
     Two strokes only, so it stays legible at the 15 px chip size. */
  tropical_cyclone: '<path d="M12.6 10.9c.6-4 3.4-6.6 7.2-6.7"/><path d="M19.8 4.2c.6 1.2.6 2.5-.1 3.5"/><path d="M11.4 13.1c-.6 4-3.4 6.6-7.2 6.7"/><path d="M4.2 19.8c-.6-1.2-.6-2.5.1-3.5"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  /* Frontal low: an open circulation (the broken isobar) around a bold L —
     a low-pressure centre mark, its own shape, not a smaller hurricane. */
  midlat_cyclone: '<path stroke-width="2" d="M18.4 6.4a8.6 8.6 0 1 0-.2 11.4"/><path stroke-width="2" d="M10.2 8.2v7.4h5.2"/>',
};
function typeIcon(etype) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + (TYPE_ICONS[etype] || '') + '</svg>';
}
/* one chip component for every static type tag (lane headers, verdict strip,
   chart captions): soft lane ground + ink text + lane glyph. */
function typeChipHtml(etype) {
  const tm = TYPE_META[etype] || {};
  return `<span class="type-chip" data-etype="${esc(etype)}">${typeIcon(etype)}`
    + `<span>${esc(tm.short || etype)}</span></span>`;
}

/* Owner 2026-11-07: empty lanes are legitimate (freezes may be empty at p15).
   Criteria wording mirrors ewb_live/events/temperature.py + ar.py thresholds —
   keep in sync if detection parameters change. */
const TYPE_CRITERIA = {
  heat_wave: 'Every 6-hourly step of a UTC day has 2 m temperature above the 85th percentile of the ERA5 1990-2019 climatology (per day of year and hour), over land. A region ≥50,000 km² becomes an event once its footprint reaches 200,000 km², and ends when the cells that have counted every day since it began hold under 100,000 km² (at least 3 days). The map shades only cells that reach the same 3-consecutive-day minimum (consecutive COMPLETED days; cells below it are blank because they can never become an event), dithered = today so far on an already-qualifying cell.',
  freeze: 'Every 6-hourly step of a UTC day has 2 m temperature below the 15th percentile of the ERA5 1990-2019 climatology (per day of year and hour) and at or below 0 °C, over land. A region ≥50,000 km² becomes an event once its footprint reaches 200,000 km², and ends when the cells that have counted every day since it began hold under 100,000 km² (at least 3 days). On the map the freeze layer shades cells from their first consecutive COMPLETED day (cold runs are short, so 1–2-day cells stay visible for context; a freeze still needs 3+ days), dithered = today so far on a cell with at least one completed day.',
  cold_snap: 'Every 6-hourly step of a UTC day has 2 m temperature below the 15th percentile of the ERA5 1990-2019 climatology (per day of year and hour), over land — the freeze rule without its ≤0 °C requirement. A region ≥50,000 km² becomes an event once its footprint reaches 200,000 km², and ends when the cells that have counted every day since it began hold under 100,000 km² (at least 3 days). Where a freeze covers the same window and box the freeze is published instead of the cold snap, so the two never double-count a spell. The cold-snap lane has no raw layer of its own — it reads the same P15 layer as freeze, shading cells from their first consecutive COMPLETED day (dithered = today so far on a cell with at least one completed day).',
  atmospheric_river: 'Integrated water-vapour transport peaking at ≥400 kg m⁻¹ s⁻¹ and reaching land.',
  // observation lane (PARDIG rain by default — AI-estimated, not ERA5): published, never scored — EWB has no flood benchmark
  flood: 'Rain from Google PARDIG-NRT — an AI-estimated precipitation product (CC BY 4.0), 15-minute instantaneous rates summed to a daily total and averaged up onto the ERA5 0.25° land lattice; GPM IMERG remains a supported fallback source. A cell exceeds when its daily total or its trailing 72 h accumulation reaches the 95th percentile of that calendar day in the ERA5 1990–2019 wet-day climatology AND at least 20 mm/day (or 50 mm per 72 h) — the floor is mandatory because the rain product and ERA5 are different instruments. Cells with no ERA5 percentile (hyper-arid) can never exceed, ocean cells are excluded, and a day whose frames are incomplete is dropped rather than read as dry. A region ≥10,000 km² becomes an event once its footprint reaches 50,000 km² and ends under 10,000 km². Floods are scored against that same AI-estimated rain, GraphCast only (see About).',
  tropical_cyclone: 'One IBTrACS best-track storm = one event: at least 3 track fixes at tropical-storm force (≥34 kt sustained) spanning at least 12 hours, extratropical/mixed fixes excluded. The wind is the archive’s 1-minute best estimate (USA/JTWC-informed) and one storm never mixes averaging periods — a storm only an RSMC reports is published on that agency’s basis and says so (details.wind_basis). Peak value is the strongest sustained wind in the tropical-storm window (kt); the detail view draws the full track, coloured by intensity. Best-track observations, so there is no model scorecard for this lane yet.',
  midlat_cyclone: 'A closed low in 6-hourly ERA5 mean sea level pressure: a centre that is the lowest pressure within 6° with a +3 hPa isobar closing around it inside 6°, tracked forward while it stays within 6° per step (gaps up to 12 h are bridged). It becomes an event when it lives ≥48 h, travels ≥1000 km, reaches ≤990 hPa, was born between 25–70° and under 500 m elevation, and did not start as a tropical cyclone. Peak value is the lowest central pressure (hPa); a bomb tag marks a fall of ≥24 hPa in 24 h at 60° latitude. Forecasts are scored against this ERA5 track: position, along/cross-track, central pressure, peak depth and bomb detection.',
};
/* ------------------------------------------------------------------ */
/* EMPTY-LANE COPY — ONE PLACE (owner rule 2026-11-17 / mlc-fix 2026-11-18)     */
/* Every lane's "nothing here" sentence is written here and read from here:     */
/* the event list, the scorecard lane blocks and the lane tabs/chips (ne-chips) */
/* must not duplicate this copy — call emptyLaneLine(type).                     */
/* ------------------------------------------------------------------ */

/* A lane that can fail must never read as an empty sky. meta.lane_status is
   written by pipeline.py from each lane's own report (ewb_live/events/__init__.py
   → mlc.lane_status): 'failed' = the scan ran and broke, 'no-data' = the source
   has nothing for the window, 'ok' = the zero is genuine. */
function laneStatusNote(t) {
  const st = (state.meta && state.meta.lane_status) || null;
  const s = st && st[t];
  if (!s || (s.status !== 'failed' && s.status !== 'no-data')) return null;
  const label = TYPE_META[t] ? TYPE_META[t].label : t;
  const why = s.reason ? ` — ${s.reason}` : '';
  return s.status === 'failed'
    ? `${label}: lane failed, so this window has no numbers${why}.`
    : `${label}: not computed, the source had nothing for this window${why}.`;
}

const emptyLaneLine = (t) => {
  const broken = t === 'all' ? null : laneStatusNote(t);
  if (broken) return broken;
  return t === 'all'
    ? 'No events met the criteria this window'
    : t === 'tropical_cyclone' ? tcEmptyNote()
    : `No ${TYPE_META[t].label.toLowerCase()} met the criteria this window`;
};

/* Honest zero for the tropical-cyclone lane (owner rule 2026-11-17): a bare
   "0" reads as "the sky was empty", but IBTrACS publishes with a lag of days
   to weeks, and a data release can also predate the lane itself. meta.json
   carries the truth (pipeline.py: meta.ibtracs) — say which it is. */
function tcEmptyNote() {
  const m = state.meta || {};
  const ib = m.ibtracs;
  if (!('ibtracs' in m)) {
    return 'Tropical cyclones are not published in this data release yet — the IBTrACS lane goes live with a future weekly build.';
  }
  if (!ib) {
    return 'The IBTrACS best-track archive was unreachable when this data was built, so tropical cyclones could not be checked this window.';
  }
  const d = String(ib.latest_fix || '').slice(0, 10);
  return `No qualifying storm in the IBTrACS archive through ${d} — best-track publication lags real time (the archive runs ~${ib.lag_days} days behind today).`;
}

/* TC lane, scored (WP1, notes/scoring_design.md 8.3). The IBTrACS archive lags real time by
   days, so the note still says how many storms it holds and which systems are too new to
   judge (ne-fix-tc 2026-11-19, audit TC-3/4), and now also states what is verified: inits are
   scored from the first >=34 kt fix, earlier ones are shown as not verified (owner decision
   2026-11-21), and a lost track is a miss, never an imputed error. `n` is the published count
   IN THE CURRENT VIEW, so it is quoted as events here. */
function trackLaneNote(etype, n) {
  const p = el('p', 'lane-note');
  const tv = ((state.meta || {}).track_verification || {})[etype];
  if (etype === 'midlat_cyclone') {
    p.textContent = mlcLaneNote(n, tv);
    return p;
  }
  const ib = (state.meta || {}).ibtracs;
  let s = `Scored against the IBTrACS best track (provisional until the archive catches up)`
    + `${n ? ` \u00b7 ${n} storm${n === 1 ? '' : 's'} in this view` : ''}. `
    + 'Track error is the great-circle distance to the best-track position; along-track is positive when '
    + 'the forecast is ahead of the storm, cross-track positive to its right. A forecast that loses the '
    + 'storm counts toward the miss rate and gets no error; cells above 50% misses are greyed. '
    + 'Central pressure is a signed bias at 0.25\u00b0 resolution, not a skill ranking.';
  /* the landfall block only exists when the build carries its score rows */
  if (scRows().some((r) => r.event_type === etype && r.metric.indexOf('landfall_') === 0)) {
    s += ' The landfall block is scored by EWB\u2019s own landfall classes: only storms with an observed '
      + 'landfall are included, a model that forecasts no paired landfall counts as a miss, and those '
      + 'cells rest on few storms (n is shown in the tooltip).';
  }
  if (tv && Number.isFinite(tv.n_inits_not_verified)) {
    s += ` Verification starts at each storm\u2019s first \u2265${tv.min_wind_kt} kt fix: `
      + `${tv.n_inits_verified} 00/12 UTC inits verified, ${tv.n_inits_not_verified} earlier ones not verified `
      + '(a pre-storm disturbance is not a missed forecast).';
  }
  if (ib && Number.isFinite(ib.storms_in_window)) {
    const d = String(ib.latest_fix || '').slice(0, 10);
    s += ` The archive holds ${ib.storms_in_window} storm${ib.storms_in_window === 1 ? '' : 's'} for this window through ${d}`
      + ` (~${ib.lag_days} days behind today).`;
    if (Number.isFinite(ib.pending) && ib.pending > 0) {
      const who = (ib.pending_names || []).filter(Boolean).join(', ');
      s += ` ${ib.pending} system${ib.pending === 1 ? '' : 's'} still live at the archive\u2019s edge`
        + `${who ? ` (${who})` : ''} cannot be judged until the next publication.`;
    }
  }
  p.textContent = s;
  return p;
}

/* MLC lane, scored: truth is the ERA5 MSLP track each low was detected on, so the same
   track-error/along/cross/miss definitions apply, plus peak depth (signed) and the two
   bomb-detection rates. The tracker-free metrics have no miss rate and are never greyed. */
function mlcLaneNote(n, tv) {
  let s = 'Scored against the ERA5 MSLP track each low was detected on '
    + '(0.25\u00b0, the same grid as the models, so central pressure has no resolution floor)'
    + `${n ? ` \u00b7 ${n} storm${n === 1 ? '' : 's'} in this view` : ''}. `
    + 'Track error is the great-circle distance to that position; along-track is positive when '
    + 'the forecast is ahead of the low, cross-track positive to its right. A forecast that loses '
    + 'the low counts toward the miss rate and gets no error; cells above 50% misses are greyed. '
    + 'Peak depth compares the forecast\u2019s lowest pressure within 8\u00b0 of the observed centre '
    + 'with the ERA5 central pressure (positive = forecast too shallow); it needs no tracking, so '
    + 'it has no miss rate. Bombs detected is the share of observed 24-h bomb-rate deepenings '
    + '(Sanders\u2013Gyakum, scaled by latitude) that the forecast also showed; false alarms is the '
    + 'share of non-bomb windows where it showed one.';
  if (tv && Number.isFinite(tv.n_inits_verified)) {
    s += ` ${tv.n_inits_verified} 00/12 UTC inits verified from each low\u2019s first ERA5 fix.`;
  }
  return s;
}

/* FLOOD lane, scored (flood lane, GraphCast only): the truth is Google PARDIG — an
   AI-estimated precipitation product, not observed rain — and the events themselves are
   defined from that same product, so every number here is conditional on a truth
   exceedance existing (owner rule: the table says it beside itself). GraphCast only: the
   store carries no precipitation for the other models and ECMWF tp is not wired, so the
   lane has no baseline and no skill colour. meta.flood_scoring (pipeline) may override
   the truth label and the unsupported-model list; `n` is the published count here. */
function floodLaneNote(etype, n) {
  const p = el('p', 'lane-note');
  const fs = (state.meta || {}).flood_scoring || {};
  const truth = fs.truth_label || 'vs AI-estimated precipitation (Google PARDIG)';
  let s = `Scored ${truth} \u00b7 GraphCast only`
    + `${n ? ` \u00b7 ${n} flood${n === 1 ? '' : 's'} in this view` : ''}. `
    + 'PARDIG is a model estimate of rain \u2014 not observed rain \u2014 and the events are '
    + 'defined by the same product. '
    + 'Hit/miss is the daily exceedance of the event\u2019s own threshold (ERA5 wet-day p95, '
    + 'at least 20 mm/day) per land cell and day; CSI = hits/(hits+misses+false alarms); '
    + 'FSS compares exceedance fractions in 3x3 / 5x5 cell neighbourhoods; area bias and '
    + 'peak error are forecast minus truth in mm over the event footprint (negative = '
    + 'forecast too dry); scores are conditional on a truth exceedance existing. '
    + 'No baseline model scores flood, so cells carry no skill colour. '
    + 'The ECMWF-initialised ' + displayModelName('graphcast') + ' has no 1 d cell (its first 6 h of precipitation are fill); '
    + `graphcast_gfs inits missing from the store are counted as no data, not rescued from a 12Z run. `;
  s += (Array.isArray(fs.unsupported_models) && fs.unsupported_models.length)
    ? `No flood rows for ${fs.unsupported_models.map((m) => displayModelName(m)).join(', ')}.`
    : 'Every other model carries no precipitation in the store.';
  p.textContent = s;
  return p;
}

/* ------------------------------------------------------------------ */
/* HUMAN MODEL NAMES (ux contract §2) — written once, read everywhere a  */
/* model is printed: scorecard tables, the KPI leader strip, the model    */
/* pills, Plotly legends, the per-event mini table, tooltips and About.   */
/* The snake_case ids stay in the data (state.allModels, cell.dataset     */
/* .model, score rows) and never reach the page as text.                 */
/* ------------------------------------------------------------------ */
const MODEL_NAME = {
  ifs_hres: 'IFS HRES', aifs: 'AIFS', aifs_single: 'AIFS Single',
  pangu_weather: 'Pangu-Weather', fourcastnet: 'FourCastNet',
  fourcastnet_v2: 'FourCastNet v2', graphcast: 'GraphCast',
  aurora: 'Aurora', aigfs: 'AIGFS',
};
const GFS_SUFFIX = '_gfs';
const gfsForced = (id) => String(id || '').toLowerCase().endsWith(GFS_SUFFIX);
function displayModelName(id) {
  const raw = String(id == null ? '' : id);
  if (!raw) return '';
  const forced = gfsForced(raw);
  const base = forced ? raw.slice(0, -GFS_SUFFIX.length) : raw;
  let name = MODEL_NAME[base];
  if (!name) {
    name = base.split(/[_\s]+/).filter(Boolean)
      .map((w) => (/^v?\d+$/.test(w) ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1))).join(' ');
  }
  return forced ? `${name} (GFS)` : name;
}
/* HTML form: the GFS forcing rides a badge instead of prose. */
function displayModelHtml(id) {
  const raw = String(id == null ? '' : id);
  const forced = gfsForced(raw);
  return esc(displayModelName(forced ? raw.slice(0, -GFS_SUFFIX.length) : raw))
    + (forced ? ' <span class="m-tag">GFS</span>' : '');
}
window.displayModelName = displayModelName;
window.displayModelHtml = displayModelHtml;

const MODEL_COLORS = {
  ifs_hres: '#8f9bb3', graphcast: '#4f8ff7', pangu_weather: '#21b8a0',
  aifs: '#e8a33d', aurora: '#a97df2', fourcastnet: '#ef6f8b',
  aifs_single: '#e8a33d', fourcastnet_v2: '#ef6f8b', aigfs: '#d9c44a',
};
const EXTRA_COLORS = ['#d9c44a', '#56b4e9', '#cc79a7', '#7f8c2d', '#e08214', '#6a3d9a'];

const FALLBACK_METRICS = {
  rmse_k:                  { label: 'RMSE', units: 'K', higher_is_better: false },
  mae_k:                   { label: 'MAE', units: 'K', higher_is_better: false },
  tmax_mae_k:              { label: 'TMAX MAE', units: 'K', higher_is_better: false },
  tmin_mae_k:              { label: 'TMIN MAE', units: 'K', higher_is_better: false },
  onset_error_days:        { label: 'Onset error', units: 'days', higher_is_better: false },
  duration_error_days:     { label: 'Duration error', units: 'days', higher_is_better: false },
  ivt_rmse_kg_m_s:         { label: 'IVT RMSE', units: 'kg m⁻¹ s⁻¹', higher_is_better: false },
  csi:                     { label: 'CSI', units: '', higher_is_better: true },
  spatial_displacement_km: { label: 'Spatial displacement', units: 'km', higher_is_better: false },
  onset_lead_hours:        { label: 'Early signal (onset lead)', units: 'h', higher_is_better: true },
  /* EWB-native metric ids (real pipeline writes extremeweatherbench names) */
  RootMeanSquaredError:            { label: 'RMSE', units: 'K', higher_is_better: false },
  MeanAbsoluteError:               { label: 'MAE', units: 'K', higher_is_better: false },
  MeanSquaredError:                { label: 'MSE', units: 'K²', higher_is_better: false },
  MaximumMeanAbsoluteError:        { label: 'Peak-max MAE', units: 'K', higher_is_better: false },
  MinimumMeanAbsoluteError:        { label: 'Min MAE', units: 'K', higher_is_better: false },
  MaximumLowestMeanAbsoluteError:  { label: 'Lowest-max MAE', units: 'K', higher_is_better: false },
  DurationMeanError:               { label: 'Duration error', units: 'h', higher_is_better: false, dir: 'zero' },
  MeanError:                       { label: 'Bias', units: 'K', higher_is_better: false, dir: 'zero' },
  CriticalSuccessIndex:            { label: 'Critical success index', units: '', higher_is_better: true },
  spatial_displacement:            { label: 'Spatial displacement', units: 'km', higher_is_better: false },
  EarlySignal:                     { label: 'Early signal', units: '', higher_is_better: true },
  Accuracy:                        { label: 'Accuracy', units: '', higher_is_better: true },
  /* track lanes (notes/scoring_design.md section 6): signed components are bias-to-zero */
  TrackPositionError:              { label: 'Track error', units: 'km', higher_is_better: false },
  AlongTrackError:                 { label: 'Along-track', units: 'km', higher_is_better: false, dir: 'zero' },
  CrossTrackError:                 { label: 'Cross-track', units: 'km', higher_is_better: false, dir: 'zero' },
  CentralPressureError:            { label: 'Central pressure', units: 'hPa', higher_is_better: false, dir: 'zero' },
  TrackMiss:                       { label: 'Miss rate', units: '', higher_is_better: false },
  /* landfall scores (EWB's landfall classes; only storms with an observed landfall).
     The three value metrics are greyed by `landfall_miss`, the same 50 % rule. */
  landfall_displacement:           { label: 'Landfall position', units: 'km', higher_is_better: false },
  landfall_time_me:                { label: 'Landfall timing', units: 'h', higher_is_better: false, dir: 'zero' },
  landfall_intensity_mae:          { label: 'Landfall intensity', units: 'hPa', higher_is_better: false },
  landfall_miss:                   { label: 'Landfall miss rate', units: '', higher_is_better: false },
  /* MLC lane extras: peak depth is signed (zero is ideal); the bomb metrics are 0/1 rates */
  PeakDepthError:                  { label: 'Peak depth', units: 'hPa', higher_is_better: false, dir: 'zero' },
  BombDetected:                    { label: 'Bombs detected', units: '', higher_is_better: true },
  BombFalseAlarm:                  { label: 'Bomb false alarms', units: '', higher_is_better: false },
  /* flood lane (GraphCast-only precipitation scores): 0..1 exceedance scores and signed
     mm errors — the signed pair is bias-to-zero, exactly like the TC components */
  ProbabilityOfDetection:          { label: 'Hit rate (POD)', units: '', higher_is_better: true },
  FalseAlarmRatio:                 { label: 'False alarm ratio', units: '', higher_is_better: false },
  FractionsSkillScore:             { label: 'Fractions skill score (3x3)', units: '', higher_is_better: true },
  FractionsSkillScore5:            { label: 'Fractions skill score (5x5)', units: '', higher_is_better: true },
  PrecipAreaBias:                  { label: 'Area rain bias', units: 'mm', higher_is_better: false, dir: 'zero' },
  PeakPrecipError:                 { label: 'Peak rain error', units: 'mm', higher_is_better: false, dir: 'zero' },
};
/* leads shown per track lane (D8): the store keeps every 6-hourly lead. MLC publishes on the
   1-4 d window; flood scores on the same 1-5 d window as the TC lane (D8). */
const TYPE_LEADS = { tropical_cyclone: [24, 48, 72, 96, 120], midlat_cyclone: [24, 48, 72, 96],
                     flood: [24, 48, 72, 96, 120] };
function leadsForType(etype, leads) {
  const want = TYPE_LEADS[etype];
  if (!want) return leads;
  const have = want.filter((h) => state.allLeads.includes(h));
  return have.length ? have : leads;
}
/* Track lanes rank models on the track error only: the signed components are bias-to-zero
   (a ratio of tiny biases is not a skill) and the miss rate is read beside the error (D5). */
function skillRanked(etype, met) { return !TYPE_LEADS[etype] || met === 'TrackPositionError'; }
/* D5: a track-error cell whose model lost more than half its samples at that lead is greyed, and a
   greyed cell feeds neither the verdict banner, the model order nor the Mean colour.
   The landfall value metrics use the same rule against their own miss rate (landfall_miss);
   a miss-rate cell IS the evidence and is never greyed. */
const LANDFALL_VALUE_METRICS = ['landfall_displacement', 'landfall_time_me', 'landfall_intensity_mae'];
/* which 0/1 miss metric greys this one; null for the miss-rate cells themselves */
function missSource(met) {
  if (LANDFALL_VALUE_METRICS.includes(met)) return 'landfall_miss';
  return met === 'TrackMiss' || met === 'landfall_miss' ? null : 'TrackMiss';
}
function missGreyed(idx, etype, met, lead, model) {
  if (!TYPE_LEADS[etype] || TRACKER_FREE_METRICS.has(met)) return false;
  const src = missSource(met);
  if (!src) return false;
  const mc = (idx.get(`${etype}|${src}|${lead}`) || {})[model];
  return !!(mc && Number.isFinite(mc.mean) && mc.mean > MISS_GREY);
}
/* D5: a cell whose miss rate is above this is greyed (its error rests on the storms the model kept) */
const MISS_GREY = 0.5;
/* one order for both track lanes: metricsFor() keeps only what the lane scores, so the
   landfall group (TC only) and the MLC extras never show in each other's table */
const TRACK_METRIC_ORDER = ['TrackPositionError', 'TrackMiss', 'AlongTrackError', 'CrossTrackError', 'CentralPressureError',
  'landfall_displacement', 'landfall_time_me', 'landfall_intensity_mae', 'landfall_miss',
  'PeakDepthError', 'BombDetected', 'BombFalseAlarm'];
/* 0/1 rates shown as percent (the miss rates and the MLC bomb rates) */
const RATE_METRICS = new Set(['TrackMiss', 'landfall_miss', 'BombDetected', 'BombFalseAlarm']);
/* metrics that do not depend on the tracker keeping the low/storm: the 50%-miss grey rule
   and the "tracked N of M samples" tooltip row never apply to them */
const TRACKER_FREE_METRICS = new Set(['PeakDepthError', 'BombDetected', 'BombFalseAlarm']);
const BINARY_METRICS = new Set(['csi', 'CriticalSuccessIndex', 'Accuracy', 'EarlySignal',
  /* flood lane: the five 0..1 exceedance scores print at 2 decimals */
  'ProbabilityOfDetection', 'FalseAlarmRatio', 'FractionsSkillScore', 'FractionsSkillScore5']);

/* ------------------------------------------------------------------ */
/* state                                                               */
/* ------------------------------------------------------------------ */
const state = {
  meta: null, events: [], scores: [], scorecard: [], pairStatus: {},
  tab: 'all',                      // 'all' | event_type
  view: 'events',                  // 'events' | 'scorecard' — the two page views (#view=)
  region: 'global',                // regions.json key (OWB-style Region dropdown)
  regionData: null,                // parsed data/results/scorecard_by_region.json (null = no region data)
  regionHit: null,                 // in-flight lazy load promise of that file (boot/week-switch guard)
  models: new Set(),               // visible models
  leads: new Set(),                // selected leads (scorecard)
  allModels: [], allLeads: [],
  baseline: 'ifs_hres',            // scorecard baseline model (meta/scorecard may override)
  week: null,                      // null = current results; 'YYYY-WW' = history snapshot
  weeks: [],                       // available history weeks (newest first)
  chartMode: 'abs',
  themePref: 'auto',               // auto | light | dark
  selectedEvent: null,
  lastViewedEvent: null,   // box highlighted on return to the catalog list
  modelOrder: [],                  // global model order from last renderScorecard
  search: '',
  evScope: 'all',                  // 'all' | 'map' — does the list follow the map frame?
  leadsMore: false,                // lead pill row disclosure
  scMore: false,                   // scorecard: show all metrics (hero shows headline only)
  detailLead: null,                // lead currently shown by the detail viz
  detailModels: null,              // model-map selection for the detail viz
  detailLayers: null,              // serialized layer state (#lyr=) for the detail viz
  detailDay: null,                 // valid-time index (#day=) for the detail viz
  detailChartsPending: null,       // deferred Plotly build until <details> opens
  skillDirty: false,               // skill charts skipped while fold collapsed
};

const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function leadLabel(h) {
  if (h < 24) return h + 'h';
  const d = h / 24;
  return Number.isInteger(d) ? d + 'd' : (Math.round(d * 10) / 10) + 'd';
}

/* Standard lead bins. The pipeline emits leads on 6 h steps (0..252 h); the
   dashboard bins every lead to the nearest standard slot (6/12/18 h + whole
   days 1d..10d) so scorecard columns line up across models and chart axes are
   readable. 0 h falls into the 6 h bin; >240 h clamps to 10 d. */
const LEAD_BINS = [6, 12, 18, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240];
const DEFAULT_LEAD_BINS = [24, 72, 120, 168, 240];   // 1/3/5/7/10 d (hero leads; rest behind 'more')
function binLead(h) {
  if (!Number.isFinite(h)) return null;
  let best = LEAD_BINS[0];
  for (const b of LEAD_BINS) if (Math.abs(h - b) < Math.abs(h - best)) best = b;
  return best;
}
/* merge rows that collapsed into the same bin: mean of means, max n_events */
function mergeBinned(rows, valueKeys, nKey, idKey) {
  /* G-43: a column labelled 1 d must show the 24 h value. Every standard bin is a multiple of
     6 h that the pipeline emits, so use the EXACT row whose lead == bin (its mean, CI and n all
     come from that one row). Only when no exact row exists do we fall back to an n-weighted
     mean of the neighbouring leads, with the CI and skill dropped (they would belong to a
     different row). The old unweighted mean-of-means blended 12/18 h-offset rows of 1–4 events
     into the headline (34/90 heat cells >25 % off). */
  const byKey = new Map();
  for (const r of rows) {
    // >240 h tail rows (246/252 h) are an upstream artefact (peak-max MAE ≈5× the 9 d
    // value): drop them rather than folding them into the 10 d bin (design #29)
    if (Number.isFinite(r.lead_time_hours) && r.lead_time_hours > 240) continue;
    const lead = binLead(r.lead_time_hours);
    const key = `${r[idKey]}|${r.metric}|${lead}|${r.model}`;
    let g = byKey.get(key);
    if (!g) { g = { lead, rows: [] }; byKey.set(key, g); }
    g.rows.push(r);
  }
  return [...byKey.values()].map((g) => {
    const exact = g.rows.find((r) => r.lead_time_hours === g.lead);
    if (exact) return Object.assign({}, exact, { lead_time_hours: g.lead });
    const out = Object.assign({}, g.rows[0], { lead_time_hours: g.lead });
    for (const k of valueKeys) {
      let sw = 0, sv = 0;
      for (const r of g.rows) {
        if (!Number.isFinite(r[k])) continue;
        const w = nKey && Number.isFinite(r[nKey]) && r[nKey] > 0 ? r[nKey] : 1;
        sw += w; sv += w * r[k];
      }
      out[k] = sw ? sv / sw : null;
    }
    if (nKey) {
      const ns = g.rows.map((r) => r[nKey]).filter(Number.isFinite);
      out[nKey] = ns.length ? Math.max(...ns) : null;
    }
    if (g.rows.length > 1) {                // merged cell: CI / skill of any one row would be wrong
      for (const k of ['ci_lo', 'ci_hi', 'rel']) if (k in out) out[k] = null;
    }
    return out;
  });
}
function modelColor(m, i) {
  return MODEL_COLORS[m] || EXTRA_COLORS[(i || 0) % EXTRA_COLORS.length];
}
function metricMeta(id) {
  const fromMeta = state.meta && state.meta.metrics && state.meta.metrics[id];
  const fb = FALLBACK_METRICS[id] || { label: id.replace(/_/g, ' '), units: '', higher_is_better: false };
  const m = Object.assign({}, fb, fromMeta || {});
  /* A dimensionless metric publishes the unit '1'; printed after a value it reads
     as a second number ("0.37 1"), so a unitless metric carries no unit text. */
  if (m.units === '1' || m.units === '-') m.units = '';
  return m;
}
function fmtVal(metricId, v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const u = metricMeta(metricId).units;
  if (BINARY_METRICS.has(metricId)) return v.toFixed(2).replace('-', '\u2212');
  if (RATE_METRICS.has(metricId)) return Math.round(v * 100) + '%';
  const a = Math.abs(v);
  let s;
  if (a >= 100) s = v.toFixed(0);
  else if (a >= 10) s = v.toFixed(1);
  else s = v.toFixed(2);
  return s.replace('-', '\u2212');   // U+2212, as in the event story line
}
/* Dates arrive as plain 'YYYY-MM-DD' (parsed as UTC) or bare ISO date-times
   (which JS would parse as LOCAL). Normalise bare date-times to UTC. */
function parseISO(iso) {
  if (!iso) return null;
  const s = /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(iso) ? iso + 'Z' : iso;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}
function fmtDate(iso) {
  if (!iso) return '—';
  const d = parseISO(iso);
  if (!d) return String(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

/* relative skill: positive = better than ifs_hres (sign-normalised).
   dir 'zero' metrics (DurationMeanError, MeanError/bias) are signed errors whose
   ideal is 0: "better" means smaller |value|, not a more-negative value. */
function relSkill(model, ref, metricId) {
  if (ref === null || ref === undefined || ref === 0 || !Number.isFinite(model)) return null;
  const mm = metricMeta(metricId);
  if (mm.dir === 'zero') return (Math.abs(ref) - Math.abs(model)) / Math.abs(ref) * 100;
  return mm.higher_is_better ? (model - ref) / Math.abs(ref) * 100
                             : (ref - model) / Math.abs(ref) * 100;
}
/* true when the pipeline-supplied skill cannot be trusted for this metric
   (pipeline uses signed semantics; wrong for zero-bias errors) */
function relUntrusted(metricId) { return metricMeta(metricId).dir === 'zero'; }
function cellColor(rel) {
  if (rel === null || !Number.isFinite(rel) || Math.abs(rel) < 0.05) return '';
  const t = Math.min(1, Math.log10(1 + Math.min(Math.abs(rel), 50)) / Math.log10(51));
  const a = (0.07 + 0.45 * t).toFixed(3);
  return `rgba(var(${rel > 0 ? '--better' : '--worse'}), ${a})`;
}

/* ------------------------------------------------------------------ */
/* data loading (per-file fallback: real results → sample; week picker) */
/* ------------------------------------------------------------------ */
async function fetchJSON(url) {
  try {
    const r = await fetch(url, { cache: 'no-cache' });
    if (r.status === 204) return null;   // optional file absent (serve.py's clean "no")
    if (!r.ok) return null;
    return { json: await r.json(), sample: r.headers.get('X-EWB-Data') === 'sample' };
  } catch (e) { return null; }
}
/* ?data=mini|mini-sparse — frozen interim snapshot with synthetic score
   cells (meta.mini), for review-time view exercising. No sample fallback:
   an explicit mini request must never silently degrade to sample data. */
const DATA_DIR = (() => {
  const d = new URLSearchParams(location.search).get('data');
  return d === 'mini' || d === 'mini-sparse' || d === 'mini-nofreeze' || d === 'mini-am' ? `data/results/${d}` : 'data/results';
})();

async function loadJSON(name, week) {
  if (week) {  // history snapshot: no sample fallback
    const hit = await fetchJSON(`data/results/history/${encodeURIComponent(week)}/${name}`);
    return hit ? { json: hit.json, dir: `data/results/history/${week}` } : null;
  }
  const dirs = DATA_DIR === 'data/results'
    ? ['data/results', 'data/results/sample'] : [DATA_DIR];
  for (const dir of dirs) {
    const hit = await fetchJSON(`${dir}/${name}`);
    if (hit) return { json: hit.json, dir: hit.sample ? 'data/results/sample' : dir };
  }
  return null;
}
/* pair_status reasons are curated vocabulary ('n/a (no 2 m temperature)'); anything that
   looks like a raw library message (long, quoted identifiers, '=') reads as a calm generic
   line instead — never an exception text on a public page (G-30) */
function friendlyReason(r) {
  const t = String(r || '').trim();
  if (!t) return '';
  return (t.length > 90 || /['"`]\w+['"`]|=|Traceback|Error\b/.test(t)) ? 'no forecast for this run' : t;
}
function asRows(json) { return Array.isArray(json) ? json : (json && json.rows) || []; }
const firstDefined = (...xs) => xs.find((x) => x !== undefined && x !== null) ?? null;

/* Normalise a scorecard row to one internal shape. The sample uses
   mean_value/relative_skill_vs_ifs_pct; the real pipeline (pipeline.py::
   build_scorecard) writes mean/skill_vs_ifs_pct and null lead_time_hours for
   event-level metrics (DurationMeanError, EarlySignal). Accept both. */
function normScRow(r) {
  return {
    event_type: r.event_type,
    metric: r.metric,
    lead_time_hours: Number.isFinite(r.lead_time_hours) ? r.lead_time_hours : null,
    model: r.model,
    mean_value: firstDefined(r.mean_value, r.mean),
    ci_lo: firstDefined(r.ci95_lo, r.ci_lo),
    ci_hi: firstDefined(r.ci95_hi, r.ci_hi),
    n_events: Number.isFinite(r.n_events) ? r.n_events : null,
    n_samples: Number.isFinite(r.n_samples) ? r.n_samples : null,
    rel: firstDefined(r.relative_skill_vs_ifs_pct, r.skill_vs_ifs_pct),
  };
}

async function loadWeeks() {
  const hit = await fetchJSON('data/results/weeks.json');
  const weeks = hit && hit.json && Array.isArray(hit.json.weeks) ? hit.json.weeks : [];   // `null` body must not kill boot
  state.weeks = weeks.map((w) => w.week).filter(Boolean).sort().reverse();
}

/* Region definitions: dashboard/regions.json — the single source shared with
   Python (ewb_live/regions.py). JS uses key/label/extents only; the event→region
   assignment and the scorecard slices are the data lane's (scorecard_by_region.json). */
let REGIONS = [];
const regionDef = (key) => REGIONS.find((r) => r.key === key) || null;
async function loadRegions() {
  const hit = await fetchJSON('regions.json');
  const rs = hit && hit.json && Array.isArray(hit.json.regions) ? hit.json.regions : [];
  REGIONS = rs.filter((r) => r && r.key && Array.isArray(r.lat) && Array.isArray(r.lon));
}

/* Parse the region contract: {regions:[{key,label,n_events,by_type}],
   by_region:{key:{rows}}}. Rows share scorecard.json's shape, so the same
   normalise+bin path applies. Global is deliberately absent from by_region
   (scorecard.json IS the global slice — regions-data contract). */
function parseRegionJson(hit) {
  const rj = hit && hit.json;
  if (!rj || !Array.isArray(rj.regions) || !rj.regions.some((r) => r && r.key)
      || !rj.by_region || typeof rj.by_region !== 'object') return null;
  const rows = {};
  for (const [k, v] of Object.entries(rj.by_region)) {
    rows[k] = mergeBinned(asRows(v && v.rows).map(normScRow),
      ['mean_value', 'ci_lo', 'ci_hi'], 'n_events', 'event_type');
  }
  /* Server-side assignment (ewb_live/regions.py): the authoritative event →
     region map, computed from the event's real footprint mask (>25 % overlap
     rule, owner 2026-11-10). The UI filters from THIS map so the map, list and
     tables can never disagree; the JS centroid port below is only the fallback
     for ids the file does not cover. */
  const evMap = (rj.events && typeof rj.events === 'object') ? rj.events : null;
  return { regions: rj.regions.filter((r) => r && r.key), rows,
           events: evMap, assignment: rj.assignment || null };
}

async function loadData(week) {
  const [meta, eventsSel, scores, scorecard, pairStatus] = await Promise.all([
    loadJSON('meta.json', week),
    loadJSON('events_selected.json', week),   // curated/scored set — shown by default
    loadJSON('scores.json', week),
    loadJSON('scorecard.json', week),
    loadJSON('pair_status.json', week),       // ewb per-pair bench status (reasons)
  ]);
  /* The raw candidate list is only a fallback for a build without the curated
     set, so it is fetched when it is actually needed — production ships both
     files, and probing a third every boot was a wasted request (and a logged
     404 wherever events_selected.json is the only published list). */
  const eventsAll = eventsSel ? null : await loadJSON('events.json', week);
  const events = eventsSel || eventsAll;
  if (!events && !scores && !scorecard) throw new Error('no data files found');
  state.meta = (meta && meta.json) || {};

  const scJson = scorecard && scorecard.json;
  // bin leads to standard slots and merge, so 6 h-step data lines up in columns
  state.scorecard = mergeBinned(asRows(scJson).map(normScRow), ['mean_value', 'ci_lo', 'ci_hi'], 'n_events', 'event_type');
  /* Region slices (scorecard_by_region.json, regions-data contract): ~2.1 MB on
     real data, so it loads in the BACKGROUND — the dropdown and region view
     appear when it lands (never delay first paint). Archived weeks pre-dating
     the lane 404 -> regionData stays null -> the dropdown hides itself
     (honest: no region data for this week). */
  state.regionData = null;
  {
    const p = loadJSON('scorecard_by_region.json', week);
    state.regionHit = p;
    p.then((hit) => {
      if (state.regionHit !== p) return;   // superseded by a newer week's load
      state.regionHit = null;
      state.regionData = parseRegionJson(hit);
      renderControls();                    // reveals the dropdown, re-validates state.region
      if (state.region !== 'global') renderAll();   // deep link that waited on this file
    }).catch(() => {});
  }
  state.events = asRows(events && events.json);
  if (typeof window !== 'undefined') window.__debug = { get state() { return state; } };
  state.scores = mergeBinned(asRows(scores && scores.json), ['value'], null, 'event_id');
  state.pairStatus = (pairStatus && pairStatus.json && typeof pairStatus.json === 'object')
    ? pairStatus.json : {};

  // header/baseline metadata
  state.baseline = (scJson && scJson.baseline) || state.meta.baseline_model || 'ifs_hres';

  // badges: sample data only applies to the live (non-history) view
  const usingSample = [meta, events, scores, scorecard]
    .some((x) => x && x.dir.includes('sample'));
  const isSample = !!state.meta.is_sample || (usingSample && !week);
  $('#sample-badge').classList.toggle('hidden', !isSample);
  $('#sample-badge').title = isSample
    ? 'Showing generated sample data — real scores appear automatically once the first weekly run publishes.'
    : '';
  // mini-snapshot chip: shown whenever the ?data=mini* snapshot is loaded
  const isMini = DATA_DIR !== 'data/results';
  const mb = $('#mini-badge');
  if (mb) {
    mb.classList.toggle('hidden', !isMini);
    mb.textContent = DATA_DIR.endsWith('sparse') ? 'mini · sparse' : 'mini data';
    mb.title = isMini
      ? (state.meta.mini_note || 'Frozen interim snapshot with synthetic score cells — view exercising only; viz and event list are real.')
      : '';
  }

  // models: only those actually present in the data (meta.models may list
  // registered sources that were never scored)
  const dataModels = [...new Set(state.scorecard.map((r) => r.model)
    .concat(state.scores.map((r) => r.model)))];
  const declared = Array.isArray(state.meta.models) ? state.meta.models : [];
  state.allModels = declared.filter((m) => dataModels.includes(m))
    .concat(dataModels.filter((m) => !declared.includes(m)));
  if (!state.allModels.length) state.allModels = declared;
  if (!state.allModels.includes(state.baseline) && dataModels.includes(state.baseline)) {
    state.allModels.unshift(state.baseline);
  }
  // leads: distinct binned slots actually present in the scorecard; the
  // scorecard starts on the default lead set (all bins if none have data)
  state.allLeads = [...new Set(state.scorecard.map((r) => r.lead_time_hours)
    .filter(Number.isFinite))].sort((a, b) => a - b);
  // default model set: every model with scorecard coverage (most coverage first), baseline always in
  const coverage = {};
  for (const r of state.scorecard) coverage[r.model] = (coverage[r.model] || 0) + 1;
  const covOrder = [...state.allModels].sort((a, b) => (coverage[b] || 0) - (coverage[a] || 0));
  const defModels = covOrder.filter((m) => (coverage[m] || 0) > 0);   // public leaderboard: never hide scored models by default
  if (!defModels.includes(state.baseline) && state.allModels.includes(state.baseline)) defModels.push(state.baseline);
  state.models = new Set(defModels.length ? defModels : state.allModels);
  const defLeads = DEFAULT_LEAD_BINS.filter((h) => state.allLeads.includes(h));
  state.leads = new Set(defLeads.length ? defLeads : state.allLeads);
}

/* ------------------------------------------------------------------ */
/* header + controls                                                   */
/* ------------------------------------------------------------------ */
function truthText(t) {
  if (!t) return { text: '—', title: '' };
  if (typeof t === 'string') return { text: t.replace(/_/g, ' '), title: t };
  const prim = String(t.primary || '').replace(/_/g, ' ');
  const fb = String(t.fallback || '').replace(/_/g, ' ');
  const shortP = /era5/i.test(prim) ? 'ERA5' : prim;
  const shortF = /ifs|hres/i.test(fb) ? 'IFS analysis' : fb;
  if (!prim) return { text: shortF || '—', title: t.note || '' };
  // reader-facing phrasing (design P0): "ERA5 · last ~6 days: IFS analysis"
  const text = shortF ? `${shortP} · last ~6 days: ${shortF}` : shortP;
  return { text, title: t.note || JSON.stringify(t) };
}
/* One-plain-English-sentence explainers per metric (OWB methodology-page habit:
   definition + direction + perfect value). Used as tooltips on scorecard
   headers and the mini-scorecard. */
const METRIC_BLURB = {
  RootMeanSquaredError: 'Root-mean-square error of 2 m temperature over event grids and leads. Lower is better; perfect = 0.',
  MeanAbsoluteError: 'Mean absolute error of 2 m temperature over event grids and leads. Lower is better; perfect = 0.',
  MeanSquaredError: 'Mean squared error of 2 m temperature. Lower is better; perfect = 0.',
  MaximumMeanAbsoluteError: 'Absolute error of the daily maximum temperature inside the event. Lower is better; perfect = 0.',
  MinimumMeanAbsoluteError: 'Absolute error of the daily minimum temperature inside the event. Lower is better; perfect = 0.',
  MaximumLowestMeanAbsoluteError: 'Absolute error of the lowest daily maximum inside the event (heat-wave relief). Lower is better; perfect = 0.',
  DurationMeanError: 'Error in predicted event duration (hours). 0 = perfect; positive over-predicts, negative under-predicts — closeness to zero counts, not sign.',
  MeanError: 'Mean bias in temperature (K). 0 = perfect; positive = model warmer than truth.',
  CriticalSuccessIndex: 'CSI = hits / (hits + misses + false alarms) of the event mask. Higher is better; perfect = 1.',
  Accuracy: 'Fraction of event-box pixels where forecast and observed event masks agree. Higher is better; perfect = 1.',
  spatial_displacement: 'Great-circle distance between observed and forecast event centroids (the spatial error decomposition’s displacement term). Lower is better.',
  EarlySignal: 'Share of events the forecast flags (0–1). Higher is better.',
  onset_error_days: 'Error in predicted event onset day (days). Closer to 0 is better.',
  duration_error_days: 'Error in predicted event duration (days). Closer to 0 is better.',
  ivt_rmse_kg_m_s: 'RMSE of integrated vapour transport (IVT) over the AR region. Lower is better; perfect = 0.',
  csi: 'CSI = hits / (hits + misses + false alarms) of the AR mask. Higher is better; perfect = 1.',
};
function metricBlurb(id) {
  const mm = metricMeta(id);
  const dir = mm.dir === 'zero' ? '0 is ideal (signed error).'
    : mm.higher_is_better ? 'Higher is better.' : 'Lower is better.';
  return (METRIC_BLURB[id] ? METRIC_BLURB[id] + ' ' : '') + dir;
}
/* The header "truth" is lane-aware: tropical cyclones are scored against the IBTrACS best
   track, not ERA5 (owner review 2026-11-21). One lane tab -> that lane's truth; "all types"
   -> the mix, named only when the TC lane has score rows in this build. Other lanes keep the
   run's truth_source. */
const LANE_TRUTH = { tropical_cyclone: { short: 'IBTrACS', title: 'Tropical cyclones are scored against the IBTrACS best track (provisional until the archive catches up), not ERA5.' },
  midlat_cyclone: { short: 'ERA5 track', title: 'Mid-latitude cyclones are scored against the ERA5 MSLP track each low was detected on (0.25\u00b0, same grid as the models).' },
  flood: { short: 'PARDIG (AI-estimated precip)', title: 'Floods are scored against Google PARDIG, an AI-estimated precipitation product (not observed rain), not ERA5.' } };
function laneHasScores(etype) { return state.scorecard.some((r) => r.event_type === etype); }
function tcHasScores() { return laneHasScores('tropical_cyclone'); }
/* Which non-ERA5 truths have score rows in THIS build, in the order the header
   names them (the 'all' tab lists every lane that actually carries rows). */
function truthMix() {
  const mix = [];
  if (tcHasScores()) mix.push('IBTrACS (TC)');
  if (laneHasScores('flood')) mix.push('PARDIG (flood)');
  return mix;
}
function truthShortFor(base) {
  const lt = LANE_TRUTH[state.tab];
  if (lt) return lt.short;
  if (state.tab === 'all') {
    const mix = truthMix();
    if (mix.length) return `${base} + ${mix.join(' + ')}`;
  }
  return base;
}
function truthTitleFor(base) {
  const lt = LANE_TRUTH[state.tab];
  if (lt) return lt.title;
  if (state.tab === 'all') {
    const bits = [];
    if (tcHasScores()) bits.push('tropical cyclones, which are scored against the IBTrACS best track');
    if (laneHasScores('flood')) bits.push('floods, which are scored against Google PARDIG (AI-estimated precipitation)');
    if (bits.length) return `${base} \u2014 except ${bits.join(' and ')}.`;
  }
  return base;
}
function renderHeader() {
  const m = state.meta;
  $('#m-updated').textContent = m.last_updated ? fmtDate(m.last_updated) : '\u2014';

  /* kv-line (OWB kv-row): events \u00b7 models \u00b7 window \u00b7 truth \u2014 the whole run
     summary in one tracked-label row; detail lives in tooltips and About. */
  const truth = truthText(m.truth_source);
  const days = m.window && Number.isFinite(m.window.days) ? m.window.days
    : (m.window && parseISO(m.window.start) && parseISO(m.window.end)
      ? Math.round((parseISO(m.window.end) - parseISO(m.window.start)) / 86400000) : null);
  const nModels = new Set([
    ...state.scores.map((r) => r.model),
    ...state.scorecard.map((r) => r.model),
  ].filter(Boolean)).size;
  const rosterN = Math.max(state.allModels.length,
    Array.isArray(m.models) ? m.models.length : 0);
  const truthShort = truthShortFor(truth.text.split(' \u00b7 ')[0]);
  const reg = regionEntry();
  const kv = (k, v, title) =>
    `<span class="kv"><span class="k">${k}</span>` +
    `<span class="v"${title ? ` title="${esc(title)}"` : ''}>${v}</span></span>`;
  $('#kv-line').innerHTML = [
    kv('events', String(reg ? reg.n_events : state.events.length),
      state.week ? `archived week ${state.week}` : (reg
        ? `scored events in ${reg.label} (bbox-centroid rule)`
        : 'events in the latest run window')),
    kv('models', `${nModels} scored · ${rosterN} in roster`,
      'models with score rows vs models declared for this run'),
    kv('window', days ? `${days} d` : '\u2014',
      m.window ? `${fmtDate(m.window.start)} \u2013 ${fmtDate(m.window.end)}` : ''),
    kv('truth', esc(truthShort), truthTitleFor(truth.title || truth.text)),
  ].join('');

  renderWeekPicker();
}

/* About dialog model list: name chips tagged with their source forcing. */
function renderAboutModels() {
  const host = $('#about-models');
  if (!host) return;
  // plain names only (forcing is explained once in the note below); the
  // baseline is named up front, so it isn't repeated as a chip
  host.innerHTML = `Baseline <b>${displayModelHtml(state.baseline)}</b> \u00b7 ` +
    state.allModels.filter((m) => m !== state.baseline)
      .map((m) => `<span class="chip">${displayModelHtml(m)}</span>`).join(' ');
}

function renderWeekPicker() {
  const sel = $('#week-picker');
  if (!sel) return;
  // the visible label belongs to the control: no snapshots = no row at all
  const row = sel.closest('.rail-set');
  if (!state.weeks.length) {
    sel.classList.add('hidden');
    if (row) row.classList.add('hidden');
    return;
  }
  sel.classList.remove('hidden');
  if (row) row.classList.remove('hidden');
  sel.innerHTML = '';
  const opt = (v, t) => {
    const o = document.createElement('option');
    o.value = v; o.textContent = t;
    if ((state.week || '') === v) o.selected = true;
    sel.appendChild(o);
  };
  opt('', 'current week');
  for (const w of state.weeks) opt(w, w);
  sel.onchange = () => switchWeek(sel.value || null);
}

async function switchWeek(week) {
  const prev = state.week;   // Ponytail 2026-11-08: a failed load must not leave
                             // state/URL on the failed week over previous-week data
  state.week = week;
  try { history.replaceState(null, '', week ? `?week=${encodeURIComponent(week)}` : location.pathname); }
  catch (e) { /* file:// or sandboxed */ }
  try {
    await loadData(week);
  } catch (err) {
    console.error('week switch failed', err);
    state.week = prev;
    try { history.replaceState(null, '', prev ? `?week=${encodeURIComponent(prev)}` : location.pathname); }
    catch (e) { /* file:// or sandboxed */ }
    renderWeekPicker();
    return;
  }
  closeDetail({ push: false });
  renderHeader(); renderControls(); renderAll();
  // region resets on week switch (older weeks have no region file) — the hash
  // must not keep claiming a region the scorecard no longer shows (review 2026-11-09)
  if (hashParams().region) setHashParam('region', null);
}

function restoreState() {
  try {
    const s = JSON.parse(localStorage.getItem('ewb-live-state') || '{}');
    if (s.themePref) state.themePref = s.themePref;
    if (s.tab && (s.tab === 'all' || TYPE_META[s.tab])) state.tab = s.tab;
    if (s.chartMode) state.chartMode = s.chartMode;
    state.railCollapsed = !!s.railCollapsed;
    state.theater = !!s.theater;
  } catch (e) { /* ignore */ }
}
function saveState() {
  try {   // blocked/full storage (private windows, embeds) must not break the UI
    localStorage.setItem('ewb-live-state', JSON.stringify({
      themePref: state.themePref, tab: state.tab, chartMode: state.chartMode,
      railCollapsed: !!state.railCollapsed,
      theater: !!state.theater,
    }));
  } catch (e) { /* preference just won't persist */ }
}

/* The event-type filter is the one page-wide control: it narrows the map
   (layers + boxes), the timeline, the event list and the scorecard together. */
function setTab(id) {
  state.tab = id; saveState(); renderHeader(); renderControls(); renderAll();
  if (window.MergedMap) MergedMap.onTabChange(id);
  syncUrl(false);   // the type filter is URL state, like Region and the date (2026-11-17)
}

/* tablist keyboard: roving tabindex, Arrow/Home/End move focus (manual
   activation — Enter/Space/click select; arrows alone never re-render).
   The row is content-sized and wraps (one line on a wide shell), so Up/Down move
   to the chip directly above/below by geometry — the nearest centre in the
   adjacent visual row — and stay put when there is no row that way; Left/Right
   step to the neighbouring tab. */
function tabsKeyNav(e) {
  const tabsEl = $('#type-tabs');
  if (!tabsEl.contains(e.target) || e.target.getAttribute('role') !== 'tab') return;
  const list = [...tabsEl.querySelectorAll('[role=tab]')];
  const i = list.indexOf(e.target);
  const box = (n) => n.getBoundingClientRect();
  const vertical = (dir) => {
    const me = box(list[i]), cx = me.left + me.width / 2;
    let best = null;
    for (let k = 0; k < list.length; k++) {
      const b = box(list[k]);
      if (dir > 0 ? b.top <= me.top + 4 : b.top >= me.top - 4) continue;   // same row or wrong way
      const rowGap = Math.abs(b.top - me.top);
      const d = Math.abs(b.left + b.width / 2 - cx);
      // the nearest row first, then the nearest column inside it
      if (!best || rowGap < best.rowGap - 4 || (rowGap <= best.rowGap + 4 && d < best.d)) {
        best = { k, rowGap, d };
      }
    }
    return best ? best.k : i;
  };
  let j = null;
  if (e.key === 'ArrowRight') j = (i + 1) % list.length;
  else if (e.key === 'ArrowLeft') j = (i - 1 + list.length) % list.length;
  else if (e.key === 'ArrowDown') j = vertical(1);
  else if (e.key === 'ArrowUp') j = vertical(-1);
  else if (e.key === 'Home') j = 0;
  else if (e.key === 'End') j = list.length - 1;
  if (j === null) return;
  e.preventDefault();
  list[j].focus();
}

function renderControls() {
  const tabs = $('#type-tabs');
  /* The event-type filter is the one page-wide control: it narrows the map
     (layers + boxes), the timeline, the event list and the scorecard together.
     A real tablist (owner 2026-11-17): aria-selected + aria-controls naming
     what it changes, roving tabindex, Arrow/Home/End move focus and Enter/
     Space select — manual activation, because arrows must not re-render the
     whole page. A zero tab stays selectable and says why in its tooltip. */
  const wasFocus = tabs.contains(document.activeElement);
  tabs.innerHTML = '';
  const inReg = (e) => state.region === 'global' || inRegion(e);
  const mkTab = (id, tabLabel, n, iconKey, slot) => {
    const unscored = id !== 'all' && state.scorecard.length > 0 && !laneHasScores(id);
    const b = el('button', 'etype-tab' + (iconKey === 'all' ? ' is-all' : '')
      + (n === 0 ? ' zero' : '') + (unscored ? ' unscored' : ''),
      typeIcon(iconKey) + `<span class="t-label">${esc(tabLabel)}</span>`
      + `<span class="n">${n}</span>`);
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-controls', 'explore scorecard-sec');
    b.setAttribute('aria-selected', String(state.tab === id));
    b.tabIndex = state.tab === id ? 0 : -1;
    /* accessible name = the visible text ("Heat wave 15"): the count rides the
       name, and axe's label-content-name-mismatch stays honest. The long label
       and the one-line detection rule (About copy) ride the tooltip. */
    const rule = id === 'all'
      ? 'Every event published this window, all types.' : (TYPE_META[id].rule || '');
    const why = n === 0 ? (id === 'tropical_cyclone' ? tcEmptyNote() + ' '
      : `No ${TYPE_META[id] ? TYPE_META[id].label.toLowerCase() : tabLabel.toLowerCase()}`
        + ' met the criteria this window. ') : '';
    b.title = (id === 'all' ? '' : (TYPE_META[id].label + ' — ')) + why + rule;
    b.dataset.tab = id;
    b.dataset.etype = id;   // CSS maps the lane hue onto the glyph ("all" stays neutral)
    b.dataset.slot = slot;  // "a" / "b": which chip row the wrapped grid puts this cell in
    b.onclick = () => setTab(id);
    // Presentational group header above its group (role=none + aria-hidden):
    // not a tab, so the tablist's owned set, the a11y tree and the roving-tabindex
    // order are unchanged, and `button:nth-of-type()` selectors stay stable.
    if (TYPE_GROUP_LABEL[id]) {
      const g = el('span', 'type-group', esc(TYPE_GROUP_LABEL[id]));
      g.setAttribute('role', 'none');
      g.setAttribute('aria-hidden', 'true');
      g.dataset.group = TYPE_GROUP_KEY[TYPE_GROUP_LABEL[id]];
      tabs.appendChild(g);
    }
    tabs.appendChild(b);
    return b;
  };
  mkTab('all', 'All', state.events.filter(inReg).length, 'all', 'a');
  /* Group membership is inherited until the next caption: only a group HEAD
     carries TYPE_GROUP_LABEL, so cold_snap/freeze belong to Temperature, and so
     on. Derived from TYPE_ORDER, not restated (one source of truth). */
  const groups = [];
  for (const t of TYPE_ORDER) {
    if (TYPE_GROUP_LABEL[t]) groups.push({ label: TYPE_GROUP_LABEL[t], members: [t] });
    else groups[groups.length - 1].members.push(t);
  }
  const slotOf = {}, tailOf = {};
  for (const g of groups) {
    const key = TYPE_GROUP_KEY[g.label];
    for (const t of g.members) slotOf[t] = key === 'temperature' ? 'a' : 'b';
    /* A two-column phone grid needs an even number of chips per group or the
       group's last row goes ragged: an odd group's tail chip spans the row. */
    if (g.members.length % 2 === 1) tailOf[g.members[g.members.length - 1]] = true;
  }
  for (const t of TYPE_ORDER) {
    const n = state.events.filter((e) => e.event_type === t && inReg(e)).length;
    const b = mkTab(t, TYPE_TAB_LABEL[t] || TYPE_META[t].short, n, t, slotOf[t]);
    if (tailOf[t]) b.classList.add('is-tail');
  }
  if (wasFocus) {   // keyboard focus must survive a re-render (tab click, region switch)
    const sel = tabs.querySelector('[aria-selected="true"]');
    if (sel) sel.focus();
  }

  const pills = $('#model-pills');
  // ponytail 10:16 NICE: the dropdown is rebuilt every renderControls();
  // snapshot open/closed so toggling a pill doesn't collapse it mid-pick
  const ddWasOpen = !!(pills.querySelector(':scope > details.dd') || {}).open;
  pills.innerHTML = '';
  const dd = document.createElement('details'); dd.className = 'dd'; dd.open = ddWasOpen;
  const sum = document.createElement('summary');
  sum.innerHTML = `<span class="ctl-label">Models</span>&nbsp;${state.models.size} of ${state.allModels.length} <span class="caret" aria-hidden="true">▾</span>`;
  dd.appendChild(sum);
  const body = el('div', 'dd-body');
  state.allModels.forEach((m, i) => {
    const on = state.models.has(m);
    const p = el('button', 'pill' + (on ? '' : ' off'),
      `<span class="dot" style="--c:${modelColor(m, i)}"></span>${displayModelHtml(m)}`);
    p.type = 'button';
    p.setAttribute('aria-pressed', String(on));
    p.title = `toggle ${displayModelName(m)}`;
    p.onclick = () => {
      if (state.models.has(m)) { if (state.models.size > 1) state.models.delete(m); }
      else state.models.add(m);
      renderControls(); renderAll();
    };
    body.appendChild(p);
  });
  if (state.allModels.length > 6) {   // only needed when the default cap hides some
    const all = el('button', 'mini', 'select all');
    all.type = 'button';
    all.title = 'select every model with data';
    all.onclick = () => { state.models = new Set(state.allModels); renderControls(); renderAll(); };
    body.appendChild(all);
  }
  dd.appendChild(body);
  pills.appendChild(dd);

  // collapsed pill row shows the default leads; 'more' discloses the rest
  const chips = $('#lead-chips');
  chips.innerHTML = '';
  const shownLeads = state.leadsMore ? state.allLeads
    : state.allLeads.filter((h) => DEFAULT_LEAD_BINS.includes(h) || state.leads.has(h));
  shownLeads.forEach((h) => {
    const b = el('button', state.leads.has(h) ? 'on' : '', leadLabel(h));
    b.onclick = () => {
      if (state.leads.has(h)) { if (state.leads.size > 1) state.leads.delete(h); }
      else state.leads.add(h);
      renderControls(); renderScorecard();
    };
    chips.appendChild(b);
  });
  const more = $('#lead-more');
  more.textContent = state.leadsMore ? 'fewer leads' : 'more leads';
  more.setAttribute('aria-expanded', String(state.leadsMore));
  more.onclick = () => { state.leadsMore = !state.leadsMore; renderControls(); };
  /* A2 regression (owner 2026-11-07): the 'more metrics' button used to be
     created and inserted on EVERY renderControls() call — one copy leaked per
     tab/model/lead click. Like #lead-more it is a static node updated in
     place; renderControls must never insert controls into persistent
     containers (all other controls are rebuilt inside innerHTML-cleared
     groups). Keep that invariant: no insertAdjacent* into nav containers. */
  const mmore = $('#metrics-more');
  mmore.textContent = state.scMore ? 'fewer metrics' : 'more metrics';
  mmore.setAttribute('aria-pressed', String(state.scMore));
  mmore.onclick = () => { state.scMore = !state.scMore; renderControls(); renderScorecard(); };

  document.querySelectorAll('#chart-mode button').forEach((b) => {
    b.classList.toggle('on', b.dataset.mode === state.chartMode);
    b.onclick = () => { state.chartMode = b.dataset.mode; saveState(); renderControls(); renderSkillCharts(); };
  });

  /* Region dropdown (OWB-style), in the explore filter row beside the type tabs:
     like the type filter it narrows the map, the list and the scorecard, and the
     scorecard echoes it. Options come from scorecard_by_region.json's ordered
     `regions` (labels + honest counts live there); map extents live in
     dashboard/regions.json. No region data (e.g. an archived week predating the
     lane) -> the control hides itself rather than offering a broken filter. */
  const rsel = $('#region-select');
  if (rsel) {
    const usable = !!(state.regionData && state.regionData.regions.length > 1 && REGIONS.length > 1);
    if (state.region !== 'global'
        && !(usable && state.regionData.regions.some((r) => r.key === state.region))) {
      state.region = 'global';   // region vanished under us (week switch / data refresh)
    }
    const rgrp = rsel.closest('.ctl-group');   // labeled group hides with the control
    if (rgrp) rgrp.classList.toggle('hidden', !usable);
    rsel.classList.toggle('hidden', !usable);
    if (usable) {
      const sig = state.regionData.regions.map((r) => r.key).join(',');
      if (rsel.dataset.sig !== sig) {
        rsel.dataset.sig = sig;
        rsel.innerHTML = '';
        for (const r of state.regionData.regions) {
          const o = document.createElement('option');
          o.value = r.key; o.textContent = r.label;
          rsel.appendChild(o);
        }
      }
      rsel.value = state.region;
      rsel.onchange = () => setRegion(rsel.value, true);
    }
  }
}

/* Region switch (dropdown or URL sync): re-render the scorecard, move the
   map, persist to the hash (#region=, Global = no param). */
function setRegion(key, push) {
  if (key === state.region) return;
  state.region = key;
  renderHeader();      // the EVENTS stat is region-scoped
  renderControls();    // type-tab counts follow the region
  renderAll();
  if (window.MergedMap) MergedMap.setRegion(regionDef(key));
  syncUrl(push);
}

/* ------------------------------------------------------------------ */
/* scorecard                                                           */
/* ------------------------------------------------------------------ */
function activeTypes() {
  return state.tab === 'all' ? TYPE_ORDER : [state.tab];
}

/* Scorecard rows of the active region: rows come pre-sliced server-side
   (scorecard_by_region.json, regions-data contract); Global reuses scorecard.json
   itself (identical by construction — the contract omits the global blob). The
   model/lead rosters in loadData keep reading the global state.scorecard, so a
   region never shrinks the model list or the lead pills. */
function scRows() {
  if (state.region === 'global' || !state.regionData) return state.scorecard;
  return state.regionData.rows[state.region] || [];
}
// the selected region's entry ({key,label,n_events,by_type}) from the contract, or null for Global
const regionEntry = () => (state.region !== 'global' && state.regionData
  ? state.regionData.regions.find((r) => r.key === state.region) || null
  : null);

/* JS port of ewb_live/regions.py assign_regions (keep in sync): the bbox
   centroid — lon follows the EASTWARD arc so antimeridian-wrapped boxes
   (lon_min > lon_max, EWB convention) land right — must sit inside the
   region's lat/lon box, inclusive on all edges. NH/SH boxes start at ±20°,
   so subtropical centroids belong to Tropics only; multi-membership is
   expected (a centroid in Europe is also Northern Hemisphere). */
function eventRegions(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return [];
  const [latmin, latmax, lonmin, lonmax] = bbox;
  const lat = (latmin + latmax) / 2;
  const dlon = ((lonmax - lonmin) % 360 + 360) % 360;
  const lon = (((lonmin + dlon / 2 + 180) % 360) + 360) % 360 - 180;
  const keys = [];
  for (const r of REGIONS) {
    if (r.key === 'global') { keys.push('global'); continue; }
    const lonIn = ((lon - r.lon[0]) % 360 + 360) % 360 <= (r.lon[1] - r.lon[0]);
    if (r.lat[0] <= lat && lat <= r.lat[1] && lonIn) keys.push(r.key);
  }
  return keys;
}
const inRegion = (e) => state.region === 'global' || regionsOf(e).includes(state.region);
/* Regions of one event: the server's footprint-based map when the contract
   carries it (authoritative — the tables were built from exactly these keys),
   the centroid port otherwise (older region file / id the file does not cover). */
function regionsOf(e) {
  const m = state.regionData && state.regionData.events;
  if (m && e && m[e.id]) return m[e.id];
  return eventRegions(e && e.bbox);
}
window.EwbRegions = { eventRegions, forEvent: (id, bbox) => regionsOf({ id, bbox }) };
// small-sample threshold (owner 2026-11-10): cells under this read as small-n
const minSample = () => (state.regionData && state.regionData.assignment
  && Number.isFinite(state.regionData.assignment.min_sample)
  ? state.regionData.assignment.min_sample : 5);

function scIndex() {
  // (etype,metric,lead) -> {model: {mean, rel, n}}; lead may be null (event-level metrics)
  const idx = new Map();
  const B = state.baseline;
  for (const r of scRows()) {
    if (!state.models.has(r.model)) continue;
    const key = `${r.event_type}|${r.metric}|${r.lead_time_hours}`;
    let g = idx.get(key);
    if (!g) { g = {}; idx.set(key, g); }
    g[r.model] = { mean: r.mean_value, rel: r.rel, n: r.n_events, ns: r.n_samples, lo: r.ci_lo, hi: r.ci_hi };
  }
  for (const [key, g] of idx) {
    const met = key.split('|')[1];
    const ref = g[B] ? g[B].mean : null;
    for (const m of Object.keys(g)) {
      if (m === B) g[m].rel = 0;
      else if (relUntrusted(met) || g[m].rel === null || g[m].rel === undefined) {
        g[m].rel = relSkill(g[m].mean, ref, met);  // sign-normalised, metric-aware
      }
    }
  }
  return idx;
}

function metricsFor(etype) {
  const fromMeta = state.meta.metrics
    ? Object.keys(state.meta.metrics).filter((k) => (state.meta.metrics[k].event_types || []).includes(etype))
    : [];
  const fromData = [...new Set(scRows().filter((r) => r.event_type === etype).map((r) => r.metric))];
  const seen = new Set();
  const all = [...fromMeta, ...fromData].filter((m) => (seen.has(m) ? false : (seen.add(m), fromData.includes(m))));
  // compact hero: one headline metric per event type unless 'more metrics' is on
  // headline = the metric that reads as skill by lead (RMSE for heat/freeze, CSI for AR); the signed
  // event-level duration error stays behind 'more metrics' (G-10)
  const pref = { heat_wave: 'RootMeanSquaredError', freeze: 'RootMeanSquaredError',
    cold_snap: 'RootMeanSquaredError',
    atmospheric_river: 'CriticalSuccessIndex' }[etype];
  if (TYPE_LEADS[etype]) {
    /* track lane: the miss rate always sits beside the headline error (D5) */
    const ord = TRACK_METRIC_ORDER.filter((m) => all.includes(m))
      .concat(all.filter((m) => !TRACK_METRIC_ORDER.includes(m)));
    return state.scMore ? ord : ord.slice(0, 2);
  }
  if (!state.scMore && all.length > 1) return [all.includes(pref) ? pref : all[0]];
  return all;
}

/* Columns for one metric in one event-type table:
   numeric leads (selected) + a Mean column; event-level metrics (lead_time_hours
   null in every row, e.g. DurationMeanError/EarlySignal from the real pipeline)
   render as a single 'event' column. */
function metricColumns(idx, etype, met, leads) {
  const hasLeadData = leads.some((h) => idx.has(`${etype}|${met}|${h}`));
  if (hasLeadData) return leads.map((h) => ({ kind: 'lead', lead: h }))
    .concat([{ kind: 'mean' }]);
  if (idx.has(`${etype}|${met}|null`)) return [{ kind: 'event' }];
  return null; // no data at all
}

/* ------------------------------------------------------------------ */
/* UNIFIED SCORECARD (ux redesign AC-1.3 / AC-1.4 / AC-1.5)                */
/*  • KPI leader strip — one card per scored hazard; a card drills the     */
/*    tables into that hazard (and back to the overview if re-clicked).    */
/*  • `All` — ONE Models × Hazards overview matrix: a column per scored    */
/*    hazard at its headline metric, the baseline row printing the raw     */
/*    mean everything is measured against, AI rows printing colour-coded    */
/*    mean relative skill. Seven stacked per-hazard tables used to sit      */
/*    here, which read as a wall of numbers.                               */
/*  • one hazard — that hazard's lead-by-lead table; model rows with zero   */
/*    finite values on the lane are hidden and named in one quiet           */
/*    footnote, and any lane methodology prose is folded away.              */
/* ------------------------------------------------------------------ */

/* the metric each lane is read by in the overview (the pipeline's headline) */
const HEADLINE_METRIC = {
  heat_wave: 'RootMeanSquaredError', cold_snap: 'RootMeanSquaredError',
  freeze: 'RootMeanSquaredError', atmospheric_river: 'CriticalSuccessIndex',
  flood: 'CriticalSuccessIndex', tropical_cyclone: 'TrackPositionError',
  midlat_cyclone: 'TrackPositionError',
};
let scSmallN = false;   // any rendered cell rests on < minSample() events

/* Published event count for one lane in the current view. The region
   contract's n_events/by_type is a SCORED split (centroid rule owned by
   ewb_live/regions.py), so it answers 0 for a lane this build never scored —
   for those the honest number is the published count through the same region
   map the tables were built from. */
function laneEventCount(etype) {
  const re = regionEntry();
  if (re) {
    const bt = (re.by_type || {})[etype];
    if (bt) return bt;
    return state.events.filter((e) => e.event_type === etype && inRegion(e)).length;
  }
  return state.events.filter((e) => e.event_type === etype).length;
}

/* Scored lanes for the overview: etype + its headline metric + its leads. */
function overviewLanes(idx) {
  const leads = state.allLeads.filter((h) => state.leads.has(h));
  const out = [];
  for (const etype of TYPE_ORDER) {
    if (!laneHasScores(etype)) continue;
    const have = [...new Set(scRows().filter((r) => r.event_type === etype).map((r) => r.metric))];
    const pref = HEADLINE_METRIC[etype];
    const met = (pref && have.includes(pref)) ? pref : (metricsFor(etype)[0] || have[0]);
    if (!met) continue;
    const laneLeads = leadsForType(etype, leads);
    if (!metricColumns(idx, etype, met, laneLeads)) continue;
    out.push({ etype, met, leads: laneLeads });
  }
  return out;
}

/* One model's mean over a lane's visible leads: the value, the mean relative
   skill, the sample behind it, and whether every contributing lead is greyed
   (>50 % misses — an error that rests on the storms the model kept). */
function laneMeanCell(idx, etype, met, model, leads) {
  let sum = 0, n = 0, grey = 0, rsum = 0, rn = 0;
  for (const h of leads) {
    const g = idx.get(`${etype}|${met}|${h}`);
    const c = g && g[model];
    if (!c || !Number.isFinite(c.mean)) continue;
    sum += c.mean; n += 1;
    if (missGreyed(idx, etype, met, h, model)) { grey += 1; continue; }
    if (Number.isFinite(c.rel)) { rsum += c.rel; rn += 1; }
  }
  if (!n) return null;
  return { mean: sum / n, rel: rn ? rsum / rn : null, nLeads: n,
    n: meanSample(idx, etype, met, model, leads), greyed: grey > 0 && grey === n };
}

/* Every model this view should account for: what the run declared plus what the
   data carries, minus the ones the model filter hid (a hidden model is a choice,
   not a missing score). Used for the "unscored" footnotes. */
function modelUniverse() {
  const declared = Array.isArray(state.meta.models) ? state.meta.models : [];
  const out = [];
  for (const m of new Set([...declared, ...state.allModels])) {
    if (!state.allModels.includes(m) || state.models.has(m)) out.push(m);
  }
  return out;
}

/* B1: roving tabindex — the first tip-cell of each row is the tab stop. */
function roveTips(tbody) {
  tbody.querySelectorAll('tr').forEach((tr) => {
    let first = true;
    tr.querySelectorAll('td[data-tip]').forEach((td) => {
      td.tabIndex = first ? 0 : -1; first = false;
    });
  });
}

/* horizontal-scroll affordance: a fade hint while the table is wider than its box */
function scrollHint(wrap) {
  const upd = () => {
    const over = wrap.scrollWidth - wrap.clientWidth > 4;
    wrap.classList.toggle('scrollable', over);
    wrap.classList.toggle('at-end', over && wrap.scrollLeft >= wrap.scrollWidth - wrap.clientWidth - 2);
  };
  requestAnimationFrame(() => { upd(); wrap.addEventListener('scroll', upd, { passive: true }); });
}

/* Per-lane leader: the model with the best mean relative skill over the
   visible metric × lead cells, and how many cells it wins. A lane no
   baseline model scores (flood) cannot rank and says so. */
function laneLeader(idx, etype) {
  const B = state.baseline;
  const leads = state.allLeads.filter((h) => state.leads.has(h));
  const visible = state.allModels.filter((m) => state.models.has(m));
  const cols = [];
  for (const met of metricsFor(etype)) {
    const c = metricColumns(idx, etype, met, leadsForType(etype, leads));
    if (c) cols.push({ met, columns: c });
  }
  if (!cols.length) return null;
  const acc = {};
  for (const m of visible) if (m !== B) acc[m] = { s: 0, n: 0, wins: 0 };
  let cellN = 0;
  for (const { met, columns } of cols) {
    if (!skillRanked(etype, met)) continue;   // track lanes: only the track error ranks
    for (const c of columns) {
      if (c.kind === 'mean') continue;        // the mean duplicates lead data
      const lead = c.kind === 'lead' ? c.lead : null;
      const g = idx.get(`${etype}|${met}|${lead}`);
      if (!g) continue;
      const rels = {};
      for (const m of visible) {
        if (m === B) continue;
        const cell = g[m];
        if (!cell || !Number.isFinite(cell.rel)) continue;
        if (missGreyed(idx, etype, met, lead, m)) continue;   // >50 % misses: not a ranking input
        rels[m] = cell.rel;
        acc[m].s += cell.rel; acc[m].n += 1;
      }
      const scored = Object.keys(rels);
      if (!scored.length) continue;
      cellN += 1;
      acc[scored.reduce((a, b) => (rels[b] > rels[a] ? b : a))].wins += 1;
    }
  }
  const ranked = Object.keys(acc).filter((m) => acc[m].n > 0)
    .sort((a, b) => (acc[b].s / acc[b].n) - (acc[a].s / acc[a].n));
  if (!ranked.length) return { unranked: true };
  const leader = ranked[0];
  const out = { model: leader, meanRel: acc[leader].s / acc[leader].n,
    cells: acc[leader].wins, total: cellN };
  /* track lanes rank on track error ONLY, so say it and put the miss rates
     beside it (a model that loses more storms than the baseline can still win
     on the storms it keeps) */
  if (TYPE_LEADS[etype]) {
    const lds = leadsForType(etype, leads);
    const mm = (m) => meanVisible(idx, etype, 'TrackMiss', m, lds);
    const a = mm(leader), b = mm(B);
    out.missNote = a !== null && b !== null
      ? `ranks track error · misses ${Math.round(a * 100)}% vs ${Math.round(b * 100)}%`
        + (a > MISS_GREY ? ` (>${Math.round(MISS_GREY * 100)}%)` : '')
      : 'ranks track error only';
  }
  return out;
}

/* KPI leader strip (replaces the multi-sentence verdict line): the answer per
   hazard in one glanceable card. */
function renderKpiStrip(idx) {
  const host = $('#kpi-strip');
  if (!host) return;
  host.innerHTML = '';
  const B = state.baseline;
  for (const etype of TYPE_ORDER) {
    if (!laneHasScores(etype)) continue;    // unscored lanes: one quiet line under the table
    const n = laneEventCount(etype);
    const lead = laneLeader(idx, etype);
    const on = state.tab === etype;
    const card = el('button', 'kpi-card' + (on ? ' on' : ''));
    card.type = 'button';
    card.setAttribute('aria-pressed', String(on));
    const short = TYPE_TAB_LABEL[etype] || TYPE_META[etype].short;
    let body, sub;
    if (!lead || lead.unranked) {
      body = `<span class="kpi-model">${displayModelHtml('graphcast')}</span>`
        + `<span class="kpi-skill na">only</span>`;
      sub = 'no baseline scores this lane';
      card.title = `${TYPE_META[etype].label}: only ${displayModelName('graphcast')} carries the field `
        + 'this lane is scored against, and no baseline model does — so nothing ranks.';
    } else {
      body = `<span class="kpi-model">${displayModelHtml(lead.model)}</span>`
        + `<span class="kpi-skill ${lead.meanRel >= 0 ? 'pos' : 'neg'}">`
        + `${lead.meanRel >= 0 ? '+' : '\u2212'}${Math.abs(lead.meanRel).toFixed(1)}%</span>`;
      sub = lead.missNote
        ? `best of ${state.models.size} · won ${lead.cells}/${lead.total} · track error`
        : `best of ${state.models.size} · won ${lead.cells}/${lead.total} cells`;
      card.title = `${TYPE_META[etype].label}: ${displayModelName(lead.model)} leads the visible cells `
        + `by ${lead.meanRel >= 0 ? '+' : '\u2212'}${Math.abs(lead.meanRel).toFixed(1)}% mean skill vs `
        + `${displayModelName(B)}, winning ${lead.cells} of ${lead.total} metric × lead cells`
        + (lead.missNote ? ` (${lead.missNote})` : '') + '.';
    }
    card.innerHTML = `<span class="kpi-top">${typeChipHtml(etype)}`
      + `<span class="kpi-n">${n} event${n === 1 ? '' : 's'}</span></span>`
      + `<span class="kpi-lead">${body}</span>`
      + `<span class="kpi-sub">${esc(sub)}</span>`;
    card.onclick = () => setTab(state.tab === etype ? 'all' : etype);
    host.appendChild(card);
  }
  host.hidden = !host.children.length;
}

/* View strip: Overview + one pill per hazard with events in this view. */
function renderScViews() {
  const host = $('#sc-views');
  if (!host) return;
  host.innerHTML = '';
  const inReg = (e) => state.region === 'global' || inRegion(e);
  const mk = (id, label, n, tip) => {
    const b = el('button', 'sc-view' + (state.tab === id ? ' on' : ''),
      `${esc(label)}<span class="n">${n}</span>`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.tab === id));
    if (tip) b.title = tip;
    b.onclick = () => setTab(id);
    host.appendChild(b);
  };
  mk('all', 'Overview', state.events.filter(inReg).length,
    'One Models × Hazards overview — every scored lane in one table.');
  for (const t of TYPE_ORDER) {
    const n = state.events.filter((e) => e.event_type === t && inReg(e)).length;
    if (!n) continue;
    mk(t, TYPE_TAB_LABEL[t] || TYPE_META[t].short, n,
      `${TYPE_META[t].label} — lead by lead. ${TYPE_META[t].rule || ''}`);
  }
}

/* The overview matrix: Model × scored hazard. Baseline row = raw mean + unit;
   AI rows = colour-coded mean relative skill (raw value where the lane has no
   baseline at all, e.g. flood). Every cell keeps the full detail tooltip. */
function renderOverviewMatrix(idx) {
  const host = $('#scorecard-tables');
  const lanes = overviewLanes(idx);
  const B = state.baseline;
  const block = el('div', 'sc-block sc-overview');
  if (!lanes.length) {
    block.appendChild(el('h3', '', 'Scorecard'));
    const note = el('p', 'lane-empty');
    note.textContent = state.region !== 'global'
      ? `No scored events in ${regionEntry() ? regionEntry().label : 'this region'} this window`
      : 'No score rows in this data release yet.';
    block.appendChild(note);
    host.appendChild(block);
    return;
  }
  block.appendChild(el('h3', '', 'Models × hazards overview'));

  const wrap = el('div', 'table-wrap');
  const table = el('table', 'sc-matrix');
  const head = el('tr');
  head.appendChild(el('th', 'model-col', 'Model'));
  for (const L of lanes) {
    const mm = metricMeta(L.met);
    const n = laneEventCount(L.etype);
    const th = el('th', 'mx-head');
    const b = el('button', 'mx-drill');
    b.type = 'button';
    b.innerHTML = `<span class="mx-name">${esc(TYPE_TAB_LABEL[L.etype] || TYPE_META[L.etype].short)}</span>`
      + `<span class="mx-met">${esc(mm.label)}${mm.units ? ` [${esc(mm.units)}]` : ''}</span>`
      + `<span class="mx-n">${n} event${n === 1 ? '' : 's'}</span>`;
    b.title = `${TYPE_META[L.etype].label} — ${mm.label}${mm.units ? ` (${mm.units})` : ''}, `
      + `mean over ${n} event${n === 1 ? '' : 's'} at the visible leads. Open the lead-by-lead table.`;
    b.onclick = () => setTab(L.etype);
    th.appendChild(b);
    head.appendChild(th);
  }
  table.appendChild(el('thead')).appendChild(head);

  /* "has any data at all" asks over EVERY lead, not just the selected ones: a
     model hidden by the lead filter is not an unscored model. */
  const hasAny = (m) => lanes.some((L) => laneMeanCell(idx, L.etype, L.met, m, state.allLeads));
  const rows = state.modelOrder.filter(hasAny);
  const quiet = modelUniverse().filter((m) => !hasAny(m));
  const tbody = el('tbody');
  for (const model of rows) {
    const tr = el('tr', model === B ? 'baseline-row' : '');
    const colorIdx = state.allModels.indexOf(model);
    tr.appendChild(el('td', 'model-col',
      `<span class="dot" style="--c:${model === B ? 'var(--ink-muted)' : modelColor(model, colorIdx)}"></span>`
      + displayModelHtml(model)));   /* the baseline row is already tagged by
                                         `tr.baseline-row .model-col::after` */
    for (const L of lanes) {
      const c = laneMeanCell(idx, L.etype, L.met, model, L.leads);
      if (!c) { tr.appendChild(el('td', 'cell empty', '—')); continue; }
      const mm = metricMeta(L.met);
      const small = Number.isFinite(c.n) && c.n < minSample();
      if (small && model !== B && !c.greyed) scSmallN = true;
      const raw = model === B || !Number.isFinite(c.rel);
      const td = el('td', 'cell mx-cell' + (model === B ? ' baseline' : '')
        + (small ? ' small-n' : '') + (c.greyed ? ' miss-grey' : ''),
      raw
        ? `${fmtVal(L.met, c.mean)}${mm.units ? ` <span class="cell-u">${esc(mm.units)}</span>` : ''}`
        : `${c.rel >= 0 ? '+' : '\u2212'}${Math.abs(c.rel).toFixed(1)}%`);
      td.dataset.model = model;
      if (!raw && !c.greyed) td.style.background = cellColor(c.rel);
      if (c.greyed) td.title = 'miss rate > 50% at every visible lead';
      attachTip(td, L.etype, L.met, { kind: 'mean', mean: c.mean, rel: c.rel,
        nLeads: c.nLeads, n: c.n, small, greyed: c.greyed,
        refMean: model === B ? null : (laneMeanCell(idx, L.etype, L.met, B, L.leads) || {}).mean,
        laneNote: raw && model !== B ? 'no baseline on this lane — the raw value is shown' : '' });
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  roveTips(tbody);
  table.appendChild(tbody);
  wrap.appendChild(table);
  block.appendChild(wrap);

  /* Lanes with events but no score rows are not a column and not an empty
     table — they are one quiet line saying where to find them. */
  const unscored = TYPE_ORDER.filter((t) => !laneHasScores(t) && laneEventCount(t) > 0);
  if (unscored.length) {
    const p = el('p', 'sc-quiet');
    p.textContent = 'Tracked on map (not yet benchmarked): '
      + unscored.map((t) => `${TYPE_META[t].label} (${laneEventCount(t)})`).join(', ');
    block.appendChild(p);
  }
  if (quiet.length) {
    const p = el('p', 'sc-quiet');
    p.textContent = `No score rows on any lane here: ${quiet.map((m) => displayModelName(m)).join(', ')}.`;
    block.appendChild(p);
  }
  host.appendChild(block);
  scrollHint(wrap);
}

/* One hazard's lead-by-lead table. Models with nothing to show on this lane
   are hidden (a row of dashes reads as performance); they are named once,
   below the table. */
function renderHazardTable(idx, etype) {
  const host = $('#scorecard-tables');
  const B = state.baseline;
  const leads = state.allLeads.filter((h) => state.leads.has(h));
  const cols = new Map();
  for (const met of metricsFor(etype)) {
    const c = metricColumns(idx, etype, met, leadsForType(etype, leads));
    if (c) cols.set(met, c);
  }
  const nEv = laneEventCount(etype);
  const smallLane = nEv > 1 && nEv < minSample();
  const block = el('div', 'sc-block');
  block.appendChild(el('h3', '',
    typeChipHtml(etype) +
    `${nEv} event${nEv === 1 ? '' : 's'}` +
    (nEv === 1 ? ' · indicative' : smallLane ? ' · small sample' : '')));

  /* Owner 2026-11-07: an empty lane is a legitimate outcome, not a bug —
     calm one-line note + criteria tooltip instead of a table of dashes. */
  if (!nEv) {
    const note = el('p', 'lane-empty');
    const re2 = regionEntry();
    const broken = laneStatusNote(etype);   // a failed lane outranks the region wording
    note.textContent = broken
      ? broken
      : re2
        ? `No ${TYPE_META[etype].label.toLowerCase()} in ${re2.label} this window`
        : emptyLaneLine(etype);
    note.title = re2 && !broken
      ? 'Events are assigned to regions by the centroid of their bounding box'
      : TYPE_CRITERIA[etype];
    block.appendChild(note);
    host.appendChild(block);
    return;
  }
  if (!cols.size) {
    /* No score columns for this lane in THIS build. Either it is published
       without a scorecard by design (the observation lanes: no EWB benchmark)
       or the release simply has no score rows for it. Say which. */
    const note = el('p', 'lane-empty');
    note.textContent = TYPE_META[etype]?.noScoresNote
      ? TYPE_META[etype].noScoresNote(nEv)
      : TYPE_META[etype]?.noScores
        ? `Not scored — ${nEv} event${nEv === 1 ? '' : 's'}; no EWB benchmark for this lane yet.`
        : `Not scored in this data release — ${nEv} published event${nEv === 1 ? '' : 's'}; no score rows for this lane yet.`;
    note.title = TYPE_CRITERIA[etype];
    block.appendChild(note);
    host.appendChild(block);
    return;
  }

  const wrap = el('div', 'table-wrap');
  const table = el('table');
  const grp = el('tr', 'grp');
  grp.appendChild(el('th', 'model-col', 'Model'));
  const leadRow = el('tr', 'lead');
  leadRow.appendChild(el('th', 'model-col', `vs ${esc(displayModelName(B))}`));
  for (const [met, columns] of cols) {
    const mm = metricMeta(met);
    const th = el('th', '', `${esc(mm.label)}${mm.units ? ` <span class="units">[${esc(mm.units)}]</span>` : ''}`);
    th.colSpan = columns.length;
    th.title = metricBlurb(met);
    grp.appendChild(th);
    for (const c of columns) {
      leadRow.appendChild(el('th', c.kind === 'mean' ? 'mean-g' : (c.kind === 'event' ? 'event-col' : ''),
        c.kind === 'lead' ? leadLabel(c.lead) : (c.kind === 'mean' ? 'Mean' : 'event')));
    }
  }
  const thead = el('thead');
  thead.appendChild(grp); thead.appendChild(leadRow);
  table.appendChild(thead);

  const laneLeads = leadsForType(etype, leads);
  const hasAny = (model) => [...cols.keys()].some((met) => {
    if (meanVisible(idx, etype, met, model, state.allLeads) !== null) return true;
    // event-level metrics carry no lead: ask the null column directly
    const g = idx.get(`${etype}|${met}|null`);
    return !!(g && g[model] && Number.isFinite(g[model].mean));
  });
  const shown = state.modelOrder.filter(hasAny);
  const skipped = modelUniverse().filter((m) => !hasAny(m));

  const tbody = el('tbody');
  for (const model of shown) {
    const tr = el('tr', model === B ? 'baseline-row' : '');
    const colorIdx = state.allModels.indexOf(model);
    tr.appendChild(el('td', 'model-col',
      `<span class="dot" style="--c:${modelColor(model, colorIdx)}"></span>${displayModelHtml(model)}`));
    for (const [met, columns] of cols) {
      let s = 0, n = 0, nCells = 0, nGrey = 0;
      for (const c of columns) {
        if (c.kind === 'mean') {
          const mv = meanVisible(idx, etype, met, model, laneLeads);
          const mN = meanSample(idx, etype, met, model, laneLeads);
          const mSmall = mN !== null && mN < minSample();
          const allGrey = nCells > 0 && nGrey === nCells;   // every contributing lead is >50% misses
          const meanTd = el('td', 'cell mean' + (mSmall ? ' small-n' : '') + (allGrey ? ' miss-grey' : ''),
            mv === null ? '—' : fmtVal(met, mv));
          meanTd.dataset.model = model;
          if (n && !allGrey) meanTd.style.background = model === B ? '' : cellColor(s / n);
          if (allGrey) meanTd.title = 'miss rate > 50% at every lead';
          if (mSmall && model !== B && !allGrey) scSmallN = true;
          attachTip(meanTd, etype, met, { kind: 'mean', mean: mv, rel: n ? s / n : null,
            nLeads: n, n: mN, small: mSmall, greyed: allGrey });
          tr.appendChild(meanTd);
          continue;
        }
        const lead = c.kind === 'lead' ? c.lead : null;
        const g = idx.get(`${etype}|${met}|${lead}`);
        const cell = g && g[model];
        if (!cell || cell.mean === null || !Number.isFinite(cell.mean)) {
          tr.appendChild(el('td', 'cell empty', '—'));
          continue;
        }
        /* D5: a track-error cell whose miss rate is above 50 % is greyed, not
           coloured by skill, and does not feed the Mean colour */
        const greyed = missGreyed(idx, etype, met, lead, model);
        nCells += 1; nGrey += greyed ? 1 : 0;
        if (Number.isFinite(cell.rel) && !greyed) { s += cell.rel; n += 1; }
        const small = Number.isFinite(cell.n) && cell.n < minSample();
        if (small && model !== B && !greyed) scSmallN = true;
        const td = el('td', 'cell' + (model === B ? ' baseline' : '')
          + (small ? ' small-n' : '') + (greyed ? ' miss-grey' : ''), fmtVal(met, cell.mean));
        td.dataset.model = model;
        td.style.background = model === B || greyed ? '' : cellColor(cell.rel);
        if (greyed) td.title = 'miss rate > 50%';
        attachTip(td, etype, met, { kind: c.kind, lead, mean: cell.mean, rel: cell.rel,
          n: cell.n, ns: cell.ns, lo: cell.lo, hi: cell.hi, small, greyed });
        tr.appendChild(td);
      }
    }
    tbody.appendChild(tr);
  }
  roveTips(tbody);
  table.appendChild(tbody);
  wrap.appendChild(table);
  block.appendChild(wrap);

  /* Models with nothing on this lane: one quiet line, not a row of dashes. */
  if (skipped.length) {
    const p = el('p', 'sc-quiet');
    p.textContent = `Unscored on this lane: ${skipped.map((m) => displayModelName(m)).join(', ')}.`;
    p.title = 'These models carry no finite value for this hazard in this run — '
      + 'no score rows, not a score of zero.';
    block.appendChild(p);
  }
  /* Lane methodology stays available but never open: these paragraphs are
     8 lines long and used to push the next table off the screen. */
  if (TYPE_LEADS[etype]) {
    const fold = el('details', 'lane-notes-fold');
    const sum = el('summary', '', 'Scoring &amp; verification notes');
    fold.appendChild(sum);
    fold.appendChild(etype === 'flood' ? floodLaneNote(etype, nEv) : trackLaneNote(etype, nEv));
    block.appendChild(fold);
  }
  host.appendChild(block);
  scrollHint(wrap);
}

function computeModelOrder(idx) {
  const B = state.baseline;
  const etypeCols = new Map();
  for (const etype of activeTypes()) {
    const cols = new Map();
    for (const met of metricsFor(etype)) {
      const c = metricColumns(idx, etype, met, leadsForType(etype, state.allLeads));
      if (c) cols.set(met, c);
    }
    if (cols.size) etypeCols.set(etype, cols);
  }
  /* ONE global model order for all views (rows line up between hazards):
     baseline first, then mean rel-skill over every visible cell. */
  const relSums = {};
  for (const m of state.allModels) relSums[m] = { s: 0, n: 0 };
  for (const [key, g] of idx) {
    const [e2, met, hStr] = key.split('|');
    const cols = etypeCols.get(e2);
    if (!cols || !cols.has(met) || !skillRanked(e2, met)) continue;
    const h = hStr === 'null' ? null : +hStr;
    if (h !== null && !cols.get(met).some((c) => c.kind === 'lead' && c.lead === h)) continue;
    for (const m of Object.keys(g)) {
      if (missGreyed(idx, e2, met, h, m)) continue;
      if (m !== B && Number.isFinite(g[m].rel)) { relSums[m].s += g[m].rel; relSums[m].n += 1; }
    }
  }
  state.modelOrder = state.allModels
    .filter((m) => state.models.has(m))
    .sort((a, b) => {
      if (a === B) return -1;
      if (b === B) return 1;
      const ra = relSums[a].n ? relSums[a].s / relSums[a].n : -1e9;
      const rb = relSums[b].n ? relSums[b].s / relSums[b].n : -1e9;
      return rb - ra;
    });
}

function renderScorecard() {
  const host = $('#scorecard-tables');
  host.innerHTML = '';
  scSmallN = false;
  // partial-results badge: shows until every meta-declared model has scores
  // (or the run declares itself complete). Auto-clears when the full run lands.
  const badge = $('#scorecard-status');
  if (badge) {
    const declared = Array.isArray(state.meta.models) ? state.meta.models : [];
    const scored = new Set(state.scores.map((r) => r.model));
    const complete = state.meta.full_run_complete === true
      || (declared.length > 0 && declared.every((m) => scored.has(m)));
    badge.textContent = complete ? ''
      : `partial results — ${scored.size} of ${declared.length} models scored`;
    badge.classList.toggle('hidden', complete);
  }
  const scope = $('#sc-scope');
  if (scope) {
    scope.innerHTML = '';
    const re = regionEntry();
    if (re) {
      scope.appendChild(el('span', '', `in ${esc(re.label)} · `));
      const g = el('button', 'linkbtn', 'all regions');
      g.type = 'button';
      g.onclick = () => setRegion('global', true);
      scope.appendChild(g);
    }
    if (state.tab !== 'all') {
      scope.appendChild(el('span', '', `${esc(TYPE_META[state.tab].label)} only · `));
      const all = el('button', 'linkbtn', 'show all types');
      all.type = 'button';
      all.onclick = () => setTab('all');
      scope.appendChild(all);
    }
  }
  const idx = scIndex();
  tooltipIdx = idx;
  computeModelOrder(idx);
  renderScViews();
  renderKpiStrip(idx);
  if (state.tab === 'all') renderOverviewMatrix(idx);
  else renderHazardTable(idx, state.tab);
  /* Small-sample warning (owner 2026-11-10): cells under minSample() events get
     a red edge + glow; say what that means once, next to the tables, and only
     when the current view actually contains such a cell. */
  const note = $('#sc-sample-note');
  if (note) {
    note.hidden = !scSmallN;
    if (scSmallN) {
      const re = regionEntry();
      note.innerHTML = `<span class="sn-mark" aria-hidden="true"></span> Glowing cells rest on `
        + `<b>fewer than ${minSample()} events</b>`
        + (re ? ` in ${esc(re.label)}` : '') + '. '
        + 'They are indicative, not a verdict: one event can move a cell by tens of percent.';
    }
  }
}

function meanVisible(idx, etype, met, model, leads) {
  let s = 0, n = 0;
  for (const h of leads) {
    const g = idx.get(`${etype}|${met}|${h}`);
    if (g && g[model] && Number.isFinite(g[model].mean)) { s += g[model].mean; n += 1; }
  }
  return n ? s / n : null;
}

/* Largest n_events behind any visible lead cell of one (etype, metric, model):
   the mean column averages leads of the SAME event set, so its own sample is
   that union — the biggest per-lead n is the honest estimate. Used only for the
   small-sample cue (owner 2026-11-10: warn below 5 events). */
function meanSample(idx, etype, met, model, leads) {
  let best = null;
  for (const h of leads) {
    const g = idx.get(`${etype}|${met}|${h}`);
    const c = g && g[model];
    if (c && Number.isFinite(c.mean) && Number.isFinite(c.n)) {
      best = best === null ? c.n : Math.max(best, c.n);
    }
  }
  return best;
}

/* tooltip (B1, DR2: mouse + keyboard + touch)
   Roving tabindex: the first tip-cell of each row is the tab stop; Left/Right
   arrows move within the row. focusin shows the tooltip (keyboard), a touch
   tap toggles it; a tap outside dismisses. aria-label carries the same data
   the tooltip shows. */
let tipEl;
/* DR2 B1-touch v2: timestamp of last non-mouse pointerdown (see attachTip) */
let lastTouchTs = 0;
let tooltipIdx = null;   // scorecard index of the currently rendered tables
let touchTipGuard = false;
function attachTip(td, etype, met, c) {
  td.dataset.tip = '1';
  td.tabIndex = 0;   // roving normalization happens after rows are appended
  const tipHTML = () => {
    const mm = metricMeta(met);
    const B = state.baseline;
    const ref = c.kind === 'event' ? (idx0(etype, met, null) || {})[B]
      : (c.kind === 'lead' ? (idx0(etype, met, c.lead) || {})[B] : null);
    const model = td.dataset.model || undefined;
    /* The baseline value beside a cell: from the same (metric, lead) group, or
       handed in by a caller whose value is a mean over leads (the overview
       matrix), which has no single group to look up. */
    const refMean = ref ? ref.mean : (Number.isFinite(c.refMean) ? c.refMean : null);
    const rows = [`<div class="t-row"><span>mean value</span><b>${fmtVal(met, c.mean)} ${esc(mm.units)}</b></div>`];
    if (refMean !== null && refMean !== undefined && model !== B) {
      rows.push(`<div class="t-row"><span>${displayModelHtml(B)}</span><b>${fmtVal(met, refMean)} ${esc(mm.units)}</b></div>`);
    }
    if (c.rel !== null && c.rel !== undefined && Number.isFinite(c.rel) && model !== B) {
      rows.push(`<div class="t-row"><span>vs ${displayModelHtml(B)}</span><b class="t-rel ${c.rel >= 0 ? 'pos' : 'neg'}">${c.rel >= 0 ? '+' : ''}${c.rel.toFixed(1)}%</b></div>`);
    } else if (model !== B) {
      rows.push(`<div class="t-row"><span>vs ${displayModelHtml(B)}</span><b class="t-rel na">no skill ref</b></div>`);
    }
    if (Number.isFinite(c.lo) && Number.isFinite(c.hi) && c.kind !== 'mean') {
      rows.push(`<div class="t-row"><span>95% CI</span><b>${fmtVal(met, c.lo)} – ${fmtVal(met, c.hi)} ${esc(mm.units)}</b></div>`);
    }
    if (c.kind !== 'mean' && Number.isFinite(c.n)) {
      rows.push(`<div class="t-row"><span>n events</span><b>${c.n}</b></div>`);
    }
    /* track lanes: how many verified samples the error rests on vs the samples tried */
    if (TYPE_LEADS[etype] && c.kind === 'lead') {
      const src = missSource(met) || met;   // landfall values pair against landfall_miss
      const mc = (idx0(etype, src, c.lead) || {})[model || state.baseline];
      if ((met === 'TrackMiss' || met === 'landfall_miss') && Number.isFinite(c.ns)) {
        rows.push(`<div class="t-row"><span>samples</span><b>${c.ns}</b></div>`);
      } else if (!TRACKER_FREE_METRICS.has(met) && mc && Number.isFinite(mc.ns) && Number.isFinite(c.ns)) {
        rows.push(`<div class="t-row"><span>${src === 'landfall_miss' ? 'landfall-paired' : 'tracked'}</span><b>${c.ns} of ${mc.ns} samples</b></div>`);
      }
      if (c.greyed) {
        rows.push(`<div class="t-row t-small"><span>miss rate</span><b class="t-warn">above ${Math.round(MISS_GREY * 100)}% \u00b7 greyed</b></div>`);
      }
    }
    /* small-sample cue (owner 2026-11-10): say it in the tooltip too, so the
       red edge never reads as a performance colour */
    if (c.small) {
      const nTxt = Number.isFinite(c.n) ? `${c.n} event${c.n === 1 ? '' : 's'}` : 'tiny';
      rows.push(`<div class="t-row t-small"><span>sample</span>`
        + `<b class="t-warn">${nTxt} \u00b7 below ${minSample()}</b></div>`);
    }
    rows.push(`<div class="t-row t-dir"><span>${mm.dir === 'zero' ? 'closer to 0 is better' : mm.higher_is_better ? 'higher is better' : 'lower is better'}</span></div>`);
    if (c.kind === 'mean' && Number.isFinite(c.nLeads)) {
      rows.push(`<div class="t-row"><span>over leads</span><b>${c.nLeads}</b></div>`);
    }
    const who = td.dataset.model ? ` · <b>${displayModelHtml(td.dataset.model)}</b>` : '';
    return `<div class="t-title">${esc(mm.label)}${mm.units ? ` [${esc(mm.units)}]` : ''} · ${esc(TYPE_META[etype].short)}${who}` +
      (c.kind === 'mean' ? ' · mean of visible leads'
        : c.kind === 'event' ? ' · event-level'
        : ` · lead ${leadLabel(c.lead)}`) + `</div>` + rows.join('')
      + (c.laneNote ? `<div class="t-note">${esc(c.laneNote)}</div>` : '');
  };
  const showAt = (x, y) => {
    tipEl = $('#tooltip');
    tipEl.innerHTML = tipHTML();
    tipEl.classList.remove('hidden');
    moveTip({ clientX: x, clientY: y });
  };
  /* DR2 B1-touch v2 (design-review root-cause log): the compat mouseout of a
     tap fires AFTER click, re-hiding what click/focus just showed. Suppress
     the mouse/blur hide paths for a short window after any non-mouse
     pointerdown; tap-away still dismisses via document pointerdown capture. */
  td.addEventListener('pointerdown', (ev) => {
    if (ev.pointerType && ev.pointerType !== 'mouse') lastTouchTs = Date.now();
  }, true);
  td.addEventListener('mouseover', (ev) => {
    if (Date.now() - lastTouchTs < 800) return;
    showAt(ev.clientX, ev.clientY);
  });
  td.addEventListener('mousemove', (ev) => {
    if (Date.now() - lastTouchTs < 800) return;
    moveTip(ev);
  });
  td.addEventListener('mouseout', () => {
    if (Date.now() - lastTouchTs < 800) return;
    tipEl && tipEl.classList.add('hidden');
  });
  td.addEventListener('focus', () => {
    const r = td.getBoundingClientRect();
    showAt(r.left + r.width / 2, r.bottom);
  });
  td.addEventListener('blur', () => {
    if (Date.now() - lastTouchTs < 800) return;
    tipEl && tipEl.classList.add('hidden');
  });
  td.addEventListener('click', () => {
    const r = td.getBoundingClientRect();
    showAt(r.left + r.width / 2, r.bottom);
  });
  td.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
    const parent = td.parentElement;
    if (!parent) return;
    const cells = [...parent.children].filter((x) => x.dataset.tip);
    const j = cells.indexOf(td) + (ev.key === 'ArrowRight' ? 1 : -1);
    if (cells[j]) {
      td.tabIndex = -1; cells[j].tabIndex = 0; cells[j].focus();
      ev.preventDefault();
    }
  });
  const mm = metricMeta(met);
  const parts = [td.dataset.model ? displayModelName(td.dataset.model) : TYPE_META[etype].short, mm.label,
    `${fmtVal(met, c.mean)}${mm.units ? ' ' + mm.units : ''}`];
  if (Number.isFinite(c.n) && c.kind !== 'mean') parts.push('n ' + c.n);
  if (c.greyed) parts.push('miss rate > 50%');
  td.setAttribute('aria-label', parts.join(', '));
  if (!touchTipGuard) {
    touchTipGuard = true;   // tap outside any cell dismisses (touch)
    document.addEventListener('pointerdown', (e) => {
      if (tipEl && !e.target.closest('[data-tip]')) tipEl.classList.add('hidden');
    }, true);
  }
}
function idx0(etype, met, lead) {
  // cheap re-lookup of the current scorecard index for the tooltip
  return tooltipIdx ? tooltipIdx.get(`${etype}|${met}|${lead}`) : null;
}
function moveTip(ev) {
  if (!tipEl) return;
  const pad = 14;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  const r = tipEl.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
  x = Math.max(8, x); y = Math.max(8, y);   // phone width: the flipped tip must not leave the viewport
  tipEl.style.left = x + 'px';
  tipEl.style.top = y + 'px';
}

/* ------------------------------------------------------------------ */
/* skill-vs-lead charts                                                */
/* ------------------------------------------------------------------ */
function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  return {
    text: cs.getPropertyValue('--text-dim').trim() || '#888',
    grid: cs.getPropertyValue('--border').trim() || '#333',
    accent: cs.getPropertyValue('--accent-link').trim() || cs.getPropertyValue('--accent').trim() || '#4f8ff7',
  };
}

function baseLayout(title, mode) {
  const t = themeColors();
  const layout = {
    height: 240, margin: { l: 52, r: 10, t: 6, b: 34 },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { size: 11, color: t.text },
    xaxis: { gridcolor: t.grid, zeroline: false, title: { text: 'lead time', font: { size: 10.5 } } },
    yaxis: { gridcolor: t.grid, zeroline: mode === 'rel', zerolinecolor: t.accent },
    showlegend: false, hovermode: 'closest',
  };
  if (title) layout.yaxis.title = { text: title, font: { size: 10.5 } };
  return layout;
}
const PLOTLY_CFG = { displayModeBar: false, responsive: true };

/* numeric x axis in lead DAYS with clean per-bin ticks (h -> h/24).
   Ticks are thinned to the available pixel width so sub-day bins
   (6h/12h/18h) never collide on narrow cards; hover still shows the exact bin. */
function numericLeadAxis(layout, leadsH, widthPx) {
  const xs = leadsH.map((h) => h / 24);
  const span = Math.max(xs[xs.length - 1] - xs[0], 0.5) + 0.8;   // padded range
  const pxPerDay = ((widthPx || 560) - 70) / span;               // ~l/r margins
  const minDays = Math.min(1.05, 30 / pxPerDay);                 // ~30px per label
  const keep = [0];
  for (let i = 1; i < xs.length; i++)
    if (xs[i] - xs[keep[keep.length - 1]] >= minDays) keep.push(i);
  if (keep[keep.length - 1] !== xs.length - 1) {
    if (keep.length > 1 && xs[xs.length - 1] - xs[keep[keep.length - 1]] < minDays) keep.pop();
    keep.push(xs.length - 1);
  }
  layout.xaxis.type = 'linear';
  layout.xaxis.tickmode = 'array';
  layout.xaxis.tickvals = keep.map((i) => xs[i]);
  layout.xaxis.ticktext = keep.map((i) => leadLabel(leadsH[i]));
  layout.xaxis.ticks = 'outside';
  layout.xaxis.tickangle = 0;
  layout.xaxis.range = [xs[0] - 0.4, xs[xs.length - 1] + 0.4];
}

/* M1 (DR2): Plotly (~3.5MB) loads lazily on first chart render, not at boot.
   The dashboard is fully interactive with just Leaflet. */
let plotlyLoading = null;
function ensurePlotly() {
  if (typeof Plotly !== 'undefined') return Promise.resolve();
  if (!plotlyLoading) {
    plotlyLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'vendor/plotly.min.js';
      s.onload = resolve;
      s.onerror = () => { plotlyLoading = null; reject(new Error('plotly load failed')); };
      document.head.appendChild(s);
    });
  }
  return plotlyLoading;
}

function renderSkillCharts() {
  const host = $('#skill-charts');
  host.innerHTML = '';
  // Plotly measures width at newPlot time: skip while the fold is collapsed and
  // rebuild on first open (toggle listener in boot).
  const fold = $('#skill-details');
  if (fold && !fold.open) { state.skillDirty = true; return; }
  state.skillDirty = false;
  if (typeof Plotly === 'undefined') { ensurePlotly().then(() => renderSkillCharts()).catch(() => {}); return; }
  const mode = state.chartMode;
  const pending = [];
  const rows = scRows();   // region-aware rows (same accessor as the tables)

  for (const etype of activeTypes()) {
    const metrics = metricsFor(etype);
    if (!metrics.length) continue;
    const block = el('div', 'etype-block');
    block.appendChild(el('h3', '',
      typeChipHtml(etype) + 'mean over events'));
    const grid = el('div', '');
    grid.id = 'skill-grid-' + etype;
    block.appendChild(grid);
    host.appendChild(block);

    let shown = 0;
    metrics.forEach((met) => {
      const traces = [];
      state.allModels.forEach((model, mIdx) => {
        if (!state.models.has(model)) return;
        const pts = rows
          .filter((r) => r.event_type === etype && r.metric === met && r.model === model
            && r.lead_time_hours !== null && Number.isFinite(r.mean_value))
          .sort((a, b) => a.lead_time_hours - b.lead_time_hours);
        if (!pts.length) return;
        const refByLead = {};
        if (mode === 'rel') for (const r of rows)
          if (r.event_type === etype && r.metric === met && r.model === state.baseline) refByLead[r.lead_time_hours] = r.mean_value;
        const ys = pts.map((p) => mode === 'rel'
          ? relSkill(p.mean_value, refByLead[p.lead_time_hours], met)   // null when no reference → line gap
          : p.mean_value);
        traces.push({
          x: pts.map((p) => p.lead_time_hours / 24), y: ys,
          customdata: pts.map((p) => leadLabel(p.lead_time_hours)),
          name: displayModelName(model), type: 'scatter', mode: 'lines+markers',
          hovertemplate: '%{fullData.name} · lead %{data.customdata}<br>%{y:.2f}<extra></extra>',
          line: { color: modelColor(model, mIdx), width: model === state.baseline ? 2.4 : 1.8 },
          marker: { size: 4 },
        });
      });
      if (!traces.length) return;   // e.g. event-level-only metrics (null leads)
      const card = el('div', 'chart-card');
      card.appendChild(el('h4', '', esc(metricMeta(met).label)));
      const div = el('div', 'chart');
      card.appendChild(div);
      grid.appendChild(card);

      const binsX = [...new Set(state.allLeads)].sort((a, b) => a - b);
      const layout = baseLayout(mode === 'rel' ? '% improvement vs ' + displayModelName(state.baseline) : `${metricMeta(met).label}${metricMeta(met).units ? ' [' + metricMeta(met).units + ']' : ''}`, mode);
      if (shown === 0) {
        layout.showlegend = true;
        layout.legend = { orientation: 'h', x: 0, y: 1.14, xanchor: 'left', yanchor: 'bottom',
                          font: { size: 10.5 }, itemsizing: 'constant', itemwidth: 28 };
        layout.margin.t = 46;
      }
      shown += 1;
      layout.xaxis.tickangle = 0;
      pending.push({ div, traces, layout, binsX });
    });
  }
  // Plot AFTER the grid has its final track count: measuring a card mid-append
  // sees the auto-fit grid with fewer tracks (full width) and thins ticks wrong.
  for (const { div, traces, layout, binsX } of pending) {
    numericLeadAxis(layout, binsX, div.getBoundingClientRect().width || 560);
    Plotly.newPlot(div, traces, layout, PLOTLY_CFG);
  }
}

/* ------------------------------------------------------------------ */
/* events: map + table + detail                                        */
/* ------------------------------------------------------------------ */
/* coarse region label from a bbox centroid (Windborne-style "over the West"
   copy without a gazetteer; deliberately vague but truthful) */
function eventCentroid(ev) {
  const [lmin, lmax, onmin, onmax] = ev.bbox;
  const lat = (lmin + lmax) / 2;
  let lon;
  if (onmin <= onmax) lon = (onmin + onmax) / 2;
  else lon = ((onmin + onmax + 360) / 2 + 180) % 360 - 180;   // wrapped bbox
  return [lat, lon];
}
function regionName(lat, lon) {
  if (lat > 66) return 'the high Arctic';
  if (lat < -55) return 'the Southern Ocean';
  if (lon >= -170 && lon < -50) {
    if (lat > 45) return 'North America';
    if (lat > 25) return 'the northern Americas';
    if (lat > 12) return 'Central America and the Caribbean';
    if (lat > -6) return 'northern South America';
    return 'South America';
  }
  if (lon >= -50 && lon < -20) return lat > 20 ? 'the North Atlantic' : 'the tropical Atlantic';
  if (lon >= -20 && lon < 45) {
    if (lat > 60) return 'northern Europe';
    if (lat > 36) return lat > 48 ? 'Europe' : 'the Mediterranean';
    if (lat > 12) return 'North Africa';
    if (lat > -12) return 'equatorial Africa';
    return 'southern Africa';
  }
  if (lon >= 45 && lon < 150) {
    if (lat > 55) return 'Siberia';
    if (lat > 35) return lat > 45 ? 'Central/North Asia' : 'East Asia';
    if (lat > 8) return 'South Asia';
    if (lat > -10) return 'maritime Southeast Asia';
    if (lat > -40) return 'southern Indonesia and Australia';
    return 'the Australian sector of the Southern Ocean';
  }
  if (lon >= 150 || lon < -170) {
    if (lat > 30) return 'the North Pacific';
    if (lat > 0) return 'the tropical Pacific';
    return 'the South Pacific';
  }
  return 'open ocean';
}
/* Named place for a centroid: Natural Earth 110m countries (public domain),
   ported from the legacy dashboard's label_regions.py (notes/legacy_review.md).
   Lazy-loaded; ocean misses and load failure fall back to regionName bands. */
let COUNTRIES = null, countriesPromise = null;
function loadCountries() {
  if (!countriesPromise) {
    countriesPromise = fetch('geo/countries.json')
      .then((r) => (r.ok ? r.json() : []))
      .then((c) => { COUNTRIES = c; return c; })
      .catch(() => { COUNTRIES = []; });
  }
  return countriesPromise;
}
function inRing(ring, x, y) {           // ray-cast, even-odd
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function countryAt(lat, lon) {
  if (!COUNTRIES) return null;
  for (const [name, cont, polys] of COUNTRIES) {
    for (const rings of polys) {
      const bb = ringBBox(rings[0]);
      if (lon < bb[0] || lon > bb[2] || lat < bb[1] || lat > bb[3]) continue;
      let inPoly = false;
      for (let k = 0; k < rings.length; k++) {
        if (inRing(rings[k], lon, lat) === (k === 0)) { inPoly = true; } else { inPoly = false; break; }
      }
      if (inPoly) return [name, cont];
    }
  }
  return null;
}
const _bbCache = new WeakMap();
function ringBBox(ring) {
  let bb = _bbCache.get(ring);
  if (!bb) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const [x, y] of ring) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    bb = [x0, y0, x1, y1]; _bbCache.set(ring, bb);
  }
  return bb;
}
function placeName(lat, lon) {
  const hit = countryAt(lat, lon);
  if (!hit) return regionName(lat, lon);
  let [name, cont] = hit;
  // Natural Earth files all of Russia under Europe; east of the Urals it reads wrong (Bering AR: "Russia, Europe")
  if (name === 'Russia' && lon >= 60 && lon <= 180) cont = 'Asia';
  return name === cont ? name : `${name}, ${cont}`;
}
/* Region label anchored where the event actually sits on land, not at the
   raw bbox mid-longitude (guardian-design 2026-11-08): a dateline-crossing
   AR used to be stamped "Russia" because its wrapped bbox center grazed
   Chukotka. Order: 1) landfall-day (else anchor/last-day) mask centroid
   from the detail viz; 2) bbox sample-grid majority vote in UNWRAPPED
   longitude space (ties resolve east — the AR landfall edge); 3) the vague
   centroid band. Antimeridian-safe: all lon math runs unwrapped and is
   normalized to -180..180 only at the end. */
const normLon = (l) => ((l + 180) % 360 + 360) % 360 - 180;
function maskCentroid(rows, lats, lons) {      // rows: '0/1' strings, lats/lons: arrays
  let n = 0, sLat = 0, sLon = 0, ref = null;
  for (let j = 0; j < rows.length; j++) {
    const row = rows[j]; if (!row || !Number.isFinite(lats[j])) continue;
    for (let i = 0; i < row.length && i < lons.length; i++) {
      if (row[i] !== '1' || !Number.isFinite(lons[i])) continue;
      let lon = lons[i];
      if (ref === null) ref = lon;
      while (lon - ref > 180) lon -= 360;
      while (ref - lon > 180) lon += 360;
      n++; sLat += lats[j]; sLon += lon;
    }
  }
  return n ? [sLat / n, normLon(sLon / n)] : null;
}
function regionFromViz(ev, viz) {
  if (!viz || !viz.obs || !viz.grid
      || !Array.isArray(viz.grid.lat) || !Array.isArray(viz.grid.lon)) return null;
  const d = (ev && ev.details) || {};
  let rows = null;
  if (Array.isArray(viz.obs.daily)) {                    // schema ≤2: per-day masks
    let di = -1;
    if (viz.window && Array.isArray(viz.window.days)) {
      di = d.landfall_time ? viz.window.days.indexOf(d.landfall_time) : -1;
      if (di < 0 && viz.obs.anchor_day) di = viz.window.days.indexOf(viz.obs.anchor_day);
    }
    if (di < 0) di = viz.obs.daily.length - 1;           // mature end = landfall side
    rows = viz.obs.daily[di];
    rows = rows && rows.split('/');
  } else if (typeof viz.obs.mask === 'string') {         // schema 3 (AR lane): single anchor-day mask
    const nx = viz.grid.lon.length;
    if (!viz.obs.mask.includes('/')) {                   // flat 0/1 string — chunk into rows
      if (viz.obs.mask.length % nx) return null;
      rows = viz.obs.mask.match(new RegExp('.{' + nx + '}', 'g'));
    } else rows = viz.obs.mask.split('/');
  }
  if (!rows) return null;
  return maskCentroid(rows, viz.grid.lat, viz.grid.lon);
}
function regionFromBbox(ev) {
  if (!COUNTRIES || !Array.isArray(ev.bbox)) return null;
  const [s, n, w, e] = ev.bbox;
  const span = e >= w ? e - w : e + 360 - w;
  if (!(span > 0) || span >= 350) return null;       // full-band: honest centroid band
  const C = 7, R = 5, acc = new Map();
  for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) {
    const lat = s + (n - s) * (r + 0.5) / R, lonW = w + span * (c + 0.5) / C;
    const hit = countryAt(lat, normLon(lonW));
    if (!hit) continue;
    let a = acc.get(hit[0]);
    if (!a) { a = { cont: hit[1], votes: 0, lat: 0, lon: 0, east: -1e9 }; acc.set(hit[0], a); }
    a.votes++; a.lat += lat; a.lon += lonW; if (lonW > a.east) a.east = lonW;
  }
  if (!acc.size) return null;
  let best = null;
  for (const [name, a] of acc) {
    if (!best || a.votes > best.a.votes || (a.votes === best.a.votes && a.east > best.a.east)) best = { name, a };
  }
  return best;
}
function eventRegion(ev, viz) {
  const pt = regionFromViz(ev, viz);
  if (pt) return placeName(pt[0], pt[1]);
  const m = regionFromBbox(ev);
  if (m) {
    const lon = normLon(m.a.lon / m.a.votes);
    let name = m.name, cont = m.a.cont;
    if (name === 'Russia' && lon >= 60 && lon <= 180) cont = 'Asia';   // placeName parity
    return name === cont ? name : `${name}, ${cont}`;
  }
  const c = eventCentroid(ev);                       // unwrapped midpoint, normalized
  return regionName(c[0], c[1]);
}
/* one short label-form line for the detail header (labels, not sentences:
   "Atmospheric river \u00b7 North America \u00b7 Aug 2 \u2013 Sep 1 \u00b7 peak IVT 2972") */
function storyLabel(ev, viz) {
  const d = ev.details || {};
  const region = eventRegion(ev, viz);
  const short = (iso) => {
    const t = fmtDate(iso);
    return t.replace(/, \d{4}$/, '');
  };
  const parts = [(TYPE_META[ev.event_type] || {}).short || ev.event_type,
    region, `${short(ev.start)} \u2013 ${short(eventLastDay(ev))}`];
  if (ev.event_type === 'tropical_cyclone') {
    // track lane (tc 2026-11-16): the storm's own numbers, not ERA5 daily means
    if (d.name && d.name !== 'Unnamed') parts.push(d.name);
    if (Number.isFinite(ev.peak_value)) {
      parts.push(Math.round(ev.peak_value) + ' kt' + (d.peak_category ? ' ' + d.peak_category : ''));
    }
    if (Number.isFinite(d.min_pressure_hpa)) parts.push(Math.round(d.min_pressure_hpa) + ' hPa');
    if (d.basin_name) parts.push(d.basin_name);
  } else if (ev.event_type === 'atmospheric_river') {
    if (Number.isFinite(ev.peak_value)) parts.push('peak IVT ' + Math.round(ev.peak_value) + ' kg m⁻¹ s⁻¹');
    // landfall_days can exceed the event length upstream (G-28): print the landfall date instead
    const lf = parseISO(d.landfall_time);
    if (lf) parts.push('landfall ' + lf.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }));
  } else if (ev.event_type === 'flood') {
    // the flood peak is a TRAILING 72 h accumulation in mm (details.peak_value_units)
    if (Number.isFinite(ev.peak_value)) parts.push('peak 72 h ' + Math.round(ev.peak_value) + ' mm');
    if (Number.isFinite(d.peak_daily_mm)) parts.push('peak day ' + Math.round(d.peak_daily_mm) + ' mm');
    if (Number.isFinite(d.peak_area_km2)) parts.push(Math.round(d.peak_area_km2).toLocaleString('en-US') + ' km² at peak');
  } else if (ev.event_type === 'midlat_cyclone') {
    // track lane (mlc 2026-11-17): depth, not temperature, is the headline
    if (Number.isFinite(ev.peak_value)) parts.push(Math.round(ev.peak_value) + ' hPa centre');
    if (Number.isFinite(d.displacement_km)) parts.push(Math.round(d.displacement_km / 111.195) + '\u00b0 travelled');
    if (d.bomb) parts.push('bomb \u00d7' + (Number.isFinite(d.bomb_ratio) ? d.bomb_ratio.toFixed(1) : '1'));
  } else {
    // peak is the ERA5 daily-mean peak (see About) — say so (G-27)
    if (Number.isFinite(ev.peak_value)) parts.push('peak daily-mean ' + ev.peak_value.toFixed(1) + ' °C');
    // the map badge reads viz.obs.max_consec; merged events keep the core part's
    // details.max_consecutive_days, so prefer the viz number once it is loaded (G-28)
    const run = viz && viz.obs && Number.isFinite(viz.obs.max_consec) ? viz.obs.max_consec : d.max_consecutive_days;
    if (Number.isFinite(run)) parts.push(run + 'd max run');
  }
  return parts.filter(Boolean).join(' \u00b7 ');
}

/* shareable deep links in the URL hash: #event=<id>&lead=24h (legacy
   ?event= query params are still read at boot for old links)          */
/* a hand-edited / truncated link ('#event=%E0%A4%A') must never throw —
   decodeURIComponent on malformed escapes used to abort boot() entirely */
function safeDecode(s) {
  try { return decodeURIComponent(s); } catch (e) { return s; }
}
function hashParams() {
  const out = {};
  for (const kv of (location.hash || '').replace(/^#/, '').split('&')) {
    if (!kv) continue;
    const i = kv.indexOf('=');
    if (i > 0) out[kv.slice(0, i)] = safeDecode(kv.slice(i + 1));
  }
  return out;
}
/* calm, self-clearing status line for links that no longer resolve (event ids
   and weekly snapshots rotate, so shared links rot) */
function routeNote(msg) {
  let n = document.getElementById('route-note');
  if (!n) {
    n = el('p', 'sec-note');
    n.id = 'route-note';
    n.setAttribute('role', 'status');
    const m = document.querySelector('main');
    m.insertBefore(n, m.firstChild);
  }
  n.textContent = msg;
  clearTimeout(routeNote.t);
  routeNote.t = setTimeout(() => n.remove(), 9000);
}
function setHashParam(k, v) {
  try {
    const p = hashParams();
    if (v == null || v === '') delete p[k]; else p[k] = String(v);
    const h = Object.entries(p).map(([a, b]) => `${a}=${encodeURIComponent(b)}`).join('&');
    const u = new URL(location.href);
    u.searchParams.delete('event'); // migrate legacy links
    if (h) u.hash = h; else u.hash = '';
    history.replaceState(null, '', u);
  } catch (e) { /* file:// or sandboxed */ }
}

/* ------------------------------------------------------------------ */
/* navigation (A1, owner 2026-11-07): list ⇄ event detail is a real    */
/* history transition. Opening an event PUSHES a #event=… entry        */
/* (browser Back returns to the list); close ✕ and Esc push the list   */
/* state; lead/day tweaks REPLACE so the deep-linkable URL never      */
/* spam-drowns history. popstate + hashchange are the single sync      */
/* path — hash-less pushes never fire them, so no echo loops.          */
/* ------------------------------------------------------------------ */
function locationFor(params) {
  const u = new URL(location.href);
  u.searchParams.delete('event'); // migrate legacy ?event= links
  const h = Object.entries(params).filter(([, v]) => v != null && v !== '')
    .map(([a, b]) => `${a}=${encodeURIComponent(b)}`).join('&');
  u.hash = h ? `#${h}` : '';
  return u;
}
function syncUrl(push) {
  const params = {};
  if (state.selectedEvent) params.event = state.selectedEvent;
  if (state.detailLead) params.lead = `${state.detailLead}h`;
  if (state.detailLayers) params.lyr = state.detailLayers;
  if (state.detailDay != null) params.day = state.detailDay;
  if (state.region && state.region !== 'global') params.region = state.region;
  if (state.tab && state.tab !== 'all') params.tab = state.tab;   // type filter is shareable state
  // the active view rides the hash; the default (Events) keeps it clean, and a
  // detail is its own view — the rail's choice returns when it closes
  if (state.view === 'scorecard' && !state.selectedEvent) params.view = 'scorecard';
  // the merged global map's #map/#layers/#sel ride along so Back from a
  // detail restores the slider/selection state (URL is the source of truth)
  if (window.MergedMap) Object.assign(params, MergedMap.urlParams());
  try {
    const s = { event: state.selectedEvent || null };
    const u = locationFor(params);
    if (push) history.pushState(s, '', u); else history.replaceState(s, '', u);
  } catch (e) { /* file:// or sandboxed */ }
}

/* The merged map recovered a wrapped bbox for a degenerate full-band event:
   patch the catalog's copy (see mergedmap.js repairBboxes) */
const bboxFixes = {};   // the merged map self-starts, so it can resolve BEFORE state.events is loaded
window.applyBboxFix = function (id, bbox) {
  bboxFixes[id] = bbox;
  const ev = state.events.find((e) => e.id === id);
  if (!ev) return;
  ev.bbox = bbox;
  delete ev._region;
  renderEventTable();
};

/* Human name for a list row / detail title: "Heat wave · Iran". Region comes
   from the bbox vote once the country table is loaded (vague band before). */
function eventName(ev, viz) {
  let t = (TYPE_META[ev.event_type] || {}).short || ev.event_type;
  // the track lane names its storm (IBTrACS NAME); the area lanes stay anonymous
  const nm = ev.details && ev.details.name;
  if (ev.event_type === 'tropical_cyclone' && nm && nm !== 'Unnamed') t += ' ' + nm;
  const region = viz ? eventRegion(ev, viz) : eventNameRegion(ev);
  return `${t} \u00b7 ${String(region).split(',')[0]}`;
}
/* Cached bbox/country vote for a list row (the viz, when loaded, is sharper). */
function eventNameRegion(ev) {
  if (!ev._region || ev._regionRough !== !COUNTRIES) {
    ev._region = eventRegion(ev, null); ev._regionRough = !COUNTRIES;
  }
  return ev._region;
}

/* Band 1 title (contract §3): the place, or the storm's own name — NEVER the
   event type, which sits beside it in #detail-type-slot. "Heat wave · Heat
   wave · S. Sudan" was the header the owner sent back three times. */
function eventPlaceName(ev, viz) {
  const d = ev.details || {};
  if (ev.event_type === 'tropical_cyclone' && d.name && d.name !== 'Unnamed') return d.name;
  return String(viz ? eventRegion(ev, viz) : eventNameRegion(ev)).split(',')[0];
}

/* Band 1 fact pills: four labelled values — dates+length, the peak, the place,
   the source/status — identical in shape for all seven lanes. */
function detailMetaChips(ev, viz) {
  const d = ev.details || {};
  const short = (iso) => fmtDate(iso).replace(/, \d{4}$/, '');
  const days = eventDurationDays(ev);
  const num = (v) => (Number.isFinite(v)
    ? (Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1)) : null);
  const extra = [];
  let peakLabel = 'Peak', peakValue = null;
  switch (ev.event_type) {
    case 'tropical_cyclone':
      peakLabel = 'Peak wind';
      peakValue = num(ev.peak_value) === null ? null
        : `${num(ev.peak_value)} kt${d.peak_category ? ' ' + d.peak_category : ''}`;
      if (Number.isFinite(d.min_pressure_hpa)) extra.push(`${Math.round(d.min_pressure_hpa)} hPa`);
      break;
    case 'midlat_cyclone':
      peakLabel = 'Min pressure';
      peakValue = num(ev.peak_value) === null ? null : `${num(ev.peak_value)} hPa`;
      if (Number.isFinite(d.displacement_km)) {
        extra.push(`${Math.round(d.displacement_km).toLocaleString('en-US')} km travelled`);
      }
      if (d.bomb) extra.push(`bomb \u00d7${Number.isFinite(d.bomb_ratio) ? d.bomb_ratio.toFixed(1) : '1'}`);
      break;
    case 'atmospheric_river': {
      peakLabel = 'Peak IVT';
      const u = d.peak_value_units || 'kg m\u207b\u00b9 s\u207b\u00b9';
      peakValue = num(ev.peak_value) === null ? null : `${num(ev.peak_value)} ${u}`;
      const lf = parseISO(d.landfall_time);
      if (lf) extra.push('landfall ' + lf.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }));
      break;
    }
    case 'flood':
      peakLabel = 'Peak 72 h';
      peakValue = num(ev.peak_value) === null ? null : `${num(ev.peak_value)} mm`;
      if (Number.isFinite(d.peak_daily_mm)) extra.push(`${Math.round(d.peak_daily_mm)} mm peak day`);
      if (Number.isFinite(d.peak_area_km2)) extra.push(`${Math.round(d.peak_area_km2).toLocaleString('en-US')} km\u00b2 at peak`);
      break;
    default:
      peakLabel = 'Peak daily-mean';
      peakValue = num(ev.peak_value) === null ? null : `${num(ev.peak_value)} \u00b0C`;
      // the map badge reads viz.obs.max_consec; merged events keep the core part's
      // details.max_consecutive_days, so prefer the viz number once it is loaded (G-28)
      {
        const run = viz && viz.obs && Number.isFinite(viz.obs.max_consec)
          ? viz.obs.max_consec : d.max_consecutive_days;
        if (Number.isFinite(run)) extra.push(`${run} d max run`);
      }
  }
  const placeFull = String(viz ? eventRegion(ev, viz) : eventNameRegion(ev));
  const title = eventPlaceName(ev, viz);
  /* The title already IS the place, so the pill only earns its seat with what the
     title left out — the wider region. A TC is titled by its storm name, so its
     basin always belongs here. */
  let placeLabel = ev.event_type === 'tropical_cyclone' ? 'Basin' : 'Region';
  const placeVal = ev.event_type === 'tropical_cyclone' ? placeFull
    : (placeFull.startsWith(title + ',') ? placeFull.slice(title.length + 1).trim() : '');
  const source = ev.event_type === 'tropical_cyclone'
    ? [d.source || 'IBTrACS', d.wind_basis].filter(Boolean).join(' \u00b7 ')
    : ev.event_type === 'midlat_cyclone' ? (d.source || 'ERA5 MSLP')
      : ev.event_type === 'flood' ? (d.source_label || 'AI-estimated rain \u00b7 Google PARDIG')
        : ev.event_type === 'atmospheric_river' ? 'ERA5 \u00b7 EWB AR mask'
          : 'ERA5 daily mean';
  const chips = [['Dates', `${short(ev.start)} \u2013 ${short(eventLastDay(ev))}${days ? ` \u00b7 ${days} d` : ''}`]];
  if (peakValue) chips.push([peakLabel, [peakValue].concat(extra).join(' \u00b7 ')]);
  if (placeVal) chips.push([placeLabel, placeVal]);
  chips.push(['Source', source]);
  return chips.map(([k, v]) =>
    `<span class="det-meta-chip"><span class="k">${esc(k)}</span>${esc(v)}</span>`).join('');
}

/* One paint for the whole Band-1 header, re-run whenever a sharper place name
   arrives (country table, then the viz landfall mask). */
function paintDetailHeader(ev) {
  const viz = state._vizFor === ev.id ? state._viz : null;
  const h2 = $('#detail-title');
  const place = eventPlaceName(ev, viz);
  h2.textContent = place ? place.charAt(0).toUpperCase() + place.slice(1) : '';
  h2.title = ev.title;
  const slot = $('#detail-type-slot');
  if (slot) slot.innerHTML = typeChipHtml(ev.event_type);
  const chips = $('#detail-chips');
  if (chips) chips.innerHTML = detailMetaChips(ev, viz);
  const story = $('#detail-story');
  if (story) story.textContent = storyLabel(ev, viz);   // story + map badge agree on max run
}

function matchesSearch(ev) {
  if (!state.search) return true;
  const q = state.search.toLowerCase();
  return (ev.title + ' ' + eventName(ev) + ' ' + ev.id + ' ' + (ev.details?.synoptic || '') + ' ' + ev.event_type).toLowerCase().includes(q);
}

const EVENT_TABLE_MAX_ROWS = 400;   // real runs detect 1000+ events; don't build 1500 DOM rows

function eventDurationDays(ev) {
  if (Number.isFinite(ev.details?.duration_days)) return ev.details.duration_days;
  const a = parseISO(ev.start), b = parseISO(ev.end);
  if (!a || !b) return null;
  return Math.max(1, Math.round((b - a) / 86400000));
}

/* ev.end is exclusive (last scored instant = end - 6 h; viz renders
   end - 1 min) — display the last ACTIVE day, matching the detector's own
   title and the viz frame range. Track lanes end mid-day (a cyclone ending at
   09:00 is still active that day), so step back one instant rather than one
   day; for the midnight-aligned heat/freeze/AR ends that is the same day. */
function eventLastDay(ev) {
  const b = parseISO(ev.end);
  if (!b) return ev.end;
  return new Date(b.getTime() - 1000).toISOString();
}

function renderEventTable() {
  const tbody = $('#event-table tbody');
  tbody.innerHTML = '';
  /* 'On map' narrows the list to the events the map paints at this very frame
     (the merged map reports them through window.onMapEvents), so the table and
     the picture on screen are the same set of events. */
  const onMap = state.evScope === 'map' ? new Set(mapRowState.active) : null;
  let n = 0, drawn = 0;
  for (const ev of state.events) {
    if (state.tab !== 'all' && ev.event_type !== state.tab) continue;
    if (!inRegion(ev)) continue;   // Region dropdown: list follows the region
    if (onMap && !onMap.has(ev.id)) continue;
    if (!matchesSearch(ev)) continue;
    n += 1;
    if (drawn >= EVENT_TABLE_MAX_ROWS) continue;   // count all, render a bounded prefix
    drawn += 1;
    const peakUnitFull = ev.details?.peak_value_units
      || (ev.event_type === 'atmospheric_river' ? 'kg m⁻¹ s⁻¹' : '°C');
    // DR2 minor: column-abbreviated unit so Peak isn't clipped at 1440; full unit in title
    const units = peakUnitFull;
    const peak = typeof ev.peak_value === 'number' && Number.isFinite(ev.peak_value)
      ? (Math.abs(ev.peak_value) >= 100 ? Math.round(ev.peak_value) : ev.peak_value.toFixed(1))
        + (units ? ` <span class="ev-unit">${esc(units)}</span>` : '')
      : '—';
    const tr = el('tr', state.selectedEvent === ev.id ? 'sel' : '');
    /* DR3: documented-exclusion events get the pair_status reason as a row
       tooltip — the row reads honest, not silently empty. */
    {
      const ents = Object.entries(state.pairStatus).filter(([k]) => k.startsWith(ev.id + '|'));
      if (ents.length && !ents.some(([, v]) => v.status === 'scored')) {
        const reasons = [...new Set(ents.map(([, v]) => friendlyReason(v.reason)).filter(Boolean))];
        tr.title = `Not scored — ${ents.length} models: ${reasons.join('; ')}`;
      }
    }
    /* B1 (DR2): rows were click-only — operable via keyboard now. */
    tr.tabIndex = 0;
    tr.dataset.id = ev.id;
    tr.setAttribute('role', 'link');   // accessible name = row text (label must contain visible text)
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectEvent(ev.id, false); }
    });
    const short = (iso) => fmtDate(iso).replace(/, \d{4}$/, '');
    const days = eventDurationDays(ev);
    tr.innerHTML =
      `<td><span class="type-ic" data-etype="${esc(ev.event_type)}" title="${esc(ev.event_type.replace('_', ' '))}">${typeIcon(ev.event_type)}</span></td>` +
      `<td><span class="ev-name">${esc(eventName(ev))}</span>` +
      `<span class="ev-sub">${short(ev.start)} \u2013 ${short(eventLastDay(ev))}${days ? ` \u00b7 ${days}d` : ''}` +
      `<span class="ev-live" hidden> \u00b7 on map</span></span></td>` +
      `<td class="num" title="${esc(peakUnitFull)}">${peak}</td>`;
    // list ⇄ map: pointing at a row outlines the event on the map and timeline
    tr.addEventListener('pointerenter', () => { if (window.MergedMap) MergedMap.preview(ev.id); });
    tr.addEventListener('pointerleave', () => { if (window.MergedMap) MergedMap.preview(null); });
    tr.addEventListener('focus', () => { if (window.MergedMap) MergedMap.preview(ev.id); });
    tr.addEventListener('blur', () => { if (window.MergedMap) MergedMap.preview(null); });
    tr.onclick = () => selectEvent(ev.id, false);
    tbody.appendChild(tr);
  }
  if (n > drawn) {
    const tr = el('tr', 'more-rows');
    tr.innerHTML = `<td></td><td colspan="2">… ${n - drawn} more — refine with the filter</td>`;
    tbody.appendChild(tr);
  } else if (n === 0) {
    // Owner 2026-11-07: empty lane = legitimate outcome; note + criteria tooltip.
    const tr = el('tr', 'lane-empty-row');
    const searching = !!(state.search && state.search.trim());
    const re = regionEntry();
    const broken = state.tab === 'all' ? null : laneStatusNote(state.tab);
    const note = onMap ? 'Nothing on the map in this frame \u2014 step the time, or pick All'
      : searching ? 'No events match your search'
      : broken ? broken
      : re && state.tab === 'all' ? `No events in ${re.label} this window`
      : re ? `No ${TYPE_META[state.tab] ? TYPE_META[state.tab].label.toLowerCase() : ''} in ${re.label} this window`
      : emptyLaneLine(state.tab);
    const crit = onMap ? (searching ? '' : 'The map paints the events alive at the selected time step and inside the view')
      : searching ? ''
      : re && !broken ? 'Events are assigned to regions by the centroid of their bounding box'
      : state.tab === 'all' ? 'Events must clear the detection criteria for their type in the 90-day window.' : TYPE_CRITERIA[state.tab];
    tr.innerHTML = `<td></td><td colspan="2"><span class="lane-empty"${crit ? ` title="${esc(crit)}"` : ''}>${esc(note)}${crit ? ' <span class="crit-mark" aria-hidden="true">ⓘ</span>' : ''}</span></td>`;
    tbody.appendChild(tr);
  }
  $('#event-count').textContent = `${n} event${n === 1 ? '' : 's'}`;
  syncEvScopeUI();
  markMapRows();
  /* M7 (DR2): same horizontal-scroll affordance as the scorecard tables */
  const evWrap = tbody.closest('.table-wrap');
  if (evWrap) {
    const updEvScroll = () => {
      const over = evWrap.scrollWidth - evWrap.clientWidth > 4;
      evWrap.classList.toggle('scrollable', over);
      evWrap.classList.toggle('at-end', over && evWrap.scrollLeft >= evWrap.scrollWidth - evWrap.clientWidth - 2);
    };
    requestAnimationFrame(updEvScroll);
    if (!evWrap.dataset.scrollHook) {
      evWrap.dataset.scrollHook = '1';
      evWrap.addEventListener('scroll', updEvScroll, { passive: true });
    }
    /* Ponytail 2026-11-08: `tbody.innerHTML=''` above resets inner scroll on
       EVERY mount, and async re-renders land after the fixed-timer restore in
       closeDetail — so apply the pending listReturn at the end of every mount
       (window scroll too; a full-page re-layout can reset it). Cleared on user
       input or when an event opens, so it can never hijack fresh browsing. */
    if (state.listReturn && !state.selectedEvent) {
      const pending = state.listReturn;
      requestAnimationFrame(() => {
        if (state.listReturn !== pending || state.selectedEvent) return;
        window.scrollTo(0, pending.y);
        evWrap.scrollLeft = pending.x;
        evWrap.scrollTop = pending.ty;
      });
      if (!evWrap.dataset.restoreHook) {
        evWrap.dataset.restoreHook = '1';
        ['wheel', 'touchstart', 'keydown'].forEach((evt) =>
          window.addEventListener(evt, () => { state.listReturn = null; },
            { passive: true, once: true }));
      }
    }
  }
}

/* list ⇄ map: the merged map reports which events are on screen at the
   current time step and which are selected; rows mirror that, and an 'On map'
   list follows the frame as it plays. */
let mapRowState = { active: [], sel: [] };
window.onMapEvents = (active, sel) => {
  sel = sel || [];   // mergedmap always passes both; the hook is public, so don't throw
  const scroll = sel.length && sel.join() !== mapRowState.sel.join();
  const changed = active.join() !== mapRowState.active.join();
  mapRowState = { active, sel };
  markMapRows(scroll);
  syncEvScopeUI();
  if (changed && state.evScope === 'map' && !state.selectedEvent) renderEventTable();
};
/* The scope toggle says what 'On map' would show right now, and keeps the
   pressed state after any re-render. */
function syncEvScopeUI() {
  const host = $('#ev-scope-toggle');
  if (!host) return;
  const cnt = $('#ev-map-count');
  if (cnt) cnt.textContent = String(mapRowState.active.length);
  host.querySelectorAll('[data-scope]').forEach((b) => {
    const on = b.dataset.scope === state.evScope;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  });
}
function setEvScope(scope) {
  const s = scope === 'map' ? 'map' : 'all';
  if (s === state.evScope) return;
  state.evScope = s;
  renderEventTable();
}
function markMapRows(scroll) {
  const act = new Set(mapRowState.active), sel = new Set(mapRowState.sel);
  let first = null;
  document.querySelectorAll('#event-table tbody tr[data-id]').forEach((tr) => {
    const id = tr.dataset.id, live = tr.querySelector('.ev-live');
    tr.classList.toggle('live', act.has(id));
    if (live) live.hidden = !act.has(id);
    tr.classList.toggle('picked', sel.has(id));
    if (sel.has(id) && !first) first = tr;
  });
  if (scroll && first) {                    // bring the picked row into the list's own viewport
    const wrap = first.closest('.table-wrap');
    const r = first.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    if (r.top < w.top + 30 || r.bottom > w.bottom) wrap.scrollTop += r.top - w.top - w.height / 3;
  }
}

/* per-event mini scorecard: model × metric (mean over leads; event-level
   metrics shown as a single value), coloured vs the baseline's own value for
   this event — same encoding as the main scorecard, no filters applied. */
function renderEventMini(ev) {
  const host = $('#detail-mini');
  if (!host) return;
  host.innerHTML = '';
  const rows = state.scores.filter((r) => r.event_id === ev.id);
  if (!rows.length) { host.classList.add('hidden'); return; }
  host.classList.remove('hidden');
  const B = state.baseline;
  // metric -> {kind, model -> value}
  const byMet = new Map();
  for (const r of rows) {
    if (!state.models.has(r.model)) continue;
    let g = byMet.get(r.metric);
    if (!g) { g = { byModel: {}, leadSums: {}, evLevel: {} }; byMet.set(r.metric, g); }
    if (!Number.isFinite(r.value)) continue;
    if (r.lead_time_hours === null || r.lead_time_hours === undefined) {
      g.evLevel[r.model] = r.value;
    } else {
      const s = (g.leadSums[r.model] = g.leadSums[r.model] || { s: 0, n: 0 });
      s.s += r.value; s.n += 1;
    }
  }
  // columns: metrics ordered by the global metric order for this type
  const metricOrder = metricsFor(ev.event_type);
  const metList = [...byMet.keys()].sort((a, b) => {
    const ia = metricOrder.indexOf(a), ib = metricOrder.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const val = (g, m) => (Object.keys(g.leadSums).length
    ? (g.leadSums[m] ? g.leadSums[m].s / g.leadSums[m].n : null)
    : (g.evLevel[m] !== undefined ? g.evLevel[m] : null));
  const models = (state.modelOrder && state.modelOrder.length ? state.modelOrder
    : state.allModels.filter((m) => state.models.has(m)))
    .filter((m) => metList.some((met) => val(byMet.get(met), m) !== null));
  if (!models.length || !metList.length) { host.classList.add('hidden'); return; }

  const wrap = el('div', 'table-wrap mini-wrap');
  const table = el('table', 'mini-table');
  const head = el('tr');
  head.appendChild(el('th', 'model-col', `this event · vs ${esc(displayModelName(B))}`));
  for (const met of metList) {
    const mm = metricMeta(met);
    const th = el('th', '', `${esc(mm.label)}${mm.units ? ` <span class="units">[${esc(mm.units)}]</span>` : ''}`);
    th.title = metricBlurb(met);
    head.appendChild(th);
  }
  table.appendChild(el('thead')).appendChild(head);
  const tbody = el('tbody');
  for (const model of models) {
    const tr = el('tr', model === B ? 'baseline-row' : '');
    tr.appendChild(el('td', 'model-col',
      `<span class="dot" style="--c:${modelColor(model, state.allModels.indexOf(model))}"></span>${displayModelHtml(model)}`));
    for (const met of metList) {
      const g = byMet.get(met);
      const v = val(g, model), ref = val(g, B);
      if (v === null) { tr.appendChild(el('td', 'cell empty', '—')); continue; }
      const rel = model === B ? null : relSkill(v, ref, met);
      const td = el('td', 'cell' + (model === B ? ' baseline' : ''), fmtVal(met, v));
      if (model !== B) td.style.background = cellColor(rel);
      td.title = `${displayModelName(model)}: ${fmtVal(met, v)}${rel !== null && Number.isFinite(rel)
        ? ` (${rel >= 0 ? '+' : ''}${rel.toFixed(1)}% vs ${displayModelName(B)})` : ''} — ${metricBlurb(met)}`;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
}

/* default model maps for the detail grid: top-3 by |rel skill| on THIS event
   (from scores), intersected with models that have viz data; fall back to the
   first 3 in viz order; baseline always in and never removable; ≤4 tiles. */
function pickDetailModels(ev, viz) {
  const vizModels = Object.keys(viz.models || {});
  const B = state.baseline;
  const byKey = new Map();   // "metric|lead" -> {model: value}
  for (const r of state.scores) {
    if (r.event_id !== ev.id || !Number.isFinite(r.value)) continue;
    const k = `${r.metric}|${r.lead_time_hours}`;
    let g = byKey.get(k);
    if (!g) { g = {}; byKey.set(k, g); }
    g[r.model] = r.value;
  }
  const rel = {};
  for (const [key, g] of byKey) {
    const met = key.split('|')[0];
    const ref = g[B];
    for (const m in g) {
      if (m === B || !Number.isFinite(g[m])) continue;
      const rs = relSkill(g[m], ref, met);
      if (rs === null || !Number.isFinite(rs)) continue;
      (rel[m] = rel[m] || { s: 0, n: 0 });
      rel[m].s += Math.abs(rs); rel[m].n += 1;
    }
  }
  let sel = Object.keys(rel)
    .sort((a, b) => (rel[b].s / rel[b].n) - (rel[a].s / rel[a].n))
    .filter((m) => vizModels.includes(m)).slice(0, 3);
  if (!sel.length) sel = vizModels.slice(0, 3);
  if (vizModels.includes(B) && !sel.includes(B)) sel.unshift(B);
  return sel.slice(0, 4);
}

/* event detail — observed map, chip-picked model maps, collapsed scores fold.
   nav.push: true (default) makes opening an event a history entry so Back
   returns to the list; popstate/hashchange sync passes push:false. */
function selectEvent(id, fromMap, nav) {
  nav = nav || { push: true };
  const wasOpen = !!state.selectedEvent && state.selectedEvent !== id;
  const openingFromList = !state.selectedEvent;
  if (wasOpen) { state.detailModels = null; state.detailLead = null; state.detailLayers = null; state.detailDay = null; state.detailChartsPending = null; }
  if (openingFromList) {
    /* A1: remember the list view (page + table scroll) so close/Back lands
       the owner exactly where they left it; filters live in state + the
       persistent search input, so they survive the view swap untouched. */
    const wrap = document.querySelector('#events-sec .table-wrap.tall');
    state.listReturn = { y: window.scrollY,
      x: wrap ? wrap.scrollLeft : 0, ty: wrap ? wrap.scrollTop : 0 };
  }
  state.selectedEvent = id;
  state.lastViewedEvent = id;   // its box stays highlighted on return to the list
  /* Ponytail MUST 2026-11-08: adopt the hash's lead BEFORE syncUrl rewrites
     the URL from state — otherwise a #lead=120h deep link is dropped and the
     event silently opens at leads[0] with the URL rewritten to the wrong lead. */
  if (state.detailLead == null) {
    const h = parseInt(hashParams().lead, 10);
    state.detailLead = Number.isFinite(h) ? h : null;
  }
  if (state.detailLayers == null) state.detailLayers = hashParams().lyr || null;
  if (state.detailDay == null) {
    const dd = parseInt(hashParams().day, 10);
    state.detailDay = Number.isFinite(dd) ? dd : null;
  }
  syncUrl(nav.push && openingFromList);
  const sec = $('#detail-sec');
  sec.classList.remove('hidden');
  // one primary view at a time (QA 2026-11-06): both list views yield to the detail
  document.body.classList.add('detail-open');
  applyView();
  const ev = state.events.find((e) => e.id === id);
  if (!ev) return;
  /* Band 1: title = the place or the storm, the type rides beside it as a chip,
     and the dates/peak/place/source are four labelled pills. The sentence-form
     story stays for screen readers only (zero visual repetition). */
  paintDetailHeader(ev);
  loadCountries().then(() => {           // upgrade vague band → named country
    if (state.selectedEvent !== id) return;
    paintDetailHeader(ev);
  });
  renderEventMini(ev);
  /* a11y: the row that had focus is hidden by now, which dropped focus to
     <body>; land on the detail heading (not for same-event re-renders such as
     a theme flip, which must not steal focus from the theme button) */
  if (openingFromList || wasOpen) {
    const h = $('#detail-title');
    h.tabIndex = -1;
    h.focus({ preventScroll: true });
  }

  const scoresFold = $('#detail-scores');
  scoresFold.open = false;
  $('#detail-charts').innerHTML = '';

  const vizHost = $('#detail-viz');
  /* Track lanes (tropical_cyclone, tc 2026-11-16) carry their own geometry in
     details.track (shared 5-slot row schema, documented in the tc lane module)
     and have no viz.json asset: draw the best-track map instead of loading an
     event visual. One renderer serves every lane that publishes a track. */
  if (vizHost && window.TrackViz && Array.isArray(ev.details?.track) && ev.details.track.length) {
    if (window.EventViz) EventViz.teardown();
    vizHost.style.minHeight = '';
    vizHost.innerHTML = '';
    state._viz = null; state._vizFor = id;
    TrackViz.render(vizHost, ev, { color: typeColor(ev.event_type) });
  } else if (vizHost && window.EventViz) {
    vizHost.innerHTML = '<div class="sec-note">loading event visual\u2026</div>';
    vizHost.style.minHeight = '520px';   // CLS: reserve the panel area while the viz JSON loads
    /* Owner 2026-11-08 + Ponytail: a missing/broken viz never leaves a blank
       panel. "null" (404) = asset not built yet; a throw/parse failure =
       asset failed. Distinct calm copy + exactly one console.warn each. */
    const showNoViz = (kind) => {
      const msg = state.week
        ? 'Map images are only kept for the current week — scores and charts below are unaffected.'
        : kind === 'network'
        ? 'Could not reach the server for this event\'s map — check your connection and reopen the event. '
          + 'Scores and charts below are unaffected.'
        : kind === 'failed'
        ? 'Map data could not load yet — this event\'s map asset appears incomplete. '
          + 'It should appear once rebuilt; scores and charts below are unaffected.'
        : 'Map data not available yet — this event\'s map asset hasn\'t been built. '
          + 'Scores and charts below are unaffected.';
      vizHost.style.minHeight = '';
      vizHost.innerHTML = '<div class="lane-empty" role="status">' + msg + '</div>';
    };
    EventViz.load(id).then((viz) => {
      if (state.selectedEvent !== id) return;
      if (!viz || viz.__vizError) {
        console.warn('viz asset unavailable for event ' + id);
        showNoViz(viz && viz.__vizError ? 'failed' : 'missing');
        return;
      }
      vizHost.innerHTML = '';
      vizHost.style.minHeight = '';
      state._viz = viz; state._vizFor = id;
      paintDetailHeader(ev);   // story + badge + title all upgrade with the mask
      // ponytail 2026-11-08: detailLead was already seeded from the hash in
      // selectEvent before syncUrl erased it — re-reading the hash here was dead
      EventViz.initialLead = state.detailLead;
      EventViz.onLead = (lead) => { state.detailLead = lead; syncUrl(false); };
      EventViz.onModels = (ms) => { state.detailModels = ms; };
      EventViz.onLayers = (s) => { state.detailLayers = s || null; syncUrl(false); };
      EventViz.onDay = (d) => { state.detailDay = d === '' || d == null ? null : d; syncUrl(false); };
      if (!state.detailModels || !state.detailModels.length) state.detailModels = pickDetailModels(ev, viz);
      EventViz.render(vizHost, viz, ev, { models: state.detailModels, baseline: state.baseline,
        layers: state.detailLayers, day: state.detailDay });
    }).catch((err) => {
      if (state.selectedEvent !== id) return;
      console.warn('viz asset failed for event ' + id + ': ' + err);
      showNoViz(err instanceof TypeError ? 'network' : 'failed');   // fetch() rejects with TypeError offline
    });
  }

  /* per-metric charts: built lazily when the Scores fold opens (Plotly needs
     a laid-out container). */
  const buildCharts = () => {
    const host = $('#detail-charts');
    host.innerHTML = '';
    const rows = state.scores.filter((r) => r.event_id === id && state.models.has(r.model)
      && Number.isFinite(r.lead_time_hours) && Number.isFinite(r.value));
    /* DR3 (ewb 2026-11-07): documented exclusions (bench_failed "n/a
       (memory limit)", unsupported) must read as n/a WITH the reason —
       never a blank fold. Reason comes from pair_status.json only; no
       event ids live in the dashboard. */
    if (!rows.length) {
      const ents = Object.entries(state.pairStatus).filter(([k]) => k.startsWith(id + '|'));
      const allUnscored = ents.length && !ents.some(([, v]) => v.status === 'scored');
      const note = el('div', 'lane-empty');
      note.setAttribute('role', 'status');
      if (allUnscored) {
        const reasons = [...new Set(ents.map(([, v]) => friendlyReason(v.reason)).filter(Boolean))];
        note.textContent = `Not scored — ${ents.length} models: `
          + (reasons.join('; ') || 'no reason recorded');
      } else if (state.scores.some((r) => r.event_id === id)) {
        // ponytail 10:16: rows EXIST but the visible-model/lead/value filter
        // hid them — never blame the pipeline for a local filter
        note.textContent = 'No plottable (lead-level, non-NaN) scores for the '
          + 'visible models — adjust the model filter.';
      } else {
        note.textContent = 'No scores for this event yet — the pipeline has not '
          + 'produced rows for it (scoring runs sequentially; check back after the run).';
      }
      host.appendChild(note);
      return;
    }
    if (typeof Plotly === 'undefined') { ensurePlotly().then(() => buildCharts()).catch(() => {}); return; }
    const metrics = [...new Set(rows.map((r) => r.metric))];
    const leadsX = [...new Set(rows.map((r) => r.lead_time_hours))].sort((a, b) => a - b);
    for (const met of metrics) {
      const card = el('div', 'chart-card');
      card.appendChild(el('h4', '', esc(metricMeta(met).label)));
      const div = el('div', 'chart');
      card.appendChild(div);
      host.appendChild(card);
      const traces = [];
      state.allModels.forEach((model, mIdx) => {
        if (!state.models.has(model)) return;
        const byLead = {};
        for (const r of rows) if (r.model === model && r.metric === met)
          byLead[r.lead_time_hours] = (byLead[r.lead_time_hours] || []).concat(r.value);
        const pts = leadsX.filter((h) => byLead[h]).map((h) => [h, byLead[h].reduce((a, b) => a + b, 0) / byLead[h].length]);
        if (!pts.length) return;
        traces.push({
          x: pts.map((p) => p[0] / 24), y: pts.map((p) => p[1]),
          customdata: pts.map((p) => leadLabel(p[0])),
          name: displayModelName(model), type: 'scatter', mode: 'lines+markers',
          hovertemplate: '%{fullData.name} \u00b7 lead %{data.customdata}<br>%{y:.2f}<extra></extra>',
          line: { color: modelColor(model, mIdx), width: model === state.baseline ? 2.4 : 1.8 }, marker: { size: 4 },
        });
      });
      if (!traces.length) continue;
      const layout = baseLayout(`${metricMeta(met).label}${metricMeta(met).units ? ' [' + metricMeta(met).units + ']' : ''}`, 'abs');
      numericLeadAxis(layout, leadsX, div.getBoundingClientRect().width || 560);
      Plotly.newPlot(div, traces, layout, PLOTLY_CFG);
    }
    // G-29: the fold/grid has no final width when Plotly first measures (first chart
    // rendered ~2× its container until a window resize) — re-fit once layout settled
    requestAnimationFrame(() => requestAnimationFrame(() => {
      host.querySelectorAll('.chart').forEach((d) => { try { Plotly.Plots.resize(d); } catch (e) { /* torn down */ } });
    }));
  };
  state.detailChartsPending = buildCharts;

  if (!fromMap) renderEventTable();
  sec.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
}

function closeDetail(nav) {
  nav = nav || { push: true };
  state.selectedEvent = null;
  state.detailLead = null;
  state.detailModels = null;
  state.detailLayers = null;
  state.detailDay = null;
  state.detailChartsPending = null;
  if (window.EventViz) { EventViz.onLead = null; EventViz.onModels = null; EventViz.onLayers = null; EventViz.onDay = null; EventViz.initialLead = null; EventViz.teardown(); }
  if (window.TrackViz) TrackViz.teardown();
  syncUrl(!!nav.push);
  $('#detail-sec').classList.add('hidden');
  document.body.classList.remove('detail-open');
  applyView();   // back to whichever view the rail has selected (week-aware re-show)
  const vh = $('#detail-viz'); if (vh) vh.innerHTML = '';
  const st = $('#detail-story'); if (st) st.textContent = '';
  const mini = $('#detail-mini'); if (mini) mini.innerHTML = '';
  const charts = $('#detail-charts'); if (charts) charts.innerHTML = '';
  const fold = $('#detail-scores'); if (fold) fold.open = false;
  renderEventTable();
  /* A1: restore the list scroll position (page + event table) once the home
     layout is back. Two passes: rAF catches the un-hidden layout, a second
     pass after the map/table re-layout finishes (single rAF clamps short).
     A user scroll during the window cancels the second pass. */
  const back = state.listReturn;
  const retId = state.lastViewedEvent;
  requestAnimationFrame(() => {
    // a11y: hand focus back to the row the user came from (Esc/close/Back)
    const row = retId && document.querySelector(`#event-table tr[data-id="${retId}"]`);
    if (row) row.focus({ preventScroll: true });
    window.scrollTo(0, back ? back.y : 0);
    const wrap = document.querySelector('#events-sec .table-wrap.tall');
    if (wrap && back) { wrap.scrollLeft = back.x; wrap.scrollTop = back.ty; }
  });
  if (back) {
    let touched = false;
    const cancel = () => { touched = true; };
    ['wheel', 'touchstart', 'keydown'].forEach((e) =>
      window.addEventListener(e, cancel, { passive: true, once: true }));
    setTimeout(() => {
      window.removeEventListener('wheel', cancel);
      window.removeEventListener('touchstart', cancel);
      window.removeEventListener('keydown', cancel);
      if (!touched && !state.selectedEvent) {
        window.scrollTo(0, back.y);
        const wrap = document.querySelector('#events-sec .table-wrap.tall');
        if (wrap) { wrap.scrollLeft = back.x; wrap.scrollTop = back.ty; }
      }
      // ponytail 08:1x: the restore window is over — a pending listReturn from
      // THIS close must never hijack a later render (scrollbar drags don't
      // fire the cancel listeners, and once:true listeners are not re-armed)
      if (state.listReturn === back) state.listReturn = null;
    }, 350);
  }
}

/* ------------------------------------------------------------------ */
/* theme                                                               */
/* ------------------------------------------------------------------ */
function applyTheme() {
  const eff = state.themePref === 'auto'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : state.themePref;
  document.documentElement.dataset.theme = eff;
  /* The rail footer's segmented control marks the PREFERENCE, not the result —
     'Auto' stays selected while it resolves to dark, and says what it resolves
     to in its tooltip. Same selected state as every other control in the app. */
  document.querySelectorAll('#theme-ctl [data-pref]').forEach((b) => {
    const on = b.dataset.pref === state.themePref;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
    if (b.dataset.pref === 'auto') b.title = `Follow the system setting (currently ${eff})`;
  });
}
function setThemePref(p) {
  if (!p || p === state.themePref) return;
  state.themePref = p;
  saveState(); applyTheme(); renderSkillCharts();
  renderEventTable();   // type tokens are theme-paired — re-paint dots
  if (state.selectedEvent) selectEvent(state.selectedEvent, true);
}

/* ------------------------------------------------------------------ */
/* VIEWS: Events and Scorecard are two views, not one long page.         */
/* The rail switches between them (the inactive section is hidden, so     */
/* neither scrolls the other away), the active view rides the URL hash    */
/* (#view=scorecard; the default Events view keeps a clean hash), and the */
/* two links stay usable while an event detail is open — from a detail    */
/* either link closes it and goes to that view.                           */
/* ------------------------------------------------------------------ */
let railRaf = 0;

function paintRail(cur) {
  document.querySelectorAll('#rail [data-jump]').forEach((b) => {
    const on = b.dataset.jump === cur;
    b.classList.toggle('is-active', on);
    if (on) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
}
function syncRailActive() {
  paintRail(state.selectedEvent ? null
    : state.view === 'scorecard' ? 'scorecard-sec' : 'explore');
}
function syncRailSoon() {
  if (railRaf) return;
  railRaf = requestAnimationFrame(() => { railRaf = 0; syncRailActive(); });
}

/* Show exactly one view. Safe to call at any point in boot: it only moves
   classes, so the first paint of a #view=scorecard or #event= link is right. */
function applyView() {
  const detail = !!state.selectedEvent;
  const ev = !detail && state.view === 'events';
  const sc = !detail && state.view === 'scorecard';
  const explore = $('#explore'), scEl = $('#scorecard-sec'), evSec = $('#events-sec');
  if (explore) explore.classList.toggle('hidden', !ev);
  if (scEl) scEl.classList.toggle('hidden', !sc);
  if (evSec) evSec.classList.toggle('hidden', !ev);
  syncWindowSections();   // #global-sec: archived weeks and the detail view hide the map
  syncRailActive();
}

function viewFromHash(h) {
  const v = h.view;
  if (v == null) return null;
  return v === 'scorecard' || v === 'events' ? v : undefined;   // undefined = garbage in the bar
}

/* Switch views. From an open detail this closes it first (the detail is its own
   view; the rail never goes dead), and the page starts at the top of whatever
   view comes next instead of at the detail's scroll depth. */
function setView(view, push = true) {
  const v = view === 'scorecard' ? 'scorecard' : 'events';
  const changed = state.view !== v || !!state.selectedEvent;
  state.view = v;
  if (state.selectedEvent) {
    state.listReturn = null;        // the list's saved scroll belongs to the old visit
    closeDetail({ push: false });
  }
  applyView();
  syncUrl(!!push && changed);
  if (changed) window.scrollTo({ top: 0, behavior: 'auto' });
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */
function renderAll() {
  renderScorecard();
  renderSkillCharts();
  renderEventTable();
  syncWindowSections();
  syncRailSoon();   // the scorecard changes the page height under the probe line
}

// The merged global 90-day view hardcodes current-window data (no history
// snapshot exists for it) — it must vanish from archived-week pages
// (ponytail 2026-11-08 16:15), and stay hidden while a detail is open.
function syncWindowSections() {
  const hide = !!state.week || !!state.selectedEvent;
  $('#global-sec').classList.toggle('hidden', hide);
  syncRailSoon();   // the marked section can change when a section appears/vanishes
}

async function boot() {
  /* CLS: a #event=… deep link used to paint the whole home page (global map, scorecard,
     catalog) and then swap it for the detail view once the JSON arrived — a 0.4+ layout
     shift on phones. Hide the home sections from the first paint when the URL names an
     event; boot() lifts the class as soon as the right view is up. */
  const deepLink = !!(hashParams().event || new URLSearchParams(location.search).get('event'));
  if (deepLink) document.documentElement.classList.add('deeplink');
  restoreState();
  /* The view is URL state (#view=scorecard): read it before anything paints, so
     a shared Scorecard link never flashes the Events page first. */
  { const v = viewFromHash(hashParams());
    if (v === undefined) setHashParam('view', null);   // garbage never survives the bar
    else if (v) state.view = v; }
  applyView();
  applyTheme();
  document.querySelectorAll('#theme-ctl [data-pref]').forEach((b) => {
    b.onclick = () => setThemePref(b.dataset.pref);
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    // ponytail 08:1x: OS theme flip repaints everything setThemePref does —
    // boxes/table dots/minis read theme-paired CSS tokens at render time;
    // 09:18: and an open detail resolved its palette at build time — re-select
    if (state.themePref === 'auto') {
      applyTheme(); renderSkillCharts(); renderEventTable();
      if (state.selectedEvent) selectEvent(state.selectedEvent, true);
    }
  });
  $('#detail-close').onclick = () => closeDetail();
  /* The rail's two section links SWITCH VIEWS (AC-1.1) — they used to scroll one
     very long page, which meant "Scorecard" landed anywhere from the map to the
     middle of a table depending on where you had been reading. From an open
     event detail either link closes it and goes to that view. */
  document.querySelectorAll('[data-jump]').forEach((b) => {
    b.onclick = () => setView(b.dataset.view
      || (b.dataset.jump === 'scorecard-sec' ? 'scorecard' : 'events'));
  });
  /* All | On map: the list can follow the map frame instead of the window. */
  const scope = $('#ev-scope-toggle');
  if (scope) scope.querySelectorAll('[data-scope]').forEach((b) => {
    b.onclick = () => setEvScope(b.dataset.scope);
  });
  syncEvScopeUI();
  /* Skip link (WCAG 2.4.1). Intercepted rather than a plain #anchor: the hash is
     app state (#event=, #region=, #map=, #layers=), so pointing a real anchor at
     #main-content would wipe the current view's URL. */
  $('#skip-link').onclick = (e) => {
    e.preventDefault();
    const m = document.getElementById('main-content');
    m.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start' });
    m.focus({ preventScroll: true });
  };
  /* The rail marks the active VIEW now (no scroll probe): which section is on
     screen is a fact about state, not about the viewport. */
  syncRailSoon();
  /* Show-tabs: tablist keyboard over the wrapped grid (no scroller, so no edge
     fades and no wheel-capture — the whole row set is always visible). */
  $('#type-tabs').addEventListener('keydown', tabsKeyNav);
  /* RAIL (after owb.brightband.com): one toggle button, two jobs — it collapses the
     220 px column to a 58 px glyph rail on desktop and closes the drawer below 1100px,
     which is the width where the column becomes a drawer (a CSS breakpoint). */
  const rail = $('#rail'), railToggle = $('#rail-toggle'), navBtn = $('#nav-btn');
  const narrow = () => matchMedia('(max-width: 1099px)').matches;
  const syncToggle = () => {   // one button, two meanings — say which one it has right now
    if (!railToggle) return;
    const t = narrow() ? 'Close navigation'
      : document.documentElement.classList.contains('rail-collapsed') ? 'Expand navigation' : 'Collapse navigation';
    railToggle.title = railToggle.ariaLabel = t;
  };
  const setCollapsed = (on) => {
    document.documentElement.classList.toggle('rail-collapsed', on);
    if (railToggle) railToggle.setAttribute('aria-expanded', String(!on));
    /* Collapsed, a section link is an icon with a clip-hidden label: screen
       readers still get the name, sighted users only get it as a tooltip. */
    document.querySelectorAll('#rail [data-jump]').forEach((b) => {
      b.title = on ? (b.querySelector('.rail-label') || b).textContent.trim() : '';
    });
    syncToggle();
  };
  /* Drawer focus: opening moves focus into the panel, closing hands it back to the
     hamburger (a link inside the drawer is display:none the moment it closes, so
     leaving focus there drops it to <body> and the next Tab restarts at the top). */
  let navReturn = null;
  const focusables = () => [...rail.querySelectorAll('button, [href], select, [tabindex]:not([tabindex="-1"])')]
    .filter((n) => n.offsetParent !== null && !n.disabled);
  const closeNav = () => {
    if (!document.body.classList.contains('nav-open')) return;
    const hadFocus = rail.contains(document.activeElement);
    document.body.classList.remove('nav-open');
    if (navBtn) navBtn.setAttribute('aria-expanded', 'false');
    if (hadFocus && navReturn && navReturn.isConnected) navReturn.focus({ preventScroll: true });
    navReturn = null;
    syncToggle();
  };
  const openNav = () => {
    document.body.classList.add('nav-open');
    if (navBtn) navBtn.setAttribute('aria-expanded', 'true');
    navReturn = document.activeElement;
    const f = focusables();
    if (f.length) f[0].focus();
    syncToggle();
  };
  setCollapsed(!narrow() && !!state.railCollapsed);
  if (railToggle) railToggle.onclick = () => {
    if (narrow()) { closeNav(); return; }
    state.railCollapsed = !document.documentElement.classList.contains('rail-collapsed');
    saveState(); setCollapsed(state.railCollapsed);
  };
  if (navBtn) navBtn.onclick = () => {
    if (document.body.classList.contains('nav-open')) closeNav(); else openNav();
  };
  matchMedia('(max-width: 1099px)').addEventListener('change', () => {
    if (!narrow()) closeNav();
    setCollapsed(!narrow() && !!state.railCollapsed);
  });
  /* THEATER MODE: the map takes the whole explore grid and the event list drops
     below it (the .explore theater rules) — the list is never hidden, so event
     picking works exactly as before. Same shape as the rail collapse: an html.*
     class the CSS keys off, one flag in 'ewb-live-state', and nothing in the URL
     (theater is a view preference, not shareable state). Below 1100px the grid
     is one column already, so the class is ignored there — but the flag is kept,
     and widening the window restores it (the rail's rule). */
  const theaterBtn = $('#theater-btn');
  const theaterOn = () => document.documentElement.classList.contains('theater');
  const setTheater = (on, persist = true) => {
    document.documentElement.classList.toggle('theater', on);
    if (theaterBtn) theaterBtn.setAttribute('aria-pressed', String(on));
    if (persist) { state.theater = on; saveState(); }
  };
  if (theaterBtn) {
    theaterBtn.onclick = () => setTheater(!theaterOn());
    setTheater(!!state.theater && !narrow(), false);   // restore; boot never writes back
  }
  matchMedia('(max-width: 1099px)').addEventListener('change', () => {
    setTheater(!!state.theater && !narrow(), false);
  });
  document.addEventListener('click', (e) => {   // scrim is a ::before — its clicks land on <body>
    if (e.target === document.body) closeNav();
  });
  /* Drawer keyboard contract: Esc closes, Tab stays inside the panel (the scrim
     blocks the page behind it, so focus must not leak out of it). */
  document.addEventListener('keydown', (e) => {
    if (!document.body.classList.contains('nav-open')) return;
    if (e.key === 'Escape') { closeNav(); return; }
    if (e.key !== 'Tab') return;
    const f = focusables();
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1], cur = document.activeElement;
    if (!rail.contains(cur)) { e.preventDefault(); first.focus(); return; }
    if (e.shiftKey && cur === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && cur === last) { e.preventDefault(); first.focus(); }
  });
  /* Every rail link navigates, so the drawer closes on its way. The footer
     settings (theme, week) are not links and are not in this set — the drawer
     stays open when you try a theme. */
  document.querySelectorAll('.rail-link').forEach((b) => b.addEventListener('click', closeNav));
  /* A1: Esc returns from the event detail to the list (owner bug A1). The
     About dialog and the fullscreen map panel own Esc while they are open. */
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !state.selectedEvent) return;
    if (document.querySelector('dialog[open]')) return;
    if (document.querySelector('.ev3-fs')) return;
    closeDetail();
  });
  /* Theater keys: `t` toggles, Esc leaves. Quiet: no modifier, never while
     typing, and only while the button is actually on screen — below 1100px, or
     on an archive/detail page that hides the map, it is display:none, and the
     key does nothing there either. Captured on purpose: Esc belongs to whatever
     else owns it (a dialog, the drawer, an open event detail) and those handlers
     clear their own state synchronously, so this one has to see it first. */
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const src = e.target;   // never `el`/`t`: both names are taken in this file
    const typing = !!src && (src.tagName === 'INPUT' || src.tagName === 'SELECT'
      || src.tagName === 'TEXTAREA' || src.isContentEditable);
    if (e.key === 'Escape' && window.MergedMap && MergedMap.isLegendOpen && MergedMap.isLegendOpen()) {
      MergedMap.closeLegend(true);   // the popover owns Esc while it is open
      return;
    }
    if (e.key === 't' && !e.shiftKey && !typing && theaterBtn && theaterBtn.offsetParent !== null) {
      e.preventDefault();
      setTheater(!theaterOn());
    } else if (e.key === 'Escape' && theaterOn() && !state.selectedEvent
        && !document.body.classList.contains('nav-open')
        && !document.querySelector('dialog[open]')) {
      setTheater(false);
    }
  }, { capture: true });
  // About dialog: native <dialog>, Esc closes, backdrop click closes, scrim per OWB §6.7
  const dlg = $('#about-dialog');
  const openAbout = () => { renderAboutModels(); dlg.showModal(); };
  $('#about-btn').onclick = openAbout;
  $('#about-btn-foot').onclick = openAbout;
  $('#about-close').onclick = () => dlg.close();
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  /* "Reproduce the tracker in Python": the listing is authored, not generated
     (dashboard/detection.py) and the test suite runs it against the shipped
     tracker, so the page cannot drift from the detector. Fetched on first open
     to keep the dialog light. */
  const reproFold = $('.repro');
  reproFold?.addEventListener('toggle', (e) => {
    const pre = $('#detection-code');
    if (!e.target.open || !pre || pre.dataset.loaded) return;
    fetch('detection.py')
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((src) => { pre.textContent = src; pre.dataset.loaded = '1'; })
      .catch(() => { pre.textContent = 'Could not load detection.py — it ships with the dashboard (dashboard/detection.py).'; });
  });
  /* "How the track errors are computed": same arrangement (dashboard/track_scoring.py,
     checked against the shipped scorer by the test suite). */
  const trackFold = $('.repro-track');
  trackFold?.addEventListener('toggle', (e) => {
    const pre = $('#track-scoring-code');
    if (!e.target.open || !pre || pre.dataset.loaded) return;
    fetch('track_scoring.py')
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((src) => { pre.textContent = src; pre.dataset.loaded = '1'; })
      .catch(() => { pre.textContent = 'Could not load track_scoring.py — it ships with the dashboard (dashboard/track_scoring.py).'; });
  });
  // lazy Plotly builds for collapsed folds (measuring inside display:none breaks width)
  $('#skill-details').addEventListener('toggle', (e) => {
    if (e.target.open && state.skillDirty) renderSkillCharts();
  });
  $('#detail-scores').addEventListener('toggle', (e) => {
    if (e.target.open && state.detailChartsPending) {
      const f = state.detailChartsPending;
      state.detailChartsPending = null;
      f();
    }
  });
  $('#event-search').addEventListener('input', (e) => {
    state.search = e.target.value.trim();
    renderEventTable();
  });
  let pendingNote = null;
  try {
    const params = new URLSearchParams(location.search);
    const urlWeek = params.get('week');
    if (urlWeek && /^\d{4}-W\d{2}$/.test(urlWeek)) state.week = urlWeek;
    const urlEvent = hashParams().event || params.get('event');
    // weeks.json is only needed to validate ?week=; with no such param load it IN PARALLEL
    // with the results instead of in front of them (saves a round trip before first paint)
    const dataP = params.get('week') ? null : loadData(null);
    if (dataP) dataP.catch(() => {});   // rejection is surfaced by the await below; no stray unhandledrejection meanwhile
    const regionsP = loadRegions().catch(() => {});   // region defs for the dropdown/map, in parallel
    await loadWeeks();
    if (params.get('week') && !state.weeks.includes(params.get('week'))) {
      // unknown/expired snapshot: show the current week and drop the stale param
      state.week = null;
      try {
        const u = new URL(location.href);
        u.searchParams.delete('week');
        history.replaceState(history.state, '', u);
      } catch (e) { /* file:// or sandboxed */ }
      pendingNote = 'That weekly snapshot is not available — showing the current week.';
    }
    await (dataP || loadData(state.week));
    await regionsP;
    if (urlEvent && state.events.some((e) => e.id === urlEvent)) state.selectedEvent = urlEvent;
    else if (urlEvent) {
      setHashParam('event', null);
      setHashParam('lead', null);
      pendingNote = 'That event is not in the current 90-day window (events roll over weekly) — showing the catalog.';
    }
    /* Type filter from the URL (#tab=): the URL wins over the stored
       preference; an unknown value never survives in the address bar. */
    {
      const urlTab = hashParams().tab;
      if (urlTab != null) {
        if (urlTab === 'all' || TYPE_META[urlTab]) state.tab = urlTab;
        else setHashParam('tab', null);
      }
    }
    /* Region from the URL (#region=), validated against the data contract: a deep
       link restores the region view; an unknown key is dropped from the URL and
       never silently kept (URL/state must agree after boot). The region file is
       lazy — a region deep link waits for it (a plain Global load never does). */
    {
      const urlRegion = hashParams().region;
      if (urlRegion && urlRegion !== 'global') {
        if (state.regionHit) await state.regionHit.catch(() => {});
        const ok = state.regionData && regionDef(urlRegion)
          && state.regionData.regions.some((r) => r.key === urlRegion);
        if (ok) state.region = urlRegion;
        else setHashParam('region', null);
      }
    }
  } catch (err) {
    console.error(err);
    $('#load-error').classList.remove('hidden');
    document.documentElement.classList.remove('deeplink');
    return;
  }
  renderHeader();
  renderControls();
  for (const id of Object.keys(bboxFixes)) {
    const ev = state.events.find((e) => e.id === id);
    if (ev) ev.bbox = bboxFixes[id];
  }
  renderAll();
  loadCountries().then(() => { if (!state.selectedEvent) renderEventTable(); });   // vague bands → country names
  if (pendingNote) routeNote(pendingNote);
  if (window.MergedMap) {
    MergedMap.mount();   // one global map (briefs/merged_global_map.md); self-reports failure
    if (state.region !== 'global') MergedMap.setRegion(regionDef(state.region));   // deep-linked region view
  }
  if (state.selectedEvent) selectEvent(state.selectedEvent, true, { push: false });
  document.documentElement.classList.remove('deeplink');
  /* A1: one sync path for every way the URL can change under us — Back/Forward
     (popstate) and a pasted/edited #event=… link (hashchange). App-initiated
     pushState/replaceState fire neither, so this never echoes. */
  const syncFromLocation = () => {
    /* The view follows the URL on Back/Forward and pasted #view= links; an
       unknown value falls back to the default view and never stays in the bar. */
    const hv = viewFromHash(hashParams());
    if (hv === undefined) setHashParam('view', null);
    const wantView = hv || 'events';
    if (wantView !== state.view) { state.view = wantView; applyView(); }
    /* The Show tab follows the URL on Back/Forward (and pasted #tab= links).
       syncUrl writes the tab from state, so an invalid value rewrites itself
       out via the next state write; here it just falls back to All. */
    const tabP = hashParams().tab;
    if (tabP !== undefined) {
      const valid = tabP === 'all' || TYPE_META[tabP];
      if (!valid) setHashParam('tab', null);   // an unknown value never survives the address bar
      const want = valid ? tabP : 'all';
      if (want !== state.tab) {
        state.tab = want; saveState();
        renderControls(); renderAll();
        if (window.MergedMap) MergedMap.onTabChange(want);
      }
    }
    /* Region follows the URL on Back/Forward (and pasted #region= links) without
       writing back; an unknown or dropped key falls back to Global. */
    const reg = hashParams().region || 'global';
    const wantReg = reg !== 'global' && state.regionData
      && regionDef(reg) && state.regionData.regions.some((r) => r.key === reg) ? reg : 'global';
    if (wantReg !== state.region) {
      state.region = wantReg;
      renderControls();   // sync the dropdown selection + tab counts
      renderHeader();     // EVENTS stat follows the region
      renderAll();
      if (window.MergedMap) MergedMap.setRegion(regionDef(wantReg));
    }
    // a key that is truly unknown (not just a lazy file still in flight) never survives
    if (reg !== 'global' && wantReg === 'global' && state.regionData && !regionDef(reg)) {
      setHashParam('region', null);
    }
    if (window.MergedMap) MergedMap.applyUrl(hashParams());
    const id = hashParams().event;
    if (id && state.events.some((e) => e.id === id)) {
      if (state.selectedEvent !== id) selectEvent(id, true, { push: false });
    } else if (state.selectedEvent && (!id || !state.events.some((e) => e.id === id))) {
      closeDetail({ push: false });   // no/unknown event in the URL → back to the catalog
      if (id) routeNote('That event is not in the current 90-day window — showing the catalog.');
    } else if (id) {
      setHashParam('event', null);    // unknown id typed into the address bar
      setHashParam('lead', null);
      routeNote('That event is not in the current 90-day window — showing the catalog.');
    }
  };
  window.addEventListener('popstate', syncFromLocation);
  window.addEventListener('hashchange', syncFromLocation);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
