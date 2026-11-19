"""Event detection for EWB Live (SPEC section 2).

``detect_events`` finds heat_wave / freeze / cold_snap / atmospheric_river /
tropical_cyclone / flood events over a date range using EWB's defaults and
thresholds, with a persistent per-window cache so re-runs don't redo completed
scans.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .types import EVENT_TYPES, Event
from .truth import truth_cutoff

logger = logging.getLogger(__name__)

__all__ = ["detect_events", "lane_status_report", "EVENT_TYPES", "Event",
           "truth_cutoff"]

DEFAULT_CACHE_DIR = Path("data/cache/events")

# --------------------------------------------------------------------------
# Per-lane scan outcome for the UI's honest zero (owner rule 2026-11-17, mlc-fix
# 2026-11-18): a lane that can fail must be able to tell "the lane broke" from
# "nothing happened this window". The midlat_cyclone tracker used to fail soft to
# ``[]``, so a reader bug spent a whole 90-day build publishing a calm-looking 0
# (notes/mlc_fix.md). A lane reports its outcome through its own ``lane_status()``
# (mlc.py); ``detect_events`` records it here and persists it next to the scan
# cache so a cache-hit run still knows, and ``pipeline`` publishes the dict as
# ``meta.lane_status``. Lanes that report nothing get no entry and keep their
# existing empty-lane copy — the TC lane's ``meta.ibtracs`` stays as it is.
# --------------------------------------------------------------------------
LANE_STATUS: dict[str, dict] = {}


def _status_path(cache_dir: Path, event_type: str) -> Path:
    return cache_dir / f"lane_status_{event_type}.json"


def _lane_scan_status(event_type: str) -> dict:
    """What the scan that just ran says about itself ({} = it reports nothing)."""
    if event_type == "midlat_cyclone":
        from .mlc import lane_status

        try:
            return lane_status()
        except Exception as exc:  # noqa: BLE001 - a status note must not fail a scan
            logger.warning("lane_status(%s) unreadable: %s", event_type, exc)
            return {}
    return {}


def _remember_lane_status(cache_dir: Path, event_type: str) -> None:
    """Keep this run's outcome in memory and on disk (atomic, like the cache)."""
    st = _lane_scan_status(event_type)
    if not st:
        return
    LANE_STATUS[event_type] = st
    path = _status_path(cache_dir, event_type)
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(path)
    except OSError as exc:                      # read-only cache: in-memory still ships it
        logger.warning("lane_status(%s): could not persist %s: %s", event_type, path, exc)


def lane_status_report(cache_dir: str | Path = DEFAULT_CACHE_DIR,
                       types: tuple[str, ...] = EVENT_TYPES) -> dict:
    """``meta.lane_status`` payload for these lanes (empty dict = nothing known)."""
    cache_dir = Path(cache_dir)
    out: dict[str, dict] = {}
    for et in types:
        p = _status_path(cache_dir, et)
        if p.exists():
            try:
                out[et] = json.loads(p.read_text())
            except (OSError, ValueError) as exc:
                logger.warning("lane_status_report: bad %s: %s", p, exc)
        if et in LANE_STATUS:
            out[et] = LANE_STATUS[et]
    return out


def _cache_path(cache_dir: Path, event_type: str, start: dt.date, end: dt.date) -> Path:
    # temperature scans depend on the active climatology mode AND the
    # effective clip quantile (EWB_LIVE_CLIM + EWB_LIVE_HEAT_CLIP /
    # EWB_LIVE_FREEZE_CLIP); tag the key so neither switch can resurrect
    # a scan made with different thresholds.
    tag = ""
    if event_type in ("heat_wave", "freeze", "cold_snap"):
        import os

        from .temperature import _mode_clim

        clip = _mode_clim()[event_type]
        qv = clip["q_lower"] if event_type == "heat_wave" else clip["q_upper"]
        tag = f"_{os.environ.get('EWB_LIVE_CLIM', 'owb')}{qv}"
    if event_type == "atmospheric_river":
        # bump when AR rules change (mask rules / tropical screen / TC
        # radius) so an old scan can't resurrect under new code —
        # v2 = SPEC "AR tropical exclusion" 2026-11-08
        from .ar import AR_RULE_REV

        tag = f"_{AR_RULE_REV}"
    if event_type == "tropical_cyclone":
        # bump when the TC qualification rule changes (tc lane, 2026-11-16)
        from .tc import MIN_TS_FIXES, MIN_TS_HOURS, MIN_TS_WIND_KT

        tag = f"_v1-{int(MIN_TS_WIND_KT)}kt-{MIN_TS_FIXES}fix-{int(MIN_TS_HOURS)}h"
    if event_type == "flood":
        # bump when the flood rule changes (quantile / accumulation lanes /
        # tracker floors) so an old scan can't resurrect under new code; the
        # RAIN SOURCE is part of the key too (2026-11-19 PARDIG switch) — a
        # scan made on one store must never serve the other lane config
        from .flood import DEFAULT_QUANTILE, FLOOD_PARAMS, WINDOWS
        from .flood import resolve_source as _flood_source

        tag = (f"_v1-q{int(round(DEFAULT_QUANTILE * 100))}-w"
               f"{''.join(str(w) for w in WINDOWS)}"
               f"-a{int(FLOOD_PARAMS.qualify_area_km2 // 1000)}k"
               f"-d{FLOOD_PARAMS.min_days}-{_flood_source(None)}")
    if event_type == "midlat_cyclone":
        # bump when a mid-latitude screen or the track mapping changes, so an
        # old scan cannot resurrect under new rules (mlc lane, 2026-11-17)
        from .mlc import RULE_REV

        tag = f"_{RULE_REV}"
    return cache_dir / f"{event_type}{tag}_{start:%Y-%m-%d}_{end:%Y-%m-%d}.json"


