"""Mid-latitude-cyclone events from ERA5 mean sea level pressure (mlc lane).

The lane in one sentence: find closed MSLP lows on the 6-hourly ERA5 grid,
stitch them into tracks, keep the tracks that are *weather*, and publish each
one as a ``midlat_cyclone`` Event whose ``details["track"]`` is the shared
5-slot track schema, so the dashboard draws it with the same renderer as an
IBTrACS tropical cyclone.

Detection/stitching is :mod:`ewb_live.events.mlc_track` (a pure-Python port of
ExtremeWeatherBench's own TC tracker, adapted to extratropical lows and to the
TempestExtremes recipe mlc-research validated); this module is the part that
turns a *system* into an *event*:

**Qualification** (owner decision 2026-11-17, ``notes/mlc_algorithm.md`` §4 —
all gates are on the track, all explainable in the About dialog):

1. **genesis latitude band**: the first fix must be in |lat| 25-70 deg — the
   extratropical belt, poleward of the trade-wind regime the tropical trackers
   own (and upstream EWB's ``latitude_max_degrees=50`` is the *tropical* cap,
   not usable here).
2. **orography at genesis**: genesis must sit over ERA5 surface elevation
   < 500 m. MSLP-only trackers invent lows over the Tibetan Plateau, the Andes
   and Greenland, where reducing surface pressure to sea level adds a fictive
   air column (the Murray-Simmonds failure mode mlc-research measured: 16.4 %
   of nodes above 500 m, 32 % in the 20-40 deg orogeny belt vs 8.6 % in 40-65 deg).
3. **lifetime >= 48 h** (the tracker's own floor) **and displacement >= 1000 km**
   first-fix to last-fix — the displacement floor also kills stationary
   cold-core cut-off lows, the other known MSLP artefact.
4. **depth**: minimum central MSLP <= 990 hPa somewhere in the track.
5. **no tropical origin**: reject a track whose genesis is within 500 km and
   +/-24 h of a tropical IBTrACS fix, looked back 7 days from genesis. One
   system, one event: when this fires, the ``tc`` lane owns the system, which
   is exactly the post-tropical Philippe -> Ciaran case.

**Tags, never gates** (``select.py`` ranks with them, the detector does not
hide events with them): ``bomb`` = the max over any 24 h of dMSL is at most
-24 hPa * sin|lat| / sin 60 deg (Sanders & Gyakum 1980), plus
``bomb_ratio``, ``max_24h_fall_hPa``, ``min_mslp_hPa``, ``displacement_km``,
``lifetime_h``.

**Window and values.** ``start``/``end`` are the *deep-core* sub-window of the
track: the contiguous fixes at most ``CORE_WINDOW_MARGIN_HPA`` (12 hPa) above
the track's minimum central pressure, containing the deepest fix, grown to at
least ``CORE_WINDOW_MIN_H`` (24 h) so a 6-hour minimum is not published as a
6-hour event. ``start`` is that fix's timestamp and ``end`` the following
midnight (exclusive, so the last active day is the last core fix's UTC day —
the convention the shared validator and event table read); the track in
``details`` is the **full life**, which may reach outside the window (schema
rule 4). ``peak_value`` = minimum central MSLP (hPa) — pressure is the headline
intensity here, so track slot 3 and slot 4 are both the central MSL and
``details["intensity_units"]`` says so.

**bbox**: the padded extent of the *full* track (a low is not confined to its
deepest day), latitude clamped to +-90, longitude on the track-unwrapped arc so
an antimeridian-crossing event comes out as ``lon_min > lon_max`` with
``details["antimeridian_wrap"]`` set (SPEC wrap convention).

Not scored: EWB has no metric set for a pressure-minimum track, so this lane is
published and rendered, never benchmarked (``select.TRACK_TYPES``, and the
pipeline documents an ``unsupported`` pair_status per pair exactly like the tc
lane). Fail-soft by design: if ERA5 ARCO cannot be read the scan logs and
returns ``[]`` rather than taking the weekly run down with it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from .mlc_track import Params as TrackParams
from .mlc_track import (Track, generate_mlc_tracks, haversine_km,
                          haversine_deg, unwrap_lons)
from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

RULE_REV = "mlc-v1"          # bump when a screen or the mapping changes (cache key)
TITLE_LABEL = "Midlatitude Cyclone"
SOURCE_NAME = "ERA5 MSLP (ARCO), closed-low tracker"
BBOX_PAD_DEG = 5.0           # same margin as Event.padded / the tc lane
CORE_WINDOW_MARGIN_HPA = 12.0
CORE_WINDOW_MIN_H = 24.0
LOOKBACK_DAYS = 6.0          # stitching context before the requested window


@dataclasses.dataclass(frozen=True)
class QualifyParams:
    """The event-qualification screens (see the module docstring)."""

    genesis_lat_min: float = 25.0
    genesis_lat_max: float = 70.0
    genesis_max_elev_m: float = 500.0
    min_lifetime_h: float = 48.0
    min_displacement_km: float = 1000.0
    max_min_mslp_hPa: float = 990.0
    tropical_radius_km: float = 500.0
    tropical_window_h: float = 24.0
    tropical_lookback_d: float = 7.0


def reject_reason(track: Track, elev_m: float, tropical: bool,
                  q: QualifyParams = QualifyParams()) -> str | None:
    """Why this track is not an event, or None when it qualifies."""
    g_time, g_lat, g_lon, _g_msl = track.genesis
    if not (q.genesis_lat_min <= abs(g_lat) <= q.genesis_lat_max):
        return f"genesis |lat| {abs(g_lat):.1f} outside {q.genesis_lat_min}-{q.genesis_lat_max}"
    if elev_m >= q.genesis_max_elev_m:
        return f"genesis over {elev_m:.0f} m orography (>= {q.genesis_max_elev_m:.0f} m)"
    if track.duration_h < q.min_lifetime_h - 1e-9:
        return f"lifetime {track.duration_h:.0f} h < {q.min_lifetime_h:.0f} h"
    if track.displacement_km < q.min_displacement_km:
        return (f"displacement {track.displacement_km:.0f} km < "
                f"{q.min_displacement_km:.0f} km")
    if track.min_mslp > q.max_min_mslp_hPa:
        return f"min MSLP {track.min_mslp:.1f} hPa > {q.max_min_mslp_hPa:.0f} hPa"
    if tropical:
        return f"tropical origin within {q.tropical_radius_km:.0f} km of genesis"
    return None


def core_window(track: Track, margin_hPa: float = CORE_WINDOW_MARGIN_HPA,
                min_hours: float = CORE_WINDOW_MIN_H,
                ) -> tuple[int, int]:
    """``[first, last]`` fix indices of the deep-core window of the track."""
    k = track.argmin
    lo = hi = k
    while lo > 0 and track.mslp[lo - 1] <= track.mslp[k] + margin_hPa:
        lo -= 1
    while hi < len(track) - 1 and track.mslp[hi + 1] <= track.mslp[k] + margin_hPa:
        hi += 1
    step_h = 6.0
    while (hi - lo) * step_h < min_hours:
        if lo > 0 and hi < len(track) - 1:
            # grow toward the deeper side, so the window follows the storm's
            # core instead of opening over its filling half
            if track.mslp[lo - 1] <= track.mslp[hi + 1]:
                lo -= 1
            else:
                hi += 1
        elif lo > 0:
            lo -= 1
        elif hi < len(track) - 1:
            hi += 1
        else:
            break
    return lo, hi


def event_bbox(track: Track, lo: int, hi: int, pad_deg: float = BBOX_PAD_DEG,
               ) -> tuple[float, float, float, float]:
    """Padded bbox of the qualifying window, wrap-aware (``lon_min > lon_max``
    when the arc crosses the antimeridian)."""
    lon_u = unwrap_lons(track.lon[lo:hi + 1])
    lat_min = max(-90.0, float(np.min(track.lat[lo:hi + 1])) - pad_deg)
    lat_max = min(90.0, float(np.max(track.lat[lo:hi + 1])) + pad_deg)
    lon_lo = float(lon_u.min()) - pad_deg
    lon_hi = float(lon_u.max()) + pad_deg
    if lon_hi - lon_lo >= 360.0:
        return (round(lat_min, 4), round(lat_max, 4), -180.0, 180.0)
    return (round(lat_min, 4), round(lat_max, 4),
            round(normalize_lon_180(lon_lo), 4), round(normalize_lon_180(lon_hi), 4))


def _round(v: float | None, nd: int = 1) -> float | None:
    if v is None:
        return None
    v = float(v)
    return round(v, nd) if np.isfinite(v) else None


def track_rows(track: Track) -> list[list]:
    """``details["track"]``: ``[iso_utc_Z, lat, lon, intensity, pressure_hpa]``.

    The shared 5-slot schema (tc lane, notes/channel_new_events.md). For this
    lane slot 3 = slot 4 = central MSL in hPa: pressure *is* the headline
    intensity for a mid-latitude low, and lower is stronger (say so in
    ``details["intensity_units"]``). Raw 6-hourly cadence, no resampling, and
    never NaN — JSON has no NaN.
    """
    rows = []
    for k, t in enumerate(track.times):
        msl = _round(float(track.mslp[k]), 1)
        rows.append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), _round(float(track.lat[k]), 3),
                     _round(float(track.lon[k]), 3), msl, msl])
    return rows


def track_to_event(track: Track, *, elev_m: float = 0.0, tropical: bool = False,
                   q: QualifyParams = QualifyParams(),
                   ) -> tuple[Event | None, str | None]:
    """One stitched track -> (Event, None) or (None, rejection reason)."""
    reason = reject_reason(track, elev_m, tropical, q)
    if reason is not None:
        return None, reason
    lo, hi = core_window(track)
    start = track.times[lo]
    # end is exclusive and MIDNIGHT-ALIGNED: the last core fix's own UTC day is
    # the last active day. A 6-hourly end (Nov 4 06:00) would make "the last day
    # of the event" ambiguous for the shared publish validator and the event
    # table on main, which both read the last day as end - 1 day; the exact
    # 6-hourly cadence lives in details["track"] anyway.
    end = dt.datetime.combine(track.times[hi].date() + dt.timedelta(days=1), dt.time())
    bbox = event_bbox(track, lo, hi)
    bomb, fall, ratio = track.bomb_ratio()
    det: dict[str, Any] = {
        "min_mslp_hPa": _round(track.min_mslp, 1),
        "intensity_units": "hPa (MSL, lower = stronger)",
        "peak_value_units": "hPa",
        "lifetime_h": _round(track.duration_h, 0),
        "displacement_km": _round(track.displacement_km, 0),
        "path_km": _round(track.path_km, 0),
        "bomb": bool(bomb),
        "bomb_ratio": _round(ratio, 2),
        "max_24h_fall_hPa": _round(fall, 1),
        "genesis_lat": _round(track.genesis[1], 3),
        "genesis_lon": _round(track.genesis[2], 3),
        "genesis_elev_m": _round(elev_m, 0),
        "core_window_h": _round((hi - lo) * 6.0, 0),
        "n_fixes": len(track),
        "n_gap_filled": int(np.sum(track.filled)) if track.filled is not None else 0,
        "track": track_rows(track),
        "track_crosses_antimeridian": bool(track.crosses_antimeridian),
        "antimeridian_wrap": bbox[2] > bbox[3],
        "source": SOURCE_NAME,
        "detection": ("closed 3 hPa MSLP low within 6 deg on 6-hourly ERA5, "
                      ">=48 h, >=1000 km, min MSLP <=990 hPa, genesis 25-70 deg "
                      "and under 500 m, no tropical origin; bomb = 24 h fall "
                      "<= -24 hPa at 60 deg"),
    }
    last_day = end - dt.timedelta(seconds=1)
    title = f"{TITLE_LABEL} {start:%Y-%m-%d} to {last_day:%Y-%m-%d}"
    ev = Event(
        id=make_event_id("midlat_cyclone", start, bbox),
        event_type="midlat_cyclone",
        title=title,
        start=start,
        end=end,
        bbox=bbox,
        peak_value=_round(track.min_mslp, 1),
        details=det,
    )
    return ev, None


def dedupe_tracks(tracks: Sequence[Track], dist_deg: float = 6.0
                  ) -> tuple[list[Track], list[tuple[Track, Track]]]:
    """Drop tracks that are the same low twice (split-and-re-identify).

    Two tracks conflict when a fix of one lies within ``dist_deg`` of a fix of
    the other at the same or an adjacent 6-hourly step; the deeper low is kept
    and the shallower one is dropped (not merged: a merged track would have a
    discontinuous pressure series and the dashboard would draw one system with
    a jump). Done here rather than in ``select.py`` because the product
    no-overlap rule is bbox-based — two lows 8 deg apart have overlapping padded
    boxes, while the *same* low re-identified 100 km apart is one system.

    The neighbour lookup is a 6-degree cell index (3x3 cells x 3 time steps per
    fix), so this is linear in fixes, not quadratic.
    """
    step_s = 6 * 3600
    order = sorted(range(len(tracks)), key=lambda i: tracks[i].min_mslp)

    def cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(np.floor(lat / dist_deg)),
                int(np.floor(((lon + 180.0) % 360.0) / dist_deg)))

    grid: dict[tuple[int, int, int], int] = {}     # (step, latcell, loncell) -> track
    for i in order:                                # deepest first owns its cells
        ta = tracks[i]
        for t, la, lo in zip(ta.times, ta.lat, ta.lon):
            ci, cj = cell(la, lo)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for sgn in (-1, 0, 1):
                        key = (int(t.timestamp()) // step_s + sgn, ci + di, cj + dj)
                        grid.setdefault(key, i)
    owner: dict[int, int] = {}
    dropped: set[int] = set()
    pairs: list[tuple[Track, Track]] = []
    for i in order:
        if i in dropped:
            continue
        ta = tracks[i]
        for j in range(len(tracks)):
            if (j == i or j in dropped
                    or (tracks[j].min_mslp, j) < (tracks[i].min_mslp, i)):
                continue        # deeper (or earlier-index) tracks are processed as i
            if owner.get(j, i) != i:
                continue
            tb = tracks[j]
            hit = None
            for t, la, lo in zip(tb.times, tb.lat, tb.lon):
                ci, cj = cell(la, lo)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        for sgn in (-1, 0, 1):
                            k = grid.get((int(t.timestamp()) // step_s + sgn,
                                          ci + di, cj + dj))
                            if k == i:
                                hit = (t, la, lo)
                                break
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit is not None:
                t, la, lo = hit
                # confirm with the real great-circle distance at that step
                near = haversine_deg(la, lo, ta.lat, ta.lon)
                same_step = [abs((t2 - t).total_seconds()) <= step_s
                             for t2 in ta.times]
                d = np.asarray(near, float)[np.asarray(same_step, bool)]
                if d.size and float(d.min()) <= dist_deg:
                    dropped.add(j)
                    owner[j] = i
                    pairs.append((ta, tb))
    kept = [tracks[i] for i in range(len(tracks)) if i not in dropped]
    return kept, pairs


# ------------------------------------------------------------ tropical screen
def tropical_origin_checker(q: QualifyParams = QualifyParams(), *, loader=None):
    """A ``genesis -> is-it-a-tropical-system`` test from the tc lane's IBTrACS.

    Looks back ``q.tropical_lookback_d`` days for a *tropical* fix (IBTrACS
    NATURE not ET/MX) within ``q.tropical_radius_km`` and +/-``q.tropical_window_h``
    hours of genesis. The source module is the tc lane's
    ``ewb_live/sources/ibtracs.py`` (imported lazily, so this lane runs before
    that lane is merged and the screen simply does nothing, once, with a log
    line); ``loader`` overrides it in tests.
    """
    if loader is None:
        try:
            from ..sources.ibtracs import load_tracks as loader  # noqa: F811
        except Exception as exc:  # noqa: BLE001 - tc lane absent is not an error
            logger.info("mlc: no IBTrACS source (%s); tropical-origin screen off", exc)
            return lambda lat, lon, t: False

    state: dict[str, Any] = {"loaded": False, "fixes": None}

    def check(lat: float, lon: float, t: dt.datetime) -> bool:
        import pandas as pd

        if not state["loaded"]:
            state["loaded"] = True
            try:
                state["fixes"] = loader(pd.Timestamp(t - dt.timedelta(days=int(q.tropical_lookback_d))
                                                     - dt.timedelta(days=1)),
                                        pd.Timestamp(t))
            except Exception as exc:  # noqa: BLE001
                logger.warning("mlc: IBTrACS read failed (%s); screen off", exc)
                state["fixes"] = None
        fx = state["fixes"]
        if fx is None or not len(fx):
            return False
        t0 = pd.Timestamp(t - dt.timedelta(days=q.tropical_lookback_d))
        t1 = pd.Timestamp(t + dt.timedelta(hours=q.tropical_window_h))
        sel = fx[(fx["time"] >= t0) & (fx["time"] <= t1)].dropna(subset=["lat", "lon"])
        if "nature" in sel:
            sel = sel[~sel["nature"].astype(str).isin(["ET", "MX"])]
        if not len(sel):
            return False
        d = haversine_km(np.full(len(sel), float(lat)), np.full(len(sel), float(lon)),
                         sel["lat"].to_numpy(float), sel["lon"].to_numpy(float))
        return bool(np.nanmin(d) <= q.tropical_radius_km)

    return check


# ------------------------------------------------------------------- scan
def scan_mlc_events(
    start: dt.date, end: dt.date, *,
    q: QualifyParams = QualifyParams(),
    tracker: TrackParams = TrackParams(),
    fields: tuple[Sequence[dt.datetime], np.ndarray, np.ndarray, np.ndarray] | None = None,
    orography: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    tropical_check: Callable[[float, float, dt.datetime], bool] | None = None,
    lookback_days: float = LOOKBACK_DAYS,
) -> list[Event]:
    """``midlat_cyclone`` events with genesis in [start, end].

    ``fields`` (times, mslp, lats, lons) and ``orography`` override the ERA5
    reads — the unit tests hand in a synthetic field; ``tropical_check``
    overrides the IBTrACS screen the same way. Everything else is the real
    thing: ERA5 MSLP streamed from ARCO over ``[start - lookback_days, end]``
    (the look-back is stitching context so a low born the day before the window
    keeps its true genesis), detected, stitched, screened, mapped.

    The window is clamped to what ERA5 actually holds (ARCO lags ~6 days): a
    scan that ends in the future just ends earlier, and a scan with nothing
    available returns [] instead of raising.
    """
    t1 = dt.datetime.combine(min(end, dt.date.today()), dt.time())
    t0 = dt.datetime.combine(start, dt.time()) - dt.timedelta(days=lookback_days)
    if fields is None:
        try:
            from ..sources import mlc_era5

            times, mslp, lats, lons = mlc_era5.read_mslp_window(t0, t1)
            if orography is None:
                orography = mlc_era5.load_orography()
        except Exception as exc:  # noqa: BLE001 - fail soft, like the exceedance lane
            logger.warning("midlat_cyclone: ERA5 read failed (%s) — no events", exc)
            return []
    else:
        times, mslp, lats, lons = fields
    if orography is None:
        orography = (np.zeros((0, 0), np.float32), np.zeros(0), np.zeros(0))
    elev, elev_lat, elev_lon = orography
    if not len(times) or mslp.size == 0:
        logger.warning("midlat_cyclone: no ERA5 MSLP for %s..%s — no events", start, end)
        return []

    res = generate_mlc_tracks(mslp, lats, lons, times, tracker)
    from ..sources.mlc_era5 import elevation_at

    check = tropical_check or tropical_origin_checker(q)
    tracks, dupes = dedupe_tracks(res.tracks)
    events: list[Event] = []
    counts: dict[str, int] = {}
    for tr in tracks:
        if tr.start < dt.datetime.combine(start, dt.time()):
            continue          # genesis before the window: the earlier scan owns it
        if tr.start > t1:
            continue
        g_t, g_lat, g_lon, _ = tr.genesis
        zm = elevation_at(elev, elev_lat, elev_lon, g_lat, g_lon)
        try:
            trop = bool(check(g_lat, g_lon, g_t))
        except Exception as exc:  # noqa: BLE001 - the screen must never kill the lane
            logger.warning("mlc: tropical screen failed at %s: %s", g_t, exc)
            trop = False
        ev, reason = track_to_event(tr, elev_m=zm, tropical=trop, q=q)
        if ev is None:
            key = reason.split("(")[0].split("<")[0].split(">")[0].strip()[:48]
            counts[key] = counts.get(key, 0) + 1
            continue
        events.append(ev)
    events.sort(key=lambda e: (e.start, e.id))
    logger.info(
        "midlat_cyclone %s..%s: %d frames, %d nodes, %d tracks -> %d events "
        "(%d same-low dupes; screened out %s)",
        start, end, res.n_frames, res.n_nodes, len(res.tracks), len(events),
        len(dupes), counts or "nothing")
    return events


# --------------------------------------------------------------- curation
# select.py calls these so the ranking/floor arithmetic lives with the lane.
TOP_N = 30           # a busy 90-day window holds ~20-40 qualifying lows
                     # (Nov 2023: 99 tracks, ~53 in the genesis band)


def severity(e: Event) -> float:
    """Impact-ish ranking for the quota: depth deficit x days alive x bomb.

    Depth below the 990 hPa floor is the mid-latitude analogue of a hurricane's
    wind surplus; a bomb gets a 1.5x nudge (it is the event people remember),
    and nothing here can push a shallow long-lived low above a deep short one
    by more than that factor.
    """
    det = e.details or {}
    depth = max(990.0 - float(det.get("min_mslp_hPa") or 990.0), 0.0)
    days = max(float(det.get("lifetime_h") or 48.0), 24.0) / 24.0
    return depth * days * (1.5 if det.get("bomb") else 1.0)


def passes_floor(e: Event, q: QualifyParams = QualifyParams()) -> bool:
    """Physical-sanity re-check of the detector's screens (see module docstring).

    The detector already applied them; this is the tripwire that catches a
    merged/mutated event (select.py can merge same-type fragments, which this
    lane forbids — see ``TRACK_TYPES``) rather than a fresh detection.
    """
    det = e.details or {}
    dur_h = (e.end - e.start).total_seconds() / 3600.0
    glat = det.get("genesis_lat")
    return (
        dur_h >= 24.0
        and float(det.get("min_mslp_hPa") or 9999.0) <= q.max_min_mslp_hPa
        and float(det.get("lifetime_h") or 0.0) >= q.min_lifetime_h - 1e-9
        and float(det.get("displacement_km") or 0.0) >= q.min_displacement_km
        and glat is not None and q.genesis_lat_min <= abs(float(glat)) <= q.genesis_lat_max
    )
