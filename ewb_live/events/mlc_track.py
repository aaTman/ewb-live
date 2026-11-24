"""Mid-latitude-cyclone track detection from gridded MSLP, in pure Python.

Structure is a port of **ExtremeWeatherBench's own** TC tracker
(`extremeweatherbench/events/tropical_cyclone.py`, v1.1.1 @ c6467f4), which the
owner picked as the reference method on 2026-11-17 ("similar methods as we did
for ewb's TC tracker" = upstream EWB, not our IBTrACS ``events/tc.py``). The
function map is deliberately one-to-one so the two can be read side by side:

===========================  =============================================
EWB tropical_cyclone.py      this module
===========================  =============================================
``_degrees_to_gridpoints``   ``_degrees_to_gridpoints``
``_find_peaks_for_time_slice``  ``find_peaks_for_time_slice``
  (``peak_local_max`` on        (``peak_local_max`` on ``-mslp``,
   masked ``-slp``)               lat-masked MSLP, same idea)
``find_contours_from_point_specified_field``  ``find_contours_from_point``
``find_valid_candidates``    ``find_valid_candidates``
``find_furthest_contour_from_point`` /        ``find_valid_contour_from_point``
  ``find_valid_contour_from_point``
``_process_single_init_time`` (per-time loop:  ``detect_nodes``
   peaks -> associate -> filter -> fill gaps)
active-track association     ``associate_nodes`` (same greedy nearest-peak
   (``max_spatial_distance``)    match per step, ``max_spatial_distance_degrees``)
``timestep_count_wind_minimum`` screen          ``min_duration_h`` screen
``_fill_track_gaps_from_fields``               ``fill_track_gaps_from_fields``
``generate_tc_tracks_by_init_time``            ``generate_mlc_tracks``
``_convert_detections_to_dataset``             ``tracks_to_dataset``
===========================  =============================================

What is **adapted** for extratropical lows, and why (all of it is a tropical
warm-core assumption or an upstream quirk, not a difference of opinion about
mid-latitude physics — the numbers that are shared with the TempestExtremes
PROOF recipe keep the PROOF values, `notes/mlc_algorithm.md`):

* **no analysis-track prior.** Upstream is a *forecast* tracker: it masks the
  field with IBTrACS-style best-track points (``_create_spatial_mask``) and
  only looks for peaks near a known storm. Here the event has to be *found*
  from the field alone, so the spatial mask is dropped and detection is
  global; the event-level screens that make a low publishable (genesis
  latitude band, orography, displacement, depth, tropical-origin rejection)
  live in ``ewb_live/events/mlc.py`` instead.
* **one contour, not two.** Upstream validates a peak against a closed SLP
  contour *and* a closed geopotential-thickness (warm-core) contour. The
  thickness criterion is exactly the tropical assumption we must not copy:
  an extratropical low is cold-core aloft, so requiring a negative-thickness
  contour would filter out the storms we are after. We keep the MSLP closed
  contour (the TempestExtremes ``--closedcontourcmd msl,3.0,6.0,0`` criterion
  the PROOF validated) with a great-circle distance cap, upstream-style.
* **no wind-speed gate.** Upstream requires >= 10 lead times with a
  neighbourhood peak wind >= 10 m/s. 10 m winds are cheap to read but were not
  part of the validated MLC recipe (ERA5 MSLP only, see mlc-research §5), and
  a wind floor is not how mid-latitude impact is defined here; depth, lifetime
  and displacement do that job. ``surface_wind_speed`` is therefore absent
  from the output; the slot-3 intensity of the published track is central MSL.
* **no 50 deg latitude cap.** Upstream's ``latitude_max_degrees=50`` is the
  tropical belt; here the node search keeps |lat| >= 5 deg (TE ``minabslat``)
  and the *event* gate is the extratropical band 25-70 deg (mlc.py).
* **great-circle contour distance.** Upstream's
  ``find_furthest_contour_from_point`` returns the furthest contour point and
  ``find_valid_contour_from_point`` converts grid indices to lat/lon through
  ``calc.convert_from_cartesian_to_latlon``; we work on the [-180, 180] grid
  directly and measure the same quantity (furthest contour point -> peak) with
  haversine in degrees, which also behaves correctly across the antimeridian.
* **grid seam.** Upstream's masks/peak finding treat the array as planar; the
  repo convention is a wrapping longitude, so the peak search runs on a
  one-cell-padded copy (``exclude_border=False`` then cannot merge two halves
  of the globe) and everything distance-related is great-circle.

Detection + stitching are pure (numpy/scipy/scikit-image in, `Track` objects
out, no I/O) so they unit-test on a synthetic field in milliseconds;
``ewb_live/events/mlc.py`` reads ERA5 MSLP and turns tracks into ``Event``s.

Conventions: latitude ascending, longitude ascending in [-180, 180), MSLP in
**hPa** (upstream works in Pa; hPa keeps the contour numbers in the same units
as the PROOF recipe), times naive UTC ``datetime`` on the 6-hourly grid. The
axes are checked against these conventions by :func:`check_grid` — a rotated or
non-uniform lattice used to be accepted silently and cost the lane every event
on real ARCO MSLP (``notes/mlc_fix.md``).
"""

