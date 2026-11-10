#!/usr/bin/env python3
"""Regenerate scorecard_by_region.json for a results dir from its published
events.json + scores.json (the same contract the browser sees). Dev-time tool:
the real pipeline writes the file itself; this exists so sample/branch dirs
can be refreshed without running scoring.

    uv run python scripts/build_region_scores.py data/results/sample
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from ewb_live.events.types import Event
from ewb_live.pipeline import SCORE_COLUMNS
from ewb_live.regions import build_region_scorecards, load_footprints


def _load_events(d: Path) -> list:
    """Scored-event universe for bbox lookup: events_selected.json is the curated
    set the pipeline scores and publishes (and what pipeline.py itself passes to
    build_region_scorecards); events.json is the raw candidate list. Neither is a
    superset of the other in practice — curation can keep an event the detector
    re-labelled — so read BOTH and union by id. Reading only events.json silently
    dropped 4 of 28 scored events on the live box (2026-11-10): region n_events
    then disagreed with scorecard.json for the same week."""
    by_id: dict = {}
    for name in ("events.json", "events_selected.json"):   # selected wins on id clash
        p = d / name
        if not p.exists():
            continue
        raw = json.loads(p.read_text())
        raw = raw["events"] if isinstance(raw, dict) and "events" in raw else raw
        for e in raw or []:
            if isinstance(e, dict) and e.get("id"):
                by_id[e["id"]] = e
    return list(by_id.values())


def main(results_dir: str) -> None:
    d = Path(results_dir)
    events_raw = _load_events(d)
    scores_raw = json.loads((d / "scores.json").read_text())
    scores_raw = scores_raw["rows"] if isinstance(scores_raw, dict) else scores_raw
    events = [Event.from_dict(e) for e in events_raw]
    df = pd.DataFrame(scores_raw)
    if isinstance(df.get("init_time"), pd.Series):
        df["init_time"] = pd.to_datetime(df["init_time"])
    # footprint masks come from the same results dir's viz assets (the rule the
    # owner chose 2026-11-10); events without an asset fall back to the centroid
    fps = load_footprints(d, [e.id for e in events])
    out = build_region_scorecards(events, df.loc[:, SCORE_COLUMNS], footprints=fps)
    scored = set(df["event_id"].unique()) if len(df) else set()
    missing = sorted(scored - {e.id for e in events})
    if missing:
        # honest, not silent: an event with score rows but no bbox cannot be assigned
        print(f"WARNING: {len(missing)} scored event(s) have no event record "
              f"(no bbox -> excluded from region counts): {missing[:5]}")
    dest = d / "scorecard_by_region.json"
    dest.write_text(json.dumps(out, indent=1))
    a = out["assignment"]
    print(f"wrote {dest} ({len(out['by_region'])} regions; footprints "
          f"{a['events_with_footprint']}/{a['events_total']} events, "
          f"rule {a['rule']}>{a['threshold']:g}, fallback {a['fallback']})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/results/sample")
