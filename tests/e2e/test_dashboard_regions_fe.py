"""E2E (Playwright) for the Region dropdown (briefs/regional_breakdowns.md).

Ported to the landed redesign (map + list explore area, Leaflet catalog map
retired): the Region control sits in the explore filter row beside the type
tabs and — like the type filter — narrows the map, the event list and the
scorecard together, which echoes it in #sc-scope.

Self-contained deterministic fixture (tiny synthetic results dir; the merged
map's raw/timeline assets reused from test_dashboard_fe so boot is console-
clean) covering: dropdown population, per-region scorecard re-render with
honest counts, the region-aware empty-lane lines, the merged-map extent/veil
view, #region= URL persistence (reload, Back/Forward, deep link, unknown-key
drop), and the graceful hide on an archived week that predates the region file.

Console filter: browser-level 'Failed to load resource' notes (the OPTIONAL
scorecard_by_region.json 404 on archived weeks) are ignored; JS errors are not.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import test_dashboard_fe as fe  # noqa: E402  (raw/timeline fixture builders + server)

ROOT = Path(fe.ROOT)
DASH = ROOT / "dashboard"

# ----------------------------------------------------------------- fixture --
# bbox centres hit exactly the intended regions (dashboard/regions.json boxes):
EV = [
    dict(id="h_eu", event_type="heat_wave", title="EU heat", status="ended",
         start="2026-01-02", end="2026-01-05", bbox=[40.0, 50.0, -5.0, 10.0],
         details={"region": "e2e"}),                     # europe + NH
    dict(id="h_na", event_type="heat_wave", title="NA heat", status="ended",
         start="2026-01-03", end="2026-01-06", bbox=[35.0, 45.0, -110.0, -90.0],
         details={"region": "e2e"}),                     # north_america + NH
    dict(id="f_sh", event_type="freeze", title="AU cold", status="ended",
         start="2026-01-04", end="2026-01-07", bbox=[-40.0, -30.0, 140.0, 155.0],
         details={"region": "e2e"}),                     # southern_hemisphere
    dict(id="f_eu", event_type="freeze", title="EU cold", status="ended",
         start="2026-01-06", end="2026-01-09", bbox=[50.0, 60.0, 5.0, 20.0],
         details={"region": "e2e"}),                     # europe + NH
    dict(id="a_tp", event_type="atmospheric_river", title="TP arc", status="ended",
         start="2026-01-08", end="2026-01-10", bbox=[10.0, 15.0, -30.0, -20.0],
         details={"region": "e2e"}),                     # tropics ONLY
]
SCORES = [dict(event_id=e["id"], event_type=e["event_type"], model=m,
               lead_time_hours=l, metric="RMSE", value=i + l / 24.0)
          for i, e in enumerate(EV) for m in ("ifs_hres", "m2") for l in (24, 72)]


def _sc(types_n):   # {etype: n_events} -> scorecard rows (values not pinned; counts are)
    return [dict(event_type=et, model=m, lead_time_hours=l, metric="RMSE",
                 mean_value=i + l / 24.0, n_events=n)
            for i, (et, n) in enumerate(types_n.items())
            for m in ("ifs_hres", "m2") for l in (24, 72)]


BY_TYPE = {  # scored-event split per region (footprint rule, ewb_live/regions.py)
    "global": {"heat_wave": 2, "freeze": 2, "atmospheric_river": 1},
    "tropics": {"heat_wave": 0, "freeze": 0, "atmospheric_river": 1},
    "northern_hemisphere": {"heat_wave": 2, "freeze": 2, "atmospheric_river": 0},
    "southern_hemisphere": {"heat_wave": 0, "freeze": 1, "atmospheric_river": 0},
    "north_america": {"heat_wave": 1, "freeze": 0, "atmospheric_river": 0},
    "europe": {"heat_wave": 1, "freeze": 1, "atmospheric_river": 0},
}
# The Global scorecard.json rows deliberately carry n_events >= 5 while the
# catalog holds 5 events: the small-sample cue (owner 2026-11-10) must be
# testable in BOTH states, and every region slice here is under 5 events.
# (Lane headers count the catalog, cells count the rows — see app.js.)
GLOBAL_ROW_N = {"heat_wave": 6, "freeze": 5, "atmospheric_river": 5}
# Authoritative server-side assignment (the footprint rule's output): the UI
# filters from this map, not from its own geometry.
EVENT_REGIONS = {
    "h_eu": ["global", "northern_hemisphere", "europe"],
    "h_na": ["global", "northern_hemisphere", "north_america"],
    "f_sh": ["global", "southern_hemisphere"],
    "f_eu": ["global", "northern_hemisphere", "europe"],
    "a_tp": ["global", "tropics"],
}
REGIONS_META = json.loads((DASH / "regions.json").read_text())["regions"]
REGION_KEYS = [r["key"] for r in REGIONS_META]
SCORECARD_BY_REGION = {
    "baseline": "ifs_hres",
    "assignment": {"rule": "footprint-overlap", "threshold": 0.25,
                   "fallback": "bbox-centroid", "min_sample": 5,
                   "events_with_footprint": len(EVENT_REGIONS),
                   "events_total": len(EVENT_REGIONS)},
    "regions": [{"key": k, "label": r["label"], "n_events": sum(BY_TYPE[k].values()),
                 "by_type": BY_TYPE[k]} for k, r in zip(REGION_KEYS, REGIONS_META)],
    "events": EVENT_REGIONS,
    # 'global' deliberately absent from by_region (contract: scorecard.json IS global)
    "by_region": {k: {"rows": _sc(BY_TYPE[k])} for k in REGION_KEYS if k != "global"},
}
META = {"last_updated": "2026-01-11T03:00:00Z", "cycle": "weekly", "week": "2026-W02",
        "window": {"start": "2026-01-01", "end": "2026-01-11"},
        "models": ["ifs_hres", "m2"], "baseline_model": "ifs_hres",
        "lead_times_hours": [24, 72],
        "metrics": {"RMSE": {"label": "RMSE", "units": "K",
                             "event_types": ["heat_wave", "freeze", "atmospheric_river"]}}}

# merged-map timeline: same 5 events (type/bbox drive the region-filtered view)
TL = [dict(id=e["id"], type=e["event_type"], title=e["title"], start=e["start"],
           end=e["end"], qualified_at=e["start"].replace("-", "") + "00", bbox=e["bbox"])
      for e in EV]


def _write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _build(root: Path):
    shutil.copytree(DASH, root, dirs_exist_ok=True)
    d = root / "data" / "results"
    _write(d / "meta.json", META)
    _write(d / "events.json", EV)          # events files are bare arrays (asRows)
    _write(d / "events_selected.json", EV)
    _write(d / "scores.json", {"rows": SCORES})
    _write(d / "scorecard.json", {"baseline": "ifs_hres", "rows": _sc(GLOBAL_ROW_N)})
    _write(d / "pair_status.json", {})
    _write(d / "weeks.json", {"weeks": [{"week": "2025-W52"}]})
    _write(d / "scorecard_by_region.json", SCORECARD_BY_REGION)
    _write(d / "global_timeline.json",   # merged map boxes (region-filtered counts)
           {"schema": 1, "window": {"start": "2026-01-01", "end": "2026-01-11", "days": 11},
            "events": TL})
    fe._build_raw_fixture(str(d), frames=fe.GD_RAW_TS, cutoff=fe.GD_RAW_TS[-1])
    # archived week predating the region lane: same files, region file ABSENT
    h = d / "history" / "2025-W52"
    h.mkdir(parents=True, exist_ok=True)
    for f in ("meta.json", "events.json", "events_selected.json", "scores.json",
              "pair_status.json"):
        shutil.copy(d / f, h / f)
    _write(h / "scorecard.json", {"baseline": "ifs_hres", "rows": _sc(BY_TYPE["global"])})


# ------------------------------------------------------------------ server --
@pytest.fixture()
def base(tmp_path):
    root = tmp_path / "webroot"
    _build(root)
    srv, url = fe._gd_server(str(root))
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def _pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture()
def page_ctx(_pw):
    browser = _pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.on("console", lambda m: errs.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    yield page, errs
    browser.close()


@pytest.fixture()
def dash(page_ctx, base):
    page, errs = page_ctx
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#scorecard-tables .sc-block", timeout=15000)
    page.wait_for_selector("#region-group:not(.hidden)", timeout=8000)
    yield page, errs, base


def region(page):
    return page.evaluate("() => window.__debug.state.region")


def lane(page, chip):
    return page.locator("#scorecard-tables .sc-block",
                        has=page.locator(".type-chip", has_text=chip))


def tab_labels(page):
    return page.eval_on_selector_all("#type-tabs button", "els => els.map(e => e.textContent)")


KV_EVENTS = """() => [...document.querySelectorAll('#kv-line .kv')]
    .find(k => k.querySelector('.k').textContent === 'events')
    .querySelector('.v').textContent"""


# ------------------------------------------------------------------- tests --
def test_dropdown_populates(dash):
    page, errs, _ = dash
    opts = page.eval_on_selector_all("#region-select option", "els => els.map(e => e.value)")
    assert opts == REGION_KEYS           # contract order, Global first
    assert page.input_value("#region-select") == "global"
    assert errs == []


def test_region_renders_scorecard_with_honest_counts(dash):
    page, errs, _ = dash
    page.select_option("#region-select", "europe")
    page.wait_for_function("() => window.__debug.state.region === 'europe'")
    assert "1 event" in lane(page, "Heat wave").locator("h3").inner_text()
    assert "1 event" in lane(page, "Freeze").locator("h3").inner_text()
    assert "No atmospheric rivers in Europe" in lane(page, "Atmospheric river").inner_text()
    # the scorecard echoes the region the way it echoes the type filter
    assert "in Europe" in page.inner_text("#sc-scope")
    # NH counts the multi-membership event (f_eu is NH *and* Europe); its empty
    # note names NH, not Europe
    page.select_option("#region-select", "northern_hemisphere")
    page.wait_for_function("() => window.__debug.state.region === 'northern_hemisphere'")
    assert "2 events" in lane(page, "Heat wave").locator("h3").inner_text()
    assert "No atmospheric rivers in Northern Hemisphere" in lane(page, "Atmospheric river").inner_text()
    page.select_option("#region-select", "tropics")
    page.wait_for_function("() => window.__debug.state.region === 'tropics'")
    assert "1 event" in lane(page, "Atmospheric river").locator("h3").inner_text()
    assert "No heat waves in Tropics" in lane(page, "Heat wave").inner_text()
    assert errs == []


def test_merged_map_region_view(dash):
    page, errs, _ = dash
    page.wait_for_function("() => { const d = window.MergedMap && MergedMap._dbg(); return d && d.mounted; }",
                           timeout=15000)
    p0 = page.evaluate("() => window.MergedMap._dbg().project([0, 0])")
    page.select_option("#region-select", "europe")
    page.wait_for_function("() => window.MergedMap._dbg().region === 'europe'", timeout=4000)
    p1 = page.evaluate("() => window.MergedMap._dbg().project([0, 0])")
    assert p1 and (abs(p1[0] - p0[0]) > 2 or abs(p1[1] - p0[1]) > 2), \
        f"projection unchanged after region switch: {p0} -> {p1}"
    # the region's SW corner sits inside the canvas (the view re-zoomed, not just shifted)
    w = page.evaluate("() => document.querySelector('#raw-canvas').clientWidth")
    corner = page.evaluate("() => window.MergedMap._dbg().project([-12.5, 35])")
    assert corner and 0 <= corner[0] <= w and 0 <= corner[1], f"corner {corner} off canvas (W={w})"
    # region view survives a type-tab repaint (counts are region-filtered)
    page.locator("#type-tabs button", has_text="Heat waves").click()
    assert page.evaluate("() => window.MergedMap._dbg().region") == "europe"
    page.locator("#type-tabs button", has_text="All").click()
    page.select_option("#region-select", "global")
    page.wait_for_function("() => window.MergedMap._dbg().region === null", timeout=4000)
    p3 = page.evaluate("() => window.MergedMap._dbg().project([0, 0])")
    assert abs(p3[0] - p0[0]) < 1 and abs(p3[1] - p0[1]) < 1, "world view not restored"
    assert errs == []


def test_url_persistence_roundtrip(page_ctx, base):
    page, errs = page_ctx
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#region-group:not(.hidden)", timeout=8000)
    page.select_option("#region-select", "europe")
    page.wait_for_function("() => location.hash.includes('region=europe')")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => window.__debug.state.region === 'europe'", timeout=8000)
    assert "No atmospheric rivers in Europe" in page.content()
    page.go_back()   # back to the no-region entry -> Global
    page.wait_for_function("() => window.__debug.state.region === 'global'", timeout=4000)
    page.go_forward()
    page.wait_for_function("() => window.__debug.state.region === 'europe'", timeout=4000)
    # an unknown region key is dropped from the URL, never silently kept
    page.goto(base + "/#region=atlantis", wait_until="domcontentloaded")
    page.wait_for_selector("#scorecard-tables .sc-block", timeout=8000)
    page.wait_for_function("() => !location.hash.includes('atlantis')", timeout=8000)
    assert region(page) == "global"
    assert errs == []


def test_archived_week_hides_dropdown(page_ctx, base):
    page, errs = page_ctx
    page.goto(base + "/?week=2025-W52", wait_until="domcontentloaded")
    page.wait_for_selector("#scorecard-tables .sc-block", timeout=8000)
    page.wait_for_timeout(1000)   # let the region 404 resolve (control stays hidden)
    assert page.is_hidden("#region-select")
    assert region(page) == "global"
    assert errs == []


def test_region_filters_lists_and_counts(dash):
    """The region view must be honest everywhere — merged-map count + section
    label, the EVENTS stat, type tabs, the scorecard echo and the event list all
    follow the region via the ewb_live/regions.py centroid rule (JS port)."""
    page, errs, _ = dash
    sec_aria = "() => document.querySelector('#global-sec').getAttribute('aria-label')"
    assert page.inner_text("#gm-count").startswith("5 events")
    assert page.evaluate(sec_aria).startswith("Global")
    assert page.evaluate(KV_EVENTS) == "5"
    page.select_option("#region-select", "europe")
    page.wait_for_function("() => window.__debug.state.region === 'europe'")
    assert page.inner_text("#gm-count").startswith("Europe · 2 events")
    assert page.evaluate(sec_aria).startswith("Europe")
    assert page.evaluate(KV_EVENTS) == "2"      # matches the contract's n_events
    labels = tab_labels(page)
    assert any(l.startswith("Heat waves") and l.strip().endswith("1") for l in labels)
    assert any(l.startswith("Freezes") and l.strip().endswith("1") for l in labels)
    assert any(l.startswith("Atmospheric rivers") and l.strip().endswith("0") for l in labels)
    assert page.inner_text("#event-count").startswith("2 event")
    titles = page.eval_on_selector_all("#event-table tbody tr td:nth-child(2)",
                                       "els => els.map(e => e.textContent)")
    # redesign rows read "<Type> · <place>" + a date/days line — region-filtered
    assert len(titles) == 2
    assert titles[0].startswith("Heat wave") and titles[1].startswith("Freeze")
    # centroid-rule parity with ewb_live/regions.py: dateline wrap + subtropical rule
    assert "tropics" in page.evaluate("() => window.EwbRegions.eventRegions([5, 15, 170, -170])")
    assert "northern_hemisphere" not in page.evaluate(
        "() => window.EwbRegions.eventRegions([5, 15, 170, -170])")
    got_eu = page.evaluate("() => window.EwbRegions.eventRegions([40, 50, -5, 10])")
    assert "europe" in got_eu and "northern_hemisphere" in got_eu
    # and back to Global restores everything
    page.select_option("#region-select", "global")
    page.wait_for_function("() => window.__debug.state.region === 'global'")
    assert page.inner_text("#gm-count").startswith("5 events")
    assert page.inner_text("#event-count").startswith("5 event")
    assert errs == []


def test_region_deep_link_waits_for_lazy_file(page_ctx, base):
    page, errs = page_ctx
    page.goto(base + "/#region=north_america", wait_until="domcontentloaded")
    # first paint (Global rows) lands before the lazy region file resolves; the
    # region view must apply once it does — no manual reload.
    page.wait_for_function("() => window.__debug.state.region === 'north_america'", timeout=8000)
    assert "No freezes in North America" in page.content()
    assert "region=north_america" in page.evaluate("() => location.hash")
    assert errs == []


def test_small_sample_cue_marks_cells_and_warns(dash):
    """Owner 2026-11-10: cells resting on fewer than 5 events carry a subtle red
    glow, and the page says so — in the note, the lane header and the tooltip.
    The fixture's Global rows carry n >= 5, so Global must show a clean table;
    every region slice is under 5, so Europe must show the whole cue."""
    page, errs, _ = dash
    small_count = "() => document.querySelectorAll('td.cell.small-n').length"
    assert page.evaluate(small_count) == 0
    assert page.is_hidden("#sc-sample-note")
    page.select_option("#region-select", "europe")
    page.wait_for_function("() => window.__debug.state.region === 'europe'")
    assert page.evaluate(small_count) > 0, "no cell flagged small-n in a 1-event region"
    note = page.inner_text("#sc-sample-note")
    assert "fewer than 5 events" in note and "Europe" in note and "indicative" in note
    # the cue is not colour-only: every flagged cell also carries the words
    page.locator("td.cell.small-n").first.hover()
    page.wait_for_selector("#tooltip:not(.hidden)", timeout=4000)
    tip = page.inner_text("#tooltip")
    assert "sample" in tip and "below 5" in tip
    # lane header wording: 1 event reads 'indicative', 2..4 read 'small sample'
    assert "indicative" in lane(page, "Heat wave").locator("h3").inner_text()
    page.select_option("#region-select", "northern_hemisphere")
    page.wait_for_function("() => window.__debug.state.region === 'northern_hemisphere'")
    assert "small sample" in lane(page, "Heat wave").locator("h3").inner_text()
    assert page.evaluate(small_count) > 0
    # back to Global: the cue and the note both leave the page
    page.select_option("#region-select", "global")
    page.wait_for_function("() => window.__debug.state.region === 'global'")
    assert page.evaluate(small_count) == 0
    assert page.is_hidden("#sc-sample-note")
    assert errs == []


def test_regions_come_from_the_server_map_not_client_geometry(dash):
    """The tables were built from the server's footprint assignment, so the JS
    views filter with that same map (`events` in the contract). Client geometry
    is only the fallback for an id the file does not cover."""
    page, errs, _ = dash
    # published map wins: a nonsense bbox still returns the server's keys
    got = page.evaluate("() => window.EwbRegions.forEvent('h_na', [0, 1, 0, 1])")
    assert got == EVENT_REGIONS["h_na"]
    # unknown id -> the centroid port (europe + NH for that box)
    fb = page.evaluate("() => window.EwbRegions.forEvent('not-in-the-run', [40, 50, -5, 10])")
    assert "europe" in fb and "northern_hemisphere" in fb
    assert errs == []
