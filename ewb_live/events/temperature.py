"""Heat-wave / freeze detection: thin wrapper over EWB data_prep scripts.

All thresholds, masks, blob tracking, bound expansion, and filtering are the
upstream functions in ``third_party/ExtremeWeatherBench/data_prep/
temperature_events.py`` (loaded unmodified via ``_upstream``). This module
mirrors the upstream ``global`` subcommand's non-plotting flow
(``_run_global``) and maps its output to the SPEC ``Event`` schema.

Deviations from upstream ``_run_global`` (documented in notes/ewb.md):
  * Dask uses the in-process *threads* scheduler instead of a
    ``LocalCluster`` (scheduling only; the computed graphs are identical).
  * No plotting (consecutive-map / event PNGs skipped).
  * Longitudes mapped from ERA5 0-360 to [-180, 180) for the Event schema.
  * ``peak_value`` = max (heat) / min (freeze) daily-mean t2m over active
    cells in the event box — a descriptive stat for the Event schema;
    upstream CSV only reports mean/min temperature.
  * Event ``end`` is exclusive (last active date + 1 day); upstream CSV
    reports the inclusive last date.
  * Operators: product-owner decision 2026-11-07 uses p85 (heat) / p15
    (freeze) against the OWB pooled climatology (ewb_live/climatology/
    owb_clim.py; rationale + estimator notes in notes/ewb.md). Bounds are
    applied exactly as upstream (6-hourly steps at the hour anchors,
    daily-pass = min over the 4 steps), so only the source store and
    percentile values differ.
    Env EWB_LIVE_CLIM=ewb_default forces EWB's shipped climatology with
    the old 0.85/0.15 percentiles (comparison mode).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

# Detection clips. owb mode (default): p95 heat / p05 freeze per the
# product-owner decision 2026-11-06. NOTE (funnel, notes/ewb.md): p05
# freeze is STRUCTURALLY EMPTY under the upstream AND-chain (all-4-anchors
# < p05 AND >=3 consecutive days AND 200k km2 seed blob on adjacent days;
# Sep 2026: 7659 exceedance cell-days -> 179 after the consecutive filter,
# largest blob 7k km2 vs the 200k seed floor). EWB_LIVE_HEAT_CLIP /
# EWB_LIVE_FREEZE_CLIP override the owb-mode quantiles (values must exist
# in the OWB store: 0.01/0.05/0.15/0.85/0.95/0.99); the owner can flip
# freeze to 0.15 with one env var.
# ewb_default mode: upstream CLI defaults, kept for comparison runs.
EVENT_CLIP_MODES: dict[str, Any] = {
    "owb": {
        # owner decision 2026-11-07: heat p85 / freeze p15 of the OWB ERA5
        # 1990-2019 climatology (zero freeze events acceptable if none
        # qualify); was p95/p05 during the 2026-11-06 comparison runs.
        "heat_wave": dict(q_lower=0.85, op_lower=">", q_upper=None, op_upper="<"),
        "freeze": dict(q_lower=None, op_lower=">", q_upper=0.15, op_upper="<"),
    },
    "ewb_default": {
        "heat_wave": dict(q_lower=0.85, op_lower=">", q_upper=None, op_upper="<"),
        "freeze": dict(q_lower=None, op_lower=">", q_upper=0.15, op_upper="<"),
    },
}


def _clim_mode() -> str:
    return os.environ.get("EWB_LIVE_CLIM", "owb")


def _clip_override(quantile: float | None, env: str) -> float | None:
    if quantile is None:
        return None
    return float(os.environ.get(env, quantile))


def _mode_clim(mode: str | None = None) -> dict:
    table = EVENT_CLIP_MODES[mode or _clim_mode()]
    if mode == "ewb_default" or (mode is None and _clim_mode() == "ewb_default"):
        return table
    return {
        "heat_wave": {**table["heat_wave"],
                      "q_lower": _clip_override(table["heat_wave"]["q_lower"],
                                                "EWB_LIVE_HEAT_CLIP")},
        "freeze": {**table["freeze"],
                   "q_upper": _clip_override(table["freeze"]["q_upper"],
                                             "EWB_LIVE_FREEZE_CLIP")},
    }


def _clim_bounds(te: Any, event_type: str, mode: str) -> tuple:
    """(clim_lower, clim_upper, used_mode) — OWB store when built, else
    fall back to EWB defaults (+ their percentiles) with a warning so a
    half-provisioned box still produces a full, honest run."""
    clip: dict[str, Any] = _mode_clim(mode)[event_type]
    if mode == "owb":
        from ewb_live.climatology import owb_clim

        if (owb_clim.default_out_path() / ".done").exists():
            lo = (owb_clim.get_climatology_owb(clip["q_lower"])
                  if clip["q_lower"] is not None else None)
            up = (owb_clim.get_climatology_owb(clip["q_upper"])
                  if clip["q_upper"] is not None else None)
            return lo, up, "owb"
        logger.warning(
            "OWB climatology not built at %s — falling back to EWB default "
            "climatology with 0.85/0.15 percentiles for this scan",
            owb_clim.default_out_path())
    fb = EVENT_CLIP_MODES["ewb_default"][event_type]
    lo, up = te.get_climatology_bounds(q_lower=fb["q_lower"],
                                       q_upper=fb["q_upper"])
    return lo, up, "ewb_default"


def climatology_meta() -> dict:
    """meta.json 'climatology' block describing the finders' thresholds."""
    from ewb_live.climatology import owb_clim

    mode = _clim_mode()
    if mode == "owb" and (owb_clim.default_out_path() / ".done").exists():
        return owb_clim.climatology_meta()
    return {
        "source": "ewb_default_climatology",
        "path": "gs://extremeweatherbench/datasets/"
                "surface_air_temperature_1990_2019_climatology.zarr",
        "percentiles": {"heat_wave": 0.85, "freeze": 0.15},
        "estimator": "EWB shipped climatology (triangular rolling-mean "
                     "estimator, 1990-2019)",
        "note": "fallback used because EWB_LIVE_CLIM=ewb_default or the OWB "
                "store is not built on this host",
    }


