# Heat/freeze tracking: the footprint/core methodology (port of `aaTman/ewb-events`)

Owner rule, 2026-11-10. Supersedes, in order:

1. EWB's upstream blob tracker (`expand_event_bounds` + `_terminate_declined_events`) —
   see `notes/ewb.md`; it grew boxes 1°/iteration while an edge stayed half-active
   (7–10 M km² boxes off a 200 k km² seed), ended events relative to their own peak,
   and tracked one object as 2–3 overlapping events (live 2026-09-12 had three heat
   waves on one start date).
2. The same morning's interim area rule (200 k seed → seed bbox + 250 km pad → end
   below 100 k km², 31-day cap) — `notes/temperature_area_rule.md`. Its code
   (`ewb_live/events/temperature_detect.py`) is deleted by this change; the note stays
   as the record of why the pad rule was not enough: a fixed 250 km pad cannot follow
   an event that moves, and "in-box area below 100 k km²" never fires where the
   climatology is exceeded all season (Amazon, Sahara/Sahel, Gulf), so those spells
   needed an arbitrary duration cap.

## What the rule is

The short version is in SPEC §2. Mechanics, in the order the tracker applies them
(`ewb_live/events/event_tracker.py`, all thresholds in its `Params` dataclass):
region grouping (closing + local density) → candidate at 50 k km² → event at 200 k
km² of footprint → footprint that only ever grows over **valid** cells within
8 cells/day of what it holds (2026-11-20 rule, below) → end when the **core**
(valid every day since the event **qualified**, measured inside the **anchor
box** — the box as it stood on the later of day 3 and the qualification day;
owner decision 2026-11-21, below) holds < 100 k km². What a scan *publishes* as
the event rectangle is not the tracker box but the **persistence box** (bbox of
the cells valid on ≥ half the event's days, area-weighted 2–98 % trimmed).

The two ideas that actually buy something over the pad rule:

* **Qualify later than you seed.** A spell seeds at 50 k km² and has 5 days to reach
  200 k, so a heat wave that builds over three days keeps its early start date instead
  of starting on the day it crossed 200 k.
* **End on the core, not on the box.** A permanent p85 belt keeps 200 k km² *somewhere*
  in its box forever, but the cells that were valid on day 1 do not stay in place: the
  core drains and the event closes on its own (33 days is the live maximum, and it is a
  real central-African spell). No duration cap is needed, so `validate.MAX_DURATION_DAYS`
  moved 31 → 45 purely as a nonsense tripwire.

`Params` defaults are the reference CLI's defaults (checked against the reference run's
own `params` block): seed 50 000 km², qualify 200 000 km², end 100 000 km², min_days 3,
max_ramp_days 5, region_radius 6, region_min_fraction 0.3, move_cells 8, min_overlap
0.5, join_area 10 000 km², anchor_day 3, max_box_area None, and since
2026-11-21 `core_clock="qualified"` (the flood lane pins `"start"`).

## The 2026-11-21 merge-survivor rule (round 4): qualified events never merge

The third adversarial review found that the round-3 survivor rule (oldest
qualified member absorbs the group) **deletes larger, healthy qualified
events**: the dome object `heat_wave-20260917-321` (3.62M km2, qualified at
seed) was absorbed on 09-20 by a 0.48M km2 older neighbour that ended the next
day; the dome re-seeded a day late under a new id. Round 4 implemented three
candidates behind `Params.merge_survivor` and measured them on the real
lat>=-60 replay of the live window for all three hazards
(`scripts/review/hb08_merge_survivor.py`, outputs `out/hb08r4_*.json`):

| rule (heat/freeze/cold) | events | aged-Q lost | qual. same-day re-seeds | G1 dome | G2 | G4 | G5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `oldest_qualified` (round-3 shipped) | 159 / 13 / 30 | 25 / 1 / 1 | 24 / 1 / 1 | **fail** (09-21 object) | **4** | pass | pass |
| `largest` (M1) | 156 / 12 / 29 | 24 / 1 / 1 | 19 / 1 / 1 | pass | **7 (fails)** | pass | pass |
| `largest_close` (M2) | 163 / 12 / 29 | 24 / 1 / 1 | 19 / 1 / 1 | pass | 0 | pass | pass |
| `separate` (M3, **ships**) | 172 / 13 / 30 | 0 / 0 / 0 | 0 / 0 / 0 | pass | 0 | pass | pass |

