"""Seam-periodic AR mask — EWB's detector with longitude-periodic stencils.

Upstream (`extremeweatherbench.events.atmospheric_river`) thresholds a
Gaussian-blurred Laplacian of IVT, dilates it, intersects with IVT >= 400 and
keeps components >= 500 gridpoints with |mean lat| > 15. Every one of those
steps uses a **non-periodic** longitude stencil or labeling:

  * ``calc._compute_blurred_laplacian_ufunc`` — 3x3 Laplace with reflect
    borders, then ``ndimage.gaussian_filter`` (mode='reflect');
  * ``calc._binary_dilation_ufunc`` — ``border_value=0`` (a cell at the array
    edge cannot borrow across the seam);
  * ``_label_objects_time_linked`` — plain 6-connectivity, so an object that
    straddles the seam is TWO objects for the >= 500 gridpoint floor and the
    |mean lat| rule.

ERA5 ARCO and the local mirror are 0..360 arrays, so the array edge — the
place all three artifacts live — is the **prime meridian**. Measured on the
published raw frames (340 frames, 90-day window): the 2 deg band immediately
WEST of lon 0 holds ~30% fewer AR cells than the band immediately east
(z = -3.8 against 350 control longitudes, neighbours at lon +/-1 deg sitting
at z ~ +1/+0.2), i.e. the deficit is locked to lon 0 and not to geography;
and a synthetic periodic filament recomputed with the seam at lon 0 vs lon
180 differs in |laplacian| by up to 13.9 kg m-2 s-2 against the 2.5
threshold. Live symptom (owner 2026-11-10): the 2026-08-27/30 Europe AR
(6b4dec9dc8db) looked "sliced at the prime meridian", with step-to-step
discontinuity — both symptoms sit here.

Fix, keeping upstream's thresholds and semantics intact:

  * the stencil runs on a wrap-padded copy of the field (AR_LON_PAD_COLS
    columns of the opposite end pasted on each side, wide enough that the
    padded array's own reflect border cannot reach the trimmed output) and
    is trimmed back before anything else sees it;
  * component labeling is longitude-periodic and time-linked exactly like
    upstream (4-connectivity within a step, same-pixel links across steps),
    so a Greenwich-straddling AR is ONE object for the size/latitude floors;
  * a NON-global longitude array (a regional crop) is never wrapped —
    gluing lon +30 to lon -20 would invent weather. Periodicity is decided
    from the longitude coordinate itself (``is_periodic_lon``), never from a
    caller's promise; callers that crop must crop wide (see
    ``ewb_live/viz/build.py`` AR_COMPUTE_PAD_*).

Callers swap ``build_atmospheric_river_mask_and_land_intersection`` for the
same-signature fn below; detection (``events/ar.py``), the raw map layer
(``viz/exceedance.py``) and the per-event viz assets (``viz/build.py``)
therefore share ONE mask definition and cannot diverge.
"""
from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

# upstream defaults (events/atmospheric_river.py), kept identical
AR_IVT_THRESHOLD = 400.0
AR_LAPLACIAN_THRESHOLD = 2.5
AR_SIGMA = 3.0
AR_DILATION_RADIUS = 8
AR_MIN_SIZE_GRIDPOINTS = 500
AR_MIN_ABS_LAT = 15.0

# Wrap pad, in longitude COLUMNS (0.25 deg grid): the blurred Laplacian needs
# 4*sigma = 12 columns of true neighbours on each side of the trimmed region
# and the dilation another `radius` = 8, so 32 columns (8 deg) leaves the
# padded array's own reflect border ~12 columns clear of the output.
AR_LON_PAD_COLS = 32

# upstream links only WEST and SOUTH neighbours inside a step (4-conn)
_PLUS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)


