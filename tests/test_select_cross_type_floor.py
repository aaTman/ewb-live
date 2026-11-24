"""Offline tests for the cross-type overlap floor (owner decision 2026-11-23).

Same-type eviction is untouched (zero time+space overlap). A cross-type pair
only conflicts when the overlap is material, i.e. bbox containment
(intersection area / smaller box area) >= CROSS_TYPE_MIN_CONTAINMENT.

Severity is monkeypatched to details["sev"] so the tests stay offline (real
severity reads the climatology store).
"""

import datetime as dt

import pytest

from ewb_live.events import select
from ewb_live.events.types import Event


@pytest.fixture(autouse=True)
def _offline_severity(monkeypatch):
    monkeypatch.setattr(select, "_severity", lambda e: float(e.details.get("sev", 0.0)))
    monkeypatch.setattr(select, "_severity_cache", {})


D0 = dt.datetime(2026, 9, 2)
CONT = (30.0, 40.0, -100.0, -90.0)


def ev(id_, etype, bbox, sev, start=D0, days=5, **det):
    return Event(id=id_, event_type=etype, title=f"{etype} {id_}",
                 start=start, end=start + dt.timedelta(days=days),
                 bbox=bbox, peak_value=30.0,
                 details={"sev": sev, **det})


FLOOD_DET = {"peak_3day_mm": 80.0, "peak_area_km2": 2.0e5}  # floor envelope


def resolve(by_type, decisions, floor=None, top_n=15):
    """_resolve_conflicts with a per-type quota, floor optionally overridden."""
    old = select.CROSS_TYPE_MIN_CONTAINMENT
    if floor is not None:
        select.CROSS_TYPE_MIN_CONTAINMENT = floor
    try:
        n_map = {t: top_n for t in by_type}
        return select._resolve_conflicts(by_type, n_map, decisions)
    finally:
        select.CROSS_TYPE_MIN_CONTAINMENT = old


# the measured production pair that motivated the rule: the 17-day US heat
# wave 4da488600cae vs the 4-day IMERG flood 2fa6f9823a3c
HEAT_BOX = (18.25, 41.5, -108.0, -82.25)
FLOOD_BOX = (39.0, 45.5, -99.0, -75.0)
# intersection 2.5 x 16.75 = 41.875 deg2; the flood box is 6.5 x 24 = 156 deg2
GRAZE_CONTAINMENT = 41.875 / 156.0


# ------------------------------------------------------- _bbox_containment

def test_containment_normal_pair_matches_the_measured_case():
    c = select._bbox_containment(HEAT_BOX, FLOOD_BOX)
    assert c == pytest.approx(GRAZE_CONTAINMENT)  # 0.268: the graze, not IoU 0.059
    assert select._bbox_iou(HEAT_BOX, FLOOD_BOX) < 0.06


def test_containment_is_symmetric_in_argument_order():
    assert select._bbox_containment(HEAT_BOX, FLOOD_BOX) == pytest.approx(
        select._bbox_containment(FLOOD_BOX, HEAT_BOX))


def test_containment_nested_is_one():
    outer = (0.0, 20.0, 0.0, 40.0)
    inner = (5.0, 10.0, 10.0, 20.0)
    assert select._bbox_containment(outer, inner) == pytest.approx(1.0)
    assert select._bbox_containment(inner, outer) == pytest.approx(1.0)


def test_containment_disjoint_is_zero():
    a = (0.0, 5.0, 0.0, 10.0)
    assert select._bbox_containment(a, (20.0, 25.0, 30.0, 40.0)) == 0.0   # lat+lon apart
    assert select._bbox_containment(a, (0.0, 5.0, 30.0, 40.0)) == 0.0     # lon apart
    # boxes that only touch along an edge share no area
    assert select._bbox_containment(a, (5.0, 9.0, 0.0, 10.0)) == 0.0
    assert select._bbox_containment(a, (0.0, 5.0, 10.0, 19.0)) == 0.0


def test_containment_half_overlap_is_half_of_the_smaller_box():
    a = (0.0, 10.0, 0.0, 10.0)   # 100 deg2
    b = (0.0, 10.0, 5.0, 15.0)   # 100 deg2, shares a 50 deg2 band
    assert select._bbox_containment(a, b) == pytest.approx(0.5)


