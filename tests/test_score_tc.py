"""TC lane scorer (scoring/tc.py, scoring/lane_pool.py, pipeline wiring). Offline, synthetic.

The reader is faked: it serves an MSLP stack with a closed low sitting at the observed
position plus a fixed east offset, so position / along / cross errors are known.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import threading

import numpy as np
import pandas as pd
import pytest

from ewb_live import pipeline
from ewb_live.events import select
from ewb_live.events.types import Event
from ewb_live.scoring import lane_pool, tc, track_verif as tv, truth

T0 = dt.datetime(2026, 9, 1, 0)
MODELS = ["graphcast", "aurora"]


def _event(n_fixes=45, wind0=20.0, wind1=90.0, eid="tc1", name="Testa", with_wind=True):
    """Northbound storm, 3-hourly fixes, wind ramping from wind0 (< 34 kt) to wind1."""
    wind = np.linspace(wind0, wind1, n_fixes)
    rows = []
    for i in range(n_fixes):
        t = T0 + dt.timedelta(hours=3 * i)
        rows.append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), 10.0 + 0.25 * i, -110.0, float(wind[i]) if with_wind else None,
                     float(1005 - 0.5 * i)])
    return Event(id=eid, event_type="tropical_cyclone", title="Tropical Cyclone 2026-09-01 to 2026-09-06",
                 start=T0, end=T0 + dt.timedelta(hours=3 * n_fixes), bbox=(5.0, 25.0, -125.0, -95.0),
                 peak_value=float(wind1), details={"name": name, "track": rows, "peak_wind_kt": float(wind1)})


def _depth_low(lat, lon, lat0, lon0, depth=30.0, sigma=1.6):
    yy, xx = np.meshgrid(lat, lon, indexing="ij")
    d2 = (yy - lat0) ** 2 + ((xx - lon0) * np.cos(np.radians(lat0))) ** 2
    return -depth * np.exp(-d2 / (2 * sigma ** 2))


def make_reader(event, east_offset_deg=0.5, gaps=(), errors=(), lost_after_lead=None, calls=None):
    tr = tc.observed_track(event)

    def reader(model, init, leads, win, counter=None):
        if calls is not None:
            calls.append((model, init))
        if (model, init) in gaps:
            raise tc.NoData(f"{model} {init} not in store")
        if (model, init) in errors:
            raise RuntimeError("boom")
        lat0, lat1, lon0, width = win
        lat = np.arange(np.ceil(lat0 * 4) / 4, lat1, 0.25)
        lon = lon0 + np.arange(0, width, 0.25)
        fld = np.empty((len(leads), lat.size, lon.size))
        for j, h in enumerate(leads):
            k = tr.index_of(init + dt.timedelta(hours=h))
            f = np.full((lat.size, lon.size), 1012.0)
            if lost_after_lead is None or h <= lost_after_lead:
                f += _depth_low(lat, lon, tr.lat[k], tr.lon[k] + east_offset_deg)
            fld[j] = f
        return lat, lon, fld, 0

    return reader


# ------------------------------------------------------------------- adapter --
def test_observed_track_and_amended_d2_start():
    ev = _event(wind0=20.0, wind1=90.0)
    tr = tc.observed_track(ev)
    assert len(tr.times) == 45 and tr.wind[0] == 20.0 and tr.pres[0] == 1005.0
    start = tc.verification_start(tr)
    first34 = int(np.argmax(tr.wind >= 34.0))
    assert start == tr.times[first34] and first34 > 0                    # not the first fix
    plan = tc.plan_for(ev)
    assert plan["start"] == start
    assert plan["not_verified"] and all(i < start for i in plan["not_verified"])
    assert plan["verified"] and all(i >= start for i, _ in plan["verified"])
    # pre-start inits are exactly the verifiable ones before the 34 kt fix
    all_inits, _ = tv.verifiable_inits(tr)
    assert len(all_inits) == len(plan["verified"]) + len(plan["not_verified"])


def test_storm_that_never_reaches_34kt_has_nothing_to_verify():
    ev = _event(wind0=15.0, wind1=30.0)
    plan = tc.plan_for(ev)
    assert plan["start"] is None and plan["verified"] == [] and plan["not_verified"]
    df, st = tc.score_tc_event(ev, MODELS, reader=make_reader(ev))
    assert df.empty and all(s["status"] == "empty" for s in st.values())


def test_storm_born_hurricane_verifies_from_its_first_fix():
    ev = _event(wind0=50.0, wind1=90.0)
    plan = tc.plan_for(ev)
    assert plan["not_verified"] == [] and plan["verified"][0][0] == T0


def test_verification_summary_counts():
    s = tc.verification_summary([_event(eid="a"), _event(eid="b", wind0=50.0)])
    assert s["n_events"] == 2 and s["min_wind_kt"] == 34.0
    assert s["events"]["a"]["n_not_verified"] > 0 and s["events"]["b"]["n_not_verified"] == 0
    assert s["n_inits_not_verified"] == s["events"]["a"]["n_not_verified"]


# ----------------------------------------------------------------- scoring ---
def test_score_tc_event_rows_and_errors():
    ev = _event()
    plan = tc.plan_for(ev)
    df, st = tc.score_tc_event(ev, MODELS, reader=make_reader(ev, east_offset_deg=0.5))
    assert list(df.columns) == tv.SCORE_COLUMNS
    assert set(df["metric"]) == set(tv.TRACK_METRICS)
    assert set(df["model"]) == set(MODELS) and (df["event_type"] == "tropical_cyclone").all()
    # D2 amended: nothing before the first >= 34 kt fix
    assert df["init_time"].min() >= np.datetime64(plan["start"], "ns")
    # error = the 0.5 deg east offset (~55 km, storm heads north: purely cross-track, to the RIGHT)
    pos = df[df.metric == "TrackPositionError"]["value"]
    assert pos.median() == pytest.approx(0.5 * 111.195 * np.cos(np.radians(15)), rel=0.12)
    cross = df[df.metric == "CrossTrackError"]["value"]
    along = df[df.metric == "AlongTrackError"]["value"]
    assert (cross > 0).mean() > 0.95 and along.abs().median() < 0.2 * pos.median()
    miss = df[df.metric == "TrackMiss"]["value"]
    assert miss.sum() == 0 and len(miss) == len(pos)                      # nothing lost
    assert all(s["status"] == "scored" and s["n_inits_not_verified"] == len(plan["not_verified"])
               for s in st.values())
    # central pressure is fc - obs (forecast MSLP at the low minus best track), signed
    assert df[df.metric == "CentralPressureError"]["value"].notna().all()


def test_lost_low_gives_miss_rows_only():
    ev = _event()
    df, _ = tc.score_tc_event(ev, ["graphcast"], reader=make_reader(ev, lost_after_lead=24))
    late = df[df.lead_time_hours >= 48]
    assert set(late["metric"]) == {"TrackMiss"} and (late["value"] == 1.0).all()
    early = df[df.lead_time_hours == 12]
    assert set(early["metric"]) == set(tv.TRACK_METRICS)


def test_store_gaps_are_missing_not_misses():
    ev = _event()
    plan = tc.plan_for(ev)
    gap_init = plan["verified"][2][0]
    df, st = tc.score_tc_event(ev, ["graphcast"], reader=make_reader(ev, gaps={("graphcast", gap_init)}))
    assert np.datetime64(gap_init, "ns") not in set(df["init_time"])      # no rows at all for the gap init
    assert df[df.metric == "TrackMiss"]["value"].sum() == 0               # and no miss rows either
    s = st["graphcast"]
    assert s["status"] == "scored" and s["n_inits_no_data"] == 1 and "not counted as misses" in s["reason"]


def test_all_inits_missing_is_no_data_and_read_errors_block_the_pair():
    ev = _event()
    plan = tc.plan_for(ev)
    every = {("graphcast", i) for i, _ in plan["verified"]}
    df, st = tc.score_tc_event(ev, ["graphcast"], reader=make_reader(ev, gaps=every))
    assert df.empty and st["graphcast"]["status"] == "no_data" and st["graphcast"]["rows"] == 0
    df, st = tc.score_tc_event(ev, ["aurora"], reader=make_reader(ev, errors={("aurora", plan["verified"][0][0])}))
    assert df.empty and st["aurora"]["status"] == "read_error"           # retriable, not documented
    assert st["aurora"]["status"] not in pipeline.DOCUMENTED_ZERO_STATUSES


def test_lead_is_the_true_lead_when_the_older_init_rescues():
    ev = _event()
    base = make_reader(ev)

    def reader(model, init, leads, win, counter=None):
        lat, lon, fld, _ = base(model, init, leads, win, counter)
        return lat, lon, fld, 12                                          # the store served the init 12 h earlier

    df, _ = tc.score_tc_event(ev, ["graphcast"], reader=reader)
    plan = tc.plan_for(ev)
    nominal = {np.datetime64(i, "ns") for i, _ in plan["verified"]}
    assert set(df["init_time"]) <= {n - np.timedelta64(12, "h") for n in nominal}
    assert df["lead_time_hours"].min() == 18.0                           # nominal 6 h + 12 h offset


# --------------------------------------------------------------------- retry --
def test_retry_transient_backs_off_and_recovers():
    calls, slept, counter = [], [], {}

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("StorageError: error fetching virtual reference: InternalError")
        return "ok"

    assert tc.retry_transient(flaky, sleep=slept.append, counter=counter, base_s=2.0) == "ok"
    assert slept == [2.0, 4.0] and counter["retries"] == 2


def test_retry_gives_up_after_the_last_attempt_and_never_retries_a_gap():
    n = []

    def always():
        n.append(1)
        raise OSError("StorageError: x")

    with pytest.raises(OSError):
        tc.retry_transient(always, tries=3, sleep=lambda s: None)
    assert len(n) == 3
    m = []

    def gap():
        m.append(1)
        raise KeyError("init not in store")

    with pytest.raises(KeyError):
        tc.retry_transient(gap, sleep=lambda s: None)
    assert len(m) == 1
    assert not tc.is_transient(tc.NoData("x")) and tc.is_transient(TimeoutError()) and not tc.is_transient(ValueError("bad"))


# ---------------------------------------------------------------- lane pool ---
@pytest.fixture
def fake_lane(monkeypatch):
    """Route the pool's jobs through the fake reader and run them in threads."""
    ev = _event()
    holder = {"reader": make_reader(ev)}

    def init_job(track, model, init, leads, reader=None):
        return tc.score_init(track, model, init, leads, holder["reader"])

    lane = lane_pool.Lane(event_type="tropical_cyclone", truth="ibtracs", models=lane_pool.tc_models,
                          plan=tc.plan_for, init_job=init_job, finalize=tc.finalize_pair, label="tc")
    monkeypatch.setitem(lane_pool.LANES, "tc", lane)
    return ev, holder


