import time
import numpy as np, xarray as xr
uri = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ds = xr.open_zarr(uri, storage_options={"token": "anon"}, decode_times=True, consolidated=False)
for v in ["mean_sea_level_pressure", "u_component_of_wind", "geopotential", "10m_u_component_of_wind"]:
    da = ds[v]
    print(v, da.dims, "chunks:", da.encoding.get("chunks"), da.dtype)
print("level values:", list(map(float, ds.level.values)))
t = ds.time.values
i0 = int(np.where(t == np.datetime64("2023-11-01T00"))[0][0])
print("index for 2023-11-01T00:", i0, "of", t.size)
# time cost of reading 2 global MSL steps
s = time.time()
msl = ds["mean_sea_level_pressure"].isel(time=[i0, i0 + 6]).load().values
print("msl 2 steps: %.1fs  min/max hPa: %.1f %.1f" % (time.time() - s, (msl.min()/100), (msl.max()/100)))
# time cost of reading u,v at 850 hPa for 2 steps
lv = [float(x) for x in ds.level.values]
k850 = lv.index(850.0)
s = time.time()
u = ds["u_component_of_wind"].isel(time=[i0, i0 + 6], level=[k850]).load().values
v = ds["v_component_of_wind"].isel(time=[i0, i0 + 6], level=[k850]).load().values
print("u,v@850 2 steps: %.1fs shapes %s" % (time.time() - s, u.shape))
# Ciaran sanity check: 2023-11-01 12Z ~ (51N, 16W) -> ARCO lon is 0..360
def val(da, lat, lon360, tidx):
    la = int(round((90 - lat) / 0.25)); lo = int(round(lon360 / 0.25))
    return float(da.isel(time=tidx, latitude=la, longitude=lo).load())
for d in [0, 1, 2, 3]:
    ti = i0 + 6 * d
    m = val(ds["mean_sea_level_pressure"], 51.0, 360 - 16, ti) / 100
    print(f"2023-11-0{1+d} 12Z  MSL@51N,16W = {m:.1f} hPa")
