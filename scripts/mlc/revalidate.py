"""Re-validate the midlat_cyclone lane on ERA5 ARCO after the reader fix.

Runs the production lane (``ewb_live.events.mlc.scan_mlc_events``) over a window
that goes through ``ewb_live.sources.mlc_era5.read_mslp_window`` — the ARCO read
path, the one the 0-event blocker lived on — and reports the funnel, the event
list, the known-storm check, an independent "is the reported central pressure
really the field minimum there" check, timing and peak RSS.

Run on the ewb LXC (it reads ARCO; scratch is a JSON summary only):

    .venv/bin/python scripts/mlc/revalidate.py --start 2023-10-26 --end 2023-11-30 \
        --out tmp/proof.json --ciaran
    .venv/bin/python scripts/mlc/revalidate.py --start 2026-08-19 --end 2026-11-17 \
        --out tmp/dev90.json --top 10

``--ciaran`` adds Storm Ciaran (landfall 2 Nov 2023, Finistere), the PROOF's
known-storm check: the event whose track passes nearest 48.4 N 4.6 W within 12 h
of 2023-11-02 06Z, with its min MSLP, lifetime, displacement and bomb ratio.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ewb_live.events import mlc as M          # noqa: E402
from ewb_live.events import mlc_track as T    # noqa: E402
from ewb_live.events.validate import validate_event   # noqa: E402
from ewb_live.sources import mlc_era5         # noqa: E402

# Coarse open-ocean basins, just enough to eyeball a top-10 list.
BASINS = [
    (("N", -99, -25, 40, 62), "NW Atlantic / Canadianbanks"),
    (("N", -25, 0, 40, 62), "NE Atlantic / Bay of Biscay-NI"),
    (("N", 0, 35, 45, 70), "North Sea-Norwegian Sea-Baltic"),
    (("N", 35, 75, 40, 70), "Caspian-Kara inland corridor"),
    (("N", 75, 140, 40, 62), "E Asia / Sea of Okhotsk"),
    (("N", 140, 180, 35, 62), "NW Pacific / Kuroshio extension"),
    (("N", -180, -140, 35, 62), "NE Pacific / Gulf of Alaska"),
    (("N", -140, -99, 30, 50), "NE Pacific offshore CA-Baja"),
    (("N", 0, 75, 25, 45), "Mediterranean-Black Sea"),
    (("N", -60, -25, 25, 40), "Subtropical W Atlantic"),
    (("S", -80, -40, 35, 62), "SE South America / Argentina"),
    (("S", -40, 20, 35, 62), "St Helena-Africa S"),
    (("S", 20, 90, 30, 62), "Indian Ocean southern mid-lats"),
    (("S", 90, 180, 40, 68), "S Australia-Tasman NZ Pacific"),
    (("S", -180, -80, 40, 68), "SE Pacific / Drake"),
]


def place(lat: float, lon: float) -> str:
    hem = "N" if lat >= 0 else "S"
    for (h, w, e, s, n), name in BASINS:
        if h == hem and w <= lon < e and s <= abs(lat) < n:
            return name
    return "other ocean" if abs(lat) >= 25 else "low latitude"


def field_min(mslp, lats, lons, times, lat, lon, when, radius_deg=2.0,
              window_h=6.0):
    """Minimum MSLP in the ERA5 field near a fix, at that fix's time (+/-6 h).

    The independent check for "is the reported central pressure really the field
    minimum there". Time-limited on purpose: without it a deeper, unrelated low
    passing the same spot a day later would be counted.
    """
    k = [i for i, t in enumerate(times)
         if abs((t - when).total_seconds()) <= window_h * 3600]
    d = T.haversine_deg(lats[:, None], lons[None, :], lat, lon) <= radius_deg
    sub = mslp[k][:, d] if k else mslp[:0]
    return float(np.nanmin(sub)) if sub.size else float("nan")


def nearest_fix(track_rows, when, lat, lon, within_h=12.0):
    """(fix, distance km) of the track fix closest to (when, lat, lon)."""
    best, best_d = None, np.inf
    for (t, la, lo, _i, p) in track_rows:
        tt = dt.datetime.fromisoformat(t.replace("Z", ""))
        if abs((tt - when).total_seconds()) > within_h * 3600:
            continue
        d = T.haversine_km(lat, lon, la, lo)
        if d < best_d:
            best, best_d = (tt, la, lo, p, d), d
    return best, best_d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--ciaran", action="store_true")
    ap.add_argument("--chunk-hours", type=int, default=24)
    ap.add_argument("--lookback-days", type=float, default=6.0,
                    help="stitching context read before --start (the lane's own "
                         "default; mlc-impl's 71-event validation window had 0, "
                         "which counts lows born before --start as new events)")
    ap.add_argument("--read-end", default="",
                    help="read frames past --end (ISO datetime) without widening "
                         "the event window — reproduces mlc-impl's harness, whose "
                         "netCDFs ran to 2023-11-30T18 while events had to be born "
                         "by 2023-11-30 00Z")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end)

    t_start = time.perf_counter()
    read_end = (dt.datetime.fromisoformat(a.read_end) if a.read_end
                else dt.datetime.combine(end, dt.time()))
    times, mslp, lats, lons = mlc_era5.read_mslp_window(
        dt.datetime.combine(start, dt.time())
        - dt.timedelta(days=a.lookback_days),
        read_end, chunk_hours=a.chunk_hours)
    read_s = time.perf_counter() - t_start
    rss_read = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0
    topo = mlc_era5.load_orography()
    print(f"read {len(times)} frames {times[0]}..{times[-1]} "
          f"{mslp.shape} [{mslp.min():.1f},{mslp.max():.1f}] hPa in {read_s:.0f} s "
          f"({mslp.nbytes/1e9:.2f} GB, peak RSS {rss_read:.1f} GB)")

    t0 = time.perf_counter()
    evs = M.scan_mlc_events(start, end, fields=(times, mslp, lats, lons),
                            orography=topo)
    scan_s = time.perf_counter() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0
    st = M.lane_status()
    print(f"scan {scan_s:.0f} s ({scan_s/max(len(times),1):.2f} s/frame), "
          f"peak RSS {rss:.1f} GB")
    print("lane_status:", json.dumps(st))

    bad = [f"{e.id}: {v}" for e in evs for v in validate_event(e)]
    bombs = [e for e in evs if e.details.get("bomb")]
    msl = np.array([e.details["min_mslp_hPa"] for e in evs])
    disp = np.array([e.details["displacement_km"] for e in evs])
    life = np.array([e.details["lifetime_h"] for e in evs])
    glat = np.array([abs(e.details["genesis_lat"]) for e in evs])
    print(f"\nevents {len(evs)}  bombs {len(bombs)}  invalid {len(bad)}")
    if len(evs):
        print(f"min MSLP median {np.median(msl):.1f} deepest {msl.min():.1f} | "
              f"lifetime median {np.median(life):.0f} h | displacement median "
              f"{np.median(disp):.0f} km | mean |genesis lat| {glat.mean():.1f}")
    for w in bad:
        print("  INVALID", w)

    # seasonality sanity: a real mid-latitude lane is not flat across the window
    by_month: dict[str, list] = {}
    for e in evs:
        by_month.setdefault(f"{e.start:%Y-%m}", []).append(e)
    print("\nby genesis month  " + "  ".join(
        f"{m}: {len(v)} ev/{sum(1 for e in v if e.details['bomb'])} bomb/"
        f"{min(e.details['min_mslp_hPa'] for e in v):.0f} hPa"
        for m, v in sorted(by_month.items())))

    print(f"\ntop {a.top} by severity (depth x lifetime x bomb):")
    print(f"{'#':>2} {'genesis':16} {'last':10} {'min hPa':>8} {'life h':>7} "
          f"{'disp km':>8} {'bomb':>5}  genesis -> deepest (place)")
    rows = []
    for k, e in enumerate(sorted(evs, key=M.severity, reverse=True)[:a.top]):
        d = e.details
        deep = d["track"][min(range(len(d["track"])),
                             key=lambda i: d["track"][i][4])]
        p = place(d["genesis_lat"], d["genesis_lon"])
        p2 = place(deep[1], deep[2])
        # independent check: the reported central pressure IS the field minimum
        fmin = field_min(mslp, lats, lons, times, deep[1], deep[2],
                         dt.datetime.fromisoformat(deep[0].replace("Z", "")))
        rows.append({"start": f"{e.start:%Y-%m-%d}", "end": f"{e.end:%Y-%m-%d}",
                     "min_mslp_hPa": d["min_mslp_hPa"], "lifetime_h": d["lifetime_h"],
                     "displacement_km": d["displacement_km"], "bomb": d["bomb"],
                     "genesis": [d["genesis_lat"], d["genesis_lon"]],
                     "deepest": [deep[1], deep[2], deep[0]], "place": p,
                     "field_min_hPa": fmin})
        print(f"{k+1:2d} {e.start:%Y-%m-%d %H:%M}  {e.end - dt.timedelta(days=1):%Y-%m-%d} "
              f"{d['min_mslp_hPa']:8.1f} {d['lifetime_h']:7.0f} {d['displacement_km']:8.0f} "
              f"{'yes' if d['bomb'] else '-':>5}  {d['genesis_lat']:.1f}/{d['genesis_lon']:.1f}"
              f" -> {deep[1]:.1f}/{deep[2]:.1f} [{p} -> {p2}] field min {fmin:.1f}")

    ciaran = None
    if a.ciaran:
        when = dt.datetime(2023, 11, 2, 6)
        best, best_d = None, np.inf
        for e in evs:
            fix, d = nearest_fix(e.details["track"], when, 48.4, -4.6)
            if fix and d < best_d:
                best, best_d = (e, fix, d), d
        if best is None:
            print("\nCIARAN: NOT FOUND (no event passes within 12 h of the landfall point)")
        else:
            e, fix, d = best
            ciaran = {"min_mslp_hPa": e.details["min_mslp_hPa"],
                      "lifetime_h": e.details["lifetime_h"],
                      "displacement_km": e.details["displacement_km"],
                      "max_24h_fall_hPa": e.details["max_24h_fall_hPa"],
                      "bomb": e.details["bomb"], "bomb_ratio": e.details["bomb_ratio"],
                      "fixes": e.details["n_fixes"], "nearest_fix": fix[0],
                      "km_to_landfall": d}
            print(f"\nCIARAN (nearest fix to 2023-11-02 06Z, 48.4N 4.6W): "
                  f"{fix[0]:%Y-%m-%d %H:%M} {fix[1]:.1f}/{fix[2]:.1f} "
                  f"{fix[3]:.1f} hPa, {d:.0f} km to the landfall point; "
                  f"event min MSLP {e.details['min_mslp_hPa']:.1f} hPa, "
                  f"lifetime {e.details['lifetime_h']:.0f} h, displacement "
                  f"{e.details['displacement_km']:.0f} km, 24 h fall "
                  f"{e.details['max_24h_fall_hPa']:.1f} hPa = "
                  f"{e.details['bomb_ratio']:.2f}x the bomb threshold, "
                  f"{e.details['n_fixes']} fixes")

    summary = {"window": [a.start, a.end], "frames": len(times),
               "lookback_days": a.lookback_days, "read_end": a.read_end or None,
               "frame_range": [str(times[0]), str(times[-1])] if times else None,
               "read_s": round(read_s, 1), "scan_s": round(scan_s, 1),
               "peak_rss_gb": round(rss, 1), "lane_status": st,
               "n_events": len(evs), "n_bombs": len(bombs), "n_invalid": len(bad),
               "invalid": bad[:5],
               "median_min_mslp_hPa": float(np.median(msl)) if len(evs) else None,
               "deepest_mslp_hPa": float(msl.min()) if len(evs) else None,
               "median_lifetime_h": float(np.median(life)) if len(evs) else None,
               "median_displacement_km": float(np.median(disp)) if len(evs) else None,
               "mean_abs_genesis_lat": float(glat.mean()) if len(evs) else None,
               "by_month": {m: {"events": len(v),
                                "bombs": sum(1 for e in v if e.details["bomb"]),
                                "deepest": min(e.details["min_mslp_hPa"] for e in v)}
                            for m, v in sorted(by_month.items())},
               "top": rows, "ciaran": ciaran,
               "host": os.uname().nodename, "cores": os.cpu_count(),
               "run_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(summary, indent=1, default=str) + "\n")
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