def _threads(n=3):
    return lambda: cf.ThreadPoolExecutor(max_workers=n)


def test_pool_completes_pairs_and_reports_stats(fake_lane):
    ev, _ = fake_lane
    got = {}
    stats = lane_pool.run_pairs("tc", [(ev, m) for m in MODELS],
                                lambda e, m, df, st: got.update({m: (df, st)}),
                                workers=3, executor_factory=_threads(), poll_s=0.05)
    plan = tc.plan_for(ev)
    assert set(got) == set(MODELS) and stats["pairs_done"] == 2 and not stats["stopped"]
    assert stats["jobs"] == 2 * len(plan["verified"]) == stats["jobs_done"]
    assert all(st["status"] == "scored" for _, st in got.values())
    serial, _ = tc.score_tc_event(ev, ["graphcast"], reader=make_reader(ev))
    pd.testing.assert_frame_equal(got["graphcast"][0].sort_values(["init_time", "lead_time_hours", "metric"]).reset_index(drop=True),
                                  serial.sort_values(["init_time", "lead_time_hours", "metric"]).reset_index(drop=True))
    assert stats["peak_total_rss_gb"] > 0 and stats["mean_job_s_store"] is not None


def test_pool_pair_with_nothing_verifiable_is_finalised_empty(fake_lane):
    _, holder = fake_lane
    ev = _event(wind0=10.0, wind1=25.0, eid="weak")
    got = {}
    stats = lane_pool.run_pairs("tc", [(ev, "graphcast")], lambda e, m, df, st: got.update({m: st}),
                                executor_factory=_threads(), poll_s=0.05)
    assert got["graphcast"]["status"] == "empty" and stats["jobs"] == 0


