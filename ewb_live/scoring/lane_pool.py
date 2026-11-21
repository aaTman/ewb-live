"""Dedicated worker pool for the track lanes (notes/scoring_design.md R4, owner decision 2026-11-21).

ONE pool, ``EWB_LIVE_SCORING_WORKERS`` spawn workers (default 12), each recycled
after ``recycle`` jobs (default 30: the WP0 pilot measured per-worker RSS growing
slowly with the jobs a worker has run, 3.347 GB after ~110 jobs, 2.433 GB with
recycling every 30, i.e. 29.7 GB projected at 12 workers, under the 40 GB cap).
The ECMWF-direct baseline ``ifs_hres`` stays in the same pool. Workers read with
``stores.mlwp_icechunk.open_init`` only (never ``open_model``).

The unit of work is one ``(event, model, init)`` job (the pilot's unit, so its
numbers carry over); the parent collects the jobs of an ``(event, model)`` pair
and, when the pair is complete, hands it to ``on_pair`` (the pipeline writes the
part file keyed ``(event, model, fingerprint, truth)``, so a rerun resumes).

Stopping: SIGTERM (from ``ops/lane_backfill.sh`` when the weekly run becomes
live, or from ``timeout``) stops submitting, lets the running jobs finish
(workers ignore SIGTERM), keeps every completed pair and returns / exits 0: an
unfinished pair is simply rescored next run. The RSS sampler enforces the cap:
above ``rss_cap_gb`` the pool drains and stops (exit 6).

CLI (R1 guard wraps this: ``ops/lane_backfill.sh tc``)::

    python -m ewb_live.scoring.lane_pool tc [--models a,b] [--workers 12] [--recycle 30]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import logging
import multiprocessing as mp
import os
import signal
import sys
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import flood as flood_lane
from . import tc as tc_lane
from . import track_verif as tv

logger = logging.getLogger("ewb_live.scoring.lane_pool")

DEFAULT_WORKERS = 12
DEFAULT_RECYCLE = 30
DEFAULT_RSS_CAP_GB = 40.0
EXIT_RSS_CAP = 6
EXIT_REFUSED = 3
MAX_POOL_REBUILDS = 2
LIVE_ROOT = Path("/opt/ewb-live")      # the live tree: a backfill must not write under it


@dataclass(frozen=True)
class Lane:
    """What the pool needs to know about a lane (WP2 / WP4 register theirs here)."""
    event_type: str
    truth: str                                   # truth_source tag
    models: Callable[[list], tuple]              # all models -> (scored models, {model: reason unsupported})
    plan: Callable                               # event -> {"track", "verified", "not_verified", "start"}
    init_job: Callable                           # (track, model, init, leads, reader) -> record
    finalize: Callable                           # (event, model, plan, [records]) -> (df|None, status)
    label: str
    # serial in-process scorer (event, models, status_out=None) -> (DataFrame, {model: status});
    # what pipeline._score_job dispatches to for this lane's events
    serial: Callable | None = None


def tc_models(models: list) -> tuple[list, dict]:
    """The TC lane scores the 8 MLWP-store models and the ``ifs_hres`` baseline (D4);
    every other registered model is a documented ``unsupported`` pair."""
    from ..sources import registry

    specs = registry.models()
    ok = [m for m in models if m == tc_lane.BASELINE_MODEL
          or (m in specs and specs[m].backend == "aiwp")]
    why = {m: "tropical_cyclone scores the 8 MLWP-store models and the ifs_hres baseline "
              "(track lane, MSLP only)" for m in models if m not in ok}
    return ok, why


LANES: dict[str, Lane] = {
    "tc": Lane(event_type=tc_lane.EVENT_TYPE, truth=tc_lane.TRUTH_TAG, models=tc_models,
               plan=tc_lane.plan_for, init_job=tc_lane.score_init, finalize=tc_lane.finalize_pair,
               label="tropical cyclone", serial=tc_lane.score_tc_event),
    "flood": Lane(event_type=flood_lane.EVENT_TYPE, truth=flood_lane.TRUTH_TAG,
                  models=flood_lane.flood_models, plan=flood_lane.plan_for,
                  init_job=flood_lane.score_init, finalize=flood_lane.finalize_pair,
                  label="flood", serial=flood_lane.score_flood_event),
}
LANE_BY_EVENT_TYPE = {ln.event_type: ln for ln in LANES.values()}
LANE_TYPES = frozenset(LANE_BY_EVENT_TYPE)


# =============================================================================
# worker side
# =============================================================================
_DASK_SET = False


def _worker_init():
    """Spawned worker: ignore SIGTERM / SIGINT (the parent drains, the guard script
    escalates to SIGKILL), and keep dask's own threading small (12 workers)."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _run_job(job: dict) -> dict:
    """Worker entry for one ``(event, model, init)`` job. Never raises."""
    import resource

    import psutil

    global _DASK_SET
    if not _DASK_SET:
        import dask

        dask.config.set(scheduler="threads", num_workers=int(job.get("dask_threads", 2)))
        logging.getLogger("ewb_live").setLevel(logging.WARNING)
        _DASK_SET = True
    lane = LANES[job["lane"]]
    t0 = time.perf_counter()
    track = job["track"]
    init = dt.datetime.fromisoformat(job["init"])
    rec = lane.init_job(track, job["model"], init, job["leads"])
    rec.update(key=job["key"], pid=os.getpid(), job_s=round(time.perf_counter() - t0, 3),
               rss_gb=round(psutil.Process().memory_info().rss / 1e9, 3),
               rss_gb_peak=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 3))
    return rec


