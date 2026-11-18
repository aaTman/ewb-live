"""ERA5 MSLP + orography reads for the mid-latitude-cyclone lane.

One job: hand :mod:`ewb_live.events.mlc_track` a ``float32 (time, lat, lon)``
MSLP stack in hPa on the repo's ``[-180, 180)`` longitude frame, plus the static
ERA5 orography it screens genesis against.

**MSLP is not in the local truth mirror.** ``ewb_live.scoring.
truth_mirror.MIRROR_VARS`` mirrors ``2m_temperature`` plus the AR variables
(``q``/``u``/``v`` >= 300 hPa); ``mean_sea_level_pressure`` is not among them,
and the ewb root disk is at 99 % (5.9 G free, 2026-11-17), so this lane streams
its window straight from the ERA5 ARCO zarr (the same store
``ewb_live/events/truth.py`` probes) instead of growing the mirror. Mirroring
MSLP instead costs ~0.9 GB/90 d of 6-hourly global 0.25 deg data compressed
(~3.4 GB at the mirror's chunking) — an owner decision on a 99 %-full disk, so
the numbers are written down in ``notes/mlc_data_costs.md`` and the lane stays
streaming.

Scratch discipline: nothing is written to disk except the small static
orography field (``data/cache/mlc/topo_0p25.npz``, ~4 MB, written once) and the
per-window event cache ``events/__init__.py`` already owns for every lane.

Units: ARCO MSLP is Pa; we divide by 100 and hand out **hPa**, the unit
``mlc_track`` and the PROOF recipe use. Latitude comes out ascending (ARCO is
descending), longitude in [-180, 180) (ARCO is 0-360), the duplicated polar rows
beyond +-88 deg dropped (the tracker never searches there and ERA5 repeats them).
Both orders are *enforced* by ``check_axes``: the axis order is part of the
tracker's contract, and a rotated longitude axis silently doubles the grid step
its contour mapper derives, which cost this lane every event it should have
published (mlc-fix 2026-11-18, ``notes/mlc_fix.md``).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ARCO_URI = os.environ.get(
    "EWB_LIVE_ARCO_URI",
    "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3")
MSLP_VAR = "mean_sea_level_pressure"   # registry 'msl' == air_pressure_at_mean_sea_level
TOPO_VAR = "geopotential_at_surface"
G0 = 9.80665                                   # m s-2: geopotential -> elevation
LAT_LIMIT = 88.0                               # drop ERA5's duplicated polar rows
DEFAULT_CACHE_DIR = Path(os.environ.get("EWB_LIVE_CACHE", "data/cache")) / "mlc"

_ARCO = None


def open_arco():
    """The ARCO ERA5 zarr, opened lazily and anonymously (cached per process)."""
    global _ARCO
    if _ARCO is None:
        import xarray as xr

        _ARCO = xr.open_zarr(ARCO_URI, storage_options={"token": "anon"},
                             consolidated=False, decode_times=True)
    return _ARCO


def _to_repo_frame(mslp):
    """Pa (time, lat desc, lon 0-360) -> hPa (time, lat asc, lon [-180, 180)).

    Both axis orders are load-bearing, not cosmetic (mlc-fix 2026-11-18, the
    0-event blocker in notes/mlc_fix.md):

    * **latitude** — ARCO is 90 N-first (descending). The old guard flipped only
      an *ascending* input, so ARCO came out descending and every consumer that
      reads the documented ascending axis (``grid_spacing``, the gap-fill
      distance field, the cached orography) was off by a reversal.
    * **longitude** — shifting ``0..359.75`` by ``+180 mod 360`` does not sort
      it, it ROTATES it: the axis becomes ``0, 0.25, …, 179.75, -180, …, -0.25``
      with one ``-359.75`` jump. The data columns stay in physical order, but
      ``mlc_track.contour_to_latlon`` rebuilds longitudes as
      ``lon[0] + column * grid_spacing(lon)``, and ``grid_spacing`` averages
      ``|diff|`` across that jump: ``0.4998 deg`` on a 0.25 deg grid, exactly
      2x. Every closed-contour radius the tracker measures is then inflated by
      up to ``2/cos(lat)`` — the ``centre + 3 hPa closes within 6 deg`` test
      measured 8-51 deg for rings that are 1-4 deg across, so real ARCO MSLP
      produced 0 nodes and the lane published nothing while the same frames in
      the sorted frame gave 24-27 nodes per frame. Hence the explicit reorder.

    The result is validated against the tracker's contract before it leaves
    this module.
    """
    da = mslp.rename("mslp")
    if float(da.latitude.values[0]) > float(da.latitude.values[-1]):   # ARCO: 90N-first
        da = da.isel(latitude=slice(None, None, -1))
    da = da.sel(latitude=slice(-LAT_LIMIT, LAT_LIMIT))                 # ascending slice
    shifted = (np.asarray(da.longitude.values, float) + 180.0) % 360.0 - 180.0
    da = da.assign_coords(longitude=shifted).isel(longitude=np.argsort(shifted,
                                                                         kind="stable"))
    out = (da / 100.0).astype(np.float32)
    check_axes(out.latitude.values, out.longitude.values)
    return out


def check_axes(lats: np.ndarray, lons: np.ndarray) -> None:
    """Fail loudly if the lattice is not the ascending 0.25-style frame the
    tracker's index math assumes (see :func:`_to_repo_frame`)."""
    la = np.asarray(lats, float)
    lo = np.asarray(lons, float)
    if la.size < 2 or not np.all(np.diff(la) > 0):
        raise ValueError(f"mlc_era5: latitude axis must be ascending, got "
                         f"{la[0] if la.size else float('nan')}.."
                         f"{la[-1] if la.size else float('nan')}")
    d = np.diff(lo)
    if lo.size < 2 or np.any(d <= 0) or np.max(d) - np.min(d) > 1e-6:
        raise ValueError(f"mlc_era5: longitude axis must be ascending and uniform; "
                         f"got first={lo[0] if lo.size else float('nan')} "
                         f"last={lo[-1] if lo.size else float('nan')} "
                         f"max|d|={np.max(np.abs(d)) if d.size else float('nan')}")


