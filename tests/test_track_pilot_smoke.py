"""Offline smoke test for the WP0 pilot tracker (ewb_live/tools/track_pilot.py).

Synthetic MSLP fields only: a closed low that moves is followed, a low that
vanishes is lost (misses from then on), a second low does not capture the
track, and the along/cross geometry reproduces the section 3.4 hand checks.
"""
import datetime as dt

import numpy as np
import pytest

from ewb_live.tools import track_pilot as tp

LAT = np.arange(0.0, 40.0 + 0.25, 0.25)
LON = np.arange(-120.0, -80.0 + 0.25, 0.25)


def _low(lat0, lon0, depth=30.0, sigma=2.5):
    yy, xx = np.meshgrid(LAT, LON, indexing="ij")
    d2 = (yy - lat0) ** 2 + ((xx - lon0) * np.cos(np.radians(lat0))) ** 2
    return -depth * np.exp(-d2 / (2 * sigma ** 2))


def _stack(centres, base=1012.0):
    """One field per step; each entry is a list of (lat, lon[, depth]) lows."""
    out = []
    for lows in centres:
        f = np.full((LAT.size, LON.size), base)
        for low in lows:
            f = f + _low(*low)
        out.append(f)
    return np.array(out)


def _path(n, lat0=10.0, lon0=-110.0, dlat=1.0, dlon=1.2):
    return [(lat0 + i * dlat, lon0 + i * dlon) for i in range(n)]


def test_tracker_follows_moving_low():
    path = _path(10)
    fld = _stack([[p] for p in path])
    la, lo, pr, ok, nod = tp.follow_track(fld, LAT, LON, path[0][0], path[0][1], tp.TC_PARAMS)
    assert ok.all()
    assert np.max(tp.haversine_km(la, lo, [p[0] for p in path], [p[1] for p in path])) < 30.0
    assert np.allclose(pr, 1012.0 - 30.0, atol=0.5)


def test_tracker_loses_vanished_low():
    path = _path(10)
    fld = _stack([[p] if i < 5 else [] for i, p in enumerate(path)])
    la, lo, pr, ok, nod = tp.follow_track(fld, LAT, LON, path[0][0], path[0][1], tp.TC_PARAMS)
    assert ok[:5].all()
    assert not ok[5:].any()                    # vanished at step 5: misses ever after
    assert np.isnan(la[5:]).all() and np.isnan(pr[5:]).all()


def test_tracker_does_not_jump_to_a_neighbouring_deeper_low():
    path = _path(10)
    # a deeper low 12 degrees away stays put; it must never capture the track
    fld = _stack([[p, (30.0, -95.0, 45.0)] for p in path])
    la, lo, pr, ok, nod = tp.follow_track(fld, LAT, LON, path[0][0], path[0][1], tp.TC_PARAMS)
    assert ok.all()
    assert np.max(tp.haversine_km(la, lo, [p[0] for p in path], [p[1] for p in path])) < 30.0


def test_open_trough_is_not_a_low():
    # a monotone ramp has no closed contour anywhere
    ramp = np.tile(np.linspace(1000.0, 1020.0, LON.size), (LAT.size, 1))
    fld = np.array([ramp, ramp])
    *_, ok, nod = tp.follow_track(fld, LAT, LON, 20.0, -100.0, tp.TC_PARAMS)
    assert not ok.any()


def test_along_cross_hand_checks():
    # observed at the equator moving due north (heading 0) at 30 km/h
    cases = {"ahead": (1.0, 0.0), "behind": (-1.0, 0.0), "right": (0.0, 1.0), "left": (0.0, -1.0)}
    got = {k: tp.along_cross(0.0, 0.0, la, lo, 0.0, 30.0) for k, (la, lo) in cases.items()}
    d = 111.195
    assert got["ahead"][1] == pytest.approx(d, rel=1e-3) and abs(got["ahead"][2]) < 0.5
    assert got["behind"][1] == pytest.approx(-d, rel=1e-3)
    assert got["right"][2] == pytest.approx(d, rel=1e-3) and abs(got["right"][1]) < 0.5
    assert got["left"][2] == pytest.approx(-d, rel=1e-3)
    for dist, a, c in got.values():
        assert a ** 2 + c ** 2 == pytest.approx(dist ** 2, rel=1e-6)


