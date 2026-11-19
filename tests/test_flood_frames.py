"""Unit tests for ewb_live/viz/flood_frames (flood lane, defect F0).

Class coding, contract orientation, byte codec, the additive index block —
and the build() flow end to end on a synthetic scan (monkeypatched join, so
no IMERG store / climatology needed; the real join is exercised by the flood
lane's own tests).
"""
import datetime as dt
import json
import zlib
from types import SimpleNamespace

import numpy as np

from ewb_live.events import flood
from ewb_live.viz import flood_frames as ff


def test_classes_from_lanes_bins_and_mask_gate():
    m = np.zeros((2, 5), bool)
    m[:] = True
    m[1, 4] = False                              # off-mask cell must read 0
    d = np.array([[20., 49.9, 50., 200., 0.], [350., 999., 0., 25., 0.]])
    a3 = np.zeros_like(d)
    cls = ff.classes_from_lanes(m, d, a3)
    assert cls[0, 0] == 1 and cls[0, 1] == 1     # >=20 <50 -> class 1
    assert cls[0, 2] == 2 and cls[0, 3] == 4     # 50-100 -> 2, 200-350 -> 4
    assert cls[1, 0] == 5 and cls[1, 1] == 5     # >=350 -> 5 (saturates)
    assert cls[1, 2] == 1    # on-mask intensity below the first edge rides 1
                             # (the 72 h-lane ride rule, never a hidden 0)
    assert cls[1, 4] == 0    # mask-off is always class 0


def test_classes_rides_lightest_for_72h_only():
    m = np.ones((1, 2), bool)
    d = np.array([[5., np.nan]])                 # daily below floor / missing
    a3 = np.array([[60., 150.]])                 # 20 and 50 mm/day-equiv
    cls = ff.classes_from_lanes(m, d, a3)
    assert cls.tolist() == [[1, 2]]              # 60/72h=20 -> 1; 150/72h=50 -> 2
                                                 # (an exact edge rides the
                                                 # upper bin, side='right')


def test_classes_mask_off_is_always_zero():
    m = np.zeros((1, 3), bool)
    cls = ff.classes_from_lanes(m, np.array([[999., 999., 999.]]),
                                np.array([[9999., 9999., 9999.]]))
    assert cls.tolist() == [[0, 0, 0]]


def test_to_display_flips_rows_only():
    a = np.arange(12, dtype="uint8").reshape(3, 4)
    d = ff.to_display(a)
    assert np.array_equal(d[0], a[2]) and np.array_equal(d[2], a[0])
    assert np.array_equal(d, a[::-1, :])


def test_cls_codec_roundtrip_and_size_guard():
    grid = np.zeros((7, 11), "uint8")
    grid[3, 4] = 5
    raw = ff.cls_bytes(grid)
    assert len(zlib.decompress(raw)) == 77
    back = ff.cls_from_bytes(raw, (7, 11))
    assert np.array_equal(back, grid)
    import pytest
    with pytest.raises(ValueError, match="payload"):
        ff.cls_from_bytes(raw, (7, 12))


def test_flood_index_block_sorts_and_carries_the_contract():
    blk = ff.flood_index_block(["20260103", "20260101"], ["2026-01-02"],
                               {"20260103": "early", "20260101": "late"})
    assert blk["days"] == ["20260101", "20260103"]
    assert blk["absent"] == ["2026-01-02"]
    assert list(blk["runs"]) == ["20260101", "20260103"]
    assert blk["bins_mm"] == list(ff.BINS_MM) and blk["units"] == "mm/day-equiv"


def test_merge_index_is_additive_and_skipped_without_index(tmp_path):
    p = tmp_path / "index.json"
    ff.merge_index(tmp_path, {"rev": "v1"})     # no index yet: skip, no crash
    assert not p.exists()
    p.write_text(json.dumps({"frames": ["2026010100"], "dur": {"rev": "v2"}}))
    ff.merge_index(tmp_path, {"rev": "v1", "days": ["20260101"]})
    idx = json.loads(p.read_text())
    assert idx["frames"] == ["2026010100"] and idx["dur"]["rev"] == "v2"
    assert idx["flood"]["days"] == ["20260101"]


def test_prune_frames_keeps_window_start(tmp_path):
    old = tmp_path / "flood_20251231.cls"
    keep = tmp_path / "flood_20260101.cls"
    other = tmp_path / "2026010100.bin"
    for f in (old, keep, other):
        f.write_bytes(b"x")
    ff.prune_frames(tmp_path, "20260101")
    assert not old.exists() and keep.exists() and other.exists()


