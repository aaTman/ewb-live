"""sr14b: maps for the top-5 rejected-but-landfalling AR tracks. Run on box."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, ".")
from ewb_live.viz.exceedance import labels_from_bytes, LAT_N, LON_N
from ewb_live.viz.build import land_mask

R = Path("data/results")
X = R / "raw_exceedance"
idx = json.load(open(X / "index.json"))
led = json.load(open(X / "ar_tracks.json"))
id2lab = {v: int(k) for k, v in led["tracks"].items()}
lats = -90 + 0.25 * np.arange(LAT_N)
lons = -180 + (np.arange(LON_N) + 0.5) * 0.25
land = land_mask(lats, lons)

f = json.load(open("/tmp/sr14_funnel.json"))
rows = [r for r in f["funnel"]
        if r["reject"] not in ("SELECTED",) and not r["reject"].startswith("OCEANIC")
        and r["land_px"] > 0]
rows.sort(key=lambda r: (r["peak_land"] * (r["peak_ivt"] or 400)), reverse=True)
top = rows[:5]
print(json.dumps([t["track"] for t in top]))

for n, r in enumerate(top, 1):
    lbl = id2lab[r["track"]]
    days = [fr for fr in idx["frames"] if fr[:8] >= r["days"][0] and fr[:8] <= r["days"][1]]
    best = (0, None)
    for fr in days:
        lab = labels_from_bytes(open(X / f"{fr}.trk", "rb").read(), (LAT_N, LON_N))
        cnt = int((lab == lbl).sum())
        if cnt > best[0]:
            best = (cnt, fr, lab)
    _, fr, lab = best
    m = lab == lbl
    ys, xs = np.nonzero(m)
    # continuous cols about the first occupied cell (A4 pattern): a
    # dateline-crossing track must not crop as a full-globe strip
    xs = xs[0] + ((xs - xs[0] + LON_N // 2) % LON_N) - LON_N // 2
    pad = 5
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, LAT_N)
    gx = np.arange(xs.min() - pad, xs.max() + pad + 1)  # continuous cell cols
    lonc = -180.0 + (gx + 0.5) * 0.25  # may exceed 180; plot axis follows
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.imshow(land[y0:y1][:, np.mod(gx, LON_N)], cmap="gray_r", vmin=0, vmax=1, alpha=.45,
              extent=[lonc[0], lonc[-1], lats[y0], lats[min(y1, LAT_N) - 1]],
              origin="lower", aspect="auto")
    ax.scatter(lonc[np.searchsorted(gx, xs)], lats[ys], s=2, c="crimson")
    ax.set_title(f"{r['track']}  ({fr} peak day)\n{r['reject']}\n"
                 f"land px {r['land_px']} | peak land/day {r['peak_land']} | "
                 f"IVT {r['peak_ivt'] or 0:.0f} | {r['n_days']}d {r['days'][0]}..{r['days'][1]}",
                 fontsize=9)
    ax.grid(alpha=.2)
    fig.savefig(f"/tmp/sr-arfunnel-{n}.png", dpi=110, bbox_inches="tight")
    print("wrote", n)
