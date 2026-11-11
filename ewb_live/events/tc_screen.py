"""IBTrACS tropical-cyclone colocation screen for the AR lane.

Owner decision 2026-11-08 (science-review section G, guardian G-40): keep
upstream EWB's AR mask filters EXACTLY as-is for comparability (IVT >= 400,
Laplacian ridge, connected component >= 500 px, component mean |lat| > 15),
and additionally drop AR *candidates* whose mask is co-located in space and
time with an IBTrACS tropical-cyclone track center. The mask itself is never
modified — the raw exceedance map keeps the raw upstream mask (labeled raw).

Track data access reuses EWB's IBTrACS plumbing (inputs.IBTRACS_URI +
IBTrACS_metadata_variable_mapping). The full NOAA CSV (~200 MB) is fetched
at most once per EWB_LIVE_IBTRACS_MAX_AGE_H and cached as parquet under
data/cache/ibtracs/; a fetch failure falls back to the stale cache and, if
no cache exists, disables the screen for the run (detection must not die on
a NOAA blip — the next cycle retries).

Colocation rule (all env-configurable):
  a track point is a match when its center is within EWB_LIVE_TC_SCREEN_KM
  (default 250 km, matching upstream ibtracs_bounds.py's 250 km buffer
  scale) of any pixel of the AR component's mask on the nearest segment day
  AND within EWB_LIVE_TC_SCREEN_HOURS (default 24 h) of the segment window.
  Semantics note (science-review H3): the track gate is DAY-resolution —
  a track point matches a segment day with up to 24 h + ~12 h nearest-day
  slack (~36-48 h effective). Over-exclusionary by design: this is a
  screen, and a false drop costs one candidate, not a missed TC.
Set EWB_LIVE_TC_SCREEN=0 to disable.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TC_SCREEN_ON = os.environ.get("EWB_LIVE_TC_SCREEN", "1") != "0"
# owner 2026-11-08 (HIGH, SPEC "AR tropical exclusion" part b): 250 -> 500 km.
# 250 missed big-hurricane wind fields (34-kt radii commonly >300 km) and
# extratropical-transition storms whose mask detached from the center;
# IBTrACS track points carry all statuses so the buffer follows the
# transitioned remnant too. Applies to BOTH layers (tc_screen + ar_filter).
TC_SCREEN_KM = float(os.environ.get("EWB_LIVE_TC_SCREEN_KM", "500.0"))
TC_SCREEN_HOURS = float(os.environ.get("EWB_LIVE_TC_SCREEN_HOURS", "24.0"))
_MAX_AGE_H = float(os.environ.get("EWB_LIVE_IBTRACS_MAX_AGE_H", "24"))

COLS = ["storm_id", "tc_name", "valid_time", "latitude", "longitude"]


def _uri() -> str:
    override = os.environ.get("EWB_LIVE_IBTRACS_URI")
    if override:
        return override
    from extremeweatherbench import inputs as ewb_inputs

    return ewb_inputs.IBTRACS_URI


def _cache_dir() -> Path:
    return Path(os.environ.get("EWB_LIVE_IBTRACS_CACHE") or "data/cache/ibtracs")


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (km); inputs in degrees, broadcasting applies."""
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def load_ibtracs_tracks(years: set[int]):
    """Return a DataFrame(storm_id, tc_name, valid_time, latitude, longitude)
    filtered to the given seasons, or None when unavailable."""
    import pandas as pd

    cache = _cache_dir() / "tracks.parquet"
    fresh = cache.exists() and (
        (dt.datetime.now() - dt.datetime.fromtimestamp(cache.stat().st_mtime))
        .total_seconds() / 3600.0 < _MAX_AGE_H
    )
    if not fresh:
        try:
            uri = _uri()
            csv_bytes = urllib.request.urlopen(uri, timeout=600).read()
            import io

            raw = pd.read_csv(io.BytesIO(csv_bytes),
                              usecols=lambda c: c in ("SID", "ISO_TIME", "NAME",
                                                      "LAT", "LON", "SEASON"),
                              low_memory=False)
            from extremeweatherbench import inputs as ewb_inputs

            m = ewb_inputs.IBTrACS_metadata_variable_mapping
            raw = raw.rename(columns={k: v for k, v in m.items()
                                      if k in raw.columns})
            keep = [c for c in COLS + ["season"] if c in raw.columns]
            raw = raw[keep]
            cache.parent.mkdir(parents=True, exist_ok=True)
            raw.to_parquet(cache.with_suffix(".tmp.parquet"))
            cache.with_suffix(".tmp.parquet").replace(cache)
            logger.info("ibtracs: refreshed track cache (%d rows) from %s",
                        len(raw), uri)
        except Exception as exc:  # noqa: BLE001 — screen is fail-soft by design
            if cache.exists():
                logger.warning("ibtracs: refresh failed (%s); using stale cache", exc)
            else:
                logger.warning("ibtracs: no cache and fetch failed (%s); TC screen "
                               "DISABLED for this detection run", exc)
                return None
    if not cache.exists():
        return None
    try:
        df = pd.read_parquet(cache)
        df["valid_time"] = pd.to_datetime(df["valid_time"], errors="coerce")
        for c in ("latitude", "longitude"):
            df[c] = pd.to_numeric(df[c], errors="coerce")  # " " sentinels -> NaN
        df = df.dropna(subset=["valid_time", "latitude", "longitude"])
        if "season" in df.columns:
            season = pd.to_numeric(df["season"], errors="coerce")
            df = df[season.isin(list(years))]
            df = df.assign(season=season[df.index])
        else:
            df = df[df["valid_time"].dt.year.isin(list(years))]
        return df.dropna(subset=["storm_id"]).reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ibtracs: cache unreadable (%s); TC screen disabled", exc)
        return None


