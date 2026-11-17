#!/usr/bin/env python3
"""Before/after screenshot + geometry pass for the event-type chips redesign.

Captures every surface the event-type chips/tabs/badges appear on (role
chips-design, briefs-new-events/chips_design.md):

  * the Show tab row (#type-tabs), *element* shot + per-button geometry
  * the raw-map colour legend strip (#gm-legend) and Gantt lane labels
  * the event list type dots (#event-table)
  * the verdict strip + scorecard lane headers (.type-chip)
  * the event-detail header (title + story line)

for viewport desktop 1440 / tablet 820 / phone 390, light + dark, and writes a
JSON metrics report (chip widths/heights/rows, label lengths, page overflow)
next to the PNGs so "uneven" can be argued from numbers, not vibes.

    .venv/bin/python scripts/chips_shots.py --url http://192.168.0.121:8051 --prefix before
    .venv/bin/python scripts/chips_shots.py --url http://192.168.0.121:8052 --prefix after

Read-only browser; never writes to the served tree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORTS = {"desktop": (1440, 900), "tablet": (820, 1180), "phone": (390, 844)}

METRICS_JS = """() => {
  const box = (e) => { const r = e.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top)}; };
  const tabs = [...document.querySelectorAll('#type-tabs button')].map((b) => {
    const r = b.getBoundingClientRect();
    const badge = b.querySelector('.n');
    return {text: b.textContent.replace(/\\s+/g,' ').trim(),
            h: Math.round(r.height), w: Math.round(r.width), row: Math.round(r.top),
            ariaPressed: b.getAttribute('aria-pressed'), role: b.getAttribute('role'),
            title: b.getAttribute('title') || '',
            badgeRight: badge ? Math.round(b.querySelector('.n').getBoundingClientRect().right - r.right) : null,
            dot: !!b.querySelector('i')};
  });
  const rows = [...new Set(tabs.map(t => t.row))].length;
  const heights = [...new Set(tabs.map(t => t.h))];
  const widths  = [...new Set(tabs.map(t => t.w))];
  const sel = document.querySelector('#type-tabs button.on');
  return {
    tabs, rows, heights, widths,
    selShadow: sel ? getComputedStyle(sel).boxShadow !== 'none' : null,
    pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    filterBox: box(document.querySelector('.type-filter') || document.body),
    legendBox: document.querySelector('#gm-legend') ? box(document.querySelector('#gm-legend')) : null,
    chips: [...document.querySelectorAll('.type-chip')].slice(0, 12).map((c) => {
      const r = c.getBoundingClientRect();
      return {text: c.textContent.trim(), h: Math.round(r.height),
              color: getComputedStyle(c).color, bg: getComputedStyle(c).backgroundColor,
              fs: getComputedStyle(c).fontSize, tt: getComputedStyle(c).textTransform};
    }),
  };
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.0.121:8051")
    ap.add_argument("--prefix", default="before")
    ap.add_argument("--out", default="notes/screenshots")
    args = ap.parse_args()
    url = args.url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vname, (w, h) in VIEWPORTS.items():
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"{vname}: {m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e, v=vname: errors.append(f"{v}: pageerror: {e}"))
            for theme in ("light", "dark"):
                page.goto(url, wait_until="networkidle")
                page.evaluate("(t) => { document.documentElement.dataset.theme = t;"
                              " localStorage.setItem('ewb-theme', t); }", theme)
                page.wait_for_timeout(900)
                tag = f"chips_{args.prefix}-{vname}-{theme}"
                m = page.evaluate(METRICS_JS)
                report[tag] = m
                # full viewport
                page.screenshot(path=str(out / f"{tag}-home.png"))
                for sel, name in [(".type-filter", "tabs"),
                                  ("#gm-legend", "legend"),
                                  ("#gm-gantt", "gantt"),
                                  (".evlist", "events"),
                                  ("#verdict-strip", "verdict"),
                                  ("#scorecard-tables .sc-block:first-child", "lanehead")]:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        try:
                            el.screenshot(path=str(out / f"{tag}-{name}.png"))
                        except Exception as exc:
                            report[tag].setdefault("shot_fail", {})[name] = str(exc)
                # event detail: first row of the list
                row = page.query_selector("#event-table tbody tr")
                if row:
                    row.click()
                    page.wait_for_timeout(1400)
                    page.screenshot(path=str(out / f"{tag}-detail.png"))
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            ctx.close()
        browser.close()

    (out / f"chips_{args.prefix}_metrics.json").write_text(
        json.dumps({"report": report, "console_errors": errors}, indent=1))
    for k, v in report.items():
        print(f"{k}: tabRows={v['rows']} heights={v['heights']} widths={len(v['widths'])}"
              f" pageOverflow={v['pageOverflow']}px")
    if errors:
        print("CONSOLE ERRORS:", *sorted(set(errors))[:10], sep="\n  ")
    print(f"shots + metrics in {out} (prefix {args.prefix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
