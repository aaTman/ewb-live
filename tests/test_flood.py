"""Offline unit tests for the flood lane (``ewb_live/events/flood.py``).

Everything here is synthetic and offline:

* the observations are a small **global** IMERG daily store written in the real
  on-disk layout (``daily/imerg_<day>.nc`` + ``status.json``) at a coarse
  0.5 deg lattice, joined to a 1.25 deg target lattice — the same 2.5x lattice
  ratio as the production 0.1 -> 0.25 deg join, at ~1/600 of the cells;
* the climatology is a flat synthetic percentile store whose p95 equals the
  absolute floor (20 mm/day, 50 mm/72 h), so "this cell exceeded" is a
  statement the test can check by hand, and one store cell is deliberately
  hyper-arid (NaN threshold, wet-sample count below the reliability floor).

The scenarios are the ones the brief asked for: a true flood footprint, a
dry-season false alarm, a partial-day exclusion, and a footprint that wraps the
antimeridian — plus the join arithmetic itself (the seam column, float
precision, NaN propagation) and the lane's registration.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ewb_live.climatology import precip_clim as pc
from ewb_live.events import flood as F
from ewb_live.events.event_tracker import Params
from ewb_live.events.types import EVENT_TYPES, Event
from ewb_live.events.validate import validate_event
from ewb_live.sources import imerg

# --- synthetic lattices --------------------------------------------------- #
SRC_DEG = 0.5            # "IMERG" store lattice: 360 x 720 cells (1 MB/day)
DST_DEG = 1.25           # "ERA5" target lattice: 145 x 288 (0.1 -> 0.25 ratio)
NLAT_S, NLON_S = int(180 / SRC_DEG), int(360 / SRC_DEG)
NLAT_T, NLON_T = int(180 / DST_DEG) + 1, int(360 / DST_DEG)
SRC_LAT = -90.0 + SRC_DEG / 2 + SRC_DEG * np.arange(NLAT_S)
SRC_LON = -180.0 + SRC_DEG / 2 + SRC_DEG * np.arange(NLON_S)

# --- synthetic climatology ------------------------------------------------ #
CLIM_DEG = 10.0
CLIM_LAT = 90.0 - CLIM_DEG * np.arange(19)        # descending, like ERA5
CLIM_LON = CLIM_DEG * np.arange(36)               # 0..350, like the real store
ARID_CELL = (9, 0)                                # the hyper-arid store cell
#: its neighbourhood on the target grid: lat 0 +-5 deg, lon 0 +-5 deg (wrapped)
ARID_BOX = (-4.0, 4.0, -4.0, 4.0)

DAY0 = dt.date(2026, 10, 1)
WINDOW = DAY0 - dt.timedelta(days=F.TRACKED_LOOKBACK_DAYS)
LOOKBACK = [WINDOW + dt.timedelta(days=i) for i in range(F.TRACKED_LOOKBACK_DAYS)]
DAYS = [DAY0 + dt.timedelta(days=i) for i in range(9)]
RAIN_DAYS = (1, 2, 3)                         # indices of the wet days
#: a wet 6 x 6 deg box in the tropics, away from the arid cell and the seam
BLOB = (18.0, 24.0, 40.0, 46.0)


def src_mask(box: tuple[float, float, float, float]) -> np.ndarray:
    """Boolean mask of the box on the SOURCE lattice (lon wraps, lat does not)."""
    rows = (SRC_LAT >= box[0]) & (SRC_LAT <= box[1])
    d = np.mod(SRC_LON - (box[2] + box[3]) / 2.0 + 180.0, 360.0) - 180.0
    cols = np.abs(d) <= (box[3] - box[2]) / 2.0
    return rows[:, None] & cols[None, :]


def target_mask(box: tuple[float, float, float, float]) -> np.ndarray:
    """Boolean mask of the box on the TARGET lattice (lon wraps, lat does not)."""
    lat, lon = F.era5_grid(DST_DEG)
    rows = (lat >= box[0]) & (lat <= box[1])
    d = np.mod(lon - (box[2] + box[3]) / 2.0 + 180.0, 360.0) - 180.0
    cols = np.abs(d) <= (box[3] - box[2]) / 2.0
    return rows[:, None] & cols[None, :]


def field(*, value: float = 0.0, blob: tuple | None = None,
          blob_mm: float = 60.0) -> np.ndarray:
    v = np.full((NLAT_S, NLON_S), float(value), "float32")
    if blob is not None:
        v[src_mask(blob)] = blob_mm
    return v


def write_daily(root: Path, day: dt.date, values: np.ndarray,
                cnt: int = imerg.STEPS_PER_DAY) -> Path:
    """One daily store file, exactly as ``ewb_live/sources/imerg.py`` writes it."""
    ds = xr.Dataset(
        {
            "precipitation": (("time", "lat", "lon"), values[np.newaxis],
                              {"units": imerg.UNITS}),
            "precipitation_cnt": (("time", "lat", "lon"),
                                  np.full((1, NLAT_S, NLON_S), cnt, "uint8")),
            "probabilityLiquidPrecipitation": (("time", "lat", "lon"),
                                               np.full((1, NLAT_S, NLON_S), 100, "uint8")),
        },
        coords={"time": pd.to_datetime([dt.datetime.combine(day, dt.time())]),
                "lat": ("lat", SRC_LAT), "lon": ("lon", SRC_LON)},
        attrs={"units": imerg.UNITS, "day": f"{day:%Y-%m-%d}",
               "grid_deg": SRC_DEG, "grid": json.dumps({"deg": SRC_DEG})},
    )
    path = imerg.daily_path(day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="h5netcdf")
    imerg._status_entry(root, day, status="partial" if cnt < 48 else "present",
                        product="late", bytes=path.stat().st_size,
                        mean_mm_day=float(np.nanmean(values)),
                        max_mm_day=float(np.nanmax(values)))
    return path


@pytest.fixture
def store(tmp_path) -> Path:
    """A store with a 3-day flood in the blob box and 2 mm/day elsewhere."""
    root = tmp_path / "imerg"
    for i, day in enumerate(DAYS):
        wet = i in (1, 2, 3)
        write_daily(root, day, field(value=2.0, blob=BLOB if wet else None))
    return root


def write_clim(out: Path, p1: float, p3: float, *, arid: bool = True,
               p3_late: float | None = None, from_doy: int = 367) -> Path:
    """A flat percentile store: p95 = the given values for the 1 d / 3 d lanes.

    ``p3_late`` from ``from_doy`` on models the real thing that matters here: a
    percentile that CHANGES with the day of year, so an accumulation can stop
    being extreme and start being extreme again across a calendar boundary.
    """
    a1 = np.full((19, 36), p1, "float32")
    a3 = np.full((19, 36), p3, "float32")
    if arid:
        a1[ARID_CELL] = np.nan
        a3[ARID_CELL] = np.nan
    tp = np.zeros((2, 2, 366, 19, 36), "float32")
    tp[:, 0] = np.broadcast_to(a1[None, None], (2, 366, 19, 36))
    tp[:, 1] = np.broadcast_to(a3[None, None], (2, 366, 19, 36))
    if p3_late is not None:
        tp[:, 1, from_doy - 1:] = float(p3_late)
        tp[1, 1, from_doy - 1:] = float(p3_late) * 1.5
    tp[1] *= 1.5                                   # p99 kept, not used here
    wsc = np.full((2, 366, 19, 36), 200, "uint8")
    if arid:
        wsc[:, :, ARID_CELL] = 5                   # below pc.MIN_WET_SAMPLES
    ds = xr.Dataset(
        {"total_precipitation": (("quantile", "accumulation", "dayofyear",
                                  "latitude", "longitude"), tp),
         "wet_sample_count": (("accumulation", "dayofyear", "latitude",
                               "longitude"), wsc)},
        coords={"quantile": [0.95, 0.99], "accumulation": [1, 3],
                "dayofyear": np.arange(1, 367), "latitude": CLIM_LAT,
                "longitude": CLIM_LON})
    ds.to_zarr(out, consolidated=False, mode="w")
    (out / pc.DONE_NAME).touch()
    return out


@pytest.fixture(scope="module")
def clim(tmp_path_factory) -> Path:
    """Flat percentile store: p95 == FLOOR_MM, one hyper-arid cell (NaN)."""
    return write_clim(tmp_path_factory.mktemp("floodclim") / "precip_synth.zarr",
                      F.FLOOR_MM[1], F.FLOOR_MM[3])


@pytest.fixture
def land() -> np.ndarray:
    return np.ones((NLAT_T, NLON_T), bool)


def scan(store, clim, land, *, start=DAYS[0], end=DAYS[-1], **kw) -> F.FloodScan:
    obs = F.load_observations(start - dt.timedelta(days=F.TRACKED_LOOKBACK_DAYS),
                              end, root=store, src_deg=SRC_DEG, dst_deg=DST_DEG)
    return F.build_scan(obs, store=clim, land=land, **kw)


# ---------------------------------------------------------------- the join --
def test_era5_grid_is_the_climatology_lattice_in_the_minus180_frame():
    lat, lon = F.era5_grid()
    assert lat.size == 721 and lon.size == 1440
    assert lat[0] == 90.0 and lat[-1] == -90.0 and np.all(np.diff(lat) < 0)
    assert lon[0] == -180.0 and np.all(np.diff(lon) > 0)
    # same lattice as the climatology product, modulo the longitude frame
    assert abs(lat[0] - pc.ERA5_GRID["lat_start"]) < 1e-9
    assert abs(lon[0] + 180.0 - (pc.ERA5_GRID["lon_start"] + 360.0) % 360.0) < 1e-9


def test_aggregate_is_the_mean_of_the_source_cells_in_each_target_cell():
    rng = np.random.default_rng(3)
    vals = (rng.random((1, NLAT_S, NLON_S)) * 30).astype("float32")
    da = xr.DataArray(vals, dims=("time", "lat", "lon"),
                      coords={"time": pd.to_datetime([DAY0]), "lat": SRC_LAT,
                              "lon": SRC_LON}, attrs={"units": imerg.UNITS})
    out = F.aggregate_to_era5_grid(da, src_deg=SRC_DEG, dst_deg=DST_DEG)
    assert out.shape == (1, NLAT_T, NLON_T)
    tlat, tlon = F.era5_grid(DST_DEG)
    for i, j in [(0, 0), (10, 20), (72, 144), (NLAT_T - 1, NLON_T - 1),
                 (5, 1), (NLAT_T - 2, 3)]:
        rows = np.abs(SRC_LAT - tlat[i]) < DST_DEG / 2 - 1e-6
        d = np.mod(SRC_LON - tlon[j] + 180.0, 360.0) - 180.0
        cols = np.abs(d) < DST_DEG / 2 - 1e-6
        assert out.values[0, i, j] == pytest.approx(vals[0][rows][:, cols].mean(),
                                                    abs=1e-4), (i, j)


def test_aggregate_handles_the_seam_column_and_a_constant_field():
    """The 0.1/0.25 (here 0.5/1.25) lattices are offset, so ONE target column
    wraps the antimeridian and must receive source cells from BOTH ends."""
    da = xr.DataArray(field(blob=(18.0, 24.0, 176.0, 180.0), blob_mm=100.0)[None],
                      dims=("time", "lat", "lon"),
                      coords={"time": pd.to_datetime([DAY0]), "lat": SRC_LAT,
                              "lon": SRC_LON})
    out = F.aggregate_to_era5_grid(da, src_deg=SRC_DEG, dst_deg=DST_DEG).values[0]
    tlat, tlon = F.era5_grid(DST_DEG)
    j = int(np.argmin(np.abs(tlon + 180.0)))          # the seam column
    i = int(np.argmin(np.abs(tlat - 20.0)))
    assert 0.0 < out[i, j] < 100.0                    # its western half is seen
    # the wrapped cell holds 12 source columns and exactly half of them are wet
    assert out[i, j] == pytest.approx(50.0, abs=0.01)
    assert out[i, (tlon.size - 1)] > 0                # and its eastern half too
    # a constant field must come back exactly: float32 box sums lose ~1 mm to
    # cancellation over a global grid, which is why the sums run in float64
    const = np.full((1, NLAT_S, NLON_S), 12.34, "float32")
    da2 = xr.DataArray(const, dims=("time", "lat", "lon"),
                       coords={"time": pd.to_datetime([DAY0]), "lat": SRC_LAT,
                               "lon": SRC_LON})
    o2 = F.aggregate_to_era5_grid(da2, src_deg=SRC_DEG, dst_deg=DST_DEG)
    assert np.allclose(o2.values, 12.34, atol=1e-4)


def test_aggregate_drops_a_cell_with_any_unobserved_source_cell():
    vals = np.full((1, NLAT_S, NLON_S), 10.0, "float32")
    i = int(np.argmin(np.abs(SRC_LAT - 20.0)))
    j = int(np.argmin(np.abs(SRC_LON - 30.0)))
    vals[0, i, j] = np.nan
    da = xr.DataArray(vals, dims=("time", "lat", "lon"),
                      coords={"time": pd.to_datetime([DAY0]), "lat": SRC_LAT,
                              "lon": SRC_LON})
    strict = F.aggregate_to_era5_grid(da, src_deg=SRC_DEG, dst_deg=DST_DEG)
    loose = F.aggregate_to_era5_grid(da, src_deg=SRC_DEG, dst_deg=DST_DEG,
                                     require_all=False)
    tlat, tlon = F.era5_grid(DST_DEG)
    ti = int(np.argmin(np.abs(tlat - 20.0)))
    tj = int(np.argmin(np.abs(tlon - 30.0)))
    assert np.isnan(strict.values[0, ti, tj])
    assert np.isfinite(strict.values).sum() == strict.size - 1
    assert loose.values[0, ti, tj] == pytest.approx(10.0)


def test_aggregate_refuses_a_lattice_that_does_not_cover_the_target():
    da = xr.DataArray(np.zeros((1, 20, 40), "float32"), dims=("time", "lat", "lon"),
                      coords={"time": pd.to_datetime([DAY0]),
                              "lat": -75.0 + 5.0 * np.arange(20),
                              "lon": -177.5 + 5.0 * np.arange(40)})
    with pytest.raises(ValueError, match="lattice|tile|spacing"):
        F.aggregate_to_era5_grid(da, src_deg=5.0, dst_deg=DST_DEG)


def test_trailing_accumulation_is_indexed_by_the_end_date():
    d = np.stack([np.full((1, 1), k + 1.0, "float32") for k in range(4)])
    out = F.trailing_accumulation(d, 3)[:, 0, 0]
    assert np.isnan(out[:2]).all()
    assert out[2] == 6.0 and out[3] == 9.0            # 1+2+3, 2+3+4
    assert F.trailing_accumulation(d, 1)[1, 0, 0] == 2.0
    # one unobserved day poisons every window that contains it, and only those
    d[1, 0, 0] = np.nan
    out = F.trailing_accumulation(d, 3)[:, 0, 0]
    assert np.isnan(out[[1, 2, 3]]).all()


def test_exceedance_needs_both_the_threshold_and_the_floor():
    obs = np.array([19.9, 20.0, 25.0, np.nan])
    thr = np.array([20.0, 20.0, 24.0, 20.0])
    assert list(F.exceedance(obs, thr, 20.0)) == [False, True, True, False]
    # a NaN threshold means "no flood is definable here", never "flood"
    assert not F.exceedance(np.array([100.0]), np.array([np.nan]), 20.0).any()
    assert not F.exceedance(np.array([19.0]), np.array([1.0]), 20.0).any()


# ------------------------------------------------------------------- masks --
def test_true_flood_is_tracked_and_validated(store, clim, land):
    s = scan(store, clim, land)
    assert s.observed_dates == DAYS                    # every asked-for day has data
    assert s.dates[0] == DAYS[0] - dt.timedelta(days=F.TRACKED_LOOKBACK_DAYS)
    wet = [i for i, d in enumerate(s.dates) if d in (DAYS[1], DAYS[2], DAYS[3])]
    for i in wet:
        assert s.mask[i].any()
    assert not s.mask[0].any()                         # 2 mm/day is not a flood
    ev = F.events_from_scan(s)
    assert len(ev) == 1
    e = ev[0]
    assert e.event_type == "flood" and e.start.date() == DAYS[1]
    # the flood lasts as long as the 72 h accumulation stays extreme: two dry
    # days after the rain still carry 64 mm of it, past the 50 mm floor
    assert e.end.date() == DAYS[6]                     # exclusive end
    assert e.title == f"Flood {DAYS[1]} to {DAYS[5]}"
    assert validate_event(e) == []
    assert e.details["peak_3day_mm"] >= F.FLOOR_MM[3]
    assert e.peak_value == pytest.approx(e.details["peak_3day_mm"])
    assert e.details["peak_value_units"] == "mm"
    assert e.details["peak_daily_mm"] >= F.FLOOR_MM[1]
    assert e.details["peak_3day_mm"] >= e.details["peak_daily_mm"]
    assert e.details["lanes"]["daily"] > 0 and e.details["lanes"]["accum_3d"] > 0
    assert e.details["duration_days"] == 5
    assert e.details["observed_days"] == 5
    assert e.details["unobserved_days"] == []
    assert e.details["peak_area_km2"] >= F.FLOOD_PARAMS.qualify_area_km2
    assert e.details["peak_excess_ratio"] >= 1.0
    assert e.details["quantile"] == F.DEFAULT_QUANTILE
    assert e.details["accumulation_days"] == [1, 3]
    assert e.details["climatology"]["source"].startswith("era5_pooled")
    assert e.details["climatology_note"]
    assert e.details["land_mask"].startswith("natural_earth")
    # the exceedance must sit where the rain was
    assert target_mask(BLOB).any()
    assert e.bbox[0] >= 15.0 and e.bbox[1] <= 27.5
    assert 38.0 <= e.bbox[2] and e.bbox[3] <= 48.0


def test_provisional_and_source_provenance_are_recorded(store, clim, land):
    s = scan(store, clim, land)
    e = F.events_from_scan(s)[0]
    assert e.details["provisional"] is True
    assert "late" in e.details["provisional_reason"]
    src = e.details["data_source"]
    assert src["days_present"] == len(DAYS)          # the lookback days are absent
    assert src["missing_days"] == [f"{d:%Y-%m-%d}" for d in LOOKBACK]
    assert src["src_grid_deg"] == SRC_DEG and src["grid_deg"] == DST_DEG
    assert src["screen"] == "cnt" and src["runs"].get("late") == len(DAYS)


def test_dry_season_and_hyper_arid_cells_do_not_flood(store, clim, land):
    """A few-mm day must not be an event, and neither may heavy rain in a cell
    whose percentile is NaN (hyper-arid: no flood is definable there)."""
    root = store
    for i, d in enumerate(DAYS):                       # 15 mm/day: rain, not a flood
        write_daily(root, d, field(value=15.0, blob=None))
    s = scan(root, clim, land)
    assert not s.mask.any()
    assert F.events_from_scan(s) == []


def test_heavy_rain_in_a_hyper_arid_cell_is_not_an_event(tmp_path, clim, land):
    root = tmp_path / "imerg"
    for d in DAYS:
        write_daily(root, d, field(blob=ARID_BOX, blob_mm=120.0))
    s = scan(root, clim, land)
    assert not s.mask.any(), "a NaN threshold must never be filled with 0"
    assert F.events_from_scan(s) == []


def test_partial_day_is_never_counted(tmp_path, clim, land):
    """One under-sampled day (cnt < 48) is excluded outright: its own 60 mm day
    cannot exceed, it cannot feed a 72 h accumulation, and no published event
    may claim it as evidence."""
    root = tmp_path / "imerg"
    for i, d in enumerate(DAYS):
        wet = i in RAIN_DAYS
        cnt = 20 if i == 2 else imerg.STEPS_PER_DAY
        write_daily(root, d, field(value=2.0, blob=BLOB if wet else None), cnt=cnt)
    s = scan(root, clim, land)
    assert DAYS[2] not in s.observed_dates             # dropped, not read as dry
    assert DAYS[2].isoformat() in s.source["missing_days"]
    i3 = s.dates.index(DAYS[3])
    assert np.isnan(s.obs_lane[3][i3]).all()           # it poisons every window
    # the outage ENDS the open flood: the rain on either side becomes separate
    # 1-day events instead of one event whose interval contains a day with no
    # evidence. That is the conservative trade - a real flood can be split by an
    # outage, but an interval is never credited with a day it cannot see.
    events = F.events_from_scan(s)
    assert [e.details["duration_days"] for e in events] == [1, 1, 1]
    assert not any(e.start.date() <= DAYS[2] < e.end.date() for e in events)
    assert all(e.details["unobserved_days"] == [] for e in events)
    assert all(validate_event(e) == [] for e in events)
    # control: the same window with full sampling IS one flood
    for i, d in enumerate(DAYS):
        write_daily(root, d, field(value=2.0, blob=BLOB if i in RAIN_DAYS else None),
                    cnt=imerg.STEPS_PER_DAY)
    ok = F.events_from_scan(scan(root, clim, land))
    assert len(ok) == 1 and ok[0].details["duration_days"] == 5


def test_missing_day_is_dropped_and_never_read_as_dry(tmp_path, clim, land):
    """No granule at all: the same no-evidence rule as an under-sampled day, and
    the day stays reported as MISSING, never as a day without a flood."""
    root = tmp_path / "imerg"
    for i, d in enumerate(DAYS):
        if i == 2:
            continue                                   # no granule at all
        write_daily(root, d, field(value=2.0, blob=BLOB if i in RAIN_DAYS else None))
    obs = F.load_observations(DAYS[0] - dt.timedelta(days=F.TRACKED_LOOKBACK_DAYS),
                              DAYS[-1], root=root, src_deg=SRC_DEG, dst_deg=DST_DEG)
    s = F.build_scan(obs, store=clim, land=land)
    assert DAYS[2] in s.dates                          # the calendar is complete...
    assert DAYS[2] not in s.observed_dates             # ...the data is not
    assert DAYS[2].isoformat() in s.source["missing_days"]
    assert not s.mask[s.dates.index(DAYS[2])].any()    # never read as "dry"
    assert np.isnan(s.obs_lane[3][s.dates.index(DAYS[3])]).all()
    ev = F.events_from_scan(s)
    assert not any(e.start.date() <= DAYS[2] < e.end.date() for e in ev)
    assert all(e.details["unobserved_days"] == [] for e in ev)
    assert ev[0].details["data_source"]["days_present"] == len(DAYS) - 1
    # "no data" must stay distinguishable from "no flood" in the run report
    assert F.daily_summary(obs, s)[s.dates.index(DAYS[2])]["observed"] is False
    assert all(validate_event(e) == [] for e in ev)


def test_footprint_across_the_antimeridian_is_one_wrapped_event(tmp_path, clim, land):
    root = tmp_path / "imerg"
    seam = (18.0, 24.0, 177.0, 183.0)                  # straddles +-180
    for i, d in enumerate(DAYS):
        write_daily(root, d, field(value=2.0, blob=seam if i in RAIN_DAYS else None,
                                  blob_mm=80.0))
    s = scan(root, clim, land)
    ev = F.events_from_scan(s)
    assert len(ev) == 1, "the seam must not split one flood into two"
    e = ev[0]
    lat0, lat1, lon0, lon1 = e.bbox
    assert lon0 > lon1 and e.details["antimeridian_wrap"] is True
    assert lon0 > 170.0 and lon1 < -170.0
    assert validate_event(e) == []
    assert e.details["peak_3day_mm"] >= F.FLOOR_MM[3]
    # the wrapped span is ~7 deg of longitude, not the 353 deg the naive
    # (lon1 - lon0) would claim
    assert (lon1 - lon0) % 360.0 == pytest.approx(7.0, abs=2.0)


def test_a_slow_storm_floods_without_ever_reaching_the_daily_floor(tmp_path, land):
    """18 mm/day for four days is below the 20 mm/day daily floor every single
    day, and is a 54 mm per 72 h flood: the daily lane must not be what proves
    it, and the validator must not call it impossible."""
    root = tmp_path / "imerg"
    store_clim = write_clim(tmp_path / "clim_slow.zarr", 20.0, 50.0)
    for d in LOOKBACK + DAYS:
        write_daily(root, d, field(value=2.0, blob=BLOB, blob_mm=18.0))
    s = scan(root, store_clim, land)
    assert not s.mask_lane[1].any(), "18 mm/day must not fire the daily lane"
    assert s.mask_lane[3].any()
    ev = F.events_from_scan(s)
    assert len(ev) == 1
    e = ev[0]
    assert e.details["peak_daily_mm"] < F.FLOOR_MM[1]
    assert e.details["peak_3day_mm"] >= F.FLOOR_MM[3]
    assert e.details["lanes"]["daily"] == 0 and e.details["lanes"]["accum_3d"] > 0
    assert validate_event(e) == []


def test_rain_that_fell_before_the_flagged_day_is_attributed(tmp_path, land):
    """The 72 h lane flags the day an extreme accumulation ENDS. When the
    percentile drops across a calendar boundary, that day can fall after the
    rain: the event must then say which rain caused it instead of advertising a
    flood with no rain in it."""
    root = tmp_path / "imerg"
    doy0 = DAYS[0].timetuple().tm_yday
    # p95(1 d) 100 mm: the 60 mm days are never a daily exceedance.
    # p95(3 d) 130 mm until DAYS[1], 50 mm from DAYS[2] on.
    store_clim = write_clim(tmp_path / "clim_step.zarr", 100.0, 130.0,
                            p3_late=50.0, from_doy=doy0 + 2)
    for d in LOOKBACK + DAYS:
        wet = d in (DAYS[0], DAYS[1])
        write_daily(root, d, field(value=2.0, blob=BLOB if wet else None,
                                   blob_mm=60.0))
    s = scan(root, store_clim, land)
    ev = F.events_from_scan(s)
    assert len(ev) == 1
    e = ev[0]
    assert e.start.date() == DAYS[2], "the flag day is the day the 72 h peaks"
    assert e.end.date() == DAYS[4]
    # the honest part: the rain is named, even though it fell outside the event
    assert e.details["peak_daily_mm"] is not None and e.details["peak_daily_mm"] < 10.0
    assert e.details["rain_peak_daily_mm"] == pytest.approx(60.0, abs=1.0)
    assert e.details["rain_peak_day"] in (f"{DAYS[0]:%Y-%m-%d}", f"{DAYS[1]:%Y-%m-%d}")
    assert e.details["peak_3day_mm"] >= F.FLOOR_MM[3]
    assert validate_event(e) == []


def test_attribution_survives_an_unobserved_day_in_its_lookback(tmp_path, clim, land):
    """The rain attribution looks back two days past the event start; a day with
    no granule there must not turn the answer into "no rain" (an unobserved cell
    is not a zero, and it must not win an argmax either)."""
    root = tmp_path / "imerg"
    for d in LOOKBACK + DAYS:
        if d == LOOKBACK[-1]:
            continue                                   # the day the lookback needs
        wet = d == DAYS[1]
        write_daily(root, d, field(value=2.0, blob=BLOB if wet else None,
                                   blob_mm=60.0))
    s = scan(root, clim, land)
    ev = F.events_from_scan(s)
    assert len(ev) == 1
    e = ev[0]
    assert e.start.date() == DAYS[1]
    assert f"{LOOKBACK[-1]:%Y-%m-%d}" in s.source["missing_days"]
    assert e.details["rain_peak_daily_mm"] == pytest.approx(60.0, abs=1.0)
    assert e.details["rain_peak_day"] == f"{DAYS[1]:%Y-%m-%d}"
    assert validate_event(e) == []


def test_land_mask_gates_the_ocean(tmp_path, clim, land):
    root = tmp_path / "imerg"
    for i, d in enumerate(DAYS):
        write_daily(root, d, field(value=2.0, blob=BLOB if i in RAIN_DAYS else None,
                                  blob_mm=90.0))
    # blank the land wherever the rule fires (the areal mean makes the wet
    # footprint slightly wider than the rain blob), then check nothing survives
    wet_here = scan(root, clim, np.ones((NLAT_T, NLON_T), bool))
    everywhere = np.ones((NLAT_T, NLON_T), bool)
    assert len(F.events_from_scan(wet_here)) == 1
    ocean_out = everywhere & ~wet_here.mask.any(axis=0)
    assert F.events_from_scan(scan(root, clim, ocean_out)) == []


# ---------------------------------------------------------------- tracker --
def test_flood_params_are_a_documented_shrink_from_the_temperature_lane():
    p = F.FLOOD_PARAMS
    d = Params()
    assert p.qualify_area_km2 < d.qualify_area_km2 and p.seed_area_km2 < d.seed_area_km2
    assert p.min_days == 1 < d.min_days
    assert p.region_radius < d.region_radius and p.move_cells < d.move_cells
    assert p.max_box_area_km2 is not None
    assert dataclasses.asdict(p).keys() == dataclasses.asdict(d).keys()


def test_scan_window_does_not_publish_events_outside_it(store, clim, land):
    events = F.scan_flood_events(DAYS[3], DAYS[-1], root=store, store=clim,
                                 land=land, src_deg=SRC_DEG, dst_deg=DST_DEG)
    assert events and all(e.end.date() > DAYS[3] for e in events)
    dry = F.scan_flood_events(DAYS[7], DAYS[8], root=store, store=clim, land=land,
                              src_deg=SRC_DEG, dst_deg=DST_DEG)
    assert dry == []


# ----------------------------------------------------------- registration --
def test_flood_is_registered_end_to_end():
    assert "flood" in EVENT_TYPES
    from ewb_live.events.validate import _PEAK_RANGE, _TITLE_LABELS

    assert _TITLE_LABELS["flood"] == "Flood"
    lo, hi = _PEAK_RANGE["flood"]
    assert lo <= 20.0 and hi >= 500.0                  # mm, not degC
    assert F.SCORED is False                           # published, never scored


def test_detect_events_routes_flood_and_does_not_clamp_it(monkeypatch, tmp_path):
    """The flood lane reads IMERG, not ERA5: the ERA5 truth cutoff must not
    trim its window (the newest days are where a flood lives)."""
    from ewb_live.events import detect_events

    calls: list[tuple] = []
    monkeypatch.setattr(F, "scan_flood_events",
                        lambda start, end, **kw: calls.append((start, end)) or [])
    end = dt.date(2026, 11, 20)
    detect_events(dt.date(2026, 8, 22), end, types=("flood",), top_n=None,
                  cache_dir=tmp_path, use_cache=False)
    assert calls == [(dt.date(2026, 8, 22), end)]


def test_daily_summary_reports_areas_and_runs(store, clim, land):
    obs = F.load_observations(DAYS[0], DAYS[-1], root=store, src_deg=SRC_DEG,
                              dst_deg=DST_DEG)
    s = F.build_scan(obs, store=clim, land=land)
    rows = F.daily_summary(obs, s)
    wet = [r for r in rows if r["cells"]]
    assert {r["date"] for r in wet} == {d.isoformat() for d in DAYS[1:6]}
    assert all(r["area_km2"] > 0 and r["run"] == "late" for r in wet)
    assert all(r["peak_daily_mm"] for r in wet)
    assert any(r["peak_3day_mm"] for r in wet)
