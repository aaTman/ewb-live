"""EWB landfall classes on our TC tracks (``scoring/landfall.py``). Offline, synthetic.

The module is pure geometry plus EWB's detector, so the two Natural Earth polygons EWB
would download are replaced by two boxes (fixture ``fake_coastline``): land = lon in
[0, 60], open ocean = lon in [-60, 0), both lat in [-40, 40]. With that coast standing in
for a continent, a storm sailing due east along lat 10 makes exactly ONE observed
landfall, on the prime meridian, and every number the module reports is hand-computable.

Storm used throughout (``_event`` / ``_fc_track``)::

    observed  3-hourly fixes from 2026-09-01T00Z at lat 10, lon -12 + 1 deg / 6 h, 960 hPa,
              running to +96 h so the lon-0 crossing at +72 h (``LF``) is inside the track
              (a 60 h storm never reaches the coast: 12 deg at 1 deg/6 h is 72 h of fixes)
    forecast  dict {init_true, lead_true, lat, lon, p}: 6-hourly leads, p in hPa, NaN
              lat/lon where the tracker lost the low. A forecast from init ``i`` starting at
              lon ``l`` and moving ``r`` deg/6 h crosses the coast ``-l / r * 6`` h after ``i``.

Needs ``extremeweatherbench`` (import-skipped elsewhere).
"""
from __future__ import annotations

import datetime as dt
import types

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("extremeweatherbench")        # the landfall lane runs where EWB is installed

from shapely.geometry import box  # noqa: E402

from ewb_live.scoring import landfall, tc, track_verif as tv  # noqa: E402

T0 = dt.datetime(2026, 9, 1, 0)         # first observed fix
LAT = 10.0
LON0 = -12.0                            # first observed fix longitude (in the open ocean)
DEG_PER_6H = 1.0                        # observed storm speed
OBS_H = 96                              # observed track length in hours (3-hourly fixes)
LF_H = int(round((0.0 - LON0) / DEG_PER_6H * 6))            # 72 h: first fix -> the coast
LF = T0 + dt.timedelta(hours=LF_H)      # the storm's one observed landfall
KM_PER_DEG_LAT = 6371.0 * np.pi / 180.0                     # EWB's haversine radius, 1 deg of latitude

LAND = box(0.0, -40.0, 60.0, 40.0)      # our fake continent: everything east of lon 0
OCEAN = box(-60.0, -40.0, 0.0, 40.0)    # its fake open ocean: everything west of lon 0

MODEL = "graphcast"
NAN = float("nan")


@pytest.fixture(autouse=True)
def fake_coastline(monkeypatch):
    """Box geometries instead of Natural Earth, with the module's geometry cache emptied."""
    from extremeweatherbench import utils as ewb_utils

    monkeypatch.setattr(ewb_utils, "load_land_geometry", lambda *a, **k: LAND)
    monkeypatch.setattr(ewb_utils, "load_ocean_geometry", lambda *a, **k: OCEAN)
    landfall._geoms.cache_clear()
    yield
    landfall._geoms.cache_clear()       # never leak the boxes into another module's test


