"""R1 guard of ops/lane_backfill.sh (notes/scoring_design.md 8.0 / 8.3).

State-based: fake `systemctl` (state read from a file), fake lock file (a real
flock held by the test), fake clock (LANE_GUARD_NOW). No weekly service, no box.
"""
import fcntl
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "lane_backfill.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# fake systemctl: `is-active <unit>` -> $FAKE_STATE_FILE, `show <unit> -p ActiveExitTimestamp ...` -> $FAKE_EXIT_FILE
case "$1" in
  is-active) cat "$FAKE_STATE_FILE" ;;
  show) echo "ActiveExitTimestamp=$(cat "$FAKE_EXIT_FILE" 2>/dev/null)" ;;
esac
"""


@pytest.fixture
def box(tmp_path):
    sc = tmp_path / "systemctl"
    sc.write_text(FAKE_SYSTEMCTL)
    sc.chmod(sc.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "state"
    state.write_text("inactive\n")
    exit_ts = tmp_path / "exit_ts"
    exit_ts.write_text("")
    lock = tmp_path / "deploy.lock"
    lock.write_text("")
    env = {**os.environ, "LANE_GUARD_TEST": "1", "LANE_GUARD_SYSTEMCTL": str(sc), "LANE_GUARD_LOCK": str(lock),
           "FAKE_STATE_FILE": str(state), "FAKE_EXIT_FILE": str(exit_ts),
           "LANE_GUARD_NOW": "2026-11-21 12:00:00 UTC",           # a Saturday
           "LANE_GUARD_POLL": "1", "LANE_GUARD_DRAIN": "3"}
    return {"env": env, "state": state, "exit": exit_ts, "lock": lock, "tmp": tmp_path}


def run(box, *args, job=None, now=None, timeout=60, **kw):
    env = dict(box["env"])
    if now:
        env["LANE_GUARD_NOW"] = now
    if job is not None:
        env["LANE_JOB_CMD"] = job
    return subprocess.run(["bash", str(SCRIPT), "tc", *args], env=env, capture_output=True,
                          text=True, timeout=timeout, **kw)


def check(box, **kw):
    return run(box, "--check-only", **kw)


@pytest.mark.parametrize("state", ["active", "activating", "reloading", "deactivating"])
def test_refuses_while_weekly_is_live(box, state):
    box["state"].write_text(state + "\n")
    r = check(box)
    assert r.returncode == 3, r.stderr
    assert "REFUSED" in r.stderr and state in r.stderr


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_starts_when_weekly_is_idle(box, state):
    box["state"].write_text(state + "\n")
    r = check(box)
    assert r.returncode == 0, r.stderr


def test_refuses_when_state_cannot_be_read(box):
    box["env"]["LANE_GUARD_SYSTEMCTL"] = "/nonexistent/systemctl"
    r = check(box)
    assert r.returncode == 3 and "cannot prove" in r.stderr


def test_refuses_when_deploy_lock_is_held(box):
    with open(box["lock"], "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        r = check(box)
    assert r.returncode == 3 and "lock" in r.stderr
    assert check(box).returncode == 0                      # released -> ok again


def test_missing_lock_file_is_not_held(box):
    box["lock"].unlink()
    assert check(box).returncode == 0
    assert not box["lock"].exists()                        # the probe must not create it


# 2026-11-23 is a Monday (weekly timer 07:00 UTC)
@pytest.mark.parametrize("now,expect", [
    ("2026-11-23 06:29:59 UTC", 0),      # before the window: state checks alone decide
    ("2026-11-23 06:30:00 UTC", 3),      # window opens, weekly not run yet
    ("2026-11-23 10:00:00 UTC", 3),
    ("2026-11-23 13:59:59 UTC", 3),
    ("2026-11-23 14:00:00 UTC", 0),      # window closed
    ("2026-11-22 10:00:00 UTC", 0),      # Sunday, same hours: no clock restriction
    ("2026-11-24 10:00:00 UTC", 0),      # Tuesday
])
def test_monday_window_before_weekly_has_run(box, now, expect):
    r = check(box, now=now)
    assert r.returncode == expect, r.stderr
    if expect == 3:
        assert "Monday" in r.stderr


def test_monday_window_open_if_weekly_already_ran_this_week(box):
    box["exit"].write_text("Mon 2026-11-23 09:30:00 UTC")
    assert check(box, now="2026-11-23 10:00:00 UTC").returncode == 0
    box["exit"].write_text("Mon 2026-11-23 07:00:00 UTC")           # exactly this Monday 07:00: at or after
    assert check(box, now="2026-11-23 10:00:00 UTC").returncode == 0


def test_monday_window_stays_shut_if_last_run_was_last_week(box):
    box["exit"].write_text("Mon 2026-11-16 16:12:00 UTC")
    r = check(box, now="2026-11-23 10:00:00 UTC")
    assert r.returncode == 3 and "before this Monday 07:00" in r.stderr
    box["exit"].write_text("Mon 2026-11-23 06:59:59 UTC")
    assert check(box, now="2026-11-23 10:00:00 UTC").returncode == 3


def test_monday_window_unparseable_exit_timestamp_refuses(box):
    box["exit"].write_text("garbage")
    assert check(box, now="2026-11-23 10:00:00 UTC").returncode == 3


def test_active_weekly_wins_even_if_it_ran_this_week(box):
    box["exit"].write_text("Mon 2026-11-23 09:30:00 UTC")
    box["state"].write_text("active\n")
    assert check(box, now="2026-11-23 10:00:00 UTC").returncode == 3


# ---------------------------------------------------------------- the poll ---
def test_job_runs_and_its_status_is_returned(box):
    r = run(box, job="exit 0")
    assert r.returncode == 0 and "job exited rc=0" in r.stderr
    r = run(box, job="exit 7")
    assert r.returncode == 7


def test_refused_start_never_launches_the_job(box):
    box["state"].write_text("active\n")
    marker = box["tmp"] / "ran"
    r = run(box, job=f"touch {marker}")
    assert r.returncode == 3 and not marker.exists()


def test_poll_sends_sigterm_waits_for_the_drain_and_exits_zero(box):
    marker, started = box["tmp"] / "drained", box["tmp"] / "started"
    job = (f"trap 'sleep 1; echo drained > {marker}; exit 0' TERM; touch {started}; "
           "while true; do sleep 0.2; done")
    p = subprocess.Popen(["bash", str(SCRIPT), "tc"], env={**box["env"], "LANE_JOB_CMD": job},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.1)
    assert started.exists()
    box["state"].write_text("activating\n")                 # the weekly service comes up mid-run
    _, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    assert marker.read_text().strip() == "drained"          # it was allowed to finish its cleanup
    assert "STOPPING" in err and "activating" in err and "resumable" in err.replace("resume", "resumable")


def test_poll_takes_the_lock_as_a_stop_signal_too(box):
    started = box["tmp"] / "started"
    job = f"trap 'exit 0' TERM; touch {started}; while true; do sleep 0.2; done"
    p = subprocess.Popen(["bash", str(SCRIPT), "tc"], env={**box["env"], "LANE_JOB_CMD": job},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.1)
    with open(box["lock"], "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _, err = p.communicate(timeout=60)
    # PAUSED FOR DEPLOY: distinct from success so a stopped backfill is not mistaken for a finished one
    assert p.returncode == 75 and "PAUSED FOR DEPLOY" in err and "STOPPING" in err, (p.returncode, err)


def test_a_pool_that_will_not_drain_is_killed_after_the_wait(box):
    started = box["tmp"] / "started"
    job = f"trap '' TERM; touch {started}; while true; do sleep 0.2; done"
    p = subprocess.Popen(["bash", str(SCRIPT), "tc"], env={**box["env"], "LANE_JOB_CMD": job},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.1)
    box["state"].write_text("active\n")
    _, err = p.communicate(timeout=60)
    assert p.returncode == 0 and "SIGKILL" in err


def test_hard_cap_stops_the_job(box):
    r = run(box, "--cap-hours", "0.0006", job="sleep 30", timeout=60)       # ~2 s
    assert r.returncode == 124 and "hard cap" in r.stderr


# ------------------------------------------------- test hooks are gated (review)
def test_hooks_are_ignored_without_the_test_flag(box):
    marker = box["tmp"] / "fake_systemctl_called"
    sc = box["tmp"] / "systemctl"
    sc.write_text(f"#!/usr/bin/env bash\ntouch {marker}\necho active\n")
    env = {k: v for k, v in box["env"].items() if k != "LANE_GUARD_TEST"}
    env["LANE_JOB_CMD"] = f"touch {box['tmp'] / 'job_ran'}"
    r = subprocess.run(["bash", str(SCRIPT), "tc", "--check-only"], env=env, capture_output=True, text=True, timeout=60)
    assert not marker.exists(), "a stray LANE_GUARD_SYSTEMCTL must not fake the guard"
    assert "is active" not in r.stderr or "fake" not in r.stderr
    assert not (box["tmp"] / "job_ran").exists()


def test_header_says_hooks_need_the_flag():
    head = SCRIPT.read_text().split("set -uo pipefail")[0]
    assert "LANE_GUARD_TEST=1" in head and "ignored" in head


# ------------------------------------------------------- live-tree refusal (review)
@pytest.fixture
def trees(box):
    """tmp/live (the fake live tree) and tmp/priv (a private tree), each with its own copy of the script."""
    live, priv = box["tmp"] / "live", box["tmp"] / "priv"
    for t in (live, priv):
        (t / "ops").mkdir(parents=True)
        (t / "ops" / "lane_backfill.sh").write_text(SCRIPT.read_text())
        (t / "data" / "results").mkdir(parents=True)
        (t / "data" / "cache" / "score_parts").mkdir(parents=True)
    box["env"]["LANE_GUARD_LIVE_ROOT"] = str(live)
    return live, priv


def run_in(box, tree, *args, env_extra=None):
    env = {**box["env"], **(env_extra or {})}
    return subprocess.run(["bash", str(tree / "ops" / "lane_backfill.sh"), "tc", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_refuses_to_run_from_the_live_tree(box, trees):
    live, priv = trees
    r = run_in(box, live, "--check-only")
    assert r.returncode == 3 and "live tree" in r.stderr, r.stderr
    assert run_in(box, priv, "--check-only").returncode == 0


@pytest.mark.parametrize("form", ["sep", "eq"])
@pytest.mark.parametrize("flag", ["--results", "--parts"])
def test_refuses_results_or_parts_under_the_live_tree_also_after_dashdash(box, trees, flag, form):
    live, priv = trees
    target = str(live / "data" / "results")
    args = ["--check-only", "--", f"{flag}={target}"] if form == "eq" else ["--check-only", "--", flag, target]
    r = run_in(box, priv, *args)
    assert r.returncode == 3 and "live tree" in r.stderr, r.stderr
    ok = run_in(box, priv, "--check-only", "--", flag, str(priv / "data" / "results"))
    assert ok.returncode == 0, ok.stderr


def test_refuses_a_relative_or_symlinked_path_that_resolves_into_live(box, trees):
    live, priv = trees
    (priv / "alias").symlink_to(live / "data" / "results")
    r = run_in(box, priv, "--check-only", "--", "--results", "alias")
    assert r.returncode == 3, r.stderr
    r = run_in(box, priv, "--check-only", "--", "--parts", "../live/data/cache/score_parts")
    assert r.returncode == 3, r.stderr


def test_refuses_when_the_default_data_dirs_are_symlinked_into_live(box, trees):
    live, priv = trees
    import shutil

    shutil.rmtree(priv / "data")
    (priv / "data").symlink_to(live / "data")
    r = run_in(box, priv, "--check-only")
    assert r.returncode == 3 and "default data/results" in r.stderr, r.stderr


def test_allow_live_override(box, trees):
    live, _ = trees
    assert run_in(box, live, "--check-only", env_extra={"LANE_BACKFILL_ALLOW_LIVE": "1"}).returncode == 0