def is_periodic_lon(lons: np.ndarray) -> bool:
    """True when a longitude array covers the WHOLE globe, i.e. wrapping it
    is physically correct. A regional crop is not periodic: its ends are not
    neighbours, and padding would splice unrelated weather together."""
    l = np.asarray(lons, float).ravel()
    if l.size < 8:
        return False
    step = abs(float(np.median(np.diff(l))))
    if not step > 0:
        return False
    # cell-centred grid: the domain spans lon[0]-step/2 .. lon[-1]+step/2
    return bool((l[-1] - l[0] + step) >= 360.0 - 1e-6)


def wrap_pad_lon(a: np.ndarray, pad: int = AR_LON_PAD_COLS) -> tuple[np.ndarray, int]:
    """Paste `pad` columns of the opposite end on each side of the last axis.

    Returns (padded, pad_actually_used); pad 0 = untouched (already trimmed).
    """
    n = a.shape[-1]
    p = int(min(pad, n // 4)) if n >= 8 else 0
    if p <= 0:
        return a, 0
    return np.concatenate([a[..., -p:], a, a[..., :p]], axis=-1), p


def dilated_high_laplacian(ivt: np.ndarray, *, sigma: float = AR_SIGMA,
                           laplacian_threshold: float = AR_LAPLACIAN_THRESHOLD,
                           dilation_radius: int = AR_DILATION_RADIUS,
                           periodic: bool = True) -> np.ndarray:
    """|blurred Laplacian(ivt)| >= threshold, dilated — periodic in longitude.

    Any number of leading dims; the last two are (lat, lon), matching the
    core dims upstream's ufunc consumes. One plane at a time, like upstream.
    """
    from extremeweatherbench import calc

    a = np.asarray(ivt, dtype=np.float64)
    if a.ndim < 2:
        raise ValueError("dilated_high_laplacian needs (..., lat, lon)")
    out = np.empty(a.shape, dtype=np.int8)
    flat = a.reshape(-1, a.shape[-2], a.shape[-1])
    o = out.reshape(-1, a.shape[-2], a.shape[-1])
    for i in range(flat.shape[0]):
        pad_src, pad = (wrap_pad_lon(flat[i]) if periodic
                        else (flat[i], 0))
        lap = calc._compute_blurred_laplacian_ufunc(pad_src, float(sigma))
        high = np.abs(lap) >= float(laplacian_threshold)
        dil = calc._binary_dilation_ufunc(high, int(dilation_radius))
        o[i] = dil[..., pad:pad + flat.shape[-1]]
    return out


def _label_periodic_stack(mask: np.ndarray, periodic: bool) -> np.ndarray:
    """Time-linked labels (t, lat, lon), longitude-periodic when asked.

    Same connectivity as upstream `_label_time_linked_stack`: 4-neighbour
    links inside a step, plus a link for every pixel set in BOTH consecutive
    steps. Periodicity adds the seam union (col nx-1 <-> col 0, same row
    only — 4-connectivity has no diagonal neighbour across the seam).
    """
    from scipy import ndimage

    from .seam import label_periodic

    m = np.asarray(mask, bool)
    if m.ndim != 3:
        raise ValueError("_label_periodic_stack wants (t, lat, lon)")
    nt = m.shape[0]
    labs = np.zeros(m.shape, dtype=np.int64)
    offs = np.zeros(nt + 1, dtype=np.int64)
    for t in range(nt):
        if periodic:
            # ndimage convention, same as the non-periodic branch: 0 =
            # background, 1..n = objects (label_periodic relabels densely).
            cur, n = label_periodic(m[t], _PLUS, seam_rows=0)
            cur = np.asarray(cur, np.int64)
            n = int(n)
        else:
            res = ndimage.label(m[t], structure=_PLUS)
            cur = np.asarray(res[0], np.int64)
            n = int(res[1])
        labs[t] = cur + offs[t]
        offs[t + 1] = offs[t] + n
    total = int(offs[-1])
    if total < 2:
        return labs.astype(np.int32)

    parent = np.arange(total + 1, dtype=np.int64)
    for t in range(nt - 1):
        both = m[t] & m[t + 1]
        if not both.any():
            continue
        a = labs[t][both]
        b = labs[t + 1][both]
        base = total + 1
        for key in np.unique(a * base + b):
            parent[int(key) % base] = int(key) // base
    while True:                                    # path-compress to fixpoint
        p = parent[parent]
        if np.array_equal(p, parent):
            break
        parent = p
    root = parent[labs]
    uniq, inv = np.unique(root[labs > 0], return_inverse=True)
    out = np.zeros(m.shape, dtype=np.int32)
    out[labs > 0] = (inv + 1).reshape(out[labs > 0].shape)
    return out


def filter_components(mask: np.ndarray, lats: np.ndarray, *,
                      min_size: int = AR_MIN_SIZE_GRIDPOINTS,
                      min_abs_lat: float = AR_MIN_ABS_LAT,
                      periodic: bool = True) -> np.ndarray:
    """Keep components with >= `min_size` cells and |mean lat| > `min_abs_lat`.

    `mask` is (t, lat, lon) [or (lat, lon)]; the size/latitude floors are the
    same per-object quantities upstream computes (`_filter_labels_size_and_lat`
    over its time-linked labels), only with the seam stitched shut.
    """
    m = np.asarray(mask, bool)
    if m.ndim == 2:
        m = m[None]
        squeeze = True
    else:
        squeeze = False
    lab = _label_periodic_stack(m, periodic)
    n = int(lab.max())
    if n == 0:
        return np.zeros(m.shape, bool)[0] if squeeze else np.zeros(m.shape, bool)
    flat = lab.ravel()
    counts = np.bincount(flat, minlength=n + 1)
    lat_w = np.broadcast_to(np.asarray(lats, float).reshape(1, -1, 1), m.shape)
    sums = np.bincount(flat, weights=lat_w.ravel(), minlength=n + 1)
    ok = np.zeros(n + 1, dtype=bool)
    ok[1:] = ((counts[1:] >= min_size)
              & (np.abs(sums[1:] / np.maximum(counts[1:], 1)) > min_abs_lat))
    out = ok[lab]
    return out[0] if squeeze else out


def _finalize_ar_mask_periodic(da: xr.DataArray, *, min_size: int,
                               min_abs_lat: float, time_dimension: str,
                               periodic: bool) -> xr.DataArray:
    """Component floors over a DataArray, upstream's dim handling, periodic labels.

    Upstream labels the whole (time, lat, lon) slab per lead with extra
    leading dims kept unlinked; here the same decomposition happens per extra
    dim, so a (lead_time, valid_time, lat, lon) input gets one time-linked
    object set per lead and a bare (lat, lon) input gets plain labeling.
    """
    core = ["latitude", "longitude"]
    dims = list(da.dims)
    ordered = ([time_dimension] if time_dimension in dims else []) + \
        [d for d in dims if d not in core and d != time_dimension]
    arr = np.asarray(da.transpose(*(*ordered, *core)).values)
    from extremeweatherbench import utils

    vals = utils.values_as_bool(arr)
    lats = np.asarray(da.latitude.values, float)
    if vals.shape[-2:] != (lats.size, np.asarray(da.longitude.values).size):
        raise RuntimeError("AR mask finalize: lat/lon axes desynced")
    if time_dimension in ordered:
        t_shape, e_shape = vals.shape[0], vals.shape[1:-2]
    else:
        t_shape, e_shape = 0, vals.shape[:-2]
    out = np.zeros(vals.shape, dtype=np.int8)
    if t_shape:
        for idx in np.ndindex(e_shape):
            out[(slice(None),) + idx] = filter_components(
                vals[(slice(None),) + idx], lats, min_size=min_size,
                min_abs_lat=min_abs_lat, periodic=periodic)
    else:
        for idx in np.ndindex(e_shape):
            out[idx] = filter_components(vals[idx], lats, min_size=min_size,
                                         min_abs_lat=min_abs_lat,
                                         periodic=periodic)
    res = xr.DataArray(out, coords={d: da.coords[d] for d in (*ordered, *core)},
                       dims=(*ordered, *core)).transpose(*dims)
    res.name = "atmospheric_river_mask"
    return res


def build_atmospheric_river_mask_and_land_intersection(
    data: xr.Dataset,
    output_variables: list[str] | None = None,
    *,
    ivt_threshold: float = AR_IVT_THRESHOLD,
    laplacian_threshold: float = AR_LAPLACIAN_THRESHOLD,
    sigma: float = AR_SIGMA,
    dilation_radius: int = AR_DILATION_RADIUS,
    min_size_gridpoints: int = AR_MIN_SIZE_GRIDPOINTS,
    min_abs_lat: float = AR_MIN_ABS_LAT,
    time_dimension: str = "valid_time",
) -> xr.Dataset:
    """Drop-in for upstream's builder, seam-periodic (module docstring).

    Same thresholds, same output names/dims; the ONLY behavioural difference
    is that longitude stencils and component labeling wrap on a global grid.
    """
    from extremeweatherbench import calc, utils
    from extremeweatherbench.events import atmospheric_river as ar_ev

    working = utils.stack_valid_time_pairs(data)
    ivt = ar_ev.integrated_vapor_transport(
        specific_humidity=working["specific_humidity"],
        eastward_wind=working["eastward_wind"],
        northward_wind=working["northward_wind"])
    lons = np.asarray(ivt.longitude.values, float)
    periodic = is_periodic_lon(lons)
    if periodic and ivt.chunks is not None and len(ivt.chunks[ivt.dims.index("longitude")]) > 1:
        # the wrap pad splices the array's own two ends; with the longitude
        # axis split across dask chunks each chunk would splice ITS ends —
        # silently inventing a seam in the middle of the field.
        raise ValueError("seam-periodic AR mask needs ONE longitude chunk "
                         f"(got {len(ivt.chunks[ivt.dims.index('longitude')])}); "
                         "rechunk longitude=-1")
    dilated = xr.apply_ufunc(
        _dilated_high_laplacian_ufunc, ivt, sigma, laplacian_threshold,
        dilation_radius, periodic,
        input_core_dims=[["latitude", "longitude"], [], [], [], []],
        output_core_dims=[["latitude", "longitude"]],
        dask="parallelized", keep_attrs=True, output_dtypes=[np.int8])
    initial_intersection = dilated.astype(bool) & (ivt >= ivt_threshold)
    ivt = cast(xr.DataArray, utils.unstack_valid_time_pairs(ivt, like=data))
    initial_intersection = cast(
        xr.DataArray, utils.unstack_valid_time_pairs(initial_intersection, like=data))
    ar_mask = _finalize_ar_mask_periodic(
        initial_intersection, min_size=min_size_gridpoints,
        min_abs_lat=min_abs_lat, time_dimension=time_dimension,
        periodic=periodic)
    land_intersection = calc.find_land_intersection(ar_mask)
    land_intersection.name = "atmospheric_river_land_intersection"

    result: dict[str, Any] = {
        "atmospheric_river_mask": ar_mask,
        "atmospheric_river_land_intersection": land_intersection,
        "integrated_vapor_transport": ivt,
    }
    if output_variables is not None:
        result = {k: v for k, v in result.items() if k in output_variables}
    return xr.Dataset(result)


def _dilated_high_laplacian_ufunc(ivt: xr.DataArray, sigma: float,
                                  laplacian_threshold: float,
                                  dilation_radius: int, periodic: bool) -> np.ndarray:
    """apply_ufunc shim: core dims (lat, lon) in, int8 (lat, lon) out."""
    return dilated_high_laplacian(
        ivt, sigma=sigma, laplacian_threshold=laplacian_threshold,
        dilation_radius=dilation_radius, periodic=bool(periodic))
