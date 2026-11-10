# Regional breakdowns → the live site (integration handoff, 2026-11-10)

Who: regions lane (pi, worktree `regional-breakdowns`), on the owner's
"integrate into the main website" instruction.
Base: `aaTman/fable-review` @ `b901e2e` — byte-identical to what was deployed
(verified by md5 before deploying): app.js `77791cfb`, mergedmap.js `6d8b21c8`,
index.html `0ad31534`, styles.css `de3bbd18`, and `ewb_live/{pipeline,serve}.py`.
Integration branch: **`aaTman/regions-integration`** (this worktree). The
original pre-redesign lane history stays intact on `aaTman/regional-breakdowns`
(`d43b084..dbbf08c`).

## Coordination state

- fable-review owns `dashboard/*`; its freeze was lifted at 04:25Z
  (`REDESIGN LANDED`). I asked for a split of work at 03:50Z (orchestration
  message to `orca_session_id:claude_a3b3a772_…`).
- The fable session is **stopped by a provider spend limit** — its own transcript
  answers that message with "You've hit your monthly spend limit … session limit
  resets 8:20am (UTC)". Owner instruction was to keep going solo in that case, so
  this document is the review package for when it returns.
- Channel entries posted in `regional-breakdowns`, `dogfish` and `fable-review`
  copies of `notes/channel_design_frontend.md`.

## What the port looks like on the redesigned UI

The redesign's rule is "a control sits on what it changes". Region is a page-wide
filter (map + list + scorecard), exactly like the type filter, so it lives in the
**explore filter row beside the type tabs**, not in the scorecard toolbar:

```html
<div class="type-filter">
  <span class="ctl-label" id="type-tabs-label">Show</span>
  <div class="ctl-group" id="type-tabs" role="tablist" …></div>
  <div class="ctl-group ctl-region hidden" id="region-group">
    <label class="ctl-label" for="region-select">Region</label>
    <select id="region-select" title="Events, map and scorecard by region"></select>
  </div>
</div>
```

and the scorecard echoes it the way it echoes the type filter — `#sc-scope`
reads `in Europe · all regions` (the link resets to Global).

