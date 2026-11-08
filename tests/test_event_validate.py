"""Offline tests for the publish validator (ewb_live/events/validate.py)."""

import datetime as dt
import json
import pathlib

import pytest

from ewb_live.events.types import Event
from ewb_live.events.validate import validate_event, validate_events


def _ev(**kw):
    base = dict(
        id="t0", event_type="heat_wave",
        title="Heat Wave 2026-08-11 to 2026-08-13",
        start=dt.datetime(2026, 8, 11), end=dt.datetime(2026, 8, 14),
        bbox=(12.25, 19.75, -9.0, -0.25), peak_value=36.86,
        details={"min_temp_c": 26.08, "mean_temp_c": 32.67,
                 "max_consecutive_days": 3, "antimeridian_wrap": False},
    )
    base.update(kw)
    return Event(**base)


def _ar(**kw):
    base = dict(
        id="t1", event_type="atmospheric_river",
        title="Atmospheric River 2026-08-04 to 2026-08-09",
        start=dt.datetime(2026, 8, 4), end=dt.datetime(2026, 8, 10),
        bbox=(27.75, 67.5, -110.75, -64.0), peak_value=1240.6,
        details={"ivt_threshold_kg_m_s": 400.0, "peak_land_pixels": 3633,
                 "min_mask_gridpoints": 500, "landfall_days": 7,
                 "centroid_lat": 48.77},
    )
    base.update(kw)
    return Event(**base)


class TestHappy:
    def test_good_heat(self):
        assert validate_event(_ev()) == []

    def test_good_ar(self):
        assert validate_event(_ar()) == []

    def test_good_freeze(self):
        e = _ev(event_type="freeze",
                title="Freeze 2026-08-15 to 2026-08-18",
                start=dt.datetime(2026, 8, 15), end=dt.datetime(2026, 8, 19),
                peak_value=-1.07,
                details={"min_temp_c": -1.07, "mean_temp_c": 7.86,
                         "max_consecutive_days": 4, "antimeridian_wrap": False})
        assert validate_event(e) == []

    def test_percentile_relative_desert_freeze_passes(self):
        """Semantics pin: EWB 'freeze' = below LOCAL p15 (SPEC §detection),
        so a Sahara event whose coldest daily mean is 18.6 degC is a VALID
        detection. The gate must not reject it on absolute coldness.
        (Live events b6344dee3fad et al., 2026-09/10 selection.)"""
        e = _ev(event_type="freeze",
                title="Freeze 2026-09-24 to 2026-09-27",
                start=dt.datetime(2026, 9, 24), end=dt.datetime(2026, 9, 28),
                bbox=(22.75, 32.25, 0.0, 13.75), peak_value=18.62,
                details={"min_temp_c": 18.62, "mean_temp_c": 25.03,
                         "max_consecutive_days": 4, "quantile": 0.15,
                         "antimeridian_wrap": False})
        assert validate_event(e) == []

    def test_freeze_kelvin_misparse_caught(self):
        e = _ev(event_type="freeze",
                title="Freeze 2026-08-15 to 2026-08-18",
                start=dt.datetime(2026, 8, 15), end=dt.datetime(2026, 8, 19),
                peak_value=272.08,  # Kelvin value leaked into a degC field
                details={"min_temp_c": 272.08, "mean_temp_c": 280.0})
        assert any("range" in p for p in validate_event(e))


class TestTitleClaims:
    def test_bad_date_pair(self):
        probs = validate_event(_ev(title="Heat Wave 2026-08-10 to 2026-08-13"))
        assert any("title start" in p for p in probs)

    def test_title_end_not_last_day(self):
        # end is exclusive: title end must be end-1day, not end
        probs = validate_event(_ev(title="Heat Wave 2026-08-11 to 2026-08-14"))
        assert any("last event day" in p for p in probs)

    def test_wrong_label(self):
        probs = validate_event(_ev(title="Cold Snap 2026-08-11 to 2026-08-13"))
        assert any("title label" in p for p in probs)

    def test_unparseable_title(self):
        assert validate_event(_ev(title="Mystery event")) != []


