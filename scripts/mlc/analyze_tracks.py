"""mlc-research PROOF step 3: summarise TempestExtremes tracks and check known storms.

Reads the StitchNodes CSV (--out_file_format csv --out_seconds) and reports track
counts, lifetimes, displacement, explosive-deepening ("bomb") tags, plus a check
against named storms.  Also reads the gridded ERA5 files that were tracked so the
tracked central pressure can be compared with the raw field minimum in a box.

  python scripts/mlc/analyze_tracks.py --tracks data/mlc/run_octnov/tracks_vor.csv \
      --data data/mlc/nov23 --from 2023-11-01 --to 2023-11-30
"""
from __future__ import annotations

import argparse
import glob

import numpy as np
import pandas as pd

R_EARTH = 6371.0
G0 = np.deg2rad(60.0)  # Sanders & Gyakum reference latitude


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dp = p2 - p1
    dl = np.deg2rad(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


def storm_targets() -> list[dict]:
    """Named storms to look for, verified against the tracked field.

    Ciaran is pinned by its documented landfall on the Brittany coast
    (Finistere, ~48.4N 4.6W) on 2 Nov 2023 rather than by a pressure value,
    so the check is "does a track pass through the landfall point at the right
    time, and what central pressure / deepening rate does ERA5 give it".
    """
    return [
        dict(name="Ciaran (landfall Finistere, France, 2 Nov 2023)",
             lat=48.4, lon=-4.6, radius_km=450.0,
             start="2023-11-01", end="2023-11-03",
             box=(30.0, 62.0, -60.0, 5.0), box_start="2023-10-29", box_end="2023-11-03",
             note="Storm Ciaran (Met Office/Met Eireann naming, 1-2 Nov 2023): a bomb cyclone "
                  "that crossed the Atlantic from the remnants of Tropical Philippe and made "
                  "landfall in Finistere, France, on 2 Nov 2023 with violent winds in Brittany, "
                  "the Channel/North Sea coasts and southern England."),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", required=True)
    ap.add_argument("--data", default="", help="dir of tracked ERA5 files (for the raw-field check)")
    ap.add_argument("--from", dest="tfrom", required=True)
    ap.add_argument("--to", dest="tto", required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.tracks, skipinitialspace=True)
    df["time"] = pd.to_datetime(dict(year=df.year, month=df.month, day=df.day, hour=df.hour,
                                     minute=df.minute if "minute" in df else 0,
                                     second=df.second if "second" in df else 0))
    t0, t1 = pd.Timestamp(args.tfrom), pd.Timestamp(args.tto) + pd.Timedelta(days=1)  # exclusive

    # keep tracks that have at least one point inside the reporting window
    keep = df[(df.time >= t0) & (df.time < t1)].track_id.unique()
    d = df[df.track_id.isin(keep)].sort_values(["track_id", "time"])

    rows = []
    for tid, g in d.groupby("track_id"):
        g = g.drop_duplicates("time").sort_values("time")
        times = g.time.values
        lat, lon, msl = g.lat.values, g.lon.values, g.msl.values
        life_h = (times[-1] - times[0]) / np.timedelta64(1, "h")
        steps = life_h / 6.0 + 1
        disp = hav(lat[0], lon[0], lat[-1], lon[-1])
        path = float(np.sum(hav(lat[:-1], lon[:-1], lat[1:], lon[1:]))) if len(lat) > 1 else 0.0
        kmin = int(np.argmin(msl))
        # 24 h pressure tendency (4 x 6 h steps), Sanders & Gyakum scaling
        fall = np.full(len(msl), np.nan)
        if len(msl) > 4:
            fall[4:] = msl[:-4] - msl[4:]          # positive = deepening
        kfall = int(np.nanargmax(fall)) if np.isfinite(fall).any() else 0
        latfall = float(lat[kfall])
        threshold = 24.0 * abs(np.sin(np.deg2rad(latfall))) / np.sin(G0)
        rows.append(dict(
            track_id=int(tid), start=pd.Timestamp(times[0]), end=pd.Timestamp(times[-1]),
            steps=int(steps), lifetime_h=float(life_h), min_msl=float(msl[kmin]),
            min_lat=float(lat[kmin]), min_lon=float(lon[kmin]), min_time=pd.Timestamp(times[kmin]),
            max_vor850=float(g.vor850.max()) if "vor850" in g else np.nan,
            displacement_km=float(disp), path_km=path,
            max_24h_fall=float(np.nanmax(fall)) if np.isfinite(fall).any() else 0.0,
            bomb_ratio=float((np.nanmax(fall) / threshold) if threshold > 0 else np.nan),
            bomb_lat=latfall,
            abs_lat_mean=float(np.mean(np.abs(lat))),
        ))
    t = pd.DataFrame(rows).sort_values("min_msl")

    print(f"# tracks file: {args.tracks}")
    print(f"# window {args.tfrom} .. {args.tto} (tracks with >=1 point in window)")
    print(f"tracks: {len(t)}   points: {len(d)}")
    print(f"lifetime h: median {t.lifetime_h.median():.0f}  mean {t.lifetime_h.mean():.1f}  "
          f"max {t.lifetime_h.max():.0f}")
    for h in (24, 48, 72, 96):
        print(f"  lifetime >= {h:>2} h: {int((t.lifetime_h >= h).sum()):4d} "
              f"({(t.lifetime_h >= h).mean()*100:.0f} %)")
    print(f"displacement km: median {t.displacement_km.median():.0f}  "
          f"p90 {t.displacement_km.quantile(0.9):.0f}  "
          f">=1000 km: {int((t.displacement_km >= 1000).sum())}")
    print(f"min MSL hPa: median {t.min_msl.median():.1f}  deepest {t.min_msl.min():.1f}")
    print(f"bomb (24 h fall >= 24 hPa*sin(lat)/sin60): {int((t.bomb_ratio >= 1).sum())} tracks")
    print(f"mean |lat| of tracks: {t.abs_lat_mean.mean():.1f} deg; "
          f"in 25-70 deg band: {int(((t.abs_lat_mean >= 25) & (t.abs_lat_mean <= 70)).sum())}")

    print("\n## deepest 10 tracks")
    cols = ["track_id", "start", "end", "lifetime_h", "min_msl", "min_lat", "min_lon",
            "displacement_km", "max_24h_fall", "bomb_ratio"]
    print(t[cols].head(10).to_string(index=False))

    for tgt in storm_targets():
        print(f"\n## known-storm check: {tgt['name']}")
        sel = t[t.track_id.isin([int(r0.track_id) for _, r0 in d.iterrows()
                                 if tgt["start"] <= str(r0.time)[:10] <= tgt["end"]
                                 and hav(r0.lat, r0.lon, tgt["lat"], tgt["lon"]) <= tgt["radius_km"]])]
        if sel.empty:
            print("  NOT FOUND within 800 km / window")
            continue
        r = sel.iloc[0]
        print(f"  FOUND track {r.track_id} (a point within {tgt['radius_km']:.0f} km of "
              f"{tgt['lat']}N {tgt['lon']}E): {r.start} -> {r.end} "
              f"({r.lifetime_h:.0f} h, {r.steps:.0f} steps)")
        print(f"  deepest along track: {r.min_msl:.1f} hPa at {r.min_lat:.2f}N {r.min_lon:.2f}E "
              f"on {r.min_time}")
        print(f"  displacement {r.displacement_km:.0f} km, path {r.path_km:.0f} km, "
              f"max 24 h fall {r.max_24h_fall:.1f} hPa "
              f"(= {r.bomb_ratio:.2f}x the bomb threshold at {r.bomb_lat:.1f}N)")
        print(f"  reference: {tgt['note']}")
        # whole-track point list for the named storm
        pts = d[d.track_id == r.track_id][["time", "lat", "lon", "msl", "vor850"]]
        print("  track points:")
        print(pts.to_string(index=False))

    if args.data:
        print("\n## raw-field check (ERA5 field minima, independent of the tracker)")
        import xarray as xr
        files = sorted(glob.glob(f"{args.data}/etcc_*.nc"))
        ds = xr.open_mfdataset(files, combine="nested", concat_dim="time", parallel=False).load()
        lat, lon = ds.lat.values, ds.lon.values
        for tgt in storm_targets():
            w = ds.msl.sel(time=slice(tgt["box_start"], tgt["box_end"]))
            la = (lat >= tgt["box"][0]) & (lat <= tgt["box"][1])
            lo = (lon >= tgt["box"][2]) & (lon <= tgt["box"][3])
            sub = w.values[:, la][:, :, lo]
            print(f"  {tgt['name']}:")
            for i, tt in enumerate(w.time.values):
                k = np.unravel_index(int(np.nanargmin(sub[i])), sub[i].shape)
                print(f"    {str(tt)[:16]}  min {sub[i][k]:.1f} hPa at "
                      f"{lat[la][k[0]]:.2f}N {lon[lo][k[1]]:.2f}E")
            sub2 = w.values[:, (lat >= 45) & (lat <= 58)][:, :, (lon >= -25) & (lon <= 5)]
            k = np.unravel_index(int(np.nanargmin(sub2)), sub2.shape)
            print(f"    storm-core min over 45-58N/25W-5E: {sub2[k]:.1f} hPa at "
                  f"{str(w.time.values[k[0]])[:16]}")


if __name__ == "__main__":
    main()
