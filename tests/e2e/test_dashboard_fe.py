"""Frontend e2e tests (owner feedback 2026-11-07, A1-A5 + B).

Playwright (sync API) against the deployed dashboard:
  EWB_LIVE_URL, default http://localhost:8050.
Run:  .venv/bin/python -m pytest tests/e2e -m e2e
Skips cleanly when the box is unreachable. Screenshots land in
notes/screenshots/fe-*.png (desktop + mobile) for the owner review.

Freeze coverage: the live week may contain no freeze candidates (they are
seasonal), so those tests run against a local fixture built from the repo's
data/results snapshot plus a synthesized freeze event whose viz.json is real
(88bb73d7ae25, schema 2, P15) — same renderer code path as the live site.
"""
import json
import os
import shutil
import threading
import time
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIVE_URL = os.environ.get("EWB_LIVE_URL", "http://localhost:8050")
SHOTS = os.path.join(ROOT, "notes", "screenshots")

# event ids rotate with weekly runs — tests resolve live ids via fixtures
FREEZE_VIZ_ID = "88bb73d7ae25"  # freeze, viz schema 2 (P15) — repo snapshot fixture

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _urlopen(url, timeout=10):
    # the public edge (ewb.espr.ai) 403s Python-urllib's default UA; a
    # plain browser UA passes — box URL (port 8050) doesn't care
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (e2e)"})
    return urlopen(req, timeout=timeout)


def _reachable(url):
    try:
        with _urlopen(url.rstrip("/") + "/data/results/meta.json", 5):
            return True
    except Exception:
        return False


def _get_json(base, path):
    with _urlopen(base.rstrip("/") + path) as r:
        return json.load(r)


def _find_with_viz(base, want_type):
    """First curated event of the given type that has a viz.json asset."""
    sel = _get_json(base, "/data/results/events_selected.json")
    evs = sel["events"] if isinstance(sel, dict) else sel
    for ev in evs:
        if ev.get("event_type") != want_type:
            continue
        try:
            v = _get_json(base, f"/data/results/events/{ev['id']}/viz.json")
            if v and v.get("schema") in (1, 2, 3):
                return ev["id"], v
        except Exception:
            continue
    return None, None


@pytest.fixture(scope="module")
def live_url():
    if not _reachable(LIVE_URL):
        pytest.skip(f"dashboard not reachable at {LIVE_URL}")
    return LIVE_URL.rstrip("/")


@pytest.fixture(scope="module")
def heat_event(live_url):
    ev_id, viz = _find_with_viz(live_url, "heat_wave")
    if not ev_id:
        pytest.skip("no heat_wave event with viz in the live curated set")
    return ev_id, viz


@pytest.fixture(scope="module")
def ar_event(live_url):
    ev_id, viz = _find_with_viz(live_url, "atmospheric_river")
    if not ev_id:
        pytest.skip("no atmospheric_river event with viz in the live curated set")
    return ev_id, viz


@pytest.fixture()
def page_ctx():
    """Fresh browser context; collects console/page errors for the test."""
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.errors = errors  # type: ignore[attr-defined]
        yield page
        browser.close()


def _open_event(page, base, ev_id):
    page.goto(base + "/#event=" + ev_id)
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.wait_for_timeout(900)  # tiles + refit frames


def _raster_alpha_stats(page, selector):
    """Sample an overlay <img> raster on a canvas: fraction of fully
    transparent pixels and a probe of painted colors."""
    return page.evaluate(
        """(sel) => {
            const img = document.querySelector(sel);
            if (!img || !img.complete || !img.naturalWidth) return null;
            const c = document.createElement('canvas');
            c.width = img.naturalWidth; c.height = img.naturalHeight;
            const g = c.getContext('2d');
            g.drawImage(img, 0, 0);
            const d = g.getImageData(0, 0, c.width, c.height).data;
            let transparent = 0, painted = 0;
            const hist = {};   // probe = the most common painted color (the
            for (let i = 0; i < d.length; i += 4) {  // tier fill), not an
                if (d[i + 3] === 0) transparent++;   // arbitrary first pixel —
                else {                               // tier edges are drawn in
                    painted++;                       // a darker hairline tone
                    const k = d[i] + ',' + d[i+1] + ',' + d[i+2];
                    hist[k] = (hist[k] || 0) + 1;
                }
            }
            const top = Object.entries(hist).sort((a, b) => b[1] - a[1])[0];
            const probe = top ? top[0].split(',').map(Number) : null;
            const corner = [d[3]];  // alpha of pixel (0,0)
            return { transparentFrac: transparent / (d.length / 4),
                     painted, probe, cornerAlpha: corner[0] };
        }""",
        selector,
    )


# ---------------------------------------------------------------- A2 ----
def test_more_metrics_button_never_duplicates(live_url, page_ctx):
    """A2 regression (owner 2026-11-07): every tab click used to append
    another 'more metrics' button to the leads row. Exactly one, forever."""
    page = page_ctx
    page.goto(live_url)
    page.wait_for_selector("#lead-chips button", timeout=20000)
    tabs = page.locator("#type-tabs button")
    n = tabs.count()
    assert n >= 2
    for i in range(10):
        for t in range(n):
            tabs.nth(t).click()
            page.wait_for_timeout(15)
    # also churn open/close an event mid-loop
    page.locator("#type-tabs button").first.click()
    row = page.locator("#event-table tbody tr").first
    row.click()
    page.wait_for_selector("#detail-sec:not(.hidden)")
    page.click("#detail-close")
    for i in range(5):
        for t in range(n):
            tabs.nth(t).click()
            page.wait_for_timeout(15)
    assert page.locator("#metrics-more").count() == 1
    assert page.locator("#lead-more").count() == 1
    assert page.locator('nav.controls button:has-text("more metrics")').count() == 1


# ---------------------------------------------------------------- A1 ----
def test_open_back_forward(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url)
    page.wait_for_selector("#event-table tbody tr", timeout=20000)
    page.locator(".table-wrap.tall").first.evaluate("el => el.scrollTop = 150")
    # Park a table row mid-viewport, then read the ACTUAL window scroll (the
    # global dashboard section shifts geometry between runs; a hardcoded 600
    # can land the table below the fold).
    page.evaluate("""() => { const rows = document.querySelectorAll('#event-table tbody tr');
        rows[7].scrollIntoView({ block: 'center' }); }""")
    page.wait_for_timeout(150)
    # Click the row Playwright will NOT move the page for: the one already at
    # viewport center (its scrollIntoViewIfNeeded re-centers any other row and
    # shifts the window before the app records scrollY — the app restores the
    # CLICK-TIME position, which is correct behavior).
    vis = page.evaluate("""() => { const rows = [...document.querySelectorAll('#event-table tbody tr')];
        const mid = innerHeight / 2;
        let best = -1, bd = 1e9;
        rows.forEach((r, i) => { const b = r.getBoundingClientRect();
            if (b.top < 90 || b.bottom > innerHeight - 10) return;
            const d = Math.abs((b.top + b.bottom) / 2 - mid);
            if (d < bd) { bd = d; best = i; } });
        return best; }""")
    assert vis >= 0, "no event row fully visible"
    page.locator("#event-table tbody tr").nth(vis).click()
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    # the app records the click-time scroll in its own return-state — that is
    # the exact position back must restore (win0 measured earlier can differ by
    # however far Playwright re-centers the viewport for the click)
    win0 = page.evaluate("state.listReturn.y")
    wrap0 = page.evaluate("state.listReturn.ty")
    assert "event=" in page.evaluate("location.hash")
    assert page.locator("#events-sec").is_hidden()
    # Back: returns to list, hash cleared, filters/scroll restored
    page.go_back()
    page.wait_for_timeout(600)
    assert page.locator("#detail-sec").is_hidden()
    assert page.locator("#events-sec").is_visible()
    assert "event=" not in page.evaluate("location.hash")
    scroll = page.evaluate("window.scrollY")
    wrap_top = page.locator(".table-wrap.tall").first.evaluate("el => el.scrollTop")
    assert abs(scroll - win0) < 80, f"list scroll not restored ({scroll} vs {win0})"
    assert abs(wrap_top - wrap0) < 40, f"table scroll not restored ({wrap_top} vs {wrap0})"
    # Forward: re-opens the event
    page.go_forward()
    page.wait_for_selector("#detail-sec:not(.hidden)")
    assert "event=" in page.evaluate("location.hash")


def test_open_then_close_button(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url)
    page.wait_for_selector("#event-table tbody tr", timeout=20000)
    page.locator("#event-table tbody tr").first.click()
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.click("#detail-close")
    page.wait_for_selector("#events-sec:not(.hidden)")
    assert "event=" not in page.evaluate("location.hash")
    # Back after close: lands on the event again (real history entries)
    page.go_back()
    page.wait_for_selector("#detail-sec:not(.hidden)")


def test_open_then_esc(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url)
    page.wait_for_selector("#event-table tbody tr", timeout=20000)
    page.locator("#event-table tbody tr").first.click()
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.keyboard.press("Escape")
    page.wait_for_selector("#events-sec:not(.hidden)")
    assert page.locator("#detail-sec").is_hidden()
    page.keyboard.press("Escape")  # no-op on the list view
    assert page.locator("#events-sec").is_visible()


def test_deep_link_then_back(live_url, heat_event, page_ctx):
    page = page_ctx
    page.goto(live_url)  # list
    page.wait_for_selector("#event-table tbody tr", timeout=20000)
    page.evaluate(f"location.hash = '#event={heat_event[0]}'")  # pushes a history entry
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.go_back()
    page.wait_for_timeout(600)
    assert page.locator("#detail-sec").is_hidden()
    assert page.locator("#events-sec").is_visible()
    # cold deep link in a fresh page
    page2 = page.context.new_page()
    page2.goto(live_url + f"/#event={heat_event[0]}")
    page2.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    assert page2.locator("#detail-title").inner_text().strip() != ""
    page2.close()


# ------------------------------------------------------------ B / heat ----
def test_heat_detail_reference_component(live_url, heat_event, page_ctx):
    ev_id, viz = heat_event
    page = page_ctx
    _open_event(page, live_url, ev_id)
    # white chip near the event
    chip = page.locator(".ev3-namechip").first
    assert chip.is_visible()
    assert chip.inner_text().startswith("Heatwave #")
    # bottom bar: duration-ramp note left, single-swatch legend right
    bar = page.locator(".ev3-panel.ev3-obs .ev3-bbar").first
    assert bar.is_visible()
    left = bar.locator(".bbar-l").inner_text()
    assert left.startswith("Heat \u2014 days >"), left
    legend = bar.locator(".bbar-r")
    assert legend.locator(".ev3-swatch").count() == 1
    q = viz.get("quantile") or 0.85
    assert legend.inner_text().strip() == "P" + str(round(q * 100))
    # one tier color, everything else transparent (basemap shows through)
    stats = _raster_alpha_stats(page, ".ev3-panel.ev3-obs img.ev3-crisp")
    assert stats and stats["cornerAlpha"] == 0, "below-threshold cells must be transparent"
    # Triage ponytail 2026-11-08: the >0.5 bound assumed sparse events; the
    # p85/p15 re-selection produced denser rasters (live event at 0.39).
    # 0.25 is the honest floor for "tier covers a minority of the grid"
    # (P85 exceedance ≈ top decile of climatology). Tighten only if the
    # colorizer starts flooding cells.
    assert stats["transparentFrac"] > 0.25, "tier cells must remain a minority of the grid"
    assert stats["painted"] > 100, "event tier cells must be painted"
    # A4: fitted to the footprint, not a hemisphere
    fit = page.evaluate("""() => {
        const m = window.__ev3.maps[0];
        const b = m.getBounds();
        return { zoom: m.getZoom(), latSpan: b.getNorth() - b.getSouth(),
                 lonSpan: b.getEast() - b.getWest() }; }""")
    assert fit["zoom"] >= 4, f"map still zoomed out: {fit}"
    assert fit["latSpan"] < 45 and fit["lonSpan"] < 90, f"fit too wide: {fit}"
    # time slider: play/step/range chrome + readout (contract §4)
    assert page.locator(".ev3-slider").count() == 1
    read0 = page.locator(".ev3-readout").inner_text()
    assert "Valid time" in read0 and "models T+" in read0, read0
    page.locator(".ev3-next").click()
    page.wait_for_timeout(300)
    read1 = page.locator(".ev3-readout").inner_text()
    assert read1 != read0, "step button must change the valid time"
    assert "day=" in page.url, "off-anchor valid time belongs in the URL"
    rng_max = int(page.locator(".ev3-srange").get_attribute("max"))
    page.locator(".ev3-srange").fill(str(rng_max))
    page.wait_for_timeout(300)
    assert "day=" not in page.url or page.locator(".ev3-srange").input_value() == str(rng_max)
    page.locator(".ev3-play").click()   # play, then pause again (no hang, no errors)
    page.wait_for_timeout(1100)
    page.locator(".ev3-play").click()
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-heat.png"), full_page=False)
    page.evaluate("window.scrollTo(0, document.querySelector('#detail-sec').offsetTop)")
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-heat-full.png"))
    assert page.errors == [], f"console errors: {page.errors}"


