"""Why do some heat/freeze events fall back to the start date in qualified_at?

Item 4 of the round-1 fix (review #6): 29/145 live heat events qualify at
their start under BOTH the published (persistence) box and the tracker box -
with or without the fallback clamp. This script re-runs the qualification
scan per event on the production lane (read-only) and attributes each
fallback to one of:

  empty-window   no frame exists in [max(start, qualified_date), end)
                 (scan starts at/after the event's last day - typical for
                 ongoing events whose end sits at the lane cutoff, or where
                 qualified_date equals the end day)
  no-bins        index.json lists frames but the .bin files are absent
  below-floor    frames cover the window, but no single 6-hourly frame has
                 the 200k km2 floor of TRUE cells inside the event bbox -
                 the tracker qualifies a DAY (four steps ANDed, cumulative
                 footprint growth); a single frame inside the published box
                 can legitimately be under the floor.

Run on the ewb LXC (production read-only):
  cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
    timeout 600 /opt/ewb-live/.venv/bin/python \
      scripts/review/hb05_qualify_fallbacks.py \
        --events /opt/ewb-live/data/results/events.json \
        --lane /opt/ewb-live/data/results/raw_exceedance
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

from ewb_live.events.qualification import SEED_AREA_KM2, _layer_at


def _frames_for(idx, lo: str, end: dt.datetime) -> list[str]:
    return [f for f in idx["frames"]
            if f"{f[:4]}-{f[4:6]}-{f[6:8]}" >= lo
            and f"{f[:4]}-{f[4:6]}-{f[6:8]}" < f"{end:%Y-%m-%d}"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--lane", type=Path, required=True)
    a = ap.parse_args()
    idx = json.loads((a.lane / "index.json").read_text())
    grid = idx["grid"]
    rows = json.loads(a.events.read_text())
    counts: dict[str, int] = {}
    detail = []
    for e in rows:
        if e["event_type"] not in ("heat_wave", "freeze"):
            continue
        det = e.get("details") or {}
        start = dt.datetime.fromisoformat(e["start"])
        end = dt.datetime.fromisoformat(e["end"])
        qd = str(det.get("qualified_date") or start.date())
        lo = max(f"{start:%Y-%m-%d}", qd)
        frames = _frames_for(idx, lo, end)
        lat_min, lat_max, lon_min, lon_max = e["bbox"]
        mid = np.radians((min(lat_min, lat_max) + max(lat_min, lat_max)) / 2)
        cell_km2 = (111.32 * grid["step_lat"]) * (
            111.32 * grid["step_lon"] * max(0.15, abs(np.cos(mid))))
        floor_px = max(1, int(np.ceil(SEED_AREA_KM2 / cell_km2)))
        layer = 0 if e["event_type"] == "heat_wave" else 1
        if not frames:
            reason = "empty-window"
        else:
            ns, missing = [], 0
            for f in frames:
                p = a.lane / f"{f}.bin"
                if not p.exists():
                    missing += 1
                    continue
                ns.append(_layer_at(p, grid, layer, lat_min, lat_max,
                                    lon_min, lon_max))
            if ns and all(n == 0 for n in ns) and missing == len(frames) - len(ns):
                reason = "no-bins"
            elif ns and max(ns) < floor_px:
                reason = "below-floor"
            else:
                reason = "qualifies"      # scan would find a frame - not a fallback
        counts[reason] = counts.get(reason, 0) + 1
        if reason in ("empty-window", "below-floor"):
            detail.append({"id": e["id"], "type": e["event_type"],
                           "reason": reason, "start": e["start"][:10],
                           "end": e["end"][:10], "qualified_date": qd,
                           "ongoing": bool(det.get("ongoing")),
                           "frames_in_window": len(frames),
                           "max_frame_px": int(max([_layer_at(a.lane / f"{f}.bin", grid, layer, lat_min, lat_max, lon_min, lon_max)
                                                     for f in frames
                                                     if (a.lane / f"{f}.bin").exists()],
                                                   default=0)),
                           "floor_px": floor_px})
    print(json.dumps(counts, indent=1))
    by_type: dict[str, int] = {}
    qd_eq_end = 0
    for d in detail:
        k = f"{d['reason']}:{d['type']}"
        by_type[k] = by_type.get(k, 0) + 1
        if d["qualified_date"] == d["end"]:
            qd_eq_end += 1
    print(json.dumps({"by_type": by_type,
                      "qualified_date_eq_end_day": qd_eq_end,
                      "of": len(detail)}, indent=1))
    for d in sorted(detail, key=lambda r: (r["reason"], r["id"])):
        print(f"{d['reason']:12s} {d['id']} {d['start']}..{d['end']} "
              f"qd={d['qualified_date']} ongoing={int(d['ongoing'])} "
              f"frames={d['frames_in_window']} max_px={d['max_frame_px']}"
              f"/{d['floor_px']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
