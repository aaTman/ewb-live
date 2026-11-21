"""Flood lane WIRING (WP4): lane_pool registration, pipeline dispatch / metric sets / meta.

Offline and synthetic: PARDIG, the climatology, the land mask and the icechunk store are
never read. The store reader is injected through ``score_init`` (the lane's ``init_job``)
and the pool runs in-process through ``run_pairs(executor_factory=...)``, exactly like
tests/test_score_tc.py does for the TC lane.

The scorer core itself (metrics, thresholds, day padding) is tested against real-shaped
data separately; here only the plumbing that makes the flood lane a scored lane is
exercised: registration, UNSCORED_TYPES / truth tag, metric direction sets, the scorecard,
the compacted scores.json payload, ``_score_job`` dispatch and ``run()``'s per-lane pool block.
"""
import concurrent.futures as cf
import dataclasses
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from ewb_live import pipeline, regions, sources
from ewb_live.events import select
from ewb_live.events.types import Event
from ewb_live.scoring import flood as flood_lane
from ewb_live.scoring import lane_pool, tc as tc_lane, truth

T0 = dt.datetime(2026, 9, 1, 0)
MODELS = ["graphcast", "graphcast_gfs"]
BBOX = (10.0, 10.5, 20.0, 20.5)


def _event(eid="flood000001", ndays=1, bbox=BBOX):
    return Event(id=eid, event_type="flood",
                 title=f"Flood {T0:%Y-%m-%d} to {T0 + dt.timedelta(days=ndays):%Y-%m-%d}",
                 start=T0, end=T0 + dt.timedelta(days=ndays), bbox=bbox, peak_value=42.0,
                 details={"accum_days": 1, "threshold_mm": 20.0})


# ------------------------------------------------------- synthetic lane parts ---
def _track(event):
    """A FloodTruth on the event's real domain, with flat 25 mm truth over a 20 mm threshold."""
    lats, lons, window = flood_lane.domain_for(event.bbox)
    vdays = flood_lane.verification_days(event)
    shape = (len(vdays), lats.size, lons.size)
    return flood_lane.FloodTruth(
        lat=lats, lon=lons, days=vdays, present=np.ones(len(vdays), bool),
        truth=np.full(shape, 25.0, "float32"), thr=np.full(shape, 20.0, "float32"),
        land=np.ones((lats.size, lons.size), bool), footprint=np.ones((lats.size, lons.size), bool),
        window=window, event_days=flood_lane.event_days(event))


def _plan(event, track):
    """Two 00Z inits that both verify the event's first day (leads 24 h and 48 h)."""
    day0 = flood_lane.event_days(event)[0]
    verified = []
    for k in (1, 2):
        init = day0 - dt.timedelta(days=k - 1)
        verified.append((dt.datetime.combine(init, dt.time()), flood_lane.bin_leads_of_day(
            dt.datetime.combine(init, dt.time()), day0)))
    return {"track": track, "start": dt.datetime.combine(day0, dt.time()),
            "verified": verified, "not_verified": []}


def make_reader(track, day_mm=25.0, gaps=(), errors=()):
    """apcp server: four 6-h bins totalling ``day_mm`` over the whole domain (= exceedance
    everywhere, forecast == truth). ``gaps`` / ``errors`` simulate store gaps / read failures."""

    def reader(model, init, leads, win, counter=None):
        if (model, init) in gaps:
            raise tc_lane.NoData(f"{model} {init} not in store")
        if (model, init) in errors:
            raise RuntimeError("boom")
        fld = np.full((len(leads), track.lat.size, track.lon.size),
                      day_mm / 4.0 / 1000.0, "float32")
        return track.lat, track.lon, fld, 0

    return reader


