"""ar_filter: track-level tropical/TC exclusion + linking (owner 2026-11-09,
tropical rule revised 2026-11-10, link caps 2026-11-10, TC screen moved to
STEP level 2026-11-10).

Exclusion is TRACK level for the tropical rule, so "drop the whole object"
means "drop it at every step" — which is only safe if a track is one physical
object: hence the centroid-based tropical test and the speed/age caps on
linking. The TC rule is per STEP: a cyclone reaching an AR removes the frames
it touches, not the AR's whole life.
"""
import numpy as np
import pandas as pd

from ewb_live.events.ar_filter import (LINK_MAX_STEPS, _storm_pass,
                                       ar_filter_tracks, track_ar_components)

LATS = np.arange(-90.0, 91.0, 1.0)
LONS = np.arange(-180.0, 180.0, 1.0)
T = np.array([np.datetime64("2026-10-01T00", "h")
              + np.timedelta64(6 * i, "h") for i in range(4)])


def _grid(n=4):
    return np.zeros((n, LATS.size, LONS.size), bool)


def _blob(m, i, lat0, lat1, lon0, lon1):
    ys = np.nonzero((LATS >= lat0) & (LATS <= lat1))[0]
    xs = np.nonzero((LONS >= lon0) & (LONS <= lon1))[0]
    m[i][np.ix_(ys, xs)] = True


def test_tropical_cells_with_midlatitude_centroid_are_kept():
    """Owner 2026-11-10: a tropical CELL is not the test. A landfalling AR
    reaching into the tropics keeps its centroid outside ±15 and stays IN —
    the old 'any cell at any step' rule deleted 6.7M cells this way."""
    m = _grid()
    for i in range(4):
        _blob(m, i, 10, 30, 10, 20)   # centroid 20N, cells down to 10N
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert dropped == [] and tracks[0]["steps"] == [0, 1, 2, 3]
    assert int(keep.sum()) == int(m.sum())


def test_centroid_inside_tropics_for_a_day_drops_the_whole_track():
    """'It shouldn't ever enter 15S/N, and if it does, if it stays for a day,
    remove the ID'd AR entirely' (owner 2026-11-10): 4 consecutive 6-hourly
    steps = one day, and the drop covers every step the track ever had."""
    n = 5
    T5 = np.array([np.datetime64("2026-10-01T00", "h")
                   + np.timedelta64(6 * i, "h") for i in range(n)])
    m = _grid(n)
    for i in range(n):                # centroid 9.5N for five steps
        _blob(m, i, 5, 14, 10, 20)
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T5, None)
    assert not keep.any()
    assert len(dropped) == 1 and dropped[0]["reason"] == "tropical"
    assert dropped[0]["tropical_run_steps"] == n


def test_centroid_in_tropics_under_a_day_is_kept():
    """18 h inside the band is weather (a passing squall line, a monsoon
    plume), not a tropical system: those steps stay painted."""
    m = _grid()
    for i in range(3):                # centroid 9.5N for 18 h only
        _blob(m, i, 5, 14, 10, 20)
    _blob(m, 3, 20, 40, 10, 20)       # step 3 sits back outside the band
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert dropped == []
    assert [t["status"] for t in tracks] == ["kept", "kept"]
    assert int(keep[:3].sum()) == int(m[:3].sum())   # tropical 18 h painted


def test_persistent_ar_no_flicker_and_25_60N_kept():
    m = _grid()
    for i in range(4):
        _blob(m, i, 25 + i, 60, 160 + 2 * i, 175 + 2 * i)
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert dropped == []
    assert len(tracks) == 1 and tracks[0]["steps"] == [0, 1, 2, 3]
    for i in range(4):  # no frame-to-frame blink
        assert int(keep[i].sum()) == int(m[i].sum())


def test_tc_colocated_track_dropped_at_all_steps():
    m = _grid()
    for i in range(4):
        _blob(m, i, 25, 35, -50, -40)
    tracks = pd.DataFrame({"valid_time": ["2026-10-01 12:00"],
                           "latitude": [30.0], "longitude": [-45.0],
                           "storm_id": ["X1"], "tc_name": ["DUMMY"]})
    keep, dropped, _labels, _tracks = ar_filter_tracks(m, LATS, LONS, T, tracks)
    assert not keep.any()
    assert dropped[0]["reason"] == "tc" and dropped[0]["tc_name"] == "DUMMY"


def test_tc_far_in_time_keeps():
    m = _grid()
    for i in range(4):
        _blob(m, i, 25, 35, -50, -40)
    old = pd.DataFrame({"valid_time": ["2026-10-06 00:00"],
                        "latitude": [30.0], "longitude": [-45.0],
                        "storm_id": ["X2"], "tc_name": ["OLD"]})
    keep, dropped, _labels, _tracks = ar_filter_tracks(m, LATS, LONS, T, old)
    assert dropped == [] and int(keep.sum()) == int(m.sum())


