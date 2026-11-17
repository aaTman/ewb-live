"""drop-subsets validation on the ewb LXC (scratch outputs only).

Scores one AR event (full live init set, per-(metric,lead) rows) and one
heat_wave event (5 inits, per-init rows) for graphcast + pangu_weather +
ifs_hres through the NEW read path (MLWP via the Icechunk virtual store;
ifs_hres unchanged ECMWF path) and compares row-by-row against the live
pre-change score_parts. Also asserts the retired subsets tree never
appears and times the run against the old-path numbers in notes/ewb.md.
"""
import datetime as dt
import glob
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.environ.get("EWB_DEV_TREE", "/root/ne-subsets-dev"))

import dask
import pandas as pd

LIVE = pathlib.Path("/opt/ewb-live")
os.environ["EWB_MLWP_REPO"] = str(LIVE / "data/icechunk/cira_mlwp")
os.environ["EWB_LIVE_CACHE"] = str(LIVE / "data/cache")
os.makedirs("/root/ne-subsets-dev/data/results", exist_ok=True)

import logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("extremeweatherbench").setLevel(logging.WARNING)
dask.config.set(scheduler="threads", num_workers=2)

from ewb_live.events.types import Event
from ewb_live.scoring.evaluate import build_forecast, score_event

_sel = json.load(open(LIVE / "data/results/events_selected.json"))
EVENTS = {e["id"]: e for e in (_sel["events"] if isinstance(_sel, dict) else _sel)}
MODELS = ["graphcast", "pangu_weather", "ifs_hres"]
HEAT_INITS = [dt.datetime(2026, 9, 22), dt.datetime(2026, 9, 23),
              dt.datetime(2026, 9, 24), dt.datetime(2026, 9, 25),
              dt.datetime(2026, 9, 27)]  # first 5 inits in the live parts rows

out = {"runs": [], "checks": {}}


def live_parts(ev_id):
    fs = glob.glob(f"{LIVE}/data/cache/score_parts/{ev_id}_*.parquet")
    df = pd.concat([pd.read_parquet(f) for f in fs])
    return df[df.model.isin(MODELS)]


def compare(ev_id, new_df, keys):
    ref = live_parts(ev_id)
    if "init_time" in keys:  # rows with NaT init are cross-init aggregates
        new_df = new_df[new_df.init_time.notna()]
        ref = ref[ref.init_time.notna()]
    m = new_df.merge(ref, on=keys + ["model", "metric"], suffixes=("_new", "_ref"))
    both = m.dropna(subset=["value_new", "value_ref"])
    if len(both):
        rel = ((both.value_new - both.value_ref).abs()
               / both.value_ref.abs().clip(lower=1e-6))
        match = int((rel < 1e-6).sum())
        max_rel = float(rel.max())
    else:
        match, max_rel = 0, None
    res = {"rows_new": int(len(new_df)), "rows_ref": int(len(ref)),
           "rows_compared": int(len(both)), "rows_match": match,
           "max_rel_diff": max_rel}
    return res


def subsets_absent():
    p = LIVE / "data/cache/subsets"
    dev = pathlib.Path("/root/ne-subsets-dev/data/cache")
    found = {"live_subsets_exists": p.exists(),
             "dev_subsets_exists": (dev / "subsets").exists()}
    return found


# ---------------------------------------------------------------- heat/freeze
heat = Event.from_dict(EVENTS["085497ecbe06"])
t0 = time.time()
status = {}
df_heat = score_event(heat, MODELS, truth_source="era5_arco",
                      build_forecast_override=lambda ev, mdl: build_forecast(
                          ev, mdl, inits=HEAT_INITS),
                      status_out=status)
heat_time = time.time() - t0
df_heat.to_parquet("/root/ne-subsets-dev/data/results/validate_heat.parquet")
out["runs"].append({"event": "085497ecbe06 heat_wave", "models": MODELS,
                    "inits": len(HEAT_INITS), "wall_s": heat_time,
                    "status": status,
                    "compare": compare("085497ecbe06", df_heat,
                                       ["init_time", "lead_time_hours"])})
out["checks"].update(subsets_absent())
json.dump(out, open("/root/ne-subsets-dev/validate_out.json", "w"),
          default=str, indent=1)
print("HEAT DONE", heat_time, flush=True)

# -------------------------------------------------------------------------- AR
ar = Event.from_dict(EVENTS["56b282629cf6"])
t0 = time.time()
status = {}
df_ar = score_event(ar, MODELS, truth_source="era5_arco", status_out=status)
ar_time = time.time() - t0
df_ar.to_parquet("/root/ne-subsets-dev/data/results/validate_ar.parquet")
out["runs"].append({"event": "56b282629cf6 atmospheric_river",
                    "models": MODELS, "inits": "full-live", "wall_s": ar_time,
                    "status": status,
                    "compare": compare("56b282629cf6", df_ar,
                                       ["lead_time_hours"])})
out["checks"].update(subsets_absent())
json.dump(out, open("/root/ne-subsets-dev/validate_out.json", "w"),
          default=str, indent=1)
print("AR DONE", ar_time, flush=True)
print("VALIDATE COMPLETE", flush=True)
