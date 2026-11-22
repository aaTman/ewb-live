"""Storm-following track verification core (notes/scoring_design.md sections 3.4, 3.5, 8.3).

Pure numpy / scipy, no I/O: the lane scorers (``scoring.tc``; MLC later) read
the MSLP window and hand it to :func:`follow_track`; everything else here is
geometry and bookkeeping that is identical for any track lane.

* Geometry (D10): great-circle distance and bearing, observed motion from the
  centred 6 h + 6 h difference of observed positions (one-sided at the first /
  last fix, 2 km/h stationary cutoff) and the along / cross decomposition.
  ``along > 0`` = forecast AHEAD of the observed storm, ``cross > 0`` = RIGHT of
  the observed track (Leonardo & Colle 2017 convention).
* Tracker (D1): start at the OBSERVED fix at init, then at each 6-h step search
  the MSLP stack for a closed low near the persistence-advanced previous
  position; two consecutive steps without one lose the track (all later leads
  are misses). Steps whose whole field is fill are "no data", never misses.
* Bookkeeping: which inits / leads are verifiable (:func:`verifiable_inits`,
  :func:`leads_at`), the per-sample result (:func:`verify_init`) and the
  SCORE_COLUMNS rows incl. ``TrackMiss`` (:func:`rows_for_pair`).

Built on the primitives of ``events/mlc_track.py`` (haversine in km / degrees,
antimeridian-safe longitude handling); the prototype of the tracker is the WP0
pilot ``tools/track_pilot.py`` (measured numbers: notes/scoring_pilot.md).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import ndimage

from ..events.mlc_track import R_EARTH_KM, haversine_deg, haversine_km

LEAD_STEP_H = 6
MAX_LEAD_H = 240
STATIONARY_KMH = 2.0                 # section 3.4: below this along/cross are undefined
SCORE_COLUMNS = ["event_id", "event_type", "model", "metric", "lead_time_hours", "value", "init_time"]

# metric strings (section 6)
M_POS, M_ALONG, M_CROSS = "TrackPositionError", "AlongTrackError", "CrossTrackError"
M_CP, M_MISS = "CentralPressureError", "TrackMiss"
TRACK_METRICS = (M_POS, M_ALONG, M_CROSS, M_CP, M_MISS)


# =============================================================================
# geometry
# =============================================================================
def wrap_deg(x):
    """Wrap to [-180, 180)."""
    return (np.asarray(x, float) + 180.0) % 360.0 - 180.0


def great_circle(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (R = 6371.0 km, as ``events/mlc_track.py``)."""
    return haversine_km(lat1, lon1, lat2, lon2)


def bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing 1 -> 2, degrees clockwise from north."""
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dl = np.deg2rad(wrap_deg(np.asarray(lon2, float) - np.asarray(lon1, float)))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return np.rad2deg(np.arctan2(y, x))


def _local_xy(lat0, lon0, lat, lon):
    """(east, north) km of (lat, lon) in the tangent plane at (lat0, lon0)."""
    d = great_circle(lat0, lon0, lat, lon)
    b = np.deg2rad(bearing(lat0, lon0, lat, lon))
    return d * np.sin(b), d * np.cos(b)


def _as_dt64(times) -> np.ndarray:
    return np.asarray([np.datetime64(t, "s") for t in times])


def observed_motion(times, lat, lon, k: int):
    """``(heading deg cw from north, speed km/h)`` at fix ``k`` of an observed track.

    Centred difference of the fixes at ``t-6 h`` and ``t+6 h`` projected in the
    tangent plane at fix ``k`` (so the antimeridian and high latitudes need no
    special case); one-sided at the first / last fix; the 3-h neighbour is used
    when the 6-h one is missing. ``(nan, nan)`` if fewer than two usable fixes
    lie within +/-6 h.
    """
    t = times if (isinstance(times, np.ndarray) and times.dtype.kind == "M") else _as_dt64(times)
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)

    def find(dh):
        hit = np.where(t == t[k] + np.timedelta64(int(dh * 3600), "s"))[0]
        return int(hit[0]) if hit.size else None

    back = find(-6)
    if back is None:
        back = find(-3)
    fwd = find(6)
    if fwd is None:
        fwd = find(3)
    if back is None and fwd is None:
        return float("nan"), float("nan")
    i0 = back if back is not None else k
    i1 = fwd if fwd is not None else k
    x0, y0 = _local_xy(lat[k], lon[k], lat[i0], lon[i0])
    x1, y1 = _local_xy(lat[k], lon[k], lat[i1], lon[i1])
    hrs = float((t[i1] - t[i0]) / np.timedelta64(1, "s")) / 3600.0
    vx, vy = (x1 - x0) / hrs, (y1 - y0) / hrs
    return float(np.rad2deg(np.arctan2(vx, vy))), float(np.hypot(vx, vy))


def along_cross(obs_lat, obs_lon, fc_lat, fc_lon, heading_deg, speed_kmh):
    """``(distance, along, cross)`` km of the forecast position relative to the observed.

    ``along > 0``: forecast ahead of the observed storm (too fast); ``cross > 0``:
    right of the observed track. ``along`` / ``cross`` are NaN when the observed
    motion is undefined or slower than the 2 km/h stationary cutoff (the distance
    is still returned). ``along**2 + cross**2 == distance**2``.
    """
    d = float(great_circle(obs_lat, obs_lon, fc_lat, fc_lon))
    if not (np.isfinite(heading_deg) and np.isfinite(speed_kmh)) or speed_kmh < STATIONARY_KMH:
        return d, float("nan"), float("nan")
    beta = float(bearing(obs_lat, obs_lon, fc_lat, fc_lon))
    delta = math.radians(float(wrap_deg(beta - heading_deg)))
    return d, d * math.cos(delta), d * math.sin(delta)


# =============================================================================
# the storm-following tracker
# =============================================================================
@dataclass(frozen=True)
class TrackerParams:
    """Tracker knobs. TC numbers = EWB's own (2 hPa closed contour, 5.5 deg);
    MLC numbers = the mlc-v3 recipe (3 hPa, 6 deg, faster movers)."""
    r_step_deg: float          # search radius around the persistence-advanced position
    delta_hPa: float           # the centre + delta isobar must close
    max_gc_deg: float          # ... within this great-circle distance of the centre
    smooth_sigma_cells: float = 1.0
    nbhd_deg: float = 1.0      # local-minimum neighbourhood radius
    lost_after: int = 2        # consecutive steps without a low -> lost


TC_PARAMS = TrackerParams(r_step_deg=3.0, delta_hPa=2.0, max_gc_deg=5.5)
MLC_PARAMS = TrackerParams(r_step_deg=5.0, delta_hPa=3.0, max_gc_deg=6.0)


def _closed_low(sm: np.ndarray, lat: np.ndarray, lon: np.ndarray, r: int, c: int,
                p: TrackerParams, dlat: float, dlon: float) -> bool:
    """Does the ``sm[r, c] + delta`` isobar close within ``max_gc_deg`` of the centre?

    The sub-level connected set around the centre must not reach the edge of a
    box of half-width ``max_gc + 1 cell`` (open contour) and every member must
    be within ``max_gc`` great-circle degrees (the EWB / PROOF closed-contour test).
    A box clipped by the window edge counts as "not closed".
    """
    ny, nx = sm.shape
    hr = int(math.ceil(p.max_gc_deg / dlat)) + 1
    coslat = max(0.2, math.cos(math.radians(float(lat[r]))))
    hc = int(math.ceil(p.max_gc_deg / (dlon * coslat))) + 1
    r0, r1, c0, c1 = max(0, r - hr), min(ny, r + hr + 1), max(0, c - hc), min(nx, c + hc + 1)
    box = sm[r0:r1, c0:c1]
    lab, _ = ndimage.label(box < sm[r, c] + p.delta_hPa, structure=np.ones((3, 3), int))
    comp = lab == lab[r - r0, c - c0]
    if comp[0, :].any() or comp[-1, :].any() or comp[:, 0].any() or comp[:, -1].any():
        return False
    rr, cc = np.nonzero(comp)
    d = haversine_deg(float(lat[r]), float(lon[c]), lat[r0 + rr], lon[c0 + cc])
    return bool(np.max(d) < p.max_gc_deg)


def follow_track(field: np.ndarray, lat: np.ndarray, lon: np.ndarray,
                 start_lat: float, start_lon: float, p: TrackerParams):
    """Follow one low through a ``(step, lat, lon)`` MSLP stack (hPa).

    ``step`` 0 is the verification init (lead 0 of the nominal init); lat
    ascending, lon ascending and contiguous (may exceed 180). Returns
    ``(lat, lon, mslp, ok, nodata)`` arrays, one entry per step; ``ok`` is False
    where there is no valid low or the track is lost (position / MSLP NaN there).
    ``nodata`` marks steps whose field is all fill / NaN (e.g. GraphCast lead 0):
    they are not the tracker's failure, do not count towards losing the track and
    are not verified.
    """
    nstep, ny, nx = field.shape
    dlat = float(np.mean(np.abs(np.diff(lat))))
    dlon = float(np.mean(np.abs(np.diff(lon))))
    nb = max(2, int(round(p.nbhd_deg / dlat)))
    out_lat = np.full(nstep, np.nan)
    out_lon = np.full(nstep, np.nan)
    out_p = np.full(nstep, np.nan)
    ok = np.zeros(nstep, bool)
    nodata = np.zeros(nstep, bool)
    prev = (start_lat, start_lon)               # last accepted (or carried) position
    vel = np.zeros(2)                           # (dlat, dlon) per step
    misses = 0
    lost = False
    cap = 0.75 * p.r_step_deg
    for s in range(nstep):
        if lost:
            continue
        f = np.asarray(field[s], float)
        if not np.isfinite(f).any():
            nodata[s] = True
            continue
        f = np.where(np.isfinite(f), f, np.nanmax(f))
        sm = ndimage.gaussian_filter(f, p.smooth_sigma_cells, mode="nearest")
        pred_lat = prev[0] + vel[0]
        pred_lon = prev[1] + vel[1]
        is_min = sm == ndimage.minimum_filter(sm, size=2 * nb + 1, mode="nearest")
        rr, cc = np.nonzero(is_min)
        d = np.empty(0)
        if rr.size:
            d = haversine_deg(pred_lat, pred_lon, lat[rr], lon[cc])
            near = d <= p.r_step_deg
            rr, cc, d = rr[near], cc[near], d[near]
        found = None
        for i in np.argsort(d):
            if _closed_low(sm, lat, lon, int(rr[i]), int(cc[i]), p, dlat, dlon):
                found = (int(rr[i]), int(cc[i]))
                break
        if found is None:
            misses += 1
            if misses >= p.lost_after:
                lost = True
            else:
                prev = (pred_lat, pred_lon)                        # carry on persistence
            continue
        misses = 0
        r, c = found
        r0, r1, c0, c1 = max(0, r - 1), min(ny, r + 2), max(0, c - 1), min(nx, c + 2)
        out_lat[s], out_lon[s] = float(lat[r]), float(lon[c])
        out_p[s] = float(np.min(f[r0:r1, c0:c1]))                  # unsmoothed centre MSLP
        ok[s] = True
        new = (float(lat[r]), float(lon[c]))
        if s >= 1 and ok[s - 1]:                # motion only from consecutive forecast fixes
            step = np.array([new[0] - prev[0], new[1] - prev[1]])
            n = float(np.hypot(*step))
            vel = step if n <= cap else step * (cap / n)
        prev = new
    return out_lat, wrap_deg(out_lon), out_p, ok, nodata


# =============================================================================
# the observed track and which (init, lead) samples exist
# =============================================================================
@dataclass
class ObservedTrack:
    """Observed best / analysed track: fixes at ``times`` (naive UTC, any cadence)."""
    times: list                        # naive UTC datetimes, ascending
    lat: np.ndarray
    lon: np.ndarray                    # wrapped to [-180, 180)
    pres: np.ndarray                   # hPa, NaN when missing
    wind: np.ndarray | None = None     # kt, NaN when missing (TC only)
    _t64: np.ndarray = field(init=False, repr=False)
    _idx: dict = field(init=False, repr=False)

    def __post_init__(self):
        self.lat = np.asarray(self.lat, float)
        self.lon = wrap_deg(np.asarray(self.lon, float))
        self.pres = np.asarray(self.pres, float)
        self._t64 = _as_dt64(self.times)
        self._idx = {t: i for i, t in enumerate(self.times)}

    def index_of(self, t: dt.datetime):
        return self._idx.get(t)

    def motion(self, k: int):
        return observed_motion(self._t64, self.lat, self.lon, k)


def leads_at(track: ObservedTrack, init: dt.datetime, max_lead_h: int = MAX_LEAD_H) -> list[int]:
    """6-hourly leads (0 included) whose valid time has an observed fix."""
    return [h for h in range(0, max_lead_h + 1, LEAD_STEP_H)
            if track.index_of(init + dt.timedelta(hours=h)) is not None]


def verifiable_inits(track: ObservedTrack, start: dt.datetime | None = None,
                     cadence_h: int = 12) -> tuple[list[dt.datetime], list[dt.datetime]]:
    """``(verified, not_verified)`` nominal inits (00/12Z) of one storm.

    An init is *verifiable* when the observed storm has a fix AT the init time
    and at least one verifying fix 6 h later. ``start`` is the first instant
    verification may begin (TC, owner decision 2026-11-21: the first observed
    fix with wind >= 34 kt; MLC: the first fix); verifiable inits earlier than
    ``start`` are returned in ``not_verified`` (excluded from scoring and shown
    as not verified, pre-genesis misses are never counted).
    """
    first, last = track.times[0], track.times[-1]
    cur = first.replace(hour=(first.hour // cadence_h) * cadence_h, minute=0, second=0, microsecond=0)
    if cur < first:
        cur += dt.timedelta(hours=cadence_h)
    verified, excluded = [], []
    while cur <= last - dt.timedelta(hours=LEAD_STEP_H):
        if track.index_of(cur) is not None and track.index_of(cur + dt.timedelta(hours=LEAD_STEP_H)) is not None:
            (excluded if (start is not None and cur < start) else verified).append(cur)
        cur += dt.timedelta(hours=cadence_h)
    return verified, excluded


def verify_init(track: ObservedTrack, init: dt.datetime, leads: list[int], offset_h: int,
                fc_lat, fc_lon, fc_mslp, ok, nodata) -> list[dict]:
    """Per-sample verification of one tracked init.

    ``init`` is the NOMINAL init (the storm exists at it, the tracker was seeded
    there); ``leads`` the nominal leads that were read (``leads[0] == 0``);
    ``offset_h`` > 0 when the nearest-earlier-init rule supplied the forecast: the
    sample then records the TRUE init (``init - offset_h``) and TRUE lead
    (``lead + offset_h``), so ``init_true + lead_true`` is still the valid time.
    Lead 0 only seeds the tracker and is not a sample. One dict per verified lead:
    ``init_true``, ``lead_true``, ``tracked`` and (if tracked) ``d_km``,
    ``along_km`` / ``cross_km`` (None when motion undefined or stationary) and
    ``dp_hpa`` (None when the observed pressure is missing). ``nodata`` leads are
    omitted.
    """
    init_true = init - dt.timedelta(hours=offset_h)
    out = []
    for j, h in enumerate(leads):
        if h == 0 or nodata[j]:
            continue
        k = track.index_of(init + dt.timedelta(hours=h))
        if k is None:
            continue
        rec = {"init_true": init_true.isoformat(), "lead_true": float(h + offset_h),
               "tracked": bool(ok[j])}
        if ok[j]:
            hd, sp = track.motion(k)
            d, a, c = along_cross(track.lat[k], track.lon[k], fc_lat[j], fc_lon[j], hd, sp)
            rec["d_km"] = d
            rec["along_km"] = a if np.isfinite(a) else None
            rec["cross_km"] = c if np.isfinite(c) else None
            op = track.pres[k]
            rec["dp_hpa"] = float(fc_mslp[j] - op) if np.isfinite(op) and np.isfinite(fc_mslp[j]) else None
        out.append(rec)
    return out


def rows_for_pair(event_id: str, event_type: str, model: str, samples: list[dict]) -> pd.DataFrame:
    """SCORE_COLUMNS rows for one (event, model) from its verified samples.

    One row per (init, lead, metric) (D5). A tracked sample emits
    ``TrackPositionError`` (+ along / cross / central pressure where defined) and
    ``TrackMiss = 0``; a lost / never-closed low emits ONLY ``TrackMiss = 1``
    (never imputed, never dropped). The same (init, lead) twice (a rescued
    older init that coincides with a nominal one) is emitted once.
    """
    seen: set = set()
    recs: list[tuple] = []
    for s in samples:
        key = (s["init_true"], s["lead_true"])
        if key in seen:
            continue
        seen.add(key)
        init = np.datetime64(s["init_true"], "ns")
        lead = float(s["lead_true"])
        if s["tracked"]:
            recs.append((M_POS, lead, s["d_km"], init))
            if s.get("along_km") is not None:
                recs.append((M_ALONG, lead, s["along_km"], init))
            if s.get("cross_km") is not None:
                recs.append((M_CROSS, lead, s["cross_km"], init))
            if s.get("dp_hpa") is not None:
                recs.append((M_CP, lead, s["dp_hpa"], init))
            recs.append((M_MISS, lead, 0.0, init))
        else:
            recs.append((M_MISS, lead, 1.0, init))
    n = len(recs)
    df = pd.DataFrame({
        "event_id": np.full(n, event_id, dtype=object),
        "event_type": np.full(n, event_type, dtype=object),
        "model": np.full(n, model, dtype=object),
        "metric": [r[0] for r in recs],
        "lead_time_hours": np.array([r[1] for r in recs], dtype="float64"),
        "value": np.array([r[2] for r in recs], dtype="float64"),
        "init_time": np.array([r[3] for r in recs], dtype="datetime64[ns]"),
    })
    return df[SCORE_COLUMNS]


# =============================================================================
# read-window helpers (pure)
# =============================================================================
def window_for(track: ObservedTrack, init: dt.datetime, pad_deg: float = 15.0,
               horizon_h: int = MAX_LEAD_H) -> tuple[float, float, float, float]:
    """``(lat_min, lat_max, lon_start, lon_width)`` covering the observed track from
    ``init`` for ``horizon_h`` hours, padded ``pad_deg`` (longitude widened by
    1/cos lat), in an unwrapped longitude frame (so a dateline-crossing track is
    one contiguous window)."""
    from ..events.mlc_track import unwrap_lons

    idx = [i for i, t in enumerate(track.times)
           if init <= t <= init + dt.timedelta(hours=horizon_h)]
    lat = track.lat[idx]
    lon = unwrap_lons(track.lon[idx])
    lat0, lat1 = max(-89.75, lat.min() - pad_deg), min(89.75, lat.max() + pad_deg)
    coslat = max(0.2, math.cos(math.radians(min(80.0, float(np.max(np.abs(lat))) + pad_deg))))
    lpad = min(90.0, pad_deg / coslat)
    return (float(lat0), float(lat1), float(lon.min() - lpad),
            float(min(360.0, lon.max() - lon.min() + 2 * lpad)))


def contiguous_lons(lon_native, lon_start: float, width: float):
    """``(indices, lon coords)`` of the native longitudes inside ``[lon_start,
    lon_start + width]``, ordered west -> east in one continuous frame."""
    off = (np.asarray(lon_native, float) - lon_start) % 360.0
    sel = np.where(off <= width)[0]
    sel = sel[np.argsort(off[sel])]
    return sel, lon_start + off[sel]


def match_samples(track: ObservedTrack, start: dt.datetime | None = None,
                  cadence_h: int = 12) -> dict:
    """The verification plan of one storm: ``{"verified": [(init, leads)], "not_verified":
    [init]}`` (see :func:`verifiable_inits`; ``leads`` = :func:`leads_at`)."""
    ok, excluded = verifiable_inits(track, start, cadence_h)
    return {"verified": [(i, leads_at(track, i)) for i in ok], "not_verified": excluded}
