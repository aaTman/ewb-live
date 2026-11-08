#!/usr/bin/env python
"""Enumerate the FULL CIRA MLWP archive: per-model per-year file counts, boundaries,
and structural probes of edge-case models (successor-2 design recon for the backfill).

    ops/remote.sh run python scripts/mlwp_archive_survey.py [--json-out PATH]

Fast: one recursive LIST per model-year (~70 requests). Also virtualises one file
per model via the builder's virtual_init to measure era signatures and detect
partial edge chunks (the virtualizarr <=2.8 mis-offset hazard class).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ewb_live.stores import mlwp_common as C  # noqa: E402
from ewb_live.stores import mlwp_icechunk as M  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default="data/cache/mlwp/archive_survey.json")
    args = ap.parse_args()

    fs = C.s3()
    years = range(2019, dt.date.today().year + 1)
    out: dict = {"date": str(dt.date.today()), "models": {}}
    total = 0
    for m in C.list_model_dirs(fs):
        per_year: dict[str, int] = {}
        first = last = None
        mismatches = 0
        for y in years:
            prefix = f"{C.BUCKET}/{m}/{y}/"
            try:
                keys = fs.find(prefix)
            except Exception as e:  # noqa: BLE001
                print(f"  ls {prefix} failed: {e}", file=sys.stderr)
                continue
            keys = [k for k in keys if k.endswith(".nc")]
            if not keys:
                continue
            per_year[str(y)] = len(keys)
            fns = sorted(k.rsplit("/", 1)[1] for k in keys)
            d0 = C.parse_filename(fns[0])[1]
            d1 = C.parse_filename(fns[-1])[1]
            first = d0 if first is None else min(first, d0)
            last = d1 if last is None else max(last, d1)
            # key path date must equal filename init date (url_for reconstructs from it)
            for k in keys[:2000:250]:
                seg = k.split("/")[-2]
                fn = k.rsplit("/", 1)[1]
                if seg != f"{C.parse_filename(fn)[1]:%m%d}":
                    mismatches += 1
        total += sum(per_year.values())
        out["models"][m] = {"years": per_year, "total": sum(per_year.values()),
                            "first": str(first), "last": str(last),
                            "path_date_mismatches": mismatches}
        print(f"{m:16s} files={sum(per_year.values()):5d} "
              f"first={first} last={last} years={sorted(per_year)} "
              f"mismatch={mismatches}", flush=True)
    out["total_files"] = total
    print(f"TOTAL model files: {total}")

    # structural probe: one recent file per model through the builder's parse path
    print("\n== structural probe (virtual_init) ==")
    probes = []
    for m, info in out["models"].items():
        # pick the newest init of that model
        d = dt.date.fromisoformat(info["last"][:10])
        fn = None
        for k in fs.ls(f"{C.BUCKET}/{m}/{d:%Y}/{d:%m%d}/", detail=False):
            if k.endswith(".nc"):
                fn = k.rsplit("/", 1)[1]
        if fn is None:
            probes.append({"model": m, "error": "no file on last day"})
            continue
        url = f"{C.BUCKET_URI}/{m}/{d:%Y}/{d:%m%d}/{fn}"
        init = C.parse_filename(fn)[1]
        try:
            vds = M.virtual_init(url, init)
        except Exception as e:  # noqa: BLE001
            probes.append({"model": m, "error": f"{type(e).__name__}: {e}"})
            print(f"[FAIL] {m}: {type(e).__name__}: {e}", flush=True)
            continue
        risk = {}
        for name, da in vds.data_vars.items():
            md = getattr(da.data, "metadata", None)
            if md is None:
                continue
            bad = [f"{dim}:{shape}%{ch}" for dim, shape, ch in
                   zip(da.dims, md.shape, md.chunks) if shape % ch]
            if bad:
                risk[name] = bad
        rec = {"model": m, "sig": vds.attrs["layout_signature"],
               "vars": sorted(vds.data_vars), "nrefs": int(vds.vz.nrefs()),
               "dims": {str(k): int(v) for k, v in vds.sizes.items()},
               "edge_chunk_risk": risk}
        probes.append(rec)
        print(f"[ok] {m:16s} sig={rec['sig']} nrefs={rec['nrefs']} "
              f"dims={rec['dims']}" +
              (f" EDGE-RISK={risk}" if risk else ""), flush=True)
    out["probes"] = probes

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
