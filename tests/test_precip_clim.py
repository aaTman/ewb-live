"""Synthetic-data tests for the flood precipitation climatology.

Offline and tiny: a 4 x 6 "ERA5" grid, a 2-year reference period and a fake
source reader, so the pooled wet-day quantile estimator, the trailing
accumulation convention, the reliability masking, the resumable scratch and the
loader API are all checked against an independent reference implementation.
No network, no ewb host required.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import shutil

import numpy as np
import pytest
import xarray as xr

from ewb_live.climatology import precip_clim as pc

NLAT, NLON = 4, 6
LAT = 90.0 - 0.25 * np.arange(NLAT)          # DESCENDING like ERA5
LON = 0.25 * np.arange(NLON)
YEARS = (2000, 2001)                          # 2000 is a leap year
WET = 1.0
MIN_N = 20
POOL = 15


def synthetic_daily(cfg: pc.Config) -> np.ndarray:
    """Deterministic mm/day series: a wet season per cell, one hyper-arid cell.

    Cell (0, 1) never reaches the 1 mm wet-day threshold, so its threshold must
    come back NaN; every other cell has a season-dependent amount so the
    day-of-year pooling actually matters.
    """
    dates = cfg.dates().astype("datetime64[D]")
    doy = (dates - dates.astype("datetime64[Y]")).astype(int) + 1
    rng = np.random.default_rng(11)
    n = len(dates)
    d = np.empty((n, NLAT, NLON), dtype="float32")
    for i in range(NLAT):
        for j in range(NLON):
            amp = 5.0 + 3.0 * i + 2.0 * j              # spatial gradient
            phase = 30.0 * i + 25.0 * j                # different onset per cell
            season = 0.5 + 0.5 * np.cos(np.radians(doy - phase))
            u = rng.random(n)
            # gamma-ish spiky series, wet in season, dry out of season
            v = amp * season ** 2 * (-np.log1p(-u)) ** 1.5
            d[:, i, j] = np.where(u < 0.25, 0.0, v).astype("float32")
    d[:, 0, 1] = 0.4                                    # hyper-arid cell
    return d


def reference_thresholds(cfg: pc.Config, d: np.ndarray, w: int,
                         q: float) -> np.ndarray:
    """Straightforward independent implementation: (366, NLAT, NLON) mm."""
    doy = cfg.dayofyear()
    ref = np.flatnonzero(cfg.ref_mask())
    out = np.full((366, NLAT, NLON), np.nan, dtype="float32")
    for day in range(1, 367):
        dist = np.abs(doy[ref] - day)
        dist = np.minimum(dist, 366 - dist)
        idx = ref[dist <= POOL]
        idx = idx[idx >= w - 1]
        if idx.size < MIN_N:
            continue
        vals = sum(d[idx - k] for k in range(w))
        for y in range(NLAT):
            for x in range(NLON):
                v = vals[:, y, x]
                v = v[np.isfinite(v) & (v >= WET)]
                if v.size >= MIN_N:
                    out[day - 1, y, x] = np.quantile(v, q)
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build a complete tiny climatology with a faked remote source."""
    out = tmp_path_factory.mktemp("clim") / "precip_test.zarr"
    scratch = tmp_path_factory.mktemp("scratch") / "daily"
    cfg = pc.Config(years=YEARS, pool=POOL, wet_min=WET, min_samples=MIN_N,
                    out=out, scratch=scratch, tile=(2, 3))
    d = synthetic_daily(cfg)
    start = cfg.start

    def fake_source_grid():
        return LAT.copy(), LON.copy()

    def fake_daily_field(date64):
        k = int((np.datetime64(date64) - np.datetime64(start))
                / np.timedelta64(1, "D"))
        return d[k].copy()

    real = (pc.source_grid, pc.daily_field, pc.MIN_FREE_GB)
    pc.source_grid, pc.daily_field = fake_source_grid, fake_daily_field
    pc.MIN_FREE_GB = 0.001
    try:
        summary = pc.build(cfg, workers=1, threads=3, nlat_pass=1,
                           keep_scratch=True)
        yield cfg, d, summary
    finally:
        pc.source_grid, pc.daily_field, pc.MIN_FREE_GB = real


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def test_build_completes_and_marks_done(built):
    cfg, _, summary = built
    assert summary["complete"] is True
    assert (cfg.out / pc.DONE_NAME).exists()
    assert cfg.meta_path().exists()
    ds = xr.open_zarr(cfg.out, consolidated=False)
    assert ds.sizes == {"quantile": 2, "accumulation": 2, "dayofyear": 366,
                        "latitude": NLAT, "longitude": NLON}
    assert ds[pc.VAR].attrs["units"] == "mm"
    assert not np.isnan(ds[pc.VAR].sel(quantile=0.95, accumulation=1)
                        .isel(dayofyear=100).values).all()


