"""Flood events: GPM IMERG daily rain measured against ERA5 wet-day percentiles.

Owner spec (2026-11-16): *"use ERA5 for climatologies for QPF, then use
IMERG/GPM for the past 90 days of data."* The two halves are built by the other
flood roles and are joined here:

* climatology — ``ewb_live/climatology/precip_clim.py`` (role flood-clim):
  pooled wet-day day-of-year percentiles (p95/p99) of the ERA5 0.25 deg daily
  total and of the trailing 72 h accumulation for 1990-2019, NaN where the
  pooled wet-sample count is too low for a percentile to mean anything;
* observations — ``ewb_live/sources/imerg.py`` (role flood-imerg): IMERG V07
  L3 daily mm/day on the producer's 0.1 deg lattice for the rolling 90-day
  window, with a per-pixel sampling-count completeness mask.

The rule (design: ``notes/flood_design.md`` Part 1 §5 + Part 2 §3-4)
---------------------------------------------------------------------
1. **Join direction.** IMERG 0.1 deg is aggregated UP to the ERA5 0.25 deg
   lattice (the mean of the 0.1 deg cell centres that fall inside each 0.25 deg
   cell — a 2.5 x 2.5 areal average) and the comparison is made there.
   Thresholds are never interpolated down onto the 0.1 deg observation grid:
   that direction inflates exceedance (a finer observation grid samples more
   spatial intermittency than a coarser climatology) and interpolating a
   threshold field smooths exactly the extremes being thresholded on
   (flood-clim §3). The 0.25 deg result is also cell-aligned with the live
   temperature climatology, so the dashboard needs no new regridding.
2. **Bias.** The thresholds are ERA5 percentiles, not IMERG percentiles, so an
   exceedance honestly reads as *"IMERG measured more rain in this cell than
   ERA5's p95 for this cell and date of year"*. No distribution mapping is
   applied — that needs an IMERG climatology the 90-day lane cannot supply
   (flood-clim §3 lists the owner decision: a 0.1 deg IMERG-Final percentile
   build). ``precip_clim.precip_threshold*(scale=)`` is the seam for a later
   per-cell correction; this lane ships ``scale=None``. Because the two
   conditional distributions really do differ, the **absolute floor is
   mandatory, not decorative**: ``obs >= threshold`` AND ``obs >= FLOOR_MM``
   with ``FLOOR_MM = {1: 20 mm/day, 3: 50 mm/72 h}``, read from
   ``precip_clim`` (one source, so the detector and the About text cannot
   drift apart). Without it a few-millimetre day in the Sahel "exceeds" a
   14-20 mm desert percentile.
3. **Two accumulation lanes.** ``window=1`` is the UTC-day total, ``window=3``
   the trailing 72 h ending 24:00 UTC of the indexed date — the climatology's
   own convention, so the 3-day threshold is indexed by the END date. A rain
   flood is driven by antecedent accumulation and a flash flood by one burst,
   so the daily exceedance mask is the UNION of the two lanes and every event
   records which lane(s) produced it (``details.lanes``). One tracker pass over
   the union keeps one spell one event; tracking the lanes separately would
   publish the same flood twice.
4. **Never count a partial day.** A 0.25 deg cell is compared only where EVERY
   contributing 0.1 deg pixel is a real observation whose 48 half-hourly steps
   all contributed (``screen="cnt"`` plus all-pixel completeness inside the
   cell). A day whose granule is absent is dropped from the tracked sequence
   rather than read as dry, and a 3-day sum requires all three days observed.
   IMERG's Early/Late runs lose sampling above ~60 deg, so those cells are
   blank here instead of averaged from partial sampling.
5. **Land only.** The climatology has thresholds over the ocean (ERA5 rains
   there) and the largest values in the product are oceanic ITCZ cells
   (flood-clim §5); the Natural Earth 110 m land mask the temperature and viz
   lanes already use (``ewb_live/viz/build.py``) gates the mask.
6. **Provisional.** IMERG Final is published ~3.5 months after the fact, so a
   rolling 90-day window is built on the Late Run (Early Run for the last day
   or two). Every event carries ``details.provisional`` plus the run mix: the
   product never presents an Early/Late accumulation as settled science.

Tracking reuses the product owner's footprint/core tracker
(``ewb_live/events/event_tracker.py``, SPEC §2) with a flood-tuned
:class:`~ewb_live.events.event_tracker.Params` — floods are smaller, faster and
more ragged than the continental heat spells the default areas were tuned for
(see :data:`FLOOD_PARAMS`). Longitude wraps; latitude does not; a wrapped bbox
uses the repo's ``lon_min > lon_max`` eastward-arc convention.

Not scored: EWB ships no precipitation-flood benchmark for this event shape, so
the pipeline publishes flood events with a documented ``unsupported`` pair
status — the same treatment as the tropical-cyclone lane (``SCORED = False``).

CLI (ewb LXC, where the two stores live)::

    python -m ewb_live.events.flood --days 90            # report only
    python -m ewb_live.events.flood --days 90 --json /tmp/floods.json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import sys
from bisect import bisect_left
from typing import Any, Literal, Sequence

import numpy as np
import xarray as xr

from ..climatology import precip_clim
from ..sources import imerg, pardig
from .event_tracker import Box, Params, track_events
from .types import Event, make_event_id, normalize_lon_180

logger = logging.getLogger(__name__)

FLOOD_TYPE = "flood"

# --- rule constants, each with ONE source of truth ------------------------ #
DEFAULT_QUANTILE = float(os.environ.get("EWB_LIVE_FLOOD_Q", "0.95"))
#: accumulation lanes in days: 1 = UTC-day total, 3 = trailing 72 h ending
#: 24:00 UTC of the indexed date (the climatology's convention)
WINDOWS: tuple[int, ...] = (1, 3)
#: absolute plausibility floors (mm), read from the climatology module
FLOOR_MM: dict[int, float] = {int(k): float(v) for k, v in precip_clim.FLOOR_MM.items()}
PEAK_VALUE_UNITS = "mm"
#: the only run that would make an event non-provisional (never in 90 days)
SETTLED_RUN = "final"
#: this lane is published, not scored (see the module docstring + pipeline.py)
SCORED = False

# --- grid ------------------------------------------------------------------ #
ERA5_DEG = 0.25          # climatology/dashboard lattice (lat desc, lon -180)
LATTICE_TOL = 1e-4       # degrees; the daily store keeps float32 coordinates

# --- window ---------------------------------------------------------------- #
#: days loaded before ``start``: 2 for the trailing 72 h lane, 2 more so an
#: event that began before the window keeps its true start date
TRACKED_LOOKBACK_DAYS = 4
#: a UTC day counts as observed when at least this share of the globe has a
#: real IMERG value (an absent granule is an all-NaN day -> dropped entirely)
PRESENT_FRACTION_MIN = 0.5

DETECTION_TEXT = (
    "IMERG V07 daily rain (0.1 deg, averaged up to the 0.25 deg ERA5 lattice) "
    "at or above the local 95th percentile of the ERA5 1990-2019 wet-day "
    "distribution for that day of year — as a daily total and/or as a trailing "
    "72 h accumulation — and at or above the absolute floor (20 mm in a day, "
    "50 mm in 72 h), over land, with every contributing half-hourly sample "
    "present. A region >=10,000 km2 becomes an event once its footprint "
    "reaches 50,000 km2 and ends when the cells that have counted every day "
    "since it began hold under 10,000 km2."
)


@dataclasses.dataclass(frozen=True)
class FloodParams(Params):
    """The footprint/core tracker tuned for rain floods (see :data:`FLOOD_PARAMS`).

    A named subclass of the shipped :class:`~ewb_live.events.event_tracker.Params`
    rather than an inline ``dataclasses.replace`` so the block is importable,
    documented and comparable with the temperature lane's defaults. Areas in
    km2, distances in 0.25 deg cells.
    """

    # WHY these numbers differ from the heat/freeze defaults
    # ------------------------------------------------------
    # The shipped areas (seed 50k / qualify 200k / end 100k km2, min 3 days) are
    # tuned for continental heat spells that sit for days. A rain-flood
    # footprint that exceeds a p95 wet-day threshold over 200k km2 is rare
    # outside monsoon bursts, so the flood lane uses ~1/4 of those areas; the
    # live 90-day window measured on the ewb LXC is the evidence in
    # notes/channel_new_events.md (flood-detect DONE entry).
    #   seed/qualify/end  10k / 50k / 10k km2  (26 / 65 / 13 equatorial cells)
    #   min_days 1        a one-day 50k km2 p95-exceeding wet spell is a real
    #                     flash flood; persistence is carried by the 72 h lane
    #                     and by the area floor, not by a duration floor
    #   max_ramp_days 3   floods organize faster than heat domes
    #   region_radius 3   1.5 deg grouping: convective footprints are ragged, a
    #                     6-cell close would weld unrelated storms into one
    #                     "event" across a whole monsoon trough
    #   region_min_fraction 0.25, move_cells 4, min_overlap 0.4, join 2k km2
    #   anchor_day 2      the core is measured in the day-2 box (a rain complex
    #                     that walks 2 deg overnight is still the same event)
    #   max_box_area_km2 1.5e6  an object whose box is bigger than that (~35x43
    #                     deg at the equator) is a season, not an event
    #   box-size rule knobs (2026-11-20): pinned to the shipped tracking rules.
    #                     The valid-only claim/seed, keep-the-older-box merge
    #                     and persistence publish box were designed and
    #                     measured for heat/freeze spells
    #                     (notes/heatwave_box_diagnosis.md); the flood lane
    #                     keeps its pre-change geometry until re-measured, and
    #                     already caps its box via max_box_area_km2.
    #   core_clock "start"  the core counts every-day-valid cells from the
    #                     candidate's start day (the pre-2026-11-21 rule). The
    #                     qualify-clock decision is the owner's for the
    #                     heat/freeze/cold_snap lane only; floods are not
    #                     re-measured against it, so they keep the old clock.
    seed_area_km2: float = 1.0e4
    qualify_area_km2: float = 5.0e4
    end_area_km2: float = 1.0e4
    min_days: int = 1
    max_ramp_days: int = 3
    region_radius: int = 3
    region_min_fraction: float = 0.25
    move_cells: int = 4
    min_overlap: float = 0.4
    join_area_km2: float = 2.0e3
    core_clock: Literal["qualified", "start"] = "start"
    anchor_day: int | None = 2
    max_box_area_km2: float | None = 1.5e6
    claim_valid_only: bool = False
    seed_valid_only: bool = False
    merge_keep_box: bool = False
    # inert here (merge_keep_box=False ignores it) but PINNED: the value is
    # published in every flood event's details.tracker_params, so inheriting
    # the base default (now "largest_close") would claim a rule
    # this lane does not run - the pre-2026-11-21 merge is what the flood
    # digest is pinned to
    merge_survivor: Literal["oldest_qualified", "largest", "largest_close",
                            "separate"] = "oldest_qualified"
    publish_box: Literal["persistence", "tracker"] = "tracker"


FLOOD_PARAMS = FloodParams()


# ---------------------------------------------------------------------------
# observations: IMERG 0.1 deg -> the ERA5 0.25 deg lattice
# ---------------------------------------------------------------------------
def era5_grid(deg: float = ERA5_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Target lattice: latitude 90..-90 DESCENDING, longitude -180.. ascending.

    The same arrays as the live temperature climatology / the dashboard DAYEXT
    grid, kept in the -180 longitude frame so the array seam IS the
    antimeridian (what the tracker's wrap convention and ``Event.bbox`` mean).
    """
    nlat = 180.0 / deg
    nlon = 360.0 / deg
    if abs(nlat - round(nlat)) > 1e-9:
        raise ValueError(f"grid step {deg} deg must divide 180 evenly")
    return (90.0 - deg * np.arange(round(nlat) + 1),
            -180.0 + deg * np.arange(round(nlon)))


