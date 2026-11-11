#!/usr/bin/env python
"""Probe one MLWP source file: raw structure (dims/vars/HDF5 chunk shapes) and the
builder's virtual_init result (with traceback), to diagnose odd model versions.

    ops/remote.sh run python scripts/mlwp_probe_file.py FOUR_v100_GFS 2023-10-01 12
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ewb_live.stores import mlwp_common as C  # noqa: E402
from ewb_live.stores import mlwp_icechunk as M  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("hour", nargs="?", type=int, default=None)
    ap.add_argument("--direct", action="store_true", help="also print direct-read dtypes")
    args = ap.parse_args()

    d = dt.date.fromisoformat(args.date)
    fns = C.day_files(args.model, d)
    if args.hour is not None:
        fns = [f for f in fns if f"_{args.hour:02d}_f000" in f or f"{args.hour:010d}" in f]
        fns = [f for f in C.day_files(args.model, d)
               if C.parse_filename(f)[1].hour == args.hour]
    if not fns:
        print(f"no files for {args.model} on {d} hour={args.hour}", file=sys.stderr)
        return 1
    fn = fns[0]
    init = C.parse_filename(fn)[1]
    url = C.url_for(args.model, d, fn)
    print(f"file {url}")

    import s3fs
    import xarray as xr

    fs = s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": C.REGION})
    with fs.open(C.key_for(args.model, d, fn)) as f:
        with xr.open_dataset(f, engine="h5netcdf", decode_cf=False) as raw:
            print("== global attrs ==")
            for k, v in raw.attrs.items():
                print(f"  {k}: {str(v)[:100]}")
            print("== dims ==", dict(raw.sizes))
            print("== variables ==")
            for name, da in raw.variables.items():
                enc = da.encoding or {}
                ch = enc.get("chunksizes")
                print(f"  {name:10s} dims={da.dims} shape={da.shape} "
                      f"dtype={da.dtype} chunks={ch}")
            if args.direct:
                print("== direct read t2-ish ==")
                for cand in ("t2", "2t", "t"):
                    if cand in raw:
                        v = raw[cand].isel(time=0).values
                        print(f"  {cand}: mean={np.nanmean(v.astype('float64')):.2f} "
                              f"min={np.nanmin(v.astype('float64')):.2f}")
                        break

    print("== virtual_init ==")
    try:
        vds = M.virtual_init(url, init)
        print("  ok:", dict(vds.sizes), "sig=", vds.attrs["layout_signature"],
              "nrefs=", int(vds.vz.nrefs()))
        for name, da in vds.data_vars.items():
            md = getattr(da.data, "metadata", None)
            if md is not None:
                bad = [f"{dim}:{shape}%{ch}" for dim, shape, ch in
                       zip(da.dims, md.shape, md.chunks) if shape % ch]
                print(f"  {name:10s} {da.dims} chunks={md.chunks}"
                      + (f"  PARTIAL-EDGE {bad}" if bad else ""))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
