#!/usr/bin/env python
"""Verify the operational Icechunk store: coverage, size, values, read speed.

    uv run python scripts/mlwp_verify_store.py [--repo data/icechunk/cira_mlwp]
                                               [--days N]  (default: full archive)
                                               [--mem-gb 20]

Checks, per model in the store:
  * init_time count/sortedness/uniqueness vs the live bucket listing
    (missing may be non-zero: the known-bad ledger lists upstream-corrupt objects
    deliberately absent from the store; extras are always a bug)
  * coverage summary: first/last init, year span, era groups
  * timed subset reads (1 init x 3 leads x 2 vars x small bbox) vs direct NetCDF
    reads of the same files, asserting bit-identical values, on TWO inits: the
    newest and the oldest in the store (spans the whole archive)
  * store size: total bytes, object (file) count, manifest bytes/count

The value checks each run in a CHILD PROCESS scoped with
`systemd-run -p MemoryMax` (falling back to plain spawn when systemd is
unavailable): building a model's lazy view decodes every era-group manifest
(~8 GB RSS for the largest model) and looping all models in one process starves
the box's other tenants (observed 2026-11-06: 70-210 GB bursts killed a sibling
scoring lane). Children read through open_init() so each check decodes ONE era
group, and memory is bounded even for pathological stores. Exit 0 = passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ewb_live.stores import mlwp_common as C  # noqa: E402
from ewb_live.stores import mlwp_icechunk as M  # noqa: E402

VARS_2D = ["t2", "msl"]


def _value_check(model: str, init_iso: str, repo: str,
                 bbox: tuple[tuple[float, float], tuple[float, float]]) -> int:
    """Child mode: one init, store (via open_init) vs direct NetCDF. Prints one
    machine-parsable RESULT line; exit 0 = bit-identical, 3 = mismatch,
    4 = no bucket object, 5 = store read failed."""
    lat, lon = bbox
    ts = pd.Timestamp(init_iso)
    if ts is pd.NaT:
        print("RESULT ERROR bad init_iso")
        return 5
    init = ts.to_pydatetime()
    try:
        t = time.time()
        ds = M.open_init(model, init, path=repo)
        opens = time.time() - t
        vs = [v for v in VARS_2D if v in ds]
        leads = [pd.Timedelta(hours=h) for h in (0, 6, 12)]
        t = time.time()
        sub = ds[vs].sel(init_time=init, lead_time=leads,
                         latitude=slice(*lat), longitude=slice(*lon)).compute()
        read_s = time.time() - t
    except Exception as e:  # noqa: BLE001
        print(f"RESULT ERROR {type(e).__name__}: {e}")
        return 5
    fn = f"{model}_{init:%Y%m%d%H}_f000_f240_06.nc"
    import s3fs

    fs = s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": C.REGION})
    try:
        with fs.open(C.key_for(model, init.date(), fn)) as f:
            with xr.open_dataset(f) as raw:
                vts = [np.datetime64(init, "ns") + np.timedelta64(h, "h")
                       for h in (0, 6, 12)]
                ref = {v: raw[v].sel(time=vts, latitude=slice(*lat),
                                     longitude=slice(*lon)).values for v in vs}
    except FileNotFoundError:
        print("RESULT NOBUCKET")
        return 4
    diffs = {v: float(np.nanmax(np.abs(sub[v].values - ref[v]))) for v in vs}
    bad = any(d != 0.0 for d in diffs.values())
    print(f"RESULT OK read={read_s:.2f} open={opens:.2f} "
          f"group={ds.attrs.get('init_group')} vars={vs} maxdiff={diffs}")
    return 3 if bad else 0


def _spawn_check(model: str, init, repo: str, bbox, mem_gb: float) -> tuple[int, str]:
    """Run _value_check in a scoped child process; returns (exit, last RESULT)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--value-check",
           model, init.isoformat(), "--repo", repo,
           "--bbox", ",".join(str(x) for pair in bbox for x in pair)]
    outer = (["systemd-run", "--quiet", "--scope",
              f"-pMemoryMax={int(mem_gb)}G", "-pMemorySwapMax=0"]
             if mem_gb > 0 else [])
    try:
        p = subprocess.run(outer + cmd, capture_output=True, text=True, timeout=1800)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if outer and isinstance(e, FileNotFoundError):  # systemd absent: uncapped
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        else:
            return 5, f"{type(e).__name__}: {e}"
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    tail = lines[-1] if lines else p.stdout.strip().rsplit("\n", 1)[-1]
    return p.returncode, tail or f"(no output, exit {p.returncode})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=M.DEFAULT_REPO)
    ap.add_argument("--days", type=int, default=None,
                    help="restrict the bucket comparison to a day-window "
                         "(default: full archive)")
    ap.add_argument("--bbox", default="40,30,230,245")
    ap.add_argument("--no-values", action="store_true",
                    help="skip the value-equality reads (coverage/size only)")
    ap.add_argument("--mem-gb", type=float, default=20.0,
                    help="per-check child memory cap GB (systemd scope; 0=uncapped)")
    ap.add_argument("--value-check", nargs=2, metavar=("MODEL", "INIT_ISO"),
                    default=None, help=argparse.SUPPRESS)  # child mode
    args = ap.parse_args()
    n1, s1, e1, e2 = (float(x) for x in args.bbox.split(","))
    bbox = ((n1, s1), (e1, e2))

    if args.value_check:
        return _value_check(args.value_check[0], args.value_check[1],
                            args.repo, bbox)

    fails = 0
    inv = (C.inventory_all() if args.days is None
           else C.inventory(args.days))
    models = M.list_models(path=args.repo)
    n_bucket = sum(len(v) for m in inv.values() for v in m.values())
    scope = "full archive" if args.days is None else f"{args.days}d window"
    print(f"repo {args.repo}: {len(models)} store models, "
          f"{n_bucket} bucket inits ({scope})")

    import icechunk

    repo = icechunk.Repository.open(M.repo_storage(args.repo), M.repo_config(),
                                    M.repo_credentials())
    snap = repo.lookup_branch("main")
    manifests = list(repo.list_manifest_files(snap))
    extra = ""
    if M.repo_is_local(args.repo):
        root = Path(args.repo)
        files = [p for p in root.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        man_bytes = sum(p.stat().st_size for p in (root / "manifests").glob("*"))
        extra = (f", repo {total/1e6:.1f} MB in {len(files)} objects "
                 f"(manifests {man_bytes/1e6:.1f} MB)")
    print(f"icechunk: branch main snapshot {str(snap)[:12]}, "
          f"manifests={len(manifests)}{extra}")

    known_bad = {e["url"] for e in M.known_bad(path=args.repo)}
    for m in models:
        got = M.list_inits(m, path=args.repo)
        want = [C.parse_filename(fn)[1]
                for _, d, fn in C.iter_files(inv, model=m)
                if C.url_for(m, d, fn) not in known_bad]
        missing = sorted(set(want) - set(got))
        extra_i = sorted(set(got) - set(want))
        groups = M._era_groups(args.repo, m).get(m, [])
        ok = got == sorted(got) and len(got) == len(set(got)) \
            and not extra_i and not missing
        status = "OK " if ok else "BAD"
        years = sorted({t.year for t in got})
        print(f"[{status}] {m:16s} store={len(got):5d} bucket={len(want):5d} "
              f"missing={len(missing)} extra={len(extra_i)} eras={len(groups)} "
              f"range={got[0]:%Y-%m-%d %H}Z..{got[-1]:%Y-%m-%d %H}Z "
              f"years={years[0]}-{years[-1]}" if got else f"{m}: EMPTY")
        fails += 0 if ok else 1
        if missing:
            print(f"        missing examples: {[str(t) for t in missing[:4]]}")

        if args.no_values or not got:
            continue
        for label, init in (("newest", got[-1]), ("oldest", got[0])):
            code, line = _spawn_check(m, init, args.repo, bbox, args.mem_gb)
            if code == 4:
                print(f"        {label} init {init:%Y-%m-%d %H}Z: no bucket object "
                      f"(skip value check)")
                continue
            if code == 0:
                detail = line.removeprefix("RESULT OK ")
            elif code == 3:
                detail = line.removeprefix("RESULT OK ") + "  <-- MISMATCH"
                fails += 1
            else:
                detail = f"child failed (exit {code}): {line}"
                fails += 1
            print(f"        {label:6s} {init:%Y-%m-%d %H}Z {detail}")

    print("PASS" if fails == 0 else f"FAIL ({fails} problems)")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
