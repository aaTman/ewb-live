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
    """Pa (time, lat desc, lon 0-360) -> hPa (time, lat asc, lon [-180, 180))."""
    da = mslp.rename("mslp")
    if float(da.latitude.values[0]) < float(da.latitude.values[-1]):
        da = da.isel(latitude=slice(None, None, -1))
    da = da.assign_coords(
        longitude=(np.asarray(da.longitude.values, float) + 180.0) % 360.0 - 180.0)
    # ARCO longitude is ascending 0..359.75, so the shifted axis is ascending too
    return (da / 100.0).sel(latitude=slice(LAT_LIMIT, -LAT_LIMIT)).astype(np.float32)


def read_mslp_window(start: dt.datetime, end: dt.datetime, *,
                     chunk_hours: int = 24, ds=None,
                     ) -> tuple[list[dt.datetime], np.ndarray, np.ndarray, np.ndarray]:
    """6-hourly global MSLP over [start, end] -> ``(times, mslp, lats, lons)``.

    ``mslp`` is float32 ``(n_time, 705, 1440)`` in hPa. A 90-day window is
    ~1.4 GB, so the window is read in ``chunk_hours`` slices: the ARCO MSLP
    store is chunked ``(4, 721, 600)``, i.e. 24 frames is exactly 6 chunks and
    one read per day, and the transient peak stays at the window plus one chunk.

    Times that ARCO does not have yet (the ~6-day lag) are skipped, so the
    caller always gets a contiguous array of what actually exists.
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
    step = max(1, int(chunk_hours) // 6)
    for i in range(0, want.size, step):
        block = want[i:i + step]
        sel = src[MSLP_VAR].sel(time=block)
        da = _to_repo_frame(sel).transpose("time", "latitude", "longitude")
        blocks.append(np.ascontiguousarray(
            np.asarray(da.values, np.float32), dtype=np.float32))
        times.extend(t.astype("datetime64[h]").astype(dt.datetime)
                     for t in np.asarray(da.time.values))
        if lats is None:
            lats = np.asarray(da.latitude.values, float)
            lons = np.asarray(da.longitude.values, float)
        del sel, da
    mslp = blocks[0] if len(blocks) == 1 else np.concatenate(blocks, axis=0)
    del blocks
    logger.info("mlc_era5: %d frames %s..%s (%.2f GB), mslp [%.1f, %.1f] hPa",
                mslp.shape[0], times[0], times[-1], mslp.nbytes / 1e9,
                float(np.nanmin(mslp)), float(np.nanmax(mslp)))
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
        if lats[0] < lats[-1]:
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
