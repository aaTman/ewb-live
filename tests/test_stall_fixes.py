"""Offline tests for the 2026-11-07 stall-postmortem fixes.

Covers: pipeline heartbeat publishing, per-job worker timeout surfacing,
and the ops/freshness_check.py restart decision table. No network, no box.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from ewb_live import pipeline as pl

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- heartbeat ----

def test_heartbeat_written(tmp_path, monkeypatch):
    # heartbeat lives OUTSIDE the served tree: RESULTS.parent (20:36Z move)
    monkeypatch.setattr(pl, "RESULTS", tmp_path / "results")
    (tmp_path / "results").mkdir()
    pl._heartbeat(phase="score", jobs_done=3, jobs_total=10)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["phase"] == "score"
    assert hb["jobs_done"] == 3 and hb["jobs_total"] == 10
    assert hb["pid"] > 0
    # ts parses as aware ISO
    ts = dt.datetime.fromisoformat(hb["ts"])
    assert ts.tzinfo is not None


def test_heartbeat_failure_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "RESULTS", Path("/proc/nonexistent-ewb"))
    pl._heartbeat(phase="score")  # must not raise


# ---------------------------------------------------------- run_worker ------

def test_run_worker_timeout(monkeypatch, tmp_path):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=k["timeout"])
    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out after 7s"):
        pl._run_worker(tmp_path / "j.json", tmp_path / "j.log", 7.0)


def test_run_worker_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(pl.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 137))
    with pytest.raises(RuntimeError, match="worker exit 137"):
        pl._run_worker(tmp_path / "j.json", tmp_path / "j.log", 60.0)


# -------------------------------------------------------- freshness_check ---

@pytest.fixture()
def fc(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "freshness_check", REPO / "ops" / "freshness_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # keep the restart-path tests out of the real logs/freshness.log
    mod.LOG = tmp_path / "freshness.log"
    return mod


def _mk_results(tmp_path, hb_age_s=None, meta_age_s=None, jobs_done=5,
                hb_pid=None):
    results = tmp_path / "results"
    results.mkdir()
    now = dt.datetime.now(dt.timezone.utc)
    if hb_age_s is not None:  # heartbeat sits beside, not inside, results/
        hb = {"ts": (now - dt.timedelta(seconds=hb_age_s)).isoformat(),
              "phase": "score", "jobs_done": jobs_done}
        if hb_pid:
            hb["pid"] = hb_pid
        (results.parent / "heartbeat.json").write_text(json.dumps(hb))
    if meta_age_s is not None:
        (results / "meta.json").write_text(json.dumps(
            {"last_updated": (now - dt.timedelta(seconds=meta_age_s)).isoformat()}))
    return results


def _fake_subprocess(active=True, weekly=False, restarts=None, main_pid=None):
    def fake(cmd, *a, **k):
        joined = " ".join(cmd)
        if "is-active" in joined:
            return subprocess.CompletedProcess(cmd, 0 if active else 3)
        if "MainPID" in joined:  # pid gate: None => empty stdout => fail-open
            return subprocess.CompletedProcess(
                cmd, 0, stdout=str(main_pid) if main_pid else "")
        if "pgrep" in joined:
            return subprocess.CompletedProcess(cmd, 0 if weekly else 1)
        if "restart" in joined:
            if restarts is not None:
                restarts.append(joined)
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(joined)
    return fake


def test_freshness_ok(fc, tmp_path, monkeypatch):
    results = _mk_results(tmp_path, hb_age_s=120, meta_age_s=300)
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess())
    assert fc.main(["--results", str(results)]) == 0


def test_freshness_acts_only_on_active_unit(fc, tmp_path, monkeypatch):
    results = _mk_results(tmp_path, hb_age_s=99 * 60)  # very stale
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess(active=False))
    assert fc.main(["--results", str(results)]) == 0  # never starts scoring


def test_freshness_wedged_parent_restarts(fc, tmp_path, monkeypatch):
    results = _mk_results(tmp_path, hb_age_s=120 * 60, meta_age_s=60)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess(restarts=restarts))
    assert fc.main(["--results", str(results), "--hb-max-min", "90"]) == 0
    assert any("restart" in r for r in restarts)


def test_freshness_publication_broken_restarts(fc, tmp_path, monkeypatch):
    # heartbeat alive, jobs finishing, but meta.json frozen 13 h: the
    # exact 2026-11-07 failure mode
    results = _mk_results(tmp_path, hb_age_s=60, meta_age_s=13 * 3600, jobs_done=12)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess(restarts=restarts))
    assert fc.main(["--results", str(results)]) == 0
    assert any("restart" in r for r in restarts)


def test_freshness_meta_stale_in_detect_no_action(fc, tmp_path, monkeypatch):
    # meta old but heartbeat says no jobs finished (detect/mirror phase):
    # warn, don't kill
    results = _mk_results(tmp_path, hb_age_s=60, meta_age_s=13 * 3600, jobs_done=0)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess(restarts=restarts))
    assert fc.main(["--results", str(results)]) == 0
    assert not restarts


def test_freshness_skips_during_weekly(fc, tmp_path, monkeypatch):
    results = _mk_results(tmp_path, hb_age_s=120 * 60, meta_age_s=13 * 3600)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run",
                        _fake_subprocess(weekly=True, restarts=restarts))
    assert fc.main(["--results", str(results)]) == 0
    assert not restarts


def test_freshness_missing_heartbeat_restarts(fc, tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run", _fake_subprocess(restarts=restarts))
    assert fc.main(["--results", str(results), "--hb-max-min", "90"]) == 0
    assert any("restart" in r for r in restarts)


def test_freshness_foreign_pid_beat_restarts(fc, tmp_path, monkeypatch):
    # 22:21Z masking class: a standalone CLI (e.g. truth_mirror sync) beats
    # into the same file with a fresh ts while the unit itself is wedged;
    # pid != MainPID must read as silence and take the restart path.
    results = _mk_results(tmp_path, hb_age_s=60, meta_age_s=60, hb_pid=4242)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run",
                        _fake_subprocess(restarts=restarts, main_pid=9999))
    assert fc.main(["--results", str(results)]) == 0
    assert any("restart" in r for r in restarts)


def test_freshness_matching_pid_beat_no_action(fc, tmp_path, monkeypatch):
    results = _mk_results(tmp_path, hb_age_s=60, meta_age_s=60, hb_pid=4242)
    restarts = []
    monkeypatch.setattr(fc.subprocess, "run",
                        _fake_subprocess(restarts=restarts, main_pid=4242))
    assert fc.main(["--results", str(results)]) == 0
    assert not restarts


def test_with_retries_retries_429_but_not_404():
    """_net.with_retries must honor its docstring: 429 is the real S3/
    CloudFront throttle code (ponytail 2026-11-08); 404 stays fatal."""
    import urllib.error

    from ewb_live.sources import _net

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)
        return "ok"

    assert _net.with_retries(flaky, tries=4, base_delay=0.0) == "ok"
    assert calls["n"] == 3

    n404 = {"n": 0}

    def notfound():
        n404["n"] += 1
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)

    with pytest.raises(urllib.error.HTTPError):
        _net.with_retries(notfound, tries=3, base_delay=0.0)
    # HTTPError is an OSError subclass: a definitive 404 must fail on the
    # first call, not walk the backoff ladder (which the old bare-OSError
    # check silently did, 22:21Z finding)
    assert n404["n"] == 1


def test_with_retries_not_found_s3_path_raises_first_call():
    """s3fs surfaces a definitive 404 as FileNotFoundError (OSError
    subclass) — must raise immediately, not burn the ladder (ponytail
    2026-11-08 23:42 SHOULD; aigfs .idx probes depend on fast continue)."""
    from ewb_live.sources import _net

    n = {"n": 0}

    def missing():
        n["n"] += 1
        raise FileNotFoundError("NoSuchKey")

    with pytest.raises(FileNotFoundError):
        _net.with_retries(missing, tries=6, base_delay=0.0)
    assert n["n"] == 1


def test_open_remote_nc_config_not_in_client_kwargs(monkeypatch):
    """aiobotocore 3.10 D1 collision: config in client_kwargs alongside
    region raises 'multiple values for config' (ponytail MUST 23:42)."""
    from ewb_live.sources import _net

    captured = {}

    class _OF:
        def open(self):
            return "stream"

    def fake_open(path, **kw):
        captured.update(kw)
        return _OF()

    monkeypatch.setattr("fsspec.open", fake_open)
    assert _net.open_remote_nc("s3://b/k.nc") == "stream"
    assert "config" not in captured.get("client_kwargs", {})
    assert captured["client_kwargs"]["region_name"] == "us-east-1"
    assert captured["config_kwargs"] is not None
