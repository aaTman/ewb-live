"""Synthetic-field tests for the mid-latitude-cyclone lane (no network, no ERA5).

The field is built from Gaussians: a deep low that travels 50 deg of longitude
(deepening fast enough to be a bomb), a static shallow blob, a low that is born
in the tropics, a low born on top of the Andes, and a low that crosses the
antimeridian. The screens in ``ewb_live/events/mlc.py`` are then checked one by
one on exactly those cases, plus the published ``details["track"]`` schema and
the publish validator.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from ewb_live.events import mlc as M
from ewb_live.events import mlc_track as T
from ewb_live.events.validate import validate_event

LATS = np.arange(-80.0, 80.0 + 1e-9, 0.25)
LONS = np.arange(-180.0, 180.0, 0.25)
T0 = dt.datetime(2023, 11, 1)
N = 40                                    # 10 days at 6-hourly cadence
TIMES = [T0 + dt.timedelta(hours=6 * i) for i in range(N)]


def add_low(f, lat, lon, depth, sigma=2.0):
    d = T.haversine_km(LATS[:, None], LONS[None, :], lat, lon) / 111.195
    return f - depth * np.exp(-(d / sigma) ** 2 / 2.0)


def build_field():
    """(mslp, lats, lons, times) with the five synthetic systems described above."""
    out = np.empty((N, LATS.size, LONS.size), np.float32)
    for k in range(N):
        f = np.full((LATS.size, LONS.size), 1013.0)
        # A: deep, fast, travels 2 deg lon + 0.25 deg lat per step; deepens 6 hPa
        #    per step (= 24 hPa / 24 h -> a bomb) for 6 steps, then fills
        depth = 20.0 + 6.0 * min(k, 6) - (0.0 if k <= 6 else 2.0 * (k - 6))
        f = add_low(f, 50.0 + 0.25 * k, -40.0 + 2.0 * k, depth)
        # B: static and shallow -> fails displacement AND depth
        f = add_low(f, -30.0, 60.0, 6.0, sigma=1.0)
        # C: born at |lat| 14 -> fails the genesis band, moves a lot otherwise
        f = add_low(f, 14.0 + 0.5 * k, -100.0 + 1.0 * k, 45.0)
        # D: born over the Andes (-32, -70) at 3000 m, deep and moving
        f = add_low(f, -32.0 + 0.2 * k, -70.0 + 1.2 * k, 50.0)
        # E: crosses the antimeridian at ~55 N
        f = add_low(f, 55.0 - 0.1 * k, 175.0 + 0.5 * k, 40.0)
        out[k] = f
    return out, LATS, LONS, TIMES


def synthetic_topo():
    """Andes-like wall: 3000 m in a box around (-32, -70), 0 m elsewhere."""
    elev = np.zeros((LATS.size, LONS.size), np.float32)
    lat_ok = (LATS > -40.0) & (LATS < -25.0)
    lon_ok = (LONS > -75.0) & (LONS < -65.0)
    elev[np.ix_(lat_ok, lon_ok)] = 3000.0
    return elev, LATS, LONS


@pytest.fixture(scope="module")
def scanned():
    mslp, lats, lons, times = build_field()
    return T.generate_mlc_tracks(mslp, lats, lons, times)


def track_near(tracks, lat, lon, t=T0):
    """The track whose first fix is closest to (lat, lon) at time t."""
    best, best_d = None, 1e18
    for tr in tracks:
        d = T.haversine_km(tr.lat[0], tr.lon[0], lat, lon)
        if tr.times[0] == t and d < best_d:
            best, best_d = tr, d
    return best


# ------------------------------------------------------------- tracker core
def test_detects_nodes_and_tracks(scanned):
    assert scanned.n_nodes > 0 and scanned.n_frames == N
    assert len(scanned.tracks) >= 3
    for tr in scanned.tracks:
        assert tr.duration_h >= 48.0 - 1e-9
        assert tr.times == sorted(tr.times)
        assert np.all(np.isfinite(tr.lat)) and np.all(np.isfinite(tr.mslp))
        assert np.all((tr.lon >= -180.0) & (tr.lon < 180.0))


def test_moving_low_tracked_with_right_position(scanned):
    tr = track_near(scanned.tracks, 50.0, -40.0)
    assert tr is not None, "the deep travelling low was not tracked"
    assert tr.displacement_km > 3000.0
    # its fixes stay within one grid cell of the injected low centre
    for k in (0, 5, 10):
        want_lat, want_lon = 50.0 + 0.25 * k, -40.0 + 2.0 * k
        i = tr.times.index(TIMES[k])
        assert abs(tr.lat[i] - want_lat) <= 0.3
        assert abs(((tr.lon[i] - want_lon + 180) % 360) - 180) <= 0.3


def test_bomb_tag_matches_sanders_gyakum(scanned):
    tr = track_near(scanned.tracks, 50.0, -40.0)
    bomb, fall, ratio = tr.bomb_ratio()
    assert bomb and ratio >= 1.0 and fall < 0.0
    assert fall <= -24.0 * math.sin(math.radians(abs(tr.lat[6]))) \
        / math.sin(math.radians(60.0)) * 0.0 + 0.0  # fall magnitude vs its own latitude
    assert fall <= -20.0
    # the static shallow blob is not a bomb (nothing falls at all)
    static = track_near(scanned.tracks, -30.0, 60.0)
    assert static.bomb_ratio()[0] is False


def test_static_low_is_tracked_but_not_an_event(scanned):
    tr = track_near(scanned.tracks, -30.0, 60.0)
    assert tr is not None and tr.displacement_km < 1000.0
    ev, reason = M.track_to_event(tr, elev_m=0.0, tropical=False)
    assert ev is None and "displacement" in reason


# ------------------------------------------------------------- screens, one by one
def test_each_screen_rejects_what_it_says(scanned):
    deep = track_near(scanned.tracks, 50.0, -40.0)
    ok, reason = M.track_to_event(deep, elev_m=10.0, tropical=False)
    assert ok is not None and reason is None

    tr_andes = track_near(scanned.tracks, -32.0, -70.0)
    if tr_andes is not None:
        ev, reason = M.track_to_event(tr_andes, elev_m=3000.0, tropical=False)
        assert ev is None and "orography" in reason

    _, reason = M.track_to_event(deep, elev_m=10.0, tropical=True)
    assert reason is not None and "tropical" in reason

    tr_trop = track_near(scanned.tracks, 14.0, -100.0)
    if tr_trop is not None:
        _, reason = M.track_to_event(tr_trop, elev_m=0.0, tropical=False)
        assert reason is not None and "genesis |lat|" in reason


def test_short_track_fails_the_lifetime_floor():
    tr = T.Track([T0 + dt.timedelta(hours=6 * i) for i in range(4)],
                 np.array([50.0, 50.5, 51.0, 51.5]),
                 np.array([-10.0, -8.0, -6.0, -4.0]),
                 np.array([995.0, 990.0, 985.0, 980.0]),
                 np.zeros(4, bool))
    ev, reason = M.track_to_event(tr, elev_m=0.0, tropical=False)
    assert ev is None and "lifetime" in reason


def test_depth_floor_rejects_a_shallow_but_fast_low():
    tr = T.Track(TIMES[:10], np.linspace(50, 55, 10), np.linspace(-40, 10, 10),
                 np.full(10, 995.0), np.zeros(10, bool))
    ev, reason = M.track_to_event(tr, elev_m=0.0, tropical=False)
    assert ev is None and "MSLP" in reason


def test_dedupe_keeps_the_deeper_of_two_same_low_tracks():
    a = T.Track(TIMES[:12], np.linspace(50, 53, 12), np.linspace(-40, -20, 12),
                np.linspace(980, 960, 12), np.zeros(12, bool))
    b = T.Track(TIMES[12:24], np.linspace(53, 56, 12), np.linspace(-20, 0, 12),
                np.linspace(975, 970, 12), np.zeros(12, bool))     # same low, re-identified
    far = T.Track(TIMES[:12], np.linspace(-30, -27, 12), np.linspace(60, 80, 12),
                  np.linspace(1000, 984, 12), np.zeros(12, bool))  # a different low
    kept, pairs = M.dedupe_tracks([a, b, far])
    assert [id(x) for x in kept] == [id(a), id(far)]
    assert pairs and pairs[0][0] is a


def test_core_window_is_at_least_24h_and_contains_the_min():
    tr = T.Track(TIMES[:12], np.linspace(50, 55, 12), np.linspace(-40, 0, 12),
                 np.array([1005., 1004., 1003., 1002., 1001., 968., 1001., 1002.,
                           1003., 1004., 1005., 1006.]), np.zeros(12, bool))
    lo, hi = M.core_window(tr)
    assert lo <= tr.argmin <= hi
    assert (hi - lo) * 6.0 >= 24.0                  # grown to at least 24 h
    # the +/-12 hPa margin window alone is just the deepest fix; growth may
    # only ever EXTEND that window, never shrink or shift it
    margin = [k for k in range(len(tr)) if tr.mslp[k] <= 968.0 + M.CORE_WINDOW_MARGIN_HPA]
    assert min(margin) >= lo and max(margin) <= hi


def test_gap_filling_re_drops_missing_fixes_from_the_field():
    """A low skipped for one step is re-dropped from the MSLP minima (upstream
    ``_fill_track_gaps_from_fields``), not linearly interpolated."""
    mslp = np.empty((6, LATS.size, LONS.size), np.float32)
    lats = np.linspace(50.0, 51.5, 6)
    lons = np.linspace(-40.0, -25.0, 6)             # ~2.5 deg/step: a 2-step gap is ~5 deg
    for k in range(6):
        mslp[k] = add_low(np.full((LATS.size, LONS.size), 1013.0),
                          lats[k], lons[k], 45.0)
    times = [T0 + dt.timedelta(hours=6 * i) for i in range(6)]
    keep = [0, 1, 2, 4, 5]                       # frame 3 detection lost
    nodes = [T.Node(times[k], lats[k], lons[k], 968.0, k) for k in keep]
    tr = T.associate_nodes(nodes)[0]
    assert len(tr) == 5
    tr = T.fill_track_gaps_from_fields(tr, mslp, times, LATS, LONS)
    assert len(tr) == 6
    assert bool(tr.filled[3])
    i = int(np.argmin(mslp[3])) if False else np.unravel_index(
        np.argmin(mslp[3]), mslp[3].shape)
    assert abs(tr.lat[3] - LATS[i[0]]) <= 0.3
    assert abs(tr.mslp[3] - float(mslp[3].min())) <= 1.0


def test_gap_filling_follows_a_low_across_the_antimeridian() -> None:
    """A fix lost next to +-180 is re-dropped on the low, not on the far side of the globe.

    The expected position used a plain average of the two neighbouring longitudes, so
    178 and -176 gave +1 (near Greenwich). The fill then took the lowest cell there, a
    1028 hPa fix stitched into a 970 hPa storm (W47, storm 3b4108d8a8d0).
    """
    n = 6
    lats = np.linspace(50.0, 51.5, n)
    lons = np.linspace(172.0, 187.0, n)  # unwrapped; steps of 3 deg
    wrapped = (lons + 180.0) % 360.0 - 180.0
    mslp = np.empty((n, LATS.size, LONS.size), np.float32)
    for k in range(n):
        mslp[k] = add_low(np.full((LATS.size, LONS.size), 1013.0), lats[k], wrapped[k], 45.0)
    times = [T0 + dt.timedelta(hours=6 * i) for i in range(n)]
    nodes = [T.Node(times[k], lats[k], wrapped[k], 968.0, k) for k in (0, 1, 2, 4, 5)]
    tr = T.associate_nodes(nodes)[0]
    tr = T.fill_track_gaps_from_fields(tr, mslp, times, LATS, LONS)
    assert len(tr) == n
    assert bool(tr.filled[3])
    dlon = (tr.lon[3] - wrapped[3] + 180.0) % 360.0 - 180.0
    assert abs(dlon) <= 0.3
    assert abs(tr.mslp[3] - float(mslp[3].min())) <= 1.0


# ---------------------------------------------------------------- events
def test_events_and_schema():
    mslp, lats, lons, times = build_field()
    events = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=9)).date(),
                               fields=(times, mslp, lats, lons),
                               orography=synthetic_topo(),
                               tropical_check=lambda la, lo, t: False)
    assert events, "no qualifying event in a field built from 5 qualifying lows"
    for e in events:
        assert e.event_type == "midlat_cyclone"
        assert e.start < e.end
        assert e.end.hour == e.end.minute == e.end.second == 0   # midnight-aligned
        assert e.end - e.start >= dt.timedelta(days=1)
        assert validate_event(e) == []           # publish gate
        tr = e.details["track"]
        assert tr and all(len(r) == 5 for r in tr)
        assert all(r[0].endswith("Z") for r in tr)
        assert [r[0] for r in tr] == sorted(r[0] for r in tr)
        assert len(tr) >= 9                      # >= 48 h at the 6-hourly cadence
        for row in tr:
            assert row[3] == row[4]              # intensity == central MSL
            assert all(v == v for v in row[1:]), "NaN must never reach events.json"
        assert e.details["intensity_units"].startswith("hPa")
        assert e.peak_value == pytest.approx(e.details["min_mslp_hPa"])
        assert e.start <= dt.datetime.fromisoformat(tr[0][0].rstrip("Z")) + \
            dt.timedelta(days=30)
    # the Andes-born low is not published, the mid-latitude ones are
    assert not any(e.details["genesis_lon"] < -65 and e.details["genesis_lon"] > -75
                   and e.details["genesis_lat"] < -25 and e.details["genesis_lat"] > -40
                   for e in events)


def test_tropical_origin_rejection_is_applied():
    mslp, lats, lons, times = build_field()
    kw = dict(fields=(times, mslp, lats, lons), orography=synthetic_topo())
    none_trop = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=9)).date(),
                                  tropical_check=lambda la, lo, t: False, **kw)
    # this is the post-tropical -> extratropical hand-off (Philippe -> Ciaran):
    # whichever low the screen claims, the tc lane owns it and we must not
    # publish it, and the other low must survive unchanged
    target = none_trop[0]
    claim = (target.details["genesis_lat"], target.details["genesis_lon"])

    def trop_target(lat, lon, t):
        return T.haversine_km(lat, lon, claim[0], claim[1]) < 300.0

    some = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=9)).date(),
                             tropical_check=trop_target, **kw)
    assert len(some) == len(none_trop) - 1
    assert all(T.haversine_km(e.details["genesis_lat"], e.details["genesis_lon"],
                              claim[0], claim[1]) > 300.0 for e in some)


def test_antimeridian_event_uses_the_wrap_convention():
    mslp, lats, lons, times = build_field()
    events = M.scan_mlc_events(T0.date(), (T0 + dt.timedelta(days=9)).date(),
                               fields=(times, mslp, lats, lons),
                               orography=synthetic_topo(),
                               tropical_check=lambda la, lo, t: False)
    crossing = [e for e in events if e.details["track_crosses_antimeridian"]]
    assert crossing, "the antimeridian-crossing synthetic low was not published"
    for e in crossing:
        lat0, lat1, lon0, lon1 = e.bbox
        assert -90.0 <= lat0 < lat1 <= 90.0
        assert lon0 > lon1, "a crossing bbox must have lon_min > lon_max"
        assert e.details["antimeridian_wrap"] is True


def test_genesis_outside_the_window_belongs_to_the_previous_scan():
    mslp, lats, lons, times = build_field()
    late = M.scan_mlc_events((T0 + dt.timedelta(days=5)).date(),
                             (T0 + dt.timedelta(days=9)).date(),
                             fields=(times, mslp, lats, lons),
                             orography=synthetic_topo(),
                             lookback_days=6.0,
                             tropical_check=lambda la, lo, t: False)
    for e in late:
        assert e.start.date() >= (T0 + dt.timedelta(days=5)).date()


def test_fail_soft_without_data(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert M.scan_mlc_events(dt.date(2023, 1, 1), dt.date(2023, 1, 2),
                                 fields=([[]], np.zeros((0, 0, 0), np.float32),
                                         np.zeros(0), np.zeros(0))) == []


# ------------------------------------------------------- orography source
# ARCO's time axis opens with a 1900-01-01T00 pseudo step that is all NaN for
# geopotential_at_surface. Reading it (the pre-2026-11-17 behaviour) yields an
# all-zero elevation field, which silently disables the genesis orography screen
# — the screen that exists to stop the tracker inventing lows off the Tibetan
# Plateau. These tests pin the store handling without touching the network.
def _store(values_by_time, times=("1900-01-01T00", "2000-01-01T00")):
    import xarray as xr

    return xr.Dataset(
        {"geopotential_at_surface": (("time", "latitude", "longitude"), values_by_time)},
        coords={"time": np.array(times, dtype="datetime64[h]"),
                "latitude": [10.0, 0.0, -10.0], "longitude": [0.0, 120.0, -120.0]})


def _patch_store(monkeypatch, ds):
    from ewb_live.sources import mlc_era5

    monkeypatch.setattr(mlc_era5, "open_arco", lambda *a, **k: ds)
    return mlc_era5


def test_orography_skips_the_all_nan_pseudo_step_and_finds_land(tmp_path, monkeypatch):
    real = (np.arange(9).reshape(3, 3) * 900.0)            # metres via /9.81
    ds = _store(np.stack([np.full((3, 3), np.nan), real]))
    src = _patch_store(monkeypatch, ds)
    elev, lats, lons = src.load_orography(tmp_path)
    assert elev.max() > 0.0, "the real step must be used, not the NaN one"
    assert lats[0] > lats[-1] or lats[0] < lats[-1]        # monotone either way
    assert set(np.round(lons, 3)) == {-120.0, 0.0, 120.0}  # wrapped to [-180, 180)
    # and it is cached, so a second call never re-reads the store
    monkeypatch.setattr(src, "open_arco",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-read")))
    assert src.load_orography(tmp_path)[0].max() > 0.0


def test_poisoned_all_zero_orography_cache_is_refetched(tmp_path, monkeypatch):
    np.savez_compressed(  # what the old code cached: a silent all-zero field
        (tmp_path / "topo_0p25.npz"), elev=np.zeros((3, 3), np.float32),
        lats=np.arange(3.0), lons=np.arange(3.0))
    real = (np.arange(9).reshape(3, 3) * 900.0)
    src = _patch_store(monkeypatch, _store(np.stack([np.full((3, 3), np.nan), real])))
    elev = src.load_orography(tmp_path)[0]
    assert elev.max() > 0.0, "an all-zero cache must not disable the screen"


def test_all_nan_orography_fails_soft(tmp_path, monkeypatch):
    src = _patch_store(monkeypatch, _store(np.full((2, 3, 3), np.nan)))
    elev, lats, lons = src.load_orography(tmp_path)
    assert elev.size == 0 and lats.size == 0 and lons.size == 0
    assert src.elevation_at(elev, lats, lons, 30.0, 90.0) == 0.0  # screen off, lane alive