@pytest.fixture
def fake_flood_lane(monkeypatch):
    """Register the flood lane with an injected truth bundle, plan and reader."""
    ev = _event()
    track = _track(ev)
    plan = _plan(ev, track)
    holder = {"reader": make_reader(track)}

    def init_job(trk, model, init, leads, reader=None):
        return flood_lane.score_init(trk, model, init, leads, reader or holder["reader"])

    lane = lane_pool.Lane(event_type=flood_lane.EVENT_TYPE, truth="pardig",
                          models=flood_lane.flood_models, plan=lambda e: plan,
                          init_job=init_job, finalize=flood_lane.finalize_pair,
                          label="flood", serial=flood_lane.score_flood_event)
    monkeypatch.setitem(lane_pool.LANES, "flood", lane)
    monkeypatch.setitem(lane_pool.LANE_BY_EVENT_TYPE, "flood", lane)
    return ev, plan, holder


def _threads(n=2):
    return lambda: cf.ThreadPoolExecutor(max_workers=n)


@pytest.fixture
def results_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(pipeline, "PARTS", tmp_path / "parts")
    (tmp_path / "results").mkdir()
    (tmp_path / "parts").mkdir()
    return tmp_path


# ------------------------------------------------------------- registration ---
def test_flood_lane_is_registered_with_its_scorers_and_serial():
    assert "flood" in lane_pool.LANES and "flood" in lane_pool.LANE_TYPES
    ln = lane_pool.LANE_BY_EVENT_TYPE["flood"]
    assert ln.event_type == "flood" and ln.truth == "pardig"
    assert ln.models is flood_lane.flood_models and ln.plan is flood_lane.plan_for
    assert ln.init_job is flood_lane.score_init and ln.finalize is flood_lane.finalize_pair
    assert ln.serial is flood_lane.score_flood_event
    assert lane_pool.LANE_BY_EVENT_TYPE["tropical_cyclone"].serial is tc_lane.score_tc_event


def test_flood_scores_only_the_graphcast_family_and_documents_the_rest():
    allm = sources.list_models()
    assert len(allm) == 11
    ok, why = lane_pool.LANE_BY_EVENT_TYPE["flood"].models(allm)
    assert ok == ["graphcast", "graphcast_gfs"]
    assert set(why) == set(allm) - set(ok)
    assert all("GraphCast only" in r for r in why.values())
    # a pending list that already only holds unsupported models scores nothing
    ok2, why2 = lane_pool.LANE_BY_EVENT_TYPE["flood"].models(["ifs_hres", "aigfs"])
    assert ok2 == [] and set(why2) == {"ifs_hres", "aigfs"}


def test_flood_left_the_unscored_set():
    assert "flood" not in select.UNSCORED_TYPES
    assert "midlat_cyclone" not in select.UNSCORED_TYPES      # WP2 (merged) scores it in its own lane


def test_flood_truth_tag_is_pardig_and_is_not_cutoff_dependent():
    ev = _event()
    assert truth.truth_source_for_event(ev, dt.datetime(2030, 1, 1)) == "pardig"
    assert truth.truth_source_for_event(ev, dt.datetime(1999, 1, 1)) == "pardig"
    # the ERA5 lanes keep their cutoff rule
    heat = Event(id="heat0000001", event_type="heat_wave", title="Heat", start=T0,
                 end=T0 + dt.timedelta(days=3), bbox=(10.0, 20.0, 0.0, 10.0), peak_value=5.0)
    assert truth.truth_source_for_event(heat, dt.datetime(2030, 1, 1)) == "era5_arco"
    assert truth.truth_source_for_event(heat, dt.datetime(1999, 1, 1)) == "ifs_hres_analysis"


def test_flood_metric_direction_sets_are_wired():
    assert {"ProbabilityOfDetection", "FractionsSkillScore", "FractionsSkillScore5"} \
        <= pipeline.HIGHER_IS_BETTER
    assert "CriticalSuccessIndex" in pipeline.HIGHER_IS_BETTER
    assert "FalseAlarmRatio" not in pipeline.HIGHER_IS_BETTER     # lower is better
    assert {"PrecipAreaBias", "PeakPrecipError"} <= pipeline.SIGNED_ERROR_METRICS
    # every metric the scorer can emit is classified (a new one silently reads as lower-is-better)
    for m in flood_lane.FLOOD_METRICS:
        assert m in pipeline.HIGHER_IS_BETTER or m in pipeline.SIGNED_ERROR_METRICS \
            or m == "FalseAlarmRatio", f"{m} has no direction in the scorecard"


