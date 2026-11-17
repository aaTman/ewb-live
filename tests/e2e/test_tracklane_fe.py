"""Track lane in the real dashboard (tc lane, 2026-11-16).

The renderer itself is covered by test_trackviz_fe.py; this is the SHARED-file
wiring — app.js must route an event that carries details.track to TrackViz
instead of fetching a viz.json asset that will never exist, the lane must get
its tab/colour/criteria, and the scorecard must say "Not scored" for a
published-but-unscored lane rather than render a table of dashes.

Fixture = the repo's data/results/sample snapshot (so the scorecard, list and
map all have real content) plus one synthetic tropical_cyclone event. Runs on
any host with Playwright's chromium; no pipeline, no live box.

Run:  .venv/bin/python -m pytest tests/e2e/test_tracklane_fe.py -m e2e
"""
import json
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DASH = os.path.join(ROOT, "dashboard")
SNAP = os.path.join(ROOT, "data", "results", "sample")
SHOTS = os.path.join(ROOT, "notes", "screenshots")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from test_trackviz_fe import EVENT as TC_EVENT  # the same synthetic storm  # noqa: E402


def _fixture(root):
    web = os.path.join(root, "webroot")
    os.makedirs(web)
    for f in os.listdir(DASH):
        s = os.path.join(DASH, f)
        d = os.path.join(web, f)
        shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)
    out = os.path.join(web, "data", "results")
    os.makedirs(out)
    for f in ("meta.json", "events.json", "scores.json", "scorecard.json"):
        shutil.copy2(os.path.join(SNAP, f), os.path.join(out, f))
    evs = json.load(open(os.path.join(out, "events.json")))
    evs.append(TC_EVENT)
    # the pipeline writes a plain LIST (pipeline.py: [_pub_event(e) ...])
    json.dump(evs, open(os.path.join(out, "events_selected.json"), "w"))
    # documented-exclusion bookkeeping the pipeline writes for a track lane
    json.dump({f"{TC_EVENT['id']}|ifs_hres": {"status": "unsupported", "rows": 0,
             "reason": "no EWB benchmark for best-track events"}},
              open(os.path.join(out, "pair_status.json"), "w"))
    json.dump({"weeks": []}, open(os.path.join(out, "weeks.json"), "w"))
    json.dump({}, open(os.path.join(out, "scorecard_by_region.json"), "w"))
    json.dump({"events": []}, open(os.path.join(out, "global_timeline.json"), "w"))
    return web


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("tl")))
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def page(base):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 950})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))          # JS exceptions
        pg.on("requestfailed", lambda r: errs.append(f"requestfailed {r.url}"))
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base + "/index.html")
        pg.wait_for_selector("#event-table tbody tr", timeout=20000)
        yield pg
        b.close()


def test_tc_row_is_named_and_coloured(page):
    row = page.locator("#event-table tbody tr", has_text="Tropical Cyclone")
    assert row.count() >= 1, "the storm is in the catalog"
    txt = row.first.inner_text()
    assert "Fixture" in txt, f"the row is named after the storm, got: {txt}"
    assert "kt" in txt, f"the Peak column carries the lane's own unit: {txt}"


def test_tab_and_criteria_exist(page):
    tabs = page.inner_text("#lane-tabs") if page.locator("#lane-tabs").count() else ""
    assert "Tropical cyclones" in tabs or "Tropical cyclone" in page.inner_text("body")


def test_detail_uses_the_track_renderer(page):
    page.evaluate(f"() => location.hash = '#event={TC_EVENT['id']}'")
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=20000)
    # the panel is TrackViz's, not the EventViz "no asset" fallback
    assert page.locator("#detail-viz .tv-svg").count() == 1
    assert page.locator("#detail-viz .tv-legend .tv-sw").count() == 5
    assert "not available" not in page.inner_text("#detail-viz").lower()
    assert "Best track" in page.inner_text("#detail-viz")
    story = page.inner_text("#detail-story")
    assert "Fixture" in story and "kt" in story, story
    assert page._errs == [], page._errs


def test_close_detail_teardown(page):
    page.evaluate(f"() => location.hash = '#event={TC_EVENT['id']}'")
    page.wait_for_selector("#detail-viz .tv-svg", timeout=20000)
    page.evaluate("() => { location.hash = '#'; }")
    page.wait_for_timeout(300)
    assert page.locator("#detail-viz .tv-svg").count() == 0
    assert page._errs == [], page._errs


def test_scorecard_says_not_scored_for_the_lane(page):
    page.wait_for_selector("#scorecard-sec .sc-block", timeout=20000)
    body = page.inner_text("#scorecard-sec")
    assert "Not scored" in body, body[:400]
    assert "IBTrACS" in body


def test_screenshot(page):
    page.evaluate(f"() => location.hash = '#event={TC_EVENT['id']}'")
    page.wait_for_selector("#detail-viz .tv-svg", timeout=20000)
    page.wait_for_timeout(600)
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "fe-tc-detail.png"), full_page=True)
    assert page._errs == [], page._errs