(G2 = aged qualified members that vanished unpublished with no same-start
carrier; "aged-Q lost" counts absorbed qualified members of any age - under
M2 they publish, which is why its G2 is 0; re-seeds count same-day re-seeded
objects inside an absorbed member's box. G1 dome = the central-Africa spell
published as ONE event starting <= 09-17 running to ~10-06: M1/M2/M3 all
publish `09-17 -> 10-06` peak 3.62M km2 as one object, the round-3 rule
publishes the 09-21 re-seed.)

**Winner: `separate`, by the owner-brief's ranking** (fewest lost qualified
events, then fewest same-day re-seeds, then smallest event-count change).
`largest` fails G2 outright (7 heat events). M2 and M3 both zero G2, but M3
doesn't absorb any aged qualified member at all (0 vs 24) and leaves 0
qualified same-day re-seeds (vs 19-21); its cost is the third criterion -
heat publishes 172 events instead of 159 (+8% score jobs) because a
qualifying blob beside a qualified event is its own event instead of being
clipped into the neighbour, and heat box median moves 1.12M -> 1.21M km2
(fill 0.83 unchanged; G5's medians pass with margin). Two qualified objects
whose regions weld can now run side by side with overlapping boxes (see
`test_separate_default_splits_the_belt_instead_of_clipping_it`) - the owner
can flip to `largest_close` in one line if the product prefers fewer rows
over never truncating a spell.

Shipped semantics: contested cells go to the larger qualified event's claim
first (ties: oldest) - the claim step keeps every cell in exactly one event
regardless of rule; unqualified candidates are still absorbed by qualified
events, with their claim clipped into the survivor's box; qualification is
never inherited (round-3 invariant kept). `TRACK_RULE_REV` c3-v2 -> c3-v3
tags the temperature SCAN cache (scans re-run under the new rule; per-event
scores are keyed by `start|end|bbox` in `pipeline.fingerprint`, so only
events whose geometry actually moved re-score), while the flood lane pins
`merge_survivor="oldest_qualified"` in `FLOOD_PARAMS` so its published
`details.tracker_params` keeps naming the rule it actually runs. G6 on real
data: the current AND legacy rule digests of the new code match the 8b3b152
tree for all three hazards (six digests), the
flood fuzz golden (`61288b41...`) is byte-identical, and the pre-knob
`oldest_qualified` fuzz digest (`e588d54c...`, 323 events) is pinned in
`tests/test_event_tracker.py::test_merge_survivor_rule_fuzz_digests_are_pinned`
next to the shipped `separate` golden (`b72440c1...`, 409 events).

## The 2026-11-21 core clock: the end check starts at qualification

Owner decision 2026-11-21 (the round-2 review of `fix/heatwave-box-size`). The
shipped core counted cells valid on *every day since the candidate's start*, so
a spell that grew into qualification had a core of only its small seed area:
the day it reached 200 k km² it also fell under the 100 k km² end floor and was
closed in the same pass. Its `end` (exclusive) sat on the qualifying day, so
the qualifying day was not one of the event's days and the region was free to
seed a new object the same day — the live catalogue carries **34** events that
end the day they qualify (29 heat waves + 2 freezes + 3 cold snaps;
`scripts/review/hb05_qualify_fallbacks.py` attributes all 34 to empty
qualification-frame windows). `Params.core_clock` makes the clock explicit:

* `"qualified"` (default, the owner's rule): the core counts cells valid every
  day **since `qualified_date`**, and the **anchor** freezes on the later of
  `anchor_day` and the qualification day — never before it. On its qualifying
  day an event's core is its whole footprint, so the same-day ending is
  structurally impossible: every valid event satisfies `start <= qualified <
  end` and its qualifying day is one of its days (`tests/test_event_tracker.py::
  test_no_event_ever_ends_on_its_qualifying_day`).
* `"start"`: the shipped rule, unchanged, for anything that must not move — the
  flood lane (`FLOOD_PARAMS`, which is not re-measured against this decision)
  and the LEGACY knobs of the tracker tests.

A merge never hands qualification to a spell that never reached the floor on
its own footprint (round-3 review fix, 2026-11-21): a
qualified day is always a day the survivor's OWN footprint reached
`qualify_area_km2`, and an absorbed unqualified candidate disappears like any
younger member (its leftover region cells stay free to seed). The pre-fix rule
let the OLDEST member survive even when it was an unqualified candidate: it
inherited a younger member's qualification, the merge then clipped the
qualified cells into the candidate's small box, and the tracker published
"valid" events whose peak footprint sat below the qualify floor (second
adversarial review: 10 of 174 replayed heat events published with a 63k-195k
km2 peak, 28 heat and 1 freeze merge groups where an unqualified survivor
absorbed a qualified member; the real 469k km² spell it swallowed re-seeded a
day late). Round 3's survivor choice (oldest QUALIFIED member) is superseded by
round 4's merge-survivor rule below; the no-inherited-qualification invariant
survives unchanged. `test_merge_survivor_is_the_oldest_qualified_member` pins
the repro,
`test_fuzz_default_params_never_publish_a_sub_floor_event` fuzzes the
invariant, and `test_legacy_and_flood_results_are_unchanged_by_the_survivor_rule`
goldens that the LEGACY/flood parameter paths (their pinned
`core_clock="start"`) are byte-identical to the pre-fix code. With the
survivor rule there is no merge-day core clock any more: a survivor's clock
and anchor always start on its own qualifying day.
Everything else is untouched — start dates, the ramp rules, `max_ramp_days`, the
claim/seed/merge geometry and the persistence publish box — and
`TRACK_RULE_REV` moved `c3-v1 → c3-v2`, so the event cache and the frames-derived
detection are rebuilt together with the release.

Measured on the live lane (`scripts/review/hb01_box_growth.py`), **on
production's population** — round 3 replayed at the production scan's
`lat >= -60` clamp and with the merge-survivor fix, so the replay and the
catalogue now count the same world; round 2's tables (162 heat / 22 freeze /
35 cold on an Antarctica-inclusive globe) are superseded. Stages: `S0_current`
shipped → `C3_valid_box_persist` (core from start) → `C5_core_from_qualification`
= production now; 85 days 2026-08-21..11-13, freeze measured with production's
`<= 0 degC` ceiling (the p15 lane bits uncapped are the cold-snap population —
45 % of them survive the ceiling at lat >= -60). Re-measured 2026-11-21 with
the round-4 merge rule (`separate`) in the replay — the C3 and C5 columns and
the churn figures below are from that run (`out/hb01r4_*.json`); the round-3
figures for the superseded `oldest_qualified` replay were 148 heat events at
C3 and 159 at C5. Gates all PASS: selfcheck
(legacy-pinned replay == shipped tracker, 149/149 heat, 11/11 freeze, 24/24
cold_snap), prodcheck (C5-tuned replay == production `Params()`, 172/13/30)
and `prod_match` (the S0 replay vs the PRODUCTION catalogue: freeze 11/11 and
cold_snap 24/24 paired with every (start, end) exact; heat 149 replay vs 151
production, 149 paired, 148 exactly equal — all 151 production events read
`details.mask_source = era5_arco` because the lane does not span the live
90-day window, and the ARCO lookback/boundary pixels account for the
residue: `heat_wave-20260915-240` ends 09-18 in the lane but 09-17 under
ARCO, and the ARCO path split two spells the lane keeps single
(`2230575b4bb8` 09-05..09-10, `ba6e076fce14` 10-27..11-01)):

| metric | heat S0 | heat C3 | **heat C5** | freeze S0 | freeze C3 | **freeze C5** | cold_snap S0 | **cold_snap C3** | **cold_snap C5** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| events | 149 | 159 | **172** | 11 | 10 | **13** | 24 | 23 | **30** |
| fill, median | 0.52 | 0.75 | **0.83** | 0.42 | 0.69 | **0.85** | 0.48 | 0.78 | **0.81** |
| daily fill, median | 0.23 | 0.42 | **0.43** | 0.24 | 0.45 | **0.47** | 0.25 | 0.47 | **0.52** |
| box, median km² | 3.23M | 1.26M | **1.21M** | 2.41M | 0.83M | **0.55M** | 2.49M | 1.09M | **0.58M** |
| box, p90 km² | 16.45M | 4.13M | **3.23M** | 4.02M | 1.92M | **1.11M** | 5.57M | 2.60M | **2.59M** |
| box, max km² | 61.6M | 22.15M | **22.15M** | 5.90M | 3.14M | **3.14M** | 8.06M | 4.55M | **4.55M** |
| box / biggest day | 5.02× | 2.23× | **1.96×** | 4.59× | 1.86× | **1.67×** | 3.71× | 1.55× | **1.47×** |
| duration med/p90/max d | 4/7/32 | 4/7/28 | 4/8/28 | 3/4/6 | 3/4.1/5 | 4/5/6 | 3/4/6 | 3/4/7 | 3/5/7 |
| events with `qualified == end` | 29 | 30 | **0** | 2 | 2 | **0** | 3 | 3 | **0** |
| events over 45 d | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| merge pairs / max merged window | 45 / 28 d | 26 / 33 d | 27 / **33 d** | 2 / 5 d | 1 / 7 d | 1 / 8 d | 3 / 8 d | 1 / 7 d | 1 / 8 d |
| lost / gained vs S0 | — | 40 / 50 | 44 / 67 | — | 5 / 4 | 2 / 4 | — | 7 / 6 | 4 / 10 |

The qualification clock is not a box-size change — C3 already fixed the boxes —
it is a *spell-continuity* change, and it makes every metric above better or
level: the events that used to be cut off at their own qualifying day now live
long enough to be valid at all, which is where the +13 heat waves come from
(159 → 172 on the replay, churn 44 lost / 67 gained against the shipped rule,
C3 was 40 / 50).
Curation caps what ships — `select.rank_and_cap` keeps 15 heat waves per window
(`DEFAULT_TOP_N`; the live selection is 15 heat / 5 freeze / 8 cold snaps, and
re-running the real curation path over the fresh scan — `hb06_ship.py`,
exclusions included — ships 15 heat / 12 freeze / 15 cold snaps after the
release), so this changes which events are scored and grows the weekly
temperature score jobs from 28 to 42 (+14) on top of the one-off id re-hash the
release already carries (42 viz assets to rebuild on the first weekly run: 42
selected ids with no asset yet, 0 stamp mismatches — `hb06r4.json`; measured
per-asset build times from the production viz logs: temperature assets
median 55 s / mean 181 s / p90 489 s, all assets median 37 s / p90 344 s, so
42 rebuilds cost ~2,300 s at median, ~7,600 s at mean — over the weekly step's
budget at mean cost. The viz step now stops at a soft time budget
(`--time-budget-s`, 5,100 s from the weekly cycle) and still runs the global
scorecard steps, so an over-budget first run simply resumes later (assets are
id-keyed and skipped when current) — or is pre-warmed before release per the
checklist below).
Nothing breaks a duration gate: the longest
heat spell is 28 days against the 45-day publish ceiling
(`validate.MAX_DURATION_DAYS`), and no replayed event exceeds it. Curation merge
pressure barely moves (27 mergeable heat pairs, largest merged window 33 d, 5
over the 16-day `select.MERGE_CAP_DAYS` envelope — same 5 in the C3 replay,
and all still under the 45-day hard cap).

## The 2026-11-20 box-size change (strategy `C3_valid_box_persist`)

Owner complaint: heat-wave boxes were far bigger than the cells that qualify —
the median published box was 3.80 M km² with 1.23 M km² of ever-qualifying
land inside (fill 0.52, daily fill 0.24, box 4.9× the event's own biggest day;
`notes/heatwave_box_diagnosis.md` carries the attribution: 52 % whole-region
claims, 32 % merge box-unions, 11 % drift hops). Three tracking knobs and one
publish rule (`Params` fields; each stays reachable via explicit params, so
the old rule is reproducible):

* `claim_valid_only` — a claimed region continues an event only for its valid
  cells within `move_cells` of the cells the event holds; the rest of the
  region stays free and may seed its own candidate (the reference port's
  `share = isin(labels, hits)` became `share = isin(labels, hits) & valid &
  dilate(footprint, move_cells)`; kills H3/H4/H5 growth).
* `seed_valid_only` — a candidate's first footprint/box is the region's valid
  cells, never its closing-filled bridge cells.
* `merge_keep_box` — the merge's survivor (its oldest qualified member, or
  the oldest candidate when nothing in the merge qualified — round-3 fix,
  above) keeps its box; every other member's claim is clipped into it, while
  the survivor keeps growing by its own valid claims within `move_cells`
  (round-1 fix — clipping the survivor's own drift cells too split drifting
  spells in two). A merge never unions two boxes.
