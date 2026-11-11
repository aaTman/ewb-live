"""Offline tests for the owner curation rules (2026-11-06):
- merge only same-event fragments (gap <= 1 d AND bbox IoU >= 0.3), with
  per-type merged-duration caps from upstream events.yaml (AR 9, heat 16,
  freeze 16); other overlapping pairs DROP the lower within-type severity
  percentile (never merge);
- the final set has zero time+space overlap pairs;
- AR ocean-origin/landfall rule.

Severity is monkeypatched to details["sev"] so tests stay offline (real
severity reads the climatology store)."""

import datetime as dt
import itertools

import numpy as np
import pytest

from ewb_live.events import select
from ewb_live.events.types import Event
from ewb_live.events.validate import validate_event


@pytest.fixture(autouse=True)
def _offline_severity(monkeypatch):
    monkeypatch.setattr(select, "_severity", lambda e: float(e.details.get("sev", 0.0)))
    monkeypatch.setattr(select, "_severity_cache", {})


def mk(etype, start, days, bbox, peak, sev=1.0, id=None, **det):
    s = dt.datetime.combine(start, dt.time())
    details = {"sev": sev, "max_consecutive_days": min(days, 4),
               "peak_area_km2": 1e5, **det}
    if etype == "freeze":
        details.setdefault("min_temp_c", peak)
        details.setdefault("mean_temp_c", peak + 5.0)
    if etype == "heat_wave":
        details.setdefault("min_temp_c", peak - 10.0)
        details.setdefault("mean_temp_c", peak - 3.0)
    if etype == "atmospheric_river":
        details.setdefault("landfall_days", 3)
        details.setdefault("peak_land_pixels", 500)
        details.setdefault("min_mask_gridpoints", 100)
        details.setdefault("ivt_threshold_kg_m_s", 250.0)
        details.setdefault("centroid_lat", (bbox[0] + bbox[1]) / 2)
    e = Event(id=id or f"{etype}{start}{bbox}{sev}", event_type=etype,
              title="", start=s, end=s + dt.timedelta(days=days),
              bbox=bbox, peak_value=peak, details=details)
    label = {"heat_wave": "Heat Wave", "freeze": "Freeze",
             "atmospheric_river": "Atmospheric River"}[etype]
    e.title = f"{label} {e.start:%Y-%m-%d} to {e.end - dt.timedelta(days=1):%Y-%m-%d}"
    return e


def assert_no_overlaps(events):
    for a, b in itertools.combinations(events, 2):
        time_ov = min(a.end, b.end) - max(a.start, b.start)
        assert not (time_ov > dt.timedelta(0) and select._bbox_intersects(a.bbox, b.bbox)), (
            f"overlap pair: {a.title} | {b.title}")


# ------------------------------------------------------------ rule 1: merge

def test_same_event_fragments_merge_iou_gated():
    d0 = dt.date(2026, 8, 14)
    # boxes shift by 1 deg/step: consecutive IoU = 36/64 = 0.56 >= 0.3
    a = mk("heat_wave", d0, 4, (55.0, 60.0, 45.0, 55.0), 38.0, sev=5)
    b = mk("heat_wave", d0 + dt.timedelta(days=3), 4,
           (56.0, 61.0, 46.0, 56.0), 36.0, sev=3)
    c = mk("heat_wave", d0 + dt.timedelta(days=6), 4,
           (57.0, 62.0, 47.0, 57.0), 37.0, sev=2)
    out = select.rank_and_cap([a, b, c], top_n=15)
    assert len(out) == 1
    m = out[0]
    assert m.details["merged_count"] == 3
    assert m.start == a.start and m.end == c.end
    assert m.peak_value == 38.0
    assert m.bbox == (55.0, 62.0, 45.0, 57.0)  # union within caps
    assert m.title == "Heat Wave 2026-08-14 to 2026-08-23"
    assert validate_event(m) == []


