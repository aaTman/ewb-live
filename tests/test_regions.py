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
    assign_regions_footprint,
    bbox_centroid,
    build_region_scorecards,
    footprint_cells,
    load_footprints,
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


# ------------------------------------------------- footprint overlap rule ----
# Owner 2026-11-10: an event belongs to every region holding >25 % of its
# FOOTPRINT (the detection mask), not to the region of its bbox centroid.

def _grid(lats, lons):
    return list(lats), list(lons)


def _mask(rows):
    return [[c == "1" for c in row] for row in rows]


def test_footprint_overlap_threshold_is_strict():
    """Membership needs MORE THAN 25 % of the footprint; exactly 25 % does not qualify."""
    # fractions are of the FOOTPRINT (cells switched on), not of the grid
    lats, lons = _grid([15.0, 15.5, 25.0, 30.0], [0.0])   # 2 tropics cells, 2 NH cells
    regs = load_regions()
    # all four cells on: tropics 2/4 = 50 %, NH 2/4 = 50 % -> both
    keys = assign_regions_footprint(lats, lons, _mask(["1", "1", "1", "1"]), regs)
    assert set(keys) == {"global", "tropics", "northern_hemisphere"}
    # one tropics cell out of four ON (3 cells): tropics 1/4 = exactly 25 %, not more
    lats3, lons3 = _grid([15.0, 25.0, 30.0, 40.0], [0.0])
    keys = assign_regions_footprint(lats3, lons3, _mask(["1", "1", "1", "1"]), regs)
    assert set(keys) == {"global", "northern_hemisphere"}


def test_footprint_straddle_beats_centroid():
    """Centroid sits in NH (bbox centroid rule -> NH only), but half the
    footprint is subtropical -> the footprint rule puts it in Tropics as well."""
    lats, lons = _grid([25.0, 26.0, 15.0, 5.0], [0.0])
    bbox = (5.0, 45.0, 0.0, 0.0)          # centroid lat 25 -> NH only under the old rule
    assert assign_regions(bbox) == ["global", "northern_hemisphere"]
    keys = assign_regions_footprint(lats, lons, _mask(["1", "1", "1", "1"]),
                                    load_regions())
    assert set(keys) == {"global", "northern_hemisphere", "tropics"}


def test_footprint_falls_back_to_largest_overlap():
    """Defensive path: if no box clears the threshold, the largest overlap wins —
    a region-less event would silently vanish from every breakdown. Needs boxes
    that do NOT partition the sphere (the hemispheric bands always clear 25 %)."""
    boxes = [{"key": "global", "label": "Global", "lat": [-90, 90], "lon": [-180, 180]},
             {"key": "a", "label": "A", "lat": [0, 10], "lon": [0, 10]},
             {"key": "b", "label": "B", "lat": [20, 30], "lon": [0, 10]}]
    lats, lons = _grid([5.0, 5.5, 25.0, 25.5, 45.0, 45.5, 65.0, 65.5], [5.0])
    # all 8 footprint cells on: 1 in A, 1 in B (12.5 % each), 6 in neither box,
    # so nothing clears 25 % and the largest overlap (first, on a tie) wins
    keys = assign_regions_footprint(lats, lons, _mask(["1"] * 8), boxes, threshold=0.25)
    assert keys == ["global", "a"]


def test_footprint_parsing_both_viz_kinds():
    ar = {"kind": "ar_ivt", "grid": {"lat": [40.0, 41.0], "lon": [0.0, 10.0]},
          "obs": {"mask": "10/01"}}
    got = footprint_cells(ar)
    assert got is not None and got[2] == [[True, False], [False, True]]
    days = {"kind": "consecutive_days",
            "grid": {"lat": [40.0, 41.0], "lon": [0.0, 10.0]},
            "obs": {"daily": ["10/00", "00/01"]}}              # OR across days
    got = footprint_cells(days)
    assert got is not None and got[2] == [[True, False], [False, True]]
    assert footprint_cells({"kind": "consecutive_days", "grid": {}, "obs": {}}) is None
    assert footprint_cells({"kind": "consecutive_days",
                            "grid": {"lat": [1.0], "lon": [1.0]}, "obs": {"daily": ["0"]}}) is None
    assert footprint_cells(None) is None


def test_footprint_bits_unpack_matches_text_masks():
    """daily_bits is the packed twin of daily (viz.build.pack_bits: day-major over
    the FLATTENED grid, one pad tail per day) — parse both, get the same footprint."""
    import base64
    import numpy as np

    days = np.array([[[True, False, True, False, True], [False, False, False, False, False]],
                     [[False, True, False, False, False], [True, False, False, False, False]]])
    parts = [np.packbits(days[d].ravel()) for d in range(2)]
    blob = np.concatenate(parts)
    packed = {"kind": "consecutive_days",
              "grid": {"lat": [10.0, 20.0], "lon": [0.0, 10.0, 20.0, 30.0, 40.0]},
              "obs": {"daily_bits": {"b64": base64.b64encode(blob.tobytes()).decode(),
                                     "ndays": 2, "ny": 2, "nx": 5}}}
    text = {"kind": "consecutive_days",
            "grid": packed["grid"],
            "obs": {"daily": ["/".join("".join("1" if v else "0" for v in r)
                                       for r in days[d]) for d in range(2)]}}
    a = footprint_cells(packed)
    b = footprint_cells(text)
    assert a is not None and b is not None and a[2] == b[2]


def test_build_region_scorecards_publishes_assignment_and_event_map():
    """Contract additions: `assignment` (rule + threshold) and `events` (id -> region
    keys) — the dashboard filters from that map, so the tables and the JS views
    can never disagree about which event is in which region."""
    import pandas as pd

    evs = [_event("eu", "heat_wave", (40.0, 50.0, 0.0, 10.0)),
           _event("sh", "freeze", (-40.0, -30.0, 140.0, 150.0))]
    df = pd.DataFrame([{"event_id": e.id, "event_type": e.event_type, "model": m,
                        "lead_time_hours": 24, "metric": "RMSE", "value": 1.0,
                        "init_time": pd.Timestamp("2026-11-02")}
                       for e in evs for m in (BASELINE, "m2")])[SCORE_COLUMNS]
    out = build_region_scorecards(evs, df)
    assert out["assignment"]["rule"] == "bbox-centroid" or "rule" in out["assignment"]
    assert out["assignment"]["events_with_footprint"] == 0
    assert out["events"] == {"eu": ["global", "northern_hemisphere", "europe"],
                             "sh": ["global", "southern_hemisphere"]}
    # with a footprint, the same event lands by area instead of by centroid
    fp = {"sh": ([ -40.0, -30.0], [140.0, 150.0], [[True, True], [True, True]])}
    out2 = build_region_scorecards(evs, df, footprints=fp)
    assert out2["assignment"]["events_with_footprint"] == 1
    assert out2["events"]["sh"] == ["global", "southern_hemisphere"]


def test_load_footprints_reads_viz_assets_and_survives_corruption(tmp_path):
    import json

    d = tmp_path / "results"
    good = d / "events" / "a"
    good.mkdir(parents=True)
    (good / "viz.json").write_text(json.dumps(
        {"kind": "ar_ivt", "grid": {"lat": [40.0], "lon": [0.0]}, "obs": {"mask": "1"}}))
    bad = d / "events" / "b"
    bad.mkdir()
    (bad / "viz.json").write_text("{ not json")
    (d / "events" / "c").mkdir()          # no asset at all
    fps = load_footprints(d, ["a", "b", "c"])
    assert list(fps) == ["a"]
