"""Dual-write sink: mirrors the pipeline's results into the versioned results store.

Phase 2 of SPEC §6. The existing ``data/results/*`` files stay the source of truth; this
sink copies what the pipeline already holds into ``stores.results_icechunk`` so the two
can be parity-checked (``tools/results_parity``) before the server switches over.

Every method is best-effort: a store failure is logged and never fails the run. The one
hook in ``pipeline._write_outputs`` calls ``sync`` after every job, so cached events, the
EWB lane and the track lanes (TC/MLC/flood) are all covered without touching each lane;
only events whose content hash changed are committed.

Off switch: ``EWB_LIVE_RESULTS_STORE=0``. Run identity comes from ``EWB_LIVE_RUN_WEEK`` /
``EWB_LIVE_RUN_ID`` (set by ``weekly.run`` so its later publish step joins the same run);
standalone pipeline runs derive both from the clock.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

import pandas as pd

from ewb_live.stores import results_icechunk as rs

logger = logging.getLogger(__name__)

DOCS = ("events_selected.json", "curation.json", "events.json", "global_timeline.json",
        "meta.json", "scorecard.json", "scorecard_by_region.json", "pair_status.json",
        "tc_wind.json")
_MAX_FAILURES = 3
_SCORE_KEYS = ["model", "metric", "lead_time_hours", "init_time"]


def enabled() -> bool:
    return os.environ.get("EWB_LIVE_RESULTS_STORE", "1").strip().lower() not in ("0", "false", "no", "")


def iso_week(now: dt.datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def make_run(window_start: dt.date, window_end: dt.date, now: dt.datetime | None = None) -> rs.Run:
    now = now or dt.datetime.now(dt.UTC)
    window_end = os.environ.get("EWB_LIVE_RUN_WINDOW_END") or window_end    # weekly pins it
    week = os.environ.get("EWB_LIVE_RUN_WEEK") or iso_week(now)
    rule_rev = ""
    with contextlib.suppress(Exception):
        from ewb_live.events.temperature import TRACK_RULE_REV
        rule_rev = TRACK_RULE_REV
    return rs.Run(week=week, run_id=os.environ.get("EWB_LIVE_RUN_ID") or f"{week}-{now:%Y%m%dT%H%M%SZ}",
                  window_start=str(window_start), window_end=str(window_end),
                  rule_rev=rule_rev, code_sha=os.environ.get("EWB_LIVE_CODE_SHA", ""))


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, default=str).encode()


class ResultsSink:
    """Best-effort writer for one weekly run. Build with ``ResultsSink.open``."""

    def __init__(self, repo, run: rs.Run, path: str | None):
        self.repo, self.run, self.path = repo, run, path
        self._lock = threading.Lock()
        self._shas = rs.content_shas(repo, run.branch)
        self._failures = 0
        self._last_sync = 0.0
        self.disabled = False

    @classmethod
    def open(cls, window_start: dt.date, window_end: dt.date, *, path: str | None = None):
        """Sink for this run, or None when disabled / the store cannot be opened."""
        if not enabled():
            return None
        try:
            path = path or rs.default_repo_path()
            with rs.write_lock(path, block=True):
                repo = rs.open_or_create(path)
                run = make_run(window_start, window_end)
                rs.begin_run(repo, run)
            logger.info("results store: run %s on branch %s (%s)", run.run_id, run.branch, path)
            return cls(repo, run, path)
        except Exception as exc:  # noqa: BLE001 — the store must never fail the run
            logger.warning("results store disabled for this run: %s", exc)
            return None

    # ------------------------------------------------------------------ guarded call
    def _guard(self, what: str, fn):
        if self.disabled:
            return None
        try:
            # the single-writer flock is held per operation, not for the process: the
            # weekly runs the pipeline and its own publish step in one process
            with rs.write_lock(self.path, block=True):
                out = fn()
            self._failures = 0
            return out
        except Exception as exc:  # noqa: BLE001
            self._failures += 1
            logger.warning("results store %s failed (%d/%d): %s", what, self._failures,
                           _MAX_FAILURES, exc)
            if self._failures >= _MAX_FAILURES:
                self.disabled = True
                logger.error("results store disabled for the rest of this run")
            return None

    # ----------------------------------------------------------------------- scoring
    def sync(self, events, scores: pd.DataFrame, pair_status: dict, *, force: bool = False,
             min_interval_s: float = 30.0) -> int:
        """Commit every event whose (record, rows, status) changed; returns commits made.

        ``scores`` is the pipeline's full deduped frame; ``pair_status`` the already
        public-sanitised ``{"<event>|<model>": {...}}`` table.
        """
        now = time.monotonic()
        if not force and now - self._last_sync < min_interval_s:
            return 0
        with self._lock:
            self._last_sync = now
            return self._guard("sync", lambda: self._sync(events, scores, pair_status)) or 0

    def _sync(self, events, scores: pd.DataFrame, pair_status: dict) -> int:
        by_event = dict(tuple(scores.groupby("event_id", sort=False))) if len(scores) else {}
        n = 0
        for e in events:
            ed = e.to_dict()
            rows = by_event.get(e.id)
            if rows is None:
                rows = scores.iloc[0:0]
            rows = rows.sort_values(_SCORE_KEYS, kind="stable").reset_index(drop=True)
            status = {k.split("|", 1)[1]: v for k, v in pair_status.items()
                      if k.startswith(e.id + "|")}
            h = hashlib.sha1(_canon([ed, status]))
            if len(rows):
                h.update(pd.util.hash_pandas_object(rows[["event_id", *_SCORE_KEYS, "value"]],
                                                    index=False).values.tobytes())
            sha = h.hexdigest()
            if self._shas.get(e.id, {}).get("sha") == sha:
                continue
            rs.commit_event(self.repo, self.run, ed, rows, status=status, sha=sha)
            self._shas[e.id] = {"sha": sha, "blobs": {}}
            n += 1
        if set(self._shas) - {e.id for e in events}:
            for gone in rs.retain_only(self.repo, self.run, [e.id for e in events]):
                self._shas.pop(gone, None)
        if n:
            logger.info("results store: committed %d events (run %s)", n, self.run.run_id)
        return n

    # ---------------------------------------------------------------------- finishing
    def attach_files(self, results_dir: Path, event_ids) -> dict:
        """Copy per-event viz.json and run-level docs from the results dir (after viz)."""
        def go():
            per = {}
            for eid in event_ids:
                p = Path(results_dir) / "events" / eid / "viz.json"
                if p.is_file():
                    per[eid] = {"viz.json": p.read_bytes()}
            docs = {n: (Path(results_dir) / n).read_bytes() for n in DOCS
                    if (Path(results_dir) / n).is_file()}
            return {"blobs": rs.attach_blobs(self.repo, self.run, per),
                    "docs": rs.put_docs(self.repo, self.run, docs)}
        return self._guard("attach", go) or {}

    def publish(self) -> dict:
        """Prune to the window, tag + fast-forward main, log the maintenance dry run."""
        def go():
            pruned = rs.prune_window(self.repo, self.run)
            tip = rs.publish(self.repo, self.run)
            confirm = os.environ.get("EWB_RESULTS_MAINTENANCE_CONFIRM") == "1"
            rep = rs.maintenance(self.repo, confirm=confirm)
            logger.info("results store: published %s at %s (pruned %d); maintenance %s: %s",
                        self.run.week, tip, len(pruned), "APPLIED" if confirm else "dry run",
                        json.dumps(rep))
            return {"tip": tip, "pruned": pruned, "maintenance": rep,
                    "bytes": rs.repo_bytes(self.path)}
        return self._guard("publish", go) or {}
