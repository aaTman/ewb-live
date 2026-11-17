"""Cold-snap detection, its freeze relationship, and the shared plumbing.

Synthetic only (no network, no EWB): the same lane format the frames reader is
tested against — a tiny 10 x 12 grid at 1 deg, so one cell is ~12,300 km2 and
the 5 x 5 patch is ~307k km2, above the 200k km2 qualify floor. The daily
temperature ceiling that separates freeze from cold snap is driven through the
stubbed truth-mirror daily MAX, which is exactly the array the shipped scanner
ANDs onto the p15 mask (`temperature.MAX_C_BY_TYPE`).

Three spells are the contract:
  * p15 with everything above 0 C  -> cold_snap only (freeze finds nothing),
  * p15 with everything at/below 0 C -> both lanes find the same spell, and the
    curated selection keeps the freeze and drops the cold snap,
  * no p15 exceedance at all       -> neither lane publishes anything.
"""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from ewb_live.events import cold_snap as cs
from ewb_live.events import frame_masks, select, temperature
from ewb_live.events.event_tracker import Params
from ewb_live.events.qualification import qualified_at
from ewb_live.events.validate import validate_event
from ewb_live.viz.exceedance import frame_bytes

NY, NX = 10, 12
LAT0, LON0, STEP = -10.0, -25.0, 1.0
LATS = LAT0 + STEP * np.arange(NY)
LONS = LON0 + STEP * np.arange(NX)
D0 = dt.date(2026, 9, 1)
PARAMS = Params(region_radius=1, move_cells=2)      # small-grid geometry
ROWS, COLS = slice(2, 7), slice(3, 8)               # the 5 x 5 patch (~307k km2)
WINDOW = (D0, D0 + dt.timedelta(days=3))            # spell on days 0-2


def _lane(root: Path, cold_days: set[int], dmax: float, monkeypatch) -> Path:
    """Frames lane with p15 (freeze-layer) bits on `cold_days`.

    `dmax` is the daily-mean-of-maximum temperature the stubbed truth mirror
    reports for every cell — the array the scanner compares against 0 C for
    freeze (and ignores for cold_snap). The daily MEAN is reported as
    unavailable, so per-event temperatures come from the stubbed truth read.
    """
    out = root / "raw_exceedance"
    out.mkdir(parents=True, exist_ok=True)
    days: dict[tuple[dt.date, str], tuple] = {}
    for k in range(4):
        d = D0 + dt.timedelta(days=k)
        p15 = _patch() if k in cold_days else np.zeros((NY, NX), bool)
        heat = np.zeros((NY, NX), bool)
        for h in frame_masks.HOURS:
            days[(d, h)] = (heat, p15)
    tags = sorted(f"{d:%Y%m%d}{h}" for d, h in days)
    for (d, h), (heat, p15) in days.items():
        (out / f"{d:%Y%m%d}{h}.bin").write_bytes(frame_bytes(
            np.asarray(heat, bool), np.asarray(p15, bool),
            np.zeros((NY, NX), bool)))
    (out / "index.json").write_text(json.dumps({
        "schema": 3,
        "grid": {"res_deg": STEP, "ny": NY, "nx": NX, "lat0": LAT0,
                 "lon0": LON0, "step_lat": STEP, "step_lon": STEP},
        "frames": tags, "cutoff": max(tags),
        "window": {"start": tags[0], "end": max(tags)},
    }))

    def _mirror(dates, lats, lons, reduce="mean"):
        if reduce == "max":
            return np.full((len(dates), len(lats), len(lons)), dmax, np.float32)
        return None

    # cold snaps (and freezes) above 0 C are a normal outcome, so the stubbed
    # per-event truth read has to agree with the lane's temperature
    def _stats(bbox, start, end, quantile, event_type):
        cold = dmax <= 0.0
        return {"peak_temp_c": -4.0 if cold else 1.5,
                "mean_temp_c": 1.0 if cold else 3.0,
                "min_temp_c": -4.0 if cold else 1.5,
                "temp_source": "era5_mirror"}

    monkeypatch.setattr(temperature, "_mirror_t2m_daily", _mirror)
    monkeypatch.setattr(temperature, "_event_temp_stats", _stats)
    return out


