"""open_truth_window must not read past the end of the truth mirror: the mirror
lags the ARCO cutoff by days, so a window <= cutoff but past the mirror's last
step used to die with KeyError "not all values found in index 'time'" (cold_snap
070121bb3e09, 2026-11-19) and left a stale viz.json behind."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from ewb_live.viz import build as vb


def _ds(t0, n, fill):
    time = np.array([np.datetime64(t0) + np.timedelta64(6 * i, "h") for i in range(n)])
    lat = np.arange(10.0, 14.0, 1.0)
    lon = np.arange(100.0, 104.0, 1.0)
    return xr.Dataset({"2m_temperature": (("time", "latitude", "longitude"),
                                          np.full((n, lat.size, lon.size), fill, "float32"))},
                      coords={"time": time, "latitude": lat, "longitude": lon})


@pytest.fixture
def stores(monkeypatch):
    import ewb_live.scoring.truth as truth
    import ewb_live.scoring.truth_mirror as tm
    mirror = _ds("2026-11-09T00:00", 8, 280.0)        # ends 11-10T18
    analysis = _ds("2026-11-09T00:00", 20, 290.0)     # reaches 11-13
    monkeypatch.setattr(tm, "open_mirror", lambda: mirror)
    monkeypatch.setattr(truth, "build_hres_analysis_zarr", lambda *a, **k: "unused")
    monkeypatch.setattr(vb.xr, "open_zarr", lambda p: analysis)
    return mirror


def _times(start, n):
    t = dt.datetime.fromisoformat(start)
    return [t + dt.timedelta(hours=6 * i) for i in range(n)]


BBOX = (10.0, 13.0, 100.0, 103.0)
CUTOFF = dt.datetime(2026, 11, 13, 23)


def test_window_inside_the_mirror_still_uses_it(stores):
    _t, _lon, _lat, src = vb.open_truth_window(_times("2026-11-09T00:00", 4), BBOX, CUTOFF)
    assert src == "era5_mirror"


def test_window_past_the_mirror_end_falls_back_to_the_analysis(stores):
    t, _lon, _lat, src = vb.open_truth_window(_times("2026-11-10T00:00", 12), BBOX, CUTOFF)
    assert src == "ifs_hres_analysis" and t.shape[0] == 12
