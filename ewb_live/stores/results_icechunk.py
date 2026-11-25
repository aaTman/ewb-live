"""Versioned results store (Icechunk): one transactional commit per scored event.

Replaces the "dump data/results/*.json at the end of the run" model (SPEC §6).
Design notes live in SPEC §6; the short version:

* Own repo (``EWB_RESULTS_REPO``, default ``data/icechunk-results``), separate from the
  MLWP *reference* store (``stores/mlwp_icechunk``) — this one holds real data.
* Branch ``main`` = what is published. Each weekly run writes to ``run/<week>`` and
  commits once per event (event record, score rows, blobs such as viz.json, gridded
  arrays) in ONE transaction, so a reader never sees half an event. Publishing tags
  the run tip ``<week>`` and fast-forwards ``main`` to it.
* Rolling window: ``prune_window`` drops every event that ended before the window
  start (logical removal, a normal commit). Physical removal is a separate explicit
  step (``maintenance(confirm=True)``): old weekly tags are dropped, snapshots expired
  and unreachable objects garbage-collected, so the repo stays ~ the 90-day window plus
  the retained tags' deltas instead of growing forever. ``confirm`` defaults to False
  (dry run) — the owner's never-delete rule keeps destructive steps opt-in.
* Single writer: the pipeline's collector process commits; workers never touch the
  repo. A flock guards against a second writer (local-filesystem repos are not safe
  for concurrent commits).

Layout::

    /                         attrs: schema, window{start,end,days}, run{...}
    /events/<id>/             attrs: event (Event dict), status {model: outcome}, start, end
        scores/<col>          1-D arrays (model, metric, lead_time_hours, value, init_time)
        blobs/<name>          uint8 1-D (e.g. viz.json bytes)
        grids/<name>          n-D arrays (masks, gridded fields)
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = 1
PUBLISHED = "main"
WINDOW_DAYS = 90
KEEP_TAG_WEEKS = int(os.environ.get("EWB_RESULTS_KEEP_TAG_WEEKS", "12"))
SNAPSHOT_KEEP_DAYS = int(os.environ.get("EWB_RESULTS_SNAPSHOT_KEEP_DAYS", "14"))
_SCORE_COLS = ("model", "metric", "lead_time_hours", "value", "init_time")
_DTYPES = {"model": str, "metric": str, "lead_time_hours": "int32",
           "value": "float64", "init_time": "datetime64[s]"}
_GRID_CHUNK = 256


def default_repo_path() -> str:
    root = Path(__file__).resolve().parent.parent.parent
    return os.environ.get("EWB_RESULTS_REPO", str(root / "data" / "icechunk-results"))


@dataclass(frozen=True)
class Run:
    """Identity of one weekly run; stamped on every commit it makes."""
    week: str                       # ISO year-week, e.g. "2026-W48"
    run_id: str                     # unique per attempt (resumes reuse it)
    window_start: str               # YYYY-MM-DD, inclusive
    window_end: str
    rule_rev: str = ""              # TRACK_RULE_REV of the heat/freeze/cold tracker
    code_sha: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def branch(self) -> str:
        return f"run/{self.week}"

    def meta(self, **kw) -> dict:
        return {"week": self.week, "run_id": self.run_id, "rule_rev": self.rule_rev,
                "code_sha": self.code_sha, **kw}


# ----------------------------------------------------------------------------- repo
def open_repo(path: str | None = None, *, create: bool = False):
    """Open the repo; ``create=True`` initialises a new one (refuses a non-empty dir)."""
    import icechunk
    import zarr

    path = path or default_repo_path()
    if path.startswith(("s3://", "gs://")):
        raise NotImplementedError("results store is local-filesystem only (phase 1)")
    storage = icechunk.local_filesystem_storage(path=path)
    if not create:
        return icechunk.Repository.open(storage)
    if os.path.isdir(path) and os.listdir(path):
        raise FileExistsError(f"refusing to overwrite existing repo at {path!r}")
    repo = icechunk.Repository.create(storage)
    s = repo.writable_session(PUBLISHED)
    zarr.open_group(s.store, mode="a").attrs.update({"schema": SCHEMA})
    s.commit("init: results store")
    return repo


def open_or_create(path: str | None = None):
    path = path or default_repo_path()
    exists = os.path.isdir(path) and bool(os.listdir(path))
    return open_repo(path, create=not exists)


@contextlib.contextmanager
def write_lock(path: str | None = None, *, block: bool = False):
    """Exclusive single-writer lock (flock on ``<repo>.lock``)."""
    lock = Path((path or default_repo_path()).rstrip("/") + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError(f"another results-store writer holds {lock}") from None
        yield


def _ro(repo, ref: str):
    """Read-only session for a branch, tag or snapshot id."""
    if ref in repo.list_branches():
        return repo.readonly_session(branch=ref)
    if ref in repo.list_tags():
        return repo.readonly_session(tag=ref)
    return repo.readonly_session(snapshot_id=ref)


# ------------------------------------------------------------------------ run + write
def begin_run(repo, run: Run) -> str:
    """Create (or resume) the run branch off the published tip; returns its name.

    Idempotent: resuming a half-finished run keeps the events it already committed.
    """
    import zarr

    if run.branch not in repo.list_branches():
        repo.create_branch(run.branch, repo.lookup_branch(PUBLISHED))
    s = repo.writable_session(run.branch)
    attrs = zarr.open_group(s.store, mode="r+").attrs
    new = {"window": {"start": run.window_start, "end": run.window_end,
                      "days": WINDOW_DAYS}, "run": asdict(run)}
    if {k: attrs.get(k) for k in new} != new:
        attrs.update(new)
        s.commit(f"begin run {run.run_id}", metadata=run.meta(kind="begin"))
    return run.branch


def _put(group, name: str, arr, *, dtype=None, chunks=None):
    arr = np.asarray(arr)
    if dtype is not None:
        arr = arr.astype(object if dtype is str else dtype)
    shape = arr.shape
    ch = chunks or tuple(max(1, min(n, _GRID_CHUNK * 64 if len(shape) == 1 else _GRID_CHUNK))
                         for n in shape)
    out = group.create_array(name, shape=shape, dtype=dtype or arr.dtype, chunks=ch)
    if arr.size:
        out[...] = arr


def sha_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def commit_event(repo, run: Run, event: dict, scores: pd.DataFrame, *,
                 status: dict | None = None, blobs: dict[str, bytes] | None = None,
                 grids: dict[str, np.ndarray] | None = None, sha: str = "") -> str:
    """Replace the event's whole subtree in ONE commit on the run branch.

    An event that is rescored or extended simply overwrites itself; readers see the
    old version until the commit lands and the new one after, never a mixture.
    ``sha`` is the caller's content hash of (event, scores, status); ``content_shas``
    returns it so a re-sync can skip events that did not change. Returns the snapshot id.
    """
    import zarr

    eid = str(event["id"])
    s = repo.writable_session(run.branch)
    root = zarr.open_group(s.store, mode="r+")
    path = f"events/{eid}"
    if path in root:
        del root[path]
    g = root.require_group(path)
    g.attrs.update({"event": json.loads(json.dumps(event, default=str)),
                    "status": status or {}, "start": str(event.get("start", "")),
                    "end": str(event.get("end", "")),
                    "event_type": str(event.get("event_type", "")),
                    "content_sha": sha,
                    "blob_sha": {n: sha_bytes(b) for n, b in (blobs or {}).items()}})
    sg = g.require_group("scores")
    for col in _SCORE_COLS:
        vals = scores[col].to_numpy() if col in scores else np.array([])
        if col == "init_time":
            vals = pd.to_datetime(vals).to_numpy().astype("datetime64[s]")
        _put(sg, col, vals, dtype=_DTYPES[col])
    bg = g.require_group("blobs")
    for name, data in (blobs or {}).items():
        _put(bg, name, np.frombuffer(data, dtype="uint8"))
    gg = g.require_group("grids")
    for name, arr in (grids or {}).items():
        _put(gg, name, arr)
    return s.commit(f"event {eid}: {len(scores)} rows",
                    metadata=run.meta(kind="event", event_id=eid,
                                      event_type=g.attrs["event_type"], rows=len(scores)))


def _parse_date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def prune_window(repo, run: Run, window_start: dt.date | str | None = None) -> list[str]:
    """Logically drop events that ended before the window start (one commit).

    Events with an unparseable end are kept — never drop what cannot be dated.
    """
    import zarr

    cutoff = _parse_date(window_start) if isinstance(window_start, str) else window_start
    cutoff = cutoff or _parse_date(run.window_start)
    s = repo.writable_session(run.branch)
    root = zarr.open_group(s.store, mode="r+")
    if "events" not in root:
        return []
    gone = []
    for eid, g in list(root["events"].groups()):
        end = _parse_date(g.attrs.get("end", ""))
        if end is not None and end < cutoff:
            gone.append(eid)
    for eid in gone:
        del root["events"][eid]
    if gone:
        s.commit(f"prune {len(gone)} events ended before {cutoff}",
                 metadata=run.meta(kind="prune", removed=len(gone)))
    return sorted(gone)


def content_shas(repo, ref: str) -> dict[str, dict]:
    """{event_id: {"sha": content hash, "blobs": {name: sha}}} at ``ref``."""
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    if "events" not in root:
        return {}
    return {eid: {"sha": g.attrs.get("content_sha", ""),
                  "blobs": dict(g.attrs.get("blob_sha", {}))}
            for eid, g in root["events"].groups()}


def retain_only(repo, run: Run, ids) -> list[str]:
    """Drop run-branch events not in ``ids`` (selection churn) in ONE commit."""
    import zarr

    keep = {str(i) for i in ids}
    s = repo.writable_session(run.branch)
    root = zarr.open_group(s.store, mode="r+")
    if "events" not in root:
        return []
    gone = sorted(eid for eid, _g in root["events"].groups() if eid not in keep)
    for eid in gone:
        del root["events"][eid]
    if gone:
        s.commit(f"retain selection: drop {len(gone)} events",
                 metadata=run.meta(kind="retain", removed=len(gone)))
    return gone


def attach_blobs(repo, run: Run, per_event: dict[str, dict[str, bytes]]) -> int:
    """Add/replace blobs on EXISTING events in ONE commit; unchanged blobs are skipped.

    Returns the number of blobs written. Events not on the branch are ignored (their
    scores have not been committed yet; the next attach picks them up).
    """
    import zarr

    s = repo.writable_session(run.branch)
    root = zarr.open_group(s.store, mode="r+")
    n = 0
    for eid, blobs in per_event.items():
        path = f"events/{eid}"
        if path not in root:
            continue
        g = root[path]
        have = dict(g.attrs.get("blob_sha", {}))
        bg = g.require_group("blobs")
        for name, data in blobs.items():
            h = sha_bytes(data)
            if have.get(name) == h:
                continue
            if name in bg:
                del bg[name]
            _put(bg, name, np.frombuffer(data, dtype="uint8"))
            have[name] = h
            n += 1
        g.attrs["blob_sha"] = have
    if n:
        s.commit(f"attach {n} blobs", metadata=run.meta(kind="attach", blobs=n))
    return n


def put_docs(repo, run: Run, docs: dict[str, bytes]) -> int:
    """Store run-level documents (``/docs/<name>``: meta, scorecard, ...) in ONE commit."""
    import zarr

    s = repo.writable_session(run.branch)
    root = zarr.open_group(s.store, mode="r+")
    dg = root.require_group("docs")
    have = dict(dg.attrs.get("sha", {}))
    n = 0
    for name, data in docs.items():
        h = sha_bytes(data)
        if have.get(name) == h:
            continue
        if name in dg:
            del dg[name]
        _put(dg, name, np.frombuffer(data, dtype="uint8"))
        have[name] = h
        n += 1
    dg.attrs["sha"] = have
    if n:
        s.commit(f"docs: {n} updated", metadata=run.meta(kind="docs", docs=n))
    return n


def read_doc(repo, name: str, ref: str = PUBLISHED) -> bytes:
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    return bytes(root[f"docs/{name}"][:].tobytes())


def publish(repo, run: Run) -> str:
    """Tag the run tip ``<week>``, fast-forward ``main`` to it, retire the run branch.

    Returns the tip. Re-running after the branch is gone is a no-op when the tag exists.
    """
    if run.branch not in repo.list_branches():
        if run.week in repo.list_tags():
            return repo.lookup_tag(run.week)
        raise RuntimeError(f"no run branch {run.branch!r} to publish")
    tip = repo.lookup_branch(run.branch)
    tags = repo.list_tags()
    if run.week in tags:
        if repo.lookup_tag(run.week) != tip:
            raise RuntimeError(f"tag {run.week} already published at a different snapshot")
    else:
        repo.create_tag(run.week, tip)
    repo.reset_branch(PUBLISHED, tip)
    # the tag now pins this snapshot; a lingering run branch would pin it forever
    # (branch tips are never expired) and defeat the retention bound
    repo.delete_branch(run.branch)
    return tip


# ------------------------------------------------------------------------------ read
def list_events(repo, ref: str = PUBLISHED) -> list[dict]:
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    if "events" not in root:
        return []
    return [{**dict(g.attrs["event"]), "status": dict(g.attrs.get("status", {}))}
            for _, g in root["events"].groups()]


def read_scores(repo, ref: str = PUBLISHED, event_ids: list[str] | None = None) -> pd.DataFrame:
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    frames = []
    if "events" in root:
        for eid, g in root["events"].groups():
            if event_ids is not None and eid not in event_ids:
                continue
            sg = g["scores"]
            df = pd.DataFrame({c: sg[c][:] for c in _SCORE_COLS})
            df.insert(0, "event_id", eid)
            df.insert(1, "event_type", g.attrs.get("event_type", ""))
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["event_id", "event_type", *_SCORE_COLS])


def read_blob(repo, event_id: str, name: str, ref: str = PUBLISHED) -> bytes:
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    return bytes(root[f"events/{event_id}/blobs/{name}"][:].tobytes())


def read_grid(repo, event_id: str, name: str, ref: str = PUBLISHED) -> np.ndarray:
    import zarr

    root = zarr.open_group(_ro(repo, ref).store, mode="r")
    return root[f"events/{event_id}/grids/{name}"][...]


def progress(repo, run: Run) -> dict:
    """Events committed on the run branch so far (for the 'in progress' banner)."""
    return {"week": run.week, "events": len(list_events(repo, run.branch)),
            "tip": repo.lookup_branch(run.branch)}


# ------------------------------------------------------------------------ maintenance
def maintenance(repo, *, keep_tag_weeks: int = KEEP_TAG_WEEKS,
                snapshot_keep_days: int = SNAPSHOT_KEEP_DAYS, confirm: bool = False) -> dict:
    """Bound the repo size. DRY RUN unless ``confirm=True``.

    Drops weekly tags older than the newest ``keep_tag_weeks`` (a tag pins its whole
    snapshot, including events long since pruned from the window) and stale unpublished
    run branches (same reason), then expires
    snapshots older than ``snapshot_keep_days`` and garbage-collects what nothing
    references any more. Branch tips and kept tags are never expired.
    """
    week_tags = sorted(t for t in repo.list_tags() if len(t) == 8 and t[4:6] == "-W")
    stale = week_tags[:-keep_tag_weeks] if keep_tag_weeks else week_tags
    kept = week_tags[len(stale):]
    # unpublished (failed/abandoned) run branches older than the oldest kept week would
    # pin their snapshots forever; with no kept tag nothing is old enough to retire
    stale_branches = sorted(b for b in repo.list_branches()
                            if b.startswith("run/") and kept and b[4:] < kept[0])
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=snapshot_keep_days)
    report = {"confirm": confirm, "tags_dropped": stale, "tags_kept": kept,
              "run_branches_dropped": stale_branches,
              "snapshot_cutoff": cutoff.isoformat(), "expired_snapshots": None,
              "gc_objects_deleted": None, "bytes_deleted": None}
    if not confirm:
        return report
    for t in stale:
        repo.delete_tag(t)
    for b in stale_branches:
        repo.delete_branch(b)
    report["expired_snapshots"] = len(repo.expire_snapshots(cutoff))
    gc = repo.garbage_collect(cutoff)
    report["gc_objects_deleted"] = int(getattr(gc, "objects_deleted", -1))
    report["bytes_deleted"] = int(getattr(gc, "bytes_deleted", -1))
    return report


def repo_bytes(path: str | None = None) -> int:
    return sum(p.stat().st_size for p in Path(path or default_repo_path()).rglob("*") if p.is_file())


# ----------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ewb_live.stores.results_icechunk")
    ap.add_argument("--repo", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="branches, tags, event counts, size on disk")
    m = sub.add_parser("maintenance", help="drop old tags, expire snapshots, GC (dry run by default)")
    m.add_argument("--confirm", action="store_true", help="actually delete (owner-approved only)")
    m.add_argument("--keep-tag-weeks", type=int, default=KEEP_TAG_WEEKS)
    m.add_argument("--snapshot-keep-days", type=int, default=SNAPSHOT_KEEP_DAYS)
    a = ap.parse_args(argv)
    repo = open_repo(a.repo)
    if a.cmd == "status":
        out = {"branches": sorted(repo.list_branches()), "tags": sorted(repo.list_tags()),
               "published_events": len(list_events(repo)),
               "bytes": repo_bytes(a.repo)}
    else:
        with write_lock(a.repo):
            out = maintenance(repo, keep_tag_weeks=a.keep_tag_weeks,
                              snapshot_keep_days=a.snapshot_keep_days, confirm=a.confirm)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