* `publish_box="persistence"` (+ `publish_min_fraction=0.5`, `publish_trim=0.02`)
  — the published Event bbox is `persistence_box()`: the bbox of the cells
  valid on ≥ `max(2, ⌈0.5 × days⌉)` days, trimmed to their area-weighted 2–98 %
  range (fallback: the ever-valid bbox). The tracker box keeps its
  follow/claim/anchor role and is reported in `details.tracker_box_area_km2`
  next to `published_box_area_km2`, `fill` and `daily_fill`.

The flood lane (`FloodParams`) pins the tracking knobs to the shipped rules —
the change was designed and measured for heat/freeze only.

Test coverage (`tests/test_event_tracker.py`): the C3 knobs are pinned per
rule (`test_claim_takes_only_valid_cells_inside_the_drift_window`,
`test_footprint_and_seed_box_hold_only_valid_cells`,
`test_merge_keeps_the_older_box`), the merge fix has its drift/split repro
(`test_drifting_event_keeps_its_own_cells_when_a_merge_is_clipped`) and the
`ending()`-vs-claim agreement its own test
(`test_ending_grows_only_over_the_claim_rule`); the shipped-knob versions of
the cap / capped-neighbour / release / anchor cases are the
`test_c3_*`/`test_c3_capped_*` tests, each annotated with why the C3 outcome
differs from the LEGACY-pinned one; the publish rule has the
`test_persistence_box_*` and seam cases.