def test_overlay_layers_single_card_and_controls(live_url, heat_event, page_ctx):
    """briefs/event_view_overlay.md: one map card; every model with data at the
    lead is a layer row with visibility/colour/opacity; state round-trips to
    the URL (#lyr=) and back on reload."""
    page = page_ctx
    _open_event(page, live_url, heat_event[0])
    assert page.locator("#detail-viz .ev3-panel").count() == 1, "detail must be ONE map card"
    rows = page.locator("#detail-viz .ev3-layer")
    assert rows.count() >= 2, "observed + at least one model layer row expected"
    # observed layer is painted on the map
    assert page.locator(".ev3-panel.ev3-obs img.ev3-crisp").count() >= 1
    # toggle a model layer off: overlay removed, state reported through #lyr=
    # (contract §1: which models are visible by default is baseline + 2, so
    # find the first model row that IS visible rather than assuming row 1)
    model_row = None
    for i in range(1, rows.count()):
        r = rows.nth(i)
        cls = r.get_attribute("class") or ""
        if "dis" not in cls and r.locator(".ev3-lvis").is_checked():
            model_row = r
            break
    assert model_row is not None, "at least one model layer visible by default"
    model_name = model_row.locator(".ev3-lname").inner_text()
    model_row.locator(".ev3-lvis").uncheck()
    page.wait_for_timeout(300)
    lyr = page.evaluate("() => window.__ev3.layers()")
    entry = [e for e in lyr.split(",") if e.startswith(model_name + "|")]
    assert entry and entry[0].endswith("|0"), f"toggled-off layer must report 0: {lyr}"
    assert "lyr=" in page.url, "layer state must round-trip into the URL hash"
    # opacity slider repaints the layer and follows the URL
    obs_row = rows.nth(0)
    obs_row.locator(".ev3-lop").fill("60")
    obs_row.locator(".ev3-lop").dispatch_event("input")
    page.wait_for_timeout(300)
    assert obs_row.locator(".ev3-lopv").inner_text() == "60%"
    lyr2 = page.evaluate("() => window.__ev3.layers()")
    assert "|60|" in lyr2, f"opacity must serialize: {lyr2}"
    # recolour the observed layer: raster probe follows (pure red fill)
    obs_row.locator(".ev3-lcol").evaluate(
        "el => { el.value = '#ff0000'; el.dispatchEvent(new Event('input')); }")
    page.wait_for_timeout(300)
    stats = _raster_alpha_stats(page, ".ev3-panel.ev3-obs img.ev3-crisp")
    assert stats and stats["probe"][0] > 200 and stats["probe"][1] < 60 and stats["probe"][2] < 60, \
        f"layer colour control must repaint the raster: {stats}"
    # deep link: the same #lyr= state reproduces on a fresh page
    url = page.url
    page.goto("about:blank")
    page.goto(url)
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.wait_for_timeout(900)
    lyr3 = page.evaluate("() => window.__ev3.layers()")
    assert "|60|" in lyr3, f"deep link must restore opacity state: {lyr3}"
    col = page.locator("#detail-viz .ev3-layer").nth(0).locator(".ev3-lcol").input_value()
    assert col.lower() == "#ff0000", f"colour input must restore from #lyr=: {col}"
    assert page.errors == [], f"console errors: {page.errors}"


# --------------------------------------------------------------- AR ----
def test_ar_detail_transparent_and_fitted(live_url, ar_event, page_ctx):
    page = page_ctx
    _open_event(page, live_url, ar_event[0])
    fit = page.evaluate("""() => {
        const m = window.__ev3.maps[0];
        const b = m.getBounds();
        return { zoom: m.getZoom(), latSpan: b.getNorth() - b.getSouth(),
                 lonSpan: b.getEast() - b.getWest() }; }""")
    # AR ribbons are large; the bug was the WORLD view (zoom 2, ~360° span)
    assert fit["zoom"] >= 3, f"AR map still hemisphere-zoomed: {fit}"
    assert fit["latSpan"] < 110 and fit["lonSpan"] < 200, f"AR fit too wide: {fit}"
    stats = _raster_alpha_stats(page, ".ev3-panel.ev3-obs img.leaflet-image-layer")
    assert stats, "AR field overlay missing"
    assert stats["cornerAlpha"] == 0, "below-threshold AR cells must be transparent (A3)"
    assert stats["transparentFrac"] > 0.15, "opaque plate: below-threshold area must show basemap"
    chip = page.locator(".ev3-namechip").first
    assert chip.inner_text().startswith("Atmospheric river #")
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-ar.png"))
    assert page.errors == [], f"console errors: {page.errors}"


# ---------------------------------------------------------- mobile ----
def test_mobile_screenshots_no_overflow(live_url, heat_event, page_ctx):
    page = page_ctx
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_url)
    page.wait_for_selector("#event-table tbody tr", timeout=20000)
    page.screenshot(path=os.path.join(SHOTS, "fe-home-mobile.png"))
    _open_event(page, live_url, heat_event[0])
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 2, f"horizontal overflow on mobile: {overflow}px"
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-heat-mobile.png"))
    assert page.errors == [], f"console errors: {page.errors}"


# ---------------------------------------------------- freeze fixture ----
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass


