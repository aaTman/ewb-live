"""Loader smoke test against the REAL store on the ewb LXC (read-only).

    ops/remote.sh run python tmp/smoke_loader.py     # or the ssh one-liner below
    ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate && python -' \
        < tmp/smoke_loader.py

This is the exact call surface flood-detect codes against; it prints what each
function returns so the shapes/units/grids can be checked by eye.
"""
import datetime as dt

import numpy as np

from ewb_live.climatology import precip_clim as pc

d0 = dt.date(2026, 7, 15)
dates = [d0 + dt.timedelta(days=k) for k in range(5)]

th = pc.precip_threshold(d0, quantile=0.95, window=1)
print("precip_threshold       ", tuple(th.dims), tuple(th.shape),
      th.attrs.get("units"), f"mean={float(th.mean()):.1f} mm",
      f"nan={float(th.isnull().mean()):.3f}")

s = pc.precip_threshold_series(dates, quantile=0.99, window=3)
print("precip_threshold_series", tuple(s.dims), tuple(s.shape),
      s.attrs.get("units"), f"mean={float(s.mean()):.1f} mm")

im = pc.precip_threshold(d0, quantile=0.95, window=1, grid="imerg")
print("grid='imerg'           ", tuple(im.dims), tuple(im.shape),
      f"lat {float(im.latitude.min())}..{float(im.latitude.max())}",
      f"lon {float(im.longitude.min())}..{float(im.longitude.max())}")

lats = np.linspace(-60, 60, 41)
lons = np.linspace(0, 357.5, 40)
on = pc.precip_threshold_on(lats, lons, dates, quantile=0.95, window=1)
print("precip_threshold_on    ", tuple(on.dims), tuple(on.shape))

c = pc.wet_sample_count(d0, window=1)
print("wet_sample_count       ", tuple(c.dims), tuple(c.shape),
      f"median={float(c.median()):.0f} max={int(c.max())} (uint8, saturates 255)")

sc = pc.precip_threshold(d0, quantile=0.95, window=1, scale=0.8)
print("scale=0.8              ",
      f"ratio to unscaled={float((sc / th).mean()):.3f}")

print("FLOOR_MM               ", pc.FLOOR_MM, " MIN_WET_SAMPLES",
      pc.MIN_WET_SAMPLES, " POOL_HALF_WINDOW_DAYS", pc.POOL_HALF_WINDOW_DAYS)
print("ERA5_GRID              ", pc.ERA5_GRID)
print("IMERG_GRID             ", pc.IMERG_GRID)
meta = pc.climatology_meta()
print("meta                   ", meta["source"], "|", meta["reference_period"],
      "|", meta["percentiles"], meta["accumulations_days"])

try:
    pc.precip_threshold(d0, quantile=0.90, window=1)
except ValueError as e:
    print("bad quantile           ", str(e)[:70])
try:
    pc.precip_threshold(d0, quantile=0.95, window=2)
except (ValueError, KeyError) as e:
    print("bad window             ", str(e)[:70])
