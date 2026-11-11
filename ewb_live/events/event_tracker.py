"""Heat-wave / freeze event tracking (port of aaTman/ewb-events, 2026-11-10).

This is the product owner's methodology (github.com/aaTman/ewb-events,
`src/ewb_events/{geometry,tracker}.py`, ported 2026-11-10). It replaces both
the upstream EWB blob tracker and the interim 200k-seed/+250 km box rule of
2026-11-10 morning; the reasoning and the measured before/after are in
``notes/footprint_tracker.md``.

An event is a *footprint that keeps being re-claimed* plus a *core that keeps
growing*:

1. **Region.** Each day the valid points (cell passed all four 6-hourly steps,
   land only — see ``ewb_live/events/temperature.py``) are grouped into
   regions by morphological closing plus a local-density test, so a heat wave
   that is a scatter of patches counts as one area instead of dozens of
   fragments.
2. **Candidate.** Any region of >= ``seed_area_km2`` (50k km2) outside every
   open box starts a candidate, a quarter of the target area so the start day
   is caught while the heat is still small. It has ``max_ramp_days`` to grow.
3. **Follow.** Each open object re-claims the regions overlapping its last
   footprint grown by ``move_cells`` (2 deg/day drift), where the overlap is
   >= ``min_overlap`` of the *smaller* of region and footprint — so a large
   neighbour brushing the edge is not swallowed. Its box grows to cover every
   footprint it has ever had and never shrinks; objects that claim the same
   region merge into the older one.
4. **Qualify / end.** A candidate becomes an event the first day its footprint
   holds ``qualify_area_km2`` (200k km2); it is valid when it qualified and
   lasted >= ``min_days`` (3) days. An event ends on the first day its **core**
   holds less than ``end_area_km2`` (100k km2). The core is the valid area in
   its box made of cells valid *every day since the event began* — the heat
   that started the event. From ``anchor_day`` (3) the core is measured inside
   the box the event had on that day (its **anchor**), so an event ends when
   the persistent spell at its early location breaks, even if fresh heat keeps
   appearing elsewhere inside its grown box. That is what stops the permanently
   hot belts (Amazon, Sahara/Sahel, Indonesia) from running for a whole season.

Everything is day-by-day and never looks ahead, so it can run each morning on
the newest day. Longitude wraps (``circular_span``/``dilate``); latitude does
not. Boxes are grid-index boxes with inclusive bounds; ``j0 > j1`` means the
box crosses the longitude seam of the array it was built on (the antimeridian
on a -180..180 grid).
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy import ndimage as ndi

# per-axis ndimage modes: rows (latitude) do not wrap, columns (longitude) do
WRAP = ("nearest", "wrap")


def circular_span(occupied: np.ndarray) -> tuple[int, int]:
    """Shortest run of columns, wrapping at the seam, covering all occupied ones.

    Args:
        occupied: Boolean array over longitude columns; at least one True.

    Returns:
        ``(first, last)`` column index of the run; ``first > last`` when the run
        wraps the seam.
    """
    idx = np.flatnonzero(occupied)
    gaps = np.diff(np.r_[idx, idx[0] + occupied.size])
    k = int(np.argmax(gaps))
    if gaps[k] == 1:
        return 0, occupied.size - 1
    return int(idx[(k + 1) % idx.size]), int(idx[k])


@dataclass(frozen=True)
class Box:
    """A lat/lon box in grid indices, inclusive on all four edges."""

    i0: int
    i1: int
    j0: int
    j1: int

    @classmethod
    def from_mask(cls, mask: np.ndarray) -> "Box":
        """Return the smallest box covering every True cell of ``mask``."""
        rows = np.flatnonzero(mask.any(axis=1))
        j0, j1 = circular_span(mask.any(axis=0))
        return cls(int(rows[0]), int(rows[-1]), j0, j1)

    def columns(self, nx: int) -> np.ndarray:
        """Boolean length-``nx`` array marking the box's columns."""
        cols = np.zeros(nx, bool)
        if self.j0 <= self.j1:
            cols[self.j0: self.j1 + 1] = True
        else:
            cols[self.j0:] = True
            cols[: self.j1 + 1] = True
        return cols

    def mask(self, ny: int, nx: int) -> np.ndarray:
        """Return the box as a boolean ``(ny, nx)`` grid."""
        rows = np.zeros(ny, bool)
        rows[self.i0: self.i1 + 1] = True
        return rows[:, None] & self.columns(nx)[None, :]

    def union(self, other: "Box", nx: int) -> "Box":
        """Return the smallest box covering this box and ``other``."""
        j0, j1 = circular_span(self.columns(nx) | other.columns(nx))
        return Box(min(self.i0, other.i0), max(self.i1, other.i1), j0, j1)