def test_chain_stops_at_iou_gate_and_overlap_drops():
    d0 = dt.date(2026, 8, 14)
    a = mk("heat_wave", d0, 4, (55.0, 60.0, 45.0, 55.0), 38.0, sev=5)
    # touches A in time but IoU vs A ~ 0.11 (small corner): NOT the same
    # event -> no merge; it overlaps A in time+space -> dropped as the
    # lower-severity one.
    b = mk("heat_wave", d0 + dt.timedelta(days=3), 4,
           (58.0, 63.0, 50.0, 60.0), 36.0, sev=3)
    out = select.rank_and_cap([a, b], top_n=15)
    assert [e.id for e in out] == [a.id]
    drop = [d for d in select.LAST_DECISIONS if d["action"] == "drop"]
    assert drop and drop[0]["reason"] == "same-type-overlap"


def test_no_merge_when_far_apart_in_time_or_space():
    d0 = dt.date(2026, 8, 1)
    a = mk("heat_wave", d0, 4, (10.0, 15.0, 10.0, 20.0), 35.0)      # 1-5
    b = mk("heat_wave", d0 + dt.timedelta(days=7), 4, (12.0, 17.0, 15.0, 25.0), 34.0)  # starts 8, gap 3 d
    c = mk("heat_wave", d0, 4, (-40.0, -35.0, 100.0, 110.0), 33.0)  # overlaps in time, disjoint bbox
    out = select.rank_and_cap([a, b, c], top_n=15)
    assert len(out) == 3  # b is too far in time; c is spatially disjoint
    assert_no_overlaps(out)


def test_touching_gap_one_day_merges():
    d0 = dt.date(2026, 8, 1)
    a = mk("heat_wave", d0, 4, (10.0, 15.0, 10.0, 20.0), 35.0)      # ends day 5
    # starts day 5 (gap 0), box shifted 1 deg: IoU 36/64 = 0.56 >= 0.3
    b = mk("heat_wave", d0 + dt.timedelta(days=4), 4,
           (11.0, 16.0, 11.0, 21.0), 34.0)
    out = select.rank_and_cap([a, b], top_n=15)
    assert len(out) == 1 and out[0].details["merged_count"] == 2


def test_touching_far_apart_boxes_only_drop_on_overlap():
    d0 = dt.date(2026, 8, 1)
    a = mk("heat_wave", d0, 4, (10.0, 15.0, 10.0, 20.0), 35.0)
    # touches in time but IoU 15/85 = 0.18 < 0.3: no merge; time overlap
    # with A? b starts exactly when A ends -> no time overlap -> both kept
    b = mk("heat_wave", d0 + dt.timedelta(days=4), 4,
           (12.0, 17.0, 15.0, 25.0), 34.0)
    out = select.rank_and_cap([a, b], top_n=15)
    assert len(out) == 2
    assert_no_overlaps(out)


def test_merged_duration_capped_at_upstream_p90():
    d0 = dt.date(2026, 8, 1)
    box = (30.0, 40.0, 10.0, 20.0)
    evs = [mk("heat_wave", d0 + dt.timedelta(days=4 * i), 5, box,
              38.0 - i, sev=10.0 - i) for i in range(4)]   # 0-5,4-9,8-13,12-17
    out = select.rank_and_cap(evs, top_n=15)
    m = [e for e in out if e.event_type == "heat_wave"]
    assert len(m) == 1
    dur = (m[0].end - m[0].start).days
    assert dur <= select.MERGE_CAP_DAYS["heat_wave"]  # 0-13 merged; 4th over cap
    assert m[0].details["merged_count"] == 3
    assert_no_overlaps(out)


def test_ar_merge_cap_is_9_days():
    d0 = dt.date(2026, 8, 1)
    box = (30.0, 40.0, 10.0, 30.0)
    a = mk("atmospheric_river", d0, 5, box, 700.0, sev=9)          # 0-5
    b = mk("atmospheric_river", d0 + dt.timedelta(days=4), 5, box,
           650.0, sev=8)                                           # 4-9: union 9
    c = mk("atmospheric_river", d0 + dt.timedelta(days=8), 5, box,
           620.0, sev=7)                                           # 8-13: union 13 > 9
    out = select.rank_and_cap([a, b, c], top_n=15)
    m = [e for e in out if e.event_type == "atmospheric_river"]
    assert len(m) == 1 and (m[0].end - m[0].start).days == 9
    assert m[0].details["merged_count"] == 2
    drop = [d for d in select.LAST_DECISIONS
            if d["action"] == "drop" and d["id"] == c.id]
    assert drop and drop[0]["reason"] == "same-type-overlap"


