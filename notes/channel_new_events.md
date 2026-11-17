# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.
(coldsnap copy: this worktree had no channel file after the 2026-11-16 owner
revert — the entries below are the reconstructed log of the earlier session,
written from the commits, plus the rebase entry.)

## [coldsnap] 2026-11-16 17:30 UTC — freeze <=0C patch applied (COMMON setup)

`git apply briefs-new-events/freeze_le0c.patch` → commit `freeze: <=0C at all
four steps (from owner patch)` (now `a2b6991`): `FREEZE_MAX_C = 0.0` in
`ewb_live/events/temperature.py`, applied to the daily mask before tracking on
both the frames path (`_mirror_t2m_daily(..., reduce="max")`) and the ARCO path
(`t2m_dmax` alongside `t2m_daily`), plus `recompute_merged_max_consec` and the
SPEC/detection.py/`test_frame_masks.py` bits. Raises if the freeze cap is asked
for with no 6-hourly temperature field — never silently drops the cap.

## [coldsnap] 2026-11-16 18:40 UTC — cold_snap registered as a parameterisation, not a copy

- **Decision (relationship with freeze, per the brief):** option (a) + a
  dedupe at publish time. `cold_snap` is the *same* p15 mask, so a freeze region
  is a subset of the cold-snap mask and every freeze spell would otherwise be
  published and scored twice. Chosen: the **raw catalogue** (`events.json`,
  `top_n=None`) keeps both lanes, each cold snap carrying
  `details.subzero_cell_fraction` / `details.contains_subzero`; the **curated**
  selection drops a cold snap that overlaps a freeze in time **and** space in
  favour of the freeze — the more specific claim — under the owner's existing
  no-overlap rule. Drop is recorded in `curation.json` as
  `reason: "freeze-subsumed"` (prepended to `select.LAST_DECISIONS`).
  Rationale: no information is lost (the freeze cap is derivable, and the
  fraction says how much of the spell froze), the two scoreable lanes stay
  independent, and the published event set never double-counts a spell. Needs an
  owner OK (asked in DONE).
- **Implementation is a thin parameterisation** of `temperature.py`:
  `MAX_C_BY_TYPE = {"freeze": 0.0, "cold_snap": None}` — the scanner, the
  footprint/core tracker, `EVENT_CLIP` (cold_snap shares freeze's p15 clip and
  the `EWB_LIVE_FREEZE_CLIP` override so the two p15 lanes cannot drift), the
  severity (`p15 anomaly × area × days`) and the merge envelope (16 d) are all
  shared. No copy of the scanner.
- **Frames lane needs no third layer:** `frame_masks.MASK_ALIAS =
  {"cold_snap": "freeze"}` — the published p15 bit *is* the cold-snap mask, and
  `qualified_at` reads layer 1 with the 200k km² floor.
- **Scoring:** EWB (1.1.1) has no cold-snap type and `event_type` is a free-form
  string used only to match a case to its `EvaluationObject`, so `"cold_snap"`
  passes through untouched and is scored with freeze's EWB ingredients
  (`era5_freeze_target`, MinMAE/RMSE/DurationMeanError). Same quantities,
  different event set.
- Files: `ewb_live/events/{cold_snap.py (new),temperature.py,__init__.py,
  select.py,qualification.py,frame_masks.py,types.py,validate.py}`,
  `ewb_live/{pipeline.py,scoring/evaluate.py,scoring/truth.py,viz/build.py,
  viz/timeline.py,viz/__main__.py}`, `ops/validate_selection.py`, `SPEC.md`.

## [coldsnap] 2026-11-16 19:30 UTC — dashboard lane

`TYPE_META`/`TYPE_SERIES_VAR`/`TYPE_ORDER`/`TYPE_CRITERIA`/`metricsFor`, the
merged-map lane (`mergedmap.js` TYPES/LANE_ORDER/counts/`layersForTab` →
cold_snap reads the P15 raw layer, no third temperature lane), `eventviz.js`
freeze-like ramp/legend/chip handling, About-dialog copy, and
`--series-cold-snap` teal tokens for both themes (light `#0e6d78` 6.04:1, dark
`#26808f` 3.29 on paper — freeze-family cold blue, distinct hue, L* between
freeze and heat). `tests/test_cold_snap.py`: 316 lines, synthetic lanes only —
cold-snap-only (p15 but warm), freeze+cold_snap, neither, the ARCO-path cap
table, `qualified_at` on the p15 layer, subsumption, severity/merge/scoring
parity with freeze.

