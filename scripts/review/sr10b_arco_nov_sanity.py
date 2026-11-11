#!/usr/bin/env python3
"""Sanity: is live ARCO NW Australia Nov 1-6 real data or NaN fill?"""
import xarray as xr, numpy as np, pandas as pd
from extremeweatherbench import inputs

t = xr.open_zarr(inputs.ARCO_ERA5_FULL_URI, storage_options={"token": "anon"},
                 consolidated=False)["2m_temperature"]
if "valid_time" in t.dims:
    t = t.rename({"valid_time": "time"})
t = t.sortby(t.latitude, ascending=False).sortby("longitude")
la, lo = t.latitude.values, t.longitude.values
ilat = np.where((la >= -25) & (la <= -13))[0]
ilon = np.where((lo >= 117) & (lo <= 131))[0]
s = t.sel(time=slice("2026-11-01", "2026-11-06T23")).isel(latitude=ilat,
                                                           longitude=ilon).compute()
v = s.values
print("nan frac:", float(np.isnan(v).mean()),
      "min/max K:", np.nanmin(v), np.nanmax(v),
      "= gridcellmax C:", round(float(np.nanmax(v)) - 273.15, 1))
# warmest daily-mean 6h-anchor per day over land-ish subset
d = pd.DataFrame({"t": v.reshape(v.shape[0], -1).mean(axis=1)},
                 index=pd.to_datetime(s.time.values))
print(d.groupby(d.index.date).agg(["min", "max"]).round(2))