def _patch() -> np.ndarray:
    m = np.zeros((NY, NX), bool)
    m[ROWS, COLS] = True
    return m


def _scan(lane, event_type):
    return temperature.scan_temperature_events(*WINDOW, event_type,
                                               frames_dir=lane, params=PARAMS)


# ------------------------------------------------------- the two rules apart --

def test_a_cold_snap_needs_no_sub_zero_temperature(tmp_path, monkeypatch):
    """p15 all day but warm air: a cold snap, and NOT a freeze."""
    lane = _lane(tmp_path, {0, 1, 2}, dmax=4.0, monkeypatch=monkeypatch)

    evs = _scan(lane, "cold_snap")
    assert len(evs) == 1
    e = evs[0]
    assert e.event_type == "cold_snap"
    assert e.title == "Cold Snap 2026-09-01 to 2026-09-03"
    assert e.start == dt.datetime(2026, 9, 1)
    assert e.end == dt.datetime(2026, 9, 4)                  # exclusive
    assert e.bbox == pytest.approx((LATS[2], LATS[6], LONS[3], LONS[7]))
    assert e.peak_value == pytest.approx(1.5)                # peak == coldest
    d = e.details
    assert d["quantile"] == 0.15 and d["operator"] == "<"
    assert d["mask_source"] == "raw_exceedance_frames"
    assert d["qualified_date"] == "2026-09-01"
    assert d["duration_days"] == 3
    assert d["peak_area_km2"] == pytest.approx(3.07e5, rel=0.02)
    # the freeze/cold-snap relationship, flagged at mask resolution
    assert d["subzero_cell_fraction"] == 0.0
    assert d["contains_subzero"] is False
    assert validate_event(e) == []

    # ... and the freeze lane stays empty: the <=0C cap is unchanged
    assert _scan(lane, "freeze") == []


def test_a_freeze_is_also_a_cold_snap(tmp_path, monkeypatch):
    """Same spell, both lanes, when the air really does freeze."""
    lane = _lane(tmp_path, {0, 1, 2}, dmax=-4.0, monkeypatch=monkeypatch)

    frz, cold = _scan(lane, "freeze"), _scan(lane, "cold_snap")
    assert [e.event_type for e in frz] == ["freeze"]
    assert [e.event_type for e in cold] == ["cold_snap"]
    assert frz[0].bbox == cold[0].bbox and frz[0].start == cold[0].start
    assert frz[0].id != cold[0].id                    # ids hash the type
    assert cold[0].details["subzero_cell_fraction"] == 1.0
    assert cold[0].details["contains_subzero"] is True
    assert validate_event(cold[0]) == []


def test_no_p15_exceedance_is_neither(tmp_path, monkeypatch):
    lane = _lane(tmp_path, set(), dmax=-4.0, monkeypatch=monkeypatch)
    assert _scan(lane, "cold_snap") == []
    assert _scan(lane, "freeze") == []


def test_cold_snap_ignores_the_freeze_cap_in_the_arco_path_too(tmp_path,
                                                               monkeypatch):
    """The cap is a per-type table, not a hard-coded branch on "freeze"."""
    assert temperature.MAX_C_BY_TYPE["freeze"] == temperature.FREEZE_MAX_C
    assert temperature.MAX_C_BY_TYPE["cold_snap"] is None
    assert cs.MAX_C is None
    # identical thresholds: cold snap is the freeze clip with nothing else
    assert temperature.EVENT_CLIP["cold_snap"] == temperature.EVENT_CLIP["freeze"]


# ------------------------------------------------------------ qualification ---

