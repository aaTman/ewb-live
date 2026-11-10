"""Dev-only forecast adapter (SPEC section 1 stand-in).

Wraps ARCO ERA5 as a fake "forecast" on EWB conventions so the scoring path
can be exercised end-to-end without the real ``ewb_live.sources`` loader.
The forecast is ERA5-as-perfect-predictor: for init T and lead L the field
value is ERA5 at valid time T+L. Values land on the ERA5 grid, so metrics
against the ERA5 target come out ~0 — ideal for wiring tests.

Not for production scoring: use ewb_live.sources.build_forecast
(ewb_live.scoring.evaluate.build_forecast).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from extremeweatherbench import derived as ewb_derived
from extremeweatherbench import inputs as ewb_inputs

_ARCO_RAW = {
    "surface_air_temperature": "2m_temperature",
    "eastward_wind": "u_component_of_wind",
    "northward_wind": "v_component_of_wind",
    "specific_humidity": "specific_humidity",
}


def make_dev_forecast(
    name: str,
    variables: list[str],
    event_start: dt.datetime,
    event_end: dt.datetime,
    *,
    lead_hours: list[int] | None = None,
    bbox: tuple[float, float, float, float] = (-90, 90, -180, 180),
    arco_uri: str | None = None,
    perturbation: float = 0.0,
    seed: int = 0,
) -> ewb_inputs.XarrayForecast:
    """XarrayForecast of ERA5 delayed to act as forecasts at daily 00Z inits.

    Args:
        name: model name reported as forecast_source.
        variables: EWB standard names (subset of _ARCO_RAW keys).
        event_start/event_end: event window; inits are the daily 00Z times in
            [start - max_lead, end).
        lead_hours: lead grid, default [0, 6, 12, 24].
        bbox: (lat_min, lat_max, lon_min, lon_max) with lon in [-180, 180).
        perturbation: add N(0, perturbation) K to surface_air_temperature
            (only for smoke tests that need nonzero error).
    """
    arco_uri = arco_uri or ewb_inputs.ARCO_ERA5_FULL_URI
    lead_hours = lead_hours or [0, 6, 12, 24]
    max_lead = max(lead_hours)
    raw_vars = [_ARCO_RAW[v] for v in variables]

    raw = xr.open_zarr(arco_uri, storage_options={"token": "anon"}, chunks=None)
    lat0, lat1, lon0, lon1 = bbox
    lon0 %= 360.0
    lon1 %= 360.0
    # seam crop via PeriodicBoundaryIndex (owner 2026-11-09): one continuous
    # slice, the index splits wrap arcs — replaces the manual two-part concat
    from ewb_live.periodic_index import attach_periodic, lon_slice

    ds = attach_periodic(raw)[raw_vars].sel(
        latitude=slice(max(lat0, lat1), min(lat0, lat1)),  # ARCO lat descending
        longitude=lon_slice(np.asarray(raw.longitude.values, float), lon0, lon1),
    )

    init0 = (event_start - dt.timedelta(hours=max_lead)).replace(hour=0, minute=0, second=0)
    inits = pd.date_range(init0, event_end - pd.Timedelta(hours=1), freq="D")
    leads = pd.to_timedelta(lead_hours, unit="h")
    valid = sorted({i + l for i in inits for l in leads})

    sub = ds.sel(time=slice(valid[0], valid[-1]))
    # materialise the small region (lazy dask on ARCO)
    sub = sub.compute()
    if perturbation and "2m_temperature" in sub:
        rng = np.random.default_rng(seed)
        sub["2m_temperature"] = sub["2m_temperature"] + rng.normal(0, perturbation, sub["2m_temperature"].shape)

    parts = []
    for init in inits:
        vts = [init + l for l in leads]
        block = sub.sel(time=vts)
        block = block.rename({"time": "lead_time"})
        block = block.assign_coords(lead_time=leads)  # timedelta coordinate
        arr = block.expand_dims(init_time=[np.datetime64(init)])
        parts.append(arr)
    out = xr.concat(parts, dim="init_time")
    out = out.rename({raw: ewb for ewb, raw in _ARCO_RAW.items() if raw in out})
    out = out.transpose("init_time", "lead_time", ...)

    ewb_vars: list[Any]
    if {"eastward_wind", "northward_wind", "specific_humidity"} <= set(variables):
        ewb_vars = list(variables) + [
            ewb_derived.AtmosphericRiverVariables(
                output_variables=["atmospheric_river_land_intersection"]
            )
        ]
    else:
        ewb_vars = list(variables)
    return ewb_inputs.XarrayForecast(ds=out, name=name, variables=ewb_vars)
