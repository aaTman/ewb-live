"""Review: the 2026-11-10 area rule vs the published heat/freeze events.

Runs the real scan path (OWB climatology bounds -> daily exceedance mask ->
area-tracked events) over the live 90-day window, reading the box's LOCAL
ERA5 mirror instead of streaming ARCO, and prints the resulting event list
against the published catalogue (data/results/events.json, top_n=None) so
the funnel is comparable: seeds, boxes, areas, durations.

Run on the ewb LXC from the checkout root:

    .venv/bin/python scripts/review/evt01_area_rule_compare.py \
        --start 2026-08-11 --end 2026-11-09 --out /tmp/evt01.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np

MIRROR = Path("data/cache/truth/era5.zarr")


def patch_mirror() -> None:
    """Point the upstream ERA5 opener at the local mirror (no network)."""
    import xarray as xr

    from ewb_live.events._upstream import temperature_events as _te_module

    te = _te_module()

    def open_mirror_t2m(start_date: str, end_date: str):
        ds = xr.open_zarr(MIRROR, consolidated=False)
        t2m = ds["2m_temperature"].sel(time=slice(start_date, end_date))
        six = t2m.time.dt.hour.isin([0, 6, 12, 18])
        t2m = t2m.sel(time=six)
        return t2m.chunk({"latitude": -1, "longitude": -1}).sortby("latitude")

    te.open_era5_t2m = open_mirror_t2m
    print(f"[mirror] t2m source -> {MIRROR}")


def box_km(bbox) -> tuple[float, float]:
    lat0, lat1, lon0, lon1 = bbox
    mid = math.radians((lat0 + lat1) / 2.0)
    h = abs(lat1 - lat0) * 111.32
    w = (abs(lon1 - lon0) if lon1 >= lon0 else abs(lon1 - lon0) + 360.0)
    return w * 111.32 * max(0.05, abs(math.cos(mid))), h


def fmt(e, tag: str = "") -> str:
    w, h = box_km(e.bbox)
    d = e.details or {}
    return (f"{tag}{e.event_type[:5]:5} {e.start:%m-%d}->{e.end - dt.timedelta(days=1):%m-%d}"
            f" {(e.end - e.start).days:2}d  box {w:6.0f}x{h:6.0f} km = "
            f"{w * h / 1e6:5.2f}M km2  peak_area {d.get('peak_area_km2', 0) / 1e3:6.0f}k"
            f"  consec {d.get('max_consecutive_days', 0):2}"
            f"  seed {d.get('seed_area_km2', 0) / 1e3 if d.get('seed_area_km2') is not None else -1:6.0f}k"
            f"  {'ONGOING' if d.get('ongoing') else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-11")
    ap.add_argument("--end", default="2026-11-02")
    ap.add_argument("--types", default="heat_wave,freeze")
    ap.add_argument("--published", default="data/results/events.json")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    patch_mirror()
    from ewb_live.events.temperature import scan_temperature_events
    from ewb_live.events.truth import truth_cutoff

    start = dt.date.fromisoformat(args.start)
    cutoff = truth_cutoff().date()
    end = min(dt.date.fromisoformat(args.end), cutoff)
    print(f"[window] {start} .. {end} (ERA5 cutoff {cutoff} respected)")

    new = []
    for et in args.types.split(","):
        evs = scan_temperature_events(start, end, et)
        print(f"\n=== NEW RULE {et}: {len(evs)} events ===")
        for e in evs:
            print("  " + fmt(e))
        new += evs

    pub_path = Path(args.published)
    if pub_path.exists():
        pub = json.loads(pub_path.read_text())
        print(f"\n=== PUBLISHED (upstream tracker) {pub_path} ===")
        from ewb_live.events.types import Event
        old = [Event.from_dict(d) for d in pub
               if d["event_type"] in args.types.split(",")]
        by_type: dict[str, list] = {}
        for e in old:
            by_type.setdefault(e.event_type, []).append(e)
        for t, evs in sorted(by_type.items()):
            print(f"  {t}: {len(evs)} events")
            for e in evs:
                print("  " + fmt(e))

    if args.out:
        Path(args.out).write_text(json.dumps([e.to_dict() for e in new], indent=1))
        print(f"\n[out] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
