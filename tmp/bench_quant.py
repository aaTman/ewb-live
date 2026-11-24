"""Micro-benchmark the pooled-quantile estimator options (run on ewb).

Tile = (91 lat x 180 lon), pool = 31 days x 30 years = 930 samples/doy,
comparing exact strategies for per-cell wet-conditioned quantiles.
"""
from __future__ import annotations

import time

import numpy as np

rng = np.random.default_rng(0)
NLAT, NLON, NPOOL = 91, 180, 930
shape = (NPOOL, NLAT, NLON)
n_doy, n_year = 366, 30


def bench(name, fn, n=3):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n
    print(f"{name:52s} {dt*1000:8.1f} ms  -> per doy-tile")
    return dt


a = rng.gamma(1.2, 2.0, shape).astype("float32")
a[rng.random(shape) < 0.35] = 0.0            # dry samples
a[:20] = np.nan                               # missing samples
wet = np.isfinite(a) & (a >= 1.0)
n = wet.sum(0)

bench("gather take(930 slice)", lambda: a.take(np.arange(NPOOL), axis=0))


def sort_gather():
    s = np.sort(a, axis=0)                    # NaN/inf to the end
    lo = np.floor(0.99 * (n - 1)).astype("intp")
    return np.take_along_axis(s, lo[None], axis=0)[0]


bench("sort + take_along_axis (p99 exact)", sort_gather)
bench("np.partition at fixed pos", lambda: np.partition(a, 920, axis=0))
bench("np.quantile(a,[.95,.99]) (NaN-poisoned)",
      lambda: np.quantile(a, [0.95, 0.99], axis=0))
bench("np.nanquantile(a,0.99)", lambda: np.nanquantile(a, 0.99, axis=0), n=1)
bench("np.sort only", lambda: np.sort(a, axis=0))
bench("np.sort float32 of int-quantised", lambda: np.sort((a * 8).astype("int32"), axis=0))


def hist_quant():
    K = 512
    b = np.clip((a * 8).astype("int32"), 0, K - 1)   # 0.125 mm bins to 64 mm
    flat = b.reshape(NPOOL, -1)
    h = np.zeros((NLAT * NLON, K), dtype="int32")
    idx = np.tile(np.arange(NLAT * NLON), NPOOL)
    np.add.at(h, (idx, flat.reshape(-1)), 1)
    return h


bench("histogram scatter (0.125mm bins)", hist_quant, n=1)
print("pool bytes", a.nbytes / 1e6, "MB; tile time-axis bytes",
      NPOOL * NLAT * NLON * 4 / 1e6, "MB")
