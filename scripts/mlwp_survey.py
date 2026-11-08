"""Survey the CIRA MLWP archive through the same VirtualiZarr path the builder uses.

For every NetCDF file in the window this does a header-only remote read and records,
per variable: dims, shape, dtype, zarr/HDF5 chunk shape, fill value, codecs (compression
filters), attribute keys; plus coordinate values (time/level/latitude/longitude), the
global attrs, and the manifest size. Resume-safe (reruns skip already-recorded keys).

This is both the metadata survey and a per-file build-readiness check: a file whose
parse fails here will fail in the store build too.

Run: .venv/bin/python scripts/mlwp_survey.py scan --days 120
     .venv/bin/python scripts/mlwp_survey.py hist --years 2023 2024 2025
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ewb_live.stores import mlwp_common as C  # noqa: E402

LOADABLE = ["time", "level", "latitude", "longitude"]

_W: dict = {}


def _worker_ctx():
    if "registry" not in _W:
        import obstore as obs
        import virtualizarr as vz
        from obspec_utils.registry import ObjectStoreRegistry

        store = obs.store.from_url(C.BUCKET_URI, region=C.REGION, skip_signature=True)
        _W["registry"] = ObjectStoreRegistry({C.BUCKET_URI: store})
        _W["parser"] = vz.parsers.HDFParser()
        _W["vz"] = vz
    return _W


def inspect_url(url: str) -> dict:
    import numpy as np
    import xarray as xr

    ctx = _worker_ctx()
    res: dict = {"url": url, "model": url.split("/")[3]}
    try:
        vds = ctx["vz"].open_virtual_dataset(
            url,
            registry=ctx["registry"],
            parser=ctx["parser"],
            loadable_variables=LOADABLE,
            decode_times=True,
        )
        res["attrs"] = {k: str(v) for k, v in vds.attrs.items()}
        variables = {}
        for name, da in vds.variables.items():
            ma = da.data
            if not hasattr(ma, "metadata") or not hasattr(ma, "manifest"):
                variables[name] = {"inline": True, "dims": list(da.dims),
                                   "shape": list(da.shape), "dtype": str(da.dtype)}
                continue
            md = da.data.metadata
            codecs = md.codecs
            filt_names = []
            for c in codecs:
                cf = c.to_dict() if hasattr(c, "to_dict") else {}
                cname = cf.get("name") or type(c).__name__
                cfg = {k: v for k, v in cf.items() if k != "name"}
                filt_names.append((cname, cfg))
            variables[name] = {
                "dims": list(da.dims),
                "shape": list(md.shape),
                "dtype": str(md.dtype),
                "chunk_shape": list(md.chunks),
                "chunk_grid": (md.chunk_grid.to_dict() if hasattr(md.chunk_grid, "to_dict")
                               else str(md.chunk_grid)),
                "fill_value": repr(md.fill_value),
                "codecs": filt_names,
                "chunk_key_encoding": str(md.chunk_key_encoding),
                "nbytes_virtual_mb": round(ma.nbytes_virtual / 1e6, 2),
                "nchunks_total": int(np.prod([np.ceil(s / c) for s, c in
                                              zip(md.shape, md.chunks)])),
                "manifest_entries": len(ma.manifest),
                "attr_keys": sorted(da.attrs.keys()),
                "units": str(da.attrs.get("units", "")),
                "attr_subset": {k: str(v) for k, v in da.attrs.items()
                                if k in ("_FillValue", "scale_factor", "add_offset",
                                         "coordinates", "grid_mapping", "long_name")},
            }
        res["variables"] = variables
        coords = {}
        for cn in LOADABLE:
            if cn not in vds.variables:
                continue
            v = np.asarray(vds[cn].values)
            c = {"n": int(v.size), "dtype": str(v.dtype),
                 "attrs": {k: str(x) for k, x in vds[cn].attrs.items()}}
            f = np.ravel(v)
            c["first3"] = [str(x) for x in f[:3]]
            c["last3"] = [str(x) for x in f[-3:]]
            if v.dtype.kind == "M":
                c["step_h"] = sorted({int((f[i + 1] - f[i]) / np.timedelta64(1, "h"))
                                      for i in range(min(3, f.size - 1))})
            elif v.dtype.kind == "f":
                f64 = f.astype("float64")
                c["min"], c["max"] = float(f64.min()), float(f64.max())
                c["deltas"] = sorted(float(x) for x in set(np.round(np.diff(f64), 6)))[:4]
            if f.size <= 45:
                c["all"] = [str(x) for x in f]
            coords[cn] = c
        res["coords"] = coords
        res["nvars"] = len(vds.data_vars)
        res["nbytes_virtual_mb"] = round(
            sum(v.data.nbytes_virtual for v in vds.data_vars.values()
                if hasattr(v.data, "nbytes_virtual")) / 1e6, 1)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def _done_keys(p: str) -> set[str]:
    done = set()
    if os.path.exists(p):
        for line in open(p):
            try:
                done.add(json.loads(line)["url"])
            except Exception:  # noqa: BLE001
                pass
    return done


def _run(urls: list[str], out: str, workers: int) -> None:
    done = _done_keys(out)
    todo = [u for u in urls if u not in done]
    print(f"{len(todo)} files to inspect ({len(done)} cached)", flush=True)
    n = err = 0
    with open(out, "a") as fh, ProcessPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(inspect_url, todo, chunksize=4):
            fh.write(json.dumps(res) + "\n")
            n += 1
            err += bool(res.get("error"))
            if n % 100 == 0:
                fh.flush()
                print(f"  {n}/{len(todo)} ({err} errors)", flush=True)
    print(f"wrote {out}: {n} records, {err} errors")


def cmd_scan(a) -> None:
    inv = C.inventory(a.days, refresh=True)
    urls = [C.url_for(m, d, fn) for m, d, fn in C.iter_files(inv)]
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    _run(urls, f"{C.CACHE_DIR}/survey_vz_{a.days}d.jsonl", a.workers)


def cmd_hist(a) -> None:
    urls = []
    for m in C.list_model_dirs():
        for y in a.years:
            for md in ("0115", "0401", "0520", "0526", "0528", "0701", "1001", "1215"):
                d = dt.datetime.strptime(f"{y}{md}", "%Y%m%d").date()
                for fn in C.day_files(m, d):
                    urls.append(C.url_for(m, d, fn))
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    _run(sorted(urls), f"{C.CACHE_DIR}/survey_vz_hist.jsonl", a.workers)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["scan", "hist"])
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--years", nargs="*", default=["2023", "2024", "2025"])
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    {"scan": cmd_scan, "hist": cmd_hist}[a.cmd](a)