# backward-compatible view of the active clip table (tests/notes reference
# EVENT_CLIP); live mapping over the active mode's table (supports []/in/
# sorted, unlike a plain dict with only __missing__)
class _ActiveClips(Mapping):
    def __getitem__(self, key):
        return _mode_clim()[key]

    def __iter__(self):
        return iter(_mode_clim())

    def __len__(self):
        return len(_mode_clim())


EVENT_CLIP = _ActiveClips()


def scan_temperature_events(
    start: dt.date,
    end: dt.date,
    event_type: str,
    *,
    n_workers: int = 4,
    chunk_months: int = 1,
    lat_min: float = -60.0,
    lat_max: float = 90.0,
    min_area_km2: float | None = None,
) -> list[Event]:
    """Run the upstream EWB global scan for heat_wave or freeze events.

    [start, end] are inclusive dates (same semantics as the upstream CLI).
    Returns Events overlapping the window, sorted by start.
    """
    import dask
    import joblib
    import xarray as xr

    from ._upstream import temperature_events as _te_module

    te = _te_module()
    if event_type not in EVENT_CLIP:
        raise ValueError(f"event_type must be one of {sorted(EVENT_CLIP)}")
    clip = EVENT_CLIP[event_type]
    min_area_km2 = te.MIN_AREA_KM2 if min_area_km2 is None else min_area_km2

    # rolling-window refinement (documented deviation): upstream global scan
    # loads exactly [start, end]; for a live rolling window we extend the
    # *load and scan* window back by upstream's TEMPORAL_LOAD_BUFFER_DAYS so
    # in-progress events keep their true start dates across weekly runs, then
    # keep only events with >=1 active day inside [start, end].
    load_start = start - dt.timedelta(days=te.TEMPORAL_LOAD_BUFFER_DAYS)
    t2m = te.open_era5_t2m(str(load_start), str(end))
    if lat_min != -90.0 or lat_max != 90.0:
        t2m = t2m.sel(latitude=slice(lat_min, lat_max))
    clim_lower, clim_upper, used_mode = _clim_bounds(
        te, event_type, _clim_mode()
    )
    if used_mode != _clim_mode():  # fallback active: percentiles shift too
        clip = EVENT_CLIP_MODES[used_mode][event_type]
    logger.info(
        "global scan: %s %s..%s (clim=%s q_low=%s op=%s q_up=%s op=%s, lat [%s,%s])",
        event_type, start, end, used_mode, clip["q_lower"], clip["op_lower"],
        clip["q_upper"], clip["op_upper"], lat_min, lat_max,
    )
    land_mask = te.build_land_mask(t2m.longitude, t2m.latitude)
    land_mask_np = land_mask.values.astype(bool)
    area_grid = te.compute_grid_cell_area(t2m.latitude.values, t2m.longitude.values)
    tdim = te.detect_time_dim(t2m)

    # month-chunked lazy graphs, computed one month at a time (upstream
    # defaults: chunk_months=1). Upstream derives chunk bounds via
    # date_range(freq="MS"), which yields nothing for windows starting
    # mid-month; we keep the same monthly chunking but start the first
    # chunk at `start` so any window works.
    month_starts = [d.date() for d in pd.date_range(start.replace(day=1), end, freq="MS")]
    bounds = [start] + [d for d in month_starts if d > start] + [end + dt.timedelta(days=1)]
    bounds = sorted(set(bounds))
    chunks = [
        (bounds[i], min(bounds[i + 1] - dt.timedelta(days=1), end))
        for i in range(len(bounds) - 1)
    ]
    chunks = [(a, b) for a, b in chunks if a <= b]
    exc_parts: list = []
    t2m_parts: list = []
    with dask.config.set(scheduler="threads", num_workers=n_workers):
        for ms_d, me_d in chunks:
            t2m_chunk = t2m.sel({tdim: slice(str(ms_d), str(me_d))})
            if t2m_chunk.sizes[tdim] == 0:
                continue
            exc_lazy = te.build_exceedance_mask(
                t2m_chunk, clim_lower, clim_upper, land_mask,
                op_lower=clip["op_lower"], op_upper=clip["op_upper"],
            )
            t2m_lazy = t2m_chunk.resample({tdim: "1D"}).mean()
            exc_da, t2m_daily_da = dask.compute(exc_lazy, t2m_lazy)
            exc_parts.append(exc_da)
            t2m_parts.append(t2m_daily_da)
            logger.info("  chunk %s..%s done", ms_d, me_d)

        if not exc_parts:
            return []
        exc_da = xr.concat(exc_parts, dim=tdim)
        t2m_daily_da = xr.concat(t2m_parts, dim=tdim)
        del exc_parts, t2m_parts

        dates = exc_da[tdim].values
        lats = exc_da.latitude.values
        lons = exc_da.longitude.values
        exc_np = exc_da.values.astype(bool)
        del exc_da

        t2m_daily_np = (t2m_daily_da.values - 273.15).astype(np.float32)
        del t2m_daily_da

        exc_filt = te._consecutive_filter_np(exc_np, te.MIN_CONSECUTIVE_DAYS, 1)
        logger.info(
            "  consecutive filter: %d -> %d cells", int(exc_np.sum()), int(exc_filt.sum())
        )
        del exc_np

        events = te.detect_events(
            exc_filt, dates, lats, lons, event_type,
            area_grid=area_grid, min_seed_area_km2=min_area_km2,
        )
        logger.info("  raw events: %d", len(events))

        events = joblib.Parallel(n_jobs=-1, prefer="threads")(
            joblib.delayed(te.expand_event_bounds)(
                ev, exc_filt, dates, lats, lons, land_mask_np
            )
            for ev in events
        )
        events = te.include_temps_with_events(
            events, t2m_daily_np, exc_filt, dates, lats, lons
        )
        df = te.events_to_dataframe(events, min_area_km2=min_area_km2)

    # keep only events surviving the upstream filter, mapped to Event schema
    kept = {(r.start_date, r.latitude_min, r.longitude_min) for r in df.itertuples()}
    # owner 2026-11-09 (HIGH): naive upstream labeling splits one object at
    # the 0-meridian array seam into two pieces — stitch them back before
    # bbox/peak attribution (shared seam module; also fixes wrap-bbox slices)
    from .seam import merge_seam_objects, seam_columns

    events = [e for e in merge_seam_objects(events, lons)
              if (str(e["start"]), e["lat_min"], e["lon_min"]) in kept
              or any(k in kept for k in e.get("seam_merged_from", ()))]
    out: list[Event] = []
    for ev in events:
        if np.datetime64(ev["end"]) < np.datetime64(start):
            continue  # active only in the 14-day lookback, outside the window
        start_d = np.datetime64(ev["start"]).astype("datetime64[D]")
        end_d = np.datetime64(ev["end"]).astype("datetime64[D]")
        start_dt = dt.datetime.combine(start_d.astype(dt.date), dt.time())
        end_dt = dt.datetime.combine(end_d.astype(dt.date), dt.time()) + dt.timedelta(days=1)

        # peak active daily-mean temp in the expanded box (see docstring)
        ti = np.where(
            (dates >= np.datetime64(ev["start"])) & (dates <= np.datetime64(ev["end"]))
        )[0]
        i_s = te._nearest_1d_index(lats, ev["lat_min"])
        i_n = te._nearest_1d_index(lats, ev["lat_max"])
        ilon = seam_columns(lons, float(ev["lon_min"]), float(ev["lon_max"]))
        ic = np.flatnonzero(ilon)
        sub_t = t2m_daily_np[ti][:, i_s : i_n + 1][:, :, ic]
        sub_x = exc_filt[ti][:, i_s : i_n + 1][:, :, ic]
        n = min(sub_t.shape[0], sub_x.shape[0])
        active = sub_t[:n][sub_x[:n]]
        if active.size:
            peak_c = float(active.max()) if event_type == "heat_wave" else float(active.min())
        else:
            peak_c = float("nan")

        bbox = (
            float(ev["lat_min"]),
            float(ev["lat_max"]),
            normalize_lon_180(float(ev["lon_min"])),
            normalize_lon_180(float(ev["lon_max"])),
        )
        eid = make_event_id(event_type, start_dt, bbox)
        details: dict[str, Any] = {
            "quantile": clip["q_lower"] if event_type == "heat_wave" else clip["q_upper"],
            "clim_source": used_mode,
            "operator": clip["op_lower"] if event_type == "heat_wave" else clip["op_upper"],
            "max_consecutive_days": int(ev.get("max_consecutive_days", 0)),
            "mean_consecutive_days": float(ev.get("mean_consecutive_days", 0.0)),
            "peak_area_km2": round(float(ev.get("peak_area_km2", 0.0)), 1),
            "peak_gridpoints": int(ev["peak"]),
            "mean_temp_c": ev.get("mean_temp_c"),
            "min_temp_c": ev.get("min_temp_c"),
            "antimeridian_wrap": bbox[2] > bbox[3],
            "detection": "EWB data_prep/temperature_events.py global scan",
        }
        out.append(
            Event(
                id=eid,
                event_type=event_type,
                title=f"{event_type.replace('_', ' ').title()} {start_d} to {end_d}",
                start=start_dt,
                end=end_dt,
                bbox=bbox,
                peak_value=round(peak_c, 2) if np.isfinite(peak_c) else None,
                details=details,
            )
        )
    out.sort(key=lambda e: e.start)
    logger.info("%s events kept: %d", event_type, len(out))
    return out


