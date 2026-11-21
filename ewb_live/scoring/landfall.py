"""EWB landfall classes on our storm-following TC tracks (notes/scoring_design.md 3.2, 8.5).

Our forecast tracks (``tc.score_init`` -> ``rec["fc_track"]``) are shaped into EWB's
forecast-track layout and handed, together with a target built from the storm's own
IBTrACS series (``event.details["track"]``), to ``LandfallMetric`` through its documented
``forecast_landfall`` / ``target_landfall`` kwargs path (the WP0 pilot, notes/scoring_pilot.md
section 6, showed this accepts our tracks). No name-based IBTrACS lookup is involved: our TC
titles carry no storm name, so the storm is identified by ``details.sid`` (IBTrACS id) and the
target is that storm's best-track series, never an EWB ``inputs.IBTrACS`` name filter.

Rows (scores.parquet columns; ``init_time`` = true init, ``lead_time_hours`` = hours from the
init to the OBSERVED landfall, rounded to 6 h):

* ``landfall_displacement`` km, ``landfall_time_me`` h (forecast - observed, > 0 = later),
  ``landfall_intensity_mae`` hPa (EWB computes Pa; divided by 100): one row per (init) whose
  forecast landfall EWB paired with the next observed landfall (approach "next", EWB's default
  +/- 24 h window; island crossings are handled as EWB does).
* ``landfall_miss`` 0/1: for every verified init with an observed landfall ahead within the
  240 h horizon, 0 when EWB paired a forecast landfall with it, 1 when it did not (no forecast
  landfall: the low was lost / stayed offshore / never reached land, or the timing error
  exceeded EWB's window). Misses carry no value rows (never imputed).

Storms with no observed landfall produce NO rows (not NaN rows). Storms EWB's detector cannot
read are reported by :func:`scorable` and also produce no rows. Needs ``extremeweatherbench``
(and its Natural Earth land geometry); imported lazily.
"""
from __future__ import annotations

import datetime as dt
import functools
import logging

import numpy as np
import pandas as pd

from . import track_verif as tv

logger = logging.getLogger("ewb_live.scoring.landfall")

M_DISP, M_TIME, M_INT, M_MISS = ("landfall_displacement", "landfall_time_me",
                                 "landfall_intensity_mae", "landfall_miss")
LANDFALL_METRICS = (M_DISP, M_TIME, M_INT, M_MISS)
VAR = "air_pressure_at_mean_sea_level"
LEAD_STEP_H = tv.LEAD_STEP_H


def storm_id(event) -> dict:
    """IBTrACS identity of a published storm (``details.sid`` / ``name`` / ``season``)."""
    d = event.details or {}
    return {"sid": d.get("sid"), "name": d.get("name"), "season": d.get("season")}


def scorable(event) -> tuple[bool, str]:
    """``(ok, reason)``: can EWB's landfall detector read this storm's tracks?

    Antimeridian-crossing tracks are scorable: EWB's own IBTrACS path hands the same wrapped
    longitudes to ``find_landfalls`` and the detector is not fooled by the 179 -> -179 jump
    (checked on Nolo and Olana, notes/landfall_validation.md: landfall time and position identical
    to EWB's target). Storms without a sid / track (nothing to key the target on) are not.
    """
    d = event.details or {}
    if not d.get("sid") or not d.get("track"):
        return False, "no IBTrACS sid / track in the event details"
    return True, ""


@functools.lru_cache(maxsize=1)
def _geoms():
    from extremeweatherbench import utils as ewb_utils

    return ewb_utils.load_land_geometry(), ewb_utils.load_ocean_geometry()


def observed_target(track: tv.ObservedTrack):
    """The storm's best track as EWB's target layout: dim ``valid_time``, coords ``latitude`` /
    ``longitude``, values = best-track pressure in Pa (NaN where missing)."""
    import xarray as xr

    vt = np.array(track.times, dtype="datetime64[ns]")
    return xr.DataArray(np.where(np.isfinite(track.pres), track.pres * 100.0, np.nan),
                        dims=("valid_time",),
                        coords={"valid_time": vt, "latitude": ("valid_time", track.lat),
                                "longitude": ("valid_time", track.lon)}, name=VAR)


