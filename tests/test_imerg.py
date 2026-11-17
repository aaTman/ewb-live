"""Offline unit tests for the IMERG ingest (synthetic granules, no network).

Every test here is offline: granules are written by hand with xarray/h5netcdf, the
download step is injected. Network fetches are exercised by
``python -m ewb_live.sources.imerg update`` on the ewb LXC.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ewb_live.sources import imerg
from ewb_live.sources.imerg import (
    CNT_UNKNOWN, MASK_FULL_SAMPLING, MASK_PRESENT, Granule, ImergError,
    accumulate_half_hourly, daily_from_granule, fetch, ingest, load_imerg_daily,
    load_imerg_mask, load_imerg_status, roll_window, survey, _unit_factor,
)

DAY1 = dt.date(2026, 11, 10)
#: Synthetic grid resolution. Still a *global* lattice (so the coverage assertion in
#: ``_check_grid`` bites) but 6 x 12 = 72 cells: a granule is ~1 kB, so a 100-day test
#: cache is a few MB. Postmortem note: an earlier version of this file ran away and wrote
#: 140 GB into /tmp — the cause was NOT grid size, it was a stub HTTP response whose
#: ``read()`` never returned ``b""``, which made ``shutil.copyfileobj`` append forever
#: (see ``test_https_route_caps_a_body_that_never_ends``). Keep stubs EOF-terminated.
DEG = 30.0


def _grid(deg: float = DEG) -> tuple[np.ndarray, np.ndarray]:
    lat = np.arange(-90.0 + deg / 2, 90.0, deg)
    lon = np.arange(-180.0 + deg / 2, 180.0, deg)
    return lat, lon


def _shape(deg: float = DEG) -> tuple[int, int]:
    lat, lon = _grid(deg)
    return lat.size, lon.size


def write_granule(path, day: dt.date, *, rate=0.5, units="mm/hr", cnt=imerg.STEPS_PER_DAY,
                  pol=100, deg: float = DEG, fill_cells: tuple[tuple[int, int], ...] = (),
                  bad_grid: bool = False) -> str:
    """Write a producer-shaped daily granule (mm/hr mean rate) and return its path."""
    lat, lon = _grid(deg)
    if bad_grid:
        lat = np.arange(-89.0, 90.0, 2.0)
    values = np.full((lat.size, lon.size), float(rate), dtype="float32")
    for (i, j) in fill_cells:
        values[i, j] = -9999.0                      # the producer's fill value
    arr = values[np.newaxis]
    cnt_arr = np.full((1, lat.size, lon.size), int(cnt), dtype="uint8") \
        if np.ndim(cnt) == 0 else np.asarray(cnt, dtype="uint8")[np.newaxis]
    ds = xr.Dataset(
        {
            "precipitation": (("time", "lat", "lon"), arr, {"units": units,
                                                            "long_name": "precipitation"}),
            "precipitation_cnt": (("time", "lat", "lon"), cnt_arr, {"units": "count"}),
            "probabilityLiquidPrecipitation": (("time", "lat", "lon"),
                                               np.full((1, lat.size, lon.size), int(pol),
                                                       dtype="uint8"),
                                               {"units": "%"}),
        },
        coords={"time": [(day - EPOCH).days + 0.0], "lat": ("lat", lat), "lon": ("lon", lon)},
    )
    ds["time"].attrs["units"] = f"days since {EPOCH:%Y-%m-%d %H:%M:%S}"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="h5netcdf", format="NETCDF4")
    return str(path)


def granule_name(day: dt.date) -> str:
    return f"3B-DAY-E.MS.MRG.3IMERG.{day:%Y%m%d}-S000000-E235959.V07C.nc4"


EPOCH = dt.date(1980, 1, 1)      # the producer's reference time for the 'time' variable


def fake_remote(tmp_path, days, **kw) -> tuple[dict, dict[dt.date, Granule]]:
    """A 'GES DISC' of synthetic granules plus the survey map that points at it."""
    remote, granules = {}, {}
    for day in days:
        path = Path(tmp_path) / "remote" / granule_name(day)
        write_granule(path, day, **kw)
        remote[day] = path
        granules[day] = Granule(day=day, granule_ur=f"GPM_3IMERGDE.07:{path.name}",
                                url=str(path), size_mb=26.5)
    return remote, granules


def copier(remote: dict[dt.date, Granule]):
    """Stand-in downloader: copy from the fake remote, honouring a partial .part file."""

    def _download(url: str, dest) -> ...:
        from pathlib import Path

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        if part.exists():                       # resume: keep what landed, add the rest
            part.write_bytes(Path(url).read_bytes())
        else:
            part.write_bytes(b"")
            part.write_bytes(Path(url).read_bytes()[10:])     # simulate an interruption
            _download(url, dest)                              # retry with .part present
            return dest
        part.rename(dest)
        return dest

    return _download


# ------------------------------------------------------------------ unit conversion


def test_unit_factor_reads_the_producer_units():
    assert _unit_factor("mm/day") == 1.0
    assert _unit_factor("Gauge-Adjusted (GAD) ... (mm/hr)") == imerg.HOURS_PER_DAY
    assert _unit_factor("mm hr-1") == 24.0
    assert _unit_factor("mm/hour") == 24.0
    with pytest.raises(ImergError, match="cannot convert"):
        _unit_factor("kg m-2")


def test_daily_from_granule_rate_to_accumulation(tmp_path):
    day = DAY1
    src = write_granule(tmp_path / granule_name(day), day, rate=0.5, units="mm/hr")
    out = daily_from_granule(src, grid_deg=DEG)
    assert out["precipitation"].dims == ("time", "lat", "lon")
    assert out["precipitation"].attrs["units"] == imerg.UNITS
    assert float(out["precipitation"][0, 0, 0]) == pytest.approx(12.0)      # 0.5 mm/hr x 24
    assert out.attrs["producer_units"] == "mm/hr"
    assert out["precipitation_cnt"].dtype == np.uint8
    assert out["precipitation_cnt"].values.max() == imerg.STEPS_PER_DAY
    stamp = pd.Timestamp(out["time"].values[0])
    assert stamp.to_pydatetime().date() == day
    assert stamp.hour == 0                                                  # 00:00 UTC


def test_daily_from_granule_passes_accumulated_units_through(tmp_path):
    src = write_granule(tmp_path / "x.nc4", DAY1, rate=10.0, units="mm/day")
    out = daily_from_granule(src, grid_deg=DEG)
    assert float(out["precipitation"][0, 2, 3]) == pytest.approx(10.0)


def test_daily_from_granule_fill_is_nan_not_zero(tmp_path):
    src = write_granule(tmp_path / "x.nc4", DAY1, rate=1.0, fill_cells=((1, 2), (3, 4)))
    out = daily_from_granule(src, grid_deg=DEG)
    assert np.isnan(out["precipitation"].values[0, 1, 2])
    assert np.isnan(out["precipitation"].values[0, 3, 4])
    assert out["precipitation"].values[0, 0, 0] == pytest.approx(24.0)
    assert not np.nanmin(out["precipitation"].values) <= 0.0                 # no zero-fill


def test_daily_from_granule_rejects_unknown_units_and_odd_grid(tmp_path):
    bad_units = write_granule(tmp_path / "u.nc4", DAY1, units="kg/m2")
    with pytest.raises(ImergError, match="cannot convert"):
        daily_from_granule(bad_units, grid_deg=DEG)
    coarse = write_granule(tmp_path / "g.nc4", DAY1, bad_grid=True)
    with pytest.raises(ImergError, match="spacing"):
        daily_from_granule(coarse, grid_deg=DEG)
    fine = write_granule(tmp_path / "f.nc4", DAY1)
    with pytest.raises(ImergError, match="spacing"):                        # strict 0.1 default
        daily_from_granule(fine)


# ---------------------------------------------------------- half-hourly aggregation


def _half_hourly(times, values) -> xr.DataArray:
    lat, lon = np.array([-0.5, 0.5]), np.array([-0.5, 0.5])
    data = np.asarray(values, dtype="float32")[:, None, None] * np.ones((1, 2, 2))
    return xr.DataArray(data, dims=("time", "lat", "lon"),
                        coords={"time": pd.to_datetime(times), "lat": lat, "lon": lon},
                        attrs={"units": "mm/hr"})


def test_accumulate_half_hourly_uses_the_utc_day_boundary():
    # 00:00 on 10 Nov .. 00:00 on 12 Nov, hourly-ish 30-min steps, constant 1 mm/hr
    times = pd.date_range("2026-11-10T00:00", "2026-11-12T00:00", freq="30min")
    steps = np.ones(len(times))
    steps[-1] = 4.0                                    # the 12th's first step, alone
    accum, n_steps = accumulate_half_hourly(_half_hourly(times, steps))
    assert [pd.Timestamp(t).date().isoformat() for t in accum.time.values] == [
        "2026-11-10", "2026-11-11", "2026-11-12"]
    assert accum.values[0, 0, 0] == pytest.approx(24.0)          # 48 x 0.5 h x 1 mm/hr
    assert accum.values[1, 0, 0] == pytest.approx(24.0)
    assert accum.values[2, 0, 0] == pytest.approx(2.0)           # one step of 4 mm/hr
    assert n_steps.values[0, 0, 0] == imerg.STEPS_PER_DAY
    assert n_steps.values[2, 0, 0] == 1


def test_accumulate_half_hourly_never_zero_fills_a_gap():
    times = pd.date_range("2026-11-10T00:00", periods=48, freq="30min")
    steps = np.ones(48)
    steps[7] = np.nan
    accum, n_steps = accumulate_half_hourly(_half_hourly(times, steps))
    assert n_steps.values[0, 0, 0] == imerg.STEPS_PER_DAY - 1          # partial day, flagged
    assert accum.values[0, 0, 0] == pytest.approx(47 * 0.5)

    all_nan = _half_hourly(times, np.full(48, np.nan))
    accum2, n2 = accumulate_half_hourly(all_nan)
    assert np.isnan(accum2.values).all() and n2.values[0, 0, 0] == 0    # NaN, never 0

    with pytest.raises(ImergError, match="time step"):
        accumulate_half_hourly(_half_hourly(pd.date_range("2026-11-10", periods=4, freq="h"),
                                            [1, 1, 1, 1]))


# --------------------------------------------------------------- ingest + loading


@pytest.fixture()
def cache(tmp_path):
    return tmp_path / "imerg"


def test_ingest_load_and_idempotence(cache):
    days = [DAY1, DAY1 + dt.timedelta(days=1), DAY1 + dt.timedelta(days=2)]
    remote, granules = fake_remote(tmp_path=cache, days=days[:2])     # day 3 unpublished
    root = cache / "root"

    summary = ingest(days[0], days[2], root=root, granules=granules, workers=1,
                     downloader=copier(remote), keep_raw=99, grid_deg=DEG)
    assert summary["fetch"]["fetched"] == 2
    assert summary["fetch"]["missing"] == [f"{days[2]:%Y-%m-%d}"]
    assert (root / "daily" / f"imerg_{days[0]:%Y%m%d}.nc").exists()
    assert not (root / "daily" / f"imerg_{days[2]:%Y%m%d}.nc").exists()

    da = load_imerg_daily(days[0], days[2], root=root)
    assert da.dims == ("time", "lat", "lon")
    assert da.attrs["units"] == imerg.UNITS
    assert da.shape[0] == 3                                       # regular daily time axis
    assert da.attrs["days_present"] == 2
    assert da.attrs["missing_days"] == [f"{days[2]:%Y-%m-%d}"]
    assert np.allclose(da.values[0], 12.0)
    assert np.isnan(da.values[2]).all()                           # missing day: NaN, not 0

    mask = load_imerg_mask(days[0], days[2], root=root)
    assert mask.dtype == np.uint8
    assert (mask.values[0] == (MASK_PRESENT | MASK_FULL_SAMPLING | 4)).all()
    assert (mask.values[2] == 0).all()

    status = load_imerg_status(days[0], days[2], root=root)
    assert list(status["status"]) == ["present", "present", "missing"]

    again = ingest(days[0], days[2], root=root, granules=granules, workers=1,
                   downloader=copier(remote), keep_raw=99, grid_deg=DEG)
    assert again["fetch"]["fetched"] == 0 and again["fetch"]["cached"] == 2
    assert [e["status"] for e in again["ingested"]] == ["already", "already"]
    assert again["global_mean_mm_day"] == pytest.approx(12.0)


def test_ingest_flags_partial_granule_and_screens(cache):
    day = DAY1
    nlat, nlon = _shape()
    cnt = np.full((nlat, nlon), imerg.STEPS_PER_DAY, dtype="int")
    cnt[3, 5] = 12                                            # under-sampled cell
    path = tmp_path_granule(cache, day, cnt=cnt, rate=1.0)
    granules = {day: Granule(day=day, granule_ur=path.name, url=str(path), size_mb=26.5)}
    root = cache / "root"
    ingest(day, day, root=root, granules=granules, workers=1, downloader=copyfile_downloader,
           grid_deg=DEG)

    da = load_imerg_daily(day, day, root=root)
    assert da.values[0, 3, 5] == pytest.approx(24.0)          # kept with screen="none"
    screened = load_imerg_daily(day, day, root=root, screen="cnt")
    assert np.isnan(screened.values[0, 3, 5])
    assert np.allclose(screened.values[0, 0, 0], 24.0)

    mask = load_imerg_mask(day, day, root=root)
    assert mask.values[0, 3, 5] == (MASK_PRESENT | 4)         # full-sampling bit cleared
    status = load_imerg_status(day, day, root=root)
    assert status["status"].iloc[0] == "partial"
    assert status["cnt_min"].iloc[0] == 12


def tmp_path_granule(cache, day, **kw):
    from pathlib import Path

    return Path(write_granule(Path(cache) / "remote" / granule_name(day), day, **kw))


def copyfile_downloader(url, dest):
    """Stand-in downloader that returns the Path it wrote (``fetch`` sizes it)."""
    import shutil
    from pathlib import Path

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(url, dest)
    return dest


def test_fetch_reports_missing_and_survives_a_bad_download(cache):
    days = [DAY1, DAY1 + dt.timedelta(days=1)]
    remote, granules = fake_remote(tmp_path=cache, days=[days[0]])    # days[1]: no granule

    def broken(url, dest):
        raise ImergError("boom")

    out = fetch(days, root=cache / "root", granules=granules, workers=1, downloader=broken)
    assert out["fetched"] == 0
    assert "boom" in out["errors"][f"{days[0]:%Y-%m-%d}"]              # a failed download is
    assert out["missing"] == [f"{days[1]:%Y-%m-%d}"]                   # not a survey gap
    status = load_imerg_status(days[1], days[1], root=cache / "root")
    assert status["status"].iloc[0] == "missing"
    assert load_imerg_status(days[0], days[0], root=cache / "root")["status"].iloc[0] \
        == "not_ingested"


def test_resumable_download_reuses_the_partial_file(cache):
    day = DAY1
    remote, granules = fake_remote(tmp_path=cache, days=[day])
    root = cache / "root"
    dest = imerg.raw_path(list(granules.values())[0], root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.with_name(dest.name + ".part").write_bytes(b"")
    calls = []

    def flaky(url, path):
        from pathlib import Path

        path = Path(path)
        part = path.with_name(path.name + ".part")
        calls.append(path.name)
        if not part.stat().st_size:                       # first attempt: leave a partial
            part.write_bytes(b"half a granule")
            raise TimeoutError("interrupted")
        part.write_bytes(Path(url).read_bytes())          # resume completes it
        part.rename(path)
        return path

    out = fetch([day], root=root, granules=granules, workers=1, downloader=flaky)
    assert out["fetched"] == 0 and len(calls) == 1             # error recorded, nothing cached
    assert "interrupted" in list(out["errors"].values())[0]
    out2 = fetch([day], root=root, granules=granules, workers=1, downloader=flaky)
    assert out2["fetched"] == 1 and out2["bytes"] > 0         # second run resumes the .part


def test_load_rejects_mixed_grids_and_empty_window(cache):
    day = DAY1
    remote, granules = fake_remote(tmp_path=cache, days=[day])
    root = cache / "root" / "mixed"
    ingest(day, day, root=root, granules=granules, workers=1, downloader=copyfile_downloader,
           grid_deg=DEG)
    other = root / "daily" / f"imerg_{day + dt.timedelta(days=1):%Y%m%d}.nc"
    ds = xr.open_dataset(root / "daily" / f"imerg_{day:%Y%m%d}.nc").load()
    ds = ds.assign_coords(lat=ds.lat.values + 0.5)          # same shape, different lattice
    ds["precipitation"].encoding.clear()
    for v in ("precipitation_cnt", "probabilityLiquidPrecipitation"):
        ds[v].encoding.clear()
    ds["lat"].encoding.clear()
    ds.to_netcdf(other, engine="h5netcdf")
    with pytest.raises(ImergError, match="grid differs"):
        load_imerg_daily(day, day + dt.timedelta(days=1), root=root)
    with pytest.raises(ImergError, match="no IMERG daily fields"):
        load_imerg_daily(dt.date(2020, 1, 1), dt.date(2020, 1, 2), root=root)


# ------------------------------------------------------------------ rolling window


def test_days_arg_window_math():
    """Regression: the CLI used ``date.fromtimestamp(ts, tz)``, which does not exist
    (only ``datetime.fromtimestamp`` takes a tz) — every CLI invocation died on it."""
    now = dt.datetime(2026, 11, 17, 3, 30, tzinfo=dt.timezone.utc)
    assert imerg._days_arg(argparse.Namespace(days=7, start=None, end=None), now) \
        == (dt.date(2026, 11, 11), dt.date(2026, 11, 17))
    assert imerg._days_arg(argparse.Namespace(days=90, start="2026-01-01", end="2026-01-05"),
                           now) == (dt.date(2026, 1, 1), dt.date(2026, 1, 5))


def test_daily_file_round_trips_grid_attr(tmp_path):
    """Regression: the per-file grid summary was a dict, which h5netcdf refuses to write,
    so *no* daily file could be produced. It must write, reopen and stay parseable."""
    day = DAY1
    src = write_granule(tmp_path / granule_name(day), day, rate=0.5)
    gran = Granule(day=day, granule_ur=Path(src).name, url=src, size_mb=26.5)
    out = imerg.ingest_day(gran, day, root=tmp_path / "root", grid_deg=DEG)
    assert out["status"] == "present"
    path = imerg.daily_path(day, tmp_path / "root")
    assert path.exists() and path.stat().st_size > 0
    with xr.open_dataset(path) as ds:
        assert ds["precipitation"].attrs["units"] == imerg.UNITS
        grid = json.loads(ds.attrs["grid"])
        assert grid["nlat"] == _shape()[0] and grid["nlon"] == _shape()[1]
        assert ds["precipitation"].values[0, 0, 0] == pytest.approx(12.0)


def test_daily_store_packs_precipitation_to_uint16(tmp_path):
    """The daily field is the only persisted state (keep-raw 0 by default), so it must be
    packed uint16 @ 0.01 mm — half the float32 footprint on a 99 %-full root FS — and the
    loader must undo the packing exactly (to the quantisation step) with fill → NaN."""
    day = DAY1
    src = write_granule(tmp_path / granule_name(day), day, rate=0.5, fill_cells=((1, 2),))
    imerg.ingest_day(Granule(day=day, granule_ur=Path(src).name, url=src), day,
                     root=tmp_path / "root", grid_deg=DEG)
    with xr.open_dataset(imerg.daily_path(day, tmp_path / "root"),
                         mask_and_scale=False) as ds:
        v = ds["precipitation"]
        assert str(v.dtype) == "uint16"
        assert float(v.attrs["scale_factor"]) == pytest.approx(imerg.PP_SCALE_MM)
        assert int(v.attrs["_FillValue"]) == imerg.PP_FILL
    arr = imerg.load_imerg_daily(day, day, root=tmp_path / "root").values
    assert np.isnan(arr[0, 1, 2])                       # fill → NaN, never a silent 0
    wet = arr[~np.isnan(arr)]
    assert np.allclose(wet, 12.0, atol=imerg.PP_SCALE_MM)


def test_ingest_clips_precipitation_at_packing_ceiling(tmp_path):
    """Packed uint16 tops out at 655.34 mm/day: a hotter cell must clip (recorded in
    status) rather than wrap or corrupt the file."""
    day = DAY1
    src = tmp_path / granule_name(day)
    write_granule(src, day, rate=0.2)
    with xr.open_dataset(src, engine="h5netcdf", mask_and_scale=False) as ds:
        ds = ds.load()
    v = ds["precipitation"].values
    v[0, 1, 1] = 40.0                                    # 960 mm/day > PP_MAX_MM
    ds["precipitation"].values = v
    ds.to_netcdf(src, engine="h5netcdf", mode="w", format="NETCDF4")
    ds.close()
    out = imerg.ingest_day(Granule(day=day, granule_ur=src.name, url=str(src)), day,
                           root=tmp_path / "root", grid_deg=DEG)
    assert out["clipped_at_max"] is True
    da = imerg.load_imerg_daily(day, day, root=tmp_path / "root")
    assert float(da.values[0, 1, 1]) == pytest.approx(imerg.PP_MAX_MM, abs=imerg.PP_SCALE_MM)


def test_roll_window_drops_outside_90_days(cache):
    root = cache / "root"
    now = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc)
    days = [now.date() - dt.timedelta(days=i) for i in range(99, -1, -1)]   # 100 days, ends today
    remote, granules = fake_remote(tmp_path=cache, days=days)

    def copier_simple(url, dest):
        return copyfile_downloader(url, dest)

    for day in days:                                        # one day at a time, cheap
        ingest(day, day, root=root, granules={day: granules[day]}, workers=1,
               downloader=copier_simple, keep_raw=99, keep_days=3650, grid_deg=DEG,
               min_free=0)
    assert len(list((root / "daily").glob("imerg_*.nc"))) == 100

    out = roll_window(root=root, keep_days=90, keep_raw=3, now=now)
    assert out["cutoff"] == "2026-08-19"
    kept = sorted(p.stem for p in (root / "daily").glob("imerg_*.nc"))
    assert len(kept) == 90 and kept[-1] == "imerg_20261116"
    assert kept[0] == "imerg_20260819" and "imerg_20260818" not in kept
    assert out["freed_bytes"] > 0
    status = load_imerg_status(days[0], days[0], root=root)          # entry pruned away
    assert status["status"].iloc[0] == "not_ingested"
    raw_days = sorted(imerg._DAY_IN_NAME.search(p.name).group(1)
                      for p in (root / "raw").rglob("*.nc4"))
    assert raw_days == ["20261113", "20261114", "20261115", "20261116"]
    dry = roll_window(root=root, keep_days=1, keep_raw=0, now=now, dry_run=True)
    assert dry["removed"] and len(list((root / "daily").glob("imerg_*.nc"))) == 90


# ------------------------------------------------------------- disk headroom guard


def test_project_window_peaks_per_batch():
    days = [DAY1 + dt.timedelta(days=i) for i in range(10)]
    granules = {d: Granule(day=d, granule_ur=f"x{d:%Y%m%d}.nc4", url="u", size_mb=26.5)
                for d in days}
    p = imerg.project_window(days, granules, batch_days=5)
    assert p["batches"] == 2 and p["batch_days"] == 5
    assert p["peak_raw"] == pytest.approx(5 * 26.5e6)          # one batch, not the window
    assert p["raw_total"] == pytest.approx(10 * 26.5e6)
    assert p["net"] == p["peak_raw"] + 10 * imerg.EST_DAILY_BYTES


def test_headroom_guard_refuses_before_downloading(cache, monkeypatch):
    """The ewb LXC root FS is 98-100 % full and hosts the live service: an ingest that
    cannot fit must fail up front, without writing a single byte to the cache."""
    monkeypatch.setattr(imerg, "free_bytes",
                        lambda root=None: imerg.MIN_FREE_BYTES + 10_000_000)
    with pytest.raises(ImergError, match="not enough free space"):
        imerg.require_headroom(cache, need=80_000_000)
    assert imerg.require_headroom(cache, need=1_000_000) > 0

    days = [DAY1, DAY1 + dt.timedelta(days=1)]
    _, granules = fake_remote(tmp_path=cache, days=days)
    called = []

    def never(url, dest):
        called.append(url)
        return copyfile_downloader(url, dest)

    with pytest.raises(ImergError, match="not enough free space"):
        ingest(DAY1, days[-1], root=cache / "root", granules=granules, workers=1,
               downloader=never, grid_deg=DEG)
    assert called == [] and not (cache / "root").exists()


def test_ingest_batches_roll_raw_between_them(cache):
    """Peak disk use is bounded by one batch of granules, not the window: with
    ``keep_raw=0`` the raw cache must be empty even though 10 days were fetched."""
    days = [dt.date(2026, 11, 10) + dt.timedelta(days=i) for i in range(10)]
    _, granules = fake_remote(tmp_path=cache, days=days)
    root = cache / "root"
    calls = []

    def counting(url, dest):
        calls.append(url)
        return copyfile_downloader(url, dest)

    out = ingest(days[0], days[-1], root=root, granules=granules, workers=1,
                 downloader=counting, grid_deg=DEG, keep_raw=0, keep_days=90, batch_days=3,
                 now=dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc), min_free=0)
    assert out["fetch"]["fetched"] == 10
    assert len(calls) == 10
    assert len(list((root / "daily").glob("imerg_*.nc"))) == 10
    assert not list((root / "raw").rglob("*.nc4"))            # rolled between batches


# --------------------------------------------------- AWS Open Data S3 transport plumbing


def test_survey_query_is_one_cmr_accepts(monkeypatch):
    """Regression: CMR answers HTTP 400 "Parameter [sortable] was not recognized" for the
    IMERG daily collections, which made every survey fail before a single granule list."""
    seen = []

    def fake_get(url, opener=None, **kw):
        seen.append(url)
        return json.dumps({"hits": 0, "items": []}).encode()

    monkeypatch.setattr(imerg, "_http_get", fake_get)
    assert imerg.survey(DAY1, DAY1, product="late") == {}
    assert len(seen) == 1 and "sortable" not in seen[0]
    assert "short_name=GPM_3IMERGDL" in seen[0] and "temporal=" in seen[0]


def test_preemptive_auth_handler_only_targets_the_auth_hosts():
    """Regression: the first *real* request (auth-check on the LXC) died with
    ``AttributeError: 'Request' object has no attribute 'fullurl'`` — the stdlib
    attribute is ``full_url``. Every offline test monkeypatches ``_opener``/``_http_get``,
    so this handler only ever runs against live Earthdata; exercise it here directly."""
    op = imerg._opener(("user", "pw"))
    handler = next(h for h in op.handlers
                   if hasattr(h, "https_request")
                   and type(h).__module__ == imerg.__name__)

    req = handler.https_request(urllib.request.Request(
        "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/x.nc4"))
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(
        b"user:pw").decode()

    away = handler.https_request(urllib.request.Request("https://example.com/x.nc4"))
    assert "Authorization" not in away.headers

    kept = handler.https_request(urllib.request.Request(
        "https://data.gesdisc.earthdata.nasa.gov/s3credentials",
        headers={"Authorization": "Bearer x"}))
    assert kept.headers["Authorization"] == "Bearer x"   # never overwrite an existing one


def test_s3_key_maps_a_gesdisc_url_to_the_mirror_key():
    url = ("https://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/GPM_3IMERGHHL.07/2026/319/"
           "3B-HHR-L.MS.MRG.3IMERG.20261115-S000000-E002959.0000.V07C.HDF5")
    assert imerg.s3_key(url) == ("GPM_L3/GPM_3IMERGHHL.07/2026/319/"
                                 "3B-HHR-L.MS.MRG.3IMERG.20261115-S000000-E002959.0000.V07C.HDF5")
    assert imerg.s3_key("s3://gesdisc-cumulus-prod-protected/GPM_L3/x.nc4") == "GPM_L3/x.nc4"


def test_s3_credentials_parses_and_caches(monkeypatch):
    calls = []

    def fake_get(url, opener=None, **kw):
        calls.append(url)
        return json.dumps({"access_key_id": "AK", "secret_access_key": "SK",
                           "session_token": "ST",
                           "expiration": "2030-01-01 00:00:00 UTC",
                           "region": "us-west-2", "bucket": "gesdisc-cumulus-prod-protected"
                           }).encode()

    monkeypatch.setattr(imerg, "_http_get", fake_get)
    monkeypatch.setattr(imerg, "_S3_CACHE", {"creds": None, "fs": None, "expires": 0.0})
    creds = imerg.s3_credentials(("user", "pw"))
    assert creds["access_key_id"] == "AK" and creds["region"] == "us-west-2"
    assert imerg.s3_credentials(("user", "pw")) is creds        # cached, no second call
    assert len(calls) == 1
    monkeypatch.setattr(imerg, "_S3_CACHE", {"creds": None, "fs": None, "expires": 0.0})
    assert imerg.s3_credentials(None) is None                   # no login configured at all

    def boom(url, opener=None, **kw):
        raise ImergError("nope")

    monkeypatch.setattr(imerg, "_http_get", boom)
    monkeypatch.setattr(imerg, "_S3_CACHE", {"creds": None, "fs": None, "expires": 0.0})
    with pytest.raises(ImergError, match="nope"):
        imerg.s3_credentials(("user", "pw"))


def test_download_route_prefers_s3_and_falls_back(monkeypatch, tmp_path):
    tried = []
    good = tmp_path / "granule.nc4"
    write_granule(good, DAY1)

    def s3_fail(url, dest, creds, *, overwrite=False):
        tried.append("s3")
        raise ImergError("AccessDenied")

    def https_ok(url, dest, creds, *, overwrite=False):
        tried.append("https")
        import shutil

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(good, dest)
        return dest

    monkeypatch.setattr(imerg, "_get_s3", s3_fail)
    monkeypatch.setattr(imerg, "_get_https", https_ok)
    dest = tmp_path / "out" / "g.nc4"
    out = imerg.download_granule("https://example/g.nc4", dest, creds=("u", "p"),
                                 log=lambda m: None)
    assert tried == ["s3", "https"] and Path(out).exists()
    tried.clear()
    with pytest.raises(ImergError, match="AccessDenied"):
        imerg.download_granule("https://example/g.nc4", tmp_path / "x.nc4", creds=("u", "p"),
                               route="s3")
    assert tried == ["s3"]


def test_https_route_rejects_a_non_granule_body(monkeypatch, tmp_path):
    class Resp:
        """A well-behaved response: a finite body, then EOF (``b""``). Stubs that omit the
        EOF turn ``copyfileobj`` into an infinite writer — see the cap test below."""

        status = 200

        def __init__(self):
            self._left = [b"<html>Earthdata Login</html>"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return self._left.pop(0) if self._left else b""

    class Opener:
        def open(self, req, timeout=None):
            return Resp()

    monkeypatch.setattr(imerg, "_opener", lambda creds: Opener())
    monkeypatch.setattr(imerg, "DOWNLOAD_TRIES", 1)
    with pytest.raises(ImergError, match="did not return a NetCDF/HDF5 granule"):
        imerg._get_https("https://example/g.nc4", tmp_path / "g.nc4", ("u", "p"))
    assert not (tmp_path / "g.nc4.part").exists()


def test_https_route_caps_a_body_that_never_ends(monkeypatch, tmp_path):
    """Regression for the run that filled /tmp with 140 GB: a ``read()`` that never
    reaches EOF must be stopped by the size cap, after at most ``cap`` bytes — and the
    junk ``.part`` must be deleted so the next run cannot resume it."""

    class Endless:
        status = 200

        def __init__(self):
            self.bytes_read = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            n = 1 << 20 if n and n > 0 else 1
            self.bytes_read += n
            assert self.bytes_read < 64 << 20, "copy loop is unbounded"
            return b"x" * n

    class Opener:
        def open(self, req, timeout=None):
            return Endless()

    monkeypatch.setattr(imerg, "_opener", lambda creds: Opener())
    monkeypatch.setattr(imerg, "DOWNLOAD_TRIES", 1)
    monkeypatch.setattr(imerg, "MAX_GRANULE_BYTES", 4 << 20)
    part = tmp_path / "g.nc4.part"
    with pytest.raises(ImergError, match="granule size cap"):
        imerg._get_https("https://example/g.nc4", tmp_path / "g.nc4", ("u", "p"))
    assert not part.exists()
    assert not any(p.stat().st_size > 4 << 20 for p in tmp_path.iterdir())


def test_read_capped_bounds_a_response_body():
    class Finite:
        def __init__(self):
            self._left = [b"a" * 1024, b"b"]

        def read(self, n=-1):
            return self._left.pop(0) if self._left else b""

    class Endless:
        def __init__(self):
            self.n = 0

        def read(self, n=-1):
            self.n += 1
            assert self.n * 1024 < 1 << 20, "read loop is unbounded"
            return b"x" * 1024

    assert imerg._read_capped(Finite(), 4096, "u") == b"a" * 1024 + b"b"
    with pytest.raises(ImergError, match="not a valid response"):
        imerg._read_capped(Endless(), 4096, "https://example/x")


# ----------------------------------------------------------- run policy (auto) + upgrade


def test_survey_window_prefers_late_and_fills_the_head(monkeypatch):
    # 'auto' is a run policy, not a collection: survey() must refuse it (checked on the
    # real function, before it is replaced below)
    with pytest.raises(ImergError, match="run policy"):
        imerg.survey(DAY1, DAY1, product="auto")

    late = {DAY1: Granule(day=DAY1, granule_ur="l1.nc4", url="s/l1.nc4", product="late")}
    early = {DAY1: Granule(day=DAY1, granule_ur="e1.nc4", url="s/e1.nc4", product="early"),
             DAY1 + dt.timedelta(days=1): Granule(day=DAY1 + dt.timedelta(days=1),
                                                  granule_ur="e2.nc4", url="s/e2.nc4",
                                                  product="early")}

    def fake_survey(start, end, *, product="early"):
        return {"late": late, "early": early}[product]

    monkeypatch.setattr(imerg, "survey", fake_survey)
    chosen, missing = imerg.survey_window(DAY1, DAY1 + dt.timedelta(days=2), product="auto")
    assert chosen[DAY1].product == "late" and chosen[DAY1].granule_ur == "l1.nc4"
    assert chosen[DAY1 + dt.timedelta(days=1)].product == "early"
    assert [f"{d:%Y-%m-%d}" for d in missing] == [f"{DAY1 + dt.timedelta(days=2):%Y-%m-%d}"]


def test_auto_policy_upgrades_an_early_head_day_to_late(cache):
    day = DAY1
    early_remote = Path(write_granule(cache / "early" / granule_name(day), day, rate=0.2))
    late_remote = Path(write_granule(cache / "late" / granule_name(day), day, rate=0.3))
    root = cache / "root"

    def run(granule):
        def dl(url, dest):
            return copyfile_downloader(url, dest)
        ingest(day, day, root=root, granules={day: granule}, workers=1, downloader=dl,
               keep_raw=99, keep_days=3650, grid_deg=DEG)

    run(Granule(day=day, granule_ur=early_remote.name, url=str(early_remote), product="early"))
    assert load_imerg_daily(day, day, root=root).values[0, 0, 0] == pytest.approx(4.8)
    assert load_imerg_status(day, day, root=root)["product"].iloc[0] == "early"

    run(Granule(day=day, granule_ur=late_remote.name, url=str(late_remote), product="late"))
    da = load_imerg_daily(day, day, root=root)
    assert da.values[0, 0, 0] == pytest.approx(7.2)          # rebuilt from the Late granule
    assert da.attrs["products"] == {"late": 1}
    assert load_imerg_status(day, day, root=root)["product"].iloc[0] == "late"


# ----------------------------------------------------------------- units plausibility band


def test_units_band_refuses_a_24x_misread(tmp_path):
    too_big = write_granule(tmp_path / "big.nc4", DAY1, rate=10.0, units="mm/hr")
    with pytest.raises(ImergError, match="plausible band"):
        daily_from_granule(too_big, grid_deg=DEG)
    too_small = write_granule(tmp_path / "small.nc4", DAY1, rate=0.001, units="mm/day")
    with pytest.raises(ImergError, match="plausible band"):
        daily_from_granule(too_small, grid_deg=DEG)
    ok = write_granule(tmp_path / "ok.nc4", DAY1, rate=0.1, units="mm/hr")
    out = daily_from_granule(ok, grid_deg=DEG)
    assert out["precipitation"].values.mean() == pytest.approx(2.4)
    assert out.attrs["global_mean_mm_day"] == pytest.approx(2.4, abs=0.01)
