"""WP0 scoring pilot: throwaway storm-following MSLP tracker (notes/scoring_design.md 8.2).

Measures, on ONE tropical cyclone (``Polo``) and the longest-lived published
mid-latitude low, over the 8 virtual-store models plus ``ifs_hres`` read
directly, what the later work packages only estimate:

* seconds per ``(model, init)``: store open, field read, tracker;
* peak RSS per worker and for the pool (sampled like ``pipeline._PeakRss``,
  four workers, extrapolated to twelve);
* tracker miss rate by lead, median / p90 position error by lead, along/cross
  sign sanity, and whether EWB's landfall classes accept the pilot tracks.

The storm-following tracker here is a **prototype** of the one WP1 builds in
``ewb_live/scoring/track_verif.py``: start at the observed fix at init, then at
each 6-h step search for a closed MSLP low near the persistence-advanced
previous position; two consecutive steps without one lose the track. The
geometry (``bearing``, ``observed_motion``, ``along_cross``) is the exact
section 3.4 convention (D10).

Pilot guard (replaces the clock-only part of R1 for this run): before every
launch and every polling cycle ``systemctl is-active ewb-live-weekly.service``
must not be active and the clock must be outside Monday 06:00-14:00 UTC; the
job aborts (workers killed, partial results kept) the moment that stops being
true. Reads are read-only on the live icechunk store; everything is written
under the scratch ``--out`` directory (``/opt/ewb-pilot/...``).

Run on the ewb LXC only (``ewb_live/_guard.py``)::

    python -m ewb_live.tools.track_pilot run --out /opt/ewb-pilot/out --workers 4
    python -m ewb_live.tools.track_pilot sanity            # offline, observed tracks only
    python -m ewb_live.tools.track_pilot landfall --out /opt/ewb-pilot/out
    python -m ewb_live.tools.track_pilot report --out /opt/ewb-pilot/out
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import logging
import math
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from ..events.mlc_track import R_EARTH_KM, haversine_deg, haversine_km, unwrap_lons

logger = logging.getLogger("track_pilot")

EVENTS_JSON = "/opt/ewb-live/data/results/events_selected.json"
MODELS_STORE = ["graphcast", "graphcast_gfs", "aurora", "aurora_gfs",
                "pangu_weather", "pangu_weather_gfs", "fourcastnet_v2", "fourcastnet_v2_gfs"]
MODELS_ALL = MODELS_STORE + ["ifs_hres"]
LEAD_STEP_H = 6
MAX_LEAD_H = 240
STATIONARY_KMH = 2.0                       # section 3.4: below this, along/cross undefined
WEEKLY_UNIT = "ewb-live-weekly.service"
GUARD_WINDOW = (0, 6, 14)                  # Monday (weekday 0), 06:00 <= hour < 14:00 UTC


# =============================================================================
# pilot guard (R1, pilot variant)
# =============================================================================
def guard_status(now: dt.datetime | None = None) -> tuple[bool, str]:
    """(ok, reason). ok only when the weekly service is not active and it is not
    Monday 06:00-14:00 UTC."""
    now = now or dt.datetime.now(dt.timezone.utc)
    wd, h0, h1 = GUARD_WINDOW
    if now.weekday() == wd and h0 <= now.hour < h1:
        return False, f"inside blocked window (Monday {h0:02d}:00-{h1:02d}:00 UTC) at {now:%a %H:%M}Z"
    try:
        out = subprocess.run(["systemctl", "is-active", WEEKLY_UNIT], capture_output=True,
                             text=True, timeout=15).stdout.strip()
    except Exception as exc:  # noqa: BLE001 - cannot prove it is idle: do not run
        return False, f"systemctl is-active failed: {exc}"
    if out != "inactive":
        return False, f"{WEEKLY_UNIT} is {out!r}"
    return True, "ok"


# =============================================================================
# geometry (section 3.4, owner-approved convention D10)
# =============================================================================
def wrap_deg(x):
    """Wrap to [-180, 180)."""
    return (np.asarray(x, float) + 180.0) % 360.0 - 180.0


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing 1 -> 2, degrees clockwise from north."""
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dl = np.deg2rad(lon2 - lon1)
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return np.rad2deg(np.arctan2(y, x))


def _local_xy(lat0, lon0, lat, lon):
    """(east, north) km of (lat, lon) in the tangent plane at (lat0, lon0)."""
    d = haversine_km(lat0, lon0, lat, lon)
    b = np.deg2rad(bearing_deg(lat0, lon0, lat, lon))
    return d * np.sin(b), d * np.cos(b)


def observed_motion(times, lat, lon, k: int):
    """(heading deg cw from north, speed km/h) at fix ``k`` of an observed track.

    Centred 6 h + 6 h difference; one-sided at the first/last fix; a 3-h
    neighbour is used when the 6-h one is missing. ``(nan, nan)`` if fewer than
    two usable fixes lie within +/-6 h.
    """
    t = np.asarray([np.datetime64(x, "s") for x in times])
    sec = lambda a, b: float((a - b) / np.timedelta64(1, "s"))  # noqa: E731

    def find(dh):
        target = t[k] + np.timedelta64(int(dh * 3600), "s")
        hit = np.where(t == target)[0]
        return int(hit[0]) if hit.size else None

    back = find(-6) if find(-6) is not None else find(-3)
    fwd = find(6) if find(6) is not None else find(3)
    if back is None and fwd is None:
        return float("nan"), float("nan")
    i0 = back if back is not None else k
    i1 = fwd if fwd is not None else k
    if i0 == i1:
        return float("nan"), float("nan")
    x0, y0 = _local_xy(lat[k], lon[k], lat[i0], lon[i0])
    x1, y1 = _local_xy(lat[k], lon[k], lat[i1], lon[i1])
    hrs = sec(t[i1], t[i0]) / 3600.0
    vx, vy = (x1 - x0) / hrs, (y1 - y0) / hrs
    return float(np.rad2deg(np.arctan2(vx, vy))), float(np.hypot(vx, vy))


