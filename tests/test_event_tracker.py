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
PARAMS = Params(region_radius=1, region_min_fraction=0.3, move_cells=2,
                max_ramp_days=4, end_area_km2=1e4)
# The shipped tracker claims whole labelled regions, seeds candidates from
# them, and unions the boxes of merged objects. The 2026-11-20 box-size rule
# (Params.claim_valid_only / seed_valid_only / merge_keep_box, SPEC §2) is the
# default; these tests predate it and assert its geometry, so they run with
# the legacy knobs on. The C3 rule has its own tests further down.
LEGACY = dataclasses.replace(PARAMS, claim_valid_only=False,
                             seed_valid_only=False, merge_keep_box=False)
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
    """H3: a small footprint must not swallow a whole labelled region (belt)."""
    event = block(slice(13, 18), slice(8, 13))                  # 25 cells, qualifies
    belt = event | block(slice(10, 21), slice(12, 61))          # one giant region
    frames = [event, belt, belt, belt, cells(0)]
    evs = run(frames)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.duration_days == 4
    # C3: the box only ever grows over valid cells within move_cells=2/day of
    # what the event holds - it never reaches past the belt's near edge
    assert ev.box == Box(11, 19, 8, 14)
    # legacy: the day-1 claim takes the ENTIRE region -> a 5.8 M km2 box
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(10, 20, 8, 60)


def test_drift_window_clips_a_touching_blob_to_its_near_edge():
    """H5: cells of a region past the move_cells window are not claimed."""
    here = block(slice(10, 15), slice(10, 15))                  # 25 cells
    blob = here | block(slice(8, 17), slice(16, 41))            # touches at col 16
    frames = [here, blob, blob, blob, cells(0)]
    ev = run(frames)[0]
    # the blob past the drift window never enters the box; its body seeds a
    # candidate of its own, which the next merge absorbs without its box
    assert ev.box == Box(8, 16, 10, 16)
    ev_old = run(frames, LEGACY)[0]
    assert ev_old.box == Box(8, 16, 10, 40)                     # blob taken whole


def test_merge_keeps_the_older_box():
    """H2: a merge must not paint the partner's whole box onto the survivor."""
    a = block(slice(10, 15), slice(5, 11))                      # 30 cells, qualifies
    b = block(slice(10, 13), slice(25, 29))                     # 90k km2 candidate
    region = block(slice(10, 15), slice(5, 31))                 # one label over both
    frames = [a | b, region, region, cells(0)]
    evs = run(frames)
    assert len(evs) == 1                                       # still one event
    # the older object keeps its own box; B's block (and the daily re-seeded
    # candidate from the region's body) never stretches it
    assert evs[0].box == Box(10, 14, 5, 10)
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
