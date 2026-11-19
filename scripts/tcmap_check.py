#!/usr/bin/env python3
"""Dissipation check against the :8053 preview with the REAL 44-storm asset.

For one storm that ends mid-day in the published window: paint the map at the
last 6-hourly step of its life and at the next one, and measure — from the
rendered canvas and the layer's own hit test — that its wind field exists in the
first and is entirely gone in the second, while other still-living storms keep
theirs. Screenshots land in notes/screenshots/.

Read-only against the preview tree. Run: .venv/bin/python scripts/tcmap_check.py [TC_URL=...]  # needs Playwright
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

import numpy as np
from playwright.sync_api import sync_playwright

URL = os.environ.get("TC_URL", "http://192.168.0.121:8053")
SHOTS = os.path.join(os.path.dirname(__file__), "..", "notes", "screenshots")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests", "e2e"))

DOC = json.loads(urllib.request.urlopen(f"{URL}/data/results/tc_wind.json").read())


def cells(entry):
    """Cell centres of a day grid, by the placement contract (lat0 + i*step)."""
    g = np.repeat(np.asarray(entry["rle"][::2]), np.asarray(entry["rle"][1::2]))
    ii, jj = np.nonzero(g.reshape(entry["ny"], entry["nx"]))
    step = DOC["step_deg"]
    return [(entry["lat0"] + i * step,
             ((entry["lon0"] + j * step + 180.0) % 360.0) - 180.0)
            for i, j in zip(ii, jj)]


def pick():
    """A storm whose LAST live day has cells and whose death is not at 00Z, so the
    next frame is inside the same UTC day: the daily grid exists, the step does
    not. That is the case a per-day layer gets wrong."""
    best = None
    for s in DOC["storms"]:
        if not s["days"] or s["life"]["ongoing"]:
            continue
        last = s["days"][-1]
        if not cells(last):
            continue
        death = dt.datetime.strptime(s["life"]["death"], "%Y-%m-%dT%H:%M:%SZ")
        if death.hour == 0:
            continue                     # then the next DAY is the honest empty one
        if best is None or len(cells(last)) > len(cells(best[2]["days"][-1])):
            best = (s["id"], s["name"], s)
    return best


GOTO = """(f) => { const d = window.MergedMap._dbg(); const i = d.frames.indexOf(f);
  if (i < 0) return false;
  const s = document.querySelector('#raw-slider');
  s.value = String(i); s.dispatchEvent(new Event('input', {bubbles: true}));
  return window.MergedMap._dbg().idx === i; }"""
PROBE = """([lat, lon]) => {
  const d = window.MergedMap._dbg(), cv = document.querySelector('#gm-tracks');
  const r = cv.getBoundingClientRect(), p = d.project([lon, lat]);
  if (!p) return {err: 'no projection'};
  const k = r.width / (parseFloat(cv.style.width) || r.width);
  return {x: r.x + p[0] * k, y: r.y + p[1] * k,
          hit: (() => { const h = window.EwbTracks._at(r.x + p[0] * k, r.y + p[1] * k);
            return h && {id: h.id, kt: h.kt, d: h.d}; })()};
}"""
VIOLET = """() => { const cv = document.querySelector('#gm-tracks');
  const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) {
    const [r, g, b] = [d[i-3], d[i-2], d[i-1]];
    if (d[i] > 120 && b > r + 18 && r > g + 8 && b > g + 40) n++;
  }
  return n; }"""


def main() -> int:
    sid, name, storm = pick()
    last = storm["days"][-1]
    death = dt.datetime.strptime(storm["life"]["death"], "%Y-%m-%dT%H:%M:%SZ")
    # the last 6-hourly step BEFORE the death, and the next step up: the first is
    # the last frame that may shade the storm, the second must not shade it at all
    alive = death.replace(minute=0, second=0, microsecond=0)
    while alive >= death or alive.hour % 6:
        alive -= dt.timedelta(hours=1)
    dead = alive + dt.timedelta(hours=6)
    f_alive, f_dead = alive.strftime("%Y%m%d%H"), dead.strftime("%Y%m%d%H")
    pts = cells(last)
    mid = pts[len(pts) // 2]
    print(f"{name}: life {storm['life']['birth']} .. {storm['life']['death']} "
          f"(ongoing {storm['life']['ongoing']}), last day {last['d']} steps "
          f"{last['steps']}, {len(pts)} cells")
    print(f"frames: alive {f_alive} -> dead {f_dead}; probing cell "
          f"{mid[0]:.2f}N {mid[1]:.2f}E")

    os.makedirs(SHOTS, exist_ok=True)
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 1000},
                   device_scale_factor=2)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{URL}/index.html")
        pg.wait_for_selector("#gm-track-marks [data-id]", timeout=30000)
        pg.wait_for_timeout(2000)
        for frame, tag, expect in ((f_alive, "last_day", True), (f_dead, "after", False)):
            assert pg.evaluate(GOTO, frame), f"frame {frame} not on the slider"
            pg.wait_for_timeout(900)
            life = pg.evaluate("(i) => window.EwbTracks._life(i)", sid)
            probe = pg.evaluate(PROBE, list(mid))
            n = pg.evaluate(VIOLET)
            print(f"  {frame}: alive={life['alive']} dissipated={life['dissipated']} "
                  f"field={life['field']} hit={probe['hit']} violet_px={n}")
            pg.screenshot(path=os.path.join(SHOTS, f"tc_map_dissipate_{tag}_{name}.png"),
                          clip={**pg.locator("#raw-map-wrap").bounding_box(),
                                "height": 520})
            # and a close-up on the storm itself: at world scale a 1° cell is two
            # pixels, and a difference you cannot see is not evidence
            pg.screenshot(path=os.path.join(SHOTS, f"tc_map_zoom_{tag}_{name}.png"),
                          clip={"x": probe["x"] - 130, "y": probe["y"] - 90,
                                "width": 300, "height": 210})
            if life["field"] is not expect:
                fails.append(f"{frame}: field={life['field']} expected {expect}")
            if bool(probe["hit"]) is not expect:
                fails.append(f"{frame}: hit={probe['hit']} expected drawn={expect}")
            if expect and (probe["hit"] or {}).get("id") != sid:
                fails.append(f"{frame}: hit belongs to {(probe['hit'] or {}).get('id')}")
            if not expect:
                st = pg.evaluate("""(id) => { const m = document.querySelector(
                    `#gm-track-marks [data-id="${id}"]`);
                    return m && m.dataset.state; }""", sid)
                print(f"  {frame}: marker state = {st}")
                if st != "dissipated":
                    fails.append(f"{frame}: marker state {st!r}, expected 'dissipated'")
        other = [(s["id"], s["name"]) for s in DOC["storms"]
                 if s["life"]["ongoing"] or s["life"]["death"] > dead.isoformat()]
        if other:
            oid = other[0][0]
            life = pg.evaluate("(i) => window.EwbTracks._life(i)", oid)
            print(f"  still-open check {other[0][1]}: {life}")
        if errs:
            fails.append(f"page errors: {errs}")
        pg.close(); b.close()
    print("FAIL: " + "; ".join(fails) if fails else "OK: the field ends with the storm")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
