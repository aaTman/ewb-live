"""PARDIG-NRT — AI-estimated global precipitation, the flood lane's daily rain.

Owner decision 2026-11-19 (briefs-new-events/fix_flood.md, UPDATE section):
swap IMERG for Google PARDIG as the flood lane's precipitation source,
KEEPING the ERA5 wet-day climatology/thresholds (the known ERA5-vs-satellite
bias is accepted, and the flood.py climatology_note states it). IMERG stays
fully implemented as the selectable fallback (``source="imerg"`` /
``EWB_FLOOD_SOURCE=imerg``).

Product (https://developers.google.com/weathernext/guides/pardig):
PARDIG-NRT, Google DeepMind/Research — GPM-CORRA-trained AI inference, global
0.1 deg, 15-min **instantaneous rate in m h-1** (cap 75 mm/h), 2016 -> present,
published in 6-hourly batches of 24 frames ~2-8 h behind real time
(versus IMERG Late's months — the flood lane finally sees near-real-time rain).
License CC BY 4.0; attribution: "Google PARDIG". It is an EXPERIMENTAL AI
estimate, not an official observation — every UI string must say so.

Store: ``gs://pardig_1`` — public; anonymous reads verified against the XML
endpoint (no credentials anywhere in this code path). Two layouts:

  NRT/zarr/2026_to_present/<YYYYMMDDTHHMMSS>/predictions.zarr
      one zarr-v3 group per 15-min frame, ``precipitation`` (1801, 3600)
      float32, single chunk (c/0/0), zstd;
  NRT/zarr/2016_to_2025/predictions.zarr
      one time-stacked store, chunks (4, 1801, 3600) — a day is 24 chunk
      reads via fancy indexing.

Lattice: NODES — latitude -90..90 ascending (1801 pts, both poles),
longitude 0..359.9 (3600 pts, 0..360 frame). This module converts to the
IMERG-style cell-centre convention the pipeline already joins with
(:func:`events.flood.aggregate_to_era5_grid`): centre = midpoint of the two
adjacent nodes, so rows become lat -89.95..89.95 (1800) and columns become
lon -179.95..179.95 (3600) in the -180 frame.

Daily totals: the rate is instantaneous, so ``daily_mm = sum(96 rates) *
0.25 h * 1000 mm/m``. A day is a day of the UTC calendar (frames
00:00..23:45 of that day). A day with ANY frame missing is recorded partial
and serves NOTHING (a 24-frame hole is a 25 % low bias dressed as a dry day —
worse than an absent day; the weekly step refills holes when the late batch
lands).

Cache discipline (brief): NO 0.1 deg disk cache (~26 MB/frame is streaming
input, like IMERG's raw/); the cache of record is one **0.25 deg ERA5-lattice
daily total** per day (~4 MB zlib): ``data/pardig/daily/pardig_025_<YYYYMMDD>.nc``
plus ``data/pardig/status.json``. Weekly updates fetch only new days.

Caveats carried into provenance: values poleward of ~67 deg are spatial
extrapolation outside GPM's training domain; the 75 mm/h rate cap clips peak
convection (18.75 mm per step); the estimate is finite EVERYWHERE, so IMERG's
"NaN = no observation" semantics do not exist here — land-only detection and
the ERA5 threshold lattice carry the honesty instead.

CLI (ewb LXC only — this is the EWB compute node):

    python -m ewb_live.sources.pardig [--root PATH] [--days 90] [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("EWB_PARDIG_URL") or "https://storage.googleapis.com/pardig_1"
NRT_FRAME_ROOT = "NRT/zarr/2026_to_present"          # <YYYYMMDDTHHMMSS>/predictions.zarr
NRT_ARCHIVE = "NRT/zarr/2016_to_2025/predictions.zarr"  # time-stacked, chunk (4,1801,3600)
ARCHIVE_T0 = dt.datetime(2016, 1, 1, 7, 45)           # first timestamp of the archive
ARCHIVE_T0_IDX = dt.datetime(2016, 1, 1, 0, 0)        # time-coord origin of the store

GRID_DEG = 0.1
N_LAT = 1801                    # nodes -90..90 ascending
N_LON = 3600                    # nodes 0..359.9
STEPS_PER_DAY = 96              # 15-min frames per UTC day
#: m/h * 1000 mm/m * 0.25 h  ->  mm of rain per frame step
MM_PER_STEP = 1000.0 * 0.25
RATE_CAP_MMH = 75.0             # producer-side cap (documented caveat)
UNITS = "mm/day"
SOURCE_KIND = "pardig"
DISPLAY = "AI-estimated precipitation (Google PARDIG, CC BY 4.0)"
LICENSE = "CC BY 4.0"
SCHEMA_VERSION = 1

#: node-latitude midpoint rows land on lat -89.95..89.95 ascending; the
#: longitude permutation lands the pair midpoints on lon -179.95..179.95.
_LON_J0 = (1800 + np.arange(N_LON)) % N_LON
_LON_J1 = (_LON_J0 + 1) % N_LON


class PardigError(RuntimeError):
    """Unreadable frame/store or an unusable window (never a 404-missing day)."""


def pardig_root(root: str | os.PathLike | None = None) -> Path:
    if root is not None:
        return Path(root)
    return Path(os.environ.get("EWB_LIVE_PARDIG_DIR") or (Path("data") / "pardig"))


def daily_dir(root: str | os.PathLike | None = None) -> Path:
    return pardig_root(root) / "daily"


def daily_path(day: dt.date, root: str | os.PathLike | None = None) -> Path:
    return daily_dir(root) / f"pardig_025_{day:%Y%m%d}.nc"


def status_path(root: str | os.PathLike | None = None) -> Path:
    return pardig_root(root) / "status.json"


# ---------------------------------------------------------------------------
# remote reads (anonymous; one store per frame for 2026+, fancy-indexed
# archive before that). zarr/obstore do the GETs and the zstd decode.
# ---------------------------------------------------------------------------

def frame_ts(day: dt.date) -> list[dt.datetime]:
    """The 96 frame timestamps of one UTC day."""
    t0 = dt.datetime(day.year, day.month, day.day)
    return [t0 + dt.timedelta(minutes=15 * k) for k in range(STEPS_PER_DAY)]


def _open_array(store_path: str, path: str = "precipitation"):
    import obstore.store as obs
    import zarr
    store = obs.HTTPStore(url=store_path)
    grp = zarr.open_group(store=zarr.storage.ObjectStore(store), mode="r")
    return grp[path]


def _is_missing(err: Exception) -> bool:
    return isinstance(err, (FileNotFoundError, KeyError)) or "404" in str(err)


def read_frame(ts: dt.datetime) -> np.ndarray | None:
    """One 15-min frame at its native node grid (m/h, float32), or None if the
    producer has not published that timestamp yet (HTTP 404 -> absent). A
    ``EWB_PARDIG_DIR`` override swaps the remote store for a local directory
    of ``<YYYYMMDDTHHMMSS>.f32`` node-grid files (test/dev fixture)."""
    local = os.environ.get("EWB_PARDIG_DIR")
    if local:
        p = Path(local) / f"{ts:%Y%m%dT%H%M%S}.f32"
        if not p.exists():
            return None
        return np.fromfile(p, np.float32).reshape(N_LAT, N_LON)
    if ts.year >= 2026:
        url = (f"{BASE_URL}/{NRT_FRAME_ROOT}/{ts:%Y%m%dT%H%M%S}/predictions.zarr")
        try:
            return np.asarray(_open_array(url)[...], "float32")
        except Exception as e:                      # noqa: BLE001 — classify below
            if _is_missing(e):
                return None
            raise PardigError(f"frame {ts:%Y-%m-%dT%H:%M} unreadable: {e}") from e
    idx = int(round((ts - ARCHIVE_T0_IDX).total_seconds() / 900))
    try:
        a = _open_array(f"{BASE_URL}/{NRT_ARCHIVE}")
        return np.asarray(a[idx], "float32")
    except Exception as e:                          # noqa: BLE001
        if _is_missing(e):
            return None
        raise PardigError(f"archive frame {ts:%Y-%m-%dT%H:%M} unreadable: {e}") from e


def read_day_frames(day: dt.date, *, workers: int = 12) -> tuple[np.ndarray, list[dt.datetime]]:
    """Sum of the day's frames at the node grid (m/h units kept), plus the
    list of timestamps the store did not serve."""
    stamps = frame_ts(day)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = list(pool.map(read_frame, stamps))
    acc: np.ndarray | None = None
    missing = [t for t, f in zip(stamps, frames, strict=True) if f is None]
    for f in frames:
        if f is None:
            continue
        acc = f.copy() if acc is None else acc + f
    if acc is None:
        raise PardigError(f"PARDIG {day}: the store served none of {STEPS_PER_DAY} frames")
    if len(missing) == 0 and (acc.shape != (N_LAT, N_LON)):
        raise PardigError(f"PARDIG {day}: frame shape {acc.shape}, expected {(N_LAT, N_LON)}")
    return acc, missing


def nodes_to_centers_mm(acc_mh: np.ndarray) -> np.ndarray:
    """Node-grid rate-sum (m/h, 1801x3600, lon 0..360) -> cell-centre daily
    total (mm/day, 1800x3600, lat -89.95..89.95 asc, lon -179.95..179.95):
    midpoint-of-neighbours in both axes, longitude folded into the -180 frame.
    """
    lat_mid = 0.5 * (acc_mh[1:, :] + acc_mh[:-1, :])          # (1800, 3600)
    centre = 0.5 * (lat_mid[:, _LON_J0] + lat_mid[:, _LON_J1])
    return (centre * MM_PER_STEP).astype("float32")


def aggregate_to_era5(centres: np.ndarray, day: dt.date) -> xr.DataArray:
    """0.1 deg centres -> 0.25 deg ERA5 lattice via the detector's own join."""
    from ..events.flood import aggregate_to_era5_grid   # lazy: flood imports pardig
    lats = -89.95 + GRID_DEG * np.arange(N_LAT - 1)
    lons = -179.95 + np.arange(N_LON) * GRID_DEG
    da = xr.DataArray(centres[None], dims=("time", "lat", "lon"),
                      coords={"time": [np.datetime64(day)],
                              "lat": lats, "lon": lons})
    return aggregate_to_era5_grid(da, src_deg=GRID_DEG, dst_deg=0.25)