def test_download_is_resumable_and_idempotent(built):
    cfg, _, _ = built
    first = pc.download(cfg, threads=2, quiet=True)
    assert first["fetched"] == 0                      # everything is done
    assert first["days"] == first["requested"]


def test_estimator_matches_reference_daily(built):
    cfg, d, _ = built
    ds = pc.open_climatology(cfg.out)
    got = ds[pc.VAR].sel(quantile=0.95, accumulation=1).transpose(
        "dayofyear", "latitude", "longitude").values
    exp = reference_thresholds(cfg, d, 1, 0.95)
    both = np.isfinite(got) & np.isfinite(exp)
    assert both.mean() > 0.5                          # NaN pattern agrees
    assert np.allclose(np.isnan(got), np.isnan(exp))
    assert np.abs(got[both] - exp[both]).max() < 1e-3


def test_estimator_matches_reference_3day(built):
    cfg, d, _ = built
    ds = pc.open_climatology(cfg.out)
    got = ds[pc.VAR].sel(quantile=0.99, accumulation=3).transpose(
        "dayofyear", "latitude", "longitude").values
    exp = reference_thresholds(cfg, d, 3, 0.99)
    both = np.isfinite(got) & np.isfinite(exp)
    assert np.allclose(np.isnan(got), np.isnan(exp))
    assert np.abs(got[both] - exp[both]).max() < 1e-3


def test_trailing_window_is_larger_than_daily(built):
    """A 3-day threshold must be materially above its 1-day sibling."""
    cfg, _, _ = built
    ds = pc.open_climatology(cfg.out)
    d1 = ds[pc.VAR].sel(quantile=0.95, accumulation=1).values
    d3 = ds[pc.VAR].sel(quantile=0.95, accumulation=3).values
    ok = np.isfinite(d1) & np.isfinite(d3)
    assert ok.mean() > 0.3
    ratio = d3[ok] / d1[ok]
    assert np.median(ratio) > 1.3                     # 3 days of rain > 1 day
    assert (ratio > 0.9).mean() > 0.9                 # and almost never below


def test_wet_sample_count_and_arid_masking(built):
    cfg, d, _ = built
    ds = pc.open_climatology(cfg.out)
    cnt = ds[pc.COUNT_VAR].sel(accumulation=1).isel(dayofyear=100).values
    th = ds[pc.VAR].sel(quantile=0.95, accumulation=1).isel(
        dayofyear=100).values
    assert cnt.max() <= 255                            # uint8 saturated
    assert cnt[0, 1] == 0                              # 0.4 mm/day: never wet
    assert np.isnan(th[0, 1]).all()                    # -> no threshold
    assert np.isfinite(th).sum() >= 5                  # others are defined
    assert (cnt[0, 1] < pc.MIN_WET_SAMPLES)
    # NaN exactly where the pooled wet count is too small, everywhere in the year
    allc = ds[pc.COUNT_VAR].sel(accumulation=1).values
    allt = ds[pc.VAR].sel(quantile=0.95, accumulation=1).values
    assert (np.isnan(allt) == (allc < 20)).all()
    assert pc.MIN_WET_SAMPLES == 30                    # shipped default


# --------------------------------------------------------------------------
# loader API
# --------------------------------------------------------------------------

