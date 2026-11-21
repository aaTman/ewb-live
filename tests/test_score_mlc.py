"""MLC lane scorer (scoring/mlc.py, lane_pool, pipeline wiring). Offline, synthetic.

Style / fake reader modelled on ``tests/test_score_tc.py`` (the MLC event is a
``midlat_cyclone`` Event whose ``details['track']`` rows are
``[iso, lat, lon, central_mslp_hPa, central_mslp_hPa]``, six-hourly fixes, no
wind). The fake reader serves an MSLP stack with a closed low sitting at the
observed position plus a fixed east offset; with ``mirror=True`` the low's
central pressure is ERA5's central pressure at that step plus a constant bias
(``BIAS``), so the peak-depth error is exactly ``+BIAS`` (forecast too
shallow) and the forecast deepens in step with the analysis (fc_bomb ==
obs_bomb) — every depth/bomb decision is then known a priori.
"""
import concurrent.futures as cf
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ewb_live import pipeline
from ewb_live.events import select
from ewb_live.events.types import Event
from ewb_live.scoring import lane_pool, mlc, tc, track_verif as tv, truth

T0 = dt.datetime(2026, 9, 1, 0)
MODELS = ["graphcast", "aurora"]
BASE = 1012.0        # the fake reader's background MSLP
OBS_P0 = 990.0       # central MSLP of the observed low at fix 0
BIAS = 10.0          # the forecast low sits BIAS hPa shallower than ERA5 -> peak_err ~ +BIAS
LOST = 30.0          # the deepening (bomb) segment drops the central pressure this much in 24 h


def _extra(i):
    """Piecewise-linear bomb segment: LOST hPa extra fall between fixes 14 and 16."""
    return 0.0 if i <= 14 else (LOST / 2.0 * (i - 14) if i < 16 else LOST)


def _central(i):
    return OBS_P0 - 0.8 * i - _extra(i)


def _event(n_fixes=30, eid="mlc1", t0=T0, lat0=45.0, lon0=-40.0, with_bomb=True, dlat=0.4, dlon=0.9,
           wrap_lon=False):
    """A northeast-moving extratropical low (lat 45+0.4*i, lon -40+0.9*i), 6-hourly
    fixes, no wind column. The central pressure follows the gentle ``990 - 0.8*i``
    drift except during the bomb segment (fixes 14-16) when ``with_bomb`` is set;
    without it the low falls only 3.2 hPa/24 h and is never an observed bomb."""
    rows = []
    for i in range(n_fixes):
        t = t0 + dt.timedelta(hours=6 * i)
        c = _central(i) if with_bomb else OBS_P0 - 0.8 * i
        lo = lon0 + dlon * i
        if wrap_lon:
            lo = (lo + 180.0) % 360.0 - 180.0
        rows.append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), lat0 + dlat * i, lo, c, c])
    end = t0 + dt.timedelta(hours=6 * (n_fixes - 1))
    central = [r[4] for r in rows]
    return Event(id=eid, event_type="midlat_cyclone", title=f"Mid-lat cyclone {eid}",
                 start=t0, end=end, bbox=(lat0 - 5, lat0 + dlat * n_fixes + 5, lon0 - 5, lon0 + dlon * n_fixes + 5),
                 peak_value=min(central),
                 details={"track": rows, "bomb": bool(with_bomb), "min_mslp_hPa": float(min(central))})


def _depth_low(lat, lon, lat0, lon0, depth=30.0, sigma=1.6):
    yy, xx = np.meshgrid(lat, lon, indexing="ij")
    d2 = (yy - lat0) ** 2 + ((xx - lon0) * np.cos(np.radians(lat0))) ** 2
    return -depth * np.exp(-d2 / (2 * sigma ** 2))


def make_reader(event, east_offset_deg=0.5, gaps=(), errors=(), lost_after_lead=None, calls=None,
                mirror=True, depth=30.0, sigma=1.6):
    """Fake MSLP reader: a closed low at the observed centre + ``east_offset_deg``.

    With ``mirror`` the low's central MSLP is ERA5's central pressure at that step
    plus ``BIAS`` — a closed low of ``BASE - p - BIAS >= 12`` hPa — so the window
    minimum is ``p + BIAS``: ``peak_err`` is ``+BIAS`` (forecast shallower than
    ERA5, positive) at every lead, and the forecast's 24 h fall equals the
    observed one (fc_bomb == obs_bomb)."""
    tr = mlc.observed_track(event)

    def reader(model, init, leads, win, counter=None):
        if calls is not None:
            calls.append((model, init))
        if (model, init) in gaps:
            raise tc.NoData(f"{model} {init} not in store")
        if (model, init) in errors:
            raise RuntimeError("boom")
        lat0, lat1, lon0, width = win
        lat = np.arange(np.ceil(lat0 * 4) / 4, lat1, 0.25)
        lon = lon0 + np.arange(0, width, 0.25)
        fld = np.empty((len(leads), lat.size, lon.size))
        for j, h in enumerate(leads):
            k = tr.index_of(init + dt.timedelta(hours=h))
            f = np.full((lat.size, lon.size), BASE)
            if lost_after_lead is None or h <= lost_after_lead:
                dep = BASE - tr.pres[k] - BIAS if mirror else depth
                f = f + _depth_low(lat, lon, tr.lat[k], tr.lon[k] + east_offset_deg, depth=dep, sigma=sigma)
            fld[j] = f
        return lat, lon, fld, 0

    return reader


