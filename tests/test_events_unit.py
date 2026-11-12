"""Unit tests for event detection modules (no network)."""

import datetime as dt

import numpy as np
import pytest

from ewb_live.events import EVENT_TYPES, Event, detect_events
from ewb_live.events.ar import AR_MIN_DAYS, AR_MIN_LAND_PIXELS, AR_PAD_DEG
from ewb_live.events.temperature import EVENT_CLIP
from ewb_live.events.types import make_event_id, normalize_lon_180


# ------------------------------------------------------------------ types ---

def test_event_id_stable_and_distinct():
    bbox = (30.0, 40.0, -120.0, -110.0)
    a = make_event_id("heat_wave", dt.datetime(2026, 10, 1), bbox)
    b = make_event_id("heat_wave", dt.datetime(2026, 10, 1), bbox)
    assert a == b and len(a) == 12
    assert a != make_event_id("heat_wave", dt.datetime(2026, 10, 2), bbox)
    assert a != make_event_id("freeze", dt.datetime(2026, 10, 1), bbox)


def test_event_roundtrip():
    ev = Event(
        id="abc123", event_type="heat_wave", title="t",
        start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 5),
        bbox=(10.0, 20.0, -30.0, -20.0), peak_value=1.5, details={"k": 2},
    )
    assert Event.from_dict(ev.to_dict()) == ev


def test_event_to_ewb_case_schema():
    ev = Event(id="abc123def456", event_type="freeze", title="t",
               start=dt.datetime(2026, 1, 1), end=dt.datetime(2026, 1, 4),
               bbox=(10.0, 20.0, -30.0, -20.0), peak_value=None)
    case = ev.to_ewb_case(case_id_number=7)
    assert case["event_type"] == "freeze"
    assert case["case_id_number"] == 7
    loc = case["location"]
    assert loc["type"] == "bounded_region"
    p = loc["parameters"]
    assert (p["latitude_min"], p["latitude_max"]) == (10.0, 20.0)
    assert case["start_date"] == ev.start and case["end_date"] == ev.end


def test_event_padded_clamps_lat():
    ev = Event(id="x", event_type="atmospheric_river", title="t",
               start=dt.datetime(2026, 1, 1), end=dt.datetime(2026, 1, 2),
               bbox=(86.0, 90.0, 176.0, -176.0), peak_value=None)
    lat_min, lat_max, lon_min, lon_max = ev.padded(pad_deg=5.0).bbox
    assert lat_min == 81.0 and lat_max == 90.0
    # wrapped arc convention: lon_min > lon_max means eastward arc 171 -> 189
    assert lon_min == pytest.approx(171.0) and lon_max == pytest.approx(-171.0)


def test_normalize_lon():
    assert normalize_lon_180(350.0) == pytest.approx(-10.0)
    assert normalize_lon_180(-190.0) == pytest.approx(170.0)
    assert normalize_lon_180(10.0) == pytest.approx(10.0)


# ----------------------------------------------------- detector constants ---

def test_temperature_clip_product_decision(monkeypatch):
    # 2026-11-07 product-owner decision: finders use p85 (heat) / p15
    # (freeze) against the OWB pooled climatology (owb mode is default)
    from ewb_live.events.temperature import EVENT_CLIP_MODES

    assert EVENT_CLIP["heat_wave"] == EVENT_CLIP_MODES["owb"]["heat_wave"]
    assert EVENT_CLIP["heat_wave"]["q_lower"] == 0.85
    assert EVENT_CLIP["freeze"]["q_upper"] == 0.15
    # owner-reversible quantile overrides (structural-empty finding,
    # notes/ewb.md funnel: stricter percentiles can yield ~0 events globally)
    monkeypatch.setenv("EWB_LIVE_FREEZE_CLIP", "0.05")
    monkeypatch.setenv("EWB_LIVE_HEAT_CLIP", "0.95")
    assert EVENT_CLIP["freeze"]["q_upper"] == 0.05
    assert EVENT_CLIP["heat_wave"]["q_lower"] == 0.95
    monkeypatch.delenv("EWB_LIVE_FREEZE_CLIP")
    monkeypatch.delenv("EWB_LIVE_HEAT_CLIP")
    assert EVENT_CLIP["freeze"]["q_upper"] == 0.15
    # membership/iteration must see the active keys (box bug 2026-11-06:
    # a dict-subclass with only __missing__ made `in` always False and the
    # scan guard rejected every event type)
    assert "heat_wave" in EVENT_CLIP and "freeze" in EVENT_CLIP
    assert sorted(EVENT_CLIP) == ["freeze", "heat_wave"]
    assert len(EVENT_CLIP) == 2
    # comparison mode keeps the EWB data_prep CLI defaults
    assert EVENT_CLIP_MODES["ewb_default"]["heat_wave"] == dict(
        q_lower=0.85, op_lower=">", q_upper=None, op_upper="<")
    assert EVENT_CLIP_MODES["ewb_default"]["freeze"] == dict(
        q_lower=None, op_lower=">", q_upper=0.15, op_upper="<")


def test_ar_floors_match_upstream_ar_bounds():
    from ewb_live.events._upstream import ar_bounds

    ab = ar_bounds()
    assert AR_MIN_LAND_PIXELS == ab.MIN_LAND_PIXELS_FOR_PEAK
    assert AR_MIN_DAYS == 2
    assert AR_PAD_DEG == 5.0