def detect_events(
    start: dt.date,
    end: dt.date,
    types: tuple[str, ...] = EVENT_TYPES,
    *,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    force: bool = False,
    use_cache: bool = True,
    top_n: int | dict[str, int] | None = 15,
    **scan_kwargs,
) -> list[Event]:
    """Detect events overlapping [start, end] (inclusive dates).

    Scans run only against the ERA5-ARCO-available sub-window (detection
    beyond the truth cutoff is impossible and would stream NaN). Raw scan
    results are cached per (type, clamped window); a cache hit skips the scan.

    The raw detector output is then curated (ewb_live.events.select): cold
    snaps already claimed by a freeze are dropped first
    (ewb_live.events.cold_snap.subsume_freeze_overlaps), then per-type floors,
    severity ranking (climatology anomaly x area x duration; landfall days x
    land pixels x IVT excess for ARs), fragment dedup, and a cap of ``top_n``
    per type (default 15; None disables curation and that drop, so the raw
    catalogue lists both lanes). Events are returned sorted by (start, id),
    deduplicated by id.
    """
    if start > end:
        raise ValueError("start must be <= end")
    for et in types:
        if et not in EVENT_TYPES:
            raise ValueError(f"unknown event type {et!r}; expected {EVENT_TYPES}")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cutoff = truth_cutoff()
    det_end = min(end, cutoff.date())

    from .select import SELF_SOURCE_TYPES   # lazy: select pulls in the raster lanes

    by_id: dict[str, Event] = {}
    for et in types:
        # Lanes that read their own source (SELF_SOURCE_TYPES: tropical_cyclone
        # from IBTrACS best tracks, flood from IMERG) are not bound by the ERA5
        # truth cutoff, so the clamp must not trim them — it silently erased the
        # last ~6 days of storms from the "90-day" TC window every week
        # (2026-11-17), and the newest days are exactly where a fresh flood or
        # cyclone lives. midlat_cyclone is NOT exempt: it is tracked in ERA5.
        lane_end = end if et in SELF_SOURCE_TYPES else det_end
        if lane_end < start:
            logger.warning("detect_events %s: window %s..%s fully beyond the "
                           "ERA5 cutoff %s; lane skipped", et, start, end, cutoff)
            continue
        path = _cache_path(cache_dir, et, start, lane_end)
        status_path = _status_path(cache_dir, et)
        if use_cache and not force and path.exists():
            events = [Event.from_dict(d) for d in json.loads(path.read_text())]
            logger.info("detect_events %s: cache hit (%d events)", et, len(events))
            if status_path.exists():      # the scan that made this cache said why
                try:
                    LANE_STATUS[et] = json.loads(status_path.read_text())
                except (OSError, ValueError) as exc:
                    logger.warning("detect_events %s: bad lane status: %s", et, exc)
        else:
            if et in ("heat_wave", "freeze", "cold_snap"):
                from .temperature import scan_temperature_events

                events = scan_temperature_events(start, lane_end, et, **scan_kwargs)
            elif et == "tropical_cyclone":
                # track-based lane (IBTrACS best tracks), no ERA5 scan
                from .tc import scan_tc_events

                events = scan_tc_events(start, lane_end)
            elif et == "flood":
                # observation lane: IMERG daily rain vs the ERA5 percentile
                from .flood import scan_flood_events

                events = scan_flood_events(start, lane_end)
            elif et == "midlat_cyclone":
                # track lane: closed MSLP lows tracked in ERA5 (mlc lane) — the
                # scan streams its own window from ARCO and fails soft to [].
                # lane_end is the ERA5-clamped end here: midlat_cyclone IS an
                # ERA5 lane (SELF_SOURCE_TYPES), it just streams its own fields.
                from .mlc import scan_mlc_events

                events = scan_mlc_events(start, lane_end, **scan_kwargs)
            else:
                from .ar import scan_ar_events

                events = scan_ar_events(start, lane_end, **scan_kwargs)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([e.to_dict() for e in events], indent=1))
            tmp.replace(path)
            logger.info("detect_events %s: scanned %d events", et, len(events))
            _remember_lane_status(cache_dir, et)
        for e in events:
            by_id[e.id] = e

    out = sorted(by_id.values(), key=lambda e: (e.start, e.id))
    raw_counts = {t: sum(1 for e in out if e.event_type == t) for t in types}
    if top_n is not None:
        # cold snaps that a freeze already claims are removed BEFORE curation,
        # so the duplicate cannot take a slot or be scored twice (the freeze is
        # the more specific claim; ewb_live/events/cold_snap.py). The drop log
        # is prepended to select.LAST_DECISIONS so it lands in curation.json.
        from .cold_snap import subsume_freeze_overlaps
        from .select import rank_and_cap

        subsumed: list[dict] = []
        out = subsume_freeze_overlaps(out, subsumed)
        out = rank_and_cap(out, top_n)
        if subsumed:
            from . import select as _select

            _select.LAST_DECISIONS[:] = subsumed + list(_select.LAST_DECISIONS)
        # merged heat/freeze: member-max under-reports contiguous runs;
        # recompute from the exceedance mask (guardian-design 2026-11-08)
        from .temperature import recompute_merged_max_consec
        recompute_merged_max_consec(out, cutoff)
    logger.info(
        "detect_events %s..%s -> %d events %s (raw %s)",
        start,
        end,
        len(out),
        {t: sum(1 for e in out if e.event_type == t) for t in types},
        raw_counts,
    )
    return out
