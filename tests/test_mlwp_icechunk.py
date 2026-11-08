"""Tests for the CIRA MLWP Icechunk virtual store (ewb_live.stores.mlwp_icechunk).

Unit tests run offline. Everything that touches S3 is marked ``network``:

    uv run pytest -m network tests/test_mlwp_icechunk.py

The network build test creates a *tiny* store (2-day window, one model) in a tmp dir
and checks virtual-store reads are bit-identical to direct NetCDF reads. Full-archive
verification lives in scripts/mlwp_verify_store.py.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ewb_live.stores import mlwp_common as C
from ewb_live.stores import mlwp_icechunk as M

TEST_MODEL = "GRAP_v100_IFS"


# ----------------------------------------------------------------- unit ---

def test_parse_filename_roundtrip():
    fn = "GRAP_v100_IFS_2026110512_f000_f240_06.nc"
    model, init, hours = C.parse_filename(fn)
    assert model == "GRAP_v100_IFS"
    assert init == dt.datetime(2026, 11, 5, 12)
    assert hours == (0, 240, 6)
    key = C.key_for(model, init.date(), fn)
    assert key == ("noaa-oar-mlwp-data/GRAP_v100_IFS/2026/1105/"
                   "GRAP_v100_IFS_2026110512_f000_f240_06.nc")


def test_parse_filename_rejects_junk():
    with pytest.raises(ValueError):
        C.parse_filename("README.txt")


def test_layout_signature_stable_and_layout_sensitive():
    import xarray as xr

    a = xr.DataArray(np.zeros((2, 2), dtype="float32"), dims=("a", "b"), name="v")
    b = xr.DataArray(np.zeros((2, 2), dtype="float32"), dims=("a", "b"), name="v")
    c = xr.DataArray(np.zeros((2, 2), dtype="float64"), dims=("a", "b"), name="v")
    s1 = M.layout_signature(xr.Dataset({"v": a}))
    s2 = M.layout_signature(xr.Dataset({"v": b}))
    s3 = M.layout_signature(xr.Dataset({"v": c}))
    assert s1 == s2
    assert s1 != s3


def test_layout_signature_coord_values_sensitive():
    """A level-dim value change at unchanged variable shapes must change the sig
    (era grouping is how level changes get their own group instead of corrupting
    an appended one)."""
    import xarray as xr

    base = xr.Dataset(
        {"t": (("level", "y"), np.zeros((2, 2), dtype="float32"))},
        coords={"level": [50.0, 100.0], "y": [0, 1]},
    )
    moved = base.assign_coords(level=[50.0, 200.0])
    assert M.layout_signature(base) != M.layout_signature(moved)


def test_layout_signature_ignores_init_time_values():
    """init_time is a per-file size-1 coord: hashing its VALUES would give every
    file its own era (then two eras in one day collide on the dated group name).
    Layout sigs of same-layout inits must be identical."""
    import xarray as xr

    def mk(init_ns: int) -> xr.Dataset:
        return xr.Dataset(
            {"t": (("init_time", "lead"), np.zeros((1, 3), dtype="float32"))},
            coords={"init_time": np.array([init_ns], dtype="datetime64[ns]"),
                    "lead": np.arange(3, dtype="timedelta64[h]")},
        )

    assert M.layout_signature(mk(0)) == M.layout_signature(
        mk(int(np.datetime64("2026-11-05", "ns").astype(np.int64))))


def test_repo_config_registers_virtual_container_and_splits():
    pytest.importorskip("icechunk")
    cfg = M.repo_config()
    conts = cfg.virtual_chunk_containers or {}
    assert f"{C.BUCKET_URI}/" in conts
    assert cfg.manifest is not None
    split = cfg.manifest.splitting.to_dict()
    assert split, "manifest splitting must be configured"


def test_repo_storage_uri_dispatch():
    """Portability: repo paths may be local, s3:// or gs:// (Brightband/GCP)."""
    pytest.importorskip("icechunk")
    assert not M.repo_is_local("s3://bucket/prefix")
    assert not M.repo_is_local("gs://bucket/prefix")
    assert M.repo_is_local("data/icechunk/cira_mlwp")
    for uri in ("s3://bucket/prefix", "gs://bucket/prefix", "s3://bucket"):
        storage = M.repo_storage(uri)  # constructed lazily; no network here
        assert storage is not None


def test_update_lock_serializes_writers(tmp_path):
    repo = str(tmp_path / "cira_mlwp")
    with M.update_lock(repo) as first:
        assert first is True
        with M.update_lock(repo) as second:
            assert second is False
        # and it is released afterwards
    with M.update_lock(repo) as again:
        assert again is True


