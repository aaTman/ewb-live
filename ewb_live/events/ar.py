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

from . import ar_mask
from ._upstream import temperature_events as _te
from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

# Land-intersection floors (ar_bounds.py MIN_LAND_PIXELS_FOR_PEAK / window)
AR_MIN_LAND_PIXELS = 10
# detection-rule version (cache key, events/__init__._cache_path): bump on
# any AR mask/screen rule change. v2 = SPEC "AR tropical exclusion" 2026-11-08
AR_RULE_REV = "v7"  # v3 = track exclusion; v4 = seam-periodic labeling;
                    # v5 = 1-step gap bridge (display-only bridged cells;
                    # physical keep unchanged, event ids stable)
                    # v6 = seam-periodic AR mask (events/ar_mask: the stencil
                    # and the >=500-cell floor wrap in longitude, so an AR
                    # across Greenwich is no longer cut at the prime meridian)
                    # + PUBLISHED BBOX = the AR object's own IVT-mask extent
                    # (screens/detections unchanged); bbox is in the event id,
                    # v7 = TRACK definition (events/ar_filter): centroid drift
                    # cap + 40-step age cap on linking, and the tropical
                    # screen now keys on the track CENTROID staying inside
                    # +/-15 deg for a full day instead of any cell at any
                    # step (owner 2026-11-10: a month-long chain hid 1.92M
                    # cells this way and "reappeared" as a new id)
                    # so ids shift and the window re-detects once.
# Search window (deg) around a land-falling blob for the AR OBJECT it belongs
# to: the kept AR cells of the blob's own tracks, whose extent becomes the
# published bbox. An AR filament routinely runs 30+ deg out over the ocean
# ahead of its landfall, and the land blob alone is what used to be published
# as the event bbox — so every crop that bbox produced sliced the plume (25/30
# live AR viz assets clipped their own mask; for the 2026-08-27 Europe AR the
# clip line sat exactly on the prime meridian).
AR_OBJECT_SEARCH_PAD_DEG = 30.0
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


