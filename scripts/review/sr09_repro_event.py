# Science-review sr09: end-to-end reproducibility of one scored event.
# Reruns score_event() from scratch for event 457f3f87e24a (S. Plains heat,
# completed in the live run) on two models and compares row-for-row against
# scores.parquet. RUN ON THE ewb BOX, only when the scoring lane is idle.
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events.types import Event  # noqa: E402
from ewb_live.scoring.evaluate import score_event  # noqa: E402

ROOT = pathlib.Path("/opt/ewb-live/data/results")
# Targets: heat event scored in the live run + the shim-proof AR (B8 production
# proof). AR rerun costs ~0.075 GB/deg2 + 15 GB (~80 GB for 28x31) -> run ONLY
# when the scoring lane is idle and nothing else is on the box.
TARGETS = [("32d04a30d801", ["graphcast_gfs", "pangu_weather"]),
           ("88bb73d7ae25", ["ifs_hres"])]
if len(sys.argv) > 1:  # run one target: python sr09_repro_event.py 1
    TARGETS = [TARGETS[int(sys.argv[1])]]

sel = json.load(open(ROOT / "events_selected.json"))
evs = sel["events"] if isinstance(sel, dict) and "events" in sel else sel
live_all = pd.read_parquet(ROOT / "scores.parquet")

rep_all = []
for EV_ID, MODELS in TARGETS:
    ev = Event.from_dict(next(e for e in evs if e["id"] == EV_ID))
    df = score_event(ev, MODELS)
    live = live_all[(live_all.event_id == EV_ID) & (live_all.model.isin(MODELS))]

    key = ["model", "metric", "lead_time_hours", "init_time"]
    mg = live.merge(df, on=key, suffixes=("_live", "_rerun"), how="outer", indicator=True)
    d = mg[mg._merge == "both"]
    diff = (d.value_live - d.value_rerun).abs()
    rep_all.append({
        "event": EV_ID, "models": MODELS,
        "rows_live": len(live), "rows_rerun": len(df),
        "matched": int((mg._merge == "both").sum()),
        "only_live": int((mg._merge == "left_only").sum()),
        "only_rerun": int((mg._merge == "right_only").sum()),
        "max_abs_diff": float(diff.max()) if len(d) else None,
        "exact_frac": float((diff == 0).mean()) if len(d) else None,
        "metrics_live": sorted(live.metric.unique().tolist()),
    })
rep = rep_all
out = pathlib.Path("/opt/ewb-live/data/results/review/sr09_repro_event.json")
out.write_text(json.dumps(rep, indent=1))
print(json.dumps(rep, indent=1))
