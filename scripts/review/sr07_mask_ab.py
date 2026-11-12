import numpy as np
import xarray as xr
import regionmask

t = xr.open_zarr("/opt/ewb-live/data/cache/truth/era5.zarr", consolidated=False)["2m_temperature"]
c = xr.open_zarr("/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr", consolidated=False)["2m_temperature"]
land = (regionmask.defined_regions.natural_earth_v5_0_0.land_110
        .mask(t.longitude, t.latitude).values == 0)
day = "2026-10-20"
tt = t.sel(time=slice(day, day + "T18"))
# scheme A: xarray reindex_like nearest (production build_exceedance_mask)
doy = tt.time.dt.dayofyear
hour = tt.time.dt.hour
aligned = c.sel(quantile=0.15).sel(dayofyear=doy, hour=hour).reindex_like(tt, method="nearest")
maskA = ((tt < aligned).resample(time="1D").min() & land).sum()
# scheme B: positional numpy (my v1)
d = doy.values
h = np.array([{0: 0, 6: 1, 12: 2, 18: 3}[int(x)] for x in hour.values])
bounds = c.sel(quantile=0.15).values[d - 1, h]
maskB = ((tt.values < bounds).reshape(1, 4, *tt.shape[1:]).all(axis=1) & land).sum()
print("production-style passes:", int(maskA), " positional:", int(maskB))
# where do they disagree?
a = (tt < aligned)
b = tt.values < bounds
print("step disagreements:", int(((a.values != b).sum(axis=(1, 2))).sum()))
# check aligned values at a couple points
print("aligned[0,360lat=90?,0]", float(aligned.values[0, 0, 0]), "manual:", float(c.sel(quantile=0.15).values[d[0]-1, h[0], 0, 0]))
print("clim doy coords sample:", c.dayofyear.values[:3], c.dayofyear.values[-3:])
print("aligned doy/hour coords:", aligned.dayofyear.values[:4], aligned.hour.values[:4])
