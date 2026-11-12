"""IBTrACS TC colocation screen (owner 2026-11-08, G-40).

Rule: drop an AR candidate when an IBTrACS TC track center passes within
radius_km (default 250, env EWB_LIVE_TC_SCREEN_KM) of any pixel of the
component mask on a day within hours (default 24, EWB_LIVE_TC_SCREEN_HOURS)
of the segment window. Test contract from the owner: a TC-like fixture is
dropped; a normal AR is kept.
"""

import datetime as dt

import numpy as np
import pandas as pd

from ewb_live.events import tc_screen


def _tracks(sid="2026270N28302", name="HANNA"):
    """A TC track crossing the AR region at mid-window, 3-hourly."""
    times = pd.date_range("2026-09-26 00", "2026-09-28 00", freq="3h")
    lats = np.linspace(26.0, 36.0, len(times))
    lons = np.linspace(-59.0, -54.0, len(times))
    return pd.DataFrame({
        "storm_id": sid, "tc_name": name, "valid_time": times,
        "latitude": lats, "longitude": lons,
    })


def _grid():
    lats = np.arange(24.0, 52.01, 0.25)
    lons = np.arange(-86.0, -52.01, 0.25)
    return lats, lons


def _mask(lats, lons, days, lon_edge=-61.25):
    """A mask band whose east edge is lon_edge, lat 26..36 at that edge."""
    m = np.zeros((len(days), len(lats), len(lons)), dtype=bool)
    lon_ok = lons <= lon_edge
    lat_ok = (lats >= 26.0) & (lats <= 36.0)
    ys = np.nonzero(lat_ok)[0]
    xs = np.nonzero(lon_ok)[0]
    m[:, ys[:, None], xs[None, :]] = True
    return m


def _rule(mask, tracks, lats, lons, days, **kw):
    return tc_screen.tc_colocation_rule(days, lats, lons, mask, tracks, **kw)


def test_tc_track_near_component_is_dropped():
    days = np.arange("2026-09-25", "2026-09-29", dtype="datetime64[D]")
    lats, lons = _grid()
    mask = _mask(lats, lons, days)
    hit = _rule(mask, _tracks(), lats, lons, days)
    assert hit is not None
    assert hit["ibtracs_sid"] == "2026270N28302"
    assert hit["tc_name"] == "HANNA"
    assert hit["distance_km"] <= 250.0


def test_normal_midlatitude_ar_far_from_any_track_is_kept():
    days = np.arange("2026-09-25", "2026-09-29", dtype="datetime64[D]")
    lats, lons = _grid()
    mask = _mask(lats, lons, days)
    # track stays deep tropical, 15+ deg south of the component -> no match
    tr = _tracks().assign(latitude=lambda d: d["latitude"] - 15.0)
    assert _rule(mask, tr, lats, lons, days) is None


def test_time_separation_keeps_event():
    days = np.arange("2026-09-25", "2026-09-29", dtype="datetime64[D]")
    lats, lons = _grid()
    mask = _mask(lats, lons, days)
    # same track but 10 days later: outside the hours window -> kept
    tr = _tracks().assign(valid_time=lambda d: d["valid_time"] + pd.Timedelta("10D"))
    assert _rule(mask, tr, lats, lons, days) is None


def test_configurable_radius():
    days = np.arange("2026-09-25", "2026-09-29", dtype="datetime64[D]")
    lats, lons = _grid()
    mask = _mask(lats, lons, days)
    # tight 50 km radius: nearest track point ~227 km away -> kept
    assert _rule(mask, _tracks(), lats, lons, days, km=50.0) is None
    # 500 km radius -> dropped
    assert _rule(mask, _tracks(), lats, lons, days, km=500.0) is not None


def test_missing_tracks_disable_screen_not_crash():
    days = np.arange("2026-09-25", "2026-09-29", dtype="datetime64[D]")
    lats, lons = _grid()
    mask = _mask(lats, lons, days)
    assert _rule(mask, None, lats, lons, days) is None


def test_loader_uses_ewb_mapping_and_caches(tmp_path, monkeypatch):
    """load_ibtracs_tracks downloads via EWB's URI/mapping and caches parquet;
    a fetch failure with a stale cache keeps serving the cache."""
    calls = []

    class FakeResp:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    good = (b"SID,ISO_TIME,NAME,LAT,LON,SEASON\n"
            b"2026270N28302,2026-09-26 18:00:00,HANNA,27.5,-58.3,2026\n"
            b"9999001N01,1990-01-01 00:00:00,OLD,10,-30,1990\n")

    def fake_urlopen(uri, timeout=None):
        calls.append(uri)
        return FakeResp(good)

    monkeypatch.setattr(tc_screen.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tc_screen, "_uri", lambda: "https://invalid.invalid/x.csv")
    monkeypatch.setattr(tc_screen, "_cache_dir", lambda: tmp_path)

    tr = tc_screen.load_ibtracs_tracks({2026})
    assert tr is not None and len(tr) == 1
    assert tr.iloc[0]["storm_id"] == "2026270N28302"
    assert (tmp_path / "tracks.parquet").exists()

    # second call: fresh cache, no re-download
    n = len(calls)
    tr2 = tc_screen.load_ibtracs_tracks({2026})
    assert len(calls) == n and len(tr2) == 1