def recompute_merged_max_consec(events: list[Event], cutoff) -> int:
    """Merged heat/freeze events: recompute details.max_consecutive_days
    over the MERGED window+bbox with the viz daily rule (a cell's day counts
    only when all four 6-hourly steps exceed; run = consecutive days per
    cell, max over cells). Upstream components only measure their own
    fragment, so a member-max merge under-reports contiguous runs
    (guardian-design 2026-11-08). Returns the number of events updated."""
    from ..viz import build as vb
    from .truth import truth_cutoff

    n = 0
    for e in events:
        if e.event_type not in ("heat_wave", "freeze"):
            continue
        d = e.details or {}
        if (d.get("merged_count") or 1) < 2:
            continue
        try:
            times = vb.truth_times(e.start, e.end if e.end > e.start
                                   else e.start + dt.timedelta(days=1))
            if len(times) < 4 or len(times) % 4:
                continue
            t, lon, lat, _src = vb.open_truth_window(times, tuple(e.bbox), cutoff)
            thr, _used = vb.thresholds_for(times, lat, lon,
                                           float(d.get("quantile")
                                                 or (0.85 if e.event_type == "heat_wave" else 0.15)))
            land = vb.land_mask(lat, lon)
            passed = ((t > thr) if e.event_type == "heat_wave" else (t < thr)) & land
            daily = passed.reshape(len(times) // 4, 4, len(lat), len(lon)).min(axis=1)
            d["max_consecutive_days"] = int(vb.consecutive_days(daily).max())
            n += 1
        except Exception as exc:  # noqa: BLE001 — keep member-max, never fail detection
            logger.warning("max_consec recompute failed for %s (keeping %s): %s",
                           e.id, d.get("max_consecutive_days"), exc)
    return n
