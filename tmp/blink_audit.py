# Read-only blink audit (design lane): per-step sid presence, box data.
import json, zlib, numpy as np
from pathlib import Path

out = Path("/opt/ewb-live/data/results/raw_exceedance")
idx = json.load(open(out / "index.json"))
frames = idx["frames"]
pos = {f: i for i, f in enumerate(frames)}
LAT_N, LON_N = 721, 1440


def trk(tag):
    b = zlib.decompress((out / f"{tag}.trk").read_bytes())
    return np.frombuffer(b, np.uint16).reshape(LAT_N, LON_N)


longish = [(t, v) for t, v in idx["tracks"].items()
           if v["t0"] in pos and pos[v["t1"]] - pos[v["t0"]] >= 8]
print("tracks span>=8:", len(longish))
cache = {}


def sidset(tag):
    if tag not in cache:
        cache[tag] = set(np.unique(trk(tag)).tolist()) - {0}
    return cache[tag]


dropouts = 0
examples = []
for tid, v in longish[:15]:
    lo, hi = pos[v["t0"]], pos[v["t1"]]
    miss = [frames[i] for i in range(lo, hi + 1)
            if int(tid) not in sidset(frames[i])]
    if miss:
        dropouts += 1
        examples.append((tid, v, miss[:4]))
print("longish tracks with interior dropouts:", dropouts, "/", len(longish[:15]))
for e in examples:
    print(e)

jx0, jx1 = int((130 + 180) / 0.25), int((155 + 180) / 0.25)
jy0, jy1 = int((30 + 90) / 0.25), int((50 + 90) / 0.25)
print("\nJapan box per-step top sids (Oct 17 00Z .. Oct 20 18Z):")
for f in frames:
    if not ("2026101700" <= f <= "2026102018"):
        continue
    a = trk(f)
    box = a[jy0:jy1, jx0:jx1]
    vals, cnts = np.unique(box[box > 0], return_counts=True)
    top = sorted(zip(vals.tolist(), cnts.tolist()), key=lambda x: -x[1])[:4]
    print(f, top)
