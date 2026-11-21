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


# aurora tracks the storms it keeps very well (+50 % at 24/48 h, +90 % where it keeps almost none) but loses most
# of them from 72 h on: its 72-120 h cells are greyed and must not feed the banner; its mean miss rate is 59 %.
AURORA_LOSES_STORMS = {24.0: (0.20, 50.0), 48.0: (0.30, 50.0), 72.0: (0.60, 90.0), 96.0: (0.90, 90.0), 120.0: (0.95, 90.0)}


def _tc_landfall_rows():
    """Landfall score rows (EWB's landfall classes, storms that reached land only) for the
    same three models at leads 24/48/72; n = 2 storms / 6 (storm, init) samples per cell.
    aurora lands the easy storms well but its 72 h landfall miss rate is 60 % (> 50 %):
    that greys its three landfall VALUE cells at that lead, and landfall never feeds the
    banner or the model order — even though aurora's landfall values alone would lead it."""
    rows = []
    # model: {lead: (displacement km, time_me h, intensity_mae hPa, miss rate)}
    spec = {
        "ifs_hres":  {24.0: (90.0, 6.0, 8.0, 0.20), 48.0: (90.0, 6.0, 8.0, 0.20),
                      72.0: (90.0, 6.0, 8.0, 0.30)},
        "graphcast": {24.0: (108.0, 4.0, 6.4, 0.25), 48.0: (108.0, 4.0, 6.4, 0.25),
                      72.0: (108.0, 4.0, 6.4, 0.25)},
        "aurora":    {24.0: (45.0, -2.0, 4.0, 0.35), 48.0: (45.0, -2.0, 4.0, 0.35),
                      72.0: (117.0, 13.0, 16.0, 0.60)},
    }
    for model, per_lead in spec.items():
        for lead, (disp, time_me, inten, miss) in per_lead.items():
            for metric, val, nsamp in (("landfall_displacement", disp, 4), ("landfall_time_me", time_me, 4),
                                       ("landfall_intensity_mae", inten, 4), ("landfall_miss", miss, 6)):
                rows.append({"event_type": "tropical_cyclone", "metric": metric, "lead_time_hours": lead,
                             "model": model, "mean": val, "n_events": 2, "n_samples": nsamp,
                             "ci95_lo": None, "ci95_hi": None,
                             # signed landfall_time_me: no pipeline skill; the dashboard
                             # recomputes it sign-aware (dir 'zero')
                             "skill_vs_ifs_pct": None, "skill_n_common": 2})
    return rows


def _tc_score_rows(aurora=None):
    """Scorecard rows of the TC lane in the real pipeline schema (build_scorecard)."""
    rows = []
    for model, scale, miss in (("ifs_hres", 1.0, 0.05), ("graphcast", 0.8, 0.10), ("aurora", 1.3, 0.62)):
        for lead in (24.0, 48.0, 72.0, 96.0, 120.0):
            rel = None if model == "ifs_hres" else round((1.0 - scale) * 100, 2)
            if aurora and model == "aurora":
                miss, rel = aurora[lead]
                scale = 1.0 - rel / 100.0
            for metric, val in (("TrackPositionError", 60.0 * scale * lead / 24.0), ("TrackMiss", miss),
                                ("AlongTrackError", -4.0 * scale), ("CrossTrackError", 6.0 * scale),
                                ("CentralPressureError", 12.0 * scale)):
                signed = metric in ("AlongTrackError", "CrossTrackError", "CentralPressureError")
                rows.append({"event_type": "tropical_cyclone", "metric": metric, "lead_time_hours": lead,
                             "model": model, "mean": round(val, 3), "n_events": 6, "n_samples": 40,
                             "ci95_lo": None, "ci95_hi": None,
                             "skill_vs_ifs_pct": None if signed or model == "ifs_hres" else rel,
                             "skill_n_common": 6})
    return rows + _tc_landfall_rows()


