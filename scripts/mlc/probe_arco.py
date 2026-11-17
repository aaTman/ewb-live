import xarray as xr
uri = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ds = xr.open_zarr(uri, storage_options={"token": "anon"}, decode_times=True, consolidated=False)
print("uri:", uri)
print("dims:", dict(ds.sizes))
print("time:", str(ds.time.values[0]), "->", str(ds.time.values[-1]), "n=", ds.time.size)
print("data_vars:", sorted(ds.data_vars))
if "var" in ds.coords or "var" in ds:
    print("var list:", sorted(str(x) for x in ds["var"].values))
print("pressure_level:", None if "pressure_level" not in ds.coords else list(map(float, ds.pressure_level.values)))
print("lat:", float(ds.latitude.values[0]), float(ds.latitude.values[-1]), ds.latitude.size)
print("lon:", float(ds.longitude.values[0]), float(ds.longitude.values[-1]), ds.longitude.size)
for k in ["msl", "sp", "2m_temperature"]:
    if k in ds:
        da = ds[k]
        print(k, da.dims, da.encoding.get("chunks"), da.dtype)
print("u850 test:", "u" in ds.data_vars)
