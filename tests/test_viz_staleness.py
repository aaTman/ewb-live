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
    built, write_events, _tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    write_events([event(bbox=(10.0, 20.0, 30.0, 42.0))])
    viz_main.main(["--all"])
    assert len(built) == 2


def test_legacy_asset_without_stamp_is_rebuilt_once(harness):
    """A temperature asset predating the stamp is rebuilt once, then quiet.

    The persistence box of an ongoing spell is redrawn every weekly run, so
    an unstamped heat/freeze/cold_snap asset can never be trusted - the
    release rebuilds it and writes the stamp for every later run.
    """
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


def test_legacy_unstamped_ar_asset_is_not_rebuilt(harness):
    """An AR asset with no window and the current schema is treated current.

    The release must not rebuild all 40 unstamped AR assets (review #2, real
    cost ~95 assets); the AR lane only changes with a schema bump.
    """
    built, write_events, tmp = harness
    ar = {"id": "ar_20260901T00_1", "event_type": "atmospheric_river",
          "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z",
          "bbox": [10.0, 20.0, 30.0, 40.0], "details": {}}
    write_events([ar])
    viz_main.main(["--all"])                 # first build (no asset yet)
    assert built == ["ar_20260901T00_1"]
    p = asset_path(tmp, "ar_20260901T00_1")
    old = json.loads(p.read_text())
    old.pop("event_stamp", None)             # pretend it predates the stamp
    p.write_text(json.dumps(old))
    viz_main.main(["--all"])
    assert built == ["ar_20260901T00_1"]     # unstamped AR: not rebuilt


def test_legacy_unstamped_flood_asset_current_window_not_rebuilt(harness):
    """A flood asset that records its own (still-matching) window is current."""
    built, write_events, tmp = harness
    flood = {"id": "flood-20260901-1", "event_type": "flood",
             "start": "2026-09-01T00:00:00Z", "end": "2026-09-04T00:00:00Z",
             "bbox": [10.0, 20.0, 30.0, 40.0], "details": {}}
    write_events([flood])
    viz_main.main(["--all"])
    p = asset_path(tmp, "flood-20260901-1")
    old = json.loads(p.read_text())
    old.pop("event_stamp", None)
    # the window the old flood builder recorded: day range 09-01..09-03
    old["window"] = {"start": "2026-09-01", "end": "2026-09-03"}
    p.write_text(json.dumps(old))
    viz_main.main(["--all"])
    assert built == ["flood-20260901-1"]     # window still equals the event
    # ... but once the event grows the window no longer matches -> rebuild
    flood["end"] = "2026-09-06T00:00:00Z"
    write_events([flood])
    viz_main.main(["--all"])
    assert built == ["flood-20260901-1", "flood-20260901-1"]


def test_time_budget_spent_skips_events_but_runs_global_steps(
        harness, monkeypatch):
    """--time-budget-s stops new event builds yet runs the global steps.

    global_timeline.json must never keep old event ids, so the budget only
    gates the per-event loop: update_index, timeline.build and raw_exceedance
    still run, and the skipped ids are logged (the next weekly run resumes).
    """
    built, write_events, tmp = harness
    events = [event(f"heat_wave-2026090{k}-3") for k in (1, 2, 3)]
    write_events(events)
    # fake clock: monotonic jumps past the budget after the first build
    clock = {"t": 0.0}
    monkeypatch.setattr(viz_main.time, "monotonic", lambda: clock["t"])
    real_build = build.build_event

    def slow_build(ev, cutoff, hl, al):
        doc = real_build(ev, cutoff, hl, al)
        clock["t"] += 100.0                   # this build cost 100 s
        return doc
    monkeypatch.setattr(build, "build_event", slow_build)
    global_done = {"timeline": False, "raw": False}
    monkeypatch.setattr(viz_main, "timeline",
                        type("_t", (), {"build": staticmethod(
                            lambda r: global_done.__setitem__("timeline", True))}))
    monkeypatch.setattr(viz_main, "raw_exceedance",
                        type("_x", (), {"build": staticmethod(
                            lambda r: global_done.__setitem__("raw", True))}))
    assert viz_main.main(["--all", "--time-budget-s", "150"]) == 0
    assert built == ["heat_wave-20260901-3", "heat_wave-20260902-3"]
    assert global_done["timeline"] and global_done["raw"]   # global steps ran
    index = json.loads((tmp / "data/results/events/index.json").read_text())
    assert "heat_wave-20260903-3" not in index["events"]    # the 3rd was skipped


def test_time_budget_skips_resume_next_run(harness):
    """The run after a budget-skip builds the events the budget skipped."""
    built, write_events, _tmp = harness
    events = [event(f"heat_wave-2026090{k}-3") for k in (1, 2)]
    write_events(events)
    assert viz_main.main(["--all", "--time-budget-s", "0"]) == 0  # budget 0 = no budget
    assert built == ["heat_wave-20260901-3", "heat_wave-20260902-3"]



def test_force_rebuilds_anyway(harness):
    built, write_events, _tmp = harness
    write_events([event()])
    viz_main.main(["--all"])
    viz_main.main(["--all", "--force"])
    assert len(built) == 2
