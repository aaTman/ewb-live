"""Offline tests for the raw_exceedance frame codec + incremental build."""
import numpy as np
import pytest

from ewb_live import serve
from ewb_live.viz import exceedance as rx


def _translate(tmp_path, url_path):
    orig_results, orig_sample = serve.RESULTS_DIR, serve.SAMPLE_DIR
    try:
        serve.RESULTS_DIR = str(tmp_path)
        serve.SAMPLE_DIR = str(tmp_path / "empty-sample")
        h = serve.DashboardHandler.__new__(serve.DashboardHandler)
        return serve.DashboardHandler.translate_path(h, url_path)
    finally:
        serve.RESULTS_DIR, serve.SAMPLE_DIR = orig_results, orig_sample


def test_raw_frame_routes(tmp_path):
    (tmp_path / "raw_exceedance").mkdir()
    assert _translate(tmp_path, "/data/results/raw_exceedance/2026081012.bin") \
        == str(tmp_path / "raw_exceedance" / "2026081012.bin")
    assert _translate(tmp_path, "/data/results/raw_exceedance/2026081012.trk") \
        == str(tmp_path / "raw_exceedance" / "2026081012.trk")
    assert _translate(tmp_path, "/data/results/raw_exceedance/2026081000.run") \
        == str(tmp_path / "raw_exceedance" / "2026081000.run")
    assert _translate(tmp_path, "/data/results/raw_exceedance/index.json") \
        == str(tmp_path / "raw_exceedance" / "index.json")
    for bad in ("2026.bin", "evil.parquet", "2026081012345.bin",
                "2026081012.trk.json", "20260810.trk", "20260810.run",
                "evil.run.json", "2026081012.run.json"):
        assert "nonesuch" in _translate(tmp_path, f"/data/results/raw_exceedance/{bad}")
    assert "nonesuch" in _translate(tmp_path, "/data/results/raw_exceedance")


def test_frame_codec_roundtrip():
    rng = np.random.default_rng(0)
    h = rng.random((37, 61)) > 0.8
    f = rng.random((37, 61)) > 0.9
    a = rng.random((37, 61)) > 0.7
    b = rng.random((37, 61)) > 0.95
    raw = rx.frame_bytes(h, f, a, b)
    h2, f2, a2, b2 = rx.frame_layers(raw, h.shape)
    assert (h == h2).all() and (f == f2).all() and (a == a2).all()
    assert (b == b2).all()          # gap-bridge flag survives the wire
    # ALWAYS 4 planes: an old writer's payload is plane 3 = empty, never a
    # shorter payload (the JS decoder offsets planes 0-2 by it)
    h3, f3, a3, b3 = rx.frame_layers(rx.frame_bytes(h, f, a), h.shape)
    assert (h == h3).all() and (f == f3).all() and (a == a3).all()
    assert not b3.any()
    # sparse frames must compress hard
    assert len(raw) < (4 * 37 * 61 + 7) // 8


def test_build_frames_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "LAT_N", 8)
    monkeypatch.setattr(rx, "LON_N", 8)
    times = [np.datetime64("2026-08-10T00"), np.datetime64("2026-08-10T06"),
             np.datetime64("2026-08-11T00"), np.datetime64("2026-08-11T06")]
    calls = []

    def layer_fn(day_times):
        calls.append(len(day_times))
        n = len(day_times)
        m = np.zeros((n, 8, 8), bool)
        m[:, 2:4, 2:4] = True
        bridged = np.zeros((n, 8, 8), bool)
        bridged[:, 3, 3] = True     # one AR cell is only a carried footprint
        return m, np.zeros_like(m), m, \
            np.zeros((n, 8, 8), np.uint16), np.zeros((n, 8, 8), np.float32), \
            bridged      # labels -> .trk (done-rule), bridged -> plane 3

    frames: list[str] = []
    assert rx.build_frames(tmp_path, times, layer_fn, False, frames) == 4
    assert calls == [2, 2]                       # one call per day, not per frame
    assert sorted(frames) == ["2026081000", "2026081006",
                              "2026081100", "2026081106"]
    h, f, a, b = rx.frame_layers((tmp_path / "2026081000.bin").read_bytes(), (8, 8))
    assert h[2:4, 2:4].all() and not h[:2].any() and not f.any()
    assert a[2:4, 2:4].all() and b[3, 3] and int(b.sum()) == 1
    n_before = len(calls)
    assert rx.build_frames(tmp_path, times, layer_fn, False, frames) == 0
    assert len(calls) == n_before                # incremental: nothing recomputed


