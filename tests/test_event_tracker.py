"""Unit tests for the ported footprint/core tracker (no network).

Ported from aaTman/ewb-events' own suite (tests/test_{geometry,tracker}.py)
and adapted to the ported API (daily dates instead of Timestamps,
``track_events(mask, dates, ...)`` instead of an xarray wrapper). A grid where
every cell is 10,000 km2: 5 cells = 50k km2 (seed), 20 cells = 200k km2
(qualify). Most tracker cases set the core end threshold to 10k km2 (one cell)
so other behaviour stays visible.
"""
import dataclasses
import datetime as dt

import numpy as np
import pytest

from ewb_live.events.event_tracker import (
    Box,
    Params,
    TrackedEvent,
    Tracker,
    circular_span,
    dilate,
    label_regions,
    persistence_box,
    persistence_k_days,
    track_events,
)

NY, NX = 40, 80
AREA = np.full((NY, NX), 1e4)
PARAMS = Params(merge_survivor="separate", region_radius=1, region_min_fraction=0.3, move_cells=2,
                max_ramp_days=4, end_area_km2=1e4)
# The shipped tracker claims whole labelled regions, seeds candidates from
# them, and unions the boxes of merged objects. The 2026-11-20 box-size rule
# (Params.claim_valid_only / seed_valid_only / merge_keep_box, SPEC §2) is the
# default; these tests predate it and assert its geometry, so they run with
# the legacy knobs on. The C3 rule has its own tests further down.
# core_clock="start" (owner 2026-11-21): the shipped core counted every-day
# valid cells from the candidate's start day; these cases pin that too.
LEGACY = dataclasses.replace(PARAMS, claim_valid_only=False,
                             seed_valid_only=False, merge_keep_box=False,
                             core_clock="start")
DAY0 = dt.date(2026, 9, 1)


def block(rows: slice, cols: slice) -> np.ndarray:
    out = np.zeros((NY, NX), bool)
    out[rows, cols] = True
    return out