def test_precip_threshold_shape_units_and_grid(built):
    cfg, _, _ = built
    th = pc.precip_threshold(dt.date(2015, 7, 1), 0.95, 1, store=cfg.out)
    assert isinstance(th, xr.DataArray)
    assert th.dims == ("latitude", "longitude")
    assert th.attrs["units"] == "mm"
    assert np.allclose(th.latitude.values, LAT)
    assert np.allclose(th.longitude.values, LON)
    assert th.name == "total_precipitation_p95_d1"
    # the date's day-of-year picks the slice, matching the store directly
    ds = pc.open_climatology(cfg.out)
    exp = ds[pc.VAR].sel(quantile=0.95, accumulation=1).isel(
        dayofyear=dt.date(2015, 7, 1).timetuple().tm_yday - 1).values
    assert np.allclose(np.nan_to_num(th.values, nan=-1),
                       np.nan_to_num(exp, nan=-1))


def test_precip_threshold_series_and_on(built):
    cfg, _, _ = built
    dates = [dt.date(2012, 1, 31), dt.date(2012, 7, 15), dt.date(2016, 2, 29)]
    s = pc.precip_threshold_series(dates, 0.99, 3, store=cfg.out)
    assert s.dims == ("time", "latitude", "longitude")
    assert s.sizes["time"] == 3
    for k, date in enumerate(dates):
        one = pc.precip_threshold(date, 0.99, 3, store=cfg.out)
        assert np.allclose(np.nan_to_num(s.isel(time=k).values, nan=-1),
                           np.nan_to_num(one.values, nan=-1))
    # a subset lattice (nearest neighbour) equals indexing the native grid
    lats = np.array([89.75, 89.5])
    lons = np.array([0.5, 0.75])
    sub = pc.precip_threshold_on(lats, lons, dates[1], 0.95, 1, store=cfg.out)
    ref = pc.precip_threshold(dates[1], 0.95, 1, store=cfg.out)
    assert np.allclose(np.nan_to_num(sub.values, nan=-1),
                       np.nan_to_num(ref.sel(latitude=lats, longitude=lons,
                                             method=None).values, nan=-1))


def test_day_of_year_convention(built):
    cfg, _, _ = built
    assert pc.day_of_year("2020-02-29") == 60
    assert pc.day_of_year(dt.date(2021, 3, 1)) == 60
    assert pc.day_of_year(dt.date(2020, 12, 31)) == 366
    assert pc.day_of_year(np.datetime64("2021-01-01T18:00")) == 1
    th = pc.precip_threshold("2004-12-31", 0.95, 1, store=cfg.out)
    assert th.dims == ("latitude", "longitude")       # doy 366 slice exists


def test_imerg_grid_is_the_published_lattice(built):
    lat, lon = pc.grid_coords("imerg")
    assert lat.size == 1800 and lon.size == 3600
    assert lat[0] == pytest.approx(89.95) and lat[-1] == pytest.approx(-89.95)
    assert lon[0] == pytest.approx(-179.95) and lon[-1] == pytest.approx(179.95)
    assert np.allclose(np.diff(lat), -0.1) and np.allclose(np.diff(lon), 0.1)


def test_threshold_on_imerg_lattice_samples_the_native_grid(built):
    """A coarse stand-in for the 0.1 deg lattice (the tiny store is 0.25 deg)."""
    cfg, _, _ = built
    lat = np.array([89.95, 89.85, 89.75, 89.65, 89.55])
    lon = np.array([-179.95, -179.85, 0.05, 0.15, 0.25, 0.35])
    out = pc.precip_threshold_on(lat, lon, dt.date(2013, 6, 20), 0.95, 1,
                                store=cfg.out)
    native = pc.precip_threshold(dt.date(2013, 6, 20), 0.95, 1, store=cfg.out)
    assert out.dims == ("latitude", "longitude")
    assert out.shape == (lat.size, lon.size)
    # nearest neighbour must reproduce native cells (with longitude wrap)
    src = pc.open_climatology(cfg.out)
    iy = np.round((lat - 90.0) / -0.25).astype(int)
    ix = np.rint(np.mod(lon, 360.0) / 0.25).astype(int) % NLON
    assert np.allclose(np.nan_to_num(out.values, nan=-1),
                       np.nan_to_num(native.values[np.ix_(iy, ix)], nan=-1))
    assert src.sizes["longitude"] == NLON


