/* EWB Live dashboard — vanilla JS + vendored Plotly/Leaflet. No build step. */
'use strict';

/* ------------------------------------------------------------------ */
/* constants                                                           */
/* ------------------------------------------------------------------ */
const TYPE_META = {
  heat_wave:          { label: 'Heat waves',         short: 'Heat wave',          color: 'var(--series-heat)' },
  freeze:             { label: 'Freezes',            short: 'Freeze',             color: 'var(--series-freeze)' },
  atmospheric_river:  { label: 'Atmospheric rivers', short: 'Atmospheric river',  color: 'var(--series-ar)' },
};
/* AR green (design 2026-11-08, owner request): all type colors resolve
   through CSS tokens — dual-theme values, no hardcoded hex in JS. */
const TYPE_SERIES_VAR = { heat_wave: '--series-heat', freeze: '--series-freeze',
  atmospheric_river: '--series-ar' };
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function typeColor(etype) {
  return cssVar(TYPE_SERIES_VAR[etype] || '--series-unknown', '#848475');
}
const TYPE_ORDER = ['heat_wave', 'freeze', 'atmospheric_river'];

/* Owner 2026-11-07: empty lanes are legitimate (freezes may be empty at p15).
   Criteria wording mirrors ewb_live/events/temperature.py + ar.py thresholds —
   keep in sync if detection parameters change. */
const TYPE_CRITERIA = {
  heat_wave: 'Every 6-hourly step of a UTC day has 2 m temperature above the 85th percentile of the ERA5 1990-2019 climatology (per day of year and hour), over land. A region ≥50,000 km² becomes an event once its footprint reaches 200,000 km², and ends when the cells that have counted every day since it began hold under 100,000 km² (at least 3 days). The map shades only cells that reach the same 3-consecutive-day minimum (consecutive COMPLETED days; cells below it are blank because they can never become an event), dithered = today so far on an already-qualifying cell.',
  freeze: 'Every 6-hourly step of a UTC day has 2 m temperature below the 15th percentile of the ERA5 1990-2019 climatology (per day of year and hour), over land. A region ≥50,000 km² becomes an event once its footprint reaches 200,000 km², and ends when the cells that have counted every day since it began hold under 100,000 km² (at least 3 days). On the map the freeze layer shades cells from their first consecutive COMPLETED day (cold runs are short, so 1–2-day cells stay visible for context; a freeze still needs 3+ days), dithered = today so far on a cell with at least one completed day.',
  atmospheric_river: 'Integrated water-vapour transport peaking at ≥400 kg m⁻¹ s⁻¹ and reaching land.',
};
const emptyLaneLine = (t) => t === 'all'
  ? 'No events met the criteria this window'
  : `No ${TYPE_META[t].label.toLowerCase()} met the criteria this window`;

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
  CriticalSuccessIndex:            { label: 'CSI', units: '', higher_is_better: true },
  spatial_displacement:            { label: 'Spatial displacement', units: 'km', higher_is_better: false },
  EarlySignal:                     { label: 'Early signal', units: '', higher_is_better: true },
  Accuracy:                        { label: 'Accuracy', units: '', higher_is_better: true },
};
const BINARY_METRICS = new Set(['csi', 'CriticalSuccessIndex', 'Accuracy', 'EarlySignal']);

/* ------------------------------------------------------------------ */
/* state                                                               */
/* ------------------------------------------------------------------ */
const state = {
  meta: null, events: [], scores: [], scorecard: [], pairStatus: {},
  tab: 'all',                      // 'all' | event_type
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
  return Object.assign({}, fb, fromMeta || {});
}
function fmtVal(metricId, v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const u = metricMeta(metricId).units;
  if (BINARY_METRICS.has(metricId)) return v.toFixed(2).replace('-', '\u2212');
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
  const [meta, eventsSel, eventsAll, scores, scorecard, pairStatus] = await Promise.all([
    loadJSON('meta.json', week),
    loadJSON('events_selected.json', week),   // curated/scored set — shown by default
    loadJSON('events.json', week),            // raw candidates — fallback
    loadJSON('scores.json', week),
    loadJSON('scorecard.json', week),
    loadJSON('pair_status.json', week),       // ewb per-pair bench status (reasons)
  ]);
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
function renderHeader() {
  const m = state.meta;
  $('#m-updated').textContent = m.last_updated ? fmtDate(m.last_updated) : '\u2014';

  /* kv-line (OWB kv-row): events \u00b7 models \u00b7 window \u00b7 truth \u2014 the whole run
     summary in one tracked-label row; detail lives in tooltips and About. */
  const truth = truthText(m.truth_source);
  const days = m.window && Number.isFinite(m.window.days) ? m.window.days
    : (m.window && parseISO(m.window.start) && parseISO(m.window.end)
      ? Math.round((parseISO(m.window.end) - parseISO(m.window.start)) / 86400000) : null);
  const nModels = new Set(state.scores.map((r) => r.model)).size;
  const rosterN = Math.max(state.allModels.length,
    Array.isArray(m.models) ? m.models.length : 0);
  const truthShort = truth.text.split(' \u00b7 ')[0];
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
    kv('truth', esc(truthShort), truth.title || truth.text),
  ].join('');

  renderWeekPicker();
}

