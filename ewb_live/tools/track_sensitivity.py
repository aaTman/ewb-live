"""Tracker-parameter sensitivity of the TC miss rate (PR #16 follow-up, notes/channel_scoring.md).

Why: every model misses more often than the ``ifs_hres`` baseline. This reads each
(storm, model, init) window ONCE and runs several tracker parameter sets on the same
field, so the only thing that changes between columns is the tracker. If the gap between
``ifs_hres`` and the MLWP models were a tracker artefact it would close or flip as the
closed-contour depth / lost-track patience change.

    python -m ewb_live.tools.track_sensitivity --every 3 --workers 10 --out logs/track_sensitivity.json

Run it through the R1 guard (it reads the store and ECMWF); the guard only honours a replacement job
command in test mode:
    LANE_GUARD_TEST=1 LANE_JOB_CMD='.venv/bin/python -m ewb_live.tools.track_sensitivity ...' ops/lane_backfill.sh tc
LXC only (``ewb_live/_guard.py``).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import datetime as dt
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

VARIANTS = {
    "baseline (2 hPa, lost after 2)": dict(delta_hPa=2.0, lost_after=2),
    "1 hPa closed contour": dict(delta_hPa=1.0, lost_after=2),
    "0.5 hPa closed contour": dict(delta_hPa=0.5, lost_after=2),
    "2 hPa, lost after 3": dict(delta_hPa=2.0, lost_after=3),
    "1 hPa, lost after 3": dict(delta_hPa=1.0, lost_after=3),
}


def _job(arg):
    from ..events.types import Event
    from ..scoring import tc, track_verif as tv

    global _DASK
    if not globals().get("_DASK"):
        import dask

        dask.config.set(scheduler="threads", num_workers=2)
        _DASK = True
    ev = Event.from_dict(arg["event"])
    track = tc.observed_track(ev)
    init = dt.datetime.fromisoformat(arg["init"])
    leads = arg["leads"]
    out = {"event": ev.id, "model": arg["model"], "init": arg["init"], "variants": {}}
    try:
        win = tv.window_for(track, init)
        lat, lon, fld, offset = tc.default_reader(arg["model"], init, leads, win)
    except FileNotFoundError:
        out["status"] = "no_data"
        return out
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"error {type(exc).__name__}"
        return out
    i0 = track.index_of(init)
    for name, kw in VARIANTS.items():
        p = dataclasses.replace(tv.TC_PARAMS, **kw)
        flat, flon, fp, ok, nod = tv.follow_track(fld, lat, lon, float(track.lat[i0]), float(track.lon[i0]), p)
        rows = []
        for j, h in enumerate(leads):
            if h == 0 or nod[j]:
                continue
            k = track.index_of(init + dt.timedelta(hours=h))
            if k is None:
                continue
            d = (float(tv.great_circle(track.lat[k], track.lon[k], flat[j], flon[j])) if ok[j] else None)
            rows.append((h + offset, bool(ok[j]), d, float(track.wind[k]) if np.isfinite(track.wind[k]) else None))
        out["variants"][name] = rows
    out["status"] = "ok"
    return out


def main(argv=None) -> int:
    from .. import _guard
    from ..events.types import Event
    from ..scoring import tc

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events-json", default="data/results/events_selected.json")
    ap.add_argument("--every", type=int, default=3, help="use every Nth storm")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    _guard.require_ewb_host()
    evs = [e for e in json.loads(Path(args.events_json).read_text()) if e["event_type"] == tc.EVENT_TYPE]
    evs = evs[:: args.every]
    models = ["graphcast", "aurora", "pangu_weather", "fourcastnet_v2", "graphcast_gfs", "aurora_gfs",
              "pangu_weather_gfs", "fourcastnet_v2_gfs", tc.BASELINE_MODEL]
    jobs = []
    for e in evs:
        plan = tc.plan_for(Event.from_dict(e))
        for init, leads in plan["verified"]:
            for m in models:
                jobs.append({"event": e, "model": m, "init": init.isoformat(), "leads": leads})
    jobs.sort(key=lambda j: (j["model"] != tc.BASELINE_MODEL, j["event"]["id"], j["init"]))
    print(f"{len(evs)} storms, {len(jobs)} jobs", flush=True)
    t0, res = time.time(), []
    # bounded submission (as lane_pool does): max_tasks_per_child + an unbounded ex.map backlog
    # stalls the executor once the first workers retire
    with cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn"),
                                max_tasks_per_child=30) as ex:
        it, inflight = iter(jobs), set()
        while True:
            while len(inflight) < args.workers + 2:
                j = next(it, None)
                if j is None:
                    break
                inflight.add(ex.submit(_job, j))
            if not inflight:
                break
            done, inflight = cf.wait(inflight, return_when=cf.FIRST_COMPLETED)
            for f in done:
                res.append(f.result())
                if len(res) % 200 == 0:
                    print(f"{len(res)}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
    Path(args.out).write_text(json.dumps({"storms": [e["id"] for e in evs], "results": res}))
    print(f"done {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
