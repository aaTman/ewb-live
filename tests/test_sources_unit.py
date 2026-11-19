"""Unit tests for ewb_live.sources (no network)."""

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from ewb_live import sources
from ewb_live.sources import _transform, cache
from ewb_live.sources.registry import LEVEL_VARS_EWB, models


def test_import_pure_and_list_models():
    ms = sources.list_models()
    assert "ifs_hres" in ms
    assert "graphcast" in ms
    assert ms == sorted(ms)
    for m in ms:
        spec = sources.model_info(m)
        assert spec.variables
        assert spec.level_variables == frozenset(spec.variables) & LEVEL_VARS_EWB


def test_model_info_unknown():
    with pytest.raises(KeyError):
        sources.model_info("does_not_exist")


def test_open_forecast_bad_variable():
    with pytest.raises(ValueError, match="not available"):
        sources.open_forecast("ifs_hres", dt.datetime(2026, 1, 1), ["not_a_variable"])


def test_open_forecast_empty_variables():
    with pytest.raises(ValueError):
        sources.open_forecast("ifs_hres", dt.datetime(2026, 1, 1), [])


def test_lon_indices_wrap():
    lon = np.array([-179.0, -1.0, 0.0, 1.0, 179.0])
    m = _transform.lon_indices(lon, -10.0, 10.0)
    assert list(lon[m]) == [-1.0, 0.0, 1.0]
    # wrapped box crossing the antimeridian
    m = _transform.lon_indices(lon, 170.0, -170.0)
    assert list(lon[m]) == [-179.0, 179.0]


def test_normalize_longitude():
    da = xr.DataArray(
        np.zeros((1, 4)),
        dims=("latitude", "longitude"),
        coords={"latitude": [0.0], "longitude": [0.0, 90.0, 180.0, 270.0]},
    )
    # name the lat dim properly
    da = da.rename({"latitude": "latitude"})
    out = _transform.normalize_longitude(da.expand_dims(lead=[0]).squeeze("latitude"))
    # 270 -> -90, sorted ascending
    assert list(out.longitude.values) == [-90.0, 0.0, 90.0, 180.0] or list(
        out.longitude.values
    ) == [-90.0, -0.0, 90.0, 180.0]


def _toy_da():
    lat = np.arange(90.0, -91.0, -1.0)
    lon = np.arange(0.0, 360.0, 2.0)
    lead = np.arange(3)
    return xr.DataArray(
        np.random.default_rng(0).standard_normal((3, len(lat), len(lon))),
        dims=("lead", "latitude", "longitude"),
        coords={"lead": lead, "latitude": lat, "longitude": lon},
    )


def test_assemble_conventions():
    da = _toy_da()
    ds = _transform.assemble(
        {"surface_air_temperature": da},
        np.datetime64("2026-11-01T00", "ns"),
        [0, 12, 24],
        (20.0, 40.0, -120.0, -80.0),
    )
    assert set(ds["surface_air_temperature"].dims) == {"init_time", "lead_time", "latitude", "longitude"}
    assert np.issubdtype(ds.lead_time.dtype, np.timedelta64)
    assert ds.lead_time.values[1] == np.timedelta64(12, "h")
    assert ds.init_time.values[0] == np.datetime64("2026-11-01T00")
    assert ds.longitude.values.min() >= -180 and ds.longitude.values.max() <= 180
    assert ds.latitude.values.min() >= 20 and ds.latitude.values.max() <= 40
    assert -120 <= ds.longitude.values.min() and ds.longitude.values.max() <= -80


def test_assemble_level_dim():
    da = _toy_da().expand_dims(level=[1000, 500]).transpose("lead", "level", "latitude", "longitude")
    ds = _transform.assemble({"eastward_wind": da}, np.datetime64("2026-11-01T00", "ns"), [0, 12, 24], None)
    assert set(ds["eastward_wind"].dims) == {"init_time", "lead_time", "level", "latitude", "longitude"}
    assert ds.level.attrs["units"] == "hPa"


def test_prune_cache(tmp_path):
    root = tmp_path / "subsets"
    old = root / "graphcast" / "2026010100Z" / "abc"
    new = root / "graphcast" / "2026102000Z" / "def"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "x.bin").write_bytes(b"0" * 100)
    (new / "x.bin").write_bytes(b"0" * 100)
    res = cache.prune_cache(120, now=dt.datetime(2026, 11, 6), cache_root=str(root))
    assert not old.exists()
    assert new.exists()
    assert res["freed_bytes"] == 100
    assert len(res["removed"]) == 1


def test_prune_cache_missing_root(tmp_path):
    assert cache.prune_cache(cache_root=str(tmp_path / "nope")) == {"removed": [], "freed_bytes": 0}