def dilate(mask: np.ndarray, cells: int) -> np.ndarray:
    """Grow a mask by ``cells`` in every direction, wrapping in longitude."""
    return ndi.maximum_filter(mask.view(np.uint8), size=2 * cells + 1,
                              mode=WRAP).astype(bool)


def label_regions(valid: np.ndarray, radius: int, min_fraction: float) -> np.ndarray:
    """Group nearby patches of valid points into labelled regions.

    A cell joins a region when it lies inside the morphological closing of the
    valid mask (dilate then erode by ``radius``: fills gaps up to ~2*radius
    cells wide without growing the outer edge) **and** at least
    ``min_fraction`` of the ``(2*radius+1)^2`` window around it is valid (stops
    a thin scatter of lone points chaining across a continent). Valid cells
    always join. 8-connectivity, seam-aware.

    Args:
        valid: Boolean ``(lat, lon)`` grid.
        radius: Half-width of the grouping window in cells.
        min_fraction: Minimum share of valid cells in that window.

    Returns:
        Integer ``(lat, lon)`` label grid; 0 means no region.
    """
    size = 2 * radius + 1
    u8 = valid.view(np.uint8)
    closed = ndi.minimum_filter(ndi.maximum_filter(u8, size=size, mode=WRAP),
                                size=size, mode=WRAP).astype(bool)
    density = ndi.uniform_filter(valid.astype(np.float32), size=size, mode=WRAP)
    regions = (closed & (density >= min_fraction)) | valid
    labels, _ = ndi.label(regions, structure=np.ones((3, 3), bool))
    for a, b in set(zip(labels[:, 0].tolist(), labels[:, -1].tolist(), strict=True)):
        if a and b and a != b:
            labels[labels == b] = a
    return labels


@dataclass(frozen=True)
class Params:
    """Detection settings. Areas in km2, distances in grid cells (0.25 deg).

    Attributes:
        seed_area_km2: Smallest region that can start a candidate.
        qualify_area_km2: Footprint area that turns a candidate into an event.
        end_area_km2: The event ends the first day its core holds less than this.
        min_days: Fewest days a valid event lasts.
        max_ramp_days: Days a candidate has to qualify before it is dropped.
        region_radius: Half-width of the patch-grouping window in cells.
        region_min_fraction: Share of valid cells needed in that window.
        move_cells: How far a footprint may drift between days.
        min_overlap: A region continues an event only when its overlap with the
            event's grown footprint is at least this share of the *smaller* of
            region and (ungrown) footprint.
        join_area_km2: Smallest region that may join an open footprint.
        anchor_day: Event day (1 = first) whose box fixes where the core is
            counted from then on; None counts the whole growing box.
        max_box_area_km2: Largest box allowed: a bigger region cannot seed, and
            an event at the cap keeps its box and clips its footprint to it.
            None means no limit.
    """

    seed_area_km2: float = 5e4
    qualify_area_km2: float = 2e5
    end_area_km2: float = 1e5
    min_days: int = 3
    max_ramp_days: int = 5
    region_radius: int = 6
    region_min_fraction: float = 0.3
    move_cells: int = 8
    min_overlap: float = 0.5
    join_area_km2: float = 1e4
    anchor_day: int | None = 3
    max_box_area_km2: float | None = None