def test_merge_refused_when_union_bbox_too_huge():
    # absorbing C would push the lon span past the 60 deg cap: C is NOT
    # merged; it has no time overlap with the cluster -> kept separately.
    d0 = dt.date(2026, 8, 1)
    a = mk("heat_wave", d0, 4, (0.0, 5.0, 0.0, 30.0), 40.0, sev=9)
    b = mk("heat_wave", d0 + dt.timedelta(days=1), 4,
           (1.0, 6.0, 25.0, 55.0), 39.0, sev=5)      # IoU 2*5/(150+150-10)=0.034
    out = select.rank_and_cap([a, b], top_n=15)
    # low IoU -> no merge; the two DO overlap in time+space -> lower drops
    assert len(out) == 1 and out[0].id == a.id


def test_antimeridian_union_wraps_and_validates():
    d0 = dt.date(2026, 9, 1)
    # a wraps the antimeridian (170E..178W), b wraps too and overlaps a's
    # arc by 7 deg: IoU 21/69 >= 0.3 -> same event, merge across the line
    a = mk("heat_wave", d0, 4, (0.0, 5.0, 170.0, -178.0), 35.0, sev=9,
           antimeridian_wrap=True)
    b = mk("heat_wave", d0, 4, (2.0, 7.0, 175.0, -175.0), 34.0, sev=5,
           antimeridian_wrap=True)
    out = select.rank_and_cap([a, b], top_n=15)
    assert len(out) == 1
    m = out[0]
    assert m.bbox[2] > m.bbox[3]  # wrapped eastward arc
    assert m.details["antimeridian_wrap"] is True
    assert validate_event(m) == []


# ------------------------------------------------- AR ocean-origin rule ---

def _mask(shape, steps_days):
    """(day, lat, lon) bool mask; steps_days: list of (day, box) slices."""
    m = np.zeros(shape, dtype=bool)
    for day, (l0, l1, o0, o1) in steps_days:
        m[day, l0:l1, o0:o1] = True
    return m


def test_ocean_rule_rejects_all_land():
    from ewb_live.events.ar import ocean_land_rule

    ar = _mask((3, 10, 10), [(d, (2, 8, 2, 8)) for d in range(3)])
    land = ar.copy()  # the whole IVT mask sits on land: not an AR
    assert ocean_land_rule(ar, land) is None


def test_ocean_rule_accepts_ocean_to_land():
    from ewb_live.events.ar import ocean_land_rule

    ar = _mask((3, 10, 10), [(d, (2, 8, 2, 8)) for d in range(3)])
    land = np.zeros_like(ar)
    land[0, 2:8, 2:3] = True    # day 1: 6 of 36 px land -> ocean 30/36
    land[1, 2:8, 2:6] = True    # 12 land px >= 10 -> landfall
    land[2, 2:8, 2:6] = True
    r = ocean_land_rule(ar, land)
    assert r is not None
    assert r["ocean_frac_start"] == 0.833
    assert r["landfall_step"] == 1


def test_ocean_rule_rejects_land_start():
    from ewb_live.events.ar import ocean_land_rule

    ar = _mask((3, 10, 10), [(d, (2, 8, 2, 8)) for d in range(3)])
    land = np.zeros_like(ar)
    land[0, 2:8, 2:7] = True    # day 1: only 10/60 = 17% ocean
    land[1, 2:8, 2:4] = True    # later mostly ocean
    land[2, 2:8, 2:4] = True
    assert ocean_land_rule(ar, land) is None


# ------------------------------------------------------ rule 2: cross type

def test_cross_type_overlap_drops_lower_rank():
    d0 = dt.date(2026, 8, 1)
    box = (10.0, 20.0, 10.0, 20.0)
    h = mk("heat_wave", d0, 6, box, 40.0, sev=10)
    f = mk("freeze", d0, 6, (15.0, 25.0, 15.0, 25.0), -5.0, sev=9)
    out = select.rank_and_cap([h, f], top_n=15)
    assert [e.event_type for e in out] == ["heat_wave"]  # rank-0 heat wins
    assert select.LAST_DECISIONS[-1]["action"] == "drop"
    assert select.LAST_DECISIONS[-1]["reason"] == "cross-type"