def _build_freeze_fixture(tmpdir):
    """dashboard/ + snapshot data/results + synthesized freeze event."""
    src = os.path.join(ROOT, "data", "results")
    viz_p = os.path.join(src, "events", FREEZE_VIZ_ID, "viz.json")
    if not os.path.exists(viz_p):
        pytest.skip("local snapshot lacks the freeze viz asset")
    viz = json.load(open(viz_p))
    days = viz["window"]["days"]
    lat, lon = viz["grid"]["lat"], viz["grid"]["lon"]
    ev = {
        "id": FREEZE_VIZ_ID,
        "event_type": "freeze",
        "title": f"Freeze {days[0]} to {days[-1]} (e2e fixture)",
        "bbox": [min(lat), max(lat), min(lon), max(lon)],
        # validate.py:67 contract: titles name the LAST day; end is EXCLUSIVE
        "start": days[0],
        "end": (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat(),
        "details": {"region": "fixture"},
    }
    root = os.path.join(tmpdir, "webroot")
    os.makedirs(root)
    for f in os.listdir(os.path.join(ROOT, "dashboard")):
        s = os.path.join(ROOT, "dashboard", f)
        d = os.path.join(root, f)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    data_dir = os.path.join(root, "data", "results")
    os.makedirs(data_dir)
    for f in ("meta.json", "events_selected.json", "events.json", "scores.json",
              "scorecard.json", "pair_status.json"):
        p = os.path.join(src, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(data_dir, f))
    sel_p = os.path.join(data_dir, "events_selected.json")
    sel = json.load(open(sel_p))
    sel_list = sel["events"] if isinstance(sel, dict) else sel
    sel_list.append(ev)
    json.dump(sel, open(sel_p, "w"))
    # app boot fetches weeks.json — serve an empty archive list so the boot
    # path stays 404-free (a network 404 trips the page.errors assertion)
    json.dump({"weeks": []}, open(os.path.join(data_dir, "weeks.json"), "w"))
    # boot lazy-fetches scorecard_by_region.json — an unparseable stub means
    # "no region data" (dropdown hidden), same as the pre-region dashboard
    json.dump({}, open(os.path.join(data_dir, "scorecard_by_region.json"), "w"))
    # GlobalDash boot-fetches the timeline — same minimal synthetic asset so
    # the global section renders instead of 404-ing (console-error assertion)
    json.dump(_gd_timeline(), open(os.path.join(data_dir, "global_timeline.json"), "w"))
    _build_raw_fixture(data_dir)   # RawDash boot-fetches the raw index too
    ev_dir = os.path.join(data_dir, "events", FREEZE_VIZ_ID)
    os.makedirs(ev_dir)
    shutil.copy2(viz_p, os.path.join(ev_dir, "viz.json"))
    return root


@pytest.fixture(scope="module")
def freeze_server(tmp_path_factory):
    root = _build_freeze_fixture(str(tmp_path_factory.mktemp("fe-freeze")))
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


def test_freeze_detail_reference_component(freeze_server, page_ctx):
    page = page_ctx
    _open_event(page, freeze_server, FREEZE_VIZ_ID)
    chip = page.locator(".ev3-namechip").first
    assert chip.inner_text().startswith("Freeze #")
    bar = page.locator(".ev3-panel.ev3-obs .ev3-bbar").first
    assert bar.is_visible()
    assert bar.locator(".bbar-l").inner_text().startswith("Freeze \u2014 days <")
    legend = bar.locator(".bbar-r")
    assert legend.locator(".ev3-swatch").count() == 1
    assert legend.inner_text().strip() == "P15"
    rgb = legend.locator(".ev3-swatch").evaluate(
        "el => getComputedStyle(el).backgroundColor")
    assert rgb.replace(" ", "") == "rgb(33,113,181)", rgb  # --tier-freeze token
    stats = _raster_alpha_stats(page, ".ev3-panel.ev3-obs img.ev3-crisp")
    assert stats and stats["cornerAlpha"] == 0
    # dur-alpha raster stores premultiplied alpha -> getImageData rgb may sit
    # one quantum off the token; compare with the rounding tolerance.
    assert all(abs(a - b) <= 2 for a, b in zip(stats["probe"], [33, 113, 181])), \
        f"freeze tier color wrong: {stats['probe']}"
    page.screenshot(path=os.path.join(SHOTS, "fe-freeze.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    page.screenshot(path=os.path.join(SHOTS, "fe-freeze-mobile.png"))
    assert page.errors == [], f"console errors: {page.errors}"


# ---------------------------------------------------------------------------
# Owner 2026-11-08: clickable catalog boxes, graceful viz-404, AR green token,
# and the ponytail MUST (deep-link to a NON-FIRST lead must be honored).
# ---------------------------------------------------------------------------

# 2026-11-10 redesign: the Leaflet catalog map is gone — the Equal Earth map
# and the event list sit side by side and talk to each other; every control
# sits on the thing it changes.

def _wait_js(page, expr, timeout=20000):
    """Poll a JS expression through evaluate(). wait_for_function() compiles the
    string in the *page* world, which the CSP the public endpoint serves forbids
    (its script-src has no 'unsafe-eval'); evaluate() is protocol-level and
    CSP-immune. Same reason tests/e2e/test_guardian_fe.py carries its own
    wait_js — three live-mode tests here failed on ewb.espr.ai over it, 2026-11-10."""
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate("() => !!(" + expr + ")"):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for: {expr}")


def _home(page, live_url):
    page.goto(live_url + "/")
    page.wait_for_selector("#event-table tbody tr[data-id]", timeout=25000)
    _wait_js(page, "MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(600)


def test_controls_live_in_the_section_they_change(live_url, page_ctx):
    page = page_ctx
    _home(page, live_url)
    where = page.evaluate("""() => {
        const sec = (s) => { const n = document.querySelector(s); return n && (n.closest('section') || {}).id; };
        return { models: sec('#model-pills'), leads: sec('#lead-chips'), metrics: sec('#metrics-more'),
                 search: sec('#event-search'), layers: sec('#raw-layers'), play: sec('#raw-play'),
                 tabs: !!document.querySelector('.explore #type-tabs'),
                 maps: document.querySelectorAll('#map, .events-grid').length,
                 navOutside: document.querySelectorAll('body > nav.controls').length }; }""")
    assert where["models"] == where["leads"] == where["metrics"] == "scorecard-sec", where
    assert where["search"] == "events-sec" and where["play"] == "global-sec", where
    assert where["layers"] is None, where   # the #raw-layers chip row retired (complaint 3)
    assert where["tabs"] and where["maps"] == 0 and where["navOutside"] == 0, where
    # map and list share the first screen on a desktop viewport
    tops = page.evaluate("""() => ['#raw-map-wrap', '#events-sec', '#raw-slider'].map(
        (s) => Math.round(document.querySelector(s).getBoundingClientRect().bottom))""")
    assert max(tops) <= 900, tops
    assert page.errors == [], page.errors


def test_type_filter_narrows_map_list_and_scorecard_together(live_url, page_ctx):
    page = page_ctx
    _home(page, live_url)
    n_all = page.locator("#event-table tbody tr[data-id]").count()
    page.click("#type-tabs button:nth-child(4)")           # atmospheric rivers
    page.wait_for_timeout(600)
    st = page.evaluate("""() => ({ on: MergedMap._dbg().on, hash: location.hash,
        rows: [...document.querySelectorAll('#event-table tbody tr[data-id]')].map((r) => r.dataset.id),
        blocks: document.querySelectorAll('#scorecard-tables .sc-block').length,
        scope: document.querySelector('#sc-scope').textContent,
        types: state.events.reduce((m, e) => (m[e.id] = e.event_type, m), {}) })""")
    assert st["on"] == {"h": False, "f": False, "a": True} and "layers=a" in st["hash"], st
    assert st["rows"] and all(st["types"][i] == "atmospheric_river" for i in st["rows"]), st["rows"]
    assert st["blocks"] == 1 and "Atmospheric rivers only" in st["scope"], st
    page.click("#sc-scope button")                           # the scorecard's own way back
    page.wait_for_timeout(600)
    st = page.evaluate("() => ({ on: MergedMap._dbg().on, hash: location.hash, tab: state.tab })")
    assert st["tab"] == "all" and all(st["on"].values()) and "layers=" not in st["hash"], st
    assert page.locator("#event-table tbody tr[data-id]").count() == n_all
    assert page.errors == [], page.errors


def test_list_row_previews_event_on_map_and_box_marks_its_row(live_url, page_ctx):
    page = page_ctx
    _home(page, live_url)
    ids = page.evaluate("""() => { const act = new Set([...document.querySelectorAll('#gm-boxes .gm-ev')]
            .map((n) => n.dataset.id));
        const rows = [...document.querySelectorAll('#event-table tbody tr[data-id]')].map((r) => r.dataset.id);
        return { off: rows.find((i) => !act.has(i)), on: rows.find((i) => act.has(i)),
                 live: [...document.querySelectorAll('#event-table tr.live')].map((r) => r.dataset.id).sort(),
                 act: [...act].sort() }; }""")
    assert ids["act"] and ids["live"] == ids["act"], ids       # rows mirror what the map shows now
    if ids["off"]:
        page.hover(f'#event-table tr[data-id="{ids["off"]}"]')
        page.wait_for_timeout(250)
        assert page.locator("#gm-boxes .gm-ghost").count() == 1
        bar = page.locator(f'#gm-gantt .gm-bar[data-id="{ids["off"]}"]')
        assert bar.count() == 0 or bar.first.get_attribute("opacity") == "1"
        page.mouse.move(5, 5)
        page.wait_for_timeout(250)
        assert page.locator("#gm-boxes .gm-ghost").count() == 0
    page.locator(f'#gm-boxes .gm-ev[data-id="{ids["on"]}"] .gm-hit').click(force=True)
    page.wait_for_timeout(300)
    assert page.locator(f'#event-table tr.picked[data-id="{ids["on"]}"]').count() == 1
    # a row still opens the event; Back returns to map + list
    page.click(f'#event-table tr[data-id="{ids["on"]}"]')
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=6000)
    assert not page.is_visible("#type-tabs") and not page.is_visible("#raw-canvas")
    page.click("#detail-close")
    page.wait_for_selector("#detail-sec.hidden", state="attached", timeout=6000)
    assert page.is_visible("#type-tabs") and page.is_visible("#raw-canvas") and page.is_visible("#event-table")
    assert page.errors == [], page.errors


def test_viz_404_shows_graceful_state(live_url, heat_event, page_ctx):
    page = page_ctx
    ev_id, _ = heat_event
    warns = []
    page.on("console", lambda m: warns.append(m.text) if m.type == "warning" else None)
    page.route(f"**/events/{ev_id}/viz.json*",
               lambda r: r.fulfill(status=404, body='{"detail":"not found"}',
                                   content_type="application/json"))
    page.goto(live_url + f"/#event={ev_id}")
    page.wait_for_selector("#detail-viz .lane-empty", timeout=15000)
    msg = page.inner_text("#detail-viz")
    assert "Map data not available yet" in msg, msg
    assert page.is_visible("#detail-title"), "rest of the detail page must still render"
    n = sum(1 for w in warns if "viz" in w)
    assert n == 1, f"expected exactly one concise console warning, got {n}: {warns}"


def test_lead_deep_link_nonfirst_lead(live_url, heat_event, page_ctx):
    """Ponytail MUST 2026-11-08: syncUrl used to drop #lead before EventViz
    read it; deep links at any lead except leads[0] opened leads[0]."""
    page = page_ctx
    ev_id, viz = heat_event
    m0 = next(iter((viz.get("models") or {}).values()), {})
    leads = sorted(int(k) for k in m0)
    if len(leads) < 2:
        pytest.skip("single-lead viz asset")
    nonfirst = leads[-1]
    page.goto(live_url + f"/#event={ev_id}&lead={nonfirst}h")
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.wait_for_timeout(900)
    st = page.evaluate("() => ({ lead: state.detailLead, hash: location.hash })")
    assert st["lead"] == nonfirst, f"deep-linked lead {nonfirst} lost: {st}"
    assert f"lead={nonfirst}h" in st["hash"], st
    # the RENDERED panels must sit at the linked lead too — state alone would
    # not catch a mismatch between state.detailLead and the EventViz initial lead
    on = page.locator("#detail-viz .ev-lead.on")
    assert on.count() >= 1
    assert on.first.inner_text().strip() == f"{nonfirst}h", on.first.inner_text()


def test_page_width_shell_caps_content(live_url, page_ctx):
    """Owner spec: main content constrained to min(75vw, 1280px) on wide
    screens, centered, no overflow. Since the OWB rail (2026-11-10) the shell is
    centered in the space the rail leaves, not in the viewport."""
    page = page_ctx
    page.set_viewport_size({"width": 1920, "height": 900})
    page.goto(live_url + "/")
    page.wait_for_selector("#event-table tbody tr, .gm-lrow")
    m = page.evaluate("""(() => { const e = document.querySelector('.shell')
            || document.querySelector('main');
        const rail = document.querySelector('.rail');
        const railW = rail && getComputedStyle(rail).display !== 'none'
            ? rail.getBoundingClientRect().width : 0;
        const r = e.getBoundingClientRect();
        return {w: r.width, left: r.left - railW, right: innerWidth - r.right,
                rail: railW}; })()""")
    assert m["w"] <= 1280.5, f"content wider than 1280 at 1920: {m}"
    assert m["w"] <= 1920 * 0.75 + 1
    assert m["rail"] == 220, f"rail should be 220 px on desktop: {m}"
    assert abs(m["left"] - m["right"]) <= 2, f"not centered beside the rail: {m}"


def test_os_theme_flip_repaints_open_detail(live_url, heat_event, page_ctx):
    """Ponytail NICE 09:18: auto-theme OS flip must repaint an OPEN detail
    (it resolves type/model colors at build time), same as cycleTheme does."""
    page = page_ctx
    ev_id, _ = heat_event
    _open_event(page, live_url, ev_id)
    hits = []
    page.on("request", lambda r: hits.append(r.url)
            if f"events/{ev_id}/viz.json" in r.url else None)
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(1500)
    assert hits, "open detail was not re-selected (colors resolve at build time)"
    assert page.locator("#detail-sec:not(.hidden)").count() == 1, "detail closed by the flip"
    assert page.locator("#detail-viz .ev3-panel").count() >= 1, "detail not repainted"
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.errors == [], f"console errors: {page.errors}"


def test_ar_type_color_token_both_themes(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url + "/")
    light = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--series-ar').trim()")
    assert light == "#2e7d32", light
    page.click("#theme-ctl button[data-pref='dark']")   # segmented control (rail footer)
    dark = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--series-ar').trim()")
    assert dark == "#66bb6a", dark

# ---------------------------------------------------------------- global ----
# Owner 2026-11-08: GLOBAL EVENT DASHBOARD. Synthetic deterministic timeline so
# these tests never rotate with the weekly runs. Includes the ponytail MUST
# 2026-11-08 regression: a bbox-only entry (no grid/masks) must not crash fit.
import base64

_GD_DAYS = ["2026-01-0" + str(i) for i in range(1, 10)] + ["2026-01-10", "2026-01-11"]


def _gd_masks(cells_per_day):
    stride = 2  # ceil(4*4/8)
    out = []
    for (r, c) in cells_per_day:
        ba = bytearray(stride)
        ba[(r * 4 + c) // 8] |= 0x80 >> ((r * 4 + c) % 8)
        out.append(base64.b64encode(bytes(ba)).decode())
    return out


def _gd_timeline():
    grid = {"ny": 4, "nx": 4, "lat0": 40.0, "lat1": 44.0,
            "lon0": -100.0, "lon1": -96.0, "step_deg": 1.0, "step_lon": 1.0}
    e1 = {"id": "gf1", "type": "freeze", "title": "Freeze One (e2e)",
          "start": "2026-01-02", "end": "2026-01-04", "bbox": [40.0, 44.0, -100.0, -96.0],
          "grid": grid, "days": _GD_DAYS[1:4], "masks": _gd_masks([(1, 1), (2, 1), (2, 2)]),
          "qualified_at": "2026010206"}          # ewb contract form: 6-hourly step
    g2 = dict(grid, lon0=-80.0, lon1=-76.0)
    e2 = {"id": "gf2", "type": "heat_wave", "title": "Heat Two (e2e)",
          "start": "2026-01-05", "end": "2026-01-06", "bbox": [40.0, 44.0, -80.0, -76.0],
          "grid": g2, "days": _GD_DAYS[4:6], "masks": _gd_masks([(0, 0), (1, 1)])}
    # ghost: curated event with bbox but no viz asset yet — grid/masks absent
    e3 = {"id": "gf3", "type": "atmospheric_river", "title": "AR Ghost (e2e)",
          "start": "2026-01-07", "end": "2026-01-08", "bbox": [10.0, 20.0, 140.0, 160.0],
          "days": _GD_DAYS[6:8],
          # ISO qualified_at form — pins the qualTs ISO branch (ponytail 19:15):
          # a wrong compact-fold here makes the box/gantt bar never render
          "qualified_at": "2026-01-07T12:00:00Z"}
    # dateline-crossing AR (bbox lon_min > lon_max): the region label must
    # follow the land mass (Alaska/USA), not the raw mid-longitude (Chukotka)
    e4 = {"id": "gm4", "type": "atmospheric_river", "title": "AR Dateline (e2e)",
          "start": "2026-01-02", "end": "2026-01-05",   # 3-day span: tooltip '· 3 days'
          "bbox": [60.0, 68.0, 178.0, -155.0], "days": _GD_DAYS[1:4],
          "qualified_at": "2026010200"}
    # prime-meridian-crossing AR in the 0-360 producer form (350E->20E, the
    # Oct-3 UK/eastward AR shape): box must render as ONE minimal arc across
    # 0 deg -- never split at +/-180, never a whole-globe band. Its viz.json
    # (written by _build_gd_fixture) carries a CONTINUOUS east-ordered grid.
    e5 = {"id": "gm5", "type": "atmospheric_river", "title": "AR Prime Meridian (e2e)",
          "start": "2026-01-02", "end": "2026-01-04",
          "bbox": [50.0, 60.0, 350.0, 20.0], "days": _GD_DAYS[1:4],
          "qualified_at": "2026010200"}
    return {"schema": 1, "generated": "2026-01-11T00:00:00Z",
            "window": {"start": "2026-01-01", "end": "2026-01-11", "days": 11},
            "events": [e1, e2, e3, e4, e5]}


GD_RAW_TS = [d.strftime("%Y%m%d%H") for d in
             [__import__("datetime").datetime(2026, 1, 1)
              + __import__("datetime").timedelta(hours=6 * i)
              for i in range(41)]]   # 2026-01-01T00 … 2026-01-11T00, 6-hourly


def _build_gd_fixture(tmpdir, with_timeline=True, raw_frames=None, raw_cutoff=None, raw_run=False, raw_ivt=False,
                      raw_run_v2=False):
    src = os.path.join(ROOT, "data", "results")
    root = os.path.join(tmpdir, "webroot")
    os.makedirs(root)
    for f in os.listdir(os.path.join(ROOT, "dashboard")):
        s = os.path.join(ROOT, "dashboard", f)
        d = os.path.join(root, f)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    data_dir = os.path.join(root, "data", "results")
    os.makedirs(data_dir)
    for f in ("meta.json", "events_selected.json", "events.json", "scores.json",
              "scorecard.json", "pair_status.json"):
        p = os.path.join(src, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(data_dir, f))
    # boot fetches weeks.json; the repo snapshot has none — serve an empty list
    json.dump({"weeks": []}, open(os.path.join(data_dir, "weeks.json"), "w"))
    # boot lazy-fetches scorecard_by_region.json — stub = no region data,
    # keeping these pre-region fixtures' console clean (404 trips errs==[])
    json.dump({}, open(os.path.join(data_dir, "scorecard_by_region.json"), "w"))
    if raw_frames is not None:
        _build_raw_fixture(data_dir, frames=raw_frames,
                           cutoff=raw_cutoff if raw_cutoff is not None else raw_frames[-1],
                           run=raw_run, ivt=raw_ivt, run_v2=raw_run_v2)
    else:
        _build_raw_fixture(data_dir, run=raw_run, ivt=raw_ivt, run_v2=raw_run_v2)
    if with_timeline:
        tl = _gd_timeline()
        json.dump(tl, open(os.path.join(data_dir, "global_timeline.json"), "w"))
        # curated set must contain them too — the detail view resolves ids via
        # events_selected.json, "open" is a no-op otherwise
        sel_p = os.path.join(data_dir, "events_selected.json")
        if os.path.exists(sel_p):
            sel = json.load(open(sel_p))
            lst = sel["events"] if isinstance(sel, dict) else sel
            for e in tl["events"]:
                lst.append({"id": e["id"], "event_type": e["type"],
                            "title": e["title"], "bbox": e["bbox"],
                            "start": e["start"], "end": e["end"],
                            "details": {"region": "e2e"}})
            json.dump(sel, open(sel_p, "w"))
        # gm5 viz.json: PRIME-MERIDIAN AR with a continuous east-ordered
        # lon array (350..380) and a blob straddling lon 0 -- exactly the
        # shape ewb's seam-safe crop emits after the 0-deg fix (schema 3,
        # '/'-joined mask rows, uint8-scaled ivt per the eventviz decoder)
        import base64
        ev_dir = os.path.join(data_dir, "events", "gm5")
        os.makedirs(ev_dir, exist_ok=True)
        lat = [50.0 + j for j in range(11)]
        lon = [350.0 + i for i in range(31)]          # continuous: 350E -> 380E (= 20E)
        rows = ["".join("1" if (8 <= i <= 12 and 4 <= j <= 7) else "0"
                        for i in range(31)) for j in range(11)]
        ivt = bytearray(11 * 31)
        for j in range(11):
            for i in range(31):
                ivt[j * 31 + i] = 200 if (8 <= i <= 12 and 4 <= j <= 7) else 100
        entry = {"status": "ok", "area_cells": 20, "mask": "/".join(rows),
                 "ivt": {"b64": base64.b64encode(bytes(ivt)).decode(),
                         "shape": [11, 31], "lo": 0.0, "hi": 1000.0}}
        json.dump({"schema": 3, "kind": "ar_ivt", "event_type": "atmospheric_river",
                   "truth_source": "era5", "ivt_threshold_kg_m_s": 250,
                   "grid": {"lat": lat, "lon": lon, "cell_deg": 1.0},
                   "obs": {"anchor_time": "2026-01-03 12:00", "area_cells": 20,
                           "mask": "/".join(rows),
                           "ivt": {"b64": base64.b64encode(bytes(ivt)).decode(),
                                   "shape": [11, 31], "lo": 0.0, "hi": 1000.0}},
                   "models": {"aifs_single": {"24": dict(entry)}}},
                  open(os.path.join(ev_dir, "viz.json"), "w"))
    return root


def _gd_server(root):
    # bind port 0 and read it back — no probe/rebind race on shared runners
    handler = lambda *a, **k: _QuietHandler(*a, directory=root, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


RAW_TS = ["2025110600", "2025110606", "2025110612"]  # 6-hourly, ts2 is past cutoff


def _build_raw_fixture(data_dir, drop_ts=None, frames=None, cutoff=None, run=False, ivt=False,
                       run_v2=False):
    """raw_exceedance asset per the contract in channel_design_frontend.md:
    zlib'd layer-major bitpacked rows (heat,freeze,ar; ny x ceil(nx/8),
    row 0 = lat0 lowest, MSB-first). 5-deg grid; heat blob mid-pacifica,
    freeze blob south, AR band north — distinct so pixel checks can differ.
    run=True adds the duration sidecars of channel_consecutive_days: index
    gets `dur`, every frame a `<ts>.run` (zlib, u8 planes heat/freeze ny*nx)
    with runs for heat: 2 (unpainted), 3 (bin0), 14+ (bin4); freeze unchanged
    1/7+; a fourth heat bit cell is added with run=0 (must stay unpainted).
    ivt=True adds AR IVT class plane sidecars (.ivt) with 5 bins (250-500,
    500-750, 750-1000, 1000-1250, >=1250 kg m-1 s-1) for the AR layer.
    run_v2=True publishes the sidecars as dur rev v2 and sets the provisional
    (today-so-far) bit on the heat run=3 cell."""
    import zlib
    frames = frames or RAW_TS
    cutoff = cutoff or RAW_TS[1]
    g = {"res_deg": 5.0, "ny": 36, "nx": 72, "lat0": -90.0, "lon0": -180.0,
         "step_lat": 5.0, "step_lon": 5.0}
    ny, nx = g["ny"], g["nx"]
    stride = (nx + 7) // 8
    rd = os.path.join(data_dir, "raw_exceedance")
    os.makedirs(rd, exist_ok=True)
    idx = {"schema": 1, "generated": "2025-11-06T12:00:00Z", "grid": g,
           "frames": frames, "cutoff": cutoff,
           "window": {"start": frames[0][:8], "end": frames[-1][:8]}}
    if run:
        idx["dur"] = {"rev": "v2" if run_v2 else "v1", "seed": frames[0]}
    if ivt:
        idx["ivt"] = {"schema": 1, "bins": [250, 500, 750, 1000, 1250]}
        idx["schema"] = 3  # v3 with IVT class planes
    json.dump(idx, open(os.path.join(rd, "index.json"), "w"))
    for k, ts in enumerate(frames):
        layers = [bytearray(ny * stride) for _ in range(3)]
        blobs = [(18 + k % 12, 30), (6 + k % 6, 10), (28, 40 + (k % 5) * 6)]
        for li, (j, i) in enumerate(blobs):
            b = layers[li]
            b[j * stride + (i >> 3)] |= 1 << (7 - (i & 7))
            b[j * stride + ((i + 1) >> 3)] |= 1 << (7 - ((i + 1) & 7))
        if run:
            jh, ih = blobs[0]
            # Add third and fourth heat cells for run=0 (unpainted) and run=14 (bin4)
            # Space them well apart: 20 columns ~ 20*5=100 deg ~ 200px in Equal Earth at 5N
            layers[0][jh * stride + ((ih + 20) >> 3)] |= 1 << (7 - ((ih + 20) & 7))
            layers[0][jh * stride + ((ih + 40) >> 3)] |= 1 << (7 - ((ih + 40) & 7))
        if ts == drop_ts:
            p = os.path.join(rd, ts + ".bin")
            if os.path.exists(p):
                os.remove(p)
            continue
        with open(os.path.join(rd, ts + ".bin"), "wb") as f:
            f.write(zlib.compress(b"".join(bytes(x) for x in layers)))
        if run:
            jh, ih = blobs[0]
            planes = bytearray(2 * ny * nx)
            # heat plane (li=0): run=2 at (j,i), run=3 at (j,i+1), run=0 at (j,i+20), run=14 at (j,i+40)
            planes[0 * ny * nx + jh * nx + ih] = 2       # unpainted (< 3)
            planes[0 * ny * nx + jh * nx + ih + 1] = 3 | (0x80 if run_v2 else 0)   # bin 0 (+ today)
            planes[0 * ny * nx + jh * nx + ih + 20] = 0   # unpainted (= 0)
            planes[0 * ny * nx + jh * nx + ih + 40] = 14  # bin 4
            # freeze plane (li=1): unchanged 1/7+
            for li, (j, i) in enumerate(blobs[1:2]):
                planes[(li + 1) * ny * nx + j * nx + i] = 1
                planes[(li + 1) * ny * nx + j * nx + i + 1] = 7
            with open(os.path.join(rd, ts + ".run"), "wb") as f:
                f.write(zlib.compress(bytes(planes)))
        if ivt:
            # AR IVT class plane: create a u8 grid with classes 1-5 for AR cells
            # AR blob is at blobs[2] = (28, 40 + (k % 5) * 6)
            j_ar, i_ar = blobs[2]
            # First, ensure the AR mask in the .bin file has these cells set
            # The layers[2] (AR layer) needs to have bits set for the AR cells
            # We set cells at (j_ar, i_ar) through (j_ar, i_ar+4) in the AR layer
            for di in range(5):
                if i_ar + di < nx:
                    col = i_ar + di
                    layers[2][j_ar * stride + (col >> 3)] |= 1 << (7 - (col & 7))
            # Now create the .ivt file with classes 1-5
            ivt_plane = bytearray(ny * nx)  # u8, initialized to 0
            # Mark AR cells with different classes for testing
            # Class 1: cell at (j_ar, i_ar)
            ivt_plane[j_ar * nx + i_ar] = 1
            # Class 2: cell at (j_ar, i_ar + 1)
            if i_ar + 1 < nx:
                ivt_plane[j_ar * nx + i_ar + 1] = 2
            # Class 3: cell at (j_ar, i_ar + 2)
            if i_ar + 2 < nx:
                ivt_plane[j_ar * nx + i_ar + 2] = 3
            # Class 4: cell at (j_ar, i_ar + 3)
            if i_ar + 3 < nx:
                ivt_plane[j_ar * nx + i_ar + 3] = 4
            # Class 5: cell at (j_ar, i_ar + 4)
            if i_ar + 4 < nx:
                ivt_plane[j_ar * nx + i_ar + 4] = 5
            with open(os.path.join(rd, ts + ".ivt"), "wb") as f:
                f.write(zlib.compress(bytes(ivt_plane)))
            # Also update the .bin file with the new AR layer
            with open(os.path.join(rd, ts + ".bin"), "wb") as f:
                f.write(zlib.compress(b"".join(bytes(x) for x in layers)))


@pytest.fixture(scope="module")
def raw_server(tmp_path_factory):
    # middle frame's .bin deliberately missing -> graceful-state path
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-raw")), with_timeline=False)
    _build_raw_fixture(os.path.join(root, "data", "results"), drop_ts=RAW_TS[0])
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def ar_ramp_server(tmp_path_factory):
    """AR ramp lane: index carries `ivt` schema 3 and every frame has a `.ivt`
    sidecar with AR IVT class planes (5 bins). AR layer paints with stepped
    green ramp instead of solid green; binary fallback still works."""
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-ar-ramp")),
                             with_timeline=False, raw_ivt=True)
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def dur_server(tmp_path_factory):
    """Duration-ramp lane (channel_consecutive_days): index carries `dur` and
    every frame has a `.run` sidecar (heat/freeze runs 1/7+ + a run=0 bit cell)."""
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-dur")),
                             with_timeline=False, raw_run=True)
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def dur2_server(tmp_path_factory):
    """dur rev v2 lane: the provisional bit rides on the heat run=3 cell, so the
    legend must show a 'today' stop for heat and none for freeze."""
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-dur2")),
                             with_timeline=False, raw_run=True, raw_run_v2=True)
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def gd_server(tmp_path_factory):
    # raw frames span the timeline window (merged map is one time axis) and
    # the cutoff sits at the last frame so the IFS badge never fires here
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-gd")),
                             raw_frames=GD_RAW_TS, raw_cutoff=GD_RAW_TS[-1])
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def gd404_server(tmp_path_factory):
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-gd404")), with_timeline=False)
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


def _mm_goto(page, base, hash_=""):
    page.goto(base + "/" + hash_)
    _wait_js(page, "MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(400)


def _mm_frame(page, ts):
    """Drive the merged slider to a frame by ts (lands exactly: frames are 6h)."""
    idx = page.evaluate(f"MergedMap._dbg().frames.indexOf('{ts}')")
    assert idx >= 0, ts
    page.evaluate("""(i) => { const s = document.querySelector('#raw-slider');
        s.value = String(i); s.dispatchEvent(new Event('input', {bubbles: true})); }""", idx)
    page.wait_for_timeout(250)


def test_merged_renders_layers_boxes_and_gantt(gd_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gd_server)
    assert page.is_visible("#raw-canvas")
    assert "5 events" in page.inner_text("#gm-count")
    assert page.locator("#gm-gantt .gm-bar").count() == 5
    # boot parks on the busiest step (G-11): 03 Jan 18:00 — gf1 + gm4 + gm5 live
    assert "03 Jan 2026" in page.inner_text("#raw-readout")
    assert page.locator("#gm-boxes .gm-ev").count() == 3
    # 02 Jan 06:00: freeze gf1 (qualified 06:00) + dateline AR gm4 + PM AR gm5 are live
    _mm_frame(page, "2026010206")
    assert "02 Jan 2026" in page.inner_text("#raw-readout")
    assert "06:00 UTC" in page.inner_text("#raw-readout")
    ids = page.eval_on_selector_all("#gm-boxes .gm-ev", "els => els.map(e => e.dataset.id).sort()")
    assert ids == ["gf1", "gm4", "gm5"], ids
    # the dateline box renders SPLIT into two rects (wrapped bbox -> two paths)
    d = page.get_attribute("#gm-boxes .gm-ev[data-id=gm4] path.gm-bbox", "d")
    assert d.count("M") == 2, d
    assert page.errors == [], f"console errors: {page.errors}"
    page.screenshot(path=os.path.join(SHOTS, "fe-merged-fixture.png"))


def test_merged_prime_meridian_box_contiguous(gd_server, page_ctx):
    """350E→20E AR (the Oct-3 UK/eastward producer form) on the merged map:
    ONE minimal arc across the prime meridian — no ±180 split, no globe wrap."""
    page = page_ctx
    _mm_goto(page, gd_server)
    _mm_frame(page, "2026010206")
    d = page.get_attribute("#gm-boxes .gm-ev[data-id=gm5] path.gm-bbox", "d")
    assert d.count("M") == 1, d                     # not split at ±180
    m = page.evaluate("""() => {
        const b = document.querySelector('#gm-boxes .gm-ev[data-id=gm5] path.gm-bbox').getBBox();
        const vb = document.querySelector('#gm-boxes').viewBox.baseVal;
        return {span: b.width / vb.width, mid: b.x + b.width / 2 - (vb.x + vb.width / 2)};
    }""")
    assert m["span"] < 0.25, m                      # a 30° arc is <10% width; a globe wrap ~full
    assert m["mid"] > 0, m                          # arc centre (≈5°E) sits east of lon 0
    # dateline box still splits at ±180 correctly (regression guard)
    d2 = page.get_attribute("#gm-boxes .gm-ev[data-id=gm4] path.gm-bbox", "d")
    assert d2.count("M") == 2, d2
    assert page.errors == [], f"console errors: {page.errors}"


def test_detail_prime_meridian_field_and_box(gd_server, page_ctx):
    """Leaflet detail for the 350E→20E AR: field grid (continuous 350→380)
    and the dashed box share ONE world copy, the box span stays minimal and
    the fit shows field and box in a single view (no seam, no lost half)."""
    page = page_ctx
    _open_event(page, gd_server, "gm5")
    page.wait_for_function("() => window.__ev3 && window.__ev3.maps.length > 0", timeout=20000)
    r = page.evaluate("""() => {
        const m = window.__ev3.maps[0], b = window.__ev3.boxRect;
        let ob = null;
        m.eachLayer(l => { if (l.options && (l.options.className || '')
                .split(' ').includes('ev3-field') && l.getBounds)
            ob = l.getBounds(); });                       // the AR field imageOverlay
        const vb = m.getBounds();
        return {boxSpan: b[1][1] - b[0][1], ovW: ob && ob.getWest(), ovE: ob && ob.getEast(),
                viewW: vb.getWest(), viewE: vb.getEast()};
    }""")
    assert r["boxSpan"] < 60, r                     # 30° arc + 2×13.5° pad; a wrap form is ~330°+
    # occFit zooms to the OCCUPIED blob (A4 contract), so require: the blob
    # is fully in view, and the overlay sits on the CONTINUOUS eastward
    # window (≈349.5→380.5) sharing the view's world copy — a min/max stitch
    # or a copy-mismatched box puts the field off-screen (the Oct-3 bug).
    assert 349 <= r["ovW"] <= 351 and 379 <= r["ovE"] <= 381, r
    assert r["viewW"] <= 358.0 and r["viewE"] >= 362.0, r      # blob (lon 358–362) fully visible
    assert r["viewW"] >= r["ovW"] - 90, r                     # view is on the overlay's copy, not a jump
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs
    page.screenshot(path=os.path.join(SHOTS, "fe-pm-detail.png"))


def test_merged_box_appears_at_qualified_and_gone_after_end(gd_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gd_server)
    _mm_frame(page, "2026010200")      # one step BEFORE gf1's qualification
    ids = page.eval_on_selector_all("#gm-boxes .gm-ev", "els => els.map(e => e.dataset.id)")
    assert "gf1" not in ids, ids       # fallback-to-start would have shown it here
    _mm_frame(page, "2026010206")
    assert page.locator("#gm-boxes .gm-ev[data-id=gf1]").count() == 1
    _mm_frame(page, "2026010400")      # end is exclusive: gone at the end hour
    assert page.locator("#gm-boxes .gm-ev[data-id=gf1]").count() == 0
    _mm_frame(page, "2026010500")      # gf2 lacks qualified_at -> appears at start 00Z
    assert page.locator("#gm-boxes .gm-ev[data-id=gf2]").count() == 1
    assert page.errors == [], f"console errors: {page.errors}"


def test_merged_select_open_and_back_keeps_highlight(gd_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gd_server, "#map=2026010206")
    page.locator("#gm-boxes .gm-ev[data-id=gf1]").click()
    page.wait_for_timeout(300)
    assert "sel=gf1" in page.evaluate("decodeURIComponent(location.hash)")
    page.locator("#gm-boxes .gm-ev[data-id=gf1]").press("Enter")   # keyboard open (small box has no room for the in-box button)
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=25000)
    assert not page.locator("#global-sec").is_visible(), "merged map still live under detail"
    page.go_back()
    page.wait_for_timeout(700)
    assert page.locator("#global-sec").is_visible(), "merged map not restored on back"
    assert "06:00 UTC" in page.inner_text("#raw-readout"), "frame not restored on back"
    assert page.locator("#gm-boxes .gm-ev.sel").count() == 1, "selection not restored on back"
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs   # the synthetic event has no viz.json — a 404 is the honest state


def test_merged_gantt_bar_click_jumps_and_selects(gd_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gd_server)
    page.locator("#gm-gantt .gm-bar[data-id=gf1]").click()
    page.wait_for_timeout(300)
    assert "02 Jan 2026" in page.inner_text("#raw-readout")
    assert "06:00 UTC" in page.inner_text("#raw-readout")   # jumped to qualified step
    assert page.locator("#gm-boxes .gm-ev[data-id=gf1].sel").count() == 1
    assert "sel=gf1" in page.evaluate("decodeURIComponent(location.hash)")


def test_merged_old_links_redirect(gd_server, page_ctx):
    page = page_ctx
    page.goto(gd_server + "/#raw=2026010200&layers=h,f")
    page.wait_for_selector("#raw-readout:not(:has-text('—'))", timeout=20000)   # rewrite rides with the first render
    h = page.evaluate("decodeURIComponent(location.hash)")
    assert "map=2026010200" in h and "raw=" not in h, h
    assert "02 Jan 2026" in page.inner_text("#raw-readout")
    page.goto(gd_server + "/#global=gf2")
    page.wait_for_selector("#raw-readout:not(:has-text('—'))", timeout=20000)
    h = page.evaluate("decodeURIComponent(location.hash)")
    assert "sel=gf2" in h and "global=" not in h, h
    assert "gf2" in page.evaluate("MergedMap._dbg().sel")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs


def test_merged_missing_timeline_is_graceful(gd404_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gd404_server)
    # no timeline at all: raw layers must still render, zero boxes, no hard error
    assert page.is_visible("#raw-canvas")
    assert page.locator("#gm-boxes .gm-ev").count() == 0
    assert "every 6 h" in page.inner_text("#gm-count")
    assert page.locator("#event-table tbody tr").count() > 0, "catalog must be intact"
    errs = [e for e in page.errors if "global timeline" in e.lower()]
    assert errs == [], f"must warn, not error: {page.errors}"


def test_merged_dateline_region_label_avoids_russia(gd_server, page_ctx):
    """Orchestrator 2026-11-08 (guardian-design): a wrapped AR bbox must not
    be labeled by its raw mid-longitude (Chukotka/Russia); the unwrapped,
    land-overlap-weighted label is Alaska — 'United States of America'."""
    page = page_ctx
    _mm_goto(page, gd_server)
    page.click('#event-table tbody tr[data-id="gm4"]')   # "AR Dateline (e2e)"; rows show place names now
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=25000)
    _wait_js(page,
        "document.querySelector('#detail-title').textContent.includes('United States')",
        timeout=15000)
    title = page.inner_text("#detail-title")
    assert "Russia" not in title, title
    story = page.inner_text("#detail-story")
    assert "United States" in story, story
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs   # viz.json 404 for the synthetic id is the honest graceful state


