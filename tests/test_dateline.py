"""Canonical antimeridian convention (SPEC §2, owner 2026-11-08): bboxes
with lon_min > lon_max are eastward arcs across ±180, never ~360 deg wide.
These fixtures pin every consumer in the ewb lane: source subsetting,
curation overlap/union, EWB case region, validation, and padded bbox."""

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from ewb_live.events import select
from ewb_live.events.types import Event
from ewb_live.events.validate import validate_event
from ewb_live.sources import _transform

AR180 = (40.0, 50.0, 170.0, -170.0)   # 20 deg wide across the dateline


def _ev(bbox, start="2026-09-09", days=3, etype="atmospheric_river"):
    s = dt.datetime.fromisoformat(start)
    return Event(id=str(abs(hash(str(bbox) + start)))[-12:], event_type=etype,
                 title="t", start=s, end=s + dt.timedelta(days=days), bbox=bbox,
                 peak_value=500.0,
                 details={"sev": 1.0, "landfall_days": 2, "peak_land_pixels": 10,
                          "min_mask_gridpoints": 10, "ivt_threshold_kg_m_s": 250.0,
                          "centroid_lat": 45.0, "max_consecutive_days": 2,
                          "peak_area_km2": 1e5,
                          "antimeridian_wrap": bbox[2] > bbox[3]})


# ----------------------------------------------------------- source slicing
@pytest.mark.parametrize("grid", [np.arange(-180, 180, 1.0), np.arange(0, 360, 1.0)])
def test_subset_bbox_arc_across_180(grid):
    da = xr.DataArray(np.arange(float(len(grid))).reshape(1, -1),
                      coords={"latitude": np.array([45.0]), "longitude": grid},
                      dims=["latitude", "longitude"])
    out = _transform.subset_latlon(_transform.normalize_longitude(da), AR180)
    lons = set(out.longitude.values.tolist())
    core = {float(v) for v in list(range(170, 180)) + list(range(-179, -169))}
    seam = lons - core          # the ±180 seam point belongs to the arc
    assert seam and seam <= {-180.0, 180.0}
    assert lons == core | seam
    assert 100.0 not in lons and -100.0 not in lons


# ----------------------------------------------------------------- overlap
def test_overlap_pair_across_180():
    a = _ev(AR180)
    b = _ev((45.0, 55.0, -175.0, -160.0))       # shares -175..-170 with a
    c = _ev((45.0, 55.0, -140.0, -130.0))       # far away in lon
    assert select._bbox_intersects(a.bbox, b.bbox)
    assert select._overlaps(a, b)
    assert not select._overlaps(a, c)


def test_heat_union_across_180_never_inflates():
    core = _ev((0.0, 10.0, 175.0, -175.0), etype="heat_wave")      # arc 175..185
    west = _ev((0.0, 10.0, 170.0, -178.0), start="2026-09-11",     # arc 170..182
               etype="heat_wave")
    assert select._overlaps(core, west)          # arcs share 175..182
    u = select._bbox_union([core.bbox, west.bbox])
    span = (u[3] - u[2]) % 360.0
    assert u[2] == 170.0 and span == pytest.approx(15.0)   # NOT ~345/360 deg


# ------------------------------------------------------------- EWB case region
def test_ewb_case_region_covers_correct_longitudes():
    from extremeweatherbench.regions import BoundingBoxRegion
    loc = _ev(AR180).to_ewb_case()["location"]["parameters"]
    reg = BoundingBoxRegion(longitude_min=loc["longitude_min"],
                            longitude_max=loc["longitude_max"],
                            latitude_min=loc["latitude_min"],
                            latitude_max=loc["latitude_max"])
    for grid in (np.arange(-180, 180, 1.0), np.arange(0, 360, 1.0)):
        ds = xr.Dataset({"t": (("latitude", "longitude"),
                               np.zeros((2, len(grid))))},
                        coords={"latitude": [44.0, 46.0], "longitude": grid})
        m = reg.mask(ds, drop=True)
        # true arc = 170..180 + -180..-170 inclusive = 21 cells; the
        # naive (wrong) read of the bbox would select ~340 cells
        assert m.sizes["longitude"] == 21