# ------------------------------------------------------------------ scorer ---
def test_score_init_and_finalize_pair_through_the_lane_fields(fake_flood_lane):
    ev, plan, holder = fake_flood_lane
    ln = lane_pool.LANES["flood"]
    recs = [ln.init_job(plan["track"], "graphcast", init, leads) for init, leads in plan["verified"]]
    assert all(r["status"] == "scored" for r in recs)
    df, st = ln.finalize(ev, "graphcast", plan, recs)
    assert st["status"] == "scored" and st["n_inits"] == 2 and len(df) == st["rows"]
    assert set(df["metric"]) == set(flood_lane.FLOOD_METRICS)
    # forecast == truth everywhere above the threshold: perfect contingency, no areal bias
    assert (df[df.metric == flood_lane.M_CSI]["value"] == 1.0).all()
    assert (df[df.metric == flood_lane.M_FSS5]["value"] == 1.0).all()
    assert (df[df.metric == flood_lane.M_POD]["value"] == 1.0).all()
    assert (df[df.metric == flood_lane.M_FAR]["value"] == 0.0).all()
    assert df[df.metric.isin([flood_lane.M_BIAS, flood_lane.M_PEAK])]["value"].abs().max() < 1e-5
    assert (df["event_type"] == "flood").all() and set(df["lead_time_hours"]) == {24.0, 48.0}
    # a store gap on every init is a documented no_data, a read error blocks the pair
    gap = {("graphcast", i) for i, _ in plan["verified"]}
    df0, st0 = ln.finalize(ev, "graphcast", plan, [
        ln.init_job(plan["track"], "graphcast", i, l, make_reader(plan["track"], gaps=gap))
        for i, l in plan["verified"]])
    assert df0 is None and st0["status"] == "no_data"
    bad = [ln.init_job(plan["track"], "graphcast", i, l,
                       make_reader(plan["track"], errors={("graphcast", i)}))
           for i, l in plan["verified"]]
    df1, st1 = ln.finalize(ev, "graphcast", plan, bad)
    assert df1 is None and st1["status"] == "read_error"
    assert st1["status"] not in pipeline.DOCUMENTED_ZERO_STATUSES


def test_pool_runs_flood_pairs_in_process_and_matches_the_direct_path(fake_flood_lane):
    """The worker-pool path (lane fields + injected reader) equals the same jobs run inline."""
    ev, plan, holder = fake_flood_lane
    got = {}
    stats = lane_pool.run_pairs("flood", [(ev, m) for m in MODELS],
                                lambda e, m, df, st: got.update({m: (df, st)}),
                                workers=2, executor_factory=_threads(2), poll_s=0.05)
    assert stats["lane"] == "flood" and stats["pairs_done"] == 2
    assert stats["stopped"] is False and stats["abort"] is None
    assert stats["jobs"] == stats["jobs_done"] == 2 * len(plan["verified"])
    assert set(got) == set(MODELS) and all(st["status"] == "scored" for _, st in got.values())
    direct = [flood_lane.score_init(plan["track"], "graphcast", i, l, holder["reader"])
              for i, l in plan["verified"]]
    df_direct, _st = flood_lane.finalize_pair(ev, "graphcast", plan, direct)
    sort = ["model", "metric", "init_time", "lead_time_hours"]
    pd.testing.assert_frame_equal(
        got["graphcast"][0].sort_values(sort).reset_index(drop=True),
        df_direct.sort_values(sort).reset_index(drop=True))
    assert set(pd.concat([got[m][0] for m in MODELS], ignore_index=True)["model"]) == set(MODELS)


