"""Region definitions and event-to-region assignment for the regional scorecard.

Single source of truth for region boxes: dashboard/regions.json (read here and
fetched by the dashboard JS). Aggregation reuses pipeline.build_scorecard
verbatim on region-filtered scores, so region rows are byte-identical in shape
(and seed-identical in CIs) to the global scorecard.json.

Assignment rule (documented for the data contract):
  An event belongs to every region whose lat/lon box (inclusive on all edges)
  contains the centroid of the event's bbox. The centroid longitude is the
  midpoint of the event's eastward arc, so antimeridian-crossing events
  (stored with lon_min > lon_max, EWB wrapped convention) land correctly.
  NH/SH boxes start at +-20 deg, so subtropical centroids belong to Tropics
  only. Multi-membership is expected (e.g. a centroid in Europe is also in
  Northern Hemisphere). Global holds every event by definition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

REGION_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "regions.json"
GLOBAL_KEY = "global"


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


def assign_regions(bbox: tuple[float, float, float, float],
                   regions: list[dict[str, Any]] | None = None) -> list[str]:
    """Region keys containing the bbox centroid (inclusive edges). Global always matches."""
    lat_c, lon_c = bbox_centroid(bbox)
    keys = []
    for r in regions if regions is not None else load_regions():
        if r["key"] == GLOBAL_KEY:
            keys.append(r["key"])
            continue
        lat_rmin, lat_rmax = r["lat"]
        lon_rmin, lon_rmax = r["lon"]
        lon_in = ((lon_c - lon_rmin) % 360.0) <= (lon_rmax - lon_rmin)
        if lat_rmin <= lat_c <= lat_rmax and lon_in:
            keys.append(r["key"])
    return keys


def build_region_scorecards(events: list[Any], scores_df: pd.DataFrame,
                            regions: list[dict[str, Any]] | None = None) -> dict:
    """Per-region scorecards from published events + the SCORE_COLUMNS contract.

    events: objects with .id/.event_type/.bbox (Event dataclass).
    Returns {"baseline", "regions": [{key,label,n_events,by_type}],
            "by_region": {key: {rows}}}. "global" is deliberately ABSENT from
    by_region (it is exactly scorecard.json; don't duplicate the blob) but
    present in "regions" for counts/order.
    """
    from .pipeline import BASELINE, build_scorecard  # lazy: pipeline imports us

    regions = regions or load_regions()
    member_ids: dict[str, set[str]] = {r["key"]: set() for r in regions}
    type_by_id = {}
    for e in events:
        for key in assign_regions(e.bbox, regions):
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
    return {"baseline": BASELINE, "regions": out_regions, "by_region": by_region}
