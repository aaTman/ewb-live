"""How EWB Live turns daily exceedance pixels into heat-wave and freeze events.

This is the detection rule of https://ewb.live (SPEC §2, "footprint/core
tracking") in one readable pass, so you can run the criteria yourself instead of
taking them on faith. The shipped implementation is `ewb_live/events/
event_tracker.py` (plus `temperature.py` for the thresholds); this listing
reproduces it, with two simplifications noted below, and the project's test
suite asserts the two agree on the same masks.

Input: one boolean (lat, lon) grid per UTC day — True where a cell *counts* on
that day, i.e. 2 m temperature is above the 85th percentile of the ERA5
1990-2019 climatology (heat) or below its 15th percentile (freeze) for that day
of year and hour at **all four** 6-hourly steps, and the cell is on land. The
grid is 0.25 degrees, longitudes ascending; longitude wraps, latitude does not.

Output: one dict per event — its start, its exclusive end, how long it lasted
and its largest footprint — oldest first.

Run `python3 detection.py` for a worked example, or feed it your own masks:

    events = detect(dates, {date: mask, ...}, cell_area_km2(lats))
"""
import datetime as dt

import numpy as np
from scipy import ndimage as ndi

SEED, QUALIFY, END = 5e4, 2e5, 1e5    # km2: seed a candidate, call it an event, end it
MIN_DAYS, RAMP, ANCHOR = 3, 5, 3      # days: shortest event, days to reach QUALIFY, anchor day
RADIUS, DENSITY, DRIFT = 6, 0.30, 8   # closing radius, min local density, box drift (cells)
MODE = ("nearest", "wrap")            # rows are latitude (no wrap), columns are longitude (wrap)


def cell_area_km2(lats, step_deg=0.25, nx=1440):
    """Area of each grid cell in km2 (EWB's formula, R = 6371 km)."""
    col = 6371.0 ** 2 * np.deg2rad(step_deg) ** 2 * np.cos(np.deg2rad(lats))
    return np.broadcast_to(np.clip(col, 0.0, None)[:, None], (lats.size, nx))


def span(cols):
    """First and last column of the shortest run covering all True columns."""
    idx = np.flatnonzero(cols)
    gaps = np.diff(np.r_[idx, idx[0] + cols.size])
    k = int(np.argmax(gaps))
    return (0, cols.size - 1) if gaps[k] == 1 else (int(idx[(k + 1) % idx.size]), int(idx[k]))


def box_cols(box, nx):
    """The box's columns: j0 > j1 means the run crosses the seam (the antimeridian)."""
    j0, j1 = box[2], box[3]
    cols = np.zeros(nx, bool)
    if j0 <= j1:
        cols[j0:j1 + 1] = True
    else:
        cols[j0:] = True
        cols[:j1 + 1] = True
    return cols


def box_mask(box, shape):
    """Box (i0, i1, j0, j1), inclusive on all four edges, as a boolean grid."""
    rows = np.zeros(shape[0], bool)
    rows[box[0]:box[1] + 1] = True
    return rows[:, None] & box_cols(box, shape[1])


def box_of(mask):
    """Smallest box covering a mask; the columns take the short way round."""
    rows = np.flatnonzero(mask.any(axis=1))
    j0, j1 = span(mask.any(axis=0))
    return int(rows[0]), int(rows[-1]), int(j0), int(j1)


def box_union(a, b, nx):
    """Smallest box covering both boxes."""
    j0, j1 = span(box_cols(a, nx) | box_cols(b, nx))
    return min(a[0], b[0]), max(a[1], b[1]), j0, j1


def regions(valid):
    """Label the cells that count: gaps up to 2*RADIUS wide are bridged, a thin
    scatter is not (at least DENSITY of the window around a cell must count)."""
    size = 2 * RADIUS + 1
    u8 = valid.view(np.uint8)
    closed = ndi.minimum_filter(ndi.maximum_filter(u8, size, mode=MODE), size, mode=MODE)
    dense = ndi.uniform_filter(valid.astype(np.float32), size, mode=MODE) >= DENSITY
    return ndi.label((closed & dense) | valid, structure=np.ones((3, 3), bool))[0]