# ----------------------------------------------------------- rule 3: refill

def test_cross_type_drop_frees_slot_for_refill():
    top_n = {"heat_wave": 1, "freeze": 1}
    d0 = dt.date(2026, 8, 1)
    box = (10.0, 20.0, 10.0, 20.0)
    h = mk("heat_wave", d0, 6, box, 40.0, sev=10)
    f1 = mk("freeze", d0, 5, (15.0, 25.0, 15.0, 25.0), -5.0, sev=9)   # rank-0 freeze: overlaps heat
    f2 = mk("freeze", dt.date(2026, 9, 1), 5, (40.0, 45.0, 0.0, 10.0), -8.0, sev=5)  # disjoint
    out = select.rank_and_cap([h, f1, f2], top_n=top_n)
    types = sorted(e.event_type for e in out)
    assert types == ["freeze", "heat_wave"]
    assert out[[e.event_type for e in out].index("freeze")].id == f2.id  # refill


# ------------------------------------------------ final-set invariants ----

def test_final_set_has_zero_pairwise_overlaps():
    d0 = dt.date(2026, 8, 1)
    events = [
        # a 4-member Siberia-style heat chain
        *[mk("heat_wave", d0 + dt.timedelta(days=4 * i), 5,
             (55.0 + 3 * i, 60.0 + 3 * i, 40.0 + 12 * i, 55.0 + 12 * i),
             38.0 - i, sev=10 - i) for i in range(4)],
        # an independent heat wave far away
        mk("heat_wave", d0, 5, (-30.0, -25.0, 120.0, 130.0), 33.0, sev=6),
        # a freeze overlapping the heat chain spatially (lower rank -> dropped)
        mk("freeze", d0 + dt.timedelta(days=2), 5, (58.0, 62.0, 52.0, 60.0), -2.0, sev=1),
        # an AR crossing both
        mk("atmospheric_river", d0, 6, (40.0, 50.0, 100.0, 115.0), 700.0, sev=8),
    ]
    out = select.rank_and_cap(events, top_n=15)
    assert_no_overlaps(out)
    # every raw event is either a survivor or recorded in a merge/drop
    merged_ids = {mid for d in select.LAST_DECISIONS if d["action"] == "merge"
                  for mid in d["members"]}
    dropped_ids = {d["id"] for d in select.LAST_DECISIONS if d["action"] == "drop"}
    survivor_ids = {e.id for e in out} | merged_ids
    for e in events:
        assert e.id in survivor_ids or e.id in dropped_ids
    assert all(validate_event(e) == [] for e in out)


def test_quota_drop_logged():
    d0 = dt.date(2026, 8, 1)
    evs = [mk("atmospheric_river", d0 + dt.timedelta(days=10 * i), 4,
              (30.0 + 15 * i, 40.0 + 15 * i, 10.0 * i, 10.0 + 10.0 * i),
              700.0, sev=10.0 - i) for i in range(4)]
    out = select.rank_and_cap(evs, top_n=2)
    assert len(out) == 2
    assert sum(1 for d in select.LAST_DECISIONS
               if d["action"] == "drop" and d["reason"] == "quota") == 2


def test_adjudicated_exclusion_drops_event(tmp_path, monkeypatch):
    """G-40: data/curation_exclusions.json drops owner-adjudicated events
    (e.g. post-tropical cyclones misread as ARs) with a logged decision."""
    import json
    a = mk("atmospheric_river", dt.date(2026, 9, 25), 4, (30.0, 51.0, -85.0, -61.0), 500, sev=9)
    b = mk("atmospheric_river", dt.date(2026, 10, 3), 3, (50.5, 71.0, 0.0, 23.0), 400, sev=5)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "curation_exclusions.json").write_text(json.dumps(
        {a.id: {"reason": "post-tropical cyclone", "by": "owner", "date": "2026-11-08"}}))
    monkeypatch.chdir(tmp_path)
    decisions: list[dict] = []
    out = select._resolve_conflicts({"atmospheric_river": [a, b]},
                                    {"atmospheric_river": 15}, decisions)
    assert [e.id for e in out] == [b.id]
    drop = [d for d in decisions if d["action"] == "drop" and d["id"] == a.id]
    assert drop and "post-tropical cyclone" in drop[0]["reason"]