def test_to_display_native_to_contract(tmp_path, monkeypatch):
    """native (row0=lat90, col0=lon0) -> row0=south, col0=lon-180."""
    monkeypatch.setattr(rx, "LAT_N", 8)
    monkeypatch.setattr(rx, "LON_N", 8)
    a = np.zeros((8, 8), bool)
    a[2, 3] = True                    # (N-first row 2, lon col 3)
    d = rx._to_display(a)
    assert d[8 - 1 - 2, (3 - 4) % 8]        # flipped rows, rolled cols
    assert d.sum() == 1


def test_label_sig_accepts_numpy_remap():
    """Ponytail 04:15Z MUST: the build's numbering fingerprint must accept
    the numpy uint16 remap (json of a bare ndarray crashed every build)."""
    remap = np.zeros(4, np.uint16)
    remap[1], remap[2] = 1, 2
    sig = rx.label_sig(remap)
    assert len(sig) == 12 and sig == rx.label_sig(remap.copy())
    remap[3] = 3                       # renumbering -> new fingerprint
    assert sig != rx.label_sig(remap)


def test_track_sidecar_and_flicker_gate(tmp_path):
    """labels codec roundtrip + presence scan + interior-dropout detection
    (owner Japan bug: long-lived AR blinking between frames)."""
    frames = [f"202608{10 + i // 4:02d}{(i % 4) * 6:02d}" for i in range(20)]
    index = {"frames": frames}
    present = set(frames) - {frames[10], frames[11]}      # track 7 blinks out
    shape = (6, 7)
    for tag in frames:                                     # sparse labels
        lab = np.zeros(shape, np.uint16)
        if tag in present:
            lab[1:3, 2:5] = 7
        if frames.index(tag) < 6:
            lab[5, 6] = 8                                  # track 8: clean
        if tag in frames[:4]:
            lab[0, 0] = 9                                  # track 9: short
        (tmp_path / f"{tag}.trk").write_bytes(rx.labels_bytes(lab))
    assert (rx.labels_from_bytes(rx.labels_bytes(
        np.arange(42, dtype=np.uint16).reshape(6, 7)), (6, 7))
        == np.arange(42, dtype=np.uint16).reshape(6, 7)).all()

    presence = rx.read_presence(tmp_path, frames, shape)
    assert presence[7] == present
    flick = rx.flickering_tracks(index, presence)
    assert flick == {7: [frames[10], frames[11]]}          # only the blinker
    with pytest.raises(AssertionError):                    # dtype guard
        rx.labels_bytes(np.zeros((2, 2), np.int32))        # must be uint16


# ---- consecutive-day duration sidecar (channel_consecutive_days §2-§4) ----

def _day(patterns, cell_bits=3):
    """Build one day of (4, 1, cell_bits) step masks from per-step truth
    tuples ordered (A, B, C) — one bool cell per scenario."""
    return [np.array([p], bool).reshape(1, cell_bits) for p in patterns]


