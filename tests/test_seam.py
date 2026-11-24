"""seam.py: periodic labeling, minimal-arc bbox, seam-merge (owner 2026-11-09).

Both seams: 0 deg in 0..360 arrays (ERA5 ARCO scans) and 180 deg in
-180..180 arrays — the seam is the array edge in either convention.
"""
import numpy as np
import pandas as pd

from ewb_live.events.ar_filter import ar_filter_tracks, track_ar_components
from ewb_live.events.seam import (arc_bbox_180, label_periodic,
                                  mask_lat_span, merge_seam_objects,
                                  minimal_arc, pad_bbox_180, seam_columns)

T = np.array([np.datetime64("2026-10-03T00", "h")
              + np.timedelta64(6 * i, "h") for i in range(3)])
LATS = np.arange(-90.0, 91.0, 1.0)


def _lons(base):  # global 1-deg grid starting at base (0 or -180)
    return base + np.arange(0.0, 360.0, 1.0)


def _blob(m, i, lat0, lat1, lon_set, lons):
    ys = np.nonzero((LATS >= lat0) & (LATS <= lat1))[0]
    xs = np.flatnonzero(np.isin(lons, lon_set))
    m[i][np.ix_(ys, xs)] = True


def test_ar_spanning_350E_to_20E_is_one_component_one_track_360_grid():
    lons = _lons(0.0)
    m = np.zeros((3, LATS.size, lons.size), bool)
    span = list(range(350, 360)) + list(range(0, 21))  # 350E..20E
    for i in range(3):
        _blob(m, i, 25, 35, np.array(span), lons)
    lab, n = label_periodic(m[0])
    assert n == 1                                    # ONE component
    assert lab.shape[1] == lons.size                 # no seam column added
    _labels, tracks = track_ar_components(m, T, LATS, lons)
    assert len(tracks) == 1 and tracks[0]["steps"] == [0, 1, 2]
    keep, dropped, _lab2, _t = ar_filter_tracks(m, LATS, lons, T, None)
    assert keep.any() and not dropped                # kept at ALL steps
    # bbox: two naive pieces stitch to the minimal arc, then SPEC sec 2
    pieces = [{"lat_min": 25.0, "lat_max": 35.0, "lon_min": 350.0,
               "lon_max": 359.0, "start": "2026-10-03", "end": "2026-10-05"},
              {"lat_min": 25.0, "lat_max": 35.0, "lon_min": 0.0,
               "lon_max": 20.0, "start": "2026-10-03", "end": "2026-10-04"}]
    merged = merge_seam_objects(pieces, lons)
    assert len(merged) == 1
    assert merged[0]["lon_min"] == 350.0 and merged[0]["lon_max"] == 20.0
    assert len(merged[0]["seam_merged_from"]) == 2  # curation gates on originals
    assert merged[0]["end"] == "2026-10-05"          # union window
    west, east = merged[0]["lon_min"], merged[0]["lon_max"]
    assert arc_bbox_180(west, east) == (-10.0, 20.0)  # correct -180..180 box
    cols = seam_columns(lons, west, east)
    assert cols.sum() == len(span)                   # no duplicate/dropped seam col


def test_diagonal_only_seam_touch_keeps_its_cells_and_joins_one_object():
    # left-edge cell at row r, right-edge cell only at row r+1 (8-connected
    # across the seam, NOT same-row): label_periodic read lab[r, -1] (0) and
    # merged the whole object into the background -> every cell unlabeled.
    m = np.zeros((12, 40), bool)
    m[5, 0:6] = True                 # east piece, touches col 0 at row 5
    m[6, 34:40] = True               # west piece, touches col -1 at row 6
    lab, n = label_periodic(m)
    assert n == 1
    assert (lab[m] > 0).all()        # no mask cell left unlabeled
    assert (lab[~m] == 0).all()
    # same-row touch (the case that always worked) is unchanged
    m2 = np.zeros((12, 40), bool)
    m2[5, 0:6] = True
    m2[5, 34:40] = True
    lab2, n2 = label_periodic(m2)
    assert n2 == 1 and (lab2[m2] > 0).all()
    # 4-connected callers (seam_rows=0, ar_mask) must NOT join the diagonal pair
    lab3, n3 = label_periodic(m, np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool),
                              seam_rows=0)
    assert n3 == 2 and (lab3[m] > 0).all()


