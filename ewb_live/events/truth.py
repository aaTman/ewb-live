"""Truth-latency probe for ERA5 ARCO (single canonical implementation).

ERA5 ARCO (gs://gcp-public-data-arco-era5) lags wall clock by ~5-7 days.
This module probes the archive for its last fully-populated hour so the
pipeline knows which events can be scored against ERA5 and which need the
IFS-HRES-lead-0 analysis fallback (SPEC section 2).

The probe reads ``2m_temperature`` at 4 scattered grid points and walks BACKWARDS
from now on a 12 h stride (bounded probe count; see probe_arco_cutoff for why the
old bisection-on-a-fixed-anchor broke on interior holes), cached to
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
    """Last ERA5 ARCO hour that is fully populated, searched BACKWARDS from `now`.

    Returns the newest timestamp with finite t2m at all probe points (naive UTC).

    Why not a bisection anchored at ``now - lookback_days`` (the old code): the
    archive is 5-7 d behind wall clock *and*, observed 2026-11-18, it also has
    INTERIOR holes -- 2026-11-04T05Z and 2026-11-09T23Z are NaN at all four probe
    points while the hours on either side are filled, and the fill ended at
    2026-11-11T17Z. The old probe asserted that the single hour at ``now - 14 d``
    was valid and raised otherwise, so one interior hole aborted every lane build
    (`--events-only`, `--publish-only`) and the unit test that reaches the real
    archive. A strided backwards walk tolerates a hole at the end *and* inside,
    and its probe count is bounded (stride 12 h over `lookback_days`, then a
    <=12-probe hourly refine).

    A hole in the MIDDLE of the window is not this function's business -- it only
    reports the last fully-available hour; scans that cross a hole still see NaN
    truth cells there and handle them as missing.
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

    stride = 12
    hi = int(times.searchsorted(np.datetime64(now, "h")))
    if hi >= len(times):
        hi = len(times) - 1
    floor_i = max(0, hi - int(lookback_days * 24))

    anchor = None
    n_probe = 0
    i = hi
    while i >= floor_i:
        n_probe += 1
        if is_valid(times[i]):
            anchor = i
            break
        i -= stride
    if anchor is None:
        raise RuntimeError(
            f"ERA5 ARCO probe: no fully-populated hour in the last "
            f"{lookback_days} d ({n_probe} probes) -- archive unavailable")
    # newest valid hour is within `stride` above the anchor; walk down for it
    for j in range(min(anchor + stride, hi), anchor, -1):
        n_probe += 1
        if is_valid(times[j]):
            anchor = j
            break
    logger.info("ERA5 ARCO truth cutoff probed: %s (%d probes, oldest probed %s)",
                times[anchor], n_probe, times[max(floor_i, anchor - lookback_days * 24)])
    return times[anchor].astype("datetime64[h]").astype(dt.datetime)


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
    cutoff = _probe_or_stale(now)
    os.makedirs(os.path.dirname(CUTOFF_CACHE_PATH), exist_ok=True)
    tmp = CUTOFF_CACHE_PATH + ".tmp"
    now_iso = dt.datetime.utcnow().isoformat()
    with open(tmp, "w") as f:
        json.dump({"cutoff": cutoff.isoformat(), "probed_at": now_iso, "fetched": now_iso}, f)
    os.replace(tmp, CUTOFF_CACHE_PATH)
    return cutoff


def _probe_or_stale(now: dt.datetime | None) -> dt.datetime:
    """Probe ARCO; if the archive is unreachable, fall back to the cached cutoff.

    The probe is a *precondition* for every lane, so a transient read failure
    must not be able to abort a build and report it as "no events" -- that is a
    lie about the data, not about the atmosphere. On failure we keep the last
    known cutoff (however old) and say so in the log; the caller still gets a
    datetime and the build still clamps its window to real available truth.
    """
    try:
        return probe_arco_cutoff(now=now)
    except Exception as exc:  # noqa: BLE001 - any read/probe failure
        if os.path.exists(CUTOFF_CACHE_PATH):
            try:
                with open(CUTOFF_CACHE_PATH) as f:
                    d = json.load(f)
                cutoff = dt.datetime.fromisoformat(d["cutoff"])
                when = d.get("probed_at") or d.get("fetched")
                age_h = ((now or dt.datetime.utcnow())
                         - dt.datetime.fromisoformat(when)).total_seconds() / 3600.0
                logger.warning(
                    "ERA5 ARCO probe failed (%s: %s) -- continuing with the cached "
                    "cutoff %s, probed %.1f h ago; truth after that hour is not "
                    "available and the window is clamped to it",
                    type(exc).__name__, exc, cutoff.isoformat(), age_h)
                return cutoff
            except Exception:  # noqa: BLE001 - unreadable cache -> real failure
                pass
        raise


def truth_source_for(event_end: dt.datetime, cutoff: dt.datetime | None = None) -> str:
    """'era5_arco' if the event is fully covered by ARCO, else 'ifs_hres_analysis'.

    Canonical truth-source names (same strings as ewb_live.scoring.truth).
    """
    cutoff = cutoff or truth_cutoff()
    return "era5_arco" if event_end <= cutoff else "ifs_hres_analysis"
