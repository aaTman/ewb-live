"""Region definitions and event-to-region assignment for the regional scorecard.

Single source of truth for region boxes: dashboard/regions.json (read here and
fetched by the dashboard JS). Aggregation reuses pipeline.build_scorecard
verbatim on region-filtered scores, so region rows are byte-identical in shape
(and seed-identical in CIs) to the global scorecard.json.

Assignment rule (owner decision 2026-11-10, replaces the bbox-centroid rule)
---------------------------------------------------------------------------
An event belongs to every region whose lat/lon box contains more than
``OVERLAP_FRACTION`` (default 25 %) of the event's **footprint** — the real
detection mask, not the bbox:

  * heat waves / freezes: the union of the published daily exceedance masks in
    ``data/results/events/<id>/viz.json`` (``obs.daily``),
  * atmospheric rivers: the peak-day IVT mask (``obs.mask``) — the strongest
    single footprint the asset publishes for a tracked AR.

Why: heat waves and freezes are blob-shaped, so a bbox centroid sits near the
event either way, but atmospheric rivers are long and thin — a US-East-Coast AR
was scored with 49 % of its footprint over North America while its centroid
missed the lon box by 0.1 deg, and a subtropical AR spent 22 % of its area in
the Tropics with a centroid just outside. Centroid assignment hid both.

Fallbacks, both explicit and reported in the output's ``assignment`` block:
  * no viz asset / empty mask for an event  -> bbox-centroid rule (the original
    rule, kept as ``assign_regions``),
  * an event is never region-less: if no box clears the fraction threshold
    (possible only for pathological footprints), the largest-overlap box wins.

Consequences the UI and readers must know:
  * Multi-membership is expected and now also happens across the hemispheres:
    an AR straddling the subtropical boundary with >25 % of its area in two
    bands counts in both, so ``tropics + NH + SH >= global`` (under the
    centroid rule it was exactly ``==``).
  * Region membership is computed once, server-side, and published per event in
    the ``events`` map of scorecard_by_region.json. The dashboard filters from
    that map (its JS centroid port is only the fallback for unknown ids), so the
    map, list and scorecard can never disagree with the tables.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pandas as pd

REGION_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "regions.json"
GLOBAL_KEY = "global"
OVERLAP_FRACTION = 0.25      # owner 2026-11-10: ">25 % of the footprint"
MIN_SAMPLE = 5               # owner 2026-11-10: cells below this read as small-n


def load_regions(path: Path | None = None) -> list[dict[str, Any]]:
    return json.loads((path or REGION_FILE).read_text())["regions"]


def _normalize_lon(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


def bbox_centroid(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Centroid (lat, lon) of a bbox; lon follows the eastward arc so
    wrapped boxes (lon_min > lon_max, antimeridian-crossing) are correct."""
    lat_min, lat_max, lon_min, lon_max = bbox
    dlon = (lon_max - lon_min) % 360.0
    return (lat_min + lat_max) / 2.0, _normalize_lon(lon_min + dlon / 2.0)


def _lon_in_box(lon: float, lon_min: float, lon_max: float) -> bool:
    """Inside a lon box that may itself wrap the antimeridian."""
    return ((lon - lon_min) % 360.0) <= (lon_max - lon_min)


def assign_regions(bbox: tuple[float, float, float, float],
                   regions: list[dict[str, Any]] | None = None) -> list[str]:
    """Region keys containing the bbox centroid (inclusive edges). Global always matches.

    Fallback rule — used when an event has no footprint mask. Prefer
    :func:`assign_regions_footprint`, which is what the owner asked for.
    """
    lat_c, lon_c = bbox_centroid(bbox)
    keys = []
    for r in regions if regions is not None else load_regions():
        if r["key"] == GLOBAL_KEY:
            keys.append(r["key"])
            continue
        lat_rmin, lat_rmax = r["lat"]
        lon_rmin, lon_rmax = r["lon"]
        if lat_rmin <= lat_c <= lat_rmax and _lon_in_box(lon_c, lon_rmin, lon_rmax):
            keys.append(r["key"])
    return keys


