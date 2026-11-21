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
* re-scores the tightening strategies S1..S7 (production ``Params`` knob sets
  plus a rolling-box ``Tracker`` subclass that lives only in this file —
  nothing in ``ewb_live/`` is changed) on identical inputs so they can be
  compared on one table.``--selfcheck``/``--prodcheck`` prove the legacy and
  C3 rows run the same ``Tracker`` code production runs, and every hazard also
reports ``prod_match``: the legacy replay paired against the PRODUCTION raw
catalogue (start/end exactness, pairing, and the mask-source split that
explains the residue - the lane and ARCO paths are not the same bytes).

The daily mask is the tracker's own input: the published ``raw_exceedance``
frames ANDed over the four 00/06/12/18Z steps, land only (SPEC §2 "Mask
source"), so "a valid heat-wave point" here is exactly a cell the detector
counted. **Freeze gets the production rule, not the raw lane bits** (review
round 2): the lane's p15 layer has no <=0 degC ceiling, so "freeze" measured on
the lane is really the cold-snap population. For ``freeze`` this script applies
production's cap the same way ``scan_temperature_events`` does
(``mask & (t2m_dmax <= temperature.MAX_C_BY_TYPE["freeze"])``, t2m_dmax from the
ERA5 truth mirror, read-only); ``cold_snap`` is the same lane bits uncapped, so
the two rows are finally different populations (SPEC §2).

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
import dataclasses
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

import numpy as np
from scipy import ndimage as ndi

from ewb_live.events import frame_masks
from ewb_live.events.event_tracker import (
    Box,
    Params,
    TrackedEvent,
    Tracker,
    circular_span,
    dilate,
    label_regions,
)
from ewb_live.events.temperature import _cell_area_km2
from ewb_live.events.validate import MAX_DURATION_DAYS as MAX_PUBLISH_DAYS

# the greedy bbox/day matcher from the tracker-port review, reused verbatim
_evt02 = importlib.import_module("evt02_tracker_port_compare")

HAZARDS = ("heat_wave", "freeze", "cold_snap")
# the production scan's latitude window (scan_temperature_events defaults):
# the replay must track the SAME population the product publishes, no
# Antarctica (round-3 review item 2)
LAT_MIN, LAT_MAX = -60.0, 90.0
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
    """Daily masks straight out of the published frames lane (read-only).

    cold_snap (review #7) tracks the p15 freeze layer without the <=0C cap
    (temperature.MAX_C_BY_TYPE[cold_snap] is None), so its replay input is
    the freeze lane bits themselves - exactly the MASK_ALIAS the scan uses.

    ``freeze`` is NOT (review round 2): those lane bits are the p15 spell
    population (= published freeze + published cold_snap together), so the
    production ceiling is applied here with the production rule and the
    production temperature source - ``temperature._mirror_t2m_daily(...,
    "max")`` over the ERA5 truth mirror, opened read-only - exactly as
    ``scan_temperature_events`` does. Without it every freeze number in this
    script would be a cold-snap number wearing a freeze label.

    Round-3 fix: the tracker input is clamped to ``lat >= -60`` exactly like
    ``scan_temperature_events`` (``lat_min=-60``). Without the clamp the
    replay tracked Antarctica - a different event population from anything
    the product publishes, and every published count it was compared to.
    """
    dates, lats, lons, masks = frame_masks.daily_masks(lane)
    sel = [i for i, d in enumerate(dates) if start <= d <= end]
    if not sel:
        raise SystemExit(f"lane {lane} has no complete days in {start}..{end}")
    dates = [dates[i] for i in sel]
    step = float(abs(lons[1] - lons[0]))
    keep = (lats >= LAT_MIN) & (lats <= LAT_MAX)   # production scan clamp
    lats = lats[keep]
    mask = masks[frame_masks.MASK_ALIAS.get(hazard, hazard)][sel][:, keep]
    if land is None:
        from ewb_live.viz.build import land_mask

        land = land_mask(lats, lons)
    from ewb_live.events import temperature as T

    max_c = T.MAX_C_BY_TYPE.get(hazard)
    if max_c is not None:
        dmax = T._mirror_t2m_daily(dates, lats, lons, "max")
        if dmax is None:
            raise SystemExit(
                f"{hazard} needs the ERA5 truth mirror for the <= {max_c:g} degC "
                "cap ($EWB_LIVE_CACHE/truth/era5.zarr); without it the replay "
                "would measure the cold-snap population and call it freeze")
        capped = mask & (dmax <= max_c)
        print(f"  {hazard} cap: t2m daily max <= {max_c:g} degC from the ERA5 "
              f"truth mirror; p15 cells {int(mask.sum())} -> "
              f"{int(capped.sum())} "
              f"({100 * capped.sum() / max(1, int(mask.sum())):.0f} %)", flush=True)
        mask = capped
    return Window(hazard, dates, lats, lons, mask,
                  _cell_area_km2(lats, step, lons.size), land)


# ------------------------------------------------------------------- objects
@dataclass
class Spell:
    """One tracked spell, with the box and day range the tracker gave it."""

    hazard: str
    id: str
    start: dt.date
    end: dt.date | None       # exclusive; None = still open at the window end
    qualified: dt.date | None  # the day the footprint first reached qualify area
    i0: int                   # first event day, index into Window.dates
    i1: int                   # last event day, inclusive
    box: Box
    peak_km2: float
    ongoing: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "hazard": self.hazard, "start": str(self.start),
                "end": str(self.end) if self.end else None,
                "qualified": str(self.qualified) if self.qualified else None,
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


# ------------------------------------------------------- tuned tracker (S7)
class TunedTracker(Tracker):
    """``Tracker`` with the prototype flags expressed as production Params.

    The three box rules (claim/seed/merge) are production ``Params`` knobs
    since the 2026-11-20 C3 rule, so the tuned tracker maps its prototype
    flags onto them and inherits ``Tracker.step`` unchanged - the table rows
    and production then cannot drift apart (``--selfcheck`` proves a flags-off
    run equals the shipped legacy rule, ``--prodcheck`` that production
    defaults equal the C3 row, merge fix included).

    Args:
        claim_overlap: production ``claim_valid_only`` (S3).
        seed_valid_only: production ``seed_valid_only`` (S3v).
        merge_keeps_older_box: production ``merge_keep_box`` (S5; since the
            round-1 fix the survivor keeps growing by its OWN claim and only
            the younger members' cells are clipped into the older box).
        rolling_days: re-derive the reported box each day from the
            footprints of the last ``rolling_days`` days so it may shrink
            (S7); None = the cumulative box. Applied as a post-step hook:
            the day's claim, anchor and core checks run on the stock
            cumulative box (the pre-anchor rolling the first S7 pass ran
            inside the step could not be reused once the rules moved into
            production ``Tracker.step``; it only ever differed for events
            under ``anchor_day`` days old).
        core_clock: production ``Params.core_clock``. Pinned to ``"start"``
            (the shipped rule) so the S0/selfcheck baselines stay the shipped
            rule after the owner's 2026-11-21 decision made ``"qualified"`` the
            production default; ``--prodcheck`` sets it to ``"qualified"`` to
            prove the C3+qualify row is what production now runs.
    """

    def __init__(self, cell_area: np.ndarray, params: Params, hazard: str,
                 claim_overlap: bool = False, seed_valid_only: bool = False,
                 merge_keeps_older_box: bool = False,
                 rolling_days: int | None = None,
                 core_clock: str = "start") -> None:
        super().__init__(cell_area, dataclasses.replace(
            params, claim_valid_only=claim_overlap,
            seed_valid_only=seed_valid_only,
            merge_keep_box=merge_keeps_older_box,
            core_clock=core_clock), hazard)
        self.rolling_days = rolling_days
        self.box_hist: dict[str, list[Box]] = {}

    def step(self, day: dt.date, valid: np.ndarray) -> list[TrackedEvent]:
        ended = super().step(day, valid)
        if self.rolling_days:
            for ev in self.active:
                ev.box = self._rolling_box(ev, nx=valid.shape[1])
        return ended

    def _rolling_box(self, ev: TrackedEvent, nx: int) -> Box:
        """Bbox of the footprints of the last ``rolling_days`` days."""
        hist = self.box_hist.setdefault(ev.id, [])
        hist.append(Box.from_mask(ev.footprint) if ev.footprint.any() else ev.box)
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
        out.append(Spell(w.hazard, ev.id, ev.start, ev.end, ev.qualified,
                         idx[ev.start], idx[last], ev.box,
                         float(ev.peak_area_km2), ev.end is None))
    return out


def _evkey(ev):
    """Identity of a tracked event for the shipped-vs-tuned comparisons."""
    return (ev.id, str(ev.start), str(ev.end),
            (ev.box.i0, ev.box.i1, ev.box.j0, ev.box.j1))


def selfcheck(w: Window, params: Params) -> bool:
    """With all flags off the tuned tracker must equal the pre-C3 shipped rule.

    Production ``Params()`` is C3 since 2026-11-20 (SPEC §2), so both sides
    pin the knobs off here - the S0 row replays the old rule through the SAME
    ``Tracker`` code the strategies use (TunedTracker now only maps its flags
    onto the production knobs, so a table row cannot silently drift from
    production any more - review round 1).
    """
    from ewb_live.events.event_tracker import track_events

    legacy = replace(params, claim_valid_only=False, seed_valid_only=False,
                     merge_keep_box=False, core_clock="start")
    ref = [_evkey(ev) for ev in track_events(w.mask, w.dates, w.cell_area,
                                             legacy, w.hazard)]
    mine = [_evkey(ev) for ev in replay_raw(w, params)]
    ok = ref == mine
    print(f"selfcheck {w.hazard}: {'PASS' if ok else 'FAIL'} "
          f"({len(ref)} shipped vs {len(mine)} tuned events)", flush=True)
    return ok


def prodcheck(w: Window, params: Params) -> bool:
    """Production defaults (the C3 knobs) must reproduce the tuned C3 tracking.

    The C3 row is the TunedTracker with claim_overlap + seed_valid_only +
    merge_keeps_older_box, which maps onto production ``Params()`` - this
    asserts the two agree event-for-event on the live lane (including the
    round-1 merge fix, which both sides now run through the shared
    ``Tracker.step``), so the table row *is* what the next production run does.
    """
    from ewb_live.events.event_tracker import track_events

    ref = [_evkey(ev) for ev in track_events(w.mask, w.dates, w.cell_area,
                                             params, w.hazard)]
    mine = [_evkey(ev) for ev in replay_raw(w, params, claim_overlap=True,
                                            seed_valid_only=True,
                                            merge_keeps_older_box=True,
                                            core_clock="qualified")]
    ok = ref == mine
    print(f"prodcheck {w.hazard}: {'PASS' if ok else 'FAIL'} "
          f"({len(ref)} production-C3 vs {len(mine)} tuned-C3 events)", flush=True)
    return ok


def prodmatch(path: Path, w: Window, base_spells: list[Spell]) -> dict:
    """Compare the legacy (S0) replay with the PRODUCTION raw scan.

    selfcheck/prodcheck only prove the harness runs the tracker correctly;
    they agree with the tracker by construction. This is the check against
    the product: the shipped-rule replay of the lane window versus the
    production catalogue (``events.json``, the raw scan, uncured), paired by
    date+location (:func:`pairable`) and counted on (start, end) equality.
    Differences are expected where the two runs read different bytes or
    windows: production scanned to a later cutoff and fell back to ERA5
    ARCO when the lane did not span its window (``details.mask_source``) -
    the replay only sees the lane days.
    """
    live = [d for d in json.loads(path.read_text())
            if d["event_type"] == w.hazard]
    prod = [{"id": d["id"], "start": d["start"][:10], "end": d["end"][:10],
             "bbox": list(d["bbox"]),
             "mask_source": str((d.get("details") or {}).get("mask_source")),
             "outside_window": d["end"][:10] <= str(w.dates[0])
             or d["start"][:10] > str(w.dates[-1])}
            for d in live]
    mine = [{"id": s.id, "start": str(s.start),
             "end": str(s.end or w.dates[-1] + dt.timedelta(days=1)),
             "bbox": [float(w.lats[s.box.i0]), float(w.lats[s.box.i1]),
                      float(w.lons[s.box.j0]), float(w.lons[s.box.j1])]}
            for s in base_spells]
    exact = [m for m in mine if any(pairable(m, p) and p["start"] == m["start"]
                                    and p["end"] == m["end"] for p in prod)]
    paired = [m for m in mine if any(pairable(m, p) for p in prod)]
    unmatched_prod = [p for p in prod if not p["outside_window"]
                      and not any(pairable(m, p) for m in mine)]
    unmatched_mine = [m for m in mine
                      if not any(pairable(m, p) for p in prod)]
    res = {"n_replay": len(mine), "n_production": len(prod),
           "n_paired": len(paired), "n_exact_start_end": len(exact),
           "n_prod_outside_replay_window": sum(
               1 for p in prod if p["outside_window"]),
           "prod_mask_sources": {k: sum(1 for p in prod
                                        if p["mask_source"] == k)
                                 for k in sorted({p["mask_source"]
                                                  for p in prod})},
           "unmatched_production": sorted(unmatched_prod,
                                          key=lambda p: p["start"]),
           "unmatched_replay": sorted(unmatched_mine, key=lambda m: m["start"])}
    print(f"prodmatch {w.hazard}: replay {res['n_replay']} vs production "
          f"{res['n_production']} ({res['n_paired']} paired, "
          f"{res['n_exact_start_end']} exact start+end; "
          f"{res['n_prod_outside_replay_window']} production events outside "
          f"the replay window)", flush=True)
    return res


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
    return Box(i0, i1, round((bbox[2] - w.lons[0]) / step) % nx,
               round((bbox[3] - w.lons[0]) / step) % nx)


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
        "end": str(w.dates[p.spell.i1]),
        "qualified": str(p.spell.qualified) if p.spell.qualified else None,
        "days": p.spell.i1 - p.spell.i0 + 1,
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


def merge_pressure(rows: list[dict], hazard: str) -> dict:
    """How much work the curation merge gets asked to do with these events.

    Same-type events whose boxes overlap by ``select.MERGE_MIN_IOU`` and whose
    windows sit within ``select.MERGE_GAP_DAYS`` are merged into one event by
    ``select.rank_and_cap``; the merged window is capped at
    ``select.MERGE_CAP_DAYS`` for the type and the validator refuses anything
    over ``select.MAX_MERGED_DAYS``. Longer events make both caps bite, so the
    replay reports the exposure before the curator runs: how many pairs would
    merge, the largest merged window they would make, and how many break the
    type cap / the hard 45-day cap. O(n^2) on a couple of hundred events.
    """
    from ewb_live.events import select as _sel

    cap = float(_sel.MERGE_CAP_DAYS.get(hazard, _sel.MAX_MERGED_DAYS))
    evs = [(tuple(r["bbox"]), dt.date.fromisoformat(r["start"][:10]),
            dt.date.fromisoformat(r["end"][:10]))
           for r in rows if r.get("bbox")]
    pairs = over_cap = over_hard = 0
    worst = 0
    for i in range(len(evs)):
        bi, si, ei = evs[i]
        for j in range(i + 1, len(evs)):
            bj, sj, ej = evs[j]
            if _sel._bbox_iou(bi, bj) < _sel.MERGE_MIN_IOU:
                continue
            if max((sj - ei).days, (si - ej).days) > _sel.MERGE_GAP_DAYS:
                continue
            days = (max(ei, ej) - min(si, sj)).days
            pairs += 1
            worst = max(worst, days)
            over_cap += days > cap
            over_hard += days > _sel.MAX_MERGED_DAYS
    return {"merge_pairs": pairs, "merge_days_max": worst or 0,
            "merge_over_type_cap": over_cap, "merge_over_45d": over_hard}


def aggregate(rows: list[dict], runtime_s: float,
              hazard: str | None = None) -> dict:
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
        "days_med": float(np.median(dur)), "days_p90": _q(dur, 90),
        "days_max": max(dur),
        "excess_out_km2_tot": round(sum(r["excess_out_km2"] for r in rows)),
        "hole_km2_tot": round(sum(r["hole_km2"] for r in rows)),
        "worst3": [{"id": r["id"], "start": r["start"], "end": r["end"],
                    "box_km2": round(r["box_km2"]),
                    "bbox": [round(x, 1) for x in r["bbox"]],
                    "valid_ever_km2": round(r["valid_ever_km2"]),
                    "fill": round(r["fill"], 3)} for r in worst],
        "runtime_s": round(runtime_s, 1),
        **({} if hazard is None else merge_pressure(rows, hazard)),
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
    Strategy("C5_core_from_qualification",
             tweaks={"claim_overlap": True, "seed_valid_only": True,
                     "merge_keeps_older_box": True, "core_clock": "qualified"},
             post=post_persist(persist_k(0.5), 0.02),
             note="C3 tracking with the core clock and the anchor freeze starting "
                  "at qualification (owner 2026-11-21) = production Params()"),
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


def _bbox_intersects(a: dict, b: dict) -> bool:
    for l0, l1 in ([(a["bbox"][2], a["bbox"][3])]
                   if a["bbox"][2] <= a["bbox"][3]
                   else [(a["bbox"][2], 180.0), (-180.0, a["bbox"][3])]):
        for m0, m1 in ([(b["bbox"][2], b["bbox"][3])]
                       if b["bbox"][2] <= b["bbox"][3]
                       else [(b["bbox"][2], 180.0), (-180.0, b["bbox"][3])]):
            if min(l1, m1) - max(l0, m0) > 0:
                return True
    return False


def pairable(b: dict, r: dict) -> bool:
    """Location pairing between two scored rows: the windows share at least
    3 calendar days and the bboxes intersect (wrap-aware). Tracker ids are
    renumbered by every replay, so string pairing compares nothing."""
    b0 = dt.date.fromisoformat(b["start"])
    b1 = dt.date.fromisoformat(b["end"])
    r0 = dt.date.fromisoformat(r["start"])
    r1 = dt.date.fromisoformat(r["end"])
    return (min(b1, r1) - max(b0, r0)).days + 1 >= 3 and _bbox_intersects(b, r)


def lost_gained5(rows: list[dict], base_rows: list[dict]) -> dict:
    """The five biggest spells each rule failed to reproduce, by tracker box
    area - location pairing (see `pairable`), ids and bboxes included so the
    numbers can be checked by hand."""
    unmatched_c3 = {i for i, r in enumerate(rows)
                    if not any(pairable(b, r) for b in base_rows)}
    unmatched_s0 = {i for i, b in enumerate(base_rows)
                    if not any(pairable(b, r) for r in rows)}
    lost = sorted((base_rows[i] for i in sorted(unmatched_s0)),
                  key=lambda r: -r["box_km2"])
    gained = sorted((rows[i] for i in sorted(unmatched_c3)),
                    key=lambda r: -r["box_km2"])
    pick = lambda rr: [{"id": r["id"], "start": r["start"], "end": r["end"],
                        "box_km2": round(r["box_km2"]), "bbox": r["bbox"]}
                       for r in rr[:5]]
    return {"lost5": pick(lost), "gained5": pick(gained)}


def paired_worst(rows: list[dict], base_rows: list[dict], n: int = 5) -> list[dict]:
    """Before/after of the ``n`` biggest baseline boxes (C3 is a big rule,
    not a tweak - we want to see what happened to the boxes that made the
    problem visible).

    Successors are matched by DATE AND LOCATION - windows sharing >= 3 days
    whose bboxes intersect - not by tracker-id string: every C3 run renumbers
    the tracker's objects, so id pairing compares nothing (review #5). The
    biggest such successor is the "after" box; a spell that C3 split shows
    its largest piece, and one it killed shows kept=false.
    """
    out = []
    for b in sorted(base_rows, key=lambda r: -r["box_km2"])[:n]:
        cand = [r for r in rows if pairable(b, r)]
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
        agg = aggregate(rows, time.perf_counter() - t0, w.hazard)
        agg["n_qual_eq_end"] = sum(1 for s in spells
                                   if s.qualified is not None and s.qualified == s.end)
        agg["n_over_45d"] = sum(1 for s in spells
                                if (s.i1 - s.i0 + 1) > MAX_PUBLISH_DAYS)
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
            agg["worst5_paired"] = paired_worst(rows, base_rows)
            agg.update(lost_gained5(rows, base_rows))
        out[st.name] = {"summary": agg, "rows": rows}
        print(f"  {w.hazard:<10} {st.name:<31} n={agg['n']:<4} "
              f"fill_med={agg.get('fill_med', 0):.2f} "
              f"box_med={agg.get('box_km2_med', 0) / 1e6:6.2f}M "
              f"box_p90={agg.get('box_km2_p90', 0) / 1e6:6.2f}M "
              f"box_max={agg.get('box_km2_max', 0) / 1e6:6.2f}M "
              f"days_med={agg.get('days_med', 0):4.1f} "
              f"q=end={agg.get('n_qual_eq_end', 0):<3} "
              f">45d={agg.get('n_over_45d', 0):<3} "
              f"merge={agg.get('merge_pairs', 0)}/{agg.get('merge_days_max', 0)}d/"
              f"{agg.get('merge_over_type_cap', 0)}cap "
              f"lost={agg.get('lost_vs_current', '-'):<3} "
              f"gained={agg.get('gained_vs_current', '-'):<3} "
              f"split+={agg.get('published_split_extra', 0):<3} "
              f"({agg['runtime_s']}s)", flush=True)
    return out


# ------------------------------------------------------------ published state
def successor_audit(path: Path, w: Window, rows_by_stage: dict[str, list[dict]],
                    follow: tuple[str, ...] = ("C3_valid_box_persist",
                                               "C5_core_from_qualification"),
                    ) -> dict:
    """Did the old same-day ending really split a spell?

    The shipped rule could close a candidate on the day it qualified, leaving
    its region free to seed a new event the same day; 31 live heat/freeze
    events have ``qualified_date == end``. For each of them this finds a live
    event of the same type that *starts on that closing day* and overlaps the
    closed box in space (the successor the old rule created, if the spell kept
    going), then follows the closed event into each replay stage with the
    committed location matcher (:func:`pairable`) and reports how many replay
    rows cover it and its successor. ``became = 1`` means one spell under that
    stage, i.e. the pair really was one event.
    """
    live = [d for d in json.loads(path.read_text())
            if d["event_type"] == w.hazard]
    rows = []
    for d in live:
        det = d.get("details") or {}
        if str(det.get("qualified_date") or "") != str(d["end"][:10]):
            continue
        qd = dt.date.fromisoformat(str(d["end"][:10]))
        closed = {"id": d["id"], "start": d["start"][:10], "end": d["end"][:10],
                  "bbox": list(d["bbox"])}
        succ = [x for x in live
                if x["id"] != d["id"]
                and dt.date.fromisoformat(x["start"][:10]) == qd
                and _bbox_intersects(closed, {"bbox": list(x["bbox"])})]
        row = {"id": d["id"], "start": closed["start"], "end": closed["end"],
               "successors": [{"id": x["id"], "end": x["end"][:10],
                               "days": (dt.date.fromisoformat(x["end"][:10])
                                         - qd).days} for x in succ],
               "successor_start_overlaps_spell": any(
                   x["start"][:10] <= closed["end"] and x["end"][:10] >= closed["start"]
                   for x in succ)}
        for stage in follow:
            stage_rows = rows_by_stage.get(stage, [])
            hit = [r for r in stage_rows if pairable(closed, r)]
            succ_rows = [{"start": x["start"][:10], "end": x["end"][:10],
                          "bbox": list(x["bbox"])} for x in succ]
            both = [r for r in stage_rows
                    if pairable(closed, r) or any(pairable(s, r) for s in succ_rows)]
            # the pair really was one spell if ONE replay object covers both
            one = [r for r in stage_rows
                   if pairable(closed, r) and any(pairable(s, r) for s in succ_rows)]
            row[stage] = {"rows_for_closed": len(hit),
                          "rows_for_closed_plus_successor": len(both),
                          "one_row_covers_both": bool(one),
                          "successor_was_live": bool(succ)}
        rows.append(row)
    return {"n_qual_eq_end": len(rows),
            "n_with_same_day_successor": sum(1 for r in rows if r["successors"]),
            "n_successor_overlapping_the_spell": sum(
                1 for r in rows if r["successor_start_overlaps_spell"]),
            "rows": rows}


def published_metrics(path: Path, w: Window) -> dict:
    """Fill of the live catalogue's boxes, measured on the lane's own masks."""
    rows = []
    for d in json.loads(path.read_text()):
        if d["event_type"] != w.hazard:
            continue
        det = d.get("details") or {}
        st = dt.date.fromisoformat(d["start"][:10])
        en = dt.date.fromisoformat(d["end"][:10]) - dt.timedelta(days=1)
        if en < w.dates[0] or st > w.dates[-1]:
            continue
        i0 = max(0, next(i for i, x in enumerate(w.dates) if x >= st))
        i1 = next((i for i, x in enumerate(w.dates) if x >= en), len(w.dates) - 1)
        spell = Spell(w.hazard, d["id"], st, en + dt.timedelta(days=1),
                      dt.date.fromisoformat(str(det.get("qualified_date")
                                                or st)), i0, i1,
                      box_from_bbox(w, tuple(d["bbox"])),
                      float(d["details"].get("peak_area_km2", 0.0)),
                      bool(d["details"].get("ongoing")))
        m = score_one(w, Published(spell, spell.box))
        m["days_clamped"] = st < w.dates[0] or en > w.dates[-1]
        m["merged_at_curation"] = bool(det.get("merged_from"))
        rows.append(m)
    agg = aggregate(rows, 0.0, w.hazard)
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
    ap.add_argument("--emit-events", default="",
                    help="write the C5 stage as an events.json-shaped file "
                         "(for scripts/review/hb05_qualify_fallbacks.py)")
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
        if a.selfcheck and not (selfcheck(w, params0) and prodcheck(w, params0)):
            return 1
        t0 = time.perf_counter()
        base_spells = replay(w, params0)
        base_rows = [score_one(w, Published(s, s.box)) for s in base_spells]
        rows_by_stage: dict[str, list[dict]] = {"S0_current": base_rows}
        base_agg = aggregate(base_rows, time.perf_counter() - t0, w.hazard)
        base_agg["note"] = "as shipped"
        base_agg["n_qual_eq_end"] = sum(1 for s in base_spells
                                        if s.qualified == s.end)
        base_agg["n_over_45d"] = sum(1 for s in base_spells
                                     if (s.i1 - s.i0 + 1) > MAX_PUBLISH_DAYS)
        base_agg["worst5_paired"] = paired_worst(base_rows, base_rows)
        entry: dict[str, Any] = {"S0_current": {"summary": base_agg,
                                                "rows": base_rows}}
        entry["attribution"] = attribute(w, params0)
        entry["seam_audit"] = seam_audit(w, base_spells)
        entry.update(run_strategies(w, base_spells, base_rows, params0, only))
        rows_by_stage.update({k: v["rows"] for k, v in entry.items()
                              if isinstance(v, dict) and "rows" in v})
        if a.published and Path(a.published).exists():
            entry["prod_match"] = prodmatch(Path(a.published), w, base_spells)
        if a.published and Path(a.published).exists():
            entry["successor_audit"] = successor_audit(Path(a.published), w,
                                                       rows_by_stage)
        if a.published and Path(a.published).exists():
            entry["P0_published_json"] = published_metrics(Path(a.published), w)
            p = entry["P0_published_json"]["summary"]
            print(f"  {hazard:<10} {'P0_published_json':<31} n={p['n']:<4} "
                  f"fill_med={p['fill_med']:.2f} "
                  f"box_med={p['box_km2_med'] / 1e6:6.2f}M "
                  f"box_max={p['box_km2_max'] / 1e6:6.2f}M", flush=True)
        if a.emit_events and "C5_core_from_qualification" in entry:
            # the qualification scan only needs id/type/dates/bbox/qualified_date,
            # so the C5 rows can be replayed through hb05 without a full scan
            emitted = [{"id": r["id"], "event_type": hazard,
                        "start": f"{r['start']}T00:00:00Z",
                        "end": f"{(dt.date.fromisoformat(r['end']) + dt.timedelta(days=1)):%Y-%m-%dT00:00:00Z}",
                        "bbox": r["bbox"],
                        "details": {"qualified_date": r["qualified"],
                                    "peak_area_km2": r["peak_km2"],
                                    "ongoing": r["ongoing"]}}
                       for r in entry["C5_core_from_qualification"]["rows"]]
            out = Path(a.emit_events).with_name(
                f"{Path(a.emit_events).name}_{hazard}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(emitted, indent=1))
            print(f"  wrote {out} ({len(emitted)} events)", flush=True)
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
            "successor_audit": entry.get("successor_audit"),
            "stages": {k: {col: entry[k]["summary"].get(col)
                           for col in ("n", "fill_med", "daily_fill_med",
                                       "box_km2_med", "box_km2_p90",
                                       "box_km2_max", "box_over_peak_med",
                                       "days_med", "days_p90", "days_max",
                                       "n_qual_eq_end", "n_over_45d",
                                       "merge_pairs", "merge_days_max",
                                       "merge_over_type_cap", "merge_over_45d",
                                       "lost_vs_current", "gained_vs_current")}
                       for k in ("S0_current", "C3_valid_box_persist",
                                 "C5_core_from_qualification")
                       if k in entry},
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
