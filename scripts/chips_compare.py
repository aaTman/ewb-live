#!/usr/bin/env python3
"""8051-vs-8052 side-by-side evidence for the event-type chips (role chips-design,
owner feedback 2026-11-17: "the chips look the same on 8052").

For every place the event-type chiclets appear — the Show tab row with count
badges, the merged-map colour legend + Gantt lane labels, the event-list type
marks, the verdict strip + scorecard lane headers, and the event-detail header —
capture that region from BOTH sites in a FRESH browser context (fresh context =
cold cache; ewb_live.serve already sends `Cache-Control: no-cache`, verified),
stack 8051 over 8052 into one labelled PNG, and report a pixel-diff percentage
so "the same" is argued from numbers.

    .venv/bin/python scripts/chips_compare.py \
        --a http://192.168.0.121:8051 --b http://192.168.0.121:8052

Read-only browser; never writes to either served tree.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import sync_playwright

VIEWPORTS = {"desktop": (1440, 900), "tablet": (820, 1180), "phone": (390, 844)}
REGIONS = [(".type-filter", "tabs"), ("#gm-legend", "legend"), ("#gm-gantt", "gantt"),
           (".evlist", "events"), ("#verdict-strip", "verdict"),
           ("#scorecard-tables .sc-block:first-child", "lanehead")]


def capture(page, url, theme):
    page.goto(url, wait_until="networkidle")
    page.evaluate("(t) => { document.documentElement.dataset.theme = t;"
                  " localStorage.setItem('ewb-theme', t); }", theme)
    page.wait_for_timeout(1000)
    shots = {}
    for sel, name in REGIONS:
        el = page.query_selector(sel)
        if el and el.is_visible():
            try:
                shots[name] = el.screenshot()
            except Exception:
                pass
    row = page.query_selector("#event-table tbody tr")
    if row:
        row.click()
        page.wait_for_timeout(1300)
        shots["detail"] = page.screenshot()
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    return shots


def stack(a_bytes, b_bytes, la, lb):
    import io
    a = Image.open(io.BytesIO(a_bytes)).convert("RGB")
    b = Image.open(io.BytesIO(b_bytes)).convert("RGB")
    w = max(a.width, b.width)
    gap = 34
    out = Image.new("RGB", (w, a.height + b.height + gap + 2), "white")
    d = ImageDraw.Draw(out)
    d.text((6, 2), la, fill="#b00020")
    out.paste(a, (0, 18))
    d.text((6, a.height + 20), lb, fill="#00600f")
    out.paste(b, (0, a.height + gap))
    diff = ImageChops.difference(a, b.resize((a.width, a.height)))
    changed = sum(1 for p in diff.convert("L").getdata() if p > 24)
    pct = 100.0 * changed / (a.width * a.height) if a.height else 0.0
    return out, pct


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="http://192.168.0.121:8051")
    ap.add_argument("--b", default="http://192.168.0.121:8052")
    ap.add_argument("--out", default="notes/screenshots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vname, (w, h) in VIEWPORTS.items():
            # fresh context per site => cold cache, no cross-contamination
            pa = browser.new_context(viewport={"width": w, "height": h}).new_page()
            pb = browser.new_context(viewport={"width": w, "height": h}).new_page()
            for theme in ("light", "dark"):
                sa, sb = capture(pa, args.a, theme), capture(pb, args.b, theme)
                for name in [n for _, n in REGIONS] + ["detail"]:
                    if name in sa and name in sb:
                        img, pct = stack(sa[name], sb[name],
                                         f"8051 {vname} {theme} /{name} (OLD)",
                                         f"8052 {vname} {theme} /{name} (NEW)")
                        tag = f"cmp-{vname}-{theme}-{name}"
                        img.save(out / f"{tag}.png")
                        summary.append(f"{tag}: {pct:5.1f}% pixels differ")
            pa.context.close(); pb.context.close()
        browser.close()
    print("\n".join(summary))
    print(f"side-by-side composites in {out} (cmp-*)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