def test_containment_wrapped_longitudes_use_the_eastward_arc():
    # a: 170E -> 170W (20 deg wide), b: 175E -> 175W (10 deg wide) inside it
    a = (0.0, 10.0, 170.0, -170.0)
    b = (2.0, 8.0, 175.0, -175.0)
    assert select._bbox_containment(a, b) == pytest.approx(1.0)
    # k: 170E -> 179W (11 deg wide) covers 3 of b2's 4 deg (178E..179W)
    b2 = (2.0, 8.0, 178.0, -178.0)     # 4 deg wide, 24 deg2
    k = (0.0, 10.0, 170.0, -179.0)     # 11 deg wide, 110 deg2
    assert select._bbox_containment(k, b2) == pytest.approx(6.0 * 3.0 / 24.0)  # 0.75
    # the same two boxes written unwrapped (lon > 180) score identically
    assert select._bbox_containment((0.0, 10.0, 170.0, 181.0),
                                    (2.0, 8.0, 178.0, 182.0)) == pytest.approx(0.75)
    # two non-wrapped boxes either side of the line share nothing
    assert select._bbox_containment((0.0, 10.0, 170.0, 175.0),
                                    (0.0, 10.0, -175.0, -170.0)) == 0.0


def test_containment_degrees_not_km_is_accepted_skew():
    """deg^2 area weights a high-latitude box less than the km^2 it covers.

    The floor is a rule threshold, not an exposure measure: two boxes with the
    same km^2 at different latitudes score differently (documented in the
    docstring). Pinned here so a future km^2 rewrite is a deliberate change.
    """
    tropic = (0.0, 6.0, 0.0, 6.0)          # 36 deg2 ~ 400,000 km2
    polar = (70.0, 76.0, 0.0, 6.0)         # 36 deg2 ~ 140,000 km2
    assert select._bbox_containment(tropic, (0.0, 6.0, 3.0, 9.0)) == pytest.approx(0.5)
    assert select._bbox_containment(polar, (70.0, 76.0, 3.0, 9.0)) == pytest.approx(0.5)


# ------------------------------------------------- rule: conflict vs graze

def test_grazing_cross_type_pair_both_survive():
    heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)
    flood = ev("flood", "flood", FLOOD_BOX, 10.0, days=4)
    decisions: list[dict] = []
    out = resolve({"heat_wave": [heat], "flood": [flood]}, decisions)
    assert sorted(e.id for e in out) == ["flood", "heat"]
    assert [d for d in decisions if d["action"] == "drop"] == []


def test_material_cross_type_overlap_evicts_the_lower_percentile():
    # flood rank 0 (percentile 1.0) vs heat rank 1 (percentile 0.5): the heat
    # fragment inside the flood box is the weaker claim and goes
    flood = ev("flood", "flood", (0.0, 20.0, 0.0, 40.0), 10.0)
    strong = ev("heat-a", "heat_wave", (60.0, 66.0, 100.0, 110.0), 9.0)
    weak = ev("heat-b", "heat_wave", (14.0, 18.0, 10.0, 20.0), 8.0)
    decisions: list[dict] = []
    out = resolve({"flood": [flood], "heat_wave": [strong, weak]}, decisions)
    assert sorted(e.id for e in out) == ["flood", "heat-a"]
    drops = [d for d in decisions if d["action"] == "drop"]
    assert len(drops) == 1
    d = drops[0]
    assert d["reason"] == "cross-type"
    assert d["id"] == "heat-b" and d["kept"] == "flood"
    assert d["kept_type"] == "flood" and d["kept_rank"] == 0
    assert d["rank"] == 1
    assert d["containment"] == pytest.approx(1.0, abs=0.001)
    assert d["containment_floor"] == select.CROSS_TYPE_MIN_CONTAINMENT


def test_same_type_overlap_evicts_regardless_of_containment():
    """The 2026-11-06 zero-overlap rule for same-type pairs is unchanged:
    containment is never consulted, so even a 0.02 graze drops the loser."""
    a = ev("heat-a", "heat_wave", (0.0, 20.0, 0.0, 40.0), 9.0)
    graze = ev("heat-b", "heat_wave", (19.0, 31.0, 39.0, 51.0), 8.0)  # 1 deg2 of 144
    assert select._bbox_containment(a.bbox, graze.bbox) < 0.01
    for floor in (0.0, select.CROSS_TYPE_MIN_CONTAINMENT, 0.99):
        decisions: list[dict] = []
        out = resolve({"heat_wave": [a, graze]}, decisions, floor=floor)
        assert [e.id for e in out] == ["heat-a"], floor
        drop = next(d for d in decisions if d["action"] == "drop")
        assert drop["reason"] == "same-type-overlap"
        assert "containment" not in drop  # the floor did not apply


def test_track_types_stay_exempt_even_at_a_full_containment():
    tc = ev("tc", "tropical_cyclone", HEAT_BOX, 5.0)     # containment 1.0
    mlc = ev("mlc", "midlat_cyclone", (20.0, 24.0, -100.0, -90.0), 5.0)
    heat = ev("heat", "heat_wave", HEAT_BOX, 1.0)
    for floor in (0.0, select.CROSS_TYPE_MIN_CONTAINMENT):
        decisions: list[dict] = []
        out = resolve({"tropical_cyclone": [tc], "midlat_cyclone": [mlc],
                       "heat_wave": [heat]}, decisions, floor=floor)
        assert sorted(e.id for e in out) == ["heat", "mlc", "tc"], floor
        assert [d for d in decisions if d["action"] == "drop"] == []


