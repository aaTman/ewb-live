"""Review: why are heat-wave/freeze boxes bigger than the cells that qualify?

Phase 1+2 of ``notes/heatwave_box_diagnosis.md``. It replays the production
tracker (``ewb_live/events/event_tracker.py``, SPEC §2) on the same lane frames
``scripts/review/evt02_tracker_port_compare.py`` uses, and for every spell

* measures the ``fill`` of its published box — the share of the box's land area
  made of cells that pass the daily rule on >= 1 event day — plus the daily
  fill, the box/peak-area ratio and the excess area outside the bbox of those
  cells;
* records every day the box grew and WHY, attributing the added area to the
  growth hypotheses H1..H5 (H6/H7 are audited separately, see
  ``notes/heatwave_box_diagnosis.md``);
* re-scores the tightening strategies S1..S7 (parameter sets plus a
  ``Tracker`` subclass that lives only in this file — nothing in ``ewb_live/``
  is changed) on identical inputs so they can be compared on one table.

The daily mask is the tracker's own input: the published ``raw_exceedance``
frames ANDed over the four 00/06/12/18Z steps, land only (SPEC §2 "Mask
source"), so "a valid heat-wave point" here is exactly a cell the detector
counted. Freeze gets the same metrics as a side column (shared rule).

Run on the ewb LXC from the scratch tree (production stays read-only):

    cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
      timeout 600 /opt/ewb-live/.venv/bin/python \
        scripts/review/hb01_box_growth.py \
          --lane /opt/ewb-live/data/results/raw_exceedance \
          --published /opt/ewb-live/data/results/events.json \
          --start 2026-08-21 --end 2026-11-13 --selfcheck \
          --out /opt/ewb-heatbox/out/hb01

Writes ``<out>_<hazard>.json`` (per-strategy metrics, per-event rows and the
growth log) and ``<out>_summary.json``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))     # the repo root
sys.path.insert(0, str(_HERE.parent))         # scripts/review, for evt02

import numpy as np  # noqa: E402
from scipy import ndimage as ndi  # noqa: E402

from ewb_live.events import frame_masks  # noqa: E402
from ewb_live.events.event_tracker import (  # noqa: E402
    Box, Params, TrackedEvent, Tracker, circular_span, dilate, label_regions,
    merge_groups,
)
from ewb_live.events.temperature import _cell_area_km2  # noqa: E402

# the greedy bbox/day matcher from the tracker-port review, reused verbatim
_evt02 = importlib.import_module("evt02_tracker_port_compare")

HAZARDS = ("heat_wave", "freeze")
# growth hypotheses the attribution can charge area to (H6 = downstream
# curation, H7 = seam; both audited outside the per-day log)
CAUSES = ("H1_contiguous", "H2_merge", "H3_region_claim", "H4_closing_fill",
          "H5_drift_hop", "H0_geometry")


# --------------------------------------------------------------------- window
@dataclass(frozen=True)
class Window:
    """One hazard's daily masks over the replay window."""

    hazard: str
    dates: list[dt.date]
    lats: np.ndarray
    lons: np.ndarray
    mask: np.ndarray          # (days, ny, nx) — the tracker's daily valid mask
    cell_area: np.ndarray     # (ny, nx) km2
    land: np.ndarray          # (ny, nx) bool, Natural Earth 110


def load_window(lane: Path, start: dt.date, end: dt.date, hazard: str,
                land: np.ndarray | None = None) -> Window:
    """Daily masks straight out of the published frames lane (read-only)."""
    dates, lats, lons, masks = frame_masks.daily_masks(lane)
    sel = [i for i, d in enumerate(dates) if start <= d <= end]
    if not sel:
        raise SystemExit(f"lane {lane} has no complete days in {start}..{end}")
    dates = [dates[i] for i in sel]
    step = float(abs(lons[1] - lons[0]))
    if land is None:
        from ewb_live.viz.build import land_mask

        land = land_mask(lats, lons)
    return Window(hazard, dates, lats, lons, masks[hazard][sel],
                  _cell_area_km2(lats, step, lons.size), land)


# ------------------------------------------------------------------- objects
@dataclass
class Spell:
    """One tracked spell, with the box and day range the tracker gave it."""

    hazard: str
    id: str
    start: dt.date
    end: dt.date | None       # exclusive; None = still open at the window end
    i0: int                   # first event day, index into Window.dates
    i1: int                   # last event day, inclusive
    box: Box
    peak_km2: float
    ongoing: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "hazard": self.hazard, "start": str(self.start),
                "end": str(self.end) if self.end else None,
                "days": self.i1 - self.i0 + 1,
                "box": [self.box.i0, self.box.i1, self.box.j0, self.box.j1],
                "peak_km2": round(self.peak_km2, 1),
                "ongoing": self.ongoing}


@dataclass
class Published:
    """A spell plus the box the dashboard would draw and score."""

    spell: Spell
    box: Box
    tag: str = ""             # "c0"/"c1"/... for a component of a split spell

    @property
    def id(self) -> str:
        return self.spell.id + (f"-{self.tag}" if self.tag else "")


