#!/usr/bin/env python3
"""tc_wind: the geometry contract and the DISSIPATION rule (tc-map).

Two promises are tested here.

1. PLACEMENT. The wind-field asset is a promise about cell placement — the map
   paints `kt[i, j]` at latitude lat0 + i*step_deg and longitude lon0 + j*step_deg
   wrapped to [-180, 180) — so a cell one row off here is a storm drawn ~111 km
   off on the map, and nothing downstream would ever notice. The round trip is
   therefore tested end to end: a synthetic storm blob at a known meridian must
   come back at the exact row/column the front end will paint, for a window that
   straddles the dateline, with the classes and the census pinned alongside.

2. DISSIPATION (owner, 2026-11-18: "make sure the TCs dissipate after the storm
   ends"). A storm's field exists only inside the lane's event window, only
   around the position it had AT THAT STEP, and only where the wind is its own.
   Every test in the second half asks the same question of a synthetic storm —
   is there a single shaded cell after it is over? — for the cases that could
   break it: an end in the middle of a UTC day, an end exactly at 00Z, an
   extratropical tail that is still blowing 45 kt, two storms alive at once, a
   storm still open at the data cutoff, and ambient wind 6° away.

The store is a HERMETIC FAKE shaped like ERA5 ARCO — (time, lat descending
90..-90, lon ascending 0..359.75) in m/s — exposing exactly the three things
step_fields() asks of the real zarr (`sel(time=<exact labels>)`,
indexes["time"], `ds[var].values`), so the suite needs no xarray and no
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
    CLASSES, CORE_DEG, KEEP_DEG, NoWindData, cell_axes, centre_at, class_of, crop,
    day_steps, envelope, grid_geometry, keep_mask, life_window, live_rows, live_steps,
    ms_to_kt, rebin_max, rle_grid, snap_lattice, step_windows, unpack_rle, window_for,
    window_indices, wrap_lon,
)

STEP = 0.25
OUT = 1.0                                  # the rendered cell (W.OUT_DEG)


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


# ------------------------------------------------ life: when a storm EXISTED --
def fix(t, lat, lon, v=45.0, p=1000.0):
    """Track row in the event format (ISO-Z, lat, lon, kt, hPa); `t` is the
    compact YYYYMMDDHH the timeline uses, so the fixtures stay readable."""
    return [f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:00:00Z", lat, lon, v, p]


def ev(rows, start, end, *, ongoing=False, step_h=6.0, eid="a", name="Lala",
       peak=115.0):
    """A published tropical_cyclone event: `start` is its first qualifying fix
    and `end` its last qualifying fix + one step (exclusive), exactly as
    ewb_live/events/tc.py builds them — that window IS the storm's life."""
    return {"id": eid * 12, "event_type": "tropical_cyclone", "title": f"TC {name}",
            "start": start, "end": end, "peak_value": peak,
            "bbox": [15.0, 174.0, 26.0, -176.0],
            "details": {"track": rows, "fix_step_h": step_h, "intensity_units": "kt",
                        "peak_wind_kt": peak, "peak_category": "4", "name": name,
                        "min_pressure_hpa": 960.0, "basin_name": "EP",
                        "ongoing": ongoing, "track_crosses_antimeridian": True}}


def test_life_window_is_the_event_window_and_counts_its_live_steps():
    e = ev([fix("2026081900", 19.0, 178.0, 20.0),          # pre-genesis: outside
            fix("2026081906", 20.0, 179.0, 35.0),
            fix("2026082018", 22.0, -178.0, 40.0),
            fix("2026082106", 23.0, -176.0, 45.0)],        # ET tail: outside
           "2026-08-19T06:00:00Z", "2026-08-21T00:00:00Z")
    life = life_window(e)
    assert life["birth"] == W.to_ms("2026-08-19T06:00:00Z")
    assert life["death"] == W.to_ms("2026-08-21T00:00:00Z")
    assert life["first_fix"] == life["birth"]
    assert life["last_fix"] == W.to_ms("2026-08-20T18:00:00Z")
    assert life["alive_steps"] == 7          # 06/12/18Z + 00/06/12/18Z: no 00Z of
    assert life["ongoing"] is False          # the 21st, the window ended at 00Z
    assert [s.strftime("%d%H") for s in live_steps(life["birth"], life["death"])] \
        == ["1906", "1912", "1918", "2000", "2006", "2012", "2018"]


