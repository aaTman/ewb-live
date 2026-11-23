"""Offline tests for fc_cache region keys, incl. antimeridian-wrapping boxes.

Wrapped AR boxes used to return _region_key=None and therefore bypassed the
fc_parts disk cache entirely (no resume reuse on the weekly). The key now
pads the UNWRAPPED arc and stays wrapped; every consumer resolves wrapped
boxes through _transform.lon_indices, so fetch -> zarr -> re-cut round-trips.
"""

import datetime as dt

import numpy as np
import xarray as xr

from ewb_live.scoring import fc_cache
from ewb_live.sources import _transform

WRAP_BBOX = (23.25, 65.75, 173.5, -139.5)
PLAIN_BBOX = (20.0, 40.0, -120.0, -80.0)


def test_region_key_plain_unchanged():
    # exact multiples of _PAD stay exact (floor/ceil are no-ops there)
    assert fc_cache._region_key(PLAIN_BBOX) == (20.0, 40.0, -120.0, -80.0)
    assert fc_cache._region_key((20.0, 40.0, -125.0, -85.0)) == (20.0, 40.0, -130.0, -80.0)


def test_region_key_wrapped_is_padded_and_still_wrapped():
    r = fc_cache._region_key(WRAP_BBOX)
    assert r == (20.0, 70.0, 170.0, -130.0)  # arc [170, 230) deg, wrapped form
    assert r[2] > r[3]


def test_region_key_wrapped_arc_padded_over_globe_is_none():
    # 355 deg wide wrapped arc: 10 deg padding would cover the globe
    assert fc_cache._region_key((0.0, 10.0, 175.0, 170.0)) is None


def test_region_key_wrapped_small_arc_stable_under_pad():
    # 5 deg wide arc, padding stays < 360 -> still keyed
    r = fc_cache._region_key((0.0, 10.0, 179.0, -175.0))
    assert r is not None and r[2] > r[3]
    # padded arc contains the requested arc
    lo, hi = r[2], r[3]
    assert _transform.lon_indices(np.array([179.0, 180.0, -180.0, -179.0, -175.0]),
                                  lo, hi).all()


def _fake_global_ds(leads):
    lat = np.arange(90.0, -90.01, -1.0)
    lon = np.arange(0.0, 360.0, 1.0)
    lt = np.array([np.timedelta64(h, "h") for h in leads])
    data = np.zeros((1, len(leads), len(lat), len(lon)))
    return xr.Dataset(
        {"surface_air_temperature": (("init_time", "lead_time", "latitude", "longitude"), data)},
        coords={"init_time": [np.datetime64("2026-11-14T00", "ns")],
                "lead_time": lt, "latitude": lat, "longitude": lon},
    )


def test_wrapped_box_roundtrips_through_cache(tmp_path, monkeypatch):
    """fetch(padded wrapped) -> zarr -> re-cut(exact wrapped) == direct subset."""
    leads = [0, 6]
    glob = _fake_global_ds(leads)
    calls = []

    def fake_open(model, init, variables, leads_arg, bbox):
        calls.append(bbox)
        # behave like the real backend: cut the global store to the requested bbox
        return _transform.subset_latlon(_transform.normalize_longitude(glob), bbox)

    monkeypatch.setenv("EWB_LIVE_CACHE", str(tmp_path))
    monkeypatch.setattr(fc_cache, "_cache_dir",
                        lambda: tmp_path / "fc_parts")
    import ewb_live.sources as sources
    monkeypatch.setattr(sources, "open_forecast", fake_open)

    out = fc_cache.open_forecast_cached("ifs_hres", dt.datetime(2026, 11, 14),
                                        ["surface_air_temperature"], leads, WRAP_BBOX)
    assert calls == [(20.0, 70.0, 170.0, -130.0)]  # padded wrapped fetch
    exp = _transform.subset_latlon(_transform.normalize_longitude(glob), WRAP_BBOX)
    np.testing.assert_array_equal(out.longitude.values, exp.longitude.values)
    np.testing.assert_array_equal(out.latitude.values, exp.latitude.values)

    # second call: served from the on-disk store, no refetch, same result
    out2 = fc_cache.open_forecast_cached("ifs_hres", dt.datetime(2026, 11, 14),
                                         ["surface_air_temperature"], leads, WRAP_BBOX)
    assert calls == [(20.0, 70.0, 170.0, -130.0)]
    np.testing.assert_array_equal(out2.longitude.values, out.longitude.values)
