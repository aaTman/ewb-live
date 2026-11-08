"""Truth-latency probe for ERA5 ARCO (single canonical implementation).

ERA5 ARCO (gs://gcp-public-data-arco-era5) lags wall clock by ~5-7 days.
This module probes the archive for its last fully-populated hour so the
pipeline knows which events can be scored against ERA5 and which need the
IFS-HRES-lead-0 analysis fallback (SPEC section 2).

The probe reads ``2m_temperature`` at 4 scattered grid points (bisection
over hourly indices, ~11 probes x 4 points), cached to
``data/cache/truth_cutoff.json`` with a 12 h TTL. ``ewb_live.scoring.truth``
re-exports ``probe_arco_cutoff`` from here; do not implement another probe.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

logger = logging.getLogger(__name__)

CUTOFF_CACHE_PATH = os.environ.get(
    "EWB_LIVE_TRUTH_CACHE", "data/cache/truth_cutoff.json"
)
CACHE_TTL_HOURS = 12

# (lat, lon) with lon in ARCO's 0-360 convention: Pacific, South Pacific,
# North Atlantic, West Pacific — all open ocean, populated whenever data lands
PROBE_POINTS = [(35.0, 260.0), (-20.0, 130.0), (55.0, 10.0), (10.0, 280.0)]


def _open_arco_t2m(arco_uri: str):
    import xarray as xr

    ds = xr.open_zarr(arco_uri, storage_options={"token": "anon"}, chunks=None)
    return ds["2m_temperature"]


def probe_arco_cutoff(
    now: dt.datetime | None = None,
    arco_uri: str | None = None,
    lookback_days: int = 14,
) -> dt.datetime:
    """Bisect ERA5 ARCO for the last hour with finite t2m at all probe points.

    Returns the last valid timestamp found (naive UTC). If even the oldest
    probed hour is NaN, raises RuntimeError.
    """
    import numpy as np

    if arco_uri is None:
        from extremeweatherbench import inputs as ewb_inputs

        arco_uri = ewb_inputs.ARCO_ERA5_FULL_URI
    now = now or dt.datetime.utcnow()
    t2m = _open_arco_t2m(arco_uri)
    times = t2m.time.values

    def is_valid(ts) -> bool:
        for lat, lon in PROBE_POINTS:
            val = t2m.sel(time=ts, latitude=lat, longitude=lon, method="nearest").values
            if not np.isfinite(val):
                return False
        return True

    lo = int(times.searchsorted(np.datetime64(now - dt.timedelta(days=lookback_days), "h")))
    hi = int(times.searchsorted(np.datetime64(now, "h")))
    if hi >= len(times):
        hi = len(times) - 1
    if not is_valid(times[lo]):
        raise RuntimeError("ERA5 ARCO probe: oldest probed hour is NaN")
    # bisect for last valid
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if is_valid(times[mid]):
            lo = mid
        else:
            hi = mid - 1
    cutoff = times[lo].astype("datetime64[h]").astype(dt.datetime)
    logger.info("ERA5 ARCO truth cutoff probed: %s", cutoff.isoformat())
    return cutoff


def truth_cutoff(now: dt.datetime | None = None, force: bool = False) -> dt.datetime:
    """Last fully-available ERA5 ARCO hour (cached, TTL ``CACHE_TTL_HOURS``).

    Cache file is shared with ewb_live.scoring.truth; either timestamp key
    ("probed_at" or "fetched") is accepted so the two writers don't invalidate
    each other.
    """
    if not force and os.path.exists(CUTOFF_CACHE_PATH):
        try:
            with open(CUTOFF_CACHE_PATH) as f:
                d = json.load(f)
            when = d.get("probed_at") or d.get("fetched")
            probed = dt.datetime.fromisoformat(when)
            if now is None:
                now = dt.datetime.utcnow()
            if now - probed < dt.timedelta(hours=CACHE_TTL_HOURS):
                return dt.datetime.fromisoformat(d["cutoff"])
        except Exception:  # noqa: BLE001 - fall through to re-probe
            pass
    cutoff = probe_arco_cutoff(now=now)
    os.makedirs(os.path.dirname(CUTOFF_CACHE_PATH), exist_ok=True)
    tmp = CUTOFF_CACHE_PATH + ".tmp"
    now_iso = dt.datetime.utcnow().isoformat()
    with open(tmp, "w") as f:
        json.dump({"cutoff": cutoff.isoformat(), "probed_at": now_iso, "fetched": now_iso}, f)
    os.replace(tmp, CUTOFF_CACHE_PATH)
    return cutoff


def truth_source_for(event_end: dt.datetime, cutoff: dt.datetime | None = None) -> str:
    """'era5_arco' if the event is fully covered by ARCO, else 'ifs_hres_analysis'.

    Canonical truth-source names (same strings as ewb_live.scoring.truth).
    """
    cutoff = cutoff or truth_cutoff()
    return "era5_arco" if event_end <= cutoff else "ifs_hres_analysis"
