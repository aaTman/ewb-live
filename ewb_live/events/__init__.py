"""Event detection for EWB Live (SPEC section 2).

``detect_events`` finds heat_wave / freeze / cold_snap / atmospheric_river
events over a date range using EWB's defaults and thresholds, with a
persistent per-window cache so re-runs don't redo completed scans.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .types import EVENT_TYPES, Event
from .truth import truth_cutoff

logger = logging.getLogger(__name__)

__all__ = ["detect_events", "EVENT_TYPES", "Event", "truth_cutoff"]

DEFAULT_CACHE_DIR = Path("data/cache/events")


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
    from .select import TRACK_TYPES   # lazy: select pulls in the raster lanes

    by_id: dict[str, Event] = {}
    for et in types:
        # Track lanes read best-track observations, not ERA5 rasters: the
        # truth-cutoff clamp must not trim them (2026-11-17 — the clamp
        # silently erased the last ~6 days of storms from the "90-day" TC
        # window every week, exactly where the newest cyclone lives).
        lane_end = end if et in TRACK_TYPES else det_end
        if lane_end < start:
            logger.warning("detect_events %s: window %s..%s fully beyond "
                           "ERA5 cutoff %s; lane skipped", et, start, end, cutoff)
            continue
        path = _cache_path(cache_dir, et, start, lane_end)
        if use_cache and not force and path.exists():
            events = [Event.from_dict(d) for d in json.loads(path.read_text())]
            logger.info("detect_events %s: cache hit (%d events)", et, len(events))
        else:
            if et in ("heat_wave", "freeze", "cold_snap"):
                from .temperature import scan_temperature_events

                events = scan_temperature_events(start, lane_end, et, **scan_kwargs)
            elif et == "tropical_cyclone":
                # track-based lane (IBTrACS best tracks), no ERA5 scan
                from .tc import scan_tc_events

                events = scan_tc_events(start, lane_end)
            else:
                from .ar import scan_ar_events

                events = scan_ar_events(start, lane_end, **scan_kwargs)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([e.to_dict() for e in events], indent=1))
            tmp.replace(path)
            logger.info("detect_events %s: scanned %d events", et, len(events))
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