def test_region_mask_centroid_handles_schema3_flat_mask(gd_server, page_ctx):
    """Live AR viz.json is schema 3: obs.mask is ONE flat 0/1 string (no
    obs.daily, no window). regionFromViz must chunk it by grid.lon length and
    centroid the mask cells unwrapped — the anchor-day mask of the live
    dateline AR sits in the Bering Strait, which is ocean, not Russia."""
    page = page_ctx
    _mm_goto(page, gd_server)
    got = page.evaluate("""(() => {
      const grid = { lat: [64, 65, 66], lon: [177, 178, 179, -180, -179] };
      const viz = { obs: { mask: '00111/00111/00111' }, grid };   // 3x5 flat string
      const pt = regionFromViz({}, viz);
      // the coarse band, not placeName: the page now preloads the 110m country
      // table, whose Chukotka polygon touches lon ±180 at this latitude
      return pt ? { lon: pt[1], place: regionName(pt[0], pt[1]) } : null;
    })()""")
    assert got is not None, "flat schema-3 mask must yield a mask centroid"
    assert 178.5 < got["lon"] < 179.5 or got["lon"] < -179.5, got   # unwrapped mean of 179..-179
    assert "Russia" not in got["place"], got
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs


def test_archived_week_hides_window_sections(gd_server, page_ctx):
    """Ponytail 16:15: global/raw are current-window views — they must vanish
    for archived weeks and stay hidden through an open/close detail cycle."""
    page = page_ctx
    page.goto(gd_server + "/")
    page.wait_for_selector("#event-table tbody tr")
    page.evaluate("state.week = '2025-W42'; renderAll();")
    hidden = "document.querySelector('#global-sec').classList.contains('hidden')"
    assert page.evaluate(hidden)
    page.click("#event-table tbody tr")       # detail open, then close:
    page.wait_for_selector("#detail-sec:not(.hidden)")
    page.keyboard.press("Escape")
    page.wait_for_timeout(450)                # closeDetail's 350ms restore cb
    assert page.evaluate(hidden), "closeDetail re-showed current-window views"
    page.evaluate("state.week = null; renderAll();")
    assert not page.evaluate(
        "document.querySelector('#global-sec').classList.contains('hidden')")