def test_tc_touch_removes_only_the_touched_steps():
    """Owner reports 2026-11-10 (north Atlantic 08-12 18Z -> 08-13 00Z "IVT
    values just disappear"; south Atlantic 08-14 12Z -> 08-15 18Z "disappears
    then pops back"): the TC screen deleted the WHOLE track, so one cyclone
    erased weeks of mid-latitude AR (`ar_20260811T00_9` 170 623 cells / 56
    steps, `ar_20260815T12_254` 73 422 cells). A storm contaminates the frames
    it is in and nothing else — the steps it does not reach stay IN."""
    n = 12                                  # a 72 h mid-latitude AR
    T12 = np.array([np.datetime64("2026-10-01T00", "h")
                    + np.timedelta64(6 * i, "h") for i in range(n)])
    m = _grid(n)
    for i in range(n):
        _blob(m, i, 25, 35, -50, -40)       # one stationary object, centroid 30N
    # one storm point at the LAST step: the 30 h gate reaches steps 6..11 only
    tc = pd.DataFrame({"valid_time": [str(T12[-1]).replace("T", " ")],
                       "latitude": [30.0], "longitude": [-45.0],
                       "storm_id": ["X3"], "tc_name": ["CROSSER"]})
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T12, tc)
    assert dropped == []                    # the AR is NOT deleted by a touch
    assert tracks[0]["status"] == "kept"
    first_hit = int(np.argmax([str(t) in tracks[0]["tc_steps"]
                               for t in T12]))
    assert tracks[0]["tc_name"] == "CROSSER"
    assert [t["tc_steps"] for t in tracks][0] == [str(t) for t in T12[first_hit:]]
    assert int(keep[:first_hit].sum()) == int(m[:first_hit].sum())  # painted
    assert not keep[first_hit:].any()                              # storm steps
    assert first_hit == 6                    # the 30 h gate, not the whole life


def test_tc_over_whole_track_drops_it_and_says_where():
    """A track inside a cyclone for EVERY step is not an AR: reason 'tc', and
    the ledger now says WHERE (the owner report could only be attributed by
    re-running the detector — notes/ar_outage_20261110.md §2)."""
    m = _grid()
    for i in range(4):
        _blob(m, i, 25, 35, -50, -40)
    tc = pd.DataFrame({"valid_time": ["2026-10-01 12:00"],
                       "latitude": [30.0], "longitude": [-45.0],
                       "storm_id": ["X4"], "tc_name": ["HOME"]})
    keep, dropped, _labels, _tracks = ar_filter_tracks(m, LATS, LONS, T, tc)
    assert not keep.any() and dropped[0]["reason"] == "tc"
    assert dropped[0]["bbox"] == [25.0, 35.0, -50.0, -40.0]
    assert dropped[0]["tc_steps"] == ["2026-10-01T00", "2026-10-01T06",
                                      "2026-10-01T12", "2026-10-01T18"]


def test_dropped_tropical_track_records_its_location():
    m = _grid()
    for i in range(4):
        _blob(m, i, 5, 14, 10, 20)        # centroid 9.5N -> tropical drop
    keep, dropped, _labels, _tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert dropped[0]["reason"] == "tropical"
    assert dropped[0]["bbox"] == [5.0, 14.0, 10.0, 20.0]


def test_dropped_tropical_track_records_its_location():
    m = _grid()
    for i in range(4):
        _blob(m, i, 5, 14, 10, 20)        # centroid 9.5N -> tropical drop
    keep, dropped, _labels, _tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert dropped[0]["reason"] == "tropical"
    assert dropped[0]["bbox"] == [5.0, 14.0, 10.0, 20.0]


def test_split_strongest_child_inherits_other_gets_new_id():
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 14)   # parent (150 px)
    _blob(m, 1, 30, 40, 0, 9)    # child A: overlap 100 px, strength 1.0
    _blob(m, 1, 30, 40, 12, 21)  # child B: overlap 30 px, strength 0.3
    # (gap at cols 10-11 keeps A and B separate components under 8-conn)
    _labels, tracks = track_ar_components(m, T[:2], LATS, LONS)
    assert len(tracks) == 2
    a, b = tracks
    assert a["steps"] == [0, 1] and b["steps"] == [1]
    assert a["id"] != b["id"]


