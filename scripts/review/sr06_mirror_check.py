import xarray as xr
import numpy as np
m = xr.open_zarr("/opt/ewb-live/data/cache/truth/era5.zarr", consolidated=False)["2m_temperature"]
print("mirror lon head:", m.longitude.values[:5], "nan count:", int(np.isnan(m.longitude.values).sum()))
print("mirror lat nan:", int(np.isnan(m.latitude.values).sum()), "ntime", m.time.size)
from extremeweatherbench import inputs
a = xr.open_zarr(inputs.ARCO_ERA5_FULL_URI, storage_options={"token": "anon"}, consolidated=False)["2m_temperature"]
print("ARCO lon head:", a.longitude.values[:5], "lat0", float(a.latitude[0]))
mlon = np.nan_to_num(m.longitude.values, nan=0.0)
lat_i = int(np.argmin(abs(m.latitude.values - 40.0)))
lon_i = int(np.argmin(abs(mlon - 0.0)))
v_m = float(m.isel(latitude=lat_i, longitude=lon_i).sel(time="2026-08-15T12"))
lat_j = int(np.argmin(abs(a.latitude.values - 40.0)))
lon_j = int(np.argmin(abs(a.longitude.values - 0.0)))
v_a = float(a.isel(latitude=lat_j, longitude=lon_j).sel(time="2026-08-15T12"))
print("mirror 40N 0E:", v_m, " ARCO:", v_a)
# global field diff on one step
mm = m.sel(time="2026-08-15T12").values
aa = a.sel(time="2026-08-15T12").sortby("latitude", ascending=False).sortby("longitude").values
print("field maxdiff:", float(np.nanmax(np.abs(mm - aa))), "mirror nans in field:", int(np.isnan(mm).sum()))