# ------------------------------------------------------- tuned tracker (S3/5/7)
class TunedTracker(Tracker):
    """``Tracker`` with the three box rules the strategies need, all off by default.

    With every flag off this reproduces :class:`~ewb_live.events.event_tracker.Tracker`
    exactly (``--selfcheck`` proves it), so a strategy can change the rule
    without touching production code.

    Args:
        claim_overlap: claim only the region cells that are valid today AND
            within ``move_cells`` of the current footprint (S3), instead of
            every cell of a claimed region, closing-filled cells included.
        seed_valid_only: a new candidate's box/footprint is the region's *valid*
            cells, not the whole labelled region (S3's other half: a seed box
            that spans a closing-filled gap is born too big).
        merge_keeps_older_box: on a merge keep the surviving (older) event's box
            and clip the partner's cells to it (S5) instead of unioning boxes.
        rolling_days: re-derive the box each day from the footprints of the last
            ``rolling_days`` days so it may shrink (S7); None = cumulative box.
    """

    def __init__(self, cell_area: np.ndarray, params: Params, hazard: str,
                 claim_overlap: bool = False, seed_valid_only: bool = False,
                 merge_keeps_older_box: bool = False,
                 rolling_days: int | None = None) -> None:
        super().__init__(cell_area, params, hazard)
        self.claim_overlap = claim_overlap
        self.seed_valid_only = seed_valid_only
        self.merge_keeps_older_box = merge_keeps_older_box
        self.rolling_days = rolling_days
        self.box_hist: dict[str, list[Box]] = {}

    def step(self, day: dt.date, valid: np.ndarray) -> list[TrackedEvent]:
        """One day: the stock step with the box rules above swapped in."""
        p = self.params
        ny, nx = valid.shape
        weighted = valid * self.cell_area
        self.streak = np.where(valid, self.streak + 1, 0)
        labels = label_regions(valid, p.region_radius, p.region_min_fraction)
        region_area = ndi.sum(weighted, labels, np.arange(labels.max() + 1))
        region_area[0] = 0.0

        hits = [self.follow(ev, labels, region_area) for ev in self.active]
        for k, ev in enumerate(self.active):
            if ev.qualified is not None and self.ending(ev, hits[k], labels,
                                                        weighted, day):
                hits[k] = set()   # release its regions, as the stock step does
        groups = merge_groups(hits, [ev.box for ev in self.active],
                              self.fits, nx)
        groups.sort(key=lambda g: (self.active[g[0]].qualified is None, g[0]))
        taken = np.zeros((ny, nx), bool)
        kept: list[TrackedEvent] = []
        ended: list[TrackedEvent] = []
        for group in groups:
            ev = self.active[group[0]]
            group_hits = set().union(*(hits[k] for k in group))
            merged_in = False
            for k in group[1:]:
                other = self.active[k]
                if self.merge_keeps_older_box:
                    merged_in = True
                else:
                    ev.box = ev.box.union(other.box, nx)
                if other.qualified is not None and (
                        ev.qualified is None
                        or other.qualified < ev.qualified):
                    ev.qualified = other.qualified
            share = (np.isin(labels, list(group_hits)) & ~taken if group_hits
                     else np.zeros((ny, nx), bool))
            if self.claim_overlap and ev.footprint.any():
                share &= valid & dilate(ev.footprint, p.move_cells)
            if merged_in:
                share &= ev.box.mask(ny, nx)
            if share.any():
                grown = ev.box.union(Box.from_mask(share), nx)
                if self.fits(grown):
                    ev.box = grown
                else:
                    share &= ev.box.mask(ny, nx)
            if share.any():
                ev.footprint = share
            if self.rolling_days:
                ev.box = self._rolling_box(ev, share, nx)
            footprint_area = float(weighted[share].sum())
            box_area = float(weighted[ev.box.mask(ny, nx)].sum())
            if ev.qualified is None:
                if footprint_area >= p.qualify_area_km2:
                    ev.qualified = day
                elif not share.any() or (day - ev.start).days >= p.max_ramp_days:
                    continue
            if (p.anchor_day is not None and ev.anchor is None
                    and (day - ev.start).days + 1 >= p.anchor_day):
                ev.anchor = ev.box
            core_area = self.core_area(ev, ev.anchor or ev.box, day, weighted)
            if ev.qualified is not None and core_area < p.end_area_km2:
                ev.end = day
                ended.append(ev)
                continue
            taken |= share
            ev.footprint_area_km2.append(footprint_area)
            ev.box_area_km2.append(box_area)
            ev.core_area_km2.append(core_area)
            kept.append(ev)

        occupied = np.zeros((ny, nx), bool)
        for ev in kept:
            occupied |= ev.box.mask(ny, nx)
        free_area = ndi.sum(weighted * ~taken, labels,
                            np.arange(labels.max() + 1))
        free_area[0] = 0.0
        for label in np.flatnonzero(free_area >= p.seed_area_km2):
            region = (labels == label) & ~taken
            if self.seed_valid_only:
                region &= valid
                if not region.any():
                    continue
            if (occupied & region).any():
                continue
            box = Box.from_mask(region)
            if not self.fits(box):
                continue
            self.count += 1
            ev = TrackedEvent(f"{self.hazard}-{day:%Y%m%d}-{self.count}",
                              self.hazard, day, box)
            ev.footprint = region
            area = float(free_area[label])
            if area >= p.qualify_area_km2:
                ev.qualified = day
            ev.footprint_area_km2.append(area)
            ev.box_area_km2.append(float(weighted[ev.box.mask(ny, nx)].sum()))
            ev.core_area_km2.append(ev.box_area_km2[-1])
            if p.anchor_day is not None and p.anchor_day <= 1:
                ev.anchor = box
            occupied |= ev.box.mask(ny, nx)
            kept.append(ev)
        self.active = sorted(kept, key=lambda e: (e.start, e.id))
        return ended

    def _rolling_box(self, ev: TrackedEvent, share: np.ndarray,
                     nx: int) -> Box:
        """Bbox of the footprints of the last ``rolling_days`` days."""
        hist = self.box_hist.setdefault(ev.id, [])
        hist.append(Box.from_mask(share) if share.any() else ev.box)
        del hist[:-self.rolling_days]
        box = hist[0]
        for b in hist[1:]:
            box = box.union(b, nx)
        return box


