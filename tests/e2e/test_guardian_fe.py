"""Production-hardening regression suite (guardian-frontend, 2026-11-08).

Every bug fixed in the launch audit has a test here. Runs on Chromium, Firefox
and WebKit:

    scripts/e2e.sh                       # all three engines (see script for WebKit libs)
    EWB_E2E_BROWSERS=chromium scripts/e2e.sh -k hash      # one engine / a subset

Targets EWB_LIVE_URL (default: the dashboard on the ewb box). Header /
static-hygiene tests start the repo's own ewb_live.serve on a free local port
(a static file server — no EWB compute) so they validate the code in the
worktree, not whatever is deployed.
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
LIVE_URL = os.environ.get("EWB_LIVE_URL", "http://192.168.0.121:8050").rstrip("/")
AXE = (Path(__file__).parent / "vendor" / "axe.min.js").read_text()

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

_ENV = os.environ.get("EWB_E2E_BROWSERS", "chromium")
BROWSERS = ["chromium", "firefox", "webkit"] if _ENV == "all" else _ENV.split(",")

INIT = "window.__rej=[];window.addEventListener('unhandledrejection',e=>window.__rej.push(String(e.reason)));"


def wait_js(page, expr, timeout=20000):
    """Poll a JS expression via evaluate(). page.wait_for_function() evals a string, which the
    served CSP forbids and WebKit ignores bypass_csp for — evaluate() is protocol-level and fine."""
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate(f"() => !!({expr})"):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for: {expr}")


def _reachable(url):
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(
            url + "/data/results/meta.json", headers={"User-Agent": "Mozilla/5.0 (e2e)"}), timeout=6)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def base():
    if not _reachable(LIVE_URL):
        pytest.skip(f"dashboard not reachable at {LIVE_URL}")
    return LIVE_URL


@pytest.fixture(scope="module", params=BROWSERS)
def engine(request):
    with sync_playwright() as pw:
        try:
            b = getattr(pw, request.param).launch()
        except Exception as e:  # engine not installed / missing system libs
            pytest.skip(f"{request.param} unavailable: {str(e).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture()
def pg(engine):
    """Fresh context+page; console errors, page errors and unhandled
    rejections are collected and asserted empty by tests that call .clean()."""
    # bypass_csp: Playwright's string wait_for_function needs eval, which the public
    # origin's CSP (correctly) forbids. CSP itself is asserted in test_csp_*.
    ctx = engine.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    ctx.add_init_script(INIT)
    page = ctx.new_page()
    page.errs = []
    # tile fetches depend on the public internet / CSP of the runner — not our bug
    page.on("console", lambda m: page.errs.append(m.text[:200])
            if m.type == "error" and "arcgisonline" not in m.text and "tile.openstreetmap" not in m.text
            and "TLS support is not available" not in m.text   # WPE build without TLS: https tiles only
            else None)
    page.on("pageerror", lambda e: page.errs.append("pageerror: " + str(e)[:200]))
    page.on("response", lambda r: page.errs.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and "arcgisonline" not in r.url and "openstreetmap" not in r.url else None)
    page.clean = lambda: (page.errs, page.evaluate("window.__rej"))  # type: ignore[attr-defined]
    yield page
    ctx.close()


def _boot(page, base, path="/", wait=1500):
    page.goto(base + path, wait_until="load")
    wait_js(page, "typeof state !== 'undefined' && state.events && state.events.length > 0")
    page.wait_for_timeout(wait)


def _first_event(page):
    return page.evaluate("state.events[0].id")


# --------------------------------------------------------------------------
# routing robustness
# --------------------------------------------------------------------------
@pytest.mark.parametrize("hash_", ["#event=%E0%A4%A", "#global=%ZZ&date=%", "#event=%", "#%E0%A4%A=1"])
def test_malformed_hash_does_not_kill_boot(pg, base, hash_):
    """Regression: decodeURIComponent threw on a malformed escape inside
    hashParams() and aborted boot() -> 'Could not load score data'."""
    _boot(pg, base, "/" + hash_)
    assert pg.is_hidden("#load-error")
    assert pg.locator("#event-table tbody tr").count() > 0
    errs, rej = pg.clean()
    assert not [e for e in errs if "URIError" in e or "malformed" in e.lower()], errs
    assert not rej


def test_unknown_event_id_falls_back_to_catalog_with_note(pg, base):
    """Event ids rotate weekly, so shared links rot: the catalog opens, the
    stale id is dropped from the URL, and a status note says why."""
    _boot(pg, base, "/#event=deadbeef0000&lead=72h")
    assert pg.is_hidden("#detail-sec")
    assert pg.is_visible("#events-sec")
    assert "event=" not in pg.evaluate("location.hash")
    assert "lead=" not in pg.evaluate("location.hash")
    assert "not in the current 90-day window" in pg.inner_text("#route-note")
    assert pg.get_attribute("#route-note", "role") == "status"


def test_unknown_event_via_address_bar_while_open_closes_detail(pg, base):
    _boot(pg, base)
    ev = _first_event(pg)
    pg.goto(base + f"/#event={ev}")
    pg.wait_for_selector("#detail-sec:not(.hidden)")
    pg.evaluate("location.hash = '#event=deadbeef0000'")
    pg.wait_for_selector("#detail-sec", state="hidden")
    assert pg.is_visible("#events-sec")
    assert "event=" not in pg.evaluate("location.hash")


def test_unknown_week_param_is_dropped_from_url(pg, base):
    _boot(pg, base, "/?week=2020-W01")
    assert "week=" not in pg.evaluate("location.search")
    assert "weekly snapshot is not available" in pg.inner_text("#route-note")
    assert pg.locator("#event-table tbody tr").count() > 0


def test_route_note_clears_itself(pg, base):
    _boot(pg, base, "/#event=deadbeef0000")
    assert pg.locator("#route-note").count() == 1
    wait_js(pg, "!document.getElementById('route-note')", 12000)


def test_storage_blocked_does_not_break_theme_toggle(engine, base):
    """saveState() wrote localStorage unguarded: a blocked store (private
    windows, sandboxed embeds) threw out of the theme/tab handlers."""
    ctx = engine.new_context(bypass_csp=True)
    ctx.add_init_script("""Object.defineProperty(window,'localStorage',{get(){throw new DOMException('blocked','SecurityError')}})""")
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    _boot(page, base)
    page.click("#theme-ctl button[data-pref='dark']")
    page.click("#type-tabs button:nth-child(2)")
    page.wait_for_timeout(300)
    assert page.locator("#event-table tbody tr").count() >= 0
    assert not errs, errs
    ctx.close()


# --------------------------------------------------------------------------
# focus management / keyboard
# --------------------------------------------------------------------------
def test_focus_moves_to_detail_heading_and_returns_to_row(pg, base):
    """Regression: opening an event from the keyboard dropped focus to <body>
    (the focused row was hidden) and Esc never restored it."""
    _boot(pg, base)
    row = pg.locator("#event-table tbody tr").nth(2)
    ev = row.get_attribute("data-id")
    row.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#detail-sec:not(.hidden)")
    assert pg.evaluate("document.activeElement.id") == "detail-title"
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#detail-sec", state="hidden")
    wait_js(pg, f"document.activeElement && document.activeElement.dataset && document.activeElement.dataset.id === '{ev}'")


def test_theme_flip_inside_detail_does_not_steal_focus(pg, base):
    _boot(pg, base, f"/#event={_first_event_id(base)}")
    pg.wait_for_selector("#detail-sec:not(.hidden)")
    pg.focus("#theme-ctl button[data-pref='dark']")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(400)
    assert pg.evaluate("document.activeElement.dataset.pref") == "dark"
    assert pg.evaluate("document.activeElement.closest('#theme-ctl') !== null")


def _first_event_id(base):
    import json
    import urllib.request
    r = urllib.request.urlopen(urllib.request.Request(
        base + "/data/results/events_selected.json", headers={"User-Agent": "Mozilla/5.0 (e2e)"}))
    d = json.load(r)
    return (d["events"] if isinstance(d, dict) else d)[0]["id"]


def test_global_map_keyboard_toggle_keeps_focus(pg, base):
    """Toggling an event on the merged map rebuilt its nodes and dropped focus
    to <body> — keyboard users lost their place after every Enter."""
    _boot(pg, base)
    pg.wait_for_selector("#gm-boxes .gm-ev", timeout=15000)
    node = pg.locator("#gm-boxes .gm-ev").first
    ident = node.get_attribute("data-id")
    node.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.activeElement.getAttribute('data-id')") == ident
    assert pg.evaluate("document.activeElement.getAttribute('aria-pressed')") == "true"


def test_reduced_motion_scroll_is_instant(engine, base):
    ctx = engine.new_context(reduced_motion="reduce", viewport={"width": 1280, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    _boot(page, base)
    page.evaluate("window.__sb=[];Element.prototype.scrollIntoView=function(a){window.__sb.push(a&&a.behavior)}")
    page.locator("#event-table tbody tr").first.click()
    page.wait_for_selector("#detail-sec:not(.hidden)")
    assert [b for b in page.evaluate("window.__sb") if b] == ["auto"]
    ctx.close()


# --------------------------------------------------------------------------
# leaks / races
# --------------------------------------------------------------------------
def test_no_dom_or_listener_growth_over_open_close_cycles(pg, base):
    """The 'more metrics' class of bug: repeated interactions must not grow
    the DOM or the window/document listener set."""
    pg.add_init_script("""(()=>{const A=EventTarget.prototype.addEventListener,R=EventTarget.prototype.removeEventListener;
      window.__L=0;EventTarget.prototype.addEventListener=function(t,f,o){if(this===window||this===document)window.__L++;return A.call(this,t,f,o)};
      EventTarget.prototype.removeEventListener=function(t,f,o){if(this===window||this===document)window.__L--;return R.call(this,t,f,o)}})()""")
    _boot(pg, base)

    def cycle(i):
        pg.locator("#event-table tbody tr").nth(i % 5).click()
        pg.wait_for_selector("#detail-sec:not(.hidden)")
        pg.wait_for_timeout(700)
        pg.click("#detail-close")
        pg.wait_for_selector("#detail-sec", state="hidden")
        pg.click("#metrics-more")
        pg.click("#metrics-more")

    for i in range(3):          # warm-up: lazy singletons (Plotly hooks, tooltips) settle
        cycle(i)
    pg.wait_for_timeout(500)
    n0 = pg.evaluate("document.getElementsByTagName('*').length")
    l0 = pg.evaluate("window.__L")
    for i in range(8):
        cycle(i)
    pg.wait_for_timeout(500)
    n1 = pg.evaluate("document.getElementsByTagName('*').length")
    l1 = pg.evaluate("window.__L")
    assert n1 - n0 <= 25, f"DOM grew {n0}->{n1}"
    assert l1 - l0 <= 3, f"window/document listeners grew {l0}->{l1}"
    assert pg.locator("#metrics-more").count() == 1
    errs, rej = pg.clean()
    assert not errs and not rej, (errs, rej)


def test_rapid_open_close_back_forward_keeps_url_and_view_consistent(pg, base):
    _boot(pg, base)
    ids = pg.evaluate("state.events.slice(0,4).map(e=>e.id)")
    for i in range(4):
        pg.evaluate(f"selectEvent('{ids[i]}', false)")
        pg.evaluate("history.back()")
        pg.evaluate("history.forward()")
        pg.keyboard.press("Escape")
    pg.wait_for_timeout(1500)
    hid = pg.is_hidden("#detail-sec")
    h = pg.evaluate("location.hash")
    assert hid == ("event=" not in h)
    assert pg.evaluate("state.selectedEvent") == (None if hid else pg.evaluate("hashParams().event"))
    errs, rej = pg.clean()
    assert not errs and not rej, (errs, rej)


def test_deep_link_every_view_no_console_noise(pg, base):
    _boot(pg, base)
    ids = pg.evaluate("state.events.map(e=>e.id)")
    for ev in ids[:6]:
        pg.goto(base + f"/#event={ev}&lead=24h")
        pg.wait_for_selector("#detail-sec:not(.hidden)")
        pg.wait_for_timeout(900)
    errs, rej = pg.clean()
    assert not errs and not rej, (errs, rej)


# --------------------------------------------------------------------------
# content / formatting regressions requested by the design guardian
# --------------------------------------------------------------------------
def test_early_signal_is_a_unitless_share(pg, base):
    _boot(pg, base)
    assert pg.evaluate("metricMeta('EarlySignal').units") == ""
    assert pg.evaluate("fmtVal('EarlySignal', 0.4567)") == "0.46"
    assert "Share of events" in pg.evaluate("metricBlurb('EarlySignal')")


def test_negative_numbers_use_true_minus(pg, base):
    _boot(pg, base)
    assert pg.evaluate("fmtVal('DurationMeanError', -14.62)") == "−14.6"
    assert pg.evaluate("fmtVal('CriticalSuccessIndex', -0.05)") == "−0.05"


def test_default_models_are_all_scored_models(pg, base):
    _boot(pg, base)
    assert pg.evaluate("state.models.size >= Math.min(state.allModels.length, 7)")
    assert pg.evaluate("[...state.models].every(m => state.allModels.includes(m))")


def test_scorecard_tooltip_has_ci_and_direction(pg, base):
    _boot(pg, base)
    cell = pg.locator("td[data-tip]").nth(3)
    cell.hover()
    pg.wait_for_timeout(300)
    tip = pg.inner_text("#tooltip")
    assert re.search(r"(higher|lower|closer to 0) is better", tip), tip
    if pg.evaluate("state.scorecard.some(r => Number.isFinite(r.ci_lo))"):
        assert "95% CI" in tip, tip


def test_catalog_peak_column_uses_full_units(pg, base):
    _boot(pg, base)
    assert "kg/m/s" not in pg.inner_text("#event-table")


# --------------------------------------------------------------------------
# accessibility: axe-core must report ZERO violations
# --------------------------------------------------------------------------
AXE_STATES = [("home", "/"), ("detail", "EVENT")]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", ["home", "detail"])
def test_axe_zero_violations(engine, base, scheme, name):
    ctx = engine.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme, bypass_csp=True)
    page = ctx.new_page()
    path = "/" if name == "home" else f"/#event={_first_event_id(base)}&lead=24h"
    _boot(page, base, path, wait=3500)
    page.add_script_tag(content=AXE)
    res = page.evaluate("axe.run(document, {resultTypes:['violations']}).then(r => r.violations.map(v => "
                        "({id: v.id, n: v.nodes.length, sel: v.nodes.slice(0,3).map(n => n.target.join(' '))})))")
    ctx.close()
    assert res == [], res


# --------------------------------------------------------------------------
# server hygiene (runs the worktree's own serve.py on a free port)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def local_server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = dict(os.environ, EWB_PUBLIC_URL="https://example.test")
    proc = subprocess.Popen([sys.executable, "-m", "ewb_live.serve", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield url
    proc.terminate()


def _get(url, headers=None, method="GET"):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_security_headers_present(local_server):
    st, h, _ = _get(local_server + "/")
    assert st == 200
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert h["Referrer-Policy"]
    csp = h["Content-Security-Policy"]
    for want in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'self'", "base-uri 'self'"):
        assert want in csp
    assert "Python" not in (h["Server"] or "")


def test_no_directory_listings_or_dev_files(local_server):
    for p in ("/vendor/", "/vendor", "/geo/", "/vendor/fonts/", "/tools/generate_sample_data.py",
              "/tools/", "/.git/config", "/data/", "/data/results/"):
        st, _, body = _get(local_server + p)
        assert st == 404, (p, st)
        assert b"Directory listing" not in body


def test_404_page_is_ours_and_not_cacheable(local_server):
    st, h, body = _get(local_server + "/nope.html")
    assert st == 404
    assert b"Error response" not in body and b"EWB Live" in body
    assert h["Cache-Control"] == "no-store"


def test_own_assets_always_revalidate(local_server):
    """mergedmap.js was cached for 1 h (not in the no-cache list),
    so a deploy could leave a new app.js talking to a stale sibling script."""
    for p in ("/", "/app.js", "/eventviz.js", "/mergedmap.js", "/styles.css", "/index.html"):
        _, h, _ = _get(local_server + p)
        assert h["Cache-Control"] == "no-cache", p
    _, h, _ = _get(local_server + "/vendor/fonts/inter-latin-400-normal.woff2")
    assert "immutable" in h["Cache-Control"]


def test_cache_control_is_not_duplicated(local_server):
    for p in ("/data/results/weeks.json", "/data/results/meta.json", "/data/results/events/zzzz/viz.json"):
        _, h, _ = _get(local_server + p)
        assert len(h.get_all("Cache-Control") or []) == 1, (p, h.get_all("Cache-Control"))


def test_index_head_social_and_seo(local_server):
    _, _, body = _get(local_server + "/")
    html = body.decode()
    assert '<link rel="canonical" href="https://example.test/">' in html
    assert 'property="og:image" content="https://example.test/og-image.png"' in html
    assert 'name="twitter:card"' in html and 'name="description"' in html
    assert "%PUBLIC_URL%" not in html
    st, h, img = _get(local_server + "/og-image.png")
    assert st == 200 and img[:8] == b"\x89PNG\r\n\x1a\n"
    st, _, rb = _get(local_server + "/robots.txt")
    assert st == 200 and b"User-agent" in rb


def test_public_tags_dropped_when_no_public_url(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if k != "EWB_PUBLIC_URL"}
    proc = subprocess.Popen([sys.executable, "-m", "ewb_live.serve", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import urllib.request
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        _, _, body = _get(f"http://127.0.0.1:{port}/")
        assert b"%PUBLIC_URL%" not in body and b'rel="canonical"' not in body
    finally:
        proc.terminate()


def test_csp_does_not_break_the_app(engine, local_server):
    """Boot + open an event under the real CSP: no 'Refused to ...' errors."""
    ctx = engine.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if "Content Security Policy" in m.text
            or "Refused to" in m.text else None)
    page.goto(local_server + "/", wait_until="load")
    page.wait_for_timeout(2500)
    if page.evaluate("typeof state !== 'undefined' && state.events.length") > 0:
        page.locator("#event-table tbody tr").first.click()
        page.wait_for_timeout(1500)
    ctx.close()
    assert not errs, errs


def test_history_week_event_explains_missing_maps_truthfully(pg, base):
    """Ponytail 14:31: weekly snapshots archive top-level JSON only, so an archived
    week's events never have viz.json — the copy must not claim 'not built yet'."""
    import json
    import urllib.request
    wk = json.load(urllib.request.urlopen(urllib.request.Request(
        base + "/data/results/weeks.json", headers={"User-Agent": "Mozilla/5.0 (e2e)"}))).get("weeks", [])
    if not wk:
        pytest.skip("no archived weeks on this deployment")
    pg.route("**/events/*/viz.json", lambda r: r.fulfill(status=204))   # asset absent (as in an archived week)
    _boot(pg, base, f"/?week={wk[-1]['week']}")
    pg.evaluate("selectEvent(state.events[0].id, false)")
    pg.wait_for_selector("#detail-viz .lane-empty", timeout=15000)
    txt = pg.inner_text("#detail-viz .lane-empty")
    assert "only kept for the current week" in txt and "hasn't been built" not in txt


