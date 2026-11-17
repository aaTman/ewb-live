"""mlc-research PROOF step 1: extract one month of ERA5 (ARCO) for TempestExtremes.

Writes TE-style netCDF files (one per day, 6-hourly steps) with:
  msl(time, lat, lon)        mean sea level pressure, Pa
  vor850(time, lat, lon)     relative vorticity at 850 hPa, 1/s
  u850/v850(time, lat, lon)  850 hPa winds, m/s
Longitudes are shifted to [-180,180) (the EWB Live bbox convention), lat/lon
dimension names are TE's defaults, so no --latname/--lonname plumbing is needed.

Usage: python extract_month.py --year 2023 --month 11 --out data/mlc/202311
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import time

import numpy as np
import pandas as pd
import xarray as xr

ARCO = os.environ.get("EWB_LIVE_ARCO_URI",
                      "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3")
A_EARTH = 6.371e6


def rel_vorticity(u: xr.DataArray, v: xr.DataArray) -> xr.DataArray:
    """z-relative vorticity on a 0.25 deg lat-lon grid:

        zeta = [ d(v)/d(lambda) - d(u*cos(phi))/d(phi) ] / (a * cos(phi))

    longitude derivative is periodic (np.roll), latitude derivative is central
    with one-sided edges; the polar cap (|lat| > 87 deg, where 1/cos blows up)
    is set to NaN — the tracker only searches |lat| <= 80 deg anyway.
    """
    dlat = np.deg2rad(0.25)
    dlon = np.deg2rad(0.25)
    u_a, v_a = u.load().values, v.load().values
    coslat = np.cos(np.deg2rad(u.latitude.values)).astype(np.float64)
    c = coslat.reshape(1, -1, 1)  # broadcast over (time, lat, lon)
    dvdl = (np.roll(v_a, -1, axis=-1) - np.roll(v_a, 1, axis=-1)) / (2.0 * dlon)
    ucos = u_a * c
    dudc = (ucos[..., 2:, :] - ucos[..., :-2, :]) / (2.0 * dlat)
    dudc = np.concatenate([dudc[..., :1, :], dudc, dudc[..., -1:, :]], axis=-2)
    zeta = (dvdl - dudc) / (A_EARTH * c)
    zeta[..., np.abs(u.latitude.values) > 87.0, :] = np.nan
    return xr.DataArray(zeta.astype(np.float32), dims=u.dims, coords=u.coords)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--month", type=int)
    ap.add_argument("--start", default="", help="ISO start (overrides --year/--month)")
    ap.add_argument("--end", default="", help="ISO end (inclusive)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--nowinds", action="store_true", help="MSLP only")
    ap.add_argument("--days", type=int, default=0, help="limit to the first N days (smoke test)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    if args.start:
        start = dt.datetime.fromisoformat(args.start)
        end = dt.datetime.fromisoformat(args.end)
    else:
        start = dt.datetime(args.year, args.month, 1)
        end = (pd.Timestamp(start) + pd.offsets.MonthEnd(1)).to_pydatetime()
        if args.days:
            end = start + dt.timedelta(days=args.days - 1, hours=18)
    times = pd.date_range(start, end, freq="6h")

    ds = xr.open_zarr(ARCO, storage_options={"token": "anon"}, consolidated=False,
                      decode_times=True)
    sel = ds.sel(time=times.to_numpy())
    lev = ds.level.values.astype(float)
    k850 = float(lev[int(np.where(np.isclose(lev, 850.0))[0][0])])  # 850 hPa label
    vars_ = ["mean_sea_level_pressure"] if args.nowinds else [
        "mean_sea_level_pressure", "u_component_of_wind", "v_component_of_wind"]
    sub = sel[vars_]
    if not args.nowinds:
        sub = sub.sel(level=[k850])
        assert float(sub.level.values[0]) == 850.0
    # drop the duplicated polar rows: TE assumes a regular lat-lon lattice and
    # the tracker only searches |lat| <= 80 deg.
    sub = sub.sel(latitude=slice(88.0, -88.0))

    print(f"extract {start:%Y-%m-%d}..{end:%Y-%m-%d} n={len(times)} steps", flush=True)
    t0 = time.time()
    # stream day by day so peak memory stays ~1 GB/step
    for day, grp in sub.groupby(sub.time.dt.date):
        d = grp.load()
        out = xr.Dataset()
        # MSLP in hPa: TempestExtremes deltas/thresholds are in file units,
        # so hPa keeps the DetectNodes command line readable (delta 1.0 = 1 hPa).
        out["msl"] = (d["mean_sea_level_pressure"] / 100.0).rename("msl").assign_attrs(units="hPa")
        if not args.nowinds:
            u850 = d["u_component_of_wind"].isel(level=0).rename("u850")
            v850 = d["v_component_of_wind"].isel(level=0).rename("v850")
            out["u850"] = u850
            out["v850"] = v850
            out["vor850"] = rel_vorticity(u850, v850).rename("vor850").assign_attrs(units="1/s")
        # 0..360 -> -180..180
        out = out.assign_coords(longitude=(out.longitude + 180.0) % 360.0 - 180.0).sortby("longitude")
        out = out.rename(latitude="lat", longitude="lon").transpose("time", "lat", "lon")
        enc = {v: {"zlib": True, "complevel": 4, "_FillValue": False} for v in out}
        f = f"{args.out}/etcc_{day:%Y%m%d}.nc"
        out.to_netcdf(f, encoding=enc)
        print(f"  {f}  msl[{float(out.msl.min()):.1f},{float(out.msl.max()):.1f}] hPa"
              + (f"  vor850 max={float(out.vor850.max()):.2e} 1/s" if "vor850" in out else ""),
              flush=True)
    print(f"wrote {args.out} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