## [coldsnap] 2026-11-17 03:40 UTC — REBASE onto main (e492a52) + DONE

Session restarted after the owner's revert; uncommitted dashboard/test WIP was
committed as `cold_snap: dashboard label/colour/criteria, mergedmap lane, About
text, unit tests` (backup of the pre-rebase branch: `backup/pre-rebase-main-coldsnap`),
then `git rebase main` onto `e492a52`. Five commits now sit on main:

    a0c7c9b cold_snap: dashboard label/colour/criteria, mergedmap lane, About text, unit tests
    58098c9 cold_snap: pipeline, scoring and viz lanes
    78d2d6a cold_snap: curation, publish gate and qualified_at
    d0c574d cold_snap: register the event type — p15 rule without the <=0C cap
    a2b6991 freeze: <=0C at all four steps (from owner patch)

Two conflicts, both resolved keeping **main** and re-applying only my additive
lines (nothing outside the slice was touched):

- `ewb_live/viz/build.py` `CELL`: main's `AR_COMPUTE_PAD_*` comment block kept
  verbatim, `cold_snap: 0.25` added to the dict.
- `dashboard/app.js` `TYPE_CRITERIA`: main's (newer, 3-consecutive-day paint)
  wording for `heat_wave`/`freeze` kept; `and at or below 0 °C` inserted into
  the freeze line (my branch carries the freeze cap) and a `cold_snap` entry
  added with main's map-shading wording.
- Auto-merged and re-checked against main: `mergedmap.js`, `index.html`,
  `styles.css`, `eventviz.js` (diff vs main is exactly my cold_snap additions).

**Tests after the rebase:** `.venv/bin/python -m pytest tests -q` → **310
passed, 2 failed, 29 skipped**. Both failures are pre-existing and environmental
(`FileNotFoundError: .../third_party/ExtremeWeatherBench/data_prep` in
`test_ar_floors_match_upstream_ar_bounds`, `test_upstream_consecutive_filter_and_blobs`)
— they fail on the base branch in this worktree too, and both need the EWB clone
(`third_party/` or `EWB_DATA_PREP_DIR`, present on the ewb LXC). `node --check`
clean on all three JS files. Playwright e2e (`-m e2e`) not run: it targets the
*deployed* dashboard at 192.168.0.121:8050, which is main, not this branch.
No real scans run locally (brief rule); everything is synthetic masks.

**For the orchestrator / owner:**

1. `ops/events_rebuild.sh` (main, `391e639`) md5-guards seven files including
   `ewb_live/events/temperature.py` against a hard-coded `WANT` list. Landing
   this slice changes that hash, so the script will `ABORT: code is not what was
   deployed` until `WANT` is recomputed on the LXC after deploy. Left untouched
   (main's version). Rebuild must run from the isolated tree as the script says.
2. `dashboard/globalmap.js` + `dashboard/rawmap.js` (make-nicer) are **not
   referenced by `index.html`** — the live map is still `mergedmap.js`, which is
   what I extended. If the redesign adopts `globalmap.js`, it needs a
   `cold_snap` entry in its `TYPES`/`LANE_ORDER` and `rawmap.js` needs nothing
   (cold snap shares the P15 layer). I deliberately did not edit them.
3. No heavy/scan work done; a real 90-day cold-snap scan + scoring on the LXC is
   the next step and will need `EWB_LIVE_MERGE_CAP_FREEZE_DAYS`-style env
   parity (cold_snap reuses the freeze merge cap deliberately).
4. Owner decision requested (see the 18:40 entry): raw catalogue keeps both
   lanes, curated selection drops freeze-subsumed cold snaps. If you'd rather
   freeze and cold_snap never coexist at all (or rather always coexist), it's a
   one-function change in `ewb_live/events/cold_snap.py`.

No credentials needed; nothing pushed; branch `aaTman/ne-coldsnap`.
