"""Unit tests for the area-tracked heat/freeze detector (no network).

Contract under test (owner 2026-11-10): trigger on >=200k km2 of 3+
consecutive exceedance days, box = seed bbox + 250 km each edge, end when
the in-box area drops below 100k km2.
"""
import numpy as np
import pytest

from ewb_live.events.temperature_detect import (
    BOX_PAD_KM,
    END_AREA_KM2,
    SEED_AREA_KM2,
    detect_area_tracked_events,
)

CELL_KM2 = 10_000.0          # synthetic grid: 1 cell = 10,000 km2
SEED_CELLS = int(SEED_AREA_KM2 / CELL_KM2)      # 20 cells
END_CELLS = int(END_AREA_KM2 / CELL_KM2)        # 10 cells


def grid(ndays=10, nlat=41, nlon=41, ddeg=1.0):
    lats = np.arange(-20.0, -20.0 + nlat * ddeg, ddeg)
    lons = np.arange(0.0, 0.0 + nlon * ddeg, ddeg)
    area = np.full((nlat, nlon), CELL_KM2)
    dates = np.array([np.datetime64("2026-09-01") + np.timedelta64(i, "D")
                      for i in range(ndays)], dtype="datetime64[ns]")
    return dates, lats, lons, area


def run(mask, dates, lats, lons, area, **kw):
    kw.setdefault("pad_km", BOX_PAD_KM)
    return detect_area_tracked_events(mask, dates, lats, lons, area,
                                      "heat_wave", **kw)


def test_triggers_on_third_day_and_covers_the_run():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 10:15, 5:9] = True          # 20 cells = 200,000 km2, days 0-2
    ev = run(m, dates, lats, lons, area)
    assert len(ev) == 1
    e = ev[0]
    assert e["start"] == np.datetime64("2026-09-01")
    assert e["end"] == np.datetime64("2026-09-03")     # inclusive last day
    assert e["trigger"] == np.datetime64("2026-09-03")
    assert e["seed_area_km2"] == pytest.approx(SEED_AREA_KM2)
    assert e["ongoing"] is False
    assert e["max_consecutive_days"] == 3
    assert e["area_in_box_km2"] == [SEED_AREA_KM2] * 3


def test_two_days_never_trigger():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:2, 10:15, 5:9] = True
    assert run(m, dates, lats, lons, area) == []


def test_subfloor_blob_never_triggers():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:5, 10:15, 5:8] = True          # 15 cells = 150,000 km2 < seed floor
    assert run(m, dates, lats, lons, area) == []


def test_box_is_seed_bbox_plus_250km_per_edge():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 10:15, 5:9] = True
    e = run(m, dates, lats, lons, area)[0]
    pad_rows = int(np.ceil(BOX_PAD_KM / 111.32))        # 1 deg grid -> 3 rows
    pad_cols = int(np.ceil(BOX_PAD_KM / (111.32 * 1.0)))  # 3 cols at the equator
    assert e["lat_min"] == pytest.approx(lats[10 - pad_rows])
    assert e["lat_max"] == pytest.approx(lats[14 + pad_rows])
    assert e["lon_min"] == pytest.approx(lons[5 - pad_cols])
    assert e["lon_max"] == pytest.approx(lons[8 + pad_cols])
    assert e["pad_km"] == BOX_PAD_KM


def test_pads_clamp_lat_at_the_grid_edge_and_wrap_in_longitude():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 0:5, 0:4] = True
    e = run(m, dates, lats, lons, area)[0]
    assert e["lat_min"] == lats[0]           # latitude clamps at the grid edge
    # longitude is periodic: the west pad wraps to the far side of the array
    assert e["lon_min"] == lons[(0 - 3) % len(lons)]
    assert e["lon_max"] == lons[3 + 3]          # seed east edge 3 + 3 columns


def test_ends_the_day_in_box_area_drops_below_100k():
    dates, lats, lons, area = grid(ndays=10)
    m = np.zeros((10, len(lats), len(lons)), bool)
    m[0:5, 10:15, 5:9] = True          # 20 cells through day 4
    m[5, 10:15, 5:6] = True            # day 5: 5 cells = 50,000 km2 < floor
    m[6:8, 10:15, 5:9] = True          # a full recovery does NOT reopen it
    ev = run(m, dates, lats, lons, area)
    assert len(ev) == 1
    assert ev[0]["end"] == np.datetime64("2026-09-05")   # last day = day 4
    assert ev[0]["ongoing"] is False
    # the sub-floor day is outside the event, so the series stops at day 4
    assert ev[0]["area_in_box_km2"] == [SEED_AREA_KM2] * 5


def test_area_at_or_above_the_end_floor_keeps_it_alive():
    dates, lats, lons, area = grid(ndays=10)
    m = np.zeros((10, len(lats), len(lons)), bool)
    m[0:3, 10:15, 5:9] = True
    m[3:10, 10:15, 5:7] = True         # 10 cells = exactly 100,000 km2
    ev = run(m, dates, lats, lons, area)
    assert ev[0]["end"] == np.datetime64("2026-09-10")   # last day of window
    assert ev[0]["ongoing"] is True     # window ended while still alive
    assert ev[0]["area_in_box_km2"][-1] == pytest.approx(100_000.0)
    assert len(ev[0]["area_in_box_km2"]) == 10