class LoggingTracker(TunedTracker):
    """Records every day an open object's box grew and the reason (H1..H5).

    Cell categories of the added area:
      H2_merge        inside the box of a partner that was merged away today
      H4_closing_fill inside today's labelled region but not valid today
      H1_contiguous   valid today, adjacent to yesterday's footprint
      H5_drift_hop    valid today, reached only through the ``move_cells`` dilation
      H3_region_claim valid today but outside that dilation — only a
                      whole-region claim can reach those cells
      H0_geometry     neither valid nor region: the bbox rectangle's own
                      interior, which follows from the day's driving cells
    """

    def __init__(self, cell_area: np.ndarray, params: Params, hazard: str,
                 land: np.ndarray) -> None:
        super().__init__(cell_area, params, hazard)
        self.land = land
        self.growth: list[dict] = []
        self.seeded: list[dict] = []
        self.ended_all: list[TrackedEvent] = []

    def step(self, day: dt.date, valid: np.ndarray) -> list[TrackedEvent]:
        before = {ev.id: (ev.box, ev.footprint.copy()) for ev in self.active}
        ended = super().step(day, valid)
        self.ended_all += ended
        self._record(day, valid, before, ended)
        return ended

    def _record(self, day: dt.date, valid: np.ndarray, before: dict,
                ended: list[TrackedEvent]) -> None:
        ny, nx = valid.shape
        after = {ev.id: ev for ev in self.active}
        ended_ids = {ev.id for ev in ended}
        partners = [b for eid, (b, _f) in before.items()
                    if eid not in after and eid not in ended_ids]
        merged = np.zeros((ny, nx), bool)
        for b in partners:
            merged |= b.mask(ny, nx)
        region = label_regions(valid, self.params.region_radius,
                               self.params.region_min_fraction) > 0
        for eid, (old_box, fp) in before.items():
            ev = after.get(eid)
            if ev is None or ev.box == old_box:
                continue
            ext = ev.box.mask(ny, nx) & ~old_box.mask(ny, nx)
            near1 = dilate(fp, 1)
            near8 = dilate(fp, self.params.move_cells)
            cats = {
                "H2_merge": ext & merged,
                "H4_closing_fill": ext & ~merged & region & ~valid,
                "H1_contiguous": ext & ~merged & valid & near1,
                "H5_drift_hop": ext & ~merged & valid & ~near1 & near8,
                "H3_region_claim": ext & ~merged & valid & ~near8,
                "H0_geometry": ext & ~merged & ~valid & ~region,
            }
            areas = {k: float(self.cell_area[m].sum()) for k, m in cats.items()}
            causal = {k: v for k, v in areas.items() if k != "H0_geometry"}
            self.growth.append({
                "day": str(day), "id": ev.id,
                "added_km2": round(sum(areas.values())),
                "added_land_km2": round(float(
                    self.cell_area[ext & self.land].sum())),
                "cats": {k: round(v) for k, v in areas.items()},
                # the bbox rectangle's interior is never a cause of its own: it
                # is charged to the cells that made the box grow today
                "driver": max(causal, key=lambda k: causal[k]) if causal
                else "H0_geometry",
                "wrapped": ev.box.j0 > ev.box.j1 and old_box.j0 <= old_box.j1,
            })
        for ev in self.active:
            if ev.id in before or ev.start != day:
                continue
            self.seeded.append({"day": str(day), "id": ev.id, "start_km2": round(
                float(self.cell_area[ev.box.mask(ny, nx)].sum()))})
        for ev in ended:
            if ev.start == day:
                self.seeded.append({"day": str(day), "id": ev.id,
                                    "start_km2": round(float(
                                        self.cell_area[ev.box.mask(ny, nx)].sum()))})


def replay_raw(w: Window, params: Params, **tweaks: Any) -> list[TrackedEvent]:
    """Run the tuned tracker over the window and return its valid events."""
    tracker = TunedTracker(w.cell_area, params, w.hazard, **tweaks)
    done: list[TrackedEvent] = []
    for day, frame in zip(w.dates, w.mask, strict=True):
        done += tracker.step(day, frame)
    events = [ev for ev in [*done, *tracker.active] if ev.is_valid(params)]
    return sorted(events, key=lambda e: (e.start, e.id))   # as track_events does


def replay(w: Window, params: Params, **tweaks: Any) -> list[Spell]:
    """One tracker pass, wrapped as :class:`Spell` rows."""
    idx = {d: i for i, d in enumerate(w.dates)}
    out: list[Spell] = []
    for ev in replay_raw(w, params, **tweaks):
        last = (ev.end - dt.timedelta(days=1)) if ev.end else w.dates[-1]
        out.append(Spell(w.hazard, ev.id, ev.start, ev.end, idx[ev.start],
                         idx[last], ev.box, float(ev.peak_area_km2),
                         ev.end is None))
    return out


def selfcheck(w: Window, params: Params) -> bool:
    """With all flags off the tuned tracker must equal the shipped tracker."""
    from ewb_live.events.event_tracker import track_events

    key = lambda ev: (ev.id, str(ev.start), str(ev.end),  # noqa: E731
                      (ev.box.i0, ev.box.i1, ev.box.j0, ev.box.j1))
    ref = [key(ev) for ev in track_events(w.mask, w.dates, w.cell_area,
                                          params, w.hazard)]
    mine = [key(ev) for ev in replay_raw(w, params)]
    ok = ref == mine
    print(f"selfcheck {w.hazard}: {'PASS' if ok else 'FAIL'} "
          f"({len(ref)} shipped vs {len(mine)} tuned events)", flush=True)
    return ok