def test_live_rows_drop_the_pre_genesis_and_et_fixes():
    """The rows outside the window cannot drag the interpolated centre, the
    window or the shading: this is where the ET tail used to leak in."""
    e = ev([fix("2026081900", 19.0, 178.0, 20.0), fix("2026081906", 20.0, 179.0, 35.0),
            fix("2026082018", 22.0, -178.0, 40.0), fix("2026082106", 23.0, -176.0, 45.0)],
           "2026-08-19T06:00:00Z", "2026-08-21T00:00:00Z")
    rows = live_rows(e["details"]["track"], W.to_ms(e["start"]), W.to_ms(e["end"]))
    assert [W.iso_z(r[0]) for r in rows] == ["2026-08-19T06:00:00Z",
                                             "2026-08-20T18:00:00Z"]
    assert rows[0][1:] == (20.0, 179.0)
    assert rows[1][1] == 22.0 and rows[1][2] == pytest.approx(182.0)   # unwrapped
    assert live_rows([[None, 1, 2, 3, 4], ["bad", 1, 2, 3, 4], [1, None, 2, 3, 4],
                      [1, 91.0, 0.0, 3, 4], [1, 1.0, 999.0, 3, 4]], 0, 1e13) == []


def test_centre_at_interpolates_between_live_fixes_and_clamps_at_the_ends():
    rows = live_rows([fix("2026081906", 20.0, 179.0), fix("2026082018", 22.0, -178.0)],
                     W.to_ms("2026-08-19T06:00:00Z"), W.to_ms("2026-08-21T00:00:00Z"))
    mid = centre_at(rows, dt.datetime(2026, 8, 19, 18))     # 12 h of a 36 h run
    assert (round(mid[0], 4), wrap_lon(mid[1])) == (20.6667, -180.0)
    late = centre_at(rows, dt.datetime(2026, 8, 20, 19))    # past the last fix
    assert (round(late[0], 2), wrap_lon(late[1])) == (22.0, -178.0)


def test_step_windows_grow_to_hold_a_full_envelope_and_skip_dead_days():
    cent = {dt.datetime(2026, 8, 19, h): (20.0 + h / 100.0, 170.0) for h in (0, 6, 12, 18)}
    slow = step_windows(cent)
    assert list(slow) == [dt.date(2026, 8, 19)]
    _, _, half = slow[dt.date(2026, 8, 19)]
    assert half == W.HALF_DEG                              # nothing to grow for
    fast = step_windows({dt.datetime(2026, 8, 19, 0): (20.0, 170.0),
                         dt.datetime(2026, 8, 19, 18): (20.0, 185.0)})
    _, _, half_f = fast[dt.date(2026, 8, 19)]
    assert half_f > W.HALF_DEG                             # 15° of travel
    assert half_f >= 15.0 / 2 + KEEP_DEG                   # holds the envelope
    assert half_f <= W.MAX_HALF_DEG
    assert step_windows({}) == {}


def test_window_for_centres_a_day_straddling_the_dateline_the_short_way():
    lat_c, lon_c, _ = window_for([(5.0, 179.0), (5.0, -179.0)])
    assert abs(abs(lon_c) - 180.0) < 1e-9 and lat_c == 5.0
    lat0, lon0, ny, _ = grid_geometry(5.0, lon_c, W.HALF_DEG, STEP)
    assert lon0 == pytest.approx(175.0) and ny == 41


# ------------------------------------------------ geometry: the placement --
def test_grid_geometry_is_the_southwest_centre_and_rows_run_north():
    lat0, lon0, ny, nx = grid_geometry(20.0, -168.0, 5.0, STEP)
    assert (lat0, lon0, ny, nx) == (15.0, -173.0, 41, 41)
    assert lat0 + (ny - 1) * STEP == pytest.approx(25.0)         # row 0 is southern
    n = grid_geometry(86.0, 0.0, 5.0, STEP)              # clamped: it slides
    assert n[0] + (n[2] - 1) * STEP <= 90.0
    assert grid_geometry(-86.0, 0.0, 5.0, STEP)[0] >= -90.0


def test_grid_geometry_snaps_the_origin_onto_the_data_lattice():
    # an origin between data cells would be ROUNDed by window_indices, and the
    # published lat0/lon0 would then name a cell that was never read
    for lat_c, lon_c in ((20.37, 175.42), (20.13, -179.94), (0.01, 0.01)):
        lat0, lon0, ny, nx = grid_geometry(lat_c, lon_c, 5.13, STEP)
        assert abs((lat0 + 90) % STEP) < 1e-9, lat0
        assert abs((lon0 + 180) % STEP) < 1e-9, lon0
        assert ny == nx and ny % 2, "the box is symmetric about its centre"
    assert snap_lattice(175.12, STEP, -180.0) == 175.0
    assert snap_lattice(175.13, STEP, -180.0) == 175.25


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
    i = round((20.0 - lat0) / STEP)
    j = next(j for j in range(nx) if abs(wrap_lon(lon0 + j * STEP) + 178.75) < 1e-9)
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


