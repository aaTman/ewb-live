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


def load_orography(cache_dir: str | Path | None = None, *, ds=None,
                   time_step: np.datetime64 | None = None,
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ERA5 surface elevation (m) as ``(elev, lats asc, lons [-180,180))``.

    ``geopotential_at_surface`` is time-invariant in ERA5, so a single step is
    the whole static field; cached once as a ~4 MB float32 npz. A cache miss
    with no network (unit tests, offline dev) returns an all-zero field, which
    disables the orography screen instead of failing the lane — the screen is a
    false-positive filter, so a zero field is the safe direction.
    """
    p = topo_path(cache_dir)
    if p.exists():
        with np.load(p) as z:
            return z["elev"].astype(np.float32), z["lats"], z["lons"]
    try:
        src = open_arco() if ds is None else ds
        t = time_step if time_step is not None else np.asarray(src.time.values)[0]
        z = src[TOPO_VAR]
        if "time" in z.dims:
            idx = int(np.abs(np.asarray(z.time.values, "datetime64[h]")
                             - np.datetime64(t, "h")).argmin())
            z = z.isel(time=idx)
        elev = z.load() / G0
        if float(elev.latitude.values[0]) < float(elev.latitude.values[-1]):
            elev = elev.isel(latitude=slice(None, None, -1))
        elev = elev.assign_coords(
            longitude=(np.asarray(elev.longitude.values, float) + 180.0) % 360.0 - 180.0)
        elev = elev.sel(latitude=slice(LAT_LIMIT, -LAT_LIMIT))
        arr = np.nan_to_num(
            np.asarray(elev.transpose("latitude", "longitude").values, np.float32),
            nan=0.0)
        lats = np.asarray(elev.latitude.values, float)
        lons = np.asarray(elev.longitude.values, float)
    except Exception as exc:  # noqa: BLE001 - orography is a screen, not a dependency
        logger.warning("mlc_era5: no orography (%s); genesis elevation screen off", exc)
        return np.zeros((0, 0), np.float32), np.zeros(0), np.zeros(0)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(p, elev=arr, lats=lats, lons=lons)
        logger.info("mlc_era5: cached orography %s (%.1f MB)", p, p.stat().st_size / 1e6)
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