def _fixture(root, tc_scores=False, real_shape=False, aurora=None, mlc_scores=False):
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
        if real_shape:   # 9 models x 5 metrics x 24-120 h cut from the real 44-storm backfill (miss rates intact)
            sc["rows"] = sc["rows"] + json.load(open(os.path.join(ROOT, "tests", "fixtures", "tc_scorecard_real_shape.json")))
        else:
            sc["rows"] = sc["rows"] + _tc_score_rows(aurora)
        json.dump(sc, open(os.path.join(out, "scorecard.json"), "w"))
        meta = json.load(open(os.path.join(out, "meta.json")))
        meta["track_verification"] = {"tropical_cyclone": {
            "min_wind_kt": 34.0, "n_events": 1, "n_inits_verified": 12, "n_inits_not_verified": 5, "events": {}}}
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"))
    if mlc_scores:
        evp = os.path.join(out, "events_selected.json")
        evs2 = json.load(open(evp))
        evs2.append(MLC_EVENT)
        json.dump(evs2, open(evp, "w"))
        scp = os.path.join(out, "scorecard.json")
        sc = json.load(open(scp))
        sc["rows"] = sc["rows"] + _mlc_score_rows()
        json.dump(sc, open(scp, "w"))
        mp = os.path.join(out, "meta.json")
        meta = json.load(open(mp))
        tv = dict(meta.get("track_verification") or {})
        tv["midlat_cyclone"] = {"n_events": 1, "n_inits_verified": 18, "events": {}}
        meta["track_verification"] = tv
        json.dump(meta, open(mp, "w"))
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
    assert grey.count() == 6, grey.count()                     # aurora: 5 error cells (miss rate 62 %) + the all-greyed Mean
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


def test_tc_verdict_ranks_on_track_error_only(scored_page):
    """The signed components and the miss rate must not feed the verdict strip: graphcast's
    track error is 20 % better than the baseline in the fixture, and that is the number quoted."""
    pg = scored_page
    pg.get_by_role("button", name="more metrics").click()       # signed metrics now in the table
    pg.wait_for_timeout(400)
    strip = pg.inner_text("#verdict-strip")
    assert "graphcast" in strip and "+20.0%" in strip and "won 5/5 cells" in strip, strip
    # the banner says what it ranks and shows the miss rates beside it (owner review: every model
    # missed more storms than the baseline while the banner only quoted the track error)
    assert "ranks track error only" in strip and "misses 10% vs 5%" in strip, strip


def _truth(pg):
    return pg.inner_text("#kv-line .kv:has(.k:text-is('truth')) .v").strip()


def test_header_truth_is_lane_aware(scored_page):
    """The header said TRUTH ERA5 on the TC tab although TC is scored vs IBTrACS (owner review)."""
    pg = scored_page
    assert _truth(pg) == "IBTrACS"                                     # the page opens on #tab=tropical_cyclone
    pg.locator("#type-tabs button", has_text="All").click()
    pg.wait_for_timeout(300)
    mix = _truth(pg)
    assert "ERA5" in mix and "IBTrACS" in mix, mix
    pg.locator("#type-tabs [data-tab=atmospheric_river]").click()
    pg.wait_for_timeout(300)
    other = _truth(pg)
    assert "ERA5" in other and "IBTrACS" not in other, other           # other lanes unchanged
    pg.locator("#type-tabs [data-tab=tropical_cyclone]").click()
    pg.wait_for_timeout(300)
    assert _truth(pg) == "IBTrACS"
    assert "IBTrACS best track" in pg.get_attribute("#kv-line .kv:has(.k:text-is('truth')) .v", "title")


# ------------------------------------------- real-data-shaped scorecard (PR #16 follow-up)
@pytest.fixture(scope="module")
def base_real(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("tlr")), tc_scores=True, real_shape=True)
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(params=[(1440, 1000, "light"), (390, 844, "light"), (390, 844, "dark")],
                ids=["1440", "390-light", "390-dark"])
def real_page(base_real, request):
    w, h, scheme = request.param
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, color_scheme=scheme)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base_real + "/index.html#tab=tropical_cyclone")
        pg.wait_for_selector("#scorecard-sec .sc-block table", timeout=20000)
        yield pg
        b.close()


