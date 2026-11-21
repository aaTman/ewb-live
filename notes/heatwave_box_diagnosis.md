# Why heat-wave boxes are bigger than the cells that qualify

Diagnosis and strategy evaluation (PLAN Phase 1+2), 2026-11-20, agent
`hb-diagnose`. Nothing here changes `ewb_live/`; every rule below was measured
with `scripts/review/hb01_box_growth.py`, which replays the shipped tracker
(`ewb_live/events/event_tracker.py`, SPEC §2) on the same lane frames
`scripts/review/evt02_tracker_port_compare.py` uses and adds a per-day growth
log plus the strategy sweep. Its `--selfcheck` proves the instrumented tracker
reproduces the shipped one event-for-event (162 heat / 35 freeze, identical
ids, dates and boxes).

Window: **2026-08-21 .. 2026-11-13**, the 85 complete days the published
`raw_exceedance` lane holds inside the live 90-day window (meta window
2026-08-21..2026-11-19, lane cutoff `2026111318`). Grid 721×1440 (0.25°),
510.1 M km², of which 147.3 M km² (29 %) is land.
Raw outputs: `/opt/ewb-heatbox/out/hb01_heat_wave.json`,
`hb02_hw_heat_wave.json`, `hb02_fz_freeze.json` (LXC, scratch tree only).

## 1. What "valid" and "fill" mean here

**Valid heat-wave point** — a 0.25° grid cell on one UTC day whose *daily*
mask is True: all four 6-hourly 2 m T steps (00/06/12/18Z) past the OWB p85
bound, land only. That is literally the tracker's input: the lane bits
(`ewb_live/events/frame_masks.py`, four steps ANDed) are the same bytes the map
paints and the same mask `track_events` consumes, so the replay cannot drift
from the detector. Freeze is the p15 mirror of the same rule. (The lane's
freeze bits do not carry production's extra ≤0 °C daily-max cap —
`temperature.py` applies it after the mask is built — so the freeze column here
is the p15 spell population, i.e. published `freeze` + `cold_snap` together:
35 replay events vs 11 + 24 published. The box mechanics are identical.)

**Fill** — for one published box:

```
fill = Σ area(cells valid on >= 1 event day, inside the box)
       / Σ area(land cells inside the box)
```

Land (Natural Earth 110, `ewb_live/viz/build.land_mask`) is the denominator
because ocean cells can never be valid; with the whole box area as denominator
every fill number would drop by the box's land fraction (~3.4× globally).
Companion numbers, same box:

* **daily fill** — median over the event's days of (valid that day inside the
  box / box land area). This is what the dashboard's box actually contains on a
  given day, and it is what the score is diluted by.
* **box/peak** — box grid area ÷ the tracker's peak footprint area. 1.0 means a
  box that just fits the biggest day.