def _cell_index(src: np.ndarray, dst: np.ndarray, *, wrap: bool) -> np.ndarray:
    """Which target cell each source centre belongs to (its nearest target centre).

    For two regular lattices "the source centre inside the target box" and
    "the source centre nearest the target centre" are the same relation, and
    the nearest-centre form wraps correctly: at 0.1/0.25 deg the lattices are
    offset by 0.05 deg, so a target box boundary never lines up with a source
    cell edge and exactly one target column always straddles the antimeridian —
    index arithmetic on a circle handles it, a linear searchsorted does not
    (it silently drops the seam cell's other source column and biases that
    column of the areal mean).
    """
    step = float(dst[1] - dst[0])
    k = np.rint((src - dst[0]) / step)
    if wrap:
        n = dst.size
        k = np.mod(np.rint(np.mod(src - dst[0], abs(step) * n) / step), n)
    else:
        k = np.clip(k, 0, dst.size - 1)      # poles absorb what falls outside
    return k.astype("int64")


def aggregate_to_era5_grid(da: xr.DataArray, *,
                           src_deg: float = float(imerg.IMERG_GRID_DEG),
                           dst_deg: float = ERA5_DEG,
                           require_all: bool = True) -> xr.DataArray:
    """Areal-average a 0.1 deg ``(time, lat, lon)`` daily field onto the ERA5 grid.

    A target cell is the unweighted mean of the source cell centres inside it
    (2-3 rows x 2-3 columns at 0.1 -> 0.25 deg, and 1-3 rows near the poles,
    where IMERG's lattice stops at +-89.95). Cell areas inside one 0.25 deg
    cell differ by <0.2 % (cos latitude moves ~0.0015 across it), so area
    weighting is below the data's own precision; NaNs are excluded from both
    the sum and the count.

    ``require_all`` (default): a target cell is NaN unless EVERY source cell
    belonging to it was observed — a partly observed 0.25 deg cell is not the
    same random variable as the climatology's, so it is dropped rather than
    averaged.

    Returns ``(time, latitude, longitude)`` float32 in the input's units on the
    lattice from :func:`era5_grid`; a 2-D input returns a 2-D output.
    """
    if da.dims[-1] not in ("lon", "longitude") or da.dims[-2] not in ("lat", "latitude"):
        raise ValueError(f"expected trailing (lat, lon) dims, got {da.dims}")
    lat = np.asarray(da.coords[da.dims[-2]].values, "float64")
    lon = np.asarray(da.coords[da.dims[-1]].values, "float64")
    for name, ax in (("latitude", lat), ("longitude", lon)):
        step = float(np.median(np.diff(ax)))
        if abs(abs(step) - src_deg) > LATTICE_TOL:
            raise ValueError(f"{name}: source spacing {step:.6f} deg, expected "
                             f"{src_deg} deg")
    if abs(abs(lon[1] - lon[0]) - src_deg) > LATTICE_TOL:
        raise ValueError("longitude is not a regular lattice")
    dst_lat, dst_lon = era5_grid(dst_deg)
    iy = _cell_index(lat, dst_lat, wrap=False)
    ix = _cell_index(lon, dst_lon, wrap=True)
    ncell = (dst_lat.size, dst_lon.size)
    flat = iy[:, None] * dst_lon.size + ix[None, :]        # (src lat, src lon)
    # how many source cells each target cell is supposed to receive
    expect = np.bincount(flat.ravel(), minlength=ncell[0] * ncell[1])
    if (expect == 0).any():
        missing = int((expect == 0).sum())
        raise ValueError(f"the {src_deg} deg lattice leaves {missing} target "
                         f"cells without any source cell")
    if int(expect.sum()) != lat.size * lon.size:
        raise ValueError("source cells were not partitioned exactly once")
    expect = expect.reshape(ncell)

    src = np.asarray(da.values, "float32")
    single = src.ndim == 2
    if single:
        src = src[None]
    if src.ndim != 3:
        raise ValueError(f"expected (time, lat, lon), got shape {src.shape}")
    out = np.empty((src.shape[0], *ncell), "float32")
    flat_r = flat.ravel()
    for k in range(src.shape[0]):
        frame = src[k]
        good = np.isfinite(frame)
        # bincount accumulates in float64 over <= 9 values per cell, so no
        # cancellation (a global float32 cumsum loses ~1 mm to it)
        w = np.where(good, frame, 0.0).astype("float64").ravel()
        tot = np.bincount(flat_r, weights=w, minlength=ncell[0] * ncell[1])
        cnt = np.bincount(flat_r[good.ravel()], minlength=ncell[0] * ncell[1])
        tot = tot.reshape(ncell)
        cnt = cnt.reshape(ncell)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = (tot / cnt).astype("float32")
        m[(cnt <= 0) | (require_all & (cnt != expect))] = np.nan
        out[k] = m

    coords: dict[str, Any] = {"latitude": dst_lat, "longitude": dst_lon}
    dims: list[str] = ["latitude", "longitude"]
    if not single:
        coords["time"] = da.coords["time"].values
        dims = ["time", "latitude", "longitude"]
    return xr.DataArray(
        out[0] if single else out, dims=dims, coords=coords,
        name="precipitation_era5_grid",
        attrs={"units": da.attrs.get("units", imerg.UNITS),
               "src_deg": float(src_deg), "dst_deg": float(dst_deg),
               "join": f"{src_deg} deg cell centres averaged into each "
                       f"{dst_deg} deg cell",
               "require_all": bool(require_all)})