def along_cross(obs_lat, obs_lon, fc_lat, fc_lon, heading_deg, speed_kmh):
    """(distance, along, cross) km. along > 0 forecast ahead, cross > 0 right of
    the observed motion. along/cross are NaN for an undefined / stationary motion."""
    d = float(haversine_km(obs_lat, obs_lon, fc_lat, fc_lon))
    if not (np.isfinite(heading_deg) and np.isfinite(speed_kmh)) or speed_kmh < STATIONARY_KMH:
        return d, float("nan"), float("nan")
    beta = float(bearing_deg(obs_lat, obs_lon, fc_lat, fc_lon))
    delta = np.deg2rad(float(wrap_deg(beta - heading_deg)))
    return d, float(d * np.cos(delta)), float(d * np.sin(delta))


# =============================================================================
# prototype storm-following tracker
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
    be within ``max_gc`` great-circle degrees (the PROOF/EWB closed-contour test).
    """
    ny, nx = sm.shape
    hr = int(math.ceil(p.max_gc_deg / dlat)) + 1
    coslat = max(0.2, math.cos(math.radians(float(lat[r]))))
    hc = int(math.ceil(p.max_gc_deg / (dlon * coslat))) + 1
    r0, r1, c0, c1 = max(0, r - hr), min(ny, r + hr + 1), max(0, c - hc), min(nx, c + hc + 1)
    box = sm[r0:r1, c0:c1]
    lab, _ = ndimage.label(box < sm[r, c] + p.delta_hPa, structure=np.ones((3, 3), int))
    comp = lab == lab[r - r0, c - c0]
    # a set that reaches the box edge is open; so is one that reaches the window edge
    # (the box is clipped there: "unknown" counts as not closed)
    if comp[0, :].any() or comp[-1, :].any() or comp[:, 0].any() or comp[:, -1].any():
        return False
    rr, cc = np.nonzero(comp)
    d = haversine_deg(float(lat[r]), float(lon[c]), lat[r0 + rr], lon[c0 + cc])
    return bool(np.max(d) < p.max_gc_deg)


def follow_track(field: np.ndarray, lat: np.ndarray, lon: np.ndarray,
                 start_lat: float, start_lon: float, p: TrackerParams):
    """Follow one low through a ``(step, lat, lon)`` MSLP stack (hPa).

    ``step`` 0 is the init (lead 0); lat ascending, lon ascending and contiguous
    (may exceed 180). Returns ``(lat, lon, mslp, ok, nodata)`` arrays, one per
    step; ``ok`` is False where there is no valid low or the track is lost,
    position and MSLP are NaN there. ``nodata`` marks steps whose field is all
    fill/NaN (e.g. GraphCast lead 0): they are not the tracker's failure, do not
    count towards losing the track, and are not verified.
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
        # candidate local minima within r_step of the predicted position
        is_min = sm == ndimage.minimum_filter(sm, size=2 * nb + 1, mode="nearest")
        rr, cc = np.nonzero(is_min)
        if rr.size:
            d = haversine_deg(pred_lat, pred_lon, lat[rr], lon[cc])
            near = d <= p.r_step_deg
            rr, cc, d = rr[near], cc[near], d[near]
        found = None
        for i in np.argsort(d) if rr.size else []:
            if _closed_low(sm, lat, lon, int(rr[i]), int(cc[i]), p, dlat, dlon):
                found = (int(rr[i]), int(cc[i]))
                break
        if found is None:
            misses += 1
            if misses >= p.lost_after:
                lost = True
            else:
                prev, vel = (pred_lat, pred_lon), vel              # carry on persistence
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
    out_lon = wrap_deg(out_lon)
    return out_lat, out_lon, out_p, ok, nodata


# =============================================================================
# observed truth (published events)
# =============================================================================
@dataclass
class Storm:
    kind: str                      # "tc" | "mlc"
    event_id: str
    name: str
    times: list                    # naive UTC datetimes
    lat: np.ndarray
    lon: np.ndarray
    pres: np.ndarray               # hPa, NaN when missing
    params: TrackerParams = field(default=TC_PARAMS)
    wind: np.ndarray | None = None  # kt (TC best-track slot 3); None for MLC

    def index(self) -> dict:
        return {t: i for i, t in enumerate(self.times)}


def _parse_wind(rows) -> np.ndarray:
    return np.array([np.nan if len(r) < 4 or r[3] is None else float(r[3]) for r in rows])


def _parse_track(rows) -> tuple[list, np.ndarray, np.ndarray, np.ndarray]:
    times = [dt.datetime.fromisoformat(r[0].replace("Z", "")) for r in rows]
    lat = np.array([float(r[1]) for r in rows])
    lon = wrap_deg(np.array([float(r[2]) for r in rows]))
    pres = np.array([np.nan if len(r) < 5 or r[4] is None else float(r[4]) for r in rows])
    return times, lat, lon, pres


def load_storms(path: str = EVENTS_JSON, tc_name: str = "Polo") -> dict[str, Storm]:
    ev = json.load(open(path))
    tcs = [e for e in ev if e["event_type"] == "tropical_cyclone"]
    pick = [e for e in tcs if e["details"].get("name") == tc_name
            and not e["details"].get("antimeridian_wrap")]
    if pick:
        # the strongest storm of that name (two published storms are called Polo)
        e = max(pick, key=lambda e: e["details"].get("peak_wind_kt") or 0)
    else:                                       # fallback of section 8.2: strongest storm
        e = max((x for x in tcs if not x["details"].get("antimeridian_wrap")),  # that crosses land is
                key=lambda x: x["details"].get("peak_wind_kt") or 0)            # picked by the owner
    t, la, lo, pr = _parse_track(e["details"]["track"])
    storms = {"tc": Storm("tc", e["id"], e["details"].get("name") or e["title"], t, la, lo, pr,
                          TC_PARAMS, _parse_wind(e["details"]["track"]))}
    mlcs = [x for x in ev if x["event_type"] == "midlat_cyclone"
            and not x["details"].get("antimeridian_wrap")
            and not x["details"].get("track_crosses_antimeridian")]
    m = max(mlcs, key=lambda x: ((x["details"].get("lifetime_h") or 0), bool(x["details"].get("bomb"))))
    t, la, lo, pr = _parse_track(m["details"]["track"])
    storms["mlc"] = Storm("mlc", m["id"], m["title"], t, la, lo, pr, MLC_PARAMS)
    return storms


