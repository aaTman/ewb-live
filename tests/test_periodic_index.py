"""PeriodicBoundaryIndex seam tests (owner spec 2026-11-09):
350E-20E and 170E-170W crops wrap correctly, one eastward run,
no duplicate seam column."""
import numpy as np
import pytest
import xarray as xr

from ewb_live.periodic_index import PeriodicBoundaryIndex, attach_periodic


def _grid(lon0, n=1440, res=0.25):
    lon = lon0 + res * np.arange(n)
    da = xr.DataArray(np.arange(n, dtype=float), dims="longitude",
                      coords={"longitude": lon}, name="v")
    return attach_periodic(da)


@pytest.fixture
def g0():
    return _grid(0.0)          # ERA5 mirror convention 0..359.75


@pytest.fixture
def g180():
    return _grid(-180.0)       # -180..179.75


def test_wrap_identity_and_scalars(g0):
    # in-range exact sel unchanged (the fixed wrap formula)
    assert float(g0.sel(longitude=347.5).values) == 347.5 / 0.25
    # out-of-range scalars wrap to the same cell
    assert g0.sel(longitude=376.25).values == g0.sel(longitude=16.25).values
    assert g0.sel(longitude=-10).values == g0.sel(longitude=350).values


def test_350E_20E_crop_one_eastward_run_no_seam_dup(g0):
    sub = g0.sel(longitude=slice(350.0, 380.0))   # NOT slice(350, 20)
    lons = np.asarray(sub.longitude)
    assert len(lons) == 121                       # 350..359.75 + 0..20
    assert len(set(lons.tolist())) == len(lons)   # no duplicate seam column
    assert lons[0] == 350.0 and lons[-1] == 20.0
    # contiguous eastward: consecutive cells 0.25 deg apart modulo 360
    d = np.diff(lons) % 360
    assert np.allclose(np.where(d > 180, d - 360, d), 0.25)


def test_170E_170W_on_minus180_grid(g180):
    sub = g180.sel(longitude=slice(170.0, 190.0))   # 190 == -170 wrapped
    lons = np.asarray(sub.longitude)
    assert lons[0] == 170.0 and lons[-1] == -170.0
    assert len(set(lons.tolist())) == len(lons)
    assert len(lons) == 81                        # 170..179.75 + -180..-170


def test_plain_slice_unchanged(g0):
    sub = g0.sel(longitude=slice(10.0, 20.0))
    assert float(sub.longitude.min()) == 10.0 and float(sub.longitude.max()) == 20.0


def test_inverted_slice_empty(g0):
    assert g0.sel(longitude=slice(350.0, 20.0)).size == 0  # documented convention


def test_attach_idempotent_and_repr(g0):
    again = attach_periodic(g0)
    assert isinstance(again.xindexes["longitude"], PeriodicBoundaryIndex)
    assert "period=360" in repr(again.xindexes["longitude"])


def test_open_ended_slices_pass_through(g0):
    assert g0.sel(longitude=slice(None, 20.0)).sizes["longitude"] == 81
    assert g0.sel(longitude=slice(350.0, None)).sizes["longitude"] == 40
    assert g0.sel(longitude=slice(None, None)).sizes["longitude"] == 1440


def test_lon_slice_arc_to_continuous(g0):
    from ewb_live.periodic_index import lon_slice
    g = np.arange(0.0, 360.0, 1.0)
    assert lon_slice(g, 350.0, 20.0) == slice(350.0, 380.0)   # wrap eastward
    assert lon_slice(g, 10.0, 20.0) == slice(10.0, 20.0)      # plain
    assert lon_slice(g, -10.0, 10.0) == slice(350.0, 370.0)   # -180 input, 0-360 grid
    w = np.arange(-180.0, 180.0, 1.0)
    assert lon_slice(w, 170.0, -170.0) == slice(170.0, 190.0)
    sub = g0.sel(longitude=lon_slice(np.asarray(g0.longitude.values, float),
                                     350.0, 20.0))
    # 0.25-deg grid: 350..359.75 (40 cols) + 0..20 (81 cols), seam once
    assert sub.sizes["longitude"] == 121         # no duplicated seam column
    lons = np.asarray(sub.longitude.values, float)
    assert lons[0] == 350.0 and lons[40] == 0.0  # eastward order across seam
