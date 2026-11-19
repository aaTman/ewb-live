import json
import sys

R = "data/results"
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-10-26"
sel = json.load(open(f"{R}/events_selected.json"))
evs = sel["events"] if isinstance(sel, dict) and "events" in sel else sel
day = {e["id"]: e for e in evs if e["start"] <= DAY and e["end"] > DAY}
tl = json.load(open(f"{R}/global_timeline.json"))
t = {x["id"]: x for x in tl.get("events", [])
     if x.get("start", "")[:10] <= DAY < x.get("end", "")[:10]}
print("published %d  timeline %d" % (len(day), len(t)))
for i, e in day.items():
    if i not in t:
        print("IN PUBLISHED, NOT IN TIMELINE:", e["event_type"], i, e["bbox"],
              e["start"], e["end"], (e.get("details") or {}).get("name"))
    elif list(e["bbox"]) != list(t[i].get("bbox")):
        print("BBOX DIFFERS:", e["event_type"], i, "event", e["bbox"],
              "timeline", t[i].get("bbox"))
for i, x in t.items():
    if i not in day:
        print("IN TIMELINE, NOT PUBLISHED THAT DAY:", x.get("event_type"), i,
              x.get("bbox"), x.get("start"), x.get("end"))
print()
print("published events on %s:" % DAY)
for i, e in sorted(day.items(), key=lambda kv: kv[1]["event_type"]):
    b = e["bbox"]
    print("  %-18s %-13s %-42s wrap=%s" % (e["event_type"], i[:12], b, b[2] > b[3]))
print()
allwrap = [(e["event_type"], e["id"][:12], e["bbox"]) for e in evs if e["bbox"][2] > e["bbox"][3]]
print("PUBLISHED events with lon_min > lon_max (wrapped bbox): %d" % len(allwrap))
for w in allwrap:
    print("   ", w)