# ---------------------------------------- the storm's OWN envelope (masks) --
def test_keep_mask_keeps_the_core_and_drops_the_distant_and_the_detached():
    ny = nx = 41
    lats, lons = cell_axes(15.0, 175.0, ny, nx, STEP)        # 15–25 N, 175–185 E
    sub = np.zeros((ny, nx), np.uint8)
    near = (round((20.0 - 15.0) / STEP), round((179.0 - 175.0) / STEP))
    sub[near] = 50                                            # the storm's core
    sub[near[0], near[1] + 1] = 40                            # attached envelope
    far = (round((20.0 - 15.0) / STEP), round((181.5 - 175.0) / STEP))
    sub[far] = 45                                             # 1.5° east, detached
    m = keep_mask(sub, lats, lons, (20.0, 179.0))
    assert m[near] and m[near[0], near[1] + 1]
    assert not m[far], "a >=34 kt patch not connected to the core is other weather"
    assert int(m.sum()) == 2


def test_keep_mask_drops_ambient_wind_beyond_keep_deg_and_a_displaced_core():
    ny = nx = 81
    lats, lons = cell_axes(10.0, 0.0, ny, nx, STEP)
    sub = np.zeros((ny, nx), np.uint8)
    far = (round((10.0 - 10.0) / STEP), round(6.0 / STEP))
    sub[far] = 60                                             # 6° away, > KEEP_DEG
    assert not keep_mask(sub, lats, lons, (10.0, 0.0))[far]
    assert keep_mask(sub, lats, lons, (10.0, 0.0)).sum() == 0
    # a core 3° from the best-track position is not the storm's: ERA5 can miss
    # the centre entirely, and shading whatever is nearby is a lie
    sub2 = np.zeros((ny, nx), np.uint8)
    sub2[0, round(3.0 / STEP)] = 45
    assert keep_mask(sub2, lats, lons, (10.0, 0.0)).sum() == 0
    assert CORE_DEG == 2.0 and KEEP_DEG == 4.0


def test_keep_mask_attributes_a_shared_cell_to_the_nearest_live_storm():
    ny = nx = 41
    lats, lons = cell_axes(18.0, 0.0, ny, nx, STEP)     # 18–28 N, 0–10 E
    sub = np.ones((ny, nx), np.uint8) * 45              # storm force everywhere
    west, east = (20.0, 2.0), (20.0, 8.0)
    mw = keep_mask(sub, lats, lons, west, [east])
    me = keep_mask(sub, lats, lons, east, [west])
    d_w = W.dist_deg(lats[:, None], lons[None, :], *west)
    d_e = W.dist_deg(lats[:, None], lons[None, :], *east)
    assert np.array_equal(mw, (d_w < d_e) & (d_w <= KEEP_DEG))
    assert np.array_equal(me, (d_e < d_w) & (d_e <= KEEP_DEG))
    assert not (mw & me).any(), "one cell cannot be shaded in two storms' names"
    # an exact tie belongs to neither, so the two halves simply touch
    assert not mw[round((20.0 - 18.0) / STEP), round((5.0) / STEP)]


def test_envelope_follows_a_thy_arm_and_stops_at_a_gap():
    m = np.zeros((9, 9), bool)
    m[4, 1:8] = True
    m[4, 6] = False                                  # a one-cell gap
    m[4, 7] = True
    got = envelope(m, (4, 4))
    assert got[4, 1:6].all() and not got[4, 7]
    assert envelope(np.zeros((3, 3), bool), (1, 1)).sum() == 0


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
    """ERA5 ARCO's shape and the whole API step_fields() uses: sel() with exact
    labels (missing labels raise, exactly as xarray does), indexes["time"], and
    ds[var].values as (time, lat, lon) m/s.

    A blob is `(when, lat, lon, m/s)`: a `date` paints every step of that UTC
    day, a `datetime` paints ONLY that step — which is the whole point, because
    the dissipation tests need wind that exists on the 18Z step and not the 00Z
    one. `missing_days` are absent from the time axis entirely.
    """

    def __init__(self, days, blobs, base=5.0):
        times = [t for d in days for t in day_steps(d)]
        self.times = np.array([np.datetime64(t) for t in times])
        lat_g, lon_g = np.meshgrid(LATS, LONS, indexing="ij")
        sp = np.full((len(times), LATS.size, LONS.size), base, np.float32)
        for n, t in enumerate(times):
            for bwhen, blat, blon, bms in blobs:
                day_only = isinstance(bwhen, dt.date) and not isinstance(bwhen, dt.datetime)
                if (t.date() == bwhen) if day_only else (np.datetime64(bwhen) == t):
                    hit = ((np.abs(lat_g - blat) <= 0.125)
                           & (np.abs(lon_g - (blon % 360.0)) <= 0.125))
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


