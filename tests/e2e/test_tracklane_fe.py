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


def _tc_score_rows():
    """Scorecard rows of the TC lane in the real pipeline schema (build_scorecard)."""
    rows = []
    for model, scale, miss in (("ifs_hres", 1.0, 0.05), ("graphcast", 0.8, 0.10), ("aurora", 1.3, 0.62)):
        for lead in (24.0, 48.0, 72.0, 96.0, 120.0):
            rel = None if model == "ifs_hres" else round((1.0 - scale) * 100, 2)
            for metric, val in (("TrackPositionError", 60.0 * scale * lead / 24.0), ("TrackMiss", miss),
                                ("AlongTrackError", -4.0 * scale), ("CrossTrackError", 6.0 * scale),
                                ("CentralPressureError", 12.0 * scale)):
                signed = metric in ("AlongTrackError", "CrossTrackError", "CentralPressureError")
                rows.append({"event_type": "tropical_cyclone", "metric": metric, "lead_time_hours": lead,
                             "model": model, "mean": round(val, 3), "n_events": 6, "n_samples": 40,
                             "ci95_lo": None, "ci95_hi": None,
                             "skill_vs_ifs_pct": None if signed or model == "ifs_hres" else rel,
                             "skill_n_common": 6})
    return rows


def _fixture(root, tc_scores=False):
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
    if tc_scores:
        sc = json.load(open(os.path.join(out, "scorecard.json")))
        sc["rows"] = sc["rows"] + _tc_score_rows()
        json.dump(sc, open(os.path.join(out, "scorecard.json"), "w"))
        meta = json.load(open(os.path.join(out, "meta.json")))
        meta["track_verification"] = {"tropical_cyclone": {
            "min_wind_kt": 34.0, "n_events": 1, "n_inits_verified": 12, "n_inits_not_verified": 5, "events": {}}}
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"))
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
    assert "Tropical cyclone" in tabs or "Tropical cyclone" in page.inner_text("body")


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


def test_scorecard_says_not_scored_when_the_build_has_no_tc_score_rows(page):
    """TC is a scored lane now (WP1): a data release WITHOUT its score rows says so
    ("Not scored in this data release"), not "no EWB benchmark"."""
    page.wait_for_selector("#scorecard-sec .sc-block", timeout=20000)
    body = page.inner_text("#scorecard-sec")
    assert "Not scored" in body, body[:400]
    assert "no EWB benchmark for IBTrACS" not in body


def test_screenshot(page):
    page.evaluate(f"() => location.hash = '#event={TC_EVENT['id']}'")
    page.wait_for_selector("#detail-viz .tv-svg", timeout=20000)
    page.wait_for_timeout(600)
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "fe-tc-detail.png"), full_page=True)
    assert page._errs == [], page._errs


# ------------------------------------------------ honest zero (tc lane) --
# Owner rule 2026-11-17 (from the live "0 tropical cyclones" report): an
# empty tropical-cyclone lane must never print a bare 0. The three honest
# states come from meta.json (pipeline.py): key absent = release predates
# the lane; null = archive unreachable; dict = quote the archive's reach.
def _zero_fixture(root, mutate_meta):
    web = _fixture(root)
    out = os.path.join(web, "data", "results")
    p = os.path.join(out, "events_selected.json")
    evs = [e for e in json.load(open(p)) if e["event_type"] != "tropical_cyclone"]
    json.dump(evs, open(p, "w"))
    mp = os.path.join(out, "meta.json")
    meta = json.load(open(mp))
    mutate_meta(meta)
    json.dump(meta, open(mp, "w"))
    return web


# the chip labels are abbreviated in the no-scroll grid ("Trop. cyclone", 2026-11-17
# chips pass), so a text selector on the full lane name silently matches nothing.
# data-tab is what the click handler keys on (app.js mkTab) — stable, and the
# tooltip is not usable as a hook because the honest-zero note overwrites it.
TC_TAB = '#type-tabs button[data-tab="tropical_cyclone"]'