# ---------------------------------------------------------------------------
# the daily 0.25 deg cache of record
# ---------------------------------------------------------------------------

def _status(root: str | os.PathLike | None) -> dict[str, Any]:
    p = status_path(root)
    if p.exists():
        return json.loads(p.read_text())
    return {"schema": SCHEMA_VERSION, "source": "Google PARDIG-NRT (gs://pardig_1)",
            "license": LICENSE, "units": UNITS, "days": {}}


def _write_status(status: dict[str, Any], root: str | os.PathLike | None) -> None:
    p = status_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=None, sort_keys=True))
    tmp.replace(p)


_lock = threading.Lock()


def build_daily(day: dt.date, root: str | os.PathLike | None = None, *,
                force: bool = False, workers: int = 12) -> dict[str, Any]:
    """Fetch one day's 96 frames, accumulate + regrid, write the 0.25 deg
    cache file and the status row. Returns the status row (``frames`` < 96
    means the day is NOT cached — a hole, refetchable later)."""
    path = daily_path(day, root)
    with _lock:
        status = _status(root)
    row = status["days"].get(f"{day:%Y-%m-%d}")
    if row and row.get("frames") == STEPS_PER_DAY and path.exists() and not force:
        return row
    acc, missing = read_day_frames(day, workers=workers)
    if missing:
        logger.warning("PARDIG %s: %d/%d frames published, %d missing (%s..) — "
                       "day NOT cached (hole, refetch when the batch lands)",
                       day, STEPS_PER_DAY - len(missing), STEPS_PER_DAY,
                       len(missing), f"{missing[0]:%H:%M}")
        with _lock:
            status = _status(root)
            # keep any previously cached day untouched; record the hole honestly
            status["days"][f"{day:%Y-%m-%d}"] = {"frames": STEPS_PER_DAY - len(missing),
                                                 "hole_first": f"{missing[0]:%Y-%m-%dT%H:%M}"}
            _write_status(status, root)
        return status["days"][f"{day:%Y-%m-%d}"]
    agg = aggregate_to_era5(nodes_to_centers_mm(acc), day)
    vals = np.asarray(agg.values[0], "float32")
    ds = xr.Dataset(
        {"precip": (("latitude", "longitude"), vals,
                    {"units": UNITS, "long_name": "daily precipitation total, "
                                              "sum of 96 PARDIG-NRT 15-min instantaneous rates"})},
        coords={"latitude": np.asarray(agg.latitude.values),
                "longitude": np.asarray(agg.longitude.values)},
        attrs={"source": "Google PARDIG-NRT (gs://pardig_1, Zarr on Cloud Storage)",
               "license": LICENSE, "attribution": DISPLAY,
               "kind": SOURCE_KIND, "run": "nrt", "units": UNITS,
               "producer_units": "m/h (instantaneous, cap 75 mm/h)",
               "grid": json.dumps({"lat": "latitude", "lon": "longitude",
                                   "nlat": int(vals.shape[0]), "nlon": int(vals.shape[1])}),
               "grid_deg": 0.25, "src_grid_deg": GRID_DEG,
               "day": f"{day:%Y-%m-%d}", "frames": STEPS_PER_DAY,
               "day_boundary": "UTC calendar day (frames 00:00..23:45)",
               "caveats": "AI estimate, not an official observation; >67 deg is "
                          "extrapolation outside GPM's training domain; rate cap "
                          "75 mm/h; finite everywhere (no unobserved-NaN semantics)",
               "schema_version": SCHEMA_VERSION})
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".nc") as tf:
        tmp = Path(tf.name)
    try:
        ds.to_netcdf(tmp, encoding={"precip": {"zlib": True, "complevel": 5,
                                               "dtype": "float32"}})
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    row = {"frames": STEPS_PER_DAY, "mean_mm": round(float(np.mean(vals)), 4),
           "max_mm": round(float(np.max(vals)), 2)}
    with _lock:
        status = _status(root)
        status["days"][f"{day:%Y-%m-%d}"] = row
        _write_status(status, root)
    return row


