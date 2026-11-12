"""The About page's criteria copy and Python listing must match the detector.

`dashboard/detection.py` is the plain-Python reproduction the About dialog shows
("Reproduce the tracker in Python"). It is documentation that people run, so it
is tested as code: the same masks go through the listing and through the shipped
tracker, and the events must agree. The wording of the criteria is checked
against the shipped `Params`, so a threshold change cannot leave the page
describing the old rule.
"""
import datetime as dt
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from ewb_live.events.event_tracker import Params, track_events

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytest.importorskip("scipy")
D = _load("detection_listing_under_test", DASH / "detection.py")

NY, NX = 60, 120
LATS = np.linspace(-50, 50, NY)
AREA = D.cell_area_km2(LATS, 360.0 / NX, NX)
Z = np.zeros((NY, NX), bool)
DAYS = [dt.date(2026, 9, 1) + dt.timedelta(days=k) for k in range(12)]


def _patch(rows, cols):
    m = np.zeros((NY, NX), bool)
    m[rows, cols] = True
    return m


CASES = {
    "steady": [_patch(slice(20, 24), slice(20, 26))] * 5 + [Z] * 7,
    "too short": [_patch(slice(20, 24), slice(20, 26))] * 2 + [Z] * 10,
    "builds up": [_patch(slice(20, 22), slice(20, 26))] * 2
    + [_patch(slice(20, 24), slice(20, 26))] * 4 + [Z] * 6,
    "two places": [_patch(slice(10, 13), slice(10, 16))
                   | _patch(slice(40, 44), slice(60, 66))] * 5 + [Z] * 7,
    "gap day": [_patch(slice(20, 24), slice(20, 26))] * 2 + [Z]
    + [_patch(slice(20, 24), slice(20, 26))] * 4 + [Z] * 5,
    "runs away": [_patch(slice(20, 24), slice(10 + 3 * k, 16 + 3 * k))
                  for k in range(6)] + [Z] * 6,
    "two spells": [_patch(slice(20, 24), slice(20, 26))] * 4 + [Z] * 2
    + [_patch(slice(20, 24), slice(20, 26))] * 4 + [Z] * 2,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_listing_agrees_with_the_shipped_tracker(name):
    masks = CASES[name]
    page = D.detect(DAYS[:len(masks)], dict(zip(DAYS, masks)), AREA)
    shipped = track_events(np.array(masks), DAYS[:len(masks)], AREA, Params(), "heat_wave")
    key = lambda hz, s, e: (hz, str(s), str(e))     # noqa: E731
    assert sorted((key(e["hazard"], e["start"], e["end"]) for e in page)) == sorted(
        (key(e.hazard, e.start, e.end) for e in shipped))


def test_listing_demo_runs_and_finds_the_planted_event():
    out = subprocess.run([sys.executable, str(DASH / "detection.py")],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    assert "4 d" in out.stdout                       # the planted 4-day heat wave
    assert out.stdout.strip().count("\n") == 0        # the too-small patch is not an event


def test_about_copy_states_the_shipped_thresholds():
    body = (DASH / "index.html").read_text().split('id="about-dialog"', 1)[1]
    p = Params()
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    checks = {
        "seed floor": f"{int(p.seed_area_km2):,}&nbsp;km²",
        "qualify floor": f"{int(p.qualify_area_km2):,}&nbsp;km²",
        "end floor": f"{int(p.end_area_km2):,}&nbsp;km²",
        "closing window": f"{2 * p.region_radius + 1}×{2 * p.region_radius + 1} window",
        "local density": f"{int(p.region_min_fraction * 100)}&nbsp;%",
        "min length": f"<b>{p.min_days}&nbsp;days</b>",
    }
    for what, text in checks.items():
        assert text in body, f"About page does not state the {what} ({text!r})"
    assert re.search(rf"up to {p.move_cells}\s+cells a day", body), "box drift is stale"
    assert f"has {words[p.max_ramp_days - 1]} days to become an event" in body, \
        "the qualify window in the copy does not match Params.max_ramp_days"
    assert p.anchor_day == 3 and "day&nbsp;3" in body


def test_page_wires_the_listing():
    about = (DASH / "index.html").read_text()
    assert 'id="detection-code"' in about
    assert re.search(r'href="detection\.py"', about)
    app = (DASH / "app.js").read_text()
    assert "fetch('detection.py')" in app
    # the empty-lane criteria tooltip must not keep a superseded rule's numbers
    crit = app.split("const TYPE_CRITERIA = {", 1)[1].split("};", 1)[0]
    for text in ("50,000 km²", "200,000 km²", "100,000 km²"):
        assert text in crit