def tc_colocation_rule(dates_ti, lat_sel, lon_sel, mask_days,
                       tracks, *, km: float | None = None,
                       hours: float | None = None) -> dict[str, Any] | None:
    """Match an AR segment's daily mask against TC track centers.

    ``dates_ti``: np.datetime64[D] per segment day; ``lat_sel``/``lon_sel``:
    coordinate vectors of the segment's region (lon may be 0-360 native);
    ``mask_days``: (n_days, n_lat, n_lon) bool AR mask; ``tracks``: DataFrame
    from load_ibtracs_tracks. Returns {ibtracs_sid, tc_name, distance_km,
    matched_time} for the first match, else None.
    """
    if tracks is None or not TC_SCREEN_ON:
        return None
    tracks = tracks.assign(valid_time=pd.to_datetime(tracks["valid_time"]))
    km = TC_SCREEN_KM if km is None else km
    hours = TC_SCREEN_HOURS if hours is None else hours
    d0, d1 = pd.Timestamp(dates_ti.min()), pd.Timestamp(dates_ti.max())
    slack = pd.Timedelta(hours=hours) + pd.Timedelta(hours=12)  # day-resolution
    sub = tracks[(tracks["valid_time"] >= d0 - slack)
                 & (tracks["valid_time"] <= d1 + slack)]
    if sub.empty:
        return None
    # precompute pixel coords per segment day (lon normalized to [-180,180])
    lon_norm = (np.asarray(lon_sel, float) + 180.0) % 360.0 - 180.0
    day_px = []
    for k in range(len(dates_ti)):
        ys, xs = np.nonzero(mask_days[k])
        if len(ys):
            px = (np.repeat(lat_sel[ys], len(xs)).reshape(len(ys), len(xs)),
                  np.tile(lon_norm[xs], (len(ys), 1)))
        else:
            px = (np.empty((0, 1)), np.empty((0, 1)))
        day_px.append(px)
    day_idx = np.arange(len(dates_ti))
    dates_pd = pd.to_datetime(np.asarray(dates_ti))
    for sid, grp in sub.groupby("storm_id", sort=False):
        for _, pt in grp.iterrows():
            dd = np.abs((pd.to_datetime(pt["valid_time"]) - dates_pd)
                        .total_seconds()) / 3600.0
            k = int(day_idx[np.argmin(dd)])
            if dd[k] > 24.0 + hours:  # nearest segment day too far in time
                continue
            p_lat, p_lon = day_px[k]
            if p_lat.size == 0:
                continue
            dmin = float(_haversine_km(pt["latitude"], pt["longitude"],
                                       p_lat, p_lon).min())
            if dmin <= km:
                return {"ibtracs_sid": str(sid),
                        "tc_name": str(grp["tc_name"].iloc[0]),
                        "distance_km": round(dmin, 1),
                        "matched_time": str(pt["valid_time"])}
    return None
