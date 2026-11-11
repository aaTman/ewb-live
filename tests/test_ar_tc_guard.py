"""TC-core plausibility guard for the AR lane (science-review B8).

Typhoon Dolphin (fbe99f30e625, peak land IVT 2972 kg/m/s) and Hurricane
Isaias (6ad93bd2a76d, 2018) passed as ARs in the new selection. Guard:
hard IVT cap (calibrated: legit events topped at 1435), plus a compact
core rule (round blob at IVT >= 1000). Pure function over numpy inputs.
"""
from __future__ import annotations

import numpy as np

from ewb_live.events.ar import tc_plausibility_rule


def _blob(shape=(40, 40), kind: str = "filament") -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    if kind == "filament":       # elongated AR: thin diagonal band
        for i in range(30):
            m[i, i] = True
            if i < 29:
                m[i, i + 1] = True
    elif kind == "vortex":       # compact round core
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        m[(yy - 20) ** 2 + (xx - 20) ** 2 <= 8 ** 2] = True
    return m


def test_implausible_ivt_rejected():
    assert tc_plausibility_rule(2972.4, _blob(kind="vortex")) == "ivt_implausible"
    assert tc_plausibility_rule(1501.0, _blob(kind="filament")) == "ivt_implausible"


def test_legit_ivt_at_cap_kept():
    assert tc_plausibility_rule(1434.9, _blob(kind="filament")) is None


def test_compact_core_rejected_at_moderate_ivt():
    assert tc_plausibility_rule(1200.0, _blob(kind="vortex")) == "tc_compact_core"


def test_elongated_high_ivt_kept():
    assert tc_plausibility_rule(1200.0, _blob(kind="filament")) is None


def test_compact_low_ivt_kept():
    # topographic concentration at low IVT stays (not TC-flagged)
    assert tc_plausibility_rule(800.0, _blob(kind="vortex")) is None


def test_missing_inputs_kept():
    assert tc_plausibility_rule(None, None) is None
    assert tc_plausibility_rule(None, np.zeros((4, 4), dtype=bool)) is None
