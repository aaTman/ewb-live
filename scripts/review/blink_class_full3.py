# Replicate design lane tmp/blink_class.py on FULL timeline, memory-safe:
# bincount aggregation instead of per-component full-grid `lab == sid` copies.
# Same semantics: comp wholly-gone if <10% of its t-1 pixels survive at t.
import json, sys, zlib, numpy as np
from pathlib import Path
from scipy import ndimage

out = Path("/opt/ewb-live/data/results/raw_exceedance")
idx = json.load(open(out / "index.json"))
frames = idx["frames"]
LAT_N, LON_N = 721, 1440
RPW = (LON_N + 7) // 8

def ar(tag):
    raw = zlib.decompress((out / f"{tag}.bin").read_bytes())
    n = LAT_N * RPW
    return np.unpackbits(np.frombuffer(raw[n * 2:n * 3], np.uint8)
                         .reshape(LAT_N, RPW), axis=1)[:, :LON_N].astype(bool)

whole = edge = whole_objs = blink_frames = 0
for i in range(1, len(frames) - 1):
    if i % 50 == 0:
        print(f"frame {i}/{len(frames)}", file=sys.stderr, flush=True)
    a = ar(frames[i - 1]); b = ar(frames[i]); c = ar(frames[i + 1])
    bl = a & c & ~b
    if not bl.any():
        a = b = c = None
        continue
    blink_frames += 1
    lab, nlab = ndimage.label(a)
    flat = lab.ravel()
    total = np.bincount(flat, minlength=nlab + 1)              # comp size at t-1
    still = np.bincount(flat[b.ravel()], minlength=nlab + 1)   # comp px at t
    blp = np.bincount(flat[bl.ravel()], minlength=nlab + 1)    # blink px per comp
    sids = np.nonzero(blp)[0]
    is_whole = still[sids] < 0.1 * total[sids]
    whole += blp[sids][is_whole].sum(); edge += blp[sids][~is_whole].sum()
    whole_objs += int(is_whole.sum())
    a = b = c = lab = flat = None
print(f"blink frames: {blink_frames}")
print(f"blink px in wholly-gone comps (<10% survives at t): {whole} ({whole_objs} objs)")
print(f"blink px in partially-eroded comps: {edge}")
