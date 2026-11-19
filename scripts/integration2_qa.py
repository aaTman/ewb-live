#!/usr/bin/env python3
"""Integration-2 browser QA on the dev site (read-only): all tabs, chips rows,
overflow, console errors, per-lane detail panels, map layers; 1440 + 390, light + dark.

    python scripts/integration2_qa.py [--url http://192.168.0.121:8051] [--out notes/screenshots/integration2]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

VP = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}
THEMES = ("light", "dark")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.0.121:8051")
    ap.add_argument("--out", default="notes/screenshots/integration2")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"url": a.url, "runs": []}
    bad = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for vname, vp in VP.items():
            for theme in THEMES:
                ctx = br.new_context(viewport=vp, color_scheme=theme)
                pg = ctx.new_page()
                errs: list[str] = []
                pg.on("console", lambda m, errs=errs: errs.append(m.text[:200]) if m.type == "error" else None)
                pg.on("pageerror", lambda e, errs=errs: errs.append("pageerror " + str(e)[:200]))
                pg.goto(a.url + "/", wait_until="domcontentloaded")
                pg.wait_for_selector("#event-table tbody tr[data-id]", timeout=30000)
                pg.wait_for_timeout(1500)
                pg.evaluate("(t) => { const b = document.querySelector(\"#theme-ctl button[data-pref='\" + t + \"']\"); if (b) b.click(); }", theme)
                pg.wait_for_timeout(500)
                run = {"viewport": vname, "theme": theme, "tabs": [], "details": []}
                tabs = pg.evaluate("() => [...document.querySelectorAll('#type-tabs button')].map(b => ({tab: b.dataset.tab || b.textContent.trim(), text: b.textContent.trim().replace(/\\s+/g,' ')}))")
                for t in tabs:
                    pg.evaluate("(k) => { const b = [...document.querySelectorAll('#type-tabs button')].find(x => (x.dataset.tab || x.textContent.trim()) === k); if (b) b.click(); }", t["tab"])
                    pg.wait_for_timeout(900)
                    m = pg.evaluate("""() => ({
                        rows: document.querySelectorAll('#event-table tbody tr[data-id]').length,
                        chipRows: new Set([...document.querySelectorAll('#type-tabs button')].map(b => Math.round(b.getBoundingClientRect().top))).size,
                        overflow: document.documentElement.scrollWidth - innerWidth,
                        on: (typeof MergedMap !== 'undefined') ? MergedMap._dbg().on : null,
                        legend: (document.querySelector('#gm-legend') || {}).innerText || '' })""")
                    m["legend"] = " ".join(m["legend"].split())[:160]
                    m.update(t)
                    run["tabs"].append(m)
                    if m["overflow"] > 0: bad += 1
                    pg.screenshot(path=str(out / f"{vname}-{theme}-tab-{t['tab']}.png"))
                # detail of the first event per lane
                ids = pg.evaluate("""() => { const o = {}; state.events.forEach(e => { if (!o[e.event_type]) o[e.event_type] = e.id; }); return o; }""")
                for et, eid in ids.items():
                    pg.goto(a.url + f"/#event={eid}&lead=24h", wait_until="domcontentloaded")
                    try:
                        pg.wait_for_selector("#detail-viz .ev3-panel, #detail-viz .tv-map-wrap, #detail-viz .lane-empty", timeout=25000)
                    except Exception:  # noqa: BLE001
                        pass
                    pg.wait_for_timeout(1500)
                    d = pg.evaluate("""() => ({
                        kind: document.querySelector('#detail-viz .ev3-panel') ? 'ev3' : document.querySelector('#detail-viz .tv-map-wrap') ? 'trackviz' : document.querySelector('#detail-viz .lane-empty') ? 'empty' : 'none',
                        title: (document.querySelector('#detail-title') || {}).textContent || '',
                        overflow: document.documentElement.scrollWidth - innerWidth })""")
                    d.update(type=et, id=eid)
                    run["details"].append(d)
                    if d["overflow"] > 0 or d["kind"] == "none": bad += 1
                    pg.screenshot(path=str(out / f"{vname}-{theme}-detail-{et}.png"))
                run["console_errors"] = errs
                bad += len(errs)
                rep["runs"].append(run)
                ctx.close()
        br.close()
    (out / "qa_report.json").write_text(json.dumps(rep, indent=1))
    for r in rep["runs"]:
        print(r["viewport"], r["theme"], "tabs", [(t["tab"], t["rows"]) for t in r["tabs"]], "chipRows", {t["chipRows"] for t in r["tabs"]},
              "overflow", max(t["overflow"] for t in r["tabs"]), "detail", [(d["type"], d["kind"]) for d in r["details"]],
              "console", len(r["console_errors"]))
    print("problems:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