def test_scale_seam_and_errors(built):
    cfg, _, _ = built
    a = pc.precip_threshold(dt.date(2015, 7, 1), 0.95, 1, store=cfg.out)
    b = pc.precip_threshold(dt.date(2015, 7, 1), 0.95, 1, scale=0.8,
                            store=cfg.out)
    assert np.allclose(b.values, 0.8 * a.values, rtol=1e-5, equal_nan=True)
    assert "bias_scale" in b.attrs
    with pytest.raises(ValueError, match="quantile"):
        pc.precip_threshold(dt.date(2015, 7, 1), 0.5, 1, store=cfg.out)
    with pytest.raises(ValueError, match="accumulation"):
        pc.precip_threshold(dt.date(2015, 7, 1), 0.95, 5, store=cfg.out)
    with pytest.raises(ValueError, match="latitudes"):
        pc.precip_threshold_on([10.0], [0.0], dt.date(2015, 7, 1),
                               store=cfg.out)
    with pytest.raises(FileNotFoundError, match="precip_clim build"):
        pc.precip_threshold(dt.date(2015, 7, 1), store="/nonexistent/nope.zarr")


def test_wet_sample_count_loader(built):
    cfg, _, _ = built
    c = pc.wet_sample_count(dt.date(2015, 7, 1), 1, store=cfg.out)
    assert c.dims == ("latitude", "longitude")
    assert c.shape == (NLAT, NLON)
    assert (c.values >= 0).all() and c.dtype == np.uint8


def test_region_stats_and_meta(built, monkeypatch):
    cfg, _, _ = built
    monkeypatch.setattr(pc, "REGIONS", {"tiny": (89.0, 90.0, 0.0, 0.75)})
    st = pc.region_stats((15,), cfg.out)
    r = st["regions"]["tiny"]["doy15_d1d_p95"]
    assert r["wet_n_min"] >= 0 and "mean_mm" in r
    meta = pc.climatology_meta(cfg.out)
    assert meta["percentiles"] == [0.95, 0.99]
    assert meta["units"] == "mm" and meta["path"].endswith(".zarr")
    assert "15-day" in meta["estimator"]


def test_target_indices_accept_both_longitude_frames():
    """The store's own longitudes are 0..359.75, so the documented default
    ``grid='era5'`` path must not be rejected for being '>180' (box bug,
    2026-11-17). Either frame maps; nonsense still raises."""
    src_lat = 90.0 - 0.25 * np.arange(721)
    src_lon = 0.25 * np.arange(1440)
    # the native lattice maps to itself exactly
    iy, ix, wy, wx = pc._target_indices(src_lat, src_lon, src_lat, src_lon,
                                        "nearest")
    assert np.array_equal(iy, np.arange(721))
    assert np.array_equal(ix, np.arange(1440))
    assert wy is None and wx is None
    # a -180..180 lattice maps to the same columns as the equivalent 0..360 one
    lons_w = np.linspace(-179.95, 179.95, 9)
    lons_e = np.mod(lons_w, 360.0)
    (_, ixw, _, _), (_, ixe, _, _) = (pc._target_indices(np.array([0.0]), l,
                                                         src_lat, src_lon,
                                                         "nearest")
                                      for l in (lons_w, lons_e))
    assert np.array_equal(ixw, ixe)
    with pytest.raises(ValueError, match=r"\[-180, 360\]"):
        pc._target_indices(np.array([0.0]), np.array([0.0, 400.0]),
                           src_lat, src_lon, "nearest")


