"""Shared track core (ewb_live/scoring/track_verif.py): geometry, tracker, matching, rows."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from ewb_live.scoring import track_verif as tv

D1 = 111.195                           # km per degree (R = 6371 km)
T0 = dt.datetime(2026, 9, 1, 0)


def _times(n, step_h=6, t0=T0):
    return [t0 + dt.timedelta(hours=step_h * i) for i in range(n)]


# ------------------------------------------------------------------ geometry --
def test_great_circle_and_bearing_basics():
    assert tv.great_circle(0, 0, 1, 0) == pytest.approx(D1, rel=1e-3)
    assert tv.bearing(0, 0, 1, 0) == pytest.approx(0.0)
    assert tv.bearing(0, 0, 0, 1) == pytest.approx(90.0)
    assert tv.bearing(0, 0, -1, 0) == pytest.approx(180.0)
    assert abs(tv.bearing(0, 0, 0, -1)) == pytest.approx(90.0)


@pytest.mark.parametrize("fc,expect", [
    ((1.0, 0.0), (+D1, 0.0)),          # straight ahead of a north-moving storm
    ((-1.0, 0.0), (-D1, 0.0)),         # straight behind
    ((0.0, 1.0), (0.0, +D1)),          # 90 deg clockwise from the motion = right
    ((0.0, -1.0), (0.0, -D1)),         # left
])
def test_along_cross_hand_cases_northbound(fc, expect):
    d, a, c = tv.along_cross(0.0, 0.0, fc[0], fc[1], heading_deg=0.0, speed_kmh=30.0)
    assert d == pytest.approx(D1, rel=1e-3)
    assert a == pytest.approx(expect[0], abs=0.2)
    assert c == pytest.approx(expect[1], abs=0.2)


def test_along_cross_rotates_with_the_motion():
    # eastbound storm: north of it is LEFT, east is AHEAD
    _, a, c = tv.along_cross(0.0, 0.0, 1.0, 0.0, heading_deg=90.0, speed_kmh=30.0)
    assert (a, c) == (pytest.approx(0.0, abs=0.2), pytest.approx(-D1, abs=0.2))
    _, a, c = tv.along_cross(0.0, 0.0, 0.0, 1.0, heading_deg=90.0, speed_kmh=30.0)
    assert (a, c) == (pytest.approx(D1, abs=0.2), pytest.approx(0.0, abs=0.2))
    # southbound: west is RIGHT
    _, a, c = tv.along_cross(0.0, 0.0, 0.0, -1.0, heading_deg=180.0, speed_kmh=30.0)
    assert (a, c) == (pytest.approx(0.0, abs=0.2), pytest.approx(D1, abs=0.2))


def test_dateline_crossing_forecast_is_ahead():
    # eastbound at 179.5E, forecast 1 deg further east = -179.5E: ahead by ~111 km, not 39,900 km
    d, a, c = tv.along_cross(0.0, 179.5, 0.0, -179.5, heading_deg=90.0, speed_kmh=25.0)
    assert d == pytest.approx(D1, rel=1e-3) and a == pytest.approx(D1, rel=1e-3) and c == pytest.approx(0, abs=0.3)


def test_high_latitude_decomposition_keeps_pythagoras():
    d, a, c = tv.along_cross(78.0, 10.0, 79.0, 14.0, heading_deg=60.0, speed_kmh=20.0)
    assert a ** 2 + c ** 2 == pytest.approx(d ** 2, rel=1e-9)
    assert d == pytest.approx(float(tv.great_circle(78.0, 10.0, 79.0, 14.0)))


def test_stationary_storm_keeps_position_error_drops_along_cross():
    d, a, c = tv.along_cross(10.0, 20.0, 11.0, 20.0, heading_deg=0.0, speed_kmh=1.99)
    assert d == pytest.approx(D1, rel=1e-3) and np.isnan(a) and np.isnan(c)
    d, a, c = tv.along_cross(10.0, 20.0, 11.0, 20.0, heading_deg=float("nan"), speed_kmh=float("nan"))
    assert d > 0 and np.isnan(a) and np.isnan(c)
    d, a, c = tv.along_cross(10.0, 20.0, 11.0, 20.0, heading_deg=0.0, speed_kmh=2.0)   # cutoff itself: defined
    assert np.isfinite(a)


def test_along_cross_pythagoras_property():
    rng = np.random.default_rng(7)
    for _ in range(200):
        la0, lo0 = rng.uniform(-70, 70), rng.uniform(-180, 180)
        la1, lo1 = la0 + rng.uniform(-5, 5), lo0 + rng.uniform(-5, 5)
        hd, sp = rng.uniform(-180, 180), rng.uniform(3, 60)
        d, a, c = tv.along_cross(la0, lo0, la1, lo1, hd, sp)
        assert a ** 2 + c ** 2 == pytest.approx(d ** 2, rel=1e-7, abs=1e-6)


def test_observed_motion_centred_and_one_sided():
    t = _times(5)
    lat = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    lon = np.zeros(5)
    for k in range(5):                              # northbound 1 deg / 6 h everywhere
        hd, sp = tv.observed_motion(t, lat, lon, k)
        assert hd == pytest.approx(0.0, abs=1e-6)
        assert sp == pytest.approx(D1 / 6.0, rel=1e-3)
    # centred difference ignores the fix itself: a kink at k is smoothed by (k-1, k+1)
    lat2 = np.array([0.0, 1.0, 1.0, 3.0, 4.0])
    hd, sp = tv.observed_motion(t, lat2, lon, 2)
    assert sp == pytest.approx(2 * D1 / 12.0, rel=1e-3)


def test_observed_motion_uses_3h_neighbour_when_6h_missing_and_is_nan_when_alone():
    t = [T0, T0 + dt.timedelta(hours=3)]
    hd, sp = tv.observed_motion(t, [0.0, 0.5], [0.0, 0.0], 0)
    assert hd == pytest.approx(0.0) and sp == pytest.approx(0.5 * D1 / 3.0, rel=1e-3)
    hd, sp = tv.observed_motion([T0], [0.0], [0.0], 0)
    assert np.isnan(hd) and np.isnan(sp)
    # neighbours 9 h away are not usable
    hd, sp = tv.observed_motion([T0, T0 + dt.timedelta(hours=9)], [0.0, 1.0], [0.0, 0.0], 0)
    assert np.isnan(hd)


def test_observed_motion_across_the_dateline():
    t = _times(3)
    lon = np.array([179.0, 180.0, -179.0])
    hd, sp = tv.observed_motion(t, [0.0, 0.0, 0.0], tv.wrap_deg(lon), 1)
    assert hd == pytest.approx(90.0, abs=1e-6) and sp == pytest.approx(D1 / 6.0, rel=1e-3)


# ------------------------------------------------------------------- tracker --
LAT = np.arange(0.0, 40.0 + 0.25, 0.25)
LON = np.arange(-120.0, -80.0 + 0.25, 0.25)


def _low(lat0, lon0, depth=30.0, sigma=2.5):
    yy, xx = np.meshgrid(LAT, LON, indexing="ij")
    d2 = (yy - lat0) ** 2 + ((xx - lon0) * np.cos(np.radians(lat0))) ** 2
    return -depth * np.exp(-d2 / (2 * sigma ** 2))


def _stack(centres, base=1012.0):
    out = []
    for lows in centres:
        f = np.full((LAT.size, LON.size), base)
        for low in lows:
            f = f + _low(*low)
        out.append(f)
    return np.array(out)


def _path(n, lat0=10.0, lon0=-110.0, dlat=1.0, dlon=1.2):
    return [(lat0 + i * dlat, lon0 + i * dlon) for i in range(n)]


def test_tracker_follows_a_moving_low_and_reports_its_pressure():
    path = _path(10)
    la, lo, p, ok, nod = tv.follow_track(_stack([[pt] for pt in path]), LAT, LON, *path[0], tv.TC_PARAMS)
    assert ok.all() and not nod.any()
    assert np.allclose(la, [x[0] for x in path], atol=0.5) and np.allclose(lo, [x[1] for x in path], atol=0.5)
    assert np.allclose(p, 1012.0 - 30.0, atol=1.0)


def test_tracker_loses_a_vanished_low_and_stays_lost():
    path = _path(10)
    stack = _stack([[pt] if i < 5 else [] for i, pt in enumerate(path)])
    la, lo, p, ok, nod = tv.follow_track(stack, LAT, LON, *path[0], tv.TC_PARAMS)
    assert ok[:5].all() and not ok[5:].any()
    assert np.isnan(la[5:]).all() and np.isnan(p[5:]).all()
    # a low that reappears later must NOT resurrect a lost track
    stack2 = _stack([[pt] if (i < 5 or i == 8) else [] for i, pt in enumerate(path)])
    assert not tv.follow_track(stack2, LAT, LON, *path[0], tv.TC_PARAMS)[3][5:].any()


def test_one_missing_step_is_bridged_by_persistence():
    path = _path(8)
    stack = _stack([[pt] if i != 4 else [] for i, pt in enumerate(path)])
    _, _, _, ok, _ = tv.follow_track(stack, LAT, LON, *path[0], tv.TC_PARAMS)
    assert ok.tolist() == [True] * 4 + [False] + [True] * 3


def test_second_low_does_not_capture_the_track():
    path = _path(8)
    other = [(path[i][0] + 6.0, path[i][1] + 14.0, 45.0) for i in range(8)]     # deeper, 14 deg away
    la, lo, p, ok, _ = tv.follow_track(_stack([[path[i], other[i]] for i in range(8)]), LAT, LON,
                                       *path[0], tv.TC_PARAMS)
    assert ok.all() and np.allclose(la, [x[0] for x in path], atol=0.6)


def test_all_fill_step_is_nodata_not_a_miss_and_does_not_lose_the_track():
    path = _path(6)
    stack = _stack([[pt] for pt in path])
    stack[0] = np.nan                                   # GraphCast lead 0
    stack[3] = np.nan
    _, _, _, ok, nod = tv.follow_track(stack, LAT, LON, *path[0], tv.TC_PARAMS)
    assert nod.tolist() == [True, False, False, True, False, False]
    assert ok.tolist() == [False, True, True, False, True, True]


# ------------------------------------------------------------------ matching --
def _track(n_fixes=40, step_h=3, t0=T0, wind0=20.0):
    t = _times(n_fixes, step_h, t0)
    lat = np.linspace(10.0, 20.0, n_fixes)
    return tv.ObservedTrack(t, lat, np.linspace(-120.0, -100.0, n_fixes), np.full(n_fixes, 990.0),
                            np.linspace(wind0, 80.0, n_fixes))


def test_verifiable_inits_are_00_12z_with_a_fix_and_a_following_fix():
    tr = _track(40, t0=dt.datetime(2026, 9, 1, 6))      # fixes 09-01 06Z .. 09-06 03Z, every 3 h
    ok, ex = tv.verifiable_inits(tr)
    assert ex == []
    assert ok[0] == dt.datetime(2026, 9, 1, 12) and ok[-1] == dt.datetime(2026, 9, 5, 12)
    assert all(i.hour in (0, 12) for i in ok)
    # the last fix is 09-06 03Z: the 00Z init of 09-06 has a fix but no fix 6 h later
    assert dt.datetime(2026, 9, 6, 0) not in ok


def test_pre_start_inits_are_excluded_and_reported_not_verified():
    tr = _track(40, t0=T0)
    start = dt.datetime(2026, 9, 2, 6)
    ok, ex = tv.verifiable_inits(tr, start)
    assert ex == [dt.datetime(2026, 9, 1, 0), dt.datetime(2026, 9, 1, 12), dt.datetime(2026, 9, 2, 0)]
    assert ok[0] == dt.datetime(2026, 9, 2, 12)
    assert not set(ok) & set(ex)


def test_leads_at_only_where_a_fix_exists_and_never_past_240h():
    tr = _track(20, 6)                                  # 114 h of fixes
    assert tv.leads_at(tr, T0) == list(range(0, 115, 6))
    long = _track(100, 6)
    assert tv.leads_at(long, T0)[-1] == 240


def test_verify_init_nearest_earlier_init_records_true_init_and_lead():
    tr = _track(30, 6)
    init = T0 + dt.timedelta(hours=12)
    leads = [0, 6, 12]
    n = len(leads)
    res = tv.verify_init(tr, init, leads, 12, np.full(n, 11.0), np.full(n, -118.0), np.full(n, 995.0),
                         np.ones(n, bool), np.zeros(n, bool))
    assert [r["lead_true"] for r in res] == [18.0, 24.0]               # nominal 6, 12 + 12 h offset
    assert all(r["init_true"] == T0.isoformat() for r in res)          # true init = 12 h earlier
    # init_true + lead_true is still the valid time the observed fix belongs to
    assert dt.datetime.fromisoformat(res[0]["init_true"]) + dt.timedelta(hours=res[0]["lead_true"]) \
        == init + dt.timedelta(hours=6)


def test_verify_init_skips_nodata_and_lead0_and_marks_misses():
    tr = _track(30, 6)
    leads = [0, 6, 12, 18]
    ok = np.array([False, True, False, False])
    nod = np.array([True, False, False, True])
    res = tv.verify_init(tr, T0, leads, 0, np.full(4, 11.0), np.full(4, -118.0), np.full(4, 995.0), ok, nod)
    assert [(r["lead_true"], r["tracked"]) for r in res] == [(6.0, True), (12.0, False)]
    assert res[0]["d_km"] > 0 and res[0]["dp_hpa"] == pytest.approx(5.0)


# ---------------------------------------------------------------------- rows --
def test_rows_for_pair_contract_and_miss_rows():
    samples = [
        {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0, "tracked": True, "d_km": 50.0,
         "along_km": -30.0, "cross_km": 40.0, "dp_hpa": 6.0},
        {"init_true": "2026-09-01T00:00:00", "lead_true": 48.0, "tracked": False},
        {"init_true": "2026-09-01T00:00:00", "lead_true": 72.0, "tracked": True, "d_km": 90.0,
         "along_km": None, "cross_km": None, "dp_hpa": None},        # stationary + no pressure
    ]
    df = tv.rows_for_pair("ev1", "tropical_cyclone", "graphcast", samples)
    assert list(df.columns) == tv.SCORE_COLUMNS
    assert df["lead_time_hours"].dtype == "float64" and df["value"].dtype == "float64"
    assert df["init_time"].dtype == "datetime64[ns]"
    got = {(r.metric, r.lead_time_hours): r.value for r in df.itertuples()}
    assert got == {
        ("TrackPositionError", 24.0): 50.0, ("AlongTrackError", 24.0): -30.0,
        ("CrossTrackError", 24.0): 40.0, ("CentralPressureError", 24.0): 6.0, ("TrackMiss", 24.0): 0.0,
        ("TrackMiss", 48.0): 1.0,                                    # a miss: ONLY the miss row
        ("TrackPositionError", 72.0): 90.0, ("TrackMiss", 72.0): 0.0,
    }


def test_rows_for_pair_dedupes_a_rescued_init_that_coincides_with_a_nominal_one():
    s = {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0, "tracked": True, "d_km": 1.0,
         "along_km": None, "cross_km": None, "dp_hpa": None}
    assert len(tv.rows_for_pair("e", "tropical_cyclone", "m", [s, dict(s)])) == 2     # pos + miss once
    assert tv.rows_for_pair("e", "tropical_cyclone", "m", []).empty


def test_scorecard_gives_skill_only_for_position_error_and_counts_misses():
    from ewb_live.pipeline import build_scorecard

    def pair(model, scale):
        samples = []
        for ev in ("a", "b", "c"):
            for lead in (24.0, 48.0):
                samples.append((ev, {"init_true": "2026-09-01T00:00:00", "lead_true": lead, "tracked": True,
                                     "d_km": 100.0 * scale, "along_km": 10.0 * scale,
                                     "cross_km": -5.0 * scale, "dp_hpa": 3.0 * scale}))
        samples.append(("a", {"init_true": "2026-09-01T12:00:00", "lead_true": 24.0, "tracked": False}))
        frames = [tv.rows_for_pair(ev, "tropical_cyclone", model, [s]) for ev, s in samples]
        return pd.concat(frames, ignore_index=True)

    sc = build_scorecard(pd.concat([pair("ifs_hres", 1.0), pair("graphcast", 0.8)], ignore_index=True))
    rows = {(r["metric"], r["lead_time_hours"], r["model"]): r for r in sc["rows"]}
    gc = rows[("TrackPositionError", 24.0, "graphcast")]
    assert gc["skill_vs_ifs_pct"] == pytest.approx(20.0, abs=0.01) and gc["n_events"] == 3
    for m in ("AlongTrackError", "CrossTrackError", "CentralPressureError"):
        assert rows[(m, 24.0, "graphcast")]["skill_vs_ifs_pct"] is None        # signed: suppressed
        assert rows[(m, 24.0, "ifs_hres")]["skill_vs_ifs_pct"] is None
    miss = rows[("TrackMiss", 24.0, "graphcast")]
    assert miss["n_samples"] == 4 and miss["mean"] == pytest.approx(0.25)       # the miss row is counted
    assert rows[("TrackPositionError", 24.0, "graphcast")]["n_samples"] == 3    # n(error) vs n(miss): 3 of 4 tracked


# ------------------------------------------- the About listing and the page copy
def _listing():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "dashboard" / "track_scoring.py"
    spec = importlib.util.spec_from_file_location("track_scoring_listing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_about_listing_agrees_with_the_scorer():
    L = _listing()
    rng = np.random.default_rng(11)
    for _ in range(300):
        la0, lo0 = rng.uniform(-75, 75), rng.uniform(-180, 180)
        la1, lo1 = la0 + rng.uniform(-6, 6), lo0 + rng.uniform(-6, 6)
        hd, sp = rng.uniform(-180, 180), rng.uniform(0.0, 50.0)
        d, a, c = tv.along_cross(la0, lo0, la1, lo1, hd, sp)
        ld, la_, lc = L.along_cross((la0, lo0), (la1, lo1), hd, sp)
        assert ld == pytest.approx(d, rel=1e-9)
        if sp < tv.STATIONARY_KMH:
            assert la_ is None and np.isnan(a)
        else:
            assert la_ == pytest.approx(a, abs=1e-6) and lc == pytest.approx(c, abs=1e-6)
    assert L.STATIONARY_KMH == tv.STATIONARY_KMH and L.R_EARTH_KM == tv.R_EARTH_KM


def test_page_copy_matches_the_rules():
    from pathlib import Path

    from ewb_live.scoring import tc

    root = Path(__file__).resolve().parents[1] / "dashboard"
    html = (root / "index.html").read_text()
    js = (root / "app.js").read_text()
    assert f"{tc.MIN_WIND_KT:.0f}&nbsp;kt" in html and "ahead" in html and "right" in html
    assert "2&nbsp;km/h" in html and tv.STATIONARY_KMH == 2.0
    assert "tropical_cyclone:   { label" in js
    tc_meta = js[js.index("tropical_cyclone:   { label"):js.index("flood:              { label")]
    assert "noScores" not in tc_meta                       # the lane stops showing 'Not scored'
    assert "tropical_cyclone: [24, 48, 72, 96, 120]" in js  # D8 lead columns
    assert "MISS_GREY = 0.5" in js
