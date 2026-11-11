"""Virtual Icechunk store over the CIRA/NOAA MLWP archive (s3://noaa-oar-mlwp-data).

Design (see notes/mlwp-store.md for the survey that drove it):

* VirtualiZarr 2.8 + Icechunk 2.3 + zarr-python 3.4. **No data is copied**: every data
  variable is a Zarr v3 array whose chunks are Icechunk *virtual references* (byte ranges)
  into the original NetCDF4/HDF5 objects in ``s3://noaa-oar-mlwp-data`` (anonymous S3,
  registered as a trusted VirtualChunkContainer). The store therefore keeps the FULL
  archive (all model dirs back to 2020-09); there is no rolling window and nothing is
  ever evicted from the store (download caches in ewb_live/sources may still evict).
* Layout: one Zarr group per model directory (``AURO_v100_GFS`` ... == the names EWB
  uses in ``inputs.CIRA_MODEL_NAMES``). Native variable names are kept verbatim.
  Dims are ``(init_time, lead_time, level?, latitude, longitude)``: the file's ``time``
  (valid times) is converted to ``lead_time`` timedelta64[s] (0..240 h step 6 h) and
  becomes the dimension, ``init_time`` is materialised as a real datetime64 coordinate
  (one value per source file), and a correct 2-D ``valid_time`` coordinate
  ``(init_time, lead_time)`` is stored as small real (non-virtual) chunks.
* Chunk layout: store chunks are exactly the source HDF5 chunk shapes, which are
  per-(lead, level) slabs — a virtual ref must be one contiguous byte range, so it can
  only ever point at one compressed HDF5 chunk. When the source layout changes over
  time (upstream EWB hits this on 2025-05-27 and simply drops the newer data), a new
  *era group* is started (``<model>__YYYYMMDD``) and ``open_model()`` presents a single
  logical view by lazily concatenating era groups along ``init_time``.
* Incremental: ``update`` appends only inits missing from the store, using
  ``virtualizarr ... to_icechunk(mode="a", append_dim="init_time")``, and makes exactly
  one Icechunk commit per run (idempotent: a second run with no new data commits
  nothing). Manifest splitting along ``init_time`` keeps that commit cost proportional
  to the new inits instead of the whole archive. A non-blocking flock serialises
  concurrent runs (daily timer + weekly catch-up); a stall watchdog rebuilds a wedged
  reference-generation pool; ``maintenance`` expires old snapshots and garbage-collects
  committed metadata so a long-lived repo does not grow without bound.
* Safety: virtualizarr <= 2.8 is known to mis-offset virtual refs when a compressed
  PARTIAL EDGE CHUNK shares a zarr grid cell (legacy /root/mlwp-icechunk CHANGELOG
  2026-10-27; e.g. the retired FOUR_v100_GFS). Era groups whose source arrays have
  partial edge chunks are only built after ``probe_edge_chunk_equivalence`` proves a
  byte-exact round-trip of one edge chunk through a scratch store; a failed probe
  refuses the group instead of writing corrupt refs.

CLI (all heavy commands are host-guarded to the ewb LXC, see ewb_live/_guard.py):
    python -m ewb_live.stores.mlwp_icechunk build      [--days N]        # fresh build (full archive)
    python -m ewb_live.stores.mlwp_icechunk update     [--days N]        # daily append, idempotent
    python -m ewb_live.stores.mlwp_icechunk backfill   [--batch 750] [--swap]
    python -m ewb_live.stores.mlwp_icechunk maintenance
    python -m ewb_live.stores.mlwp_icechunk probe-edge --model FOUR_v100_GFS --date 2021-06-15

Python:
    from ewb_live.stores.mlwp_icechunk import list_models, list_inits, open_model, known_bad
    ds = open_model("GRAP_v100_IFS")            # lazy xr.Dataset (dask-backed)
    sub = ds[["t2", "msl"]].sel(init_time=..., lead_time=[0, 6, 12])
    known_bad("GRAP_v100_IFS")                  # upstream-corrupt objects missing here

Storage: ``EWB_MLWP_REPO`` (or ``--repo``) may be a local path, ``s3://bucket/prefix``
or ``gs://bucket/prefix``; object-store repos take credentials from the environment.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import xarray as xr

from . import mlwp_common as C

DEFAULT_REPO = os.environ.get("EWB_MLWP_REPO", "data/icechunk/cira_mlwp")
BRANCH = "main"

#: coords read eagerly per file (tiny); 'time' is read to build lead_time. The
#: ``*level`` names are the FOUR_v100_GFS level dims; they MUST be loaded because
#: xarray materialises every 1-D coord when it builds indexes (a virtual coord
#: raises NotImplementedError there). Unknown names are tolerated by virtualizarr.
LOADABLE = ["time", "latitude", "longitude", "level",
            "zlevel", "tlevel", "windlevel", "rhlevel"]

#: manifest split sizes along init_time: 3-D fields get a finer split than 2-D ones so
#: that every manifest holds >=1000 refs (icechunk trains its URL dictionary at that
#: point) while a subset read only has to fetch the split covering its init.
SPLIT_3D_INITS = 20
SPLIT_2D_INITS = 96
#: variable names that carry a level dimension in this archive
NAME_3D_RE = "^(t|q|u|v|z|w|r)$"

#: no result from the reference pool for this many seconds -> kill + rebuild it
#: (legacy lesson: an unattended S3 hang can wedge a worker forever)
STALL_S = int(os.environ.get("EWB_MLWP_STALL_S", "240"))
POOL_MAX_REBUILDS = 2

#: snapshot expire + garbage-collect cadence for the long-lived repo
SNAPSHOT_RETENTION_DAYS = int(os.environ.get("EWB_MLWP_KEEP_SNAPSHOTS_DAYS", "14"))
MAINTENANCE_EVERY_DAYS = 7
LAST_MAINTENANCE_KEY = "last_maintenance"

#: files per model per backfill commit (bounds memory and makes the run resumable)
BACKFILL_BATCH = 750

#: virtualizarr releases with the reported partial-edge-chunk mis-offset bug
VZ_EDGE_BUG_MAX = (2, 8, 0)

_W: dict = {}
#: layout_signature -> probe passed, per process (edge-chunk gate results)
_EDGE_OK: dict[str, dict] = {}


# ----------------------------------------------------------------------------- bucket
def _registry():
    if "registry" not in _W:
        import obstore as obs
        from obspec_utils.registry import ObjectStoreRegistry

        store = obs.store.from_url(C.BUCKET_URI, region=C.REGION, skip_signature=True)
        _W["registry"] = ObjectStoreRegistry({C.BUCKET_URI: store})
    return _W["registry"]


def _vz_version() -> tuple[int, ...]:
    import virtualizarr

    parts = re.match(r"(\d+)\.(\d+)\.(\d+)", virtualizarr.__version__)
    return tuple(int(p) for p in parts.groups()) if parts else (0, 0, 0)


def layout_signature(vds: xr.Dataset) -> str:
    """Stable hash of the per-variable chunk layout + dtype of one source file,
    plus the 1-D coordinate shapes and values (a level-value change at unchanged
    variable shape must start a new era group, not join the old one)."""
    import hashlib as _h

    h = _h.sha256()
    for name in sorted(vds.data_vars.keys(), key=str):
        da = vds[name]
        ma = da.data
        if hasattr(ma, "metadata"):
            md = ma.metadata
            h.update(repr((name, tuple(md.shape), str(md.dtype),
                           tuple(md.chunks))).encode())
        else:
            h.update(repr((name, tuple(da.shape), str(da.dtype), None)).encode())
    for name in sorted(vds.coords.keys()):
        da = vds[name]
        if name == "init_time":
            continue  # per-file by construction; would make every file its own era
        if da.ndim == 1 and da.size <= 4096:
            vals = np.ascontiguousarray(np.asarray(da.values))
            h.update(repr((("coord", name), vals.dtype.str)).encode())
            h.update(vals.tobytes())
    return h.hexdigest()[:12]


def virtual_init(url: str, init: dt.datetime) -> xr.Dataset:
    """One source file -> one virtual Dataset with an init_time axis of length 1."""
    import virtualizarr as vz
    from virtualizarr.manifests import ManifestArray
    from virtualizarr.parsers import HDFParser

    vds = vz.open_virtual_dataset(
        url,
        registry=_registry(),
        parser=HDFParser(),
        loadable_variables=LOADABLE,
        decode_times=True,
    )
    virt = [c for c in vds.coords
            if isinstance(vds[c].data, ManifestArray)]
    if virt:
        # xarray would materialise these virtual coords on the first index build
        raise ValueError(f"virtual 1-D coords {virt} in {url.rsplit('/', 1)[-1]}; "
                         f"extend LOADABLE (mlwp_icechunk.py)")
    init64 = np.datetime64(init.replace(tzinfo=None), "ns")
    leads = (np.asarray(vds["time"].values) - init64) / np.timedelta64(1, "h")
    vds = vds.rename({"time": "valid_time"})
    vds = vds.expand_dims(init_time=np.array([init64], dtype="datetime64[ns]"))
    vds = vds.assign_coords(lead_time=("valid_time", leads.astype("timedelta64[h]")))
    vds = vds.swap_dims({"valid_time": "lead_time"})
    # valid_time differs per init, so it cannot stay a 1-D coord on lead_time: drop it
    # here and rebuild the correct 2-D version after concatenation.
    vds = vds.drop_vars(["valid_time"])
    vds.attrs = {
        k: str(v)
        for k, v in vds.attrs.items()
        if k
        in (
            "Conventions",
            "version",
            "model_name",
            "initialization_model",
            "initialization_time",
            "first_forecast_hour",
            "last_forecast_hour",
            "forecast_hour_step",
            "creation_time",
        )
    }
    vds.attrs["cira_object"] = url
    vds.attrs["layout_signature"] = layout_signature(vds)
    return vds


def _worker(args):
    url, init = args
    try:
        return url, init, virtual_init(url, init), None
    except Exception as e:  # noqa: BLE001 - per-file failure must not kill the run
        return url, init, None, f"{type(e).__name__}: {e}"


class _Pool:
    """ProcessPoolExecutor with a stall watchdog.

    Unattended runs against S3 occasionally wedge a worker (hung range request);
    the legacy store's answer was a 240 s stall detector with pool rebuilds, which
    we adopt: no finished result for ``stall_s`` seconds -> cancel the pending
    futures, throw the pool away, resubmit the unfinished jobs. A worker stuck
    inside the killed pool is leaked (its pending future never resolves) but the
    run survives; systemd TimeoutStartSec is the hard backstop."""

    def __init__(self, workers: int, stall_s: int = STALL_S,
                 max_rebuilds: int = POOL_MAX_REBUILDS):
        self.workers = workers
        self.stall_s = stall_s
        self.max_rebuilds = max_rebuilds
        self.rebuilds = 0
        self.ex = ProcessPoolExecutor(max_workers=workers)

    def results(self, jobs: list):
        pending_jobs = list(jobs)
        while pending_jobs:
            fut2job = {self.ex.submit(_worker, j): j for j in pending_jobs}
            pending = set(fut2job)
            last = time.monotonic()
            stalled = False
            while pending:
                done, pending = wait(pending, timeout=15,
                                     return_when=FIRST_COMPLETED)
                if done:
                    last = time.monotonic()
                    for f in done:
                        yield f.result()
                elif time.monotonic() - last > self.stall_s:
                    stalled = True
                    break
            if not stalled:
                return
            self.rebuilds += 1
            for f in pending:
                f.cancel()
            self.ex.shutdown(wait=False, cancel_futures=True)
            print(f"  pool stalled {self.stall_s}s with {len(pending)} files "
                  f"unfinished; rebuild {self.rebuilds}/{self.max_rebuilds}",
                  file=sys.stderr, flush=True)
            if self.rebuilds > self.max_rebuilds:
                raise TimeoutError(
                    f"reference pool stalled {self.max_rebuilds + 1} times "
                    f"(S3 unreachable?) — aborting the run")
            self.ex = ProcessPoolExecutor(max_workers=self.workers)
            pending_jobs = [fut2job[f] for f in pending]

    def shutdown(self):
        self.ex.shutdown(wait=False)


def _add_valid_time(ds: xr.Dataset) -> xr.Dataset:
    it = np.asarray(ds["init_time"].values, dtype="datetime64[ns]")
    lt = np.asarray(ds["lead_time"].values, dtype="timedelta64[h]").astype("timedelta64[ns]")
    return ds.assign_coords(
        valid_time=(("init_time", "lead_time"), it[:, None] + lt[None, :])
    )


def _model_files(inv: dict, model: str, days: int | None = None) -> list[tuple[dt.date, str, dt.datetime]]:
    out = []
    for m, d, fn in C.iter_files(inv, model=model):
        _, init, _ = C.parse_filename(fn)
        out.append((d, fn, init))
    return out


# ------------------------------------------------------------------------------ repo
def repo_storage(path: str = DEFAULT_REPO):
    """Icechunk Storage for a repo path: local dir, ``s3://bucket/prefix`` or
    ``gs://bucket/prefix`` (object-store repos authenticate from the environment)."""
    import icechunk

    if path.startswith("s3://"):
        bucket, _, prefix = path[len("s3://"):].partition("/")
        return icechunk.s3_storage(bucket=bucket, prefix=prefix or None,
                                   region=os.environ.get("EWB_ICECHUNK_REGION",
                                                         C.REGION))
    if path.startswith("gs://"):
        bucket, _, prefix = path[len("gs://"):].partition("/")
        return icechunk.gcs_storage(bucket=bucket, prefix=prefix or None)
    return icechunk.local_filesystem_storage(path=path)