def test_plot_window_rotates_the_lattice_onto_minus_180_to_180():
    """The store is 0..359.75; every map panel must come out on a strictly
    ascending -180..180 axis with the FIELD rotated along with the coordinates
    (a sheared/blank world panel was the symptom of getting this wrong)."""
    lat = np.array([50.0, 0.0, -50.0])
    lon = np.arange(0.0, 360.0, 15.0)               # 0, 15, ..., 345
    raw = np.zeros((2, lat.size, lon.size), "float32")
    raw[1] = np.arange(lon.size)[None, :]           # day 2 encodes the column
    v = xr.DataArray(raw, dims=("dayofyear", "latitude", "longitude"),
                     coords={"dayofyear": [1, 2], "latitude": lat,
                             "longitude": lon})

    f, x, y = pc._plot_window(v, 2, (-80.0, 80.0, None, None), lat, lon)
    assert np.all(np.diff(x) > 0) and x[0] == -180.0 and x[-1] == 165.0
    assert np.array_equal(y, lat)
    # the rotated column k holds the store's own column (x[k] mod 360)
    for k in range(lon.size):
        j = int(np.argmin(np.abs(lon - np.mod(x[k], 360.0))))
        assert np.array_equal(f[:, k], raw[1][:, j])

    # a box straddling the antimeridian: -60..45 E, sorted ascending
    f2, x2, y2 = pc._plot_window(v, 2, (10.0, 60.0, -45.0, 45.0), lat, lon)
    assert np.array_equal(x2, [-45.0, -30.0, -15.0, 0.0, 15.0, 30.0, 45.0])
    assert np.array_equal(y2, [50.0])
    cols = [21, 22, 23, 0, 1, 2, 3]                # lon 315..345, 0..45
    assert np.array_equal(f2, raw[1][[0]][:, cols])


def test_write_maps_renders_every_panel(built, tmp_path, monkeypatch):
    cfg, _, _ = built
    monkeypatch.setattr(pc, "MAP_ZOOMS",
                        {"world": (-80.0, 80.0, None, None),
                         "tiny": (89.0, 90.0, 0.0, 0.75)})
    files = pc.write_maps(tmp_path / "maps", (15,), cfg.out, quantile=0.95)
    assert len(files) == 2                           # 1-day + 3-day
    for f in files:
        assert f.exists() and f.stat().st_size > 10_000


# --------------------------------------------------------------------------
# verification / finalisation: a run that died after its writes is recoverable
# --------------------------------------------------------------------------

def _copy(built, tmp_path, name="precip_copy.zarr", markers=True):
    """Copy a finished tiny store, gate closed, as its own Config.

    ``markers=False`` simulates a store built before per-tile markers existed,
    where completeness has to be proved from the chunk files alone.
    """
    cfg, _, _ = built
    dst = tmp_path / name
    shutil.copytree(cfg.out, dst)
    (dst / pc.DONE_NAME).unlink(missing_ok=True)
    if markers and cfg.tiles_dir.exists():
        shutil.copytree(cfg.tiles_dir, tmp_path / (name + ".tiles"))
    c2 = pc.Config(years=YEARS, pool=POOL, wet_min=WET, min_samples=MIN_N,
                   out=dst, scratch=tmp_path / "scratch-unused", tile=(2, 3))
    c2.meta_path().unlink(missing_ok=True)
    return c2


def test_finalize_closes_a_store_whose_run_died_after_the_writes(built,
                                                                 tmp_path):
    cfg2 = _copy(built, tmp_path)
    with pytest.raises(FileNotFoundError):
        pc.open_climatology(cfg2.out)                 # the gate is closed
    assert pc.tiles_missing(cfg2) == []               # markers say it is done
    rep = pc.finalize(cfg2)
    assert rep["decoded"] is True and rep["finalized"] is True
    assert rep["decode"]["negatives"] == 0
    assert rep["decode"]["p99_below_p95"] == 0
    assert rep["decode"]["nan_vs_reliability"] == 0
    assert (cfg2.out / pc.DONE_NAME).exists()
    assert cfg2.meta_path().exists()                   # provenance sidecar
    pc._CACHE.clear()


def test_a_tile_without_its_record_falls_back_to_chunk_coverage(built,
                                                                tmp_path):
    """Markers are the record; without them a tile is certified only when every
    chunk it owns is on disk — and zarr omits all-fill chunks, so the tiny
    grid's all-NaN tile cannot be certified that way."""
    cfg2 = _copy(built, tmp_path, "precip_nomarks.zarr", markers=False)
    assert pc.tiles_missing(cfg2) == [[0, 0, 0]]
    with pytest.raises(SystemExit, match="incomplete store"):
        pc.verify_store(cfg2)


