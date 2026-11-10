"""Offline unit tests for ewb_live.viz.build (no network, no heavy deps).

model_daily_pass must degrade to an 'unavailable' status — never raise —
when a model's forecast horizon cannot cover lead L + the whole event window
(the KeyError crash seen during the 2026-11-06 --all backfill).
"""
import base64
import datetime as dt
import math

import numpy as np
import pytest
import xarray as array

from ewb_live.sources import api
from ewb_live.viz import build

LATS = np.array([10.0, 11.0, 12.0])
LONS = np.array([-20.0, -19.0, -18.0, -17.0])
MAX_MODEL_LEAD_H = 240


def _fake_open_forecast(model, init, variables, lead_hours=None, bbox=None, **kw):
    """Dataset on the requested lead grid, capped at the model's horizon."""
    leads = [h for h in (lead_hours or []) if h <= MAX_MODEL_LEAD_H]
    if not leads:
        # realistic source behaviour: nothing on the requested grid
        raise ValueError("no leads available")
    shape = (1, 1, len(leads), len(LATS), len(LONS))
    da = array.DataArray(
        np.full(shape, 300.0, dtype=np.float32),
        dims=("init_time", "height", "lead_time", "latitude", "longitude"),
        coords={
            "init_time": [np.datetime64(init.replace(tzinfo=None))],
            "height": [0],
            "lead_time": [np.timedelta64(h, "h") for h in leads],
            "latitude": LATS,
            "longitude": LONS,
        },
    ).squeeze("height")
    return array.Dataset({"surface_air_temperature": da})


@pytest.fixture()
def stub_sources(monkeypatch):
    monkeypatch.setattr(api, "open_forecast", _fake_open_forecast)


def test_horizon_shortfall_is_recorded_not_raised(stub_sources):
    day0 = dt.date(2026, 10, 1)
    # lead 240 h + a 1-day window needs up to 258 h: beyond the stub horizon.
    steps, status = build.model_daily_pass("graphcast", day0, 1, 240, None,
                                           (10, 12, -20, -17), LATS, LONS)
    assert steps is None
    assert "beyond horizon" in status and "258" in status and "240" in status


def test_full_coverage_returns_ok(stub_sources):
    day0 = dt.date(2026, 10, 1)
    steps, status = build.model_daily_pass("graphcast", day0, 1, 24, None,
                                           (10, 12, -20, -17), LATS, LONS)
    assert status == "ok" and steps is not None
    assert steps.shape == (1, 4, len(LATS), len(LONS))


