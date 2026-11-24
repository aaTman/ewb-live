"""guardian-design: serial event viz rebuild WITHOUT the CLI's raw_exceedance tail."""
import datetime as dt, json, logging, sys, time
from ewb_live.viz import build, timeline
from ewb_live.viz.__main__ import load_events
from ewb_live.scoring.truth import probe_arco_cutoff
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("ewb_live.viz.build").setLevel(logging.DEBUG)
order = {"heat_wave": 0, "freeze": 1, "atmospheric_river": 2}
events = sorted(load_events(build.ROOT), key=lambda e: order.get(e["event_type"], 9))
only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
if only: events = [e for e in events if e["id"] in only]
cutoff = probe_arco_cutoff()
entries = {}
for i, ev in enumerate(events, 1):
    t0 = time.monotonic()
    try:
        doc = build.build_event(ev, cutoff, build.HEAT_LEADS_DEFAULT, build.AR_LEADS_DEFAULT)
        path = build.write_event(ev["id"], doc)
        entries[ev["id"]] = {"kind": doc["kind"], "ok": True, "size_kb": path.stat().st_size // 1024,
                             "truth_source": doc.get("truth_source"),
                             "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
        bad = sum(1 for m in doc["models"].values() if isinstance(m, dict) for d in m.values()
                  if isinstance(d, dict) and str(d.get("status", "ok")).startswith("unavailable"))
        logging.info("[%d/%d] %s %s ok %d KB %.0fs unavailable_cells=%d", i, len(events), ev["id"], doc["kind"],
                     path.stat().st_size // 1024, time.monotonic() - t0, bad)
    except Exception as exc:
        logging.exception("event %s failed", ev["id"])
        entries[ev["id"]] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
if not only:
    build.update_index(entries)
    timeline.build(build.ROOT / "data" / "results")
logging.info("DONE")