# ----------------------------------------------------------- scorecard/meta ---
def _flood_rows(events=("f1", "f2", "f3"), models=tuple(MODELS), metrics=flood_lane.FLOOD_METRICS,
                lead=24.0, shift=0.0):
    recs = []
    for k, eid in enumerate(events):
        for mi, m in enumerate(models):
            for ji, metric in enumerate(metrics):
                recs.append({"event_id": eid, "event_type": "flood", "model": m, "metric": metric,
                             "lead_time_hours": lead,
                             "value": 0.4 + 0.05 * ji + 0.01 * k + 0.02 * mi + shift,
                             "init_time": np.datetime64("2026-09-01", "ns")})
    return pd.DataFrame(recs)


def test_build_scorecard_handles_flood_rows_without_a_baseline():
    card = pipeline.build_scorecard(_flood_rows())
    assert card["baseline"] == pipeline.BASELINE
    rows = card["rows"]
    assert {r["metric"] for r in rows} == set(flood_lane.FLOOD_METRICS)
    assert {r["model"] for r in rows} == set(MODELS)
    # no ifs_hres in the flood lane: no skill colour anywhere, and no fake common subset
    assert all(r["skill_vs_ifs_pct"] is None for r in rows)
    assert all(r["skill_n_common"] is None for r in rows)
    # 3+ events per cell: the event bootstrap runs
    assert all(r["ci95_lo"] is not None and r["ci95_hi"] is not None and r["n_events"] == 3
               for r in rows)
    json.dumps(card)


def test_signed_flood_metrics_never_score_against_a_baseline():
    """With an ifs_hres row present the signed metrics must still carry no skill (D6), while
    the direction-flagged metrics do compare (higher is better)."""
    rows = pd.concat([_flood_rows(),
                      _flood_rows(models=("ifs_hres",), shift=-0.1)], ignore_index=True)
    card = pipeline.build_scorecard(rows)
    by = {(r["metric"], r["model"]): r for r in card["rows"]}
    for m in sorted(set(pipeline.SIGNED_ERROR_METRICS) & set(flood_lane.FLOOD_METRICS)):
        assert by[(m, "ifs_hres")]["skill_vs_ifs_pct"] is None
        assert by[(m, "graphcast")]["skill_vs_ifs_pct"] is None
    g = by[(flood_lane.M_CSI, "graphcast")]
    assert g["skill_vs_ifs_pct"] > 0 and g["skill_n_common"] == 3
    far = by[("FalseAlarmRatio", "graphcast")]
    assert far["skill_vs_ifs_pct"] < 0                 # lower is better: a larger FAR is worse


def test_compact_track_rows_collapses_flood_rows_to_one_mean_per_lead():
    rows = flood_lane.rows_for_pair("f1", "flood", "graphcast", [
        {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0,
         "metrics": {flood_lane.M_CSI: 0.5, flood_lane.M_BIAS: 1.0}},
        {"init_true": "2026-08-31T00:00:00", "lead_true": 24.0,
         "metrics": {flood_lane.M_CSI: 1.0, flood_lane.M_BIAS: 3.0}},
        {"init_true": "2026-08-30T00:00:00", "lead_true": 48.0,
         "metrics": {flood_lane.M_CSI: 0.25}}])
    heat = pd.DataFrame({"event_id": ["h"], "event_type": ["heat_wave"], "model": ["m"],
                         "metric": ["x"], "lead_time_hours": [24.0], "value": [1.0],
                         "init_time": [np.datetime64("2026-09-01", "ns")]})
    out = pipeline.compact_track_rows(pd.concat([rows, heat], ignore_index=True))
    flood_out = out[out.event_type == "flood"]
    assert len(flood_out) == 3                                  # csi@24, bias@24, csi@48
    csi24 = flood_out[(flood_out.metric == flood_lane.M_CSI) & (flood_out.lead_time_hours == 24.0)]
    assert len(csi24) == 1 and csi24["value"].iloc[0] == pytest.approx(0.75)
    bias24 = flood_out[flood_out.metric == flood_lane.M_BIAS]
    assert bias24["value"].iloc[0] == pytest.approx(2.0)
    assert flood_out["init_time"].isna().all()
    assert out[out.event_type == "heat_wave"]["init_time"].notna().all()