def write_events(path, events):
    (path / "events_selected.json").write_text(json.dumps(events))


def by_id(doc, i):
    return next(s for s in doc["storms"] if s["id"] == i * 12)


def day_entry(storm, day):
    return next(d for d in storm["days"] if d["d"] == day)


def grids_of(entry):
    """One day entry -> {step label: 2-D u8 kt grid}. Schema 3 stores ONE grid per
    live 6-hourly step, which is the whole point: the map paints the frame's OWN
    step, so a test that asks "what does 12Z look like" must read the 12Z grid.
    """
    n = entry["ny"] * entry["nx"]
    return {g["label"]: unpack_rle(g["rle"], n).reshape(entry["ny"], entry["nx"])
            for g in entry["grids"]}


def grid_of(entry):
    """The day's UNION over its live steps: where the storm had storm-force wind
    at any hour of that day (what the audit asks; the map never paints this)."""
    out = None
    for g in grids_of(entry).values():
        out = g if out is None else np.maximum(out, g)
    return out if out is not None else np.zeros((entry["ny"], entry["nx"]), np.uint8)


def step_labels(entry):
    return [g["label"] for g in entry["grids"]]


def entry_cells(entry):
    return int(np.count_nonzero(grid_of(entry)))


def centres_of(entry):
    """The (lat, lon) centre of every non-zero rendered cell, by THE CONTRACT."""
    lats, lons = cell_axes(entry["lat0"], entry["lon0"], entry["ny"], entry["nx"], OUT)
    ii, jj = np.nonzero(grid_of(entry))
    return [(float(lats[i]), float(lons[j])) for i, j in zip(ii, jj)]


