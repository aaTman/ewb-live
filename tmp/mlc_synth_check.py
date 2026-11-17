"""Synthetic-field smoke check for the mlc tracker (dev helper, not a test)."""
import datetime as dt

import numpy as np

from ewb_live.events import mlc_track as mt

LATS = np.arange(-80.0, 80.0 + 1e-9, 0.25)
LONS = np.arange(-180.0, 180.0, 0.25)
TIMES = [dt.datetime(2023, 11, 1) + dt.timedelta(hours=6 * i) for i in range(40)]


def low(f, lat0, lon0, depth=40.0, sigma_deg=2.0):
    d = mt.haversine_km(LATS[:, None], LONS[None, :], lat0, lon0) / 111.0
    return f - depth * np.exp(-(d / sigma_deg) ** 2 / 2.0)


fields = []
for k, t in enumerate(TIMES):
    f = np.full((LATS.size, LONS.size), 1013.0)
    f = low(f, 50.0 + 0.3 * k, -30.0 + 2.0 * k, depth=40.0)      # deep fast mover
    f = low(f, -45.0, 100.0, depth=8.0, sigma_deg=1.0)           # weak, static
    f = low(f, 60.0 - 0.5 * k, 179.5 + 0.4 * k, depth=25.0)      # seam crosser
    fields.append(f)
msl = np.stack(fields)

res = mt.generate_mlc_tracks(msl, LATS, LONS, TIMES)
print("nodes", res.n_nodes, "tracks", len(res.tracks), res.summary)
for t in res.tracks:
    print(f"  id={t.track_id} {t.start} -> {t.end} n={len(t)} msl={t.min_mslp:.1f} "
          f"disp={t.displacement_km:.0f} km bomb={t.bomb_ratio()} "
          f"filled={int(np.sum(t.filled))} am={t.crosses_antimeridian}")