# --------------------------------------------------------------------------
# build() flow on a synthetic scan (window 2026-01-01 .. 2026-01-04)
# --------------------------------------------------------------------------
def _mk_root(tmp_path):
    res = tmp_path / "data" / "results"
    (res / "raw_exceedance").mkdir(parents=True)
    (res / "meta.json").write_text(json.dumps(
        {"window": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-05T00:00:00Z"}}))
    (res / "raw_exceedance" / "index.json").write_text(json.dumps(
        {"schema": 3, "frames": ["2026010100"], "grid": {"ny": 3, "nx": 4,
                                                         "res_deg": 0.25}}))
    return tmp_path


def _patch_scan(monkeypatch):
    dates = [dt.date(2026, 1, d) for d in range(1, 6)]   # Jan 5 = outside window
    ny, nx = 3, 4
    mask = np.zeros((5, ny, nx), bool)
    lane1 = np.zeros((5, ny, nx), "float32")
    lane3 = np.zeros_like(lane1)
    for k in (2, 3):            # Jan 3 + Jan 4 carry evidence (Jan 4 is absent
        mask[k, 1, 1] = True    # by `present` — its frame must NOT be written)
        lane1[k, 1, 1] = 60.0                              # class 2
    mask[2, 2, 2] = True
    lane3[2, 2, 2] = 90.0                                  # 30 mm/day-equiv -> 1
    present = np.array([True, True, True, False, True])    # Jan 4 unobserved
    scan = SimpleNamespace(dates=dates, mask=mask, obs_lane={1: lane1, 3: lane3},
                           present=present,
                           runs={f"2026-01-{d:02d}": "late" for d in range(1, 6)},
                           source={"missing_days": ["2026-01-04"]},
                           quantile=0.95)
    monkeypatch.setattr(flood, "load_observations",
                        lambda *a, **k: SimpleNamespace(grid=(np.zeros(1), np.zeros(1))))
    monkeypatch.setattr(flood, "land_mask", lambda la, lo: np.ones((ny, nx), bool))
    monkeypatch.setattr(flood, "build_scan", lambda obs, **k: scan)
    return scan


def test_build_writes_frames_merges_index_and_skips_absent(tmp_path, monkeypatch):
    _mk_root(tmp_path)
    _patch_scan(monkeypatch)
    ff.build(tmp_path)
    rd = tmp_path / "data" / "results" / "raw_exceedance"
    # window days Jan 1..Jan 4; Jan 4 carries present=False -> NO frame,
    # never in days; empty days still write (an exceedance-free day is real)
    assert sorted(p.name for p in rd.glob("flood_*.cls")) == [
        "flood_20260101.cls", "flood_20260102.cls", "flood_20260103.cls"]
    grid = ff.cls_from_bytes((rd / "flood_20260103.cls").read_bytes(), (3, 4))
    # detector cells (1,1) class 2 and (2,2) class 1, rows flipped to display
    assert grid[3 - 1 - 1, 1] == 2 and grid[3 - 1 - 2, 2] == 1
    assert grid[1, 2] == 0 and grid[0, 1] == 0   # untouched cells stay 0
    idx = json.loads((rd / "index.json").read_text())
    assert idx["schema"] == 3                       # other keys preserved
    assert idx["flood"]["rev"] == ff.REV
    assert idx["flood"]["days"] == ["20260101", "20260102", "20260103"]
    assert idx["flood"]["absent"] == ["2026-01-04"]
    assert "20260104" not in idx["flood"]["days"]   # absent day never fetches


def test_build_incremental_and_force(tmp_path, monkeypatch):
    _mk_root(tmp_path)
    _patch_scan(monkeypatch)
    ff.build(tmp_path)
    rd = tmp_path / "data" / "results" / "raw_exceedance"
    stamps = {p: p.stat().st_mtime_ns for p in rd.glob("flood_*.cls")}
    ff.build(tmp_path)                              # incremental: no rewrites
    assert {p: p.stat().st_mtime_ns for p in rd.glob("flood_*.cls")} == stamps
    ff.build(tmp_path, force=True)                  # --force: every day rewritten
    assert all(p.stat().st_mtime_ns != stamps[p] for p in stamps)
