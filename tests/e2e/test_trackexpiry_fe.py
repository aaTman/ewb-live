"""Track expiry e2e (visual-tracks, 2026-11-19): nothing of a cyclone may stay
on the map after its life (owner, emphasised).

Two layers of proof:

1. THE SYNTHETIC SWEEP (runs anywhere Playwright's chromium is installed): five
   track events built to cover every expiry shape — plain end, post-tropical
   tail past the end, MLC with end past the last fix, MLC with the last fix
   past the end, and the one that used to linger forever: a storm the producer
   still flags `life.ongoing` (dissipation unconfirmed at the data edge) whose
   fixes STOP before the window does. The old rule (`ongoing` short-circuited
   expiredAt) kept its ring, label and whole line drawn through every frame
   after its last fix. The rule under test, asserted for every event and every
   tab/region:

       a storm draws for frames  first_fix <= t <= max(last_fix, end)
       an ongoing storm draws for  first_fix <= t <= last_fix  only,
       and EVERY paint path obeys it: ring + dot + label (SVG #gm-track-marks),
       line + fix dots + wind field (the #gm-tracks canvas), the hover-preview
       ghost, and the field's own life gate.

   The window ends after every cap, so the last frame proves the canvas holds
   ZERO opaque track-layer pixels — the whole layer, not just the DOM.

2. THE PUBLISHED-DATA SWEEP (skipped when the repo has no real results): walks
   every tropical_cyclone / midlat_cyclone entry of the actual
   data/results/global_timeline.json (124 events in the 2026-11-19 build, one
   flagged ongoing — storm a1d6b391e4d8, the measured failure) to its first
   frame past the cap and asserts the same zero, exactly as the browser shows
   it. Run against a local preview:  EWB_LIVE_URL=http://127.0.0.1:8063.

Run: .venv/bin/python -m pytest tests/e2e/test_trackexpiry_fe.py -m e2e
"""
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIVE_URL = os.environ.get("EWB_LIVE_URL", "http://127.0.0.1:8063").rstrip("/")
SHOTS = os.path.join(ROOT, "notes", "screenshots")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from ewb_live.viz import tc_wind as TW                                      # noqa: E402
from ewb_live.viz import timeline as TL                                     # noqa: E402

from test_dashboard_fe import _build_gd_fixture                             # noqa: E402

# the slider: 1 Jan 00Z .. 6 Jan 00Z, 6-hourly. Every event's cap (below) lands
# at or before 5 Jan 00Z, so the WINDOW-END frame is pure after-life.
FRAMES = ([f"202601{d:02d}{h:02d}" for d in range(1, 6) for h in (0, 6, 12, 18)]
          + ["2026010600"])

# --- five storms, five expiry shapes (lat/lon boxes kept far apart so no two
#     storms' pixels can ever be confused for one another) ---
TC1 = {"id": "xp1", "event_type": "tropical_cyclone", "title": "Alpha (e2e)",
       "start": "2026-01-01T06:00:00", "end": "2026-01-02T18:00:00",
       "peak_value": 65.0, "bbox": [8.0, 18.0, -142.0, -130.0],
       "details": {"track": [["2026-01-0" + d + "T" + h + ":00:00Z", la, lo, w, 990.0]
                             for d, h, la, lo, w in
                             (("1", "06", 10.0, -140.0, 40.0), ("1", "12", 10.5, -139.5, 55.0),
                              ("1", "18", 11.0, -139.0, 65.0), ("2", "00", 11.5, -138.5, 60.0),
                              ("2", "06", 12.0, -138.0, 50.0), ("2", "12", 12.5, -137.5, 40.0))],
                   "intensity_units": "kt", "fix_step_h": 6, "name": "Alpha",
                   "basin_name": "EP", "peak_wind_kt": 65.0, "peak_category": "1",
                   "min_pressure_hpa": 985.0, "ongoing": False,
                   "track_crosses_antimeridian": False}}
