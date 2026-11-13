#!/usr/bin/env python3
"""EWB Live: hourly Ponytail code-review driver (agent tooling, NOT EWB compute).

Runs on the orca container under ewb-live-ponytail-review.service (systemd
--user, flock-guarded by the unit). One run:

  1. Decide mode: FULL SWEEP once per UTC day (and on first run), otherwise
     incremental (files changed since the last run's start).
  2. Write the scope to notes/.ponytail_worklist.txt (the review agent reads it).
  3. Close the previous run's Orca terminal if it is still open.
  4. Launch ONE Hermes review agent in this worktree:
       orca agent launch --agent pi --surface terminal --prompt "$(cat notes/briefs/ponytail_review.md)" --json
  5. Wait until the agent writes notes/.ponytail_done.txt bearing this run's
     RUN STAMP (its last step per the brief), or the deadline hits (45 min
     incremental / 110 min full sweep; timeouts do not advance state);
     then close the terminal so tabs never pile up.
  6. Update notes/.ponytail_state.json (mtime cutoff = this run's start, so
     edits made during the run are picked up by the next one).

Never runs EWB compute; the review agent is static-review only (brief says so).
Exit 0 on success (including "nothing changed"), 1 on failure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "notes" / ".ponytail_state.json"
DONE_FILE = REPO / "notes" / ".ponytail_done.txt"
WORKLIST = REPO / "notes" / ".ponytail_worklist.txt"
BRIEF = REPO / "notes" / "briefs" / "ponytail_review.md"
LOGDIR = REPO / "logs" / "ponytail"
# Only these trees/extensions are reviewable code; everything else is data,
# vendored code (third_party/), or agent notes.
CODE_DIRS = ["ewb_live", "dashboard", "ops", "scripts", "tests"]
CODE_EXT = {".py", ".js", ".mjs", ".css", ".html", ".sh"}
SKIP_DIR_PARTS = {"third_party", "node_modules", ".venv", "__pycache__", "data",
                  "logs", "notes", ".git", "dist", "screenshots", ".ipynb_checkpoints"}
WORKLIST_CAP = 60           # keep one hourly review bounded
RUN_DEADLINE_S = 45 * 60    # hard cap per incremental review agent
FULL_SWEEP_DEADLINE_S = 110 * 60  # the daily whole-repo audit gets more room
                                   # (75-min runs timed out on the grown repo;
                                   # ticks while busy queue behind the flock)
ORCA = os.environ.get("ORCA_BIN", shutil.which("orca") or "/home/orca/.local/bin/orca")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(STATE)


def is_reviewable(p: Path) -> bool:
    parts = set(p.parts)
    if parts & SKIP_DIR_PARTS:
        return False
    if p.suffix not in CODE_EXT:
        return False
    return p.parts[0] in CODE_DIRS  # pathlib match('d/**') does not span dirs (ponytail 2026-11-08)


def changed_files(cutoff: datetime) -> list[str]:
    """Files touched after cutoff in reviewable code. Agents in this repo do
    not commit, so "changed since the last run" means mtime, not diff-vs-HEAD:
    a file still dirty but untouched since the last run was already reviewed
    by it (findings live in the owner channels), and the once-a-day full
    sweep re-covers anything the incremental window could miss."""
    found: set[str] = set()
    cutoff_ts = cutoff.timestamp()
    for d in CODE_DIRS:
        root = REPO / d
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(REPO)
            if not is_reviewable(rel):
                continue
            try:
                if p.stat().st_mtime > cutoff_ts:
                    found.add(str(rel))
            except OSError:
                pass
    return sorted(found)


def close_terminal(handle: str) -> None:
    if not handle:
        return
    try:
        subprocess.run([ORCA, "terminal", "close", "--terminal", handle, "--tab", "--json"],
                       cwd=REPO, capture_output=True, text=True, timeout=60)
        print(f"[ponytail] closed previous terminal {handle}")
    except Exception as e:  # noqa: BLE001
        print(f"[ponytail] close {handle} failed (ignored): {e}")


def _find_handle(obj) -> str | None:
    if isinstance(obj, str):
        m = re.search(r"term_[0-9a-zA-Z_-]+", obj)
        return m.group(0) if m else None
    if isinstance(obj, dict):
        for v in obj.values():
            h = _find_handle(v)
            if h:
                return h
    if isinstance(obj, list):
        for v in obj:
            h = _find_handle(v)
            if h:
                return h
    return None


def launch_agent() -> str | None:
    prompt = BRIEF.read_text()
    r = subprocess.run(
        [ORCA, "agent", "launch", "--agent", "pi", "--surface", "terminal",
         "--prompt", prompt, "--json"],
        cwd=REPO, capture_output=True, text=True, timeout=180)
    print(f"[ponytail] launch rc={r.returncode}")
    if r.stdout:
        print(r.stdout.strip()[:2000])
    if r.stderr.strip():
        print(r.stderr.strip()[:1000])
    if r.returncode != 0:
        return None
    try:
        return _find_handle(json.loads(r.stdout))
    except Exception:  # noqa: BLE001
        return _find_handle(r.stdout)


def wait_for_done(handle: str, stamp: str, deadline_s: int) -> str:
    """Finished when the review agent writes notes/.ponytail_done.txt bearing
    this run's stamp (report+channels are by then written), then a short idle
    to let the TUI flush. Idle alone does NOT count: the agent may sit idle
    between internal hand-offs. Otherwise the deadline cap fires."""
    deadline = time.monotonic() + deadline_s
    time.sleep(30)
    while time.monotonic() < deadline:
        try:
            marker = DONE_FILE.read_text().strip() if DONE_FILE.exists() else ""
        except OSError:
            marker = ""
        if stamp in marker:
            subprocess.run([ORCA, "terminal", "wait", "--terminal", handle,
                            "--for", "tui-idle", "--timeout-ms", "30000", "--json"],
                           cwd=REPO, capture_output=True, text=True, timeout=45)
            return "done-marker"
        try:
            r = subprocess.run([ORCA, "terminal", "wait", "--terminal", handle,
                                "--for", "tui-idle", "--timeout-ms", "60000", "--json"],
                               cwd=REPO, capture_output=True, text=True, timeout=75)
            if r.returncode != 0:
                out = (r.stdout + r.stderr).lower()
                if "exit" in out or "not found" in out or "no terminal" in out:
                    return "terminal-gone"
        except Exception:  # noqa: BLE001
            return "terminal-gone"
    return "timeout"


def main() -> int:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    run_log = LOGDIR / (utcnow().strftime("%Y%m%dT%H%M%SZ") + ".log")
    print(f"[ponytail] logging to {run_log}")
    fh = open(run_log, "a", buffering=1)  # line-buffered
    os.dup2(fh.fileno(), 1)
    os.dup2(fh.fileno(), 2)
    for stream in (sys.stdout, sys.stderr):  # flush per line for tailing
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    start = utcnow()
    state = load_state()
    prev_term = state.get("last_terminal") or ""

    # ---- mode + scope ----
    last_sweep = ""
    try:
        last_sweep = (state.get("last_full_sweep_utc") or "")[:10]
    except Exception:  # noqa: BLE001
        pass
    today = start.strftime("%Y-%m-%d")
    full = not state or last_sweep != today
    truncated = False  # incremental worklist hit WORKLIST_CAP: carry over
    files: list[str] = []
    if full:
        scope_lines = ["MODE: FULL SWEEP",
                       "Review the whole repo (ewb_live/, dashboard/, ops/, scripts/, tests/) "
                       "with the ponytail-audit skill. Never read third_party/ or data/."]
        n = -1
    else:
        cutoff_iso = state.get("cutoff_utc") or iso(start)
        try:
            cutoff = datetime.strptime(cutoff_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            cutoff = start
        files = sorted(set(state.get("carry", [])) | set(changed_files(cutoff)))
        n = len(files)
        if not files:
            state.update(cutoff_utc=iso(start), last_run_utc=iso(start), last_result="no-changes")
            save_state(state)
            print(f"[ponytail] {iso(start)} no code changes since {iso(cutoff)}; nothing to review")
            return 0
        scope_lines = ["MODE: INCREMENTAL",
                       f"Review ONLY these files (changed since {iso(cutoff)}) with the "
                       "ponytail-review skill; read connected code as needed:",
                       *files[:WORKLIST_CAP]]
        if n > WORKLIST_CAP:
            scope_lines.append(f"(+{n - WORKLIST_CAP} more; carry over to the next run)")
            truncated = True

    stamp = iso(start)
    scope_lines.insert(0, f"RUN STAMP: {stamp}")
    WORKLIST.write_text("\n".join(scope_lines) + "\n")
    DONE_FILE.unlink(missing_ok=True)  # no stale "done" markers
    print(f"[ponytail] run {stamp} mode={'full' if full else 'incremental'} files={n}")

    # ---- one agent at a time: close the previous run's terminal ----
    close_terminal(prev_term)

    handle = launch_agent()
    if not handle:
        state.update(last_run_utc=iso(start), last_result="launch-failed")
        save_state(state)
        print("[ponytail] launch failed; state left so the next run re-reviews the same scope")
        return 1
    state.update(last_terminal=handle)
    save_state(state)

    status = wait_for_done(handle, stamp,
                           FULL_SWEEP_DEADLINE_S if full else RUN_DEADLINE_S)
    print(f"[ponytail] review agent {handle}: {status}")
    close_terminal(handle)

    if status == "done-marker":
        state.update(last_terminal="", last_run_utc=iso(start), last_result=status)
        if not truncated:  # honor the worklist's carry-over promise (ponytail 2026-11-08)
            state["cutoff_utc"] = iso(start)
            state["carry"] = []
        else:
            state["carry"] = files[WORKLIST_CAP:]  # merge first next run (ponytail 06:24: no tail starvation)
        if full:
            state["last_full_sweep_utc"] = iso(start)
    else:
        # timeout / agent died: outputs may be partial — do NOT advance the
        # cutoff or mark today's sweep done; the next tick re-reviews the scope.
        state.update(last_terminal="", last_run_utc=iso(start), last_result=status)
    save_state(state)
    print(f"[ponytail] done {iso(utcnow())} status={status}")
    return 0 if status in ("done-marker", "terminal-gone") else 1


if __name__ == "__main__":
    sys.exit(main())
