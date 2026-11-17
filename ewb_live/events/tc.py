"""Tropical-cyclone events from IBTrACS best tracks (tc lane, 2026-11-16).

Owner brief: "storms -> Event objects, define the event qualification
(document it)". The rule, in full:

**Qualification.** One IBTrACS storm (unique ``SID``) is one event. It becomes
a ``tropical_cyclone`` event when its fixes contain at least
``MIN_TS_FIXES`` (3) fixes with a sustained wind of at least
``MIN_TS_WIND_KT`` (34 kt — tropical-storm force, ~17.2 m/s) that span at
least ``MIN_TS_HOURS`` (12 h). Wind is the chosen-agency value from
``ewb_live/sources/ibtracs.py`` (USA_ first, then WMO_, then the regional RSMC
— documented there); fixes whose WMO ``NATURE`` is ``ET`` (extratropical) or
``MX`` (mixed open/closed low) do **not** count towards qualification, so a
remnant that re-deepens after transition is not re-counted as a tropical
cyclone. ``NATURE`` never *grants* qualification: v04r01 leaves it ``NR`` on
whole basins, so the 34 kt wind threshold carries the rule and NATURE only
excludes transitioned fixes. A tropical depression that never reaches 34 kt is
not an event.

**Window.** ``start`` = first qualifying fix, ``end`` = last qualifying fix +
one fix step (the storm's own median fix interval, clamped to 3-6 h — IBTrACS
is 3-hourly for most agencies and 6-hourly for some), i.e. ``end`` is
exclusive exactly like the heat/freeze/AR lanes. The window deliberately spans
first-to-last tropical-storm fix, so a 6-hour dip below 34 kt in the middle of
a storm's life stays inside the event.

**bbox.** the extent of the *qualifying* fixes (the tropical-storm stage, not
the full life, which can be 60 degrees of longitude for a recurving typhoon)
padded by ``BBOX_PAD_DEG`` (5° all round: about the radius of a 34-kt wind
field plus analysis error, and the same default as ``Event.padded``), latitude
clamped to +-90. Antimeridian crossing follows the repo wrap convention: the
arc is computed on longitudes unwrapped along the track, so a crossing bbox
comes out with ``lon_min > lon_max`` (SPEC "Antimeridian bbox convention") and
``details["antimeridian_wrap"]`` is set, exactly like ``temperature.py``.

**Values.** ``peak_value`` = max sustained wind (kt) inside the event window
(the headline intensity, so the dashboard's Peak column reads like the paper's
TC figures); ``details["min_pressure_hpa"]`` = lowest pressure in the window.
``details["track"]`` is the **full** storm life in the shared 5-slot row
schema (see notes/channel_new_events.md) so the dashboard can draw the track
and ``midlat_cyclone`` can reuse one renderer.

TC events are **not scored** (EWB has no default metric set for best-track
events here; ``pipeline`` records a documented ``unsupported`` pair_status),
they never merge and they never take part in the cross-type overlap rule — a
hurricane sits inside a heat wave and inside an AR routinely, and the
"no two events overlap in time+space" rule would delete the most interesting
events in the catalog. See notes/channel_new_events.md (needs an owner
decision if the rule should ever cover track events).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import numpy as np
import pandas as pd

from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

MIN_TS_WIND_KT = float(34.0)   # tropical-storm force (1 min sustained, kt)
MIN_TS_FIXES = 3               # fixes at/above the wind floor
MIN_TS_HOURS = 12.0            # qualifying fixes must span at least this
BBOX_PAD_DEG = 5.0             # bbox margin around the qualifying track extent
NON_TROPICAL_NATURE = frozenset({"ET", "MX"})   # transitioned / mixed lows
MAX_LIFE_DAYS = 40.0           # sanity: no tropical cyclone lives this long
SOURCE_NAME = "IBTrACS v04r01 (NOAA NCEI, best track)"
CATEGORY_BANDS = ((137.0, "CAT 5"), (113.0, "CAT 4"), (96.0, "CAT 3"),
                  (83.0, "CAT 2"), (64.0, "CAT 1"), (34.0, "TS"), (0.0, "TD"))


def category(kt: float | None) -> str:
    """Saffir-Simpson-style label for a sustained wind in kt."""
    if kt is None or not np.isfinite(kt):
        return "TD"
    for lo, name in CATEGORY_BANDS:
        if kt >= lo:
            return name
    return "TD"


def _norm(v: float | None) -> float | None:
    """JSON-safe float (NaN/inf -> None; events.json forbids NaN)."""
    if v is None:
        return None
    v = float(v)
    return round(v, 2) if np.isfinite(v) else None


def unwrap_lons(lons: np.ndarray) -> np.ndarray:
    """Track longitudes made continuous (each fix within 180 deg of the last).

    A storm crossing the antimeridian has consecutive fixes like 179.6, -179.8;
    unwrapping gives 179.6, 180.2 so the bbox arc is the 0.6 deg it really is
    instead of a 359 deg band around the globe.
    """
    out = np.asarray(lons, dtype=float).copy()
    for i in range(1, out.size):
        d = out[i] - out[i - 1]
        while d > 180.0:
            out[i] -= 360.0
            d = out[i] - out[i - 1]
        while d < -180.0:
            out[i] += 360.0
            d = out[i] - out[i - 1]
    return out


def _track_rows(f: pd.DataFrame) -> list[list]:
    """Shared track representation: [iso_time_utc_z, lat, lon, intensity, pres].

    Schema owner: tc, mirrored by midlat_cyclone — see
    notes/channel_new_events.md ("TRACK SCHEMA").
    """
    out = []
    for t, lat, lon, w, p in zip(f["time"], f["lat"], f["lon"],
                                 f["wind_kt"], f["pressure_hpa"]):
        ts = pd.Timestamp(t)
        out.append([ts.strftime("%Y-%m-%dT%H:%M:%SZ"), _norm(lat), _norm(lon),
                    _norm(w), _norm(p)])
    return out


def _crosses_antimeridian(lons: np.ndarray) -> bool:
    d = np.diff(np.asarray(lons, dtype=float))
    return bool(d.size and np.nanmax(np.abs(d)) > 180.0)


def storm_to_event(sid: str, f: pd.DataFrame,
                   window: tuple[dt.datetime, dt.datetime] | None = None) -> Event | None:
    """One IBTrACS storm's fixes -> one Event (or None if it does not qualify).

    ``f`` must be one SID's fixes sorted by time. ``window`` optionally clips
    the storm to a scan window (the fix set the event is built from); fixes
    outside it are not drawn, so a storm that straddles the window edge keeps
    the part we can see.
    """
    f = f.sort_values("time")
    if window is not None:
        t0, t1 = pd.Timestamp(window[0]), pd.Timestamp(window[1])
        f = f[(f["time"] >= t0) & (f["time"] <= t1)]
    if len(f) < MIN_TS_FIXES:
        return None

    wind = f["wind_kt"].astype(float)
    trop = ~f["nature"].astype(str).isin(NON_TROPICAL_NATURE)
    qual = (wind >= MIN_TS_WIND_KT) & trop & f["lat"].notna() & f["lon"].notna()
    if int(qual.sum()) < MIN_TS_FIXES:
        return None
    qf = f[qual]
    span_h = (qf["time"].max() - qf["time"].min()).to_pytimedelta().total_seconds() / 3600.0
    if float(span_h) < MIN_TS_HOURS:
        return None

    # fix cadence of THIS storm (3-hourly for most agencies, 6-hourly for some)
    steps = f["time"].diff().dropna().dt.total_seconds() / 3600.0
    step = float(steps.median()) if len(steps) else 6.0
    step = float(np.clip(step if np.isfinite(step) else 6.0, 3.0, 6.0))

    start = qf["time"].min()
    # (python-level timedelta: Timestamp + pd.Timedelta trips numpy 2.5's
    #  generic-unit deprecation and will raise in a future numpy)
    end = pd.Timestamp(qf["time"].max().to_pydatetime() + dt.timedelta(hours=step))
    life_days = (end - start).to_pytimedelta().total_seconds() / 86400.0
    if life_days > MAX_LIFE_DAYS:
        logger.warning("ibtracs %s: %.0f-day storm, implausible — skipped",
                       sid, life_days)
        return None

    # bbox: qualifying-fix extent, unwrapped along the track, padded
    lon_u = unwrap_lons(qf["lon"].to_numpy(dtype=float))
    lat_min = float(qf["lat"].min()) - BBOX_PAD_DEG
    lat_max = float(qf["lat"].max()) + BBOX_PAD_DEG
    lat_min, lat_max = max(-90.0, lat_min), min(90.0, lat_max)
    lon_lo = float(lon_u.min()) - BBOX_PAD_DEG
    lon_hi = float(lon_u.max()) + BBOX_PAD_DEG
    if lon_hi - lon_lo >= 360.0:            # encircles the globe: full band
        lon_min, lon_max = -180.0, 180.0
    else:
        lon_min = normalize_lon_180(lon_lo)
        lon_max = normalize_lon_180(lon_hi)
    bbox = (round(lat_min, 4), round(lat_max, 4),
            round(lon_min, 4), round(lon_max, 4))

    in_win = (f["time"] >= start) & (f["time"] < end)
    wv = wind[in_win]
    wv = wv[wv.notna()]
    peak_kt = float(wv.max()) if len(wv) else float("nan")
    pv = f.loc[in_win, "pressure_hpa"].astype(float)
    pv = pv[pv.notna()]
    min_pres = float(pv.min()) if len(pv) else None

    name = str(f["name"].iloc[-1]).strip()
    if name in ("", "-", "UNNAMED", "NAN", "None", "NaN"):
        name = "Unnamed"
    else:
        name = name.title()
    # modal basin over the storm's fixes: IBTrACS labels a fix by the basin it
    # is in, so a recurving storm has several; the last fix would call Hurricane
    # Hanna "unassigned" after its European remnant drifted out of the code.
    modes = f["basin"].mode()
    basin = str(modes.iat[0]).strip() if len(modes) else ""
    from ..sources.ibtracs import basin_name

    track = _track_rows(f)
    wsrc = f["wind_source"].astype(str)
    psrc = f["pressure_source"].astype(str)
    det: dict[str, Any] = {
        "sid": sid,
        "name": name,
        "basin": basin or "?",
        "basin_name": basin_name(basin),
        "season": int(f["season"].iloc[-1]) if pd.notna(f["season"].iloc[-1]) else None,
        "peak_wind_kt": _norm(peak_kt),
        "peak_category": category(peak_kt),
        "min_pressure_hpa": _norm(min_pres),
        "intensity_units": "kt",
        "peak_value_units": "kt",   # the dashboard's Peak column unit (app.js)
        "ts_fixes": int(qual.sum()),
        "ts_hours": round(float(span_h) + step, 1),
        "n_fixes": int(len(f)),
        "fix_step_h": step,
        "track": track,
        "track_crosses_antimeridian": _crosses_antimeridian(f["lon"].to_numpy(float)),
        "antimeridian_wrap": bbox[2] > bbox[3],
        "wind_source": wsrc.mode().iat[0] if len(wsrc.mode()) else "",
        "pressure_source": psrc.mode().iat[0] if len(psrc.mode()) else "",
        "ongoing": bool(qf["time"].max() >= f["time"].max()),
        "source": SOURCE_NAME,
        "detection": (f"IBTrACS best track with >= {MIN_TS_FIXES} fixes at "
                      f">= {MIN_TS_WIND_KT:.0f} kt spanning >= "
                      f"{MIN_TS_HOURS:.0f} h (extratropical/mixed fixes excluded)"),
    }
    start_dt = start.to_pydatetime()
    end_dt = end.to_pydatetime()
    # last ACTIVE day (end is exclusive; sub-daily for a track event)
    last_day = end_dt - dt.timedelta(seconds=1)
    title = f"Tropical Cyclone {start_dt:%Y-%m-%d} to {last_day:%Y-%m-%d}"
    return Event(
        id=make_event_id("tropical_cyclone", start_dt, bbox),
        event_type="tropical_cyclone",
        title=title,
        start=start_dt,
        end=end_dt,
        bbox=bbox,
        peak_value=round(peak_kt, 1) if np.isfinite(peak_kt) else None,
        details=det,
    )


def scan_tc_events(start: dt.date, end: dt.date, *, force: bool = False,
                   tracks: pd.DataFrame | None = None) -> list[Event]:
    """tropical_cyclone events with any qualifying fix in [start, end].

    ``tracks`` overrides the IBTrACS source (tests / an already-loaded table);
    otherwise ``ewb_live.sources.ibtracs.load_tracks`` fetches/caches it. A
    window fully before the first recorded season yields [].
    """
    if tracks is None:
        from ..sources.ibtracs import load_tracks

        t0 = dt.datetime.combine(start, dt.time())
        t1 = dt.datetime.combine(end, dt.time()) + dt.timedelta(days=1)
        tracks = load_tracks(t0, t1, force=force)
    if tracks is None or not len(tracks):
        logger.warning("tropical_cyclone: no IBTrACS fixes for %s..%s "
                       "(source unavailable or empty)", start, end)
        return []
    tracks = tracks.dropna(subset=["time", "lat", "lon"])

    win = (dt.datetime.combine(start, dt.time()),
           dt.datetime.combine(end, dt.time()) + dt.timedelta(days=1))
    out: list[Event] = []
    skipped = 0
    for sid, grp in tracks.groupby("sid", sort=True):
        try:
            ev = storm_to_event(str(sid), grp, window=win)
        except Exception as exc:  # noqa: BLE001 — one torn storm must not kill the lane
            logger.warning("ibtracs storm %s failed: %s", sid, exc)
            continue
        if ev is None:
            skipped += 1
            continue
        out.append(ev)
    out.sort(key=lambda e: e.start)
    logger.info("tropical_cyclone %s..%s: %d storms -> %d events (%d below the "
                "%d kt / %d fix floor)", start, end, tracks["sid"].nunique(),
                len(out), skipped, MIN_TS_WIND_KT, MIN_TS_FIXES)
    return out
