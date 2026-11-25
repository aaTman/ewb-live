"""Cache retention report (SPEC §6 phase 2) — REPORT ONLY, deletes nothing.

    python -m ewb_live.tools.cache_retention [--cache data/cache] [--window-days 90] [--margin-days 30]

For each big cache it prints how many bytes sit OUTSIDE ``window start - margin`` and so
could be evicted under the window-keyed policy. ``subsets`` already evicts by init time
(``sources/cache.py``, 120 d); ``fc_parts`` and the ERA5 mirror have no eviction today.
Dates come from entry names (init time / truth end), never from mtimes. Entries whose date
cannot be parsed are counted as "undated" and never as evictable. There is deliberately no
delete option: removal needs the owner's approval (SPEC §6).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

_INIT10 = re.compile(r"(?<!\d)(\d{10})Z?(?!\d)")
_HRES = re.compile(r"^hres_analysis_(\d{10})_(\d{10})_")


def _tree_bytes(p: Path) -> int:
    total = 0
    stack = [p]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    total += e.stat(follow_symlinks=False).st_size
    return total


def _d(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d%H").date()


def _entry_date(cache: str, name: str) -> dt.date | None:
    """The date an entry is keyed on: init time (fc_parts, subsets), truth end (hres)."""
    try:
        if cache == "truth":
            m = _HRES.match(name)
            return _d(m.group(2)) if m else None
        m = _INIT10.search(name)
        return _d(m.group(1)) if m else None
    except ValueError:
        return None


def _scan(cache_root: Path, sub: str, keep_from: dt.date) -> dict:
    root = cache_root / sub
    out = {"entries": 0, "bytes": 0, "evictable_entries": 0, "evictable_bytes": 0,
           "undated_entries": 0, "undated_bytes": 0, "oldest": None}
    if not root.is_dir():
        return out | {"missing": True}
    # subsets/<model>/<init>Z : one level deeper than the flat caches
    dirs = [d for m in sorted(root.iterdir()) if m.is_dir() for d in sorted(m.iterdir())] \
        if sub == "subsets" else sorted(root.iterdir())
    for d in dirs:
        size = _tree_bytes(d) if d.is_dir() else d.stat().st_size
        date = _entry_date(sub, d.name)
        out["entries"] += 1
        out["bytes"] += size
        if date is None:
            out["undated_entries"] += 1
            out["undated_bytes"] += size
            continue
        out["oldest"] = min(filter(None, [out["oldest"], str(date)]))
        if date < keep_from:
            out["evictable_entries"] += 1
            out["evictable_bytes"] += size
    return out


def report(cache_root: Path, window_days: int = 90, margin_days: int = 30,
           today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.UTC).date()
    keep_from = today - dt.timedelta(days=window_days + margin_days)
    rep = {"today": str(today), "keep_from": str(keep_from), "policy": f"window {window_days} d + margin {margin_days} d",
           "caches": {s: _scan(cache_root, s, keep_from) for s in ("subsets", "fc_parts", "truth")}}
    rep["evictable_gb"] = round(sum(c["evictable_bytes"] for c in rep["caches"].values()) / 1e9, 2)
    rep["note"] = ("truth/era5.zarr is one mirror array, not per-day entries: its time extent is "
                   "reported by the mirror manifest, not here")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default="data/cache")
    ap.add_argument("--window-days", type=int, default=90)
    ap.add_argument("--margin-days", type=int, default=30)
    a = ap.parse_args(argv)
    print(json.dumps(report(Path(a.cache), a.window_days, a.margin_days), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
