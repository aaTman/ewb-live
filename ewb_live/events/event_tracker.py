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
   region merge into the merge's **oldest qualified member** (only a group of
   pure candidates keeps its oldest candidate; review round 3 #1) — a merge
   never hands qualification to a spell that never reached the floor on its
   own footprint.
   Since the 2026-11-20 box-size rule (SPEC §2, ``notes/heatwave_box_diagnosis.md``)
   the claimed part of a region is only its **valid** cells within
   ``move_cells`` of what the event holds (``claim_valid_only``), a candidate
   seeds as the region's valid cells (``seed_valid_only``), and a merge keeps
   the older object's box instead of unioning the two (``merge_keep_box``).
4. **Qualify / end.** A candidate becomes an event the first day its footprint
   holds ``qualify_area_km2`` (200k km2); it is valid when it qualified and
   lasted >= ``min_days`` (3) days. An event ends on the first day its **core**
   holds less than ``end_area_km2`` (100k km2). The core is the valid area in
   its box made of cells valid *every day since the event qualified* — the heat
   that made it an event (owner decision 2026-11-21, ``Params.core_clock``;
   ``core_clock="start"`` keeps the pre-change count from the start day). The
   core clock therefore starts at qualification, so the qualifying day can
   never also be the ending day. From the later of ``anchor_day`` (3) and the
   qualification day the core is measured inside the box the event had on that
   day (its **anchor**), so an event ends when the persistent spell at its
   early location breaks, even if fresh heat keeps appearing elsewhere inside
   its grown box. That is what stops the permanently hot belts (Amazon,
   Sahara/Sahel, Indonesia) from running for a whole season.

The tracker box is the *tracking* geometry. What a scan publishes as the event
rectangle (``ewb_live/events/temperature.py``) is the **persistence box**
(:func:`persistence_box`, ``publish_box="persistence"``): the bbox of the cells
valid on at least half the event's days, trimmed to their 2-98 % area-weighted
range.

Everything is day-by-day and never looks ahead, so it can run each morning on
the newest day. Longitude wraps (``circular_span``/``dilate``); latitude does
not. Boxes are grid-index boxes with inclusive bounds; ``j0 > j1`` means the
box crosses the longitude seam of the array it was built on (the antimeridian
on a -180..180 grid).
"""
from __future__ import annotations

import datetime as dt
import math
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


def _measure_arc(occupied_cols: np.ndarray, box: Box, nx: int) -> np.ndarray:
    """Column indices to measure a persistence box on, in eastward order.

    The arc must fit inside ``box`` - the shortest seam-wrapping arc over a
    subset of a very wide box's columns can leave the box and run the other
    way round the globe (round-4 fuzz: 455 of 20 000 cases), and the
    published rectangle may never exceed the tracker box it reports fill for
    (SPEC §2). Only when the box itself is the whole ring is the short arc
    over the event's own columns the right measure (a seam-crossing spell
    then legitimately wraps); otherwise the arc is the box's own arc (wrapped
    box) or the plain min-max span (unwrapped box).

    Args:
        occupied_cols: Boolean length-``nx`` array, the cells to cover.
        box: The tracker box (the search area, all its own cells count).
        nx: Number of grid columns.
    """
    if box.j0 <= box.j1 and not box.columns(nx).all():
        idx = np.flatnonzero(occupied_cols)
        return np.arange(int(idx[0]), int(idx[-1]) + 1)
    if box.j0 > box.j1 and (box.j1 - box.j0) % nx + 1 < nx:
        return np.concatenate([np.arange(box.j0, nx), np.arange(0, box.j1 + 1)])
    j0, j1 = circular_span(occupied_cols)
    return (j0 + np.arange((j1 - j0) % nx + 1)) % nx


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Area-weighted ``q``-quantile of ascending ``values``."""
    w = np.cumsum(weights)
    if not w.size or w[-1] <= 0:
        return float(values[0]) if values.size else 0.0
    return float(values[int(np.searchsorted(w / w[-1], q))])


def persistence_k_days(duration_days: int, min_fraction: float) -> int:
    """Persistence day-count: ``max(2, ceil(min_fraction x days))``."""
    return max(2, math.ceil(min_fraction * duration_days))


