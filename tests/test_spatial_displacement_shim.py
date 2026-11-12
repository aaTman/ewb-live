"""SpatialDisplacement dask-vectorize shim (AR lane blocker, 2026-11-07).

EWB 1.1.1's SpatialDisplacement._compute_metric calls
apply_ufunc(center_of_mass_ufunc, ..., vectorize=True, dask="allowed").
Numpy-backed masks are looped as 2D slices and return the 2-tuple the
signature expects; a DASK-backed mask is handed to the ufunc whole, so
ndimage.center_of_mass sees the full 3D+ core array and returns a
3-element tuple -> "Received a tuple with 3 elements. Expected a tuple
of 2", which zeroed out every model on the large new-set AR events
(a3bb50ccde3d, fbe99f30e625). ewb_live.scoring.evaluate installs a
wrapper that materializes the (small, boolean) masks to numpy first.
"""
from __future__ import annotations

import numpy as np
import xarray as xr
import pytest

import ewb_live.scoring.evaluate  # noqa: F401  (installs the shim)
from extremeweatherbench.metrics import SpatialDisplacement


def _mask(backend: str, roll: int = 0) -> xr.DataArray:
    dims = ("lead_time", "valid_time", "latitude", "longitude")
    lat = np.array([2.0, 1.0, 0.0, -1.0])
    lon = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    data = np.zeros((2, 2, 4, 5), dtype="int64")
    data[0, :, 1:3, 1:3] = 1
    data[1, :, 2:4, 2:4] = 1
    data = np.roll(data, roll, axis=3)
    da = xr.DataArray(
        data, dims=dims,
        coords={"lead_time": [0, 24],
                "valid_time": np.array(["2026-08-01T00", "2026-08-01T06"],
                                       dtype="datetime64[ns]"),
                "latitude": lat, "longitude": lon})
    return (da if backend == "numpy"
            else da.chunk({"lead_time": 2, "valid_time": 2}))


def test_dask_backed_3d_mask_scores_after_shim():
    f, t = _mask("dask", roll=1), _mask("dask")
    assert f.chunks is not None  # stays dask-backed into the metric
    out = SpatialDisplacement()._compute_metric(f, t)
    arr = np.asarray(out if isinstance(out, xr.DataArray) else out)
    assert arr.size >= 2
    assert np.isfinite(arr).all()


def test_numpy_backed_mask_still_works():
    f, t = _mask("numpy", roll=1), _mask("numpy")
    out = SpatialDisplacement()._compute_metric(f, t)
    arr = np.asarray(out if isinstance(out, xr.DataArray) else out)
    assert arr.size >= 2


def test_upstream_unpatched_path_reproduces_the_bug():
    """Documents the EWB 1.1.1 failure the shim guards against: the dask
    path hands the ufunc the full >2D array (N elements != 2 outputs)."""
    from ewb_live.scoring.evaluate import _SD_ORIG

    f, t = _mask("dask"), _mask("dask")
    with pytest.raises(Exception, match="number of outputs"):
        _SD_ORIG(SpatialDisplacement(), f, t)
