"""Tests for the frames-lane mask reader + the scan integration (no network).

The synthetic lane is a small grid — 10 x 12 cells, 1 deg apart, so a cell is
~12,300 km2 and a 5 x 5 patch is ~307k km2, above the 200k km2 qualify floor —
written with the SAME encoder the lane uses
(``ewb_live.viz.exceedance.frame_bytes``), so the reader is tested against the
real byte format. Tracking runs with a small ``region_radius``/``move_cells``
for the tiny grid; the area floors stay at the published defaults. The patch
sits away from the array edges, where scipy's border handling would otherwise
grow the region artificially.
"""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from ewb_live.events import frame_masks, temperature
from ewb_live.events.event_tracker import Params
from ewb_live.events.validate import validate_event
from ewb_live.viz.exceedance import frame_bytes

NY, NX = 10, 12
LAT0, LON0, STEP = -10.0, -25.0, 1.0
LATS = LAT0 + STEP * np.arange(NY)
LONS = LON0 + STEP * np.arange(NX)
D0 = dt.date(2026, 9, 1)
PARAMS = Params(region_radius=1, move_cells=2)     # small-grid geometry
ROWS, COLS = slice(2, 7), slice(3, 8)              # the 5 x 5 patch


def _write_lane(root: Path, days: dict[tuple[dt.date, str], tuple]) -> Path:
    """days: (date, "00"|"06"|"12"|"18") -> (heat, freeze) bool grids in
    ascending-latitude order — the order the lane stores them (index.grid.lat0
    is the south edge, so row 0 is south)."""
    out = root / "raw_exceedance"
    out.mkdir(parents=True, exist_ok=True)
    tags = sorted(f"{d:%Y%m%d}{h}" for d, h in days)
    for (d, h), (heat, freeze) in days.items():
        ar = np.zeros((NY, NX), bool)
        (out / f"{d:%Y%m%d}{h}.bin").write_bytes(frame_bytes(
            np.asarray(heat, bool), np.asarray(freeze, bool), ar))
    (out / "index.json").write_text(json.dumps({
        "schema": 3,
        "grid": {"res_deg": STEP, "ny": NY, "nx": NX, "lat0": LAT0,
                 "lon0": LON0, "step_lat": STEP, "step_lon": STEP},
        "frames": tags, "cutoff": max(tags),
        "window": {"start": tags[0], "end": max(tags)},
    }))
    return out


def _patch(rows=ROWS, cols=COLS) -> np.ndarray:
    m = np.zeros((NY, NX), bool)
    m[rows, cols] = True
    return m


def _lane(root: Path, hot_days, cold_days, straddle=False, ndays=6) -> Path:
    days = {}
    for k in range(ndays):
        d = D0 + dt.timedelta(days=k)
        if straddle:
            m = np.zeros((NY, NX), bool)
            m[ROWS, slice(0, 2)] = True
            m[ROWS, slice(NX - 2, NX)] = True   # one region across the array seam
        else:
            m = _patch()
        heat = m if k in hot_days else np.zeros((NY, NX), bool)
        freeze = m if k in cold_days else np.zeros((NY, NX), bool)
        for h in frame_masks.HOURS:
            days[(d, h)] = (heat, freeze)
    return _write_lane(root, days)


def _stats(bbox, start, end, quantile, event_type):
    """Stand-in for the truth read (the real one needs the ERA5 mirror)."""
    return {"peak_temp_c": 41.0 if event_type == "heat_wave" else -4.0,
            "mean_temp_c": 38.0 if event_type == "heat_wave" else 1.0,
            "min_temp_c": 30.0 if event_type == "heat_wave" else -4.0,
            "temp_source": "era5_mirror"}


@pytest.fixture()
def lane(tmp_path, monkeypatch):
    """Six days: heat over one patch on days 0-2, freeze over it on days 3-5.

    Three days is the tracker's minimum event length, so a two-day spell never
    becomes an event at all.
    """
    frame_masks.clear_cache()
    d = _lane(tmp_path, hot_days={0, 1, 2}, cold_days={3, 4, 5})
    monkeypatch.setattr(temperature, "_event_temp_stats", _stats)
    yield d
    frame_masks.clear_cache()


