"""Loader for the upstream EWB ``data_prep`` scripts.

The detection logic lives in the ExtremeWeatherBench repository's
``data_prep/`` directory (not in the installed package). The full repo is
cloned at ``third_party/ExtremeWeatherBench`` (v1.1.1 @ c6467f4). This module
imports those scripts *unmodified* via importlib so the live pipeline runs
exactly the upstream code; all deviations live in the thin wrappers
(``temperature.py``, ``ar.py``) and are documented in notes/ewb.md.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_EWB_DATA_PREP = REPO_ROOT / "third_party" / "ExtremeWeatherBench" / "data_prep"


def data_prep_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("EWB_DATA_PREP_DIR", DEFAULT_EWB_DATA_PREP))
    if not d.is_dir():
        raise FileNotFoundError(
            f"EWB data_prep dir not found: {d} (clone "
            "github.com/brightbandtech/ExtremeWeatherBench to third_party/ "
            "or set EWB_DATA_PREP_DIR)"
        )
    return d


def _load(module_name: str, filename: str):
    path = data_prep_dir() / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@functools.lru_cache(maxsize=1)
def temperature_events():
    """Upstream data_prep/temperature_events.py (heat wave + freeze detection)."""
    return _load("ewb_upstream_temperature_events", "temperature_events.py")


@functools.lru_cache(maxsize=1)
def ar_bounds():
    """Upstream data_prep/ar_bounds.py (AR case bounds)."""
    return _load("ewb_upstream_ar_bounds", "ar_bounds.py")
