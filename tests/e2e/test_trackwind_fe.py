"""The cyclone WIND FIELD on the merged map, in a real browser (tc lane).

Covers what the unit tests cannot: the map must fetch tc_wind.json and paint it
under the tracks, the legend must explain the ramp *and say the field is
reanalysis that under-reads*, hovering a cell must answer with the wind, the
storm, the day and the best-track peak in separate rows, and a build that has not
produced the asset yet must lose the field and keep the tracks — with no JS error
and no invented shading.

It also covers the STEP contract (owner, 2026-11-19: the field is the INSTANT
wind of the displayed hour): schema 3 stores one grid per live 6-hourly step and
a frame paints ONLY its own step's grid — a day whose 00Z step the storm did not
live through paints nothing at 00Z even when 12Z has a field. And the DATELINE
contract: a storm whose field straddles ±180 must paint on both edges of the
sheet, with the hit test answering for the wrapped cells.

It also covers the DISSIPATION contract (owner, 2026-11-18: "make sure the TCs
dissipate after the storm ends"): a second storm whose life ends at 06Z on 3 Jan
is shaded at the 00Z step, gone at 06Z — field, cell tooltip and hit test alike —
and back when the slider is pulled back, while a still-living storm next door
keeps its field throughout. The marker goes hollow, not absent: the track is
history.

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

# 12 six-hourly steps: 2-3 Jan and the first three steps of 4 Jan
FRAMES = [f"202601{d:02d}{h:02d}" for d in (2, 3, 4) for h in (0, 6, 12, 18)
          if not (d == 4 and h == 18)]
DAYS = ["2026-01-02", "2026-01-03", "2026-01-04"]        # storm A's whole life
# Both windows are deliberately BIG (±12°, a 25×25 cell grid): the world sheet in
# the test viewport is ~675 px wide, so one degree is under 2 px, and a hover has
# to land on a field cell that is clear of the 15 px marker hit circle to test the
# field's own tooltip. The producer's own window cap is 12° for exactly this
# reason; a real storm's envelope is much smaller and is pinned in unit tests.
LAT0, LON0, NY, NX = 8.0, -162.0, 25, 25
FIX_LAT, FIX_LON = 20.0, -150.0                          # the field's centre
R34 = 11.0                                               # A's storm-force radius

# storm B exists to DISSIPATE: alive from 2 Jan 00Z to 3 Jan 06Z (exclusive), so
# the 00Z step of 3 Jan is the last frame that may shade it. A separate ocean box
# (3..17 N, 127..113 W) so its cells never touch storm A's.
BLAT0, BLON0, BNY, BNX = -2.0, -132.0, 25, 25
BFIX_LAT, BFIX_LON = 10.0, -120.0
ALIVE_FRAME, DEAD_FRAME = "2026010300", "2026010306"
ALL_STEPS = ["00Z", "06Z", "12Z", "18Z"]
ALL_H = [0, 6, 12, 18]

TC = {"id": "tww1", "event_type": "tropical_cyclone", "title": "TC Fieldwide (e2e)",
      "start": "2026-01-02T00:00:00", "end": "2026-01-05T00:00:00",
      "peak_value": 70.0, "bbox": [18.0, 22.0, -152.0, -148.0],
      "details": {"track": [["2026-01-02T00:00:00Z", FIX_LAT, FIX_LON, 60.0, 985.0],
                            ["2026-01-02T06:00:00Z", 20.5, -149.5, 70.0, 980.0],
                            ["2026-01-03T00:00:00Z", 21.0, -149.0, 55.0, 990.0]],
                  "intensity_units": "kt", "fix_step_h": 6, "name": "Fieldwide",
                  "basin_name": "EP", "peak_wind_kt": 70.0, "peak_category": "1",
                  "min_pressure_hpa": 980.0,
                  "ongoing": False, "track_crosses_antimeridian": False}}

FADE = {"id": "tww2", "event_type": "tropical_cyclone", "title": "TC Fadeaway (e2e)",
        "start": "2026-01-02T00:00:00", "end": "2026-01-03T06:00:00",
        "peak_value": 60.0, "bbox": [8.0, 12.0, -122.0, -118.0],
        "details": {"track": [["2026-01-02T00:00:00Z", BFIX_LAT, BFIX_LON, 55.0, 992.0],
                              ["2026-01-02T12:00:00Z", 10.5, -119.5, 60.0, 988.0],
                              ["2026-01-03T00:00:00Z", 11.0, -119.0, 50.0, 994.0],
                              # the ET tail: still blowing 45 kt after the window, and
                              # it must NOT be shaded in this storm's name
                              ["2026-01-03T18:00:00Z", 13.0, -116.0, 45.0, 998.0]],
                    "intensity_units": "kt", "fix_step_h": 6, "name": "Fadeaway",
                    "basin_name": "EP", "peak_wind_kt": 60.0, "peak_category": "TS",
                    "min_pressure_hpa": 988.0,
                    "ongoing": False, "track_crosses_antimeridian": False}}

# SEAM crosses the antimeridian on 2 Jan (fixes 175 E → 177 W), so its window's
# columns run RAW PAST +180: lon0 = 172, 25 columns reach 196, and every column
# past 180 belongs on the LEFT edge of the sheet. The grid is a plain 45 kt
# rectangle (the producer would mask it; here the mask is the producer's own
# unit-test problem) whose only job is to put field cells on both sides of the
# seam at once — the client must split them, not skip them, not smear a band.
SEAM = {"id": "tww3", "event_type": "tropical_cyclone", "title": "TC Seamcrosser (e2e)",
        "start": "2026-01-02T00:00:00", "end": "2026-01-03T00:00:00",
        "peak_value": 65.0, "bbox": [19.0, 21.0, 175.0, -177.0],
        "details": {"track": [["2026-01-02T00:00:00Z", 20.0, 175.0, 60.0, 985.0],
                              ["2026-01-02T06:00:00Z", 20.0, 177.0, 65.0, 980.0],
                              ["2026-01-02T12:00:00Z", 20.0, -179.0, 62.0, 983.0],
                              ["2026-01-02T18:00:00Z", 20.0, -177.0, 55.0, 990.0]],
                    "intensity_units": "kt", "fix_step_h": 6, "name": "Seamcrosser",
                    "basin_name": "WP", "peak_wind_kt": 65.0, "peak_category": "1",
                    "min_pressure_hpa": 980.0,
                    "ongoing": False, "track_crosses_antimeridian": True}}
SLAT0, SLON0, SNY, SNX = 14.0, 172.0, 13, 25          # columns 172…196 (raw)


def _field():
    """Storm force inside R34 (a large cyclone) with a CAT 1 core at the centre,
    packed exactly as the producer packs it — rle over the 1° lattice. The field
    reaches far past the marker's own claim on the pointer, which is what the
    hover test needs."""
    g = np.zeros((NY, NX), np.uint8)
    i0, j0 = int(round(FIX_LAT - LAT0)), int(round(FIX_LON - LON0))
    assert (i0, j0) == (12, 12)                           # the fix is the centre
    ii, jj = np.mgrid[0:NY, 0:NX]
    g[(ii - i0) ** 2 + (jj - j0) ** 2 <= R34 ** 2] = 45   # tropical-storm force
    g[i0, j0] = 70                                        # CAT 1 core
    return g


def _grids(g, hours, ny, nx):
    """One day entry's step grids, exactly as the producer writes them: the SAME
    rle at every requested hour, geometry shared by the whole day."""
    r = TW.rle_grid(g)
    return [{"h": h, "label": f"{h:02d}Z", "max_kt": int(g.max()), "rle": r}
            for h in hours]


def _wind_doc():
    """The asset exactly as tc_wind.py writes it (schema 3): the class scale, the
    measured bias, the dissipation rule as data, and per storm a `life` window
    plus ONE GRID PER LIVE 6-HOURLY STEP grouped by UTC day."""
    fa, fb, fs = _field(), _field_fade(), _field_seam()
    # cells per class summed over the STEP grids each storm actually carries:
    # TC 9 grids (4+4+1), FADE 5 (4+1), SEAM 4 — the producer's census, honestly
    census = {"1": int((fa == 45).sum()) * 9 + int((fb == 45).sum()) * 5
                  + int((fs == 45).sum()) * 4,
              "2": int((fa == 70).sum()) * 9, "3": 0, "4": 0, "5": 0, "6": 0}
    life = lambda ev: {                                  # noqa: E731
        "birth": ev["start"] + "Z", "death": ev["end"] + "Z",
        "last_fix": ev["details"]["track"][-2][0],       # the ET tail is not life
        "first_fix": ev["details"]["track"][0][0],
        "ongoing": ev["details"]["ongoing"], "alive_steps": 0}
    return {"schema": 3, "lane": "tropical_cyclone",
            "source": "ERA5 10 m wind (10m_u/v_component_of_wind), ERA5 ARCO 0.25°, "
                      "00/06/12/18Z), the one 6-hourly step the map is showing",
            "cadence_h": 6, "step_deg": 1.0, "read_deg": 0.25, "half_deg": 5.0,
            "floor_kt": 34.0, "units": "kt", "classes": TW.CLASSES,
            "dissipation": {"window": "the lane's event window: first to last "
                                      "qualifying fix >= 34 kt, NATURE not ET/MX",
                            "steps": "only the 00/06/12/18Z steps inside it",
                            "keep_deg": TW.KEEP_DEG, "core_deg": TW.CORE_DEG,
                            "attribution": "nearest live centre, then the storm's "
                                           "own connected >=34 kt envelope"},
            "bias": {"ratio_median": 0.58, "ratio_min": 0.34, "ratio_max": 0.9,
                     "n": 2, "note": "ERA5 10 m wind under-reads cyclone peaks."},
            "census": census, "days_no_data": [],
            "storms": [
                {"id": TC["id"], "title": TC["title"], "name": "Fieldwide",
                 "best_peak_kt": 70.0, "best_category": "1", "era_peak_kt": 70,
                 "ratio": 1.0, "life": life(TC),
                 # one entry per UTC day of the storm's life; 2/3 Jan hold all
                 # four step grids, 4 Jan holds ONLY the 12Z one — the frame↔step
                 # contract test paints it at 12Z and nothing at 00Z/06Z
                 "days": [{"d": d, "max_kt": 70,
                           "lat0": LAT0, "lon0": LON0, "ny": NY, "nx": NX,
                           "grids": _grids(fa, ALL_H if d != "2026-01-04" else [12],
                                           NY, NX)} for d in DAYS]},
                {"id": FADE["id"], "title": FADE["title"], "name": "Fadeaway",
                 "best_peak_kt": 60.0, "best_category": "TS", "era_peak_kt": 45,
                 "ratio": 0.75, "life": life(FADE),
                 # 3 Jan holds ONE grid (00Z): the storm's life ends at 06Z, so
                 # the day carries only the hour it lived — no 06Z grid exists to
                 # linger at the dead frame
                 "days": [{"d": "2026-01-02", "max_kt": 45,
                           "lat0": BLAT0, "lon0": BLON0, "ny": BNY, "nx": BNX,
                           "grids": _grids(fb, ALL_H, BNY, BNX)},
                          {"d": "2026-01-03", "max_kt": 45,
                           "lat0": BLAT0, "lon0": BLON0, "ny": BNY, "nx": BNX,
                           "grids": _grids(fb, [0], BNY, BNX)}]},
                {"id": SEAM["id"], "title": SEAM["title"], "name": "Seamcrosser",
                 "best_peak_kt": 65.0, "best_category": "1", "era_peak_kt": 45,
                 "ratio": 0.7, "life": life(SEAM),
                 # the window crosses +180 as RAW columns (lon0=172, 25 columns):
                 # the client wraps every past-180 column to the opposite edge
                 "days": [{"d": "2026-01-02", "max_kt": 45,
                           "lat0": SLAT0, "lon0": SLON0, "ny": SNY, "nx": SNX,
                           "grids": _grids(fs, ALL_H, SNY, SNX)}]}]}


def _field_fade():
    """Storm force across a ±12° window, filled (no disc): the field must reach
    well past Fadeaway's own marker so a cell can be hovered away from it, and the
    SW corner is the probe the dissipation test uses."""
    return np.full((BNY, BNX), 45, np.uint8)


def _field_seam():
    """Seamcrosser's whole window at tropical-storm force: columns 172…196 raw,
    so the last 17 columns wrap to the far edge and a painted seam is plain to
    see (and hit-test) at both ends of the sheet."""
    return np.full((SNY, SNX), 45, np.uint8)


def _fixture(tmpdir, with_wind=True):
    """The global-design fixture (raw_exceedance + timeline, so the merged map
    mounts), plus the two cyclone TRACK entries on the timeline and — unless asked
    otherwise — the wind-field asset beside it."""
    root = _build_gd_fixture(str(tmpdir), raw_frames=FRAMES, raw_cutoff=FRAMES[-1])
    out = os.path.join(root, "data", "results")
    tl = json.load(open(os.path.join(out, "global_timeline.json")))
    for spec in (TC, FADE, SEAM):
        entry = TL._track_entry(spec)
        assert entry and entry["track"]["fixes"][0][0] == "2026010200"
        tl["events"].append(entry)
    json.dump(tl, open(os.path.join(out, "global_timeline.json"), "w"))
    sel = os.path.join(out, "events_selected.json")
    evs = json.load(open(sel)) if os.path.exists(sel) else []
    evs += [dict(TC), dict(FADE), dict(SEAM)]
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
    # the extra pixels are the storms' own neighbourhoods: the hit test answers for
    # a cell probed at a known lat/lon in each field (not an arbitrary pixel)
    for spec, (la, lo) in ((TC, (FIX_LAT + 3, FIX_LON + 4)),
                           (FADE, (BFIX_LAT - 4, BFIX_LON - 4))):
        hit = _probe(witha, la, lo)
        assert hit["hit"] and hit["hit"]["id"] == spec["id"], hit
        assert hit["hit"]["kt"] >= 34 and hit["hit"]["cid"] in (1, 2)
        assert hit["hit"]["d"] in ("2026-01-02", "2026-01-03", "2026-01-04")
    assert not witha.errors, witha.errors
    witha.close(); b.close()


def test_legend_names_the_field_and_says_it_under_reads(pw, wind_server):
    b = pw.chromium.launch(); pg = _open(b, wind_server)
    g = pg.locator('#gm-legend .lg-group[data-track="tropical_cyclone"]')
    assert g.count() == 1
    assert "ERA5 10 m wind" in g.locator(".lg-unit").inner_text()
    assert "displayed hour" in g.locator(".lg-unit").inner_text()
    assert "under-reads" in g.locator(".lg-unit").inner_text()
    assert "under-reads" in g.get_attribute("title")
    assert "not a maximum" in g.get_attribute("title")   # instantaneous, in words
    labs = pg.eval_on_selector_all(
        '#gm-legend .lg-group[data-track="tropical_cyclone"] .lg-bin',
        "els => els.map(e => e.querySelector('.lg-t').textContent)")
    assert labs == ["TS", "C1", "C2", "C3", "C4", "C5", "track", "dissipated"]
    assert pg.locator('#gm-legend .lg-group[data-track="tropical_cyclone"] '
                     + '.lg-bin .swatch.lg-gone').count() == 1
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
    pg = _open(b, wind_server)
    # a marker on top answers with the storm's own card (by design: the dot is what
    # the generous click target exists for), so hover a field cell clear of every
    # marker's hit circle — the biggest clearance the candidate points allow
    cand = [[FIX_LAT + 3, FIX_LON + 8], [FIX_LAT + 8, FIX_LON + 3],
            [FIX_LAT - 3, FIX_LON + 8], [FIX_LAT - 8, FIX_LON + 3],
            [FIX_LAT + 3, FIX_LON - 8], [FIX_LAT - 6, FIX_LON - 6]]
    spot = pg.evaluate(CLEAR_JS, cand)
    assert spot and spot["hit"] and spot["hit"]["id"] == TC["id"], spot
    pg.mouse.move(spot["x"], spot["y"])
    pg.wait_for_timeout(300)
    tip = _tip(pg)
    assert "ERA5 10 m wind" in tip, tip
    assert "best-track peak" in tip, tip          # the two numbers stay separate
    assert "Fieldwide" in tip, tip
    assert "never the official category" in tip, tip
    assert "instantaneous" in tip, tip            # the row the owner asked for
    # the hour row names the frame's OWN step (date + step label of this frame)
    why = pg.evaluate("() => window.EwbTracks._why(10, 10)")
    assert why["date"] in tip, (tip, why)
    assert f"{why['hour']:02d}Z" in tip, (tip, why)
    pg.mouse.move(5, 5)                            # and gives the tooltip back
    pg.wait_for_timeout(250)
    assert pg.evaluate("() => document.querySelector('#tooltip').classList.contains('hidden')")
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def _goto(pg, frame):
    """Move the map's time slider to a frame ('YYYYMMDDHH') the way the UI does."""
    ok = pg.evaluate("""(f) => {
      const d = window.MergedMap._dbg();
      const i = d.frames.indexOf(f);
      if (i < 0) return false;
      const s = document.querySelector('#raw-slider');
      s.value = String(i); s.dispatchEvent(new Event('input', {bubbles: true}));
      return window.MergedMap._dbg().idx === i;
    }""", frame)
    assert ok, f"no frame {frame} on the slider"
    pg.wait_for_timeout(400)


