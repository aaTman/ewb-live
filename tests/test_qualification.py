"""qualified_at (merged global map, owner 2026-11-08): first 6-hourly frame
meeting the detection floors inside the event bbox, fallback 00Z of start."""

import datetime as dt
import json
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ewb_live.events import qualification as q

NY, NX, STEP = 145, 145, 2.5  # lat/lon -90..(90 step), -180..180 (full globe)


def _write_frame(d: Path, name: str, layer: int, cells: list[tuple[int, int]]):
    stride = (NX + 7) // 8
    layers = [np.zeros((NY, stride), np.uint8) for _ in range(3)]
    rows = {}
    for r, c in cells:
        rows.setdefault(r, set()).add(c)
    for r, cs in rows.items():
        bits = np.zeros(NX, np.uint8)
        for c in cs:
            bits[c] = 1
        layers[layer][r] = np.packbits(bits.reshape(1, -1), axis=1)[0]
    blob = zlib.compress(b"".join(l.tobytes() for l in layers))
    (d / f"{name}.bin").write_bytes(blob)


def _index(d: Path, frames: list[str]):
    (d / "index.json").write_text(json.dumps({
        "schema": 1,
        "grid": {"res_deg": STEP, "ny": NY, "nx": NX,
                 "lat0": -90.0, "lon0": -180.0, "step_lat": STEP, "step_lon": STEP},
        "frames": frames,
    }))


def _event(etype, start, end, bbox):
    return SimpleNamespace(event_type=etype,
                           start=dt.datetime.fromisoformat(start),
                           end=dt.datetime.fromisoformat(end), bbox=bbox)


def _heat_event():
    # bbox lat 10..20 (rows 40..44), lon 10..20 (cols 76..80) -> 25 cells
    # cell km2 = (111.32*2.5)^2*cos(15deg) ~ 74.8k -> floor = 3 cells
    return _event("heat_wave", "2026-09-10T00:00", "2026-09-13T00:00",
                  (10.0, 20.0, 10.0, 20.0))


def test_first_qualifying_frame_wins(tmp_path):
    fr = [(r, c) for r in range(40, 45) for c in range(76, 81)]
    _index(tmp_path, ["2026091000", "2026091006", "2026091012", "2026091018"])
    _write_frame(tmp_path, "2026091000", 0, [])                      # empty
    _write_frame(tmp_path, "2026091006", 0, [(40, 76), (40, 77)])    # 2 < 3
    _write_frame(tmp_path, "2026091012", 0, fr[:6])                  # 6 >= 3
    _write_frame(tmp_path, "2026091018", 0, fr)
    assert q.qualified_at(_heat_event(), tmp_path) == "2026-09-10T12:00:00Z"


def test_pre_window_and_after_end_frames_ignored(tmp_path):
    fr = [(r, c) for r in range(40, 45) for c in range(76, 81)]
    _index(tmp_path, ["2026090912", "2026091000", "2026091306"])
    _write_frame(tmp_path, "2026090912", 0, fr)   # before start: ignored
    _write_frame(tmp_path, "2026091000", 0, [])
    _write_frame(tmp_path, "2026091306", 0, fr)   # end exclusive: ignored
    assert q.qualified_at(_heat_event(), tmp_path) == "2026-09-10T00:00:00Z"


def test_missing_index_falls_back_to_start_midnight(tmp_path):
    assert q.qualified_at(_heat_event(), tmp_path) == "2026-09-10T00:00:00Z"


def test_ar_wrapped_bbox_counts_across_seam(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "AR_MIN_PX", 10)  # tiny synthetic grid
    ev = _event("atmospheric_river", "2026-09-10T00:00", "2026-09-11T00:00",
                (10.0, 20.0, 175.0, -175.0))
    # lon 175 -> col (175+180)/2.5 = 142; lon -175 -> col 2; arc = 142..144 +
    # 0..2 (wraps). Put 6 px each side of the seam.
    cells = [(r, c) for r in range(40, 43) for c in (142, 143, 0, 1)]
    _index(tmp_path, ["2026091000", "2026091006"])
    _write_frame(tmp_path, "2026091000", 2, cells[:5])   # 5 < 10
    _write_frame(tmp_path, "2026091006", 2, cells)       # 12 >= 10
    assert q.qualified_at(ev, tmp_path) == "2026-09-10T06:00:00Z"


def test_torn_frame_skipped(tmp_path):
    ev = _heat_event()
    _index(tmp_path, ["2026091000", "2026091006"])
    _write_frame(tmp_path, "2026091000", 0,
                 [(40, 76), (41, 76), (42, 76), (43, 76)])
    (tmp_path / "2026091006.bin").write_bytes(b"garbage")  # torn: skipped
    assert q.qualified_at(ev, tmp_path) == "2026-09-10T00:00:00Z"