def test_offline_map_fetch_says_network_not_incomplete_asset(pg, base):
    """Going offline mid-session used to read 'asset appears incomplete / should
    appear once rebuilt' — blaming the pipeline for the viewer's network."""
    _boot(pg, base)
    pg.context.set_offline(True)
    pg.evaluate("selectEvent(state.events[3].id, false)")
    pg.wait_for_selector("#detail-viz .lane-empty", timeout=15000)
    assert "Could not reach the server" in pg.inner_text("#detail-viz .lane-empty")
    pg.context.set_offline(False)


# --------------------------------------------------------------------------
# raw exceedance map rendering (owner 2026-11-08: scanline stripes + pole band)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def full_heat_server(tmp_path_factory):
    """Fixture webroot whose single raw frame has EVERY heat cell set on the real
    0.25° grid (721×1440): any seam, overlap or edge artefact then shows up as a
    non-uniform pixel run."""
    import json
    import shutil
    import zlib
    sys.path.insert(0, str(Path(__file__).parent))
    import test_dashboard_fe as fe
    root = fe._build_gd_fixture(str(tmp_path_factory.mktemp("fe-fullheat")), with_timeline=False)
    rd = Path(root) / "data" / "results" / "raw_exceedance"
    shutil.rmtree(rd, ignore_errors=True)
    rd.mkdir(parents=True)
    ny, nx, stride = 721, 1440, 180
    ts = "2026110100"
    json.dump({"schema": 1, "generated": "2026-11-01T00:00:00Z",
               "grid": {"res_deg": 0.25, "ny": ny, "nx": nx, "lat0": -90.0, "lon0": -180.0,
                        "step_lat": 0.25, "step_lon": 0.25},
               "frames": [ts], "cutoff": ts, "window": {"start": "2026-11-01", "end": "2026-11-01"}},
              open(rd / "index.json", "w"))
    heat = b"\xff" * (ny * stride)
    (rd / f"{ts}.bin").write_bytes(zlib.compress(heat + bytes(ny * stride) * 2))
    srv, url = fe._gd_server(root)
    yield url
    srv.shutdown()


