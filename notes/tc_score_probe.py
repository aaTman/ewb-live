"""TC scoring feasibility probe (audit TC-8, ne-fix-tc 2026-11-19).

Question the owner copy hinges on ("Not scored — … no EWB benchmark"): what,
if anything, can the CIRA MLWP Icechunk VIRTUAL STORE verify about a
best-track event? The measurable claim is storm PRESENCE and POSITION: at
lead time L from an init, does the model carry a wind-max / MSLP-min cell
near the IBTrACS best-track position, how far off is it, and how fast is the
read? Intensity is RECORDED but not judged: at 0.25 deg the MLWP models
cannot resolve a TC core, so a wind "error" against a 1-minute best-track kt
is mostly resolution bias — a number, not a benchmark.

Runs on the ewb LXC, in the DEV tree, strictly READ-ONLY (events JSON + the
virtual store via the sanctioned `sources.api.open_forecast` path, cache
writes off). Nothing here promotes or scores anything; it measures
feasibility and prints a number.

  cd /opt/ewb-live-dev && . .venv/bin/activate
  python notes/tc_score_probe.py --json notes/screenshots/tc_score_probe.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np

MS_TO_KT = 1.9438444924406046
VARS = ["surface_eastward_wind", "surface_northward_wind",
        "air_pressure_at_mean_sea_level"]


def hav_km(a, b) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="data/results/events.json")
    ap.add_argument("--models", default="graphcast,aurora,pangu_weather,"
                                        "fourcastnet_v2")
    ap.add_argument("--leads", default="24,48,72")
    ap.add_argument("--storms", type=int, default=3, help="strongest N events")
    ap.add_argument("--box", type=float, default=5.0, help="half-box, deg")
    ap.add_argument("--json", default="", help="write full report here")
    args = ap.parse_args()

    events = [e for e in json.load(open(args.events))
              if e.get("event_type") == "tropical_cyclone"
              and e.get("peak_value")]
    events.sort(key=lambda e: -e["peak_value"])
    chosen = events[:args.storms]
    models = args.models.split(",")
    leads = [int(x) for x in args.leads.split(",")]

    from ewb_live.sources import api

    rows, t_all = [], time.perf_counter()
    for ev in chosen:
        d = ev["details"]
        track = sorted((dt.datetime.fromisoformat(r[0].replace("Z", "")),
                        r[1], r[2], r[3])
                       for r in d["track"] if r[1] is not None)
        init = next((r[0] for r in track if r[0].hour % 6 == 0), None)
        if init is None:
            continue
        for m in models:
            try:
                inits = [i for i in api.available_inits(
                    m, init - dt.timedelta(hours=12), init) if i <= init]
            except Exception as exc:                       # noqa: BLE001
                rows.append(dict(storm=d["name"], model=m,
                                 error=f"inits: {type(exc).__name__}: {exc}"[:140]))
                continue
            if not inits:
                rows.append(dict(storm=d["name"], model=m,
                                 error="no inits in [-12 h, init]"))
                continue
            use_init = max(inits)
            shift_h = int((init - use_init).total_seconds() // 3600)
            for L in leads:
                valid = init + dt.timedelta(hours=L)
                near = min(track, key=lambda r: abs((r[0] - valid).total_seconds()))
                if abs((near[0] - valid).total_seconds()) > 3 * 3600:
                    continue
                b = args.box
                bbox = (near[1] - b, near[1] + b, near[2] - b, near[2] + b)
                t0 = time.perf_counter()
                try:
                    ds = api.open_forecast(m, use_init, VARS,
                                           [L + shift_h], bbox,
                                           use_cache=False)
                    g = ds.isel(init_time=0, lead_time=-1)
                    wsp = (np.hypot(g["surface_eastward_wind"].values,
                                    g["surface_northward_wind"].values)
                           * MS_TO_KT)
                    mslp = g["air_pressure_at_mean_sea_level"].values
                    lats, lons = g.latitude.values, g.longitude.values
                    iy, ix = np.unravel_index(int(np.nanargmax(wsp)), wsp.shape)
                    my, mx = np.unravel_index(int(np.nanargmin(mslp)), mslp.shape)
                    row = dict(storm=d["name"], model=m, init=str(use_init),
                               lead_h=L, best_kt=near[3],
                               fc_wind_max_kt=round(float(np.nanmax(wsp)), 1),
                               fc_mslp_min_hpa=round(float(np.nanmin(mslp)), 1),
                               windmax_to_besttrack_km=round(
                                   hav_km((lats[iy], lons[ix]), (near[1], near[2])), 1),
                               mslpmin_to_besttrack_km=round(
                                   hav_km((lats[my], lons[mx]), (near[1], near[2])), 1),
                               read_s=round(time.perf_counter() - t0, 2))
                except Exception as exc:                   # noqa: BLE001
                    row = dict(storm=d["name"], model=m, lead_h=L,
                               error=f"{type(exc).__name__}: {exc}"[:140],
                               read_s=round(time.perf_counter() - t0, 2))
                rows.append(row)
                print(json.dumps(row), flush=True)
    print(f"--- {len(rows)} records in {time.perf_counter() - t_all:.1f} s wall")
    ok = [r for r in rows if "error" not in r]
    if ok:
        d1 = [r["windmax_to_besttrack_km"] for r in ok]
        d2 = [r["mslpmin_to_besttrack_km"] for r in ok]
        print(f"wind-max distance: n={len(ok)} median={np.median(d1):.0f} km "
              f"p90={np.percentile(d1, 90):.0f} km; mslp-min median="
              f"{np.median(d2):.0f} km; median read "
              f"{np.median([r['read_s'] for r in ok]):.2f} s")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
             "models": models, "leads_h": leads,
             "storms": [e["details"]["name"] for e in chosen],
             "rows": rows}, indent=1))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