def cells(n: int, row: int = 10, col: int = 10) -> np.ndarray:
    """A compact patch of exactly ``n`` cells, row by row, five cells wide."""
    out = np.zeros((NY, NX), bool)
    for k in range(n):
        out[row + k // 5, col + k % 5] = True
    return out


def run(frames: list[np.ndarray], params: Params = PARAMS) -> list:
    dates = [DAY0 + dt.timedelta(days=k) for k in range(len(frames))]
    return track_events(np.array(frames), dates, AREA, params, "heat_wave")


def drifting(days: int) -> list[np.ndarray]:
    """An 5-row block drifting 2 columns a day, then nothing."""
    return [block(slice(10, 15), slice(10 + 2 * t, 18 + 2 * t))
            for t in range(days)] + [cells(0)]


# --------------------------------------------------------------- geometry ---

def test_box_from_mask_is_tight():
    mask = np.zeros((10, 20), bool)
    mask[2:5, 3:8] = True
    box = Box.from_mask(mask)
    assert box == Box(2, 4, 3, 7)
    np.testing.assert_array_equal(box.mask(10, 20), mask)


def test_box_crosses_the_dateline():
    mask = np.zeros((4, 20), bool)
    mask[1, [0, 1, 19]] = True
    box = Box.from_mask(mask)
    assert box == Box(1, 1, 19, 1)
    assert box.mask(4, 20)[1].nonzero()[0].tolist() == [0, 1, 19]


def test_box_union_covers_both():
    assert Box(0, 1, 2, 3).union(Box(4, 5, 8, 9), nx=20) == Box(0, 5, 2, 9)


def test_circular_span_full_width():
    assert circular_span(np.ones(12, bool)) == (0, 11)


def test_dilate_wraps_in_longitude():
    mask = np.zeros((5, 10), bool)
    mask[2, 0] = True
    out = dilate(mask, 1)
    assert bool(out[2, 9]) and bool(out[1, 1]) and not bool(out[2, 2])


def test_label_regions_bridges_nearby_patches():
    valid = np.zeros((20, 40), bool)
    valid[5:8, 5:8] = True
    valid[5:8, 10:13] = True      # two-column gap
    valid[5:8, 30:33] = True      # far away
    labels = label_regions(valid, radius=2, min_fraction=0.2)
    assert labels[6, 6] == labels[6, 11] != 0
    assert labels[6, 31] not in (0, labels[6, 6])


def test_label_regions_does_not_bridge_with_zero_radius():
    valid = np.zeros((20, 40), bool)
    valid[5:8, 5:8] = True
    valid[5:8, 10:13] = True
    labels = label_regions(valid, radius=0, min_fraction=1.0)
    assert labels[6, 6] != labels[6, 11]


def test_label_regions_merges_across_the_dateline():
    valid = np.zeros((10, 40), bool)
    valid[3:6, 0:3] = True
    valid[3:6, 37:40] = True
    labels = label_regions(valid, radius=0, min_fraction=1.0)
    assert labels[4, 1] == labels[4, 38] != 0


def test_label_regions_only_marks_cells_near_valid_points():
    valid = np.zeros((20, 40), bool)
    valid[5:8, 5:8] = True
    labels = label_regions(valid, radius=4, min_fraction=0.05)
    assert labels[6, 6] != 0
    assert labels[6, 10] == 0


# ---------------------------------------------------------------- tracker ---

def test_event_that_starts_small_keeps_its_early_start():
    evs = run([cells(6), cells(12), cells(25), cells(15), cells(5), cells(0)], LEGACY)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.start == DAY0
    assert ev.qualified == DAY0 + dt.timedelta(days=2)
    # the last 5 cells were valid from day one, so the core holds to day 5
    assert ev.end == DAY0 + dt.timedelta(days=5)
    assert ev.duration_days == 5
    assert ev.peak_area_km2 == 25e4


def test_event_shorter_than_three_days_is_not_valid():
    assert run([cells(25), cells(25), cells(0)]) == []


def test_candidate_that_never_reaches_200k_is_not_an_event():
    assert run([cells(6), cells(12), cells(19), cells(12), cells(6), cells(0)]) == []


def test_candidate_must_qualify_within_ramp_window():
    evs = run([cells(6)] * 5 + [cells(25)] * 3 + [cells(0)])
    assert len(evs) == 1
    assert evs[0].start > DAY0


def test_patchy_points_are_grouped_into_one_event():
    patchy = np.zeros((NY, NX), bool)
    patchy[10:22:2, 10:22:2] = True      # 36 lone cells, none touching
    assert patchy.sum() * 1e4 >= 2e5
    evs = run([patchy] * 4 + [cells(0)])
    assert len(evs) == 1
    assert evs[0].peak_area_km2 == 36e4


def test_moving_event_box_covers_its_whole_path():
    evs = run(drifting(4))
    assert len(evs) == 1
    assert evs[0].box == Box(10, 14, 10, 23)


def test_box_is_fixed_once_the_event_ends():
    ev = run(drifting(4))[0]
    assert ev.end == DAY0 + dt.timedelta(days=4)
    assert len(ev.box_area_km2) == ev.duration_days


def test_new_patch_inside_an_active_box_does_not_start_a_second_event():
    frames = []
    for t in range(6):
        frame = block(slice(10, 15), slice(10 + 2 * t, 18 + 2 * t))
        if t >= 3:
            frame |= block(slice(10, 13), slice(10, 12))  # back where it began
        frames.append(frame)
    frames.append(cells(0))
    assert len(run(frames)) == 1


def test_two_candidates_that_grow_together_merge():
    frames = [
        block(slice(10, 12), slice(10, 13)) | block(slice(10, 12), slice(30, 33)),
        block(slice(10, 13), slice(10, 18)) | block(slice(10, 13), slice(25, 33)),
        block(slice(10, 14), slice(10, 33)),
        block(slice(10, 14), slice(10, 33)),
        cells(0),
    ]
    evs = run(frames)
    assert len(evs) == 1
    assert evs[0].start == DAY0


def test_persistent_points_in_the_box_keep_the_event_alive():
    here = block(slice(10, 15), slice(10, 15))
    lingering = block(slice(10, 12), slice(10, 12))   # 4 cells every day = 40k core
    fresh = block(slice(10, 15), slice(15, 18))       # new cells: join, not core yet
    ev = run([here] * 4 + [lingering | fresh, cells(0)], LEGACY)
    assert len(ev) == 1
    assert ev[0].core_area_km2[-1] == 4e4
    assert ev[0].box_area_km2[-1] == 19e4
    assert ev[0].end == DAY0 + dt.timedelta(days=5)


def test_box_stops_growing_at_the_size_cap():
    frames = [block(slice(10, 15), slice(10 + 2 * t, 15 + 2 * t))
              for t in range(5)] + [cells(0)]
    ev = run(frames, Params(end_area_km2=1e4, region_radius=1, move_cells=2,
                            max_box_area_km2=35e4))[0]
    assert ev.box == Box(10, 14, 10, 16)
    # day 3's footprint is clipped to the box it could keep
    assert ev.footprint_area_km2 == [25e4, 25e4, 15e4]


def test_events_do_not_merge_past_the_size_cap():
    left, right = block(slice(10, 15), slice(10, 15)), block(slice(10, 15), slice(30, 35))
    bridge = block(slice(12, 13), slice(15, 30))
    frames = [left | right, left | right | bridge, left | right | bridge, cells(0)]
    evs = run(frames, dataclasses.replace(LEGACY, max_box_area_km2=60e4))
    assert [ev.box for ev in evs] == [Box(10, 14, 10, 14), Box(10, 14, 30, 34)]
    assert [ev.peak_area_km2 for ev in evs] == [25e4, 25e4]


def test_capped_event_leaves_the_rest_of_a_shared_region_to_its_neighbour():
    a = block(slice(10, 15), slice(10, 15))
    b_seed = block(slice(10, 12), slice(22, 25))
    region = block(slice(10, 15), slice(10, 26))   # touches both; A+B would pass the cap
    frames = [a, a | b_seed, region, region, region, cells(0)]
    evs = run(frames, dataclasses.replace(LEGACY, max_box_area_km2=60e4))
    assert len(evs) == 2
    a_ev, b_ev = evs
    assert a_ev.box == Box(10, 14, 10, 14)
    assert a_ev.footprint_area_km2[2] == 25e4
    assert b_ev.qualified == DAY0 + dt.timedelta(days=2)
    assert b_ev.box == Box(10, 14, 15, 25)
    assert b_ev.footprint_area_km2[1] == 55e4


def test_event_that_ends_today_does_not_block_a_neighbour():
    a = block(slice(10, 15), slice(10, 15))
    b_seed = block(slice(10, 12), slice(22, 25))
    region = block(slice(10, 14), slice(16, 28))   # touches A's grown footprint
    frames = [a, a, a | b_seed, region, region, cells(0)]
    evs = run(frames, dataclasses.replace(LEGACY, max_box_area_km2=60e4))
    assert [ev.start for ev in evs] == [DAY0, DAY0 + dt.timedelta(days=2)]
    assert evs[0].end == DAY0 + dt.timedelta(days=3)
    assert evs[1].qualified == DAY0 + dt.timedelta(days=3)
    assert evs[1].box == Box(10, 13, 16, 27)


def test_region_bigger_than_the_size_cap_cannot_seed():
    frames = [block(slice(10, 15), slice(10, 15))] * 4 + [cells(0)]
    assert run(frames, Params(end_area_km2=1e4, region_radius=1, move_cells=2,
                              max_box_area_km2=20e4)) == []


def test_neighbour_that_only_brushes_the_footprint_is_not_swallowed():
    a = block(slice(10, 15), slice(10, 15))
    neighbour = block(slice(10, 21), slice(18, 32))   # 3-column gap
    frames = [a, a | neighbour, a | neighbour, a | neighbour, cells(0)]
    evs = run(frames, Params(end_area_km2=1e4, region_radius=1, move_cells=4))
    assert [ev.box for ev in evs] == [Box(10, 14, 10, 14), Box(10, 20, 18, 31)]


def test_drifting_event_ends_once_no_cell_has_stayed_valid_since_day_one():
    ev = run(drifting(10))[0]
    assert ev.anchor == Box(10, 14, 10, 21)              # the box after day 3
    assert ev.end == DAY0 + dt.timedelta(days=4)         # it left its day-1 ground
    assert ev.box == Box(10, 14, 10, 23)                 # reported box keeps the path


def test_core_is_counted_only_inside_the_anchor_box():
    a = block(slice(10, 15), slice(10, 15))
    hot_spot = block(slice(10, 12), slice(22, 24))       # 40k every day, too small to seed
    reach = block(slice(10, 15), slice(15, 22))          # day 5: the event reaches it
    frames = [a | hot_spot] * 4 + [a | reach | hot_spot] + [hot_spot] * 2 + [cells(0)]
    anchored = run(frames, LEGACY)[0]
    assert anchored.anchor == Box(10, 14, 10, 14)
    assert anchored.end == DAY0 + dt.timedelta(days=5)   # nothing left in the anchor box
    whole_box = run(frames, dataclasses.replace(LEGACY, anchor_day=None))[0]
    assert whole_box.anchor is None
    assert whole_box.end == DAY0 + dt.timedelta(days=7)  # the hot spot keeps it alive


def test_defaults_match_the_published_methodology():
    p = Params()
    assert p.max_box_area_km2 is None
    assert p.end_area_km2 == 1e5
    assert p.qualify_area_km2 == 2e5
    assert p.seed_area_km2 == 5e4
    assert (p.min_days, p.max_ramp_days, p.anchor_day) == (3, 5, 3)
    # 2026-11-20 box-size rule (SPEC §2): valid-only claim/seed, merges keep
    # the older box, published box = persistence box (2-98 % trim, half-days k)
    assert (p.claim_valid_only, p.seed_valid_only, p.merge_keep_box) == (True, True, True)
    assert (p.publish_box, p.publish_min_fraction, p.publish_trim) == ("persistence", 0.5, 0.02)
    # owner 2026-11-21: the core clock (and the anchor freeze) start at
    # qualification; "start" is the pinned pre-change rule
    assert p.core_clock == "qualified"
    assert LEGACY.core_clock == "start"


def test_event_ends_when_its_persistent_core_drops_under_the_floor():
    here = block(slice(10, 15), slice(10, 15))
    moved = block(slice(10, 15), slice(15, 20))   # touches the old patch, all-new cells
    tracker = Tracker(AREA, PARAMS, hazard="heat_wave")
    days = [DAY0 + dt.timedelta(days=k) for k in range(5)]
    for day in days[:4]:
        assert tracker.step(day, here) == []
    ended = tracker.step(days[4], moved)
    assert [ev.end for ev in ended] == [days[4]]
    assert ended[0].core_area_km2 == [25e4, 25e4, 25e4, 25e4]


def test_core_needs_cells_valid_every_day_since_the_event_began():
    a = block(slice(10, 15), slice(10, 15))
    left = block(slice(10, 15), slice(10, 11))
    evs = run([a, a & ~left, a, a, left, cells(0)])   # left misses day 2
    assert evs[0].end == DAY0 + dt.timedelta(days=4)  # its run restarted: not core
    assert evs[0].core_area_km2 == [25e4, 20e4, 20e4, 20e4]


# ------------------------------------------- core clock from qualification (2026-11-21) ---
# Owner decision 2026-11-21 (SPEC §2 step 4): the core counts cells valid every
# day since the event QUALIFIED, and the anchor freezes no earlier than that
# day. Under the shipped rule (``core_clock="start"``) a spell that grew into
# its qualifying day was closed in the same pass - the core still held only the
# small seed area - and its region was free to re-seed the very next day, which
# is how 29 heat + 2 freeze events in the live catalogue got
# ``qualified_date == end``. Every case below runs both clocks so the diff is
# visible, and every pinned number comes from a measurement in the worktree.

QUALIFY_END = dataclasses.replace(PARAMS, end_area_km2=Params().end_area_km2)


def test_growing_spell_survives_its_own_qualifying_day():
    """60k -> 120k -> 250k held: ONE event, early start kept (fails on "start")."""
    frames = [cells(6), cells(12), cells(25), cells(25), cells(25), cells(0)]
    evs = run(frames, QUALIFY_END)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.start == DAY0                              # the ramp day is kept
    assert ev.qualified == DAY0 + dt.timedelta(days=2)
    assert ev.end == DAY0 + dt.timedelta(days=5)
    assert (ev.qualified - ev.start).days < ev.duration_days
    # the qualifying heat is the core: 25 cells valid since the qualifying day
    assert ev.core_area_km2 == [6e4, 6e4, 25e4, 25e4, 25e4]
    # the old rule closed the candidate on the day it qualified (its core was
    # still the 60k seed) and the same spell re-seeded as a new event
    old = run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))
    assert len(old) == 1
    assert old[0].start == DAY0 + dt.timedelta(days=2)   # the early days were lost
    assert old[0].core_area_km2 == [25e4, 25e4, 25e4]


