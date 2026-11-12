"""Daily valid-point masks from the published raw-exceedance frames.

The frames lane (``data/results/raw_exceedance/``, written by
``ewb_live/viz/exceedance.py``) holds the detector's own per-6-hourly heat and
freeze bits. ANDing the four steps of a UTC day gives the SAME daily mask the
finder uses (all four 00/06/12/18Z steps past the bound, land only), so
reading the lane instead of re-deriving it means detection cannot drift from
what the map paints or from ``qualified_at`` — which already reads these bytes.

This is the input of the owner's tracking methodology (SPEC §2,
``ewb_live/events/event_tracker.py``). Where the lane does not span the scan
window, ``ewb_live/events/temperature.py`` falls back to streaming ERA5 ARCO
through the upstream helpers.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

DIRNAME = Path("data/results/raw_exceedance")
HOURS = ("00", "06", "12", "18")
# layer index inside a frame (frame_bytes writes heat, freeze, ar)
LAYERS = {"heat_wave": 0, "freeze": 1}
# (dir, index mtime) -> (dates, lats, lons, {"heat_wave": mask, "freeze": mask})
_CACHE: dict[tuple[str, float], tuple] = {}


def load_index(frames_dir: Path | None = None) -> dict | None:
    """index.json of the frames lane, or None when the lane is absent."""
    path = Path(frames_dir or DIRNAME) / "index.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def _tag_to_date(tag: str) -> dt.date:
    return dt.date(int(tag[:4]), int(tag[4:6]), int(tag[6:8]))


def complete_days(index: dict) -> list[str]:
    """Day stamps (YYYYMMDD) whose four frames are all in the lane <= cutoff.

    Only frames up to ``index["cutoff"]`` count: the last steps of a day may
    not be published yet, and a partial day is not a valid day.
    """
    cutoff = str(index.get("cutoff", ""))
    by_day: dict[str, list[str]] = {}
    for tag in index.get("frames", []):
        if len(tag) >= 10 and tag <= cutoff:
            by_day.setdefault(tag[:8], []).append(tag[8:10])
    return sorted(day for day, hours in by_day.items() if set(hours) == set(HOURS))


def spans_window(frames_dir: Path | None = None,
                start: dt.date | None = None, end: dt.date | None = None) -> bool:
    """True when complete days exist on BOTH ends of the window (inclusive).

    The lane is normally pruned to the meta window and ends at the ERA5
    mirror cutoff, so this is the cheap test for "the lane is usable for this
    window": if its head or tail is missing, retention or the frame build is
    behind and the caller should re-derive the masks from ERA5 instead.
    """
    index = load_index(frames_dir)
    if not index or start is None or end is None:
        return False
    days = {_tag_to_date(d) for d in complete_days(index)}
    return start in days and end in days and start <= end


def daily_masks(frames_dir: Path | None = None,
                ) -> tuple[list[dt.date], np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Decode the whole lane into daily masks of both layers.

    Returns:
        ``(dates, lats, lons, masks)`` where ``dates`` are the complete days in
        ascending order, ``lats``/``lons`` come from ``index["grid"]`` (row 0 =
        ``lat0``, col 0 = ``lon0`` — the frame's own display orientation), and
        ``masks["heat_wave"]`` / ``masks["freeze"]`` are boolean arrays of shape
        ``(days, ny, nx)``: a cell is True when it exceeded at all four steps.
        ``masks`` is cached per (lane, index mtime), so scanning both hazards
        decodes the files once.
    """
    from ewb_live.viz.exceedance import frame_layers

    d = Path(frames_dir or DIRNAME)
    index = load_index(d)
    if not index:
        raise FileNotFoundError(f"no raw_exceedance index under {d}")
    key = (str(d), (d / "index.json").stat().st_mtime)
    if key in _CACHE:
        dates, lats, lons, masks = _CACHE[key]
    else:
        g = index["grid"]
        ny, nx = int(g["ny"]), int(g["nx"])
        lats = float(g["lat0"]) + float(g["step_lat"]) * np.arange(ny)
        lons = float(g["lon0"]) + float(g["step_lon"]) * np.arange(nx)
        days = complete_days(index)
        dates = [_tag_to_date(t) for t in days]
        out = {hazard: np.zeros((len(days), ny, nx), bool)
               for hazard in LAYERS}
        for i, day in enumerate(days):
            planes = [frame_layers((d / f"{day}{hh}.bin").read_bytes(), (ny, nx))
                      for hh in HOURS]
            for hazard, layer in LAYERS.items():
                out[hazard][i] = np.logical_and.reduce(
                    [p[layer] for p in planes])
        _CACHE.clear()          # one lane at a time; keeps ~170 MB, not many
        _CACHE[key] = (dates, lats, lons, out)
    return dates, lats, lons, _CACHE[key][3]


def clear_cache() -> None:
    """Drop the decoded lane (tests / after a rebuild)."""
    _CACHE.clear()