# Bravo: the post-tropical TAIL runs 30 h past the published end — inside the
# rule, its marks stay with the tail (owner: keep the tail ring) and must leave
# after the LAST FIX, not after the end.
TAIL_LAST = ["2026-01-04T06:00:00Z", 26.0, -118.0, 40.0, 998.0]
TC2 = {"id": "xp2", "event_type": "tropical_cyclone", "title": "Bravo (e2e)",
       "start": "2026-01-01T12:00:00", "end": "2026-01-03T00:00:00",
       "peak_value": 70.0, "bbox": [20.0, 28.0, -126.0, -114.0],
       "details": {"track": [["2026-01-01T12:00:00Z", 20.0, -124.0, 60.0, 988.0],
                             ["2026-01-02T00:00:00Z", 22.0, -122.0, 70.0, 985.0],
                             ["2026-01-03T00:00:00Z", 24.0, -120.0, 45.0, 995.0],
                             ["2026-01-04T00:00:00Z", 25.0, -119.0, 42.0, 997.0],
                             TAIL_LAST],
                   "intensity_units": "kt", "fix_step_h": 6, "name": "Bravo",
                   "basin_name": "EP", "peak_wind_kt": 70.0, "peak_category": "1",
                   "min_pressure_hpa": 985.0, "ongoing": False,
                   "track_crosses_antimeridian": False}}
MLC1 = {"id": "xp3", "event_type": "midlat_cyclone", "title": "Charla (e2e)",
        "start": "2026-01-02T00:00:00", "end": "2026-01-04T12:00:00",
        "peak_value": 968.0, "bbox": [42.0, 50.0, -40.0, -24.0],
        "details": {"track": [["2026-01-02T00:00:00Z", 43.0, -38.0, 985.0, 985.0],
                              ["2026-01-03T00:00:00Z", 46.0, -32.0, 970.0, 970.0],
                              ["2026-01-04T00:00:00Z", 48.0, -27.0, 968.0, 968.0]],
                    "intensity_units": "hPa", "fix_step_h": 6, "name": "", "basin_name": "",
                    "ongoing": False, "track_crosses_antimeridian": False}}
MLC2 = {"id": "xp4", "event_type": "midlat_cyclone", "title": "Dora (e2e)",
        "start": "2026-01-02T06:00:00", "end": "2026-01-04T00:00:00",
        "peak_value": 972.0, "bbox": [-58.0, -50.0, 20.0, 40.0],
        "details": {"track": [["2026-01-02T06:00:00Z", -56.0, 22.0, 990.0, 990.0],
                              ["2026-01-03T12:00:00Z", -54.0, 30.0, 972.0, 972.0],
                              ["2026-01-04T06:00:00Z", -52.0, 34.0, 980.0, 980.0],
                              ["2026-01-05T00:00:00Z", -51.0, 38.0, 995.0, 995.0]],
                    "intensity_units": "hPa", "fix_step_h": 6, "name": "", "basin_name": "",
                    "ongoing": False, "track_crosses_antimeridian": False}}
# THE regression shape (storm a1d6b391e4d8, 2026-11-19 build): the producer has
# not confirmed the dissipation — `life.ongoing` — the window is still open at
# the data edge, and the fixes simply STOP. Old code: the ongoing short circuit
# drew it forever. New rule: it draws up to its last fix and leaves there.
DELTA_LAST = ["2026-01-04T12:00:00Z", -2.0, 96.0, 55.0, 990.0]
TC3 = {"id": "xp5", "event_type": "tropical_cyclone", "title": "Delta (e2e)",
       "start": "2026-01-02T06:00:00", "end": "2026-01-05T00:00:00",
       "peak_value": 60.0, "bbox": [-6.0, 2.0, 92.0, 100.0],
       "details": {"track": [["2026-01-02T06:00:00Z", -4.0, 93.0, 40.0, 996.0],
                             ["2026-01-03T06:00:00Z", -3.0, 94.5, 55.0, 991.0],
                             ["2026-01-03T18:00:00Z", -2.5, 95.0, 60.0, 989.0],
                             DELTA_LAST],
                   "intensity_units": "kt", "fix_step_h": 6, "name": "Delta",
                   "basin_name": "SI", "peak_wind_kt": 60.0, "peak_category": "TS",
                   "min_pressure_hpa": 989.0, "ongoing": True,
                   "track_crosses_antimeridian": False}}

