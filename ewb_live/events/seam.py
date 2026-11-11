"""Longitude seam handling (owner HIGH 2026-11-09).

ERA5 ARCO scans run on 0..360 grids: 0 deg is the ARRAY SEAM, so naive
component labeling splits an object crossing Greenwich into two pieces, and
min/max lon spans give the wrong (340-ish deg wide) bbox. The same class of
bug exists at ±180 for -180..180 arrays. The seam is always the array edge,
regardless of longitude convention, so everything here is defined in terms
of the longitude ARRAY, not the longitude values.

Canonical bbox = the minimal eastward arc containing the object; encoded per
SPEC sec 2 (longitudes normalized to [-180, 180]; arc crossing ±180 encoded
with lon_min > lon_max, meaning "from lon_min eastward across ±180").

Shared entry points (design's raw frames use these too):
  label_periodic  — seam-connected component labeling (mask, structure)
  minimal_arc     — (west, east) minimal arc from lon intervals; west > east
                    means the arc crosses the array's 0-deg seam
  mask_arc        — minimal arc of a (lat, lon) bool mask (per-row runs)
  mask_lat_span   — (lat_min, lat_max) of a (lat, lon) bool mask
  arc_bbox_180    — SPEC sec 2 (lon_min, lon_max) from an arc, optional pad
  pad_bbox_180    — grow a SPEC sec 2 bbox arc-safe (ends, not min/max)
  seam_columns    — boolean column mask for an arc bbox (wrap-aware)
  merge_seam_objects — stitch seam-split pieces of one object back together
"""
from __future__ import annotations

import numpy as np

from .types import normalize_lon_180

_STRUCT = np.ones((3, 3), int)


def label_periodic(mask2d: np.ndarray,
                   structure: np.ndarray | None = None,
                   *, seam_rows: int = 1) -> tuple[np.ndarray, int]:
    """ndimage.label with PERIODIC longitude: col nx-1 is adjacent to col 0.

    Labels normally, then UNIONs labels whose pixels touch across the seam
    in the same or a neighboring row (8-connectivity) and relabels densely.
    `seam_rows` = how many rows either side of a seam pixel also count as
    touching: 1 for 8-connectivity (default), 0 for a 4-connected structure
    (no diagonal neighbour across the seam; events/ar_mask.py).
    (Padding columns does NOT work: a copy adjacent to one piece never joins
    the other real piece, and creates phantom labels in the pad columns.)
    Returns (labels int, n) exactly like ndimage.label.
    """
    from scipy import ndimage

    m = np.asarray(mask2d)
    # `structure or _STRUCT` would raise on a multi-element array (ambiguous
    # truth value) — the sentinel has to be an identity check.
    res = ndimage.label(m, structure=_STRUCT if structure is None else structure)
    assert isinstance(res, tuple)  # scipy stub union
    lab, n = res
    if n < 2 or m.shape[1] < 2:
        return lab, int(n)
    parent = list(range(n + 1))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    left = np.flatnonzero(lab[:, 0])
    right = np.flatnonzero(lab[:, -1])
    for r in left:
        for r2 in right[np.abs(right.astype(int) - int(r)) <= seam_rows]:
            # the neighbour is at r2, not r: with seam_rows >= 1, r2 != r reads
            # background (0) at lab[r, -1] and unions the whole object INTO the
            # background — every cell of an AR touching the seam diagonally
            # lost its label (Europe 2026-09-08, 0 painted cells for 18 h)
            a, b = int(lab[r, 0]), int(lab[r2, -1])
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    roots = np.fromiter((find(x) for x in range(n + 1)), np.int64, n + 1)
    uniq, inv = np.unique(roots[lab], return_inverse=True)
    lab = inv.reshape(lab.shape).astype(np.int32)  # root 0 sorts first: bg stays 0
    return lab, int(len(uniq) - 1)


