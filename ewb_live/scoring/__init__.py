"""Scoring for EWB Live (SPEC section 2).

score_event(event, models) -> DataFrame[event_id, event_type, model, metric,
lead_time_hours, value, init_time]

Truth policy: ERA5 via ARCO where available (EWB default target), IFS HRES 0-h
analysis fallback for recent windows past ARCO's latency (recorded as
truth_source). Detection lives in ewb_live.events.

Track lanes (tropical_cyclone; WP1 of notes/scoring_design.md) are not scored by
EWB's evaluate: ``track_verif`` (geometry + storm-following tracker), ``tc``
(IBTrACS adapter, readers, per-pair scoring) and ``lane_pool`` (the worker pool).
"""

from .evaluate import SCORE_COLUMNS, build_forecast, default_metrics, score_event
from .track_verif import TRACK_METRICS, along_cross, follow_track, observed_motion
from .truth import (
    build_hres_analysis_zarr,
    make_targets,
    probe_arco_cutoff,
    truth_source_for_event,
)

__all__ = [
    "TRACK_METRICS",
    "along_cross",
    "follow_track",
    "observed_motion",
    "SCORE_COLUMNS",
    "score_event",
    "build_forecast",
    "default_metrics",
    "probe_arco_cutoff",
    "truth_source_for_event",
    "make_targets",
    "build_hres_analysis_zarr",
]