# ---------------------------------------------------------------------------- builders --
def _observed_rows(deg_per_6h: float = DEG_PER_6H, lat: float = LAT, pres: float = 960.0):
    """IBTrACS-style ``[iso, lat, lon, wind_kt, pres_hPa]`` fixes every 3 h from T0."""
    rows = []
    for i in range(OBS_H // 3 + 1):
        h = 3.0 * i
        t = (T0 + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append([t, lat, LON0 + deg_per_6h * h / 6.0, 45.0, pres])
    return rows


def _event(rows=None, sid="S1", **details):
    """A published TC: the landfall module only reads ``id`` / ``event_type`` / ``details``."""
    details = {"sid": sid, "name": "X", "track": _observed_rows() if rows is None else rows, **details}
    return types.SimpleNamespace(id="syn", event_type="tropical_cyclone", details=details)


def _lon_at(init: dt.datetime) -> float:
    """Observed longitude at ``init`` (where a forecast has to start to be perfectly placed)."""
    return LON0 + DEG_PER_6H * ((init - T0).total_seconds() / 3600.0) / 6.0


def _fc_track(init: dt.datetime, lon_at_init: float, deg_per_6h: float, *, lat: float = LAT,
              pres: float = 950.0, max_lead_h: int = 96, lost: bool = False):
    """Forecast-track dict: due east from (``lat``, ``lon_at_init``) at ``deg_per_6h``, 6-hourly.

    The coast crossing then sits ``-lon_at_init / deg_per_6h * 6`` hours after the init, so a
    test picks (start, speed) to place the crossing anywhere on its 6-hourly lead grid.
    ``lost=True`` is a low the tracker never found: NaN lat/lon/pressure at every lead.
    """
    leads = [float(h) for h in range(6, max_lead_h + 1, 6)]
    lons = [lon_at_init + deg_per_6h * h / 6.0 for h in leads]
    return {"init_true": init.isoformat(), "lead_true": leads,
            "lat": [NAN if lost else lat for _ in leads],
            "lon": [NAN if lost else lo for lo in lons],
            "p": [NAN if lost else pres for _ in leads]}


def _perfect_fc_track(init: dt.datetime, **kw):
    """Forecast that reproduces the observed positions at every valid time (950 hPa, 10 hPa weak)."""
    return _fc_track(init, _lon_at(init), DEG_PER_6H, **kw)


def _value(df, metric):
    """The single value row of ``metric`` (raises when the metric is missing or repeated)."""
    hit = df[df.metric == metric]["value"]
    assert len(hit) == 1, f"expected exactly one {metric} row, got {list(hit)}"
    return float(hit.iloc[0])


def _row(df, metric):
    return df[df.metric == metric].iloc[0]


# ------------------------------------------------------------------- the observed storm --
def test_the_synthetic_storm_has_exactly_one_observed_landfall_on_the_coast():
    """Detector sanity: one ocean->land crossing, exactly at lon 0 / hour 72, nothing after it."""
    tr = tc.observed_track(_event())
    lfs = landfall.observed_landfalls(tr)
    assert lfs.dtype == np.dtype("datetime64[ns]")
    assert len(lfs) == 1 and lfs[0] == np.datetime64(LF, "ns")
    assert tr.lon[tr.index_of(LF)] == pytest.approx(0.0)              # the crossing is a real fix
    assert tr.lon[tr.index_of(LF) + 1] > 0.0                          # ... and it keeps going inland


def test_open_ocean_storm_is_status_none_with_an_empty_frame_not_nan_rows():
    """A storm that never touches land: ``status='none'``, a 0-row frame with the lane columns."""
    ev = _event(rows=_observed_rows(deg_per_6h=0.5))                  # lon -12 -> -4, stays offshore
    tr = tc.observed_track(ev)
    assert len(landfall.observed_landfalls(tr)) == 0
    df, info = landfall.landfall_rows(ev, MODEL, tr, [_perfect_fc_track(T0)])
    assert info["status"] == "none" and info["n_landfalls"] == 0 and info["n_eligible"] == 0
    assert len(df) == 0 and df.empty and list(df.columns) == tv.SCORE_COLUMNS


# ------------------------------------------------------------------------ perfect score --
def test_perfect_forecast_scores_the_one_landfall_with_zero_space_and_time_error():
    """Positions and valid times identical to the observation: 0 km, 0 h, 10 hPa, miss 0, lead 72."""
    ev, tr = _event(), tc.observed_track(_event())
    df, info = landfall.landfall_rows(ev, MODEL, tr, [_perfect_fc_track(T0)])
    assert info["status"] == "scored" and info["n_landfalls"] == 1 and info["n_eligible"] == 1
    assert info["n_paired"] == 1 and "1/1" in info["reason"]
    assert list(df.columns) == tv.SCORE_COLUMNS
    assert set(df.metric) == set(landfall.LANDFALL_METRICS) and len(df) == 4
    assert _value(df, landfall.M_DISP) == pytest.approx(0.0, abs=1e-6)     # same crossing point
    assert _value(df, landfall.M_TIME) == pytest.approx(0.0, abs=1e-6)     # same crossing time
    assert _value(df, landfall.M_INT) == pytest.approx(10.0)               # EWB's 1000 Pa -> hPa
    assert _value(df, landfall.M_MISS) == 0.0
    assert (df.lead_time_hours % 6 == 0).all()
    assert (df.lead_time_hours == float(LF_H)).all()                # hours from init to OBSERVED landfall
    assert (df.init_time == np.datetime64(T0, "ns")).all()
    assert (df.model == MODEL).all() and (df.event_id == "syn").all()
    assert (df.event_type == "tropical_cyclone").all() and df.value.notna().all()


# ------------------------------------------------------------------------- timing sign ---
def test_a_slower_forecast_arrives_later_so_the_time_error_is_positive():
    """0.5 deg/6 h from lon -7 crosses lon 0 at +84 h (observed +72 h): time_me > 0, spot on."""
    ev, tr = _event(), tc.observed_track(_event())
    fc = _fc_track(T0, -7.0, 0.5)                                    # crossing at lead 84 = +12 h late
    df, info = landfall.landfall_rows(ev, MODEL, tr, [fc])
    assert info["n_paired"] == 1
    late_by = _value(df, landfall.M_TIME)
    assert late_by == pytest.approx(12.0, abs=1e-6) and late_by > 0
    assert _value(df, landfall.M_DISP) == pytest.approx(0.0, abs=1e-6)   # same coast, same latitude
    assert _row(df, landfall.M_MISS)["value"] == 0.0


def test_a_faster_forecast_arrives_earlier_so_the_time_error_is_negative():
    """1.5 deg/6 h from lon -15 crosses lon 0 at +60 h: time_me < 0, displacement still ~0."""
    ev, tr = _event(), tc.observed_track(_event())
    fc = _fc_track(T0, -15.0, 1.5)                                   # crossing at lead 60 = -12 h early
    df, info = landfall.landfall_rows(ev, MODEL, tr, [fc])
    assert info["n_paired"] == 1
    early_by = _value(df, landfall.M_TIME)
    assert early_by == pytest.approx(-12.0, abs=1e-6) and early_by < 0
    assert _value(df, landfall.M_DISP) == pytest.approx(0.0, abs=1e-6)


# ----------------------------------------------------------------------- position error --
def test_a_forecast_one_degree_north_lands_at_the_right_time_111_km_away():
    """Same timing, 1 deg of latitude off: displacement ~111 km, time error still 0."""
    ev, tr = _event(), tc.observed_track(_event())
    fc = _perfect_fc_track(T0, lat=LAT + 1.0)
    df, info = landfall.landfall_rows(ev, MODEL, tr, [fc])
    assert info["n_paired"] == 1
    assert _value(df, landfall.M_DISP) == pytest.approx(KM_PER_DEG_LAT, abs=15.0)
    assert _value(df, landfall.M_TIME) == pytest.approx(0.0, abs=1e-6)
    assert _value(df, landfall.M_INT) == pytest.approx(10.0)


# ---------------------------------------------------------------------------- misses --
def test_a_forecast_landing_far_outside_ewb_24h_window_counts_as_a_miss():
    """A pairing EWB refuses is still a miss: 11 h late scores, 42 h late is outside the window.

    ``LandfallMetric(landfall_time_filter=("window", 24.0))`` drops the pair, and the module must
    then report a miss (``landfall_miss=1``, no value rows) rather than drop the init.
    """
    ev, tr = _event(), tc.observed_track(_event())
    inside, i1 = landfall.landfall_rows(ev, MODEL, tr, [_fc_track(T0, -9.7, 0.7)])   # +83 h: 11 h late
    assert i1["n_paired"] == 1 and _value(inside, landfall.M_TIME) == pytest.approx(11.14, abs=0.01)
    # the second forecast crosses lon 0 at +114 h, i.e. 42 h after the observed landfall
    outside, i2 = landfall.landfall_rows(ev, MODEL, tr, [_fc_track(T0, -9.5, 0.5, max_lead_h=144)])
    assert i2["n_eligible"] == 1 and i2["n_paired"] == 0
    assert len(outside) == 1 and list(outside.metric) == [landfall.M_MISS] and outside.value.iloc[0] == 1.0


def test_a_lost_forecast_track_is_one_miss_row_and_no_value_rows():
    """Tracker lost the low (all-NaN track): exactly one ``landfall_miss=1`` row, nothing imputed."""
    ev, tr = _event(), tc.observed_track(_event())
    df, info = landfall.landfall_rows(ev, MODEL, tr, [_perfect_fc_track(T0, lost=True)])
    assert info["status"] == "scored" and info["n_eligible"] == 1 and info["n_paired"] == 0
    assert len(df) == 1 and list(df.metric) == [landfall.M_MISS]
    assert df.value.iloc[0] == 1.0 and df.lead_time_hours.iloc[0] == float(LF_H)
    assert df.value.notna().all()                                    # never a NaN value row
    assert (df.init_time == np.datetime64(T0, "ns")).all()


def test_a_forecast_that_stays_offshore_is_a_miss_too():
    """Forecast never reaches land (lon -12 -> -4 in 96 h): the observed landfall is missed."""
    ev, tr = _event(), tc.observed_track(_event())
    df, info = landfall.landfall_rows(ev, MODEL, tr, [_fc_track(T0, LON0, 0.5)])
    assert info["n_paired"] == 0
    assert len(df) == 1 and list(df.metric) == [landfall.M_MISS] and df.value.iloc[0] == 1.0
    assert df.value.notna().all()


# -------------------------------------------------------------------------- eligibility --
def test_an_init_after_the_observed_landfall_is_never_eligible_and_never_scored():
    """An init 6 h past the landfall has no landfall ahead: no rows at all, ``n_eligible`` 0."""
    ev, tr = _event(), tc.observed_track(_event())
    late = T0 + dt.timedelta(hours=LF_H + 6)
    df_alone, info_alone = landfall.landfall_rows(ev, MODEL, tr, [_perfect_fc_track(late)])
    assert info_alone["status"] == "scored" and info_alone["n_landfalls"] == 1
    assert info_alone["n_eligible"] == 0 and info_alone["n_paired"] == 0
    assert len(df_alone) == 0                                        # not even a miss row
    # mixed with a genuinely eligible init: still nothing for the late one, before or after it
    df, info = landfall.landfall_rows(ev, MODEL, tr,
                                      [_perfect_fc_track(late), _perfect_fc_track(T0),
                                       _perfect_fc_track(T0 + dt.timedelta(hours=12))])
    assert info["n_eligible"] == 2 and info["n_paired"] == 2
    inits = set(df.init_time)
    assert inits == {np.datetime64(T0, "ns"), np.datetime64(T0 + dt.timedelta(hours=12), "ns")}
    assert np.datetime64(late, "ns") not in inits


# --------------------------------------------------------------------------- unscorable --
def test_an_antimeridian_flag_does_not_block_scoring_but_a_missing_sid_does():
    """The antimeridian flag is no reason to skip (EWB's detector matched its own target on such storms);
    a storm without an IBTrACS sid cannot be keyed and gets no rows. Same-name storms differ by sid."""
    tr = tc.observed_track(_event())
    crosser = _event(track_crosses_antimeridian=True)
    assert landfall.scorable(crosser) == (True, "")
    df, info = landfall.landfall_rows(crosser, MODEL, tr, [_perfect_fc_track(T0)])
    assert info["status"] == "scored" and len(df) > 0
    assert landfall.storm_id(_event())["sid"] == "S1"

    no_sid = _event()
    del no_sid.details["sid"]
    ok2, why2 = landfall.scorable(no_sid)
    assert ok2 is False and why2 and "sid" in why2
    df2, info2 = landfall.landfall_rows(no_sid, MODEL, tr, [_perfect_fc_track(T0)])
    assert info2["status"] == "unscorable" and len(df2) == 0


# ------------------------------------------------------------- per-init vs pooled calls --
def test_per_init_pairing_scores_both_inits_where_ewb_pooled_pairing_drops_one():
    """Two inits landing on the same spot: per-init pairs both, EWB's pooled 50 km de-dup keeps one.

    ``calc.find_landfalls`` de-duplicates forecast landfalls within 50 km across the inits it is
    handed, so a pooled call turns the second init's (identical) landfall into a spurious miss --
    the reason ``landfall_rows`` calls EWB once per init by default.
    """
    ev, tr = _event(), tc.observed_track(_event())
    inits = [T0, T0 + dt.timedelta(hours=12)]
    fcs = [_perfect_fc_track(i) for i in inits]                      # both cross at (0, 10), hour 72
    per_init, info_p = landfall.landfall_rows(ev, MODEL, tr, fcs)
    pooled, info_q = landfall.landfall_rows(ev, MODEL, tr, fcs, pooled=True)
    assert info_p["n_eligible"] == 2 == info_q["n_eligible"]
    assert info_p["n_paired"] == 2                                   # every init keeps its own pairing
    assert info_q["n_paired"] < info_p["n_paired"]                   # pooled de-dup ate one
    assert info_q["n_paired"] == 1
    assert len(per_init) == 8 and per_init.metric.value_counts().max() == 2
    miss = pooled[pooled.metric == landfall.M_MISS].set_index("init_time")["value"]
    assert miss[np.datetime64(T0, "ns")] == 0.0
    assert miss[np.datetime64(inits[1], "ns")] == 1.0                # the de-duplicated init


# ---------------------------------------------------------------------- forecast layout --
def test_forecast_array_is_the_ewb_layout_with_valid_time_equal_to_init_plus_lead():
    """(lead_time, valid_time), 2-D lat/lon, valid_time = init + lead at every point, Pa values."""
    fc = _perfect_fc_track(T0, pres=950.0)
    arr = landfall.forecast_array([fc])
    assert arr.dims == ("lead_time", "valid_time")
    assert arr.shape == (len(fc["lead_true"]), arr.sizes["valid_time"])
    for name in ("latitude", "longitude"):
        assert arr.coords[name].dims == ("lead_time", "valid_time")
    assert arr.lead_time.dtype == np.dtype("timedelta64[ns]")
    init = np.datetime64(T0, "ns")
    finite = np.isfinite(arr.values)
    assert finite.any()
    for i, lead in enumerate(arr.lead_time.values):                 # every followed point
        for j, vt in enumerate(arr.valid_time.values):
            if finite[i, j]:
                assert vt == init + lead                             # the init mapping stays lossless
    assert np.nanmax(arr.values) == pytest.approx(950.0 * 100.0)     # hPa in, Pa out
    assert np.nanmin(arr.values) == pytest.approx(950.0 * 100.0)
    assert landfall.forecast_array([]) is None
    assert landfall.forecast_array([_perfect_fc_track(T0, lost=True)]) is None


def test_observed_target_is_the_ewb_target_layout_in_pa():
    """The best track as EWB's target: dim valid_time, lat/lon coords, pressure in Pa."""
    tr = tc.observed_track(_event())
    tgt = landfall.observed_target(tr)
    assert tgt.dims == ("valid_time",) and tgt.name == landfall.VAR
    assert tgt.sizes["valid_time"] == len(tr.times)
    assert tgt.values[0] == pytest.approx(960.0 * 100.0)      # hPa in, Pa out
    assert np.allclose(tgt.coords["latitude"].values, tr.lat)
    assert np.allclose(tgt.coords["longitude"].values, tr.lon)
    assert tgt.coords["valid_time"].values[2] == np.datetime64(T0 + dt.timedelta(hours=6), "ns")


# ------------------------------------------------------------- finalize_pair integration --
def _samples(init: dt.datetime = T0, lead: float = 6.0):
    """One verified sample carrying all five track metrics (``track_verif.verify_init`` shape)."""
    return [{"init_true": init.isoformat(), "lead_true": lead, "tracked": True,
             "d_km": 50.0, "along_km": 40.0, "cross_km": 30.0, "dp_hpa": -5.0}]


def _plan(tr, init: dt.datetime = T0, leads=(6, 12)):
    """A verification plan like ``tc.plan_for`` builds (the landfall path reads ``track`` only)."""
    return {"verified": [(init, list(leads))], "not_verified": [], "start": init, "track": tr}


def test_finalize_pair_publishes_track_rows_and_landfall_rows_together():
    """One pair through ``tc.finalize_pair``: the 5 track metrics AND the 4 landfall metrics."""
    ev = _event()
    tr = tc.observed_track(ev)
    fc = _perfect_fc_track(T0)
    samples = _samples()
    results = [{"init": T0.isoformat(), "status": "scored", "samples": samples, "retries": 0,
                "fc_track": fc}]
    df, status = tc.finalize_pair(ev, MODEL, _plan(tr), results)
    assert df is not None and status["status"] == "scored" and status["rows"] == len(df)
    assert status["landfall"]["status"] == "scored" and status["landfall"]["n_paired"] == 1
    track_rows = df[df.metric.isin(tv.TRACK_METRICS)].reset_index(drop=True)
    assert len(track_rows) == 5 and set(track_rows.metric) == set(tv.TRACK_METRICS)
    pd.testing.assert_frame_equal(track_rows, tv.rows_for_pair(ev.id, ev.event_type, MODEL, samples))
    lf = df[df.metric.isin(landfall.LANDFALL_METRICS)]
    assert set(lf.metric) == set(landfall.LANDFALL_METRICS) and len(lf) == 4
    assert lf[lf.metric == landfall.M_DISP]["value"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert lf[lf.metric == landfall.M_MISS]["value"].iloc[0] == 0.0
    assert (lf.lead_time_hours == float(LF_H)).all()
    assert df.value.notna().all()


def test_finalize_pair_on_an_open_ocean_storm_keeps_the_track_rows_and_no_landfall_rows():
    """No observed landfall: the lane rows are unchanged and the landfall status says ``none``."""
    ev = _event(rows=_observed_rows(deg_per_6h=0.5))
    tr = tc.observed_track(ev)
    samples = _samples()
    results = [{"init": T0.isoformat(), "status": "scored", "samples": samples, "retries": 0,
                "fc_track": _perfect_fc_track(T0)}]
    df, status = tc.finalize_pair(ev, MODEL, _plan(tr), results)
    assert df is not None and set(df.metric) == set(tv.TRACK_METRICS)
    assert status["status"] == "scored" and status["landfall"]["status"] == "none"
    pd.testing.assert_frame_equal(df.reset_index(drop=True),
                                  tv.rows_for_pair(ev.id, ev.event_type, MODEL, samples))


def test_finalize_pair_turns_a_landfall_failure_into_a_retriable_read_error(monkeypatch):
    """An EWB-side exception must not publish half a pair: (None, read_error) mentioning landfall."""
    ev, tr = _event(), tc.observed_track(_event())

    def boom(*a, **k):
        raise RuntimeError("shapely exploded")

    monkeypatch.setattr(landfall, "landfall_rows", boom)
    results = [{"init": T0.isoformat(), "status": "scored", "samples": _samples(), "retries": 0,
                "fc_track": _perfect_fc_track(T0)}]
    df, status = tc.finalize_pair(ev, MODEL, _plan(tr), results)
    assert df is None and status["status"] == "read_error" and status["rows"] == 0
    assert "landfall" in status["reason"] and "shapely exploded" in status["reason"]
