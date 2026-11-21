"""Kernel-level tests for the pure numpy metric helpers in ``ewb_live.scoring.flood``.

Offline, tiny arrays, hand-computed expectations.
"""

import datetime as dt

import numpy as np
import pytest

from ewb_live.scoring import flood


# --------------------------------------------------------------------------- #
# 1. contingency / scores_from_counts
# --------------------------------------------------------------------------- #
def test_contingency_counts_and_scores():
    fc_exc = np.array([[True, False, True],
                       [True, True, False],
                       [False, False, False]])
    obs_exc = np.array([[True, True, True],
                        [False, True, False],
                        [False, False, False]])
    valid = np.array([[False, True, True],      # (0, 0) invalid: fc and obs both exceed there
                      [True, True, True],
                      [True, True, True]])

    # valid cells only.  hits: (0,2) (1,1) | misses: (0,1) | false alarms: (1,0)
    # correct negatives: (1,2) (2,0) (2,1) (2,2) -> 4; 2+1+1+4 == 8 valid cells
    h, m, fa, cn = flood.contingency(fc_exc, obs_exc, valid)
    assert (h, m, fa, cn) == (2, 1, 1, 4)

    s = flood.scores_from_counts(h, m, fa)
    assert np.isclose(s[flood.M_CSI], 2 / 4)
    assert np.isclose(s[flood.M_POD], 2 / 3)
    assert np.isclose(s[flood.M_FAR], 1 / 3)


def test_scores_from_counts_undefined_denominators():
    # nothing forecast or observed -> every denominator undefined
    assert flood.scores_from_counts(0, 0, 0) == {}

    # no forecast exceedance at all: CSI and POD defined (0), FAR undefined
    s = flood.scores_from_counts(0, 3, 0)
    assert np.isclose(s[flood.M_CSI], 0.0)
    assert np.isclose(s[flood.M_POD], 0.0)
    assert flood.M_FAR not in s


# --------------------------------------------------------------------------- #
# 2. fractions_skill_score
# --------------------------------------------------------------------------- #
def test_fss_identical_fields_is_one():
    fc = np.zeros((5, 5), bool)
    fc[1:3, 1:3] = True
    valid = np.ones((5, 5), bool)
    assert np.isclose(flood.fractions_skill_score(fc, fc.copy(), valid, 3), 1.0)


def test_fss_disjoint_n1_is_zero():
    fc = np.zeros((5, 5), bool)
    obs = np.zeros((5, 5), bool)
    fc[0, 0] = True
    obs[3, 3] = True
    valid = np.ones((5, 5), bool)
    assert np.isclose(flood.fractions_skill_score(fc, obs, valid, 1), 0.0)


def test_fss_shifted_block_scores_better_with_bigger_neighbourhood():
    obs = np.zeros((7, 7), bool)
    obs[2:4, 2:4] = True                      # 2x2 block
    fc = np.zeros((7, 7), bool)
    fc[2:4, 3:5] = True                       # same block shifted one cell east
    valid = np.ones((7, 7), bool)

    f1 = flood.fractions_skill_score(fc, obs, valid, 1)
    f3 = flood.fractions_skill_score(fc, obs, valid, 3)
    # n=1: 2 of 4 cells agree -> 1 - 4/8 = 0.5
    assert np.isclose(f1, 0.5)
    assert f3 > f1
    assert 0.0 < f3 < 1.0


def test_fss_both_fields_empty_is_none():
    fc = np.zeros((4, 4), bool)
    obs = np.zeros((4, 4), bool)
    valid = np.ones((4, 4), bool)
    assert flood.fractions_skill_score(fc, obs, valid, 3) is None


def test_fss_ignores_exceedance_in_invalid_cells():
    valid = np.ones((5, 5), bool)
    valid[0, 0] = False
    obs = np.zeros((5, 5), bool)
    obs[2:4, 2:4] = True

    fc_on = np.zeros((5, 5), bool)
    fc_on[0, 0] = True                        # True in a cell that is not valid
    fc_off = fc_on.copy()
    fc_off[0, 0] = False

    a = flood.fractions_skill_score(fc_on, obs, valid, 3)
    b = flood.fractions_skill_score(fc_off, obs, valid, 3)
    assert a is not None and b is not None
    assert np.isclose(a, b)


