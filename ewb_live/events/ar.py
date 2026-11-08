"""Atmospheric-river event detection on ERA5.

EWB ships the AR *mask* algorithm (``events.atmospheric_river``: IVT >= 400
kg/m/s AND |Gaussian-blurred Laplacian(IVT)| >= 2.5 kg/m2/s2 dilated by 8
grid points, connected components >= 500 grid points, centroid |lat| > 15)
and its land intersection (natural_earth land_110), but no global AR *case*
scanner (``data_prep/ar_bounds.py`` only tightens curated cases). This module
applies EWB's own mask builder to 6-hourly ERA5 ARCO, then tracks the daily
land-intersection footprints with the same blob tracker used for temperature
events, keeping events with >= AR_MIN_LAND_PIXELS land points on the peak day
and >= 2 landfall days (the same land-pixel floor EWB uses in
``data_prep/ar_bounds.py``).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

import numpy as np
import xarray as xr

from ._upstream import temperature_events as _te
from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

# Land-intersection floors (ar_bounds.py MIN_LAND_PIXELS_FOR_PEAK / window)
AR_MIN_LAND_PIXELS = 10
AR_MIN_DAYS = 2
AR_MAX_DURATION_DAYS = 9   # upstream cases.yaml AR p90 duration (was 12)
AR_PAD_DEG = 5.0
# Product-owner rule 2026-11-06: an AR must START over the ocean (ocean
# fraction of the IVT mask on the first tracked day >= this) and make
# landfall (guaranteed by the land-pixel tracking floors); objects whose
# mask is (almost) all land throughout are rejected at the candidate stage.
# Fractions are computed from EWB's own mask vs its land intersection on
# the event's (padded) bbox region, per day. Thresholds env-overridable.
AR_OCEAN_FRAC_START_MIN = float(os.environ.get("EWB_LIVE_AR_OCEAN_FRAC_START_MIN", "0.5"))
AR_ALL_LAND_OCEAN_FRAC_MAX = float(os.environ.get("EWB_LIVE_AR_ALL_LAND_OCEAN_FRAC_MAX", "0.05"))

# Science-review B8 (2026-11-07): tropical-cyclone cores contaminate the AR
# lane — fbe99f30e625 = Typhoon Dolphin (peak land IVT 2972 kg/m/s,
# physically impossible for an extratropical AR), 6ad93bd2a76d = Hurricane
# Isaias (2018). Calibrated against the selected set: legitimate events top
# out at 1435 kg/m/s, so 1500 is the hard plausibility cap; the compact-core
# rule (a round, IVT>=1000 blob) catches TCs that land below the cap.
AR_IVT_MAX_KGM_S = float(os.environ.get("EWB_LIVE_AR_IVT_MAX_KGM_S", "1500"))
AR_TC_FILL_RATIO_MIN = float(os.environ.get("EWB_LIVE_AR_TC_FILL_RATIO_MIN", "0.55"))
AR_TC_IVT_MIN_KGM_S = float(os.environ.get("EWB_LIVE_AR_TC_IVT_MIN_KGM_S", "1000"))


def tc_plausibility_rule(peak_ivt_kgm_s: float | None,
                         peak_day_mask: np.ndarray | None) -> str | None:
    """TC-core rejection for one segment: None = keep, else the reason.

    ``peak_day_mask`` is the full AR-IVT mask of the segment's peak day on
    its bbox (not land-restricted) — TC cores are compact (high bbox fill
    ratio), ARs are elongated filaments (low fill).
    """
    if peak_ivt_kgm_s is not None and peak_ivt_kgm_s > AR_IVT_MAX_KGM_S:
        return "ivt_implausible"
    if (peak_ivt_kgm_s is not None and peak_ivt_kgm_s >= AR_TC_IVT_MIN_KGM_S
            and peak_day_mask is not None and peak_day_mask.any()):
        rows = np.where(peak_day_mask.any(axis=1))[0]
        cols = np.where(peak_day_mask.any(axis=0))[0]
        fill = peak_day_mask.sum() / ((rows[-1] - rows[0] + 1)
                                      * (cols[-1] - cols[0] + 1))
        if fill >= AR_TC_FILL_RATIO_MIN:
            return "tc_compact_core"
    return None


def ocean_land_rule(daily_ar_region: np.ndarray,
                    daily_land_region: np.ndarray) -> dict | None:
    """Ocean-origin/landfall verdict for one tracked segment.

    ``daily_ar_region``: full IVT mask (day, lat, lon) over the segment's
    bbox; ``daily_land_region``: the same mask restricted to land (EWB land
    intersection). Returns ``None`` when the segment is rejected:
    * first tracked day ocean fraction < AR_OCEAN_FRAC_START_MIN (must
      originate as an oceanic plume), or
    * ocean fraction < AR_ALL_LAND_OCEAN_FRAC_MAX on EVERY day (all-land
      object), or
    * no mask cells at all.
    """
    tot = daily_ar_region.reshape(daily_ar_region.shape[0], -1).sum(axis=1)
    land = daily_land_region.reshape(daily_land_region.shape[0], -1).sum(axis=1)
    valid = tot > 0
    if not valid.any():
        return None
    ocean_frac = 1.0 - land[valid] / tot[valid]
    if ocean_frac.max() < AR_ALL_LAND_OCEAN_FRAC_MAX:
        return None                      # all-land object
    if ocean_frac[0] < AR_OCEAN_FRAC_START_MIN:
        return None                      # did not start over the ocean
    landfall = np.where(land >= AR_MIN_LAND_PIXELS)[0]
    return {
        "ocean_frac_start": round(float(ocean_frac[0]), 3),
        "ocean_frac_max": round(float(ocean_frac.max()), 3),
        "land_frac_peak": round(float(land[int(np.argmax(tot))] /
                                       tot[int(np.argmax(tot))]), 3),
        "landfall_step": int(landfall[0]) if landfall.size else None,
    }
# AR is a synoptic-scale feature; EWB cases evaluate the 6-hourly signal
AR_HOURS = (0, 6, 12, 18)


def _open_ar_inputs(arco_uri: str, storage_options: dict) -> xr.Dataset:
    """u/v/q on levels >= 300 hPa, EWB variable names, dim 'valid_time'."""
    from extremeweatherbench import inputs as ewb_inputs

    mapping = ewb_inputs.ERA5_metadata_variable_mapping
    ds = xr.open_zarr(arco_uri, storage_options=storage_options, chunks=None)
    needed = {src: ewb for src, ewb in mapping.items() if ewb in
              ("eastward_wind", "northward_wind", "specific_humidity")}
    ds = ds[list(needed)].rename(needed)
    ds = ds.sel(level=ds.level >= 300)
    ds = ds.rename({"time": "valid_time"})
    return ds[["eastward_wind", "northward_wind", "specific_humidity"]]


def _daily_masks(
    ds_step: xr.Dataset, scheduler_workers: int = 6
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute daily land-intersection masks, the full daily AR mask, and
    daily peak masked IVT.

    ``ds_step`` covers a few days of 6-hourly steps. Returns
    (daily_land bool (day, lat, lon), daily_ar bool (full mask, day, lat,
    lon), daily_peak_ivt float32 (day, lat, lon), day_times datetime64[D]).
    """
    from extremeweatherbench.events import atmospheric_river as ar

    ds_step = ds_step.chunk(
        {"valid_time": 1, "level": -1, "latitude": -1, "longitude": -1}
    )
    out = ar.build_atmospheric_river_mask_and_land_intersection(
        ds_step,
        output_variables=["atmospheric_river_mask", "atmospheric_river_land_intersection",
                          "integrated_vapor_transport"],
    )
    li = out["atmospheric_river_land_intersection"] > 0
    full = out["atmospheric_river_mask"] > 0
    ivt_masked = out["integrated_vapor_transport"].where(li)

    def _daily(da):
        return (
            da.astype("float32")
            .resample(valid_time="1D")
            .max(skipna=True)
            .fillna(0.0)
            .astype(bool)
        )

    peak_ivt = ivt_masked.resample(valid_time="1D").max()
    import dask

    with dask.config.set(scheduler="threads", num_workers=scheduler_workers):
        daily_np, ar_np, peak_np = dask.compute(_daily(li), _daily(full), peak_ivt)
    day_times = np.asarray(daily_np.valid_time).astype("datetime64[D]")
    return (
        np.asarray(daily_np, dtype=bool),
        np.asarray(ar_np, dtype=bool),
        np.asarray(peak_np, dtype=np.float32),
        day_times,
    )


