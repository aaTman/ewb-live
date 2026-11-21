"""Tropical-cyclone lane scorer (notes/scoring_design.md sections 3, 6, 8.3).

For every storm published in the TC lane and every model, verify the model's
MSLP against the IBTrACS best track with the storm-following tracker of
``track_verif``: per ``(event, model, init, lead)`` one row each of
``TrackPositionError`` / ``AlongTrackError`` / ``CrossTrackError`` /
``CentralPressureError`` and a ``TrackMiss`` 0/1 row (D5; never imputed).

Owner decision 2026-11-21 (amends D2): TC verification starts at the FIRST
observed fix with wind >= 34 kt, not at the first IBTrACS fix. 00/12Z inits
earlier than that are excluded from scoring and reported as not verified
(``verification_summary``); pre-genesis misses are never counted.

This module owns the storm adapter (``details.track`` -> ObservedTrack), the
store / ECMWF readers (retry with backoff on transient S3 errors; store gaps are
"no data", not misses) and the per-(model, init) job. The worker pool lives in
``lane_pool``; ``score_tc_event`` is the serial in-process equivalent.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from typing import Callable

import numpy as np
import pandas as pd

from . import track_verif as tv

logger = logging.getLogger("ewb_live.scoring.tc")

EVENT_TYPE = "tropical_cyclone"
TRUTH_TAG = "ibtracs"                 # truth_source recorded in scores.parquet / part keys
MIN_WIND_KT = 34.0                    # D2 (amended 2026-11-21): verify from the first fix >= 34 kt
BASELINE_MODEL = "ifs_hres"           # D4: read through the ECMWF-direct path
PARAMS = tv.TC_PARAMS


# =============================================================================
# storm adapter
# =============================================================================
def _f(x) -> float:
    return float("nan") if x is None else float(x)


def observed_track(event) -> tv.ObservedTrack:
    """IBTrACS adapter: ``event.details["track"]`` rows ``[iso, lat, lon, wind_kt, pres_hPa]``."""
    rows = event.details["track"]
    times = [dt.datetime.fromisoformat(r[0].replace("Z", "")) for r in rows]
    return tv.ObservedTrack(
        times=times,
        lat=np.array([float(r[1]) for r in rows]),
        lon=np.array([float(r[2]) for r in rows]),
        pres=np.array([_f(r[4]) if len(r) > 4 else float("nan") for r in rows]),
        wind=np.array([_f(r[3]) if len(r) > 3 else float("nan") for r in rows]))


def verification_start(track: tv.ObservedTrack, min_kt: float = MIN_WIND_KT) -> dt.datetime | None:
    """Time of the first observed fix with wind >= ``min_kt`` (None if the storm never has one)."""
    if track.wind is None:
        return track.times[0]
    hit = np.where(np.isfinite(track.wind) & (track.wind >= min_kt))[0]
    return track.times[int(hit[0])] if hit.size else None


def plan_for(event) -> dict:
    """Verification plan of one storm: the track, the amended-D2 start, and the
    verified / not-verified 00/12Z inits with the leads that have a fix."""
    tr = observed_track(event)
    start = verification_start(tr)
    if start is None:                         # no fix at >= 34 kt: nothing can be verified
        every, _ = tv.verifiable_inits(tr, None)
        return {"track": tr, "start": None, "verified": [], "not_verified": every}
    plan = tv.match_samples(tr, start)
    plan.update(track=tr, start=start)
    return plan


def verification_summary(events) -> dict:
    """meta.track_verification payload: how many 00/12Z inits per storm are verified
    and how many are shown as not verified (observed wind < 34 kt at the init)."""
    out, nv_tot, nx_tot = {}, 0, 0
    for e in events:
        if e.event_type != EVENT_TYPE or not (e.details or {}).get("track"):
            continue
        p = plan_for(e)
        out[e.id] = {"verified_from": None if p["start"] is None else p["start"].isoformat(),
                     "n_verified": len(p["verified"]), "n_not_verified": len(p["not_verified"])}
        nv_tot += len(p["verified"])
        nx_tot += len(p["not_verified"])
    return {"rule": f"verification starts at the first observed fix with wind >= {MIN_WIND_KT:.0f} kt; "
                    "earlier 00/12Z inits are not verified (pre-genesis misses are not counted)",
            "min_wind_kt": MIN_WIND_KT, "n_events": len(out), "n_inits_verified": nv_tot,
            "n_inits_not_verified": nx_tot, "events": out}


# =============================================================================
# readers (S3 retry; gaps are NoData)
# =============================================================================
class NoData(FileNotFoundError):
    """The model has no init (even the nearest earlier one) for this valid window:
    a store gap, reported as ``no_data``, never counted as a track miss."""


_TRANSIENT = ("StorageError", "InternalError", "SlowDown", "Timeout", "timed out", "Connection",
              "Throttl", "ServiceUnavailable", "temporarily", "Too many open files", "reset by peer",
              "RequestTimeout", "BadDigest", "IncompleteRead")
_TRANSIENT_STATUS = re.compile(r"\b(?:HTTP|status(?: code)?|code)[ :=]*5\d\d\b", re.I)   # a 5xx, not any "500" in a message


def is_transient(exc: BaseException) -> bool:
    """Transient S3 / network trouble worth a retry (never a missing init)."""
    if isinstance(exc, (KeyError, NoData)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    text = f"{type(exc).__name__} {exc}"
    return any(t in text for t in _TRANSIENT) or bool(_TRANSIENT_STATUS.search(text))


def retry_transient(fn: Callable, *, tries: int = 4, base_s: float = 2.0,
                    sleep: Callable[[float], None] = time.sleep, counter: dict | None = None):
    """``fn()`` with up to ``tries`` attempts and linear backoff ``base_s * attempt`` on
    transient errors (measured ~4 % of store reads fail once; the pilot's 3-try / 2 s
    retry cleared them). Non-transient errors and the last failure propagate."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == tries or not is_transient(exc):
                raise
            if counter is not None:
                counter["retries"] = counter.get("retries", 0) + 1
            logger.warning("transient read error (attempt %d/%d): %s: %s",
                           attempt, tries, type(exc).__name__, str(exc)[:120])
            sleep(base_s * attempt)


