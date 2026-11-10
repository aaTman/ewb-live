"""Replay check (science-review 2026-11-09): production rank_and_cap output
must equal shipped events + publish-gate rejects, exactly. Any disagreement
means selection is non-deterministic or artifacts desynced — a bug.

Re-derives curation from the persisted full catalogue (data/results/
events.json) — no detection re-run. Run after every pipeline run:
    ./ops/remote.sh run python scripts/review/rank_cap_replay.py
Exit 0 = agree; 1 = disagreement (printed both sides).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os

import ewb_live.events.select as select  # noqa: E402
from ewb_live.events.select import rank_and_cap  # noqa: E402
from ewb_live.events.temperature import recompute_merged_max_consec  # noqa: E402
from ewb_live.events.truth import truth_cutoff  # noqa: E402
from ewb_live.events.types import Event  # noqa: E402
from ewb_live.events.validate import validate_event  # noqa: E402

R = ROOT / "data" / "results"

# Pin the code state that produced the shipped artifacts: data/results/*
# were curated by the 2026-11-09 08:13Z runner when AR_MIN_LANDFALL_DAYS
# was still 3; the constant was relaxed to 1 later that day (~22:00Z,
# owner landfall-funnel amendment). Unpin (set 1) after the next pipeline
# run regenerates the shipped selection under current code.
select.AR_MIN_LANDFALL_DAYS = int(os.environ.get("EWB_REPLAY_LANDFALL_FLOOR", "3"))


def main() -> int:
    catalogue = [Event.from_dict(d) for d in
                 json.loads((R / "events.json").read_text())]
    curated = rank_and_cap(catalogue)
    # mirror detect_events (__init__.py:116-117): merged max_consecutive_days
    # is recomputed from the exceedance mask BEFORE validate_event runs
    recompute_merged_max_consec(curated, truth_cutoff())
    rejects = {e.id for e in curated if validate_event(e)}
    kept = [e.id for e in curated if e.id not in rejects]
    shipped = {e["id"] for e in json.loads((R / "events_selected.json").read_text())}
    if set(kept) == shipped:
        print(f"REPLAY OK: rank_and_cap {len(curated)} -> gate rejects "
              f"{sorted(rejects)} -> kept {len(kept)} == shipped {len(shipped)}")
        return 0
    print(f"REPLAY MISMATCH: replay kept {len(kept)}, shipped {len(shipped)}")
    print("  replay-only:", sorted(set(kept) - shipped))
    print("  shipped-only:", sorted(shipped - set(kept)))
    print("  gate rejects:", sorted(rejects))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
