"""What the curated selection ships before/after the C3+C5 catalogue (hb-fix3).

Round-3 item 5's "what ships" measurement, on the REAL select path
(``ewb_live/events/select.py``): both catalogues go through
``subsume_freeze_overlaps`` + ``rank_and_cap`` exactly as ``detect_events``
runs them, with the owner-adjudicated exclusions applied the way
``_load_exclusions`` applies them (run with CWD = a tree whose
``data/curation_exclusions.json`` is the production file; pass
``--exclusions`` to copy it there first).

  before = the production raw catalogue (``events.json``) of the three
           temperature types, curated;
  after  = a production ``scan_temperature_events`` of the lane window with
           the CURRENT code (C3 + qualification-clock defaults, round-3
           merge fix), the same window the lane spans, curated the same way.

Only the three temperature types take part in either pass: cross-type
conflicts against AR/flood/TC are not re-judged here (the release re-runs
the full curation), so the "before" pass reproduces the live heat/freeze/
cold-snap selection only up to those cross-type drops.

Cost of the first weekly run after the release:
  * score jobs = selected-count delta (each selected event is one scoring
    job per model per week);
  * viz rebuilds = selected-after events whose production asset
    (``data/results/events/<id>/viz.json``) is absent or carries a
    different ``event_stamp`` (the staleness rule in ``ewb_live/viz/
    __main__.py``), priced with the production per-asset build times parsed
    from a weekly log (``--viz-log``, the ``ok ... Xs`` lines) against the
    5400 s step timeout. No production build is run here.

LXC-only (reads the lane + production catalogue read-only, writes only
under --out). Run on the ewb LXC (scratch tree):

  cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
    /opt/ewb-live/.venv/bin/python scripts/review/hb06_ship.py \
      --lane /opt/ewb-live/data/results/raw_exceedance \
      --prod-events /opt/ewb-live/data/results/events.json \
      --exclusions /opt/ewb-live/data/curation_exclusions.json \
      --prod-assets /opt/ewb-live/data/results/events \
      --start 2026-08-21 --end 2026-11-13 \
      --out /opt/ewb-heatbox/out/hb06_ship.json
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from ewb_live.events import select as sel
from ewb_live.events.cold_snap import subsume_freeze_overlaps
from ewb_live.events.temperature import scan_temperature_events
from ewb_live.events.types import Event

TYPES = ("heat_wave", "freeze", "cold_snap")
VIZ_STEP_TIMEOUT_S = 5400.0
_OK_S = re.compile(r"ok \d+ KB ([\d.]+)s")


def _to_event(d: dict) -> Event:
    return Event(id=d["id"], event_type=d["event_type"],
                 title=d.get("title", ""),
                 start=dt.datetime.fromisoformat(d["start"]),
                 end=dt.datetime.fromisoformat(d["end"]),
                 bbox=tuple(d["bbox"]), peak_value=d.get("peak_value"),
                 details=d.get("details") or {})


def _as_dict(e: Event) -> dict:
    d = dataclasses.asdict(e)
    d["start"] = e.start.isoformat()      # as Event.to_dict writes it
    d["end"] = e.end.isoformat()
    return d


def curate(events: list[Event]) -> tuple[list[Event], list[dict]]:
    """The real detect_events curation order, temperature types only."""
    decisions: list[dict] = []
    events = subsume_freeze_overlaps(events, decisions)
    return sel.rank_and_cap(events, sel.DEFAULT_TOP_N, decisions), decisions


def counts(events: list[Event]) -> dict[str, int]:
    return {t: sum(1 for e in events if e.event_type == t) for t in TYPES}


def viz_rebuilds(after: list[Event], assets_root: Path) -> tuple[list[str], list[str]]:
    """(missing-asset ids, stamp-mismatch ids) of the new selection (SPEC:
    the viz staleness rule in ewb_live/viz/__main__.py)."""
    missing: list[str] = []
    stale: list[str] = []
    for e in after:
        p = assets_root / e.id / "viz.json"
        if not p.exists():
            missing.append(e.id)
            continue
        try:
            asset = json.loads(p.read_text())
        except Exception:  # noqa: BLE001 - unreadable asset rebuilds
            missing.append(e.id)
            continue
        stamp = {"start": e.start.isoformat(), "end": e.end.isoformat(),
                 "bbox": [round(float(x), 4) for x in e.bbox]}
        if (asset.get("schema") != 3
                or asset.get("event_stamp") != stamp):
            stale.append(e.id)
    return missing, stale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", type=Path, required=True)
    ap.add_argument("--prod-events", type=Path, required=True)
    ap.add_argument("--exclusions", type=Path, default=None,
                    help="production data/curation_exclusions.json; copied "
                         "to ./data/curation_exclusions.json (CWD scratch "
                         "tree) so _load_exclusions applies it")
    ap.add_argument("--prod-assets", type=Path, default=None,
                    help="production data/results/events dir for the viz "
                         "staleness check")
    ap.add_argument("--viz-log", type=Path, action="append", default=[],
                    help="production log(s) with the ewb_live.viz per-asset "
                         "'ok ... KB ...s' lines; repeatable. Round-3 timing "
                         "was read from the wrong file - these lines go to "
                         "the viz stdout logs (viz*.log), NOT the "
                         "weekly-*.log, so a single weekly path matched "
                         "nothing. Pass every viz*.log.")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--after-events-out", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    if a.exclusions and a.exclusions.exists():
        Path("data").mkdir(exist_ok=True)
        Path("data/curation_exclusions.json").write_text(
            a.exclusions.read_text())
        print(f"applied exclusions from {a.exclusions}", flush=True)

    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end)

    before = [_to_event(d) for d in json.loads(a.prod_events.read_text())
              if d["event_type"] in TYPES]
    before_sel, _dec0 = curate(before)

    after: list[Event] = []
    for hazard in TYPES:
        evs = scan_temperature_events(start, end, hazard, frames_dir=a.lane)
        print(f"scanned {hazard}: {len(evs)}", flush=True)
        after += evs
    after_sel, _dec1 = curate(after)
    if a.after_events_out:
        a.after_events_out.parent.mkdir(parents=True, exist_ok=True)
        a.after_events_out.write_text(json.dumps([_as_dict(e) for e in after],
                                                 indent=1))

    res: dict = {"window": [a.start, a.end],
                 "raw": {"before": counts(before), "after": counts(after)},
                 "selected": {"before": counts(before_sel),
                              "after": counts(after_sel)}}
    b_ids = {e.id for e in before_sel}
    a_ids = {e.id for e in after_sel}
    res["selected"]["added_ids"] = sorted(a_ids - b_ids)
    res["selected"]["dropped_ids"] = sorted(b_ids - a_ids)
    res["score_jobs_per_run"] = {"before": len(before_sel),
                                 "after": len(after_sel),
                                 "delta": len(after_sel) - len(before_sel)}
    if a.prod_assets and a.prod_assets.exists():
        missing, stale = viz_rebuilds(after_sel, a.prod_assets)
        est: dict = {"rebuild_missing": len(missing),
                     "rebuild_stale_stamp": len(stale),
                     "total_rebuild": len(missing) + len(stale)}
        if a.viz_log:
            times = [float(m.group(1))
                     for path in a.viz_log if path.exists()
                     for line in path.read_text(errors="replace").splitlines()
                     if (m := _OK_S.search(line))]
            if times:
                srt = sorted(times)

                def pct(q: float) -> float:
                    return srt[min(len(srt) - 1, int(round(q * (len(srt) - 1))))]

                med = statistics.median(times)
                mean = statistics.fmean(times)
                p90 = pct(0.90)
                est.update({"asset_s_n": len(times),
                            "asset_s_med": round(med, 1),
                            "asset_s_mean": round(mean, 1),
                            "asset_s_p90": round(p90, 1),
                            "asset_s_max": round(srt[-1], 1),
                            "rebuild_s_med_est": round(med * est["total_rebuild"]),
                            "rebuild_s_mean_est": round(mean * est["total_rebuild"]),
                            "rebuild_s_p90_est": round(p90 * est["total_rebuild"]),
                            "step_timeout_s": VIZ_STEP_TIMEOUT_S,
                            "fits_median": round(med * est["total_rebuild"]) < VIZ_STEP_TIMEOUT_S,
                            "fits_p90": round(p90 * est["total_rebuild"]) < VIZ_STEP_TIMEOUT_S})
            else:
                est["viz_log_note"] = (
                    "no 'ok ... KB ...s' lines in the given logs - the viz "
                    "per-asset timing is in viz*.log, not weekly-*.log")
        res["viz_first_weekly_run"] = est
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