def scan_ar_events(
    start: dt.date,
    end: dt.date,
    *,
    arco_uri: str | None = None,
    chunk_days: int = 4,
    scheduler_workers: int = 6,
) -> list[Event]:
    """Detect land-falling atmospheric rivers over [start, end] (inclusive)."""
    if arco_uri is None:
        from extremeweatherbench import inputs as ewb_inputs

        arco_uri = ewb_inputs.ARCO_ERA5_FULL_URI

    logger.info("AR scan %s -> %s", start, end)
    # rolling-window refinement (see temperature.py): scan 14 days earlier so
    # in-progress ARs keep stable start dates across weekly runs; events with
    # no active day inside [start, end] are dropped below.
    load_start = start - dt.timedelta(days=_te().TEMPORAL_LOAD_BUFFER_DAYS)
    t = _open_ar_inputs(arco_uri, {"token": "anon"})
    hours_ok = t.valid_time.dt.hour.isin(list(AR_HOURS))
    t = t.sel(valid_time=hours_ok)
    t = t.sel(valid_time=slice(str(load_start), str(end) + "T23:59:59"))

    daily_parts: list[np.ndarray] = []
    ar_full_parts: list[np.ndarray] = []
    peak_parts: list[np.ndarray] = []
    date_parts: list[np.ndarray] = []
    cur = np.datetime64(load_start)
    end_np = np.datetime64(end)
    while cur <= end_np:
        stop = min(end_np, cur + np.timedelta64(chunk_days - 1, "D"))
        chunk = t.sel(valid_time=slice(str(cur), str(stop + np.timedelta64(1, "D"))))
        if chunk.sizes["valid_time"] == 0:
            cur = stop + np.timedelta64(1, "D")
            continue
        d, a, p, days = _daily_masks(chunk, scheduler_workers)
        daily_parts.append(d)
        ar_full_parts.append(a)
        peak_parts.append(p)
        date_parts.append(days)
        logger.info("  AR chunk %s..%s: %d land-AR pixels, %d total AR pixels",
                    cur, stop, int(d.sum()), int(a.sum()))
        cur = stop + np.timedelta64(1, "D")
        del chunk

    if not daily_parts:
        return []
    daily = np.concatenate(daily_parts, axis=0)
    daily_ar = np.concatenate(ar_full_parts, axis=0)
    peak_ivt = np.concatenate(peak_parts, axis=0)
    dates = np.concatenate(date_parts, axis=0)
    del daily_parts, ar_full_parts, peak_parts

    lats = t.latitude.values
    lons = t.longitude.values

    te = _te()
    blobs = te.detect_events(
        daily, dates, lats, lons, "atmospheric_river", area_grid=None, min_seed_area_km2=0.0
    )
    logger.info("AR raw tracked blobs: %d", len(blobs))

    # owner 2026-11-08 (G-40): IBTrACS TC colocation screen (mask untouched)
    from .tc_screen import TC_SCREEN_KM, TC_SCREEN_HOURS, TC_SCREEN_ON
    from .tc_screen import load_ibtracs_tracks, tc_colocation_rule

    years = {int(d.astype("datetime64[Y]").astype(int)) + 1970
             for d in np.unique(dates.astype("datetime64[Y]"))}
    tc_tracks = load_ibtracs_tracks(years) if TC_SCREEN_ON else None

    out: list[Event] = []
    win0 = np.datetime64(start)
    win1 = np.datetime64(end)
    funnel = {"segments": 0, "short": 0, "land_floor": 0, "ocean_rule": 0,
              "tc_guard": 0, "tc_track": 0, "kept": 0}
    for ev in blobs:
        d0 = np.datetime64(ev["start"]).astype("datetime64[D]")
        d1 = np.datetime64(ev["end"]).astype("datetime64[D]")
        if d1 < win0 or d0 > win1:
            continue
        lat_min, lat_max = float(ev["lat_min"]), float(ev["lat_max"])
        lon_min, lon_max = float(ev["lon_min"]), float(ev["lon_max"])
        ilat = (lats <= lat_max) & (lats >= lat_min)
        if lon_min <= lon_max:
            ilon = (lons >= lon_min) & (lons <= lon_max)
        else:  # bbox arc wraps the 0 meridian in 0-360 space
            ilon = (lons >= lon_min) | (lons <= lon_max)
        ti_all = np.where((dates >= d0) & (dates <= d1))[0]
        if len(ti_all) < AR_MIN_DAYS:
            continue
        # overlong tracks (monsoon/TC moisture plumes) split into <=12-day
        # consecutive segments (upstream AR cases: p90 duration 8 d)
        n_seg = max(1, int(np.ceil(len(ti_all) / AR_MAX_DURATION_DAYS)))
        for ti in np.array_split(ti_all, n_seg):
            if len(ti) < AR_MIN_DAYS:
                funnel["short"] += 1
                continue
            if dates[ti].max() < win0 or dates[ti].min() > win1:
                continue  # no active day inside the requested window
            funnel["segments"] += 1
            region = daily[np.ix_(ti, ilat, ilon)]
            daily_px = region.reshape(len(ti), -1).sum(axis=1)
            peak_px = int(daily_px.max())
            land_days = int((daily_px >= AR_MIN_LAND_PIXELS).sum())
            if land_days < AR_MIN_DAYS or peak_px < AR_MIN_LAND_PIXELS:
                funnel["land_floor"] += 1
                continue
            # product-owner rule: oceanic origin + landfall (landfall is
            # implied by the floors above; here reject land-locked plumes)
            rule = ocean_land_rule(daily_ar[np.ix_(ti, ilat, ilon)], region)
            if rule is None:
                funnel["ocean_rule"] += 1
                continue
            peak_day = int(np.argmax(daily_px))
            w = region[peak_day].sum(axis=1).astype(np.float64)
            centroid_lat = (
                float((lats[ilat] * w).sum() / w.sum()) if w.sum() > 0 else float("nan")
            )
            ivt_reg = peak_ivt[np.ix_(ti, ilat, ilon)][region]
            peak_val = float(np.nanmax(ivt_reg)) if ivt_reg.size else None
            # science-review B8: TC cores are not ARs (implausible IVT or a
            # compact vortex-shaped blob at high IVT)
            tc_reason = tc_plausibility_rule(
                peak_val, daily_ar[np.ix_(ti, ilat, ilon)][peak_day])
            if tc_reason:
                funnel["tc_guard"] += 1
                logger.info("AR segment %s..%s rejected by TC guard (%s, peak_ivt=%s)",
                            dates[ti].min(), dates[ti].max(), tc_reason, peak_val)
                continue
            # G-40 screen: TC center co-located with the AR component's mask
            tc_hit = tc_colocation_rule(
                dates[ti], lats[ilat], lons[ilon],
                daily_ar[np.ix_(ti, ilat, ilon)], tc_tracks)
            if tc_hit:
                funnel["tc_track"] += 1
                logger.info("AR segment %s..%s rejected by IBTrACS screen (%s %s, "
                            "%s km of mask @ %s)", dates[ti].min(), dates[ti].max(),
                            tc_hit["ibtracs_sid"], tc_hit["tc_name"],
                            tc_hit["distance_km"], tc_hit["matched_time"])
                continue

            start_dt = dt.datetime.combine(dates[ti].min().astype(dt.date), dt.time())
            end_dt = dt.datetime.combine(dates[ti].max().astype(dt.date), dt.time()) + dt.timedelta(days=1)
            bbox = (
                max(-90.0, lat_min - AR_PAD_DEG),
                min(90.0, lat_max + AR_PAD_DEG),
                normalize_lon_180(lon_min - AR_PAD_DEG),
                normalize_lon_180(lon_max + AR_PAD_DEG),
            )
            eid = make_event_id("atmospheric_river", start_dt, bbox)
            details: dict[str, Any] = {
                "ivt_threshold_kg_m_s": 400.0,
                "laplacian_threshold_kg_m2_s2": 2.5,
                "dilation_radius_gridpoints": 8,
                "min_mask_gridpoints": 500,
                "peak_land_pixels": peak_px,
                "landfall_days": land_days,
                "centroid_lat": round(centroid_lat, 2),
                "track_split": n_seg > 1,
                "ocean_frac_start": rule["ocean_frac_start"],
                "ocean_frac_max": rule["ocean_frac_max"],
                "land_frac_peak": rule["land_frac_peak"],
                "landfall_time": (
                    str(dates[ti][rule["landfall_step"]])
                    if rule["landfall_step"] is not None else None),
                "detection": "EWB AR mask + land intersection blob tracking",
                # provenance for the IBTrACS screen (owner 2026-11-08): kept
                # segments record the screen config they passed
                "tc_screen": {"radius_km": TC_SCREEN_KM, "hours": TC_SCREEN_HOURS,
                              "tracks_available": tc_tracks is not None,
                              "ibtracs_sid": None},
            }
            out.append(
                Event(
                    id=eid,
                    event_type="atmospheric_river",
                    title=f"Atmospheric River {dates[ti].min()} to {dates[ti].max()}",
                    start=start_dt,
                    end=end_dt,
                    bbox=bbox,
                    peak_value=round(peak_val, 1) if peak_val is not None and np.isfinite(peak_val) else None,
                    details=details,
                )
            )
            funnel["kept"] += 1
    out.sort(key=lambda e: e.start)
    logger.info("AR events kept: %d (funnel: %s)", len(out), funnel)
    return out