def test_qualifying_at_seed_is_the_same_under_both_clocks():
    """A region that qualifies on its seed day: start == qualified, no diff."""
    frames = [cells(25), cells(25), cells(25), cells(25), cells(0)]
    new = run(frames, QUALIFY_END)[0]
    old = run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))[0]
    assert (new.start, new.qualified, new.end, new.anchor) == \
           (old.start, old.qualified, old.end, old.anchor)
    assert (new.start, new.qualified, new.end) == (DAY0, DAY0, DAY0 + dt.timedelta(days=4))
    assert new.core_area_km2 == old.core_area_km2


def test_qualify_clock_still_ends_a_draining_spell():
    """The new clock must not make events immortal: cells still drain."""
    frames = [cells(25), cells(25), cells(25), cells(5), cells(5), cells(0)]
    ev = run(frames, QUALIFY_END)[0]
    assert ev.qualified == DAY0
    assert ev.end == DAY0 + dt.timedelta(days=3)     # 50k core < the 100k floor
    assert ev.core_area_km2 == [25e4, 25e4, 25e4]
    assert ev == run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))[0]


def test_qualify_clock_still_ends_a_drifting_belt():
    """Permanent-belt style: 1.2M km2 of heat that never sits still still dies.

    The belt holds its footprint by drifting 2 cells a day, so no cell stays
    valid long enough inside the frozen anchor box: the core falls 1.2M -> 120k
    and the event ends on its 10th day exactly as under the start clock. The
    qualify clock only stops a *growing* spell from being cut off at its own
    qualifying day; a belt that re-claims fresh ground is still a season, not
    an event.
    """
    frames = [block(slice(10, 16), slice(10 + 2 * t, 30 + 2 * t)) for t in range(14)]
    frames.append(cells(0))
    new = run(frames, QUALIFY_END)
    old = run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))
    assert [(ev.start, ev.end) for ev in new] == [(ev.start, ev.end) for ev in old]
    assert [ev.duration_days for ev in new] == [10, 4]
    assert new[0].core_area_km2[:4] == [12e5, 10.8e5, 9.6e5, 8.4e5]
    assert new[0].core_area_km2[-1] == 1.2e5          # the day before it fell under


def test_anchor_freezes_no_earlier_than_qualification():
    """A spell that qualifies after day 3 anchors on its qualifying day."""
    frames = [cells(6), cells(12), cells(19), cells(25), cells(25), cells(25), cells(0)]
    ev = run(frames, QUALIFY_END)[0]
    assert ev.qualified == DAY0 + dt.timedelta(days=3)
    # the box the event had on its qualifying day (rows 10..14 - the ramp's
    # 4-row day-3 box under the start clock froze a row too early)
    assert ev.anchor == Box(10, 14, 10, 14)
    old = run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))[0]
    assert old.anchor == Box(10, 13, 10, 14)          # frozen on event day 3


def test_merge_survivor_is_the_oldest_qualified_member():
    """A merge cannot hand qualification to an unqualified candidate.

    Round-3 blocker (real-data repro: a 48k km2 candidate absorbed a 363k km2
    qualified event, clipped its cells into the small box and published a
    "valid" event with a 51k km2 peak, while the real spell re-seeded a day
    late). The survivor is the group's OLDEST QUALIFIED member, an event's
    ``qualified`` is a day its OWN footprint reached the floor, and an
    absorbed candidate simply disappears. Here the 60k candidate A welds to
    the qualified 250k event B on day 3: the merged object is B - B's own
    seed day, B's own core clock, and B's core survives the merge, not A's
    clipped husk.
    """
    a = block(slice(10, 12), slice(10, 13))            # 60k candidate, day 0
    b = block(slice(10, 15), slice(40, 45))            # 250k: qualifies at its seed day
    region = block(slice(10, 15), slice(10, 45))       # one label welds them on day 3
    frames = [a, a | b, a | b, region, region, cells(0)]
    evs = run(frames, QUALIFY_END)
    assert len(evs) == 1                               # A left no event
    ev = evs[0]
    assert ev.id.endswith("-2")                        # it is B, seeded day 1
    assert ev.start == DAY0 + dt.timedelta(days=1)     # B's own seed, not A's
    assert ev.qualified == ev.start                    # its own qualifying day
    assert ev.core_clock_start == ev.qualified         # no merge-day clock
    assert (ev.footprint_area_km2[(ev.qualified - ev.start).days]
            >= QUALIFY_END.qualify_area_km2)           # floor reached on its own day
    assert ev.peak_area_km2 >= QUALIFY_END.qualify_area_km2   # never clipped sub-floor
    assert ev.anchor == Box(10, 14, 38, 44)            # day-3 anchor: B's grown box
    assert ev.end == DAY0 + dt.timedelta(days=5)
    # the pinned start clock picks the same survivor - the fix is the
    # survivor rule, not the clock
    old = run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))[0]
    assert (old.start, old.qualified) == (ev.start, ev.qualified)


def test_seam_ramp_keeps_its_early_start():
    """The same growing spell across the antimeridian."""
    def seam(cols: int) -> np.ndarray:
        m = np.zeros((NY, NX), bool)
        m[10:15, [(78 + k) % NX for k in range(cols)]] = True
        return m
    frames = [seam(1), seam(2), seam(5), seam(5), seam(5), cells(0)]
    ev = run(frames, QUALIFY_END)[0]
    assert (ev.start, ev.qualified, ev.end) == (
        DAY0, DAY0 + dt.timedelta(days=2), DAY0 + dt.timedelta(days=5))
    assert ev.box.j0 > ev.box.j1                     # still the short wrapped arc
    assert ev.anchor == Box(10, 14, 78, 1)
    assert run(frames, dataclasses.replace(QUALIFY_END, core_clock="start"))[0].start \
        == DAY0 + dt.timedelta(days=2)


