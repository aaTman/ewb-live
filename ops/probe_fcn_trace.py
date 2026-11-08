"""One-off trace of the FCN-GFS AR bench crash (a3bb50ccde3d). Run on box."""
import json
import logging
import sys
import traceback

logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events import Event
from ewb_live.scoring.evaluate import score_event

evs = json.load(open(next(p for p in ("/opt/ewb-live/data/events_selected.json", "/opt/ewb-live/data/results/events_selected.json") if __import__("os").path.exists(p))))
items = evs["events"] if isinstance(evs, dict) else evs
ev = next(Event.from_dict(e) for e in items if e["id"] == "a3bb50ccde3d")

# surface the real traceback through evaluate's error logging
import ewb_live.scoring.evaluate as _ev
_orig_err = _ev.logger.error
def _err(*a, **k):
    traceback.print_exc()
    _orig_err(*a, **k)
_ev.logger.error = _err

status = {}
try:
    df = score_event(ev, ["fourcastnet_v2_gfs"], truth_source="era5_arco",
                     status_out=status)
    print("ROWS:", 0 if df is None else len(df))
except Exception:
    traceback.print_exc()
print("STATUS:", status)