# page coordinates of a lat/lon on the map, plus what the wind hit test says there
# (note the argument order: PROBE is given [lat, lon] and the map's projection
# wants [lon, lat] — a silent wrong answer if you forget)
PROBE_JS = """([lat, lon]) => {
  const d = window.MergedMap._dbg();
  const cv = document.querySelector('#gm-tracks');
  const r = cv.getBoundingClientRect();
  const p = d.project([lon, lat]);
  const k = r.width / (parseFloat(cv.style.width) || r.width);
  if (!p) return {err: 'no projection'};
  const x = r.x + p[0] * k, y = r.y + p[1] * k;
  const hit = window.EwbTracks._at(x, y);
  return {x, y, hit: hit && {id: hit.id, kt: hit.kt, cid: hit.cid, d: hit.d}};
}"""


def _probe(pg, lat, lon):
    got = pg.evaluate(PROBE_JS, [lat, lon])
    assert not got.get("err"), got
    return got


# the first of a list of map points that is clear of every storm marker (whose hit
# circle owns the pointer), with the field hit test there
CLEAR_JS = """(pts) => {
  const cv = document.querySelector('#gm-tracks');
  const r = cv.getBoundingClientRect(), d = window.MergedMap._dbg();
  const k = r.width / (parseFloat(cv.style.width) || r.width);
  const rings = [...document.querySelectorAll('#gm-track-marks .gm-mark-ring')]
    .map((m) => [r.x + (+m.getAttribute('cx')) * k, r.y + (+m.getAttribute('cy')) * k]);
  for (const [lat, lon] of pts) {
    const p = d.project([lon, lat]);
    if (!p) continue;
    const x = r.x + p[0] * k, y = r.y + p[1] * k;
    if (rings.every(([qx, qy]) => Math.hypot(x - qx, y - qy) > 16))
      return {x, y, hit: window.EwbTracks._at(x, y)};
  }
  return null;
}"""