# ------------------------------------------------------------------- adapter --
def test_observed_track_adapter():
    """rows -> ObservedTrack: no wind, central pressure from slot 4, longitudes
    wrapped to [-180, 180)."""
    lons_in = [178.0, 179.5, 181.0, 182.5]      # crossing the antimeridian (181 -> -179 ...)
    rows = []
    for i, lo in enumerate(lons_in):
        t = T0 + dt.timedelta(hours=6 * i)
        rows.append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), 50.0 + i, lo, 1005.0, 985.0 - i])
    ev = Event(id="adp", event_type="midlat_cyclone", title="adapter", start=T0,
               end=T0 + dt.timedelta(hours=24), bbox=(45.0, 55.0, 170.0, -170.0), peak_value=982.0,
               details={"track": rows, "bomb": False, "min_mslp_hPa": 982.0})
    tr = mlc.observed_track(ev)
    assert tr.wind is None
    assert len(tr.times) == 4 and tr.times[1] == T0 + dt.timedelta(hours=6)
    # central pressure is read from slot 4, NOT slot 3 (slot 3 is the decoy)
    assert list(tr.pres) == [985.0, 984.0, 983.0, 982.0]
    assert list(tr.lon) == [178.0, 179.5, -179.0, -177.5]
    assert bool(((tr.lon >= -180.0) & (tr.lon < 180.0)).all())
    assert list(tr.lat) == [50.0, 51.0, 52.0, 53.0]


def test_plan_inits_are_0012z_with_following_fix_and_nothing_not_verified():
    """The MLC plan takes EVERY 00/12Z init where the low exists (a fix AT the init
    and one at init+6 h): unlike the TC lane nothing is 'not verified' (D2 keeps the
    first fix), the plan starts at the first ERA5 fix, and a low first seen at 18Z
    yields no 18Z init."""
    t0 = T0 + dt.timedelta(hours=18)                 # first fix at 18Z
    ev = _event(n_fixes=8, t0=t0)
    plan = mlc.plan_for(ev)
    inits = [i for i, _ in plan["verified"]]
    assert plan["start"] == t0                       # verification starts at the first ERA5 fix
    assert plan["not_verified"] == []                # nothing is ever "not verified" in this lane
    assert inits and all(i.hour in (0, 12) for i in inits)     # 00/12Z only ...
    assert t0 not in inits                           # ... so the 18Z first fix never becomes an init
    assert inits[0] == T0 + dt.timedelta(hours=24)   # the next 00Z after the 18Z first fix
    tr = plan["track"]
    for init, leads in plan["verified"]:             # every verified init has a fix at init and at +6 h
        assert tr.index_of(init) is not None and tr.index_of(init + dt.timedelta(hours=6)) is not None
        assert leads[0] == 0 and 6 in leads


def test_window_min_hpa():
    """Closed low found within +/-8 deg of the observed centre; not outside it; NaN
    on an all-fill window; wrap-aware on an unwrapped longitude frame."""
    lat = np.arange(-8.0, 8.0 + 0.25, 0.25)
    lon = np.arange(-20.0, 20.0 + 0.25, 0.25)

    def field_with_low_at(lon_c):
        f = np.full((lat.size, lon.size), 1013.0)
        ri = int(np.argmin(np.abs(lat)))            # lat 0
        ci = int(np.argmin(np.abs(lon - lon_c)))
        f[ri, ci] = 960.0                           # a single-cell 960 hPa minimum
        return f

    # 5 deg east of the centre: inside the +/-8 deg window -> found
    assert mlc.window_min_hpa(field_with_low_at(5.0), lat, lon, 0.0, 0.0) == pytest.approx(960.0)
    # 10 deg east: outside the +/-8 deg window -> the larger background is returned
    assert mlc.window_min_hpa(field_with_low_at(10.0), lat, lon, 0.0, 0.0) == pytest.approx(1013.0)
    # an all-NaN window -> NaN
    nan_f = np.full((lat.size, lon.size), np.nan)
    assert np.isnan(mlc.window_min_hpa(nan_f, lat, lon, 0.0, 0.0))
    # a wrapped frame: lon coords 175..195 (unwrapped), centre -175, low at 183 (= -177) -> found
    lat2 = np.arange(-5.0, 5.0 + 0.25, 0.25)
    lon2 = np.arange(175.0, 195.0 + 0.25, 0.25)
    f = np.full((lat2.size, lon2.size), 1013.0)
    f[int(np.argmin(np.abs(lat2))), int(np.argmin(np.abs(lon2 - 183.0)))] = 960.0
    assert mlc.window_min_hpa(f, lat2, lon2, 0.0, -175.0) == pytest.approx(960.0)