# =============================================================================
# RSS sampler (R4; like pipeline._PeakRss but keeps per-worker peaks)
# =============================================================================
class RssSampler:
    """Samples RSS of this process + children every ``interval`` s; calls
    ``on_over(total_gb)`` once when the pool exceeds ``cap_gb``."""

    def __init__(self, interval: float = 2.0, cap_gb: float | None = None,
                 on_over: Callable[[float], None] | None = None):
        self.interval, self.cap_gb, self.on_over = interval, cap_gb, on_over
        self.peak_total = 0
        self.peak_by_pid: dict[int, int] = {}
        self.timeline: list[tuple[float, float, int]] = []
        self._stop = threading.Event()
        self._t0 = time.time()
        self._fired = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def sample(self) -> int:
        import psutil

        me = psutil.Process()
        total = me.memory_info().rss
        kids = me.children(recursive=True)
        for c in kids:
            try:
                r = c.memory_info().rss
            except psutil.Error:
                continue
            total += r
            self.peak_by_pid[c.pid] = max(self.peak_by_pid.get(c.pid, 0), r)
        self.peak_total = max(self.peak_total, total)
        self.timeline.append((round(time.time() - self._t0, 1), round(total / 1e9, 2), len(kids)))
        if self.cap_gb and total / 1e9 > self.cap_gb and not self._fired and self.on_over:
            self._fired = True
            self.on_over(total / 1e9)
        return total

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except Exception:  # noqa: BLE001 - the monitor must never break the run
                pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self.sample()
        except Exception:  # noqa: BLE001
            pass