def load_pardig_daily(start: dt.date, end: dt.date, *,
                      root: str | os.PathLike | None = None,
                      require_all: bool = True) -> xr.DataArray:
    """Cached PARDIG daily totals (time, latitude, longitude) mm/day on the
    ERA5 0.25 deg lattice — the same shape/units as
    ``imerg.load_imerg_daily`` post-join. Absent days are NaN planes (never
    zeros); with ``require_all`` and NOTHING served, raise (mirrors the IMERG
    loader's empty-chunk behaviour so callers treat a bad window loudly)."""
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    frames, present, lats, lons = [], [], None, None
    shape = None
    for d in days:
        p = daily_path(d, root)
        if not p.exists():
            frames.append(None)
            continue
        with xr.open_dataset(p) as ds:
            vals = np.asarray(ds["precip"].values, "float32")
            if lats is None:
                lats = np.asarray(ds.latitude.values, "float64")
                lons = np.asarray(ds.longitude.values, "float64")
                shape = vals.shape
        frames.append(vals)
        present.append(d)
    if not present:
        raise PardigError(f"no PARDIG daily fields in {start}..{end} under "
                          f"{daily_dir(root)}")
    assert lats is not None and lons is not None and shape is not None
    if len(present) < len(days) and not require_all:
        raise PardigError(f"PARDIG window {start}..{end}: {len(days) - len(present)} "
                          f"of {len(days)} days are not cached")
    # absent days become all-NaN planes: never zero (an absent day is not a
    # dry day — the same contract the IMERG loader keeps)
    nan = np.full(shape, np.nan, "float32")
    stack, time = [], []
    for d, f in zip(days, frames, strict=True):
        stack.append(nan if f is None else f)
        time.append(d)
    values = np.stack(stack).astype("float32")
    return xr.DataArray(
        values, dims=("time", "latitude", "longitude"),
        coords={"time": [np.datetime64(d) for d in time],
                "latitude": lats, "longitude": lons},
        name="precipitation",
        attrs={"units": UNITS, "kind": SOURCE_KIND, "run": "nrt",
               "source": "Google PARDIG-NRT (gs://pardig_1)", "license": LICENSE,
               "display": DISPLAY, "grid_deg": 0.25, "src_grid_deg": GRID_DEG,
               "day_boundary": "UTC calendar day",
               "days_total": len(days), "days_present": len(present),
               "missing_days": [f"{d:%Y-%m-%d}" for d in days if d not in present],
               "schema_version": SCHEMA_VERSION})