def test_build_places_the_wind_field_where_the_map_will_paint_it(tmp_path):
    """A 70 kt blob at 20.5 N / 178.75 E comes back at the row/column the map
    will paint, on the day it exists, with the best-track bias recorded."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    blob = (d19, 20.5, 178.75, 36.0)                       # 36 m/s = 70 kt = CAT 1
    store = FakeStore([d19, d20], [blob])
    write_events(tmp_path, [ev([fix("2026081900", 20.0, 179.0, 60.0, 985.0),
                                fix("2026081912", 21.0, -179.0, 65.0, 980.0),
                                fix("2026082000", 22.0, -178.0, 60.0, 985.0)],
                               "2026-08-19", "2026-08-21")])

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    assert doc["schema"] == 3 and doc["units"] == "kt"
    assert doc["lane"] == "tropical_cyclone"      # which lane this field explains
    assert doc["cadence_h"] == 6 and doc["step_deg"] == 1.0 and doc["read_deg"] == 0.25
    assert doc["floor_kt"] == 34.0 and doc["half_deg"] == W.HALF_DEG
    assert "ERA5" in doc["source"] and "10 m" in doc["source"]
    assert "under" in doc["bias"]["note"].lower()          # the honest caveat
    assert doc["dissipation"]["keep_deg"] == KEEP_DEG      # the rule, as data
    assert "event window" in doc["dissipation"]["window"]

    (storm,) = doc["storms"]
    assert storm["id"] == "a" * 12 and storm["name"] == "Lala"
    assert storm["best_peak_kt"] == 115.0 and storm["best_category"] == "4"
    assert storm["era_peak_kt"] == 70 and storm["ratio"] == pytest.approx(0.61)
    assert storm["peak_day"] == "2026-08-19"
    # schema 3 writes NO entry for a day with no live step grid — the all-zero
    # day of schema 2 ("a field that draws nothing") is simply absent now
    assert {d["d"] for d in storm["days"]} == {"2026-08-19"}

    d = day_entry(storm, "2026-08-19")
    # rendered at the map's 1° step (step_deg), read at 0.25° (read_deg); the box
    # is centred on the day's LIVE STEP positions, snapped onto the read lattice
    assert d["ny"] == d["nx"]                 # the coarse grid the map paints
    assert abs((d["lat0"] + 90) % STEP) < 1e-9 and abs((d["lon0"] + 180) % STEP) < 1e-9
    g = grid_of(d)
    # THE CONTRACT: a coarse cell is the max of the fine cells that share its
    # corner lattice — fine index (i*0.25) pooled by round(index / 4)
    fi = round((20.5 - d["lat0"]) / STEP)
    fj = round((178.75 - d["lon0"]) / STEP)
    ci, cj = int(np.rint(fi / 4)), int(np.rint(fj / 4))
    assert abs(d["lat0"] + ci * OUT - 20.5) <= 0.5          # the cell that holds it
    assert abs(wrap_lon(d["lon0"] + cj * OUT) - 178.75) <= 0.5
    assert g[ci, cj] == 70, "the blob must land exactly where the map will draw it"
    assert int(class_of(g)[ci, cj]) == 2                    # CAT 1
    assert g.max() == 70 and int((g > 0).sum()) == 1 and d["max_kt"] == 70
    # the next day has no blob: no grid at all, so the map draws nothing (an
    # all-zero grid and "nothing to draw" must not both exist)
    assert "2026-08-20" not in {d["d"] for d in storm["days"]}
    # census counts DRAWN cells at the source, per class, summed over the asset's
    # STEP grids. The blob sits within CORE_DEG of the interpolated centre at 00Z
    # and 06Z but the centre has moved >2° away by 12Z/18Z and the lone blob is
    # not seeded — the envelope rule drops it there, so it is drawn in 2 grids.
    assert step_labels(day_entry(storm, "2026-08-19")) == ["00Z", "06Z"]
    assert doc["census"]["2"] == 2 and doc["census"]["1"] == 0
    assert doc["bias"] == {**doc["bias"], "ratio_median": 0.61, "n": 1,
                           "ratio_min": 0.61, "ratio_max": 0.61}
    assert doc["days_no_data"] == []


def test_step_fields_split_the_day_and_floor_the_rest():
    """Below 34 kt a cell is 0 (the floor is the layer's own threshold) and each
    step comes back on its own — the per-step split is what lets a field end when
    the storm does."""
    day = dt.date(2026, 8, 19)
    store = FakeStore([day], [(dt.datetime(2026, 8, 19, 6), 20.0, -179.0, 10.0),
                              (dt.datetime(2026, 8, 19, 12), 20.0, -178.75, 20.0)])
    steps = dict((t, f) for t, f in W.step_fields(store, day))
    assert [t.strftime("%H") for t, _ in W.step_fields(store, day)] == \
        ["00", "06", "12", "18"]
    j = int((-179.0 + 180) / STEP)
    i = int((20.0 + 90) / STEP)
    assert steps[dt.datetime(2026, 8, 19, 6)][i, j] == 0            # 19 kt: below
    assert steps[dt.datetime(2026, 8, 19, 12)][i, j + 1] == 39      # 39 kt: TS
    assert steps[dt.datetime(2026, 8, 19, 0)].max() == 0
    assert W.day_max_kt(store, day)[i, j + 1] == 39                 # the day's max
    with pytest.raises(NoWindData):
        W.step_fields(store, dt.date(2026, 9, 1))                   # not in the store


# ------------------------------------------------------- DISSIPATION tests --
def test_a_storm_dies_midday_and_nothing_is_shaded_after_it(tmp_path):
    """Alive 19th 06Z → 20th 06Z (exclusive). ERA5 is blowing storm force at
    EVERY step of both days, including the 12Z and 18Z steps of the 20th, 4° and
    8° east of the last live position — none of it may be shaded."""
    d19, d20, d21 = dt.date(2026, 8, 19), dt.date(2026, 8, 20), dt.date(2026, 8, 21)
    live = [dt.datetime(2026, 8, 19, 6), dt.datetime(2026, 8, 19, 12),
            dt.datetime(2026, 8, 19, 18), dt.datetime(2026, 8, 20, 0)]
    blobs = [(t, 20.0, 179.0 + n * 0.5, 30.0) for n, t in enumerate(live)]
    blobs += [(dt.datetime(2026, 8, 20, 12), 20.0, 183.0, 35.0),      # after death
              (dt.datetime(2026, 8, 20, 18), 20.0, 187.0, 40.0),
              (d21, 20.0, 189.0, 45.0)]
    store = FakeStore([d19, d20, d21], blobs)
    write_events(tmp_path, [ev([fix("2026081906", 20.0, 179.0, 40.0),
                                fix("2026082000", 20.0, 180.5, 40.0),
                                fix("2026082012", 20.0, 183.0, 45.0),      # ET tail
                                fix("2026082018", 20.0, 187.0, 50.0)],
                               "2026-08-19T06:00:00Z", "2026-08-20T06:00:00Z")])

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert storm["life"]["alive_steps"] == 4
    assert [d["d"] for d in storm["days"]] == ["2026-08-19", "2026-08-20"]
    # the end day carries ONLY the steps while the storm was alive
    assert step_labels(day_entry(storm, "2026-08-19")) == ["06Z", "12Z", "18Z"]
    assert step_labels(day_entry(storm, "2026-08-20")) == ["00Z"]
    # nothing from the 12Z/18Z wind (183°/187° E) nor the 21st's (189° E): every
    # drawn cell sits within an envelope of the live positions (179–181° E, and
    # the rendered cell is 1° wide, so 182° is the first cell that would be news)
    drawn = []
    for e in storm["days"]:
        drawn += [(lo if lo > 0 else lo + 360) for _, lo in centres_of(e)]
    assert drawn and max(drawn) < 182.0 and min(drawn) > 177.0, drawn
    # and the day AFTER the last live fix simply has no entry: nothing to draw
    assert "2026-08-21" not in {d["d"] for d in storm["days"]}
    assert W.print_audit(W.audit(tmp_path))


def test_a_storm_that_ends_at_00z_leaves_its_final_day_untouched(tmp_path):
    """`end` is exclusive, so a window ending at 2026-08-20T00Z has NO live step
    on the 20th: the 20th gets no entry at all, and the 00Z wind — real, 45 kt,
    6° east — is not shaded."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    store = FakeStore([d19, d20], [(d19, 20.0, 179.0, 30.0),
                                   (dt.datetime(2026, 8, 20, 0), 20.0, 185.0, 45.0)])
    write_events(tmp_path, [ev([fix("2026081900", 20.0, 179.0, 40.0),
                                fix("2026081918", 20.0, 180.0, 40.0),
                                fix("2026082000", 20.0, 185.0, 45.0)],
                               "2026-08-19T00:00:00Z", "2026-08-20T00:00:00Z")])
    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert [d["d"] for d in storm["days"]] == ["2026-08-19"]
    assert step_labels(day_entry(storm, "2026-08-19")) == ["00Z", "06Z", "12Z", "18Z"]
    assert all(abs(lo - 179.0) < 1.5 for _, lo in centres_of(day_entry(storm,
                                                                       "2026-08-19")))
    assert W.print_audit(W.audit(tmp_path))