def persistence_box(daily_mask: np.ndarray, box: Box, cell_area: np.ndarray,
                    k_days: int, trim: float | None = None) -> Box | None:
    """The published rectangle of an event: bbox of its persistent cells.

    Of the cells inside the tracker ``box``, keep those valid on at least
    ``k_days`` of the given days, optionally trim to the area-weighted
    ``[trim, 1-trim]`` lat/lon range of that mask (a one-cell outlier column
    no longer stretches the published box), and return the bbox. If no cell
    reaches ``k_days`` the bbox of every ever-valid cell is used instead;
    ``None`` when the event held no valid cell at all inside ``box``. Only
    ``box``'s own cells count - other spells' cells are not this event's
    event (SPEC §2, published box) - and the result never leaves ``box``,
    columns measured on the box's own longitude arc.

    Args:
        daily_mask: Boolean ``(days, lat, lon)`` mask of the event's days only.
        box: The event's tracker box (the search area).
        cell_area: ``(lat, lon)`` cell areas in km2 (broadcastable).
        k_days: Persistence threshold in days (see :func:`persistence_k_days`).
        trim: Area-weighted quantile to trim each edge by; None to skip.

    Returns:
        The persistence box, or None when the event has no valid cell.
    """
    ny, nx = daily_mask.shape[1:]
    boxmask = box.mask(ny, nx)
    ever = daily_mask.any(axis=0) & boxmask
    if not ever.any():
        return None
    keep = (daily_mask.sum(axis=0) >= max(1, k_days)) & boxmask
    if not keep.any():
        keep = ever
    cols = _measure_arc(keep.any(axis=0), box, nx)
    sub = keep[:, cols]
    area = cell_area[:, cols]
    i_lo = c_lo = 0
    if trim is not None:
        row_idx = np.arange(ny)
        row_w = (sub * area).sum(axis=1)
        col_idx = np.arange(cols.size)
        col_w = (sub * area).sum(axis=0)
        i0 = int(np.floor(_weighted_quantile(row_idx, row_w, trim)))
        i1 = int(np.ceil(_weighted_quantile(row_idx, row_w, 1.0 - trim)))
        if cols.size < nx:
            c0 = int(np.floor(_weighted_quantile(col_idx, col_w, trim)))
            c1 = int(np.ceil(_weighted_quantile(col_idx, col_w, 1.0 - trim)))
        else:            # a full ring has no seam-free edge to trim from
            c0, c1 = 0, cols.size - 1
        sub = sub[max(0, i0):i1 + 1, max(0, c0):c1 + 1]
        i_lo = max(0, i0)
        c_lo = max(0, c0)
        if not sub.any():        # the trim cut through every row or column
            cols = _measure_arc(ever.any(axis=0), box, nx)
            sub = ever[:, cols]
            i_lo = c_lo = 0
    rr = np.flatnonzero(sub.any(axis=1))
    cc = np.flatnonzero(sub.any(axis=0))
    if rr.size == 0 or cc.size == 0:
        return None
    return Box(int(i_lo + rr[0]), int(i_lo + rr[-1]),
               int(cols[c_lo + cc[0]]), int(cols[c_lo + cc[-1]]))


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
        core_clock: Which day the core's every-day-since streak is counted
            from: ``"qualified"`` (default, owner 2026-11-21) counts cells
            valid every day since the event *qualified*, so a spell that grows
            into qualification is never ended by its own qualifying day; the
            anchor freezes no earlier than that day either. ``"start"`` is the
            pre-2026-11-21 rule (count from the candidate's start day) and
            reproduces it exactly.
        anchor_day: Event day (1 = first) whose box fixes where the core is
            counted from then on; None counts the whole growing box. Under
            ``core_clock="qualified"`` the anchor freezes on the later of this
            day and the qualification day, never before qualification.
        max_box_area_km2: Largest box allowed: a bigger region cannot seed, and
            an event at the cap keeps its box and clips its footprint to it.
            None means no limit.
        claim_valid_only: A claimed region continues the event only for its
            *valid* cells within ``move_cells`` of the cells the event already
            holds; the rest of the region stays free and may seed its own
            candidate (2026-11-20 box-size rule, SPEC §2 step 4). False keeps
            the original whole-region claim.
        seed_valid_only: A new candidate's footprint and first box are the
            labelled region's valid cells only, never its closing-filled
            bridge cells. False keeps the original whole-region seeding.
        merge_keep_box: When objects claim the same cells the survivor (see
            ``merge_survivor``) keeps its own box and every other member's
            claim is clipped into it; the survivor keeps growing its box by
            its own claims, and qualification is never inherited. False keeps
            the original oldest-survivor box-union.
        merge_survivor: Which member of a merge group carries the group on,
            only meaningful with ``merge_keep_box=True`` (SPEC §2 step 4;
            the round-4 candidates, measured in ``notes/footprint_tracker.md``):
            ``"oldest_qualified"`` is the group's oldest qualified member, or
            its oldest candidate when nothing qualified (the pre-round-4 rule,
            pinned by the flood/legacy lanes);
            ``"largest"`` the qualified member holding the largest footprint
            that day (ties: oldest); ``"largest_close"`` as ``"largest"``, but
            a qualified member already ``min_days`` old is closed and published
            (``end`` = merge day) instead of vanishing unpublished (the shipped
            rule, owner 2026-11-21); ``"separate"`` never merges qualified
            events - each keeps its own claim, contested cells go to the larger
            event (ties: oldest), and only unqualified candidates are absorbed.
            Ignored when ``merge_keep_box=False``
            (legacy: always the oldest member).
        publish_box: The rectangle a scan publishes for the event:
            ``"persistence"`` = :func:`persistence_box` over the event's days,
            ``"tracker"`` = the cumulative tracker box.
        publish_min_fraction: The persistence box keeps the cells valid on at
            least ``max(2, ceil(fraction x duration_days))`` event days
            (:func:`persistence_k_days`).
        publish_trim: Area-weighted lat/lon quantile each persistence-box edge
            is trimmed by (0.02 keeps the 2-98 % range); None disables it.
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
    core_clock: Literal["qualified", "start"] = "qualified"
    anchor_day: int | None = 3
    max_box_area_km2: float | None = None
    claim_valid_only: bool = True
    seed_valid_only: bool = True
    merge_keep_box: bool = True
    merge_survivor: Literal["oldest_qualified", "largest", "largest_close",
                            "separate"] = "largest_close"
    publish_box: Literal["persistence", "tracker"] = "persistence"
    publish_min_fraction: float = 0.5
    publish_trim: float | None = 0.02


@dataclass
class TrackedEvent:
    """One candidate or event tracked by :class:`Tracker`.

    Attributes:
        id: Stable name, e.g. ``heat_wave-20260901-3``.
        hazard: ``heat_wave`` or ``freeze``.
        start: Day the candidate was seeded.
        box: Box covering every footprint so far; fixed once ended.
        qualified: First day the footprint reached ``qualify_area_km2``.
        core_clock_start: Day the core's every-day-since streak counts from
            (:meth:`Tracker.core_clock_day`): the day this object's OWN
            footprint qualified - a merge never moves it (SPEC §2 step 4).
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
    core_clock_start: dt.date | None = None
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
            if ev.qualified is not None and self.ending(ev, hits[k], labels, valid, weighted, day):
                hits[k] = set()  # release its regions so they cannot block a neighbour
        groups = merge_groups(hits, [ev.box for ev in self.active], self.fits, nx)
        # Each group is carried on by one or more SURVIVOR units (see
        # _group_units); every other member is absorbed into its unit's
        # survivor. A merge never inherits a qualification onto an unqualified
        # candidate, which would clip the qualified member's cells into the
        # candidate's small box and publish a sub-floor "event" (review round
        # 3 #1). Only a group with no qualified member keeps its oldest
        # candidate. The legacy rule keeps the oldest member (SPEC §2 step 4).
        units = [self._group_units(g, hits, labels, valid, weighted)
                 for g in groups]
        order = sorted(range(len(groups)),
                       key=lambda g: (self.active[units[g][0][0]].qualified is None,
                                      groups[g][0]))
        taken = np.zeros((ny, nx), bool)
        kept: list[TrackedEvent] = []
        ended: list[TrackedEvent] = []
        for gi in order:
            for sv, absorbed in units[gi]:
                ev = self.active[sv]
                # the survivor keeps its own claim of its own regions ...
                share = self.claim(ev, hits[sv], labels, valid) & ~taken
                for k in absorbed:
                    other = self.active[k]
                    if p.merge_keep_box:
                        if (p.merge_survivor == "largest_close"
                                and other.qualified is not None
                                and other.duration_days >= p.min_days):
                            # close and PUBLISH an aged qualified member rather
                            # than letting its history vanish unpublished; its
                            # claim still goes to the survivor (end is
                            # exclusive - the merge day is the survivor's)
                            other.end = day
                            ended.append(other)
                        # ... a merge only clips the OTHER member's claim into
                        # the survivor's box; the survivor's own drift cells
                        # stay free to grow it (SPEC §2 step 4, review round
                        # 1 #2). An absorbed unqualified candidate disappears;
                        # qualification is never inherited - a qualified day
                        # is a day the survivor's OWN footprint reached the
                        # floor (round 3 #1).
                        share |= (self.claim(other, hits[k], labels, valid)
                                  & ev.box.mask(ny, nx) & ~taken)
                    else:
                        if other.qualified is not None and (
                                ev.qualified is None or other.qualified < ev.qualified):
                            # legacy: the survivor publishes the earlier
                            # qualification (its core clock keeps its own start;
                            # under the pinned start clock nothing reads it)
                            ev.qualified = other.qualified
                        ev.box = ev.box.union(other.box, nx)
                        share |= self.claim(other, hits[k], labels, valid) & ~taken
                if self._advance(ev, share, day, weighted, taken, ended):
                    kept.append(ev)

        occupied = np.zeros((ny, nx), bool)
        for ev in kept:
            occupied |= ev.box.mask(ny, nx)
        free_area = ndi.sum(weighted * ~taken, labels, np.arange(labels.max() + 1))
        free_area[0] = 0.0
        for label in np.flatnonzero(free_area >= p.seed_area_km2):
            region = (labels == label) & ~taken
            if p.seed_valid_only:
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
                ev.core_clock_start = day
            ev.footprint_area_km2.append(area)
            ev.box_area_km2.append(float(weighted[ev.box.mask(ny, nx)].sum()))
            ev.core_area_km2.append(ev.box_area_km2[-1])
            if (p.anchor_day is not None and p.anchor_day <= 1
                    and self.anchor_released(ev)):
                ev.anchor = box
            occupied |= ev.box.mask(ny, nx)
            kept.append(ev)
        self.active = sorted(kept, key=lambda e: (e.start, e.id))
        return ended

    def _group_units(self, group: list[int], hits: list[set[int]],
                     labels: np.ndarray, valid: np.ndarray,
                     weighted: np.ndarray) -> list[tuple[int, list[int]]]:
        """Split a merge group into ``(survivor index, absorbed indices)`` units.

        ``Params.merge_survivor`` picks the rule (SPEC §2 step 4). The legacy
        path and ``"oldest_qualified"`` give a single unit; ``"largest"`` and
        ``"largest_close"`` move the survivor to the qualified member holding
        the largest claim today (ties: oldest); ``"separate"`` keeps every
        qualified member as its own unit - processing them largest-first over
        the shared ``taken`` mask is what hands contested cells to the larger
        event - and absorbs the unqualified candidates into the largest one.
        """
        p = self.params
        if not p.merge_keep_box:
            sv = group[0]
            return [(sv, [k for k in group if k != sv])]
        qualified = [k for k in group if self.active[k].qualified is not None]
        if not qualified:
            sv = group[0]
            return [(sv, [k for k in group if k != sv])]
        if p.merge_survivor == "oldest_qualified":
            sv = qualified[0]
            return [(sv, [k for k in group if k != sv])]
        area = {k: float(weighted[self.claim(self.active[k], hits[k], labels,
                                             valid)].sum())
                for k in qualified}
        # qualified is oldest-first (groups and self.active are sorted), so a
        # stable sort by descending area breaks ties toward the oldest event
        by_area = sorted(qualified, key=lambda k: -area[k])
        if p.merge_survivor != "separate":
            sv = by_area[0]
            return [(sv, [k for k in group if k != sv])]
        units: list[list[int]] = [[k, []] for k in by_area]
        for k in group:
            if self.active[k].qualified is None:
                units[0][1].append(k)
        return [(int(u[0]), [int(k) for k in u[1]]) for u in units]

    def _advance(self, ev: TrackedEvent, share: np.ndarray, day: dt.date,
                 weighted: np.ndarray, taken: np.ndarray,
                 ended: list[TrackedEvent]) -> bool:
        """Grow, qualify, anchor and end-check one open object on today's claim.

        Returns True when the object stays open (its day is recorded and its
        cells enter ``taken``); on False it ended today (``ended``) or was
        dropped (ramp-up failed). :meth:`step` calls this once per survivor
        unit, so a ``"separate"`` merge advances several events per day.
        """
        p = self.params
        ny, nx = weighted.shape
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
                ev.core_clock_start = day
            elif not share.any() or (day - ev.start).days >= p.max_ramp_days:
                return False
        if (p.anchor_day is not None and ev.anchor is None
                and (day - ev.start).days + 1 >= p.anchor_day
                and self.anchor_released(ev)):
            ev.anchor = ev.box
        core_area = self.core_area(ev, ev.anchor or ev.box, day, weighted)
        if ev.qualified is not None and core_area < p.end_area_km2:
            ev.end = day
            ended.append(ev)
            return False
        taken |= share
        ev.footprint_area_km2.append(footprint_area)
        ev.box_area_km2.append(box_area)
        ev.core_area_km2.append(core_area)
        return True

    def claim(self, ev: TrackedEvent, hits: set[int], labels: np.ndarray,
              valid: np.ndarray) -> np.ndarray:
        """The cells of today's regions that continue ``ev`` (the claim rule).

        Under ``claim_valid_only`` only the **valid** cells within
        ``move_cells`` of what the event already holds; otherwise the whole
        claimed regions (SPEC §2 step 4). :meth:`step` and :meth:`ending`
        must grow the box over exactly these cells, never more.
        """
        share = np.isin(labels, list(hits)) if hits else np.zeros(labels.shape, bool)
        if self.params.claim_valid_only and ev.footprint.any():
            share &= valid & dilate(ev.footprint, self.params.move_cells)
        return share

    def ending(self, ev: TrackedEvent, hits: set[int], labels: np.ndarray,
               valid: np.ndarray, weighted: np.ndarray, day: dt.date) -> bool:
        """True when the event ends today even after growing its box to today's footprint."""
        box = ev.anchor or ev.box
        if hits and ev.anchor is None:
            # grow exactly as far as step's claim rule will, no further
            share = self.claim(ev, hits, labels, valid)
            if share.any():
                grown = box.union(Box.from_mask(share), labels.shape[1])
                if self.fits(grown):
                    box = grown
        return self.core_area(ev, box, day, weighted) < self.params.end_area_km2

    def core_clock_day(self, ev: TrackedEvent) -> dt.date:
        """The day the core counts its every-day-since streak from."""
        if self.params.core_clock == "start":
            return ev.start
        return ev.core_clock_start or ev.qualified or ev.start

    def anchor_released(self, ev: TrackedEvent) -> bool:
        """True when the anchor may freeze: under ``core_clock="qualified"``
        never before the core clock starts, i.e. never before qualification."""
        return (self.params.core_clock == "start"
                or ev.core_clock_start is not None)

    def core_area(self, ev: TrackedEvent, box: Box, day: dt.date,
                  weighted: np.ndarray) -> float:
        """Valid area in ``box`` from cells valid every day since the core clock.

        The clock starts at the day the event qualified (SPEC §2 step 4, owner
        2026-11-21) or at its start day under ``core_clock="start"``, so an
        event always holds at least one core cell on its qualifying day.
        """
        age = (day - self.core_clock_day(ev)).days + 1
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
