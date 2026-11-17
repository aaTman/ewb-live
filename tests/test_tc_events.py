"""Tropical-cyclone lane (tc, 2026-11-16): qualification, geometry, curation.

Synthetic IBTrACS-shaped fix tables only (no network): the qualification rule
(>= 3 fixes at >= 34 kt spanning >= 12 h, ET/MX fixes excluded), the padded
bbox with the antimeridian wrap convention, the shared `details["track"]`
schema, the publish validator and the curation exemptions.
"""

import datetime as dt
import json
import math

import numpy as np
import pandas as pd
import pytest

from ewb_live.events import select as sel
from ewb_live.events import tc
from ewb_live.events.tc import scan_tc_events, storm_to_event
from ewb_live.events.types import Event
from ewb_live.events.validate import validate_event

SID = "2026263N12345"


def fixes(n=12, sid=SID, start="2026-09-20 00:00", step_h=3, lat0=20.0,
          lon0=-70.0, dlat=0.3, dlon=-0.4, winds=None, nature=None,
          pres0=1002.0, name="HAINA", basin="NA", wind_source="USA",
          season=2026) -> pd.DataFrame:
    times = pd.date_range(start, periods=n, freq=f"{step_h}h")
    if winds is None:                       # strengthen, peak, then weaken
        winds = [25, 30, 35, 40, 55, 70, 85, 95, 80, 60, 45, 30][:n]
    if len(winds) < n:
        winds = list(winds) + [winds[-1]] * (n - len(winds))
    from ewb_live.events.types import normalize_lon_180

    return pd.DataFrame({
        "sid": sid, "season": season, "number": range(1, n + 1),
        "basin": basin, "name": name, "time": times,
        "lat": [lat0 + dlat * i for i in range(n)],
        # IBTrACS longitudes are in [-180, 180]; keep the fixture honest
        "lon": [normalize_lon_180(lon0 + dlon * i) for i in range(n)],
        "wind_kt": [float(w) for w in winds[:n]],
        "pressure_hpa": [pres0 - 2.0 * i for i in range(n)],
        "nature": nature or ["TS"] * n,
        "wind_source": wind_source, "pressure_source": wind_source,
        "status": "", "track_type": "MAIN",
    })


# ------------------------------------------------------------ qualification --
def test_qualifies_tropical_storm_life():
    ev = storm_to_event(SID, fixes())
    assert ev is not None and ev.event_type == "tropical_cyclone"
    # first fix at/over 34 kt is index 2 (06:00), last is index 10 (06:00 on d21)
    assert ev.start == dt.datetime(2026, 9, 20, 6, 0)
    assert ev.end == dt.datetime(2026, 9, 21, 9, 0)      # last qual fix + 3 h
    assert ev.peak_value == 95.0                          # max wind in the window
    assert ev.details["peak_category"] == "CAT 2"        # 95 kt = 83-95 kt band
    assert ev.details["ts_fixes"] == 9
    assert ev.details["min_pressure_hpa"] == 982.0        # window, not the full life
    assert ev.details["intensity_units"] == "kt"
    assert ev.details["ongoing"] is False                 # it weakened afterwards


def test_below_the_wind_floor_is_not_an_event():
    assert storm_to_event(SID, fixes(winds=[20, 25, 30, 34, 33, 30])) is None


def test_needs_three_qualifying_fixes():
    # exactly 2 fixes >= 34 kt, spanning long enough: still not an event
    assert storm_to_event(SID, fixes(winds=[25, 35, 40, 30, 25, 20])) is None


def test_needs_twelve_hour_span():
    # 3 fixes at 34+ kt inside a 6-hour span
    assert storm_to_event(SID, fixes(winds=[35, 40, 45, 30, 25, 20])) is None


def test_extratropical_fixes_do_not_qualify():
    nat = ["TS", "TS", "ET", "ET", "ET", "MX", "MX", "MX", "MX", "TS", "TS", "TS"]
    assert storm_to_event(SID, fixes(nature=nat)) is None


def test_nature_nr_still_qualifies_on_wind():
    """v04r01 leaves NATURE=NR on whole basins; the wind rule carries it."""
    ev = storm_to_event(SID, fixes(nature=["NR"] * 12))
    assert ev is not None


def test_title_and_validator_agree():
    ev = storm_to_event(SID, fixes())
    assert ev.title == ("Tropical Cyclone 2026-09-20 to 2026-09-21")
    assert validate_event(ev) == []


def test_json_round_trip_is_nan_free():
    ev = storm_to_event(SID, fixes(winds=[25, 35, 40, 55, 70, 85, 95, 80, 60,
                                          45, 30, 25],
                                   pres0=float("nan")))
    s = json.dumps(ev.to_dict(), allow_nan=False)
    back = Event.from_dict(json.loads(s))
    assert back.start == ev.start and back.bbox == ev.bbox
    assert all(r[4] is None for r in back.details["track"])


