#!/usr/bin/env python3
"""tc_wind: the geometry contract between the producer and the map (tc-map).

The wind-field asset is a promise about cell placement — the map paints
`kt[i, j]` at latitude lat0 + i*0.25 and longitude lon0 + j*0.25 wrapped to
[-180, 180) — so a cell one row off here is a storm drawn 275 km off on the
map, and nothing downstream would ever notice. The round trip is therefore
tested end to end: a synthetic storm blob at a known meridian must come back at
the exact row/column the front end will paint, for a window that straddles the
dateline, with the classes, the census, the best-track bias and the missing-day
behaviour pinned alongside.

The store is a HERMETIC FAKE shaped like ERA5 ARCO — (time, lat descending
90..-90, lon ascending 0..359.75) in m/s — exposing exactly the three things
day_max_kt() asks of the real zarr (`sel(time=<exact labels>)`,
`indexes["time"]`, `ds[var].values`), so the suite needs no xarray and no
network. The real read path is smoke-tested on the ewb box.

Run: .venv/bin/python -m pytest tests/test_tc_wind.py -q
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ewb_live.viz import tc_wind as W                                       # noqa: E402
from ewb_live.viz.tc_wind import (                                          # noqa: E402
    CLASSES, NoWindData, class_of, crop, day_steps, day_windows, grid_geometry,
    ms_to_kt, rebin_max, rle_grid, unpack_rle, window_indices,
)

STEP = 0.25


# ------------------------------------------------------------------ scale --
@pytest.mark.parametrize("kt,want", [
    (0, 0), (20, 0), (33, 0), (33.9, 0),                    # below storm force
    (34, 1), (40, 1), (63, 1),                              # TS (34–63 kt)
    (64, 2), (82, 2),                                       # CAT 1
    (83, 3), (95, 3),                                       # CAT 2
    (96, 4), (112, 4),                                      # CAT 3
    (113, 5), (136, 5),                                     # CAT 4
    (137, 6), (200, 6),                                     # CAT 5, open top
])
def test_class_boundaries_are_the_saffir_simpson_thresholds(kt, want):
    assert int(class_of(np.uint8(kt))) == want


def test_scale_is_saffir_simpson_with_mph_alongside():
    assert [(c["kt"][0], c["kt"][1], c["label"]) for c in CLASSES] == [
        (34, 63, "TS"), (64, 82, "CAT 1"), (83, 95, "CAT 2"),
        (96, 112, "CAT 3"), (113, 136, "CAT 4"), (137, None, "CAT 5")]
    assert CLASSES[0]["mph"] == [39, 72]                    # the owner's 39 mph
    assert CLASSES[5]["mph"][0] == 157
    assert {c["id"] for c in CLASSES} == set(range(1, 7))
    assert all(c["tok"].startswith("--") for c in CLASSES)  # map colour tokens


def test_ms_to_kt_and_the_floor():
    assert ms_to_kt(0.0) == 0.0
    for kt in (34, 64, 83, 96, 113, 137):
        assert abs(ms_to_kt(kt / W.MS_TO_KT) - kt) < 0.02
    assert W.FLOOR_KT == 34.0 and W.HOURS == (0, 6, 12, 18)


def test_day_steps_are_the_four_utc_synoptics():
    d = dt.date(2026, 8, 19)
    assert day_steps(d) == [dt.datetime(2026, 8, 19, h) for h in (0, 6, 12, 18)]


# ---------------------------------------------------------------- windows --
def fix(t, lat, lon, v=45.0, p=1000.0):
    """Track row in the event format (ISO-Z, lat, lon, kt, hPa); `t` is the
    compact YYYYMMDDHH the timeline uses, so the fixtures stay readable."""
    return [f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:00:00Z", lat, lon, v, p]


def test_day_windows_one_box_per_utc_day_centred_on_that_days_fixes():
    w = day_windows([fix("2026081900", 20.0, 170.0), fix("2026081906", 21.0, 171.0),
                     fix("2026082000", 22.0, 172.0)])
    assert list(w) == [dt.date(2026, 8, 19), dt.date(2026, 8, 20)]
    lat_c, lon_c, half = w[dt.date(2026, 8, 19)]
    assert (round(lat_c, 4), round(lon_c, 4)) == (20.5, 170.5)
    assert half == W.HALF_DEG
    # 18Z belongs to the same UTC day; 00Z of the next day opens the next window
    w2 = day_windows([fix("2026081918", 10.0, 10.0), fix("2026082000", 12.0, 10.0)])
    assert list(w2) == [dt.date(2026, 8, 19), dt.date(2026, 8, 20)]


def test_day_windows_averages_longitudes_the_short_way_around():
    # fixes at 179 E and 179 W straddle the dateline: the centre is ±180, NOT 0
    w = day_windows([fix("2026081900", 5.0, 179.0), fix("2026081912", 5.0, -179.0)])
    _, lon_c, _ = w[dt.date(2026, 8, 19)]
    assert abs(abs(lon_c) - 180.0) < 1e-9
    _, lon0, ny, _ = grid_geometry(5.0, lon_c, W.HALF_DEG, STEP)
    assert lon0 == pytest.approx(175.0) and ny == 41


def test_day_windows_grows_for_a_fast_mover_and_caps_at_max_half():
    slow = day_windows([fix("2026081900", 20.0, 170.0)])
    fast = day_windows([fix("2026081900", 20.0, 170.0), fix("2026081918", 20.0, 185.0)])
    assert slow[list(slow)[0]][2] == W.HALF_DEG                  # nothing to grow for
    assert fast[list(fast)[0]][2] > W.HALF_DEG                   # 15° of travel
    assert fast[list(fast)[0]][2] <= W.MAX_HALF_DEG              # and capped


def test_day_windows_skips_unusable_rows_and_empty_tracks():
    assert day_windows([]) == {}
    assert day_windows([[None, 1, 2], ["bad", 1, 2], [1, None, 2], [1, 1, None]]) == {}
    assert day_windows([fix("2026081900", 91.0, 0.0)]) == {}     # impossible fix
    assert day_windows([fix("2026081900", float("nan"), 0.0)]) == {}


def test_day_windows_normalises_longitudes_into_the_maps_frame():
    # some best-track sources quote 0-360 longitudes; the map frame is [-180, 180)
    w = day_windows([fix("2026081900", 5.0, 185.0)])
    assert round(w[dt.date(2026, 8, 19)][1], 6) == -175.0


def test_grid_geometry_is_the_southwest_centre_and_rows_run_north():
    lat0, lon0, ny, nx = grid_geometry(20.0, -168.0, 5.0, STEP)
    assert (lat0, lon0, ny, nx) == (15.0, -173.0, 41, 41)
    assert lat0 + (ny - 1) * STEP == pytest.approx(25.0)         # row 0 is southern
    # the window is clamped to the grid: it slides, never runs off a pole
    n = grid_geometry(86.0, 0.0, 5.0, STEP)
    assert n[0] + (n[2] - 1) * STEP <= 89.875
    s = grid_geometry(-86.0, 0.0, 5.0, STEP)
    assert s[0] >= -89.875


def test_window_indices_picks_the_exact_cells():
    # field frame: lat ASCENDING -90..90 (row = (lat + 90)/0.25) and the ARCO
    # longitude axis 0..359.75 (col = (lon + 180)/0.25, wrapped)
    jj, ii = window_indices(15.0, -173.0, 41, 41)
    assert (jj.size, ii.size) == (41, 41)
    assert jj[0] == int((15.0 + 90) / STEP) and jj[-1] == int((25.0 + 90) / STEP)
    assert ii[0] == int((-173.0 + 180) / STEP)
    assert ii[-1] == int((-163.0 + 180) / STEP)


def test_crop_wraps_the_dateline_and_never_wraps_latitude():
    field = np.zeros((W.LAT_N, W.LON_N), np.uint8)
    field[int((20.0 + 90) / STEP), int((-178.75 + 180) / STEP)] = 70
    lat0, lon0, ny, nx = grid_geometry(20.0, 179.0, 5.0, STEP)   # centred ON 180
    assert lon0 == pytest.approx(174.0)
    sub = crop(field, lat0, lon0, ny, nx)
    assert sub.max() == 70
    # exactly where the placement contract says it must be, found BY the contract:
    # cell (i, j) is at lat0 + i*step, wrap(lon0 + j*step)
    wrap = lambda x: ((x + 180.0) % 360.0) - 180.0        # noqa: E731
    i = round((20.0 - lat0) / STEP)
    j = next(j for j in range(nx) if abs(wrap(lon0 + j * STEP) + 178.75) < 1e-9)
    assert sub[i, j] == 70
    # latitude does not wrap: a polar window cannot see the other pole's storm
    assert crop(field, 79.875, 0.0, 41, 41).max() == 0


def test_rebin_max_pools_onto_the_same_lattice_and_keeps_the_peak():
    g = np.zeros((5, 5), np.uint8)
    g[2, 2] = 70
    out = rebin_max(g, 2)
    assert out.shape == (3, 3) and out[1, 1] == 70 and out.max() == 70
    # a coarse cell is an OR over its fine cells: the weakest peak inside survives
    g2 = np.zeros((8, 8), np.uint8)
    g2[0, 0], g2[1, 1], g2[3, 3] = 40, 60, 90
    out2 = rebin_max(g2, 4)
    assert out2[0, 0] == 60 and out2[1, 1] == 90 and rebin_max(g2, 1).shape == (8, 8)


def test_rle_round_trips_and_stays_smaller_than_the_bytes():
    g = (np.arange(1681, dtype=np.uint8) % 200).reshape(41, 41)
    assert np.array_equal(unpack_rle(rle_grid(g), 1681), g.reshape(-1))
    zeros = np.zeros((41, 41), np.uint8)
    assert rle_grid(zeros) == [0, 1681]              # an empty day is two numbers
    blob = np.zeros((41, 41), np.uint8)
    blob[18:23, 12:20] = 70
    r = rle_grid(blob)
    assert len(r) // 2 < 20 and np.array_equal(unpack_rle(r, 1681), blob.reshape(-1))
    with pytest.raises(ValueError):
        unpack_rle([0, 10], 11)                      # a short run is an error, not a zero


# ------------------------------------------------- build against a fake ARCO --
LATS = np.arange(90.0, -90.0 - STEP, -STEP)          # 721, DESCENDING (ARCO)
LONS = np.arange(0.0, 360.0, STEP)                   # 1440, ASCENDING (ARCO)


class FakeVar:
    def __init__(self, values):
        self.values = values


class FakeSel:
    def __init__(self, u, v):
        self._d = {W.WIND_VARS[0]: FakeVar(u), W.WIND_VARS[1]: FakeVar(v)}

    def __getitem__(self, k):
        return self._d[k]

    def load(self):
        return self


class FakeStore:
    """ERA5 ARCO's shape and the whole API day_max_kt() uses: sel() with exact
    labels (missing labels raise, exactly as xarray does), indexes["time"], and
    ds[var].values as (time, lat, lon) m/s. `blobs` are (date, lat, lon, m/s)
    spots on a `base` m/s background; `missing_days` are absent from the axis."""

    def __init__(self, days, blobs, base=5.0):
        """`blobs` are (UTC date, lat, lon, m/s) in the MAP's [-180, 180) frame;
        the store's own longitude axis is 0..359.75, and that is this fake's job."""
        times = [t for d in days for t in day_steps(d)]
        self.times = np.array([np.datetime64(t) for t in times])
        lat_g, lon_g = np.meshgrid(LATS, LONS, indexing="ij")
        sp = np.full((len(times), LATS.size, LONS.size), base, np.float32)
        for n, t in enumerate(times):
            d = t.date()
            for bd, blat, blon, bms in blobs:
                if bd != d:
                    continue
                hit = (np.abs(lat_g - blat) <= 0.125) & (np.abs(lon_g - (blon % 360.0)) <= 0.125)
                sp[n][hit] = bms
        self.sp = sp
        self.indexes = {"time": FakeIndex(self.times)}
        self.missing_days = ()

    def sel(self, time):
        want = np.atleast_1d(np.asarray(time, dtype="datetime64[ns]"))
        if not np.isin(want, self.times).all():
            raise KeyError("some index labels are not present in the axis")
        idx = [int(np.where(self.times == t)[0][0]) for t in want]
        u = self.sp[idx]
        return FakeSel(u, np.zeros_like(u))

    def __getitem__(self, keys):
        return self              # the module pre-selects the two wind vars


class FakeIndex:
    def __init__(self, times):
        self.values = times


def tc_event(eid, name, rows, peak_kt, *, start="2026-08-19", end="2026-08-21",
             category="4"):
    return {"id": eid, "event_type": "tropical_cyclone", "title": f"TC {name}",
            "start": start, "end": end, "peak_value": peak_kt,
            "bbox": [15.0, 174.0, 26.0, -176.0],
            "details": {"track": rows, "track_step_h": 6, "intensity_units": "kt",
                        "peak_wind_kt": peak_kt, "peak_category": category,
                        "min_pressure_hpa": 960.0, "basin_name": "EP",
                        "name": name, "track_crosses_antimeridian": True}}


def test_build_places_the_wind_field_where_the_map_will_paint_it(tmp_path):
    """A 70 kt blob at 20.5 N / 178.75 E comes back at the row/column the map
    will paint, on the day it exists, with the best-track bias recorded."""
    blob = (dt.date(2026, 8, 19), 20.5, 178.75, 36.0)      # 36 m/s = 70 kt = CAT 1
    store = FakeStore([dt.date(2026, 8, 19), dt.date(2026, 8, 20)], [blob])
    (tmp_path / "events_selected.json").write_text(json.dumps([tc_event(
        "a" * 12, "Lala",
        [fix("2026081900", 20.0, 179.0, 60.0, 985.0),
         fix("2026081912", 21.0, -179.0, 65.0, 980.0),
         fix("2026082000", 22.0, -178.0, 60.0, 985.0)], 115.0)]))

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    assert doc["schema"] == 1 and doc["units"] == "kt"
    assert doc["lane"] == "tropical_cyclone"      # which lane this field explains
    assert doc["cadence_h"] == 6 and doc["step_deg"] == 1.0 and doc["read_deg"] == 0.25
    assert doc["floor_kt"] == 34.0 and doc["half_deg"] == W.HALF_DEG
    assert "ERA5" in doc["source"] and "10 m" in doc["source"]
    assert "under" in doc["bias"]["note"].lower()          # the honest caveat

    (storm,) = doc["storms"]
    assert storm["id"] == "a" * 12 and storm["name"] == "Lala"
    assert storm["best_peak_kt"] == 115.0 and storm["best_category"] == "4"
    assert storm["era_peak_kt"] == 70 and storm["ratio"] == pytest.approx(0.61)
    assert storm["peak_day"] == "2026-08-19"
    days = {d["d"]: d for d in storm["days"]}
    assert set(days) == {"2026-08-19", "2026-08-20"}

    d = days["2026-08-19"]
    # window centred on that day's fixes: lat 20.5, lon ±180 -> 15.5 S, 175 W... west
    # rendered at the map's 1° step (step_deg), read at 0.25° (read_deg)
    assert (d["lat0"], d["lon0"], d["ny"], d["nx"]) == (15.5, 175.0, 11, 11)
    g = unpack_rle(d["rle"], d["ny"] * d["nx"]).reshape(d["ny"], d["nx"])
    # THE CONTRACT: cell (i, j) sits at lat0 + i*step_deg, wrap(lon0 + j*step_deg)
    wrap = lambda x: ((x + 180.0) % 360.0) - 180.0        # noqa: E731
    i = round((20.5 - d["lat0"]) / 1.0)
    j = next(j for j in range(11) if abs(wrap(d["lon0"] + j * 1.0) - 179.0) < 1e-9)
    assert (i, j) == (5, 4)
    assert g[i, j] == 70, "the blob must land exactly where the map will draw it"
    assert int(class_of(g)[i, j]) == 2
    assert g.max() == 70 and int((g > 0).sum()) == 1 and d["max_kt"] == 70
    # the next day has no blob: an honest all-zero field, drawn as nothing
    d2 = days["2026-08-20"]
    assert d2["rle"] == [0, 121] and d2["max_kt"] == 0       # an honest empty day
    # census counts cells at the source, per class; bias is the storm-level ratio
    assert doc["census"]["2"] == 1 and doc["census"]["1"] == 0
    assert doc["bias"] == {**doc["bias"], "ratio_median": 0.61, "n": 1,
                           "ratio_min": 0.61, "ratio_max": 0.61}


def test_build_records_a_day_with_no_wind_instead_of_a_false_zero(tmp_path):
    """Past the archive cutoff there is no wind at all. The honest output is NO
    entry for that day (the map draws nothing), never a zero field that would
    read as "the storm made no storm-force wind that day"."""
    blob = (dt.date(2026, 8, 19), 20.0, 179.0, 36.0)
    store = FakeStore([dt.date(2026, 8, 19), dt.date(2026, 8, 20)], [blob])
    store.times = store.times[store.times < np.datetime64("2026-08-20")]
    store.sp = store.sp[:len(store.times)]                # 20 Aug: not in the axis
    store.indexes = {"time": FakeIndex(store.times)}
    (tmp_path / "events_selected.json").write_text(json.dumps([tc_event(
        "b" * 12, "Saudel",
        [fix("2026081900", 20.0, 179.0, 60.0, 985.0),
         fix("2026082000", 21.0, 178.0, 60.0, 985.0)], 90.0)]))

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert [d["d"] for d in storm["days"]] == ["2026-08-19"]     # 20 Aug is absent
    assert doc["days_no_data"] == ["2026-08-20"]


def test_build_needs_a_published_cyclone_and_says_so(tmp_path):
    (tmp_path / "events_selected.json").write_text(json.dumps(
        [{"id": "c" * 12, "event_type": "heatwave", "details": {},
          "start": "2026-08-19", "end": "2026-08-20"}]))
    doc = json.loads(W.build(tmp_path, store=None).read_text())
    assert doc["storms"] == [] and doc["census"] and doc["bias"]["n"] == 0
    assert doc["bias"]["ratio_median"] is None and "ERA5" in doc["source"]


def test_day_max_kt_takes_the_max_over_the_days_steps_and_floors_the_rest():
    """Below 34 kt a cell is 0 (the floor is the layer's own threshold), a missing
    step drops out of the max, and a day with no steps at all is an absence."""
    lat_g, lon_g = np.meshgrid(LATS, LONS, indexing="ij")
    day = dt.date(2026, 8, 19)
    store = FakeStore([day], [(day, 20.0, -179.0, 10.0), (day, 20.0, -178.75, 20.0)])
    # the field is in the map frame: col = (lon + 180)/0.25
    field = W.day_max_kt(store, day)
    assert field[int((20.0 + 90) / STEP), int((-179.0 + 180) / STEP)] == 0    # 19 kt
    assert field[int((20.0 + 90) / STEP), int((-178.75 + 180) / STEP)] == 39   # 39 kt
    assert field.max() == 39
    with pytest.raises(NoWindData):
        W.day_max_kt(store, dt.date(2026, 9, 1))            # not in the store at all
