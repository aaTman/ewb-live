"""Disk-cache maintenance — RETIRED (kept as inert shims).

Owner ruling 2026-11-17 (drop-subsets): the per-init forecast-subset cache
was deleted on the live box and may not be rebuilt: CIRA MLWP forecast
reads go through the Icechunk virtual store
(:mod:`ewb_live.stores.mlwp_icechunk`, refs only — the bytes are never
materialised) and the sources layer memoises in-process only
(:func:`ewb_live.sources.api.open_forecast`). Nothing writes that tree
anymore, so there is nothing to evict.

``prune_cache``/``maybe_prune_daily`` are kept as no-ops with the old
signatures so any still-installed caller (live host code predating this
change, cron, muscle memory) runs harmlessly; ``python -m
ewb_live.sources.cache`` prints the retirement notice.
"""

from __future__ import annotations

import argparse

RETIRED = (
    "the per-init subset cache is retired; MLWP reads use the Icechunk "
    "virtual store and nothing is materialised under the cache root"
)

#: kept for call-signature compatibility only (never used)
MAX_AGE_DAYS = 120


def prune_cache(
    max_age_days: int = MAX_AGE_DAYS,
    now=None,
    dry_run: bool = False,
    cache_root: str | None = None,
) -> dict:
    """No-op: nothing materialises subset entries any more.

    Returns the old-shaped empty result; never touches the filesystem.
    """
    return {"removed": [], "freed_bytes": 0}


def maybe_prune_daily(max_age_days: int = MAX_AGE_DAYS) -> dict | None:
    """No-op counterpart of the former once-daily auto prune."""
    return None


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="ewb_live.sources cache maintenance (retired)")
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(f"nothing to prune: {RETIRED}")


if __name__ == "__main__":  # pragma: no cover
    main()
