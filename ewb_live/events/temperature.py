"""Heat-wave / freeze detection: exceedance masks + the footprint/core tracker.

Thresholds and masks are EWB's (climatology percentiles, the daily rule "all
four 6-hourly steps pass", land only). Tracking is the product owner's
methodology of 2026-11-10, ported from github.com/aaTman/ewb-events into
``ewb_live/events/event_tracker.py`` and specified in SPEC §2: regions ->
candidates at 50k km2 -> events at 200k km2 of footprint, a box that grows with
the footprint, and an end when the **core** (the cells valid every day since
the event began, measured in the day-3 anchor box) falls below 100k km2. It
replaced, in order: the upstream EWB blob tracker (unbounded 1 deg-per-edge
expansion, "blob vanished or <50% of own peak" ending, no seed suppression;
notes/ewb.md) and the same morning's interim 200k-seed/+250 km-box rule
(notes/temperature_area_rule.md, superseded by notes/footprint_tracker.md).

Daily masks come from the published raw-exceedance frames when the lane spans
the window (``frame_masks.py`` — the same bytes the map paints and
``qualified_at`` reads, so detection cannot drift from the display); otherwise
ERA5 ARCO is streamed through the upstream helpers.

Other deviations from upstream ``_run_global`` (documented in notes/ewb.md):
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
    the old 0.85/0.15 percentiles (comparison mode; it also forces the ERA5
    path, since the frames lane is p85/p15 only).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import os
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from .event_tracker import Params, track_events
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


def _cell_area_km2(lats: np.ndarray, step_deg: float, nx: int) -> np.ndarray:
    """Grid-cell areas (km2) as a broadcast (lat, lon) array — the same formula
    as upstream ``compute_grid_cell_area`` / the tracker reference (R = 6371 km)."""
    r_km = 6371.0
    col = r_km ** 2 * np.deg2rad(step_deg) * np.deg2rad(step_deg) \
        * np.cos(np.deg2rad(np.asarray(lats, float)))
    col = np.clip(col, 0.0, None)
    return np.broadcast_to(col[:, None], (col.size, nx))


def _mirror_t2m_daily(dates, lats, lons) -> np.ndarray | None:
    """Daily-mean t2m (degC) on the mask grid from the local ERA5 truth mirror.

    The mirror holds exactly the four 6-hourly anchors (00/06/12/18) per day,
    so a day's mean is the mean of its four stamps — the same convention as the
    ARCO path's ``resample(time="1D").mean()`` over the 6-hourly field. Returns
    None (caller falls back to per-event truth reads) when the store, the
    window or the grid does not line up with the mask grid exactly: a silently
    shifted temperature field would mislabel every peak, so alignment is
    asserted cell by cell instead of snapped to the nearest index.
    """
    try:
        import dask

        from ewb_live.scoring import truth_mirror as _tm
    except ImportError:                     # pragma: no cover - env-dependent
        return None
    if not _tm.mirror_path().exists():
        logger.info("truth mirror absent; event temperatures read per event")
        return None
    t2m = _tm.open_mirror()["2m_temperature"]
    lat = np.asarray(t2m.latitude.values, float)
    if lat[0] > lat[-1]:                    # mirror is 90N-first, mask grids ascend
        t2m = t2m.isel(latitude=slice(None, None, -1))
        lat = lat[::-1]
    if float(np.max(t2m.longitude.values)) > 180.0:     # ERA5 native 0..360
        lon180 = (np.asarray(t2m.longitude.values, float) + 180.0) % 360.0 - 180.0
        t2m = t2m.assign_coords(longitude=lon180).roll(
            longitude=t2m.sizes["longitude"] // 2, roll_coords=True)
    lon = np.asarray(t2m.longitude.values, float)
    i_lat = np.abs(lat[:, None] - np.asarray(lats, float)[None, :]).argmin(axis=0)
    i_lon = np.abs(lon[:, None] - np.asarray(lons, float)[None, :]).argmin(axis=0)
    if (np.abs(lat[i_lat] - lats).max() > 1e-4
            or np.abs(lon[i_lon] - lons).max() > 1e-4):
        logger.warning("truth mirror grid does not match the mask grid; "
                       "event temperatures read per event")
        return None
    stamps = np.array([f"{d:%Y-%m-%d}T{h:02d}:00" for d in dates for h in (0, 6, 12, 18)],
                      dtype="datetime64[ns]")
    # compare as datetimes: np.datetime64(x, "h") on a datetime object does NOT
    # truncate, it reinterprets the value, so mixing units here silently
    # reports "every hour missing"
    have = {t.item() for t in np.asarray(t2m.time.values, dtype="datetime64[ns]")}
    want = {t.item() for t in stamps}
    miss = sorted(want - have)
    if miss:
        logger.warning("truth mirror lacks %d/%d hours (%s..%s); event "
                       "temperatures read per event",
                       len(miss), stamps.size, miss[0], miss[-1])
        return None
    sub = t2m.sel(time=stamps).isel(latitude=i_lat, longitude=i_lon)
    arr = sub.transpose("time", "latitude", "longitude").data
    daily = arr.reshape(len(dates), 4, *arr.shape[1:]).mean(axis=1)
    with dask.config.set(scheduler="threads"):
        out = np.asarray(daily.compute(), dtype=np.float32)
    logger.info("event temperatures: daily-mean t2m from the truth mirror "
                "(%d days, %s)", len(dates), out.shape[1:])
    return out - 273.15


def _temp_stats(active: np.ndarray, event_type: str) -> dict | None:
    """Peak/mean/min of the daily-mean t2m over a day's active cells (degC)."""
    vals = active[np.isfinite(active)]
    if not vals.size:
        return None
    return {
        "peak_temp_c": float(vals.max() if event_type == "heat_wave" else vals.min()),
        "mean_temp_c": float(vals.mean()),
        "min_temp_c": float(vals.min()),
    }