def test_real_shape_grey_rule_hits_exactly_the_over_50pct_cells(real_page):
    pg = real_page
    grey = pg.eval_on_selector_all("#scorecard-sec .sc-block td.cell.miss-grey",
                                   "els => els.map(e => e.closest('tr').innerText.split('\\t')[0].trim())")
    assert grey and set(grey) == {"fourcastnet_v2_gfs"} and len(grey) == 2, grey   # 96 h and 120 h track error
    # the miss-rate cells themselves are never greyed (they ARE the evidence)
    assert pg.locator("#scorecard-sec .sc-block td.cell.miss-grey").count() == 2
    assert pg._errs == [], pg._errs


def test_real_shape_cross_track_group_and_skill_expander(real_page):
    pg = real_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(600)
    heads = pg.eval_on_selector_all("#scorecard-sec .sc-block thead tr.grp th", "els => els.map(e => e.innerText)")
    cross = [h for h in heads if h.upper().startswith("CROSS-TRACK")]
    assert cross, heads
    # every model row has 5 numeric cross-track cells + a mean (signed values keep their sign)
    row = pg.locator("#scorecard-sec .sc-block tbody tr", has_text="graphcast").first
    cells = row.locator("td.cell").all_inner_texts()
    assert len(cells) == 6 * 5, cells                    # 5 metrics x (5 leads + mean)
    assert all(c.strip() != "" for c in cells[-12:-6]) and any("\u2212" in c for c in cells), cells
    # "Skill vs lead" opens and draws charts for the TC lane
    pg.locator("#skill-details summary").click()
    pg.wait_for_selector("#skill-details .js-plotly-plot", timeout=15000)
    assert pg.locator("#skill-details .js-plotly-plot").count() >= 1
    assert pg._errs == [], pg._errs


def test_real_shape_no_overflow_and_dark_theme_tokens(real_page):
    pg = real_page
    assert pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    grey = pg.locator("#scorecard-sec .sc-block td.cell.miss-grey").first
    bg = grey.evaluate("e => getComputedStyle(e).backgroundImage")
    assert "gradient" in bg                                   # the hatch survives both themes


# ---------------------------- greyed cells are not ranking inputs (PR #16 review)
@pytest.fixture(scope="module")
def base_greyed(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("tlg")), tc_scores=True, aurora=AURORA_LOSES_STORMS)
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def greyed_page(base_greyed):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base_greyed + "/index.html#tab=tropical_cyclone")
        pg.wait_for_selector("#scorecard-sec .sc-block table", timeout=20000)
        yield pg
        b.close()


def test_greyed_cells_do_not_feed_the_banner_or_the_mean_colour(greyed_page):
    pg = greyed_page
    strip = pg.inner_text("#verdict-strip")
    # aurora wins its 24/48 h cells (+50 %) and loses most storms later: the +90 % of its greyed 72-120 h
    # cells is NOT averaged in, and the banner says the leader's mean miss rate is above 50 %
    assert "aurora best" in strip and "+50.0%" in strip and "won 2/" in strip, strip
    assert "(>50% misses)" in strip and "misses 59%" in strip, strip
    grey = pg.locator("#scorecard-sec .sc-block tbody tr", has_text="aurora").first.locator("td.cell.miss-grey")
    assert grey.count() == 3, grey.count()                       # 72, 96, 120 h; the mean is not all-greyed
    cell = grey.first
    assert cell.get_attribute("title") == "miss rate > 50%"
    assert "miss rate > 50%" in cell.get_attribute("aria-label")
    mean = pg.locator("#scorecard-sec .sc-block tbody tr", has_text="aurora").first.locator("td.cell.mean").first
    assert "miss-grey" not in mean.get_attribute("class")
    assert pg._errs == [], pg._errs


def test_scored_fixture_all_greyed_mean(scored_page):
    pg = scored_page
    row = pg.locator("#scorecard-sec .sc-block tbody tr", has_text="aurora").first
    assert row.locator("td.cell.miss-grey").count() == 6      # 5 leads + the Mean
    assert "at every lead" in row.locator("td.cell.mean").first.get_attribute("title")