def repo_is_local(path: str = DEFAULT_REPO) -> bool:
    return not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path)


def repo_exists(path: str = DEFAULT_REPO) -> bool:
    """True if an Icechunk repository is initialised at ``path``."""
    import icechunk

    try:
        return bool(icechunk.Repository.exists(repo_storage(path)))
    except Exception:  # noqa: BLE001 - unreadable path counts as absent
        return False


def _lock_path(repo: str) -> Path:
    """flock file serialising writers of ``repo`` (never inside the repo dir)."""
    if repo_is_local(repo):
        return Path(str(repo) + ".lock")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", repo)[:80]
    return Path(C.CACHE_DIR) / f"repo_{safe}.lock"


@contextlib.contextmanager
def update_lock(repo: str, block: bool = False):
    """Non-blocking exclusive flock for one repo; yields False if busy (a second
    writer — e.g. the weekly catch-up while the daily timer is running — walks
    away instead of racing the first)."""
    lp = _lock_path(repo)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ------------------------------------------------------- known-bad ledger
#: root-group attr holding objects that failed to virtualise (upstream-truncated
#: NetCDF objects). Planning skips these, otherwise permanently unreadable files look
#: like interior gaps and every update would rewrite whole era groups forever without
#: ever converging. Entries are retried after ``RETRY_AFTER_DAYS`` in case upstream
#: repairs an object.
KNOWN_BAD_KEY = "known_bad"
RETRY_AFTER_DAYS = 14


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _known_bad_of(session) -> dict[str, dict]:
    """The ledger as committed on the session's current snapshot."""
    import zarr

    try:
        root = zarr.open_group(session.store, path="/", mode="r")
        raw = root.attrs.get(KNOWN_BAD_KEY, {})
        if not isinstance(raw, dict):
            return {}
        return {str(k): (dict(v) if isinstance(v, dict) else {})
                for k, v in raw.items()}
    except Exception:  # noqa: BLE001 - no root attrs yet (empty repo)
        return {}


