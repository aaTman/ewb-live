import json, numpy as np
from ewb_live.viz.exceedance import frame_layers, LAT_N, LON_N
idx = json.load(open("data/results/raw_exceedance/index.json"))
D = "data/results/raw_exceedance"
FR = idx["frames"]
ev = json.load(open("data/results/events_selected.json"))
ev = ev["events"] if isinstance(ev, dict) else ev
PL = {"heat_wave": 0, "freeze": 1, "atmospheric_river": 2}
ar = [e for e in ev if e["event_type"] in PL]
cache = {}
def ar_(t):
    if t not in cache:
        cache[t] = frame_layers(open(f"{D}/{t}.bin", "rb").read(), (LAT_N, LON_N))
    return cache[t]
lat = -90 + 0.25 * np.arange(LAT_N)
lon = -180 + 0.25 * np.arange(LON_N)
def cnt(a, la1, la2, lo1, lo2):
    r = np.where((lat >= la1) & (lat <= la2))[0]
    c = np.flatnonzero(((lon - lo1) % 360.0) <= ((lo2 - lo1) % 360.0))  # eastward arc, AM-safe
    if not len(r) or not len(c):
        return -1
    return int(a[np.ix_(r, c)].sum())
report = []
for e in ar:
    b = e["bbox"]
    la1, la2 = min(b[0], b[1]), max(b[0], b[1])
    lo1, lo2 = b[2], b[3]  # eastward arc; lo1 > lo2 = antimeridian crossing (types.py:22-26)
    t0 = e["start"][:13].replace("-", "").replace("T", "")  # YYYYMMDDHH
    t1 = e["end"][:13].replace("-", "").replace("T", "")
    win = [t for t in FR if t0 <= t <= t1]
    if not win:
        continue
    inside, near = [], []
    for t in win:
        a = ar_(t)[PL[e["event_type"]]]
        pi = cnt(a, la1, la2, lo1, lo2)
        inside.append(pi)
    darks = [(t, p) for t, p in zip(win, inside) if p == 0]
    interior = [(t, p) for t, p in darks if t != win[0] and t != win[-1]]
    classes = []
    for t, _ in interior:
        a = ar_(t)[PL[e["event_type"]]]
        pn = cnt(a, la1 - 5, la2 + 5, lo1 - 5, lo2 + 5)
        classes.append((t, "DEAD" if pn == 0 else f"NEARBY:{pn}"))
    # maximal runs of consecutive DEAD frames (adjacent in win); NEARBY breaks runs
    dead_set = {t for t, c in classes if c == "DEAD"}
    dead_runs, s = [], None
    for i, t in enumerate(win):
        if t in dead_set and s is None: s = i
        if t not in dead_set and s is not None: dead_runs.append([win[s], win[i-1], i-s]); s = None
    if s is not None: dead_runs.append([win[s], win[-1], len(win)-s])
    # G3: object absent at window start (bbox and bbox+5deg) — §Q dead-at-t0 class
    start_dead = False
    if inside[0] == 0:
        pn0 = cnt(ar_(win[0])[PL[e["event_type"]]], la1 - 5, la2 + 5, lo1 - 5, lo2 + 5)
        start_dead = pn0 == 0
    report.append({"event": e["id"], "kind": e["event_type"], "win": f"{t0}->{t1}", "frames": len(win),
                   "interior_dark": len(interior), "classes": classes, "dead_runs": dead_runs,
                   "start_dead": start_dead})
for r in report:
    print(r["event"], r["win"], "frames", r["frames"], "interior_dark", r["interior_dark"], r["classes"])
n_int = sum(r["interior_dark"] for r in report)
n_ev = sum(1 for r in report if r["interior_dark"])
n_dead = sum(1 for r in report for c in r["classes"] if c[1] == "DEAD")
print("events:", len(report), "| with interior dark:", n_ev, "| interior dark frames:", n_int,
      "| of which DEAD:", n_dead)
print("GATE_JSON:" + json.dumps(report))
