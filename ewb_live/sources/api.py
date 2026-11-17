"""Public source API (SPEC section 1).

    list_models() -> list[str]
    available_inits(model, start, end) -> list[datetime]
    open_forecast(model, init, variables, lead_hours=None, bbox=None) -> Dataset
    make_ewb_forecast(model, variables, inits=None, ...) -> ForecastBase

open_forecast returns an xarray.Dataset on EWB conventions:
dims (init_time, lead_time, level?, latitude, longitude); EWB variable names
(surface_air_temperature, specific_humidity, ...); longitudes normalised to
[-180, 180); latitudes descending 90..-90; lead_time timedelta64[ns]; level in
hPa (int). bbox is (lat_min, lat_max, lon_min, lon_max), longitudes in
[-180, 180], wraps the antimeridian when lon_min > lon_max.

Repeat calls are memoised IN-PROCESS only (bounded LRU, ~512 MB default,
``EWB_LIVE_MEM_CACHE_MB``). There is no on-disk forecast-subset cache:
owner ruling 2026-11-17 removed the retired materialised subset tree —
MLWP reads go through the Icechunk virtual store
(:mod:`ewb_live.stores.mlwp_icechunk`) and every other backend re-reads
its source. ``use_cache`` toggles the in-process memo, not a disk cache.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
from collections import OrderedDict

import xarray as xr

from . import aigfs, aiwp, ecmwf
from .registry import LEVEL_VARS_EWB, ModelSpec, models

_BACKENDS = {"aiwp": aiwp, "ecmwf": ecmwf, "aigfs": aigfs}

DEFAULT_LEADS = list(range(0, 241, 6))


def list_models() -> list[str]:
    """Model ids with live data on the stores (verified 2026-11-06)."""
    return sorted(models())


def model_info(model: str) -> ModelSpec:
    m = models()
    if model not in m:
        raise KeyError(f"unknown model {model!r}; known: {sorted(m)}")
    return m[model]


def available_inits(model: str, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Initialisation times (UTC naive datetimes) available in [start, end]."""
    spec = model_info(model)
    if start > end:
        raise ValueError("start must be <= end")
    return _BACKENDS[spec.backend].available_inits(spec, start, end)


def open_forecast_nearest_init(
    model: str,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox: tuple[float, float, float, float] | None = None,
    window_h: int = 12,
    use_cache: bool = True,
):
    """open_forecast with fallback to the nearest EARLIER published init.

    AIWP archives publish on irregular cadences (GFS-forced runs mostly 12Z,
    IFS-forced mostly 00Z/12Z), so a hardcoded 00Z/06Z/18Z init 404s even when
    the model clearly covers the target valid times. Returns
    ``(ds, offset_h)`` where offset_h >= 0 is how many hours earlier the used
    init is: callers must add offset_h to every requested lead to keep the
    SAME valid times (the forecast is then that much older, not mis-dated).
    Raises the original FileNotFoundError if nothing is available in the
    window.
    """
    try:
        return open_forecast(model, init, variables, lead_hours=lead_hours,
                             bbox=bbox, use_cache=use_cache), 0
    except FileNotFoundError as exc0:
        for cand in reversed(available_inits(
                model, init - dt.timedelta(hours=window_h), init)):
            if cand >= init:
                try:
                    return open_forecast(
                        model, cand, variables,
                        lead_hours=[l + int((init - cand).total_seconds() // 3600)
                                    for l in lead_hours],
                        bbox=bbox, use_cache=use_cache), int((init - cand).total_seconds() // 3600)
                except FileNotFoundError:
                    continue
        raise exc0


# ------------------------------------------------------------------ in-process memo
# Bounded LRU replacing the old on-disk subset materialisation. Entries are
# fully materialised Datasets shared between callers; treat them as read-only.
_MEM_CACHE_MAX_BYTES = int(os.environ.get("EWB_LIVE_MEM_CACHE_MB", "512")) * 1024**2
_mem_lock = threading.Lock()
_mem_cache: OrderedDict[str, xr.Dataset] = OrderedDict()
_mem_bytes = 0


def _mem_get(key: str) -> xr.Dataset | None:
    with _mem_lock:
        ds = _mem_cache.get(key)
        if ds is not None:
            _mem_cache.move_to_end(key)
        return ds


def _mem_put(key: str, ds: xr.Dataset) -> None:
    global _mem_bytes
    nbytes = int(ds.nbytes)
    with _mem_lock:
        # an object larger than the whole budget is simply not retained
        if nbytes > _MEM_CACHE_MAX_BYTES:
            return
        _mem_cache[key] = ds
        _mem_bytes += nbytes
        while _mem_bytes > _MEM_CACHE_MAX_BYTES and _mem_cache:
            _k, old = _mem_cache.popitem(last=False)
            _mem_bytes -= int(old.nbytes)


def _materialize(ds: xr.Dataset) -> xr.Dataset:
    """Compute any lazy arrays so the Dataset is safe to retain and share."""
    lazy = any(
        type(v.data).__module__.split(".", 1)[0] in ("dask", "zarr", "fsspec")
        for v in ds.data_vars.values()
    )
    return ds.compute() if lazy else ds


def _cache_key(spec: ModelSpec, init, variables, lead_hours, bbox) -> str:
    payload = json.dumps(
        {
            "model": spec.name,
            "init": init.strftime("%Y%m%d%H"),
            "variables": sorted(variables),
            "leads": sorted(lead_hours),
            "bbox": bbox,
        },
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def open_forecast(
    model: str,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    use_cache: bool = True,
) -> xr.Dataset:
    """Open a forecast as an EWB-convention Dataset (see module docstring)."""
    spec = model_info(model)
    unknown = [v for v in variables if v not in spec.variables]
    if unknown:
        raise ValueError(
            f"variables {unknown} not available for {model!r}; "
            f"available: {list(spec.variables)}"
        )
    if not variables:
        raise ValueError("variables must be a non-empty list")
    leads = list(lead_hours) if lead_hours else list(DEFAULT_LEADS)

    key = _cache_key(spec, init, variables, leads, bbox)
    if use_cache:
        hit = _mem_get(key)
        if hit is not None:
            return hit

    ds = _BACKENDS[spec.backend].open_forecast(spec, init, variables, leads, bbox)
    # reorder level descending (surface -> top) like EWB expects for CAPE paths
    if "level" in ds.dims:
        ds = ds.isel(level=slice(None, None, -1)) if ds.level[0] < ds.level[-1] else ds
    if use_cache:
        ds = _materialize(ds)
        _mem_put(key, ds)
    return ds


def make_ewb_forecast(
    model: str,
    variables: list[str],
    inits: list[dt.datetime],
    lead_hours: list[int] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
):
    """Build an EWB ForecastBase (XarrayForecast) over several inits.

    Concatenates open_forecast output along init_time and wraps it in
    extremeweatherbench.inputs.XarrayForecast, which the EWB evaluation
    machinery consumes directly.
    """
    from extremeweatherbench import inputs as ewb_inputs

    if not inits:
        raise ValueError("inits must be non-empty")
    parts = [
        open_forecast(model, i, variables, lead_hours, bbox)
        for i in sorted(inits)
    ]
    ds = xr.concat(parts, dim="init_time") if len(parts) > 1 else parts[0]
    ds = ds.load()
    return ewb_inputs.XarrayForecast(
        ds=ds,
        name=f"{model} (ewb_live.sources)",
        variables=list(variables),
    )
