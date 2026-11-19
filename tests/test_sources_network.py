"""Network tests: open a real, recent forecast for every registered model.

Run with:  uv run pytest -m network
Each test downloads only a small bbox x few leads, so the full suite takes a
few minutes (ECMWF byte-range reads dominate).
"""

import datetime as dt

import pytest

from ewb_live import sources

pytestmark = [pytest.mark.network]

BBOX = (30.0, 40.0, -110.0, -100.0)  # CONUS slice, small
LEADS = [0, 24]


def _recent_init(model: str) -> dt.datetime | None:
    end = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    start = end - dt.timedelta(days=4)
    inits = sources.available_inits(model, start, end)
    daily00 = [i for i in inits if i.hour == 0]
    return (daily00 or inits)[-1] if inits else None


@pytest.mark.parametrize("model", sources.list_models())
def test_open_recent_forecast(model):
    init = _recent_init(model)
    if init is None:
        pytest.skip(f"no init for {model} in the last 4 days (store gap)")
    spec = sources.model_info(model)
    var = (
        "surface_air_temperature"
        if "surface_air_temperature" in spec.variables
        else spec.variables[0]
    )
    ds = sources.open_forecast(model, init, [var], LEADS, BBOX, use_cache=False)
    da = ds[var]

    assert ds.init_time.values[0] == np_datetime(init)
    assert da.dims[0] == "init_time" and da.dims[1] == "lead_time"
    assert set(("init_time", "lead_time", "latitude", "longitude")) <= set(da.dims)
    assert ds.longitude.values.min() >= -180.0
    assert ds.longitude.values.max() <= 180.0
    assert ds.latitude.values.min() >= 30.0 - 1e-6
    assert ds.latitude.values.max() <= 40.0 + 1e-6
    assert float(da.mean()) == da.mean().compute().item()  # finite dtype check
    assert da.notnull().any()


def np_datetime(init: dt.datetime):
    import numpy as np

    return np.datetime64(init, "ns")


@pytest.mark.parametrize("model", ["graphcast"])
def test_make_ewb_forecast(model):
    """The EWB-facing wrapper returns an inputs.Forecast with the right vars."""
    from extremeweatherbench import inputs as ewb_inputs

    init = _recent_init(model)
    if init is None:
        pytest.skip(f"no init for {model} in the last 4 days")
    fc = sources.make_ewb_forecast(
        model, ["surface_air_temperature"], [init], [0, 12], BBOX
    )
    assert isinstance(fc, ewb_inputs.ForecastBase)
    assert fc.name.startswith(model)
    ds = fc.ds
    assert ds is not None
    assert "surface_air_temperature" in ds.data_vars
    assert ds.init_time.size == 1