def test_the_extratropical_tail_shades_nothing(tmp_path):
    """A storm that transitions and then re-deepens into a 55 kt extratropical
    gale: the lane's window stops at the last tropical-storm-force fix, so the
    gale is not this event's wind field. This was the owner's bug."""
    days = [dt.date(2026, 8, 19), dt.date(2026, 8, 20), dt.date(2026, 8, 21),
            dt.date(2026, 8, 22)]
    store = FakeStore(days, [(d, la, lo, 32.0) for d, la, lo in
                             [(days[0], 20.0, 179.0), (days[1], 22.0, 182.0),
                              (days[2], 26.0, 186.0), (days[3], 30.0, 190.0)]])
    write_events(tmp_path, [ev([fix("2026081900", 20.0, 179.0, 50.0),
                                fix("2026082000", 22.0, 182.0, 45.0),      # last TS
                                fix("2026082106", 26.0, 186.0, 55.0, 984.0),
                                fix("2026082206", 30.0, 190.0, 55.0, 978.0)],
                               "2026-08-19T00:00:00Z", "2026-08-20T06:00:00Z")])
    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert storm["life"]["last_fix"] == "2026-08-20T00:00:00Z"
    assert [d["d"] for d in storm["days"]] == ["2026-08-19", "2026-08-20"]
    pts = [p for e in storm["days"] for p in centres_of(e)]
    assert max(la for la, _ in pts) == pytest.approx(22.0)    # never 26 N / 30 N
    assert max(lo for _, lo in pts) < 184.0                   # never 186 E/190 E
    assert W.print_audit(W.audit(tmp_path))


def patch(when, lat, lon, ms=30.0, r=0.25):
    """A contiguous storm-force patch around (lat, lon): a real wind field is
    connected, and the envelope rule keeps only the part connected to the core,
    so a fixture of isolated single cells would not exercise it."""
    out = []
    for di in (-r, 0.0, r):
        for dj in (-r, 0.0, r):
            out.append((when, lat + di, lon + dj, ms))
    return out


