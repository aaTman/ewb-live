"""Dual-write sink, parity tool and cache retention report (SPEC §6 phase 2). Offline."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from ewb_live import results_sink as sink_mod
from ewb_live.events.types import Event
from ewb_live.stores import results_icechunk as rs
from ewb_live.tools import cache_retention, results_parity

COLS = ["event_id", "event_type", "model", "metric", "lead_time_hours", "value", "init_time"]


def _ev(eid: str, end=dt.datetime(2026, 10, 5)) -> Event:
    return Event(id=eid, event_type="heat_wave", title=f"t {eid}", start=end - dt.timedelta(days=4),
                 end=end, bbox=(0.0, 10.0, 0.0, 10.0), peak_value=33.0, details={"fill": 0.9})


def _rows(eid: str, n: int = 4, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"event_id": eid, "event_type": "heat_wave",
                         "model": ["aurora", "graphcast"] * (n // 2), "metric": "rmse",
                         "lead_time_hours": np.arange(n) * 6, "value": rng.normal(size=n),
                         "init_time": pd.to_datetime(["2026-10-01T00:00:00"] * n)})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_RESULTS_REPO", str(tmp_path / "store"))
    monkeypatch.setenv("EWB_LIVE_RUN_WEEK", "2026-W48")
    monkeypatch.setenv("EWB_LIVE_RUN_ID", "2026-W48-test")
    monkeypatch.delenv("EWB_LIVE_RESULTS_STORE", raising=False)
    monkeypatch.delenv("EWB_LIVE_RUN_WINDOW_END", raising=False)
    return tmp_path


def _open():
    return sink_mod.ResultsSink.open(dt.date(2026, 8, 26), dt.date(2026, 11, 24))


def test_disabled_by_env(env, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_RESULTS_STORE", "0")
    assert _open() is None


def test_run_identity_from_env_and_window_end_pin(env, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_RUN_WINDOW_END", "2026-11-23")
    run = sink_mod.make_run(dt.date(2026, 8, 25), dt.date(2026, 11, 24))
    assert (run.week, run.run_id, run.window_end) == ("2026-W48", "2026-W48-test", "2026-11-23")


def test_sync_commits_only_changed_events(env):
    s = _open()
    evs = [_ev("a"), _ev("b")]
    scores = pd.concat([_rows("a"), _rows("b", seed=1)], ignore_index=True)
    assert s.sync(evs, scores, {}, force=True) == 2
    assert s.sync(evs, scores, {}, force=True) == 0                       # nothing changed
    ps = {"b|aurora": {"status": "scored", "rows": 2}}
    assert s.sync(evs, scores, ps, force=True) == 1                        # status changed: only b
    scores2 = pd.concat([_rows("a", seed=9), _rows("b", seed=1)], ignore_index=True)
    assert s.sync(evs, scores2, ps, force=True) == 1                       # a's rows changed
    got = rs.read_scores(s.repo, s.run.branch)
    assert sorted(got["event_id"].unique()) == ["a", "b"] and len(got) == 8


def test_sync_drops_events_that_left_the_selection(env):
    s = _open()
    scores = pd.concat([_rows("a"), _rows("b")], ignore_index=True)
    s.sync([_ev("a"), _ev("b")], scores, {}, force=True)
    s.sync([_ev("a")], scores[scores["event_id"] == "a"], {}, force=True)
    assert [e["id"] for e in rs.list_events(s.repo, s.run.branch)] == ["a"]


def test_sync_is_throttled_unless_forced(env):
    s = _open()
    scores = _rows("a")
    assert s.sync([_ev("a")], scores, {}) == 1
    assert s.sync([_ev("b")], _rows("b"), {}) == 0                         # inside the interval
    assert s.sync([_ev("b")], _rows("b"), {}, force=True) == 1


def test_store_failures_never_raise_and_disable_after_three(env, monkeypatch):
    s = _open()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rs, "commit_event", boom)
    for _ in range(3):
        assert s.sync([_ev("a")], _rows("a"), {}, force=True) == 0
    assert s.disabled
    monkeypatch.undo()
    assert s.sync([_ev("a")], _rows("a"), {}, force=True) == 0             # stays off for the run


def test_resumed_run_does_not_recommit_unchanged_events(env):
    s1 = _open()
    s1.sync([_ev("a")], _rows("a"), {}, force=True)
    s2 = _open()                                                           # same run id: resume
    assert s2.sync([_ev("a")], _rows("a"), {}, force=True) == 0


def _write_results(res, events, scores, viz=True):
    res.mkdir(parents=True, exist_ok=True)
    (res / "events_selected.json").write_text(json.dumps([e.to_dict() for e in events]))
    scores[COLS].to_parquet(res / "scores.parquet")
    (res / "meta.json").write_text(json.dumps({"week": "2026-W48"}))
    if viz:
        for e in events:
            (res / "events" / e.id).mkdir(parents=True, exist_ok=True)
            (res / "events" / e.id / "viz.json").write_bytes(b'{"id": "%s"}' % e.id.encode())


def test_attach_publish_and_parity(env):
    s = _open()
    evs = [_ev("a"), _ev("b")]
    scores = pd.concat([_rows("a"), _rows("b", seed=1)], ignore_index=True)
    s.sync(evs, scores, {}, force=True)
    res = env / "results"
    _write_results(res, evs, scores)
    assert s.attach_files(res, ["a", "b"]) == {"blobs": 2, "docs": 2}      # events_selected + meta
    assert s.attach_files(res, ["a", "b"]) == {"blobs": 0, "docs": 0}      # idempotent
    pub = s.publish()
    assert pub["tip"] and pub["maintenance"]["confirm"] is False           # dry run, owner rule
    assert "2026-W48" in s.repo.list_tags() and s.run.branch not in s.repo.list_branches()
    rep = results_parity.compare(res, s.repo, "main")
    assert rep["ok"], rep["problems"]
    assert rep["events"] == 2 and rep["score_rows"] == 8 and rep["viz_blobs"] == 2


def test_parity_reports_each_kind_of_drift(env):
    s = _open()
    evs = [_ev("a")]
    scores = _rows("a")
    s.sync(evs, scores, {}, force=True)
    res = env / "results"
    _write_results(res, evs, scores)
    s.attach_files(res, ["a"])
    s.publish()
    drift = scores.copy()
    drift.loc[0, "value"] += 1.0
    _write_results(res, evs + [_ev("zz")], pd.concat([drift, _rows("zz")], ignore_index=True))
    (res / "events" / "a" / "viz.json").write_bytes(b"changed")
    (res / "meta.json").write_text("{}")
    probs = " | ".join(results_parity.compare(res, s.repo, "main")["problems"])
    for frag in ("missing from store", "score values differ", "doc meta.json differs",
                 "viz.json blobs differ"):
        assert frag in probs


def test_cache_retention_report_never_counts_undated_entries(tmp_path):
    c = tmp_path / "cache"
    (c / "fc_parts" / "aurora_2026060100Z_x.zarr").mkdir(parents=True)        # old
    (c / "fc_parts" / "aurora_2026060100Z_x.zarr" / "c").write_bytes(b"x" * 1000)
    (c / "fc_parts" / "aurora_2026110100Z_x.zarr").mkdir()                    # in window
    (c / "fc_parts" / "aurora_2026110100Z_x.zarr" / "c").write_bytes(b"x" * 500)
    (c / "fc_parts" / "weird.zarr").mkdir()                                   # undated
    (c / "fc_parts" / "weird.zarr" / "c").write_bytes(b"x" * 200)
    (c / "subsets" / "aurora" / "2026050100Z").mkdir(parents=True)
    (c / "subsets" / "aurora" / "2026050100Z" / "c").write_bytes(b"x" * 300)
    (c / "truth").mkdir()
    (c / "truth" / "hres_analysis_2026050100_2026050106_t2m_0x1.zarr").mkdir()
    (c / "truth" / "era5.zarr").mkdir()
    rep = cache_retention.report(c, window_days=90, margin_days=30, today=dt.date(2026, 11, 24))
    assert rep["keep_from"] == "2026-07-27"
    fp = rep["caches"]["fc_parts"]
    assert (fp["entries"], fp["evictable_entries"], fp["evictable_bytes"]) == (3, 1, 1000)
    assert (fp["undated_entries"], fp["undated_bytes"]) == (1, 200)
    assert rep["caches"]["subsets"]["evictable_bytes"] == 300
    assert rep["caches"]["truth"]["evictable_entries"] == 1                    # hres only
    assert rep["caches"]["truth"]["undated_entries"] == 1                      # era5.zarr mirror
