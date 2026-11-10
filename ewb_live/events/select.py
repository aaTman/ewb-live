"""Curation layer: rank raw scans by severity and cap per type.

The upstream data_prep global scan is a *detector*: over a 90-day global
window it finds every cell-eligible blob (hundreds of AR wisps, >100 heat
waves). EWB's shipped case set (src/extremeweatherbench/data/events.yaml) is
the curated equivalent — tens of events, long durations, big bboxes,
land-falling, chosen for impact. This module bridges detector -> showcase,
using only EWB's own ingredients:

  * anomaly intensity from EWB's climatology file
    (gs://extremeweatherbench/.../surface_air_temperature_1990_2019_climatology.zarr
    via defaults.get_climatology), the same quantiles detection uses;
  * upstream detection outputs (peak area, consecutive days, AR landfall days
    / peak land pixels / peak IVT) from Event.details.

Selection rules (documented in notes/ewb.md):

heat_wave   keep: max_consecutive_days >= 3 (upstream already enforces)
            severity = anomaly_K x peak_area_km2 x max_consecutive_days
            anomaly_K = peak daily-mean t2m - mean q0.85 climatology over the
            event bbox across its days/synoptic hours
freeze      symmetric with q0.15: anomaly_K = mean q0.15 - min daily-mean t2m
atmospheric_river
            keep: landfall_days >= 1 (owner 2026-11-09; was 3) and peak IVT >= 600 kg/m/s
            severity = landfall_days x peak_land_pixels x (peak_IVT - 400)

PRODUCT RULE (owner, 2026-11-06, revised same day): NO two events may
overlap in BOTH time and space in the final selection, and mega-merges of
distinct events are forbidden. Curation therefore runs three phases:

1. merge_same_type: only FRAGMENTS OF THE SAME EVENT merge — same-type
   events that are temporally contiguous (gap <= 1 day) AND have bbox
   IoU >= 0.3, grown greedily from the highest-severity seed and capped
   at the upstream events.yaml per-type p90 duration (AR 9 d, heat_wave
   and freeze 16 d). A merged bbox must stay within ~40x60 deg.
2. resolve conflicts: any two surviving events (same or different type)
   that still overlap in time+space — e.g. a low-IoU neighbour of a
   merged cluster — are decided by WITHIN-TYPE SEVERITY PERCENTILE: the
   higher percentile is kept, the other dropped.
3. greedy order: candidates are consumed in global percentile order up
   to top_n per type, so a drop frees the slot for the next candidate
   under the same rules (refill).
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 15

# AR extra floors (see module docstring)
AR_MIN_LANDFALL_DAYS = 1  # owner 2026-11-09: floor relaxed 3 -> 1 (landfall funnel review)
AR_MIN_PEAK_IVT = 600.0
AR_IVT_BASELINE = 400.0  # the EWB mask threshold
# ceiling = TC-core plausibility cap, mirrors ar.AR_IVT_MAX_KGM_S (B8):
# 1500 kg/m/s is above every legit selected event (max 1435) and below
# Dolphin/Isaias/monsoon blobs (1785-2972).
AR_MAX_PEAK_IVT = float(os.environ.get("EWB_LIVE_AR_IVT_MAX_KGM_S", "1500"))


@functools.lru_cache(maxsize=2)
def _climatology_cached(q: float):
    """Climatology quantile field, LAZY (dims: dayofyear, hour,
    latitude(721), longitude(1440, 0-360)). Per-event reads pull only that
    event's bbox x days from the chunked remote store."""
    from extremeweatherbench import defaults as ewb_defaults

    return ewb_defaults.get_climatology(q)


@functools.lru_cache(maxsize=4)
def _climatology_cached_owb(q: float):
    """OWB pooled-quantile field (local zarr on the ewb box; same dims as
    the EWB store). Used for severity when EWB_LIVE_CLIM=owb so anomalies
    are relative to the finder's OWN thresholds and need no remote reads."""
    from ewb_live.climatology import owb_clim

    return owb_clim.get_climatology_owb(q)