def trailing_accumulation(daily: np.ndarray, days: int) -> np.ndarray:
    """Trailing ``days``-day sums ending at each index; NaN if any day is NaN.

    Matches the climatology's ``window=N`` convention: the accumulation of
    ``date-(N-1) ... date``, ending 24:00 UTC of the indexed date. The first
    ``days-1`` entries are NaN (not observed far enough back).
    """
    d = np.asarray(daily, "float32")
    if days < 1:
        raise ValueError("days must be >= 1")
    if days == 1:
        return d.copy()
    if d.shape[0] < days:
        return np.full_like(d, np.nan)
    v = np.where(np.isfinite(d), d, 0.0)
    s = np.cumsum(v, axis=0, dtype="float32")
    c = np.cumsum(np.isfinite(d), axis=0, dtype="int32")
    # s_prev[k] = s[k-days] (the mass that left the window), 0 for the leading frames
    def _lag(a: np.ndarray) -> np.ndarray:
        z = np.zeros((days,) + a.shape[1:], dtype=a.dtype)
        return np.concatenate([z, a], axis=0)[:a.shape[0]]

    win_sum, win_cnt = s - _lag(s), c - _lag(c)
    out = np.where(win_cnt == days, win_sum, np.nan).astype("float32")
    out[:days - 1] = np.nan           # never observed far enough back
    return out


