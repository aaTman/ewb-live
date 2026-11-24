#!/usr/bin/env python3
"""One-line status of a dev/review tree's published outputs.

Usage: python ops/dev_site_report.py [results_dir]   (default data/results)

Prints the per-type selected counts, meta's event_counts, the window, the
IBTrACS freshness record and how many per-event viz docs exist — the handful of
numbers the /dev/ review site is judged by.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

res = Path(sys.argv[1] if len(sys.argv) > 1 else "data/results")


def load(name):
    p = res / name
    try:
        return json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001 - a missing artifact is the report
        return {"__error__": f"{type(exc).__name__}: {exc}"}


sel = load("events_selected.json")
raw = load("events.json")
meta = load("meta.json")
if isinstance(sel, dict):
    print(f"events_selected.json: {sel['__error__']}")
    sys.exit(1)
cat = collections.Counter(e["event_type"] for e in sel)
rawl = collections.Counter(e["event_type"] for e in raw) if isinstance(raw, list) else {}
vizdir = res / "events"
viz = sum(1 for p in vizdir.glob("*/viz.json")) if vizdir.is_dir() else 0
print(f"window: {meta.get('window') or (meta.get('start'), meta.get('end'))}  "
      f"run: {meta.get('run_id') or meta.get('generated_at')}")
print("selected : " + "  ".join(f"{k}={cat.get(k, 0)}" for k in
                                ("heat_wave", "freeze", "cold_snap", "atmospheric_river",
                                 "tropical_cyclone", "midlat_cyclone", "flood")))
print("raw cat  : " + "  ".join(f"{k}={rawl.get(k, 0)}" for k in cat) if rawl else "raw cat  : (events.json missing)")
print("meta counts: " + "  ".join(f"{k}={v}" for k, v in (meta.get("event_counts") or {}).items()))
print(f"viz docs: {viz}   scorecard: {'yes' if (res / 'scorecard.json').exists() else 'NO'}"
      f"   score rows: {meta.get('n_score_rows')}   ibtracs: {meta.get('ibtracs')}")
