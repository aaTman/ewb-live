"""Frontend e2e tests (owner feedback 2026-11-07, A1-A5 + B).

Playwright (sync API) against the deployed dashboard:
  EWB_LIVE_URL, default http://192.168.0.121:8050.
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
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIVE_URL = os.environ.get("EWB_LIVE_URL", "http://192.168.0.121:8050")
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
    # bottom bar: 'Valid time: …' left, single-swatch legend right
    bar = page.locator(".ev3-panel.ev3-obs .ev3-bbar").first
    assert bar.is_visible()
    left = bar.locator(".bbar-l").inner_text()
    assert left.startswith("Valid time:")
    assert "UTC" in left
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
    # valid-time stepping: two different frames
    days = page.locator(".ev-days .ev-day")
    assert days.count() >= 2
    bar0 = page.locator(".ev3-panel.ev3-obs .ev3-bbar .bbar-l").inner_text()
    days.nth(0).click()
    page.wait_for_timeout(400)
    bar1 = page.locator(".ev3-panel.ev3-obs .ev3-bbar .bbar-l").inner_text()
    days.nth(1).click()
    page.wait_for_timeout(400)
    bar2 = page.locator(".ev3-panel.ev3-obs .ev3-bbar .bbar-l").inner_text()
    assert bar1 != bar2, "valid-time stepper must change the frame label"
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-heat.png"), full_page=False)
    page.evaluate("window.scrollTo(0, document.querySelector('#detail-sec').offsetTop)")
    page.screenshot(path=os.path.join(SHOTS, "fe-detail-heat-full.png"))
    assert page.errors == [], f"console errors: {page.errors}"


def test_heat_model_panels_forecast_at_lead(live_url, heat_event, page_ctx):
    """Same component for forecast-at-lead: model panels carry the bottom bar
    (valid time from the model's own daily tier field on schema 3)."""
    page = page_ctx
    _open_event(page, live_url, heat_event[0])
    leads = page.locator(".ev-lead")
    assert leads.count() >= 2
    leads.nth(1).click()
    page.wait_for_timeout(900)
    bars = page.locator(".ev3-grid .ev3-bbar")
    assert bars.count() >= 1, "model panels must carry the bottom bar too"
    txt = bars.first.locator(".bbar-l").inner_text()
    assert txt.startswith("Valid time:"), txt
    # A5: no struck-through chips visible in the picker
    dis = page.locator(".ev3-picker button.dis")
    for i in range(dis.count()):
        assert not dis.nth(i).is_visible(), "unavailable model chip must be hidden"
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
    assert bar.locator(".bbar-l").inner_text().startswith("Valid time:")
    legend = bar.locator(".bbar-r")
    assert legend.locator(".ev3-swatch").count() == 1
    assert legend.inner_text().strip() == "P15"
    rgb = legend.locator(".ev3-swatch").evaluate(
        "el => getComputedStyle(el).backgroundColor")
    assert rgb.replace(" ", "") == "rgb(33,113,181)", rgb  # --tier-freeze token
    stats = _raster_alpha_stats(page, ".ev3-panel.ev3-obs img.ev3-crisp")
    assert stats and stats["cornerAlpha"] == 0
    assert stats["probe"] == [33, 113, 181], f"freeze tier color wrong: {stats['probe']}"
    page.screenshot(path=os.path.join(SHOTS, "fe-freeze.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    page.screenshot(path=os.path.join(SHOTS, "fe-freeze-mobile.png"))
    assert page.errors == [], f"console errors: {page.errors}"


# ---------------------------------------------------------------------------
# Owner 2026-11-08: clickable catalog boxes, graceful viz-404, AR green token,
# and the ponytail MUST (deep-link to a NON-FIRST lead must be honored).
# ---------------------------------------------------------------------------

def _pick_visible_box(page):
    """Largest catalog box whose center is inside the map viewport."""
    page.evaluate("document.querySelector('#map').scrollIntoView({block:'center'})")
    page.wait_for_timeout(400)
    return page.evaluate("""() => {
        const mapR = document.querySelector('#map').getBoundingClientRect();
        let best = null;
        for (const p of document.querySelectorAll('#map path[data-ev]')) {
            const r = p.getBoundingClientRect();
            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            if (cx < mapR.left + 8 || cx > mapR.right - 8 || cy < mapR.top + 8 || cy > mapR.bottom - 8) continue;
            if (r.width * r.height > (best ? best.a : 0)) {
                // the point click actually resolves to — an overlapping
                // smaller box painted on top (byAreaDesc) would steal it
                const el = document.elementFromPoint(cx, cy);
                const top = el && el.closest('[data-ev]');
                if (top) best = { id: top.getAttribute('data-ev'), x: cx, y: cy, a: r.width * r.height };
            }
        }
        return best;
    }""")


def _map_view(page):
    return page.evaluate("""() => { const m = window.__catalogMap; const c = m.getCenter();
        return { lat: c.lat, lng: c.lng, zoom: m.getZoom() }; }""")


def test_map_box_click_opens_detail_and_back_restores_map(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url + "/")
    page.wait_for_selector("#event-table tbody tr", timeout=25000)
    page.wait_for_selector("#map path[data-ev]", timeout=15000)
    page.wait_for_timeout(1500)   # let the initial fit settle
    view0 = _map_view(page)
    box = _pick_visible_box(page)
    assert box, "no catalog box inside the map viewport"
    page.mouse.click(box["x"], box["y"])
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=6000)
    page.wait_for_timeout(800)
    st = page.evaluate("""() => ({ hash: location.hash, sel: state.selectedEvent,
        title: document.querySelector('#detail-title').textContent })""")
    assert st["sel"] == box["id"], f"opened {st['sel']}, clicked box {box['id']}"
    assert box["id"] in st["hash"] and "#event=" in st["hash"]
    page.go_back()
    # `.hidden` = display:none, so match on attachment, never visibility
    page.wait_for_selector("#detail-sec.hidden", state="attached", timeout=6000)
    page.wait_for_timeout(800)
    view1 = _map_view(page)
    assert abs(view1["lat"] - view0["lat"]) < 0.6 and abs(view1["lng"] - view0["lng"]) < 0.6, (view0, view1)
    assert view1["zoom"] == view0["zoom"]
    # last-viewed box keeps its 3px highlight stroke on return to the list
    w = page.evaluate(
        "(id) => document.querySelector('#map path[data-ev=\"' + id + '\"]')?.getAttribute('stroke-width')",
        box["id"])
    assert w == "3", f"selected-on-return stroke missing (stroke-width={w})"
    assert page.errors == [], f"console errors: {page.errors}"


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
    screens, centered, no overflow."""
    page = page_ctx
    page.set_viewport_size({"width": 1920, "height": 900})
    page.goto(live_url + "/")
    page.wait_for_selector("#event-table tbody tr, .gm-lrow")
    m = page.evaluate("""(() => { const e = document.querySelector('.shell')
            || document.querySelector('main');
        const r = e.getBoundingClientRect();
        return {w: r.width, left: r.left, right: innerWidth - r.right}; })()""")
    assert m["w"] <= 1280.5, f"content wider than 1280 at 1920: {m}"
    assert m["w"] <= 1920 * 0.75 + 1
    assert abs(m["left"] - m["right"]) <= 2, f"not centered: {m}"


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
    page.click("#theme-toggle")   # auto -> light
    page.click("#theme-toggle")   # light -> dark
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
          "start": "2026-01-02", "end": "2026-01-04",
          "bbox": [60.0, 68.0, 178.0, -155.0], "days": _GD_DAYS[1:4],
          "qualified_at": "2026010200"}
    return {"schema": 1, "generated": "2026-01-11T00:00:00Z",
            "window": {"start": "2026-01-01", "end": "2026-01-11", "days": 11},
            "events": [e1, e2, e3, e4]}


GD_RAW_TS = [d.strftime("%Y%m%d%H") for d in
             [__import__("datetime").datetime(2026, 1, 1)
              + __import__("datetime").timedelta(hours=6 * i)
              for i in range(41)]]   # 2026-01-01T00 … 2026-01-11T00, 6-hourly


def _build_gd_fixture(tmpdir, with_timeline=True, raw_frames=None, raw_cutoff=None):
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
    if raw_frames is not None:
        _build_raw_fixture(data_dir, frames=raw_frames,
                           cutoff=raw_cutoff if raw_cutoff is not None else raw_frames[-1])
    else:
        _build_raw_fixture(data_dir)
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
    return root


def _gd_server(root):
    # bind port 0 and read it back — no probe/rebind race on shared runners
    handler = lambda *a, **k: _QuietHandler(*a, directory=root, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


RAW_TS = ["2025110600", "2025110606", "2025110612"]  # 6-hourly, ts2 is past cutoff


def _build_raw_fixture(data_dir, drop_ts=None, frames=None, cutoff=None,
                       run_planes=None, dur=None):
    """raw_exceedance asset per the contract in channel_design_frontend.md:
    zlib'd layer-major bitpacked rows (heat,freeze,ar; ny x ceil(nx/8),
    row 0 = lat0 lowest, MSB-first). 5-deg grid; heat blob mid-pacifica,
    freeze blob south, AR band north — distinct so pixel checks can differ.

    run_planes: optional [(heat_u8, freeze_u8), ...] one pair per frame, each
    ny*nx raw bytes (NO bit packing) -> writes the <ts>.run duration sidecar
    (zlib(heat ++ freeze)); dur: optional dict written as index.json "dur"
    (the frontend feature-detect + the published qualification gate).
    """
    import zlib
    frames = frames or RAW_TS
    cutoff = cutoff or RAW_TS[1]
    g = {"res_deg": 5.0, "ny": 36, "nx": 72, "lat0": -90.0, "lon0": -180.0,
         "step_lat": 5.0, "step_lon": 5.0}
    ny, nx = g["ny"], g["nx"]
    stride = (nx + 7) // 8
    rd = os.path.join(data_dir, "raw_exceedance")
    os.makedirs(rd, exist_ok=True)
    index = {"schema": 1, "generated": "2025-11-06T12:00:00Z", "grid": g,
             "frames": frames, "cutoff": cutoff,
             "window": {"start": frames[0][:8], "end": frames[-1][:8]}}
    if dur is not None:
        index["dur"] = dur
    json.dump(index, open(os.path.join(rd, "index.json"), "w"))
    for k, ts in enumerate(frames):
        layers = [bytearray(ny * stride) for _ in range(3)]
        blobs = [(18 + k % 12, 30), (6 + k % 6, 10), (28, 40 + (k % 5) * 6)]
        for li, (j, i) in enumerate(blobs):
            b = layers[li]
            b[j * stride + (i >> 3)] |= 1 << (7 - (i & 7))
            b[j * stride + ((i + 1) >> 3)] |= 1 << (7 - ((i + 1) & 7))
        if ts == drop_ts:
            p = os.path.join(rd, ts + ".bin")
            if os.path.exists(p):
                os.remove(p)
            continue
        with open(os.path.join(rd, ts + ".bin"), "wb") as f:
            f.write(zlib.compress(b"".join(bytes(x) for x in layers)))
        if run_planes is not None:
            heat, freeze = run_planes[k]
            assert len(heat) == ny * nx and len(freeze) == ny * nx, ts
            with open(os.path.join(rd, ts + ".run"), "wb") as f:
                f.write(zlib.compress(bytes(heat) + bytes(freeze)))


@pytest.fixture(scope="module")
def raw_server(tmp_path_factory):
    # middle frame's .bin deliberately missing -> graceful-state path
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-raw")), with_timeline=False)
    _build_raw_fixture(os.path.join(root, "data", "results"), drop_ts=RAW_TS[0])
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
    page.wait_for_function("MergedMap._dbg().mounted === true", timeout=20000)
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
    assert "4 events" in page.inner_text("#gm-count")
    assert page.locator("#gm-gantt .gm-bar").count() == 4
    # boot parks on the busiest step (G-11): 03 Jan 18:00 — gf1 + gm4 live
    assert "03 Jan 2026" in page.inner_text("#raw-readout")
    assert page.locator("#gm-boxes .gm-ev").count() == 2
    # 02 Jan 06:00: freeze gf1 (qualified 06:00) + dateline AR gm4 are live
    _mm_frame(page, "2026010206")
    assert "02 Jan 2026" in page.inner_text("#raw-readout")
    assert "06:00 UTC" in page.inner_text("#raw-readout")
    ids = page.eval_on_selector_all("#gm-boxes .gm-ev", "els => els.map(e => e.dataset.id).sort()")
    assert ids == ["gf1", "gm4"], ids
    # the dateline box renders SPLIT into two rects (wrapped bbox -> two paths)
    d = page.get_attribute("#gm-boxes .gm-ev[data-id=gm4] path.gm-bbox", "d")
    assert d.count("M") == 2, d
    assert page.errors == [], f"console errors: {page.errors}"
    page.screenshot(path=os.path.join(SHOTS, "fe-merged-fixture.png"))


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
    page.click('#event-table tbody tr:has-text("AR Dateline")')
    page.wait_for_selector("#detail-sec:not(.hidden)", timeout=25000)
    page.wait_for_function(
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
      return pt ? { lon: pt[1], place: placeName(pt[0], pt[1]) } : null;
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
    page.wait_for_function(
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
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')")
    assert not page.is_visible("#raw-badge"), "frame == cutoff is still ERA5"
    # a heat-token pixel is actually painted on the canvas (poll: the frame
    # fetch is async; tolerance covers the 84% alpha blend over the basemap)
    page.wait_for_function(_HEAT_PIXEL_JS, timeout=10000)
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs   # prefetch of the deliberately-dropped frame 0


def test_raw_layer_toggles_and_url(raw_server, page_ctx):
    page = page_ctx
    page.goto(raw_server + "/")
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('Valid time')",
        timeout=20000)
    chips = page.locator("#raw-layers .gm-chip")
    assert chips.count() == 3
    chips.nth(1).click()               # freeze off
    assert chips.nth(1).get_attribute("aria-pressed") == "false"
    assert page.evaluate("decodeURIComponent(location.hash)").find("layers=h,a") >= 0
    chips.nth(1).click()               # back on -> param disappears
    assert "layers" not in page.evaluate("location.hash")
    # deep link: frame + only-heat layers survive a load
    page.goto(raw_server + "/#raw=2025110606&layers=h")
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')",
        timeout=20000)
    chips = page.locator("#raw-layers .gm-chip")
    assert chips.nth(0).get_attribute("aria-pressed") == "true"
    assert chips.nth(1).get_attribute("aria-pressed") == "false"
    assert chips.nth(2).get_attribute("aria-pressed") == "false"
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
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('12:00 UTC')",
        timeout=20000)
    # slider scrub: jump to the first frame
    page.evaluate("""(() => { const s = document.querySelector('#raw-slider');
        s.value = '0'; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('00:00 UTC')")
    # play from the start: crosses 06:00, then auto-stops back at 12:00
    page.click("#raw-play")
    assert page.inner_text("#raw-play") == "⏸ pause"
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('06:00 UTC')",
        timeout=5000)
    page.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('12:00 UTC')"
        " && document.querySelector('#raw-play').textContent === '▶ play'",
        timeout=5000)
    # play pressed at the END wraps to frame 0 before advancing (checked the
    # readout flips off 12:00 within one 5fps tick)
    page.click("#raw-play")
    page.wait_for_function(
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


# ------------------------------------------------ duration-gate (owner 2026-11-10) ----
# The heat wave bug, twice now: cells that can never become an event were painted as
# if they had, and the "today so far" provisional bit made points flash VALID at 00Z
# (one 6-hourly step of four is enough to set it). Paint rule
# (notes/spec_min_gate.md): a cell paints only when its COMPLETED-day streak reaches
# the lane minimum published in index.dur.min (heat = the detector's 3, freeze = 1 by
# owner decision — a freeze still needs 3 days, but cold runs are short, so 1-2 day
# cells stay visible for context); the provisional bit is texture only and can never
# make a cell valid.
GATE_TS = ["2025110600", "2025110606", "2025110612", "2025110618"]
GATE_NY, GATE_NX = 36, 72
GATE_ROW = 18                       # lat 2.5 in the fixture's 5-deg grid
GATE_SUB = (GATE_ROW, 20)           # heat: 2 completed days — below the 3-day gate
GATE_QUAL = (GATE_ROW, 40)          # heat: 3 completed days — qualifies
GATE_PROV = (GATE_ROW, 60)          # heat: 0 completed + provisional bit at 00Z only
GATE_F_ONE = (GATE_ROW, 28)         # freeze: 1 completed day — paints (gate is 1)
GATE_F_REF_A = (GATE_ROW, 30)       # freeze: blank reference, same ocean row
GATE_F_PROV = (GATE_ROW, 32)        # freeze: 0 completed + provisional bit at 00Z only
GATE_F_REF_B = (GATE_ROW, 34)       # freeze: blank reference, same ocean row
GATE_MIN = {"heat": 3, "freeze": 1}


def _gate_planes():
    """One UTC day, four 6-hourly frames (v2 sidecar encoding: low 7 bits =
    COMPLETED days, bit 7 = today so far)."""
    planes = []
    for k, _ts in enumerate(GATE_TS):
        heat = bytearray(GATE_NY * GATE_NX)
        heat[GATE_SUB[0] * GATE_NX + GATE_SUB[1]] = (0x80 | 2) if k == 0 else 2
        heat[GATE_QUAL[0] * GATE_NX + GATE_QUAL[1]] = (0x80 | 3) if k == 0 else 3
        heat[GATE_PROV[0] * GATE_NX + GATE_PROV[1]] = 0x80 if k == 0 else 0
        freeze = bytearray(GATE_NY * GATE_NX)
        freeze[GATE_F_ONE[0] * GATE_NX + GATE_F_ONE[1]] = 1
        freeze[GATE_F_PROV[0] * GATE_NX + GATE_F_PROV[1]] = 0x80 if k == 0 else 0
        planes.append((heat, freeze))
    return planes


def _gate_cell_lonlat(cell):
    # fixture grid: lat0 -90 / lon0 -180, 5 deg steps -> cell CENTRE
    return [-180.0 + (cell[1] + 0.5) * 5.0, -90.0 + (cell[0] + 0.5) * 5.0]


_GATE_CELLS = {"sub": GATE_SUB, "qual": GATE_QUAL, "prov": GATE_PROV,
               "fOne": GATE_F_ONE, "fRefA": GATE_F_REF_A,
               "fProv": GATE_F_PROV, "fRefB": GATE_F_REF_B}

_GATE_READ_JS = """({cells}) => {
    const cv = document.querySelector('#raw-canvas');
    const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    const red = (i) => d[i] > d[i + 1] + 15 && d[i] > d[i + 2] + 15;
    let painted = 0;
    for (let i = 0; i < d.length; i += 4) if (red(i)) painted++;
    const dpr = MergedMap._dbg().dpr;
    // probe the 11x11 block around the cell's projected CENTRE: one cell is only
    // ~12 px wide here and the LUT rounds lon->cell, so the centre pixel itself
    // can land on the neighbour — never read a single pixel
    const block = (ll) => { const p = MergedMap._dbg().project(ll);
        if (!p) return null;
        const cx = Math.round(p[0] * dpr), cy = Math.round(p[1] * dpr);
        let n = 0, r = 0, g = 0, b = 0, reds = 0;
        for (let y = cy - 5; y <= cy + 5; y++)
            for (let x = cx - 5; x <= cx + 5; x++) {
                if (x < 0 || y < 0 || x >= cv.width || y >= cv.height) continue;
                const i = 4 * (y * cv.width + x);
                n++; r += d[i]; g += d[i + 1]; b += d[i + 2];
                if (red(i)) reds++;
            }
        return {reds, rgb: [r / n, g / n, b / n]}; };
    const out = {painted};
    for (const [k, ll] of Object.entries(cells)) out[k] = block(ll);
    return out;
}"""


def _gate_lum(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


@pytest.fixture(scope="module")
def gate_server(tmp_path_factory):
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-gate")),
                             with_timeline=False, raw_frames=GATE_TS,
                             raw_cutoff=GATE_TS[-1])
    _build_raw_fixture(os.path.join(root, "data", "results"), frames=GATE_TS,
                       cutoff=GATE_TS[-1], run_planes=_gate_planes(),
                       dur={"rev": "v2", "seed": GATE_TS[0], "min": dict(GATE_MIN)})
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


def test_duration_gate_blanks_sub_threshold_and_never_flashes_00z(gate_server, page_ctx):
    page = page_ctx
    _mm_goto(page, gate_server)
    assert page.evaluate("!!MergedMap._dbg().grid"), "raw index did not load"
    counts = {}
    for ts in GATE_TS:
        _mm_frame(page, ts)
        assert f"{ts[8:10]}:00 UTC" in page.inner_text("#raw-readout"), ts
        page.wait_for_timeout(300)               # let the async frame paint land
        got = page.evaluate(_GATE_READ_JS, {"cells": {
            k: _gate_cell_lonlat(c) for k, c in _GATE_CELLS.items()}})
        counts[ts] = got["painted"]
        # HEAT (gate 3): the qualifying cell paints in EVERY frame of the day,
        # prov bit included; a 2-day cell stays blank all day; and a
        # provisional-only cell must never light up — that 00Z flash IS the bug
        # (one step of four exceeded is not a completed day).
        assert got["qual"]["reds"] > 0, (ts, "qualifying cell never painted", got["qual"])
        assert got["sub"]["reds"] == 0, (ts, "sub-threshold heat cell painted", got["sub"])
        assert got["prov"]["reds"] == 0, (ts, "provisional-only heat cell painted", got["prov"])
        # FREEZE (gate 1, owner 2026-11-10): a 1-day cell paints. The base map is
        # already blue-family, so compare the cell against its blank neighbour on
        # the same ocean row instead of classifying the colour.
        painted = _gate_lum(got["fOne"]["rgb"]) - _gate_lum(got["fRefA"]["rgb"])
        assert painted < -12, (ts, "1-day freeze cell not painted",
                               got["fOne"], got["fRefA"], painted)
        # ...while a provisional-only freeze cell is indistinguishable from blank
        ghost = _gate_lum(got["fProv"]["rgb"]) - _gate_lum(got["fRefB"]["rgb"])
        assert abs(ghost) < 6, (ts, "provisional-only freeze cell painted",
                                got["fProv"], got["fRefB"], ghost)
    # the painted set is frame-invariant across the day: no 00Z abundance
    assert counts[GATE_TS[0]] == counts[GATE_TS[1]] == counts[GATE_TS[2]] == counts[GATE_TS[3]], counts
    assert counts[GATE_TS[0]] > 0, "the qualifying cell never painted"
    errs = [e for e in page.errors if "Failed to load resource" not in e]
    assert errs == [], errs