# ------------------------------------------------------------ lane reader ---

def test_daily_masks_and_the_four_steps(lane):
    dates, lats, lons, masks = frame_masks.daily_masks(lane)
    assert dates == [D0 + dt.timedelta(days=k) for k in range(6)]
    assert lats[0] == LAT0 and lons[-1] == LON0 + STEP * (NX - 1)
    assert masks["heat_wave"].shape == (6, NY, NX)
    empty = np.zeros((NY, NX), bool)
    heat_seq = [_patch()] * 3 + [empty] * 3
    freeze_seq = [empty] * 3 + [_patch()] * 3
    assert [bool((h == q).all()) for h, q in zip(masks["heat_wave"], heat_seq)] == [True] * 6
    assert [bool((f == q).all()) for f, q in zip(masks["freeze"], freeze_seq)] == [True] * 6


def test_a_day_missing_a_step_is_not_a_day(lane):
    gone = f"{D0 + dt.timedelta(days=5):%Y%m%d}18"
    (lane / f"{gone}.bin").unlink()
    idx = json.loads((lane / "index.json").read_text())
    idx["frames"] = [t for t in idx["frames"] if t != gone]
    (lane / "index.json").write_text(json.dumps(idx))
    frame_masks.clear_cache()
    dates, _, _, _ = frame_masks.daily_masks(lane)
    assert dates == [D0 + dt.timedelta(days=k) for k in range(5)]
    assert frame_masks.spans_window(lane, D0, D0 + dt.timedelta(days=4))
    assert not frame_masks.spans_window(lane, D0, D0 + dt.timedelta(days=5))


def test_frames_past_the_cutoff_do_not_count(lane):
    idx = json.loads((lane / "index.json").read_text())
    idx["cutoff"] = f"{D0 + dt.timedelta(days=3):%Y%m%d}12"   # 09-04 has 00/06/12 only
    (lane / "index.json").write_text(json.dumps(idx))
    frame_masks.clear_cache()
    days = frame_masks.complete_days(frame_masks.load_index(lane))
    assert [dt.datetime.strptime(t, "%Y%m%d").date() for t in days] \
        == [D0 + dt.timedelta(days=k) for k in range(3)]


def test_missing_lane_raises_and_spans_window_is_false():
    assert not frame_masks.spans_window(Path("/nonexistent"), D0, D0)
    with pytest.raises(FileNotFoundError):
        frame_masks.daily_masks(Path("/nonexistent"))


# ------------------------------------------------------------- scan path ---

def test_scan_over_a_synthetic_lane(lane):
    evs = temperature.scan_temperature_events(D0, D0 + dt.timedelta(days=5),
                                              "heat_wave", frames_dir=lane,
                                              params=PARAMS)
    assert len(evs) == 1
    e = evs[0]
    assert e.event_type == "heat_wave"
    assert e.start == dt.datetime(2026, 9, 1)
    # end is exclusive: open on days 0-2, the core is gone on day 3
    assert e.end == dt.datetime(2026, 9, 4)
    assert e.title == "Heat Wave 2026-09-01 to 2026-09-03"
    # cell centres of the patch, not its outer edges
    assert e.bbox == pytest.approx((LATS[2], LATS[6], LONS[3], LONS[7]))
    assert e.peak_value == pytest.approx(41.0)
    d = e.details
    assert d["mask_source"] == "raw_exceedance_frames"
    assert d["qualified_date"] == "2026-09-01"
    assert d["duration_days"] == 3
    assert d["ongoing"] is False
    assert d["max_consecutive_days"] == 3
    assert d["mean_consecutive_days"] == 3.0
    assert d["peak_area_km2"] == pytest.approx(3.07e5, rel=0.02)
    assert d["window_start_truncated"] is True      # it began on the lane's first day
    assert d["anchor_box"] == list(e.bbox)          # the day-3 box is the patch
    assert d["tracker_params"]["qualify_area_km2"] == 2e5
    assert validate_event(e) == []


