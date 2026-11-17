"""AIWP backend: CIRA / NOAA-GSL MLWP archive, read via the Icechunk virtual store.

**Virtual-store only (owner ruling 2026-11-17, drop-subsets).** Every MLWP
forecast read goes through :func:`open_mlwp_forecast` — the single choke
point for the archive — which opens the local Icechunk repo built by
``ewb_live.stores.mlwp_icechunk`` (virtual chunk refs into the archive's
NetCDF4/HDF5 objects; bit-identical to direct reads per
``scripts/mlwp_verify_store.py``, ~30x faster for subsets, and it handles
the archive's per-era chunking/variable changes). Nothing is downloaded or
materialised to disk: bbox+lead+level cuts are applied lazily and only the
touched HDF5 chunks are range-fetched at ``compute()`` time.

Consequently:
* there is **no direct-bucket read path** in this module (the former HDF5
  range-read fallback is removed); a store miss or an absent store raises
  ``FileNotFoundError`` so ``open_forecast_nearest_init`` falls back to an
  earlier published init and callers degrade the same way as for a gap;
* init *listing* takes the store as authoritative and only tails the
  store's coverage using the store builder's listing layer
  (``ewb_live.stores.mlwp_common``) — the one sanctioned direct-bucket
  consumer (LIST only, never data reads);
* keep the store fresh: ``ewb-live-store.timer`` (daily 05:30 UTC) plus the
  weekly catch-up; inits published after the last store update are not
  readable until the next one.

Concurrency guard (notes/ewb.md, scoring-run incident 2026-11): icechunk
sessions hang when >= 3 download concurrently in one host, so
:class:`threading.BoundedSemaphore` bounds concurrent in-process store
reads; override with ``EWB_LIVE_ICECHUNK_MAX_SESSIONS``.

Archive layout (for reference only — accessed through the store):
``<CODE>_v<V>_<INIT>/YYYY/mmdd/<CODE>_v<V>_<INIT>_YYYYMMDDHH_f000_f240_06.nc``,
one file per init (00Z/12Z), leads 0..240 step 6, 0.25 deg, 13 levels.
"""

from __future__ import annotations

import datetime as dt
import os
import threading

import numpy as np
import xarray as xr

from . import _transform
from .registry import CIRA_NAME_TO_EWB, ModelSpec

AIWP_LEADS = list(range(0, 241, 6))
#: values above this are unmasked CF default fill (9.96921e36) in the source
#: files — e.g. GraphCast lead 0, which the 2-step model does not produce
_FILL_LIMIT = 9e35

#: max concurrent icechunk read sessions per process (notes/ewb.md: >=3
#: concurrent S3-downloading sessions hang; 2 keeps scoring pools safe)
ICECHUNK_MAX_SESSIONS = max(1, int(os.environ.get("EWB_LIVE_ICECHUNK_MAX_SESSIONS", "2")))
_ICECHUNK_SESSIONS = threading.BoundedSemaphore(ICECHUNK_MAX_SESSIONS)


def _code(spec: ModelSpec) -> str:
    # description embeds the base code via registry keys; recover from name
    base = spec.name.removesuffix("_gfs")
    from .registry import AIWP_MODELS

    return AIWP_MODELS[base] + ("_GFS" if spec.init_source == "GFS" else "_IFS")


def _store():
    """Lazy import of the mlwp icechunk store module (None if repo absent)."""
    try:
        from ..stores import mlwp_icechunk as mod

        if not mod.repo_exists(mod.DEFAULT_REPO):
            return None
        return mod
    except Exception:  # noqa: BLE001 - store absent/broken: reads must fail loud
        return None


def _native_map(spec: ModelSpec, variables: list[str]) -> dict[str, str]:
    inv = {ewb: nat for nat, ewb in CIRA_NAME_TO_EWB.items()}
    native: dict[str, str] = {}
    for v in variables:
        if v not in inv:
            raise ValueError(f"variable {v!r} not available for model {spec.name!r}")
        native[inv[v]] = v
    return native


def _select_leads(lead_hours: list[int], spec: ModelSpec) -> list[int]:
    leads = [h for h in lead_hours if h in AIWP_LEADS]
    if not leads:
        raise ValueError(
            f"no compatible lead hours for {spec.name}: available {AIWP_LEADS[:3]}... "
            "step 6 up to 240"
        )
    return leads


def _grid_cut(ds: xr.Dataset, bbox) -> xr.Dataset:
    """Cut lat/lon lazily BEFORE compute (index masks handle descending
    latitudes and native 0..360 longitudes)."""
    if bbox is None:
        return ds
    lat_min, lat_max, lon_min, lon_max = bbox
    li = _transform.lat_indices(
        ds.latitude.values, min(lat_min, lat_max), max(lat_min, lat_max)
    )
    oi = _transform.lon_indices(ds.longitude.values, lon_min, lon_max)
    return ds.isel(latitude=li, longitude=oi)