# =============================================================================
# parent side
# =============================================================================
def build_jobs(lane_key: str, pairs: list[tuple], dask_threads: int) -> tuple[list[dict], dict]:
    """``(jobs, pending)``: one job per verified init of every ``(event, model)`` pair;
    ``pending[key]`` tracks the pair until all of its jobs are back. Pairs with no
    verifiable init are finalised straight away (returned in ``pending`` with n=0)."""
    lane = LANES[lane_key]
    jobs: list[dict] = []
    pending: dict = {}
    plans: dict = {}
    for e, model in pairs:
        plan = plans.get(e.id)
        if plan is None:
            plan = plans[e.id] = lane.plan(e)
        key = f"{e.id}|{model}"
        pending[key] = {"event": e, "model": model, "plan": plan, "n": len(plan["verified"]), "recs": []}
        for init, leads in plan["verified"]:
            jobs.append({"lane": lane_key, "key": key, "model": model, "init": init.isoformat(),
                         "leads": leads, "track": plan["track"], "dask_threads": dask_threads,
                         "_ifs": model == tc_lane.BASELINE_MODEL})
    # slow ECMWF-direct jobs first (the tail of the run should be fast store jobs), then by
    # event / init so a pair completes (and is persisted) soon after it starts
    jobs.sort(key=lambda j: (not j["_ifs"], j["key"], j["init"]))
    return jobs, pending


