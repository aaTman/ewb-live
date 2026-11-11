"""Truth-source policy for scoring (SPEC section 2).

Primary truth is EWB's default ERA5 target (ARCO ERA5, anonymous GCS).
ARCO lags real time by ~6 days; for event windows that extend past the
last fully-available ARCO hour, truth falls back to IFS HRES 0-h analysis
(ECMWF open data, via ``ewb_live.sources``), materialised locally as an
ARCO-shaped zarr and consumed through EWB's own ``inputs.ERA5`` target
class so the whole EWB pipeline still does the subsetting/alignment.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from extremeweatherbench import inputs as ewb_inputs

logger = logging.getLogger(__name__)

PROBE_POINTS = [(35.0, 260.0), (-20.0, 130.0), (55.0, 10.0), (10.0, 280.0)]
CUTOFF_TTL_HOURS = 12


def probe_arco_cutoff(*, force: bool = False, now: dt.datetime | None = None) -> dt.datetime:
    """Last ARCO hour with data everywhere probed (naive UTC).

    Delegates to the single canonical probe in ``ewb_live.events.truth``
    (bisect + 4 open-ocean points + ``data/cache/truth_cutoff.json`` cache).
    """
    from ..events.truth import truth_cutoff

    return truth_cutoff(now=now, force=force)


def truth_source_for_event(event, cutoff: dt.datetime) -> str:
    """'era5_arco' if the whole event window has ARCO data, else 'ifs_hres_analysis'.

    Event.end is exclusive midnight after the last active day, so the last
    valid instant scored is end - 1h; ARCO covering that instant suffices.
    """
    return "era5_arco" if event.end - dt.timedelta(hours=1) <= cutoff else "ifs_hres_analysis"


def _analysis_window(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """6-hourly valid times covering [start, end)."""
    t = start.replace(minute=0, second=0, microsecond=0)
    t = t - dt.timedelta(hours=t.hour % 6) + dt.timedelta(hours=t.hour % 6)
    out = []
    while t < end:
        out.append(t)
        t += dt.timedelta(hours=6)
    return out


def build_hres_analysis_zarr(
    start: dt.datetime,
    end: dt.datetime,
    variables: list[str],
    bbox: tuple[float, float, float, float],
    *,
    model: str = "ifs_hres",
    cache_dir: str | Path | None = None,
) -> str:
    """Materialise IFS HRES 0-h analysis (ARCO-shaped zarr) for [start, end).

    ``variables`` uses EWB standard names. Returns the local zarr path.
    """
    from ewb_live import sources

    cache_dir = Path(cache_dir or (Path(os.environ.get("EWB_LIVE_CACHE", "data/cache")) / "truth"))
    # bbox IS part of the key: stores are region-cropped, and two events in
    # the same window on different continents must never share one
    # (ponytail 2026-11-08 — second caller got the first region's crop)
    la0, la1, on0, on1 = bbox
    key = (f"hres_analysis_{start:%Y%m%d%H}_{end:%Y%m%d%H}_"
           f"{'-'.join(sorted(variables))}_"
           f"{la0:+.1f}_{la1:+.1f}_{on0:+.1f}_{on1:+.1f}")
    zpath = cache_dir / f"{key}.zarr"
    if zpath.exists():
        return str(zpath)
    cache_dir.mkdir(parents=True, exist_ok=True)

    times = _analysis_window(start, end)
    parts = []
    for ts in times:
        run = ts.replace(hour=ts.hour - ts.hour % 6)
        lead = int((ts - run).total_seconds() // 3600)
        ds = sources.open_forecast(model, run, variables, lead_hours=[lead], bbox=bbox)
        ds = ds.squeeze(["init_time", "lead_time"], drop=True)
        ds = ds.assign_coords(time=np.datetime64(ts))
        ds = ds.rename({"valid_time": "time"}) if "valid_time" in ds.dims else ds.expand_dims("time")
        parts.append(ds)
    window = xr.concat(parts, dim="time")
    # ARCO-style raw variable names so EWB's ERA5 variable_mapping applies
    ewb_to_arco = {
        "surface_air_temperature": "2m_temperature",
        "eastward_wind": "u_component_of_wind",
        "northward_wind": "v_component_of_wind",
        "specific_humidity": "specific_humidity",
        "air_temperature": "temperature",
        "relative_humidity": "2m_relative_humidity",
        "air_pressure_at_mean_sea_level": "mean_sea_level_pressure",
    }
    window = window.rename({k: v for k, v in ewb_to_arco.items() if k in window})
    tmp = zpath.with_suffix(".tmp")
    window.to_zarr(tmp, mode="w", consolidated=False)
    tmp.rename(zpath)
    logger.info("materialised HRES analysis truth %s (%d times)", zpath, len(times))
    return str(zpath)


def _cropped_target(default):
    """Wrap an ERA5 target so its data is hard-cropped to the case bbox
    (+2 deg alignment margin) BEFORE EWB derives variables.

    EWB's mask keeps the global grid (drop=False) and computes derived
    fields (IVT etc.) on it — the dominant per-job memory cost. Values
    outside the case region are masked out by EWB anyway, so pre-cropping
    is value-identical inside the region.
    """
    import dataclasses

    class _Cropped(type(default)):
        def subset_data_to_case(self, data, case_metadata, **kwargs):
            loc = getattr(case_metadata, "location", None)
            if (
                hasattr(data, "variables")
                and loc is not None
                and all(
                    hasattr(loc, a)
                    for a in ("latitude_min", "latitude_max",
                              "longitude_min", "longitude_max")
                )
            ):
                data = _crop_bbox(data, loc, margin=2.0)
            return super().subset_data_to_case(data, case_metadata, **kwargs)

    return _Cropped(**{f.name: getattr(default, f.name)
                       for f in dataclasses.fields(default)})


def _crop_bbox(data, loc, margin: float = 2.0):
    import xarray as xr

    if not isinstance(data, (xr.Dataset, xr.DataArray)) or "latitude" not in data.coords:
        return data
    from ewb_live.sources import _transform

    lat0 = min(loc.latitude_min, loc.latitude_max) - margin
    lat1 = max(loc.latitude_min, loc.latitude_max) + margin
    li = _transform.lat_indices(data.latitude.values, max(-89.99, lat0), min(89.99, lat1))
    oi = _transform.lon_indices(data.longitude.values,
                                loc.longitude_min - margin, loc.longitude_max + margin)
    # Canonical longitude convention (SPEC §2): truth and forecast must share
    # the [-180,180] label lattice or EWB's nearest-neighbor grid alignment
    # silently extrapolates edge columns for western-hemisphere / dateline
    # cases (G-34: structurally-zero AR CSI, inflated western RMSE). The ERA5
    # mirror stores 0-360; api.open_forecast delivers -180..180.
    return _transform.normalize_longitude(data.isel(latitude=li, longitude=oi))


def _normalized_target(default):
    """Wrap a target so its data is canonicalized to -180..180 AFTER EWB's
    own subsetting (G-34 class: the remote ARCO store labels 0-360 while
    open_forecast delivers -180..180; EWB's nearest-neighbor alignment has
    no convention handling -> edge-column garbage / zero-overlap CSI for
    western bboxes. _cropped_target does this for the mirror branch; the
    remote branch is un-cropped (hourly fidelity) so the normalize must
    happen here (8fe8ae212d73, science-review H4)."""
    import dataclasses

    class _Normalized(type(default)):
        def subset_data_to_case(self, data, case_metadata, **kwargs):
            data = super().subset_data_to_case(data, case_metadata, **kwargs)
            if hasattr(data, "coords") and "longitude" in data.coords:
                from ewb_live.sources import _transform

                data = _transform.normalize_longitude(data)
            return data

    return _Normalized(**{f.name: getattr(default, f.name)
                          for f in dataclasses.fields(default)})


def make_targets(
    event_type: str,
    truth_source: str,
    event=None,
    max_lead_hours: int = 240,
) -> dict[str, ewb_inputs.TargetBase]:
    """EWB default targets for an event type, ERA5-ARCO or HRES-analysis truth.

    Returns {"model_name": TargetBase} with the target shared per model — the
    caller (score_event) builds EvaluationObjects per model.
    """
    from extremeweatherbench import defaults as ewb_defaults

    if truth_source == "era5_arco":
        import dataclasses

        default = None
        if event_type == "heat_wave":
            default = ewb_defaults.era5_heatwave_target
        elif event_type == "freeze":
            default = ewb_defaults.era5_freeze_target
        elif event_type == "atmospheric_river":
            default = ewb_defaults.era5_atmospheric_river_target
        if default is not None and event is not None:
            # Serve the ERA5 target from the local 6-hourly mirror when it
            # fully covers the event window: identical variable/coordinate
            # names and values, so EWB's logic is unchanged, but no global
            # ARCO streaming from GCS. Fall back to remote ARCO otherwise
            # (hourly fidelity beyond the mirror's coverage).
            from .truth_mirror import mirror_covers, mirror_path

            if mirror_covers(event.start, event.end):
                from .truth_mirror import ensure_coord_integrity

                ensure_coord_integrity()  # EWB opens the store itself
                default = dataclasses.replace(
                    default, source=str(mirror_path()), storage_options=None
                )
                default = _cropped_target(default)
                return {"ERA5": default}
        if default is not None:
            # EWB defaults pass storage_options={"remote_options": {"anon":
            # True}}, which gcsfs ignores -> ~2 min ADC-discovery stall per
            # run. Same target, anonymous token passed the way gcsfs honours.
            try:
                default = dataclasses.replace(default, storage_options={"token": "anon"})
            except (TypeError, dataclasses.FrozenInstanceError):  # pragma: no cover
                pass
            return {"ERA5": _normalized_target(default)}
    if event is None:
        raise ValueError("event required for HRES-analysis truth")
    # materialise analysis window (padded 10 deg for alignment margin)
    from ewb_live.events.types import Event  # noqa: F401  (typing only)

    pad = 10.0
    lat_min, lat_max, lon_min, lon_max = event.bbox
    bbox = (
        max(-89.0, lat_min - pad),
        min(89.0, lat_max + pad),
        ((lon_min - pad + 180) % 360) - 180,
        ((lon_max + pad + 180) % 360) - 180,
    )
    if event_type in ("heat_wave", "freeze"):
        var_names = ["surface_air_temperature"]
    else:
        var_names = ["eastward_wind", "northward_wind", "specific_humidity"]
    path = build_hres_analysis_zarr(
        event.start, event.end, var_names, bbox
    )
    storage = {"token": "anon"} if path.startswith("gs://") else None
    if event_type in ("heat_wave", "freeze"):
        target = ewb_inputs.ERA5(
            source=path,
            variables=["surface_air_temperature"],
            name="IFS HRES 0-h analysis",
        )
    else:
        from extremeweatherbench import derived

        target = ewb_inputs.ERA5(
            source=path,
            variables=[
                derived.AtmosphericRiverVariables(
                    output_variables=["atmospheric_river_land_intersection"]
                )
            ],
            name="IFS HRES 0-h analysis",
        )
    return {"IFS HRES 0-h analysis": target}
