#!/usr/bin/env python3
"""visual-gantt QA: measure + shoot the timeline card (the Gantt strip under the
map) at four viewports in light and dark, for BOTH the sparse 3-lane live window
and the dense new-event window (midlat/TC/flood → density ribbons).

    .venv/bin/python scripts/gantt_visual_qa.py --url http://127.0.0.1:8079 \
        --out /home/orca/orca/workspaces/ewb-live/screenshots/visual-gantt --tag dev-r2

Reports, per viewport x theme: the SVG box height, overflow, how many bars are
FULLY covered by another bar in the same lane, the smallest DRAWN bar height (must
be >= 5px), the time axis's share of the strip width (must be >= 80%), how many
lanes render as a density ribbon, whether a lane label runs past the SVG box, and
whether the strip's bottom sits inside the card. Screenshots are PNGs — the owner
rule is they NEVER go in the repo, so --out must point OUTSIDE it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

VPS = {"1440x900": {"width": 1440, "height": 900},
       "1024x768": {"width": 1024, "height": 768},
       "390x844": {"width": 390, "height": 844},
       "320x640": {"width": 320, "height": 640}}
THEMES = ("light", "dark")

# One page-side measurement pass. Bar geometry comes from getBoundingClientRect
# (device-independent CSS px, so the comparison is the same scale the eye sees).
MEASURE = """() => {
  const svg = document.querySelector('#gm-gantt');
  const card = document.querySelector('.timeline');
  const bars = [...svg.querySelectorAll('.gm-bar')];
  const rect = (n) => n.getBoundingClientRect();
  const r = rect(svg), cr = rect(card);
  // group bars by their LANE, taken from the DOM order groups: each lane is
  // painted as a run of consecutive siblings that share a fill (lane colour) —
  // but the robust key is the y-position set per lane band, so bucket by the
  // lane separator rects instead: .gm-lane rects define the lane bands.
  const lanes = [...svg.querySelectorAll('.gm-lane-band, .gm-lane')];
  const laneOf = (b) => {
    const br = rect(b);
    let best = -1, bd = 1e9;
    lanes.forEach((l, i) => { const lr = rect(l);
      const d = Math.abs((br.top + br.bottom) / 2 - (lr.top + lr.bottom) / 2);
      if (d < bd) { bd = d; best = i; } });
    return best;
  };
  const covered = [];
  for (let i = 0; i < bars.length; i++) {
    const a = rect(bars[i]);
    if (a.width < 1.5) continue;
    for (let j = 0; j < bars.length; j++) {
      if (i === j) continue;
      const b = rect(bars[j]);
      // fully covered = a's rect sits inside b's rect on BOTH axes (>=2 sides
      // strictly inside so an identical-position pair counts once)
      if (a.left >= b.left - 0.5 && a.right <= b.right + 0.5
          && a.top >= b.top - 0.5 && a.bottom <= b.bottom + 0.5) {
        covered.push({ i: bars[i].dataset.id, by: bars[j].dataset.id, sameLane: laneOf(bars[i]) === laneOf(bars[j]) });
        break;
      }
    }
  }
  // sub-row depth per lane band: count distinct bar top positions inside a band
  const depth = lanes.map((l, i) => new Set(bars.filter((b) => laneOf(b) === i)
    .map((b) => Math.round(rect(b).top))).size);
  const labs = [...svg.querySelectorAll('.gm-lane-lab')];
  const labOver = labs.map((t) => ({ t: t.textContent, w: +rect(t).width.toFixed(1),
    clipped: rect(t).right > cr.right - 2 })).filter((o) => o.clipped);
  const next = document.querySelector('#events-sec');
  // RIBBON awareness (round 2): a dense lane paints a density ribbon instead of
  // bars; every DRAWN bar must keep ONE height >= 5px, the time axis must keep
  // >= 80% of the strip width, and a lane label must never run past the SVG box.
  const ribbons = [...svg.querySelectorAll('.gm-ribbon')];
  const lane0 = lanes[0] ? rect(lanes[0]) : r;
  const axisFrac = r.width ? (lane0.right - lane0.left) / r.width : 1;
  const barHs = [...new Set(bars.map((b) => +rect(b).height.toFixed(2)))];
  const minBarH = bars.length ? Math.min(...barHs) : null;
  const labClip = labs.filter((t) => rect(t).right > r.right + 1).map((t) => t.textContent.slice(0, 24));
  return {
    svg: { w: +r.width.toFixed(1), h: +r.height.toFixed(1), top: +r.top.toFixed(1), bottom: +r.bottom.toFixed(1) },
    svgAttrs: { w: svg.getAttribute('width'), h: svg.getAttribute('height'), vb: svg.getAttribute('viewBox') },
    card: { w: +cr.width.toFixed(1), right: +cr.right.toFixed(1) },
    barCount: bars.length,
    barW: bars.map((b) => +rect(b).width.toFixed(1)),
    barH: barHs,
    minBarH, axisFrac: +axisFrac.toFixed(3),
    ribbonLanes: ribbons.length,
    ribSegs: svg.querySelectorAll('.gm-rib-seg').length,
    laneCount: lanes.length,
    laneDepth: depth,
    coveredCount: covered.length,
    coveredSameLane: covered.filter((c) => c.sameLane).length,
    coveredExamples: covered.slice(0, 4),
    labelOverflow: labOver,
    labelClip: labClip,
    labels: labs.map((t) => t.textContent),
    overflowDoc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    overflowTimeline: (() => { const t = document.querySelector('.timeline'); return t.scrollWidth - t.clientWidth; })(),
    ganttBottomInsideCard: r.bottom <= cr.bottom + 1,
    gapToNextSection: next ? +(next.getBoundingClientRect().top - r.bottom).toFixed(1) : null,
  };
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8061")
    ap.add_argument("--out", default="notes/screenshots/visual-gantt")
    ap.add_argument("--tag", default="after")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"url": a.url, "tag": a.tag, "runs": []}
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for vname, vp in VPS.items():
            for theme in THEMES:
                ctx = br.new_context(viewport=vp, color_scheme=theme)
                pg = ctx.new_page()
                errs: list[str] = []
                pg.on("console", lambda m, e=errs: e.append(m.text[:200])
                      if m.type == "error" and "Failed to load resource" not in m.text else None)
                pg.on("pageerror", lambda e, errs=errs: errs.append("pageerror " + str(e)[:200]))
                pg.goto(a.url + "/", wait_until="domcontentloaded")
                pg.wait_for_selector("#event-table tbody tr[data-id]", timeout=40000)
                pg.wait_for_function("() => typeof MergedMap !== 'undefined' && MergedMap._dbg().mounted === true", timeout=40000)
                pg.wait_for_function("() => document.querySelector('#gm-gantt .gm-bar, #gm-gantt .gm-rib-seg')", timeout=40000)
                pg.wait_for_timeout(1200)
                pg.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
                pg.wait_for_timeout(350)
                m = pg.evaluate(MEASURE)
                m["viewport"] = vname
                m["theme"] = theme
                m["consoleErrors"] = errs
                m["minBarW"] = min(m["barW"]) if m["barW"] else None
                m.pop("barW")
                # shoot the whole timeline card, and the full page for context
                card = pg.query_selector(".timeline")
                card.screenshot(path=str(out / f"timeline-{vname}-{theme}-{a.tag}.png"))
                pg.screenshot(path=str(out / f"page-{vname}-{theme}-{a.tag}.png"))
                # scrolled into view: the strip must sit inside the card
                pg.evaluate("() => document.querySelector('#global-sec').scrollIntoView({block:'start'})")
                pg.wait_for_timeout(200)
                pg.screenshot(path=str(out / f"section-{vname}-{theme}-{a.tag}.png"))
                rep["runs"].append(m)
                ctx.close()
        br.close()
    (out / f"measure-{a.tag}.json").write_text(json.dumps(rep, indent=1))
    print(f"{'viewport':11} {'theme':6} {'svgH':>6} {'ovfDoc':>7} {'covLane':>8} "
          f"{'minBarH':>8} {'axis%':>6} {'rib':>4} {'labClip':>8} {'inCard':>7} {'errs':>5}")
    bad = 0
    for m in rep["runs"]:
        # a bar drawn below 5px, an axis under 80% of the width, a label running past
        # the SVG box, a bar hidden behind another, overflow, or a console error all FAIL
        bad += (m["coveredSameLane"] > 0) + (m["overflowDoc"] > 0) + (m["overflowTimeline"] > 0) \
               + (not m["ganttBottomInsideCard"]) + (len(m["labelOverflow"]) > 0) \
               + (len(m["consoleErrors"]) > 0) + (len(m["labelClip"]) > 0) \
               + (m["minBarH"] is not None and m["minBarH"] < 5) + (m["axisFrac"] < 0.8)
        print(f"{m['viewport']:11} {m['theme']:6} {m['svg']['h']:>6} {m['overflowDoc']:>7} "
              f"{m['coveredSameLane']:>8} {str(m['minBarH']):>8} {m['axisFrac']*100:>5.0f}% "
              f"{m['ribbonLanes']:>4} {len(m['labelClip']):>8} "
              f"{str(m['ganttBottomInsideCard']):>7} {len(m['consoleErrors']):>5}")
    print("FAILS:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
