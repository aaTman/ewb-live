"""Shared conversions from raw store arrays to EWB-convention Datasets."""

from __future__ import annotations

import numpy as np
import xarray as xr


def normalize_longitude(da):
    """Convert longitudes in [0, 360) to [-180, 180) and sort ascending.

    Accepts Dataset or DataArray (assign_coords/sortby semantics are shared).
    """
    lon = da.longitude.values
    if lon.min() >= 0 and lon.max() >= 180:
        new_lon = np.where(lon > 180, lon - 360, lon)
        da = da.assign_coords(longitude=new_lon)
    return da.sortby(da.longitude)


def lon_indices(lon: np.ndarray, lon_min: float, lon_max: float) -> np.ndarray:
    """Index mask of longitudes within [lon_min, lon_max]; wraps if min > max."""
    lon = (lon + 180.0) % 360.0 - 180.0
    lon_min = (lon_min + 180.0) % 360.0 - 180.0
    lon_max = (lon_max + 180.0) % 360.0 - 180.0
    if lon_min <= lon_max:
        return (lon >= lon_min) & (lon <= lon_max)
    return (lon >= lon_min) | (lon <= lon_max)


def lat_indices(lat: np.ndarray, lat_min: float, lat_max: float) -> np.ndarray:
    return (lat >= lat_min) & (lat <= lat_max)


def subset_latlon(
    da: xr.DataArray, bbox: tuple[float, float, float, float] | None
) -> xr.DataArray:
    """Subset on (lat_min, lat_max, lon_min, lon_max) with wrapped longitudes."""
    if bbox is None:
        return da
    lat_min, lat_max, lon_min, lon_max = bbox
    lat = da.latitude.values
    li = lat_indices(lat, min(lat_min, lat_max), max(lat_min, lat_max))
    da = da.isel(latitude=li)
    lon = da.longitude.values
    oi = lon_indices(lon, lon_min, lon_max)
    return da.isel(longitude=oi)


def assemble(
    arrays: dict[str, xr.DataArray],
    init: np.datetime64,
    leads_hours: list[int],
    bbox: tuple[float, float, float, float] | None,
) -> xr.Dataset:
    """Assemble 2-D/3-D (lead, [level], lat, lon) arrays into an EWB Dataset.

    Each input array must have dims (lead, latitude, longitude) optionally with
    'level'. Output dims: (init_time, lead_time, [level], latitude, longitude).
    """
    out: dict[str, xr.DataArray] = {}
    levels = None
    lead_times = np.array([np.timedelta64(int(h), "h") for h in leads_hours])
    for name, da in arrays.items():
        da = da.rename({"lead": "lead_time"})
        da = normalize_longitude(da)
        da = subset_latlon(da, bbox)
        da = da.expand_dims(init_time=[init]).transpose(
            "init_time", "lead_time", ...
        )
        da = da.assign_attrs(standard_name=name)
        if "level" in da.dims:
            levels = da.level.values
            da = da.transpose("init_time", "lead_time", "level", "latitude", "longitude")
        else:
            da = da.transpose("init_time", "lead_time", "latitude", "longitude")
        out[name] = da
    ds = xr.Dataset(out)
    ds = ds.assign_coords(
        init_time=np.array([init], dtype="datetime64[ns]"),
        lead_time=lead_times.astype("timedelta64[ns]"),
    )
    if levels is not None:
        ds = ds.assign_coords(level=np.asarray(levels).astype("int64"))
        ds["level"].attrs["units"] = "hPa"
    ds.attrs["sources_agent_model_convention"] = (
        "EWB: dims init_time/lead_time/level/latitude/longitude; "
        "longitudes in [-180, 180)"
    )
    return ds
