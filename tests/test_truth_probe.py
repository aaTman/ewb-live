"""ERA5 ARCO truth-cutoff probe: hole tolerance and the stale-cache fallback.

Real-world driver (2026-11-18): gs://gcp-public-data-arco-era5 stopped filling at
2026-11-11T17Z and had NaN *interior* hours (2026-11-04T05Z, 2026-11-09T23Z). The
old probe anchored on ``now - 14 d`` and raised if that one hour was NaN, which
aborted every lane build and made the pipeline report a data outage as an empty
result. These tests fake the archive in memory -- no network, no xarray.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from ewb_live.events import truth as T

HOUR = np.timedelta64(1, "h")


class _Sel:
    """Stands in for `t2m.sel(...)` — the probe only reads `.values`."""

    def __init__(self, value):
        self.values = np.float64(value)


class FakeT2m:
    """Hourly 'time' axis; `holes` are hours that read NaN at every point."""

    def __init__(self, first: dt.datetime, hours: int, holes=(), tail_nan=0):
        first64 = np.datetime64(first.replace(tzinfo=None), "h")
        self.time = type("T", (), {"values": first64 + np.arange(hours) * HOUR})()
        self._holes = {np.datetime64(h.replace(tzinfo=None), "h") for h in holes}
        self._tail_nan = tail_nan  # the last N hours read NaN (the ARCO lag)

    def sel(self, *, time, latitude, longitude, method):  # noqa: ARG002
        i = int(self.time.values.searchsorted(time))
        if time in self._holes or i >= len(self.time.values) - self._tail_nan:
            return _Sel(np.nan)
        return _Sel(290.0)


def _patch(monkeypatch, fake):
    monkeypatch.setattr(T, "_open_arco_t2m", lambda uri: fake)


NOW = dt.datetime(2026, 11, 18, 21)


def test_finds_the_newest_valid_hour_when_the_archive_lags(monkeypatch):
    # 20 days of hourly data, last 7 days unfilled (the real 5-7 d ARCO lag)
    fake = FakeT2m(NOW - dt.timedelta(days=20), 20 * 24, tail_nan=7 * 24)
    _patch(monkeypatch, fake)
    # the axis' newest timestamp is now-1h, so "last 168 h unfilled" ends at
    # now-169h -- the expectation is the fake's boundary, not a round date
    assert T.probe_arco_cutoff(now=NOW, arco_uri="fake") == NOW - dt.timedelta(days=7, hours=1)


def test_interior_hole_does_not_abort_the_probe(monkeypatch):
    """The regression: NaN at now-14 d (the old anchor) with data both sides."""
    hole_at_anchor = NOW - dt.timedelta(days=14)
    fake = FakeT2m(NOW - dt.timedelta(days=20), 20 * 24,
                   holes=[hole_at_anchor, NOW - dt.timedelta(hours=3)],
                   tail_nan=2 * 24)
    _patch(monkeypatch, fake)
    cut = T.probe_arco_cutoff(now=NOW, arco_uri="fake")
    # newest filled hour = now-49h (48 h unfilled on a now-1h-terminated axis);
    # the point is that the NaN anchor at now-14 d no longer aborts the probe
    assert cut == NOW - dt.timedelta(days=2, hours=1), cut


def test_unavailable_archive_raises_from_the_probe_but_the_cache_carries_the_build(monkeypatch, tmp_path):
    cache = tmp_path / "truth_cutoff.json"
    cache.write_text(json.dumps({"cutoff": (NOW - dt.timedelta(days=6)).isoformat(),
                                 "probed_at": (NOW - dt.timedelta(hours=30)).isoformat()}))
    monkeypatch.setattr(T, "CUTOFF_CACHE_PATH", str(cache))
    fake = FakeT2m(NOW - dt.timedelta(days=20), 20 * 24, tail_nan=20 * 24)  # all NaN
    _patch(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        T.probe_arco_cutoff(now=NOW, arco_uri="fake")
    # ...but the caller used by every lane keeps going on the cached cutoff
    assert T.truth_cutoff(now=NOW) == NOW - dt.timedelta(days=6)


def test_probe_failure_without_a_cache_is_a_real_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "CUTOFF_CACHE_PATH", str(tmp_path / "absent.json"))
    fake = FakeT2m(NOW - dt.timedelta(days=20), 20 * 24, tail_nan=20 * 24)
    _patch(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        T.truth_cutoff(now=NOW)
