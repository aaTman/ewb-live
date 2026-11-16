"""ERA5 pooled-quantile PRECIPITATION climatology for flood detection.

Owner spec for the ``flood`` event type (2026-11-16): "use ERA5 for
climatologies for QPF, then use IMERG/GPM for the past 90 days of data."
This module owns the CLIMATOLOGY half (thresholds); ``ewb_live.sources.imerg``
(flood-imerg) owns the observations and ``flood-detect`` joins the two.
Design decisions and justification: ``notes/flood_design.md``. The API contract
for the other flood agents is posted in ``notes/channel_new_events.md``.

Product
-------
zarr store (default ``/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr``,
env override ``EWB_LIVE_PRECIP_CLIM``)::

    total_precipitation  (quantile, accumulation, dayofyear, latitude, longitude) float32  [mm]
    wet_sample_count     (accumulation, dayofyear, latitude, longitude)          uint8     [n, saturates at 255]

* **Grid**: the ERA5 / ARCO 0.25° grid — coordinate arrays identical to the live
  temperature climatology ``t2m_owb_1990_2019.zarr`` (latitude 90→-90
  DESCENDING, longitude 0→359.75), so flood masks are cell-aligned with the
  heat/freeze masks and with the dashboard DAYEXT grid.
* **Quantity**: mm of liquid-water equivalent, all precipitation types.
  ``accumulation=1`` is the UTC-day total (00:00-24:00); ``accumulation=3`` is
  the TRAILING 72 h ending at 24:00 UTC of the indexed day (days d-2..d).
* **Estimator**: pooled flat ±``POOL_HALF_WINDOW_DAYS`` day-of-year window over
  1990-2019 (30 years — the same reference period as the temperature
  climatology), i.e. ~930 daily samples per cell per day-of-year. Same
  estimator family as ``owb_clim.py`` (flat window, pooled across years), so
  the two climatology products are methodologically comparable.
* **Wet-day conditioning** (ETCCDI RX1day convention): samples with
  accumulation < ``WET_DAY_MIN_MM`` (1 mm) are excluded. ERA5 reports nonzero
  precipitation over essentially the whole globe on a typical day (drizzle), so
  an all-days percentile in a dry season or dry region collapses toward zero
  and any drizzle would "exceed" it; a rain-flood threshold must be relative to
  the rain-day distribution.
* **Reliability**: the threshold is NaN where the pooled wet sample count is
  below ``MIN_WET_SAMPLES`` (hyper-arid cells — a percentile of a dozen rain
  days in 30 years is not a climate threshold). ``wet_sample_count`` is
  published so consumers can apply their own reliability mask.

Source
------
``gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr``, variable
``total_precipitation_24hr`` **read at 00Z of day D+1 = the whole UTC day D**.
Verified 2026-11-16 against 24 hourly ``total_precipitation`` increments of the
EWB store (``full_37-1h-0p25deg-chunk-1.zarr-v3``): global max abs difference
6e-5 mm; ``total_precipitation_6hr`` is likewise the exact 6-hour accumulation.
Reading one daily field per day instead of 24 hourly ones cuts the anonymous
GCS transfer for 30 years from ~530 GB to ~22 GB (no credentials needed).

Run on the ewb LXC only (SPEC hard rule; 16 cores / 256 GB there)::

    ops/remote.sh bg precipclim env OMP_NUM_THREADS=1 python -m \
      ewb_live.climatology.precip_clim build --workers 8 --threads 20

The daily-totals scratch is a RAM-backed memmap under ``/dev/shm`` (env
override ``EWB_LIVE_PRECIP_SCRATCH``): the box's root disk is at ~98 % and must
never be used for temp space. Both phases are resumable — the download by a day
manifest, the quantile phase by a tile manifest — and both refuse to start when
the disk or memory guards fail.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import xarray as xr

log = logging.getLogger("precip_clim")

# --------------------------------------------------------------------------
# constants / configuration
# --------------------------------------------------------------------------

VAR = "total_precipitation"
COUNT_VAR = "wet_sample_count"

SOURCE_URI = "gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr"
SOURCE_VAR = "total_precipitation_24hr"      # 00Z of D+1 == UTC day D total

YEARS = (1990, 2019)
DEFAULT_QUANTILES = (0.95, 0.99)
DEFAULT_ACCUMS = (1, 3)
POOL_HALF_WINDOW_DAYS = 15    # flat +/- window, circular in day-of-year
WET_DAY_MIN_MM = 1.0          # ETCCDI wet-day threshold
MIN_WET_SAMPLES = 30          # below this the threshold is NaN
TILE = (91, 180)              # compute/write tile (same partition as owb_clim)
FLOOR_MM = {1: 20.0, 3: 50.0}  # recommended absolute plausibility floors (mm)

#: Public grid definitions (not infrastructure). ERA5/ARCO: 0.25 deg point
#: lattice, lat 90..-90 descending, lon 0..359.75. IMERG V06/V07 HalfHourly:
#: 0.1 deg cell-centre lattice, lat 89.95..-89.95, lon -179.95..179.95.
ERA5_GRID = {"lat_step": 0.25, "lon_step": 0.25, "lat_start": 90.0,
             "lon_start": 0.0, "nlat": 721, "nlon": 1440}
IMERG_GRID = {"lat_step": 0.1, "lon_step": 0.1, "lat_start": 89.95,
              "lon_start": -179.95, "nlat": 1800, "nlon": 3600}

MIN_FREE_GB = 6.0             # guard: disk room for the finished store
MIN_MEM_MARGIN_GB = 25.0      # guard: RAM to leave free for live services
STOP_FREE_GB = 2.0            # guard: abort mid-build below this

DONE_NAME = ".done"


def default_out_path() -> pathlib.Path:
    """Where the precipitation climatology is built/read. SPEC portability:
    env-driven with the box layout as the default, like ``owb_clim``."""
    return pathlib.Path(os.environ.get(
        "EWB_LIVE_PRECIP_CLIM",
        "/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr"))


def default_scratch_root() -> pathlib.Path:
    """RAM-backed scratch for the daily-totals array (never the root disk)."""
    return pathlib.Path(os.environ.get("EWB_LIVE_PRECIP_SCRATCH",
                                       "/dev/shm/ewb-live-precip-daily"))


class Config:
    """Everything a build needs; also what the loader validates against."""

    def __init__(self, *, years: tuple[int, int] = YEARS,
                 quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
                 accums: tuple[int, ...] = DEFAULT_ACCUMS,
                 pool: int = POOL_HALF_WINDOW_DAYS,
                 wet_min: float = WET_DAY_MIN_MM,
                 min_samples: int = MIN_WET_SAMPLES,
                 out: pathlib.Path | str | None = None,
                 scratch: pathlib.Path | str | None = None,
                 dtype: str = "float32",
                 source_uri: str | None = None,
                 source_var: str | None = None,
                 tile: tuple[int, int] = TILE,
                 limit_days: int | None = None) -> None:
        self.years = tuple(years)
        self.quantiles = tuple(float(q) for q in quantiles)
        self.accums = tuple(int(w) for w in accums)
        self.pool = int(pool)
        self.wet_min = float(wet_min)
        self.min_samples = int(min_samples)
        self.out = default_out_path() if out is None else pathlib.Path(out)
        self.dtype = np.dtype(dtype)
        self.scratch = (default_scratch_root() if scratch is None
                        else pathlib.Path(scratch))
        self.source_uri = source_uri or SOURCE_URI
        self.source_var = source_var or SOURCE_VAR
        self.tile = (int(tile[0]), int(tile[1]))
        self.limit_days = limit_days
        self.lead = max(self.accums) - 1        # extra days before Jan 1
        self.start = dt.date(self.years[0], 1, 1) - dt.timedelta(days=self.lead)
        self.end = dt.date(self.years[1], 12, 31)

    # -- time axis -------------------------------------------------------
    def dates(self) -> np.ndarray:
        n = (self.end - self.start).days + 1
        if self.limit_days:
            n = min(n, self.limit_days)
        return np.datetime64(self.start) + np.arange(n).astype("timedelta64[D]")

    def dayofyear(self) -> np.ndarray:
        t = self.dates().astype("datetime64[D]")
        return (t - t.astype("datetime64[Y]")).astype(int) + 1

    def ref_mask(self) -> np.ndarray:
        """True inside the reference period: the lead-in days exist only to
        complete trailing accumulations, they are never pooled themselves."""
        t = self.dates()
        return ((t >= np.datetime64(dt.date(self.years[0], 1, 1)))
                & (t <= np.datetime64(dt.date(self.years[1], 12, 31))))

    def pool_index(self) -> dict[int, np.ndarray]:
        """day-of-year -> end-day positions inside the circular +/- pool window
        (positions index ``self.dates()``)."""
        doy = self.dayofyear()
        ref = np.flatnonzero(self.ref_mask())
        out: dict[int, np.ndarray] = {}
        for d in range(1, 367):
            dist = np.abs(doy[ref] - d)
            dist = np.minimum(dist, 366 - dist)
            out[d] = ref[dist <= self.pool]
        return out

    # -- scratch ---------------------------------------------------------
    def scratch_file(self, lat_pass: int = 0) -> pathlib.Path:
        return self.scratch / (f"daily_{self.years[0]}_{self.years[1]}"
                               f"_{self.dtype.name}_p{lat_pass}.bin")

    def scratch_meta_path(self, lat_pass: int = 0) -> pathlib.Path:
        return self.scratch / f"scratch_meta_p{lat_pass}.json"

    def scratch_bytes(self, nlat: int, nlon: int) -> int:
        return len(self.dates()) * nlat * nlon * self.dtype.itemsize

    def meta_path(self) -> pathlib.Path:
        """Sidecar (outside the store) with the build provenance."""
        return self.out.parent / (self.out.name + ".meta.json")

    def state_path(self) -> pathlib.Path:
        return self.out.parent / (self.out.name + ".tile_state.json")

    def fingerprint(self) -> dict:
        return {"source": f"{self.source_uri}#{self.source_var}",
                "years": list(self.years), "quantiles": list(self.quantiles),
                "accums": list(self.accums),
                "pool_half_window_days": self.pool,
                "wet_day_min_mm": self.wet_min,
                "min_wet_samples": self.min_samples,
                "dtype": self.dtype.name, "tile": list(self.tile),
                "grid": "era5_arco_0p25", "units": "mm",
                "accumulation_definition": {
                    "1": "UTC-day total 00:00-24:00",
                    "3": "trailing 72 h ending 24:00 UTC of the indexed day"}}

    def estimator_text(self) -> str:
        return (f"pooled flat +/-{self.pool}-day day-of-year window over "
                f"{self.years[0]}-{self.years[1]} daily ERA5 totals (0.25 deg), "
                f"wet samples only (>={self.wet_min} mm), linear-interpolated "
                f"quantile, NaN where pooled wet samples < {self.min_samples}")


# --------------------------------------------------------------------------
# box guards (shared LIVE box: root disk ~98 % full, other agents' jobs in RAM)
# --------------------------------------------------------------------------

def free_gb(path: pathlib.Path) -> float:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def mem_available_gb() -> float:
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    except (OSError, ValueError):
        return np.inf
    return np.inf


def check_disk_guard(cfg: Config) -> None:
    free = free_gb(cfg.out)
    if free < MIN_FREE_GB:
        raise SystemExit(
            f"precip_clim: only {free:.1f} GB free on the filesystem holding "
            f"{cfg.out} (need >= {MIN_FREE_GB} GB). Prune data/cache or set "
            "EWB_LIVE_PRECIP_CLIM to a roomier path; refusing to fill a live "
            "box.")


def check_mem_guard(cfg: Config, nlat: int, nlon: int) -> None:
    need = cfg.scratch_bytes(nlat, nlon) / 1e9
    avail = mem_available_gb()
    if avail < need + MIN_MEM_MARGIN_GB:
        raise SystemExit(
            f"precip_clim: the daily scratch needs {need:.1f} GB in "
            f"{cfg.scratch} but only {avail:.1f} GB is available (leave "
            f"{MIN_MEM_MARGIN_GB} GB free for the live services). Retry with "
            "--lat-passes N (N x the network, 1/N the RAM), "
            "--scratch-dtype float16, or a roomier EWB_LIVE_PRECIP_SCRATCH.")


# --------------------------------------------------------------------------
# remote source
# --------------------------------------------------------------------------

_tls = threading.local()


def _source_dataset() -> xr.Dataset:
    from ewb_live import _gcsfs_speedup

    _gcsfs_speedup.apply()
    return xr.open_zarr(SOURCE_URI, storage_options={"token": "anon"},
                        chunks=None)


def _thread_source() -> xr.DataArray:
    """Per-thread handle (gcsfs readers are not thread-shared)."""
    if not hasattr(_tls, "da"):
        _tls.da = _source_dataset()[SOURCE_VAR]
    return _tls.da


def source_grid() -> tuple[np.ndarray, np.ndarray]:
    """(latitude DESCENDING, longitude 0..359.75) of the ERA5 source grid."""
    ds = _source_dataset()
    lat = np.asarray(ds.latitude.values, dtype="float64")
    lon = np.asarray(ds.longitude.values, dtype="float64")
    ds.close()
    return lat, lon


def daily_field(date64: np.datetime64) -> np.ndarray:
    """Global mm/day field for a UTC day (the store's 24 h accumulation is
    stamped at the END timestamp, i.e. 00Z of the following day)."""
    v = _thread_source().sel(time=np.datetime64(date64)
                             + np.timedelta64(1, "D")).values
    v = np.asarray(v, dtype="float32") * 1000.0          # m -> mm
    return v.reshape(v.shape[-2:])


# --------------------------------------------------------------------------
# latitude passes (RAM vs network knob)
# --------------------------------------------------------------------------

def lat_tiles(cfg: Config, nlat_all: int) -> int:
    return -(-nlat_all // cfg.tile[0])


def pass_rows(cfg: Config, nlat_pass: int, p: int,
              nlat_all: int) -> tuple[int, int, list[int]]:
    """Rows and tile ids owned by latitude pass ``p`` (tile-aligned).

    Chunks in the source store are one GLOBAL field per timestep, so a pass
    still downloads the whole globe; splitting by latitude trades network for
    RAM (``nlat_pass`` passes = ``nlat_pass`` x download, 1/n of the scratch).
    """
    nt = lat_tiles(cfg, nlat_all)
    per = -(-nt // nlat_pass)
    ids = list(range(p * per, min((p + 1) * per, nt)))
    if not ids:
        return 0, 0, []
    r0 = ids[0] * cfg.tile[0]
    r1 = min((ids[-1] + 1) * cfg.tile[0], nlat_all)
    return r0, r1, ids


# --------------------------------------------------------------------------
# phase 1: download the daily-totals archive into the RAM scratch
# --------------------------------------------------------------------------

def download(cfg: Config, *, threads: int = 20, lat_pass: int = 0,
             nlat_pass: int = 1, quiet: bool = False) -> dict:
    """Fill the scratch memmap with daily totals (mm) for ``cfg.dates()``.

    Idempotent and resumable: a boolean day manifest marks completed days, so
    an interrupted run resumes where it stopped.
    """
    lat, lon = source_grid()
    r0, r1, _ = pass_rows(cfg, nlat_pass, lat_pass, len(lat))
    nlat, nlon = r1 - r0, len(lon)
    if nlat <= 0:
        return {"requested": 0, "fetched": 0, "skipped": True}

    check_disk_guard(cfg)
    check_mem_guard(cfg, nlat, nlon)
    cfg.scratch.mkdir(parents=True, exist_ok=True)
    f = cfg.scratch_file(lat_pass)
    man_p = cfg.scratch / (f.name + ".done.npy")
    dates = cfg.dates()
    n = len(dates)
    nbytes = n * nlat * nlon * cfg.dtype.itemsize

    if f.exists():
        got = np.load(man_p)
        if got.shape != (n,):
            raise SystemExit(f"precip_clim: scratch manifest {man_p} shape "
                             f"{got.shape} != {(n,)}; delete {cfg.scratch} "
                             "to rebuild")
    else:
        f.touch()
        with open(f, "wb") as fh:
            fh.truncate(nbytes)
        got = np.zeros(n, dtype=bool)
        np.save(man_p, got)
    cfg.scratch_meta_path(lat_pass).write_text(json.dumps(
        {"lat_pass": lat_pass, "nlat_pass": nlat_pass, "rows": [r0, r1],
         "nlat": nlat, "nlon": nlon, "dtype": cfg.dtype.name,
         "ndays": n, "date_first": str(dates[0]), "date_last": str(dates[-1]),
         "tiles": pass_rows(cfg, nlat_pass, lat_pass, len(lat))[2],
         **cfg.fingerprint()}, indent=1))

    mm = np.memmap(f, dtype=cfg.dtype, mode="r+", shape=(n, nlat, nlon))
    todo = [int(k) for k in np.flatnonzero(~got)]
    if not todo:
        log.info("scratch complete: %s (%d days)", f, got.sum())
        return {"requested": n, "fetched": 0, "days": int(got.sum()),
                "bytes": nbytes, "rows": [r0, r1], "seconds": 0.0}

    log.info("download: %d/%d days missing (rows %d:%d, %.1f GB scratch)",
             len(todo), n, r0, r1, nbytes / 1e9)
    import time as _t

    t0 = _t.monotonic()
    lock = threading.Lock()
    counters = {"fetched": 0, "errors": 0}

    def _task(k: int) -> None:
        try:
            rows = daily_field(dates[k])[r0:r1]
            if np.isnan(rows).all():
                raise RuntimeError("all-NaN field")
            mm[k] = rows.astype(cfg.dtype, copy=False)
        except Exception as exc:  # noqa: BLE001 - one flaky object must not kill it
            log.warning("daily read failed %s: %s", dates[k], exc)
            with lock:
                counters["errors"] += 1
            return
        with lock:
            got[k] = True
            counters["fetched"] += 1
            if counters["fetched"] % 200 == 0:
                np.save(man_p, got)
                mm.flush()
                log.info("download: %d/%d days (%.0f MB/s)",
                         counters["fetched"], len(todo),
                         counters["fetched"] * nlat * nlon * 4 / 1e6
                         / max(_t.monotonic() - t0, 1e-6))

    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(_task, todo))
    np.save(man_p, got)
    mm.flush()
    del mm
    secs = _t.monotonic() - t0
    out = {"requested": n, "fetched": counters["fetched"],
           "days": int(got.sum()), "errors": counters["errors"],
           "bytes": nbytes, "rows": [r0, r1], "seconds": round(secs, 1),
           "mb_s_uncompressed": round(nbytes / 1e6 / max(secs, 1e-6), 1)}
    log.info("download: %s", out)
    return out


# --------------------------------------------------------------------------
# phase 2: pooled wet-conditioned quantiles, tile by tile
# --------------------------------------------------------------------------

def quantile_from_sorted(s: np.ndarray, n: np.ndarray, q: float,
                         min_samples: int) -> np.ndarray:
    """Linear-interpolated quantile of each cell's valid prefix of ``s``.

    ``s`` is the pooled sample axis sorted ascending along axis 0 with NaN at
    the end (numpy sorts NaN last), so positions ``0..n-1`` hold a cell's valid
    samples. The addressed positions never exceed ``n-1``, so NaN samples can
    not leak into a result; cells with too few samples are blanked.
    """
    nc = np.maximum(n, 1)
    pos = q * (nc - 1)
    lo = np.floor(pos).astype(np.intp)
    hi = np.minimum(lo + 1, nc - 1)
    frac = pos - lo
    v0 = np.take_along_axis(s, lo[None], axis=0)[0]
    v1 = np.take_along_axis(s, hi[None], axis=0)[0]
    out = v0 + frac * (v1 - v0)
    out[n < min_samples] = np.nan
    return out.astype("float32")


def pool_values(sub: np.ndarray, idx: np.ndarray, accum: int) -> np.ndarray:
    """(n_idx, lat, lon) accumulation values for end-day positions ``idx``:
    ``accum`` consecutive days ending at each position (trailing window).

    Positions nearer the start of the scratch than ``accum - 1`` days are
    dropped (the scratch carries ``max(accums) - 1`` lead-in days precisely so
    that the reference period never hits this, but a wrapped ``np.take`` index
    would silently read the END of the record, so guard it).
    """
    idx = idx[idx >= accum - 1]
    vals = np.take(sub, idx, axis=0).astype("float32", copy=True)
    for k in range(1, accum):
        vals += np.take(sub, idx - k, axis=0)
    return vals


def _tile(cfg: Config, lat_pass: int, tile_id: int, j: int,
          pools: dict[int, np.ndarray], lat: np.ndarray,
          lon: np.ndarray) -> list[int]:
    """Compute and region-write one (lat tile x lon tile) block."""
    meta = json.loads(cfg.scratch_meta_path(lat_pass).read_text())
    if meta.get("fingerprint") is not None and \
            {k: meta[k] for k in cfg.fingerprint()} != cfg.fingerprint():
        raise SystemExit("precip_clim: scratch was written with a different "
                         "configuration; delete it and rebuild")
    r0, r1 = meta["rows"]
    nlon = meta["nlon"]
    j0, j1 = j * cfg.tile[1], min((j + 1) * cfg.tile[1], nlon)
    mm = np.memmap(cfg.scratch_file(lat_pass), dtype=cfg.dtype, mode="r",
                   shape=(meta["ndays"], r1 - r0, nlon))
    sub = mm[:, :, j0:j1]        # the pass's latitude rows are stored packed

    nq, na, ni = len(cfg.quantiles), len(cfg.accums), r1 - r0
    res = np.full((nq, na, 366, ni, j1 - j0), np.nan, "float32")
    cnt = np.zeros((na, 366, ni, j1 - j0), "uint8")
    for d in range(1, 367):
        idx = pools[d]
        if idx.size < cfg.min_samples:
            continue
        for ai, w in enumerate(cfg.accums):
            vals = pool_values(sub, idx, w)
            ok = np.isfinite(vals) & (vals >= cfg.wet_min)
            n_wet = ok.sum(axis=0).astype(np.int32)
            np.copyto(vals, np.nan, where=~ok)
            s = np.sort(vals, axis=0)
            for qi, q in enumerate(cfg.quantiles):
                res[qi, ai, d - 1] = quantile_from_sorted(s, n_wet, q,
                                                         cfg.min_samples)
            cnt[ai, d - 1] = np.minimum(n_wet, 255).astype("uint8")
            del vals, ok, s, n_wet
    del mm, sub

    ilat, ilon = slice(r0, r1), slice(j0, j1)
    ds = xr.Dataset(
        {VAR: (("quantile", "accumulation", "dayofyear", "latitude",
                "longitude"), res),
         COUNT_VAR: (("accumulation", "dayofyear", "latitude", "longitude"),
                     cnt)},
        coords={"quantile": list(cfg.quantiles),
                "accumulation": list(cfg.accums),
                "dayofyear": np.arange(1, 367),
                "latitude": lat[ilat], "longitude": lon[ilon]})
    ds[VAR].attrs.update({"units": "mm", "long_name":
                          "ERA5 pooled wet-day precipitation threshold (mm)",
                          "era5_name": "total_precipitation"})
    ds.to_zarr(cfg.out, region={"quantile": slice(None),
                                "accumulation": slice(None),
                                "dayofyear": slice(None),
                                "latitude": ilat, "longitude": ilon})
    log.info("tile pass%d lat%d lon%d done (rows %d:%d, cols %d:%d)",
             lat_pass, tile_id, j, r0, r1, j0, j1)
    return [lat_pass, tile_id, j]


def write_template(cfg: Config, lat: np.ndarray, lon: np.ndarray) -> None:
    """Create the empty (lazy) store that the tile workers region-write into."""
    import dask.array as da

    nq, na = len(cfg.quantiles), len(cfg.accums)
    tmpl = xr.Dataset(
        {VAR: (("quantile", "accumulation", "dayofyear", "latitude", "longitude"),
               da.zeros((nq, na, 366, len(lat), len(lon)), dtype="float32")),
         COUNT_VAR: (("accumulation", "dayofyear", "latitude", "longitude"),
                     da.zeros((na, 366, len(lat), len(lon)), dtype="uint8"))},
        coords={"quantile": list(cfg.quantiles),
                "accumulation": list(cfg.accums),
                "dayofyear": np.arange(1, 367),
                "latitude": lat, "longitude": lon},
    ).chunk({"quantile": 1, "accumulation": 1, "dayofyear": 31,
             "latitude": cfg.tile[0], "longitude": cfg.tile[1]})
    tmpl.attrs.update(cfg.fingerprint())
    tmpl.attrs["estimator"] = cfg.estimator_text()
    tmpl[VAR].attrs.update({"units": "mm",
                            "long_name": "ERA5 pooled wet-day precipitation "
                                         "threshold (mm)",
                            "source_variable": SOURCE_VAR,
                            "source_reading": (
                                f"{SOURCE_VAR} at 00Z of D+1 = UTC day D total "
                                "(verified against hourly total_precipitation "
                                "increments of the ARCO full store)")})
    # (zarr v3 on disk, the same format the live t2m climatology uses; the
    #  writer is xarray/zarr-python, so no format encoding override here)
    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    tmpl.to_zarr(cfg.out, mode="w", compute=False)


def compute(cfg: Config, *, workers: int = 8, nlat_pass: int = 1,
            keep_scratch: bool = True, limit_tiles: int | None = None) -> dict:
    """Build the quantile product tile by tile (resumable per tile)."""
    import joblib

    lat, lon = source_grid()
    pools = cfg.pool_index()
    check_disk_guard(cfg)
    if not cfg.scratch_meta_path(0).exists():
        raise SystemExit("precip_clim: no scratch metadata; run the download "
                         "phase first (or check EWB_LIVE_PRECIP_SCRATCH)")

    jobs: list[list[int]] = []
    for p in range(nlat_pass):
        _, _, ids = pass_rows(cfg, nlat_pass, p, len(lat))
        if not ids:
            continue
        if not cfg.scratch_meta_path(p).exists():
            raise SystemExit(f"precip_clim: scratch for lat pass {p} is "
                             "missing; run the build phase for that pass")
        jobs += [[p, i, j] for i in ids for j in range(-(-len(lon) // cfg.tile[1]))]

    state = ({"done": [], "nlat_pass": nlat_pass, "fingerprint": cfg.fingerprint()}
             if not cfg.state_path().exists() else
             json.loads(cfg.state_path().read_text()))
    if state.get("fingerprint") != cfg.fingerprint():
        raise SystemExit("precip_clim: the partial build on disk has a "
                         "different configuration; delete the store, its "
                         f"{cfg.state_path().name} and the scratch to rebuild")
    if state.get("nlat_pass") != nlat_pass:
        raise SystemExit("precip_clim: --lat-passes changed mid-build")
    if limit_tiles:
        jobs = jobs[:limit_tiles]
    todo = [x for x in jobs if list(x) not in [list(y) for y in state["done"]]]
    if not cfg.out.exists():
        write_template(cfg, lat, lon)
    log.info("compute: %d tiles to do, %d already done, workers=%d",
             len(todo), len(state["done"]), workers)

    def _one(args):
        p, i, j = args
        return _tile(cfg, p, i, j, pools, lat, lon)

    for done in joblib.Parallel(n_jobs=workers)(
            joblib.delayed(_one)(a) for a in todo):
        state["done"].append(done)
        tmp = cfg.state_path().with_name(cfg.state_path().name + ".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(cfg.state_path())
        if free_gb(cfg.out) < STOP_FREE_GB:
            raise SystemExit("precip_clim: aborted, filesystem below "
                             f"{STOP_FREE_GB} GB free (live box protection)")

    complete = (limit_tiles is None and len(jobs) > 0
                and len(state["done"]) >= len(jobs))
    if complete:
        cfg.meta_path().write_text(json.dumps(
            {**cfg.fingerprint(), "estimator": cfg.estimator_text(),
             "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
             "tiles": len(state["done"])}, indent=1))
        (cfg.out / DONE_NAME).write_text("ok\n")
        cfg.state_path().unlink(missing_ok=True)
        if not keep_scratch:
            shutil.rmtree(cfg.scratch, ignore_errors=True)
    log.info("compute: store %s (%d/%d tiles, complete=%s)", cfg.out,
             len(state["done"]), len(jobs), complete)
    return {"tiles_done": len(state["done"]), "tiles_total": len(jobs),
            "complete": bool(complete)}


def build(cfg: Config, *, workers: int = 8, threads: int = 20,
          nlat_pass: int = 1, keep_scratch: bool = True,
          limit_tiles: int | None = None) -> dict:
    """Full pipeline: download every latitude pass, then compute all tiles."""
    lat, _ = source_grid()
    summary: dict = {"config": cfg.fingerprint(), "out": str(cfg.out),
                     "passes": []}
    for p in range(nlat_pass):
        if cfg.scratch_meta_path(p).exists() and not cfg.scratch_file(p).exists():
            cfg.scratch_meta_path(p).unlink()      # stale metadata, no data
        summary["passes"].append(download(cfg, threads=threads, lat_pass=p,
                                          nlat_pass=nlat_pass))
    summary.update(compute(cfg, workers=workers, nlat_pass=nlat_pass,
                           keep_scratch=keep_scratch, limit_tiles=limit_tiles))
    return summary


# --------------------------------------------------------------------------
# loader / public API (flood-detect codes against these)
# --------------------------------------------------------------------------

_CACHE: dict[str, xr.Dataset] = {}


def open_climatology(store: pathlib.Path | str | None = None) -> xr.Dataset:
    """Open the store (cached per path), or fail with build instructions."""
    p = default_out_path() if store is None else pathlib.Path(store)
    if not (p / DONE_NAME).exists():
        raise FileNotFoundError(
            f"precipitation climatology not built at {p}; build it on the box: "
            "ops/remote.sh bg precipclim python -m ewb_live.climatology."
            "precip_clim build")
    key = str(p)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = xr.open_zarr(p, consolidated=False,
                                   chunks={"dayofyear": 31})
    return _CACHE[key]


def _check_coords(quantile: float, window: int, ds: xr.Dataset) -> None:
    qs = [float(q) for q in ds["quantile"].values]
    ws = [int(w) for w in ds["accumulation"].values]
    if float(quantile) not in qs:
        raise ValueError(f"quantile {quantile} not in climatology {qs}")
    if int(window) not in ws:
        raise ValueError(f"accumulation window {window} d not in "
                         f"climatology {ws}")


def day_of_year(day) -> int:
    if isinstance(day, str):
        d = dt.date.fromisoformat(day[:10])
    elif isinstance(day, np.datetime64):
        d = day.astype("datetime64[D]").astype(dt.date)
    elif isinstance(day, dt.datetime):
        d = day.date()
    else:
        d = day
    return d.timetuple().tm_yday


def grid_coords(grid: str, nlat: int | None = None, nlon: int | None = None,
                store: pathlib.Path | str | None = None,
                ) -> tuple[np.ndarray, np.ndarray]:
    """Latitude/longitude vectors of the supported target grids."""
    if grid in ("era5", "native"):
        ds = open_climatology(store)
        return np.asarray(ds.latitude.values), np.asarray(ds.longitude.values)
    if grid in ("imerg", "gpm"):
        s = IMERG_GRID
        nlat = nlat or s["nlat"]
        nlon = nlon or s["nlon"]
        return (s["lat_start"] - s["lat_step"] * np.arange(nlat),
                s["lon_start"] + s["lon_step"] * np.arange(nlon))
    raise ValueError(f"unknown grid {grid!r} (use 'era5' or 'imerg')")


def _target_indices(lats: np.ndarray, lons: np.ndarray, src_lat: np.ndarray,
                    src_lon: np.ndarray, method: str):
    """Row/col indices (+weights) of a target lattice on the source grid."""
    if lats.max() > src_lat.max() + 1e-6 or lats.min() < src_lat.min() - 1e-6:
        raise ValueError(f"target latitudes {lats.min()}..{lats.max()} outside "
                         f"the climatology grid {src_lat.min()}.."
                         f"{src_lat.max()}")
    if lons.min() < -180.0 - 1e-6 or lons.max() >= 180.0 + 1e-6:
        raise ValueError("target longitudes must be in [-180, 180)")
    step_y = float(np.median(np.diff(src_lat)))              # negative: desc
    step_x = float(np.median(np.diff(src_lon)))
    fy = (lats - src_lat[0]) / step_y
    fx = np.mod(lons, 360.0) / step_x                        # lon 0..359.75
    if method == "bilinear":
        iy = np.clip(np.floor(fy).astype(int), 0, len(src_lat) - 2)
        ix = np.floor(fx).astype(int) % len(src_lon)
        wy = np.clip(fy - iy, 0.0, 1.0)
        wx = np.clip(fx - np.floor(fx), 0.0, 1.0)
    else:
        iy = np.clip(np.rint(fy).astype(int), 0, len(src_lat) - 1)
        ix = np.rint(fx).astype(int) % len(src_lon)
        wy = wx = None
    return iy, ix, wy, wx


def precip_threshold_on(lats, lons, dates, quantile: float = 0.95,
                        window: int = 1, *, method: str = "nearest",
                        store: pathlib.Path | str | None = None) -> xr.DataArray:
    """Thresholds on an arbitrary target lattice.

    Returns ``(latitude, longitude)`` for a single date and
    ``(time, latitude, longitude)`` for several dates, in mm.

    ``method='nearest'`` (default) keeps the field piecewise constant:
    interpolating a 0.25 deg threshold field onto, say, IMERG's 0.1 deg lattice
    would smooth exactly the extreme maxima we threshold on. ``bilinear`` is
    available for areal/visual use.
    """
    ds = open_climatology(store)
    _check_coords(quantile, window, ds)
    if method not in ("nearest", "bilinear"):
        raise ValueError(f"unknown method {method!r}")
    lats = np.asarray(lats, dtype="float64")
    lons = np.asarray(lons, dtype="float64")
    src_lat = np.asarray(ds.latitude.values, dtype="float64")
    src_lon = np.asarray(ds.longitude.values, dtype="float64")
    iy, ix, wy, wx = _target_indices(lats, lons, src_lat, src_lon, method)

    single = not isinstance(dates, (list, tuple, np.ndarray, xr.DataArray))
    vals = [dates] if single else list(dates)
    times = np.array([np.datetime64(str(t)[:10]) for t in vals])
    field = ds[VAR].sel(quantile=float(quantile), accumulation=int(window))
    out = np.empty((times.size, lats.size, lons.size), "float32")
    for k, t in enumerate(times):
        f = field.isel(dayofyear=day_of_year(t) - 1).values
        if method == "bilinear":
            a = f[np.ix_(iy, ix)]
            b = f[np.ix_(iy + 1, ix)]
            c = f[np.ix_(iy, (ix + 1) % src_lon.size)]
            e = f[np.ix_(iy + 1, (ix + 1) % src_lon.size)]
            wy2, wx2 = wy[:, None], wx[None, :]
            out[k] = ((1 - wy2) * ((1 - wx2) * a + wx2 * c)
                      + wy2 * ((1 - wx2) * b + wx2 * e))
        else:
            out[k] = f[np.ix_(iy, ix)]

    coords: dict = {"latitude": lats, "longitude": lons}
    dims = ["latitude", "longitude"]
    if not single:
        coords["time"] = times
        dims = ["time", "latitude", "longitude"]
    return xr.DataArray(
        out if not single else out[0], dims=dims, coords=coords,
        name=f"{VAR}_p{int(round(quantile * 100))}_d{window}",
        attrs={"units": "mm", "quantile": float(quantile),
               "accumulation_days": int(window),
               "accumulation_convention": (
                   "trailing window ending 24:00 UTC of the date" if window > 1
                   else "UTC-day total 00:00-24:00"),
               "grid": "era5_arco_0p25",
               "resample": method,
               "climatology_source": "era5_pooled_wet_day_percentile"})


def _apply_scale(th: xr.DataArray, scale) -> xr.DataArray:
    """Optional IMERG<->ERA5 bias-correction seam (default: nothing)."""
    if scale is None:
        return th
    if isinstance(scale, (int, float, np.floating, np.integer)):
        out = th * float(scale)
        out.attrs["bias_scale"] = f"constant {float(scale)}"
        return out
    s = scale if isinstance(scale, xr.DataArray) else xr.open_dataarray(str(scale))
    out = th * s
    out.attrs["bias_scale"] = "applied from " + str(s.name or "DataArray")
    return out


def precip_threshold(date, quantile: float = 0.95, window: int = 1, *,
                     grid: str = "era5", scale=None,
                     store: pathlib.Path | str | None = None) -> xr.DataArray:
    """One date -> (latitude, longitude) precipitation threshold in mm.

    ``window`` is the ACCUMULATION length in days (1 or 3) — NOT the pooling
    half-width; ``window=3`` is the trailing 72 h ending 24:00 UTC of ``date``.
    ``grid``: ``"era5"`` (native 0.25 deg, latitude descending) or ``"imerg"``
    (IMERG V06/V07 0.1 deg lattice, lon -180..180).
    """
    lats, lons = grid_coords(grid, store=store)
    return _apply_scale(precip_threshold_on(lats, lons, date, quantile, window,
                                            store=store), scale)


def precip_threshold_series(dates, quantile: float = 0.95, window: int = 1, *,
                            grid: str = "era5", scale=None,
                            store: pathlib.Path | str | None = None) -> xr.DataArray:
    """Many dates -> (time, latitude, longitude) thresholds in mm (one read)."""
    lats, lons = grid_coords(grid, store=store)
    return _apply_scale(precip_threshold_on(lats, lons, dates, quantile, window,
                                            store=store), scale)


def wet_sample_count(date, window: int = 1, *, grid: str = "era5",
                     store: pathlib.Path | str | None = None) -> xr.DataArray:
    """Pooled wet-sample count behind the threshold (reliability mask)."""
    ds = open_climatology(store)
    lats, lons = grid_coords(grid, store=store)
    src_lat = np.asarray(ds.latitude.values, dtype="float64")
    src_lon = np.asarray(ds.longitude.values, dtype="float64")
    iy, ix, _, _ = _target_indices(lats, lons, src_lat, src_lon, "nearest")
    v = ds[COUNT_VAR].sel(accumulation=int(window)).isel(
        dayofyear=day_of_year(date) - 1).values
    return xr.DataArray(v[np.ix_(iy, ix)], dims=("latitude", "longitude"),
                        coords={"latitude": lats, "longitude": lons},
                        name=COUNT_VAR, attrs={"units": "samples"})


def climatology_meta(store: pathlib.Path | str | None = None) -> dict:
    """meta.json / About-dialog facing description of the flood thresholds."""
    p = default_out_path() if store is None else pathlib.Path(store)
    built = (p / DONE_NAME).exists()
    info = {
        "source": ("era5_pooled_wet_day_percentile" if built else
                   "era5_pooled_wet_day_percentile (NOT BUILT — flood "
                   "detection unavailable)"),
        "path": p.name,           # filename only: meta.json is public
        "percentiles": list(DEFAULT_QUANTILES),
        "accumulations_days": list(DEFAULT_ACCUMS),
        "units": "mm",
        "reference_period": f"{YEARS[0]}-{YEARS[1]}",
        "estimator": ("pooled flat +/-15-day day-of-year window over ERA5 "
                      "daily totals (0.25 deg), wet days only (>=1 mm), NaN "
                      "where pooled wet days < 30"),
        "origin": ("ewb_live/climatology/precip_clim.py 2026-11-16 from "
                   "gs://gcp-public-data-arco-era5 (anonymous)"),
        "recommended_floor_mm": FLOOR_MM,
    }
    side = p.parent / (p.name + ".meta.json")
    if side.exists():
        try:
            info.update({"generated": json.loads(side.read_text()).get("generated")})
        except Exception:  # noqa: BLE001
            pass
    return info


# --------------------------------------------------------------------------
# sanity reporting (numbers and maps for notes/flood_design.md)
# --------------------------------------------------------------------------

REGIONS = {
    "amazon": (-12.0, 3.0, -76.0, -49.0),
    "sahara": (20.0, 31.0, -6.0, 31.0),
    "sahel": (11.0, 18.0, -12.0, 30.0),
    "monsoon_india": (8.0, 26.0, 71.0, 93.0),
    "nw_europe": (48.0, 61.0, -9.0, 16.0),
    "us_gulf": (25.0, 32.0, -98.0, -84.0),
    "south_china": (21.0, 32.0, 106.0, 122.0),
}


def _lon_index(lon_src: np.ndarray, lo0: float, lo1: float) -> np.ndarray:
    """Source-grid longitude indices spanning [lo0, lo1] (handles wrapping)."""
    step = float(np.median(np.diff(lon_src)))
    a, b = np.mod(lo0, 360.0), np.mod(lo1, 360.0)
    ia, ib = int(a // step), int(b // step)
    idx = np.arange(lon_src.size)
    return idx[(idx >= ia) & (idx <= ib)] if a <= b \
        else idx[(idx >= ia) | (idx <= ib)]


def region_stats(doy_pair: tuple[int, ...] = (15, 200),
                 store: pathlib.Path | str | None = None) -> dict:
    """Threshold statistics per region for the given day-of-year slices."""
    ds = open_climatology(store)
    out: dict = {"doy": list(doy_pair), "regions": {}}
    lat = np.asarray(ds.latitude.values, dtype="float64")
    lon = np.asarray(ds.longitude.values, dtype="float64")
    for name, (la0, la1, lo0, lo1) in REGIONS.items():
        iy = np.flatnonzero((lat <= la1) & (lat >= la0))
        ix = _lon_index(lon, lo0, lo1)
        out["regions"][name] = {}
        for d in doy_pair:
            th = ds[VAR].isel(dayofyear=int(d) - 1)
            cn = ds[COUNT_VAR].isel(dayofyear=int(d) - 1)
            for w in [int(x) for x in ds["accumulation"].values]:
                for q in [float(x) for x in ds["quantile"].values]:
                    v = th.sel(accumulation=w, quantile=q).values[np.ix_(iy, ix)]
                    c = cn.sel(accumulation=w).values[np.ix_(iy, ix)]
                    out["regions"][name][f"doy{d}_d{w}d_p{int(q * 100)}"] = {
                        "mean_mm": round(float(np.nanmean(v)), 2),
                        "median_mm": round(float(np.nanmedian(v)), 2),
                        "p90_mm": round(float(np.nanpercentile(v, 90)), 2),
                        "max_mm": round(float(np.nanmax(v)), 1),
                        "nan_frac": round(float(np.isnan(v).mean()), 3),
                        "wet_n_median": float(np.median(c)),
                        "wet_n_min": int(np.min(c))}
    return out


def write_maps(outdir: pathlib.Path | str, doys: tuple[int, ...] = (15, 200),
               store: pathlib.Path | str | None = None,
               quantile: float = 0.99) -> list[pathlib.Path]:
    """Sanity figures: threshold field over the world and three zooms."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ds = open_climatology(store)
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    zooms = {"world": (-80.0, 80.0, None, None),
             "amazon": (-12.0, 3.0, -76.0, -49.0),
             "sahara_and_sahel": (11.0, 33.0, -12.0, 35.0),
             "monsoon_india": (6.0, 30.0, 65.0, 96.0)}
    files: list[pathlib.Path] = []
    for d in doys:
        for w in [int(x) for x in ds["accumulation"].values]:
            fig, axes = plt.subplots(2, 2, figsize=(15, 9))
            for ax, (zname, (la0, la1, lo0, lo1)) in zip(axes.ravel(),
                                                          zooms.items()):
                v = ds[VAR].sel(quantile=float(quantile), accumulation=int(w))
                if lo0 is None:
                    sub = v.isel(dayofyear=int(d) - 1)
                else:
                    lat = np.asarray(ds.latitude.values, dtype="float64")
                    ix = _lon_index(np.asarray(ds.longitude.values), lo0, lo1)
                    iy = np.flatnonzero((lat <= la1) & (lat >= la0))
                    sub = v.isel(dayofyear=int(d) - 1, latitude=iy, longitude=ix)
                hi = float(np.nanpercentile(sub.values, 99.5)) or 1.0
                im = ax.pcolormesh(sub.longitude, sub.latitude, sub.values,
                                   vmin=0, vmax=hi, cmap="magma",
                                   shading="nearest")
                ax.set_title(f"p{int(quantile * 100)} {w}-day threshold, "
                             f"doy {d} — {zname} (mm)")
                ax.set_xticks(np.linspace(-180, 180, 7))
                fig.colorbar(im, ax=ax, fraction=0.046)
            f = out / f"precip_p{int(quantile * 100)}_{w}day_doy{d}.png"
            fig.tight_layout()
            fig.savefig(f, dpi=110)
            plt.close(fig)
            files.append(f)
    return files


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(
        prog="python -m ewb_live.climatology.precip_clim")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build the climatology (ewb LXC only)")
    b.add_argument("--start", type=int, default=YEARS[0])
    b.add_argument("--end", type=int, default=YEARS[1])
    b.add_argument("--out", default=None)
    b.add_argument("--scratch", default=None)
    b.add_argument("--workers", type=int, default=8,
                   help="tile workers for the quantile phase")
    b.add_argument("--threads", type=int, default=20,
                   help="download threads for the daily-field phase")
    b.add_argument("--lat-passes", type=int, default=1,
                   help="split by latitude: N x the network, 1/N the RAM")
    b.add_argument("--scratch-dtype", default="float32",
                   choices=["float32", "float16"])
    b.add_argument("--pool", type=int, default=POOL_HALF_WINDOW_DAYS)
    b.add_argument("--wet-min", type=float, default=WET_DAY_MIN_MM)
    b.add_argument("--min-samples", type=int, default=MIN_WET_SAMPLES)
    b.add_argument("--quantiles",
                   default=",".join(str(q) for q in DEFAULT_QUANTILES))
    b.add_argument("--accums", default=",".join(str(w) for w in DEFAULT_ACCUMS))
    b.add_argument("--keep-scratch", action="store_true")
    b.add_argument("--limit-tiles", type=int, default=None,
                   help="DEBUG: compute only the first N tiles (partial store)")
    b.add_argument("--limit-days", type=int, default=None,
                   help="DEBUG: download only the first N days (partial store)")

    s = sub.add_parser("status", help="is the store built? what is in it?")
    s.add_argument("--out", default=None)
    r = sub.add_parser("regions", help="sanity numbers per region (JSON)")
    r.add_argument("--out", default=None)
    r.add_argument("--doys", default="15,200")
    m = sub.add_parser("maps", help="sanity maps (PNG)")
    m.add_argument("--out", default=None)
    m.add_argument("--dir", default="notes/flood_maps")
    m.add_argument("--doys", default="15,200")
    a = p.parse_args(argv)

    if a.cmd != "build":
        if a.cmd == "status":
            path = default_out_path() if a.out is None else pathlib.Path(a.out)
            info = climatology_meta(path)
            if (path / DONE_NAME).exists():
                ds = open_climatology(path)
                info["sizes"] = {k: int(v) for k, v in ds.sizes.items()}
                info["size_bytes"] = sum(f.stat().st_size
                                         for f in path.rglob("*") if f.is_file())
                info["free_gb_on_fs"] = round(free_gb(path), 1)
                info["mean_threshold_mm"] = {
                    f"doy{d}_d{int(w)}d_p{int(q * 100)}": round(float(np.nanmean(
                        ds[VAR].sel(accumulation=int(w), quantile=q)
                        .isel(dayofyear=d - 1).values)), 2)
                    for d in (15, 200)
                    for w in [int(x) for x in ds["accumulation"].values]
                    for q in [float(x) for x in ds["quantile"].values]}
            print(json.dumps(info, indent=1))
        elif a.cmd == "regions":
            print(json.dumps(region_stats(tuple(int(x) for x in a.doys.split(",")),
                                          a.out), indent=1))
        else:
            for f in write_maps(a.dir, tuple(int(x) for x in a.doys.split(",")),
                                a.out):
                print(f)
        return 0

    cfg = Config(years=(a.start, a.end),
                 quantiles=tuple(float(x) for x in a.quantiles.split(",")),
                 accums=tuple(int(x) for x in a.accums.split(",")),
                 pool=a.pool, wet_min=a.wet_min, min_samples=a.min_samples,
                 out=a.out, scratch=a.scratch, dtype=a.scratch_dtype,
                 limit_days=a.limit_days)
    from ewb_live._guard import require_ewb_host

    require_ewb_host()
    print(json.dumps(build(cfg, workers=a.workers, threads=a.threads,
                           nlat_pass=a.lat_passes,
                           keep_scratch=a.keep_scratch,
                           limit_tiles=a.limit_tiles), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