def test_open_forecast_cache_roundtrip(tmp_path, monkeypatch):
    """Zarr cache write + hit path, with a fake backend (no network)."""
    import numpy as np
    import xarray as xr

    from ewb_live.sources import _net, api

    calls = {"n": 0}

    def fake_open(spec, init, variables, leads, bbox):
        calls["n"] += 1
        return _transform.assemble(
            {
                "surface_air_temperature": xr.DataArray(
                    np.full((len(leads), 3, 4), 300.0),
                    dims=("lead", "latitude", "longitude"),
                    coords={
                        "lead": range(len(leads)),
                        "latitude": [10.0, 0.0, -10.0],
                        "longitude": [0.0, 90.0, 180.0, 270.0],
                    },
                )
            },
            np.datetime64(init, "ns"),
            list(leads),
            bbox,
        )

    monkeypatch.setitem(api._BACKENDS, "aiwp", type("B", (), {"open_forecast": staticmethod(fake_open), "available_inits": staticmethod(lambda s, a, b: [])})())
    monkeypatch.setattr(_net, "CACHE_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "_cache_key", lambda *a: "k")
    # cache_path() reads _net.CACHE_ROOT at call time, so patching it redirects the cache
    init = dt.datetime(2026, 11, 1)
    ds1 = api.open_forecast("graphcast", init, ["surface_air_temperature"], [0, 6], None)
    assert calls["n"] == 1
    ds2 = api.open_forecast("graphcast", init, ["surface_air_temperature"], [0, 6], None)
    assert calls["n"] == 1  # served from cache
    assert float(ds2.surface_air_temperature.mean()) == 300.0
    api.open_forecast("graphcast", init, ["surface_air_temperature"], [0, 6], None, use_cache=False)
    assert calls["n"] == 2  # bypass re-reads


def test_registry_matches_ewb_names():
    from extremeweatherbench import defaults as ewb_defaults

    names = set(ewb_defaults.DEFAULT_VARIABLE_NAMES)
    for m in models().values():
        assert set(m.variables) <= names, m.name


# ------------------------------------------------ open_forecast_nearest_init
def _fake_archive(monkeypatch, cadence, published):
    """api.open_forecast / api.available_inits backed by in-memory sets.

    `cadence`  = the inits available_inits reports (inclusive of both ends)
    `published`= {(init, leads)} the fake archive can actually serve
    Returns the list of (init, leads) open_forecast was called with.
    """
    from ewb_live.sources import api

    calls: list[tuple[dt.datetime, tuple[int, ...]]] = []

    def fake_open(model, init, variables, lead_hours=None, bbox=None,
                  use_cache=True):
        leads = tuple(lead_hours or ())
        calls.append((init, leads))
        if (init, leads) not in published:
            raise FileNotFoundError(f"no archive for {init} {list(leads)}")
        return {"init": init, "leads": leads}

    monkeypatch.setattr(api, "open_forecast", fake_open)
    monkeypatch.setattr(api, "available_inits",
                        lambda model, start, end: [i for i in cadence
                                                   if start <= i <= end])
    return calls


def test_nearest_init_falls_back_to_the_nearest_earlier_init(monkeypatch):
    """The fallback must actually try EARLIER inits (AR-view regression).

    AIWP archives publish 00Z/12Z only; AR anchors are 6-hourly, so an AR event
    page asks for a 06Z/18Z init and can only be served by the earlier run. The
    guard around the fallback's `return` read `if cand >= init:` — and since
    available_inits is inclusive of the requested init, that fired only for the
    init that had just 404'd, so every earlier candidate was skipped and the
    loop raised. Every model panel on the AR view read "unavailable (archive
    missing for this run)": 49 of 121 ok on the served set, 2026-11-17.
    """
    from ewb_live.sources import api

    day = dt.datetime(2026, 8, 28)
    calls = _fake_archive(monkeypatch, [day, day + dt.timedelta(hours=12)],
                          {(day, (30,))})
    ds, off = api.open_forecast_nearest_init(
        "aurora", day + dt.timedelta(hours=6), ["specific_humidity"],
        lead_hours=[24], window_h=6)
    assert off == 6                      # 06Z served by the 00Z run
    assert ds["init"] == day
    assert calls[-1] == (day, (30,))     # lead + offset => the SAME valid time


def test_nearest_init_prefers_the_exact_init(monkeypatch):
    from ewb_live.sources import api

    day = dt.datetime(2026, 8, 28)
    calls = _fake_archive(monkeypatch, [day, day + dt.timedelta(hours=12)],
                          {(day, (24,)), (day - dt.timedelta(hours=6), (30,))})
    ds, off = api.open_forecast_nearest_init(
        "aurora", day, ["specific_humidity"], lead_hours=[24], window_h=6)
    assert (off, ds["init"]) == (0, day)
    assert calls == [(day, (24,))]       # no fallback when the run exists


def test_nearest_init_raises_when_nothing_earlier_is_published(monkeypatch):
    from ewb_live.sources import api

    day = dt.datetime(2026, 8, 28)
    # the requested init 404s and the only earlier cadence slot is outside the
    # 6 h window: the original FileNotFoundError must surface unchanged
    calls = _fake_archive(monkeypatch,
                          [day - dt.timedelta(hours=12), day],
                          {(day - dt.timedelta(hours=12), (36,))})
    with pytest.raises(FileNotFoundError):
        api.open_forecast_nearest_init("aurora", day, ["specific_humidity"],
                                       lead_hours=[24], window_h=6)
    assert calls == [(day, (24,))]
