"""Shared bucket helpers for the CIRA/NOAA MLWP archive (s3://noaa-oar-mlwp-data).

Layout (verified against the bucket 2026-11-06):
    <MODEL_DIR>/YYYY/mmdd/<CODE>_v<V>_<FORCING>_YYYYMMDDHH_f000_f240_06.nc
where MODEL_DIR == the filename stem prefix, e.g. ``GRAP_v100_IFS``, CODE is a 4-letter
model code (AURO/FOUR/GRAP/PANG), FORCING is GFS or IFS. One file per model+init,
lead hours 0..240 step 6 (41 steps), files are NetCDF4/HDF5.

This module knows *nothing* about the store: it is the S3 listing/paths layer used by
both the metadata survey (scripts/mlwp_survey.py) and the Icechunk builder.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
import re

BUCKET = "noaa-oar-mlwp-data"
BUCKET_URI = f"s3://{BUCKET}"
REGION = "us-east-1"
LEAD_STEP_H = 6
LEAD_MAX_H = 240

#: directory names that live in the bucket but are not model directories
NON_MODEL_DIRS = {"Derived", "parquet", "colab_resources"}

MODEL_DIR_RE = re.compile(r"^[A-Z]{4}_v\d{2,3}_(?:GFS|IFS)$")
FILE_RE = re.compile(
    r"^(?P<prefix>[A-Z]{4}_v\d{2,3}_(?:GFS|IFS))_"
    r"(?P<init>\d{10})_f(?P<f0>\d{3})_f(?P<f1>\d{3})_(?P<step>\d{2})\.nc$"
)

CACHE_DIR = "data/cache/mlwp"

#: last date with data as of the initial survey; listing beyond this is cheap and
#: returns nothing, so the inventory simply comes up short (self-correcting).
TODAY = dt.date.today()


def s3():
    """Anonymous s3fs filesystem for the MLWP bucket (one per process)."""
    import s3fs

    return s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": REGION})


def list_model_dirs(fs=None) -> list[str]:
    """Model directories in the bucket (e.g. ``GRAP_v100_IFS``), sorted."""
    fs = fs or s3()
    out = []
    for e in fs.ls(f"{BUCKET}/"):
        name = e.rstrip("/").split("/")[-1]
        if name in NON_MODEL_DIRS or not MODEL_DIR_RE.match(name):
            continue
        out.append(name)
    return sorted(out)


def parse_filename(fn: str) -> tuple[str, dt.datetime, tuple[int, int, int]]:
    """(model_dir, init datetime, (first_hour, last_hour, step_hours)) from a filename."""
    m = FILE_RE.match(fn)
    if not m:
        raise ValueError(f"unrecognised MLWP filename: {fn}")
    init = dt.datetime.strptime(m["init"], "%Y%m%d%H")
    return (
        m["prefix"],
        init,
        (int(m["f0"]), int(m["f1"]), int(m["step"])),
    )


def key_for(model: str, day: dt.date, fn: str) -> str:
    """S3 object key for a file."""
    return f"{BUCKET}/{model}/{day:%Y}/{day:%m%d}/{fn}"


def url_for(model: str, day: dt.date, fn: str) -> str:
    return f"{BUCKET_URI}/{model}/{day:%Y}/{day:%m%d}/{fn}"


def day_files(model: str, day: dt.date, fs=None) -> list[str]:
    """Filenames present for a model on a UTC calendar day ([] when the day is absent)."""
    fs = fs or s3()
    prefix = f"{BUCKET}/{model}/{day:%Y}/{day:%m%d}"
    try:
        keys = fs.ls(prefix)
    except FileNotFoundError:
        return []
    return sorted(k.rsplit("/", 1)[1] for k in keys if k.endswith(".nc"))


def inits_for_day(model: str, day: dt.date, fs=None) -> list[dt.datetime]:
    out = []
    for fn in day_files(model, day, fs):
        _, init, _ = parse_filename(fn)
        out.append(init)
    return sorted(out)


def inventory(days: int = 120, end: dt.date | None = None, refresh: bool = False,
              fs=None) -> dict[str, dict[str, list[str]]]:
    """model -> {"YYYY-MM-DD": [filenames]} over the last ``days`` days to ``end``.

    Cached on disk (per window length) unless ``refresh``.
    """
    end = end or TODAY
    path = os.path.join(CACHE_DIR, f"inventory_{days}d_{end:%Y%m%d}.json")
    if not refresh and os.path.exists(path):
        return json.load(open(path))
    fs = fs or s3()
    models = list_model_dirs(fs)
    out: dict[str, dict[str, list[str]]] = {m: {} for m in models}
    d = end - dt.timedelta(days=days)
    while d <= end:
        for m in models:
            fns = day_files(m, d, fs)
            if fns:
                out[m][f"{d:%Y-%m-%d}"] = fns
        d += dt.timedelta(days=1)
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, path)
    return out


#: first year to scan in the full-archive listing (archive starts 2020-09-30;
#: one extra year of slack costs a single empty LIST per model).
ARCHIVE_FIRST_YEAR = 2019


def inventory_all(end: dt.date | None = None, refresh: bool = False,
                  fs=None) -> dict[str, dict[str, list[str]]]:
    """model -> {"YYYY-MM-DD": [filenames]} over the ENTIRE archive (no window).

    Listed with one recursive LIST per model-year (``fs.find``) instead of one per
    day: the full archive is ~31k files and the store keeps all of it, so the
    day-by-day loop in ``inventory`` would need ~2200 requests per update. Keys in
    the bucket path (``<model>/YYYY/mmdd/``) always matched the filename init in
    the survey — a mismatch is a listing bug we must not hide, so the file is
    skipped loudly rather than silently mis-dated. Same cache/output shape as
    ``inventory`` (cached once per UTC day).
    """
    end = end or TODAY
    path = os.path.join(CACHE_DIR, f"inventory_all_{end:%Y%m%d}.json")
    if not refresh and os.path.exists(path):
        return json.load(open(path))
    fs = fs or s3()
    out: dict[str, dict[str, list[str]]] = {}
    for m in list_model_dirs(fs):
        days: dict[str, list[str]] = {}
        for y in range(ARCHIVE_FIRST_YEAR, end.year + 1):
            for key in fs.find(f"{BUCKET}/{m}/{y}/"):
                key = key.removeprefix(f"{BUCKET}/")
                if not key.endswith(".nc"):
                    continue
                fn = key.rsplit("/", 1)[1]
                seg = key.rsplit("/", 2)[-2]          # the mmdd path segment
                try:
                    _, init, _ = parse_filename(fn)
                except ValueError:
                    print(f"inventory_all: unparseable object skipped: {key}",
                          flush=True)
                    continue
                if seg != f"{init:%m%d}":
                    print(f"inventory_all: path/init mismatch skipped: {key}",
                          flush=True)
                    continue
                days.setdefault(f"{init:%Y-%m-%d}", []).append(fn)
        out[m] = {d: sorted(v) for d, v in sorted(days.items())}
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, path)
    return out


def iter_files(inv: dict[str, dict[str, list[str]]], model: str | None = None,
               start: dt.date | None = None, end: dt.date | None = None):
    """Yield (model, day, filename) triples from an inventory, sorted by init."""
    for m in sorted(inv):
        if model and m != model:
            continue
        for ds in sorted(inv[m]):
            d = dt.date.fromisoformat(ds)
            if start and d < start:
                continue
            if end and d > end:
                continue
            for fn in sorted(inv[m][ds]):
                yield m, d, fn


def file_url(model: str, day: dt.date, fn: str) -> str:
    return url_for(model, day, fn)


@functools.lru_cache(maxsize=1)
def _tzinfo() -> dt.timezone:
    return dt.timezone.utc
