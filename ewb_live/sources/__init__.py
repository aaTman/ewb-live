"""ewb_live.sources — live forecast data for the EWB dashboard.

Sources:
* CIRA/NOAA 'AI Weather Prediction' MLWP store, s3://noaa-oar-mlwp-data
  (graphcast, pangu_weather, fourcastnet_v2, aurora; IFS- and GFS-initialised)
* ECMWF open data, s3://ecmwf-forecasts (ifs_hres, aifs_single)
* NOAA operational GraphCast, s3://noaa-nws-graphcastgfs-pds (aigfs)
"""

from .api import (
    available_inits,
    list_models,
    make_ewb_forecast,
    model_info,
    open_forecast,
)

__all__ = [
    "list_models",
    "available_inits",
    "open_forecast",
    "make_ewb_forecast",
    "model_info",
]
