"""D2 pixel audit: no raw AR frame pixel may sit within TC_SCREEN_KM of a
TC track center at a time within +/-TC_SCREEN_HOURS (the screen contract).

Run on the box (reads data/results/raw_exceedance + the IBTrACS cache):
    ./ops/remote.sh run python scripts/review/d2_pixel_audit.py

Exit 0 = clean; 1 = violations (prints worst frame/pixel/track). Re-run after
any LAYER_REV rebuild. (2026-11-09: v4 frames had 51 tracks inside the buffer,
including a 0.0 km LALA hit, from the durbackfill collision — this script is
the gate that says v5 fixed it.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ewb_live.events.tc_screen import TC_SCREEN_KM, TC_SCREEN_HOURS, load_ibtracs_tracks  # noqa: E402
from ewb_live.viz.exceedance import AR_LABELS_NAME, LAT_N, LON_N, frame_layers, labels_from_bytes  # noqa: E402


def main() -> int:
    out = ROOT / "data" / "results" / "raw_exceedance"
    index = json.loads((out / "index.json").read_text())
    frames = sorted(index["frames"])
    if not frames:  # all-AR-fail path publishes a blank index — never certify a vacuous PASS
        raise SystemExit("D2: index has no frames — refusing to certify an empty window")
    # one shared lat/lon grid, ascending lat (frame decode convention)
    lats = np.linspace(-90.0, 90.0, LAT_N)
    lons = np.linspace(-180.0, 180.0, LON_N, endpoint=False)  # display grid: col 0 = -180
    tracks = load_ibtracs_tracks({int(t[:4]) + d for t in frames for d in (-1, 0, 1)})
    if tracks is None:
        raise SystemExit("D2 audit: IBTrACS unavailable (no cache, fetch failed) — "
                         "refuse to report CLEAN without the screen's input")
    pts = tracks[["latitude", "longitude", "valid_time", "storm_id"]].dropna()
    times = np.array([np.datetime64(t.to_pydatetime().replace(tzinfo=None), "h")
                      for t in pts["valid_time"]], dtype="datetime64[h]")
    if not len(times):
        raise SystemExit("D2: IBTrACS cache has no usable points — refusing to certify")
    # audit-by-nobody guard: frames past the IBTrACS points' coverage (frozen/
    # stale cache after a fetch failure) would silently skip the newest frames
    ft_last = np.datetime64(f"{frames[-1][:4]}-{frames[-1][4:6]}-{frames[-1][6:8]}T{frames[-1][8:10]}", "h")
    if times.max() < ft_last - np.timedelta64(3, "D"):
        raise SystemExit(f"D2: IBTrACS coverage ends {times.max()} but frames run to "
                         f"{frames[-1]} — cannot certify the tail of the window")
    worst_km, detail = np.inf, None
    offenders: set[str] = set()
    offender_ids: set = set()
    lab2id = {int(k): v for k, v in index.get("tracks", {}).items()}
    for tag in frames:
        ft = np.datetime64(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}T{tag[8:10]}", "h")
        near = np.where(np.abs((times - ft) / np.timedelta64(1, "h")) <= TC_SCREEN_HOURS)[0]
        if not len(near):
            continue
        mask = frame_layers((out / f"{tag}.bin").read_bytes(), (LAT_N, LON_N))[2]
        ys, xs = np.nonzero(mask)
        if not len(ys):
            continue
        for i in near:
            la, lo = float(pts.iloc[i]["latitude"]), float(pts.iloc[i]["longitude"]) % 360.0
            dlat = (TC_SCREEN_KM + 60) / 111.32
            rows = np.nonzero(np.abs(lats - la) <= dlat)[0]
            dlon = (TC_SCREEN_KM + 60) / (111.32 * max(np.cos(np.radians(la)), 1e-3))
            cols = np.nonzero(np.abs(((lons - lo + 180.0) % 360.0) - 180.0) <= dlon)[0]
            if not len(rows) or not len(cols):
                continue
            sel_r, sel_c = np.ix_(rows, cols)
            sub = mask[sel_r, sel_c]
            ry, rx = np.nonzero(sub)
            if not len(ry):
                continue
            plat, plon = lats[rows[ry]], lons[cols[rx]]
            dl = np.radians(plat - la)
            do = np.radians(plon - lo)
            a = np.sin(dl / 2) ** 2 + np.cos(np.radians(la)) * np.cos(np.radians(plat)) * np.sin(do / 2) ** 2
            km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
            j = int(np.argmin(km))
            if km[j] < worst_km:
                worst_km = km[j]
                detail = (tag, float(plat[j]), float(plon[j]), str(pts.iloc[i]["storm_id"]), str(times[i]))
            if km[j] <= TC_SCREEN_KM:
                lab = int(labels_from_bytes((out / f"{tag}{AR_LABELS_NAME}").read_bytes(),
                                            (LAT_N, LON_N))[rows[ry[j]], cols[rx[j]]])
                offenders.add(f"{lab2id.get(lab, 'label:' + str(lab))} ({km[j]:.0f} km @ {tag}, {pts.iloc[i]['storm_id']})")
                offender_ids.add(lab2id.get(lab, 'label:' + str(lab)))
    if worst_km > TC_SCREEN_KM:
        print(f"D2 CLEAN: min AR-px->TC-center = {worst_km:.1f} km > {TC_SCREEN_KM} "
              f"(frames={len(frames)}, TC pts gated)")
        return 0
    print(f"D2 VIOLATION: min = {worst_km:.1f} km at {detail}")
    for o in sorted(offenders)[:60]:
        print("  within-buffer track:", o)
    print(f"total distinct within-buffer tracks: {len(offender_ids)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
