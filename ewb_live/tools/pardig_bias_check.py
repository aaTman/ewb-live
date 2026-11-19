"""PARDIG-vs-IMERG bias check on the published flood events (owner step 1).

Run on the ewb LXC from the tree root:

    python -m ewb_live.tools.pardig_bias_check [--events N] [--sample-days D]

Builds the PARDIG daily cache for the union of the event day windows (and a
handful of extra global sample days), joins it to the IMERG daily store on
the ERA5 0.25 deg lattice (the detector's own aggregate + cnt screen), and
prints per-event and per-latitude-band ratio statistics as JSON on stdout.
The human summary lives in notes/pardig_vs_imerg.md; this tool is the
reproducible measurement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import numpy as np

from ..events import flood
from ..sources import imerg, pardig

logger = logging.getLogger("pardig_bias")

BANDS = [(0.0, 23.5, "0-23.5"), (23.5, 45.0, "23.5-45"),
         (45.0, 60.0, "45-60"), (60.0, 67.0, "60-67")]


def _day_pair(day: dt.date, root) -> tuple[np.ndarray, np.ndarray] | None:
    """(imerg_025, pardig_025) mm/day for one UTC day, both on the ERA5
    lattice with the lane's cnt screen; None if either source lacks the day."""
    ip = imerg.daily_path(day, root)
    if not ip.exists():
        return None
    import xarray as xr
    with xr.open_dataset(ip) as ds:
        cnt = ds.precipitation_cnt.to_numpy()
        values = np.where(cnt >= imerg.STEPS_PER_DAY,
                          ds.precipitation.to_numpy().astype("float32"), np.nan)
        lats = ds.lat.to_numpy()
        lons = ds.lon.to_numpy()
    da = xr.DataArray(values[None], dims=("time", "lat", "lon"),
                      coords={"time": [np.datetime64(day)], "lat": lats, "lon": lons})
    i025 = flood.aggregate_to_era5_grid(da).values[0]
    try:
        p025 = xr.open_dataset(pardig.daily_path(day))["precip"].to_numpy().astype("float32")
    except FileNotFoundError:
        return None
    return i025, p025


def main(argv=None) -> int:
    from .._guard import require_ewb_host
    require_ewb_host()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", type=int, default=8, help="published flood events to probe")
    ap.add_argument("--sample-days", type=int, default=6, help="extra global sample days")
    ap.add_argument("--imerg-root", default="data/imerg")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    sel = json.load(open("data/results/events_selected.json"))
    events = [e for e in (sel["events"] if isinstance(sel, dict) else sel)
              if e.get("event_type") == "flood"]
    events.sort(key=lambda e: -(e.get("peak_value") or 0))
    picks = events[:args.events]
    # a high-latitude event if the top-N did not include one (the >67 deg caveat)
    if not any(float(e["bbox"][1]) > 50 or float(e["bbox"][0]) < -50 for e in picks):
        hi = [e for e in events if abs(float(e["bbox"][1])) > 50]
        if hi:
            picks.append(hi[0])

    days: set[dt.date] = set()
    for e in picks:
        d0 = dt.date.fromisoformat(e["start"][:10])
        n = max(1, (dt.date.fromisoformat(e["end"][:10]) - d0).days)
        days.update(d0 + dt.timedelta(days=k) for k in range(n))
    imerg_have = set()
    import glob as g
    from pathlib import Path as _P
    for p in g.glob(f"{args.imerg_root}/daily/imerg_*.nc"):
        s = _P(p).stem[-8:]
        try:
            imerg_have.add(dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])))
        except ValueError:
            continue
    sample = sorted(imerg_have)[-args.sample_days:]
    days.update(sample)
    logger.info("building PARDIG for %d days (events %d + samples)", len(days), len(picks))
    for d in sorted(days):
        try:
            pardig.build_daily(d)
        except pardig.PardigError as e:
            logger.warning("skip %s: %s", d, e)

    land = flood.land_mask(*flood.era5_grid())
    lats, lons = flood.era5_grid()
    band_idx = {name: np.where((np.abs(lats)[:, None] >= lo) & (np.abs(lats)[:, None] < hi))[0]
                for lo, hi, name in BANDS}

    out: dict = {"generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                 "events": [], "bands": {b: {"sum_p": 0.0, "sum_i": 0.0, "cells": 0,
                                             "wet_p": 0, "wet_i": 0} for b in band_idx},
                 "reach": {"imerg_last": f"{max(imerg_have)}", "pardig_last":
                           (f"{max(pardig.served_days())}" if pardig.served_days() else None)}}

    for e in picks:
        s, n_, w, ee = (float(v) for v in e["bbox"])
        d0 = dt.date.fromisoformat(e["start"][:10])
        n_days = max(1, (dt.date.fromisoformat(e["end"][:10]) - d0).days)
        ilat = np.where((lats <= max(s, n_)) & (lats >= min(s, n_)))[0]
        ilon = np.where((lons >= w) & (lons <= ee))[0] if w <= ee else \
            np.where((lons >= w) | (lons <= ee))[0]
        sp = si = 0.0
        per_day = []
        for k in range(n_days):
            d = d0 + dt.timedelta(days=k)
            pair = _day_pair(d, args.imerg_root)
            if pair is None:
                continue
            i025, p025 = (a[:, ilon][ilat] for a in pair)
            m = land[:, ilon][ilat]
            sp += float(np.nansum(p025[m & np.isfinite(i025)]))
            si += float(np.nansum(i025[m & np.isfinite(i025)]))
            per_day.append({"day": f"{d}", "pardig_mm": round(float(np.nansum(p025[m])), 1),
                            "imerg_mm": round(float(np.nansum(i025[m])), 1)})
        out["events"].append({"id": e["id"], "title": e.get("title"),
                              "start": e["start"], "days": n_days,
                              "bbox": [s, n_, w, ee],
                              "peak_mm": e.get("peak_value"),
                              "lat_mid": round((s + n_) / 2, 1),
                              "pardig_sum_mm": round(sp, 1), "imerg_sum_mm": round(si, 1),
                              "ratio": round(sp / si, 3) if si else None,
                              "per_day": per_day})

    for d in sample:
        pair = _day_pair(d, args.imerg_root)
        if pair is None:
            continue
        i025, p025 = pair
        for b, ii in band_idx.items():
            m = land[ii] & np.isfinite(i025[ii])
            acc = out["bands"][b]
            acc["sum_p"] += float(np.nansum(p025[ii][m]))
            acc["sum_i"] += float(np.nansum(i025[ii][m]))
            acc["cells"] += int(m.sum())
            acc["wet_p"] += int((p025[ii][m] >= 20.0).sum())
            acc["wet_i"] += int((i025[ii][m] >= 20.0).sum())
    for b in out["bands"]:
        a = out["bands"][b]
        a["ratio"] = round(a["sum_p"] / a["sum_i"], 3) if a["sum_i"] else None
        a["ge20_ratio"] = round(a["wet_p"] / a["wet_i"], 3) if a["wet_i"] else None
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
