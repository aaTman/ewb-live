"""Flood lane SCORED in the real dashboard (flood lane, GraphCast only).

Modelled on tests/e2e/test_tracklane_fe.py (the same server/fixture approach —
own copies, nothing imported from it). The flood lane used to print "Not
scored"; the backend now publishes `flood` score rows for graphcast and
graphcast_gfs ONLY, with `skill_vs_ifs_pct` null everywhere (there is no
ifs_hres baseline: no other model carries precipitation in the store). This
test pins the FE contract that follows from that data shape:

  * the flood tab renders a real table (no "Not scored"), leads 1-5 d;
  * the lane note says GraphCast only / AI-estimated truth / no skill colour;
  * the header truth reads PARDIG on the flood tab;
  * all seven metric labels render, signed mm cells with a real minus (U+2212);
  * NO cell carries a skill colour and the banner makes no vs-ifs_hres claim;
  * no page/console errors, no horizontal page overflow, both viewports and
    both colour schemes.

Fixture = the repo's data/results/sample snapshot (so the other lanes keep real
content) + synthetic flood rows/events. Runs on any host with Playwright's
chromium; no pipeline, no live box.

Run:  .venv/bin/python -m pytest tests/e2e/test_floodlane_fe.py -m e2e
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

FLOOD_TAB = '#type-tabs button[data-tab="flood"]'
LEADS = (24.0, 48.0, 72.0, 96.0, 120.0)

# the lane's seven metrics, in the order the backend writes them
FLOOD_LABELS = ("Critical success index", "Hit rate (POD)", "False alarm ratio",
                "Fractions skill score (3x3)", "Fractions skill score (5x5)",
                "Area rain bias", "Peak rain error")

# one synthetic flood event, shaped like the published flood lane (peak is the
# trailing 72 h accumulation in mm; the daily peak and area ride details)
FLOOD_EVENT = {
    "id": "fl-fixture-ganges-0905", "event_type": "flood",
    "title": "Fixture flood (Bengal delta)",
    "start": "2026-09-05", "end": "2026-09-09", "bbox": [21.0, 25.5, 87.0, 91.5],
    "peak_value": 214.0,
    "details": {"peak_value_units": "mm", "peak_daily_mm": 96.0,
                "peak_area_km2": 74000.0, "duration_days": 5,
                "rain_source": "pardig",
                "detection": "PARDIG daily or 72 h rain past the ERA5 wet-day p95 "
                             "and >=20 mm/day", "bbox_units": "degrees"},
}


def _flood_score_rows():
    """Scorecard rows of the flood lane in the real pipeline schema
    (build_scorecard): graphcast + graphcast_gfs only, the seven metrics, leads
    24-120 h, and skill null everywhere (no ifs_hres baseline for this lane)."""
    rows = []
    for model, scale in (("graphcast", 1.0), ("graphcast_gfs", 0.8)):
        for lead in LEADS:
            growth = 1.0 + lead / 240.0          # worse at longer leads
            vals = {"CriticalSuccessIndex": 0.42 / growth * scale,
                    "ProbabilityOfDetection": 0.55 / growth * scale,
                    "FalseAlarmRatio": 0.28 * growth * (2.0 - scale),
                    "FractionsSkillScore": 0.61 / growth * scale,
                    "FractionsSkillScore5": 0.74 / growth * scale,
                    # the signed pair: negative = forecast too dry (U+2212 test)
                    "PrecipAreaBias": -(2.5 + lead / 24.0) * scale,
                    "PeakPrecipError": -(9.0 + lead / 12.0) * scale}
            for metric, val in vals.items():
                rows.append({"event_type": "flood", "metric": metric, "lead_time_hours": lead,
                             "model": model, "mean": round(val, 3), "n_events": 6,
                             "n_samples": 40, "ci95_lo": None, "ci95_hi": None,
                             "skill_vs_ifs_pct": None, "skill_n_common": None})
    return rows


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
    evs.append(FLOOD_EVENT)
    # the pipeline writes a plain LIST (pipeline.py: [_pub_event(e) ...])
    json.dump(evs, open(os.path.join(out, "events_selected.json"), "w"))
    json.dump({}, open(os.path.join(out, "pair_status.json"), "w"))
    json.dump({"weeks": []}, open(os.path.join(out, "weeks.json"), "w"))
    json.dump({}, open(os.path.join(out, "scorecard_by_region.json"), "w"))
    json.dump({"events": []}, open(os.path.join(out, "global_timeline.json"), "w"))
    sc = json.load(open(os.path.join(out, "scorecard.json")))
    sc["rows"] = sc["rows"] + _flood_score_rows()
    json.dump(sc, open(os.path.join(out, "scorecard.json"), "w"))
    # register the second GraphCast member the way the pipeline does, so the
    # tables roster it as a scored model instead of a no-data row
    sco = json.load(open(os.path.join(out, "scores.json")))
    sco["rows"] = sco["rows"] + [dict(r, model="graphcast_gfs")
                                 for r in sco["rows"] if r["model"] == "graphcast"]
    json.dump(sco, open(os.path.join(out, "scores.json"), "w"))
    meta = json.load(open(os.path.join(out, "meta.json")))
    meta["flood_scoring"] = {"truth": "pardig",
                             "truth_label": "vs AI-estimated precipitation (Google PARDIG)",
                             "scored_models": ["graphcast", "graphcast_gfs"],
                             "unsupported_models": ["ifs_hres", "pangu_weather", "aifs",
                                                    "aurora", "fourcastnet"],
                             "note": "GraphCast apcp only; ECMWF tp is not wired"}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"))
    return web


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base_flood(tmp_path_factory):
    web = _fixture(str(tmp_path_factory.mktemp("fl")))
    handler = lambda *a, **k: _Quiet(*a, directory=web, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# both viewports x both colour schemes (the owner's acceptance grid)
@pytest.fixture(params=[(1440, 900, "light"), (1440, 900, "dark"),
                        (390, 844, "light"), (390, 844, "dark")],
                ids=["1440-light", "1440-dark", "390-light", "390-dark"])
def flood_page(base_flood, request):
    w, h, scheme = request.param
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, color_scheme=scheme)
        errs: list[str] = []
        def _on_console(m):
            if m.type != "error":
                return
            # the snapshot ships without the raw lane: mergedmap.js probes
            # raw_exceedance/index.json, falls back with its own warn (app code
            # handles it), but Chromium still logs the 404 as a console error.
            # An expected optional-data probe, not a lane regression.
            if "Failed to load resource" in m.text and \
                    "/raw_exceedance/" in (m.location or {}).get("url", ""):
                return
            errs.append(f"console {m.text} @{(m.location or {}).get('url', '')}")
        pg.on("pageerror", lambda e: errs.append(f"pageerror {e}"))          # JS exceptions
        pg.on("requestfailed", lambda r: errs.append(f"requestfailed {r.url}"))
        pg.on("console", _on_console)
        pg._errs = errs                      # type: ignore[attr-defined]
        pg.goto(base_flood + "/index.html#view=scorecard&tab=flood")
        pg.wait_for_selector("#scorecard-sec .sc-block table", timeout=20000)
        yield pg
        b.close()


def test_flood_lane_renders_a_scored_table_not_a_not_scored_note(flood_page):
    pg = flood_page
    body = pg.inner_text("#scorecard-sec")
    assert "Not scored" not in body, body[:400]
    assert pg.locator("#scorecard-sec .sc-block table").count() >= 1
    # the lane keeps the track-lane lead discipline: 1-5 d (D8), hero metrics first
    leads = pg.eval_on_selector_all("#scorecard-sec .sc-block thead tr.lead th",
                                    "els => els.map(e => e.innerText)")
    assert [x for x in leads if x.endswith("d")] == ["1d", "2d", "3d", "4d", "5d",
                                                     "1d", "2d", "3d", "4d", "5d"], leads
    # only the two GraphCast members carry numbers; unscored models move to the footnote
    rows = pg.eval_on_selector_all("#scorecard-sec .sc-block tbody tr td.model-col",
                                   "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
    assert "GraphCast" in rows and "GraphCast GFS" in rows, rows
    assert pg._errs == [], pg._errs


def test_flood_lane_note_says_what_is_scored_and_against_what(flood_page):
    pg = flood_page
    fold = pg.locator("#scorecard-sec details.lane-notes-fold > summary")
    if fold.count():
        fold.first.click()
    note = pg.inner_text("#scorecard-sec .lane-note")
    assert "GraphCast only" in note, note
    assert "AI-estimated" in note and "PARDIG" in note, note
    assert "not observed rain" in note, note
    assert "the events are defined by the same product" in note, note
    # the metric definitions ride beside the table (same sentence as About)
    assert "CSI = hits/(hits+misses+false alarms)" in note, note
    assert "3x3 / 5x5 cell neighbourhoods" in note, note
    assert "negative = forecast too dry" in note, note
    # no baseline -> no skill colour, and the unsupported models are named (meta list)
    assert "No baseline model scores flood, so cells carry no skill colour." in note, note
    assert "Pangu-Weather" in note, note
    assert pg._errs == [], pg._errs


def _truth(pg):
    return pg.inner_text("#kv-line .kv:has(.k:text-is('truth')) .v").strip()


def test_flood_header_truth_is_pardig(flood_page):
    """The header must not say ERA5 on a lane scored against AI-estimated rain."""
    pg = flood_page
    assert _truth(pg) == "PARDIG (AI-estimated precip)", _truth(pg)
    tip = pg.get_attribute("#kv-line .kv:has(.k:text-is('truth')) .v", "title")
    assert tip and "PARDIG" in tip, tip
    pg.locator("#sc-views button", has_text="Overview").click()
    pg.wait_for_timeout(300)
    mix = _truth(pg)
    assert "ERA5" in mix and "PARDIG (flood)" in mix, mix       # the 'all' mix names both lanes
    pg.locator("#sc-views button", has_text="Heat wave").click()
    pg.wait_for_timeout(300)
    other = _truth(pg)
    assert "ERA5" in other and "PARDIG" not in other, other     # other lanes unchanged
    assert pg._errs == [], pg._errs


def test_all_seven_flood_metrics_are_labelled(flood_page):
    pg = flood_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    head = pg.inner_text("#scorecard-sec .sc-block thead").upper()
    for label in FLOOD_LABELS:
        assert label.upper() in head, (label, head)
    assert "[MM]" in head, head      # the signed pair carries its unit
    assert pg._errs == [], pg._errs


def test_signed_flood_cells_print_a_real_minus(flood_page):
    """fmtVal prints U+2212, not the ASCII hyphen, for the signed mm errors."""
    pg = flood_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(500)
    row = pg.locator('#scorecard-sec .sc-block tbody tr:has(td.model-col:text-is("GraphCast"))')
    cells = row.first.locator("td.cell").all_inner_texts()
    minus = [c for c in cells if "\u2212" in c]
    assert len(minus) == 12, cells          # PrecipAreaBias + PeakPrecipError: 5 leads + mean
    assert not any(c.startswith("-") for c in cells), cells
    assert pg._errs == [], pg._errs


def test_no_flood_cell_carries_a_skill_colour(flood_page):
    """skill_vs_ifs_pct is null and there is no baseline row: colouring is off,
    and the TC miss-grey rule must not leak onto this lane."""
    pg = flood_page
    painted = pg.eval_on_selector_all(
        "#scorecard-sec .sc-block td.cell",
        "els => els.filter(e => e.style && e.style.background && e.style.background !== 'none')"
        "             .map(e => e.textContent)")
    assert painted == [], painted
    assert pg.locator("#scorecard-sec .sc-block td.cell.miss-grey").count() == 0
    assert pg._errs == [], pg._errs


def test_verdict_banner_makes_no_baseline_claim(flood_page):
    """No ifs_hres rows exist for flood: the KPI card must not rank against it —
    it says GraphCast only / no baseline scores this lane."""
    pg = flood_page
    card = pg.locator("#kpi-strip .kpi-card.on").first
    strip = card.inner_text()
    assert "GraphCast" in strip and "only" in strip and "no baseline" in strip.lower(), strip
    assert "ifs" not in strip.lower(), strip
    assert pg._errs == [], pg._errs


def test_flood_lane_no_horizontal_page_overflow(flood_page):
    pg = flood_page
    over = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 0, over


def test_flood_lane_screenshot(flood_page, request):
    pg = flood_page
    pg.get_by_role("button", name="more metrics").click()
    pg.wait_for_timeout(400)
    os.makedirs(SHOTS, exist_ok=True)
    pg.screenshot(path=os.path.join(SHOTS, f"fe-floodlane-{request.node.name}.png"),
                  full_page=True)
    assert pg._errs == [], pg._errs