def test_track_scoring_listing_is_not_a_live_region(scored_page):
    assert scored_page.locator("#track-scoring-code").get_attribute("aria-live") is None


# ------------------------------------------------- the landfall block (PR #17)
# EWB's landfall classes score the storms that reached land: Landfall position (km),
# timing (h, signed), intensity (hPa) and the landfall miss rate (0/1 per init, read
# as a percent). In the fixture aurora's landfall values are the table's best at 24/48 h
# but its 72 h landfall miss rate is 60 % — its value cells there are greyed, and no
# landfall metric feeds the verdict banner or the model order.
def test_landfall_block_renders_and_greys_on_its_own_miss_rate(scored_page):
    pg = scored_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    head = pg.inner_text("#scorecard-sec .sc-block thead").upper()
    for label in ("LANDFALL POSITION", "LANDFALL TIMING", "LANDFALL INTENSITY", "LANDFALL MISS RATE"):
        assert label in head, head
    note = pg.inner_text("#scorecard-sec .lane-note")
    assert "landfall classes" in note and "observed landfall" in note and "no paired landfall" in note, note
    # the aurora row is 9 metric groups x (5 leads + Mean). Its TrackMiss (62 %) greys the
    # four track-error groups (cells 0-5, 12-29); landfall_miss 60 % @72 h adds exactly the
    # three landfall VALUE cells (32, 38, 44). The two miss-rate groups are never greyed.
    idxs = pg.eval_on_selector_all(
        "#scorecard-sec .sc-block tbody tr",
        """rows => {
             const r = rows.find((x) => x.innerText.includes('aurora'));
             return [...r.querySelectorAll('td.cell')]
               .map((c, i) => (c.classList.contains('miss-grey') ? i : -1)).filter((i) => i >= 0);
           }""")
    assert idxs == [*range(0, 6), *range(12, 30), 32, 38, 44], idxs
    cells = pg.locator("#scorecard-sec .sc-block tbody tr", has_text="aurora").first.locator("td.cell")
    miss72 = cells.nth(8 * 6 + 2)                              # landfall miss rate at 72 h
    assert "60%" in miss72.inner_text(), miss72.inner_text()   # prints as a percent, like TrackMiss
    assert "miss-grey" not in (miss72.get_attribute("class") or "")
    cells.nth(5 * 6 + 2).hover()                               # greyed Landfall position @72 h
    pg.wait_for_selector("#tooltip:not(.hidden)", timeout=5000)
    tip = pg.inner_text("#tooltip")
    assert "greyed" in tip and "landfall-paired" in tip, tip
    assert pg._errs == [], pg._errs


def test_landfall_metrics_stay_out_of_the_verdict_and_the_model_order(scored_page):
    """The verdict still counts only the five track-error cells, and no landfall cell moves
    a row: aurora's track error is fully miss-greyed here (unranked band), yet its 24/48 h
    landfall values would average ~+56 % — if landfall fed the ranking it would jump to 2."""
    pg = scored_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(400)
    strip = pg.inner_text("#verdict-strip")
    assert "graphcast" in strip and "+20.0%" in strip and "won 5/5 cells" in strip, strip
    order = pg.eval_on_selector_all("#scorecard-sec .sc-block tbody tr .model-col",
                                    "els => els.map(e => e.innerText.trim())")
    assert order[0] == "ifs_hres" and order[1] == "graphcast", order
    assert order.index("aurora") > order.index("graphcast"), order
    assert pg._errs == [], pg._errs


# ------------------------------------------- MLC lane, scored (mirrors TC, PR #16 pattern)
# Mid-latitude cyclones score against the ERA5 MSLP track each low was detected on — the same
# 0.25° grid the models run on. Metrics: the five TC track metrics plus PeakDepthError (signed;
# needs no tracking, so it has no miss rate and is never greyed) and the two 0-1 bomb rates.
# graphcast's TrackMiss at 96 h is 0.6 to exercise the >50%-miss grey rule; its 96 h
# PeakDepthError must NOT be greyed with it.

