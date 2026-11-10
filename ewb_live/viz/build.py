"""Build per-event viz.json assets (schema v3: per-valid-time exceedance masks).
See package docstring.

Schema v3 (owner 2026-11-07, reference notes/owner_refs/2026-11-07_heatwave_reference.png):
heat/freeze detail maps render ONE exceedance class per valid time (cells above
the p85 heat threshold / below the p15 freeze threshold; everything else
transparent), stepping through valid days, observed and model-at-lead alike.
For that, every source carries a per-day binary exceedance mask:
  * obs.daily_bits / models[m][lead].daily_bits = {b64, ndays, ny, nx}:
    bit-packed booleans, day-major then row-major, MSB-first (np.packbits of
    day.ravel()); 1 = the cell's event condition held at ALL four 6-hourly
    steps of that valid day (same rule as detection).
  * obs.daily ('0/1' strings) and the u8 consec grids stay for v2 readers.

Encoding choices (keep total ≤ ~400 KB/event):
  * grids coarsened to a uniform lattice (0.25° heat/freeze, 0.5° AR), stored
    row-major with nulls; consec fields are ints
  * daily mask strips packed as '0/1' strings, rows joined by '/' (legacy),
    and bit-packed base64 (v3)
  * per-step climatology thresholds are fetched lazily from the EWB zarr in
    unique (dayofyear, hour) pairs only
Truth follows scoring: ERA5 mirror while the window is inside the ARCO
cutoff, IFS-HRES 0-h analysis (surface) after it. AR truth needs the
pressure-level mirror; after the cutoff AR truth is absent (surface-only).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import math
import os
import time
from pathlib import Path

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

SCHEMA = 3
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "results" / "events"
CELL = {"heat_wave": 0.25, "freeze": 0.25, "atmospheric_river": 0.5}
# AR input window = event bbox grown by this MUCH before the mask is computed.
# Two independent reasons, both live bugs (2026-11-10, Europe AR
# 6b4dec9dc8db "sliced at the prime meridian"):
#   * the AR mask is computed ON THIS WINDOW, so its edges are array edges —
#     a 2 deg margin put the detector's non-wrapping stencil/labeling wall
#     inside the picture (the 500-cell component floor was applied to the
#     truncated piece: 691 cells survived a crop that cut half the plume);
#   * the stencil itself reaches ~8 deg of true neighbours (events/ar_mask).
# The mask/IVT are regridded back onto the display grid afterwards, so a wide
# window costs compute, never pixels.
AR_COMPUTE_PAD_LON_DEG = 25.0
AR_COMPUTE_PAD_LAT_DEG = 10.0
HEAT_LEADS_DEFAULT = (24, 72, 120, 240)
AR_LEADS_DEFAULT = (24, 120)
FETCH_BUDGET_S = 600.0


# ------------------------------------------------------------------ helpers
def pack_mask(mask: np.ndarray) -> str:
    return "/".join("".join("1" if v else "0" for v in row) for row in mask)


def pack_bits(masks: np.ndarray) -> dict:
    """Bit-pack a (ndays, ny, nx) boolean stack: day-major, row-major, MSB-first.

    np.packbits over each day's flattened grid (one pad tail per day),
    concatenated across days. Unpack (JS): stride = ceil(ny*nx/8);
    on = b64[day*stride + (i>>3)] & (1 << (7 - i&7)), i = y*nx + x.
    ~8x smaller than the '0/1' string strips.
    """
    m = np.ascontiguousarray(np.asarray(masks, bool))
    nd, ny, nx = m.shape
    parts = [np.packbits(m[d].ravel()) for d in range(nd)]
    blob = np.concatenate(parts) if parts else np.zeros(0, np.uint8)
    return {"b64": base64.b64encode(blob.tobytes()).decode("ascii"),
            "ndays": nd, "ny": ny, "nx": nx}


def pack_u8(arr: np.ndarray, lo: float, hi: float) -> dict:
    """Quantise a 2-D field to base64 u8: 0..254 map linearly lo..hi, 255 = missing.

    Borrowed from Weather Explorer (MIT): image-style u8 quantisation with an
    explicit missing sentinel cuts float-array JSON by ~4x. Unpack is 10 lines
    of JS (dashboard/eventviz.js) and np.frombuffer in Python.
    """
    a = np.asarray(arr, np.float64)
    scale = (hi - lo) / 254.0 if hi > lo else 1.0
    q = np.where(np.isfinite(a), np.clip(np.round((a - lo) / scale), 0, 254),
                 255).astype(np.uint8)
    return {"b64": base64.b64encode(q.tobytes()).decode("ascii"),
            "shape": list(q.shape), "lo": float(lo), "hi": float(hi)}


def field_to_list(arr: np.ndarray, nd: int = 1) -> list:  # schema v1 (legacy)
    return [[[None if not np.isfinite(v) else round(float(v), nd) for v in row]
             for row in layer] for layer in arr.reshape((-1,) + arr.shape[-2:])] \
        if arr.ndim == 3 else [[None if not np.isfinite(v) else round(float(v), nd)
                                for v in row] for row in arr]


def consecutive_days(mask: np.ndarray) -> np.ndarray:
    cur = np.zeros(mask.shape[1:], np.int32)
    best = np.zeros_like(cur)
    for day in mask:
        cur = (cur + 1) * day
        np.maximum(best, cur, out=best)
    return best


def dst_grid(bbox, cell):
    lmin, lmax, onmin, onmax = bbox
    pad = cell
    lats = np.round(np.arange(math.floor((lmin - pad) / cell) * cell,
                              math.ceil((lmax + pad) / cell) * cell + cell / 2, cell), 4)
    # B8 QA (2026-11-07): antimeridian-crossing boxes (onmax < onmin) build a
    # CONTINUOUS eastward lattice (e.g. 156..195, longitudes may exceed 180).
    # This matches _lon_idx, which returns the native window as one contiguous
    # eastward slice for wrapped windows, and keeps image-overlay extents and
    # contour paths from jumping across the whole globe. Leaflet renders
    # lng > 180 natively (standard dateline pattern), so no wrap-back.
    hi = onmax + 360.0 if onmax < onmin else onmax
    lons = np.round(np.arange(math.floor((onmin - pad) / cell) * cell,
                              math.ceil((hi + pad) / cell) * cell + cell / 2, cell), 4)
    return lats, lons


def regrid(field: np.ndarray, src_lat: np.ndarray, src_lon: np.ndarray,
           dst_lat: np.ndarray, dst_lon: np.ndarray) -> np.ndarray:
    """Nearest-neighbour regridding; tolerates descending/unordered source axes."""
    field = np.asarray(field, np.float32)
    src_lat = np.asarray(src_lat, float)
    if src_lat[1] < src_lat[0]:
        src_lat = src_lat[::-1]
        field = field[..., ::-1, :]
    src_lon = np.asarray(src_lon, float)
    # Canonicalize source longitudes into the DESTINATION window's frame:
    # every src point moves within ±180° of the dst center. Keeps ascending
    # order intact and lets a continuous dst lattice reach lng > 180
    # (antimeridian-crossing events) without clamping to the old point.
    ref = 0.5 * (float(np.min(dst_lon)) + float(np.max(dst_lon)))
    src_lon = (src_lon - ref + 180.0) % 360.0 - 180.0 + ref
    if not np.all(np.diff(src_lon) > 0):
        order = np.argsort(src_lon, kind="stable")
        src_lon = src_lon[order]
        field = field[..., order]
    si = np.searchsorted(src_lat, dst_lat).clip(0, len(src_lat) - 1)
    sj = np.searchsorted(src_lon, dst_lon).clip(0, len(src_lon) - 1)
    # snap to closer neighbour
    si = np.where(np.abs(src_lat[np.clip(si - 1, 0, None)] - dst_lat) <
                  np.abs(src_lat[si] - dst_lat), np.clip(si - 1, 0, None), si)
    sj = np.where(np.abs(src_lon[np.clip(sj - 1, 0, None)] - dst_lon) <
                  np.abs(src_lon[sj] - dst_lon), np.clip(sj - 1, 0, None), sj)
    full = (slice(None),) * (field.ndim - 2) + np.ix_(si, sj)
    out = field[full]
    return out.reshape(field.shape[:-2] + (len(dst_lat), len(dst_lon))) \
        if field.ndim > 2 else out


# ------------------------------------------------------------------ truth
def truth_times(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    t = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while t < end:
        if t.hour in (0, 6, 12, 18):
            out.append(t)
        t += dt.timedelta(hours=6)
    return out


def _lon_slice(lon_native: np.ndarray, onmin: float, onmax: float) -> slice:
    """Delegate to the shared ewb_live.periodic_index.lon_slice (one seam
    formula everywhere; owner direction 2026-11-09)."""
    from ewb_live.periodic_index import lon_slice
    return lon_slice(lon_native, onmin, onmax)


def _lon_idx(lon_native: np.ndarray, onmin: float, onmax: float) -> np.ndarray:
    """Legacy native-coordinate indices for a [-180,180] lon window — kept
    ONLY for masked stores whose lon coord has NaN holes (no index then)."""
    native = lon_native.copy()
    wrap360 = float(np.nanmax(native)) > 180.0
    if wrap360:
        native = native % 360
        lo, hi = (onmin + 360) % 360, (onmax + 360) % 360
    else:
        lo, hi = onmin, onmax
    if lo <= hi:
        return np.where((native >= lo) & (native <= hi))[0]
    return np.concatenate([np.where(native >= lo)[0], np.where(native <= hi)[0]])


def _crop_lon(obj, lon_native, onmin, onmax):
    """Longitude crop through PeriodicBoundaryIndex (design-lane windows).
    Fallback: legacy index math for legacy masked stores with NaN lons."""
    from ewb_live.periodic_index import attach_periodic
    if np.isnan(lon_native).any():   # masked-stores path (lon has NaN holes)
        idx = _lon_idx(np.where(np.isnan(lon_native), 361.0, lon_native),
                       onmin, onmax)
        return obj.isel(longitude=idx), lon_native[idx]
    sub = attach_periodic(obj).sel(longitude=_lon_slice(lon_native, onmin, onmax))
    return sub, np.asarray(sub.longitude.values, float)



def open_truth_window(times: list[dt.datetime], bbox, cutoff):
    """(t2m (K) (n, y, x), lon180 (x,), lat (y,), truth_source)."""
    from ewb_live.scoring import truth_mirror
    from ewb_live.scoring.truth import build_hres_analysis_zarr

    t0, t1 = times[0], times[-1] + dt.timedelta(hours=6)
    lmin, lmax, onmin, onmax = bbox
    if t1 <= cutoff:
        ds = truth_mirror.open_mirror()
        src = "era5_mirror"
    else:
        # exact-key cache lookup happens inside build_hres_analysis_zarr
        # (time+vars+bbox); time-range-based reuse of another region's
        # cropped store was the bug (ponytail 2026-11-08)
        zpath = build_hres_analysis_zarr(
            t0 - dt.timedelta(hours=6), t1 + dt.timedelta(hours=6),
            ["surface_air_temperature"], bbox)
        ds = xr.open_zarr(zpath)
        src = "ifs_hres_analysis"
    da = ds["2m_temperature"]
    lat_native = np.asarray(da.latitude.values, float)
    lon_native = np.asarray(da.longitude.values, float)
    lats = lat_native[(lat_native >= lmin - 1) & (lat_native <= lmax + 1)]
    lat_idx = np.where((lat_native >= lmin - 1) & (lat_native <= lmax + 1))[0]
    da, lon_sel = _crop_lon(da, lon_native, onmin - 1, onmax + 1)
    da = da.isel(latitude=lat_idx)
    da = da.sel(time=[np.datetime64(t) for t in times])
    lon = np.where(lon_sel > 180, lon_sel - 360, lon_sel)
    field = np.asarray(da.transpose("time", "latitude", "longitude").values, np.float32)
    return field, lon, lats, src


_CLIM: dict[tuple[float, str], xr.DataArray] = {}


def thresholds_for(times: list[dt.datetime], lats, lons, quantile: float,
                   clim: str = "owb"):
    """(n, ny, nx) K thresholds from the climatology zarr, disk-cached (npz).

    clim='owb' uses our pooled OWB ERA5 1990-2019 store (the finder basis);
    falls back to the EWB-shipped climatology when that store is absent
    (thresholds then differ slightly — viz.json carries the source note).
    """
    key_clim = clim
    cl = _CLIM.get((quantile, key_clim))
    if cl is None:
        cl = None
        if clim == "owb":
            try:
                from ewb_live.climatology.owb_clim import get_climatology_owb
                cl = get_climatology_owb(quantile)
            except (FileNotFoundError, ImportError):
                cl = None
                key_clim = "fallback-ewb"
        if cl is None:
            from extremeweatherbench import defaults
            cl = defaults.get_climatology(quantile)
        _CLIM[(quantile, key_clim)] = cl
    doys = sorted({t.timetuple().tm_yday - 1 for t in times})
    hours = sorted({t.hour for t in times})
    cl_lon_native = np.asarray(cl.longitude.values, float)
    wrap360 = float(np.nanmax(cl_lon_native)) > 180.0
    lat0, lat1 = min(lats) - 0.5, max(lats) + 0.5
    on0, on1 = min(lons) - 0.5, max(lons) + 0.5
    if wrap360:
        on0, on1 = (on0 + 360) % 360, (on1 + 360) % 360
    key = (f"{quantile:.2f}_{lat0:.1f}_{lat1:.1f}_{on0:.1f}_{on1:.1f}"
           f"_d{'_'.join(map(str, doys))}").replace("-", "m")
    cache = build_cache_dir() / f"clim_{key}_{key_clim}.npz"
    if cache.exists():
        z = np.load(cache)
        sub_doy, sub_hr = z["doy"], z["hr"]
        sub_lat, sub_lon = z["lat"], z["lon"]
        field = np.asarray(z["field"], np.float32)
    else:
        sub = cl.isel(dayofyear=doys).sel(hour=hours)
        # store requested positions, not 1-based coord labels
        lat_slice = slice(lat1, lat0) if float(cl.latitude[1]) < float(cl.latitude[0]) \
            else slice(lat0, lat1)
        sub, _ = _crop_lon(sub, cl_lon_native, on0, on1)
        sub = sub.sel(latitude=lat_slice).load()
        sub_doy = np.asarray(doys)
        sub_hr = np.asarray(sub.hour.values)
        sub_lat = np.asarray(sub.latitude.values, float)
        sub_lon = np.asarray(sub.longitude.values, float)
        field = np.asarray(sub.transpose("dayofyear", "hour", "latitude",
                                         "longitude").values, np.float32)
        tmp = cache.with_name(cache.stem + ".tmp.npz")
        np.savez_compressed(tmp, doy=sub_doy, hr=sub_hr, lat=sub_lat,
                            lon=sub_lon, field=field)
        tmp.rename(cache)
    lat_idx = [int(np.abs(sub_lat - v).argmin()) for v in lats]
    want = np.asarray(lons, float) if not wrap360 else (np.asarray(lons, float) + 360) % 360
    lon_idx = [int(np.abs(sub_lon - w).argmin()) for w in want]
    ia, ib = np.ix_(lat_idx, lon_idx)
    field = field[:, :, ia, ib]
    di = {int(d): i for i, d in enumerate(sub_doy)}
    hi = {int(h): i for i, h in enumerate(sub_hr)}
    return np.stack([field[di[t.timetuple().tm_yday - 1], hi[t.hour]] for t in times]), key_clim


def build_cache_dir() -> Path:
    d = ROOT / "data/cache/viz"
    d.mkdir(parents=True, exist_ok=True)
    return d


def land_mask(lats, lons) -> np.ndarray:
    from extremeweatherbench.utils import regionmask_land_110
    LON, LAT = np.meshgrid(np.asarray(lons, float), np.asarray(lats, float))
    m = regionmask_land_110(xr.DataArray(LAT, dims=("latitude", "longitude")),
                            xr.DataArray(LON, dims=("latitude", "longitude")))
    return np.asarray(m.values == 0)


# ------------------------------------------------------------------ models
def model_daily_pass(model: str, day0: dt.date, ndays: int, lead: int,
                     thr_daily, bbox, lats, lons):
    """Daily all-steps-pass mask (ndays, ny, nx) for one model/lead, or None.

    Steps 00/06/12/18Z of window day k come from lead L + 24k + h for init at
    day0 00Z − L hours.
    """
    from ewb_live.sources import api

    init = dt.datetime.combine(day0, dt.time()) - dt.timedelta(hours=lead)
    nominal = [lead + 24 * k + h for k in range(ndays) for h in (0, 6, 12, 18)]
    try:
        ds, off = api.open_forecast_nearest_init(
            model, init, ["surface_air_temperature"],
            lead_hours=sorted({l + 0 for l in nominal}), bbox=bbox)
        # with an earlier init the SAME valid times sit offset h longer out
        want = [l + off for l in sorted(set(nominal))]
    except Exception as exc:
        logger.debug("open_forecast %s @%s failed: %s", model, init, exc)
        return None, f"unavailable ({_reason(exc)})"
    var = "surface_air_temperature"
    if var not in ds.data_vars:
        return None, "missing variable"
    da = ds[var].isel(init_time=0)
    src_lat = np.asarray(ds.latitude.values, float)
    src_lon = np.where(np.asarray(ds.longitude.values, float) > 180,
                       np.asarray(ds.longitude.values, float) - 360,
                       np.asarray(ds.longitude.values, float))
    field = regrid(np.asarray(da.values, np.float32), src_lat, src_lon, lats, lons)
    lt = {int(v // np.timedelta64(1, "h")) if isinstance(v, np.timedelta64)
          else int(v / dt.timedelta(hours=1)): i for i, v in enumerate(da.lead_time.values)}
    missing = [l for l in want if l not in lt]
    if missing:
        # model horizon cannot cover lead L + the full event window at that
        # lead (e.g. 6-hourly leads only to 240 h): record, don't crash.
        return None, f"beyond horizon (need {missing[-1]} h, max {max(lt)} h)"
    steps = np.stack([field[lt[lead + 24 * k + h + off]]
                      for k in range(ndays) for h in (0, 6, 12, 18)])
    steps = steps.reshape(ndays, 4, len(lats), len(lons))
    return steps, "ok"


# ------------------------------------------------------------------ builders
def build_heat_freeze(ev: dict, cutoff: dt.datetime, leads) -> dict:
    etype = ev["event_type"]
    bbox = tuple(ev["bbox"])
    lats, lons = dst_grid(bbox, CELL[etype])
    start = dt.datetime.fromisoformat(ev["start"])
    end = dt.datetime.fromisoformat(ev["end"])
    times = truth_times(start, end if end > start else start + dt.timedelta(days=1))
    days = sorted({t.date() for t in times})
    nd = len(days)
    quantile = float((ev.get("details") or {}).get("quantile")
                     or (0.85 if etype == "heat_wave" else 0.15))

    t, lon, lat, truth_src = open_truth_window(times, bbox, cutoff)
    t = regrid(t, lat, lon, lats, lons)
    thr, clim_used = thresholds_for(times, lats, lons, quantile)
    land = land_mask(lats, lons)
    passed = ((t > thr) if etype == "heat_wave" else (t < thr)) & land
    daily = passed.reshape(nd, 4, len(lats), len(lons)).min(axis=1).astype(bool)
    consec = consecutive_days(daily)
    anchor = int(np.argmax(daily.sum(axis=(1, 2))))

    models = {}
    t0 = time.monotonic()
    thr4 = thr.reshape(nd, 4, len(lats), len(lons))
    for m in model_list():
        if time.monotonic() - t0 > FETCH_BUDGET_S:
            models[m] = {"status": "skipped (budget)"}
            continue
        entry = {}
        for lead in leads:
            steps, status = model_daily_pass(m, days[0], nd, lead, None, bbox, lats, lons)
            if steps is None:
                entry[str(lead)] = {"status": status}
                continue
            mp = ((steps > thr4) if etype == "heat_wave" else (steps < thr4))
            md = mp.min(axis=1).astype(bool) & land
            mc = consecutive_days(md)
            entry[str(lead)] = {
                "max_consec": int(mc.max()),
                "footprint_cells": int(md[anchor].sum()),
                "consec": pack_u8(mc[None][0], 0, max(nd, 1)),
                "daily_bits": pack_bits(md),
                "status": "ok",
            }
        models[m] = entry
    return {
        "schema": SCHEMA, "kind": "consecutive_days", "event_type": etype,
        "truth_source": truth_src,
        "quantile": quantile,
        "threshold_note": (f"OWB ERA5 1990-2019 climatology, P{round(quantile * 100)}"
                           if clim_used == "owb" else
                           "provisional: EWB shipped climatology (OWB store absent)"),
        "grid": {"lat": lats.tolist(), "lon": lons.tolist(), "cell_deg": CELL[etype]},
        "window": {"start": str(start.date()), "end": str((end - dt.timedelta(minutes=1)).date()),
                   "days": [str(d) for d in days]},
        "obs": {"max_consec": int(consec.max()), "anchor_day": str(days[anchor]),
                "footprint_cells": int(daily[anchor].sum()),
                "consec": pack_u8(consec[None][0], 0, max(nd, 1)),
                "daily_bits": pack_bits(daily),
                "daily": [pack_mask(d) for d in daily]},
        "models": models,
    }


def model_list() -> list[str]:
    from ewb_live.sources import api
    meta = ROOT / "data/results/meta.json"
    try:
        models = json.loads(meta.read_text()).get("models") or []
    except Exception:
        models = []
    have = set(api.list_models())
    ordered = [m for m in models if m in have]
    return ordered or sorted(have)


def build_ar(ev: dict, cutoff: dt.datetime, ar_leads) -> dict:
    bbox = tuple(ev["bbox"])
    lats, lons = dst_grid(bbox, CELL["atmospheric_river"])
    start = dt.datetime.fromisoformat(ev["start"])
    end = dt.datetime.fromisoformat(ev["end"])
    thr_val = float((ev.get("details") or {}).get("ivt_threshold_kg_m_s") or 400.0)

    truth = None
    truth_src = None
    anchor = None
    if start < cutoff:
        truth_src = "era5_mirror"
        truth, anchor = _ar_truth_at_anchor(start, min(end, cutoff), bbox, lats, lons)
    models = {}
    t0 = time.monotonic()
    if anchor is not None:
        for m in model_list():
            if time.monotonic() - t0 > FETCH_BUDGET_S:
                models[m] = {str(l): {"status": "skipped (budget)"} for l in ar_leads}
                continue
            entry = {}
            for lead in ar_leads:
                r = _ar_model_panel(m, anchor, lead, bbox, lats, lons, thr_val)
                entry[str(lead)] = r
            models[m] = entry
    out = {
        "schema": SCHEMA, "kind": "ar_ivt", "event_type": "atmospheric_river",
        "truth_source": truth_src, "ivt_threshold_kg_m_s": thr_val,
        "grid": {"lat": lats.tolist(), "lon": lons.tolist(), "cell_deg": CELL["atmospheric_river"]},
        "obs": truth, "models": models,
    }
    return out


def _ar_compute_bbox(bbox, lon_native):
    """(lmin, lmax, onmin, onmax) for the AR compute window (AR_COMPUTE_PAD_*).

    onmin=None means DO NOT crop longitude. That is the fallback when the
    padded arc crosses the native grid's own origin (lon 0 on ERA5's 0..360):
    PeriodicBoundaryIndex would then return the two sides CONCATENATED, so the
    longitude axis jumps 56 -> 335 in the middle of the array — invalid for the
    mask stencils, and it is precisely the case where the seam has to be
    stitched. Computing globally is the honest answer (ar_mask sees a global
    axis and wraps it for real); it costs compute, never correctness.
    """
    from ewb_live.events.seam import pad_bbox_180

    lmin, lmax, onmin, onmax = pad_bbox_180(bbox, AR_COMPUTE_PAD_LAT_DEG,
                                            AR_COMPUTE_PAD_LON_DEG)
    if onmin == -180.0 and onmax == 180.0:      # padded past a revolution
        return lmin, lmax, None, None
    sl = _lon_slice(lon_native, onmin, onmax)
    if sl.stop > float(np.nanmax(lon_native)) + 1e-6:
        logger.warning("AR compute window (lon %.1f..%.1f) crosses the grid's "
                       "lon origin; computing the mask globally", onmin, onmax)
        return lmin, lmax, None, None
    return lmin, lmax, onmin, onmax


def _ar_inputs_window(t0, t1, bbox):
    """EWB-named u/v/q on levels>=300 over the AR compute window, 6-hourly
    (lazy). Wider than the event bbox — see AR_COMPUTE_PAD_* and
    _ar_compute_bbox: the mask is computed ON THIS WINDOW, so its edges are
    array edges and a tight crop cuts the plume it is supposed to show."""
    from ewb_live.scoring import truth_mirror
    from extremeweatherbench import inputs as ewb_inputs
    ds = truth_mirror.open_mirror()
    mapping = ewb_inputs.ERA5_metadata_variable_mapping
    needed = {src: ewb for src, ewb in mapping.items()
              if ewb in ("eastward_wind", "northward_wind", "specific_humidity")}
    ds = ds[list(needed)].rename(needed)
    ds = ds.sel(level=ds.level >= 300)
    ds = ds.rename({"time": "valid_time"})
    lon_native = np.asarray(ds.longitude.values, float)
    lmin, lmax, onmin, onmax = _ar_compute_bbox(bbox, lon_native)
    lat_native = np.asarray(ds.latitude.values, float)
    lat_idx = np.where((lat_native >= lmin) & (lat_native <= lmax))[0]
    if onmin is not None:
        ds, _ = _crop_lon(ds, lon_native, onmin, onmax)
    return (ds.sel(valid_time=slice(t0, t1 - dt.timedelta(hours=6)))
            .isel(latitude=lat_idx)
            .chunk({"valid_time": 1, "level": -1, "latitude": -1, "longitude": -1}))


def _ivt_and_mask(ds_step):
    """(IVT, AR mask) for one step, from the seam-periodic EWB detector
    (ewb_live/events/ar_mask.py — same thresholds; upstream's non-wrapping
    stencil/labeling would cut the plume at the crop edge AND at the prime
    meridian, and the crop edge IS an array edge here)."""
    from ewb_live.events import ar_mask
    out = ar_mask.build_atmospheric_river_mask_and_land_intersection(
        ds_step, output_variables=["integrated_vapor_transport", "atmospheric_river_mask"])
    ivt = out["integrated_vapor_transport"]
    mask = out["atmospheric_river_mask"]
    import dask
    with dask.config.set(scheduler="threads", num_workers=4):
        ivt_np, mask_np = dask.compute(ivt, mask)
    iv = np.asarray(ivt_np)
    if iv.ndim == 3:
        iv = iv[0]
    mk = np.asarray(mask_np)
    if mk.ndim == 3:
        mk = mk[0]
    return iv, mk.astype(bool)


def _ar_truth_at_anchor(start, end, bbox, lats, lons):
    """AR truth for the event window: the peak-area step (anchor) plus ONE
    mask per valid day (the day's largest step).

    The per-day masks (obs.daily_bits / obs.day_times) exist because the
    anchor-only shortcut froze the home-page AR footprint on every active day
    — global_timeline repeats obs.daily_bits per day, so an AR that steps 900
    km a day used to be drawn in the SAME place for three days and then pop
    out of existence. The anchor stays what the detail view draws."""
    times = truth_times(start, end)
    best = None
    per_day: dict = {}
    ds_all = _ar_inputs_window(times[0], times[-1] + dt.timedelta(hours=6), bbox)
    for t in times:
        try:
            ivt, mask = _ivt_and_mask(ds_all.sel(valid_time=[np.datetime64(t)]))
        except Exception as exc:
            logger.warning("AR truth %s failed: %r", t, exc)
            continue
        iv_r = regrid(ivt, np.asarray(ds_all.latitude.values, float),
                      np.asarray(ds_all.longitude.values, float), lats, lons)
        mk_r = regrid(mask.astype(np.float32), np.asarray(ds_all.latitude.values, float),
                      np.asarray(ds_all.longitude.values, float), lats, lons) > 0.5
        area = int(mk_r.sum())
        if best is None or area > best["area"]:
            best = {"time": t, "area": area, "ivt": iv_r, "mask": mk_r}
        d = t.date()
        if d not in per_day or area > per_day[d]["area"]:
            per_day[d] = {"time": t, "area": area, "mask": mk_r}
    if best is None:
        return None, None
    days = sorted(per_day)
    out = {
        "anchor_time": best["time"].strftime("%Y-%m-%dT%H:%M"),
        "area_cells": best["area"],
        "ivt": pack_u8(np.minimum(best["ivt"], 1100), 0, 1100),
        "mask": pack_mask(best["mask"]),
    }
    if days:
        stack = np.stack([per_day[d]["mask"] for d in days])
        out["daily_bits"] = pack_bits(stack)
        out["day_times"] = [per_day[d]["time"].strftime("%Y-%m-%dT%H:%M")
                            for d in days]
    return out, best["time"]


# DR2 B2: user-facing reason vocabulary — never render exception class names.
# The dashboard renders these strings verbatim; raw exception detail stays in
# the server log (logger.debug).
_REASON = {
    "ValueError": "field not derivable for this archive",
    "KeyError": "field not derivable for this archive",
    "FileNotFoundError": "archive missing for this run",
    "IndexError": "lead outside archive window",
    "OSError": "archive missing for this run",
}


def _reason(exc: Exception) -> str:
    # GFS-forced archives have holes (missing 00Z/12Z runs); s3fs reports the
    # missing key as this TypeError. To a reader it just means "no run then".
    if "create_client() got multiple values" in str(exc):
        return "no archived run near this date"
    return _REASON.get(type(exc).__name__, "build failed")


def _ar_model_panel(model, anchor, lead, bbox, lats, lons, thr_val):
    from ewb_live.sources import api
    init = anchor - dt.timedelta(hours=lead)
    init = init.replace(hour=(init.hour // 6) * 6, minute=0, second=0)
    need = ["specific_humidity", "eastward_wind", "northward_wind"]
    try:
        spec = api.model_info(model)
    except KeyError:
        return {"status": "unavailable (unknown model)"}
    lack = [v for v in need if v not in spec.variables]
    if lack:
        # e.g. fourcastnet archives store relative humidity, not q: IVT
        # cannot be computed as-is. Say so instead of a blank tile.
        return {"status": "unsupported (no specific humidity in this archive)"}
    try:
        ds, off = api.open_forecast_nearest_init(
            model, init, need, lead_hours=[lead], bbox=bbox, window_h=6)
        ds = ds[need]
        if "air_pressure" in ds.coords:
            ds = ds.rename({"air_pressure": "level"})
        ds = ds.sel(level=ds.level >= 300)
        ivt, mask = _ivt_and_mask(ds.isel(init_time=0)
                                  .sel(lead_time=np.timedelta64(lead + off, "h"), method="nearest")
                                  .chunk({"level": -1, "latitude": -1, "longitude": -1}))
        lead_used = lead
        lat = np.asarray(ds.latitude.values, float)
        lon = np.asarray(ds.longitude.values, float)
        iv_r = regrid(ivt, lat, lon, lats, lons)
        mk_r = regrid(mask.astype(np.float32), lat, lon, lats, lons) > 0.5
        return {"status": "ok", "area_cells": int(mk_r.sum()),
                "ivt": pack_u8(np.minimum(iv_r, 1100), 0, 1100),
                "mask": pack_mask(mk_r)}
    except Exception as exc:
        logger.debug("ar_panel %s @%s failed: %s", model, init, exc)
        return {"status": f"unavailable ({_reason(exc)})"}


# ------------------------------------------------------------------ driver
def build_event(ev: dict, cutoff: dt.datetime, heat_leads, ar_leads) -> dict:
    if ev["event_type"] in ("heat_wave", "freeze"):
        return build_heat_freeze(ev, cutoff, heat_leads)
    return build_ar(ev, cutoff, ar_leads)


def write_event(ev_id: str, doc: dict) -> Path:
    d = OUT_DIR / ev_id
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"viz.json.tmp.{os.getpid()}"  # sweep matches *.tmp.*
    tmp.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))
    tmp.rename(d / "viz.json")
    return d / "viz.json"


def update_index(entries: dict[str, dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "index.json"
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text()).get("events", {})
        except Exception:
            old = {}
    old.update(entries)
    doc = {"schema": SCHEMA, "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "events": old}
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")  # sweep *.tmp.*
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    tmp.rename(path)
