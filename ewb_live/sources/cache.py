"""Disk-cache maintenance for ``ewb_live.sources``.

The dashboard refreshes on a weekly cadence (SPEC section 5): each refresh
materialises a week of new inits under ``data/cache/subsets/<model>/<init>/``
and old inits fall out of the 90-day scoring window. To keep the cache bounded
we evict subset entries whose *initialisation time* is older than
``MAX_AGE_DAYS`` (default 120 days = 90-day window + scoring overlap).

The init timestamp is encoded in the directory name (``YYYYmmddHHZ``), so
eviction never depends on mtime (zarr writes touch mtimes).

Entry points:
* ``prune_cache()`` — programmatic; safe to call concurrently (best-effort).
* ``python -m ewb_live.sources.cache [--max-age-days N] [--dry-run]``.
* ``open_forecast`` triggers one prune per day automatically (marker file).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil

from ._net import CACHE_ROOT

MAX_AGE_DAYS = 120
_SUBSET_ROOT = os.path.join(CACHE_ROOT, "subsets")
_MARKER = os.path.join(CACHE_ROOT, ".last_prune")
_INIT_DIR = re.compile(r"^(\d{10})Z$")


def _init_from_dir(name: str) -> dt.datetime | None:
    m = _INIT_DIR.match(name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y%m%d%H")
    except ValueError:
        return None


def prune_cache(
    max_age_days: int = MAX_AGE_DAYS,
    now: dt.datetime | None = None,
    dry_run: bool = False,
    cache_root: str | None = None,
) -> dict:
    """Delete cached subsets older than ``max_age_days``.

    Returns {"removed": [paths], "freed_bytes": int}. Never raises on
    individual entries; missing cache root is a no-op.
    """
    root = cache_root or _SUBSET_ROOT
    removed: list[str] = []
    freed = 0
    if not os.path.isdir(root):
        return {"removed": removed, "freed_bytes": freed}
    cutoff = (now or dt.datetime.utcnow()) - dt.timedelta(days=max_age_days)
    for model in sorted(os.listdir(root)):
        mdir = os.path.join(root, model)
        if not os.path.isdir(mdir):
            continue
        for sub in sorted(os.listdir(mdir)):
            init = _init_from_dir(sub)
            if init is None or init >= cutoff:
                continue
            path = os.path.join(mdir, sub)
            size = _dir_size(path)
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
            freed += size
    return {"removed": removed, "freed_bytes": freed}


def maybe_prune_daily(max_age_days: int = MAX_AGE_DAYS) -> dict | None:
    """Prune at most once per 24 h (marker-file gated). Best-effort."""
    try:
        if os.path.exists(_MARKER):
            age = dt.datetime.now().timestamp() - os.path.getmtime(_MARKER)
            if age < 86400:
                return None
        os.makedirs(CACHE_ROOT, exist_ok=True)
        with open(_MARKER, "w") as fh:
            fh.write(dt.datetime.now(dt.timezone.utc).isoformat())
        return prune_cache(max_age_days)
    except OSError:  # pragma: no cover - cache maintenance is opportunistic
        return None


def _dir_size(path: str) -> int:
    total = 0
    for dp, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Prune ewb_live.sources subset cache")
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = prune_cache(args.max_age_days, dry_run=args.dry_run)
    mb = res["freed_bytes"] / 1e6
    print(f"removed {len(res['removed'])} subset entries ({mb:.1f} MB)"
          + (" [dry-run]" if args.dry_run else ""))
    for p in res["removed"]:
        print("  ", p)


if __name__ == "__main__":  # pragma: no cover
    main()