def test_pool_stop_drains_keeps_completed_pairs_and_leaves_the_rest(fake_lane):
    ev, holder = fake_lane
    stop = threading.Event()
    base = holder["reader"]
    seen = []

    def reader(model, init, leads, win, counter=None):
        seen.append(1)
        if len(seen) == 2:
            stop.set()                                  # the guard fires mid-run
        return base(model, init, leads, win, counter)

    holder["reader"] = reader
    ev2 = _event(eid="tc2")
    done = []
    stats = lane_pool.run_pairs("tc", [(ev, "graphcast"), (ev2, "graphcast"), (ev, "aurora"), (ev2, "aurora")],
                                lambda e, m, df, st: done.append((e.id, m)),
                                workers=1, executor_factory=_threads(1), stop=stop, poll_s=0.05)
    assert stats["stopped"] and stats["unfinished_pairs"] >= 1
    assert stats["jobs_done"] < stats["jobs"]
    assert len(done) == stats["pairs_done"] < 4         # completed pairs were persisted, others untouched


def test_rss_sampler_fires_the_cap_once():
    fired = []
    s = lane_pool.RssSampler(interval=0.05, cap_gb=1e-6, on_over=fired.append)
    with s:
        import time

        time.sleep(0.3)
    assert len(fired) == 1 and s.peak_total > 0


