"""Shared test configuration.

Network-marked tests download from S3/GCS — per the team hard rule, EWB
compute runs only on the `ewb` LXC (see SPEC "Where code runs" and
`ewb_live/_guard.py`). On any other host they are skipped automatically;
set ``EWB_LIVE_ALLOW_LOCAL=1`` to run them deliberately elsewhere.
"""

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