def test_scan_freeze_days_and_window_clipping(lane):
    evs = temperature.scan_temperature_events(D0 + dt.timedelta(days=3),
                                              D0 + dt.timedelta(days=5),
                                              "freeze", frames_dir=lane,
                                              params=PARAMS)
    assert [e.event_type for e in evs] == ["freeze"]
    assert evs[0].start == dt.datetime(2026, 9, 4)
    assert evs[0].peak_value == pytest.approx(-4.0)
    assert evs[0].details["ongoing"] is True        # still open at the window end
    assert validate_event(evs[0]) == []
    # no heat in those two days
    assert temperature.scan_temperature_events(D0 + dt.timedelta(days=3),
                                               D0 + dt.timedelta(days=5),
                                               "heat_wave", frames_dir=lane,
                                               params=PARAMS) == []


def test_scan_falls_back_when_the_lane_does_not_span_the_window(lane, monkeypatch):
    """No frames for the window -> the ERA5 ARCO path, not a silent empty scan."""
    called = {}

    def fake_arco(*a, **kw):
        called["yes"] = True
        return [], np.zeros(0), np.zeros(0), None, None, None

    monkeypatch.setattr(temperature, "_scan_masks_from_arco", fake_arco)
    assert temperature.scan_temperature_events(D0 + dt.timedelta(days=9),
                                               D0 + dt.timedelta(days=12),
                                               "heat_wave", frames_dir=lane) == []
    assert called.get("yes")


def test_scan_maps_a_seam_crossing_box_to_the_wrap_convention(tmp_path, monkeypatch):
    """A patch touching both ends of the lon array -> lon_min > lon_max (SPEC §2)."""
    monkeypatch.setattr(temperature, "_event_temp_stats", _stats)
    seam = _lane(tmp_path / "seam", hot_days={0, 1, 2, 3, 4, 5}, cold_days=set(),
                 straddle=True)
    evs = temperature.scan_temperature_events(D0, D0 + dt.timedelta(days=5),
                                              "heat_wave", frames_dir=seam,
                                              params=PARAMS)
    assert len(evs) == 1
    lat0, lat1, lon_min, lon_max = evs[0].bbox
    assert (lat0, lat1) == (LATS[2], LATS[6])
    assert (lon_min, lon_max) == (LONS[NX - 2], LONS[1])   # wraps past 180
    assert evs[0].details["antimeridian_wrap"] is True
    assert validate_event(evs[0]) == []


def test_qualification_clamps_to_the_qualify_day(tmp_path, monkeypatch):
    """qualified_at cannot precede the day the footprint reached the floor.

    A 15-cell patch (184k km2, under the 200k floor) on day 0 grows to 25 cells
    (307k) on days 1-3: the event starts on day 0 and *qualifies* on day 1. The
    box is 25 cells and day 0 already covers 15 of them — 60%, which is all the
    unclamped frame gate asks for — so without the clamp qualified_at would
    report a day on which the event had not qualified (SPEC §2: the first frame
    on/after the qualify day).
    """
    from ewb_live.events.qualification import qualified_at

    monkeypatch.setattr(temperature, "_event_temp_stats", _stats)
    days = {}
    for k in range(4):
        d = D0 + dt.timedelta(days=k)
        m = _patch(rows=slice(2, 5) if k == 0 else ROWS)
        for h in frame_masks.HOURS:
            days[(d, h)] = (m, np.zeros((NY, NX), bool))
    root = _write_lane(tmp_path / "ramp", days)

    evs = temperature.scan_temperature_events(D0, D0 + dt.timedelta(days=3),
                                              "heat_wave", frames_dir=root,
                                              params=PARAMS)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.start == dt.datetime(2026, 9, 1)            # began as a 15-cell patch
    assert ev.details["qualified_date"] == "2026-09-02"   # qualified on day 1
    assert qualified_at(ev, root) == "2026-09-02T00:00:00Z"
