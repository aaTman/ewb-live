"""Tropical-cyclone WIND-FIELD asset for the merged map (tc-map, 2026-11-18).

Owner direction 2026-11-18: "plot the TCs as wind fields (max winds if
possible) where greater than tropical storm force winds (34 kts) and plot by
winds and Saffir-Simpson category for shading." So the map layer for a storm is
the field it produced, not only the line it followed: every cell that reached
tropical-storm force is shaded, classed by the Saffir-Simpson scale, and the
track + centre marker stay on top for orientation.

Source — measured, not assumed (full numbers in ``notes/tc_map.md``)
-------------------------------------------------------------------
* **Used: ERA5 10 m wind, ``10m_u_component_of_wind`` / ``10m_v_component_of_wind``
  from the ERA5 ARCO zarr** (the same store ``ewb_live/sources/mlc_era5.py``
  streams; hourly on archive, 0.25°). Sustained wind, 10-minute means — the only
  quantity the Saffir-Simpson thresholds in kt are defined against.
* Not the local truth mirror: ``truth_mirror.MIRROR_VARS`` is
  ``2m_temperature`` + the AR variables on **pressure levels** — there is no
  10 m wind in it (checked 2026-11-18).
* Not the gust fields (``instantaneous_10m_wind_gust``,
  ``10m_wind_gust_since_previous_post_processing``): a gust is ~1.4× a sustained
  wind, so shading by them would overstate every category on a scale defined by
  sustained wind. They are the right source for a *gust* layer, not this one.
* Cadence: the **four 6-hourly synoptic steps** of a UTC day, max per cell.
  Measured on 8 published storms: reading all 24 hourly steps instead raises
  the daily maximum from 0.69 to 0.72 of the best-track peak (+1–3 kt) for 6×
  the reads. 6-hourly also matches the map's own frame cadence.
* "Windowed, not global" — measured the other way: the ARCO wind chunks are
  ``(1, 721, 1440)``, i.e. one whole global field per time step, so a
  13°×13° window read costs *more* than the global one (measured on the ewb LXC:
  4 steps global 4.9 s vs the same 4 steps windowed 25.6 s). The build therefore
  streams the global 6-hourly field once per day (~1.2 s/step for both
  components, ~7 min for a 90-day window) and crops **in memory**, which is the
  cheap way to honour the intent: nothing global is ever stored, every published
  raster is a ±5° window around one storm's fixes for one day.

Honesty about intensity (the bias is real and large)
----------------------------------------------------
ERA5 at 0.25° cannot resolve an eyewall and its wind is a 10-minute mean, while
IBTrACS peaks are ~1-minute sustained. For the 44 published storms in the dev
window the daily-max 10 m wind within 2° of the peak fix averaged **0.69×** the
best-track peak (0.40× for a Cat 4). So category shading derived from the field
reads *lower* than the official category, always. This lane therefore never
presents the field as the category: the asset says which field it came from, the
map labels the layer "ERA5 10 m wind" and keeps the best-track peak/category on
the storm itself, and the legend says the field under-reads. No silent
correction is applied — the measured ratio is published in the asset instead.

Output (one small asset next to the other map assets)
-----------------------------------------------------
``data/results/tc_wind.json``::

    {"schema": 1, "lane": "tropical_cyclone",
     "source": "ERA5 10 m wind (10m_u/v_component_of_wind), ERA5 ARCO 0.25°, "
               "00/06/12/18Z, max over the UTC day",
     "cadence_h": 6, "step_deg": 1.0, "read_deg": 0.25, "half_deg": 5.0, "floor_kt": 34,
     "units": "kt",
     "classes": [{"id": 1, "kt": [34, 63], "mph": [39, 72], "label": "TS",
                  "tok": "--tc-wind-1"}, … Cat 5 {"kt": [137, null]}],
     "bias": {"ratio_median": 0.69, "n": 44,
              "note": "ERA5 10 m wind at 0.25° under-reads tropical-cyclone peak
                       winds; class shading from it is lower than the best-track
                       category, which the map shows separately."},
     "census": {"1": 12345, …},                  # cells per class, whole asset
     "storms": [{"id": …, "best_peak_kt": 115, "best_category": "CAT 4",
                 "era_peak_kt": 46, "ratio": 0.4,
                 "days": [{"d": "2026-08-19", "max_kt": 46, "lat0": 15.5,
                           "lon0": -173.5, "ny": 41, "nx": 41,
                           "rle": [kt, run, kt, run, …]}]}     # run-length u8

Grid geometry (THE PLACEMENT CONTRACT, pinned by tests/test_tc_wind.py): cell
``(i, j)`` is centred at ``lat0 + i*step_deg`` (row 0 is the southern centre)
and ``((lon0 + j*step_deg + 180) % 360) - 180`` (the repo wrap convention — the
grid itself never crosses the dateline in coordinates, the client wraps per
cell, so an antimeridian-crossing storm needs no special case).

Runs on the ewb LXC (network + CPU): ``ops/remote.sh run python -m
ewb_live.viz.tc_wind --days 90``. Pure geometry/classification is offline and
unit-tested; only the read path touches the network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import statistics
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA = 1
OUT_NAME = "tc_wind.json"
SIZE_LIMIT = 8_000_000          # bytes; 44 storms ≈ 0.2 MB (measured)
MS_TO_KT = 1.0 / 0.514444       # m/s -> kt
FLOOR_KT = 34.0                 # tropical-storm force (39 mph)
STEP_DEG = 0.25                 # the ARCO grid (the read resolution)
OUT_DEG = 1.0                   # the RENDERED cell: the map's own legibility step.
                                # A 0.25° cell is half a CSS pixel on a 700 px world
                                # sheet, so the field is max-pooled to 1° — the same
                                # OR-over-cells convention (and the same ~1° scale) as
                                # the map's raster lanes. Nothing is interpolated.
HALF_DEG = 5.0                  # default window half-width around a day's fixes
MAX_HALF_DEG = 8.0              # a day's box never grows past this
NEAR_DEG = 2.0                  # "near the storm" for the bias measurement
FACTOR = int(round(OUT_DEG / STEP_DEG))   # fine cells per coarse cell
LAT_N, LON_N = 721, 1440        # ARCO 0.25° global grid (90..-90, 0..359.75)
HOURS = (0, 6, 12, 18)          # the four synoptic steps of a UTC day
WIND_VARS = ["10m_u_component_of_wind", "10m_v_component_of_wind"]

# Saffir-Simpson by sustained wind (owner's scale, NHC thresholds), with mph
# alongside because the owner's threshold was quoted in both units.
CLASSES = [
    {"id": 1, "kt": [34, 63], "mph": [39, 72], "label": "TS", "tok": "--tc-wind-1"},
    {"id": 2, "kt": [64, 82], "mph": [74, 95], "label": "CAT 1", "tok": "--tc-wind-2"},
    {"id": 3, "kt": [83, 95], "mph": [96, 110], "label": "CAT 2", "tok": "--tc-wind-3"},
    {"id": 4, "kt": [96, 112], "mph": [110, 129], "label": "CAT 3", "tok": "--tc-wind-4"},
    {"id": 5, "kt": [113, 136], "mph": [130, 156], "label": "CAT 4", "tok": "--tc-wind-5"},
    {"id": 6, "kt": [137, None], "mph": [157, None], "label": "CAT 5", "tok": "--tc-wind-6"},
]
# class edges in kt on the u8 grid (0 = below the floor = not drawn)
EDGES_KT = np.array([0, 34, 64, 83, 96, 113, 137], dtype=np.uint8)

SOURCE = ("ERA5 10 m wind (10m_u/v_component_of_wind, ERA5 ARCO 0.25°), "
          "00/06/12/18Z, max over the UTC day")
BIAS_NOTE = ("ERA5 10 m wind at 0.25° resolves no eyewall and is a 10-minute "
             "mean, so it under-reads tropical-cyclone peak winds: across the "
             "published storms the field's daily maximum near the peak fix "
             "averaged {ratio}× the IBTrACS best-track peak. Category shading "
             "from this field is always at or below the best-track category, "
             "which the map shows on the storm itself.")


# ------------------------------------------------------------- pure parts --
def class_of(kt: np.ndarray) -> np.ndarray:
    """u8 kt grid -> u8 class grid (0 where below the 34 kt floor).

    Class ids follow CLASSES (1 = TS … 6 = Cat 5); the top class is open.
    """
    return np.clip(np.searchsorted(EDGES_KT, kt, side="right") - 1, 0, 6).astype(np.uint8)


def day_windows(track: list, half_deg: float = HALF_DEG) -> dict:
    """Track rows -> {UTC date: (centre_lat, centre_lon, half_deg)}.

    One box per day, centred on that day's fixes and grown just enough to hold
    them (plus the NEAR margin) so a fast-moving storm keeps its whole day, and
    capped at MAX_HALF_DEG.
    """
    by_day: dict[dt.date, list[tuple[float, float]]] = {}
    for row in track:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            lat, lon = float(row[1]), float(row[2])
            day = dt.date.fromisoformat(str(row[0])[:10])
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(lat) and np.isfinite(lon)) or abs(lat) > 90.0:
            continue                                  # an impossible fix, not a window
        by_day.setdefault(day, []).append((lat, lon))
    out = {}
    for day, pts in by_day.items():
        lats = np.array([p[0] for p in pts], float)
        lons = np.array([p[1] for p in pts], float)
        # circular mean of the longitudes, so a day straddling ±180 centres right
        ang = np.deg2rad(lons)
        lon_c = float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())))
        spread = max(float(lats.max() - lats.min()),
                     float((np.abs(((lons - lon_c + 180) % 360) - 180)).max() * 2))
        half = min(MAX_HALF_DEG, max(half_deg, spread / 2 + 1.0))
        out[day] = (float(lats.mean()), ((lon_c + 180.0) % 360.0) - 180.0, half)
    return out


def grid_geometry(lat_c: float, lon_c: float, half: float,
                  step: float = STEP_DEG) -> tuple[float, float, int, int]:
    """Window centre -> (lat0, lon0, ny, nx) of the cell-CENTRE grid it selects.

    THE PLACEMENT CONTRACT (the map draws exactly this, and tests/test_tc_wind.py
    pins it): cell (i, j) sits at latitude lat0 + i*step — rows run NORTH from
    the southern centre — and longitude lon0 + j*step wrapped to [-180, 180).
    lat0/lon0 are therefore the south/west cell centres; lon0 stays in the repo's
    [-180, 180) frame so a window that straddles the dateline is just a wrapped
    column range. Latitude is clamped so the whole window stays inside the grid
    (a 10° window centred above 79.875° N slides south instead of running off
    the pole or repeating the edge row).
    """
    n = int(round(2 * half / step)) + 1
    span = (n - 1) * step
    lo, hi = -89.875 + span, 89.875 - span
    lat0 = round(min(hi, max(lo, lat_c - half)), 4)
    lon0 = (((lon_c - half) + 180.0) % 360.0) - 180.0
    return lat0, round(lon0, 4), n, n


def window_indices(lat0: float, lon0: float, ny: int, nx: int,
                   step: float = STEP_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Cell centres -> row indices on the ascending-latitude grid and column
    indices on the 0..359.75 ARCO longitude axis (wrapped, so a window that
    straddles ±180 is just a wrapped column range — no special case)."""
    jj = np.rint((np.arange(ny) * step + lat0 + 90.0) / step).astype(int)
    ii = np.rint((np.arange(nx) * step + lon0 + 180.0) / step).astype(int) % LON_N
    # latitude is clipped rather than wrapped: a window that would run past a
    # pole repeats the edge row, which grid_geometry() keeps from happening
    return np.clip(jj, 0, LAT_N - 1), ii