def _finish(
    per_var: dict[str, xr.DataArray], init: dt.datetime, leads: list[int], bbox
) -> xr.Dataset:
    out: dict[str, xr.DataArray] = {}
    for ewb_name, da in per_var.items():
        da = da.where(np.abs(da) < _FILL_LIMIT)
        da.name = ewb_name
        out[ewb_name] = da
    return _transform.assemble(out, np.datetime64(init.replace(tzinfo=None), "ns"), leads, bbox)


# --------------------------------------------------------------- choke point
def open_mlwp_forecast(
    spec: ModelSpec,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox,
) -> xr.Dataset:
    """THE entry point for CIRA MLWP forecast data (virtual store only).

    Everything that evaluates MLWP forecasts must arrive here (normally via
    ``sources.open_forecast`` / ``scoring.fc_cache`` / ``viz``); the
    no-direct-access rule is enforced by tests/test_mlwp_store_guard.py.
    Lazy store view -> init/lead/level/bbox cut in memory -> one bounded
    session per call -> in-memory result. Raises FileNotFoundError when the
    store or the init is unavailable.
    """
    store = _store()
    if store is None:
        raise FileNotFoundError(
            "CIRA MLWP Icechunk store unavailable (repo missing or unreadable); "
            "MLWP reads require the virtual store — build/update it with "
            "'python -m ewb_live.stores.mlwp_icechunk build|update'"
        )
    native = _native_map(spec, variables)
    leads = _select_leads(lead_hours, spec)
    code = _code(spec)
    with _ICECHUNK_SESSIONS:
        try:
            # open_init() decodes only the one era group holding this init (measured
            # 0.8s open); open_model() builds the full model view (~8 GB / ~115 s for
            # the largest model) — never do that on a per-request path.
            ds = store.open_init(code, init)
            init64 = np.datetime64(init.replace(tzinfo=None), "ns")
            ds = ds[list(native)].sel(init_time=init64)  # scalar sel drops the dim
            ds = ds.sel(lead_time=np.array([np.timedelta64(h, "h") for h in leads]))
            lv = [int(l) for l in (spec.levels or [])]
            if lv:
                ds = ds.sel(level=lv) if "level" in ds.dims else ds
            ds = _grid_cut(ds, bbox)
            per_var: dict[str, xr.DataArray] = {}
            for nat, ewb_name in native.items():
                da = ds[nat].rename({"lead_time": "lead"}).transpose("lead", ...)
                per_var[ewb_name] = da.compute()
        except KeyError as exc:
            # open_init raises KeyError for absent model/init: same contract
            # as a missing object on the old direct path (nearest-init works)
            raise FileNotFoundError(
                f"{code} init {init:%Y-%m-%d %H:%M} not in MLWP store: {exc}"
            ) from exc
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - store failure is terminal now
            raise RuntimeError(
                f"MLWP virtual-store read failed for {code} "
                f"{init:%Y%m%d%H}Z: {type(exc).__name__}: {exc}"
            ) from exc
    return _finish(per_var, init, leads, bbox)


def open_forecast(
    spec: ModelSpec,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox,
) -> xr.Dataset:
    """Backend entry point — routes straight to the virtual-store choke point."""
    return open_mlwp_forecast(spec, init, variables, lead_hours, bbox)


# ------------------------------------------------------------------- listing
def _known_bad_files(store, code: str) -> set[str]:
    """Bare filenames of upstream objects the store knows are unreadable
    (truncated/corrupt). Empty set if the store lacks the API."""
    fn = getattr(store, "known_bad", None)
    if fn is None:
        return set()
    try:
        return {e["url"].rsplit("/", 1)[-1] for e in fn(model=code)}
    except Exception:  # noqa: BLE001
        return set()


def available_inits(spec: ModelSpec, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Inits available for a model in [start, end] (full history supported).

    The Icechunk store covers the FULL CIRA archive (virtual refs, daily
    update), so its listing is the answer for everything it covers — no
    per-day bucket LIST for 2020-era queries. The tail newer than the
    store's last init is listed through the store builder's own listing
    layer (``stores.mlwp_common``; LIST only). When the store is absent the
    whole window is listed there, so *listing* still works; only reads
    require the store.
    """
    from ..stores import mlwp_common as mc

    code = _code(spec)
    store = _store()
    bad = _known_bad_files(store, code) if store is not None else set()
    out: set[dt.datetime] = set()
    covered_through: dt.datetime | None = None
    if store is not None:
        try:
            store_inits = store.list_inits(code)
            out.update(t for t in store_inits if start <= t <= end)
            covered_through = max(store_inits) if store_inits else None
        except Exception:  # noqa: BLE001 - store unreadable: tail-list only
            covered_through = None
    # tail-list from the first day the store might not know about; re-listing
    # the store's last covered day is 1 cheap LIST and closes any gap.
    if covered_through is None or covered_through < start:
        day = dt.datetime(start.year, start.month, start.day)
    else:
        day = dt.datetime(covered_through.year, covered_through.month, covered_through.day)
    while day <= end:
        for fn in mc.day_files(code, day.date()):
            if fn in bad:
                continue
            try:
                _, init, _ = mc.parse_filename(fn)
            except ValueError:
                continue
            if start <= init <= end:
                out.add(init)
        day += dt.timedelta(days=1)
    return sorted(out)
