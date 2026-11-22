"""WP3 validation (notes/landfall_validation.md): run ON the ewb LXC from a private tree.

1. Target check: for each storm, EWB's OWN IBTrACS target (``inputs.IBTrACS`` with its name + season
   filter, on a local copy of the NCEI csv) -> ``calc.find_landfalls`` vs our target built from
   ``details.track`` (keyed by sid). Reports landfall time / position differences and the
   name-filter problems (no name in our titles; two storms with the same name and season).
2. Pairing check: for one model, real tracks of every verified init (``tc.score_init``), EWB's
   landfall pairing once per init (ours) vs pooled over inits (EWB's pipeline behaviour).

    python -m ewb_live.tools.landfall_validate --csv /tmp/lf_list/ibtracs.last3years.list.v04r01.csv \
        --events data/results/events_selected.json --storms dd9825d6b56e,41ca1a5cf33c,27222b8db889 --model graphcast
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np


def ewb_target(csv: str, name: str, start: dt.datetime, number=None):
    """EWB's own IBTrACS target for a storm, exactly as its pipeline selects it (name + season)."""
    from extremeweatherbench import inputs

    tgt = inputs.IBTrACS(source=csv)
    case = SimpleNamespace(title=name.upper(), start_date=start)
    data = tgt.open_and_maybe_preprocess_data_from_source()
    data = tgt.maybe_map_variable_names(data)
    data = tgt.subset_data_to_case(data, case)
    if number is not None:
        import polars as pl

        data = data.filter(pl.col("number").cast(pl.Int64) == int(number))
    return tgt.maybe_convert_to_dataset(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--events", default="data/results/events_selected.json")
    ap.add_argument("--storms", required=True, help="comma-separated event ids")
    ap.add_argument("--model", default="graphcast")
    ap.add_argument("--no-pairing", action="store_true")
    args = ap.parse_args(argv)
    from extremeweatherbench import calc

    from ..events.types import Event
    from ..scoring import landfall, tc

    evs = {d["id"]: Event.from_dict(d) for d in json.load(open(args.events)) if d["event_type"] == tc.EVENT_TYPE}
    names = {}
    for e in evs.values():
        names.setdefault(((e.details or {}).get("name") or "").upper(), []).append(e.id)
    land, ocean = landfall._geoms()
    out = {"name_collisions": {n: ids for n, ids in names.items() if len(ids) > 1}, "storms": {}}
    for eid in args.storms.split(","):
        e = evs[eid]
        sid = landfall.storm_id(e)
        tr = tc.observed_track(e)
        ours = landfall.observed_landfalls(tr)
        rec = {"sid": sid["sid"], "name": sid["name"], "ours": [str(x)[:19] for x in ours]}
        try:
            ds = ewb_target(args.csv, sid["name"], dt.datetime.fromisoformat(e.start.isoformat()))
            da = ds["air_pressure_at_mean_sea_level"]
            lon = ((ds["longitude"].values + 180) % 360) - 180
            da = da.assign_coords(latitude=("valid_time", ds["latitude"].values), longitude=("valid_time", lon))
            tl = calc.find_landfalls(da, land_geom=land, ocean_geom=ocean)
            theirs = np.sort(tl.coords["valid_time"].values) if len(tl) else []
            rec["ewb_ibtracs"] = [str(x)[:19] for x in theirs]
            rec["ewb_n_fixes"] = int(da.sizes["valid_time"])
            if len(theirs) and len(ours):
                n = min(len(theirs), len(ours))
                rec["dt_h"] = [round(float((a - b) / np.timedelta64(1, "h")), 2) for a, b in zip(ours[:n], theirs[:n])]
        except Exception as exc:  # noqa: BLE001
            rec["ewb_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        if not args.no_pairing:
            plan = tc.plan_for(e)
            recs = [tc.score_init(plan["track"], args.model, i, ld) for i, ld in plan["verified"]]
            fcs = [r["fc_track"] for r in recs if r["status"] == "scored" and r.get("fc_track")]
            _, per = landfall.landfall_rows(e, args.model, plan["track"], fcs)
            _, pool = landfall.landfall_rows(e, args.model, plan["track"], fcs, pooled=True)
            rec["pairing"] = {"n_inits": len(fcs), "per_init": {k: per[k] for k in ("n_eligible", "n_paired")},
                              "pooled": {k: pool[k] for k in ("n_eligible", "n_paired")}}
        out["storms"][eid] = rec
        print(json.dumps({eid: rec}, default=str), flush=True)
    print(json.dumps(out["name_collisions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