def test_run_recurrence_and_codec():
    """Acceptance 1 (dur rev v2): completed-day count constant within a day,
    provisional bit 7, growth across complete days, saturation — plus .run
    codec roundtrip (two u8 planes, zlib)."""
    # cell A: 4 complete days... scenarios as (A,B,C) per step:
    days = [
        _day([(True, False, False)] * 4),                    # day1 all exceed
        _day([(True, False, False), (True, False, False),
              (False, False, False), (True, False, False)]),  # mid-day fail
        _day([(True, True, False)] * 4),                     # A regrows, B starts
        _day([(True, True, False)] * 4),                     # A, B grow
    ]
    runs = rx.run_recurrence(days)
    flat = np.stack([r[0] for r in runs])                    # (16, 3)
    a = flat[:, 0]
    assert list(a[0:4]) == [0x80] * 4          # day1: 0 completed, today provisional
    assert list(a[4:8]) == [0x81, 0x81, 0x01, 0x01]  # C(1)=1 solid ALL steps (no mid-day reset); prov bit drops at the failed step
    assert list(a[8:12]) == [0x80] * 4         # day3: C(2)=0 -> completed streak back to 0
    assert list(a[12:16]) == [0x81] * 4        # day4: C(3)=1, constant all day
    assert list(flat[:, 2]) == [0] * 16        # never-exceeding cell stays 0
    assert all(r.dtype == np.uint8 for r in runs)
    # saturation at RUN_CAP: 300 all-True days never overflow low7 into bit7
    cap = rx.run_recurrence([_day([(True, True, True)])] * 300)
    assert cap[-1][0, 0] == 0x80 | rx.RUN_CAP and cap[299][0, 0] == 0x80 | rx.RUN_CAP
    # seed = pre-window history (acceptance 4, recurrence level): the first
    # in-window frame already carries the seeded completed count
    seeded = rx.run_recurrence([_day([(True, False, False)] * 4)],
                               seed=np.array([[1, 0, 0]], np.uint8))
    assert seeded[0][0, 0] == 0x81 and seeded[0][0, 1] == 0
    # codec: two u8 planes roundtrip, sparse payload compresses, size guarded
    h = np.arange(12, dtype=np.uint8).reshape(3, 4)
    f = np.full((3, 4), 255, np.uint8)
    h2, f2 = rx.run_from_bytes(rx.run_bytes(h, f), (3, 4))
    assert (h == h2).all() and (f == f2).all()
    zeros = np.zeros((64, 64), np.uint8)
    assert len(rx.run_bytes(zeros, zeros)) < 2 * 64 * 64 / 10
    with pytest.raises(ValueError):
        rx.run_from_bytes(rx.run_bytes(h, f), (3, 5))


def test_run_sidecar_backfill_and_prune(tmp_path, monkeypatch):
    """Acceptance 2 + 4 (build level): one .run per frame; deleting a .run
    rewrites ONLY that frame with byte-identical .bin; lookback seed makes
    the first frame read > 1; prune_frames deletes .run on retention."""
    monkeypatch.setattr(rx, "LAT_N", 8)
    monkeypatch.setattr(rx, "LON_N", 8)
    times = [np.datetime64(f"2026-08-{d}T{h:02d}")
             for d in (10, 11) for h in (0, 6, 12, 18)]

    def layer_fn(day_times):
        n = len(day_times)
        m = np.zeros((n, 8, 8), bool)
        m[:, 2:4, 2:4] = True
        return m, np.zeros_like(m), np.zeros_like(m), \
            np.zeros((n, 8, 8), np.uint16), np.zeros((n, 8, 8), np.float32)

    # lookback: heat ran 3 consecutive days through 2026-08-09, 4 through
    # 2026-08-10 (run_seed keys are the day BEFORE each frame day)
    seed9 = np.zeros((8, 8), np.uint8)
    seed9[2:4, 2:4] = 3
    seed10 = np.zeros((8, 8), np.uint8)
    seed10[2:4, 2:4] = 4
    zero = np.zeros((8, 8), np.uint8)
    run_seed = {"2026-08-09": (seed9, zero), "2026-08-10": (seed10, zero)}
    frames: list[str] = []
    assert rx.build_frames(tmp_path, times, layer_fn, False, frames,
                           run_seed) == 8
    for tag in frames:
        assert (tmp_path / f"{tag}.run").exists()
    h0, f0 = rx.run_from_bytes((tmp_path / "2026081000.run").read_bytes(),
                               (8, 8))
    assert h0[2, 2] == 0x83 and h0[0, 0] == 0 and f0.max() == 0  # 3 completed seeded + today prov
    h7, _ = rx.run_from_bytes((tmp_path / "2026081118.run").read_bytes(),
                              (8, 8))
    assert h7[2, 2] == 0x84                    # C(08-10)=4 completed, today provisional
    bins_before = {t: (tmp_path / f"{t}.bin").read_bytes() for t in frames}
    (tmp_path / "2026081006.run").unlink()
    assert rx.build_frames(tmp_path, times, layer_fn, False, frames,
                           run_seed) == 1      # only the sidecar-less frame
    assert {t: (tmp_path / f"{t}.bin").read_bytes()
            for t in frames} == bins_before    # masks identical -> bytes equal
    h6, _ = rx.run_from_bytes((tmp_path / "2026081006.run").read_bytes(),
                              (8, 8))
    assert h6[2, 2] == 0x83
    # retention: prune_frames drops .bin/.trk/.run older than the floor
    for ext in (".bin", ".trk", ".run"):
        (tmp_path / f"2026070900{ext}").write_bytes(b"x")
    frames.append("2026070900")
    rx.prune_frames(tmp_path, frames, "2026081000")
    assert "2026070900" not in frames
    assert not any((tmp_path / f"2026070900{ext}").exists()
                   for ext in (".bin", ".trk", ".run"))
    assert (tmp_path / "2026081000.run").exists()   # floor kept


