"""Flood detail-view asset (``kind: "flood_accum"``).

Flood was the one published lane with no per-event viz asset: the detail
panel fell back to the honest empty state, and its evidence (IMERG
accumulation exceeding an ERA5 percentile per 0.25 deg cell) had no way to
be shown. This module rebuilds exactly the detector's join — IMERG daily mm
averaged up to the ERA5 lattice, the 1-day and trailing-72 h lanes against
the pooled ERA5 p95 with the absolute floors — over the event's window and
box, and writes what the panel needs:

* the per-day exceedance footprint on the event box (packed bit stack, same
  ``pack_bits`` contract as the heat/freeze panels),
* the per-cell exceedance-duration grid (what a "consecutive days" map is
  for a flood: how many of the event's days each cell was flooded),
* a per-day time series of the area-maximum accumulations (mm), exceedant
  area (km2) and the IMERG run that served the day.

Everything here is recomputed from the stores (no new persisted evidence);
the event's own ``details`` are only consulted for the peak cell marker.
Run per event the window load is days x 721 x 1440 float32 — a few hundred
MB at most, per the no-disk-cache rule.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

import numpy as np

from ..climatology import precip_clim
from ..events import flood

from . import build

logger = logging.getLogger(__name__)

CELL_DEG = flood.ERA5_DEG
#: extra days loaded before the event start so the trailing-72 h lane is
#: complete on the event's first day (the detector's own convention)
LANE3_LOOKBACK = 2


def _box_rows(lats: np.ndarray, bbox) -> np.ndarray:
    lat_lo, lat_hi = float(bbox[0]), float(bbox[1])
    idx = np.flatnonzero((lats <= lat_hi + 1e-6) & (lats >= lat_lo - 1e-6))
    if idx.size == 0:
        raise ValueError("bbox has no grid rows")
    return idx


def _box_cols(lons: np.ndarray, bbox) -> np.ndarray:
    """Contiguous eastward column run for the bbox (wrapped = across the seam)."""
    lon_lo, lon_hi = float(bbox[2]), float(bbox[3])
    if lon_lo <= lon_hi:
        return np.flatnonzero((lons >= lon_lo - 1e-6) & (lons <= lon_hi + 1e-6))
    left = np.flatnonzero(lons >= lon_lo - 1e-6)
    right = np.flatnonzero(lons <= lon_hi + 1e-6)
    return np.concatenate([left, right])


def build_flood(ev: dict[str, Any]) -> dict[str, Any]:
    bbox = tuple(float(x) for x in ev["bbox"])
    start = dt.datetime.fromisoformat(ev["start"]).date()
    end = dt.datetime.fromisoformat(ev["end"]).date()
    q = float((ev.get("details") or {}).get("quantile") or flood.DEFAULT_QUANTILE)

    load0 = start - dt.timedelta(days=LANE3_LOOKBACK)
    det = ev.get("details") or {}
    # rebuild on the SOURCE THE EVENT WAS DETECTED WITH (mixed stores are
    # possible while IMERG remains the selectable fallback)
    src_kind = ((det.get("data_source") or {}).get("kind")
                or os.environ.get("EWB_FLOOD_SOURCE") or "pardig")
    obs = flood.load_observations(load0, end - dt.timedelta(days=1), source=src_kind)
    lats, lons = obs.grid
    land = flood.land_mask(lats, lons)
    scan = flood.build_scan(obs, quantile=q, land=land)

    rows = _box_rows(lats, bbox)
    cols = _box_cols(lons, bbox)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    di = {d: k for k, d in enumerate(scan.dates)}
    didx = [di.get(d) for d in days]
    if any(k is None for k in didx):
        bad = [str(d) for d, k in zip(days, didx) if k is None]
        raise ValueError(f"event days outside the loaded observation window: {bad}")

    from ..events.temperature import _cell_area_km2

    cell_area = _cell_area_km2(lats, float(abs(lons[1] - lons[0])), lons.size)

    # event days only, cropped, columns re-ordered into one eastward run
    take = np.array(didx)
    sub = np.zeros((len(days), rows.size, cols.size), bool)
    for k, i in enumerate(take):
        sub[k] = scan.mask[i][np.ix_(rows, cols)] & land[np.ix_(rows, cols)]
    dur = np.zeros(sub.shape[1:], "uint8")
    run_cur = np.zeros(sub.shape[1:], "uint8")
    for k in range(sub.shape[0]):
        run_cur = ((run_cur + 1) * sub[k]).astype("uint8")
        np.maximum(dur, run_cur, out=dur)
    #: panel contract: the anchor day is the event's busiest day (most
    #: exceedant cells) — same rule as the heat/freeze build's anchor
    anchor = int(np.argmax([int(s.sum()) for s in sub])) if sub.shape[0] else 0

    series: list[dict[str, Any]] = []
    runs = obs.runs
    for k, d in enumerate(days):
        i = int(take[k])
        a1 = scan.obs_lane[1][i][np.ix_(rows, cols)]
        a3 = scan.obs_lane[3][i][np.ix_(rows, cols)]
        t1 = precip_clim.precip_threshold_on(lats[rows], lons[cols], [d], q, 1).values[0]
        act = sub[k]
        series.append({
            "day": f"{d:%Y-%m-%d}",
            "cells": int(act.sum()),
            "area_km2": round(float(cell_area[np.ix_(rows, cols)][act].sum()), 0)
            if act.any() else 0.0,
            "max_daily_mm": round(float(a1[act & np.isfinite(a1)].max()), 1)
            if (act & np.isfinite(a1)).any() else None,
            "max_3day_mm": round(float(a3[act & np.isfinite(a3)].max()), 1)
            if (act & np.isfinite(a3)).any() else None,
            "thr_daily_mm": round(float(np.nanmedian(t1[act])), 1)
            if act.any() and np.isfinite(t1[act]).any() else None,
            "run": runs.get(f"{d:%Y-%m-%d}", "unknown"),
        })
    peak1 = max((s["max_daily_mm"] or 0.0) for s in series)
    peak3 = max((s["max_3day_mm"] or 0.0) for s in series)

    lonc = lons[cols].astype("float64")
    if bbox[2] > bbox[3]:                      # wrapped: one continuous eastward run
        lonc = np.where(lonc < 0, lonc + 360.0, lonc)
    pardig = src_kind == "pardig"
    doc = {
        "schema": build.SCHEMA, "kind": "flood_accum", "event_type": "flood",
        "truth_source": "google_pardig_nrt" if pardig else "gpm_imerg_v07",
        "source_label": ("AI-estimated precipitation (Google PARDIG, CC BY 4.0)"
                         if pardig else "GPM IMERG V07"),
        "quantile": q,
        "floor_mm": flood.FLOOR_MM,
        "threshold_note": (("PARDIG-NRT AI-estimated rain (Google, CC BY 4.0) vs ERA5 "
                            "1990-2019 pooled wet-day p95 (0.25 deg lattice); no bias "
                            "correction applied — see notes/pardig_vs_imerg.md"
                            if pardig else
                            "IMERG rain vs ERA5 1990-2019 pooled wet-day p95 "
                            "(0.25 deg lattice); no bias correction applied — "
                            "see notes/audit_flood.md F1")),
        "grid": {"lat": lats[rows].tolist(), "lon": lonc.tolist(),
                 "cell_deg": CELL_DEG},
        "window": {"start": f"{start:%Y-%m-%d}", "end": f"{end - dt.timedelta(days=1):%Y-%m-%d}",
                   "days": [f"{d:%Y-%m-%d}" for d in days]},
        "obs": {
            "daily_bits": build.pack_bits(sub),
            # panel contract fields (eventviz unpacks daily_bits -> daily
            # strings itself): consec/max_consec/anchor_day/footprint_cells
            # are what the overlay layers, the duration bars and the chip
            # read for the heat/freeze lanes — flood rides the SAME renderer
            "consec": build.pack_u8(dur.astype("float64"), 0, max(sub.shape[0], 1)),
            "max_consec": int(dur.max()),
            "anchor_day": f"{days[anchor]:%Y-%m-%d}",
            "footprint_cells": int(sub[anchor].sum()),
            "peak_daily_mm": round(peak1, 1),
            "peak_3day_mm": round(peak3, 1) if peak3 else None,
            "peak_cell_lat": det.get("peak_cell_lat"),
            "peak_cell_lon": det.get("peak_cell_lon"),
            "series": series,
        },
        "models": {},
        "provisional": bool(det.get("provisional")),
        "provisional_reason": det.get("provisional_reason"),
        "detection": det.get("detection") or flood.DETECTION_TEXT,
        "units": "mm",
    }
    logger.info("flood viz %s: %d days, %dx%d cells, peak1d %.1f mm",
                ev["id"], len(days), rows.size, cols.size, peak1)
    return doc