* **excess_out** — land inside the box but outside the bbox of the event's
  valid cells (the box reaching past the event) vs **hole** — land inside that
  bbox that was never valid on any event day (the box's empty interior).

## 2. Current state

Replay of the shipped tracker (162 heat waves) and the live catalogue
(`data/results/events.json`, 151 heat waves), both measured on the same lane
masks:

| | events | fill p10 / med / p90 | daily fill (median event) | box km² med / p90 / max | box land km² med | ever-valid km² med | peak km² med | box/peak med | lat° med/p90 | lon° med/p90 | boxes >5 M / >20 M |
|---|---|---|---|---|---|---|---|---|---|---|---|
| heat replay (`S0`) | 162 | 0.30 / 0.52 / 0.72 | 0.24 | 3.80 M / 16.60 M / 61.60 M | 2.75 M | 1.23 M | 0.72 M | 4.9 | 16 / 34 | 22 / 84 | 66 / 12 |
| heat live | 151 | 0.29 / 0.52 / 0.67 | 0.23 | 3.37 M / 16.62 M / 47.31 M | 2.61 M | 1.16 M | 0.68 M | 5.0 | 16 / 35 | 20 / 64 | 59 / 10 |
| freeze replay | 35 | 0.34 / 0.46 / 0.62 | 0.24 | 2.69 M / 7.98 M / 24.60 M | 1.97 M | 1.04 M | 0.79 M | 4.3 | 14 / 22 | 35 / 198 | 8 / 1 |
| freeze live | 11 | 0.36 / 0.46 / 0.66 | 0.24 | 2.41 M / 3.49 M / 5.90 M | 1.73 M | 0.82 M | 0.56 M | 4.6 | 14 / 20 | 36 / 46 | 1 / 0 |

Read it as: **the median published heat-wave box covers 3.80 M km² of which
2.75 M km² is land, and only 1.23 M km² of that land was ever a qualifying
cell on any of the event's 4 days** — 0.24 of the box is qualifying on a typical
day, and the box is 4.9× the event's own biggest day. 154 of the 162 boxes
exceed 1 M km², 66 exceed 5 M km², and 12 exceed 20 M km².

The live catalogue and the replay agree (fill median 0.52 both, box median
within 11 %), which is itself a result: what the site draws *is* the tracker's
box (§3, H6). The live window runs to 2026-11-19 while the lane stops at
2026-11-13, so production read ERA5 ARCO for that run (`details.mask_source`);
the numbers above show the ARCO and lane masks give the same picture.

### The five worst heat-wave boxes

| id | dates | days | box km² | box land km² | ever-valid km² | fill | peak km² | span | dominant cause (from the growth log) |
|---|---|---|---|---|---|---|---|---|---|
| `heat_wave-20260821-10` | 08-21 .. 09-21 | 32 | 61.6 M | 39.9 M | 32.5 M | 0.81 | 8.4 M | 78° × 75° | H3: 45.5 M of the 58.1 M km² it added over 15 growth days were far-outside-the-drift-window region cells (+12.7 M from a merge, 3.5 M at seeding) |
| `heat_wave-20261013-548` | 10-13 .. 10-19 | 7 | 45.5 M | 38.4 M | 13.2 M | 0.34 | 5.0 M | 61° × 98° | H2: 43.0 M km² of the 45.3 M it added came in as a merged partner's whole box; it seeded at 0.2 M km² |
| `heat_wave-20261014-557` | 10-14 .. 11-03 | 21 | 42.1 M | 29.1 M | 20.3 M | 0.70 | 6.7 M | 60° × 59° | H2 (20.2 M) then H3 (16.3 M); 4.7 M at seeding |
| `heat_wave-20260918-279` | 09-18 .. 09-22 | 5 | 37.1 M | 30.2 M | 10.5 M | 0.35 | 4.8 M | 49° × 113° | H3: 31.0 M of 36.3 M km² over 4 growth days — a 5-day event drawn across half Asia |
| `heat_wave-20261105-780` | 11-05 .. 11-08 | 4 | 36.3 M | 26.2 M | 13.5 M | 0.52 | 7.9 M | 45° × 67° | no growth at all: the box is the labelled region's bbox on the seeding day |

The worst freeze boxes are the same pathology in the southern hemisphere:
`freeze-20260922-110` spans -90°..-64.8° and all 360° of longitude (5 days,
fill 0.37, box 24.6 M km², H3 for 19.7 of the 19.7 M km² it grew).


## 3. Where the extra area comes from (H1..H7)

`LoggingTracker` (in the review script) snapshots every open object's box
before each `Tracker.step`, then charges the added grid cells to the first
category that fits: merged partner's box (H2), a cell inside today's labelled
region but not valid today (H4), a valid cell adjacent to yesterday's
footprint (H1), a valid cell reachable only through the `move_cells` dilation
(H5), a valid cell *outside* that dilation, which only a whole-region claim can
reach (H3). A box step also sweeps in the rectangle's own interior — cells that
are neither valid nor region (H0, 70 % of the raw added area) — and the log
charges that interior to the day's *driving* cells, because the rectangle is
only there because of them. Both breakdowns are in the JSON
(`cells_by_cat_km2`, `attributed_share`).

| cause | added box area, heat waves | share | share, freezes | one-line mechanism |
|---|---|---|---|---|
| **H3** whole-region claim | 533.5 M km² | **51.6 %** | 68.8 % | the footprint takes a whole labelled region, and a candidate's first box is that region's bbox |
| **H2** merge box-union | 332.3 M km² | **32.4 %** | 11.4 % | the survivor adopts the partner's entire box, holes included |
| **H5** drift hop | 103.9 M km² | 11.3 % | 15.5 % | `move_cells`=8 lets a footprint land 2° away and the box swallows the gap |
| **H4** closing fill | 13.3 M km² | 4.5 % | 4.0 % | closing-filled bridge cells are claimed although they never pass the daily rule |
| **H1** contiguous growth | 1.1 M km² | 0.2 % | 0.3 % | the ring of new cells as the spell itself grows |
| total over 784 growth days | 984.1 M km² | 100 % | 140.4 M km² (160 days) | plus 271.7 M km² (heat) / 54.9 M km² (freeze) already present in the seed boxes |

(Heat-wave growth days: 784 over 162 valid events; the raw cell categories before
the rectangle interior is re-charged are in `cells_by_cat_km2`: 690 M km² of the
984 M is bbox interior, which is charged to the day's driving cells.)

### Verdicts, with the code on `origin/develop`

* **H3 — whole-region claim: the main cause.** `Tracker.step` takes the
  *entire* labelled region into the footprint,
  `share = np.isin(labels, list(group_hits)) & ~taken`
  (`event_tracker.py:299`), and the follow test only asks that the region
  overlap the drifted footprint by `min_overlap` = 0.5 of the **smaller** side
  (`:384-392`) — a 60 k km² footprint therefore claims a 6 M km² region whole.
  The seed box is born the same way: `Box.from_mask(region)` for the whole
  labelled region (`:335-345`), which is why `heat_wave-20261105-780`
  (36.3 M km², 4 days) never grows: its box is already that big on day 1.
  52 % of all box growth, and the seeding half of it is invisible to a
  growth-only count — the median *seed* box of an event that becomes valid is
  already **0.82 M km²** (max 23.9 M km²; 271.7 M km² summed over the 162 heat
  waves, i.e. 26 % of their eventual box area exists on day 1; for freezes the
  ratio is 40 %).
* **H2 — merge box-union: the second cause, 32 %.** When two objects claim the
  same region the survivor adopts `ev.box.union(other.box, nx)` (`:295`) and
  `merge_groups` unions boxes while testing `fits` (`:427-428`): the whole
  partner rectangle arrives, including its own holes. The single biggest box
  step in the whole window is one of these: `heat_wave-20261013-548` on
  2026-10-17 adds 33.8 M km² of box — 28.4 M km² of it land — in one day. Of
  the 12 largest growth days in the window, 9 are merge days and 3 are
  whole-region claims; together they add 203 M km², 21 % of all growth.
* **H5 — drift allowance: 11 %.** `follow` dilates the footprint by
  `move_cells` = 8 cells (2°)/day (`:384`), so a footprint can hop onto a
  neighbouring blob and the box then contains the 2°-wide no-man's-land.
* **H4 — closing fill: 4.5 % directly.** `label_regions` (`:119-146`) fills
  gaps up to 2·`region_radius` = 12 cells where the 13×13 density ≥ 0.3, and
  those never-valid cells enter `share` and can stretch the box. Its indirect
  share is bigger (it is what lets H3 claim a chained belt), but tightening the
  grouping alone buys little (S4a/S4b: fill 0.53), which bounds it.
* **H1 — cumulative box: the container, not the source.** Contiguous growth
  accounts for 0.2 % of the added area. The "box = bbox of every footprint
  ever, never shrinks" rule is what makes a single H3/H5 day permanent, and it
  is why the unfilled land sits mostly *inside* the valid-ever bbox (holes,
  421 M km²) rather than outside it (4 M km²).
* **H6 — downstream (temperature.py / select.py): refuted.**
  `temperature.py:427-436` publishes exactly `ev.box` (cell centres + the wrap
  convention), no pad. `select.py` unions boxes only inside
  `_merge_component` (`:407`, via `_bbox_union` at `:366`) and **0 of the 151
  published heat-wave events** carry `details.merged_from`; the replay and the
  published catalogue agree to within 11 % on the median box (3.80 M vs
  3.37 M km²) and 0.01 on the median fill (0.524 vs 0.520). One caveat worth
  telling the owner: `select.CAP_LAT_SPAN`/`CAP_LON_SPAN` (40°/60°,
  `:317-318`) cap *merged* bboxes only, so a single tracker box spanning
  78° × 75° is never questioned by curation.
* **H7 — wrap/seam: refuted as a bug.** `circular_span` (`:57`) picks the
  shortest arc and `Box.union` inherits that, so a box cannot take the long way
  round: for 158 of 162 events the box's column arc equals the arc its own
  valid cells need, and the four exceptions are ≤ 13 columns (3.25°) wider —
  ordinary bbox slack, not a seam error. 3 growth days crossed the seam
  legitimately. One real effect survives, and it is not a bbox bug: 4 of the
  162 heat waves (`-20260904-162`, `-20260930-391`, `-20261015-565`,
  `-20261028-714`, all 15-26° of latitude band on the Southern Ocean ring the
  hemisphere and legitimately span 360° of longitude (8.5-27.0 M km²). No bbox
  rule can shorten those objects; only splitting the spell or a rolling window
  can.

## 4. Strategy comparison

Every strategy ran on the identical masks, cell areas and window. **churn** in
the table is published-object churn against the shipped catalogue: a baseline
spell counts as lost if no strategy object covers ≥ 3 of its days *and*
overlaps its box (greedy, longest spells matched first); `new` counts strategy
objects nobody owns; the bracket is how many objects keep their replayed
*tracker* id (`hazard-YYYYMMDD-<seed counter>`, the harness's own label for a
tracked object — the site's published id is a different thing, see §5). The
per-strategy JSON also carries `churn_vs_current_tracker`, the same measure on
the tracker's *own* events (bbox IoU ≥ 0.15), which is what `start` dates and
`ongoing` depend on. Runtime is that strategy's replay + scoring, per hazard.

| strategy | what changes | fill med | fill p10 | daily fill med | box med | box p90 | box max | box/peak med | events | days med/max | churn lost/new (obj ids kept) | freeze daily / fill | s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `S0_current` | as shipped: cumulative tracker box | 0.52 | 0.30 | 0.24 | 3.80 M | 16.60 M | 61.60 M | 4.9 | 162 | 4/32 | — | 0.24 / 0.46 | 49 |
| `S1a_cap_3Mkm2` | `max_box_area_km2=3e6` | 0.65 | 0.34 | 0.35 | 2.19 M | 2.90 M | 3.00 M | 3.6 | 218 | 4/28 | 23/79 (7) | 0.28 / 0.52 | 62 |
| `S1b_cap_1.5Mkm2` | `max_box_area_km2=1.5e6` | 0.70 | 0.35 | 0.40 | 1.19 M | 1.45 M | 1.50 M | 3.0 | 177 | 4/20 | 53/68 (7) | — | 56 |
| `S2a_persist_2d` | publish = bbox of cells valid ≥2 days | 0.57 | 0.35 | 0.27 | 3.12 M | 16.36 M | 61.60 M | 4.3 | 162 | 4/32 | 0/0 (162) | — | 45 |
| `S2b_persist_half` | publish = bbox of cells valid ≥50 % of days | 0.59 | 0.35 | 0.29 | 2.83 M | 15.25 M | 40.54 M | 3.9 | 162 | 4/32 | 0/0 (162) | — | 45 |
| `S2c_persist_half_trim` | S2b + 2–98 % area-weighted trim | 0.69 | 0.41 | 0.38 | 1.99 M | 7.67 M | 33.71 M | 2.5 | 162 | 4/32 | 0/0 (162) | 0.43 / 0.71 | 45 |
| `S3_claim_overlap` | claim only valid cells within `move_cells` | 0.57 | 0.36 | 0.28 | 2.51 M | 9.39 M | 36.32 M | 4.9 | 161 | 4/28 | 32/31 (15) | — | 56 |
| `S3v_seed_valid` | seed box = region's valid cells only | 0.52 | 0.30 | 0.24 | 3.72 M | 17.00 M | 61.60 M | 5.0 | 159 | 4/32 | 5/2 (35) | 0.24 / 0.45 | 44 |
| `S3p_overlap_persist` | S3 tracking + S2b box | 0.63 | 0.43 | 0.33 | 1.99 M | 9.17 M | 36.32 M | 3.8 | 161 | 4/28 | 32/31 (15) | 0.34 / 0.62 | 57 |
| `S4a_group_r3f40` | `region_radius=3`, density 0.40 | 0.53 | 0.30 | 0.25 | 3.12 M | 14.42 M | 45.51 M | 4.6 | 170 | 4/32 | 14/22 (13) | — | 53 |
| `S4b_group_r2f50` | `region_radius=2`, density 0.50 | 0.54 | 0.32 | 0.25 | 2.82 M | 13.61 M | 51.84 M | 4.8 | 171 | 4/32 | 23/32 (8) | — | 48 |
| `S5_merge_keep_box` | merges keep the older box | 0.53 | 0.31 | 0.26 | 2.81 M | 13.31 M | 45.04 M | 4.3 | 160 | 4/32 | 27/25 (11) | 0.24 / 0.48 | 52 |
| `S6_split_lowfill` | split fill<0.5 spells into components | 0.53 | 0.31 | 0.23 | 3.04 M | 15.55 M | 61.60 M | 4.6 | 179 | 4/32 | 0/17 (150) | — | 45 |
| `S7a_rolling5` | box = bbox of the last 5 days' footprints | 0.55 | 0.30 | 0.27 | 2.96 M | 14.15 M | 45.51 M | 4.5 | 158 | 4/32 | 11/7 (30) | — | 45 |
| `S7b_rolling5_trailing` | S7a + persistence of the last 5 days | 0.61 | 0.37 | 0.31 | 2.50 M | 12.47 M | 44.70 M | 3.7 | 158 | 4/32 | 11/7 (30) | — | 47 |
| `C1_overlap_merge_persist` | S3+S5 tracking + S2c box | 0.78 | 0.50 | 0.43 | 1.13 M | 4.81 M | 32.96 M | 2.2 | 159 | 4/28 | 51/48 (10) | 0.50 / 0.77 | 61 |
| `C2_overlap_merge_group_persist` | C1 + `region_radius=4`, density 0.40 | 0.79 | 0.52 | 0.45 | 1.07 M | 3.84 M | 17.61 M | 2.1 | 161 | 4/30 | 55/54 (7) | — | 65 |
| **`C3_valid_box_persist`** | **S3+S3v+S5 tracking + S2c box** | **0.79** | **0.51** | **0.43** | **1.11 M** | **4.71 M** | **19.46 M** | **2.0** | 161 | 4/28 | 48/47 (10) | **0.49 / 0.80** | 64 |
| `C4_valid_box_only` | C3's tracking, tracker's own box | 0.61 | 0.39 | 0.30 | 1.81 M | 7.71 M | 25.89 M | 3.6 | 161 | 4/28 | 47/46 (10) | 0.32 / 0.59 | 64 |
| `P0_published_json` | live `events.json`, on the lane masks | 0.52 | 0.29 | 0.23 | 3.37 M | 16.62 M | 47.31 M | 5.0 | 151 | 4/32 | — | 0.24 / 0.46 | 0 |

**Correction (2026-11-21, fix round 1):** this table was measured with the
design-phase `TunedTracker`, a hand-copied replay of `Tracker.step()` that
drifted from production. The implementation round re-measured the shipped
C3 with the production code (`hb01 --selfcheck --strategies
C3_valid_box_persist`, both gates PASS: the tuned strategies now inherit
`Tracker.step()` and drive it only through `Params` knobs). Corrected C3
heat row: fill med **0.76**, p10 0.51, daily fill 0.43, box med **1.14 M**,
p90 4.70 M, max **22.15 M**, box/peak 2.2, **163** events, churn 45/46 (10
id-paired). freeze/cold_snap row: fill **0.78**, box med **0.98 M**, p90
3.03 M, max 11.80 M, 33 events, churn 13/11. The design-phase numbers in
the table stand as relative comparisons between strategies, not as the
absolute C3 outcome - see "Implementation result" for the final table.

**daily fill med** is the number a reader perceives: the median, over an event's
days, of the share of the box's land that is actually qualifying that day
(0.24 today). Freeze columns are the same strategies replayed on the freeze lane
(35 events shipped), as `daily fill / fill`; `—` means the strategy was not
replayed for freezes (the heat-side mechanism is identical there). Full freeze
numbers are in `hb02_fz_freeze.json` / `hb02_fz_summary.json`.


What the sweep says:

* **The publish box alone cannot fix it.** `S2a`, the bbox of every cell the
  event ever held as valid (the most generous honest box), moves the median
  only from 3.80 M to 3.12 M km² and leaves the 61.6 M km² tail untouched — the
  cells an event *owns* are already spread over a continent. Tightening the box
  without tightening the claim just draws a smaller rectangle around a
  meaningless set of cells.
* **Persistence plus trim is the best one-line change.** `S2c`: fill median
  0.69 (p10 0.41), box median 1.99 M km², worst box 33.7 M km², with *zero*
  churn, the same 162 events, every id kept. Freeze side 0.71 fill / 1.22 M km².
* **The cap rule is not a fix.** `S1a` (3 M km²) buys a box p90 of 2.90 M km²
  but breaks 23 spells and creates 79 new objects (218 events vs 162): a hard
  cap stops growth exactly where the tracker is doing its job (a real belt), so
  objects fragment and re-seed. Useful only as a safety net, not as a policy.
* **Splitting low-fill spells does not work** (`S6`, fill 0.53): the pieces'
  bboxes are still the belt, just relabelled.
* **Fixing the claim is what closes the tail.** `S3` alone already cuts 25 M
  km² off the worst box; with the merge rule (`C1`) and the seeding rule (`C3`)
  the median box is 1.11 M km² at fill 0.79. `C4` proves the other half is
  needed: the same tracking change with the *tracker's* box still sits at 1.81 M
  km² median and fill 0.61.
* `C3` beats `C2`: reaching for `region_radius=4` to kill the last of the tail
  costs 7 more broken spells than `C3` and buys 1.8 M km² off the maximum.

The 5 worst heat-wave boxes, before and after (date-and-location matched
successor; `pieces` = how many strategy objects cover the old spell):

| old id | before | `S2c` after | `C3` after |
|---|---|---|---|
| `heat_wave-20260821-10` | 61.6 M km², fill 0.81 | 13.8 M km², fill 0.91 (4 pieces) | 7.5 M km², fill 0.95 (14 pieces) |
| `heat_wave-20261013-548` | 45.5 M km², fill 0.34 | 33.7 M km², fill 0.36 (2) | 6.6 M km², fill 0.80 (2) |
| `heat_wave-20261014-557` | 42.1 M km², fill 0.70 | 19.5 M km², fill 0.75 (3) | 8.6 M km², fill 0.90 (2) |
| `heat_wave-20260918-279` | 37.1 M km², fill 0.35 | 22.3 M km², fill 0.37 (1) | 6.8 M km², fill 0.68 (2) |
| `heat_wave-20261105-780` | 36.3 M km², fill 0.52 | 27.0 M km², fill 0.58 (1) | 19.5 M km², fill 0.61 (1) |

## 5. Recommendation

**Primary: `C3_valid_box_persist`** — the tracker stops owning the published
rectangle. Two coupled changes, one new knob (publish-time), no new tuning
threshold in the tracker:

1. **Tracking (three spots in `Tracker.step`).**
   * claim: `share = np.isin(labels, list(group_hits)) & ~taken & valid` — a
     footprint only ever contains cells that pass the daily rule *today*, and
     only those within `move_cells` of what the event already holds (the drift
     window is already the follow test, so this adds no new parameter);
   * seed: `footprint = region & valid` and `Box.from_mask(region & valid)`, so
     a candidate is born as the bbox of its *qualifying* cells;
   * merge: the older object keeps its own box and clips the younger one's
     footprint into it (`other.footprint &= in_box(ev.box)`) instead of
     `ev.box = ev.box.union(other.box, nx)`.
2. **Published box (`to_events`).** Replace the cumulative tracker box with the
   **persistence box**: the bbox of the cells that were valid on at least
   `max(2, ceil(0.5 × days))` of the event's days, trimmed to the area-weighted
   2–98 % quantiles of that mask, falling back to the bbox of every ever-valid
   cell when the persistence mask is empty.

Measured on heat waves (production code, corrected 2026-11-21 - see
"Implementation result"): fill median 0.52 → **0.76**, p10 0.30 → **0.51**, daily
fill median 0.24 → **0.43**, box median 3.80 → **1.14 M km²** (land 2.78 →
**0.87 M km²**), p90 16.6 → **4.70 M km²**, max 61.6 → **22.1 M km²**, box/peak
4.9 → **2.2**; the worst box is still cut ~3x while fills rise.
Freeze lane: fill 0.46 → **0.78**, daily fill 0.24 → **0.47**, box median 2.69 →
**0.98 M km²**, box max 24.6 → 11.8 M km². Event count barely moves (162 → 163
heat, 35 → 33 freeze), so this is not a strategy that hides boxes by deleting
events.

**Fallback if `C3` is judged too invasive: `S2c_persist_half_trim`** — publish
box only, tracker untouched: fill 0.52 → **0.69** (p10 0.41), daily fill 0.24 →
**0.38**, box median → **1.99 M km²** (land 1.48 M km²), p90 → 7.67 M km², max →
33.7 M km², box/peak → 2.5, freeze fill → 0.71, and **no change to which events
exist** (same 162 spells, same dates, same tracker boxes). It costs the tail
(`heat_wave-20261013-548` stays at 33.7 M km² with fill 0.36, because once the
tracker has merged a belt its valid cells really are that spread out) and it
leaves H2/H3 in the tracker, so the boxes stay about 2× the honest size.

**Adopt with either choice**: keep `daily_fill` in `Event.details` (already
computed in `to_events`, currently dropped for the non-anchored family) and
publish `fill` / `daily_fill` in the event payload so the dashboard can label
what it draws. Drop the `split_low_fill` idea — `S6` shows it does not help.

### Risks the owner has to weigh

| risk | measured size | mitigation |
|---|---|---|
| **Event identity churn** | `C3`: 48 of 162 heat spells have no matched successor and 47 new objects appear (freeze 12 lost / 10 new of 35) — which spells exist, their dates and their durations change. `S2c`: none, on either lane. | Keep the tracker's object identity separate from the published id (next row): under `C3` the site's list of events genuinely changes, so per-event curation has to be re-reviewed, and the changelog should say so. |
| **Published ids re-key** | Published ids are `make_event_id` = sha1(`event_type`, start date, bbox rounded to 0.01°) (`ewb_live/events/types.py:93-101`) — the box is in the hash, so **every** candidate re-keys the catalogue: measured on (start, bbox), 0 of 162 heat ids survive `C3` *and* 0 survive `S2c`; only a box-preserving tweak keeps them (`S3v` alone: 148/162). | This is not a reason to prefer the fallback — the fallback costs the same. Budget one re-baseline in the release: rebuild `events.json`, re-run the id-keyed score cache, and if the site's URLs or curation notes use the id, treat it as a catalogue version bump. |
| **A real belt becomes several events** | the 61.6 M km² August Eurasia spell comes back as 14 pieces under `C3` (4 under `S2c`), though the net count is flat because pieces also re-merge. | that is the intended semantics — one box per hot region, not one per continent — but per-event curation keyed by id has to be redone. |
| **Under-merging genuinely connected extremes** | `S3`-family drops 27–51 spell matches yet the median duration stays 4 days and the max 28–32 days, so events do not get systematically shorter. | the 3-day contiguous core (`AnchorSpec`) still gates validity, so each piece is a real event; `min_overlap` is untouched, so near-touching objects still merge. |
| **Scores move** | every scoring region shrinks (median land box 2.75 → **0.81 M km²** under `C3`, 1.48 M km² under `S2c`), so `hazard_score` changes for all 151 live events. | re-run `python -m ewb_live.events.scorer` in the same release; the new `fill` / `daily_fill` make it auditable. |
| **Published box steps between weekly runs** | the persistence mask grows with the spell, so an ongoing event's box can jump (it only ever tightens, never exceeds the tracker box). | not measured — needs the run-over-run view. If it is too jumpy, freeze the box at qualification (`qualified_date + days`) and recompute only on revision. |

### What to change in `docs/SPEC.md` §2

Step 4 currently says "the event box is the bbox of everything its footprint has
covered, so it grows with the event". Proposed replacement — the owner edits,
this is the wording the measurements support:

> 4. **Footprint (claim rule).** The footprint is a set of *valid* cells — cells
> that pass the daily rule that day — never closing-filled cells. A labelled
> region continues an event only for the part of it that is valid and within
> `move_cells` = 8 cells of the cells the event already holds; the rest of the
> region stays free and may seed its own candidate. A new candidate's first box
> is the bbox of the region's **valid** cells. When two objects reach the same
> cells the older keeps them **and its own box**: a merge clips the younger
> object into the older box and never unions two boxes (`max_box_area_km2`
> remains an optional cap, unused by default).
> 5. **Published box.** The dashboard and the scorer use the **persistence
> box**: the bbox of the cells valid on at least
> `publish_min_days` = max(2, ⌈0.5 × duration⌉) of the event's days, trimmed to
> the area-weighted 2–98 % latitude/longitude quantiles of that mask (fallback:
> the bbox of every valid cell the event held). The tracker's internal box keeps
> its current role — follow/claim/seeding geometry and the anchor's search area —
> and is reported in `details` as `tracker_box_area_km2` next to
> `published_box_area_km2` and `published_fill`.

### Open questions for the owner

* Is one box per *hot region* (C3) the object you want, or should the Eurasian
  belt stay one named event with a big box? If the latter: `S2c` and accept
  ~20 M km² boxes on belts, or draw the footprint on the map instead of a box.
* Freeze / cold-snap boxes span all longitudes today (`lon` median 35°, p90
  198°) because polar spells wrap; `C3` fixes most of it (freeze box median
  0.86 M km²). For a hard guarantee, publish through `Box.unwrapped()` and split
  wrapped boxes into hemispheric pieces.
* `daily_fill` (0.24 median) is the number a visitor actually perceives. Should
  the site publish it, and should events below some `daily_fill` be hidden?

## 6. Reproduce

```bash
# on the ewb LXC, scratch tree; production /opt/ewb-live is read-only
cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
  /opt/ewb-live/.venv/bin/python scripts/review/hb01_box_growth.py \
    --lane /opt/ewb-live/data/results/raw_exceedance \
    --published /opt/ewb-live/data/results/events.json \
    --start 2026-08-21 --end 2026-11-13 --selfcheck \
    --out /opt/ewb-heatbox/out/hb01
# --strategies S2c_persist_half_trim,C3_valid_box_persist to run a subset;
# --hazards freeze for the side column. ~45-65 s per strategy per hazard;
# --selfcheck asserts the tuned tracker reproduces the shipped one exactly.
```

Outputs on the LXC: `out/hb01_heat_wave.json` and `out/hb01_freeze.json`
(baseline, S1-S7, P0), `out/hb02_hw_heat_wave.json` (S3v + the C-combos, heat),
`out/hb02_fz_freeze.json` (the same on the freeze lane), `out/hb03_*`
(attribution-only reruns). Each JSON carries, per strategy, the `summary` row of
the §4 table, `worst3_paired` (the baseline's 3 worst boxes and their
successors), per-event rows (`fill`, `daily_fill`, `valid_ever_km2`, `hole_km2`,
`excess_out_km2`, `box_over_peak`, tracker-vs-published areas, `parent` id) and
`churn_vs_current_tracker`, plus the `attribution` block (the 12 largest growth
days with their category split, the per-event driver totals, and the seed-box
statistics) and `seam_audit`. Every number in this note comes from those files;
the §2 and §4 tables were generated from them, not hand-typed.

**Pointer for Phase 3.** Start from `C3_valid_box_persist`: the three
`Tracker.step` spots are already implemented behind `TunedTracker`
flags (`claim_overlap`, `seed_valid_only`, `merge_keeps_older_box`) and
`persistence_box()` is the reference publish rule (40 lines, wrap- and
area-weighted). `S2c_persist_half_trim` — `TunedTracker` with no flags plus the
same `persistence_box()` — is the zero-churn fallback and shares the publish
code, so implementing the primary keeps the fallback reachable behind one
feature flag.

---

## Implementation result (hb-implement, 2026-11-21; re-measured by hb-fix1 the
same day after the round-1 review found the measuring script diverging from
production - see the correction note under §4)

C3_valid_box_persist is implemented on `fix/heatwave-box-size` (tracker knobs
in `event_tracker.py`, publish + provenance in `temperature.py`, flood lane
pinned to the shipped rules, SPEC §2 and notes/footprint_tracker.md updated).
Re-measured on the LXC with the **production** code
(`scripts/review/hb01_box_growth.py --lane /opt/ewb-live/data/results/raw_exceedance
--published /opt/ewb-live/data/results/events.json --start 2026-08-21
--end 2026-11-13 --selfcheck --hazards heat_wave,freeze,cold_snap`; the
strategies drive `Tracker.step()` through `Params` knobs only). Correctness
gates, all PASS: `selfcheck` (legacy-pinned replay reproduces the shipped
tracker event-for-event: 162/162 heat, 35/35 freeze, 35/35 cold_snap) and
`prodcheck` (C3-tuned replay reproduces the production-C3 run event-for-event:
163/163, 33/33, 33/33) - the measurement is the product.

Before / after (85-day window 2026-08-21..11-13; cold_snap is re-scanned on
the freeze mask by the weekly lane, so its detection columns equal freeze -
the published catalogue's current cold_snap state is the P0 column):

| metric                | heat S0 | heat C3 | freeze S0 | freeze C3 | cold_snap P0 |
|-----------------------|---------|---------|-----------|-----------|--------------|
| events                | 162     | 163     | 35        | 33        | 24           |
| fill, median          | 0.52    | **0.76**| 0.46      | **0.78**  | 0.48         |
| fill, 10th pct        | 0.30    | 0.51    | 0.34      | 0.53      |              |
| fill, worst event     | 0.11    | 0.27    | 0.16      | 0.34      |              |
| daily fill, median    | 0.24    | 0.43    | 0.24      | 0.47      |              |
| box, median (km2)     | 3.80M   | **1.14M**| 2.69M    | **0.98M** | 2.49M        |
| box, land-only median | 2.78M   | 0.87M   | 1.97M     | 0.92M     | 1.97M        |
| box, p90              | 16.6M   | 4.7M    | 8.0M      | 3.0M      | 8.1M (max)   |
| box, max              | 61.6M   | 22.2M   | 24.6M     | 11.8M     | 8.1M         |
| box / biggest day, med| 4.9x    | 2.2x    | 4.3x      | 1.7x      | 3.7x         |
| duration med/max (d)  | 4/32    | 4/28    | 3/6       | 3/7       | 3/6          |

Targets met: fill median >= 0.75 (0.76 / 0.78) and box median <= 1.5M km2
(1.14M / 0.98M), without touching duration medians.

Worst-5 fate (date+location matcher: >=3 shared days and bbox overlap -
tracker ids are renumbered by any replay, so ids cannot be the pairing
key; `hb01` `summary.worst5_paired`): heat_wave-20260821-10, the 61.6M km2
pan-American box, is KEPT and shrinks to 9.1M km2 at fill 0.96; the two
October belt events (-20261013-548 45.5M -> 6.6M fill 0.80, -20261014-557
42.1M -> 11.7M fill 0.28) and the -20261105-780 / -20260918-279 belts are
KEPT and cut 3-6x; the biggest surviving C3 box is 22.2M km2 (fill 0.58).
Freeze: the two biggest boxes (24.6M and 14.4M km2) are KEPT at 2.1M km2
fill 0.80; the 8.4M / 7.9M / 8.1M ring boxes shrink or die - with the
whole-region claim gone their valid-only area never reaches the seed floor.
That is C3 working as designed: those events were boxes, not weather.
Catalogue churn vs the shipped tracker is heat 117 matched / 45 lost / 46
gained, freeze 22 / 13 / 11 (lost and gained are mostly the same regions
re-cut at persistence size; the lists with ids, boxes and bboxes are in the
`hb01` summary as `lost5` / `gained5`); no published (curated) event is
dropped and no new splits appear (published_dropped=0, split_extra=0 on all
three hazards). The 32-day monster ends 4 days earlier under C3 (core
collapse measured on valid cells), the honest direction.

Ongoing-event ids: hashing the moving persistence box re-keys ongoing events
every weekly run - measured on the LXC (`scripts/review/hb04_ongoing_ids.py`,
W1 2026-08-21..11-02, extend W2 +7d and slid-start W2s +7d - the two things a
real weekly run does; at the lane edge (11-06) no spell is open, so the
earlier committed window measured nothing). Of 5-6 ongoing heat spells per
base: the published-box hash keeps 3/5 ids on extend and re-keys the rest,
and is no better on the slid start; the shipped **anchor** basis (hash of the
frozen day-3 box) keeps 5/5 under C3 on both extend and slid-start - start
unchanged; `anchor_day=None` falls back to the old rule.
An ongoing event keeps its id, URL, and score-cache lineage across weekly
runs while its published box is redrawn - covered by
`test_scan_id_survives_the_published_box_moving`. select.py merged ids and the
other lanes are unchanged; note `subsume_freeze_overlaps` and curation
overlap decisions run on the new (smaller) boxes - pinned both ways by
`test_subsumption_on_the_smaller_c3_boxes` - so curation must be
re-reviewed in the same release (scores too - same release).

Tests: full `pytest -q` green on the fix branch (617 passed, 29 skipped; the
skips are environment-gated - AR-upstream needs the
`third_party/ExtremeWeatherBench` clone, e2e needs a live deploy; environment
provisioning only). Local lint (`uvx ruff check`): no new findings in any
changed file vs its branch baseline (the ruff 0.17 default ruleset fails the
pre-existing repo baseline ~900 findings; my files are flat or -1). Replay
runtimes: heat 70s, freeze 17s per strategy on the LXC.

## Round 2 (2026-11-21, hb-fix2): the core clock starts at qualification

Owner decision, taken after reviewing this branch's C3 result: the end check
counts the cells valid on *every day since the event began*, so a spell that
grows into its qualifying day has a core of only its seed area and is closed by
its own qualification. Re-measured, replayed and written up in
`notes/footprint_tracker.md` ("The 2026-11-21 core clock"): rule, three-stage
table (shipped `S0_current` → C3 with the core from the start → C5 = production
now), duration/gate and curation-merge exposure, and the ongoing-id table. The
numbers below are the ones that motivated the decision; all of them come from
`scripts/review/hb01_box_growth.py` and `hb05_qualify_fallbacks.py` on the LXC,
production `/opt/ewb-live` read-only, window 2026-08-21..11-13.

**What the bug costs the product.** 31 of the 162 heat/freeze events in the live
`events.json` have `details.qualified_date == end` (29 heat waves, 2 freezes;
the cold-snap lane carries 3 more). `qualification.py` scans frames from
`max(start, qualified_date)` to `end`, so for those 31 the window is empty and
`qualified_at` silently falls back to 00:00 UTC of the *start* day — the
published sub-daily qualification time is a guess for 19 % of the heat waves in
the catalogue. `hb05_qualify_fallbacks.py` on the live catalogue: 131 qualify,
**31 empty-window, 0 below-floor**, and every empty-window event is one of the
31. Re-run on the C5 replay of the same window: **206 qualify, 0 empty-window,
6 below-floor** — the failure mode is gone, replaced by six events (2.8 %) whose
*persistence* box is smaller than the footprint that qualified them, so no
single frame inside the published rectangle reaches the 200 k km² floor
(`max_frame_px` within 6 % of the floor in five of the six). That is a
publish-box question, not a tracking one, and it predates this change.

**The split is real.** Of the 31, 16 have a live event that starts exactly on
the closing day and overlaps the closed spell's box (15 heat waves, 1 freeze),
i.e. in half the cases the region really did seed a successor the same day. Under
C3 only 2 of those 16 pairs are covered by a single replay object; under the
qualification clock 11 of 16 are (10 heat, 1 freeze) - the other five are
genuinely separate systems, and the remaining 15 of the 31 events never had a
successor: they were simply cut short on their best day.

**Ongoing ids stay stable.** `scripts/review/hb04_ongoing_ids.py` (W1
2026-08-21..11-02, then the two things a weekly run does - extend +7 d at the
same start, and extend +7 d with the 90-day start slid +7 d), per ruleset
`shipped` / `c3_core_from_start` / `c3_qualify` (= production now):

| ruleset | ongoing at the cut | pairable in the next week | same id, anchor basis (shipped) | same id, published-box basis (old) |
| --- | --- | --- | --- | --- |
| shipped | 5 | 5 / 5 | 5, then 4 (slid start) | 3, 3 |
| C3, core from start | 6 | 5 / 6 | 5, 5 | 3, 3 |
| C3 + qualification clock | 7 | 6 / 7 | **6, 6** | 4, 4 |

Every ongoing spell that can be followed keeps its id under the shipped
anchor-basis hash on both weekly moves - which is the property the id was given
the anchor basis for - while the old published-box hash keeps 4 of 7. One of the
7 does not pair at all: with the anchor frozen at qualification it is a bigger
box, and over +7 d its far edge moved more than the 3-cell pairing tolerance in
`hb04` (the same single loss appears in the C3-from-start ruleset, so this is the
pairing rule's tolerance, not the clock's). Worth a look if the owner wants
ongoing continuity to be total; not retuned here.

**Freeze was being measured on the wrong population** (round-2 review item): the
replay scripts fed the lane's p15 bits to the tracker for both freeze and
cold_snap, so the freeze columns above were cold-snap numbers. `hb01` now applies
production's ceiling with production's rule and source (`t2m` daily max
`<= 0 degC`, `temperature._mirror_t2m_daily` over the ERA5 truth mirror, opened
read-only): the ceiling keeps 81 % of the p15 cells (801 638 → 647 912 cell-days)
and the shipped replay drops from 35 events to 22, against 11 published freezes
after curation. Every freeze number in the round-1 write-up (fill 0.46 → 0.78,
box median 2.69 → 0.98 M km²) is a cold-snap number; the true-freeze numbers are
in the `footprint_tracker.md` table (fill 0.42 → 0.83, box median 2.78 → 0.58 M
km², max 24.6 → 4.03 M km²) and they are better, not worse.

**Nothing breaks a gate.** No event in any stage exceeds `validate.MAX_DURATION_DAYS`
(45 d); the longest heat spell is 31 d under C5 against 32 d as shipped, and
freeze/cold_snap top out at 7 d. Curation's merge exposure barely moves: 20
mergeable heat pairs under C5 against 18 under C3 and 46 under the shipped rule,
largest merged window 42 d, 3 pairs over the 16-day `select.MERGE_CAP_DAYS`
envelope (3 under C3 as well), none over the 45-day hard cap. Freeze and
cold_snap: 1 mergeable pair each.

**The cost is membership churn, not workload.** The raw replay goes 162 → 163 →
**189** heat events: spells that used to be ended at their qualifying day either
survive past `min_days` or no longer re-seed a successor, and the churn against
the shipped rule is 48 lost / 75 gained (C3 was 45 / 46). What the dashboard and
the scorecard see is capped by curation - `select.rank_and_cap` keeps 15 heat
waves per window (`DEFAULT_TOP_N`; the live selection is 15 heat / 5 freeze /
8 cold snaps) - so weekly scoring volume does not change; which events occupy
those slots does, and the release re-hashes ids once on top of the C3 re-hash.
Boxes and fills all improve at the same time (fill median 0.83, box median 1.05
M km², box p90 3.11 M km²).
