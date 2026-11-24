"""Pin down the tp accumulation semantics in the ARCO full store (run on ewb)."""
from __future__ import annotations

import numpy as np
import xarray as xr
import extremeweatherbench.inputs as I

da = xr.open_zarr(I.ARCO_ERA5_FULL_URI, storage_options={"token": "anon"},
                  chunks=None)["total_precipitation"]

day = da.sel(time=slice("1990-01-31", "1990-02-01")).load()
v = day.values * 1000.0  # mm
# find the wettest cell for the day by the 24 hourly-increment sum
inc = v[1:].sum(axis=0)
j = np.unravel_index(np.nanargmax(inc), inc.shape)
print("wet cell index", j, "lat", float(day.latitude[j[0]]),
      "lon", float(day.longitude[j[1]]))
print("hourly tp (mm) at each timestamp:")
for k, t in enumerate(day.time.values):
    print(f"  {str(t)[:16]}  {v[k][j]:9.3f}")
print("sum of all 25 =", float(v.sum(axis=0)[j]), " sum of [1:] =", float(inc[j]))
print("6-hourly only (00,06,12,18,24):",
      [float(v[k][j]) for k in (0, 6, 12, 18, 24)])
# grid-wide: compare candidate daily totals
allsum = v.sum(axis=0)
inc1 = v[1:].sum(axis=0)
six = v[[6, 12, 18, 24]].sum(axis=0)
print("global mean: sum25", float(allsum.mean() * 1e3), "mm  sum24[1:]",
      float(inc1.mean() * 1e3), "  4x6h", float(six.mean() * 1e3))
print("global max:  sum25", float(allsum.max() * 1e3), " sum24[1:]",
      float(inc1.max() * 1e3), "  4x6h", float(six.max() * 1e3))
print("n cells where 4x6h > sum24[1:] + 1mm:",
      int((six - inc1 > 1e-3).sum()), "of", inc1.size)
# does any 6-hourly value fall below the following hourly increments?
print("tp at 06 >= sum(hourly 01..06)?",
      float((v[6] >= v[1:7].sum(axis=0)).mean()))