MLC_METRICS = ("TrackPositionError", "TrackMiss", "AlongTrackError", "CrossTrackError",
               "CentralPressureError", "PeakDepthError", "BombDetected", "BombFalseAlarm")

MLC_EVENT = {
    "id": "mlcfake0001",
    "event_type": "midlat_cyclone",
    "title": "Midlatitude Cyclone Fixture",
    "start": "2026-10-09T12:00:00",
    "end": "2026-10-11T12:00:00",
    "bbox": [42.0, 55.0, -40.0, -22.0],
    "peak_value": 976.0,
    "details": {"min_pressure_hpa": 976.0, "displacement_km": 2400.0,
                "bomb": True, "bomb_ratio": 1.6, "peak_value_units": "hPa",
                "source": "ERA5 MSLP closed-low tracker"},
}


def _mlc_score_rows():
    """Scorecard rows of the MLC lane in the same pipeline schema as _tc_score_rows."""
    bomb = {"ifs_hres": (0.62, 0.06), "graphcast": (0.70, 0.04)}
    miss_by_lead = {"ifs_hres": {24.0: 0.04, 48.0: 0.06, 72.0: 0.08, 96.0: 0.10},
                    "graphcast": {24.0: 0.10, 48.0: 0.16, 72.0: 0.30, 96.0: 0.60}}
    rows = []
    for model, scale, in (("ifs_hres", 1.0), ("graphcast", 0.8)):
        rel = None if model == "ifs_hres" else round((1.0 - scale) * 100, 2)
        for lead in (24.0, 48.0, 72.0, 96.0):
            vals = {"TrackPositionError": 90.0 * scale * lead / 24.0,
                    "TrackMiss": miss_by_lead[model][lead],
                    "AlongTrackError": -6.0 * scale, "CrossTrackError": 9.0 * scale,
                    "CentralPressureError": 7.0 * scale, "PeakDepthError": 5.0 * scale,
                    "BombDetected": bomb[model][0], "BombFalseAlarm": bomb[model][1]}
            for metric, val in vals.items():
                signed = metric in ("AlongTrackError", "CrossTrackError",
                                    "CentralPressureError", "PeakDepthError")
                rows.append({"event_type": "midlat_cyclone", "metric": metric, "lead_time_hours": lead,
                             "model": model, "mean": round(val, 3), "n_events": 8, "n_samples": 60,
                             "ci95_lo": None, "ci95_hi": None,
                             "skill_vs_ifs_pct": None if signed or model == "ifs_hres" else rel,
                             "skill_n_common": 8})
    return rows


@pytest.fixture(scope="module")
def base_mlc(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("tlm")), mlc_scores=True)
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(params=[(1440, 1000), (390, 844)], ids=["1440", "390"])
def mlc_page(base_mlc, request):
    w, h = request.param
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base_mlc + "/index.html#tab=midlat_cyclone")
        pg.wait_for_selector("#scorecard-sec .sc-block table", timeout=20000)
        yield pg
        b.close()


# JS: locate one data column of the single scorecard block by its metric-group header
# (prefix match) and lead header (exact, e.g. '4d'), and report the cell in the row
# containing a given model. Column index is the offset inside the row's td.cell list.
_MLC_CELL_JS = """
([grpStart, leadText, model]) => {
  const block = document.querySelector('#scorecard-sec .sc-block');
  const grpThs = [...block.querySelectorAll('thead tr.grp th')];
  const leadThs = [...block.querySelectorAll('thead tr.lead th')];
  let acc = 0, idx = -1;
  for (let gi = 1; gi < grpThs.length; gi++) {
    const g = grpThs[gi], span = g.colSpan || 1;
    if (idx < 0 && g.textContent.trim().toUpperCase().startsWith(grpStart))
      for (let j = 0; j < span; j++)
        if (leadThs[acc + j + 1] && leadThs[acc + j + 1].textContent.trim() === leadText) idx = acc + j;
    acc += span;
  }
  if (idx < 0) return { error: 'column not found: ' + grpStart + ' ' + leadText,
                        heads: grpThs.map(t => t.textContent.trim()),
                        leads: leadThs.map(t => t.textContent.trim()) };
  const row = [...block.querySelectorAll('tbody tr')].find(r => r.innerText.includes(model));
  const td = row ? row.querySelectorAll('td')[idx + 1] : null;   // +1: the model-name column
  return { idx, text: td ? td.textContent : null, cls: td ? td.className : null,
           grey: td ? td.classList.contains('miss-grey') : null };
}
"""