def test_ending_uses_the_same_core_clock_as_step():
    """step and ending() must agree, or a live event is killed by a stale core."""
    day = DAY0 + dt.timedelta(days=4)
    valid = block(slice(10, 15), slice(10, 20))       # 50 cells = 500k km2
    streak = np.zeros((NY, NX), int)
    streak[10:15, 10:20] = 2                         # valid the last two days only
    ev = TrackedEvent("heat_wave-x", "heat_wave", DAY0, Box(10, 14, 10, 19),
                      qualified=day - dt.timedelta(days=1),
                      core_clock_start=day - dt.timedelta(days=1),
                      anchor=Box(10, 14, 10, 19), footprint=valid)
    labels = np.zeros((NY, NX), int)
    weighted = valid * AREA

    def tracker_with(params: Params) -> Tracker:
        tr = Tracker(AREA, params, "heat_wave")
        tr.streak = streak.copy()
        return tr

    qualified = tracker_with(QUALIFY_END)
    assert not qualified.ending(ev, set(), labels, valid, weighted, day)
    # the start clock counts from day 0: no cell was valid that long, core 0
    start = tracker_with(dataclasses.replace(QUALIFY_END, core_clock="start"))
    assert start.ending(ev, set(), labels, valid, weighted, day)
    assert (qualified.core_area(ev, ev.anchor, day, weighted),
            start.core_area(ev, ev.anchor, day, weighted)) == (5e5, 0.0)


def test_no_event_ever_ends_on_its_qualifying_day():
    """Invariant: start <= qualified < end, and the qualifying day is recorded."""
    a = block(slice(10, 15), slice(10, 15))
    scenarios = [
        [cells(6), cells(12), cells(25), cells(25), cells(25), cells(0)],
        [cells(25)] * 4 + [cells(0)],
        [cells(25), cells(25), cells(25), cells(5), cells(5), cells(0)],
        drifting(6),
        [a] * 3 + [block(slice(10, 15), slice(15, 20))] * 3 + [cells(0)],
        [block(slice(10, 16), slice(10 + 2 * t, 30 + 2 * t)) for t in range(8)] + [cells(0)],
    ]
    for frames in scenarios:
        for ev in run(frames, QUALIFY_END):
            assert ev.qualified is not None
            assert ev.start <= ev.qualified
            assert ev.end is None or ev.qualified < ev.end
            days = (ev.duration_days if ev.end is None
                    else (ev.end - ev.start).days)
            assert (ev.qualified - ev.start).days < days, ev.id
            assert (ev.qualified - ev.start).days < len(ev.footprint_area_km2)


def test_young_event_core_needs_only_its_age_in_days():
    evs = run([cells(25), cells(25), cells(25), cells(0)])
    assert len(evs) == 1
    assert evs[0].duration_days == 3


def test_tracker_reports_candidates_each_day():
    tracker = Tracker(AREA, LEGACY, hazard="freeze")
    tracker.step(DAY0, cells(6))
    assert [ev.status for ev in tracker.active] == ["candidate"]
    tracker.step(DAY0 + dt.timedelta(days=1), cells(25))
    assert [ev.status for ev in tracker.active] == ["active"]
    ended = tracker.step(DAY0 + dt.timedelta(days=2), cells(0))
    assert [ev.status for ev in ended] == ["ended"]
    assert tracker.active == []


def test_default_end_threshold_ends_an_event_with_a_small_core():
    frames = [cells(25), cells(25), cells(25), cells(5), cells(5), cells(0)]
    default = run(frames, dataclasses.replace(PARAMS, end_area_km2=Params().end_area_km2))[0]
    assert default.end == DAY0 + dt.timedelta(days=3)
    assert run(frames)[0].end == DAY0 + dt.timedelta(days=5)


def test_events_are_returned_in_start_order():
    early = block(slice(5, 10), slice(3, 8))      # days 0-4
    late = block(slice(25, 30), slice(40, 45))    # days 5-9
    frames = [early] * 5 + [late] * 5
    evs = run(frames)
    assert [ev.start for ev in evs] == sorted(ev.start for ev in evs)
    assert [ev.start for ev in evs] == [DAY0, DAY0 + dt.timedelta(days=5)]


def test_track_events_rejects_mismatched_shapes():
    with pytest.raises(ValueError):
        track_events(np.zeros((2, NY, NX), bool), [DAY0], AREA, PARAMS, "heat_wave")


# ------------------------------------------------- box-size rule (C3, 2026-11-20) ---
# One test per growth cause measured in notes/heatwave_box_diagnosis.md (H2, H3,
# H4, H5 + seeding). Each asserts the C3 default AND the legacy value, so the
# same scenario shows what the legacy rule did (and fails on origin/develop).

def test_claim_takes_only_valid_cells_inside_the_drift_window():
    """H3: a small footprint must not swallow a whole labelled region (belt).

    Pinned with ``merge_survivor="oldest_qualified"`` (the C3 merge rule this
    mechanism test was written for): the survivor absorbs the region's other
    member with its claim clipped into the survivor's own box. The shipped
    default (``separate``) instead lets the belt seed its own event; its
    guard - the small survivor also never swallows the belt - is pinned in
    :func:`test_separate_rule_splits_the_belt_instead_of_clipping_it`.
    """
    c3 = rule(survivor="oldest_qualified")
    event = block(slice(13, 18), slice(8, 13))                  # 25 cells, qualifies
    belt = event | block(slice(10, 21), slice(12, 61))          # one giant region
    frames = [event, belt, belt, belt, cells(0)]
    evs = run(frames, c3)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.duration_days == 4
    # C3: the box only ever grows over valid cells within move_cells=2/day of
    # what the event holds - it never teleports to the belt's far edge; the
    # survivor does creep east at its own 2-cell/day drift reach, which is the
    # claim rule working, not the region being swallowed
    assert ev.box == Box(10, 20, 8, 18)
    # legacy: the day-1 claim takes the ENTIRE region -> a 5.8 M km2 box
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(10, 20, 8, 60)


def test_drift_window_clips_a_touching_blob_to_its_near_edge():
    """H5: cells of a region past the move_cells window are not claimed.

    Pinned with ``merge_survivor="oldest_qualified"`` (see
    :func:`test_claim_takes_only_valid_cells_inside_the_drift_window`).
    """
    c3 = rule(survivor="oldest_qualified")
    here = block(slice(10, 15), slice(10, 15))                  # 25 cells
    blob = here | block(slice(8, 17), slice(16, 41))            # touches at col 16
    frames = [here, blob, blob, blob, cells(0)]
    ev = run(frames, c3)[0]
    # the blob past the drift window never enters the box at once; the
    # survivor grows only at its own claim reach (2 cells/day), so by the
    # third day the box covers columns up to 16 + 2 x 2, never the blob's
    # far edge at 40
    assert ev.box == Box(8, 16, 10, 20)
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(8, 16, 10, 40)                     # blob taken whole


def test_separate_rule_splits_the_belt_instead_of_clipping_it():
    """The shipped default (``separate``): a qualifying region next to a
    qualified event seeds its OWN event; the small survivor's box stays
    strictly inside its own drift reach - less box, not more, than the clip
    rule (j1=16 vs 20 in the H5 scenario).
    """
    here = block(slice(10, 15), slice(10, 15))
    blob = here | block(slice(8, 17), slice(16, 41))
    frames = [here, blob, blob, blob, cells(0)]
    evs = run(frames)                                           # default PARAMS
    assert len(evs) == 2
    small, big = evs                                            # by start
    assert small.box == Box(8, 16, 10, 16)                      # never past col 16
    assert big.box == Box(8, 16, 14, 40)                        # the blob, own event
    assert big.peak_area_km2 >= 2e5                             # a real 200k+ spell


