# Read-only: classify blink pixels (whole-component vs edge erosion).
import json, zlib, numpy as np
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
                         .reshape(LAT_N, RPW), axis=1)[:, :LON_N]


whole = edge = 0
whole_objs = 0
for tag in frames[::7]:
    i = frames.index(tag)
    if i == 0 or i == len(frames) - 1:
        continue
    a, b, c = ar(frames[i - 1]), ar(frames[i]), ar(frames[i + 1])
    bl = a & c & ~b
    if not bl.any():
        continue
    lab, _ = ndimage.label(a)          # parent components at t-1
    for sid in np.unique(lab[bl]):     # comps with blink px
        comp = lab == sid
        # fraction of the component still present at t (in a & c & b)
        still = (comp & b).sum() / max(comp.sum(), 1)
        if still < 0.1:
            whole += comp[bl].sum()
            whole_objs += 1
        else:
            edge += comp[bl].sum()
print(f"blink px in wholly-gone comps (<10% survives at t): {whole} ({whole_objs} objs)")
print(f"blink px in partially-eroded comps: {edge}")
