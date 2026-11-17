"""mlc-research: how many detected low-pressure nodes sit over high orography?

The classic failure mode of MSLP-reduced cyclone trackers is spurious lows over
the Tibetan Plateau / Andes / Greenland, caused by the surface-pressure-to-MSL
reduction (a column of fictive sea-level air is added over high terrain). This
script cross-references the DetectNodes node files against ERA5 orography and
against a land-sea mask-free height threshold.

  python scripts/mlc/check_orography.py --nodes data/mlc/run/nodes_mslp. --topo data/mlc/topo.nc
"""
from __future__ import annotations

import argparse
import glob

import numpy as np
import xarray as xr

ap = argparse.ArgumentParser()
ap.add_argument("--nodes", required=True, help="prefix glob of node files, e.g. data/mlc/run/nodes_mslp.")
ap.add_argument("--topo", default="data/mlc/topo.nc")
ap.add_argument("--elev", type=float, default=500.0)
args = ap.parse_args()

topo = xr.open_dataarray(args.topo).load()
lats, lons = topo.lat.values, topo.lon.values

n = hi = 0
by_band: dict[str, list[int]] = {}
rows = []
for f in glob.glob(args.nodes + "*.dat"):
    for line in open(f):
        if line.startswith("#") or len(line.split()) < 5:
            continue
        p = line.split()
        lon, lat = float(p[2]), float(p[3])
        j = int(round((90 - lat) / 0.25)) if lats[0] > lats[-1] else None
        i = int(round((lon - lons[0]) / 0.25))
        idx = int(np.argmin(np.abs(lats - lat)))
        jdx = int(np.argmin(np.abs(lons - lon)))
        h = float(topo.values[idx, jdx])
        n += 1
        hi += h > args.elev
        band = ("tropics |lat|<20" if abs(lat) < 20 else
                "orogeny belt 20-40" if abs(lat) < 40 else
                "midlat 40-65" if abs(lat) < 65 else "high lat 65+")
        by_band.setdefault(band, [0, 0])
        by_band[band][0] += 1
        by_band[band][1] += h > args.elev
        rows.append((lat, lon, h))
print(f"nodes: {n}  over {args.elev:.0f} m orography: {hi} ({hi/max(n,1)*100:.1f} %)")
for k, (a, b) in sorted(by_band.items()):
    print(f"  {k:<22} nodes {a:6d}  high-elev {b:5d} ({b/max(a,1)*100:.1f} %)")
