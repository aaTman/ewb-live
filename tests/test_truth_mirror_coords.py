"""Coordinate-integrity guard for the ERA5 truth mirror (science-review B2).

The mirror's coordinate arrays were once created with zarr's default
numeric fill_value (0.0), so xarray's decode masked the real 0.0 values
(longitude index 0 = 0E, latitude index 360 = equator) as NaN for every
consumer. ensure_coord_integrity() must detect, repair, and never touch
a healthy store.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import zarr

from ewb_live.scoring.truth_mirror import (
    _coord_integrity_status,
    ensure_coord_integrity,
)


def _make_store(mp: Path, *, coord_fill: float | None = 0.0,
                corrupt_first_lon=False, corrupt_eq_lat=False) -> None:
    mp.mkdir(parents=True, exist_ok=True)
    lat = (90.0 - 0.25 * np.arange(721)).astype("float32")
    lon = (0.25 * np.arange(1440)).astype("float32")
    lv = np.array([1000, 925, 850, 700, 500, 300], dtype="int64")
    if corrupt_first_lon:
        lon[0] = np.nan
    if corrupt_eq_lat:
        lat[360] = np.nan
    g = zarr.open_group(str(mp), mode="w", zarr_format=2)
    t = g.create_array("time", shape=(0,), chunks=(24,), dtype="datetime64[ns]",
                       fill_value=None)
    t.attrs.put({"_ARRAY_DIMENSIONS": ["time"]})
    for name, arr in (("latitude", lat), ("longitude", lon), ("level", lv)):
        a = g.create_array(name, shape=arr.shape, chunks=arr.shape,
                           dtype=arr.dtype, fill_value=coord_fill)
        a[...] = arr
        a.attrs.put({"_ARRAY_DIMENSIONS": [name]})
    a = g.create_array("2m_temperature", shape=(0, 721, 1440),
                       chunks=(1, 721, 1440), dtype="float32")
    a.attrs.put({"_ARRAY_DIMENSIONS": ["time", "latitude", "longitude"]})


def test_fill_value_zero_is_detected_and_repaired(tmp_path):
    mp = tmp_path / "era5.zarr"
    _make_store(mp, coord_fill=0.0)
    ok, problems = _coord_integrity_status(mp)
    assert not ok and any("_FillValue" in p for p in problems)

    assert ensure_coord_integrity(mp) is True
    ok, problems = _coord_integrity_status(mp)
    assert ok, problems
    # repair must be idempotent
    assert ensure_coord_integrity(mp) is False
    # the repaired metadata no longer masks real zeros
    meta = json.loads((mp / "longitude" / ".zarray").read_text())
    assert meta.get("_FillValue", meta.get("fill_value")) is None
    # and values are still the true grid
    g = zarr.open_group(str(mp), mode="r")
    assert float(np.asarray(g["longitude"])[0]) == 0.0
    assert float(np.asarray(g["latitude"])[360]) == 0.0


def test_nan_coord_values_regenerated_from_canonical_grid(tmp_path):
    mp = tmp_path / "era5.zarr"
    _make_store(mp, coord_fill=0.0, corrupt_first_lon=True, corrupt_eq_lat=True)
    ok, problems = _coord_integrity_status(mp)
    assert not ok and any("non-finite" in p for p in problems)

    assert ensure_coord_integrity(mp) is True
    lat = np.asarray(zarr.open_array(store=str(mp / "latitude"), mode="r"))
    lon = np.asarray(zarr.open_array(store=str(mp / "longitude"), mode="r"))
    np.testing.assert_allclose(lat, 90.0 - 0.25 * np.arange(721), atol=1e-5)
    np.testing.assert_allclose(lon, 0.25 * np.arange(1440), atol=1e-5)
    # xarray round-trip: no masked zeros anymore
    ds = xr.open_zarr(str(mp), consolidated=False)
    assert np.isfinite(ds.longitude.values).all()
    assert np.isfinite(ds.latitude.values).all()


def test_healthy_store_untouched(tmp_path):
    mp = tmp_path / "era5.zarr"
    _make_store(mp, coord_fill=None)
    assert ensure_coord_integrity(mp) is False


def test_missing_store_is_noop(tmp_path):
    assert ensure_coord_integrity(tmp_path / "absent.zarr") is False