# ------------------------------------------------------------- box arithmetic
def arc_cols(m: np.ndarray, nx: int) -> np.ndarray:
    """Continuous column indices of the shortest arc covering ``m``'s columns."""
    j0, j1 = circular_span(m.any(axis=0))
    return (j0 + np.arange((j1 - j0) % nx + 1)) % nx


def weighted_quantile(values: np.ndarray, weights: np.ndarray,
                      q: float) -> float:
    """Area-weighted ``q``-quantile of ascending ``values``."""
    w = np.cumsum(weights)
    if not w.size or w[-1] <= 0:
        return float(values[0]) if values.size else 0.0
    return float(values[int(np.searchsorted(w / w[-1], q))])


def persistence_box(w: Window, i0: int, i1: int, spell_box: Box, k_days: int,
                    quantile: float | None = None) -> Box | None:
    """Bbox of the spell's cells valid on >= ``k_days`` of its days (S2).

    Only the spell's own box counts (other spells' cells are not its event).
    ``quantile`` first trims to the area-weighted ``[q, 1-q]`` lat/lon range of
    those cells, then re-takes the bbox. None when the spell has no valid cell.
    """
    days = w.mask[i0:i1 + 1]
    ny, nx = w.mask.shape[1:]
    boxmask = spell_box.mask(ny, nx)
    ever = days.any(axis=0) & boxmask
    if not ever.any():
        return None
    keep = (days.sum(axis=0) >= max(1, k_days)) & boxmask
    if not keep.any():
        keep = ever
    cols = arc_cols(keep, nx)
    sub = keep[:, cols]
    area = w.cell_area[:, cols]
    i_lo = c_lo = 0
    if quantile is not None:
        row_idx = np.arange(ny)
        row_w = (sub * area).sum(axis=1)
        col_idx = np.arange(cols.size)
        col_w = (sub * area).sum(axis=0)
        i_lo = int(np.floor(weighted_quantile(row_idx, row_w, quantile)))
        i_hi = int(np.ceil(weighted_quantile(row_idx, row_w, 1 - quantile)))
        c_lo = int(np.floor(weighted_quantile(col_idx, col_w, quantile)))
        c_hi = int(np.ceil(weighted_quantile(col_idx, col_w, 1 - quantile)))
        sub = sub[max(0, i_lo):i_hi + 1, max(0, c_lo):c_hi + 1]
        i_lo = max(0, i_lo)
        c_lo = max(0, c_lo)
        if not sub.any():
            cols = arc_cols(ever, nx)
            sub = ever[:, cols]
            i_lo = c_lo = 0
    rr = np.flatnonzero(sub.any(axis=1))
    cc = np.flatnonzero(sub.any(axis=0))
    if rr.size == 0 or cc.size == 0:
        return None
    return Box(int(i_lo + rr[0]), int(i_lo + rr[-1]),
               int(cols[c_lo + cc[0]]), int(cols[c_lo + cc[-1]]))


def component_boxes(w: Window, i0: int, i1: int, box: Box,
                    min_area_km2: float) -> list[Box]:
    """Components of the spell's valid cells >= ``min_area_km2`` (S6)."""
    ever = w.mask[i0:i1 + 1].any(axis=0) & box.mask(*w.mask.shape[1:])
    if not ever.any():
        return []
    labels = label_regions(ever, 0, 0.0)      # 8-conn, seam-aware
    areas = ndi.sum(w.cell_area, labels, np.arange(labels.max() + 1))
    areas[0] = 0.0                            # label 0 = outside every region
    return [Box.from_mask(labels == lab)
            for lab in np.flatnonzero(areas >= min_area_km2)]


# ------------------------------------------------------------------- metrics
def box_from_bbox(w: Window, bbox: tuple[float, float, float,
                                         float]) -> Box:
    """A published ``(lat0, lat1, lon0, lon1)`` box as grid indices."""
    i0 = int(np.argmin(abs(w.lats - bbox[0])))
    i1 = int(np.argmin(abs(w.lats - bbox[1])))
    step = float(abs(w.lons[1] - w.lons[0]))
    nx = w.lons.size
    return Box(i0, i1, int(round((bbox[2] - w.lons[0]) / step)) % nx,
               int(round((bbox[3] - w.lons[0]) / step)) % nx)


def score_one(w: Window, p: Published) -> dict:
    """Fill / area metrics of one published box."""
    ny, nx = w.mask.shape[1:]
    boxmask = p.box.mask(ny, nx)
    days = w.mask[p.spell.i0:p.spell.i1 + 1]
    ever = days.any(axis=0) & boxmask
    land_box = w.land & boxmask
    land_km2 = float(w.cell_area[land_box].sum())
    ever_km2 = float(w.cell_area[ever & land_box].sum())
    daily = ([float(w.cell_area[days[d] & land_box].sum()) / land_km2
              for d in range(days.shape[0])] if land_km2 > 0 else [0.0])
    ever_box = Box.from_mask(ever) if ever.any() else p.box
    inside_land = float(w.cell_area[w.land
                                    & ever_box.mask(ny, nx)].sum())
    grid_km2 = float(w.cell_area[boxmask].sum())
    return {
        "id": p.id, "parent": p.spell.id, "start": str(p.spell.start),
        "end": str(w.dates[p.spell.i1]), "days": p.spell.i1 - p.spell.i0 + 1,
        "ongoing": p.spell.ongoing,
        "bbox": [float(w.lats[p.box.i0]), float(w.lats[p.box.i1]),
                 float(w.lons[p.box.j0]), float(w.lons[p.box.j1])],
        "box_km2": grid_km2, "box_land_km2": land_km2,
        "valid_ever_km2": ever_km2,
        "fill": ever_km2 / land_km2 if land_km2 > 0 else 0.0,
        "daily_fill_med": float(np.median(daily)),
        "peak_km2": round(p.spell.peak_km2, 1),
        "box_over_peak": grid_km2 / max(p.spell.peak_km2, 1.0),
        "excess_out_km2": max(0.0, land_km2 - inside_land),
        "hole_km2": max(0.0, inside_land - ever_km2),
        "lat_span": float(w.lats[p.box.i1] - w.lats[p.box.i0]),
        "lon_span": float((p.box.j1 - p.box.j0) % nx)
        * float(abs(w.lons[1] - w.lons[0])),
    }