def test_flood_scoring_meta_reports_the_truth_and_the_scored_models():
    ev = _event()
    m = pipeline._flood_scoring_meta([ev], MODELS + ["aurora", "ifs_hres"])
    assert m["truth"] == "pardig"
    assert "PARDIG" in m["truth_label"] and "AI-estimated" in m["truth_label"]
    assert m["scored_models"] == MODELS
    assert m["unsupported_models"] == ["aurora", "ifs_hres"]
    assert m["note"].startswith("Flood is scored for GraphCast only")
    json.dumps(m)
    # no flood events -> no key payload at all; fail-soft on a bad models list
    assert pipeline._flood_scoring_meta([object()], MODELS) == {}
    assert pipeline._flood_scoring_meta([ev], None) == {}


# ----------------------------------------------------------------- dispatch ---
def test_score_job_dispatches_flood_to_the_lane_serial(monkeypatch):
    """``_score_job`` asks the lane, not a hard-coded TC scorer (4a)."""
    ev = _event()
    calls = []
    rows = flood_lane.rows_for_pair(ev.id, "flood", "graphcast", [
        {"init_true": "2026-09-01T00:00:00", "lead_true": 24.0,
         "metrics": {flood_lane.M_CSI: 1.0, flood_lane.M_POD: 1.0}}])

    def spy(event, models, status_out=None, **kw):
        calls.append((event.id, event.event_type, list(models), status_out is not None))
        if status_out is not None:
            status_out["graphcast"] = {"status": "scored", "rows": len(rows)}
        return rows, {"graphcast": {"status": "scored", "rows": len(rows)}}

    monkeypatch.setitem(lane_pool.LANE_BY_EVENT_TYPE, "flood",
                        dataclasses.replace(lane_pool.LANES["flood"], serial=spy))
    status: dict = {}
    eid, df = pipeline._score_job(ev.to_dict(), ["graphcast"], "pardig", status_out=status)
    assert calls == [(ev.id, "flood", ["graphcast"], True)]
    assert eid == ev.id and df is not None
    assert set(df["metric"]) == {flood_lane.M_CSI, flood_lane.M_POD}
    assert status["graphcast"]["status"] == "scored"