def test_bomb_threshold():
    """Sanders & Gyakum rule as tagged by mlc_track: 24 hPa * sin|lat| / sin 60."""
    assert mlc.bomb_threshold_hpa(60.0) == pytest.approx(24.0)
    assert mlc.bomb_threshold_hpa(30.0) == pytest.approx(24.0 * np.sin(np.radians(30)) / np.sin(np.radians(60)))
    assert mlc.bomb_threshold_hpa(-60.0) == pytest.approx(24.0)        # |lat| (Southern-Hemisphere lows)
    assert mlc.bomb_threshold_hpa(0.0) == pytest.approx(0.0)
    lats = np.linspace(1.0, 60.0, 60)
    thr = [mlc.bomb_threshold_hpa(x) for x in lats]
    assert all(b > a for a, b in zip(thr, thr[1:]))                    # grows with latitude


# ----------------------------------------------------------- depth_samples --
DS_LAT = np.arange(47.0, 63.0 + 0.25, 0.25)       # +-8 deg around 55N
DS_LON = np.arange(-15.0, 15.0 + 0.25, 0.25)


def _ds_track(pres, hours, lat=55.0):
    """An observed track with a given pressure series at init + hours[k], at 55N."""
    pres = np.asarray(pres, float)
    times = [T0 + dt.timedelta(hours=int(h)) for h in hours]
    return tv.ObservedTrack(times, np.full(len(pres), lat), np.zeros(len(pres)), pres, None)


def _ds_field(vals, fill0=False):
    """A flat (len(vals), ny, nx) MSLP stack: the window minimum at step j is
    exactly ``vals[j]`` (a fill-only step j is all NaN)."""
    f = np.empty((len(vals), DS_LAT.size, DS_LON.size), float)
    for j, v in enumerate(vals):
        f[j] = v
    if fill0:
        f[0] = np.nan
    return f


def test_depth_samples_bomb_hit_and_false_alarm():
    lat = 55.0                                     # scaled threshold ~22.7 hPa
    thr = mlc.bomb_threshold_hpa(lat)
    assert 22.0 < thr < 23.5                       # ~22.7 at 55N
    leads = [0, 24, 48]
    # observed central pressure: a 30 hPa fall in the first 24 h, then flat
    tr_bomb = _ds_track([990.0, 960.0, 960.0], [0, 24, 48])

    # (a) the observed low falls 30 hPa/24 h and the forecast deepens equally ->
    #     an observed bomb window the forecast also caught.
    recs = mlc.depth_samples(tr_bomb, T0, leads, 0, _ds_field([1000.0, 970.0, 970.0]), DS_LAT, DS_LON)
    r24 = [x for x in recs if x["lead_true"] == 24.0][0]
    assert r24["obs_bomb"] is True and r24["fc_bomb"] is True

    # (b) forecast stays flat while the analysis deepens -> a bomb MISSED.
    recs = mlc.depth_samples(tr_bomb, T0, leads, 0, _ds_field([1000.0, 1000.0, 1000.0]), DS_LAT, DS_LON)
    r24 = [x for x in recs if x["lead_true"] == 24.0][0]
    assert r24["obs_bomb"] is True and r24["fc_bomb"] is False

    # (c) observed flat, forecast deepens 30 hPa -> a bomb the analysis never had.
    tr_flat = _ds_track([990.0, 990.0, 990.0], [0, 24, 48])
    recs = mlc.depth_samples(tr_flat, T0, leads, 0, _ds_field([1000.0, 970.0, 970.0]), DS_LAT, DS_LON)
    r24 = [x for x in recs if x["lead_true"] == 24.0][0]
    assert r24["obs_bomb"] is False and r24["fc_bomb"] is True

    # (d) leads < 24 h carry peak_err but no obs_bomb/fc_bomb keys.
    tr_h = _ds_track([990.0, 988.0, 985.0, 960.0], [0, 6, 12, 24])
    recs = mlc.depth_samples(tr_h, T0, [0, 6, 12, 24], 0, _ds_field([1000.0, 970.0, 970.0, 970.0]), DS_LAT, DS_LON)
    r6 = [x for x in recs if x["lead_true"] == 6.0][0]
    assert "peak_err" in r6 and "obs_bomb" not in r6 and "fc_bomb" not in r6

    # (e) lead 0 never produces a record.
    recs = mlc.depth_samples(tr_h, T0, [0], 0, _ds_field([1000.0]), DS_LAT, DS_LON)
    assert recs == []

    # (f) a fill-only (all-NaN) lead produces no record, and the lead-24 sample that
    #     needs it (for the forecast's own 24 h fall) carries no bomb keys.
    recs = mlc.depth_samples(tr_bomb, T0, leads, 0, _ds_field([0.0, 970.0, 970.0], fill0=True), DS_LAT, DS_LON)
    r24 = [x for x in recs if x["lead_true"] == 24.0][0]
    assert "peak_err" in r24 and "obs_bomb" not in r24 and "fc_bomb" not in r24
    assert [x["lead_true"] for x in recs] == [24.0, 48.0]      # lead 0: no record at all