def _step_arrays(
    ds_step: xr.Dataset, scheduler_workers: int = 6
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Raw 6-hourly arrays for one chunk: (AR mask, land intersection, IVT,
    step times). Tracking + exclusion happen on the full window above.

    The mask comes from ewb_live.events.ar_mask — EWB's detector with the
    longitude stencils/labeling made periodic (see that module: upstream's
    non-wrapping Laplacian, dilation and component floor cut ARs at the
    prime meridian, which is the array edge of a 0..360 ERA5 grid).
    """
    from extremeweatherbench.events import atmospheric_river as ar

    ds_step = ds_step.chunk(
        {"valid_time": 1, "level": -1, "latitude": -1, "longitude": -1}
    )
    out = ar_mask.build_atmospheric_river_mask_and_land_intersection(
        ds_step,
        output_variables=["atmospheric_river_mask", "atmospheric_river_land_intersection",
                          "integrated_vapor_transport"],
    )
    import dask

    with dask.config.set(scheduler="threads", num_workers=scheduler_workers):
        m, li, ivt = dask.compute(
            out["atmospheric_river_mask"] > 0,
            out["atmospheric_river_land_intersection"] > 0,
            out["integrated_vapor_transport"].astype("float32"),
        )
    return (
        np.asarray(m, dtype=bool),
        np.asarray(li, dtype=bool),
        np.asarray(ivt, dtype=np.float32),
        np.asarray(out.valid_time, "datetime64[h]"),
    )


def _object_extent(labels6: np.ndarray, ti: np.ndarray, region: np.ndarray,
                   ilat: np.ndarray, ilon: np.ndarray, lats: np.ndarray,
                   lons: np.ndarray) -> tuple[float, float, float, float] | None:
    """Extent (lat_min, lat_max, west, east) of the AR OBJECT a segment fell
    on to: the kept AR cells of the very tracks that own its land cells,
    inside a window grown by AR_OBJECT_SEARCH_PAD_DEG around the land blob.

    The published bbox must describe the AR, not only where it came ashore —
    the land blob alone clipped 25/30 live AR crops (and for the 2026-08-27
    Europe AR it clipped it exactly along the prime meridian). Returns None
    when the segment's land cells carry no track label (caller keeps the
    land-blob box).

    lon endpoints come back in the longitude ARRAY's convention (west > east
    = the object straddles the array seam); the caller maps them with
    seam.arc_bbox_180.
    """
    from . import seam

    lab_win = labels6[np.ix_(ti, ilat, ilon)]
    ids = np.unique(lab_win[np.asarray(region, bool)])
    ids = ids[ids > 0]
    if ids.size == 0:
        return None

    la = np.flatnonzero(ilat)
    klat = int(round(AR_OBJECT_SEARCH_PAD_DEG / abs(float(lats[1] - lats[0]))))
    win_lat = np.arange(max(0, int(la[0]) - klat),
                        min(len(lats), int(la[-1]) + klat + 1))
    cell = 360.0 / len(lons)
    klon = int(round(AR_OBJECT_SEARCH_PAD_DEG / cell))
    grow = np.zeros(len(lons), bool)          # longitude wraps; latitude does not
    for off in range(-klon, klon + 1):
        grow |= np.roll(ilon, off)
    win_lon = np.flatnonzero(grow)
    if win_lat.size == 0 or win_lon.size == 0:
        return None

    obj = np.isin(labels6[np.ix_(ti, win_lat, win_lon)], ids)
    union = obj.any(axis=0)
    arc = seam.mask_arc(union, lons[win_lon])
    span = seam.mask_lat_span(union, lats[win_lat])
    if arc is None or span is None:
        return None
    if union[:, 0].any() or union[:, -1].any():
        # touching the search-window edge means the extent MIGHT continue
        # past it; for a seam-straddling object the arc crossing the seam is
        # exactly that and correct, so this is a debug breadcrumb, not a bug
        logger.debug("AR object touches the search-window lon edge (pad=%s deg)",
                     AR_OBJECT_SEARCH_PAD_DEG)
    return float(span[0]), float(span[1]), float(arc[0]), float(arc[1])


def _dominant_track_id(labels6, days, seg_dates, tracks6, ilat, ilon):
    """Track id owning most AR cells in the event's segment (persistent id,
    owner 2026-11-09; labels6 is the shared track-label array)."""
    counts: dict[int, int] = {}
    lidx = np.flatnonzero(ilat)
    oidx = np.flatnonzero(ilon)
    for D in seg_dates:
        for s in np.flatnonzero(days == D):
            lb = labels6[s][np.ix_(lidx, oidx)].ravel()
            vals, cnt = np.unique(lb[lb > 0], return_counts=True)
            for v, c in zip(vals, cnt):
                counts[int(v)] = counts.get(int(v), 0) + int(c)
    if not counts:
        return None
    return tracks6[max(counts.items(), key=lambda kv: kv[1])[0] - 1]["id"]


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

    mask_parts: list[np.ndarray] = []
    li_parts: list[np.ndarray] = []
    ivt_parts: list[np.ndarray] = []
    time_parts: list[np.ndarray] = []
    cur = np.datetime64(load_start)
    end_np = np.datetime64(end)
    while cur <= end_np:
        stop = min(end_np, cur + np.timedelta64(chunk_days - 1, "D"))
        chunk = t.sel(valid_time=slice(str(cur), str(stop + np.timedelta64(1, "D"))))
        if chunk.sizes["valid_time"] == 0:
            cur = stop + np.timedelta64(1, "D")
            continue
        m6, li6c, ivt6c, tv = _step_arrays(chunk, scheduler_workers)
        mask_parts.append(m6)
        li_parts.append(li6c)
        ivt_parts.append(ivt6c)
        time_parts.append(tv)
        logger.info("  AR chunk %s..%s: %d land-AR pixels, %d total AR pixels",
                    cur, stop, int(li6c.sum()), int(m6.sum()))
        cur = stop + np.timedelta64(1, "D")
        del chunk

    if not mask_parts:
        return []
    mask6 = np.concatenate(mask_parts)
    li6 = np.concatenate(li_parts)
    ivt6 = np.concatenate(ivt_parts)
    times6 = np.concatenate(time_parts)
    del mask_parts, li_parts, ivt_parts, time_parts
    # chunks overlap by one day (inclusive slice ends); keep first occurrence
    dup = np.zeros(times6.shape, bool)
    _, first_idx = np.unique(times6, return_index=True)
    dup[first_idx] = True
    mask6, li6, ivt6, times6 = mask6[dup], li6[dup], ivt6[dup], times6[dup]

    lats = t.latitude.values
    lons = t.longitude.values

    # owner 2026-11-09 (HIGH): ONE shared track-level screen (events/ar_filter)
    # — tracks link across the whole window, tropical/TC exclusion drops
    # WHOLE tracks (no frame-to-frame blink), then daily aggregation and
    # blob tracking see only surviving AR cells. Same fn drives the raw map.
    from .ar_filter import ar_filter_tracks
    from .tc_screen import TC_SCREEN_ON, load_ibtracs_tracks

    years = {int(d.astype("datetime64[Y]").astype(int)) + 1970
             for d in np.unique(times6.astype("datetime64[Y]"))}
    years |= {y + 1 for y in years} | {y - 1 for y in years}  # year-boundary IBTrACS gate
    tc_tracks = load_ibtracs_tracks(years) if TC_SCREEN_ON else None
    keep6, tracks_dropped, labels6, tracks6 = ar_filter_tracks(
        mask6, lats, lons, times6, tc_tracks)
    for tr in tracks_dropped:
        logger.info("AR track %s %s..%s dropped (%s%s)", tr["id"],
                    tr["start"], tr["end"], tr["reason"],
                    f": {tr.get('tc_name')} {tr.get('distance_km')} km"
                    if tr["reason"] == "tc" else "")
    days = times6.astype("datetime64[D]")
    uniq_days = np.unique(days)
    ny, nx = mask6.shape[1:]
    daily = np.zeros((len(uniq_days), ny, nx), dtype=bool)
    daily_ar = np.zeros_like(daily)
    peak_ivt = np.full((len(uniq_days), ny, nx), np.nan, dtype=np.float32)
    for j, D in enumerate(uniq_days):
        s = np.flatnonzero(days == D)
        g = li6[s] & keep6[s]
        daily[j] = g.any(axis=0)
        daily_ar[j] = keep6[s].any(axis=0)
        vmax = np.max(np.where(g, ivt6[s], -np.inf), axis=0)
        peak_ivt[j] = np.where(np.isneginf(vmax), np.nan, vmax)
    # attribution only: zero labels of dropped tracks so a dropped track
    # can never win _dominant_track_id inside a kept event's bbox
    # (ponytail 01:17Z SHOULD)
    labels6[~keep6] = 0
    del mask6, li6, ivt6, keep6
    dates = uniq_days
    logger.info("AR tracked: %d tracks over %d days (%d dropped: %d tropical, %d tc)",
                len(tracks6), len(dates), len(tracks_dropped),
                sum(t["reason"] == "tropical" for t in tracks_dropped),
                sum(t["reason"] == "tc" for t in tracks_dropped))

    te = _te()
    blobs = te.detect_events(
        daily, dates, lats, lons, "atmospheric_river", area_grid=None, min_seed_area_km2=0.0
    )
    logger.info("AR raw tracked blobs: %d", len(blobs))
    # owner 2026-11-09 (HIGH): the upstream tracker labels non-periodically,
    # so one AR crossing the 0-meridian arrives as two seam pieces; stitch
    # them back (bbox becomes the minimal arc, wrap = lon_min > lon_max)
    from .seam import arc_bbox_180, merge_seam_objects, seam_columns

    blobs = merge_seam_objects(blobs, lons)

    # G-40 screen (belt-and-braces on top of the track-level pass above;
    # tc_tracks already loaded for the track screen)
    from .tc_screen import TC_SCREEN_KM, TC_SCREEN_HOURS, tc_colocation_rule

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
        ilon = seam_columns(lons, lon_min, lon_max)
        ti_all = np.where((dates >= d0) & (dates <= d1))[0]
        if len(ti_all) < AR_MIN_DAYS:
            continue
        # overlong tracks (monsoon/TC moisture plumes) split into <=
        # AR_MAX_DURATION_DAYS-day consecutive segments (upstream AR cases:
        # p90 duration 8 d)
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
            # PUBLISHED bbox = the AR OBJECT (its IVT-mask extent), not the
            # land-falling blob: a 5 deg pad around a land-only footprint cut
            # the oceanic half of the plume out of every crop built from it.
            # All the SCREENS above stay on the land-blob window (their
            # thresholds are calibrated there); only the box moves.
            extent = _object_extent(labels6, ti, region, ilat, ilon, lats, lons)
            bbox_source = "ar_object"
            if extent is not None:
                o_lat_min, o_lat_max, o_west, o_east = extent
                # arc_bbox_180 returns the LONGITUDE PAIR only (west padded
                # west, east padded east, normalised to [-180, 180]; a wrap arc
                # comes back lon_min > lon_max, which the bbox encoding
                # understands). The latitudes come from the extent itself.
                lon_lo, lon_hi = arc_bbox_180(o_west, o_east, AR_PAD_DEG)
                lat_lo, lat_hi = o_lat_min, o_lat_max
            else:
                bbox_source = "land_blob"
                lon_lo = normalize_lon_180(lon_min - AR_PAD_DEG)
                lon_hi = normalize_lon_180(lon_max + AR_PAD_DEG)
                lat_lo, lat_hi = lat_min, lat_max
            bbox = (
                max(-90.0, lat_lo - AR_PAD_DEG),
                min(90.0, lat_hi + AR_PAD_DEG),
                lon_lo, lon_hi,
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
                # which extent produced bbox (see _object_extent)
                "bbox_source": bbox_source,
                # persistent AR track id (owner 2026-11-09): dominant track
                # label over the segment's steps (shared label array)
                "track_id": _dominant_track_id(labels6, days, dates[ti],
                                               tracks6, ilat, ilon),
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