# --------------------------------------------------------------- footprints ---

def _unpack_bits(b64: str, ndays: int, ny: int, nx: int) -> list[list[list[int]]]:
    """daily_bits payload -> [day][row][col] bits (see viz.build.pack_bits:
    day-major over the FLATTENED grid, one pad tail per day, MSB first)."""
    raw = base64.b64decode(b64)
    stride = (ny * nx + 7) // 8
    days = []
    for d in range(ndays):
        base = d * stride
        day = [[(raw[base + ((y * nx + x) >> 3)] >> (7 - ((y * nx + x) & 7))) & 1
                 for x in range(nx)] for y in range(ny)]
        days.append(day)
    return days


def footprint_cells(viz: dict[str, Any] | None,
                    ) -> tuple[list[float], list[float], list[list[bool]]] | None:
    """(lats, lons, mask) of an event's footprint from its published viz asset.

    mask[i][j] is True where the event's own detection mask fires on the asset's
    lat/lon grid (cell centres). Returns None when the asset carries no usable
    mask, so callers can fall back to the centroid rule.
    """
    if not isinstance(viz, dict):
        return None
    grid = viz.get("grid") or {}
    lats, lons = grid.get("lat") or [], grid.get("lon") or []
    obs = viz.get("obs") or {}
    if not lats or not lons:
        return None
    ny, nx = len(lats), len(lons)
    acc = [[False] * nx for _ in range(ny)]
    any_cell = False

    def _or_rows(rows: list[str]) -> None:
        nonlocal any_cell
        for i, row in enumerate(rows[:ny]):
            cols = row[:nx]
            acc_row = acc[i]
            for j, ch in enumerate(cols):
                if ch == "1":
                    acc_row[j] = True
                    any_cell = True

    if viz.get("kind") == "ar_ivt":
        mask = obs.get("mask")
        if isinstance(mask, str) and mask:
            _or_rows(mask.split("/"))
    else:
        daily = obs.get("daily")
        if isinstance(daily, list) and daily:
            for day in daily:
                if isinstance(day, str):
                    _or_rows(day.split("/"))
        else:                                    # packed fallback (same truth)
            bits = obs.get("daily_bits")
            if isinstance(bits, dict) and bits.get("b64"):
                for day in _unpack_bits(bits["b64"], int(bits.get("ndays", 0)),
                                        int(bits.get("ny", ny)), int(bits.get("nx", nx))):
                    for i, row in enumerate(day[:ny]):
                        for j, v in enumerate(row[:nx]):
                            if v:
                                acc[i][j] = True
                                any_cell = True
    if not any_cell:
        return None
    return lats, lons, acc


def load_footprints(results_dir: Path | str, event_ids: list[str]) -> dict[str, tuple]:
    """event_id -> footprint_cells(viz.json) for every event that has an asset."""
    out: dict[str, tuple] = {}
    root = Path(results_dir) / "events"
    for eid in event_ids:
        p = root / str(eid) / "viz.json"
        if not p.exists():
            continue
        try:
            fp = footprint_cells(json.loads(p.read_text()))
        except Exception:                        # corrupt asset -> centroid rule
            continue
        if fp:
            out[eid] = fp
    return out


def footprint_overlap(lats: list[float], lons: list[float], mask: list[list[bool]],
                      regions: list[dict[str, Any]],
                      ) -> dict[str, float]:
    """Fraction of the footprint area inside each region box (cell-centre test)."""
    total = sum(1 for row in mask for v in row if v)
    if not total:
        return {}
    hits = {r["key"]: 0 for r in regions}
    for i, lat in enumerate(lats):
        row = mask[i]
        for j, on in enumerate(row):
            if not on:
                continue
            lon = lons[j]
            for r in regions:
                if r["key"] == GLOBAL_KEY:
                    continue
                lat_min, lat_max = r["lat"]
                if lat_min <= lat <= lat_max and _lon_in_box(lon, r["lon"][0], r["lon"][1]):
                    hits[r["key"]] += 1
    out = {GLOBAL_KEY: 1.0}
    for k, h in hits.items():
        if k != GLOBAL_KEY:
            out[k] = h / total
    return out