def run_pairs(lane_key: str, pairs: list[tuple], on_pair: Callable, *,
              workers: int = DEFAULT_WORKERS, recycle: int = DEFAULT_RECYCLE,
              dask_threads: int = 2, rss_cap_gb: float = DEFAULT_RSS_CAP_GB,
              stop: threading.Event | None = None, poll_s: float = 1.0,
              executor_factory: Callable | None = None, progress_s: float = 60.0) -> dict:
    """Score ``pairs`` (``[(Event, model)]``) of one lane in the worker pool.

    ``on_pair(event, model, df | None, status)`` is called in the parent as each pair
    completes. Returns stats: wall, jobs, peak RSS (total and per worker), retries, the
    per-job timing split and ``stopped`` / ``abort`` when the run ended early.
    ``executor_factory`` lets tests run the jobs in-process.
    """
    lane = LANES[lane_key]
    stop = stop or threading.Event()
    jobs, pending = build_jobs(lane_key, pairs, dask_threads)
    stats: dict = {"lane": lane_key, "workers": workers, "recycle": recycle, "dask_threads": dask_threads,
                   "rss_cap_gb": rss_cap_gb, "pairs": len(pairs), "jobs": len(jobs), "jobs_done": 0,
                   "pairs_done": 0, "retries": 0, "errors": 0, "no_data_inits": 0,
                   "stopped": False, "abort": None}
    t_start = time.time()
    done_recs: list[dict] = []

    def finish(key: str):
        p = pending.pop(key)
        df, st = lane.finalize(p["event"], p["model"], p["plan"], p["recs"])
        on_pair(p["event"], p["model"], df, st)
        stats["pairs_done"] += 1

    for key in [k for k, p in pending.items() if p["n"] == 0]:
        finish(key)                                  # nothing verifiable: documented ``empty``

    def over(gb: float):
        stats["abort"] = f"rss cap: pool at {gb:.1f} GB > {rss_cap_gb:.0f} GB"
        logger.error(stats["abort"])
        stop.set()

    ctx = mp.get_context("spawn")
    make_ex = executor_factory or (lambda: cf.ProcessPoolExecutor(
        max_workers=workers, mp_context=ctx, initializer=_worker_init,
        max_tasks_per_child=recycle or None))
    sampler = RssSampler(cap_gb=rss_cap_gb, on_over=over)
    stats["pool_rebuilds"] = 0
    with sampler:
        ex = make_ex()
        inflight: dict = {}                    # future -> job (so a broken pool can requeue them)
        retry_q: list = []                     # jobs lost to a broken pool, resubmitted first
        it = iter(jobs)
        exhausted = not jobs
        last_log = time.time()
        try:
            while inflight or retry_q or (not exhausted and not stop.is_set()):
                broken = False
                while not stop.is_set() and len(inflight) < workers + 2 and not broken:
                    job = retry_q.pop() if retry_q else None
                    if job is None:
                        if exhausted:
                            break
                        job = next(it, None)
                        if job is None:
                            exhausted = True
                            break
                    try:
                        inflight[ex.submit(_run_job, job)] = job
                    except BrokenProcessPool:
                        retry_q.append(job)
                        broken = True
                if not inflight and not broken:
                    break
                if inflight:
                    done, _ = cf.wait(list(inflight), timeout=poll_s, return_when=cf.FIRST_COMPLETED)
                else:
                    done = set()
                for f in done:
                    job = inflight.pop(f)
                    try:
                        r = f.result()
                    except BrokenProcessPool:
                        retry_q.append(job)
                        broken = True
                        continue
                    except Exception as exc:  # noqa: BLE001 - a job-level failure: the pair is retried next run
                        stats["errors"] += 1
                        logger.error("worker job failed: %r", exc)
                        continue
                    stats["jobs_done"] += 1
                    stats["retries"] += r.get("retries", 0)
                    stats["errors"] += r["status"] == "error"
                    stats["no_data_inits"] += r["status"] == "no_data"
                    done_recs.append(r)
                    p = pending.get(r["key"])
                    if p is not None:
                        p["recs"].append(r)
                        if len(p["recs"]) == p["n"]:
                            finish(r["key"])
                if broken:
                    # a worker died (OOM kill / segfault): every in-flight future is lost with it.
                    # Completed pairs are already persisted; requeue the lost jobs on a fresh pool a
                    # bounded number of times, then stop cleanly and mark the abort.
                    stats["errors"] += 1
                    retry_q.extend(inflight.values())
                    inflight.clear()
                    ex.shutdown(wait=False, cancel_futures=True)
                    if stats["pool_rebuilds"] >= MAX_POOL_REBUILDS:
                        stats["abort"] = f"worker pool broken {stats['pool_rebuilds'] + 1}x (a worker was killed, likely OOM)"
                        logger.error(stats["abort"])
                        stop.set()
                        retry_q.clear()
                    else:
                        stats["pool_rebuilds"] += 1
                        logger.error("worker pool broken (a worker was killed, likely OOM): rebuilding (%d/%d), "
                                     "%d jobs requeued", stats["pool_rebuilds"], MAX_POOL_REBUILDS, len(retry_q))
                        ex = make_ex()
                if time.time() - last_log > progress_s:
                    last_log = time.time()
                    logger.info("lane %s: %d/%d jobs, %d pairs done, pool rss %.1f GB (peak %.1f)",
                                lane_key, stats["jobs_done"], len(jobs), stats["pairs_done"],
                                sampler.timeline[-1][1] if sampler.timeline else 0.0, sampler.peak_total / 1e9)
            if stop.is_set():
                stats["stopped"] = True
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
    wpids = {r["pid"] for r in done_recs}
    wpeaks = [v for k, v in sampler.peak_by_pid.items() if k in wpids]
    good = [r for r in done_recs if r["status"] == "scored"]
    store = [r for r in good if r["key"].split("|")[1] != tc_lane.BASELINE_MODEL]
    ifs = [r for r in good if r["key"].split("|")[1] == tc_lane.BASELINE_MODEL]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    stats.update(
        wall_s=round(time.time() - t_start, 1),
        peak_total_rss_gb=round(sampler.peak_total / 1e9, 2),
        peak_worker_rss_gb=round(max(wpeaks, default=0) / 1e9, 3),
        n_worker_processes=len(wpids),
        mean_job_s_store=mean([r["job_s"] for r in store]), mean_job_s_ifs=mean([r["job_s"] for r in ifs]),
        mean_read_s_store=mean([r["read_s"] for r in store]), mean_track_s=mean([r["track_s"] for r in good]),
        n_jobs_store=len(store), n_jobs_ifs=len(ifs),
        unfinished_pairs=len(pending))
    return stats