# JS: every cell text of the first metric group whose header starts with grpStart.
_MLC_COL_JS = """
(grpStart) => {
  const block = document.querySelector('#scorecard-sec .sc-block');
  const grpThs = [...block.querySelectorAll('thead tr.grp th')];
  let acc = 0, idx = -1;
  for (let gi = 1; gi < grpThs.length; gi++) {
    const g = grpThs[gi], span = g.colSpan || 1;
    if (g.textContent.trim().toUpperCase().startsWith(grpStart)) { idx = acc; break; }
    acc += span;
  }
  if (idx < 0) return null;
  return [...block.querySelectorAll('tbody tr')]
    .map(r => { const td = r.querySelectorAll('td')[idx + 1]; return td ? td.textContent : null; });
}
"""


def test_mlc_lane_renders_a_scored_table(mlc_page):
    """The MLC tab shows a scored table — no 'Not scored' copy anywhere in the lane."""
    pg = mlc_page
    assert pg.locator("#scorecard-sec .sc-block table").count() == 1
    body = pg.inner_text("#scorecard-sec")
    assert "Not scored" not in body, body[:400]
    head = pg.inner_text("#scorecard-sec .sc-block thead").upper()
    assert "TRACK ERROR" in head and "MISS RATE" in head
    leads = pg.eval_on_selector_all("#scorecard-sec .sc-block thead tr.lead th",
                                    "els => els.map(e => e.innerText)")
    assert [x for x in leads if x.endswith("d")] == ["1d", "2d", "3d", "4d",
                                                     "1d", "2d", "3d", "4d"], leads
    assert pg._errs == [], pg._errs


def test_mlc_grey_rule_greys_track_error_not_peak_depth(mlc_page):
    """graphcast misses 60 % of its 96 h samples: its 96 h track error is greyed, its
    96 h Peak depth is NOT — that metric does not depend on the tracker keeping the low."""
    pg = mlc_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    tr96 = pg.evaluate(_MLC_CELL_JS, ["TRACK ERROR", "4d", "graphcast"])
    assert tr96.get("grey") is True, tr96
    pd96 = pg.evaluate(_MLC_CELL_JS, ["PEAK DEPTH", "4d", "graphcast"])
    assert pd96.get("grey") is False, pd96
    # greyed cells in the whole block: exactly the four tracker-dependent errors at 96 h
    # (track/along/cross/central); the miss rate itself and the three tracker-free
    # metrics (peak depth, both bomb rates) are never greyed
    assert pg.locator("#scorecard-sec .sc-block td.cell.miss-grey").count() == 4, tr96
    assert pg._errs == [], pg._errs


def test_mlc_header_truth_is_the_era5_track(mlc_page):
    assert _truth(mlc_page) == "ERA5 track"


def test_mlc_note_states_the_truth_and_the_new_metrics(mlc_page):
    note = mlc_page.inner_text("#scorecard-sec .lane-note")
    assert "ERA5 MSLP track" in note and "Peak depth" in note, note
    assert "18 00/12 UTC inits verified" in note, note


def test_mlc_no_horizontal_page_scroll(mlc_page):
    over = mlc_page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, over


def test_mlc_bomb_detected_cells_print_as_percent(mlc_page):
    pg = mlc_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    texts = pg.evaluate(_MLC_COL_JS, "BOMBS DETECTED")
    assert texts, texts
    # the fixture scores two models; the roster's other models have no rows and print an em dash
    texts = [t for t in texts if t != "\u2014"]
    assert len(texts) == 2, texts
    assert all(t.endswith("%") and t[:-1].strip().isdigit() for t in texts), texts
    assert pg._errs == [], pg._errs
