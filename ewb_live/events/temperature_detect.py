"""Area-tracked heat-wave / freeze detection (owner rule, 2026-11-10).

Replaces the upstream ``data_prep/temperature_events.py`` tracker for the
temperature event types. Upstream is a *blob* tracker whose rules do not
match the product contract:

  * ``expand_event_bounds`` grows every edge by 1 deg per iteration while
    >=50% of the land in the edge band is active, up to 20 iterations —
    live boxes reached 2657 x 2616 km (6.9M km2) off a 200k km2 seed.
  * ``_terminate_declined_events`` ends an event when its blob is absent on
    a day or falls below 50% of its OWN peak gridpoint count — a relative,
    peak-scaled rule with no absolute area floor.
  * seeds are not suppressed by live events, so one object can be tracked
    as 2-3 overlapping events (live: three heat waves starting 2026-09-12).

The contract implemented here (product owner, 2026-11-10):

  1. TRIGGER — a contiguous set of cells that all exceed the daily rule on
     day d and on each of the previous ``min_days - 1`` days, covering
     >= ``seed_area_km2`` (200,000 km2). The event's first day is the first
     day of that run, so a trigger seen on day d opens a 3-day event over
     d-2..d.
  2. BOX — the seed component's bounding box padded by ``pad_km`` (250 km)
     on every edge, fixed for the life of the event. The latitude pad is
     250 km in degrees; the longitude pad is 250 km converted at the seed's
     mean |latitude| (clamped at ``MAX_PAD_LAT`` so polar pads stay sane),
     rounded up to whole grid columns.
  3. END — the exceedance area inside that fixed box is recomputed every
     day; the first day it falls below ``end_area_km2`` (100,000 km2) is
     NOT part of the event and closes it (the event's last day is the day
     before). An event still above the floor when the scan window ends
     stays open (``ongoing = True``) and keeps its start date across runs.

A seed whose cells are >= ``SEED_ABSORB_FRACTION`` inside a live event's box
is absorbed (no new event) — that is what stops one object from being
tracked as several overlapping events.

Duration cap: over the Amazon, the Sahara/Sahel and the Gulf belts the daily
rule is exceeded all season, so a fixed box's in-box area never falls under
the 100k km2 floor and the literal rule yields 60-84 day "events" (measured
on the live 2026-08-11..11-03 window: 8 heat waves of 36-84 days, all of
which the publish gate refuses for exceeding its 31-day cap). ``max_duration
_days`` closes such an event at the cap instead, marking it
``duration_capped``; the scan passes ``EWB_LIVE_TEMP_MAX_DAYS`` (default 31,
0/None disables) so the choice is one env var.

Everything upstream of the box (climatology bounds, the daily exceedance
rule = all four 6-hourly steps passing, land mask, area grid) is unchanged
and comes from the same helpers ``ewb_live/events/temperature.py`` already
uses. This module is pure numpy/scipy: no network, no xarray, so it is
unit-testable on synthetic masks.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .seam import label_periodic, minimal_arc

SEED_AREA_KM2 = 200_000.0     # trigger floor (same value as upstream MIN_AREA_KM2)
END_AREA_KM2 = 100_000.0      # in-box area floor that keeps an event alive
BOX_PAD_KM = 250.0            # added to each edge of the seed bbox
MIN_CONSECUTIVE_DAYS = 3      # trigger persistence
KMS_PER_DEG = 111.32          # km per degree of latitude
MAX_PAD_LAT = 80.0            # clamp for the longitude-pad conversion
SEED_ABSORB_FRACTION = 0.5    # share of a seed inside a live box -> absorbed


def _col_indices(nlon: int, c0: int, c1: int) -> np.ndarray:
    """Column indices of the eastward arc c0 -> c1 (wrap when c0 > c1)."""
    if c0 <= c1:
        return np.arange(c0, c1 + 1)
    return np.concatenate([np.arange(c0, nlon), np.arange(0, c1 + 1)])


def _arc_cols(lons: np.ndarray, cols: np.ndarray) -> tuple[int, int]:
    """First/last column indices of the minimal arc containing `cols`."""
    nlon = len(lons)
    c = np.unique(cols)
    if len(c) == nlon:
        return 0, nlon - 1
    west, east = minimal_arc([(float(lons[i]), float(lons[i])) for i in c],
                             period=360.0)
    return (int(np.argmin(np.abs(lons - west))),
            int(np.argmin(np.abs(lons - east))))


def _runs_along_time(mask: np.ndarray) -> np.ndarray:
    """Longest run of True along axis 0 for every (lat, lon) cell."""
    cur = np.zeros(mask.shape[1:], dtype=np.int32)
    best = np.zeros_like(cur)
    for t in range(mask.shape[0]):
        cur = np.where(mask[t], cur + 1, 0)
        np.maximum(best, cur, out=best)
    return best


def detect_area_tracked_events(
    daily_mask: np.ndarray,
    dates: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    area_grid: np.ndarray,
    event_type: str = "heat_wave",
    *,
    t2m_daily: np.ndarray | None = None,
    seed_area_km2: float = SEED_AREA_KM2,
    end_area_km2: float = END_AREA_KM2,
    pad_km: float = BOX_PAD_KM,
    min_days: int = MIN_CONSECUTIVE_DAYS,
    max_duration_days: int | None = None,
) -> list[dict[str, Any]]:
    """Find heat/freeze events under the 200k seed / +250 km box / 100k end rule.

    Args:
        daily_mask: bool (days, lat, lon) — True where the cell passed the
            daily rule (all four 6-hourly steps) over land.
        dates: (days,) datetime64 labels aligned with axis 0.
        lats: (lat,) ascending latitude array in degrees.
        lons: (lon,) longitude array in the ARRAY's own convention (ERA5
            ARCO is 0..360; a blob touching both ends is one seam-crossing
            object — ``seam.label_periodic`` handles it).
        area_grid: (lat, lon) grid-cell areas in km2.
        event_type: "heat_wave" or "freeze" — drives the peak-temp polarity
            and the stored label only (the mask is already built).
        t2m_daily: optional float (days, lat, lon) daily-mean t2m in degC
            for peak/mean/min temperature stats over active cells in the box.
        seed_area_km2: trigger floor for the 3-day seed component.
        end_area_km2: in-box area floor that keeps an event alive.
        pad_km: expansion added to each edge of the seed bbox.
        min_days: consecutive exceedance days required to trigger.
        max_duration_days: optional hard cap on event length.

    Returns:
        One dict per event, sorted by start:
          type, start, end (datetime64[D], INCLUSIVE last day), trigger
          (the day the 3-day seed completed),
          lat_min/lat_max/lon_min/lon_max (padded box; lon in the array's
          convention, lon_min > lon_max = arc crossing the array seam),
          seed_area_km2, seed_cells, area_in_box_km2 (per event day),
          peak_area_km2, peak_cells, max_consecutive_days,
          mean_consecutive_days, ongoing, pad_km, and peak_temp_c /
          mean_temp_c / min_temp_c when t2m_daily is supplied.
    """
    m = np.asarray(daily_mask, bool)
    lats = np.asarray(lats, float)
    lons = np.asarray(lons, float)
    area = np.asarray(area_grid, float)
    n_days, n_lat, n_lon = m.shape
    if n_days < min_days or n_lat == 0 or n_lon == 0:
        return []

    dlat = float(np.abs(np.median(np.diff(lats)))) if n_lat > 1 else 0.25
    dlon = float(np.abs(np.median(np.diff(lons)))) if n_lon > 1 else 0.25
    row_pad = max(1, int(np.ceil(pad_km / (KMS_PER_DEG * dlat))))

    live: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []

    def box_area(ev: dict, day: int) -> float:
        sub = m[day][ev["r0"]:ev["r1"] + 1][:, ev["cols"]]
        return float(area[ev["r0"]:ev["r1"] + 1][:, ev["cols"]][sub].sum())

    for d in range(n_days):
        # ---- 1. advance live events: today's in-box area, then the end test
        for ev in list(live):
            a_today = box_area(ev, d)
            if a_today < end_area_km2 and d > ev["day_seed"]:
                ev["last_day"] = d - 1        # the sub-floor day is not part of it
                ev["ongoing"] = False
                live.remove(ev)
                closed.append(ev)
                continue
            ev["last_day"] = d
            if max_duration_days is not None and d - ev["day0"] + 1 >= max_duration_days:
                # the in-box area never fell below the floor (typical over the
                # Amazon / Sahara / Gulf belts, where p85 is exceeded all
                # season): close the event at the cap so it stays publishable
                # (validate.MAX_DURATION_DAYS) instead of being refused at the
                # gate. EWB_LIVE_TEMP_MAX_DAYS=0 disables the cap.
                ev["ongoing"] = False
                ev["duration_capped"] = True
                live.remove(ev)
                closed.append(ev)

        # ---- 2. seed test on day d (needs min_days of history)
        if d < min_days - 1:
            continue
        joint = m[d].copy()
        for k in range(1, min_days):
            joint &= m[d - k]
        if not joint.any():
            continue
        labels, n_obj = label_periodic(joint)
        if n_obj == 0:
            continue
        flat = labels.ravel()
        areas = np.bincount(flat, weights=area.ravel(), minlength=n_obj + 1)
        counts = np.bincount(flat, minlength=n_obj + 1)
        inside_cols = [ev["cols"] for ev in live]
        inside_rows = [(ev["r0"], ev["r1"]) for ev in live]

        order = [int(i) for i in np.argsort(-areas) if i != 0]
        for oid in order:                     # biggest seed first
            if areas[oid] < seed_area_km2:
                break
            rows, cols = np.where(labels == oid)
            r0, r1 = int(rows.min()), int(rows.max())

            # ---- 2a. absorb seeds that mostly sit inside an established box
            for (er0, er1), ecols in zip(inside_rows, inside_cols):
                if (((rows >= er0) & (rows <= er1) & np.isin(cols, ecols)).sum()
                        >= SEED_ABSORB_FRACTION * len(rows)):
                    break        # absorbed by this live event: no new event
            else:
                c_w, c_e = _arc_cols(lons, cols)

                # ---- 2b. +pad_km on every edge
                mean_abs_lat = min(MAX_PAD_LAT, float(np.abs(lats[rows]).mean()))
                col_km = (KMS_PER_DEG * dlon
                          * max(0.05, float(np.cos(np.radians(mean_abs_lat)))))
                col_pad = max(1, int(np.ceil(pad_km / col_km)))
                pr0, pr1 = max(0, r0 - row_pad), min(n_lat - 1, r1 + row_pad)
                pc0 = (c_w - col_pad) % n_lon
                pc1 = (c_e + col_pad) % n_lon
                box_cols = _col_indices(n_lon, pc0, pc1)
                if len(box_cols) >= n_lon:
                    pc0, pc1 = 0, n_lon - 1
                    box_cols = np.arange(n_lon)

                ev = {
                    "type": event_type,
                    "day0": d - (min_days - 1),
                    "day_seed": d,
                    "last_day": d,
                    "r0": pr0, "r1": pr1,
                    "c0": pc0, "c1": pc1,
                    "cols": box_cols,
                    "ongoing": True,
                    "seed_area_km2": float(areas[oid]),
                    "seed_cells": int(counts[oid]),
                    "seed_lat_min": float(lats[r0]),
                    "seed_lat_max": float(lats[r1]),
                    "pad_km": float(pad_km),
                    "duration_capped": False,
                }
                live.append(ev)
                inside_rows.append((pr0, pr1))
                inside_cols.append(box_cols)

    results = closed + live

    # ---- 3. finalise: dates, bbox, run + temperature stats inside the box
    out: list[dict[str, Any]] = []
    for ev in results:
        d0, d1 = ev["day0"], ev["last_day"]
        sub = m[d0:d1 + 1][:, ev["r0"]:ev["r1"] + 1, :][:, :, ev["cols"]]
        # in-box area per event day (the day that closed the event, when it
        # fell below the floor, is outside the window and stays out of the series)
        ab = area[ev["r0"]:ev["r1"] + 1][:, ev["cols"]]
        daily_area = (sub * ab).sum(axis=(1, 2)) if sub.size else np.zeros(0)
        ev["area_in_box_km2"] = [round(float(a), 1) for a in daily_area]
        ev["peak_area_km2"] = float(daily_area.max()) if daily_area.size else 0.0
        cell_counts = sub.sum(axis=(1, 2)) if sub.size else np.zeros(0, int)
        ev["peak_cells"] = int(cell_counts.max()) if cell_counts.size else 0
        consec = _runs_along_time(sub)
        ev["max_consecutive_days"] = int(consec.max()) if consec.size else 0
        active = consec[consec >= min_days]
        ev["mean_consecutive_days"] = (round(float(active.mean()), 2)
                                       if active.size else 0.0)
        if t2m_daily is not None:
            tsub = np.asarray(t2m_daily, np.float32)[d0:d1 + 1][
                :, ev["r0"]:ev["r1"] + 1, ][:, :, ev["cols"]]
            n = min(tsub.shape[0], sub.shape[0])
            vals = tsub[:n][sub[:n]]
            if vals.size:
                ev["peak_temp_c"] = (float(vals.max()) if event_type == "heat_wave"
                                     else float(vals.min()))
                ev["mean_temp_c"] = float(vals.mean())
                ev["min_temp_c"] = float(vals.min())
            else:
                ev["peak_temp_c"] = ev["mean_temp_c"] = ev["min_temp_c"] = float("nan")
        ev["start"] = np.datetime64(dates[d0], "D")
        ev["end"] = np.datetime64(dates[d1], "D")
        ev["trigger"] = np.datetime64(dates[ev["day_seed"]], "D")
        ev["lat_min"] = float(lats[ev["r0"]])
        ev["lat_max"] = float(lats[ev["r1"]])
        ev["lon_min"] = float(lons[ev["c0"]])
        ev["lon_max"] = float(lons[ev["c1"]])
        out.append(ev)

    out.sort(key=lambda e: (e["start"], -e["peak_area_km2"]))
    return out
