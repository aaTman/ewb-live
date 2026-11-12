"""EWB Live pipeline (SPEC section 3).

    python -m ewb_live.pipeline --days 90

Detects events over the rolling window (ERA5/ARCO truth), scores every model
from ``ewb_live.sources`` against each event with EWB's default metrics, and
writes data/results/{events.json, scores.parquet, scores.json,
scorecard.json, meta.json} atomically. Incremental: cached scores for
unchanged (event, model) pairs are reused; only new/changed work is computed.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
import logging
import os
import subprocess
import sys
import threading
import time as _t
from pathlib import Path

import numpy as np
import pandas as pd

from . import regions

logger = logging.getLogger("ewb_live.pipeline")

RESULTS = Path("data/results")

# G-31: keys stripped from the PUBLIC meta.json (served at /data/results/).
PUBLIC_META_DROP = ("perf", "host", "hostname")


def public_meta(meta: dict) -> dict:
    """meta.json payload with private-infrastructure keys removed (G-31)."""
    return {k: v for k, v in meta.items() if k not in PUBLIC_META_DROP}


def _pub_event(e) -> dict:
    """events_selected entry: schema dict + qualified_at (merged-map
    contract; attribute set by qualify_events, kept out of the dataclass
    so fingerprint/scoring caches are unaffected)."""
    return {**e.to_dict(), "qualified_at": getattr(e, "qualified_at", None)}


def public_reason(text) -> str:
    """G-30 (guardian-design 2026-11-08): pair_status.reason is published;
    keep it one line, free of absolute paths/URLs, max 120 chars."""
    import re as _re
    t = _re.sub(r"(https?://\S+|/(?:opt|home|root|var|tmp|mnt|data)\S*)", "",
                str(text)).replace("\n", " ")
    t = _re.sub(r"\s+", " ", t).strip()
    return (t[:117] + "...") if len(t) > 120 else t
PARTS = Path("data/cache/score_parts")
HIGHER_IS_BETTER = {"CriticalSuccessIndex", "Accuracy", "EarlySignal"}
# science-review D6: a signed-error metric's cell mean is signed, so
# lower-is-better on it can score LARGER |error| as positive skill
# (aurora +82% heat duration). Skill suppressed; signed mean stays as `mean`.
SIGNED_ERROR_METRICS = {"DurationMeanError"}
BASELINE = "ifs_hres"
PEAK_WINDOW_METRICS = ("MaximumMeanAbsoluteError",
                       "MaximumLowestMeanAbsoluteError",
                       "MinimumMeanAbsoluteError")
PEAK_METRIC_MAX_LEAD_H = 240  # leads past this compare truncated peak windows
# pair_status contract (ponytail 2026-11-08): DOCUMENTED_ZERO statuses count
# toward completion and (for the terminal two) are never rescheduled;
# read_error is in NEITHER tuple — retriable AND a completion blocker.
# tests/test_pair_status.py pins the vocabulary against these constants, so
# adding "read_error" back to either tuple breaks a test.
DOCUMENTED_ZERO_STATUSES = ("unsupported", "no_data", "bench_failed", "empty")
TERMINAL_NO_RESCHEDULE_STATUSES = ("unsupported", "no_data")

SCORE_COLUMNS = ["event_id", "event_type", "model", "metric", "lead_time_hours", "value", "init_time"]


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # unique tmp: the heartbeat beat thread and the main thread write
    # concurrently, a fixed tmp name lets them interleave (ponytail 2026-11-08)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    with open(tmp, "w") as f:
        write(f)
    os.replace(tmp, path)


def _heartbeat(**fields) -> None:
    """Freshness heartbeat for ``ops/freshness_check.py`` (staleness alarm).

    The 2026-11-07 stall proved that unit-active + growing logs can hide a
    run that publishes nothing. This file is the parent's "I am alive and
    here is where I am" claim, written at every phase transition, after
    every completed job, and every 5 min inside the scoring pool; the
    freshness timer restarts the unit when it goes silent while the unit
    claims to be running.
    """
    try:
        # outside the served tree: data/results/*.json is public, and this
        # file carries pid/phase/run-progress (ponytail MUST 19:15Z, same
        # class as the G-31 perf move)
        _atomic_write(RESULTS.parent / "heartbeat.json", lambda f: json.dump({
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pid": os.getpid(),
            **fields,
        }, f))
    except OSError:  # heartbeat must never break the run
        pass


def _sel_thresholds() -> dict:
    """Effective selection quantiles for the current run (meta record)."""
    from .events.temperature import _mode_clim

    clim = _mode_clim()
    return {
        "heat_wave": {"q_lower": float(clim["heat_wave"]["q_lower"])},
        "freeze": {"q_upper": float(clim["freeze"]["q_upper"])},
        "atmospheric_river": {"q_upper": "p90 ivt (EWB default)"},
    }


def fingerprint(event) -> str:
    """Cheap fingerprint of the parts of an event that change scoring output."""
    return f"{event.start:%Y-%m-%dT%H}|{event.end:%Y-%m-%dT%H}|{[round(b, 2) for b in event.bbox]}"


def _select_event_ids(todo, max_events: int) -> list[str]:
    """Round-robin across event types so a capped validation run stays
    representative (sequential order would be all heat_wave)."""
    by_type: dict[str, dict] = {}
    for e, _m in todo:
        by_type.setdefault(e.event_type, {})[e.id] = e
    pools = [list(v.values()) for _, v in sorted(by_type.items())]
    ordered, seen, i = [], set(), 0
    while any(pools):
        pool = pools[i % len(pools)]
        if pool:
            e = pool.pop(0)
            if e.id not in seen:
                seen.add(e.id)
                ordered.append(e.id)
        i += 1
    return ordered[:max_events]


def _part_path(event, model: str, truth_source: str) -> Path:
    """Part files are keyed by (event, model, fingerprint, truth) so stale
    cache entries from an earlier window/truth can never be reused."""
    key = f"{fingerprint(event)}|{truth_source}"
    tag = hashlib.sha1(key.encode()).hexdigest()[:12]
    return PARTS / f"{event.id}_{model}_{tag}.parquet"


def _run_worker(jobfile: Path, logfile: Path, timeout: float) -> None:
    """Spawn one job subprocess; raise RuntimeError on nonzero exit/timeout.

    Per-job wall-clock ceiling (stall postmortem 2026-11-07): a wedged
    socket or memory bomb must not hold a lane for days. The default is
    deliberately generous — legitimate AR jobs measured 1.1-4.9 h at
    jobs=1 — it targets runaways, not slow work. On timeout the worker is
    killed and the job takes the normal failure path: ONE serial retry
    after the pool, then documented.
    """
    with open(logfile, "wb") as lf:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "ewb_live.pipeline", "--_run-job", str(jobfile)],
                stdout=lf, stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"worker timed out after {timeout:.0f}s "
                f"(EWB_LIVE_JOB_TIMEOUT_S)") from None
    if proc.returncode != 0:
        tail = " | ".join(logfile.read_text(errors="replace").strip().splitlines()[-6:])
        raise RuntimeError(f"worker exit {proc.returncode}: {tail}")


# ---------------------------------------------------------------- workers ----

def _score_job(event_dict: dict, models: list[str], truth_source: str,
               status_out: dict | None = None):
    """Worker: score one event on all pending models in one process.

    Building the EWB target (truth load + case masks) is the expensive part
    and is identical across models, so one score_event call covers all of
    them. Returns (event_id, df|None) with df spanning models (model column).
    ``status_out`` receives score_event's per-model outcome table.
    """
    import logging as _logging
    import sys as _sys

    # spawn children don't inherit the parent's logging config
    if not _logging.getLogger().handlers:
        _logging.basicConfig(
            level=_logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[_logging.StreamHandler(_sys.stderr)],
        )
        _logging.getLogger("extremeweatherbench").setLevel(_logging.WARNING)
    from ewb_live.events.types import Event
    from ewb_live.scoring.evaluate import score_event

    import dask
    # intra-job dask threads also halve the transient peak of EWB's dense
    # IVT reindex (measured ~60 GB AR-job anon at threads=2); threads=1
    # roughly halves it at a small bench-speed cost. Env-tunable for
    # memory-tight shared hosts.
    dask.config.set(scheduler="threads",
                    num_workers=int(os.environ.get("EWB_LIVE_DASK_THREADS", "2")))
    event = Event.from_dict(event_dict)
    try:
        df = score_event(event, models, truth_source=truth_source,
                         status_out=status_out)
    except Exception as exc:  # noqa: BLE001
        logger.error("score_event(%s, %s) failed: %s", event.id, models, exc)
        if status_out is not None:
            for m in models:
                status_out.setdefault(
                    m, {"status": "failed", "reason": f"score_event raised: {exc}", "rows": 0})
        return event.id, None
    if df.empty:
        return event.id, None
    return event.id, df


# ------------------------------------------------------------- perf monitor ---

class _PeakRss:
    """Sample total RSS of this process + descendants every few seconds;
    track the peak. Workers each score one event; the monitor is what the
    'stay under ~48 GB' constraint is audited against (meta.perf)."""

    def __init__(self, interval: float = 10.0):
        self.interval = interval
        self.peak_bytes = 0
        self._stop = None
        self._thread = None

    def _sample_once(self) -> int:
        import psutil

        me = psutil.Process()
        total = me.memory_info().rss
        for c in me.children(recursive=True):
            try:
                total += c.memory_info().rss
            except psutil.Error:  # noqa: PERF203 - process vanished mid-scan
                pass
        return total

    def __enter__(self):
        import threading

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        import time

        while not self._stop.wait(self.interval):
            try:
                self.peak_bytes = max(self.peak_bytes, self._sample_once())
            except Exception:  # noqa: BLE001 - monitor must never break the run
                pass

    def __exit__(self, *exc):
        if self._stop:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self.peak_bytes = max(self.peak_bytes, self._sample_once())
        except Exception:  # noqa: BLE001
            pass
        return False


# ------------------------------------------------------------- aggregation ---

def build_scorecard(df: pd.DataFrame) -> dict:
    """Aggregate per event_type x metric x lead_time x model + skill vs ifs_hres.

    skill_vs_ifs_pct is like-for-like (science-review D2, 2026-11-08): both
    means are taken over the INTERSECTION of events the two models share in
    that (type, metric, lead) cell, so a model is never scored against a
    baseline computed on an easier event set. Rows carry skill_n_common.

    ci95_lo/ci95_hi (science-review D3): event-bootstrap 95% CI over the cell
    mean — resample the cell's events with replacement (B=400, fixed seed),
    weighting by each event's sample count so the CI brackets the same
    estimator as `mean`. n_events >= 3 required, else null.
    """
    if df.empty:
        return {"rows": []}
    d = df.copy()
    # sentinel for NaN leads: NaN dict keys never compare equal, and cells
    # are looked up by exact tuple key below
    d["lead_time_hours"] = d["lead_time_hours"].astype("Float64").astype("float64").fillna(-1.0)
    keys = ["event_type", "metric", "lead_time_hours", "model"]
    grp = (
        d.groupby(keys, dropna=False)["value"]
        .agg(mean="mean", n="count")
        .reset_index()
    )
    n_events = (
        d.groupby(keys, dropna=False)["event_id"].nunique().rename("n_events").reset_index()
    )
    grp = grp.merge(n_events, on=keys)

    # per-cell per-event (sum, count) for the bootstrap and the common-subset skill
    per_ev = (
        d.groupby(keys + ["event_id"], dropna=False)["value"]
        .agg(s="sum", n="size").reset_index()
    )
    ev_by_cell = {k: g for k, g in per_ev.groupby(keys, dropna=False, sort=False)}
    base_ev = {
        k[0:3]: g
        for k, g in ev_by_cell.items() if k[3] == BASELINE
    }
    B = 400

    def cell_ci(g: pd.DataFrame, cell_key):
        if len(g) < 3:
            return None, None
        # per-cell deterministic seed: a shared RNG makes each cell's CI
        # depend on iteration order, so universe churn shifts unrelated
        # cells' published CIs (ponytail 17:17Z); hashlib not hash():
        # PYTHONHASHSEED would make even that unstable across runs
        seed = int(hashlib.sha1(
            "|".join(str(x) for x in cell_key).encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, len(g), size=(B, len(g)))
        s = g["s"].to_numpy()[idx].sum(axis=1)
        n = g["n"].to_numpy()[idx].sum(axis=1)
        lo, hi = np.percentile(s / n, [2.5, 97.5])
        return round(float(lo), 4), round(float(hi), 4)

    rows = []
    for r in grp.itertuples():
        cell_key = (r.event_type, r.metric, r.lead_time_hours, r.model)
        g = ev_by_cell[cell_key]
        ci_lo, ci_hi = cell_ci(g, cell_key)
        skill = None
        n_common = 0
        b_g = base_ev.get(cell_key[0:3])
        if b_g is not None and r.model != BASELINE \
                and r.metric not in SIGNED_ERROR_METRICS:
            common = g["event_id"].isin(set(b_g["event_id"]))
            n_common = int(common.sum())
            b_common = b_g[b_g["event_id"].isin(set(g["event_id"]))]
            bsum, bn = float(b_common["s"].sum()), float(b_common["n"].sum())
            msum, mn = float(g.loc[common, "s"].sum()), float(g.loc[common, "n"].sum())
            b = bsum / bn if bn > 0 else None
            m = msum / mn if mn > 0 else None
            if b is not None and m is not None and abs(b) > 1e-9:
                rel = (m - b) / abs(b) * 100.0
                if r.metric not in HIGHER_IS_BETTER:
                    rel = -rel
                skill = round(float(rel), 2)
        elif r.model == BASELINE:
            n_common = int(len(g))
            # contract kept for the dashboard (baseline vs itself), except
            # signed-error metrics where all skill is suppressed (D6)
            skill = None if r.metric in SIGNED_ERROR_METRICS else 0.0
        rows.append(
            {
                "event_type": r.event_type,
                "metric": r.metric,
                "lead_time_hours": None if r.lead_time_hours == -1.0 else float(r.lead_time_hours),
                "model": r.model,
                "mean": round(float(r.mean), 4) if pd.notna(r.mean) else None,
                "n_events": int(r.n_events),
                "n_samples": int(r.n),
                "ci95_lo": ci_lo,
                "ci95_hi": ci_hi,
                "skill_vs_ifs_pct": skill,
                "skill_n_common": n_common or None,
            }
        )
    return {"baseline": BASELINE, "rows": rows}


# ------------------------------------------------------------------ main ----

def _clim_meta() -> dict:
    """meta.json climatology provenance (finders' thresholds/source)."""
    try:
        from ewb_live.events.temperature import climatology_meta

        return climatology_meta()
    except Exception as exc:  # noqa: BLE001 — meta must not fail publish
        return {"source": "unknown", "note": f"climatology meta failed: {exc}"}


def _prune_pair_status(pair_status: dict, events, models) -> dict:
    """Drop pair_status keys outside the live (event, model) universe."""
    keep = {f"{e.id}|{m}" for e in events for m in models}
    return {k: v for k, v in pair_status.items() if k in keep}


def _sweep_orphan_temps(results: Path, max_age_s: float = 3600.0) -> int:
    """Unlink orphaned _atomic_write temps from killed writers; age-guarded
    so a concurrent run's live tmp survives (ponytail 2026-11-08)."""
    n = 0
    for p in list(results.glob("*.tmp.*")) + \
             list(results.glob("pair_sidecar/*.tmp.*")):
        try:
            if _t.time() - p.stat().st_mtime > max_age_s:
                p.unlink(missing_ok=True)
                n += 1
        except OSError:
            pass
    return n


def _record_blocker_sig(path: Path, sig: str) -> "str | None":
    """Write the current blocker signature, return the previous one.
    Exit-0 when the signature is unchanged stops Restart=on-failure from
    re-entering run() forever on a permanent blocker (ponytail 2026-11-08)."""
    prev = path.read_text() if path.exists() else None
    path.write_text(sig)
    return prev


def _merge_orphan_parts(cached, done, events, models, truth_by_event):
    """Rescue part files whose interim publish failed (publish-only path;
    parts are fingerprint+truth-keyed so stale rows can't leak)."""
    frames = []
    for e in events:
        for m in models:
            if (e.id, m) in done:
                continue
            p = _part_path(e, m, truth_by_event[e.id])
            if p.exists():
                try:
                    frames.append(pd.read_parquet(p))
                    done.add((e.id, m))
                except Exception as exc:  # noqa: BLE001 — torn part -> rescore
                    logger.warning("orphan part %s unreadable: %s", p.name, exc)
    return pd.concat([cached] + frames, ignore_index=True) if frames else cached


def drop_truncated_peak_rows(scores: "pd.DataFrame") -> "pd.DataFrame":
    """Peak-in-window metrics at leads whose valid window runs past the
    verification horizon compare against truncated truth: model-independent
    ~8 K MAE spikes at lead>240 h (guardian-design 2026-11-08, D4 family).
    Dropped at every publish; raw rows stay in the box-side part files."""
    bad = (scores["metric"].isin(PEAK_WINDOW_METRICS)
           & (scores["lead_time_hours"] > PEAK_METRIC_MAX_LEAD_H))
    return scores[~bad] if bad.any() else scores


def run(
    days: int = 90,
    models: list[str] | None = None,
    jobs: int = 4,
    end: dt.date | None = None,
    force: bool = False,
    events_only: bool = False,
    max_events: int | None = None,
    top_n: int | None = 15,
    publish_only: bool = False,
) -> int:
    from ewb_live.events import detect_events
    from ewb_live.scoring.truth import probe_arco_cutoff, truth_source_for_event
    from ewb_live import sources

    RESULTS.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)

    end = end or dt.date.today()
    start = end - dt.timedelta(days=days)
    cutoff = probe_arco_cutoff()
    logger.info("window %s..%s | ARCO truth cutoff %s", start, end, cutoff)
    _heartbeat(phase="start", days=days, start=str(start), end=str(end),
               events_only=events_only)

    if publish_only:
        force = False  # publish-only never scores; --force would only wipe the published outputs (ponytail 2026-11-08)
        # Publish-now path (orchestrator 2026-11-07, stall postmortem):
        # merge cached part files against the CURRENTLY published event
        # set and rewrite scores/scorecard/meta without detection or
        # scoring. Lets ops un-stick a stale dashboard in <1 min.
        from .events.types import Event as _Event
        if not (RESULTS / "events_selected.json").exists():
            logger.error("publish-only: no events_selected.json — run detection first")
            return 1
        events = [_Event.from_dict(e) for e in
                  json.loads((RESULTS / "events_selected.json").read_text())]
        all_events = events
        logger.info("publish-only: %d published events; merging cached parts",
                    len(events))
    else:
        # 0. local ERA5 truth mirror: EWB targets read this instead of streaming
        #    the global hourly ARCO zarr from GCS (I/O-bound scoring fix).
        if not events_only:
            try:
                from ewb_live.scoring.truth_mirror import ensure_mirror

                ensure_mirror(
                    end=dt.datetime.combine(end, dt.time()), days=days,
                    lookback_days=14, cutoff=cutoff,
                    threads=int(os.environ.get("EWB_LIVE_MIRROR_THREADS", "24")),
                )
            except Exception as exc:  # noqa: BLE001 - mirror is an optimization, not a dependency
                logger.warning("truth mirror update failed (falling back to remote ARCO): %s", exc)

        # 1. detect: full catalogue to events.json; curated subset drives scoring
        all_events = detect_events(start, end, top_n=None, force=force)
        _atomic_write(
            RESULTS / "events.json",
            lambda f: json.dump([e.to_dict() for e in all_events], f, indent=1),
        )
        events = detect_events(start, end, top_n=top_n)
        # 1b. PUBLISH GATE (Weather Explorer pattern, design channel 2026-11-06):
        # refuse any curated event whose claims (title dates, value ranges,
        # cross-field consistency) don't hold — it must not reach
        # events_selected, scoring, or weekly output. Rejections stay visible in
        # events.json (full catalogue) and the log, AND land in curation.json
        # (science-review 2026-11-09: 3 rejections had no artifact paper trail,
        # 15->12 invisible to funnel replays).
        from ewb_live.events.validate import validate_event

        kept: list = []
        gate_rejects: list[dict] = []
        for e in events:
            probs = validate_event(e)
            if probs:
                for p in probs:
                    logger.error("publish gate REJECTS event %s (%s): %s",
                                 e.id, e.event_type, p)
                    gate_rejects.append({"stage": "publish_gate", "id": e.id,
                                         "event_type": e.event_type,
                                         "problem": p})
            else:
                kept.append(e)
        if len(kept) != len(events):
            logger.error("publish gate: %d/%d selected events rejected",
                         len(events) - len(kept), len(events))
        events = kept
        # curation decisions (chain merges, cross-type drops, publish-gate
        # rejects) as an auditable artifact next to the selection (owner rule
        # 2026-11-06; gate entries appended 2026-11-09)
        from ewb_live.events import select as _select

        _atomic_write(
            RESULTS / "curation.json",
            lambda f: json.dump(_select.LAST_DECISIONS + gate_rejects, f,
                                indent=1),
        )
        # merged-global-map contract (owner 2026-11-08, briefs/
        # merged_global_map.md): first 6-hourly step the event met its
        # detection criteria; plain attribute, outside fingerprint
        from ewb_live.events.qualification import qualify_events
        qualify_events(events)
        _atomic_write(
            RESULTS / "events_selected.json",
            lambda f: json.dump([_pub_event(e) for e in events], f, indent=1),
        )
        # science-review actionable (2026-11-07): event ids churn across
        # selection rebuilds, which broke external cross-ref mappings. Keep a
        # run-stamped, immutable snapshot per run + a stable id ledger so
        # cross-refs can be joined by (run, id) and old ids stay resolvable.
        runstamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapdir = RESULTS / "events_runs"
        snapdir.mkdir(exist_ok=True)
        _sweep_orphan_temps(snapdir)  # tmp names here are unique; nothing else reclaims them
        _atomic_write(
            snapdir / f"events_selected_{runstamp}.json",
            lambda f: json.dump({"run_id": runstamp,
                                 "events": [_pub_event(e) for e in events]}, f, indent=1),
        )
        ledger_p = snapdir / "ledger.json"
        ledger = json.loads(ledger_p.read_text()) if ledger_p.exists() else {"runs": []}
        ledger["runs"] = (ledger["runs"] + [{"run_id": runstamp,
                                             "ids": [e.id for e in events]}])[-40:]
        _atomic_write(ledger_p, lambda f: json.dump(ledger, f))
        # snapshots trim to the same 40-run window as the ledger (ponytail 2026-11-08)
        for p in sorted(snapdir.glob("events_selected_*.json"))[:-40]:
            p.unlink(missing_ok=True)
        logger.info("detected %d events (%d selected for scoring)", len(all_events), len(events))
        if events_only:
            return 0

    if models is None:
        models = sources.list_models()
    if BASELINE not in models:
        logger.warning("baseline model %s not in sources; scorecard skill will be null", BASELINE)

    # 2. incremental bookkeeping
    scores_path = RESULTS / "scores.parquet"
    old = pd.read_parquet(scores_path) if scores_path.exists() and not force else pd.DataFrame(
        columns=SCORE_COLUMNS + ["fingerprint", "truth_source"]
    )
    if "fingerprint" not in old.columns:  # older schema -> rebuild
        old = old.assign(fingerprint=None, truth_source=None)
    fp_by_event = {e.id: fingerprint(e) for e in events}
    truth_by_event = {e.id: truth_source_for_event(e, cutoff) for e in events}
    valid_ids = set(fp_by_event)
    keep = (
        old["event_id"].isin(valid_ids)
        & (old["fingerprint"] == old["event_id"].map(fp_by_event))
    )
    if "truth_source" in old.columns:
        # cached scores stay valid only while the truth policy is unchanged
        # (both directions: ARCO lost/gained coverage)
        keep &= old["truth_source"] == old["event_id"].map(lambda i: truth_by_event.get(i))
    cached = old[keep]
    done = set(zip(cached["event_id"], cached["model"]))
    logger.info("cached scores: %d rows; done pairs: %d", len(cached), len(done))

    todo = []
    for e in events:
        pending = [m for m in models if (e.id, m) not in done]
        if pending:
            todo.append((e, pending))
    if max_events:
        ids = _select_event_ids(todo, max_events)
        todo = [(e, ms) for e, ms in todo if e.id in ids]
    if publish_only:
        # honor the docstring promise: re-merge parts whose interim publish
        # failed (ponytail 2026-11-08 NICE)
        cached = _merge_orphan_parts(cached, done, events, models, truth_by_event)
        todo = []
    logger.info(
        "to score: %d event jobs (%d event-model pairs)",
        len(todo), sum(len(ms) for _, ms in todo),
    )

    # 3. score (process pool; each job writes its part file when done)
    new_frames = [cached]
    perf_stats: dict = {}
    # Per (event, model) outcome table, persisted so documented
    # zero-row pairs (e.g. models with no 2 m temperature on heat/freeze)
    # survive restarts and make full_run_complete decidable.
    pair_status_path = RESULTS / "pair_status.json"
    # --force = "ignore cached scores" also means ignore stale terminal
    # pair_status entries, or wrongly-documented pairs stay missing forever
    # while full_run_complete still goes true (ponytail 2026-11-08; the
    # documented status survives restarts only because of this fold-in)
    pair_status: dict = (
        {} if force else
        json.loads(pair_status_path.read_text()) if pair_status_path.exists() else {}
    )
    # fold in sidecars from jobs that were killed mid-flight (their parent
    # never got to merge them); a worker writes its full outcome table
    # atomically before exiting, so these are trustworthy
    def _sc_mtime(p: Path) -> float:
        try:  # race-guard: a concurrent publish-only/scrub can unlink mid-sort
            return p.stat().st_mtime
        except OSError:  # vanished -> fold-in read will fail-soft too
            return 0.0

    for sc in ([] if force else
               sorted((RESULTS / "pair_sidecar").glob("*.status.json"),
                      key=_sc_mtime)):  # newest sidecar wins across chunk tags
        try:
            eid = sc.name.removesuffix(".status.json")
            eid = re.sub(r"_c\d+$", "", eid)  # model-chunk tag, not part of id
            for model, st in json.loads(sc.read_text()).items():
                pair_status[f"{eid}|{model}"] = st
        except Exception:  # noqa: BLE001
            pass

    # prune to the live universe (ids churn across selection rebuilds) and
    # sweep orphaned atomic-write temps from SIGKILLed writers
    pair_status = _prune_pair_status(pair_status, events, models)
    # sidecars of churned-out events are dead weight: every run re-parses
    # every file ever written (ponytail 2026-11-08 SHOULD: unbounded growth)
    if not force:
        live_ids = {e.id for e in events}
        for sc in (RESULTS / "pair_sidecar").glob("*.status.json"):
            base = re.sub(r"_c\d+$", "", sc.name.removesuffix(".status.json"))
            if base not in live_ids:
                try:
                    sc.unlink(missing_ok=True)
                except OSError:  # concurrent unlink is fine
                    pass
    _sweep_orphan_temps(RESULTS)

    def _persist_pair_status() -> None:
        # process-unique tmp: ops publish-only can run while the unit scores
        pub = {k: ({**v, "reason": public_reason(v["reason"])}
                   if isinstance(v, dict) and v.get("reason") else v)
               for k, v in pair_status.items()}
        tmp = pair_status_path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(pub))
        tmp.replace(pair_status_path)

    def _pair(eid: str, model: str) -> dict:
        return pair_status.get(f"{eid}|{model}", {})

    failed_after_retry: set[str] = set()
    last_sig = ""  # blocker sig from the most recent _write_outputs (untruncated)

    def _write_outputs(frames: list) -> int:
        """Concat + (re)write scores/scorecard/meta atomically. Called
        periodically during long runs so the dashboard updates progressively."""
        nonlocal last_sig
        scores = (
            pd.concat([f for f in frames if len(f)], ignore_index=True)
            if any(len(f) for f in frames)
            else pd.DataFrame(columns=SCORE_COLUMNS + ["fingerprint", "truth_source"])
        )
        scores = scores.drop_duplicates(subset=SCORE_COLUMNS + ["fingerprint"], keep="last")
        scores = drop_truncated_peak_rows(scores)
        # atomic like every other results write: a kill mid-write here
        # truncated the parquet and crash-looped the next run (ponytail 2026-11-08)
        tmp_par = scores_path.with_suffix(f".parquet.tmp.{os.getpid()}")
        scores.to_parquet(tmp_par)
        os.replace(tmp_par, scores_path)
        contract = scores[SCORE_COLUMNS].copy()
        contract["init_time"] = contract["init_time"].astype("datetime64[ns]")
        _atomic_write(
            RESULTS / "scores.json",
            lambda f: json.dump(
                json.loads(contract.to_json(orient="records", date_format="iso", date_unit="s")),
                f,
            ),
        )
        scorecard = build_scorecard(scores[SCORE_COLUMNS])
        _atomic_write(RESULTS / "scorecard.json", lambda f: json.dump(scorecard, f, indent=1))
        _atomic_write(RESULTS / "scorecard_by_region.json", lambda f: json.dump(
            regions.build_region_scorecards(
                events, scores[SCORE_COLUMNS],
                footprints=regions.load_footprints(RESULTS, [e.id for e in events])),
            f, indent=1))
        expected_pairs = {(e.id, m) for e in events for m in models}
        scored_pairs = {(r0, r1) for r0, r1 in zip(scores["event_id"], scores["model"])} \
            if len(scores) else set()
        blockers = []          # pairs neither scored nor documented
        documented = 0
        for eid, m in sorted(expected_pairs):
            if (eid, m) in scored_pairs:
                continue
            st = _pair(eid, m)
            # documented only counts for the fp it was recorded against;
            # unstamped/legacy entries rescore once and re-stamp (self-heal)
            if (st.get("status") in DOCUMENTED_ZERO_STATUSES
                    and st.get("fp") == f"{fp_by_event[eid]}|{truth_by_event[eid]}"):
                documented += 1
            else:
                blockers.append(f"{eid}|{m}" + (f" ({st.get('status')})" if st else ""))
        complete = not blockers and not failed_after_retry
        # untruncated sig for run()'s restart-loop check (ponytail 2026-11-08)
        last_sig = json.dumps([sorted(blockers), sorted(failed_after_retry)])
        meta = {
            "last_updated": dt.datetime.now(dt.timezone.utc).isoformat(),
            "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "models": models,
            "truth_source": {
                "primary": "era5_arco",
                "arco_cutoff": cutoff.isoformat(),
                "fallback": "ifs_hres_analysis",
                "note": "ARCO lags ~6 days; events ending after the cutoff are scored against IFS HRES 0-h analysis",
            },
            "climatology": _clim_meta(),
            # owner decision 2026-11-08 (G-40): documented deviation — the AR
            # lane keeps the upstream EWB mask filters verbatim and ADDS an
            # IBTrACS tropical-cyclone colocation screen at candidate stage.
            # The raw exceedance map remains the raw upstream mask.
            "detection_deviation": {
                "ar_tc_screen": {
                    "rule": "drop AR candidates with an IBTrACS TC track "
                            "center within radius_km of the AR mask on the "
                            "nearest segment day, within hours of the window",
                    "radius_km": float(os.environ.get(
                        "EWB_LIVE_TC_SCREEN_KM", "250.0")),
                    "hours": float(os.environ.get(
                        "EWB_LIVE_TC_SCREEN_HOURS", "24.0")),
                    "source": "IBTrACS v04r01 via EWB inputs.IBTrACS URI/mapping",
                },
            },
            "n_events": len(events),
            # owner decision 2026-11-07: thresholds as effective quantiles +
            # per-type selected counts (freeze stays owner-spec p05; an
            # empty freeze lane is a VALID result, not a gap to hide)
            "selection_thresholds": _sel_thresholds(),
            "event_counts": {
                et: sum(1 for e in events if e.event_type == et)
                for et in ("heat_wave", "freeze", "atmospheric_river")
            },
            "n_score_rows": len(scores),
            "n_scored_pairs": int(len({(r[0], r[1]) for r in zip(scores['event_id'], scores['model'])})),
            "pair_coverage": {
                "expected": len(expected_pairs),
                "scored": len(expected_pairs & scored_pairs),
                "documented_zero_rows": documented,
                "unattempted_or_failed": blockers[:50],
                "failed_jobs_after_retry": sorted(failed_after_retry),
            },
            # design's REVIEW-READY semantics: every event x model pair is
            # scored OR attempted with a documented zero-row reason (see
            # pair_status.json), and no job failed after its serial retry.
            "full_run_complete": bool(complete),
            "perf": perf_stats,
        }
        # G-31 (guardian-design 2026-11-08, owner public-hygiene rule):
        # meta.json is served publicly; perf/host telemetry (peak RSS, etc.)
        # describes the box and must never be published. It goes to
        # data/perf.json, outside the served tree.
        _atomic_write(RESULTS / "meta.json",
                      lambda f: json.dump(public_meta(meta), f, indent=1))
        _atomic_write(RESULTS.parent / "perf.json",
                      lambda f: json.dump({"perf": perf_stats}, f, indent=1))
        return len(scores)

    # Publish-on-start (stall postmortem 2026-11-07): make cached scores and
    # existing part files visible the moment bookkeeping completes, instead of
    # hiding them behind the next job completion (detection alone takes ~20
    # min; the dashboard should not be stale that long after a restart).
    # Also the terminal publish for publish_only runs (todo forced empty).
    try:
        _n0 = _write_outputs(new_frames)
        logger.info("publish-on-start: %d rows visible", _n0)
        _heartbeat(phase="publish_on_start", published_rows=_n0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish-on-start failed: %s", exc)

    if todo:
        # Each event job runs in its own SUBPROCESS (not a shared pool): an
        # OOM-killed worker used to poison ProcessPoolExecutor
        # (BrokenProcessPool) and lose the whole run. Per-job isolation +
        # one serial retry keeps partial results safe.
        #
        # AR jobs are memory-class different: the AR metric cube EWB
        # materializes per model-bench is dense over (lead x valid_time x
        # init) — measured ~28 GB per AR job even with one model in flight
        # (py-spy 2026-11-06: utils.convert_init_time_to_valid_time
        # union-reindex). Heat/freeze jobs (2mT only) peak ~8-13 GB. So AR
        # events run on their own lane with a lower concurrency
        # (EWB_LIVE_AR_JOBS, default 2), everything else uses --jobs.
        import tempfile

        ar_jobs = int(os.environ.get("EWB_LIVE_AR_JOBS", "2"))
        lanes = {"ar": [t for t in todo if t[0].event_type == "atmospheric_river"],
                 "std": [t for t in todo if t[0].event_type != "atmospheric_river"]}
        lane_workers = {"ar": max(1, min(ar_jobs, len(lanes["ar"]))),
                        "std": max(1, min(jobs, len(lanes["std"])))}

        # reap per-run tmpdirs orphaned by memguard SIGKILLs (ponytail 2026-11-08)
        import shutil
        for _td in Path(tempfile.gettempdir()).glob("ewb_jobs_*"):
            try:
                if _t.time() - _td.stat().st_mtime > 86400:
                    shutil.rmtree(_td, ignore_errors=True)
            except OSError:
                pass
        tmpdir = Path(tempfile.mkdtemp(prefix="ewb_jobs_"))
        t_pool = _t.monotonic()

        def _spawn(e, ms, truth, tag=""):
            jobfile = tmpdir / f"{e.id}{tag}.json"
            out = tmpdir / f"{e.id}{tag}.parquet"
            logfile = tmpdir / f"{e.id}{tag}.log"
            jobfile.write_text(json.dumps({"event": e.to_dict(), "models": ms,
                                           "truth": truth, "out": str(out),
                                           "sidecar": str(RESULTS / "pair_sidecar" /
                                                          f"{e.id}{tag}.status.json")}))
            # per-job wall-clock ceiling via EWB_LIVE_JOB_TIMEOUT_S (see
            # _run_worker); generous default, targets runaways not slow work
            job_timeout = float(os.environ.get("EWB_LIVE_JOB_TIMEOUT_S", "21600"))
            _run_worker(jobfile, logfile, job_timeout)
            return out

        job_secs: list[float] = []
        failed: list[tuple] = []

        def _collect(e, truth, out, tag=""):
            # merge the worker's per-model outcome table first (every model
            # is represented: scored OR documented zero-row)
            status_file = RESULTS / "pair_sidecar" / f"{e.id}{tag}.status.json"
            if status_file.exists():
                try:
                    for model, st in json.loads(status_file.read_text()).items():
                        st["fp"] = f"{fingerprint(e)}|{truth}"  # freshly proven this fp
                        pair_status[f"{e.id}|{model}"] = st
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pair status merge failed for %s: %s", e.id, exc)
            if out is None or not Path(out).exists():
                logger.warning("no rows for event %s", e.id)
                return
            df = pd.read_parquet(out)
            for model, sub in df.groupby("model"):
                model = str(model)
                sub = sub.copy()
                sub["fingerprint"] = fingerprint(e)
                sub["truth_source"] = truth
                _part_path(e, model, truth).parent.mkdir(parents=True, exist_ok=True)
                # atomic: a SIGTERM mid-write used to leave a truncated part
                # that crash-looped every later run (ponytail 2026-11-08)
                tmp_par = _part_path(e, model, truth).with_suffix(".parquet.tmp")
                sub.to_parquet(tmp_par)
                os.replace(tmp_par, _part_path(e, model, truth))
                new_frames.append(sub)
                logger.info("scored (%s, %s): %d rows", e.id, model, len(sub))

        with _PeakRss() as perf:
            futs: dict = {}
            with cf.ThreadPoolExecutor(max_workers=lane_workers["ar"]) as ex_ar, \
                 cf.ThreadPoolExecutor(max_workers=lane_workers["std"]) as ex_std:
                mchunk = int(os.environ.get("EWB_LIVE_MODEL_CHUNK", "0") or 0)
                skip_ids = {s.strip() for s in
                            os.environ.get("EWB_LIVE_SKIP_EVENTS", "").split(",")
                            if s.strip()}
                for e, ms in todo:
                    truth = truth_by_event[e.id]
                    if e.id in skip_ids:
                        # documented exclusion (counts as documented zero-row
                        # in pair_coverage / full_run_complete)
                        for m in ms:
                            key = f"{e.id}|{m}"
                            if not _part_path(e, m, truth).exists():
                                pair_status[key] = {
                                    "status": "bench_failed", "rows": 0,
                                    "fp": f"{fingerprint(e)}|{truth}",
                                    "reason": "excluded (oversized AR case: "
                                              "EWB 3D-mask crash)"}
                        logger.info("event %s excluded by EWB_LIVE_SKIP_EVENTS", e.id)
                        continue
                    # terminal documented pairs (no 2 m temperature, no
                    # forecast data) are not rescheduled on restart
                    missing = [m for m in ms
                               if not _part_path(e, m, truth).exists()
                               and not (_pair(e.id, m).get("status")
                                         in TERMINAL_NO_RESCHEDULE_STATUSES
                                         and _pair(e.id, m).get("fp")
                                         == f"{fingerprint(e)}|{truth}")]
                    for m in ms:
                        if _part_path(e, m, truth).exists():
                            new_frames.append(pd.read_parquet(_part_path(e, m, truth)))
                    if missing:
                        # optional model-chunking: caps per-job peak memory
                        # (AR densify is ~linear in models-in-flight) and
                        # multiplies effective lane count; costs one extra
                        # target build per extra chunk
                        ex = ex_ar if e.event_type == "atmospheric_river" else ex_std
                        chunks = ([missing[i:i + mchunk]
                                   for i in range(0, len(missing), mchunk)]
                                  if mchunk and len(missing) > mchunk else [missing])
                        for ci, chunk in enumerate(chunks):
                            tag = f"_c{ci}" if len(chunks) > 1 else ""
                            futs[ex.submit(_spawn, e, chunk, truth, tag)] = (
                                e, chunk, truth, _t.monotonic(), tag)
                # keep-alive beat so the freshness alarm can distinguish a
                # long-but-alive job from a wedged parent (postmortem fix #2)
                _hb_stop = threading.Event()

                def _hb_beat() -> None:
                    while not _hb_stop.wait(300):
                        _heartbeat(phase="score", jobs_done=len(job_secs),
                                   jobs_total=len(futs), beat=True)

                threading.Thread(target=_hb_beat, daemon=True).start()
                for fut in cf.as_completed(futs):
                    e, ms, truth, t0, tag = futs[fut]
                    try:
                        out = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("job (%s%s) failed: %s", e.id, tag, exc)
                        failed.append((e, ms, truth, str(exc), tag))
                        continue
                    job_secs.append(round(_t.monotonic() - t0, 1))
                    peak = ""
                    try:
                        tail = (tmpdir / f"{e.id}{tag}.log").read_text(errors="replace")
                        rss_line = [ln for ln in tail.splitlines() if "peak_rss_mb=" in ln]
                        if rss_line:
                            peak = " peak_rss=" + rss_line[-1].split("peak_rss_mb=")[1].strip() + "MB"
                    except OSError:
                        pass
                    logger.info("event job %s%s took %.1fs%s", e.id, tag, job_secs[-1], peak)
                    _collect(e, truth, out, tag)
                    _persist_pair_status()
                    # PUBLISH AFTER EVERY JOB (stall postmortem 2026-11-07):
                    # the old gate was `len(new_frames) % 10 == 0`, but
                    # new_frames grows by the 9-11 per-model frames a job
                    # collects AT ONCE from an arbitrary offset, so it almost
                    # never aligned and results froze for 23 h in a healthy
                    # run. The merge is sub-second at this row count.
                    n_pub = -1
                    try:
                        n_pub = _write_outputs(new_frames)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("interim output write failed: %s", exc)
                    _heartbeat(phase="score", event=e.id,
                               jobs_done=len(job_secs), jobs_total=len(futs),
                               published_rows=n_pub)

            # one serial retry for jobs whose subprocess died (OOM/timeout)
            recovered: list = []
            for e, ms, truth, why, tag in failed:
                logger.info("retrying job %s%s serially (was: %s)", e.id, tag, why)
                t0 = _t.monotonic()
                try:
                    out = _spawn(e, ms, truth, tag)
                except Exception as exc:  # noqa: BLE001
                    logger.error("job (%s%s) failed on retry: %s", e.id, tag, exc)
                    failed_after_retry.add(e.id)
                    continue
                job_secs.append(round(_t.monotonic() - t0, 1))
                _collect(e, truth, out, tag)
                _persist_pair_status()
                try:
                    _write_outputs(new_frames)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("interim output write failed (retry): %s", exc)
                _heartbeat(phase="retry", event=e.id, jobs_done=len(job_secs),
                           jobs_total=len(futs))
                recovered.append((e, ms, truth, why, tag))
            # a job that succeeded on retry is not a failed job (perf
            # stats feed public meta.json; phantom failures mislead triage)
            failed[:] = [t for t in failed if t not in recovered]
            _hb_stop.set()  # beat must cover the serial retry too (ponytail 2026-11-08)

        shutil.rmtree(tmpdir, ignore_errors=True)
        _persist_pair_status()
        job_stats = {}
        if job_secs:
            s = sorted(job_secs)
            job_stats = {"n": len(s), "min": s[0], "median": s[len(s) // 2],
                         "max": s[-1]}
        perf_stats = {
            "jobs": jobs,
            "pool_seconds": round(_t.monotonic() - t_pool, 1),
            "peak_total_rss_gb": round(perf.peak_bytes / 2**30, 2),
            "event_job_seconds": job_stats,
            "failed_jobs": [e.id for e, *_ in failed],
        }
        logger.info("scoring perf: %s", perf_stats)

    n_rows = _write_outputs(new_frames)
    # "complete" must mean what meta says (orchestrator 2026-11-08): only
    # full_run_complete exits 0, otherwise systemd (Restart=on-failure) and
    # the retry re-enter run() instead of leaving a half-scored board.
    meta_tail = json.loads((RESULTS / "meta.json").read_text())
    complete = bool(meta_tail.get("full_run_complete"))
    _heartbeat(phase="complete", rows=n_rows, events=len(events), complete=complete)
    if complete:
        logger.info("pipeline complete: %d events, %d score rows", len(events), n_rows)
        (RESULTS / "blocker.sig").unlink(missing_ok=True)
        return 0
    cov = meta_tail.get("pair_coverage", {})
    if publish_only:
        # an ops un-stick is done when the publish succeeded; meta.pair_coverage
        # and full_run_complete record the honest unfinished state (ponytail 2026-11-08)
        logger.info("publish-only: %d rows written; coverage %s", n_rows, cov)
        return 0
    missing = (cov.get("expected", 0) - cov.get("scored", 0)
               - cov.get("documented_zero_rows", 0))
    # unchanged blocker set across runs = permanent (e.g. a memory-bomb event):
    # exit 0 so Restart=on-failure stops hammering; meta reports the hole (ponytail 2026-11-08)
    sig = last_sig  # untruncated, from _write_outputs (fixed-name meta nesting was wrong)
    if _record_blocker_sig(RESULTS / "blocker.sig", sig) == sig:
        logger.warning("blocker set unchanged since previous run (%d pairs missing) — "
                       "exiting 0 to stop the restart loop; pair_coverage is honest in meta",
                       missing)
        return 0
    logger.error("pipeline finished with %d pairs missing (expected %d, scored %d, "
                 "documented-zero %d) — NOT complete", missing, cov.get("expected", 0),
                 cov.get("scored", 0), cov.get("documented_zero_rows", 0))
    return 1


def _job_main(jobfile: Path) -> int:
    """Worker-mode entry (spawned by run() as `--_run-job <file>`).

    Scores one event's pending models in a fresh process — all memory is
    returned to the OS on exit, so an OOM kills at most one job — and
    writes the result frame to the job's `out` parquet path, the
    per-model outcome table to `<out>.status.json`, and its peak RSS to
    stdout (the parent records it per job).
    """
    import pandas as _pd
    import resource

    spec = json.loads(jobfile.read_text())
    status: dict = {}
    eid, df = _score_job(spec["event"], spec["models"], spec["truth"],
                         status_out=status)
    if df is not None and len(df):
        _pd.DataFrame(df).to_parquet(spec["out"])
    # sidecar lives OUTSIDE the per-run tmpdir so a mem-guard SIGTERM
    # mid-job still persists model outcomes; parent merges on collect.
    sidecar = Path(spec["sidecar"])
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # .tmp.<pid> matches _sweep_orphan_temps' shape so a SIGKILL between
    # write and replace can't leave a permanent orphan (ponytail 20:35Z)
    tmp = sidecar.with_suffix(f".tmp.{os.getpid()}")
    # sanitize at write time (ponytail 2026-11-08 MUST): the sidecar lives
    # under data/results/ — the tree public_scrub_check.sh scans — so raw
    # {exc} tails (paths/URLs) must never land on disk here; logger.warning
    # in the scorer already keeps the full detail for the ops logs
    clean = {m: ({**st, "reason": public_reason(st["reason"])}
                 if isinstance(st, dict) and st.get("reason") else st)
             for m, st in status.items()}
    tmp.write_text(json.dumps(clean))
    tmp.replace(sidecar)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(f"job {eid}: {0 if df is None else len(df)} rows peak_rss_mb={peak_mb}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from ewb_live._guard import require_ewb_host  # CLI-only guard (see _guard.py)

    require_ewb_host()
    ap = argparse.ArgumentParser(prog="ewb_live.pipeline")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--models", type=str, default=None, help="comma-separated model ids (default: all from sources)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--end", type=str, default=None, help="window end date YYYY-MM-DD (default today)")
    ap.add_argument("--force", action="store_true", help="ignore cached scores")
    ap.add_argument("--events-only", action="store_true", help="detection only")
    ap.add_argument("--max-events", type=int, default=None, help="limit scoring to first N pending events (validation runs)")
    ap.add_argument("--top-n", type=int, default=15, help="curated cap per event type (0 disables curation)")
    ap.add_argument("--publish-only", action="store_true",
                    help="merge cached parts into published outputs and exit (no detection/scoring)")
    ap.add_argument("--_run-job", type=str, default=None,
                    help=argparse.SUPPRESS)  # worker mode: one job from a JSON file
    args = ap.parse_args(argv)

    if args._run_job:
        return _job_main(Path(args._run_job))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    logging.getLogger("extremeweatherbench").setLevel(logging.WARNING)
    end = dt.date.fromisoformat(args.end) if args.end else None
    models = args.models.split(",") if args.models else None
    return run(
        days=args.days, models=models, jobs=args.jobs, end=end,
        force=args.force, events_only=args.events_only, max_events=args.max_events,
        top_n=args.top_n or None, publish_only=args.publish_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
