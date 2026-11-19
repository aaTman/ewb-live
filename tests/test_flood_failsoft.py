"""A fresh tree without cached rain days must degrade the flood lane to an honest
lane_status, not abort the whole detect (it used to take TC/MLC down with it —
notes/channel_new_events.md [ne-fix-tc] 2026-11-19 findings (a))."""
from __future__ import annotations

import datetime as dt

from ewb_live import events as ev_pkg
from ewb_live.events import flood as F

S, E = dt.date(2026, 8, 19), dt.date(2026, 11, 17)


def _detect(tmp_path, monkeypatch, root):
    monkeypatch.setattr(ev_pkg, "truth_cutoff",
                        lambda: dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setenv("EWB_LIVE_PARDIG_DIR", str(root))
    monkeypatch.setenv("EWB_FLOOD_SOURCE", "pardig")
    ev_pkg.LANE_STATUS.clear()
    return ev_pkg.detect_events(S, E, types=("flood",), cache_dir=tmp_path / "c",
                                top_n=None)


def test_missing_pardig_store_degrades_to_no_data(tmp_path, monkeypatch):
    assert _detect(tmp_path, monkeypatch, tmp_path / "nopardig") == []
    st = ev_pkg.lane_status_report(tmp_path / "c")["flood"]
    assert st["status"] == "no-data" and st["source"] == "pardig"
    assert "rain store" in st["reason"]


def test_degraded_scan_is_not_cached_so_data_arriving_later_is_picked_up(tmp_path, monkeypatch):
    _detect(tmp_path, monkeypatch, tmp_path / "nopardig")
    assert not list((tmp_path / "c").glob("flood_*.json")), "an empty scan of a missing store was cached"
    called = []
    monkeypatch.setattr(F, "scan_flood_events",
                        lambda s, e, **k: called.append(1) or [])
    ev_pkg.detect_events(S, E, types=("flood",), cache_dir=tmp_path / "c", top_n=None)
    assert called, "second run must re-scan, not hit a cached empty list"


def test_other_lanes_still_run_when_flood_has_no_data(tmp_path, monkeypatch):
    """The point of the fix: TC (and MLC) are not killed by the flood lane."""
    import ewb_live.events.tc as tc
    monkeypatch.setattr(tc, "scan_tc_events", lambda s, e: [])
    monkeypatch.setattr(ev_pkg, "truth_cutoff",
                        lambda: dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setenv("EWB_LIVE_PARDIG_DIR", str(tmp_path / "nopardig"))
    monkeypatch.setenv("EWB_FLOOD_SOURCE", "pardig")
    out = ev_pkg.detect_events(S, E, types=("flood", "tropical_cyclone"),
                               cache_dir=tmp_path / "c", top_n=None)
    assert out == []
    assert ev_pkg.lane_status_report(tmp_path / "c", ("flood",))["flood"]["status"] == "no-data"


def test_unexpected_read_error_is_a_failed_status_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "load_observations", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert F.scan_flood_events(S, E, source="pardig") == []
    assert F.lane_status()["status"] == "failed" and "boom" in F.lane_status()["reason"]
