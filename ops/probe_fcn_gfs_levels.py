"""GFS FCN: do pressure levels / variable axes differ across inits of the
a3bb50ccde3d window (era-change in the MLWP archive)?"""
import datetime as dt
import sys

sys.path.insert(0, "/opt/ewb-live")
from ewb_live.stores import mlwp_icechunk as mi  # noqa: E402

for code in ("FOUR_v200_GFS", "FOUR_v200_IFS"):
    print("==", code)
    for day in range(4, 12):
        init = dt.datetime(2026, 8, day, 0)
        try:
            ds = mi.open_init(code, init)
            lv = ds.level.values.tolist() if "level" in ds.coords else None
            print(init, "levels:", lv, "has(t,q,r):",
                  ("t" in ds, "q" in ds, "r" in ds))
        except Exception as exc:  # noqa: BLE001
            print(init, "MISS:", type(exc).__name__, str(exc)[:80])
