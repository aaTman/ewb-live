"""mlc-research: fetch ERA5 orography (single time step) for the orography check."""
import numpy as np, xarray as xr
ARCO = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ds = xr.open_zarr(ARCO, storage_options={"token": "anon"}, consolidated=False)
z = ds["geopotential_at_surface"].isel(time=int(np.where(ds.time.values == np.datetime64("2023-11-01T00"))[0][0]))
topo = (z.load() / 9.80665).rename("topo").assign_attrs(units="m")
topo = topo.assign_coords(longitude=(topo.longitude + 180) % 360 - 180).sortby("longitude")
topo = topo.rename(latitude="lat", longitude="lon")
topo.to_netcdf("data/mlc/topo.nc", encoding={"topo": {"zlib": True, "_FillValue": False}})
print("topo: max %.0f m, mean %.1f m, >500m fraction %.3f" % (
    float(topo.max()), float(topo.mean()), float((topo > 500).mean())))