def inits_for(storm: Storm) -> list[dt.datetime]:
    """00/12Z inits at which the storm exists (a fix at the init time, D2) and at
    least one 6-hourly verifying fix follows."""
    have = set(storm.times)
    out = []
    # first 00/12Z at or after the first fix
    first = storm.times[0]
    cur = first.replace(hour=(first.hour // 12) * 12, minute=0, second=0)
    if cur < first:
        cur += dt.timedelta(hours=12)
    while cur <= storm.times[-1] - dt.timedelta(hours=LEAD_STEP_H):
        if cur in have:
            out.append(cur)
        cur += dt.timedelta(hours=12)
    return out


# =============================================================================
# window + field readers
# =============================================================================
def window_for(storm: Storm, init: dt.datetime) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_start, lon_width) covering the observed track from
    ``init`` on, padded 15 deg (lon widened by 1/cos(lat)); lon_start/width in an
    unwrapped frame."""
    idx = [i for i, t in enumerate(storm.times)
           if init <= t <= init + dt.timedelta(hours=MAX_LEAD_H)]
    lat = storm.lat[idx]
    lon = unwrap_lons(storm.lon[idx])
    pad = 15.0
    lat0, lat1 = max(-89.75, lat.min() - pad), min(89.75, lat.max() + pad)
    coslat = max(0.2, math.cos(math.radians(min(80.0, float(np.max(np.abs(lat))) + pad))))
    lpad = min(90.0, pad / coslat)
    return float(lat0), float(lat1), float(lon.min() - lpad), float(min(360.0, lon.max() - lon.min() + 2 * lpad))


def _contiguous(lon_native: np.ndarray, lon_start: float, width: float):
    """(indices, lon coords) of the longitudes in [lon_start, lon_start+width], ordered
    west->east in a continuous frame."""
    off = (np.asarray(lon_native, float) - lon_start) % 360.0
    sel = np.where(off <= width)[0]
    sel = sel[np.argsort(off[sel])]
    return sel, lon_start + off[sel]


def _to_hpa(a: np.ndarray) -> np.ndarray:
    a = np.where(np.abs(a) < 9e35, a, np.nan)
    return a / 100.0 if np.nanmedian(a) > 5000.0 else a


def read_store_msl(model: str, init: dt.datetime, leads: list[int], win):
    """(lat, lon, field[lead, lat, lon] hPa, offset_h, open_s, read_s) from the icechunk
    store with ``open_init`` (never ``open_model``). Falls back to the nearest EARLIER
    init (the pipeline's rule): leads shift by the offset, same valid times."""
    import xarray as xr

    from ..sources import registry
    from ..sources.aiwp import _code
    from ..stores import mlwp_icechunk as store

    spec = registry.models()[model]
    code = _code(spec)
    lat0, lat1, lon0, width = win
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for offset in (0, 12):
        ini = init - dt.timedelta(hours=offset)
        try:
            ds = store.open_init(code, ini)
            da = ds["msl"].sel(init_time=np.datetime64(ini, "ns"))
            # an older init cannot supply valid times beyond 240 h: those leads stay NaN
            # ("no data", not a tracker miss)
            have = [j for j, h in enumerate(leads) if h + offset <= MAX_LEAD_H]
            if not have:
                raise KeyError("no lead within the 240 h horizon of the older init")
            da = da.sel(lead_time=np.array([np.timedelta64(leads[j] + offset, "h") for j in have]))
            li = np.where((da.latitude.values >= lat0) & (da.latitude.values <= lat1))[0]
            oi, lons = _contiguous(da.longitude.values, lon0, width)
            da = da.isel(latitude=li, longitude=oi)
            open_s = time.perf_counter() - t0
            t1 = time.perf_counter()
            part = np.asarray(da.compute().values, dtype=np.float32)
            read_s = time.perf_counter() - t1
            arr = np.full((len(leads),) + part.shape[1:], np.nan, np.float32)
            arr[have] = part
            lats = np.asarray(da.latitude.values, float)
            order = np.argsort(lats)
            return lats[order], lons, _to_hpa(arr[:, order, :]), offset, open_s, read_s
        except KeyError as exc:
            last_exc = exc
            continue
    raise FileNotFoundError(f"{model} init {init:%Y-%m-%d %H}Z not in store (also not -12h): {last_exc}")


def read_ifs_msl(init: dt.datetime, leads: list[int], win):
    """Same contract for ``ifs_hres`` through the existing ECMWF-direct backend."""
    from ..sources import ecmwf, registry

    spec = registry.models()["ifs_hres"]
    lat0, lat1, lon0, width = win
    lo_a = float(wrap_deg(lon0))
    lo_b = float(wrap_deg(lon0 + width)) if width < 360 else lo_a - 1e-6
    t0 = time.perf_counter()
    ds = ecmwf.open_forecast(spec, init, ["air_pressure_at_mean_sea_level"], leads,
                             (lat0, lat1, lo_a, lo_b))
    da = ds["air_pressure_at_mean_sea_level"].isel(init_time=0).load()
    read_s = time.perf_counter() - t0
    got = [int(x / np.timedelta64(1, "h")) for x in da.lead_time.values]
    oi, lons = _contiguous(da.longitude.values, lon0, width)
    arr = np.asarray(da.values, dtype=np.float32)[:, :, oi]
    lats = np.asarray(da.latitude.values, float)
    order = np.argsort(lats)
    full = np.full((len(leads), lats.size, lons.size), np.nan, np.float32)
    pos = {h: i for i, h in enumerate(leads)}
    for k, h in enumerate(got):
        if h in pos:
            full[pos[h]] = arr[k][order]
    return lats[order], lons, _to_hpa(full), 0, 0.0, read_s


# =============================================================================
# one (model, init) job
# =============================================================================
def run_job(job: dict) -> dict:
    """Worker entry: read the MSLP window, run the tracker, verify against the
    published observed track. Pure function of ``job``; never raises."""
    import resource

    import psutil

    kind, model, init_iso = job["kind"], job["model"], job["init"]
    init = dt.datetime.fromisoformat(init_iso)
    storm = load_storms(job["events_json"], job["tc_name"])[kind]
    idx = storm.index()
    # leads where a fix exists at the valid time
    leads = [h for h in range(0, MAX_LEAD_H + 1, LEAD_STEP_H)
             if init + dt.timedelta(hours=h) in idx]
    rec = {"kind": kind, "event_id": storm.event_id, "model": model, "init": init_iso,
           "n_leads_read": len(leads), "pid": os.getpid(), "status": "scored"}
    t_job = time.perf_counter()
    try:
        win = window_for(storm, init)
        if model == "ifs_hres":
            lat, lon, fld, offset, open_s, read_s = read_ifs_msl(init, leads, win)
        else:
            import dask

            with dask.config.set(scheduler="threads", num_workers=job["dask_threads"]):
                for attempt in range(3):          # transient S3 "error fetching virtual reference"
                    try:
                        lat, lon, fld, offset, open_s, read_s = read_store_msl(model, init, leads, win)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == 2 or "StorageError" not in f"{type(exc).__name__} {exc}":
                            raise
                        rec["retries"] = rec.get("retries", 0) + 1
                        time.sleep(2.0 * (attempt + 1))
        rec.update(offset_h=offset, open_s=round(open_s, 3), read_s=round(read_s, 3),
                   window=[round(x, 2) for x in win], grid=list(fld.shape))
        i0 = idx[init]
        t1 = time.perf_counter()
        flat, flon, fp, ok, nod = follow_track(fld, lat, lon, float(storm.lat[i0]),
                                          float(storm.lon[i0]), storm.params)
        rec["track_s"] = round(time.perf_counter() - t1, 3)
        samples = []
        for j, h in enumerate(leads):
            if h == 0:
                continue                      # lead 0 only seeds the tracker
            k = idx[init + dt.timedelta(hours=h)]
            hd, sp = observed_motion(storm.times, storm.lat, storm.lon, k)
            if nod[j]:
                samples.append({"lead": h, "nodata": True})
                continue
            row = {"lead": h, "tracked": bool(ok[j])}
            if ok[j]:
                d, a, c = along_cross(storm.lat[k], storm.lon[k], flat[j], flon[j], hd, sp)
                row.update(d_km=round(d, 1),
                           along_km=None if not np.isfinite(a) else round(a, 1),
                           cross_km=None if not np.isfinite(c) else round(c, 1),
                           dp_hpa=None if not np.isfinite(storm.pres[k]) else round(float(fp[j] - storm.pres[k]), 1),
                           fc_lat=round(float(flat[j]), 2), fc_lon=round(float(flon[j]), 2))
            samples.append(row)
        rec["samples"] = samples
    except FileNotFoundError as exc:
        rec.update(status="no_data", reason=str(exc)[:200], samples=[])
    except Exception as exc:  # noqa: BLE001 - a job failure is a measured outcome
        rec.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:200]}", samples=[])
    rec["job_s"] = round(time.perf_counter() - t_job, 3)
    rec["rss_gb_end"] = round(psutil.Process().memory_info().rss / 1e9, 3)
    rec["rss_gb_peak_self"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 3)
    return rec