def known_bad(model: str | None = None, path: str = DEFAULT_REPO) -> list[dict]:
    """Objects the store could not virtualise (truncated upstream), with reasons.

    These inits are absent from ``list_inits`` by design; the ledger says why.
    """
    import zarr

    repo = open_repo(path)
    session = repo.readonly_session(branch=BRANCH)
    root = zarr.open_group(session.store, path="/", mode="r")
    out = [{"url": u, **meta}
           for u, meta in sorted(_known_bad_of(session).items())]
    if model:
        out = [e for e in out if f"/{model}/" in e["url"]]
    return out


def _merge_known_bad(ledger: dict[str, dict], skipped: list[dict],
                     succeeded: set[str], now: str | None = None) -> bool:
    """Merge a run's failures/successes into ``ledger`` in place; True if it changed.

    Skipped wins over succeeded: a file can parse fine and still be REFUSED by the
    edge-chunk gate — it belongs in the ledger so planning retries it after
    ``RETRY_AFTER_DAYS`` instead of re-probing it on every daily update.
    """
    now = now or _now_iso()
    changed = False
    for url in succeeded:
        if ledger.pop(url, None) is not None:  # upstream repaired the object
            changed = True
    for rec in skipped:
        url = rec["url"]
        prev = ledger.get(url)
        entry = {"error": rec["error"],
                 "first_seen": (prev or {}).get("first_seen", now),
                 "last_checked": now}
        if prev != entry:
            ledger[url] = entry
            changed = True
    return changed


def _record_known_bad(session, ledger: dict[str, dict], skipped: list[dict],
                      succeeded: set[str]) -> bool:
    """Merge into the ledger and persist it to the root-group attrs if changed."""
    if not _merge_known_bad(ledger, skipped, succeeded):
        return False
    import zarr

    root = zarr.open_group(session.store, path="/", mode="r+")
    root.attrs[KNOWN_BAD_KEY] = ledger
    return True


def repo_config():
    import icechunk

    cfg = icechunk.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            f"{C.BUCKET_URI}/",
            icechunk.s3_store(region=C.REGION, anonymous=True),
        )
    )
    cfg.manifest = icechunk.ManifestConfig(
        splitting=icechunk.ManifestSplittingConfig.from_dict(
            {
                icechunk.ManifestSplitCondition.name_matches(NAME_3D_RE): {
                    icechunk.ManifestSplitDimCondition.DimensionName("init_time"):
                        SPLIT_3D_INITS
                },
                icechunk.ManifestSplitCondition.AnyArray(): {
                    icechunk.ManifestSplitDimCondition.DimensionName("init_time"):
                        SPLIT_2D_INITS
                },
            }
        )
    )
    return cfg


def repo_credentials():
    import icechunk

    return icechunk.containers_credentials(
        {f"{C.BUCKET_URI}/": icechunk.s3_credentials(anonymous=True)}
    )


def open_repo(path: str = DEFAULT_REPO, *, create: bool = False):
    """Open (or create) the store repository."""
    import icechunk

    storage = repo_storage(path)
    config = repo_config()
    creds = repo_credentials()
    if create:
        if repo_is_local(path) and os.path.isdir(path) and os.listdir(path):
            raise FileExistsError(f"refusing to overwrite existing repo at {path!r}")
        repo = icechunk.Repository.create(storage, config, creds)
        if BRANCH not in repo.list_branches():
            repo.create_branch(BRANCH)
        # commit root attrs immediately: a freshly created repo has no committed
        # root group, and zarr refuses to open_group(mode="r") on it (backfill's
        # first resume check hits this)
        import zarr

        init = repo.writable_session(BRANCH)
        zarr.open_group(store=init.store, mode="a").attrs.update(_group_attrs())
        init.commit("init: repository root attrs")
        return repo
    return icechunk.Repository.open(storage, config, creds)


def _root_group(store):
    """Zarr root group, tolerating a just-created repo whose root was never
    committed (zarr raises GroupNotFoundError there; semantically it is empty)."""
    import zarr.errors

    try:
        return zarr.open_group(store=store, mode="r")
    except zarr.errors.GroupNotFoundError:
        return _EmptyRoot()


class _EmptyRoot:
    def group_keys(self):
        return ()

    def __getitem__(self, k):  # pragma: no cover - defensive
        raise KeyError(k)


# ---------------------------------------------------------------- edge-chunk safety
EDGE_PROBE_KEY = "edge_chunk_probe"


