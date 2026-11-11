"""Why does fourcastnet_v2_gfs's AR mask keep a level dim? Compare GFS/IFS."""
import datetime as dt
import logging
import sys

logging.basicConfig(level=logging.ERROR)
sys.path.insert(0, "/opt/ewb-live")

import numpy as np  # noqa: E402
from extremeweatherbench import defaults  # noqa: E402
from extremeweatherbench.derived import AtmosphericRiverVariables  # noqa: E402

from ewb_live import sources  # noqa: E402

import json  # noqa: E402

evs = json.load(open("/opt/ewb-live/data/results/events_selected.json"))
items = evs["events"] if isinstance(evs, dict) else evs
ev = next(e for e in items if e["id"] == "a3bb50ccde3d")
bbox = tuple(ev["bbox"])
init = dt.datetime(2026, 8, 5, 0)  # a mid-event init
leads = list(range(0, 49, 6))

for model in ("fourcastnet_v2", "fourcastnet_v2_gfs"):
    info = sources.model_info(model)
    print(model, "levels:", info.levels)
    ds = sources.open_forecast(model, init,
                               ["eastward_wind", "northward_wind",
                                "relative_humidity", "air_temperature"],
                               leads, bbox)
    ds = defaults.preprocess_cira_icechunk_ar_forecast_dataset(ds)
    print("  q dims:", ds["specific_humidity"].dims,
          "q finite frac:", float(np.isfinite(ds["specific_humidity"]).mean()))
    ar = AtmosphericRiverVariables(output_variables=["atmospheric_river_mask"])
    try:
        out = ar.derive_variable(ds)
        m = out["atmospheric_river_mask"] if "atmospheric_river_mask" in out else out
        print("  mask dims:", m.dims, "nan frac:", float(np.isnan(m).mean())
              if "level" not in m.dims else "3D MASK!", "any true:", bool(m.sum()))
    except Exception as exc:  # noqa: BLE001
        print("  derive failed:", type(exc).__name__, str(exc)[:200])