Measured on the live window 2026-08-21..11-13 (LXC replay with the shipped
code — the review scripts replay through the production `Tracker.step()` and
both gates pass on production's population (the scan's `lat >= -60` clamp):
`selfcheck` reproduces the shipped tracker 149/149 heat, 11/11 freeze, 24/24
cold_snap, `prodcheck` reproduces the production-C5 run 159/13/30; see the
round-3 addendum in `notes/heatwave_box_diagnosis.md` for the full table):
heat fill median 0.52 → 0.74, daily fill 0.23 → 0.42, box median
3.23 → 1.17 M km², max 61.6 → 22.2 M km²; freeze fill 0.42 → 0.69, box median
2.41 → 0.83 M km²; cold_snap is re-scanned on the freeze mask, so it moves
with freeze (the published cold_snap catalogue today sits at fill 0.48, box
median 2.49 M km²). Event membership genuinely changes (a continent-wide
belt comes back as one object per hot region: 40/149 heat spells lose their
successor and 39 new ones appear under C3), the published ids re-hash once
(the bbox is in `make_event_id`), and every scoring region shrinks — so the
release rebuilds `events.json`, the id-keyed score cache and the scorecard
together.

The re-hash is a one-time cost, not a weekly one: the heat/freeze id now
hashes the **anchor bbox** (frozen day-3 box) rather than the published box,
so an ongoing event keeps its id while its persistence box is redrawn. Re-measured
by `scripts/review/hb04_ongoing_ids.py` on the shipped rule (`c3_qualify`, i.e.
`Params()` with the round-4 `separate` merge) after the merge-survivor fix: one
weekly extend (+7 d) keeps **all 10** ongoing heat-wave ids on the anchor basis
and only 4/10 on the old published-box basis; sliding the 90-day window start
+7/+14 d matches 8 of the 10 on the same-start basis and keeps **all 8**
matched ids on the anchor basis vs 3/8 published; the two that do not pair
(65f9f5758c55, 60dee55ef1cb) re-seed under shifted starts once the window head
moves — not id re-keys and not absorptions. The absorb/re-seed split that the old `oldest_qualified`
rule produced (the round-3 note had `db4e3331c5b6`, started 10-30, pairing with
nothing because the merge had folded it into a −2 d survivor) is gone: under
`separate` that spell keeps its own object and its id. `select.py`'s merged ids
and the other lanes are unchanged.