ALL = [TC1, TC2, MLC1, MLC2, TC3]

# every event's drawing cap and the first frame AFTER it (the frames the map
# must draw nothing at) — mirrors the rule in dashboard/tracklayer.js
CAPS = {"xp1": "2026-01-02T18:00:00Z",   # end (death) after the last fix
        "xp2": "2026-01-04T06:00:00Z",   # the post-tropical tail fix
        "xp3": "2026-01-04T12:00:00Z",   # end after the last fix (exclusive)
        "xp4": "2026-01-05T00:00:00Z",   # last fix after the end
        "xp5": "2026-01-04T12:00:00Z"}   # ongoing: the LAST FIX, not the end


def _grids(g, hours):
    r = TW.rle_grid(g)
    return [{"h": h, "label": f"{h:02d}Z", "max_kt": int(g.max()), "rle": r} for h in hours]


def _wind_doc():
    """schema-3 asset for Alpha and Delta only (the two fields the test paints);
    Bravo's life ends before its tail, Delta's grids STOP at its last fix — the
    producer never ships a step the storm did not live through."""
    fa = np.full((9, 9), 45, np.uint8)
    fd = np.full((9, 9), 45, np.uint8)
    life = lambda ev, ongoing: {                                          # noqa: E731
        "birth": ev["start"] + "Z", "death": ev["end"] + "Z",
        "first_fix": ev["details"]["track"][0][0],
        "last_fix": ev["details"]["track"][-1][0],
        "ongoing": ongoing, "alive_steps": 0}
    return {"schema": 3, "lane": "tropical_cyclone",
            "source": "ERA5 10 m wind (e2e fixture)",
            "cadence_h": 6, "step_deg": 1.0, "read_deg": 0.25, "half_deg": 5.0,
            "floor_kt": 34.0, "units": "kt", "classes": TW.CLASSES,
            "dissipation": {"window": "the lane's event window", "steps": "only live steps",
                            "keep_deg": TW.KEEP_DEG, "core_deg": TW.CORE_DEG,
                            "attribution": "fixture"},
            "bias": {"ratio_median": 0.58, "ratio_min": 0.34, "ratio_max": 0.9,
                     "n": 2, "note": "ERA5 10 m wind under-reads cyclone peaks."},
            "census": {"1": int((fa == 45).sum()) * 7, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0},
            "days_no_data": [],
            "storms": [
                {"id": "xp1", "title": TC1["title"], "name": "Alpha", "best_peak_kt": 65.0,
                 "best_category": "1", "era_peak_kt": 45, "ratio": 0.69, "life": life(TC1, False),
                 "days": [{"d": "2026-01-01", "max_kt": 45, "lat0": 6.0, "lon0": -144.0,
                           "ny": 9, "nx": 9, "grids": _grids(fa, [6, 12, 18])},
                          {"d": "2026-01-02", "max_kt": 45, "lat0": 6.0, "lon0": -144.0,
                           "ny": 9, "nx": 9, "grids": _grids(fa, [0, 6, 12])}]},
                {"id": "xp2", "title": TC2["title"], "name": "Bravo", "best_peak_kt": 70.0,
                 "best_category": "1", "era_peak_kt": 0, "ratio": 0.0, "life": life(TC2, False),
                 "days": []},
                {"id": "xp5", "title": TC3["title"], "name": "Delta", "best_peak_kt": 60.0,
                 "best_category": "TS", "era_peak_kt": 45, "ratio": 0.75, "life": life(TC3, True),
                 # grids only up to the last fix — the data edge, not the open end
                 "days": [{"d": "2026-01-02", "max_kt": 45, "lat0": -6.0, "lon0": 91.0,
                           "ny": 9, "nx": 9, "grids": _grids(fd, [6, 12, 18])},
                          {"d": "2026-01-03", "max_kt": 45, "lat0": -6.0, "lon0": 91.0,
                           "ny": 9, "nx": 9, "grids": _grids(fd, [0, 6, 12, 18])},
                          {"d": "2026-01-04", "max_kt": 45, "lat0": -6.0, "lon0": 91.0,
                           "ny": 9, "nx": 9, "grids": _grids(fd, [0, 6, 12])}]}]}