def minimal_arc(intervals: list[tuple[float, float]],
                period: float = 360.0) -> tuple[float, float]:
    """Minimal eastward arc (west, east) on the lon circle containing all
    given non-crossing intervals (w <= e). If the arc crosses the period
    origin (lon 0), the result has west > east (arc runs west -> period ->
    east). Contained set assumed not to cover the whole circle."""
    iv = sorted((float(w) % period, float(e) % period) for w, e in intervals)
    merged: list[list[float]] = []
    for w, e in iv:
        if merged and w <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([w, e])
    if len(merged) == 1:
        return merged[0][0], merged[0][1]
    # widest EMPTY arc between consecutive (and circularly last->first)
    # intervals; the containing arc is its complement
    gaps = [(w2 - e1, k + 1)
            for k, ((_, e1), (w2, _)) in enumerate(zip(merged, merged[1:]))]
    gaps.append((merged[0][0] + period - merged[-1][1], 0))
    _, k = max(gaps)
    return merged[k][0], merged[k - 1][1]


def arc_bbox_180(west: float, east: float,
                 pad_deg: float = 0.0) -> tuple[float, float]:
    """SPEC sec 2 bbox longitudes: normalize the arc ENDPOINTS (west end
    pushed pad westward, east end pad eastward) to [-180, 180]. An arc
    crossing ±180 comes out as lon_min > lon_max by construction."""
    return (normalize_lon_180(west - pad_deg), normalize_lon_180(east + pad_deg))


def arc_in_convention(west: float, east: float,
                      base: float) -> tuple[float, float]:
    """Re-express an eastward arc (any endpoint values) with both endpoints
    in the longitude convention starting at `base` (e.g. 0 for 0..360,
    -180 for -180..180). Wrap arcs come out as lon_min > lon_max — the one
    encoding seam_columns / BoundedRegion / subset_bbox understand."""
    west = (west - base) % 360.0 + base
    east = (west + (east - west) % 360.0 - base) % 360.0 + base
    return west, east


def seam_columns(lons: np.ndarray, lon_min: float,
                 lon_max: float) -> np.ndarray:
    """Boolean column mask for an arc bbox IN THE ARRAY'S OWN convention
    (wrap arc encoded lon_min > lon_max). Never duplicates or drops the
    seam column: the mask is per-column over lons, no slicing."""
    l = np.asarray(lons, float)
    if lon_min <= lon_max:
        return (l >= lon_min) & (l <= lon_max)
    return (l >= lon_min) | (l <= lon_max)