def test_qualified_at_reads_the_p15_layer(tmp_path, monkeypatch):
    """The published p15 frame lane IS the cold-snap mask, so a cold snap
    qualifies on the first frame whose p15 area inside its box reaches 200k km2 —
    the same answer the freeze layer gives."""
    lane = _lane(tmp_path, {0, 1, 2}, dmax=4.0, monkeypatch=monkeypatch)
    ev = _scan(lane, "cold_snap")[0]
    assert qualified_at(ev, lane) == "2026-09-01T00:00:00Z"

    # a spell that only reaches the floor on its second day cannot qualify
    # before that day (the frame gate is an area floor, not a presence test)
    idx = json.loads((lane / "index.json").read_text())
    day0 = f"{D0:%Y%m%d}"
    (lane / "index.json").write_text(json.dumps({**idx, "frames": [
        f for f in idx["frames"] if not f.startswith(day0)]}))
    frame_masks.clear_cache()
    dates, lats, lons, masks = frame_masks.daily_masks(lane)
    assert dates == [D0 + dt.timedelta(days=k) for k in (1, 2, 3)]
    assert (masks["cold_snap" if "cold_snap" in masks
                 else frame_masks.MASK_ALIAS["cold_snap"]] == masks["freeze"]).all()
    frame_masks.clear_cache()


# ------------------------------------------------------------ the dedupe ------

def _pair(dmax, tmp_path, monkeypatch):
    lane = _lane(tmp_path, {0, 1, 2}, dmax=dmax, monkeypatch=monkeypatch)
    return _scan(lane, "freeze"), _scan(lane, "cold_snap")


