"""mlc-research diagnostic: where is the raw ERA5 low on 1-2 Nov 2023 and did the
node detection see it?"""
import glob
import numpy as np
import xarray as xr

files = sorted(glob.glob("data/mlc/nov23/etcc_2023110[123].nc"))
ds = xr.open_mfdataset(files, combine="nested", concat_dim="time").load()
lat, lon = ds.lat.values, ds.lon.values
print("lat[0], lat[-1] =", lat[0], lat[-1])
for t in ds.time.values:
    f = ds.msl.sel(time=t)
    box = f.values[(lat >= 40) & (lat <= 62)][:, (lon >= -35) & (lon <= 5)]
    k = np.unravel_index(int(np.nanargmin(box)), box.shape)
    la = lat[(lat >= 40) & (lat <= 62)][k[0]]
    lo = lon[(lon >= -35) & (lon <= 5)][k[1]]
    print(f"{str(t)[:16]}  box-min {box.min():.1f} hPa at {la:.2f}N {lo:.2f}E")

# nodes near that low for each step
print("\n-- nodes within 6 deg of (50.5N,-16W) with their MSLP --")
for f in sorted(glob.glob("data/mlc/run/nodes_vor.*.dat"))[:14]:
    rows = [l.split() for l in open(f) if not l.startswith("#") and len(l.split()) >= 6]
    near = [(float(r[3]), float(r[2]), float(r[4])) for r in rows
            if abs(float(r[3]) - 50.5) < 6 and abs(float(r[2]) + 16) < 8]
    if near:
        print(f.split("dat")[-1], f"n={len(near)}", sorted(near, key=lambda x: x[2])[:4])