def test_ivt_sidecar_orientation(tmp_path, monkeypatch):
    """Real _make_layer_fn seam: ivt6/keep6 arrive mirror-native; the .ivt
    sidecar must be display-oriented (ponytail 23:59Z: native IVT x display
    mask -> garbage classes). Classes now come from the FIELD, so the low 3
    bits hold the class and bit 7 marks IVT >= 400."""
    import xarray as xr
    monkeypatch.setattr(rx, "LAT_N", 8)
    monkeypatch.setattr(rx, "LON_N", 8)
    t = [np.datetime64("2026-08-10T00")]
    lat = np.arange(8.0)
    lon = np.arange(8.0)

    def da(vals, dims, coords, name=None):
        return xr.DataArray(vals, dims=dims, coords=coords, name=name)

    mirror = da(np.full((1, 8, 8), 200.0),
                ("time", "latitude", "longitude"),
                {"time": t, "latitude": lat, "longitude": lon},
                "2m_temperature").to_dataset()
    coords = {"dayofyear": [222], "hour": [0], "latitude": lat, "longitude": lon}
    clim_hi = da(np.zeros((1, 1, 8, 8)), ("dayofyear", "hour", "latitude", "longitude"), coords)
    clim_lo = da(np.full((1, 1, 8, 8), -5.0), ("dayofyear", "hour", "latitude", "longitude"), coords)
    keep = np.zeros((1, 8, 8), bool)
    keep[0, 0, 0] = True                       # native row0 = lat 90
    lab6 = np.zeros((1, 8, 8), np.uint16)
    lab6[0, 0, 0] = 5
    ivt6 = np.zeros((1, 8, 8), float)
    ivt6[0, 0, 0] = 900.0                      # 750-1000 -> class 3
    remap = np.zeros(6, np.uint16)
    remap[5] = 1
    layer_fn = rx._make_layer_fn(mirror, clim_hi, clim_lo, np.ones((8, 8), bool),
                                 keep, lab6, t, remap, ivt6)
    frames: list[str] = []
    assert rx.build_frames(tmp_path, t, layer_fn, False, frames) == 1
    cls = rx.ivt_class_from_bytes((tmp_path / f"{frames[0]}.ivt").read_bytes(), (8, 8))
    cls = cls & rx.IVT_CLASS_MASK          # bit 7 is the >=400 flag, not a class
    assert cls.sum() == 3 and int(cls.max()) == 3
    # the one classed cell sits at the DISPLAY cell of native (0,0):
    # rows flipped, cols rolled by LON_N//2
    assert cls[8 - 1 - 0, (0 - 4) % 8] == 3


