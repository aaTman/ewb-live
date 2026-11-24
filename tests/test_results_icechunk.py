"""Results store (Icechunk): per-event commits, run/publish branches, rolling-window
retention and bounded repo size. Offline: local-filesystem repo under tmp_path."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ewb_live.stores import results_icechunk as rs


def _event(eid: str, end: str, etype: str = "heat_wave") -> dict:
    return {"id": eid, "event_type": etype, "start": end[:8] + "01T00:00:00",
            "end": end + "T00:00:00", "bbox": [0.0, 10.0, 0.0, 10.0]}


def _scores(n: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "model": ["aurora", "graphcast"] * (n // 2),
        "metric": ["rmse"] * n,
        "lead_time_hours": np.arange(n, dtype="int32") * 6,
        "value": rng.normal(size=n),
        "init_time": pd.to_datetime(["2026-09-01T00:00:00"] * n),
    })


def _run(week: str = "2026-W48", start: str = "2026-08-25") -> rs.Run:
    return rs.Run(week=week, run_id=f"{week}-a", window_start=start,
                  window_end="2026-11-22", rule_rev="c3-v4", code_sha="abc1234")


@pytest.fixture()
def repo(tmp_path):
    return rs.open_repo(str(tmp_path / "repo"), create=True)


def test_event_roundtrip_scores_blobs_grids(repo):
    run = _run()
    rs.begin_run(repo, run)
    sc = _scores()
    grid = np.arange(12, dtype="uint8").reshape(3, 4)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), sc,
                    status={"aigfs": {"status": "unsupported"}},
                    blobs={"viz.json": b'{"ok": true}'}, grids={"mask": grid})
    got = rs.read_scores(repo, run.branch)
    assert list(got["event_id"].unique()) == ["e1"]
    assert got["value"].tolist() == pytest.approx(sc["value"].tolist())
    assert got["model"].tolist() == sc["model"].tolist()
    assert rs.read_blob(repo, "e1", "viz.json", run.branch) == b'{"ok": true}'
    assert np.array_equal(rs.read_grid(repo, "e1", "mask", run.branch), grid)
    ev = rs.list_events(repo, run.branch)[0]
    assert ev["status"] == {"aigfs": {"status": "unsupported"}}


def test_commit_metadata_references_the_run(repo):
    run = _run()
    rs.begin_run(repo, run)
    sid = rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores())
    meta = next(a.metadata for a in repo.ancestry(branch=run.branch) if a.id == sid)
    assert meta["week"] == "2026-W48" and meta["run_id"] == "2026-W48-a"
    assert meta["rule_rev"] == "c3-v4" and meta["event_id"] == "e1"


def test_run_branch_is_live_but_main_unchanged_until_publish(repo):
    run = _run()
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores())
    assert rs.progress(repo, run)["events"] == 1
    assert rs.list_events(repo, rs.PUBLISHED) == []          # not published yet
    rs.commit_event(repo, run, _event("e2", "2026-09-06"), _scores(seed=1))
    rs.publish(repo, run)
    assert {e["id"] for e in rs.list_events(repo)} == {"e1", "e2"}
    assert run.week in repo.list_tags()
    assert {e["id"] for e in rs.list_events(repo, run.week)} == {"e1", "e2"}


def test_publish_same_tip_is_idempotent_other_tip_refused(repo):
    run = _run()
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores())
    tip = rs.publish(repo, run)
    assert run.branch not in repo.list_branches()              # retired; the tag pins it
    assert rs.publish(repo, run) == tip                        # re-publish: no-op
    rs.begin_run(repo, run)                                    # same week re-run branches off main
    rs.commit_event(repo, run, _event("e2", "2026-09-06"), _scores())
    with pytest.raises(RuntimeError, match="different snapshot"):
        rs.publish(repo, run)


def test_begin_run_resumes_and_keeps_committed_events(repo):
    run = _run()
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores())
    n = len(list(repo.ancestry(branch=run.branch)))
    rs.begin_run(repo, run)                                    # same run: no new commit
    assert len(list(repo.ancestry(branch=run.branch))) == n
    assert {e["id"] for e in rs.list_events(repo, run.branch)} == {"e1"}


def test_rescoring_an_event_replaces_it(repo):
    run = _run()
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores(6), blobs={"a": b"1"})
    rs.commit_event(repo, run, _event("e1", "2026-09-08"), _scores(2))
    got = rs.read_scores(repo, run.branch)
    assert len(got) == 2
    assert [e["end"] for e in rs.list_events(repo, run.branch)] == ["2026-09-08T00:00:00"]
    with pytest.raises(KeyError):                              # old blob is gone
        rs.read_blob(repo, "e1", "a", run.branch)


def test_documented_zero_event_with_no_rows(repo):
    run = _run()
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("e1", "2026-09-05"), _scores(0),
                    status={"aurora": {"status": "no-data"}})
    assert rs.read_scores(repo, run.branch).empty
    assert rs.list_events(repo, run.branch)[0]["status"]["aurora"]["status"] == "no-data"


def test_prune_window_drops_events_past_the_edge_keeps_undatable(repo):
    run = _run(start="2026-09-01")
    rs.begin_run(repo, run)
    rs.commit_event(repo, run, _event("old", "2026-08-31"), _scores())
    rs.commit_event(repo, run, _event("edge", "2026-09-01"), _scores())
    rs.commit_event(repo, run, _event("new", "2026-10-10"), _scores())
    bad = _event("undated", "2026-10-10")
    bad["end"] = "garbage"
    rs.commit_event(repo, run, bad, _scores())
    assert rs.prune_window(repo, run) == ["old"]
    assert {e["id"] for e in rs.list_events(repo, run.branch)} == {"edge", "new", "undated"}
    assert rs.prune_window(repo, run) == []                    # idempotent, no empty commit


def test_maintenance_is_a_dry_run_by_default(repo):
    for w in ("2026-W40", "2026-W41", "2026-W42"):
        run = _run(w)
        rs.begin_run(repo, run)
        rs.commit_event(repo, run, _event("e" + w, "2026-09-05"), _scores())
        rs.publish(repo, run)
    rep = rs.maintenance(repo, keep_tag_weeks=1)
    assert rep["tags_dropped"] == ["2026-W40", "2026-W41"] and rep["tags_kept"] == ["2026-W42"]
    assert not rep["confirm"] and set(repo.list_tags()) == {"2026-W40", "2026-W41", "2026-W42"}


def test_repo_size_is_bounded_by_the_window_not_by_history(tmp_path):
    """Roll the 90-day window for 30 weeks (it fills at ~13); with prune + maintenance the repo stays
    flat instead of growing with every week (the owner's retention requirement)."""
    path = str(tmp_path / "repo")
    repo = rs.open_repo(path, create=True)
    rng = np.random.default_rng(7)
    start = dt.date(2026, 1, 5)
    sizes = []
    for wk in range(30):
        week_start = start + dt.timedelta(weeks=wk)
        window_start = (week_start - dt.timedelta(days=90)).isoformat()
        run = rs.Run(week=f"2026-W{wk + 2:02d}", run_id=f"r{wk}", window_start=window_start,
                     window_end=week_start.isoformat())
        rs.begin_run(repo, run)
        for k in range(4):                                     # 4 new events a week, ~400 KB each
            end = (week_start - dt.timedelta(days=k * 3)).isoformat()
            rs.commit_event(repo, run, _event(f"w{wk}e{k}", end), _scores(),
                            blobs={"viz.json": rng.bytes(200_000)},
                            grids={"mask": rng.integers(0, 255, (250, 800), dtype="uint8")})
        rs.prune_window(repo, run)
        rs.publish(repo, run)
        rs.maintenance(repo, keep_tag_weeks=2, snapshot_keep_days=0, confirm=True)
        sizes.append(rs.repo_bytes(path))
    in_window = len(rs.list_events(repo))
    assert in_window <= 4 * 14                                 # ~13 weeks of events at most
    # the window is full by week ~14; after that the repo stays flat instead of growing
    assert max(sizes[18:]) <= sizes[14] * 1.2
    # the repo is ~ what the window holds (+ tag deltas), well under keep-everything
    assert sizes[-1] < in_window * 400_000 * 1.4
    assert sizes[-1] < 30 * 4 * 400_000 * 0.7


def test_second_writer_is_refused(tmp_path):
    path = str(tmp_path / "repo")
    with rs.write_lock(path), \
            pytest.raises(RuntimeError, match="another results-store writer"), \
            rs.write_lock(path):
        pass