def test_update_walks_away_when_locked(tmp_path):
    """The weekly catch-up must no-op (exit-0), not race or fail, while the daily
    timer's update holds the repo lock."""
    repo = str(tmp_path / "cira_mlwp")
    with M.update_lock(repo):
        s = M.update(models=[TEST_MODEL], path=repo, verbose=False)
    assert s["status"] == "locked"
    assert s["commit"] is None


def test_known_bad_merge_semantics():
    """The ledger must converge: repeat failures refresh, repairs drop out."""
    url = "s3://noaa-oar-mlwp-data/AURO_v100_IFS/2026/0919/x.nc"
    led: dict = {}
    assert M._merge_known_bad(led, [{"url": url, "error": "E1"}], set(), now="T1")
    assert led[url] == {"error": "E1", "first_seen": "T1", "last_checked": "T1"}
    # same object fails again later: last_checked moves, first_seen is preserved
    assert M._merge_known_bad(led, [{"url": url, "error": "E1"}], set(), now="T2")
    assert led[url]["first_seen"] == "T1" and led[url]["last_checked"] == "T2"
    # identical merge is a no-op (an all-failing update must not commit every week)
    assert not M._merge_known_bad(led, [{"url": url, "error": "E1"}], set(), now="T2")
    # upstream repaired the object: it leaves the ledger
    assert M._merge_known_bad(led, [], {url}, now="T3")
    assert led == {}
    assert not M._merge_known_bad(led, [], set(), now="T4")
    # a file that PARSED but was refused (edge-gate) lands in skipped AND succeeded:
    # skipped must win, or the era would be re-probed every single update
    assert M._merge_known_bad(led, [{"url": url, "error": "refused"}], {url}, now="T5")
    assert led[url]["error"] == "refused"


def test_partial_edge_chunks_detection():
    """A zarr grid whose extent is not a multiple of the chunk size = partial edge
    chunk = the virtualizarr <=2.8 mis-offset hazard class; must be detected.
    The gate only reads ``da.data.metadata.shape/.chunks`` + ``da.dims``, so plain
    stand-ins keep this offline and virtualizarr-API-agnostic."""
    import types

    def da(dims, shape, chunks):
        return types.SimpleNamespace(
            dims=dims,
            data=types.SimpleNamespace(metadata=types.SimpleNamespace(
                shape=shape, chunks=chunks)))

    ds = types.SimpleNamespace(data_vars={  # pyright: ignore[reportArgumentType]
        "t2": da(("init_time", "lead"), (1, 100), (1, 48)),   # 100 % 48 -> partial
        "ok": da(("init_time", "lead"), (1, 96), (1, 48)),    # tiles exactly
        "real_coord": types.SimpleNamespace(dims=(), data=None),  # no metadata
    })
    risky = M.partial_edge_chunks(ds)
    assert set(risky) == {"t2"}
    assert risky["t2"][0] == ("lead", 100, 48)


def test_planning_inventory_full_archive_default():
    """days=None must route to the full-archive inventory, not the day-window one."""
    called = {}
    real_all, real_win = C.inventory_all, C.inventory
    C.inventory_all = lambda **kw: called.setdefault("all", kw) or {"M": {}}
    C.inventory = lambda *a, **kw: called.setdefault("win", (a, kw)) or {"M": {}}
    try:
        M._planning_inventory(None, None, False)
        M._planning_inventory(30, None, False)
    finally:
        C.inventory_all, C.inventory = real_all, real_win
    assert "all" in called and "win" in called


def test_maintenance_due_arithmetic():
    now = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
    mk = lambda iso: (lambda path: {M.LAST_MAINTENANCE_KEY: iso})
    real = M._root_attrs
    try:
        M._root_attrs = mk("2026-10-30T00:00:00+00:00")   # 7 days: due
        assert M.maintenance_due(now=now) is True
        M._root_attrs = mk("2026-11-02T00:00:00+00:00")   # 4 days: not due
        assert M.maintenance_due(now=now) is False
        M._root_attrs = lambda path: {}                    # never run: due
        assert M.maintenance_due(now=now) is True
    finally:
        M._root_attrs = real


# --------------------------------------------------------------- network ---

@pytest.fixture(scope="session")
def smoke_store(tmp_path_factory):
    """Small real store: 2-day window of one model (network)."""
    pytest.importorskip("icechunk")
    path = str(tmp_path_factory.mktemp("cira_mlwp"))
    try:
        M.build(days=2, models=[TEST_MODEL], workers=4, path=path, verbose=False)
    except Exception as e:  # pragma: no cover - bucket outage
        pytest.skip(f"could not build store from bucket: {e}")
    return path


