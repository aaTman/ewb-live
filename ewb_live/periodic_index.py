"""Periodic-boundary xarray index for wrapping longitude selections.

Vendored from brightbandtech/ExtremeWeatherBench `data_prep/temperature_events.py`
(develop @ 3b6506b, MIT), pinned against xarray 2026.9.0 (import paths verified).

Owner direction 2026-11-09: use PeriodicBoundaryIndex for the 0-deg / 180-deg
seams instead of hand-rolled wrap logic (upstream xarray does not ship one —
pydata/xarray#7031 is still open — so the EWB repo implementation is the
package that provides it).

Two deviations from upstream: (1) `_wrap_periodically` is
``min + (label - min) % period`` instead of ``min + (label - max) % period``.
Upstream's form is the identity in-range only when ``max - min == period``
(duplicate-endpoint grids); on half-open grids like ERA5's 0..359.75 it
shifts every in-range label by one grid step. Mine is the identity on any
grid of step ``period / n``.  (2) open-ended slices (``slice(None, x)``) pass
through to PandasIndex; upstream crashes on them (``None - self._min``).

Known limits (xarray Index API, not this class):
- `sel()` is periodic; `isel()` is plain PandasIndex (see #7031).
- Cross-boundary slices use continuous labels: slice(350, 380), NOT
  slice(350, 20) — start > stop returns empty.
- join/align across datasets with DIFFERENT lon conventions unsupported;
  all datasets here share one convention per grid.
- scipy.ndimage.label still needs the wrap-union step for array-level
  periodic connectivity (the index drives crops/selection only).
"""
from __future__ import annotations

from typing import Any

import numpy as np
from xarray.core.indexes import IndexSelResult, PandasIndex, _query_slice
from xarray.core.indexing import _expand_slice


class PeriodicBoundaryIndex(PandasIndex):
    """xarray index for a 1-D coordinate that wraps at a period.

    Intercepts slice queries so a selection crossing the period boundary
    (e.g. longitude 350 to 380) is returned as two concatenated index
    arrays rather than an empty slice.
    """

    period: float
    _min: float
    _max: float

    __slots__ = ("_max", "_min", "coord_dtype", "dim", "index", "period")

    def __init__(self, *args, period=360, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self._min = self.index.min()
        self._max = self.index.max()

    @classmethod
    def from_variables(cls, variables, options):
        obj = super().from_variables(variables, options={})
        obj.period = options.get("period", obj.period)
        return obj

    def _wrap_periodically(self, label_value: float) -> float:
        return self._min + (label_value - self._min) % self.period

    def _split_slice_across_boundary(self, label: slice) -> np.ndarray:
        """Return concatenated integer indices for a slice that wraps."""
        first_slice = slice(label.start, self._max, label.step)
        second_slice = slice(self._min, label.stop, label.step)
        first_as_indices = _expand_slice(
            _query_slice(self.index, first_slice), self.index.size)
        second_as_indices = _expand_slice(
            _query_slice(self.index, second_slice), self.index.size)
        return np.concatenate([first_as_indices, second_as_indices])

    def sel(
        self, labels: dict[Any, Any], method=None, tolerance=None
    ) -> IndexSelResult:
        """Remap out-of-range labels back into the index range."""
        assert len(labels) == 1
        coord_name, label = next(iter(labels.items()))

        if isinstance(label, slice):
            start, stop, step = label.start, label.stop, label.step
            if start is None or stop is None:
                return super().sel({coord_name: label})  # open range: no wrap needed
            if stop < start:
                return super().sel({coord_name: []})
            wrapped_start = self._wrap_periodically(label.start)
            wrapped_stop = self._wrap_periodically(label.stop)
            wrapped_label = slice(wrapped_start, wrapped_stop, step)
            if (wrapped_start is None or wrapped_stop is None
                    or wrapped_start < wrapped_stop):
                return super().sel({coord_name: wrapped_label})
            # Slice crosses the wrap boundary; split in two.
            return IndexSelResult(
                {self.dim: self._split_slice_across_boundary(wrapped_label)})

        wrapped_label = self._wrap_periodically(label)
        return super().sel(
            {coord_name: wrapped_label}, method=method, tolerance=tolerance)

    def __repr__(self) -> str:
        return f"PeriodicBoundaryIndex(period={self.period})"


def attach_periodic(obj, coord="longitude", period=360.0):
    """Re-attach `coord` as a PeriodicBoundaryIndex (idempotent)."""
    if isinstance(obj.xindexes.get(coord), PeriodicBoundaryIndex):
        return obj
    return obj.drop_indexes(coord).set_xindex(
        coord, PeriodicBoundaryIndex, period=period)


def lon_slice(lon_native, lon_min: float, lon_max: float, period: float = 360.0):
    """Bbox lon arc [lon_min -> lon_max] eastward (lon_min > lon_max = wrap)
    as ONE continuous sel slice in the grid's own lon domain. Owner direction
    2026-11-09: PeriodicBoundaryIndex drives seam crops everywhere; crossing
    windows use continuous labels (slice(347.5, 376.2)), the index splits them.
    """
    gmin = float(np.nanmin(lon_native))
    lo = (lon_min - gmin) % period + gmin
    hi = (lon_max - gmin) % period + gmin
    return slice(lo, hi if hi >= lo else hi + period)
