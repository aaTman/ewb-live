"""EWB Live per-event visualization assets (design-owned).

Precomputes small JSON grids so the static dashboard can draw the paper-style
event visuals without client-side data science:

  heat/freeze  max-consecutive-event-days-per-grid-cell fields (the EWB paper's
               `plot_consecutive_map` visualization) for ERA5 truth and for each
               model forecast at standard lead bins, over the event bbox;
  AR           IVT magnitude + mask grids at the observed anchor step, and the
               same per model/lead for forecast-vs-observed mask overlap
               (hits / misses / false alarms, computed in the browser).

Output: data/results/events/<event_id>/viz.json  (schema v1, target < 400 KB)
        data/results/events/index.json

CLI:  python -m ewb_live.viz --all [--events id,id] [--limit N]
                            [--leads 24,120] [--force] [--types heat_wave,freeze,...]

Truth is read from the local ERA5 mirror (scoring.truth_mirror) with the same
daily mask rule as detection: a day counts only when every 00/06/12/18 UTC
step passes the climatology threshold, land only. Forecasts come through
ewb_live.sources.open_forecast (no direct GCS/icechunk here).

Never touches: events/, scoring/, pipeline.py, dashboard/.
"""