def _tip(pg):
    return pg.evaluate("""() => { const t = document.querySelector('#tooltip');
        return t.classList.contains('hidden') ? '' : t.innerText; }""")


def test_the_wind_field_dissipates_when_the_storm_ends(pw, wind_server):
    """The owner's addition, in the browser: fade the field out of existence with
    the storm, and bring it back when the slider goes back."""
    b = pw.chromium.launch()
    pg = _open(b, wind_server)
    corner = (BLAT0 + 1, BLON0 + 1)          # a cell of Fadeaway's field, ~9° SW of
    # its own marker, so the marker's hit circle cannot be what answers

    _goto(pg, ALIVE_FRAME)
    assert pg.evaluate("(i) => window.EwbTracks._life(i)", FADE["id"])["field"] is True
    alive = _probe(pg, *corner)
    assert alive["hit"] and alive["hit"]["id"] == FADE["id"], alive
    n_alive = _violet(pg)["n"]
    pg.mouse.move(alive["x"], alive["y"]); pg.wait_for_timeout(300)
    tip = _tip(pg)
    assert "ERA5 10 m wind" in tip and "Fadeaway" in tip, tip
    pg.screenshot(path=os.path.join(SHOTS, "tc_dissipate_last_day.png"),
                  clip={**pg.locator("#raw-map-wrap").bounding_box(), "height": 520})

    # one step later the storm is over: no shading, no cell tooltip, no hit test —
    # and the neighbouring storm, still alive, keeps every one of its own cells
    _goto(pg, DEAD_FRAME)
    life = pg.evaluate("(i) => window.EwbTracks._life(i)", FADE["id"])
    assert life["alive"] is False and life["dissipated"] is True, life
    assert life["field"] is False, life
    dead = _probe(pg, *corner)
    assert dead["hit"] is None, dead
    assert _violet(pg)["n"] < n_alive - 30, "the field lingered after the storm ended"
    pg.mouse.move(alive["x"], alive["y"]); pg.wait_for_timeout(300)
    assert "ERA5 10 m wind" not in _tip(pg), "a dead storm's cell still answers"
    assert pg.evaluate("(i) => window.EwbTracks._life(i)", TC["id"])["field"] is True
    assert (_probe(pg, FIX_LAT + 1, FIX_LON + 1)["hit"] or {}).get("id") == TC["id"]
    # the marker stays (where the storm has been is history) but says it is gone
    state = pg.evaluate("""(id) => { const m = document.querySelector(
          `#gm-track-marks [data-id="${id}"]`);
        return m && {state: m.dataset.state, aria: m.getAttribute('aria-label')}; }""",
        FADE["id"])
    assert state and state["state"] == "dissipated", state
    assert "dissipated" in state["aria"], state
    pg.screenshot(path=os.path.join(SHOTS, "tc_dissipate_after.png"),
                  clip={**pg.locator("#raw-map-wrap").bounding_box(), "height": 520})

    # and the field is not sticky in the other direction either: scrub back and it
    # is all there again, identical to the first visit
    _goto(pg, ALIVE_FRAME)
    assert _probe(pg, *corner)["hit"]["id"] == FADE["id"]
    assert abs(_violet(pg)["n"] - n_alive) <= 3
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_a_frame_paints_only_its_own_step_grid(pw, wind_server):
    """Schema 3 (owner, 2026-11-19: "instantaneous 10 m wind at the displayed
    hour, NOT the daily max"): Fieldwide's 4 Jan carries ONLY a 12Z grid. The
    00Z frame — with the storm very much alive — must draw and answer nothing;
    the 12Z frame draws and answers everything. A day entry existing is no
    longer the same thing as a frame having a field."""
    b = pw.chromium.launch()
    pg = _open(b, wind_server)
    corner = (FIX_LAT + 3, FIX_LON + 4)           # a cell of Fieldwide's field
    _goto(pg, "2026010400")
    life = pg.evaluate("(i) => window.EwbTracks._life(i)", TC["id"])
    assert life["alive"] is True and life["field"] is False, life
    assert _probe(pg, *corner)["hit"] is None
    n_empty = _violet(pg)["n"]

    _goto(pg, "2026010412")
    life = pg.evaluate("(i) => window.EwbTracks._life(i)", TC["id"])
    assert life["alive"] is True and life["field"] is True, life
    hit = _probe(pg, *corner)["hit"]
    assert hit and hit["id"] == TC["id"] and hit["kt"] >= 34, hit
    assert _violet(pg)["n"] > n_empty + 30, "the 12Z step painted no more than the empty 00Z"
    why = pg.evaluate("(p) => window.EwbTracks._why(p[0], p[1])", [10, 10])
    assert why["date"] == "2026-01-04" and why["hour"] == 12 and why["fields"] >= 1, why
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_a_crossing_field_paints_both_sides_of_the_seam(pw, wind_server):
    """The dateline contract on the wind field: Seamcrosser's window columns run
    RAW past +180, so the wrapped columns must paint at the LEFT edge (the only
    place lon 180…196 can live), the raw ones at the right edge, and the hit
    test must answer for the wrapped cells — nothing skipped, no full-band
    smear across the middle of the sheet."""
    b = pw.chromium.launch()
    pg = _open(b, wind_server)
    _goto(pg, "2026010212")                       # alive all of 2 Jan
    witha = _violet(pg)
    assert witha["dbg"]["wind"] is True
    blocked_pg = _open(b, wind_server, block_asset=True)
    _goto(blocked_pg, "2026010212")               # SAME frame: the diff is the field
    blocked = _violet(blocked_pg)
    blocked_pg.close()
    canvas_w = witha["rect"][2]
    edge = 0.10 * canvas_w
    extra = set(witha["pts"]) - set(blocked["pts"])
    left = sum(1 for p in extra if int(p.split(",")[0]) < edge)
    right = sum(1 for p in extra if int(p.split(",")[0]) > canvas_w - edge)
    assert left > 50, f"wrapped columns painted {left} px at the left edge"
    assert right > 50, f"raw columns painted {right} px at the right edge"
    # the middle of the sheet (lon ≈ -54…+54) holds NO test field: a full-band
    # degrade (or a smear) would light it up — it must stay pixel-identical
    mid = sum(1 for p in extra
              if 0.35 * canvas_w < int(p.split(",")[0]) < 0.65 * canvas_w)
    assert mid == 0, f"{mid} extra px in the empty middle — the band did not split"
    # the wrapped cells hit-test on the left: lon -172 (= raw 188, column 16)
    hit = _probe(pg, 20.0, -172.0)
    assert hit["hit"] and hit["hit"]["id"] == SEAM["id"], hit
    assert hit["hit"]["kt"] >= 34
    # and the raw ones on the right: lon 174 (column 2)
    hit2 = _probe(pg, 20.0, 174.0)
    assert hit2["hit"] and hit2["hit"]["id"] == SEAM["id"], hit2
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