_HEAT_PIXEL_JS = """(() => {
        const cv = document.querySelector('#raw-canvas');
        const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        const p = document.createElement('span');
        p.style.color = getComputedStyle(document.documentElement)
            .getPropertyValue('--series-heat').trim();
        document.body.appendChild(p);
        const m = getComputedStyle(p).color.match(/\\d+/g);
        p.remove();
        const [r, g, b] = m.map(Number);
        for (let i = 0; i < d.length; i += 4)
            if (Math.abs(d[i] - r) < 40 && Math.abs(d[i + 1] - g) < 40
                && Math.abs(d[i + 2] - b) < 40) return true;
        return false; })()"""


def test_merged_slider_spans_gantt_axis(gd_server, page_ctx):
    """Slider thumb travel == gantt time axis [LAB(44), W-12] so a thumb
    position lands on the same date in the strip below (orchestrator polish
    08 Nov: full-width track, readout above, aligned scrubbing)."""
    page = page_ctx
    _mm_goto(page, gd_server)
    m = page.evaluate("""() => {
        const s = document.querySelector('#raw-slider').getBoundingClientRect();
        const g = document.querySelector('#gm-gantt').getBoundingClientRect();
        const r = document.querySelector('#raw-readout').getBoundingClientRect();
        return {thumbL: s.x + 8, thumbR: s.right - 8,
                axisL: g.x + 44, axisR: g.right - 12,
                readoutBottom: r.bottom, sliderTop: s.top, sw: s.width, gw: g.width};
    }""")
    assert abs(m["thumbL"] - m["axisL"]) <= 2, m
    assert abs(m["thumbR"] - m["axisR"]) <= 2, m
    assert m["sw"] >= 0.85 * m["gw"], m          # full-width, not a 130px stub
    assert m["readoutBottom"] <= m["sliderTop"], m  # readout sits above the track


