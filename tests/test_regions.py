"""Unit tests for region definitions + event→region assignment + per-region
scorecard aggregation (regional breakdowns, 2026-11-09). Offline, no network.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from ewb_live.events.types import Event
from ewb_live.pipeline import BASELINE, SCORE_COLUMNS, build_scorecard
from ewb_live.regions import (
    assign_regions,
    bbox_centroid,
    build_region_scorecards,
    load_regions,
)

REGION_KEYS = ["global", "tropics", "northern_hemisphere", "southern_hemisphere",
               "north_america", "europe"]


def _event(eid, event_type, bbox):
    return Event(id=eid, event_type=event_type, title=eid,
                 start=dt.datetime(2026, 11, 1), end=dt.datetime(2026, 11, 3),
                 bbox=tuple(bbox), peak_value=None)


# ------------------------------------------------------------- definitions ---

def test_regions_file_contract():
    """dashboard/regions.json: one source of truth; brief bounds/order pinned."""
    regions = load_regions()
    assert [r["key"] for r in regions] == REGION_KEYS
    assert [r["label"] for r in regions] == [
        "Global", "Tropics", "Northern Hemisphere", "Southern Hemisphere",
        "North America", "Europe"]
    by_key = {r["key"]: r for r in regions}
    assert by_key["tropics"]["lat"] == [-20, 20]
    assert by_key["northern_hemisphere"]["lat"] == [20, 90]
    assert by_key["southern_hemisphere"]["lat"] == [-90, -20]
    assert by_key["north_america"] == {
        "key": "north_america", "label": "North America",
        "lat": [25, 60], "lon": [-120, -75]}
    assert by_key["europe"] == {
        "key": "europe", "label": "Europe",
        "lat": [35, 75], "lon": [-12.5, 42.5]}


# --------------------------------------------------------- assignment rule ---

def test_centroid_membership():
    assert bbox_centroid((30, 40, -100, -90)) == (35.0, -95.0)
    keys = assign_regions((30, 40, -100, -90))
    assert keys == ["global", "northern_hemisphere", "north_america"]


def test_tropics_only_at_subtropical_centroid():
    # NH/SH start at +-20 deg: a centroid at lat 10 is Tropics, not NH
    assert assign_regions((5, 15, 100, 110)) == ["global", "tropics"]


def test_boundaries_inclusive():
    # centroid exactly on 20 N is in both Tropics and NH (edges inclusive)
    assert assign_regions((10, 30, 0, 10)) == ["global", "tropics", "northern_hemisphere"]
    # centroid exactly 35 N / -10 lon is inside the Europe box
    assert assign_regions((30, 40, -12, -8)) == ["global", "northern_hemisphere", "europe"]


def test_dateline_crossing_centroid():
    # wrapped box lon_min > lon_max (EWB convention): eastward arc 170 -> -170
    # has midpoint 180, i.e. lon -180, NOT 0
    lat_c, lon_c = bbox_centroid((5, 15, 170.0, -170.0))
    assert (lat_c, lon_c) == (10.0, -180.0)
    assert assign_regions((5, 15, 170.0, -170.0)) == ["global", "tropics"]
    # a wrapped box straddling the dateline in the far north is NH on both sides
    assert "northern_hemisphere" in assign_regions((60, 70, 170.0, -170.0))


def test_global_holds_every_event():
    for bbox in [(-90, -89, 0, 1), (89, 90, 0, 1), (0, 1, 179, 180)]:
        assert "global" in assign_regions(bbox)


# ------------------------------------------------------------- aggregation ---

@pytest.fixture()
def scores_fixture():
    # e_na: centroid (40, -95) -> NH + NA; e_tr: centroid (10, 40) -> tropics only
    events = [
        _event("e_na", "heat_wave", (35, 45, -100, -90)),
        _event("e_tr", "heat_wave", (5, 15, 30, 50)),
        _event("e_unscored", "freeze", (40, 50, -110, -80)),  # no score rows
    ]
    rows = []
    for eid, et in [("e_na", "heat_wave"), ("e_tr", "heat_wave")]:
        for model in (BASELINE, "graphcast"):
            for lead in (6, 24):
                rows.append({"event_id": eid, "event_type": et, "model": model,
                             "metric": "mae_k", "lead_time_hours": lead,
                             "value": 1.0 if model == BASELINE else 2.0,
                             "init_time": dt.datetime(2026, 11, 1)})
    return events, pd.DataFrame(rows)[SCORE_COLUMNS]


def test_region_aggregation(scores_fixture):
    events, df = scores_fixture
    out = build_region_scorecards(events, df)

    by_key = {r["key"]: r for r in out["regions"]}
    assert by_key["global"]["n_events"] == 2          # unscored events excluded
    assert by_key["global"]["by_type"] == {"heat_wave": 2}
    assert by_key["north_america"]["n_events"] == 1
    assert by_key["tropics"]["n_events"] == 1
    assert by_key["europe"] == {"key": "europe", "label": "Europe",
                                "n_events": 0, "by_type": {}}

    assert "global" not in out["by_region"]  # global == scorecard.json
    assert out["by_region"]["europe"] == {"rows": []}
    assert out["baseline"] == BASELINE

    # each region's rows equal the house reducer on the filtered scores
    def expect(ids):
        return build_scorecard(df[df["event_id"].isin(sorted(ids))])
    assert out["by_region"]["northern_hemisphere"] == expect({"e_na"})
    assert out["by_region"]["tropics"] == expect({"e_tr"})
    assert out["by_region"]["north_america"]["rows"]
    assert out["by_region"]["north_america"]["rows"][0]["n_events"] == 1


def test_hemispheres_partition_global(scores_fixture):
    """NH + SH + tropics centroids partition the scored universe exactly."""
    events, df = scores_fixture
    out = build_region_scorecards(events, df)
    by_key = {r["key"]: r for r in out["regions"]}
    assert (by_key["tropics"]["n_events"] + by_key["northern_hemisphere"]["n_events"]
            + by_key["southern_hemisphere"]["n_events"]) == by_key["global"]["n_events"]


def test_empty_scores(scores_fixture):
    events, df = scores_fixture
    out = build_region_scorecards(events, df.iloc[0:0])
    assert all(r["n_events"] == 0 for r in out["regions"])
    assert all(v == {"rows": []} for v in out["by_region"].values())


# ------------------------------------------------------ dev regen script ----

def test_build_region_scores_script_sees_every_scored_event(tmp_path):
    """scripts/build_region_scores.py must read BOTH event files.

    The pipeline scores and counts the curated set (events_selected.json);
    events.json is the raw candidate list and is NOT a superset — on the live
    box 4 of 28 scored events existed only in events_selected.json, so a regen
    that read events.json alone published region counts disagreeing with
    scorecard.json for the same week (2026-11-10)."""
    import importlib.util
    import json

    d = tmp_path / "results"
    d.mkdir()
    a = _event("a", "heat_wave", (40.0, 50.0, 0.0, 10.0))     # europe + NH
    b = _event("b", "freeze", (-40.0, -30.0, 140.0, 150.0))   # SH, selected-only
    (d / "events.json").write_text(json.dumps([a.to_dict()]))
    (d / "events_selected.json").write_text(json.dumps([a.to_dict(), b.to_dict()]))
    rows = [{"event_id": e.id, "event_type": e.event_type, "model": m,
             "init_time": "2026-11-02T00:00:00", "lead_time_hours": 24,
             "metric": "RMSE", "value": 1.0}
            for e in (a, b) for m in (BASELINE, "m2")]
    (d / "scores.json").write_text(json.dumps({"rows": rows}))

    p = Path(__file__).resolve().parents[1] / "scripts" / "build_region_scores.py"
    spec = importlib.util.spec_from_file_location("build_region_scores", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(str(d))

    out = json.loads((d / "scorecard_by_region.json").read_text())
    glob = next(r for r in out["regions"] if r["key"] == "global")
    assert glob["n_events"] == 2, "scored event missing from the region universe"
    assert glob["by_type"] == {"heat_wave": 1, "freeze": 1}
    assert {r["key"] for r in out["regions"]} == set(REGION_KEYS)
    # the SH-only event lands in its region's rows, and no region row is invented
    sh = out["by_region"]["southern_hemisphere"]["rows"]
    assert {r["event_type"] for r in sh} == {"freeze"}
    eu = out["by_region"]["europe"]["rows"]
    assert {r["event_type"] for r in eu} == {"heat_wave"}