def partial_edge_chunks(vds: xr.Dataset) -> dict[str, list[tuple[str, int, int]]]:
    """Data vars whose zarr grid has a PARTIAL edge chunk: {var: [(dim, extent,
    chunk_size), ...]}. These are the arrays the virtualizarr <=2.8 mis-offset bug
    targets (compressed partial chunks sharing a grid cell), so they must pass the
    round-trip probe before being written."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for name, da in vds.data_vars.items():
        md = getattr(da.data, "metadata", None)
        if md is None:
            continue
        bad = [(str(dim), int(shape), int(ch))
               for dim, shape, ch in zip(da.dims, md.shape, md.chunks) if shape % ch]
        if bad:
            out[str(name)] = bad
    return out


def probe_edge_chunk_equivalence(url: str, init: dt.datetime,
                                 scratch: str | None = None) -> dict:
    """Byte-exactness proof for one file's PARTIAL edge chunks.

    Virtualises the file into a throwaway Icechunk repo, reads back the trailing
    partial chunk of the riskiest variable, and compares it to the direct NetCDF
    read of exactly that region. Returns a record dict with ``ok`` — never raises
    for a genuine mismatch (raises only when the probe itself cannot run)."""
    import s3fs

    rec: dict = {"url": url, "init": str(init),
                 "virtualizarr": ".".join(map(str, _vz_version())), "ok": False}
    vds = virtual_init(url, init)
    risky = partial_edge_chunks(vds)
    rec["partial_edge_vars"] = {k: v for k, v in risky.items()}
    if not risky:
        rec["ok"] = True
        rec["detail"] = "no partial edge chunks"
        return rec

    # riskiest = the risky var with the most partial dims, then the biggest array
    var = max(risky, key=lambda v: (len(risky[v]), float(np.prod(vds[v].shape))))
    dims = list(vds[var].dims)
    md = vds[var].data.metadata
    # the partial dim with the biggest remainder drives the selection
    dim, extent, chunk = max((d, e, c) for d, e, c in risky[var] if e % c)
    start = extent - (extent % chunk)          # start of the trailing partial chunk
    idx = {}
    for d in dims:
        pos = dims.index(d)
        extent_d, chunk_d = int(md.shape[pos]), int(md.chunks[pos])
        if d == dim:
            idx[d] = slice(start, extent)      # exactly one partial chunk
        elif extent_d % chunk_d:
            idx[d] = slice(0, extent_d - (extent_d % chunk_d))  # full chunks only
        else:
            idx[d] = slice(0, chunk_d)         # first chunk only (cheap)
    tmp = scratch or tempfile.mkdtemp(prefix="edge_probe_")
    try:
        repo = open_repo(tmp, create=True)
        session = repo.writable_session(BRANCH)
        vds[[var]].vz.to_icechunk(session.store, group="probe", mode="w-",
                                  validate_containers=True)
        session.commit("edge-chunk probe")
        ro = repo.readonly_session(branch=BRANCH)
        store_idx = {d: s for d, s in idx.items() if d != "init_time"}
        got = (xr.open_zarr(ro.store, group="probe", consolidated=False,
                            decode_times=True)[var]
               .isel(store_idx).isel(init_time=0).compute())
        # direct NetCDF read of the same region: map our dim names back to the file's
        dimmap = {d: ("time" if d == "lead_time" else d) for d in dims}
        raw_idx = {dimmap[d]: s for d, s in store_idx.items()}
        fs = s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": C.REGION})
        key = url.split("://", 1)[1]  # s3fs keys keep the bucket, drop only the scheme
        with fs.open(key) as f:
            with xr.open_dataset(f, engine="h5netcdf", decode_times=True) as raw:
                ref = raw[var].isel(raw_idx).values
        same = np.array_equal(np.asarray(got.values), ref, equal_nan=True)
        rec["ok"] = bool(same) and bool(np.asarray(got.values).size)
        rec["detail"] = (f"{var} {tuple(int(v) for v in got.shape)} "
                         f"edge-chunk round-trip {'exact' if same else 'MISMATCH'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rec


def _edge_gate(vds: xr.Dataset, url: str, init: dt.datetime,
               verbose: bool = True) -> tuple[bool, str]:
    """Decide whether an era (layout_signature) may be built. Files without partial
    edge chunks pass unconditionally (live models: store chunks tile the grid
    exactly). Risky eras are probed once per virtualizarr version and cached on the
    repo root attrs (see sync/backfill for persistence)."""
    risky = partial_edge_chunks(vds)
    if not risky:
        return True, ""
    sig = vds.attrs["layout_signature"]
    ver = ".".join(map(str, _vz_version()))
    key = f"{ver}:{sig}"
    if key in _EDGE_OK:
        rec = _EDGE_OK[key]
    else:
        rec = probe_edge_chunk_equivalence(url, init)
        if rec.get("ok"):
            _EDGE_OK[key] = rec
    if verbose:
        print(f"  edge-chunk probe sig={sig} vz={ver}: {rec.get('detail', rec)}",
              file=sys.stderr, flush=True)
    if rec.get("ok"):
        return True, ""
    if _vz_version() <= VZ_EDGE_BUG_MAX:
        why = ("virtualizarr <= 2.8 partial-edge-chunk mis-offset hazard: probe "
               "FAILED, refusing to build this era")
    else:
        why = "edge-chunk round-trip probe FAILED, refusing to build this era"
    return False, f"{why} ({rec.get('detail', 'probe error')})"


# ------------------------------------------------------------------------------- API
def list_models(path: str = DEFAULT_REPO) -> list[str]:
    """Model keys in the store (era suffixes stripped; e.g. ``GRAP_v100_IFS``)."""
    import zarr

    repo = open_repo(path)
    session = repo.readonly_session(branch=BRANCH)
    root = _root_group(session.store)
    out = {re.sub(r"__\d{8}(\d{2})?$", "", g) for g in root.group_keys()}
    return sorted(out)


def _era_groups(path: str = DEFAULT_REPO, model: str | None = None) -> dict[str, list[str]]:
    import zarr

    repo = open_repo(path)
    session = repo.readonly_session(branch=BRANCH)
    root = _root_group(session.store)
    groups = sorted(root.group_keys())
    if model:
        groups = [g for g in groups if g == model or g.startswith(model + "__")]
    out: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        out[re.sub(r"__\d{8}(\d{2})?$", "", g)].append(g)
    for m in out:
        out[m].sort()
    return dict(out)


def _group_inits(store, group: str) -> np.ndarray:
    """init_time of one era group as datetime64[ns].

    Must be decoded through xarray: the zarr array is CF-encoded and the reference
    unit/offset are chosen per write (e.g. 'hours since 2026-11-02'), so raw int
    reinterpretation is wrong."""
    import xarray as xr

    ds = xr.open_zarr(store, group=group, consolidated=False, chunks=None,
                      decode_times=True)
    return np.asarray(ds["init_time"].values, dtype="datetime64[ns]")


def list_inits(
    model: str,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    path: str = DEFAULT_REPO,
) -> list[dt.datetime]:
    """Inits present in the store for a model (all eras), optionally windowed."""
    repo = open_repo(path)
    session = repo.readonly_session(branch=BRANCH)
    out: list[dt.datetime] = []
    for g in _era_groups(path, model).get(model, []):
        for v in _group_inits(session.store, g):
            out.append(
                dt.datetime.fromtimestamp(
                    int(np.datetime64(v, "s").astype("int64")), dt.timezone.utc
                ).replace(tzinfo=None)
            )
    out = sorted(set(out))
    if start:
        out = [t for t in out if t >= start]
    if end:
        out = [t for t in out if t <= end]
    return out


def open_model(
    model: str,
    path: str = DEFAULT_REPO,
    chunks: dict | str | None = "auto",
    branch: str = BRANCH,
) -> xr.Dataset:
    """Single logical view of a model: lazy Dataset over all its era groups.

    Chunks map 1:1 onto source HDF5 chunks, so a subset read (one init, a few leads,
    a bbox) fetches only the byte ranges it needs from S3.

    NOTE (memory): building the lazy view decodes every era group's chunk
    manifests — measured ~8 GB RSS / ~115 s for the largest model (4.8k inits).
    For single-init reads prefer `open_init()` (one era group only) or run
    callers under a memory cap; do not loop open_model over models in one
    process on a shared box.
    """
    repo = open_repo(path)
    session = repo.readonly_session(branch=branch)
    groups = _era_groups(path, model).get(model)
    if not groups:
        raise KeyError(f"model {model!r} not in store at {path!r}")
    dss = [
        xr.open_zarr(session.store, group=g, consolidated=False,
                     chunks=chunks, decode_times=True)
        for g in groups
    ]
    ds = dss[0] if len(dss) == 1 else xr.concat(
        dss, dim="init_time", coords="minimal", compat="override",
        combine_attrs="drop_conflicts",
    )
    ds.attrs.setdefault("model", model)
    ds.attrs["era_groups"] = list(groups)
    ds.attrs["source_bucket"] = C.BUCKET_URI
    return ds


def open_init(
    model: str,
    init: dt.datetime,
    path: str = DEFAULT_REPO,
    chunks: dict | Literal["auto"] | None = "auto",
    branch: str = BRANCH,
) -> xr.Dataset:
    """Lazy view of ONE init, decoded from the single era group that holds it.

    open_model() decodes every era group's chunk manifests to build the full model
    view (~8 GB RSS / ~115 s for a 4.8k-init model); this reads one group's
    ``init_time`` coord first (~40 MB) and opens only the group that contains
    ``init``, so a per-init dashboard/scoring read stays small. Returns the whole
    group view — slice ``.sel(init_time=init)`` (the sibling sources layer does) —
    with ``init_group`` in attrs for provenance.

    Raises KeyError if the model is absent or the init is not in the store.
    """
    repo = open_repo(path)
    session = repo.readonly_session(branch=branch)
    groups = _era_groups(path, model).get(model)
    if not groups:
        raise KeyError(f"model {model!r} not in store at {path!r}")
    target = init.replace(tzinfo=None) if init.tzinfo else init
    wanted = np.datetime64(target, "ns")
    found: str | None = None
    for g in groups:
        inits = _group_inits(session.store, g)
        if inits.size and (wanted >= inits.min()) and (wanted <= inits.max()):
            found = g
            break
    if found is None:
        raise KeyError(f"init {init:%Y-%m-%d %H:%M} not in store for model {model!r}")
    ds = xr.open_zarr(session.store, group=found, consolidated=False,
                      chunks=chunks, decode_times=True)
    if found != model:  # era groups carry the model name in attrs
        ds.attrs.setdefault("model", model)
    ds.attrs["init_group"] = found
    ds.attrs["source_bucket"] = C.BUCKET_URI
    return ds


# -------------------------------------------------------------------------- builder
def _combine(vds_list: list[xr.Dataset]) -> xr.Dataset:
    ds = xr.combine_nested(
        vds_list,
        concat_dim="init_time",
        coords="minimal",
        compat="override",
        combine_attrs="drop_conflicts",
        join="override",
    )
    ds = ds.transpose("init_time", "lead_time", ...)
    return _add_valid_time(ds)


def _group_attrs() -> dict:
    return {
        "store": "cira_mlwp_icechunk",
        "source_bucket": C.BUCKET_URI,
        "built_with": _version_string(),
        "dims": "init_time, lead_time, level?, latitude, longitude",
    }


def _version_string() -> str:
    import icechunk
    import virtualizarr
    import zarr

    return f"virtualizarr {virtualizarr.__version__}, icechunk {icechunk.__version__}, zarr {zarr.__version__}"


def _planning_inventory(days: int | None, end: dt.date | None, refresh: bool):
    """Bucket listing for one plan: a day-window (tests, targeted rebuilds) or the
    FULL archive (default: the store keeps everything, per the product decision)."""
    if days is None:
        return C.inventory_all(end=end, refresh=refresh)
    return C.inventory(days, end=end, refresh=refresh)


def sync(
    days: int | None = None,
    models: Iterable[str] | None = None,
    workers: int = 8,
    path: str = DEFAULT_REPO,
    fresh: bool = False,
    dry_run: bool = False,
    commit_message: str | None = None,
    end: dt.date | None = None,
    verbose: bool = True,
    inv: dict | None = None,
) -> dict:
    """Build or incrementally append the store. One commit per run; idempotent.

    fresh=True creates the repository (``build``); otherwise appends the inits that are
    missing from the window (``update``). ``days=None`` (default) plans against the
    full archive; ``end`` pins the window's last day (default: today). Returns a
    per-model summary.
    """
    t0 = time.time()
    if inv is None:
        inv = _planning_inventory(days, end, refresh=True)
    wanted = sorted(models) if models else sorted(inv)
    summary: dict = {"repo": path, "days": days, "models": {},
                     "skipped": [], "refused": []}

    if not fresh and not repo_exists(path):
        raise FileNotFoundError(
            f"no Icechunk repo at {path!r} -- run build first "
            "(or call update(), which self-heals via a staged build)")
    repo = open_repo(path, create=fresh)
    session = repo.writable_session(BRANCH)

    # -- plan per model: build / append / rewrite ----------------------------------
    kinds: dict[str, str] = {}
    jobs: list[tuple[str, tuple[str, dt.datetime]]] = []
    ledger = {} if fresh else _known_bad_of(session)
    retry_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RETRY_AFTER_DAYS)

    def _bad_known(model: str, day: dt.date, fn: str) -> bool:
        """True if this object failed recently and should not be re-planned."""
        entry = ledger.get(C.url_for(model, day, fn))
        if not entry:
            return False
        try:
            return dt.datetime.fromisoformat(entry["last_checked"]) > retry_cut
        except (KeyError, ValueError):  # malformed entry -> retry
            return False

    for m in wanted:
        files = _model_files(inv, m)
        if not files:
            summary["models"][m] = {"status": "no data in window"}
            continue
        have = set() if fresh else set(list_inits(m, path=path))
        skipped_known = sum(1 for f in files
                            if f[2] not in have and _bad_known(m, f[0], f[1]))
        missing = [f for f in files
                   if f[2] not in have and not _bad_known(m, f[0], f[1])]
        if not have:
            kind, todo = "build", files
        elif not missing:
            if skipped_known:
                summary["models"][m] = {"status": "up to date", "new": 0,
                                        "known_bad_skipped": skipped_known}
            else:
                summary["models"][m] = {"status": "up to date", "new": 0}
            continue
        elif all(f[2] > max(have) for f in missing):
            kind, todo = "append", missing  # safe: init_time only grows at the end
        else:
            kind, todo = "rewrite", files  # interior gaps: rewrite era groups in place
        kinds[m] = kind
        summary["models"][m] = {"kind": kind, "pending": len(todo)}
        if skipped_known:
            summary["models"][m]["known_bad_skipped"] = skipped_known
        for d, fn, init in todo:
            jobs.append((m, (C.url_for(m, d, fn), init)))

    if verbose:
        print(f"{len(jobs)} files to virtualise across {len(kinds)} models "
              f"({workers} workers)", file=sys.stderr, flush=True)
    if dry_run or not jobs:
        summary["planned_files"] = len(jobs)
        summary["parse_errors"] = 0
        summary["commit"] = None
        summary["elapsed_s"] = round(time.time() - t0, 1)
        return summary

    jobs_by_model: dict[str, list[tuple[str, dt.datetime]]] = defaultdict(list)
    for m, job in jobs:
        jobs_by_model[m].append(job)

    import zarr

    errs = 0
    succeeded: set[str] = set()
    pool = _Pool(workers)
    try:
        for model in sorted(jobs_by_model):
            # parse this model's files (parallel), write, then free: bounded memory
            items: list[tuple[dt.datetime, xr.Dataset]] = []
            for url, init, vds, err in pool.results(jobs_by_model[model]):
                if err or vds is None:
                    errs += 1
                    summary["skipped"].append({"url": url, "error": err})
                    if verbose:
                        print(f"  skip {url.rsplit('/',1)[1]}: {err[:120]}",
                              file=sys.stderr, flush=True)
                    continue
                assert vds is not None
                succeeded.add(url)
                items.append((init, vds))
            items.sort(key=lambda kv: kv[0])
            groups = [] if fresh else list(_era_groups(path, model).get(model, []))
            # bucket the new files into eras by layout signature (contiguous runs)
            eras: list[tuple[str, list[tuple[dt.datetime, xr.Dataset]]]] = []
            for init, vds in items:
                sig = vds.attrs["layout_signature"]
                if eras and eras[-1][0] == sig:
                    eras[-1][1].append((init, vds))
                else:
                    eras.append((sig, [(init, vds)]))
            rec = summary["models"].setdefault(model, {})
            rec.setdefault("groups", {})
            rec["kind"] = "build" if fresh else kinds.get(model, "append")
            for sig, chunk in eras:
                # edge-chunk safety gate for THIS era (virtualizarr <= 2.8 hazard)
                ok, why = _edge_gate(chunk[0][1],
                                     chunk[0][1].attrs.get("cira_object", jobs_by_model[model][0][0]),
                                     chunk[0][0])
                if not ok:
                    recs = [{"url": v.attrs.get("cira_object"), "error": why}
                            for _, v in chunk]
                    summary["refused"].extend(recs)
                    # also ledger them: planning skips known-bad, so a refused era
                    # is retried after RETRY_AFTER_DAYS instead of every run
                    summary["skipped"].extend(recs)
                    summary["models"].setdefault(model, {})["status"] = \
                        "refused: edge-chunk probe failed"
                    if verbose:
                        print(f"  REFUSED {model} era {sig}: {why}",
                              file=sys.stderr, flush=True)
                    continue
                kind = "build" if fresh else kinds.get(model, "append")
                target, mode = _target_group(session, model, groups, sig, chunk[0][0], kind)
                if target is None:
                    summary["skipped"].extend(
                        {"url": v.attrs.get("cira_object"),
                         "error": f"layout change inside an older era of {model}: "
                                  "manual review"}
                        for _, v in chunk
                    )
                    continue
                ds = _combine([v for _, v in chunk])
                ds.vz.to_icechunk(
                    session.store, group=target, mode=mode,
                    append_dim="init_time" if mode == "a" else None,
                    validate_containers=True,
                )
                if mode != "a":
                    grp = zarr.open_group(session.store, path=target, mode="a")
                    grp.attrs.update(_group_attrs())
                    grp.attrs["layout_signature"] = sig
                    grp.attrs["era_start"] = chunk[0][0].isoformat()
                    grp.attrs["era_end"] = chunk[-1][0].isoformat()
                    if target not in groups:
                        groups.append(target)
                rec["groups"][target] = {
                    "inits_added": len(chunk),
                    "first": chunk[0][0].isoformat(),
                    "last": chunk[-1][0].isoformat(),
                    "mode": mode,
                }
                rec["refs_added"] = rec.get("refs_added", 0) + int(ds.vz.nrefs())
                if verbose:
                    print(f"  wrote {target}: {len(chunk)} inits "
                          f"({chunk[0][0]:%Y-%m-%d}..{chunk[-1][0]:%Y-%m-%d}) "
                          f"mode={mode}", file=sys.stderr, flush=True)
                del ds
            del items
    finally:
        pool.shutdown()
    summary["parse_errors"] = errs
    if not dry_run:
        n_new = sum(g["inits_added"] for r in summary["models"].values()
                    for g in r.get("groups", {}).values())
        ledger_changed = _record_known_bad(session, ledger, summary["skipped"],
                                           succeeded)
        summary["known_bad_total"] = len(ledger)
        _record_edge_probes(session)
        msg = commit_message or (
            f"{'build' if fresh else 'update'}: {n_new} inits, {errs} parse errors"
        )
        if n_new or fresh or ledger_changed:
            if session.has_uncommitted_changes:
                summary["commit"] = session.commit(msg)
            else:
                summary["commit"] = None
        else:
            summary["commit"] = None
            session.discard_changes()
    summary["elapsed_s"] = round(time.time() - t0, 1)
    if verbose:
        print(f"done in {summary['elapsed_s']}s; commit={summary.get('commit')}",
              file=sys.stderr)
    return summary


def _record_edge_probes(session) -> None:
    """Persist this run's edge-chunk probe results on the root group (audit trail
    for the virtualizarr mis-offset gate)."""
    if not _EDGE_OK:
        return
    import zarr

    try:
        root = zarr.open_group(session.store, path="/", mode="r+")
        root.attrs[EDGE_PROBE_KEY] = {
            k: {kk: str(vv) for kk, vv in v.items() if kk != "partial_edge_vars"}
            for k, v in _EDGE_OK.items()}
    except Exception:  # noqa: BLE001 - audit attr only; never fail a run
        pass


def _group_sig(session, group: str) -> str | None:
    import zarr

    try:
        return zarr.open_group(session.store, path=group, mode="r").attrs.get(
            "layout_signature"
        )
    except Exception:  # noqa: BLE001 - group missing
        return None


def _target_group(session, model: str, groups: list[str], sig: str,
                  first_init: dt.datetime, kind: str) -> tuple[str | None, str | None]:
    """Choose (group, zarr write mode) for a batch of files with layout ``sig``.

    Convention: the plain ``<model>`` group holds the first era; a layout change starts
    ``<model>__YYYYMMDD``. Reading is unaffected (open_model concatenates all eras).
    ``(None, None)`` = no safe home for this batch (layout reverted inside the window).
    """
    if not groups:
        return model, "w-"
    sigs = {g: _group_sig(session, g) for g in groups}
    if kind == "rewrite":
        for g in groups:
            if sigs[g] == sig:
                return g, "w"
        return f"{model}__{first_init:%Y%m%d%H}", "w-"
    # append: only ever extend the newest era, or start a newer dated era
    last = groups[-1]
    if sigs[last] == sig:
        return last, "a"
    inits = _group_inits(session.store, last)
    if inits.size == 0 or np.datetime64(first_init, "ns") > inits.max():
        return f"{model}__{first_init:%Y%m%d%H}", "w-"
    return None, None


def build(days: int | None = None, models=None, workers: int = 8, path: str = DEFAULT_REPO,
          dry_run: bool = False, end: dt.date | None = None,
          verbose: bool = True, commit_message: str | None = None) -> dict:
    """Fresh build (full archive unless ``days`` is given), staged at ``<path>.tmp``
    and moved into place after the commit.

    Readers pointed at ``path`` either see nothing (no repo) or the finished store,
    never a half-written root — a full build takes hours, and an empty root group
    mid-build would surface as ``GroupNotFoundError`` for consumers (sources absorbs
    that, but the atomic move removes the window entirely). Incremental ``update``
    needs no such staging: Icechunk commits are snapshot-atomic, so readers keep
    seeing the previous snapshot until the new one lands.
    """
    final = Path(path)
    if not dry_run and repo_exists(path):
        raise FileExistsError(f"refusing to overwrite existing repo at {str(final)!r}")
    tmp = final.with_name(final.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)  # stale staging dir from an interrupted run
    s = sync(days=days, models=models, workers=workers, path=str(tmp), fresh=True,
             dry_run=dry_run, end=end, verbose=verbose)
    if dry_run:
        shutil.rmtree(tmp, ignore_errors=True)
    elif s.get("commit"):
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, final)  # atomic on the same filesystem
        s["repo"] = str(final)
    return s


def update(days: int | None = None, **kw) -> dict:
    """Append inits missing from the store (full archive by default); one commit;
    no-op when already current. Idempotent and lock-guarded, so the daily timer and
    the weekly catch-up can both call it freely.

    Self-heals: if the repository does not exist yet (fresh checkout), run a full
    staged build instead of failing the cycle. Returns
    ``{"status": "update in progress elsewhere", ...}`` (commit=None, exit ok) when
    another writer holds the repo lock."""
    path = kw.get("path", DEFAULT_REPO)
    with update_lock(path) as got:
        if not got:
            if kw.get("verbose", True):
                print("update: another store update holds the lock; nothing to do",
                      file=sys.stderr, flush=True)
            return {"repo": path, "days": days, "status": "locked",
                    "commit": None, "models": {}, "skipped": [], "refused": [],
                    "elapsed_s": 0.0}
        if not repo_exists(path):
            if kw.get("verbose", True):
                print(f"repo {path} missing; update runs a full build instead",
                      file=sys.stderr)
            s = build(days, **kw)
            s["self_healed_build"] = True
            return s
        s = sync(days=days, fresh=False, **kw)
    # periodic hygiene for the long-lived repo (only when we committed something)
    if s.get("commit") and maintenance_due(path):
        try:
            s["maintenance"] = maintenance(path)
        except Exception as e:  # noqa: BLE001 - hygiene must not fail the update
            s["maintenance"] = {"error": f"{type(e).__name__}: {e}"}
    _record_metrics(path, s)
    return s


#: env override for the update-metrics log location (portability rule)
METRICS_ENV = "EWB_STORE_METRICS"


def _record_metrics(path: str, s: dict) -> None:
    """Append one JSONL row per completed update (time per daily update, new inits,
    refusals, maintenance outcome) — the ops audit trail for the store. Next to the
    repo when the repo path is local, else ./data/; EWB_STORE_METRICS overrides."""
    import json
    import os

    default = (str(Path(path).with_name("store_metrics.jsonl"))
               if repo_is_local(path) else "data/store_metrics.jsonl")
    dest = os.environ.get(METRICS_ENV, default)
    try:
        new_inits = sum(g.get("inits_added", 0)
                        for m in s.get("models", {}).values()
                        for g in m.get("groups", {}).values())
        row = {"ts": _now_iso(), "repo": str(path),
               "status": s.get("status", "ok"), "commit": s.get("commit"),
               "elapsed_s": s.get("elapsed_s"), "new_inits": new_inits,
               "parse_errors": s.get("parse_errors", 0),
               "refused": len(s.get("refused", [])),
               "maintenance": s.get("maintenance")}
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:  # metrics must never fail an update
        pass


# -------------------------------------------------------------------------- backfill
def backfill(models: Iterable[str] | None = None, workers: int = 16,
             batch: int = BACKFILL_BATCH, path: str = DEFAULT_REPO,
             swap: bool = False, days: int | None = None,
             end: dt.date | None = None, verbose: bool = True) -> dict:
    """Reference-generate the FULL archive into ``<path>.backfill`` in batched
    commits, resumable at any point, then (with ``swap=True``) move it into place.

    The operational store (``path``) keeps serving readers from its current snapshot
    the whole time; the swap is two atomic renames under the repo lock, and the old
    repo is left at ``<path>.old`` for the operator to delete. Resume semantics: the
    staging repo's own content IS the checkpoint — rerun the same command after a
    crash, already-ingested inits are skipped. Each commit is one ``batch``-sized
    slice of one model (oldest inits first), so memory stays flat and an interrupted
    run loses at most one batch."""
    t0 = time.time()
    staging = str(path) + ".backfill"
    if not repo_is_local(staging) or not repo_is_local(path):
        raise NotImplementedError("backfill/swap currently require local repo paths")
    with update_lock(path, block=False) as got:
        if not got:
            raise TimeoutError("backfill: another store update holds the repo lock")
        inv = _planning_inventory(days, end, refresh=True)
        wanted = sorted(models) if models else sorted(inv)
        if not repo_exists(staging):
            open_repo(staging, create=True)
            if verbose:
                print(f"backfill: created staging repo {staging}", file=sys.stderr,
                      flush=True)
        totals = {m: sum(len(f) for f in inv.get(m, {}).values()) for m in wanted}
        grand = sum(totals.values())
        summary = {"staging": staging, "models": {}, "planned_files": grand,
                   "skipped": [], "refused": []}
        pool = _Pool(workers)
        try:
            for m in wanted:
                done_m = 0
                while True:
                    st = _backfill_batch(m, inv, staging, pool, workers, batch,
                                         verbose)
                    summary["models"][m] = st["summary"]
                    done_m = st["done_after"]
                    if st["done_after"] >= st["planned"] or st["batch_files"] == 0:
                        break
                summary["models"][m]["planned"] = totals[m]
                if verbose:
                    print(f"backfill {m}: {done_m}/{totals[m]} inits done",
                          file=sys.stderr, flush=True)
        finally:
            pool.shutdown()
        if swap:
            summary["swap"] = promote(staging, path)
        summary["elapsed_s"] = round(time.time() - t0, 1)
    return summary


def _backfill_batch(model: str, inv: dict, staging: str, pool: _Pool,
                    workers: int, batch: int, verbose: bool) -> dict:
    """One resumable unit: commit <= ``batch`` oldest missing files of ``model``."""
    import zarr

    t0 = time.time()
    files = _model_files(inv, model)          # sorted by init
    planned = len(files)
    have = set(list_inits(model, path=staging))
    repo = open_repo(staging)
    session = repo.writable_session(BRANCH)
    ledger = _known_bad_of(session)
    retry_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RETRY_AFTER_DAYS)

    def _bad(day: dt.date, fn: str) -> bool:
        entry = ledger.get(C.url_for(model, day, fn))
        if not entry:
            return False
        try:
            return dt.datetime.fromisoformat(entry["last_checked"]) > retry_cut
        except (KeyError, ValueError):
            return False

    missing = [(C.url_for(model, d, fn), init, d)
               for d, fn, init in files
               if init not in have and not _bad(d, fn)]
    todo = missing[:batch]
    srec: dict = {"done_after": len(have), "planned": planned,
                  "batch_files": len(todo)}
    if not todo:
        session.discard_changes()
        return {"summary": srec, "done_after": len(have), "planned": planned,
                "batch_files": 0}

    items: list[tuple[dt.datetime, xr.Dataset]] = []
    skipped: list[dict] = []
    succeeded: set[str] = set()
    for url, init, vds, err in pool.results([(u, i) for u, i, _ in todo]):
        if err:
            skipped.append({"url": url, "error": err})
            continue
        succeeded.add(url)
        items.append((init, vds))
    items.sort(key=lambda kv: kv[0])

    refused = ""
    if items:
        groups = list(_era_groups(staging, model).get(model, []))
        eras: list[tuple[str, list[tuple[dt.datetime, xr.Dataset]]]] = []
        for init, vds in items:
            sig = vds.attrs["layout_signature"]
            if eras and eras[-1][0] == sig:
                eras[-1][1].append((init, vds))
            else:
                eras.append((sig, [(init, vds)]))
        nrefs = 0
        for sig, chunk in eras:
            ok, why = _edge_gate(chunk[0][1],
                                 chunk[0][1].attrs.get("cira_object", todo[0][0]),
                                 chunk[0][0])
            if not ok:  # refuse this era only; healthy eras still build
                refused = f"era {sig}: {why}"
                skipped.extend({"url": v.attrs.get("cira_object"), "error": refused}
                               for _, v in chunk)
                continue
            # backfill only ever extends forward (oldest-first ordering guarantees
            # missing inits are newer than everything staged for this model)
            target, mode = _target_group(session, model, groups, sig, chunk[0][0],
                                         "append")
            if target is None:  # interior gap (e.g. a known-bad gap at the start)
                skipped.extend({"url": v.attrs.get("cira_object"),
                                "error": "cannot append interior batch"} for _, v in chunk)
                continue
            ds = _combine([v for _, v in chunk])
            ds.vz.to_icechunk(session.store, group=target, mode=mode,
                              append_dim="init_time" if mode == "a" else None,
                              validate_containers=True)
            nrefs += int(ds.vz.nrefs())
            if mode != "a":
                grp = zarr.open_group(session.store, path=target, mode="a")
                grp.attrs.update(_group_attrs())
                grp.attrs["layout_signature"] = sig
                grp.attrs["era_start"] = chunk[0][0].isoformat()
                grp.attrs["era_end"] = chunk[-1][0].isoformat()
                if target not in groups:
                    groups.append(target)
            srec[f"group {target}"] = {"inits": len(chunk),
                                       "first": chunk[0][0].isoformat(),
                                       "last": chunk[-1][0].isoformat(),
                                       "mode": mode}
            del ds
        srec["refs_added"] = nrefs
    if refused:
        srec["refused"] = refused
    srec["parse_errors"] = len(skipped)
    _record_known_bad(session, ledger, skipped, succeeded)
    _record_edge_probes(session)
    if session.has_uncommitted_changes:
        srec["commit"] = session.commit(
            f"backfill {model}: {len(items)} inits ({todo[0][1]:%Y-%m-%d}..)")
    else:
        session.discard_changes()
        srec["commit"] = None
    srec["elapsed_s"] = round(time.time() - t0, 1)
    srec["done_after"] += len(items) + len(skipped)  # skipped counted as processed
    if verbose:
        print(f"backfill {model}: commit batch of {len(items)} files "
              f"(skip {len(skipped)}) -> {len(have) + len(items)}/{planned} "
              f"[{srec['elapsed_s']}s]", file=sys.stderr, flush=True)
    return {"summary": srec, "done_after": srec["done_after"], "planned": planned,
            "batch_files": len(todo)}


def promote(staging: str, path: str) -> dict:
    """Atomically move a finished backfill repo into place (old repo kept at
    ``<path>.old``). Callers hold the repo lock. Readers with an already-open repo
    keep working on the moved-out directory until they reopen."""
    old = str(path) + ".old"
    if not repo_exists(staging):
        raise FileNotFoundError(f"no backfill repo at {staging!r}")
    if os.path.exists(old):
        raise FileExistsError(f"{old!r} exists: delete the previous swap first")
    moved = None
    if repo_exists(path):
        os.replace(path, old)
        moved = old
    os.replace(staging, path)
    return {"repo": str(path), "old_repo": moved,
            "note": "delete the old repo when satisfied"}


# ------------------------------------------------------------------------ maintenance
def _root_attrs(path: str) -> dict:
    import zarr

    repo = open_repo(path)
    session = repo.readonly_session(branch=BRANCH)
    try:
        return dict(zarr.open_group(session.store, path="/", mode="r").attrs)
    except Exception:  # noqa: BLE001 - empty repo
        return {}


def maintenance_due(path: str = DEFAULT_REPO, now: dt.datetime | None = None) -> bool:
    attrs = _root_attrs(path)
    last = attrs.get(LAST_MAINTENANCE_KEY)
    if not last:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(str(last))
    except ValueError:
        return True
    return ((now or dt.datetime.now(dt.timezone.utc)) - last_dt).days >= MAINTENANCE_EVERY_DAYS


def maintenance(path: str = DEFAULT_REPO,
                older_than_days: int = SNAPSHOT_RETENTION_DAYS,
                verbose: bool = True) -> dict:
    """Expire snapshots older than ``older_than_days`` and garbage-collect the
    committed metadata they pinned (manifests only: this store writes ~no data
    objects — the virtual refs point at S3, which GC never touches). Without this
    the repo grows ~MBs per daily commit forever (legacy store lesson)."""
    t0 = time.time()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
    repo = open_repo(path)
    expired = repo.expire_snapshots(cutoff)
    gc = repo.garbage_collect(delete_object_older_than=cutoff)
    with update_lock(path, block=True):
        ws = repo.writable_session(BRANCH)
        import zarr

        root = zarr.open_group(ws.store, path="/", mode="r+")
        root.attrs[LAST_MAINTENANCE_KEY] = _now_iso()
        commit = ws.commit(f"maintenance: expire {len(expired)} snapshots "
                           f"(older_than={older_than_days}d)") if \
            ws.has_uncommitted_changes else None
    out = {"expired_snapshots": len(expired),
           "gc_objects_deleted": int(getattr(gc, "total_objects_deleted", -1)),
           "bytes_deleted": int(getattr(gc, "bytes_deleted", -1)),
           "commit": commit, "elapsed_s": round(time.time() - t0, 1)}
    if verbose:
        print(f"maintenance: expired {out['expired_snapshots']} snapshots, "
              f"gc deleted {out['gc_objects_deleted']} objects "
              f"({out['bytes_deleted']} bytes)", file=sys.stderr, flush=True)
    return out


# ------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    from ewb_live._guard import require_ewb_host  # CLI-only guard (see _guard.py)

    require_ewb_host()
    p = argparse.ArgumentParser(prog="python -m ewb_live.stores.mlwp_icechunk")
    p.add_argument("cmd", choices=["build", "update", "backfill", "maintenance",
                                   "probe-edge"])
    p.add_argument("--days", type=int, default=None,
                   help="limit the plan to a day-window (default: full archive)")
    p.add_argument("--models", nargs="*", default=None)
    p.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 4))
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--end", default=None, help="last day of the window (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch", type=int, default=BACKFILL_BATCH,
                   help="backfill: files per model per commit")
    p.add_argument("--swap", action="store_true",
                   help="backfill: promote the staging repo when complete")
    p.add_argument("--model", default=None, help="probe-edge: model dir")
    p.add_argument("--date", default=None, help="probe-edge: YYYY-MM-DD")
    p.add_argument("--hour", type=int, default=0, help="probe-edge: init hour")
    a = p.parse_args(argv)
    end = dt.date.fromisoformat(a.end) if a.end else None
    if a.cmd == "build":
        s = build(a.days, models=a.models, workers=a.workers, path=a.repo,
                  dry_run=a.dry_run, end=end)
    elif a.cmd == "update":
        s = update(a.days, models=a.models, workers=a.workers, path=a.repo,
                   dry_run=a.dry_run, end=end)
    elif a.cmd == "backfill":
        s = backfill(a.models, workers=a.workers, batch=a.batch, path=a.repo,
                     swap=a.swap, days=a.days, end=end)
    elif a.cmd == "maintenance":
        s = maintenance(a.repo)
    else:  # probe-edge
        if not (a.model and a.date):
            p.error("probe-edge needs --model and --date")
        d = dt.date.fromisoformat(a.date)
        fns = [f for f in C.day_files(a.model, d)
               if C.parse_filename(f)[1].hour == a.hour]
        if not fns:
            print(f"no {a.model} file on {d} at {a.hour:02d}Z", file=sys.stderr)
            return 1
        fn = fns[0]
        rec = probe_edge_chunk_equivalence(C.url_for(a.model, d, fn),
                                           C.parse_filename(fn)[1])
        import json as _json

        print(_json.dumps(rec, indent=1))
        return 0 if rec.get("ok") else 1
    import json

    models = s.get("models", {})
    inits = sum(sum(g.get("inits_added", 0) for g in v.get("groups", {}).values())
                for v in models.values() if isinstance(v, dict))
    refs = sum(v.get("refs_added", 0) for v in models.values() if isinstance(v, dict))
    print(f"{args_days_note(s)}: {inits} inits, {refs} virtual refs, "
          f"commit={s.get('commit')}, {s.get('elapsed_s')}s, "
          f"{len(s.get('skipped', []))} files skipped, "
          f"{len(s.get('refused', []))} refused", file=sys.stderr)
    print(json.dumps(s, indent=1, default=str))
    return 0


def args_days_note(s: dict) -> str:
    days = s.get("days")
    return (f"store {s.get('repo')} "
            f"({'full archive' if days is None else f'{days}d window'})")


if __name__ == "__main__":
    raise SystemExit(main())
