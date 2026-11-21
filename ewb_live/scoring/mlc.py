"""Mid-latitude cyclone lane scorer (notes/scoring_design.md sections 4, 6, 8.4; decision D7).

Truth is the ERA5 MSLP track each low was detected on (``details.track``, 6-hourly,
0.25 degrees), the SAME grid the models are read on, so unlike the TC lane there is no
resolution floor on the central pressure. Two sets of rows per ``(event, model)``:

* MLC-A (the shared core of ``track_verif``, MLC tracker parameters): per
  ``(init, lead)`` ``TrackPositionError`` / ``AlongTrackError`` / ``CrossTrackError`` /
  ``CentralPressureError`` and a ``TrackMiss`` 0/1 row (never imputed, D5).
* MLC-B (no tracker, so no misses): ``PeakDepthError`` = the forecast's lowest MSLP in a
  +/-8 degree window around the OBSERVED centre minus the ERA5 central pressure (hPa,
  signed, > 0 = forecast too shallow), and the bomb check. For every verifying
  ``(init, lead >= 24 h)`` the observed and the forecast 24 h fall of that central pressure
  are scaled to 60 degrees (Sanders & Gyakum, the rule ``mlc_track`` tags bombs with:
  threshold 24 hPa * sin|lat| / sin 60 at the observed latitude); a window whose observed
  scaled fall reaches -24 hPa is an observed bomb window and emits ``BombDetected`` (1 =
  the forecast also fell at bomb rate); every other window emits ``BombFalseAlarm`` (1 =
  the forecast claimed a bomb the analysis did not have). Means are the hit rate and the
  false-alarm rate (share of non-bomb windows).

D2 for this lane keeps the original wording: an init is verified when the low already
exists in ERA5 at the init (a fix AT the init and one 6 h later); there is no 34 kt
analogue, so nothing is "not verified". Truth horizon: the track stops at the ERA5 (ARCO)
edge, so leads whose valid time has no analysis fix are never samples. Readers, retry
policy and the pair statuses are the TC lane's (``scoring.tc``).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable

import numpy as np
import pandas as pd

from . import tc as tc_lane
from . import track_verif as tv

EVENT_TYPE = "midlat_cyclone"
# the published track is an ERA5 (ARCO) product by construction, so the tag does not follow the
# ARCO-vs-HRES coverage of the event window (``truth.truth_source_for_event`` returns this for MLC)
TRUTH_TAG = "era5_arco"
BASELINE_MODEL = tc_lane.BASELINE_MODEL
NoData = tc_lane.NoData
PARAMS = tv.MLC_PARAMS
PEAK_WINDOW_DEG = 8.0               # half-width of the peak-depth window around the observed centre
BOMB_WINDOW_H = 24
BOMB_REF_LAT = 60.0
BOMB_HPA = 24.0

M_PEAK, M_BOMB_HIT, M_BOMB_FA = "PeakDepthError", "BombDetected", "BombFalseAlarm"
DEPTH_METRICS = (M_PEAK, M_BOMB_HIT, M_BOMB_FA)
MLC_METRICS = tv.TRACK_METRICS + DEPTH_METRICS


# =============================================================================
# storm adapter
# =============================================================================
def observed_track(event) -> tv.ObservedTrack:
    """ERA5-track adapter: ``event.details["track"]`` rows ``[iso, lat, lon, mslp, pressure]``
    (slots 3 and 4 are the same central MSLP for this lane)."""
    rows = event.details["track"]
    times = [dt.datetime.fromisoformat(r[0].replace("Z", "")) for r in rows]
    pres = [tc_lane._f(r[4]) if len(r) > 4 and r[4] is not None else tc_lane._f(r[3] if len(r) > 3 else None)
            for r in rows]
    return tv.ObservedTrack(times=times, lat=np.array([float(r[1]) for r in rows]),
                            lon=np.array([float(r[2]) for r in rows]), pres=np.array(pres), wind=None)


def plan_for(event) -> dict:
    """Verification plan: every 00/12Z init at which the low exists (D2) with its verifying leads."""
    tr = observed_track(event)
    plan = tv.match_samples(tr, None)
    plan.update(track=tr, start=tr.times[0])
    return plan


def verification_summary(events) -> dict:
    """meta.track_verification payload of this lane (no not-verified inits: D2 keeps the first fix)."""
    out, tot = {}, 0
    for e in events:
        if e.event_type != EVENT_TYPE or not (e.details or {}).get("track"):
            continue
        n = len(plan_for(e)["verified"])
        out[e.id] = {"verified_from": observed_track(e).times[0].isoformat(), "n_verified": n,
                     "n_not_verified": 0}
        tot += n
    return {"rule": "verification starts at the first ERA5 fix of each low; every 00/12 UTC init at which "
                    "the low exists is verified",
            "n_events": len(out), "n_inits_verified": tot, "n_inits_not_verified": 0, "events": out}


# =============================================================================
# MLC-B: peak depth and the bomb check (pure numpy)
# =============================================================================
def window_min_hpa(field2d: np.ndarray, lat: np.ndarray, lon: np.ndarray, clat: float, clon: float,
                   half_deg: float = PEAK_WINDOW_DEG) -> float:
    """Lowest value of ``field2d`` (hPa) within +/-``half_deg`` of (clat, clon); the longitude
    half-width is widened by 1/cos(lat) and wrap-aware (``lon`` may be an unwrapped frame).
    NaN when the window holds no finite value."""
    lon_half = min(90.0, half_deg / max(0.2, math.cos(math.radians(abs(clat)))))
    la = np.abs(np.asarray(lat, float) - clat) <= half_deg
    lo = np.abs(tv.wrap_deg(np.asarray(lon, float) - clon)) <= lon_half
    if not (la.any() and lo.any()):
        return float("nan")
    box = np.asarray(field2d, float)[np.ix_(la, lo)]
    return float(np.nanmin(box)) if np.isfinite(box).any() else float("nan")


def bomb_threshold_hpa(lat_deg: float) -> float:
    """24 h fall that makes a bomb at ``lat_deg`` (Sanders & Gyakum 1980, as ``mlc_track.bomb_ratio``)."""
    return BOMB_HPA * abs(math.sin(math.radians(lat_deg))) / math.sin(math.radians(BOMB_REF_LAT))


def depth_samples(track: tv.ObservedTrack, init: dt.datetime, leads: list[int], offset_h: int,
                  field: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> list[dict]:
    """Peak-depth / bomb records of one init: one dict per verifying lead (lead 0 only seeds).

    ``peak_err`` = forecast window minimum - ERA5 central pressure (hPa). For leads >= 24 h
    where the forecast window minimum exists at ``t`` and ``t - 24 h`` and the observed track has
    both fixes: ``obs_bomb`` / ``fc_bomb`` (scaled 24 h fall <= -24 hPa at 60 degrees, threshold at
    the observed latitude at ``t``). Fill-only steps (GraphCast lead 0) have no record.
    """
    init_true = init - dt.timedelta(hours=offset_h)
    fmin: dict[int, float] = {}
    out: list[dict] = []
    for j, h in enumerate(leads):
        k = track.index_of(init + dt.timedelta(hours=h))
        if k is None:
            continue
        f = field[j]
        if not np.isfinite(f).any():
            continue
        fmin[h] = window_min_hpa(f, lat, lon, float(track.lat[k]), float(track.lon[k]))
    for h in leads:
        if h == 0 or h not in fmin or not np.isfinite(fmin[h]):
            continue
        k = track.index_of(init + dt.timedelta(hours=h))
        op = track.pres[k]
        if not np.isfinite(op):
            continue
        rec = {"init_true": init_true.isoformat(), "lead_true": float(h + offset_h),
               "peak_err": float(fmin[h] - op)}
        k0 = track.index_of(init + dt.timedelta(hours=h - BOMB_WINDOW_H))
        f0 = fmin.get(h - BOMB_WINDOW_H, float("nan"))
        if h >= BOMB_WINDOW_H and k0 is not None and np.isfinite(track.pres[k0]) and np.isfinite(f0):
            thr = bomb_threshold_hpa(float(track.lat[k]))
            rec["obs_bomb"] = bool((op - track.pres[k0]) <= -thr)
            rec["fc_bomb"] = bool((fmin[h] - f0) <= -thr)
        out.append(rec)
    return out


def depth_rows(event_id: str, event_type: str, model: str, samples: list[dict]) -> pd.DataFrame:
    """SCORE_COLUMNS rows of the peak-depth / bomb records (one per (init, lead, metric))."""
    seen: set = set()
    recs: list[tuple] = []
    for s in samples:
        key = (s["init_true"], s["lead_true"])
        if key in seen:
            continue
        seen.add(key)
        init = np.datetime64(s["init_true"], "ns")
        recs.append((M_PEAK, float(s["lead_true"]), s["peak_err"], init))
        if "obs_bomb" in s:
            recs.append((M_BOMB_HIT if s["obs_bomb"] else M_BOMB_FA, float(s["lead_true"]),
                         1.0 if s["fc_bomb"] else 0.0, init))
    n = len(recs)
    df = pd.DataFrame({
        "event_id": np.full(n, event_id, dtype=object), "event_type": np.full(n, event_type, dtype=object),
        "model": np.full(n, model, dtype=object), "metric": [r[0] for r in recs],
        "lead_time_hours": np.array([r[1] for r in recs], dtype="float64"),
        "value": np.array([r[2] for r in recs], dtype="float64"),
        "init_time": np.array([r[3] for r in recs], dtype="datetime64[ns]")})
    return df[tv.SCORE_COLUMNS]


# =============================================================================
# one (model, init) job and one (event, model) pair
# =============================================================================
def score_init(track: tv.ObservedTrack, model: str, init: dt.datetime, leads: list[int],
               reader: Callable = tc_lane.default_reader) -> dict:
    """Read the MSLP window once, run the tracker (MLC-A) and the window checks (MLC-B).
    Never raises: ``status`` is ``scored`` | ``no_data`` | ``error`` (as the TC lane)."""
    import time

    rec: dict = {"init": init.isoformat(), "status": "scored", "samples": [], "depth": [], "retries": 0}
    counter: dict = {}
    t0 = time.perf_counter()
    try:
        win = tv.window_for(track, init)
        lat, lon, fld, offset = reader(model, init, leads, win, counter=counter)
        t1 = time.perf_counter()
        i0 = track.index_of(init)
        flat, flon, fp, ok, nod = tv.follow_track(
            fld, lat, lon, float(track.lat[i0]), float(track.lon[i0]), PARAMS)
        rec["samples"] = tv.verify_init(track, init, leads, offset, flat, flon, fp, ok, nod)
        rec["depth"] = depth_samples(track, init, leads, offset, fld, lat, lon)
        rec.update(offset_h=offset, read_s=round(t1 - t0, 3), track_s=round(time.perf_counter() - t1, 3))
    except (NoData, KeyError) as exc:
        rec.update(status="no_data", reason=str(exc)[:160])
    except Exception as exc:  # noqa: BLE001 - a read failure is a measured outcome, not a crash
        rec.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    rec["retries"] = counter.get("retries", 0)
    return rec


def finalize_pair(event, model: str, plan: dict, init_results: list[dict]):
    """``(DataFrame | None, status)`` of one (event, model); same statuses as the TC lane
    (``scored`` | ``empty`` | ``no_data`` | ``read_error``)."""
    n_planned = len(plan["verified"])
    if n_planned == 0:
        return None, {"status": "empty", "rows": 0,
                      "reason": "no verifiable init (no 00/12Z fix with a following fix)"}
    errs = [r for r in init_results if r["status"] == "error"]
    if errs:
        return None, {"status": "read_error", "rows": 0,
                      "reason": f"{len(errs)}/{n_planned} init reads failed after retries: {errs[0]['reason']}"}
    gaps = [r for r in init_results if r["status"] == "no_data"]
    if len(gaps) == n_planned:
        return None, {"status": "no_data", "rows": 0,
                      "reason": f"no init in the store for the {n_planned} verifiable init(s)"}
    good = [r for r in init_results if r["status"] == "scored"]
    df_a = tv.rows_for_pair(event.id, event.event_type, model, [s for r in good for s in r["samples"]])
    df_b = depth_rows(event.id, event.event_type, model, [s for r in good for s in r.get("depth", [])])
    df = pd.concat([df_a, df_b], ignore_index=True)
    if df.empty:
        return None, {"status": "empty", "rows": 0, "reason": "every verifying step was fill (no data)"}
    note = f"verified from {plan['start']:%Y-%m-%d %HZ}: {n_planned - len(gaps)}/{n_planned} inits"
    if gaps:
        note += f" ({len(gaps)} missing from the store, not counted as misses)"
    return df, {"status": "scored", "rows": int(len(df)), "reason": note,
                "n_inits": n_planned - len(gaps), "n_inits_no_data": len(gaps), "n_inits_not_verified": 0}


def score_mlc_event(event, models: list[str], reader: Callable | None = None,
                    status_out: dict | None = None):
    """Serial scorer: ``(DataFrame, {model: status})`` for one low over ``models``."""
    reader = reader or tc_lane.default_reader
    plan = plan_for(event)
    frames, status = [], {}
    for m in models:
        results = [score_init(plan["track"], m, init, leads, reader) for init, leads in plan["verified"]]
        df, st = finalize_pair(event, m, plan, results)
        status[m] = st
        if df is not None:
            frames.append(df)
    if status_out is not None:
        status_out.update(status)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=tv.SCORE_COLUMNS)
    return out, status