def _q(vals: list[float], q: float) -> float:
    return float(np.percentile(vals, q)) if vals else 0.0


def aggregate(rows: list[dict], runtime_s: float) -> dict:
    """Summary numbers of one strategy for one hazard."""
    if not rows:
        return {"n": 0, "runtime_s": round(runtime_s, 1)}
    fill = [r["fill"] for r in rows]
    box = [r["box_km2"] for r in rows]
    dur = [r["days"] for r in rows]
    ratio = [r["box_over_peak"] for r in rows]
    worst = sorted(rows, key=lambda r: -r["box_km2"])[:3]
    return {
        "n": len(rows),
        "fill_med": round(float(np.median(fill)), 3),
        "fill_p10": round(_q(fill, 10), 3),
        "fill_min": round(min(fill), 3),
        "daily_fill_med": round(float(np.median(
            [r["daily_fill_med"] for r in rows])), 3),
        "box_km2_med": round(float(np.median(box))),
        "box_km2_p90": round(_q(box, 90)),
        "box_km2_max": round(max(box)),
        "box_over_peak_med": round(float(np.median(ratio)), 2),
        "box_over_peak_p90": round(_q(ratio, 90), 2),
        "days_med": float(np.median(dur)), "days_max": max(dur),
        "excess_out_km2_tot": round(sum(r["excess_out_km2"] for r in rows)),
        "hole_km2_tot": round(sum(r["hole_km2"] for r in rows)),
        "worst3": [{"id": r["id"], "start": r["start"], "end": r["end"],
                    "box_km2": round(r["box_km2"]),
                    "bbox": [round(x, 1) for x in r["bbox"]],
                    "valid_ever_km2": round(r["valid_ever_km2"]),
                    "fill": round(r["fill"], 3)} for r in worst],
        "runtime_s": round(runtime_s, 1),
    }


# --------------------------------------------------------- growth attribution
def attribute(w: Window, params: Params) -> dict:
    """Replay with per-day logging and sum up the growth causes (H1..H5)."""
    tracker = LoggingTracker(w.cell_area, params, w.hazard, w.land)
    for day, frame in zip(w.dates, w.mask, strict=True):
        tracker.step(day, frame)
    valid_ids = ({ev.id for ev in tracker.ended_all if ev.is_valid(params)}
                 | {ev.id for ev in tracker.active if ev.is_valid(params)})
    seed_of = {s["id"]: s["start_km2"] for s in tracker.seeded}
    seeds_valid = [v for k, v in seed_of.items() if k in valid_ids]
    total = sum(g["added_km2"] for g in tracker.growth)
    by_cat: dict[str, float] = {k: 0.0 for k in CAUSES}
    by_driver: dict[str, float] = {}
    attributed: dict[str, float] = {k: 0.0 for k in CAUSES}
    for g in tracker.growth:
        cats = dict(g["cats"])
        for k, v in cats.items():
            by_cat[k] += v
        by_driver[g["driver"]] = by_driver.get(g["driver"], 0.0) + g["added_km2"]
        # the bbox rectangle's interior follows from the day's driving cells
        geo = cats.pop("H0_geometry", 0.0)
        attributed[g["driver"]] += geo
        for k, v in cats.items():
            attributed[k] += v
    return {"growth_days": len(tracker.growth),
            "n_events_with_growth": len({g["id"] for g in tracker.growth}),
            "n_valid_events": len(valid_ids),
            "added_km2_total": round(total),
            "n_seeds": len(tracker.seeded),
            "seed_box_km2_total": round(sum(s["start_km2"]
                                            for s in tracker.seeded)),
            "seed_box_valid_n": len(seeds_valid),
            "seed_box_valid_med_km2": round(float(np.median(seeds_valid)))
            if seeds_valid else 0,
            "seed_box_valid_max_km2": max(seeds_valid, default=0),
            "seed_box_valid_km2_tot": round(sum(seeds_valid)),
            "added_km2_valid_total": round(sum(
                g["added_km2"] for g in tracker.growth
                if g["id"] in valid_ids)),
            "cells_by_cat_km2": {k: round(v) for k, v in by_cat.items()},
            "by_driver_km2": {k: round(v) for k, v in by_driver.items()},
            "attributed_km2": {k: round(v) for k, v in attributed.items()},
            "attributed_share": {k: (round(v / total, 3) if total else 0.0)
                                 for k, v in attributed.items()},
            "wrapped_growths": sum(1 for g in tracker.growth if g["wrapped"]),
            "largest_growth_days": sorted(tracker.growth,
                                          key=lambda g: -g["added_km2"])[:12],
            "per_event": _per_event(tracker.growth, seed_of)}