def test_merge_keeps_the_older_box():
    """H2: a merge must not paint the partner's whole box onto the survivor.

    Pinned with ``merge_survivor="oldest_qualified"`` (the clip mechanism
    under test; the shipped default absorbs unqualified candidates the same
    way and grows no further box - asserted below for both rules).
    """
    a = block(slice(10, 15), slice(5, 11))                      # 30 cells, qualifies
    b = block(slice(10, 13), slice(25, 29))                     # 90k km2 candidate
    region = block(slice(10, 15), slice(5, 31))                 # one label over both
    frames = [a | b, region, region, cells(0)]
    for p, j1 in ((rule(survivor="oldest_qualified"), 14), (PARAMS, 12)):
        evs = run(frames, p)
        assert len(evs) == 1                                    # still one event
        # the older object keeps its own box - B's block (and the daily re-seeded
        # candidate from the region's body) never stretches it to 30; the survivor
        # only creeps over the bridge at its own 2-cell/day claim reach
        assert evs[0].box == Box(10, 14, 5, j1)
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(10, 14, 5, 30)   # the partner's block unioned in


def test_footprint_and_seed_box_hold_only_valid_cells():
    """H4/seeding: closing-filled bridge cells never enter a footprint."""
    p3 = dataclasses.replace(PARAMS, region_radius=2, region_min_fraction=0.2)
    p_old = dataclasses.replace(p3, claim_valid_only=False,
                                seed_valid_only=False, merge_keep_box=False)
    blob = block(slice(5, 8), slice(5, 9)) | block(slice(5, 8), slice(11, 15))
    frames = [blob, blob, blob, cells(0)]
    for p, expect_filled in ((p3, False), (p_old, True)):
        tracker = Tracker(AREA, p, "heat_wave")
        held_filled = False
        for k, frame in enumerate(frames):
            tracker.step(DAY0 + dt.timedelta(days=k), frame)
            held_filled |= any((ev.footprint & ~frame).any()
                               for ev in tracker.active)
        assert held_filled == expect_filled   # legacy walked on bridge cells
    # one event either way; the valid footprint (24 cells = 240k km2) is the same
    evs = run(frames, p3)
    assert [ev.footprint_area_km2 for ev in evs] == [[24e4, 24e4, 24e4]]


def test_drifting_event_keeps_its_own_cells_when_a_merge_is_clipped():
    """Review #2: the survivor of a merge must not lose its own drift cells.

    A 30-cell block drifts east 2 cols/day and runs into a static blob; on the
    merge day the whole blob joins one labelled region. Legacy claims the whole
    region so A simply absorbs it; C3 keeps the older box, but must clip only
    the YOUNGER member's claim - clipping A's own drifted cells (the bug) shrank
    its footprint 30 -> 20 cells, re-seeded the lost columns as a rival object
    and split the spell in two (A ending 09-04, a twin running 09-03..09-06).
    With the fix C3 returns one event, exactly like legacy.
    """
    frames = []
    for t in range(6):
        frame = block(slice(10, 15), slice(10 + 2 * t, 16 + 2 * t))
        frame |= block(slice(10, 15), slice(19, 24))   # the static blob, 09-01..
        frames.append(frame)
    frames.append(cells(0))
    evs = run(frames, rule(survivor="oldest_qualified"))
    assert [(ev.start, ev.end) for ev in evs] == [(DAY0, DAY0 + dt.timedelta(days=6))]
    ev_old = run(frames, LEGACY)[0]
    assert (ev_old.start, ev_old.end) == (DAY0, DAY0 + dt.timedelta(days=6))
    # day 3 (09-03), the merge day: the survivor still holds its own 30 cells
    tracker = Tracker(AREA, rule(survivor="oldest_qualified"), "heat_wave")
    for k, frame in enumerate(frames[:4]):
        tracker.step(DAY0 + dt.timedelta(days=k), frame)
    assert max(ev.footprint.sum() for ev in tracker.active) == 30


def test_ending_grows_only_over_the_claim_rule():
    """Review #9: ending() must not grow the box past what step can claim.

    A young (pre-anchor) event whose own claim covers only low-streak cells,
    while a high-streak blob sits inside the same labelled region beyond the
    drift window. The whole-region growth ending() used to try reaches the
    blob and keeps the event alive for a box step() could never claim; the
    claim-rule growth reaches only the near cells, the core stays below the
    floor, and the event ends - as `step` will actually do.
    """
    day = DAY0 + dt.timedelta(days=2)
    labels = np.zeros((NY, NX), int)
    labels[10:15, 16:18] = 1       # claimable today (within move_cells=2)
    labels[10:15, 30:35] = 1       # same region, but 13 cols out of reach
    valid = labels > 0
    streak = np.where(valid, 1, 0)
    streak[10:15, 30:35] = 3      # the blob was valid every day since start
    ev = TrackedEvent("heat_wave-x", "heat_wave", DAY0,
                      Box(10, 14, 10, 15), qualified=DAY0,
                      footprint=block(slice(10, 15), slice(10, 16)))
    weighted = valid * AREA

    def tracker_with(params: Params) -> Tracker:
        tr = Tracker(AREA, params, "heat_wave")
        tr.streak = streak.copy()
        return tr

    assert tracker_with(PARAMS).ending(ev, {1}, labels, valid, weighted, day)
    # legacy claims the whole region everywhere, so the blob is inside the
    # grown box and keeps the event alive - unchanged behaviour there
    old = dataclasses.replace(PARAMS, claim_valid_only=False)
    assert not tracker_with(old).ending(ev, {1}, labels, valid, weighted, day)


# --------------------------------------------- C3 versions of the LEGACY-pinned cases ---
# Same scenarios as the LEGACY-pinned cap / capped-neighbour / release / anchor
# tests above, run with the C3 defaults (review #9: those outcomes were only
# pinned under the legacy knobs). Each C3 outcome is the intended one under
# the claim/seed/merge rule; the one-line comment says why it differs.

def test_c3_capped_event_hands_the_drift_path_to_a_successor():
    frames = [block(slice(10, 15), slice(10 + 2 * t, 15 + 2 * t))
              for t in range(5)] + [cells(0)]
    evs = run(frames, dataclasses.replace(PARAMS, max_box_area_km2=35e4))
    # C3: the capped survivor ends where its box stops it, and the freed
    # drift path seeds its own successor (legacy kept one event by claiming
    # whole regions a capped box cannot grow into)
    assert [(ev.start, ev.end, ev.box) for ev in evs] == [
        (DAY0, DAY0 + dt.timedelta(days=3), Box(10, 14, 10, 16)),
        (DAY0 + dt.timedelta(days=2), DAY0 + dt.timedelta(days=5),
         Box(10, 14, 16, 22))]


def test_c3_capped_neighbour_scenario_keeps_three_objects():
    a = block(slice(10, 15), slice(10, 15))
    b_seed = block(slice(10, 12), slice(22, 25))
    region = block(slice(10, 15), slice(10, 26))   # touches both; A+B would pass the cap
    frames = [a, a | b_seed, region, region, region, cells(0)]
    evs = run(frames, dataclasses.replace(PARAMS, max_box_area_km2=60e4))
    # C3: three objects, not legacy's two - A creeps into the region only at
    # its claim reach (box 10..20), the seed survives separately (clipped out
    # of A's box instead of merging whole-region into it) and the region's
    # body holds its own object; the cap keeps them from ever welding
    assert len(evs) == 3
    assert evs[0].box == Box(10, 14, 10, 20)