def test_depth_rows_metrics_and_signs():
    """depth_rows: PeakDepthError for every sample (forecast shallower than ERA5 ->
    POSITIVE value), BombDetected only for observed-bomb samples (1.0 if fc_bomb else
    0.0), BombFalseAlarm only for the non-bomb ones, deduped by (init_true, lead_true);
    SCORE_COLUMNS columns and dtypes."""
    def s(hour, lead, peak, obs=None, fc=None):
        rec = {"init_true": f"2026-09-01T{hour:02d}:00:00", "lead_true": float(lead), "peak_err": peak}
        if obs is not None:
            rec["obs_bomb"], rec["fc_bomb"] = obs, fc
        return rec

    samples = [
        s(0, 24, 5.0, True, True),        # observed bomb, forecast caught it -> BombDetected 1.0
        s(0, 48, 3.0, False, False),      # non-bomb window, no false alarm -> BombFalseAlarm 0.0
        s(12, 24, 2.0, False, True),      # non-bomb window, false alarm -> BombFalseAlarm 1.0
        s(12, 48, 1.0, True, False),      # observed bomb the forecast missed -> BombDetected 0.0
        s(12, 6, 9.0),                    # lead < 24 h: PeakDepthError only, no bomb keys
    ]
    df = mlc.depth_rows("e1", "midlat_cyclone", "graphcast", samples + [dict(samples[0])])   # dup deduped
    assert list(df.columns) == tv.SCORE_COLUMNS
    for c in ("event_id", "event_type", "model", "metric"):
        assert df[c].dtype == object
    assert df["lead_time_hours"].dtype == np.dtype("float64") and df["value"].dtype == np.dtype("float64")
    assert df["init_time"].dtype == np.dtype("datetime64[ns]")
    assert (df["event_type"] == "midlat_cyclone").all()

    g = {(r.metric, r.init_time, r.lead_time_hours): r.value for r in df.itertuples()}
    t00, t12 = pd.Timestamp("2026-09-01T00:00:00"), pd.Timestamp("2026-09-01T12:00:00")
    assert g[("PeakDepthError", t00, 24.0)] == 5.0 and g[("PeakDepthError", t12, 48.0)] == 1.0
    assert g[("PeakDepthError", t12, 6.0)] == 9.0
    assert g[("BombDetected", t00, 24.0)] == 1.0 and g[("BombDetected", t12, 48.0)] == 0.0
    assert g[("BombFalseAlarm", t00, 48.0)] == 0.0 and g[("BombFalseAlarm", t12, 24.0)] == 1.0
    # PeakDepthError for EVERY sample, the duplicate folded away; positive here (too shallow)
    assert len(df[df.metric == "PeakDepthError"]) == 5
    assert (df[df.metric == "PeakDepthError"]["value"] > 0).all()
    # BombDetected only on observed-bomb samples, BombFalseAlarm only on the non-bomb ones
    assert len(df[df.metric == "BombDetected"]) == 2
    assert len(df[df.metric == "BombFalseAlarm"]) == 2