def test_raw_renders_steps_and_badge(raw_server, page_ctx):
    page = page_ctx
    page.goto(raw_server + "/")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('Valid time')",
        timeout=20000)
    assert page.is_visible("#raw-canvas")
    ro = page.inner_text("#raw-readout")
    assert "06 Nov 2025" in ro and "12:00 UTC" in ro, ro
    # last frame is past the ERA5 cutoff: honest caption, basemap painted,
    # NO frame fetch (no "not available yet" note, no heat pixels)
    assert page.is_visible("#raw-badge")
    assert "raw layers end" in page.inner_text("#raw-badge").lower()
    assert page.locator("#raw-note.hidden").count() == 1
    assert page.evaluate("(() => { const c = document.querySelector('#raw-canvas');"
                         "return c.width > 100; })()")
    # one 6h step back: 06:00 = cutoff frame — ERA5 layers paint, badge clears
    page.click("#raw-prev")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')")
    assert not page.is_visible("#raw-badge"), "frame == cutoff is still ERA5"
    # a heat-token pixel is actually painted on the canvas (poll: the frame
    # fetch is async; tolerance covers the 84% alpha blend over the basemap)
    page.wait_for_function(_HEAT_PIXEL_JS, timeout=10000)
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs   # prefetch of the deliberately-dropped frame 0


def test_raw_layer_toggles_and_url(raw_server, page_ctx):
    """The #layers= deep-link contract after the chip row retired (complaint 3):
    the Show tabs are the only layer widget, yet URL state still drives the
    layers on load and records them on every change."""
    page = page_ctx
    page.goto(raw_server + "/")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('Valid time')",
        timeout=20000)
    assert page.locator("#raw-layers").count() == 0        # chips retired
    tabs = page.locator("#type-tabs button")
    assert tabs.count() == 4                               # All + the three types
    # deep link: frame + only-heat layers survive a load, no widget involved
    page.goto(raw_server + "/#raw=2025110606&layers=h")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')",
        timeout=20000)
    assert page.evaluate("MergedMap._dbg().on") == {"h": True, "f": False, "a": False}
    # the freeze tab narrows to freeze only and records layers=f
    tabs.filter(has_text="Freezes").click()
    page.wait_for_timeout(400)
    assert page.evaluate("MergedMap._dbg().on") == {"h": False, "f": True, "a": False}
    assert "layers=f" in page.evaluate("decodeURIComponent(location.hash)")
    # All restores every layer and drops the param again
    tabs.filter(has_text="All").click()
    page.wait_for_timeout(400)
    assert page.evaluate("MergedMap._dbg().on") == {"h": True, "f": True, "a": True}
    assert "layers" not in page.evaluate("location.hash")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs   # the frame-0 prefetch 404 is the fixture's doing


def test_raw_missing_frame_graceful(raw_server, page_ctx):
    page = page_ctx
    page.goto(raw_server + "/#raw=2025110600")   # index lists it; .bin dropped
    page.wait_for_selector("#raw-note:not(.hidden)", timeout=20000)
    note = page.inner_text("#raw-note")
    assert "not available" in note, note
    assert page.is_visible("#raw-canvas"), "basemap must stay painted, not blank"
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_raw_play_and_scrub(raw_server, page_ctx):
    """Design QA ask: playback advances on its own, wraps from the end, and
    auto-stops at the last frame; slider scrub lands the exact frame."""
    page = page_ctx
    page.goto(raw_server + "/")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('12:00 UTC')",
        timeout=20000)
    # slider scrub: jump to the first frame
    page.evaluate("""(() => { const s = document.querySelector('#raw-slider');
        s.value = '0'; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('00:00 UTC')")
    # play from the start: crosses 06:00, then auto-stops back at 12:00
    page.click("#raw-play")
    assert page.inner_text("#raw-play") == "⏸ pause"
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')",
        timeout=5000)
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('12:00 UTC')"
        " && document.querySelector('#raw-play').textContent === '▶ play'",
        timeout=5000)
    # play pressed at the END wraps to frame 0 before advancing (checked the
    # readout flips off 12:00 within one 5fps tick)
    page.click("#raw-play")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('00:00 UTC')",
        timeout=3000)
    page.click("#raw-play")   # pause mid-roll, leave it deterministic
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs


def test_raw_section_honest_without_data(live_url, page_ctx):
    """Public site pre-asset: the section must self-report, never hang or
    throw. Once the weekly build lands the asset, the canvas path runs and
    the note stays hidden — both states pass."""
    page = page_ctx
    page.goto(live_url + "/")
    page.wait_for_function(
        """() => { const n = document.querySelector('#raw-note');
            if (!n.classList.contains('hidden')) return true;
            return !document.querySelector('#raw-readout').textContent.includes('\\u2014'); }""",
        timeout=20000)
    if page.is_visible("#raw-canvas"):
        assert page.locator("#raw-note.hidden").count() == 1
    else:
        assert "not built" in page.inner_text("#raw-note")
    errs = [e for e in page.errors
            if "Failed to load resource" not in e and "raw exceedance" not in e.lower()]
    assert errs == [], f"unexpected page errors: {errs}"


# ---------------------------------------------------------------------------
# AR ramp (atmospheric river IVT class plane tests)
# ---------------------------------------------------------------------------

# AR ramp bins paint with the lane's constant RUN_ALPHA over the basemap
# (owner review 2026-11-11: opaque sheets hid the graticule/coasts), so a
# painted pixel is token RGB blended over --map-ocean or --paper (land).
# AR fixture truth: frame 2025110606 (k=1) has the AR blob at row j=28, cols
# i=46..50 carrying classes 1..5; 2025110612 is PAST the fixture cutoff, so
# ramp tests must drive 2025110606.
_AR_CENSUS_JS = """() => {
    const d = MergedMap._dbg();
    const bins = [0, 1, 2, 3, 4].map((b) => d.ramp(2, b));
    if (!bins.every(Boolean)) return { arPixels: 0, classes: [] };
    const varRgb = (v) => { const s = document.createElement('span'); s.style.color = v;
        document.body.appendChild(s); const m = getComputedStyle(s).color.match(/\\d+/g);
        s.remove(); return m.map(Number); };
    const grounds = [varRgb('var(--map-ocean)'), varRgb('var(--paper)')];
    const eff = (c) => { const t = c[3] / 255;   // the alpha the pack ACTUALLY paints with
        return grounds.map((g) => c.slice(0, 3).map((v, k) => v * t + g[k] * (1 - t))); };
    const targets = bins.map(eff);
    const cv = document.querySelector('#raw-canvas');
    const px = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    let arPixels = 0; const classes = new Set();
    for (let i = 0; i < px.length; i += 4) {
        if (px[i + 3] === 0) continue;
        const p = [px[i], px[i + 1], px[i + 2]];
        for (let b = 0; b < 5; b++) {
            if (targets[b].some((col) => col.every((v, k) => Math.abs(v - p[k]) <= 12))) {
                arPixels++; classes.add(b + 1); break;
            }
        }
    }
    return { arPixels, classes: [...classes] };
}"""


def _ar_ramp_wait(page, base, hash_):
    page.goto(base + "/" + hash_)
    _wait_js(page, "MergedMap._dbg().mounted === true", timeout=20000)
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('Valid time')",
        timeout=20000)
    page.locator("#raw-canvas").scroll_into_view_if_needed()
    page.wait_for_timeout(400)   # frame promise resolves + canvas painted


def test_ar_ramp_paints_class_plane(ar_ramp_server, page_ctx):
    """2026-10-03-style check on the fixture: AR pixels > 0 and >= 2 distinct
    ramp classes on the canvas; the fetched class plane decodes cell-true."""
    page = page_ctx
    _ar_ramp_wait(page, ar_ramp_server, "#map=2025110606&layers=a")
    # sidecar fetch + decode matches the fixture truth (classes 1..5 at j=28)
    assert page.evaluate(
        "[...Array(5)].map((_, d) => MergedMap._dbg().ivtAt(28, 46 + d))") == [1, 2, 3, 4, 5]
    census = page.evaluate(_AR_CENSUS_JS)
    assert census["arPixels"] > 0, census
    assert len(census["classes"]) >= 2, census
    # hover over the class-3 cell (lon 60E, lat 50N) shows its IVT bin
    box = page.evaluate("""() => {
        const d = MergedMap._dbg(), g = d.grid;
        const p = d.project([g.lon0 + 48 * g.step_lon, g.lat0 + 28 * g.step_lat]);
        const r = document.querySelector('#raw-canvas').getBoundingClientRect();
        return { x: r.left + p[0], y: r.top + p[1] }; }""")
    page.mouse.move(box["x"] - 60, box["y"])   # start off-cell: no stale tooltip
    page.mouse.move(box["x"], box["y"])
    page.wait_for_selector("#tooltip:not(.hidden)", timeout=3000)
    assert "750\u20131000" in page.inner_text("#tooltip")
    page.mouse.move(5, 5)   # pointerleave: tooltip must clear
    _wait_js(page, "document.querySelector('#tooltip').classList.contains('hidden')",
                           timeout=3000)


@pytest.fixture(scope="module")
def ar_s2_server(tmp_path_factory):
    """Schema-2 `.ivt` (the live one): the class plane covers the WHOLE IVT field.
    Adds a class-3, IVT>=400 cell at (j=20, i=10) that is NOT in the AR mask —
    the tropics' moisture the map must never draw (owner 2026-11-11)."""
    import zlib
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-ar-s2")),
                             with_timeline=False, raw_ivt=True)
    rd = os.path.join(root, "data", "results", "raw_exceedance")
    ix = json.load(open(os.path.join(rd, "index.json")))
    ix["ivt"] = {"schema": 2, "bins": [250, 500, 750, 1000, 1250],
                 "ar_min": 400.0, "above_bit": 128}
    json.dump(ix, open(os.path.join(rd, "index.json"), "w"))
    nx = ix["grid"]["nx"]
    for ts in ix["frames"]:
        fp = os.path.join(rd, ts + ".ivt")
        if not os.path.exists(fp):
            continue
        pl = bytearray(zlib.decompress(open(fp, "rb").read()))
        pl[20 * nx + 10] = 3 | 128                      # outside the mask
        open(fp, "wb").write(zlib.compress(bytes(pl)))
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