@pytest.mark.parametrize("dpr", [1, 2])
def test_raw_map_cells_tile_seamlessly_and_poles_are_clean(engine, full_heat_server, dpr):
    """Regression: per-latitude-row drawImage strips were sub-pixel tall and
    Math.ceil()'d, so neighbours overlapped (alpha doubled → horizontal scanline
    stripes) and the single pole row was stretched into a slab under Antarctica."""
    ctx = engine.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=dpr, bypass_csp=True)
    page = ctx.new_page()
    page.goto(full_heat_server + "/#raw=2026110100&layers=h", wait_until="load")
    page.wait_for_selector("#raw-canvas:not([hidden])", timeout=20000)
    page.wait_for_timeout(1200)
    res = page.evaluate("""() => {
      const cv = document.querySelector('#raw-canvas'), g = cv.getContext('2d');
      const W = cv.width, H = cv.height, d = window.devicePixelRatio || 1;
      const px = (x, y) => Array.from(g.getImageData(Math.round(x), Math.round(y), 1, 1).data);
      // vertical run through open Pacific at ~108W (no land within +-15 deg of
      // the equator there: the coastline OVERSTROKE above the fills is by
      // design since 2026-11-11, so the seam scan must not cross a coast)
      const x = W / 2 - W * 0.30, run = [];
      for (let y = H / 2 - 60 * d; y < H / 2 + 60 * d; y++) run.push(px(x, y)[1]);   // green channel
      const bandRows = [];                         // rows below the south pole / above the north pole
      for (let k = 2; k < 10; k++) bandRows.push(px(W / 2, H - k * d)[3], px(W / 2, k * d)[3]);
      // pole hairline: the rows hugging the sphere's top/bottom edge must not be heat red
      const heat = px(x, H / 2);
      return { spread: Math.max(...run) - Math.min(...run), heat, bandAlpha: bandRows, H, W };
    }""")
    ctx.close()
    assert res["heat"][3] > 150, res           # layer is painted at all
    # seamless: a doubled-alpha overlap stripe moves G by ~24; the faint graticule
    # under the fill moves it by ~3 and the --map-line coastline hairline by ~10,
    # so anything above 12 is a stripe (threshold recalibrated when drawBase
    # moved coastline ink to --map-line, design rider 3)
    assert res["spread"] <= 12, res
    assert max(res["bandAlpha"]) == 0, res     # nothing painted outside the sphere


