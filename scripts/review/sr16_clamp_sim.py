# sr16_clamp_sim: simulate the §L.1 curation-lifetime clamp on the current lane.
# Owner-decision evidence: which curated AR windows get trimmed / killed, and
# how many published frames the clamp costs, for bridge sizes 1 and 3 frames.
# Read-only. DEAD = object absent inside bbox AND absent inside bbox+5deg
# (same definition as sr15/sr14d). NEARBY = alive -> clamp keeps the window.
import json, numpy as np
from ewb_live.viz.exceedance import frame_layers, LAT_N, LON_N
idx = json.load(open("data/results/raw_exceedance/index.json"))
D = "data/results/raw_exceedance"
FR = idx["frames"]
ev = json.load(open("data/results/events_selected.json"))
ev = ev["events"] if isinstance(ev, dict) else ev
ar = [e for e in ev if e["event_type"] == "atmospheric_river"]
cache = {}
def ar_(t):
    if t not in cache:
        cache[t] = frame_layers(open(f"{D}/{t}.bin", "rb").read(), (LAT_N, LON_N))[2]
    return cache[t]
lat = -90 + 0.25 * np.arange(LAT_N)
lon = -180 + 0.25 * np.arange(LON_N)
def cnt(a, la1, la2, lo1, lo2):
    r = np.where((lat >= la1) & (lat <= la2))[0]
    c = np.flatnonzero(((lon - lo1) % 360.0) <= ((lo2 - lo1) % 360.0))  # eastward arc, AM-safe
    if not len(r) or not len(c):
        return -1
    return int(a[np.ix_(r, c)].sum())
def dead_runs(flags):  # flags[i]=True means DEAD at frame i
    runs, s = [], None
    for i, d in enumerate(flags):
        if d and s is None: s = i
        if not d and s is not None: runs.append((s, i - 1)); s = None
    if s is not None: runs.append((s, len(flags) - 1))
    return runs
report = []
for e in ar:
    b = e["bbox"]
    la1, la2 = min(b[0], b[1]), max(b[0], b[1])
    lo1, lo2 = b[2], b[3]  # eastward arc; lo1 > lo2 = AM crossing (types.py:22-26)
    t0 = e["start"][:13].replace("-", "").replace("T", "")
    t1 = e["end"][:13].replace("-", "").replace("T", "")
    win = [t for t in FR if t0 <= t <= t1]
    if not win: continue
    flags = []
    for t in win:
        a = ar_(t)
        if cnt(a, la1, la2, lo1, lo2) > 0:
            flags.append(False)
        else:
            flags.append(cnt(a, la1 - 5, la2 + 5, lo1 - 5, lo2 + 5) == 0)
    runs = dead_runs(flags)
    row = {"event": e["id"], "win": f"{win[0]}->{win[-1]}", "n": len(win),
           "dead_runs": [(win[s], win[t], t - s + 1) for s, t in runs]}
    for bridge in (1, 3):
        cut = None
        for s, t in runs:
            if t - s + 1 > bridge:  # first unbridgable DEAD run
                cut = s
                break
        if cut is None:
            row[f"b{bridge}"] = {"end": win[-1], "lost": 0, "status": "keep"}
        elif cut == 0:
            row[f"b{bridge}"] = {"end": None, "lost": len(win), "status": "REJECT"}
        else:
            row[f"b{bridge}"] = {"end": win[cut - 1], "lost": len(win) - cut,
                                 "status": "clamp"}
    report.append(row)
for r in report:
    if r["dead_runs"] or r["b1"]["status"] != "keep":
        print(r["event"], r["win"], "runs", r["dead_runs"], "| b1", r["b1"], "| b3", r["b3"])
for bridge in (1, 3):
    n_clamp = sum(1 for r in report if r[f"b{bridge}"]["status"] == "clamp")
    n_rej = sum(1 for r in report if r[f"b{bridge}"]["status"] == "REJECT")
    lost = sum(r[f"b{bridge}"]["lost"] for r in report)
    tot = sum(r["n"] for r in report)
    print(f"bridge={bridge}: clamp {n_clamp} reject {n_rej} keep {len(report)-n_clamp-n_rej}"
          f" | frames lost {lost}/{tot} ({100*lost/tot:.1f}%)")
print("GATE_JSON:" + json.dumps(report))
