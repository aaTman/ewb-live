"""viz rebuild-on-staleness (review #1): an ongoing event must not keep a
stale asset. The skip in ewb_live/viz/__main__.py requires the stored
``event_stamp`` (start, end, bbox) to match the current event, so a grown
window/box rebuilds the viz.json the map, the timeline and
``regions.load_footprints`` read. Synthetic events, no network, no LXC.
"""
import datetime as dt
import json

import pytest

from ewb_live.viz import __main__ as viz_main
from ewb_live.viz import build
from ewb_live.viz import timeline as viz_timeline


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    """main() with the host guard, truth probe, builder and side steps stubbed."""
    built: list[str] = []

    def fake_build_event(ev, cutoff, heat_leads, ar_leads):
        built.append(ev["id"])
        return {"schema": build.SCHEMA, "kind": "consecutive_days",
                "event_type": ev["event_type"]}

    monkeypatch.setattr("ewb_live._guard.require_ewb_host", lambda: None)
    monkeypatch.setattr("ewb_live.scoring.truth.probe_arco_cutoff",
                        lambda: dt.datetime(2026, 11, 13, tzinfo=dt.UTC))
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "data/results/events")
    monkeypatch.setattr(build, "build_event", fake_build_event)
    monkeypatch.setattr(viz_main, "timeline",
                        type("_t", (), {"build": staticmethod(lambda r: None)}))
    monkeypatch.setattr(viz_main, "raw_exceedance",
                        type("_x", (), {"build": staticmethod(lambda r: None)}))

    results = tmp_path / "data/results"
    results.mkdir(parents=True)

    def write_events(evs: list[dict]) -> None:
        (results / "events.json").write_text(json.dumps(evs))

    return built, write_events, tmp_path


def event(eid: str = "heat_wave-20260901-3", end: str = "2026-09-05T00:00:00Z",
          bbox=(10.0, 20.0, 30.0, 40.0)) -> dict:
    return {"id": eid, "event_type": "heat_wave",
            "start": "2026-09-01T00:00:00Z", "end": end, "bbox": list(bbox),
            "details": {}}


def asset_path(tmp_path, eid: str):
    return tmp_path / "data/results/events" / eid / "viz.json"


def test_first_run_builds_second_run_skips(harness):
    built, write_events, tmp = harness
    write_events([event()])
    assert viz_main.main(["--all"]) == 0
    assert built == ["heat_wave-20260901-3"]
    asset = json.loads(asset_path(tmp, "heat_wave-20260901-3").read_text())
    assert asset["event_stamp"] == {"start": "2026-09-01T00:00:00Z",
                                    "end": "2026-09-05T00:00:00Z",
                                    "bbox": [10.0, 20.0, 30.0, 40.0]}
    assert viz_main.main(["--all"]) == 0
    assert built == ["heat_wave-20260901-3"]          # unchanged: skipped


def test_ongoing_event_rebuilds_when_it_grows(harness):
    built, write_events, tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    # week 2: the ongoing spell is 3 days longer and its persistence box grew
    write_events([event(end="2026-09-08T00:00:00Z", bbox=(9.5, 21.0, 30.0, 40.0))])
    viz_main.main(["--all"])
    assert built == ["heat_wave-20260901-3", "heat_wave-20260901-3"]
    asset = json.loads(asset_path(tmp, "heat_wave-20260901-3").read_text())
    assert asset["event_stamp"]["end"] == "2026-09-08T00:00:00Z"


def test_bbox_only_change_rebuilds(harness):
    built, write_events, tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    write_events([event(bbox=(10.0, 20.0, 30.0, 42.0))])
    viz_main.main(["--all"])
    assert len(built) == 2


def test_legacy_asset_without_stamp_is_rebuilt_once(harness):
    built, write_events, tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    # an asset from before the stamp existed (same schema): rebuilt once, then quiet
    p = asset_path(tmp, "heat_wave-20260901-3")
    old = json.loads(p.read_text())
    old.pop("event_stamp")
    p.write_text(json.dumps(old))
    viz_main.main(["--all"])
    assert len(built) == 2
    viz_main.main(["--all"])
    assert len(built) == 2


def test_force_rebuilds_anyway(harness):
    built, write_events, tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    viz_main.main(["--all", "--force"])
    assert len(built) == 2