## Port layout

| reference (`aaTman/ewb-events`, branch `dev`, HEAD `e525504`) | this repo |
| --- | --- |
| `src/ewb_events/geometry.py` (Box, circular_span, dilate, label_regions) | `ewb_live/events/event_tracker.py` (same functions, same semantics) |
| `src/ewb_events/tracker.py` (Params, Tracker.step, detect_events) | `ewb_live/events/event_tracker.py` (`Params`, `Tracker.step(day, mask)`, `track_events(mask, dates, cell_area, params, hazard)`) |
| `src/ewb_events/masks.py` (frame decode → daily masks) | `ewb_live/events/frame_masks.py` (reads the lane's `index.json` + the same zlib/packbits frames through `ewb_live.viz.exceedance.frame_layers`) |
| `src/ewb_events/cli.py` (`event_record` JSON) | `ewb_live/events/temperature.py` (`Event` + `details`) |

Deliberate differences from the reference:

* `dt.date` days instead of `pandas.Timestamp`; `track_events` takes numpy arrays
  instead of an `xarray.Dataset`, so it is unit-testable without xarray.
* Boxes are reported on **cell centres** (`lats[i0]..lats[i1]`), the `ewb_live`
  convention every other producer uses; the reference reports cell **edges** (±0.125°).
  Every published bbox in this repo keeps the centre convention, and the antimeridian
  wrap convention of SPEC §2 is applied on top (`lon_min > lon_max`).
* The reference's `--cell-streak` experiment (a cell must pass N consecutive days before
  it counts as valid) is not ported: the lane already ANDs the four 6-hourly steps, and
  the production choice is the reference default `cell_streak = 1`.
