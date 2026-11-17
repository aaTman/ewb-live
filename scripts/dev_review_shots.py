#!/usr/bin/env python3
"""Visual-review harness for a deployed EWB Live dashboard (the /dev/ site).

Playwright pass over every event-type tab + the About dialog: records the tab
labels and per-tab event counts the UI actually shows, the first console error
per page, and screenshots (desktop + phone, light + dark) under
notes/screenshots/. Exits non-zero if any page logged a console error.

    .venv/bin/python scripts/dev_review_shots.py [--url http://192.168.0.121:8051]
        [--out notes/screenshots] [--types heat_wave,freeze,...] [--mobile]

Nothing here writes to the dashboard; it is a read-only browser.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

TYPES = ["all", "heat_wave", "freeze", "cold_snap", "atmospheric_river",
         "tropical_cyclone", "midlat_cyclone", "flood"]
VIEWPORTS = {"desktop": {"width": 1440, "height": 900},
             "phone": {"width": 390, "height": 844}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.0.121:8051")
    ap.add_argument("--out", default="notes/screenshots")
    ap.add_argument("--types", default=",".join(TYPES))
    ap.add_argument("--no-mobile", action="store_true")
    args = ap.parse_args()
    url = args.url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    # index of each type among the tab buttons (0 is "All")
    idx = {t: i + 1 for i, t in enumerate(TYPES[1:])}
    types_iter = list(types)
    vps = {"desktop": VIEWPORTS["desktop"]} if args.no_mobile else VIEWPORTS

    errors: list[str] = []
    report: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vname, vp in vps.items():
            ctx = browser.new_context(viewport=vp, device_scale_factor=1)
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"{vname}: {m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e, v=vname: errors.append(f"{v}: pageerror: {e}"))
            for tab in types_iter:
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(600)
                # the tab control is JS-built buttons ("Floods 30") with no
                # data-type attribute: click by position in TYPE_ORDER
                chips = page.query_selector_all("#type-tabs button")
                want = 0 if tab == "all" else idx.get(tab)
                if want is not None and len(chips) > want:
                    try:
                        chips[want].click(timeout=3000)
                    except Exception as exc:      # hidden rail (phone) etc.
                        report.setdefault("click_failures", []).append(f"{vname}/{tab}: {exc}")
                page.wait_for_timeout(700)
                state = page.evaluate("""() => {
                  const rows = [...document.querySelectorAll('#event-table tbody tr')];
                  const tabs = [...document.querySelectorAll('#type-tabs button')]
                     .map(b => b.textContent.replace(/\\s+/g, ' ').trim());
                  const empty = [...document.querySelectorAll('.lane-empty')]
                     .map(n => n.textContent.trim());
                  return {rows: rows.length, tabs, empty,
                          count: ((document.querySelector('#event-count')||{}).textContent||'').trim()};
                }""")
                report[f"{vname}/{tab}"] = state
                # open the first event of the lane: the detail panel is where the
                # track/footprint rendering either exists or does not
                rows = page.query_selector_all("#event-table tbody tr")
                if rows:
                    try:
                        rows[0].click(timeout=3000)
                        page.wait_for_timeout(2600)   # viz.json fetch + render
                        detail = page.evaluate("""() => {
                          const sec = document.querySelector('#detail-sec');
                          const title = document.querySelector('#detail-title');
                          const viz = document.querySelector('#detail-viz');
                          const txt = (e) => e ? e.innerText.trim().replace(/\\s*\\n+\\s*/g, ' | ') : null;
                          return {open: sec ? !sec.classList.contains('hidden') : false,
                                  title: title ? title.textContent.trim() : null,
                                  chips: txt(document.querySelector('#detail-chips')),
                                  tvTrack: !!(viz && viz.querySelector('svg.tv-svg')),
                                  plotly: !!(viz && viz.querySelector('.js-plotly-plot, .plotly-graph-div')),
                                  vizChildren: viz ? viz.children.length : 0,
                                  vizTags: viz ? [...viz.children].map((n) => n.tagName.toLowerCase() + (n.className && typeof n.className === 'string' ? '.' + n.className : '')).join(',') : null,
                                  story: txt(document.querySelector('#detail-story'))};
                        }""")
                        report[f"{vname}/{tab}/event"] = detail
                        page.screenshot(path=str(out / f"{vname}-{tab}-event.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        page.goto(url, wait_until="networkidle")
                        page.wait_for_timeout(400)
                    except Exception as exc:
                        report[f"{vname}/{tab}/event"] = f"click failed: {exc}"
                for theme in ("light", "dark"):
                    page.evaluate("""(t) => { document.documentElement.dataset.theme = t;
                                                localStorage.setItem('ewb-theme', t); }""", theme)
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(out / f"{vname}-{tab}-{theme}.png"), full_page=False)
            # About dialog, once per viewport
            page.goto(url, wait_until="networkidle")
            about = page.query_selector("#about-btn")
            if about and about.is_visible():
                about.click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{vname}-about.png"), full_page=True)
                report[f"{vname}/about-lanes"] = page.evaluate(
                    """() => [...document.querySelectorAll('#about-dialog li b')]
                        .map(b => b.textContent.trim())""")
            ctx.close()
        browser.close()

    (out / "report.json").write_text(json.dumps({"report": report, "console_errors": errors},
                                                indent=1))
    for k, v in report.items():
        if isinstance(v, dict) and "rows" in v:
            print(f"{k:24s} rows={v['rows']:>3}  count={v.get('count','').strip()!r}"
                  f"  empty={v.get('empty') or ''}")
        else:
            print(f"{k:24s} {v}")
    if errors:
        print(f"\nCONSOLE ERRORS ({len(errors)}):")
        print(*sorted(set(errors))[:20], sep="\n")
        return 1
    print(f"\nno console errors; shots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