def _to_hpa(a: np.ndarray) -> np.ndarray:
    a = np.where(np.abs(a) < 9e35, a, np.nan)
    return a / 100.0 if np.nanmedian(a) > 5000.0 else a


def read_store_msl(model: str, init: dt.datetime, leads: list[int], win, *,
                   counter: dict | None = None, sleep: Callable[[float], None] = time.sleep):
    """``(lat, lon, field[lead, lat, lon] hPa, offset_h)`` from the icechunk store via
    ``open_init`` (never ``open_model``: ~8 GB RSS each). Falls back to the nearest
    EARLIER init (the pipeline rule, 12 h): the leads shift by the offset and keep the
    same valid times; leads beyond that init's 240 h horizon stay NaN ("no data").
    Raises :class:`NoData` when neither init is in the store."""
    from ..sources import registry
    from ..sources.aiwp import _code
    from ..stores import mlwp_icechunk as store

    code = _code(registry.models()[model])
    lat0, lat1, lon0, width = win
    last: Exception | None = None
    for offset in (0, 12):
        ini = init - dt.timedelta(hours=offset)

        def attempt(ini=ini, offset=offset):
            ds = store.open_init(code, ini)
            da = ds["msl"].sel(init_time=np.datetime64(ini, "ns"))
            have = [j for j, h in enumerate(leads) if h + offset <= tv.MAX_LEAD_H]
            if not have:
                raise KeyError("no lead within the 240 h horizon of the older init")
            da = da.sel(lead_time=np.array([np.timedelta64(leads[j] + offset, "h") for j in have]))
            li = np.where((da.latitude.values >= lat0) & (da.latitude.values <= lat1))[0]
            oi, lons = tv.contiguous_lons(da.longitude.values, lon0, width)
            da = da.isel(latitude=li, longitude=oi)
            part = np.asarray(da.compute().values, dtype=np.float32)
            lats = np.asarray(da.latitude.values, float)
            return have, part, lats, lons

        try:
            have, part, lats, lons = retry_transient(attempt, counter=counter, sleep=sleep)
        except KeyError as exc:
            last = exc
            continue
        arr = np.full((len(leads),) + part.shape[1:], np.nan, np.float32)
        arr[have] = part
        order = np.argsort(lats)
        return lats[order], lons, _to_hpa(arr[:, order, :]), offset
    raise NoData(f"{model} init {init:%Y-%m-%d %H}Z not in store (also not -12h): {last}")