def test_two_storms_alongside_share_the_frontier_and_each_ends_its_own_day(tmp_path):
    """One storm-force field, two storms: each cell goes to the nearer live
    centre, and the storm that dies first stops being shaded on its own date even
    though the other one is still blowing storm force 6° away."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    blobs = patch(d19, 20.0, 170.0) + patch(d19, 20.0, 171.0) \
        + patch(d19, 20.0, 176.0) + patch(d19, 20.0, 177.0) \
        + patch(d20, 20.0, 176.0) + patch(d20, 20.0, 177.0)
    store = FakeStore([d19, d20], blobs)
    west = ev([fix("2026081900", 20.0, 170.0, 40.0), fix("2026081918", 20.0, 171.0, 40.0)],
              "2026-08-19T00:00:00Z", "2026-08-20T00:00:00Z", eid="w", name="West")
    east = ev([fix("2026081900", 20.0, 176.0, 40.0), fix("2026081918", 20.0, 176.0, 40.0),
               fix("2026082012", 20.0, 176.0, 40.0)],
              "2026-08-19T00:00:00Z", "2026-08-20T18:00:00Z", eid="e", name="East")
    write_events(tmp_path, [west, east])

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    w = {d["d"]: centres_of(d) for d in by_id(doc, "w")["days"]}
    e = {d["d"]: centres_of(d) for d in by_id(doc, "e")["days"]}
    assert set(w) == {"2026-08-19"}                        # West is over on the 20th
    assert set(e) == {"2026-08-19", "2026-08-20"}
    # every drawn cell sits within one rendered cell of its own storm's track and
    # no cell is in both storms' names
    for cents, track in ((w["2026-08-19"], (170.0, 171.0)), (e["2026-08-19"], (176.0,))):
        for la, lo in cents:
            near = min(abs(W.dist_deg(np.array([la]), np.array([lo]), 20.0, t)[0])
                       for t in track)
            assert near < 1.6, (la, lo, track, near)
    assert not (set(map(tuple, w["2026-08-19"])) & set(map(tuple, e["2026-08-19"]))), \
        "one cell, one storm"
    # East keeps shading on the 20th, from its own position — not from West's
    assert all(min(abs(W.dist_deg(np.array([la]), np.array([lo]), 20.0, 176.0)[0]),
                   abs(W.dist_deg(np.array([la]), np.array([lo]), 20.0, 177.0)[0])) < 1.6
               for la, lo in e["2026-08-20"]), e["2026-08-20"]
    assert W.print_audit(W.audit(tmp_path))


def test_an_ongoing_storm_is_marked_and_shaded_to_its_last_step(tmp_path):
    """Still open when the archive ran out: marked `ongoing`, so the map draws no
    dissipation for it — while its field still stops with the data it has."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    store = FakeStore([d19, d20], [(d20, 20.0, 179.0, 30.0)])
    write_events(tmp_path, [ev([fix("2026082000", 20.0, 179.0, 40.0),
                                fix("2026082012", 20.0, 179.5, 45.0)],
                               "2026-08-20T00:00:00Z", "2026-08-20T18:00:00Z",
                               ongoing=True)])
    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert storm["life"]["ongoing"] is True
    assert [d["d"] for d in storm["days"]] == ["2026-08-20"]
    assert step_labels(day_entry(storm, "2026-08-20")) == ["00Z", "06Z", "12Z"]
    assert storm["era_peak_kt"] == 58           # 30 m/s at the only live position
    assert storm["life"]["ongoing"] is True      # its death IS the last step there
    assert storm["life"]["track_open"] is True
    assert W.print_audit(W.audit(tmp_path))


def test_a_storm_is_only_ongoing_if_the_DATA_runs_out(tmp_path):
    """`details.ongoing` says the best track ends while the system was still
    tropical — true for a storm that dissolved days before the data ended too.
    The map must not present that as still alive, so the asset's `ongoing` also
    requires a death at or after the last wind step with real values."""
    days = [dt.date(2026, 8, 19), dt.date(2026, 8, 20), dt.date(2026, 8, 21)]
    store = FakeStore(days, [(days[0], 20.0, 179.0, 30.0), (days[1], 24.0, 170.0, 30.0)])
    # alive 19th 06Z .. 20th 06Z, while the wind data runs on to the 21st 18Z
    write_events(tmp_path, [ev([fix("2026081906", 20.0, 179.0, 40.0),
                                fix("2026082000", 21.0, 178.0, 40.0)],
                               "2026-08-19T06:00:00Z", "2026-08-20T06:00:00Z",
                               ongoing=True)])
    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert storm["life"]["track_open"] is True          # what the lane published
    assert storm["life"]["ongoing"] is False            # the data went on without it
    # the cutoff is the last step THIS BUILD read with real values: the weekly
    # build's newest storms abut the archive's end, which is what makes it the
    # data cutoff (a store with later days that nothing needed is not read)
    assert doc["dissipation"]["cutoff"] == "2026-08-20T18:00:00Z"
    assert W.print_audit(W.audit(tmp_path))


