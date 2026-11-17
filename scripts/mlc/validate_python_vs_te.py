"""Validate the pure-Python mlc tracker against the TempestExtremes PROOF.

Runs BOTH trackers on the *same* MSLP field — the daily netCDF files extracted
from ERA5 ARCO — and reports the differences honestly:

* track counts at the PROOF recipe (TE ``--closedcontourcmd msl,3.0,6.0,0
  --mergedist 6.0 --minabslat 5.0 --range 6 --mintime 48h --maxgap 12h``);
* per-track matching (a TE track is matched when a Python fix lies within
  ``--match-deg`` of one of its fixes at the same step) and the position error
  over matched fixes at matched steps;
* the lifetime / min-pressure / displacement / bomb distributions;
* the named-storm check (Storm Ciaran, landfall Finistere 2 Nov 2023), exactly
  the check mlc-research pinned in ``analyze_tracks.py``;
* wall time and peak RSS of each tracker (the profile the mlc lane is held to).

Everything is scratch-on-tmpfs: the ewb root disk is at 99 %, so the default
scratch is ``/dev/shm/ewb-mlc``. The MSLP netCDFs are written with the same
helper the PROOF used (``scripts/mlc/extract_month.py``, ``--nowinds``).

  python scripts/mlc/validate_python_vs_te.py --start 2023-10-26T00 --end 2023-11-30T18 \
      --out notes/mlc_python_validation.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ewb_live.events import mlc as MLC                                # noqa: E402
from ewb_live.events import mlc_track as T                            # noqa: E402

TE_DEFAULT = "/opt/ewb-live/opt/mm/envs/te/bin"


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def extract(start: dt.datetime, end: dt.datetime, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.combine(start.date(), dt.time())
    files: list[Path] = []
    while day <= end:
        f = out / f"etcc_{day:%Y%m%d}.nc"
        if not f.exists():
            run([sys.executable, "scripts/mlc/extract_month.py",
                 "--start", f"{day:%Y-%m-%dT%H}",
                 "--end", f"{min(day + dt.timedelta(days=1), end):%Y-%m-%dT%H}",
                 "--out", str(out), "--nowinds"])
        files += sorted(Path(p) for p in glob.glob(str(out / f"etcc_{day:%Y%m%d}*.nc")))
        day += dt.timedelta(days=1)
    return sorted(set(files))


def te_track(files: list[Path], work: Path, te_bin: str) -> dict:
    """TempestExtremes DetectNodes + StitchNodes with the PROOF recipe."""
    b = Path(te_bin)
    work.mkdir(parents=True, exist_ok=True)
    lst = work / "filelist.txt"
    lst.write_text("\n".join(str(f) for f in files) + "\n")
    t0 = time.perf_counter()
    run([str(b / "DetectNodes"), "--in_data_list", str(lst),
         "--searchbymin", "msl", "--closedcontourcmd", "msl,3.0,6.0,0",
         "--mergedist", "6.0", "--minabslat", "5.0",
         "--outputcmd", "msl,min,2", "--out_header",
         "--out", str(work / "nodes.XXXXXX.dat")], stdout=subprocess.DEVNULL)
    nodes = sorted(work.glob("nodes.*.dat"))
    (work / "nodelist.txt").write_text("\n".join(str(f) for f in nodes) + "\n")
    detect_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    out_csv = work / "tracks_te.csv"
    run([str(b / "StitchNodes"), "--in_list", str(work / "nodelist.txt"),
         "--in_fmt", "lon,lat,msl", "--range", "6", "--mintime", "48h",
         "--maxgap", "12h", "--out", str(out_csv), "--out_file_format", "csv",
         "--out_seconds"], stdout=subprocess.DEVNULL)
    stitch_s = time.perf_counter() - t1
    n_nodes = sum(max(0, len(f.read_text().splitlines()) - 1) for f in nodes)
    return {"csv": out_csv, "n_nodes": n_nodes, "detect_s": detect_s,
            "stitch_s": stitch_s, "n_files": len(files)}


def te_tracks(csv: Path) -> dict[int, list[tuple[dt.datetime, float, float, float]]]:
    import csv as _csv

    out: dict[int, list] = {}
    with open(csv) as fh:
        rows = list(_csv.DictReader(fh))
    if not rows:
        return out
    for r in rows:
        t = dt.datetime(int(r["year"]), int(r["month"]), int(r["day"]),
                        int(r["hour"]), int(r.get("minute") or 0),
                        int(r.get("second") or 0))
        out.setdefault(int(r["track_id"]), []).append(
            (t, float(r["lat"]), float(r["lon"]), float(r["msl"])))
    return {k: sorted(v) for k, v in out.items()}


def read_field(files: list[Path]):
    """The extracted netCDFs back as one (time, lat, lon) hPa stack + coords."""
    import xarray as xr

    ds = xr.open_mfdataset([str(f) for f in files], combine="by_coords")
    da = ds["msl"].transpose("time", "lat", "lon").load()
    lats = np.asarray(da.lat.values, float)
    lons = np.asarray(da.lon.values, float)
    if lats[0] > lats[-1]:
        da = da.isel(lat=slice(None, None, -1))
        lats = lats[::-1]
    times = [t.astype("datetime64[h]").astype(dt.datetime)
             for t in np.asarray(da.time.values)]
    mslp = np.ascontiguousarray(np.asarray(da.values, np.float32))
    ds.close()
    return times, mslp, lats, lons


def stats(tracks) -> dict:
    if not tracks:
        return {"n": 0}
    dur = np.array([t.duration_h for t in tracks])
    msl = np.array([t.min_mslp for t in tracks])
    disp = np.array([t.displacement_km for t in tracks])
    return {"n": len(tracks), "median_life_h": float(np.median(dur)),
            "median_min_mslp": float(np.median(msl)), "deepest": float(msl.min()),
            "median_disp_km": float(np.median(disp)),
            "n_disp_ge_1000": int((disp >= 1000).sum()),
            "n_bomb": int(sum(1 for t in tracks if t.bomb_ratio()[0])),
            "mean_abs_genesis_lat": float(np.mean([abs(t.genesis[1]) for t in tracks]))}


def te_stats(tr) -> dict:
    dur, msl, disp = [], [], []
    bomb = 0
    for pts in tr.values():
        lat = np.array([p[1] for p in pts]); lon = np.array([p[2] for p in pts])
        m = np.array([p[3] for p in pts]); ts = [p[0] for p in pts]
        dur.append((ts[-1] - ts[0]).total_seconds() / 3600.0)
        msl.append(float(m.min()))
        disp.append(float(T.haversine_km(lat[0], lon[0], lat[-1], lon[-1])))
        fall = m[4:] - m[:-4]
        if fall.size:
            thr = 24.0 * np.abs(np.sin(np.radians(lat[4:]))) / np.sin(np.radians(60.0))
            bomb += int(np.any(fall <= -thr))
    dur, msl, disp = np.array(dur), np.array(msl), np.array(disp)
    return {"n": len(tr), "median_life_h": float(np.median(dur)),
            "median_min_mslp": float(np.median(msl)), "deepest": float(msl.min()),
            "median_disp_km": float(np.median(disp)),
            "n_disp_ge_1000": int((disp >= 1000).sum()), "n_bomb": bomb,
            "mean_abs_genesis_lat": float(np.mean([abs(next(iter(v))[1]) for v in tr.values()]))}


def match_tracks(te, py, match_deg=6.0):
    """Match TE tracks to Python tracks through shared-time fixes within match_deg."""
    by_time: dict[dt.datetime, list] = {}
    for tr in py:
        for k, t in enumerate(tr.times):
            by_time.setdefault(t, []).append((tr, k))
    matched, pos_err, n_fixed = {}, [], 0
    for tid, pts in te.items():
        votes: dict[int, int] = {}
        for (t, lat, lon, _m) in pts:
            for (tr, k) in by_time.get(t, ()):
                if T.haversine_deg(lat, lon, tr.lat[k], tr.lon[k]) <= match_deg:
                    votes[id(tr)] = votes.get(id(tr), 0) + 1
        if not votes:
            continue
        best = max(votes.items(), key=lambda kv: kv[1])
        tr = next(x for x in py if id(x) == best[0])
        matched[tid] = tr
        errs = [T.haversine_deg(lat, lon, tr.lat[tr.times.index(t)],
                                tr.lon[tr.times.index(t)])
                for (t, lat, lon, _m) in pts if t in tr.times]
        pos_err += errs
        n_fixed += best[1]
    err = np.array(pos_err) if pos_err else np.zeros(0)
    return {"te_matched": len(matched), "te_total": len(te),
            "matched_fixes": n_fixed,
            "median_pos_err_deg": float(np.median(err)) if err.size else float("nan"),
            "p90_pos_err_deg": float(np.percentile(err, 90)) if err.size else float("nan"),
            "max_pos_err_deg": float(err.max()) if err.size else float("nan")}


def find_storm(tracks, when, lat, lon, within_h=12, match_deg=8.0):
    """The track with a fix nearest (when, lat, lon) — the known-storm check."""
    best, best_d = None, 1e18
    for tr in tracks:
        for k, t in enumerate(tr.times):
            if abs((t - when).total_seconds()) > within_h * 3600:
                continue
            d = T.haversine_km(lat, lon, tr.lat[k], tr.lon[k])
            if d < best_d:
                best, best_d = (tr, k, d, t), d
    if best is None:
        return None
    tr, k, d, t = best
    fall = tr.pressure_fall_24h()
    return {"t": t, "lat": float(tr.lat[k]), "lon": float(tr.lon[k]),
            "dist_km": float(d), "msl_at_fix": float(tr.mslp[k]),
            "min_mslp": tr.min_mslp, "min_t": tr.times[tr.argmin],
            "lifetime_h": tr.duration_h, "displacement_km": tr.displacement_km,
            "max_24h_fall": float(np.nanmin(fall)) if np.isfinite(fall).any() else float("nan"),
            "bomb": tr.bomb_ratio()[0], "bomb_ratio": tr.bomb_ratio()[2],
            "n_fixes": len(tr)}


def storm_from_te(tr, when, lat, lon, within_h=12, match_deg=8.0):
    class W:  # adapt TE rows to the Track API used by find_storm
        times = None

        def __init__(self, pts):
            self.times = [p[0] for p in pts]
            self.lat = np.array([p[1] for p in pts])
            self.lon = np.array([p[2] for p in pts])
            self.mslp = np.array([p[3] for p in pts])
            self.filled = np.zeros(len(pts), bool)

        duration_h = property(lambda s: (s.times[-1] - s.times[0]).total_seconds() / 3600)
        min_mslp = property(lambda s: float(s.mslp.min()))
        argmin = property(lambda s: int(np.argmin(s.mslp)))
        displacement_km = property(lambda s: float(T.haversine_km(s.lat[0], s.lon[0], s.lat[-1], s.lon[-1])))
        pressure_fall_24h = T.Track.pressure_fall_24h
        bomb_ratio = T.Track.bomb_ratio
        pressure_24h_or_zero = T.Track.pressure_24h_or_zero

    return find_storm([W(pts) for pts in tr.values()], when, lat, lon, within_h, match_deg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-10-26T00")
    ap.add_argument("--end", default="2023-11-30T18")
    ap.add_argument("--scratch", default="/dev/shm/ewb-mlc")
    ap.add_argument("--te-bin", default=TE_DEFAULT)
    ap.add_argument("--out", default="notes/mlc_python_validation.md")
    ap.add_argument("--skip-te", action="store_true", help="python side only")
    a = ap.parse_args()
    start = dt.datetime.fromisoformat(a.start)
    end = dt.datetime.fromisoformat(a.end)
    scratch = Path(a.scratch)
    peak0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    files = extract(start, end, scratch / "nc")
    print(f"extracted {len(files)} daily files", flush=True)

    te_info = None
    if not a.skip_te and Path(a.te_bin, "DetectNodes").exists():
        te_info = te_track(files, scratch / "te", a.te_bin)
        print("TE:", te_info, flush=True)

    t0 = time.perf_counter()
    times, mslp, lats, lons = read_field(files)
    read_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    res = T.generate_mlc_tracks(mslp, lats, lons, times)
    py_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    events = MLC.scan_mlc_events(start.date(), end.date(),
                                 fields=(times, mslp, lats, lons))
    ev_s = time.perf_counter() - t0
    del mslp
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    lines: list[str] = []
    lines.append("# mlc lane: pure-Python tracker vs the TempestExtremes PROOF\n")
    lines.append(f"Window `{start:%Y-%m-%d %H}Z .. {end:%Y-%m-%d %H}Z`, "
                 f"{len(times)} frames of ERA5 MSLP at 0.25 deg, "
                 f"grid {lats.size}x{lons.size}, host `{os.uname().nodename}`, "
                 f"{os.cpu_count()} cores, run {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z.\n")
    lines.append("## Timing / memory\n")
    lines.append("| stage | wall (s) |")
    lines.append("|---|---|")
    lines.append(f"| read {len(times)} frames back from the extracted netCDFs | {read_s:.1f} |")
    lines.append(f"| **python tracker** (detect + stitch + gap fill) | {py_s:.1f} "
                 f"({py_s / max(len(times), 1):.2f} s/frame) |")
    lines.append(f"| python qualification + event mapping | {ev_s:.1f} |")
    if te_info:
        lines.append(f"| TempestExtremes DetectNodes ({te_info['n_files']} files) | "
                     f"{te_info['detect_s']:.1f} |")
        lines.append(f"| TempestExtremes StitchNodes | {te_info['stitch_s']:.2f} |")
    lines.append(f"| python process peak RSS | {peak:.0f} MB (was {peak0:.0f} MB at start) |\n")

    lines.append("## Track population (tracker output, before event screens)\n")
    pystats = stats(res.tracks)
    lines.append("| quantity | python | TempestExtremes |")
    lines.append("|---|---|---|")
    if te_info:
        tr = te_tracks(te_info["csv"])
        tstats = te_stats(tr)
        rows = [("nodes", res.n_nodes, te_info["n_nodes"]),
                ("tracks >= 48 h", pystats["n"], tstats["n"]),
                ("median lifetime (h)", pystats["median_life_h"], tstats["median_life_h"]),
                ("median min MSLP (hPa)", pystats["median_min_mslp"], tstats["median_min_mslp"]),
                ("deepest (hPa)", pystats["deepest"], tstats["deepest"]),
                ("median displacement (km)", pystats["median_disp_km"], tstats["median_disp_km"]),
                ("tracks >= 1000 km", pystats["n_disp_ge_1000"], tstats["n_disp_ge_1000"]),
                ("bombs", pystats["n_bomb"], tstats["n_bomb"]),
                ("mean |genesis lat|", pystats["mean_abs_genesis_lat"], tstats["mean_abs_genesis_lat"])]
        for name, p, t in rows:
            fmt = (lambda v: f"{v:.1f}" if isinstance(v, float) else str(v))
            lines.append(f"| {name} | {fmt(p)} | {fmt(t)} |")
        m = match_tracks(tr, res.tracks)
        lines.append("\n## Track agreement\n")
        lines.append(f"* TE tracks matched by a python track: **{m['te_matched']}/{m['te_total']}**")
        lines.append(f"* matched fixes: {m['matched_fixes']}")
        lines.append(f"* position error over matched fixes: median "
                     f"**{m['median_pos_err_deg']:.2f} deg** ({m['median_pos_err_deg'] * 111.195:.0f} km), "
                     f"p90 {m['p90_pos_err_deg']:.2f} deg, max {m['max_pos_err_deg']:.2f} deg")
        ciaran_when = dt.datetime(2023, 11, 2, 6)
        te_c = storm_from_te(tr, ciaran_when, 48.4, -4.6)
        py_c = find_storm(res.tracks, ciaran_when, 48.4, -4.6)
        lines.append("\n## Known-storm check: Storm Ciaran (landfall Finistere, 2 Nov 2023)\n")
        lines.append("| | python | TempestExtremes |")
        lines.append("|---|---|---|")
        keys = [("nearest fix to the landfall point (12 h window)", "t", str),
                ("distance to the landfall point (km)", "dist_km", lambda v: f"{v:.0f}"),
                ("MSLP at that fix (hPa)", "msl_at_fix", lambda v: f"{v:.1f}"),
                ("track min MSLP (hPa)", "min_mslp", lambda v: f"{v:.1f}"),
                ("min at", "min_t", str),
                ("lifetime (h)", "lifetime_h", lambda v: f"{v:.0f}"),
                ("displacement (km)", "displacement_km", lambda v: f"{v:.0f}"),
                ("max 24 h fall (hPa)", "max_24h_fall", lambda v: f"{v:.1f}"),
                ("bomb", "bomb", str),
                ("bomb ratio", "bomb_ratio", lambda v: f"{v:.2f}"),
                ("fixes", "n_fixes", str)]
        for label, key, fmt in keys:
            pv = fmt(py_c[key]) if py_c else "not found"
            tv = fmt(te_c[key]) if te_c else "not found"
            lines.append(f"| {label} | {pv} | {tv} |")
    else:
        lines.append("| quantity | python |")
        lines.append("|---|---|")
        for k, v in pystats.items():
            lines.append(f"| {k} | {v:.1f} |" if isinstance(v, float) else f"| {k} | {v} |")
        lines.append("\n(TempestExtremes not run — `--skip-te` or no TE install.)")

    lines.append("\n## Python event screens (tracker output -> published events)\n")
    lines.append(f"* tracks considered: {len(res.tracks)}")
    lines.append(f"* events published: {len(events)}")
    for e in sorted(events, key=lambda e: e.start):
        d = e.details
        lines.append(f"  - `{e.start:%Y-%m-%d %H}Z` -> `{e.end:%Y-%m-%d}` "
                     f"{d['min_mslp_hPa']:.0f} hPa, {d['lifetime_h']:.0f} h, "
                     f"{d['displacement_km']:.0f} km, genesis "
                     f"{d['genesis_lat']:.1f}/{d['genesis_lon']:.1f} at "
                     f"{d['genesis_elev_m']:.0f} m"
                     + (", **bomb**" if d["bomb"] else ""))
    Path(a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {a.out}", flush=True)


if __name__ == "__main__":
    main()