def _per_event(growth: list[dict], seed_of: dict[str, float]) -> list[dict]:
    out: dict[str, dict] = {}
    for g in growth:
        r = out.setdefault(g["id"], {"id": g["id"], "start": g["day"],
                                     "added_km2": 0.0, "growth_days": 0,
                                     "by_cat": {k: 0.0 for k in CAUSES},
                                     "by_driver": {k: 0.0 for k in CAUSES}})
        r["added_km2"] += g["added_km2"]
        r["growth_days"] += 1
        r["start"] = min(r["start"], g["day"])
        r["by_driver"][g["driver"]] += g["added_km2"]
        for k, v in g["cats"].items():
            r["by_cat"][k] += v
    rows = sorted(out.values(), key=lambda r: -r["added_km2"])
    for r in rows:
        r["added_km2"] = round(r["added_km2"])
        r["seed_box_km2"] = seed_of.get(r["id"], 0)
        r["by_cat"] = {k: round(v) for k, v in r["by_cat"].items()}
        r["by_driver"] = {k: round(v) for k, v in r["by_driver"].items()}
        # the day-level driver already charges the rectangle interior to the
        # cells that caused the growth, so it is the fair summary
        r["dominant"] = max(r["by_driver"], key=lambda k: r["by_driver"][k])
    return rows[:15]


def seam_audit(w: Window, spells: list[Spell]) -> dict:
    """H7: does any published box take the long way round the seam?"""
    ny, nx = w.mask.shape[1:]
    rows = []
    for s in spells:
        ever = (w.mask[s.i0:s.i1 + 1].any(axis=0) & s.box.mask(ny, nx))
        if not ever.any():
            continue
        need = arc_cols(ever, nx).size
        have = int(s.box.columns(nx).sum())
        if have - need > 8:
            rows.append({"id": s.id, "start": str(s.start), "box_cols": have,
                         "needed_cols": int(need),
                         "wrapped": s.box.j0 > s.box.j1})
    return {"n_checked": len(spells), "n_flagged": len(rows),
            "flagged": sorted(rows, key=lambda r:
                              -(r["box_cols"] - r["needed_cols"]))[:10]}


# ---------------------------------------------------------------- strategies
@dataclass
class Strategy:
    """One way of producing the published box from the daily masks."""

    name: str
    params: dict = field(default_factory=dict)
    tweaks: dict = field(default_factory=dict)
    post: Callable[[Window, Spell], list[Published]] | None = None
    note: str = ""


def persist_k(ratio: float, floor: int = 2) -> Callable[[int], int]:
    """``max(floor, ceil(ratio * days))`` days of persistence."""
    return lambda days: max(floor, int(np.ceil(ratio * days)))


def post_persist(k_days: Callable[[int], int],
                 quantile: float | None = None):
    """Publish the persistence box (S2) of each spell."""
    def f(w: Window, s: Spell) -> list[Published]:
        box = persistence_box(w, s.i0, s.i1, s.box,
                              k_days(s.i1 - s.i0 + 1), quantile)
        return [Published(s, box or s.box)]
    return f


def post_persist_trailing(k_days: Callable[[int], int], days: int):
    """Persistence box over the trailing ``days`` event days (S7b)."""
    def f(w: Window, s: Spell) -> list[Published]:
        i0 = max(s.i0, s.i1 - days + 1)
        box = persistence_box(w, i0, s.i1, s.box, k_days(s.i1 - i0 + 1))
        return [Published(s, box or s.box)]
    return f


def post_split(fill_threshold: float, min_area_km2: float):
    """Split a low-fill spell into its components (S6)."""
    def f(w: Window, s: Spell) -> list[Published]:
        only = [Published(s, s.box)]
        if score_one(w, only[0])["fill"] >= fill_threshold:
            return only
        boxes = component_boxes(w, s.i0, s.i1, s.box, min_area_km2)
        if len(boxes) < 2:
            return only
        return [Published(s, b, tag=f"c{i}") for i, b in enumerate(boxes)]
    return f


STRATEGIES: list[Strategy] = [
    Strategy("S0_current", note="as shipped: cumulative tracker box"),
    Strategy("S1a_cap_3Mkm2", params={"max_box_area_km2": 3e6},
             note="max_box_area_km2=3e6"),
    Strategy("S1b_cap_1.5Mkm2", params={"max_box_area_km2": 1.5e6},
             note="max_box_area_km2=1.5e6"),
    Strategy("S2a_persist_2d", post=post_persist(persist_k(0.0, 2)),
             note="published box = bbox of cells valid on >=2 event days"),
    Strategy("S2b_persist_half", post=post_persist(persist_k(0.5)),
             note="published box = bbox of cells valid on >=50% of event days"),
    Strategy("S2c_persist_half_trim", post=post_persist(persist_k(0.5), 0.02),
             note="S2b plus 2-98% area-weighted quantile trim"),
    Strategy("S3_claim_overlap", tweaks={"claim_overlap": True},
             note="claim only valid cells within move_cells of the footprint"),
    Strategy("S3v_seed_valid", tweaks={"seed_valid_only": True},
             note="a seed box covers the region's valid cells, not the closed region"),
    Strategy("S3p_overlap_persist",
             tweaks={"claim_overlap": True},
             post=post_persist(persist_k(0.5)),
             note="S3 tracking + S2b persistence box"),
    Strategy("S4a_group_r3f40",
             params={"region_radius": 3, "region_min_fraction": 0.4},
             note="region_radius=3, region_min_fraction=0.40"),
    Strategy("S4b_group_r2f50",
             params={"region_radius": 2, "region_min_fraction": 0.5},
             note="region_radius=2, region_min_fraction=0.50"),
    Strategy("S5_merge_keep_box", tweaks={"merge_keeps_older_box": True},
             note="merges keep the older box, no whole-box union"),
    Strategy("S6_split_lowfill", post=post_split(0.5, 2e5),
             note="split spells below fill 0.5 into components >=200k km2"),
    Strategy("S7a_rolling5", tweaks={"rolling_days": 5},
             note="box = bbox of the last 5 days' footprints"),
    Strategy("S7b_rolling5_trailing", tweaks={"rolling_days": 5},
             post=post_persist_trailing(persist_k(0.5), 5),
             note="S7a tracking, published box = persistence of the last 5 days"),
    Strategy("C1_overlap_merge_persist",
             tweaks={"claim_overlap": True, "merge_keeps_older_box": True},
             post=post_persist(persist_k(0.5), 0.02),
             note="S3+S5 tracking, published box = S2c persistence"),
    Strategy("C2_overlap_merge_group_persist",
             params={"region_radius": 4, "region_min_fraction": 0.4},
             tweaks={"claim_overlap": True, "merge_keeps_older_box": True},
             post=post_persist(persist_k(0.5), 0.02),
             note="C1 plus region_radius=4, region_min_fraction=0.40"),
    Strategy("C3_valid_box_persist",
             tweaks={"claim_overlap": True, "seed_valid_only": True,
                     "merge_keeps_older_box": True},
             post=post_persist(persist_k(0.5), 0.02),
             note="S3+S3v+S5 tracking (footprint/box = valid cells only), "
                  "published box = S2c persistence"),
    Strategy("C4_valid_box_only",
             tweaks={"claim_overlap": True, "seed_valid_only": True,
                     "merge_keeps_older_box": True},
             note="C3 tracking with the tracker's own box (no persistence rule)"),
]