Follows the region: scorecard tables + skill charts + verdict strip (via
`scRows()`), per-lane `n events` + empty-lane copy ("No freezes in Tropics this
window"), header EVENTS stat, type-tab counts, event list, merged-map boxes /
gantt / count / section `aria-label`, merged-map projection + out-of-region veil.
Does **not** follow the region: model and lead rosters (they come from the global
scorecard, so switching regions never shrinks the model list).

Removed from the old lane: the Leaflet catalog-map extent code — the redesign
retired that map. Everything else is a port, not a rewrite.

## Diff to review

`git diff aaTman/fable-review..aaTman/regions-integration -- dashboard` —
app.js +221, mergedmap.js +84, index.html +4, styles.css +9, regions.json +10.
Anchor points worth reading:

| Where | What |
|---|---|
| `app.js` `state.region/regionData/regionHit` | lazy background load of `scorecard_by_region.json`; first paint never waits; archived weeks (no file) hide the control |
| `app.js` `scRows()` / `regionEntry()` | the only accessor the scorecard, skill charts and lane counts read |
| `app.js` `eventRegions()` | JS port of `ewb_live/regions.py::assign_regions` (eastward-arc centroid, ±20° subtropical rule); exposed as `window.EwbRegions` and parity-tested |
| `app.js` `setRegion()` / `syncUrl` / `syncFromLocation` | `#region=` push, Back/Forward, deep link (waits for the lazy file), unknown key dropped, week-switch reset |
| `mergedmap.js` `setRegion` / `fitProjection` / `dimOutside` | corner-bbox MultiPoint fit (Equal Earth is monotone → the region is an exact rect), veil with `--map-dim`, LUT lon centring on `ll[0]` |

## Data contract (unchanged from the lane's 2026-11-09 entry)

`data/results/scorecard_by_region.json` = `{baseline, regions:[{key,label,n_events,by_type}],
by_region:{key:{rows}}}`; `rows` is exactly `scorecard.json`'s row shape
(same reducer, same bootstrap seeds); **`global` is deliberately absent from
`by_region`** — `scorecard.json` *is* the global slice. `scorecard.json` itself is
byte-unchanged by this work.

## Deploy (live now)

Single-file scp only (standing rule while other lanes hold WIP in their trees —
a whole-tree `ops/remote.sh sync` from another lane once pushed an unfinished
redesign live). **Never** run `ops/remote.sh run|bg` from this worktree: both
rsync the tree first, and this tree is not the live tree.

Deployed to `/opt/ewb-live`: `dashboard/{app.js,mergedmap.js,index.html,styles.css,regions.json}`,
`ewb_live/{regions.py,pipeline.py,serve.py}`, `scripts/build_region_scores.py`,
then `systemctl restart ewb-live-web.service` (serve.py changed) and, in the box
venv, `python scripts/build_region_scores.py data/results` + the same for
`data/results/sample`.

Live md5s: app.js `2e6634e8`, mergedmap.js `e7ccc814`, index.html `9002884d`,
styles.css `ef36aff2`, regions.py `2a54a0fd`, pipeline.py `cedc7c15`,
serve.py `1c98c59d`.

Region JSON generated in place — `scorecard.json` / `events.json` / `scores.json`
md5-checked unchanged before and after. Weekly `pipeline.py` writes the file
itself from now on (next run Mon 07:00 UTC), and history weeks that predate it
answer 204 → the control hides itself.

Rollback: `notes/rollback_pre_regions/README.md` (six pre-deploy files, also at
`/opt/ewb-live/rollback_pre_regions_20261110/`).

## Verification

- `uv run pytest -q` → 223 passed, 28 skipped.
- `pytest -m e2e tests/e2e` with `EWB_LIVE_URL=http://127.0.0.1:8063` (local
  serve of this tree + real region JSON): 120 passed, 3 skipped.
- `pytest -m e2e tests/e2e` against the **deployed** box
  (`EWB_LIVE_URL=http://192.168.0.121:8050`): **120 passed, 3 skipped, 0 failed**
  — includes the redesign tests with the Region control visible on real data, and
  clears `notes/review_regions.md` item 4 (the two stale-deployment reds).
- Live spot check (chromium, 1440×900): dropdown = Global/Tropics/NH/SH/North
  America/Europe; EVENTS 28 global, 9/14/5/1/6 per region; map count and list
  count agree in every region; `#region=europe` deep link restores; `?week=2026-W45`
  hides the control; zero console errors. Screenshots:
  `notes/screenshots/live-regions-{europe,deeplink}.png`.

## Bug caught on live (fixed here, would have shipped numbers nobody could trust)

`scripts/build_region_scores.py` read only `events.json`. The scored universe is
`events_selected.json`, and the two are **not** nested: 4 of the box's 28 scored
events exist only in `events_selected.json`, so the first generated file reported
24 scored events and disagreed with `scorecard.json` for the same week
(regions: 24/8/11/5/1/5 instead of 28/9/14/5/1/6). `pipeline.py` was never
affected — it passes the in-memory curated set. Fix: the script unions both
files (selected wins on id clash) and warns when a scored event has no record;
regression test `tests/test_regions.py::test_build_region_scores_script_sees_every_scored_event`.
After the fix the script reproduces the pipeline-generated file exactly.

## Open (owner calls, from `notes/review_regions.md`)

1. Centroid assignment vs a >25 % footprint-overlap rule for thin atmospheric
   rivers (changes published numbers).
2. Optional: mute scorecard cells with n < 5 (Europe/SH pass the n ≥ 3 CI gate
   but some cells have zero-width CIs).
3. Public front still points at :8050 (app direct) rather than the :8080 nginx
   cache front — pre-existing, README §Public URL; unaffected by this work.
