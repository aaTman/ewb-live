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
  (e.g. the frames end at the ERA5 mirror cutoff before the event start, or
  a small persistence box never sees a 200k km2 frame inside it), qualified_at
  falls back to 00:00 UTC of the LATER of the event start date and
  details.qualified_date - never before the footprint reached the floor
  (review #6, 2026-11-21).

`cold_snap` (owner 2026-11-16) reads the freeze frame layer: the lane's p15 bit
IS the cold-snap mask (cold snap = the freeze rule minus the <=0C cap), so the
200k km^2 frame gate is exactly this event type's own frame-level criterion —
no new lane, no new floor (see ewb_live/events/cold_snap.py).

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

from . import frame_masks

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
    # observation-backed lanes have no raster exceedance layer to qualify
    # against: tropical_cyclone's first qualifying fix IS the qualification
    # instant at its own 3/6-hourly resolution (tc lane, 2026-11-16), a daily
    # lane such as flood qualifies at 00:00 UTC of its first qualifying day.
    if event.event_type not in ("heat_wave", "freeze", "cold_snap",
                                "atmospheric_river"):
        return f"{event.start:%Y-%m-%dT%H:%M:%SZ}"
    frames_dir = Path(frames_dir or DIRNAME)
    idx_p = frames_dir / "index.json"
    # the fallback day: the start, but never before details.qualified_date
    # (SPEC §2). The smaller persistence box can leave every frame under the
    # frame floor - returning the raw start then would publish a first-
    # qualifying instant days before the footprint reached the area floor
    # (review #6: heat_wave-20260909-264, 09-09T00Z vs qualified 09-12).
    qd = str((getattr(event, "details", None) or {}).get("qualified_date")
             or event.start.date())
    fallback_day = max(str(event.start.date()), qd)
    fallback = f"{fallback_day}T00:00:00Z"
    if not idx_p.exists():
        return fallback
    try:
        idx = json.loads(idx_p.read_text())
        grid = idx["grid"]
        # heat/freeze: the tracker knows the day the footprint first reached the
        # area floor (details.qualified_date). Start the frame scan there, so a
        # box that later grew over other heat cannot qualify before its own
        # footprint did. AR events carry no qualified_date -> lo == start date.
        lo = fallback_day
        frames = [f for f in idx["frames"]
                  if f"{f[:4]}-{f[4:6]}-{f[6:8]}" >= lo
                  and f"{f[:4]}-{f[4:6]}-{f[6:8]}" < f"{event.end:%Y-%m-%d}"]
    except (ValueError, KeyError):
        return fallback
    if not frames:
        return fallback
    # the frame layer of the type's own mask: heat/freeze their bits, cold_snap
    # the p15 freeze bit via frame_masks.MASK_ALIAS (that bit IS its mask,
    # cold_snap.py); LAYERS only lists the two daily temperature layers the
    # mask reader decodes, the AR paint bit is the frame's third layer
    hazard = frame_masks.MASK_ALIAS.get(event.event_type, event.event_type)
    layer = frame_masks.LAYERS.get(hazard, 2)
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
        # mlc lane (2026-11-17): track lanes have no exceedance raster to
        # qualify against — the first qualifying fix IS the qualifying instant,
        # at its own 6-hourly resolution (same rule the tc lane uses for
        # tropical_cyclone; if that lane's equivalent branch lands, keep one).
        if e.event_type == "midlat_cyclone":
            e.qualified_at = f"{e.start:%Y-%m-%dT%H:%M:%SZ}"
        else:
            e.qualified_at = qualified_at(e, frames_dir)