def test_tc_lane_models_are_the_store_models_and_the_ifs_baseline():
    ok, why = lane_pool.tc_models(["graphcast", "graphcast_gfs", "aurora", "aurora_gfs", "pangu_weather",
                                   "pangu_weather_gfs", "fourcastnet_v2", "fourcastnet_v2_gfs", "ifs_hres",
                                   "aifs_single", "aigfs"])
    assert len(ok) == 9 and "ifs_hres" in ok and set(why) == {"aifs_single", "aigfs"}


# -------------------------------------------------------------- pipeline wiring
@pytest.fixture
def results_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(pipeline, "PARTS", tmp_path / "parts")
    (tmp_path / "results").mkdir()
    (tmp_path / "parts").mkdir()
    return tmp_path


def test_tc_left_the_unscored_set_and_has_its_own_truth_tag():
    assert "tropical_cyclone" not in select.UNSCORED_TYPES
    assert "midlat_cyclone" in select.UNSCORED_TYPES and "flood" not in select.UNSCORED_TYPES
    ev = _event()
    assert truth.truth_source_for_event(ev, dt.datetime(2030, 1, 1)) == "ibtracs"
    assert truth.truth_source_for_event(ev, dt.datetime(1999, 1, 1)) == "ibtracs"   # not ARCO-dependent
    assert {"AlongTrackError", "CrossTrackError", "CentralPressureError"} <= pipeline.SIGNED_ERROR_METRICS


def test_score_job_dispatches_tc_to_the_track_scorer(monkeypatch):
    ev = _event()
    monkeypatch.setattr(tc, "default_reader", make_reader(ev))
    status = {}
    eid, df = pipeline._score_job(ev.to_dict(), ["graphcast"], "ibtracs", status_out=status)
    assert eid == ev.id and df is not None and "TrackMiss" in set(df["metric"])
    assert status["graphcast"]["status"] == "scored"


def test_run_lanes_writes_part_and_sidecar_and_a_rerun_resumes(results_tree, fake_lane):
    ev, _ = fake_lane
    truth_of = lambda e: "ibtracs"  # noqa: E731
    pairs = pipeline.lane_pairs([ev], MODELS, truth_of)
    assert len(pairs) == 2
    stats = pipeline.run_lanes(pairs, lane_key="tc", truth_of=truth_of, workers=2,
                               executor_factory=_threads(2), poll_s=0.05)
    assert stats["pairs_done"] == 2
    part = pd.read_parquet(pipeline._part_path(ev, "graphcast", "ibtracs"))
    assert part["fingerprint"].iloc[0] == pipeline.fingerprint(ev) and (part["truth_source"] == "ibtracs").all()
    side = json.loads((results_tree / "results" / "pair_sidecar" / f"{ev.id}.status.json").read_text())
    assert set(side) == set(MODELS) and side["graphcast"]["fp"] == f"{pipeline.fingerprint(ev)}|ibtracs"
    assert pipeline.lane_pairs([ev], MODELS, truth_of) == []                          # resumable: all parts exist
    assert len(pipeline.lane_pairs([ev], MODELS, truth_of, force=True)) == 2


def test_compact_track_rows_collapses_only_the_lane_rows():
    lane = tv.rows_for_pair("e1", "tropical_cyclone", "m", [
        {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0, "tracked": True, "d_km": 10.0,
         "along_km": 1.0, "cross_km": 2.0, "dp_hpa": 3.0},
        {"init_true": "2026-09-01T12:00:00", "lead_true": 24.0, "tracked": True, "d_km": 30.0,
         "along_km": 3.0, "cross_km": 4.0, "dp_hpa": 5.0}])
    heat = pd.DataFrame({"event_id": ["h"], "event_type": ["heat_wave"], "model": ["m"], "metric": ["x"],
                         "lead_time_hours": [24.0], "value": [1.0], "init_time": [np.datetime64("2026-09-01", "ns")]})
    out = pipeline.compact_track_rows(pd.concat([lane, heat], ignore_index=True))
    assert len(out) == 1 + 5                                                          # heat untouched + 5 lane means
    pos = out[out.metric == "TrackPositionError"]["value"].iloc[0]
    assert pos == 20.0 and out[out.event_type == "tropical_cyclone"]["init_time"].isna().all()
    assert out[out.event_type == "heat_wave"]["init_time"].notna().all()