def test_markers_certify_what_chunk_coverage_cannot(built, tmp_path):
    """Tile (0,0,0) holds a day-chunk that is all-NaN, and zarr does not write
    all-fill chunks — so only its per-tile marker can certify it. The other
    tiles are certified by their chunks being on disk."""
    cfg2 = _copy(built, tmp_path, "precip_marker.zarr")
    assert pc.tiles_missing(cfg2) == []
    cfg2.tile_marker(0, 0, 1).unlink()                 # provable without it
    assert pc.tiles_missing(cfg2) == []
    cfg2.tile_marker(0, 0, 0).unlink()                 # not provable
    assert pc.tiles_missing(cfg2) == [[0, 0, 0]]
    with pytest.raises(SystemExit, match="incomplete store"):
        pc.verify_store(cfg2)


def test_tile_accounting_matches_the_build(built):
    cfg, _, _ = built
    assert pc.tiles_expected(cfg) == [[0, 0, 0], [0, 0, 1], [0, 1, 0], [0, 1, 1]]
    assert pc.tiles_missing(cfg) == []                # fresh build: all marked


def test_verify_refuses_a_foreign_fingerprint(built, tmp_path):
    cfg2 = _copy(built, tmp_path, "precip_foreign.zarr")
    other = pc.Config(years=YEARS, pool=POOL + 5, wet_min=WET,
                      min_samples=MIN_N, out=cfg2.out,
                      scratch=cfg2.scratch, tile=(2, 3))
    with pytest.raises(SystemExit, match="not built with this configuration"):
        pc.verify_store(other)


def test_verify_counts_the_product_invariants(built, tmp_path):
    """A store written by the shipped estimator must pass the deep check."""
    cfg, _, _ = built
    rep = pc.verify_store(cfg, decode=True)
    d = rep["decode"]
    assert d["finite"] > 0 and d["nan"] > 0           # arid cells stay NaN
    assert d["below_wet_min"] == 0
    assert d["min_mm"] >= WET and d["max_mm"] > d["min_mm"]


# --------------------------------------------------------------------------
# estimator unit tests (pure numpy, no store)
# --------------------------------------------------------------------------

def test_quantile_from_sorted_matches_numpy():
    rng = np.random.default_rng(3)
    n_cells, pool = 40, 200
    raw = rng.gamma(1.5, 3.0, (pool, n_cells)).astype("float32")
    mask = rng.random(raw.shape) < 0.3
    raw[mask] = np.nan
    raw = np.sort(raw, axis=0)                     # NaN to the end
    n = np.isfinite(raw).sum(axis=0)
    for q in (0.5, 0.95, 0.99):
        got = pc.quantile_from_sorted(raw, n, q, min_samples=1)
        for c in range(n_cells):
            exp = np.quantile(raw[:, c][np.isfinite(raw[:, c])], q)
            assert abs(got[c] - exp) < 1e-4, (q, c)
    # too few samples -> NaN, and never a NaN result from the padded tail
    got = pc.quantile_from_sorted(raw, n, 0.99, min_samples=150)
    assert np.isnan(got[n < 150]).all()
    assert np.isfinite(got[n >= 150]).all()


def test_pool_values_is_trailing(built):
    sub = np.arange(12, dtype="float32").reshape(4, 1, 3)
    idx = np.array([2, 3])
    got = pc.pool_values(sub, idx, 3)
    assert np.allclose(got[0, 0], sub[2, 0] + sub[1, 0] + sub[0, 0])
    assert np.allclose(got[1, 0], sub[3, 0] + sub[2, 0] + sub[1, 0])
    assert np.allclose(pc.pool_values(sub, idx, 1)[0, 0], sub[2, 0])


def test_pool_index_circular_and_reference_period(built):
    cfg, _, _ = built
    pools = cfg.pool_index()
    doy = cfg.dayofyear()
    ref = cfg.ref_mask()
    p1 = pools[1]
    assert all(doy[i] <= POOL + 1 or doy[i] >= 366 - POOL for i in p1)
    assert ref[p1].all()                            # lead-in days excluded
    assert pools[1].size >= MIN_N