/* About dialog model list: name chips tagged with their source forcing. */
function renderAboutModels() {
  const host = $('#about-models');
  if (!host) return;
  // plain names only (forcing is explained once in the note below); the
  // baseline is named up front, so it isn't repeated as a chip
  host.innerHTML = `Baseline <b>${esc(state.baseline)}</b> \u00b7 ` +
    state.allModels.filter((m) => m !== state.baseline)
      .map((m) => `<span class="chip">${esc(m)}</span>`).join(' ');
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
  } catch (e) { /* ignore */ }
}
function saveState() {
  try {   // blocked/full storage (private windows, embeds) must not break the UI
    localStorage.setItem('ewb-live-state', JSON.stringify({
      themePref: state.themePref, tab: state.tab, chartMode: state.chartMode,
      railCollapsed: !!state.railCollapsed,
    }));
  } catch (e) { /* preference just won't persist */ }
}

/* The event-type filter is the one page-wide control: it narrows the map
   (layers + boxes), the timeline, the event list and the scorecard together. */
function setTab(id) {
  state.tab = id; saveState(); renderControls(); renderAll();
  if (window.MergedMap) MergedMap.onTabChange(id);
}

function renderControls() {
  const tabs = $('#type-tabs');
  tabs.innerHTML = '';
  /* Toggle buttons, not tabs: the choice narrows the map layers, the timeline,
     the event list and the scorecard all at once, so there is no single panel a
     tab could control (APG tabs would need one tabpanel + aria-controls + a
     roving tabindex for a filter that acts on four regions of the page).
     aria-pressed on a labelled group says exactly what this is. */
  const mkTab = (id, label) => {
    const b = el('button', state.tab === id ? 'on' : '', label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.tab === id));
    b.onclick = () => setTab(id);
    tabs.appendChild(b);
  };
  const inReg = (e) => state.region === 'global' || inRegion(e);
  mkTab('all', `All <span class="n">${state.events.filter(inReg).length}</span>`);
  for (const t of TYPE_ORDER) {
    const n = state.events.filter((e) => e.event_type === t && inReg(e)).length;
    mkTab(t, `<i class="type-dot" style="background:${TYPE_META[t].color}"></i>${TYPE_META[t].label} <span class="n">${n}</span>`);
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
      `<span class="dot" style="--c:${modelColor(m, i)}"></span>${esc(m)}`);
    p.type = 'button';
    p.setAttribute('aria-pressed', String(on));
    p.title = 'toggle model';
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
    g[r.model] = { mean: r.mean_value, rel: r.rel, n: r.n_events, lo: r.ci_lo, hi: r.ci_hi };
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
    atmospheric_river: 'CriticalSuccessIndex' }[etype];
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

/* Verdict strip (design.md P1.4): answer-first summary above the tables.
   For each active event type, find the model with the best mean relative skill
   over the currently visible metric × lead cells, and how many cells it wins.
   Recomputed on every scorecard render so it follows tab/model/lead filters. */