def test_recovered_spell_after_the_end_starts_a_new_event():
    dates, lats, lons, area = grid(ndays=12)
    m = np.zeros((12, len(lats), len(lons)), bool)
    m[0:3, 10:15, 5:9] = True
    m[4, 10:11, 5:6] = True            # day 4 below the end floor -> close
    m[5:8, 10:15, 5:9] = True          # a fresh 3-day seed, same footprint
    ev = run(m, dates, lats, lons, area)
    assert [e["start"] for e in ev] == [np.datetime64("2026-09-01"),
                                        np.datetime64("2026-09-06")]


def test_one_object_is_not_tracked_as_two_overlapping_events():
    """The upstream duplicate failure: a blob that wobbles must stay one event."""
    dates, lats, lons, area = grid(ndays=12)
    m = np.zeros((12, len(lats), len(lons)), bool)
    m[0:6, 10:15, 5:9] = True
    m[3:9, 11:16, 6:10] = True         # overlapping shifted copy, still alive
    ev = run(m, dates, lats, lons, area)
    assert len(ev) == 1


def test_disjoint_regions_are_separate_events():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 5:10, 3:7] = True
    m[0:3, 25:30, 25:29] = True
    assert len(run(m, dates, lats, lons, area)) == 2


def test_seam_crossing_seed_uses_the_wrap_arc_convention():
    dates, lats, lons, area = grid(ndays=8, nlon=360, ddeg=1.0)
    lons = np.arange(0.0, 360.0, 1.0)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 10:15, 358:360] = True
    m[0:3, 10:15, 0:2] = True          # 20 cells across the 0/360 seam
    e = run(m, dates, lats, lons, area)[0]
    assert e["seed_area_km2"] == pytest.approx(SEED_AREA_KM2)
    # padded arc runs west of 358 across the seam -> lon_min > lon_max
    assert e["lon_min"] > e["lon_max"]


def test_temperature_stats_and_run_lengths_use_active_cells_in_the_box():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:4, 10:15, 5:9] = True
    t = np.zeros((8, len(lats), len(lons)), np.float32)
    t[0:4, 10:15, 5:9] = 40.0
    t[2, 12, 7] = 45.0
    ev = run(m, dates, lats, lons, area, t2m_daily=t)
    e = ev[0]
    assert e["peak_temp_c"] == pytest.approx(45.0)
    assert e["mean_temp_c"] == pytest.approx(40.0625, abs=1e-3)  # 80 cell-days
    assert e["min_temp_c"] == pytest.approx(40.0)
    assert e["max_consecutive_days"] == 4
    assert e["peak_area_km2"] == pytest.approx(SEED_AREA_KM2)


def test_freeze_polarity_takes_the_coldest_value():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[0:3, 10:15, 5:9] = True
    t = np.full((8, len(lats), len(lons)), 5.0, np.float32)
    t[1, 11, 6] = -3.0
    e = detect_area_tracked_events(m, dates, lats, lons, area, "freeze",
                                   t2m_daily=t)[0]
    assert e["peak_temp_c"] == pytest.approx(-3.0)
    assert e["min_temp_c"] == pytest.approx(-3.0)
    assert e["type"] == "freeze"


def test_max_duration_cap_marks_the_event():
    dates, lats, lons, area = grid(ndays=12)
    m = np.zeros((12, len(lats), len(lons)), bool)
    m[:, 10:15, 5:9] = True
    e = run(m, dates, lats, lons, area, max_duration_days=4)[0]
    assert e["end"] == np.datetime64("2026-09-04")
    assert e["ongoing"] is False
    assert e["duration_capped"] is True
    # a spell that ends on its own (shorter than the cap) is never marked
    m2 = np.zeros_like(m)
    m2[0:3, 10:15, 5:9] = True
    e2 = run(m2, dates, lats, lons, area, max_duration_days=4)[0]
    assert e2["duration_capped"] is False
    assert e2["end"] == np.datetime64("2026-09-03")


def test_thresholds_are_overridable():
    dates, lats, lons, area = grid(ndays=8)
    m = np.zeros((8, len(lats), len(lons)), bool)
    m[:3, 10:13, 5:9] = True           # 12 cells = 120,000 km2
    assert run(m, dates, lats, lons, area) == []
    m[:8, 10:13, 5:9] = True           # holds for the whole window
    ev = run(m, dates, lats, lons, area, seed_area_km2=100_000.0,
             end_area_km2=50_000.0)
    assert len(ev) == 1 and ev[0]["ongoing"] is True
    assert ev[0]["start"] == np.datetime64("2026-09-01")
    assert ev[0]["end"] == np.datetime64("2026-09-08")


def test_empty_and_short_windows():
    dates, lats, lons, area = grid(ndays=2)
    m = np.zeros((2, len(lats), len(lons)), bool)
    m[:, 10:15, 5:9] = True
    assert run(m, dates, lats, lons, area) == []
