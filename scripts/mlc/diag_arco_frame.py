"""Diagnose the midlat_cyclone lane on ONE real ARCO MSLP frame.

The integrator's blocker (notes/channel_new_events.md, 2026-11-17 22:55): over
the 90-day dev window the lane scanned 361 frames and published 0 events, while
the PROOF window published 71 — and relaxing only the *closed-contour* knobs on
one real frame (2026-10-20 00Z) turned 0 nodes into 5-27. This script answers
"why" with numbers, on the lane's own read path:

1. what the ARCO reader (:func:`ewb_live.sources.mlc_era5.read_mslp_window`)
   hands the tracker — latitude order, longitude order, the grid step the
   contour mapper derives from it;
2. how many candidate minima the peak stage finds on that frame;
3. for the deepest candidates, whether their ``centre + 3 hPa`` isobar closes,
   how far its furthest point is measured (a) the way ``mlc_track`` does it
   today and (b) the way a correctly-ordered longitude axis gives, and how many
   nodes the frame yields as-read vs with the longitude axis sorted.

Run on the ewb LXC (it reads ARCO; nothing is written except --save-cutout):

    .venv/bin/python scripts/mlc/diag_arco_frame.py --time 2026-10-20T00
    .venv/bin/python scripts/mlc/diag_arco_frame.py --time 2026-10-20T00 \
        --save-cutout /root/scratch/mlc-fix/low_cutout.npz
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ewb_live.events import mlc_track as T          # noqa: E402
from ewb_live.sources import mlc_era5               # noqa: E402


def axis_report(name: str, c: np.ndarray) -> None:
    d = np.diff(c)
    print(f"  {name}: n={c.size} first={c[0]:.2f} last={c[-1]:.2f} "
          f"ascending={bool(np.all(d > 0))} "
          f"monotonic={bool(np.all(d > 0) or np.all(d < 0))} "
          f"mean|diff|={np.mean(np.abs(d)):.4f} max|diff|={np.max(np.abs(d)):.2f} "
          f"min|diff|={np.min(np.abs(d)):.4f} "
          f"grid_spacing()={T.grid_spacing(c):.4f}")


def sorted_lon(mslp: np.ndarray, lats: np.ndarray, lons: np.ndarray):
    """The same frame with the longitude axis in the ascending order the
    tracker's contract (and its contour mapper) requires."""
    order = np.argsort(lons, kind="stable")
    return mslp[:, order], lats, np.asarray(lons, float)[order]