# ---------------------------------------------------------------- geometry --
def test_bbox_is_padded_qualifying_extent():
    ev = storm_to_event(SID, fixes())
    lat0, lat1, lon0, lon1 = ev.bbox
    q = fixes()
    qlat = q["lat"][2:11]       # qualifying fixes 2..10
    qlon = q["lon"][2:11]
    assert lat0 == pytest.approx(max(-90.0, qlat.min() - 5.0), abs=1e-6)
    assert lat1 == pytest.approx(min(90.0, qlat.max() + 5.0), abs=1e-6)
    assert lon0 == pytest.approx(qlon.min() - 5.0, abs=1e-6)
    assert lon1 == pytest.approx(qlon.max() + 5.0, abs=1e-6)
    assert ev.details["antimeridian_wrap"] is False


def test_antimeridian_crossing_wraps_lon_min_gt_lon_max():
    # a track that runs from 178E across 180 to 182.2E (= -177.8)
    f = fixes(n=8, lon0=178.0, dlon=0.6,
              winds=[35, 40, 45, 50, 55, 60, 55, 45])
    ev = storm_to_event(SID, f)
    lat0, lat1, lon0, lon1 = ev.bbox
    assert lon0 > lon1, "crossing bboxes must be lon_min > lon_max"
    assert lon0 == pytest.approx(173.0)              # 178 - 5
    assert lon1 == pytest.approx(-172.8)             # 182.2 + 5 -> -177.8 + 5
    assert ev.details["antimeridian_wrap"] is True
    arc = (lon1 - lon0) % 360.0
    assert arc == pytest.approx(4.2 + 10.0)          # 4.2 deg of track + 2 x 5 pad
    assert validate_event(ev) == []


def test_non_crossing_track_near_the_dateline_stays_ordered():
    ev = storm_to_event(SID, fixes(n=8, lon0=160.0, dlon=0.5))
    assert ev is not None
    assert ev.bbox[2] < ev.bbox[3]
    assert ev.details["antimeridian_wrap"] is False


def test_unwrap_lons_handles_both_directions():
    u = tc.unwrap_lons(np.array([179.0, -179.0, -178.0, 178.0]))
    # eastward across 180 (179 -> 181 -> 182), then the track turns back west
    assert [float(x) for x in u] == [179.0, 181.0, 182.0, 178.0]


# ------------------------------------------------------------------ track --
def test_track_rows_follow_the_shared_schema():
    ev = storm_to_event(SID, fixes())
    tr = ev.details["track"]
    assert len(tr) == 12                              # the FULL storm life
    assert [r[0] for r in tr] == sorted(r[0] for r in tr)
    for r in tr:
        assert len(r) == 5
        assert r[0].endswith("Z") and len(r[0]) == 20
        assert r[0][:4] == "2026"
    assert tr[0] == ["2026-09-20T00:00:00Z", 20.0, -70.0, 25.0, 1002.0]
    assert ev.details["track_crosses_antimeridian"] is False
    assert ev.details["intensity_units"] == "kt"


def test_track_flags_antimeridian_crossing():
    ev = storm_to_event(SID, fixes(n=8, lon0=179.0, dlon=0.5))
    assert ev.details["track_crosses_antimeridian"] is True


def test_track_pressure_null_when_missing():
    f = fixes()
    f.loc[0, "pressure_hpa"] = np.nan
    ev = storm_to_event(SID, f)
    assert ev.details["track"][0][4] is None


# --------------------------------------------------------------- the scan --
def test_scan_skips_storms_outside_the_window_and_keeps_straddlers():
    a = fixes(sid="2026263N11111")                          # fully inside
    b = fixes(sid="2026263N22222", start="2026-07-01 00:00")  # long before
    c = fixes(sid="2026263N33333", start="2026-09-19 06:00", n=16,
              winds=[25, 30, 35, 40, 55, 70, 85, 95, 80, 60, 45, 40, 40, 40,
                     40, 40])                                # straddles the edge
    out = scan_tc_events(dt.date(2026, 9, 20), dt.date(2026, 9, 30),
                         tracks=pd.concat([a, b, c], ignore_index=True))
    assert len(out) == 2
    assert sorted(e.start for e in out) == [dt.datetime(2026, 9, 20, 0, 0),
                                            dt.datetime(2026, 9, 20, 6, 0)]
    # the straddler is clipped at the window edge, not cut short before it
    assert min(e.start for e in out) >= dt.datetime(2026, 9, 20)


def test_scan_is_empty_when_the_source_is_unavailable(monkeypatch):
    from ewb_live.sources import ibtracs

    monkeypatch.setattr(ibtracs, "load_tracks", lambda *a, **k: None)
    assert scan_tc_events(dt.date(2026, 9, 20), dt.date(2026, 9, 30)) == []