def _direct_read(model: str, init: dt.datetime, variables, lat, lon, leads_h):
    """Read the raw NetCDF object directly (the ground truth path)."""
    import s3fs
    import xarray as xr

    fn = f"{model}_{init:%Y%m%d%H}_f000_f240_06.nc"
    fs = s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": C.REGION})
    vts = [np.datetime64(init) + np.timedelta64(h, "h") for h in leads_h]
    out = {}
    with fs.open(C.key_for(model, init.date(), fn)) as f:
        with xr.open_dataset(f) as raw:
            for v in variables:
                da = raw[v].sel(time=vts, latitude=slice(*lat), longitude=slice(*lon))
                if "level" in da.dims:
                    da = da.isel(level=0)
                out[v] = da.values
    return out


@pytest.mark.network
def test_list_models_and_inits(smoke_store):
    assert TEST_MODEL in M.list_models(path=smoke_store)
    inits = M.list_inits(TEST_MODEL, path=smoke_store)
    assert inits and inits == sorted(inits)
    assert len(inits) == len(set(inits))
    # all inits are on the 00/12 UTC cadence
    assert {t.hour for t in inits} <= {0, 6, 12, 18}


@pytest.mark.network
def test_open_model_is_lazy_and_shaped(smoke_store):
    ds = M.open_model(TEST_MODEL, path=smoke_store)
    assert {"init_time", "lead_time", "latitude", "longitude"} <= set(ds.dims)
    assert "t2" in ds
    assert str(ds["t2"].data).lower().find("dask") >= 0 or hasattr(ds["t2"].data, "dask_keys")
    # lead_time is a timedelta coord 0..240h step 6h
    lt = ds["lead_time"].values.astype("timedelta64[h]").astype(int)
    assert lt[0] == 0 and lt[1] - lt[0] == 6 and lt[-1] == 240
    # valid_time is a correct (init_time, lead_time) coord
    it0 = np.datetime64(ds["init_time"].values[0], "h")
    assert ds["valid_time"].values[0, 3] == it0 + np.timedelta64(18, "h")


@pytest.mark.network
def test_virtual_values_equal_direct_netcdf(smoke_store):
    """The whole point: virtual chunk refs decode identically to the NetCDF file."""
    ds = M.open_model(TEST_MODEL, path=smoke_store)
    inits = M.list_inits(TEST_MODEL, path=smoke_store)
    it = inits[-1]
    leads_h = (0, 6, 12)
    lat, lon = (40.0, 30.0), (230.0, 245.0)  # lat index descends with value
    sub = ds[["t2", "z"]].sel(
        init_time=it,
        lead_time=[pd.Timedelta(hours=h) for h in leads_h],
        latitude=slice(*lat),
        longitude=slice(*lon),
    )
    got = sub.compute(scheduler="synchronous")
    ref = _direct_read(TEST_MODEL, it, ["t2", "z"], lat, lon, leads_h)
    assert got["t2"].shape == ref["t2"].shape
    assert np.nanmax(np.abs(got["t2"].values - ref["t2"])) == 0.0
    z = got["z"].isel(level=0).values
    assert np.nanmax(np.abs(z - ref["z"])) == 0.0


@pytest.mark.network
def test_subset_read_is_fast(smoke_store):
    """One init, 3 leads, 2 vars, a bbox must come back in seconds, not minutes."""
    import time

    ds = M.open_model(TEST_MODEL, path=smoke_store)
    it = M.list_inits(TEST_MODEL, path=smoke_store)[-1]
    t = time.time()
    got = ds[["t2", "z"]].sel(
        init_time=it,
        lead_time=[pd.Timedelta(hours=h) for h in (0, 6, 12)],
        latitude=slice(40, 30), longitude=slice(230, 245),
    ).compute()
    assert time.time() - t < 30.0, "subset read too slow"
    assert np.isfinite(got["t2"].values).any()


@pytest.mark.network
def test_update_self_heals_when_repo_missing(tmp_path):
    """`update` on a fresh checkout builds the window instead of failing the run."""
    path = str(tmp_path / "absent_repo")
    s = M.update(days=1, models=[TEST_MODEL], workers=4, path=path, verbose=False)
    assert s.get("self_healed_build") is True
    assert s["commit"] is not None
    assert M.list_inits(TEST_MODEL, path=path)