# =============================================================================
# pool with guard + RSS sampler
# =============================================================================
class RssSampler:
    """Sample RSS of this process + children (per pid and total), like
    ``pipeline._PeakRss`` but keeping per-worker peaks."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.peak_total = 0
        self.peak_by_pid: dict[int, int] = {}
        self.timeline: list[tuple[float, float, int]] = []   # (t_s, total_gb, n_children)
        self._stop = threading.Event()
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def sample(self) -> int:
        import psutil

        me = psutil.Process()
        total = me.memory_info().rss
        kids = me.children(recursive=True)
        for c in kids:
            try:
                r = c.memory_info().rss
            except psutil.Error:
                continue
            total += r
            self.peak_by_pid[c.pid] = max(self.peak_by_pid.get(c.pid, 0), r)
        self.peak_total = max(self.peak_total, total)
        self.timeline.append((round(time.time() - self._t0, 1), round(total / 1e9, 2), len(kids)))
        return total

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self.sample()
        except Exception:  # noqa: BLE001
            pass


def _kill_children():
    import psutil

    for c in psutil.Process().children(recursive=True):
        try:
            c.kill()
        except psutil.Error:
            pass


def build_jobs(storms: dict[str, Storm], models: list[str], args) -> list[dict]:
    jobs = []
    for kind, st in storms.items():
        if args.events and kind not in args.events:
            continue
        ins = inits_for(st)
        if args.init_stride > 1:
            ins = ins[:: args.init_stride]
        if args.max_inits:
            ins = ins[: args.max_inits]
        for ini in ins:
            for m in models:
                jobs.append({"kind": kind, "model": m, "init": ini.isoformat(),
                             "events_json": args.events_json, "tc_name": args.tc_name,
                             "dask_threads": args.dask_threads})
    # interleave so every worker sees a mix of models and events
    jobs.sort(key=lambda j: (j["init"], j["kind"], j["model"]))
    return jobs


def cmd_run(args) -> int:
    from .._guard import require_ewb_host
    import psutil

    require_ewb_host()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, why = guard_status()
    if not ok:
        print(f"GUARD: refusing to start: {why}", file=sys.stderr)
        return 3
    avail = psutil.virtual_memory().available / 1e9
    if avail < args.min_avail_gb:
        print(f"refusing to start: only {avail:.0f} GB available (< {args.min_avail_gb})", file=sys.stderr)
        return 4
    storms = load_storms(args.events_json, args.tc_name)
    models = args.models or MODELS_ALL
    jobs = build_jobs(storms, models, args)
    jl = out / "jobs.jsonl"
    done_keys = set()
    if args.resume and jl.exists():
        for line in jl.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") in ("scored", "no_data"):      # errors are retried
                done_keys.add((r["kind"], r["model"], r["init"]))
        jobs = [j for j in jobs if (j["kind"], j["model"], j["init"]) not in done_keys]
    print(f"{len(jobs)} jobs ({len(done_keys)} already done), {args.workers} workers, "
          f"dask threads {args.dask_threads}, storms: "
          + ", ".join(f"{k}={s.name}/{s.event_id}" for k, s in storms.items()), flush=True)
    t_start = time.time()
    abort: str | None = None
    results: list[dict] = []
    ctx = mp.get_context("spawn")
    with RssSampler() as rss, jl.open("a") as sink:
        ex = cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx,
                                    max_tasks_per_child=args.recycle or None)
        pending: set = set()
        it = iter(jobs)
        last_guard = 0.0
        exhausted = False
        while (pending or not exhausted) and abort is None:
            # guard every launch / polling cycle
            gok, why = guard_status()
            last_guard = time.time()
            if not gok:
                abort = f"guard: {why}"
                break
            if psutil.virtual_memory().available / 1e9 < args.abort_avail_gb:
                abort = f"available memory below {args.abort_avail_gb} GB"
                break
            if time.time() - t_start > args.wall_cap_s:
                abort = f"wall-clock cap {args.wall_cap_s}s"
                break
            while not exhausted and len(pending) < args.workers + 1:
                try:
                    j = next(it)
                except StopIteration:
                    exhausted = True
                    break
                pending.add(ex.submit(run_job, j))
            done, pending = cf.wait(pending, timeout=args.poll_s, return_when=cf.FIRST_COMPLETED)
            for f in done:
                try:
                    r = f.result()
                except Exception as exc:  # noqa: BLE001 - worker died
                    r = {"status": "error", "reason": f"worker: {exc!r}", "samples": [],
                         "kind": "?", "model": "?", "init": "?"}
                results.append(r)
                sink.write(json.dumps(r) + "\n")
                sink.flush()
            if len(results) % 25 == 0 and done:
                el = time.time() - t_start
                print(f"[{el:6.0f}s] {len(results)}/{len(jobs)} done, pool rss "
                      f"{rss.timeline[-1][1] if rss.timeline else 0:.1f} GB, "
                      f"peak {rss.peak_total / 1e9:.1f} GB", flush=True)
        if abort:
            print(f"ABORT: {abort}", file=sys.stderr, flush=True)
            ex.shutdown(wait=False, cancel_futures=True)
            _kill_children()
        else:
            ex.shutdown(wait=True)
        wpids = {r.get("pid") for r in results}          # job-running workers (not the spawn tracker)
        wpeaks = [v for k, v in rss.peak_by_pid.items() if k in wpids]
        pool = {"workers": args.workers, "dask_threads": args.dask_threads, "recycle": args.recycle,
                "models": sorted({r.get("model") for r in results}),
                "peak_total_rss_gb": round(rss.peak_total / 1e9, 2),
                "peak_worker_rss_gb": round(max(wpeaks, default=0) / 1e9, 3),
                "worker_peaks_gb": sorted(round(v / 1e9, 3) for v in wpeaks),
                "wall_s": round(time.time() - t_start, 1), "abort": abort,
                "n_jobs_run": len(results),
                "timeline": rss.timeline[:: max(1, len(rss.timeline) // 300)]}
    (out / "pool.json").write_text(json.dumps(pool, indent=1))
    print(json.dumps({k: v for k, v in pool.items() if k != "timeline"}), flush=True)
    return 5 if abort else 0


# =============================================================================
# aggregation / report
# =============================================================================
def _pct(a, q):
    a = np.asarray([x for x in a if x is not None and np.isfinite(x)], float)
    return None if a.size == 0 else round(float(np.percentile(a, q)), 1)


def aggregate(jobs: list[dict], pool: dict | None, workers_target: int = 12,
              wind_at_init: dict | None = None) -> dict:
    """Headline numbers from the per-job records."""
    out: dict = {"n_jobs": len(jobs)}
    by_status: dict[str, int] = {}
    for r in jobs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    out["jobs_by_status"] = by_status
    good = [r for r in jobs if r["status"] == "scored"]
    if good:
        sec = lambda key: [r[key] for r in good if key in r]  # noqa: E731
        out["seconds_per_model_init"] = {
            "mean_job_s": round(float(np.mean(sec("job_s"))), 2),
            "median_job_s": round(float(np.median(sec("job_s"))), 2),
            "p90_job_s": _pct(sec("job_s"), 90),
            "mean_open_s": round(float(np.mean(sec("open_s"))), 2),
            "mean_read_s": round(float(np.mean(sec("read_s"))), 2),
            "mean_track_s": round(float(np.mean(sec("track_s"))), 2),
            "mean_leads_read": round(float(np.mean(sec("n_leads_read"))), 1),
            "mean_read_s_per_lead": round(float(np.sum(sec("read_s")) / max(1, np.sum(sec("n_leads_read")))), 3),
            "by_model_mean_job_s": {m: round(float(np.mean([r["job_s"] for r in good if r["model"] == m])), 2)
                                    for m in sorted({r["model"] for r in good})},
        }
    if pool:
        w = pool["workers"]
        pw = pool["peak_worker_rss_gb"]
        out["rss"] = {
            "workers_measured": w, "dask_threads_per_worker": pool.get("dask_threads"),
            "peak_worker_rss_gb": pw, "peak_pool_total_rss_gb_measured": pool["peak_total_rss_gb"],
            "parent_plus_overhead_gb": round(pool["peak_total_rss_gb"] - sum(pool["worker_peaks_gb"]), 2)
            if pool["worker_peaks_gb"] else None,
            f"projected_pool_rss_gb_at_{workers_target}_workers": round(workers_target * pw + 0.5, 2),
            "projection_note": f"{workers_target} x peak-per-worker + 0.5 GB parent, extrapolated from a {w}-worker run",
        }
        out["wall"] = {"run_wall_s": pool["wall_s"], "abort": pool["abort"]}
    # miss rate / error by (kind, lead), pooled and per model
    out["by_kind"] = {}
    for kind in sorted({r["kind"] for r in good}):
        rows = [(r["model"], s) for r in good if r["kind"] == kind for s in r["samples"]
                if not s.get("nodata")]
        leads = sorted({s["lead"] for _, s in rows})
        per_lead, per_model = {}, {}
        for L in leads:
            sel = [s for _, s in rows if s["lead"] == L]
            tr = [s for s in sel if s["tracked"]]
            per_lead[L] = {"n": len(sel), "miss_rate": round(1 - len(tr) / len(sel), 3),
                           "median_d_km": _pct([s["d_km"] for s in tr], 50),
                           "p90_d_km": _pct([s["d_km"] for s in tr], 90),
                           "median_along_km": _pct([s["along_km"] for s in tr], 50),
                           "median_cross_km": _pct([s["cross_km"] for s in tr], 50),
                           "median_dp_hpa": _pct([s["dp_hpa"] for s in tr], 50)}
        for m in sorted({m for m, _ in rows}):
            per_model[m] = {}
            for L in (24, 48, 72, 96, 120):
                sel = [s for mm, s in rows if mm == m and s["lead"] == L]
                if not sel:
                    continue
                tr = [s for s in sel if s["tracked"]]
                per_model[m][L] = {"n": len(sel), "miss_rate": round(1 - len(tr) / len(sel), 3),
                                   "median_d_km": _pct([s["d_km"] for s in tr], 50),
                                   "p90_d_km": _pct([s["d_km"] for s in tr], 90)}
            win = [s for mm, s in rows if mm == m and 24 <= s["lead"] <= 72]
            per_model[m]["miss_rate_24_72h"] = round(1 - sum(s["tracked"] for s in win) / len(win), 3) if win else None
        gate = [s for _, s in rows if 24 <= s["lead"] <= 72]
        stages = {}
        if kind == "tc" and wind_at_init:
            def stage(w):
                return "unknown" if w is None or not np.isfinite(w) else (
                    "<34 kt (pre-TS)" if w < 34 else "34-63 kt (TS/Cat 1)" if w < 64 else ">=64 kt (hurricane)")
            for r in good:
                if r["kind"] != "tc":
                    continue
                st_ = stage(wind_at_init.get(r["init"]))
                d = stages.setdefault(st_, {"inits": set(), "n": 0, "tracked": 0, "d48": []})
                d["inits"].add(r["init"])
                for x in r["samples"]:
                    if x.get("nodata") or not (24 <= x["lead"] <= 72):
                        continue
                    d["n"] += 1
                    d["tracked"] += bool(x["tracked"])
                    if x["tracked"]:
                        d["d48"].append(x["d_km"])
            stages = {k: {"n_inits": len(v["inits"]), "n_samples_24_72h": v["n"],
                          "miss_rate_24_72h": round(1 - v["tracked"] / v["n"], 3) if v["n"] else None,
                          "median_d_km_24_72h": _pct(v["d48"], 50)} for k, v in sorted(stages.items())}
        out["by_kind"][kind] = {
            "by_init_intensity": stages,
            "by_lead": per_lead, "by_model": per_model,
            "miss_rate_24_72h_pooled": round(1 - sum(s["tracked"] for s in gate) / len(gate), 3) if gate else None,
            "n_samples_24_72h": len(gate),
            "n_nodata_samples": sum(1 for r in good if r["kind"] == kind for x in r["samples"] if x.get("nodata")),
            "event_id": next(r["event_id"] for r in good if r["kind"] == kind),
        }
    return out


def cmd_report(args) -> int:
    out = Path(args.out)
    latest: dict = {}
    for x in (out / "jobs.jsonl").read_text().splitlines():      # a retry supersedes an earlier error
        r = json.loads(x)
        latest[(r["kind"], r["model"], r["init"])] = r
    jobs = list(latest.values())
    pn = out / "pool_main.json"
    pool = json.loads(pn.read_text()) if pn.exists() else (
        json.loads((out / "pool.json").read_text()) if (out / "pool.json").exists() else None)
    st = load_storms(args.events_json, args.tc_name)["tc"]
    wind = {t.isoformat(): float(w) for t, w in zip(st.times, st.wind)}
    agg = aggregate(jobs, pool, args.target_workers, wind)
    print(json.dumps(agg, indent=1))
    return 0


# =============================================================================
# sign sanity on the observed track alone (persistence forecast)
# =============================================================================
def persistence_sanity(storm: Storm) -> dict:
    """Straight-line continuation of the observed motion at each init, scored with
    the same along/cross code. On a recurving storm a non-turning forecast must end
    up on the LEFT (cross < 0) of an observed clockwise turn."""
    idx = storm.index()
    rows = []
    for ini in inits_for(storm):
        k0 = idx[ini]
        hd, sp = observed_motion(storm.times, storm.lat, storm.lon, k0)
        if not np.isfinite(sp) or sp < STATIONARY_KMH:
            continue
        for h in range(LEAD_STEP_H, MAX_LEAD_H + 1, LEAD_STEP_H):
            k = idx.get(ini + dt.timedelta(hours=h))
            if k is None:
                continue
            dist = sp * h
            # destination point at constant heading/speed from the observed init fix
            la1, lo1, br, ang = (math.radians(storm.lat[k0]), math.radians(storm.lon[k0]),
                                 math.radians(hd), dist / R_EARTH_KM)
            la2 = math.asin(math.sin(la1) * math.cos(ang) + math.cos(la1) * math.sin(ang) * math.cos(br))
            lo2 = lo1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(la1),
                                   math.cos(ang) - math.sin(la1) * math.sin(la2))
            hdg_k, sp_k = observed_motion(storm.times, storm.lat, storm.lon, k)
            d, a, c = along_cross(storm.lat[k], storm.lon[k], math.degrees(la2),
                                  float(wrap_deg(math.degrees(lo2))), hdg_k, sp_k)
            rows.append({"lead": h, "d": d, "along": a, "cross": c})
    res = {"n_samples": len(rows), "by_lead": {}}
    for L in (24, 48, 72, 96, 120, 168, 240):
        sel = [r for r in rows if r["lead"] == L and np.isfinite(r["cross"])]
        if sel:
            res["by_lead"][L] = {"n": len(sel),
                                 "median_cross_km": _pct([r["cross"] for r in sel], 50),
                                 "median_along_km": _pct([r["along"] for r in sel], 50),
                                 "frac_cross_negative": round(float(np.mean([r["cross"] < 0 for r in sel])), 2),
                                 "frac_cross_positive": round(float(np.mean([r["cross"] > 0 for r in sel])), 2)}
    # heading change of the observed track (clockwise positive): first vs last third
    hds = [observed_motion(storm.times, storm.lat, storm.lon, k)[0] for k in range(len(storm.times))]
    hds = np.array([h for h in hds if np.isfinite(h)])
    third = max(1, hds.size // 3)

    def mean_heading(h):
        r = np.deg2rad(h)
        return float(np.rad2deg(np.arctan2(np.mean(np.sin(r)), np.mean(np.cos(r)))))

    res["observed_heading_first_third_deg"] = round(mean_heading(hds[:third]), 1)
    res["observed_heading_last_third_deg"] = round(mean_heading(hds[-third:]), 1)
    res["observed_turn_cw_deg"] = round(float(wrap_deg(mean_heading(hds[-third:]) - mean_heading(hds[:third]))), 1)
    return res


def synthetic_sign_cases() -> dict:
    """Section 3.4 hand checks through the same function (observed at 0N 0E moving north)."""
    obs = (0.0, 0.0)
    d = 111.195
    cases = {"ahead": (1.0, 0.0), "behind": (-1.0, 0.0), "right": (0.0, 1.0), "left": (0.0, -1.0)}
    return {k: tuple(round(v, 1) for v in along_cross(obs[0], obs[1], la, lo, 0.0, 30.0)[1:])
            for k, (la, lo) in cases.items()} | {"unit_km": d}


def recurver_sanity(path: str, min_turn_deg: float = 60.0) -> dict:
    """Sign check on every published non-wrapped TC that clearly turns: a clockwise
    turn (NH recurvature) must put the straight-line persistence forecast LEFT of
    the observed track (cross < 0); an anticlockwise turn (SH) must put it RIGHT."""
    ev = json.load(open(path))
    rows, storms = [], []
    for e in ev:
        if e["event_type"] != "tropical_cyclone" or e["details"].get("antimeridian_wrap"):
            continue
        t, la, lo, pr = _parse_track(e["details"]["track"])
        st = Storm("tc", e["id"], e["details"].get("name") or e["title"], t, la, lo, pr, TC_PARAMS)
        ps = persistence_sanity(st)
        turn = ps["observed_turn_cw_deg"]
        if abs(turn) < min_turn_deg:
            continue
        sh = float(np.mean(la)) < 0
        sel = [v for L, v in ps["by_lead"].items() if 48 <= L <= 120 and v["n"] >= 1]
        if not sel:
            continue
        expect_neg = turn > 0                              # clockwise turn -> persistence on the left
        n = sum(v["n"] for v in sel)
        agree = sum(v["n"] * (v["frac_cross_negative"] if expect_neg else v["frac_cross_positive"]) for v in sel) / n
        storms.append({"name": st.name, "event_id": st.event_id, "hemisphere": "S" if sh else "N",
                       "turn_cw_deg": turn, "expected_cross_sign": "negative" if expect_neg else "positive",
                       "n_samples_48_120h": n, "fraction_matching_expected_sign": round(float(agree), 2)})
    tot = sum(x["n_samples_48_120h"] for x in storms)
    return {"min_turn_deg": min_turn_deg, "n_storms": len(storms),
            "pooled_fraction_matching_expected_sign":
                round(sum(x["n_samples_48_120h"] * x["fraction_matching_expected_sign"] for x in storms) / tot, 2) if tot else None,
            "storms": storms}


def cmd_sanity(args) -> int:
    storms = load_storms(args.events_json, args.tc_name)
    res = {"synthetic_north_moving": synthetic_sign_cases(),
           "published_tc_recurvers": recurver_sanity(args.events_json),
           **{k: {"storm": s.name, "event_id": s.event_id, **persistence_sanity(s)} for k, s in storms.items()}}
    print(json.dumps(res, indent=1))
    return 0


# =============================================================================
# EWB landfall classes spike (TC only)
# =============================================================================
def cmd_landfall(args) -> int:
    """Feed pilot tracks (one model, one init per row of jobs.jsonl is not enough:
    the tracks are not stored per step) -- re-derives tracks for a few inits of one
    model and calls EWB's ``LandfallMetric`` through its ``forecast_landfall`` /
    ``target_landfall`` path. Writes ``landfall_spike.json``."""
    from .._guard import require_ewb_host

    require_ewb_host()
    ok, why = guard_status()
    if not ok:
        print(f"GUARD: refusing to start: {why}", file=sys.stderr)
        return 3
    import xarray as xr

    res: dict = {"steps": []}
    try:
        from extremeweatherbench import calc, metrics as ewb_metrics, utils as ewb_utils
    except Exception as exc:  # noqa: BLE001
        res["fatal"] = f"import extremeweatherbench failed: {exc!r}"
        (Path(args.out) / "landfall_spike.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res))
        return 0

    storm = load_storms(args.events_json, args.tc_name)["tc"]
    # observed target track DataArray (valid_time,)
    vt = np.array(storm.times, dtype="datetime64[ns]")
    mslp_obs = np.where(np.isfinite(storm.pres), storm.pres * 100.0, np.nan)   # Pa
    target = xr.DataArray(mslp_obs, dims=("valid_time",),
                          coords={"valid_time": vt, "latitude": ("valid_time", storm.lat),
                                  "longitude": ("valid_time", storm.lon)},
                          name="air_pressure_at_mean_sea_level")
    try:
        land = ewb_utils.load_land_geometry()
        ocean = ewb_utils.load_ocean_geometry()
        tl = calc.find_landfalls(target, land_geom=land, ocean_geom=ocean)
        res["steps"].append({"step": "observed find_landfalls", "ok": True,
                             "n_landfalls": int(tl.sizes.get("landfall", 0)) if hasattr(tl, "sizes") else None,
                             "repr": str(tl)[:600]})
    except Exception as exc:  # noqa: BLE001
        res["steps"].append({"step": "observed find_landfalls", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"})
        (Path(args.out) / "landfall_spike.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res)[:1500])
        return 0

    # forecast tracks for inits of one model from the stored pilot samples
    jobs = [json.loads(x) for x in (Path(args.out) / "jobs.jsonl").read_text().splitlines()]
    mine = [r for r in jobs if r["kind"] == "tc" and r["model"] == args.model and r["status"] == "scored"]
    mine.sort(key=lambda r: r["init"])
    inits, rows = [], []
    tidx = storm.index()
    for r in mine:
        ini = dt.datetime.fromisoformat(r["init"])
        pts = []
        for s in r["samples"]:
            if not s.get("tracked"):
                continue
            k = tidx.get(ini + dt.timedelta(hours=s["lead"]))
            op = storm.pres[k] if k is not None else np.nan
            fp = (op + s["dp_hpa"]) if (s.get("dp_hpa") is not None and np.isfinite(op)) else np.nan
            pts.append((s["lead"], s["fc_lat"], s["fc_lon"], fp))      # fc MSLP = obs + (fc - obs)
        if len(pts) >= 4:
            inits.append(np.datetime64(r["init"], "ns"))
            rows.append(pts)
    res["n_inits_with_tracks"] = len(inits)
    if not inits:
        res["steps"].append({"step": "build forecast", "ok": False, "error": "no tracked inits"})
    else:
        leads = np.arange(LEAD_STEP_H, MAX_LEAD_H + 1, LEAD_STEP_H)
        lat = np.full((len(inits), leads.size), np.nan)
        lon = np.full_like(lat, np.nan)
        pres = np.full_like(lat, np.nan)
        for i, pts in enumerate(rows):
            for L, la, lo, fp in pts:
                j = int(L // LEAD_STEP_H) - 1
                lat[i, j], lon[i, j] = la, lo
                pres[i, j] = fp * 100.0                    # Pa; NaN where the best track has no pressure
        fc = xr.DataArray(
            pres, dims=("init_time", "lead_time"),
            coords={"init_time": np.array(inits, dtype="datetime64[ns]"),
                    "lead_time": leads.astype("timedelta64[h]").astype("timedelta64[ns]")},
            name="air_pressure_at_mean_sea_level")
        fc = fc.assign_coords(valid_time=(("init_time", "lead_time"),
                                          fc.init_time.values[:, None] + fc.lead_time.values[None, :]),
                              latitude=(("init_time", "lead_time"), lat),
                              longitude=(("init_time", "lead_time"), lon))
        res["forecast_layout"] = str(fc.dims) + " coords " + str(list(fc.coords))
        # EWB expects (lead_time, valid_time); build that layout explicitly
        try:
            vt_f = fc.valid_time.values
            uniq = np.unique(vt_f[~np.isnat(vt_f)])
            f2 = np.full((leads.size, uniq.size), np.nan)
            la2 = np.full_like(f2, np.nan)
            lo2 = np.full_like(f2, np.nan)
            for i in range(len(inits)):
                for j in range(leads.size):
                    if np.isfinite(lat[i, j]):
                        u = int(np.searchsorted(uniq, vt_f[i, j]))
                        f2[j, u], la2[j, u], lo2[j, u] = pres[i, j], lat[i, j], lon[i, j]
            ewb_fc = xr.DataArray(f2, dims=("lead_time", "valid_time"),
                                  coords={"lead_time": fc.lead_time.values, "valid_time": uniq,
                                          "latitude": (("lead_time", "valid_time"), la2),
                                          "longitude": (("lead_time", "valid_time"), lo2)},
                                  name="air_pressure_at_mean_sea_level")
            fl = calc.find_landfalls(ewb_fc, land_geom=land, ocean_geom=ocean)
            res["steps"].append({"step": "forecast find_landfalls", "ok": True,
                                 "n_landfalls": int(fl.sizes.get("landfall", 0)) if hasattr(fl, "sizes") else None,
                                 "repr": str(fl)[:600]})
            lm = ewb_metrics.LandfallMetric(
                metrics=[ewb_metrics.LandfallDisplacement, ewb_metrics.LandfallTimeMeanError,
                         ewb_metrics.LandfallIntensityMeanAbsoluteError],
                approach="next",
                forecast_variable="air_pressure_at_mean_sea_level",
                target_variable="air_pressure_at_mean_sea_level")
            fcl, tgl = lm.maybe_compute_landfalls(ewb_fc, target)
            res["steps"].append({"step": "LandfallMetric.maybe_compute_landfalls", "ok": True,
                                 "forecast_repr": str(fcl)[:400], "target_repr": str(tgl)[:400]})
            vals = {}
            for m in lm._metric_instances:
                try:
                    v = m.compute_metric(ewb_fc, target, forecast_landfall=fcl, target_landfall=tgl)
                    vals[type(m).__name__] = {"ok": True, "repr": str(v)[:400]}
                except Exception as exc:  # noqa: BLE001
                    vals[type(m).__name__] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            res["metrics"] = vals
        except Exception as exc:  # noqa: BLE001
            import traceback

            res["steps"].append({"step": "forecast/landfall metric", "ok": False,
                                 "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                                 "tb": traceback.format_exc()[-1500:]})
    (Path(args.out) / "landfall_spike.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, default=str)[:3000])
    return 0


# =============================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--events-json", default=EVENTS_JSON)
        p.add_argument("--tc-name", default="Polo")
        p.add_argument("--out", default="/opt/ewb-pilot/out")

    r = sub.add_parser("run")
    common(r)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--dask-threads", type=int, default=4, help="dask threads per worker")
    r.add_argument("--models", nargs="*")
    r.add_argument("--events", nargs="*", choices=["tc", "mlc"])
    r.add_argument("--init-stride", type=int, default=1)
    r.add_argument("--max-inits", type=int, default=0)
    r.add_argument("--wall-cap-s", type=int, default=2400)
    r.add_argument("--poll-s", type=float, default=5.0)
    r.add_argument("--min-avail-gb", type=float, default=60.0)
    r.add_argument("--abort-avail-gb", type=float, default=40.0)
    r.add_argument("--resume", action="store_true")
    r.add_argument("--recycle", type=int, default=0,
                   help="restart a worker after this many jobs (bounds per-worker RSS growth)")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("sanity")
    common(s)
    s.set_defaults(fn=cmd_sanity)
    lf = sub.add_parser("landfall")
    common(lf)
    lf.add_argument("--model", default="graphcast")
    lf.set_defaults(fn=cmd_landfall)
    rp = sub.add_parser("report")
    common(rp)
    rp.add_argument("--target-workers", type=int, default=12)
    rp.set_defaults(fn=cmd_report)
    sub.add_parser("guard").set_defaults(fn=lambda a: (print(*guard_status()), 0)[1])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