@dataclass
class TrackedEvent:
    """One candidate or event tracked by :class:`Tracker`.

    Attributes:
        id: Stable name, e.g. ``heat_wave-20260901-3``.
        hazard: ``heat_wave`` or ``freeze``.
        start: Day the candidate was seeded.
        box: Box covering every footprint so far; fixed once ended.
        qualified: First day the footprint reached ``qualify_area_km2``.
        end: First day the core held less than ``end_area_km2``; None while open.
        anchor: Box on ``anchor_day``; the core is measured inside it from then on.
        footprint_area_km2: Valid area in the footprint on each open day.
        box_area_km2: Valid area in the box on each open day.
        core_area_km2: Core area on each open day.
        footprint: Current footprint mask.
    """

    id: str
    hazard: str
    start: dt.date
    box: Box
    qualified: dt.date | None = None
    end: dt.date | None = None
    anchor: Box | None = None
    footprint_area_km2: list[float] = field(default_factory=list)
    box_area_km2: list[float] = field(default_factory=list)
    core_area_km2: list[float] = field(default_factory=list)
    footprint: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), bool),
                                  repr=False, compare=False)

    @property
    def status(self) -> Literal["candidate", "active", "ended"]:
        """Where the object is in its life."""
        if self.end is not None:
            return "ended"
        return "active" if self.qualified is not None else "candidate"

    @property
    def duration_days(self) -> int:
        """Number of days the object has been open."""
        return len(self.box_area_km2)

    @property
    def peak_area_km2(self) -> float:
        """Largest footprint area so far."""
        return max(self.footprint_area_km2, default=0.0)

    def is_valid(self, params: Params) -> bool:
        """True when it qualified and lasted at least ``params.min_days`` days."""
        return self.qualified is not None and self.duration_days >= params.min_days


class Tracker:
    """Run the detection one day at a time for one hazard."""

    def __init__(self, cell_area: np.ndarray, params: Params, hazard: str) -> None:
        """Args:
        cell_area: Area of each grid cell in km2, shape (lat, lon).
        params: Detection settings.
        hazard: Name stored on each event.
        """
        self.cell_area = cell_area
        self.params = params
        self.hazard = hazard
        self.active: list[TrackedEvent] = []
        self.count = 0
        self.streak = np.zeros(cell_area.shape, np.int32)

    def fits(self, box: Box) -> bool:
        """True when the box's total grid area is within ``max_box_area_km2``."""
        cap = self.params.max_box_area_km2
        return cap is None or float(
            self.cell_area[box.mask(*self.cell_area.shape)].sum()) <= cap

    def step(self, day: dt.date, valid: np.ndarray) -> list[TrackedEvent]:
        """Process one UTC day.

        Args:
            day: The day.
            valid: Boolean ``(lat, lon)`` grid of that day's valid points.

        Returns:
            Qualified events that ended on this day.
        """
        p = self.params
        ny, nx = valid.shape
        weighted = valid * self.cell_area
        self.streak = np.where(valid, self.streak + 1, 0)
        labels = label_regions(valid, p.region_radius, p.region_min_fraction)
        region_area = ndi.sum(weighted, labels, np.arange(labels.max() + 1))
        region_area[0] = 0.0

        hits = [self.follow(ev, labels, region_area) for ev in self.active]
        for k, ev in enumerate(self.active):
            if ev.qualified is not None and self.ending(ev, hits[k], labels, weighted, day):
                hits[k] = set()  # release its regions so they cannot block a neighbour
        groups = merge_groups(hits, [ev.box for ev in self.active], self.fits, nx)
        groups.sort(key=lambda g: (self.active[g[0]].qualified is None, g[0]))
        taken = np.zeros((ny, nx), bool)
        kept: list[TrackedEvent] = []
        ended: list[TrackedEvent] = []
        for group in groups:
            ev = self.active[group[0]]
            group_hits = set().union(*(hits[k] for k in group))
            for k in group[1:]:
                other = self.active[k]
                ev.box = ev.box.union(other.box, nx)
                if other.qualified is not None and (
                        ev.qualified is None or other.qualified < ev.qualified):
                    ev.qualified = other.qualified
            share = np.isin(labels, list(group_hits)) & ~taken if group_hits \
                else np.zeros((ny, nx), bool)
            if share.any():
                grown = ev.box.union(Box.from_mask(share), nx)
                if self.fits(grown):
                    ev.box = grown
                else:
                    share &= ev.box.mask(ny, nx)
            if share.any():
                ev.footprint = share
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
        free_area = ndi.sum(weighted * ~taken, labels, np.arange(labels.max() + 1))
        free_area[0] = 0.0
        for label in np.flatnonzero(free_area >= p.seed_area_km2):
            region = (labels == label) & ~taken
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

    def ending(self, ev: TrackedEvent, hits: set[int], labels: np.ndarray,
               weighted: np.ndarray, day: dt.date) -> bool:
        """True when the event ends today even after growing its box to today's footprint."""
        box = ev.anchor or ev.box
        if hits and ev.anchor is None:
            grown = box.union(Box.from_mask(np.isin(labels, list(hits))), labels.shape[1])
            if self.fits(grown):
                box = grown
        return self.core_area(ev, box, day, weighted) < self.params.end_area_km2

    def core_area(self, ev: TrackedEvent, box: Box, day: dt.date,
                  weighted: np.ndarray) -> float:
        """Valid area in ``box`` from cells valid every day since the event began."""
        age = (day - ev.start).days + 1
        return float(weighted[box.mask(*weighted.shape) & (self.streak >= age)].sum())

    def follow(self, ev: TrackedEvent, labels: np.ndarray,
               region_area: np.ndarray) -> set[int]:
        """Labels of today's regions that continue an open object.

        A region continues it when the region is at least ``join_area_km2`` and
        overlaps the event's footprint grown by ``move_cells`` by at least
        ``min_overlap`` of the smaller of region and footprint.
        """
        p = self.params
        grown = dilate(ev.footprint, p.move_cells)
        near = np.unique(labels[grown])
        near = near[(near > 0) & (region_area[near] >= p.join_area_km2)]
        if near.size == 0:
            return set()
        overlap = ndi.sum(self.cell_area * grown, labels, near)
        size = ndi.sum(self.cell_area, labels, near)
        smaller = np.minimum(size, float(self.cell_area[ev.footprint].sum()))
        return {int(k) for k in near[overlap >= p.min_overlap * smaller]}