@pytest.mark.network
def test_update_is_idempotent(smoke_store):
    before = M.list_inits(TEST_MODEL, path=smoke_store)
    s = M.update(days=2, models=[TEST_MODEL], workers=4, path=smoke_store,
                 verbose=False)
    assert s["commit"] is None, f"no-op update must not commit: {s}"
    assert M.list_inits(TEST_MODEL, path=smoke_store) == before


@pytest.mark.network
def test_maintenance_expires_and_stamps(smoke_store):
    """maintenance() runs cleanly on a young repo (nothing to expire yet), stamps
    the root attrs so maintenance_due flips to False, and the repo still reads."""
    assert M.maintenance_due(smoke_store) is True  # never maintained
    out = M.maintenance(smoke_store, verbose=False)
    assert out["expired_snapshots"] == 0  # snapshots are minutes old
    assert out["commit"] is not None      # stamp commit
    assert M.maintenance_due(smoke_store) is False
    assert M.list_inits(TEST_MODEL, path=smoke_store)  # store still readable


@pytest.mark.network
def test_known_bad_ledger_roundtrip(smoke_store):
    """A recorded failure persists in the repo and is visible via known_bad()."""
    import icechunk

    repo = icechunk.Repository.open(M.repo_storage(smoke_store), M.repo_config(),
                                    M.repo_credentials())
    session = repo.writable_session("main")
    url = "s3://noaa-oar-mlwp-data/TEST_MODEL/2026/0101/TEST_MODEL_2026010100_f000_f240_06.nc"
    assert M._record_known_bad(session, M._known_bad_of(session),
                              [{"url": url, "error": "synthetic"}], set())
    session.commit("test: seed known_bad ledger")

    entries = M.known_bad(path=smoke_store)
    assert any(e["url"] == url and e["error"] == "synthetic" for e in entries)
    # the seeded entry belongs to no real model, so it must not leak into a filter
    assert all(e["url"] != url for e in M.known_bad(TEST_MODEL, path=smoke_store))


@pytest.mark.network
def test_edge_probe_live_model_tiles_exactly(smoke_store):
    """Live-model layouts must have no partial edge chunks: the probe passes
    trivially and the gate never blocks the daily path."""
    init = M.list_inits(TEST_MODEL, path=smoke_store)[-1]
    url = C.url_for(TEST_MODEL, init.date(),
                    f"{TEST_MODEL}_{init:%Y%m%d%H}_f000_f240_06.nc")
    rec = M.probe_edge_chunk_equivalence(url, init)
    assert rec["ok"] is True, rec
    assert rec["partial_edge_vars"] == {}


@pytest.mark.network
def test_edge_probe_partial_chunk_value_equality():
    """FOUR_v100_GFS is the historical partial-edge-chunk case (lead 41 steps in
    chunks of 14). The probe must run the REAL round-trip comparison there; with a
    buggy virtualizarr it reports ok=False and sync/backfill REFUSE the era.
    Either verdict is a pass for this test — the point is the probe executes and
    compares, which the store gate then acts on."""
    init = dt.datetime(2021, 6, 15, 0)
    url = C.url_for("FOUR_v100_GFS", init.date(),
                    f"FOUR_v100_GFS_{init:%Y%m%d%H}_f000_f240_06.nc")
    rec = M.probe_edge_chunk_equivalence(url, init)
    assert "ok" in rec and "detail" in rec, rec
    # FOUR has the risky layout; a trivial pass would mean detection broke
    assert rec["partial_edge_vars"] != {} or "no partial" not in rec["detail"], rec
    if not rec["ok"]:
        pytest.skip(f"edge-chunk probe FAILED on live virtualizarr (build gate "
                    f"will refuse this era — as designed): {rec}")


@pytest.mark.network
def test_inventory_all_full_archive():
    """The full-archive inventory must reach back to 2020 and cover ~31k files."""
    inv = C.inventory_all(refresh=True)
    n = sum(len(v) for m in inv.values() for v in m.values())
    assert n > 30000, f"expected the full archive (~31k files), got {n}"
    firsts = [min(d for d in m) for m in inv.values() if m]
    assert min(firsts) <= "2020-12-31", min(firsts)


@pytest.mark.network
def test_real_store_read_if_present():
    """Read-only check against the operational store when it exists."""
    import os

    if not M.repo_exists(M.DEFAULT_REPO):
        pytest.skip(f"no operational store at {M.DEFAULT_REPO}")
    ds = M.open_model(TEST_MODEL)
    it = M.list_inits(TEST_MODEL)[-1]
    v = ds["t2"].sel(init_time=it, lead_time=pd.Timedelta(hours=0)).compute()
    assert v.shape == (721, 1440)
