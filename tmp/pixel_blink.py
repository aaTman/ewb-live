# Read-only: pixel-level blink quantification (design lane).
# A BLINK = a ground pixel that is AR at step t-1 and t+1 but not at t
# (the eye sees an object vanish for exactly one 6-h frame then return).
import json, zlib, numpy as np
from pathlib import Path

out = Path("/opt/ewb-live/data/results/raw_exceedance")
idx = json.load(open(out / "index.json"))
frames = idx["frames"]
LAT_N, LON_N = 721, 1440


def ar(tag):
    """AR plane from the published .bin (zlib of 3 packbits planes)."""
    raw = zlib.decompress((out / f"{tag}.bin").read_bytes())
    n = LAT_N * ((LON_N + 7) // 8)
    a = np.unpackbits(np.frombuffer(raw[n * 2:n * 3], np.uint8)
                      .reshape(LAT_N, (LON_N + 7) // 8), axis=1)[:, :LON_N]
    return a


tot = blink = 0
per_frame = []
for i in range(1, len(frames) - 1):
    a, b, c = ar(frames[i - 1]), ar(frames[i]), ar(frames[i + 1])
    bl = (a & c & ~b).sum()
    tot += bl
    blink = max(blink, bl)
    if bl:
        per_frame.append((frames[i], int(bl), int(a.sum()), int(b.sum())))
print("steps:", len(frames), " blink-pixels total:", tot)
print("frames with any blink:", len(per_frame), "/", len(frames) - 2)
per_frame.sort(key=lambda x: -x[1])
print("worst 8:", per_frame[:8])
# Japan box specifically around Oct 17-19
jx0, jx1 = int((130 + 180) / 0.25), int((155 + 180) / 0.25)
jy0, jy1 = int((30 + 90) / 0.25), int((50 + 90) / 0.25)
for i in range(1, len(frames) - 1):
    if not ("2026101600" <= frames[i] <= "2026101918"):
        continue
    a = ar(frames[i - 1])[jy0:jy1, jx0:jx1]
    b = ar(frames[i])[jy0:jy1, jx0:jx1]
    c = ar(frames[i + 1])[jy0:jy1, jx0:jx1]
    print(frames[i], "box ar px:", int(b.sum()), "blink:", int((a & c & ~b).sum()))