function renderVerdictStrip(idx) {
  const host = $('#verdict-strip');
  if (!host) return;
  const B = state.baseline;
  const leads = state.allLeads.filter((h) => state.leads.has(h));
  const visible = state.allModels.filter((m) => state.models.has(m));
  const parts = [];
  for (const etype of activeTypes()) {
    // metric × lead cells that actually have data for this lane
    const cols = [];
    for (const met of metricsFor(etype)) {
      const c = metricColumns(idx, etype, met, leads);
      if (c) cols.push({ met, columns: c });
    }
    if (!cols.length) continue;
    // accumulate relative skill per model over visible cells
    const acc = {};
    for (const m of visible) if (m !== B) acc[m] = { s: 0, n: 0, wins: 0 };
    let cellN = 0;
    for (const { met, columns } of cols) {
      for (const c of columns) {
        if (c.kind === 'mean') continue;   // mean duplicates lead data
        const lead = c.kind === 'lead' ? c.lead : null;
        const g = idx.get(`${etype}|${met}|${lead}`);
        if (!g) continue;
        const rels = {};
        for (const m of visible) {
          if (m === B) continue;
          const cell = g[m];
          if (!cell || !Number.isFinite(cell.rel)) continue;
          rels[m] = cell.rel;
          acc[m].s += cell.rel; acc[m].n += 1;
        }
        const scored = Object.keys(rels);
        if (!scored.length) continue;
        cellN += 1;
        const best = scored.reduce((a, b) => (rels[b] > rels[a] ? b : a));
        acc[best].wins += 1;
      }
    }
    const ranked = Object.keys(acc).filter((m) => acc[m].n > 0)
      .sort((a, b) => (acc[b].s / acc[b].n) - (acc[a].s / acc[a].n));
    if (!ranked.length) continue;
    const leader = ranked[0];
    const meanRel = acc[leader].s / acc[leader].n;
    const cells = acc[leader].wins;
    parts.push({
      etype, model: leader, meanRel, cells, total: cellN,
    });
  }
  if (!parts.length) { host.hidden = true; host.innerHTML = ''; return; }
  host.hidden = false;
  host.innerHTML = parts.map((p) => {
    const chip = `<span class="type-chip" style="background:${TYPE_META[p.etype].color}">${esc(TYPE_META[p.etype].short)}</span>`;
    const sign = p.meanRel >= 0 ? '+' : '';
    const txt = `<b>${esc(p.model)}</b> best · ${sign}${p.meanRel.toFixed(1)}% vs ${esc(B)}` +
      ` · won ${p.cells}/${p.total} cells`;
    return `<span class="verdict-item">${chip}<span class="v-text">${txt}</span></span>`;
  }).join('');
}

