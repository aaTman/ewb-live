"""Shared test configuration.

Network-marked tests download from S3/GCS — per the team hard rule, EWB
compute runs only on the `ewb` LXC (see SPEC "Where code runs" and
`ewb_live/_guard.py`). On any other host they are skipped automatically;
set ``EWB_LIVE_ALLOW_LOCAL=1`` to run them deliberately elsewhere.

Tests that exercise the upstream ``data_prep`` scripts need the gitignored
``third_party/ExtremeWeatherBench`` clone; request the ``upstream_data_prep``
fixture and they skip with a clear reason where that checkout is absent.
"""

import pathlib

# Deterministic BLAS/OpenMP thread count - MUST be set before any numpy import
# (hence the placement at module top, before importing ewb_live). On CPU-
# starved hosts (pinned-vCPU containers, small CI runners) OpenBLAS spin-
# wait threads livelock: tiny numpy ops burn minutes of sys time and the
# suite appears to hang (GitHub CI hang + local repro, 2026-11-23).
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import pytest

from ewb_live._guard import is_ewb_host


def pytest_collection_modifyitems(config, items):
    if is_ewb_host():
        return
    skip = pytest.mark.skip(
        reason="network tests run on the ewb LXC only "
        "(ops/remote.sh run python -m pytest -m network; "
        "override: EWB_LIVE_ALLOW_LOCAL=1)"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def upstream_data_prep() -> pathlib.Path:
    """The upstream ``data_prep`` dir, or a skip when the checkout is missing.

    ``ewb_live.events._upstream`` imports the detection scripts unmodified from
    ``third_party/ExtremeWeatherBench/data_prep``, which is gitignored (it is
    cloned on the ewb LXC / CI, pinned by ``uv.lock``). Path resolution is
    delegated to ``data_prep_dir()`` so ``EWB_DATA_PREP_DIR`` works here too.
    """
    from ewb_live.events._upstream import data_prep_dir

    try:
        return data_prep_dir()
    except FileNotFoundError:
        pytest.skip(
            "third_party/ExtremeWeatherBench not checked out — the upstream "
            "data_prep tests need it: git clone "
            "https://github.com/brightbandtech/ExtremeWeatherBench "
            "third_party/ExtremeWeatherBench (or set EWB_DATA_PREP_DIR)"
        )