def test_ivt_outside_the_ar_mask_is_neither_painted_nor_hoverable(ar_s2_server, page_ctx):
    """Tropics bug: schema-2 classes exist outside the mask, and the map used to
    tint every IVT>=400 cell and answer hovers on it. Only AR-mask cells show."""
    page = page_ctx
    _ar_ramp_wait(page, ar_s2_server, "#map=2025110606&layers=a")
    assert page.evaluate("MergedMap._dbg().ivtAt(20, 10)") == 3      # the field is there
    assert page.evaluate("MergedMap._dbg().arAt(20, 10)") == 0        # the mask is not
    spot = lambda j, i: page.evaluate("""([j, i]) => {
        const d = MergedMap._dbg(), g = d.grid;
        const p = d.project([g.lon0 + i * g.step_lon, g.lat0 + j * g.step_lat]);
        const cv = document.querySelector('#raw-canvas');
        const k = cv.width / cv.getBoundingClientRect().width;
        const px = cv.getContext('2d').getImageData(Math.round(p[0] * k), Math.round(p[1] * k), 1, 1).data;
        const r = cv.getBoundingClientRect();
        return { rgba: [...px], x: r.left + p[0], y: r.top + p[1] }; }""", [j, i])
    a, ref = spot(20, 10), spot(20, 14)       # (20,14): no IVT, no AR — same basemap
    # no faint tint on the IVT-only cell: basemap shading differs by ~1 between two
    # neighbouring pixels, the old alpha-55 tint moved the channels by tens
    assert max(abs(x - y) for x, y in zip(a["rgba"], ref["rgba"])) <= 4, (a, ref)
    page.mouse.move(a["x"] - 60, a["y"])
    page.mouse.move(a["x"], a["y"])
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelector('#tooltip').classList.contains('hidden')")


def test_ar_ramp_legend_shows_5_steps(ar_ramp_server, page_ctx):
    """Legend: the AR group steps 250-500 … >=1250 kg m-1 s-1 (5 labelled
    swatches); layers the URL switched off never appear in it."""
    page = page_ctx
    _ar_ramp_wait(page, ar_ramp_server, "#map=2025110606&layers=a")
    page.wait_for_selector("#gm-legend:not(.hidden)", timeout=20000)
    assert page.locator("#gm-legend .lg-group").count() == 1      # AR only: layers=a
    assert page.locator("#gm-legend .lg-group").get_attribute("data-layer") == "a"
    sw = page.locator("#gm-legend .swatch")
    assert sw.count() == 5
    titles = [sw.nth(i).get_attribute("title") for i in range(5)]
    assert [t.split()[0] for t in titles] == ["250\u2013500", "500\u2013750",
        "750\u20131000", "1000\u20131250", "\u22651250"], titles
    txt = page.inner_text("#gm-legend")
    assert "AR IVT" in txt and "kg m\u207b\u00b9 s\u207b\u00b9" in txt


def test_ar_ramp_fallback_without_class_plane(ar_ramp_server, page_ctx):
    """Class plane 404 -> ARs still paint via the binary fallback (the
    AR-outage bug class: never let the fallback depend on the sidecar)."""
    page = page_ctx
    page.route("**/*.ivt", lambda r: r.fulfill(
        status=404, content_type="application/octet-stream", body=""))
    _ar_ramp_wait(page, ar_ramp_server, "#map=2025110606&layers=a")
    assert page.evaluate("MergedMap._dbg().ivtAt(28, 46)") == 0   # no plane
    n = page.evaluate("""() => {
        const d = MergedMap._dbg(), c = d.packed(2), t = c[3] / 255;
        const varRgb = (v) => { const s = document.createElement('span'); s.style.color = v;
            document.body.appendChild(s); const m = getComputedStyle(s).color.match(/\\d+/g);
            s.remove(); return m.map(Number); };
        const grounds = [varRgb('var(--map-ocean)'), varRgb('var(--paper)')]
            .map((g) => c.slice(0, 3).map((v, k) => v * t + g[k] * (1 - t)));
        const cv = document.querySelector('#raw-canvas');
        const px = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        let n = 0;
        for (let i = 0; i < px.length; i += 4) {
            if (grounds.some((col) => col.every((v, k) => Math.abs(v - px[i + k]) <= 12))) n++;
        }
        return n; }""")
    assert n > 0, "binary AR pixels missing while the .ivt sidecar is 404"
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs


# ---------------------------------------------------------------------------
# consecutive-day duration layers (channel_consecutive_days acceptance 5-8)
# ---------------------------------------------------------------------------

# Reads the main canvas at heat cells (dur-rev-v2 paint contract, heat ramp
# 1,2,…,7+ days): run=2 (bin 2), run=3 (bin 3), run=0 (unpainted), run=14
# (top bin, 7+). The effective painted colour is computed
# from the app's own ramp pack (colour+alpha) blended over --map-ocean.
_BIN_PROBE_JS = """(({ j, i }) => {
    const d = MergedMap._dbg(), g = d.grid;
    const cv = document.querySelector('#raw-canvas');
    const px = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    const varRgb = (v) => { const s = document.createElement('span'); s.style.color = v;
        document.body.appendChild(s); const m = getComputedStyle(s).color.match(/\\d+/g);
        s.remove(); return m.map(Number); };
    const ground = varRgb('var(--map-ocean)');
    const eff = (bin) => { const c = d.ramp(0, bin), t = c[3] / 255;
        return c.slice(0, 3).map((v, k) => v * t + ground[k] * (1 - t)); };
    const win = (ci, dx) => {
        const p = d.project([g.lon0 + ci * g.step_lon, g.lat0 + j * g.step_lat]);
        const x = Math.round(p[0] * d.dpr) + dx, y = Math.round(p[1] * d.dpr);
        // Use 3x3 window instead of 7x7 to avoid overlap between adjacent cells
        const out = [];
        for (let dy = -1; dy <= 1; dy++) for (let ddx = -1; ddx <= 1; ddx++) {
            const k = 4 * ((y + dy) * cv.width + x + ddx);
            out.push([px[k], px[k + 1], px[k + 2]]); }
        return out; };
    const near = (a, b) => a.every((v, k) => Math.abs(v - b[k]) <= 35);
    const count = (cells, col) => cells.reduce((s, c) =>
        s + c.filter((p) => near(p, col)).length, 0);
    const allBins = [0, 1, 2, 3, 4, 5, 6].map(eff);
    const isNearAnyBin = (p) => allBins.some(b => near(p, b));
    return { run3: count([win(i + 1, 0)], eff(2)),    // run=3 -> 3-day bin
             run14: count([win(i + 40, 0)], eff(6)),  // run=14 -> top bin (7+)
             run2: count([win(i, 5)], eff(1)),        // run=2 -> 2-day bin
             distinct: !near(eff(0), eff(6)),
             unpaintedRun0: win(i + 20, 5).every((p) => !isNearAnyBin(p)) }; // run=0
})"""


def test_dur_ramp_bins_painted_and_zero_unpainted(dur_server, page_ctx):
    """Acceptance 5 (dur rev v2 contract): with `.run` sidecars the heat layer
    paints every run >= 1 on the 7-step ramp (run=2, run=3, run=14 -> top bin)
    and cells with run=0 stay unpainted."""
    page = page_ctx
    _mm_goto(page, dur_server)
    _mm_frame(page, RAW_TS[1])               # frame k=1: heat blob row j=19, cols 30/31/50/70
    got = page.evaluate(_BIN_PROBE_JS, {"j": 19, "i": 30})
    assert got["run3"] >= 1 and got["run14"] >= 1 and got["run2"] >= 1, got
    assert got["distinct"] is True, f"ramp ends must differ: {got}"
    assert got["unpaintedRun0"] is True, f"run=0 should be unpainted: {got}"
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"   # 404 = the fixture's absent timeline


