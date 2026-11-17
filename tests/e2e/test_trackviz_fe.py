"""TrackViz e2e (tc lane, 2026-11-16): the best-track renderer for TRACK lanes.

The track lanes (tropical_cyclone today, midlat_cyclone next) have no
viz.json asset — the event carries its own geometry in ``details.track`` and
dashboard/trackviz.js draws it. This test mounts the renderer directly against
a synthetic event on a static harness page: no pipeline, no snapshot, no live
box, so it runs anywhere Playwright's chromium is installed.

Run:  .venv/bin/python -m pytest tests/e2e/test_trackviz_fe.py -m e2e
Screenshots land in notes/screenshots/fe-tc-track*.png for the owner review.
"""
import json
import os
import shutil
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DASH = os.path.join(ROOT, "dashboard")
SHOTS = os.path.join(ROOT, "notes", "screenshots")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

# one storm that CROSSES THE ANTIMERIDIAN (178E -> 162W, longitudes kept in the
# repo's [-180, 180] convention), 3-hourly-free 6-hourly fixes, with a sub-window
# as the event: the first two fixes and the last are the system's life OUTSIDE
# the event, the wind ramps TD -> CAT4 -> TS, and one fix has a null pressure
# (JSON null, never NaN, per the public-JSON contract).
_WINDS = [22, 30, 34, 45, 60, 78, 96, 118, 95, 60, 40]
_LONS = [170.0, 174.0, 178.0, -178.0, -174.0, -170.0, -166.0, -162.0, -158.0, -154.0, -150.0]
_T0 = datetime(2026, 10, 9, 0)
TRACK = [[(_T0 + timedelta(hours=6 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
          12.0 + 1.4 * i, lon, w, (1002.0 - 7.0 * i if i != 5 else None)]
         for i, (w, lon) in enumerate(zip(_WINDS, _LONS))]

EVENT = {
    "id": "tcfake0001",
    "event_type": "tropical_cyclone",
    "title": "Tropical Cyclone Fixture",
    # naive ISO on purpose: the schema allows it and the renderer must read it as UTC.
    # fixes 2..9 are inside the window; 0, 1 and 10 are its life outside it.
    "start": "2026-10-09T12:00:00",
    "end": "2026-10-11T12:00:00",
    "bbox": [6.0, 31.0, 165.0, -160.0],
    "peak_value": 118.0,
    "details": {
        "sid": "2026280N12170", "name": "Fixture", "basin": "WP", "basin_name": "W. Pacific",
        "peak_wind_kt": 118.0, "peak_category": "CAT 4", "min_pressure_hpa": 941.0,
        "intensity_units": "kt", "peak_value_units": "kt", "fix_step_h": 6.0,
        "wind_source": "USA", "source": "IBTrACS v04r01 (NOAA NCEI, best track)",
        "track": TRACK, "track_crosses_antimeridian": True, "antimeridian_wrap": True,
    },
}

HARNESS = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="styles.css">
<script src="vendor/d3-array.min.js"></script>
<script src="vendor/d3-geo.min.js"></script>
<script src="trackviz.js"></script>
</head><body><div id="host" style="max-width:860px;margin:0 auto"></div>
<script>
window.mount = (theme) => {
  if (theme) document.documentElement.dataset.theme = theme;
  const host = document.getElementById('host');
  TrackViz.teardown();
  TrackViz.render(host, window.__EV);
  return host.querySelectorAll('circle').length;
};
window.mountNull = () => {
  document.documentElement.removeAttribute('data-theme');
  const bad = JSON.parse(JSON.stringify(window.__EV));
  bad.details.track = bad.details.track.map((r) => [r[0], r[1], r[2], null, null]);
  TrackViz.teardown();
  TrackViz.render(document.getElementById('host'), bad);
};
</script></body></html>"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("tv"))
    for f in ("styles.css", "trackviz.js", "vendor"):
        s = os.path.join(DASH, f)
        shutil.copytree(s, os.path.join(root, f)) if os.path.isdir(s) else shutil.copy2(s, root)
    # land is optional for the renderer but the panel fetches it — give it the real one
    shutil.copytree(os.path.join(DASH, "geo"), os.path.join(root, "geo"))
    with open(os.path.join(root, "index.html"), "w") as fh:
        fh.write(HARNESS)
    # bind port 0 and read it back — no probe/rebind race on shared runners
    handler = lambda *a, **k: _Quiet(*a, directory=root, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


@pytest.fixture()
def page(server):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 960, "height": 900})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(server)
        pg.wait_for_function("typeof TrackViz !== 'undefined'")
        pg.evaluate("([ev]) => { window.__EV = ev }", [EVENT])
        pg._errs = errs                      # type: ignore[attr-defined]
        yield pg
        b.close()


def _fix_circles(pg, sel="circle:not([data-copy])"):
    """Primary circle per fix, in DOM order (the renderer paints context first,
    then the window, then the peak), keyed by its data-i fix index."""
    out = pg.eval_on_selector_all(f"#host {sel}",
                                  "cs => cs.map(c => ({i: +c.getAttribute('data-i'), "
                                  "cx: +c.getAttribute('cx'), r: +c.getAttribute('r'), "
                                  "fill: c.getAttribute('fill'), cls: c.getAttribute('class')}))")
    return {c["i"]: c for c in out}


def test_track_renders_every_fix(page):
    n = page.evaluate("() => window.mount()")
    assert len(_fix_circles(page)) == len(TRACK), "one primary circle per track fix"
    assert n >= len(TRACK)
    assert page._errs == []
    assert page.locator("#host svg").count() == 1
    # head line, five legend swatches and the explanation are all present
    assert "Best track" in page.inner_text(".tv-head")
    assert page.locator(".tv-legend .tv-sw").count() == 5
    assert "event window" in page.inner_text(".tv-note")


def test_peak_and_window_marks(page):
    page.evaluate("() => window.mount()")
    circles = _fix_circles(page)
    peak = [c for c in circles.values() if "tv-fix-peak" in (c["cls"] or "")]
    assert len(peak) == 1 and peak[0]["r"] == 6, "the strongest fix is drawn biggest"
    assert peak[0]["i"] == _WINDS.index(max(_WINDS)), "peak = the strongest fix in the window"
    assert page.eval_on_selector("#host text.tv-lab", "n => n.textContent") == "118 kt"
    # fixes outside start/end are the faded, smaller ones (two before, one after)
    out = [c for c in circles.values() if "tv-fix-out" in (c["cls"] or "")]
    assert len(out) == 3, "the fixes before storm force and after the window read as context"
    assert all(c["r"] == 2.4 for c in out)
    # two track lines per world copy: the full life (dashed) and the window (solid)
    assert page.locator("path.tv-line-pre").count() >= 1
    assert page.locator("path.tv-line-qual").count() >= 1


def test_intensity_colours_are_binned(page):
    page.evaluate("() => window.mount()")
    fills = {c["fill"] for c in _fix_circles(page).values()}
    assert len(fills) >= 4, "the wind ramp distinguishes the storm's intensities"


def test_antimeridian_unwraps_instead_of_jumping(page):
    """A crossing track must stay continuous: after 180°E the fixes keep moving
    RIGHT (the globe clip cuts them at the edge) and the same fixes are painted
    again as the next world copy at the left edge — instead of the track
    snapping back across the map as a 350-degree jump."""
    page.evaluate("() => window.mount()")
    w = int(page.get_attribute("#host svg", "width") or 0)
    circles = _fix_circles(page)
    assert len(circles) == len(TRACK)
    # fixes 0..2 are west of the dateline and keep increasing in x
    pre = [circles[i]["cx"] for i in range(3)]
    assert pre == sorted(pre) and pre[0] > w * 0.5, "the approach runs along the right edge"
    # the fixes east of the dateline are NOT drawn back on the left half: they
    # continue past the right edge (clipped) and reappear as world copies
    post = [circles[i]["cx"] for i in range(3, len(TRACK))]
    assert all(cx > w * 0.5 for cx in post), "post-crossing fixes stay on the right: " + str(post)
    ghosts = page.eval_on_selector_all("#host circle[data-copy]",
                                       "cs => cs.map(c => +c.getAttribute('cx'))")
    assert ghosts and min(ghosts) < w * 0.5, "the track re-enters at the left edge"
    # the full-life line exists for both world copies
    assert page.locator("path.tv-line-pre").count() >= 2


def test_missing_values_degrade_cleanly(page):
    page.evaluate("() => window.mountNull()")
    circles = _fix_circles(page)
    assert len(circles) == len(TRACK)
    assert all(c["fill"] for c in circles.values()), "null intensities still get a fill (unknown grey)"
    assert page.locator("#host text.tv-lab").count() == 0, "no peak label without a peak value"
    assert page._errs == []


def test_theme_tokens_resolve(page):
    page.evaluate("() => window.mount('dark')")
    dark = {c["fill"] for c in _fix_circles(page).values()}
    page.evaluate("() => window.mount('light')")
    light = {c["fill"] for c in _fix_circles(page).values()}
    assert dark and light and dark != light, "the violet ramp follows the theme"
    for f in dark | light:
        assert f and f != "none"


def test_teardown_clears(page):
    page.evaluate("() => window.mount()")
    assert page.locator("#host svg").count() == 1
    page.evaluate("() => window.TrackViz.teardown()")
    assert page.locator("#host svg").count() == 0
    assert page.locator("#host .tv-head").count() == 0


def test_screenshots(page):
    os.makedirs(SHOTS, exist_ok=True)
    page.evaluate("() => window.mount('light')")
    page.wait_for_timeout(600)                       # let the land fetch land
    page.screenshot(path=os.path.join(SHOTS, "fe-tc-track.png"))
    page.evaluate("() => window.mount('dark')")
    page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(SHOTS, "fe-tc-track-dark.png"))
    page.set_viewport_size({"width": 390, "height": 844})   # ResizeObserver must re-fit
    page.evaluate("() => window.mount('light')")
    page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(SHOTS, "fe-tc-track-mobile.png"))
    w = int(page.get_attribute("#host svg", "width") or 0)
    assert w <= 390, "the map re-fits to the narrow viewport instead of overflowing"
    assert page._errs == [], page._errs