# --------------------------------------------------------------------------
# error boundaries: a missing/garbled data file never blanks the page
# --------------------------------------------------------------------------
BOOT_FILES = ["meta.json", "events_selected.json", "scores.json", "scorecard.json", "pair_status.json",
              "weeks.json", "global_timeline.json"]
BAD_BODIES = {"404": dict(status=404, body=""), "garbage": dict(status=200, body="<html>x",
              content_type="application/json"), "null": dict(status=200, body="null", content_type="application/json"),
              "emptyobj": dict(status=200, body="{}", content_type="application/json")}


@pytest.mark.parametrize("kind", sorted(BAD_BODIES))
@pytest.mark.parametrize("fname", BOOT_FILES)
def test_bad_data_file_degrades_gracefully(engine, base, fname, kind):
    """404 / garbage / `null` / `{}` for every boot file: no uncaught exception, no
    stuck skeleton, the page still shows content (regression: weeks.json=`null`
    threw out of loadWeeks() and aborted boot with 'Could not load score data')."""
    ctx = engine.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    spec = BAD_BODIES[kind]
    page.route(f"**/data/results/{fname}", lambda r, *a: r.fulfill(**spec))
    page.goto(base + "/", wait_until="load")
    page.wait_for_timeout(2500)
    try:   # slow edges (public CDN) can take longer than the fixed wait to settle the skeletons
        wait_js(page, "![...document.querySelectorAll('.gm-skeleton,.raw-skeleton')].some(e => !e.hidden && getComputedStyle(e).display != 'none')", 12000)
    except AssertionError:
        pass
    st = page.evaluate("""() => ({txt: document.body.innerText.length,
        skel: [...document.querySelectorAll('.gm-skeleton,.raw-skeleton')].filter(e => !e.hidden && getComputedStyle(e).display != 'none').length})""")
    ctx.close()
    assert not errs, errs
    assert st["txt"] > 300 and st["skel"] == 0, st