function renderScorecard() {
  const host = $('#scorecard-tables');
  host.innerHTML = '';
  let anySmallN = false;      // any rendered cell rests on < minSample() events
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
      scope.appendChild(el('span', '', `in ${esc(re.label)} \u00b7 `));
      const g = el('button', 'linkbtn', 'all regions');
      g.type = 'button';
      g.onclick = () => setRegion('global', true);
      scope.appendChild(g);
    }
    if (state.tab !== 'all') {
      scope.appendChild(el('span', '', `${esc(TYPE_META[state.tab].label)} only \u00b7 `));
      const all = el('button', 'linkbtn', 'show all types');
      all.type = 'button';
      all.onclick = () => setTab('all');
      scope.appendChild(all);
    }
  }
  const idx = scIndex();
  tooltipIdx = idx;
  renderVerdictStrip(idx);
  const leads = state.allLeads.filter((h) => state.leads.has(h));
  const B = state.baseline;

  // columns per active event type, computed once
  const etypeCols = new Map();
  for (const etype of activeTypes()) {
    const cols = new Map();
    for (const met of metricsFor(etype)) {
      const c = metricColumns(idx, etype, met, leads);
      if (c) cols.set(met, c);
    }
    if (cols.size) etypeCols.set(etype, cols);
  }

  // ONE global model order for all tables (rows line up between event types):
  // baseline first, then mean rel-skill over every visible cell of every table.
  const relSums = {};
  for (const m of state.allModels) { relSums[m] = { s: 0, n: 0 }; }
  for (const [key, g] of idx) {
    const [e2, met, hStr] = key.split('|');
    const cols = etypeCols.get(e2);
    if (!cols || !cols.has(met)) continue;
    const h = hStr === 'null' ? null : +hStr;
    if (h !== null && !cols.get(met).some((c) => c.kind === 'lead' && c.lead === h)) continue;
    for (const m of Object.keys(g)) {
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
  for (const etype of activeTypes()) {
    const cols = etypeCols.get(etype);
    // honest event counts: regions report the scored-event split from the data
    // contract (n_events/by_type, centroid rule owned by ewb_live/regions.py);
    // Global counts the curated catalog (same universe as before this feature).
    const re = regionEntry();
    const nEv = re ? (re.by_type || {})[etype] || 0
      : state.events.filter((e) => e.event_type === etype).length;

    const block = el('div', 'sc-block');
    const smallLane = nEv > 1 && nEv < minSample();
    block.appendChild(el('h3', '',
      `<span class="type-chip" style="background:${TYPE_META[etype].color}">${esc(TYPE_META[etype].short)}</span>` +
      `${nEv} event${nEv === 1 ? '' : 's'}` +
      (nEv === 1 ? ' · indicative' : smallLane ? ' · small sample' : '')));

    /* Owner 2026-11-07: an empty lane is a legitimate outcome, not a bug —
       calm one-line note + criteria tooltip instead of a table of dashes. */
    if (!nEv) {
      const note = el('p', 'lane-empty');
      const re2 = regionEntry();
      note.textContent = re2
        ? `No ${TYPE_META[etype].label.toLowerCase()} in ${re2.label} this window`
        : emptyLaneLine(etype);
      note.title = re2 ? 'Events are assigned to regions by the centroid of their bounding box'
        : TYPE_CRITERIA[etype];
      block.appendChild(note);
      host.appendChild(block);
      continue;
    }
    if (!cols) continue;

    const wrap = el('div', 'table-wrap');
    const table = el('table');

    // header rows
    const grp = el('tr', 'grp');
    grp.appendChild(el('th', 'model-col', 'Model'));
    const leadRow = el('tr', 'lead');
    leadRow.appendChild(el('th', 'model-col', `vs ${esc(B)}`));
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

    const ordered = state.modelOrder;

    const tbody = el('tbody');
    ordered.forEach((model) => {
      const tr = el('tr', model === B ? 'baseline-row' : '');
      const colorIdx = state.allModels.indexOf(model);
      tr.appendChild(el('td', 'model-col',
        `<span class="dot" style="--c:${modelColor(model, colorIdx)}"></span>${esc(model)}`));
      for (const [met, columns] of cols) {
        let s = 0, n = 0, any = false;
        for (const c of columns) {
          if (c.kind === 'mean') {
            const mv = meanVisible(idx, etype, met, model, leads);
            const mN = meanSample(idx, etype, met, model, leads);
            const mSmall = mN !== null && mN < minSample();
            const meanTd = el('td', 'cell mean' + (mSmall ? ' small-n' : ''),
              mv === null ? '—' : fmtVal(met, mv));
            meanTd.dataset.model = model;
            if (n) meanTd.style.background = model === B ? '' : cellColor(s / n);
            if (mSmall) anySmallN = true;
            attachTip(meanTd, etype, met, { kind: 'mean', mean: mv, rel: n ? s / n : null,
              nLeads: n, n: mN, small: mSmall });
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
          any = true;
          if (Number.isFinite(cell.rel)) { s += cell.rel; n += 1; }
          const small = Number.isFinite(cell.n) && cell.n < minSample();
          if (small) anySmallN = true;
          const td = el('td', 'cell' + (model === B ? ' baseline' : '')
            + (small ? ' small-n' : ''), fmtVal(met, cell.mean));
          td.dataset.model = model;
          td.style.background = model === B ? '' : cellColor(cell.rel);
          attachTip(td, etype, met, { kind: c.kind, lead, mean: cell.mean, rel: cell.rel,
            n: cell.n, lo: cell.lo, hi: cell.hi, small });
          tr.appendChild(td);
        }
        void any;
      }
      tbody.appendChild(tr);
    });
    /* B2 (DR2): roster models with zero score rows were silently dropped from
       the tables — show them muted with an explanation instead. Roster =
       meta-declared ∪ scored (same universe as the header kv), not the
       lead-filtered display order. */
    const declared = Array.isArray(state.meta.models) ? state.meta.models : [];
    const scoredSet = new Set(state.scores.map((r) => r.model));
    const universe = new Set(declared);
    state.allModels.forEach((m) => universe.add(m));
    for (const model of universe) {
      if (scoredSet.has(model)) continue;
      const tr = el('tr', 'unscored-row');
      tr.appendChild(el('td', 'model-col',
        `<span class="dot" style="--c:var(--ink-muted)"></span>${esc(model)}`));
      for (const [, columns] of cols) {
        for (const c of columns) {
          const td = el('td', 'cell empty', '—');
          td.title = `${model}: no score rows in this run — the model ran but `
            + 'produced no comparable data for these metrics (see About for model notes)';
          tr.appendChild(td);
        }
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    /* B1: roving tabindex — first tip-cell of each row is the tab stop */
    tbody.querySelectorAll('tr').forEach((tr) => {
      let first = true;
      tr.querySelectorAll('td[data-tip]').forEach((td) => {
        td.tabIndex = first ? 0 : -1; first = false;
      });
    });
    wrap.appendChild(table);
    block.appendChild(wrap);
    host.appendChild(block);
    // horizontal-scroll affordance: fade hint while the table is wider than its box
    const updScroll = () => {
      const over = wrap.scrollWidth - wrap.clientWidth > 4;
      wrap.classList.toggle('scrollable', over);
      wrap.classList.toggle('at-end', over && wrap.scrollLeft >= wrap.scrollWidth - wrap.clientWidth - 2);
    };
    requestAnimationFrame(() => { updScroll(); wrap.addEventListener('scroll', updScroll, { passive: true }); });
  }
  /* Small-sample warning (owner 2026-11-10): cells under minSample() events get
     a red edge + glow; say what that means once, next to the tables, and only
     when the current view actually contains such a cell. */
  const note = $('#sc-sample-note');
  if (note) {
    note.hidden = !anySmallN;
    if (anySmallN) {
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
    const rows = [`<div class="t-row"><span>mean value</span><b>${fmtVal(met, c.mean)} ${esc(mm.units)}</b></div>`];
    if (ref && model !== B) rows.push(`<div class="t-row"><span>${esc(B)}</span><b>${fmtVal(met, ref.mean)} ${esc(mm.units)}</b></div>`);
    if (c.rel !== null && c.rel !== undefined && Number.isFinite(c.rel) && model !== B) {
      rows.push(`<div class="t-row"><span>vs ${esc(B)}</span><b class="t-rel ${c.rel >= 0 ? 'pos' : 'neg'}">${c.rel >= 0 ? '+' : ''}${c.rel.toFixed(1)}%</b></div>`);
    } else if (model !== B) {
      rows.push(`<div class="t-row"><span>vs ${esc(B)}</span><b class="t-rel na">no skill ref</b></div>`);
    }
    if (Number.isFinite(c.lo) && Number.isFinite(c.hi) && c.kind !== 'mean') {
      rows.push(`<div class="t-row"><span>95% CI</span><b>${fmtVal(met, c.lo)} – ${fmtVal(met, c.hi)} ${esc(mm.units)}</b></div>`);
    }
    if (c.kind !== 'mean' && Number.isFinite(c.n)) {
      rows.push(`<div class="t-row"><span>n events</span><b>${c.n}</b></div>`);
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
    const who = td.dataset.model ? ` · <b>${esc(td.dataset.model)}</b>` : '';
    return `<div class="t-title">${esc(mm.label)}${mm.units ? ` [${esc(mm.units)}]` : ''} · ${esc(TYPE_META[etype].short)}${who}` +
      (c.kind === 'mean' ? ' · mean of visible leads'
        : c.kind === 'event' ? ' · event-level'
        : ` · lead ${leadLabel(c.lead)}`) + `</div>` + rows.join('');
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
  const parts = [td.dataset.model || TYPE_META[etype].short, mm.label,
    `${fmtVal(met, c.mean)}${mm.units ? ' ' + mm.units : ''}`];
  if (Number.isFinite(c.n) && c.kind !== 'mean') parts.push('n ' + c.n);
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
      `<span class="type-chip" style="background:${TYPE_META[etype].color}">${esc(TYPE_META[etype].short)}</span>mean over events`));
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
          name: model, type: 'scatter', mode: 'lines+markers',
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
      const layout = baseLayout(mode === 'rel' ? '% improvement vs ' + esc(state.baseline) : `${metricMeta(met).label}${metricMeta(met).units ? ' [' + metricMeta(met).units + ']' : ''}`, mode);
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
  if (ev.event_type === 'atmospheric_river') {
    if (Number.isFinite(ev.peak_value)) parts.push('peak IVT ' + Math.round(ev.peak_value) + ' kg m⁻¹ s⁻¹');
    // landfall_days can exceed the event length upstream (G-28): print the landfall date instead
    const lf = parseISO(d.landfall_time);
    if (lf) parts.push('landfall ' + lf.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }));
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
  const t = (TYPE_META[ev.event_type] || {}).short || ev.event_type;
  let region;
  if (viz) region = eventRegion(ev, viz);
  else {
    if (!ev._region || ev._regionRough !== !COUNTRIES) {
      ev._region = eventRegion(ev, null); ev._regionRough = !COUNTRIES;
    }
    region = ev._region;
  }
  return `${t} \u00b7 ${String(region).split(',')[0]}`;
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
   title and the viz frame range. */
function eventLastDay(ev) {
  const b = parseISO(ev.end);
  if (!b) return ev.end;
  return new Date(b.getTime() - 86400000).toISOString();
}

function renderEventTable() {
  const tbody = $('#event-table tbody');
  tbody.innerHTML = '';
  let n = 0, drawn = 0;
  for (const ev of state.events) {
    if (state.tab !== 'all' && ev.event_type !== state.tab) continue;
    if (!inRegion(ev)) continue;   // Region dropdown: list follows the region
    if (!matchesSearch(ev)) continue;
    n += 1;
    if (drawn >= EVENT_TABLE_MAX_ROWS) continue;   // count all, render a bounded prefix
    drawn += 1;
    const tm = TYPE_META[ev.event_type] || { color: '#999' };
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
      `<td><span class="type-dot" style="background:${tm.color}" title="${esc(ev.event_type.replace('_', ' '))}"></span></td>` +
      `<td><span class="ev-name">${esc(eventName(ev))}</span>` +
      `<span class="ev-sub">${short(ev.start)} \u2013 ${short(eventLastDay(ev))}${days ? ` \u00b7 ${days} day${days === 1 ? '' : 's'}` : ''}` +
      `<span class="ev-live" hidden> \u00b7 on map now</span></span></td>` +
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
    const note = searching ? 'No events match your search'
      : re && state.tab === 'all' ? `No events in ${re.label} this window`
      : re ? `No ${TYPE_META[state.tab] ? TYPE_META[state.tab].label.toLowerCase() : ''} in ${re.label} this window`
      : emptyLaneLine(state.tab);
    const crit = searching ? ''
      : re ? 'Events are assigned to regions by the centroid of their bounding box'
      : state.tab === 'all' ? 'Events must clear the detection criteria for their type in the 90-day window.' : TYPE_CRITERIA[state.tab];
    tr.innerHTML = `<td></td><td colspan="2"><span class="lane-empty"${crit ? ` title="${esc(crit)}"` : ''}>${esc(note)}${crit ? ' <span class="crit-mark" aria-hidden="true">ⓘ</span>' : ''}</span></td>`;
    tbody.appendChild(tr);
  }
  $('#event-count').textContent = `${n} event${n === 1 ? '' : 's'}`;
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
   current time step and which are selected; rows mirror that. */
let mapRowState = { active: [], sel: [] };
window.onMapEvents = (active, sel) => {
  const scroll = sel.length && sel.join() !== mapRowState.sel.join();
  mapRowState = { active, sel };
  markMapRows(scroll);
};
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
  head.appendChild(el('th', 'model-col', `this event · vs ${esc(B)}`));
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
      `<span class="dot" style="--c:${modelColor(model, state.allModels.indexOf(model))}"></span>${esc(model)}`));
    for (const met of metList) {
      const g = byMet.get(met);
      const v = val(g, model), ref = val(g, B);
      if (v === null) { tr.appendChild(el('td', 'cell empty', '—')); continue; }
      const rel = model === B ? null : relSkill(v, ref, met);
      const td = el('td', 'cell' + (model === B ? ' baseline' : ''), fmtVal(met, v));
      if (model !== B) td.style.background = cellColor(rel);
      td.title = `${model}: ${fmtVal(met, v)}${rel !== null && Number.isFinite(rel)
        ? ` (${rel >= 0 ? '+' : ''}${rel.toFixed(1)}% vs ${B})` : ''} — ${metricBlurb(met)}`;
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
  // one primary view at a time (QA 2026-11-06): home sections yield to detail
  $('#scorecard-sec').classList.add('hidden');
  $('#events-sec').classList.add('hidden');
  $('#global-sec').classList.add('hidden');   // one primary view at a time (QA 2026-11-06)
  document.body.classList.add('detail-open');
  syncRailActive();   // the section links are hidden here — nothing may stay marked
  const ev = state.events.find((e) => e.id === id);
  if (!ev) return;
  /* G-12: "Heat wave · S. Sudan" (type · region) — the dated data title stays in the data, the
     dates are in the story line. Region upgrades once the country table is loaded. */
  const h2 = $('#detail-title');
  const humanTitle = () => eventName(ev, state._vizFor === id ? state._viz : null);
  h2.textContent = humanTitle();
  h2.title = ev.title;
  const chips = $('#detail-chips');
  chips.innerHTML = '';   // one story line below carries type/region/dates/peak (QA 2026-11-06)
  const d = ev.details || {};
  void d;
  const story = $('#detail-story');
  story.textContent = storyLabel(ev);
  loadCountries().then(() => {           // upgrade vague band → named country (storyLabel re-geocodes)
    if (state.selectedEvent !== id) return;
    h2.textContent = humanTitle();
    story.textContent = storyLabel(ev, state._vizFor === id ? state._viz : null);
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
  if (vizHost && window.EventViz) {
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
      story.textContent = storyLabel(ev, viz);   // story + map badge must agree on max run
      h2.textContent = humanTitle();             // landfall-mask region upgrades the title too
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
          name: model, type: 'scatter', mode: 'lines+markers',
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
  syncUrl(!!nav.push);
  $('#detail-sec').classList.add('hidden');
  document.body.classList.remove('detail-open');
  $('#scorecard-sec').classList.remove('hidden');
  $('#events-sec').classList.remove('hidden');
  syncWindowSections();   // week-aware re-show (ponytail 2026-11-08 16:15)
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
/* rail: which section am I looking at                                 */
/* ------------------------------------------------------------------ */
/* The rail's section links mark the section under the top of the viewport, not
   the link you last clicked. Two sections and one probe line — a quarter of the
   viewport down (below the sticky phone topbar) — and the answer is the last
   section whose top has crossed it, so a section taller than the screen stays
   marked while you read it. rAF-throttled on scroll, and the marker is frozen
   during a click-jump (otherwise the smooth scroll walks the marker through
   every section on the way down and lands back where you started). */
const RAIL_SECTIONS = ['explore', 'scorecard-sec'];
let railRaf = 0, railJumpLock = false, railJumpTimer = 0;

function railVisible(id) {
  const s = document.getElementById(id);
  return s && !s.classList.contains('hidden') && s.offsetParent !== null ? s : null;
}
function currentRailSection() {
  if (state.selectedEvent) return null;      // detail view: the section links are hidden
  const bar = document.querySelector('.mobile-topbar');
  const barH = (bar && getComputedStyle(bar).display !== 'none')
    ? bar.getBoundingClientRect().height : 0;
  const line = barH + Math.min(160, window.innerHeight * 0.28);
  let cur = null, first = null;
  for (const id of RAIL_SECTIONS) {
    const s = railVisible(id);
    if (!s) continue;
    if (!first) first = id;
    if (s.getBoundingClientRect().top <= line) cur = id;
  }
  return cur || first;    // above the first section (the page title) = the first one
}
function paintRail(cur) {
  document.querySelectorAll('#rail [data-jump]').forEach((b) => {
    const on = b.dataset.jump === cur;
    b.classList.toggle('is-active', on);
    if (on) b.setAttribute('aria-current', 'true');
    else b.removeAttribute('aria-current');
  });
}
function syncRailActive() { paintRail(currentRailSection()); }
function syncRailSoon() {
  if (railJumpLock || railRaf) return;
  railRaf = requestAnimationFrame(() => { railRaf = 0; syncRailActive(); });
}
/* A jump owns the marker until the scroll settles: mark the target now, hand
   back to the scroll listener when the motion stops (or after 800 ms, whichever
   comes first — a reduced-motion jump never fires a second scroll event). */
function markRailJump(id) {
  railJumpLock = true;
  clearTimeout(railJumpTimer);
  railJumpTimer = setTimeout(() => { railJumpLock = false; syncRailActive(); }, 800);
  paintRail(id);
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
  document.querySelectorAll('[data-jump]').forEach((b) => {   // buttons, not #anchors: the hash carries app state
    b.onclick = () => {
      markRailJump(b.dataset.jump);   // hold the marker still while the page glides down
      document.getElementById(b.dataset.jump).scrollIntoView({
        behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    };
  });
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
  window.addEventListener('scroll', syncRailSoon, { passive: true });
  window.addEventListener('resize', syncRailSoon);
  syncRailSoon();
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
