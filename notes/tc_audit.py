#!/usr/bin/env python
"""Independent audit of the tropical-cyclone lane (agent ne-fix-tc, 2026-11-19).

Runs on the ewb LXC against a built tree; READ-ONLY. It re-derives the storm
universe and every published TC event straight from the IBTrACS archive (the
upstream source of truth) instead of trusting the lane's own tests, then prints
a defect table:

  A  universe      — storms with any fix in the window vs published events
  B  drops         — each storm that did not become an event, with the rule that
                     killed it (recomputed independently of events/tc.py)
  C  wind basis    — per-fix agency mixing inside one storm + the 10-min/3-min
                     winds that the Saffir-Simpson bands were built for
  D  peak/category— published peak wind + category vs the 1-min (USA_) value
  E  reach         — latest fix per source file (ALL vs last3years)

Usage: notes/tc_audit.py --cache data/cache/ibtracs/fixes_*.parquet \
        --events data/results/events.json --start 2026-08-20 --end 2026-11-18
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ewb_live.events.tc import (MIN_TS_FIXES, MIN_TS_HOURS, MIN_TS_WIND_KT,  # noqa: E402
                                NON_TROPICAL_NATURE, category)

# 10-minute (and 3-minute) sustained wind -> 1-minute equivalent, WMO/NHC
# practice factors used by IBTrACS's own FAQ (10-min ~ 0.88 x 1-min; the NHC
# 3-min to 1-min adjustment is ~1.14). Kept coarse on purpose: the audit only
# needs to know whether a category could flip across a Saffir-Simpson band.
BASIS_FACTOR = {"USA": 1.0, "JTWC": 1.0, "TOKYO": 1.14, "KMA": 1.14,
                "CMA": 1.14, "HKO": 1.14, "BOM": 1.14, "NADI": 1.14,
                "WELLINGTON": 1.14, "REUNION": 1.14, "DS824": 1.14,
                "MLC": 1.14, "NEWDELHI": 0.88, "WMO": 1.14, "NEUMANN": 1.14}


def load_raw(uri_or_path: str) -> pd.DataFrame:
    """Raw IBTrACS CSV (path or URL) -> the columns the audit needs."""
    df = pd.read_csv(uri_or_path, low_memory=False)
    df = df[df["SEASON"].astype(str).str.strip() != "Year"]
    keep = ["SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "NATURE", "LAT", "LON",
            "TRACK_TYPE", "STATUS", "USA_WIND", "USA_PRES", "WMO_WIND",
            "TOKYO_WIND", "NEWDELHI_WIND", "CMA_WIND", "BOM_WIND"]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()
    df["time"] = pd.to_datetime(df["ISO_TIME"], errors="coerce")
    for c in ("LAT", "LON", "USA_WIND", "WMO_WIND", "TOKYO_WIND",
              "NEWDELHI_WIND", "CMA_WIND", "BOM_WIND", "SEASON"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["SID", "time", "LAT", "LON"])
    df["SID"] = df["SID"].astype(str).str.strip()
    df = df[df["SID"].str.match(r"^\d{7}[NS]\d{5}$")]
    df["NATURE"] = df["NATURE"].astype(str).str.strip().str.upper()
    return df.sort_values(["SID", "time"]).reset_index(drop=True)


def qualify(sid: str, g: pd.DataFrame, win, wind_col: str) -> dict:
    """Independent re-derivation of events/tc.py's qualification rule."""
    g = g[(g["time"] >= win[0]) & (g["time"] <= win[1])].sort_values("time")
    w = g[wind_col].astype(float)
    trop = ~g["nature"].isin(NON_TROPICAL_NATURE)
    qual = (w >= MIN_TS_WIND_KT) & trop & g["lat"].notna() & g["lon"].notna()
    q = g[qual]
    res = {"sid": sid, "name": str(g["name"].iloc[-1]).strip(),
           "basin": str(g["basin"].iloc[-1]).strip(),
           "n_fix": len(g), "n_qual": int(qual.sum()),
           "max_wind": float(w.max()) if w.notna().any() else None,
           "nature": sorted(set(g["nature"])),
           "track_type": sorted(set(g["track_type"].astype(str))),
           "status": sorted(set(g["status"].astype(str))) if "status" in g else [],
           "reason": None}
    if len(g) < MIN_TS_FIXES:
        res["reason"] = f"<{MIN_TS_FIXES} fixes in window"
        return res
    if res["n_qual"] < MIN_TS_FIXES:
        # split the two sub-causes for the report
        trop_ok = trop.sum()
        res["reason"] = (f"<{MIN_TS_FIXES} fixes at >=34 kt "
                         f"(max {res['max_wind']:.0f} kt)" if res["n_qual"] == 0 or trop_ok >= MIN_TS_FIXES
                         else f"qualifying fixes excluded as {sorted(NON_TROPICAL_NATURE)}")
        return res
    span = (q["time"].max() - q["time"].min()).total_seconds() / 3600.0
    if span < MIN_TS_HOURS:
        res["reason"] = f"qualifying fixes span {span:.0f} h < {MIN_TS_HOURS:.0f} h"
        return res
    res["start"] = q["time"].min()
    res["end"] = q["time"].max()
    res["peak"] = float(w[(g["time"] >= q["time"].min()) &
                          (g["time"] <= q["time"].max())].max())
    res["cat_1min"] = category(res["peak"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, help="lane parquet cache")
    ap.add_argument("--events", required=True, help="built events.json")
    ap.add_argument("--start", default="2026-08-20")
    ap.add_argument("--end", default="2026-11-18")
    ap.add_argument("--raw", default="", help="raw CSV path/URL for the independent re-read")
    a = ap.parse_args()

    t0 = pd.Timestamp(a.start)
    t1 = pd.Timestamp(a.end) + pd.Timedelta(days=1)
    win = (t0, t1)

    lane = pd.read_parquet(a.cache)
    lane = lane[(lane["time"] >= t0) & (lane["time"] <= t1)]
    ev = json.loads(Path(a.events).read_text())
    evs = ev["events"] if isinstance(ev, dict) and "events" in ev else ev
    tc = [e for e in evs if e["event_type"] == "tropical_cyclone"]
    by_sid = {e["details"]["sid"]: e for e in tc}

    src = lane.copy()
    src.columns = [c.lower() for c in src.columns]
    wind_col = "wind_kt"
    if a.raw:
        raw = load_raw(a.raw)
        raw = raw[(raw["time"] >= t0) & (raw["time"] <= t1)]
        src = raw.rename(columns={"SID": "sid", "LAT": "lat", "LON": "lon",
                                  "NAME": "name", "BASIN": "basin",
                                  "NATURE": "nature", "TRACK_TYPE": "track_type",
                                  "STATUS": "status"})
        src.columns = [c.lower() for c in src.columns]
        wind_col = "usa_wind"
        print(f"# independent source read: {a.raw} — {len(raw)} fixes / "
              f"{raw['SID'].nunique()} storms (lane cache: {len(lane)} fixes / "
              f"{lane['sid'].nunique()} storms)")
    else:
        print(f"# lane cache only: {len(lane)} fixes / {lane['sid'].nunique()} storms")

    print(f"# published tropical_cyclone events: {len(tc)}")

    # --- A/B: universe + drop reasons ------------------------------------------
    rows = []
    for sid, g in src.groupby("sid", sort=True):
        rows.append(qualify(str(sid), g.copy(), win, wind_col))
    r = pd.DataFrame(rows)
    should = r[r["reason"].isna()]
    print(f"\n## A. independent qualification: {len(should)}/{len(r)} storms qualify "
          f"in {a.start}..{a.end}")
    missing = should[~should["sid"].isin(by_sid)]
    extra = [s for s in by_sid if s not in set(should["sid"])]
    print(f"## B. qualifying but NOT published: {len(missing)}")
    for _, x in missing.iterrows():
        print(f"   {x['sid']} {x['name'][:14]:<14} {x['basin']:<3} "
              f"peak {x['peak']:.0f} kt {x['cat_1min']:<6} "
              f"{x['start']:%Y-%m-%d}..{x['end']:%Y-%m-%d} nfix {x['n_fix']}")
    print(f"## B2. published but NOT qualifying independently: {len(extra)}")
    for s in extra:
        e = by_sid[s]
        print(f"   {s} {e['details']['name'][:14]:<14} peak {e['peak_value']} kt "
              f"{e['details']['peak_category']}")
    print("## B3. dropped storms (independent reason)")
    for _, x in r[r["reason"].notna()].sort_values("sid").iterrows():
        pub = "PUBLISHED" if x["sid"] in by_sid else "         "
        mx = f"{x['max_wind']:.0f}" if x["max_wind"] == x["max_wind"] else "-"
        print(f"   {x['sid']} {str(x['name'])[:14]:<14} {str(x['basin']):<3} "
              f"{pub} nfix {x['n_fix']:>3} nqual {x['n_qual']:>3} maxkt {mx:>4}  "
              f"{x['reason']}; nature={x['nature']} track_type={x['track_type']}")

    # --- C: wind basis mixing --------------------------------------------------
    print("\n## C. agency mixing / wind basis (lane cache)")
    mix = (lane.groupby("sid")["wind_source"].nunique())
    mixed = mix[mix > 1]
    print(f"   storms with >1 wind agency inside the storm: {len(mixed)}/{len(mix)}")
    for sid in mixed.index[:20]:
        g = lane[lane["sid"] == sid]
        vc = g["wind_source"].value_counts().to_dict()
        nm = str(g['name'].iloc[-1])
        pk = g.loc[g["wind_kt"].idxmax(), ["wind_kt", "wind_source"]]
        print(f"     {sid} {nm[:12]:<12} {vc}  peak {pk['wind_kt']:.0f} kt from {pk['wind_source']}")
    nonusa = lane[lane["wind_source"] != "USA"]
    print(f"   fixes with a non-1-min (non-USA) wind: {len(nonusa)}/{len(lane)} "
          f"({100 * len(nonusa) / max(len(lane), 1):.1f}%)")
    if len(nonusa):
        # storms whose PUBLISHED peak came from a 10-min agency, i.e. a
        # Saffir-Simpson band applied to the wrong averaging period
        bad = []
        for sid, e in by_sid.items():
            if e["details"]["wind_source"] == "USA":
                continue
            bad.append((sid, e["details"]["name"], e["peak_value"],
                        e["details"]["peak_category"], e["details"]["wind_source"]))
        print(f"   published storms on a non-USA wind basis: {len(bad)}")
        for b in bad:
            f = BASIS_FACTOR.get(b[4], 1.14)
            alt = category((b[2] or 0) * f)
            flag = "FLIPS" if alt != b[3] else "     "
            print(f"     {b[0]} {b[1][:12]:<12} {b[2]} kt {b[4]} -> {b[3]}"
                  f"   1-min-equiv {alt} {flag}")

    # --- D: peak + category vs the independent 1-min wind ---------------------
    if a.raw:
        print("\n## D. published peak/category vs raw USA_WIND (independent)")
        raw_all = load_raw(a.raw)
        n_bad = 0
        for sid, e in sorted(by_sid.items()):
            g = raw_all[raw_all["SID"] == sid]
            if not len(g):
                print(f"   {sid} {e['details']['name'][:12]:<12} NOT IN RAW SOURCE")
                n_bad += 1
                continue
            s = pd.Timestamp(e["start"])
            en = pd.Timestamp(e["end"])
            gw = g[(g["time"] >= s) & (g["time"] < en)]["USA_WIND"].max()
            pub = e["peak_value"] or 0.0
            cat_pub = e["details"]["peak_category"]
            cat_raw = category(gw)
            if not np.isfinite(gw) or abs(gw - pub) > 0.51 or cat_raw != cat_pub:
                n_bad += 1
                print(f"   {sid} {e['details']['name'][:12]:<12} published "
                      f"{pub} kt {cat_pub} | raw USA_WIND {gw} kt {cat_raw}")
        print(f"   mismatches: {n_bad}/{len(by_sid)}")

    # --- E: reach --------------------------------------------------------------
    print("\n## E. source reach")
    latest = pd.to_datetime(lane["time"], utc=True, errors="coerce").max()
    print(f"   latest fix in the lane cache: {latest}  (now {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