# --------------------------------------------------------------------------- #
# 3. daily_totals_mm
# --------------------------------------------------------------------------- #
FILL = 9.96921e36


def test_daily_totals_mm_basic_and_clipping():
    bins = np.full((4, 3, 2), 0.001)          # metres -> 4 * 1 mm
    out = flood.daily_totals_mm(bins)
    assert out is not None
    assert out.shape == (3, 2)
    assert np.allclose(out, 4.0)

    bins2 = bins.copy()
    bins2[1, 1, 1] = -0.002                   # small negative clipped to 0
    out2 = flood.daily_totals_mm(bins2)
    assert out2 is not None
    assert np.isclose(out2[1, 1], 3.0)
    assert np.isclose(out2[0, 0], 4.0)


def test_daily_totals_mm_fill_everywhere_is_none():
    bins = np.full((4, 3, 2), 0.001)
    bins[1] = FILL                            # one whole bin filled -> day unavailable
    assert flood.daily_totals_mm(bins) is None


def test_daily_totals_mm_single_fill_cell_is_nan():
    bins = np.full((4, 3, 2), 0.001)
    bins[1, 2, 0] = FILL
    out = flood.daily_totals_mm(bins)
    assert out is not None
    assert np.isnan(out[2, 0])
    other = np.delete(out.ravel(), np.ravel_multi_index((2, 0), out.shape))
    assert np.all(np.isfinite(other))
    assert np.allclose(other, 4.0)


# --------------------------------------------------------------------------- #
# 4. bin_leads_of_day
# --------------------------------------------------------------------------- #
def test_bin_leads_of_day():
    assert flood.bin_leads_of_day(dt.datetime(2026, 9, 10, 0, 0), dt.date(2026, 9, 10)) == [6, 12, 18, 24]
    assert flood.bin_leads_of_day(dt.datetime(2026, 9, 9, 0, 0), dt.date(2026, 9, 10)) == [30, 36, 42, 48]
    assert flood.bin_leads_of_day(dt.datetime(2026, 9, 9, 12, 0), dt.date(2026, 9, 10)) == [18, 24, 30, 36]
    # the last bin of day D closes at 24 UTC on D, never the 06 UTC bin of D+1
    assert flood.bin_leads_of_day(dt.datetime(2026, 9, 10, 0, 0), dt.date(2026, 9, 10))[-1] == 24


# --------------------------------------------------------------------------- #
# 5. day_metrics
# --------------------------------------------------------------------------- #
def _base_6x6():
    thr = np.full((6, 6), 20.0)
    land = np.ones((6, 6), bool)
    truth = np.zeros((6, 6))
    truth[2:4, 2:4] = 30.0                    # 2x2 block of 30 mm
    footprint = np.zeros((6, 6), bool)
    footprint[2:4, 2:4] = True
    return thr, land, truth, footprint


def test_day_metrics_perfect_forecast():
    thr, land, truth, footprint = _base_6x6()
    out = flood.day_metrics(truth.copy(), truth.copy(), thr, land, footprint)
    assert np.isclose(out[flood.M_CSI], 1.0)
    assert np.isclose(out[flood.M_POD], 1.0)
    assert np.isclose(out[flood.M_FAR], 0.0)
    assert np.isclose(out[flood.M_FSS3], 1.0)
    assert np.isclose(out[flood.M_BIAS], 0.0)
    assert np.isclose(out[flood.M_PEAK], 0.0)