def test_source_error_degrades(stub_sources, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(api, "open_forecast", boom)
    steps, status = build.model_daily_pass("graphcast", dt.date(2026, 10, 1),
                                           1, 24, None, (10, 12, -20, -17),
                                           LATS, LONS)
    assert steps is None and "unavailable (" in status
    # DR2 B2: user-facing vocabulary — never the exception class name
    assert "RuntimeError" not in status and "build failed" in status


def test_pack_bits_roundtrip_day_major_msb_first():
    # schema v3 exceedance masks: day-major, row-major, MSB-first within a byte.
    # One continuous packbits per day (padding once at the day tail), so the
    # day stride is ceil(ny*nx/8) bytes — exactly the JS unpack contract.
    rng = np.random.default_rng(7)
    nd, ny, nx = 3, 5, 13                       # ny*nx not a multiple of 8 (pad bits)
    m = rng.random((nd, ny, nx)) > 0.5
    p = build.pack_bits(m)
    assert p["ndays"] == nd and p["ny"] == ny and p["nx"] == nx
    raw = np.frombuffer(base64.b64decode(p["b64"]), np.uint8)
    stride = math.ceil(ny * nx / 8)              # bytes per day
    assert len(raw) == nd * stride
    # decode in the exact JS contract form and compare
    got = np.zeros((nd, ny, nx), bool)
    for d in range(nd):
        day = got[d].ravel()
        for i in range(ny * nx):
            day[i] = bool(raw[d * stride + (i >> 3)] & (1 << (7 - (i & 7))))
    assert np.array_equal(got, m)


def test_pack_u8_roundtrip():
    arr = np.array([[0.0, 1.0, 2.0, 3.0], [np.nan, 1.5, 2.5, 3.0]])
    p = build.pack_u8(arr, 0.0, 3.0)
    raw = np.frombuffer(base64.b64decode(p["b64"]), np.uint8).reshape(p["shape"])
    got = p["lo"] + raw.astype(float) * ((p["hi"] - p["lo"]) / 254.0)
    got[raw == 255] = np.nan
    assert np.isnan(got[1, 0])                       # missing sentinel
    assert np.allclose(got, arr, atol=(p["hi"] - p["lo"]) / 254.0 + 1e-9,
                       equal_nan=True)
    assert p["lo"] == 0.0 and p["hi"] == 3.0 and p["shape"] == [2, 4]


# ----------------------------------------------------- antimeridian handling
# ewb B8 QA 2026-11-07: the published set contains a bbox-crossing AR
# (lon 157 -> -165). dst_grid used to build an EMPTY lattice for onmax<onmin
# (crash), and regrid clamped any dst lng > 180 to the last source column.

def test_dst_grid_wrapped_is_continuous_eastward():
    lats, lons = build.dst_grid((54.0, 74.0, 156.75, -165.0), 2.0)
    assert len(lons) >= 2
    assert np.all(np.diff(lons) > 0)              # strictly ascending (contour-safe)
    assert lons[0] <= 156.0                        # starts at the west edge
    assert lons[-1] >= 195.0                       # ends past 180 = the wrap side
    assert lons[-1] < 216.0                        # not a whole-globe lattice


def test_dst_grid_normal_unchanged():
    _, lons = build.dst_grid((30.0, 51.0, -85.0, -61.0), 2.0)
    assert lons[0] < -85.0 and lons[-1] > -61.0    # padded, in-band
    assert np.all(lons > -181) and np.all(lons < 181)


def test_regrid_wrapped_window_samples_true_cells():
    # Source field carries its native longitude (0..360 convention). Nearest-
    # neighbour regridding into a continuous wrapped lattice must return each
    # destination longitude mod 360 — the old regrid clamped every lng > 180
    # to the 180 column, so the wrap half of the window was fake.
    src_lat = np.arange(-90.0, 91.0, 2.0)
    src_lon = np.arange(0.0, 360.0, 2.0)           # 0..360 native convention
    LON, _ = np.meshgrid(src_lon, src_lat)
    field = LON.astype(np.float32)                 # value = true longitude
    dst_lat, dst_lon = build.dst_grid((54.0, 74.0, 156.75, -165.0), 2.0)
    got = build.regrid(field, src_lat, src_lon, dst_lat, dst_lon)
    row = int(np.argmin(abs(dst_lat - 64.0)))
    for j, lon in enumerate(dst_lon):
        assert abs(got[row, j] - (lon % 360.0)) <= 1.0 + 1e-3, (lon, got[row, j])


def test_ar_compute_window_pads_and_refuses_a_wrapping_crop():
    """The AR mask is computed ON THE CROP, so the crop's edges are array
    edges: a tight bbox crop cut the plume it was meant to show (Europe AR
    6b4dec9dc8db, "sliced at the prime meridian"), and a crop that crosses the
    grid's own lon origin would come back with a NON-MONOTONIC longitude axis.
    Both are decided here, once."""
    lon_native = np.arange(0.0, 360.0, 0.25)
    # bbox away from lon 0: padded crop, longitude still contiguous
    lmin, lmax, onmin, onmax = build._ar_compute_bbox(
        (30.0, 50.0, 60.0, 90.0), lon_native)
    assert onmin is not None and onmin < 60.0 and onmax > 90.0
    assert lmin < 30.0 and lmax > 50.0
    assert build.AR_COMPUTE_PAD_LON_DEG >= 10.0   # stencil reach (ar_mask)
    # bbox whose padded arc reaches lon 0 on a 0..360 grid: NO crop at all
    assert build._ar_compute_bbox((36.25, 62.5, 0.25, 30.5), lon_native)[2:] == (None, None)
    # antimeridian-crossing bbox keeps its crossing encoding (lon_min > lon_max)
    res = build._ar_compute_bbox((-5.0, 5.0, 170.0, -170.0), lon_native)
    assert res[2] is not None and res[2] > res[3]
