"""Offline tests for the ecmwf early bbox cut (peak-RAM fix, no network).

The ecmwf-forecasts GRIB byte ranges are GLOBAL fields, so open_forecast
now crops every decoded per-lead field to the bbox BEFORE xr.concat
(ecmwf._cut_decoded). These tests pin the contract: the early cut is a
no-op on the RESULT compared with the old end-of-pipeline subset inside
assemble() — normal bbox, antimeridian-wrapping bbox, and bbox=None.
"""

import datetime as dt

import numpy as np
import xarray as xr

from ewb_live.sources import ecmwf

# ECMWF open-data 0.25 deg grid, exactly as the backend sees it per lead:
# latitudes descending 90..-90, longitudes native 0..359.75.
LAT = np.arange(90.0, -90.01, -0.25)  # 721 pts, descending
LON = np.arange(0.0, 360.0, 0.25)  # 1440 pts, native 0..360
LEADS = [0, 6, 12]
LEVELS = [1000, 850, 500]
INIT = dt.datetime(2026, 11, 14, 0)

# the 2026-11-22 weekly's antimeridian AR 89e3f844a59a (141.7 GB peak job)
WRAP_BBOX = (23.25, 65.75, 173.5, -139.5)
PLAIN_BBOX = (20.0, 40.0, -120.0, -80.0)


def _make_parts() -> tuple[dict, dict]:
    """Synthetic global per-lead parts like _decode_grib produces.

    Values encode the raw grid cell (lat_index, lon_index) plus lead so any
    coordinate mix-up changes the data, not just the coords.
    """
    li, oi = np.meshgrid(np.arange(len(LAT)), np.arange(len(LON)), indexing="ij")
    surface_parts: dict[int, dict[str, xr.DataArray]] = {}
    level_parts: dict[int, dict[str, xr.DataArray]] = {}
    for n, h in enumerate(LEADS):
        base = (li * 1000000 + oi * 10 + n).astype(np.float64)
        surface_parts[h] = {
            "surface_air_temperature": xr.DataArray(
                base, dims=("latitude", "longitude"),
                coords={"latitude": LAT, "longitude": LON},
            ),
        }
        pl = np.stack([base * (k + 1) / 1000.0 for k in range(len(LEVELS))])
        level_parts[h] = {
            "eastward_wind": xr.DataArray(
                pl, dims=("level", "latitude", "longitude"),
                coords={"level": LEVELS, "latitude": LAT, "longitude": LON},
            ),
        }
    return surface_parts, level_parts


def _assemble(cut_first: bool, bbox) -> xr.Dataset:
    surface_parts, level_parts = _make_parts()
    if cut_first and bbox is not None:
        # what open_forecast._read_lead now does per decoded lead
        surface_parts = {h: ecmwf._cut_decoded(p, bbox) for h, p in surface_parts.items()}
        level_parts = {h: ecmwf._cut_decoded(p, bbox) for h, p in level_parts.items()}
    return ecmwf._assemble_grib(surface_parts, level_parts, INIT, LEADS, bbox)


def test_early_cut_identity_plain_bbox():
    xr.testing.assert_identical(_assemble(False, PLAIN_BBOX), _assemble(True, PLAIN_BBOX))


def test_early_cut_identity_wrapping_bbox():
    """The antimeridian arc survives the early cut with identical results."""
    xr.testing.assert_identical(_assemble(False, WRAP_BBOX), _assemble(True, WRAP_BBOX))


def test_early_cut_identity_no_bbox():
    """bbox=None = global, byte-for-byte unchanged (contract for truth.py)."""
    xr.testing.assert_identical(_assemble(False, None), _assemble(True, None))
    ds = _assemble(True, None)
    assert ds.latitude.values.size == len(LAT)
    # global output is normalised to [-180, 180) ascending as before
    assert ds.longitude.values.min() == -179.75 and ds.longitude.values.max() == 180.0


def test_wrapping_bbox_keeps_the_antimeridian_arc():
    """The exact coordinate set the old end-of-pipeline subset produced."""
    ds = _assemble(True, WRAP_BBOX)
    for da in (ds["surface_air_temperature"], ds["eastward_wind"]):
        got = da.longitude.values
        exp = np.concatenate(
            [np.arange(-179.75, -139.5 + 0.125, 0.25), np.arange(173.5, 180.0 + 0.125, 0.25)]
        )
        np.testing.assert_array_equal(got, exp)
        lat = da.latitude.values
        np.testing.assert_array_equal(
            lat, np.arange(65.75, 23.25 - 0.125, -0.25)
        )  # descending, like the raw grid
    # arc sanity: the wrapped box does NOT contain the far side of the globe
    lons = set(ds.longitude.values.round(2).tolist())
    assert 179.75 in lons and -179.75 in lons and 0.0 not in lons


def test_plain_bbox_coordinate_set():
    ds = _assemble(True, PLAIN_BBOX)
    np.testing.assert_array_equal(
        ds.longitude.values, np.arange(-120.0, -80.0 + 0.125, 0.25)
    )
    np.testing.assert_array_equal(
        ds.latitude.values, np.arange(40.0, 20.0 - 0.125, -0.25)
    )


def test_cut_decoded_preserves_shared_arrays():
    """_decode_grib aliases one array under two keys: cut each object once."""
    da = xr.DataArray(
        np.zeros((len(LAT), len(LON))),
        dims=("latitude", "longitude"),
        coords={"latitude": LAT, "longitude": LON},
    )
    other = xr.DataArray(
        np.ones((len(LAT), len(LON))),
        dims=("latitude", "longitude"),
        coords={"latitude": LAT, "longitude": LON},
    )
    out = ecmwf._cut_decoded({"2t": da, "t2m": da, "msl": other}, WRAP_BBOX)
    assert out["2t"] is out["t2m"]
    assert out["msl"] is not out["2t"]
    assert set(out) == {"2t", "t2m", "msl"}


def test_cut_decoded_subset_shape_shrinks():
    """The parts held between decode and concat are bbox-sized, not global."""
    surface_parts, level_parts = _make_parts()
    cut = ecmwf._cut_decoded(surface_parts[0], WRAP_BBOX)
    da = cut["surface_air_temperature"]
    frac = (da.latitude.size * da.longitude.size) / (len(LAT) * len(LON))
    assert frac < 0.12  # ~11% box: this is the whole point of the fix