def test_publish_only_pair_coverage_is_decidable_for_the_tc_lane(results_tree, fake_lane, monkeypatch):
    """TC pairs are no longer 'unsupported'; unsupported models are documented; parts merge into scores."""
    ev, _ = fake_lane
    res = results_tree / "results"
    (res / "events_selected.json").write_text(json.dumps([ev.to_dict()]))
    truth_of = lambda e: "ibtracs"  # noqa: E731
    pipeline.run_lanes(pipeline.lane_pairs([ev], MODELS, truth_of), lane_key="tc", truth_of=truth_of,
                       workers=2, executor_factory=_threads(2), poll_s=0.05)
    from ewb_live import sources
    from ewb_live.scoring import truth as truth_mod

    all_models = MODELS + ["aigfs"]
    monkeypatch.setattr(sources, "list_models", lambda: all_models)
    monkeypatch.setattr(truth_mod, "probe_arco_cutoff", lambda: dt.datetime(2026, 11, 1))
    monkeypatch.setattr(pipeline, "_ibtracs_meta", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_lane_status_meta", lambda: {})
    monkeypatch.setattr(pipeline, "_clim_meta", lambda: {})
    monkeypatch.setattr(pipeline, "_sel_thresholds", lambda: {})
    rc = pipeline.run(days=90, publish_only=True)
    assert rc == 0
    meta = json.loads((res / "meta.json").read_text())
    cov = meta["pair_coverage"]
    assert cov["expected"] == 3 and cov["scored"] == 2 and cov["documented_zero_rows"] == 1
    assert meta["full_run_complete"] is True
    assert meta["track_verification"]["tropical_cyclone"]["n_events"] == 1
    sc = pd.read_parquet(res / "scores.parquet")
    assert set(sc["metric"]) == set(tv.TRACK_METRICS) and set(sc["truth_source"]) == {"ibtracs"}
    pj = json.loads((res / "scores.json").read_text())
    assert len(pj) < len(sc)                                      # scores.json is the compacted payload
    ps = json.loads((res / "pair_status.json").read_text())
    assert ps[f"{ev.id}|aigfs"]["status"] == "unsupported"
    assert ps[f"{ev.id}|graphcast"]["status"] == "scored"
    card = json.loads((res / "scorecard.json").read_text())["rows"]
    assert {r["metric"] for r in card} == set(tv.TRACK_METRICS)


def _heat_event():
    """An EWB-lane event (NOT a lane-pool type): its pairs are never restamped by the lane block."""
    return Event(id="heat0000001", event_type="heat_wave", title="Heat 2026-09-01 to 2026-09-04",
                 start=T0, end=T0 + dt.timedelta(days=3), bbox=(10.0, 20.0, 0.0, 10.0), peak_value=5.0, details={})


def _fold_setup(results_tree, monkeypatch, *, prev, sidecar):
    """heat_wave + model aigfs, one documented pair: pair_status.json says ``prev``, the EWB-job
    sidecar says ``sidecar`` (no fp, as the workers write it)."""
    ev = _heat_event()
    res = results_tree / "results"
    (res / "events_selected.json").write_text(json.dumps([ev.to_dict()]))
    fp = f"{pipeline.fingerprint(ev)}|era5_arco"
    if prev is not None:
        (res / "pair_status.json").write_text(json.dumps(
            {f"{ev.id}|aigfs": {**prev, "fp": fp if prev.get("fp") == "OK" else prev.get("fp")}
             if "fp" in prev else prev}))
    (res / "pair_sidecar").mkdir(exist_ok=True)
    (res / "pair_sidecar" / f"{ev.id}.status.json").write_text(json.dumps({"aigfs": sidecar}))
    from ewb_live import sources
    from ewb_live.scoring import truth as truth_mod

    monkeypatch.setattr(sources, "list_models", lambda: ["aigfs"])
    monkeypatch.setattr(truth_mod, "probe_arco_cutoff", lambda: dt.datetime(2030, 1, 1))
    for name, val in (("_ibtracs_meta", lambda *a, **k: None), ("_lane_status_meta", lambda: {}),
                      ("_clim_meta", lambda: {}), ("_sel_thresholds", lambda: {})):
        monkeypatch.setattr(pipeline, name, val)
    return ev, res, fp


DOC = {"status": "unsupported", "rows": 0, "reason": "no 2 m temperature"}


def _publish_only_blockers(res):
    assert pipeline.run(days=90, publish_only=True) == 0
    return json.loads((res / "meta.json").read_text())["pair_coverage"]


def test_publish_only_foldin_keeps_a_proven_fp_for_the_same_status(results_tree, monkeypatch):
    """Positive: an EWB-job sidecar has no fp; the publish-only fold-in carries the stamp the pair already
    proved, so the documented zero-row pair is NOT a blocker (full_run_complete stays decidable)."""
    ev, res, fp = _fold_setup(results_tree, monkeypatch, prev={**DOC, "fp": "OK"}, sidecar=DOC)
    cov = _publish_only_blockers(res)
    assert cov["unattempted_or_failed"] == [] and cov["documented_zero_rows"] == 1
    assert json.loads((res / "pair_status.json").read_text())[f"{ev.id}|aigfs"]["fp"] == fp


def test_publish_only_foldin_negatives_stay_blockers(results_tree, monkeypatch):
    # stale fp: the event changed since the stamp was proven -> must still be a blocker
    _, res, _ = _fold_setup(results_tree, monkeypatch, prev={**DOC, "fp": "2020-01-01T00|old|era5_arco"}, sidecar=DOC)
    assert len(_publish_only_blockers(res)["unattempted_or_failed"]) == 1


def test_publish_only_foldin_negative_status_differs(results_tree, monkeypatch):
    _, res, _ = _fold_setup(results_tree, monkeypatch, prev={**DOC, "fp": "OK"},
                            sidecar={"status": "no_data", "rows": 0, "reason": "no init"})
    assert len(_publish_only_blockers(res)["unattempted_or_failed"]) == 1


def test_publish_only_foldin_negative_previous_entry_has_no_fp(results_tree, monkeypatch):
    _, res, _ = _fold_setup(results_tree, monkeypatch, prev=dict(DOC), sidecar=DOC)
    assert len(_publish_only_blockers(res)["unattempted_or_failed"]) == 1
    _, res2, _ = _fold_setup(results_tree, monkeypatch, prev=None, sidecar=DOC)      # no previous entry at all
    (res2 / "pair_status.json").unlink(missing_ok=True)
    assert len(_publish_only_blockers(res2)["unattempted_or_failed"]) == 1


def test_normal_run_still_reschedules_fp_less_documented_pairs(results_tree, monkeypatch):
    """The carry-over is publish-only: a normal run behaves as before (an fp-less unsupported pair
    re-enters scoring once and re-stamps), even though pair_status.json holds a proven fp."""
    ev, res, fp = _fold_setup(results_tree, monkeypatch, prev={**DOC, "fp": "OK"}, sidecar=DOC)
    from ewb_live import events as events_pkg
    from ewb_live.events import qualification, validate
    from ewb_live.scoring import truth_mirror

    monkeypatch.setattr(truth_mirror, "ensure_mirror", lambda **k: None)
    monkeypatch.setattr(events_pkg, "detect_events", lambda *a, **k: [ev])
    monkeypatch.setattr(validate, "validate_event", lambda e: [])
    monkeypatch.setattr(qualification, "qualify_events", lambda evs: None)
    spawned = []

    def fake_worker(jobfile, logfile, timeout):
        spawned.append(json.loads(jobfile.read_text())["models"])
        raise RuntimeError("stop here")

    monkeypatch.setattr(pipeline, "_run_worker", fake_worker)
    pipeline.run(days=90, top_n=15)
    assert spawned and spawned[0] == ["aigfs"], spawned


def test_full_run_scores_the_tc_lane_through_the_pool_block(results_tree, fake_lane, monkeypatch):
    """pipeline.run() (not publish-only): TC pairs go to lane_pool, rows land in scores,
    unsupported models are documented, the run completes (exit 0, full_run_complete)."""
    ev, _ = fake_lane
    from ewb_live import events as events_pkg, sources
    from ewb_live.events import qualification, validate
    from ewb_live.scoring import truth as truth_mod, truth_mirror

    monkeypatch.setattr(sources, "list_models", lambda: MODELS + ["aigfs"])
    monkeypatch.setattr(truth_mod, "probe_arco_cutoff", lambda: dt.datetime(2026, 11, 1))
    monkeypatch.setattr(truth_mirror, "ensure_mirror", lambda **k: None)
    monkeypatch.setattr(events_pkg, "detect_events", lambda *a, **k: [ev])
    monkeypatch.setattr(validate, "validate_event", lambda e: [])
    monkeypatch.setattr(qualification, "qualify_events", lambda evs: None)
    for name, val in (("_ibtracs_meta", lambda *a, **k: None), ("_lane_status_meta", lambda: {}),
                      ("_clim_meta", lambda: {}), ("_sel_thresholds", lambda: {})):
        monkeypatch.setattr(pipeline, name, val)
    real = pipeline.run_lanes
    seen = {}

    def threaded(pairs, **kw):
        seen["workers"], seen["recycle"] = kw["workers"], kw["recycle"]
        return real(pairs, executor_factory=_threads(2), poll_s=0.05, **kw)

    monkeypatch.setattr(pipeline, "run_lanes", threaded)
    monkeypatch.setenv("EWB_LIVE_SCORING_WORKERS", "5")
    assert pipeline.run(days=90, top_n=15) == 0
    assert seen == {"workers": 5, "recycle": 30}                      # one pool, recycle every 30 jobs
    res = results_tree / "results"
    meta = json.loads((res / "meta.json").read_text())
    assert meta["full_run_complete"] is True and meta["pair_coverage"]["scored"] == 2
    assert meta["pair_coverage"]["documented_zero_rows"] == 1            # aigfs: not a lane model
    sc = pd.read_parquet(res / "scores.parquet")
    assert set(sc["model"]) == set(MODELS) and set(sc["truth_source"]) == {"ibtracs"}
    assert set(sc["metric"]) == set(tv.TRACK_METRICS)
    perf = json.loads((results_tree / "perf.json").read_text())["perf"]
    assert perf["lane_tc"]["pairs_done"] == 2 and perf["lane_tc"]["jobs"] > 0
    # second run: nothing left to score (parts + cached rows), still complete
    monkeypatch.setattr(pipeline, "run_lanes", lambda pairs, **kw: pytest.fail("rescored a finished pair"))
    assert pipeline.run(days=90, top_n=15) == 0


# ------------------------------------------------- review follow-ups (PR #16)
def test_only_nodata_and_keyerror_are_store_gaps_other_filenotfound_is_a_read_error():
    ev = _event()
    plan = tc.plan_for(ev)
    init, leads = plan["verified"][0]

    def raiser(exc):
        def reader(model, init, leads, win, counter=None):
            raise exc
        return reader

    assert tc.score_init(plan["track"], "graphcast", init, leads, raiser(tc.NoData("gap")))["status"] == "no_data"
    assert tc.score_init(plan["track"], "graphcast", init, leads, raiser(KeyError("x")))["status"] == "no_data"
    rec = tc.score_init(plan["track"], "graphcast", init, leads, raiser(FileNotFoundError("/cache/x gone")))
    assert rec["status"] == "error" and "FileNotFoundError" in rec["reason"]        # retried, never silently no_data


def test_transient_matching_uses_status_codes_not_bare_numbers():
    assert tc.is_transient(OSError("HTTP 503 Service Unavailable")) and tc.is_transient(OSError("status code: 500"))
    assert not tc.is_transient(ValueError("window of 500 points exceeds 503 cells"))


def test_lane_terminal_pairs_are_final_only_for_the_same_fingerprint(results_tree):
    ev = _event()
    fp = f"{pipeline.fingerprint(ev)}|ibtracs"
    assert pipeline.lane_terminal({"status": "no_data", "fp": fp}, ev, "ibtracs")
    assert pipeline.lane_terminal({"status": "empty", "fp": fp}, ev, "ibtracs")
    assert not pipeline.lane_terminal({"status": "read_error", "fp": fp}, ev, "ibtracs")      # retriable
    assert not pipeline.lane_terminal({"status": "no_data", "fp": "old|ibtracs"}, ev, "ibtracs")
    assert not pipeline.lane_terminal({"status": "no_data"}, ev, "ibtracs")
    assert not pipeline.lane_terminal(None, ev, "ibtracs")
    # lane_pairs (the backfill CLI) skips them too, from the sidecar
    (results_tree / "results" / "pair_sidecar").mkdir()
    (results_tree / "results" / "pair_sidecar" / f"{ev.id}.status.json").write_text(
        json.dumps({"graphcast": {"status": "no_data", "rows": 0, "fp": fp}}))
    pairs = pipeline.lane_pairs([ev], MODELS, lambda e: "ibtracs")
    assert [m for _, m in pairs] == ["aurora"]
    assert len(pipeline.lane_pairs([ev], MODELS, lambda e: "ibtracs", force=True)) == 2


def test_normal_run_does_not_respawn_the_pool_for_terminal_lane_pairs(results_tree, fake_lane, monkeypatch):
    ev, _ = fake_lane
    res = results_tree / "results"
    fp = f"{pipeline.fingerprint(ev)}|ibtracs"
    (res / "pair_status.json").write_text(json.dumps(
        {f"{ev.id}|{m}": {"status": "no_data", "rows": 0, "reason": "x", "fp": fp} for m in MODELS}))
    from ewb_live import events as events_pkg, sources
    from ewb_live.events import qualification, validate
    from ewb_live.scoring import truth as truth_mod, truth_mirror

    monkeypatch.setattr(sources, "list_models", lambda: MODELS)
    monkeypatch.setattr(truth_mod, "probe_arco_cutoff", lambda: dt.datetime(2026, 11, 1))
    monkeypatch.setattr(truth_mirror, "ensure_mirror", lambda **k: None)
    monkeypatch.setattr(events_pkg, "detect_events", lambda *a, **k: [ev])
    monkeypatch.setattr(validate, "validate_event", lambda e: [])
    monkeypatch.setattr(qualification, "qualify_events", lambda evs: None)
    for name, val in (("_ibtracs_meta", lambda *a, **k: None), ("_lane_status_meta", lambda: {}),
                      ("_clim_meta", lambda: {}), ("_sel_thresholds", lambda: {})):
        monkeypatch.setattr(pipeline, name, val)
    monkeypatch.setattr(pipeline, "run_lanes", lambda pairs, **kw: pytest.fail("pool respawned for terminal pairs"))
    assert pipeline.run(days=90, top_n=15) == 0
    assert json.loads((res / "meta.json").read_text())["pair_coverage"]["documented_zero_rows"] == 2


# --- refusing the live tree (lane_pool CLI)
def _cli_args(results, parts):
    import types

    return types.SimpleNamespace(results=str(results), parts=str(parts), lane="tc", models=None, max_events=0,
                                 workers=1, recycle=30, dask_threads=1, rss_cap_gb=40.0, force=False,
                                 stats_json=None, days=90, no_publish=True)


def test_cli_refuses_results_or_parts_under_the_live_tree(tmp_path, monkeypatch):
    live = tmp_path / "live"
    (live / "data" / "results").mkdir(parents=True)
    (live / "data" / "cache" / "score_parts").mkdir(parents=True)
    priv = tmp_path / "priv"
    priv.mkdir()
    monkeypatch.setattr(lane_pool, "LIVE_ROOT", live)
    monkeypatch.chdir(priv)
    monkeypatch.delenv("LANE_BACKFILL_ALLOW_LIVE", raising=False)
    assert lane_pool.run_lane_cli(_cli_args(live / "data" / "results", priv)) == lane_pool.EXIT_REFUSED
    assert lane_pool.run_lane_cli(_cli_args(priv, live / "data" / "cache" / "score_parts")) == lane_pool.EXIT_REFUSED
    (priv / "alias").symlink_to(live / "data" / "results")                          # a symlink into live
    assert lane_pool.run_lane_cli(_cli_args(priv / "alias", priv)) == lane_pool.EXIT_REFUSED
    monkeypatch.chdir(live)                                                          # running from the live tree
    assert lane_pool.run_lane_cli(_cli_args(priv, priv)) == lane_pool.EXIT_REFUSED
    assert lane_pool.refuse_live_tree(priv, priv) is not None
    monkeypatch.chdir(priv)
    assert lane_pool.refuse_live_tree(priv, priv) is None
    monkeypatch.setenv("LANE_BACKFILL_ALLOW_LIVE", "1")
    assert lane_pool.refuse_live_tree(live / "data" / "results", priv) is None


# --- a worker that dies (OOM kill) must not hang or lose completed pairs
@pytest.fixture
def dying_lane(monkeypatch, tmp_path):
    ev = _event()
    kill_marker = tmp_path / "killed_once"
    holder = {"mode": "always"}

    def init_job(track, model, init, leads, reader=None):
        if model == "aurora" and (holder["mode"] == "always" or not kill_marker.exists()):
            kill_marker.write_text("x")
            os._exit(137)                                # what the OOM killer does to a worker
        return tc.score_init(track, model, init, leads, make_reader(ev))

    lane = lane_pool.Lane(event_type="tropical_cyclone", truth="ibtracs", models=lane_pool.tc_models,
                          plan=tc.plan_for, init_job=init_job, finalize=tc.finalize_pair, label="tc")
    monkeypatch.setitem(lane_pool.LANES, "tc", lane)
    return ev, holder


def _fork_pool(n=2):
    import multiprocessing as mp

    return lambda: cf.ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("fork"))


