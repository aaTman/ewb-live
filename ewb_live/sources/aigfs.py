"""NOAA AIGFS backend (s3://noaa-nws-graphcastgfs-pds).

Operational GraphCast-based NCEP model ("AIGFS", replaced EAGLE SOLO Dec 2025).
Layout:  aigfs.<YYYYMMDD>/<HH>/model/atmos/grib2/aigfs.t<HH>z.pres.f<LLL>.grib2
plus a Herbie-style ``.idx`` (lines ``N:END_OFFSET:d=...:SHORTNAME:LEVEL:fcst:``;
record byte range is (previous END, this END]). Cycles 00/06/12/18Z, leads
0-384 step 6 h (130 lead files per cycle, verified 2026-11-06); publication
gaps occur (e.g. a whole day missing). Pressure levels only (TMP/SPFH/UGRD/
VGRD/HGT/VVEL at 13 mb levels); NO surface fields. 0.25 deg.
"""

from __future__ import annotations

import datetime as dt
import io

import eccodes
import numpy as np
import xarray as xr

from . import _net, ecmwf
from .registry import ModelSpec

_EWB_TO_AIGFS = {
    "air_temperature": "TMP",
    "specific_humidity": "SPFH",
    "eastward_wind": "UGRD",
    "northward_wind": "VGRD",
    "geopotential": "HGT",
}

# idx SHORTNAME -> cfgrib/eccodes GRIB2 shortName of the decoded variable.
# NDFD "HGT" on pressure levels is GEOPOTENTIAL HEIGHT in gpm (eccodes
# shortName 'gh'); EWB's 'geopotential' is z in m2/s2, so it is converted
# after decode (_GPM_TO_Z).
_IDX_TO_CFGrib = {
    "TMP": "t",
    "SPFH": "q",
    "UGRD": "u",
    "VGRD": "v",
    "HGT": "gh",
    "VVEL": "w",
}

_GPM_TO_Z = 9.80665

AIGFS_LEADS = list(range(0, 241, 6))


def _prefix(spec: ModelSpec, init: dt.datetime) -> str:
    return f"{_net.AIGFS_BUCKET}/aigfs.{init:%Y%m%d}/{init:%H}/model/atmos/grib2"


def available_inits(spec: ModelSpec, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    fs = _net.s3("us-east-1")
    out: list[dt.datetime] = []
    day = dt.datetime(start.year, start.month, start.day)
    while day <= end:
        for hh in (0, 6, 12, 18):
            init = day.replace(hour=hh)
            if not (start <= init <= end):
                continue
            p = f"{_prefix(spec, init)}/aigfs.t{init:%H}z.pres.f000.grib2"
            try:
                if _net.with_retries(lambda q=p: fs.exists(q)):
                    out.append(init)
            except Exception:  # noqa: BLE001
                continue
        day += dt.timedelta(days=1)
    return sorted(out)


def open_forecast(
    spec: ModelSpec,
    init: dt.datetime,
    variables: list[str],
    lead_hours: list[int],
    bbox,
) -> xr.Dataset:
    wanted: dict[str, str] = {}
    for v in variables:
        if v not in _EWB_TO_AIGFS:
            raise ValueError(
                f"variable {v!r} not available for model 'aigfs' "
                f"(pressure levels only; surface fields not published)"
            )
        wanted[_EWB_TO_AIGFS[v]] = v
    levels = {f"{int(l)} mb" for l in (spec.levels or [])}

    prefix = _prefix(spec, init)
    surface_parts: dict[int, dict[str, xr.DataArray]] = {}
    level_parts: dict[int, dict[str, xr.DataArray]] = {}
    leads_used: list[int] = []
    for lead in lead_hours:
        obj = f"{prefix}/aigfs.t{init:%H}z.pres.f{lead:03d}.grib2"
        try:
            idx_txt = _net.with_retries(lambda q=obj: _net.s3("us-east-1").read_text(q + ".idx"))
        except Exception:  # noqa: BLE001
            continue
        lines = [ln for ln in idx_txt.splitlines() if ln and not ln.startswith("Start")]
        # Herbie-style idx for this bucket: field i spans [END_i, END_{i+1}) --
        # the column holds the cumulative offset BEFORE the message; the final
        # record ends at the object size.
        bounds = [int(ln.split(":")[1]) for ln in lines]
        try:
            size = _net.s3("us-east-1").info(obj)["size"]
        except Exception:  # noqa: BLE001
            size = bounds[-1]
        bounds.append(size)
        blob = b""
        for i, ln in enumerate(lines):
            parts = ln.split(":")
            if parts[3] in wanted and parts[4] in levels:
                s, e = bounds[i], bounds[i + 1]
                if e <= s:
                    continue
                blob += _net.with_retries(
                    lambda q=obj, s=s, e=e: _net.s3("us-east-1").cat_file(q, start=s, end=e)
                )
        if not blob:
            continue
        decoded = ecmwf._decode_grib(blob)
        ok = False
        for short, ewb in wanted.items():
            da = decoded.get(_IDX_TO_CFGrib.get(short, short))
            if da is None:
                da = decoded.get(short)
            if da is None:
                continue
            if short == "HGT":
                da = da * _GPM_TO_Z
                da.attrs = {**da.attrs, "units": "m**2 s**-2",
                            "comment": "converted from NDFD geopotential height (gpm)"}
            ok = True
            level_parts.setdefault(lead, {})[ewb] = da
        if ok:
            leads_used.append(lead)

    if not leads_used:
        raise ValueError(f"no lead files available for aigfs init {init:%Y-%m-%d %HZ}")
    return ecmwf._assemble_grib(surface_parts, level_parts, init, leads_used, bbox)