def identity_delta(rows: list[dict], base_rows: list[dict]) -> dict:
    """Lost / split counts when the strategy keeps the tracker's event identity."""
    by_parent: dict[str, int] = {}
    for r in rows:
        by_parent[r["parent"]] = by_parent.get(r["parent"], 0) + 1
    lost = [b["id"] for b in base_rows if not by_parent.get(b["id"])]
    split = sum(n - 1 for n in by_parent.values() if n > 1)
    return {"n": len(rows), "lost_vs_current": len(lost),
            "split_into_extra": split, "gained_vs_current": 0,
            "lost_ids": lost[:8]}


def geom_delta(w: Window, rows: list[dict], base_rows: list[dict]) -> dict:
    """Lost / gained counts when the tracker itself changed (ids churn)."""
    def as_evt(r: dict) -> dict:
        start = dt.date.fromisoformat(r["start"])
        end = dt.date.fromisoformat(r["end"]) + dt.timedelta(days=1)
        return {"hazard": w.hazard, "start": start, "end": end,
                "days": (end - start).days, "bbox": tuple(r["bbox"]),
                "peak": r["peak_km2"], "id": r["id"]}
    pairs, only_new, only_base = _evt02._match([as_evt(r) for r in rows],
                                               [as_evt(r) for r in base_rows])
    return {"n": len(rows), "matched": len(pairs),
            "lost_vs_current": len(only_base), "gained_vs_current": len(only_new),
            "lost_ids": [base_rows[j]["id"] for j in sorted(only_base)][:8],
            "gained_ids": [rows[i]["id"] for i in sorted(only_new)][:8]}


def paired_worst(rows: list[dict], base_rows: list[dict]) -> list[dict]:
    """Before/after of the three biggest baseline boxes."""
    out = []
    for b in sorted(base_rows, key=lambda r: -r["box_km2"])[:3]:
        cand = [r for r in rows if r["parent"] == b["parent"]]
        best = max(cand, key=lambda r: r["box_km2"], default=None)
        out.append({"id": b["id"], "start": b["start"], "end": b["end"],
                    "before_box_km2": round(b["box_km2"]),
                    "before_fill": round(b["fill"], 3),
                    "after_box_km2": round(best["box_km2"]) if best else None,
                    "after_fill": round(best["fill"], 3) if best else None,
                    "kept": best is not None})
    return out


def run_strategies(w: Window, base_spells: list[Spell], base_rows: list[dict],
                   params0: Params, only: set[str] | None = None,
                   ) -> dict[str, dict]:
    """Replay and score every strategy for one hazard."""
    out: dict[str, dict] = {}
    for st in STRATEGIES:
        if only and st.name not in only:
            continue
        t0 = time.perf_counter()
        params = replace(params0, **st.params)
        spells = (base_spells if st.name == "S0_current"
                  else replay(w, params, **st.tweaks))
        published = ([p for s in spells for p in st.post(w, s)] if st.post
                     else [Published(s, s.box) for s in spells])
        rows_pre = [score_one(w, Published(s, s.box)) for s in spells]
        rows = [score_one(w, p) for p in published]
        agg = aggregate(rows, time.perf_counter() - t0)
        agg.update({"note": st.note, "params": st.params,
                    "tweaks": {k: v for k, v in st.tweaks.items() if v}})
        if st.name != "S0_current":
            # event identity is compared on the TRACKER's own boxes (that is
            # what start dates and `ongoing` depend on); the post rule's own
            # effect is reported separately as splits / drops
            ident = not st.params and not st.tweaks
            agg.update(identity_delta(rows_pre, base_rows) if ident
                       else geom_delta(w, rows_pre, base_rows))
            if st.post is not None:
                agg["published_split_extra"] = identity_delta(
                    rows, rows_pre)["split_into_extra"]
                agg["published_dropped"] = identity_delta(
                    rows, rows_pre)["lost_vs_current"]
            agg["worst3_paired"] = paired_worst(rows, base_rows)
        out[st.name] = {"summary": agg, "rows": rows}
        print(f"  {w.hazard:<10} {st.name:<31} n={agg['n']:<4} "
              f"fill_med={agg.get('fill_med', 0):.2f} "
              f"box_med={agg.get('box_km2_med', 0) / 1e6:6.2f}M "
              f"box_p90={agg.get('box_km2_p90', 0) / 1e6:6.2f}M "
              f"box_max={agg.get('box_km2_max', 0) / 1e6:6.2f}M "
              f"lost={agg.get('lost_vs_current', '-'):<3} "
              f"gained={agg.get('gained_vs_current', '-'):<3} "
              f"split+={agg.get('published_split_extra', 0):<3} "
              f"({agg['runtime_s']}s)", flush=True)
    return out