def load_pardig_status(start: dt.date, end: dt.date, *,
                       root: str | os.PathLike | None = None):
    """Per-day cache/serving rows for the window (pandas, like the IMERG one):
    index=day, columns frames/mean_mm/max_mm/cached."""
    import pandas as pd
    status = _status(root)
    rows = []
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    for d in days:
        row = status["days"].get(f"{d:%Y-%m-%d}") or {}
        rows.append({"frames": row.get("frames", 0), "mean_mm": row.get("mean_mm"),
                     "max_mm": row.get("max_mm"),
                     "cached": daily_path(d, root).exists()})
    return pd.DataFrame(rows, index=pd.Index([f"{d:%Y-%m-%d}" for d in days]))


def served_days(root: str | os.PathLike | None = None) -> set[dt.date]:
    """Days the cache holds COMPLETELY — the store's ground-truth inventory
    (status.json may record holes; the file is the truth, as with IMERG)."""
    out = set()
    for p in sorted(daily_dir(root).glob("pardig_025_*.nc")):
        try:
            out.add(dt.date.fromisoformat(p.stem[-8:][:4] + "-" + p.stem[-6:-4] + "-"
                                          + p.stem[-4:]))
        except ValueError:
            continue
    return out


def prune(root: str | os.PathLike | None = None, *, keep_days: int = 100) -> int:
    """Drop cache files older than ``keep_days`` before the newest served day
    (the lane window is 90 d + a lookback; the cache must not grow forever)."""
    have = sorted(served_days(root))
    if len(have) <= keep_days:
        return 0
    cutoff = have[-1] - dt.timedelta(days=keep_days - 1)
    n = 0
    status = _status(root)
    for d in have:
        if d < cutoff:
            daily_path(d, root).unlink(missing_ok=True)
            status["days"].pop(f"{d:%Y-%m-%d}", None)
            n += 1
    _write_status(status, root)
    return n