def _ranges(times: list[dt.datetime]) -> str:
    """Compact contiguous-run summary of dropped timestamps."""
    if not times:
        return ""
    out, s, p = [], times[0], times[0]
    for t in times[1:]:
        if (t - p).total_seconds() > 6 * 3600:
            out.append(f"{s:%m-%d %H}Z..{p:%m-%d %H}Z")
            s = t
        p = t
    out.append(f"{s:%m-%d %H}Z..{p:%m-%d %H}Z")
    return ", ".join(out if len(out) <= 4 else out[:4] + [f"(+{len(out) - 4} more)"])


def read_mslp_window(start: dt.datetime, end: dt.datetime, *,
                     chunk_hours: int = 24, ds=None,
                     ) -> tuple[list[dt.datetime], np.ndarray, np.ndarray, np.ndarray]:
    """6-hourly global MSLP over [start, end] -> ``(times, mslp, lats, lons)``.

    ``mslp`` is float32 ``(n_time, 705, 1440)`` in hPa. A 90-day window is
    ~1.4 GB, so the window is read in ``chunk_hours`` slices: the ARCO MSLP
    store is chunked ``(4, 721, 600)``, i.e. 24 frames is exactly 6 chunks and
    one read per day, and the transient peak stays at the window plus one chunk.

    Times that ARCO does not have yet are skipped, so the caller always gets
    what actually exists — and that includes more than the time *index*: ARCO's
    axis runs ahead of its data, and whole frames at the tail are all-NaN
    (measured 2026-11-18: every cell of 2026-11-12 00Z .. 2026-11-17 00Z is NaN).
    They are dropped here, because peak detection would ignore them but the
    tracker's gap filling would happily "fill" a hole from an empty frame and
    publish a NaN central pressure. The lane therefore reports the frames it
    really used (``mlc.LANE_STATUS``), not the frames the index advertised.
    """
    src = open_arco() if ds is None else ds
    want = np.arange(np.datetime64(start, "h"),
                     np.datetime64(end, "h") + np.timedelta64(6, "h"),
                     np.timedelta64(6, "h"))
    have = np.asarray(src[MSLP_VAR].time.values)
    have64 = have.astype("datetime64[h]")
    want = want[np.isin(want, have64)]
    if want.size == 0:
        logger.warning("mlc_era5: ARCO has none of %s..%s", start, end)
        return [], np.zeros((0, 0, 0), np.float32), np.zeros(0), np.zeros(0)

    times: list[dt.datetime] = []
    blocks: list[np.ndarray] = []
    lats = lons = None
    n_empty = 0
    empty: list[dt.datetime] = []
    step = max(1, int(chunk_hours) // 6)
    for i in range(0, want.size, step):
        block = want[i:i + step]
        sel = src[MSLP_VAR].sel(time=block)
        da = _to_repo_frame(sel).transpose("time", "latitude", "longitude")
        arr = np.ascontiguousarray(np.asarray(da.values, np.float32), dtype=np.float32)
        got = [t.astype("datetime64[h]").astype(dt.datetime)
               for t in np.asarray(da.time.values)]
        ok = np.any(np.isfinite(arr), axis=(1, 2))      # ARCO index ahead of data
        if not ok.all():
            gone = [t for t, k in zip(got, ok) if not k]
            n_empty += len(gone)
            empty += gone
            arr, got = arr[ok], [t for t, k in zip(got, ok) if k]
        if arr.shape[0]:
            blocks.append(arr)
            times.extend(got)
        if lats is None and arr.shape[0]:
            lats = np.asarray(da.latitude.values, float)
            lons = np.asarray(da.longitude.values, float)
        del sel, da, arr
    if not blocks:
        logger.warning("mlc_era5: %d frames of %s..%s are all-NaN in ARCO "
                       "(its index runs ahead of the data)", n_empty, start, end)
        return [], np.zeros((0, 0, 0), np.float32), np.zeros(0), np.zeros(0)
    mslp = blocks[0] if len(blocks) == 1 else np.concatenate(blocks, axis=0)
    del blocks
    gap = f" ({n_empty} empty ARCO frames skipped: {_ranges(empty)})" if empty else ""
    logger.info("mlc_era5: %d frames %s..%s (%.2f GB), mslp [%.1f, %.1f] hPa%s",
                mslp.shape[0], times[0], times[-1], mslp.nbytes / 1e9,
                float(np.nanmin(mslp)), float(np.nanmax(mslp)), gap)
    return times, mslp, lats, lons


def topo_path(cache_dir: str | Path | None = None) -> Path:
    return Path(cache_dir or DEFAULT_CACHE_DIR) / "topo_0p25.npz"


# The store reader peaks at ~17 GB of decode buffers for a single (721, 1440)
# timestep of this variable (measured on the ewb box, both through xarray and by
# indexing the zarr array directly, ~1.4-2 s). That is a ONE-TIME cost: the field
# is static and cached as a ~4 MB npz, so every later scan reads the cache. If the
# box's memory matters, pre-warm it once (see notes/DONE_mlc_impl.md).
_TOPO_PROBE_TIMES = (np.datetime64("2000-01-01T00"), np.datetime64("1979-06-01T00"),
                     np.datetime64("2015-01-01T00"))


def _read_topo_step(z, time_index: int | None) -> np.ndarray:
    """One timestep of ``z`` in metres (NaN -> 0, i.e. sea and missing alike)."""
    sel = z if time_index is None else z.isel(time=time_index)
    return np.nan_to_num(np.asarray(sel.load(), np.float32), nan=0.0) / G0


def load_orography(cache_dir: str | Path | None = None, *, ds=None,
                   time_step: np.datetime64 | None = None,
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ERA5 surface elevation (m) as ``(elev, lats asc, lons [-180,180))``.

    ``geopotential_at_surface`` is time-invariant in ERA5, so a single step is
    the whole static field; cached once as a ~4 MB float32 npz. A cache miss
    with no network (unit tests, offline dev) returns an all-zero field, which
    disables the orography screen instead of failing the lane — the screen is a
    false-positive filter, so a zero field is the safe direction.

    A *cached* all-zero field is not trusted (it is what a pre-2026-11-17 build
    wrote after reading the all-NaN 1900 pseudo step): it is refetched.
    """
    p = topo_path(cache_dir)
    if p.exists():
        with np.load(p) as z:
            # read everything inside the with-block: an NpzFile is lazy, so
            # touching z["lats"] after it closes raises AttributeError (and the
            # lane would then publish nothing on every run after the first)
            elev = z["elev"].astype(np.float32)
            lats, lons = z["lats"], z["lons"]
        if elev.size and float(np.max(elev)) > 0.0:
            return elev, lats, lons
        logger.warning("mlc_era5: cached orography %s is all zero — refetching", p)
    try:
# ARCO's time axis opens with a 1900-01-01T00 pseudo step that is **all NaN** for
# geopotential_at_surface (the real record starts 1979-01-01), so "time index 0"
# silently yields an all-zero orography field and turns the genesis elevation
# screen off. Probe real dates, and reject a field with no land on it.
        src = open_arco() if ds is None else ds
        z = src[TOPO_VAR]
        times = (np.asarray(z.time.values, "datetime64[h]") if "time" in z.dims
                 else None)
        cands = [time_step] if time_step is not None else list(_TOPO_PROBE_TIMES)
        arr = None
        for t in cands:
            idx = (None if times is None else
                   int(np.abs(times - np.datetime64(t, "h")).argmin()))
            a = _read_topo_step(z, idx)
            if float(np.max(a)) > 0.0:      # a real field always has land
                arr = a
                break
        if arr is None:
            raise ValueError(f"{TOPO_VAR} is all-NaN at every probed time step")
        lats = np.asarray(z.latitude.values, float)
        lons = np.asarray(z.longitude.values, float)
        if lats[0] > lats[-1]:      # ARCO is 90N-first; the repo frame is ascending
            arr, lats = arr[::-1], lats[::-1]
        lons = (lons + 180.0) % 360.0 - 180.0
        keep = (lats >= -LAT_LIMIT) & (lats <= LAT_LIMIT)
        arr, lats = arr[keep], lats[keep]
        order = np.argsort(lons)
        arr, lons = arr[:, order], lons[order]
    except Exception as exc:  # noqa: BLE001 - orography is a screen, not a dependency
        logger.warning("mlc_era5: no orography (%s); genesis elevation screen off", exc)
        return np.zeros((0, 0), np.float32), np.zeros(0), np.zeros(0)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(p, elev=arr, lats=lats, lons=lons)
        logger.info("mlc_era5: cached orography %s (%.1f MB, max %.0f m)",
                    p, p.stat().st_size / 1e6, float(arr.max()))
    except OSError as exc:                       # read-only deploy: run without it
        logger.warning("mlc_era5: could not cache orography: %s", exc)
    return arr, lats, lons


def elevation_at(elev: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                 lat: float, lon: float) -> float:
    """Nearest-cell surface elevation (m); 0 when no orography field is loaded."""
    if elev is None or elev.size == 0:
        return 0.0
    la = np.asarray(lats, float)
    lo = np.asarray(lons, float)
    i = int(np.clip(np.argmin(np.abs(la - float(lat))), 0, elev.shape[0] - 1))
    j = int(np.argmin(np.abs((lo - float(lon) + 180.0) % 360.0 - 180.0)))
    return float(elev[i, j])
