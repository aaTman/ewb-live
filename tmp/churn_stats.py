# Read-only: v5 asset sanity + per-step object churn stats (design lane).
import json, zlib, numpy as np
from pathlib import Path

out = Path("/opt/ewb-live/data/results/raw_exceedance")
idx = json.load(open(out / "index.json"))
frames = idx["frames"]
LAT_N, LON_N = 721, 1440


def dec(tag, name, dtype):
    b = zlib.decompress((out / f"{tag}{name}").read_bytes())
    return np.frombuffer(b, dtype).reshape(LAT_N, LON_N)


mismatch = 0
prev = None
appear = vanish = 0
one_step = 0
sids_seen = {}
for f in frames[::4]:                      # every 4th step: 84 frames
    lab = dec(f, ".trk", np.uint16)
    cur = set(np.unique(lab[lab > 0]).tolist())
    if prev is not None:
        appear += len(cur - prev)
        vanish += len(prev - cur)
        for s in cur:
            sids_seen[s] = sids_seen.get(s, 0) + 1
    prev = cur
one_step = sum(1 for v in sids_seen.values() if v == 1)
print(f"sampled {len(frames[::4])} steps (every 4th): sid appear={appear} vanish={vanish} "
      f"per-step avg: +{appear/83:.1f} / -{vanish/83:.1f}")
print(f"sids seen: {len(sids_seen)}, one-step-only (in sampled grid): {one_step}")