def test_env_var_sets_the_shipped_default(monkeypatch):
    """EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT is read at import, like the other
    curation knobs, and the shipped default is the sweep-chosen one."""
    import importlib
    import os

    monkeypatch.delenv("EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT", raising=False)
    assert importlib.reload(select).CROSS_TYPE_MIN_CONTAINMENT == 0.5
    monkeypatch.setenv("EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT", "0.8")
    try:
        assert importlib.reload(select).CROSS_TYPE_MIN_CONTAINMENT == pytest.approx(0.8)
        decisions: list[dict] = []
        heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)
        flood = ev("flood", "flood", FLOOD_BOX, 10.0, days=4)
        # at floor 0.0 the same pair would evict the heat wave (old behaviour)
        out = resolve({"heat_wave": [heat], "flood": [flood]}, decisions, floor=0.0)
        assert [e.id for e in out] == ["flood"]
    finally:
        os.environ.pop("EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT", None)
        importlib.reload(select)


def test_floor_below_the_graze_reproduces_the_old_eviction():
    heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)
    flood = ev("flood", "flood", FLOOD_BOX, 10.0, days=4)
    for floor in (0.0, 0.2, 0.26):
        decisions: list[dict] = []
        out = resolve({"heat_wave": [heat], "flood": [flood]}, decisions, floor=floor)
        assert [e.id for e in out] == ["flood"], floor
        drop = next(d for d in decisions if d["action"] == "drop")
        assert drop["reason"] == "cross-type" and drop["kept"] == "flood"


def test_a_grazing_neighbour_does_not_hide_a_real_conflict_behind_it():
    """The sub-floor graze must not short-circuit the scan: heat-b is checked
    against the flood (graze) AND the freeze (material) and drops for the
    freeze."""
    flood = ev("flood", "flood", FLOOD_BOX, 12.0, days=4)      # accepted first
    freeze = ev("freeze", "freeze", (18.25, 41.5, -108.0, -82.25), 11.0)
    heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)     # graze+full overlap
    decisions: list[dict] = []
    out = resolve({"flood": [flood], "freeze": [freeze], "heat_wave": [heat]},
                  decisions)
    assert sorted(e.id for e in out) == ["flood", "freeze"]
    drop = next(d for d in decisions if d["action"] == "drop")
    assert drop["id"] == "heat" and drop["kept"] == "freeze"
    assert drop["reason"] == "cross-type"


def test_sub_floor_graze_is_logged_as_a_pass(caplog):
    import logging

    heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)
    flood = ev("flood", "flood", FLOOD_BOX, 10.0, days=4)
    with caplog.at_level(logging.INFO, logger="ewb_live.events.select"):
        resolve({"flood": [flood], "heat_wave": [heat]}, [])
    assert any("containment" in r.getMessage() and "flood" in r.getMessage()
               for r in caplog.records)


# ------------------------------------------------- continuation gate (phase 3)

def test_continuation_hops_past_a_sub_floor_grazer():
    """Review finding 1 on 2fd2bd4 (major): the continuation gate must use the
    SAME conflict predicate as phase 2 — a kept cross-type grazer must not
    silently kill the hop. This is the production shape that motivated the
    floor: flood F grazes both the chain P and its continuation c."""
    p = ev("p", "heat_wave", HEAT_BOX, 10.0, days=14)            # Sep 2 -> 16
    f = ev("f", "flood", FLOOD_BOX, 12.0, start=D0 + dt.timedelta(8),
           days=15, **FLOOD_DET)                                              # Sep 10 -> 25
    c = ev("c", "heat_wave", HEAT_BOX, 2.0, start=D0 + dt.timedelta(14),
           days=6)                                               # Sep 16 -> 22
    decisions: list[dict] = []
    out = select.rank_and_cap([p, f, c], {"heat_wave": 1, "flood": 1}, decisions)
    assert sorted(e.id for e in out) == ["c", "f", "p"]
    assert next(e for e in out if e.id == "c").details["continues"] == "p"
    # the phase-3 hop leaves the same graze paper trail as phase 2 (round-2 nit)
    g = [d for d in decisions if d["action"] == "cross-type-graze" and d["id"] == "c"]
    assert len(g) == 1 and g[0]["kept"] == "f"


