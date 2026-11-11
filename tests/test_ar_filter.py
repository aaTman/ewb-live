"""ar_filter: track-level tropical/TC exclusion + linking (owner 2026-11-09).

Replaces the 2026-11-08 per-frame screen tests: exclusion is now TRACK
level, so "drop the whole object" means "drop it at every step".
"""
import numpy as np
import pandas as pd

from ewb_live.events.ar_filter import (
    _storm_pass, ar_filter_tracks, track_ar_components)

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


def test_track_touching_14N_at_one_step_dropped_at_all_steps():
    m = _grid()
    _blob(m, 0, 20, 30, 10, 20)
    _blob(m, 1, 20, 30, 10, 20)
    _blob(m, 2, 14, 30, 10, 20)  # tail crosses the 15 deg band this step
    _blob(m, 3, 20, 30, 10, 20)
    keep, dropped, _labels, tracks = ar_filter_tracks(m, LATS, LONS, T, None)
    assert not keep.any()  # in for all steps or out for all
    assert len(dropped) == 1 and dropped[0]["reason"] == "tropical"
    assert len(tracks) == 1 and tracks[0]["steps"] == [0, 1, 2, 3]


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


def test_split_strongest_child_inherits_other_gets_new_id():
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 14)   # parent (150 px)
    _blob(m, 1, 30, 40, 0, 9)    # child A: overlap 100 px, strength 1.0
    _blob(m, 1, 30, 40, 12, 21)  # child B: overlap 30 px, strength 0.3
    # (gap at cols 10-11 keeps A and B separate components under 8-conn)
    _labels, tracks = track_ar_components(m, T[:2])
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
    _labels, tracks = track_ar_components(m, T[:2])
    assert len(tracks) == 2


def test_merge_child_inherits_strongest_parent_other_ends():
    m = _grid(2)
    _blob(m, 0, 30, 40, 0, 14)     # P1: 150 px (gap at col 15 = 2 comps)
    _blob(m, 0, 30, 40, 16, 30)    # P2: 150 px
    _blob(m, 1, 30, 40, 0, 24)     # child: overlap P1=150, P2=90
    _labels, tracks = track_ar_components(m, T[:2])
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
    labels, tracks = track_ar_components(m, T)
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
    _labels, tracks = track_ar_components(m, np.array(T5))
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
    _labels, tracks = track_ar_components(m, T)
    assert len(tracks) == 2
    assert all(not tr["bridged"] for tr in tracks)


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