def test_c3_release_keeps_the_dates_and_clips_the_box():
    a = block(slice(10, 15), slice(10, 15))
    b_seed = block(slice(10, 12), slice(22, 25))
    region = block(slice(10, 14), slice(16, 28))   # touches A's grown footprint
    frames = [a, a, a | b_seed, region, region, cells(0)]
    evs = run(frames, dataclasses.replace(PARAMS, max_box_area_km2=60e4))
    # C3: same starts and same end day as legacy; B's box reaches 18 not 16 -
    # it claims valid cells at its own drift reach, never the whole region
    assert [ev.start for ev in evs] == [DAY0, DAY0 + dt.timedelta(days=2)]
    assert evs[0].end == DAY0 + dt.timedelta(days=3)
    assert evs[1].qualified == DAY0 + dt.timedelta(days=3)
    assert evs[1].box == Box(10, 13, 18, 27)


def test_c3_anchor_rule_ends_the_drift_and_the_hot_spot_survives():
    a = block(slice(10, 15), slice(10, 15))
    hot_spot = block(slice(10, 12), slice(22, 24))       # 40k every day, too small to seed
    reach = block(slice(10, 15), slice(15, 22))          # day 5: the event reaches it
    frames = [a | hot_spot] * 4 + [a | reach | hot_spot] + [hot_spot] * 2 + [cells(0)]
    evs = run(frames, PARAMS)
    # C3: the anchored drift ends on DAY0+5 (same day as legacy's anchored
    # event); the body A left unclaimed seeds its own object on day 5 and
    # runs to DAY0+7 on the hot spot's core - legacy's whole-box claim kept
    # the single event alive to DAY0+7 instead
    assert [(ev.start, ev.end) for ev in evs] == [
        (DAY0, DAY0 + dt.timedelta(days=5)),
        (DAY0 + dt.timedelta(days=4), DAY0 + dt.timedelta(days=7))]
    assert evs[0].anchor == Box(10, 14, 10, 14)


def test_persistence_box_keeps_the_persistent_cells_and_trims_outliers():
    m = np.zeros((5, 10, 60), bool)
    m[:, 2:9, 5:41] = True                 # a spell that sits still, 5 days
    m[0:3, 0, 45] = True                   # one outlier cell, 3 of the 5 days
    area = np.full((10, 60), 1e4)
    box = Box(0, 9, 0, 59)
    k = persistence_k_days(5, 0.5)         # max(2, ceil(0.5 x 5)) == 3
    assert persistence_box(m, box, area, k) == Box(0, 8, 5, 45)
    assert persistence_box(m, box, area, k, trim=0.02) == Box(2, 8, 5, 40)
    assert persistence_box(m, box, area, k + 1) == Box(2, 8, 5, 40)  # k=4 cuts it
    # nothing reaches k -> the ever-valid bbox (fallback), never an empty box
    m2 = np.zeros((3, 4, 12), bool)
    m2[0, 1, 2] = m2[1, 2, 5] = m2[2, 3, 8] = True
    assert persistence_box(m2, Box(0, 3, 0, 11), area, 2) == Box(1, 3, 2, 8)
    assert persistence_box(np.zeros((3, 4, 12), bool), box, area, 1) is None


def test_persistence_box_shrinks_a_drifting_event():
    """The tracker box keeps the path; the published box keeps the overlap."""
    ev = run(drifting(4))[0]
    assert ev.box == Box(10, 14, 10, 23)                     # unchanged tracker role
    frames = np.array(drifting(4))[:4]                       # the event's days
    pb = persistence_box(frames, ev.box, AREA, persistence_k_days(4, 0.5))
    assert pb == Box(10, 14, 12, 21)   # first/last day's exclusive cells drop out


def test_persistence_box_wraps_the_seam():
    m = np.zeros((4, 8, 60), bool)
    m[:, 2:6, 58:60] = True
    m[:, 2:6, 0:2] = True
    pb = persistence_box(m, Box(0, 7, 0, 59), np.full((8, 60), 1e4), 2)
    assert pb == Box(2, 5, 58, 1)              # j0 > j1: the short arc across 180
    mask = pb.mask(8, 60)
    assert mask[3, [58, 59, 0, 1]].all()
    assert not mask[3, 2] and not mask[3, 57]


def test_persistence_box_keeps_a_full_ring_whole():
    m = np.zeros((4, 30, 60), bool)
    m[:, 12:17, :] = True                      # a band valid in every column
    pb = persistence_box(m, Box(0, 29, 0, 59), np.full((30, 60), 1e4), 2, trim=0.02)
    assert pb is not None and pb.mask(30, 60)[14].all()


def test_event_crossing_the_seam_keeps_a_wrapped_box():
    """C3 claim/dilate follow the wrap; the box stays the short arc."""
    def wrap(t: int) -> np.ndarray:      # 9-col block drifting 2 cols/day
        m = np.zeros((NY, NX), bool)
        m[10:15, [(74 + 2 * t + k) % NX for k in range(9)]] = True
        return m
    frames = [wrap(0), wrap(1), wrap(2), wrap(3), cells(0)]
    ev = run(frames)[0]
    assert ev.box == Box(10, 14, 74, 8)          # wrapped: 74..79, 0..8
    assert ev.box.j0 > ev.box.j1
    pb = persistence_box(np.array(frames[:4]), ev.box, AREA,
                         persistence_k_days(4, 0.5))
    assert pb == Box(10, 14, 76, 6)              # persistence trims, still wrapping
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(10, 14, 74, 8)      # unchanged from the shipped rule


# ------------------------------------- merge survivor rule (round 3, 2026-11-21) ---
# The merge survivor fix must hold on random input AND must not touch the
# legacy/flood parameter paths. Both cases run the same seeded fuzz: drifting,
# noisy blob fields that weld and split, so merge groups of every shape occur.

def fuzz_frames(seed: int, days: int = 30) -> np.ndarray:
    """A seeded drifting, flickering blob field (region merges/splits daily)."""
    rng = np.random.default_rng(seed)
    base = rng.random((NY, NX)) < 0.22
    return np.array([np.roll(base, k * int(rng.integers(0, 3)), axis=1)
                     & (rng.random((NY, NX)) < 0.85) for k in range(days)])


def test_fuzz_default_params_never_publish_a_sub_floor_event():
    """Round-3 fuzz: every valid event qualified on its OWN footprint.

    For every valid event over 20 seeded fuzz runs with the default (C3)
    Params: the footprint recorded on the qualifying day is >= the qualify
    floor, the peak is >= the floor, and start <= qualified < end. The
    absorbed-candidate bug (a merge handing qualification to a 48k km2
    candidate, publishing a valid event with a 51k km2 peak) fails this.
    """
    for seed in range(20):
        frames = fuzz_frames(seed)
        dates = [DAY0 + dt.timedelta(days=k) for k in range(frames.shape[0])]
        events = track_events(frames, dates, AREA, Params(), "heat_wave")
        for ev in events:
            day = (ev.qualified - ev.start).days
            assert 0 <= day < len(ev.footprint_area_km2)
            assert ev.footprint_area_km2[day] >= Params().qualify_area_km2
            assert ev.peak_area_km2 >= Params().qualify_area_km2
            assert ev.start <= ev.qualified
            if ev.end is not None:
                assert ev.qualified < ev.end


