"""Unit tests for scoring/pipeline logic that needs no network."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ewb_live.events.types import Event
from ewb_live.pipeline import BASELINE, HIGHER_IS_BETTER, build_scorecard, fingerprint
from ewb_live.scoring.evaluate import (
    INIT_STEP_HOURS,
    MAX_LEAD_HOURS,
    SCORE_COLUMNS,
    _reshape,
    default_metrics,
)
from ewb_live.scoring.truth import truth_source_for_event


def make_event(event_type="heat_wave", start=None, end=None, bbox=(0.0, 1.0, 0.0, 1.0), peak_value=None):
    start = start or dt.datetime(2026, 10, 1)
    end = end or dt.datetime(2026, 10, 3)
    return Event(id="abc123", event_type=event_type, title="t", start=start,
                 end=end, bbox=bbox, peak_value=None)


def test_score_columns_match_spec():
    assert SCORE_COLUMNS == [
        "event_id", "event_type", "model", "metric",
        "lead_time_hours", "value", "init_time",
    ]


def test_lead_config():
    assert MAX_LEAD_HOURS == 240
    assert INIT_STEP_HOURS == 24


def test_truth_source_policy():
    cutoff = dt.datetime(2026, 10, 30, 23)
    assert truth_source_for_event(make_event(end=dt.datetime(2026, 10, 20)), cutoff) == "era5_arco"
    assert truth_source_for_event(make_event(end=dt.datetime(2026, 11, 3)), cutoff) == "ifs_hres_analysis"


def test_default_metrics_match_ewb_defaults():
    names = {
        "heat_wave": {"MaximumMeanAbsoluteError", "RootMeanSquaredError",
                      "DurationMeanError", "MaximumLowestMeanAbsoluteError"},
        "freeze": {"MinimumMeanAbsoluteError", "RootMeanSquaredError", "DurationMeanError"},
        "atmospheric_river": {"CriticalSuccessIndex", "SpatialDisplacement", "EarlySignal"},
    }
    for et, expected in names.items():
        got = {m.__class__.__name__ for m in default_metrics(et)}
        assert got == expected, et
    with pytest.raises(ValueError):
        default_metrics("tornado")


def test_reshape_ewb_longform():
    ewb_df = pd.DataFrame({
        "event_type": ["heat_wave"] * 3,
        "forecast_source": ["fake"] * 3,
        "metric": ["RootMeanSquaredError", "RootMeanSquaredError", "DurationMeanError"],
        "lead_time": [pd.Timedelta(hours=0), pd.Timedelta(hours=24), pd.Timedelta(hours=0)],
        "value": [1.0, 2.0, np.nan],
        "init_time": [dt.datetime(2026, 9, 30), dt.datetime(2026, 9, 30), dt.datetime(2026, 9, 30)],
    })
    out = _reshape(ewb_df, make_event())
    assert list(out.columns) == SCORE_COLUMNS
    assert len(out) == 2  # NaN value row dropped
    assert set(out["metric"]) == {"RootMeanSquaredError"}
    assert list(out["lead_time_hours"]) == [0.0, 24.0]
    assert (out["event_id"] == "abc123").all()


def test_fingerprint_changes_with_window_or_bbox():
    a = fingerprint(make_event())
    assert a == fingerprint(make_event())
    assert a != fingerprint(make_event(bbox=(0.0, 2.0, 0.0, 1.0)))
    assert a != fingerprint(make_event(end=dt.datetime(2026, 10, 4)))


def _scorecard_fixture():
    rows = []
    for lead in (0.0, 24.0):
        rows += [
            {"event_id": "e1", "event_type": "heat_wave", "model": "ifs_hres",
             "metric": "RootMeanSquaredError", "lead_time_hours": lead,
             "value": 2.0, "init_time": dt.datetime(2026, 10, 1)},
            {"event_id": "e1", "event_type": "heat_wave", "model": "graphcast",
             "metric": "RootMeanSquaredError", "lead_time_hours": lead,
             "value": 1.5, "init_time": dt.datetime(2026, 10, 1)},
            {"event_id": "e1", "event_type": "heat_wave", "model": "ifs_hres",
             "metric": "CriticalSuccessIndex", "lead_time_hours": lead,
             "value": 0.5, "init_time": dt.datetime(2026, 10, 1)},
            {"event_id": "e2", "event_type": "heat_wave", "model": "graphcast",
             "metric": "CriticalSuccessIndex", "lead_time_hours": lead,
             "value": 0.6, "init_time": dt.datetime(2026, 10, 1)},
        ]
    return pd.DataFrame(rows)


def test_scorecard_skill_direction_and_counts():
    card = build_scorecard(_scorecard_fixture())
    r = {(x["metric"], x["model"]): x for x in card["rows"] if x["lead_time_hours"] == 0.0}
    assert card["baseline"] == BASELINE == "ifs_hres"
    # lower-is-better RMSE: graphcast 1.5 vs baseline 2.0 -> +25 %
    assert r[("RootMeanSquaredError", "graphcast")]["skill_vs_ifs_pct"] == pytest.approx(25.0)
    assert r[("RootMeanSquaredError", "ifs_hres")]["skill_vs_ifs_pct"] == pytest.approx(0.0)
    # higher-is-better CSI: graphcast on e2, baseline on e1 — DISJOINT event
    # sets. Under the common-subset rule (science-review D2) there is no
    # like-for-like comparison: skill must be None, not a bogus +20 %.
    assert "CriticalSuccessIndex" in HIGHER_IS_BETTER
    assert r[("CriticalSuccessIndex", "graphcast")]["skill_vs_ifs_pct"] is None
    assert r[("CriticalSuccessIndex", "graphcast")]["skill_n_common"] is None
    assert r[("CriticalSuccessIndex", "graphcast")]["n_events"] == 1
    # groups are per lead_time (SPEC): the lead-0 group has exactly 1 sample
    assert r[("RootMeanSquaredError", "graphcast")]["n_samples"] == 1
    lead24 = {(x["metric"], x["model"]): x for x in card["rows"] if x["lead_time_hours"] == 24.0}
    assert lead24[("RootMeanSquaredError", "graphcast")]["n_samples"] == 1


def test_scorecard_empty_and_missing_baseline():
    assert build_scorecard(pd.DataFrame(columns=SCORE_COLUMNS)) == {"rows": []}
    df = _scorecard_fixture()
    df = df[df["model"] != "ifs_hres"]
    card = build_scorecard(df)
    assert all(row["skill_vs_ifs_pct"] is None for row in card["rows"])


def test_select_event_ids_round_robin():
    from ewb_live.pipeline import _select_event_ids
    from ewb_live.events.types import Event
    import datetime as dt

    def ev(i, t):
        return Event(id=f"{i:012x}", event_type=t, title=t,
                     start=dt.datetime(2026, 10, 1 + i % 20),
                     end=dt.datetime(2026, 10, 3 + i % 20),
                     bbox=(0.0, 10.0, 0.0, 10.0), peak_value=None)
    todo = []
    for i in range(10):
        todo.append((ev(i, "heat_wave"), "m"))
    for i in range(10, 13):
        todo.append((ev(i, "freeze"), "m"))
    for i in range(13, 15):
        todo.append((ev(i, "atmospheric_river"), "m"))
    ids = _select_event_ids(todo, 6)
    types = []
    by_id = {e.id: e.event_type for e, _ in todo}
    types = [by_id[i] for i in ids]
    assert len(ids) == 6
    assert types.count("heat_wave") == 2
    assert types.count("freeze") == 2
    assert types.count("atmospheric_river") == 2
    assert len(set(ids)) == 6


def test_select_event_ids_max_above_count():
    from ewb_live.pipeline import _select_event_ids
    from ewb_live.events.types import Event
    import datetime as dt
    e = Event(id="a" * 12, event_type="freeze", title="f",
              start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 2),
              bbox=(0.0, 1.0, 0.0, 1.0), peak_value=None)
    assert _select_event_ids([(e, "m")], 99) == ["a" * 12]


def test_case_region_widened_only_for_ar():
    from ewb_live.events.types import Event
    from ewb_live.scoring.evaluate import BBOX_PAD_DEG, case_region_for
    import datetime as dt

    common = dict(start=dt.datetime(2026, 8, 1), end=dt.datetime(2026, 8, 3),
                  peak_value=None)
    ar = Event(id="aaaaaaaaaa", event_type="atmospheric_river", title="ar",
               bbox=(10.0, 15.0, 100.0, 110.0), **common)
    heat = Event(id="bbbbbbbbbb", event_type="heat_wave", title="h",
                 bbox=(10.0, 15.0, 100.0, 110.0), **common)
    ar_case = case_region_for(ar).to_ewb_case()
    loc = ar_case["location"]["parameters"]
    assert loc["latitude_min"] == 10.0 - BBOX_PAD_DEG
    assert loc["longitude_max"] == 110.0 + BBOX_PAD_DEG
    assert case_region_for(heat).bbox == heat.bbox


def _sc_df(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=["event_type", "metric", "lead_time_hours",
                                       "model", "event_id", "value"])


def test_scorecard_skill_common_subset_and_bootstrap_ci():
    """D2: skill uses only events the model and ifs_hres share (RMSE, so
    lower-is-better flips sign); D3: event-bootstrap CI needs >= 3 events."""
    df = _sc_df([
        # model m1 on events a,b; baseline on a,c — common set = {a} only
        ["heat_wave", "RootMeanSquaredError", 0.0, "m1", "a", 1.0],
        ["heat_wave", "RootMeanSquaredError", 0.0, "m1", "b", 9.0],
        ["heat_wave", "RootMeanSquaredError", 0.0, BASELINE, "a", 2.0],
        ["heat_wave", "RootMeanSquaredError", 0.0, BASELINE, "c", 100.0],
        # 4-event cell for CI coverage
        ["heat_wave", "Accuracy", 0.0, "m2", "a", 0.4],
        ["heat_wave", "Accuracy", 0.0, "m2", "b", 0.6],
        ["heat_wave", "Accuracy", 0.0, "m2", "c", 0.5],
        ["heat_wave", "Accuracy", 0.0, "m2", "d", 0.55],
    ])
    rows = {(r["model"], r["metric"]): r for r in build_scorecard(df)["rows"]}
    r = rows[("m1", "RootMeanSquaredError")]
    # common subset {a}: (1.0 - 2.0)/2 * -100 -> +50% skill, NOT skewed by b/c
    assert r["skill_vs_ifs_pct"] == pytest.approx(50.0)
    assert r["skill_n_common"] == 1
    assert r["ci95_lo"] is None and r["ci95_hi"] is None  # 2 events < 3
    c = rows[("m2", "Accuracy")]
    assert c["ci95_lo"] is not None and c["ci95_lo"] <= 0.5125 <= c["ci95_hi"]


def test_scorecard_skill_suppressed_on_signed_error_metrics():
    """D6: DurationMeanError means are signed (-26.5 < -14.6 must NOT read as
    +skill); skill is null for every duration cell, baseline included."""
    df = _sc_df([
        ["heat_wave", "DurationMeanError", 0.0, "aurora", "a", -26.5],
        ["heat_wave", "DurationMeanError", 0.0, BASELINE, "a", -14.6],
    ])
    rows = {(r["model"], r["metric"]): r for r in build_scorecard(df)["rows"]}
    assert rows[("aurora", "DurationMeanError")]["skill_vs_ifs_pct"] is None
    assert rows[(BASELINE, "DurationMeanError")]["skill_vs_ifs_pct"] is None
    # RMSE cells keep normal skill semantics
    df2 = _sc_df([
        ["heat_wave", "RootMeanSquaredError", 0.0, "aurora", "a", 1.0],
        ["heat_wave", "RootMeanSquaredError", 0.0, BASELINE, "a", 2.0],
    ])
    r2 = {(r["model"], r["metric"]): r for r in build_scorecard(df2)["rows"]}
    assert r2[("aurora", "RootMeanSquaredError")]["skill_vs_ifs_pct"] == 50.0


def test_prune_pair_status_and_sweep_orphan_temps(tmp_path):
    """Ponytail 2026-11-08: dead event ids churned out of selection must not
    accumulate in the public pair_status.json; orphaned *.tmp.<pid>.<tid>
    files from killed writers get swept, fresh ones (concurrent run) stay."""
    from ewb_live.pipeline import _prune_pair_status, _sweep_orphan_temps

    ev = make_event()
    ev2 = make_event(); ev2.id = "live000002"
    ps = {f"{ev.id}|m1": {"status": "scored"},
          "deadid0000|m1": {"status": "no_data"},
          f"{ev2.id}|m2": {"status": "read_error"}}
    out = _prune_pair_status(ps, [ev, ev2], ["m1"])
    assert set(out) == {f"{ev.id}|m1"}  # dead id AND non-live model dropped

    old = tmp_path / "meta.json.tmp.111.222"
    old.write_text("torn")
    import os as _os
    _os.utime(old, (_os.stat(old).st_atime - 7200, _os.stat(old).st_mtime - 7200))
    fresh = tmp_path / "pair_status.json.tmp.333.444"
    fresh.write_text("in-flight")
    assert _sweep_orphan_temps(tmp_path) == 1
    assert not old.exists() and fresh.exists()
    assert _sweep_orphan_temps(tmp_path / "nope") == 0


def test_record_blocker_sig_and_merge_orphan_parts(tmp_path, monkeypatch):
    """Ponytail 2026-11-08: unchanged blocker sig returns previous value
    (run() exits 0 to stop the restart loop); publish-only re-merges orphan
    part files, torn parts are skipped not fatal."""
    from ewb_live.pipeline import _record_blocker_sig, _merge_orphan_parts
    import pandas as pd
    import ewb_live.pipeline as pl

    sig = tmp_path / "blocker.sig"
    assert _record_blocker_sig(sig, "a") is None
    assert _record_blocker_sig(sig, "a") == "a"   # unchanged -> caller exits 0
    assert _record_blocker_sig(sig, "b") == "a"   # changed -> caller retries

    ev = make_event()
    monkeypatch.setattr(pl, "PARTS", tmp_path / "parts")
    cols = list(SCORE_COLUMNS) + ["fingerprint", "truth_source"]
    good = pd.DataFrame([dict(zip(cols, ["abc123", "heat_wave", "m1", "CSI", 24, 0.5, 0, None, "arco"]))])
    p = pl._part_path(ev, "m1", "arco"); p.parent.mkdir(parents=True, exist_ok=True)
    good.to_parquet(p)
    pl._part_path(ev, "m2", "arco").write_bytes(b"torn")
    cached = pd.DataFrame(columns=cols)
    done = set()
    out = _merge_orphan_parts(cached, done, [ev], ["m1", "m2"], {"abc123": "arco"})
    assert len(out) == 1 and done == {("abc123", "m1")}


def test_drop_truncated_peak_rows():
    """Guardian 2026-11-08: peak-in-window metrics past 240 h compare
    truncated windows -> masked from every published artifact."""
    from ewb_live.pipeline import drop_truncated_peak_rows
    import pandas as pd
    df = pd.DataFrame({
        "metric": ["MaximumMeanAbsoluteError", "MaximumMeanAbsoluteError",
                   "MinimumMeanAbsoluteError", "CriticalSuccessIndex",
                   "RootMeanSquareError"],
        "lead_time_hours": [240, 246, 252, 252, 246],
        "value": [1.0, 10.0, 9.8, 0.2, 2.0]})
    out = drop_truncated_peak_rows(df)
    assert len(out) == 3  # only peak-metric rows >240 h dropped
    assert "MaximumMeanAbsoluteError" in set(out["metric"])  # the 240 h row stays


def test_recompute_merged_max_consec(monkeypatch):
    """Merged heat event: per-cell run over the merged window (all-4-steps
    daily rule), not the member max."""
    from ewb_live.events import temperature as tp
    from ewb_live.viz import build as vb
    import numpy as np

    # 4 days x 4 steps, one cell: days 0-1 fully hot, day 2 has one cool
    # step (daily rule -> not exceeded), day 3 hot -> max run = 2
    t = np.full((16, 1, 1), 20.0, np.float32); t[11] = 5.0
    monkeypatch.setattr(vb, "open_truth_window",
                        lambda times, bbox, cutoff: (t, np.array([0.0]), np.array([0.0]), "era5_mirror"))
    monkeypatch.setattr(vb, "thresholds_for",
                        lambda times, lats, lons, q: (np.full((len(times), 1, 1), 10.0, np.float32), "owb"))
    monkeypatch.setattr(vb, "land_mask", lambda lats, lons: np.ones((1, 1), bool))

    import datetime as dt
    from ewb_live.events.types import Event
    merged = Event(id="m1", event_type="heat_wave", title="t",
                   start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 5),
                   bbox=(0.0, 0.5, 0.0, 0.5), peak_value=None,
                   details={"merged_count": 3, "quantile": 0.85,
                            "max_consecutive_days": 1})
    solo = Event(id="s1", event_type="heat_wave", title="t",
                 start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 5),
                 bbox=(0.0, 0.5, 0.0, 0.5), peak_value=None,
                 details={"quantile": 0.85, "max_consecutive_days": 3})
    n = tp.recompute_merged_max_consec([merged, solo], dt.datetime(2026, 11, 1))
    assert n == 1
    assert merged.details["max_consecutive_days"] == 2
    assert solo.details["max_consecutive_days"] == 3  # unmerged untouched


def test_scorecard_ci_seed_is_per_cell():
    # ponytail 2026-11-08: a shared RNG made a cell's CI depend on how many
    # cells were drawn before it; per-cell seeds must make it stable under
    # universe churn (3+ events per cell so the CI is actually computed).
    rows = []
    for lead in (0.0,):
        for ev in ("e1", "e2", "e3"):
            rows += [
                {"event_id": ev, "event_type": "heat_wave", "model": "ifs_hres",
                 "metric": "CriticalSuccessIndex", "lead_time_hours": lead,
                 "value": 0.5, "init_time": dt.datetime(2026, 10, 1)},
                {"event_id": ev, "event_type": "heat_wave", "model": "graphcast",
                 "metric": "CriticalSuccessIndex", "lead_time_hours": lead,
                 "value": 0.6, "init_time": dt.datetime(2026, 10, 1)},
            ]
    base = build_scorecard(pd.DataFrame(rows))
    kept = {(x["metric"], x["model"]): (x["ci95_lo"], x["ci95_hi"])
            for x in base["rows"]}
    assert kept[("CriticalSuccessIndex", "graphcast")] != (None, None)
    rows += [{"event_id": "e9", "event_type": "atmospheric_river", "model": "pangu",
              "metric": "CriticalSuccessIndex", "lead_time_hours": 0.0,
              "value": 0.3, "init_time": dt.datetime(2026, 10, 1)}]
    churned = build_scorecard(pd.DataFrame(rows))
    again = {(x["metric"], x["model"]): (x["ci95_lo"], x["ci95_hi"])
             for x in churned["rows"]}
    for k, v in kept.items():
        assert again[k] == v, f"cell {k} CI shifted on unrelated churn"
