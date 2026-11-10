"""sr14d: maps for top-5 rejected CANDIDATES, drawn from mirror IVT (>=250)
in the candidate bbox on its max-land px step. Run on box."""
import datetime as dt
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, ".")
from ewb_live.viz.build import land_mask, _ar_inputs_window, _ivt_and_mask

R = Path("data/results")
lats = -90 + 0.25 * np.arange(720)
lons = -180 + (np.arange(1440) + 0.5) * 0.25
land = land_mask(lats, lons)

TOP = ["eb42387c81a3", "06ceb4d8d4fe", "49a026787716", "bcc401cecaad", "df7ad7ff2f51"]
cand = {e["id"]: e for e in json.load(open(R / "events.json"))
        if e["event_type"] == "atmospheric_river"}
rej = {"eb42387c81a3": "F: lon span 90 > 60 cap (strongest AR in window)",
       "06ceb4d8d4fe": "F: landfall_days 2 < 3 (same track as shipped 0df36e)",
       "49a026787716": "F: landfall_days 2 < 3",
       "bcc401cecaad": "quota: severity rank 17 (peak IVT 938)",
       "df7ad7ff2f51": "F: landfall_days 2 < 3 (IVT 980)"}

for n, cid in enumerate(TOP, 1):
    c = cand[cid]
    eb = c["bbox"]
    bb = (eb[0] - 3, eb[1] + 3, eb[2] - 3, eb[3] + 3)
    t0 = dt.datetime.strptime(c["start"][:10], "%Y-%m-%d")
    t1 = dt.datetime.strptime(c["end"][:10], "%Y-%m-%d") + dt.timedelta(days=1)
    ds = _ar_inputs_window(t0, t1, bb)
    lat_native = np.asarray(ds["latitude"].values, float)
    lon_native = np.asarray(ds["longitude"].values, float) % 360.0
    span_o = (bb[3] - bb[2]) % 360.0
    lw = bb[2] + np.arange(int(span_o / 0.25) + 1) * 0.25  # continuous eastward lon
    latw = lats[(lats >= bb[0]) & (lats <= bb[1])]
    li = np.abs(lat_native[:, None] - latw[None, :]).argmin(0)
    lo = np.abs(lon_native[None, :] - lw[:, None] % 360.0).argmin(1)
    rows = np.clip(((latw + 90.0) / 0.25).astype(int), 0, 719)
    gj = np.abs(((lons[None, :] - lw[:, None] + 180.0) % 360.0) - 180.0).argmin(1)
    landw = land[np.ix_(rows, gj)]
    best = None
    for k in range(ds.sizes["valid_time"]):
        iv, _ = _ivt_and_mask(ds.isel(valid_time=k))
        m = np.asarray(iv) >= 250
        crop = m[np.clip(li, 0, m.shape[0] - 1)][:, lo]
        lp = int((crop & landw).sum())
        if best is None or lp > best[0]:
            best = (lp, str(ds.valid_time.values[k])[:16], crop)
    lp, ts, crop = best
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.imshow(landw, cmap="gray_r", vmin=0, vmax=1, alpha=.45,
              extent=[lw[0], lw[-1], latw[0], latw[-1]], origin="lower", aspect="auto")
    gy, gx = np.nonzero(crop)
    ax.scatter(lw[gx], latw[gy], s=2, c="crimson")
    d = c["details"] or {}
    ax.set_title(f"{cid}  {c['start'][:10]}..{c['end'][:10]}  peak step {ts}\n{rej[cid]}\n"
                 f"IVT {c['peak_value']:.0f} | land px/day {d.get('peak_land_pixels')} | "
                 f"landfall days {d.get('landfall_days')} | window land px {lp}", fontsize=9)
    ax.grid(alpha=.2)
    fig.savefig(f"/tmp/sr-arfunnel-c{n}.png", dpi=110, bbox_inches="tight")
    print("wrote", n, cid, "landpx", lp, flush=True)