def test_continuation_blocked_by_material_conflict_is_logged():
    a = ev("a", "heat_wave", CONT, 10.0, days=14)
    c = ev("c", "heat_wave", CONT, 2.0, start=D0 + dt.timedelta(14), days=5)
    fz = ev("fz", "freeze", (35.0, 45.0, -105.0, -85.0), 9.0,
            start=D0 + dt.timedelta(14), days=5)   # containment 0.5 of CONT
    decisions: list[dict] = []
    out = select.rank_and_cap([a, c, fz], {"heat_wave": 1, "freeze": 1},
                              decisions)
    assert sorted(e.id for e in out) == ["a", "fz"]
    skip = [d for d in decisions if d["action"] == "continuation-skipped"]
    assert [d["id"] for d in skip] == ["c"] and skip[0]["kept"] == "fz"


def test_sub_floor_graze_publishes_an_audit_entry():
    heat = ev("heat", "heat_wave", HEAT_BOX, 7.0, days=17)
    flood = ev("flood", "flood", FLOOD_BOX, 10.0, days=4, **FLOOD_DET)
    decisions: list[dict] = []
    out = select.rank_and_cap([heat, flood], {"heat_wave": 1, "flood": 1},
                              decisions)
    assert sorted(e.id for e in out) == ["flood", "heat"]
    g = [d for d in decisions if d["action"] == "cross-type-graze"]
    assert len(g) == 1
    assert g[0]["containment"] == pytest.approx(GRAZE_CONTAINMENT, abs=1e-3)


def test_containment_equal_to_the_floor_evicts():
    """The rule-level inclusive boundary (>=): math tests already pin 0.5,
    no rule test pinned the eviction at exactly the floor."""
    small = (8.0, 12.0, 5.0, 15.0)      # 40 deg2, wholly inside BIG
    big = (0.0, 10.0, 0.0, 20.0)        # intersection 20 deg2 -> 20/40 = 0.5
    a = ev("a", "heat_wave", big, 10.0)
    b = ev("b", "freeze", small, 9.0)
    decisions: list[dict] = []
    out = select.rank_and_cap([a, b], {"heat_wave": 1, "freeze": 1}, decisions)
    assert [e.id for e in out] == ["a"]
    drop = next(d for d in decisions if d["action"] == "drop")
    assert drop["reason"] == "cross-type" and drop["containment"] == 0.5


def test_sub_floor_graze_does_not_hide_a_same_type_conflict():
    fz = ev("fz", "freeze", FLOOD_BOX, 12.0)       # accepted first (sev 12)
    a = ev("a", "heat_wave", HEAT_BOX, 10.0)       # rank 0, kept
    x = ev("x", "heat_wave", (20.0, 30.0, -100.0, -90.0), 3.0)  # grazes fz,
    # overlaps a in same type -> must drop for the SAME-TYPE conflict, not pass
    decisions: list[dict] = []
    out = select.rank_and_cap([fz, a, x], {"heat_wave": 2, "flood": 1}, decisions)
    assert sorted(e.id for e in out) == ["a", "fz"]
    drop = next(d for d in decisions if d["action"] == "drop" and d["id"] == "x")
    assert drop["reason"] == "same-type-overlap" and drop["kept"] == "a"


@pytest.mark.parametrize("bad", ["abc", "2", "-0.1", "nan"])
def test_floor_env_is_validated_at_import(monkeypatch, bad):
    """A floor outside [0, 1] silently disables cross-type eviction (>1, NaN)
    or reverts the rule (<0); a typo must fail loudly at import instead."""
    import importlib

    monkeypatch.setenv("EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT", bad)
    try:
        with pytest.raises(ValueError):
            importlib.reload(select)
    finally:
        monkeypatch.delenv("EWB_LIVE_CROSSTYPE_MIN_CONTAINMENT")
        importlib.reload(select)
    assert select.CROSS_TYPE_MIN_CONTAINMENT == 0.5


def test_the_worst_graze_of_a_candidate_is_the_one_recorded():
    """Round-2 review: with several sub-floor grazers the recorded entry must
    be the near-floor one an auditor wants (max containment), not the first."""
    f1 = ev("f1", "flood", (43.5, 52.0, -97.0, -95.0), 12.0,
            start=D0 + dt.timedelta(8), days=4, **FLOOD_DET)   # cont ~0.03
    p = ev("p", "heat_wave", HEAT_BOX, 11.0, days=17)          # cont ~0.17
    cs = ev("cs", "cold_snap", (42.0, 44.5, -100.0, -90.0), 10.0,
            start=D0 + dt.timedelta(3), days=8)                # cont ~0.48
    x = ev("x", "freeze", (41.0, 44.0, -96.0, -86.0), 2.0,
           start=D0 + dt.timedelta(1), days=9)
    decisions: list[dict] = []
    out = select.rank_and_cap([f1, p, cs, x], 1, decisions)
    assert sorted(e.id for e in out) == ["cs", "f1", "p", "x"]
    g = [d for d in decisions if d["action"] == "cross-type-graze" and d["id"] == "x"]
    assert len(g) == 1 and g[0]["kept"] == "cs"
