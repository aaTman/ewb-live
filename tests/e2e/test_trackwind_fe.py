"""The cyclone WIND FIELD on the merged map, in a real browser (tc lane).

Covers what the unit tests cannot: the map must fetch tc_wind.json and paint it
under the tracks, the legend must explain the ramp *and say the field is
reanalysis that under-reads*, hovering a cell must answer with the wind, the
storm, the day and the best-track peak in separate rows, and a build that has not
produced the asset yet must lose the field and keep the tracks — with no JS error
and no invented shading.

Placement itself (which cell lands where) is pinned against a fake ARCO store in
tests/test_tc_wind.py. Here the field is measured A/B: the same page with and
without the asset, because the lane's best track is violet too, so a colour count
alone would prove nothing.

Run: .venv/bin/python -m pytest tests/e2e/test_trackwind_fe.py -m e2e
"""
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
SHOTS = os.path.join(ROOT, "notes", "screenshots")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from ewb_live.viz import tc_wind as TW                                      # noqa: E402
from ewb_live.viz import timeline as TL                                     # noqa: E402

from test_dashboard_fe import _build_gd_fixture                             # noqa: E402

FRAMES = ["2026010200", "2026010206", "2026010212"]
DAYS = ["2026-01-02", "2026-01-03", "2026-01-04"]        # the storm's whole life
# the storm's own window: 11×11 cells at 1°, southwest cell centre (15, -155)
LAT0, LON0, NY, NX = 15.0, -155.0, 11, 11
FIX_LAT, FIX_LON = 20.0, -150.0                          # fix at the first frame

TC = {"id": "tww1", "event_type": "tropical_cyclone", "title": "TC Fieldwide (e2e)",
      "start": "2026-01-02T00:00:00", "end": "2026-01-04T00:00:00",
      "peak_value": 70.0, "bbox": [18.0, 22.0, -152.0, -148.0],
      "details": {"track": [["2026-01-02T00:00:00Z", FIX_LAT, FIX_LON, 60.0, 985.0],
                            ["2026-01-02T06:00:00Z", 20.5, -149.5, 70.0, 980.0],
                            ["2026-01-03T00:00:00Z", 21.0, -149.0, 55.0, 990.0]],
                  "intensity_units": "kt", "fix_step_h": 6, "name": "Fieldwide",
                  "basin_name": "EP", "peak_wind_kt": 70.0, "peak_category": "1",
                  "min_pressure_hpa": 980.0, "track_crosses_antimeridian": False}}


def _field():
    """Storm force across a ±5° window (a large cyclone: R34 ≈ 5°) with a CAT 1
    core at the fix, packed exactly as the producer packs it — rle over the 1°
    lattice. The window is filled so the field reaches past the marker's own
    claim on the pointer, which is what the hover test needs."""
    g = np.zeros((NY, NX), np.uint8)
    i0, j0 = int(round(FIX_LAT - LAT0)), int(round(FIX_LON - LON0))
    assert (i0, j0) == (5, 5)                            # the fix is the centre
    ii, jj = np.mgrid[0:NY, 0:NX]
    g[(ii - i0) ** 2 + (jj - j0) ** 2 <= 30] = 45        # tropical-storm force
    g[i0, j0] = 70                                       # CAT 1 core
    return g


def _wind_doc():
    f = _field()
    census = {str(c): int((f == kt).sum()) for c, kt in ((1, 45), (2, 70))}
    return {"schema": 1, "lane": "tropical_cyclone",
            "source": "ERA5 10 m wind (10m_u/v_component_of_wind), ERA5 ARCO 0.25°",
            "cadence_h": 6, "step_deg": 1.0, "read_deg": 0.25, "half_deg": 5.0,
            "floor_kt": 34.0, "units": "kt", "classes": TW.CLASSES,
            "bias": {"ratio_median": 0.58, "ratio_min": 0.34, "ratio_max": 0.9,
                     "n": 2, "note": "ERA5 10 m wind under-reads cyclone peaks."},
            "census": census, "days_no_data": [],
            # one grid per UTC day of the storm's life, as the producer writes it,
            # so whichever frame the map opens on, that day exists
            "storms": [{"id": TC["id"], "title": TC["title"], "name": "Fieldwide",
                        "best_peak_kt": 70.0, "best_category": "1", "era_peak_kt": 70,
                        "ratio": 1.0,
                        "days": [{"d": d, "max_kt": 70, "lat0": LAT0, "lon0": LON0,
                                  "ny": NY, "nx": NX, "rle": TW.rle_grid(_field())}
                                 for d in DAYS]}]}