# ----------------------------------------------------------------- scoring ---
def test_score_mlc_event_rows_and_errors():
    ev = _event()                                   # bomb cyclone: deepens LOST hPa around fix 14-16
    plan = mlc.plan_for(ev)
    df, st = mlc.score_mlc_event(ev, MODELS, reader=make_reader(ev, east_offset_deg=0.5))
    assert list(df.columns) == tv.SCORE_COLUMNS
    assert (df["event_type"] == "midlat_cyclone").all() and set(df["model"]) == set(MODELS)
    assert df["init_time"].min() == np.datetime64(plan["start"], "ns")   # verified from the first fix
    # every metric of the lane appears: the track core on the tracked leads, the depth
    # rows everywhere, and both a bomb window (fixes 16/18) and non-bomb windows
    assert set(df.metric) == set(mlc.MLC_METRICS)

    miss = df[df.metric == "TrackMiss"]["value"]
    assert len(miss) > 0 and miss.sum() == 0                            # a well-served low is never lost
    # position error = the 0.5 deg east offset at ~50N (~35 km, plus grid snap)
    pos = df[df.metric == "TrackPositionError"]["value"]
    assert pos.median() == pytest.approx(0.5 * 111.195 * np.cos(np.radians(50.0)), rel=0.25)
    assert (pos > 0).all() and (pos < 70.0).all()
    # the low moves NORTHEAST (heading ~55 deg): a due-east offset lies to the RIGHT of
    # the motion -> cross-track positive (and along-track positive too, ~cos 35 deg)
    cross = df[df.metric == "CrossTrackError"]["value"]
    assert len(cross) > 0 and (cross > 0).mean() > 0.95
    # MLC-B: the forecast window minimum is ERA5's central pressure + BIAS -> peak_err
    # ~ +BIAS everywhere (forecast too shallow -> POSITIVE, the signed convention)
    peak = df[df.metric == "PeakDepthError"]["value"]
    assert len(peak) > 0 and (peak.between(BIAS - 1.0, BIAS + 1.0)).all()
    # the mirrored forecast deepens in step with ERA5 -> every observed bomb caught, no alarm
    assert (df[df.metric == "BombDetected"]["value"] == 1.0).all()
    assert (df[df.metric == "BombFalseAlarm"]["value"] == 0.0).all()
    for s in st.values():
        assert s["status"] == "scored" and s["n_inits_not_verified"] == 0


def test_lost_low_gives_miss_rows_but_keeps_peak_depth_rows():
    """After the low is lost MLC-A emits ONLY TrackMiss=1 (never imputed, D5), but
    MLC-B needs no tracker, so PeakDepthError rows keep scoring those leads."""
    ev = _event(with_bomb=False)
    df, _ = mlc.score_mlc_event(ev, ["graphcast"], reader=make_reader(ev, lost_after_lead=24))
    late = df[df.lead_time_hours >= 30]             # gone after 24 h + 2 bridging steps
    assert len(late) > 0
    assert (late[late.metric == "TrackMiss"]["value"] == 1.0).all()
    for m in ("TrackPositionError", "AlongTrackError", "CrossTrackError", "CentralPressureError"):
        assert m not in set(late.metric)            # misses are never imputed
    # PeakDepthError survives the lost track (it reads the field, not the tracker)
    pk = late[late.metric == "PeakDepthError"]
    assert set(pk.lead_time_hours) == set(late[late.metric == "TrackMiss"].lead_time_hours)
    # before the loss the low is tracked clean
    early = df[df.lead_time_hours == 12]
    assert {"TrackPositionError", "TrackMiss", "PeakDepthError"} <= set(early.metric)
    assert early[early.metric == "TrackMiss"]["value"].sum() == 0


def test_truth_edge_leads_past_the_track_end_are_not_scored():
    """The truth horizon is the ERA5 edge: no sample has a valid time (init_true +
    lead_true) beyond the last fix, and the first init reaches at most 66 h."""
    ev = _event(n_fixes=12, with_bomb=False)        # 12 six-hourly fixes: T0 .. T0+66h
    tr = mlc.observed_track(ev)
    last = tr.times[-1]
    df, _ = mlc.score_mlc_event(ev, ["graphcast"], reader=make_reader(ev))
    valid = df["init_time"] + pd.to_timedelta(df["lead_time_hours"], unit="h")
    assert (valid <= np.datetime64(last, "ns")).all()
    first_init = df["init_time"].min()
    d0 = df[df["init_time"] == first_init]
    assert first_init == np.datetime64(T0, "ns") and d0["lead_time_hours"].max() == 66.0