def test_a_day_with_no_wind_data_leaves_no_entry_rather_than_a_false_zero(tmp_path):
    """Past the archive cutoff there is no wind at all. The honest output is NO
    entry for that day (the map draws nothing), never a zero field that would
    read as "the storm made no storm-force wind that day"."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    store = FakeStore([d19, d20], [(d19, 20.0, 179.0, 36.0)])
    store.times = store.times[store.times < np.datetime64("2026-08-20")]
    store.sp = store.sp[:len(store.times)]                # 20 Aug: not in the axis
    store.indexes = {"time": FakeIndex(store.times)}
    write_events(tmp_path, [ev([fix("2026081900", 20.0, 179.0, 60.0),
                                fix("2026082000", 21.0, 178.0, 60.0)],
                               "2026-08-19", "2026-08-21")])

    doc = json.loads(W.build(tmp_path, store=store).read_text())
    (storm,) = doc["storms"]
    assert [d["d"] for d in storm["days"]] == ["2026-08-19"]     # 20 Aug is absent
    assert doc["days_no_data"] == ["2026-08-20"]


def test_build_needs_a_published_cyclone_and_says_so(tmp_path):
    write_events(tmp_path, [{"id": "c" * 12, "event_type": "heatwave", "details": {},
                             "start": "2026-08-19", "end": "2026-08-20"}])
    doc = json.loads(W.build(tmp_path, store=None).read_text())
    assert doc["storms"] == [] and doc["census"] and doc["bias"]["n"] == 0
    assert doc["bias"]["ratio_median"] is None and "ERA5" in doc["source"]


def test_build_skips_a_storm_with_an_unusable_window(tmp_path):
    """An event whose window cannot be read is skipped and reported, never
    guessed at: a default life would be an invented dissipation date."""
    good = ev([fix("2026081906", 20.0, 179.0, 40.0)], "2026-08-19T06:00:00Z",
              "2026-08-20T06:00:00Z", eid="g", name="Good")
    bad = ev([fix("2026081906", 20.0, 179.0, 40.0)], "2026-08-19", "2026-08-19",
             eid="b", name="Bad")
    write_events(tmp_path, [bad, good])
    store = FakeStore([dt.date(2026, 8, 19)], [(dt.date(2026, 8, 19), 20.0, 179.0, 30.0)])
    doc = json.loads(W.build(tmp_path, store=store).read_text())
    assert [s["name"] for s in doc["storms"]] == ["Good"]


def test_the_audit_cli_needs_no_network_and_fails_loudly(tmp_path, capsys):
    """`python -m ewb_live.viz.tc_wind --audit` is the check the owner (and the
    CI) can run anywhere: it parses, it skips the ewb-host guard (it reads nothing
    but local JSON), and a field that outlives its storm is a non-zero exit."""
    store = FakeStore([dt.date(2026, 8, 19)], [(dt.date(2026, 8, 19), 20.0, 179.0, 30.0)])
    write_events(tmp_path, [ev([fix("2026081906", 20.0, 179.0, 40.0)],
                               "2026-08-19T06:00:00Z", "2026-08-19T18:00:00Z")])
    path = W.build(tmp_path, store=store)
    assert W.main(["--audit", "--results", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "storm" in out.splitlines()[0] and out.rstrip().endswith("clean")

    doc = json.loads(path.read_text())
    doc["storms"][0]["days"].append({**doc["storms"][0]["days"][0], "d": "2099-01-01"})
    path.write_text(json.dumps(doc))
    assert W.main(["--audit", "--results", str(tmp_path)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_audit_flags_a_field_that_outlives_its_storm(tmp_path):
    """The audit is the check the owner asked for; prove it can FAIL, or it is
    not a check."""
    d19, d20 = dt.date(2026, 8, 19), dt.date(2026, 8, 20)
    store = FakeStore([d19, d20], [(d19, 20.0, 179.0, 30.0)])
    write_events(tmp_path, [ev([fix("2026081906", 20.0, 179.0, 40.0),
                                fix("2026082000", 20.0, 179.0, 40.0)],
                               "2026-08-19T06:00:00Z", "2026-08-20T06:00:00Z")])
    path = W.build(tmp_path, store=store)
    (row,) = W.audit(tmp_path)
    assert row["ok"] and row["cells_after"] == 0 and row["cells_before"] == 0
    assert row["cells"] > 0 and row["ongoing"] is False

    # now break it: draw the field on days after the storm's death, and 7° out
    doc = json.loads(path.read_text())
    entry = doc["storms"][0]["days"][0]
    doc["storms"][0]["days"].append({**entry, "d": "2099-01-01"})
    doc["storms"][0]["days"].append({**entry, "d": "2099-01-02",
                                     "lat0": 26.0, "lon0": 172.0})
    path.write_text(json.dumps(doc))
    (row,) = W.audit(tmp_path)
    assert row["cells_after"] == 2 * entry_cells(entry) and not row["ok"]
    assert not W.print_audit([row])
