#!/usr/bin/env python
"""Build the `mini` review dataset (owner decision 2026-11-06, 22:2x UTC).

Freezes the interim live results (read-only copy) and fills every missing
(event, model, metric, lead) cell with clearly-flagged synthetic values so the
dashboard can be exercised at full scale — all 27 events, all 11 models, all
leads, all three event types — while the real run finishes. Also writes a
`mini-sparse` variant (one event per type) to exercise sparse states.

Output (never touches the live files):
    data/results/mini/        meta.json events_selected.json scores.json scorecard.json
    data/results/mini-sparse/ (same shape, 3 events)

Synthetic rows carry synthetic=true; meta carries mini=true + a note. Real
rows are copied through untouched. The scorecard is rebuilt from the union
with the same reducer as pipeline.build_scorecard (kept in sync by the test
tests/test_mini_results.py).

Usage (on the box):
    python scripts/build_mini_results.py \
        --results data/results --leads 0 6 12 ... (optional overrides)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import zlib
from pathlib import Path

BASELINE = "ifs_hres"
HIGHER_IS_BETTER = {"CriticalSuccessIndex", "Accuracy", "EarlySignal"}

# Metrics per event type — mirror of ewb_live/scoring/evaluate.py::default_metrics.
# (duration-type + EarlySignal are event-level: lead_time_hours=None.)
METRICS = {
    "heat_wave": [
        ("MaximumMeanAbsoluteError", True),
        ("RootMeanSquaredError", True),
        ("DurationMeanError", False),
        ("MaximumLowestMeanAbsoluteError", True),
    ],
    "freeze": [
        ("MinimumMeanAbsoluteError", True),
        ("RootMeanSquaredError", True),
        ("DurationMeanError", False),
    ],
    "atmospheric_river": [
        ("CriticalSuccessIndex", True),
        ("SpatialDisplacement", True),
        ("EarlySignal", False),
    ],
}

# Multiplicative skill factors on error metrics (<1 = better than baseline);
# inverted for higher-is-better metrics. Rough ordering from the interim
# scorecard, kept modest so nothing looks absurd.
MODEL_FACTOR = {
    "aifs_single": 0.88, "ifs_hres": 1.00, "aigfs": 1.03,
    "aurora": 0.95, "aurora_gfs": 1.08,
    "graphcast": 0.98, "graphcast_gfs": 1.12,
    "pangu_weather": 0.97, "pangu_weather_gfs": 1.14,
    "fourcastnet_v2": 1.05, "fourcastnet_v2_gfs": 1.18,
}

# (base, lead_slope) for lead-varying metrics; None lead => single value `base`.
def base_level(event_type: str, metric: str, lead) -> float:
    rng_lead = 0.0 if lead is None else float(lead)
    key = (event_type, metric)
    table = {
        ("heat_wave", "MaximumMeanAbsoluteError"): 1.1 + 0.010 * rng_lead,
        ("heat_wave", "RootMeanSquaredError"): 1.7 + 0.012 * rng_lead,
        ("heat_wave", "MaximumLowestMeanAbsoluteError"): 1.9 + 0.009 * rng_lead,
        ("heat_wave", "DurationMeanError"): 0.9,
        ("freeze", "MinimumMeanAbsoluteError"): 1.5 + 0.012 * rng_lead,
        ("freeze", "RootMeanSquaredError"): 2.1 + 0.014 * rng_lead,
        ("freeze", "DurationMeanError"): 1.1,
        ("atmospheric_river", "CriticalSuccessIndex"): max(0.05, 0.66 - 0.0015 * rng_lead),
        ("atmospheric_river", "SpatialDisplacement"): 60.0 + 1.1 * rng_lead,
        ("atmospheric_river", "EarlySignal"): 42.0,
    }
    return table[key]


def det_rng(*parts) -> random.Random:
    """Deterministic across runs (str hash is not): crc32-seeded."""
    seed = zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))
    return random.Random(seed)


def synth_value(event, model, metric, lead, event_factor) -> float:
    ev = event["id"]
    et = event["event_type"]
    rng = det_rng(ev, model, metric, lead)
    base = base_level(et, metric, lead)
    f = MODEL_FACTOR.get(model, 1.0)
    if metric in HIGHER_IS_BETTER:
        f = 2.0 - f  # invert: better models score higher
        val = base * f * event_factor
        if metric == "CriticalSuccessIndex":
            val = min(0.95, max(0.02, val))
    else:
        val = base * f * event_factor
        if metric == "DurationMeanError":  # signed: sign per (event, model)
            sign = 1.0 if det_rng(ev, model, "dur_sign").random() < 0.5 else -1.0
            val = abs(val) * sign
    return round(val * (1.0 + rng.uniform(-0.06, 0.06)), 4)


def event_factor(event) -> float:
    """Bigger/longer events score slightly worse (plausible spread)."""
    days = 1.0
    try:
        d0 = dt.date.fromisoformat(event["start"][:10])
        d1 = dt.date.fromisoformat(event["end"][:10])
        days = max(1.0, (d1 - d0).days)
    except Exception:
        pass
    return round(0.92 + 0.03 * min(days, 12) / 4.0, 3)


# ---- scorecard reducer: verbatim logic of pipeline.build_scorecard ----------
def build_scorecard(rows: list[dict]) -> dict:
    from collections import defaultdict
    sums: dict[tuple, float] = defaultdict(float)
    counts: dict[tuple, int] = defaultdict(int)
    events_by_key: dict[tuple, set] = defaultdict(set)
    for r in rows:
        key = (r["event_type"], r["metric"], r["lead_time_hours"], r["model"])
        sums[key] += float(r["value"])
        counts[key] += 1
        events_by_key[key].add(r["event_id"])
    base = {(k[0], k[1], k[2]): (sums[k] / counts[k])
            for k in sums if k[3] == BASELINE and counts[k]}
    out = []
    for key in sorted(sums, key=lambda k: (str(k[0]), str(k[1]),
                                           -1 if k[2] is None else k[2], str(k[3]))):
        etype, metric, lead, model = key
        mean = sums[key] / counts[key]
        b = base.get((etype, metric, lead))
        skill = None
        if b is not None and abs(b) > 1e-9:
            rel = (mean - b) / abs(b) * 100.0
            if metric not in HIGHER_IS_BETTER:
                rel = -rel
            skill = round(rel, 2)
        out.append({
            "event_type": etype, "metric": metric, "lead_time_hours": lead,
            "model": model, "mean": round(mean, 4), "n_events": len(events_by_key[key]),
            "n_samples": counts[key], "skill_vs_ifs_pct": skill,
        })
    return {"baseline": BASELINE, "rows": out}


def build_variant(events, models, real_rows, leads, meta, out_dir, note, pair_status=None):
    have = {(r["event_id"], r["model"], r["metric"], r["lead_time_hours"])
            for r in real_rows}
    rows = list(real_rows)
    n_syn = 0
    for ev in events:
        ef = event_factor(ev)
        for model in models:
            # ewb pair_status (DR3, 2026-11-07): documented exclusions
            # (bench_failed "n/a (memory limit)", unsupported) outrank
            # synthetic fill — the excluded pairs must read n/a in mini too.
            if pair_status:
                st = (pair_status.get(f"{ev['id']}|{model}") or {}).get("status")
                if st not in (None, "scored"):
                    continue
            for metric, per_lead in METRICS[ev["event_type"]]:
                lead_opts = leads if per_lead else [None]
                for lead in lead_opts:
                    if (ev["id"], model, metric, float(lead) if lead is not None else None) in have:
                        continue
                    rows.append({
                        "event_id": ev["id"], "event_type": ev["event_type"],
                        "model": model, "metric": metric,
                        "lead_time_hours": float(lead) if lead is not None else None,
                        "value": synth_value(ev, model, metric, lead, ef),
                        "init_time": None, "synthetic": True,
                    })
                    n_syn += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    w = lambda name, obj: (out_dir / name).write_text(json.dumps(obj))
    w("scores.json", {"generated_at": now, "rows": rows})
    w("scorecard.json", build_scorecard(rows))
    w("events_selected.json", events)
    w("events.json", {"events": []})   # raw-candidate probe: empty, not 404 noise
    m = dict(meta)
    m.update({
        "mini": True,
        "mini_note": note,
        "mini_built_at": now,
        "mini_stats": {"real_rows": len(real_rows), "synthetic_rows": n_syn,
                       "events": len(events), "models": len(models)},
    })
    m.pop("full_run_complete", None)  # mini is not the real complete run
    w("meta.json", m)
    return len(rows), n_syn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="data/results")
    ap.add_argument("--leads", nargs="*", type=float, default=None,
                    help="lead slots for per-lead metrics (default: 0..240 step 6 + 252)")
    ap.add_argument("--sparse-ids", nargs=3, default=["29d2a97a7c09", "457f3f87e24a", "88bb73d7ae25"],
                    help="one event id per type for the sparse variant")
    args = ap.parse_args()

    res = Path(args.results)
    meta = json.loads((res / "meta.json").read_text())
    events = json.loads((res / "events_selected.json").read_text())
    sc = json.loads((res / "scores.json").read_text())
    real_rows = sc["rows"] if isinstance(sc, dict) else sc
    models = list(meta["models"])
    ps_path = res / "pair_status.json"
    pair_status = json.loads(ps_path.read_text()) if ps_path.exists() else None
    leads = args.leads or ([h for h in range(0, 241, 6)] + [252])
    leads = [float(x) for x in leads]

    n, syn = build_variant(
        events, models, real_rows, leads, meta, res / "mini",
        "Frozen interim snapshot (live run still in progress) + synthetic "
        "score cells so every view is exercised at full scale; viz and event "
        "list are real. Real numbers land with the live run (~02:15 UTC).",
        pair_status=pair_status)
    print(f"mini: {n} rows ({syn} synthetic), {len(events)} events x {len(models)} models")

    pick = {i: True for i in args.sparse_ids}
    sparse_events = [e for e in events if e["id"] in pick]
    sparse_ids = {e["id"] for e in sparse_events}
    sparse_real = [r for r in real_rows if r["event_id"] in sparse_ids]
    n2, syn2 = build_variant(
        sparse_events, models, sparse_real, leads, meta, res / "mini-sparse",
        "Sparse-state variant: 1 event per type (AR / heat / freeze) on the "
        "mini snapshot. Same synthetic-fill rule; use to test thin-data views.",
        pair_status=pair_status)
    print(f"mini-sparse: {n2} rows ({syn2} synthetic), {len(sparse_events)} events")

    # the app reads exclusion reasons from pair_status.json — carry the live
    # file into the snapshots (read-only copy; live files untouched).
    if ps_path.exists():
        for d in (res / "mini", res / "mini-sparse"):
            (d / "pair_status.json").write_text(ps_path.read_text())


if __name__ == "__main__":
    main()