# =============================================================================
# CLI: the guarded backfill job (ops/lane_backfill.sh) and a direct lane run
# =============================================================================
def refuse_live_tree(results: Path, parts: Path) -> str | None:
    """Name of the first of cwd / results / parts that resolves under the live tree, else None.
    ``LANE_BACKFILL_ALLOW_LIVE=1`` overrides (a deliberate live backfill)."""
    if os.environ.get("LANE_BACKFILL_ALLOW_LIVE") == "1":
        return None
    live = LIVE_ROOT.resolve()
    for name, path in (("cwd", Path.cwd()), ("--results", results), ("--parts", parts)):
        r = path.resolve()
        if r == live or live in r.parents:
            return f"{name} {r}"
    return None


def run_lane_cli(args) -> int:
    from .. import pipeline, sources
    from ..events.types import Event

    results = Path(args.results)
    live = refuse_live_tree(results, Path(args.parts))
    if live:
        logger.error("REFUSED: %s resolves under the live tree %s; run from a private tree "
                     "or set LANE_BACKFILL_ALLOW_LIVE=1", live, LIVE_ROOT)
        return EXIT_REFUSED
    pipeline.RESULTS = results
    pipeline.PARTS = Path(args.parts)
    lane = LANES[args.lane]
    events = [Event.from_dict(d) for d in json.loads((results / "events_selected.json").read_text())
              if d["event_type"] == lane.event_type]
    if args.max_events:
        events = events[:args.max_events]
    models = args.models.split(",") if args.models else sources.list_models()
    scored_models, _unsupported = lane.models(models)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: (logger.warning("SIGTERM: draining, completed pairs are kept"), stop.set()))
    truth_of = lambda e: lane.truth  # noqa: E731
    pairs = pipeline.lane_pairs(events, scored_models, truth_of, force=args.force)
    logger.info("lane %s: %d events x %d models, %d pairs without a part file",
                args.lane, len(events), len(scored_models), len(pairs))
    stats = pipeline.run_lanes(pairs, lane_key=args.lane, truth_of=truth_of, workers=args.workers,
                               recycle=args.recycle, dask_threads=args.dask_threads,
                               rss_cap_gb=args.rss_cap_gb, stop=stop)
    print(json.dumps(stats, default=str), flush=True)
    out = Path(args.stats_json) if args.stats_json else results.parent / f"lane_{args.lane}_stats.json"
    out.write_text(json.dumps(stats, indent=1, default=str))
    if stats.get("abort") and "rss cap" in str(stats["abort"]):
        return EXIT_RSS_CAP
    if stats["stopped"]:
        return 0                                    # guard stop: completed pairs kept, resumable
    if not args.no_publish:
        # merge the part files into scores / scorecard / meta (no detection, no EWB scoring)
        return pipeline.run(days=args.days, publish_only=True, top_n=None)
    return 0


def main(argv=None) -> int:
    from .. import _guard

    ap = argparse.ArgumentParser(prog="ewb_live.scoring.lane_pool", description=__doc__.splitlines()[0])
    ap.add_argument("lane", choices=sorted(LANES))
    ap.add_argument("--models", default=None, help="comma-separated (default: all registered)")
    ap.add_argument("--results", default="data/results")
    ap.add_argument("--parts", default="data/cache/score_parts")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("EWB_LIVE_SCORING_WORKERS", DEFAULT_WORKERS)))
    ap.add_argument("--recycle", type=int, default=DEFAULT_RECYCLE, help="jobs per worker before it is replaced")
    ap.add_argument("--dask-threads", type=int, default=2)
    ap.add_argument("--rss-cap-gb", type=float, default=DEFAULT_RSS_CAP_GB)
    ap.add_argument("--max-events", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="rescore pairs that already have a part file")
    ap.add_argument("--stats-json", default=None)
    ap.add_argument("--days", type=int, default=90, help="window of the publish-only merge")
    ap.add_argument("--no-publish", action="store_true", help="write part files only")
    args = ap.parse_args(argv)
    _guard.require_ewb_host()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stderr)])
    return run_lane_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
