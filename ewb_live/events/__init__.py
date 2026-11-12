"""Event detection for EWB Live (SPEC section 2).

``detect_events`` finds heat_wave / freeze / atmospheric_river events over a
date range using EWB's defaults and thresholds, with a persistent per-window
cache so re-runs don't redo completed scans.
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
    if event_type in ("heat_wave", "freeze"):
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

    The raw detector output is then curated (ewb_live.events.select):
    per-type floors, severity ranking (climatology anomaly x area x duration;
    landfall days x land pixels x IVT excess for ARs), fragment dedup, and a
    cap of ``top_n`` per type (default 15; None disables curation). Events are
    returned sorted by (start, id), deduplicated by id.
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
    if det_end < start:
        logger.warning("window %s..%s fully beyond ERA5 cutoff %s", start, end, cutoff)
        return []

    by_id: dict[str, Event] = {}
    for et in types:
        path = _cache_path(cache_dir, et, start, det_end)
        if use_cache and not force and path.exists():
            events = [Event.from_dict(d) for d in json.loads(path.read_text())]
            logger.info("detect_events %s: cache hit (%d events)", et, len(events))
        else:
            if et in ("heat_wave", "freeze"):
                from .temperature import scan_temperature_events

                events = scan_temperature_events(start, det_end, et, **scan_kwargs)
            else:
                from .ar import scan_ar_events

                events = scan_ar_events(start, det_end, **scan_kwargs)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([e.to_dict() for e in events], indent=1))
            tmp.replace(path)
            logger.info("detect_events %s: scanned %d events", et, len(events))
        for e in events:
            by_id[e.id] = e

    out = sorted(by_id.values(), key=lambda e: (e.start, e.id))
    raw_counts = {t: sum(1 for e in out if e.event_type == t) for t in types}
    if top_n is not None:
        from .select import rank_and_cap

        out = rank_and_cap(out, top_n)
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