def _fixture(tmpdir, with_wind=True):
    """The global-design fixture (raw_exceedance + timeline, so the merged map
    mounts), plus one cyclone TRACK entry on the timeline and — unless asked
    otherwise — the wind-field asset beside it."""
    root = _build_gd_fixture(str(tmpdir), raw_frames=FRAMES, raw_cutoff=FRAMES[-1])
    out = os.path.join(root, "data", "results")
    tl = json.load(open(os.path.join(out, "global_timeline.json")))
    entry = TL._track_entry(TC)
    assert entry and entry["track"]["fixes"][0][0] == "2026010200"
    tl["events"].append(entry)
    json.dump(tl, open(os.path.join(out, "global_timeline.json"), "w"))
    sel = os.path.join(out, "events_selected.json")
    evs = json.load(open(sel)) if os.path.exists(sel) else []
    evs.append(dict(TC))
    json.dump(evs, open(sel, "w"))
    if with_wind:
        json.dump(_wind_doc(), open(os.path.join(out, "tc_wind.json"), "w"))
    return root


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(root):
    handler = lambda *a, **k: _Quiet(*a, directory=root, **k)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="module")
def pw():
    # ONE Playwright context for the module: the sync API keeps a thread-local
    # loop, so a second start() while a page from an earlier one lives is an error
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def wind_server(tmp_path_factory):
    srv, url = _serve(_fixture(tmp_path_factory.mktemp("tw")))
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def nowind_server(tmp_path_factory):
    srv, url = _serve(_fixture(tmp_path_factory.mktemp("tw0"), with_wind=False))
    yield url
    srv.shutdown()


# The violet family, blend-tolerant: a 1° cell is ~2 px on a world sheet, so most
# field pixels are anti-aliased against the map under them. Returns the pixel set,
# not just a count — the A/B difference is what proves the field is painted.
COUNT_JS = """() => {
  const cv = document.querySelector('#gm-tracks');
  if (!cv || !cv.getContext) return {err: 'no canvas'};
  const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
  const pts = [];
  for (let p = 0, i = 3; i < d.length; p++, i += 4) {
    const [r, g, b] = [d[i-3], d[i-2], d[i-1]];
    if (d[i] > 120 && b > r + 18 && r > g + 8 && b > g + 40) {
      pts.push((p % cv.width) + ',' + Math.floor(p / cv.width));
    }
  }
  const r2 = cv.getBoundingClientRect();
  const dots = [...document.querySelectorAll('#gm-track-marks [data-id]')]
    .map((m) => { const b = m.getBoundingClientRect();
      return [b.x + b.width / 2, b.y + b.height / 2]; });
  return {n: pts.length, pts, rect: [r2.x, r2.y, r2.width, r2.height],
          dots, dbg: window.EwbTracks ? window.EwbTracks._dbg() : null,
          marks: dots.length};
}"""


def _open(browser, url, block_asset=False):
    pg = browser.new_page(viewport={"width": 1400, "height": 1000})
    pg.errors = []                                            # type: ignore[attr-defined]
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))    # type: ignore[attr-defined]
    if block_asset:                       # the A/B: a build where it never landed
        pg.route("**/tc_wind.json", lambda r: r.abort())
    pg.goto(url + "/index.html")
    pg.wait_for_selector("#gm-track-marks [data-id]", timeout=20000)
    pg.wait_for_timeout(1500)             # the wind fetch + the repaint it triggers
    return pg


def _violet(pg):
    got = pg.evaluate(COUNT_JS)
    assert not got.get("err"), got
    return got


