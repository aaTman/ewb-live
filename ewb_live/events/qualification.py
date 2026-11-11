"""Per-event qualified_at: the first 6-hourly step the event met detection.

Owner decision 2026-11-08 (brief notes/briefs/merged_global_map.md): the
merged global map shows event boxes from the timestep each event FIRST MEETS
THE DETECTION CRITERIA. Definition (SPEC §2):

  qualified_at = the earliest raw-exceedance frame (6-hourly ERA5 step, the
  frame-level operators the detector itself uses — see ewb_live/viz/
  exceedance.py) within the event window whose mask, inside the event bbox,
  reaches the detection area floor: 200k km^2 (the upstream seed-area floor)
  for heat/freeze, 500 pixels (min_mask_gridpoints) for AR. The AND-chain
  persistence criterion is day-level and is carried by the event itself;
  AR landfall is likewise event-level. If no frame in the window qualifies
  (e.g. the frames end at the ERA5 mirror cutoff before the event start),
  qualified_at falls back to 00:00 UTC of the event start date.

Frames are the published data/results/raw_exceedance/ assets — the same
bytes the raw map serves — so qualification cannot drift from the map.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import zlib
from pathlib import Path

import numpy as np

DIRNAME = Path("data/results/raw_exceedance")
# upstream detection floors (see ewb_live/events/*, SPEC §2)
SEED_AREA_KM2 = 200_000.0
AR_MIN_PX = 500
_DEG_KM = 111.32


def _layer_at(path: Path, grid: dict, layer: int,
              lat_min: float, lat_max: float,
              lon_min: float, lon_max: float) -> int:
    """Count TRUE cells of one layer inside (lat/lon window) of one frame.

    Frame bytes: zlib(packbits(heat) ++ packbits(freeze) ++ packbits(ar)),
    each layer row-major in DISPLAY orientation (row 0 = lat -90, col 0 =
    lon -180), lon_min > lon_max means the arc crosses ±180.
    """
    nx, ny = grid["nx"], grid["ny"]
    dlat, dlon = grid["step_lat"], grid["step_lon"]
    lat0, lon0 = grid["lat0"], grid["lon0"]
    stride = (nx + 7) // 8
    r0 = max(0, int(math.floor((min(lat_min, lat_max) - lat0) / dlat)))
    r1 = min(ny - 1, int(math.ceil((max(lat_min, lat_max) - lat0) / dlat)))
    raw = zlib.decompress(path.read_bytes())[layer * ny * stride:
                                             (layer + 1) * ny * stride]
    rows = np.unpackbits(np.frombuffer(raw, np.uint8).reshape(ny, stride),
                         axis=1)[:, :nx][r0:r1 + 1]
    c0 = int(round((lon_min - lon0) / dlon)) % nx
    c1 = int(round((lon_max - lon0) / dlon)) % nx
    if c0 <= c1:
        return int(rows[:, c0:c1 + 1].sum())
    return int(rows[:, c0:].sum() + rows[:, :c1 + 1].sum())


def qualified_at(event, frames_dir: Path | None = None) -> str:
    """ISO 8601 (6-hourly step) for one Event; see module docstring."""
    frames_dir = Path(frames_dir or DIRNAME)
    idx_p = frames_dir / "index.json"
    fallback = f"{event.start:%Y-%m-%d}T00:00:00Z"
    if not idx_p.exists():
        return fallback
    try:
        idx = json.loads(idx_p.read_text())
        grid = idx["grid"]
        # heat/freeze: the tracker knows the day the footprint first reached the
        # area floor (details.qualified_date). Start the frame scan there, so a
        # box that later grew over other heat cannot qualify before its own
        # footprint did. AR events carry no qualified_date -> unchanged.
        qd = (getattr(event, "details", None) or {}).get("qualified_date")
        lo = max(f"{event.start:%Y-%m-%d}", str(qd)) if qd else f"{event.start:%Y-%m-%d}"
        frames = [f for f in idx["frames"]
                  if f"{f[:4]}-{f[4:6]}-{f[6:8]}" >= lo
                  and f"{f[:4]}-{f[4:6]}-{f[6:8]}" < f"{event.end:%Y-%m-%d}"]
    except (ValueError, KeyError):
        return fallback
    if not frames:
        return fallback
    layer = {"heat_wave": 0, "freeze": 1, "atmospheric_river": 2}[event.event_type]
    lat_min, lat_max, lon_min, lon_max = event.bbox
    if event.event_type == "atmospheric_river":
        floor_px = AR_MIN_PX
    else:
        mid = math.radians((min(lat_min, lat_max) + max(lat_min, lat_max)) / 2.0)
        cell_km2 = (_DEG_KM * grid["step_lat"]) * (_DEG_KM * grid["step_lon"]
                                                   * max(0.15, abs(math.cos(mid))))
        floor_px = max(1, math.ceil(SEED_AREA_KM2 / cell_km2))
    for f in frames:
        p = frames_dir / f"{f}.bin"
        if not p.exists():
            continue
        try:
            n = _layer_at(p, grid, layer, lat_min, lat_max, lon_min, lon_max)
        except Exception:  # noqa: BLE001 — torn/corrupt frame -> treat as absent
            continue
        if n >= floor_px:
            return f"{f[:4]}-{f[4:6]}-{f[6:8]}T{f[8:10]}:00:00Z"
    return fallback


def qualify_events(events, frames_dir: Path | None = None) -> None:
    """Attach e.qualified_at (a plain attribute: not a dataclass field, so
    to_dict/fingerprint/scoring caches are untouched)."""
    for e in events:
        e.qualified_at = qualified_at(e, frames_dir)
