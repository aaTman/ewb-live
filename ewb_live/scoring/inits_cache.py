"""Persistent cache for ``sources.available_inits`` (performance).

``available_inits`` for AIWP-backed models lists ``s3://noaa-oar-mlwp-data``
day-by-day and unions in the local Icechunk store — ~15-30 s per call for
GFS-based models. The bucket content for a *past* day is final (files are
written within hours of the run), so per-model day coverage is cached in
``data/cache/aiwp_inits.json`` and only the last few days are re-listed
each call. Repeated pipeline/weekly runs over overlapping event windows
then pay the listing cost once, ever.

Cross-process safe by construction: every call re-reads the file, merges
its own freshly fetched days in, and writes the union back (last writer
supersedes with a superset — a lost write just costs a re-list later).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: days that ended more than this many days ago are considered final
STABLE_DAYS = 5


def _cache_path() -> Path:
    return Path(os.environ.get("EWB_LIVE_CACHE", "data/cache")) / "aiwp_inits.json"


def _load() -> dict:
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(cache: dict) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, p)


def available_inits_cached(
    model: str, start: dt.datetime, end: dt.datetime,
    now: dt.datetime | None = None,
) -> list[dt.datetime]:
    """sources.available_inits with persistent per-day memoization."""
    from ewb_live import sources

    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    fresh_cutoff = (now - dt.timedelta(days=STABLE_DAYS)).replace(hour=0)

    cache = _load()
    per_model: dict = cache.setdefault(model, {})
    cache[model] = per_model

    day = dt.datetime(start.year, start.month, start.day)
    use, fetch_days = [], []
    while day <= end:
        key = day.date().isoformat()
        day_end = day + dt.timedelta(days=1)
        if day_end <= fresh_cutoff and key in per_model:
            use += [dt.datetime.fromisoformat(t) for t in per_model[key]
                    if start <= dt.datetime.fromisoformat(t) <= end]
        else:
            fetch_days.append(day)
        day += dt.timedelta(days=1)

    if fetch_days:
        lo, hi = min(fetch_days), max(fetch_days) + dt.timedelta(days=1)
        # one call for the whole (recent) span; extra days it lists are cheap
        fresh = sources.available_inits(model, lo, hi)
        by_day: dict[str, list[str]] = {}
        for t in fresh:
            by_day.setdefault(t.date().isoformat(), []).append(t.isoformat())
        for d in fetch_days:
            key = d.date().isoformat()
            if d + dt.timedelta(days=1) <= fresh_cutoff:
                per_model[key] = sorted(set(by_day.get(key, [])))
        use += [t for t in fresh if start <= t <= end]
        _save(cache)

    return sorted(set(use))
