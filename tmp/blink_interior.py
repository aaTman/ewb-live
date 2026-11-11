# Read-only: are blinks track-span INTERIORS (gate should have caught) or
# span-edge/renumbering artifacts? Uses published .trk + index tracks{}.
import json, zlib, numpy as np
from pathlib import Path

out = Path("/opt/ewb-live/data/results/raw_exceedance")
idx = json.load(open(out / "index.json"))
frames = idx["frames"]
pos = {f: i for i, f in enumerate(frames)}
LAT_N, LON_N, RPW = 721, 1440, 180


def ar(tag):
    raw = zlib.decompress((out / f"{tag}.bin").read_bytes())
    n = LAT_N * RPW
    return np.unpackbits(np.frombuffer(raw[n * 2:n * 3], np.uint8)
                         .reshape(LAT_N, RPW), axis=1)[:, :LON_N] > 0


interior = edge = 0
worst = []
for i in range(1, len(frames) - 1):
    a, b, c = ar(frames[i - 1]), ar(frames[i]), ar(frames[i + 1])
    bl = a & c & ~b
    if not bl.any():
        continue
    la = zlib.decompress((out / f"{frames[i-1]}.trk").read_bytes())
    lc = zlib.decompress((out / f"{frames[i+1]}.trk").read_bytes())
    A = np.frombuffer(la, np.uint16).reshape(LAT_N, LON_N)
    C = np.frombuffer(lc, np.uint16).reshape(LAT_N, LON_N)
    both = (A > 0) & (C > 0) & ~b          # blink where SAME region labeled t-1 and t+1
    # same-id continuity: id present at t-1 and at t+1 (track span covers t)
    ids_a = set(np.unique(A[A > 0]).tolist())
    ids_c = set(np.unique(C[C > 0]).tolist())
    both_id = np.isin(A, list(ids_a & ids_c)) & bl
    interior += int(both_id.sum())
    edge += int(bl.sum()) - int(both_id.sum())
    if both_id.sum() > 500:
        worst.append((frames[i], int(both_id.sum())))
print(f"blink px where the track id exists at t-1 AND t+1 (span interior — "
      f"a track the flicker gate should have caught): {interior}")
print(f"blink px at id-boundaries (id lost/regained — renumber/track break): {edge}")
print("worst interior frames:", sorted(worst, key=lambda x: -x[1])[:6])
