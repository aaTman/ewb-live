"""Disk cache for expensive ECMWF forecast reads (performance).

AR jobs re-fetch the *same* ifs_hres / aifs_single (init, variables)
combinations for every overlapping event — the dominant cost after the
truth mirror. This layer caches the decoded dataset at
``data/cache/fc_parts/`` keyed by (model, init, variables, region,
lead-set), fetching exactly the REQUESTED leads.

(Early version fetched the FULL 0-240 h set per key to make any event
window a slice. Measured on the real run 2026-11-06 and reverted: the
ecmwf-forecasts GRIB byte ranges are GLOBAL fields — region padding
saves no transfer — so a full-lead pressure-level fetch moves ~8 GB per
(model, init) and AR jobs need ~10 inits x 2 models of them; first-pass
jobs ran 30+ min with ~10 GB RSS and zero output. Lead windows also
differ per (event, init), so cross-event key reuse never materialised.
Per-request leads cut the first pass ~5x and cap decode memory.)

Grids are fetched for a 10-degree-padded region box and re-cut to the
exact event bbox on read, so nearby events share one object. Cache value
is incremental re-runs (same events re-scored after new data) rather
than cross-event sharing.

Writes are tmp-dir + atomic rename, so concurrent jobs racing on the
same key are harmless. Only ECMWF-backed models are cached: the AIWP
store path is already local and fast.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import shutil
from pathlib import Path

import xarray as xr

logger = logging.getLogger(__name__)

CACHE_MODELS = ("ifs_hres", "aifs_single")
_PAD = 10.0


def _cache_dir() -> Path:
    return Path(os.environ.get("EWB_LIVE_CACHE", "data/cache")) / "fc_parts"


def _region_key(bbox: tuple[float, float, float, float]) -> tuple | None:
    lat_min, lat_max, lon_min, lon_max = bbox
    if lon_min > lon_max:  # antimeridian-wrapping: don't cache
        return None
    import math

    return (
        math.floor(min(lat_min, lat_max) / _PAD) * _PAD,
        math.ceil(max(lat_min, lat_max) / _PAD) * _PAD,
        math.floor(lon_min / _PAD) * _PAD,
        math.ceil(lon_max / _PAD) * _PAD,
    )


def _cut(ds: xr.Dataset, bbox) -> xr.Dataset:
    from ewb_live.sources import _transform

    lat_min, lat_max, lon_min, lon_max = bbox
    li = _transform.lat_indices(ds.latitude.values, min(lat_min, lat_max), max(lat_min, lat_max))
    oi = _transform.lon_indices(ds.longitude.values, lon_min, lon_max)
    return ds.isel(latitude=li, longitude=oi)


def _lead_sel(ds: xr.Dataset, leads: list[int]):
    import numpy as np

    lt = ds.lead_time.values
    if np.issubdtype(lt.dtype, np.timedelta64):
        return [np.timedelta64(h, "h") for h in leads]
    if np.issubdtype(lt.dtype, np.datetime64):
        init = np.datetime64(ds.init_time.values[0], "h") if "init_time" in ds else lt[0]
        return [init + np.timedelta64(h, "h") for h in leads]
    return list(leads)


def open_forecast_cached(model: str, init: dt.datetime, variables: list[str],
                         leads: list[int], bbox) -> xr.Dataset:
    """sources.open_forecast with a (model, init, vars) disk cache for
    ECMWF-backed models; identical return contract (exact-bbox cut,
    requested leads only)."""
    from ewb_live import sources

    region = _region_key(tuple(bbox)) if model in CACHE_MODELS else None
    if region is None:
        return sources.open_forecast(model, init, variables, leads, bbox)

    vkey = hashlib.md5(",".join(sorted(variables)).encode()).hexdigest()[:10]
    rkey = "x".join(str(int(v)) for v in region)
    lkey = hashlib.md5(",".join(str(int(h)) for h in leads).encode()).hexdigest()[:8]
    path = _cache_dir() / f"{model}_{init:%Y%m%d%H}Z_{vkey}_{rkey}_{lkey}.zarr"
    if path.exists():
        try:
            ds = xr.open_zarr(path, consolidated=False)
            return _cut(ds, bbox).sel(lead_time=_lead_sel(ds, leads)).load()
        except Exception as exc:  # noqa: BLE001 - corrupt cache entry: refetch
            logger.warning("fc_parts cache read failed (%s); refetching", exc)
            shutil.rmtree(path, ignore_errors=True)

    lat_min, lat_max, lon_min, lon_max = bbox
    region_bbox = (region[0], region[1], region[2], region[3])
    if region_bbox != tuple(bbox):
        logger.info("fc_parts: padded fetch %s %s region %s", model,
                    f"{init:%m-%d %H}Z", region_bbox)
    ds = sources.open_forecast(model, init, variables, list(leads), region_bbox)
    _cache_dir().mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zarr.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for v in ds.variables:
            ds[v].encoding = {}
        ds.to_zarr(tmp, zarr_format=3, mode="w")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 - cache write must not break scoring
        logger.warning("fc_parts cache write failed: %s", exc)
        shutil.rmtree(tmp, ignore_errors=True)
    # re-open from the just-written store so the read path is identical
    fresh = path.exists()
    base = xr.open_zarr(path, consolidated=False) if fresh else ds
    out = _cut(base, bbox).sel(lead_time=_lead_sel(base, leads)).load()
    return out