# --------------------------------------------- upstream tracker smoke use ---

def test_upstream_consecutive_filter_and_blobs():
    from ewb_live.events._upstream import temperature_events as te_mod

    te = te_mod()
    dates = np.array([np.datetime64("2026-10-01") + i for i in range(6)], dtype="datetime64[ns]")
    lats = np.arange(-5.0, 5.0, 1.0)
    lons = np.arange(0.0, 10.0, 1.0)
    mask = np.zeros((6, 10, 10), dtype=bool)
    mask[0:4, 2:8, 2:8] = True          # 4-day blob (>= MIN_CONSECUTIVE_DAYS)
    mask[5, 8, 8] = True                 # isolated day (filtered out)
    filt = te._consecutive_filter_np(mask, te.MIN_CONSECUTIVE_DAYS, 1)
    # upstream grace=1 path closes gaps but trims run edges: days 1..3 survive
    assert filt[1:4, 2:8, 2:8].all()
    assert not filt[5, 8, 8]

    blobs = te.detect_events(filt, dates, lats, lons, "heat_wave",
                             area_grid=None, min_seed_area_km2=0.0)
    assert len(blobs) == 1
    ev = blobs[0]
    assert np.datetime64(ev["start"]) == dates[1]
    assert np.datetime64(ev["end"]) == dates[3]


# -------------------------------------------------------- detect_events -----

def test_detect_events_validation_and_cache(tmp_path):
    with pytest.raises(ValueError):
        detect_events(dt.date(2026, 10, 2), dt.date(2026, 10, 1))
    with pytest.raises(ValueError):
        detect_events(dt.date(2026, 10, 1), dt.date(2026, 10, 2), types=("tornado",))

    # cache hit path: seed a cache file for the clamped window, no network
    from ewb_live.events import _cache_path
    from ewb_live.events.truth import truth_cutoff

    cutoff = truth_cutoff()  # uses data/cache/truth_cutoff.json (6 h TTL)
    start = cutoff.date() - dt.timedelta(days=5)
    ev = Event(id="deadbeef0000", event_type="heat_wave", title="cached",
               start=dt.datetime.combine(start, dt.time()),
               end=dt.datetime.combine(start + dt.timedelta(days=4), dt.time()),
               bbox=(0.0, 1.0, 0.0, 1.0), peak_value=1.0,
               details={"max_consecutive_days": 4, "peak_area_km2": 3e5})
    p = _cache_path(tmp_path, "heat_wave", start, cutoff.date())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(__import__("json").dumps([ev.to_dict()]))
    out = detect_events(start, cutoff.date(), types=("heat_wave",), cache_dir=tmp_path)
    assert [e.id for e in out] == ["deadbeef0000"]


# ------------------------------------------------------------ curation ----

def _ar(start_day, dur, centroid, peak=700.0, land=4, bbox=(-20, 20, -20, 20)):
    s = dt.datetime.combine(start_day, dt.time())
    return Event(
        id=f"ar{start_day}{centroid}", event_type="atmospheric_river", title="t",
        start=s, end=s + dt.timedelta(days=dur), bbox=bbox, peak_value=peak,
        details={"landfall_days": land, "peak_land_pixels": 500, "centroid_lat": centroid},
    )


def test_rank_and_cap_rules():
    from ewb_live.events.select import rank_and_cap

    s0 = dt.date(2026, 9, 1)
    events = [
        _ar(s0, 5, 45.0),                          # keep
        _ar(s0 + dt.timedelta(days=8), 20, 12.0),  # deep tropics -> drop
        _ar(s0 + dt.timedelta(days=20), 18, 50.0),  # over duration cap -> drop
        _ar(s0 + dt.timedelta(days=30), 4, 40.0, peak=300.0),  # weak IVT -> drop
        _ar(s0 + dt.timedelta(days=40), 4, 60.0, bbox=(50, 85, -140, 20)),  # huge box -> drop
    ]
    out = rank_and_cap(events, top_n=15)
    assert [e.id for e in out] == [f"ar{s0}45.0"]

    capped = rank_and_cap([_ar(s0 + dt.timedelta(days=7 * i), 4, 40.0 + i) for i in range(6)], top_n=3)
    assert len(capped) == 3  # top_n cap respected


def test_truth_source_offbyone():
    from ewb_live.scoring.truth import truth_source_for_event

    cutoff = dt.datetime(2026, 10, 30, 23)
    ev = Event(id="x", event_type="heat_wave", title="t",
               start=dt.datetime(2026, 10, 28), end=dt.datetime(2026, 10, 31),  # exclusive
               bbox=(0, 1, 0, 1), peak_value=None)
    assert truth_source_for_event(ev, cutoff) == "era5_arco"  # last valid 10-30T23 covered
    ev2 = Event(id="y", event_type="heat_wave", title="t",
                start=dt.datetime(2026, 10, 29), end=dt.datetime(2026, 11, 1),
                bbox=(0, 1, 0, 1), peak_value=None)
    assert truth_source_for_event(ev2, cutoff) == "ifs_hres_analysis"
