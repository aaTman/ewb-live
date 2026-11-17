"""Virtual-store-only enforcement for CIRA MLWP forecast data.

Owner ruling 2026-11-17 (drop-subsets): every read of the CIRA/NOAA MLWP
archive must go through the Icechunk virtual store
(``ewb_live.stores.mlwp_icechunk``) via the single choke point
``ewb_live.sources.aiwp.open_mlwp_forecast``. No code may download or
materialise archive bytes for evaluation, and the retired per-init subset
cache tree must never come back.

These tests are cheap static scans plus small functional probes:

* no module under ``ewb_live/`` (outside the store builders in
  ``ewb_live/stores/``) may reference the archive bucket by name/constant;
* nothing may write subset-cache directories or materialise sources reads
  with ``to_zarr``/``to_netcdf`` inside ``ewb_live/sources/``;
* only the choke point may open store data sessions outside the builders;
* ``sources.open_forecast`` serves MLWP data through the (fake) store and
  creates NO subset directory anywhere under the cache root.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "ewb_live"

STORE_BUILDERS = {  # sanctioned direct-bucket consumers (store build/maintain)
    REPO / "ewb_live" / "stores" / "mlwp_common.py",
    REPO / "ewb_live" / "stores" / "mlwp_icechunk.py",
}

BUCKET_TOKEN = "noaa-oar-mlwp-data"

# patterns that would resurrect the retired download/materialise path
FORBIDDEN: list[tuple[str, re.Pattern]] = [
    ("bucket URI outside the store builders", re.compile(re.escape(BUCKET_TOKEN))),
    ("MLWP bucket constant", re.compile(r"\bAIWP_BUCKET\b")),
    ("subset cache path", re.compile(r"cache[/\\]subsets")),
    ("subset cache dir literal", re.compile(r"cache_path\(\s*[\"']subsets[\"']")),
    ("subset prune root", re.compile(r"_SUBSET_ROOT")),
]

SOURCES = PKG / "sources"
FORBIDDEN_IN_SOURCES = [
    ("forecast materialisation", re.compile(r"\.to_zarr\(|\.to_netcdf\(")),
]

CHOKE_POINT = SOURCES / "aiwp.py"
# store *data* sessions may be opened by the choke point and the builders only
STORE_SESSION = re.compile(r"\bopen_(init|model)\s*\(")


def _pkg_pyfiles():
    return sorted(PKG.rglob("*.py"))


def _scan(path: Path, patterns):
    text = path.read_text(encoding="utf-8")
    return [(label, m) for label, pat in patterns if (m := pat.search(text))]


def test_bucket_only_under_stores():
    hits = []
    for f in _pkg_pyfiles():
        if f in STORE_BUILDERS:
            continue
        for label, m in _scan(f, FORBIDDEN):
            hits.append(f"{f.relative_to(REPO)}: {label} ({m.group(0)!r})")
    assert not hits, "MLWP download/access reappeared:\n" + "\n".join(hits)


def test_sources_never_materialise():
    hits = []
    for f in sorted(SOURCES.glob("*.py")):
        for label, m in _scan(f, FORBIDDEN_IN_SOURCES):
            hits.append(f"{f.relative_to(REPO)}: {label} ({m.group(0)!r})")
    assert not hits, "sources layer materialises forecasts again:\n" + "\n".join(hits)


def test_store_session_openers_whitelisted():
    hits = []
    for f in _pkg_pyfiles():
        if f in STORE_BUILDERS or f == CHOKE_POINT:
            continue
        for label, pat in (("open_init/open_model call", STORE_SESSION),):
            text = f.read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                if pat.search(line) and not line.lstrip().startswith(("#", '"')):
                    hits.append(f"{f.relative_to(REPO)}:{n}: {line.strip()}")
    assert not hits, "MLWP store data opened outside the choke point:\n" + "\n".join(hits)


def test_choke_point_exists_and_bounds_concurrency():
    from ewb_live.sources import aiwp

    assert callable(aiwp.open_mlwp_forecast)
    assert aiwp.open_forecast is not None
    assert 1 <= aiwp.ICECHUNK_MAX_SESSIONS <= 2  # notes/ewb.md hang guard


# ----------------------------------------------------------------- functional
def _toy_store_ds(init: dt.datetime, leads: list[int]):
    da = xr.DataArray(
        np.full((1, len(leads), 2, 2), 300.0, dtype="float32"),
        dims=("init_time", "lead_time", "latitude", "longitude"),
        coords={
            "init_time": [np.datetime64(init, "ns")],
            "lead_time": [np.timedelta64(h, "h") for h in leads],
            "latitude": [10.0, 0.0],
            "longitude": [0.0, 90.0],
        },
    )
    return xr.Dataset({"t2": da})


class _FakeStore:
    """Stands in for ewb_live.stores.mlwp_icechunk (no repo, no network)."""

    def __init__(self):
        self.opened = []

    def open_init(self, code, init, **kw):
        self.opened.append((code, init))
        return _toy_store_ds(init, [0, 6])


@pytest.fixture()
def _clear_memo():
    from ewb_live.sources import api

    api._mem_cache.clear()
    api._mem_bytes = 0
    yield
    api._mem_cache.clear()
    api._mem_bytes = 0


def test_open_forecast_uses_store_and_creates_no_subsets(tmp_path, monkeypatch,
                                                         _clear_memo):
    from ewb_live.sources import _net, aiwp, api

    fake = _FakeStore()
    monkeypatch.setattr(aiwp, "_store", lambda: fake)
    monkeypatch.setattr(_net, "CACHE_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "_MEM_CACHE_MAX_BYTES", 512 * 1024 * 1024)

    init = dt.datetime(2026, 11, 10)
    ds = api.open_forecast("graphcast", init, ["surface_air_temperature"], [0, 6],
                           (0.0, 10.0, 0.0, 90.0))
    assert fake.opened == [("GRAP_v100_IFS", init)]  # the virtual store served it
    assert float(ds.surface_air_temperature.mean()) == 300.0
    # second call: in-process memo, still no second store session, no disk I/O
    ds2 = api.open_forecast("graphcast", init, ["surface_air_temperature"], [0, 6],
                            (0.0, 10.0, 0.0, 90.0))
    assert fake.opened == [("GRAP_v100_IFS", init)]
    assert ds2 is ds
    # the retired subset tree must not exist anywhere under the cache root
    assert not any(p.name == "subsets" for p in tmp_path.rglob("subsets"))
    assert list(tmp_path.iterdir()) == []  # nothing at all was written


def test_open_forecast_store_miss_raises_filenotfound(monkeypatch, _clear_memo):
    """A store miss behaves like a missing object (nearest-init fallback works)."""
    from ewb_live.sources import aiwp, api

    class MissStore(_FakeStore):
        def open_init(self, code, init, **kw):
            raise KeyError("init not in store")

    monkeypatch.setattr(aiwp, "_store", lambda: MissStore())
    init = dt.datetime(2026, 11, 10)
    with pytest.raises(FileNotFoundError):
        api.open_forecast("graphcast", init, ["surface_air_temperature"], [0], None)
    # and no store at all -> loud FileNotFoundError too
    monkeypatch.setattr(aiwp, "_store", lambda: None)
    with pytest.raises(FileNotFoundError):
        api.open_forecast("graphcast", init, ["surface_air_temperature"], [0], None)


def test_memo_evicts_by_bytes(_clear_memo):
    from ewb_live.sources import api

    api._MEM_CACHE_MAX_BYTES = 100
    big = xr.Dataset({"a": ("x", np.zeros(4000))})  # 32 kB > budget
    api._mem_put("big", big)
    assert "big" not in api._mem_cache  # never retained when over budget

    api._MEM_CACHE_MAX_BYTES = 7_000
    one = xr.Dataset({"a": ("x", np.zeros(600))})  # ~4.8 kB each
    api._mem_put("k1", one)
    api._mem_put("k2", one)
    assert "k1" not in api._mem_cache  # oldest evicted first (LRU)
    assert "k2" in api._mem_cache
    api._MEM_CACHE_MAX_BYTES = 512 * 1024 * 1024