def exceedance(obs: np.ndarray, thr: np.ndarray, floor_mm: float) -> np.ndarray:
    """``obs >= threshold`` AND ``obs >= floor``, with NaN treated as False.

    A NaN threshold means *no flood is definable here* (hyper-arid cell, too
    few pooled wet days). It must never be filled with 0 — that turns the
    Sahara into a flood factory (flood-clim §4).
    """
    with np.errstate(invalid="ignore"):
        return np.asarray((obs >= thr) & (obs >= float(floor_mm)), bool)


# ---------------------------------------------------------------------------
# loading the observation window
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Observations:
    """IMERG daily rain on the ERA5 lattice, plus its completeness/provenance."""

    dates: list[dt.date]                 # every date asked for, observed or not
    daily: np.ndarray                    # (days, lat, lon) mm/day, ERA5 grid
    present: np.ndarray                  # bool per day: a real granule behind it
    runs: dict[str, str]                 # day -> which IMERG run served it
    grid: tuple[np.ndarray, np.ndarray]  # (lat desc, lon -180..) of ``daily``
    source: dict[str, Any]

    @property
    def observed_dates(self) -> list[dt.date]:
        """Days with a real global granule: the only days the tracker sees."""
        return [d for d, ok in zip(self.dates, self.present, strict=True) if ok]


def resolve_source(source: str | None = None) -> str:
    """'pardig' | 'imerg' — explicit arg wins, else EWB_FLOOD_SOURCE, else
    PARDIG (owner follow-up 2026-11-19). Raises on anything else."""
    s = (source or os.environ.get("EWB_FLOOD_SOURCE") or "pardig").strip().lower()
    if s not in ("pardig", "imerg"):
        raise ValueError(f"unknown flood rain source {s!r}; use 'pardig' or 'imerg'")
    return s


def _run_of(prod: str, granule: str) -> str:
    """Resolve the concrete IMERG run behind a status row.

    ``status.json`` can carry the selector key ``auto`` instead of a run; the
    granule name is the ground truth (``3B-DAY-E`` = Early, ``3B-DAY-L`` =
    Late, plain ``3B-DAY.`` = Final), so the provenance string the UI shows
    never says "auto" (audit F4).
    """
    g = str(granule or "")
    if "3B-DAY-E" in g or "3B-HHR-E" in g:
        return "early"
    if "3B-DAY-L" in g or "3B-HHR-L" in g:
        return "late"
    if "3B-DAY." in g or "3B-HHR." in g:
        return "final"
    if prod == "auto":
        return "late"          # auto prefers Late and no granule name to read
    return prod or "unknown"


