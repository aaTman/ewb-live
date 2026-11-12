"""sr18 coverage-diff gate (review lane, 2026-11-10): hole detection,
clean-diff rc0, drift rc1. Synthetic fixture; runs the script in tmp_path
so its relative data/results + notes paths resolve there."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "review" / "sr18_coverage.py"


def _fixture(tmp_path):
    r = tmp_path / "data" / "results"
    r.mkdir(parents=True)
    (tmp_path / "notes").mkdir()
    json.dump([{"id": f"e{i}", "event_type": "heat_wave"} for i in (1, 2, 3)],
              open(r / "events_selected.json", "w"))
    rows = [{"event_id": e, "event_type": "heat_wave", "model": m,
             "metric": "CSI", "lead_time_hours": l}
            for e, leads in {"e1": [0], "e2": [0, 6]}.items()
            for m in ("m1", "m2") for l in leads]
    rows.append({"event_id": "e9", "event_type": "heat_wave", "model": "m1",
                 "metric": "CSI", "lead_time_hours": 0})  # scored, not shipped
    pd.DataFrame(rows).to_parquet(r / "scores.parquet")


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=tmp_path, capture_output=True, text=True)


def test_sr18_detects_all_hole_classes(tmp_path):
    _fixture(tmp_path)
    r = _run(tmp_path, "--write")
    assert r.returncode == 0
    snap = json.loads(r.stdout[r.stdout.index("{"):])
    assert set(snap["holes"]) == {
        "missing_event e3",
        "STALE_PART event e9 scored but not shipped",
        "short_leads e1 m1 CSI 1/2",
        "short_leads e1 m2 CSI 1/2",
    }


def test_sr18_diff_mode_clean_then_drift(tmp_path):
    _fixture(tmp_path)
    base = tmp_path / "notes" / "coverage_baseline.json"
    out = _run(tmp_path, "--write").stdout
    snap = json.loads(out[out.index("{"):])
    json.dump(snap, open(base, "w"))
    assert _run(tmp_path).returncode == 0
    snap["holes"] = [h for h in snap["holes"] if "missing_event" not in h]
    json.dump(snap, open(base, "w"))
    r = _run(tmp_path)
    assert r.returncode == 1 and "NEW-HOLE missing_event e3" in r.stdout