def test_score_job_still_dispatches_tc_to_the_track_scorer(monkeypatch):
    """The generalisation did not break the lane it replaced."""
    t0 = dt.datetime(2026, 9, 1, 0)
    track = [[(t0 + dt.timedelta(hours=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              10.0 + 0.25 * i, -110.0, 40.0, 1000.0] for i in range(9)]
    ev = Event(id="tc1", event_type="tropical_cyclone", title="TC", start=t0,
               end=t0 + dt.timedelta(hours=27), bbox=(5.0, 25.0, -125.0, -95.0),
               peak_value=40.0, details={"track": track, "peak_wind_kt": 40.0, "sid": "X"})
    seen = []

    def spy(event, models, status_out=None, **kw):
        seen.append(event.event_type)
        if status_out is not None:
            status_out["graphcast"] = {"status": "empty", "rows": 0}
        return pd.DataFrame(columns=pipeline.SCORE_COLUMNS), {}

    monkeypatch.setitem(lane_pool.LANE_BY_EVENT_TYPE, "tropical_cyclone",
                        dataclasses.replace(lane_pool.LANES["tc"], serial=spy))
    eid, df = pipeline._score_job(ev.to_dict(), ["graphcast"], "ibtracs", status_out={})
    assert seen == ["tropical_cyclone"] and eid == "tc1" and df is None


# ----------------------------------------------------------------- regions ---
def _na_flood_events():
    return [Event(id=f"fl{i}", event_type="flood", title=f"Flood {i}", start=T0,
                  end=T0 + dt.timedelta(days=1), bbox=b, peak_value=30.0 + i, details={})
            for i, b in enumerate([(30.0, 40.0, -100.0, -90.0), (28.0, 38.0, -95.0, -85.0),
                                   (32.0, 42.0, -105.0, -95.0)])]


def test_region_scorecards_accept_flood_rows():
    evs = _na_flood_events()
    out = regions.build_region_scorecards(evs, _flood_rows(events=tuple(e.id for e in evs)))
    assert set(out["events"]) == {e.id for e in evs}
    assert all("north_america" in ks and "northern_hemisphere" in ks for ks in out["events"].values())
    na = out["by_region"]["north_america"]
    assert {r["metric"] for r in na["rows"]} == set(flood_lane.FLOOD_METRICS)
    assert {r["model"] for r in na["rows"]} == set(MODELS)
    assert all(r["skill_vs_ifs_pct"] is None for r in na["rows"])
    counts = {r["key"]: r for r in out["regions"]}
    assert counts["north_america"]["by_type"] == {"flood": 3}


# ------------------------------------------------------ run() per-lane block ---
def _patch_run(monkeypatch, events, models):
    from ewb_live import events as events_pkg
    from ewb_live.events import qualification, validate
    from ewb_live.scoring import truth as truth_mod
    from ewb_live.scoring import truth_mirror

    monkeypatch.setattr(sources, "list_models", lambda: list(models))
    monkeypatch.setattr(truth_mod, "probe_arco_cutoff", lambda: dt.datetime(2026, 11, 1))
    monkeypatch.setattr(truth_mirror, "ensure_mirror", lambda **k: None)
    monkeypatch.setattr(events_pkg, "detect_events", lambda *a, **k: list(events))
    monkeypatch.setattr(validate, "validate_event", lambda e: [])
    monkeypatch.setattr(qualification, "qualify_events", lambda evs: None)
    for name, val in (("_ibtracs_meta", lambda *a, **k: None), ("_lane_status_meta", lambda: {}),
                      ("_clim_meta", lambda: {}), ("_sel_thresholds", lambda: {})):
        monkeypatch.setattr(pipeline, name, val)


def test_run_scores_flood_through_the_lane_block_and_publishes_the_meta(
        results_tree, fake_flood_lane, monkeypatch):
    ev, _plan, _holder = fake_flood_lane
    models = MODELS + ["aurora"]
    _patch_run(monkeypatch, [ev], models)
    real, lanes = pipeline.run_lanes, []

    def threaded(pairs, **kw):
        lanes.append((kw["lane_key"], len(pairs)))
        return real(pairs, executor_factory=_threads(2), poll_s=0.05, **kw)

    monkeypatch.setattr(pipeline, "run_lanes", threaded)
    assert pipeline.run(days=90, top_n=15) == 0
    assert lanes == [("flood", 2)]                             # one pool call, this lane only
    res = results_tree / "results"
    meta = json.loads((res / "meta.json").read_text())
    assert meta["full_run_complete"] is True
    cov = meta["pair_coverage"]
    assert (cov["expected"], cov["scored"], cov["documented_zero_rows"]) == (3, 2, 1)
    assert cov["unattempted_or_failed"] == []
    assert meta["flood_scoring"]["scored_models"] == MODELS
    assert meta["flood_scoring"]["unsupported_models"] == ["aurora"]
    sc = pd.read_parquet(res / "scores.parquet")
    assert set(sc["metric"]) == set(flood_lane.FLOOD_METRICS) and set(sc["truth_source"]) == {"pardig"}
    assert set(sc["model"]) == set(MODELS)
    pj = json.loads((res / "scores.json").read_text())
    # scores.json is the compacted payload: one row per (event, model, metric, lead), no init stamp
    assert len(pj) == len(sc) and all(r["init_time"] is None for r in pj)
    ps = json.loads((res / "pair_status.json").read_text())
    assert ps[f"{ev.id}|aurora"]["status"] == "unsupported"
    # pair_status.reason is published through the 120-char public_reason scrub
    assert "GraphCast family" in ps[f"{ev.id}|aurora"]["reason"]
    assert ps[f"{ev.id}|graphcast"]["status"] == "scored"
    card = json.loads((res / "scorecard.json").read_text())["rows"]
    assert {r["metric"] for r in card} == set(flood_lane.FLOOD_METRICS)
    assert all(r["skill_vs_ifs_pct"] is None for r in card)
    perf = json.loads((results_tree / "perf.json").read_text())["perf"]
    assert perf["lane_flood"]["pairs_done"] == 2 and perf["lane_flood"]["jobs"] > 0
    assert "lane_tc" not in perf
    # a rerun has nothing left to score: no pool call at all
    monkeypatch.setattr(pipeline, "run_lanes",
                        lambda pairs, **kw: pytest.fail("rescored a finished pair"))
    assert pipeline.run(days=90, top_n=15) == 0


def test_run_groups_pairs_per_lane_key_and_runs_one_pool_each(
        results_tree, fake_flood_lane, monkeypatch):
    """TC and flood events in one run: two pool calls, two perf entries, both lanes scored."""
    ev, _plan, _holder = fake_flood_lane
    t0 = dt.datetime(2026, 9, 1, 0)
    tc_track = [[(t0 + dt.timedelta(hours=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 10.0 + 0.25 * i, -110.0, 40.0, 1000.0] for i in range(9)]
    tc_ev = Event(id="tc00000001", event_type="tropical_cyclone", title="TC", start=t0,
                  end=t0 + dt.timedelta(hours=27), bbox=(5.0, 25.0, -125.0, -95.0),
                  peak_value=40.0, details={"track": tc_track, "peak_wind_kt": 40.0, "sid": "X"})
    # a TC lane with nothing to verify: its pairs finalise as documented `empty`
    tc_lane_stub = lane_pool.Lane(event_type="tropical_cyclone", truth="ibtracs",
                                  models=lambda ms: (list(ms), {}),
                                  plan=lambda e: {"track": None, "start": None, "verified": [],
                                                  "not_verified": []},
                                  init_job=lambda *a, **k: pytest.fail("tc job ran"),
                                  finalize=tc_lane.finalize_pair, label="tropical cyclone",
                                  serial=tc_lane.score_tc_event)
    monkeypatch.setitem(lane_pool.LANES, "tc", tc_lane_stub)
    monkeypatch.setitem(lane_pool.LANE_BY_EVENT_TYPE, "tropical_cyclone", tc_lane_stub)
    models = MODELS + ["aurora"]
    _patch_run(monkeypatch, [ev, tc_ev], models)
    real, lanes = pipeline.run_lanes, []

    def threaded(pairs, **kw):
        lanes.append((kw["lane_key"], len(pairs)))
        return real(pairs, executor_factory=_threads(2), poll_s=0.05, **kw)

    monkeypatch.setattr(pipeline, "run_lanes", threaded)
    assert pipeline.run(days=90, top_n=15) == 0
    assert sorted(lanes) == [("flood", 2), ("tc", 3)]          # one call per lane present
    assert [k for k, _ in lanes] == sorted(k for k, _ in lanes)   # sorted lane-key order
    meta = json.loads((results_tree / "results" / "meta.json").read_text())
    assert meta["full_run_complete"] is True
    cov = meta["pair_coverage"]
    assert (cov["expected"], cov["scored"]) == (6, 2)
    assert cov["documented_zero_rows"] == 4             # aurora(flood) + the 3 TC empties
    assert meta["track_verification"] == {} or "tropical_cyclone" in meta["track_verification"]
    ps = json.loads((results_tree / "results" / "pair_status.json").read_text())
    assert all(ps[f"tc00000001|{m}"]["status"] == "empty" for m in models)
    perf = json.loads((results_tree / "perf.json").read_text())["perf"]
    assert set(perf) >= {"lane_flood", "lane_tc"}
    assert perf["lane_flood"]["pairs_done"] == 2 and perf["lane_tc"]["pairs_done"] == 3