def rle_grid(kt: np.ndarray) -> list[int]:
    """u8 grid -> [value, run_length, value, run_length, ...] row-major.

    Run-length, not base64+zlib, because the browser has no synchronous inflate:
    a day's window is a few hundred near-identical cells (mostly zeros), so runs
    are both smaller than the raw bytes and decodable in a ten-line JS loop
    without a vendored decompressor. Round-trips exactly (see the test).
    """
    flat = kt.astype(np.uint8).reshape(-1)
    if not flat.size:
        return []
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [flat.size]))
    out: list[int] = []
    for a, b in zip(starts, ends):
        out.append(int(flat[a]))
        out.append(int(b - a))
    return out


def unpack_rle(rle: list[int], n: int) -> np.ndarray:
    """The inverse of rle_grid() (and the shape of the JS decoder)."""
    out = np.empty(n, np.uint8)
    k = 0
    for v, run in zip(rle[::2], rle[1::2]):
        out[k:k + run] = v
        k += run
    if k != n:
        raise ValueError(f"rle grid holds {k} cells, expected {n}")
    return out


def ms_to_kt(ms):
    """m/s -> kt (the ARCO unit -> the owner's unit). Scalars and fields alike."""
    return np.asarray(ms, dtype=np.float64) * MS_TO_KT