def detect(days, daily, area, hazard="heat_wave"):
    """Run the rule one day at a time — it never looks ahead — and return the events.

    Simplified against the shipped tracker: two events that grow into each other
    stay separate instead of merging, and there is no cap on the box size.
    """
    streak = np.zeros(area.shape, np.int32)      # days each cell has counted in a row
    open_, done, seq = [], [], 0
    for day in days:
        valid = daily[day]
        weighted = valid * area
        streak = np.where(valid, streak + 1, 0)
        lab = regions(valid)
        region_area = ndi.sum(weighted, lab, np.arange(lab.max() + 1))
        region_area[0] = 0.0
        live = []
        for ev in open_:
            age = (day - ev["start"]).days + 1
            inside = box_mask(ev["box"], area.shape)
            # today's regions that continue this event: the ones inside its box,
            # claimed as far as DRIFT cells outside it, so the box can follow them
            share = np.isin(lab, np.unique(lab[inside & (lab > 0)]))
            footprint = share & (inside | ndi.maximum_filter(inside, 2 * DRIFT + 1, mode=MODE))
            if footprint.any():
                ev["box"] = box_union(ev["box"], box_of(footprint), area.shape[1])
            if ev["anchor"] is None and age >= ANCHOR:
                ev["anchor"] = ev["box"]         # the core is measured inside it from here
            core = float(weighted[box_mask(ev["anchor"] or ev["box"], area.shape)
                                  & (streak >= age)].sum())
            if ev["qualified"] is None:
                if float(weighted[footprint].sum()) >= QUALIFY:
                    ev["qualified"] = day
                elif not footprint.any() or (day - ev["start"]).days >= RAMP:
                    continue                     # it never became an event
            if core < END:
                ev["end"] = day                  # this day is not part of the event
                done.append(ev)
                continue
            ev.setdefault("areas", []).append(float(weighted[footprint].sum()))
            live.append(ev)
        occupied = np.zeros(area.shape, bool)    # a seed may not start in a live box
        for ev in live:
            occupied |= box_mask(ev["box"], area.shape)
        for label in np.flatnonzero(region_area >= SEED):
            region = lab == label
            if (occupied & region).any():
                continue
            seq += 1
            live.append(dict(id=f"{hazard}-{day:%Y%m%d}-{seq}", hazard=hazard, start=day,
                             box=box_of(region), anchor=None, end=None,
                             areas=[float(region_area[label])],
                             qualified=day if region_area[label] >= QUALIFY else None))
        open_ = live
    for ev in open_:                             # still running on the last day
        ev["end"] = None
        done.append(ev)
    for ev in done:
        last = ev["end"] or days[-1] + dt.timedelta(days=1)   # end is exclusive
        ev["duration_days"] = (last - ev["start"]).days
        ev["peak_area_km2"] = max(ev["areas"])
    return sorted((ev for ev in done if ev["qualified"] and ev["duration_days"] >= MIN_DAYS),
                  key=lambda ev: ev["start"])


if __name__ == "__main__":
    # A ~300,000 km2 patch counts for four days and then stops; next to it, a
    # ~75,000 km2 patch counts all week but is too small to ever qualify.
    lats = np.arange(-60.0, 60.0, 0.25)
    area = cell_area_km2(lats)
    days = [dt.date(2026, 9, 1) + dt.timedelta(days=k) for k in range(7)]
    daily = {}
    for i, day in enumerate(days):
        m = np.zeros((lats.size, 1440), bool)
        m[300:320, 600:620] = i < 4                 # the heat wave: 4 days
        m[400:410, 900:910] = True                  # the too-small patch: 7 days
        daily[day] = m
    for ev in detect(days, daily, area):
        i0, i1, j0, j1 = ev["box"]
        print(f"{ev['id']:<24} {ev['start']} -> {str(ev['end'] or 'ongoing'):<11} "
              f"{ev['duration_days']} d   peak {ev['peak_area_km2'] / 1e6:.2f}M km2   "
              f"box rows {i0}-{i1} cols {j0}-{j1}")