def test_new_far_child_without_parent_overlap_does_not_crash():
    # regression (box, 2026-11-09): bincount length is max_flat+1 — a new
    # child that never touches a labeled parent leaves the top flat indices
    # unused and the overlap-matrix reshape blew up
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 9)
    _blob(m, 1, 30, 40, 0, 9)      # persists (child label 1)
    _blob(m, 1, 60, 70, -160, -150)  # brand-new far child (label 2)
    _labels, tracks = track_ar_components(m, T[:2], LATS, LONS)
    assert len(tracks) == 2


def test_merge_child_inherits_strongest_parent_other_ends():
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 14)     # P1: 150 px (gap at col 15 = 2 comps)
    _blob(m, 0, 30, 40, 16, 30)    # P2: 150 px
    _blob(m, 1, 30, 40, 0, 24)     # child: overlap P1=150, P2=90
    _labels, tracks = track_ar_components(m, T[:2], LATS, LONS)
    assert len(tracks) == 2       # no third track: child inherited, not new
    winner, loser = tracks
    assert winner["steps"] == [0, 1] and loser["steps"] == [0]


# --- GAP BRIDGE (owner 2026-11-09, option B): 1-step dropout keeps one id,
# the gap step shows the last footprint (labels, flagged); 2-step ends it ---

def test_one_step_dropout_keeps_one_id_and_gets_bridged_frame():
    m = _grid()
    _blob(m, 0, 30, 40, 0, 14)
    _blob(m, 1, 30, 40, 0, 14)
    # step 2: physically absent (mask dips below threshold)
    _blob(m, 3, 30, 40, 2, 16)     # reappears overlapping the footprint
    labels, tracks = track_ar_components(m, T, LATS, LONS)
    assert len(tracks) == 1        # one id across the gap
    tr = tracks[0]
    assert tr["steps"] == [0, 1, 3]
    assert tr["bridged"] == [2]
    ys = np.nonzero((LATS >= 30) & (LATS <= 40))[0]
    xs = np.nonzero((LONS >= 0) & (LONS <= 14))[0]
    expect = np.zeros_like(labels[2])
    expect[np.ix_(ys, xs)] = 1
    assert (labels[2] == expect).all()   # exactly the last footprint, nothing more


def test_two_step_gap_ends_the_track():
    T5 = T[:4].tolist() + [np.datetime64("2026-10-02T00", "h")]
    m = _grid(5)
    _blob(m, 0, 30, 40, 0, 14)
    _blob(m, 1, 30, 40, 0, 14)
    # steps 2+3 absent
    _blob(m, 4, 30, 40, 0, 14)
    _labels, tracks = track_ar_components(m, np.array(T5), LATS, LONS)
    assert len(tracks) == 2                    # a 2-step gap ENDS the track
    assert tracks[0]["steps"] == [0, 1] and tracks[1]["steps"] == [4]
    assert all(not tr["bridged"] for tr in tracks)


def test_bridged_footprint_is_display_only_in_keep():
    m = _grid()
    _blob(m, 0, 30, 40, 0, 14)
    _blob(m, 1, 30, 40, 0, 14)
    _blob(m, 3, 30, 40, 2, 16)
    keep, _dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert tracks[0]["bridged"] == [2]
    assert not keep[2].any()          # physical mask never shows the ghost
    assert int(keep[[0, 1, 3]].sum()) == int(m[[0, 1, 3]].sum())


def test_reappearance_without_overlap_is_a_new_track_not_a_bridge():
    m = _grid()
    _blob(m, 0, 30, 40, 0, 14)
    _blob(m, 1, 30, 40, 0, 14)
    _blob(m, 3, 30, 40, 60, 74)       # reappears far away
    _labels, tracks = track_ar_components(m, T, LATS, LONS)
    assert len(tracks) == 2
    assert all(not tr["bridged"] for tr in tracks)


# --- LINK CAPS (owner 2026-11-10): the linker, not the threshold, is what
# made a track-level screen destructive. A month-long chain (measured: 132
# steps / 1.92M cells) hid every AR it ever touched, and the pop-in at 12Z
# was that chain re-seeding a fresh id for the same physical object.

def test_far_child_with_enough_overlap_does_not_inherit_the_id():
    # child overlaps the parent by exactly TRACK_OVERLAP_FRAC (165/660 px) but
    # its centroid sits ~2000 km east: overlap alone used to be a licence to
    # rename, which is how one id crawled between ocean basins.
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 14)        # parent: 165 px, centroid 7E
    _blob(m, 1, 30, 40, 0, 59)        # child: 660 px, centroid 29.5E
    _labels, tracks = track_ar_components(m, T[:2], LATS, LONS)
    assert len(tracks) == 2
    assert tracks[0]["steps"] == [0] and tracks[1]["steps"] == [1]


