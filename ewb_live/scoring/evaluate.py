"""Score one detected event against forecast models with EWB's evaluation API.

Builds an EWB ``IndividualCase`` from the Event, assembles forecast datasets
from ``ewb_live.sources`` (contract: SPEC section 1) into
``inputs.XarrayForecast`` objects, pairs them with EWB default ERA5 targets
(and EWB default metrics per event type), and runs
``ExtremeWeatherBench.run_evaluation``.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import xarray as xr

from extremeweatherbench import cases as ewb_cases
from extremeweatherbench import defaults as ewb_defaults
from extremeweatherbench import derived as ewb_derived
from extremeweatherbench import evaluate as ewb_evaluate
from extremeweatherbench import inputs as ewb_inputs
from extremeweatherbench import metrics as ewb_metrics

from ..events.types import Event
from .truth import (
    probe_arco_cutoff,
    truth_source_for_event,
)

logger = logging.getLogger(__name__)

# --- EWB cache-writer compat shim -------------------------------------------
# utils.maybe_cache_and_compute spills the derived (densified) truth/forecast
# fields with zarr_format=2; the densify copies xarray store encodings, and
# datetime64 variables carrying CF time encodings make xarray hand zarr-python
# a `serializer` codec, which zarr v2 arrays reject ("Zarr format 2 arrays do
# not support `serializer`"). zarr v3 stores take those variables natively and
# xarray reads both formats, so write the intermediate spill as v3. Vendored
# EWB is unmodified; attribute-style patch, call sites use
# utils.maybe_cache_and_compute.
from extremeweatherbench import utils as _ewb_utils


def _cache_and_compute_zarr3(data, name, cache_dir=None):
    if cache_dir is None:
        return data
    import os
    import pathlib

    import xarray as _xr

    zpath = pathlib.Path(cache_dir) / f"{name}.zarr"
    if not zpath.exists():
        d = _ewb_utils._cache_maybe_densify_helper(data)
        # densify copies per-variable store encodings (chunks, v2 numcodecs
        # compressors, CF time dtype/units) from the source stores; zarr v3
        # rejects v2 codec objects. Drop them and let xarray re-chunk.
        for v in d.variables:
            d[v].encoding = {}
            # CF _FillValue attrs inherited from int64 time encodings collide
            # with datetime64 dtypes on write; NaN masks live in the data
            d[v].attrs.pop("_FillValue", None)
        # write to a tmp name and rename: a job killed mid-write would
        # otherwise leave a partial store that `zpath.exists()` mistakes for
        # complete on the next attempt (corrupt truth/forecast reuse).
        tmp = pathlib.Path(cache_dir) / f"{name}.tmp.zarr"
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        d.to_zarr(tmp, zarr_format=3, mode="w")
        os.replace(tmp, zpath)
    if isinstance(data, _xr.Dataset):
        return _xr.open_zarr(zpath, consolidated=False)
    return _xr.open_dataarray(zpath)


_ewb_utils.maybe_cache_and_compute = _cache_and_compute_zarr3


# --- SpatialDisplacement dask-vectorize shim --------------------------------
# EWB 1.1.1 metrics.SpatialDisplacement._compute_metric runs
# apply_ufunc(center_of_mass_ufunc, ..., vectorize=True, dask="allowed").
# With a NUMPY-backed mask xarray loops 2D slices (works); with a DASK-backed
# mask the ufunc receives the whole 3D core array (AR masks keep a level
# dim), and ndimage.center_of_mass returns a 3-tuple against a 2-output
# signature: "applied function ... Received a tuple with 3 elements.
# Expected a tuple of 2". Small (legacy-size) events materialize to numpy
# upstream, so the bug only fires on our big multi-day AR subsets and it
# kills every model on those events (a3bb50ccde3d, fbe99f30e625,
# 2026-11-07 run). The masks are boolean case subsets (MBs), so computing
# them here is cheap and semantics-neutral; attribute-style patch, EWB
# source unmodified.
_SD_ORIG = ewb_metrics.SpatialDisplacement._compute_metric


def _spatial_displacement_numpy_inputs(self, forecast, target, **kwargs):
    if hasattr(forecast, "compute"):
        forecast = forecast.compute()
    if hasattr(target, "compute"):
        target = target.compute()
    return _SD_ORIG(self, forecast, target, **kwargs)


ewb_metrics.SpatialDisplacement._compute_metric = _spatial_displacement_numpy_inputs

MAX_LEAD_HOURS = 240
INIT_STEP_HOURS = 24  # daily 00Z inits are enough (SPEC section 1)
BBOX_PAD_DEG = 10.0

SCORE_COLUMNS = ["event_id", "event_type", "model", "metric", "lead_time_hours", "value", "init_time"]


@lru_cache(maxsize=8)
def _climatology(quantile: float):
    return ewb_defaults.get_climatology(quantile)


def default_metrics(event_type: str) -> list[ewb_metrics.BaseMetric]:
    """EWB default metric list per event type (matches defaults.py ERA5 evals)."""
    if event_type == "heat_wave":
        return [
            ewb_metrics.MaximumMeanAbsoluteError(),
            ewb_metrics.RootMeanSquaredError(),
            ewb_metrics.DurationMeanError(
                threshold_criteria=_climatology(0.85), op_func=_ge
            ),
            ewb_metrics.MaximumLowestMeanAbsoluteError(),
        ]
    if event_type == "freeze":
        return [
            ewb_metrics.MinimumMeanAbsoluteError(),
            ewb_metrics.RootMeanSquaredError(),
            ewb_metrics.DurationMeanError(
                threshold_criteria=_climatology(0.15), op_func=_le
            ),
        ]
    if event_type == "atmospheric_river":
        return [
            ewb_metrics.CriticalSuccessIndex(),
            ewb_metrics.SpatialDisplacement(),
            ewb_metrics.EarlySignal(),
        ]
    raise ValueError(f"no default metrics for event type {event_type!r}")


def _ge(a, b):
    return a >= b


def _le(a, b):
    return a <= b


EVENT_SURFACE_VARS = {
    "heat_wave": ["surface_air_temperature"],
    "freeze": ["surface_air_temperature"],
}
AR_FORECAST_VARS = ["eastward_wind", "northward_wind", "specific_humidity"]


def forecast_variables_for(event_type: str, model: str) -> tuple[list[str], Callable | None]:
    """Source-contract variable list + EWB preprocess for a model/event type.

    Returns (variables, preprocess). Models without specific_humidity but with
    relative_humidity + air_temperature (fourcastnet) use EWB's own r->q
    conversion preprocess.
    """
    from ewb_live import sources

    available = set(sources.model_info(model).variables)
    if event_type in EVENT_SURFACE_VARS:
        want = EVENT_SURFACE_VARS[event_type]
        return list(want), None
    # atmospheric river: needs u, v, and humidity on levels
    missing = [v for v in AR_FORECAST_VARS if v not in available]
    if not missing:
        vars_ = list(AR_FORECAST_VARS)
        if "air_temperature" in available:
            vars_.append("air_temperature")
        return vars_, None
    if missing == ["specific_humidity"] and {"relative_humidity", "air_temperature"} <= available:
        return (
            ["eastward_wind", "northward_wind", "relative_humidity", "air_temperature"],
            ewb_defaults.preprocess_cira_icechunk_ar_forecast_dataset,
        )
    raise ValueError(f"model {model!r} lacks variables {missing} for atmospheric_river")


def case_region_for(event: Event) -> Event:
    """Case-region event: AR cases grow with the forecast data margin.

    EWB subsets the forecast to the case region BEFORE deriving variables,
    while ERA5-target derived fields are computed globally and subset after.
    The AR mask finalisation drops blobs below 500 grid points, so a tight
    event-sized case box truncates forecast blobs and silently zeroes the
    forecast mask. Padding the AR case to the forecast data domain keeps
    blobs whole (see notes/ewb.md 'ROOT CAUSE').
    """
    if event.event_type == "atmospheric_river":
        return event.padded(BBOX_PAD_DEG)
    return event


def _padded_bbox(event: Event, pad: float = BBOX_PAD_DEG) -> tuple[float, float, float, float]:
    lat_min, lat_max, lon_min, lon_max = event.bbox
    return (
        max(-89.0, lat_min - pad),
        min(89.0, lat_max + pad),
        ((lon_min - pad + 180.0) % 360.0) - 180.0,
        ((lon_max + pad + 180.0) % 360.0) - 180.0,
    )


class ForecastReadError(RuntimeError):
    """All forecast-init reads failed for a model/event pair — transient
    source failure (retry next run), distinct from a genuine no-inits
    archive (no_data). Science-review D1, 2026-11-08."""


def build_forecast(
    event: Event, model: str, *, inits: list[dt.datetime] | None = None
) -> ewb_inputs.XarrayForecast | None:
    """Assemble an XarrayForecast covering the event window for one model."""
    from ewb_live import sources

    event_type = event.event_type
    variables, preprocess = forecast_variables_for(event_type, model)
    if inits is None:
        # inits whose [init, init+MAX_LEAD] window intersects the event:
        # init in [event.start - MAX_LEAD, event.end)
        lo = event.start - dt.timedelta(hours=MAX_LEAD_HOURS)
        hi = event.end
        from ewb_live.scoring import inits_cache

        try:
            all_inits = inits_cache.available_inits_cached(model, lo, hi)
        except Exception as exc:  # noqa: BLE001 - failed listing is a blip, not "no data" (ponytail 2026-11-08)
            raise ForecastReadError(f"init listing failed: {exc}") from exc
        daily = [t for t in all_inits if t.hour == 0]
        inits = daily if daily else all_inits
    if not inits:
        logger.warning("model %s has no inits for event %s window", model, event.id)
        return None

    bbox = _padded_bbox(event)
    tasks = []
    for init in inits:
        # read only leads whose valid time (init + lead) overlaps the event
        # window: the dominant network/IO cost is lead count
        lo_h = (event.start - init).total_seconds() / 3600.0
        hi_h = (event.end - init).total_seconds() / 3600.0
        leads = [L for L in range(0, MAX_LEAD_HOURS + 1, 6) if lo_h <= L <= hi_h]
        if leads:
            tasks.append((init, leads))
    if not tasks:
        return None

    import os as _os

    n_par = int(_os.environ.get("EWB_LIVE_INIT_PARALLEL", "4"))

    def _read(init, leads):
        logger.info(
            "build_forecast %s/%s init %s leads %d-%dh x%d",
            model, event_type, f"{init:%m-%d %H}Z", leads[0], leads[-1], len(leads),
        )
        try:
            from ewb_live.scoring import fc_cache

            return fc_cache.open_forecast_cached(model, init, variables, leads, bbox)
        except Exception as exc:  # noqa: BLE001 - one bad init shouldn't kill the event
            logger.warning("open_forecast(%s, %s) failed: %s", model, init, exc)
            return None

    if n_par > 1 and len(tasks) > 1:
        import concurrent.futures as _cf

        with _cf.ThreadPoolExecutor(max_workers=min(n_par, len(tasks))) as ex:
            parts = [p for p in ex.map(lambda t: _read(*t), tasks) if p is not None]
    else:
        parts = [p for t in tasks if (p := _read(*t)) is not None]
    if not parts:
        # every init READ failed — transient source failure, NOT "this model
        # has no data here". score_event records it as retriable read_error
        # (science-review D1 / ponytail 2026-11-08: a source blip during one
        # run must not become a permanent documented hole under full_run_complete).
        raise ForecastReadError(
            f"all {len(tasks)} init reads failed for {model} on event {event.id}")
    ds = parts[0] if len(parts) == 1 else xr.concat(parts, dim="init_time")
    try:
        ds = ds.load()  # real byte-read for lazy (non-cached) stores lands here
    except ForecastReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — load-time source failure is a blip, not "unsupported"
        raise ForecastReadError(f"load failed: {exc}") from exc

    ewb_vars: list[Any] = []
    if event_type == "atmospheric_river":
        ewb_vars.append(
            ewb_derived.AtmosphericRiverVariables(
                output_variables=["atmospheric_river_land_intersection"]
            )
        )
    else:
        ewb_vars.extend(variables)
    return ewb_inputs.XarrayForecast(
        ds=ds, name=model, variables=ewb_vars, preprocess=preprocess or _noop
    )


def _noop(x):
    return x


def score_event(
    event: Event,
    models: list[str],
    *,
    truth_source: str | None = None,
    arco_cutoff: dt.datetime | None = None,
    build_forecast_override: Callable[[Event, str], ewb_inputs.XarrayForecast | None] | None = None,
    truth_cache_dir: str | None = None,
    status_out: dict | None = None,
) -> pd.DataFrame:
    """Score one event on each model with EWB default metrics.

    ``build_forecast_override`` lets tests inject an adapter (SPEC section:
    forecast loader contract); it must return an XarrayForecast (or None)
    whose name is the model id.

    ``truth_cache_dir`` overrides where EWB spills intermediate derived
    truth/forecast fields (default ``data/cache/ewb_mid``); it is wiped
    when the call returns.

    ``status_out`` (if given) is filled with {model: {status, reason,
    rows}} for EVERY requested model — scored pairs and documented
    zero-row pairs alike ("unsupported": model lacks the variables the
    event type needs, e.g. aigfs has no 2 m temperature for heat/freeze
    and is only scoreable on ARs; "no_data"; "bench_failed"). This is
    what makes meta.full_run_complete decidable.
    """
    from .truth import make_targets

    def _note(model: str, status: str, reason: str = "", rows: int = 0) -> None:
        if status_out is not None:
            status_out[model] = {"status": status, "reason": reason, "rows": rows}

    if truth_source is None:
        cutoff = arco_cutoff or probe_arco_cutoff()
        truth_source = truth_source_for_event(event, cutoff)
    event_type = event.event_type
    logger.info("scoring event %s (%s) truth=%s models=%s", event.id, event.event_type, truth_source, models)

    case_dict = case_region_for(event).to_ewb_case(case_id_number=int(event.id[:6], 16))
    target_map = make_targets(event.event_type, truth_source, event=event)
    targets = list(target_map.values())

    # Build and evaluate ONE MODEL AT A TIME. Measured on the real run
    # 2026-11-06: an AR job holding all 11 loaded forecast datasets at once
    # peaked at ~35 GB RSS (3 jobs x 2 = OOM, 8 GB swap saturated), while
    # one model's dataset is ~1-3 GB. EWB spills each derived target/forecast
    # field to a zarr under cache_dir, keyed by (case, event type, input
    # name), so per-model benches still derive the ERA5 truth ONCE (first
    # model pays; the rest read local disk). Per-model benches also isolate
    # failures directly (EWB aborts a whole bench on the first failing
    # case-operator), replacing the old build-all + fallback-on-failure
    # structure. The spill is wiped on return.
    if truth_cache_dir is None:
        import os as _os

        root = _os.environ.get("EWB_LIVE_CACHE", "data/cache")
        truth_cache_dir = str(Path(root) / "ewb_mid" / event.id)
    import gc
    import shutil

    def _run_bench(model: str, fc) -> pd.DataFrame | None:
        evaluation_objects = [
            ewb_inputs.EvaluationObject(
                event_type=event.event_type,
                metric_list=default_metrics(event.event_type),
                target=target,
                forecast=fc,
            )
            for target in targets
        ]
        try:
            bench = ewb_evaluate.ExtremeWeatherBench(
                [case_dict], evaluation_objects, cache_dir=truth_cache_dir
            )
            df = bench.run_evaluation(n_jobs=1, progress=False, output_format="pandas")
        except Exception as exc:  # noqa: BLE001 - EWB raises on first failing pair
            logger.error("EWB evaluation failed for event %s model %s: %s", event.id, model, exc)
            return None
        if df is not None and not isinstance(df, pd.DataFrame):  # xarray output
            df = df.to_dataframe().reset_index()
        return df if df is not None and len(df) else None

    Path(truth_cache_dir).mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    try:
        for model in models:
            if event_type in EVENT_SURFACE_VARS:
                # explicit product-rule check (was an implicit ValueError
                # from the loader): models without 2 m temperature (e.g.
                # aigfs) are NOT scoreable on heat/freeze but ARE scoreable
                # on ARs via q/u/v IVT.
                from ewb_live import sources as _sources

                try:
                    avail = set(_sources.model_info(model).variables)
                except Exception:  # noqa: BLE001
                    avail = set()
                if "surface_air_temperature" not in avail:
                    logger.warning("model %s: n/a (no 2 m temperature) for %s %s",
                                   model, event_type, event.id)
                    _note(model, "unsupported", "n/a (no 2 m temperature)")
                    continue
            try:
                fc = (
                    build_forecast_override(event, model)
                    if build_forecast_override
                    else build_forecast(event, model)
                )
            except ForecastReadError as exc:
                # retriable: NOT terminal, NOT documented — the pair stays a
                # completion blocker until it actually scores
                logger.warning("forecast READ error for event %s model %s: %s",
                               event.id, model, exc)
                _note(model, "read_error", f"all init reads failed (retriable): {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - e.g. model lacks required vars
                logger.warning("forecast unavailable for event %s model %s: %s", event.id, model, exc)
                _note(model, "unsupported", f"forecast unavailable: {exc}")
                continue
            if fc is None:
                logger.warning("no forecast data for event %s model %s", event.id, model)
                _note(model, "no_data", "no forecast data (no inits or all reads failed)")
                continue
            import time as _t

            t_m = _t.monotonic()
            one = _run_bench(model, fc)
            logger.info("bench %s/%s done in %.1fs (%d rows)", event.id, model,
                        _t.monotonic() - t_m, 0 if one is None else len(one))
            if one is not None:
                frames.append(one)
                _note(model, "scored", rows=len(one))
            else:
                _note(model, "bench_failed", "EWB evaluation produced no rows (see job log)")
            del fc
            gc.collect()
    finally:
        shutil.rmtree(truth_cache_dir, ignore_errors=True)
    if not frames:
        logger.warning("event %s: no evaluable forecast/target pairs", event.id)
        return pd.DataFrame(columns=SCORE_COLUMNS)
    return _reshape(pd.concat(frames, ignore_index=True), event)


def _reshape(df: pd.DataFrame, event: Event) -> pd.DataFrame:
    """EWB long-form -> SPEC score rows: event_id,event_type,model,metric,lead_time_hours,value,init_time."""
    n = len(df)
    # Build every column from arrays. Assigning a *scalar* onto an empty
    # DataFrame yields a zero-length column, so mixing it with n-length
    # columns drops or NaNs every row (index alignment).
    lt = pd.Series(df["lead_time"].to_numpy())
    # EWB may return timedelta64 leads (from the forecast lead coord) or
    # numeric hours; pd.to_timedelta on ints would misread them as ns
    if pd.api.types.is_timedelta64_dtype(lt):
        hours = lt.dt.total_seconds() / 3600.0
    else:
        hours = pd.to_numeric(lt, errors="coerce")
    if "init_time" in df.columns:
        init = pd.Series(df["init_time"].to_numpy())
        init = pd.to_datetime(init.where(init.notna()), errors="coerce").to_numpy()
    else:
        init = pd.Series([pd.NaT] * n, dtype="datetime64[ns]").to_numpy()
    out = pd.DataFrame(
        {
            "event_id": np.full(n, event.id),
            "event_type": (
                df["event_type"].to_numpy() if "event_type" in df.columns
                else np.full(n, event.event_type)
            ),
            "model": df["forecast_source"].to_numpy(),
            "metric": df["metric"].to_numpy(),
            "lead_time_hours": pd.to_numeric(hours, errors="coerce").to_numpy(dtype="float64"),
            "value": pd.to_numeric(df["value"], errors="coerce").to_numpy(),
            "init_time": init,
        }
    )
    out = out.dropna(subset=["value"])
    # drop placeholder rows EWB pads with all-NaN leads (init_time AND value missing)
    return out[SCORE_COLUMNS].reset_index(drop=True)