def test_the_field_is_painted_and_only_by_the_asset(pw, wind_server):
    b = pw.chromium.launch()
    witha = _open(b, wind_server)
    got = _violet(witha)
    assert got["dbg"]["wind"] is True and got["dbg"]["windFailed"] is False
    # the field was seen in both classes it holds, and hit-testing agrees with paint
    assert got["dbg"]["windSeen"][1] and got["dbg"]["windSeen"][2]
    blocked = _violet(_open(b, wind_server, block_asset=True))
    assert blocked["dbg"]["windFailed"] is True
    extra = set(got["pts"]) - set(blocked["pts"])
    assert len(extra) > 30, f"field added only {len(extra)} px"
    # the extra pixels are the storm's own neighbourhood: the hit test says so
    xs = sorted(int(p.split(',')[0]) for p in extra)
    ys = sorted(int(p.split(',')[1]) for p in extra)
    x, y = xs[len(xs) // 2], ys[len(ys) // 2]
    hit = witha.evaluate("([x,y]) => window.EwbTracks._at(x, y)",
                         [got["rect"][0] + x, got["rect"][1] + y])
    assert hit and hit["id"] == TC["id"], hit
    assert hit["name"] == "Fieldwide" and hit["d"] in DAYS
    assert hit["cid"] in (1, 2) and hit["kt"] >= 34
    assert not witha.errors, witha.errors
    witha.close(); b.close()


def test_legend_names_the_field_and_says_it_under_reads(pw, wind_server):
    b = pw.chromium.launch(); pg = _open(b, wind_server)
    g = pg.locator('#gm-legend .lg-group[data-track="tropical_cyclone"]')
    assert g.count() == 1
    assert "ERA5 10 m wind" in g.locator(".lg-unit").inner_text()
    assert "under-reads" in g.locator(".lg-unit").inner_text()
    assert "under-reads" in g.get_attribute("title")
    labs = pg.eval_on_selector_all(
        '#gm-legend .lg-group[data-track="tropical_cyclone"] .lg-bin',
        "els => els.map(e => e.querySelector('.lg-t').textContent)")
    assert labs == ["TS", "C1", "C2", "C3", "C4", "C5", "track"]
    titles = pg.eval_on_selector_all(
        '#gm-legend .lg-group[data-track="tropical_cyclone"] .swatch',
        "els => els.map(e => e.title)")
    assert any("34–63 kt" in t and "39–72 mph" in t for t in titles)
    # classes this step does not show are dimmed, exactly like the raster lanes
    outs = pg.eval_on_selector_all(
        '#gm-legend .lg-group[data-track="tropical_cyclone"] .lg-bin',
        "els => els.map(e => e.classList.contains('out'))")
    assert outs[:2] == [False, False] and outs[2:6] == [True, True, True, True]
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_hover_answers_with_the_field_and_the_best_track(pw, wind_server):
    b = pw.chromium.launch()
    got = _violet(_open(b, wind_server))
    pg = _open(b, wind_server)
    only = set(got["pts"]) - set(_violet(_open(b, wind_server, block_asset=True))["pts"])
    # a marker on top answers with the storm's own card (by design: the dot is what
    # the generous click target exists for), so hover a field pixel clear of it
    dots = [(x - got["rect"][0], y - got["rect"][1]) for x, y in got["dbg"]["dots"]]

    def far(q):
        x, y = (int(v) for v in q.split(","))
        return all(abs(x - dx) > 12 or abs(y - dy) > 12 for dx, dy in dots)

    clear = sorted(tuple(int(v) for v in q.split(",")) for q in only if far(q))
    assert clear, "no field pixel away from a marker"
    x, y = clear[len(clear) // 2]
    pg.mouse.move(got["rect"][0] + x, got["rect"][1] + y)
    pg.wait_for_timeout(300)
    tip = pg.evaluate("""() => { const t = document.querySelector('#tooltip');
        return t.classList.contains('hidden') ? '' : t.innerText; }""")
    assert "ERA5 10 m wind" in tip, tip
    assert "best-track peak" in tip, tip          # the two numbers stay separate
    assert "Fieldwide" in tip, tip
    assert "never the official category" in tip, tip
    pg.mouse.move(5, 5)                            # and gives the tooltip back
    pg.wait_for_timeout(250)
    assert pg.evaluate("() => document.querySelector('#tooltip').classList.contains('hidden')")
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_no_asset_no_field_but_the_tracks_still_draw(pw, nowind_server, wind_server):
    b = pw.chromium.launch()
    pg = _open(b, nowind_server)
    got = _violet(pg)
    assert got["dbg"]["wind"] is False and got["dbg"]["windFailed"] is True
    assert got["marks"] > 0                         # the best track is still there
    blocked = _violet(_open(b, wind_server, block_asset=True))
    assert abs(got["n"] - blocked["n"]) <= 5        # nothing shaded, nothing invented
    unit = pg.locator('#gm-legend .lg-group[data-track="tropical_cyclone"] .lg-unit')
    assert unit.count() == 1
    assert "ERA5 10 m wind" not in unit.inner_text()
    assert not pg.errors, pg.errors                 # a missing asset is not an exception
    pg.close(); b.close()


def test_screenshots(pw, wind_server, nowind_server):
    os.makedirs(SHOTS, exist_ok=True)
    b = pw.chromium.launch()
    pg = _open(b, wind_server)
    box = pg.locator("#raw-map-wrap").bounding_box()
    pg.screenshot(path=os.path.join(SHOTS, "wind-field-light.png"),
                  clip={**box, "height": min(520, box["height"])})
    pg.evaluate("() => document.documentElement.setAttribute('data-theme','dark')")
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(SHOTS, "wind-field-dark.png"),
                  clip={**box, "height": min(520, box["height"])})
    pg.close()
    pg = _open(b, nowind_server)
    pg.screenshot(path=os.path.join(SHOTS, "wind-field-absent.png"),
                  clip={**pg.locator("#raw-map-wrap").bounding_box(), "height": 520})
    pg.close(); b.close()