def test_day_metrics_signed_bias_and_peak():
    thr, land, truth, footprint = _base_6x6()
    fc = truth.copy()
    fc[2:4, 2:4] -= 10.0                      # 20 mm: still >= threshold 20 -> hits
    out = flood.day_metrics(fc, truth, thr, land, footprint)
    assert np.isclose(out[flood.M_CSI], 1.0)
    assert np.isclose(out[flood.M_BIAS], -10.0)
    assert np.isclose(out[flood.M_PEAK], -10.0)


def test_day_metrics_under_threshold_is_miss():
    thr, land, truth, footprint = _base_6x6()
    fc = truth.copy()
    fc[2:4, 2:4] -= 20.0                      # 10 mm: below threshold
    out = flood.day_metrics(fc, truth, thr, land, footprint)
    assert np.isclose(out[flood.M_CSI], 0.0)
    assert np.isclose(out[flood.M_POD], 0.0)
    assert flood.M_FAR not in out             # no forecast exceedance at all
    assert np.isclose(out[flood.M_BIAS], -20.0)


def test_day_metrics_excludes_nan_and_off_land_cells():
    thr, land, truth, footprint = _base_6x6()
    fc = truth.copy()                         # only the valid block cell exceeds
    fc[2, 3] = 0.0                            # would be a miss if the NaN thr counted
    thr[2, 3] = np.nan                        # excluded: undefined threshold
    fc[3, 2] = 0.0                            # would be a miss if off-land counted
    land[3, 2] = False                        # excluded: off land
    fc[3, 3] = np.nan                         # excluded: NaN forecast
    out = flood.day_metrics(fc, truth, thr, land, footprint)
    # remaining valid exceedance cell: (2, 2) forecast and observed -> 1 hit, no misses
    assert np.isclose(out[flood.M_CSI], 1.0)
    assert np.isclose(out[flood.M_POD], 1.0)
    assert np.isclose(out[flood.M_FAR], 0.0)
    assert np.isclose(out[flood.M_FSS3], 1.0)  # masked fields are identical on valid cells


def test_day_metrics_no_exceedance_keeps_signed_metrics():
    thr, land, _t, footprint = _base_6x6()
    truth = np.zeros((6, 6))
    truth[2:4, 2:4] = 18.0                    # below the threshold everywhere
    fc = np.zeros((6, 6))
    fc[2:4, 2:4] = 15.0
    out = flood.day_metrics(fc, truth, thr, land, footprint)
    for key in (flood.M_CSI, flood.M_POD, flood.M_FAR, flood.M_FSS3):
        assert key not in out
    assert flood.M_BIAS in out and flood.M_PEAK in out
    assert np.isclose(out[flood.M_BIAS], -3.0)
    assert np.isclose(out[flood.M_PEAK], -3.0)


# --------------------------------------------------------------------------- #
# 6. domain_for
# --------------------------------------------------------------------------- #
def test_domain_for_plain_bbox():
    lats, lons, window = flood.domain_for((10.0, 20.0, 30.0, 40.0))
    assert np.all(np.diff(lats) > 0)
    assert lats.min() <= 8.0 and lats.max() >= 22.0
    assert np.allclose(lats % 0.25, 0.0)
    assert np.all(np.diff(lons) > 0)
    assert lons.min() <= 28.0 and lons.max() >= 42.0
    assert np.allclose(lons % 0.25, 0.0)
    assert window == (float(lats[0]), float(lats[-1]), float(lons[0]), float(lons[-1] - lons[0]))
    assert np.allclose(window, (8.0, 22.0, 28.0, 14.0))


def test_domain_for_wrapped_bbox():
    _, lons, _ = flood.domain_for((30.0, 40.0, 170.0, -170.0))
    assert np.all(np.diff(lons) > 0)
    assert np.allclose(np.diff(lons), 0.25)
    span = lons[-1] - lons[0]
    assert 23.9 <= span <= 24.3


def test_domain_for_polar_bbox_stays_within_90():
    lats, _, _ = flood.domain_for((80.0, 89.9, 0.0, 10.0))
    assert np.all(lats <= 90.0)
    assert np.all(np.diff(lats) > 0)
    assert lats.max() == 90.0
