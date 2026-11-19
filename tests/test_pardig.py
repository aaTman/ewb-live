"""PARDIG source unit tests — unit conversion, 15-min accumulation, lon wrap,
cache discipline, absent-day semantics, and the flood-lane source switch.

No network: remote reads are replaced by the EWB_PARDIG_DIR fixture /
monkeypatching, and the flood-lane join runs on the coarse global pair.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest
import xarray as xr

from ewb_live.sources import pardig as P

DAY = dt.date(2026, 10, 24)


# ---------------------------------------------------------------------------
# lattice + units
# ---------------------------------------------------------------------------

def test_constant_field_maps_to_centres_and_mm():
    """1 mm/h (= 0.001 m/h) sustained over 96 steps -> 24 mm/day: the input is
    the SUM of the 96 instantaneous rates (m/h), and mm/day = sum x 1000 x
    0.25 h; the node->centre midpoint pass maps a constant exactly."""
    acc = np.full((P.N_LAT, P.N_LON), 96 * 0.001, np.float32)
    out = P.nodes_to_centers_mm(acc)
    assert out.shape == (P.N_LAT - 1, P.N_LON)
    assert np.allclose(out, 24.0, atol=1e-4)


def test_accumulation_is_rate_times_quarter_hour():
    """2.5 mm/h all day = 60 mm/day."""
    acc = np.full((P.N_LAT, P.N_LON), 96 * 0.0025, np.float32)
    out = P.nodes_to_centers_mm(acc)
    assert np.allclose(out, 60.0, atol=1e-2)


def test_antimeridian_spike_splits_across_the_seam():
    """A node at lon 180.0 is shared by the +179.95 and -179.95 centres: the
    0..360 -> -180 frame fold must SPLIT it, never drop or double it."""
    b = np.zeros((P.N_LAT, P.N_LON), np.float32)
    b[:, 1800] = 1.0
    out = P.nodes_to_centers_mm(b)
    cols = np.nonzero(out[0])[0]
    assert set(cols) == {0, P.N_LON - 1}
    assert np.allclose(out[0, cols], 125.0)                  # half of 500 mm/day each


def test_greenwich_spike_is_not_split():
    """Node lon 0.0 sits at the CENTRE between -0.05/+0.05 columns — also split,
    but inside the lattice, not at the seam."""
    b = np.zeros((P.N_LAT, P.N_LON), np.float32)
    b[:, 0] = 1.0
    out = P.nodes_to_centers_mm(b)
    cols = np.nonzero(out[0])[0]
    assert set(cols) == {1799, 1800}


def test_frame_ts_are_the_96_steps_of_one_utc_day():
    ts = P.frame_ts(DAY)
    assert len(ts) == 96
    assert ts[0] == dt.datetime(2026, 10, 24, 0, 0)
    assert ts[-1] == dt.datetime(2026, 10, 24, 23, 45)
    assert all(t.date() == DAY for t in ts)


# ---------------------------------------------------------------------------
# build + cache
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_frames(monkeypatch, tmp_path):
    """read_day_frames replaced with a deterministic constant-rate day."""
    def fake(day, *, workers=12):
        acc = np.full((P.N_LAT, P.N_LON), 96 * 0.001, np.float32)   # 24 mm/day
        return acc, []
    monkeypatch.setattr(P, "read_day_frames", fake)
    return tmp_path


def test_build_daily_writes_025_cache_and_status(fake_frames):
    root = fake_frames / "pardig"
    row = P.build_daily(DAY, root)
    assert row["frames"] == 96
    p = P.daily_path(DAY, root)
    assert p.exists()
    ds = xr.open_dataset(p)
    assert ds.precip.attrs["units"] == "mm/day"
    assert ds.attrs["kind"] == "pardig" and ds.attrs["license"] == "CC BY 4.0"
    assert float(np.nanmax(ds.precip)) == pytest.approx(24.0, abs=1e-3)
    status = json.loads(P.status_path(root).read_text())
    assert status["days"][f"{DAY:%Y-%m-%d}"]["frames"] == 96


def test_holed_day_is_recorded_not_cached(monkeypatch, tmp_path):
    def fake(day, *, workers=12):
        acc = np.full((P.N_LAT, P.N_LON), 96 * 0.001, np.float32)
        return acc, [dt.datetime(2026, 10, 24, 12, 0)]        # one frame never published
    monkeypatch.setattr(P, "read_day_frames", fake)
    root = tmp_path / "pardig"
    row = P.build_daily(DAY, root)
    assert row["frames"] == 95
    assert not P.daily_path(DAY, root).exists()               # a hole is NOT a dry day
    assert DAY not in P.served_days(root)


def test_load_pardig_daily_absent_days_are_nan_not_zero(tmp_path):
    # cache two days as tiny-grid files; the third day is absent
    lats = np.array([10.0, 0.0, -10.0])
    lons = np.array([-170.0, -60.0, 100.0, 170.0])
    P.daily_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    for k, day in enumerate([dt.date(2026, 10, 24), dt.date(2026, 10, 26)]):
        vals = np.full((lats.size, lons.size), 10.0 + k, np.float32)
        xr.Dataset({"precip": (("latitude", "longitude"), vals, {"units": "mm/day"})},
                   coords={"latitude": lats, "longitude": lons}).to_netcdf(
            P.daily_path(day, tmp_path))
    da = P.load_pardig_daily(dt.date(2026, 10, 24), dt.date(2026, 10, 27), root=tmp_path)
    assert da.shape == (4, 3, 4)
    assert np.allclose(da.values[0], 10.0) and np.allclose(da.values[2], 11.0)
    assert np.isnan(da.values[1]).all() and np.isnan(da.values[3]).all()
    assert da.attrs["days_present"] == 2 and da.attrs["missing_days"] == \
        ["2026-10-25", "2026-10-27"]
    with pytest.raises(P.PardigError):
        P.load_pardig_daily(dt.date(2026, 10, 24), dt.date(2026, 10, 27),
                            root=tmp_path, require_all=True)


def test_load_pardig_daily_empty_window_raises(tmp_path):
    with pytest.raises(P.PardigError):
        P.load_pardig_daily(dt.date(2026, 1, 1), dt.date(2026, 1, 2), root=tmp_path)


def test_prune_drops_stale_days(fake_frames):
    root = fake_frames / "p2"
    for k in range(6):
        P.build_daily(dt.date(2026, 10, 1) + dt.timedelta(days=k), root)
    assert P.prune(root, keep_days=3) == 3
    assert len(P.served_days(root)) == 3


# ---------------------------------------------------------------------------
# flood-lane source switch
# ---------------------------------------------------------------------------

def _tiny_pardig_cache(root, days, value=30.0):
    lats = np.array([20.0, 0.0, -20.0])
    lons = np.array([-120.0, 0.0, 120.0])
    P.daily_dir(root).mkdir(parents=True, exist_ok=True)
    for d in days:
        vals = np.full((3, 3), value, np.float32)
        xr.Dataset({"precip": (("latitude", "longitude"), vals, {"units": "mm/day"})},
                   coords={"latitude": lats, "longitude": lons}).to_netcdf(
            P.daily_path(d, root))


def test_load_observations_pardig_default_and_provenance(monkeypatch, tmp_path):
    from ewb_live.events import flood as F
    monkeypatch.delenv("EWB_FLOOD_SOURCE", raising=False)
    days = [dt.date(2026, 10, 24), dt.date(2026, 10, 25)]
    _tiny_pardig_cache(tmp_path, days)
    obs = F.load_observations(days[0], days[-1], root=tmp_path, chunk_days=10)
    assert obs.daily.shape == (2, 3, 3)
    assert obs.source["kind"] == "pardig"
    assert "PARDIG" in obs.source["product"] and obs.source["license"] == "CC BY 4.0"
    assert obs.runs[f"{days[0]:%Y-%m-%d}"] == "nrt"
    assert obs.source["missing_days"] == []
    assert "PARDIG" in obs.source["display"]


def test_load_observations_env_selects_source(monkeypatch, tmp_path):
    from ewb_live.events import flood as F
    monkeypatch.setenv("EWB_FLOOD_SOURCE", "imerg")
    with pytest.raises(Exception) as e:                    # IMERG store is absent here
        F.load_observations(dt.date(2026, 10, 24), dt.date(2026, 10, 25), root=tmp_path)
    assert "IMERG" in str(e.value) or "imerg" in str(e.value).lower()
    monkeypatch.setenv("EWB_FLOOD_SOURCE", "nope")
    with pytest.raises(ValueError):
        F.load_observations(dt.date(2026, 10, 24), dt.date(2026, 10, 25), root=tmp_path)


def test_flood_index_block_names_its_source():
    from ewb_live.viz import flood_frames as ff
    blk = ff.flood_index_block(["20261024"], [], {"20261024": "nrt"}, "pardig")
    assert blk["source"] == "pardig"
    assert "PARDIG" in blk["rule"] and "PARDIG" in blk["source_label"]
    assert "CC BY 4.0" in blk["tip"]
    blk2 = ff.flood_index_block(["20261024"], [], {"20261024": "late"}, "imerg")
    assert "IMERG" in blk2["rule"] and blk2["source"] == "imerg"