def test_along_cross_undefined_when_stationary_and_across_dateline():
    d, a, c = tp.along_cross(0.0, 0.0, 1.0, 0.0, 0.0, 1.0)         # < 2 km/h
    assert d > 100 and np.isnan(a) and np.isnan(c)
    d, a, c = tp.along_cross(10.0, 179.5, 10.0, -179.5, 90.0, 20.0)  # eastward over the dateline
    assert d == pytest.approx(109.5, rel=0.02) and a == pytest.approx(d, rel=1e-3)


def test_observed_motion_centred_and_one_sided():
    t0 = dt.datetime(2026, 9, 1)
    times = [t0 + dt.timedelta(hours=6 * i) for i in range(4)]
    lat = np.array([10.0, 10.5, 11.0, 11.5])
    lon = np.full(4, -100.0)
    for k in range(4):                                 # due north everywhere, 0.5 deg / 6 h
        hd, sp = tp.observed_motion(times, lat, lon, k)
        assert abs(hd) < 0.5 and sp == pytest.approx(55.6 / 6 * 1.0, rel=0.02)
    assert np.isnan(tp.observed_motion(times[:1], lat[:1], lon[:1], 0)[0])


def test_guard_blocks_monday_morning_and_allows_other_times(monkeypatch):
    class R:  # fake systemctl
        stdout = "inactive\n"

    monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: R())
    mon = dt.datetime(2026, 11, 23, 8, 0, tzinfo=dt.timezone.utc)      # a Monday
    assert not tp.guard_status(mon)[0]
    assert tp.guard_status(dt.datetime(2026, 11, 23, 14, 0, tzinfo=dt.timezone.utc))[0]
    assert tp.guard_status(dt.datetime(2026, 11, 20, 20, 0, tzinfo=dt.timezone.utc))[0]
    R.stdout = "active\n"
    assert not tp.guard_status(dt.datetime(2026, 11, 20, 20, 0, tzinfo=dt.timezone.utc))[0]


def _arc_storm(turn_sign):
    """Observed track on a circle: heading rotates 3 deg / 6 h (clockwise for +1)."""
    t0 = dt.datetime(2026, 9, 1)
    lat, lon, hd = [15.0], [-100.0], 300.0
    for _ in range(60):
        la, lo = lat[-1], lon[-1]
        step = 12.0 * 6.0 / 111.195                      # 12 km/h for 6 h in degrees
        lat.append(la + step * np.cos(np.radians(hd)))
        lon.append(lo + step * np.sin(np.radians(hd)) / np.cos(np.radians(la)))
        hd += turn_sign * 3.0
    times = [t0 + dt.timedelta(hours=6 * i) for i in range(len(lat))]
    return tp.Storm("tc", "synthetic", "arc", times, np.array(lat), np.array(lon),
                    np.full(len(lat), np.nan))


@pytest.mark.parametrize("turn_sign,frac_key", [(+1, "frac_cross_negative"), (-1, "frac_cross_positive")])
def test_recurving_track_puts_persistence_on_the_outside(turn_sign, frac_key):
    # a track turning clockwise (NH recurvature) leaves a straight-line forecast LEFT of it
    res = tp.persistence_sanity(_arc_storm(turn_sign))
    for lead in (48, 96):
        assert res["by_lead"][lead][frac_key] >= 0.9


def test_all_nan_step_is_nodata_not_a_miss():
    path = _path(6)
    fld = _stack([[p] for p in path])
    fld[0] = np.nan                                    # e.g. GraphCast lead 0 fill
    fld[3] = np.nan                                    # and a later fill step
    la, lo, pr, ok, nod = tp.follow_track(fld, LAT, LON, path[0][0], path[0][1], tp.TC_PARAMS)
    assert nod[0] and nod[3] and nod.sum() == 2
    assert ok[[1, 2, 4, 5]].all()                      # fill steps neither lose nor invent the track
    assert not ok[[0, 3]].any()