def forecast_array(fc_tracks: list[dict]):
    """EWB forecast-track layout ``(lead_time, valid_time)`` with 2-D ``latitude`` /
    ``longitude`` coords from per-init tracks (``{"init_true", "lead_true", "lat", "lon", "p"}``,
    p in hPa, NaN lat/lon where the track was not followed). ``valid_time = init + lead`` keeps
    the mapping to ``init_time`` lossless. None when no init has a followed point."""
    import xarray as xr

    pts = []
    for t in fc_tracks:
        init = np.datetime64(t["init_true"], "ns")
        for h, la, lo, p in zip(t["lead_true"], t["lat"], t["lon"], t["p"]):
            if np.isfinite(la) and np.isfinite(lo):
                pts.append((float(h), init + np.timedelta64(int(round(h)), "h"), la, lo, p))
    if not pts:
        return None
    leads = np.array(sorted({p[0] for p in pts}))
    vts = np.array(sorted({p[1] for p in pts}))
    li = {h: i for i, h in enumerate(leads)}
    vi = {v: i for i, v in enumerate(vts)}
    shape = (leads.size, vts.size)
    val, lat, lon = np.full(shape, np.nan), np.full(shape, np.nan), np.full(shape, np.nan)
    for h, v, la, lo, p in pts:
        i, j = li[h], vi[v]
        val[i, j] = p * 100.0 if np.isfinite(p) else np.nan
        lat[i, j], lon[i, j] = la, lo
    return xr.DataArray(val, dims=("lead_time", "valid_time"),
                        coords={"lead_time": (leads * 3600e9).astype("timedelta64[ns]"), "valid_time": vts,
                                "latitude": (("lead_time", "valid_time"), lat),
                                "longitude": (("lead_time", "valid_time"), lon)}, name=VAR)


def _lead_slot(hours: float) -> float:
    return float(max(LEAD_STEP_H, LEAD_STEP_H * round(hours / LEAD_STEP_H)))


def observed_landfalls(track: tv.ObservedTrack) -> np.ndarray:
    """Valid times (datetime64[ns]) of the observed ocean -> land crossings, EWB's detector."""
    from extremeweatherbench import calc

    land, ocean = _geoms()
    tl = calc.find_landfalls(observed_target(track), land_geom=land, ocean_geom=ocean)
    if len(tl) == 0:
        return np.array([], dtype="datetime64[ns]")
    return np.sort(np.asarray(tl.coords["valid_time"].values, dtype="datetime64[ns]"))