# --------------------------------------------------------------------------
# antimeridian: every renderer must handle events crossing ±180
# --------------------------------------------------------------------------
def _json_get(base, name):
    import json
    import urllib.request
    return json.load(urllib.request.urlopen(urllib.request.Request(
        base + "/data/results/" + name, headers={"User-Agent": "Mozilla/5.0 (e2e)"})))


def _wrapped_event(base):
    sel = _json_get(base, "events_selected.json")
    for e in (sel["events"] if isinstance(sel, dict) else sel):
        if e["bbox"][2] > e["bbox"][3]:
            return e
    return None


def _span(b):
    return b[3] - b[2] if b[3] >= b[2] else b[3] + 360 - b[2]


DATELINE_PROBE = """(id) => {
  const ev = state.events.find(e => e.id === id);
  const node = document.querySelector('#gm-boxes [data-id="' + id + '"]');
  const W = document.querySelector('#raw-canvas').getBoundingClientRect().width;
  const bb = node && node.querySelector('.gm-bbox');
  return { bbox: ev.bbox, W,
           node: !!node,
           bboxD: bb ? bb.getAttribute('d') : null };
}"""


def _dateline_page(engine, base, ev, patch=None, strip_footprint=False):
    ctx = engine.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    if patch:
        sel, allev, tl = (_json_get(base, n) for n in ("events_selected.json", "events.json", "global_timeline.json"))
        for obj in (sel, allev, tl):
            for e in (obj["events"] if isinstance(obj, dict) else obj):
                if e["id"] == ev["id"]:
                    e["bbox"] = patch(e["bbox"])
                    if strip_footprint and "masks" in e:
                        e.pop("grid", None)
                        e.pop("masks", None)

        def handler(route, *a):
            u = route.request.url
            if u.endswith("/events_selected.json"):
                route.fulfill(json=sel)
            elif u.endswith("/events.json"):
                route.fulfill(json=allev)
            elif u.endswith("/global_timeline.json"):
                route.fulfill(json=tl)
            else:
                route.continue_()
        page.route("**/data/results/*.json", handler)
    tl_entry = next((e for e in _json_get(base, "global_timeline.json")["events"] if e["id"] == ev["id"]), None)
    q = (tl_entry or {}).get("qualified_at") or ev["start"][:10].replace("-", "") + "00"
    page.goto(base + f"/#map={q}&sel={ev['id']}", wait_until="load")
    wait_js(page, "typeof state !== 'undefined' && state.events && state.events.length > 0")
    wait_js(page, "typeof MergedMap !== 'undefined' && MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(800)
    return ctx, page


def _detail_width(page, base, ev_id):
    page.goto(base + f"/#event={ev_id}&lead=24h")
    page.wait_for_selector("#detail-viz .ev3-panel", timeout=25000)
    page.wait_for_timeout(1200)
    return page.evaluate("""() => { const m = window.__ev3.maps[0], b = m.getBounds();
        return b.getEast() - b.getWest(); }""")


def test_dateline_event_renders_sanely_on_every_surface(engine, base):
    ev = _wrapped_event(base)
    if not ev:
        pytest.skip("no dateline-crossing event in the current set")
    true_w = _span(ev["bbox"])
    ctx, page = _dateline_page(engine, base, ev)
    r = page.evaluate(DATELINE_PROBE, ev["id"])
    assert r["node"] and r["bboxD"], r                             # box drawn at the qualification step
    assert r["bboxD"].count("M") == 2, r                           # wrapped bbox -> split at the seam, not a full-band bar
    # the two halves sit at opposite map edges, so the group bbox is wide: check the path itself
    for poly in r["bboxD"].split("Z")[:-1]:
        px = [float(x) for x in re.findall(r"[ML](-?\d+\.?\d*) ", poly)]
        assert max(px) - min(px) < 0.4 * r["W"], poly[:120]        # an unsplit run spanned ~80 %
    w = _detail_width(page, base, ev["id"])
    assert w < 2 * true_w + 10, (w, true_w)
    ctx.close()


def test_degenerate_full_band_bbox_is_recovered_from_footprint(engine, base):
    """A producer that records min/max of raw longitudes gives [-180,180]: the world-wide band.
    The footprint masks in the timeline let the dashboard recover the wrapped box."""
    ev = _wrapped_event(base)
    if not ev:
        pytest.skip("no dateline-crossing event in the current set")
    ctx, page = _dateline_page(engine, base, ev, patch=lambda b: [b[0], b[1], -180.0, 180.0])
    r = page.evaluate(DATELINE_PROBE, ev["id"])
    assert r["bbox"][2] > r["bbox"][3], r                           # wrapped again (lon_min > lon_max)
    assert _span(r["bbox"]) < 2 * _span(ev["bbox"]), r
    assert r["node"] and r["bboxD"].count("M") == 2, r              # recovered box renders, split at the seam
    ctx.close()


def test_unrecoverable_full_band_bbox_draws_no_band(engine, base):
    ev = _wrapped_event(base)
    if not ev:
        pytest.skip("no dateline-crossing event in the current set")
    ctx, page = _dateline_page(engine, base, ev, patch=lambda b: [b[0], b[1], -180.0, 180.0], strip_footprint=True)
    r = page.evaluate(DATELINE_PROBE, ev["id"])
    assert not r["node"] and not r["bboxD"], r                       # none on the merged map either
    w = _detail_width(page, base, ev["id"])
    assert w < 2 * _span(ev["bbox"]) + 10, w                         # detail still frames the field, not the world
    ctx.close()


# --------------------------------------------------------------------------
# design G-43 / G-10 / G-11 / G-12
# --------------------------------------------------------------------------
def test_hero_cells_are_the_exact_lead_value_with_its_own_ci(pg, base):
    """G-43: the 1 d column used to be an unweighted mean of leads 18/24/30/36 h (34/90 heat cells
    >25 % off the pipeline value) with CI/n of some other row."""
    _boot(pg, base)
    raw = _json_get(base, "scorecard.json")
    rows = raw["rows"] if isinstance(raw, dict) else raw
    by = {(r["event_type"], r["metric"], r["model"], round(r["lead_time_hours"])): r
          for r in rows if r.get("lead_time_hours") is not None}
    bad = []
    for lead in (24, 72, 120):
        for sc in pg.evaluate(f"state.scorecard.filter(r => r.lead_time_hours === {lead})"):
            src = by.get((sc["event_type"], sc["metric"], sc["model"], lead))
            if not src:
                continue
            mean = src.get("mean", src.get("mean_value"))
            if mean is None or sc["mean_value"] is None:
                continue
            if abs(sc["mean_value"] - mean) > 1e-9 or (src.get("ci95_lo") is not None and sc["ci_lo"] != src["ci95_lo"]):
                bad.append((sc["event_type"], sc["metric"], sc["model"], lead, sc["mean_value"], mean))
    assert not bad, bad[:5]


def test_binning_falls_back_to_weighted_mean_without_ci(pg, base):
    _boot(pg, base)
    pg_out = pg.evaluate("""() => mergeBinned([
      {event_type:'x', metric:'m', model:'a', lead_time_hours: 22, mean_value: 10, n_events: 1, ci_lo: 1, ci_hi: 2, rel: 5},
      {event_type:'x', metric:'m', model:'a', lead_time_hours: 26, mean_value: 4,  n_events: 3, ci_lo: 1, ci_hi: 2, rel: 6}],
      ['mean_value','ci_lo','ci_hi'], 'n_events', 'event_type')""")
    assert len(pg_out) == 1 and abs(pg_out[0]["mean_value"] - 5.5) < 1e-9, pg_out   # (1*10 + 3*4)/4
    assert pg_out[0]["ci_lo"] is None and pg_out[0]["rel"] is None


def test_hero_headline_metrics(pg, base):
    _boot(pg, base)
    heat = pg.evaluate("metricsFor('heat_wave')")
    ar = pg.evaluate("metricsFor('atmospheric_river')")
    if len(pg.evaluate("[...new Set(state.scorecard.filter(r => r.event_type === 'heat_wave').map(r => r.metric))]")) > 1 \
            and "RootMeanSquaredError" in pg.evaluate("state.scorecard.filter(r => r.event_type === 'heat_wave').map(r => r.metric)"):
        assert heat == ["RootMeanSquaredError"], heat
    assert ar[0] == "CriticalSuccessIndex" or len(ar) == 1


def test_global_map_opens_on_the_busiest_day(pg, base):
    """G-11 lineage: the merged map must PARK on the frame with most events
    active (ties → latest) so the hero is never an empty map."""
    _boot(pg, base, wait=3000)
    wait_js(pg, "typeof MergedMap !== 'undefined' && MergedMap._dbg().mounted === true")
    tl = _json_get(base, "global_timeline.json")
    frames = pg.evaluate("MergedMap._dbg().frames")
    def _c(s, hour_default="00"):     # live data mixes compact YYYYMMDDHH, date-only and full ISO
        s = str(s)
        if re.fullmatch(r"\d{10}", s): return s
        if "T" in s: return s[:10].replace("-", "") + s[11:13]   # map's own ISO branch
        z = s.replace("-", "").replace(":", "").replace("Z", "")
        return z[:10] + (z[10:12] or hour_default)
    qual = {e["id"]: _c(e.get("qualified_at") or e["start"]) for e in tl["events"]}
    end = {e["id"]: _c(e["end"]) for e in tl["events"]}   # end is exclusive
    best, best_n = frames[0], -1
    for f in reversed(frames):
        n = sum(1 for e in tl["events"] if qual[e["id"]] <= f < end[e["id"]])
        if n > best_n:
            best, best_n = f, n
    assert pg.evaluate("MergedMap._dbg().idx") == frames.index(best), (best, best_n)
    assert pg.locator("#gm-boxes .gm-ev").count() == best_n, best_n


def test_detail_heading_is_type_and_region(pg, base):
    _boot(pg, base)
    pg.evaluate("selectEvent(state.events[0].id, false)")
    pg.wait_for_selector("#detail-sec:not(.hidden)")
    pg.wait_for_timeout(800)
    h = pg.inner_text("#detail-title")
    assert " · " in h and not re.search(r"\d{4}-\d{2}-\d{2}", h), h


# --------------------------------------------------------------------------
# AR presence on live frames (owner 2026-11-09 outage: ramp-edit regression
# silently removed every AR pixel while mounting clean with layers ON)
# --------------------------------------------------------------------------
def test_ar_layer_visible_on_live_frames(pg, base):
    """The AR layer and its boxes must paint on a frame where the CURRENT run
    has a qualified AR — the frame is read from the data, so the weekly
    rotation can never stale this test (the old hard-coded Oct-3 12Z frame
    went stale when that river's qualified_at moved to Oct-4 06Z)."""
    import json
    import urllib.request

    def _get(path):
        return json.load(urllib.request.urlopen(urllib.request.Request(
            base + path, headers={"User-Agent": "Mozilla/5.0 (e2e)"})))
    frames = _get("/data/results/raw_exceedance/index.json")["frames"]
    qual = [(e.get("qualified_at") or e["start"][:10].replace("-", "") + "00")
            for e in _get("/data/results/global_timeline.json")["events"]
            if e["type"] == "atmospheric_river"]
    ts = next((q for q in qual if q in frames), None)
    if ts is None:
        pytest.skip("no AR qualifies within the published raw frames this run")
    _boot(pg, base, f"/#map={ts}&layers=h,f,a", wait=2000)
    wait_js(pg, "window.MergedMap && MergedMap._dbg().mounted === true")
    pg.wait_for_timeout(800)   # let the frame fetch + paint land
    ar_boxes = pg.locator("#gm-boxes .gm-ev path.gm-bbox.t-ar").count()
    assert ar_boxes >= 1, "no AR event boxes on the Oct-3 12Z frame"
    ar_px = pg.evaluate("""() => {
        const cv = document.querySelector('#raw-canvas'); if (!cv) return -1;
        const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
        let n = 0;   // AR green over any base: g dominant (theme-independent)
        for (let i = 0; i < d.length; i += 4)
            if (d[i+1] > d[i] + 22 && d[i+1] > d[i+2] + 22) n++;
        return n;
    }""")
    assert ar_px > 500, f"AR pixels missing on a 15k-cell frame (got {ar_px})"
