"""AR tropical exclusion — the ONE shared filter.

Owner directive (HIGH, 2026-11-08 23:0xZ; SPEC "AR tropical exclusion"):
any AR OBJECT (connected component of the AR mask at a time step) with ANY
mask cell between 15 S and 15 N at ANY time step is DROPPED entirely.

Every consumer of the AR mask calls ``drop_tropical_components`` right
after the upstream builder so detection (events/ar.py), the raw AR map
layer (viz/exceedance.py) and any future consumer can never diverge.
Scoring applies it at event level: an event exists only if detection kept
its object; purged events are documented in pair_status (select.py
curation path).

Stricter than upstream finalization, which drops an object only when its
pixel-weighted MEAN |lat| <= 15 (and only for >=500 px): an object with a
mid-latitude body and a tropical tail passes upstream but is dropped here.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

TROPICAL_LAT = 15.0

# 8-connectivity: a single-pixel-wide neck still counts as one object, the
# way a plume tip reads on the 0.25 deg grid
_STRUCTURE = np.ones((3, 3), dtype=bool)


def drop_tropical_components(mask: xr.DataArray) -> xr.DataArray:
    """Return ``mask`` with every connected component touching |lat| < 15
    zeroed out, per time step (2-D input handled as one step).

    Shortcut: components are per-frame, not tracked across frames — an
    object that loses its tropical tail is dropped on the offending frames
    and may survive on clean frames. Upgrade to lifetime tracking only if
    science-review shows a survivor actually behaves as one tracked TC/AR.
    """
    from scipy import ndimage

    m = mask.transpose(..., "latitude", "longitude")
    vals = np.asarray(m, dtype=bool)
    shape2d = vals.shape[-2:]
    flat = vals.reshape(-1, *shape2d)
    out = flat.copy()
    lat = np.asarray(mask["latitude"], dtype=float)
    tropical_band = np.abs(lat) < TROPICAL_LAT  # along the latitude axis
    band2d = np.broadcast_to(tropical_band[:, None] if m.dims[-2] == "latitude"
                             else tropical_band[None, :], shape2d)
    for i in range(flat.shape[0]):
        if not flat[i].any():
            continue
        lab = np.asarray(ndimage.label(flat[i], structure=_STRUCTURE)[0])
        touched = np.unique(lab[band2d & flat[i]])  # n=0 => all-zero lab => empty
        if touched.size:
            out[i] = flat[i] & ~np.isin(lab, touched[touched > 0])
    return xr.DataArray(out.reshape(vals.shape), dims=m.dims, coords=m.coords)