def test_dur_fallback_binary_without_run(raw_server, page_ctx):
    """Acceptance 6: a lane whose index has no `dur` (no .run files at all)
    still paints the binary heat layer, with no console errors (no .run
    fetches are even attempted)."""
    page = page_ctx
    page.goto(raw_server + "/#raw=2025110606&layers=h")
    _wait_js(page,
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')",
        timeout=20000)
    page.wait_for_function(_HEAT_PIXEL_JS, timeout=10000)
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_show_tabs_are_the_only_layer_toggle(dur_server, page_ctx):
    """Complaint 3 (2026-11-11): the #raw-layers chip row retired — the Show
    tabs are the one type toggle. The legend strip carries the lane keys
    (heat stops start at the detector gate) and the long dur-rev-v2 tips the
    chips used to hold; a tab click narrows layers + legend and records
    #layers= for deep links."""
    page = page_ctx
    _mm_goto(page, dur_server)
    assert page.locator("#raw-layers").count() == 0
    assert page.locator(".map-legend").count() == 0
    tabs = page.locator("#type-tabs button")
    assert tabs.count() == 4                                  # All + the three types
    labels = " ".join(tabs.all_inner_texts())
    assert "Heat waves" in labels and "Freezes" in labels and "Atmospheric rivers" in labels
    page.wait_for_selector("#gm-legend .lg-group", timeout=20000)
    assert page.eval_on_selector_all("#gm-legend .lg-group",
                                     "els => els.map(e => e.dataset.layer)") == ["h", "f", "a"]
    assert page.eval_on_selector_all(
        "#gm-legend .lg-group[data-layer=h] .lg-t", "e => e.map(x => x.textContent)") \
        == ["3", "4", "5", "6", "7+"]
    tips = page.eval_on_selector_all("#gm-legend .lg-group", "els => els.map(e => e.title)")
    assert "COMPLETED UTC days" in tips[0] and "7+ days is the darkest stop in light" in tips[0]
    assert "P15" in tips[1] and "3+ days is the darkest stop in light" in tips[1]
    # the tab narrows the map layers and the legend, and records #layers=
    page.locator("#type-tabs button", has_text="Atmospheric rivers").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#gm-legend .lg-group').length === 1")
    assert page.locator("#gm-legend .lg-group[data-layer=a]").count() == 1
    assert "layers=a" in page.evaluate("decodeURIComponent(location.hash)")
    page.locator("#type-tabs button", has_text="All").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#gm-legend .lg-group').length === 3")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"   # 404 = the fixture's absent timeline


def test_legend_sits_below_the_map_and_never_moves_the_page(dur_server, page_ctx):
    """New contract (2026-11-11 redesign): the colour scales are an in-flow
    strip BELOW the map — outside #raw-map-wrap, never covering the canvas.
    Its height is fixed per layout, so content changes (a lane off through
    the tabs, a Region view) never move what sits below the strip: the map
    box, the slider and the gantt keep their exact rectangles. (The event
    list and the scorecard DO reflow on a tab change — that is the tabs'
    own page-wide filter contract, not the strip's layout.)"""
    page = page_ctx
    _mm_goto(page, dur_server)
    _mm_frame(page, RAW_TS[1])
    page.wait_for_selector("#gm-legend .lg-group", timeout=20000)
    geo = """() => {
        const r = (s) => { const b = document.querySelector(s).getBoundingClientRect();
            return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
        const map = r('#raw-map-wrap'), leg = r('#gm-legend');
        return { map, leg,
                 below: ['#raw-slider', '#gm-gantt'].map(r),
                 groups: document.querySelectorAll('#gm-legend .lg-group').length,
                 inMap: !!document.querySelector('#raw-map-wrap #gm-legend'),
                 belowMap: leg[1] >= map[1] + map[3] }; }"""
    a = page.evaluate(geo)
    assert not a["inMap"], "the strip must live outside #raw-map-wrap"
    assert a["belowMap"] and a["groups"] == 3, a          # sits under the map, not on it
    page.locator("#type-tabs button", has_text="Atmospheric rivers").click()   # 3 -> 1 lane
    page.wait_for_function("() => document.querySelectorAll('#gm-legend .lg-group').length === 1")
    b = page.evaluate(geo)
    assert b["groups"] == 1 and b["leg"][3] == a["leg"][3], (a, b)   # height is fixed
    assert b["below"] == a["below"] and b["map"] == a["map"], (a, b)  # nothing moved
    page.locator("#type-tabs button", has_text="All").click()
    page.wait_for_function("() => document.querySelectorAll('#gm-legend .lg-group').length === 3")
    page.evaluate("MergedMap.setRegion({key:'europe', label:'Europe', lat:[35,75], lon:[-12.5,42.5]})")
    page.wait_for_function("() => document.querySelectorAll('#gm-legend .lg-group.none').length === 3")
    c = page.evaluate(geo)
    assert c["leg"] == a["leg"] and c["map"] == a["map"] and c["below"] == a["below"], (a, c)
    page.evaluate("MergedMap.setRegion(null)")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_legend_never_hides_a_lane_behind_a_scroll(dur_server, page_ctx):
    """Strip responsiveness contract (owner review 2026-11-11): from 320 up
    the strip NEVER scrolls horizontally and every ON lane is fully inside
    it — a lane behind a scrollbar nobody discovers is as good as deleted.
    Narrow map columns stack the lanes one per row; wide columns lay them in
    a single row (container query, so it follows the map column, not the
    viewport)."""
    page = page_ctx
    _mm_goto(page, dur_server)
    _mm_frame(page, RAW_TS[1])
    page.wait_for_selector("#gm-legend .lg-group", timeout=20000)
    probe = """() => {
        const leg = document.querySelector('#gm-legend');
        const lb = leg.getBoundingClientRect();
        const gs = [...document.querySelectorAll('.lg-group')].map((g) => g.getBoundingClientRect());
        const rows = new Set(gs.map((r) => Math.round(r.y))).size;
        return { fits: leg.scrollWidth <= leg.clientWidth + 1,
                 inside: gs.every((r) => r.right <= lb.right + 1 && r.left >= lb.left - 1),
                 rows, lanes: gs.length,
                 dir: getComputedStyle(leg).flexDirection }; }"""
    for w in (320, 390, 768, 1100, 1440):
        page.set_viewport_size({"width": w, "height": 900})
        page.wait_for_timeout(600)          # ResizeObserver refit + legend reflow
        p = page.evaluate(probe)
        assert p["fits"] and p["inside"], (w, p)
        assert p["lanes"] == 3, (w, p)
        if p["dir"] == "column":
            assert p["rows"] == 3, (w, p)   # stacked: one lane per row
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_legend_adapts_to_layers_frame_and_region(dur_server, page_ctx):
    """The legend describes the map as it stands: one group per layer that is
    ON, heat/freeze labelled in consecutive days (heat starting at the gate),
    AR as a single flat stop while the IVT class sidecar is missing, stops the
    current frame does not paint dimmed, and an empty lane marked for the
    Region view."""
    page = page_ctx
    _mm_goto(page, dur_server)
    _mm_frame(page, RAW_TS[1])        # heat run 3 + 7+, freeze run 1 + 3+, AR mask north
    page.wait_for_selector("#gm-legend .lg-group", timeout=20000)
    groups = page.locator("#gm-legend .lg-group")
    assert groups.count() == 3
    assert page.eval_on_selector_all("#gm-legend .lg-group",
                                     "els => els.map(e => e.dataset.layer)") == ["h", "f", "a"]
    # stops per lane: heat 3,4,5,6,7+ (gate = detector minimum 3) / freeze 1,2,3+ / AR flat
    assert page.eval_on_selector_all(
        "#gm-legend .lg-group[data-layer=h] .lg-t", "e => e.map(x => x.textContent)") \
        == ["3", "4", "5", "6", "7+"]
    assert page.eval_on_selector_all(
        "#gm-legend .lg-group[data-layer=f] .lg-t", "e => e.map(x => x.textContent)") \
        == ["1", "2", "3+"]
    assert page.eval_on_selector_all(
        "#gm-legend .lg-group[data-layer=a] .lg-t", "e => e.map(x => x.textContent)") \
        == ["AR mask"]
    # this frame paints heat only at 3 and 7+ days, freeze only at 1 and 3+
    assert page.locator("#gm-legend .lg-group[data-layer=h] .lg-bin.out").count() == 3
    assert page.locator("#gm-legend .lg-group[data-layer=f] .lg-bin.out").count() == 1
    assert page.locator("#gm-legend .lg-group[data-layer=a] .lg-bin.out").count() == 0
    # a Show tab narrows to one lane -> the other groups leave the legend
    # (and never dim to nothing)
    page.locator("#type-tabs button", has_text="Freezes").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#gm-legend .lg-group').length === 1")
    assert page.locator("#gm-legend .lg-group[data-layer=f]").count() == 1
    assert page.locator("#gm-legend .lg-group[data-layer=h]").count() == 0
    page.locator("#type-tabs button", has_text="All").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#gm-legend .lg-group').length === 3")
    # Region view: the veil hides the northern lanes -> both are marked empty
    page.evaluate("MergedMap.setRegion({key:'southern_hemisphere',"
                 " label:'Southern Hemisphere', lat:[-90,-20], lon:[-180,180]})")
    page.wait_for_function(
        "() => document.querySelectorAll('#gm-legend .lg-group.none').length === 2")
    assert "none in view" in page.inner_text("#gm-legend .lg-group[data-layer=h]")
    # freeze still has cells south -> its group stays live
    assert page.locator("#gm-legend .lg-group[data-layer=f].none").count() == 0
    page.evaluate("MergedMap.setRegion(null)")
    page.wait_for_function(
        "() => !document.querySelector('#gm-legend .lg-group.none')")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_legend_provisional_stop_only_where_the_frame_dithers(dur2_server, page_ctx):
    """dur rev v2: a provisional cell dithers toward the NEXT shade, so that
    shade and a 'today' stop join the legend for the lane that has them — and
    only for that lane."""
    page = page_ctx
    _mm_goto(page, dur2_server)
    _mm_frame(page, RAW_TS[1])
    page.wait_for_selector("#gm-legend .lg-group[data-layer=h] .swatch.prov", timeout=20000)
    assert page.eval_on_selector_all("#gm-legend .lg-group[data-layer=h] .lg-t",
                                     "e => e.map(x => x.textContent)") \
        == ["3", "4", "5", "6", "7+", "today"]
    # the dither paints the 4-day shade, so '4' is live; 5 and 6 are not
    assert page.locator("#gm-legend .lg-group[data-layer=h] .lg-bin.out").count() == 2
    assert page.locator("#gm-legend .lg-group[data-layer=h] .lg-bin.out .lg-t").all_inner_texts() \
        == ["5", "6"]
    # freeze has no provisional cell in this frame -> no 'today' stop at all
    assert page.locator("#gm-legend .lg-group[data-layer=f] .swatch.prov").count() == 0
    assert "today" not in page.inner_text("#gm-legend .lg-group[data-layer=f]")
    assert "provisional" in page.get_attribute(
        "#gm-legend .lg-group[data-layer=h] .swatch.prov", "title")
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], f"unexpected page errors: {errs}"


def test_merged_event_tooltip_shows_span_days(gd_server, page_ctx):
    """Acceptance 8: the box and gantt tooltips carry the event length
    '· N days' (calendar span, end exclusive) — gm4 spans 02→05 Jan = 3 days."""
    page = page_ctx
    _mm_goto(page, gd_server)
    _mm_frame(page, "2026010206")
    box = page.evaluate("document.querySelector('#gm-boxes .gm-ev[data-id=gm4] title').textContent")
    bar = page.evaluate("document.querySelector('#gm-gantt .gm-bar[data-id=gm4] title').textContent")
    assert "· 3 days" in box, box
    assert "· 3 days" in bar, bar
    assert page.errors == [], f"console errors: {page.errors}"