def _event_temp_stats(bbox, start_dt: dt.datetime, end_dt: dt.datetime,
                      quantile: float, event_type: str) -> dict:
    """Peak/mean/min daily-mean t2m (degC) over the event's active cells.

    Reads the event's own window from the ERA5 mirror (IFS-HRES analysis beyond
    the cutoff) and applies the frame-level bound + the daily all-four-steps
    rule with the same helpers the viz lane uses — the pattern
    ``recompute_merged_max_consec`` already uses. Raises on a failed read; the
    caller logs and drops the event rather than publishing a bogus peak.
    """
    from ..viz import build as vb
    from .truth import truth_cutoff

    times = vb.truth_times(start_dt, end_dt)
    times = times[:len(times) - len(times) % 4]      # whole days only
    if not times:
        raise ValueError("empty event window")
    t2m, _lon, lat, src = vb.open_truth_window(times, bbox, truth_cutoff())
    thr, _used = vb.thresholds_for(times, lat, _lon, quantile)
    land = vb.land_mask(lat, _lon)
    nd = len(times) // 4
    shape = (nd, 4) + t2m.shape[1:]
    passed = (((t2m > thr) if event_type == "heat_wave" else (t2m < thr))
              & land).reshape(shape).all(axis=1)
    daily = t2m.reshape(shape).mean(axis=1) - 273.15
    vals = daily[passed]
    if not vals.size:
        raise ValueError("no active cells in the event window")
    return {
        "peak_temp_c": float(vals.max() if event_type == "heat_wave" else vals.min()),
        "mean_temp_c": float(vals.mean()),
        "min_temp_c": float(vals.min()),
        "temp_source": src,
    }


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
    frames_dir=None,
    params: Params | None = None,
) -> list[Event]:
    """Scan [start, end] (inclusive) for heat_wave or freeze events.

    Daily masks come from the published raw-exceedance frames when the lane
    spans the window and the active clip matches the lane's p85/p15
    (``ewb_live/events/frame_masks.py``); otherwise ERA5 ARCO is streamed and
    the mask is rebuilt with the upstream helpers, extended back by upstream's
    load buffer so in-progress events keep their true start dates.

    Tracking is the owner's footprint/core methodology
    (``ewb_live/events/event_tracker.py``, SPEC §2): regions -> candidates at
    50k km2 -> events at 200k km2 of footprint, box grows with the footprint,
    end when the core (valid every day since the start, measured in the day-3
    anchor box) holds less than 100k km2. ``min_area_km2`` overrides the 200k
    qualify floor; ``params`` replaces the whole settings block.
    Returns Events overlapping the window, sorted by start.
    """
    from . import frame_masks
    from .frame_masks import daily_masks

    if event_type not in EVENT_CLIP:
        raise ValueError(f"event_type must be one of {sorted(EVENT_CLIP)}")
    clip = dict(EVENT_CLIP[event_type])
    p = params or Params()
    if min_area_km2:
        p = dataclasses.replace(p, qualify_area_km2=float(min_area_km2))

    quantile = clip["q_lower"] if event_type == "heat_wave" else clip["q_upper"]
    lane_span = ""
    if _clim_mode() == "owb" and quantile in (0.85, 0.15):
        days = frame_masks.complete_days(frame_masks.load_index(frames_dir))
        if days:
            lane_span = (f"{dt.datetime.strptime(days[0], '%Y%m%d'):%Y-%m-%d}"
                         f"..{dt.datetime.strptime(days[-1], '%Y%m%d'):%Y-%m-%d}")
    lane_ok = (_clim_mode() == "owb"
               and quantile in (0.85, 0.15)
               and frame_masks.spans_window(frames_dir, start, end))
    if not lane_ok:
        reason = (f"clip/climatology ({_clim_mode()} q={quantile}) differs from the "
                  "lane (owb p85/p15)"
                  if _clim_mode() != "owb" or quantile not in (0.85, 0.15)
                  else f"the lane ({lane_span or 'absent'}) does not span "
                       f"{start}..{end}")
        logger.info("scan %s: %s — re-deriving masks from ERA5 ARCO",
                    event_type, reason)

    t2m_daily: np.ndarray | None = None
    t2m_source = "era5_arco"
    if lane_ok:
        # ------------------------------------------------ frames-first path --
        dates, lats, lons, masks = daily_masks(frames_dir)
        sel = [i for i, d in enumerate(dates) if start <= d <= end]
        if not sel:
            return []
        dates = [dates[i] for i in sel]
        mask = masks[event_type][sel]
        keep = (lats >= lat_min) & (lats <= lat_max)
        lats, mask = lats[keep], mask[:, keep]
        cell_area = _cell_area_km2(lats, float(np.abs(lons[1] - lons[0])), lons.size)
        # the lane stores booleans only; the peak/mean/min temperatures come
        # from the local ERA5 mirror on the same grid and days
        t2m_daily = _mirror_t2m_daily(dates, lats, lons)
        t2m_source = "era5_mirror"
        logger.info("scan %s: %s %s..%s from the raw-exceedance lane "
                    "(%d days, %d x %d grid)", event_type, _clim_mode(),
                    quantile, dates[0], dates[-1], len(dates), *mask.shape[1:])
    else:
        # --------------------------------------------------- ERA5 ARCO path --
        dates, lats, lons, mask, cell_area, t2m_daily = _scan_masks_from_arco(
            start, end, event_type, clip, n_workers=n_workers,
            lat_min=lat_min, lat_max=lat_max)
        if mask is None or not len(dates):
            return []

    raw = track_events(mask, dates, cell_area, p, event_type)
    logger.info("  tracked %s: %d valid events (%d cells-days valid)",
                event_type, len(raw), int(mask.sum()))

    # --------------------------------------------------------- Event mapping --
    from ..viz.build import consecutive_days

    out: list[Event] = []
    dropped = 0
    ny, nx = mask.shape[1:]
    for ev in raw:
        # last active day: ev.end is the first day NOT part of the event; an
        # event still open on the last tracked day ends with that day
        last = (ev.end - dt.timedelta(days=1)) if ev.end else dates[-1]
        if last < start:
            continue     # active only in the lookback, outside the window
        start_dt = dt.datetime.combine(ev.start, dt.time())
        end_excl = ev.end or (dates[-1] + dt.timedelta(days=1))
        end_dt = dt.datetime.combine(end_excl, dt.time())
        cols = np.flatnonzero(ev.box.columns(nx))
        if ev.box.j0 > ev.box.j1:      # wrapped box: order the columns as one
            cols = np.concatenate([cols[cols >= ev.box.j0],   # continuous band
                                   cols[cols <= ev.box.j1]])  # across 180
        bbox = (
            float(lats[ev.box.i0]),
            float(lats[ev.box.i1]),
            normalize_lon_180(float(lons[ev.box.j0])),
            normalize_lon_180(float(lons[ev.box.j1])),
        )
        # per-cell run lengths inside the box (curation severity + dashboard)
        sub = mask[:, ev.box.i0:ev.box.i1 + 1][:, :, cols]
        d0 = dates.index(ev.start)
        d1 = dates.index(last)
        runs = consecutive_days(sub[d0:d1 + 1])
        over_floor = runs[runs >= p.min_days]
        if t2m_daily is not None:
            sub_t = t2m_daily[d0:d1 + 1][:, ev.box.i0:ev.box.i1 + 1][:, :, cols]
            stats = _temp_stats(sub_t[sub[d0:d1 + 1]], event_type)
            if stats:
                stats["temp_source"] = t2m_source
        else:
            try:
                stats = _event_temp_stats(bbox, start_dt, end_dt, quantile,
                                          event_type)
            except Exception as exc:                    # noqa: BLE001
                dropped += 1
                logger.warning("dropping %s %s..%s: no truth for its peak: %s",
                               event_type, ev.start, end_excl, exc)
                continue
        peak_c = stats["peak_temp_c"] if stats else float("nan")
        if stats is None:
            dropped += 1
            logger.warning("dropping %s %s..%s: no finite temperature in its box",
                           event_type, ev.start, end_excl)
            continue
        details: dict[str, Any] = {
            "quantile": quantile,
            "clim_source": _clim_mode(),
            "operator": clip["op_lower"] if event_type == "heat_wave"
            else clip["op_upper"],
            "max_consecutive_days": int(runs.max()) if runs.size else 0,
            "mean_consecutive_days": round(float(over_floor.mean()), 2)
            if over_floor.size else 0.0,
            "peak_area_km2": round(float(ev.peak_area_km2), 1),
            "mean_temp_c": (stats or {}).get("mean_temp_c"),
            "min_temp_c": (stats or {}).get("min_temp_c"),
            "temp_source": (stats or {}).get("temp_source"),
            "antimeridian_wrap": bbox[2] > bbox[3],
            # tracking provenance (SPEC §2 footprint/core methodology)
            "mask_source": "raw_exceedance_frames" if lane_ok else "era5_arco",
            "qualified_date": ev.qualified.isoformat() if ev.qualified else None,
            "ongoing": ev.end is None,
            "duration_days": ev.duration_days,
            "footprint_area_km2": [round(a, 1) for a in ev.footprint_area_km2],
            "box_area_km2": [round(a, 1) for a in ev.box_area_km2],
            "core_area_km2": [round(a, 1) for a in ev.core_area_km2],
            "anchor_box": None if ev.anchor is None else [
                float(lats[ev.anchor.i0]), float(lats[ev.anchor.i1]),
                normalize_lon_180(float(lons[ev.anchor.j0])),
                normalize_lon_180(float(lons[ev.anchor.j1]))],
            "tracker_params": dataclasses.asdict(p),
            "detection": ("footprint/core tracker: candidate >="
                          f"{p.seed_area_km2:.0f} km2, event >="
                          f"{p.qualify_area_km2:.0f} km2 footprint, ends below "
                          f"{p.end_area_km2:.0f} km2 core"),
        }
        if ev.start == dates[0]:
            # the tracker cannot see before the first day it has masks for
            details["window_start_truncated"] = True
        out.append(
            Event(
                id=make_event_id(event_type, start_dt, bbox),
                event_type=event_type,
                title=f"{event_type.replace('_', ' ').title()} {ev.start} to {last}",
                start=start_dt,
                end=end_dt,
                bbox=bbox,
                peak_value=round(peak_c, 2) if np.isfinite(peak_c) else None,
                details=details,
            )
        )
    if dropped:
        logger.warning("%s: %d tracked event(s) dropped for unreadable truth", event_type, dropped)
    out.sort(key=lambda e: e.start)
    logger.info("%s events kept: %d", event_type, len(out))
    return out


