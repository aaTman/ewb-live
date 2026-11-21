"""CLI: python -m ewb_live.viz --all [--events a,b] [--limit N] [--force]
                     [--leads 24,72,120,240] [--ar-leads 24,120] [--types ...]

Builds data/results/events/<id>/viz.json for scored events (events_selected.json
preferred, events.json fallback) and refreshes data/results/events/index.json.
Skips events whose viz.json already exists at the current schema AND was built
from the same event window/box (the ``event_stamp`` saved in the asset), unless
--force. The stamp matters because an ongoing event keeps its id while its end
and persistence box are redrawn every weekly run (SPEC §2): id + schema alone
would keep serving week-old maps for a growing event.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

from . import build, exceedance as raw_exceedance, timeline

logger = logging.getLogger("ewb_live.viz")


def load_events(root: Path) -> list[dict]:
    for name in ("events_selected.json", "events.json"):
        p = root / "data/results" / name
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else data.get("events", [])
    raise SystemExit("no events json found in data/results/")


def event_stamp(ev: dict) -> dict:
    """The event geometry a viz asset was built from (staleness stamp).

    Stored in every viz.json; a rebuild is forced when the event's start,
    end or bbox moved since - which is exactly what happens to an ongoing
    event as its window grows and its persistence box is redrawn.
    """
    return {"start": str(ev["start"]), "end": str(ev["end"]),
            "bbox": [round(float(x), 4) for x in ev["bbox"]]}


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host  # CLI-only guard (see _guard.py)

    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz")
    ap.add_argument("--all", action="store_true", help="all curated events")
    ap.add_argument("--events", type=str, default="", help="comma-separated event ids")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--leads", type=str, default=",".join(str(x) for x in build.HEAT_LEADS_DEFAULT))
    ap.add_argument("--ar-leads", type=str, default=",".join(str(x) for x in build.AR_LEADS_DEFAULT))
    ap.add_argument("--types", type=str,
                    default="heat_wave,freeze,cold_snap,atmospheric_river,flood")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    heat_leads = [int(x) for x in args.leads.split(",") if x.strip()]
    ar_leads = [int(x) for x in args.ar_leads.split(",") if x.strip()]
    types = {t.strip() for t in args.types.split(",") if t.strip()}

    want = {e.strip() for e in args.events.split(",") if e.strip()}
    if not (args.all or want):
        ap.error("choose --all or --events id,id")
    events = [e for e in load_events(build.ROOT)
              if (want and e["id"] in want) or (args.all and e.get("event_type") in types)]
    if args.limit:
        events = events[:args.limit]

    from ewb_live.scoring.truth import probe_arco_cutoff
    cutoff = probe_arco_cutoff()
    logger.info("processing %d events (truth cutoff %s)", len(events), cutoff)

    entries = {}
    for i, ev in enumerate(events, 1):
        out = build.OUT_DIR / ev["id"] / "viz.json"
        stamp = event_stamp(ev)
        if out.exists() and not args.force:
            try:
                asset = json.loads(out.read_text())
                if (asset.get("schema") == build.SCHEMA
                        and asset.get("event_stamp") == stamp):
                    logger.info("[%d/%d] %s exists, skip", i, len(events), ev["id"])
                    continue
            except Exception:
                pass
        t0 = time.monotonic()
        try:
            doc = build.build_event(ev, cutoff, heat_leads, ar_leads)
            doc["event_stamp"] = stamp
            path = build.write_event(ev["id"], doc)
            doc["generated_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            entries[ev["id"]] = {"kind": doc["kind"], "ok": True,
                                 "size_kb": path.stat().st_size // 1024,
                                 "truth_source": doc.get("truth_source"),
                                 "generated": doc["generated_at"]}
            logger.info("[%d/%d] %s %s ok %.0f KB %.1fs", i, len(events), ev["id"],
                        doc["kind"], path.stat().st_size / 1024, time.monotonic() - t0)
        except Exception as exc:  # keep the batch going
            logger.exception("event %s failed", ev["id"])
            entries[ev["id"]] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    build.update_index(entries)
    try:  # global dashboard data asset — non-fatal, ids can never drift
        timeline.build(build.ROOT / "data" / "results")
    except Exception:
        logger.exception("global_timeline.json build failed (non-fatal)")
    try:  # raw 6-hourly exceedance frames — incremental, non-fatal
        raw_exceedance.build(build.ROOT)
    except Exception:
        logger.exception("raw_exceedance build failed (non-fatal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