def test_ivt_class_plane_is_the_field_not_the_paint():
    """Owner report 2026-11-10 ("IVT values just disappear", north Atlantic
    08-12 18Z -> 08-13 00Z): the class plane used to be masked by the AR paint,
    so every screen and every marginal-Laplacian dropout deleted the IVT field
    too (5 515 cells at IVT >= 400 in that basin, 85 painted). The plane is the
    IVT field now: bins everywhere, plus bit 7 at the detector threshold."""
    ivt = np.array([[0.0, 249.9, 250.0, 400.0, 500.0, 760.0, 1000.0,
                     1250.0, np.nan, -3.0]])
    cls = rx.ivt_class_plane(ivt)
    assert cls[0, 0] == 0                      # below the first edge
    assert cls[0, 1] == 0                      # < 250
    assert cls[0, 2] == 1                      # 250-500, below AR threshold
    assert cls[0, 3] == 1 | rx.IVT_ABOVE_BIT   # 400 exactly: the AR threshold
    assert cls[0, 4] == 2 | rx.IVT_ABOVE_BIT
    assert cls[0, 5] == 3 | rx.IVT_ABOVE_BIT
    assert cls[0, 6] == 4 | rx.IVT_ABOVE_BIT
    assert cls[0, 7] == 5 | rx.IVT_ABOVE_BIT
    assert cls[0, 8] == 0                      # NaN never gets a class
    assert cls[0, 9] == 0
    # and the contract the frontend reads
    assert rx.IVT_CLASS_EDGES == [250, 500, 750, 1000, 1250]
    assert rx.IVT_AR_MIN == 400.0
# ---- duration sidecar v2 (sawtooth fix, diurnal investigation 2026-11-09):
# low 7 bits = consecutive COMPLETED days (constant within a UTC day),
# bit 7 = today so far (provisional) ----

def test_run_recurrence_v2_completed_days():
    T, F = np.ones((1, 1), bool), np.zeros((1, 1), bool)
    days = [[T, T, T, T],            # D1 all steps -> today only (prov, 0 done)
            [T, T, F, F],            # D2 breaks at step 2 -> done=1 after step1
            [T, T, T, T]]            # D3 restarts: completed streak back to 0
    g = rx.run_recurrence(days)
    assert [int(v[0, 0]) for v in g] == \
        [0x80] * 4 + [0x81, 0x81, 0x01, 0x01] + [0x80] * 4
    # solid count never changes within a day: no provisional +1 at 00Z, no
    # midnight reset — the whole sawtooth fix in one assertion
    assert {int(v[0, 0]) & 0x7F for v in g[4:8]} == {1}


def test_run_recurrence_v2_seed_and_saturation():
    T, F = np.ones((1, 1), bool), np.zeros((1, 1), bool)
    g = rx.run_recurrence([[T, F, T, T]], seed=np.full((1, 1), 3, np.uint8))
    assert [int(v[0, 0]) for v in g] == [0x83, 0x03, 0x03, 0x03]
    # saturated streak stays at RUN_CAP (no u8 overflow into the prov bit)
    grids, c = rx._run_day([T, T, T, T], np.full((1, 1), rx.RUN_CAP, np.uint8))
    assert int(c[0, 0]) == rx.RUN_CAP


def test_run_sidecar_v2_in_incremental_build(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "LAT_N", 8)
    monkeypatch.setattr(rx, "LON_N", 8)
    times = [np.datetime64("2026-08-10T00"), np.datetime64("2026-08-10T06"),
             np.datetime64("2026-08-11T00"), np.datetime64("2026-08-11T06")]

    def layer_fn(day_times):
        n = len(day_times)
        m = np.zeros((n, 8, 8), bool)
        m[:, 2:4, 2:4] = True
        return m, np.zeros_like(m), np.zeros_like(m)

    assert rx.build_frames(tmp_path, times, layer_fn, True, []) == 4
    h1, f1 = rx.run_from_bytes((tmp_path / "2026081000.run").read_bytes(), (8, 8))
    assert (h1[2:4, 2:4] == 0x80).all() and not (h1 & 0x7F).any() and not f1.any()
    # day 2 with the pipeline's seed contract (run_seed from _daily_runs):
    # C(2026-08-10) = 1 completed day -> base 1 at every step of day 2
    seed = {"2026-08-10": (np.full((8, 8), 1, np.uint8),
                           np.zeros((8, 8), np.uint8))}
    assert rx.build_frames(tmp_path, times, layer_fn, True, [], seed) == 4
    h2, _ = rx.run_from_bytes((tmp_path / "2026081100.run").read_bytes(), (8, 8))
    assert (h2[2:4, 2:4] == 0x81).all()   # 1 completed day + today provisional
