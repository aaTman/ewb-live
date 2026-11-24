"""Print the exact dims at the spatial_displacement crash for FCN / a3bb50."""
import json
import logging
import sys
import traceback

logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, "/opt/ewb-live")
from extremeweatherbench import metrics as ewb_metrics  # noqa: E402

from ewb_live.events import Event  # noqa: E402
from ewb_live.scoring import evaluate as ev_mod  # noqa: E402

_orig = ewb_metrics.SpatialDisplacement._compute_metric


def _spy(self, forecast, target, **kw):
    print("SPATIAL_DISP fc:", forecast.name, dict(forecast.sizes),
          " tg:", target.name, dict(target.sizes), flush=True)
    return _orig(self, forecast, target, **kw)


ewb_metrics.SpatialDisplacement._compute_metric = _spy

evs = json.load(open("/opt/ewb-live/data/results/events_selected.json"))
items = evs["events"] if isinstance(evs, dict) else evs
ev = next(Event.from_dict(e) for e in items if e["id"] == "a3bb50ccde3d")
status = {}
try:
    df = ev_mod.score_event(ev, ["fourcastnet_v2"], truth_source="era5_arco",
                            status_out=status)
    print("ROWS:", 0 if df is None else len(df))
except Exception:
    traceback.print_exc()
print("STATUS:", status)
