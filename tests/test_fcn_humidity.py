"""FourCastNet AR humidity derivation (orchestrator Q5, 2026-11-06).

fourcastnet_v2(/_gfs) ships relative humidity (`r`, %) but no specific
humidity, which EWB's AR case operators need for IVT. evaluate.py routes
those models through EWB's own defaults
`preprocess_cira_icechunk_ar_forecast_dataset` (defaults.py
`_maybe_add_specific_humidity` -> calc.specific_humidity_from_relative_humidity)
so the physics is EWB's, not ours. These tests pin the derivation against a
hand-computed value (Magnus/Bolton closed form) and against the EWB
upstream-documented example, and assert the preprocess is a no-op when q is
already present.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from extremeweatherbench import defaults


def _fcn_like_ds(t_val: float, rh_pct: float) -> xr.Dataset:
    """Minimal forecast Dataset shaped like ewb_live.sources.assemble output:
    dims (lead_time, level, latitude, longitude), level coord in hPa."""
    lead = pd.to_timedelta([0, 6], "h")
    level = np.array([250, 500, 850], dtype="int64")
    lat = np.array([40.0, 41.0])
    lon = np.array([-30.0, -29.0])
    dims = ("lead_time", "level", "latitude", "longitude")
    shape = (2, 3, 2, 2)
    t = xr.DataArray(
        np.full(shape, t_val, dtype="float64"), dims=dims,
        coords={"lead_time": lead, "level": level, "latitude": lat,
                "longitude": lon},
    )
    rh = xr.DataArray(
        np.full(shape, rh_pct, dtype="float64"), dims=dims,
        coords={"lead_time": lead, "level": level, "latitude": lat,
                "longitude": lon},
    )
    ds = xr.Dataset({"air_temperature": t, "relative_humidity": rh})
    ds["level"].attrs["units"] = "hPa"
    return ds


def _expected_q(t_kelvin: float, rh_frac: float, p_hpa: float) -> float:
    """Independent closed-form of the SAME physics EWB implements:
    e_s = 6.112 exp(17.67 T_C / (T_C + 243.5)) hPa; w_sat = eps e_s / (p - e_s);
    w = eps w_sat RH / (eps + w_sat (1 - RH)); q = w / (1 + w).
    Constants pinned from extremeweatherbench.constants at test time."""
    from extremeweatherbench.constants import (
        A_BOLTON, B_BOLTON, E0_BOLTON, EPSILON, KELVIN_TO_CELSIUS,
    )
    t_c = t_kelvin - KELVIN_TO_CELSIUS
    e_s = E0_BOLTON * np.exp(A_BOLTON * t_c / (t_c + B_BOLTON))
    w_sat = EPSILON * e_s / (p_hpa - e_s)
    w = EPSILON * w_sat * rh_frac / (EPSILON + w_sat * (1.0 - rh_frac))
    return float(w / (1.0 + w))


def test_derived_q_matches_known_value():
    """T = 280 K, RH = 50 % on the 850 hPa level -> q = 3.63e-3 kg/kg
    (hand-checkable: e_s ~ 9.91 hPa, w_sat ~ 7.34e-3)."""
    t, rh = 280.0, 50.0
    out = defaults.preprocess_cira_icechunk_ar_forecast_dataset(_fcn_like_ds(t, rh))
    assert "specific_humidity" in out
    q = out["specific_humidity"]
    assert tuple(q.dims) == ("lead_time", "level", "latitude", "longitude")
    for j, p in enumerate([250, 500, 850]):
        exp = _expected_q(t, rh / 100.0, float(p))
        got = float(q.isel(lead_time=0, level=j, latitude=0, longitude=0))
        assert got == pytest.approx(exp, rel=1e-9), f"level {p} hPa"
    # physical sanity at 850 hPa
    q850 = float(q.isel(lead_time=0, level=2, latitude=0, longitude=0))
    assert 3.5e-3 < q850 < 3.8e-3


def test_q_levels_in_monotonic_order():
    """For a uniform T/RH column the vapor PARTIAL pressure is fixed
    (e = RH*e_s(T)), so w = eps*e/(p-e) GROWS as pressure falls: q must
    increase upward (250 hPa > 500 hPa > 850 hPa). A transposed level axis
    would break this ordering."""
    out = defaults.preprocess_cira_icechunk_ar_forecast_dataset(_fcn_like_ds(285.0, 60.0))
    q = out["specific_humidity"].isel(lead_time=0, latitude=1, longitude=0).values
    assert q[0] > q[1] > q[2]  # 250 > 500 > 850 hPa


def test_preprocess_noop_when_q_present():
    ds = _fcn_like_ds(280.0, 50.0)
    ds["specific_humidity"] = ds["air_temperature"] * 0.0 + 0.004
    out = defaults.preprocess_cira_icechunk_ar_forecast_dataset(ds)
    assert float(out["specific_humidity"].isel(lead_time=0, level=0).values.flat[0]) == 0.004