@pytest.fixture()
def zero_page(tmp_path_factory):
    """Factory: dashboard with zero published TCs and a chosen meta.json."""
    opened = []

    def make(mutate_meta):
        web = _zero_fixture(str(tmp_path_factory.mktemp("tz")), mutate_meta)
        handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        pw = sync_playwright().start()
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 950})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        pg.wait_for_selector("#event-table tbody tr", timeout=20000)
        pg.click(TC_TAB)
        pg.wait_for_selector("#event-table .lane-empty", timeout=20000)
        opened.append((pw, pg, srv))
        return pg

    yield make
    for pw, pg, srv in opened:
        pg.close()
        pw.stop()
        srv.shutdown()


def test_honest_zero_when_lane_absent_from_release(zero_page):
    pg = zero_page(lambda m: m.pop("ibtracs", None))
    assert "not published in this data release" in pg.inner_text("#event-table")
    tip = pg.get_attribute(TC_TAB, "title")
    assert tip and "not published" in tip, "the 0 badge carries the reason"


def test_honest_zero_reports_the_archive_reach(zero_page):
    def mut(m):
        m["ibtracs"] = {"latest_fix": "2026-11-12T12:00:00+00:00",
                        "lag_days": 5.2, "cache_age_h": 1.0,
                        "note": "IBTrACS best-track publication lags real time "
                                "by days to weeks"}
    pg = zero_page(mut)
    body = pg.inner_text("#event-table")
    assert "IBTrACS" in body and "2026-11-12" in body, body
    tip = pg.get_attribute(TC_TAB, "title")
    assert tip and "IBTrACS" in tip


def test_honest_zero_when_archive_unreachable(zero_page):
    pg = zero_page(lambda m: m.update(ibtracs=None))
    assert "unreachable" in pg.inner_text("#event-table")


# ---------------------------------------------------------- the scored lane ---
@pytest.fixture(scope="module")
def base_scored(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("tls")), tc_scores=True)
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(params=[(1440, 1000), (390, 844)], ids=["1440", "390"])
def scored_page(base_scored, request):
    w, h = request.param
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base_scored + "/index.html#tab=tropical_cyclone")
        pg.wait_for_selector("#scorecard-sec .sc-block table", timeout=20000)
        yield pg
        b.close()


def test_tc_lane_renders_scored_table_with_miss_rate_beside_the_error(scored_page):
    pg = scored_page
    head = pg.inner_text("#scorecard-sec .sc-block thead")
    assert "TRACK ERROR" in head.upper() and "MISS RATE" in head.upper()
    assert "NOT SCORED" not in pg.inner_text("#scorecard-sec").upper()
    leads = pg.eval_on_selector_all("#scorecard-sec .sc-block thead tr.lead th", "els => els.map(e => e.innerText)")
    assert [x for x in leads if x.endswith("d")] == ["1d", "2d", "3d", "4d", "5d", "1d", "2d", "3d", "4d", "5d"], leads
    # miss rate prints as a percentage, the error in km
    cells = pg.inner_text("#scorecard-sec .sc-block tbody tr:has-text('graphcast')")
    assert "10%" in cells and "60" not in cells.split("graphcast")[0]
    assert pg._errs == [], pg._errs


def test_tc_cell_above_50pct_misses_is_greyed_and_says_why(scored_page):
    pg = scored_page
    grey = pg.locator("#scorecard-sec .sc-block td.cell.miss-grey")
    assert grey.count() == 5, grey.count()                     # aurora: 5 error cells (miss rate 62 %)
    assert all("aurora" in t for t in pg.eval_on_selector_all(
        "#scorecard-sec .sc-block td.cell.miss-grey", "els => els.map(e => e.closest('tr').innerText)"))
    grey.first.hover()
    pg.wait_for_selector("#tooltip:not(.hidden)", timeout=5000)
    tip = pg.inner_text("#tooltip")
    assert "greyed" in tip and "tracked" in tip, tip


def test_tc_note_states_the_rules_and_the_not_verified_count(scored_page):
    note = scored_page.inner_text("#scorecard-sec .lane-note")
    assert "IBTrACS" in note and "ahead" in note and "right" in note
    assert "5 earlier ones not verified" in note and "12 00/12 UTC inits verified" in note


def test_tc_more_metrics_shows_signed_components(scored_page):
    pg = scored_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    head = pg.inner_text("#scorecard-sec .sc-block thead").upper()
    for label in ("ALONG-TRACK", "CROSS-TRACK", "CENTRAL PRESSURE"):
        assert label in head, head
    assert pg._errs == [], pg._errs


def test_tc_no_horizontal_page_scroll(scored_page):
    over = scored_page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, over