def _scan_masks_from_arco(start: dt.date, end: dt.date, event_type: str,
                          clip: dict, *, n_workers: int, lat_min: float,
                          lat_max: float):
    """Daily exceedance masks streamed from ERA5 ARCO (the fallback path).

    Returns ``(dates, lats, lons, mask, cell_area, t2m_daily_c)``; ``mask`` is
    None when the window has no data. Loads extend back by upstream's
    TEMPORAL_LOAD_BUFFER_DAYS so in-progress events keep true start dates.
    """
    import dask
    import xarray as xr

    from ._upstream import temperature_events as _te_module

    te = _te_module()
    load_start = start - dt.timedelta(days=te.TEMPORAL_LOAD_BUFFER_DAYS)
    t2m = te.open_era5_t2m(str(load_start), str(end))
    if lat_min != -90.0 or lat_max != 90.0:
        t2m = t2m.sel(latitude=slice(lat_min, lat_max))
    clim_lower, clim_upper, used_mode = _clim_bounds(te, event_type, _clim_mode())
    if used_mode != _clim_mode():          # fallback active: percentiles shift too
        clip = dict(EVENT_CLIP_MODES[used_mode][event_type])
    logger.info(
        "global scan: %s %s..%s (clim=%s q_low=%s op=%s q_up=%s op=%s, lat [%s,%s])",
        event_type, start, end, used_mode, clip["q_lower"], clip["op_lower"],
        clip["q_upper"], clip["op_upper"], lat_min, lat_max,
    )
    land_mask = te.build_land_mask(t2m.longitude, t2m.latitude)
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
            return [], None, None, None, None, None
        exc_da = xr.concat(exc_parts, dim=tdim)
        t2m_daily_da = xr.concat(t2m_parts, dim=tdim)
        del exc_parts, t2m_parts

        mask = exc_da.values.astype(bool)
        dates = [pd.Timestamp(str(t)).date() for t in exc_da[tdim].values]
        lats = exc_da.latitude.values
        lons = exc_da.longitude.values
        t2m_daily = (t2m_daily_da.values - 273.15).astype(np.float32)
    return dates, lats, lons, mask, area_grid, t2m_daily


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