def test_antimeridian_track():
    """A low crossing 180 stays tracked when the reader serves the window as one
    contiguous (unwrapped) longitude frame via ``tv.contiguous_lons``."""
    ev = _event(n_fixes=20, with_bomb=False, lon0=177.0, dlon=1.0, wrap_lon=True)   # 177 -> -164
    tr = mlc.observed_track(ev)
    unw = 177.0 + 1.0 * np.arange(len(tr.lon))      # the fixes' continuous longitudes

    def reader(model, init, leads, win, counter=None):
        lat0, lat1, lon0, width = win
        lat = np.arange(np.ceil(lat0 * 4) / 4, lat1, 0.25)
        _oi, lon = tv.contiguous_lons(np.arange(-180.0, 180.0, 0.25), lon0, width)
        fld = np.empty((len(leads), lat.size, lon.size))
        for j, h in enumerate(leads):
            k = tr.index_of(init + dt.timedelta(hours=h))
            f = np.full((lat.size, lon.size), BASE)
            # express the low's centre in the frame's own longitude coords (window_for
            # may unwrap the track east-positive or west-negative, frame-dependent!)
            c_lon = lon0 + ((unw[k] + 0.5 - lon0) % 360.0)
            f = f + _depth_low(lat, lon, tr.lat[k], c_lon, depth=30.0, sigma=2.0)
            fld[j] = f
        return lat, lon, fld, 0

    df, _ = mlc.score_mlc_event(ev, ["graphcast"], reader=reader)
    pos = df[df.metric == "TrackPositionError"]["value"]
    assert len(pos) > 0 and (pos < 80.0).all()      # the 0.5 deg east offset, not a dateline jump
    miss = df[df.metric == "TrackMiss"]["value"]
    assert len(miss) > 0 and miss.sum() == 0        # crossing 180 must not lose the low


# ---------------------------------------------------------------- pair status --
def test_store_gap_is_no_data_not_miss():
    """A missing store init is a documented gap: no rows, no miss rows, pair still scored."""
    ev = _event()
    plan = mlc.plan_for(ev)
    gap_init = plan["verified"][2][0]
    df, st = mlc.score_mlc_event(ev, ["graphcast"], reader=make_reader(ev, gaps={("graphcast", gap_init)}))
    assert np.datetime64(gap_init, "ns") not in set(df["init_time"])      # no rows at all for the gap init
    assert df[df.metric == "TrackMiss"]["value"].sum() == 0               # and no miss rows either
    s = st["graphcast"]
    assert s["status"] == "scored" and s["n_inits_no_data"] == 1 and "not counted as misses" in s["reason"]


def test_read_error_blocks_the_pair():
    ev = _event()
    plan = mlc.plan_for(ev)
    # every init missing from the store -> documented no_data
    every = {("graphcast", i) for i, _ in plan["verified"]}
    df, st = mlc.score_mlc_event(ev, ["graphcast"], reader=make_reader(ev, gaps=every))
    assert df.empty and st["graphcast"]["status"] == "no_data" and st["graphcast"]["rows"] == 0
    assert st["graphcast"]["status"] in pipeline.DOCUMENTED_ZERO_STATUSES
    # one init still failing after its retries -> read_error (retriable, a completion blocker)
    df, st = mlc.score_mlc_event(ev, ["aurora"], reader=make_reader(ev, errors={("aurora", plan["verified"][0][0])}))
    assert df.empty and st["aurora"]["status"] == "read_error"
    assert st["aurora"]["status"] not in pipeline.DOCUMENTED_ZERO_STATUSES
    # nothing verifiable at all -> documented empty
    one_fix = _event(n_fixes=1)
    df, st = mlc.score_mlc_event(one_fix, ["graphcast"], reader=make_reader(one_fix))
    assert df.empty and st["graphcast"]["status"] == "empty"


# --------------------------------------------------------- lane registration --
def _heat_event():
    return Event(id="heat0000001", event_type="heat_wave", title="Heat 2026-09-01 to 2026-09-04",
                 start=T0, end=T0 + dt.timedelta(days=3), bbox=(10.0, 20.0, 0.0, 10.0), peak_value=5.0, details={})


