"""Tropical exclusion of the shared AR mask (guardian-design G-40 investigation,
owner 2026-11-08). The ONE function all consumers share is
extremeweatherbench build_atmospheric_river_mask_and_land_intersection, whose
finalization drops connected objects with >=500 px but |pixel-weighted mean
lat| <= 15 (upstream events/atmospheric_river.py:101-129). These tests pin
that behavior so detection (events/ar.py), scoring (AtmosphericRiverVariables)
and the raw exceedance map (viz/exceedance.py) can never silently diverge."""

import numpy as np
import pytest
import xarray as xr

from extremeweatherbench.events.atmospheric_river import (
    build_atmospheric_river_mask_and_land_intersection,
)

LEVELS = np.arange(1000.0, 500.0, -50.0)  # 10 levels is enough for the mask


def _ds_with_plume(clat: float) -> xr.Dataset:
    """Synthetic saturated jet: a 10x10-deg IVT plume at 0.25 deg (the
    resolution the upstream Laplacian kernel is calibrated for)."""
    lats = np.arange(-90.0, 90.0, 0.25)
    lons = np.arange(0.0, 360.0, 0.25)
    shape = (1, len(lats), len(lons), len(LEVELS))
    q = np.full(shape, 1e-4, np.float32)
    u = np.zeros(shape, np.float32)
    v = np.zeros(shape, np.float32)
    band = (np.abs(lats[:, None] - clat) < 5.0) & (np.abs(lons[None, :] - 180.0) < 5.0)
    q[0][band] = 20e-3
    u[0][band] = 25.0
    out = xr.Dataset(
        {
            "eastward_wind": (("valid_time", "latitude", "longitude", "level"), u,
                              {"units": "m s-1"}),
            "northward_wind": (("valid_time", "latitude", "longitude", "level"), v,
                               {"units": "m s-1"}),
            "specific_humidity": (("valid_time", "latitude", "longitude", "level"), q,
                                  {"units": "kg kg-1"}),
        },
        coords={"valid_time": ["2026-09-01T00:00"], "latitude": lats,
                "longitude": lons, "level": LEVELS},
    )
    return out


@pytest.mark.parametrize("clat,expect", [(5.0, False), (35.0, True)])
def test_shared_mask_drops_tropical_plume_keeps_midlatitude(clat, expect):
    # needs the 0.25-deg assumption of the upstream Laplacian kernel
    out = build_atmospheric_river_mask_and_land_intersection(
        _ds_with_plume(clat), output_variables=["atmospheric_river_mask"])
    px = int((out["atmospheric_river_mask"] > 0).sum())
    assert (px > 0) is expect, f"clat={clat}: mask px={px}"
