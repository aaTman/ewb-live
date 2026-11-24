# Spec: home + detail simplification (owner 2026-11-06, see notes/design.md '## Simplification plan')

You own ALL FOUR: dashboard/app.js, dashboard/index.html, dashboard/styles.css,
dashboard/eventviz.js. Read notes/design.md's '## Simplification plan' first
(binding; also skim DESIGN-OWB via ssh root@192.168.0.121 cat
/opt/ewb-dashboard/docs/DESIGN-OWB.md §4-§6 for rhythm). Keep OWB tokens,
fonts, hash deep links (#event=&lead=), EventViz u8/v1 loader, reason lines,
graticule/contours — this is SUBTRACTION + layout, not a rewrite of the maps.

## Home
- Header: single row — brand title (text-lg) · week <select> · "updated
  {date}" meta · [About] button · theme toggle. Remove any other header
  text/chips/banners (keep sample-mode as a ONE-word badge with tooltip).
- New compact kv-line above scorecard (OWB kv-row style): events N ·
  models M · window 90 d · truth ERA5 — labels --text-2xs tracked
  --ink-muted, values mono --text-sm.
- Scorecard = hero. REMOVE from home DOM (or default-hidden behind
  controls): verdict-strip section, the sc-legend gradient wall (replace
  with a 12px swatch pair better|worse with tooltips), the entire
  'How skill evolves' chart SECTION (move the two trend plots inside a
  collapsed <details> labeled 'Skill vs lead' under the table).
- Lead control: default selection {24,72,120,240} h; collapsed pill row
  shows these + 'more' disclosure revealing the rest (incl. 6/12/18h).
- Model control: chips of models present in data only; keep select-all but
  default = models with most coverage capped at 6 (baseline always in).
- Event table + map: as-is minus copy (row count label stays).

## Event detail
- Header: title (text-lg) + region · dates · peak chips only. Kill bbox
  chip, detection string, truth pill (moves to About), story line → one
  short label-form sentence (≤ ~15 words, e.g. 'AR · Aug 6–10 · peak IVT
  1066 · US landfall' style; build from ev fields, no sentences).
- Observed map large: eventviz renders observed panel at full row width
  (~360px tall) as its own block ABOVE the model grid.
- Model comparison: chip picker row (models with data at this lead;
  top-3 by |rel skill| for THIS event preselected from app.js scores if
  available else first 3 in viz + baseline ifs_hres ALWAYS included and
  non-removable). Only selected models render maps (2-4 tiles,
  minmax(340px,1fr)). Re-clicking chip re-renders.
- Mini scorecard + per-event charts: inside <details> 'Scores', collapsed.
- eventviz.js API additions (keep old behavior intact):
  EventViz.render(host, viz, ev, {models: [names]}) — when provided,
  render ONLY observed + those models (baseline forced in if present).
  Keep initialLead/onLead contract. Observed panel = full-width block,
  model panels in grid below, colorbar under controls row.

## Detail cleanup in app.js
No raw bbox in UI, no details.detection strings, no peak_value_units
dupes. Deep-link restore must still work (hashParams). renderEventMini
stays but goes inside <details>. closeDetail resets everything incl.
model selection.

## Copy
Grep every user-visible string; shorten to labels; long explanations go
into About dialog (new <dialog> or div overlay, Esc close, OWB drawer
rules §6.7: right-anchored min(560px,92vw), scrim rgba(38,38,32,.28)).
Content of About: what this is (2 sentences), methodology (consecutive
days ≥ P95/P05 of OWB ERA5 1990-2019 climatology, truth ERA5 with last-6d
IFS analysis caveat, IVT≥threshold mask), metric meaning (rel skill vs
IFS HRES), model list with source-forcing (IFS vs GFS).

## Rules
node --check app.js/eventviz.js. No new libs. Don't touch ewb_live/ or
data/. Keep all CSS selectors other modules need (grep app.js for class
names before deleting a rule). Playwright verify (.venv, --no-sandbox,
serve .venv/bin/python -m ewb_live.serve --port 8078): home has ≤ 1 h2
visible above fold, no verdict strip, deep link still opens detail with
observed map + ≤4 model maps + picker chips; details collapsed by
default; About opens/closes/Esc; 0 console errors; screenshots
notes/screenshots/simplify-after-{desktop,mobile}.png (desktop 1440x900,
mobile 400x850, plus detail). Report what you deleted.