def day_steps(day: dt.date) -> list[dt.datetime]:
    return [dt.datetime.combine(day, dt.time(h)) for h in HOURS]


# ---------------------------------------------------------- read the ARCO --
def open_wind_store():
    """The ERA5 ARCO zarr with only the two 10 m wind components selected."""
    from ewb_live.sources.mlc_era5 import ARCO_URI   # one URI, one env override
    import xarray as xr

    ds = xr.open_zarr(ARCO_URI, storage_options={"token": "anon"},
                      consolidated=False, decode_times=True)
    return ds[WIND_VARS]


class NoWindData(RuntimeError):
    """No 10 m wind step exists for that day (past the ARCO cutoff): an honest
    absence, never a zero field — the map gets no entry for the day at all."""


def day_max_kt(ds, day: dt.date) -> np.ndarray:
    """Global u8 field: max 10 m wind (kt, floored to 0 below the floor) over the
    day's four 6-hourly steps, on the ascending-latitude / [-180,180)-longitude
    frame. ~50 MB float32 while the day is in flight; only the u8 result is kept.

    ARCO gives (time, lat descending 90..-90, lon ascending 0..359.75); flipping
    the rows and rolling the columns by half the axis puts it in the frame every
    other map asset uses, so index arithmetic here matches the rest of the repo.
    A step missing from the archive (very recent dates) simply drops out of the
    max — the day is then built from the steps that exist.
    """
    times = np.array([np.datetime64(t) for t in day_steps(day)], dtype="datetime64[ns]")
    # only labels the store actually has: a time past the archive's end is absent
    # from its axis, and xarray raises on a missing exact label
    have = np.isin(times, np.asarray(ds.indexes["time"].values))
    if not have.any():
        raise NoWindData(f"tc_wind: no 10 m wind steps for {day}")
    blk = ds.sel(time=times[have]).load()
    u = np.asarray(blk[WIND_VARS[0]].values, np.float32)
    v = np.asarray(blk[WIND_VARS[1]].values, np.float32)
    del blk
    if u.ndim != 3:
        raise RuntimeError(f"tc_wind: unexpected 10 m wind shape {u.shape} for {day}")
    # ARCO fills missing recent times with NaN: a step that is missing drops out
    # of the day's max, and a day with NO valid step raises (no entry, never a
    # zero field that would read as "no storm force that day")
    finite = np.isfinite(u) & np.isfinite(v)
    if not finite.any():
        raise NoWindData(f"tc_wind: no 10 m wind steps for {day}")
    sp = np.hypot(np.where(finite, u, 0.0), np.where(finite, v, 0.0))
    sp = np.where(finite.any(axis=0), sp, 0.0).max(axis=0)      # (721, 1440) m/s
    del u, v
    sp = sp[::-1]                                    # latitude ascending
    sp = np.roll(sp, LON_N // 2, axis=1)             # longitude -180..179.75
    kt = np.rint(ms_to_kt(sp)).astype(np.uint16).clip(0, 255).astype(np.uint8)
    return np.where(kt < int(FLOOR_KT), np.uint8(0), kt)


def crop(field: np.ndarray, lat0: float, lon0: float, ny: int, nx: int) -> np.ndarray:
    jj, ii = window_indices(lat0, lon0, ny, nx)
    return field[np.ix_(jj, ii)]


def rebin_max(g: np.ndarray, factor: int) -> np.ndarray:
    """Max-pool a window onto a coarser lattice (factor = OUT_DEG / STEP_DEG).

    A coarse cell means "some fine cell inside me reached this wind" — an OR over
    cells, which is exactly how the map's raster lanes are stored, and the honest
    way to show a threshold map at 1°: nothing is averaged away and nothing is
    invented. The coarse lattice SHARES the window's southwest cell centre, so the
    placement contract is unchanged (cell (i, j) is still lat0 + i*step_deg, with
    step_deg now the coarse one); a trailing partial block covers the ragged end
    of a window whose width is not a multiple of the coarse cell.
    """
    if factor <= 1:
        return g.astype(np.uint8)
    rows = np.rint(np.arange(g.shape[0]) / factor).astype(int)
    cols = np.rint(np.arange(g.shape[1]) / factor).astype(int)
    out = np.zeros((int(rows.max()) + 1, int(cols.max()) + 1), np.uint8)
    np.maximum.at(out, (rows[:, None], cols[None, :]), g)
    return out


# ------------------------------------------------------------------ build --
def _tc_events(results_dir: Path) -> list[dict]:
    doc = json.loads((results_dir / "events_selected.json").read_text())
    events = doc.get("events", []) if isinstance(doc, dict) else doc
    out = [e for e in events if e.get("event_type") == "tropical_cyclone"
           and isinstance((e.get("details") or {}).get("track"), list)
           and e["details"]["track"]]
    out.sort(key=lambda e: str(e.get("start")))
    return out


def _num(v, dflt=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dflt
    return f if np.isfinite(f) else dflt


def build(results_dir: Path, *, days: int | None = None, half_deg: float = HALF_DEG,
          limit: int | None = None, store=None, out_name: str = OUT_NAME) -> Path:
    """Write the wind-field asset next to events_selected.json; returns the path.

    ``days`` trims the window to the last N days (the weekly run's window),
    ``limit`` builds only the first N storms (dev/iteration).
    """
    events = _tc_events(results_dir)
    if days:
        end = max(dt.date.fromisoformat(str(e["end"])[:10]) for e in events) if events else None
        if end:
            lo = end - dt.timedelta(days=days - 1)
            events = [e for e in events if dt.date.fromisoformat(str(e["start"])[:10]) >= lo]
    if limit:
        events = events[:limit]
    if not events:
        logger.info("tc_wind: no published tropical_cyclone events — nothing to build")

    need = {}                                        # date -> storms that day
    for ev in events:
        for day in day_windows(ev["details"]["track"], half_deg):
            need.setdefault(day, []).append(ev)
    fields: dict[dt.date, np.ndarray] = {}
    nodata: list[str] = []
    if need:
        if store is None:
            store = open_wind_store()
        for i, day in enumerate(sorted(need)):
            try:
                fields[day] = day_max_kt(store, day)   # one day in flight: 1 MB of u8
            except NoWindData as exc:
                logger.warning("%s — skipping the day", exc)
                nodata.append(day.isoformat())
            if (i + 1) % 10 == 0:
                logger.info("tc_wind: %d/%d days read", i + 1, len(need))

    storms, ratios, census = [], [], {str(c["id"]): 0 for c in CLASSES}
    for ev in events:
        det = ev["details"]
        wins = day_windows(det["track"], half_deg)
        entries, era_peak, peak_day = [], 0.0, None
        for day in sorted(wins):
            if day not in fields:                 # no wind data that day: no entry
                continue
            lat_c, lon_c, half = wins[day]
            lat0, lon0, ny, nx = grid_geometry(lat_c, lon_c, half, STEP_DEG)
            sub = rebin_max(crop(fields[day], lat0, lon0, ny, nx), FACTOR)
            entries.append({"d": day.isoformat(), "max_kt": int(sub.max()),
                            "lat0": lat0, "lon0": lon0, "ny": sub.shape[0],
                            "nx": sub.shape[1], "rle": rle_grid(sub)})
            cls = class_of(sub)
            for cid in census:
                census[cid] += int((cls == int(cid)).sum())
            if int(sub.max()) >= era_peak:
                era_peak, peak_day = int(sub.max()), day
        best = _num(det.get("peak_wind_kt"), _num(ev.get("peak_value")))
        # ratio None when nothing was measured (no field at all) — a 0.00x would
        # say "the field read zero", which is a measurement, not an absence
        ratio = round(era_peak / best, 2) if best and era_peak else None
        if ratio is not None:
            ratios.append(ratio)
        storms.append({"id": ev["id"], "title": ev.get("title"),
                       "name": str(det.get("name") or "").strip(),
                       "best_peak_kt": best,
                       "best_category": str(det.get("peak_category") or "").strip(),
                       "era_peak_kt": era_peak, "peak_day": peak_day.isoformat() if peak_day else None,
                       "ratio": ratio,
                       "days": entries})
        logger.info("tc_wind: %s %s best %s kt, field max %s kt (%s)", ev["id"],
                    det.get("name") or "?", best, era_peak,
                    f"{ratio:.2f}x" if ratio is not None else "n/a")

    doc = {"schema": SCHEMA, "lane": "tropical_cyclone", "source": SOURCE, "cadence_h": 6,
           "step_deg": OUT_DEG, "read_deg": STEP_DEG,
           "half_deg": half_deg, "floor_kt": FLOOR_KT, "units": "kt",
           "classes": CLASSES,
           "bias": {"ratio_median": round(statistics.median(ratios), 2) if ratios else None,
                    "ratio_min": min(ratios) if ratios else None,
                    "ratio_max": max(ratios) if ratios else None, "n": len(ratios),
                    "note": BIAS_NOTE.format(ratio=statistics.median(ratios) if ratios else "?")},
           "census": census, "days_no_data": nodata, "storms": storms}
    raw = json.dumps(doc, separators=(",", ":"))
    if len(raw) > SIZE_LIMIT:
        raise RuntimeError(f"{OUT_NAME} {len(raw)} B exceeds {SIZE_LIMIT} B")
    out = results_dir / out_name
    tmp = out.with_name(out.name + f".tmp.{os.getpid()}")
    tmp.write_text(raw)
    tmp.rename(out)
    logger.info("tc_wind: %d storms, %.0f KB -> %s", len(storms), len(raw) / 1024, out)
    return out


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host      # network + CPU: ewb LXC only

    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.tc_wind")
    ap.add_argument("--results", type=Path,
                    default=Path(__file__).resolve().parents[2] / "data" / "results")
    ap.add_argument("--days", type=int, default=None,
                    help="only storms starting in the last N days (default: all published)")
    ap.add_argument("--half-deg", type=float, default=HALF_DEG,
                    help="window half-width around a day's fixes (degrees)")
    ap.add_argument("--limit", type=int, default=None, help="build only the first N storms")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build(args.results, days=args.days, half_deg=args.half_deg, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
