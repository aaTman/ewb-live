"""Scoring for EWB Live (SPEC section 2).

score_event(event, models) -> DataFrame[event_id, event_type, model, metric,
lead_time_hours, value, init_time]

Truth policy: ERA5 via ARCO where available (EWB default target), IFS HRES 0-h
analysis fallback for recent windows past ARCO's latency (recorded as
truth_source). Detection lives in ewb_live.events.
"""

from .evaluate import SCORE_COLUMNS, build_forecast, default_metrics, score_event
from .truth import (
    build_hres_analysis_zarr,
    make_targets,
    probe_arco_cutoff,
    truth_source_for_event,
)

__all__ = [
    "SCORE_COLUMNS",
    "score_event",
    "build_forecast",
    "default_metrics",
    "probe_arco_cutoff",
    "truth_source_for_event",
    "make_targets",
    "build_hres_analysis_zarr",
]
