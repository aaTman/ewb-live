"""sr14f: assemble ar_landfall_funnel.md from canonical artifacts. Run on box."""
import datetime as dt
import json
from pathlib import Path

R = Path("data/results")


def region(lat, lon):
    lon = (lon + 180.0) % 360.0 - 180.0
    boxes = [
        ("W Canada/Alaska", 43, 72, -170, -55), ("N Atlantic/UK-Scandinavia", 40, 72, -55, 12),
        ("Europe/W Russia", 45, 70, 12, 60), ("W Siberia/Kara", 50, 78, 60, 110),
        ("E Siberia/Okhotsk", 45, 72, 110, 150), ("Japan/E Asia", 25, 50, 122, 150),
        ("NW Pacific (dateline)", 15, 55, -180, -150), ("NE Pacific", 20, 60, -150, -115),
        ("W US coast", 25, 50, -130, -115), ("SE Australia/NZ", -48, -25, 135, 180),
        ("S Pacific/Chile", -56, -25, -110, -66), ("Argentina", -42, -25, -72, -53),
        ("Southern Ocean", -70, -55, -180, 180), ("Arctic", 72, 89, -180, 180),
        ("Mexico/Gulf", 15, 30, -118, -80), ("Tropics/subtropics", -25, 20, -180, 180),
    ]
    for name, la0, la1, lo0, lo1 in boxes:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return f"({lat:.0f}N,{lon:.0f}E)"


def centroid(eb):
    lat = (eb[0] + eb[1]) / 2
    lon = eb[2] + ((eb[3] - eb[2]) % 360.0) / 2
    return lat, lon


f = json.load(open("/tmp/sr14_funnel.json"))
es = json.load(open(R / "events_selected.json"))
shipped = sorted(e["id"] for e in (es["events"] if isinstance(es, dict) else es)
                 if e["event_type"] == "atmospheric_river")
cand = [e for e in json.load(open(R / "events.json")) if e["event_type"] == "atmospheric_river"]
cands = {c["id"]: c for c in cand}

# candidate table exactly as sr14c classified
# real gate outcomes recorded by pipeline.py:533-540 (empty until next pipeline run)
gate = {x["id"]: x["problem"] for x in f["curation_decisions"]
        if x.get("stage") == "publish_gate"}
lines = []
for c in sorted(cand, key=lambda e: -(e.get("peak_value") or 0)):
    d = c["details"] or {}
    dur = (dt.datetime.strptime(c["end"][:10], "%Y-%m-%d")
           - dt.datetime.strptime(c["start"][:10], "%Y-%m-%d")).days
    eb = c["bbox"]
    span_o = (eb[3] - eb[2]) % 360.0
    pv = c.get("peak_value") or 0.0
    lat, lon = centroid(eb)
    if c["id"] in shipped:
        rej = "— (shipped)"
    elif d.get("landfall_days", 0) < 3:
        rej = f"floor: landfall_days {d.get('landfall_days')} < 3"
    elif pv < 600:
        rej = f"floor: peak IVT {pv:.0f} < 600"
    elif pv > 1500:
        rej = f"floor: peak IVT {pv:.0f} > 1500"
    elif abs(d.get("centroid_lat") or 0) < 20:
        rej = "floor: |centroid| < 20"
    elif dur < 2 or dur > 14:
        rej = f"floor: duration {dur}d"
    elif (eb[1] - eb[0]) > 60 or span_o > 60:
        rej = f"floor: span lat {eb[1]-eb[0]:.0f}/lon {span_o:.0f} > 60"
    else:
        dd = {x["id"]: x for x in f["curation_decisions"] if x.get("id")}.get(c["id"])
        core = next((x for x in f["curation_decisions"] if c["id"] in (x.get("members") or [])), None)
        merged_ship = {}
        for e in (es["events"] if isinstance(es, dict) else es):
            mf = (e.get("details") or {}).get("merged_from")
            if mf:
                merged_ship[tuple(sorted(mf))] = e["id"]
        if dd and dd.get("reason") == "quota":
            rej = f"quota: severity rank {dd['rank']} (>15)"
        elif core and core["core"] == c["id"]:
            mid = merged_ship.get(tuple(sorted(core["members"])))
            rej = (f"merge core: shipped as merged id {mid[:12]}" if mid
                   else "publish gate: merged product rejected (inferred)")
        elif core:
            rej = f"merge: absorbed into {core['core'][:12]}"
        else:
            rej = (f"publish gate: {gate[c['id']]}" if c["id"] in gate
                   else "publish gate: rejected (inferred peak_land<500; no gate record)")
    lines.append(f"| {c['id'][:12]} | {c['start'][5:10]}–{c['end'][5:10]} | {region(lat, lon)} "
                 f"| {pv:.0f} | {d.get('peak_land_pixels')} | {d.get('landfall_days')} "
                 f"| {(d.get('track_id') or '')[3:]} | {rej} |")
Path("/tmp/cand_table.md").write_text("\n".join(lines))

# ledger landfalling rejects (D-rules, peak_land>=100, no candidate)
big = [r for r in f["funnel"]
       if r["event"] is None and r["peak_land"] >= 100
       and r["reject"].startswith(("D", "MYSTERY"))]
big.sort(key=lambda r: -r["peak_land"])
lrows = []
for r in big[:20]:
    lat = (r["bbox"][0] + r["bbox"][1]) / 2
    lon = r["bbox"][2] + ((r["bbox"][3] - r["bbox"][2]) % 360.0) / 2
    ivt = f"{r['peak_ivt']:.0f}" if r["peak_ivt"] else "—"
    lrows.append(f"| {r['track'][3:]} | {r['days'][0][4:]}–{r['days'][1][4:]} | {region(lat, lon)} "
                 f"| {ivt} | {r['peak_land']} | {r['landfall_days_track']} | {r['reject']} |")
Path("/tmp/ledger_table.md").write_text("\n".join(lrows))
from collections import Counter
tally = Counter(r["reject"].split(" ")[0] for r in big)
print("landfalling ledger rejects by rule:", dict(tally), "total", len(big))
print("wrote /tmp/cand_table.md /tmp/ledger_table.md")
