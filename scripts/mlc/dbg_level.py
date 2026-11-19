import numpy as np, xarray as xr
uri = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ds = xr.open_zarr(uri, storage_options={"token": "anon"}, consolidated=False, decode_times=True)
lv = ds.level.values
print("dtype", lv.dtype, "n", len(lv))
k = int(np.where(np.isclose(lv.astype(float), 850.0))[0][0])
print("k", k, "value", lv[k])
import pandas as pd
t = np.array([np.datetime64("2023-11-01T00"), np.datetime64("2023-11-01T06")])
sub = ds[["mean_sea_level_pressure", "u_component_of_wind", "v_component_of_wind"]].sel(time=t)
s2 = sub.sel(level=[k])
print("selected level:", s2.level.values, s2.u_component_of_wind.dims, float(s2.u_component_of_wind.min()), float(s2.u_component_of_wind.max()))
