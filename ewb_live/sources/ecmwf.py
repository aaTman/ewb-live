"""ECMWF open-data GRIB backend (s3://ecmwf-forecasts): IFS HRES, AIFS Single.

Layout:  <YYYYMMDD>/<HH>z/<system>/<grid>/<stream>/<init>-<lead>h-<stream>-fc.grib2
         with a JSON ``.index`` next to each file giving per-field byte ranges
         (keys: levtype, param, levelist, step, _offset, _length).
Leads (verified live 2026-11-06): IFS 0-144 h step 3 h, 144-360 h step 6 h;
AIFS Single 0-360 h step 6 h. (0.25 deg, lon 0..359.75.)
Ranged object reads go through the public CloudFront distribution to avoid the
S3 anonymous SlowDown wall; LIST stays on S3.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile

import cfgrib
import numpy as np
import xarray as xr

from . import _net
from .registry import ECMWF_PARAM_TO_EWB, ModelSpec

# EWB name -> (levtype, grib param)
_EWB_TO_ECMWF = {
    "surface_air_temperature": ("sfc", "2t"),
    "surface_dewpoint_temperature": ("sfc", "2d"),
    "air_pressure_at_mean_sea_level": ("sfc", "msl"),
    "surface_eastward_wind": ("sfc", "10u"),
    "surface_northward_wind": ("sfc", "10v"),
    "100m_eastward_wind": ("sfc", "100u"),
    "100m_northward_wind": ("sfc", "100v"),
    "accumulated_precipitation": ("sfc", "tp"),
    "air_temperature": ("pl", "t"),
    "eastward_wind": ("pl", "u"),
    "northward_wind": ("pl", "v"),
    "specific_humidity": ("pl", "q"),
    "relative_humidity": ("pl", "r"),
    "geopotential": ("pl", "z"),
    "geopotential_height": ("pl", "gh"),
}

LEVEL_DIMS = ("isobaricInhPa", "pressureLevel")

_LEAD_RE = re.compile(r"-(\d+)h-oper-fc\.grib2$")


def leads_for(init: dt.datetime, max_lead: int = 240) -> list[int]:
    """Nominal published lead schedule for the ECMWF 0.25 deg systems (IFS)."""
    leads = list(range(0, min(144, max_lead) + 1, 3))
    leads += list(range(150, max_lead + 1, 6))
    return leads


def _stream_dir(spec: ModelSpec, init: dt.datetime, lead: int) -> tuple[str, str]:
    rel = f"{init:%Y%m%d}/{init:%H}z/{spec.ecmwf_path}"
    obj = f"{init:%Y%m%d%H}0000-{lead}h-oper-fc.grib2"
    return rel, obj


def _index_obj(obj: str) -> str:
    # bucket layout: 'foo-fc.grib2' <-> 'foo-fc.index' (no '.grib2.' segment)
    return obj.removesuffix(".grib2") + ".index"


def available_inits(spec: ModelSpec, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    out: list[dt.datetime] = []
    day = dt.datetime(start.year, start.month, start.day)
    fs = _net.s3("eu-central-1")
    probe_failures = 0
    while day <= end:
        for hh in (0, 6, 12, 18):
            init = day.replace(hour=hh)
            if not (start <= init <= end):
                continue
            rel, obj = _stream_dir(spec, init, 0)
            path = f"{_net.ECMWF_BUCKET}/{rel}/{obj}"
            try:
                if _net.with_retries(lambda p=path: fs.exists(p)):
                    out.append(init)
            except Exception:  # noqa: BLE001 — probe failure != absent; must not fake an empty day
                probe_failures += 1
        day += dt.timedelta(days=1)
    if probe_failures:
        raise RuntimeError(
            f"ifs init listing incomplete: {probe_failures} probe failures in "
            f"{start:%Y%m%d}-{end:%Y%m%d}")
    return sorted(out)


def _s3_range_or_get(rel: str, obj: str, start: int | None = None, stop: int | None = None) -> bytes:
    fs = _net.s3("eu-central-1")
    path = f"{_net.ECMWF_BUCKET}/{rel}/{obj}"
    if start is None:
        return _net.with_retries(lambda: fs.read_bytes(path))
    return _net.with_retries(lambda: fs.cat_file(path, start=start, end=stop))


def _read_index(rel: str, obj: str) -> list[dict]:
    url = f"{_net.ECMWF_CLOUDFRONT}/{rel}/{_index_obj(obj)}"
    try:
        txt = _net.fetch_range_http(url)
    except Exception:  # noqa: BLE001 - CloudFront unavailable: fall back to S3
        txt = _s3_range_or_get(rel, _index_obj(obj))
    return [json.loads(line) for line in txt.decode().splitlines() if line.strip()]


def _fetch_bytes(rel: str, obj: str, start: int, stop: int) -> bytes:
    url = f"{_net.ECMWF_CLOUDFRONT}/{rel}/{obj}"
    try:
        return _net.fetch_range_http(url, start, stop)
    except Exception:  # noqa: BLE001
        return _s3_range_or_get(rel, obj, start, stop)


def open_forecast(
    spec: ModelSpec,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox,
) -> xr.Dataset:
    wanted: dict[tuple[str, str], str] = {}
    for v in variables:
        if v not in _EWB_TO_ECMWF:
            raise ValueError(f"variable {v!r} not available for model {spec.name!r}")
        wanted[_EWB_TO_ECMWF[v]] = v
    levels = [int(x) for x in (spec.levels or [])]

    # one directory listing gives the set of published leads (3 h to 144 h,
    # then 6 h for IFS; 6 h throughout for AIFS) -- avoids probing index files
    rel0, _ = _stream_dir(spec, init, 0)
    keys = _net.list_prefix(
        _net.ECMWF_BUCKET, rel0 + "/", region="eu-central-1", suffix="oper-fc.grib2"
    )
    avail = {int(m.group(1)) for k in keys if (m := _LEAD_RE.search(k))}

    surface_parts: dict[int, dict[str, xr.DataArray]] = {}
    level_parts: dict[int, dict[str, xr.DataArray]] = {}
    leads_used: list[int] = []

    import os as _os

    from concurrent.futures import ThreadPoolExecutor

    def _read_lead(lead: int):
        """index + ranged-get + grib-decode for one lead (network-bound)."""
        if lead not in avail:
            return None
        rel, obj = _stream_dir(spec, init, lead)
        try:
            index = _read_index(rel, obj)
        except Exception:  # noqa: BLE001 - lead file not (yet) published
            return None
        blob = b""
        for rec in index:
            key = (rec["levtype"], rec["param"])
            if key not in wanted:
                continue
            if rec["levtype"] == "pl":
                lvl = int(rec.get("levelist", 0))
                if lvl not in levels:
                    continue
            blob += _fetch_bytes(rel, obj, rec["_offset"], rec["_offset"] + rec["_length"])
        if not blob:
            return None
        return lead, _decode_grib(blob)

    n_par = int(_os.environ.get("EWB_LIVE_LEAD_PARALLEL", "8"))
    if n_par > 1:
        with ThreadPoolExecutor(max_workers=n_par) as ex:
            results = [r for r in ex.map(_read_lead, lead_hours) if r is not None]
    else:
        results = [r for r in map(_read_lead, lead_hours) if r is not None]
    for lead, decoded in sorted(results, key=lambda r: r[0]):
        ok = False
        for (levtype, param), ewb in wanted.items():
            da = decoded.get(param)
            if da is None:
                continue
            ok = True
            if levtype == "sfc":
                surface_parts.setdefault(lead, {})[ewb] = da
            else:
                level_parts.setdefault(lead, {})[ewb] = da
        if ok:
            leads_used.append(lead)

    if not leads_used:
        raise ValueError(f"no leads available for {spec.name} init {init:%Y-%m-%d %H:%MZ}")
    return _assemble_grib(surface_parts, level_parts, init, leads_used, bbox)


def _decode_grib(blob: bytes) -> dict[str, xr.DataArray]:
    """Decode a blob of concatenated GRIB messages to {param: DataArray}."""
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as fh:
        fh.write(blob)
        tmp = fh.name
    out: dict[str, xr.DataArray] = {}
    try:
        dss = cfgrib.open_datasets(
            tmp, backend_kwargs={"errors": "warn", "read_keys": ["param"]}
        )
        for d in dss:
            for name, da in d.data_vars.items():
                da = da.squeeze("surface", drop=True) if "surface" in da.dims else da
                for ld in LEVEL_DIMS:
                    if ld in da.dims:
                        da = da.rename({ld: "level"})
                # materialise while the temp file still exists: cfgrib's
                # backend store reads from disk lazily
                da = da.load()
                da = da.drop_vars(
                    [c for c in da.coords if c not in ("latitude", "longitude", "level")]
                )
                # key by both the cfgrib variable name and the GRIB shortName
                # (they differ, e.g. param '2t' -> name 't2m', shortName '2t')
                short = str(da.attrs.get("GRIB_shortName", name))
                out[short] = da
                out.setdefault(str(name), da)
    finally:
        os.unlink(tmp)
    return out


def _assemble_grib(surface_parts, level_parts, init, leads_used, bbox) -> xr.Dataset:
    from ._transform import assemble

    out: dict[str, xr.DataArray] = {}
    surf_names = {n for p in surface_parts.values() for n in p}
    ml_names = {n for p in level_parts.values() for n in p}
    for name in surf_names:
        arrs, sf_used = [], []
        for h in leads_used:
            if name in surface_parts[h]:
                arrs.append(surface_parts[h][name])
                sf_used.append(h)
        da = xr.concat(arrs, dim="lead")
        # lead coords must follow the leads actually present, else a missing
        # surface record shifts the whole lead axis (ponytail 2026-11-08)
        da = da.assign_coords(lead=sf_used)
        out[name] = da
    for name in ml_names:
        arrs, lv_used = [], []
        for h in leads_used:
            if name in level_parts[h]:
                arrs.append(level_parts[h][name])
                lv_used.append(h)
        da = xr.concat(arrs, dim="lead").assign_coords(lead=lv_used)
        out[name] = da.transpose("lead", "level", "latitude", "longitude")
    return assemble(out, np.datetime64(init.replace(tzinfo=None), "ns"), leads_used, bbox)