def test_legacy_and_flood_results_are_unchanged_by_the_survivor_rule():
    """merge_keep_box=False (LEGACY) and the flood lane keep HEAD's results.

    Golden digest of the same seeded fuzz run with the HEAD code (the old
    oldest-survivor-with-inheritance rule): 232 LEGACY heat events and 11 083
    flood events, event-for-event identical (ids, dates, qualified, boxes,
    anchors and all three area series). The survivor rule and the removal of
    the core-clock inheritance only take effect under merge_keep_box=True.
    """
    import hashlib
    import json

    def bx(b: Box | None) -> list[int] | None:
        return None if b is None else [b.i0, b.i1, b.j0, b.j1]

    # the PRODUCTION legacy fingerprint (default thresholds, shipped knobs),
    # not the tests' small-grid LEGACY
    prod_legacy = dataclasses.replace(
        Params(), claim_valid_only=False, seed_valid_only=False,
        merge_keep_box=False, core_clock="start")
    per_seed = []
    for seed in range(30):
        frames = fuzz_frames(seed)
        dates = [DAY0 + dt.timedelta(days=k)
                 for k in range(frames.shape[0])]
        per_seed.append([
            [ev.id, str(ev.start), str(ev.qualified), str(ev.end),
             bx(ev.box), bx(ev.anchor),
             [round(a, 1) for a in ev.footprint_area_km2],
             [round(a, 1) for a in ev.box_area_km2],
             [round(a, 1) for a in ev.core_area_km2]]
            for ev in track_events(frames, dates, AREA, prod_legacy, "heat_wave")])
    assert hashlib.sha256(json.dumps(per_seed).encode()).hexdigest() == \
        "c7f5b1fb9da5407b765c5be2ff07c807d950297b19b5ef657e6156504bdf2929"
    # flood keeps its own pinned knobs (claim/seed/merge legacy, start clock,
    # day-2 anchor) - same seeds, run through FloodParams. Round 4: assert the
    # FULL output digest, not just an event count (a count can hide shifted
    # dates/boxes); digest computed with the 8b3b152 code over the same fuzz.
    from ewb_live.events.flood import FloodParams
    flood = []
    for seed in range(30):
        frames = fuzz_frames(seed)
        dates = [DAY0 + dt.timedelta(days=k) for k in range(frames.shape[0])]
        flood.append([
            [ev.id, str(ev.start), str(ev.qualified), str(ev.end),
             bx(ev.box), bx(ev.anchor),
             [round(a, 1) for a in ev.footprint_area_km2],
             [round(a, 1) for a in ev.box_area_km2],
             [round(a, 1) for a in ev.core_area_km2]]
            for ev in track_events(frames, dates, AREA, FloodParams(),
                                   "flood")])
    assert sum(len(s) for s in flood) == 11083   # flood event count at HEAD
    assert hashlib.sha256(json.dumps(flood).encode()).hexdigest() == \
        "61288b41c7ee93073b76735ff22f4e77d62e87aaa28c8a6b894330e28d51573c"


# ------------------------- round 4: which member of a merge group survives ---
# Brief fix_round4.md item 1: a merge must not delete a larger, healthy
# qualified event. Repro of the central-Africa dome (real production case
# heat_wave-20260917-321: 3.62M km2, qualified at its seed day, absorbed by
# a 0.48M km2 neighbour that itself ended the next day): a small qualified
# event sits while a younger, far bigger dome grows toward it; one labelled
# region welds them. The shipped rule keeps the OLDEST qualified member -
# the small one - so the dome's history vanishes and its cells re-seed.

def dome_case() -> list[np.ndarray]:
    """A stationary 200k km2 spell and a young 2.24M km2 dome that grows into it."""
    frames = []
    for k in range(9):
        small = block(slice(22, 26), slice(15, 20))      # 20 cells = 200k km2
        if k < 8:
            dome = (block(slice(20, 36), slice(30 - 2 * (k - 2), 44))
                    if k >= 2 else np.zeros_like(small))  # 224 cells = 2.24M
            frames.append(small | dome)
        else:
            frames.append(np.zeros_like(small))
    return frames


def rule(**kw) -> Params:
    return dataclasses.replace(PARAMS, merge_survivor=kw["survivor"])


def seeded_after(frames: list[np.ndarray], p: Params, day: int,
                 region: list[int]) -> list[str]:
    """Ids of objects seeded on/after ``day`` whose box touches ``region``."""
    dates = [DAY0 + dt.timedelta(days=k) for k in range(len(frames))]
    tr = Tracker(AREA, p, "heat_wave")
    seen, seeds = set(), []
    for d, f in zip(dates, frames, strict=True):
        tr.step(d, f)
        for ev in tr.active:
            if ev.id in seen:
                continue
            seen.add(ev.id)
            b = [ev.box.i0, ev.box.i1, ev.box.j0, ev.box.j1]
            if ev.start >= dates[day] and b[1] >= region[0] and b[0] <= region[1] \
                    and b[3] >= region[2] and b[2] <= region[3]:
                seeds.append(ev.id)
    return seeds


def test_dome_case_shipped_rule_keeps_both_objects():
    """The blocker is fixed: a merge never deletes the larger spell.

    The pre-round-4 rule (pinned next) absorbed the dome (seeded day 2, peak
    3.84M km2) on day 6 by the older 200k km2 spell and never published it;
    its cells re-seeded as one-day candidates. The shipped rule keeps both
    objects: the small spell keeps its own object and the dome publishes as
    the larger event, with no re-seed chain behind the merge day.
    """
    frames = dome_case()
    evs = run(frames)                                    # shipped default
    assert [ev.start for ev in evs] == [DAY0, DAY0 + dt.timedelta(days=2)]
    assert max(ev.peak_area_km2 for ev in evs) > 3e6     # the dome published
    assert not seeded_after(frames, PARAMS, 6, [20, 35, 20, 43])


def test_dome_case_pre_round4_rule_deletes_the_bigger_spell():
    """The pinned regression: an oldest-qualified survivor deletes the dome.

    Kept so nobody re-ships the age rule without seeing what it does: the
    dome is absorbed by the SMALLER older spell and never published, and the
    dome region re-seeds (this is the G2/G3 failure on real data).
    """
    frames = dome_case()
    p = rule(survivor="oldest_qualified")
    evs = run(frames, p)
    assert [ev.peak_area_km2 for ev in evs] == pytest.approx([0.36e6])
    assert evs[0].start == DAY0                       # the SMALL spell lived
    # and the dome region re-seeded after the merge day (day 6 = 09-07)
    assert seeded_after(frames, p, 6, [20, 35, 20, 43])


def test_merge_largest_survivor_keeps_the_dome():
    """M1: the largest qualified claim today survives, not the oldest."""
    evs = run(dome_case(), rule(survivor="largest"))
    assert len(evs) == 1
    ev = evs[0]
    assert ev.start == DAY0 + dt.timedelta(days=2)    # the DOME lived on
    assert ev.end == DAY0 + dt.timedelta(days=8)
    assert ev.duration_days == 6
    assert ev.peak_area_km2 > 3e6                     # 3.84M km2 published
    # one object, not a chain of re-seeds (G3)
    assert not seeded_after(dome_case(), rule(survivor="largest"), 6,
                            [20, 35, 20, 43])


def test_merge_largest_close_publishes_the_absorbed_aged_event():
    """M2: an absorbed qualified member of >= min_days is CLOSED, not deleted.

    The small spell (6 days) ends on the merge day and is published with its
    own start date - G2's "no qualified event with >= min_days disappears
    unpublished" - while the dome carries the merged cells on.
    """
    evs = run(dome_case(), rule(survivor="largest_close"))
    assert len(evs) == 2
    small, dome = evs                                  # ordered by start
    assert (small.start, small.end, small.duration_days) == (
        DAY0, DAY0 + dt.timedelta(days=6), 6)         # closed on the merge day
    assert (dome.start, dome.end, dome.duration_days) == (
        DAY0 + dt.timedelta(days=2), DAY0 + dt.timedelta(days=8), 6)
    assert dome.peak_area_km2 > 3e6 and small.peak_area_km2 < 1e6
    assert not seeded_after(dome_case(), rule(survivor="largest_close"), 6,
                            [20, 35, 20, 43])