from __future__ import annotations

import datetime as dt
from collections import namedtuple
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

import numpy as np
from scipy import spatial
from skimage import measure
from skimage.feature import peak_local_max

R_EARTH_KM = 6371.0
STEP_H = 6                       # ERA5 MSLP cadence this tracker assumes
Location = namedtuple("Location", ["latitude", "longitude"])


# ---------------------------------------------------------------- geometry
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (inputs broadcast elementwise)."""
    p1, p2 = np.deg2rad(np.asarray(lat1, float)), np.deg2rad(np.asarray(lat2, float))
    dp = p2 - p1
    dl = np.deg2rad(np.asarray(lon2, float) - np.asarray(lon1, float))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))


def haversine_deg(lat1, lon1, lat2, lon2):
    """Great-circle distance in degrees (what every threshold in this module uses).

    Upstream calls the same thing ``calc.haversine_distance(..., units="deg")``.
    """
    return haversine_km(lat1, lon1, lat2, lon2) / (R_EARTH_KM * np.pi / 180.0)


def unwrap_lons(lons) -> np.ndarray:
    """Longitudes made continuous along a track (each step within 180 deg).

    A low crossing the antimeridian has fixes like 179.6, -179.8; unwrapped
    they are 179.6, 180.2, so the first->last displacement is the ~0.4 deg it
    really is instead of a 359 deg jump.
    """
    out = np.asarray(lons, float).copy()
    for i in range(1, out.size):
        d = out[i] - out[i - 1]
        while d > 180.0:
            out[i] -= 360.0
            d = out[i] - out[i - 1]
        while d < -180.0:
            out[i] += 360.0
            d = out[i] - out[i - 1]
    return out


def _degrees_to_gridpoints(degrees: float, lat_coords: np.ndarray,
                           lon_coords: np.ndarray) -> int:
    """Great-circle degrees -> grid points (upstream ``_degrees_to_gridpoints``).

    Uses the mean of the lat/lon spacings as the representative resolution and
    rounds up, so the neighbourhood always spans at least ``degrees``.
    """
    lat_spacing = (float(np.mean(np.abs(np.diff(lat_coords))))
                   if len(lat_coords) > 1 else 1.0)
    lon_spacing = (float(np.mean(np.abs(np.diff(lon_coords))))
                   if len(lon_coords) > 1 else 1.0)
    return max(1, int(np.ceil(degrees / ((lat_spacing + lon_spacing) / 2.0))))


# ---------------------------------------------------------------- params
@dataclass(frozen=True)
class Params:
    """Detection + tracking settings (mlc lane; defaults = the PROOF recipe).

    Attributes:
        mslp_max_hPa: Candidate centres must be below this (`mslp_threshold`,
            upstream's ``surface_pressure_threshold`` = 1010 hPa). 1013 hPa is
            standard atmosphere: no extra screen on top of the PROOF recipe.
        min_abs_lat: No node equatorward of this |latitude| (TE ``minabslat
            5.0``; upstream's single-sided ``latitude_max_degrees`` is tropical).
        max_abs_lat: Polar cap excluded (ERA5 duplicates its last rows).
        delta_hPa: Closed-contour magnitude: the ``centre + delta`` isobar must
            close around the centre (TE ``--closedcontourcmd msl,3.0,...``,
            upstream ``slp_contour_magnitude=200 Pa``).
        max_gc_distance_contour_degrees: Max great-circle distance from the
            centre to that closing contour (TE's closed-contour radius 6.0,
            upstream ``max_gc_distance_slp_contour_degrees=5.5``).
        min_distance_between_peaks_degrees: Upstream's peak-separation, which
            doubles as TE's ``--mergedist 6.0``: two minima closer than this
            are one low, and ``peak_local_max`` keeps the deepest.
        max_spatial_distance_degrees: Max step-to-step distance for track
            association (TE ``--range 6``, upstream's default 5.0).
        max_gap_h: Max time between two consecutive fixes of one track
            (TE ``--maxgap 12h``).
        min_duration_h: Shortest track kept (TE ``--mintime 48h``, upstream
            ``timestep_count_wind_minimum`` expressed in hours).
        gap_fill_search_degrees: Radius around the interpolated position that
            gap filling may search (upstream ``search_radius_degrees=3.0``).
        gap_fill_candidates: Number of lowest-MSLP cells considered when
            filling a gap (upstream ``n_candidates=5``).
        fill_gaps: False keeps the raw detections (for validation against the
            TempestExtremes proof, which does not bridge).
    """

    mslp_max_hPa: float = 1013.0
    min_abs_lat: float = 5.0
    max_abs_lat: float = 80.0
    delta_hPa: float = 3.0
    max_gc_distance_contour_degrees: float = 6.0
    min_distance_between_peaks_degrees: float = 6.0
    max_spatial_distance_degrees: float = 6.0
    max_gap_h: float = 12.0
    min_duration_h: float = 48.0
    gap_fill_search_degrees: float = 3.0
    gap_fill_candidates: int = 5
    fill_gaps: bool = True


# ----------------------------------------------------------------- nodes
@dataclass(frozen=True)
class Node:
    """One validated low-pressure centre at one 6-hourly step."""

    t: dt.datetime
    lat: float
    lon: float          # deg E, [-180, 180)
    mslp: float         # hPa
    node_id: int = -1


@dataclass
class Track:
    """One stitched low track: 6-hourly fixes, ascending time."""

    times: list[dt.datetime]
    lat: np.ndarray
    lon: np.ndarray     # deg E, [-180, 180)
    mslp: np.ndarray    # hPa
    filled: np.ndarray | None = None     # True where a gap fill invented the fix
    track_id: int = -1

    def __len__(self) -> int:
        return len(self.times)

    @property
    def start(self) -> dt.datetime:
        return self.times[0]

    @property
    def end(self) -> dt.datetime:
        return self.times[-1]

    @property
    def duration_h(self) -> float:
        """Hours between the first and the last fix."""
        return (self.times[-1] - self.times[0]).total_seconds() / 3600.0

    @property
    def genesis(self) -> tuple[dt.datetime, float, float, float]:
        """(time, lat, lon, mslp) of the first fix."""
        return (self.times[0], float(self.lat[0]), float(self.lon[0]),
                float(self.mslp[0]))

    @property
    def min_mslp(self) -> float:
        return float(np.min(self.mslp))

    @property
    def argmin(self) -> int:
        return int(np.argmin(self.mslp))

    @property
    def displacement_km(self) -> float:
        """Great-circle first-fix -> last-fix distance."""
        lon = unwrap_lons(self.lon)
        return float(haversine_km(self.lat[0], lon[0], self.lat[-1], lon[-1]))

    @property
    def path_km(self) -> float:
        lon = unwrap_lons(self.lon)
        if len(self.lat) < 2:
            return 0.0
        return float(np.sum(haversine_km(self.lat[:-1], lon[:-1],
                                         self.lat[1:], lon[1:])))

    @property
    def crosses_antimeridian(self) -> bool:
        d = np.diff(np.asarray(self.lon, float))
        return bool(d.size and np.max(np.abs(d)) > 180.0)

    def pressure_fall_24h(self) -> np.ndarray:
        """Per-fix 24 h MSLP tendency (hPa; negative = deepening), NaN at the head."""
        m = np.asarray(self.mslp, float)
        out = np.full(m.size, np.nan)
        if m.size >= 5:
            out[4:] = m[4:] - m[:-4]
        return out

    def bomb_ratio(self) -> tuple[bool, float, float]:
        """(is_bomb, max 24 h fall, fall / threshold at that fix's latitude).

        Threshold = 24 hPa * sin|lat| / sin 60 deg (Sanders & Gyakum 1980).
        A tag, never a screen (notes/mlc_algorithm.md §4). Fixes without a full
        24 h of history are not considered (a 6-hour-old track is not a bomb
        just because nothing fell yet).
        """
        fall = self.pressure_fall_24h()
        ok = np.isfinite(fall)
        if not ok.any():
            return False, 0.0, 0.0
        thr = 24.0 * np.abs(np.sin(np.radians(np.asarray(self.lat, float)))) \
            / np.sin(np.radians(60.0))
        ratio = np.where(ok, fall / np.maximum(thr, 1e-9), np.inf)
        k = int(np.argmin(ratio))
        return bool(ratio[k] <= -1.0), float(fall[k]), float(-ratio[k])

    def pressure_24h_or_zero(self) -> np.ndarray:
        fall = self.pressure_fall_24h()
        return np.where(np.isfinite(fall), fall, 0.0)


# ------------------------------------------------- candidate detection
def grid_spacing(coords: np.ndarray) -> float:
    """Mean grid step of an ascending 1-D coordinate array (deg)."""
    c = np.asarray(coords, float)
    return float(np.mean(np.abs(np.diff(c)))) if c.size > 1 else 1.0


def check_grid(lats: np.ndarray, lons: np.ndarray, *, tol: float = 0.01) -> None:
    """Raise unless the lattice is the one this module's index math assumes.

    ``contour_to_latlon`` rebuilds a contour point's longitude as
    ``lons[0] + column * grid_spacing(lons)`` and ``_degrees_to_gridpoints``
    derives a cell size the same way, so both need a **strictly ascending,
    uniform** longitude axis. A rotated axis (a 0-360 grid shifted by +180
    without re-sorting is ``0 … 179.75, -180 … -0.25``) averages to twice the
    true step and silently inflates every measured contour radius — which is
    how the lane came to publish 0 events on real ARCO MSLP while its
    synthetic-field tests stayed green (``notes/mlc_fix.md``). Latitude may be
    either direction (``find_peaks_for_time_slice`` flips a 90N-first grid), but
    must be monotonic and uniform.
    """
    lat = np.asarray(lats, float)
    lon = np.asarray(lons, float)
    if lon.ndim != 1 or lat.ndim != 1:
        raise ValueError("mlc_track: lat/lon axes must be 1-D")
    if lon.size < 2 or lat.size < 2:
        raise ValueError("mlc_track: need at least 2 lat and 2 lon cells")
    dl = np.diff(lon)
    if np.any(dl <= 0):
        raise ValueError(
            f"mlc_track: longitude axis must be strictly ascending (got "
            f"{lon[0]:.2f}..{lon[-1]:.2f}, max|dlon|={np.max(np.abs(dl)):.2f}); "
            "a 0-360 grid must be shifted to [-180,180) AND re-sorted")
    for name, c, d in (("longitude", lon, dl), ("latitude", lat, np.diff(lat))):
        if name == "latitude" and not (np.all(d > 0) or np.all(d < 0)):
            raise ValueError("mlc_track: latitude axis must be monotonic")
        step = float(np.median(np.abs(d)))
        if step <= 0 or np.max(np.abs(d)) - np.min(np.abs(d)) > max(1e-6,
                                                                    tol * step):
            raise ValueError(f"mlc_track: {name} axis must be uniform "
                             f"(step {step:.4f}, spread "
                             f"{np.max(np.abs(d)) - np.min(np.abs(d)):.4f})")


def find_contours_from_point(field: np.ndarray, point: tuple[int, int], level: float,
                             halfwin: int, lon_halfwin: int) -> list[np.ndarray]:
    """Contours of ``field`` at ``level`` in the neighbourhood of ``point``.

    Upstream ``find_contours_from_point_specified_field`` calls
    ``measure.find_contours`` on the whole global field for every peak and then
    accepts *any* closed contour that happens to enclose the point; here the
    search is restricted to the ``2*halfwin x 2*lon_halfwin`` window around the
    peak (which is what the great-circle distance cap means anyway), so the
    contour tested is the one that belongs to this low. Longitude wraps,
    latitude is clamped.

    Returns contours in **unwrapped global index space** ``(row, col)``: the row
    is a global row, the column is a global column that may run past either edge
    (the window wraps the grid, so a window centred on lon = 179 has columns up
    to 179 + halfwin in a 360-degrees-of-column frame). ``contour_to_latlon``
    maps that back to degrees.
    """
    ny, nx = field.shape
    r, c = point
    r0, r1 = max(0, r - halfwin), min(ny, r + halfwin + 1)
    rows = np.arange(r0, r1)
    c0 = c - lon_halfwin
    cols = np.arange(c0, c + lon_halfwin + 1)
    sub = field[np.ix_(rows, cols % nx)]
    if not np.isfinite(sub).any():
        return []
    out = []
    for contour in measure.find_contours(sub, level=float(level),
                                         positive_orientation="high"):
        g = np.asarray(contour, float).copy()
        g[:, 0] += r0
        g[:, 1] += c0
        out.append(g)
    return out


def contour_to_latlon(contour: np.ndarray, lat_coords: np.ndarray,
                      lon_coords: np.ndarray) -> np.ndarray:
    """Unwrapped-index contour -> (lat, lon) degrees, longitudes in [-180, 180).

    Upstream does this with ``calc.convert_from_cartesian_to_latlon``; here the
    longitude is rebuilt from the grid step (so a contour that ran across the
    antimeridian keeps its position, modulo 360), and the haversine used
    downstream is periodic in longitude, so no seam special case is needed.
    Assumes the documented ascending lat/lon lattice.
    """
    lat = np.asarray(lat_coords, float)
    lon = np.asarray(lon_coords, float)
    rows = np.clip(contour[:, 0], 0.0, lat.size - 1.0)
    lats = np.interp(rows, np.arange(lat.size), lat)
    lons = lon[0] + contour[:, 1] * grid_spacing(lon)
    lons = (lons + 180.0) % 360.0 - 180.0
    return np.stack([lats, lons], axis=1)


def find_furthest_contour_from_point(contour_latlon: np.ndarray,
                                     point_latlon: tuple[float, float]):
    """Contour point furthest from ``point`` (upstream of the same name)."""
    c = np.asarray(contour_latlon, float)
    d = haversine_km(c[:, 0], c[:, 1], point_latlon[0], point_latlon[1])
    return c[int(np.argmax(d))]


def find_valid_contour_from_point(contour: np.ndarray, point: tuple[int, int],
                                  lat_coords: np.ndarray,
                                  lon_coords: np.ndarray) -> float:
    """Great-circle degrees from a peak to the furthest point of its contour.

    The same quantity as upstream ``find_valid_contour_from_point``, measured on
    the sphere instead of in array-index space (index space is not isotropic:
    a longitude cell is ``cos(lat)`` shorter than a latitude cell, which at 60 deg
    N would halve the apparent distance and let open contours pass as closed).
    """
    ll = contour_to_latlon(contour, lat_coords, lon_coords)
    lat = np.asarray(lat_coords, float)
    lon = np.asarray(lon_coords, float)
    peak = (float(lat[point[0]]), float(lon[point[1]]))
    fur = find_furthest_contour_from_point(ll, peak)
    return float(haversine_deg(fur[0], fur[1], peak[0], peak[1]))


def find_valid_candidates(slp_contours: Sequence[np.ndarray],
                          point: tuple[int, int],
                          latitude: np.ndarray, longitude: np.ndarray,
                          max_gc_distance_slp_contour: float = 6.0,
                          min_abs_lat: float = 5.0,
                          max_abs_lat: float = 80.0,
                          ) -> Location | None:
    """Is this peak a closed low? (upstream ``find_valid_candidates``).

    A candidate is valid when at least one of its SLP contours is closed
    (first point == last point), encloses the peak, and lies within
    ``max_gc_distance_slp_contour`` degrees of it, and its latitude is inside
    the search band. Upstream additionally demands a closed geopotential
    thickness contour — the warm-core (tropical) criterion, dropped here
    because an extratropical low is cold-core aloft.
    """
    lat_val = float(np.asarray(latitude, float)[point[0]])
    lon_val = float(np.asarray(longitude, float)[point[1] % len(longitude)])
    if not (min_abs_lat <= abs(lat_val) <= max_abs_lat):
        return None
    for contour in slp_contours:
        if len(contour) < 4:
            continue
        if not np.allclose(contour[0], contour[-1]):
            continue                                   # not a closed ring
        if not measure.points_in_poly([tuple(float(x) for x in point)], contour)[0]:
            continue                                   # does not enclose the peak
        d = find_valid_contour_from_point(contour, point, latitude, longitude)
        if d < max_gc_distance_slp_contour:
            return Location(latitude=lat_val, longitude=lon_val)
    return None


def find_peaks_for_time_slice(mslp_slice: np.ndarray, current_time: dt.datetime,
                              lat_coords: np.ndarray, lon_coords: np.ndarray,
                              p: Params = Params(),
                              ) -> list[Node]:
    """Validated low-pressure nodes on one ``(lat, lon)`` MSLP slice in hPa.

    Upstream ``_find_peaks_for_time_slice`` without the best-track spatial mask
    (this lane discovers lows instead of re-finding a known storm): mask the
    field by the pressure threshold and the latitude band, find separated local
    minima with ``peak_local_max`` on ``-mslp``, then keep the minima whose
    ``+delta`` isobar closes around them.
    """
    f = np.asarray(mslp_slice, float)
    ny, nx = f.shape
    if nx < 8 or ny < 8:
        return []
    lat = np.asarray(lat_coords, float)
    lon = np.asarray(lon_coords, float)
    check_grid(lat, lon)
    if lat[0] > lat[-1]:                                # descending latitude grid
        f = f[::-1]
        lat = lat[::-1]

    lat_mask = (np.abs(lat) >= p.min_abs_lat) & (np.abs(lat) <= p.max_abs_lat)
    mask = lat_mask[:, None] & np.isfinite(f) & (f < p.mslp_max_hPa)
    if not mask.any():
        return []

    min_distance = _degrees_to_gridpoints(p.min_distance_between_peaks_degrees, lat, lon)
    # Seam-safe peak search. ``peak_local_max`` is planar (upstream included),
    # so a minimum straddling the antimeridian would be reported as two half
    # peaks. Copy ``min_distance`` wrapped columns onto each edge so every real
    # column gets a full neighbourhood, search there, and report only the middle
    # (unrotated) copy. Masked cells go to -inf: peak_local_max would otherwise
    # report the +inf sentinel as a peak (a flat 1013 hPa field is all mask).
    padcols = int(min(nx // 2, min_distance + 1))
    neg = np.where(mask, -f, -np.inf)
    padded = np.concatenate([neg[:, -padcols:], neg, neg[:, :padcols]], axis=1)
    cand = peak_local_max(padded, min_distance=min_distance, exclude_border=False,
                          threshold_abs=-p.mslp_max_hPa)
    # Only the middle copy is reportable: the outer edge of a pad copy has a
    # clipped half-neighbourhood, where a field that deepens monotonically
    # toward a distant low always has its window maximum on the border cell --
    # those are fake minima a few hundred km from the seam.
    peaks = [(int(r), int(c) - padcols) for r, c in cand
             if padcols <= int(c) < padcols + nx]

    # TempestExtremes ``--mergedist``: two minima inside one search radius are
    # one low, and the DEEPEST of them is its centre. peak_local_max separates
    # by its *square* footprint only, which is 6 deg of latitude but cos(lat)*6
    # deg of longitude, so the great-circle merge is what enforces the recipe.
    if peaks:
        rows = np.array([r for r, _ in peaks], int)
        cols = np.array([c for _, c in peaks], int)
        plat, plon, pmsl = lat[rows], lon[cols], f[rows, cols]
        order = np.argsort(pmsl)                  # deepest (lowest MSLP) first
        keep_idx: list[int] = []
        kept_lat: list[float] = []
        kept_lon: list[float] = []
        for i in order:
            if kept_lat:
                d = haversine_deg(float(plat[i]), float(plon[i]),
                                  np.array(kept_lat), np.array(kept_lon))
                if np.any(d <= p.min_distance_between_peaks_degrees):
                    continue
            keep_idx.append(int(i))
            kept_lat.append(float(plat[i]))
            kept_lon.append(float(plon[i]))
        cand = [peaks[i] for i in sorted(keep_idx)]
    else:
        cand = []
    if not cand:
        return []

    # contour window: max_gc_distance of latitude, widened by 1/cos(lat) in lon
    dlat = grid_spacing(lat)
    halfwin = max(2, int(np.ceil(p.max_gc_distance_contour_degrees / dlat))) + 1
    nodes: list[Node] = []
    for (r, c) in cand:
        centre = float(f[r, c])
        coslat = max(0.2, float(np.cos(np.radians(lat[r]))))
        lhw = int(np.ceil(halfwin / coslat))
        contours = find_contours_from_point(f, (r, c), centre + p.delta_hPa,
                                            halfwin, lhw)
        loc = find_valid_candidates(contours, (r, c), lat, lon,
                                    max_gc_distance_slp_contour=p.max_gc_distance_contour_degrees,
                                    min_abs_lat=p.min_abs_lat, max_abs_lat=p.max_abs_lat)
        if loc is not None:
            nodes.append(Node(current_time, loc.latitude, loc.longitude, centre))
    return sorted(nodes, key=lambda n: n.mslp)


def detect_nodes(mslp: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                 times: Sequence[dt.datetime], p: Params = Params()) -> list[Node]:
    """Nodes on a ``(time, lat, lon)`` MSLP stack (upstream's per-time loop)."""
    mslp = np.asarray(mslp, float)
    if mslp.ndim != 3 or mslp.shape[1:] != (np.asarray(lats).size, np.asarray(lons).size):
        raise ValueError(f"mslp shape {mslp.shape} does not match "
                         f"{np.asarray(lats).size}x{np.asarray(lons).size}")
    nodes: list[Node] = []
    for k, t in enumerate(times):
        nodes += find_peaks_for_time_slice(mslp[k], t, lats, lons, p)
    for i, n in enumerate(nodes):
        nodes[i] = replace(n, node_id=i)
    return nodes


# ----------------------------------------------------------- association
def associate_nodes(nodes: Iterable[Node], *, max_spatial_distance_degrees: float = 6.0,
                    max_gap_h: float = 12.0, step_h: float = STEP_H,
                    ) -> list[Track]:
    """Greedy nearest-neighbour linking of nodes into tracks.

    Upstream associates, step by step, every active track with its *closest*
    unassigned peak within ``max_spatial_distance_degrees`` (older tracks pick
    first, one peak per track); we do the same and add one post-pass that
    bridges two link gaps in a row (TE ``--maxgap 12h``: a track may lose a
    step to a weak frame and still be the same low), because upstream has no
    gap tolerance — its lead times are always present.
    """
    nodes = sorted(nodes, key=lambda n: (n.t, n.mslp))
    if not nodes:
        return []
    by_t: dict[dt.datetime, list[Node]] = {}
    for n in nodes:
        by_t.setdefault(n.t, []).append(n)

    tracks: list[Track] = []
    tail: dict[int, Node] = {}          # track index -> last node
    last_t: dict[int, dt.datetime] = {}

    for t in sorted(by_t):
        peaks = list(by_t[t])
        free = list(range(len(peaks)))
        # active tracks, oldest first (upstream iterates active_tracks in insertion order)
        for ti in range(len(tracks)):
            if not free:
                break
            if (t - last_t[ti]).total_seconds() / 3600.0 > max_gap_h:
                continue
            last = tail[ti]
            best_i, best_d = None, float("inf")
            for i in free:
                d = float(haversine_deg(peaks[i].lat, peaks[i].lon, last.lat, last.lon))
                if d <= max_spatial_distance_degrees and d < best_d:
                    best_i, best_d = i, d
            if best_i is None:
                continue
            n = peaks[best_i]
            tracks[ti].times.append(n.t)
            tracks[ti].lat = np.append(tracks[ti].lat, n.lat)
            tracks[ti].lon = np.append(tracks[ti].lon, n.lon)
            tracks[ti].mslp = np.append(tracks[ti].mslp, n.mslp)
            tracks[ti].filled = np.append(tracks[ti].filled, False)
            tail[ti], last_t[ti] = n, t
            free.remove(best_i)
        for i in free:                  # unassigned peaks start new tracks
            n = peaks[i]
            tracks.append(Track([n.t], np.array([n.lat]), np.array([n.lon]),
                                np.array([n.mslp]), np.array([False])))
            tail[len(tracks) - 1], last_t[len(tracks) - 1] = n, t

    return _bridge_track_gaps(tracks, max_spatial_distance_degrees, max_gap_h)


def _bridge_track_gaps(tracks: list[Track], max_spatial_distance_degrees: float,
                       max_gap_h: float) -> list[Track]:
    """Join track pairs whose ends are within one max_gap of each other.

    Greedy, shortest join first, each track at most one outgoing and one
    incoming join (so a low is never split into two tracks *and* never welded
    twice). ``max_gap_h`` > one step is what lets two link gaps count as one.
    """
    ends = [t.times[-1] for t in tracks]
    starts = [t.times[0] for t in tracks]
    cands: list[tuple[float, int, int]] = []
    for a, ta in enumerate(tracks):
        for b, tb in enumerate(tracks):
            if a == b:
                continue
            gap_h = (starts[b] - ends[a]).total_seconds() / 3600.0
            if not 0.0 < gap_h <= max_gap_h:
                continue
            d = float(haversine_deg(ta.lat[-1], ta.lon[-1], tb.lat[0], tb.lon[0]))
            if d <= max_spatial_distance_degrees:
                cands.append((d, a, b))
    cands.sort()
    has_next: set[int] = set()
    has_prev: set[int] = set()
    nxt: dict[int, int] = {}
    for d, a, b in cands:
        if a in has_next or b in has_prev:
            continue
        has_next.add(a)
        has_prev.add(b)
        nxt[a] = b
    merged: list[Track] = []
    for i, t in enumerate(tracks):
        if i in has_prev:
            continue
        chain = [t]
        j = i
        while j in nxt:
            j = nxt[j]
            chain.append(tracks[j])
        merged.append(_concat(chain))
    return merged


def _concat(chain: list[Track]) -> Track:
    if len(chain) == 1:
        return chain[0]
    return Track(list(chain[0].times) + [t for tr in chain[1:] for t in tr.times],
                 np.concatenate([tr.lat for tr in chain]),
                 np.concatenate([tr.lon for tr in chain]),
                 np.concatenate([tr.mslp for tr in chain]),
                 np.concatenate([tr.filled for tr in chain]))


# ------------------------------------------------------------- gap fills
def fill_track_gaps_from_fields(track: Track, mslp_stack: np.ndarray,
                                times: Sequence[dt.datetime],
                                lat_coords: np.ndarray, lon_coords: np.ndarray,
                                p: Params = Params(),
                                ) -> Track:
    """Re-drop missing fixes from the MSLP field (upstream ``_fill_track_gaps_from_fields``).

    For every step between a track's first and last fix that has no detection,
    take the ``gap_fill_candidates`` lowest-MSLP cells within
    ``gap_fill_search_degrees`` of the interpolated position and keep the one
    closest to it; each fill becomes the anchor for the next, so a string of
    fills follows the low instead of oscillating about the chord.
    """
    times = list(times)
    t_index = {t: i for i, t in enumerate(times)}
    have = {t_index[t] for t in track.times if t in t_index}
    if not have:
        return track
    first, last = min(have), max(have)
    if last - first <= 1:
        return track
    lat = np.asarray(lat_coords, float)
    lon = np.asarray(lon_coords, float)
    stack = np.asarray(mslp_stack, float)
    # fix index -> time index for the fixes we already have (ascending)
    have_seq = [(t_index[t], k) for k, t in enumerate(track.times) if t in t_index]
    have_seq.sort()
    by_time = dict(have_seq)
    out_t: list[dt.datetime] = []
    out_lat: list[float] = []
    out_lon: list[float] = []
    out_msl: list[float] = []
    out_fill: list[bool] = []
    for i in range(first, last + 1):
        if i in by_time:
            k = by_time[i]
            out_t.append(track.times[k])
            out_lat.append(float(track.lat[k]))
            out_lon.append(float(track.lon[k]))
            out_msl.append(float(track.mslp[k]))
            out_fill.append(bool(track.filled[k]) if track.filled is not None else False)
            continue
        pa, ia = max(s for s in have_seq if s[0] < i)
        pb, ib = min(s for s in have_seq if s[0] > i)
        frac = (i - pa) / (pb - pa)
        exp_lat = track.lat[ia] + frac * (track.lat[ib] - track.lat[ia])
        # along the short arc: 178 -> -176 must pass through 180, not through 0
        step_lon = (track.lon[ib] - track.lon[ia] + 180.0) % 360.0 - 180.0
        exp_lon = track.lon[ia] + frac * step_lon
        dist = haversine_deg(lat[:, None], lon[None, :], exp_lat, exp_lon)
        mask = dist <= p.gap_fill_search_degrees
        n_valid = int(np.count_nonzero(mask))
        if n_valid == 0:
            continue                       # nothing to fill from: leave the gap
        slp_masked = np.where(mask, stack[i], np.inf)
        k = min(p.gap_fill_candidates, n_valid)
        flat = np.argpartition(slp_masked.ravel(), k)[:k]
        cr, cc = np.unravel_index(flat, slp_masked.shape)
        best = int(np.argmin(dist[cr, cc]))
        r, c = int(cr[best]), int(cc[best])
        out_t.append(times[i])
        out_lat.append(float(lat[r]))
        out_lon.append(float(lon[c]))
        out_msl.append(float(stack[i][r, c]))
        out_fill.append(True)
    return Track(out_t, np.array(out_lat), np.array(out_lon), np.array(out_msl),
                 np.array(out_fill, bool))


# ------------------------------------------------------------ top level
@dataclass
class TrackResult:
    """What ``generate_mlc_tracks`` found."""

    tracks: list[Track] = field(default_factory=list)
    n_nodes: int = 0
    n_frames: int = 0
    summary: dict = field(default_factory=dict)


def generate_mlc_tracks(mslp: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                        times: Sequence[dt.datetime],
                        p: Params = Params()) -> TrackResult:
    """Find mid-latitude low tracks in a ``(time, lat, lon)`` MSLP stack in hPa.

    Mirrors upstream ``generate_tc_tracks_by_init_time``: per-time peak
    detection with contour validation, greedy active-track association, a
    minimum-lifetime screen, then field-based gap filling of the surviving
    tracks (upstream fills gaps *before* the screen only because it needs the
    wind count of every filled point; our screen is on duration alone, so
    filling afterwards is the same thing for less work).

    Args:
        mslp: ``(len(times), len(lats), len(lons))`` mean sea level pressure, hPa.
        lats: ascending latitudes, deg N.
        lons: ascending longitudes in [-180, 180) (the caller shifts ERA5's
            0-360 grid; the tracker wraps longitude itself).
        times: naive UTC datetimes on the 6-hourly grid, ascending.
        p: :class:`Params`.

    Returns:
        :class:`TrackResult` with tracks ordered by start time.
    """
    times = list(times)
    nodes = detect_nodes(mslp, lats, lons, times, p)
    tracks = associate_nodes(nodes,
                             max_spatial_distance_degrees=p.max_spatial_distance_degrees,
                             max_gap_h=p.max_gap_h)
    kept = [t for t in tracks if t.duration_h >= p.min_duration_h - 1e-9]
    if p.fill_gaps:
        kept = [fill_track_gaps_from_fields(t, np.asarray(mslp, float), times,
                                            lats, lons, p) for t in kept]
    for i, t in enumerate(kept):
        t.track_id = i
    kept.sort(key=lambda t: (t.start, t.min_mslp))
    return TrackResult(tracks=kept, n_nodes=len(nodes), n_frames=len(times),
                       summary=tracks_summary(kept))


def tracks_summary(tracks: Sequence[Track]) -> dict:
    """Count/lifetime/pressure summary (validation + run logging)."""
    if not tracks:
        return {"n_tracks": 0}
    dur = np.array([t.duration_h for t in tracks])
    msl = np.array([t.min_mslp for t in tracks])
    disp = np.array([t.displacement_km for t in tracks])
    glat = np.array([abs(t.genesis[1]) for t in tracks])
    return {
        "n_tracks": len(tracks),
        "median_lifetime_h": float(np.median(dur)),
        "median_min_mslp_hPa": float(np.median(msl)),
        "deepest_mslp_hPa": float(msl.min()),
        "median_displacement_km": float(np.median(disp)),
        "n_displacement_ge_1000km": int((disp >= 1000.0).sum()),
        "n_bomb": int(sum(1 for t in tracks if t.bomb_ratio()[0])),
        "mean_abs_genesis_lat": float(np.mean(glat)),
    }


def tracks_to_dataframe(tracks: Sequence[Track]):
    """Fix-level ``pandas.DataFrame`` (time, lat, lon, mslp_hPa, track_id, filled)."""
    import pandas as pd

    rows = []
    for t in tracks:
        for k, ts in enumerate(t.times):
            rows.append((t.track_id, ts, float(t.lat[k]), float(t.lon[k]),
                         float(t.mslp[k]),
                         bool(t.filled[k]) if t.filled is not None and k < len(t.filled)
                         else False))
    return pd.DataFrame(rows, columns=["track_id", "time", "lat", "lon",
                                       "mslp_hPa", "gap_filled"])


def tracks_to_dataset(tracks: Sequence[Track]):
    """Fix-level ``xarray.Dataset`` on upstream's naming (track, time dims)."""
    import xarray as xr

    df = tracks_to_dataframe(tracks)
    if not len(df):
        return xr.Dataset({"air_pressure_at_mean_sea_level": (("track", "time"), np.full((0, 0), np.nan)),
                           "latitude": (("track", "time"), np.full((0, 0), np.nan)),
                           "longitude": (("track", "time"), np.full((0, 0), np.nan))})
    da = df.set_index(["track_id", "time"])
    n_track = max(len(tracks), 1)
    n_time = max(len(df.groupby("track_id").size()), 1)
    out = np.full((n_track, n_time), np.nan)
    lats = np.full((n_track, n_time), np.nan)
    lons = np.full((n_track, n_time), np.nan)
    times = [np.datetime64("NaT")] * n_time
    for ti, (tid, grp) in enumerate(df.groupby("track_id")):
        for k, (ts, la, lo, msl) in enumerate(zip(grp["time"], grp["lat"],
                                                  grp["lon"], grp["mslp_hPa"])):
            out[ti, k] = msl
            lats[ti, k] = la
            lons[ti, k] = lo
            times[k] = np.datetime64(ts)
    ds = xr.Dataset(
        {"air_pressure_at_mean_sea_level": (("track", "time"), out),
         "latitude": (("track", "time"), lats),
         "longitude": (("track", "time"), lons)},
        coords={"track": [t.track_id for t in tracks], "time": times},
    )
    del da
    return ds
