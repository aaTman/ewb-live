"""Pair-status bookkeeping (aigfs rule + full_run_complete inputs).

score_event(status_out=...) must record an outcome for EVERY requested
model: scored rows, or a documented zero-row reason — heat/freeze pairs
for models without 2 m temperature are 'n/a (no 2 m temperature)' while
the same model still scores on ARs (q/u/v). Offline: EWB bench/target
machinery is monkeypatched; only our routing logic is exercised.
"""
import datetime as dt

import pandas as pd
import pytest

from ewb_live.events.types import Event
from ewb_live.scoring import evaluate, truth


def mk(event_type, bbox=(10.0, 20.0, 0.0, 10.0)):
    s = dt.datetime(2026, 9, 1)
    return Event(
        id="deadbeef01", event_type=event_type,
        title=f"Fake {event_type} 2026-09-01 to 2026-09-04",
        start=s, end=s + dt.timedelta(days=4), bbox=bbox, peak_value=700.0,
        details={"clim_source": "owb", "antimeridian_wrap": False},
    )


VARS = {
    "ifs_hres": {"surface_air_temperature", "eastward_wind",
                 "northward_wind", "specific_humidity"},
    "aigfs": {"eastward_wind", "northward_wind", "specific_humidity",
              "air_temperature"},  # deliberately NO 2 m temperature
}


@pytest.fixture
def patch_evaluate(monkeypatch):
    monkeypatch.setattr(truth, "make_targets",
                        lambda *a, **k: {"ERA5": object()})
    monkeypatch.setattr(evaluate, "default_metrics", lambda *a, **k: [])
    monkeypatch.setattr(evaluate, "case_region_for", lambda e: e)
    monkeypatch.setattr(evaluate.ewb_inputs, "EvaluationObject",
                        lambda **k: object())

    class _FakeBench:
        def __init__(self, *a, **k):
            pass

        def run_evaluation(self, **k):
            return pd.DataFrame({
                "lead_time": [0, 6, 12],
                "value": [1.0, 2.0, 3.0],
                "metric": ["rmse"] * 3,
                "forecast_source": ["x"] * 3,
            })

    monkeypatch.setattr(evaluate.ewb_evaluate, "ExtremeWeatherBench", _FakeBench)

    def fake_model_info(model):
        class I:
            variables = sorted(VARS[model])
        return I()

    import ewb_live.sources as sources

    monkeypatch.setattr(sources, "model_info", fake_model_info)


def _fc(event, model):
    class FC:
        name = model
    return FC() if model in VARS else None


def test_heat_wave_no_sat_model_marked_n_a_and_never_built(patch_evaluate):
    ev = mk("heat_wave")
    status, built = {}, []

    def spy(event, model):
        built.append(model)
        return _fc(event, model)

    df = evaluate.score_event(ev, ["ifs_hres", "aigfs"],
                              truth_source="era5_arco",
                              build_forecast_override=spy,
                              status_out=status)
    assert status["aigfs"]["status"] == "unsupported"
    assert status["aigfs"]["reason"] == "n/a (no 2 m temperature)"
    assert "aigfs" not in built, "build must not be attempted for unsupported"
    assert status["ifs_hres"]["status"] == "scored"
    assert status["ifs_hres"]["rows"] == 3
    assert len(df) == 3


def test_same_no_sat_model_scores_on_ar(patch_evaluate):
    ev = mk("atmospheric_river")
    status = {}
    df = evaluate.score_event(ev, ["aigfs"], truth_source="era5_arco",
                              build_forecast_override=_fc, status_out=status)
    assert status["aigfs"]["status"] == "scored"  # AR needs q/u/v only
    assert len(df) == 3


def test_no_data_and_bench_failure_recorded(patch_evaluate, monkeypatch):
    ev = mk("heat_wave")
    monkeypatch.setitem(VARS, "ghost", {"surface_air_temperature"})

    def only_ifs(event, model):
        return _fc(event, model) if model == "ifs_hres" else None

    status = {}
    evaluate.score_event(ev, ["ifs_hres", "ghost"], truth_source="era5_arco",
                         build_forecast_override=only_ifs, status_out=status)
    assert status["ghost"]["status"] == "no_data"
    assert status["ghost"]["rows"] == 0

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def run_evaluation(self, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(evaluate.ewb_evaluate, "ExtremeWeatherBench", _Boom)
    status2 = {}
    evaluate.score_event(ev, ["ifs_hres"], truth_source="era5_arco",
                         build_forecast_override=_fc, status_out=status2)
    assert status2["ifs_hres"]["status"] == "bench_failed"


def test_read_error_is_retriable_not_terminal_or_documented(patch_evaluate):
    """D1/ponytail 2026-11-08: all-init-read-failure must record read_error
    (NOT no_data), so the pair re-enters `missing` next run and stays a
    full_run_complete blocker (coverage's documented tuple omits it)."""
    ev = mk("heat_wave")

    def boom(event, model):
        raise evaluate.ForecastReadError(f"all 3 init reads failed for {model}")

    status = {}
    evaluate.score_event(ev, ["ifs_hres"], truth_source="era5_arco",
                         build_forecast_override=boom, status_out=status)
    assert status["ifs_hres"]["status"] == "read_error"
    assert "retriable" in status["ifs_hres"]["reason"]
    # pin the ACTUAL pipeline gate tuples (constants, not copies): read_error
    # must be absent from both — retriable at :736 and a blocker at :583
    from ewb_live import pipeline as pl
    assert "read_error" not in pl.TERMINAL_NO_RESCHEDULE_STATUSES
    assert "read_error" not in pl.DOCUMENTED_ZERO_STATUSES


def test_failed_init_listing_is_retriable_read_error(monkeypatch):
    """Ponytail 2026-11-08: a transient failure in the init LISTING path must
    land in the retriable read_error lane, never a permanent no_data hole."""
    from ewb_live.scoring import evaluate as ev, inits_cache

    def boom(*a, **k):
        raise OSError("simulated S3 region blip")
    monkeypatch.setattr(inits_cache, "available_inits_cached", boom)
    from ewb_live.events.types import Event
    import datetime as dt
    ev_ = Event(id="abc123", event_type="heat_wave", title="t",
                start=dt.datetime(2026, 10, 1), end=dt.datetime(2026, 10, 3),
                bbox=(0.0, 1.0, 0.0, 1.0), peak_value=None)
    with pytest.raises(ev.ForecastReadError, match="init listing failed"):
        ev.build_forecast(ev_, "ifs_hres")