def test_merge_separate_never_merges_qualified_events():
    """M3: qualified events keep their own object; the small one is untouched.

    The small spell keeps its own start, its own end and - mutation guard -
    its OWN tracker box: it never grows over the dome's cells, and the dome
    keeps the contested cells as the larger event.
    """
    evs = run(dome_case(), rule(survivor="separate"))
    assert len(evs) == 2
    small, dome = evs
    assert (small.start, small.end, small.duration_days) == (
        DAY0, DAY0 + dt.timedelta(days=8), 8)
    assert small.box == Box(22, 25, 15, 19)           # not the dome's box
    assert (dome.start, dome.end) == (DAY0 + dt.timedelta(days=2),
                                      DAY0 + dt.timedelta(days=8))
    assert dome.peak_area_km2 > 3e6
    assert not seeded_after(dome_case(), rule(survivor="separate"), 6,
                            [20, 35, 20, 43])


def test_fuzz_merge_survivor_rules_publish_nothing_sub_floor():
    """G4 for the candidate rules: floor + start <= qualified < end holds."""
    for survivor in ("largest", "largest_close", "separate"):
        p = dataclasses.replace(Params(), merge_survivor=survivor)
        for seed in range(20):
            frames = fuzz_frames(seed)
            dates = [DAY0 + dt.timedelta(days=k) for k in range(frames.shape[0])]
            for ev in track_events(frames, dates, AREA, p, "heat_wave"):
                day = (ev.qualified - ev.start).days
                assert 0 <= day < len(ev.footprint_area_km2)
                assert ev.footprint_area_km2[day] >= p.qualify_area_km2
                assert ev.peak_area_km2 >= p.qualify_area_km2
                assert ev.start <= ev.qualified
                if ev.end is not None:
                    assert ev.qualified < ev.end


def test_merge_survivor_rule_fuzz_digests_are_pinned():
    """G6 (unit part): byte-identical fuzz digests for both merge rules.

    The ``oldest_qualified`` digest is the one the HEAD code (before the
    knob existed) produced for the 30-seed fuzz (323 events): the step()
    refactor (advance -> _advance, survivor pick -> _group_units) and the
    merge_survivor field moved NOTHING on that rule - the flood/legacy lanes
    ride it. The shipped default from round 4 is ``separate`` (409 events);
    its digest is pinned too, so a refactor that quietly changes either
    rule breaks here. Together with the FLOOD/LEGACY goldens this covers
    every merge path.
    """
    import hashlib
    import json

    def bx(b: Box | None) -> list[int] | None:
        return None if b is None else [b.i0, b.i1, b.j0, b.j1]

    def digest_of(p: Params) -> tuple[int, str]:
        per_seed = []
        for seed in range(30):
            frames = fuzz_frames(seed)
            dates = [DAY0 + dt.timedelta(days=k)
                     for k in range(frames.shape[0])]
            per_seed.append([
                [ev.id, str(ev.start), str(ev.qualified), str(ev.end),
                 bx(ev.box), bx(ev.anchor),
                 [round(a, 1) for a in ev.footprint_area_km2],
                 [round(a, 1) for a in ev.box_area_km2],
                 [round(a, 1) for a in ev.core_area_km2]]
                for ev in track_events(frames, dates, AREA, p, "heat_wave")])
        return (sum(len(s) for s in per_seed),
                hashlib.sha256(json.dumps(per_seed).encode()).hexdigest())

    assert Params().merge_survivor == "largest_close"  # the shipped default
    assert digest_of(dataclasses.replace(Params(),
                                         merge_survivor="oldest_qualified")) == (
        323, "e588d54c78885e403989075b50f3038390457b320d4a7205b9791d249e9d188f")
    assert digest_of(Params()) == (
        320, "63e2f305ab58c2f910cd4b5e57d5a09a815fa68a85fc3fceb70684c9e687e662")


def test_persistence_box_stays_inside_a_wide_unwrapped_box():
    """The published box never escapes the tracker box (round-4 fuzz fix).

    A 41-column box on a 60-column grid; the persistent cells sit at both
    ends. The SHORTEST arc over those columns wraps the array seam (cols
    40..1) and leaves the box at both ends; the box's own arc is the 0..40
    span. The old rule published the wrapped arc - a box bigger than (and
    mostly outside) the tracker box whose fill it reports.
    """
    m = np.zeros((4, 8, 60), bool)
    m[:, 2:6, 0:2] = True
    m[:, 2:6, 40:41] = True
    box = Box(0, 7, 0, 40)
    pb = persistence_box(m, box, np.full((8, 60), 1e4), 2)
    assert pb == Box(2, 5, 0, 40)
    assert not (pb.mask(8, 60) & ~box.mask(8, 60)).any()


def test_missing_truth_day_keeps_only_cells_valid_on_both_sides():
    from ewb_live.events.temperature import _bridge_missing_days

    mask = np.zeros((4, 3, 3), bool)
    mask[0, 0:2, :] = True
    mask[2, 1:3, :] = True
    t2m = np.full((4, 3, 3), 300.0)
    t2m[1] = np.nan
    out = _bridge_missing_days(mask, t2m)
    assert out[1, 1].all() and not out[1, 0].any() and not out[1, 2].any()
    assert (out[[0, 2, 3]] == mask[[0, 2, 3]]).all()
    assert not mask[1].any()                        # the input is not modified


def test_missing_truth_at_the_window_edge_stays_empty():
    from ewb_live.events.temperature import _bridge_missing_days

    mask = np.ones((3, 2, 2), bool)
    mask[0] = mask[2] = False
    t2m = np.full((3, 2, 2), 300.0)
    t2m[0] = t2m[2] = np.nan
    assert (_bridge_missing_days(mask, t2m) == mask).all()
    assert _bridge_missing_days(mask, None) is mask


def test_a_missing_truth_day_never_counts_toward_the_day_floor():
    from types import SimpleNamespace

    from ewb_live.events.temperature import _observed_days

    dates = [dt.date(2026, 9, d) for d in range(1, 8)]
    closed = SimpleNamespace(start=dates[1], end=dates[4])      # three event days
    still_open = SimpleNamespace(start=dates[5], end=None)
    assert _observed_days(closed, dates, set()) == 3
    assert _observed_days(closed, dates, {dates[2]}) == 2
    assert _observed_days(still_open, dates, {dates[6]}) == 1


def test_merged_run_length_bridges_a_missing_truth_day(monkeypatch):
    from ewb_live.events.temperature import recompute_merged_max_consec
    from ewb_live.events.types import Event
    from ewb_live.viz import build as vb

    t = np.full((20, 2, 2), 310.0)
    t[8:12] = np.nan                                            # day 3 of 5
    axis = np.array([0.0, 1.0])
    monkeypatch.setattr(vb, "truth_times", lambda start, end: list(range(20)))
    monkeypatch.setattr(vb, "open_truth_window", lambda *a: (t, axis, axis, "mirror"))
    monkeypatch.setattr(vb, "thresholds_for", lambda *a: (np.full_like(t, 300.0), 0.85))
    monkeypatch.setattr(vb, "land_mask", lambda lat, lon: np.ones((2, 2), bool))
    ev = Event(id="x", event_type="heat_wave", title="", start=dt.datetime(2026, 9, 1),
               end=dt.datetime(2026, 9, 6), bbox=(0.0, 1.0, 0.0, 1.0), peak_value=40.0,
               details={"merged_count": 2, "max_consecutive_days": 2, "quantile": 0.85})
    assert recompute_merged_max_consec([ev], None) == 1
    assert ev.details["max_consecutive_days"] == 5