def _fixture(tmpdir):
    root = _build_gd_fixture(str(tmpdir), raw_frames=FRAMES, raw_cutoff=FRAMES[0])
    out = os.path.join(root, "data", "results")
    tl = {"schema": 3, "generated": "2026-01-06T00:00:00+00:00",
          "window": {"start": "2026-01-01", "end": "2026-01-06", "days": 5},
          "mask_bits": {}, "events": [], "raw": {}}
    for spec in ALL:
        entry = TL._track_entry(spec)
        assert entry, spec["id"]
        tl["events"].append(entry)
    json.dump(tl, open(os.path.join(out, "global_timeline.json"), "w"))
    sel_p = os.path.join(out, "events_selected.json")
    sel = json.load(open(sel_p)) if os.path.exists(sel_p) else []
    json.dump(list(sel) + [dict(s) for s in ALL], open(sel_p, "w"))
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


CANVAS_PX = """() => { const cv = document.querySelector('#gm-tracks');
  const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }"""


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    srv, url = _serve(_fixture(tmp_path_factory.mktemp("xpiry")))
    yield url
    srv.shutdown()


def _open(browser, url):
    pg = browser.new_page(viewport={"width": 1400, "height": 1000})
    pg.errors = []                                            # type: ignore[attr-defined]
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))    # type: ignore[attr-defined]
    pg.goto(url + "/index.html")
    pg.wait_for_selector("#gm-track-marks [data-id]", timeout=20000)
    pg.wait_for_timeout(1200)             # the wind fetch + the repaint it triggers
    return pg


def _goto(pg, frame):
    ok = pg.evaluate("""(f) => {
      const d = window.MergedMap._dbg();
      const i = d.frames.indexOf(f);
      if (i < 0) return false;
      const s = document.querySelector('#raw-slider');
      s.value = String(i); s.dispatchEvent(new Event('input', {bubbles: true}));
      return window.MergedMap._dbg().idx === i;
    }""", frame)
    assert ok, f"no frame {frame} on the slider"
    pg.wait_for_timeout(350)


def _marks(pg):
    return set(pg.evaluate("""() => [...document.querySelectorAll(
        '#gm-track-marks [data-id]')].map((n) => n.dataset.id)"""))


def _frame_after(iso):
    ts = iso[:4] + iso[5:7] + iso[8:10] + iso[11:13]
    later = [f for f in FRAMES if f > ts]
    return later[0] if later else None


