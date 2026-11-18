"""Build data/results/global_timeline.json — the global dashboard's data asset.

Reads the curated event list and the per-event viz.json assets (owner: the
viz builder) and emits ONE lightweight file for the home-page Equal Earth
map: per event, its id/type/title/dates/bbox plus a per-day activity mask
coarsened to ~1 deg, bit-packed with the SAME convention as viz schema-3
daily_bits (day-major, row-major, MSB-first; stride = ceil(ny*nx/8)).

shortcut: ARs have a single anchor (peak-area) mask in viz.json, repeated on
every active day — same approximation the AR detail panel already draws;
upgrade when build_ar stores true per-day masks.

The cyclone lanes have no footprint raster at all: their entry carries the
best track itself (``track`` — see TRACK_TYPES), which is what the merged map
draws as a polyline with intensity-coloured fixes (dashboard/tracklayer.js).

Runs as the tail of `python -m ewb_live.viz` (after any build pass) or
standalone: `python -m ewb_live.viz.timeline`. No network, seconds of CPU.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA = 3          # 2: + qualified_at/q_source per entry, + raw header mirror
                    # 3: + track entries for the cyclone lanes (merged map)
STEP_DEG = 1.0          # coarsening target for footprints
SIZE_LIMIT = 2_000_000  # bytes; brief target < 1-2 MB
OUT_NAME = "global_timeline.json"

# Timeline lanes = the Gantt strip under the merged map. The raster lanes need
# their per-event viz.json for the footprint bits; flood has none (its evidence
# is a 0.1° IMERG accumulation, not a v2 sidecar raster), so its entries fall
# back to bbox+days in build() — enough to populate the lane's bars, which is
# what mergedmap's LANE_ORDER draws.
TYPES = ("heat_wave", "freeze", "cold_snap", "atmospheric_river", "flood")

# TRACK lanes (tc-map 2026-11-18): a cyclone's geometry is not a footprint, it
# is the best track the lane already published in ``details.track`` (the shared
# 5-slot row schema). Those entries carry a compact ``track`` object instead of
# grid/masks and the merged map draws the TRACK — polyline, intensity fixes, the
# position at the selected step — never a bbox box. Without this the lane is in
# the catalogue, the tabs and the event table but on NO map at all, which is the
# bug this fixes (44 published storms, 0 pixels).
TRACK_TYPES = ("tropical_cyclone", "midlat_cyclone")


def _qualified(ev: dict) -> tuple[str, str]:
    """('YYYYMMDDHH', source): ewb's detection-time qualified_at if present,
    else the event start truncated to the hour (honest placeholder until
    ewb lands — merged_global_map brief §4)."""
    q = ev.get("qualified_at")
    if q:
        return _compact(q), "detection"
    return _compact(ev["start"]), "start_fallback"


def _compact(iso) -> str:
    s = str(iso)[:19].replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    return s[:10].ljust(10, "0")  # date-only input -> 00Z step


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _decode_bits(blob_b64: str, nd: int, ny: int, nx: int) -> np.ndarray:
    """Daily bit masks -> (nd, ny, nx) bool; the JS consumer's exact contract."""
    b = np.frombuffer(base64.b64decode(blob_b64), dtype=np.uint8)
    stride = -(-ny * nx // 8)  # ceil, one pad tail per day
    out = np.zeros((nd, ny * nx), dtype=bool)
    for d in range(nd):
        out[d] = np.unpackbits(b[d * stride:(d + 1) * stride])[:ny * nx].astype(bool)
    return out.reshape(nd, ny, nx)


def _unpack_str_mask(mask: str, ny: int, nx: int) -> np.ndarray:
    """Legacy '0/1' row strips ('/'-joined) -> (ny, nx) bool."""
    rows = mask.split("/")
    return np.array([[c == "1" for c in row] for row in rows], dtype=bool)


def _coarsen(masks: np.ndarray, factor: int) -> np.ndarray:
    """OR-reduce (nd, ny, nx) in factor×factor blocks (pads the ragged edge)."""
    if factor <= 1:
        return masks.astype(bool)
    nd, ny, nx = masks.shape
    py, px = -(-ny // factor) * factor, -(-nx // factor) * factor
    pad = np.zeros((nd, py, px), dtype=bool)
    pad[:, :ny, :nx] = masks
    return pad.reshape(nd, py // factor, factor, px // factor, factor).any(axis=(2, 4))


def _edges(arr: np.ndarray, step: float) -> tuple[float, float]:
    """cell centers -> outer edges of the first/last cell"""
    return float(arr[0] - step / 2), float(arr[-1] + step / 2)


def _align(m: np.ndarray, n: int) -> np.ndarray:
    """Truncate or zero-extend the day axis to n days (masks start at window start)."""
    if m.shape[0] >= n:
        return m[:n]
    pad = np.zeros((n - m.shape[0],) + m.shape[1:], dtype=bool)
    return np.concatenate([m, pad], axis=0)


def _num(v) -> float | None:
    """Track/meta scalar -> rounded float or None. Lane details scalars arrive
    as strings after the DataFrame round-trip ("115.0"), and JSON has no NaN:
    anything non-numeric or non-finite becomes a JSON null."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 3) if np.isfinite(f) else None


def _flag(v) -> bool:
    """bool that also reads the string form the lane writes ("False")."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def _text(v) -> str:
    s = "" if v is None else str(v)
    return "" if s.strip().lower() in ("", "nan", "none", "unnamed") else s.strip()


def _pick(d: dict, *keys):
    """First key present with a usable value (the lanes name their headline
    differently: peak_wind_kt vs min_mslp_hPa, min_pressure_hpa vs …)."""
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _track_entry(ev: dict) -> dict | None:
    """A TRACK-lane entry: the event's own geometry, compacted to the map's
    frame key. Slot 0 of every fix becomes 'YYYYMMDDHH' so the browser compares
    fixes against ``raw.frames`` with a string compare — the same convention as
    ``qualified_at`` and the same reason (no Date parsing, no timezone traps).
    The 5-slot row order is the repo-wide ``details.track`` contract:
    [time, lat, lon, intensity, pressure_hpa]; intensity is the lane's own unit
    and direction (``units`` carries it, e.g. 'kt' up, 'hPa' down).
    """
    d = ev.get("details") or {}
    rows = d.get("track")
    if not isinstance(rows, list):
        logger.warning("timeline: %s (%s) has no details.track — no map entry",
                       ev["id"], ev.get("event_type"))
        return None
    fixes = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 3:
            continue
        ts = _compact(r[0])
        if not re.fullmatch(r"\d{10}", ts):      # unusable stamp: drop the fix
            continue
        lat, lon = _num(r[1]), _num(r[2])
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 360:
            continue
        fixes.append([ts, lat, lon,
                      _num(r[3]) if len(r) > 3 else None,
                      _num(r[4]) if len(r) > 4 else None])
    if len(fixes) < 2:
        logger.warning("timeline: %s (%s) track has %d usable fix(es) — no map entry",
                       ev["id"], ev.get("event_type"), len(fixes))
        return None
    entry = {"id": ev["id"], "type": ev["event_type"], "title": ev.get("title"),
             "start": ev["start"], "end": ev["end"], "bbox": ev["bbox"],
             "days": [str(ev["start"])[:10]]}
    entry["qualified_at"], entry["q_source"] = _qualified(ev)
    entry["track"] = {
        "units": _text(d.get("intensity_units")),
        "step_h": _num(_pick(d, "fix_step_h")),
        "name": _text(_pick(d, "name", "storm_name")),
        "basin": _text(_pick(d, "basin_name", "basin")),
        "peak": _num(_pick(d, "peak_wind_kt", "min_mslp_hPa", ev.get("peak_value"))),
        "peak_label": _text(_pick(d, "peak_category")),
        "pressure": _num(_pick(d, "min_pressure_hpa", "min_mslp_hPa")),
        "crosses_antimeridian": _flag(d.get("track_crosses_antimeridian")),
        "fixes": fixes,
    }
    return entry


def _event_entry(ev: dict, results: Path) -> dict:
    entry = {"id": ev["id"], "type": ev["event_type"], "title": ev.get("title"),
             "start": ev["start"], "end": ev["end"], "bbox": ev["bbox"],
             "days": [str(ev["start"])[:10]]}
    entry["qualified_at"], entry["q_source"] = _qualified(ev)
    viz = _load(results / "events" / ev["id"] / "viz.json")
    if not viz or viz.get("schema") != 3 or not viz.get("grid"):
        logger.warning("timeline: %s has no usable viz.json — bbox-only entry", ev["id"])
        return entry
    g = viz["grid"]
    lats, lons = np.asarray(g["lat"], float), np.asarray(g["lon"], float)
    cell = float(g["cell_deg"])
    factor = max(1, int(round(STEP_DEG / cell)))
    obs = viz.get("obs") or {}
    start = dt.datetime.fromisoformat(ev["start"]).date()
    # ev['end'] is EXCLUSIVE; active days run through end-1d
    end = dt.datetime.fromisoformat(ev["end"]).date() - dt.timedelta(days=1)
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    entry["days"] = days
    db = obs.get("daily_bits")
    masks = None
    if db:  # heat/freeze: true daily exceedance masks
        m = _decode_bits(db["b64"], db["ndays"], db["ny"], db["nx"])
        if db["ndays"] != len(days):
            logger.warning("timeline: %s ndays %s != %s window days — aligning "
                           "from window start", ev["id"], db["ndays"], len(days))
            m = _align(m, len(days))
        masks = _coarsen(m, factor)
    elif obs.get("mask"):  # AR: single anchor mask (see shortcut note above)
        m = _coarsen(_unpack_str_mask(obs["mask"], len(lats), len(lons))[None], factor)
        masks = np.repeat(m, len(days), axis=0)
    if masks is None:
        logger.warning("timeline: %s viz.json has no mask — bbox-only entry", ev["id"])
        return entry
    y0, y1 = _edges(lats, cell)
    x0, x1 = _edges(lons, cell)
    gy, gx = int(masks.shape[1]), int(masks.shape[2])
    entry["grid"] = {"lat0": round(y0, 4), "lat1": round(y1, 4),
                     "lon0": round(x0, 4), "lon1": round(x1, 4),
                     "step_deg": round((y1 - y0) / gy, 4),
                     "step_lon": round((x1 - x0) / gx, 4),
                     "ny": gy, "nx": gx,
                     "continuous_lon": bool(x1 > 180.0)}
    entry["masks"] = [base64.b64encode(np.packbits(masks[d].ravel()).tobytes()).decode("ascii")
                      for d in range(masks.shape[0])]
    return entry


def build(results_dir: Path) -> Path:
    """Write global_timeline.json next to events_selected.json; returns the path."""
    events = _load(results_dir / "events_selected.json")
    if events is None:
        raise FileNotFoundError(f"{results_dir}/events_selected.json missing")
    if isinstance(events, dict):
        events = events.get("events", [])
    meta = _load(results_dir / "meta.json") or {}
    entries = []
    for ev in events:
        if ev.get("event_type") in TRACK_TYPES:
            try:
                te = _track_entry(ev)
            except Exception:  # one bad track must not kill the timeline
                logger.exception("timeline: track entry for %s failed", ev["id"])
                te = None
            if te:
                entries.append(te)
            continue
        if ev.get("event_type") not in TYPES:
            continue
        try:
            entries.append(_event_entry(ev, results_dir))
        except Exception:  # one bad asset must not kill the timeline
            logger.exception("timeline: event %s failed — bbox-only fallback", ev["id"])
            qe = {"id": ev["id"], "type": ev["event_type"],
                  "title": ev.get("title"), "start": ev["start"],
                  "end": ev["end"], "bbox": ev["bbox"],
                  "days": [str(ev["start"])[:10]]}
            qe["qualified_at"], qe["q_source"] = _qualified(ev)
            entries.append(qe)
    doc = {"schema": SCHEMA,
           "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "window": (meta.get("window") or {}),
           "mask_bits": "day-major, row-major, MSB-first; stride=ceil(ny*nx/8)",
           "events": entries}
    raw_idx = _load(results_dir / "raw_exceedance" / "index.json") or {}
    if raw_idx.get("frames"):  # merged map: one fetch drives canvas + boxes
        fr = raw_idx["frames"]
        doc["raw"] = {"grid": raw_idx.get("grid"), "cutoff": raw_idx.get("cutoff"),
                      "first": fr[0], "last": fr[-1]}
    raw = json.dumps(doc, separators=(",", ":"))
    if len(raw) > SIZE_LIMIT:
        raise RuntimeError(f"global_timeline.json {len(raw)} B exceeds {SIZE_LIMIT} B target")
    out = results_dir / OUT_NAME
    tmp = out.with_name(out.name + f".tmp.{os.getpid()}")  # sweep matches *.tmp.*
    tmp.write_text(raw)
    tmp.rename(out)
    logger.info("timeline: %d events, %.0f KB -> %s", len(entries), len(raw) / 1024, out)
    return out


def main(argv=None) -> int:
    from . import build as viz_build  # lazy: keep this module import-light
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.timeline")
    ap.add_argument("--results", type=Path, default=viz_build.ROOT / "data" / "results")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build(args.results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