def read_ifs_msl(init: dt.datetime, leads: list[int], win, *,
                 counter: dict | None = None, sleep: Callable[[float], None] = time.sleep):
    """Same contract for the baseline ``ifs_hres`` through the existing ECMWF-direct
    backend (the one non-virtual read, D4)."""
    from ..sources import ecmwf, registry

    spec = registry.models()[BASELINE_MODEL]
    lat0, lat1, lon0, width = win
    lo_a = float(tv.wrap_deg(lon0))
    lo_b = float(tv.wrap_deg(lon0 + width)) if width < 360 else lo_a - 1e-6

    def attempt():
        try:
            ds = ecmwf.open_forecast(spec, init, ["air_pressure_at_mean_sea_level"], leads,
                                     (lat0, lat1, lo_a, lo_b))
        except ValueError as exc:
            if "no leads available" in str(exc):
                raise NoData(f"{BASELINE_MODEL} init {init:%Y-%m-%d %H}Z not published") from exc
            raise
        return ds["air_pressure_at_mean_sea_level"].isel(init_time=0).load()

    da = retry_transient(attempt, counter=counter, sleep=sleep)
    got = [int(x / np.timedelta64(1, "h")) for x in da.lead_time.values]
    oi, lons = tv.contiguous_lons(da.longitude.values, lon0, width)
    arr = np.asarray(da.values, dtype=np.float32)[:, :, oi]
    lats = np.asarray(da.latitude.values, float)
    order = np.argsort(lats)
    full = np.full((len(leads), lats.size, lons.size), np.nan, np.float32)
    pos = {h: i for i, h in enumerate(leads)}
    for k, h in enumerate(got):
        if h in pos:
            full[pos[h]] = arr[k][order]
    return lats[order], lons, _to_hpa(full), 0


def default_reader(model: str, init: dt.datetime, leads: list[int], win, *,
                   counter: dict | None = None):
    if model == BASELINE_MODEL:
        return read_ifs_msl(init, leads, win, counter=counter)
    return read_store_msl(model, init, leads, win, counter=counter)


# =============================================================================
# one (model, init) job and one (event, model) pair
# =============================================================================
def score_init(track: tv.ObservedTrack, model: str, init: dt.datetime, leads: list[int],
               reader: Callable = default_reader) -> dict:
    """Read the MSLP window for one init, run the tracker, verify. Never raises:
    ``status`` is ``scored`` | ``no_data`` (store gap) | ``error`` (read still failing
    after the retries; the pair is retried by the next run)."""
    rec: dict = {"init": init.isoformat(), "status": "scored", "samples": [], "retries": 0}
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
        # the followed track itself (true init / true leads), for the EWB landfall classes
        rec["fc_track"] = {"init_true": (init - dt.timedelta(hours=offset)).isoformat(),
                           "lead_true": [float(h + offset) for h in leads],
                           "lat": [float(x) if o else float("nan") for x, o in zip(flat, ok)],
                           "lon": [float(x) if o else float("nan") for x, o in zip(flon, ok)],
                           "p": [float(x) if o else float("nan") for x, o in zip(fp, ok)]}
        rec.update(offset_h=offset, read_s=round(t1 - t0, 3), track_s=round(time.perf_counter() - t1, 3))
    except (NoData, KeyError) as exc:          # a store gap; any other FileNotFoundError is a read error
        rec.update(status="no_data", reason=str(exc)[:160])
    except Exception as exc:  # noqa: BLE001 - a read failure is a measured outcome, not a crash
        rec.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    rec["retries"] = counter.get("retries", 0)
    return rec