def test_ar_spanning_170E_to_170W_one_component_pm180_grid():
    lons = _lons(-180.0)
    m = np.zeros((3, LATS.size, lons.size), bool)
    span = list(range(170, 180)) + list(range(-180, -170))
    for i in range(3):
        _blob(m, i, 25, 35, np.array(span), lons)
    _lab, n = label_periodic(m[0])
    assert n == 1
    _labels, tracks = track_ar_components(m, T, LATS, lons)
    assert len(tracks) == 1
    pieces = [{"lat_min": 25.0, "lat_max": 35.0, "lon_min": -180.0,
               "lon_max": -171.0, "start": "2026-10-03", "end": "2026-10-05"},
              {"lat_min": 25.0, "lat_max": 35.0, "lon_min": 170.0,
               "lon_max": 179.0, "start": "2026-10-03", "end": "2026-10-05"}]
    merged = merge_seam_objects(pieces, lons)
    assert len(merged) == 1
    west, east = merged[0]["lon_min"], merged[0]["lon_max"]
    assert (west, east) == (170.0, -171.0)  # arc ends at the piece's last cell
    assert arc_bbox_180(west, east) == (170.0, -171.0)  # lon_min > lon_max
    assert seam_columns(lons, west, east).sum() == 20


def test_minimal_arc_cases():
    assert minimal_arc([(350.0, 359.0), (0.0, 20.0)]) == (350.0, 20.0)
    # (10..20)+(30..40): widest EMPTY arc is 40->370 (330 deg), so the
    # minimal CONTAINING arc is 10 -> 40
    assert minimal_arc([(10.0, 20.0), (30.0, 40.0)]) == (10.0, 40.0)
    assert minimal_arc([(10.0, 20.0)]) == (10.0, 20.0)


def test_merge_requires_lat_overlap_and_date_overlap():
    lons = _lons(0.0)
    a = {"lat_min": 25.0, "lat_max": 35.0, "lon_min": 0.0, "lon_max": 20.0,
         "start": "2026-10-03", "end": "2026-10-05"}
    far = {"lat_min": -10.0, "lat_max": 0.0, "lon_min": 350.0,
           "lon_max": 359.0, "start": "2026-10-03", "end": "2026-10-05"}
    assert len(merge_seam_objects([a, far], lons)) == 2   # lat disjoint
    old = {"lat_min": 25.0, "lat_max": 35.0, "lon_min": 350.0,
           "lon_max": 359.0, "start": "2026-09-01", "end": "2026-09-02"}
    assert len(merge_seam_objects([a, old], lons)) == 2   # dates disjoint


def test_arc_bbox_180_returns_a_lon_pair_pad_bbox_180_a_full_bbox():
    """Arity contract between seam and ar.py's published-bbox path.

    `ar.scan_ar_events` unpacks both helpers inline, and a migration from
    pad_bbox_180 (4 values: lat_lo, lat_hi, lon_lo, lon_hi) to arc_bbox_180
    (2 values: the longitude pair) left a 4-target unpack in place, which
    raised `ValueError: not enough values to unpack (expected 4, got 2)` in the
    live weekly lane on 2026-11-10T23:09Z — detection died before any event was
    written. Nothing else exercises that call site offline, so pin the shapes.
    """
    assert arc_bbox_180(-10.0, 10.0, 5.0) == (-15.0, 15.0)
    assert arc_bbox_180(175.0, -175.0, 5.0) == (170.0, -170.0)   # wrap arc
    assert len(pad_bbox_180((-10.0, 10.0, -20.0, 20.0), 5.0, 5.0)) == 4


def test_mask_lat_span_is_latitude_axis_order_agnostic():
    """(lat_min, lat_max) must be south-then-north on ANY latitude axis.

    ERA5 ARCO / the local mirror — and the model archives — are DESCENDING
    (latitude 90..-90). mask_lat_span used to return (lats[rows[0]],
    lats[rows[-1]]), which on a descending axis is (north, south).
    events/ar.py publishes the AR-object extent as the event bbox, so every AR
    whose box came from that path was inverted and the publish gate dropped it
    ("bbox latitudes invalid: 56.25..41.0" — 9 of the 15 curated ARs on the
    2026-11-16 weekly, including the top-severity ones).
    """
    lats_up = np.arange(-90.0, 91.0, 1.0)
    lats_dn = lats_up[::-1]

    def band(lats):
        m = np.zeros((len(lats), 4), bool)
        m[(lats >= 41.0) & (lats <= 61.0), 1] = True
        return m

    assert mask_lat_span(band(lats_up), lats_up) == (41.0, 61.0)
    assert mask_lat_span(band(lats_dn), lats_dn) == (41.0, 61.0)

    # the way ar.py pads that extent into a published bbox: still a box
    lat_lo, lat_hi = mask_lat_span(band(lats_dn), lats_dn)
    bbox = (max(-90.0, lat_lo - 2.5), min(90.0, lat_hi + 2.5), 0.0, 10.0)
    assert bbox[0] < bbox[1]

    # a gapped mask reports the full span (min/max of the rows that hold cells)
    m = np.zeros((len(lats_dn), 4), bool)
    m[(lats_dn >= 70.0) & (lats_dn <= 75.0), 0] = True
    m[(lats_dn >= -20.0) & (lats_dn <= -10.0), 0] = True
    assert mask_lat_span(m, lats_dn) == (-20.0, 75.0)
    empty = np.zeros((len(lats_dn), 4), bool)
    assert mask_lat_span(empty, lats_dn) is None
