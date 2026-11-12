#!/usr/bin/env python3
"""Generate realistic sample data for the EWB Live dashboard.

Writes data/results/sample/{events,scores,scorecard,meta}.json matching SPEC §3.
Deterministic (seeded). Run from repo root:

    python3 dashboard/tools/generate_sample_data.py

Shape of the data mirrors EWB's own metric families (RMSE/MAE on surface_air_temperature,
TMAX/TMIN MAE for heat/freeze, CSI + spatial displacement + IVT RMSE + onset lead for ARs)
and Brightband's published finding that AI models beat IFS HRES on extremes, relatively
more on detection than on location. AI advantage grows with lead time; fourcastnet is a
deliberate "slightly worse than IFS" member so the heatmap exercises both colours.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "results" / "sample"

END_DATE = date(2026, 11, 6)
START_DATE = END_DATE - timedelta(days=90)

MODELS = ["ifs_hres", "graphcast", "pangu_weather", "aifs", "aurora", "fourcastnet"]
LEADS_H = [6, 12, 24, 48, 72, 120, 168, 240]

# multiplier vs ifs_hres for ERROR metrics (x<1 = better). Detection-type skills
# (csi, onset lead) are inverted later. Advantage grows with lead via lead_factor.
MODEL_QUALITY = {           # rmse      mae       tmax/tmin  onset    duration
    "graphcast":     {"err": 0.86, "mae": 0.87, "ext": 0.88, "timing": 0.92},
    "pangu_weather": {"err": 0.85, "mae": 0.86, "ext": 0.87, "timing": 0.91},
    "aifs":          {"err": 0.95, "mae": 0.95, "ext": 0.96, "timing": 0.96},
    "aurora":        {"err": 0.89, "mae": 0.90, "ext": 0.90, "timing": 0.94},
    "fourcastnet":   {"err": 1.05, "mae": 1.05, "ext": 1.06, "timing": 1.03},
}
# AR metrics per model (err = ivt_rmse & displacement; det = csi & onset lead, higher better)
AR_QUALITY = {
    "graphcast":     {"err": 0.88, "det": 1.12},
    "pangu_weather": {"err": 0.87, "det": 1.10},
    "aifs":          {"err": 0.94, "det": 1.05},
    "aurora":        {"err": 0.90, "det": 1.08},
    "fourcastnet":   {"err": 1.06, "det": 0.94},
}

HIGHER_IS_BETTER = {"csi": True, "onset_lead_hours": True}

METRICS = {
    "rmse_k":               {"label": "RMSE", "units": "K", "event_types": ["heat_wave", "freeze"], "higher_is_better": False, "ewb": "RootMeanSquaredError(surface_air_temperature)"},
    "mae_k":                {"label": "MAE", "units": "K", "event_types": ["heat_wave", "freeze"], "higher_is_better": False, "ewb": "MeanAbsoluteError(surface_air_temperature)"},
    "tmax_mae_k":           {"label": "TMAX MAE", "units": "K", "event_types": ["heat_wave"], "higher_is_better": False, "ewb": "MaximumMeanAbsoluteError"},
    "tmin_mae_k":           {"label": "TMIN MAE", "units": "K", "event_types": ["freeze"], "higher_is_better": False, "ewb": "LowestHighestMeanAbsoluteError (analog)"},
    "onset_error_days":     {"label": "Onset error", "units": "days", "event_types": ["heat_wave", "freeze"], "higher_is_better": False, "ewb": "event onset timing error"},
    "duration_error_days":  {"label": "Duration error", "units": "days", "event_types": ["heat_wave", "freeze"], "higher_is_better": False, "ewb": "event duration error"},
    "ivt_rmse_kg_m_s":      {"label": "IVT RMSE", "units": "kg m$^{-1}$ s$^{-1}$", "event_types": ["atmospheric_river"], "higher_is_better": False, "ewb": "RMSE on integrated water vapour transport"},
    "csi":                  {"label": "CSI", "units": "", "event_types": ["atmospheric_river"], "higher_is_better": True, "ewb": "Critical Success Index on AR mask over land"},
    "spatial_displacement_km": {"label": "Spatial displacement", "units": "km", "event_types": ["atmospheric_river"], "higher_is_better": False, "ewb": "displacement of IVT centre of mass"},
    "onset_lead_hours":     {"label": "Early signal (onset lead)", "units": "h", "event_types": ["atmospheric_river"], "higher_is_better": True, "ewb": "lead time at which AR onset is first signalled"},
}

# (id, type, title, start, end, bbox(lat_min,lat_max,lon_min,lon_max), peak, units, extra)
EVENT_DEFS = [
    ("hw-iberia-0810",      "heat_wave",          "Iberia heat wave",                 date(2026, 8, 10),  date(2026, 8, 16), (36.0, 43.5, -9.0, 2.0),    44.8, "degC", {"synoptic": "Celtic-high heat dome"}),
    ("hw-us-west-0812",     "heat_wave",          "Great Basin / Southwest US heat wave", date(2026, 8, 12), date(2026, 8, 19), (32.0, 42.0, -120.0, -108.0), 46.1, "degC", {"synoptic": "monsoon-ridge collapse"}),
    ("hw-n-sam-0818",       "heat_wave",          "Northern South America heat wave",  date(2026, 8, 18),  date(2026, 8, 24), (-2.0, 10.0, -76.0, -66.0), 38.9, "degC", {"synoptic": "subsidence under anomalous subtropical high"}),
    ("hw-caspia-0902",      "heat_wave",          "Caspian / Central Asia heat wave",  date(2026, 9, 2),   date(2026, 9, 9),  (38.0, 48.0, 55.0, 72.0),  41.2, "degC", {"synoptic": "blocking ridge"}),
    ("hw-se-aus-0918",      "heat_wave",          "Southeast Australia spring heat wave", date(2026, 9, 18), date(2026, 9, 23), (-34.0, -28.0, 144.0, 152.0), 41.5, "degC", {"synoptic": "offshore flow ahead of trough"}),
    ("hw-se-us-0922",       "heat_wave",          "Southeast US lingering autumn heat", date(2026, 9, 22),  date(2026, 9, 30), (29.0, 36.0, -92.0, -78.0), 38.3, "degC", {"synoptic": "dipole pattern, ridge off Southeast coast"}),
    ("hw-medit-1002",       "heat_wave",          "Maghreb–Iberia October heat wave",  date(2026, 10, 2),  date(2026, 10, 8), (34.0, 40.0, -6.0, 10.0),   39.7, "degC", {"synoptic": "Saharan plume, cut-off low to the north"}),
    ("fr-prairies-0921",    "freeze",             "Canadian Prairies early-season freeze", date(2026, 9, 21), date(2026, 9, 25), (49.0, 56.0, -112.0, -98.0), -3.8, "degC", {"synoptic": "post-frontal Arctic outbreak"}),
    ("fr-rockies-0928",     "freeze",             "Central Rockies high-elevation freeze", date(2026, 9, 28), date(2026, 10, 2), (37.0, 43.0, -108.0, -104.0), -8.2, "degC", {"synoptic": "trough + radiative nights over snow"}),
    ("fr-hokkaido-1008",    "freeze",             "Hokkaido first-frost freeze",       date(2026, 10, 8),  date(2026, 10, 12), (41.5, 45.5, 140.0, 145.5), -1.9, "degC", {"synoptic": "seasonal cold-air mass buildup"}),
    ("fr-siberia-1014",     "freeze",             "Central Siberia early cold spell",  date(2026, 10, 14), date(2026, 10, 21), (55.0, 64.0, 85.0, 105.0), -18.4, "degC", {"synoptic": "Ural blocking, persistent pool"}),
    ("fr-baltic-1022",      "freeze",             "Baltic / Northern Europe first freeze", date(2026, 10, 22), date(2026, 10, 27), (53.0, 60.0, 8.0, 24.0), -6.3, "degC", {"synoptic": "continental easterly"}),
    ("fr-plains-1101",      "freeze",             "Northern Plains hard freeze",       date(2026, 11, 1),  date(2026, 11, 5), (44.0, 49.0, -105.0, -95.0), -12.7, "degC", {"synoptic": "500-hPa trough, Canadian transfer"}),
    ("ar-norcal-0814",      "atmospheric_river",  "Northern California coastal AR",    date(2026, 8, 14),  date(2026, 8, 17), (37.0, 42.0, -127.0, -121.0), 650.0, "kg/m/s", {"landfall": "Mendocino coast", "peak_ivt": 650}),
    ("ar-pnw-0914",         "atmospheric_river",  "Pacific Northwest AR",              date(2026, 9, 14),  date(2026, 9, 17), (43.0, 49.0, -128.0, -121.0), 720.0, "kg/m/s", {"landfall": "Columbia River mouth", "peak_ivt": 720}),
    ("ar-weur-0926",        "atmospheric_river",  "Western Europe / UK AR",            date(2026, 9, 26),  date(2026, 9, 29), (47.0, 58.0, -12.0, 3.0),     780.0, "kg/m/s", {"landfall": "SW England, Wales", "peak_ivt": 780}),
    ("ar-japan-1005",       "atmospheric_river",  "Japan / NW Pacific AR",             date(2026, 10, 5),  date(2026, 10, 8), (31.0, 40.0, 138.0, 146.0),  610.0, "kg/m/s", {"landfall": "Sea of Japan coast", "peak_ivt": 610}),
    ("ar-schile-1011",      "atmospheric_river",  "Southern Chile AR",                date(2026, 10, 11), date(2026, 10, 14), (-45.0, -37.0, -76.0, -70.0), 690.0, "kg/m/s", {"landfall": "Araucanía / Los Ríos", "peak_ivt": 690}),
    ("ar-calif-1024",       "atmospheric_river",  "California October AR",             date(2026, 10, 24), date(2026, 10, 28), (34.0, 41.0, -126.0, -119.0), 820.0, "kg/m/s", {"landfall": "Bodega to Big Sur", "peak_ivt": 820}),
    ("ar-nsea-1101",        "atmospheric_river",  "North Sea / South Norway AR",       date(2026, 11, 1),  date(2026, 11, 4), (57.0, 66.0, -5.0, 12.0),     760.0, "kg/m/s", {"landfall": "Rogaland, Oslofjord", "peak_ivt": 760}),
]

DETECTION_NOTES = {
    "heat_wave": "95th percentile of 90-day sliding t2m climatology (EWB percentile method), >=3 consecutive days",
    "freeze": "t2m <= 10th percentile of local climatology, >=1 day below 273.15 K (EWB freeze default)",
    "atmospheric_river": "IVT >= 250 kg m-1 s-1 crossing an AR mask, landfall within bbox",
}


def jitter(rng: random.Random, pct: float) -> float:
    return 1.0 + rng.uniform(-pct, pct)


def metric_base(etype: str, metric: str, lead: int) -> float:
    """IFS HRES reference curve: value at lead h (logistic-ish growth with lead)."""
    x = lead / 240.0
    def curve(lo, hi, k=1.4):
        # smooth growth from lo (lead->0) toward hi (lead=240), convex like error growth
        return lo + (hi - lo) * (x ** 0.68)
    if etype == "heat_wave":
        return {
            "rmse_k": curve(1.05, 3.2),
            "mae_k": curve(0.85, 2.6),
            "tmax_mae_k": curve(1.2, 3.55),
            "onset_error_days": curve(0.18, 1.7),
            "duration_error_days": curve(0.28, 1.8),
        }[metric]
    if etype == "freeze":
        return {
            "rmse_k": curve(1.15, 3.45),
            "mae_k": curve(0.95, 2.8),
            "tmin_mae_k": curve(1.3, 3.75),
            "onset_error_days": curve(0.2, 1.85),
            "duration_error_days": curve(0.3, 1.95),
        }[metric]
    # atmospheric_river
    return {
        "ivt_rmse_kg_m_s": curve(135.0, 410.0),
        "csi": curve(0.66, 0.315),           # decays with lead; handled by curve direction
        "spatial_displacement_km": curve(90.0, 360.0),
        "onset_lead_hours": curve(56.0, 17.0),  # shrinks with lead
    }[metric]


def main() -> None:
    rng = random.Random(20261106)
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- events.json ------------------------------------------------------
    events = []
    for eid, etype, title, s, e, (lmin, lmax, lonmin, lonmax), peak, units, extra in EVENT_DEFS:
        d = {
            "id": eid,
            "event_type": etype,
            "title": title,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "bbox": [lmin, lmax, lonmin, lonmax],
            "peak_value": peak,
            "details": {
                "peak_value_units": units,
                "duration_days": (e - s).days + 1,
                "detection": DETECTION_NOTES[etype],
                "bbox_units": "degrees",
                **extra,
            },
        }
        events.append(d)
    assert len(events) == 20

    # ---- scores.json ------------------------------------------------------
    rows = []
    # one shared 'difficulty' factor per event (applies to all models equally,
    # so relative ordering is preserved but curves are not perfectly parallel)
    for ev in events:
        diff = rng.uniform(0.88, 1.14)
        etype = ev["event_type"]
        metrics = [m for m, spec in METRICS.items() if etype in spec["event_types"]]
        for model in MODELS:
            for metric in metrics:
                for lead in LEADS_H:
                    base = metric_base(etype, metric, lead)
                    hb = HIGHER_IS_BETTER.get(metric, False)
                    if model == "ifs_hres":
                        mult = 1.0
                    elif etype == "atmospheric_river":
                        key = "det" if hb else "err"
                        q = AR_QUALITY[model][key]
                        # advantage grows with lead
                        lf = 0.75 + 0.5 * (lead / 240.0)
                        mult = 1.0 + (q - 1.0) * lf
                    else:
                        fam = ("timing" if "error_days" in metric
                               else "ext" if metric in ("tmax_mae_k", "tmin_mae_k")
                               else "mae" if metric == "mae_k" else "err")
                        q = MODEL_QUALITY[model][fam]
                        lf = 0.75 + 0.5 * (lead / 240.0)
                        mult = 1.0 + (q - 1.0) * lf
                    val = base * diff * mult * jitter(rng, 0.04 if hb else 0.03)
                    if metric == "csi":
                        val = min(0.95, max(0.05, val))
                    if metric == "onset_error_days":
                        val = max(0.05, val)
                    init = datetime.fromordinal(
                        (date.fromisoformat(ev["start"]) - timedelta(hours=lead)).toordinal()
                    ).replace(hour=0, minute=0)
                    rows.append({
                        "event_id": ev["id"],
                        "event_type": etype,
                        "model": model,
                        "metric": metric,
                        "lead_time_hours": lead,
                        "value": round(val, 3),
                        "init_time": init.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

    # ---- scorecard.json ---------------------------------------------------
    agg: dict[tuple, list[float]] = {}
    for r in rows:
        agg.setdefault((r["event_type"], r["metric"], r["lead_time_hours"], r["model"]), []).append(r["value"])

    scorecard = []
    keys = sorted(agg, key=lambda k: (k[0], k[1], k[2], MODELS.index(k[3])))
    means: dict[tuple, float] = {}
    for (etype, metric, lead, model) in keys:
        vals = agg[(etype, metric, lead, model)]
        mean = sum(vals) / len(vals)
        means[(etype, metric, lead, model)] = mean
    for (etype, metric, lead, model) in keys:
        mean = means[(etype, metric, lead, model)]
        ref = means[(etype, metric, lead, "ifs_hres")]
        hb = HIGHER_IS_BETTER.get(metric, False)
        rel = (mean - ref) / ref * 100.0 if hb else (ref - mean) / ref * 100.0
        scorecard.append({
            "event_type": etype,
            "metric": metric,
            "lead_time_hours": lead,
            "model": model,
            "mean_value": round(mean, 3),
            "n_events": len(agg[(etype, metric, lead, model)]),
            # positive = better than ifs_hres (sign-normalised per SPEC)
            "relative_skill_vs_ifs_pct": round(rel, 1),
        })

    # ---- meta.json ---------------------------------------------------------
    meta = {
        "last_updated": "2026-11-06T03:00:00Z",
        "cycle": "weekly",
        "week": "2026-W45",
        "next_update": "2026-11-09T06:00:00Z",
        "window": {"start": START_DATE.isoformat(), "end": END_DATE.isoformat()},
        "models": MODELS,
        "baseline_model": "ifs_hres",
        "truth_source": "ERA5 (ARCO) single-levels, GHCN for station t2m where available; fallback IFS HRES 0-h analysis",
        "lead_times_hours": LEADS_H,
        "event_counts": {
            t: sum(1 for e in events if e["event_type"] == t)
            for t in ("heat_wave", "freeze", "atmospheric_river")
        },
        "metrics": METRICS,
        "is_sample": True,
        "generated_by": "dashboard/tools/generate_sample_data.py (seeded synthetic sample; NOT real scores)",
        "schema_version": 1,
    }

    (OUT / "events.json").write_text(json.dumps(events, indent=1))
    (OUT / "scores.json").write_text(json.dumps({"rows": rows}, indent=1))
    (OUT / "scorecard.json").write_text(json.dumps({"rows": scorecard}, indent=1))
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))

    print(f"events={len(events)} scores={len(rows)} scorecard={len(scorecard)} -> {OUT}")
    for t, n in meta["event_counts"].items():
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