def landfall_rows(event, model: str, track: tv.ObservedTrack, fc_tracks: list[dict],
                  max_lead_h: float = tv.MAX_LEAD_H, pooled: bool = False) -> tuple[pd.DataFrame, dict]:
    """``(rows, info)`` for one (storm, model). ``info``: ``status`` (``none`` | ``unscorable`` |
    ``scored``), ``n_landfalls`` (observed), ``n_eligible`` (inits with an observed landfall
    ahead), ``n_paired``, ``reason``."""
    empty = pd.DataFrame(columns=tv.SCORE_COLUMNS)
    ok, why = scorable(event)
    if not ok:
        return empty, {"status": "unscorable", "reason": why, "n_landfalls": 0, "n_eligible": 0, "n_paired": 0}
    obs_lf = observed_landfalls(track)
    if obs_lf.size == 0:
        return empty, {"status": "none", "reason": "no observed landfall (open-ocean or already-inland storm)",
                       "n_landfalls": 0, "n_eligible": 0, "n_paired": 0}
    horizon = np.timedelta64(int(max_lead_h), "h")
    eligible: dict = {}                                   # init (ns) -> lead hours to the next observed landfall
    for t in fc_tracks:
        init = np.datetime64(t["init_true"], "ns")
        ahead = obs_lf[(obs_lf > init) & (obs_lf <= init + horizon)]
        if ahead.size:
            eligible[init] = float((ahead[0] - init) / np.timedelta64(1, "h"))
    info = {"status": "scored", "n_landfalls": int(obs_lf.size), "n_eligible": len(eligible), "n_paired": 0}
    if not eligible:
        return empty, {**info, "reason": "no verified init has an observed landfall within 240 h"}

    paired: dict = {}                                     # init -> {"lead": h, "v": {metric: value}}
    if fc_tracks:
        from extremeweatherbench import metrics as m

        target = observed_target(track)
        lm = m.LandfallMetric(
            metrics=[m.LandfallDisplacement, m.LandfallTimeMeanError, m.LandfallIntensityMeanAbsoluteError],
            approach="next", forecast_variable=VAR, target_variable=VAR)
        names = {m.LandfallDisplacement: M_DISP, m.LandfallTimeMeanError: M_TIME,
                 m.LandfallIntensityMeanAbsoluteError: M_INT}
        # One EWB call per init by default: ``calc.find_landfalls`` de-duplicates forecast landfalls
        # within 50 km ACROSS the inits it is given (``_deduplicate_landfalls``), so a pooled call
        # keeps only the first init's landfall whenever successive inits agree on the coast and
        # turns the others into spurious misses (notes/landfall_validation.md). ``pooled=True``
        # reproduces EWB's pooled behaviour for that comparison.
        groups = [fc_tracks] if pooled else [[t] for t in fc_tracks]
        for grp in groups:
            fc = forecast_array(grp)
            if fc is None:
                continue
            # EWB's own detection + next-landfall matching + time window once, then each metric
            # class through the documented pre-computed kwargs (no repeated detection)
            fcl, tgl = lm.maybe_compute_landfalls(fc, target)
            for inst in lm._metric_instances:
                res = inst.compute_metric(fc, target, forecast_landfall=fcl, target_landfall=tgl)
                if "init_time" not in getattr(res, "dims", ()) or "target_landfall_valid_time" not in res.coords:
                    continue
                for init, v, tv_ in zip(res.init_time.values, res.values,
                                        res.coords["target_landfall_valid_time"].values):
                    if not np.isfinite(v):
                        continue
                    name = names[type(inst)]
                    if name == M_INT:
                        v = v / 100.0                      # EWB: Pa -> hPa
                    e = paired.setdefault(np.datetime64(init, "ns"), {"lead": None, "v": {}})
                    e["v"][name] = float(v)
                    e["lead"] = float((np.datetime64(tv_, "ns") - np.datetime64(init, "ns")) / np.timedelta64(1, "h"))
    recs = []
    n_paired = 0
    for init, lead_next in sorted(eligible.items()):
        e = paired.get(init)
        if e is None or not e["v"]:
            recs.append((M_MISS, _lead_slot(lead_next), 1.0, init))
            continue
        n_paired += 1
        lead = _lead_slot(e["lead"])
        for name, v in e["v"].items():
            recs.append((name, lead, v, init))
        recs.append((M_MISS, lead, 0.0, init))
    info["n_paired"] = n_paired
    info["reason"] = f"{n_paired}/{len(eligible)} inits paired with an observed landfall"
    df = pd.DataFrame({
        "event_id": np.full(len(recs), event.id, dtype=object),
        "event_type": np.full(len(recs), event.event_type, dtype=object),
        "model": np.full(len(recs), model, dtype=object),
        "metric": [r[0] for r in recs],
        "lead_time_hours": np.array([r[1] for r in recs], dtype="float64"),
        "value": np.array([r[2] for r in recs], dtype="float64"),
        "init_time": np.array([r[3] for r in recs], dtype="datetime64[ns]")})
    return df[tv.SCORE_COLUMNS], info