def test_scan_survives_a_torn_storm(monkeypatch):
    f = fixes()
    orig = tc.storm_to_event

    def boom(sid, grp, window=None):
        if str(sid).endswith("22222"):
            raise RuntimeError("torn storm")
        return orig(sid, grp, window)

    monkeypatch.setattr(tc, "storm_to_event", boom)
    other = fixes(sid="2026263N22222")
    out = scan_tc_events(dt.date(2026, 9, 20), dt.date(2026, 9, 30),
                         tracks=pd.concat([f, other], ignore_index=True))
    assert len(out) == 1


# --------------------------------------------------------------- curation --
def _heat(bbox, start, end):
    return Event(id="heat1", event_type="heat_wave",
                 title=f"Heat Wave {start:%Y-%m-%d} to {end - dt.timedelta(days=1):%Y-%m-%d}",
                 start=start, end=end, bbox=bbox, peak_value=46.0,
                 details={"max_consecutive_days": 6, "peak_area_km2": 4e5,
                          "min_temp_c": 20.0, "mean_temp_c": 30.0})


def test_tc_never_merges_and_never_conflicts_with_area_events():
    tc_ev = storm_to_event(SID, fixes())
    # a heat wave whose box swallows the storm's box over the same days
    hw = _heat((10.0, 40.0, -90.0, -50.0),
               tc_ev.start - dt.timedelta(days=2), tc_ev.end + dt.timedelta(days=2))
    out = sel.rank_and_cap([tc_ev, hw], top_n={"tropical_cyclone": 5,
                                               "heat_wave": 5})
    types = sorted(e.event_type for e in out)
    assert types == ["heat_wave", "tropical_cyclone"], \
        [f"{e.event_type}:{e.id}" for e in out]


def test_two_storms_in_one_basin_both_survive():
    a = storm_to_event("2026263N11111", fixes(sid="2026263N11111"))
    b = storm_to_event("2026263N22222",
                       fixes(sid="2026263N22222", lat0=20.2, lon0=-70.2))
    out = sel.rank_and_cap([a, b], top_n=5)
    assert len(out) == 2


def test_severity_prefers_the_stronger_longer_storm():
    weak = storm_to_event("2026263N11111",
                          fixes(sid="2026263N11111",
                                winds=[25, 34, 36, 38, 35, 34, 30, 25, 20, 20,
                                       20, 20]))
    strong = storm_to_event("2026263N22222",
                            fixes(sid="2026263N22222",
                                  winds=[35, 60, 80, 110, 140, 120, 90, 60,
                                         45, 35, 34, 30]))
    assert sel.severity(strong) > sel.severity(weak)


def test_quota_allows_a_whole_season():
    evs = [storm_to_event(f"2026263N{i:05d}",
                          fixes(sid=f"2026263N{i:05d}", lat0=10.0 + 0.5 * i,
                                lon0=-40.0)) for i in range(20)]
    out = sel.rank_and_cap([e for e in evs if e], top_n=15)
    assert len(out) == 20        # TOP_N_BY_TYPE lifts the cap above the global 15


# ------------------------------------------------------------ validator net --
def test_validator_rejects_a_broken_tc_event():
    ev = storm_to_event(SID, fixes())
    bad = Event(id=ev.id, event_type=ev.event_type, title="Tropical Cyclone "
                f"{ev.start:%Y-%m-%d} to {ev.end - dt.timedelta(days=1):%Y-%m-%d}",
                start=ev.start, end=ev.end, bbox=ev.bbox, peak_value=95.0,
                details={**ev.details, "peak_wind_kt": 40.0,
                         "min_pressure_hpa": 1200.0, "ts_fixes": 1,
                         "peak_category": "CAT 5", "track": []})
    problems = validate_event(bad)
    assert any("peak_wind_kt" in p for p in problems)
    assert any("min_pressure_hpa" in p for p in problems)
    assert any("ts_fixes" in p for p in problems)
    assert any("track empty" in p for p in problems)


def test_category_bands():
    assert [tc.category(w) for w in (0, 33, 34, 63, 64, 82, 83, 96, 113, 137)] == \
        ["TD", "TD", "TS", "TS", "CAT 1", "CAT 1", "CAT 2", "CAT 3", "CAT 4",
         "CAT 5"]
    assert tc.category(None) == "TD"
    assert math.isfinite(1.0)


def test_recurling_storm_keeps_its_modal_basin():
    """IBTrACS labels each fix by the basin it sits in, so the last fix of a
    recurving Atlantic hurricane can be blank; the event takes the modal basin
    of its qualifying fixes instead."""
    f = fixes(n=8, lon0=-45.0, dlon=6.0)
    f.loc[f.index[-2:], "basin"] = ""
    ev = storm_to_event(SID, f)
    assert ev.details["basin"] == "NA"
    assert ev.details["basin_name"] == "North Atlantic"