* Inputs come from the published frames lane when it spans the window (the reference read
  its own downloaded copy of the same bytes); the ERA5 ARCO path through the upstream
  helpers stays as the fallback (different clip, different climatology mode, or a lane
  whose head/tail is behind the window — logged with the reason).

## Validation (LXC, 2026-11-10, `scripts/review/evt02_tracker_port_compare.py`)

Window 2026-08-11..2026-11-02, 336 frames, the reference's own downloaded lane, so both
sides read identical bytes.

* **Port vs reference:** 175 events each (153 heat_wave, 22 freeze). The sets of
  `(hazard, start, end)` triples are **identical** — 0 port-only, 0 reference-only — and
  peak-area medians/maxima agree (heat 676.6 M / 8 397.3 M km², freeze 614.4 M / 2 155.1
  km²). Duration medians 4 d (heat) / 3 d (freeze), p90 7 d, max 33 d.
* **Publish gate:** 0 rejects of 175 (the 33-day event passes under the 45-day ceiling).
* **vs the published catalogue** (deployed upstream-tracker output): heat 61 → 153 raw
  events, freeze 2 → 22. The published-only entries are mostly fragments of a longer new
  event (the matcher asks for ≥3 shared days and bbox IoU ≥0.15, and a new 20-day box is
  several times the area of the 3-day published fragment inside it), which is the point
  of the change: one spell, one event.
* **Funnel** (merge → rank → cap 15, with the still-live AR events unchanged):
  15 heat_wave / 12 freeze / 15 atmospheric_river curated. Curated heat durations are
  5–20 days (production shows 3–7), and freeze goes from 2 curated to 12.

## Open product questions (for the owner, not blockers)

