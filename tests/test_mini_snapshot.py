"""Offline tests for the mini review-snapshot feature (?data=mini).

1. serve.py routes data/results/mini[-sparse]/<file>.json and nothing wilder.
2. scripts/build_mini_results.py fills the full event x model x metric x lead
   grid, flags synthetic rows, and builds a baseline-anchored scorecard.
"""
import importlib.util
import json
import os
from pathlib import Path

from ewb_live import serve


def _translate(tmp_path, url_path):
    orig_results, orig_sample = serve.RESULTS_DIR, serve.SAMPLE_DIR
    try:
        serve.RESULTS_DIR = str(tmp_path)
        serve.SAMPLE_DIR = str(tmp_path / "empty-sample")  # no fallback hits
        h = serve.DashboardHandler.__new__(serve.DashboardHandler)
        return serve.DashboardHandler.translate_path(h, url_path)
    finally:
        serve.RESULTS_DIR, serve.SAMPLE_DIR = orig_results, orig_sample


def test_mini_routes(tmp_path):
    (tmp_path / "mini").mkdir()
    got = _translate(tmp_path, "/data/results/mini/scores.json")
    assert got == str(tmp_path / "mini" / "scores.json")
    got = _translate(tmp_path, "/data/results/mini-sparse/meta.json")
    assert got == str(tmp_path / "mini-sparse" / "meta.json")


def test_mini_routes_reject_nonjson_and_dirs(tmp_path):
    got = _translate(tmp_path, "/data/results/mini/scores.parquet")
    assert "nonesuch" in got            # parquet not served
    got = _translate(tmp_path, "/data/results/mini")
    assert "nonesuch" in got            # no directory listing


def test_default_routes_unaffected(tmp_path):
    got = _translate(tmp_path, "/data/results/scores.json")
    assert got == str(tmp_path / "scores.json")


def _load_builder():
    p = Path(__file__).resolve().parents[1] / "scripts" / "build_mini_results.py"
    spec = importlib.util.spec_from_file_location("build_mini_results", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_builder_fills_grid_and_flags(tmp_path):
    m = _load_builder()
    events = [
        {"id": "aa11", "event_type": "heat_wave", "start": "2026-09-01", "end": "2026-09-05"},
        {"id": "bb22", "event_type": "atmospheric_river", "start": "2026-10-01", "end": "2026-10-02"},
        {"id": "cc33", "event_type": "freeze", "start": "2026-10-10", "end": "2026-10-16"},
    ]
    models = list(m.MODEL_FACTOR)
    real_rows = [  # one real heat row for aifs_single, must survive untouched
        {"event_id": "aa11", "event_type": "heat_wave", "model": "aifs_single",
         "metric": "RootMeanSquaredError", "lead_time_hours": 18.0,
         "value": 1.2345, "init_time": None},
    ]
    meta = {"models": models, "baseline_model": "ifs_hres"}
    out = tmp_path / "mini"
    total, n_syn = m.build_variant(events, models, real_rows, [0.0, 18.0],
                                   meta, out, "test note")
    scores = json.loads((out / "scores.json").read_text())
    rows = scores["rows"]
    # full grid: per event, models x (per-lead leads + null-lead metrics)
    per = lambda et: len(models) * (sum(l for _nm, l in m.METRICS[et]) * 2 + sum(1 for _nm, l in m.METRICS[et] if not l))
    assert total == len(real_rows) + (per("heat_wave") - 1) + per("atmospheric_river") + per("freeze")
    assert n_syn == total - len(real_rows)
    real = [r for r in rows if not r.get("synthetic")]
    assert real == real_rows                      # untouched
    assert all(r.get("synthetic") for r in rows if r not in real_rows)
    # every (event, model, metric) cell exists
    keys = {(r["event_id"], r["model"], r["metric"], r["lead_time_hours"]) for r in rows}
    for ev in events:
        for mo in models:
            for metric, per_lead in m.METRICS[ev["event_type"]]:
                leads = [0.0, 18.0] if per_lead else [None]
                for lead in leads:
                    assert (ev["id"], mo, metric, lead) in keys
    # scorecard: baseline present, skill computed, event_types all covered
    sc = json.loads((out / "scorecard.json").read_text())
    assert sc["baseline"] == "ifs_hres"
    assert {r["event_type"] for r in sc["rows"]} == {"heat_wave", "atmospheric_river", "freeze"}
    heat = [r for r in sc["rows"] if r["model"] == "ifs_hres" and r["metric"] == "RootMeanSquaredError"]
    assert heat and all(r["skill_vs_ifs_pct"] == 0.0 for r in heat)
    # meta: mini flags set, no fake full_run_complete
    meta_out = json.loads((out / "meta.json").read_text())
    assert meta_out["mini"] is True and "full_run_complete" not in meta_out
    assert meta_out["mini_stats"]["models"] == len(models)


def test_builder_deterministic(tmp_path):
    m = _load_builder()
    v1 = m.synth_value({"id": "aa11", "event_type": "heat_wave",
                        "start": "2026-09-01", "end": "2026-09-05"},
                       "graphcast", "MaximumMeanAbsoluteError", 24.0, 1.0)
    v2 = m.synth_value({"id": "aa11", "event_type": "heat_wave",
                        "start": "2026-09-01", "end": "2026-09-05"},
                       "graphcast", "MaximumMeanAbsoluteError", 24.0, 1.0)
    assert v1 == v2