def _tc_event():
    rows = [[(T0 + dt.timedelta(hours=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), 10.0 + 0.25 * i, -110.0, 40.0, 1000.0]
            for i in range(10)]
    return Event(id="tc1", event_type="tropical_cyclone", title="TC", start=T0,
                 end=T0 + dt.timedelta(hours=30), bbox=(5.0, 25.0, -125.0, -95.0), peak_value=40.0,
                 details={"track": rows, "peak_wind_kt": 40.0})


def test_truth_tag_and_lane_registration():
    ev = _event()
    # the published MLC track IS an ERA5 (ARCO) product by construction: the tag does
    # not follow the window's ARCO reach, even with a cutoff long before the event end
    assert truth.truth_source_for_event(ev, dt.datetime(1999, 1, 1)) == "era5_arco"
    assert truth.truth_source_for_event(ev, dt.datetime(2030, 1, 1)) == "era5_arco"
    # the observation-lane (heat_wave) ERA5-vs-HRES choice is untouched
    h = _heat_event()
    assert truth.truth_source_for_event(h, T0 + dt.timedelta(days=10)) == "era5_arco"
    assert truth.truth_source_for_event(h, T0 - dt.timedelta(days=10)) == "ifs_hres_analysis"
    # the TC lane still reports its own truth
    assert truth.truth_source_for_event(_tc_event(), dt.datetime(1999, 1, 1)) == "ibtracs"

    assert "midlat_cyclone" in lane_pool.LANE_TYPES
    assert lane_pool.LANE_KEY_BY_EVENT_TYPE["midlat_cyclone"] == "mlc"
    assert lane_pool.LANES["mlc"].truth == "era5_arco"

    names = ["graphcast", "graphcast_gfs", "aurora", "aurora_gfs", "pangu_weather",
             "pangu_weather_gfs", "fourcastnet_v2", "fourcastnet_v2_gfs", "ifs_hres", "aifs_single", "aigfs"]
    ok, why = lane_pool.mlc_models(names)
    assert len(ok) == 9 and "ifs_hres" in ok               # the 8 store models + the baseline
    assert set(why) == {"aifs_single", "aigfs"}
    assert all("midlat_cyclone" in r for r in why.values())
    assert select.UNSCORED_TYPES == {"flood"}


# ------------------------------------------------------ scorecard + pipeline --
def _score_rows(metric, model, value):
    rows = []
    for ev in ("a", "b", "c", "d"):
        for lead in (24.0, 48.0):
            rows.append({"event_id": ev, "event_type": "midlat_cyclone", "model": model, "metric": metric,
                         "lead_time_hours": lead, "value": float(value),
                         "init_time": np.datetime64("2026-09-01T00:00:00", "ns")})
    return rows


def test_scorecard_signed_and_higher_is_better():
    rows = []
    rows += _score_rows("TrackPositionError", "graphcast", 50.0) + _score_rows("TrackPositionError", "ifs_hres", 100.0)
    rows += _score_rows("PeakDepthError", "graphcast", 5.0) + _score_rows("PeakDepthError", "ifs_hres", 5.0)
    # BombDetected: graphcast reproduced every observed bomb (1.0), the baseline half (0.5)
    rows += _score_rows("BombDetected", "graphcast", 1.0) + _score_rows("BombDetected", "ifs_hres", 0.5)
    rows += _score_rows("TrackMiss", "graphcast", 0.0) + _score_rows("TrackMiss", "ifs_hres", 0.0)
    card = pipeline.build_scorecard(pd.DataFrame(rows, columns=tv.SCORE_COLUMNS))
    idx = {(r["metric"], r["lead_time_hours"], r["model"]): r for r in card["rows"]}
    # a signed-error metric gets NO skill anywhere (D6), even for the baseline
    assert "PeakDepthError" in pipeline.SIGNED_ERROR_METRICS
    assert idx[("PeakDepthError", 24.0, "graphcast")]["skill_vs_ifs_pct"] is None
    assert idx[("PeakDepthError", 24.0, "ifs_hres")]["skill_vs_ifs_pct"] is None
    # TrackPositionError: lower is better -> graphcast (50 vs 100 km) has +50 % skill
    assert idx[("TrackPositionError", 24.0, "graphcast")]["skill_vs_ifs_pct"] == pytest.approx(50.0)
    # BombDetected is higher-is-better: the better hit rate (1.0 vs 0.5) is POSITIVE skill
    assert "BombDetected" in pipeline.HIGHER_IS_BETTER
    assert idx[("BombDetected", 24.0, "graphcast")]["skill_vs_ifs_pct"] == pytest.approx(100.0)
    # the miss rows are counted in n
    assert idx[("TrackMiss", 24.0, "graphcast")]["n_samples"] == 4
    assert idx[("TrackMiss", 48.0, "graphcast")]["n_events"] == 4


def test_compact_track_rows_collapses_mlc():
    """scores.json payload: the MLC lane's per-(init, lead) rows collapse to one mean
    row per (event, model, metric, lead); the observation lanes pass through."""
    samples = [
        {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0, "peak_err": 2.0, "obs_bomb": False, "fc_bomb": False},
        {"init_true": "2026-09-01T12:00:00", "lead_true": 24.0, "peak_err": 8.0, "obs_bomb": False, "fc_bomb": False},
    ]
    mlc_rows = mlc.depth_rows("e1", "midlat_cyclone", "m", samples)
    heat = pd.DataFrame({"event_id": ["h"], "event_type": ["heat_wave"], "model": ["m"], "metric": ["x"],
                         "lead_time_hours": [24.0], "value": [1.0], "init_time": [np.datetime64("2026-09-01", "ns")]})
    out = pipeline.compact_track_rows(pd.concat([mlc_rows, heat], ignore_index=True))
    m = out[out.event_type == "midlat_cyclone"]
    peak = m[m.metric == "PeakDepthError"]
    assert len(peak) == 1 and peak["value"].iloc[0] == pytest.approx(5.0)     # mean(2, 8)
    assert len(m[m.metric == "BombFalseAlarm"]) == 1
    assert m["init_time"].isna().all()
    assert len(out[out.event_type == "heat_wave"]) == 1
    assert out[out.event_type == "heat_wave"]["init_time"].notna().all()


# --------------------------------------------------------------- lane pool ---
def _threads(n=3):
    return lambda: cf.ThreadPoolExecutor(max_workers=n)


@pytest.fixture
def fake_mlc_lane(monkeypatch):
    """Route the 'mlc' pool lane's jobs through the fake reader and run them in threads.

    The reader is injected through the SAME mechanism ``tests/test_score_tc.py``'s
    ``fake_lane`` fixture uses — a replaced ``lane_pool.LANES['mlc']`` whose ``init_job``
    closes over the fake reader — NOT by monkeypatching ``tc.read_store_msl`` /
    ``tc.read_ifs_msl``: the pool's worker calls ``lane.init_job(track, model, init,
    leads)`` with no reader argument, and ``mlc.score_init`` binds
    ``tc.default_reader`` as its default at definition time, so a module-attribute patch
    would not reach it."""
    ev1, ev2 = _event(eid="mlc1"), _event(eid="mlc2")     # identical geometry: one reader serves both
    holder = {"reader": make_reader(ev1)}

    def init_job(track, model, init, leads, reader=None):
        return mlc.score_init(track, model, init, leads, holder["reader"])

    lane = lane_pool.Lane(event_type="midlat_cyclone", truth="era5_arco", models=lane_pool.mlc_models,
                          plan=mlc.plan_for, init_job=init_job, finalize=mlc.finalize_pair, label="mlc")
    monkeypatch.setitem(lane_pool.LANES, "mlc", lane)
    return (ev1, ev2), holder


def test_lane_pool_runs_mlc_pairs_in_process(fake_mlc_lane):
    (ev1, ev2), _ = fake_mlc_lane
    got = {}
    pairs = [(ev1, "graphcast"), (ev1, "aurora"), (ev2, "graphcast"), (ev2, "aurora")]
    stats = lane_pool.run_pairs("mlc", pairs, lambda e, m, df, st: got.update({(e.id, m): (df, st)}),
                                workers=4, executor_factory=_threads(4), poll_s=0.05)
    assert stats["pairs_done"] == 4 and not stats["stopped"]
    assert set(got) == {(e.id, m) for e in (ev1, ev2) for m in ("graphcast", "aurora")}
    for (eid, m), (df, st) in got.items():
        assert st["status"] == "scored", (eid, m, st)
        assert "PeakDepthError" in set(df["metric"])


def test_score_event_serial_matches_pool_path():
    ev = _event()
    reader = make_reader(ev)
    a, _ = lane_pool.score_event_serial(ev, MODELS, reader=reader)
    b, _ = mlc.score_mlc_event(ev, MODELS, reader=reader)
    key = ["event_id", "event_type", "model", "metric", "init_time", "lead_time_hours", "value"]
    pd.testing.assert_frame_equal(a.sort_values(key).reset_index(drop=True),
                                  b.sort_values(key).reset_index(drop=True))
    assert set(a["model"]) == set(MODELS)


@pytest.mark.xfail(
    reason="scoring/lane_pool.py::score_event_serial only forwards a reader when one is passed "
           "(it does `*([reader] if reader else [])`), and pipeline._score_job — the MLC commit's "
           "'pipeline dispatch' path — calls it WITHOUT a reader. mlc.score_init/tc.score_init both "
           "bind `reader: Callable = tc.default_reader` as a DEFAULT ARGUMENT at import time, so a "
           "later monkeypatch of tc.default_reader (the whole point of the serial path's injectable "
           "reader, and what the old tc.score_tc_event's call-time `reader or default_reader` honoured) "
           "never reaches it: the MLC lane falls back to the real store and a patched reader is "
           "silently ignored. Source fix: resolve the lane's default reader at call time (e.g. "
           "`reader=tc.default_reader` read inside the function body, or pass reader through _score_job).",
    strict=True)
def test_score_job_lane_dispatch_honours_a_patched_default_reader(monkeypatch):
    """The track-lane dispatch in pipeline._score_job must use an injected default reader."""
    ev = _event()
    monkeypatch.setattr(tc, "default_reader", make_reader(ev))
    from ewb_live.stores import mlwp_icechunk
    monkeypatch.setattr(mlwp_icechunk, "DEFAULT_REPO", "/nonexistent-mlc-lane-repo")  # fail the real read fast
    status = {}
    eid, df = pipeline._score_job(ev.to_dict(), ["graphcast"], "era5_arco", status_out=status)
    assert eid == ev.id and df is not None and "TrackMiss" in set(df["metric"])
    assert status["graphcast"]["status"] == "scored"