# ---------------------------------------------------------- validate + padded
def test_validate_accepts_wrap_and_padded_preserves_it():
    e = _ev(AR180)
    e.title = ("Atmospheric River 2026-09-09 to 2026-09-11")
    assert validate_event(e) == []
    p = e.padded(5.0)
    assert p.bbox[2] == 165.0 and p.bbox[3] == -165.0   # still a wrap, arc 30 deg
    assert (p.bbox[3] - p.bbox[2]) % 360.0 == pytest.approx(30.0)


def test_truth_crop_normalizes_to_canonical_convention():
    """G-34: _crop_bbox must deliver [-180,180] sorted longitudes regardless of
    the store's native convention, so EWB grid-alignment matches physical
    cells for western & dateline-crossing cases (was: native 0-360 labels vs
    the forecast's -180..180 -> edge-extrapolated alignment -> CSI=0)."""
    import types
    import numpy as np
    import xarray as xr
    from ewb_live.scoring.truth import _crop_bbox

    lons = np.arange(0.0, 360.0, 5.0)
    lats = np.arange(-90.0, 90.5, 5.0)
    data = xr.Dataset(
        {"t2m": (("latitude", "longitude"),
                 np.arange(len(lats) * len(lons), dtype=float).reshape(len(lats), len(lons)))},
        coords={"latitude": lats, "longitude": lons},
    )
    # western case (Chile-ish)
    loc_w = types.SimpleNamespace(latitude_min=-40.0, latitude_max=-10.0,
                                  longitude_min=-80.0, longitude_max=-60.0)
    cw = _crop_bbox(data, loc_w, margin=2.0)
    lon_w = cw.longitude.values
    assert lon_w.min() >= -180.0 and lon_w.max() <= 180.0
    assert np.all(np.diff(lon_w) > 0)
    # same physical cells as the native-grid selection: 280..300 -> -80..-60
    assert abs(lon_w.min() - (-80.0)) < 1e-6 and abs(lon_w.max() - (-60.0)) < 1e-6

    # dateline-crossing case (170E..170W)
    loc_x = types.SimpleNamespace(latitude_min=30.0, latitude_max=60.0,
                                  longitude_min=170.0, longitude_max=-170.0)
    cx = _crop_bbox(data, loc_x, margin=2.0)
    lon_x = cx.longitude.values
    assert lon_x.min() >= -180.0 and lon_x.max() <= 180.0
    assert np.all(np.diff(lon_x) > 0)  # sorted; lattice may be two blocks
    # covers 172.5..180 and -180..-172.5 physical cells (5-deg grid + 2 margin)
    assert any(v >= 172.0 for v in lon_x) and any(v <= -172.0 for v in lon_x)


def test_remote_arco_target_normalizes_after_subset():
    """H4 (8fe8ae212d73): the REMOTE ARCO branch is un-cropped, so the
    canonicalization must run in the target wrapper itself — 0-360 truth
    labels vs the forecast's -180..180 reproduce the G-34 zero-overlap."""
    import dataclasses
    import numpy as np
    import xarray as xr
    from ewb_live.scoring.truth import _normalized_target

    @dataclasses.dataclass
    class FakeTarget:
        source: str = "gs://fake"

        def subset_data_to_case(self, data, case_metadata, **kwargs):
            return data  # EWB's region mask stands in for the real subset

    lons = np.arange(0.0, 360.0, 5.0)
    data = xr.Dataset(
        {"ivt": (("longitude",), np.arange(len(lons), dtype=float))},
        coords={"latitude": 0.0, "longitude": lons},
    )
    wrapped = _normalized_target(FakeTarget())
    out = wrapped.subset_data_to_case(data, None)
    assert out.longitude.values.min() >= -180.0
    assert out.longitude.values.max() <= 180.0
    assert np.all(np.diff(out.longitude.values) > 0)