* **Region chaining can make one giant object.** `region_radius` = 6 cells closes gaps of
  1.5°, which can chain the Sahara–Arabia–India p85 belt into one event: the window's
  largest footprint is 8.4 M km² and its box spans 30° of latitude. Since 2026-11-20 the
  claim rule bounds the damage (an event only grows over valid cells it can reach, and a
  merge cannot paint a partner's box over it), so the belt comes back as several
  region-sized objects; `Params.max_box_area_km2` (a cap stops growth and leaves the rest
  of the region to a neighbour event — covered by `tests/test_event_tracker.py`) and a
  smaller `region_radius` remain as cranks. Reference-faithful default is still no cap.
* **12 of 15 freeze slots** are now filled by percentile-relative freezes (peak values
  3–19 °C in desert regions, which SPEC §2 already documents as legitimate). If the
  dashboard should not spend a fifth of its cards on those, that is a curation-floor
  decision (`select._passes_floor`), not a detection one.
* **126 of 738 flood events qualify on their last day.** The flood lane keeps its
  own legacy qualification clock; the `core_clock` knob lives in the temperature
  tracker and cannot reach it. Out of scope for this change, listed for the owner.
* **The 22.15 M km² Africa/Middle East spell** survives every C3/C5 rule — it is a
  valid single object, not a bug; whether one card should score that much area is
  the same curation question as the quotas above (`Params.max_box_area_km2` is the
  detection-side crank if it is ever wanted).
* **Extra flood `details.tracker_params` keys are harmless** — neither the flood
  score-cache key nor `pipeline.fingerprint` reads them; no action needed.
* **Window-head truncation.** The lane is a rolling 84-day window; when it starts after
  the scan window, the scan falls back to ERA5 (and says so). Events cannot start before
  the lane's first day, hence `details.window_start_truncated`.
* **The published persistence box can be smaller than the qualifying footprint, and
  below the 200 k km² floor.** It keeps only cells valid on >= `publish_min_days` event
  days, so a spell that qualified in one push and then collapses publishes a rectangle
  under the qualify floor that qualified it - no single frame inside the published box
  reaches 200 k km² (C5 replay of the live window: 15 of 215 published events hold less
  than their largest qualifying-day footprint, 1 of 215 publishes a box below the
  200 k km² floor; per-event numbers `out/hb01r4_*.json`). Behaviour left as is;
  whether the published box should never be smaller than the qualify-day footprint (or
  such events be flagged/curated) is the owner's call.
* **`separate` lets two qualified objects run side by side.** Round 4's merge rule
  never absorbs a qualified event, so a blob whose region welds to a qualified event's
  region keeps its own object: overlapping published boxes for physically adjacent
  spells, +13 heat events vs the round-3 rule (see the round-4 section). If the
  product prefers one row per storm system, `Params.merge_survivor="largest_close"`
  is the measured alternative (all gates pass; truncates the smaller spell at the
  merge day instead of duplicating it).

## Release checklist (merge day, one-time)

Merging changes event geometry (new ids) and starts stamping viz assets, so the
first weekly after deploy rebuilds part of the asset tree. In order:

1. Merge via PR; CI gates as usual. Deploy `main` to the LXC per the usual path.
2. **Pre-warm the viz assets** off the weekly window (one run, resumable):
   `./ops/remote.sh 'cd /opt/ewb-live && .venv/bin/python -m ewb_live.viz --all --time-budget-s 3300'`.
   Expected as of 2026-11-21 (`hb06_ship.py --fresh`-equivalent run, output
   `out/hb06r4.json`): **42 rebuilds** - the 42 newly selected ids that have
   no asset yet (42 selected ids drop out and keep their old assets on disk;
   0 stamp mismatches). ~7,600 s at the measured mean per-asset cost, so
   budget for one resume. AR assets are current by rule (unstamped at schema
   3) and are NOT rebuilt.
3. Re-run the same command if the budget prints (`viz: time budget ... reached`)
   - completed assets match their stamp and are skipped, so the rerun resumes.
   `viz: global steps ran` must NOT appear; if it does, finish the warm-up
   before the weekly window instead of letting weekly burn its timeout.
4. Trigger the weekly run; check the log for `weekly: global scorecard steps
   still ran after the viz time budget` (must not appear) and that the asset
   count moved by the expected number.
5. **Re-check curation exclusions**: ids change with the geometry, so
   `data/curation_exclusions.json` entries pinned to old ids are inert - the
   excluded events reappear until re-excluded by id or by the operator's
   judgement (one-off curation review; owner call).
6. Rollback is a plain revert of the merge commit: nothing is deleted, old-id
   assets stay on disk and the old viz code ignores the new `event_stamp`
   field (schema stays 3). If AR viz code ever changes, bump
   `ASSET_SCHEMA_VERSION` to 4 - that is the one lever that invalidates the
   unstamped AR assets (SPEC: "bump when the viz artifact shape changes").

## Reproduce

```bash
# on the ewb LXC, scratch checkout /opt/ewb-heatbox (production /opt/ewb-live
# read-only; the round-2 evt02 tracker-port recipe lives in git history)
cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
  /opt/ewb-live/.venv/bin/python scripts/review/hb01_box_growth.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --published /opt/ewb-live/data/results/events.json \
    --start 2026-08-21 --end 2026-11-13 --selfcheck \
    --hazards heat_wave,freeze,cold_snap --emit-events out/hb01_c5_events \
    --out out/hb01
#   --selfcheck runs both gates: the pinned legacy fingerprint replays the
#   shipped tracker (flood/legacy regression on real data) and production
#   Params() replays the tuned C5 row exactly (round-4 note: --prodcheck is
#   not a flag; both gates live inside --selfcheck). --emit-events writes
#   the C5 stage as events.json-shaped files (input for hb05). Per-hazard
#   JSONs carry the churn lists (lost5/gained5/worst5_paired).
  /opt/ewb-live/.venv/bin/python scripts/review/hb04_ongoing_ids.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --start 2026-08-21 --end 2026-11-02      # ongoing@W1end, W2 = +7d, W2s slid +7d
  /opt/ewb-live/.venv/bin/python scripts/review/hb05_qualify_fallbacks.py \
    --events /opt/ewb-live/data/results/events.json \
    --lane /opt/ewb-live/data/results/raw_exceedance   # prints JSON (redirect it)
#   attributes every qualified_at fallback (all 34 in the live catalogue sit on
#   the exclusive end date, so the frame window is empty by construction; the
#   C5 replay shows 0 empty windows and 3 below-floor - see the round-3 addendum)
#   (run again per hazard on the emitted C5 files for the replay-side numbers)
  /opt/ewb-live/.venv/bin/python scripts/review/hb06_ship.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --prod-events /opt/ewb-live/data/results/events.json \
    --exclusions /opt/ewb-live/data/curation_exclusions.json \
    --prod-assets /opt/ewb-live/data/results/events \
    --viz-log /opt/ewb-live/logs/weekly-2026-11-19T17:00:00Z.log \
    --start 2026-08-21 --end 2026-11-13 --after-events-out out/hb06_new_events.json \
    --out out/hb06_ship.json
#   replays the real scan + curation path (exclusions included) for both
#   catalogues; reports what ships before/after, score-job delta, the viz
#   rebuild set, and viz timings read from the weekly-*.log viz-lines (the
#   weekly-*.log itself has no viz-timing lines - do not point --viz-log at it)
  /opt/ewb-live/.venv/bin/python scripts/review/hb07_subfloor.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --start 2026-08-21 --end 2026-11-13
#   the every-published-event-passes-the-frame-floor gate (0 violations)
  /opt/ewb-live/.venv/bin/python scripts/review/hb08_merge_survivor.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --start 2026-08-21 --end 2026-11-13 --hazards heat_wave,freeze,cold_snap \
    --out out/hb08
#   the merge-survivor gate run (G1 dome case, G2 lost-published-qualified check,
#   G4 floor gate, G5 box gates, G6 current/legacy real-data digests); writes
#   one JSON per hazard plus out/hb08_summary.json when --hazards has three
```