def load_observations(start: dt.date, end: dt.date, *, root: Any = None,
                      screen: str = "cnt", min_cnt: int = imerg.STEPS_PER_DAY,
                      chunk_days: int = 10, require_all: bool = True,
                      src_deg: float = float(imerg.IMERG_GRID_DEG),
                      dst_deg: float = ERA5_DEG,
                      source: str | None = None) -> Observations:
    """Daily precipitation mm on the ERA5 lattice for [start, end] + completeness.

    ``source``: ``"pardig"`` (default, owner 2026-11-19 — Google PARDIG-NRT
    AI-estimated 15-min rates summed to daily totals, ~2-8 h latency, CC BY
    4.0; cache root = ``data/pardig`` / ``EWB_LIVE_PARDIG_DIR``) or
    ``"imerg"`` (NASA GPM IMERG V07 daily, cache root = ``data/imerg`` — kept
    as the fallback until the owner approves removal). The env
    ``EWB_FLOOD_SOURCE`` overrides the default; every produced event carries
    the resolved provenance in ``details.data_source`` (incl. ``kind``), so a
    mixed store is always attributable per event. The climatology/thresholds
    are ERA5 in BOTH modes (owner decision — the accepted ERA5-vs-satellite
    bias lives in climatology_note).

    Reads the store in ``chunk_days`` slices so peak memory stays a few hundred
    MB on a live box (the full 90-day 0.1 deg stack is 2.4 GB). ``src_deg`` /
    ``dst_deg`` default to the production pair (0.1 -> 0.25); they are
    parameters because the join is pure lattice arithmetic and the unit tests
    run it on a coarse global pair. (PARDIG's cached totals are already on
    the ``dst_deg`` lattice; the join happened at cache build.)
    """
    source = resolve_source(source)
    dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    frames: list[np.ndarray] = []
    present: list[bool] = []
    lats = lons = None
    for i in range(0, len(dates), chunk_days):
        part = dates[i:i + chunk_days]
        if source == "imerg":
            da = imerg.load_imerg_daily(part[0], part[-1], root=root, product="auto",
                                        screen=screen, min_cnt=min_cnt)
            agg = aggregate_to_era5_grid(da, src_deg=src_deg, dst_deg=dst_deg,
                                         require_all=require_all)
            del da
        else:
            agg = pardig.load_pardig_daily(part[0], part[-1], root=root)
        if lats is None:
            lats = np.asarray(agg.latitude.values, "float64")
            lons = np.asarray(agg.longitude.values, "float64")
        frames.append(np.asarray(agg.values, "float32"))
        frac = np.isfinite(agg.values).mean(axis=(-2, -1))
        present += [bool(k) for k in frac >= PRESENT_FRACTION_MIN]
        del agg
    daily = (np.concatenate(frames, axis=0) if frames
             else np.zeros((0, 0, 0), "float32"))
    del frames

    present_arr = np.array(present, bool)
    missing = [f"{d:%Y-%m-%d}" for d, ok in zip(dates, present_arr, strict=True)
               if not ok]
    if source == "imerg":
        status = imerg.load_imerg_status(start, end, root=root)
        want = {f"{d:%Y-%m-%d}" for d in dates}
        served = {f"{d:%Y-%m-%d}": ok for d, ok in zip(dates, present, strict=True)}
        runs: dict[str, str] = {}
        mix: dict[str, int] = {}
        for day, row in status.iterrows():
            key = f"{dt.date.fromisoformat(str(day)[:10]):%Y-%m-%d}"
            if key not in want:
                continue
            prod = _run_of(str(row.get("product") or "unknown"), str(row.get("granule") or ""))
            runs[key] = prod
            # the run mix counts only days that ACTUALLY served data — status.json
            # lists not-yet-arrived days too, and a resolved label for an absent
            # day must not masquerade as provenance of observations.
            if served.get(key):
                mix[prod] = mix.get(prod, 0) + 1
        source_dict = {
            "kind": "imerg",
            "product": "auto (Late Run preferred, Early Run for the head)",
            "collection": "GPM_3IMERGDE / GPM_3IMERGDL V07 daily, 0.1 deg",
            "store": imerg._root(root).name,
            "screen": screen, "min_cnt": int(min_cnt),
            "days_total": len(dates), "days_present": int(present_arr.sum()),
            "missing_days": missing, "runs": mix,
            "grid_deg": dst_deg, "src_grid_deg": float(src_deg),
            "units": imerg.UNITS,
        }
    else:
        runs = {f"{d:%Y-%m-%d}": "nrt" for d, ok in zip(dates, present, strict=True) if ok}
        mix = {"nrt": int(present_arr.sum())}
        source_dict = {
            "kind": "pardig",
            "product": "PARDIG-NRT (AI-estimated precipitation, Google DeepMind/Research)",
            "collection": "gs://pardig_1 NRT: 0.1 deg 15-min instantaneous rates summed "
                          "to UTC-day totals, 0.25 deg ERA5-lattice cache",
            "license": "CC BY 4.0",
            "display": "AI-estimated precipitation (Google PARDIG, CC BY 4.0) — not an "
                       "official observation",
            "store": pardig.pardig_root(root).name,
            "days_total": len(dates), "days_present": int(present_arr.sum()),
            "missing_days": missing, "runs": mix,
            "grid_deg": dst_deg, "src_grid_deg": float(pardig.GRID_DEG),
            "units": pardig.UNITS,
        }
    logger.info("flood source %s %s..%s: %d/%d days served, runs %s", source,
                start, end, source_dict["days_present"], len(dates), mix)
    return Observations(dates=dates, daily=daily, present=present_arr, runs=runs,
                        grid=(lats if lats is not None else np.array([]),
                              lons if lons is not None else np.array([])),
                        source=source_dict)


