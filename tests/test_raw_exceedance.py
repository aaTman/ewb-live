"""Offline tests for the raw_exceedance frame codec + incremental build."""
import json

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
        return m, np.zeros_like(m), np.zeros_like(m), np.zeros((n, 8, 8), np.uint16)

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


# ---- published qualification gate (contract notes/spec_min_gate.md §B):
# index.dur.min is the per-lane PAINT minimum -- heat follows the detector's
# MIN_CONSECUTIVE_DAYS (read from upstream, never a hardcoded 3), freeze is the
# owner's 1-day paint floor (2026-11-10) -- and must survive an index rewrite.

def test_dur_block_min_per_lane(monkeypatch):
    from ewb_live.events import _upstream
    real = _upstream.temperature_events().MIN_CONSECUTIVE_DAYS
    assert rx.dur_block("2026072500") == {
        "rev": "v2", "seed": "2026072500",
        "min": {"heat": real, "freeze": rx.FREEZE_PAINT_MIN}}
    assert rx.FREEZE_PAINT_MIN == 1          # owner 2026-11-10: freeze from day 1

    # a non-3 upstream value flows through unchanged: proves the heat gate is
    # wired to the detector constant and not a duplicated literal
    class FakeTE:
        MIN_CONSECUTIVE_DAYS = real + 2
    monkeypatch.setattr(_upstream, "temperature_events", lambda: FakeTE)
    assert rx.dur_block("2026072500")["min"] == {
        "heat": real + 2, "freeze": rx.FREEZE_PAINT_MIN}


def test_dur_min_survives_incremental_index_rewrite(tmp_path):
    """Incremental builds keep existing frames and refresh the dur block;
    dur.min must still be published after the index round-trip."""
    from ewb_live.events import _upstream
    real = _upstream.temperature_events().MIN_CONSECUTIVE_DAYS
    out = tmp_path / rx.OUT_DIRNAME
    out.mkdir(parents=True)

    first = rx._load_index(out)
    first["frames"] = ["2026081000", "2026081006"]   # already-done frames
    first["dur"] = rx.dur_block("2026072500")
    (out / "index.json").write_text(json.dumps(first))

    # second (incremental) pass: load, keep frames, rewrite dur
    again = rx._load_index(out)
    assert again["frames"] == ["2026081000", "2026081006"]
    again["dur"] = rx.dur_block("2026072500")
    (out / "index.json").write_text(json.dumps(again))

    final = rx._load_index(out)
    assert final["dur"]["min"] == {"heat": real, "freeze": 1}
