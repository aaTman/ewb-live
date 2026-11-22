"""Flood lane unit tests: verification window, truth build, plan, score_init, pair finalize.

Everything is offline and synthetic: PARDIG daily files are written into a throwaway root and
the climatology / land mask are injected, and the store reader is faked with a field that
reproduces the daily truth exactly (so every CSI is 1.0 by construction).
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ewb_live.events.types import Event
from ewb_live.scoring import flood, tc, track_verif as tv

FILL = np.float32(9.96921e36)          # the MLWP store fill value
CLIM1, CLIM3 = 5.0, 15.0               # injected ERA5 p95 climatology (below both floors)

# the PARDIG window build_truth reads: (start - 2 day pad) - 2 more days for the 72 h lane .. end + pad
ALL_DAYS = [dt.date(2026, 9, 6) + dt.timedelta(days=i) for i in range(9)]           # Sep 6 .. Sep 14
GAP_DAY = dt.date(2026, 9, 11)

# the 4x4 wet block inside the bbox: lat 11.00..11.75, lon 31.00..31.75 on the 0.25 deg lattice
# (the PARDIG latitude axis runs from 90 N downwards: row (90 - lat) / 0.25)
BLOCK = (slice(313, 317), slice(844, 848))
BLOCK_LATS = [11.0, 11.25, 11.5, 11.75]
BLOCK_LONS = [31.0, 31.25, 31.5, 31.75]


def _event():
    return Event(id="fl1", event_type="flood", title="Flood",
                 start=dt.datetime(2026, 9, 10), end=dt.datetime(2026, 9, 13),
                 bbox=(10.0, 14.0, 30.0, 34.0), peak_value=100.0, details={})


def _write_pardig(root, days, skip=()):
    """PARDIG-style daily files: zeros plus a 4x4 block of 30 mm/day inside the bbox."""
    daily = root / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    lats = np.arange(90, -90.25, -0.25)
    lons = np.arange(-180, 180, 0.25)
    for d in days:
        if d in skip:
            continue
        arr = np.zeros((lats.size, lons.size), "float32")
        arr[BLOCK] = 30.0
        xr.Dataset({"precip": (("latitude", "longitude"), arr, {"units": "mm/day"})},
                   coords={"latitude": lats, "longitude": lons}).to_netcdf(
            daily / f"pardig_025_{d:%Y%m%d}.nc", engine="h5netcdf",
            encoding={"precip": {"zlib": True, "complevel": 5}})


def _inject(root, clim=CLIM1, clim3=CLIM3, land=None, nan_cell=None):
    """Keyword bundle for build_truth / plan_for / score_flood_event with injected inputs."""
    lats, lons, _ = flood.domain_for(_event().bbox)

    def thresholds(lat, lon, dates, w):
        out = np.full((len(dates), lat.size, lon.size), float(clim) if w == 1 else float(clim3),
                      "float32")
        if nan_cell is not None:
            out[:, nan_cell[0], nan_cell[1]] = np.nan
        return out

    return {"pardig_root": root, "thresholds": thresholds,
            "land": np.ones((lats.size, lons.size), bool) if land is None else land}


@pytest.fixture(scope="module")
def roots(tmp_path_factory):
    base = tmp_path_factory.mktemp("pardig_flood_units")
    made = {}
    for name, skip in (("full", ()), ("gap", {GAP_DAY}),
                       ("sparse", {d for d in ALL_DAYS if d != dt.date(2026, 9, 6)})):
        r = base / name
        r.mkdir()
        _write_pardig(r, ALL_DAYS, skip)
        made[name] = r
    return made


def _idx(lat_arr, lon_arr, lat, lon):
    return (int(np.where(np.isclose(lat_arr, lat))[0][0]), int(np.where(np.isclose(lon_arr, lon))[0][0]))


# --------------------------------------------------------------------------- 1
def test_verification_days_window():
    assert flood.event_days(_event()) == [dt.date(2026, 9, 10), dt.date(2026, 9, 11), dt.date(2026, 9, 12)]
    vd = flood.verification_days(_event())
    assert vd == [dt.date(2026, 9, 8) + dt.timedelta(days=i) for i in range(7)]
    assert (vd[0], vd[-1]) == (dt.date(2026, 9, 8), dt.date(2026, 9, 14))


# --------------------------------------------------------------------------- 2
def test_build_truth_thresholds_and_footprint(roots):
    ev = _event()
    tr = flood.build_truth(ev, **_inject(roots["full"]))
    assert tr.present.tolist() == [True] * len(tr.days)
    assert tr.days == flood.verification_days(ev)
    # daily lane: max(injected 5 mm clim, 20 mm floor) on every cell
    assert tr.thr.shape == (len(tr.days), tr.lat.size, tr.lon.size)
    assert np.isfinite(tr.thr).all() and (tr.thr == 20.0).all()
    # a 30 mm block cell is a truth exceedance, the effective threshold is what gates it
    iy, ix = _idx(tr.lat, tr.lon, 11.0, 31.0)
    assert tr.truth[:, iy, ix] == pytest.approx(30.0)
    # footprint: non-empty, inside land, holds the block, not the far corner
    assert tr.footprint.any()
    assert not (tr.footprint & ~tr.land).any()
    for la in BLOCK_LATS:
        for lo in BLOCK_LONS:
            j, k = _idx(tr.lat, tr.lon, la, lo)
            assert tr.footprint[j, k]
    for la in (8.0, 16.0):
        for lo in (28.0, 36.0):
            j, k = _idx(tr.lat, tr.lon, la, lo)
            assert not tr.footprint[j, k]

    hi = flood.build_truth(ev, **_inject(roots["full"], clim=25.0))
    assert (hi.thr == 25.0).all()

    tr2 = flood.build_truth(ev, **_inject(roots["full"], nan_cell=(iy, ix)))
    assert np.isnan(tr2.thr[:, iy, ix]).all()
    assert (tr2.thr[:, iy + 1, ix] == 20.0).all()        # only the injected cell is undefined

    dry = flood.build_truth(ev, **_inject(roots["full"], land=np.zeros_like(tr.land)))
    assert not dry.footprint.any()


# --------------------------------------------------------------------------- 3
def test_plan_for_skips_the_missing_day(roots):
    ev = _event()
    plan = flood.plan_for(ev, **_inject(roots["gap"]))
    tr = plan["track"]
    assert tr.present.tolist() == [d != GAP_DAY for d in tr.days]
    assert plan["not_verified"] == [GAP_DAY]

    inits = [i for i, _ in plan["verified"]]
    assert inits == sorted(inits)
    assert set(inits) == {dt.datetime(2026, 8, 30) + dt.timedelta(days=k) for k in range(16)}
    assert dt.datetime(2026, 8, 30, 0) in inits and dt.datetime(2026, 9, 14, 0) in inits
    assert all(i.hour == 0 for i in inits)

    present = [d for d, ok in zip(tr.days, tr.present) if ok]
    for init, leads in plan["verified"]:
        assert leads == sorted(leads)
        assert all(6 <= h <= tv.MAX_LEAD_H and h % 6 == 0 for h in leads)
        want = set()
        for day in present:
            if 0 <= (day - init.date()).days <= flood.MAX_LEAD_DAYS - 1:
                want.update(flood.bin_leads_of_day(init, day))
        assert set(leads) == want                     # no lead survives for the missing day only
    # the missing day's own 6..24 h block is gone from the init dated like it
    _, l11 = plan["verified"][inits.index(dt.datetime(2026, 9, 11))]
    assert 6 not in l11 and min(l11) >= 30                # the gap day's own bins are not needed

    full = flood.plan_for(ev, **_inject(roots["full"]))
    assert full["not_verified"] == []
    assert len(full["verified"]) >= len(plan["verified"])


# --------------------------------------------------------------------------- 4
def _reader(truth, offset=0, fill_lead6=False):
    """Fake store reader: daily totals that reproduce the truth exactly (METRES per 6 h bin)."""
    def reader(model, init, leads, win, counter=None):
        fld = np.zeros((len(leads), truth.lat.size, truth.lon.size), "float32")
        pos = {h: k for k, h in enumerate(leads)}
        for i, day in enumerate(truth.days):
            if not truth.present[i]:
                continue
            step = np.asarray(truth.truth[i], "float32") / 4.0 / 1000.0
            for h in flood.bin_leads_of_day(init, day):
                if h in pos:
                    fld[pos[h]] = step
        if fill_lead6 and 6 in pos:
            fld[pos[6]] = FILL
        return truth.lat, truth.lon, fld, offset
    return reader


def test_score_init_perfect_reader_and_gaps(roots):
    ev = _event()
    plan = flood.plan_for(ev, **_inject(roots["gap"]))
    tr = plan["track"]
    reader = _reader(tr)

    by_day = {}
    for init, leads in plan["verified"]:
        rec = flood.score_init(tr, "graphcast", init, leads, reader)
        assert rec["status"] == "scored" and rec["retries"] == 0
        assert rec["init"] == init.isoformat() and rec["n_days_fill"] == 0
        assert rec["samples"], init
        for s in rec["samples"]:
            day = dt.date.fromisoformat(s["day"])
            nominal = int((dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
                           - init) / dt.timedelta(hours=1))
            assert s["lead_true"] == nominal
            assert s["init_true"] == init.isoformat()
            assert s["lead_true"] % 6 == 0
            i = tr.days.index(day)
            assert tr.present[i]
            if (tr.truth[i] >= tr.thr[i]).any():
                assert s["metrics"][flood.M_CSI] == pytest.approx(1.0)
            by_day.setdefault(s["day"], 0)
            by_day[s["day"]] += 1
    # every present verified day is scored by the inits that can reach it; the gap never appears
    assert set(by_day) == {d.isoformat() for d in tr.days if d != GAP_DAY}
    assert GAP_DAY.isoformat() not in by_day


def test_score_init_fill_day_not_scored(roots):
    plan = flood.plan_for(_event(), **_inject(roots["full"]))
    tr = plan["track"]
    inits = [i for i, _ in plan["verified"]]
    init = dt.datetime(2026, 9, 10)
    leads = plan["verified"][inits.index(init)][1]
    assert 6 in leads
    rec = flood.score_init(tr, "graphcast", init, leads, _reader(tr, fill_lead6=True))
    assert rec["status"] == "scored"
    days = {s["day"] for s in rec["samples"]}
    assert "2026-09-10" not in days                       # the day whose first bin is lead 6
    assert rec["n_days_fill"] >= 1
    assert "2026-09-12" in days                            # later days still scored


def test_score_init_offset_and_failures(roots):
    plan = flood.plan_for(_event(), **_inject(roots["full"]))
    tr = plan["track"]
    inits = [i for i, _ in plan["verified"]]
    init = dt.datetime(2026, 9, 10)
    leads = plan["verified"][inits.index(init)][1]

    rec = flood.score_init(tr, "graphcast", init, leads, _reader(tr, offset=12))
    assert rec["status"] == "scored" and rec["offset_h"] == 12
    for s in rec["samples"]:
        day = dt.date.fromisoformat(s["day"])
        nominal = int((dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
                       - init) / dt.timedelta(hours=1))
        assert s["init_true"] == (init - dt.timedelta(hours=12)).isoformat()
        assert s["lead_true"] == nominal + 12

    def raises(exc):
        def reader(model, init_, leads_, win, counter=None):
            raise exc
        return reader

    assert flood.score_init(tr, "graphcast", init, leads, raises(tc.NoData("x")))["status"] == "no_data"
    bad = flood.score_init(tr, "graphcast", init, leads, raises(RuntimeError("boom")))
    assert bad["status"] == "error" and "boom" in bad["reason"]


# --------------------------------------------------------------------------- 5
def test_finalize_pair_statuses(roots):
    ev = _event()
    kw = _inject(roots["full"])
    plan = flood.plan_for(ev, **kw)
    tr = plan["track"]
    results = [flood.score_init(tr, "graphcast", i, l, _reader(tr)) for i, l in plan["verified"]]

    df, st = flood.finalize_pair(ev, "graphcast", plan, results)
    assert st["status"] == "scored" and st["rows"] == len(df) > 0

    sparse = flood.plan_for(ev, **_inject(roots["sparse"]))
    df, st = flood.finalize_pair(ev, "graphcast", sparse, [])
    assert df is None and st["status"] == "empty" and "PARDIG" in st["reason"]

    nd = [{"status": "no_data", "reason": "x", "samples": [], "n_days_fill": 0} for _ in plan["verified"]]
    df, st = flood.finalize_pair(ev, "graphcast", plan, nd)
    assert df is None and st["status"] == "no_data"

    err = [{"status": "error", "reason": "RuntimeError: boom", "samples": [], "n_days_fill": 0}] + results
    df, st = flood.finalize_pair(ev, "graphcast", plan, err)
    assert df is None and st["status"] == "read_error" and "boom" in st["reason"]

    gap_plan = flood.plan_for(ev, **_inject(roots["gap"]))
    gres = [flood.score_init(gap_plan["track"], "graphcast", i, l, _reader(gap_plan["track"]))
            for i, l in gap_plan["verified"]]
    df, st = flood.finalize_pair(ev, "graphcast", gap_plan, gres)
    assert st["status"] == "scored" and "PARDIG" in st["reason"]


# --------------------------------------------------------------------------- 6
def test_rows_for_pair_columns_and_dedupe():
    iso1 = dt.datetime(2026, 9, 10).isoformat()
    iso2 = dt.datetime(2026, 9, 11).isoformat()
    samples = [{"init_true": iso1, "lead_true": 24, "metrics": {flood.M_CSI: 1.0, flood.M_POD: 1.0}},
               {"init_true": iso1, "lead_true": 24, "metrics": {flood.M_CSI: 0.0}},
               {"init_true": iso2, "lead_true": 30, "metrics": {flood.M_CSI: 0.5}}]
    df = flood.rows_for_pair("fl1", "flood", "graphcast", samples)
    assert list(df.columns) == tv.SCORE_COLUMNS
    assert [str(t) for t in df.dtypes] == ["object", "object", "object", "object",
                                           "float64", "float64", "datetime64[ns]"]
    assert len(df) == 3                                    # the duplicate (init, lead) collapses
    assert df["event_id"].unique().tolist() == ["fl1"]
    assert df["model"].unique().tolist() == ["graphcast"]
    assert df.loc[df.metric == flood.M_CSI, "value"].tolist() == [1.0, 0.5]
    assert df["init_time"].iloc[0] == np.datetime64("2026-09-10T00:00:00.000000000")


# --------------------------------------------------------------------------- 7
def test_flood_models_graphcast_only():
    names = ["graphcast", "graphcast_gfs", "aurora", "aurora_gfs", "pangu_weather", "pangu_weather_gfs",
             "fourcastnet_v2", "fourcastnet_v2_gfs", "ifs_hres", "aifs_single", "aigfs"]
    ok, bad = flood.flood_models(names)
    assert ok == ["graphcast", "graphcast_gfs"]
    assert set(bad) == set(names[2:])
    assert all("GraphCast only" in v for v in bad.values())


# --------------------------------------------------------------------------- 8
def test_score_flood_event_end_to_end(roots):
    ev = _event()
    plan = flood.plan_for(ev, **_inject(roots["full"]))
    reader = _reader(plan["track"])
    st = {}
    df, status = flood.score_flood_event(ev, ["graphcast", "graphcast_gfs"], reader=reader,
                                         status_out=st, **_inject(roots["full"]))
    assert sorted(df["model"].unique()) == ["graphcast", "graphcast_gfs"]
    assert set(df["metric"].unique()) <= set(flood.FLOOD_METRICS)
    csi = df[df.metric == flood.M_CSI]
    assert len(csi) > 0 and set(csi["value"].unique().tolist()) == {1.0}
    assert not df.isin([np.nan]).any().any()
    assert {m: v["status"] for m, v in status.items()} == {"graphcast": "scored", "graphcast_gfs": "scored"}
    assert st == status and all(status[m]["rows"] > 0 for m in status)
    assert set(df["init_time"].dt.date.unique()) >= {dt.date(2026, 8, 30), dt.date(2026, 9, 14)}
    assert list(df.columns) == tv.SCORE_COLUMNS
