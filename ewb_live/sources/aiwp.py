"""AIWP store backend: s3://noaa-oar-mlwp-data (CIRA / NOAA-GSL 'MLWP' archive).

Layout:  <CODE>_v<V>_<INIT>/YYYY/mmdd/<CODE>_v<V>_<INIT>_YYYYMMDDHH_f000_f240_06.nc
One file per init (00Z and 12Z), lead hours 0..240 step 6, `time` coord holds
valid times. 0.25 deg lat/lon (721x1440), lon 0..359.75, 13 pressure levels.

Two read paths:
* **primary** — the local Icechunk virtual store built by
  ``ewb_live.stores.mlwp_icechunk`` (virtual chunk refs into the same NetCDF4
  objects; per mlwp-store agent: bit-identical to direct reads, ~30x faster
  for subsets, handles archive chunking/variable changes across eras).
* **fallback** — direct remote HDF5 range reads (h5netcdf via fsspec). Used
  automatically when the store module/repo is absent, a model or init is not
  in the store, or the store raises. Keeps us independent of the store's
  daily (05:30 UTC) update cadence for the freshest inits.
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import xarray as xr

from . import _net
from . import _transform
from .registry import CIRA_NAME_TO_EWB, ModelSpec

AIWP_RE = re.compile(r"_v\d+_(IFS|GFS)_(\d{10})_f\d{3}_f\d{3}_\d{2}\.nc$")
AIWP_LEADS = list(range(0, 241, 6))
#: values above this are unmasked CF default fill (9.96921e36) in the source
#: files — e.g. GraphCast lead 0, which the 2-step model does not produce
_FILL_LIMIT = 9e35


class _StoreMiss(Exception):
    """Requested data not servable from the icechunk store; use direct path."""


def _code(spec: ModelSpec) -> str:
    # description embeds the base code via registry keys; recover from name
    base = spec.name.removesuffix("_gfs")
    from .registry import AIWP_MODELS

    return AIWP_MODELS[base] + ("_GFS" if spec.init_source == "GFS" else "_IFS")


def _store():
    """Lazy import of the mlwp icechunk store module (may not exist)."""
    try:
        from ..stores import mlwp_icechunk as mod

        if not mod.repo_exists(mod.DEFAULT_REPO):
            return None
        return mod
    except Exception:  # noqa: BLE001 - store absent/broken: direct path only
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


# --------------------------------------------------------------------- store path
def _open_via_store(
    spec: ModelSpec, init: dt.datetime, native: dict[str, str], leads: list[int], bbox
) -> xr.Dataset:
    store = _store()
    if store is None:
        raise _StoreMiss("store unavailable")
    code = _code(spec)
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
        return _finish(per_var, init, leads, bbox)
    except _StoreMiss:
        raise
    except Exception as exc:  # noqa: BLE001 - any store hiccup falls back
        raise _StoreMiss(f"{type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------- direct path
def _open_direct(
    spec: ModelSpec, init: dt.datetime, native: dict[str, str], leads: list[int], bbox
) -> xr.Dataset:
    code = _code(spec)
    path = (
        f"{_net.AIWP_BUCKET}/{code}/{init:%Y}/{init:%m%d}/"
        f"{code}_{init:%Y%m%d%H}_f000_f240_06.nc"
    )
    f = _net.open_remote_nc(f"s3://{path}")
    try:
        raw = xr.open_dataset(f, engine="h5netcdf", decode_times=True)
        times = np.array(
            [
                np.datetime64(init.replace(tzinfo=None), "h") + np.timedelta64(h, "h")
                for h in leads
            ]
        )
        ds = raw[list(native)].sel(time=times)
        lv = [int(l) for l in (spec.levels or [])]
        if "level" in ds.dims and lv:
            ds = ds.sel(level=lv)
        ds = _grid_cut(ds, bbox)
        per_var: dict[str, xr.DataArray] = {}
        for nat, ewb_name in native.items():
            da = ds[nat].rename({"time": "lead"}).transpose("lead", ...)
            per_var[ewb_name] = da.chunk({"lead": 6}).compute()
    finally:
        f.close()
    return _finish(per_var, init, leads, bbox)


# ------------------------------------------------------------------------- public
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

    The Icechunk store covers the FULL CIRA archive (virtual refs, daily update),
    so its listing is the primary answer and works for 2020-era queries without
    ~2200 bucket LIST calls. The direct bucket listing (authoritative for brand-new
    files, and the only path when the store is absent) then only has to cover the
    tail newer than the store's last init. The store listing already excludes
    upstream-corrupt objects (known-bad ledger); the direct path skips them too.
    """
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
        except Exception:  # noqa: BLE001 - store unreadable: direct only
            covered_through = None
    # direct bucket listing from the first day the store might not know about;
    # re-listing the store's last covered day is 1 cheap LIST and closes any gap.
    if covered_through is None or covered_through < start:
        day = dt.datetime(start.year, start.month, start.day)
    else:
        day = dt.datetime(covered_through.year, covered_through.month, covered_through.day)
    while day <= end:
        key = f"{code}/{day:%Y}/{day:%m%d}/"
        try:
            keys = _net.list_prefix(_net.AIWP_BUCKET, key, suffix=".nc")
        except FileNotFoundError:  # missing day is normal
            keys = []
        except Exception as exc:  # noqa: BLE001 - probe failure != absent day
            raise RuntimeError(f"aiwp listing failed for {key}: {exc}") from exc
        for k in keys:
            if k.rsplit("/", 1)[-1] in bad:
                continue
            m = AIWP_RE.search(k)
            if m:
                ts = dt.datetime.strptime(m.group(2), "%Y%m%d%H")
                if start <= ts <= end:
                    out.add(ts)
        day += dt.timedelta(days=1)
    return sorted(out)


def open_forecast(
    spec: ModelSpec,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox,
) -> xr.Dataset:
    native = _native_map(spec, variables)
    leads = _select_leads(lead_hours, spec)
    try:
        return _open_via_store(spec, init, native, leads, bbox)
    except _StoreMiss:
        return _open_direct(spec, init, native, leads, bbox)
