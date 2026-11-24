#!/usr/bin/env python
"""Browser check of the TC lane on the /dev/ review site (ne-fix-tc).

Reads the tropical_cyclone tab in a real browser: tab badge, the "Not scored"
lane note (the honest-universe copy), one detail panel, console errors, and
screenshots at 1440/390 x light/dark.

  .venv/bin/python notes/tc_site_check.py http://192.168.0.121:8051/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.121:8051/"
OUT = Path("notes/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
report: dict = {"url": URL, "console_errors": [], "views": {}}


def shot(pg, name):
    pg.screenshot(path=str(OUT / name), full_page=False)
    print("  shot:", name)


def probe(pg, w, h, theme):
    tag = f"tc-{w}-{theme}"
    pg.set_viewport_size({"width": w, "height": h})
    pg.goto(URL, wait_until="domcontentloaded")
    pg.wait_for_selector("#app .chip, .chips button, [data-tab]", timeout=30000)
    # the app's own preference, through its own localStorage key: the rail (and
    # with it #theme-ctl) is collapsed at 390 px, so clicking is not an option
    pg.evaluate("""(p) => { const k='ewb-live-state';
        const s = JSON.parse(localStorage.getItem(k) || '{}');
        s.themePref = p; localStorage.setItem(k, JSON.stringify(s)); }""", theme)
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_selector("[data-tab]", timeout=30000)
    assert pg.evaluate("() => document.documentElement.dataset.theme") == theme
    # TC tab
    tab = pg.query_selector('[data-tab="tropical_cyclone"]')
    if tab is None:
        report["views"][tag] = {"error": "no [data-tab=tropical_cyclone] chip"}
        return
    tab.click()
    pg.wait_for_timeout(1200)
    badge = pg.evaluate("""() => {
        const t=document.querySelector('[data-tab="tropical_cyclone"]');
        return t ? t.textContent.trim() : null; }""")
    note = pg.evaluate("""() => {
        const n=[...document.querySelectorAll('.lane-empty, .sc-block p')]
            .map(e=>e.textContent.trim()).filter(s=>s.length);
        return n; }""")
    rows = pg.evaluate("() => document.querySelectorAll('#event-table tbody tr').length")
    peak = pg.evaluate('''() => { const r=document.querySelector('#event-table tbody tr');
        return r ? [...r.querySelectorAll('td,th')].map(c=>c.textContent.replace(/\s+/g,' ').trim()).slice(0,6) : null; }''')
    report["views"][tag] = {"tab_text": badge, "notes": note, "list_rows": rows, "first_row": peak,
                            "scroll_w": pg.evaluate(
                                "() => [document.documentElement.scrollWidth, window.innerWidth]")}
    print(f"  {tag}: tab={badge!r} rows={rows} notes={note}")
    shot(pg, f"{tag}.png")
    # detail panel of the first TC event
    if w >= 1000:
        link = pg.query_selector("#event-table tbody tr")
        if link:
            link.click()
            pg.wait_for_timeout(1500)
            head = pg.evaluate("""() => {
                const h=document.querySelector('#detail-viz .tv-head, #detail-viz, .detail-head');
                return h ? h.textContent.replace(/\\s+/g,' ').trim().slice(0,300) : null; }""")
            report["views"][tag]["detail_head"] = head
            # ne-fix-tc 2026-11-19: the basis + truncation copy (TC-2/TC-6) —
            # the first row is the window-START event, so it must say
            # "(1-min)" and, for Saudel/Lala, the past-the-window note
            tv = pg.evaluate("""() => {
                const n=document.querySelector('#detail-viz .tv-note');
                return n ? n.textContent.replace(/\\s+/g,' ').trim() : null; }""")
            report["views"][tag]["tv_note"] = tv
            print("   detail:", (head or "")[:180])
            print("   note:", (tv or "")[:200])
            shot(pg, f"{tag}-detail.png")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.on("console", lambda m: report["console_errors"].append(
        f"{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: report["console_errors"].append(f"pageerror: {str(e)[:160]}"))
    probe(pg, 1440, 900, "light")
    probe(pg, 1440, 900, "dark")
    probe(pg, 390, 844, "light")
    probe(pg, 390, 844, "dark")
    ctx.close()
    b.close()

errs = sorted(set(report["console_errors"]))
report["console_errors"] = errs
print("\nconsole errors:", len(errs))
for e in errs[:10]:
    print("  ", e)
(OUT / "tc_site_check.json").write_text(json.dumps(report, indent=1))
print("report ->", OUT / "tc_site_check.json")
