"""Screen 6 (audit F1) + quota severity (audit F2) — ne-fix-mlc 2026-11-19.

F1: a post-tropical TC continuation must not publish as its own MLC. The
regression is the real Josephine 2026-10 case (``notes/audit_mlc.md``): an
MLC track that starts ON an IBTrACS storm's ET/SS track and then continues
over its own — the genesis screen (tropical natures only) cannot see it, the
whole-track overlap screen can. All synthetic; the IBTrACS table is a stub
loader with the real ``load_tracks`` columns that matter.

F2: depth must dominate the quota ranking — depth^2 x min(days, 7) x bomb.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ewb_live.events import mlc as M
from ewb_live.events import mlc_track as T
from ewb_live.events.types import Event

T0 = dt.datetime(2026, 10, 18, 6)


def _track(lat0=-0.25, lon_step=1.0, n=20, lat_start=31.0, lon_start=-71.0):
    times = [T0 + dt.timedelta(hours=6 * i) for i in range(n)]
    lat = lat_start + lat0 * np.arange(n)
    lon = lon_start + lon_step * np.arange(n)
    mslp = 1000.0 - 1.2 * np.arange(n)
    return T.Track(times, lat, lon, mslp, np.zeros(n, bool))


def _fixes(rows):
    """rows: (time, lat, lon, nature, name) -> minimal load_tracks-shaped frame."""
    return pd.DataFrame({
        "time": [r[0] for r in rows],
        "lat": [r[1] for r in rows],
        "lon": [r[2] for r in rows],
        "nature": [r[3] for r in rows],
        "name": [r[4] for r in rows],
    })


def _stub(fx):
    return lambda start, end: fx


# ------------------------------------------------------- screen 6: the track
def test_post_tropical_continuation_is_rejected():
    """Josephine 2026: ET/SS fixes under the young MLC track, then it diverges."""
    tr = _track()                      # NE-track (31,-71) -> (26.25,-52)
    rows = []
    for k in range(10):                # storm holds near the first fixes 12 h apart
        t = tr.times[k * 2] if k * 2 < len(tr.times) else tr.times[-1]
        rows.append((t, tr.lat[min(k * 2, len(tr) - 1)] + 0.2,
                     tr.lon[min(k * 2, len(tr) - 1)] + 0.2,
                     "ET" if k < 6 else "SS", "JOSEPHINE"))
    check = M.tc_overlap_checker(window=(T0, tr.times[-1]), loader=_stub(_fixes(rows)))
    assert check(tr) == "JOSEPHINE"


def test_track_to_event_rejects_the_double_count():
    tr = _track(lat0=-0.25, lon_step=2.0)   # deep enough, moves > 1000 km
    ev, reason = M.track_to_event(tr, elev_m=0.0, tropical=False,
                                  tc_overlap="JOSEPHINE")
    assert ev is None
    assert "same system as tracked TC" in reason and "JOSEPHINE" in reason
    # without the overlap flag the same track qualifies (so the screen, not
    # some other gate, is what removes it)
    ev2, _ = M.track_to_event(tr, elev_m=0.0, tropical=False)
    assert ev2 is not None


def test_distant_storm_does_not_fire():
    tr = _track()
    rows = [(t, -45.0, 150.0, "TS", "UNNAMABLE")
            for t in (tr.times[0], tr.times[5], tr.times[10])]
    check = M.tc_overlap_checker(window=(T0, tr.times[-1]), loader=_stub(_fixes(rows)))
    assert check(tr) is None


def test_one_coincident_fix_is_not_enough():
    """A lone 6-hourly coincidence (crossing tracks) stays publishable."""
    tr = _track(lon_step=6.0)   # ~570 km per step: neighbours fall outside 500 km
    rows = [(tr.times[3], tr.lat[3] + 0.1, tr.lon[3] + 0.1, "ET", "PASSERBY")]
    check = M.tc_overlap_checker(window=(T0, tr.times[-1]), loader=_stub(_fixes(rows)))
    assert check(tr) is None


def test_time_separation_outside_the_window_does_not_fire():
    """Same coordinates 30 h apart on a fast track: the low moved on, it is
    not the same system (the track is ~2 400 km away when the fix exists)."""
    tr = _track(lon_step=5.0)   # ~475 km per 6-h step
    rows = [(t, tr.lat[k], tr.lon[k], "TS", "GHOST")
            for k in (0, 8, 16) for t in (tr.times[k] + dt.timedelta(hours=30),
                                          tr.times[k] - dt.timedelta(hours=30))]
    check = M.tc_overlap_checker(window=(T0 - dt.timedelta(days=2),
                                         tr.times[-1] + dt.timedelta(days=2)),
                                 loader=_stub(_fixes(rows)))
    assert check(tr) is None


def test_checker_fails_soft_when_the_source_is_gone():
    def boom(start, end):
        raise OSError("no archive")
    tr = _track()
    check = M.tc_overlap_checker(window=(T0, tr.times[-1]), loader=boom)
    assert check(tr) is None          # logged once, lane survives


def test_scan_wires_the_screen_into_the_funnel():
    """A stub overlap screen that rejects everything removes every event and
    the rejection shows up in lane_status.screened_by (scan-level plumbing)."""
    lats = np.arange(-80.0, 80.0 + 1e-9, 0.5)
    lons = np.arange(-180.0, 180.0, 0.5)
    times = [T0 + dt.timedelta(hours=6 * i) for i in range(24)]
    mslp = np.empty((24, lats.size, lons.size), np.float32)
    for k in range(24):
        f = np.full((lats.size, lons.size), 1013.0)
        d = T.haversine_km(lats[:, None], lons[None, :], 50.0 + 0.5 * k,
                           -40.0 + 4.0 * k) / 111.195
        mslp[k] = f - (20.0 + 5.0 * min(k, 6)) * np.exp(-((d / 2.0) ** 2) / 2.0)
    kw = dict(fields=(times, mslp, lats, lons),
              orography=(np.zeros((0, 0), np.float32), np.zeros(0), np.zeros(0)),
              tropical_check=lambda la, lo, t: False)
    plain = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=5)).date(), **kw)
    assert len(plain) > 0, "fixture must produce at least one event"
    evs = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=5)).date(),
                            overlap_check=lambda tr: "STUB", **kw)
    assert evs == []
    st = M.lane_status()
    assert any("same system as tracked TC" in k for k in st["screened_by"])


# ------------------------------------------------------------- F2: severity
def _ev(min_mslp, lifetime_h, bomb=False):
    return Event(id="x", event_type="midlat_cyclone", title="t",
                 start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 3),
                 bbox=(0.0, 1.0, 0.0, 1.0), peak_value=min_mslp,
                 details={"min_mslp_hPa": min_mslp, "lifetime_h": lifetime_h,
                          "bomb": bomb})


def test_depth_beats_lifetime_in_the_quota_ranking():
    monster_2d = _ev(954.3, 48.0)          # the deepest dev quota-drop (F2)
    wanderer_10d = _ev(980.0, 240.0)       # shallow/long-lived (also dropped
    # by the old product — the point is depth must never lose to lifetime)
    assert M.severity(monster_2d) > M.severity(wanderer_10d)
    # ...and every dropped >=961 hPa low outranks a shallow long-lived profile
    weakest_kept = _ev(989.0, 240.0)
    for deep in (954.3, 959.9, 960.7, 965.0, 970.0, 975.0):
        assert M.severity(_ev(deep, 48.0)) > M.severity(weakest_kept)


def test_lifetime_still_orders_equal_depth_and_bombs_get_the_nudge():
    assert M.severity(_ev(960.0, 120.0)) > M.severity(_ev(960.0, 48.0))
    assert M.severity(_ev(960.0, 48.0, bomb=True)) > M.severity(_ev(960.0, 48.0))
    # days are clamped, so a 30-day shallow low cannot resurrect
    assert M.severity(_ev(985.0, 720.0)) == M.severity(_ev(985.0, 168.0))