def _finder_mode() -> str:
    from ewb_live.events.temperature import _clim_mode

    return _clim_mode()


@functools.lru_cache(maxsize=4096)
def _bbox_clim_mean(q: float, bbox, start: dt.datetime, end: dt.datetime,
                    src: str = "ewb") -> float | None:
    """Mean climatology (K) over bbox + event days + synoptic hours."""
    try:
        da = (_climatology_cached_owb(q) if src == "owb"
              else _climatology_cached(q))
    except Exception as exc:  # noqa: BLE001
        logger.warning("climatology unavailable (%s); severity falls back to 0", exc)
        return None
    lat0, lat1, lon0, lon1 = bbox

    # seam handling = PeriodicBoundaryIndex everywhere (owner 2026-11-09):
    # the arc [lon0 -> lon1] eastward as one continuous sel slice in the
    # grid's own domain (0-360 or -180..180) — no manual segment split.
    from ..periodic_index import attach_periodic, lon_slice

    da = attach_periodic(da)
    sl = lon_slice(np.asarray(da.longitude.values, float), lon0, lon1)

    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    # bound the read at 11 probes but sample across the WHOLE window:
    # merged same-type events reach 14-16 days, and a head-truncated
    # sample missed late-window peaks (ponytail 2026-11-08)
    days = days[::max(1, len(days) // 11)]
    doy = [d.timetuple().tm_yday for d in days]

    sub = da.sel(latitude=slice(lat0, lat1), longitude=sl).sel(dayofyear=doy)
    if sub.size == 0:
        return None
    return float(sub.mean().compute().values)


def _detail(e, key: str, default: float = 0.0) -> float:
    v = (e.details or {}).get(key)
    return float(v) if v is not None else default


_severity_cache: dict[str, float] = {}


def severity(e) -> float:
    """Severity score (arbitrary units, comparable within an event type)."""
    if e.id in _severity_cache:
        return _severity_cache[e.id]
    val = _severity(e)
    _severity_cache[e.id] = val
    return val


def _severity(e) -> float:
    """Severity score (arbitrary units, comparable within an event type).
    Temperature anomalies are measured against the ACTIVE finder
    thresholds' climatology (OWB p05/p95 in owb mode, EWB p15/p85 in
    ewb_default) so ranking stays coherent with detection."""
    area = _detail(e, "peak_area_km2")
    days = _detail(e, "max_consecutive_days", 3.0)
    owb = _finder_mode() == "owb"
    src = "owb" if owb else "ewb"
    from ewb_live.events.temperature import _mode_clim

    clips = _mode_clim()
    if e.event_type == "heat_wave":
        q = clips["heat_wave"]["q_lower"] if owb else 0.85
        clim = _bbox_clim_mean(q, e.bbox, e.start, e.end, src)
        anom = (e.peak_value - clim + 273.15) if clim is not None and e.peak_value is not None else 0.0
        return max(anom, 0.0) * area * days
    if e.event_type == "freeze":
        q = clips["freeze"]["q_upper"] if owb else 0.15
        clim = _bbox_clim_mean(q, e.bbox, e.start, e.end, src)
        mn = (e.details or {}).get("min_temp_c", e.peak_value)
        anom = (clim - 273.15 - mn) if clim is not None and mn is not None else 0.0
        return max(anom, 0.0) * area * days
    if e.event_type == "atmospheric_river":
        ivt = e.peak_value or 0.0
        return _detail(e, "landfall_days") * _detail(e, "peak_land_pixels") * max(
            ivt - AR_IVT_BASELINE, 1.0
        )
    return 0.0


def _passes_floor(e) -> bool:
    dur = (e.end - e.start).days
    if e.event_type == "atmospheric_river":
        # events.yaml AR cases: centroid |lat| >= 22, duration p90 8 d
        centroid = _detail(e, "centroid_lat", 999.0)
        lat_span = e.bbox[1] - e.bbox[0]
        lon_span = (e.bbox[3] - e.bbox[2]) % 360.0
        return (
            _detail(e, "landfall_days") >= AR_MIN_LANDFALL_DAYS
            and (e.peak_value or 0.0) >= AR_MIN_PEAK_IVT
            and (e.peak_value or 0.0) <= AR_MAX_PEAK_IVT  # B8 TC-core ceiling
            and abs(centroid) >= 20.0
            and 2 <= dur <= 14
            and lat_span <= 60.0
            and lon_span <= 60.0
        )
    # heat_wave / freeze: upstream filters + events.yaml case sanity envelope
    lat_span = e.bbox[1] - e.bbox[0]
    lon_span = (e.bbox[3] - e.bbox[2]) % 360.0
    return (
        _detail(e, "max_consecutive_days", 3.0) >= 3.0
        and 3 <= dur <= 21
        and lat_span <= 60.0
        and lon_span <= 60.0
    )


# ------------------------------------------------------------- overlap ops
# product rule (owner 2026-11-06): the final selection must contain NO pair
# of events overlapping in BOTH time and space. See module docstring.

MERGE_GAP_DAYS = 1.0      # same-type fragments may touch by at most this
MERGE_MIN_IOU = float(os.environ.get("EWB_LIVE_MERGE_MIN_IOU", "0.3"))
# merged-duration caps = upstream events.yaml per-type p90 duration
# (measured 2026-11-06: AR p90 9 d, heat_wave p90 16 d, freeze p90 16 d)
MERGE_CAP_DAYS = {
    "atmospheric_river": float(os.environ.get("EWB_LIVE_MERGE_CAP_AR_DAYS", "9")),
    "heat_wave": float(os.environ.get("EWB_LIVE_MERGE_CAP_HEAT_DAYS", "16")),
    "freeze": float(os.environ.get("EWB_LIVE_MERGE_CAP_FREEZE_DAYS", "16")),
}
CAP_LAT_SPAN = 40.0       # merged-union bbox caps; beyond -> refuse absorb
CAP_LON_SPAN = 60.0
MAX_MERGED_DAYS = 45.0    # validator hard cap (validate.MAX_DURATION_DAYS)

LAST_DECISIONS: list[dict] = []  # merge/drop log of the last rank_and_cap()


def _time_gap_days(a, b) -> float:
    """Days between the two windows; <= 0 when they overlap."""
    earlier, later = (a, b) if a.start <= b.start else (b, a)
    return (later.start - earlier.end).total_seconds() / 86400.0


def _window_gap_days(win, ev) -> float:
    """Days between event ``ev`` and a cluster window (start, end)."""
    if ev.start >= win[1]:
        return (ev.start - win[1]).total_seconds() / 86400.0
    if win[0] >= ev.end:
        return (win[0] - ev.end).total_seconds() / 86400.0
    return -1.0  # overlaps


def _lon_arcs(lon0: float, lon1: float) -> list[tuple[float, float]]:
    return [(lon0, lon1)] if lon0 <= lon1 else [(lon0, 180.0), (-180.0, lon1)]


def _bbox_intersects(a_bbox, b_bbox) -> bool:
    lat = min(a_bbox[1], b_bbox[1]) - max(a_bbox[0], b_bbox[0])
    if lat <= 0.0:
        return False
    lon = 0.0
    for l0, h0 in _lon_arcs(a_bbox[2], a_bbox[3]):
        for l1, h1 in _lon_arcs(b_bbox[2], b_bbox[3]):
            lon += max(0.0, min(h0, h1) - max(l0, l1))
    return lon > 0.0


def _overlaps(a, b) -> bool:
    """True iff the two events share >=1 day of time AND bbox area."""
    if min(a.end, b.end) - max(a.start, b.start) <= dt.timedelta(0):
        return False
    return _bbox_intersects(a.bbox, b.bbox)


def _bbox_union(bboxes) -> tuple[float, float, float, float]:
    """Union of bboxes as an eastward lon arc, anchored at bboxes[0] lon_min
    (the core). Returns (lat0, lat1, lon0, lon1) in [-180, 180], lon0 > lon1
    when the union wraps the antimeridian."""
    from ewb_live.events.types import normalize_lon_180

    lat0 = min(b[0] for b in bboxes)
    lat1 = max(b[1] for b in bboxes)
    anchor = bboxes[0][2]
    lo = hi = anchor
    for b in bboxes:
        span = (b[3] - b[2]) % 360.0 or 360.0
        # nearest signed offset from the anchor (members may extend WEST of
        # it); a purely-eastward unwrap maps a member 5 deg west as 355 deg
        # east and inflates the union toward the whole globe (dateline fix,
        # owner 2026-11-08)
        a = anchor + ((b[2] - anchor + 180.0) % 360.0) - 180.0
        lo = min(lo, a)
        hi = max(hi, a + span)
    span = min(hi - lo, 360.0)
    return (lat0, lat1, normalize_lon_180(lo), normalize_lon_180(lo + span))


def _merge_component(members: list, decisions: list[dict]):
    """Collapse one same-type merge component into a single Event."""
    import dataclasses

    from ewb_live.events.types import make_event_id
    from ewb_live.events.validate import _TITLE_LABELS

    t = members[0].event_type
    by_sev = sorted(members, key=severity, reverse=True)
    core = by_sev[0]

    start = min(m.start for m in members)
    end = max(m.end for m in members)
    truncated = False
    if (end - start).total_seconds() / 86400.0 > MAX_MERGED_DAYS:
        end = start + dt.timedelta(days=int(MAX_MERGED_DAYS))
        truncated = True

    bbox = _bbox_union([m.bbox for m in by_sev])  # core-anchored frame
    lat_span = bbox[1] - bbox[0]
    lon_span = (bbox[3] - bbox[2]) % 360.0 or 360.0
    core_fallback = lat_span > CAP_LAT_SPAN or lon_span > CAP_LON_SPAN
    if core_fallback:
        bbox = core.bbox

    def _vals(key):
        return [float(m.details[key]) for m in members
                if (m.details or {}).get(key) is not None]

    det = dict(core.details or {})
    if _vals("max_consecutive_days"):
        det["max_consecutive_days"] = max(_vals("max_consecutive_days"))
    if _vals("peak_area_km2"):
        det["peak_area_km2"] = max(_vals("peak_area_km2"))

    peaks = [m.peak_value for m in members if m.peak_value is not None]
    if t == "heat_wave":
        peak = max(peaks) if peaks else core.peak_value
        if _vals("min_temp_c"):
            det["min_temp_c"] = min(_vals("min_temp_c"))
    elif t == "freeze":
        mins = _vals("min_temp_c") or peaks
        peak = mn = min(mins) if mins else core.peak_value
        det["min_temp_c"] = mn
    else:  # atmospheric_river: core keeps its consistent pixel/threshold pair
        peak = max(peaks) if peaks else core.peak_value
        if _vals("landfall_days"):
            det["landfall_days"] = max(_vals("landfall_days"))

    wrapped = bbox[2] > bbox[3]
    if "antimeridian_wrap" in det or wrapped:
        det["antimeridian_wrap"] = wrapped

    det["merged_from"] = [m.id for m in by_sev]
    det["merged_count"] = len(members)
    det["merge_core"] = core.id

    title = (f"{_TITLE_LABELS[t]} {start:%Y-%m-%d} to "
             f"{(end - dt.timedelta(days=1)):%Y-%m-%d}")
    merged = dataclasses.replace(
        core, id=make_event_id(t, start, bbox), title=title,
        start=start, end=end, bbox=bbox, peak_value=peak, details=det,
    )
    decisions.append({
        "action": "merge", "event_type": t, "core": core.id,
        "members": [m.id for m in by_sev], "count": len(members),
        "bbox": list(bbox), "core_fallback": core_fallback,
        "truncated_31d": truncated,
        "window": [start.isoformat(), end.isoformat()],
    })
    logger.info(
        "curation MERGE %s x%d -> %s window %s..%s bbox %s%s%s",
        t, len(members), merged.id, start.date(), end.date(),
        tuple(round(x, 1) for x in bbox),
        " (core bbox)" if core_fallback else "",
        " (truncated to 31d)" if truncated else "",
    )
    return merged


def _bbox_iou(a_bbox, b_bbox) -> float:
    """Bbox intersection-over-union in deg^2 (arc-aware for wrapped lon)."""
    lat_i = min(a_bbox[1], b_bbox[1]) - max(a_bbox[0], b_bbox[0])
    if lat_i <= 0.0:
        return 0.0
    lon_i = 0.0
    for l0, h0 in _lon_arcs(a_bbox[2], a_bbox[3]):
        for l1, h1 in _lon_arcs(b_bbox[2], b_bbox[3]):
            lon_i += max(0.0, min(h0, h1) - max(l0, l1))
    if lon_i <= 0.0:
        return 0.0
    inter = lat_i * lon_i
    a_area = (a_bbox[1] - a_bbox[0]) * ((a_bbox[3] - a_bbox[2]) % 360.0 or 360.0)
    b_area = (b_bbox[1] - b_bbox[0]) * ((b_bbox[3] - b_bbox[2]) % 360.0 or 360.0)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def merge_same_type(events: list, decisions: list[dict]) -> list:
    """Merge only same-event fragments, greedily from the highest-severity
    seed: same type, temporal contiguity (gap <= MERGE_GAP_DAYS) AND bbox
    IoU >= MERGE_MIN_IOU, respecting the per-type merged-duration cap and
    the bbox span caps. Fragments that fail these gates are NOT merged;
    if they still overlap the cluster they are resolved as conflicts in
    _resolve_conflicts (drop, never merge)."""
    pending = sorted(events, key=severity, reverse=True)
    out: list = []
    while pending:
        seed = pending.pop(0)
        cluster = [seed]
        win = (seed.start, seed.end)
        bbox = seed.bbox
        cap = MERGE_CAP_DAYS.get(seed.event_type, MAX_MERGED_DAYS)
        changed = True
        while changed:
            changed = False
            for m in list(pending):  # severity order
                if _window_gap_days(win, m) > MERGE_GAP_DAYS:
                    continue
                if _bbox_iou(m.bbox, bbox) < MERGE_MIN_IOU:
                    continue
                ubox = _bbox_union([bbox, m.bbox])
                if (ubox[1] - ubox[0]) > CAP_LAT_SPAN or \
                        ((ubox[3] - ubox[2]) % 360.0 or 360.0) > CAP_LON_SPAN:
                    continue
                nwin = (min(win[0], m.start), max(win[1], m.end))
                if (nwin[1] - nwin[0]).total_seconds() / 86400.0 > cap:
                    continue
                cluster.append(m)
                pending.remove(m)
                win, bbox = nwin, ubox
                changed = True
        out.append(cluster[0] if len(cluster) == 1
                   else _merge_component(cluster, decisions))
    return out


def _load_exclusions() -> dict:
    """Owner-adjudicated event exclusions (G-40, 2026-11-08): JSON sidecar
    {event_id: {"reason": str, "by": str, "date": str}} applied at curation.
    Used for events misclassified by detection but settled by product review
    (e.g. post-tropical cyclones scored as ARs). Absent file = no exclusions."""
    import json
    import pathlib
    p = pathlib.Path("data/curation_exclusions.json")
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — malformed file must not kill curation
        logger.warning("curation exclusions file unreadable (%s); ignoring", exc)
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _resolve_conflicts(merged_by_type: dict[str, list], top_n_map: dict[str, int],
                       decisions: list[dict]) -> list:
    """Global greedy by within-type severity PERCENTILE: any surviving pair
    (same or cross type) that overlaps in time+space goes to the higher
    percentile; the loser is dropped. Per-type quota top_n; greedy order
    lets lower-ranked candidates refill freed slots (rule 3)."""
    exclusions = _load_exclusions()
    queue = []
    for t, evs in merged_by_type.items():
        n = max(1, len(evs))
        for rank, e in enumerate(evs):
            excl = exclusions.get(e.id)
            if excl:
                decisions.append({"action": "drop",
                                  "reason": f"excluded: {excl.get('reason', 'n/a')}",
                                  "event_type": t, "id": e.id, "rank": rank})
                logger.info("curation DROP %s %s (adjudicated exclusion: %s)",
                            t, e.id, excl.get("reason", "n/a"))
                continue
            queue.append(((n - rank) / n, -severity(e), t, e))
    queue.sort(key=lambda x: (-x[0], x[1], x[3].id))

    def _rank_of(e) -> int:
        return next(r for r, x in enumerate(merged_by_type[e.event_type])
                    if x.id == e.id)

    accepted: list = []
    quota: dict[str, int] = {}
    for _pct, _negsev, t, e in queue:
        if quota.get(t, 0) >= top_n_map.get(t, DEFAULT_TOP_N):
            decisions.append({"action": "drop", "reason": "quota",
                              "event_type": t, "id": e.id,
                              "rank": _rank_of(e)})
            continue
        conflict = next((a for a in accepted if _overlaps(a, e)), None)
        if conflict is not None:
            kind = ("cross-type" if conflict.event_type != t
                    else "same-type-overlap")
            decisions.append({
                "action": "drop", "reason": kind,
                "event_type": t, "id": e.id, "rank": _rank_of(e),
                "kept": conflict.id, "kept_type": conflict.event_type,
                "kept_rank": _rank_of(conflict),
            })
            logger.info(
                "curation DROP %s %s (rank %d) overlaps %s %s (rank %d) -> keep higher rank",
                e.id, t, _rank_of(e), conflict.event_type, conflict.id,
                _rank_of(conflict))
            continue
        accepted.append(e)
        quota[t] = quota.get(t, 0) + 1
    return accepted


def rank_and_cap(events, top_n: int | dict[str, int] = DEFAULT_TOP_N,
                 decisions: list[dict] | None = None) -> list:
    """Floor, merge same-type chains, resolve cross-type conflicts with
    refill, cap per event type. Guarantees zero time+space overlap pairs in
    the returned set (owner rule 2026-11-06). Decisions (merge/drop log) go
    to the optional list and to LAST_DECISIONS."""
    if decisions is None:
        decisions = []
    by_type: dict[str, list] = {}
    for e in events:
        if _passes_floor(e):
            by_type.setdefault(e.event_type, []).append(e)

    merged_by_type: dict[str, list] = {}
    for t, evs in by_type.items():
        merged = merge_same_type(evs, decisions)
        merged_by_type[t] = sorted(merged, key=severity, reverse=True)
        logger.info(
            "curation %s: %d floor-passing -> %d after merge (top severities %s)",
            t, len(evs), len(merged_by_type[t]),
            [round(severity(e), 1) for e in merged_by_type[t][:3]],
        )

    n_map = ({t: top_n for t in merged_by_type} if not isinstance(top_n, dict)
             else top_n)
    selected = _resolve_conflicts(merged_by_type, n_map, decisions)
    for e in selected:
        e.details = {**(e.details or {}), "severity_rank_score": round(severity(e), 2)}
    LAST_DECISIONS[:] = decisions
    logger.info("curation final: %s",
                {t: sum(1 for e in selected if e.event_type == t) for t in merged_by_type})
    selected.sort(key=lambda e: e.start)
    return selected