def _row_runs(row: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True stretches of a 1-D bool row as (start, end) INCLUSIVE
    index pairs — the pieces an arc bbox is built from."""
    d = np.diff(np.concatenate(([0], np.asarray(row, np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def mask_arc(mask2d: np.ndarray,
             lons: np.ndarray) -> tuple[float, float] | None:
    """Minimal eastward arc (west, east) covering every True cell of a mask,
    in the longitude ARRAY's own convention (west > east = it straddles the
    array seam). Built from per-row contiguous runs, so a mask that occupies
    both ends of the array collapses onto the short arc across the seam
    instead of the ~340 deg nonsense a naive min/max gives.

    None for an empty mask. Assumes the mask does not cover the whole globe
    (minimal_arc's own precondition).
    """
    m = np.asarray(mask2d, bool)
    l = np.asarray(lons, float)
    iv: list[tuple[float, float]] = []
    for j in range(m.shape[0]):
        row = m[j]
        if not row.any():
            continue
        for a, b in _row_runs(row):
            iv.append((float(l[a]), float(l[b])))
    if not iv:
        return None
    return minimal_arc(iv)


def mask_lat_span(mask2d: np.ndarray,
                  lats: np.ndarray) -> tuple[float, float] | None:
    """(lat_min, lat_max) of the True cells of a (lat, lon) mask, or None."""
    rows = np.flatnonzero(np.asarray(mask2d, bool).any(axis=1))
    if rows.size == 0:
        return None
    l = np.asarray(lats, float)
    return float(l[rows[0]]), float(l[rows[-1]])


def pad_bbox_180(bbox: tuple[float, float, float, float], dlat: float,
                 dlon: float) -> tuple[float, float, float, float]:
    """Grow a SPEC sec 2 bbox by dlat/dlon, arc-safe.

    Padding must push the arc's WEST end westward and its EAST end eastward,
    which for a wrap arc (lon_min > lon_max) is the opposite of min/max
    arithmetic; a box padded past a full revolution clamps to the whole
    globe instead of inverting into a narrow arc.
    """
    from .types import normalize_lon_180

    lat_min, lat_max, lon_min, lon_max = (float(b) for b in bbox)
    span = lon_max - lon_min if lon_max >= lon_min else lon_max + 360.0 - lon_min
    if span + 2.0 * dlon >= 360.0:
        return (max(-90.0, lat_min - dlat), min(90.0, lat_max + dlat),
                -180.0, 180.0)
    return (max(-90.0, lat_min - dlat), min(90.0, lat_max + dlat),
            normalize_lon_180(lon_min - dlon),
            normalize_lon_180(lon_max + dlon))


def _edge_tol(lons: np.ndarray) -> float:
    return float(np.median(np.diff(np.asarray(lons, float)))) / 2.0


def merge_seam_objects(objs: list[dict], lons: np.ndarray) -> list[dict]:
    """Stitch pieces of one object that a naive labeling split at the array
    seam. A pair merges when one piece is flush with the array's west edge
    (lon_min == lons[0]), the other flush with its east edge
    (lon_max == lons[-1]), their date windows overlap and their latitude
    ranges intersect. The merged object keeps the larger piece's fields;
    lon_min/lon_max become the minimal arc (lon_min > lon_max = wrap).
    Greedy, one merge per piece, best latitude overlap first.

    Requires objs with keys lat_min/lat_max/lon_min/lon_max/start/end."""
    lons = np.asarray(lons, float)
    tol = _edge_tol(lons)
    west_i = [i for i, o in enumerate(objs) if o["lon_min"] <= lons[0] + tol]
    east_i = [i for i, o in enumerate(objs) if o["lon_max"] >= lons[-1] - tol
              and o["lon_min"] > lons[0] + tol]
    used: set[int] = set()
    out: list[dict] = list(objs)
    for w in west_i:
        a = objs[w]
        best = None
        for e in east_i:
            if e in used or e == w:
                continue
            b = objs[e]
            lat_ov = min(a["lat_max"], b["lat_max"]) - max(a["lat_min"],
                                                           b["lat_min"])
            if lat_ov < -tol:
                continue
            if max(str(a["start"]), str(b["start"])) > min(str(a["end"]),
                                                           str(b["end"])):
                continue
            if best is None or lat_ov > best[1]:
                best = (e, lat_ov)
        if best is None:
            continue
        e = best[0]
        b = objs[e]
        big, small = (a, b) if a["lon_max"] - a["lon_min"] >= (
            b["lon_max"] - b["lon_min"]) else (b, a)
        west_arc, east_arc = minimal_arc(
            [(a["lon_min"], a["lon_max"]), (b["lon_min"], b["lon_max"])])
        west_arc, east_arc = arc_in_convention(west_arc, east_arc,
                                               float(lons[0]))
        merged = {**big,
                  "lat_min": min(a["lat_min"], b["lat_min"]),
                  "lat_max": max(a["lat_max"], b["lat_max"]),
                  "lon_min": west_arc, "lon_max": east_arc,
                  "start": min(str(a["start"]), str(b["start"])),
                  "end": max(str(a["end"]), str(b["end"])),
                  "seam_merged_pieces": 2,
                  # originals that must have survived curation (>=1), else a
                  # merge of two sub-floor pieces could publish (ponytail 02:16Z)
                  "seam_merged_from": [(str(p["start"]), p["lat_min"],
                                        p["lon_min"]) for p in (a, b)]}
        keep_i, drop_i = (w, e) if big is a else (e, w)
        out[keep_i] = merged
        used.add(w)
        used.add(e)
        out[drop_i] = None  # type: ignore[call-overload]
    return [o for o in out if o is not None]