class TestValues:
    def test_heat_peak_out_of_range(self):
        probs = validate_event(_ev(peak_value=400.0))  # looks like Kelvin
        assert any("range" in p for p in probs)

    def test_heat_peak_below_mean(self):
        probs = validate_event(_ev(peak_value=30.0))  # mean is 32.67
        assert any("mean_temp_c" in p for p in probs)

    def test_min_above_mean(self):
        probs = validate_event(_ev(details={
            "min_temp_c": 40.0, "mean_temp_c": 32.67,
            "max_consecutive_days": 3}))
        assert any("min_temp_c 40.0 > mean_temp_c" in p for p in probs)

    def test_freeze_peak_must_equal_min(self):
        e = _ev(event_type="freeze", title="Freeze 2026-08-15 to 2026-08-18",
                peak_value=5.0, details={"min_temp_c": -1.07,
                                         "mean_temp_c": 7.86})
        assert any("!= min_temp_c" in p for p in validate_event(e))

    def test_ar_peak_below_threshold(self):
        assert any("threshold" in p for p in validate_event(_ar(peak_value=100.0)))

    def test_ar_land_pixels_below_min_mask(self):
        probs = validate_event(_ar(details={**_ar().details,
                                            "peak_land_pixels": 100}))
        assert any("min_mask_gridpoints" in p for p in probs)

    def test_ar_centroid_outside_bbox(self):
        probs = validate_event(_ar(details={**_ar().details,
                                            "centroid_lat": -80.0}))
        assert any("centroid_lat" in p for p in probs)

    def test_missing_peak(self):
        assert any("peak_value" in p for p in validate_event(_ev(peak_value=None)))


class TestGeometryTime:
    def test_bad_lat_order(self):
        assert any("latitudes" in p for p in validate_event(_ev(bbox=(40.0, 10.0, 0.0, 10.0))))

    def test_lon_out_of_range(self):
        assert any("longitudes" in p for p in validate_event(_ev(bbox=(0.0, 1.0, -200.0, 5.0))))

    def test_wrap_flag_mismatch(self):
        e = _ev(bbox=(0.0, 10.0, 170.0, -170.0),  # wrapped arc
                details={"antimeridian_wrap": False})
        assert any("antimeridian_wrap" in p for p in validate_event(e))

    def test_wrapped_arc_without_flag_is_ok(self):
        # AR events carry no wrap flag; lon_min > lon_max is the convention
        e = _ar(bbox=(27.75, 67.5, 170.0, -170.0),
                details={k: v for k, v in _ar().details.items()})
        assert not any("longitude" in p for p in validate_event(e))

    def test_inverted_time(self):
        e = _ev(start=dt.datetime(2026, 8, 15), end=dt.datetime(2026, 8, 11))
        assert any("not before end" in p for p in validate_event(e))

    def test_too_long_duration(self):
        e = _ev(start=dt.datetime(2026, 7, 1), end=dt.datetime(2026, 8, 20),
                title="Heat Wave 2026-07-01 to 2026-08-19")
        assert any("duration" in p for p in validate_event(e))


class TestGateIntegration:
    def test_validate_events_maps_only_failures(self):
        bad = _ev(id="bad", peak_value=400.0)
        out = validate_events([_ev(), bad, _ar()])
        assert set(out) == {"bad"}

    def test_current_events_selected_all_pass(self):
        """Regression guard: the live curated set must clear the gate."""
        p = (pathlib.Path(__file__).resolve().parents[1]
             / "data" / "results" / "events_selected.json")
        if not p.exists():
            pytest.skip("no results dir in this checkout")
        events = [Event.from_dict(d) for d in json.loads(p.read_text())]
        out = validate_events(events)
        assert out == {}, f"publish gate would reject live events: {out}"
