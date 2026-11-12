"""Local ERA5 truth mirror for EWB scoring targets.

EWB's ERA5 targets stream the *global hourly* ARCO zarr from GCS and
derive AR variables (IVT, Laplacian) on the global grid: scoring was
I/O-bound at ~5 % CPU. This module mirrors locally, incrementally,
exactly what EWB's default targets and derivations read:

- ``2m_temperature`` (heat wave / freeze targets)
- ``q``, ``u_component_of_wind``, ``v_component_of_wind`` at pressure
  levels >= 300 hPa — everything ``AtmosphericRiverVariables`` reads
  (``sel(level >= 300)`` then the IVT integral)

on the ARCO grid with *identical* coordinate values (latitude /
longitude / level arrays copied from the source, so EWB's subsetting
and alignment logic is untouched), subsampled to 6-hourly timesteps —
the forecast cadence, so scoring loses no valid times.

Store layout: zarr v2 at ``data/cache/truth/era5.zarr``, origin-anchored
6-hourly time axis (index = hours_since_origin / 6), one (time) chunk
per point so appends never rewrite old chunks. A manifest JSON tracks
which (variable, time) points completed; interrupted runs resume
point-by-point. ``mirror_covers`` is manifest-driven: a window is served
from the mirror only when every needed point for every variable is done.

``truth.py`` points EWB's own ``inputs.ERA5`` class at the store with
the same variable/coordinate names, so scoring semantics are unchanged;
the IFS-HRES-analysis fallback still covers days past the ARCO cutoff.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import threading
import time as _time
from pathlib import Path

import numcodecs
import numpy as np
import xarray as xr
import zarr

logger = logging.getLogger(__name__)

MIRROR_VARS = ["2m_temperature", "specific_humidity", "u_component_of_wind",
               "v_component_of_wind"]
IVT_MIN_LEVEL = 300  # hPa; == AtmosphericRiverVariables.top_pressure_level
BLOSC = numcodecs.blosc.Blosc(cname="zstd", clevel=5, shuffle=numcodecs.blosc.BITSHUFFLE)


def mirror_path() -> Path:
    """Path of the ERA5 mirror zarr store."""
    root = Path(os.environ.get("EWB_LIVE_CACHE", "data/cache"))
    return root / "truth" / "era5.zarr"


def _manifest_path() -> Path:
    return mirror_path().with_name("era5_mirror_manifest.json")


def _key(t: dt.datetime | np.datetime64) -> str:
    return str(np.datetime64(t, "h"))


def load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as exc:  # noqa: BLE001
            logger.warning("truth mirror manifest unreadable (%s); starting fresh", exc)
    return {"complete": {}}


def save_manifest(man: dict) -> None:
    tmp = _manifest_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(man))
    tmp.replace(_manifest_path())


def grid_times(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """6-hourly instants covering [start, end] endpoints inclusive (EWB
    slices the target time range inclusively, so the exact-midnight ``end``
    instant of an event is part of the target slice)."""
    t0 = start.replace(minute=0, second=0, microsecond=0)
    t0 = t0 - dt.timedelta(hours=t0.hour % 6)
    out: list[dt.datetime] = []
    t = t0
    while t <= end:
        out.append(t)
        t += dt.timedelta(hours=6)
    return out


def _time_complete(man: dict, t: dt.datetime) -> bool:
    return set(man.get("complete", {}).get(_key(t), [])) >= set(MIRROR_VARS)


def mirror_covers(start: dt.datetime, end: dt.datetime) -> bool:
    """True iff every 6-hourly point of [start, end] is fully mirrored
    (all four variables) — the gate for scoring a window off the mirror."""
    if not mirror_path().exists():
        return False
    man = load_manifest()
    return all(_time_complete(man, t) for t in grid_times(start, end))


def ensure_mirror(
    *,
    end: dt.datetime,
    days: int = 90,
    lookback_days: int = 14,
    cutoff: dt.datetime | None = None,
    threads: int = 24,
) -> dict:
    """Mirror ARCO so every needed 6-hourly point of
    [end - (days+lookback_days), min(end, cutoff)] is available locally.

    Incremental (manifest-driven), parallel over (variable, time) points,
    resumable. Times older than the store's origin are reported as
    ``skipped_before_origin`` (window only grows forward; a rebuild would
    be needed to extend backwards).
    """
    t0 = _time.monotonic()
    cutoff = cutoff or _truth_cutoff()
    window_start = end - dt.timedelta(days=days + lookback_days)
    window_end = min(end, cutoff)
    wanted = grid_times(window_start, window_end)
    if not wanted:
        return {"requested": 0, "existing": 0, "copied": 0, "errors": 0,
                "skipped_before_origin": 0, "seconds": 0.0}

    g, origin = _open_or_create_store(wanted[0])
    man = load_manifest()
    complete = man.get("complete", {})

    def _idx(t: dt.datetime) -> int:
        return int((np.datetime64(t) - np.datetime64(origin)) / np.timedelta64(6, "h"))

    before = [t for t in wanted if _idx(t) < 0]
    usable = [t for t in wanted if _idx(t) >= 0]
    todo = [(v, t, _idx(t)) for t in usable for v in MIRROR_VARS
            if v not in set(complete.get(_key(t), []))]
    n_existing = sum(1 for t in usable if _time_complete({"complete": complete}, t))
    if not todo:
        return {"requested": len(usable), "existing": n_existing, "copied": 0,
                "errors": 0, "skipped_before_origin": len(before),
                "seconds": 0.0}

    max_idx = max(i for _, _, i in todo)
    if g["time"].shape[0] <= max_idx:
        g["time"].resize((max_idx + 1,))
        new_tv = np.array(
            [np.datetime64(origin) + np.timedelta64(6 * k, "h")
             for k in range(g["time"].shape[0])], dtype="datetime64[ns]")
        g["time"][:] = new_tv
    for v in MIRROR_VARS:
        a = g[v]
        if a.shape[0] <= max_idx:
            a.resize((max_idx + 1, *a.shape[1:]))

    logger.info(
        "truth mirror: %d/%d times complete, copying %d variable-points "
        "(%d threads)", n_existing, len(usable), len(todo), threads)

    lock = threading.Lock()
    counters = {"copied": 0, "errors": 0}

    from concurrent.futures import ThreadPoolExecutor

    def _task(args):
        var, t, idx = args
        try:
            _copy_point(var, t, idx)
        except Exception as exc:  # noqa: BLE001 - one flaky GCS object must not kill the run
            logger.warning("mirror copy failed %s %s: %s", var, t, exc)
            with lock:
                counters["errors"] += 1
            return
        with lock:
            lst = complete.setdefault(_key(t), [])
            if var not in lst:
                lst.append(var)
            counters["copied"] += 1
            if counters["copied"] % 20 == 0:
                # freshness heartbeat during long first-syncs: the wedge
                # alarm must not restart a fresh box mid mirror copy
                # (ponytail 21:34Z); lazy import, silent on failure
                from ..pipeline import _heartbeat
                _heartbeat(phase="mirror", done=counters["copied"],
                           total=len(todo))
            if counters["copied"] % 200 == 0:
                save_manifest({"complete": complete})
                logger.info("truth mirror: %d/%d points copied",
                            counters["copied"], len(todo))

    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(_task, todo))
    save_manifest({"complete": complete})
    secs = _time.monotonic() - t0
    summary = {"requested": len(usable), "existing": n_existing,
               "copied": counters["copied"], "errors": counters["errors"],
               "skipped_before_origin": len(before), "seconds": round(secs, 1)}
    logger.info("truth mirror: %s", summary)
    return summary


def _truth_cutoff() -> dt.datetime:
    from ..events.truth import truth_cutoff

    return truth_cutoff()


def _source_dataset() -> xr.Dataset:
    from extremeweatherbench.inputs import ARCO_ERA5_FULL_URI

    return xr.open_zarr(ARCO_ERA5_FULL_URI, storage_options={"token": "anon"},
                        chunks=None)


# per-thread source handles (gcsfs readers are not thread-shared)
_tls = threading.local()


def _thread_source() -> xr.Dataset:
    if not hasattr(_tls, "src"):
        _tls.src = _source_dataset()
    return _tls.src


def _open_or_create_store(first: dt.datetime) -> tuple[zarr.Group, dt.datetime]:
    """Open (or create) the mirror store; returns (group, origin)."""
    mp = mirror_path()
    if mp.exists():
        g = zarr.open_group(str(mp), mode="r+", zarr_format=2)
        if "origin" in g.attrs:
            return g, dt.datetime.fromisoformat(str(g.attrs["origin"]))
        # store without origin metadata: stamp it from the time array if it
        # has one; a store that is neither stamped nor populated is a
        # half-created remnant of an interrupted run -> rebuild from scratch
        t0 = g["time"] if "time" in g.members() else None
        if t0 is not None and t0.shape[0] > 0:
            origin = dt.datetime.fromisoformat(str(np.datetime64(t0[0], "h")))
            g.attrs.put({"origin": _key(origin)})
            return g, origin
        logger.warning("truth mirror: incomplete store at %s; rebuilding", mp)
        shutil.rmtree(mp)
    mp.parent.mkdir(parents=True, exist_ok=True)
    src = _source_dataset()
    lat = np.asarray(src.latitude.values, dtype="float32")
    lon = np.asarray(src.longitude.values, dtype="float32")
    lv_all = np.asarray(src.level.values, dtype="int64")
    lv = lv_all[lv_all >= IVT_MIN_LEVEL]
    var_units = {v: src[v].attrs.get("units") for v in MIRROR_VARS}
    src.close()

    g = zarr.open_group(str(mp), mode="w", zarr_format=2)
    t = g.create_array("time", shape=(0,), chunks=(24,), dtype="datetime64[ns]")
    t.attrs.put({"_ARRAY_DIMENSIONS": ["time"]})
    for name, arr in (("latitude", lat), ("longitude", lon), ("level", lv)):
        # fill_value=None is REQUIRED: the zarr default fill (0.0) makes
        # xarray mask the *real* 0.0 coordinate values (lon 0E col, lat 0
        # row) as NaN on every decoded read (science-review B2).
        a = g.create_array(name, shape=arr.shape, chunks=arr.shape,
                           dtype=arr.dtype, fill_value=None)
        a[...] = arr
        a.attrs.put({"_ARRAY_DIMENSIONS": [name]})
    for v in MIRROR_VARS:
        is2d = v == "2m_temperature"
        dims = (["time", "latitude", "longitude"] if is2d
                else ["time", "level", "latitude", "longitude"])
        shape = (0, lat.size, lon.size) if is2d else (0, lv.size, lat.size, lon.size)
        chunks = (1, lat.size, lon.size) if is2d else (1, lv.size, lat.size, lon.size)
        a = g.create_array(v, shape=shape, chunks=chunks, dtype="float32",
                           compressor=BLOSC)
        # single attrs.put: zarr-python replaces the whole attrs dict per
        # put on a stale handle, so a second put would drop _ARRAY_DIMENSIONS
        attrs = {"_ARRAY_DIMENSIONS": dims}
        if var_units.get(v):
            attrs["units"] = var_units[v]
        a.attrs.put(attrs)
    g.attrs.put({"coords": "time latitude longitude level " + " ".join(MIRROR_VARS),
                 "origin": _key(first)})
    logger.info("truth mirror: created store %s (levels>=%d hPa: %s)", mp,
                IVT_MIN_LEVEL, lv.tolist())
    return g, first


def _copy_point(var: str, t: dt.datetime, idx: int) -> None:
    src = _thread_source()
    da = src[var].sel(time=np.datetime64(t))
    if var != "2m_temperature":
        da = da.sel(level=da.level.values[da.level.values >= IVT_MIN_LEVEL])
    vals = np.asarray(da.values, dtype="float32")
    if not hasattr(_tls, "grp"):
        _tls.grp = zarr.open_group(str(mirror_path()), mode="r+")
    _tls.grp[var][idx] = vals


def open_mirror() -> xr.Dataset:
    ensure_coord_integrity()
    return xr.open_zarr(str(mirror_path()), consolidated=False)


# ---------------------------------------------------------------------------
# Coordinate integrity (science-review B2): the store's coordinate arrays
# were once created with zarr's default numeric fill_value (0.0), so xarray's
# decode masked the REAL 0.0 values — longitude 0E (index 0) and latitude 0
# (equator row) — as NaN for every consumer. These helpers detect and repair
# that at the store level (no data re-copy) and guard every future read.
# ---------------------------------------------------------------------------

_COORD_STEP_DEG = 0.25


def _fill_is_null(meta: dict) -> bool:
    return meta.get("_FillValue", meta.get("fill_value", None)) is None


def _coord_integrity_status(mp: Path) -> tuple[bool, list[str]]:
    """(ok, problems) from the RAW zarr view (not xarray-decoded)."""
    g = zarr.open_group(str(mp), mode="r")
    problems: list[str] = []
    for name, direction in (("latitude", -1), ("longitude", 1)):
        fp = mp / name / ".zarray"
        if fp.exists():
            try:
                meta = json.loads(fp.read_text())
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: unreadable .zarray ({exc})")
            else:
                if not _fill_is_null(meta):
                    problems.append(f"{name}: coordinate _FillValue is not null")
        c = np.asarray(g[name], dtype="float64")
        if not np.isfinite(c).all():
            problems.append(f"{name}: {int((~np.isfinite(c)).sum())} non-finite values")
            continue
        d = np.diff(c)
        mono = np.all(d > 0) if direction > 0 else np.all(d < 0)
        if not mono:
            problems.append(f"{name}: not strictly monotonic")
        if not np.allclose(np.abs(d), _COORD_STEP_DEG, atol=1e-6):
            problems.append(f"{name}: spacing != {_COORD_STEP_DEG} deg")
    lv = np.asarray(g["level"], dtype="int64")
    dlv = np.diff(lv)
    if not np.all(lv >= IVT_MIN_LEVEL) or not (np.all(dlv > 0) or np.all(dlv < 0)):
        problems.append("level: not strictly monotonic above IVT_MIN_LEVEL")
    return not problems, problems


def _repair_coord_fill(mp: Path) -> None:
    """Null the coordinate fill metadata (atomic rewrite; v2 layout)."""
    for name in ("latitude", "longitude", "level"):
        fp = mp / name / ".zarray"
        if not fp.exists():
            continue
        meta = json.loads(fp.read_text())
        key = "_FillValue" if "_FillValue" in meta else "fill_value"
        meta[key] = None
        tmp = fp.with_name(fp.name + ".tmp")
        tmp.write_text(json.dumps(meta))
        tmp.replace(fp)


def ensure_coord_integrity(mp: Path | None = None) -> bool:
    """Validate the mirror's coords against the ARCO grid contract; repair
    fill-metadata (and regenerate coord values if corrupted) in place.

    Returns True if a repair was performed; raises RuntimeError if the store
    still fails validation afterwards or levels cannot be repaired offline.
    """
    mp = mirror_path() if mp is None else Path(mp)
    if not (mp / "latitude").exists():
        return False
    ok, problems = _coord_integrity_status(mp)
    if ok:
        return False
    logger.warning("truth mirror: coordinate integrity failure %s; repairing",
                   problems)
    _repair_coord_fill(mp)
    for name, gen in (("latitude", lambda n: 90.0 - _COORD_STEP_DEG * np.arange(n)),
                      ("longitude", lambda n: _COORD_STEP_DEG * np.arange(n))):
        zraw = zarr.open_array(store=str(mp / name), mode="r+")
        c = np.asarray(zraw, dtype="float64")
        if not np.isfinite(c).all():
            zraw[...] = gen(len(c)).astype(c.dtype)
    ok, problems = _coord_integrity_status(mp)
    if not ok:
        raise RuntimeError(f"truth mirror coords still invalid after repair: {problems}")
    logger.info("truth mirror: coordinates repaired in place")
    return True


def verify_against_arco(n_samples: int = 6, seed: int = 0) -> dict:
    """Random-point equality check of the mirror against ARCO (exact)."""
    rng = np.random.default_rng(seed)
    m = open_mirror()
    src = _source_dataset()
    times = [str(t) for t in m.time.values]
    bad = []
    for _ in range(n_samples):
        v = MIRROR_VARS[int(rng.integers(len(MIRROR_VARS)))]
        t = times[int(rng.integers(len(times)))]
        mv = m[v].sel(time=np.datetime64(t))
        sv = src[v].sel(time=np.datetime64(t))
        if v != "2m_temperature":
            sv = sv.sel(level=mv.level.values)
        mv, sv = xr.align(mv, sv, join="inner")
        diff = float(np.nanmax(np.abs(mv.values - sv.values)))
        if diff > 0:
            bad.append({"var": v, "time": t, "max_abs_diff": diff})
    src.close()
    return {"checked": n_samples, "mismatches": bad}


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host  # CLI-only guard (see _guard.py)

    require_ewb_host()
    import argparse

    p = argparse.ArgumentParser(description="ERA5 truth mirror for EWB scoring")
    p.add_argument("--end", default=None, help="window end YYYY-MM-DD (default today)")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--lookback", type=int, default=14)
    p.add_argument("--threads", type=int, default=24)
    p.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    end = (dt.datetime.strptime(a.end, "%Y-%m-%d") if a.end
           else dt.datetime.now(dt.timezone.utc).replace(
               hour=0, minute=0, second=0, microsecond=0, tzinfo=None))
    summ = ensure_mirror(end=end, days=a.days, lookback_days=a.lookback,
                         threads=a.threads)
    print(json.dumps(summ))
    if a.verify:
        print(json.dumps(verify_against_arco()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
