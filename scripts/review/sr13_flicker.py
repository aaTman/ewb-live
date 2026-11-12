"""sr13: frame-to-frame flicker test on the PUBLISHED raw AR layer.

Independent of ewb's tracker ids: label components per 6-h frame (display
grid), link objects across steps by overlap, and flag a FLICKER when an
object present at t-1 has no successor at t but a pixel-overlapping object
reappears at t+1 (blink: on-off-on). Reports blink count + where they are.
READ-ONLY. Run on the box via ops/remote.sh.
"""
import json
import sys

import numpy as np
from scipy import ndimage

idx = json.load(open("data/results/raw_exceedance/index.json"))
ny, nx = idx["grid"]["ny"], idx["grid"]["nx"]
frames = idx["frames"]

from ewb_live.viz.exceedance import frame_layers, labels_from_bytes, AR_LABELS_NAME


def load(i):  # decode one frame in-loop: holding ~340 masks costs ~350 MB
    # returns (ar_mask, label_plane) — .trk ids are the AUTHORITATIVE object
    # continuity signal; overlap chaining is only a mask-level prefilter
    # (§K 2026-11-10: 3x3 overlap over-splits growing/moving tracks into
    # false on-off-on chains; label-id intersection vetoes them)
    ar = frame_layers(open(f"data/results/raw_exceedance/{frames[i]}.bin", "rb")
                      .read(), (ny, nx))[2]
    lab = labels_from_bytes(open(f"data/results/raw_exceedance/{frames[i]}{AR_LABELS_NAME}", "rb")
                            .read(), (ny, nx))
    return ar, lab

MIN_PX = 50  # ignore specks as objects
FRAC = 0.30  # overlap vs smaller object


def objs(m, lab):
    if not m.any():
        return []
    lbl, n = ndimage.label(m, structure=np.ones((3, 3), int))
    sizes = np.bincount(lbl.ravel())
    # specks < MIN_PX can never satisfy overlap_ok; dropping them here is
    # semantics-preserving and keeps chains count = real objects
    out = []
    for i in range(1, n + 1):
        if sizes[i] < MIN_PX:
            continue
        o = lbl == i
        out.append((o, frozenset(int(x) for x in np.unique(lab[o]) if x)))
    return out


def overlap_ok(a, b):
    ov = int((a & b).sum())
    return ov >= MIN_PX and ov >= FRAC * min(int(a.sum()), int(b.sum()))


# explicit gap-bridging: a chain may match at t from last_t == t-2; that
# jump is the on-off-on blink the owner saw — UNLESS the before/after
# components share a .trk track id (same tracked object; mask-level
# over-split, §K veto)
blinks = []
ar0, lab0 = load(0)
chains = [{"last": o, "last_t": 0, "ids": s} for o, s in objs(ar0, lab0)]
for t in range(1, len(frames)):
    ar_t, lab_t = load(t)
    cur = objs(ar_t, lab_t)
    elig = [c for c in chains if c["last_t"] in (t - 1, t - 2)]
    used = [False] * len(cur)
    for c in sorted(elig, key=lambda c: c["last_t"], reverse=True):
        d = ndimage.binary_dilation(c["last"])  # loop-invariant in o
        for oi, (o, ids) in enumerate(cur):
            if used[oi]:
                continue
            if overlap_ok(c["last"], o) or int((d & o).sum()) >= max(MIN_PX, FRAC * min(int(c["last"].sum()), int(o.sum()))):
                if c["last_t"] == t - 2 and not (c["ids"] & ids):
                    ys, xs = np.nonzero(o)
                    blinks.append({"gap_frame": frames[t - 1],
                                   "gap": 1,  # by construction: elig is last_t in (t-1, t-2)
                                   "bbox": [round(-90 + 0.25 * ys.min(), 1), round(-90 + 0.25 * ys.max(), 1),
                                            round(-180 + 0.25 * xs.min(), 1), round(-180 + 0.25 * xs.max(), 1)],
                                   "px": int(o.sum())})
                c["last"], c["last_t"], c["ids"] = o, t, ids
                used[oi] = True
                break
    for oi, (o, ids) in enumerate(cur):
        if not used[oi]:
            chains.append({"last": o, "last_t": t, "ids": ids})
    for c in chains:  # dead chain: can never match again (elig needs last_t >= t-2)
        if c["last_t"] < t - 2:
            c["last"] = None

def _bb(b):  # (latmin,latmax,lon_w,lon_e); lon = EASTWARD arc — lon_w > lon_e means
    # antimeridian crossing (ewb_live/events/types.py:22-26); never min/max the lon pair
    if isinstance(b, dict):
        return (min(b["lat"], b.get("lat2", b["lat"])),
                max(b["lat"], b.get("lat2", b["lat"])),
                b["lon"], b.get("lon2", b["lon"]))
    la = sorted(b[:2])
    return (la[0], la[1], b[2], b[3])


def lon_overlap(w1, e1, w2, e2):  # do two eastward lon arcs [w,e] intersect on the circle?
    s1, s2 = (e1 - w1) % 360.0, (e2 - w2) % 360.0
    return ((w2 - w1) % 360.0 <= s1 or (e2 - w1) % 360.0 <= s1 or
            (w1 - w2) % 360.0 <= s2 or (e1 - w2) % 360.0 <= s2)


ev = json.load(open("data/results/events_selected.json"))
ev = ev["events"] if isinstance(ev, dict) else ev
ar = [e for e in ev if e["event_type"] == "atmospheric_river"]
in_curated = []
for b in blinks:
    d = b["gap_frame"][:8]
    for e in ar:
        s, t = e["start"][:10].replace("-", ""), e["end"][:10].replace("-", "")
        la1, la2, lo1, lo2 = _bb(e["bbox"])
        bl1, bl2, bo1, bo2 = b["bbox"]  # [latmin, latmax, lonmin, lonmax]
        if s <= d <= t and lon_overlap(lo1, lo2, bo1, bo2) and not (bl2 < la1 or bl1 > la2):
            in_curated.append({**b, "event": e["id"]})
            break

big = [b for b in blinks if b["px"] >= 500]
print("GATE_JSON:" + json.dumps({"frames": len(frames),
                  "objects_seen": len(chains),
                  "flicker_blinks": len(blinks),
                  "big_blinks_ge500px": len(big),
                  "in_curated_ar_window_and_bbox": in_curated,
                  "big_blinks": big}))
json.dump(blinks, open("scripts/review/sr13_blinks_v2.json", "w"))