def test_the_lives_draw_and_the_after_life_frames_draw_nothing(pw, base):
    """The per-event half of the rule: alive frames draw, the FIRST frame past
    the cap draws nothing — mark, label, and the field gate agrees — in the
    global view, both lane tabs, and a region view."""
    b = pw.chromium.launch()
    pg = _open(b, base)
    alive_frame = {"xp1": "2026010206", "xp2": "2026010400", "xp3": "2026010406",
                   "xp4": "2026010500", "xp5": "2026010412"}
    for spec in ALL:
        eid = spec["id"]
        # a frame inside the life: the mark is on the map
        _goto(pg, alive_frame[eid])
        assert eid in _marks(pg), f"{eid} absent at {alive_frame[eid]}"
    # ongoing shows its state while drawn (the rule is about AFTER the last fix)
    _goto(pg, "2026010412")
    st = pg.evaluate("""(id) => { const m = document.querySelector(
        `#gm-track-marks [data-id="${id}"]`); return m && m.dataset.state; }""", "xp5")
    assert st == "ongoing", st
    # every context: the first frame past the cap draws NOTHING for the event
    # (the tropics line uses the app's own region key — its footprint-overlap
    # rule keeps the two tropical storms in view and hides the other three;
    # expiry must hold in the region view exactly as in the global one)
    contexts = [("all", None, ["xp1", "xp2", "xp3", "xp4", "xp5"]),
                ("tropical_cyclone", None, ["xp1", "xp2", "xp5"]),
                ("midlat_cyclone", None, ["xp3", "xp4"]),
                ("all", {"key": "tropics", "label": "Tropics",
                         "lat": [-20, 20], "lon": [-180, 180]}, ["xp1", "xp5"])]
    for tab, region, visible in contexts:
        pg.evaluate("""(r) => MergedMap.setRegion(r)""", region)
        pg.evaluate("""(t) => { state.tab = t; MergedMap.onTabChange(t); }""", tab)
        pg.wait_for_timeout(250)
        if region:                        # the region view really shows its storms
            _goto(pg, alive_frame[visible[0]])
            assert visible[0] in _marks(pg), (tab, region, visible)
        for spec in ALL:
            eid = spec["id"]
            nxt = _frame_after(CAPS[eid])
            if not nxt:
                continue
            _goto(pg, nxt)
            assert eid not in _marks(pg), f"{eid} still drawn past its cap in {tab}"
            if eid not in visible:
                continue
            life = pg.evaluate("(i) => window.EwbTracks._life(i)", eid)
            assert life and life["expired"] is True, (eid, nxt, tab, life)
            assert life["field"] is False, (eid, nxt, tab, life)
    # and the life comes back when the slider goes back (no sticky expiry)
    pg.evaluate("() => { MergedMap.setRegion(null); state.tab = 'all'; MergedMap.onTabChange('all'); }")
    pg.wait_for_timeout(200)
    _goto(pg, "2026010400")
    assert "xp2" in _marks(pg) and "xp5" in _marks(pg)
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_the_ongoing_storm_leaves_at_its_last_fix_not_at_the_window_end(pw, base):
    """The owner's complaint, pinned: `life.ongoing` (dissipation unconfirmed,
    the window still open at the data edge) must NOT keep the storm on the map.
    Delta's last fix is 4 Jan 12Z, the window runs to 6 Jan: old code drew its
    ring, label and whole line at EVERY frame past the last fix (measured: the
    real twin a1d6b391e4d8 lingered 8 frames / 30 h in the 2026-11-19 build).
    New rule: drawn AT the last fix, gone from the next step to the end."""
    b = pw.chromium.launch()
    pg = _open(b, base)
    _goto(pg, "2026010412")                          # AT the last fix
    assert "xp5" in _marks(pg)
    for f in ("2026010418", "2026010500", "2026010512", "2026010600"):
        _goto(pg, f)
        assert "xp5" not in _marks(pg), f"the ongoing storm lingers at {f}"
        life = pg.evaluate("(i) => window.EwbTracks._life(i)", "xp5")
        assert life["ongoing"] is True and life["expired"] is True, (f, life)
    # the field obeys the same edge: its last grid is the last fix's hour
    _goto(pg, "2026010412")
    assert pg.evaluate("(i) => window.EwbTracks._life(i)", "xp5")["field"] is True
    _goto(pg, "2026010418")
    assert pg.evaluate("(i) => window.EwbTracks._life(i)", "xp5")["field"] is False
    assert not pg.errors, pg.errors
    pg.close(); b.close()


def test_the_window_end_frame_holds_zero_track_pixels(pw, base):
    """The whole-layer proof at the window end: every event's cap is before
    6 Jan 00Z, so the track layer's own canvas (#gm-tracks holds ONLY wind
    cells, lines and dots — the raster lives on another canvas) must hold zero
    opaque pixels, the ring layer must be empty, and hovering any storm's row
    (the preview ghost) must not add one pixel either."""
    b = pw.chromium.launch()
    pg = _open(b, base)
    _goto(pg, "2026010600")
    assert _marks(pg) == set(), "marks on the map after every storm ended"
    assert pg.evaluate(CANVAS_PX) == 0, "track-layer pixels after every storm ended"
    for spec in ALL:
        pg.evaluate("(id) => MergedMap.preview(id)", spec["id"])
        pg.wait_for_timeout(120)
        assert pg.evaluate(CANVAS_PX) == 0, f"the ghost of {spec['id']} re-drew its track"
        pg.evaluate("() => MergedMap.preview(null)")
    # scrub to every frame past the caps: the layer is empty on all of them
    for f in [f for f in FRAMES if f > "2026010500"]:
        _goto(pg, f)
        assert pg.evaluate(CANVAS_PX) == 0, f"pixels at {f}"
    # and the layer is NOT trivially empty: at Alpha's mid-life it paints
    _goto(pg, "2026010206")
    assert pg.evaluate(CANVAS_PX) > 0, "the canvas never paints anything (fixture broken?)"
    assert not pg.errors, pg.errors
    os.makedirs(SHOTS, exist_ok=True)
    pg.screenshot(path=os.path.join(SHOTS, "trackexpiry-alive.png"),
                  clip={**pg.locator("#raw-map-wrap").bounding_box(), "height": 520})
    _goto(pg, "2026010600")
    pg.screenshot(path=os.path.join(SHOTS, "trackexpiry-window-end.png"),
                  clip={**pg.locator("#raw-map-wrap").bounding_box(), "height": 520})
    pg.close(); b.close()


