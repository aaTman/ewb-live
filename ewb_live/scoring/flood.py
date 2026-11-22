"""Flood lane scorer (notes/scoring_design.md sections 5, 6, 8.6; decision D6).

What is verified, and against what
----------------------------------
* Truth: PARDIG-NRT daily totals (``data/pardig/daily``, mm/day, 0.25 degree ERA5 lattice).
  PARDIG is an **AI-estimated, provisional** precipitation product and the flood events
  are *defined* by it, so every score is "vs AI-estimated precipitation", conditional on
  events chosen where PARDIG exceeded the threshold. It is not "observed rain".
* Forecast: the MLWP store's ``apcp`` (6-h accumulated precipitation, m), which only the
  GraphCast family carries (``graphcast``, ``graphcast_gfs``). Every other model is a
  documented ``unsupported`` pair (no precipitation variable in the store).
* Threshold (the event's own definition, ``events/flood.py``): ``max(ERA5 wet-day p95 for
  that day of year, 20 mm)`` per cell for the daily lane, applied to forecast AND truth.
  Cells whose climatology threshold is undefined (NaN, hyper-arid) are excluded, never
  filled.
* Domain: land cells of the event bbox padded 2 degrees; days ``start-2 .. last+2`` so false
  alarms outside the event days are counted (``DAY_PAD``).

Metrics (one row per ``(event, model, init, verified day)``; D5 granularity)
----------------------------------------------------------------------------
``CriticalSuccessIndex`` / ``ProbabilityOfDetection`` / ``FalseAlarmRatio`` from the
cell-day contingency of the daily exceedance; ``FractionsSkillScore`` (3x3 cells) and
``FractionsSkillScore5`` (5x5) on the same exceedance fields; ``PrecipAreaBias`` (mm, signed,
footprint-mean of forecast minus truth of the day's total) and ``PeakPrecipError`` (mm,
signed, footprint-max forecast minus footprint-max truth). The *footprint* is the event's
own union exceedance mask (daily lane OR trailing-72 h lane, exactly the detection rule)
over the event days. A day without a defined denominator (e.g. no exceedance anywhere in
forecast or truth) emits no row for that metric: a correct rejection carries no score.

``lead_time_hours`` is the end of the verified UTC day measured from the (true) init: 24,
48, ... 240 for 00Z inits. The 6-h bins of day ``D`` are the ones valid at 06, 12, 18 and
24 UTC; a day whose bins are not all present (the IFS-initialised GraphCast has fill values
in its first 6 h, ``9.97e36``) is not emitted. Small negative values (~15-30 % of cells,
|v| < 0.4 mm) are clipped at 0 before summing.

Known limitations (also in the About text): one model family only (two members), a
truth that is itself a model, the 3-day lane is not scored separately (all 40 current
events have the 72 h lane as lane of record, but the table is the daily lane), CSI/POD are
conditional on a truth exceedance existing (selection bias), and ``ifs_hres`` has no
baseline here (ECMWF ``tp`` is not wired), so the table carries no skill colour.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import os
import time
from typing import Callable

import numpy as np
import pandas as pd

from . import tc as tc_lane
from . import track_verif as tv

logger = logging.getLogger("ewb_live.scoring.flood")

EVENT_TYPE = "flood"
TRUTH_TAG = "pardig"
QUANTILE = 0.95
FLOOR_1D_MM = 20.0
FLOOR_3D_MM = 50.0
PAD_DEG = 2.0                 # verification domain: bbox + 2 degrees
DAY_PAD = 2                   # verified days: start-2 .. last event day + 2
MAX_LEAD_DAYS = 10            # leads 24 .. 240 h
FILL_ABS = 1e30               # store fill values are 9.97e36
FALLBACK_OFFSETS_H = (0,)     # nearest-earlier-init rescue disabled: leads stay whole days (see read_store_apcp)
GRID = 0.25
N_LAT, N_LON = 721, 1440
#: store families that carry ``apcp`` (registry ``AIWP_MODELS`` base codes)
PRECIP_FAMILIES = ("GRAP_v100",)

M_CSI, M_POD, M_FAR = "CriticalSuccessIndex", "ProbabilityOfDetection", "FalseAlarmRatio"
M_FSS3, M_FSS5 = "FractionsSkillScore", "FractionsSkillScore5"
M_BIAS, M_PEAK = "PrecipAreaBias", "PeakPrecipError"
FLOOD_METRICS = (M_CSI, M_POD, M_FAR, M_FSS3, M_FSS5, M_BIAS, M_PEAK)
SIGNED_METRICS = (M_BIAS, M_PEAK)
# <= 120 chars: pipeline.public_reason truncates published pair_status reasons there
UNSUPPORTED_REASON = "no precipitation in the MLWP store: only the GraphCast family carries it (GraphCast only)"


# =============================================================================
# pure metric kernels (numpy, no I/O)
# =============================================================================
def contingency(fc_exc: np.ndarray, obs_exc: np.ndarray, valid: np.ndarray) -> tuple[int, int, int, int]:
    """``(hits, misses, false_alarms, correct_negatives)`` over the valid cells."""
    f, o = fc_exc & valid, obs_exc & valid
    hits = int((f & o).sum())
    miss = int((~f & o & valid).sum())
    fa = int((f & ~o).sum())
    cn = int((valid & ~f & ~o).sum())
    return hits, miss, fa, cn


def scores_from_counts(hits: int, miss: int, fa: int) -> dict[str, float]:
    """CSI / POD / FAR; a metric without a defined denominator is left out."""
    out: dict[str, float] = {}
    if hits + miss + fa > 0:
        out[M_CSI] = hits / (hits + miss + fa)
    if hits + miss > 0:
        out[M_POD] = hits / (hits + miss)
    if hits + fa > 0:
        out[M_FAR] = fa / (hits + fa)
    return out


def _box_sum(a: np.ndarray, n: int) -> np.ndarray:
    from scipy.ndimage import uniform_filter

    return uniform_filter(a.astype("float64"), size=n, mode="constant", cval=0.0) * (n * n)


def fractions_skill_score(fc_exc: np.ndarray, obs_exc: np.ndarray, valid: np.ndarray, n: int) -> float | None:
    """Fractions Skill Score (Roberts & Lean 2008) on ``n`` x ``n`` cell neighbourhoods:
    ``1 - sum((Pf - Po)^2) / (sum(Pf^2) + sum(Po^2))`` over the valid cells, where ``P`` is the
    fraction of valid neighbourhood cells in exceedance. Edge / invalid cells shrink the window
    (their count is the normaliser), they are never counted as dry. ``None`` when neither
    field exceeds anywhere (denominator 0)."""
    v = valid.astype("float64")
    cnt = _box_sum(v, n)
    ok = valid & (cnt > 0)
    if not ok.any():
        return None
    pf = np.where(ok, _box_sum((fc_exc & valid), n) / np.where(cnt > 0, cnt, 1.0), 0.0)
    po = np.where(ok, _box_sum((obs_exc & valid), n) / np.where(cnt > 0, cnt, 1.0), 0.0)
    den = float((pf ** 2).sum() + (po ** 2).sum())
    if den <= 0.0:
        return None
    return 1.0 - float(((pf - po) ** 2).sum()) / den


def day_metrics(fc: np.ndarray, truth: np.ndarray, thr: np.ndarray, land: np.ndarray,
                footprint: np.ndarray) -> dict[str, float]:
    """All flood metrics of one verified day on the domain lattice.

    ``fc`` / ``truth`` mm of the day, ``thr`` the effective per-cell threshold (NaN =
    undefined), ``land`` bool, ``footprint`` bool (the event's union exceedance mask).
    Cells that are NaN in forecast, truth or threshold, or off land, are excluded."""
    fin = np.isfinite(fc) & np.isfinite(truth) & np.isfinite(thr)
    valid = fin & land
    if not valid.any():
        return {}
    with np.errstate(invalid="ignore"):
        fe, oe = fc >= thr, truth >= thr
    h, m, fa, _cn = contingency(fe, oe, valid)
    out = scores_from_counts(h, m, fa)
    for name, n in ((M_FSS3, 3), (M_FSS5, 5)):
        v = fractions_skill_score(fe, oe, valid, n)
        if v is not None:
            out[name] = v
    fp = footprint & fin
    if fp.any():
        out[M_BIAS] = float(np.mean(fc[fp] - truth[fp]))
        out[M_PEAK] = float(np.max(fc[fp]) - np.max(truth[fp]))
    return out


def daily_totals_mm(bins: np.ndarray) -> np.ndarray | None:
    """Daily total (mm) of four 6-h ``apcp`` bins in metres, shape ``(4, lat, lon)``.

    Fill values (|v| > 1e30) become NaN, small negatives are clipped at 0, and the sum is NaN
    where any bin is NaN. ``None`` when any bin is NaN everywhere (a day the forecast cannot
    supply, e.g. the IFS-initialised GraphCast's filled first 6 h)."""
    b = np.where(np.abs(bins) < FILL_ABS, bins, np.nan).astype("float64")
    if any(not np.isfinite(b[k]).any() for k in range(b.shape[0])):
        return None
    return np.clip(b, 0.0, None).sum(axis=0) * 1000.0


def bin_leads_of_day(init: dt.datetime, day: dt.date) -> list[int]:
    """Leads (h) of the four 6-h bins of UTC ``day`` (valid 06, 12, 18, 24 UTC) for an init."""
    d0 = int((dt.datetime.combine(day, dt.time()) - init) / dt.timedelta(hours=1))
    return [d0 + 6, d0 + 12, d0 + 18, d0 + 24]


# =============================================================================
# truth bundle (picklable, small) and the verification plan
# =============================================================================
@dataclasses.dataclass
class FloodTruth:
    """Everything a worker needs about one event, as small numpy arrays on the domain lattice.

    ``lat`` ascending, ``lon`` in one continuous (possibly > 180) frame; ``days`` the verified
    UTC days; ``truth`` / ``thr`` are ``(day, lat, lon)`` mm; ``present[d]`` False = the PARDIG
    day is missing (not scored)."""
    lat: np.ndarray
    lon: np.ndarray
    days: list
    present: np.ndarray
    truth: np.ndarray
    thr: np.ndarray
    land: np.ndarray
    footprint: np.ndarray
    window: tuple            # (lat0, lat1, lon_start, width) for the store reader
    event_days: list

    # ObservedTrack-compatible surface used by lane_pool / tc helpers: none needed
    times = ()


def event_days(event) -> list[dt.date]:
    """UTC days the event covers (``event.end`` is the exclusive midnight after the last day)."""
    d0 = event.start.date()
    last = (event.end - dt.timedelta(seconds=1)).date()
    return [d0 + dt.timedelta(days=i) for i in range((last - d0).days + 1)]


def verification_days(event) -> list[dt.date]:
    ed = event_days(event)
    return [ed[0] + dt.timedelta(days=i) for i in range(-DAY_PAD, len(ed) + DAY_PAD)]


def domain_for(bbox, pad: float = PAD_DEG):
    """``(lats asc, lons continuous, window)`` of the padded bbox on the 0.25 degree lattice.
    ``lon_min > lon_max`` is an antimeridian-wrapped box (eastward arc)."""
    la0, la1, lo0, lo1 = (float(x) for x in bbox)
    la0, la1 = max(-90.0, la0 - pad), min(90.0, la1 + pad)
    width = ((lo1 - lo0) % 360.0) + 2 * pad
    start = lo0 - pad
    if width >= 360.0:
        width, start = 359.75, 0.0
    i0, i1 = int(np.floor((90.0 - la1) / GRID)), int(np.ceil((90.0 - la0) / GRID))
    lats = 90.0 - GRID * np.arange(i0, i1 + 1)[::-1]            # ascending
    j0 = int(np.floor(start / GRID))
    lons = (j0 + np.arange(int(np.ceil(width / GRID)) + 1)) * GRID
    return lats, lons, (float(lats[0]), float(lats[-1]), float(lons[0]), float(lons[-1] - lons[0]))


def _lat_idx(lats):
    return np.rint((90.0 - np.asarray(lats, float)) / GRID).astype(int)


def _pardig_lon_idx(lons):
    return np.rint((((np.asarray(lons, float) + 180.0) % 360.0)) / GRID).astype(int) % N_LON


def _store_lon_idx(lons):
    return np.rint((np.asarray(lons, float) % 360.0) / GRID).astype(int) % N_LON


def _eff_thr(raw: np.ndarray, floor: float) -> np.ndarray:
    """Per-cell threshold ``max(clim p95, floor)``; an undefined (NaN) climatology stays NaN."""
    return np.where(np.isfinite(raw), np.maximum(raw, floor), np.nan).astype("float32")


def build_truth(event, *, pardig_root=None, thresholds: Callable | None = None,
                land: np.ndarray | None = None) -> FloodTruth:
    """Load PARDIG for the event window and build the :class:`FloodTruth` bundle.

    ``thresholds(lats, lons, dates, accum_days) -> (day, lat, lon)`` mm (the ERA5 p95
    climatology) and ``land`` can be injected (tests). Missing PARDIG days stay NaN planes
    and are flagged ``present = False``."""
    from ..sources import pardig

    vdays = verification_days(event)
    edays = event_days(event)
    first, last = vdays[0] - dt.timedelta(days=2), vdays[-1]       # 2 more days for the 72 h lane
    all_days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    lats, lons, window = domain_for(event.bbox)
    da = pardig.load_pardig_daily(first, last, root=pardig_root)
    if da.shape[-2:] != (N_LAT, N_LON):
        raise ValueError(f"PARDIG lattice {da.shape[-2:]} is not the 0.25 degree ERA5 lattice")
    full = np.asarray(da.values, "float32")                          # (day, 721, 1440)
    sub = full[:, _lat_idx(lats)][:, :, _pardig_lon_idx(lons)]
    present = np.array([bool(np.isfinite(sub[i]).mean() >= 0.5) for i in range(len(all_days))])
    if thresholds is None:
        from ..climatology import precip_clim

        def thresholds(lat_, lon_, dates, w):           # noqa: E306
            return precip_clim.precip_threshold_on(lat_, ((lon_ + 180.0) % 360.0) - 180.0, dates,
                                                   QUANTILE, w).values
    if land is None:
        from ..events import flood

        land = flood.land_mask(lats, ((lons + 180.0) % 360.0) - 180.0)
    thr1 = _eff_thr(thresholds(lats, lons, all_days, 1), FLOOR_1D_MM)
    thr3 = _eff_thr(thresholds(lats, lons, all_days, 3), FLOOR_3D_MM)
    # trailing 72 h truth accumulation; a gap poisons the windows that contain it (NaN)
    acc3 = np.full_like(sub, np.nan)
    for i in range(2, len(all_days)):
        acc3[i] = sub[i - 2] + sub[i - 1] + sub[i]
    with np.errstate(invalid="ignore"):
        e1 = (sub >= thr1) & np.isfinite(thr1)
        e3 = (acc3 >= thr3) & np.isfinite(thr3)
    ix = {d: i for i, d in enumerate(all_days)}
    fp = np.zeros(sub.shape[1:], bool)
    for d in edays:
        fp |= (e1[ix[d]] | e3[ix[d]])
    fp &= land
    sel = [ix[d] for d in vdays]
    return FloodTruth(lat=lats, lon=lons, days=vdays, present=present[sel], truth=sub[sel], thr=thr1[sel],
                      land=land, footprint=fp, window=window, event_days=edays)


def plan_for(event, **kw) -> dict:
    """Verification plan of one event: ``verified = [(init, leads)]`` (00Z inits, the bin
    leads that init contributes), ``not_verified`` = verified days with no PARDIG truth."""
    tr = build_truth(event, **kw)
    need: dict[dt.datetime, set] = {}
    for i, day in enumerate(tr.days):
        if not tr.present[i]:
            continue
        d0 = dt.datetime.combine(day, dt.time())
        for k in range(1, MAX_LEAD_DAYS + 1):
            init = d0 - dt.timedelta(days=k - 1)
            need.setdefault(init, set()).update(bin_leads_of_day(init, day))
    verified = [(i, sorted(need[i])) for i in sorted(need)]
    missing = [d for d, ok in zip(tr.days, tr.present) if not ok]
    return {"track": tr, "start": dt.datetime.combine(tr.days[0], dt.time()), "verified": verified,
            "not_verified": missing}


# =============================================================================
# store reader (apcp; fill -> NaN)
# =============================================================================
def read_store_apcp(model: str, init: dt.datetime, leads: list[int], win, *,
                    counter: dict | None = None, sleep=time.sleep):
    """``(lat asc, lon, apcp[lead, lat, lon] in m with fill as NaN, offset_h)`` for the
    domain window, from the icechunk store via ``open_init``. The nearest-earlier-init
    fallback (``FALLBACK_OFFSETS_H``) is OFF for flood: a rescued 12Z init would emit rows
    at leads 36, 60, ... that the dashboard folds into the 1 d / 3 d columns, which would
    show 36 h skill under a 24 h label. A missing 00Z init is a ``no_data`` init instead.
    Raises :class:`tc.NoData` when the init is not in the store."""
    from ..sources import registry
    from ..sources.aiwp import _code
    from ..stores import mlwp_icechunk as store

    code = _code(registry.models()[model])
    lat0, lat1, lon0, width = win
    last: Exception | None = None
    for offset in FALLBACK_OFFSETS_H:
        ini = init - dt.timedelta(hours=offset)

        def attempt(ini=ini, offset=offset):
            ds = store.open_init(code, ini)
            da = ds["apcp"].sel(init_time=np.datetime64(ini, "ns"))
            have = [j for j, h in enumerate(leads) if h + offset <= tv.MAX_LEAD_H]
            if not have:
                raise KeyError("no lead within the 240 h horizon of the older init")
            da = da.sel(lead_time=np.array([np.timedelta64(leads[j] + offset, "h") for j in have]))
            li = np.where((da.latitude.values >= lat0 - 1e-6) & (da.latitude.values <= lat1 + 1e-6))[0]
            oi, lons = tv.contiguous_lons(da.longitude.values, lon0, width + 1e-6)
            part = np.asarray(da.isel(latitude=li, longitude=oi).compute().values, dtype=np.float32)
            return have, part, np.asarray(da.latitude.values, float)[li], lons

        try:
            have, part, lats, lons = tc_lane.retry_transient(attempt, counter=counter, sleep=sleep)
        except KeyError as exc:
            last = exc
            continue
        arr = np.full((len(leads),) + part.shape[1:], np.nan, np.float32)
        arr[have] = part
        order = np.argsort(lats)
        return lats[order], lons, arr[:, order, :], offset
    raise tc_lane.NoData(f"{model} init {init:%Y-%m-%d %H}Z not in store: {last}")


def default_reader(model: str, init: dt.datetime, leads: list[int], win, *, counter: dict | None = None):
    return read_store_apcp(model, init, leads, win, counter=counter)


# =============================================================================
# one (model, init) job and one (event, model) pair
# =============================================================================
def _align(tr: FloodTruth, lat, lon, fld):
    """Index the read field onto the truth domain lattice (they share the 0.25 degree grid)."""
    la = {int(round(v / GRID)): k for k, v in enumerate(lat)}
    lo = {int(round(v / GRID)): k for k, v in enumerate(lon)}
    try:
        yi = [la[int(round(v / GRID))] for v in tr.lat]
        xi = [lo[int(round(v / GRID))] for v in tr.lon]
    except KeyError as exc:
        raise ValueError(f"store window does not cover the domain cell {exc}") from exc
    return fld[:, yi][:, :, xi]


def score_init(track: FloodTruth, model: str, init: dt.datetime, leads: list[int],
               reader: Callable = default_reader) -> dict:
    """Read the apcp window for one init and verify every day it can supply. Never raises:
    ``status`` is ``scored`` | ``no_data`` (store gap) | ``error`` (read still failing)."""
    rec: dict = {"init": init.isoformat(), "status": "scored", "samples": [], "retries": 0, "n_days_fill": 0}
    counter: dict = {}
    t0 = time.perf_counter()
    try:
        lat, lon, fld, offset = reader(model, init, leads, track.window, counter=counter)
        t1 = time.perf_counter()
        fld = _align(track, lat, lon, fld)
        pos = {h: i for i, h in enumerate(leads)}
        init_true = init - dt.timedelta(hours=offset)
        for i, day in enumerate(track.days):
            if not track.present[i]:
                continue
            bl = bin_leads_of_day(init, day)
            if any(b not in pos for b in bl):
                continue
            fc = daily_totals_mm(fld[[pos[b] for b in bl]])
            if fc is None:
                rec["n_days_fill"] += 1
                continue
            met = day_metrics(fc, track.truth[i], track.thr[i], track.land, track.footprint)
            if met:
                rec["samples"].append({"init_true": init_true.isoformat(), "lead_true": bl[-1] + offset,
                                       "day": day.isoformat(), "metrics": met})
        rec.update(offset_h=offset, read_s=round(t1 - t0, 3), track_s=round(time.perf_counter() - t1, 3))
    except (tc_lane.NoData, KeyError) as exc:
        rec.update(status="no_data", reason=str(exc)[:160])
    except Exception as exc:  # noqa: BLE001 - a read failure is a measured outcome, not a crash
        rec.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    rec["retries"] = counter.get("retries", 0)
    return rec


def rows_for_pair(event_id: str, event_type: str, model: str, samples: list[dict]) -> pd.DataFrame:
    """SCORE_COLUMNS rows (one per metric per (init, lead)); duplicates are emitted once."""
    seen: set = set()
    recs: list[tuple] = []
    for s in samples:
        key = (s["init_true"], s["lead_true"])
        if key in seen:
            continue
        seen.add(key)
        init = np.datetime64(s["init_true"], "ns")
        for name, val in s["metrics"].items():
            recs.append((name, float(s["lead_true"]), float(val), init))
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
    return df[tv.SCORE_COLUMNS]


def finalize_pair(event, model: str, plan: dict, init_results: list[dict]):
    """``(DataFrame | None, status)`` of one (event, model); same status vocabulary as TC:
    ``scored`` | ``empty`` | ``no_data`` | ``read_error`` (no rows, retried next run)."""
    n_planned = len(plan["verified"])
    nv = plan["not_verified"]
    skipped = (f"; {len(nv)} day{'s' if len(nv) != 1 else ''} without PARDIG truth not scored" if nv else "")
    if n_planned == 0:
        return None, {"status": "empty", "rows": 0, "reason": "no verified day has PARDIG truth" + skipped}
    errs = [r for r in init_results if r["status"] == "error"]
    if errs:
        return None, {"status": "read_error", "rows": 0,
                      "reason": f"{len(errs)}/{n_planned} init reads failed after retries: {errs[0]['reason']}"}
    gaps = [r for r in init_results if r["status"] == "no_data"]
    if len(gaps) == n_planned:
        return None, {"status": "no_data", "rows": 0, "reason": f"no init in the store for the {n_planned} planned init(s)"}
    samples = [s for r in init_results if r["status"] == "scored" for s in r["samples"]]
    df = rows_for_pair(event.id, event.event_type, model, samples)
    fill_days = sum(r.get("n_days_fill", 0) for r in init_results)
    if df.empty:
        return None, {"status": "empty", "rows": 0,
                      "reason": "no verifying day had forecast precipitation and a defined score" + skipped}
    note = f"verified {n_planned - len(gaps)}/{n_planned} inits"
    if gaps:
        note += f" ({len(gaps)} missing from the store)"
    if fill_days:
        note += f"; {fill_days} init-days with fill precipitation not scored"
    return df, {"status": "scored", "rows": int(len(df)), "reason": note + skipped,
                "n_inits": n_planned - len(gaps), "n_inits_no_data": len(gaps)}


def score_flood_event(event, models: list[str], reader: Callable | None = None,
                      status_out: dict | None = None, **truth_kw):
    """Serial scorer: ``(DataFrame, {model: status})`` for one event over ``models``."""
    reader = reader or default_reader
    plan = plan_for(event, **truth_kw)
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


def flood_models(models: list) -> tuple[list, dict]:
    """The flood lane scores the models whose store family carries ``apcp`` (GraphCast, both
    initialisations); every other registered model is a documented ``unsupported`` pair."""
    from ..sources import registry

    specs = registry.models()
    ok = [m for m in models if m in specs and specs[m].backend == "aiwp"
          and registry.AIWP_MODELS.get(m.removesuffix("_gfs")) in PRECIP_FAMILIES]
    return ok, {m: UNSUPPORTED_REASON for m in models if m not in ok}


def lane_workers_default() -> int:
    return int(os.environ.get("EWB_LIVE_SCORING_WORKERS", "12"))