def test_pool_survives_one_killed_worker_by_rebuilding(dying_lane):
    ev, holder = dying_lane
    holder["mode"] = "once"
    done = {}
    stats = lane_pool.run_pairs("tc", [(ev, m) for m in MODELS], lambda e, m, df, st: done.update({m: st}),
                                workers=2, executor_factory=_fork_pool(), poll_s=0.05)
    assert stats["pool_rebuilds"] == 1 and stats["errors"] >= 1 and stats["abort"] is None
    assert set(done) == set(MODELS) and all(s["status"] == "scored" for s in done.values())   # nothing lost


def test_pool_that_keeps_dying_stops_cleanly_marks_abort_and_keeps_completed_pairs(dying_lane):
    ev, _ = dying_lane
    done = {}
    stats = lane_pool.run_pairs("tc", [(ev, "graphcast"), (ev, "aurora")], lambda e, m, df, st: done.update({m: st}),
                                workers=1, executor_factory=_fork_pool(1), poll_s=0.05)
    assert stats["abort"] and "worker pool broken" in stats["abort"] and stats["stopped"]
    assert stats["pool_rebuilds"] == lane_pool.MAX_POOL_REBUILDS
    assert "aurora" not in done                         # never half-written: the pair stays pending for the next run
    assert stats["unfinished_pairs"] >= 1