# ---------------------------------------------------------------------------
# CLI (the weekly / lanes build step)
# ---------------------------------------------------------------------------

def build_window(start: dt.date, end: dt.date, root: str | os.PathLike | None = None,
                 *, force: bool = False, workers: int = 12) -> dict[str, int]:
    """Ensure every day of [start, end] is cached (or recorded as a hole),
    newest day first so the window tail — where floods live — lands early."""
    have = served_days(root)
    todo = [d for d in sorted({*[(start + dt.timedelta(days=i))
                                 for i in range((end - start).days + 1)]}, reverse=True)
            if force or d not in have]
    built = holes = failed = 0
    for d in todo:
        try:
            row = build_daily(d, root, force=force, workers=workers)
            if row.get("frames") == STEPS_PER_DAY:
                built += 1
            else:
                holes += 1
        except PardigError as e:
            failed += 1
            logger.warning("PARDIG build %s failed (non-fatal): %s", d, e)
    logger.info("PARDIG build %s..%s: %d built, %d holes, %d failed (%d already cached)",
                start, end, built, holes, failed, len(served_days(root)) - built - holes)
    return {"built": built, "holes": holes, "failed": failed}


def main(argv: Iterable[str] | None = None) -> int:
    from .._guard import require_ewb_host
    require_ewb_host()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="cache root (default data/pardig)")
    ap.add_argument("--days", type=int, default=90, help="window length ending today")
    ap.add_argument("--end", default=None, help="window end date (YYYY-MM-DD), default today")
    ap.add_argument("--force", action="store_true", help="refetch cached days too")
    ap.add_argument("--workers", type=int, default=12, help="parallel frame GETs")
    args = ap.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    end = (dt.date.fromisoformat(args.end) if args.end else dt.date.today())
    start = end - dt.timedelta(days=args.days - 1)
    stats = build_window(start, end, args.root, force=args.force, workers=args.workers)
    n = prune(args.root, keep_days=args.days + 14)
    if n:
        logger.info("PARDIG prune: %d stale cache days dropped", n)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
