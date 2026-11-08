"""Offline tests for the global_timeline data asset (no network, no box)."""
import base64
import json
from pathlib import Path

import numpy as np
import pytest

from ewb_live.viz import timeline


def _pack_daily(m):  # (nd,ny,nx) bool -> daily_bits dict, build.pack_bits contract
    stride = -(-m.shape[1] * m.shape[2] // 8)
    blob = b"".join(np.packbits(m[d].ravel()).tobytes() for d in range(m.shape[0]))
    return {"b64": base64.b64encode(blob).decode(), "ndays": m.shape[0],
            "ny": m.shape[1], "nx": m.shape[2]}


@pytest.fixture()
def results(tmp_path):
    r = tmp_path / "results"
    (r / "events" / "h1").mkdir(parents=True)
    (r / "events" / "a1").mkdir()
    heat = np.zeros((2, 10, 20), bool)
    heat[0, 2:4, 5:9] = True
    heat[1, 3:5, 6:10] = True
    ar = np.zeros((10, 20), bool)
    ar[0:3, 10:15] = True
    (r / "events" / "h1" / "viz.json").write_text(json.dumps({
        "schema": 3, "grid": {"lat": list(np.arange(0, 2.5, 0.25)),
                              "lon": list(np.arange(0, 5.0, 0.25)),
                              "cell_deg": 0.25},
        "obs": {"daily_bits": _pack_daily(heat)}}))
    (r / "events" / "a1" / "viz.json").write_text(json.dumps({
        "schema": 3, "grid": {"lat": list(np.arange(40, 45.0, 0.5)),
                              "lon": list(np.arange(178, 188.0, 0.5)),  # crosses 180
                              "cell_deg": 0.5},
        "obs": {"mask": "/".join("".join("1" if v else "0" for v in row) for row in ar)}}))
    (r / "events_selected.json").write_text(json.dumps([
        {"id": "h1", "event_type": "heat_wave", "title": "H", "start": "2026-09-01",
         "end": "2026-09-03", "bbox": [0, 2.25, 0, 4.75]},
        {"id": "a1", "event_type": "atmospheric_river", "title": "A", "start": "2026-09-02",
         "end": "2026-09-04", "bbox": [40, 44.5, 178, -176],
         "qualified_at": "2026-09-02T06:00:00"},
        {"id": "g1", "event_type": "heat_wave", "title": "no-asset", "start": "2026-09-05T00:00:00",
         "end": "2026-09-06", "bbox": [10, 12, 10, 12]}]))
    (r / "meta.json").write_text(json.dumps({"window": {"start": "2026-08-10", "end": "2026-11-08"}}))
    (r / "raw_exceedance").mkdir()
    (r / "raw_exceedance" / "index.json").write_text(json.dumps(
        {"schema": 1, "grid": {"lat0": -90.0}, "frames": ["2026081000", "2026110118"],
         "cutoff": "2026110118"}))
    return r


def test_build_roundtrip(results):
    out = timeline.build(results)
    doc = json.loads(out.read_text())
    assert doc["schema"] == 2 and doc["window"]["start"] == "2026-08-10"  # 2: additive (qualified_at, raw header)
    by_id = {e["id"]: e for e in doc["events"]}
    assert set(by_id) == {"h1", "a1", "g1"}
    h = by_id["h1"]
    assert h["days"] == ["2026-09-01", "2026-09-02"]      # end is EXCLUSIVE
    assert h["grid"]["ny"] == 3 and h["grid"]["nx"] == 5  # 10x20 @0.25 -> ~1 deg
    m0 = np.unpackbits(np.frombuffer(base64.b64decode(h["masks"][0]), dtype=np.uint8))[:h["grid"]["ny"] * h["grid"]["nx"]] \
        .reshape(h["grid"]["ny"], h["grid"]["nx"]).astype(bool)
    assert m0.any()               # coarsened OR keeps the blob
    assert not m0[2].any() if m0.shape[0] > 2 else True
    a = by_id["a1"]
    assert a["grid"]["continuous_lon"] is True and a["grid"]["lon1"] > 180
    assert len(a["masks"]) == len(a["days"]) == 2          # anchor mask per day
    assert any(np.unpackbits(np.frombuffer(base64.b64decode(m), dtype=np.uint8))[:64].any() for m in a["masks"])
    assert "masks" not in by_id["g1"] and by_id["g1"]["bbox"]  # bbox-only fallback
    assert all(len(d) == 10 for d in by_id["g1"]["days"])      # date-only, never a datetime (ponytail 14:31)
    assert by_id["a1"]["qualified_at"] == "2026090206" and by_id["a1"]["q_source"] == "detection"
    assert by_id["h1"]["qualified_at"] == "2026090100" and by_id["h1"]["q_source"] == "start_fallback"
    assert all(e.get("qualified_at") for e in by_id.values())  # merged map: every entry step-keyed
    doc_raw = json.loads(out.read_text())["raw"]
    assert doc_raw["cutoff"] == "2026110118" and doc_raw["first"] == "2026081000"
    assert out.stat().st_size < timeline.SIZE_LIMIT
