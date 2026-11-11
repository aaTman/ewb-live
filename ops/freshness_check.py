#!/usr/bin/env python3
"""EWB Live freshness alarm (stall postmortem 2026-11-07).

Runs every 10 min from ``ewb-live-freshness.timer``. The 2026-11-07
incident: the pipeline unit stayed ``active`` for ~23 h, its log grew, jobs
completed — but ``data/results`` never updated, and nothing restarted or
alerted because every watcher keyed on process EXIT. This check keys on
the two things that must advance while the unit claims to run:

  1. heartbeat  — ``data/heartbeat.json`` (outside the served tree; written
     by the pipeline parent at start, after every job, and every 5 min in
     the pool).
     Silent for > ``--hb-max-min`` while the unit is active
     => parent wedged => ``systemctl restart ewb-live-pipeline``.
  2. publication — ``data/results/meta.json`` ``last_updated``. Older than
     ``--meta-max-min`` while the unit is active and jobs have completed
     since that publish => results pipeline broken => restart.

The restart is safe: the pipeline is incremental (per-part files +
pair_status survive a restart) and the runner re-enters ``run()`` from
scratch. This tool NEVER starts scoring on a stopped unit (that is the
agent/orchestrator's decision) and skips while ``ewb_live.weekly`` is
running (weekly owns the pipeline as its own child; restarting the unit
then would double-run).

Exit 0 = healthy or acted; 1 = restart FAILED; 2 = bad state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

UNIT = "ewb-live-pipeline.service"
REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "logs" / "freshness.log"


def _log(msg: str) -> None:
    line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z freshness: {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def _iso_age(path: Path, key: str | None) -> float | None:
    """Age in seconds of an ISO timestamp (json `key`, else file mtime)."""
    ts = None
    try:
        if key:
            raw = json.loads(path.read_text()).get(key)
            ts = dt.datetime.fromisoformat(raw)
        else:
            ts = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()


def _restart(reason: str) -> int:
    _log(f"RESTART {UNIT}: {reason}")
    r = subprocess.run(["systemctl", "restart", UNIT])
    if r.returncode == 0:
        _log("restart issued")
        return 0
    _log(f"restart FAILED rc={r.returncode}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(REPO / "data" / "results"))
    ap.add_argument("--hb-max-min", type=float, default=90.0,
                    help="heartbeat silence that means wedged (detect+mirror "
                         "update can legitimately run ~45 min before scoring)")
    ap.add_argument("--meta-max-min", type=float, default=720.0,
                    help="meta.json publication age that means broken "
                         "(a healthy serial run publishes after every job)")
    args = ap.parse_args(argv)
    results = Path(args.results)

    if not _active(UNIT):
        return 0  # not running: not our call (enable-on-boot + orchestrator)

    # weekly.py runs the pipeline as its own child; the unit must not
    # double-run against it
    if subprocess.run(["pgrep", "-f", "ewb_live.weekly"],
                      stdout=subprocess.DEVNULL).returncode == 0:
        _log("ewb_live.weekly in flight; skipping checks this tick")
        return 0

    hb_path = results.parent / "heartbeat.json"  # outside the served tree (ponytail 19:15Z)
    hb_age = _iso_age(hb_path, "ts")
    if hb_age is None:
        hb_age = time.time() - hb_path.stat().st_mtime \
            if hb_path.exists() else 1e9

    # A beat only counts if it belongs to the active unit: a standalone CLI
    # run (e.g. truth_mirror sync) beating into the same file would mask a
    # wedged unit for the whole sync (ponytail 22:21Z). The unit's ExecStart
    # execs python, so MainPID *is* the beating parent. Fail-open: any
    # query/parse trouble leaves the age untouched.
    if hb_age <= args.hb_max_min * 60:
        try:
            unit_pid = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "--value", UNIT],
                capture_output=True, text=True).stdout.strip()
            hb_pid = json.loads(hb_path.read_text()).get("pid")
        except (OSError, json.JSONDecodeError, AttributeError):
            unit_pid = hb_pid = None
        if hb_pid and unit_pid not in (None, "", "0") and str(hb_pid) != unit_pid:
            _log(f"WARN heartbeat pid {hb_pid} != unit MainPID {unit_pid} "
                 f"(beat from another process); treating heartbeat as silent")
            hb_age = 1e9

    if hb_age > args.hb_max_min * 60:
        return _restart(f"heartbeat silent for {hb_age/60:.0f} min "
                        f"(limit {args.hb_max_min:.0f}) while unit active")

    meta_age = _iso_age(results / "meta.json", "last_updated")
    if meta_age is None:
        return 0  # fresh box, nothing published yet; heartbeat covers it
    # publication must keep pace with completed work: compare meta age with
    # jobs completed since that publish (heartbeat carries jobs_done)
    jobs_done = None
    try:
        jobs_done = json.loads((results.parent / "heartbeat.json").read_text()).get("jobs_done")
    except (OSError, json.JSONDecodeError):
        pass
    if meta_age > args.meta_max_min * 60:
        if jobs_done:
            return _restart(f"meta.json not updated for {meta_age/3600:.1f} h while "
                            f"{jobs_done} jobs completed since last publish")
        _log(f"WARN meta.json stale ({meta_age/3600:.1f} h) but no jobs done "
             f"(phase likely detect/mirror); no action")
    return 0


if __name__ == "__main__":
    sys.exit(main())