def test_normal_motion_still_links():
    # 3 deg of lon at 35N is ~274 km/6 h (~12.7 m/s) — ordinary AR advection
    # must keep one id, or the caps would shred every real AR.
    m = _grid(3)
    for i in range(3):
        _blob(m, i, 30, 40, 3 * i, 3 * i + 14)
    _labels, tracks = track_ar_components(m, T[:3], LATS, LONS)
    assert len(tracks) == 1 and tracks[0]["steps"] == [0, 1, 2]


def test_ghost_bridge_with_high_track_ids_remaps_centroids():
    """Regression (real-data run, 2026-11-10): the ghost grid holds TRACK ids,
    the centroid tables are indexed by the step's LOCAL label. Track ids are
    never compacted, so once the window has spawned many tracks a ghost's id
    exceeds the local label count of its own step (measured: id 36 against 22
    labels) and the drift lookup read out of bounds. Five ARs early, three
    dying, the highest-id one gap-and-return reproduces it."""
    n = 6
    T6 = np.array([np.datetime64("2026-10-01T00", "h")
                   + np.timedelta64(6 * i, "h") for i in range(n)])
    m = _grid(n)
    # ndimage labels in raster order and LATS runs south->north, so the
    # northernmost blob gets the LAST local label at step 0 and therefore the
    # HIGHEST track id — which is what a ghost must have to overrun the
    # centroid table two steps later.
    blobs = [(-60, -50, 20, 34), (-30, -20, 60, 74), (0, 10, 100, 114),
             (20, 30, 140, 154)]
    hi = (40, 50, -160, -150)          # the one that gaps and returns (id 4)
    for i in (0, 1):                   # five tracks 0..4
        for b in blobs:
            _blob(m, i, *b)
        _blob(m, i, *hi)
    for i in (2, 3):                   # three of them die after t=1
        _blob(m, i, *blobs[0])
        _blob(m, i, *hi)
    _blob(m, 4, *blobs[0])             # id 4 absent (gap step)
    _blob(m, 5, *blobs[0])
    _blob(m, 5, *hi)                   # id 4 returns -> ghost bridge at t=4
    labels, tracks = track_ar_components(m, T6, LATS, LONS)
    back = next(t for t in tracks if 5 in t["steps"] and 4 not in t["steps"]
                and 3 in t["steps"])
    assert back["bridged"] == [4] and back["id"].endswith("_4")
    assert int((labels[4] > 0).sum()) > 0     # gap frame carries the footprint


def test_far_ghost_does_not_bridge_across_the_speed_cap():
    m = _grid()
    _blob(m, 0, 30, 40, 0, 14)
    _blob(m, 1, 30, 40, 0, 14)
    _blob(m, 3, 30, 40, 120, 134)      # same id could not have teleported
    labels, tracks = track_ar_components(m, T, LATS, LONS)
    assert len(tracks) == 2 and all(not t["bridged"] for t in tracks)
    assert not labels[2].any()


def test_elder_chain_restarts_under_a_new_id():
    n = LINK_MAX_STEPS + 5
    tl = np.array([np.datetime64("2026-10-01T00", "h")
                   + np.timedelta64(6 * i, "h") for i in range(n)])
    m = np.zeros((n, LATS.size, LONS.size), bool)
    for i in range(n):
        _blob(m, i, 30, 40, 0, 14)    # same blob, every step, no drift
    _labels, tracks = track_ar_components(m, tl, LATS, LONS)
    assert len(tracks) == 2
    assert tracks[0]["steps"] == list(range(LINK_MAX_STEPS))
    assert tracks[1]["steps"] == list(range(LINK_MAX_STEPS, n))
    assert tracks[0]["id"] != tracks[1]["id"]


def test_storm_pass_ignores_ghost_label_pixels():
    """A label pixel where the physical mask is False (gap-bridge ghost)
    must never trigger the TC screen (physical px only)."""
    times = np.array([f"2026-10-01T{h:02d}" for h in range(0, 24, 6)],
                     "datetime64[h]")
    lat3, lon3 = np.array([-89.0, -88.0, -87.0]), np.array([-179., -178., -177.])
    mask = np.zeros((4, 3, 3), bool)
    labels = np.zeros((4, 3, 3), np.int32)
    labels[2, 2, 2] = 1                     # GHOST px only (mask False)
    storm = pd.DataFrame({"valid_time": [pd.Timestamp("2026-10-01T12")],
                          "latitude": [-88.0], "longitude": [-178.0],
                          "storm_id": ["X"], "tc_name": ["X"]})
    assert _storm_pass(labels, mask, lat3, lon3, times, storm) == {}
    mask[2, 2, 2] = True                    # same px physical now -> hits
    assert 0 in _storm_pass(labels, mask, lat3, lon3, times, storm)
