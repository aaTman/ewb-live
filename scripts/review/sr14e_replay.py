"""sr14e: faithful replay of rank_and_cap (floor->merge->conflicts->cap)
on the current candidate catalogue. Run on box."""
import json
from pathlib import Path

from ewb_live.events import select as S
from ewb_live.events.types import Event

R = Path("data/results")
ev = [Event.from_dict(e) for e in json.load(open(R / "events.json"))]
dec: list = []
kept = S.rank_and_cap(list(ev), 15, decisions=dec)
kept_ar = sorted(e.id for e in kept if e.event_type == "atmospheric_river")
es = json.load(open(R / "events_selected.json"))
shipped = sorted(e["id"] for e in (es["events"] if isinstance(es, dict) else es)
                 if e["event_type"] == "atmospheric_river")
print("replay AR kept:", len(kept_ar))
print("shipped AR     :", len(shipped))
print("replay-only:", [i[:6] for i in kept_ar if i not in shipped])
print("shipped-only:", [i[:6] for i in shipped if i not in kept_ar])
for d in dec:
    if d.get("event_type") == "atmospheric_river":
        print(json.dumps({k: d[k] for k in ("action", "id", "core", "members", "reason",
                                            "rank", "conflicts_with") if k in d}))
