"""Offline tests for the raw_exceedance frame codec + incremental build."""
import numpy as np

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
    assert _translate(tmp_path, "/data/results/raw_exceedance/index.json") \
        == str(tmp_path / "raw_exceedance" / "index.json")
    for bad in ("2026.bin", "evil.parquet", "2026081012345.bin"):
        assert "nonesuch" in _translate(tmp_path, f"/data/results/raw_exceedance/{bad}")
    assert "nonesuch" in _translate(tmp_path, "/data/results/raw_exceedance")


def test_frame_codec_roundtrip():
    rng = np.random.default_rng(0)
    h = rng.random((37, 61)) > 0.8
    f = rng.random((37, 61)) > 0.9
    a = rng.random((37, 61)) > 0.7
    raw = rx.frame_bytes(h, f, a)
    h2, f2, a2 = rx.frame_layers(raw, h.shape)
    assert (h == h2).all() and (f == f2).all() and (a == a2).all()
    # sparse frames must compress hard
    assert len(raw) < (3 * 37 * 61 + 7) // 8


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
        return m, np.zeros_like(m), np.zeros_like(m)

    frames: list[str] = []
    assert rx.build_frames(tmp_path, times, layer_fn, False, frames) == 4
    assert calls == [2, 2]                       # one call per day, not per frame
    assert sorted(frames) == ["2026081000", "2026081006",
                              "2026081100", "2026081106"]
    h, f, a = rx.frame_layers((tmp_path / "2026081000.bin").read_bytes(), (8, 8))
    assert h[2:4, 2:4].all() and not h[:2].any() and not f.any()
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