def test_freeze_subsumes_the_same_cold_snap(tmp_path, monkeypatch):
    frz, cold = _pair(-4.0, tmp_path, monkeypatch)
    decisions: list[dict] = []
    kept = cs.subsume_freeze_overlaps(list(cold) + list(frz), decisions)

    assert [e.event_type for e in kept] == ["freeze"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d["action"] == "drop" and d["reason"] == "freeze-subsumed"
    assert d["event_type"] == "cold_snap" and d["kept_type"] == "freeze"
    assert d["id"] == cold[0].id and d["kept"] == frz[0].id


def test_a_cold_snap_without_a_freeze_survives(tmp_path, monkeypatch):
    _frz, cold = _pair(4.0, tmp_path, monkeypatch)          # no freeze at all
    decisions: list[dict] = []
    kept = cs.subsume_freeze_overlaps(list(cold), decisions)
    assert [e.event_type for e in kept] == ["cold_snap"]
    assert decisions == []


def test_subsumption_only_ever_removes_cold_snaps(tmp_path, monkeypatch):
    """Freeze and heat members pass through untouched, in input order."""
    from ewb_live.events.types import Event

    cold = Event(id="c", event_type="cold_snap", title="Cold Snap 2026-09-01 to 2026-09-03",
                 start=dt.datetime(2026, 9, 1), end=dt.datetime(2026, 9, 4),
                 bbox=(10.0, 20.0, 30.0, 40.0), peak_value=1.0)
    frz = Event(id="f", event_type="freeze", title="Freeze 2026-09-01 to 2026-09-03",
                start=dt.datetime(2026, 9, 1), end=dt.datetime(2026, 9, 4),
                bbox=(10.0, 20.0, 30.0, 40.0), peak_value=-4.0)
    heat = Event(id="h", event_type="heat_wave", title="Heat Wave 2026-09-01 to 2026-09-03",
                 start=dt.datetime(2026, 9, 1), end=dt.datetime(2026, 9, 4),
                 bbox=(10.0, 20.0, 30.0, 40.0), peak_value=40.0)
    kept = cs.subsume_freeze_overlaps([cold, frz, heat], [])
    assert [e.id for e in kept] == ["f", "h"]


def test_subzero_flags_helper():
    mask = np.zeros((2, 2, 2), bool)
    mask[:, 0, 0] = True                      # one cell-day pair counting
    dmax = np.zeros((2, 2, 2), np.float32)
    dmax[0, 0, 0], dmax[1, 0, 0] = 2.0, -1.0  # day 1 froze, day 0 did not
    assert cs.subzero_flags(mask, dmax) == {"subzero_cell_fraction": 0.5,
                                            "contains_subzero": True}
    assert cs.subzero_flags(np.zeros((2, 2, 2), bool), dmax) == {}
    assert cs.subzero_flags(mask, None) == {}


# ------------------------------------------------------- curation + wiring ---

def test_detect_events_publishes_the_freeze_and_logs_the_drop(tmp_path, monkeypatch):
    """End-to-end on a synthetic lane: the catalogue keeps both lanes, the
    curated selection keeps the freeze, and curation.json carries the reason."""
    from ewb_live.events import detect_events

    lane = _lane(tmp_path, {0, 1, 2}, dmax=-4.0, monkeypatch=monkeypatch)
    monkeypatch.setattr("ewb_live.events.truth_cutoff",
                        lambda *a, **k: dt.datetime(2026, 9, 30))
    kwargs = dict(frames_dir=lane, params=PARAMS,
                  cache_dir=tmp_path / "cache")
    types = ("heat_wave", "freeze", "cold_snap")   # no AR scan (no frames kwarg)

    catalogue = detect_events(*WINDOW, types=types, top_n=None, **kwargs)
    assert sorted(e.event_type for e in catalogue) == ["cold_snap", "freeze"]

    select.LAST_DECISIONS.clear()
    chosen = detect_events(*WINDOW, types=types, top_n=15, **kwargs)
    assert [e.event_type for e in chosen] == ["freeze"]
    subsumed = [d for d in select.LAST_DECISIONS if d.get("reason") == "freeze-subsumed"]
    assert len(subsumed) == 1 and subsumed[0]["kept_type"] == "freeze"


def test_cold_snap_severity_and_merge_envelope_follow_freeze(monkeypatch):
    """Curation treats the lane like freeze: a p15 anomaly x area x days score,
    a 16-day merge cap, and 'peak is the coldest value' when fragments merge."""
    from ewb_live.events.types import Event, make_event_id

    start = dt.datetime(2026, 9, 1)
    bbox = (10.0, 20.0, 30.0, 40.0)
    det = {"peak_area_km2": 3.0e5, "max_consecutive_days": 4,
           "min_temp_c": -4.0, "mean_temp_c": 1.0, "quantile": 0.15}
    cold = Event(id=make_event_id("cold_snap", start, bbox), event_type="cold_snap",
                 title="Cold Snap 2026-09-01 to 2026-09-04", start=start,
                 end=start + dt.timedelta(days=4), bbox=bbox, peak_value=-4.0,
                 details=dict(det))
    frz = Event(id=make_event_id("freeze", start, bbox), event_type="freeze",
                title="Freeze 2026-09-01 to 2026-09-04", start=start,
                end=start + dt.timedelta(days=4), bbox=bbox, peak_value=-4.0,
                details=dict(det))
    # no climatology on this box -> anomaly 0 for both (severity 0, no crash)
    assert select.severity(cold) == select.severity(frz) == 0.0
    assert select._passes_floor(cold)                      # 4 d, sane spans
    assert select.MERGE_CAP_DAYS["cold_snap"] == select.MERGE_CAP_DAYS["freeze"]

    later = Event(id="x2", event_type="cold_snap",
                  title="Cold Snap 2026-09-05 to 2026-09-08",
                  start=start + dt.timedelta(days=4),
                  end=start + dt.timedelta(days=8), bbox=bbox,
                  peak_value=-9.0, details=dict(det, min_temp_c=-9.0,
                                                max_consecutive_days=4))
    decisions: list[dict] = []
    merged = select.merge_same_type([cold, later], decisions)
    assert len(merged) == 1
    assert merged[0].event_type == "cold_snap"
    assert merged[0].peak_value == pytest.approx(-9.0)     # coldest, not warmest
    assert merged[0].details["min_temp_c"] == pytest.approx(-9.0)
    assert merged[0].title == "Cold Snap 2026-09-01 to 2026-09-08"
    assert validate_event(merged[0]) == []


def test_scoring_treats_cold_snap_like_freeze():
    """EWB has no cold-snap event type; it is scored with freeze's defaults."""
    from ewb_live.scoring.evaluate import (EVENT_SURFACE_VARS,
                                           default_metrics)
    from ewb_live.scoring.truth import make_targets

    assert EVENT_SURFACE_VARS["cold_snap"] == ["surface_air_temperature"]
    assert [type(m).__name__ for m in default_metrics("cold_snap")] == \
        [type(m).__name__ for m in default_metrics("freeze")]
    with pytest.raises(ValueError):
        default_metrics("cold_snap_not_a_type")
    # the ERA5 target for cold snap is the freeze target (2 m t2m)
    t = make_targets("cold_snap", "era5_arco")
    assert list(t) == ["ERA5"]
    assert "surface_air_temperature" in str(t["ERA5"].variables)
