"""The midlat_cyclone lane must tell a broken scan from an empty ocean.

``scan_mlc_events`` fails soft — an unreachable ARCO must not take the weekly
run down with it — but a bare ``[]`` is indistinguishable from "no low met the
criteria", which is how the 0-event reader bug (``notes/mlc_fix.md``) spent a
whole 90-day dev build looking like a quiet autumn. The scan now records *why*
it ended (``mlc.LANE_STATUS``: ok / failed / no-data), ``detect_events`` persists
that next to the scan cache so a cache-hit run still knows, ``pipeline``
publishes it as ``meta.lane_status``, and the dashboard's single empty-lane
helper turns it into "lane failed: …" / "not computed: …" instead of the
criteria sentence. Same idea as the TC lane's ``meta.ibtracs`` freshness record.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

import ewb_live.events as ev_pkg
from ewb_live.events import mlc as M
from ewb_live.sources import mlc_era5

FIXTURE = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / \
    "mlc_ciaran_cutout.npz"


@pytest.fixture(autouse=True)
def _clean_status():
    M.LANE_STATUS.clear()
    ev_pkg.LANE_STATUS.clear()
    yield
    M.LANE_STATUS.clear()
    ev_pkg.LANE_STATUS.clear()


def _cutout():
    if not FIXTURE.exists():
        pytest.skip(f"missing real-frame fixture {FIXTURE}")
    with np.load(FIXTURE) as z:
        return ([dt.datetime.fromisoformat(str(t)) for t in z["times"]],
                z["mslp_all"].astype(np.float32), np.asarray(z["lats"], float),
                np.asarray(z["lons"], float))


def _no_tropical(lat, lon, t):
    return False


# ------------------------------------------------------- what the scan reports
def test_scan_reports_a_failed_read(monkeypatch):
    def boom(*a, **k):
        raise OSError("ARCO unreachable")
    monkeypatch.setattr(mlc_era5, "read_mslp_window", boom)

    assert M.scan_mlc_events(dt.date(2026, 8, 19), dt.date(2026, 11, 17)) == []
    st = M.lane_status()
    assert st["status"] == "failed"
    assert "ARCO unreachable" in st["reason"]
    assert st["lane"] == "midlat_cyclone" and st["rule_rev"] == M.RULE_REV


def test_scan_reports_missing_source_data(monkeypatch):
    monkeypatch.setattr(mlc_era5, "read_mslp_window",
                        lambda *a, **k: ([], np.zeros((0, 0, 0), np.float32),
                                         np.zeros(0), np.zeros(0)))

    assert M.scan_mlc_events(dt.date(2099, 1, 1), dt.date(2099, 1, 31)) == []
    st = M.lane_status()
    assert st["status"] == "no-data" and "no MSLP frames" in st["reason"]


def test_scan_reports_the_funnel_when_it_ran():
    times, mslp, lats, lons = _cutout()
    evs = M.scan_mlc_events(dt.date(2023, 10, 31), dt.date(2023, 11, 2),
                            fields=(times, mslp, lats, lons),
                            orography=(np.zeros((0, 0), np.float32),
                                       np.zeros(0), np.zeros(0)),
                            tropical_check=_no_tropical)
    st = M.lane_status()
    assert st["status"] == "ok" and st["reason"] is None
    assert st["n_frames"] == len(times) and st["n_nodes"] > 0
    assert st["n_tracks"] > 0 and st["n_events"] == len(evs)
    assert st["n_screened"] == sum(st["screened_by"].values())


# ------------------------------------------- recorded, persisted, published
def _fake_failed_scan(s, e, **kw):
    M._set_lane_status("failed", reason="ERA5 MSLP read failed: ARCO unreachable",
                       window=(s, e))
    return []


def _detect(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "scan_mlc_events", _fake_failed_scan)
    monkeypatch.setattr(ev_pkg, "truth_cutoff",
                        lambda: dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
    return ev_pkg.detect_events(dt.date(2026, 8, 19), dt.date(2026, 11, 17),
                                types=("midlat_cyclone",), cache_dir=tmp_path,
                                top_n=None)


def test_detect_events_records_and_persists_the_status(tmp_path, monkeypatch):
    assert _detect(tmp_path, monkeypatch) == []
    side = tmp_path / f"lane_status_midlat_cyclone_{M.RULE_REV}.json"
    assert side.exists(), "the scan outcome must survive the run that made it"
    assert json.loads(side.read_text())["status"] == "failed"
    assert ev_pkg.LANE_STATUS["midlat_cyclone"]["status"] == "failed"
    assert ev_pkg.lane_status_report(tmp_path)["midlat_cyclone"]["status"] \
        == "failed"


def test_cached_scan_still_reports_its_status(tmp_path, monkeypatch):
    """The weekly run usually reads the scan cache; a cached empty scan must not
    silently become a clean zero."""
    _detect(tmp_path, monkeypatch)
    ev_pkg.LANE_STATUS.clear()
    monkeypatch.setattr(M, "scan_mlc_events",
                        lambda *a, **k: pytest.fail("cached scan re-ran"))
    assert ev_pkg.detect_events(dt.date(2026, 8, 19), dt.date(2026, 11, 17),
                                types=("midlat_cyclone",), cache_dir=tmp_path,
                                top_n=None) == []
    assert ev_pkg.LANE_STATUS["midlat_cyclone"]["status"] == "failed"


def test_lane_that_reports_nothing_gets_no_entry(tmp_path, monkeypatch):
    """Additive contract: lanes without a report keep their old copy — the
    dashboard must not start calling a working lane failed."""
    import ewb_live.events.temperature as temperature

    monkeypatch.setattr(temperature, "scan_temperature_events",
                        lambda s, e, et, **k: [])
    monkeypatch.setattr(ev_pkg, "truth_cutoff",
                        lambda: dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
    ev_pkg.detect_events(dt.date(2026, 8, 19), dt.date(2026, 11, 17),
                         types=("freeze",), cache_dir=tmp_path, top_n=None)
    assert ev_pkg.lane_status_report(tmp_path) == {}
    assert not (tmp_path / "lane_status_freeze.json").exists()


def test_pipeline_publishes_lane_status(tmp_path, monkeypatch):
    from ewb_live import pipeline

    payload = {"midlat_cyclone": {"status": "failed", "reason": "boom"}}
    monkeypatch.setattr(ev_pkg, "lane_status_report", lambda *a, **k: payload)
    assert pipeline._lane_status_meta() == payload

    def explode(*a, **k):
        raise RuntimeError("no cache dir")
    monkeypatch.setattr(ev_pkg, "lane_status_report", explode)
    assert pipeline._lane_status_meta() == {}     # a status note never fails a publish


# ------------------------------------------------------------ the UI copy
def test_empty_lane_copy_lives_in_one_place():
    """``emptyLaneLine()`` is the single source of a lane's "nothing here"
    sentence (the tabs/chips, the event list and the scorecard lanes all read
    it), and it asks the lane's own status record before claiming an empty sky."""
    import re
    from pathlib import Path

    dash = Path(__file__).resolve().parents[1] / "dashboard"
    app = (dash / "app.js").read_text()
    assert app.count("function laneStatusNote(") == 1
    body = app.split("const emptyLaneLine", 1)[1].split("};", 1)[0]
    assert "laneStatusNote(t)" in body, "the status record must gate the criteria line"
    # the wording exists once, and nowhere else in the served JS
    for f in sorted(dash.glob("*.js")):
        n = f.read_text().count("lane failed, so this window has no numbers")
        assert n == (1 if f.name == "app.js" else 0), f"empty-lane copy duplicated in {f}"
    assert re.search(r"meta\.lane_status", app), "the UI reads meta.lane_status"
