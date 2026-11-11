"""Model registry for the forecast sources.

All model metadata lives here: bucket layouts, variable mappings from the
native store naming to ExtremeWeatherBench (EWB) variable names, and the
pressure levels each model carries.

Variable names follow ``extremeweatherbench.defaults.DEFAULT_VARIABLE_NAMES``
(verified against EWB 1.1.1 ``inputs.CIRA_metadata_variable_mapping`` and
``inputs.HRES_metadata_variable_mapping``).

Longitudes are normalised to [-180, 180) on output; latitudes stay descending
90 .. -90 (native order of every store here).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# EWB-native variable name -> (per-model native name) tables ------------------

# AIWP / CIRA store (s3://noaa-oar-mlwp-data). Mirrors
# extremeweatherbench.inputs.CIRA_metadata_variable_mapping (keys reversed).
CIRA_NAME_TO_EWB = {
    "t2": "surface_air_temperature",
    "t": "air_temperature",
    "q": "specific_humidity",
    "u": "eastward_wind",
    "v": "northward_wind",
    "z": "geopotential",
    "r": "relative_humidity",
    "u10": "surface_eastward_wind",
    "v10": "surface_northward_wind",
    "u100": "100m_eastward_wind",
    "v100": "100m_northward_wind",
    "msl": "air_pressure_at_mean_sea_level",
}

# ECMWF GRIB2 shortNames (ecmwf-forecasts: IFS HRES and AIFS Single).
ECMWF_PARAM_TO_EWB = {
    "2t": "surface_air_temperature",
    "2d": "surface_dewpoint_temperature",
    "msl": "air_pressure_at_mean_sea_level",
    "10u": "surface_eastward_wind",
    "10v": "surface_northward_wind",
    "100u": "100m_eastward_wind",
    "100v": "100m_northward_wind",
    "t": "air_temperature",
    "u": "eastward_wind",
    "v": "northward_wind",
    "q": "specific_humidity",
    "r": "relative_humidity",
    "z": "geopotential",
}

# NOAA AIGFS (GraphCast operational) GRIB2 disc codes; pressure levels only.
AIGFS_PARAM_TO_EWB = {
    "TMP": "air_temperature",
    "SPFH": "specific_humidity",
    "UGRD": "eastward_wind",
    "VGRD": "northward_wind",
    "HGT": "geopotential",
}

AIWP_LEVELS_HPA = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]

# Variables that live on pressure levels in the stores (per EWB native name).
LEVEL_VARS_EWB = {
    "air_temperature",
    "specific_humidity",
    "eastward_wind",
    "northward_wind",
    "geopotential",
    "geopotential_height",
    "relative_humidity",
}

AIWP_MODELS = {
    "graphcast": "GRAP_v100",
    "pangu_weather": "PANG_v100",
    "fourcastnet_v2": "FOUR_v200",
    "aurora": "AURO_v100",
}


@dataclass(frozen=True)
class ModelSpec:
    """Static description of one model source."""

    name: str
    description: str
    backend: str  # "aiwp" | "ecmwf" | "aigfs"
    variables: tuple[str, ...]  # EWB names available for this model
    level_variables: frozenset[str] = frozenset()  # set = exposed vars ∩ LEVEL_VARS_EWB
    levels: tuple[int, ...] | None = None
    # AIWP only: which initial-condition system ('IFS' or 'GFS')
    init_source: str = "IFS"
    # ecmwf only: path template directory under <date>/<init>/
    ecmwf_path: str = ""
    resolution_deg: float = 0.25


def _aiwp_vars(store_vars: set[str]) -> tuple[str, ...]:
    # 'w', 'sp', 'tcwv', 'apcp' have no EWB default name and are not exposed
    return tuple(sorted(CIRA_NAME_TO_EWB[v] for v in store_vars if v in CIRA_NAME_TO_EWB))


# Native variable sets verified against the live store 2026-11-06.
_AIWP_STORE_VARS_IFS = {
    "graphcast": {"t2", "t", "q", "u", "v", "z", "u10", "v10", "msl", "apcp", "w"},
    "aurora": {"t2", "t", "q", "u", "v", "z", "u10", "v10", "msl"},
    "pangu_weather": {"t2", "t", "q", "u", "v", "z", "u10", "v10", "msl"},
    "fourcastnet_v2": {
        "t2",
        "t",
        "r",
        "u",
        "v",
        "z",
        "u10",
        "v10",
        "u100",
        "v100",
        "msl",
        "sp",
        "tcwv",
    },
}
_AIWP_STORE_VARS_GFS = dict(_AIWP_STORE_VARS_IFS)

_ECMWF_VARS = (
    "100m_eastward_wind",
    "100m_northward_wind",
    "air_temperature",
    "air_pressure_at_mean_sea_level",
    "eastward_wind",
    "geopotential",
    "northward_wind",
    "relative_humidity",
    "specific_humidity",
    "surface_air_temperature",
    "surface_dewpoint_temperature",
    "surface_eastward_wind",
    "surface_northward_wind",
)

_MODELS: dict[str, ModelSpec] | None = None


def models() -> dict[str, ModelSpec]:
    """Model registry keyed by model id."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    m: dict[str, ModelSpec] = {}
    for name, code in AIWP_MODELS.items():
        for src in ("IFS", "GFS"):
            store_vars = (_AIWP_STORE_VARS_IFS if src == "IFS" else _AIWP_STORE_VARS_GFS)[name]
            mid = name if src == "IFS" else f"{name}_gfs"
            m[mid] = ModelSpec(
                name=mid,
                description=f"CIRA/NOAA AIWP reanalysis-initialised {name} ({src}-initialised)",
                backend="aiwp",
                variables=_aiwp_vars(store_vars),
                levels=tuple(AIWP_LEVELS_HPA),
                init_source=src,
            )
    m["ifs_hres"] = ModelSpec(
        name="ifs_hres",
        description="ECMWF IFS HRES deterministic forecast (ECMWF open data, 0.25 deg)",
        backend="ecmwf",
        variables=_ECMWF_VARS,
        levels=tuple(sorted(AIWP_LEVELS_HPA)),
        ecmwf_path="ifs/0p25/oper",
    )
    m["aifs_single"] = ModelSpec(
        name="aifs_single",
        description="ECMWF AIFS Single deterministic AI forecast (ECMWF open data, 0.25 deg)",
        backend="ecmwf",
        variables=_ECMWF_VARS,
        levels=tuple(sorted(AIWP_LEVELS_HPA)),
        ecmwf_path="aifs-single/0p25/oper",
    )
    m["aigfs"] = ModelSpec(
        name="aigfs",
        description=(
            "NOAA AIGFS operational GraphCast (00/06/12/18Z, pressure levels only, "
            "no surface fields)"
        ),
        backend="aigfs",
        variables=tuple(sorted(set(AIGFS_PARAM_TO_EWB.values()))),
        levels=tuple(AIWP_LEVELS_HPA),
    )
    _MODELS = {
        k: replace(spec, level_variables=frozenset(spec.variables) & LEVEL_VARS_EWB)
        for k, spec in m.items()
    }
    return _MODELS


def ecb_name_for(store_param: str, backend: str) -> str | None:
    table = {
        "aiwp": CIRA_NAME_TO_EWB,
        "ecmwf": ECMWF_PARAM_TO_EWB,
        "aigfs": AIGFS_PARAM_TO_EWB,
    }[backend]
    return table.get(store_param)