def finalize_pair(event, model: str, plan: dict, init_results: list[dict]):
    """``(DataFrame | None, status)`` of one (event, model) from its per-init results.

    status.status: ``scored`` | ``empty`` (nothing verifiable) | ``no_data`` (every init
    missing from the store) | ``read_error`` (an init still failed after its retries: no
    rows are published for the pair, so it is retried and blocks ``full_run_complete``).
    """
    n_planned = len(plan["verified"])
    n_nv = len(plan["not_verified"])
    since = "" if plan["start"] is None else f" from {plan['start']:%Y-%m-%d %HZ}"
    skipped = f"; {n_nv} pre-34-kt init{'s' if n_nv != 1 else ''} not verified" if n_nv else ""
    if n_planned == 0:
        return None, {"status": "empty", "rows": 0, "reason":
                      "no verifiable init (no 00/12Z fix at >= 34 kt with a following fix)" + skipped}
    errs = [r for r in init_results if r["status"] == "error"]
    if errs:
        return None, {"status": "read_error", "rows": 0,
                      "reason": f"{len(errs)}/{n_planned} init reads failed after retries: {errs[0]['reason']}"}
    gaps = [r for r in init_results if r["status"] == "no_data"]
    if len(gaps) == n_planned:
        return None, {"status": "no_data", "rows": 0,
                      "reason": f"no init in the store for the {n_planned} verifiable init(s){since}"}
    samples = [s for r in init_results if r["status"] == "scored" for s in r["samples"]]
    df = tv.rows_for_pair(event.id, event.event_type, model, samples)
    if df.empty:
        return None, {"status": "empty", "rows": 0, "reason": "every verifying step was fill (no data)" + skipped}
    note = f"verified{since}: {n_planned - len(gaps)}/{n_planned} inits"
    if gaps:
        note += f" ({len(gaps)} missing from the store, not counted as misses)"
    lf_info = _landfall_info(event, model, plan["track"], init_results)
    if lf_info["status"] == "error":     # EWB side failed: retried next run, blocks full_run_complete
        return None, {"status": "read_error", "rows": 0, "reason": "landfall classes failed: " + lf_info["reason"]}
    lf_df = lf_info.pop("df")
    if len(lf_df):
        df = pd.concat([df, lf_df], ignore_index=True)
    return df, {"status": "scored", "rows": int(len(df)), "reason": note + skipped,
                "n_inits": n_planned - len(gaps), "n_inits_no_data": len(gaps),
                "n_inits_not_verified": n_nv, "landfall": lf_info}


def _landfall_info(event, model: str, track, init_results: list[dict]) -> dict:
    """Landfall rows of one pair (``df``) + a small status dict; ``status == "error"`` when the
    EWB side raised (the pair is then a ``read_error``, rescored next run)."""
    from . import landfall

    fc_tracks = [r["fc_track"] for r in init_results if r["status"] == "scored" and r.get("fc_track")]
    try:
        df, info = landfall.landfall_rows(event, model, track, fc_tracks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("landfall (%s, %s) failed: %s: %s", event.id, model, type(exc).__name__, str(exc)[:200])
        return {"df": pd.DataFrame(columns=tv.SCORE_COLUMNS), "status": "error",
                "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {"df": df, **info}


def score_tc_event(event, models: list[str], reader: Callable | None = None,
                   status_out: dict | None = None):
    """Serial scorer: ``(DataFrame, {model: status})`` for one storm over ``models``.
    The production path is ``lane_pool`` (same functions, init-level parallelism)."""
    reader = reader or default_reader
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


def lane_workers_default() -> int:
    return int(os.environ.get("EWB_LIVE_SCORING_WORKERS", "12"))
