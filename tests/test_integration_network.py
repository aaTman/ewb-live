"""Network integration tests — opt-in.

These hit live GCS (ERA5 ARCO, climatology) and the sources backends, so
they are marked ``network`` and skipped by default. Run with:

    EWB_LIVE_NET=1 NO_GCE_CHECK=True uv run pytest -q -m network tests/test_integration_network.py
"""

import datetime as dt
import os

import pytest

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("EWB_LIVE_NET") != "1",
        reason="set EWB_LIVE_NET=1 to run live-network integration tests",
    ),
]


def test_truth_cutoff_recent():
    from ewb_live.scoring.truth import probe_arco_cutoff

    cutoff = probe_arco_cutoff()
    age = dt.datetime.utcnow() - cutoff
    assert 2 * 24 <= age.total_seconds() / 3600 <= 10 * 24  # ARCO lags ~6 d


def test_score_event_dev_perfect_predictor():
    """ARCO-as-forecast must score ~0 against ARCO truth (plumbing test)."""
    import pandas as pd

    from ewb_live.events.types import Event
    from ewb_live.scoring.evaluate import score_event

    ev = Event(
        id="00000000dev001",
        event_type="heat_wave",
        title="dev",
        start=dt.datetime(2026, 10, 12),
        end=dt.datetime(2026, 10, 14),
        bbox=(35.0, 40.0, -120.0, -115.0),
        peak_value=None,
    )
    from ewb_live.scoring._dev_forecast import make_dev_forecast

    df = score_event(
        ev,
        ["dev_a"],
        truth_source="era5_arco",
        build_forecast_override=lambda e, m: make_dev_forecast(
            "dev_a", ["surface_air_temperature"], e.start, e.end,
            bbox=e.bbox, perturbation=0.0,
        ),
    )
    assert not df.empty
    rmse = df[df.metric == "RootMeanSquaredError"].value
    assert (rmse < 0.1).all(), f"perfect predictor RMSE not ~0: {rmse.describe()}"


def test_score_event_real_graphcast_smoke():
    """One real (event, model) score through the full EWB path."""
    from ewb_live.events.types import Event
    from ewb_live.pipeline import SCORE_COLUMNS
    from ewb_live.scoring.evaluate import score_event

    ev = Event(
        id="realgrph001",
        event_type="heat_wave",
        title="smoke",
        start=dt.datetime(2026, 10, 12),
        end=dt.datetime(2026, 10, 15),
        bbox=(30.0, 40.0, -120.0, -110.0),
        peak_value=None,
    )
    df = score_event(ev, ["graphcast"], truth_source="era5_arco")
    assert not df.empty
    assert list(df.columns[:7]) == SCORE_COLUMNS
    assert (df.model == "graphcast").all()
    # RMSE sanity band: real graphcast vs ERA5 over 0.25 deg should be < 15 K
    rmse = df[df.metric == "RootMeanSquaredError"].value
    assert (rmse < 15).all()


def test_score_event_ar_mask_metrics_regress():
    """AR mask metrics must produce usable CSI values (regression guard).

    With the pre-padding case region, forecast AR blobs were truncated by
    EWB's region-before-derive order and every CSI row came out 0/NaN. A
    capable model at short lead must clear CSI > 0.2 somewhere <= 48 h.
    """
    from ewb_live.events.types import Event
    from ewb_live.scoring.evaluate import score_event

    ev = Event(
        id="armaskrg01",
        event_type="atmospheric_river",
        title="ar regression",
        start=dt.datetime(2026, 8, 2),
        end=dt.datetime(2026, 8, 4),
        bbox=(6.75, 16.75, 117.0, 127.0),
        peak_value=None,
    )
    df = score_event(ev, ["graphcast"], truth_source="era5_arco")
    csi = df[df.metric == "CriticalSuccessIndex"]
    assert not csi.empty, "CSI produced no rows"
    short = csi[csi.lead_time_hours <= 48]
    assert (short.value > 0.2).any(), f"CSI never exceeds 0.2 at <=48h:\n{short}"
