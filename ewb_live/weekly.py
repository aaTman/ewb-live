"""EWB Live weekly refresh cycle (SPEC section 5).

    python -m ewb_live.weekly [--days 90] [--jobs 4]

Order: (1) mlwp_icechunk update + source refresh hook, (2) new extreme-event
search over the rolling 90-day window, (3) EWB eval for new/changed events and
newly available forecasts (pipeline.run is incremental), (4) atomic rewrite of
data/results/*.json, (5) snapshot to data/results/history/<YYYY-WW>/.

meta.json gains cycle/week/next_update. Logs to logs/weekly-<date>.log, exits
non-zero on failure, safe to re-run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("ewb_live.weekly")

RESULTS = Path("data/results")
HISTORY = RESULTS / "history"
LOGS = Path("logs")


def _iso_week(now: dt.datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _next_update(now: dt.datetime) -> dt.datetime:
    """Next Monday 07:00 UTC strictly after now (ops timer schedule)."""
    days_ahead = (0 - now.weekday()) % 7
    candidate = (now + dt.timedelta(days=days_ahead)).replace(
        hour=7, minute=0, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += dt.timedelta(days=7)
    return candidate


# ------------------------------------------------------------- step 1 -------

def update_mlwp_icechunk(timeout: int = 3 * 3600) -> bool:
    """Run the CIRA MLWP Icechunk store update; skip cleanly if unavailable.

    Preferred path is the in-repo store module `ewb_live.stores.mlwp_icechunk`
    (virtual Icechunk store over s3://noaa-oar-mlwp-data, FULL archive, updated
    daily by ewb-live-store.timer at 05:30 UTC). This weekly call is a harmless
    catch-up: `update` appends only inits missing since the last commit and is
    flock-guarded, so while the daily update runs it exits 0 having done nothing.
    Falls back to a `mlwp-icechunk` executable or a top-level `mlwp_icechunk`
    module for older installs.

    Returns True if an update ran successfully, False if skipped, raises on a
    failed update (the chair will want the non-zero exit).
    """
    try:
        import ewb_live.stores.mlwp_icechunk  # noqa: F401

        cmd = [sys.executable, "-m", "ewb_live.stores.mlwp_icechunk", "update"]
    except ImportError:
        exe = shutil.which("mlwp-icechunk")
        if exe is None:
            try:
                import mlwp_icechunk  # noqa: F401

                exe = None  # module present, no CLI: use -m
            except ImportError:
                logger.warning("mlwp_icechunk not installed — skipping source update step")
                return False
        cmd = [exe, "update"] if exe else [sys.executable, "-m", "mlwp_icechunk", "update"]
    logger.info("running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"mlwp-icechunk update exited {proc.returncode}")
    return True


# ------------------------------------------------------------- step 5 -------

def snapshot(week: str) -> Path:
    """Copy the current results dir into history/<week>/ (atomic-ish)."""
    dest = HISTORY / week
    dest.mkdir(parents=True, exist_ok=True)
    for f in RESULTS.iterdir():
        if f.is_file():
            tmp = dest / (f.name + f".tmp.{os.getpid()}")  # pid suffix: sweep-visible
            shutil.copy2(f, tmp)
            os.replace(tmp, dest / f.name)
    logger.info("snapshot written to %s", dest)
    return dest


def _augment_meta(week: str, now: dt.datetime) -> None:
    path = RESULTS / "meta.json"
    meta = json.loads(path.read_text()) if path.exists() else {}
    meta["cycle"] = "weekly"
    meta["week"] = week
    meta["next_update"] = _next_update(now).isoformat()
    tmp = path.with_suffix(f".json.tmp.{os.getpid()}")  # pid suffix: sweep-visible
    tmp.write_text(json.dumps(meta, indent=1))
    os.replace(tmp, path)


# ---------------------------------------------------------------- main ------

def _leak_gate(stage: str) -> bool:
    """Run the public-scrub gate; on a hit, quarantine every flagged file
    under data/results out of the served tree so the failure removes the
    leak (not just logs it). Returns True when CLEAN."""
    gate = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "public_scrub_check.sh")],
        capture_output=True, text=True,
    )
    if gate.returncode == 0:
        logger.info("public_scrub_check: CLEAN (%s)", stage)
        return True
    logger.error("public_scrub_check FAILED at %s:\n%s", stage,
                 (gate.stdout + gate.stderr)[-4000:])
    # quarantine must land OUTSIDE data/results: the gate rescans that tree
    # every run and install.sh gates deploys on it — an in-tree rename would
    # poison all future weeklies/redeploys (ponytail 2026-11-08, my bug)
    qdir = RESULTS.parent / "quarantine"
    qdir.mkdir(exist_ok=True)
    moved = []
    for ln in (gate.stdout + gate.stderr).splitlines():
        head, sep, rest = ln.partition(":")
        if not sep or not rest.split(":", 1)[0].isdigit():
            continue  # not a grep "path:line:content" hit line
        f = Path(head)
        try:
            f = f.resolve()
        except OSError:
            continue
        if f.is_file() and RESULTS.resolve() in f.parents:
            # stamp the name: two runs leaking the same filename must not
            # overwrite each other's evidence (ponytail 2026-11-08); flatten
            # the RELATIVE path too: one run flagging four meta.json's (live
            # + history + minis) otherwise collapses to one survivor
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
            rel = str(f.relative_to(RESULTS)).replace("/", "__")
            f = f.rename(qdir / (stamp + rel))
            moved.append(str(f))
    logger.error("QUARANTINED %d leaked file(s): %s", len(moved), moved)
    return False


def run(days: int = 90, jobs: int = 4, models: list[str] | None = None) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    week = _iso_week(now)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"weekly-{now:%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_path),
        ],
    )
    logging.getLogger("extremeweatherbench").setLevel(logging.WARNING)
    logger.info("=== weekly run start week=%s (UTC %s) ===", week, now.isoformat())

    try:
        # 1. forecast source update (skips cleanly when mlwp_icechunk absent)
        update_mlwp_icechunk()

        # 2-4. detect + incremental eval + atomic results write
        from ewb_live import pipeline

        rc = pipeline.run(days=days, models=models, jobs=jobs)
        if rc != 0:
            logger.error("pipeline.run exited %d", rc)
            return rc

        # 4b. public leak gate (owner rule 2026-11-08) BEFORE meta-augment /
        # history snapshot / viz: on a hit, quarantine flagged files out of
        # the served tree so the gate failure actually removes the leak
        # (ponytail 2026-11-08: a post-publish-only check left leaked files
        # served AND copied into history/)
        if not _leak_gate("post-scoring"):
            return 1

        # 5. meta cycle fields + history snapshot
        _augment_meta(week, now)
        snapshot(week)

        # 6. dashboard per-event viz assets (design team; non-fatal —
        # a viz failure must not fail the weekly cycle). Cold-cache build
        # is network-heavy (~30-60 min), warm runs take minutes;
        # idempotent, skips events already at current schema.
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "ewb_live.viz", "--all"],
                timeout=5400,
            )
            logger.info("ewb_live.viz --all exited %d", proc.returncode)
        except Exception as exc:  # noqa: BLE001 — viz is best-effort
            logger.warning("ewb_live.viz step failed (non-fatal): %s", exc)

        # 6b. global raw-exceedance frames (design's raw map, briefs/
        # raw_exceedance_map.md): per-6h heat/freeze/AR bitmasks under
        # data/results/raw_exceedance/, incremental (only new frames).
        # Non-fatal like viz; skipped until design lands the builder.
        import importlib.util
        if importlib.util.find_spec("ewb_live.viz.exceedance") is None:
            logger.info("ewb_live.viz.exceedance not present; skipping raw-exceedance step")
        else:
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "ewb_live.viz.exceedance"],
                    timeout=5400,
                )
                logger.info("ewb_live.viz.exceedance exited %d", proc.returncode)
            except Exception as exc:  # noqa: BLE001 — best-effort like viz
                logger.warning("ewb_live.viz.exceedance step failed (non-fatal): %s", exc)

        # 6c. regional scorecard refresh (regions lane): pipeline.run wrote
        # scorecard_by_region.json BEFORE viz assets existed for newly detected
        # events, so those fell back to the bbox-centroid rule. Re-run the regen
        # now that viz (step 6) published the footprint masks, so the published
        # numbers are footprint-based in the same cycle. Non-fatal: the file
        # written in step 3 is already a valid (centroid-fallback) breakdown.
        try:
            proc = subprocess.run(
                [sys.executable, "scripts/build_region_scores.py", "data/results"],
                timeout=1800,
            )
            logger.info("build_region_scores exited %d", proc.returncode)
        except Exception as exc:  # noqa: BLE001 — best-effort like viz
            logger.warning("region scorecard refresh failed (non-fatal): %s", exc)

        # 7. final public leak gate (re-check after meta/viz writes)
        if not _leak_gate("post-publish"):
            return 1
        logger.info("public_scrub_check: CLEAN (post-publish)")
    except Exception:  # noqa: BLE001 — SPEC: non-zero exit on failure
        logger.exception("weekly run failed")
        return 1

    logger.info("=== weekly run complete (log: %s) ===", log_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    from ewb_live._guard import require_ewb_host  # CLI-only guard (see _guard.py)

    require_ewb_host()
    ap = argparse.ArgumentParser(prog="ewb_live.weekly")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--models", type=str, default=None, help="comma-separated model ids")
    args = ap.parse_args(argv)
    return run(days=args.days, jobs=args.jobs,
               models=args.models.split(",") if args.models else None)


if __name__ == "__main__":
    raise SystemExit(main())