def merge_groups(hits: list[set[int]], boxes: list[Box],
                 fits: Callable[[Box], bool], nx: int) -> list[list[int]]:
    """Group events claiming a shared region, unless their joined box is too big.

    Args:
        hits: Per event, oldest first, the region labels it claims.
        boxes: Each event's current box.
        fits: True when a box is small enough.
        nx: Number of grid columns.

    Returns:
        Groups of event indices; each group is sorted, so index 0 is its oldest.
    """
    parent = list(range(len(hits)))
    box = list(boxes)

    def root(k: int) -> int:
        while parent[k] != k:
            k = parent[k]
        return k

    owner: dict[int, int] = {}
    for k, labels in enumerate(hits):
        for label in labels:
            if label not in owner:
                owner[label] = k
                continue
            a, b = sorted((root(owner[label]), root(k)))
            if a == b:
                continue
            joined = box[a].union(box[b], nx)
            if fits(joined):
                parent[b] = a
                box[a] = joined
    groups: dict[int, list[int]] = {}
    for k in range(len(hits)):
        groups.setdefault(root(k), []).append(k)
    return list(groups.values())


def track_events(daily_mask: np.ndarray, dates: Sequence[dt.date],
                 cell_area: np.ndarray, params: Params, hazard: str,
                 ) -> list[TrackedEvent]:
    """Run the tracker over a window of daily masks.

    Args:
        daily_mask: Boolean ``(days, lat, lon)``; True = the cell passed the
            daily rule (all four 6-hourly steps over land).
        dates: One date per day of ``daily_mask`` (complete days only).
        cell_area: ``(lat, lon)`` grid-cell areas in km2.
        params: Detection settings.
        hazard: ``heat_wave`` or ``freeze``.

    Returns:
        Valid events ordered by start day. Events still open on the last day
        are included when already valid, with ``end is None``.
    """
    tracker = Tracker(np.asarray(cell_area, float), params, hazard)
    done: list[TrackedEvent] = []
    for day, frame in zip(dates, np.asarray(daily_mask, bool), strict=True):
        done += tracker.step(day, frame)
    events = [ev for ev in done + tracker.active if ev.is_valid(params)]
    return sorted(events, key=lambda e: (e.start, e.id))
