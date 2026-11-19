import json
import os
import sys

R = "data/results"
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-10-26"
sel = json.load(open(f"{R}/events_selected.json"))
evs = sel["events"] if isinstance(sel, dict) and "events" in sel else sel
day = [e for e in evs if e["start"] <= DAY and e["end"] > DAY]
BOX = {"heat_wave", "freeze", "cold_snap", "atmospheric_river", "flood"}
TRACK = {"tropical_cyclone", "midlat_cyclone"}
print("published active %s: %d" % (DAY, len(day)))
by = {}
for e in day:
    by[e["event_type"]] = by.get(e["event_type"], 0) + 1
print("by type:", by)
boxes = [e for e in day if e["event_type"] in BOX]
print("box-lane events: %d; wrapped bboxes: %d" % (
    len(boxes), sum(1 for e in boxes if e["bbox"][2] > e["bbox"][3])))
seen = {}
for e in boxes:
    seen.setdefault(tuple(e["bbox"]), []).append(e["id"][:12] + " " + e["event_type"])
for k, v in seen.items():
    if len(v) > 1:
        print("DUPLICATE bbox", k, v)
# viz docs present?
missing = []
for e in day:
    p = f"{R}/viz/{e['event_type']}/{e['id']}.json"
    if not os.path.exists(p):
        p2 = f"{R}/viz/{e['id']}.json"
        if not os.path.exists(p2):
            missing.append((e["event_type"], e["id"][:12]))
print("events without a viz doc:", missing[:20], "(%d)" % len(missing))
print("viz layout:", sorted(os.listdir(f"{R}"))[:5], os.path.isdir(f"{R}/viz"))
tl = json.load(open(f"{R}/global_timeline.json"))
ev_t = [x for x in tl.get("events", []) if x.get("start", "")[:10] <= DAY < x.get("end", "")[:10]]
print("timeline events active that day:", len(ev_t))
for x in ev_t:
    print("  ", x.get("event_type"), x.get("id", "")[:12], x.get("track") is not None,
          len(x.get("track") or []), (x.get("bbox") or ["?"])[:4])
