"""Drop all incremental artifacts for one event so the next run re-scores it.

Stale-parts bug (e86c13f47bc0, 2026-11-09): score parts are keyed by event
fingerprint only — a code fix (e.g. G-34 longitude normalization) does not
invalidate parts written by the broken code. After such a fix, purge the
suspect events' parts + pair_status entries + spill, then rerun the pipeline
(merges/re-scores; publish-only cannot regenerate).

Usage: python scripts/review/purge_event_parts.py EVENT_ID [--dry-run]
"""
import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def purge(eid: str, dry: bool) -> None:
    parts = list((ROOT / "data/cache/score_parts").glob(f"{eid}_*.parquet"))
    sidecars = list((ROOT / "data/results/pair_sidecar").glob(f"{eid}*.status.json"))
    spill = ROOT / "data/cache/ewb_mid" / eid
    ps_path = ROOT / "data/results/pair_status.json"
    ps = json.loads(ps_path.read_text())
    ps_hit = [k for k in ps if k.startswith(eid + "|")]
    # CRITICAL (e86c rescore no-op, 2026-11-09): the pipeline's `done` set is
    # derived from scores.parquet rows whose fingerprint+truth_source still
    # match — cached ROWS mark a pair scored even with its parts purged. The
    # rows must go too, or the next run silently skips the event and
    # re-publishes the stale rows from the cache.
    scores_path = ROOT / "data/results/scores.parquet"
    n_rows = 0
    if scores_path.exists():
        import pandas as pd
        old = pd.read_parquet(scores_path)
        n_rows = int((old["event_id"] == eid).sum())
    print(f"{eid}: {len(parts)} parts, {len(sidecars)} sidecars, "
          f"spill={spill.exists()}, {len(ps_hit)} pair_status keys, "
          f"{n_rows} scores.parquet rows")
    if dry:
        return
    for p in parts + sidecars:
        p.unlink()
    import shutil
    shutil.rmtree(spill, ignore_errors=True)
    for k in ps_hit:
        ps.pop(k)
    tmp = ps_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ps))
    tmp.replace(ps_path)
    if n_rows:
        import pandas as pd
        import time
        old = pd.read_parquet(scores_path)
        backup = scores_path.with_name(f"scores.parquet.bak-{eid}-{int(time.time())}")
        old.to_parquet(backup)
        cut = old[old["event_id"] != eid]
        tmp2 = scores_path.with_suffix(".tmp.parquet")
        cut.to_parquet(tmp2)
        os.replace(tmp2, scores_path)
        print(f"dropped {n_rows} rows from scores.parquet (backup: {backup.name})")
    print("purged — next pipeline run re-scores", eid)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("event_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    purge(a.event_id, a.dry_run)
    sys.exit(0)