def contour_stage(mslp: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                  t: dt.datetime, p: T.Params, top: int) -> None:
    """Candidate minima (pre-contour) and what the contour stage measures."""
    f = np.asarray(mslp, float)
    lat, lon = np.asarray(lats, float), np.asarray(lons, float)
    if lat[0] > lat[-1]:
        f, lat = f[::-1], lat[::-1]
    band = (np.abs(lat) >= p.min_abs_lat) & (np.abs(lat) <= p.max_abs_lat)
    below = int(np.count_nonzero(band[:, None] & (f < 1010.0)))
    print(f"  cells <1010 hPa in |lat| {p.min_abs_lat:.0f}-{p.max_abs_lat:.0f}: {below}"
          f"   band min {np.nanmin(f[band[:, None] & np.isfinite(f)]):.1f} hPa")

    from skimage.feature import peak_local_max

    mask = band[:, None] & np.isfinite(f) & (f < p.mslp_max_hPa)
    min_distance = T._degrees_to_gridpoints(p.min_distance_between_peaks_degrees, lat, lon)
    print(f"  peak separation min_distance = {min_distance} cells "
          f"(lon grid step used: {T.grid_spacing(lon):.4f} deg)")
    nx = f.shape[1]
    padcols = int(min(nx // 2, min_distance + 1))
    neg = np.where(mask, -f, -np.inf)
    padded = np.concatenate([neg[:, -padcols:], neg, neg[:, :padcols]], axis=1)
    cand = peak_local_max(padded, min_distance=min_distance, exclude_border=False,
                          threshold_abs=-p.mslp_max_hPa)
    peaks = [(int(r), int(c) - padcols) for r, c in cand if padcols <= int(c) < padcols + nx]
    print(f"  local minima (pre-merge, pre-contour): {len(peaks)}")

    dlat = T.grid_spacing(lat)
    halfwin = max(2, int(np.ceil(p.max_gc_distance_contour_degrees / dlat))) + 1
    rows = [r for r, _ in peaks]
    cols = [c for _, c in peaks]
    order = np.argsort(f[rows, cols])
    n_closed = n_within = 0
    print(f"  {'lat':>7} {'lon':>8} {'mslp':>7} {'closed?':>8} "
          f"{'r_contour(as)':>13} {'r_contour(true)':>15}")
    for i in order[:top]:
        r, c = peaks[i]
        centre = float(f[r, c])
        coslat = max(0.2, float(np.cos(np.radians(lat[r]))))
        lhw = int(np.ceil(halfwin / coslat))
        contours = T.find_contours_from_point(f, (r, c), centre + p.delta_hPa, halfwin, lhw)
        best_as, best_true, closed = np.inf, np.inf, False
        for con in contours:
            if len(con) < 4 or not np.allclose(con[0], con[-1]):
                continue
            if not T.measure.points_in_poly([(float(r), float(c))], con)[0]:
                continue
            closed = True
            d_as = T.find_valid_contour_from_point(con, (r, c), lat, lon)
            # the same contour, mapped through the TRUE column -> lon table
            ll = con.copy()
            lats_i = np.interp(np.clip(ll[:, 0], 0, lat.size - 1), np.arange(lat.size), lat)
            ci = np.mod(np.rint(ll[:, 1]).astype(int), lon.size)   # true cell longitudes
            best_true = min(best_true, float(np.max(T.haversine_deg(
                lats_i, lon[ci], lat[r], lon[c % lon.size]))))
            best_as = min(best_as, d_as)
        flag = "yes" if closed else "no"
        print(f"  {lat[r]:7.2f} {lon[c % lon.size]:8.2f} {centre:7.1f} {flag:>8} "
              f"{best_as:13.2f} {best_true:15.2f}")
        if closed:
            n_closed += 1
            if min(best_as, best_true) < p.max_gc_distance_contour_degrees:
                n_within += 1
    print(f"  enclosing closed +3 hPa rings among those {min(top, len(order))}: "
          f"{n_closed}, within {p.max_gc_distance_contour_degrees} deg: {n_within}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", default="2026-10-20T00", help="frame timestamp (UTC)")
    ap.add_argument("--top", type=int, default=12, help="deepest candidates to print")
    ap.add_argument("--save-cutout", default="", help="write a real low cutout npz")
    ap.add_argument("--center", default="", metavar="LAT,LON",
                    help="centre the cutout here instead of on the frame's deepest low")
    ap.add_argument("--cutout", type=int, default=0, nargs=3,
                    metavar=("NLAT", "NLON", "STEP"),
                    help="cutout size + frame step (hours) for --save-cutout")
    a = ap.parse_args()
    t = dt.datetime.fromisoformat(a.time)
    p = T.Params()

    times, mslp, lats, lons = mlc_era5.read_mslp_window(t, t)
    if not len(times):
        print("ARCO has no frame at", t)
        return
    k = times.index(min(times, key=lambda x: abs((x - t).total_seconds())))
    print(f"== frame {times[k]} (ARCO reader path, 1 frame, "
          f"{mslp.shape} {mslp.dtype}) ==")
    axis_report("lat", lats)
    axis_report("lon", lons)

    frame = np.asarray(mslp[k], np.float32)
    print("\n-- tracker as the reader hands it --")
    nodes = T.find_peaks_for_time_slice(frame, times[k], lats, lons, p)
    print(f"  nodes: {len(nodes)}")
    contour_stage(frame, lats, lons, times[k], p, a.top)

    lats2, lons2, frame2 = lats, lons, frame
    if not (np.all(np.diff(lats) > 0)):
        frame2, lats2 = frame2[::-1], lats[::-1]
    f3, lats3, lons3 = sorted_lon(frame2, lats2, lons2)
    print("\n-- same frame, longitude axis sorted ascending --")
    axis_report("lon", lons3)
    nodes3 = T.find_peaks_for_time_slice(f3, times[k], lats3, lons3, p)
    print(f"  nodes: {len(nodes3)}")
    contour_stage(f3, lats3, lons3, times[k], p, a.top)
    for n in nodes3[:5]:
        print(f"    {n.lat:7.2f} {n.lon:8.2f} {n.mslp:7.1f} hPa")

    if a.save_cutout:
        nlat, nlon, step_h = (a.cutout or (40, 60, 6))
        # centre the cutout on --center, else on the deepest closed low here
        if a.center:
            clat, clon = (float(x) for x in a.center.split(","))
        else:
            if not nodes3:
                print("no closed low on this frame; cutout not saved")
                return
            clat, clon = min(nodes3, key=lambda n: n.mslp).lat, \
                min(nodes3, key=lambda n: n.mslp).lon
        i = int(np.argmin(np.abs(lats3 - clat)))
        j = int(np.argmin(np.abs(lons3 - clon)))
        r0, r1 = max(0, i - nlat // 2), min(lats3.size, i + nlat // 2 + 1)
        c0, c1 = max(0, j - nlon // 2), min(lons3.size, j + nlon // 2 + 1)
        times2, m2, lats2, lons2 = mlc_era5.read_mslp_window(
            times[k] - dt.timedelta(hours=step_h), times[k] + dt.timedelta(hours=step_h))
        # the reader's longitude axis is rotated (0..179.75, then -180..-0.25);
        # put the stack in the same sorted frame the cutout indices refer to
        m2 = np.asarray(m2)[:, :, np.argsort(lons2, kind="stable")]
        lons2 = np.asarray(lons2, float)[np.argsort(np.asarray(lons2, float),
                                                   kind="stable")]
        lats2 = np.asarray(lats2, float)
        if lats2[0] > lats2[-1]:
            m2, lats2 = m2[:, ::-1], lats2[::-1]
        assert np.allclose(lats2, lats3) and np.allclose(lons2, lons3), \
            "cutout window and the wider read are on different grids"
        k0 = int(np.argmin([abs((tt - times[k]).total_seconds()) for tt in times2]))
        sub = np.asarray(m2[:, r0:r1, c0:c1], np.float32)
        np.savez_compressed(a.save_cutout,
                            mslp=sub[k0:k0 + 1], mslp_all=sub,
                            lats=lats3[r0:r1], lons=lons3[c0:c1],
                            times=np.array([tt.isoformat() for tt in times2]),
                            centre_time=np.array(times2[k0].isoformat()),
                            source=np.array("ERA5 ARCO mean_sea_level_pressure, hPa"))
        c_sub = sub[k0]
        rmin, cmin = np.unravel_index(int(np.argmin(c_sub)), c_sub.shape)
        print(f"saved cutout {a.save_cutout}: {sub.shape} around {clat:.2f},{clon:.2f} "
              f"lat[{lats3[r0]:.2f},{lats3[r1-1]:.2f}] lon[{lons3[c0]:.2f},{lons3[c1-1]:.2f}] "
              f"mslp[{sub.min():.1f},{sub.max():.1f}] hPa; "
              f"frame {times2[k0]} cutout min {c_sub.min():.1f} hPa at "
              f"{lats3[r0+rmin]:.2f},{lons3[c0+cmin]:.2f}")


if __name__ == "__main__":
    main()