def assign_regions_footprint(lats: list[float], lons: list[float], mask: list[list[bool]],
                             regions: list[dict[str, Any]] | None = None,
                             threshold: float = OVERLAP_FRACTION) -> list[str]:
    """Region keys holding more than ``threshold`` of the footprint, in region order.

    Never empty: if no box clears the threshold, the largest-overlap box wins
    (a region-less event would silently vanish from every breakdown).
    """
    regions = regions or load_regions()
    frac = footprint_overlap(lats, lons, mask, regions)
    keys = [r["key"] for r in regions
            if r["key"] != GLOBAL_KEY and frac.get(r["key"], 0.0) > threshold]
    if not keys:
        best = max(((r["key"], frac.get(r["key"], 0.0)) for r in regions
                    if r["key"] != GLOBAL_KEY), key=lambda kv: kv[1], default=None)
        if best and best[1] > 0:
            keys = [best[0]]
    return [GLOBAL_KEY] + keys


# ----------------------------------------------------------- scorecard slices ---

def build_region_scorecards(events: list[Any], scores_df: pd.DataFrame,
                            regions: list[dict[str, Any]] | None = None,
                            footprints: dict[str, tuple] | None = None,
                            threshold: float = OVERLAP_FRACTION,
                            ) -> dict:
    """Per-region scorecards from published events + the SCORE_COLUMNS contract.

    events: objects with .id/.event_type/.bbox (Event dataclass).
    footprints: event_id -> (lats, lons, mask) from :func:`load_footprints`;
    events without an entry fall back to the bbox-centroid rule.
    Returns {"baseline", "assignment", "regions": [{key,label,n_events,by_type}],
            "events": {event_id: [region keys]}, "by_region": {key: {rows}}}.
    "global" is deliberately ABSENT from by_region (it is exactly scorecard.json;
    don't duplicate the blob) but present in "regions" for counts/order.
    """
    from .pipeline import BASELINE, build_scorecard  # lazy: pipeline imports us

    regions = regions or load_regions()
    footprints = footprints or {}
    member_ids: dict[str, set[str]] = {r["key"]: set() for r in regions}
    type_by_id, event_regions = {}, {}
    n_fp = 0
    for e in events:
        fp = footprints.get(e.id)
        if fp:
            keys = assign_regions_footprint(*fp, regions=regions, threshold=threshold)
            n_fp += 1
        else:
            keys = assign_regions(e.bbox, regions)
        event_regions[e.id] = keys
        for key in keys:
            member_ids[key].add(e.id)
        type_by_id[e.id] = e.event_type
    scored_ids = set(scores_df["event_id"].unique()) if len(scores_df) else set()

    out_regions, by_region = [], {}
    for r in regions:
        ids = member_ids[r["key"]] & scored_ids
        by_type: dict[str, int] = {}
        for eid in ids:
            t = type_by_id[eid]
            by_type[t] = by_type.get(t, 0) + 1
        out_regions.append({"key": r["key"], "label": r["label"],
                            "n_events": len(ids), "by_type": by_type})
        if r["key"] == GLOBAL_KEY:
            continue  # global rows == scorecard.json
        mask = scores_df["event_id"].isin(sorted(ids))
        by_region[r["key"]] = build_scorecard(scores_df.loc[mask])
    return {
        "baseline": BASELINE,
        "assignment": {"rule": "footprint-overlap", "threshold": threshold,
                       "fallback": "bbox-centroid",
                       "events_with_footprint": n_fp, "events_total": len(events),
                       "min_sample": MIN_SAMPLE},
        "regions": out_regions,
        "events": event_regions,
        "by_region": by_region,
    }