def land_mask(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Natural Earth 110 m land — the same mask the temperature/viz lanes use."""
    from ..viz.build import land_mask as _land   # lazy: pulls regionmask/EWB

    return _land(np.asarray(lats), np.asarray(lons))


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class FloodScan:
    """One window's exceedance masks + everything needed to build Events."""

    dates: list[dt.date]                 # every calendar day (complete sequence)
    mask: np.ndarray                     # (days, lat, lon) union exceedance
    mask_lane: dict[int, np.ndarray]     # per-lane exceedance masks
    obs_lane: dict[int, np.ndarray]      # per-lane accumulations (mm)
    lats: np.ndarray
    lons: np.ndarray
    runs: dict[str, str]
    source: dict[str, Any]
    quantile: float
    present: np.ndarray = dataclasses.field(default_factory=lambda: np.ones(0, bool))
    store: Any = None

    @property
    def observed_dates(self) -> list[dt.date]:
        """Days with a real global granule: the days that carry evidence."""
        if self.present.size != len(self.dates):
            return list(self.dates)
        return [d for d, ok in zip(self.dates, self.present, strict=True) if ok]


def build_scan(obs: Observations, *, quantile: float = DEFAULT_QUANTILE,
               windows: Sequence[int] = WINDOWS,
               land: np.ndarray | None = None,
               store: Any = None) -> FloodScan:
    """Threshold every lane and build the union mask.

    The tracker is fed the CALENDAR-complete sequence, with days IMERG did not
    observe as NaN observations and an all-False mask. Two reasons, both about
    not lying:

    * a trailing sum is ``lag=N`` over whatever rows it is handed, so a compact
      "observed days only" stack would silently turn a 72 h accumulation into a
      4-day one whenever a day is missing. A gap must POISON the windows that
      contain it.
    * the tracker's core is "cells valid every day since the start", measured in
      CALENDAR days, so a gappy sequence would decay cores by arithmetic and an
      event would keep running through a day it knows nothing about. With the
      complete sequence an outage ENDS the open events, which is why no
      published interval can claim an unobserved day as flood evidence.
    """
    dates = obs.dates
    if not dates:
        raise ValueError("no observed days in the window")
    lats, lons = obs.grid
    daily = obs.daily                       # calendar-complete, NaN where absent
    if daily.shape[0] != len(dates):
        raise ValueError(f"{daily.shape[0]} daily rows for {len(dates)} days")
    if land is None:
        land = land_mask(lats, lons)
    if land.shape != daily.shape[1:]:
        raise ValueError(f"land mask {land.shape} does not match {daily.shape[1:]}")
    mask = np.zeros(daily.shape, bool)
    mask_lane: dict[int, np.ndarray] = {}
    obs_lane: dict[int, np.ndarray] = {}
    for w in windows:
        acc = daily if w == 1 else trailing_accumulation(daily, w)
        thr = precip_clim.precip_threshold_on(lats, lons, dates, quantile, w,
                                              store=store).values
        m = exceedance(acc, thr, FLOOR_MM[w]) & land
        obs_lane[int(w)] = acc
        mask_lane[int(w)] = m
        mask |= m
        logger.info("flood lane %dd: %d exceedant cell-days (floor %.0f mm, "
                    "%d threshold cells NaN)", w, int(m.sum()), FLOOR_MM[w],
                    int(np.isnan(thr).sum()))
    return FloodScan(dates=dates, mask=mask, mask_lane=mask_lane,
                     obs_lane=obs_lane, lats=lats, lons=lons, runs=obs.runs,
                     present=np.asarray(obs.present, bool), source=obs.source,
                     quantile=float(quantile), store=store)


def _max_run(mask: np.ndarray) -> int:
    """Longest run of consecutive True days in a (days, lat, lon) mask."""
    cur = np.zeros(mask.shape[1:], np.int32)
    best = np.zeros_like(cur)
    for day in mask:
        cur = (cur + 1) * day
        np.maximum(best, cur, out=best)
    return int(best.max()) if best.size else 0


def _box_columns(box: Box, nx: int) -> np.ndarray:
    """The box's columns as ONE continuous eastward run (wrapped boxes start at
    the seam), so a wrapped event is a contiguous slice of the array."""
    cols = np.flatnonzero(box.columns(nx))
    if box.j0 > box.j1:
        cols = np.concatenate([cols[cols >= box.j0], cols[cols <= box.j1]])
    return cols


def _footprint(scan: FloodScan, box: Box, days: slice, arr: np.ndarray) -> np.ndarray:
    """``arr`` cropped to the event's day range and box, columns re-ordered."""
    cols = _box_columns(box, scan.mask.shape[2])
    return arr[days, box.i0:box.i1 + 1][:, :, cols]


def events_from_scan(scan: FloodScan, *, params: Params | None = None,
                     cell_area: np.ndarray | None = None) -> list[Event]:
    """Track the exceedance masks and turn valid objects into ``Event``s."""
    p = params or FLOOD_PARAMS
    dates = scan.dates
    ny, nx = scan.mask.shape[1:]
    lats, lons = scan.lats, scan.lons
    if cell_area is None:
        from .temperature import _cell_area_km2

        cell_area = _cell_area_km2(lats, float(abs(lons[1] - lons[0])), nx)
    raw = track_events(scan.mask, dates, np.asarray(cell_area, float), p, FLOOD_TYPE)
    logger.info("flood: %d valid events from %d exceedant cell-days",
                len(raw), int(scan.mask.sum()))
    clim = precip_clim.climatology_meta(scan.store)
    present = (scan.present if scan.present.size == len(dates)
               else np.ones(len(dates), bool))
    out: list[Event] = []
    for ev in raw:
        # the day indices, not calendar arithmetic: the tracked sequence skips
        # days IMERG did not observe, so "one day before the tracker's end" can
        # be an unobserved day (and would mislabel the event's last wet day)
        i0 = dates.index(ev.start)
        i1 = (len(dates) - 1 if ev.end is None
              else bisect_left(dates, ev.end) - 1)
        if i1 < i0:
            continue
        last = dates[i1]
        start_dt = dt.datetime.combine(ev.start, dt.time())
        end_dt = dt.datetime.combine(last + dt.timedelta(days=1), dt.time())
        days = slice(i0, i1 + 1)
        active = _footprint(scan, ev.box, days, scan.mask)

        bbox = (float(min(lats[ev.box.i0], lats[ev.box.i1])),
                float(max(lats[ev.box.i0], lats[ev.box.i1])),
                normalize_lon_180(float(lons[ev.box.j0])),
                normalize_lon_180(float(lons[ev.box.j1])))

        peak = {1: np.nan, 3: np.nan}
        excess = {1: np.nan, 3: np.nan}
        peak_cell: tuple[float, float] | None = None
        cols = _box_columns(ev.box, nx)
        box_lat = lats[ev.box.i0: ev.box.i1 + 1]
        for w in scan.obs_lane:
            if w not in (1, 3):
                continue
            v = _footprint(scan, ev.box, days, scan.obs_lane[w])
            live = active & np.isfinite(v)
            if not live.any():
                continue
            masked = np.where(live, v, -np.inf)
            peak[w] = float(v[live].max())
            # how far above its own percentile the peak ran: read the
            # threshold on the event's box only (a few zarr reads per event,
            # instead of holding two global threshold fields in memory)
            th = precip_clim.precip_threshold_on(box_lat, lons[cols], dates[days],
                                                 scan.quantile, w,
                                                 store=scan.store).values
            rel = live & np.isfinite(th) & (th > 0)
            if rel.any():
                with np.errstate(invalid="ignore", divide="ignore"):
                    excess[w] = float(np.nanmax(np.where(rel, v / th, np.nan)))
            if w == 3:
                k, i, j = np.unravel_index(int(np.argmax(masked)), v.shape)
                peak_cell = (float(lats[ev.box.i0 + i]), float(lons[cols[j]]))
        peak_value = peak[3] if np.isfinite(peak[3]) else peak[1]
        # Rain attribution. The 72 h lane flags the day an extreme accumulation
        # ENDS, so the rain that caused a flood can have fallen up to two days
        # before the flagged day - and when the day-of-year percentile rises
        # across those days, the exceedance can appear only after the rain
        # stopped. Reporting only the in-window daily peak would then advertise
        # a "flood" with 0 mm of rain in it, so name the rain that did it.
        max_w = max(scan.obs_lane) if scan.obs_lane else 1
        cells = active.any(axis=0)
        stat0 = max(0, i0 - (max_w - 1))
        days_stat = slice(stat0, i1 + 1)
        live_stat = np.broadcast_to(cells, (days_stat.stop - days_stat.start,) + cells.shape)
        v1 = _footprint(scan, ev.box, days_stat, scan.obs_lane[1])
        rain_peak, rain_day = np.nan, None
        # -inf, not NaN, for "no value": argmax happily returns a NaN position
        stat = live_stat & np.isfinite(v1)
        if stat.any():
            r = np.where(stat, v1, -np.inf)
            k, i, j = np.unravel_index(int(np.argmax(r)), r.shape)
            rain_peak = float(r[k, i, j])
            rain_day = dates[stat0 + k]
        lanes = {}
        for w, m in scan.mask_lane.items():
            sub = _footprint(scan, ev.box, days, m)
            lanes["daily" if w == 1 else "accum_3d"] = int(sub.sum())
        day_runs = {scan.runs.get(f"{d:%Y-%m-%d}", "unknown") for d in dates[days]}
        unsettled = sorted(day_runs - {SETTLED_RUN})
        src_kind = (scan.source or {}).get("kind", "imerg")
        # calendar days inside the event window that IMERG did not observe. The
        # tracker ends an event on an unobserved day, so this is a TRIPWIRE: a
        # non-empty list would mean an interval is claiming a day it has no
        # evidence for (see build_scan).
        window_days = [ev.start + dt.timedelta(days=k)
                       for k in range((end_dt.date() - ev.start).days)]
        gaps = [f"{d:%Y-%m-%d}" for k, d in enumerate(window_days)
                if not present[i0 + k]]
        details: dict[str, Any] = {
            "quantile": scan.quantile,
            "accumulation_days": sorted(scan.mask_lane),
            "floor_mm": FLOOR_MM,
            "peak_value_units": PEAK_VALUE_UNITS,
            "peak_3day_mm": round(peak[3], 2) if np.isfinite(peak[3]) else None,
            "peak_daily_mm": round(peak[1], 2) if np.isfinite(peak[1]) else None,
            "rain_peak_daily_mm": round(rain_peak, 2) if np.isfinite(rain_peak) else None,
            "rain_peak_day": None if rain_day is None else f"{rain_day:%Y-%m-%d}",
            "peak_excess_ratio": round(float(np.nanmax([excess[1], excess[3]])), 3)
            if np.isfinite([excess[1], excess[3]]).any() else None,
            "peak_excess_ratio_daily": round(excess[1], 3) if np.isfinite(excess[1]) else None,
            "peak_excess_ratio_3day": round(excess[3], 3) if np.isfinite(excess[3]) else None,
            "peak_cell_lat": None if peak_cell is None else round(peak_cell[0], 3),
            "peak_cell_lon": None if peak_cell is None else round(peak_cell[1], 3),
            "lanes": lanes,
            "lane_of_record": ("3day" if lanes.get("accum_3d") else "1day"),
            "max_consecutive_days": _max_run(active),
            "peak_area_km2": round(float(ev.peak_area_km2), 1),
            "duration_days": ev.duration_days,
            "observed_days": int(days.stop - days.start),
            "unobserved_days": gaps,
            "qualified_date": ev.qualified.isoformat() if ev.qualified else None,
            "ongoing": ev.end is None,
            "footprint_area_km2": [round(a, 1) for a in ev.footprint_area_km2],
            "box_area_km2": [round(a, 1) for a in ev.box_area_km2],
            "core_area_km2": [round(a, 1) for a in ev.core_area_km2],
            "anchor_box": None if ev.anchor is None else [
                float(lats[ev.anchor.i0]), float(lats[ev.anchor.i1]),
                normalize_lon_180(float(lons[ev.anchor.j0])),
                normalize_lon_180(float(lons[ev.anchor.j1]))],
            "tracker_params": dataclasses.asdict(p),
            "antimeridian_wrap": bbox[2] > bbox[3],
            "detection": DETECTION_TEXT,
            "climatology": {k: clim.get(k) for k in
                            ("source", "path", "percentiles", "reference_period",
                             "estimator", "recommended_floor_mm")},
            "climatology_note": (
                "thresholds are ERA5 percentiles on the ERA5 0.25 deg grid; "
                + ("PARDIG" if src_kind == "pardig" else "IMERG")
                + " is averaged UP to that grid (never the reverse) and no "
                  "ERA5<->satellite bias correction is applied, so an exceedance "
                  "means '"
                + ("PARDIG" if src_kind == "pardig" else "IMERG")
                + " measured more rain than ERA5's p95 for this cell "
                  "and date of year' — which is why the absolute floor is "
                  "mandatory (precip_clim.FLOOR_MM)."),
            "land_mask": "natural_earth_110 (ewb_live/viz/build.land_mask)",
            "data_source": scan.source,
            "provisional": bool(unsettled),
            "provisional_reason": (
                None if not unsettled else (
                    "AI-estimated precipitation (Google PARDIG-NRT, CC BY 4.0) — an "
                    "experimental model product, not a science-grade observation; "
                    "frames are not retroactively settled"
                    if src_kind == "pardig" else
                    "built on the IMERG " + "/".join(unsettled) + " run; the Final "
                    "(science-grade) run is not published for these days yet")),
        }
        if dates[days][0] == dates[0]:
            details["window_start_truncated"] = True
        out.append(Event(
            id=make_event_id(FLOOD_TYPE, start_dt, bbox),
            event_type=FLOOD_TYPE,
            title=f"Flood {ev.start} to {last}",
            start=start_dt,
            end=end_dt,
            bbox=bbox,
            peak_value=round(float(peak_value), 2)
            if np.isfinite(peak_value) else None,
            details=details,
        ))
    out.sort(key=lambda e: (e.start, e.id))
    return out


#: Outcome of the last :func:`scan_flood_events` (same shape as the mlc lane's,
#: published as ``meta.lane_status.flood``): ``ok`` | ``no-data`` | ``failed``.
LANE_STATUS: dict[str, Any] = {}


def lane_status() -> dict:
    return dict(LANE_STATUS)


def _set_lane_status(status: str, *, reason: str | None = None,
                     window: tuple | None = None, source: str | None = None,
                     **counts) -> None:
    LANE_STATUS.clear()
    LANE_STATUS.update({
        "lane": "flood", "status": status, "reason": reason,
        "source": resolve_source(source),
        "window": [window[0].isoformat(), window[1].isoformat()] if window else None,
        **counts,
    })


def scan_flood_events(start: dt.date, end: dt.date, *,
                      quantile: float = DEFAULT_QUANTILE,
                      windows: Sequence[int] = WINDOWS,
                      params: Params | None = None,
                      root: Any = None, store: Any = None,
                      land: np.ndarray | None = None,
                      chunk_days: int = 10,
                      src_deg: float = float(imerg.IMERG_GRID_DEG),
                      dst_deg: float = ERA5_DEG,
                      source: str | None = None) -> list[Event]:
    """Detect floods overlapping [start, end] (inclusive UTC days).

    The window is the rain store (~90 days; PARDIG by default, IMERG via
    ``source="imerg"``). Unlike the ERA5 lanes this scan is NOT clamped to the
    ERA5 truth cutoff: the observations here are near-real-time (PARDIG ~2-8 h,
    IMERG Late ~2.5 h-months), and the newest days are where a flood lives.
    """
    try:
        obs = load_observations(start - dt.timedelta(days=TRACKED_LOOKBACK_DAYS), end,
                                root=root, chunk_days=chunk_days,
                                src_deg=src_deg, dst_deg=dst_deg, source=source)
    except pardig.PardigError as exc:
        # a fresh tree has no cached rain days: degrade to an honest lane
        # status instead of aborting the whole detect (it took TC/MLC down)
        logger.warning("flood: no rain data (%s) — lane reports no-data", exc)
        _set_lane_status("no-data", window=(start, end), source=source,
                         reason=f"the rain store has no data for "
                                f"{start:%Y-%m-%d}..{end:%Y-%m-%d} ({exc})")
        return []
    except Exception as exc:  # noqa: BLE001 - fail soft, like the mlc lane
        logger.warning("flood: rain read failed (%s) — no events", exc)
        _set_lane_status("failed", window=(start, end), source=source,
                         reason=f"rain read failed: {exc}")
        return []
    scan = build_scan(obs, quantile=quantile, windows=windows, land=land,
                      store=store)
    events = events_from_scan(scan, params=params)
    kept = [e for e in events
            if e.start.date() <= end and (e.end - dt.timedelta(days=1)).date() >= start]
    logger.info("flood %s..%s: %d events (%d before the requested window)",
                start, end, len(kept), len(events) - len(kept))
    _set_lane_status("ok", window=(start, end), source=source, n_events=len(kept))
    return kept


# ---------------------------------------------------------------------------
# reporting for the real run (streamed; nothing persisted unless --json)
# ---------------------------------------------------------------------------
def daily_summary(obs: Observations, scan: FloodScan) -> list[dict[str, Any]]:
    """Per-day exceedance cells / area / peak, for the run report."""
    from .temperature import _cell_area_km2

    lats, lons = scan.lats, scan.lons
    area = _cell_area_km2(lats, float(abs(lons[1] - lons[0])), lons.size)
    rows = []
    present = np.asarray(obs.present, bool)
    for k, d in enumerate(scan.dates):
        m = scan.mask[k]
        row: dict[str, Any] = {"date": f"{d:%Y-%m-%d}", "cells": int(m.sum()),
                               "observed": bool(present[k]) if present.size > k else True,
                               "area_km2": round(float(area[m].sum()), 0),
                               "run": obs.runs.get(f"{d:%Y-%m-%d}", "unknown")}
        for w, key in ((1, "peak_daily_mm"), (3, "peak_3day_mm")):
            a = scan.obs_lane.get(int(w))
            row[key] = (round(float(np.nanmax(a[k])), 1)
                        if a is not None and np.isfinite(a[k]).any() else None)
        rows.append(row)
    return rows


def _fmt_event(e: Event) -> str:
    d = e.details
    return (f"{e.start:%Y-%m-%d}..{(e.end - dt.timedelta(days=1)):%Y-%m-%d} "
            f"({d['duration_days']}d) bbox "
            f"[{e.bbox[0]:.1f},{e.bbox[1]:.1f}]x[{e.bbox[2]:.1f},{e.bbox[3]:.1f}] "
            f"area {d['peak_area_km2']:,.0f} km2  peak3d {d['peak_3day_mm']} mm "
            f"peak1d {d['peak_daily_mm']} mm  lanes {d['lanes']}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="flood event scan (IMERG x ERA5)")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    ap.add_argument("--quantile", type=float, default=DEFAULT_QUANTILE)
    ap.add_argument("--json", type=str, default=None,
                    help="write the event list here (default: report only)")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    end = a.end or dt.date.today()
    start = end - dt.timedelta(days=a.days - 1)
    import time

    t0 = time.monotonic()
    obs = load_observations(start - dt.timedelta(days=TRACKED_LOOKBACK_DAYS), end)
    scan = build_scan(obs, quantile=a.quantile)
    events = events_from_scan(scan)
    peak_rss = 0.0
    try:
        import resource

        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:  # noqa: BLE001
        pass
    print(f"\nwindow {start}..{end}: {len(events)} flood events in "
          f"{time.monotonic() - t0:.1f}s, peak RSS {peak_rss:.0f} MB")
    print(f"source: {json.dumps(scan.source)}")
    print("\nper-day exceedance summary (only days with any exceedance):")
    for row in daily_summary(obs, scan):
        if row["cells"]:
            print("  " + json.dumps(row))
    print(f"\ntop {a.top} by peak 72 h accumulation:")
    ranked = sorted(events, key=lambda e: (e.details.get("peak_3day_mm") or 0.0),
                    reverse=True)
    for e in ranked[:a.top]:
        print(f"  {_fmt_event(e)}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump([e.to_dict() for e in events], f, indent=1)
        print(f"\nwrote {len(events)} events to {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
