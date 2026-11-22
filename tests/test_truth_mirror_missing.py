"""A point ARCO serves as entirely NaN must never be recorded as complete: it is
retried every run, and the mirror keeps covering it so scoring does not fall back."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import xarray as xr
import zarr

from ewb_live.scoring import truth_mirror as tm

ORIGIN = dt.datetime(2026, 10, 31)
END = dt.datetime(2026, 11, 1)
GAP = dt.datetime(2026, 10, 31, 12)


def _source(gap: bool) -> xr.Dataset:
    time = np.array([np.datetime64(ORIGIN) + np.timedelta64(6 * i, "h") for i in range(5)],
                    dtype="datetime64[ns]")
    data = {"2m_temperature": (("time", "latitude", "longitude"), np.full((5, 2, 2), 280.0, "f4"))}
    for v in tm.MIRROR_VARS[1:]:
        data[v] = (("time", "level", "latitude", "longitude"), np.full((5, 1, 2, 2), 1.0, "f4"))
    ds = xr.Dataset(data, coords={"time": time, "level": [500], "latitude": [10.0, 9.0],
                                  "longitude": [0.0, 1.0]})
    if gap:
        for v in tm.MIRROR_VARS:
            ds[v].loc[{"time": np.datetime64(GAP)}] = np.nan
    return ds


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_CACHE", str(tmp_path))
    monkeypatch.delattr(tm._tls, "grp", raising=False)
    mp = tm.mirror_path()
    mp.parent.mkdir(parents=True)
    g = zarr.open_group(str(mp), mode="w", zarr_format=2)
    g.create_array("time", shape=(0,), chunks=(24,), dtype="datetime64[ns]")
    g.create_array("2m_temperature", shape=(0, 2, 2), chunks=(1, 2, 2), dtype="float32")
    for v in tm.MIRROR_VARS[1:]:
        g.create_array(v, shape=(0, 1, 2, 2), chunks=(1, 1, 2, 2), dtype="float32")
    g.attrs["origin"] = tm._key(ORIGIN)
    return g


def _sync(monkeypatch, gap: bool) -> dict:
    src = _source(gap)
    monkeypatch.setattr(tm, "_thread_source", lambda: src)
    return tm.ensure_mirror(end=END, days=1, lookback_days=0, cutoff=END, threads=2)


def _t2m(idx: int) -> np.ndarray:
    return np.asarray(zarr.open_group(str(tm.mirror_path()), mode="r")["2m_temperature"][idx])


def test_all_nan_point_is_unavailable_not_complete(mirror, monkeypatch):
    summary = _sync(monkeypatch, gap=True)
    man = tm.load_manifest()
    assert summary["unavailable"] == 4 and summary["errors"] == 0
    assert tm._key(GAP) not in man["complete"]
    assert sorted(man["unavailable"][tm._key(GAP)]) == sorted(tm.MIRROR_VARS)
    assert np.isnan(_t2m(2)).all()                  # NaN, never the zarr fill value
    assert tm.mirror_covers(ORIGIN, END)            # scoring keeps reading the mirror


def test_unavailable_point_is_refetched_once_the_source_has_it(mirror, monkeypatch):
    _sync(monkeypatch, gap=True)
    summary = _sync(monkeypatch, gap=False)
    man = tm.load_manifest()
    assert summary["copied"] == 4 and summary["unavailable"] == 0
    assert man["unavailable"] == {}
    assert sorted(man["complete"][tm._key(GAP)]) == sorted(tm.MIRROR_VARS)
    assert (_t2m(2) == 280.0).all()


def test_requeue_finds_points_already_marked_complete(mirror, monkeypatch):
    _sync(monkeypatch, gap=True)
    man = tm.load_manifest()
    man["complete"][tm._key(GAP)] = man["unavailable"].pop(tm._key(GAP))
    tm.save_manifest(man)
    assert tm.requeue_all_nan(ORIGIN, END) == 4
    man = tm.load_manifest()
    assert man["complete"][tm._key(GAP)] == []
    assert sorted(man["unavailable"][tm._key(GAP)]) == sorted(tm.MIRROR_VARS)
    assert tm.requeue_all_nan(ORIGIN, END) == 0
    _sync(monkeypatch, gap=False)
    assert (_t2m(2) == 280.0).all() and tm.load_manifest()["unavailable"] == {}