# ------------------------------------------------------------ published state
def published_metrics(path: Path, w: Window) -> dict:
    """Fill of the live catalogue's boxes, measured on the lane's own masks."""
    rows = []
    for d in json.loads(path.read_text()):
        if d["event_type"] != w.hazard:
            continue
        st = dt.date.fromisoformat(d["start"][:10])
        en = dt.date.fromisoformat(d["end"][:10]) - dt.timedelta(days=1)
        if en < w.dates[0] or st > w.dates[-1]:
            continue
        i0 = max(0, next(i for i, x in enumerate(w.dates) if x >= st))
        i1 = next((i for i, x in enumerate(w.dates) if x >= en), len(w.dates) - 1)
        spell = Spell(w.hazard, d["id"], st, en + dt.timedelta(days=1), i0, i1,
                      box_from_bbox(w, tuple(d["bbox"])),
                      float(d["details"].get("peak_area_km2", 0.0)),
                      bool(d["details"].get("ongoing")))
        m = score_one(w, Published(spell, spell.box))
        m["days_clamped"] = st < w.dates[0] or en > w.dates[-1]
        m["merged_at_curation"] = bool(d["details"].get("merged_from"))
        rows.append(m)
    agg = aggregate(rows, 0.0)
    agg["n_merged_events"] = sum(1 for r in rows if r["merged_at_curation"])
    agg["n_clamped"] = sum(1 for r in rows if r["days_clamped"])
    return {"summary": agg, "rows": rows}


# ----------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="data/results/raw_exceedance")
    ap.add_argument("--published", default="")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="/opt/ewb-heatbox/out/hb01")
    ap.add_argument("--hazards", default=",".join(HAZARDS))
    ap.add_argument("--strategies", default="",
                    help="comma list of strategy names (default: all)")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()

    params0 = Params()
    out_prefix = Path(a.out)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in a.strategies.split(",") if s.strip()} or None
    summary: dict[str, Any] = {"window": [a.start, a.end],
                               "lane": str(a.lane), "hazards": {}}
    land: np.ndarray | None = None
    for hazard in [h.strip() for h in a.hazards.split(",") if h.strip()]:
        w = load_window(Path(a.lane), dt.date.fromisoformat(a.start),
                        dt.date.fromisoformat(a.end), hazard, land)
        land = w.land
        print(f"\n== {hazard}: {len(w.dates)} days {w.dates[0]}..{w.dates[-1]}, "
              f"grid {w.mask.shape[1:]} = {w.cell_area.sum() / 1e6:.1f}M km2, "
              f"land {w.cell_area[w.land].sum() / 1e6:.1f}M km2 "
              f"({100 * w.cell_area[w.land].sum() / w.cell_area.sum():.0f}%)",
              flush=True)
        if a.selfcheck and not selfcheck(w, params0):
            return 1
        t0 = time.perf_counter()
        base_spells = replay(w, params0)
        base_rows = [score_one(w, Published(s, s.box)) for s in base_spells]
        base_agg = aggregate(base_rows, time.perf_counter() - t0)
        base_agg["note"] = "as shipped"
        base_agg["worst3_paired"] = paired_worst(base_rows, base_rows)
        entry: dict[str, Any] = {"S0_current": {"summary": base_agg,
                                                "rows": base_rows}}
        entry["attribution"] = attribute(w, params0)
        entry["seam_audit"] = seam_audit(w, base_spells)
        entry.update(run_strategies(w, base_spells, base_rows, params0, only))
        if a.published and Path(a.published).exists():
            entry["P0_published_json"] = published_metrics(Path(a.published), w)
            p = entry["P0_published_json"]["summary"]
            print(f"  {hazard:<10} {'P0_published_json':<31} n={p['n']:<4} "
                  f"fill_med={p['fill_med']:.2f} "
                  f"box_med={p['box_km2_med'] / 1e6:6.2f}M "
                  f"box_max={p['box_km2_max'] / 1e6:6.2f}M", flush=True)
        path = out_prefix.with_name(f"{out_prefix.name}_{hazard}.json")
        path.write_text(json.dumps(entry, indent=1, default=str))
        summary["hazards"][hazard] = {
            "baseline": base_agg,
            "attribution": {k: entry["attribution"][k] for k in
                            ("growth_days", "n_events_with_growth",
                             "added_km2_total", "n_seeds", "seed_box_km2_total",
                             "cells_by_cat_km2", "attributed_km2",
                             "attributed_share", "wrapped_growths")},
            "per_event": entry["attribution"]["per_event"],
            "seam": entry["seam_audit"],
            "published": entry.get("P0_published_json", {}).get("summary"),
            "strategies": {k: v["summary"] for k, v in entry.items()
                           if isinstance(v, dict) and "summary" in v},
            "spells": [s.to_dict() for s in base_spells],
        }
        print(f"  wrote {path}", flush=True)
    summary_path = out_prefix.with_name(f"{out_prefix.name}_summary.json")
    summary_path.write_text(json.dumps(summary, indent=1, default=str))
    print(f"wrote {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