# ---------------------------------------------------------------------------
# 2. the published-data sweep (the 124-event real table, same assertions)
# ---------------------------------------------------------------------------

def _cap(e, wind):
    """max(last fix, published end); ongoing -> last fix — mirrors expiredAt."""
    fixes = e["track"]["fixes"]
    last = fixes[-1][0]                                   # 'YYYYMMDDHH'
    life = wind.get(e["id"])
    ongoing = bool(life and life.get("ongoing"))
    end = str(e["end"])
    end10 = end[:4] + end[5:7] + end[8:10] + end[11:13]
    return (last if ongoing else max(last, end10)), ongoing


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "data", "results", "global_timeline.json")),
                    reason="no published results in this checkout")
def test_every_published_track_event_is_gone_past_its_end(pw):
    tl_p = os.path.join(ROOT, "data", "results", "global_timeline.json")
    tl = json.load(open(tl_p))
    track = [e for e in tl["events"] if e.get("track")
             and e["type"] in ("tropical_cyclone", "midlat_cyclone")]
    if not track:
        pytest.skip("timeline has no track-lane events (pre-schema-3 data)")
    wind_p = os.path.join(ROOT, "data", "results", "tc_wind.json")
    wind = {s["id"]: s.get("life") or {} for s in json.load(open(wind_p))["storms"]} \
        if os.path.exists(wind_p) else {}
    caps = {e["id"]: _cap(e, wind) for e in track}

    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.errors = []                                        # type: ignore[attr-defined]
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))  # type: ignore[attr-defined]
    pg.goto(LIVE_URL + "/")
    pg.wait_for_selector("#event-table tbody tr[data-id]", timeout=30000)
    pg.wait_for_function("() => window.MergedMap && MergedMap._dbg().mounted === true", timeout=30000)
    frames = pg.evaluate("() => MergedMap._dbg().frames")
    by_frame = {}
    for eid, (cap, _o) in caps.items():
        later = [f for f in frames if f > cap]
        if later:
            by_frame.setdefault(later[0], []).append(eid)
    for f in sorted(by_frame):
        ok = pg.evaluate("""(f) => {
            const i = window.MergedMap._dbg().frames.indexOf(f);
            if (i < 0) return false;
            const s = document.querySelector('#raw-slider');
            s.value = String(i); s.dispatchEvent(new Event('input', {bubbles: true}));
            return window.MergedMap._dbg().idx === i; }""", f)
        assert ok, f"no frame {f}"
        pg.wait_for_timeout(90)
        present = _marks(pg)
        drawn = present & set(by_frame[f])
        assert not drawn, f"still drawn at {f}: {sorted(drawn)}"
    # window end: nothing may remain whose cap is before the last frame, and
    # when NO cap reaches the last frame the whole track canvas must be empty
    last = frames[-1]
    expect_live = {eid for eid, (cap, _o) in caps.items() if cap >= last}
    assert _marks(pg) == expect_live, (_marks(pg), expect_live)
    if not expect_live:
        assert pg.evaluate(CANVAS_PX) == 0, "track pixels at the window end with no live storm"
    assert not pg.errors, pg.errors
    pg.close(); b.close()
